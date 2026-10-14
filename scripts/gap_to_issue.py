#!/usr/bin/env python3
# pyright: basic
"""Draft regression issue markdown from a ``regression_evidence.py`` report.

The decision whether a report is issue-worthy belongs to ``scripts/regression_evidence.py``,
the same policy the workflow uses. This script only renders drafts for operations classified
``reproduced_regression`` in an ``eligible_for_issue`` report. It never posts anything, and a
draft for another repository (for example pycubrid or sqlalchemy-cubrid) needs explicit approval
from that repository's owner before anyone files it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regression_evidence import SCHEMA  # noqa: E402

EXIT_NO_ISSUES = 0
EXIT_ISSUES_GENERATED = 1
EXIT_ERROR = 2

DEFAULT_TARGET_REPO = "cubrid-labs/pycubrid"


def _load_report(path_arg: str | None) -> dict[str, object]:
    if path_arg and path_arg != "-":
        return json.loads(Path(path_arg).read_text(encoding="utf-8"))
    return json.load(sys.stdin)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "issue"


def _trials(report: Mapping[str, object], side: str) -> list[Mapping[str, object]]:
    trials = report.get("trials")
    values = trials.get(side, []) if isinstance(trials, Mapping) else []
    return [trial for trial in values if isinstance(trial, Mapping)]


def _select_operations(
    report: Mapping[str, object], min_effect_size: float, min_replications: int
) -> list[Mapping[str, object]]:
    """Operations to draft; empty unless the shared evidence policy made the report eligible."""
    if report.get("schema") != SCHEMA:
        sys.stderr.write(
            "WARNING: not a regression-evidence report; run scripts/regression_evidence.py "
            "with independent trials first (a compare_runs report is not issue evidence)\n"
        )
        return []
    if report.get("eligible_for_issue") is not True:
        sys.stderr.write(f"No issue drafted: evidence decision is {report.get('decision')!r}\n")
        return []
    trials = report.get("independent_trials")
    counts = [int(v) for v in trials.values()] if isinstance(trials, Mapping) else [0]
    if min(counts) < min_replications:
        sys.stderr.write(
            f"No issue drafted: {min(counts)} independent trials < {min_replications}\n"
        )
        return []
    operations = report.get("operations")
    if not isinstance(operations, list):
        raise ValueError("Invalid regression-evidence report: operations is not a list")
    return [
        operation
        for operation in operations
        if isinstance(operation, Mapping)
        and operation.get("classification") == "reproduced_regression"
        and abs(float(operation.get("percent_change", 0.0))) >= min_effect_size
    ]


def _trial_rows(trials: list[Mapping[str, object]]) -> list[str]:
    rows = [
        "| Run | Date | Benchmark source SHA | Command | Artifacts | Raw results |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for trial in trials:
        rows.append(
            f"| `{trial.get('run_id')}` | {trial.get('date')} | "
            f"`{trial.get('benchmark_source_sha')}` | `{trial.get('command', 'not recorded')}` | "
            f"{trial.get('artifact_url', 'not recorded')} | `{trial.get('results_path')}` |"
        )
    return rows


def _render_issue(
    report: Mapping[str, object],
    operation: Mapping[str, object],
    report_path_arg: str | None,
    target_repo: str,
) -> tuple[str, str]:
    name = str(operation.get("name", "unknown"))
    change = float(operation.get("percent_change", 0.0))
    baseline = _trials(report, "baseline")
    candidate = _trials(report, "candidate")
    group = baseline[0].get("comparable_group") if baseline else "unknown"
    candidate_sha = (
        str(candidate[0].get("benchmark_source_sha", "unknown"))[:12] if candidate else ""
    )
    date = str(candidate[-1].get("date", "")) if candidate else ""
    title = f"perf: {name} reproduced regression ({change:+.2f}%) at {candidate_sha} {date[:10]}"
    unit = operation.get("unit", "ops/sec")
    thresholds = report.get("thresholds", {})
    lines = [
        f"<!-- {report.get('dedup_key')} -->",
        f"# {title}",
        "",
        f"Draft for `{target_repo}`. **Do not file it there without explicit approval from that "
        "repository's owner.** This text was generated, not reviewed.",
        "",
        "## Evidence decision",
        f"- Decision: `{report.get('decision')}` (`scripts/regression_evidence.py`)",
        f"- Independent trials: {report.get('independent_trials')}",
        f"- Thresholds: {thresholds}",
        f"- Comparable group: `{group}`",
        f"- Evidence report: `{report_path_arg or 'stdin'}`",
        "",
        "## Operation",
        f"- `{name}`: median {operation.get('baseline_median')} → "
        f"{operation.get('candidate_median')} {unit} ({change:+.2f}%)",
        f"- Baseline trials: {operation.get('baseline_values')} "
        f"(range {operation.get('baseline_range')})",
        f"- Candidate trials: {operation.get('candidate_values')} "
        f"(range {operation.get('candidate_range')})",
        "- Every candidate trial is below every baseline trial (non-overlapping ranges). The "
        "uncertainty is the spread of those trials; nothing is extrapolated beyond them.",
        "",
        "## Baseline commands (one row per independent run)",
        *_trial_rows(baseline),
        "",
        "## Candidate commands (one row per independent run)",
        *_trial_rows(candidate),
        "",
        "Excluded runs: "
        + (json.dumps(report.get("excluded_runs")) if report.get("excluded_runs") else "none"),
        "",
        "Not a comparison with another database engine and not a requirement to reach any "
        "absolute performance target.",
        "",
    ]
    return title, "\n".join(lines)


def _write_issues(output_dir: str | None, issues: list[tuple[str, str]]) -> None:
    if not output_dir:
        for index, (_, body) in enumerate(issues):
            if index:
                sys.stdout.write("\n---\n\n")
            sys.stdout.write(body)
        return
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    for index, (title, body) in enumerate(issues, start=1):
        (output_path / f"{index:02d}-{_slugify(title)}.md").write_text(body, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", default="-", help="regression_evidence.py JSON (or -)")
    parser.add_argument("--min-effect-size", type=float, default=10.0)
    parser.add_argument("--min-replications", type=int, default=3)
    parser.add_argument("--target-repo", default=DEFAULT_TARGET_REPO)
    parser.add_argument("--output-dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.min_effect_size < 0:
        sys.stderr.write("ERROR: --min-effect-size must be non-negative\n")
        return EXIT_ERROR
    if args.min_replications < 1:
        sys.stderr.write("ERROR: --min-replications must be at least 1\n")
        return EXIT_ERROR
    try:
        report = _load_report(args.report)
        selected = _select_operations(report, args.min_effect_size, args.min_replications)
        issues = [
            _render_issue(report, operation, args.report, args.target_repo)
            for operation in selected
        ]
        _write_issues(args.output_dir, issues)
        return EXIT_ISSUES_GENERATED if issues else EXIT_NO_ISSUES
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
