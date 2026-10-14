"""Validated throughput and observed latency percentiles from pytest-benchmark stats.

Output contract shared by ``normalize_results.py`` and ``emit_benchforge_v2.py``:

- ``p95_latency`` / ``p99_latency`` are **observed** sample percentiles, in seconds, computed
  from the per-round samples pytest-benchmark stores in ``stats.data``. Each sample is the
  duration of one measured round (``iterations`` calls of the benchmarked function), not of one
  SQL statement.
- Method: linear interpolation between the two closest ranks (Hyndman and Fan type 7, the
  ``numpy.percentile`` / ``statistics.quantiles(method="inclusive")`` default).
- A percentile is reported only with enough samples: ``MIN_SAMPLES[95] = 20`` and
  ``MIN_SAMPLES[99] = 100``. Otherwise the key is omitted and ``latency_percentiles_unavailable``
  states why. No value is ever derived from mean and standard deviation, and pytest-benchmark's
  own summary fields (``q1``/``q3``/``iqr``) are not reinterpreted as tail percentiles.
- Invalid statistics (non-finite or non-positive mean, non-finite or negative throughput or
  samples) raise ``InvalidBenchmarkStats`` instead of producing a number.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

QUANTILE_METHOD = "linear interpolation between closest ranks (Hyndman-Fan type 7)"
SAMPLE_SOURCE = "pytest-benchmark stats.data (seconds per measured round)"
MIN_SAMPLES = {95: 20, 99: 100}


class InvalidBenchmarkStats(ValueError):
    """A benchmark entry's statistics cannot produce a trustworthy number."""


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def throughput(name: str, stats: Mapping[str, object]) -> float:
    """Return ops/sec from ``stats.ops`` or ``1 / stats.mean``; reject anything invalid."""
    mean = _finite_number(stats.get("mean"))
    if mean is None or mean <= 0:
        raise InvalidBenchmarkStats(f"{name}: mean must be a finite positive number, got {mean!r}")
    raw_ops = stats.get("ops")
    if raw_ops is None:
        return 1.0 / mean
    ops = _finite_number(raw_ops)
    if ops is None or ops <= 0:
        raise InvalidBenchmarkStats(
            f"{name}: ops must be a finite positive number, got {raw_ops!r}"
        )
    return ops


def stddev(name: str, stats: Mapping[str, object]) -> float:
    raw = stats.get("stddev", 0.0)
    value = _finite_number(raw)
    if value is None or value < 0:
        raise InvalidBenchmarkStats(f"{name}: stddev must be finite and >= 0, got {raw!r}")
    return value


def empirical_quantile(samples: Sequence[float], percent: float) -> float:
    """Hyndman-Fan type 7 quantile of ``samples`` (``0 <= percent <= 100``)."""
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def observed_latency(name: str, stats: Mapping[str, object]) -> dict[str, object]:
    """Return observed p95/p99 fields, or the reason they are unavailable."""
    raw = stats.get("data")
    if raw is None:
        return {"latency_percentiles_unavailable": "no per-round samples (stats.data) recorded"}
    if not isinstance(raw, list):
        raise InvalidBenchmarkStats(f"{name}: stats.data must be a list of seconds")
    samples: list[float] = []
    for item in raw:
        value = _finite_number(item)
        if value is None or value < 0:
            raise InvalidBenchmarkStats(f"{name}: stats.data holds an invalid sample {item!r}")
        samples.append(value)

    fields: dict[str, object] = {
        "latency_samples": len(samples),
        "latency_sample_source": SAMPLE_SOURCE,
        "latency_quantile_method": QUANTILE_METHOD,
    }
    missing: list[str] = []
    for percent, minimum in MIN_SAMPLES.items():
        if len(samples) >= minimum:
            fields[f"p{percent}_latency"] = round(empirical_quantile(samples, percent), 9)
        else:
            missing.append(f"p{percent} needs >= {minimum} samples")
    if missing:
        fields["latency_percentiles_unavailable"] = f"{len(samples)} samples; " + ", ".join(missing)
    return fields
