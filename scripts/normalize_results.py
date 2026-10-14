from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_stats import (  # noqa: E402
    InvalidBenchmarkStats,
    observed_latency,
    stddev,
    throughput,
)


def _read_input(path_arg: str | None) -> dict[str, object]:
    if path_arg:
        return json.loads(Path(path_arg).read_text(encoding="utf-8"))
    return json.load(sys.stdin)


def _to_percent(stddev: float, mean: float) -> str:
    if mean == 0:
        return "+/- 0.0%"
    value = abs((stddev / mean) * 100.0)
    return "+/- {:.1f}%".format(value)


def normalize(pytest_benchmark_json: Mapping[str, object]) -> list[dict[str, object]]:
    """Convert pytest-benchmark JSON to github-action-benchmark style entries.

    ``p95_latency`` / ``p99_latency`` appear only as observed percentiles of the recorded
    per-round samples (see ``scripts/benchmark_stats.py``); otherwise they are omitted and
    ``latency_percentiles_unavailable`` says why. Invalid statistics raise
    ``InvalidBenchmarkStats``.
    """
    benchmarks = pytest_benchmark_json.get("benchmarks", [])
    output: list[dict[str, object]] = []

    if not isinstance(benchmarks, list):
        raise InvalidBenchmarkStats("input has no pytest-benchmark 'benchmarks' list")

    for entry in benchmarks:
        if not isinstance(entry, Mapping):
            continue
        stats = entry.get("stats", {})
        if not isinstance(stats, Mapping):
            continue
        name = str(entry.get("name", "unknown"))
        value = throughput(name, stats)
        mean = float(stats["mean"])  # validated by throughput()

        result: dict[str, object] = {
            "name": name,
            "unit": "ops/sec",
            "value": round(value, 4),
            "range": _to_percent(stddev(name, stats), mean),
        }
        result.update(observed_latency(name, stats))
        result["extra"] = "language=python tier=1 framework=pytest-benchmark"
        output.append(result)

    if not output:
        raise InvalidBenchmarkStats("no benchmark entries could be normalized")
    return output


def main() -> int:
    path_arg = sys.argv[1] if len(sys.argv) > 1 else None
    payload = _read_input(path_arg)
    try:
        normalized = normalize(payload)
    except InvalidBenchmarkStats as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2
    json.dump(normalized, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
