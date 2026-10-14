"""Offline contract tests for the Python Tier 1 workload in ``python/bench_pycubrid.py``.

SQLite stands in for CUBRID: the workload SQL (explicit integer keys, ``?`` placeholders,
``DROP TABLE IF EXISTS``) runs unchanged on both, so these tests exercise the real measured
functions, the untimed setup/teardown and the real ``pytest-benchmark`` pedantic loop.
The CUBRID-specific ``AUTO_INCREMENT`` behaviour is shown by the live proof recorded in
``experiments/tier1-python-workload``.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

_BENCH_PATH = Path(__file__).resolve().parent.parent / "python" / "bench_pycubrid.py"
_spec = importlib.util.spec_from_file_location("bench_pycubrid_under_test", _BENCH_PATH)
assert _spec is not None and _spec.loader is not None
bench: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)

OPERATIONS = sorted(bench.WORKLOADS)


class CountingConnection:
    """Wrap a sqlite3 connection and record the statements every cursor executes."""

    def __init__(self) -> None:
        self.raw = sqlite3.connect(":memory:")
        self.statements: list[str] = []

    def cursor(self) -> CountingCursor:
        return CountingCursor(self.raw.cursor(), self.statements)

    def commit(self) -> None:
        self.raw.commit()


class CountingCursor:
    def __init__(self, raw: sqlite3.Cursor, statements: list[str]) -> None:
        self.raw = raw
        self.statements = statements

    @property
    def rowcount(self) -> int:
        return self.raw.rowcount

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> object:
        self.statements.append(sql)
        return self.raw.execute(sql, params or ())

    def fetchone(self) -> tuple[object, ...] | None:
        return self.raw.fetchone()

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.raw.fetchall()

    def close(self) -> None:
        self.raw.close()


@pytest.fixture
def conn() -> Generator[CountingConnection, None, None]:
    connection = CountingConnection()
    try:
        yield connection
    finally:
        connection.raw.close()


@pytest.fixture
def cursor(conn: CountingConnection) -> Generator[CountingCursor, None, None]:
    cur = conn.cursor()
    bench._reset_table(cur, conn)
    yield cur


def _summary(conn: CountingConnection) -> tuple[object, ...]:
    return conn.raw.execute(bench.SUMMARY_SQL).fetchone()


def test_table_uses_explicit_keys_without_auto_increment() -> None:
    assert "AUTO_INCREMENT" not in bench.TABLE_SQL.upper()
    assert bench.INSERT_SQL.startswith("INSERT INTO bench_driver_pycubrid (id, name, amount)")


@pytest.mark.parametrize("operation", OPERATIONS)
def test_first_round_and_warmup_rounds_verify(
    benchmark: Any, cursor: CountingCursor, conn: CountingConnection, operation: str
) -> None:
    bench.run_workload(benchmark, cursor, conn, operation)

    stats = benchmark.stats.stats
    assert stats.rounds == bench.ROUNDS
    # Every round (warm-up included) re-seeds with explicit keys 1..ROW_COUNT.
    rounds_run = bench.ROUNDS + bench.WARMUP_ROUNDS
    inserts = sum(1 for sql in conn.statements if sql == bench.INSERT_SQL)
    measured_inserts = bench.ROW_COUNT if operation == "insert_sequential" else 0
    seeded = 0 if operation == "insert_sequential" else bench.ROW_COUNT
    assert inserts == rounds_run * (seeded + measured_inserts)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_back_to_back_runs_start_from_equivalent_state(
    benchmark: Any, cursor: CountingCursor, conn: CountingConnection, operation: str
) -> None:
    bench.run_workload(benchmark, cursor, conn, operation)
    first = _summary(conn)

    # A second full run on the same table (no DROP/CREATE in between) must verify again.
    benchmark.disabled = True
    bench.run_workload(benchmark, cursor, conn, operation)

    assert _summary(conn) == first


@pytest.mark.parametrize(
    ("operation", "wrong_keys"),
    [
        ("select_by_pk", range(2, bench.ROW_COUNT + 2)),
        ("update_indexed", range(bench.ROW_COUNT + 1, bench.ROW_COUNT + bench.UPDATE_COUNT + 1)),
        ("delete_sequential", range(bench.ROW_COUNT + 1, bench.ROW_COUNT + bench.DELETE_COUNT + 1)),
        ("insert_sequential", range(1, bench.ROW_COUNT)),
    ],
)
def test_wrong_keys_fail_verification(
    benchmark: Any,
    cursor: CountingCursor,
    conn: CountingConnection,
    operation: str,
    wrong_keys: range,
) -> None:
    with pytest.raises(bench.WorkloadVerificationError):
        bench.run_workload(benchmark, cursor, conn, operation, keys=wrong_keys)


def test_unchecked_writes_against_missing_keys_are_rejected(
    cursor: CountingCursor, conn: CountingConnection
) -> None:
    # The pre-fix failure mode: after DELETE + AUTO_INCREMENT re-seed the keys 1..N no longer
    # exist, so UPDATE/DELETE affect 0 rows. Simulate that state with shifted keys.
    conn.raw.executemany(
        bench.INSERT_SQL,
        [(key + bench.ROW_COUNT, "update_x", key) for key in range(1, bench.ROW_COUNT + 1)],
    )
    observed: list[object] = []
    bench._run_update_indexed(cursor, conn, range(1, bench.UPDATE_COUNT + 1), observed)

    assert observed == [0] * bench.UPDATE_COUNT
    with pytest.raises(bench.WorkloadVerificationError, match="not affecting exactly 1 row"):
        bench._verify_update(cursor, observed)


def test_empty_select_answer_fails(cursor: CountingCursor) -> None:
    with pytest.raises(bench.WorkloadVerificationError, match="expected 10000 rows, got 0"):
        bench._verify_select_full_scan(cursor, [])


def test_wrong_payload_fails(cursor: CountingCursor, conn: CountingConnection) -> None:
    bench._seed(cursor, conn, "select")
    observed: list[object] = []
    bench._run_select_by_pk(cursor, conn, range(1, bench.ROW_COUNT + 1), observed)
    observed[17] = (18, "select_00018", 19)

    with pytest.raises(bench.WorkloadVerificationError, match="expected row"):
        bench._verify_select_by_pk(cursor, observed)


def test_seed_rejects_leftover_rows(cursor: CountingCursor, conn: CountingConnection) -> None:
    class NoDeleteCursor(CountingCursor):
        def execute(self, sql: str, params: tuple[object, ...] | None = None) -> object:
            if sql == "DELETE FROM bench_driver_pycubrid":
                return None
            return super().execute(sql, params)

    conn.raw.execute(bench.INSERT_SQL, (1, "stale", 1))
    stale_cursor = NoDeleteCursor(conn.raw.cursor(), conn.statements)

    with pytest.raises(bench.WorkloadVerificationError, match="empty table before round"):
        bench._seed(stale_cursor, conn, "select")
