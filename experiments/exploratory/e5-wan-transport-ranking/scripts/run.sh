#!/bin/bash
# Validate the static crossover, smoke-test, then run the 18-condition E5 matrix.
#   bash scripts/run.sh --smoke
#   bash scripts/run.sh
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "${EXP_DIR}"

python3 "${SCRIPT_DIR}/generate_topologies.py"
python3 "${SCRIPT_DIR}/generate_workloads.py"
python3 "${SCRIPT_DIR}/static_model.py"
python3 "${SCRIPT_DIR}/validate.py"

run() {
  local scale=$1
  local mapping=$2
  local cca=$3
  local value
  case "${scale}" in
    0.5x) value=0.5 ;;
    1x) value=1.0 ;;
    2x) value=2.0 ;;
  esac
  local run_id="wan${value}__${mapping}__${cca}"
  if [[ -f "results/raw/${run_id}/fct.txt" ]]; then
    echo "already finished ${run_id}"
    return 0
  fi
  "${SCRIPT_DIR}/run_one.sh" "${scale}" "${mapping}" "${cca}"
}

echo "smoke: λ=1 / latency-ring / PFC"
run 1x latency-ring pfc
echo "smoke: λ=1 / bandwidth-ring / PFC"
run 1x bandwidth-ring pfc
python3 "${SCRIPT_DIR}/analyze.py" --gate-pair \
  "${EXP_DIR}/results/raw/wan1.0__latency-ring__pfc" \
  "${EXP_DIR}/results/raw/wan1.0__bandwidth-ring__pfc"

echo "smoke: λ=1 / latency-ring / DCQCN"
run 1x latency-ring dcqcn
echo "smoke: λ=1 / bandwidth-ring / DCQCN"
run 1x bandwidth-ring dcqcn
python3 "${SCRIPT_DIR}/analyze.py" --gate-pair \
  "${EXP_DIR}/results/raw/wan1.0__latency-ring__dcqcn" \
  "${EXP_DIR}/results/raw/wan1.0__bandwidth-ring__dcqcn"

if [[ "${1:-}" == "--smoke" ]]; then
  python3 "${SCRIPT_DIR}/analyze.py"
  echo "smoke tests passed"
  exit 0
fi

for scale in 0.5x 1x 2x; do
  for mapping in latency-ring bandwidth-ring; do
    for cca in pfc dcqcn hpcc; do
      run "${scale}" "${mapping}" "${cca}"
    done
  done
done

python3 "${SCRIPT_DIR}/analyze.py"
echo "summary: results/summary.csv"
