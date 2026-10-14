# Benchmark Methodology

How the CUBRID Benchmark suite measures and compares database performance.

## Overview

```mermaid
flowchart TD
    Trigger[Trigger: Schedule / Manual / PR] --> Setup[Setup Phase]
    Setup --> Docker[Start CUBRID + MySQL containers]
    Docker --> Schema[Apply schema + seed data]
    Schema --> Tier0{Tier 0: Smoke Tests}
    Tier0 -->|Pass| Tier1[Tier 1: Full Benchmarks]
    Tier0 -->|Fail| Abort[Abort — connectivity issue]
    Tier1 --> Normalize[Normalize Results]
    Normalize --> Publish[Publish to gh-pages]
    Publish --> Report[Generate Report + Charts]
```

## Tier Structure

### Tier 0 — Smoke Tests

**Purpose**: Verify database connectivity and basic operation before running expensive benchmarks.

**What it tests**:
- TCP connectivity to both CUBRID (port 33000) and MySQL (port 3306)
- Basic `INSERT` / `SELECT` / `UPDATE` / `DELETE` operations
- Schema compatibility

**Languages**: Python, TypeScript, Go

**Runtime**: ~30 seconds

**When it runs**: CUBRID-only on every PR and push that touches benchmark code (`cubrid-smoke`), in
the weekly `representative` lane, and in every manual full-suite run.

### Tier 1 — Full Benchmarks

**Purpose**: Measure real performance characteristics under controlled conditions.

**What it tests**:
- Bulk `INSERT` throughput
- Primary key `SELECT` latency
- Range `SELECT` with filtering
- `UPDATE` throughput
- Mixed read/write workloads
- Extended connection and concurrency workloads

**Languages**: Python, TypeScript, Go

**Runtime**: ~10-15 minutes

**When it runs**: pycubrid Tier 1 in the weekly `representative` lane; the cross-language Tier 1
only through a manual `workflow_dispatch` with `lane=full` (never on PRs).

## Database Setup

Both databases run as Docker containers on the CI runner:

| Database | Image | Port | Credentials |
|----------|-------|------|-------------|
| CUBRID | `cubrid/cubrid:11.2` | 33000 | `dba` (no password) |
| MySQL | `mysql:8.0` | 3306 | `root` / `bench` |

Both use the same database name (`benchdb`) and identical schema, ensuring a fair comparison.

### Schema

The benchmark schema is applied via `scripts/apply_schema.py`:
- Creates identical tables in both databases
- Seeds with the same initial data
- Ensures indexes match between CUBRID and MySQL

## Metrics

### Primary Metric: ops/sec

All results are normalized to **operations per second** for consistent comparison.

```mermaid
flowchart LR
    Raw[Raw pytest-benchmark JSON] --> Normalize[normalize_results.py]
    Normalize --> Ops["ops/sec per benchmark"]
    Ops --> Compare["CUBRID vs MySQL ratio"]
```

### Secondary Metrics: Tail Latency and Memory

- `p95_latency` and `p99_latency` are estimated from benchmark mean/stddev
- Child-process peak RSS is captured for Python benchmark commands
- Extended benchmark artifacts include both throughput and memory metadata

### Conversion Logic

From `pytest-benchmark` output:
1. If `stats.ops` is available → use directly
2. If only `stats.mean` is available → compute `1 / mean`
3. Include `stddev` as `+/- X%` range
4. Approximate P95 latency: `mean + 1.645 * stddev`
5. Approximate P99 latency: `mean + 2.326 * stddev`

### Result Format

Normalized results follow the `github-action-benchmark` schema:
```json
{
    "name": "bench_insert_bulk",
    "unit": "ops/sec",
    "value": 95.4321,
    "range": "+/- 3.2%",
    "p95_latency": 0.024112341,
    "p99_latency": 0.028873441,
    "extra": "language=python tier=1 framework=pytest-benchmark"
}
```

## Extended Workloads

Nightly Tier 1 extended runs include four additional workloads in Python, TypeScript, and Go.

```mermaid
flowchart LR
    CD[connect_disconnect] --> PREP[prepared_statement]
    PREP --> BATCH[batch_insert]
    BATCH --> CONC[concurrent_select]
```

- `connect_disconnect`: 100 connect/ping/close cycles to isolate connection overhead
- `prepared_statement`: Reuse one prepared cursor/statement for 1000 point-select operations
- `batch_insert`: Insert 1000 rows with batched APIs (`executemany`/prepared statement loops)
- `concurrent_select`: Four parallel workers executing point-select ranges

## Memory Tracking

