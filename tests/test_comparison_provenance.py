"""Offline tests for measured run provenance and comparison compatibility (#27)."""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

compare_runs = cast(Any, importlib.import_module("compare_runs"))
run_provenance = cast(Any, importlib.import_module("run_provenance"))
nightly_compare = cast(Any, importlib.import_module("nightly_compare"))

HISTORICAL_BASELINE = ROOT / "experiments" / "baseline-multilang" / "runs" / "2026-03-16_initial"
WORKFLOW = ROOT / ".github" / "workflows" / "bench.yml"


def _identity(**overrides: object) -> dict[str, object]:
    identity: dict[str, object] = {
        "host_class": "github-hosted:ubuntu24:20261005.1",
        "cpu": "AMD EPYC 7763 64-Core Processor",
        "cores": 4,
        "os": "Ubuntu 24.04.3 LTS",
        "kernel": "6.11.0-1018-azure",
        "docker": "28.0.4",
        "cubrid_image": "cubrid/cubrid:11.4",
        "cubrid_image_digest": "sha256:1248b77ad39888df9e064655937188ab99fe056ed7e35b",
        "cubrid_build": "11.4.6.1963",
        "python": "CPython 3.12.13",
        "scenario": "python-tier1",
        "workload_revision": "tier1-explicit-keys-v2",
        "schema": "bench_driver_pycubrid (id INT PRIMARY KEY, name VARCHAR(100), amount INT)",
        "data_count": 10000,
        "transaction_policy": "autocommit=false; one commit per write round",
        "measurement_protocol": "pytest-benchmark pedantic; 1 warm-up + 5 rounds",
    }
    identity.update(overrides)
    return identity


def _results(ops: dict[str, float], unit: str = "ops/sec") -> list[dict[str, object]]:
    return [{"name": name, "unit": unit, "value": value} for name, value in ops.items()]


def _write_run(
    run_dir: Path,
    *,
    role: str,
    identity: dict[str, object] | None,
    ops: dict[str, float],
    group: str | None = None,
    pycubrid: str = "1.12.0",
    unit: str = "ops/sec",
    date: str = "2026-10-14T00:00:00Z",
) -> Path:
    run_dir.mkdir(parents=True)
    (run_dir / "raw").mkdir()
    (run_dir / "raw" / "results.json").write_text(json.dumps(_results(ops, unit)), encoding="utf-8")
    if identity is None:
        text = (
            f'run_id: "{run_dir.name}"\ndate: "{date}"\nrole: {role}\n'
            f'comparable_group: "{group or ""}"\nartifacts:\n  raw_dir: raw/\n'
        )
    else:
        text = run_provenance.render_run_yaml(
            run_id=run_dir.name,
            date=date,
            label=run_dir.name,
            role=role,
            identity=identity,
            software={
                "benchmark_source_sha": "ab" * 20,
                "benchmark_source_dirty": "false",
                "packages": {"pycubrid": pycubrid},
            },
            notes="",
        )
        if group is not None:
            derived = compare_runs.derive_comparable_group(identity)
            text = text.replace(f'comparable_group: "{derived}"', f'comparable_group: "{group}"')
    (run_dir / "run.yaml").write_text(text, encoding="utf-8")
    return run_dir


OPS = {"insert": 100.0, "select": 200.0}


def _historical_copy(run_dir: Path) -> Path:
    """The real 2026-03 run.yaml with unambiguous results (its own raw file is ambiguous)."""
    run_dir.mkdir(parents=True)
    shutil.copyfile(HISTORICAL_BASELINE / "run.yaml", run_dir / "run.yaml")
    (run_dir / "raw").mkdir()
    (run_dir / "raw" / "results.json").write_text(json.dumps(_results(OPS)), encoding="utf-8")
    return run_dir


def _compare(baseline: Path, candidate: Path, allow_legacy: bool = False) -> dict[str, Any]:
    return compare_runs._compare_runs(
        compare_runs._load_run(baseline),
        compare_runs._load_run(candidate),
        5.0,
        10.0,
        allow_legacy=allow_legacy,
    )


