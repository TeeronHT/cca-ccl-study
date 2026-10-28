#!/bin/bash
# One E5 condition. Usage: run_one.sh <0.5x|1x|2x> <latency-ring|bandwidth-ring> <pfc|dcqcn|hpcc>
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: run_one.sh <0.5x|1x|2x> <latency-ring|bandwidth-ring> <pfc|dcqcn|hpcc>" >&2
  exit 1
fi

SCALE=$1
MAPPING=$2
CCA=$3

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
case "${CCA}" in
  pfc) CCA_FILE=pfc-only.txt ;;
  dcqcn) CCA_FILE=dcqcn.txt ;;
  hpcc) CCA_FILE=hpcc.txt ;;
  *) echo "unknown cca ${CCA}" >&2; exit 1 ;;
esac

RUN_ID="wan${VALUE}__${MAPPING}__${CCA}"
RUN_DIR="${EXP_DIR}/results/raw/${RUN_ID}"
mkdir -p "${RUN_DIR}"
python3 "${SCRIPT_DIR}/net.py" emit "${SCALE}" "${MAPPING}" "${RUN_DIR}/topology.txt"

python3 - "${EXP_DIR}/configs/cca/${CCA_FILE}" "${RUN_DIR}/network.txt" "${RUN_ID}" << 'PY'
import sys
src, dst, run_id = sys.argv[1:]
out = []
for line in open(src):
    line = line.rstrip("\n")
    key = line.split(" ", 1)[0] if line else ""
    if key == "TOPOLOGY_FILE":
        line = f"TOPOLOGY_FILE results/raw/{run_id}/topology.txt"
    elif key == "FLOW_FILE":
        line = "FLOW_FILE configs/flow.txt"
    elif key == "TRACE_FILE":
        line = "TRACE_FILE configs/physical/trace.txt"
    elif key == "TRACE_OUTPUT_FILE":
        line = f"TRACE_OUTPUT_FILE results/raw/{run_id}/mix.tr"
    elif key == "FCT_OUTPUT_FILE":
        line = f"FCT_OUTPUT_FILE results/raw/{run_id}/fct.txt"
    elif key == "PFC_OUTPUT_FILE":
        line = f"PFC_OUTPUT_FILE results/raw/{run_id}/pfc.txt"
    elif key == "QLEN_MON_FILE":
        line = f"QLEN_MON_FILE results/raw/{run_id}/qlen.txt"
    out.append(line)
open(dst, "w").write("\n".join(out) + "\n")
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
out = sys.argv[1]
exp = "${EXP_DIR}"
def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()
meta = {
    "run_id": "${RUN_ID}",
    "scale": "${SCALE}",
    "latency_scale": float("${VALUE}"),
    "mapping": "${MAPPING}",
    "cca": "${CCA}",
    "ports_file": "${RUN_DIR}/ports.json",
    "route": "hop-count shortest path; direct WAN edge is the unique one-hop border path; LINK_DOWN 0",
    "study_commit": os.popen("git -C ${STUDY_DIR} rev-parse HEAD").read().strip(),
    "astra_commit": os.popen("git -C ${ASTRA_DIR} rev-parse HEAD").read().strip(),
    "ns3_commit": os.popen("git -C ${NS3_DIR} rev-parse HEAD").read().strip(),
    "topology_sha256": sha("${RUN_DIR}/topology.txt"),
    "logical_sha256": sha(exp + "/configs/logical/${JSON}"),
    "system_sha256": sha(exp + "/configs/system/${JSON}"),
    "cca_sha256": sha(exp + "/configs/cca/${CCA_FILE}"),
    "workload_sha256": sha(exp + "/workload/allreduce.0.et"),
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
