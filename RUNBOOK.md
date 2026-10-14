# Benchmark Runbook

This runbook is the operator checklist for producing benchmark evidence that is comparable,
reviewable, and suitable for filing against driver or ORM repositories.

## Table of Contents

- [Before You Run](#before-you-run)
- [Tier 0 Procedure](#tier-0-procedure)
- [Tier 1 Procedure](#tier-1-procedure)
- [Tier 2 Procedure](#tier-2-procedure)
- [Scheduled and manual benchmark runs](#scheduled-and-manual-benchmark-runs)
- [Reproducibility Controls](#reproducibility-controls)
- [Environment Validation Checklist](#environment-validation-checklist)
- [Filing Quality Bar](#filing-quality-bar)

## Before You Run

1. Confirm you are on the intended branch and have a clean working tree for the benchmark session.
2. Confirm the host belongs to the same `comparable_group` as the baseline you want to compare.
3. Use the repository-standard DB stack from `docker/compose.yml`:
   - CUBRID `11.2` on port `33000`
   - MySQL `8.0` on port `3306`
4. Keep the machine on AC power.
5. Stop editors, browsers, package managers, and unrelated containers that can perturb CPU or I/O.
6. Apply schema and deterministic seed before collecting reportable measurements.

Standard setup:

```bash
make up
make seed
```

## Tier 0 Procedure

Purpose: validate that both DBs and all language harnesses are healthy before collecting
performance evidence.

Run:

```bash
make tier0
make tier0-ts
make tier0-go
```

Acceptance criteria:

- Both databases accept connections
- CRUD smoke tests pass in Python, TypeScript, and Go
- No container restarts or health-check flaps occur during the gate

If Tier 0 fails, stop. Do not treat any later measurements as valid until readiness is restored.

## Tier 1 Procedure

Purpose: measure raw driver throughput for the core sequential workloads.

### Standard run

```bash
make tier1-python
make tier1-ts
make tier1-go
```

### Reproducibility control run

Run the local control target before filing a regression or improvement claim:

```bash
make bench-verify
```

`bench-verify` executes the Python Tier 1 command twice and fails if any benchmark drifts outside
the repository noise band for back-to-back control runs.

### Evidence handling

For a reportable Tier 1 session:

1. Create or select the correct experiment folder
2. Create a new immutable `runs/YYYY-MM-DD_label/` folder
3. Record host/software/driver metadata in `run.yaml`
4. Copy raw outputs into `raw/`
5. Update the experiment README with the new run and conclusion

## Tier 2 Procedure

Purpose: measure ORM overhead relative to the raw driver path.

Run the Tier 2 benchmark for the language under study after Tier 0 passes and after the same DB
stack is freshly seeded.

Expected operator flow:

1. Run Tier 0 validation first
2. Re-seed if the ORM workload mutates benchmark tables materially
3. Execute the Tier 2 language target
4. Preserve raw output and derived summaries in the experiment run folder
5. Compare ORM results against the matching raw-driver baseline from the same comparable group

Tier 2 conclusions must describe the ratio versus the raw driver, not only absolute throughput.

## Scheduled and manual benchmark runs

`.github/workflows/bench.yml` has four lanes. Every job has a `timeout-minutes` bound, every
action is pinned to a commit SHA, and the workflow token is `contents: read` except in the
manual full suite (`contents: write` for the gh-pages publish, `actions: read` for the previous
artifact). No lane opens GitHub issues.

| Lane | Trigger | What runs | Bound |
|------|---------|-----------|-------|
| `checks` | pull request or push to `main` touching benchmark code | `pytest tests` (offline tests and workflow policy) | 10 min |
| `cubrid-smoke` | same | CUBRID 11.4 only: Tier 0 plus the verified Tier 1 workload with `--benchmark-disable` (correctness, no timing) | 15 min |
| `representative` | weekly (`0 4 * * 0`) or `workflow_dispatch` with `lane=representative` | CUBRID 11.4 only: Tier 0 smoke, pycubrid Tier 1 measurement, provenance capture, report-only baseline comparison, required-result check | 40 min |
| `full-suite` / `matrix` | `workflow_dispatch` with `lane=full` / `lane=matrix` only | the legacy cross-language suite (CUBRID 11.2 + MySQL 8.0) / the legacy pycubrid 1.0.0 matrix | 120 / 45 min |

Docs-only changes trigger nothing. Benchmark throughput is never a pull request gate. The
full cross-language suite and the version matrix never run on a schedule.

Failure handling:

- Required steps have no `continue-on-error` and no `if`, so GitHub's implicit `success()`
  skips everything after the first failure or a cancellation.
- Every artifact upload uses `if: always()` with `if-no-files-found: warn`. Whatever was produced
  is uploaded, and a missing file never adds a second, misleading failure.
- `scripts/check_required_results.py` is the separate gate. A lane that measured successfully
  but lacks a required result (or has an empty pytest-benchmark file) fails there.
- In the full suite, Python Tier 0 and Tier 1 are required. The rest is optional
  (`continue-on-error`): Python extended/Tier 2, the TypeScript, Go and Rust tracks (including
  their toolchain setup, whose failure skips only that track and never falls back to a
  preinstalled toolchain) and the benchforge report steps. Their outcomes are listed in the job
  summary and as warnings. The full suite's required-result check runs only after Python Tier 1
  succeeded, so a Tier 1 failure is not followed by a second error.
- `workflow_dispatch` with `inject_failure=true` makes the representative lane fail right after
  its Tier 0 smoke, to check this behaviour on demand.

Manual runs:

```bash
gh workflow run bench.yml -f lane=representative            # bounded CUBRID-only lane
gh workflow run bench.yml -f lane=representative -f inject_failure=true
gh workflow run bench.yml -f lane=full                      # legacy cross-language suite
```

### Scheduler status (investigated 2026-10-14)

- The Actions API reports the `Benchmark` workflow as `disabled_inactivity`, last updated
  2026-06-22. GitHub disables scheduled workflows in a public repository after 60 days without
  repository activity. No run of any kind (schedule, pull request or dispatch) was created after
  2026-06-22: pull requests #21-#23 in October ran only Copilot review.
- The last scheduled runs were daily. Run
  [27898618886](https://github.com/cubrid-lab/cubrid-benchmark/actions/runs/27898618886)
  (2026-06-21) succeeded. Run
  [27942243992](https://github.com/cubrid-lab/cubrid-benchmark/actions/runs/27942243992)
  (2026-06-22) failed: `Run Rust Tier 0` failed, then both `if: always()` uploads failed with
  `if-no-files-found: error`. Most runs from 2026-06-15 to 2026-06-20 also failed.
- The job logs are no longer retrievable (HTTP 410), so the Rust failure's cause is unknown.
  The Rust tracks are optional in the manual full suite and are not part of the scheduled lane.
- Actions is enabled for the repository with all actions allowed. A workflow disabled for
  inactivity is re-enabled with
  `gh api -X PUT repos/cubrid-lab/cubrid-benchmark/actions/workflows/bench.yml/enable`. The
  weekly schedule is disabled again by the same rule after 60 more days without activity.

## Reproducibility Controls

### CPU governor and power management

- Linux hosts should use the `performance` governor when possible
- If `performance` is unavailable, record the active governor in run notes
- Disable laptop battery saver / vendor power-saving profiles
- Avoid thermal throttling: let the machine cool down before back-to-back replications if needed

### Network isolation

- Run benchmarks on a stable local network with no VPN reconnects during measurement
- Do not run heavy downloads, package installs, or remote sync jobs during a benchmark session
- Prefer localhost-only DB traffic; do not move DB containers to shared remote hosts for a run that
  will be compared to local baselines

### Version pinning strategy

Pin and record these versions for every reportable run:

- DB images: `cubrid/cubrid:11.2`, `mysql:8.0`
- Docker / Docker Compose version
- Python / Go / Node.js runtime versions
- Driver and ORM versions under test
- Benchmark command variant used

If any pinned component changes, assume the old baseline is no longer directly comparable until a
new baseline is recorded.

### Significant-difference threshold

Use the following interpretation inside one comparable group:

- `≤5%` relative delta: equivalent/noise band
- `>5%` and `<10%`: suspicious, requires more replication
- `≥10%` relative delta: significant effect-size threshold for opening a performance issue,
  provided replication and provenance requirements are met

### Threshold tuning for draft issue generation

`scripts/gap_to_issue.py` converts `compare_runs.py` JSON output into draft GitHub issue markdown.
Use it only for fail-level regressions inside one comparable group. `make gap-issues` runs the
comparator without the legacy flag, so a pair without a matching measured `comparison_identity`
stops with exit code `3` (not comparable) before any draft is written.

- `--min-effect-size` defaults to `10.0` and should stay aligned with the issue-opening bar above.
- `--min-replications` defaults to `3`, matching the minimum issue-worthy claim in this runbook.
- If a benchmark environment is noisy, raise `--min-effect-size` before filing rather than treating
  a single borderline run as actionable.
- If provenance does not yet encode replication count, keep the default filing bar in the issue body
  and treat the draft as a reminder to gather more evidence before opening anything manually.

Examples:

```bash
python3 scripts/gap_to_issue.py --report results/compare_latest.json
make gap-issues
make gap-issues MIN_EFFECT_SIZE=15.0 MIN_REPLICATIONS=4 TARGET_REPO=cubrid-labs/sqlalchemy-cubrid
```

## Environment Validation Checklist

Before accepting a run as evidence, confirm all items below:

- [ ] `make up` completed without container restart loops
- [ ] `make seed` completed against the expected schema
- [ ] Tier 0 passed for Python, TypeScript, and Go
- [ ] Host was on AC power and not in a power-saving profile
- [ ] CPU governor was `performance` or recorded explicitly in notes
- [ ] No unrelated heavy containers or background jobs were running
- [ ] DB image versions matched the intended baseline
- [ ] Driver/runtime versions were captured in `run.yaml`
- [ ] A new immutable run folder was created under `experiments/**/runs/**`
- [ ] Raw outputs were stored under `raw/`
- [ ] The run references the correct `comparable_group`

## Filing Quality Bar

Open a performance issue only when all of the following are true:

1. Minimum replications:
   - exploratory note: `2` full replications
   - issue-worthy claim: `3` full replications
2. Confidence bar:
   - the direction of change is consistent across replications
   - the observed effect remains outside the `±5%` noise band
   - claims at or above `10%` relative delta are preferred for filing
3. Provenance in the issue body:
   - experiment slug
   - baseline run ID
   - candidate run ID(s)
   - `comparable_group`
   - exact commands used
   - links or paths to raw artifacts and report summary

Issue bodies should state whether the result is a driver-throughput regression, ORM-overhead
regression, or an improvement validated by before/after evidence.
