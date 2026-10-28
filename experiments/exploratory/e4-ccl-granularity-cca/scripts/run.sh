#!/bin/bash
# Validate, smoke-test, then run the 24-condition E4 matrix.
#   bash scripts/run.sh --smoke
#   bash scripts/run.sh
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "${EXP_DIR}"

python3 "${SCRIPT_DIR}/generate_topologies.py"
python3 "${SCRIPT_DIR}/generate_workloads.py"
python3 "${SCRIPT_DIR}/validate.py"

run() {
  local fabric=$1
  local chunk=$2
  local cca=$3
  local run_id="${fabric}__${chunk}__${cca}"
  if [[ -f "results/raw/${run_id}/fct.txt" && -f "results/raw/${run_id}/meta.json" ]]; then
    echo "already finished ${run_id}"
    return 0
  fi
  "${SCRIPT_DIR}/run_one.sh" "${fabric}" "${chunk}" "${cca}"
}

echo "smoke: 1:1 / K=1 / PFC"
run 1to1 k1 pfc
echo "smoke: 1:1 / K=64 / PFC"
run 1to1 k64 pfc
python3 "${SCRIPT_DIR}/analyze.py" --gate-granularity \
  "${EXP_DIR}/results/raw/1to1__k1__pfc" \
  "${EXP_DIR}/results/raw/1to1__k64__pfc"
# Persistent-QP mechanics must be visible even without congestion.
python3 "${SCRIPT_DIR}/analyze.py" --gate-persistence "${EXP_DIR}/results/raw/1to1__k64__pfc"

echo "smoke: 2:1 / K=1 / DCQCN"
run 2to1 k1 dcqcn
python3 "${SCRIPT_DIR}/analyze.py" --gate-contention "${EXP_DIR}/results/raw/2to1__k1__dcqcn"
# With contention present, later messages on a QP must start from carried state.
python3 "${SCRIPT_DIR}/analyze.py" --gate-persistence --require-carried-state \
  "${EXP_DIR}/results/raw/2to1__k1__dcqcn"

echo "smoke: 2:1 / K=64 / DCQCN"
run 2to1 k64 dcqcn
python3 "${SCRIPT_DIR}/analyze.py" --gate-granularity \
  "${EXP_DIR}/results/raw/2to1__k1__dcqcn" \
  "${EXP_DIR}/results/raw/2to1__k64__dcqcn"
python3 "${SCRIPT_DIR}/analyze.py" --gate-persistence --require-carried-state \
  "${EXP_DIR}/results/raw/2to1__k64__dcqcn"

if [[ "${1:-}" == "--smoke" ]]; then
  python3 "${SCRIPT_DIR}/analyze.py"
  echo "smoke tests passed"
  exit 0
fi

for fabric in 1to1 2to1; do
  for chunk in k1 k4 k16 k64; do
    for cca in pfc dcqcn hpcc; do
      run "${fabric}" "${chunk}" "${cca}"
    done
  done
done

python3 "${SCRIPT_DIR}/analyze.py"
echo "summary: results/summary.csv"
