"""Pinned Python lanes: matrix selection, pin verification, cell packaging and manifest."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
python_lanes = cast(Any, importlib.import_module("python_lanes"))
generate_manifest = cast(Any, importlib.import_module("generate_manifest"))

SHA = "0123456789abcdef0123456789abcdef01234567"


class TestPlan:
    def test_default_lane_is_one_representative_cell(self) -> None:
        cells = python_lanes.plan("", python_lanes.load_lanes())

        assert [cell["id"] for cell in cells] == ["representative-py3.12-cubrid11.4"]
        assert cells[0]["image"] == "cubrid/cubrid:11.4"
        assert cells[0]["constraints"] == "python/constraints-current.txt"

    def test_release_lane_covers_oldest_newest_python_and_both_servers(self) -> None:
        cells = python_lanes.plan("release", python_lanes.load_lanes())

        assert {(cell["python"], cell["cubrid"]) for cell in cells} == {
            ("3.11", "10.2"),
            ("3.11", "11.4"),
            ("3.14", "10.2"),
            ("3.14", "11.4"),
        }

    def test_archival_lane_is_the_legacy_1_0_0_matrix(self) -> None:
        cells = python_lanes.plan("archival", python_lanes.load_lanes())
        pins = python_lanes.read_pins(ROOT / cells[0]["constraints"])

        assert pins == {"pycubrid": "1.0.0"}
        assert {cell["python"] for cell in cells} == {"3.10", "3.11", "3.12"}

    @pytest.mark.parametrize(
        ("lanes", "message"),
        [
            ({"lanes": {}}, "unknown lane"),
            (
                {"lanes": {"representative": {"constraints": "missing.txt", "cells": []}}},
                "no constraints file",
            ),
            (
                {
                    "lanes": {
                        "representative": {
                            "constraints": "python/constraints-current.txt",
                            "cells": [{"python": "3.12", "cubrid": "latest"}],
                        }
                    }
                },
                "invalid cell",
            ),
            (
                {
                    "lanes": {
                        "representative": {
                            "constraints": "python/constraints-current.txt",
                            "cells": [{"python": "3.12", "cubrid": "11.4"}] * 2,
                        }
                    }
                },
                "repeats cell",
            ),
            (
                {
                    "lanes": {
                        "representative": {
                            "constraints": "python/constraints-current.txt",
                            "cells": [],
                        }
                    }
                },
                "no cells",
            ),
        ],
    )
    def test_invalid_lane_definitions_fail(self, lanes: dict[str, object], message: str) -> None:
        with pytest.raises(python_lanes.LaneError, match=message):
            python_lanes.plan("representative", lanes)

    def test_plan_writes_github_outputs(self, tmp_path: Path) -> None:
        output = tmp_path / "out"

        assert python_lanes.main(["plan", "--lane", "release", "--github-output", str(output)]) == 0

        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        assert values["lane"] == "release"
        assert len(json.loads(values["cells"])) == 4
        assert values["expect"].count(",") == 3

    def test_unknown_lane_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert python_lanes.main(["plan", "--lane", "nightly"]) == 1
        assert "::error::unknown lane" in capsys.readouterr().out


class TestPins:
    def test_current_pins_are_exact(self) -> None:
        pins = python_lanes.read_pins(ROOT / "python" / "constraints-current.txt")

        assert set(pins) == {"pycubrid", "sqlalchemy-cubrid", "sqlalchemy", "pytest-benchmark"}

    @pytest.mark.parametrize("line", ["pycubrid>=1.12", "pycubrid", "pycubrid==1.12.0,<2"])
    def test_non_exact_pins_are_rejected(self, tmp_path: Path, line: str) -> None:
        path = tmp_path / "c.txt"
        path.write_text(line + "\n", encoding="utf-8")

        with pytest.raises(python_lanes.LaneError, match="not an exact"):
            python_lanes.read_pins(path)


class FakeDistribution:
    def __init__(self, version: str, direct_url: dict[str, object] | None = None) -> None:
        self.version = version
        self._direct = direct_url

    def read_text(self, name: str) -> str | None:
        return json.dumps(self._direct) if self._direct and name == "direct_url.json" else None


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    env: dict[str, Any] = {
        "dists": {
            "pycubrid": FakeDistribution("1.12.0"),
            "sqlalchemy-cubrid": FakeDistribution("1.11.0"),
            "sqlalchemy": FakeDistribution("2.1.4"),
        },
        "dunder": "1.12.0",
        "requires": ["sqlalchemy<2.2,>=2.0"],
    }
    metadata = python_lanes.importlib.metadata

    def distribution(name: str) -> FakeDistribution:
        if name not in env["dists"]:
            raise metadata.PackageNotFoundError(name)
        return env["dists"][name]

    monkeypatch.setattr(metadata, "distribution", distribution)
    monkeypatch.setattr(metadata, "version", lambda name: env["dists"][name].version)
    monkeypatch.setattr(metadata, "requires", lambda name: env["requires"])
    monkeypatch.setattr(
        python_lanes.importlib,
        "import_module",
        lambda name: SimpleNamespace(__version__=env["dunder"]),
    )
    return env


PINS = {"pycubrid": "1.12.0", "sqlalchemy-cubrid": "1.11.0", "sqlalchemy": "2.1.4"}


class TestVerifyInstall:
    def test_exact_release_install_passes(self, fake_env: dict[str, Any]) -> None:
        versions = python_lanes.verify_install(PINS, None)

        assert versions["packages"]["pycubrid"] == {
            "version": "1.12.0",
            "pinned": "1.12.0",
            "source": "index",
            "__version__": "1.12.0",
        }

    def test_silently_upgraded_package_fails(self, fake_env: dict[str, Any]) -> None:
        fake_env["dists"]["sqlalchemy"] = FakeDistribution("2.1.5")

        with pytest.raises(python_lanes.LaneError, match="sqlalchemy 2.1.5 is installed"):
            python_lanes.verify_install(PINS, None)

    def test_missing_package_fails(self, fake_env: dict[str, Any]) -> None:
        del fake_env["dists"]["sqlalchemy-cubrid"]

        with pytest.raises(python_lanes.LaneError, match="sqlalchemy-cubrid is not installed"):
            python_lanes.verify_install(PINS, None)

    def test_dunder_version_mismatch_fails(self, fake_env: dict[str, Any]) -> None:
        fake_env["dunder"] = "1.11.0"

        with pytest.raises(python_lanes.LaneError, match="__version__"):
            python_lanes.verify_install(PINS, None)

    def test_non_index_install_of_a_pinned_release_fails(self, fake_env: dict[str, Any]) -> None:
        fake_env["dists"]["pycubrid"] = FakeDistribution("1.12.0", {"url": "file:///src"})

        with pytest.raises(python_lanes.LaneError, match="not the index"):
            python_lanes.verify_install(PINS, None)

    def test_sqlalchemy_outside_dialect_requirement_fails(self, fake_env: dict[str, Any]) -> None:
        fake_env["dists"]["sqlalchemy"] = FakeDistribution("2.2.0")
        pins = dict(PINS, sqlalchemy="2.2.0")

        with pytest.raises(python_lanes.LaneError, match="does not satisfy"):
            python_lanes.verify_install(pins, None)

    def test_source_commit_install_passes(self, fake_env: dict[str, Any]) -> None:
        fake_env["dists"]["pycubrid"] = FakeDistribution(
            "1.13.0.dev0",
            {"url": "https://github.com/cubrid-lab/pycubrid", "vcs_info": {"commit_id": SHA}},
        )
        fake_env["dunder"] = "1.13.0.dev0"

        versions = python_lanes.verify_install(PINS, SHA)

        assert versions["packages"]["pycubrid"]["source"] == "vcs"
        assert versions["packages"]["pycubrid"]["commit_id"] == SHA

    def test_failed_source_install_fails(self, fake_env: dict[str, Any]) -> None:
        # The release is still installed: the requested source commit never replaced it.
        with pytest.raises(python_lanes.LaneError, match="source commit is None"):
            python_lanes.verify_install(PINS, SHA)

    @pytest.mark.parametrize("sha", ["abc123", "main", SHA.upper(), SHA + "0"])
    def test_invalid_source_pin_fails(self, fake_env: dict[str, Any], sha: str) -> None:
        with pytest.raises(python_lanes.LaneError, match="40-hex"):
            python_lanes.verify_install(PINS, sha)

    def test_real_environment_matches_current_pins(self, tmp_path: Path) -> None:
        # The offline test environment installs python/requirements.txt; when it also matches
        # the pins this exercises the real metadata path end to end.
        pins = python_lanes.read_pins(ROOT / "python" / "constraints-current.txt")
        try:
            versions = python_lanes.verify_install(pins, None)
        except python_lanes.LaneError as exc:
            pytest.skip(f"local environment is not the pinned set: {exc}")
        assert versions["packages"]["pycubrid"]["version"] == pins["pycubrid"]


def _cell(tmp_path: Path, lane: str, python: str, cubrid: str) -> Path:
    out = tmp_path / f"pinned-{lane}-py{python}-cubrid{cubrid}-1-1"
    out.mkdir(parents=True)
    (out / "versions.json").write_text(
        json.dumps({"packages": {"pycubrid": {"version": "1.12.0", "source": "index"}}}),
        encoding="utf-8",
    )
    (out / "python_tier1_pycubrid.json").write_text('{"benchmarks": [{}]}', encoding="utf-8")
    args = ["package-cell", "--dir", str(out), "--lane", lane, "--python", python]
    assert python_lanes.main(args + ["--cubrid", cubrid]) == 0
    return out


class TestCellsAndManifest:
    def test_package_cell_records_versions(self, tmp_path: Path) -> None:
        out = _cell(tmp_path, "release", "3.11", "10.2")
        cell = json.loads((out / "cell.json").read_text())

        assert cell["cell_id"] == "release-py3.11-cubrid10.2"
        assert cell["driver_version"] == "1.12.0"
        assert cell["result_file"] == "python_tier1_pycubrid.json"

    def test_package_cell_without_result_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "cell"
        out.mkdir()
        (out / "versions.json").write_text(
            json.dumps({"packages": {"pycubrid": {"version": "1.12.0"}}}), encoding="utf-8"
        )

        args = ["package-cell", "--dir", str(out), "--lane", "release", "--python", "3.11"]
        assert python_lanes.main(args + ["--cubrid", "10.2"]) == 1

    def test_complete_manifest(self, tmp_path: Path) -> None:
        cells = python_lanes.plan("release", python_lanes.load_lanes())
        for cell in cells:
            _cell(tmp_path, "release", cell["python"], cell["cubrid"])

        manifest = generate_manifest.build_manifest(
            tmp_path, "*/cell.json", [cell["id"] for cell in cells]
        )

        assert sorted(entry["cell_id"] for entry in manifest["matrix"]) == sorted(
            cell["id"] for cell in cells
        )
        assert {entry["cubrid"] for entry in manifest["matrix"]} == {"10.2", "11.4"}

    def test_missing_failed_cell_fails_the_manifest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cells = python_lanes.plan("release", python_lanes.load_lanes())
        for cell in cells[:-1]:  # the last cell failed or was cancelled: no cell.json
            _cell(tmp_path, "release", cell["python"], cell["cubrid"])

        args = ["--results-dir", str(tmp_path), "--glob", "*/cell.json"]
        for cell in cells:
            args += ["--expect-cell", cell["id"]]

        assert generate_manifest.main(args) == 1
        assert f"missing cells ['{cells[-1]['id']}']" in capsys.readouterr().out
        assert not (tmp_path / "manifest.json").exists()