Python extended Tier 1 runs are executed via `scripts/collect_metrics.py`, which wraps pytest and captures RSS from `resource.getrusage(resource.RUSAGE_CHILDREN)`.

```mermaid
flowchart TD
    Start[collect_metrics.py starts] --> Before[Read RUSAGE_CHILDREN ru_maxrss]
    Before --> Run[Run pytest benchmark subprocess]
    Run --> After[Read RUSAGE_CHILDREN ru_maxrss again]
    After --> Emit[Write *.metrics.json artifact with before/after/delta]
```

Metrics artifact fields:
- `rusage_children_before_kb`
- `rusage_children_after_kb`
- `peak_rss_delta_kb`

## Language-Specific Drivers

| Language | CUBRID Driver | MySQL Driver | Benchmark Framework |
|----------|--------------|--------------|---------------------|
| Python | pycubrid + sqlalchemy-cubrid | PyMySQL + SQLAlchemy | pytest-benchmark |
| TypeScript | cubrid-client | mysql2 | Custom timer |
| Go | cubrid-go | go-sql-driver/mysql | `testing.B` |

## Known Performance Characteristics

Based on benchmark results:

| Language | CUBRID vs MySQL | Notes |
|----------|----------------|-------|
| Python | ~4-6× slower | pycubrid is pure Python; PyMySQL has C extensions |
| TypeScript | ~0.4-0.5× (CUBRID faster) | cubrid-client uses efficient binary protocol |
| Go | ~1:1 (near parity) | Both drivers use native implementations |

## Normalization

The `scripts/normalize_results.py` script converts raw `pytest-benchmark` JSON output into the standardized format consumed by `github-action-benchmark`.

### Process

1. Read raw JSON from `pytest-benchmark` output
2. Extract `stats.ops` or compute from `stats.mean`
3. Calculate `stddev` as percentage of mean
4. Tag with language, tier, and framework metadata
5. Output standardized JSON

## CI Integration

### Workflow: `bench.yml`

```mermaid
flowchart TD
    PR["Pull request / push to main (code paths)"] --> Checks["checks: offline tests + workflow policy"]
    PR --> Smoke["cubrid-smoke: CUBRID-only Tier 0 + verified Tier 1, no timing"]
    Schedule["Schedule (weekly, Sunday 04:00 UTC)"] --> Rep["representative: CUBRID-only Tier 0 + pycubrid Tier 1 + provenance"]
    Manual["workflow_dispatch lane=representative"] --> Rep
    ManualFull["workflow_dispatch lane=full"] --> Full["full-suite: cross-language Tier 0-2"]
    Full --> PubStep[Publish Step]
    PubStep --> GHPages["Push to gh-pages branch"]
    GHPages --> Charts["github-action-benchmark charts"]
```

**PR and push runs**: offline tests, workflow policy and a CUBRID-only correctness smoke. They
are never a throughput gate.

**Scheduled runs**: the bounded CUBRID-only `representative` lane, once a week. Its comparison is
report-only.

**Manual full suite**: Tier 0 → Tier 1 → Tier 2 → Normalize → Publish across languages. Only
Python Tier 0/1 are required. See `RUNBOOK.md`, "Scheduled and manual benchmark runs".

### Benchmark Tracking

Results are published to the `gh-pages` branch using [`github-action-benchmark`](https://github.com/benchmark-action/github-action-benchmark):
- Historical data stored in `dev/bench/` directory
- Alert threshold: 110% (warns if performance degrades by >10%)
- Charts auto-generated from historical data
- Published only from manual full-suite runs; alerts do not comment or open issues

## Running Locally

```bash
# Start databases
make up

# Run smoke tests
make tier0
make tier0-ts
make tier0-go

# Run full benchmarks
make tier1-python
make tier1-python-extended
make tier1-ts
make tier1-ts-extended
make tier1-go
make tier1-go-extended

# Normalize results
python3 scripts/normalize_results.py results/python_tier1.json
python3 scripts/normalize_results.py results/python_tier1_extended.json

# Cleanup
make clean
```

## Extending Benchmarks

### Adding a new workload

1. Add the benchmark function to the appropriate language directory
2. Follow naming convention: `bench_{operation}_{variant}`
3. Ensure both CUBRID and MySQL variants exist for fair comparison
4. Update the Makefile targets if needed

### Adding a new language

1. Create a new directory under the repo root
2. Implement Tier 0 (connectivity) and Tier 1 (performance) benchmarks
3. Add Makefile targets: `tier0-{lang}`, `tier1-{lang}`
4. Update `bench.yml` workflow to include the new language
