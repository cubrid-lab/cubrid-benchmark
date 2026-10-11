# pycubrid prepared batch paths for `executemany()`

> **Status**: completed
> **Question**: Is a prepared-statement batch path faster than pycubrid's literal `EXECUTE_BATCH`
> `executemany()`, on CUBRID 10.2, 11.0, 11.2 and 11.4, and does it keep the same transaction, error,
> `rowcount` and type behaviour?
> **Tracking issue**: [cubrid-lab/pycubrid#836](https://github.com/cubrid-lab/pycubrid/issues/836)
> Follows [pycubrid-executemany-overhead](../pycubrid-executemany-overhead/README.md) (pycubrid#822).

## Paths

| Path | Requests per batch | What is sent |
|---|---|---|
| `executemany` (shipped) | 1 | `EXECUTE_BATCH` (function code 20): N client-rendered literal SQL strings |
| prepared loop (`pycubrid.compat.native`) | 1 + N | `PREPARE` (FC2) once, `EXECUTE` (FC3) with bound values per row |
| `EXECUTE_ARRAY` (prototype) | 3 | `PREPARE` (FC2), one `EXECUTE_ARRAY` (FC21) with every row's bound values, `CLOSE_REQ` (FC6) |

The `EXECUTE_ARRAY` request and reply are built by the harness (`ExecuteArrayPacket` in
`scripts/prepared_batch_bench.py`) from the CUBRID sources: CAS `fn_execute_array` / `ux_execute_array`
(`src/broker/cas_function.c`, `cas_execute.c`) and JDBC `UStatement.writeExecuteBatchRequest` /
`executeBatchInternal` (`UFunctionCode.EXECUTE_BATCH_PREPAREDSTATEMENT`). Request: int query handle,
int query timeout (protocol V4+), byte autocommit, then one (byte type, value) pair per marker per row, the
same pair pycubrid's `ExecutePacket` writes. Reply: int result code, int row count, then per row either
`count >= 0` plus an 8-byte OID or `-1`, error code, message; shard id last. The driver is not modified;
values are encoded with pycubrid's own prepared encoders.

## Result (2026-10-11, pycubrid f8d906d, autocommit off, commit timed, pooled medians)

`EXECUTE_ARRAY` time as a fraction of the shipped `executemany()`, commit included:

| CUBRID | Rows | INSERT | UPDATE | DELETE | MERGE |
|---|---:|---:|---:|---:|---:|
| 11.4.6 (5 trials) | 1,000 | 0.47 | 0.43 | 0.43 | 0.29 |
| 11.4.6 | 10,000 | 0.40 | 0.33 | 0.35 | 0.15 |
| 11.2.9 (3 trials) | 10,000 | 0.44 | 0.38 | 0.39 | 0.16 |
| 11.0.16 (3 trials) | 10,000 | 0.55 | 0.50 | 0.51 | 0.16 |
| 10.2.18 (3 trials) | 10,000 | 0.53 | 0.51 | 0.51 | 0.61 |

* At 10,000 rows on 11.4 the statement phase drops from 2,887 to 954 ms (INSERT), 2,740 to 855 ms (UPDATE),
  2,671 to 797 ms (DELETE) and 13,020 to 1,739 ms (MERGE): about 80-95 us per row (MERGE 174 us) instead of
  about 267-289 us (MERGE 1,302 us).
* `EXECUTE_ARRAY` is also 0.41-0.54x of the prepared loop for INSERT/UPDATE/DELETE at 1,000 and 10,000 rows on
  every version (MERGE 0.57-0.62x, 0.76-0.89x on 10.2), so it is the faster prepared path, not only the faster batch.
* At 100 rows the commit (about 52-56 ms, identical for all paths) dominates; the statement phase alone is
  0.35-0.53x of `executemany()`.
* The prepared loop is 0.79-1.06x of `executemany()` on 11.4 and 11.2, but 1.13-1.22x on 11.0 and 10.2 for
  INSERT/UPDATE/DELETE at 1,000 and 10,000 rows: on the older servers the literal batch is cheaper and the per-row round trip costs more.
* On 10.2 every MERGE path is slow (21-35 s at 10,000 rows); `EXECUTE_ARRAY` is still the fastest (0.61x).

All tables, statement/commit split and per-trial ranges:
[`runs/2026-10-11_prepared-batch-paths/derived/summary.md`](runs/2026-10-11_prepared-batch-paths/derived/summary.md).

## Semantics (all four versions, autocommit on and off)

The probes (`--stage semantics`, raw `semantics_<version>.json`) give the same outcome on 10.2, 11.0, 11.2
and 11.4; only server message text differs (owner-qualified names from 11.2, file paths).

| Behaviour | `executemany` (FC20) | prepared loop (FC3) | `EXECUTE_ARRAY` (FC21) |
|---|---|---|---|
| Row 3 of 5 fails (duplicate key or conversion error) | rows 1, 2, 4, 5 executed; only row 3 undone | stops at row 3; rows 4, 5 not executed | rows 1, 2, 4, 5 executed; only row 3 undone |
| Autocommit off, then `rollback()` / `commit()` | nothing kept / rows 1, 2, 4, 5 kept | nothing kept / rows 1, 2 kept | nothing kept / rows 1, 2, 4, 5 kept |
| Autocommit on | each successful row committed (1, 2, 4, 5) | rows 1, 2 committed | each successful row committed (1, 2, 4, 5) |
| Error report | server gives one result per row in order; the driver raises the first error (`IntegrityError` -670, `ProgrammingError` -494) and leaves `rowcount = -1` | exception at the failing row | server gives one result per row in order (same codes) |
| Per-row counts (UPDATE 3/0/2, DELETE 1/0/1) | identical; `rowcount` = sum (5, 2) | identical | identical; sum 5, 2 |
| `lastrowid` | `None` | n/a | not returned; a `GET_LAST_INSERT_ID` after the array returns the last generated id |
| NULL values | stored | stored | stored |
| `SET(INT)` | `pycubrid.Set` literal | `bind_set()` | `_PreparedCollection`, stored |
| CLOB | `str` literal into the column | not probed (`bind_lob()`) | LOB handle per row works with autocommit off; with autocommit on the second row fails (-1016, temporary LOB file removed by the first row's commit) |
| `bool`, int beyond 32 bits, `float`, `Decimal`, `date`, `datetime`, `bytes` | rendered and stored | rejected by the client encoder | rejected by the client encoder (pycubrid's prepared encoders cover only INT32, string, NULL, INT/STRING collections and LOB handles) |
| Server error text for a conversion error | `Cannot coerce 'abc' to type integer ...` | redacted by `compat.native` | `Cannot coerce host var to type integer.` |

So `EXECUTE_ARRAY` keeps the shipped batch's transaction and partial-failure semantics, and its per-row
counts and `rowcount` sum, on all four versions. It differs in type coverage (the prepared encoders), in
value coercion (host-variable rather than literal rules, visible in the error text), and in LOBs under
autocommit. It also needs every statement to be preparable with its markers (for example the MERGE source
uses `CAST(? AS ...)`).

## Recommendation

* **Adopt `EXECUTE_ARRAY` as an opt-in `executemany()` path; do not change the default.** The default would
  change which Python types are accepted and how values are coerced, so it is not behaviour-preserving even
  though transactions, partial failures and `rowcount` match.
* Do not recommend the prepared loop over `EXECUTE_ARRAY`: it is slower everywhere and slower than the literal
  batch on 10.2 and 11.0, and it stops at the first failing row.
* Implementation and the wider prepared type set are follow-up work in pycubrid (see the pycubrid#836 decision
  comment for the issues).

## Reproduction

```bash
docker run -d --name pyc836-114 -e CUBRID_DB=testdb -p 33814:33000 cubrid/cubrid:11.4
docker run -d --name pyc836-112 -e CUBRID_DB=testdb -p 33812:33000 cubrid/cubrid:11.2
docker run -d --name pyc836-110 -e CUBRID_DB=testdb -p 33811:33000 cubrid/cubrid:11.0
docker run -d --name pyc836-102 -e CUBRID_DB=testdb -p 33810:33000 cubrid/cubrid:10.2
# a venv with pycubrid installed from $MAIN_SHA
export PY_MAIN=/path/venv/bin/python MAIN_SHA=f8d906df0eb0ad0099e1e0342bf702937ae7ec2c
experiments/pycubrid-prepared-batch/run.sh experiments/pycubrid-prepared-batch/runs/<run-id>
```

The whole run took about 1 hour 45 minutes. `run.sh` stops the other containers while one version is
measured and starts them again at the end. `QUICK=1` shrinks the compare stage to a smoke run.

## Measurement caveat

Shared development machine (7 logged-in users, one-minute load average 1.3-7.8 at trial starts,
`raw/loadavg.log`). The 100-row times and single per-trial values carry load noise (see the per-trial ranges);
the pooled medians at 1,000 and 10,000 rows are stable across trials and versions.

## Run History

| Run ID | Date | Label | Compares To | Key Finding |
|---|---|---|---|---|
| [2026-10-11_prepared-batch-paths](runs/2026-10-11_prepared-batch-paths/derived/summary.md) | 2026-10-11 | prepared-batch-paths | pycubrid-executemany-overhead | `EXECUTE_ARRAY` takes 0.33-0.55x of `executemany()` for INSERT/UPDATE/DELETE and 0.15-0.16x for MERGE at 10,000 rows on 11.0-11.4; same transaction and partial-failure semantics on 10.2-11.4; narrower types |

## Limitations

Loopback only; CUBRIDdb not re-measured (its relation comes through the prepared loop of the previous run);
the `EXECUTE_ARRAY` path is a prototype outside the driver, without reconnect, deferred-close or async
handling; only INT and string values were timed; LOB `bind_lob()` on the prepared loop and server-side
profiles were not measured.
