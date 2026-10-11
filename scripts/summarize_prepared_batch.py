#!/usr/bin/env python3
"""Summarise experiments/pycubrid-prepared-batch runs (cubrid-lab/pycubrid#836).

Usage: summarize_prepared_batch.py <run-dir>

Reads raw/compare_<version>_t<N>.json and raw/semantics_<version>.json, writes
derived/summary.json and derived/summary.md. Times are medians pooled over every
measured iteration of every trial (commit included); the per-trial median range is
reported next to each pooled median.
"""

from __future__ import annotations

import glob
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from typing import Any

VARIANTS = ["executemany", "native_prepared", "execute_array"]
OPS = ["insert", "update", "delete", "merge"]


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def load_compare(raw: str) -> dict[str, dict[str, Any]]:
    pooled: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": [], "stmt": [], "commit": [], "trial_medians": []}
    )
    meta: dict[str, Any] = {}
    for path in sorted(glob.glob(os.path.join(raw, "compare_*_t*.json"))):
        m = re.match(r"compare_(.+)_t(\d+)\.json", os.path.basename(path))
        if not m:
            continue
        version = m.group(1)
        with open(path) as fh:
            doc = json.load(fh)
        meta.setdefault(version, doc["meta"].get("server_version"))
        for key, r in doc["results"].items():
            p = pooled[f"{version}/{key}"]
            p["total"] += r["total_ns"]
            p["stmt"] += r["stmt_ns"]
            p["commit"] += r["commit_ns"]
            p["trial_medians"].append(statistics.median(r["total_ns"]) / 1e6)
    return {"pooled": pooled, "server_versions": meta}


def summarise(run_dir: str) -> dict[str, Any]:
    raw = os.path.join(run_dir, "raw")
    data = load_compare(raw)
    pooled = data["pooled"]
    rows: list[dict[str, Any]] = []
    versions = sorted({k.split("/")[0] for k in pooled}, key=version_key, reverse=True)
    for version in versions:
        for op in OPS:
            sizes = sorted(
                {int(k.split("/")[2]) for k in pooled if k.startswith(f"{version}/{op}/")}
            )
            for n in sizes:
                rec: dict[str, Any] = {"version": version, "op": op, "rows": n}
                for v in VARIANTS:
                    p = pooled.get(f"{version}/{op}/{n}/{v}")
                    if not p:
                        continue
                    rec[v] = {
                        "median_ms": statistics.median(p["total"]) / 1e6,
                        "stmt_median_ms": statistics.median(p["stmt"]) / 1e6,
                        "commit_median_ms": statistics.median(p["commit"]) / 1e6,
                        "trial_median_range_ms": [min(p["trial_medians"]), max(p["trial_medians"])],
                        "iterations": len(p["total"]),
                        "trials": len(p["trial_medians"]),
                    }
                em = rec.get("executemany", {}).get("median_ms")
                npd = rec.get("native_prepared", {}).get("median_ms")
                ea = rec.get("execute_array", {}).get("median_ms")
                if em and ea:
                    rec["execute_array_over_executemany"] = ea / em
                if em and npd:
                    rec["native_prepared_over_executemany"] = npd / em
                if npd and ea:
                    rec["execute_array_over_native_prepared"] = ea / npd
                if ea:
                    rec["execute_array_us_per_row"] = ea * 1000 / n
                rows.append(rec)
    return {
        "server_versions": data["server_versions"],
        "rows": rows,
        "semantics": summarise_semantics(raw),
    }


def _norm(obj: Any) -> str:
    """Drop text that legitimately differs per server or run (OIDs, ids, paths, owner)."""
    s = json.dumps(obj, sort_keys=True, default=str)
    s = re.sub(r'"message": "(?:[^"\\]|\\.)*"', '"message": "-"', s)
    s = re.sub(r'\["error", (-?\d+), "(?:[^"\\]|\\.)*"\]', r'["error", \1, "-"]', s)
    s = re.sub(r'"max_id": \d+', '"max_id": "-"', s)
    s = re.sub(r'"last_insert_id_after": "\d+"', '"last_insert_id_after": "<id>"', s)
    return s


def summarise_semantics(raw: str) -> dict[str, Any]:
    docs = {}
    for path in glob.glob(os.path.join(raw, "semantics_*.json")):
        version = re.match(r"semantics_(.+)\.json", os.path.basename(path)).group(1)
        with open(path) as fh:
            docs[version] = json.load(fh)
    if not docs:
        return {}
    versions = sorted(docs, key=version_key, reverse=True)
    ref = docs[versions[0]]["results"]
    differences = []
    for version in versions[1:]:
        res = docs[version]["results"]
        for path_key, scen in ref.items():
            for name, value in scen.items():
                if _norm(value) != _norm(res.get(path_key, {}).get(name)):
                    differences.append({"version": version, "path": path_key, "scenario": name})
    return {
        "versions": versions,
        "reference": versions[0],
        "differences_ignoring_message_text": differences,
        "server_versions": {v: docs[v]["meta"].get("server_version") for v in versions},
    }


