# CUBRID Benchmark Suite

<!-- BADGES:START -->
![CI](https://github.com/cubrid-labs/cubrid-benchmark/actions/workflows/bench.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
[![GitHub stars](https://img.shields.io/github/stars/cubrid-labs/cubrid-benchmark)](https://github.com/cubrid-labs/cubrid-benchmark)
![status](https://img.shields.io/badge/status-active%20development-yellow)
<!-- BADGES:END -->

Scientific benchmark suite for CUBRID database ecosystem — reproducible experiments, automated comparison.

## Experiments

| # | Experiment | Status | Question | Latest Run | Report |
|---|---|---|---|---|---|
| 1 | Baseline (multi-language) | completed | CUBRID vs MySQL across Python/Go/TypeScript | 2026-03-16 | [Report](experiments/baseline-multilang/README.md) |
| 2 | pycubrid vs PyMySQL | completed | Cross-database driver comparison | 2026-03-27 | [Report](experiments/benchforge-pycubrid-vs-pymysql/README.md) |
| 3 | pycubrid vs CUBRIDdb | active | Same-database driver comparison, optimization targets | 2026-03-27 | [Report](experiments/driver-comparison/README.md) |
| 4 | Row-count sweep | active | SELECT fetch scaling by result set size (100–10K rows) | 2026-03-27 | [Report](experiments/row-count-sweep/README.md) |
| 5 | ORM overhead | scaffold | Raw pycubrid vs SQLAlchemy Core vs SQLAlchemy ORM | — | [Report](experiments/orm-overhead/README.md) |
| 6 | pycubrid 1.0 baseline | completed | Official pycubrid 1.0.0 baseline after PR #55 optimizations | 2026-04-12 | [Report](experiments/pycubrid-1.0-baseline/README.md) |
| 7 | pycubrid release baseline | active | pycubrid main vs 1.10.0 vs CUBRIDdb on CUBRID 11.4 (pycubrid#797) | 2026-10-11 | [Report](experiments/pycubrid-release-baseline/README.md) |
| 8 | Python Tier 1 workload correction | active | Tier 1 with explicit keys and verified rows; new baseline, not comparable with earlier Tier 1 runs | 2026-10-14 | [Report](experiments/tier1-python-workload/README.md) |
| 9 | Pinned-release comparison demo | completed | Candidate-to-baseline mechanics for the pinned lanes (not a performance result) | 2026-10-14 | [Report](experiments/pycubrid-pinned-lane-demo/README.md) |

## Quick Start

```bash
make up        # Start CUBRID + MySQL Docker containers (MySQL serves the TypeScript/Go tracks)
make seed      # Apply schema to CUBRID
make all       # Run all benchmarks
```

The maintained Python track measures pycubrid and sqlalchemy-cubrid on the same CUBRID server and
needs neither MySQL nor PyMySQL. The earlier pycubrid-vs-PyMySQL runs stay as historical
experiments; reproduce them from the commands and revisions recorded in their experiment folders.

`make historical-python-mysql` only runs the archived PyMySQL and SQLAlchemy+MySQL pytest modules
at the current revision. It needs a MySQL 8.0 server and a prior
`pip install -r python/requirements-historical-mysql.txt`. It is opt-in, is not part of
`make all` or CI, and is not a protocol-accurate reproduction of those experiments.

Retiring the active comparison is a project-scope decision, not evidence that MySQL or PyMySQL is
slow or broken.

## Measured Python Versions

**Supported is not the same as measured.** pycubrid supports Python 3.11-3.15 and CUBRID
10.2-11.4, and sqlalchemy-cubrid supports SQLAlchemy 2.0-2.1. The workflow measures the pinned
releases in `python/constraints-current.txt` (currently pycubrid 1.12.0, sqlalchemy-cubrid
1.11.0, SQLAlchemy 2.1.4) on the cells in `python/lanes.json`:

| Lane | Trigger | Cells (Python × CUBRID) |
|------|---------|-------------------------|
| `representative` | weekly schedule and manual dispatch | 3.12 × 11.4 |
| `release` | manual dispatch | 3.11 and 3.14 × 10.2 and 11.4 |
| `archival` | manual dispatch | 3.10, 3.11, 3.12 × 11.2 with pycubrid 1.0.0 (legacy reproduction, not current support evidence) |

Every cell installs the exact pins and verifies them before measuring. The check covers
`importlib.metadata`, `pycubrid.__version__` and the SQLAlchemy range of the dialect. A
`pycubrid_source_sha` dispatch input measures a pycubrid commit instead, verified through its
`direct_url.json`. A silently upgraded package or a failed source install fails the cell.

Each cell measures only the correctness-verified pycubrid Tier 1 workload
(`python/bench_pycubrid.py`). It then records measured provenance and a report-only baseline
comparison, and uploads `cell.json`. `pinned-manifest` builds `manifest.json` and fails when a
planned cell is missing.

To change what is measured, edit `python/constraints-current.txt` for a new published release
or `python/lanes.json` for cells, then run `gh workflow run bench.yml -f lane=release`. See
`RUNBOOK.md`, "Scheduled and manual benchmark runs".

## Drivers Tested

| Language | CUBRID Driver | MySQL Driver |
|----------|--------------|--------------|
| Python | [pycubrid](https://github.com/cubrid-labs/pycubrid) and sqlalchemy-cubrid | — (PyMySQL: historical reproduction only) |
| Python | [CUBRIDdb](https://github.com/cubrid/cubrid-python) (C ext, same-server reference driver) | — |
| TypeScript | [cubrid-client](https://github.com/cubrid-labs/cubrid-client) v1.1.0 | mysql2 |
| Go | [cubrid-go](https://github.com/cubrid-labs/cubrid-go) v0.2.1 | go-sql-driver/mysql |

## Tier Model

| Tier | Description | Status |
|------|-------------|--------|
| 0 | Functional smoke (connect + CRUD) | ✅ |
| 1 | Driver throughput (sequential ops) | ✅ |
| 2 | ORM overhead vs raw driver | Planned |
| 3/4 | Concurrency and soak stability | Planned |

## Links

- [Roadmap](ROADMAP.md)
- [Methodology](docs/METHODOLOGY.md)
- [Project Board](https://github.com/orgs/cubrid-labs/projects/2)
- [CUBRID Ecosystem](https://github.com/cubrid-labs)

## Docs

- [Benchmark Methodology](docs/METHODOLOGY.md)
- [Benchmark Reproducibility Policy](REPRODUCIBILITY.md)
- [Benchmark Runbook](RUNBOOK.md)

## Disclaimer

This project is part of [CUBRID Lab](https://github.com/cubrid-lab), an independent open-source initiative for CUBRID developer tooling, and is not affiliated with, sponsored by, or endorsed by CUBRID Corporation or the official CUBRID project.

## License

MIT
