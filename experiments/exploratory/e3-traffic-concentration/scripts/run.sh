#!/bin/bash
# Stage A of the redesigned E3.
#   bash scripts/run.sh --smoke    four checks, then stop
#   bash scripts/run.sh            those checks, then the other 14 runs
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

echo "smoke: 1:1 / 1D Ring [16] / fixed-rate"
run 1to1 ring16 fixed-rate
echo "smoke: 1:1 / 2D Ring [4,4] / fixed-rate"
run 1to1 ring44 fixed-rate
python3 "${SCRIPT_DIR}/analyze.py" --gate fixed "${EXP_DIR}/results/raw/1to1__ring16__fixed-rate"
python3 "${SCRIPT_DIR}/analyze.py" --gate fixed "${EXP_DIR}/results/raw/1to1__ring44__fixed-rate"

echo "smoke: 2:1 / 2D Ring [4,4] / DCQCN"
run 2to1 ring44 dcqcn
echo "smoke: 4:1 / 2D Ring [4,4] / DCQCN"
run 4to1 ring44 dcqcn
python3 "${SCRIPT_DIR}/analyze.py" --gate dcqcn "${EXP_DIR}/results/raw/2to1__ring44__dcqcn"
python3 "${SCRIPT_DIR}/analyze.py" --gate dcqcn "${EXP_DIR}/results/raw/4to1__ring44__dcqcn"

if [[ "${1:-}" == "--smoke" ]]; then
  echo "smoke tests passed"
  exit 0
fi

for fabric in 1to1 2to1 4to1; do
  for collective in ring16 ring44; do
    for transport in fixed-rate dcqcn hpcc; do
      run "${fabric}" "${collective}" "${transport}"
    done
  done
done

python3 "${SCRIPT_DIR}/analyze.py"
echo "summary: results/summary.csv"
