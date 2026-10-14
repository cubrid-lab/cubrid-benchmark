.PHONY: up down clean seed compare report gap-issues historical-python-mysql tier0 tier0-ts tier0-go tier0-rust tier1-python tier1-python-extended tier2-python tier1-ts tier1-ts-extended tier1-go tier1-go-extended tier1-rust bench-verify all

COMPARE_BASELINE_DIR ?= experiments/pycubrid-post-1.0-optimization/runs/2026-04-12_before-optimization
COMPARE_CANDIDATE_DIR ?= experiments/pycubrid-post-1.0-optimization/runs/2026-04-12_after-optimization
COMPARE_REPORT ?= results/compare_latest.json
REPORT_INPUT ?=
REPORT_OUTPUT_DIR ?=
REPORT_BASELINE ?=
REPORT_FORMAT ?= both
MIN_EFFECT_SIZE ?= 10.0
MIN_REPLICATIONS ?= 3
TARGET_REPO ?= cubrid-labs/pycubrid
GAP_ISSUES_OUTPUT_DIR ?=

up:
	docker compose -f docker/compose.yml up -d
	./scripts/wait_for_db.sh

down:
	docker compose -f docker/compose.yml down

clean:
	docker compose -f docker/compose.yml down -v

seed:
	python3 scripts/apply_schema.py

# Manual comparison of two historical runs: they predate measured comparison_identity, so the
# operator-written comparable_group strings are accepted (the report marks the legacy basis).
compare:
	mkdir -p "$(dir $(COMPARE_REPORT))"
	python3 scripts/compare_runs.py --allow-legacy-group-match --baseline-dir "$(COMPARE_BASELINE_DIR)" --candidate-dir "$(COMPARE_CANDIDATE_DIR)" --output "$(COMPARE_REPORT)"
	python3 -c 'import sys; from pathlib import Path; sys.stdout.write(Path(sys.argv[1]).read_text(encoding="utf-8"))' "$(COMPARE_REPORT)"

report:
	input="$(REPORT_INPUT)"; \
	if [ -z "$$input" ]; then \
		input="$$(python3 -c 'from pathlib import Path; import sys; files = sorted(Path("results/benchforge_v2").glob("*.json"), key=lambda path: path.stat().st_mtime); sys.stdout.write(str(files[-1]) if files else "")')"; \
	fi; \
	if [ -z "$$input" ]; then \
		printf '%s\n' 'No benchforge v2 results found under results/benchforge_v2' >&2; \
		exit 1; \
	fi; \
	output_dir="$(REPORT_OUTPUT_DIR)"; \
	if [ -z "$$output_dir" ]; then \
		stem="$$(python3 -c 'from pathlib import Path; import sys; sys.stdout.write(Path(sys.argv[1]).stem)' "$$input")"; \
		output_dir="results/reports/$$stem"; \
	fi; \
	if [ -n "$(REPORT_BASELINE)" ]; then \
		python3 scripts/generate_report.py --input "$$input" --output-dir "$$output_dir" --format "$(REPORT_FORMAT)" --baseline "$(REPORT_BASELINE)"; \
	else \
		python3 scripts/generate_report.py --input "$$input" --output-dir "$$output_dir" --format "$(REPORT_FORMAT)"; \
	fi

gap-issues:
	mkdir -p "$(dir $(COMPARE_REPORT))"
	python3 scripts/compare_runs.py --baseline-dir "$(COMPARE_BASELINE_DIR)" --candidate-dir "$(COMPARE_CANDIDATE_DIR)" --output "$(COMPARE_REPORT)"; rc=$$?; if [ $$rc -gt 1 ]; then exit $$rc; fi
	python3 -c 'import sys; from pathlib import Path; sys.stdout.write(Path(sys.argv[1]).read_text(encoding="utf-8"))' "$(COMPARE_REPORT)"
	python3 scripts/gap_to_issue.py --report "$(COMPARE_REPORT)" --min-effect-size "$(MIN_EFFECT_SIZE)" --min-replications "$(MIN_REPLICATIONS)" --target-repo "$(TARGET_REPO)" $(if $(GAP_ISSUES_OUTPUT_DIR),--output-dir "$(GAP_ISSUES_OUTPUT_DIR)",); rc=$$?; if [ $$rc -gt 1 ]; then exit $$rc; fi

