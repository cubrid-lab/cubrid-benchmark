from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import cast


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a version-matrix manifest from packaged benchmark result files."
    )
    _ = parser.add_argument(
        "--results-dir",
        required=True,
        type=Path,
        help="Directory containing packaged matrix benchmark JSON files.",
    )
    _ = parser.add_argument(
        "--glob",
        default="*.json",
        help="Pattern for packaged result files under --results-dir (e.g. '*/cell.json').",
    )
    _ = parser.add_argument(
        "--expect-cell",
        action="append",
        default=[],
        help="A cell_id that must be present; a missing cell fails the manifest.",
    )
    return parser.parse_args(argv)


def _iter_result_files(results_dir: Path, pattern: str = "*.json") -> Iterable[Path]:
    for path in sorted(results_dir.glob(pattern)):
        if path.name == "manifest.json":
            continue
        yield path


def _read_result_file(result_file: Path) -> Mapping[str, object]:
    raw_payload = cast(object, json.loads(result_file.read_text(encoding="utf-8")))
    if not isinstance(raw_payload, Mapping):
        raise ValueError(f"Expected object payload in {result_file}")
    return cast(Mapping[str, object], raw_payload)


def _require_string(payload: Mapping[str, object], key: str, result_file: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing '{key}' in {result_file}")
    return value


def build_manifest(
    results_dir: Path, pattern: str = "*.json", expected_cells: Iterable[str] = ()
) -> dict[str, object]:
    entries: list[dict[str, str]] = []
    relative_base = results_dir.parent

    for result_file in _iter_result_files(results_dir, pattern):
        payload = _read_result_file(result_file)
        entry = {
            "scenario": _require_string(payload, "scenario", result_file),
            "python": _require_string(payload, "python", result_file),
            "driver": _require_string(payload, "driver", result_file),
            "driver_version": _require_string(payload, "driver_version", result_file),
            "result_file": result_file.relative_to(relative_base).as_posix(),
        }
        for optional in ("cell_id", "lane", "cubrid"):
            value = payload.get(optional)
            if isinstance(value, str) and value:
                entry[optional] = value
        entries.append(entry)

    entries.sort(
        key=lambda entry: (
            entry["scenario"],
            entry["python"],
            entry["driver"],
            entry["driver_version"],
            entry["result_file"],
        )
    )
    expected = set(expected_cells)
    if not entries and not expected:
        raise ValueError(f"No result JSON files found in {results_dir}")
    if expected:
        present = [entry.get("cell_id", "") for entry in entries]
        duplicated = sorted({cell for cell in present if present.count(cell) > 1})
        missing = sorted(expected - set(present))
        unexpected = sorted(set(present) - expected)
        if missing or unexpected or duplicated:
            raise ValueError(
                "Manifest is incomplete: "
                f"missing cells {missing or 'none'}, unexpected cells {unexpected or 'none'}, "
                f"duplicated cells {duplicated or 'none'} (a failed, cancelled or skipped cell "
                "has no result)"
            )

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "matrix": entries,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    results_dir = cast(Path, args.results_dir).resolve()

    if not results_dir.is_dir():
        raise SystemExit(f"Results directory does not exist: {results_dir}")

    try:
        manifest = build_manifest(results_dir, args.glob, args.expect_cell)
    except ValueError as exc:
        print(f"::error::{exc}")
        return 1
    manifest_path = results_dir / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    _ = sys.exit(main())
