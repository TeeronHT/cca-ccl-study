#!/bin/bash
# One E6 condition.
# Usage: run_one.sh <2to1|1to1> <ring|stragglar> <symmetric|one-late|two-late> <tau0|tau0.25|tau0.50|tau1.00> <pfc|dcqcn|hpcc>
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: run_one.sh <2to1|1to1> <ring|stragglar> <symmetric|one-late|two-late> <tau...> <pfc|dcqcn|hpcc>" >&2
  exit 1
fi

TOPO=$1
SCHEDULE=$2
PATTERN=$3
TAU=$4
CCA=$5

case "${TOPO}" in
  2to1) FABRIC=2to1 ;;
  1to1) FABRIC=1to1-control ;;
  *) echo "unknown topology ${TOPO}" >&2; exit 1 ;;
esac
case "${CCA}" in
  pfc) CCA_FILE=pfc-only.txt ;;
  dcqcn) CCA_FILE=dcqcn.txt ;;
  hpcc) CCA_FILE=hpcc.txt ;;
  *) echo "unknown cca ${CCA}" >&2; exit 1 ;;
esac

RUN_ID="${TOPO}__${PATTERN}__${TAU}__${SCHEDULE}__${CCA}"
if [[ "${PATTERN}" == "symmetric" ]]; then
  RUN_ID="${TOPO}__symmetric__${SCHEDULE}__${CCA}"
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
STUDY_DIR=$(cd "${EXP_DIR}/../../.." && pwd)
ASTRA_DIR="${STUDY_DIR}/astra-sim"
NS3_DIR="${ASTRA_DIR}/extern/network_backend/ns-3"
RUN_DIR="${EXP_DIR}/results/raw/${RUN_ID}"
WORK_DIR="${EXP_DIR}/workload/${SCHEDULE}__${PATTERN}__${TAU}"

if [[ ! -f "${WORK_DIR}/trace.0.et" ]]; then
  echo "missing ${WORK_DIR}; generate arrivals and workloads first" >&2
  exit 1
fi
rm -rf "${RUN_DIR}"
mkdir -p "${RUN_DIR}"

python3 - "${EXP_DIR}/configs/cca/${CCA_FILE}" "${RUN_DIR}/network.txt" "${FABRIC}" "${RUN_ID}" "${WORK_DIR}/case.json" << 'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[4] / "common"))
import netconfig
src, dst, fabric, run_id, case_path = sys.argv[1:]
case = json.load(open(case_path))
rel = f"results/raw/{run_id}"
overrides = {
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
    "SNAPSHOT_OUTPUT_FILE": f"{rel}/snapshots.txt",
}
# Network state at the final rank's arrival: one backend snapshot at tau
# (the applied COMP delay of the late rank). Symmetric cases have tau 0 and
# get no snapshot.
tau = int(case.get("tau_ns", 0))
if tau > 0:
    overrides["STATE_SNAPSHOT_TIMES"] = f"1 {tau}"
netconfig.rewrite(src, dst, overrides)
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
  --workload-configuration="${WORK_DIR}/trace" \
  --system-configuration="${EXP_DIR}/configs/system/system.json" \
  --network-configuration="${RUN_DIR}/network.txt" \
  --remote-memory-configuration="${EXP_DIR}/configs/memory.json" \
  --logical-topology-configuration="${EXP_DIR}/configs/logical/logical.json" \
  --comm-group-configuration=empty \
  --rendezvous-protocol=true \
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
def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()
case = json.load(open("${WORK_DIR}/case.json"))
prov_path = "${EXP_DIR}/schedules/provenance/provenance.json"
prov = json.load(open(prov_path)) if os.path.exists(prov_path) else {}
tref_path = "${EXP_DIR}/results/t_ref.txt"
tref = open(tref_path).read().split()[0] if os.path.exists(tref_path) else ""
net = netconfig.effective("${RUN_DIR}/network.txt")
meta = {
    "run_id": "${RUN_ID}",
    "topology": "${TOPO}",
    "schedule": "${SCHEDULE}",
    "cca": "${CCA}",
    "arrival_pattern": case["pattern"],
    "tau_ns": case["tau_ns"],
    "tau_over_tref": case["tau_over_tref"],
    "late_ranks": case["late_ranks"],
    "payload_sends": case["payload_sends"],
    "ports_file": "${EXP_DIR}/configs/physical/ports-${FABRIC}.json",
    "t_ref_ns": tref,
    "transport_connection_mode": net["TRANSPORT_CONNECTION_MODE"],
    "rendezvous": True,
    "cc_mode": net.get("CC_MODE"),
    "has_win": net.get("HAS_WIN"),
    "enable_pfc": net.get("ENABLE_PFC"),
    "pfc_threshold_validation": net.get("PFC_THRESHOLD_VALIDATION"),
    "buffer_size_mib": net.get("BUFFER_SIZE"),
    "switch_buffer_map": net.get("SWITCH_BUFFER_MAP_FILE", ""),
    "ecn_maps": {k: net.get(k) for k in ("KMIN_MAP", "KMAX_MAP", "PMAX_MAP")},
    "trace_level": net.get("TRACE_LEVEL"),
    "queue_log_min_bytes": net.get("QUEUE_LOG_MIN_BYTES"),
    "snapshot_times_ns": net.get("STATE_SNAPSHOT_TIMES", ""),
    "cca_config_hash": netconfig.semantic_hash("${RUN_DIR}/network.txt"),
    "route": "hop-count shortest path; rendezvous protocol on; LINK_DOWN 0",
    "study_commit": os.popen("git -C ${STUDY_DIR} rev-parse HEAD").read().strip(),
    "astra_commit": os.popen("git -C ${ASTRA_DIR} rev-parse HEAD").read().strip(),
    "ns3_commit": os.popen("git -C ${NS3_DIR} rev-parse HEAD").read().strip(),
    "topology_sha256": sha("${EXP_DIR}/configs/physical/clos-${FABRIC}.txt"),
    "ring_schedule_sha256": sha("${EXP_DIR}/schedules/ring/mechanism.txt"),
    "stragglar_schedule_sha256": sha("${EXP_DIR}/schedules/stragglar/schedule.json"),
    "workload_sha256": sha("${WORK_DIR}/trace.0.et"),
    "stragglar_source_commit": prov.get("source_commit", ""),
    "synthesis_command": prov.get("synthesis_command", ""),
    "cca_sha256": sha("${EXP_DIR}/configs/cca/${CCA_FILE}"),
    "arrival_sha256": sha("${WORK_DIR}/case.json"),
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
