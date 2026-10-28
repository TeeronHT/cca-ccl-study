#!/bin/bash
# Calibrate T_ref, smoke-test, then run the 48 E6 conditions.
#   bash scripts/run.sh --smoke
#   bash scripts/run.sh
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "${EXP_DIR}"

python3 "${SCRIPT_DIR}/generate_topologies.py"
python3 "${SCRIPT_DIR}/obtain_stragglar_schedule.py"
python3 "${SCRIPT_DIR}/translate_schedule.py"
python3 "${SCRIPT_DIR}/validate_schedule.py"
python3 "${SCRIPT_DIR}/generate_workloads.py" --bootstrap
python3 "${SCRIPT_DIR}/validate_experiment.py"

run() {
  local topo=$1 schedule=$2 pattern=$3 tau=$4 cca=$5
  local run_id="${topo}__${pattern}__${tau}__${schedule}__${cca}"
  if [[ "${pattern}" == "symmetric" ]]; then
    run_id="${topo}__symmetric__${schedule}__${cca}"
  fi
  if [[ -f "results/raw/${run_id}/fct.txt" ]]; then
    echo "already finished ${run_id}"
    return 0
  fi
  "${SCRIPT_DIR}/run_one.sh" "${topo}" "${schedule}" "${pattern}" "${tau}" "${cca}"
}

echo "smoke 1 / calibration: 2:1 symmetric Ring PFC"
run 2to1 ring symmetric tau0 pfc
python3 "${SCRIPT_DIR}/analyze.py" --tref "${EXP_DIR}/results/raw/2to1__symmetric__ring__pfc"
python3 "${SCRIPT_DIR}/generate_arrivals.py"
python3 "${SCRIPT_DIR}/generate_workloads.py"
python3 "${SCRIPT_DIR}/paper_model.py"
python3 "${SCRIPT_DIR}/validate_experiment.py" --full

echo "smoke 2: 2:1 one-late tau0.50 Ring PFC"
run 2to1 ring one-late tau0.50 pfc
python3 "${SCRIPT_DIR}/analyze.py" --gate-late "${EXP_DIR}/results/raw/2to1__one-late__tau0.50__ring__pfc"

echo "smoke 3: 2:1 one-late tau0.50 StragglAR PFC"
run 2to1 stragglar one-late tau0.50 pfc
python3 "${SCRIPT_DIR}/analyze.py" --gate-stragglar "${EXP_DIR}/results/raw/2to1__one-late__tau0.50__stragglar__pfc"

echo "smoke 4: 2:1 one-late tau0.50 StragglAR DCQCN"
run 2to1 stragglar one-late tau0.50 dcqcn
python3 "${SCRIPT_DIR}/analyze.py" --gate-dcqcn "${EXP_DIR}/results/raw/2to1__one-late__tau0.50__stragglar__dcqcn"

if [[ "${1:-}" == "--smoke" ]]; then
  python3 "${SCRIPT_DIR}/analyze.py"
  echo "smoke tests passed"
  exit 0
fi

for schedule in ring stragglar; do
  for cca in pfc dcqcn hpcc; do
    run 2to1 "${schedule}" symmetric tau0 "${cca}"
    for pattern in one-late two-late; do
      for tau in tau0.25 tau0.50 tau1.00; do
        run 2to1 "${schedule}" "${pattern}" "${tau}" "${cca}"
      done
    done
    run 1to1 "${schedule}" one-late tau0.50 "${cca}"
  done
done

python3 "${SCRIPT_DIR}/analyze.py"
echo "summary: results/summary.csv"
