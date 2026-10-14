"""Fail when a lane that reported success is missing a required result file.

Artifact uploads in ``bench.yml`` use ``if-no-files-found: warn`` so that an early failure is
not followed by misleading "no files found" errors. This check is the separate gate that keeps
a *successful* required measurement from passing without its evidence.

    python scripts/check_required_results.py --lane pinned --dir results/pinned
    python scripts/check_required_results.py --lane outcomes --dir results --outcomes "a=success"

``--outcomes`` (full lane) lists optional track outcomes as ``name=outcome`` pairs. Optional
failures are reported as warnings and in the job summary; they do not fail this step.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REQUIRED: dict[str, tuple[str, ...]] = {
    "pinned": (
        "source.txt",
        "pip-freeze.txt",
        "versions.json",
        "tier0.xml",
        "python_tier1_pycubrid.json",
        "candidate/run.yaml",
        "candidate/raw/results.json",
        "comparison/regression_report.json",
        "cell.json",
    ),
    "full": ("python_tier1.json",),
    # Report optional track outcomes only; nothing is required.
    "outcomes": (),
}
PYTEST_BENCHMARK_FILES = {"python_tier1_pycubrid.json", "python_tier1.json"}


def missing_results(lane: str, directory: Path) -> list[str]:
    problems: list[str] = []
    for relative in REQUIRED[lane]:
        path = directory / relative
        if not path.is_file() or path.stat().st_size == 0:
            problems.append(f"missing or empty required result: {path}")
            continue
        if relative in PYTEST_BENCHMARK_FILES:
            try:
                benchmarks = json.loads(path.read_text(encoding="utf-8")).get("benchmarks")
            except (json.JSONDecodeError, AttributeError):
                benchmarks = None
            if not benchmarks:
                problems.append(f"required result has no benchmarks: {path}")
    return problems


def parse_outcomes(text: str) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for item in text.split():
        name, _, outcome = item.partition("=")
        outcomes[name] = outcome or "unknown"
    return outcomes


def _summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lane", choices=sorted(REQUIRED), required=True)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--outcomes", default="")
    args = parser.parse_args(argv)

    lines: list[str] = []
    problems = missing_results(args.lane, Path(args.dir))
    if REQUIRED[args.lane]:
        lines += [f"### Required results ({args.lane} lane)", ""]
    for relative in REQUIRED[args.lane]:
        lines.append(
            f"- `{relative}`: {'missing' if any(relative in p for p in problems) else 'ok'}"
        )

    outcomes = parse_outcomes(args.outcomes)
    if outcomes:
        lines += ["", "### Optional tracks", "", "| Track | Outcome |", "| --- | --- |"]
        for name, outcome in outcomes.items():
            lines.append(f"| {name} | {outcome or 'not run'} |")
            if outcome not in ("success", "skipped", ""):
                print(f"::warning::optional track {name} finished with outcome {outcome}")
    _summary(lines)

    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
