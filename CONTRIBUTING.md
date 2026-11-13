# Contributing to cubrid-benchmark

**Issue title policy:** use type-only `feat:`, `bug:`, etc. with observable acceptance criteria. PR and commit messages follow local Conventional Commits rules. Separate issue triage from PR CI; see [AGENTS.md](AGENTS.md).

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
6. Apply identical review criteria to every author and coding tool.
   Required GitHub CI is authoritative; local checks are recommended,
   and Oracle/Copilot/`agent-review` are optional advisory feedback.
   The responsible maintainer verifies final results and decides on merge.
   For `.github/workflows/bench.yml` and `scripts/file_regression_issue.py`,
   require explicit independent security/write-permissions assessment.

## Reporting Issues

When filing an issue, include:

- Workload and file affected (for example `python/bench_pycubrid.py::select_by_pk`)
- CUBRID server version and image digest
- pycubrid / sqlalchemy-cubrid versions (or source commit) and Python version
- The run folder or workflow run with raw results
- What you expected and what you measured

## Working with issues and coding agents

An observed slow run is **not automatically a regression**. Before filing a
correctness or performance issue, check the current code, overlapping open
issues and PRs, and the actual run artifacts. Distinguish hypotheses from
reproduced defects; a regression verdict requires comparable
`comparison_identity` values and at least three independent trials per side
as specified in `REPRODUCIBILITY.md`. Do not treat one timed sample, an
AI summary, or the advisory report job as a required CI throughput gate.

New issues start as `status: needs triage`. Maintainers verify type, scope,
duplicates and urgency against this repository's open queue before finalizing
one `priority: critical/high/medium/low` and one `size: XS/S/M/L/XL`.
The status is removed only after reviewing the persisted GitHub labels. Priority
means urgency, size means estimated effort; neither implies PR approval.

Human contributors may work on unclaimed tasks without an AI tool or a mandatory
pre-approval. Maintainer-operated autonomous agents must have authorization and
a confirmed implementer assignment before taking reserved tasks. Coordinate
with active PR owners, keep one independent change per PR, and report which
tests, benchmark lanes and actual runs were **not** performed. Required final-head
GitHub CI and maintainer review remain authoritative. Historical plans in
`AGENTS.md` are context, not new fixed deadlines for everyday development.


## Issue handoff and development progress

Use the issue and PR's actual state as evidence. Classification, authorization,
implementation, review and completion are **different decisions**:

1. **Intake / triage:** A newly filed issue starts with `status: needs triage`.
   Check evidence or reproduction, duplicate open/closed issues and overlapping
   PRs, repository scope, type, priority, size, dependencies, and verifiable
   acceptance criteria. A maintainer removes the intake label only after
   confirming the saved GitHub metadata. Do not infer priority, size, or triage
   completion automatically from the title or an AI suggestion.
2. **Ready:** Triage completion alone does not authorize development. Mark work
   Ready only when the intended outcome and non-goals are bounded, blockers
   have been resolved, and any repository-specific product, architecture,
   safety or compatibility decisions have been accepted. Unresolved research
   remains investigation; blocked work records its dependency and next action.
3. **In Progress:** Before maintainer-directed or autonomous implementation,
   identify the actual implementer in the GitHub Assignee field, check whether
   another open PR already owns the change, and link the implementation PR to
   its issue. Human-led outside contributors may propose a change on an
   unclaimed issue without prior assignment or an AI-tool-specific approval;
   coordinate with existing assignees and follow the local contribution rules.
4. **In Review:** A PR is in review only when its scoped change and verification
   evidence are available. Check the required CI on the final PR head, test
   coverage appropriate to the risk, unresolved review findings and any
   repository-specific independent review or live-system validation. A bot
   review or passing CI cannot replace an outstanding product/safety decision.
5. **Done:** Record the actual disposition: a merged and validated change,
   a completed investigation with evidence, or an explained duplicate/not-
   planned closure. Use an issue-closing PR keyword only if merging actually
   satisfies its acceptance criteria. A merged PR is not proof that any
   deferred integration, fault, security or release verification has passed.

**Tracking:** Where a GitHub Project is in use, track `Todo`, `Ready`,
`In Progress`, `In Review` and `Done` in its Status field; record
`Blocked` and the unblock condition when applicable. Without a Project,
record the next action in the issue, maintain the Assignee and linked PR, and
avoid creating duplicate status-label taxonomies. Priority means impact,
size means estimated effort, and neither is a work-in-progress indicator.
This guidance does not add an automatic merge gate or supersede this
repository's existing design, testing, security, or release requirements.
