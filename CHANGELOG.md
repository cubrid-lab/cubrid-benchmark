# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed
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
