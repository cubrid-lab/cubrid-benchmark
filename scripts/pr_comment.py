# pyright: basic
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

STATUS_ICON = {
    "improved": "✅",
    "neutral": "✅",
    "alert": "⚠️",
    "fail": "❌",
}

STATUS_LABEL = {
    "improved": "Improved",
    "neutral": "Within threshold",
    "alert": "Regression alert",
    "fail": "Regression fail",
}


def _load_report(path_arg: str | None) -> dict[str, object]:
    if path_arg:
        return json.loads(Path(path_arg).read_text(encoding="utf-8"))
    return json.load(sys.stdin)


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _as_str(value: object, default: str) -> str:
    if isinstance(value, str):
        return value
    return default


def _render_comment(report: Mapping[str, object]) -> str:
    provenance = report.get("provenance", {})
    summary = report.get("summary", {})
    operations = report.get("operations", [])

    if (
        not isinstance(provenance, Mapping)
        or not isinstance(summary, Mapping)
        or not isinstance(operations, list)
    ):
        raise ValueError("Invalid compare report structure")

    if summary.get("overall_status") == "not_comparable":
        lines = [
            "## Benchmark Comparison: not comparable",
            "",
            "**Not comparable / needs fresh baseline.** No regression or improvement verdict.",
            (
                f"Candidate `{provenance.get('candidate_run_id', 'unknown')}` "
                f"(`{provenance.get('candidate_comparable_group', 'unknown-group')}`), baseline "
                f"`{provenance.get('baseline_run_id') or 'none selected'}`."
            ),
            "",
            "Reasons:",
            "",
        ]
        reasons = summary.get("reasons", [])
        if isinstance(reasons, list):
            lines.extend(f"- {reason}" for reason in reasons)
        return "\n".join(lines) + "\n"

    improved = _as_int(summary.get("improved", 0))
    total = _as_int(summary.get("total_operations", 0))
    regressions = _as_int(summary.get("alert_regressions", 0)) + _as_int(
        summary.get("fail_regressions", 0)
    )
    overall_status = _as_str(summary.get("overall_status", "pass"), "pass")

    lines = [
        "## Benchmark Regression Report",
        "",
        (
            f"**Summary:** {improved}/{total} operations improved, "
            f"{regressions} regressions detected."
        ),
        (
            f"Compared `{provenance.get('baseline_run_id', 'unknown')}` → "
            f"`{provenance.get('candidate_run_id', 'unknown')}` "
            f"in `{provenance.get('comparable_group', 'unknown-group')}`."
        ),
        f"Overall status: **{overall_status}**",
        "",
        "| Status | Operation | Baseline | Candidate | Δ | Δ% |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]

    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        status = _as_str(operation.get("status", "neutral"), "neutral")
        lines.append(
            "| {icon} {label} | {name} | {baseline:.4f} {unit} | {candidate:.4f} {unit} | "
            "{delta:+.4f} | {change:+.2f}% |".format(
                icon=STATUS_ICON.get(status, "ℹ️"),
                label=STATUS_LABEL.get(status, status.title()),
                name=_as_str(operation.get("name", "unknown"), "unknown"),
                baseline=_as_float(operation.get("baseline", 0.0)),
                candidate=_as_float(operation.get("candidate", 0.0)),
                unit=_as_str(operation.get("unit", "ops/sec"), "ops/sec"),
                delta=_as_float(operation.get("absolute_delta", 0.0)),
                change=_as_float(operation.get("percent_change", 0.0)),
            )
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a GitHub PR comment from compare_runs output."
    )
    parser.add_argument(
        "report", nargs="?", help="Path to the compare_runs JSON report (defaults to stdin)"
    )
    args = parser.parse_args()

    try:
        report = _load_report(args.report)
        sys.stdout.write(_render_comment(report))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
