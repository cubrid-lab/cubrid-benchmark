# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed
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
