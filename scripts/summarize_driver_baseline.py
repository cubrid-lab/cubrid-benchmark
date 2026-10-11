#!/usr/bin/env python3
"""Summarize driver_baseline_bench.py raw output into derived/summary.{json,md}.

Pools the measured samples of every trial per driver label, reports median / p95 / IQR,
the spread of the per-trial medians, and compares:

* pycubrid main vs pycubrid 1.10.0 (regression check), and
* pycubrid main vs CUBRIDdb (driver overhead).

A regression is flagged when main is more than 5% slower at the median AND the pooled
interquartile ranges of the two distributions do not overlap. A bootstrap 95% CI of the
median ratio and a Mann-Whitney U p-value are reported alongside.

Each main-vs-1.10.0 row also reports the rule's minimum detectable slowdown: the smallest
uniform slowdown of a distribution with the observed spread that the rule could flag, i.e.
max(5%, q3/q1 - 1) of the pooled samples (for throughput, max(5%, 1 - q1/q3)). It is computed
for each of the two versions; the larger value is the conservative one. Per-trial medians are
compared too: the min-max range of each version's trial medians and whether they overlap.

If ``<run-dir>/raw-connect/`` exists (``run.sh`` with ``CASES=connect``), the connect/close
cases are taken from it instead of ``raw/``.

Usage: python scripts/summarize_driver_baseline.py <run-dir>
"""

from __future__ import annotations

import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

MAIN, REL, CDB = "pycubrid-main", "pycubrid-1.10.0", "cubriddb"
THRESH = 0.05
BOOT = 1000


def pct(data: list[float], p: float) -> float:
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    return s[int(k)] if f == c else s[f] * (c - k) + s[c] * (k - f)


def describe(ms: list[float]) -> dict[str, float]:
    return {"n": len(ms), "median": pct(ms, 50), "p95": pct(ms, 95), "q1": pct(ms, 25),
            "q3": pct(ms, 75), "iqr": pct(ms, 75) - pct(ms, 25),
            "stdev": statistics.stdev(ms) if len(ms) > 1 else 0.0, "mean": statistics.mean(ms)}


