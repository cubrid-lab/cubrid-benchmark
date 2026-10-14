# Python Workload Contracts

The contract of every maintained CUBRID Python workload. The machine-readable source is the
`SCENARIOS` dictionary in each module, which `tests/test_workload_contracts.py` checks against
this page and against the module's benchmark functions.

The pytest-benchmark `ops` value is **calls per second**: one call is one pedantic round (one
iteration). For rows per second, multiply by `rows_per_call`. Each pedantic round, the warm-up
included, starts from the stated setup, which runs outside the timed region. An untimed
teardown verifies the stated result, and a mismatch fails the benchmark instead of producing a
number.

## Tier 1: `python/bench_pycubrid.py` (`tier1-explicit-keys-v2`)

Table `bench_driver_pycubrid (id INT PRIMARY KEY, name VARCHAR(100), amount INT)`, explicit keys.

| Scenario | One call | Rows/call | Timed | Commit timed | Verification |
|---|---|---:|---|---|---|
| `insert_sequential` | 10,000 single-row INSERTs + 1 commit | 10,000 | INSERT loop and commit | yes | row count, key range, sum, every row |
| `select_by_pk` | 10,000 × SELECT ... WHERE id = ? + fetchone | 10,000 | SELECT loop | no | every row's id and payload |
| `select_full_scan` | 1 SELECT of 10,000 rows + fetchall | 10,000 | query and fetch | no | all rows |
| `update_indexed` | 1,000 × UPDATE ... WHERE id = ? + 1 commit | 1,000 | UPDATE loop and commit | yes | every rowcount is 1, updated values |
| `delete_sequential` | 1,000 × DELETE ... WHERE id = ? + 1 commit | 1,000 | DELETE loop and commit | yes | every rowcount is 1, remaining rows |

## Tier 1 extended: `python/bench_pycubrid_extended.py` (`tier1-extended-v2`)

Table `bench_driver_pycubrid_extended (id INT PRIMARY KEY, name VARCHAR(100), amount INT)`.
`amount` is not indexed. Where a protocol path is claimed, one untimed probe call before the
measurement counts the CAS request packets pycubrid sent and fails the benchmark if the path
differs. The observed counts go into the result's `extra_info`.

| Scenario | One call | Rows/call | Timed | Commit timed | Protocol (verified) | Verification |
|---|---|---:|---|---|---|---|
| `connect_query_close` | 100 × (connect, SELECT 1, close) | 100 | connect, query, close | no | not claimed | every SELECT 1 returns (1,) |
| `execute_parameterized_loop` | 1,000 × execute(SELECT ... WHERE amount = ?) + fetchone | 1,000 | the loop | no | 1,000 × `PrepareAndExecutePacket`, no EXECUTE_ARRAY or batch | every row |
| `executemany_literal_batch` | executemany(INSERT, 1,000 rows) + commit | 1,000 | executemany and commit | yes | 1 `BatchExecutePacket` (FC20, rendered literals) | all 1,000 rows |
| `executemany_prepared_batch` (opt-in: `BENCH_EXECUTEMANY_PREPARED=1`) | executemany(INSERT, 1,000 rows, prepared=True) + commit | 1,000 | executemany and commit | yes | 1 `PreparePacket` + 1 `ExecuteArrayPacket` (FC2 + FC21) | all 1,000 rows |
| `concurrent_select_4_threads` | 4 threads, own connection each, 250 lookups per thread | 1,000 | thread start, connect, lookups, close | no | not claimed | every row |

`execute_parameterized_loop` replaces the earlier `prepared_statement` scenario. That name was
wrong: the loop is ordinary `cursor.execute(sql, params)`, one PREPARE_AND_EXECUTE request per
statement, not an explicit prepared-statement reuse. An explicit FC2/FC3 prepared loop is not
measured, because pycubrid has no public API for one.

## Tier 2: `python/bench_sqlalchemy_cubrid.py` (`tier2-sqlalchemy-v2`)

ORM model `bench_users` (autoincrement `id`, name, email, age, created_at). One untimed probe call
per scenario counts the SQL statements and `executemany` calls the engine issued, and stores them
in `extra_info` (`sql_statements`, `executemany_calls`).

| Scenario | One call | Rows/call | Timed | Commit timed | SQL shape | Verification |
|---|---|---:|---|---|---|---|
| `single_row_crud_orm` | ORM insert, get, update, get, delete; 3 sessions, 3 commits | 1 | the sessions and commits | yes | unit of work (5 statements observed on CUBRID 11.4/10.2) | row found, age + 1, row gone |
| `bulk_insert_orm_100` | session.add_all(100) + commit | 100 | add_all, flush, commit | yes | one INSERT per row observed (100 statements, 0 executemany) | 100 rows with the expected names |
| `bulk_insert_orm_1000` | session.add_all(1000) + commit | 1,000 | add_all, flush, commit | yes | one INSERT per row observed (1,000 statements, 0 executemany) | 1,000 rows with the expected names |
| `orm_select_all_entities` | select(BenchUser).order_by(id), 1,000 entities | 1,000 | query, fetch, entity construction | no | 1 SELECT plus identity-map work | 1,000 entities in order |
| `text_select_all_rows` | session.execute(text(SELECT ... ORDER BY id)), 1,000 rows | 1,000 | query and fetch of plain rows | no | 1 SELECT, no entities | 1,000 rows in order |

These are **not equivalent pairs**. `orm_select_all_entities` builds entities and
`text_select_all_rows` does not. The ORM bulk insert sends one INSERT per row through the
current dialect, so it is not comparable with the driver's single-request
`executemany_literal_batch`. Compare scenarios only together with their recorded SQL shape.

## Series

Changing what one call does starts a new workload revision. The revisions on this page are new
series: results recorded under earlier names (`prepared_statement`, `batch_insert`,
`connect_disconnect`, `concurrent_select`, `test_bench_bulk_insert`,
`test_bench_query_builder_select_all`, `test_bench_raw_sql_select_all`) are historical and not
comparable with them.
