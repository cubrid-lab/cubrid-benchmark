# pyright: basic, reportAny=false, reportArgumentType=false, reportReturnType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnusedCallResult=false
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import benchmark_stats  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "benchforge_v2.json"
KNOWN_DRIVER_PACKAGES = (
    "pycubrid",
    "PyMySQL",
    "sqlalchemy",
    "sqlalchemy-cubrid",
    "cubrid",
    "cubrid-dbapi",
    "mysqlclient",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert pytest-benchmark JSON into benchforge schema v2 output.",
    )
    _ = parser.add_argument("--input", required=True, help="Path to pytest-benchmark JSON input")
    _ = parser.add_argument("--output", required=True, help="Path to benchforge v2 JSON output")
    _ = parser.add_argument("--suite-name", required=True, help="Benchmark suite name")
    _ = parser.add_argument("--tier", required=True, help="Benchmark tier label")
    _ = parser.add_argument("--language", required=True, help="Benchmark language label")
    _ = parser.add_argument("--driver", required=True, help="Driver identifier for emitted results")
    _ = parser.add_argument(
        "--database", required=True, help="Database identifier for emitted results"
    )
    return parser.parse_args()


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isfinite(number):
            return number
    return default


def _to_percent(stddev: float, mean: float) -> str:
    if mean == 0:
        return "+/- 0.0%"
    return "+/- {:.1f}%".format(abs((stddev / mean) * 100.0))


def _run_command(*command: str) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"

    if completed.returncode != 0:
        return "unknown"

    output = completed.stdout.strip()
    return output or "unknown"


