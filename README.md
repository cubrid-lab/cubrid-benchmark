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

## Quick Start

```bash
make up        # Start CUBRID + MySQL Docker containers
make seed      # Apply schema
make all       # Run all benchmarks
```

## Version Matrix

The weekly version-matrix runner lives in `.github/workflows/bench.yml` as the
`matrix-benchmark` job. It expands two axes:

- `python-version`: `3.10`, `3.11`, `3.12`
- `driver-version`: `1.0.0` for `pycubrid`

Each matrix combination runs Tier 1 Python benchmarks against the Docker-backed CUBRID and
MySQL services, then packages the raw benchmark output into a descriptive result file such as
`results/python310_pycubrid100.json`.

### Adding New Version Axes

1. Edit the `strategy.matrix` block in `.github/workflows/bench.yml`.
2. Add a new Python version under `python-version` or a new `pycubrid` release under
   `driver-version`.
3. Keep the packaging step in sync if you add a brand-new driver axis with a different driver
   name, so artifact names and manifest metadata stay descriptive.
4. Re-run the workflow with `workflow_dispatch` to validate the new combinations before the next
   weekly schedule.

### Matrix Results and Manifest

- Per-combination result artifacts are uploaded from `results/` with names like
  `python-tier1-py3.10-pycubrid-1.0.0`.
- The `matrix-manifest` job downloads those artifacts back into `results/` and runs
  `python scripts/generate_manifest.py --results-dir results`.
- The generated `results/manifest.json` records the `(scenario, version-combo) -> result_file`
  mapping consumed by downstream reporting.

## Drivers Tested

| Language | CUBRID Driver | MySQL Driver |
|----------|--------------|--------------|
| Python | [pycubrid](https://github.com/cubrid-labs/pycubrid) v0.6.0 | PyMySQL |
| Python | [CUBRIDdb](https://github.com/cubrid/cubrid-python) v9.3.0.1 (C ext) | — |
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
