from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any, cast

import pytest

_ = sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

emit_benchforge_v2 = cast(Any, importlib.import_module("scripts.emit_benchforge_v2"))
generate_manifest = cast(Any, importlib.import_module("scripts.generate_manifest"))
generate_report = cast(Any, importlib.import_module("scripts.generate_report"))


def _benchforge_payload() -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "metadata": {
            "timestamp": "2026-04-12T12:00:00Z",
            "git_sha": "abc123",
            "git_branch": "main",
            "runner_id": "runner-1",
            "benchmark_suite": "suite-a",
        },
        "provenance": {
            "hardware": {
                "hostname": "host-1",
                "cpu": "cpu-1",
                "cores": 8,
                "memory_gb": 16.0,
                "os": "Linux",
                "kernel": "6.8.0",
            },
            "software": {
                "python": "CPython 3.12.0",
                "docker": "26.1.0",
                "cubrid_server": "11.2",
            },
            "drivers": {"pycubrid": "0.5.0"},
        },
        "results": [
            {
                "name": "op_a",
                "unit": "ops/sec",
                "value": 123.4,
                "range": "+/- 1.2%",
                "tier": "tier2",
                "language": "python",
                "driver": "pycubrid",
                "database": "cubrid",
                "extra": {"rounds": 5},
            }
        ],
        "comparable_group": "group-1",
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _metadata(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["metadata"])


def _hardware(payload: dict[str, object]) -> dict[str, object]:
    provenance = cast(dict[str, object], payload["provenance"])
    return cast(dict[str, object], provenance["hardware"])


def _software(payload: dict[str, object]) -> dict[str, object]:
    provenance = cast(dict[str, object], payload["provenance"])
    return cast(dict[str, object], provenance["software"])


def _first_result(payload: dict[str, object]) -> dict[str, object]:
    results = cast(list[dict[str, object]], payload["results"])
    return results[0]


class TestEmitBenchforgeV2:
    def test_manual_validate_valid_payload(self) -> None:
        errors = emit_benchforge_v2._manual_validate(_benchforge_payload())

        assert errors == []

    def test_manual_validate_rejects_unknown_top_level_keys(self) -> None:
        payload = _benchforge_payload()
        payload["unexpected"] = True

        errors = emit_benchforge_v2._manual_validate(payload)

        assert "unknown top-level keys: unexpected" in errors

    def test_manual_validate_rejects_unknown_metadata_keys(self) -> None:
        payload = _benchforge_payload()
        metadata = _metadata(payload)
        metadata["extra"] = "nope"

        errors = emit_benchforge_v2._manual_validate(payload)

        assert "unknown metadata keys: extra" in errors

    def test_manual_validate_rejects_unknown_hardware_keys(self) -> None:
        payload = _benchforge_payload()
        hardware = _hardware(payload)
        hardware["gpu"] = "none"

        errors = emit_benchforge_v2._manual_validate(payload)

        assert "unknown provenance.hardware keys: gpu" in errors

    def test_manual_validate_rejects_unknown_software_keys(self) -> None:
        payload = _benchforge_payload()
        software = _software(payload)
        software["node"] = "20"

        errors = emit_benchforge_v2._manual_validate(payload)

        assert "unknown provenance.software keys: node" in errors

    def test_manual_validate_rejects_unknown_result_keys(self) -> None:
        payload = _benchforge_payload()
        result = _first_result(payload)
        result["oops"] = "extra"

        errors = emit_benchforge_v2._manual_validate(payload)

        assert "results[0] has unknown keys: oops" in errors

    def test_manual_validate_rejects_empty_results(self) -> None:
        payload = _benchforge_payload()
        payload["results"] = []

        errors = emit_benchforge_v2._manual_validate(payload)

        assert "results must be a non-empty array" in errors

    def test_manual_validate_rejects_non_finite_value(self) -> None:
        payload = _benchforge_payload()
        result = _first_result(payload)
        result["value"] = math.inf

        errors = emit_benchforge_v2._manual_validate(payload)

        assert "results[0].value must be a finite number" in errors

    def test_validate_with_jsonschema_checks_datetime_format(self) -> None:
        payload = _benchforge_payload()
        metadata = _metadata(payload)
        metadata["timestamp"] = "not-a-datetime"

        errors = emit_benchforge_v2._validate_with_jsonschema(payload)

        assert any("not a 'date-time'" in error for error in errors)


