# CUBRID Benchmark Suite

<!-- BADGES:START -->
![CI](https://github.com/cubrid-lab/cubrid-benchmark/actions/workflows/bench.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
[![GitHub stars](https://img.shields.io/github/stars/cubrid-lab/cubrid-benchmark)](https://github.com/cubrid-lab/cubrid-benchmark)
![status](https://img.shields.io/badge/status-active%20development-yellow)
<!-- BADGES:END -->

A regression laboratory for the CUBRID Python stack. It makes reproducible, same-server
measurements of [pycubrid](https://github.com/cubrid-lab/pycubrid) and
[sqlalchemy-cubrid](https://github.com/cubrid-lab/sqlalchemy-cubrid) (raw driver, Core, ORM),
with the official C-extension driver CUBRIDdb as a same-server reference.

The goal is **measurable regression avoidance and controlled experiments**:

- Is a release or a source revision slower than its baseline on the same CUBRID server, with
  the same workload?
- Where does the time go?

It is not a race against another database engine. It is also not a claim that pycubrid matches
or beats CUBRIDdb on every workload. Each result says which environment, workload and software
it measured (`REPRODUCIBILITY.md`).

## Current Experiments (same CUBRID server)

| Experiment | Status | Question | Runs | Report |
|---|---|---|---|---|
| pycubrid vs CUBRIDdb | active | Same-server driver comparison and optimization targets (CUBRID 11.2) | 2026-03-27 (before and after parse optimization) | [Report](experiments/driver-comparison/README.md) |
| Row-count sweep | active | SELECT fetch scaling by result size (100–10K rows) | 2026-03-27 | [Report](experiments/row-count-sweep/README.md) |
| ORM overhead | active | Raw pycubrid vs SQLAlchemy Core vs ORM (CUBRID 11.2) | 2026-03-28 to 2026-04-22, 5 runs (incl. multi-trial and native-ping) | [Report](experiments/orm-overhead/README.md) |
| pycubrid post-1.0 optimization | completed | Gains from protocol-level optimizations | 2026-04-12 (before/after) | [Report](experiments/pycubrid-post-1.0-optimization/README.md) |
| pycubrid 1.0 baseline | completed | The pycubrid 1.0.0 baseline | 2026-04-12 | [Report](experiments/pycubrid-1.0-baseline/README.md) |
| pycubrid release baseline | active | pycubrid main vs 1.10.0 vs CUBRIDdb on CUBRID 11.4 | 2026-10-11 | [Report](experiments/pycubrid-release-baseline/README.md) |
| pycubrid executemany overhead | active | executemany() INSERT/UPDATE/DELETE/MERGE vs CUBRIDdb on CUBRID 11.4 | 2026-10-11 | [Report](experiments/pycubrid-executemany-overhead/README.md) |
| pycubrid prepared batch paths | completed | EXECUTE_BATCH vs prepared loop vs EXECUTE_ARRAY on CUBRID 10.2–11.4 | 2026-10-11 | [Report](experiments/pycubrid-prepared-batch/README.md) |
| Python Tier 1 workload correction | active | Tier 1 with explicit keys and verified rows; new baseline | 2026-10-14 | [Report](experiments/tier1-python-workload/README.md) |
| Pinned-release comparison demo | completed | Candidate-to-baseline mechanics of the pinned lanes (not a performance result) | 2026-10-14 | [Report](experiments/pycubrid-pinned-lane-demo/README.md) |

Results recorded before the Tier 1 workload correction (2026-10-14) timed a different workload.
They stay as historical evidence and are not comparable with the corrected series. See each
report's environment and run folders for the exact versions.

## Historical Experiments (cross-database, no longer maintained)

These compared CUBRID with MySQL. Their results are preserved unchanged at their original paths.
Each one measures the **combined DBMS and driver stack** (CUBRID + pycubrid vs MySQL + PyMySQL).
That design cannot isolate driver efficiency, and its results are not current engineering
targets. Both pycubrid and PyMySQL are pure-Python drivers.

| Experiment | Date | Pinned versions | Run folder | Known limitations |
|---|---|---|---|---|
| [Baseline (multi-language)](experiments/baseline-multilang/README.md) | 2026-03-16 | CUBRID 11.2, MySQL 8.0; pycubrid 0.5.0, PyMySQL (version not recorded), cubrid-client 1.1.0, cubrid-go 0.2.1; CPython 3.10.12 | `experiments/baseline-multilang/runs/2026-03-16_initial` | pycubrid `fetchall()` bug affected SELECT results; pre-correction Tier 1 workload; DBMS and driver mixed |
| [pycubrid vs PyMySQL (benchforge)](experiments/benchforge-pycubrid-vs-pymysql/README.md) | 2026-03-27 | CUBRID 11.2, MySQL 8.0; pycubrid 0.5.0, PyMySQL 1.1.2; CPython 3.12.8 | `experiments/benchforge-pycubrid-vs-pymysql/runs/2026-03-27_initial` | DBMS and driver mixed; single shared devbox |

The Go, TypeScript and Rust suites ("Track B") are separate research, run only through the
manual `lane=full` workflow run, and are not part of the Python routine lanes. The Go
(`go/`) and TypeScript (`typescript/`) suites still run CUBRID next to MySQL. The Rust suite
(`rust/`) measures CUBRID only.

## Quick Start (CUBRID only)

Prerequisites: Docker, Python 3.11+ and [uv](https://docs.astral.sh/uv/). `pip` works too.

```bash
git clone https://github.com/cubrid-lab/cubrid-benchmark.git
cd cubrid-benchmark
docker run -d --name cubrid-bench-114 -e CUBRID_DB=benchdb -p 33000:33000 cubrid/cubrid:11.4
uv venv -p 3.12 .venv && . .venv/bin/activate
uv pip install -r requirements-dev.txt        # benchmark + offline test dependencies
python scripts/wait_for_cubrid.py --timeout 180
make seed                                      # schema and seed data on CUBRID
make tier0                                     # CUBRID-only functional smoke
(cd python && pytest bench_pycubrid.py --benchmark-disable -q)   # verified Tier 1, no timing
make tier1-python                              # timed Tier 1 -> results/python_tier1.json
pytest tests -q                                # offline tests (no database needed)
docker rm -f cubrid-bench-114
```

Use `CUBRID_PORT` (and `CUBRID_ORM_DSN` for `make tier2-python`) if port 33000 is taken. One
timed command is one independent trial. The pedantic rounds inside it are not separate
trials, and a regression claim needs at least three independent trials per side
(`RUNBOOK.md`, "Regression evidence and draft issues").

The maintained Python track needs neither MySQL nor PyMySQL.
`make historical-python-mysql` only runs the archived PyMySQL and SQLAlchemy+MySQL pytest modules
at the current revision. It needs a MySQL 8.0 server and a prior
`pip install -r python/requirements-historical-mysql.txt`. It is opt-in, is not part of
`make all` or CI, and is not a protocol-accurate reproduction of the historical experiments.
Retiring the active comparison is a project-scope decision, not evidence that MySQL or PyMySQL is
slow or broken.

The legacy cross-language stack (`docker/compose.yml`: CUBRID 11.2 + MySQL 8.0) starts with
`make up` and is used by the Go/TypeScript suites and `make all` (which runs the Python,
TypeScript and Go targets, not Rust).

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

## Drivers

| Track | Driver | Role |
|-------|--------|------|
| Maintained (Python) | [pycubrid](https://github.com/cubrid-lab/pycubrid) | driver under test (pinned in `python/constraints-current.txt`) |
| Maintained (Python) | [sqlalchemy-cubrid](https://github.com/cubrid-lab/sqlalchemy-cubrid) | SQLAlchemy dialect under test (Core/ORM) |
| Maintained (Python) | [CUBRIDdb](https://github.com/CUBRID/cubrid-python) | C-extension same-server reference driver (experiments only; no requirement to beat it) |
| Historical (Python) | PyMySQL | archived cross-database comparison only |
| Research (Track B) | [cubrid-client](https://github.com/cubrid-lab/cubrid-client), [cubrid-go](https://github.com/cubrid-lab/cubrid-go), [cubrid-rs](https://github.com/cubrid-lab/cubrid-rs) | separate Go/TypeScript/Rust suites, next to mysql2 and go-sql-driver/mysql |

## Tier Model

| Tier | Description | Status |
|------|-------------|--------|
| 0 | Functional smoke (connect + CRUD) | ✅ CUBRID-only in PR checks and pinned lanes |
| 1 | Driver throughput (sequential ops), verified rows | ✅ `python/bench_pycubrid.py`, `python/bench_pycubrid_extended.py` |
| 2 | SQLAlchemy Core/ORM workloads | ✅ `python/bench_sqlalchemy_cubrid.py` (manual) |
| 3/4 | Concurrency and soak stability | Planned |

Workload contracts (what one call does, rows per call, timed region and verified protocol) are in
[docs/WORKLOADS.md](docs/WORKLOADS.md). `ops` is calls per second. `p95_latency`/`p99_latency`
appear only as observed percentiles with enough samples (`docs/METHODOLOGY.md`). The
`peak_rss_delta_kb` from `scripts/collect_metrics.py` is a child-process RSS delta whose
multi-child semantics are still being settled (#32).

## Links

- [Roadmap](ROADMAP.md)
- [Project Board](https://github.com/orgs/cubrid-lab/projects/2)
- [CUBRID Lab](https://github.com/cubrid-lab)

## Docs

- [Benchmark Methodology](docs/METHODOLOGY.md)
- [Python Workload Contracts](docs/WORKLOADS.md)
- [Benchmark Reproducibility Policy](REPRODUCIBILITY.md)
- [Benchmark Runbook](RUNBOOK.md)
- [Contributing](CONTRIBUTING.md)

## Disclaimer

This project is part of [CUBRID Lab](https://github.com/cubrid-lab), an independent open-source initiative for CUBRID developer tooling, and is not affiliated with, sponsored by, or endorsed by CUBRID Corporation or the official CUBRID project.

## License

MIT
