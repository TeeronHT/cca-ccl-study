#!/bin/bash
# Persistent-QP microbenchmark: 2 topologies x 2 CCAs x 2 modes x 2 cases = 16 short runs.
#   bash scripts/run.sh            run everything, then check
#   bash scripts/run.sh --check    only re-run the checker on existing results
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
STUDY_DIR=$(cd "${EXP_DIR}/../../.." && pwd)
NS3_DIR="${STUDY_DIR}/astra-sim/extern/network_backend/ns-3"
cd "${EXP_DIR}"

if [[ "${1:-}" != "--check" ]]; then
  python3 "${SCRIPT_DIR}/generate.py"
  BINARY=${BINARY:-$(find "${NS3_DIR}/build/scratch" -maxdepth 1 -type f -name '*AstraSimNetwork*' -perm -111 2>/dev/null | head -n 1)}
  if [[ -z "${BINARY}" ]]; then
    echo "AstraSimNetwork binary not found. Build it before this experiment." >&2
    exit 1
  fi
  for topology in bottleneck ecmp; do
    for cca in dcqcn hpcc; do
      for mode in LEGACY PERSISTENT; do
        for case in b2b gap; do
          run_dir=$(python3 "${SCRIPT_DIR}/generate.py" config "${topology}" "${cca}" "${mode}" "${case}")
          echo "running $(basename "${run_dir}")"
          set +e
          "${BINARY}" \
            --workload-configuration="${EXP_DIR}/workload/${case}/trace" \
            --system-configuration="${EXP_DIR}/configs/system.json" \
            --network-configuration="${run_dir}/network.txt" \
            --remote-memory-configuration="${EXP_DIR}/configs/memory.json" \
            --logical-topology-configuration="${EXP_DIR}/configs/logical.json" \
            --comm-group-configuration=empty \
            > "${run_dir}/stdout.log" 2>&1
          status=$?
          set -e
          if [[ ${status} -ne 0 ]]; then
            echo "$(basename "${run_dir}") exited ${status}; see ${run_dir}/stdout.log" >&2
            exit "${status}"
          fi
        done
      done
    done
  done
fi

python3 "${SCRIPT_DIR}/check.py"