def mann_whitney_p(a: list[float], b: list[float]) -> float:
    allv = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks = [0.0] * len(allv)
    i, tie_term = 0, 0.0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = r
        t = j - i + 1
        tie_term += t ** 3 - t
        i = j + 1
    n1, n2 = len(a), len(b)
    r1 = sum(r for r, (_, g) in zip(ranks, allv) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    n = n1 + n2
    sigma = math.sqrt(n1 * n2 / 12 * ((n + 1) - tie_term / (n * (n - 1))))
    if sigma == 0:
        return 1.0
    z = (abs(u1 - mu) - 0.5) / sigma
    return min(1.0, math.erfc(max(z, 0.0) / math.sqrt(2)))


def boot_ratio_ci(a: list[float], b: list[float], seed: int = 797) -> tuple[float, float]:
    rng = random.Random(seed)
    rs = []
    for _ in range(BOOT):
        ma = statistics.median(rng.choices(a, k=len(a)))
        mb = statistics.median(rng.choices(b, k=len(b)))
        rs.append(ma / mb)
    return pct(rs, 2.5), pct(rs, 97.5)


CONNECT_KEYS = ("connect_close", "connect_only", "close_only")
RERUN_DIR = "raw-connect"


def load(run_dir: Path, sub: str = "raw") -> dict[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in sorted((run_dir / sub).glob("*_t*.json")):
        d = json.loads(f.read_text())
        if d["metadata"].get("quick"):
            continue
        by[d["metadata"]["label"]].append(d)
    return by


def metric_samples(trials: list[dict[str, Any]], key: str) -> tuple[list[float], list[float]]:
    pooled, trial_medians = [], []
    for d in trials:
        if key not in d["results"]:
            continue
        ms = [t / 1e6 for t in d["results"][key]["samples_ns"]]
        pooled.extend(ms)
        trial_medians.append(statistics.median(ms))
    return pooled, trial_medians


def conc_samples(trials: list[dict[str, Any]], mode: str, c: int) -> dict[str, Any] | None:
    thr, tmed, p50s, p95s = [], [], [], []
    for d in trials:
        for e in d["concurrency"]:
            if e["mode"] == mode and e["concurrency"] == c:
                q = e["queries_per_round"]
                t = [q / (ns / 1e9) for ns in e["round_wall"]["samples_ns"]]
                thr.extend(t)
                tmed.append(statistics.median(t))
                p50s.append(e["query_latency"]["stats"]["median_ms"])
                p95s.append(e["query_latency"]["stats"]["p95_ms"])
    if not thr:
        return None
    return {"throughput_qps": describe(thr), "trial_median_qps": [min(tmed), max(tmed)],
            "query_p50_ms_median_of_trials": statistics.median(p50s),
            "query_p95_ms_median_of_trials": statistics.median(p95s),
            "throughput_samples": thr}


UNITS = {"connect_close": 1, "connect_only": 1, "select_pk": 1, "insert_commit": 1,
         "update_commit": 1, "delete_commit": 1, "insert_execute_only": 1,
         "update_execute_only": 1, "delete_execute_only": 1, "close_only": 1}


def units(key: str) -> tuple[int, str]:
    if key.startswith("executemany_"):
        return int(key.split("_")[1]), "rows/s"
    if key.startswith(("fetchone_", "fetchmany_", "fetchall_")):
        return 10_000, "rows/s"
    return 1, "ops/s"


def fmt(v: float, nd: int = 3) -> str:
    return f"{v:,.{nd}f}"


def mde(d: dict[str, float], higher_is_better: bool = False) -> float:
    """Smallest uniform slowdown (fraction) the >5% + non-overlapping-IQR rule can flag."""
    spread = 1 - d["q1"] / d["q3"] if higher_is_better else d["q3"] / d["q1"] - 1
    return max(THRESH, spread)


def ranges_overlap(a: list[float], b: list[float]) -> bool:
    return not (a[0] > b[1] or b[0] > a[1])


def compare(a: list[float], b: list[float], da: dict[str, float],
            db: dict[str, float]) -> dict[str, Any]:
    ratio = da["median"] / db["median"]
    lo, hi = boot_ratio_ci(a, b)
    overlap = not (da["q1"] > db["q3"] or db["q1"] > da["q3"])
    return {"ratio": ratio, "ci95": [lo, hi], "p_mw": mann_whitney_p(a, b),
            "iqr_overlap": overlap}


def main() -> None:
    run_dir = Path(sys.argv[1])
    by = load(run_dir)
    rerun = load(run_dir, RERUN_DIR)
    labels = [x for x in (MAIN, REL, CDB) if x in by]
    keys: list[str] = []
    for d in by[MAIN]:
        for k in d["results"]:
            if k not in keys:
                keys.append(k)

    summary: dict[str, Any] = {"labels": {}, "metrics": {}, "concurrency": {}, "memory": {},
                               "comparisons": {"main_vs_1.10.0": {}, "main_vs_cubriddb": {}}}
    if rerun:
        summary["connect_rerun"] = {
            "source": f"{RERUN_DIR}/", "keys": list(CONNECT_KEYS),
            "trials": {lab: len(v) for lab, v in rerun.items()},
            "timestamps_utc": sorted(d["metadata"]["environment"]["timestamp_utc"]
                                     for v in rerun.values() for d in v),
            "driver": {lab: v[0]["metadata"]["driver"] for lab, v in rerun.items()},
        }
    for lab in labels:
        md = by[lab][0]["metadata"]
        summary["labels"][lab] = {
            "trials": len(by[lab]), "driver": md["driver"], "environment": md["environment"],
            "elapsed_s": [d["metadata"]["elapsed_s"] for d in by[lab]],
            "ru_maxrss_kb": [d["metadata"]["ru_maxrss_kb"] for d in by[lab]],
            "loadavg_at_start": [d["metadata"]["environment"]["loadavg_at_start"]
                                 for d in by[lab]],
        }

    pooled: dict[tuple[str, str], list[float]] = {}
    for k in keys:
        summary["metrics"][k] = {}
        for lab in labels:
            src = rerun if rerun and k in CONNECT_KEYS else by
            s, tm = metric_samples(src.get(lab, []), k)
            if not s:
                continue
            pooled[(lab, k)] = s
            d = describe(s)
            u, unit = units(k)
            d["throughput_at_median"] = u / (d["median"] / 1000)
            d["throughput_unit"] = unit
            d["trial_median_range"] = [min(tm), max(tm)]
            summary["metrics"][k][lab] = d
        m = summary["metrics"][k]
        if MAIN in m and REL in m:
            c = compare(pooled[(MAIN, k)], pooled[(REL, k)], m[MAIN], m[REL])
            c["regression"] = c["ratio"] > 1 + THRESH and not c["iqr_overlap"]
            c["improvement"] = c["ratio"] < 1 - THRESH and not c["iqr_overlap"]
            c["min_detectable_slowdown"] = {"main": mde(m[MAIN]), "1.10.0": mde(m[REL])}
            c["trial_median_range"] = {"main": m[MAIN]["trial_median_range"],
                                       "1.10.0": m[REL]["trial_median_range"]}
            c["trial_medians_overlap"] = ranges_overlap(m[MAIN]["trial_median_range"],
                                                        m[REL]["trial_median_range"])
            summary["comparisons"]["main_vs_1.10.0"][k] = c
        if MAIN in m and CDB in m:
            summary["comparisons"]["main_vs_cubriddb"][k] = compare(
                pooled[(MAIN, k)], pooled[(CDB, k)], m[MAIN], m[CDB])

    conc_keys = []
    for lab in labels:
        for mode in ("sync-threads", "async-tasks"):
            for c in (1, 8, 32):
                r = conc_samples(by[lab], mode, c)
                if r:
                    summary["concurrency"][f"{lab}|{mode}|{c}"] = r
                    conc_keys.append((lab, mode, c))
    for mode in ("sync-threads", "async-tasks"):
        for c in (1, 8, 32):
            a = summary["concurrency"].get(f"{MAIN}|{mode}|{c}")
            b = summary["concurrency"].get(f"{REL}|{mode}|{c}")
            if a and b:
                # throughput: higher is better, so regression is ratio < 0.95
                ra = a["throughput_qps"]["median"] / b["throughput_qps"]["median"]
                da, db = a["throughput_qps"], b["throughput_qps"]
                overlap = not (da["q1"] > db["q3"] or db["q1"] > da["q3"])
                summary["comparisons"]["main_vs_1.10.0"][f"conc_{mode}_{c}"] = {
                    "throughput_ratio": ra, "iqr_overlap": overlap,
                    "p_mw": mann_whitney_p(a["throughput_samples"], b["throughput_samples"]),
                    "regression": ra < 1 - THRESH and not overlap,
                    "min_detectable_slowdown": {"main": mde(da, True), "1.10.0": mde(db, True)},
                    "trial_median_range": {"main": a["trial_median_qps"],
                                           "1.10.0": b["trial_median_qps"]},
                    "trial_medians_overlap": ranges_overlap(a["trial_median_qps"],
                                                            b["trial_median_qps"])}

    for lab in labels:
        mem: dict[str, list[int]] = defaultdict(list)
        for d in by[lab]:
            for k, v in d["memory"].items():
                mem[k].extend(v["tracemalloc_peak_bytes"])
        summary["memory"][lab] = {k: {"median_bytes": int(statistics.median(v)),
                                      "min": min(v), "max": max(v), "n": len(v)}
                                  for k, v in mem.items()}

    phase = {}
    for lab in (MAIN, REL):
        f = run_dir / "raw" / f"phase_decomposed_{lab}.json"
        if f.exists():
            phase[lab] = json.loads(f.read_text())
    summary["phase_decomposed"] = {lab: p["results"] for lab, p in phase.items()}

    for v in summary["concurrency"].values():
        v.pop("throughput_samples", None)
    (run_dir / "derived").mkdir(exist_ok=True)
    (run_dir / "derived" / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    (run_dir / "derived" / "summary.md").write_text(render(summary, keys, phase))
    print(f"wrote {run_dir / 'derived'}")


LABEL_ROWS = [
    ("connect_close", "connect + close"),
    ("connect_only", "connect only"),
    ("select_pk", "SELECT by PK (execute + fetchone)"),
    ("insert_commit", "INSERT 1 row + commit"),
    ("update_commit", "UPDATE 1 row + commit"),
    ("delete_commit", "DELETE 1 row + commit"),
    ("insert_execute_only", "INSERT execute only"),
    ("update_execute_only", "UPDATE execute only"),
    ("delete_execute_only", "DELETE execute only"),
    ("executemany_100", "executemany 100 rows + commit"),
    ("executemany_1000", "executemany 1,000 rows + commit"),
    ("executemany_10000", "executemany 10,000 rows + commit"),
    ("fetchone_10000", "fetchone loop, 10,000 rows"),
    ("fetchmany_10000", "fetchmany(100) loop, 10,000 rows"),
    ("fetchall_10000", "fetchall, 10,000 rows"),
    ("fetchmany_10000_fs1000", "fetchmany(100), fetch_size=1000"),
    ("fetchall_10000_fs1000", "fetchall, fetch_size=1000"),
    ("fetchmany_10000_fs10000", "fetchmany(100), fetch_size=10000"),
    ("fetchall_10000_fs10000", "fetchall, fetch_size=10000"),
]


def render(s: dict[str, Any], keys: list[str], phase: dict[str, Any]) -> str:
    m, cmpr, cmpc = s["metrics"], s["comparisons"]["main_vs_1.10.0"], \
        s["comparisons"]["main_vs_cubriddb"]
    L: list[str] = []
    L.append("# Summary (generated by scripts/summarize_driver_baseline.py)\n")
    L.append("Times are milliseconds per operation; samples from all trials are pooled. "
             "`trial medians` is the min-max of the per-trial medians. Throughput is at the "
             "pooled median.\n")
    if "connect_rerun" in s:
        r = s["connect_rerun"]
        L.append(f"Connect cases ({', '.join(f'`{k}`' for k in r['keys'])}) come from "
                 f"`{r['source']}` ({r['timestamps_utc'][0]} to {r['timestamps_utc'][-1]}), "
                 "a re-measurement of those cases only; every other case comes from `raw/`.\n")
    for lab in (MAIN, REL, CDB):
        if lab not in s["labels"]:
            continue
        L.append(f"\n## {lab}\n")
        L.append("| Operation | n | median | p95 | IQR | stdev | trial medians | throughput |")
        L.append("|---|---:|---:|---:|---:|---:|---|---:|")
        for k, name in LABEL_ROWS:
            d = m.get(k, {}).get(lab)
            if not d:
                continue
            tr = d["trial_median_range"]
            L.append(f"| {name} | {d['n']} | {fmt(d['median'])} | {fmt(d['p95'])} | "
                     f"{fmt(d['iqr'])} | {fmt(d['stdev'])} | {fmt(tr[0])}-{fmt(tr[1])} | "
                     f"{d['throughput_at_median']:,.0f} {d['throughput_unit']} |")
    L.append("\n## pycubrid main vs 1.10.0\n")
    L.append("Ratio = main median / 1.10.0 median (time; >1 is slower). Regression = ratio > "
             "1.05 and pooled IQRs do not overlap. `min detectable` is the smallest uniform "
             "slowdown the rule could flag given the observed spread, max(5%, q3/q1 - 1), for "
             "main / 1.10.0; a slowdown below it is reported as `no change`. `trial medians` "
             "is the min-max of the per-trial medians of main / 1.10.0.\n")
    L.append("| Operation | main median | 1.10.0 median | ratio | bootstrap 95% CI | "
             "Mann-Whitney p | IQRs overlap | min detectable | trial medians main / 1.10.0 "
             "| trial medians overlap | verdict |")
    L.append("|---|---:|---:|---:|---|---:|---|---:|---|---|---|")
    for k, name in LABEL_ROWS:
        c = cmpr.get(k)
        if not c:
            continue
        v = "REGRESSION" if c["regression"] else ("faster" if c["improvement"] else "no change")
        md, tr = c["min_detectable_slowdown"], c["trial_median_range"]
        L.append(f"| {name} | {fmt(m[k][MAIN]['median'])} | {fmt(m[k][REL]['median'])} | "
                 f"{c['ratio']:.3f} | {c['ci95'][0]:.3f}-{c['ci95'][1]:.3f} | "
                 f"{c['p_mw']:.3g} | {'yes' if c['iqr_overlap'] else 'no'} | "
                 f"{md['main']:.0%} / {md['1.10.0']:.0%} | "
                 f"{fmt(tr['main'][0])}-{fmt(tr['main'][1])} / "
                 f"{fmt(tr['1.10.0'][0])}-{fmt(tr['1.10.0'][1])} | "
                 f"{'yes' if c['trial_medians_overlap'] else 'no'} | {v} |")
    L.append("\n## pycubrid main vs CUBRIDdb\n")
    L.append("Ratio = pycubrid main median / CUBRIDdb median (time; >1 means pycubrid is "
             "slower).\n")
    L.append("| Operation | pycubrid main | CUBRIDdb | ratio | bootstrap 95% CI | "
             "Mann-Whitney p |")
    L.append("|---|---:|---:|---:|---|---:|")
    for k, name in LABEL_ROWS:
        c = cmpc.get(k)
        if not c:
            continue
        L.append(f"| {name} | {fmt(m[k][MAIN]['median'])} | {fmt(m[k][CDB]['median'])} | "
                 f"{c['ratio']:.3f} | {c['ci95'][0]:.3f}-{c['ci95'][1]:.3f} | "
                 f"{c['p_mw']:.3g} |")
    L.append("\n## Concurrency: SELECT by PK, 200 queries per worker, one connection per worker\n")
    L.append("| Driver | Mode | Workers | median queries/s | IQR | trial medians | "
             "query p50 ms | query p95 ms |")
    L.append("|---|---|---:|---:|---:|---|---:|---:|")
    for key, v in s["concurrency"].items():
        lab, mode, c = key.split("|")
        t = v["throughput_qps"]
        L.append(f"| {lab} | {mode} | {c} | {t['median']:,.0f} | {t['iqr']:,.0f} | "
                 f"{v['trial_median_qps'][0]:,.0f}-{v['trial_median_qps'][1]:,.0f} | "
                 f"{v['query_p50_ms_median_of_trials']:.3f} | "
                 f"{v['query_p95_ms_median_of_trials']:.3f} |")
    L.append("\nmain vs 1.10.0 throughput ratio (>1 is faster):\n")
    L.append("| Case | ratio | Mann-Whitney p | IQRs overlap | min detectable | "
             "trial medians overlap | verdict |")
    L.append("|---|---:|---:|---|---:|---|---|")
    for k, c in cmpr.items():
        if k.startswith("conc_"):
            md = c["min_detectable_slowdown"]
            L.append(f"| {k} | {c['throughput_ratio']:.3f} | {c['p_mw']:.3g} | "
                     f"{'yes' if c['iqr_overlap'] else 'no'} | "
                     f"{md['main']:.0%} / {md['1.10.0']:.0%} | "
                     f"{'yes' if c['trial_medians_overlap'] else 'no'} | "
                     f"{'REGRESSION' if c['regression'] else 'no regression'} |")
    L.append("\n## Peak Python-heap memory (tracemalloc), median over trials\n")
    L.append("CUBRIDdb allocates rows and buffers in C (CCI); tracemalloc does not see that "
             "memory, so its numbers are a lower bound and not comparable to pycubrid's.\n")
    labs = [x for x in (MAIN, REL, CDB) if x in s["memory"]]
    L.append("| Case | " + " | ".join(labs) + " |")
    L.append("|---|" + "---:|" * len(labs))
    for k in s["memory"][MAIN]:
        L.append(f"| {k} | " + " | ".join(
            f"{s['memory'][x][k]['median_bytes'] / 1024:,.1f} KiB" for x in labs) + " |")
    L.append("\nProcess peak RSS (ru_maxrss) at the end of each trial: " + "; ".join(
        f"{x}: {min(s['labels'][x]['ru_maxrss_kb']) / 1024:.1f}-"
        f"{max(s['labels'][x]['ru_maxrss_kb']) / 1024:.1f} MiB" for x in labs) + "\n")
    if phase:
        L.append("\n## Existing harness: scripts/phase_decomposed_bench.py (single run each)\n")
        L.append("p50 ms (200 iterations, 50 warm-up).\n")
        L.append("| Phase | " + " | ".join(phase) + " | ratio |")
        L.append("|---|" + "---:|" * (len(phase) + 1))
        first = next(iter(phase.values()))["results"]
        for k in first:
            vals = [phase[lab]["results"][k]["p50_ms"] for lab in phase]
            ratio = vals[0] / vals[1] if len(vals) == 2 and vals[1] else float("nan")
            L.append(f"| {k} | " + " | ".join(fmt(v) for v in vals) + f" | {ratio:.3f} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
