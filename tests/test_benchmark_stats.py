"""Observed latency percentiles and validation for the normalizer and benchforge emitter."""

from __future__ import annotations

import importlib
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

benchmark_stats = cast(Any, importlib.import_module("benchmark_stats"))
normalize_results = cast(Any, importlib.import_module("normalize_results"))
emit_benchforge_v2 = cast(Any, importlib.import_module("scripts.emit_benchforge_v2"))

# 95 fast rounds and 5 slow ones: a skewed, two-mode distribution.
SKEWED = [0.001] * 95 + [0.050] * 5
# A symmetric spread of 200 samples.
SYMMETRIC = [0.010 + 0.001 * math.sin(i) for i in range(200)]


def _entry(samples: list[float] | None, name: str = "op", **overrides: object) -> dict[str, Any]:
    stats: dict[str, object] = {}
    if samples is not None:
        stats = {
            "mean": statistics.fmean(samples),
            "stddev": statistics.stdev(samples) if len(samples) > 1 else 0.0,
            "ops": 1.0 / statistics.fmean(samples),
            "rounds": len(samples),
            "iterations": 1,
            "min": min(samples),
            "max": max(samples),
            "data": samples,
        }
    else:
        stats = {"mean": 0.01, "stddev": 0.001, "ops": 100.0, "rounds": 5, "iterations": 1}
    stats.update(overrides)
    return {"name": name, "stats": stats}


def _normal_model(samples: list[float], z: float) -> float:
    return statistics.fmean(samples) + z * statistics.stdev(samples)


class TestEmpiricalQuantile:
    def test_matches_statistics_inclusive_method(self) -> None:
        expected = statistics.quantiles(SYMMETRIC, n=100, method="inclusive")
        assert benchmark_stats.empirical_quantile(SYMMETRIC, 95) == pytest.approx(expected[94])
        assert benchmark_stats.empirical_quantile(SYMMETRIC, 99) == pytest.approx(expected[98])

    def test_skewed_fixture_differs_from_the_normal_model(self) -> None:
        fields = benchmark_stats.observed_latency("op", {"data": SKEWED})

        # Observed: p95 interpolates between the 95th (fast) and 96th (slow) ranked samples.
        assert fields["p95_latency"] == pytest.approx(0.001 + 0.049 * 0.05)
        assert fields["p99_latency"] == pytest.approx(0.050)
        assert fields["latency_samples"] == 100
        # The removed normal-model estimate would have reported very different tails.
        assert _normal_model(SKEWED, 1.645) > 5 * fields["p95_latency"]
        assert _normal_model(SKEWED, 2.326) < fields["p99_latency"]

    def test_symmetric_fixture_reports_both_percentiles(self) -> None:
        fields = benchmark_stats.observed_latency("op", {"data": SYMMETRIC})

        assert "latency_percentiles_unavailable" not in fields
        assert fields["latency_quantile_method"] == benchmark_stats.QUANTILE_METHOD
        assert fields["p95_latency"] <= max(SYMMETRIC)


class TestAvailability:
    def test_no_samples_reports_unavailable(self) -> None:
        fields = benchmark_stats.observed_latency("op", {"mean": 0.01})

        assert "p95_latency" not in fields and "p99_latency" not in fields
        assert "no per-round samples" in fields["latency_percentiles_unavailable"]

    @pytest.mark.parametrize(
        ("count", "p95", "p99"), [(5, False, False), (20, True, False), (100, True, True)]
    )
    def test_small_samples_omit_percentiles(self, count: int, p95: bool, p99: bool) -> None:
        fields = benchmark_stats.observed_latency("op", {"data": [0.01] * count})

        assert ("p95_latency" in fields) is p95
        assert ("p99_latency" in fields) is p99
        assert fields["latency_samples"] == count
        assert ("latency_percentiles_unavailable" in fields) is not (p95 and p99)


class TestValidation:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"mean": float("nan")},
            {"mean": float("inf")},
            {"mean": 0.0},
            {"mean": -1.0},
            {"ops": float("nan")},
            {"ops": float("inf")},
            {"ops": -5.0},
            {"ops": 0.0},
            {"stddev": float("nan")},
            {"stddev": -0.1},
            {"data": [0.01, float("nan")]},
            {"data": [0.01, -0.01]},
            {"data": "0.01"},
        ],
    )
    def test_invalid_stats_fail_explicitly(self, overrides: dict[str, object]) -> None:
        payload = {"benchmarks": [_entry(None, **overrides)]}

        with pytest.raises(benchmark_stats.InvalidBenchmarkStats):
            normalize_results.normalize(payload)
        with pytest.raises(benchmark_stats.InvalidBenchmarkStats):
            emit_benchforge_v2._results(
                payload, tier="t", language="python", driver="d", database="db"
            )

    def test_empty_input_fails(self) -> None:
        with pytest.raises(benchmark_stats.InvalidBenchmarkStats):
            normalize_results.normalize({"benchmarks": []})


class TestOutputs:
    def test_normalizer_reports_observed_percentiles_only(self) -> None:
        normalized = normalize_results.normalize(
            {"benchmarks": [_entry(SKEWED, "skewed"), _entry(SKEWED[:5], "tier1_like")]}
        )
        by_name = {item["name"]: item for item in normalized}

        assert by_name["skewed"]["p95_latency"] == pytest.approx(0.00345)
        assert by_name["skewed"]["latency_samples"] == 100
        assert "p95_latency" not in by_name["tier1_like"]
        assert "p99_latency" not in by_name["tier1_like"]
        assert by_name["tier1_like"]["latency_percentiles_unavailable"].startswith("5 samples")
        assert by_name["tier1_like"]["unit"] == "ops/sec"

    def test_emitter_extra_carries_observed_percentiles(self) -> None:
        results = emit_benchforge_v2._results(
            {"benchmarks": [_entry(SKEWED, "skewed"), _entry(SKEWED[:5], "small")]},
            tier="tier1",
            language="python",
            driver="pycubrid",
            database="cubrid",
        )
        by_name = {item["name"]: item["extra"] for item in results}

        assert by_name["skewed"]["p99_latency"] == pytest.approx(0.05)
        assert "p95_latency" not in by_name["small"]
        assert by_name["small"]["latency_samples"] == 5

    def test_real_tier1_run_reports_unavailable(self) -> None:
        raw = (
            ROOT
            / "experiments/tier1-python-workload/runs/2026-10-14_explicit-keys-at-457e9f6"
            / "raw/cubrid114_t1.json"
        )
        normalized = normalize_results.normalize(json.loads(raw.read_text(encoding="utf-8")))

        for item in normalized:
            assert "p95_latency" not in item
            assert item["latency_samples"] == 5

    def test_cli_exit_code_on_invalid_input(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"benchmarks": [_entry(None, ops=-1.0)]}), encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "normalize_results.py"), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 2
        assert "ops must be a finite positive number" in completed.stderr
        assert completed.stdout == ""
