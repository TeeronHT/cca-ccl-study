#!/usr/bin/env python3
"""Fail if an E3 invariant is broken. Does not start the simulator."""

import os
import sys

MESSAGE_BYTES = 64 * 1024 * 1024
N_GPU = 16
RACKS = [(0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11), (12, 13, 14, 15)]
FABRIC_SPINES = {"1to1": 4, "2to1": 2, "4to1": 1}
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
    "L2_CHUNK_SIZE",
    "L2_ACK_INTERVAL",
    "L2_BACK_TO_ZERO",
    "U_TARGET",
    "MI_THRESH",
    "FAST_REACT",
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
    return node_count, switches, links


def ring_groups(dims):
    """Same placement rule as GeneralComplexTopology + RingTopology."""
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
    endpoint_links = {}
    for name, n_spine in FABRIC_SPINES.items():
        path = os.path.join(physical, f"clos-{name}.txt")
        if not os.path.exists(path):
            fail(f"missing {path}; run generate_topologies.py")
        _, switches, links = parse_topology(path)
        gpus = [node for node in range(N_GPU)]
        tors = list(range(N_GPU, N_GPU + 4))
        spines = [switch for switch in switches if switch not in tors]
        if spines != list(range(N_GPU + 4, N_GPU + 4 + n_spine)):
            fail(f"{name}: expected {n_spine} spines, found {spines}")
        if any(gpu in switches for gpu in gpus):
            fail(f"{name}: a GPU id is listed as a switch")
        rates = {rate for _, _, rate, _, _ in links}
        delays = {delay for _, _, _, delay, _ in links}
        errors = {error for *_, error in links}
        if rates != {"200Gbps"} or delays != {"500ns"} or errors != {"0"}:
            fail(f"{name}: a link is not 200Gbps 500ns 0")
        down = {tor: [] for tor in tors}
        up = {tor: [] for tor in tors}
        for src, dst, *_ in links:
            if src < N_GPU and dst in down:
                down[dst].append(src)
            elif dst < N_GPU and src in down:
                down[src].append(dst)
            elif src in up and dst in spines:
                up[src].append(dst)
            elif dst in up and src in spines:
                up[dst].append(src)
            else:
                fail(f"{name}: unexpected link {src}-{dst}")
        for rack, members in enumerate(RACKS):
            tor = N_GPU + rack
            if sorted(down[tor]) != list(members):
                fail(f"{name}: ToR {tor} downlinks {sorted(down[tor])} != {list(members)}")
            if sorted(up[tor]) != spines:
                fail(f"{name}: ToR {tor} uplinks {sorted(up[tor])} != {spines}")
        endpoint_links[name] = (tuple(sorted(rates)), tuple(sorted(delays)))
    if len(set(endpoint_links.values())) != 1:
        fail("fabrics do not share endpoint and fabric link settings")
    print("topologies: 16 GPUs, racks 0-3/4-7/8-11/12-15, uplink counts 4/2/1")


def check_logical():
    one = ring_groups([16])
    if one != [[tuple(range(16))]]:
        fail(f"1D ring is {one[0]}, expected 0..15 in order")
    two = ring_groups([4, 4])
    if two[0] != [tuple(group) for group in RACKS]:
        fail(f"2D local groups are {two[0]}")
    inter = [(0, 4, 8, 12), (1, 5, 9, 13), (2, 6, 10, 14), (3, 7, 11, 15)]
    if two[1] != [tuple(group) for group in inter]:
        fail(f"2D inter-rack groups are {two[1]}")
    print("logical: 1D ring 0→1→…→15; 2D local rings are the four racks")


def check_cca(configs):
    e0 = e0_configs()
    parsed = {}
    for token, filename in CCA_FILES.items():
        path = os.path.join(configs, "cca", filename)
        e0_path = os.path.join(e0, filename)
        ours = parse_config(path)
        canon = parse_config(e0_path)
        for key in TRANSPORT_KEYS:
            if ours.get(key) != canon.get(key):
                fail(f"{filename} {key}={ours.get(key)} differs from E0 {canon.get(key)}")
        for key, value in ours.items():
            if key in PATH_KEYS:
                continue
            if canon.get(key) != value:
                fail(f"{filename} {key}={value} differs from E0 {canon.get(key)}")
        parsed[token] = ours
    if parsed["pfc"]["CC_MODE"] != "0" or parsed["pfc"]["ENABLE_QCN"] != "0":
        fail("PFC-only is not CC_MODE 0 / ENABLE_QCN 0")
    if parsed["pfc"]["HAS_WIN"] != "0" or parsed["dcqcn"]["HAS_WIN"] != "0":
        fail("PFC-only or DCQCN HAS_WIN is not 0")
    if parsed["hpcc"]["HAS_WIN"] != "1" or parsed["hpcc"]["CC_MODE"] != "3":
        fail("HPCC is not CC_MODE 3 / HAS_WIN 1")
    if parsed["dcqcn"]["CC_MODE"] != "1" or parsed["dcqcn"]["ENABLE_QCN"] != "1":
        fail("DCQCN is not CC_MODE 1 / ENABLE_QCN 1")
    if parsed["hpcc"]["ENABLE_QCN"] != "1":
        fail("HPCC ENABLE_QCN is not 1")
    buffers = {parsed[token]["BUFFER_SIZE"] for token in parsed}
    if buffers != {"32"}:
        fail(f"buffer sizes {buffers}")
    print("cca: E0 transport keys unchanged; HPCC HAS_WIN 1; others HAS_WIN 0")


def check_workload(directory):
    paths = [os.path.join(directory, f"allreduce.{rank}.et") for rank in range(N_GPU)]
    missing = [path for path in paths if not os.path.exists(path)]
    if missing:
        fail("workload traces missing; run generate_workload.py")
    blobs = [open(path, "rb").read() for path in paths]
    if any(blob != blobs[0] for blob in blobs):
        fail("AllReduce traces are not identical across ranks")
    size = MESSAGE_BYTES
    encoded = bytearray()
    while True:
        byte = size & 0x7F
        size >>= 7
        encoded.append(byte | (0x80 if size else 0))
        if not size:
            break
    if bytes(encoded) not in blobs[0]:
        fail("workload does not contain the 64 MiB size")
    print(f"workload: 16 identical traces, tensor {MESSAGE_BYTES} bytes")


def check_run_ids():
    ids = []
    for fabric in FABRIC_SPINES:
        for collective in ("1d", "2d"):
            for cca in CCA_FILES:
                ids.append(f"{fabric}__{collective}__{cca}")
    if len(ids) != len(set(ids)) or len(ids) != 18:
        fail(f"run ids are not 18 unique values: {ids}")
    print("run ids: 18 unique")


def main():
    base = root()
    check_topologies(os.path.join(base, "configs", "physical"))
    check_logical()
    check_cca(os.path.join(base, "configs"))
    check_workload(os.path.join(base, "workload"))
    check_run_ids()
    print("E3 invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
