# pyright: basic
"""File (or plan) one deduplicated regression issue in this benchmark repository.

It acts only on a ``regression_evidence.py`` report whose ``eligible_for_issue`` is true. It
never files into another repository: drafts for pycubrid or sqlalchemy-cubrid stay drafts until
that repository's owner approves them. No workflow calls it today; automated performance
checks stay advisory.

Without ``--execute`` it prints the plan and changes nothing. With ``--execute`` it uses the
``gh`` CLI to look for an open issue that carries the report's dedup key, comments there, or
creates one titled with the candidate SHA and date. A failed ``gh`` call (for example a missing
``issues: write`` permission) exits 2; nothing is reported as filed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gap_to_issue import _render_issue, _select_operations  # noqa: E402

BENCHMARK_REPO = "cubrid-lab/cubrid-benchmark"
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)


class FilingError(RuntimeError):
    """The issue could not be filed; nothing must be reported as filed."""


def _gh(runner: Runner, *args: str) -> str:
    result = runner(["gh", *args])
    if result.returncode != 0:
        raise FilingError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def file_issue(
    report: dict[str, object],
    repo: str,
    *,
    execute: bool,
    runner: Runner | None = None,
    min_effect_size: float = 10.0,
    min_replications: int = 3,
) -> dict[str, object]:
    # Resolved at call time, so a replaced module-level _run (tests) is always honoured.
    runner = runner or _run
    if repo != BENCHMARK_REPO:
        return {
            "action": "refused",
            "reason": f"only {BENCHMARK_REPO} may be filed automatically; a draft for {repo} "
            "needs explicit approval from that repository's owner",
        }
    running_in = os.environ.get("GITHUB_REPOSITORY")
    if execute and running_in and running_in != BENCHMARK_REPO:
        return {
            "action": "refused",
            "reason": f"refusing to file from {running_in}; only {BENCHMARK_REPO} may file",
        }
    operations = _select_operations(report, min_effect_size, min_replications)
    if not operations:
        return {"action": "none", "reason": f"evidence decision {report.get('decision')!r}"}

    title, body = _render_issue(report, operations[0], None, repo)
    for operation in operations[1:]:
        body += "\n---\n" + _render_issue(report, operation, None, repo)[1]
    key = str(report.get("dedup_key"))
    plan: dict[str, object] = {"title": title, "dedup_key": key, "operations": len(operations)}
    if not execute:
        return {"action": "plan", **plan}

    found = _gh(
        runner,
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--search",
        f'"{key}" in:body',
        "--json",
        "number",
        "--jq",
        ".[].number",
    ).split()
    if found:
        number = found[0]
        _gh(runner, "issue", "comment", number, "--repo", repo, "--body", body)
        return {"action": "commented", "number": number, **plan}
    url = _gh(runner, "issue", "create", "--repo", repo, "--title", title, "--body", body)
    return {"action": "created", "url": url.strip(), **plan}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--repo", default=BENCHMARK_REPO)
    parser.add_argument("--execute", action="store_true", help="Call gh (default: plan only)")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.evidence.read_text(encoding="utf-8"))
        outcome = file_issue(report, args.repo, execute=args.execute)
    except (OSError, ValueError, FilingError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2
    print(json.dumps(outcome, indent=2))
    return 2 if outcome["action"] == "refused" else 0


if __name__ == "__main__":
    raise SystemExit(main())
