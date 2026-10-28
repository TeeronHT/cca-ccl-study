#!/bin/bash
# One E4 condition. Usage: run_one.sh 1to1 k4 dcqcn
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

mkdir -p "${RUN_DIR}"
python3 - "${EXP_DIR}/configs/cca/${CCA_FILE}" "${RUN_DIR}/network.txt" "${FABRIC}" "${RUN_ID}" << 'PY'
import sys
src, dst, fabric, run_id = sys.argv[1:]
out = []
for line in open(src):
    line = line.rstrip("\n")
    key = line.split(" ", 1)[0] if line else ""
    if key == "TOPOLOGY_FILE":
        line = f"TOPOLOGY_FILE configs/physical/clos-{fabric}.txt"
    elif key == "FLOW_FILE":
        line = "FLOW_FILE configs/flow.txt"
    elif key == "TRACE_FILE":
        line = f"TRACE_FILE configs/physical/trace-{fabric}.txt"
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
out = sys.argv[1]
exp = "${EXP_DIR}"
def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()
meta = {
    "run_id": "${RUN_ID}",
    "topology": "${FABRIC}",
    "oversubscription": "${FABRIC}",
    "spine_count": {"1to1": 4, "2to1": 2}["${FABRIC}"],
    "chunk_count": int("${K}"),
    "cca": "${CCA}",
    "ports_file": os.path.join(exp, "configs", "physical", "ports-${FABRIC}.json"),
    "chunking_mechanism": "preferred-dataset-splits",
    "qp_state_persists": False,
    "study_commit": os.popen("git -C ${STUDY_DIR} rev-parse HEAD").read().strip(),
    "astra_commit": os.popen("git -C ${ASTRA_DIR} rev-parse HEAD").read().strip(),
    "ns3_commit": os.popen("git -C ${NS3_DIR} rev-parse HEAD").read().strip(),
    "topology_sha256": sha(os.path.join(exp, "configs", "physical", "clos-${FABRIC}.txt")),
    "logical_sha256": sha(os.path.join(exp, "configs", "logical", "allreduce-1d.json")),
    "system_sha256": sha(os.path.join(exp, "configs", "granularity", "chunks-${K}.json")),
    "cca_sha256": sha(os.path.join(exp, "configs", "cca", "${CCA_FILE}")),
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
