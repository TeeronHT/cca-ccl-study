#!/bin/bash
# BDP / time-unit / PFC-headroom sanity test. Four 2-host cases, each a
# single 1 MiB message. Invalid-headroom cases are expected to abort under
# PFC_THRESHOLD_VALIDATION STRICT; their link report is still written.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
STUDY_DIR=$(cd "${EXP_DIR}/../../.." && pwd)
NS3_DIR="${STUDY_DIR}/astra-sim/extern/network_backend/ns-3"
cd "${EXP_DIR}"

python3 "${SCRIPT_DIR}/bdp_units.py" --analytic
python3 "${SCRIPT_DIR}/bdp_units.py" --prepare

BINARY=${BINARY:-$(find "${NS3_DIR}/build/scratch" -maxdepth 1 -type f -name '*AstraSimNetwork*' -perm -111 2>/dev/null | head -n 1)}
if [[ -z "${BINARY}" ]]; then
  echo "AstraSimNetwork binary not found. Build it before this experiment." >&2
  exit 1
fi

for case_dir in results/raw/*/; do
  name=$(basename "${case_dir}")
  echo "running ${name}"
  set +e
  "${BINARY}" \
    --workload-configuration="${EXP_DIR}/workload/trace" \
    --system-configuration="${EXP_DIR}/configs/system.json" \
    --network-configuration="${EXP_DIR}/${case_dir}network.txt" \
    --remote-memory-configuration="${EXP_DIR}/configs/memory.json" \
    --logical-topology-configuration="${EXP_DIR}/configs/logical.json" \
    --comm-group-configuration=empty \
    > "${case_dir}stdout.log" 2>&1
  echo $? > "${case_dir}exit_status.txt"
  set -e
done

python3 "${SCRIPT_DIR}/bdp_units.py" --compare
