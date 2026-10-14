"""Python Tier 1 extended workloads for pycubrid (workload revision ``tier1-extended-v2``).

Every scenario has a contract in ``SCENARIOS`` (also listed in ``docs/WORKLOADS.md``): what one
benchmarked call does, how many statements and rows it touches, what is timed, and which CAS
protocol path it is expected to use. The pytest-benchmark ``ops`` value is **calls per second**;
divide by ``rows_per_call`` for rows per second.

- Seeding and clean-up run in an untimed pedantic ``setup``; an untimed ``teardown`` verifies
  every fetched row and the final table state, and a mismatch fails the benchmark.
- Where ``SCENARIOS[...]["protocol"]`` names a packet path, one untimed probe call before the
  measurement records the CAS request packets pycubrid sent and fails the benchmark if the path
  differs. The observed counts are stored in the result's ``extra_info``. A parameterized
  ``execute`` loop is labelled as such: it is not an explicit prepared-statement reuse.
- ``executemany_prepared_batch`` (``executemany(..., prepared=True)``, PREPARE + EXECUTE_ARRAY)
  is opt-in: it runs only with ``BENCH_EXECUTEMANY_PREPARED=1`` and a pycubrid that accepts
  ``prepared=``.
"""

from __future__ import annotations

import importlib
import inspect
import os
from collections import Counter
from collections.abc import Callable, Generator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Protocol, cast

import pytest

WORKLOAD_REVISION = "tier1-extended-v2"
ROWS = 1000
CONNECT_CYCLES = 100
THREADS = 4
ROUNDS = 5
WARMUP_ROUNDS = 1

TABLE_SQL = (
    "CREATE TABLE bench_driver_pycubrid_extended "
    "(id INT PRIMARY KEY, name VARCHAR(100), amount INT)"
)
INSERT_SQL = "INSERT INTO bench_driver_pycubrid_extended (id, name, amount) VALUES (?, ?, ?)"
LOOKUP_SQL = "SELECT id, name, amount FROM bench_driver_pycubrid_extended WHERE amount = ?"
SUMMARY_SQL = "SELECT COUNT(*), MIN(id), MAX(id), SUM(amount) FROM bench_driver_pycubrid_extended"

SCENARIOS: dict[str, dict[str, object]] = {
    "connect_query_close": {
        "call": f"{CONNECT_CYCLES}x (connect, SELECT 1, close)",
        "statements_per_call": CONNECT_CYCLES,
        "rows_per_call": CONNECT_CYCLES,
        "timed": "connect, one SELECT 1 and close per cycle",
        "setup": "none",
        "commit_in_timed_region": False,
        "verification": "every SELECT 1 returns (1,)",
        "protocol": None,
    },
    "execute_parameterized_loop": {
        "call": f"{ROWS}x cursor.execute(SELECT ... WHERE amount = ?) + fetchone",
        "statements_per_call": ROWS,
        "rows_per_call": ROWS,
        "timed": "the SELECT loop only (amount is not indexed)",
        "setup": f"{ROWS} rows seeded with explicit keys, untimed",
        "commit_in_timed_region": False,
        "verification": "every fetched row matches its key",
        "protocol": {"PrepareAndExecutePacket": ROWS},
    },
    "executemany_literal_batch": {
        "call": f"cursor.executemany(INSERT, {ROWS} rows) + commit",
        "statements_per_call": ROWS,
        "rows_per_call": ROWS,
        "timed": "executemany and the commit",
        "setup": "empty table, untimed",
        "commit_in_timed_region": True,
        "verification": f"{ROWS} rows with the expected keys, names and amounts",
        "protocol": {"BatchExecutePacket": 1},
    },
    "executemany_prepared_batch": {
        "call": f"cursor.executemany(INSERT, {ROWS} rows, prepared=True) + commit",
        "statements_per_call": ROWS,
        "rows_per_call": ROWS,
        "timed": "executemany(prepared=True) and the commit",
        "setup": "empty table, untimed",
        "commit_in_timed_region": True,
        "verification": f"{ROWS} rows with the expected keys, names and amounts",
        "protocol": {"PreparePacket": 1, "ExecuteArrayPacket": 1},
        "opt_in": "BENCH_EXECUTEMANY_PREPARED=1",
    },
    "concurrent_select_4_threads": {
        "call": f"{THREADS} threads, own connection each, {ROWS // THREADS} lookups per thread",
        "statements_per_call": ROWS,
        "rows_per_call": ROWS,
        "timed": "thread start, per-thread connect, the lookups and close",
        "setup": f"{ROWS} rows seeded with explicit keys, untimed",
        "commit_in_timed_region": False,
        "verification": "every fetched row matches its key",
        "protocol": None,
    },
}


