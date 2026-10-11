#!/usr/bin/env bash
# Reproduce experiments/pycubrid-prepared-batch (cubrid-lab/pycubrid#836).
#
# Prerequisites (see README.md "Reproduction"): one container per CUBRID version, e.g.
#   docker run -d --name pyc836-114 -e CUBRID_DB=testdb -p 33814:33000 cubrid/cubrid:11.4
#   PY_MAIN:  python3.12 of a venv with pycubrid installed from the pinned commit ($MAIN_SHA)
#   MAIN_SHA: pycubrid source commit, recorded in the raw JSON.
#   TARGETS:  space-separated "label:port:container:trials" entries, measured one after
#             another; the other containers are stopped while one is measured.
#
# Usage: experiments/pycubrid-prepared-batch/run.sh <run-dir>
#   QUICK=1 shrinks the compare stage to a smoke run.
set -euo pipefail

RUN_DIR=${1:?run dir}
TARGETS=${TARGETS:-"11.4:33814:pyc836-114:5 11.2:33812:pyc836-112:3 11.0:33811:pyc836-110:3 10.2:33810:pyc836-102:3"}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BENCH="$ROOT/scripts/prepared_batch_bench.py"
RAW="$RUN_DIR/raw"
if [ -d "$RAW" ] && [ -n "$(ls -A "$RAW")" ]; then
  echo "refusing to write into non-empty $RAW (stale trial files would be pooled)" >&2
  exit 1
fi
mkdir -p "$RAW" "$RUN_DIR/derived"
Q=""; [ "${QUICK:-0}" = 1 ] && Q="--quick"
ALL_CONTAINERS=$(for t in $TARGETS; do echo "$t" | cut -d: -f3; done)

wait_db() {
  for _ in $(seq 1 60); do
    "$PY_MAIN" -I -c "import pycubrid,sys; pycubrid.connect(host='localhost', port=int(sys.argv[1]), database='testdb', user='dba', password='').close()" "$1" 2>/dev/null && return 0
    sleep 5
  done
  return 1
}

for t in $TARGETS; do
  IFS=: read -r label port container trials <<<"$t"
  for other in $ALL_CONTAINERS; do [ "$other" = "$container" ] || docker stop "$other" >/dev/null 2>&1 || true; done
  docker start "$container" >/dev/null
  wait_db "$port"
  py() { "$PY_MAIN" -I "$BENCH" --driver-commit "$MAIN_SHA" --cubrid-version "$label" --port "$port" "$@"; }
  # 1. semantics probes (untimed)
  py --stage semantics --output "$RAW/semantics_${label}.json"
  # 2. end-to-end comparison; variant order rotates every trial
  py --init
  for i in $(seq 1 "$trials"); do
    uptime | sed "s/^/[$label t$i] /" | tee -a "$RAW/loadavg.log"
    py --stage compare --trial "$i" $Q --output "$RAW/compare_${label}_t$i.json"
  done
done
for c in $ALL_CONTAINERS; do docker start "$c" >/dev/null; done
python3 -I "$ROOT/scripts/summarize_prepared_batch.py" "$RUN_DIR"
