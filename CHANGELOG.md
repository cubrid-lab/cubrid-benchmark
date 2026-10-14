# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Documentation
- Current-facing docs describe the maintained scope: a same-server regression laboratory for
  pycubrid and sqlalchemy-cubrid with CUBRIDdb as a reference, not a cross-database race.
  - `README.md` lists current experiments with their actual statuses and runs, and a separate
    historical cross-database table with dates, pinned versions, run folders and limitations. It
    also has a copyable CUBRID-only quick start and explains trials vs rounds and the metric
    labels.
  - `AGENTS.md`, `CONTRIBUTING.md`, `docs/METHODOLOGY.md` and `REPRODUCIBILITY.md` drop vanished
    commands, mark the "4.5–6× slower than MySQL" target and the `pycubrid / PyMySQL` ratio as
    historical, correct that PyMySQL is pure Python, and separate the current lanes from the
    historical compose environment.
  - `BENCHMARK_PLAN.md`, `PRD.md` and `ROADMAP.md` carry historical-status banners. Links and
    the draft-issue target default (`TARGET_REPO`, `gap_to_issue.py`) use the `cubrid-lab`
    organization.
  - `tests/test_docs.py` checks links, documented `make` targets, organization URLs and the
    historical framing.

### Changed
- The maintained Python workloads have explicit scenario contracts (`SCENARIOS` in each module,
  listed in `docs/WORKLOADS.md`, checked by `tests/test_workload_contracts.py`).
  - **Extended workloads (`tier1-extended-v2`).** `prepared_statement` is now
    `execute_parameterized_loop`: it was an ordinary `execute` loop, not prepared-statement
    reuse. `batch_insert` is now `executemany_literal_batch` (FC20), and there is a new opt-in
    `executemany_prepared_batch` (`executemany(prepared=True)`, FC2 + FC21, with
    `BENCH_EXECUTEMANY_PREPARED=1`). `connect_disconnect` is now `connect_query_close` and
    `concurrent_select` is now `concurrent_select_4_threads`. Seeding is untimed, every row
    is verified, and an untimed packet probe proves each claimed protocol path and records the
    counts in `extra_info`.
  - **SQLAlchemy Tier 2 (`tier2-sqlalchemy-v2`).** Clearing is untimed, results are verified,
    and a statement probe records the SQL each scenario issued. On the current dialect, ORM
    bulk inserts send one INSERT per row. The scenarios are renamed to say what they compare,
    and they are not equivalent pairs.
  - Results under the old names are a historical series.
- Regression reports now go through one evidence decision, `scripts/regression_evidence.py`.
  Every run directory is one independent trial, and repeated runs or copies are excluded. The
  decision is one of `error`, `not_comparable` (including unpinned or dirty source and mixed
  software), `insufficient_evidence` (fewer than 3 trials a side), `equivalent`, `investigate`
  or `reproduced_regression`. A reproduced regression is a median decline of at least 10% with
  every candidate trial below every baseline trial. `nightly_compare.py` reports the decision
  (`evidence=`) and sets `open_issue` only for a reproduced regression, so a single run never
  qualifies. `make gap-issues` and `gap_to_issue.py` draft only from an eligible evidence
  report and no longer accept a compare report that merely claims replications. The new
  `scripts/file_regression_issue.py` plans by default, files only into this repository with
  dedup by key, and treats a `gh` failure as an error. No workflow calls it.
- The workflow measures pinned current releases instead of only pycubrid 1.0.0.
  `python/constraints-current.txt` pins pycubrid 1.12.0, sqlalchemy-cubrid 1.11.0, SQLAlchemy
  2.1.4 and pytest-benchmark 5.3.0, and `python/lanes.json` defines the cells:
  `representative` (weekly and manual, Python 3.12 × CUBRID 11.4), `release` (manual, Python 3.11
  and 3.14 × CUBRID 10.2 and 11.4) and `archival` (manual, the legacy pycubrid 1.0.0 matrix,
  labelled as not current support evidence). The old `matrix` lane is replaced by `archival`.
- Each cell checks its installed versions before measuring (`scripts/python_lanes.py
  verify-install`): exact pins, index installs, `pycubrid.__version__` and the dialect's
  SQLAlchemy range. An optional `pycubrid_source_sha` input measures a pycubrid commit, and its
  `direct_url.json` commit is checked. Each cell writes `versions.json` and `cell.json`.
  `scripts/generate_manifest.py --glob/--expect-cell` builds the manifest and fails when a
  planned cell is missing.
