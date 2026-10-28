#!/usr/bin/env python3
"""Fail if an E4 invariant is broken. Does not start the simulator."""

import json
import os
import sys

MESSAGE_BYTES = 64 * 1024 * 1024
N_GPU = 16
RACKS = [(0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11), (12, 13, 14, 15)]
FABRIC_SPINES = {"1to1": 4, "2to1": 2}
CHUNKS = {1: MESSAGE_BYTES, 4: MESSAGE_BYTES // 4, 16: MESSAGE_BYTES // 16, 64: MESSAGE_BYTES // 64}
CCA_FILES = {"pfc": "pfc-only.txt", "dcqcn": "dcqcn.txt", "hpcc": "hpcc.txt"}
PATH_KEYS = {
    "TOPOLOGY_FILE",
    "FLOW_FILE",
    "TRACE_FILE",
    "TRACE_OUTPUT_FILE",
    "FCT_OUTPUT_FILE",
    "PFC_OUTPUT_FILE",
    "QLEN_MON_FILE",
}
TRANSPORT_KEYS = [
    "USE_DYNAMIC_PFC_THRESHOLD",
    "PACKET_PAYLOAD_SIZE",
    "ALPHA_RESUME_INTERVAL",
    "RATE_DECREASE_INTERVAL",
    "CLAMP_TARGET_RATE",
    "RP_TIMER",
    "EWMA_GAIN",
    "FAST_RECOVERY_TIMES",
    "RATE_AI",
    "RATE_HAI",
    "MIN_RATE",
    "ERROR_RATE_PER_LINK",
    "L2_ACK_INTERVAL",
    "U_TARGET",
    "BUFFER_SIZE",
    "KMIN_MAP",
    "KMAX_MAP",
    "PMAX_MAP",
]


def fail(message):
    print(f"INVALID: {message}", file=sys.stderr)
    sys.exit(1)


def root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def e0_configs():
    return os.path.abspath(
        os.path.join(root(), "..", "..", "hoti22-reproduction", "e0-incast", "configs")
    )


def parse_config(path):
    values = {}
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            key, rest = line.split(" ", 1)
            values[key] = rest
    return values


def parse_topology(path):
    lines = open(path).read().splitlines()
    node_count, switch_count, link_count = map(int, lines[0].split())
    switches = list(map(int, lines[1].split()))
    links = []
    for line in lines[2:]:
        src, dst, rate, delay, error = line.split()
        links.append((int(src), int(dst), rate, delay, error))
    if len(switches) != switch_count or len(links) != link_count:
        fail(f"{path}: header counts do not match the body")
    return switches, links


def ring_groups(dims):
    offset = 1
    n = 1
    for size in dims:
        n *= size
    groups = []
    for size in dims:
        seen = []
        for node in range(n):
            index = (node % (offset * size)) // offset
            base = node - index * offset
            members = tuple(base + i * offset for i in range(size))
            if members not in seen:
                seen.append(members)
        groups.append(seen)
        offset *= size
    return groups


def check_topologies(physical):
    for name, n_spine in FABRIC_SPINES.items():
        path = os.path.join(physical, f"clos-{name}.txt")
        if not os.path.exists(path):
            fail(f"missing {path}; run generate_topologies.py")
        switches, links = parse_topology(path)
        tors = list(range(N_GPU, N_GPU + 4))
        spines = [switch for switch in switches if switch >= N_GPU + 4]
        if spines != list(range(N_GPU + 4, N_GPU + 4 + n_spine)):
            fail(f"{name}: spines {spines}")
        if any(rate != "200Gbps" or delay != "500ns" or error != "0" for _, _, rate, delay, error in links):
            fail(f"{name}: a link is not 200Gbps 500ns 0")
        down = {tor: [] for tor in tors}
        up = {tor: [] for tor in tors}
        for src, dst, *_ in links:
            if src < N_GPU:
                down[dst].append(src)
            elif dst in spines:
                up[src].append(dst)
            else:
                fail(f"{name}: unexpected link {src}-{dst}")
        for rack, members in enumerate(RACKS):
            tor = N_GPU + rack
            if sorted(down[tor]) != list(members):
                fail(f"{name}: ToR {tor} endpoints {sorted(down[tor])}")
            if len(up[tor]) != n_spine or sorted(up[tor]) != spines:
                fail(f"{name}: ToR {tor} has uplinks {sorted(up[tor])}")
    print("topologies: 16 GPUs, 1:1 has 4 uplinks, 2:1 has 2, all links 200Gbps 500ns")


# Backend-extension keys E4 requires, and the values it requires. None of
# them is a congestion-control parameter.
REQUIRED_EXTENSIONS = {
    "TRANSPORT_CONNECTION_MODE": "PERSISTENT",
    "PFC_THRESHOLD_VALIDATION": "STRICT",
    "ENABLE_CCA_TRACE": "2",
    "ENABLE_FLOW_METADATA_TRACE": "1",
    "ENABLE_PATH_TRACE": "1",
    "QUEUE_LOG_MIN_BYTES": "0",
}
OPTIONAL_EXTENSIONS = {"TRACE_LEVEL", "COUNTERS_OUTPUT_FILE", "LINK_REPORT_FILE"}


def check_cca(base):
    e0 = e0_configs()
    parsed = {}
    for token, filename in CCA_FILES.items():
        ours = parse_config(os.path.join(base, "configs", "cca", filename))
        canon = parse_config(os.path.join(e0, filename))
        for key, value in ours.items():
            if key in PATH_KEYS or key in REQUIRED_EXTENSIONS or key in OPTIONAL_EXTENSIONS:
                continue
            if canon.get(key) != value:
                fail(f"{filename} {key}={value} differs from E0 {canon.get(key)}")
        for key in TRANSPORT_KEYS:
            if ours.get(key) != canon.get(key):
                fail(f"{filename} missing transport key {key}")
        for key, value in canon.items():
            if key in PATH_KEYS:
                continue
            if ours.get(key) != value:
                fail(f"{filename} lost E0 key {key}")
        for key, value in REQUIRED_EXTENSIONS.items():
            if ours.get(key) != value:
                fail(f"{filename} {key}={ours.get(key)}; E4 requires {value}")
        parsed[token] = ours
    if parsed["hpcc"]["HAS_WIN"] != "1" or parsed["dcqcn"]["HAS_WIN"] != "0":
        fail("HAS_WIN does not match the validated E0 settings")
    if parsed["pfc"]["HAS_WIN"] != "0" or parsed["pfc"]["CC_MODE"] != "0":
        fail("PFC-only config changed")
    if any(parsed[token]["BUFFER_SIZE"] != "32" for token in parsed):
        fail("buffer is not 32 MiB")
    print("cca: E0 CCA keys unchanged; HPCC HAS_WIN 1; DCQCN HAS_WIN 0; "
          "TRANSPORT_CONNECTION_MODE PERSISTENT; STRICT headroom; traces on")


def check_workload_and_state(base):
    paths = [os.path.join(base, "workload", f"allreduce.{rank}.et") for rank in range(N_GPU)]
    if any(not os.path.exists(path) for path in paths):
        fail("workload traces missing; run generate_workloads.py")
    blobs = [open(path, "rb").read() for path in paths]
    if any(blob != blobs[0] for blob in blobs):
        fail("rank traces differ")
    size = MESSAGE_BYTES
    encoded = bytearray()
    while True:
        byte = size & 0x7F
        size >>= 7
        encoded.append(byte | (0x80 if size else 0))
        if not size:
            break
    if bytes(encoded) not in blobs[0]:
        fail("trace is not a 64 MiB AllReduce")
    note = open(os.path.join(base, "configs", "chunking.txt")).read()
    if "qp_state_persists=true" not in note or "preferred-dataset-splits" not in note:
        fail("chunking.txt does not record the persistent-QP mechanism")
    print("workload: 16 identical 64 MiB AllReduces; one persistent QP per rank pair carries every chunk")


def check_ids():
    ids = [
        f"{fabric}__k{count}__{cca}"
        for fabric in FABRIC_SPINES
        for count in CHUNKS
        for cca in CCA_FILES
    ]
    if len(ids) != 24 or len(set(ids)) != 24:
        fail("run ids are not 24 unique values")
    print("run ids: 24 unique")


def main():
    base = root()
    check_topologies(os.path.join(base, "configs", "physical"))
    check_system(base)
    check_cca(base)
    check_workload_and_state(base)
    check_ids()
    print("E4 invariants hold")
    return 0


def check_system(base):
    logical = json.load(open(os.path.join(base, "configs", "logical", "allreduce-1d.json")))
    if logical.get("logical-dims") != ["16"]:
        fail(f"logical dims {logical.get('logical-dims')}")
    if ring_groups([16]) != [[tuple(range(16))]]:
        fail("1D ring is not 0..15")
    system = json.load(open(os.path.join(base, "configs", "system", "allreduce-1d.json")))
    if system.get("all-reduce-implementation") != ["ring"]:
        fail("not a 1D ring")
    if system.get("active-chunks-per-dimension") != 1 or system.get("scheduling-policy") != "FIFO":
        fail("chunks are not serialized FIFO")
    frozen = None
    for count, chunk_bytes in CHUNKS.items():
        doc = json.load(open(os.path.join(base, "configs", "granularity", f"chunks-{count}.json")))
        if doc.get("endpoint-delay") != 0:
            fail(f"K={count} has a nonzero endpoint delay")
        if doc.get("preferred-dataset-splits") != count:
            fail(f"K={count} file has splits {doc.get('preferred-dataset-splits')}")
        if count * chunk_bytes != MESSAGE_BYTES:
            fail(f"K={count} does not partition 64 MiB")
        comparable = dict(doc)
        comparable["preferred-dataset-splits"] = 1
        if frozen is None:
            frozen = comparable
        elif comparable != frozen:
            fail("a granularity file changes more than the split count")
    print("collective: 1D ring 0→1→…→15; granularity changes only preferred-dataset-splits")


if __name__ == "__main__":
    sys.exit(main())
