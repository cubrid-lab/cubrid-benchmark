# pycubrid executemany overhead

> **Status**: active
> **Question**: Why is pycubrid's one-round-trip `executemany()` slower than CUBRIDdb's per-row prepared
> execution, and is it client or server time? Does it hold for UPDATE, DELETE and MERGE?
> **Tracking issue**: [cubrid-lab/pycubrid#822](https://github.com/cubrid-lab/pycubrid/issues/822)
> Follows [pycubrid-release-baseline](../pycubrid-release-baseline/README.md) (pycubrid#797).

## Result (2026-10-11, CUBRID 11.4.6, pycubrid d727224, CUBRIDdb v11.3.0.51, 5 trials, medians, commit included)

| Rows | pycubrid executemany / CUBRIDdb executemany, INSERT | same, UPDATE | DELETE | MERGE |
|---:|---:|---:|---:|---:|
| 100 | 1.13x | 1.13x | 1.13x | 1.49x |
| 1,000 | 1.39x | 1.65x | 1.48x | 2.64x |
| 10,000 | 1.54x | 1.56x | 1.47x | 4.72x |

* INSERT at 10,000 rows: 95% of pycubrid's statement phase (2,748 of 2,885 ms) is waiting for the broker's
  reply; rendering parameters is 4% (120 ms), packet write + parse under 1%. The broker SQL log shows the CAS busy
  for 2,734 ms with the batch. All 10,000 batch statements are plan-cache hits.
* Per statement the server needs about 275 us in the literal batch versus about 131 us per prepared execute
  (`pycubrid.compat.native`, FC2 once + FC3 per row, 10,000 round trips), which reaches 0.99-1.31x of CUBRIDdb for INSERT/UPDATE/DELETE/MERGE.
* cProfile at 10,000 rows: 89% of the call is blocked in `recv_into`; of the remaining 11% parameter rendering is 80%
  (one third of it re-tokenising the same SQL template in `split_on_placeholders`).
* Memory: 361 B/row (140 rendered SQL strings, 109 request buffer, about 105 final bytes copy at the peak).
* Recommendation: document the trade-off; do not treat client optimisation as the fix (at most about 2-4%);
  evaluate a prepared-statement batch path (FC2/FC3 loop or the unused `EXECUTE_ARRAY` FC21) in a separately scoped issue.

See [`runs/2026-10-11_executemany-vs-cubriddb/derived/summary.md`](runs/2026-10-11_executemany-vs-cubriddb/derived/summary.md)
for all tables. The prose report that accompanied the run is [`REPORT.md`](REPORT.md) in this directory.

Follow-up: the prepared batch paths, including `EXECUTE_ARRAY`, were measured on CUBRID 10.2-11.4 in
[pycubrid-prepared-batch](../pycubrid-prepared-batch/README.md) (pycubrid#836).

## Methodology

Same server, schema, data shape, `autocommit=False` and timed explicit commit as pycubrid-release-baseline. Every measured iteration
checks COUNT/SUM(id)/SUM(age) of the whole target table. Stages of `scripts/executemany_overhead_bench.py`:

| Stage | What | Drivers |
|---|---|---|
| `compare` | end-to-end time of INSERT/UPDATE/DELETE/MERGE batches through each execution path | pycubrid (4 paths), CUBRIDdb (2 paths) |
| `phases` | client/server split: render, packet write, sendall, wait, receive, parse, residual (socket proxy + wrappers in the harness process) | pycubrid |
| `profile` | cProfile of `executemany` at 10,000 rows | pycubrid |
| `memory` | tracemalloc peak and allocation sites | pycubrid |
| `serverlog` | one run per path on a fresh connection with a marker statement; the broker SQL log is sliced after each run | both |

Sensitivity: broker `SQL_LOG` off, and connecting to the container IP without docker-proxy (both 3 trials, noisy).

## Reproduction

```bash
docker run -d --name inv822-114 -e CUBRID_DB=testdb -p 33531:33000 cubrid/cubrid:11.4
# venv with pycubrid at $MAIN_SHA (pip install ./pycubrid) and a venv with CUBRIDdb v11.3.0.51
export PY_MAIN=/path/venv-py/bin/python PY_CUBRIDDB=/path/venv-db/bin/python
export MAIN_SHA=d72722467c81cb690f002c921dc2e468110192fe CUBRIDDB_SHA=c63ab9c51353e5c80e02dd2c1a19d4640acb29f5
export CONTAINER=inv822-114 CONTAINER_IP=$(docker inspect -f '{{.NetworkSettings.Networks.bridge.IPAddress}}' inv822-114)
experiments/pycubrid-executemany-overhead/run.sh experiments/pycubrid-executemany-overhead/runs/<run-id> 5
```

The full run took about 2.5 hours on the shared devbox. `QUICK=1` shrinks everything to a smoke run.
`broker_changer` must run as the `cubrid` user inside the container (`docker exec -u cubrid ... bash -lc`).

## Measurement caveat: shared, loaded machine

The run was made on a shared development machine (7 logged-in users, one-minute load average 1.6-14.0, see
`runs/2026-10-11_executemany-vs-cubriddb/raw/loadavg.log`; a foreign `pip install` process used about 90% of one core the whole time).
Absolute times, interquartile ranges and individual per-trial ratios (some outliers of 0.4-2.5x for UPDATE, DELETE and MERGE) carry
load noise. **Only the pooled medians over five trials and the phase shares (for example the 95% wait) are reliable; quote nothing else.**
The two sensitivity runs (broker `SQL_LOG` off, direct container IP) used 3 trials and ran at the highest load; the direct-IP run is inconclusive.

## Disclosed deviations

* **CUBRIDdb build**: `build_cci.sh` in cubrid-python v11.3.0.51 calls `sh build.sh`, which fails on dash (Ubuntu `/bin/sh`) because of `typeset`.
  The CUBRIDdb build needed `bash build.sh`; that one word was changed locally. No other change to CUBRIDdb or its bundled CCI sources.
* **Server-log step re-run**: step 5 (broker SQL-log slices) was first run with a one-shot log dump, which lost the INSERT slices to log
  rotation. It was re-run with per-run slicing (the version now in `run.sh`) against the same server before the summary was generated.
* **Restarted run**: the first attempt of the whole run died at the DELETE 10,000-row verification (`SUM()` is NULL on an empty table, a harness bug).
  The harness was fixed and the run restarted from scratch; no partial data was reused.
* **Private flag for the prepared path**: the `pycubrid.compat.native` harness sets `_driver.autocommit = False` directly. The public
  `connection.set_autocommit(False)` exists at the measured commit (pycubrid#467) and gives the same mode; the harness did not use it.
* **Driver code identity**: pycubrid `main` d727224 is used; `git diff 2228d66..d727224 -- pycubrid scripts` is empty, so the driver code equals
  the one measured in pycubrid-release-baseline.

## Run History

| Run ID | Date | Label | Compares To | Key Finding |
|---|---|---|---|---|
| [2026-10-11_executemany-vs-cubriddb](runs/2026-10-11_executemany-vs-cubriddb/derived/summary.md) | 2026-10-11 | executemany-vs-cubriddb | pycubrid-release-baseline | Gap reproduced (1.13/1.39/1.54x INSERT); 95% of the statement phase is broker wait; the server needs about 2x more time per literal batch statement than per prepared execute; MERGE batches 4.7x slower |

## Limitations

Shared machine (load average up to 14), single server instance, loopback only (RTT 76 us), CUBRID 11.4.6 only (10.2/11.0/11.2 not measured), server-side time not profiled below the broker SQL log
(1 ms resolution), `EXECUTE_ARRAY` not tested, `compat.native` path binds only scalar INT/str/NULL (`bind_param`) and the harness sets the private autocommit flag instead of the public setter.
