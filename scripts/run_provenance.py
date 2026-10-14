# pyright: basic
"""Capture measured run provenance and derive a comparison identity.

A run's ``comparison_identity`` holds the fields that must be equal for two runs to be compared
directly: the measurement host, operating system, Docker, the CUBRID image and build, the Python
runtime and the workload definition. ``software_under_test`` holds what a regression comparison
is expected to change (driver versions, benchmark source SHA); it is recorded but never part of
the identity. ``comparable_group`` is derived from the identity, so it cannot be copied from
another run without also matching that run's identity.

Subcommands:

    capture          measure this host and write <out-dir>/run.yaml (+ raw/<results>)
    select-baseline  find the newest compatible baseline run for a candidate run directory

Neither subcommand reads environment identity from another run.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_runs import (  # noqa: E402
    IDENTITY_KEYS,
    UNKNOWN,
    _parse_run_yaml,
    derive_comparable_group,
)

DRIVER_PACKAGES = ("pycubrid", "sqlalchemy-cubrid", "SQLAlchemy", "pytest-benchmark")


def _run(*command: str) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or UNKNOWN


def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system() or UNKNOWN


def _host_class(env: Mapping[str, str], fixed_host: str | None) -> str:
    """Name the measurement host class without borrowing it from any other run.

    GitHub-hosted runners are a pool: the runner image and its version identify the class,
    never the per-job machine name. A fixed benchmark machine is named by the operator.
    """
    if fixed_host:
        return f"fixed:{fixed_host}"
    if env.get("GITHUB_ACTIONS") == "true":
        parts = [env.get("RUNNER_ENVIRONMENT"), env.get("ImageOS"), env.get("ImageVersion")]
        # A partly measured runner class is not an identity: two such hosts must not match.
        if not all(parts):
            return UNKNOWN
        return ":".join(str(part) for part in parts)
    node = platform.node()
    return f"local:{node}" if node else UNKNOWN


def _docker_version() -> str:
    return _run("docker", "version", "--format", "{{.Server.Version}}") or UNKNOWN


def _image_digest(image: str) -> str:
    if not image:
        return UNKNOWN
    digest = _run("docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image)
    return digest.split("@", 1)[1] if "@" in digest else UNKNOWN


def _cubrid_build(host: str, port: int, database: str) -> str:
    try:
        import pycubrid
    except ImportError:
        return UNKNOWN
    # Any failure (connect, query or close) leaves the field unknown, which blocks comparison.
    try:
        conn = pycubrid.connect(host=host, port=port, database=database, user="dba", password="")
    except Exception:  # noqa: BLE001
        return UNKNOWN
    build = UNKNOWN
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT version()")
        row = cursor.fetchone()
        if row and row[0]:
            build = str(row[0])
    except Exception:  # noqa: BLE001
        build = UNKNOWN
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    return build


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in DRIVER_PACKAGES:
        try:
            versions[name.lower()] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _source_sha(env: Mapping[str, str]) -> str:
    return env.get("GITHUB_SHA") or _run("git", "rev-parse", "HEAD") or UNKNOWN


def _source_dirty() -> str:
    status = _run("git", "status", "--porcelain", "--untracked-files=no")
    return "true" if status else "false"


def capture_identity(args: argparse.Namespace, env: Mapping[str, str]) -> dict[str, object]:
    return {
        "host_class": _host_class(env, args.fixed_host),
        "cpu": _cpu_model(),
        "cores": os.cpu_count() or UNKNOWN,
        "os": _os_name(),
        "kernel": platform.release() or UNKNOWN,
        "docker": _docker_version(),
        "cubrid_image": args.cubrid_image,
        "cubrid_image_digest": _image_digest(args.cubrid_image),
        "cubrid_build": _cubrid_build(args.cubrid_host, args.cubrid_port, args.cubrid_db),
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "scenario": args.scenario,
        "workload_revision": args.workload_revision,
        "schema": args.schema,
        "data_count": args.data_count,
        "transaction_policy": args.transaction_policy,
        "measurement_protocol": args.measurement_protocol,
    }


def _yaml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value is None:
        return "null"
    return json.dumps(str(value))


def render_run_yaml(
    *,
    run_id: str,
    date: str,
    label: str,
    role: str,
    identity: Mapping[str, object],
    software: Mapping[str, object],
    notes: str,
) -> str:
    lines = [
        f"run_id: {_yaml_value(run_id)}",
        f"date: {_yaml_value(date)}",
        f"label: {_yaml_value(label)}",
        f"role: {role}",
        "compares_to: null",
        f"comparable_group: {_yaml_value(derive_comparable_group(identity))}",
        "",
        "# Measured on this run's host; never copied from a baseline.",
        "comparison_identity:",
    ]
    lines.extend(f"  {key}: {_yaml_value(identity[key])}" for key in IDENTITY_KEYS)
    lines.append("")
    lines.append("# Expected to differ between candidate and baseline; not part of the identity.")
    lines.append("software_under_test:")
    for key, value in software.items():
        if isinstance(value, Mapping):
            lines.append(f"  {key}:")
            lines.extend(f"    {inner}: {_yaml_value(v)}" for inner, v in value.items())
        else:
            lines.append(f"  {key}: {_yaml_value(value)}")
    lines.extend(["", "artifacts:", "  raw_dir: raw/", "", f"notes: {_yaml_value(notes)}", ""])
    return "\n".join(lines)


def select_module_results(payload: object, module: str) -> dict[str, object]:
    """Keep only the pytest-benchmark entries collected from ``module`` (a file name).

    One ``pytest`` invocation over several benchmark modules reuses test names (for example
    ``bench_pycubrid.py`` and ``bench_pymysql.py``); the candidate must hold one module only.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("benchmarks"), list):
        raise ValueError("--results-module needs a pytest-benchmark JSON file")
    kept = [
        entry
        for entry in payload["benchmarks"]
        if isinstance(entry, dict)
        and Path(str(entry.get("fullname", "")).split("::", 1)[0]).name == module
    ]
    if not kept:
        raise ValueError(f"no benchmarks from {module} in the results file")
    selected = dict(payload)
    selected["benchmarks"] = kept
    return selected