class TestComparator:
    def test_copied_historical_group_is_not_comparable(self, tmp_path: Path) -> None:
        historical = compare_runs._parse_run_yaml(HISTORICAL_BASELINE / "run.yaml")
        copied = str(historical["comparable_group"])
        baseline = _historical_copy(tmp_path / "experiment" / "runs" / "2026-03-16_initial")
        candidate = _write_run(
            tmp_path / "candidate", role="candidate", identity=_identity(), ops=OPS, group=copied
        )

        report = _compare(baseline, candidate)

        summary = report["summary"]
        assert summary["overall_status"] == "not_comparable"
        assert summary["exit_code"] == compare_runs.EXIT_NOT_COMPARABLE
        reasons = "\n".join(summary["reasons"])
        assert "baseline run.yaml has no comparison_identity" in reasons
        assert "copied or edited" in reasons
        assert report["operations"] == []

    def test_copied_group_is_rejected_even_with_legacy_flag(self, tmp_path: Path) -> None:
        historical = compare_runs._parse_run_yaml(HISTORICAL_BASELINE / "run.yaml")
        baseline = _historical_copy(tmp_path / "baseline")
        candidate = _write_run(
            tmp_path / "candidate",
            role="candidate",
            identity=_identity(),
            ops=OPS,
            group=str(historical["comparable_group"]),
        )

        report = _compare(baseline, candidate, allow_legacy=True)

        assert report["summary"]["overall_status"] == "not_comparable"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("host_class", "fixed:bench-01"),
            ("cubrid_image", "cubrid/cubrid:11.2"),
            ("cubrid_image_digest", "sha256:other"),
            ("workload_revision", "tier1-autoincrement-v1"),
            ("data_count", 1000),
        ],
    )
    def test_identity_difference_is_not_comparable(
        self, tmp_path: Path, field: str, value: object
    ) -> None:
        baseline = _write_run(tmp_path / "b", role="baseline", identity=_identity(), ops=OPS)
        candidate = _write_run(
            tmp_path / "c", role="candidate", identity=_identity(**{field: value}), ops=OPS
        )

        report = _compare(baseline, candidate)

        assert report["summary"]["overall_status"] == "not_comparable"
        assert any(
            f"comparison_identity.{field} differs" in reason
            for reason in report["summary"]["reasons"]
        )
        provenance = report["provenance"]
        assert provenance["baseline_identity"][field] != provenance["candidate_identity"][field]

    def test_valid_pair_with_different_driver_is_compared(self, tmp_path: Path) -> None:
        baseline = _write_run(
            tmp_path / "b", role="baseline", identity=_identity(), ops=OPS, pycubrid="1.11.0"
        )
        candidate = _write_run(
            tmp_path / "c",
            role="candidate",
            identity=_identity(),
            ops={"insert": 101.0, "select": 199.0},
            pycubrid="1.12.0",
        )

        report = _compare(baseline, candidate)

        assert report["summary"]["overall_status"] == "pass"
        assert report["summary"]["exit_code"] == compare_runs.EXIT_PASS
        provenance = report["provenance"]
        assert provenance["comparison_basis"] == "measured comparison_identity"
        assert provenance["baseline_software_under_test"]["packages"]["pycubrid"] == "1.11.0"
        assert provenance["candidate_software_under_test"]["packages"]["pycubrid"] == "1.12.0"

    def test_blank_groups_are_never_comparable(self, tmp_path: Path) -> None:
        baseline = _write_run(tmp_path / "b", role="baseline", identity=None, ops=OPS, group="")
        candidate = _write_run(tmp_path / "c", role="candidate", identity=None, ops=OPS, group="")

        report = _compare(baseline, candidate, allow_legacy=True)

        assert report["summary"]["overall_status"] == "not_comparable"
        assert "baseline comparable_group is missing or blank" in report["summary"]["reasons"]

    def test_legacy_pair_needs_explicit_flag(self, tmp_path: Path) -> None:
        baseline = _write_run(tmp_path / "b", role="baseline", identity=None, ops=OPS, group="g")
        candidate = _write_run(tmp_path / "c", role="candidate", identity=None, ops=OPS, group="g")

        strict = _compare(baseline, candidate)
        legacy = _compare(baseline, candidate, allow_legacy=True)

        assert strict["summary"]["overall_status"] == "not_comparable"
        assert legacy["summary"]["overall_status"] == "pass"
        assert legacy["provenance"]["comparison_basis"].startswith("legacy")

    def test_unknown_identity_field_is_not_comparable(self, tmp_path: Path) -> None:
        identity = _identity(cubrid_image_digest="unknown")
        baseline = _write_run(tmp_path / "b", role="baseline", identity=identity, ops=OPS)
        candidate = _write_run(tmp_path / "c", role="candidate", identity=identity, ops=OPS)

        report = _compare(baseline, candidate)

        assert report["summary"]["overall_status"] == "not_comparable"
        assert any("cubrid_image_digest" in reason for reason in report["summary"]["reasons"])

    def test_wrong_roles_are_not_comparable(self, tmp_path: Path) -> None:
        baseline = _write_run(tmp_path / "b", role="candidate", identity=_identity(), ops=OPS)
        candidate = _write_run(tmp_path / "c", role="candidate", identity=_identity(), ops=OPS)

        report = _compare(baseline, candidate)

        assert report["summary"]["overall_status"] == "not_comparable"

    def test_unit_and_operation_mismatch_are_not_comparable(self, tmp_path: Path) -> None:
        baseline = _write_run(tmp_path / "b", role="baseline", identity=_identity(), ops=OPS)
        candidate = _write_run(
            tmp_path / "c",
            role="candidate",
            identity=_identity(),
            ops={"insert": 100.0, "update": 1.0},
            unit="scans/sec",
        )

        reasons = _compare(baseline, candidate)["summary"]["reasons"]

        assert "operations missing in candidate: select" in reasons
        assert "operations missing in baseline: update" in reasons
        assert any("unit differs" in reason for reason in reasons)

    def test_historical_mixed_driver_results_are_ambiguous(self) -> None:
        # The 2026-03 python_tier1.json holds pycubrid and PyMySQL benchmarks under the same
        # names, so a name-keyed comparison silently kept only one of each pair.
        with pytest.raises(ValueError, match="Ambiguous operation name"):
            compare_runs._load_run(HISTORICAL_BASELINE)

    def test_duplicate_names_in_normalized_list_are_ambiguous(self, tmp_path: Path) -> None:
        run = _write_run(tmp_path / "b", role="baseline", identity=_identity(), ops=OPS)
        (run / "raw" / "results.json").write_text(
            json.dumps(_results(OPS) + _results({"insert": 1.0})), encoding="utf-8"
        )

        with pytest.raises(ValueError, match="Ambiguous operation name 'insert'"):
            compare_runs._load_run(run)

    @pytest.mark.parametrize("unit", ["ms", None])
    def test_unsupported_or_missing_unit_is_not_comparable(
        self, tmp_path: Path, unit: str | None
    ) -> None:
        baseline = _write_run(tmp_path / "b", role="baseline", identity=_identity(), ops=OPS)
        candidate = _write_run(tmp_path / "c", role="candidate", identity=_identity(), ops=OPS)
        for run in (baseline, candidate):
            entries = [
                {"name": name, "value": value} | ({"unit": unit} if unit else {})
                for name, value in OPS.items()
            ]
            (run / "raw" / "results.json").write_text(json.dumps(entries), encoding="utf-8")

        report = _compare(baseline, candidate)

        assert report["summary"]["overall_status"] == "not_comparable"
        assert any("unsupported unit" in reason for reason in report["summary"]["reasons"])

    def test_duplicate_pytest_names_are_ambiguous(self) -> None:
        payload = {
            "benchmarks": [
                {"name": "test_bench_insert_sequential", "stats": {"ops": 1.0}},
                {"name": "test_bench_insert_sequential", "stats": {"ops": 2.0}},
            ]
        }

        with pytest.raises(ValueError, match="Ambiguous operation name"):
            compare_runs._extract_pytest_results(payload)

    def test_cli_exit_code_for_not_comparable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        baseline = _write_run(tmp_path / "b", role="baseline", identity=_identity(), ops=OPS)
        candidate = _write_run(
            tmp_path / "c", role="candidate", identity=_identity(python="CPython 3.11.9"), ops=OPS
        )
        out = tmp_path / "report.json"
        argv = ["compare_runs.py", "--baseline-dir", str(baseline), "--candidate-dir"]
        argv += [str(candidate), "--output", str(out)]
        monkeypatch.setattr(sys, "argv", argv)

        assert compare_runs.main() == compare_runs.EXIT_NOT_COMPARABLE
        assert json.loads(out.read_text())["summary"]["overall_status"] == "not_comparable"


