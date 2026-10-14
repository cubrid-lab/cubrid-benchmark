"""Policy checks for .github/workflows/bench.yml (bounded runs, pins, permissions, artifacts)."""

from __future__ import annotations

import fnmatch
import importlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
check_required_results = cast(Any, importlib.import_module("check_required_results"))

WORKFLOW_PATH = ROOT / ".github" / "workflows" / "bench.yml"
WORKFLOW: dict[Any, Any] = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
TRIGGERS: dict[str, Any] = WORKFLOW.get("on", WORKFLOW.get(True))  # PyYAML reads "on" as True
JOBS: dict[str, dict[str, Any]] = WORKFLOW["jobs"]
MAX_TIMEOUT_MINUTES = 120
PINNED_JOBS = {"plan", "pinned", "pinned-manifest"}


def _steps(job: str) -> list[dict[str, Any]]:
    return JOBS[job]["steps"]


def _step(job: str, name: str) -> dict[str, Any]:
    matches = [step for step in _steps(job) if step.get("name") == name]
    assert len(matches) == 1, f"{job} has no single step named {name!r}"
    return matches[0]


def _index(job: str, name: str) -> int:
    return [step.get("name") for step in _steps(job)].index(name)


def _evaluate(
    expression: str, event: str, inputs: dict[str, object], needs: dict[str, str]
) -> bool:
    """Evaluate the small subset of GitHub expressions used by job conditions."""
    python = expression.replace("||", " or ").replace("&&", " and ").replace("\n", " ")
    if re.search(r"[^\w\s.'=()!-]", python.replace("==", "")):
        raise AssertionError(f"unsupported expression: {expression}")
    context = {
        "github": SimpleNamespace(event_name=event),
        "inputs": SimpleNamespace(lane=inputs.get("lane"), inject_failure=False),
        "needs": SimpleNamespace(**{n: SimpleNamespace(result=r) for n, r in needs.items()}),
        "always": lambda: True,
    }
    return bool(eval(python, {"__builtins__": {}}, context))  # noqa: S307 - fixed test input


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _jobs_for(event: str, **inputs: object) -> set[str]:
    """Jobs GitHub would start, assuming every started job succeeds."""
    results: dict[str, str] = {}
    pending = dict(JOBS)
    while pending:
        for name, job in list(pending.items()):
            needs = _needs(job)
            if any(need in pending for need in needs):
                continue
            needed = {need: results[need] for need in needs}
            condition = job.get("if")
            if condition is None:
                # Implicit success(): every needed job must have succeeded.
                runs = all(result == "success" for result in needed.values())
            else:
                runs = _evaluate(str(condition), event, inputs, needed)
                if "always()" not in str(condition):
                    runs = runs and all(result == "success" for result in needed.values())
            results[name] = "success" if runs else "skipped"
            del pending[name]
    return {name for name, result in results.items() if result == "success"}


@pytest.mark.parametrize(
    ("event", "inputs", "expected"),
    [
        ("pull_request", {}, {"checks", "cubrid-smoke"}),
        ("push", {}, {"checks", "cubrid-smoke"}),
        ("schedule", {}, PINNED_JOBS),
        ("workflow_dispatch", {"lane": "representative"}, PINNED_JOBS),
        ("workflow_dispatch", {"lane": "release"}, PINNED_JOBS),
        ("workflow_dispatch", {"lane": "archival"}, PINNED_JOBS),
        ("workflow_dispatch", {"lane": "full"}, {"full-suite"}),
    ],
)
def test_event_selection(event: str, inputs: dict[str, object], expected: set[str]) -> None:
    assert _jobs_for(event, **inputs) == expected


def test_only_one_bounded_weekly_schedule() -> None:
    schedules = TRIGGERS["schedule"]
    assert len(schedules) == 1
    minute, hour, day_of_month, month, day_of_week = schedules[0]["cron"].split()
    assert day_of_month == "*" and month == "*" and day_of_week.isdigit()
    # The scheduled lane is the bounded CUBRID-only one, never the cross-language suite.
    assert _jobs_for("schedule") == PINNED_JOBS
    assert "mysql" not in JOBS["pinned"].get("services", {})
    # A schedule has no inputs; the planner then selects the one-cell representative lane.
    assert '--lane "$LANE"' in _step("plan", "Plan the lane's cells")["run"]


