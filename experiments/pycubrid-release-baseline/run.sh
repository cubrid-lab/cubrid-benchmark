#!/usr/bin/env bash
# Reproduce experiments/pycubrid-release-baseline (cubrid-lab/pycubrid#797).
#
# Prerequisites (see README.md "Reproduction"):
#   docker run -d --name bench797-114 -e CUBRID_DB=testdb -p 33511:33000 cubrid/cubrid:11.4
#   PY_MAIN, PY_REL, PY_CUBRIDDB: python3.12 interpreters of three venvs with
#     pycubrid@main, pycubrid==1.10.0 and CUBRIDdb (cubrid-python v11.3.0.51) installed.
#   MAIN_SHA, CUBRIDDB_SHA: source commits, recorded in the raw JSON.
#
# Usage: experiments/pycubrid-release-baseline/run.sh <run-dir> [trials]
set -euo pipefail

RUN_DIR=${1:?run dir}
TRIALS=${2:-3}
HOST=${CUBRID_HOST:-localhost}
PORT=${CUBRID_PORT:-33511}
DB=${CUBRID_DB:-testdb}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BENCH="$ROOT/scripts/driver_baseline_bench.py"
PHASE="$ROOT/scripts/phase_decomposed_bench.py"
mkdir -p "$RUN_DIR/raw"

run_one() {  # label
  case $1 in
    pycubrid-main)   "$PY_MAIN" -I "$BENCH" --driver pycubrid --label "$1" --driver-commit "$MAIN_SHA" \
                       --host "$HOST" --port "$PORT" --db "$DB" --trial "$t" \
                       --output "$RUN_DIR/raw/$1_t$t.json" ;;
    pycubrid-1.10.0) "$PY_REL" -I "$BENCH" --driver pycubrid --label "$1" --driver-commit "pypi:1.10.0" \
                       --host "$HOST" --port "$PORT" --db "$DB" --trial "$t" \
                       --output "$RUN_DIR/raw/$1_t$t.json" ;;
    cubriddb)        "$PY_CUBRIDDB" -I "$BENCH" --driver cubriddb --label "$1" --driver-commit "$CUBRIDDB_SHA" \
                       --host "$HOST" --port "$PORT" --db "$DB" --trial "$t" \
                       --output "$RUN_DIR/raw/$1_t$t.json" ;;
  esac
}

# Rotate the driver order every trial so slow drift on the shared host is spread evenly.
ORDERS=("pycubrid-main pycubrid-1.10.0 cubriddb" "pycubrid-1.10.0 cubriddb pycubrid-main" \
        "cubriddb pycubrid-main pycubrid-1.10.0")
for t in $(seq 1 "$TRIALS"); do
  for label in ${ORDERS[$(( (t - 1) % 3 ))]}; do
    uptime | tee -a "$RUN_DIR/raw/loadavg.log"
    run_one "$label"
  done
done

# Existing harness, pycubrid only (continuity with driver-comparison/pycubrid-1.0 runs).
for pair in "pycubrid-main:$PY_MAIN" "pycubrid-1.10.0:$PY_REL"; do
  label=${pair%%:*}; py=${pair#*:}
  uptime | tee -a "$RUN_DIR/raw/loadavg.log"
  "$py" -I "$PHASE" --host "$HOST" --port "$PORT" --db "$DB" \
    --iterations 200 --warmup 50 --output "$RUN_DIR/raw/phase_decomposed_$label.json"
done

python3 -I "$ROOT/scripts/summarize_driver_baseline.py" "$RUN_DIR"
