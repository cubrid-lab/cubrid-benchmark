# pyright: basic
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping
from pathlib import Path

EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_ERROR = 2
EXIT_NOT_COMPARABLE = 3

UNKNOWN = "unknown"

# Fields that must be equal for two runs to be compared directly. Driver versions and the
# benchmark source SHA are expected to differ in a regression comparison and are not listed.
IDENTITY_KEYS = (
    "host_class",
    "cpu",
    "cores",
    "os",
    "kernel",
    "docker",
    "cubrid_image",
    "cubrid_image_digest",
    "cubrid_build",
    "python",
    "scenario",
    "workload_revision",
    "schema",
    "data_count",
    "transaction_policy",
    "measurement_protocol",
)

# Throughput units, where a larger value is better; the comparator's verdicts assume that.
SUPPORTED_UNITS = ("ops/sec", "scans/sec")

RESULT_PRIORITY = (
    "results.json",
    "summary.json",
    "normalized_results.json",
    "python_tier1.json",
)


def _parse_scalar(raw_value: str) -> object:
    value = raw_value.strip()
    if not value:
        return ""
    if value in {"null", "Null", "NULL"}:
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
        return decoded if isinstance(decoded, str) else value[1:-1]
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1]
    try:
        if any(marker in value for marker in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value


def _strip_comment(value: str) -> str:
    """Drop a trailing ``# comment``; a ``#`` inside a quoted scalar is part of the value."""
    if value[:1] in {'"', "'"}:
        quote = value[0]
        index = 1
        while index < len(value):
            char = value[index]
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                return value[: index + 1]
            index += 1
        return value
    return value.split("#", 1)[0].rstrip()


def _parse_run_yaml(path: Path) -> dict[str, object]:
    root: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, root)]

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if ":" not in stripped:
            continue

        key, raw_value = stripped.split(":", 1)
        value = raw_value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()

        current = stack[-1][1]
        if not value:
            child: dict[str, object] = {}
            current[key] = child
            stack.append((indent, child))
            continue

        current[key] = _parse_scalar(_strip_comment(value))

    return root


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", str(value).lower()).strip("-") or UNKNOWN


