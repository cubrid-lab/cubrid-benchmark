# Investigation of cubrid-lab/pycubrid#822: executemany() overhead against CUBRIDdb

Date of measurement: 2026-10-11. Investigation only; no driver code was changed, nothing was pushed
and nothing was posted to GitHub. All raw data, harness scripts and the derived tables are in
`publish/` (laid out like `experiments/pycubrid-release-baseline`); the full generated tables are in
`publish/experiments/pycubrid-executemany-overhead/runs/2026-10-11_executemany-vs-cubriddb/derived/summary.md`.

## 1. Conclusion in one paragraph

The INSERT gap reproduces (pycubrid `executemany` / CUBRIDdb `executemany`: 1.13x, 1.39x, 1.54x at
100 / 1,000 / 10,000 rows; the #797 baseline had 1.15x, 1.35x, 1.40x). It is **not a client-side cost**.
In `executemany` the client spends about 5% of the statement phase (rendering 4%, packet write+parse
under 1%) and **95% waiting for the broker's reply**; the broker's own SQL log shows the CAS working on
the batch for 2,734 ms of the 2,865 ms the client measured at 10,000 rows. The same server executes
the same rows about 2x cheaper per statement when it receives one prepared statement and N binds
(pycubrid's explicit `compat.native` FC2/FC3 path: 1.3x CUBRIDdb for INSERT, and 1.1-1.2x for UPDATE,
DELETE and MERGE) than when it receives N literal SQL strings in one `EXECUTE_BATCH` request. For MERGE
the literal batch is 4.7x slower than CUBRIDdb at 10,000 rows. Fewer round trips (1 instead of 10,000)
therefore do not pay off, because per-statement server cost for literal SQL (parse, plan-cache lookup,
no handle reuse) is larger than the round trips saved on loopback (about 76 us each). Optimising the
Python client can recover at most about 2-4% at 10,000 rows. **Recommendation: document the trade-off now
and open a separately scoped investigation of a prepared-statement batch path
(`PREPARE` once + `EXECUTE`/`EXECUTE_ARRAY`), with explicit transaction, error, row-count and
performance equivalence evidence. Do not spend effort on micro-optimising the client render path
as a fix for this gap.**

## 2. Environment and pins

| Item | Value |
|---|---|
| Host | Intel Core i5-9400F (6 cores), 31 GiB, Ubuntu 22.04.5, kernel 5.15.0-198, Docker 29.2.1, **shared machine** (7 logged-in users; 1-min load average 1.6-14.0 during the run, see `raw/loadavg.log`; a foreign `pip install` process was spinning at 90% CPU the whole time) |
| Server | CUBRID 11.4.6 (11.4.6.1963-0e7d3c1), `cubrid/cubrid:11.4` sha256:1248b77ad39888df9e064655937188ab99fe056ed7e35b57d06155a015dc1791 (same digest as #797), container `inv822-114`, host port 33531 -> 33000 through docker-proxy, db `testdb`, default BROKER1 (`SQL_LOG=ON`, `MAX_NUM_APPL_SERVER=40`) |
| Python | CPython 3.12.13 (uv), one venv per driver, every run under `python -I` |
| pycubrid | `main` = d72722467c81cb690f002c921dc2e468110192fe (reports `__version__` 1.10.0). The #797 run used 2228d663; `git diff 2228d66..d727224 -- pycubrid scripts` is empty (only docs changed), so the driver code is identical |
| CUBRIDdb | cubrid-python tag v11.3.0.51 = c63ab9c51353e5c80e02dd2c1a19d4640acb29f5, cci-src 7d1eb8f40f04089b8218d08e36e2c24a2de11b24, built locally with GCC 11.4.0 (only deviation: `build_cci.sh` calls `sh build.sh`, which fails on dash because of `typeset`; I changed that one word to `bash`) |
| Harness base | cubrid-benchmark 27e9783 (schema, data and timer conventions of `driver_baseline_bench.py` reused) |
| Transaction policy | `autocommit=False` for every connection, explicit `commit()` after each batch; the commit is timed (reported separately as "commit") |

## 3. Method

* Schema as in #797: `(id INT PRIMARY KEY, name VARCHAR(100), email VARCHAR(200), age INT)`; row `i` is
  `(i, 'user_i', 'user_i@example.com', 20 + i % 50)`. One table per operation plus a 10,000-row seed table.
* INSERT: empty target table, rows 1..N. UPDATE: `SET age = ? WHERE id = ?` on a 10,000-row table (ids 1..N).
  DELETE: `WHERE id = ?` (ids 1..N). MERGE: `MERGE INTO t USING (SELECT CAST(? AS INT) id, ... FROM db_root) s ON ...
  WHEN MATCHED THEN UPDATE SET age WHEN NOT MATCHED THEN INSERT`, half of the N rows pre-existing in the target.
* Every iteration resets the table (untimed), runs the batch, commits, then checks `COUNT(*)`, `SUM(id)`,
  `SUM(age)` of the whole table against values computed in Python. A mismatch aborts the run. All reported
  numbers come from verified iterations. (Row counts reported by `cursor.rowcount` are not compared across
  drivers: CUBRIDdb's `executemany` leaves `rowcount = -1`/last-row values.)
* Paths compared (all with the same rows and commit policy):
  * `pycubrid executemany`: one `EXECUTE_BATCH` (FC20) request with N literal SQL strings (current behaviour).
  * `pycubrid loop_execute`: `cursor.execute(sql, params)` per row. This is **not** a prepared statement:
    pycubrid renders literals client-side and sends `PREPARE_AND_EXECUTE` (FC41) per row, plus a
    `GET_LAST_INSERT_ID` request for INSERT and a `CLOSE_REQ` (3 requests per INSERT row, 2 per UPDATE/DELETE row;
    `raw/loop_requests_probe.out`).
  * `pycubrid batch_prerendered`: SQL strings rendered before the timer, then `cursor.executemany_batch(sql_list)`.
    Isolates "send + server + parse" from client rendering.
  * `pycubrid native_prepared`: the explicit `pycubrid.compat.native` cursor: FC2 `PREPARE` once, then `bind_param` x k
    and FC3 `EXECUTE` per row (typed INT/string binds). This is the closest pycubrid equivalent of CUBRIDdb's loop and the
    only true server-side prepared path in the driver today. It is a private-flag harness setup: `_driver.autocommit = False`
    because the public native cursor has no manual-commit mode yet (#467).
  * `CUBRIDdb executemany` (one `cci_prepare`, then bind + execute per row) and `CUBRIDdb loop_execute` (`cursor.execute(sql, args)` per row).
* Trials: 5 trials per driver, driver order alternating per trial, variant order rotated per trial, a separate process
  per driver and trial. Iterations per trial and path: 20 / 10 / 5 for 100 / 1,000 / 10,000 rows (plus 3 / 2 / 1 untimed warm-ups).
  Reported value = **median pooled over all trials and iterations**, plus the pooled IQR and the range of the five per-trial medians.
  Timer `time.perf_counter_ns()`.
* Phase split (pycubrid only, harness-side monkeypatching, driver untouched): a proxy around `Connection._socket` times
  `sendall`, the wait from the end of `sendall` to the first received byte, and the rest of `recv_into`; wrappers around
  `Packet.write/parse` and `Cursor._bind_parameters` time packet serialisation, parsing and parameter rendering; the residual is
  `statement time - sum of the above` (driver glue). The timing hooks in docs/PERFORMANCE.md (`enable_timing=True`) only expose
  one `execute` counter per call, so they cannot separate these phases and were not used. The cost of an empty request
  (`CHECK_CAS`) is measured as the loopback + CAS round-trip floor: **76 us** median (IQR 73-86 us, n=1,400).
* Server side: BROKER1's SQL log (`SQL_LOG=ON`, 1 ms timestamps) sliced per run between a unique marker statement and the
  disconnect. The slice is taken right after each run because the broker rotates each CAS log at about 10 MB. The log gives the
  CAS-side `execute_batch N` ... `execute_batch end` span, plan-cache hits (`(PC)`) and prepare counts. It has no usable per-statement
  time for single executes (all `time` fields are 0.000-0.001 s).
* Client profile: `cProfile` around `executemany` of 10,000 INSERT rows, 3 trials x 3 calls (profiler overhead 1.08x).
* Memory: `tracemalloc` peak around `executemany` + `commit` (same method as the baseline; rows built before tracing), 5 repetitions,
  plus allocation snapshots at the stages leading to the peak.
* Sensitivity: broker `SQL_LOG` switched off (`broker_changer broker1 sql_log off`); connecting to the container's bridge IP
  (no docker-proxy). Both were measured with 3 trials only and are noisy; see section 8.
* Exact commands: `publish/experiments/pycubrid-executemany-overhead/run.sh` (see README for the `PY_MAIN`, `PY_CUBRIDDB`, `MAIN_SHA` variables). One deviation, disclosed:
  step 5 (SQL-log slices) was first run with a one-shot log dump, which lost the INSERT slices to log rotation; step 5 was re-run
  afterwards with per-run slicing (the version now in `run.sh`) against the same server before the summary was generated.
  The first attempt of the whole run also died at the DELETE 10,000 verification (`SUM()` is NULL on an empty table, a harness bug), was fixed and
  restarted from scratch; no partial data was reused.

## 4. Question 1: where does the time of one `executemany` go?

### 4.1 INSERT, end-to-end (statement phase + commit), 5 trials, medians in ms

| Rows | pycubrid executemany | CUBRIDdb executemany | ratio | per-trial ratios | #797 baseline (pycubrid / CUBRIDdb / ratio) |
|---:|---:|---:|---:|---|---|
| 100 | 64.2 (IQR 0.3) | 56.6 (IQR 0.1) | 1.13x | 1.13 1.61 1.13 1.13 1.13 | 124.6 / 108.7 / 1.15x |
| 1,000 | 322.5 (IQR 83.6) | 231.7 (IQR 16.7) | 1.39x | 1.37 2.27 1.42 1.39 1.39 | 359.7 / 266.1 / 1.35x |
| 10,000 | 3,146 (IQR 708) | 2,039 (IQR 59) | 1.54x | 1.51 1.58 1.50 1.98 1.72 | 3,017.9 / 2,150.0 / 1.40x |

The ratios reproduce the baseline. Absolute 100-row times are about half the baseline's because the median commit here is 33 ms (commit-bound
case; the baseline's storage was slower or busier). Trials 4-5 at 10,000 rows ran while the box was heavily loaded (load average up to 14); the
medians are robust to that, the IQR and the 1.98/1.72 trial ratios show it. The commit is 6% of the time at 10,000 rows and 53% at 100 rows, so
the 100-row ratio is mostly the (identical) commit.

### 4.2 INSERT, phase split of pycubrid `executemany` (ms, median of 5 trials x 5-20 iterations; percent of the statement phase)

| Rows | statement phase | render params to SQL | packet write | `sendall` | **wait for reply** | receive rest | packet parse | residual glue | commit |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 30.5 | 1.2 (4%) | 0.1 (0%) | 0.0 | **29.0 (95%)** | 0.0 | 0.1 | 0.1 | 34.1 |
| 1,000 | 288.3 | 11.9 (4%) | 0.6 (0%) | 0.1 | **274.7 (95%)** | 0.0 | 0.8 | 0.4 | 37.9 |
| 10,000 | 2,884.6 | 119.6 (4%) | 5.9 (0%) | 0.2 | **2,747.7 (95%)** | 0.0 | 8.3 | 3.1 | 205.9 |

Per row: 288 us total, of which 12 us render, 1.4 us packet write + parse, **275 us waiting**. The request is about 104 bytes per row
(1,036,713 bytes at 10,000 rows) and the reply is small, so transmission is negligible (`sendall` 0.2 ms).
The standalone re-measurement of the same render loop (`[cur._bind_parameters(...) for p in rows]`) agrees with the in-situ number
(12.0 vs 11.9 ms at 1,000 rows).

### 4.3 Where the 95% wait is spent

* The CAS-side span from the broker SQL log (`execute_batch N` to `execute_batch end`) is 2,734 ms for the 2,865 ms client statement phase at
  10,000 rows (273 ms of 287 ms at 1,000; 32 of 34 ms at 100). Loopback, docker-proxy and kernel scheduling are therefore at most a few
  percent; the time is inside the broker/server while it processes the N literal statements.
* All batch statements are plan-cache hits (`(PC)`: 10,000 of 10,000 for INSERT/UPDATE/DELETE), so this is not plan compilation either. What remains
  is per-statement parse/normalise/cache lookup plus execution inside the CAS without handle reuse. **I cannot split CAS parse work from `cub_server`
  execution or from CAS's own per-statement log writing with the available instrumentation** (that needs server-side profiling such as `perf` inside the
  container, which was outside the "supported instrumentation" scope). Switching `SQL_LOG` off did not reduce the wait (2,902 ms vs 2,748 ms at 10,000
  rows in the phase split, noisy), so logging is not the explanation.
* The empty-request floor is 76 us. One round trip for the entire batch therefore saves at most about 10,000 x 76 us = 0.76 s relative to a
  per-row protocol, but the literal batch costs about 275 us per row of server time versus about 130 us per prepared execute (section 5).

### 4.4 Client profile (cProfile, `executemany` of 10,000 INSERT rows, 3 calls)

`raw/profile_pycubrid_t*.json`, `derived/profile_cumulative.txt`, `derived/profile_tottime.txt`. 9.40 s profiled for 3 calls:

| Function | cumulative (s) | tottime (s) | share of 9.40 s |
|---|---:|---:|---:|
| `socket.recv_into` (blocked waiting for reply) | 8.357 | 8.357 | 88.9% |
| `Cursor._bind_parameters` -> `bind_parameters` (render) | 0.836 | 0.020 + 0.121 | 8.9% |
| `split_on_placeholders` (re-tokenises the same SQL template for every row) | 0.347 | 0.322 | 3.7% |
| `format_parameter` (4 per row) | 0.219 | 0.109 | 2.3% |
| `typing` `isinstance(parameters, Sequence)` checks | 0.064 | 0.010 | 0.7% |
| `BatchExecutePacket.write` (incl. `_write_null_terminated_string` 0.096) | 0.103 | 0.006 | 1.1% |
| `BatchExecutePacket.parse` | 0.088 | 0.034 | 0.9% |

Everything the client does (not blocked in `recv_into`) is 1.04 s of 9.40 s = 11% under the profiler, and of that 80% is parameter rendering and 20% packet write/parse.
The largest single item, `split_on_placeholders`, is re-run for every row although the SQL template is the same: one third of the client work, 3.7% of the call.

### 4.5 Memory growth (about 380 bytes per row)

Reproduced: tracemalloc peak 40,290 B (100 rows), 354,637 B (1,000), **3,613,511 B (10,000) = 361 B/row**, five repetitions identical to within 30 bytes.
Allocation sites at 10,000 rows (`raw/memory_pycubrid_t1.json`):

| Site | Bytes | B/row | What |
|---|---:|---:|---|
| `_cursor_common.py:485` (`"".join(result)`) | 1,396,682 | 139.7 | the 10,000 rendered SQL `str` objects (102 chars -> 143 B each) |
| `packet.py:278` (`PacketWriter` `bytearray` extend) | 1,089,424 | 108.9 | the request buffer, one block |
| `bytes(self._buffer)` in `PacketWriter.finalize` | about 105 | about 105 | immutable copy of the request alive together with the buffer at the peak (peak 3,627,302 minus 2,583,033 before finalize) |
| `cursor.py:319` (`sql_list` list) | 85,176 | 8.5 | list of pointers |

All of it is transient: 130,792 B remain after the call. So the growth is the rendered SQL list plus two copies of the serialised packet; it is bounded by
the batch size and independent of the server.

## 5. Question 2: is a batch of literal SQL slower on the server than repeated prepared execution?

### 5.1 INSERT, all paths, SQL_LOG=ON (pooled medians in ms; "stmt" excludes the commit)

| Rows | CUBRIDdb executemany | pycubrid executemany (batch) | pycubrid batch_prerendered | pycubrid native_prepared (FC2 + N x FC3) | pycubrid loop_execute (FC41 per row) | CUBRIDdb loop_execute |
|---:|---:|---:|---:|---:|---:|---:|
| 100, total | 56.6 | 64.2 (1.13x) | 63.0 (1.11x) | 64.2 (1.13x) | 97.5 (1.72x) | 81.6 (1.44x) |
| 1,000, total | 231.7 | 322.5 (1.39x) | 339.3 (1.46x) | 281.3 (1.21x) | 672.5 (2.90x) | 507.2 (2.19x) |
| 10,000, total | 2,038.9 | 3,146.3 (1.54x) | 2,972.5 (1.46x) | 2,663.6 (1.31x) | 6,489.0 (3.18x) | 4,824.0 (2.37x) |
| 10,000, stmt | 1,844 | 2,947 (1.60x) | 2,772 (1.50x) | 2,447 (1.33x) | 6,304 (3.42x) | 4,648 (2.52x) |

Reading:

* `batch_prerendered` (client rendering removed) is only about 6% faster than `executemany` at 10,000 rows (2,772 vs 2,947 ms stmt): the render + write + parse cost.
  The remaining 50% over CUBRIDdb is entirely server wait.
* The Python-client prepared loop (`native_prepared`) does 10,000 round trips and 4 `bind_param` calls per row in pure Python, with 31 us/row of driver glue and 21 us/row of packet work
  (phase table below), and it is still **faster** than the one-round-trip literal batch (2,447 vs 2,947 ms stmt). CUBRIDdb's C loop is faster again (1,844 ms).
* Per-statement cost on the server side, from the client's send+wait+receive time (microseconds per row at 10,000 rows, SQL_LOG=ON):

| Path | requests/row | send+wait+recv per row | per request | minus 76 us round-trip floor |
|---|---:|---:|---:|---:|
| executemany (1 request) | 0.0002 | 275 | n/a | about 275 |
| native_prepared (FC3 per row) | 1.0 | 207 | 207 | about 131 |
| loop_execute (FC41 + last-insert-id + close) | 3.0 | 573 | 191 | about 115 per request, 3 requests |

So the server spends about **275 us per literal statement in a batch versus about 131 us per prepared execute (about 2.1x)**, even though the prepared path pays a full round trip for each row.
(`native_prepared` here is pycubrid's own client, so this comparison does not involve CUBRIDdb's C code at all.)
* Broker log cross-check at 1,000 INSERT rows: CAS processes the literal batch in 273 ms and a prepared loop for 245 ms (pycubrid native, includes the client's gaps between requests) and
  191 ms (CUBRIDdb, includes its gaps), i.e. the prepared loops are faster in wall time while waiting 1,000 round trips; prepare counts: 1 per batch (no handle reuse; every
  statement is its own parse), 2 per prepared loop (one real prepare + the marker SELECT), 1,001 per `execute()` loop.
* Why the per-row `execute()` loops are slow in both drivers: each row is a new prepare (1,001 prepares, all plan-cache hits) and a new execute. pycubrid additionally issues
  `GET_LAST_INSERT_ID` and `CLOSE_REQ` per INSERT row, which is why its `execute()` loop is 1.3-1.4x slower than CUBRIDdb's.

### 5.2 Phase split of the other pycubrid paths (INSERT, 10,000 rows, ms per statement phase)

| Path | stmt | render | packet write | send | wait | recv rest | parse | residual | requests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| executemany | 2,884.6 | 119.6 (4%) | 5.9 | 0.2 | 2,747.7 (95%) | 0.0 | 8.3 | 3.1 | 2 |
| native_prepared | 2,616.4 | 0 | 134.6 (5%) | 126.3 (5%) | 1,927.4 (74%) | 15.6 | 76.8 (3%) | 309.1 (12%) | 10,002 |
| loop_execute | 6,752.6 | 217.8 (3%) | 147.9 (2%) | 374.8 (6%) | 5,315.9 (79%) | 42.1 | 124.9 (2%) | 552.9 (8%) | 30,000 |

## 6. Question 3: UPDATE, DELETE and MERGE (not part of the original #797 measurement)

Total time (statement + commit) ratios against CUBRIDdb `executemany`, pooled medians (per-trial ranges and absolute times in `derived/summary.md`):

| Operation | Rows | pycubrid executemany (batch) | pycubrid batch_prerendered | pycubrid native_prepared | pycubrid loop_execute | CUBRIDdb loop_execute |
|---|---:|---:|---:|---:|---:|---:|
| INSERT | 100 / 1,000 / 10,000 | 1.13 / 1.39 / 1.54 | 1.11 / 1.46 / 1.46 | 1.13 / 1.21 / 1.31 | 1.72 / 2.90 / 3.18 | 1.44 / 2.19 / 2.37 |
| UPDATE | 100 / 1,000 / 10,000 | 1.13 / 1.65 / 1.56 | 1.12 / 1.50 / 1.69 | 0.99 / 1.37 / 1.19 | 1.57 / 2.86 / 3.58 | 1.44 / 2.27 / 2.52 |
| DELETE | 100 / 1,000 / 10,000 | 1.13 / 1.48 / 1.47 | 1.13 / 1.40 / 1.44 | 0.99 / 1.11 / 1.11 | 1.58 / 2.72 / 2.54 | 1.44 / 2.23 / 2.48 |
| MERGE | 100 / 1,000 / 10,000 | 1.49 / 2.64 / **4.72** | 1.30 / 2.39 / 4.55 | 1.11 / 1.21 / 1.17 | 1.94 / 3.82 / 6.10 | 1.77 / 2.66 / 2.82 |

Absolute medians at 10,000 rows (ms, total): UPDATE 2,923 vs 1,874 (CUBRIDdb), DELETE 2,888 vs 1,959, MERGE 13,565 vs 2,875.

* UPDATE and DELETE behave like INSERT: 1.5-1.65x at 1,000-10,000 rows, (the phase split was measured for INSERT only; for these operations `batch_prerendered` is within run-to-run noise of `executemany`, which is consistent with a small client share).
  The pycubrid prepared path closes most of the gap (1.1-1.4x).
* MERGE is the largest effect. With literal statements the CAS gets plan-cache hits on only 548-702 of 1,000 batch statements (the others are compiled), and the batch takes 4.7x as long as
  CUBRIDdb's prepared MERGE at 10,000 rows; the prepared path (one real prepare per run in the log) is 1.17x. A prepared batch path matters most here. MERGE run-to-run spread is large
  (one 10,000-row CUBRIDdb trial took 14 s while the box was loaded; the per-trial ratios are 4.7, 1.0, 4.8, 5.6, 4.6 and the median is unaffected).
* 100-row cases are commit-bound (33 ms median commit out of 56-65 ms) and the differences there are within the run-to-run noise except MERGE.
* The per-trial ratios for UPDATE/DELETE/MERGE contain load-induced outliers (0.4-2.5x); medians of five trials are what is reported.

## 7. Unattributable or uncertain

* The 95% "wait" is the sum of loopback + docker-proxy + broker + CAS + `cub_server` time; the broker log shows the CAS span equals the client's wait to within 5%, so the network is not it, but
  the split between CAS statement processing and the engine cannot be made with the SQL log (1 ms resolution, `time` fields 0 for single executes). I did not profile the server.
* Whether the 2.1x per-statement difference is due to parse/normalise work, absence of handle reuse, per-statement result marshalling in the batch reply, or CAS
  logging of each batch statement is not established. SQL_LOG=OFF did not change the picture, but that sensitivity run is noisy (3 trials, load average up to 14).
* `execute()` loop and `native_prepared` have other bookkeeping differences (autocommit handling, holdable cursor flag in FC2) that I did not isolate.
* The direct-IP sensitivity run (no docker-proxy) was inconclusive: 10,000-row ratios 1.28x (batch), 1,000-row 0.73x, 100-row 1.04x with IQRs of 0.5-5 s; the load average was highest during it.
  The SQL-log match above is the better evidence that the proxy is not the bottleneck.
* The `native_prepared` path only binds INT/str/NULL. It is evidence about server cost, not a drop-in replacement; error and partial-failure semantics differ from `EXECUTE_BATCH`
  (batch continues after a failing statement and reports per-statement errors; the loop stops at the first error).
* `EXECUTE_ARRAY` (FC21, "execute with array binding"; defined in `constants.py` and `docs/PROTOCOL.md`, unused) is the protocol's single-round-trip prepared batch (CCI `cci_execute_array`). I did **not** test it. It is the
  obvious candidate to evaluate because it could combine both advantages.
* Box was shared; all absolute times carry load noise (see IQRs). Only the five-trial medians and the phase shares should be quoted.

## 8. Sensitivity runs (INSERT, 3 trials each; see `derived/summary.md` section 5)

* SQL_LOG=OFF: pycubrid executemany / CUBRIDdb executemany totals 3,062 / 1,770 ms at 10,000 rows (1.73x), 354 / 230 at 1,000 (1.54x); phase split at 10,000 rows: wait 2,902 ms of 3,059 (95%). The gap persists.
* Direct container IP: inconclusive (see section 7).

## 9. Recommendation

1. **Document the trade-off (small, do now).** `executemany` is one round trip but the server processes N literal statements; on a loopback CUBRID 11.4 server it is about 1.1-1.6x slower than CUBRIDdb's
   prepared loop for INSERT/UPDATE/DELETE at 1,000-10,000 rows and up to 4.7x slower for MERGE at 10,000 rows. The memory cost is about 360 B/row (rendered SQL + request buffer + one copy).
   Suitable places: `docs/PERFORMANCE.md` baseline notes and the `executemany` section of `docs/PARAMETER_BINDING.md`.
2. **Do not pursue client-side micro-optimisation as the fix.** At most the client render + serialise path (12-14 us/row of 290 us) can be sped up; caching `split_on_placeholders` per operation and avoiding the
   `typing.Sequence` `isinstance` would save roughly half of render, about 2% of the statement phase, so the 1.5x gap would remain about 1.5x. It could still be done as an independent low-priority cleanup (also lowers the 140 B/row
   rendered-string memory only if streaming into the packet buffer is introduced).
3. **Open a separately scoped investigation of a prepared-statement batch path** (follow-up text below). The evidence supports it as a candidate, not as "the fix": the prepared path reaches 0.99-1.31x of CUBRIDdb versus
   1.1-4.7x for the literal batch, but transaction, error, row-count and cross-version (10.2 / 11.0 / 11.2 / 11.4) equivalence are unproven, and CUBRID 10.2 behaviour was not measured at all.
4. Out of scope but noticed: `Cursor.execute()` for INSERT sends an extra `GET_LAST_INSERT_ID` request per statement (and `CLOSE_REQ` per statement in manual-commit mode), making pycubrid's per-row `execute()` loop 1.3-1.4x
   slower than CUBRIDdb's. This is a separate behaviour question (lastrowid contract) and should not be bundled.

## 10. Proposed follow-up issues (text ready to paste; labels per AGENTS.md)

**Issue A: Evaluate a prepared-statement batch path for executemany (investigation, then maybe implementation)**
Labels: `enhancement`, `area: performance`, `priority: medium`, `size: M`

> Problem and evidence: #822 found that `executemany()` INSERT/UPDATE/DELETE batches are 1.1-1.6x slower than CUBRIDdb's per-row prepared execution at 1,000-10,000 rows on CUBRID 11.4.6, and MERGE batches
> 4.7x slower at 10,000 rows (experiments/pycubrid-executemany-overhead, run 2026-10-11, pycubrid d727224). The client is only about 5% of the statement time; the broker spends 2,734 of 2,865 ms processing the 10,000
> literal statements (CAS SQL log), about 275 us per statement versus about 131 us per prepared execute (pycubrid.compat.native FC2 + FC3 loop). The unused `EXECUTE_ARRAY` (FC21) request is the protocol's one-round-trip prepared batch.
> Scope: (1) measure FC21 against FC20 and the FC2/FC3 loop for INSERT/UPDATE/DELETE/MERGE on CUBRID 10.2, 11.0, 11.2 and 11.4 with the existing harness (same schema, data, verification); (2) document transaction behaviour (autocommit,
> commit-in-batch), per-statement error reporting and partial-failure semantics, `rowcount` and `lastrowid`, and type coverage (the native path binds only INT/str/NULL today) of each; (3) decide whether an opt-in or default path is
> warranted. Non-goals: changing `executemany()` default behaviour before the equivalence evidence exists; any protocol change on the server side.
> Completion: a recorded decision (implement as separate issue, or document and close) with raw data.

**Issue B (optional, low): Cache placeholder tokenisation for `executemany` / `execute`**
Labels: `enhancement`, `area: performance`, `priority: low`, `size: S`

> `split_on_placeholders` re-tokenises the same SQL template for every parameter set (cProfile at 10,000 rows: 0.32 s tottime of 9.4 s, one third of the client-side work). Cache the split per operation within one
> `executemany` call (and avoid `isinstance(..., typing.Sequence)` per row). Expected gain: about 2% of the 10,000-row statement phase and a lower per-row CPU cost; it does not close the #822 gap. Must keep the documented
> literal-binding contract (docs/PARAMETER_BINDING.md) byte-identical: pin with the existing binding tests.

**Issue C (optional): document `executemany` batch trade-off and measured costs**
Labels: `documentation`, `priority: low`, `size: XS`

> Add the trade-off and the measured numbers from #822 to docs/PERFORMANCE.md and the `executemany` section of docs/PARAMETER_BINDING.md (one round trip, N literal statements processed by the broker; about 360 B/row transient memory; guidance for large
> batches). `Docs:` change only.

(Do not file a separate issue for the `GET_LAST_INSERT_ID` round trip without a decision on the `lastrowid` contract.)

## 11. Files

* `publish/scripts/executemany_overhead_bench.py`, `publish/scripts/summarize_executemany_overhead.py`, `publish/scripts/loop_requests_probe.py`
* `publish/experiments/pycubrid-executemany-overhead/{README.md,experiment.yaml,run.sh}` and `runs/2026-10-11_executemany-vs-cubriddb/{run.yaml,raw/,derived/}`
