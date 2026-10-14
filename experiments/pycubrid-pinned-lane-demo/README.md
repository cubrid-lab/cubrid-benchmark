# Pinned-release candidate-to-baseline comparison (demonstration)

> **Status**: completed
> **Question**: Does a pinned-release candidate compare against a baseline only when its measured
> environment and workload identity match, with the driver revision as the only difference?
> **Tracking issue**: [#30](https://github.com/cubrid-lab/cubrid-benchmark/issues/30)

This experiment demonstrates the **comparison mechanics** of the pinned Python lanes. It is
**not a performance result**. Each run is a single trial on a shared developer machine, where
the load average reached 4.4 during the runs. Run-to-run noise on this host is larger than any
plausible driver difference: the same pycubrid release varied by more than 2x between trials of
`experiments/tier1-python-workload`. No regression or improvement is claimed, and no issue was
filed.

## Runs

All three runs were measured from a clean checkout of benchmark commit `829fa36`
(`benchmark_source_dirty: false`). Each used its own virtual environment with pinned packages
that `scripts/python_lanes.py verify-install` had checked (`raw/versions.json`). The identity was
captured with `--fixed-host devbox-i5-9400F`.

| Run | Role | pycubrid | CUBRID | comparable_group |
|-----|------|----------|--------|------------------|
| `2026-10-14_pycubrid-1.11.0-baseline` | baseline | 1.11.0 | 11.4.6.1963 | `fixed-cubrid11.4-...-8428ae17b434` |
| `2026-10-14_pycubrid-1.12.0-candidate` | candidate | 1.12.0 | 11.4.6.1963 | `fixed-cubrid11.4-...-8428ae17b434` |
| `2026-10-14_pycubrid-1.12.0-cubrid102-candidate` | candidate | 1.12.0 | 10.2.18.9024 | `fixed-cubrid10.2-...-c4196e8f6604` |

## Outcome

- **Same environment and workload, different driver revision.** The pycubrid 1.12.0 candidate
  against the 1.11.0 baseline is comparable: the groups are equal and every identity field
  matches. Only `software_under_test` differs. `compare_runs.py` therefore produced
  per-operation deltas (`derived/comparison_vs_1.11.0-baseline.json`). They range from -38% to
  +137% and come from a single noisy trial each, so they are not evidence about either release.
- **Mismatched server.** The pycubrid 1.12.0 candidate on CUBRID 10.2 is rejected as
  `not_comparable` (exit code `3`). `cubrid_image`, `cubrid_image_digest` and `cubrid_build`
  differ, so no verdict is produced
  (`2026-10-14_pycubrid-1.12.0-cubrid102-candidate/derived/comparison_vs_1.11.0-baseline.json`).

A real release-to-release comparison needs repeated trials on a quiet, fixed host, or on one
GitHub-hosted runner image, under the replication rules in `REPRODUCIBILITY.md`.
