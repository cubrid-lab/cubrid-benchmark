#!/usr/bin/env bash
# Reproduce one run of experiments/pycubrid-pinned-lane-demo (cubrid-benchmark#30).
#
# Prerequisites (one CUBRID container per server version):
#   docker run -d --name bench30-cubrid-114 -e CUBRID_DB=benchdb -p 33681:33000 cubrid/cubrid:11.4
#   docker run -d --name bench30-cubrid-102 -e CUBRID_DB=benchdb -p 33682:33000 cubrid/cubrid:10.2
#   python3.12 on PATH (or PYTHON=/path/to/python3.12), and a clean checkout of this repository.
#
# Usage: experiments/pycubrid-pinned-lane-demo/run.sh <run-dir> <pycubrid-version> <role> [<baseline-run-dir>]
#   <run-dir>    runs/<YYYY-MM-DD>_<label>; must not exist (runs are immutable)
#   <role>       baseline or candidate
#   CUBRID_VERSION (default 11.4), CUBRID_PORT (default 33681), FIXED_HOST (optional fixed-host
#   name for the comparison identity, e.g. devbox-i5-9400F).
#
# Each run gets its own virtual environment. It is pinned by python/constraints-current.txt with
# pycubrid replaced by <pycubrid-version>, and checked by scripts/python_lanes.py verify-install.
# Then it measures python/bench_pycubrid.py, captures run.yaml with the same arguments as the
# pinned lanes in .github/workflows/bench.yml, and compares against <baseline-run-dir> when given.
# capture writes compares_to: null; the recorded candidate runs name their baseline there.
set -euo pipefail

RUN_DIR=${1:?run dir}
VERSION=${2:?pycubrid version}
ROLE=${3:?role (baseline or candidate)}
BASELINE=${4:-}
CUBRID_VERSION=${CUBRID_VERSION:-11.4}
export CUBRID_HOST=${CUBRID_HOST:-localhost}
export CUBRID_PORT=${CUBRID_PORT:-33681}
export CUBRID_DB=${CUBRID_DB:-benchdb}
PYTHON=${PYTHON:-python3.12}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
RUN_DIR=$(mkdir -p "$(dirname "$RUN_DIR")" && cd "$(dirname "$RUN_DIR")" && pwd)/$(basename "$RUN_DIR")
LABEL=$(basename "$RUN_DIR" | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}_//')

if [ -e "$RUN_DIR" ]; then
  echo "refusing to write into existing $RUN_DIR (runs are immutable)" >&2
  exit 1
fi
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

sed -E "s/^pycubrid==.*/pycubrid==$VERSION/" "$ROOT/python/constraints-current.txt" > "$WORK/constraints.txt"
"$PYTHON" -m venv "$WORK/venv"
VPY="$WORK/venv/bin/python"
"$VPY" -m pip install -q -r "$ROOT/python/requirements.txt" -c "$WORK/constraints.txt"
"$VPY" "$ROOT/scripts/python_lanes.py" verify-install --constraints "$WORK/constraints.txt" \
  --output "$WORK/versions.json"

uptime | tee -a "$WORK/loadavg.log"
(cd "$ROOT/python" && "$VPY" -m pytest -p no:cacheprovider bench_pycubrid.py -v \
  --benchmark-json="$WORK/python_tier1_pycubrid.json")
uptime | tee -a "$WORK/loadavg.log"

workload_revision=$(cd "$ROOT/python" && "$VPY" -c 'import bench_pycubrid as b; print(b.WORKLOAD_REVISION)')
fixed_host=()
[ -n "${FIXED_HOST:-}" ] && fixed_host=(--fixed-host "$FIXED_HOST")
(cd "$ROOT" && "$VPY" scripts/run_provenance.py capture \
  --out-dir "$RUN_DIR" \
  --label "$LABEL" \
  --role "$ROLE" \
  --results "$WORK/python_tier1_pycubrid.json" \
  --results-module bench_pycubrid.py \
  --scenario python-tier1 \
  --workload-revision "$workload_revision" \
  --schema "bench_driver_pycubrid (id INT PRIMARY KEY, name VARCHAR(100), amount INT)" \
  --data-count 10000 \
  --transaction-policy "autocommit=false; one commit per write round" \
  --measurement-protocol "pytest-benchmark pedantic; 1 warm-up + 5 rounds; untimed setup/teardown" \
  --cubrid-image "cubrid/cubrid:$CUBRID_VERSION" \
  --cubrid-host "$CUBRID_HOST" --cubrid-port "$CUBRID_PORT" --cubrid-db "$CUBRID_DB" \
  ${fixed_host[@]+"${fixed_host[@]}"} \
  --versions-file "$WORK/versions.json" \
  --notes "pycubrid $VERSION from PyPI; candidate-to-baseline demonstration for cubrid-benchmark#30" \
  --command "cd python && python -m pytest -p no:cacheprovider bench_pycubrid.py -v --benchmark-json=<run>/python_tier1_pycubrid.json")
cp "$WORK/loadavg.log" "$RUN_DIR/raw/loadavg.log"

if [ -n "$BASELINE" ]; then
  mkdir -p "$RUN_DIR/derived"
  # Exit 3 (not comparable) is a recorded outcome of this demonstration, not a script failure.
  status=0
  "$VPY" "$ROOT/scripts/compare_runs.py" --baseline-dir "$BASELINE" --candidate-dir "$RUN_DIR" \
    --output "$RUN_DIR/derived/comparison_vs_$(basename "$BASELINE" | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}_pycubrid-//').json" \
    || status=$?
  echo "compare_runs.py exit status: $status"
fi