def _cmd_capture(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    out_dir = Path(args.out_dir)
    if (out_dir / "run.yaml").exists():
        sys.stderr.write(f"ERROR: {out_dir / 'run.yaml'} already exists; runs are immutable\n")
        return 2
    identity = capture_identity(args, env)
    unknown = [key for key in IDENTITY_KEYS if identity[key] in ("", UNKNOWN)]
    software: dict[str, object] = {
        "benchmark_source_sha": _source_sha(env),
        "benchmark_source_dirty": _source_dirty(),
        "packages": _package_versions(),
    }
    if args.versions_file:
        # Verified by python_lanes.py verify-install: a source build records its exact commit,
        # so two commits that report the same version stay distinguishable.
        verified = json.loads(Path(args.versions_file).read_text(encoding="utf-8"))
        for name, info in sorted(verified.get("packages", {}).items()):
            if isinstance(info, dict) and info.get("source") == "vcs":
                software[f"{name}_source_commit"] = info.get("commit_id")
        software["verified_versions_file"] = Path(args.versions_file).name
    if env.get("GITHUB_RUN_ID"):
        software["github_run"] = (
            f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{env['GITHUB_RUN_ID']}"
        )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    run_id = f"{now.date().isoformat()}_{args.label}"
    notes = args.notes
    if unknown:
        notes = (notes + " " if notes else "") + f"unmeasured identity fields: {', '.join(unknown)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.results:
        raw_dir = out_dir / "raw"
        raw_dir.mkdir(exist_ok=True)
        if args.results_module:
            payload = select_module_results(
                json.loads(Path(args.results).read_text(encoding="utf-8")), args.results_module
            )
            (raw_dir / "results.json").write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
        else:
            shutil.copyfile(args.results, raw_dir / "results.json")
    (out_dir / "run.yaml").write_text(
        render_run_yaml(
            run_id=run_id,
            date=now.isoformat().replace("+00:00", "Z"),
            label=args.label,
            role=args.role,
            identity=identity,
            software=software,
            notes=notes,
        ),
        encoding="utf-8",
    )
    sys.stdout.write(str(out_dir / "run.yaml") + "\n")
    return 0


def select_baseline(candidate_dir: Path, search_roots: list[Path]) -> tuple[Path | None, str]:
    """Return the newest compatible ``role: baseline`` run, or ``None`` with the reason."""
    candidate = _parse_run_yaml(candidate_dir / "run.yaml")
    identity = candidate.get("comparison_identity")
    if not isinstance(identity, Mapping):
        return None, "candidate run.yaml has no comparison_identity"
    group = candidate.get("comparable_group")
    matches: list[tuple[str, Path]] = []
    for root in search_roots:
        for run_yaml in sorted(root.glob("runs/*/run.yaml")):
            meta = _parse_run_yaml(run_yaml)
            if meta.get("role") != "baseline":
                continue
            if meta.get("comparable_group") != group:
                continue
            if meta.get("comparison_identity") != identity:
                continue
            matches.append((str(meta.get("date", "")), run_yaml.parent))
    if not matches:
        return None, (
            f"not comparable / needs fresh baseline: no role=baseline run with comparable_group "
            f"{group!r} and an identical comparison_identity under "
            f"{', '.join(str(root) for root in search_roots)}"
        )
    matches.sort()
    return matches[-1][1], "compatible baseline found"


def _cmd_select(args: argparse.Namespace) -> int:
    baseline, reason = select_baseline(Path(args.candidate_dir), [Path(p) for p in args.search])
    sys.stderr.write(reason + "\n")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"baseline_dir={baseline or ''}\n")
            handle.write(f"reason={reason}\n")
    if baseline is not None:
        sys.stdout.write(str(baseline) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="Measure this host and write run.yaml")
    capture.add_argument("--out-dir", required=True)
    capture.add_argument("--label", required=True)
    capture.add_argument("--role", choices=["baseline", "candidate"], default="candidate")
    capture.add_argument("--results", help="Raw result JSON to copy to raw/results.json")
    capture.add_argument(
        "--results-module",
        help="Keep only pytest-benchmark entries from this module file (e.g. bench_pycubrid.py)",
    )
    capture.add_argument("--scenario", required=True)
    capture.add_argument("--workload-revision", required=True)
    capture.add_argument("--schema", required=True)
    capture.add_argument("--data-count", type=int, required=True)
    capture.add_argument("--transaction-policy", required=True)
    capture.add_argument("--measurement-protocol", required=True)
    capture.add_argument("--cubrid-image", required=True)
    capture.add_argument("--cubrid-host", default="localhost")
    capture.add_argument("--cubrid-port", type=int, default=33000)
    capture.add_argument("--cubrid-db", default="benchdb")
    capture.add_argument(
        "--fixed-host", help="Name of a fixed benchmark machine (otherwise detected)"
    )
    capture.add_argument("--notes", default="")
    capture.add_argument(
        "--versions-file",
        help="versions.json from python_lanes.py verify-install (records source commits)",
    )
    capture.set_defaults(func=_cmd_capture)

    select = sub.add_parser("select-baseline", help="Find a compatible baseline run")
    select.add_argument("--candidate-dir", required=True)
    select.add_argument(
        "--search", action="append", required=True, help="Experiment directory to search"
    )
    select.add_argument("--github-output", help="Append baseline_dir=/reason= to this file")
    select.set_defaults(func=_cmd_select)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
