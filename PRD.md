# Product Requirements Document — cubrid-benchmark

> **Status (2026-10-14): historical (March 2026).** The cross-database goal below is no longer
> maintained. The current scope is reproducible same-server regression measurement of pycubrid and
> sqlalchemy-cubrid, with CUBRIDdb as a reference; see `README.md`. Cross-database results stay as
> archived experiments.

## Overview

**cubrid-benchmark** is a unified, multi-language benchmark suite that measures CUBRID database
performance against MySQL across the entire cubrid-labs ecosystem: Python (pycubrid, SQLAlchemy),
Go (cubrid-go, GORM), and TypeScript (cubrid-client, Drizzle).

## Goals

1. **Regression Detection** — Catch performance regressions across CUBRID driver/ORM releases
2. **Cross-DB Comparison** — Provide fair, reproducible CUBRID vs MySQL comparisons
3. **Within-Language ORM Overhead** — Quantify raw driver vs ORM overhead per language
4. **Cross-Language Directional Comparison** — Show relative performance trends (not absolute scores)
5. **Automated Trend Tracking** — Nightly CI with GitHub Pages visualization

## Non-Goals

- Single cross-language performance score (unfair, meaningless)
- Benchmarking CUBRID server internals (this benchmarks the driver/ORM layer)
- Replacing CUBRID's own server-level benchmarks (TPC-C, etc.)

## Architecture

### 4-Tier Benchmark Model

| Tier | Name | Purpose | Frequency |
|------|------|---------|-----------|
| 0 | Functional | Connection + basic CRUD smoke test | Every PR |
| 1 | Driver Throughput | INSERT/SELECT/UPDATE/DELETE × 10K rows | Nightly |
| 2 | ORM Overhead | Raw driver vs ORM for same operations | Nightly |
| 3 | Concurrency | Parallel connections stress test | Weekly |
| 4 | Soak / Leak | Long-running stability & memory leak detection | Weekly |

### Language × Tool Matrix

| Language | Raw Driver | ORM | Benchmark Tool |
|----------|-----------|-----|----------------|
| Python | pycubrid / PyMySQL | SQLAlchemy | pytest-benchmark |
| Go | cubrid-go / go-sql-driver/mysql | GORM | go test -bench |
| TypeScript | cubrid-client / mysql2 | Drizzle | tinybench |

### Comparison Strategy

1. **Per-package regression over time** — primary metric, alerts on > 10% regression
2. **Within-language raw vs ORM** — quantify ORM overhead per language
3. **CUBRID vs MySQL (same language, same tier)** — fair side-by-side
4. **Cross-language** — directional only, never absolute ranking

### Result Format

All benchmarks emit a common JSON schema:

```json
{
  "name": "insert_10k_rows",
  "unit": "ops/sec",
  "value": 12345.67,
  "range": "± 2.3%",
  "extra": "language=python driver=pycubrid db=cubrid tier=1"
}
```

### Visualization

- `github-action-benchmark` → GitHub Pages (`gh-pages` branch)
- Per-language dashboard with trend lines
- CUBRID vs MySQL overlay charts
- Automatic alert comments on PRs with > 10% regression

## Phased Implementation

### Phase 1 (MVP)
- Docker Compose: CUBRID 11.2 + MySQL 8.0
- Schema: `init.sql` + `seed.sql` (deterministic data)
- Tier 0: Functional gate (all languages)
- Tier 1: Python driver throughput (pycubrid vs PyMySQL)
- CI: Nightly workflow + GitHub Pages

### Phase 2
- Tier 1: Go driver throughput (cubrid-go vs go-sql-driver/mysql)
- Tier 1: TypeScript driver throughput (cubrid-client vs mysql2)

### Phase 3
- Tier 2: ORM benchmarks (SQLAlchemy vs raw, GORM vs raw, Drizzle vs raw)

### Phase 4
- Tier 3: Concurrency benchmarks (10/50/100 parallel connections)
- Tier 4: Soak tests (1hr continuous load, memory tracking)

## Technical Constraints

- Docker-only: no local DB installs required
- Deterministic seed data: same results across runs
- Each language uses its standard benchmark tool (no custom framework)
- CI runner: ubuntu-latest with standard GitHub Actions resources
- Results must be reproducible within ±5% on same hardware

## Example-first Design

Every benchmark must have:
1. A clear, runnable example in its README
2. Expected output format documented
3. One-command execution (`make tier1-python`, `make tier1-go`, etc.)

## Success Metrics

- Nightly CI green for 30 consecutive days
- Trend data visible on GitHub Pages
- Each CUBRID driver repo links to its benchmark results
- PR regression alerts trigger within 5 minutes
