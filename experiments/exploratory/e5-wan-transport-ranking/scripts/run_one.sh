#!/bin/bash
# One E5 condition. Usage: run_one.sh <0.5x|1x|2x> <latency-ring|bandwidth-ring> <fixed-rate|dcqcn|hpcc>
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: run_one.sh <0.5x|1x|2x> <latency-ring|bandwidth-ring> <fixed-rate|dcqcn|hpcc>" >&2
  exit 1
fi

SCALE=$1
MAPPING=$2
TRANSPORT=$3

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
STUDY_DIR=$(cd "${EXP_DIR}/../../.." && pwd)
ASTRA_DIR="${STUDY_DIR}/astra-sim"
NS3_DIR="${ASTRA_DIR}/extern/network_backend/ns-3"

case "${SCALE}" in
  0.5x) VALUE=0.5 ;;
  1x) VALUE=1.0 ;;
  2x) VALUE=2.0 ;;
  *) echo "unknown scale ${SCALE}" >&2; exit 1 ;;
esac
case "${MAPPING}" in
  latency-ring) JSON=ring-latency-favoring.json ;;
  bandwidth-ring) JSON=ring-bandwidth-favoring.json ;;
  *) echo "unknown mapping ${MAPPING}" >&2; exit 1 ;;
esac
case "${TRANSPORT}" in
  fixed-rate|dcqcn|hpcc) CCA_FILE="${TRANSPORT}.txt" ;;
  *) echo "unknown transport ${TRANSPORT} (pfc is not an E5 transport)" >&2; exit 1 ;;
esac

RUN_ID="wan${VALUE}__${MAPPING}__${TRANSPORT}"
RUN_DIR="${EXP_DIR}/results/raw/${RUN_ID}"
mkdir -p "${RUN_DIR}"
python3 "${SCRIPT_DIR}/net.py" emit "${SCALE}" "${MAPPING}" "${RUN_DIR}/topology.txt"

python3 - "${EXP_DIR}/configs/cca/${CCA_FILE}" "${RUN_DIR}/network.txt" "${RUN_ID}" << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[4] / "common"))
import netconfig
src, dst, run_id = sys.argv[1:]
rel = f"results/raw/{run_id}"
netconfig.rewrite(src, dst, {
    "TOPOLOGY_FILE": f"{rel}/topology.txt",
    "FLOW_FILE": "configs/flow.txt",
    "TRACE_FILE": "configs/physical/trace.txt",
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
  --system-configuration="${EXP_DIR}/configs/system/${JSON}" \
  --network-configuration="${RUN_DIR}/network.txt" \
  --remote-memory-configuration="${EXP_DIR}/configs/memory.json" \
  --logical-topology-configuration="${EXP_DIR}/configs/logical/${JSON}" \
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
    "scale": "${SCALE}",
    "latency_scale": float("${VALUE}"),
    "mapping": "${MAPPING}",
    "cca": "${TRANSPORT}",
    "transport": "${TRANSPORT}",
    "transport_connection_mode": net["TRANSPORT_CONNECTION_MODE"],
    "rendezvous": False,
    "cc_mode": net.get("CC_MODE"),
    "has_win": net.get("HAS_WIN"),
    "enable_qcn": net.get("ENABLE_QCN"),
    "enable_pfc": net.get("ENABLE_PFC"),
    "pfc_threshold_validation": net.get("PFC_THRESHOLD_VALIDATION"),
    "buffer_size_mib": net.get("BUFFER_SIZE"),
    "switch_buffer_map": net.get("SWITCH_BUFFER_MAP_FILE", ""),
    "switch_buffer_map_sha256": sha(exp + "/configs/physical/buffers.txt"),
    "ecn_maps": {k: net.get(k) for k in ("KMIN_MAP", "KMAX_MAP", "PMAX_MAP")},
    "trace_level": net.get("TRACE_LEVEL"),
    "queue_log_min_bytes": net.get("QUEUE_LOG_MIN_BYTES"),
    "qlen_mon_interval_ns": net.get("QLEN_MON_INTERVAL", "100"),
    "ports_file": "${RUN_DIR}/ports.json",
    "route": "hop-count shortest path; direct WAN edge is the unique one-hop border path; LINK_DOWN 0",
    "study_commit": os.popen("git -C ${STUDY_DIR} rev-parse HEAD").read().strip(),
    "astra_commit": os.popen("git -C ${ASTRA_DIR} rev-parse HEAD").read().strip(),
    "ns3_commit": os.popen("git -C ${NS3_DIR} rev-parse HEAD").read().strip(),
    "topology_sha256": sha("${RUN_DIR}/topology.txt"),
    "logical_sha256": sha(exp + "/configs/logical/${JSON}"),
    "system_sha256": sha(exp + "/configs/system/${JSON}"),
    "cca_sha256": sha(exp + "/configs/cca/${CCA_FILE}"),
    "cca_config_hash": netconfig.semantic_hash("${RUN_DIR}/network.txt"),
    "workload_sha256": sha(exp + "/workload/allreduce.0.et"),
    "seed": "deterministic ECMP; SwitchNode::m_ecmpSeed = switch node id; single path per pair",
    "hostname": socket.gethostname(),
    "start": "${START}",
    "end": "${END}",
}
with open(out, "w") as handle:
    json.dump(meta, handle, indent=2)
    handle.write("\n")
PY

python3 "${SCRIPT_DIR}/analyze.py" --one "${RUN_DIR}"