class TestBaselineSelectionAndNightlyDecision:
    def _decide(self, tmp_path: Path, candidate: Path, search: Path) -> dict[str, str]:
        output = tmp_path / "github_output"
        rc = nightly_compare.main(
            [
                "--candidate-dir",
                str(candidate),
                "--search",
                str(search),
                "--output-dir",
                str(tmp_path / "out"),
                "--github-output",
                str(output),
            ]
        )
        assert rc == 0
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_no_compatible_baseline_opens_no_issue(self, tmp_path: Path) -> None:
        experiment = tmp_path / "experiment"
        _write_run(
            experiment / "runs" / "2026-10-01_devbox",
            role="baseline",
            identity=_identity(host_class="fixed:devbox"),
            ops=OPS,
        )
        candidate = _write_run(
            tmp_path / "candidate", role="candidate", identity=_identity(), ops=OPS
        )

        outputs = self._decide(tmp_path, candidate, experiment)

        assert outputs == {
            "status": "not_comparable",
            "exit_code": str(compare_runs.EXIT_NOT_COMPARABLE),
            "evidence": "not_comparable",
            "open_issue": "false",
        }
        comment = (tmp_path / "out" / "regression_comment.md").read_text()
        assert "needs fresh baseline" in comment

    def test_historical_experiment_is_never_selected(self, tmp_path: Path) -> None:
        candidate = _write_run(
            tmp_path / "candidate", role="candidate", identity=_identity(), ops=OPS
        )

        baseline, reason = run_provenance.select_baseline(
            candidate, [ROOT / "experiments" / "baseline-multilang"]
        )

        assert baseline is None
        assert reason.startswith("not comparable / needs fresh baseline")

    def test_corrupt_candidate_results_are_an_error_even_without_baseline(
        self, tmp_path: Path
    ) -> None:
        candidate = _write_run(
            tmp_path / "candidate", role="candidate", identity=_identity(), ops=OPS
        )
        (candidate / "raw" / "results.json").write_text("not json", encoding="utf-8")
        output = tmp_path / "github_output"

        rc = nightly_compare.main(
            ["--candidate-dir", str(candidate), "--search", str(tmp_path / "empty")]
            + ["--output-dir", str(tmp_path / "out"), "--github-output", str(output)]
        )

        # A corrupt raw result is an error (#34), never "not comparable" with a green exit.
        assert rc == compare_runs.EXIT_ERROR
        assert not output.exists()

    def test_single_run_regression_on_compatible_pair_stays_advisory(self, tmp_path: Path) -> None:
        experiment = tmp_path / "experiment"
        _write_run(
            experiment / "runs" / "2026-10-01_old",
            role="baseline",
            identity=_identity(),
            ops={"insert": 50.0, "select": 50.0},
            date="2026-10-01T00:00:00Z",
        )
        newest = _write_run(
            experiment / "runs" / "2026-10-10_new",
            role="baseline",
            identity=_identity(),
            ops=OPS,
            date="2026-10-10T00:00:00Z",
        )
        _write_run(
            experiment / "runs" / "2026-10-12_other-runner",
            role="baseline",
            identity=_identity(host_class="fixed:bench-01"),
            ops=OPS,
            date="2026-10-12T00:00:00Z",
        )
        candidate = _write_run(
            tmp_path / "candidate",
            role="candidate",
            identity=_identity(),
            ops={"insert": 80.0, "select": 200.0},
        )

        assert run_provenance.select_baseline(candidate, [experiment])[0] == newest
        outputs = self._decide(tmp_path, candidate, experiment)

        # The threshold alert is reported, but one candidate run is not issue evidence (#34).
        assert outputs == {
            "status": "fail",
            "exit_code": "1",
            "evidence": "insufficient_evidence",
            "open_issue": "false",
        }


