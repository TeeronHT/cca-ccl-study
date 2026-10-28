#!/bin/bash
# One E4 condition. Usage: run_one.sh 1to1 k4 dcqcn
# Runs under TRANSPORT_CONNECTION_MODE PERSISTENT (set in configs/cca/*.txt).
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: run_one.sh <1to1|2to1> <k1|k4|k16|k64> <pfc|dcqcn|hpcc>" >&2
  exit 1
fi

FABRIC=$1
CHUNK=$2
CCA=$3
RUN_ID="${FABRIC}__${CHUNK}__${CCA}"
K=${CHUNK#k}

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
STUDY_DIR=$(cd "${EXP_DIR}/../../.." && pwd)
ASTRA_DIR="${STUDY_DIR}/astra-sim"
NS3_DIR="${ASTRA_DIR}/extern/network_backend/ns-3"
RUN_DIR="${EXP_DIR}/results/raw/${RUN_ID}"

case "${CCA}" in
  pfc) CCA_FILE=pfc-only.txt ;;
  dcqcn) CCA_FILE=dcqcn.txt ;;
  hpcc) CCA_FILE=hpcc.txt ;;
  *) echo "unknown cca ${CCA}" >&2; exit 1 ;;
esac
case "${K}" in
  1|4|16|64) ;;
  *) echo "unknown granularity ${CHUNK}" >&2; exit 1 ;;
esac

rm -rf "${RUN_DIR}"
mkdir -p "${RUN_DIR}"
python3 - "${EXP_DIR}/configs/cca/${CCA_FILE}" "${RUN_DIR}/network.txt" "${FABRIC}" "${RUN_ID}" << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[4] / "common"))
import netconfig
src, dst, fabric, run_id = sys.argv[1:]
netconfig.rewrite(src, dst, {
    "TOPOLOGY_FILE": f"configs/physical/clos-{fabric}.txt",
    "FLOW_FILE": "configs/flow.txt",
    "TRACE_FILE": f"configs/physical/trace-{fabric}.txt",
    "TRACE_OUTPUT_FILE": f"results/raw/{run_id}/mix.tr",
    "FCT_OUTPUT_FILE": f"results/raw/{run_id}/fct.txt",
    "PFC_OUTPUT_FILE": f"results/raw/{run_id}/pfc.txt",
    "QLEN_MON_FILE": f"results/raw/{run_id}/qlen.txt",
    "CCA_TRACE_FILE": f"results/raw/{run_id}/cca_trace.csv",
    "FLOW_TRACE_FILE": f"results/raw/{run_id}/flows.csv",
    "PATH_TRACE_FILE": f"results/raw/{run_id}/paths.csv",
    "COUNTERS_OUTPUT_FILE": f"results/raw/{run_id}/counters.txt",
    "LINK_REPORT_FILE": f"results/raw/{run_id}/link_report.txt",
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
  --system-configuration="${EXP_DIR}/configs/granularity/chunks-${K}.json" \
  --network-configuration="${RUN_DIR}/network.txt" \
  --remote-memory-configuration="${EXP_DIR}/configs/memory.json" \
  --logical-topology-configuration="${EXP_DIR}/configs/logical/allreduce-1d.json" \
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
meta = {
    "run_id": "${RUN_ID}",
    "topology": "${FABRIC}",
    "oversubscription": "${FABRIC}",
    "spine_count": {"1to1": 4, "2to1": 2}["${FABRIC}"],
    "chunk_count": int("${K}"),
    "cca": "${CCA}",
    "transport_connection_mode": net["TRANSPORT_CONNECTION_MODE"],
    "rendezvous": False,
    "has_win": net.get("HAS_WIN"),
    "cc_mode": net.get("CC_MODE"),
    "enable_pfc": net.get("ENABLE_PFC"),
    "pfc_threshold_validation": net.get("PFC_THRESHOLD_VALIDATION"),
    "buffer_size_mib": net.get("BUFFER_SIZE"),
    "switch_buffer_map": net.get("SWITCH_BUFFER_MAP_FILE", ""),
    "ecn_maps": {k: net.get(k) for k in ("KMIN_MAP", "KMAX_MAP", "PMAX_MAP")},
    "trace_level": net.get("TRACE_LEVEL"),
    "queue_log_min_bytes": net.get("QUEUE_LOG_MIN_BYTES"),
    "ports_file": os.path.join(exp, "configs", "physical", "ports-${FABRIC}.json"),
    "chunking_mechanism": "preferred-dataset-splits",
    "qp_state_persists": net["TRANSPORT_CONNECTION_MODE"] == "PERSISTENT",
    "study_commit": os.popen("git -C ${STUDY_DIR} rev-parse HEAD").read().strip(),
    "astra_commit": os.popen("git -C ${ASTRA_DIR} rev-parse HEAD").read().strip(),
    "ns3_commit": os.popen("git -C ${NS3_DIR} rev-parse HEAD").read().strip(),
    "topology_sha256": sha(os.path.join(exp, "configs", "physical", "clos-${FABRIC}.txt")),
    "logical_sha256": sha(os.path.join(exp, "configs", "logical", "allreduce-1d.json")),
    "system_sha256": sha(os.path.join(exp, "configs", "granularity", "chunks-${K}.json")),
    "cca_sha256": sha(os.path.join(exp, "configs", "cca", "${CCA_FILE}")),
    "cca_config_hash": netconfig.semantic_hash("${RUN_DIR}/network.txt"),
    "workload_sha256": sha(os.path.join(exp, "workload", "allreduce.0.et")),
    "seed": "deterministic ECMP; SwitchNode::m_ecmpSeed = switch node id",
    "hostname": socket.gethostname(),
    "start": "${START}",
    "end": "${END}",
}
with open(out, "w") as handle:
    json.dump(meta, handle, indent=2)
    handle.write("\n")
PY

python3 "${SCRIPT_DIR}/analyze.py" --one "${RUN_DIR}"
