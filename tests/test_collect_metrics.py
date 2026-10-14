"""collect_metrics.py: what the peak-RSS fields mean, and when they must say "unavailable"."""

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
collect_metrics = cast(Any, importlib.import_module("collect_metrics"))


class TestMemoryMetrics:
    def test_fresh_wrapper_reports_the_child_peak_on_linux(self) -> None:
        memory = collect_metrics.memory_metrics(0, 215652, "linux")

        assert memory["child_peak_rss_kb"] == 215652
        assert memory["ru_maxrss_unit"] == "KiB"
        assert memory["measured_children"] == 1
        assert "child_peak_rss_unavailable" not in memory
        assert memory["peak_rss_delta_kb"] == 215652  # deprecated alias agrees here

    def test_earlier_child_high_water_mark_is_not_a_zero_cost_child(self) -> None:
        # A 200 MiB child ran first; the measured 50 MiB child leaves the mark unchanged.
        memory = collect_metrics.memory_metrics(215656, 215656, "linux")

        assert memory["child_peak_rss_kb"] is None
        assert "fresh collect_metrics.py process" in memory["child_peak_rss_unavailable"]
        assert memory["peak_rss_delta_kb"] == 0  # the misleading old value, kept only as alias

    def test_macos_bytes_are_converted_to_kib(self) -> None:
        memory = collect_metrics.memory_metrics(0, 64 * 1024 * 1024, "darwin")

        assert memory["ru_maxrss_unit"] == "bytes"
        assert memory["child_peak_rss_kb"] == 64 * 1024
        assert memory["rusage_children_after_kb"] == 64 * 1024

    def test_unknown_platform_unit_is_unavailable(self) -> None:
        memory = collect_metrics.memory_metrics(0, 1234, "sunos5")

        assert memory["child_peak_rss_kb"] is None
        assert memory["peak_rss_delta_kb"] is None
        assert "unit is unknown" in memory["child_peak_rss_unavailable"]


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    before: int,
    after: int,
    returncode: int,
    result: str | None,
) -> tuple[int, dict[str, Any]]:
    readings = iter([SimpleNamespace(ru_maxrss=before), SimpleNamespace(ru_maxrss=after)])
    monkeypatch.setattr(collect_metrics.resource, "getrusage", lambda who: next(readings))
    monkeypatch.setattr(
        collect_metrics.subprocess, "run", lambda cmd, check: SimpleNamespace(returncode=returncode)
    )
    monkeypatch.setattr(collect_metrics.sys, "platform", "linux")
    result_path = tmp_path / "result.json"
    if result is not None:
        result_path.write_text(result, encoding="utf-8")
    metrics_path = tmp_path / "metrics.json"
    rc = collect_metrics.main(
        ["--result-json", str(result_path), "--metrics-json", str(metrics_path), "--", "true"]
    )
    return rc, json.loads(metrics_path.read_text(encoding="utf-8"))


class TestWrapper:
    def test_success_with_result(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        rc, payload = _run(
            tmp_path, monkeypatch, before=0, after=900, returncode=0, result='{"benchmarks": []}'
        )

        assert rc == 0
        assert payload["result_status"] == "ok"
        assert payload["metrics"]["memory"]["child_peak_rss_kb"] == 900

    def test_command_failure_status_is_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc, payload = _run(tmp_path, monkeypatch, before=0, after=900, returncode=5, result=None)

        assert rc == 5
        assert payload["metrics"]["returncode"] == 5
        assert payload["result_status"] == "missing"

    @pytest.mark.parametrize(("result", "status"), [(None, "missing"), ("{oops", "invalid_json")])
    def test_successful_command_without_valid_result_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: str | None, status: str
    ) -> None:
        rc, payload = _run(tmp_path, monkeypatch, before=0, after=900, returncode=0, result=result)

        assert rc == collect_metrics.EXIT_RESULT_MISSING
        assert payload["result_status"] == status

    def test_reused_wrapper_reports_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc, payload = _run(
            tmp_path, monkeypatch, before=5000, after=5000, returncode=0, result="{}"
        )

        assert rc == 0
        assert payload["metrics"]["memory"]["child_peak_rss_kb"] is None
        assert "unavailable" in capsys.readouterr().err


def test_real_child_peak_matches_the_childs_own_reading(tmp_path: Path) -> None:
    """End to end on this host: one fresh wrapper, one child that reports its own maxrss."""
    if sys.platform not in collect_metrics.RU_MAXRSS_UNITS:
        pytest.skip(f"ru_maxrss unit unknown on {sys.platform}")
    child = tmp_path / "child.py"
    child.write_text(
        "import json, resource, sys\n"
        "block = bytearray(32 * 1024 * 1024)\n"
        "block[::4096] = b'x' * len(block[::4096])\n"
        "json.dump({'self': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},"
        " open(sys.argv[1], 'w'))\n",
        encoding="utf-8",
    )
    result = tmp_path / "result.json"
    metrics = tmp_path / "metrics.json"
    import subprocess

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "collect_metrics.py"), "--result-json", str(result)]
        + ["--metrics-json", str(metrics), "--", sys.executable, str(child), str(result)],
        check=False,
    )
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    memory = payload["metrics"]["memory"]
    _, divisor = collect_metrics.RU_MAXRSS_UNITS[sys.platform]

    assert completed.returncode == 0
    assert memory["child_peak_rss_kb"] == payload["result"]["self"] // divisor
    assert memory["child_peak_rss_kb"] >= 32 * 1024