class TestGenerateManifest:
    def test_build_manifest_valid_results(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        _write_json(
            results_dir / "b.json",
            {
                "scenario": "scenario-b",
                "python": "3.12",
                "driver": "pycubrid",
                "driver_version": "0.5.0",
            },
        )
        _write_json(
            results_dir / "a.json",
            {
                "scenario": "scenario-a",
                "python": "3.11",
                "driver": "pymysql",
                "driver_version": "1.1.0",
            },
        )

        manifest = generate_manifest.build_manifest(results_dir)
        matrix = cast(list[dict[str, str]], manifest["matrix"])

        assert manifest["schema_version"] == "1.0"
        assert [entry["scenario"] for entry in matrix] == [
            "scenario-a",
            "scenario-b",
        ]
        assert [entry["result_file"] for entry in matrix] == [
            "results/a.json",
            "results/b.json",
        ]

    def test_build_manifest_empty_directory_raises(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        with pytest.raises(ValueError, match="No result JSON files found"):
            generate_manifest.build_manifest(results_dir)

    def test_build_manifest_skips_manifest_json(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        _write_json(results_dir / "manifest.json", {"ignored": True})
        _write_json(
            results_dir / "only.json",
            {
                "scenario": "scenario-a",
                "python": "3.12",
                "driver": "pycubrid",
                "driver_version": "0.5.0",
            },
        )

        manifest = generate_manifest.build_manifest(results_dir)
        matrix = cast(list[dict[str, str]], manifest["matrix"])

        assert len(matrix) == 1
        assert matrix[0]["result_file"] == "results/only.json"

    def test_build_manifest_validates_required_fields(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        _write_json(
            results_dir / "bad.json",
            {
                "python": "3.12",
                "driver": "pycubrid",
                "driver_version": "0.5.0",
            },
        )

        with pytest.raises(ValueError, match="Missing 'scenario'"):
            generate_manifest.build_manifest(results_dir)


class TestGenerateReport:
    def test_status_for_change_thresholds(self) -> None:
        assert generate_report._status_for_change(None) == "missing"
        assert generate_report._status_for_change(0.0) == "neutral"
        assert generate_report._status_for_change(3.0) == "neutral"
        assert generate_report._status_for_change(6.0) == "improved"
        assert generate_report._status_for_change(-6.0) == "alert"
        assert generate_report._status_for_change(-11.0) == "fail"

    def test_build_comparisons_union_of_names(self) -> None:
        current = cast(list[dict[str, Any]], [{"name": "op_a", "value": 100.0}])
        baseline = cast(
            list[dict[str, Any]],
            [{"name": "op_a", "value": 90.0}, {"name": "op_b", "value": 50.0}],
        )

        comparisons = generate_report._build_comparisons(current, baseline)

        names = [item.name for item in comparisons]
        assert "op_a" in names
        assert "op_b" in names
        op_b = next(item for item in comparisons if item.name == "op_b")
        assert op_b.status == "missing"

    def test_build_comparisons_no_baseline(self) -> None:
        current = cast(list[dict[str, Any]], [{"name": "op_a", "value": 100.0}])

        comparisons = generate_report._build_comparisons(current, None)

        assert len(comparisons) == 1
        assert comparisons[0].status == "missing"
        assert comparisons[0].percent_change is None

    def test_comparable_group_mismatch_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        current_path = tmp_path / "current.json"
        baseline_path = tmp_path / "baseline.json"
        output_dir = tmp_path / "out"
        current_payload = _benchforge_payload()
        baseline_payload = _benchforge_payload()
        baseline_payload["comparable_group"] = "other-group"
        _write_json(current_path, current_payload)
        _write_json(baseline_path, baseline_payload)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "generate_report.py",
                "--input",
                str(current_path),
                "--output-dir",
                str(output_dir),
                "--baseline",
                str(baseline_path),
            ],
        )

        exit_code = generate_report.main()
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "baseline.comparable_group must match input.comparable_group" in captured.err
