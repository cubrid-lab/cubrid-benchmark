# pyright: basic
"""Plan, verify and package the pinned Python benchmark lanes (``python/lanes.json``).

Subcommands:

    plan            print/emit the matrix cells of a lane (``representative`` by default)
    verify-install  check that the installed packages are exactly the pinned releases (or the
                    requested pycubrid source commit) and write versions.json
    package-cell    write cell.json, the manifest entry of one measured cell

A silently upgraded package, a failed source install or an unknown lane is an error (exit 1),
never a measurement.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
LANES_FILE = ROOT / "python" / "lanes.json"
DEFAULT_LANE = "representative"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
CUBRID_PATTERN = re.compile(r"\d+\.\d+")
PYTHON_PATTERN = re.compile(r"3\.\d+")


class LaneError(ValueError):
    """The lane definition, the pins or the installed environment is not valid."""


def load_lanes(path: Path = LANES_FILE) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def cell_id(lane: str, python: str, cubrid: str) -> str:
    return f"{lane}-py{python}-cubrid{cubrid}"


def plan(lane: str, lanes: dict[str, object]) -> list[dict[str, str]]:
    lane = lane or DEFAULT_LANE
    definitions = lanes.get("lanes")
    if not isinstance(definitions, dict) or lane not in definitions:
        raise LaneError(f"unknown lane {lane!r}")
    definition = definitions[lane]
    constraints = definition.get("constraints")
    if not isinstance(constraints, str) or not (ROOT / constraints).is_file():
        raise LaneError(f"lane {lane!r} has no constraints file {constraints!r}")
    cells: list[dict[str, str]] = []
    seen: set[str] = set()
    for cell in definition.get("cells", []):
        python, cubrid = str(cell.get("python", "")), str(cell.get("cubrid", ""))
        if not PYTHON_PATTERN.fullmatch(python) or not CUBRID_PATTERN.fullmatch(cubrid):
            raise LaneError(f"lane {lane!r} has an invalid cell {cell!r}")
        identifier = cell_id(lane, python, cubrid)
        if identifier in seen:
            raise LaneError(f"lane {lane!r} repeats cell {identifier}")
        seen.add(identifier)
        cells.append(
            {
                "lane": lane,
                "id": identifier,
                "python": python,
                "cubrid": cubrid,
                "image": f"cubrid/cubrid:{cubrid}",
                "constraints": constraints,
            }
        )
    if not cells:
        raise LaneError(f"lane {lane!r} has no cells")
    return cells


def read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        requirement = Requirement(line)
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != "==":
            raise LaneError(f"{path}: {line!r} is not an exact '==' pin")
        pins[requirement.name.lower()] = specifiers[0].version
    if not pins:
        raise LaneError(f"{path} pins nothing")
    return pins


def _direct_url(distribution: importlib.metadata.Distribution) -> dict[str, object] | None:
    text = distribution.read_text("direct_url.json")
    return json.loads(text) if text else None


def verify_install(pins: dict[str, str], source_sha: str | None) -> dict[str, object]:
    """Return the installed versions, raising ``LaneError`` on any mismatch."""
    problems: list[str] = []
    packages: dict[str, dict[str, object]] = {}
    if source_sha is not None and not SHA_PATTERN.fullmatch(source_sha):
        raise LaneError(f"pycubrid source must be a full 40-hex commit SHA, got {source_sha!r}")

    for name, pinned in sorted(pins.items()):
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"{name} is not installed (pinned {pinned})")
            continue
        installed = distribution.version
        direct = _direct_url(distribution)
        entry: dict[str, object] = {"version": installed, "pinned": pinned, "source": "index"}
        if name == "pycubrid" and source_sha is not None:
            vcs = (direct or {}).get("vcs_info", {})
            commit = vcs.get("commit_id") if isinstance(vcs, dict) else None
            entry.update({"source": "vcs", "commit_id": commit, "pinned": f"commit {source_sha}"})
            if commit != source_sha:
                problems.append(f"pycubrid source commit is {commit!r}, expected {source_sha}")
        else:
            if direct is not None:
                problems.append(f"{name} was installed from {direct.get('url')}, not the index")
            if Version(installed) != Version(pinned):
                problems.append(f"{name} {installed} is installed, pinned {pinned}")
        packages[name] = entry

    if "pycubrid" in packages:
        module = importlib.import_module("pycubrid")
        dunder = getattr(module, "__version__", None)
        packages["pycubrid"]["__version__"] = dunder
        if source_sha is None and dunder != packages["pycubrid"]["version"]:
            problems.append(
                f"pycubrid.__version__ {dunder!r} != metadata {packages['pycubrid']['version']!r}"
            )

    # The dialect's own SQLAlchemy requirement must accept the resolved SQLAlchemy.
    if "sqlalchemy-cubrid" in packages:
        resolved = importlib.metadata.version("sqlalchemy")
        for requirement_text in importlib.metadata.requires("sqlalchemy-cubrid") or []:
            requirement = Requirement(requirement_text)
            if requirement.name.lower() == "sqlalchemy" and requirement.marker is None:
                if not requirement.specifier.contains(resolved, prereleases=True):
                    problems.append(
                        f"SQLAlchemy {resolved} does not satisfy sqlalchemy-cubrid's "
                        f"{requirement.specifier}"
                    )
        packages.setdefault("sqlalchemy", {"version": resolved, "source": "index"})

    if problems:
        raise LaneError("; ".join(problems))
    return {
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "packages": packages,
    }


def package_cell(args: argparse.Namespace) -> dict[str, object]:
    out = Path(args.dir)
    versions = json.loads((out / "versions.json").read_text(encoding="utf-8"))
    pycubrid = versions["packages"]["pycubrid"]
    driver_version = (
        f"{pycubrid['version']}+{pycubrid['commit_id'][:12]}"
        if pycubrid.get("source") == "vcs"
        else pycubrid["version"]
    )
    result = out / args.result
    if not result.is_file():
        raise LaneError(f"measured result {result} is missing")
    return {
        "scenario": args.scenario,
        "lane": args.lane,
        "cell_id": cell_id(args.lane, args.python, args.cubrid),
        "python": args.python,
        "cubrid": args.cubrid,
        "driver": "pycubrid",
        "driver_version": driver_version,
        "packages": versions["packages"],
        "result_file": args.result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--lane", default="")
    plan_parser.add_argument("--github-output")

    verify = sub.add_parser("verify-install")
    verify.add_argument("--constraints", required=True)
    verify.add_argument("--source-sha", default="")
    verify.add_argument("--output", required=True)

    cell = sub.add_parser("package-cell")
    cell.add_argument("--dir", required=True)
    cell.add_argument("--lane", required=True)
    cell.add_argument("--python", required=True)
    cell.add_argument("--cubrid", required=True)
    cell.add_argument("--scenario", default="python-tier1")
    cell.add_argument("--result", default="python_tier1_pycubrid.json")

    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            cells = plan(args.lane, load_lanes())
            payload = json.dumps(cells, separators=(",", ":"))
            print(payload)
            if args.github_output:
                with open(args.github_output, "a", encoding="utf-8") as handle:
                    handle.write(f"lane={cells[0]['lane']}\n")
                    handle.write(f"cells={payload}\n")
                    handle.write(f"expect={','.join(cell['id'] for cell in cells)}\n")
        elif args.command == "verify-install":
            versions = verify_install(read_pins(ROOT / args.constraints), args.source_sha or None)
            Path(args.output).write_text(json.dumps(versions, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(versions["packages"], indent=2))
        else:
            out = Path(args.dir) / "cell.json"
            out.write_text(json.dumps(package_cell(args), indent=2) + "\n", encoding="utf-8")
            print(out)
    except (LaneError, OSError, KeyError) as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
