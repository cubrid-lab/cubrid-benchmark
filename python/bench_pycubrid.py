"""Python Tier 1 driver-throughput workload for pycubrid.

Workload protocol (revision ``tier1-explicit-keys-v2``):

- Table ``bench_driver_pycubrid (id INT PRIMARY KEY, name VARCHAR(100), amount INT)``.
  Keys are written explicitly as ``1..ROW_COUNT``; the table has no ``AUTO_INCREMENT``
  column, so a ``DELETE`` followed by a re-seed always produces the same keys.
- Every round (the warm-up round included) starts from an equivalent, verified state that the
  untimed ``setup`` callback builds: an empty table for INSERT, ``ROW_COUNT`` seeded rows for
  the other operations.
- The timed region contains only the measured operations and, for writes, their final
  ``commit()``. Re-seeding, verification and clean-up are outside the timed region.
- The untimed ``teardown`` callback checks every SELECT row (id and payload), every UPDATE and
  DELETE affected-row count, and the final table contents. A wrong or empty answer raises
  ``WorkloadVerificationError`` and fails the benchmark instead of producing a throughput number.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Sequence
from typing import Protocol

import pytest

WORKLOAD_REVISION = "tier1-explicit-keys-v2"
ROW_COUNT = 10_000
UPDATE_COUNT = 1_000
DELETE_COUNT = 1_000
UPDATE_OFFSET = 100_000
ROUNDS = 5
WARMUP_ROUNDS = 1

TABLE_SQL = "CREATE TABLE bench_driver_pycubrid (id INT PRIMARY KEY, name VARCHAR(100), amount INT)"
INSERT_SQL = "INSERT INTO bench_driver_pycubrid (id, name, amount) VALUES (?, ?, ?)"
SELECT_BY_PK_SQL = "SELECT id, name, amount FROM bench_driver_pycubrid WHERE id = ?"
SELECT_ALL_SQL = "SELECT id, name, amount FROM bench_driver_pycubrid"
UPDATE_SQL = "UPDATE bench_driver_pycubrid SET amount = ? WHERE id = ?"
DELETE_SQL = "DELETE FROM bench_driver_pycubrid WHERE id = ?"
SUMMARY_SQL = "SELECT COUNT(*), MIN(id), MAX(id), SUM(amount) FROM bench_driver_pycubrid"


class DBCursor(Protocol):
    rowcount: int

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> object: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...

    def close(self) -> None: ...


class DBConnection(Protocol):
    def cursor(self) -> DBCursor: ...

    def commit(self) -> None: ...


class WorkloadVerificationError(AssertionError):
    """A measured round did not perform the intended database work."""


def _row_name(prefix: str, key: int) -> str:
    return "{}_{:05d}".format(prefix, key)


def _expected_row(prefix: str, key: int) -> tuple[object, ...]:
    return (key, _row_name(prefix, key), key)


def _reset_table(cursor: DBCursor, conn: DBConnection) -> None:
    cursor.execute("DROP TABLE IF EXISTS bench_driver_pycubrid")
    cursor.execute(TABLE_SQL)
    conn.commit()


def _summary(cursor: DBCursor) -> tuple[int, int | None, int | None, int | None]:
    cursor.execute(SUMMARY_SQL)
    row = cursor.fetchone()
    if row is None:
        raise WorkloadVerificationError("summary query returned no row")
    count, min_id, max_id, total = row
    return (
        int(count),
        None if min_id is None else int(min_id),
        None if max_id is None else int(max_id),
        None if total is None else int(total),
    )


def _expect_summary(
    cursor: DBCursor, expected: tuple[int, int | None, int | None, int | None], context: str
) -> None:
    actual = _summary(cursor)
    if actual != expected:
        raise WorkloadVerificationError(
            "{}: expected (count, min id, max id, sum amount) {}, got {}".format(
                context, expected, actual
            )
        )


def _seeded_summary(count: int) -> tuple[int, int | None, int | None, int | None]:
    if count == 0:
        return (0, None, None, None)
    return (count, 1, count, count * (count + 1) // 2)


def _clear(cursor: DBCursor, conn: DBConnection) -> None:
    cursor.execute("DELETE FROM bench_driver_pycubrid")
    conn.commit()
    _expect_summary(cursor, _seeded_summary(0), "empty table before round")


def _seed(cursor: DBCursor, conn: DBConnection, prefix: str) -> None:
    _clear(cursor, conn)
    for key in range(1, ROW_COUNT + 1):
        cursor.execute(INSERT_SQL, (key, _row_name(prefix, key), key))
    conn.commit()
    _expect_summary(cursor, _seeded_summary(ROW_COUNT), "seeded table before round")


def _expect_rows(observed: Sequence[object], prefix: str, keys: range, context: str) -> None:
    if len(observed) != len(keys):
        raise WorkloadVerificationError(
            "{}: expected {} rows, got {}".format(context, len(keys), len(observed))
        )
    for key, row in zip(keys, observed):
        expected = _expected_row(prefix, key)
        if row is None or tuple(row) != expected:
            raise WorkloadVerificationError(
                "{}: expected row {}, got {}".format(context, expected, row)
            )


def _expect_rowcounts(observed: Sequence[int], expected_len: int, context: str) -> None:
    if len(observed) != expected_len or any(count != 1 for count in observed):
        misses = sum(1 for count in observed if count != 1)
        raise WorkloadVerificationError(
            "{}: expected {} statements affecting 1 row each, got {} statements, {} not "
            "affecting exactly 1 row".format(context, expected_len, len(observed), misses)
        )


# Measured operations. ``keys`` are the primary keys the timed region addresses; the teardown
# verifies against the protocol constants, so a wrong key list fails instead of passing.


def _run_insert_sequential(
    cursor: DBCursor, conn: DBConnection, keys: range, observed: list[object]
) -> None:
    for key in keys:
        cursor.execute(INSERT_SQL, (key, _row_name("insert", key), key))
    conn.commit()


def _run_select_by_pk(
    cursor: DBCursor, conn: DBConnection, keys: range, observed: list[object]
) -> None:
    for key in keys:
        cursor.execute(SELECT_BY_PK_SQL, (key,))
        observed.append(cursor.fetchone())


def _run_select_full_scan(
    cursor: DBCursor, conn: DBConnection, keys: range, observed: list[object]
) -> None:
    cursor.execute(SELECT_ALL_SQL)
    observed.extend(cursor.fetchall())


def _run_update_indexed(
    cursor: DBCursor, conn: DBConnection, keys: range, observed: list[object]
) -> None:
    for key in keys:
        cursor.execute(UPDATE_SQL, (key + UPDATE_OFFSET, key))
        observed.append(cursor.rowcount)
    conn.commit()


def _run_delete_sequential(
    cursor: DBCursor, conn: DBConnection, keys: range, observed: list[object]
) -> None:
    for key in keys:
        cursor.execute(DELETE_SQL, (key,))
        observed.append(cursor.rowcount)
    conn.commit()


# Untimed verification of each round.


def _verify_insert(cursor: DBCursor, observed: list[object]) -> None:
    _expect_summary(cursor, _seeded_summary(ROW_COUNT), "after INSERT")
    cursor.execute(SELECT_ALL_SQL + " ORDER BY id")
    _expect_rows(cursor.fetchall(), "insert", range(1, ROW_COUNT + 1), "INSERT contents")


def _verify_select_by_pk(cursor: DBCursor, observed: list[object]) -> None:
    _expect_rows(observed, "select", range(1, ROW_COUNT + 1), "SELECT by PK")
    _expect_summary(cursor, _seeded_summary(ROW_COUNT), "after SELECT by PK")


def _verify_select_full_scan(cursor: DBCursor, observed: list[object]) -> None:
    # The scan has no ORDER BY (as before the workload correction); compare in key order.
    rows = sorted(observed, key=lambda row: int(row[0]))
    _expect_rows(rows, "scan", range(1, ROW_COUNT + 1), "SELECT full scan")
    _expect_summary(cursor, _seeded_summary(ROW_COUNT), "after SELECT full scan")


def _verify_update(cursor: DBCursor, observed: list[object]) -> None:
    _expect_rowcounts(observed, UPDATE_COUNT, "UPDATE")
    expected_total = ROW_COUNT * (ROW_COUNT + 1) // 2 + UPDATE_COUNT * UPDATE_OFFSET
    _expect_summary(cursor, (ROW_COUNT, 1, ROW_COUNT, expected_total), "after UPDATE")
    cursor.execute(
        "SELECT COUNT(*) FROM bench_driver_pycubrid WHERE id <= ? AND amount = id + ?",
        (UPDATE_COUNT, UPDATE_OFFSET),
    )
    row = cursor.fetchone()
    if row is None or int(row[0]) != UPDATE_COUNT:
        raise WorkloadVerificationError(
            "after UPDATE: expected {} updated rows in ids 1..{}, got {}".format(
                UPDATE_COUNT, UPDATE_COUNT, row
            )
        )


def _verify_delete(cursor: DBCursor, observed: list[object]) -> None:
    _expect_rowcounts(observed, DELETE_COUNT, "DELETE")
    remaining = ROW_COUNT - DELETE_COUNT
    expected_total = ROW_COUNT * (ROW_COUNT + 1) // 2 - DELETE_COUNT * (DELETE_COUNT + 1) // 2
    _expect_summary(
        cursor, (remaining, DELETE_COUNT + 1, ROW_COUNT, expected_total), "after DELETE"
    )


# Scenario contracts (also listed in docs/WORKLOADS.md). ``ops`` is calls (rounds) per second.
SCENARIOS: dict[str, dict[str, object]] = {
    "insert_sequential": {
        "call": f"{ROW_COUNT} single-row INSERTs with explicit keys + 1 commit",
        "rows_per_call": ROW_COUNT,
        "timed": "the INSERT loop and the commit",
        "setup": "empty table, untimed",
        "commit_in_timed_region": True,
        "verification": "row count, key range, sum and every row's values",
    },
    "select_by_pk": {
        "call": f"{ROW_COUNT}x SELECT ... WHERE id = ? + fetchone",
        "rows_per_call": ROW_COUNT,
        "timed": "the SELECT loop",
        "setup": f"{ROW_COUNT} rows seeded, untimed",
        "commit_in_timed_region": False,
        "verification": "every fetched row's id and payload",
    },
    "select_full_scan": {
        "call": f"1 SELECT of {ROW_COUNT} rows + fetchall",
        "rows_per_call": ROW_COUNT,
        "timed": "the query and the fetch",
        "setup": f"{ROW_COUNT} rows seeded, untimed",
        "commit_in_timed_region": False,
        "verification": "all rows' ids and payloads",
    },
    "update_indexed": {
        "call": f"{UPDATE_COUNT}x UPDATE ... WHERE id = ? + 1 commit",
        "rows_per_call": UPDATE_COUNT,
        "timed": "the UPDATE loop and the commit",
        "setup": f"{ROW_COUNT} rows seeded, untimed",
        "commit_in_timed_region": True,
        "verification": "every rowcount == 1 and the updated values",
    },
    "delete_sequential": {
        "call": f"{DELETE_COUNT}x DELETE ... WHERE id = ? + 1 commit",
        "rows_per_call": DELETE_COUNT,
        "timed": "the DELETE loop and the commit",
        "setup": f"{ROW_COUNT} rows seeded, untimed",
        "commit_in_timed_region": True,
        "verification": "every rowcount == 1 and the remaining rows",
    },
}

RunFn = Callable[[DBCursor, DBConnection, range, list[object]], None]
VerifyFn = Callable[[DBCursor, list[object]], None]


def run_workload(
    benchmark: object,
    cursor: DBCursor,
    conn: DBConnection,
    operation: str,
    keys: range | None = None,
) -> None:
    """Run one Tier 1 operation through ``benchmark.pedantic`` with untimed setup/verification.

    ``keys`` overrides the addressed primary keys; it exists so tests can prove that a wrong
    key list fails verification.
    """
    run, verify, seed_prefix, default_keys = WORKLOADS[operation]
    measured_keys = default_keys if keys is None else keys

    def setup() -> tuple[tuple[object, ...], dict[str, object]]:
        if seed_prefix is None:
            _clear(cursor, conn)
        else:
            _seed(cursor, conn, seed_prefix)
        return (cursor, conn, measured_keys, []), {}

    def teardown(
        cursor_: DBCursor, conn_: DBConnection, keys_: range, observed: list[object]
    ) -> None:
        verify(cursor_, observed)

    if getattr(benchmark, "disabled", False):
        # pytest-benchmark skips teardown when benchmarking is disabled; verify explicitly.
        args, _ = setup()
        run(*args)
        teardown(*args)
        return

    benchmark.pedantic(
        run,
        setup=setup,
        teardown=teardown,
        rounds=ROUNDS,
        warmup_rounds=WARMUP_ROUNDS,
    )


WORKLOADS: dict[str, tuple[RunFn, VerifyFn, str | None, range]] = {
    "insert_sequential": (_run_insert_sequential, _verify_insert, None, range(1, ROW_COUNT + 1)),
    "select_by_pk": (_run_select_by_pk, _verify_select_by_pk, "select", range(1, ROW_COUNT + 1)),
    "select_full_scan": (
        _run_select_full_scan,
        _verify_select_full_scan,
        "scan",
        range(1, ROW_COUNT + 1),
    ),
    "update_indexed": (_run_update_indexed, _verify_update, "update", range(1, UPDATE_COUNT + 1)),
    "delete_sequential": (
        _run_delete_sequential,
        _verify_delete,
        "delete",
        range(1, DELETE_COUNT + 1),
    ),
}


@pytest.fixture
def cubrid_bench_cursor(cubrid_conn: DBConnection) -> Generator[DBCursor, None, None]:
    cursor = cubrid_conn.cursor()
    _reset_table(cursor, cubrid_conn)
    try:
        yield cursor
    finally:
        cursor.execute("DROP TABLE IF EXISTS bench_driver_pycubrid")
        cubrid_conn.commit()
        cursor.close()


def test_bench_insert_sequential(benchmark, cubrid_bench_cursor, cubrid_conn) -> None:
    run_workload(benchmark, cubrid_bench_cursor, cubrid_conn, "insert_sequential")


def test_bench_select_by_pk(benchmark, cubrid_bench_cursor, cubrid_conn) -> None:
    run_workload(benchmark, cubrid_bench_cursor, cubrid_conn, "select_by_pk")


def test_bench_select_full_scan(benchmark, cubrid_bench_cursor, cubrid_conn) -> None:
    run_workload(benchmark, cubrid_bench_cursor, cubrid_conn, "select_full_scan")


def test_bench_update_indexed(benchmark, cubrid_bench_cursor, cubrid_conn) -> None:
    run_workload(benchmark, cubrid_bench_cursor, cubrid_conn, "update_indexed")


def test_bench_delete_sequential(benchmark, cubrid_bench_cursor, cubrid_conn) -> None:
    run_workload(benchmark, cubrid_bench_cursor, cubrid_conn, "delete_sequential")
