# Contributing to cubrid-benchmark

Thank you for your interest in contributing to `cubrid-benchmark`. The maintained track measures
the CUBRID Python stack (pycubrid, sqlalchemy-cubrid, with CUBRIDdb as a same-server reference) on
CUBRID only; see `README.md` for the scope.

## Development Setup

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or `pip`)
- Docker
- Git
- Only for the Track B research suites: Node.js 20, Go 1.22, Rust 1.75

### Installation

```bash
git clone https://github.com/cubrid-lab/cubrid-benchmark.git
cd cubrid-benchmark
uv venv -p 3.12 .venv && . .venv/bin/activate
uv pip install -r requirements-dev.txt   # python/requirements.txt + offline test tools
```

## Running Benchmarks

### Start CUBRID (maintained track)

```bash
docker run -d --name cubrid-bench-114 -e CUBRID_DB=benchdb -p 33000:33000 cubrid/cubrid:11.4
python scripts/wait_for_cubrid.py --timeout 180
make seed
```

Set `CUBRID_PORT` (and `CUBRID_ORM_DSN=cubrid+pycubrid://dba@localhost:<port>/benchdb` for
SQLAlchemy) when you map another port.

### Run the Python benchmarks

```bash
make tier0                                                        # functional smoke
(cd python && pytest bench_pycubrid.py --benchmark-disable -q)    # verified, no timing
make tier1-python                                                 # timed Tier 1
make tier1-python-extended                                        # extended workloads
make tier2-python                                                 # SQLAlchemy Core/ORM
```

What each workload does and what is timed is in `docs/WORKLOADS.md`. A timed command is one
independent trial; follow `REPRODUCIBILITY.md` and `RUNBOOK.md` before reporting a result.

### Run the offline tests

```bash
pytest tests -q
```

### Track B research suites (optional)

They need the legacy CUBRID 11.2 + MySQL 8.0 stack:

```bash
make up            # docker compose -f docker/compose.yml up -d + readiness wait
make seed          # CUBRID; add MySQL with: python3 scripts/apply_schema.py --with-mysql
make tier0-ts tier1-ts tier0-go tier1-go tier0-rust tier1-rust
```

### Cleanup

```bash
docker rm -f cubrid-bench-114
make clean         # only if you started the compose stack
```

## Code Style

**Python** (on the files you change; some older files predate the formatter settings):

```bash
ruff check <changed files>
ruff format --check <changed files>
```

**Go** (Track B): `cd go && gofmt -d .`. The TypeScript suite has no linter configured.

## Pull Request Guidelines

1. Keep changes focused and explain the motivation in the PR description.
2. Add or update tests (`tests/`) and, for a workload change, its contract in `docs/WORKLOADS.md`.
3. Ensure the offline tests and the lint checks above pass.
4. For performance claims, include the run folder or workflow run and follow
   `REPRODUCIBILITY.md` (same measured identity; at least three independent runs before any
   regression claim). Do not present a comparison with another database engine as a goal.
5. Update `CHANGELOG.md` for user-visible changes.
6. Agent-authored PRs follow the review route in [`AGENTS.md`](AGENTS.md#review-route-for-standalone-agents-and-humans):
   a standalone agent's PR needs a substantive submitted GitHub Copilot review or a review by a
   maintainer other than the author, recorded on the PR (maintainer decision, 2026-10-15). Other
   people's and other AI agents' reviews do not satisfy that route, and AI review is limited to
   two published rounds per PR. Changes to `.github/workflows/bench.yml` and to
   `scripts/file_regression_issue.py`, which write to GitHub, need a review that explicitly
   assesses them, confirmed by a maintainer. Human contributors follow the normal review process and need no AI tools.

## Reporting Issues

When filing an issue, include:

- Workload and file affected (for example `python/bench_pycubrid.py::select_by_pk`)
- CUBRID server version and image digest
- pycubrid / sqlalchemy-cubrid versions (or source commit) and Python version
- The run folder or workflow run with raw results
- What you expected and what you measured