def _read_meminfo_gb() -> float:
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return 0.0

    for line in meminfo.read_text(encoding="utf-8").splitlines():
        if not line.startswith("MemTotal:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            break
        try:
            return round(int(parts[1]) / 1024 / 1024, 1)
        except ValueError:
            return 0.0
    return 0.0


def _python_version() -> str:
    return f"{platform.python_implementation()} {platform.python_version()}"


def _detect_cpu() -> str:
    cpu = platform.processor().strip()
    if cpu:
        return cpu

    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith("model name") and ":" in line:
                return line.split(":", 1)[1].strip()
    return "unknown"


def _docker_version() -> str:
    version = _run_command("docker", "--version")
    match = re.search(r"Docker version\s+([^,\s]+)", version)
    return match.group(1) if match else version


def _docker_image_tag(prefix: str) -> str:
    output = _run_command("docker", "ps", "--format", "{{.Image}}")
    if output == "unknown":
        return "unknown"

    for line in output.splitlines():
        image = line.strip()
        if not image.startswith(prefix):
            continue
        _, _, tag = image.partition(":")
        return tag or "latest"
    return "unknown"


def _driver_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package_name in KNOWN_DRIVER_PACKAGES:
        try:
            versions[package_name.lower().replace("-", "_")] = importlib.metadata.version(
                package_name
            )
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _sanitize_group_part(value: str) -> str:
    sanitized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return sanitized or "unknown"


def _comparable_group(hardware: Mapping[str, object], software: Mapping[str, object]) -> str:
    parts = [
        _sanitize_group_part(str(hardware.get("hostname", "unknown"))),
        _sanitize_group_part(str(hardware.get("cpu", "unknown"))),
        _sanitize_group_part(str(hardware.get("os", "unknown"))),
        _sanitize_group_part(str(hardware.get("kernel", "unknown"))),
        f"docker-{_sanitize_group_part(str(software.get('docker', 'unknown')))}",
        f"cubrid-{_sanitize_group_part(str(software.get('cubrid_server', 'unknown')))}",
    ]
    return "-".join(parts)


def _metadata(suite_name: str) -> dict[str, str]:
    git_sha = os.environ.get("GITHUB_SHA") or _run_command("git", "rev-parse", "HEAD")
    git_branch = os.environ.get("GITHUB_REF_NAME") or _run_command(
        "git", "rev-parse", "--abbrev-ref", "HEAD"
    )
    runner_id = os.environ.get("RUNNER_NAME") or os.environ.get("GITHUB_RUN_ID") or platform.node()
    return {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "git_sha": git_sha,
        "git_branch": git_branch,
        "runner_id": str(runner_id),
        "benchmark_suite": suite_name,
    }


def _provenance() -> dict[str, object]:
    hardware = {
        "hostname": platform.node() or "unknown",
        "cpu": _detect_cpu(),
        "cores": os.cpu_count() or 0,
        "memory_gb": _read_meminfo_gb(),
        "os": platform.system() or "unknown",
        "kernel": platform.release() or "unknown",
    }
    software = {
        "python": _python_version(),
        "docker": _docker_version(),
        "cubrid_server": os.environ.get("CUBRID_SERVER_VERSION")
        or _docker_image_tag("cubrid/cubrid"),
    }
    return {
        "hardware": hardware,
        "software": software,
        "drivers": _driver_versions(),
    }


def _results(
    payload: Mapping[str, object],
    tier: str,
    language: str,
    driver: str,
    database: str,
) -> list[dict[str, object]]:
    benchmarks = payload.get("benchmarks")
    if not isinstance(benchmarks, list):
        raise ValueError("Input JSON does not contain a pytest-benchmark 'benchmarks' array")

    results: list[dict[str, object]] = []
    for entry in benchmarks:
        if not isinstance(entry, Mapping):
            continue
        stats = entry.get("stats")
        if not isinstance(stats, Mapping):
            continue

        name = str(entry.get("name", "unknown"))
        value = benchmark_stats.throughput(name, stats)
        mean = _as_float(stats.get("mean"))
        stddev = benchmark_stats.stddev(name, stats)
        extra: dict[str, object] = {
            "min": round(_as_float(stats.get("min")), 9),
            "max": round(_as_float(stats.get("max")), 9),
            "mean": round(mean, 9),
            "stddev": round(stddev, 9),
            "rounds": int(_as_float(stats.get("rounds"), 0.0)),
            "iterations": int(_as_float(stats.get("iterations"), 0.0)),
        }
        # Observed percentiles only; never a mean/stddev normal-model estimate.
        extra.update(benchmark_stats.observed_latency(name, stats))
        extra["framework"] = "pytest-benchmark"
        result = {
            "name": name,
            "unit": "ops/sec",
            "value": round(value, 4),
            "range": _to_percent(stddev, mean),
            "tier": tier,
            "language": language,
            "driver": driver,
            "database": database,
            "extra": extra,
        }
        results.append(result)

    if not results:
        raise ValueError("No benchmark entries could be converted from pytest-benchmark input")
    return results


def _manual_validate(payload: Mapping[str, object]) -> list[str]:
    errors: list[str] = []

    if payload.get("schema_version") != "2.0":
        errors.append("schema_version must equal '2.0'")
    allowed_top = {"schema_version", "metadata", "provenance", "results", "comparable_group"}
    extra_top = set(payload.keys()) - allowed_top
    if extra_top:
        errors.append(f"unknown top-level keys: {', '.join(sorted(extra_top))}")

    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        errors.append("metadata must be an object")
    else:
        for key in ("timestamp", "git_sha", "git_branch", "runner_id", "benchmark_suite"):
            value = metadata.get(key)
            if not isinstance(value, str) or not value:
                errors.append(f"metadata.{key} must be a non-empty string")
        allowed_meta = {"timestamp", "git_sha", "git_branch", "runner_id", "benchmark_suite"}
        extra_meta = set(metadata.keys()) - allowed_meta
        if extra_meta:
            errors.append(f"unknown metadata keys: {', '.join(sorted(extra_meta))}")

    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        errors.append("provenance must be an object")
    else:
        hardware = provenance.get("hardware")
        software = provenance.get("software")
        drivers = provenance.get("drivers")
        if not isinstance(hardware, Mapping):
            errors.append("provenance.hardware must be an object")
        else:
            for key in ("hostname", "cpu", "os", "kernel"):
                value = hardware.get(key)
                if not isinstance(value, str) or not value:
                    errors.append(f"provenance.hardware.{key} must be a non-empty string")
            cores = hardware.get("cores")
            if not isinstance(cores, int) or cores < 0:
                errors.append("provenance.hardware.cores must be a non-negative integer")
            memory_gb = hardware.get("memory_gb")
            if (
                not isinstance(memory_gb, (int, float))
                or isinstance(memory_gb, bool)
                or memory_gb < 0
            ):
                errors.append("provenance.hardware.memory_gb must be a non-negative number")
            allowed_hw = {"hostname", "cpu", "cores", "memory_gb", "os", "kernel"}
            extra_hw = set(hardware.keys()) - allowed_hw
            if extra_hw:
                errors.append(
                    f"unknown provenance.hardware keys: {', '.join(sorted(extra_hw))}"
                )
        if not isinstance(software, Mapping):
            errors.append("provenance.software must be an object")
        else:
            for key in ("python", "docker", "cubrid_server"):
                value = software.get(key)
                if not isinstance(value, str) or not value:
                    errors.append(f"provenance.software.{key} must be a non-empty string")
            allowed_sw = {"python", "docker", "cubrid_server"}
            extra_sw = set(software.keys()) - allowed_sw
            if extra_sw:
                errors.append(
                    f"unknown provenance.software keys: {', '.join(sorted(extra_sw))}"
                )
        if not isinstance(drivers, Mapping):
            errors.append("provenance.drivers must be an object")
        else:
            for key, value in drivers.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    errors.append("provenance.drivers entries must map strings to strings")
        allowed_prov = {"hardware", "software", "drivers"}
        extra_prov = set(provenance.keys()) - allowed_prov
        if extra_prov:
            errors.append(f"unknown provenance keys: {', '.join(sorted(extra_prov))}")

    comparable_group = payload.get("comparable_group")
    if not isinstance(comparable_group, str) or not comparable_group:
        errors.append("comparable_group must be a non-empty string")

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        errors.append("results must be a non-empty array")
    else:
        for index, item in enumerate(results):
            if not isinstance(item, Mapping):
                errors.append(f"results[{index}] must be an object")
                continue
            for key in ("name", "unit", "range", "tier", "language", "driver", "database"):
                value = item.get(key)
                if not isinstance(value, str) or not value:
                    errors.append(f"results[{index}].{key} must be a non-empty string")
            value = item.get("value")
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                errors.append(f"results[{index}].value must be a finite number")
            extra = item.get("extra")
            if not isinstance(extra, Mapping):
                errors.append(f"results[{index}].extra must be an object")
            allowed_result = {
                "name",
                "unit",
                "value",
                "range",
                "tier",
                "language",
                "driver",
                "database",
                "extra",
            }
            extra_result = set(item.keys()) - allowed_result
            if extra_result:
                errors.append(
                    f"results[{index}] has unknown keys: {', '.join(sorted(extra_result))}"
                )

    return errors


def _validate_with_jsonschema(payload: Mapping[str, object]) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return _manual_validate(payload)

    schema = _read_json(SCHEMA_PATH)
    if not isinstance(schema, Mapping):
        return [f"Schema file {SCHEMA_PATH} is not a JSON object"]

    validator = jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        return [error.message for error in errors]
    return []


def emit(args: argparse.Namespace) -> dict[str, object]:
    input_path = Path(args.input)
    output_path = Path(args.output)
    payload = _read_json(input_path)
    if not isinstance(payload, Mapping):
        raise ValueError("Input benchmark JSON must be an object")

    provenance = _provenance()
    software = provenance.get("software")
    if not isinstance(software, Mapping):
        raise ValueError("Generated provenance.software must be an object")

    emitted = {
        "schema_version": "2.0",
        "metadata": _metadata(args.suite_name),
        "provenance": provenance,
        "results": _results(payload, args.tier, args.language, args.driver, args.database),
        "comparable_group": _comparable_group(
            provenance["hardware"],
            software,
        ),
    }

    errors = _validate_with_jsonschema(emitted)
    if errors:
        raise ValueError("Schema validation failed:\n- " + "\n- ".join(errors))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _ = output_path.write_text(json.dumps(emitted, indent=2) + "\n", encoding="utf-8")
    return emitted


def main() -> int:
    args = _parse_args()
    try:
        _ = emit(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
