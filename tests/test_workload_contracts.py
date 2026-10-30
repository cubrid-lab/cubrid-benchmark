"""Scenario contracts, truthful protocol names and offline smoke runs of the Python workloads."""

from __future__ import annotations

import importlib.util
import inspect
import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON_DIR = ROOT / "python"
DOC = (ROOT / "docs" / "WORKLOADS.md").read_text(encoding="utf-8")
REQUIRED_FIELDS = {"call", "rows_per_call", "timed", "setup", "commit_in_timed_region"}
REQUIRED_FIELDS |= {"verification"}


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", PYTHON_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # SQLAlchemy resolves Mapped[...] through sys.modules
    spec.loader.exec_module(module)
    return module


TIER1 = _load("bench_pycubrid")
EXTENDED = _load("bench_pycubrid_extended")
TIER2 = _load("bench_sqlalchemy_cubrid")
MODULES = [TIER1, EXTENDED, TIER2]


def _scenarios_from_tests(module: Any) -> set[str]:
    names: set[str] = set()
    for name, function in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("test_bench_"):
            continue
        marks = {mark.name: mark for mark in getattr(function, "pytestmark", [])}
        if "skip" in marks:
            continue
        base = name.removeprefix("test_bench_")
        if "parametrize" in marks:
            for value in marks["parametrize"].args[1]:
                names.add(f"{base}_{value}")
        else:
            names.add(base)
    return names


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.WORKLOAD_REVISION)
def test_every_benchmark_has_a_complete_contract(module: Any) -> None:
    assert _scenarios_from_tests(module) == set(module.SCENARIOS)
    for name, contract in module.SCENARIOS.items():
        assert REQUIRED_FIELDS <= set(contract), name
        assert isinstance(contract["rows_per_call"], int) and contract["rows_per_call"] > 0


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.WORKLOAD_REVISION)
def test_contract_page_lists_every_scenario(module: Any) -> None:
    assert f"`{module.WORKLOAD_REVISION}`" in DOC
    for name in module.SCENARIOS:
        assert f"`{name}`" in DOC, name


def test_only_a_verified_prepare_path_is_called_prepared() -> None:
    for module in MODULES:
        for name, contract in module.SCENARIOS.items():
            protocol = contract.get("protocol") or {}
            if "prepared" in name:
                assert protocol.get("PreparePacket") == 1, name
                assert protocol.get("ExecuteArrayPacket") == 1, name
    assert "prepared_statement" not in EXTENDED.SCENARIOS
    assert EXTENDED.SCENARIOS["execute_parameterized_loop"]["protocol"] == {
        "PrepareAndExecutePacket": EXTENDED.ROWS
    }


def test_new_series_ids() -> None:
    assert EXTENDED.WORKLOAD_REVISION == "tier1-extended-v2"
    assert TIER2.WORKLOAD_REVISION == "tier2-sqlalchemy-v2"


class PycubridLikeConnection:
    """Mimics the private request hook the probe wraps."""

    __module__ = "pycubrid.connection"

    def __init__(self, packets: list[str]) -> None:
        self.packets = packets

    def _send_and_receive_locked(self, packet: object, **kwargs: object) -> object:
        return packet


def _packet(name: str) -> object:
    return type(name, (), {})()


class TestProtocolProof:
    def test_probe_counts_packets_and_restores_the_hook(self) -> None:
        conn = PycubridLikeConnection([])
        with EXTENDED.packet_probe(conn) as counts:
            for name in ("PreparePacket", "ExecuteArrayPacket", "CommitPacket"):
                conn._send_and_receive_locked(_packet(name))
        assert counts == Counter(PreparePacket=1, ExecuteArrayPacket=1, CommitPacket=1)
        assert "_send_and_receive_locked" not in vars(conn)

    def test_claimed_path_passes(self) -> None:
        counts = Counter(PreparePacket=1, ExecuteArrayPacket=1, CommitPacket=1)

        evidence = EXTENDED.check_protocol("executemany_prepared_batch", counts, object())

        assert evidence["protocol"] == "verified"

    @pytest.mark.parametrize(
        ("scenario", "counts"),
        [
            ("executemany_prepared_batch", Counter(BatchExecutePacket=1)),
            ("executemany_literal_batch", Counter(PreparePacket=1, ExecuteArrayPacket=1)),
            ("executemany_literal_batch", Counter(PrepareAndExecutePacket=1000)),
            ("execute_parameterized_loop", Counter(PrepareAndExecutePacket=999)),
        ],
    )
    def test_a_different_path_fails(self, scenario: str, counts: Counter[str]) -> None:
        with pytest.raises(EXTENDED.WorkloadVerificationError, match="expected"):
            EXTENDED.check_protocol(scenario, counts, object())

    def test_unobservable_pycubrid_fails(self) -> None:
        conn = PycubridLikeConnection([])

        with pytest.raises(EXTENDED.WorkloadVerificationError, match="cannot observe"):
            EXTENDED.check_protocol("executemany_literal_batch", None, conn)

    def test_unclaimed_scenario_needs_no_proof(self) -> None:
        assert EXTENDED.check_protocol("connect_query_close", None, object()) == {
            "protocol": "not claimed"
        }


