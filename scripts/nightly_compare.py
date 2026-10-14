# pyright: basic
"""Compare a captured nightly candidate with a compatible baseline and decide on an alert.

The candidate directory must come from ``scripts/run_provenance.py capture``. The baseline is
chosen only among baseline-arm runs (``role: baseline``, or a repeat of the baseline condition
recorded as ``role: candidate`` with ``arm: baseline``) whose comparison identity equals the
candidate's; every such run is one baseline trial for the evidence decision.
When none exists, or the comparator finds the pair not comparable, the result is
``not comparable / needs fresh baseline`` and no regression verdict (and no issue) is produced.

Writes ``<output-dir>/regression_report.json`` and ``<output-dir>/regression_comment.md``, and
appends ``status=``, ``exit_code=``, ``evidence=`` and ``open_issue=`` to ``--github-output``.
``open_issue`` is true only for a ``reproduced_regression`` evidence decision, which needs at least
three independent trials on each side; a single candidate run never qualifies.
Exit status: 0 when a decision was made (including regressions and non-comparability),
2 when the inputs could not be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_runs import (  # noqa: E402
    EXIT_ERROR,
    EXIT_NOT_COMPARABLE,
    _compare_runs,
    _load_run,
)
from pr_comment import _render_comment  # noqa: E402
from regression_evidence import evaluate  # noqa: E402
from run_provenance import select_baselines  # noqa: E402


def decide(
    candidate_dir: Path,
    search_roots: list[Path],
    alert_threshold: float,
    fail_threshold: float,
) -> dict[str, object]:
    # Load (and so validate) the candidate first: a missing or corrupt result is an error
    # (exit 2), never "not comparable".
    candidate = _load_run(candidate_dir)
    baselines, reason = select_baselines(candidate_dir, search_roots)
    if not baselines:
        return {
            "provenance": {
                "candidate_run_id": candidate.get("run_id"),
                "candidate_comparable_group": candidate.get("comparable_group"),
                "candidate_identity": candidate.get("comparison_identity"),
                "candidate_software_under_test": candidate.get("software_under_test"),
                "baseline_search": [str(root) for root in search_roots],
                "comparison_basis": "measured comparison_identity",
            },
            "summary": {
                "total_operations": 0,
                "improved": 0,
                "neutral": 0,
                "alert_regressions": 0,
                "fail_regressions": 0,
                "overall_status": "not_comparable",
                "exit_code": EXIT_NOT_COMPARABLE,
                "verdict": "not comparable / needs fresh baseline; no regression verdict",
                "reasons": [reason],
            },
            "operations": [],
        }
    baseline = _load_run(baselines[-1])
    report = _compare_runs(baseline, candidate, alert_threshold, fail_threshold)
    # The issue decision is the shared evidence policy: every compatible baseline run and this
    # one candidate run each count as one independent trial.
    report["evidence"] = evaluate(
        baselines, [candidate_dir], noise=alert_threshold, fail=fail_threshold
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--search", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--alert-threshold", type=float, default=5.0)
    parser.add_argument("--fail-threshold", type=float, default=10.0)
    parser.add_argument("--github-output")
    args = parser.parse_args(argv)

    try:
        report = decide(
            Path(args.candidate_dir),
            [Path(root) for root in args.search],
            args.alert_threshold,
            args.fail_threshold,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return EXIT_ERROR

    summary = report["summary"]
    if not isinstance(summary, dict):
        sys.stderr.write("ERROR: comparison report has no summary\n")
        return EXIT_ERROR
    exit_code = summary["exit_code"]
    status = summary["overall_status"]
    # Only the shared evidence policy may open an issue (scripts/regression_evidence.py): a
    # threshold alert from one candidate run is advisory, never a confirmed regression.
    evidence = report.get("evidence")
    decision = evidence.get("decision") if isinstance(evidence, dict) else "not_comparable"
    open_issue = isinstance(evidence, dict) and evidence.get("eligible_for_issue") is True

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "regression_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "regression_comment.md").write_text(_render_comment(report), encoding="utf-8")
    sys.stderr.write(f"comparison status: {status} (exit code {exit_code})\n")
    for reason in summary.get("reasons", []):
        sys.stderr.write(f"- {reason}\n")

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"status={status}\n")
            handle.write(f"exit_code={exit_code}\n")
            handle.write(f"evidence={decision}\n")
            handle.write(f"open_issue={'true' if open_issue else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
