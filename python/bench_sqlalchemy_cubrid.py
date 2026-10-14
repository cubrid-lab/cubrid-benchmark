"""Python Tier 2 SQLAlchemy workloads on CUBRID (workload revision ``tier2-sqlalchemy-v2``).

Every scenario has a contract in ``SCENARIOS`` (also listed in ``docs/WORKLOADS.md``). The
pytest-benchmark ``ops`` value is **calls per second**. The scenarios are **not equivalent
pairs**: the ORM paths go through the unit of work and build entities, while the text() path
returns plain rows, and how the dialect batches ORM inserts (one statement per row or an
``executemany``) decides the SQL shape. One untimed probe call per scenario counts the SQL
statements and ``executemany`` calls the engine actually issued and stores them in the
result's ``extra_info``, so a comparison can be judged on the SQL that ran.

Clearing and seeding ``bench_users`` happen outside the timed region; an untimed teardown
verifies row counts and values after every round.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Generator, Sequence
from datetime import datetime, timezone
from typing import Any

import pytest
from pytest_benchmark.fixture import BenchmarkFixture
from sqlalchemy import DateTime, Integer, String, create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

WORKLOAD_REVISION = "tier2-sqlalchemy-v2"
SELECT_ROWS = 1000
ROUNDS = 5
WARMUP_ROUNDS = 1

SCENARIOS: dict[str, dict[str, object]] = {
    "single_row_crud_orm": {
        "call": "ORM insert + get + update + get + delete of one row, 3 sessions, 3 commits",
        "rows_per_call": 1,
        "timed": "the three sessions and their commits",
        "setup": "empty table, untimed",
        "commit_in_timed_region": True,
        "sql_shape": "unit of work; statement count recorded in extra_info",
        "verification": "row found after insert, age + 1 after update, gone after delete",
    },
    "bulk_insert_orm_100": {
        "call": "ORM session.add_all(100 rows) + commit",
        "rows_per_call": 100,
        "timed": "add_all, flush and commit",
        "setup": "empty table, untimed",
        "commit_in_timed_region": True,
        "sql_shape": "dialect-dependent batching; statements and executemany calls recorded",
        "verification": "exactly 100 rows with the expected names",
    },
    "bulk_insert_orm_1000": {
        "call": "ORM session.add_all(1000 rows) + commit",
        "rows_per_call": 1000,
        "timed": "add_all, flush and commit",
        "setup": "empty table, untimed",
        "commit_in_timed_region": True,
        "sql_shape": "dialect-dependent batching; statements and executemany calls recorded",
        "verification": "exactly 1000 rows with the expected names",
    },
    "orm_select_all_entities": {
        "call": f"ORM select(BenchUser).order_by(id), {SELECT_ROWS} entities",
        "rows_per_call": SELECT_ROWS,
        "timed": "query, fetch and entity construction",
        "setup": f"{SELECT_ROWS} rows seeded, untimed",
        "commit_in_timed_region": False,
        "sql_shape": "one SELECT; entity identity map work included",
        "verification": f"{SELECT_ROWS} entities in id order with the seeded names",
    },
    "text_select_all_rows": {
        "call": f"session.execute(text(SELECT ... ORDER BY id)), {SELECT_ROWS} rows",
        "rows_per_call": SELECT_ROWS,
        "timed": "query and fetch of plain rows",
        "setup": f"{SELECT_ROWS} rows seeded, untimed",
        "commit_in_timed_region": False,
        "sql_shape": "one SELECT; no entity construction (not equivalent to the ORM select)",
        "verification": f"{SELECT_ROWS} rows in id order with the seeded names",
    },
}


class WorkloadVerificationError(AssertionError):
    """A benchmarked call did not do the intended database work."""


class Base(DeclarativeBase):
    pass


class BenchUser(Base):
    __tablename__ = "bench_users"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200))
    age: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)


SessionFactory = sessionmaker[Session]


def _build_user(index: int, prefix: str) -> BenchUser:
    return BenchUser(
        name=f"{prefix}_{index:05d}",
        email=f"{prefix}_{index:05d}@example.com",
        age=20 + (index % 50),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _clear_users(engine: Engine) -> None:
    with engine.begin() as connection:
        _ = connection.execute(text("DELETE FROM bench_users"))


def _count_users(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(select(func.count()).select_from(BenchUser)).scalar_one())


def _expect_names(engine: Engine, prefix: str, count: int, context: str) -> None:
    with engine.connect() as connection:
        names = sorted(connection.execute(select(BenchUser.name)).scalars().all())
    expected = sorted(f"{prefix}_{index:05d}" for index in range(1, count + 1))
    if names != expected:
        raise WorkloadVerificationError(
            f"{context}: expected {count} rows named {prefix}_*, got {len(names)}"
        )


def _seed_users(session_factory: SessionFactory, engine: Engine, count: int, prefix: str) -> None:
    _clear_users(engine)
    with session_factory() as session:
        session.add_all(_build_user(index, prefix) for index in range(1, count + 1))
        session.commit()
    _expect_names(engine, prefix, count, "seeded bench_users")


def statement_probe(engine: Engine, call: Callable[[], None]) -> dict[str, object]:
    """Run ``call`` once and count the SQL statements and executemany calls it issued."""
    counts = {"sql_statements": 0, "executemany_calls": 0}

    def _before(conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool):
        counts["sql_statements"] += 1
        counts["executemany_calls"] += int(bool(many))

    event.listen(engine, "before_cursor_execute", _before)
    try:
        call()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return counts


def _record(benchmark: Any, scenario: str, sql: dict[str, object]) -> None:
    info = getattr(benchmark, "extra_info", None)
    if isinstance(info, dict):
        info.update(
            {
                "scenario": scenario,
                "workload_revision": WORKLOAD_REVISION,
                "contract": SCENARIOS[scenario],
                **sql,
            }
        )


def _measure(
    benchmark: Any,
    scenario: str,
    engine: Engine,
    target: Callable[[], None],
    setup: Callable[[], None],
    verify: Callable[[], None],
) -> None:
    setup()
    _record(benchmark, scenario, statement_probe(engine, target))
    verify()
    if getattr(benchmark, "disabled", False):
        return
    benchmark.pedantic(
        target,
        setup=setup,
        teardown=verify,
        rounds=ROUNDS,
        warmup_rounds=WARMUP_ROUNDS,
    )


@pytest.fixture(scope="module")
def cubrid_engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        os.environ.get("CUBRID_ORM_DSN", "cubrid+pycubrid://dba@localhost:33000/benchdb")
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def cubrid_session_factory(cubrid_engine: Engine) -> SessionFactory:
    return sessionmaker(bind=cubrid_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def cleanup_bench_users(cubrid_engine: Engine) -> Generator[None, None, None]:
    _clear_users(cubrid_engine)
    try:
        yield
    finally:
        _clear_users(cubrid_engine)


def test_bench_single_row_crud_orm(
    benchmark: BenchmarkFixture,
    cubrid_session_factory: SessionFactory,
    cubrid_engine: Engine,
) -> None:
    observed: dict[str, object] = {}

    def crud() -> None:
        # Only record what happened; the untimed verify() judges it.
        with cubrid_session_factory() as session:
            user = _build_user(1, "crud")
            session.add(user)
            session.commit()
            user_id = user.id
        with cubrid_session_factory() as session:
            selected = session.get(BenchUser, user_id)
            observed["inserted_age"] = None if selected is None else selected.age
            if selected is None:
                return
            selected.age += 1
            session.commit()
        with cubrid_session_factory() as session:
            selected = session.get(BenchUser, user_id)
            observed["updated_age"] = None if selected is None else selected.age
            if selected is None:
                return
            session.delete(selected)
            session.commit()

    def verify() -> None:
        inserted, updated = observed.pop("inserted_age", None), observed.pop("updated_age", None)
        if inserted is None:
            raise WorkloadVerificationError("single_row_crud_orm: inserted row not found")
        if not isinstance(inserted, int) or updated != inserted + 1:
            raise WorkloadVerificationError("single_row_crud_orm: update not visible")
        if _count_users(cubrid_engine) != 0:
            raise WorkloadVerificationError("single_row_crud_orm: row left after delete")

    _measure(
        benchmark,
        "single_row_crud_orm",
        cubrid_engine,
        crud,
        lambda: _clear_users(cubrid_engine),
        verify,
    )


@pytest.mark.parametrize("count", [100, 1000], ids=["100", "1000"])
def test_bench_bulk_insert_orm(
    benchmark: BenchmarkFixture,
    cubrid_session_factory: SessionFactory,
    cubrid_engine: Engine,
    count: int,
) -> None:
    prefix = f"bulk_{count}"

    def insert() -> None:
        with cubrid_session_factory() as session:
            session.add_all(_build_user(index, prefix) for index in range(1, count + 1))
            session.commit()

    _measure(
        benchmark,
        f"bulk_insert_orm_{count}",
        cubrid_engine,
        insert,
        lambda: _clear_users(cubrid_engine),
        lambda: _expect_names(cubrid_engine, prefix, count, f"bulk_insert_orm_{count}"),
    )


def _select_test(
    benchmark: Any,
    scenario: str,
    session_factory: SessionFactory,
    engine: Engine,
    query: Callable[[Session], Sequence[Any]],
    name_of: Callable[[Any], str],
) -> None:
    prefix = scenario
    observed: list[Sequence[Any]] = []

    def run() -> None:
        # Keep what was fetched; names are extracted and compared in the untimed verify().
        with session_factory() as session:
            observed.append(query(session))

    def verify() -> None:
        expected = [f"{prefix}_{index:05d}" for index in range(1, SELECT_ROWS + 1)]
        got = [name_of(item) for item in observed[-1]] if observed else []
        observed.clear()
        if got != expected:
            raise WorkloadVerificationError(
                f"{scenario}: expected {SELECT_ROWS} rows, got {len(got)}"
            )

    _seed_users(session_factory, engine, SELECT_ROWS, prefix)
    _measure(benchmark, scenario, engine, run, lambda: None, verify)


def test_bench_orm_select_all_entities(
    benchmark: BenchmarkFixture,
    cubrid_session_factory: SessionFactory,
    cubrid_engine: Engine,
) -> None:
    def query(session: Session) -> Sequence[Any]:
        return session.execute(select(BenchUser).order_by(BenchUser.id)).scalars().all()

    _select_test(
        benchmark,
        "orm_select_all_entities",
        cubrid_session_factory,
        cubrid_engine,
        query,
        lambda user: user.name,
    )


def test_bench_text_select_all_rows(
    benchmark: BenchmarkFixture,
    cubrid_session_factory: SessionFactory,
    cubrid_engine: Engine,
) -> None:
    def query(session: Session) -> Sequence[Any]:
        return session.execute(
            text("SELECT id, name, email, age, created_at FROM bench_users ORDER BY id")
        ).all()

    _select_test(
        benchmark,
        "text_select_all_rows",
        cubrid_session_factory,
        cubrid_engine,
        query,
        lambda row: row[1],
    )


@pytest.mark.skip(
    reason="TODO: add relationship/join loading benchmarks once schema has foreign keys"
)
def test_bench_relationship_join_loading() -> None:
    pass
