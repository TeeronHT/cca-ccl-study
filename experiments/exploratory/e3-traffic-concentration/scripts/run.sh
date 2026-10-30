#!/bin/bash
# Stage A of the redesigned E3.
#   bash scripts/run.sh --smoke    1:1 PFC for both collectives, then stop
#   bash scripts/run.sh            those checks, then the rest of PFC / DCQCN / HPCC
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "${EXP_DIR}"

python3 "${SCRIPT_DIR}/generate_topologies.py"
python3 "${SCRIPT_DIR}/generate_configs.py"
python3 "${SCRIPT_DIR}/generate_workload.py"
python3 "${SCRIPT_DIR}/validate.py"

run() {
  local fabric=$1 collective=$2 transport=$3
  local run_id="${fabric}__${collective}__${transport}"
  if [[ -f "results/raw/${run_id}/fct.txt" && -f "results/raw/${run_id}/meta.json" ]]; then
    echo "skip ${run_id}"
    return
  fi
  "${SCRIPT_DIR}/run_one.sh" "${fabric}" "${collective}" "${transport}"
}

echo "smoke: 1:1 / 1D Ring [16] / PFC"
run 1to1 ring16 pfc
echo "smoke: 1:1 / 2D Ring [4,4] / PFC"
run 1to1 ring44 pfc
python3 "${SCRIPT_DIR}/analyze.py" --gate pfc "${EXP_DIR}/results/raw/1to1__ring16__pfc"
python3 "${SCRIPT_DIR}/analyze.py" --gate pfc "${EXP_DIR}/results/raw/1to1__ring44__pfc"

if [[ "${1:-}" == "--smoke" ]]; then
  echo "smoke tests passed"
  exit 0
fi

for fabric in 1to1 2to1 4to1; do
  for collective in ring16 ring44; do
    for transport in pfc dcqcn hpcc; do
      run "${fabric}" "${collective}" "${transport}"
    done
  done
done

python3 "${SCRIPT_DIR}/analyze.py"
echo "summary: results/summary.csv"
