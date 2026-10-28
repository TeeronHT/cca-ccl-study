#!/bin/bash
# E5: validate, smoke-test (A-D), then run the 18-condition primary matrix.
#   bash scripts/run.sh --smoke          stop after the smoke tests
#   bash scripts/run.sh                  smoke tests, then the matrix
#   bash scripts/run.sh --skip-prereqs   do not require the backend-validation markers
#
# The matrix does not start unless the legacy regression, the persistent-QP
# microbenchmark and the BDP unit test have passed with this binary
# (experiments/backend-validation/*/results/PASSED), the validator passes,
# and smoke A-D pass.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
BV_DIR=$(cd "${EXP_DIR}/../../backend-validation" && pwd)
cd "${EXP_DIR}"

SKIP_PREREQS=0
SMOKE_ONLY=0
for arg in "$@"; do
  case "${arg}" in
    --smoke) SMOKE_ONLY=1 ;;
    --skip-prereqs) SKIP_PREREQS=1 ;;
    *) echo "unknown argument ${arg}" >&2; exit 1 ;;
  esac
done

python3 "${SCRIPT_DIR}/generate_topologies.py"
python3 "${SCRIPT_DIR}/generate_configs.py"
python3 "${SCRIPT_DIR}/generate_workloads.py"
python3 "${SCRIPT_DIR}/static_model.py"

if [[ ${SKIP_PREREQS} -eq 0 ]]; then
  missing=0
  for harness in legacy-regression persistent-qp bdp-units; do
    if [[ ! -f "${BV_DIR}/${harness}/results/PASSED" ]]; then
      echo "prerequisite missing: ${BV_DIR}/${harness}/results/PASSED (run that harness first)" >&2
      missing=1
    fi
  done
  if [[ ${missing} -ne 0 ]]; then
    exit 1
  fi
  python3 "${SCRIPT_DIR}/validate.py"
else
  python3 "${SCRIPT_DIR}/validate.py" --static-only
fi

run() {
  local scale=$1 mapping=$2 transport=$3
  local value
  case "${scale}" in
    0.5x) value=0.5 ;;
    1x) value=1.0 ;;
    2x) value=2.0 ;;
  esac
  local run_id="wan${value}__${mapping}__${transport}"
  if [[ -f "results/raw/${run_id}/fct.txt" && -f "results/raw/${run_id}/counters.txt" && -f "results/raw/${run_id}/meta.json" ]]; then
    echo "already finished ${run_id}"
    return 0
  fi
  "${SCRIPT_DIR}/run_one.sh" "${scale}" "${mapping}" "${transport}"
}

echo "smoke A: λ=1 / latency-ring / fixed-rate"
run 1x latency-ring fixed-rate
python3 "${SCRIPT_DIR}/analyze.py" --gate-fixed "${EXP_DIR}/results/raw/wan1.0__latency-ring__fixed-rate"

echo "smoke B: λ=1 / latency-ring / DCQCN"
run 1x latency-ring dcqcn
python3 "${SCRIPT_DIR}/analyze.py" --gate-dcqcn "${EXP_DIR}/results/raw/wan1.0__latency-ring__dcqcn"

echo "smoke C: λ=1 / bandwidth-ring / DCQCN"
run 1x bandwidth-ring dcqcn
python3 "${SCRIPT_DIR}/analyze.py" --gate-dcqcn "${EXP_DIR}/results/raw/wan1.0__bandwidth-ring__dcqcn"

echo "smoke D: λ=1 / both rings / HPCC"
run 1x latency-ring hpcc
python3 "${SCRIPT_DIR}/analyze.py" --gate-hpcc "${EXP_DIR}/results/raw/wan1.0__latency-ring__hpcc"
run 1x bandwidth-ring hpcc
python3 "${SCRIPT_DIR}/analyze.py" --gate-hpcc "${EXP_DIR}/results/raw/wan1.0__bandwidth-ring__hpcc"

if [[ ${SMOKE_ONLY} -eq 1 ]]; then
  python3 "${SCRIPT_DIR}/analyze.py" || true
  echo "smoke tests passed"
  exit 0
fi

for scale in 0.5x 1x 2x; do
  for mapping in latency-ring bandwidth-ring; do
    for transport in fixed-rate dcqcn hpcc; do
      run "${scale}" "${mapping}" "${transport}"
    done
  done
done

python3 "${SCRIPT_DIR}/analyze.py"
echo "summary: results/summary.csv"
