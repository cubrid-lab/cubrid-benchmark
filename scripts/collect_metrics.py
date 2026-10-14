"""Run one benchmark command and record the peak RSS of that measured child process.

What is measured: ``resource.getrusage(RUSAGE_CHILDREN).ru_maxrss`` after the command exits is
the high-water RSS over all waited-for children of this wrapper. It equals the measured child's
own peak RSS only when this wrapper has had no earlier child (the value before the command is
0). That is the supported, one-child-per-fresh-wrapper case (the ``Makefile`` starts a new
wrapper for each command).

If an earlier child already left a high-water mark, the measured child's peak cannot be
recovered from it (a smaller child would show no change). ``child_peak_rss_kb`` is then
``null`` and ``child_peak_rss_unavailable`` says why, instead of a misleading number. This is
peak resident memory of the child process, not allocated bytes, not in-process growth, not the
Python heap (``tracemalloc``) and not the database server's memory.

Units: Linux reports ``ru_maxrss`` in KiB and macOS in bytes (converted to KiB here). On other
platforms the unit is unknown and the value is reported as unavailable.

Exit status: the command's own non-zero status is returned unchanged. When the command succeeds
but does not write its result file (any earlier file is removed before the command starts), or
writes one that is not valid UTF-8 JSON, the exit status is 2 and ``result_status``
records which.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
from pathlib import Path

EXIT_RESULT_MISSING = 2

# ru_maxrss unit -> divisor to KiB
RU_MAXRSS_UNITS = {"linux": ("KiB", 1), "darwin": ("bytes", 1024)}


def _read_result(path: Path) -> tuple[str, object]:
    if not path.exists():
        return "missing", None
    try:
        return "ok", json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        return "invalid_json", str(exc)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run benchmark command and collect the measured child's peak RSS.",
    )
    parser.add_argument(
        "--result-json",
        required=True,
        help="Path to benchmark result JSON produced by command.",
    )
    parser.add_argument(
        "--metrics-json",
        default=None,
        help="Path to metrics output JSON. Defaults to <result>.metrics.json.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to execute (prefix with '--').",
    )
    return parser.parse_args(argv)


def _normalize_command(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValueError("command is required after '--'")
    return command


def memory_metrics(before: int, after: int, platform: str) -> dict[str, object]:
    """Interpret two ``RUSAGE_CHILDREN.ru_maxrss`` readings around one measured child."""
    unit, divisor = RU_MAXRSS_UNITS.get(platform, ("unknown", 0))
    memory: dict[str, object] = {
        "platform": platform,
        "ru_maxrss_unit": unit,
        "measured_children": 1,
        "rusage_children_before_raw": before,
        "rusage_children_after_raw": after,
        # Compatibility fields (deprecated). The delta equals the child's peak only when the
        # before value is 0; read child_peak_rss_kb instead.
        "rusage_children_before_kb": before // divisor if divisor else None,
        "rusage_children_after_kb": after // divisor if divisor else None,
        "peak_rss_delta_kb": max(after - before, 0) // divisor if divisor else None,
        "child_peak_rss_kb": None,
    }
    if not divisor:
        memory["child_peak_rss_unavailable"] = f"ru_maxrss unit is unknown on {platform!r}"
    elif before != 0:
        memory["child_peak_rss_unavailable"] = (
            "this wrapper already had a child with a high-water RSS of "
            f"{before // divisor} KiB; the measured child's own peak cannot be derived. Run "
            "each measured command in a fresh collect_metrics.py process."
        )
    else:
        memory["child_peak_rss_kb"] = after // divisor
    return memory


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    command = _normalize_command(args.command)

    result_path = Path(args.result_json)
    metrics_path = (
        Path(args.metrics_json) if args.metrics_json else result_path.with_suffix(".metrics.json")
    )

    # The command must write the result now: a stale file from an earlier run is not its output.
    result_path.unlink(missing_ok=True)
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    completed = subprocess.run(command, check=False)
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)

    memory = memory_metrics(int(usage_before.ru_maxrss), int(usage_after.ru_maxrss), sys.platform)
    if "child_peak_rss_unavailable" in memory:
        sys.stderr.write(
            f"WARNING: child_peak_rss_kb unavailable: {memory['child_peak_rss_unavailable']}\n"
        )

    result_status, result_payload = _read_result(result_path)
    artifact_payload = {
        "result_file": str(result_path),
        "result_status": result_status,
        "result": result_payload if result_status == "ok" else {},
        "metrics": {
            "command": command,
            "returncode": completed.returncode,
            "memory": memory,
        },
    }
    if result_status == "invalid_json":
        artifact_payload["result_error"] = result_payload
    _write_json(metrics_path, artifact_payload)

    if completed.returncode != 0:
        return completed.returncode
    if result_status != "ok":
        sys.stderr.write(f"ERROR: command succeeded but its result file is {result_status}\n")
        return EXIT_RESULT_MISSING
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