class TestCountedRows:
    def test_missing_row_fails(self) -> None:
        rows = EXTENDED._rows("lookup")
        rows[10] = None

        with pytest.raises(EXTENDED.WorkloadVerificationError, match="1 missing"):
            EXTENDED._expect_rows(rows, "lookup", "test")

    def test_wrong_payload_fails(self) -> None:
        rows = EXTENDED._rows("lookup")
        rows[0] = (1, "other_00001", 1)

        with pytest.raises(EXTENDED.WorkloadVerificationError):
            EXTENDED._expect_rows(rows, "lookup", "test")


FAKE_PYCUBRID = """
import os
import sqlite3


def connect(**kwargs):
    return sqlite3.connect(os.environ["FAKE_CUBRID_DB"], check_same_thread=False)
"""
FAKE_PYMYSQL = 'raise RuntimeError("PyMySQL imported")\n'


def test_offline_smoke_of_all_maintained_workloads(tmp_path: Path) -> None:
    """Run the real modules on SQLite: every scenario's setup, probe and verification pass."""
    for module in ("conftest.py", "bench_pycubrid.py", "bench_pycubrid_extended.py"):
        shutil.copy(PYTHON_DIR / module, tmp_path / module)
    shutil.copy(PYTHON_DIR / "bench_sqlalchemy_cubrid.py", tmp_path / "bench_sqlalchemy_cubrid.py")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "pycubrid.py").write_text(FAKE_PYCUBRID, encoding="utf-8")
    (stubs / "pymysql.py").write_text(FAKE_PYMYSQL, encoding="utf-8")
    database = tmp_path / "bench.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE bench_users (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(100), "
            "email VARCHAR(200), age INT, created_at DATETIME)"
        )
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(stubs),
        "FAKE_CUBRID_DB": str(database),
        "CUBRID_ORM_DSN": f"sqlite:///{database}",
    }
    out = tmp_path / "results.json"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--benchmark-disable"]
        + ["bench_pycubrid.py", "bench_pycubrid_extended.py", "bench_sqlalchemy_cubrid.py"]
        + [f"--benchmark-json={out}"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    summary = result.stdout.strip().splitlines()[-1]
    # Every maintained workload must run end to end, so a renamed, dropped or newly
    # failing benchmark changes these counts. 14 pass: the 5 pycubrid driver
    # workloads (Tier 1), 4 of the 5 extended pycubrid workloads, and 5 SQLAlchemy
    # runs (Tier 2; the ORM bulk insert runs at 100 and 1000 rows). 2 skip on
    # purpose: executemany(prepared=True) is opt-in via BENCH_EXECUTEMANY_PREPARED,
    # and the relationship/join loading benchmark waits for a schema with foreign keys.
    assert "14 passed" in summary and "2 skipped" in summary, summary
    assert not out.exists() or json.loads(out.read_text()).get("benchmarks") == []


def test_scenario_manifests_point_at_existing_benchmarks() -> None:
    modules = {"python/bench_sqlalchemy_cubrid.py": TIER2, "python/bench_pycubrid.py": TIER1}
    modules["python/bench_pycubrid_extended.py"] = EXTENDED
    checked = 0
    for manifest in sorted((ROOT / "scenarios").rglob("*.yaml")):
        for line in manifest.read_text(encoding="utf-8").splitlines():
            worker = line.strip().removeprefix("worker:").strip()
            path, _, node = worker.partition("::")
            if path not in modules:
                continue
            function = node.split("[", 1)[0]
            assert hasattr(modules[path], function), f"{manifest.name}: {worker}"
            checked += 1
    assert checked >= 5
