# Comprehensive Benchmark Plan

> **Status (2026-10-14): historical planning document.** The maintained scope is now same-server
> regression measurement of the CUBRID Python stack (pycubrid, sqlalchemy-cubrid, CUBRIDdb as a
> reference); see `README.md`. The Track A metric below, `pycubrid / PyMySQL`, is **retired**:
> it divides measurements from two different DBMS engines and cannot isolate driver overhead.
> Track B stays separate, optional research outside the Python routine lanes. The rest of this
> plan is kept unchanged as the March 2026 record.
>
> **Created**: 2026-03-22
>
> This plan defines the two-track benchmark strategy for cubrid-benchmark,
> covering all cubrid-labs driver/ORM stacks across Python, Go, TypeScript, and Rust.

## Table of Contents

- [Purpose](#purpose)
- [Track A — Driver Quality Tracking (Internal)](#track-a--driver-quality-tracking-internal)
- [Track B — DB Competitiveness Comparison (External)](#track-b--db-competitiveness-comparison-external)
- [Canonical Dataset](#canonical-dataset)
- [Scenario Catalog](#scenario-catalog)
- [Language × Driver/ORM Matrix](#language--driverorm-matrix)
- [Phased Execution](#phased-execution)
- [Result Interpretation Guidelines](#result-interpretation-guidelines)
- [CI/CD Integration](#cicd-integration)
- [Missing Components & Dependencies](#missing-components--dependencies)

---

## Purpose

cubrid-benchmark serves two distinct audiences with shared infrastructure:

| | Track A | Track B |
|---|---|---|
| **Goal** | Measure driver/ORM quality over time | Fair CUBRID vs MySQL engine comparison |
| **Audience** | cubrid-labs internal developers | External developers, decision makers |
| **Output** | Regression data → driver repo improvement backlog | Cross-DB performance positioning |
| **Key Metric** | Efficiency ratio vs reference driver (e.g., `pycubrid / PyMySQL`; *retired 2026-10: cross-engine, cannot isolate driver overhead*) | Absolute ops/s with compiled-language drivers |
| **Caveats** | Client overhead included intentionally | Client overhead minimized via compiled stacks |

### Core Principle

**Benchmarks are not just measurements — they produce actionable data.**

- Track A regression → filed as an issue on the relevant driver/ORM repo
- Track B result → published as CUBRID competitive positioning data
- ORM overhead ratio → optimization target for dialect maintainers

---

## Track A — Driver Quality Tracking (Internal)

### What We Measure

For each language, benchmark the CUBRID driver against its MySQL counterpart using identical operations. The ratio reveals driver-specific overhead.

**Metrics per scenario:**
- `ops/s` — throughput
- `p50/p95/p99 latency` — latency distribution
- `error rate` — correctness
- `worker CPU%` and `RSS` — client-side resource cost (Phase 2+)

**Regression signals:**
- `>10% ops/s drop` sustained for 2 consecutive nightly runs (3 iterations each)
- `>15% p95 latency increase` sustained for 2 consecutive nightly runs
- Any non-zero error rate increase

### How Results Become Actions

| Scenario | Regression Signal | Likely Root Cause | Target Repo |
|----------|-------------------|-------------------|-------------|
| `point_select_prepared` | ops/s drop | Statement cache, parse/encode path | driver repo |
| `bulk_insert_batch` | ops/s drop | Batching logic, parameter binding | driver repo |
| `connection_lifecycle` | latency spike | Auth handshake, protocol negotiation | driver repo |
| `prepared_statement_churn` | latency spike | Statement alloc/dealloc overhead | driver repo |
| ORM vs raw ratio increase | efficiency drop | Dialect SQL generation regression | ORM dialect repo |

---

## Track B — DB Competitiveness Comparison (External)

### Language Selection

**Primary: Go** — Both `cubrid-go` and `go-sql-driver/mysql` are compiled, mature, use `database/sql` interface. Minimal runtime jitter. Most fair comparison.

**Secondary: Rust** — Once `cubrid-rs` worker is implemented. Compiled, zero-cost abstractions, minimal runtime overhead.

**Not used for Track B headlines:**
- Python — `pycubrid` is pure Python while `PyMySQL` is also pure Python but more optimized. Driver overhead dominates DB engine signal.
- TypeScript — V8 JIT adds variable overhead. Useful for Track A but not ideal for Track B.

### What We Measure

- **Prepared statements only** — eliminates query parsing variance
- Same schema, same dataset, same concurrency, same duration
- Pinned Docker images (CUBRID 11.2, MySQL 8.0), fixed CPU set where possible
- Report as "CUBRID engine + official driver stack vs MySQL engine + official driver stack"

### Track B Scenarios (Go Primary)

| Scenario | Description | Concurrency |
|----------|-------------|-------------|
| `point_select_prepared` | PK lookup, 1 row | 1, 10, 50, 100 |
| `range_scan_index_prepared` | Indexed range query | 1, 10, 50 |
| `join_query_prepared` | orders ↔ order_items join | 1, 10 |
| `bulk_insert_batch_prepared` | 100-row batches | 1, 10 |
| `mixed_crud_txn_prepared` | 80/20 read/write in transaction | 10, 50, 100 |

---

## Canonical Dataset

All scenarios share a standardized dataset for cross-scenario and cross-language comparability.

### Tables

```sql
-- Key-value store (simple operations)
CREATE TABLE kv (
    id       INT PRIMARY KEY,
    k        VARCHAR(64) UNIQUE,
    v        VARCHAR(255),
    pad      TEXT
);

-- Transactional table (realistic operations)
CREATE TABLE orders (
    id         INT PRIMARY KEY AUTO_INCREMENT,
    user_id    INT NOT NULL,
    total      DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    INDEX idx_user_id (user_id),
    INDEX idx_created_at (created_at)
);

-- Join table
CREATE TABLE order_items (
    order_id   INT NOT NULL,
    sku        VARCHAR(32) NOT NULL,
    qty        INT NOT NULL,
    price      DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (order_id, sku),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);
```

### Dataset Sizes

| Size | kv rows | orders rows | order_items rows | Use Case |
|------|---------|-------------|------------------|----------|
| S | 10,000 | 10,000 | 30,000 | CI nightly (Tier 1-2) |
| M | 1,000,000 | 1,000,000 | 3,000,000 | Weekly deep benchmarks (Tier 3-4) |

### Data Generation Rules

- Deterministic seed (`seed=42` default)
- `kv.k` = `printf("key_%08d", id)`, `kv.v` = `printf("val_%08d", id)`
- `orders.user_id` = `random_int(1, 1000)`, `orders.total` = `random_float(10.0, 999.99)`
- `order_items` = 1-5 items per order, `sku` = `printf("SKU-%06d", random_int(1, 10000))`
- All timestamps within a 90-day window for realistic range queries

---

## Scenario Catalog

### Core Scenarios (All Tracks, All Languages)

| ID | Name | Description | SQL Shape | Transaction |
|----|------|-------------|-----------|-------------|
| S01 | `point_select_prepared` | PK lookup, 1 row returned | `SELECT k, v FROM kv WHERE id = ?` | autocommit |
| S02 | `point_select_unprepared` | Same as S01, no prepared statement | `SELECT k, v FROM kv WHERE id = <val>` | autocommit |
| S03 | `range_scan_index` | Indexed range, ~100 rows | `SELECT * FROM orders WHERE created_at BETWEEN ? AND ?` | autocommit |
| S04 | `full_scan` | No predicate, sequential read | `SELECT * FROM kv` (with LIMIT for sanity) | autocommit |
| S05 | `join_query` | Two-table join | `SELECT o.id, o.total, oi.sku FROM orders o JOIN order_items oi ON o.id = oi.order_id WHERE o.user_id = ?` | autocommit |
| S06 | `bulk_insert_batch` | Insert N rows per batch | Batch INSERT into `kv` (100 or 1000 rows) | explicit BEGIN/COMMIT |
| S07 | `mixed_crud_txn` | Read/write mix in transaction | 80/20 and 50/50 read/write ratio | explicit BEGIN/COMMIT |
| S08 | `connection_lifecycle` | Connect + auth + 1 query + close | Full connection cycle | N/A |
| S09 | `prepared_statement_churn` | Prepare / execute / close loops | Repeated prepare+exec+close | autocommit |

### Transaction Rules

- **Write scenarios (S06, S07)**: Explicit `BEGIN` / `COMMIT`, autocommit OFF
- **Read scenarios (S01-S05)**: Autocommit ON
- **Isolation level**: Default for each DB (CUBRID: READ COMMITTED, MySQL: REPEATABLE READ) — document in results
- **⚠️ Important**: Autocommit and isolation differences can silently change behavior. Always enforce explicit settings.

### Concurrency Tiers

| Tier | Concurrency | Duration | Warmup | Frequency |
|------|-------------|----------|--------|-----------|
| Tier 1 (Driver Throughput) | 1 | 30s | 5s | Nightly |
| Tier 3a (Moderate) | 10 | 30s | 5s | Nightly |
| Tier 3b (Heavy) | 50 | 30s | 5s | Weekly |
| Tier 3c (Stress) | 100 | 60s | 10s | Weekly |
| Tier 4 (Soak) | 10 | 3600s | 60s | Weekly (manual) |

---

## Language × Driver/ORM Matrix

### Priority Order

#### Phase 1 — Immediate (existing workers)

| Language | CUBRID Driver | MySQL Driver | Worker Status | Track |
|----------|---------------|-------------|---------------|-------|
| Python | pycubrid v0.5.0 | PyMySQL | ✅ In-process worker | A |
| Go | cubrid-go v0.2.1 | go-sql-driver/mysql | ✅ External worker | A + B (primary) |
| TypeScript | cubrid-client v1.1.0 | mysql2 | ✅ External worker | A |

#### Phase 2 — Short-term (ORM workers needed)

| Language | CUBRID ORM | MySQL ORM | Worker Status | Track |
|----------|-----------|-----------|---------------|-------|
| Python | sqlalchemy-cubrid v2.1.1 | SQLAlchemy + PyMySQL | ⬜ Needs ORM worker | A (Tier 2) |
| TypeScript | drizzle-cubrid v0.2.1 | Drizzle + mysql2 | ⬜ Needs external ORM worker | A (Tier 2) |
| Go | gorm-cubrid v0.1.0 | GORM + go-sql-driver/mysql | ⬜ Needs external ORM worker | A (Tier 2) |

#### Phase 3 — Medium-term (Rust worker needed)

| Language | CUBRID Driver | MySQL Driver | Worker Status | Track |
|----------|---------------|-------------|---------------|-------|
| Rust | cubrid-rs v0.1.0 | sqlx-mysql / mysql_async | ⬜ Needs new external worker | A + B (secondary) |
| Rust | sea-orm-cubrid v0.1.0 | SeaORM + sqlx-mysql | ⬜ Needs Rust ORM worker | A (Tier 2) |

#### Optional — Legacy

| Language | CUBRID Driver | MySQL Driver | Worker Status | Track |
|----------|---------------|-------------|---------------|-------|
| Node.js | node-cubrid v11.0.0 | mysql2 | ⬜ Needs external worker | A only (legacy) |

### Comparison Pairs

Each benchmark runs matching pairs for fair comparison:

```
Track A (per-language driver quality):
  pycubrid ←→ PyMySQL           (Python raw)
  cubrid-client ←→ mysql2       (TypeScript raw)
  cubrid-go ←→ go-sql-driver    (Go raw)
  cubrid-rs ←→ sqlx-mysql       (Rust raw)

Track A (ORM overhead — Tier 2):
  sqlalchemy-cubrid ←→ SQLAlchemy+PyMySQL    (Python ORM)
  drizzle-cubrid ←→ Drizzle+mysql2           (TypeScript ORM)
  gorm-cubrid ←→ GORM+go-sql-driver         (Go ORM)
  sea-orm-cubrid ←→ SeaORM+sqlx             (Rust ORM)

  Plus: raw driver vs ORM ratio per language
    pycubrid time / sqlalchemy-cubrid time = ORM overhead

Track B (DB engine comparison — Go primary):
  cubrid-go ←→ go-sql-driver    (prepared statements, compiled, fair)
```

---

## Phased Execution

### Phase 1 — Immediate (1–4 hours)

**Goal**: Produce first useful data with existing infrastructure.

**Prerequisites**: Docker Compose running (CUBRID 11.2 + MySQL 8.0)

**Actions**:
1. Create/update scenario YAML files for core scenarios (S01-S09) targeting Python, Go, TypeScript raw drivers
2. Run Tier 0 smoke tests (all languages)
3. Run Tier 1 driver throughput (concurrency=1, 30s duration)
4. Run Tier 3a moderate concurrency (concurrency=10)
5. Collect and compare results via `bench compare`

**Deliverables**:
- Scenario YAML files: `point_select_{lang}.yaml`, `bulk_insert_{lang}.yaml`, etc.
- Baseline results for all 3 languages × 2 DBs
- Initial comparison tables

### Phase 2 — Short-term (1–2 weeks)

**Goal**: Add prepared statement variants, ORM benchmarks, nightly CI.

**Actions**:
1. Add prepared statement scenario variants (S01 vs S02)
2. Implement Python SQLAlchemy ORM worker (in-process)
3. Add `--mode=orm` to Go and TypeScript external workers
4. Wire up nightly CI workflow (`bench.yml`)
5. Add join_query and full_scan scenarios
6. Establish baseline "last known good" values for regression detection

**Dependencies**:
- benchforge: [ORM-layer worker support](https://github.com/yeongseon/benchforge/issues/7)
- benchforge: [Setup/Load lifecycle](https://github.com/yeongseon/benchforge/issues/8)

**Deliverables**:
- ORM benchmark results (Tier 2)
- Nightly CI green
- Regression baselines stored

### Phase 3 — Medium-term (1–2 months)

**Goal**: Complete language coverage, automated soak testing, dashboard.

**Actions**:
1. Implement Rust external worker (raw driver)
2. Implement Rust ORM worker (SeaORM)
3. Add Tier 3b/3c concurrency benchmarks (50/100 connections)
4. Add Tier 4 soak test (1hr duration, memory tracking)
5. Implement resource sampling (CPU/RSS)
6. Build GitHub Pages dashboard with trend lines
7. Set up regression alerting (PR comments, Slack/email)

**Dependencies**:
- benchforge: [Rust external worker](https://github.com/yeongseon/benchforge/issues/6)
- benchforge: [Per-worker resource sampling](https://github.com/yeongseon/benchforge/issues/9)
- `cubrid-rs` stabilization (currently v0.1.0 Alpha)

**Deliverables**:
- All 4 languages benchmarked
- Automated soak/leak detection
- Public dashboard on GitHub Pages
- Regression alerts in CI

---

## Result Interpretation Guidelines

### Track A — Reading Driver Quality Data

1. **Always compare within the same language**: `pycubrid` vs `PyMySQL`, never `pycubrid` vs `cubrid-go`
2. **Report the efficiency ratio**: If `pycubrid` = 3,500 ops/s and `PyMySQL` = 5,200 ops/s, the ratio is 0.67 — meaning pycubrid has ~33% overhead vs the reference driver
3. **Track ratio over time, not absolute values**: Hardware changes affect absolutes; ratios are stable
4. **Client resource cost matters**: If ratio is 0.95 but pycubrid uses 3x CPU, that's still an issue
5. **Separate concerns**:
   - `point_select` regression → protocol parsing, result decoding
   - `bulk_insert` regression → parameter binding, batch encoding
   - `connection_lifecycle` regression → auth handshake, TLS negotiation
   - `prepared_statement_churn` regression → statement cache, alloc/dealloc
   - ORM overhead increase → dialect SQL generation, model mapping

### Track B — Reading DB Competitiveness Data

1. **Only cite compiled-language results (Go, Rust)** for DB engine comparison
2. **Always note**: "Measured via [driver name] v[version], prepared statements, [concurrency] concurrent connections"
3. **Acknowledge**: Results reflect DB engine + network protocol + driver encoding — not pure DB engine
4. **Full scan caveats**: Extremely sensitive to dataset size and buffer pool warmup — always publish cache-state assumptions
5. **Isolation level**: CUBRID defaults to READ COMMITTED, MySQL to REPEATABLE READ — document this difference

### What NOT to Do

- ❌ Cross-language absolute ranking ("Go is faster than Python")
- ❌ Using Python/TypeScript results for Track B headlines
- ❌ Comparing ORM results across languages
- ❌ Drawing conclusions from a single run (minimum 3 iterations, 2 consecutive nights for regressions)
- ❌ Ignoring warmup — always exclude warmup phase from measurements

---

## CI/CD Integration

### Workflow Structure

```
PR Gate (every PR):
  └── Tier 0: Functional smoke (all languages) — < 2 min

Nightly (scheduled):
  ├── Tier 0: Functional smoke
  ├── Tier 1: Driver throughput (concurrency=1)
  ├── Tier 2: ORM overhead (concurrency=1)
  └── Tier 3a: Moderate concurrency (concurrency=10)
  Duration: ~15 min

Weekly (scheduled):
  ├── All nightly scenarios
  ├── Tier 3b: Heavy concurrency (concurrency=50)
  ├── Tier 3c: Stress concurrency (concurrency=100)
  └── Tier 4: Soak test (1hr, concurrency=10)
  Duration: ~90 min
```

### Regression Detection

| Signal | Threshold | Window | Action |
|--------|-----------|--------|--------|
| ops/s drop | > 10% | 2 consecutive nights | Auto-create issue on driver repo |
| p95 latency increase | > 15% | 2 consecutive nights | Auto-create issue on driver repo |
| Error rate increase | Any non-zero | 1 night | Alert + block Tier escalation |
| RSS memory growth | > 20% over baseline | Soak test | Auto-create issue tagged `memory-leak` |

### Results Publishing

- **Storage**: JSON results in `results/` directory, gitignored (generated in CI only)
- **Dashboard**: GitHub Pages static site with:
  - Trend lines: ops/s over time per `scenario × language × driver`
  - Track B panels: CUBRID vs MySQL (Go results, prepared statements)
  - ORM overhead charts: raw vs ORM ratio per language
- **Tooling**: `github-action-benchmark` for trend tracking, `bench report` for HTML generation

---

## Missing Components & Dependencies

### benchforge Issues (Created)

| Issue | Title | Priority | Phase |
|-------|-------|----------|-------|
| [#6](https://github.com/yeongseon/benchforge/issues/6) | Rust external worker reference implementation | High | Phase 3 |
| [#7](https://github.com/yeongseon/benchforge/issues/7) | ORM-layer worker support | High | Phase 2 |
| [#8](https://github.com/yeongseon/benchforge/issues/8) | Setup/Load lifecycle step in Scenario YAML | Medium | Phase 2 |
| [#9](https://github.com/yeongseon/benchforge/issues/9) | Per-worker resource sampling (CPU/RSS) | Medium | Phase 3 |

### cubrid-benchmark Tasks

| Task | Priority | Phase | Depends On |
|------|----------|-------|------------|
| Create canonical schema SQL files (`kv`, `orders`, `order_items`) | High | 1 | — |
| Write deterministic seed data generator | High | 1 | — |
| Create scenario YAML files for S01-S09 (Python/Go/TS) | High | 1 | — |
| Run Phase 1 baseline benchmarks | High | 1 | Schema + scenarios |
| Implement Python SQLAlchemy ORM worker | High | 2 | benchforge #7 |
| Add `--mode=orm` to Go external worker | High | 2 | benchforge #7 |
| Add `--mode=orm` to TypeScript external worker | High | 2 | benchforge #7 |
| Set up nightly CI workflow | Medium | 2 | Phase 1 baselines |
| Implement Rust external worker | High | 3 | benchforge #6, cubrid-rs stable |
| Implement Rust SeaORM worker | Medium | 3 | Rust worker |
| Build GitHub Pages dashboard | Medium | 3 | Nightly CI running |
| Add Tier 4 soak test automation | Low | 3 | benchforge #9 |

### External Dependencies

| Dependency | Current State | Required For |
|------------|---------------|-------------|
| `cubrid-rs` v0.1.0 | Alpha | Rust benchmarks (Phase 3) |
| `sea-orm-cubrid` v0.1.0 | Alpha | Rust ORM benchmarks (Phase 3) |
| `gorm-cubrid` v0.1.0 | Alpha | Go ORM benchmarks (Phase 2) — may have issues |
| benchforge resource sampling | Not implemented | Tier 4 soak/leak detection |

---

## Watch Out For

1. **Autocommit/isolation differences**: CUBRID defaults to READ COMMITTED, MySQL to REPEATABLE READ. Enforce explicit settings in all write scenarios.
2. **Full scan sensitivity**: Results heavily depend on dataset size and buffer pool warmup. Always include warmup phase and document cache state.
3. **ORM SQL variation**: ORMs may generate different SQL per dialect. Validate generated SQL shapes during Tier 0 runs.
4. **Pure-Python overhead**: `pycubrid` is pure Python — its results reflect driver implementation quality, not DB engine speed. Never use for Track B.
5. **Connection pooling**: Some drivers pool by default, others don't. Standardize pool settings or disable pooling for fair comparison.
6. **Network locality**: Docker networking adds variable latency. Use `host` network mode or document the overhead.
