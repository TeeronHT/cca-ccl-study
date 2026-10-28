#!/bin/bash
# Legacy-fidelity regression: E0 PFC-only / DCQCN / HPCC and E1 DCQCN with the
# unchanged E0/E1 inputs, under TRANSPORT_CONNECTION_MODE LEGACY.
#
#   bash scripts/run.sh --record-baseline   run with the PRE-change binary, store baseline/
#   bash scripts/run.sh --compare           run with the extended binary, compare to baseline/
#
# Point BINARY at a specific executable to avoid rebuilding between the two:
#   BINARY=/path/to/old/AstraSimNetwork bash scripts/run.sh --record-baseline
set -euo pipefail

MODE=${1:-}
if [[ "${MODE}" != "--record-baseline" && "${MODE}" != "--compare" ]]; then
  echo "usage: run.sh --record-baseline | --compare" >&2
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
STUDY_DIR=$(cd "${EXP_DIR}/../../.." && pwd)
ASTRA_DIR="${STUDY_DIR}/astra-sim"
NS3_DIR="${ASTRA_DIR}/extern/network_backend/ns-3"
E0_DIR="${STUDY_DIR}/experiments/hoti22-reproduction/e0-incast"
E1_DIR="${STUDY_DIR}/experiments/hoti22-reproduction/e1-single-switch-allreduce"
cd "${EXP_DIR}"

BINARY=${BINARY:-$(find "${NS3_DIR}/build/scratch" -maxdepth 1 -type f -name '*AstraSimNetwork*' -perm -111 2>/dev/null | head -n 1)}
if [[ -z "${BINARY}" ]]; then
  echo "AstraSimNetwork binary not found. Build it (or set BINARY)." >&2
  exit 1
fi
if [[ "${MODE}" == "--record-baseline" ]]; then
  OUT="${EXP_DIR}/baseline"
else
  OUT="${EXP_DIR}/results/current"
fi
mkdir -p "${OUT}"
{
  echo "binary ${BINARY}"
  echo "study $(git -C "${STUDY_DIR}" rev-parse HEAD) dirty=$(git -C "${STUDY_DIR}" status --porcelain | wc -l | tr -d ' ')"
  echo "astra-sim $(git -C "${ASTRA_DIR}" rev-parse HEAD) dirty=$(git -C "${ASTRA_DIR}" status --porcelain | wc -l | tr -d ' ')"
  echo "ns-3 $(git -C "${NS3_DIR}" rev-parse HEAD) dirty=$(git -C "${NS3_DIR}" status --porcelain | wc -l | tr -d ' ')"
  echo "date $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} | tee "${OUT}/provenance.txt"

python3 "${E0_DIR}/scripts/generate_workload.py" > /dev/null
python3 "${E1_DIR}/scripts/generate_workload.py" > /dev/null

run_case() {
  local label=$1 exp_dir=$2 config=$3 workload=$4
  local run_dir="${OUT}/${label}"
  mkdir -p "${run_dir}"
  python3 "${SCRIPT_DIR}/compare.py" --write-config "${exp_dir}/configs/${config}" "${run_dir}/network.txt" "${run_dir}"
  echo "running ${label}"
  (
    cd "${exp_dir}"
    "${BINARY}" \
      --workload-configuration="${exp_dir}/workload/${workload}" \
      --system-configuration="${exp_dir}/configs/system.json" \
      --network-configuration="${run_dir}/network.txt" \
      --remote-memory-configuration="${exp_dir}/configs/memory.json" \
      --logical-topology-configuration="${exp_dir}/configs/logical-topology.json" \
      --comm-group-configuration=empty \
      > "${run_dir}/stdout.log" 2>&1
  )
  python3 "${SCRIPT_DIR}/compare.py" --summarize "${run_dir}"
}

run_case e0-pfc-only "${E0_DIR}" pfc-only.txt incast
run_case e0-dcqcn    "${E0_DIR}" dcqcn.txt    incast
run_case e0-hpcc     "${E0_DIR}" hpcc.txt     incast
run_case e1-dcqcn    "${E1_DIR}" dcqcn.txt    allreduce

if [[ "${MODE}" == "--compare" ]]; then
  python3 "${SCRIPT_DIR}/compare.py" --compare "${EXP_DIR}/baseline" "${OUT}" "${TOLERANCE:-0}"
else
  echo "baseline recorded under ${OUT}. Commit baseline/*/metrics.json and provenance.txt."
fi