class TestNightlyMixedResults:
    def _mixed_payload(self) -> dict[str, object]:
        benchmarks = []
        for module, scale in (("bench_pycubrid.py", 1.0), ("bench_pymysql.py", 9.0)):
            for name, value in OPS.items():
                benchmarks.append(
                    {
                        "name": name,
                        "fullname": f"python/{module}::{name}",
                        "stats": {"ops": value * scale},
                    }
                )
        return {"benchmarks": benchmarks}

    def test_mixed_tier1_file_is_compared_after_module_selection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        identity = _identity(host_class="fixed:bench-01")
        monkeypatch.setattr(run_provenance, "capture_identity", lambda args, env: identity)
        mixed = tmp_path / "python_tier1.json"
        mixed.write_text(json.dumps(self._mixed_payload()), encoding="utf-8")
        experiment = tmp_path / "experiment"
        baseline = _write_run(
            experiment / "runs" / "2026-10-01_base", role="baseline", identity=identity, ops={}
        )
        (baseline / "raw" / "results.json").write_text(
            json.dumps(
                run_provenance.select_module_results(self._mixed_payload(), "bench_pycubrid.py")
            ),
            encoding="utf-8",
        )
        candidate = tmp_path / "candidate"
        argv = ["capture", "--out-dir", str(candidate), "--label", "nightly"]
        argv += ["--results", str(mixed), "--results-module", "bench_pycubrid.py"]
        for flag in ("--scenario", "--workload-revision", "--schema", "--transaction-policy"):
            argv += [flag, "x"]
        argv += ["--measurement-protocol", "x", "--data-count", "1", "--cubrid-image", "i"]

        assert run_provenance.main(argv) == 0
        kept = json.loads((candidate / "raw" / "results.json").read_text())["benchmarks"]
        assert {entry["fullname"].split("::")[0] for entry in kept} == {"python/bench_pycubrid.py"}

        output = tmp_path / "gh"
        rc = nightly_compare.main(
            ["--candidate-dir", str(candidate), "--search", str(experiment)]
            + ["--output-dir", str(tmp_path / "out"), "--github-output", str(output)]
        )

        assert rc == 0
        assert "status=pass" in output.read_text()

    def test_unfiltered_mixed_file_is_an_input_error(self, tmp_path: Path) -> None:
        identity = _identity()
        experiment = tmp_path / "experiment"
        _write_run(experiment / "runs" / "b", role="baseline", identity=identity, ops=OPS)
        candidate = _write_run(tmp_path / "c", role="candidate", identity=identity, ops=OPS)
        (candidate / "raw" / "results.json").write_text(
            json.dumps(self._mixed_payload()), encoding="utf-8"
        )

        rc = nightly_compare.main(
            ["--candidate-dir", str(candidate), "--search", str(experiment)]
            + ["--output-dir", str(tmp_path / "out")]
        )

        assert rc == compare_runs.EXIT_ERROR

    def test_module_without_results_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="no benchmarks from bench_x.py"):
            run_provenance.select_module_results(self._mixed_payload(), "bench_x.py")

    def test_workflow_selects_the_pycubrid_module(self) -> None:
        assert "--results-module bench_pycubrid.py" in WORKFLOW.read_text(encoding="utf-8")


