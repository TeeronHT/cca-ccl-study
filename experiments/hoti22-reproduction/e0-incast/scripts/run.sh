#!/bin/bash
# Build ASTRA-sim/ns-3 if needed, then run the PFC-only and DCQCN incast cases.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
EXP_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
STUDY_DIR=$(cd "${EXP_DIR}/../../.." && pwd)
ASTRA_DIR="${STUDY_DIR}/astra-sim"
NS3_DIR="${ASTRA_DIR}/extern/network_backend/ns-3"
JOBS=$(sysctl -n hw.ncpu 2>/dev/null || nproc)

cd "${EXP_DIR}"

mkdir -p results
{
  echo "study $(git -C "${STUDY_DIR}" rev-parse HEAD)"
  echo "astra-sim $(git -C "${ASTRA_DIR}" rev-parse HEAD)"
  echo "ns-3 $(git -C "${NS3_DIR}" rev-parse HEAD)"
} | tee results/commits.txt

find_binary() {
  find "${NS3_DIR}/build/scratch" -maxdepth 1 -type f -name '*AstraSimNetwork*' -perm -111 2>/dev/null | head -n 1
}

# ns-3.42's ./ns3 launcher parses "cmake version X.Y.Z" with int().
# A suffix such as 4.2.0-rc3 makes that parser throw. Present a numeric version.
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
  # qbb-helper.cc includes ns3/mpi-interface.h unconditionally, so configure
  # must enable MPI even though this experiment runs as one process.
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

python3 "${SCRIPT_DIR}/generate_workload.py"

run_one() {
  local mode=$1
  local config=$2
  mkdir -p "results/${mode}"
  echo "running ${mode}"
  python3 -c '
import subprocess, sys
try:
    subprocess.run(sys.argv[1:], timeout=900, check=True)
except subprocess.TimeoutExpired:
    raise SystemExit("simulation exceeded 900s")
' "${BINARY}" \
    --workload-configuration="${EXP_DIR}/workload/incast" \
    --system-configuration="${EXP_DIR}/configs/system.json" \
    --network-configuration="${EXP_DIR}/${config}" \
    --remote-memory-configuration="${EXP_DIR}/configs/memory.json" \
    --logical-topology-configuration="${EXP_DIR}/configs/logical-topology.json" \
    --comm-group-configuration=empty \
    > "results/${mode}/stdout.log" 2>&1
}

run_one pfc-only configs/pfc-only.txt
run_one dcqcn configs/dcqcn.txt

python3 "${SCRIPT_DIR}/analyze.py" results/pfc-only results/dcqcn
echo "commits recorded in results/commits.txt"
