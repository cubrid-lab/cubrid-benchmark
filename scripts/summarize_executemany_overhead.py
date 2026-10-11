#!/usr/bin/env python3
"""Summarise a run of experiments/pycubrid-executemany-overhead (cubrid-lab/pycubrid#822).

Usage: summarize_executemany_overhead.py <run-dir>
Reads  <run-dir>/raw/*.json (+ sqllog_slices.txt) and writes <run-dir>/derived/summary.{json,md}
and <run-dir>/derived/profile_*.txt.
"""

from __future__ import annotations

import datetime as dt
import glob
import gzip
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from typing import Any

med = statistics.median


def pct(data: list[float], p: float) -> float:
    s = sorted(data)
    k = (len(s) - 1) * p / 100
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def load(raw: str, pattern: str) -> list[dict[str, Any]]:
    out = []
    for p in sorted(glob.glob(os.path.join(raw, pattern))):
        with open(p) as fh:
            out.append(json.load(fh))
    return out


def ms(ns: float) -> float:
    return ns / 1e6


# --------------------------------------------------------------------------- compare


def pooled_compare(files: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """key -> {driver: {...}} pooled over trials; key = op/rows/variant."""
    acc: dict[str, dict[str, Any]] = {}
    for f in files:
        drv = f["meta"]["driver"]
        for key, r in f["results"].items():
            d = acc.setdefault(key, {}).setdefault(
                drv,
                {"total": [], "stmt": [], "commit": [], "trial_medians": [], "rowcounts": set()},
            )
            d["total"] += r["total_ns"]
            d["stmt"] += r["stmt_ns"]
            d["commit"] += r["commit_ns"]
            d["trial_medians"].append(med(r["total_ns"]))
            d["rowcounts"].update(x for x in r["rowcount"] if x is not None)
    return acc


def cstat(d: dict[str, Any], what: str = "total") -> dict[str, float]:
    v = [ms(x) for x in d[what]]
    return {
        "median": med(v),
        "q1": pct(v, 25),
        "q3": pct(v, 75),
        "min": min(v),
        "max": max(v),
        "n": len(v),
    }


def trial_range(d: dict[str, Any]) -> tuple[float, float]:
    t = [ms(x) for x in d["trial_medians"]]
    return min(t), max(t)


def compare_tables(
    acc: dict[str, dict[str, Any]], title: str, out: list[str], summary: dict[str, Any], tag: str
) -> None:
    ops = []
    for key in acc:
        op = key.split("/")[0]
        if op not in ops:
            ops.append(op)
    order = ["executemany", "loop_execute", "batch_prerendered", "native_prepared"]
    for op in ops:
        out.append(f"\n#### {title}: {op.upper()}\n")
        out.append(
            "Statement phase + commit, pooled median over all trials and iterations "
            "(ms). `stmt` excludes the commit. Ratio is total median / CUBRIDdb "
            "`executemany` total median.\n"
        )
        out.append(
            "| Rows | Path | total median | IQR | trial-median range | stmt median | "
            "commit median | ratio vs CUBRIDdb em | stmt ratio |"
        )
        out.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
        sizes = sorted({int(k.split("/")[1]) for k in acc if k.startswith(op + "/")})
        for n in sizes:
            ref = acc.get(f"{op}/{n}/executemany", {}).get("cubriddb")
            for v in order:
                e = acc.get(f"{op}/{n}/{v}", {})
                for drv in ("pycubrid", "cubriddb"):
                    if drv not in e:
                        continue
                    d = e[drv]
                    t, s, c = cstat(d), cstat(d, "stmt"), cstat(d, "commit")
                    lo, hi = trial_range(d)
                    name = ("pycubrid " if drv == "pycubrid" else "CUBRIDdb ") + v
                    ratio = sr = ""
                    if ref:
                        ratio = f"{t['median'] / cstat(ref)['median']:.2f}x"
                        sr = f"{s['median'] / cstat(ref, 'stmt')['median']:.2f}x"
                    out.append(
                        f"| {n:,} | {name} | {t['median']:.1f} | "
                        f"{t['q3'] - t['q1']:.1f} | {lo:.1f}-{hi:.1f} | {s['median']:.1f} | "
                        f"{c['median']:.1f} | {ratio} | {sr} |"
                    )
                    summary.setdefault(tag, {})[f"{op}/{n}/{drv}/{v}"] = {
                        "total": t,
                        "stmt": s,
                        "commit": c,
                        "trial_median_range": [lo, hi],
                    }


# --------------------------------------------------------------------------- phases


PHASE_KEYS = [
    ("render_insitu", "render params -> SQL"),
    ("pkt_write", "packet write"),
    ("send", "socket sendall"),
    ("wait", "wait for reply (server+RTT)"),
    ("recv_rest", "receive rest"),
    ("pkt_parse", "packet parse"),
    ("residual_ns", "residual (driver glue)"),
]


def phases_tables(
    files: list[dict[str, Any]], title: str, out: list[str], summary: dict[str, Any], tag: str
) -> None:
    if not files:
        return
    rtt = [x for f in files for x in f["results"]["empty_request_roundtrip_ns"]]
    out.append(f"\n#### {title}\n")
    out.append(
        f"Empty request (CHECK_CAS) round trip on this host: median "
        f"{med(rtt) / 1e3:.0f} us (IQR {pct(rtt, 25) / 1e3:.0f}-{pct(rtt, 75) / 1e3:.0f} us, "
        f"n={len(rtt)}).\n"
    )
    summary.setdefault(tag, {})["empty_rtt_us"] = med(rtt) / 1e3
    keys = sorted(
        {k for f in files for k in f["results"] if "/" in k},
        key=lambda k: (int(k.split("/")[0]), k.split("/")[1]),
    )
    hdr = (
        "| Rows | Path | stmt ms | "
        + " | ".join(n for _, n in PHASE_KEYS)
        + " | round trips | bytes out | commit ms |"
    )
    out.append(hdr)
    out.append("|---:|---|---:|" + "---:|" * len(PHASE_KEYS) + "---:|---:|---:|")
    for k in keys:
        recs = [r for f in files for r in f["results"].get(k, {}).get("iterations", [])]
        if not recs:
            continue
        n, v = k.split("/")
        stmt = med(r["stmt_ns"] for r in recs)
        cells, rec = [], {}
        for key, _ in PHASE_KEYS:
            m = med(r[key] for r in recs)
            rec[key] = ms(m)
            cells.append(f"{ms(m):.1f} ({m / stmt * 100:.0f}%)")
        rt = med(r["round_trips"] for r in recs)
        out.append(
            f"| {int(n):,} | {v} | {ms(stmt):.1f} | "
            + " | ".join(cells)
            + f" | {rt:,.0f} | {med(r['bytes_out'] for r in recs):,.0f} | "
            f"{ms(med(r['commit_ns'] for r in recs)):.1f} |"
        )
        extra = {}
        if v == "executemany":
            extra["render_standalone_ms"] = ms(med(r["render_standalone_ns"] for r in recs))
        summary[tag][k] = {
            "stmt_ms": ms(stmt),
            **rec,
            "round_trips": rt,
            "n_iterations": len(recs),
            "stmt_ms_min_max": [
                ms(min(r["stmt_ns"] for r in recs)),
                ms(max(r["stmt_ns"] for r in recs)),
            ],
            **extra,
        }
    out.append("\nPer-row view (microseconds per row, medians):\n")
    out.append(
        "| Rows | Path | stmt us/row | render | packet write+parse | send+wait+recv "
        "(server+network) | residual | requests per row | us per request (send+wait+recv) |"
    )
    out.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for k in keys:
        s = summary[tag].get(k)
        if not s:
            continue
        n, v = k.split("/")
        n = int(n)
        net = s["send"] + s["wait"] + s["recv_rest"]
        rt = s["round_trips"]
        per_req = f"{net * 1e3 / rt:.0f}" if v != "executemany" else "-"
        out.append(
            f"| {n:,} | {v} | {s['stmt_ms'] / n * 1e3:.1f} | "
            f"{s['render_insitu'] / n * 1e3:.1f} | "
            f"{(s['pkt_write'] + s['pkt_parse']) / n * 1e3:.1f} | "
            f"{net / n * 1e3:.1f} | {s['residual_ns'] / n * 1e3:.1f} | "
            f"{rt / n:.2f} | {per_req} |"
        )


# --------------------------------------------------------------------------- broker SQL log

TS = re.compile(r"^(\d\d-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) ")


def ts_ms(line: str) -> float | None:
    m = TS.match(line)
    if not m:
        return None
    t = dt.datetime.strptime("20" + m.group(1), "%Y-%m-%d %H:%M:%S.%f")
    return t.timestamp() * 1000


def parse_sqllog(path: str) -> list[list[str]]:
    sections: list[list[str]] = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("### "):
                sections.append([])
            elif sections:
                sections[-1].append(line)
    return sections


def slice_run(sections: list[list[str]], marker: str) -> dict[str, Any] | None:
    for sec in sections:
        for i, ln in enumerate(sec):
            if "prepare" in ln and marker in ln:
                j = i
                while (
                    j < len(sec)
                    and "disconnect" not in sec[j]
                    and not (j > i and "CLIENT IP" in sec[j])
                ):
                    j += 1
                return analyse(sec[i:j])
    return None


def analyse(lines: list[str]) -> dict[str, Any]:
    r: dict[str, Any] = {"batch_ms": None, "commit_server_ms": None}
    stmts_done = prep = pc = 0
    first_prep: float | None = None
    last_done: float | None = None
    sum_exec = 0.0
    seen_marker_done = False
    batch0 = batch1 = None
    batch_lines = batch_pc = 0
    commit_next = False
    for ln in lines:
        t = ts_ms(ln)
        if "execute_batch end" in ln:
            batch1 = t
        elif re.search(r"\) execute_batch \d+", ln):
            batch0 = t
        elif re.search(r"\) batch \d+ :", ln):
            batch_lines += 1
            batch_pc += ln.rstrip().endswith("(PC)")
        elif re.search(r"\) prepare srv_h_id", ln):
            prep += 1
            pc += ln.rstrip().endswith("(PC)")
        elif re.search(r"\) prepare \d+ ", ln):
            if seen_marker_done and first_prep is None:
                first_prep = t
        elif re.search(r"\) execute(_all)? \d+ tuple", ln):
            if not seen_marker_done:  # the marker SELECT itself
                seen_marker_done = True
                continue
            stmts_done += 1
            last_done = t
            m = re.search(r"time ([\d.]+)$", ln.strip())
            sum_exec += float(m.group(1)) * 1000 if m else 0
        elif "end_tran COMMIT" in ln:
            commit_next = True
        elif commit_next and "end_tran 0 time" in ln:
            m = re.search(r"time ([\d.]+)", ln)
            r["commit_server_ms"] = float(m.group(1)) * 1000
            commit_next = False
    if batch0 is not None and batch1 is not None:
        r["batch_ms"] = batch1 - batch0
    r.update(
        {
            "batch_statements_logged": batch_lines,
            "batch_statements_plan_cache_hit": batch_pc,
            "executes": stmts_done,
            "prepares": prep,
            "prepare_plan_cache_hit": pc,
            "first_prepare_to_last_execute_ms": (last_done - first_prep)
            if first_prep and last_done
            else None,
            "sum_logged_exec_time_ms": sum_exec,
        }
    )
    return r


def serverlog_tables(raw: str, out: list[str], summary: dict[str, Any]) -> None:
    dump = os.path.join(raw, "sqllog_slices.txt")
    if not os.path.exists(dump) and os.path.exists(dump + ".gz"):
        dump += ".gz"
    files = load(raw, "serverlog_*.json")
    if not files or not os.path.exists(dump):
        return
    sections = parse_sqllog(dump)
    groups: dict[tuple[str, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for f in files:
        drv = f["meta"]["driver"]
        for run in f["results"]["runs"]:
            a = slice_run(sections, run["marker"])
            if a is None:
                continue
            a.update(
                {"client_stmt_ms": ms(run["stmt_ns"]), "client_commit_ms": ms(run["commit_ns"])}
            )
            groups[(run["op"], run["rows"], drv, run["variant"])].append(a)
    out.append("\n### Broker SQL log (CAS side), per run\n")
    out.append(
        "Slices of `broker1_*.sql.log` (SQL_LOG=ON, 1 ms timestamp resolution) between a "
        "unique marker statement and the disconnect. `server batch span` is the time "
        "between the `execute_batch N` and `execute_batch end` lines (one CAS processing "
        "N literal statements). For per-row paths the log has no usable per-statement time "
        "(all `time` fields are 0.000-0.001 s), so only the first-prepare-to-last-execute "
        "span is shown, which includes the client's think time between requests.\n"
    )
    out.append(
        "| Op | Rows | Path | client stmt ms | server batch span ms | prepare-to-last-exec "
        "span ms | prepares (plan-cache hit) | executes | batch stmts (plan-cache hit) | "
        "server commit ms |"
    )
    out.append("|---|---:|---|---:|---:|---:|---|---:|---|---:|")

    def m(rs: list[dict[str, Any]], k: str) -> str:
        v = [x[k] for x in rs if x.get(k) is not None]
        return f"{med(v):.0f}" if v else "-"

    for key in sorted(groups):
        op, n, drv, v = key
        rs = groups[key]
        prepares = med(x["prepares"] for x in rs)
        prepare_hits = med(x["prepare_plan_cache_hit"] for x in rs)
        pcs = f"{prepares:.0f} ({prepare_hits:.0f})"
        logged = med(x["batch_statements_logged"] for x in rs)
        logged_hits = med(x["batch_statements_plan_cache_hit"] for x in rs)
        bs = f"{logged:.0f} ({logged_hits:.0f})"
        out.append(
            f"| {op} | {n:,} | {drv} {v} | {m(rs, 'client_stmt_ms')} | {m(rs, 'batch_ms')} | "
            f"{m(rs, 'first_prepare_to_last_execute_ms')} | {pcs} | "
            f"{m(rs, 'executes')} | {bs} | {m(rs, 'commit_server_ms')} |"
        )
        summary.setdefault("serverlog", {})[f"{op}/{n}/{drv}/{v}"] = rs


# --------------------------------------------------------------------------- profile / memory


def profile_section(raw: str, derived: str, out: list[str], summary: dict[str, Any]) -> None:
    files = load(raw, "profile_pycubrid_t*.json")
    if not files:
        return
    r = files[0]["results"]
    over = [
        p / u
        for f in files
        for p, u in zip(f["results"]["profiled_wall_ns"], f["results"]["unprofiled_wall_ns"])
    ]
    out.append("\n### cProfile of `executemany` at 10,000 rows (client side)\n")
    out.append(
        f"{len(files)} runs x {r['reps']} profiled calls. Profiler overhead factor "
        f"(profiled / unprofiled wall, median): {med(over):.2f}x. Wall time inside the "
        "profiler is not representative; the *shares* are what matter. The full pstats "
        "listings are in `profile_cumulative.txt` and `profile_tottime.txt`.\n"
    )
    for key in ("cumulative", "tottime"):
        with open(os.path.join(derived, f"profile_{key}.txt"), "w") as fh:
            fh.write(r["pstats"][key])
        out.append(f"\nTop by {key} (first run, 3 calls accumulated):\n")
        out.append("```")
        out.extend(r["pstats"][key].splitlines()[:26])
        out.append("```")
    summary["profile"] = {"profiler_overhead_median": med(over)}


def memory_section(raw: str, out: list[str], summary: dict[str, Any]) -> None:
    files = load(raw, "memory_pycubrid_t*.json")
    if not files:
        return
    r = files[0]["results"]
    out.append("\n### Memory (tracemalloc, Python heap)\n")
    out.append("| Rows | median peak | bytes per row | peaks |")
    out.append("|---:|---:|---:|---|")
    for n in (100, 1000, 10000):
        p = r[f"peak_{n}"]
        out.append(
            f"| {n:,} | {p['median']:,} B | {p['bytes_per_row']:.0f} | "
            f"{', '.join(f'{x:,}' for x in p['peaks'])} |"
        )
    b = r["breakdown_10000"]
    out.append(
        f"\nSample rendered SQL ({b['sample_sql_chars']} chars): `{b['sample_sql']}`; "
        f"`sys.getsizeof(str)` = {b['sample_sql_sys_getsizeof']} B, "
        f"`sys.getsizeof(str.encode())` = {b['sample_encoded_getsizeof']} B.\n"
    )
    out.append(
        f"Baseline {b['baseline_bytes']:,} B, peak {b['peak_bytes']:,} B "
        f"({(b['peak_bytes'] - b['baseline_bytes']) / 10000:.0f} B/row), still allocated after "
        f"the call {b['end_bytes']:,} B.\n"
    )
    for st in ("after_render", "before_finalize"):
        s = b["stages"].get(st)
        if not s:
            continue
        out.append(
            f"\nTop allocations, stage `{st}` (traced current {s['current_bytes']:,} B = "
            f"{s['current_bytes'] / 10000:.0f} B/row):\n"
        )
        out.append("| allocation site | bytes | B/row | blocks |")
        out.append("|---|---:|---:|---:|")
        for t in s["top"][:8]:
            w = t["where"].replace("/", "/")
            w = re.sub(r"^.*site-packages/", "", w)
            out.append(f"| `{w}` | {t['bytes']:,} | {t['bytes'] / 10000:.1f} | {t['count']:,} |")
    summary["memory"] = {k: v for k, v in r.items() if k != "breakdown_10000"}
    summary["memory"]["breakdown_10000"] = {k: v for k, v in b.items()}


# --------------------------------------------------------------------------- main


def main() -> None:
    run = sys.argv[1]
    raw, derived = os.path.join(run, "raw"), os.path.join(run, "derived")
    os.makedirs(derived, exist_ok=True)
    out: list[str] = ["# pycubrid executemany overhead: derived summary\n"]
    summary: dict[str, Any] = {}
    meta = {}
    for pat in ("compare_pycubrid_t1.json", "compare_cubriddb_t1.json"):
        p = os.path.join(raw, pat)
        if os.path.exists(p):
            with open(p) as fh:
                meta[pat] = json.load(fh)["meta"]
    summary["meta"] = meta

    out.append("## 1. End-to-end comparison of execution paths\n")
    cmp_files = load(raw, "compare_*_t*.json")
    if cmp_files:
        acc = pooled_compare(cmp_files)
        compare_tables(acc, "SQL_LOG=ON (default)", out, summary, "compare")
    out.append("\n## 2. Client/server phase split (pycubrid, INSERT)\n")
    phases_tables(load(raw, "phases_pycubrid_t*.json"), "SQL_LOG=ON", out, summary, "phases")
    serverlog_tables(raw, out, summary)
    out.append("\n## 3. Client profile\n")
    profile_section(raw, derived, out, summary)
    out.append("\n## 4. Memory\n")
    memory_section(raw, out, summary)
    out.append("\n## 5. Sensitivity runs (INSERT)\n")
    for tag, pat, title in (
        ("sqllogoff", "sqllogoff_*_t*.json", "broker SQL_LOG=OFF"),
        ("direct", "direct_*_t*.json", "direct container IP (no docker-proxy)"),
    ):
        fs = [f for f in load(raw, pat) if f["meta"]["stage"] == "compare"]
        if fs:
            compare_tables(pooled_compare(fs), title, out, summary, tag)
    phases_tables(
        load(raw, "sqllogoff_phases_pycubrid_t*.json"),
        "Phase split with SQL_LOG=OFF",
        out,
        summary,
        "phases_sqllogoff",
    )
    with open(os.path.join(derived, "summary.json"), "w") as fh:
        json.dump(
            summary, fh, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o)
        )
    with open(os.path.join(derived, "summary.md"), "w") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"wrote {derived}/summary.md")


if __name__ == "__main__":
    main()