# Python (maintained track: CUBRID only; no MySQL server or PyMySQL needed)
tier0:
	cd python && pytest bench_tier0_cubrid.py -v

tier1-python:
	cd python && pytest bench_pycubrid.py --benchmark-json=../results/python_tier1.json -v

bench-verify:
	mkdir -p results
	cd python && pytest bench_pycubrid.py --benchmark-json=../results/bench_verify_run1.json -v
	cd python && pytest bench_pycubrid.py --benchmark-json=../results/bench_verify_run2.json -v
	python3 -c 'import json, sys; from pathlib import Path; run1 = json.loads(Path("results/bench_verify_run1.json").read_text())["benchmarks"]; run2 = json.loads(Path("results/bench_verify_run2.json").read_text())["benchmarks"]; by_name = {item["name"]: item for item in run2}; deltas = []; failures = []; threshold = 0.05; [deltas.append((item["name"], item["stats"]["ops"], by_name[item["name"]]["stats"]["ops"], abs(by_name[item["name"]]["stats"]["ops"] - item["stats"]["ops"]) / item["stats"]["ops"])) or (failures.append(item["name"]) if abs(by_name[item["name"]]["stats"]["ops"] - item["stats"]["ops"]) / item["stats"]["ops"] > threshold else None) for item in run1 if item["name"] in by_name]; print("bench-verify: compared %d benchmarks" % len(deltas)); [print("- %s: run1=%.4f ops/s run2=%.4f ops/s delta=%.2f%%" % (name, ops1, ops2, delta * 100.0)) for name, ops1, ops2, delta in deltas]; sys.exit("bench-verify failed: back-to-back variance exceeded 5%% for %s" % ", ".join(failures)) if failures else print("bench-verify: all benchmark deltas within 5%%")'

tier1-python-extended:
	cd python && python3 ../scripts/collect_metrics.py --result-json ../results/python_tier1_extended.json --metrics-json ../results/python_tier1_extended.metrics.json -- pytest bench_pycubrid_extended.py --benchmark-json=../results/python_tier1_extended.json -v

tier2-python:
	cd python && pytest bench_sqlalchemy_cubrid.py --benchmark-json=../results/tier2_python.json -v

# Archived MySQL-module runner (opt-in, never part of `all` or CI). Runs the retired PyMySQL and
# SQLAlchemy+MySQL pytest modules at the current revision against a MySQL 8.0 server; install
# `python/requirements-historical-mysql.txt` first. It is NOT a protocol-accurate reproduction of
# experiments/baseline-multilang or experiments/benchforge-pycubrid-vs-pymysql (use the commands
# and revisions recorded in those experiments), and its results are not current comparisons.
historical-python-mysql:
	mkdir -p results
	python3 scripts/apply_schema.py --with-mysql
	cd python && pytest bench_tier0_mysql.py bench_pymysql.py bench_pymysql_extended.py bench_sqlalchemy_mysql.py --benchmark-json=../results/historical_python_mysql.json -v

# TypeScript
tier0-ts:
	cd typescript && npm run tier0

tier1-ts:
	cd typescript && npm run tier1

tier1-ts-extended:
	cd typescript && npm run tier1:extended

# Go
tier0-go:
	cd go && go test -v -run TestTier0 -timeout 120s ./...

# Rust
tier0-rust:
	cd rust && cargo run --release --bin tier0

tier1-go:
	cd go && go test -v -bench 'Benchmark(Cubrid|Mysql)(InsertSequential|SelectByPK|SelectFullScan|UpdateIndexed|DeleteSequential)$$' -benchtime 5x -timeout 900s ./...

tier1-go-extended:
	cd go && go test -v -bench 'Benchmark(Cubrid|Mysql)(ConnectDisconnect|PreparedStatement|BatchInsert|ConcurrentSelect)$$' -benchtime 5x -timeout 900s ./...

tier1-rust:
	cd rust && cargo bench

all: tier0 tier1-python tier1-python-extended tier2-python tier0-ts tier1-ts tier1-ts-extended tier0-go tier1-go tier1-go-extended