class TestCapture:
    def test_capture_measures_this_host_and_round_trips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(run_provenance, "_docker_version", lambda: "28.0.4")
        monkeypatch.setattr(run_provenance, "_image_digest", lambda image: "sha256:abc")
        monkeypatch.setattr(run_provenance, "_cubrid_build", lambda *a: "11.4.6.1963")
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
        monkeypatch.setenv("ImageOS", "ubuntu24")
        monkeypatch.setenv("ImageVersion", "20261005.1")
        monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef")
        monkeypatch.setenv("GITHUB_RUN_ID", "42")
        results = tmp_path / "python_tier1.json"
        results.write_text(json.dumps(_results(OPS)), encoding="utf-8")
        out_dir = tmp_path / "candidate"
        args = [
            "capture",
            "--out-dir",
            str(out_dir),
            "--label",
            "nightly",
            "--results",
            str(results),
            "--scenario",
            "python-tier1",
            "--workload-revision",
            "tier1-explicit-keys-v2",
            "--schema",
            "s",
            "--data-count",
            "10000",
            "--transaction-policy",
            "t",
            "--measurement-protocol",
            "m #1",
            "--cubrid-image",
            "cubrid/cubrid:11.4",
        ]

        assert run_provenance.main(args) == 0
        assert run_provenance.main(args) == 2  # an existing run is never overwritten

        meta = compare_runs._parse_run_yaml(out_dir / "run.yaml")
        identity = meta["comparison_identity"]
        assert identity["host_class"] == "github-hosted:ubuntu24:20261005.1"
        assert identity["cubrid_image_digest"] == "sha256:abc"
        assert identity["measurement_protocol"] == "m #1"
        assert meta["comparable_group"] == compare_runs.derive_comparable_group(identity)
        assert meta["software_under_test"]["benchmark_source_sha"] == "0123456789abcdef"
        assert (out_dir / "raw" / "results.json").is_file()
        # The default arm is the role: a plain candidate run measured the candidate condition.
        assert (meta["role"], meta["arm"]) == ("candidate", "candidate")

        repeat_dir = tmp_path / "baseline-repeat"
        repeat = ["--out-dir", str(repeat_dir), *args[3:], "--arm", "baseline"]
        assert run_provenance.main(["capture", *repeat]) == 0
        meta = compare_runs._parse_run_yaml(repeat_dir / "run.yaml")
        assert (meta["role"], meta["arm"]) == ("candidate", "baseline")

        # The first run of a group (role baseline) cannot be recorded as the candidate arm.
        contradictory = tmp_path / "contradictory"
        bad = [
            "--out-dir",
            str(contradictory),
            *args[3:],
            "--role",
            "baseline",
            "--arm",
            "candidate",
        ]
        assert run_provenance.main(["capture", *bad]) == 2
        assert not (contradictory / "run.yaml").exists()

    def test_unmeasured_fields_are_recorded_and_block_comparison(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(run_provenance, "_docker_version", lambda: "unknown")
        monkeypatch.setattr(run_provenance, "_image_digest", lambda image: "unknown")
        monkeypatch.setattr(run_provenance, "_cubrid_build", lambda *a: "unknown")
        namespace = argparse.Namespace(
            fixed_host="bench-01",
            cubrid_image="cubrid/cubrid:11.4",
            cubrid_host="localhost",
            cubrid_port=1,
            cubrid_db="benchdb",
            scenario="python-tier1",
            workload_revision="r",
            schema="s",
            data_count=1,
            transaction_policy="t",
            measurement_protocol="m",
        )

        identity = run_provenance.capture_identity(namespace, {})

        assert identity["host_class"] == "fixed:bench-01"
        assert identity["docker"] == "unknown"


class TestSerialization:
    def test_hash_in_values_is_preserved_and_distinct(self, tmp_path: Path) -> None:
        with_hash = _write_run(
            tmp_path / "a",
            role="baseline",
            identity=_identity(measurement_protocol="m #1"),
            ops=OPS,
        )
        without = _write_run(
            tmp_path / "b",
            role="candidate",
            identity=_identity(measurement_protocol="m 1"),
            ops=OPS,
        )

        a = compare_runs._parse_run_yaml(with_hash / "run.yaml")
        b = compare_runs._parse_run_yaml(without / "run.yaml")

        assert a["comparison_identity"]["measurement_protocol"] == "m #1"
        assert a["comparable_group"] != b["comparable_group"]
        assert a["comparable_group"] == compare_runs.derive_comparable_group(
            a["comparison_identity"]
        )

    def test_quoted_scalars_are_decoded_and_comments_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / "run.yaml"
        path.write_text(
            'a: "x \\"#1\\" y"  # comment\nb: plain # comment\nc: \'q # r\'\n',
            encoding="utf-8",
        )

        assert compare_runs._parse_run_yaml(path) == {"a": 'x "#1" y', "b": "plain", "c": "q # r"}


class TestUnmeasuredFields:
    @pytest.mark.parametrize("missing", ["RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion"])
    def test_partly_measured_runner_class_is_unknown(self, missing: str) -> None:
        env = {
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "ImageOS": "ubuntu24",
            "ImageVersion": "20261005.1",
        }
        env.pop(missing)

        assert run_provenance._host_class(env, None) == "unknown"

    def test_missing_hostname_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(run_provenance.platform, "node", lambda: "")

        assert run_provenance._host_class({}, None) == "unknown"

    def test_unmeasured_core_count_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(run_provenance, "_docker_version", lambda: "28.0.4")
        monkeypatch.setattr(run_provenance, "_image_digest", lambda image: "sha256:abc")
        monkeypatch.setattr(run_provenance, "_cubrid_build", lambda *a: "11.4.6.1963")
        monkeypatch.setattr(run_provenance.os, "cpu_count", lambda: None)
        namespace = argparse.Namespace(
            fixed_host="bench-01",
            cubrid_image="cubrid/cubrid:11.4",
            cubrid_host="localhost",
            cubrid_port=1,
            cubrid_db="benchdb",
            scenario="python-tier1",
            workload_revision="r",
            schema="s",
            data_count=1,
            transaction_policy="t",
            measurement_protocol="m",
        )

        assert run_provenance.capture_identity(namespace, {})["cores"] == "unknown"

    @pytest.mark.parametrize("failing", ["cursor", "execute", "fetchone", "close"])
    def test_database_failures_leave_build_unknown(
        self, monkeypatch: pytest.MonkeyPatch, failing: str
    ) -> None:
        def boom(*args: object) -> object:
            raise RuntimeError(failing)

        class Cursor:
            def execute(self, sql: str) -> None:
                if failing == "execute":
                    boom()

            def fetchone(self) -> tuple[str]:
                if failing == "fetchone":
                    boom()
                return ("11.4.6.1963",)

        class Conn:
            def cursor(self) -> Cursor:
                if failing == "cursor":
                    boom()
                return Cursor()

            def close(self) -> None:
                if failing == "close":
                    boom()

        fake = type(sys)("pycubrid")
        fake.connect = lambda **kwargs: Conn()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pycubrid", fake)

        build = run_provenance._cubrid_build("localhost", 1, "benchdb")

        assert build == ("11.4.6.1963" if failing == "close" else "unknown")


class TestWorkflowWiring:
    def test_candidate_never_copies_baseline_identity(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        assert 'baseline_meta["comparable_group"]' not in text
        assert "scripts/run_provenance.py capture" in text
        assert "scripts/nightly_compare.py" in text

    def test_no_lane_opens_regression_issues(self) -> None:
        # Single runs are not issue evidence; see tests/test_workflow_policy.py for the rest.
        text = WORKFLOW.read_text(encoding="utf-8")

        assert "gh issue create" not in text
        assert "issues: write" not in text
