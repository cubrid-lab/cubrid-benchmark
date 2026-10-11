#!/usr/bin/env bash
# Reproduce experiments/pycubrid-executemany-overhead (cubrid-lab/pycubrid#822).
#
# Prerequisites (see README.md "Reproduction"):
#   docker run -d --name inv822-114 -e CUBRID_DB=testdb -p 33531:33000 cubrid/cubrid:11.4
#   PY_MAIN:      python3.12 of a venv with pycubrid installed from the pinned commit ($MAIN_SHA)
#   PY_CUBRIDDB:  python3.12 of a venv with CUBRIDdb (cubrid-python v11.3.0.51) installed
#   MAIN_SHA, CUBRIDDB_SHA: source commits, recorded in the raw JSON.
#   CONTAINER:    name of the CUBRID container (the SQL-log and sql_log on/off steps use docker exec)
#   CONTAINER_IP: bridge IP of that container (the "direct" sensitivity run bypasses docker-proxy)
#
# Usage: experiments/pycubrid-executemany-overhead/run.sh <run-dir> [trials]
#   QUICK=1 shrinks every stage to a smoke run.
set -euo pipefail

RUN_DIR=${1:?run dir}
TRIALS=${2:-5}
HOST=${CUBRID_HOST:-localhost}
PORT=${CUBRID_PORT:-33531}
DB=${CUBRID_DB:-testdb}
CONTAINER=${CONTAINER:-inv822-114}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BENCH="$ROOT/scripts/executemany_overhead_bench.py"
RAW="$RUN_DIR/raw"
if [ -d "$RAW" ] && [ -n "$(ls -A "$RAW")" ]; then
  echo "refusing to write into non-empty $RAW (stale trial files would be pooled)" >&2
  exit 1
fi
mkdir -p "$RAW" "$RUN_DIR/derived"
Q=""; [ "${QUICK:-0}" = 1 ] && Q="--quick"
COMMON=(--host "$HOST" --port "$PORT" --db "$DB")

py()  { "$PY_MAIN" -I "$BENCH" --driver pycubrid --driver-commit "$MAIN_SHA" "${COMMON[@]}" "$@"; }
dbd() { "$PY_CUBRIDDB" -I "$BENCH" --driver cubriddb --driver-commit "$CUBRIDDB_SHA" "${COMMON[@]}" "$@"; }
load() { uptime | tee -a "$RAW/loadavg.log"; }

py --init
# 1. end-to-end comparison of execution paths; driver order alternates every trial.
for t in $(seq 1 "$TRIALS"); do
  if [ $((t % 2)) = 1 ]; then ORDER="py dbd"; else ORDER="dbd py"; fi
  for d in $ORDER; do
    load
    if [ "$d" = py ]; then py --stage compare --trial "$t" $Q --output "$RAW/compare_pycubrid_t$t.json"
    else dbd --stage compare --trial "$t" $Q --output "$RAW/compare_cubriddb_t$t.json"; fi
  done
done
# 2. client/server phase split (pycubrid only), insert
for t in $(seq 1 "$TRIALS"); do load; py --stage phases --trial "$t" $Q --output "$RAW/phases_pycubrid_t$t.json"; done
# 3. cProfile at 10,000 rows
for t in 1 2 3; do load; py --stage profile --trial "$t" --sizes 10000 --output "$RAW/profile_pycubrid_t$t.json"; done
# 4. tracemalloc
load; py --stage memory --trial 1 --output "$RAW/memory_pycubrid_t1.json"
# 5. broker SQL log slices (SQL_LOG=ON is the default of BROKER1)
SL=(--container "$CONTAINER" --sqllog-out "$RAW/sqllog_slices.txt")
py  --stage serverlog --ops insert --sizes 100,1000,10000 "${SL[@]}" --output "$RAW/serverlog_pycubrid_insert.json"
dbd --stage serverlog --ops insert --sizes 100,1000,10000 "${SL[@]}" --output "$RAW/serverlog_cubriddb_insert.json"
py  --stage serverlog --ops update,delete,merge --sizes 1000 "${SL[@]}" --output "$RAW/serverlog_pycubrid_other.json"
dbd --stage serverlog --ops update,delete,merge --sizes 1000 "${SL[@]}" --output "$RAW/serverlog_cubriddb_other.json"
# 6. sensitivity: broker SQL logging off (the broker writes one log line per batch statement)
docker exec -u cubrid "$CONTAINER" bash -lc 'broker_changer broker1 sql_log off' | tee -a "$RAW/broker_changer.log"
for t in $(seq 1 3); do
  load
  py  --stage compare --ops insert --trial "$t" $Q --output "$RAW/sqllogoff_pycubrid_t$t.json"
  dbd --stage compare --ops insert --trial "$t" $Q --output "$RAW/sqllogoff_cubriddb_t$t.json"
done
for t in 1 2 3; do load; py --stage phases --trial "$t" $Q --sizes 1000,10000 --output "$RAW/sqllogoff_phases_pycubrid_t$t.json"; done
docker exec -u cubrid "$CONTAINER" bash -lc 'broker_changer broker1 sql_log on' | tee -a "$RAW/broker_changer.log"
# 7. sensitivity: connect to the container bridge IP directly (no docker-proxy hop)
if [ -n "${CONTAINER_IP:-}" ]; then
  for t in 1 2 3; do
    load
    "$PY_MAIN" -I "$BENCH" --driver pycubrid --driver-commit "$MAIN_SHA" --host "$CONTAINER_IP" --port 33000 \
      --db "$DB" --stage compare --ops insert --trial "$t" $Q --output "$RAW/direct_pycubrid_t$t.json"
    "$PY_CUBRIDDB" -I "$BENCH" --driver cubriddb --driver-commit "$CUBRIDDB_SHA" --host "$CONTAINER_IP" --port 33000 \
      --db "$DB" --stage compare --ops insert --trial "$t" $Q --output "$RAW/direct_cubriddb_t$t.json"
  done
fi

gzip -9 -f "$RAW/sqllog_slices.txt"
python3 -I "$ROOT/scripts/summarize_executemany_overhead.py" "$RUN_DIR"