- The maintained Python track is CUBRID-only. `make tier0`, `tier1-python`,
  `tier1-python-extended`, `tier2-python`, `bench-verify` (and so `make all`) run only the pycubrid
  and sqlalchemy-cubrid modules. `make seed` seeds CUBRID only (`apply_schema.py --with-mysql`
  also seeds MySQL). `python/conftest.py` no longer opens a MySQL connection or creates MySQL
  tables for every session; `mysql_conn` is created only when a historical module requests it.
  PyMySQL left `python/requirements.txt`. The historical PyMySQL sources stay at their paths and
  run only through the opt-in `make historical-python-mysql`, after a separate
  `pip install -r python/requirements-historical-mysql.txt`. That target runs the archived modules
  at the current revision; it is not a protocol-accurate reproduction of the archived
  experiments. The CUBRID-only workflow lanes now use the
  repository conftest. Historical cross-database experiments are unchanged. This is a scope
  decision, not a claim about MySQL or PyMySQL performance.

### Fixed
- `scripts/normalize_results.py` and `scripts/emit_benchforge_v2.py` no longer write
  `mean + z * stddev` normal-model estimates as `p95_latency` / `p99_latency`. Those keys now hold
  observed percentiles of pytest-benchmark's per-round samples (`stats.data`, seconds per call
  within a round; Hyndman-Fan type 7), only with at least 20 (p95) or 100 (p99) samples. Each
  result records
  `latency_samples`, `latency_sample_source` and `latency_quantile_method`, or
  `latency_percentiles_unavailable` (Python Tier 1 has 5 rounds, so it reports unavailable).
  Non-finite or non-positive means and throughput, negative stddev and invalid samples now fail
  the conversion (`normalize_results.py` exits `2`). Compatibility: consumers must treat a missing
  `p95_latency` / `p99_latency` as unavailable. Historical values under these names were
  estimates and stay unchanged in their run folders.
- The benchmark workflow runs on a bounded schedule again. The daily cross-language nightly and
  the weekly matrix are replaced by one weekly CUBRID-only `representative` lane (CUBRID 11.4:
  Tier 0, pycubrid Tier 1, provenance, report-only comparison). The cross-language suite
  (`lane=full`) and the matrix (`lane=matrix`) now run only through manual dispatch. Pull
  requests and pushes that touch benchmark code run offline tests, workflow policy checks and a
  CUBRID-only correctness smoke; docs-only changes run nothing.
- Every job has a timeout, every action is pinned to a commit SHA, and the token is read-only
  except for the manual full suite's gh-pages publish. No lane opens regression issues.
- Uploads keep whatever was produced (`if-no-files-found: warn`) instead of adding "no files
  found" failures after the first error. `scripts/check_required_results.py` fails a lane whose
  required measurement is missing. Optional full-suite tracks are reported instead of stopping
  the run. `inject_failure` checks this behaviour on demand. The scheduler state
  (`disabled_inactivity` since 2026-06-22) is recorded in `RUNBOOK.md`.

### Added
- `requirements-dev.txt` for the offline tests (PyYAML, jsonschema format checks),
  `scripts/wait_for_cubrid.py` and `tests/test_workflow_policy.py`.
- The nightly comparison no longer copies the historical baseline's `comparable_group` into its
  candidate. `scripts/run_provenance.py capture` measures the candidate's own identity: runner
  image, CPU, OS/kernel, Docker, CUBRID image digest and build, Python and the workload
  definition. Driver and source versions are recorded separately as `software_under_test`, and
  `comparable_group` is derived from the identity. `scripts/compare_runs.py` now reports
  `not_comparable` (exit code `3`, no verdict) for missing, blank, copied or edited groups,
  different identities, wrong roles, and operation or unit mismatches. It also rejects result
  files that repeat a benchmark name. The nightly workflow selects only an identical-identity
  baseline (`scripts/nightly_compare.py`), reports `not comparable / needs fresh baseline`
  otherwise, and opens a regression issue only for a comparable regression. `make compare` keeps
  historical pairs usable with the explicit `--allow-legacy-group-match` flag.
- Python Tier 1 (`python/bench_pycubrid.py`) now measures actual CRUD rows. The table uses
  explicit primary keys `1..10000` instead of `AUTO_INCREMENT`, every round (warm-up included)
  starts from a verified seeded state built outside the timed region, and every SELECT row,
  UPDATE/DELETE affected-row count and final table state is checked; a miss fails the run.
  Before this fix every measured SELECT-by-PK/UPDATE/DELETE round after the warm-up addressed
  keys that no longer existed. Tier 1 results recorded before this change are a different
  workload and must not be compared with the corrected series
  (`experiments/tier1-python-workload`). Requires `pytest-benchmark>=5.0` (pedantic `teardown`).

## [0.1.0] - 2026-03-17

### Added
- Initial project scaffolding
- Multi-language benchmark suite for CUBRID database
- Python, TypeScript, and Go benchmark implementations
- Docker setup for test environment
- Schema and test data management
- Benchmark result collection and reporting