def test_manual_dispatch_is_kept_with_lane_choice() -> None:
    inputs = TRIGGERS["workflow_dispatch"]["inputs"]
    assert inputs["lane"]["default"] == "representative"
    assert set(inputs["lane"]["options"]) == {"representative", "release", "archival", "full"}
    assert inputs["pycubrid_source_sha"]["default"] == ""
    assert inputs["inject_failure"]["default"] is False


def test_push_and_pull_request_skip_docs_only_changes() -> None:
    assert TRIGGERS["push"]["branches"] == ["main"]
    for event in ("push", "pull_request"):
        patterns = TRIGGERS[event]["paths"]
        for docs_path in ("README.md", "docs/METHODOLOGY.md", "CHANGELOG.md"):
            assert not any(fnmatch.fnmatch(docs_path, p.replace("**", "*")) for p in patterns)
        assert any(
            fnmatch.fnmatch("python/bench_pycubrid.py", p.replace("**", "*")) for p in patterns
        )


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    for job_name, job in JOBS.items():
        for step in job["steps"]:
            uses = step.get("uses")
            if uses:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses), (job_name, uses)
    for line in WORKFLOW_PATH.read_text(encoding="utf-8").splitlines():
        if "uses:" in line:
            assert "#" in line, f"pinned action has no version comment: {line.strip()}"


def test_every_job_has_a_bounded_timeout() -> None:
    for job_name, job in JOBS.items():
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int) and 0 < timeout <= MAX_TIMEOUT_MINUTES, job_name


def test_permissions_are_read_only_except_publishing() -> None:
    assert WORKFLOW["permissions"] == {"contents": "read"}
    for job_name, job in JOBS.items():
        permissions = job.get("permissions")
        if job_name == "full-suite":
            assert permissions == {"actions": "read", "contents": "write"}
        else:
            assert permissions is None, job_name
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "issues: write" not in text
    assert "gh issue create" not in text


def test_uploads_keep_existing_files_without_masking_the_first_failure() -> None:
    for job_name, job in JOBS.items():
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                if job_name.startswith("matrix"):
                    continue
                assert step.get("if") == "always()", (job_name, step["name"])
                assert step["with"].get("if-no-files-found") == "warn", (job_name, step["name"])


def test_pinned_required_steps_fail_the_job() -> None:
    required = [
        "Install pinned Python packages",
        "Verify installed versions",
        "CUBRID-only Tier 0 smoke",
        "CUBRID-only Tier 1 pycubrid measurement",
        "Capture candidate provenance",
        "Compare against a compatible baseline (report only)",
        "Package cell",
        "Verify required results",
    ]
    for name in required:
        step = _step("pinned", name)
        assert "continue-on-error" not in step, name
        # No condition: GitHub's implicit success() skips them after a failure or cancellation.
        assert "if" not in step, name
    # cell.json (the manifest entry) is written only after every required result passed.
    assert (
        _index("pinned", "Verify required results")
        < _index("pinned", "Package cell")
        < _index("pinned", "Upload cell artifacts")
    )
    assert "--versions-file" in _step("pinned", "Capture candidate provenance")["run"]


def test_injected_failure_precedes_measurement_and_needs_dispatch() -> None:
    step = _step("pinned", "Injected failure (manual verification only)")
    assert step["if"] == "github.event_name == 'workflow_dispatch' && inputs.inject_failure"
    assert "exit 1" in step["run"]
    assert _index("pinned", "CUBRID-only Tier 0 smoke") < _index(
        "pinned", "Injected failure (manual verification only)"
    )
    assert _index("pinned", "Injected failure (manual verification only)") < _index(
        "pinned", "CUBRID-only Tier 1 pycubrid measurement"
    )


@pytest.mark.parametrize(
    ("setup", "dependents"),
    [
        ("setup_node", ["ts_install"]),
        ("setup_go", ["go_tier0", "go_tier1", "go_tier1_extended"]),
        ("setup_rust", ["rust_tier0", "rust_tier1"]),
    ],
)
def test_optional_toolchain_setup_cannot_stop_python(setup: str, dependents: list[str]) -> None:
    steps = {step.get("id"): step for step in _steps("full-suite")}
    assert steps[setup]["continue-on-error"] is True
    for dependent in dependents:
        assert steps[dependent]["if"] == f"steps.{setup}.outcome == 'success'", dependent
    for ts_step in ("ts_tier0", "ts_tier1", "ts_tier1_extended"):
        assert steps[ts_step]["if"] == "steps.ts_install.outcome == 'success'"


def test_artifact_names_are_unique_per_attempt() -> None:
    for job_name in ("pinned", "pinned-manifest", "full-suite"):
        for step in _steps(job_name):
            if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                assert "${{ github.run_attempt }}" in step["with"]["name"], step["name"]


