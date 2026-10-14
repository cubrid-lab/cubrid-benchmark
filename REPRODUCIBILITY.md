# Benchmark Reproducibility Policy

This policy defines the minimum controls required for benchmark evidence in `cubrid-benchmark`.
All future benchmark reports, experiment READMEs, and performance issues must reference this
policy together with the matching run metadata under `experiments/**/runs/**/run.yaml`.

## Scope

This policy applies to:

- Tier 0 smoke checks used to validate environment readiness
- Tier 1 driver-throughput measurements across Python, Go, and TypeScript
- Tier 2 ORM-overhead measurements built on top of Tier 1 scenarios
- Before/after optimization comparisons used by the Performance Loop

It does not authorize cross-machine or cross-version comparisons without a new baseline.

## Standard Benchmark Environment

### Containerized database baseline

All benchmark reports must use the repository-standard Docker environment unless the experiment
README explicitly documents a justified exception:

- CUBRID: `cubrid/cubrid:11.2`
- MySQL: `mysql:8.0`
- Compose file: `docker/compose.yml`
- CUBRID port: `33000`
- MySQL port: `3306`
- Shared database name: `benchdb`
- CUBRID container `shm_size`: `256m`

### Host controls

The host used for a comparable group must stay fixed for all direct before/after comparisons.
Each run must capture the same metadata fields already used in real runs such as:

- `hostname`
- `cpu`
- `cores`
- `memory_gb`
- `os`
- `kernel`
- language runtime versions
- database image versions
- Docker version
- driver versions

If CPU model, core count, memory class, kernel family, Docker version, or DB image version changes,
start a new baseline with a new `comparable_group`.

### Resource envelope

For local reproducibility checks and any report intended for comparison:

- Keep background workloads off the host during measurement
- Use AC power and disable power-saving modes
- Keep Docker Desktop-style dynamic resource resizing out of the measurement path
- Use a stable CPU governor (`performance` preferred; if not possible, record actual governor)
- Do not co-schedule unrelated containers on the same host
- Keep available memory comfortably above combined DB container demand to avoid swap activity

Reports must describe any deviation from this envelope in the run `notes` field.

## Comparable Groups

The authoritative comparability model is the experiment structure in `AGENTS.md`.

A `comparable_group` identifies the measurement surface for valid direct comparison:

`machine + CPU + OS/kernel family + Docker version + DB version`

Examples already in this repository include values such as:

- `devbox-i5-9400F-linux5.15-docker-cubrid112`
- `devbox-i5-4200M-linux5.15-docker-cubrid112`

Rules:

1. Compare candidate runs only against the baseline in the same `comparable_group`
2. When environment identity changes, create a fresh baseline run
3. Never overwrite an existing run folder; every execution is immutable evidence
4. Benchmark reports must state which baseline a candidate run compares to

## Measurement Protocol

### Warmup and measurement counts

Repository-default measurement parameters for Python Tier 1 are currently encoded in the benchmark
files via `pytest-benchmark` pedantic mode:

- warmup: `1` warmup round discarded
- measurement: `5` measured rounds per benchmark function

`python/bench_pycubrid.py` (workload revision `tier1-explicit-keys-v2`) also fixes what each
round measures:

- the table uses explicit primary keys `1..10000`, not `AUTO_INCREMENT`, so a re-seed after a
  `DELETE` always produces the same keys
- an untimed `setup` rebuilds and verifies the starting state before every round, warm-up
  included: an empty table for INSERT, `10000` seeded rows for the other operations
- the timed region holds only the measured statements and, for INSERT/UPDATE/DELETE, their one
  final `commit()`; re-seeding, verification and clean-up are not timed
- an untimed `teardown` checks every SELECT row (id and payload), every UPDATE/DELETE
  affected-row count and the final table state; a miss raises `WorkloadVerificationError`
  and fails the benchmark

Tier 1 results recorded before this revision timed a re-seed in every round and, after the
warm-up round, addressed keys that `AUTO_INCREMENT` had already moved past. They are a different
workload: keep them as historical evidence and never compare them with the corrected series as
proof of a regression or improvement.

Go and TypeScript reports must document their equivalent command-level repetition settings in the
experiment definition and run notes.

### Aggregation method

All reports must aggregate repeated measurements using:

- primary statistic: median throughput or median latency
- dispersion: standard deviation or percentage range emitted by the benchmark framework
- supporting latencies where available: `p50`, `p95`, `p99`

Do not headline a single best run. The reported conclusion must reflect the aggregated result.

## Variance Handling

Variance control is mandatory for any claim of improvement or regression.

### Minimum replication bar

- Tier 0: one clean pass per language is enough; it is a readiness gate, not a performance claim
- Tier 1 and Tier 2 exploratory work: at least `2` full command replications
- Tier 1 and Tier 2 issue-filing or report-worthy claims: at least `3` full command replications

Back-to-back replications must be executed on the same host, same Docker images, same schema, and
same seed state.

### Outlier handling

- Never silently delete slow or fast runs
- If a run is invalid due to a known external disturbance (container restart, thermal throttling,
  network interruption, host package update, etc.), keep the artifact and mark it invalid in notes
- Outlier exclusion is only allowed when a concrete environmental fault is documented
- If no documented fault exists, keep all replications and report the variance honestly

### Equivalent vs significant differences

This repository already treats results within `±5%` as equivalent in `AGENTS.md`.
This policy keeps that rule for control runs and local reproducibility checks.

- `≤5%` relative delta within the same comparable group: equivalent/noise band
- `>5%` and `<10%`: investigate, do not headline without additional replications
- `≥10%` relative delta: potentially meaningful effect size, still requires provenance and
  replication before filing or publishing as a regression/improvement claim

### Drift formula used by `make bench-verify`

The `bench-verify` target runs the Python Tier 1 suite twice back-to-back and computes
per-benchmark relative drift as:

```
drift = abs(run2_ops - run1_ops) / run1_ops
```

This is a baseline-relative (asymmetric) formula where `run1` is the reference.
The target fails if any individual benchmark exceeds `5%` drift.

This check is intended for controlled local environments where background noise is minimal.
On shared or noisy CI runners, the threshold may produce false positives; in those cases,
run the verify target manually and inspect the per-benchmark output before treating a failure
as evidence of a real regression.

## Provenance Requirements

Every reportable run must preserve:

- immutable `run.yaml`
- raw benchmark artifacts under `raw/`
- derived summaries or figures when used in the report
- exact command used to reproduce the run
- notes on any deviation from standard controls

Future benchmark reports must include a short reproducibility note that links to:

1. this policy
2. the experiment definition
3. the exact run folder(s) used as evidence

## Required Statement in Future Reports

Use this or an equivalent sentence in future benchmark reports:

> Reproducibility note: this report follows [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md); direct
> comparisons are limited to runs in the same `comparable_group`, with raw artifacts preserved
> under `experiments/**/runs/**`.
