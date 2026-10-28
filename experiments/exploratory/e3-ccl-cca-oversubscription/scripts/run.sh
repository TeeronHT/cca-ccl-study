#!/bin/bash
# Validate, smoke-test, then run the 18-condition E3 matrix.
#   bash scripts/run.sh           full matrix after the smoke gate
#   bash scripts/run.sh --smoke   stop after the three smoke runs
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "${EXP_DIR}"

python3 "${SCRIPT_DIR}/generate_topologies.py"
python3 "${SCRIPT_DIR}/generate_workload.py"
python3 "${SCRIPT_DIR}/validate.py"

run() {
  "${SCRIPT_DIR}/run_one.sh" "$1" "$2" "$3"
}

echo "smoke: 1:1 / 1D / PFC"
run 1to1 1d pfc
echo "smoke: 1:1 / 2D / PFC"
run 1to1 2d pfc
echo "smoke: 4:1 / 2D / DCQCN"
run 4to1 2d dcqcn
python3 "${SCRIPT_DIR}/analyze.py" --gate "${EXP_DIR}/results/raw/4to1__2d__dcqcn"

if [[ "${1:-}" == "--smoke" ]]; then
  python3 "${SCRIPT_DIR}/analyze.py"
  echo "smoke tests passed"
  exit 0
fi

for fabric in 1to1 2to1 4to1; do
  for layout in 1d 2d; do
    for cca in pfc dcqcn hpcc; do
      run_id="${fabric}__${layout}__${cca}"
      if [[ -f "results/raw/${run_id}/fct.txt" ]]; then
        continue
      fi
      run "${fabric}" "${layout}" "${cca}"
    done
  done
done

python3 "${SCRIPT_DIR}/analyze.py"
echo "summary: results/summary.csv"
