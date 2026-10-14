# Python Tier 1 workload correction

> **Status**: active
> **Question**: What does pycubrid Python Tier 1 measure once every SELECT, UPDATE and DELETE is
> proven to reach the intended rows, and what baseline does the corrected workload start?
> **Tracking issue**: [#26](https://github.com/cubrid-lab/cubrid-benchmark/issues/26)

## Why the workload changed

Before workload revision `tier1-explicit-keys-v2`, `python/bench_pycubrid.py` created
`bench_driver_pycubrid` with an `AUTO_INCREMENT` key, and every pedantic round ran
`DELETE` + 10,000 `INSERT`s and then addressed the hard-coded keys `1..10000` (SELECT) or
`1..1000` (UPDATE/DELETE). A `DELETE` does not reset the `AUTO_INCREMENT` counter. The table
is created once per test, so the warm-up round got keys `1..10000` and the measured rounds got
`10001..20000`, `20001..30000` and so on. Every measured SELECT-by-PK, UPDATE and DELETE round
therefore addressed keys that no longer existed. No result or row count was checked, so the
misses produced throughput numbers anyway. Every round also timed the re-seed.

`auto_increment_proof.py` replays that shape on a 5-row table. The output on both servers
(`runs/2026-10-14_explicit-keys-baseline/raw/auto_increment_proof_*.json`):

| Server | Round | Generated ids | SELECT hits for keys 1-5 | UPDATE rowcount | DELETE rowcount |
|--------|------:|---------------|------------------------:|----------------:|----------------:|
| CUBRID 11.4.6.1963 | 1 | 1-5 | 5 | 5 | 5 |
| CUBRID 11.4.6.1963 | 2 | 6-10 | 0 | 0 | 0 |
| CUBRID 10.2.18.9024 | 1 | 1-5 | 5 | 5 | 5 |
| CUBRID 10.2.18.9024 | 2 | 6-10 | 0 | 0 | 0 |

## Corrected protocol (`tier1-explicit-keys-v2`)

- Table `bench_driver_pycubrid (id INT PRIMARY KEY, name VARCHAR(100), amount INT)` with
  explicit keys `1..10000`. There is no `AUTO_INCREMENT` column.
- An untimed pedantic `setup` rebuilds and verifies the starting state before every round,
  warm-up included: an empty table for INSERT, 10,000 seeded rows for everything else.
- Timed region: only the measured statements and, for INSERT/UPDATE/DELETE, the one final
  `commit()`. The `DELETE` of previous contents, the re-seed, the verification and the `DROP`
  are not timed. Each round's time is therefore a different quantity from the pre-fix series.
- An untimed pedantic `teardown` checks every SELECT row (id and payload), that every
  UPDATE/DELETE affected exactly one row, and the final `COUNT/MIN/MAX/SUM`. A miss raises
  `WorkloadVerificationError` and fails the benchmark.
- `tests/test_tier1_workload.py` runs the same functions offline (SQLite stands in for the
  server) through the real pedantic loop: first round, warm-up, back-to-back runs and wrong-key
  cases that must fail.

| Operation | Rows per measured round | Statements in the timed region |
|-----------|------------------------:|--------------------------------|
| insert_sequential | 10,000 | 10,000 INSERT + 1 commit |
| select_by_pk | 10,000 | 10,000 SELECT + fetchone |
| select_full_scan | 10,000 | 1 SELECT (no ORDER BY) + fetchall |
| update_indexed | 1,000 | 1,000 UPDATE + 1 commit |
| delete_sequential | 1,000 | 1,000 DELETE + 1 commit |

## Results: 2026-10-14_explicit-keys-baseline (CUBRID 11.4)

pycubrid 1.12.0, CPython 3.12.13, CUBRID 11.4.6.1963. There were 3 trials of 5 measured rounds
each. The table shows the median of the three per-trial median round times, and rows per second
at that median. The machine was shared, and trial 2 ran during a load spike (see `run.yaml`), so
treat the spread as real noise.

| Operation | Per-trial median round (s) | Median (s) | Rows/s at median |
|-----------|----------------------------|-----------:|-----------------:|
| insert_sequential | 8.113 / 14.377 / 7.568 | 8.113 | 1,233 |
| select_by_pk | 7.438 / 11.034 / 6.815 | 7.438 | 1,344 |
| select_full_scan | 0.0341 / 0.0338 / 0.0483 | 0.0341 | 293,052 |
| update_indexed | 0.579 / 0.836 / 0.778 | 0.778 | 1,285 |
| delete_sequential | 0.616 / 0.728 / 0.720 | 0.720 | 1,388 |

A single correctness smoke on CUBRID 10.2.18.9024 (`raw/cubrid102_smoke_t1.json`) passed every
verification. Its timings come from another server and are not part of this comparable group.

## Run history

| Run ID | Date | Label | Comparable Group | Compares To | Key Finding |
|--------|------|-------|------------------|-------------|-------------|
| 2026-10-14_explicit-keys-baseline | 2026-10-14 | explicit-keys-baseline | devbox-i5-9400F-linux5.15.0-198-docker29.2.1-cubrid114-tier1-explicit-keys-v2 | — | First baseline of the corrected workload; every round verified |

## Not comparable

Tier 1 numbers recorded before this revision are historical evidence of a different workload.
That includes `driver-comparison`, `pycubrid-1.0-baseline`, `pycubrid-post-1.0-optimization`,
the `baseline-multilang` Python Tier 1 rows and any nightly `python_tier1.json`. Do not compare
them with this series as proof of a regression or improvement. `python/bench_pymysql.py` was not
changed: it is a historical mirror whose active use is being retired separately.

## Reproduction

`python/conftest.py` still opens a MySQL connection for every Python benchmark session, so these
runs load only the CUBRID fixture in `cubrid_only_plugin.py`:

```bash
docker run -d --name bench26-cubrid-114 -e CUBRID_DB=benchdb -p 33641:33000 cubrid/cubrid:11.4
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r python/requirements.txt
.venv/bin/python experiments/tier1-python-workload/auto_increment_proof.py --port 33641
cd python && CUBRID_PORT=33641 PYTHONPATH=../experiments/tier1-python-workload \
  ../.venv/bin/python -m pytest --noconftest -p cubrid_only_plugin bench_pycubrid.py \
  --benchmark-json=../experiments/tier1-python-workload/runs/<run-id>/raw/cubrid114_t1.json -v
```
