#!/bin/bash
# 128-GPU Clos AllReduce: 1D and 2D, five CCAs from Experiment 0.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
E0_CONFIGS=$(cd "${EXP_DIR}/../e0-incast/configs" && pwd)
STUDY_DIR=$(cd "${EXP_DIR}/../../.." && pwd)
ASTRA_DIR="${STUDY_DIR}/astra-sim"
NS3_DIR="${ASTRA_DIR}/extern/network_backend/ns-3"
JOBS=$(sysctl -n hw.ncpu 2>/dev/null || nproc)

cd "${EXP_DIR}"
python3 "${SCRIPT_DIR}/generate_topology.py"
python3 "${SCRIPT_DIR}/generate_workload.py"

mkdir -p results
{
  echo "study $(git -C "${STUDY_DIR}" rev-parse HEAD)"
  echo "astra-sim $(git -C "${ASTRA_DIR}" rev-parse HEAD)"
  echo "ns-3 $(git -C "${NS3_DIR}" rev-parse HEAD)"
} | tee results/commits.txt

find_binary() {
  find "${NS3_DIR}/build/scratch" -maxdepth 1 -type f -name '*AstraSimNetwork*' -perm -111 2>/dev/null | head -n 1
}

if cmake --version | head -n 1 | grep -q -- '-'; then
  REAL_CMAKE=$(command -v cmake)
  mkdir -p "${EXP_DIR}/.bin"
  cat > "${EXP_DIR}/.bin/cmake" << EOF
#!/bin/bash
if [[ "\${1:-}" == "--version" ]]; then
  "${REAL_CMAKE}" --version | sed -E 's/-[A-Za-z0-9.]+//'
else
  exec "${REAL_CMAKE}" "\$@"
fi
EOF
  chmod +x "${EXP_DIR}/.bin/cmake"
  export PATH="${EXP_DIR}/.bin:${PATH}"
fi

if [[ "${1:-}" == "--rebuild" ]] || [[ -z "$(find_binary)" ]]; then
  PROTO_DIR="${ASTRA_DIR}/extern/graph_frontend/chakra/schema/protobuf"
  protoc et_def.proto --proto_path "${PROTO_DIR}" --cpp_out "${PROTO_DIR}"
  cd "${NS3_DIR}"
  if ! command -v mpicc >/dev/null 2>&1; then
    echo "mpicc not found. Install OpenMPI, then rerun." >&2
    exit 1
  fi
  ./ns3 configure --enable-mpi --disable-examples --disable-tests
  ./ns3 build AstraSimNetwork -j "${JOBS}"
  cd "${EXP_DIR}"
fi

BINARY=$(find_binary)
if [[ -z "${BINARY}" ]]; then
  echo "AstraSimNetwork binary not found under ${NS3_DIR}/build/scratch" >&2
  exit 1
fi
echo "binary ${BINARY}"

write_network_config() {
  local layout=$1
  local cca=$2
  local out=$3
  python3 - "$E0_CONFIGS/${cca}.txt" "$out" "$layout" "$cca" << 'PY'
import sys
src, dst, layout, cca = sys.argv[1:]
text = open(src).read().splitlines()
rewritten = []
for line in text:
    key = line.split(" ", 1)[0] if line else ""
    if key == "TOPOLOGY_FILE":
        line = "TOPOLOGY_FILE configs/topology.txt"
    elif key == "FLOW_FILE":
        line = "FLOW_FILE configs/flow.txt"
    elif key == "TRACE_FILE":
        line = "TRACE_FILE configs/trace.txt"
    elif key == "TRACE_OUTPUT_FILE":
        line = f"TRACE_OUTPUT_FILE results/{layout}/{cca}/mix.tr"
    elif key == "FCT_OUTPUT_FILE":
        line = f"FCT_OUTPUT_FILE results/{layout}/{cca}/fct.txt"
    elif key == "PFC_OUTPUT_FILE":
        line = f"PFC_OUTPUT_FILE results/{layout}/{cca}/pfc.txt"
    elif key == "QLEN_MON_FILE":
        line = f"QLEN_MON_FILE results/{layout}/{cca}/qlen.txt"
    rewritten.append(line)
open(dst, "w").write("\n".join(rewritten) + "\n")
PY
}

run_one() {
  local layout=$1
  local cca=$2
  local system=$3
  local logical=$4
  mkdir -p "results/${layout}/${cca}"
  write_network_config "${layout}" "${cca}" "results/${layout}/${cca}/network.txt"
  echo "running ${layout}/${cca}"
  echo "  log: results/${layout}/${cca}/stdout.log"
  echo "  queue samples: results/${layout}/${cca}/qlen.txt"
  "${BINARY}" \
    --workload-configuration="${EXP_DIR}/workload/allreduce" \
    --system-configuration="${EXP_DIR}/${system}" \
    --network-configuration="${EXP_DIR}/results/${layout}/${cca}/network.txt" \
    --remote-memory-configuration="${EXP_DIR}/configs/memory.json" \
    --logical-topology-configuration="${EXP_DIR}/${logical}" \
    --comm-group-configuration=empty \
    > "results/${layout}/${cca}/stdout.log" 2>&1
}

ARGS=()
for layout in 1d 2d; do
  if [[ "${layout}" == "1d" ]]; then
    system=configs/system-1d.json
    logical=configs/logical-1d.json
  else
    system=configs/system-2d.json
    logical=configs/logical-2d.json
  fi
  for cca in pfc-only dcqcn dctcp timely hpcc; do
    run_one "${layout}" "${cca}" "${system}" "${logical}"
    ARGS+=("${layout}:${cca}:results/${layout}/${cca}")
  done
done

python3 "${SCRIPT_DIR}/analyze.py" "${ARGS[@]}"
echo "summary: results/summary.csv"
