# pycubrid release baseline

> **Status**: active
> **Question**: On one CUBRID server, is pycubrid `main` slower than the latest release, and how
> does pycubrid compare with the official CUBRIDdb driver?
> **Tracking issue**: [cubrid-lab/pycubrid#797](https://github.com/cubrid-lab/pycubrid/issues/797)

## Methodology

- **One server for every driver**: CUBRID 11.4.6 in Docker (`cubrid/cubrid:11.4`), database
  `testdb`. Every driver uses the same schema, data and transaction settings.
- **Schema and data**: `bench797_rows(id INT PRIMARY KEY, name VARCHAR(100), email VARCHAR(200),
  age INT)` seeded with 10,000 deterministic rows `(i, 'user_i', 'user_i@example.com', 20 + i % 50)`.
  `bench797_batch` has the same columns and is the `executemany()` target.
- **Transactions**: `autocommit=False` is set explicitly on every connection. Each write is
  followed by an explicit `commit()`, and the commit is inside the timed region.
- **Drivers**: pycubrid `main`, pycubrid 1.10.0 (PyPI) and CUBRIDdb (cubrid-python v11.3.0.51).
  Each runs in its own CPython 3.12.13 venv and its own process.
- **Trials**: 5 trials per driver. The driver order rotates every trial so that drift on the
  shared host is spread evenly. The samples from all trials are pooled.
- **Timer**: `time.perf_counter_ns()`. Warm-up iterations are discarded.
- **Correctness first**: every measured iteration checks the returned rows (values for reads,
  `rowcount` plus a `COUNT`/`SUM` check for writes). A failed check aborts the run, so every
  reported number comes from verified data.
- **Memory**: `tracemalloc` peak (Python heap), measured in a separate untimed pass, plus the
  process `ru_maxrss`.
- **Regression rule** (main vs 1.10.0): a case is a regression when the `main` median is more
  than 5% slower **and** the pooled IQRs do not overlap. The table also reports a bootstrap
  95% CI of the median ratio and a Mann-Whitney U p-value.

### Cases

| Case | Iterations per trial (warm-up) | Drivers |
|---|---|---|
| connect + close | 200 (20) | all |
| SELECT by PK (`execute` + `fetchone`) | 1,000 (100) | all |
| INSERT / UPDATE / DELETE 1 row + commit | 300 (30) each | all |
| `executemany()` INSERT 100 / 1,000 / 10,000 rows + commit | 50 / 20 / 10 (3) | all |
| `fetchone` / `fetchmany(100)` / `fetchall` over 10,000 rows | 30 (3) each | all |
| the same fetches with `fetch_size` 1,000 and 10,000 | 30 (3) each | pycubrid only |
| Concurrency: 1 / 8 / 32 workers, one connection each, 200 PK SELECTs per worker | 5 rounds (1) | sync threads: all; `pycubrid.aio` tasks: pycubrid |

### Why a new script

The issue asks for the existing harness to be used. These are the gaps in it:

- `scripts/phase_decomposed_bench.py` only works with pycubrid: it imports `pycubrid.connection`
  directly. It has no `executemany()` sizes, no `fetchone`/`fetchmany` modes, no async or
  concurrency case, no memory measurement and no result verification. It was still run for
  pycubrid, as a continuity check (see below).
- `python/bench_pycubrid*.py` (pytest-benchmark) only works with pycubrid, and its session-wide
  autouse fixture needs a MySQL server. It also does not verify results.
- The earlier CUBRIDdb numbers (`driver-comparison`) came from the external benchforge tool.
  At benchforge `30ec73d`, that tool has no CUBRIDdb adapter.

`scripts/driver_baseline_bench.py` fills these gaps with one driver-neutral script. It follows
the timing and percentile conventions of `phase_decomposed_bench.py`.
`scripts/summarize_driver_baseline.py` produces `derived/summary.{json,md}`.

## Run History

| Run ID | Date | Label | Comparable Group | Compares To | Key Finding |
|--------|------|-------|-----------------|-------------|-------------|
| [2026-10-11_main-vs-1.10.0-vs-cubriddb](runs/2026-10-11_main-vs-1.10.0-vs-cubriddb/derived/summary.md) | 2026-10-11 | main-vs-1.10.0-vs-cubriddb | devbox-i5-9400F-linux5.15.0-198-docker29.2.1-cubrid114 | — | No main-vs-1.10.0 regression; pycubrid is 1.10-1.40x slower than CUBRIDdb except on commit-bound writes, and scales under threads where CUBRIDdb does not |

## Latest Conclusion (2026-10-11)

Environment: Intel Core i5-9400F (6 cores, 1 thread per core), 31 GiB RAM, Ubuntu 22.04.5,
kernel 5.15.0-198-generic, Docker 29.2.1, CPython 3.12.13, CUBRID 11.4.6. pycubrid `main` is
`2228d66`; 1.10.0 comes from PyPI; CUBRIDdb is cubrid-python `v11.3.0.51` (`c63ab9c`). The
machine is **shared**: other users and agent sessions were active, and the 1-minute load
average was 0.7-5.3 during the run. Treat differences of a few percent as noise unless the
table shows otherwise.

### pycubrid main vs 1.10.0

**No regression.** No case is more than 5% slower with non-overlapping IQRs. The full table is
in [`derived/summary.md`](runs/2026-10-11_main-vs-1.10.0-vs-cubriddb/derived/summary.md).

| Case | main median (ms) | 1.10.0 median (ms) | ratio | 95% CI |
|---|---:|---:|---:|---|
| connect + close | 1.923 | 1.914 | 1.005 | 0.990-1.021 |
| SELECT by PK | 0.601 | 0.602 | 0.998 | 0.991-1.006 |
| INSERT + commit | 34.46 | 33.88 | 1.017 | 0.966-1.217 |
| UPDATE + commit | 33.83 | 33.89 | 0.998 | 0.860-1.001 |
| DELETE + commit | 41.24 | 41.32 | 0.998 | 0.822-1.000 |
| executemany 100 | 124.6 | 124.6 | 1.000 | 0.875-1.134 |
| executemany 1,000 | 359.7 | 358.3 | 1.004 | 0.954-1.046 |
| executemany 10,000 | 3,017.9 | 2,997.5 | 1.007 | 1.004-1.010 |
| fetchone 10,000 rows | 51.20 | 50.56 | 1.013 | 0.993-1.021 |
| fetchmany 10,000 rows | 48.41 | 48.89 | 0.990 | 0.972-1.008 |
| fetchall 10,000 rows | 49.69 | 49.61 | 1.002 | 0.989-1.016 |

`executemany` of 10,000 rows is 0.7% slower on `main`, and that difference is statistically
detectable (p = 0.004). It is far below the 5% threshold. Concurrency throughput ratios (main /
1.10.0) are 0.96-1.03, with no regression.

### pycubrid main vs CUBRIDdb

| Case | pycubrid (ms) | CUBRIDdb (ms) | pycubrid / CUBRIDdb |
|---|---:|---:|---:|
| connect + close | 1.923 | 1.008 | **1.91x** |
| SELECT by PK | 0.601 | 0.546 | 1.10x |
| INSERT + commit | 34.46 | 33.52 | 1.03x |
| UPDATE + commit | 33.83 | 33.56 | 1.01x |
| DELETE + commit | 41.24 | 33.80 | 1.22x (bimodal commit, see below) |
| executemany 100 | 124.6 | 108.7 | 1.15x |
| executemany 1,000 | 359.7 | 266.1 | 1.35x |
| executemany 10,000 | 3,017.9 | 2,150.0 | **1.40x** |
| fetchone 10,000 rows | 51.20 | 40.55 | 1.26x |
| fetchmany 10,000 rows | 48.41 | 40.71 | 1.19x |
| fetchall 10,000 rows | 49.69 | 39.42 | 1.26x |

| Concurrency (PK SELECT, queries/s) | 1 worker | 8 workers | 32 workers |
|---|---:|---:|---:|
| pycubrid main, threads | 1,510 | 5,336 | 5,173 |
| pycubrid main, `pycubrid.aio` | 1,414 | 5,672 | 5,693 |
| CUBRIDdb, threads | 1,725 | 981 | 956 |

Observations:

- **Writes are commit-bound.** For every driver, single-row write latency (execute + commit)
  falls into discrete clusters at about 33, 41, 75 and 83 ms. This points to the server's log
  flush cadence, not the driver. The share of samples in each cluster differs between runs and
  drivers: for DELETE, pycubrid had 677 samples near 33 ms and 424 near 41 ms; CUBRIDdb had 755
  and 330. So the pooled median can land in a different cluster. The DELETE 1.22x ratio is that
  effect. This run cannot tell whether a slightly longer execute phase shifts samples into a
  later flush window.
- **`executemany()` is slower than CUBRIDdb's per-row loop.** pycubrid sends one
  `BatchExecutePacket`; CUBRIDdb runs one prepared `execute()` per row. pycubrid is still
  1.15-1.40x slower. This run does not split the time between client-side rendering and the
  server's batch execution. The finding needs its own investigation issue.
- **Python-heap memory of `executemany()` grows linearly**: about 380 bytes per row (3.6 MiB
  for 10,000 rows). This matches the documented "holds the whole batch in memory" limitation.
- **Fetch memory is bounded.** `fetchone`/`fetchmany` peak at about 130 KiB regardless of
  result size. `fetchall` peaks at 2.1 MiB, which is the result list itself; CUBRIDdb's
  `fetchall` is the same.
- **`fetch_size`** (100 / 1,000 / 10,000) makes no measurable difference for 10,000 rows over
  localhost: all medians are 48-50 ms.
- **Concurrency.** pycubrid threads scale about 3.5x from 1 to 8 workers. `pycubrid.aio` is
  about 6% slower than sync on a single connection and about 6-10% faster at 8-32 workers.
  CUBRIDdb throughput *drops* with more than one thread: p95 latency is 40 ms at 8 workers and
  197 ms at 32. Its calls appear to be serialised.

### Existing harness (continuity, pycubrid only, single run each)

`scripts/phase_decomposed_bench.py` (200 iterations, 50 warm-up) agrees with the run above for
reads: SELECT 10K total is 46.75 ms on main and 46.52 ms on 1.10.0. Its single-run write-phase
p50s move by up to ±30% between the two versions; that is the commit bimodality described
above. One run per version cannot support a regression decision, so these numbers are not used
for one.

### Not measured

| Case | Reason |
|---|---|
| Async for CUBRIDdb | CUBRIDdb has no async API |
| `fetch_size` sweep for CUBRIDdb | CUBRIDdb's Python API does not expose a fetch size |
| CUBRIDdb native (C/CCI) memory | `tracemalloc` only sees the Python heap; only the process `ru_maxrss` is recorded, and it cannot be split per operation |
| Per-operation RSS | only the process peak RSS is recorded, at the end of each trial |
| `executemany()` for UPDATE/DELETE/MERGE | only INSERT batches were measured |
| Time split of `executemany()` (client rendering vs server) | not instrumented in this run |
| `lastrowid` extra round trip | no case covers it |
| Remote network, TLS, LOB, collection types | localhost via docker-proxy only; plain types only |
| Other Python and CUBRID versions | the scope was one cell: CPython 3.12 × CUBRID 11.4 |
| `python/bench_pycubrid*.py` pytest suites | need a MySQL server (autouse fixture) and do not verify results |
| benchforge `driver-comparison` scenarios | benchforge has no CUBRIDdb adapter at `30ec73d` |

## Reproduction

```bash
# 1. Server (same image, port and database as this run)
docker run -d --name bench797-114 -e CUBRID_DB=testdb -p 33511:33000 cubrid/cubrid:11.4

# 2. Drivers: one Python 3.12 venv each
uv venv -p python3.12 venv-main    && uv pip install -p venv-main/bin/python    /path/to/pycubrid@2228d66
uv venv -p python3.12 venv-v1100   && uv pip install -p venv-v1100/bin/python   pycubrid==1.10.0
uv venv -p python3.12 venv-cubriddb
git clone --branch v11.3.0.51 --depth 1 --recurse-submodules \
  https://github.com/CUBRID/cubrid-python.git cubrid-python
(cd cubrid-python/cci-src && mkdir -p build_x86_64_release && cd build_x86_64_release \
  && cmake ../ && make -j"$(nproc)")
printf '#!/bin/bash\nexit 0\n' > cubrid-python/build_cci.sh   # as in sqlalchemy-cubrid ci.yml
uv pip install -p venv-cubriddb/bin/python setuptools ./cubrid-python
# On GCC 14 add: CFLAGS="-Wno-error=incompatible-pointer-types -Wno-error=int-conversion"

# 3. Run (5 trials per driver + continuity phase bench + summary)
PY_MAIN=$PWD/venv-main/bin/python PY_REL=$PWD/venv-v1100/bin/python \
PY_CUBRIDDB=$PWD/venv-cubriddb/bin/python \
MAIN_SHA=2228d663b6e2c9462c648b9dd941720219c464ae \
CUBRIDDB_SHA="c63ab9c51353e5c80e02dd2c1a19d4640acb29f5 (cci-src 7d1eb8f40f04089b8218d08e36e2c24a2de11b24)" \
experiments/pycubrid-release-baseline/run.sh \
  experiments/pycubrid-release-baseline/runs/2026-10-11_main-vs-1.10.0-vs-cubriddb 5
```