def derive_comparable_group(identity: Mapping[str, object]) -> str:
    """Return a readable prefix plus a hash over every identity field.

    Two runs share a group only when every identity field is equal, so a group string copied
    from another run does not match unless the copying run's measured identity also matches.
    """
    missing = [key for key in IDENTITY_KEYS if key not in identity]
    if missing:
        raise ValueError(f"comparison_identity is missing: {', '.join(missing)}")
    canonical = json.dumps(
        {key: str(identity[key]) for key in IDENTITY_KEYS}, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    prefix = "-".join(
        [
            _slug(str(identity["host_class"]).split(":", 1)[0]),
            _slug(identity["cubrid_image"]).replace("cubrid-cubrid-", "cubrid"),
            _slug(identity["scenario"]),
            _slug(identity["workload_revision"]),
        ]
    )
    return f"{prefix}-{digest}"


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _coerce_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _pytest_ops_value(stats: Mapping[str, object]) -> float | None:
    ops = _coerce_float(stats.get("ops"))
    if ops is not None:
        return ops

    mean = _coerce_float(stats.get("mean"))
    if mean is not None and mean > 0:
        return 1.0 / mean

    return None


def _normalize_named_results(entries: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    operations: dict[str, dict[str, object]] = {}
    for entry in entries:
        name = entry.get("name")
        value = _coerce_float(entry.get("value"))
        if not isinstance(name, str) or value is None:
            continue
        if name in operations:
            raise ValueError(f"Ambiguous operation name {name!r}: it appears more than once")
        operations[name] = {
            "name": name,
            "value": value,
            "unit": entry.get("unit"),
            "source": "normalized",
        }
    return operations


def _extract_custom_results(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    results = payload.get("results")
    if not isinstance(results, Mapping):
        return {}

    operations: dict[str, dict[str, object]] = {}
    for name, metrics in results.items():
        if not isinstance(name, str) or not isinstance(metrics, Mapping):
            continue

        value = None
        unit = "ops/sec"
        for metric_name, metric_unit in (
            ("ops_per_sec", "ops/sec"),
            ("scans_per_sec", "scans/sec"),
        ):
            metric_value = _coerce_float(metrics.get(metric_name))
            if metric_value is not None:
                value = metric_value
                unit = metric_unit
                break

        if value is None:
            continue

        operations[name] = {
            "name": name,
            "value": value,
            "unit": unit,
            "source": "raw-results",
        }
    return operations


def _extract_pytest_results(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    benchmarks = payload.get("benchmarks")
    if not isinstance(benchmarks, list):
        return {}

    operations: dict[str, dict[str, object]] = {}
    for entry in benchmarks:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        stats = entry.get("stats")
        if not isinstance(name, str) or not isinstance(stats, Mapping):
            continue

        value = _pytest_ops_value(stats)
        if value is None:
            continue
        if name in operations:
            raise ValueError(
                f"Ambiguous operation name {name!r}: several benchmarks in one result file share "
                "it (for example the same test name from two benchmark modules)"
            )

        operations[name] = {
            "name": name,
            "value": round(value, 4),
            "unit": "ops/sec",
            "source": "pytest-benchmark",
        }

    return operations


def _load_operations(path: Path) -> dict[str, dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        operations = _normalize_named_results(
            [entry for entry in payload if isinstance(entry, dict)]
        )
    elif isinstance(payload, Mapping):
        operations = _extract_custom_results(payload)
        if not operations:
            operations = _extract_pytest_results(payload)
    else:
        operations = {}

    if not operations:
        raise ValueError(f"Could not extract comparable benchmark operations from {path}")

    return operations


def _find_results_json(run_dir: Path, run_meta: Mapping[str, object]) -> Path:
    artifacts = run_meta.get("artifacts")
    raw_dir_name = "raw"
    if isinstance(artifacts, Mapping):
        candidate = artifacts.get("raw_dir")
        if isinstance(candidate, str) and candidate.strip():
            raw_dir_name = candidate.strip()

    raw_dir = run_dir / raw_dir_name
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"Raw results directory not found: {raw_dir}")

    candidates = sorted(raw_dir.glob("*.json"))
    if not candidates:
        raise FileNotFoundError(f"No JSON result files found in {raw_dir}")

    for name in RESULT_PRIORITY:
        for path in candidates:
            if path.name == name:
                return path

    if len(candidates) == 1:
        return candidates[0]

    raise ValueError(
        f"Ambiguous raw result file in {raw_dir}; "
        f"expected one JSON file or one of {RESULT_PRIORITY}"
    )


def _load_run(run_dir: Path) -> dict[str, object]:
    run_yaml_path = run_dir / "run.yaml"
    if not run_yaml_path.is_file():
        raise FileNotFoundError(f"run.yaml not found in {run_dir}")

    run_meta = _parse_run_yaml(run_yaml_path)
    results_path = _find_results_json(run_dir, run_meta)
    operations = _load_operations(results_path)

    return {
        "dir": str(run_dir),
        "run_yaml_path": str(run_yaml_path),
        "results_path": str(results_path),
        "run_id": run_meta.get("run_id", run_dir.name),
        "date": run_meta.get("date"),
        "role": run_meta.get("role"),
        "arm": run_arm(run_meta),
        "compares_to": run_meta.get("compares_to"),
        "comparable_group": run_meta.get("comparable_group"),
        "comparison_identity": run_meta.get("comparison_identity"),
        "software_under_test": run_meta.get("software_under_test"),
        "operations": operations,
    }


ARMS = ("baseline", "candidate")


def run_arm(run: Mapping[str, object]) -> object:
    """The comparison arm of a run: ``arm`` when recorded, otherwise its ``role``.

    ``role`` is the run's place in its group's lifecycle (the first run is ``baseline``, every
    later run is ``candidate``). ``arm`` is the condition it measured, so a later repeat of the
    baseline condition is ``role: candidate`` with ``arm: baseline``. Runs recorded before
    ``arm`` existed keep their ``role`` as their arm.
    """
    arm = run.get("arm")
    return run.get("role") if arm is None else arm


def arm_problems(run: Mapping[str, object], label: str) -> list[str]:
    """Reasons a run's ``role``/``arm`` pair is not a valid comparison arm."""
    arm, role = run_arm(run), run.get("role")
    if role not in ARMS:
        return [f"{label} run has role {role!r}, expected one of {list(ARMS)}"]
    if arm not in ARMS:
        return [f"{label} run has arm {arm!r} (role {role!r}), expected one of {list(ARMS)}"]
    if role == "baseline" and arm != "baseline":
        return [
            f"{label} run has role 'baseline' but arm {arm!r}; the first run of a group is "
            "the baseline arm"
        ]
    return []


def _blank(value: object) -> bool:
    return (
        not isinstance(value, str)
        or not value.strip()
        or value.strip().lower()
        in {
            UNKNOWN,
            "null",
            "none",
        }
    )


def _comparability_problems(
    baseline: Mapping[str, object], candidate: Mapping[str, object], allow_legacy: bool
) -> tuple[list[str], str]:
    """Return (reasons the runs are not comparable, comparison basis)."""
    problems: list[str] = []
    for label, run in (("baseline", baseline), ("candidate", candidate)):
        invalid = arm_problems(run, label)
        problems.extend(invalid)
        if not invalid and run_arm(run) != label:
            problems.append(
                f"{label} run measured the {run_arm(run)!r} arm (role {run.get('role')!r}), "
                f"expected the {label!r} arm"
            )
        if _blank(run.get("comparable_group")):
            problems.append(f"{label} comparable_group is missing or blank")

    baseline_identity = baseline.get("comparison_identity")
    candidate_identity = candidate.get("comparison_identity")
    has_baseline_identity = isinstance(baseline_identity, Mapping)
    has_candidate_identity = isinstance(candidate_identity, Mapping)

    if not has_baseline_identity and not has_candidate_identity and allow_legacy:
        basis = "legacy comparable_group string (operator-asserted, no measured identity)"
        if baseline.get("comparable_group") != candidate.get("comparable_group"):
            problems.append(
                "comparable_group mismatch "
                f"({baseline.get('comparable_group')!r} != {candidate.get('comparable_group')!r})"
            )
        return problems, basis

    basis = "measured comparison_identity"
    for label, run, identity in (
        ("baseline", baseline, baseline_identity),
        ("candidate", candidate, candidate_identity),
    ):
        if not isinstance(identity, Mapping):
            problems.append(f"{label} run.yaml has no comparison_identity")
            continue
        blank = [key for key in IDENTITY_KEYS if _blank(str(identity.get(key, "")))]
        if blank:
            problems.append(
                f"{label} comparison_identity is missing or unknown: {', '.join(blank)}"
            )
            continue
        derived = derive_comparable_group(identity)
        if run.get("comparable_group") != derived:
            problems.append(
                f"{label} comparable_group {run.get('comparable_group')!r} does not match the "
                f"group derived from its comparison_identity ({derived!r}); it was copied or edited"
            )

    if isinstance(baseline_identity, Mapping) and isinstance(candidate_identity, Mapping):
        differing = [
            key
            for key in IDENTITY_KEYS
            if str(baseline_identity.get(key)) != str(candidate_identity.get(key))
        ]
        for key in differing:
            problems.append(
                f"comparison_identity.{key} differs (baseline "
                f"{baseline_identity.get(key)!r}, candidate {candidate_identity.get(key)!r})"
            )
    return problems, basis


def _operation_problems(
    baseline_ops: Mapping[str, object], candidate_ops: Mapping[str, object]
) -> list[str]:
    problems: list[str] = []
    baseline_names = set(baseline_ops)
    candidate_names = set(candidate_ops)
    if baseline_names != candidate_names:
        missing_in_candidate = sorted(baseline_names - candidate_names)
        missing_in_baseline = sorted(candidate_names - baseline_names)
        if missing_in_candidate:
            problems.append(f"operations missing in candidate: {', '.join(missing_in_candidate)}")
        if missing_in_baseline:
            problems.append(f"operations missing in baseline: {', '.join(missing_in_baseline)}")
    for name in sorted(baseline_names & candidate_names):
        baseline_entry = baseline_ops[name]
        candidate_entry = candidate_ops[name]
        if not isinstance(baseline_entry, Mapping) or not isinstance(candidate_entry, Mapping):
            problems.append(f"operation {name} has no result entry")
            continue
        baseline_unit = baseline_entry.get("unit")
        candidate_unit = candidate_entry.get("unit")
        unsupported = [
            f"{label} {unit!r}"
            for label, unit in (("baseline", baseline_unit), ("candidate", candidate_unit))
            if unit not in SUPPORTED_UNITS
        ]
        if unsupported:
            problems.append(
                f"operation {name} has a missing or unsupported unit ({', '.join(unsupported)}); "
                f"supported: {', '.join(SUPPORTED_UNITS)}"
            )
        elif baseline_unit != candidate_unit:
            problems.append(
                f"operation {name} unit differs (baseline {baseline_unit!r}, "
                f"candidate {candidate_unit!r})"
            )
    return problems


def _not_comparable_report(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    problems: list[str],
    basis: str,
) -> dict[str, object]:
    return {
        "provenance": _provenance(baseline, candidate, basis),
        "summary": {
            "total_operations": 0,
            "improved": 0,
            "neutral": 0,
            "alert_regressions": 0,
            "fail_regressions": 0,
            "overall_status": "not_comparable",
            "exit_code": EXIT_NOT_COMPARABLE,
            "verdict": "not comparable / needs fresh baseline; no regression verdict",
            "reasons": problems,
        },
        "operations": [],
    }


def _provenance(
    baseline: Mapping[str, object], candidate: Mapping[str, object], basis: str
) -> dict[str, object]:
    return {
        "baseline_run_id": baseline.get("run_id"),
        "baseline_date": baseline.get("date"),
        "baseline_results_path": baseline.get("results_path"),
        "baseline_comparable_group": baseline.get("comparable_group"),
        "baseline_identity": baseline.get("comparison_identity"),
        "baseline_software_under_test": baseline.get("software_under_test"),
        "candidate_run_id": candidate.get("run_id"),
        "candidate_date": candidate.get("date"),
        "candidate_results_path": candidate.get("results_path"),
        "candidate_comparable_group": candidate.get("comparable_group"),
        "candidate_identity": candidate.get("comparison_identity"),
        "candidate_software_under_test": candidate.get("software_under_test"),
        "comparable_group": baseline.get("comparable_group"),
        "comparison_basis": basis,
    }


def _status_for_change(percent_change: float, alert_threshold: float, fail_threshold: float) -> str:
    if percent_change <= -fail_threshold:
        return "fail"
    if percent_change <= -alert_threshold:
        return "alert"
    if percent_change > 0:
        return "improved"
    return "neutral"


def _compare_runs(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    alert_threshold: float,
    fail_threshold: float,
    allow_legacy: bool = False,
) -> dict[str, object]:
    baseline_ops = baseline["operations"]
    candidate_ops = candidate["operations"]
    if not isinstance(baseline_ops, dict) or not isinstance(candidate_ops, dict):
        raise ValueError("Invalid operation data extracted from run results")

    problems, basis = _comparability_problems(baseline, candidate, allow_legacy)
    problems.extend(_operation_problems(baseline_ops, candidate_ops))
    if problems:
        return _not_comparable_report(baseline, candidate, problems, basis)

    baseline_names = set(baseline_ops)
    operations: list[dict[str, object]] = []
    counts = {"improved": 0, "neutral": 0, "alert": 0, "fail": 0}

    for name in sorted(baseline_names):
        baseline_entry = baseline_ops[name]
        candidate_entry = candidate_ops[name]
        baseline_value = _coerce_float(baseline_entry.get("value"))
        candidate_value = _coerce_float(candidate_entry.get("value"))
        if baseline_value is None or candidate_value is None:
            raise ValueError(f"Operation {name} has a non-numeric benchmark value")
        if baseline_value == 0:
            raise ValueError(
                f"Operation {name} has a zero baseline value; cannot compute percent change"
            )

        absolute_delta = candidate_value - baseline_value
        percent_change = (absolute_delta / baseline_value) * 100.0
        status = _status_for_change(percent_change, alert_threshold, fail_threshold)
        counts[status] += 1

        operations.append(
            {
                "name": name,
                "unit": baseline_entry.get("unit") or candidate_entry.get("unit") or "ops/sec",
                "baseline": round(baseline_value, 4),
                "candidate": round(candidate_value, 4),
                "absolute_delta": round(absolute_delta, 4),
                "percent_change": round(percent_change, 2),
                "status": status,
            }
        )

    overall_status = "pass"
    exit_code = EXIT_PASS
    if counts["fail"]:
        overall_status = "fail"
        exit_code = EXIT_REGRESSION
    elif counts["alert"]:
        overall_status = "alert"
        exit_code = EXIT_REGRESSION

    return {
        "provenance": _provenance(baseline, candidate, basis),
        "thresholds": {
            "alert_regression_percent": alert_threshold,
            "fail_regression_percent": fail_threshold,
        },
        "summary": {
            "total_operations": len(operations),
            "improved": counts["improved"],
            "neutral": counts["neutral"],
            "alert_regressions": counts["alert"],
            "fail_regressions": counts["fail"],
            "overall_status": overall_status,
            "exit_code": exit_code,
        },
        "operations": operations,
    }


def _write_output(report: Mapping[str, object], output_path: str | None) -> None:
    if output_path and output_path != "-":
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _print_summary(report: Mapping[str, object]) -> None:
    provenance = report.get("provenance", {})
    summary = report.get("summary", {})
    operations = report.get("operations", [])
    if (
        not isinstance(provenance, Mapping)
        or not isinstance(summary, Mapping)
        or not isinstance(operations, list)
    ):
        return

    sys.stderr.write(
        "Compared {baseline} -> {candidate} ({group})\n".format(
            baseline=provenance.get("baseline_run_id", "unknown-baseline"),
            candidate=provenance.get("candidate_run_id", "unknown-candidate"),
            group=provenance.get("comparable_group", "unknown-group"),
        )
    )
    if summary.get("overall_status") == "not_comparable":
        sys.stderr.write("NOT COMPARABLE / needs fresh baseline; no regression verdict:\n")
        reasons = summary.get("reasons", [])
        for reason in reasons if isinstance(reasons, list) else []:
            sys.stderr.write(f"- {reason}\n")
        return
    sys.stderr.write(
        "Summary: {improved} improved, {neutral} neutral, {alert} alerts, {fail} fails\n".format(
            improved=summary.get("improved", 0),
            neutral=summary.get("neutral", 0),
            alert=summary.get("alert_regressions", 0),
            fail=summary.get("fail_regressions", 0),
        )
    )

    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        sys.stderr.write(
            "- {name}: {change:+.2f}% ({delta:+.4f} {unit}) [{status}]\n".format(
                name=operation.get("name", "unknown"),
                change=float(operation.get("percent_change", 0.0)),
                delta=float(operation.get("absolute_delta", 0.0)),
                unit=operation.get("unit", "ops/sec"),
                status=operation.get("status", "unknown"),
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two experiment runs for regressions.")
    parser.add_argument(
        "--baseline-dir", required=True, help="Path to the baseline experiment run directory"
    )
    parser.add_argument(
        "--candidate-dir", required=True, help="Path to the candidate experiment run directory"
    )
    parser.add_argument("--output", help="Write JSON report to this path instead of stdout")
    parser.add_argument(
        "--alert-threshold",
        type=float,
        default=5.0,
        help="Regression alert threshold in percent (default: 5.0)",
    )
    parser.add_argument(
        "--fail-threshold",
        type=float,
        default=10.0,
        help="Regression fail threshold in percent (default: 10.0)",
    )
    parser.add_argument(
        "--allow-legacy-group-match",
        action="store_true",
        help=(
            "Accept two historical runs that both lack comparison_identity when their "
            "operator-written comparable_group strings are equal. Never use for automated alerts."
        ),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.alert_threshold < 0 or args.fail_threshold < 0:
        sys.stderr.write("Thresholds must be non-negative\n")
        return EXIT_ERROR
    if args.fail_threshold < args.alert_threshold:
        sys.stderr.write("--fail-threshold must be greater than or equal to --alert-threshold\n")
        return EXIT_ERROR

    try:
        baseline = _load_run(Path(args.baseline_dir).resolve())
        candidate = _load_run(Path(args.candidate_dir).resolve())
        report = _compare_runs(
            baseline,
            candidate,
            args.alert_threshold,
            args.fail_threshold,
            allow_legacy=args.allow_legacy_group_match,
        )
        _write_output(report, args.output)
        _print_summary(report)
        summary = report.get("summary", {})
        if isinstance(summary, Mapping):
            return _coerce_int(summary.get("exit_code"), EXIT_ERROR)
        return EXIT_ERROR
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
