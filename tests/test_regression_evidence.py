"""Shared regression evidence policy, issue drafting and filing (no network, no database)."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

compare_runs = cast(Any, importlib.import_module("compare_runs"))
run_provenance = cast(Any, importlib.import_module("run_provenance"))
evidence = cast(Any, importlib.import_module("regression_evidence"))
gap_to_issue = cast(Any, importlib.import_module("gap_to_issue"))
filer = cast(Any, importlib.import_module("file_regression_issue"))
nightly_compare = cast(Any, importlib.import_module("nightly_compare"))

BASE_OPS = {"insert": 100.0, "select": 200.0}


def _identity(**overrides: object) -> dict[str, object]:
    identity: dict[str, object] = {
        "host_class": "fixed:bench-01",
        "cpu": "cpu",
        "cores": 4,
        "os": "Ubuntu 24.04",
        "kernel": "6.8",
        "docker": "28.0.4",
        "cubrid_image": "cubrid/cubrid:11.4",
        "cubrid_image_digest": "sha256:abc",
        "cubrid_build": "11.4.6.1963",
        "python": "CPython 3.12.13",
        "scenario": "python-tier1",
        "workload_revision": "tier1-explicit-keys-v2",
        "schema": "s",
        "data_count": 10000,
        "transaction_policy": "t",
        "measurement_protocol": "m",
    }
    identity.update(overrides)
    return identity


def _run(
    root: Path,
    name: str,
    role: str,
    ops: dict[str, float],
    *,
    identity: dict[str, object] | None = None,
    pycubrid: str = "1.12.0",
    sha: str = "a" * 40,
    dirty: str | None = "false",
    extra_software: dict[str, object] | None = None,
) -> Path:
    run_dir = root / name
    (run_dir / "raw").mkdir(parents=True)
    results = [{"name": k, "unit": "ops/sec", "value": v} for k, v in ops.items()]
    (run_dir / "raw" / "results.json").write_text(json.dumps(results), encoding="utf-8")
    text = run_provenance.render_run_yaml(
        run_id=name,
        date=f"2026-10-{len(name) % 28 + 1:02d}T00:00:00Z",
        label=name,
        role=role,
        identity=identity or _identity(),
        software={
            "benchmark_source_sha": sha,
            **({"benchmark_source_dirty": dirty} if dirty is not None else {}),
            **(extra_software or {}),
            "packages": {"pycubrid": pycubrid},
        },
        notes="",
        measurement={
            "command": f"pytest bench_pycubrid.py  # {name}",
            "artifact_url": "https://x/1",
        },
    )
    (run_dir / "run.yaml").write_text(text, encoding="utf-8")
    return run_dir


def _sides(
    tmp_path: Path,
    baseline_n: int,
    candidate_n: int,
    candidate_scale: float,
    *,
    jitter: float = 0.01,
    **candidate_kwargs: Any,
) -> tuple[list[Path], list[Path]]:
    baseline = [
        _run(
            tmp_path / "b",
            f"base-{i}",
            "baseline",
            {k: v * (1 + jitter * (i - 1)) for k, v in BASE_OPS.items()},
            pycubrid="1.11.0",
        )
        for i in range(baseline_n)
    ]
    candidate = [
        _run(
            tmp_path / "c",
            f"cand-{i}",
            "candidate",
            {k: v * candidate_scale * (1 + jitter * (i - 1)) for k, v in BASE_OPS.items()},
            **candidate_kwargs,
        )
        for i in range(candidate_n)
    ]
    return baseline, candidate


class TestTrialCounts:
    @pytest.mark.parametrize("trials", [0, 1, 2])
    def test_fewer_than_three_trials_is_insufficient(self, tmp_path: Path, trials: int) -> None:
        baseline, candidate = _sides(tmp_path, 3, trials, 0.80)

        report = evidence.evaluate(baseline, candidate)

        assert report["decision"] == "insufficient_evidence"
        assert report["eligible_for_issue"] is False
        assert report["exit_code"] == 0
        for operation in report["operations"]:
            assert operation["classification"].startswith("advisory:")

    def test_three_independent_trials_with_a_separated_12_percent_decline(
        self, tmp_path: Path
    ) -> None:
        baseline, candidate = _sides(tmp_path, 3, 3, 0.88)

        report = evidence.evaluate(baseline, candidate)

        assert report["decision"] == "reproduced_regression"
        assert report["eligible_for_issue"] is True
        assert report["exit_code"] == 1
        assert report["independent_trials"] == {"baseline": 3, "candidate": 3}
        assert {op["classification"] for op in report["operations"]} == {"reproduced_regression"}

    def test_pedantic_rounds_and_copies_are_not_trials(self, tmp_path: Path) -> None:
        baseline, candidate = _sides(tmp_path, 3, 1, 0.88)
        copy = tmp_path / "copy"
        copy.mkdir()
        (copy / "raw").mkdir()
        (copy / "raw" / "results.json").write_bytes(
            (candidate[0] / "raw" / "results.json").read_bytes()
        )
        (copy / "run.yaml").write_text(
            (candidate[0] / "run.yaml").read_text().replace("cand-0", "cand-copy"),
            encoding="utf-8",
        )

        report = evidence.evaluate(baseline, [candidate[0], candidate[0], copy])

        assert report["independent_trials"]["candidate"] == 1
        reasons = [run["reason"] for run in report["excluded_runs"]]
        assert any("repeats run_id" in reason for reason in reasons)
        assert any("identical raw results" in reason for reason in reasons)
        assert report["decision"] == "insufficient_evidence"


class TestDecisions:
    def test_noisy_8_percent_change_is_investigate_not_an_issue(self, tmp_path: Path) -> None:
        baseline, candidate = _sides(tmp_path, 3, 3, 0.92, jitter=0.05)

        report = evidence.evaluate(baseline, candidate)

        assert report["decision"] == "investigate"
        assert report["eligible_for_issue"] is False

    def test_overlapping_12_percent_decline_is_investigate(self, tmp_path: Path) -> None:
        baseline, candidate = _sides(tmp_path, 3, 3, 0.88, jitter=0.15)

        report = evidence.evaluate(baseline, candidate)

        assert report["decision"] == "investigate"
        assert not any(op["ranges_separated"] for op in report["operations"])

    def test_within_noise_band_is_equivalent(self, tmp_path: Path) -> None:
        baseline, candidate = _sides(tmp_path, 3, 3, 0.98)

        assert evidence.evaluate(baseline, candidate)["decision"] == "equivalent"

    def test_wrong_comparable_group_is_not_comparable(self, tmp_path: Path) -> None:
        baseline, candidate = _sides(
            tmp_path, 3, 3, 0.80, identity=_identity(host_class="fixed:other")
        )

        report = evidence.evaluate(baseline, candidate)

        assert report["decision"] == "not_comparable"
        assert report["exit_code"] == 3
        assert any("host_class differs" in reason for reason in report["reasons"])

    def test_different_workload_is_not_comparable(self, tmp_path: Path) -> None:
        baseline, candidate = _sides(
            tmp_path, 3, 3, 0.80, identity=_identity(workload_revision="tier1-other")
        )

        report = evidence.evaluate(baseline, candidate)

        assert report["decision"] == "not_comparable"

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"sha": "unknown"}, "no pinned benchmark_source_sha"),
            ({"dirty": "true"}, "not from a verified clean source tree"),
            ({"dirty": None}, "not from a verified clean source tree"),
            ({"dirty": "unknown"}, "not from a verified clean source tree"),
        ],
    )
    def test_unpinned_source_is_not_comparable(
        self, tmp_path: Path, kwargs: dict[str, Any], message: str
    ) -> None:
        baseline, candidate = _sides(tmp_path, 3, 3, 0.80, **kwargs)

        report = evidence.evaluate(baseline, candidate)

        assert report["decision"] == "not_comparable"
        assert any(message in reason for reason in report["reasons"])

    def test_mixed_candidate_software_is_not_comparable(self, tmp_path: Path) -> None:
        baseline, candidate = _sides(tmp_path, 3, 2, 0.80)
        candidate.append(_run(tmp_path / "c", "cand-x", "candidate", BASE_OPS, pycubrid="1.13.0"))

        report = evidence.evaluate(baseline, candidate)

        assert report["decision"] == "not_comparable"
        assert any("mix software" in reason for reason in report["reasons"])

    @pytest.mark.parametrize("damage", ["missing", "corrupt", "no-run-yaml"])
    def test_missing_or_corrupt_raw_results_are_errors(self, tmp_path: Path, damage: str) -> None:
        baseline, candidate = _sides(tmp_path, 3, 3, 0.80)
        if damage == "missing":
            (candidate[1] / "raw" / "results.json").unlink()
        elif damage == "corrupt":
            (candidate[1] / "raw" / "results.json").write_text("{not json", encoding="utf-8")
        else:
            (candidate[1] / "run.yaml").unlink()

        report = evidence.evaluate(baseline, candidate)

        assert report["decision"] == "error"
        assert report["exit_code"] == 2
        assert report["eligible_for_issue"] is False

    def test_dedup_key_ignores_per_run_fields(self, tmp_path: Path) -> None:
        first = evidence.evaluate(*_sides(tmp_path / "1", 3, 3, 0.88))
        rerun = evidence.evaluate(
            *_sides(
                tmp_path / "2",
                3,
                3,
                0.88,
                extra_software={"github_run": "https://x/runs/99", "verified_versions_file": "v"},
            )
        )

        assert rerun["decision"] == "reproduced_regression"
        assert first["dedup_key"] == rerun["dedup_key"]

    def test_cli_exit_codes_and_report(self, tmp_path: Path) -> None:
        baseline, candidate = _sides(tmp_path, 3, 3, 0.88)
        out = tmp_path / "evidence.json"
        argv = [arg for b in baseline for arg in ("--baseline-dir", str(b))]
        argv += [arg for c in candidate for arg in ("--candidate-dir", str(c))]

        assert evidence.main(argv + ["--output", str(out)]) == 1
        assert json.loads(out.read_text())["schema"] == evidence.SCHEMA


class TestDraftsAgreeWithEvidence:
    def _report(self, tmp_path: Path, scale: float, candidates: int = 3) -> dict[str, Any]:
        baseline, candidate = _sides(tmp_path, 3, candidates, scale)
        return evidence.evaluate(baseline, candidate)

    def test_reproduced_regression_draft_names_every_run(self, tmp_path: Path) -> None:
        report = self._report(tmp_path, 0.88)

        selected = gap_to_issue._select_operations(report, 10.0, 3)
        title, body = gap_to_issue._render_issue(report, selected[0], "e.json", "x/y")

        assert len(selected) == 2
        assert "reproduced regression" in title and "aaaaaaaaaaaa" in title
        for run in ("base-0", "base-1", "base-2", "cand-0", "cand-1", "cand-2"):
            assert f"`{run}`" in body
        assert "raw/results.json" in body
        assert "pytest bench_pycubrid.py  # cand-2" in body
        assert "https://x/1" in body
        assert "explicit approval" in body
        assert report["dedup_key"] in body

    @pytest.mark.parametrize(("scale", "candidates"), [(0.88, 2), (0.92, 3), (0.98, 3)])
    def test_no_draft_without_eligible_evidence(
        self, tmp_path: Path, scale: float, candidates: int
    ) -> None:
        report = self._report(tmp_path, scale, candidates)

        assert gap_to_issue._select_operations(report, 10.0, 3) == []

    def test_plain_compare_report_is_never_issue_evidence(self) -> None:
        compare_report = {
            "summary": {"replications": 5, "overall_status": "fail"},
            "operations": [{"name": "op", "status": "fail", "percent_change": -20.0}],
        }

        assert gap_to_issue._select_operations(compare_report, 10.0, 1) == []

    def test_nightly_evidence_uses_the_caller_thresholds(self, tmp_path: Path) -> None:
        experiment = tmp_path / "experiment"
        for i in range(3):
            _run(experiment / "runs", f"base-{i}", "baseline", BASE_OPS, pycubrid="1.11.0")
        candidate = _run(tmp_path, "cand", "candidate", {k: v * 0.94 for k, v in BASE_OPS.items()})

        report = nightly_compare.decide(candidate, [experiment], 8.0, 12.0)

        assert report["summary"]["overall_status"] == "pass"
        assert report["evidence"]["thresholds"]["noise_percent"] == 8.0
        assert {op["classification"] for op in report["evidence"]["operations"]} == {
            "advisory:equivalent"
        }

    def test_nightly_single_candidate_never_opens_an_issue(self, tmp_path: Path) -> None:
        experiment = tmp_path / "experiment"
        for i in range(3):
            _run(experiment / "runs", f"base-{i}", "baseline", BASE_OPS, pycubrid="1.11.0")
        candidate = _run(tmp_path, "cand", "candidate", {k: v * 0.5 for k, v in BASE_OPS.items()})
        output = tmp_path / "gh"

        rc = nightly_compare.main(
            ["--candidate-dir", str(candidate), "--search", str(experiment)]
            + ["--output-dir", str(tmp_path / "out"), "--github-output", str(output)]
        )

        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        assert rc == 0
        assert values["status"] == "fail"  # the comparator still reports the threshold alert
        assert values["evidence"] == "insufficient_evidence"
        assert values["open_issue"] == "false"


def _forbidden(*args: object, **kwargs: object) -> Any:
    raise AssertionError("a test tried to run a real command")


class FakeGh:
    def __init__(self, existing: str = "", fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.existing = existing
        self.fail_on = fail_on

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command))
        verb = command[2]
        if verb == self.fail_on:
            return subprocess.CompletedProcess(command, 1, "", "HTTP 403: Resource not accessible")
        output = self.existing if verb == "list" else "https://github.com/x/y/issues/9\n"
        return subprocess.CompletedProcess(command, 0, output, "")


class TestFiling:
    REPO = "cubrid-lab/cubrid-benchmark"

    def _eligible(self, tmp_path: Path) -> dict[str, Any]:
        baseline, candidate = _sides(tmp_path, 3, 3, 0.88)
        return evidence.evaluate(baseline, candidate)

    @pytest.fixture(autouse=True)
    def _repo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        # Never reach the real gh CLI from a test.
        monkeypatch.setattr(filer, "_run", _forbidden)
        monkeypatch.setattr(filer.subprocess, "run", _forbidden)

    def test_plan_only_by_default(self, tmp_path: Path) -> None:
        gh = FakeGh()

        outcome = filer.file_issue(self._eligible(tmp_path), self.REPO, execute=False, runner=gh)

        assert outcome["action"] == "plan"
        assert gh.calls == []

    def test_running_in_another_repository_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_REPOSITORY", "cubrid-lab/pycubrid")
        gh = FakeGh()

        outcome = filer.file_issue(self._eligible(tmp_path), self.REPO, execute=True, runner=gh)
        other = filer.file_issue(
            self._eligible(tmp_path / "o"), "cubrid-lab/pycubrid", execute=True, runner=gh
        )

        assert outcome["action"] == "refused" and other["action"] == "refused"
        assert gh.calls == []

    def test_cross_repository_filing_is_refused(self, tmp_path: Path) -> None:
        gh = FakeGh()

        outcome = filer.file_issue(
            self._eligible(tmp_path), "cubrid-lab/pycubrid", execute=True, runner=gh
        )

        assert outcome["action"] == "refused"
        assert gh.calls == []

    def test_creates_one_issue_when_none_matches(self, tmp_path: Path) -> None:
        gh = FakeGh()

        outcome = filer.file_issue(self._eligible(tmp_path), self.REPO, execute=True, runner=gh)

        assert outcome["action"] == "created"
        assert [call[2] for call in gh.calls] == ["list", "create"]
        assert any(outcome["dedup_key"] in part for part in gh.calls[0])

    def test_comments_on_the_matching_issue_instead_of_a_new_one(self, tmp_path: Path) -> None:
        gh = FakeGh(existing="42\n")

        outcome = filer.file_issue(self._eligible(tmp_path), self.REPO, execute=True, runner=gh)

        assert outcome == {**outcome, "action": "commented", "number": "42"}
        assert [call[2] for call in gh.calls] == ["list", "comment"]

    def test_permission_failure_is_an_error_not_a_filed_issue(self, tmp_path: Path) -> None:
        gh = FakeGh(fail_on="create")

        with pytest.raises(filer.FilingError, match="403"):
            filer.file_issue(self._eligible(tmp_path), self.REPO, execute=True, runner=gh)

    def test_ineligible_evidence_files_nothing(self, tmp_path: Path) -> None:
        baseline, candidate = _sides(tmp_path, 3, 1, 0.5)
        gh = FakeGh()

        outcome = filer.file_issue(
            evidence.evaluate(baseline, candidate), self.REPO, execute=True, runner=gh
        )

        assert outcome["action"] == "none"
        assert gh.calls == []

    def test_cli_permission_failure_exit_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(self._eligible(tmp_path)), encoding="utf-8")
        gh = FakeGh(fail_on="list")
        monkeypatch.setattr(filer, "_run", gh)

        assert filer.main(["--evidence", str(path), "--execute"]) == 2
        assert [call[2] for call in gh.calls] == ["list"]

    def test_default_runner_is_resolved_at_call_time(self, tmp_path: Path) -> None:
        # Regression: a default argument once bound the real gh runner past the test's mock.
        with pytest.raises(AssertionError, match="real command"):
            filer.file_issue(self._eligible(tmp_path), self.REPO, execute=True)