def test_cubrid_only_lanes_do_not_use_mysql() -> None:
    for job_name in ("cubrid-smoke", "pinned"):
        job = JOBS[job_name]
        assert set(job["services"]) == {"cubrid"}
        assert job["services"]["cubrid"]["image"] in (
            "cubrid/cubrid:11.4",
            "${{ matrix.cell.image }}",
        )
        commands = " ".join(str(step.get("run", "")) for step in job["steps"])
        # The repository conftest is CUBRID-only, so no plugin or --noconftest is needed.
        assert "--noconftest" not in commands
        assert "wait_for_db.sh" not in commands
        assert "pymysql" not in commands.lower()


def test_full_suite_required_and_optional_tracks() -> None:
    for name in ("Run Python Tier 0", "Run Python Tier 1", "Install Python dependencies"):
        step = _step("full-suite", name)
        assert "continue-on-error" not in step and "if" not in step, name
    required = _step("full-suite", "Verify required results")
    # Only after a successful Tier 1, so a Tier 1 failure is not followed by a second error.
    assert required["if"] == "always() && steps.python_tier1.outcome == 'success'"
    outcomes = _step("full-suite", "Report optional track outcomes")
    assert outcomes["if"] == "always()"
    assert "--lane outcomes" in outcomes["run"]
    reported = str(outcomes["env"]["OUTCOMES"])
    for step in _steps("full-suite"):
        if step.get("continue-on-error"):
            assert f"steps.{step['id']}.outcome" in reported, step["name"]


def test_full_suite_optional_benchforge_conversions_cannot_fail_the_job() -> None:
    required = _step("full-suite", "Normalize and emit required benchforge v2 results")
    assert "continue-on-error" not in required and "if" not in required
    assert "results/python_tier1.json" in required["run"]
    # The required step converts only the required Tier 1 result.
    assert "python_tier1_extended" not in required["run"]
    assert "tier2_python" not in required["run"]
    steps = {step.get("id"): step for step in _steps("full-suite")}
    for emit_id, track_id, source in (
        ("emit_python_tier1_extended", "python_tier1_extended", "python_tier1_extended.json"),
        ("emit_python_tier2", "python_tier2", "tier2_python.json"),
    ):
        step = steps[emit_id]
        assert step["continue-on-error"] is True, emit_id
        assert step["if"] == f"steps.{track_id}.outcome == 'success'", emit_id
        assert f"results/{source}" in step["run"], emit_id
    reported = str(_step("full-suite", "Report optional track outcomes")["env"]["OUTCOMES"])
    assert "emit-python-tier1-extended=${{ steps.emit_python_tier1_extended.outcome }}" in reported
    assert "emit-python-tier2=${{ steps.emit_python_tier2.outcome }}" in reported


class TestRequiredResults:
    def _complete(self, root: Path) -> None:
        for relative in check_required_results.REQUIRED["pinned"]:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"benchmarks": [{"name": "x"}]} if relative.endswith(".json") else "x"
            path.write_text(json.dumps(payload), encoding="utf-8")

    def test_complete_pinned_results_pass(self, tmp_path: Path) -> None:
        self._complete(tmp_path)

        assert check_required_results.main(["--lane", "pinned", "--dir", str(tmp_path)]) == 0

    def test_missing_measurement_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._complete(tmp_path)
        (tmp_path / "python_tier1_pycubrid.json").unlink()

        assert check_required_results.main(["--lane", "pinned", "--dir", str(tmp_path)]) == 1
        assert "::error::missing or empty required result" in capsys.readouterr().out

    def test_measurement_without_benchmarks_fails(self, tmp_path: Path) -> None:
        self._complete(tmp_path)
        (tmp_path / "python_tier1_pycubrid.json").write_text('{"benchmarks": []}')

        assert check_required_results.main(["--lane", "pinned", "--dir", str(tmp_path)]) == 1

    def test_optional_failures_warn_without_failing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "python_tier1.json").write_text('{"benchmarks": [{"name": "x"}]}')
        summary = tmp_path / "summary.md"

        rc = check_required_results.main(
            [
                "--lane",
                "outcomes",
                "--dir",
                str(tmp_path),
                "--outcomes",
                "rust-tier0=failure go=success",
            ]
        )

        assert rc == 0
        assert "::warning::optional track rust-tier0 finished with outcome failure" in (
            capsys.readouterr().out
        )
        assert not summary.exists()