def fmt(x: float | None, nd: int = 1) -> str:
    return "-" if x is None else f"{x:,.{nd}f}"


def markdown(s: dict[str, Any]) -> str:
    out = ["# pycubrid prepared batch paths: summary", ""]
    out.append("Pooled medians of every measured iteration over all trials, commit included (ms).")
    out.append("Ratios are against the shipped `executemany()` (EXECUTE_BATCH with literal SQL).")
    out.append("")
    for version in sorted({r["version"] for r in s["rows"]}, key=version_key, reverse=True):
        sv = s["server_versions"].get(version, "")
        out.append(f"## CUBRID {version} ({sv})")
        out.append("")
        out.append(
            "| Op | Rows | executemany ms | prepared loop ms | EXECUTE_ARRAY ms | "
            "loop / executemany | ARRAY / executemany | ARRAY / loop | ARRAY us/row | trials |"
        )
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in [x for x in s["rows"] if x["version"] == version]:
            out.append(
                f"| {r['op']} | {r['rows']:,} | "
                f"{fmt(r.get('executemany', {}).get('median_ms'))} | "
                f"{fmt(r.get('native_prepared', {}).get('median_ms'))} | "
                f"{fmt(r.get('execute_array', {}).get('median_ms'))} | "
                f"{fmt(r.get('native_prepared_over_executemany'), 2)} | "
                f"{fmt(r.get('execute_array_over_executemany'), 2)} | "
                f"{fmt(r.get('execute_array_over_native_prepared'), 2)} | "
                f"{fmt(r.get('execute_array_us_per_row'), 0)} | "
                f"{r.get('executemany', {}).get('trials', '-')} |"
            )
        out.append("")
        out.append(
            "Statement phase only (commit excluded), median ms, "
            "executemany / prepared loop / EXECUTE_ARRAY, and commit medians:"
        )
        out.append("")
        out.append(
            "| Op | Rows | executemany | prepared loop | EXECUTE_ARRAY | ARRAY / executemany "
            "| commit executemany | commit loop | commit ARRAY |"
        )
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in [x for x in s["rows"] if x["version"] == version]:
            st = [r.get(v, {}).get("stmt_median_ms") for v in VARIANTS]
            cm = [r.get(v, {}).get("commit_median_ms") for v in VARIANTS]
            ratio = st[2] / st[0] if st[0] and st[2] else None
            out.append(
                f"| {r['op']} | {r['rows']:,} | "
                + " | ".join(fmt(x) for x in st)
                + f" | {fmt(ratio, 2)} | "
                + " | ".join(fmt(x) for x in cm)
                + " |"
            )
        out.append("")
        out.append("Per-trial median range (ms), executemany / prepared loop / EXECUTE_ARRAY:")
        out.append("")
        for r in [x for x in s["rows"] if x["version"] == version]:
            parts = []
            for v in VARIANTS:
                rng = r.get(v, {}).get("trial_median_range_ms")
                parts.append("-" if not rng else f"{rng[0]:,.1f}-{rng[1]:,.1f}")
            out.append(f"- {r['op']} {r['rows']:,}: " + " / ".join(parts))
        out.append("")
    sem = s.get("semantics") or {}
    if sem:
        out.append("## Semantics probes")
        out.append("")
        out.append(
            f"Reference: CUBRID {sem['reference']}. Probed: "
            + ", ".join(f"{v} ({sem['server_versions'][v]})" for v in sem["versions"])
            + "."
        )
        diffs = sem["differences_ignoring_message_text"]
        if diffs:
            out.append("")
            out.append("Outcomes that differ from the reference (message text ignored):")
            out.append("")
            for d in diffs:
                out.append(f"- {d['version']}: {d['path']} {d['scenario']}")
        else:
            out.append("")
            out.append(
                "Every probe outcome (which rows were executed, applied, committed or rolled "
                "back, per-row counts, raised class and code, stored values) is identical on "
                "all probed versions once server message text is ignored."
            )
        out.append("")
    return "\n".join(out)


def main() -> None:
    run_dir = sys.argv[1]
    s = summarise(run_dir)
    d = os.path.join(run_dir, "derived")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "summary.json"), "w") as fh:
        json.dump(s, fh, indent=1)
    with open(os.path.join(d, "summary.md"), "w") as fh:
        fh.write(markdown(s))
    print(markdown(s))


if __name__ == "__main__":
    main()
