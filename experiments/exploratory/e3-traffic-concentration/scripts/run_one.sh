#!/bin/bash
# One redesigned-E3 condition.
# Usage: run_one.sh <1to1|2to1|4to1> <ring16|ring44> <pfc|dcqcn|hpcc|fixed-rate>
# TRANSPORT_CONNECTION_MODE is PERSISTENT, set in configs/cca.
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: run_one.sh <1to1|2to1|4to1> <ring16|ring44> <pfc|dcqcn|hpcc|fixed-rate>" >&2
  exit 1
fi

FABRIC=$1
COLLECTIVE=$2
TRANSPORT=$3
RUN_ID="${FABRIC}__${COLLECTIVE}__${TRANSPORT}"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
STUDY_DIR=$(cd "${EXP_DIR}/../../.." && pwd)
ASTRA_DIR="${STUDY_DIR}/astra-sim"
NS3_DIR="${ASTRA_DIR}/extern/network_backend/ns-3"
RUN_DIR="${EXP_DIR}/results/raw/${RUN_ID}"

case "${FABRIC}" in
  1to1|2to1|4to1) ;;
  *) echo "unknown fabric ${FABRIC}" >&2; exit 1 ;;
esac
case "${COLLECTIVE}" in
  ring16|ring44) ;;
  *) echo "unknown collective ${COLLECTIVE}" >&2; exit 1 ;;
esac
case "${TRANSPORT}" in
  pfc) CCA_FILE=pfc-only.txt ;;
  fixed-rate|dcqcn|hpcc) CCA_FILE="${TRANSPORT}.txt" ;;
  *) echo "unknown transport ${TRANSPORT}" >&2; exit 1 ;;
esac

rm -rf "${RUN_DIR}"
mkdir -p "${RUN_DIR}"
python3 - "${EXP_DIR}/configs/cca/${CCA_FILE}" "${RUN_DIR}/network.txt" "${FABRIC}" "${RUN_ID}" << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[4] / "common"))
import netconfig
src, dst, fabric, run_id = sys.argv[1:]
rel = f"results/raw/{run_id}"
netconfig.rewrite(src, dst, {
    "TOPOLOGY_FILE": f"configs/physical/clos-{fabric}.txt",
    "FLOW_FILE": "configs/flow.txt",
    "TRACE_FILE": f"configs/physical/trace-{fabric}.txt",
    "TRACE_OUTPUT_FILE": f"{rel}/mix.tr",
    "FCT_OUTPUT_FILE": f"{rel}/fct.txt",
    "PFC_OUTPUT_FILE": f"{rel}/pfc.txt",
    "QLEN_MON_FILE": f"{rel}/qlen.txt",
    "CCA_TRACE_FILE": f"{rel}/cca_trace.csv",
    "FLOW_TRACE_FILE": f"{rel}/flows.csv",
    "PATH_TRACE_FILE": f"{rel}/paths.csv",
    "COUNTERS_OUTPUT_FILE": f"{rel}/counters.txt",
    "LINK_REPORT_FILE": f"{rel}/link_report.txt",
})
PY

BINARY=$(find "${NS3_DIR}/build/scratch" -maxdepth 1 -type f -name '*AstraSimNetwork*' -perm -111 | head -n 1)
if [[ -z "${BINARY}" ]]; then
  echo "AstraSimNetwork binary not found. Build it before this experiment." >&2
  exit 1
fi

START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cd "${EXP_DIR}"
set +e
"${BINARY}" \
  --workload-configuration="${EXP_DIR}/workload/allreduce" \
  --system-configuration="${EXP_DIR}/configs/system/${COLLECTIVE}.json" \
  --network-configuration="${RUN_DIR}/network.txt" \
  --remote-memory-configuration="${EXP_DIR}/configs/memory.json" \
  --logical-topology-configuration="${EXP_DIR}/configs/logical/${COLLECTIVE}.json" \
  --comm-group-configuration=empty \
  > "${RUN_DIR}/stdout.log" 2>&1
STATUS=$?
set -e
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [[ ${STATUS} -ne 0 ]]; then
  echo "${RUN_ID} exited ${STATUS}. See ${RUN_DIR}/stdout.log" >&2
  exit "${STATUS}"
fi

python3 - "${RUN_DIR}/meta.json" << PY
import hashlib, json, os, socket, sys
sys.path.insert(0, "${STUDY_DIR}/experiments/common")
import netconfig
out = sys.argv[1]
exp = "${EXP_DIR}"
def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()
net = netconfig.effective("${RUN_DIR}/network.txt")
uplink = {"1to1": "200Gbps", "2to1": "100Gbps", "4to1": "50Gbps"}["${FABRIC}"]
meta = {
    "run_id": "${RUN_ID}",
    "fabric": "${FABRIC}",
    "uplink_rate": uplink,
    "gpu_tor_rate": "200Gbps",
    "link_delay": "500ns",
    "spine_count": 4,
    "collective": "${COLLECTIVE}",
    "logical_dims": ["16"] if "${COLLECTIVE}" == "ring16" else ["4", "4"],
    "transport": "${TRANSPORT}",
    "transport_connection_mode": net.get("TRANSPORT_CONNECTION_MODE"),
    "cc_mode": net.get("CC_MODE"),
    "has_win": net.get("HAS_WIN"),
    "enable_qcn": net.get("ENABLE_QCN"),
    "enable_pfc": net.get("ENABLE_PFC"),
    "pfc_threshold_validation": net.get("PFC_THRESHOLD_VALIDATION"),
    "buffer_size_mib": net.get("BUFFER_SIZE"),
    "ecn_maps": {k: net.get(k) for k in ("KMIN_MAP", "KMAX_MAP", "PMAX_MAP")},
    "trace_level": net.get("TRACE_LEVEL"),
    "queue_log_min_bytes": net.get("QUEUE_LOG_MIN_BYTES"),
    "ecmp_seed": "switch-node-id",
    "ports_file": os.path.join(exp, "configs", "physical", "ports-${FABRIC}.json"),
    "topology_file": os.path.join(exp, "configs", "physical", "clos-${FABRIC}.txt"),
    "study_commit": os.popen("git -C ${STUDY_DIR} rev-parse HEAD").read().strip(),
    "astra_commit": os.popen("git -C ${ASTRA_DIR} rev-parse HEAD").read().strip(),
    "ns3_commit": os.popen("git -C ${NS3_DIR} rev-parse HEAD").read().strip(),
    "topology_sha256": sha(os.path.join(exp, "configs", "physical", "clos-${FABRIC}.txt")),
    "logical_sha256": sha(os.path.join(exp, "configs", "logical", "${COLLECTIVE}.json")),
    "system_sha256": sha(os.path.join(exp, "configs", "system", "${COLLECTIVE}.json")),
    "cca_sha256": sha(os.path.join(exp, "configs", "cca", "${CCA_FILE}")),
    "cca_config_hash": netconfig.semantic_hash("${RUN_DIR}/network.txt"),
    "workload_sha256": sha(os.path.join(exp, "workload", "allreduce.0.et")),
    "hostname": socket.gethostname(),
    "start": "${START}",
    "end": "${END}",
}
with open(out, "w") as handle:
    json.dump(meta, handle, indent=2)
    handle.write("\n")
PY

python3 "${SCRIPT_DIR}/analyze.py" --one "${RUN_DIR}"
