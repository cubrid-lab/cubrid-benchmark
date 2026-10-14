# pyright: basic
"""Decide whether benchmark runs are evidence for a regression report.

This is the one eligibility decision used by every regression-reporting path (the workflow's
``nightly_compare.py``, ``make gap-issues`` / ``gap_to_issue.py`` and
``file_regression_issue.py``).

An *independent trial* is one run directory: one complete benchmark command with its own
``run.yaml`` and raw result file. Pedantic rounds, rows and operations inside one result file are
never counted as trials. Run directories that repeat a ``run_id`` or an identical raw result file
are excluded, and the reason is recorded.

Decisions (``decision`` in the report, with the exit code):

- ``error`` (2): a run or its raw result is missing, unreadable or invalid. Never a pass.
- ``not_comparable`` (3): a trial pair fails the ``compare_runs.py`` compatibility checks
  (identity, roles, group, operations, units), or one side mixes software or benchmark source
  revisions, or a source revision is not pinned.
- ``insufficient_evidence`` (0): fewer than ``--min-trials`` independent trials on either side.
  Per-operation results are listed as advisory only.
- ``equivalent`` (0): every operation is within the noise band (``|change| <= --noise``).
- ``investigate`` (0): some operation moved beyond the noise band, but without a reproduced
  decline.
- ``reproduced_regression`` (1): at least one operation's median dropped by ``--fail`` percent
  or more, and every candidate trial is below every baseline trial (the ranges do not overlap),
  with at least ``--min-trials`` trials on each side.

Only ``reproduced_regression`` sets ``eligible_for_issue``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_runs import (  # noqa: E402
    _comparability_problems,
    _load_run,
    _operation_problems,
    _parse_run_yaml,
)

SCHEMA = "regression-evidence/1"
DEFAULT_MIN_TRIALS = 3
DEFAULT_NOISE = 5.0
DEFAULT_FAIL = 10.0
EXIT_CODES = {
    "equivalent": 0,
    "investigate": 0,
    "insufficient_evidence": 0,
    "reproduced_regression": 1,
    "error": 2,
    "not_comparable": 3,
}
UNKNOWN = {"", "unknown", "none", "null"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


VOLATILE_SOFTWARE_KEYS = ("github_run", "benchmark_source_dirty", "verified_versions_file")


def _tested_software(software: Mapping[str, object]) -> dict[str, object]:
    """The software a trial measured, without per-run fields (CI run URL, dirty flag)."""
    return {k: v for k, v in software.items() if k not in VOLATILE_SOFTWARE_KEYS}


def _trial_summary(run: Mapping[str, object]) -> dict[str, object]:
    software = run.get("software_under_test")
    software = software if isinstance(software, Mapping) else {}
    measurement: object = {}
    if run.get("run_yaml_path"):
        measurement = _parse_run_yaml(Path(str(run["run_yaml_path"]))).get("measurement", {})
    measurement = measurement if isinstance(measurement, Mapping) else {}
    return {
        "run_id": run.get("run_id"),
        "date": run.get("date"),
        "dir": run.get("dir"),
        "results_path": run.get("results_path"),
        "comparable_group": run.get("comparable_group"),
        "benchmark_source_sha": software.get("benchmark_source_sha"),
        "benchmark_source_dirty": software.get("benchmark_source_dirty"),
        "software_under_test": dict(software),
        "command": measurement.get("command", "not recorded"),
        "artifact_url": measurement.get("artifact_url", "not recorded"),
    }


def _load_side(
    dirs: Sequence[Path], side: str, errors: list[str], excluded: list[dict[str, str]]
) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    seen_ids: dict[str, str] = {}
    seen_hashes: dict[str, str] = {}
    for run_dir in dirs:
        try:
            run = _load_run(run_dir)
            digest = _sha256(Path(str(run["results_path"])))
        except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError) as exc:
            errors.append(f"{side} run {run_dir}: {exc}")
            continue
        run_id = str(run.get("run_id"))
        if run_id in seen_ids:
            excluded.append(
                {"dir": str(run_dir), "reason": f"repeats run_id {run_id} of {seen_ids[run_id]}"}
            )
            continue
        if digest in seen_hashes:
            excluded.append(
                {
                    "dir": str(run_dir),
                    "reason": f"identical raw results to {seen_hashes[digest]} (same run copied)",
                }
            )
            continue
        seen_ids[run_id] = str(run_dir)
        seen_hashes[digest] = str(run_dir)
        runs.append(run)
    return runs


def _pinning_problems(runs: Sequence[Mapping[str, object]], side: str) -> list[str]:
    problems: list[str] = []
    shas = set()
    software = set()
    for run in runs:
        summary = _trial_summary(run)
        sha = str(summary["benchmark_source_sha"] or "").strip().lower()
        if sha in UNKNOWN:
            problems.append(f"{side} trial {summary['run_id']} has no pinned benchmark_source_sha")
        if str(summary["benchmark_source_dirty"]).strip().lower() != "false":
            problems.append(
                f"{side} trial {summary['run_id']} is not from a verified clean source tree "
                f"(benchmark_source_dirty={summary['benchmark_source_dirty']!r})"
            )
        shas.add(sha)
        tested = _tested_software(summary["software_under_test"])
        software.add(json.dumps(tested, sort_keys=True, default=str))
    if len(shas) > 1:
        problems.append(f"{side} trials mix benchmark source revisions: {sorted(shas)}")
    if len(software) > 1:
        problems.append(f"{side} trials mix software under test")
    return problems


def _classify(
    name: str,
    unit: object,
    baseline_values: list[float],
    candidate_values: list[float],
    noise: float,
    fail: float,
    enough: bool,
) -> dict[str, object]:
    base = statistics.median(baseline_values)
    cand = statistics.median(candidate_values)
    change = (cand - base) / base * 100.0
    separated = max(candidate_values) < min(baseline_values)
    if change <= -fail:
        classification = "reproduced_regression" if enough and separated else "investigate"
    elif change < -noise:
        classification = "investigate"
    elif change > noise:
        classification = "improved"
    else:
        classification = "equivalent"
    return {
        "name": name,
        "unit": unit,
        "baseline_values": baseline_values,
        "candidate_values": candidate_values,
        "baseline_median": round(base, 6),
        "candidate_median": round(cand, 6),
        "baseline_range": [min(baseline_values), max(baseline_values)],
        "candidate_range": [min(candidate_values), max(candidate_values)],
        "percent_change": round(change, 2),
        "ranges_separated": separated,
        "classification": classification if enough else f"advisory:{classification}",
    }


def dedup_key(report: Mapping[str, object]) -> str:
    trials = report.get("trials", {})
    baseline = trials.get("baseline", []) if isinstance(trials, Mapping) else []
    candidate = trials.get("candidate", []) if isinstance(trials, Mapping) else []
    group = baseline[0].get("comparable_group") if baseline else "none"

    def _software(side: list[Mapping[str, object]]) -> str:
        if not side:
            return "none"
        tested = _tested_software(side[0].get("software_under_test") or {})
        return hashlib.sha256(
            json.dumps(tested, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:12]

    return f"regression:{group}:{_software(baseline)}:{_software(candidate)}"


def evaluate(
    baseline_dirs: Sequence[Path],
    candidate_dirs: Sequence[Path],
    *,
    min_trials: int = DEFAULT_MIN_TRIALS,
    noise: float = DEFAULT_NOISE,
    fail: float = DEFAULT_FAIL,
    allow_legacy: bool = False,
) -> dict[str, object]:
    errors: list[str] = []
    excluded: list[dict[str, str]] = []
    baseline = _load_side(baseline_dirs, "baseline", errors, excluded)
    candidate = _load_side(candidate_dirs, "candidate", errors, excluded)
    report: dict[str, object] = {
        "schema": SCHEMA,
        "thresholds": {"min_trials": min_trials, "noise_percent": noise, "fail_percent": fail},
        "independent_trials": {"baseline": len(baseline), "candidate": len(candidate)},
        "trials": {
            "baseline": [_trial_summary(run) for run in baseline],
            "candidate": [_trial_summary(run) for run in candidate],
        },
        "excluded_runs": excluded,
        "operations": [],
    }

    def _finish(decision: str, reasons: list[str]) -> dict[str, object]:
        report["decision"] = decision
        report["reasons"] = reasons
        report["exit_code"] = EXIT_CODES[decision]
        report["eligible_for_issue"] = decision == "reproduced_regression"
        report["dedup_key"] = dedup_key(report)
        return report

    if errors:
        return _finish("error", errors)
    if not baseline or not candidate:
        return _finish("insufficient_evidence", ["no independent trial on one side"])

    problems: list[str] = []
    for base_run in baseline:
        for cand_run in candidate:
            pair, _ = _comparability_problems(base_run, cand_run, allow_legacy)
            pair += _operation_problems(base_run["operations"], cand_run["operations"])
            for problem in pair:
                text = f"{base_run['run_id']} vs {cand_run['run_id']}: {problem}"
                if text not in problems:
                    problems.append(text)
    for side, runs in (("baseline", baseline), ("candidate", candidate)):
        problems += _pinning_problems(runs, side)
    if problems:
        return _finish("not_comparable", problems)

    enough = len(baseline) >= min_trials and len(candidate) >= min_trials
    names = sorted(baseline[0]["operations"])
    operations = []
    for name in names:
        values_b = [float(run["operations"][name]["value"]) for run in baseline]
        values_c = [float(run["operations"][name]["value"]) for run in candidate]
        unit = baseline[0]["operations"][name].get("unit")
        operations.append(_classify(name, unit, values_b, values_c, noise, fail, enough))
    report["operations"] = operations

    if not enough:
        return _finish(
            "insufficient_evidence",
            [
                f"{len(baseline)} baseline and {len(candidate)} candidate independent trials; "
                f"a report needs at least {min_trials} on each side. Per-operation results "
                "are advisory only."
            ],
        )
    classes = {op["classification"] for op in operations}
    if "reproduced_regression" in classes:
        return _finish("reproduced_regression", ["a decline reproduced in every trial"])
    if classes & {"investigate", "improved"}:
        return _finish(
            "investigate", ["a change beyond the noise band is not a reproduced decline"]
        )
    return _finish("equivalent", [f"every operation is within +/-{noise:g}%"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--baseline-dir", action="append", required=True, type=Path)
    parser.add_argument("--candidate-dir", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-trials", type=int, default=DEFAULT_MIN_TRIALS)
    parser.add_argument("--noise", type=float, default=DEFAULT_NOISE)
    parser.add_argument("--fail", type=float, default=DEFAULT_FAIL)
    parser.add_argument("--allow-legacy-group-match", action="store_true")
    args = parser.parse_args(argv)
    if args.min_trials < 1 or not 0 <= args.noise <= args.fail:
        sys.stderr.write("ERROR: need --min-trials >= 1 and 0 <= --noise <= --fail\n")
        return 2

    report = evaluate(
        args.baseline_dir,
        args.candidate_dir,
        min_trials=args.min_trials,
        noise=args.noise,
        fail=args.fail,
        allow_legacy=args.allow_legacy_group_match,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    trials = report["independent_trials"]
    sys.stderr.write(
        f"decision: {report['decision']} ({trials['baseline']} baseline / "
        f"{trials['candidate']} candidate independent trials)\n"
    )
    for reason in report["reasons"]:
        sys.stderr.write(f"- {reason}\n")
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