class DBCursor(Protocol):
    rowcount: int

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> object: ...

    def executemany(self, sql: str, params: Sequence[tuple[object, ...]]) -> object: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...

    def close(self) -> None: ...


class DBConnection(Protocol):
    def cursor(self) -> DBCursor: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...


class WorkloadVerificationError(AssertionError):
    """A benchmarked call did not do the intended database work."""


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _connect_cubrid() -> DBConnection:
    cubrid_module = importlib.import_module("pycubrid")
    return cast(
        DBConnection,
        cubrid_module.connect(
            host=_env("CUBRID_HOST", "localhost"),
            port=int(_env("CUBRID_PORT", "33000")),
            database=_env("CUBRID_DB", "benchdb"),
            user="dba",
            password="",
        ),
    )


def _row(key: int, prefix: str) -> tuple[object, ...]:
    return (key, "{}_{:05d}".format(prefix, key), key)


def _rows(prefix: str) -> list[tuple[object, ...]]:
    return [_row(key, prefix) for key in range(1, ROWS + 1)]


def _summary(cursor: DBCursor) -> tuple[int, ...]:
    cursor.execute(SUMMARY_SQL)
    row = cursor.fetchone()
    if row is None:
        raise WorkloadVerificationError("summary query returned no row")
    return tuple(0 if value is None else int(cast(int, value)) for value in row)


def _expect_summary(cursor: DBCursor, count: int, context: str) -> None:
    expected = (count, 1, count, count * (count + 1) // 2) if count else (0, 0, 0, 0)
    actual = _summary(cursor)
    if actual != expected:
        raise WorkloadVerificationError(f"{context}: expected {expected}, got {actual}")


def _clear(cursor: DBCursor, conn: DBConnection) -> None:
    cursor.execute("DELETE FROM bench_driver_pycubrid_extended")
    conn.commit()
    _expect_summary(cursor, 0, "empty table")


def _seed(cursor: DBCursor, conn: DBConnection, prefix: str) -> None:
    _clear(cursor, conn)
    for row in _rows(prefix):
        cursor.execute(INSERT_SQL, row)
    conn.commit()
    _expect_summary(cursor, ROWS, "seeded table")


def _expect_rows(observed: Sequence[object], prefix: str, context: str) -> None:
    expected = _rows(prefix)
    actual = sorted(
        (tuple(cast(Sequence[object], row)) for row in observed if row is not None),
        key=lambda row: int(cast(int, row[0])),
    )
    if len(observed) != ROWS or actual != expected:
        raise WorkloadVerificationError(
            f"{context}: expected {ROWS} matching rows, got {len(observed)} "
            f"({sum(1 for row in observed if row is None)} missing)"
        )


@contextmanager
def packet_probe(conn: object) -> Generator[Counter[str] | None, None, None]:
    """Count the CAS request packets pycubrid sends on ``conn`` (``None`` if not observable)."""
    original = getattr(conn, "_send_and_receive_locked", None)
    if original is None:
        yield None
        return
    counts: Counter[str] = Counter()

    def _counting(packet: Any, *args: Any, **kwargs: Any) -> Any:
        counts[type(packet).__name__] += 1
        return original(packet, *args, **kwargs)

    setattr(conn, "_send_and_receive_locked", _counting)
    try:
        yield counts
    finally:
        delattr(conn, "_send_and_receive_locked")


def check_protocol(scenario: str, counts: Counter[str] | None, conn: object) -> dict[str, object]:
    """Return the protocol evidence for ``scenario``; raise if the path is not the claimed one."""
    expected = SCENARIOS[scenario]["protocol"]
    if not expected:
        return {"protocol": "not claimed"}
    if counts is None:
        if type(conn).__module__.split(".")[0] == "pycubrid":
            raise WorkloadVerificationError(
                f"{scenario}: cannot observe pycubrid request packets to prove the protocol path"
            )
        return {"protocol": "unverified (driver is not pycubrid)"}
    exclusive = {"PrepareAndExecutePacket", "BatchExecutePacket", "PreparePacket"}
    exclusive |= {"ExecuteArrayPacket"}
    for name in exclusive:
        want = cast(dict[str, int], expected).get(name, 0)
        if counts.get(name, 0) != want:
            raise WorkloadVerificationError(
                f"{scenario}: expected {want} {name}, observed {dict(counts)}"
            )
    return {"protocol": "verified", "protocol_packets": dict(sorted(counts.items()))}


# Benchmarked calls. ``observed`` collects results for the untimed teardown.


def _run_connect_query_close(
    connect_factory: Callable[[], DBConnection], observed: list[object]
) -> None:
    for _ in range(CONNECT_CYCLES):
        conn = connect_factory()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        observed.append(cursor.fetchone())
        cursor.close()
        conn.close()


def _run_execute_parameterized_loop(
    cursor: DBCursor, conn: DBConnection, observed: list[object]
) -> None:
    for key in range(1, ROWS + 1):
        cursor.execute(LOOKUP_SQL, (key,))
        observed.append(cursor.fetchone())


def _run_executemany_literal_batch(
    cursor: DBCursor, conn: DBConnection, rows: list[tuple[object, ...]]
) -> None:
    cursor.executemany(INSERT_SQL, rows)
    conn.commit()


def _run_executemany_prepared_batch(
    cursor: DBCursor, conn: DBConnection, rows: list[tuple[object, ...]]
) -> None:
    cast(Any, cursor).executemany(INSERT_SQL, rows, prepared=True)
    conn.commit()


def _run_concurrent_select(
    connect_factory: Callable[[], DBConnection], observed: list[object]
) -> None:
    def _worker(start: int, end: int) -> list[object]:
        worker_conn = connect_factory()
        worker_cursor = worker_conn.cursor()
        rows: list[object] = []
        try:
            for key in range(start, end + 1):
                worker_cursor.execute(LOOKUP_SQL, (key,))
                rows.append(worker_cursor.fetchone())
        finally:
            worker_cursor.close()
            worker_conn.close()
        return rows

    step = ROWS // THREADS
    ranges = [(start, start + step - 1) for start in range(1, ROWS + 1, step)]
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        for rows in executor.map(lambda bounds: _worker(*bounds), ranges):
            observed.extend(rows)


def _pedantic(
    benchmark: Any,
    target: Callable[..., None],
    setup: Callable[[], tuple[Any, ...]],
    verify: Callable[..., None],
) -> None:
    def _setup() -> tuple[tuple[Any, ...], dict[str, Any]]:
        return setup(), {}

    if getattr(benchmark, "disabled", False):
        args = setup()
        target(*args)
        verify(*args)
        return
    benchmark.pedantic(
        target, setup=_setup, teardown=verify, rounds=ROUNDS, warmup_rounds=WARMUP_ROUNDS
    )


def _record(benchmark: Any, scenario: str, evidence: dict[str, object]) -> None:
    info = getattr(benchmark, "extra_info", None)
    if isinstance(info, dict):
        info.update({"scenario": scenario, "workload_revision": WORKLOAD_REVISION, **evidence})
        info["contract"] = {k: v for k, v in SCENARIOS[scenario].items() if k != "protocol"}


def _probe(scenario: str, conn: DBConnection, call: Callable[[], None]) -> dict[str, object]:
    with packet_probe(conn) as counts:
        call()
    return check_protocol(scenario, counts, conn)


@pytest.fixture
def cubrid_extended_cursor(cubrid_conn: DBConnection) -> Generator[DBCursor, None, None]:
    cursor = cubrid_conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS bench_driver_pycubrid_extended")
    cursor.execute(TABLE_SQL)
    cubrid_conn.commit()
    try:
        yield cursor
    finally:
        cursor.execute("DROP TABLE IF EXISTS bench_driver_pycubrid_extended")
        cubrid_conn.commit()
        cursor.close()


@pytest.fixture
def cubrid_connect_factory() -> Callable[[], DBConnection]:
    return _connect_cubrid


def test_bench_connect_query_close(benchmark, cubrid_connect_factory) -> None:
    def verify(factory: object, observed: list[object]) -> None:
        got = [tuple(cast(Sequence[object], row)) for row in observed if row is not None]
        if got != [(1,)] * CONNECT_CYCLES:
            raise WorkloadVerificationError("connect_query_close: a SELECT 1 did not return (1,)")

    _record(benchmark, "connect_query_close", {"protocol": "not claimed"})
    _pedantic(
        benchmark,
        _run_connect_query_close,
        lambda: (cubrid_connect_factory, []),
        verify,
    )


def test_bench_execute_parameterized_loop(benchmark, cubrid_extended_cursor, cubrid_conn) -> None:
    cursor, conn = cubrid_extended_cursor, cubrid_conn

    def setup() -> tuple[Any, ...]:
        _seed(cursor, conn, "lookup")
        return cursor, conn, []

    def verify(cursor_: DBCursor, conn_: DBConnection, observed: list[object]) -> None:
        _expect_rows(observed, "lookup", "execute_parameterized_loop")
        _expect_summary(cursor_, ROWS, "after execute_parameterized_loop")

    args = setup()
    evidence = _probe(
        "execute_parameterized_loop", conn, lambda: _run_execute_parameterized_loop(*args)
    )
    verify(*args)
    _record(benchmark, "execute_parameterized_loop", evidence)
    _pedantic(benchmark, _run_execute_parameterized_loop, setup, verify)


def _batch_test(
    benchmark: Any, scenario: str, target: Callable[..., None], cursor: DBCursor, conn: DBConnection
) -> None:
    def setup() -> tuple[Any, ...]:
        _clear(cursor, conn)
        return cursor, conn, _rows("batch")

    def verify(cursor_: DBCursor, conn_: DBConnection, rows: object) -> None:
        cursor_.execute("SELECT id, name, amount FROM bench_driver_pycubrid_extended ORDER BY id")
        _expect_rows(cursor_.fetchall(), "batch", scenario)

    args = setup()
    evidence = _probe(scenario, conn, lambda: target(*args))
    verify(*args)
    _record(benchmark, scenario, evidence)
    _pedantic(benchmark, target, setup, verify)


def test_bench_executemany_literal_batch(benchmark, cubrid_extended_cursor, cubrid_conn) -> None:
    _batch_test(
        benchmark,
        "executemany_literal_batch",
        _run_executemany_literal_batch,
        cubrid_extended_cursor,
        cubrid_conn,
    )


def test_bench_executemany_prepared_batch(benchmark, cubrid_extended_cursor, cubrid_conn) -> None:
    if os.getenv("BENCH_EXECUTEMANY_PREPARED") != "1":
        pytest.skip("opt-in: set BENCH_EXECUTEMANY_PREPARED=1")
    signature = inspect.signature(cubrid_extended_cursor.executemany)
    if "prepared" not in signature.parameters:
        pytest.skip("this pycubrid has no executemany(prepared=True)")
    _batch_test(
        benchmark,
        "executemany_prepared_batch",
        _run_executemany_prepared_batch,
        cubrid_extended_cursor,
        cubrid_conn,
    )


def test_bench_concurrent_select_4_threads(
    benchmark, cubrid_connect_factory, cubrid_extended_cursor, cubrid_conn
) -> None:
    def setup() -> tuple[Any, ...]:
        _seed(cubrid_extended_cursor, cubrid_conn, "concurrent")
        return cubrid_connect_factory, []

    def verify(factory: object, observed: list[object]) -> None:
        _expect_rows(observed, "concurrent", "concurrent_select_4_threads")

    _record(benchmark, "concurrent_select_4_threads", {"protocol": "not claimed"})
    _pedantic(benchmark, _run_concurrent_select, setup, verify)
