#!/usr/bin/env python3
"""Fail if an E5 invariant is broken. Does not start the simulator."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net

PATH_KEYS = {
    "TOPOLOGY_FILE",
    "FLOW_FILE",
    "TRACE_FILE",
    "TRACE_OUTPUT_FILE",
    "FCT_OUTPUT_FILE",
    "PFC_OUTPUT_FILE",
    "QLEN_MON_FILE",
}
MAP_KEYS = {"KMIN_MAP", "KMAX_MAP", "PMAX_MAP"}
ADDED_RATES = {
    50_000_000_000: (200, 800, 0.2),
    800_000_000_000: (800, 3200, 0.2),
}


def fail(message):
    print(f"INVALID: {message}", file=sys.stderr)
    sys.exit(1)


def e0_configs():
    return os.path.abspath(
        os.path.join(net.root(), "..", "..", "hoti22-reproduction", "e0-incast", "configs")
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


def parse_map(text):
    parts = text.split()
    count = int(parts[0])
    values = parts[1:]
    if len(values) != count * 2:
        fail(f"map count {count} does not match {len(values)} values")
    return {int(values[i]): values[i + 1] for i in range(0, len(values), 2)}


def check_physical():
    physical = os.path.join(net.root(), "configs", "physical")
    parsed = {}
    for scale_name in net.SCALES:
        path = os.path.join(physical, f"wan-latency-{scale_name}.txt")
        if not os.path.exists(path):
            fail(f"missing {path}; run generate_topologies.py")
        node_count, switch_count, link_count, switches, links = net.parse_topology(path)
        expected, _ports = net.build(scale_name, "latency-ring")
        actual = open(path).read().splitlines()
        if actual != expected:
            fail(f"{path} does not match the latency-ring builder")
        if node_count != 24 or switch_count != 8 or link_count != 26:
            fail(f"{path} header {node_count} {switch_count} {link_count}")
        if switches != list(range(16, 24)):
            fail(f"{path} switches {switches}")
        parsed[scale_name] = links
        homes = {site: [] for site in net.SITES}
        wan = {}
        for src, dst, rate, delay, error in links:
            if error != "0":
                fail(f"{path} link error {error}")
            if src < net.N_GPU:
                site = net.SITES[dst - net.TOR0]
                homes[site].append(src)
                if rate != "200Gbps" or delay != "500ns":
                    fail(f"GPU-ToR {src}-{dst} is {rate} {delay}")
            elif src < net.BORDER0:
                if rate != "800Gbps" or delay != "10000ns":
                    fail(f"ToR-border {src}-{dst} is {rate} {delay}")
            else:
                left = net.SITES[src - net.BORDER0]
                right = net.SITES[dst - net.BORDER0]
                wan[frozenset((left, right))] = (rate, delay)
        for site, members in homes.items():
            if sorted(members) != sorted(net.build(scale_name, "latency-ring")[1]["homes"][site]):
                fail(f"{scale_name} site {site} homes {members}")
        for pair, kind in net.WAN_CLASS.items():
            rate, delay = wan[pair]
            if rate != net.wan_rate_token(kind):
                fail(f"{pair} rate {rate}")
            if delay != f"{net.wan_delay_ns(kind, scale_name)}ns":
                fail(f"{pair} delay {delay}")
        if len(wan) != 6:
            fail(f"{scale_name} has {len(wan)} WAN edges")
    for kind, pair in (
        ("shared", frozenset(("A", "B"))),
        ("latency", frozenset(("B", "C"))),
        ("bandwidth", frozenset(("B", "D"))),
    ):
        delays = []
        for scale_name in ("0.5x", "1x", "2x"):
            delay = next(
                item[3]
                for item in parsed[scale_name]
                if item[0] >= net.BORDER0
                and frozenset(
                    (net.SITES[item[0] - net.BORDER0], net.SITES[item[1] - net.BORDER0])
                )
                == pair
            )
            delays.append(int(delay[:-2]))
        if delays != [net.wan_delay_ns(kind, name) for name in ("0.5x", "1x", "2x")]:
            fail(f"{kind} delays {delays} are not 0.5/1/2")
    local = [
        (src, dst, rate, delay)
        for src, dst, rate, delay, _error in parsed["1x"]
        if src < net.BORDER0
    ]
    for scale_name in net.SCALES:
        other = [
            (src, dst, rate, delay)
            for src, dst, rate, delay, _error in parsed[scale_name]
            if src < net.BORDER0
        ]
        if other != local:
            fail(f"{scale_name} changed a local link")
        rates = [rate for src, _dst, rate, _delay, _error in parsed[scale_name] if src >= net.BORDER0]
        if rates != [rate for src, _dst, rate, _delay, _error in parsed["1x"] if src >= net.BORDER0]:
            fail(f"{scale_name} changed a WAN capacity")
    print("physical: 16 GPUs, 4 per site, local links fixed, λ scales only WAN delay")


def check_rings_and_routes():
    orders = {}
    for mapping in net.MAPPINGS:
        orders[mapping] = net.ring_order(mapping)
        pairs = net.edge_pairs(mapping)
        used = {frozenset(pair) for pair in pairs}
        counts = {}
        for pair in pairs:
            counts[frozenset(pair)] = counts.get(frozenset(pair), 0) + 1
        if mapping == "latency-ring":
            expected = {
                frozenset(("A", "B")),
                frozenset(("B", "C")),
                frozenset(("C", "D")),
                frozenset(("D", "A")),
            }
        else:
            expected = {
                frozenset(("A", "B")),
                frozenset(("B", "D")),
                frozenset(("D", "C")),
                frozenset(("C", "A")),
            }
        if used != expected or any(count != net.STREAMS_PER_EDGE for count in counts.values()):
            fail(f"{mapping} edges {counts}")
        if len(pairs) != net.N_GPU:
            fail(f"{mapping} does not have 16 ring edges")
    if orders["latency-ring"] == orders["bandwidth-ring"]:
        fail("the two ring orders are identical")
    graph_cache = {}
    for mapping in net.MAPPINGS:
        _body, ports = net.build("1x", mapping)
        links = []
        for line in _body[2:]:
            src, dst, rate, delay, error = line.split()
            links.append((int(src), int(dst), rate, delay, error))
        graph = net.neighbors_from_links(links)
        graph_cache[mapping] = graph
        for src in range(net.N_GPU):
            for dst in range(net.N_GPU):
                if src == dst:
                    continue
                used = net.wan_edges_used(graph, src, dst)
                src_site = net.site_of_node(mapping, src)
                dst_site = net.site_of_node(mapping, dst)
                if src_site == dst_site:
                    if used:
                        fail(f"{mapping} {src}->{dst} left the site via {used}")
                elif used != {frozenset((src_site, dst_site))}:
                    fail(f"{mapping} {src}->{dst} uses {used}, not the direct WAN edge")
        for site in net.SITES:
            if len(ports["homes"][site]) != 4:
                fail(f"{mapping} site {site} has {ports['homes'][site]}")
    print("rings: intended WAN pairs only; every cross-site route is the direct one-hop edge")


def check_workload_and_algorithm():
    base = net.root()
    for name in net.MAPPING_FILE.values():
        path = os.path.join(base, "configs", "system", name)
        document = json.load(open(path))
        if document.get("all-reduce-implementation") != ["ring"]:
            fail(f"{name} is not ring")
        if document.get("preferred-dataset-splits") != 1:
            fail(f"{name} splits the 64 MiB tensor")
        if document.get("endpoint-delay") != 0:
            fail(f"{name} adds endpoint delay")
        if "multicast" in json.dumps(document).lower():
            fail(f"{name} mentions multicast")
    system_blobs = [
        open(os.path.join(base, "configs", "system", name)).read()
        for name in net.MAPPING_FILE.values()
    ]
    if system_blobs[0] != system_blobs[1]:
        fail("the two system files do not describe the same ring algorithm")
    logical = [
        json.load(open(os.path.join(base, "configs", "logical", net.MAPPING_FILE[mapping])))
        for mapping in net.MAPPINGS
    ]
    if any(item.get("logical-dims") != ["16"] for item in logical):
        fail("logical dimension is not [16]")
    paths = [os.path.join(base, "workload", f"allreduce.{rank}.et") for rank in range(net.N_GPU)]
    if any(not os.path.exists(path) for path in paths):
        fail("workload traces missing; run generate_workloads.py")
    blobs = [open(path, "rb").read() for path in paths]
    if any(blob != blobs[0] for blob in blobs):
        fail("rank traces differ")
    size = net.MESSAGE_BYTES
    encoded = bytearray()
    while True:
        byte = size & 0x7F
        size >>= 7
        encoded.append(byte | (0x80 if size else 0))
        if not size:
            break
    if bytes(encoded) not in blobs[0]:
        fail("trace is not a 64 MiB AllReduce")
    flow = open(os.path.join(base, "configs", "flow.txt")).read().strip()
    if flow != "0":
        fail("flow.txt is not empty")
    print("workload: one 64 MiB ring AllReduce, no background flows, no endpoint gap")


def check_cca():
    e0 = e0_configs()
    for token, filename in net.CCA_FILES.items():
        ours = parse_config(os.path.join(net.root(), "configs", "cca", filename))
        canon = parse_config(os.path.join(e0, filename))
        for key, value in canon.items():
            if key in PATH_KEYS or key in MAP_KEYS:
                continue
            if ours.get(key) != value:
                fail(f"{filename} {key} differs from E0")
        if ours["LINK_DOWN"] != "0 0 0":
            fail(f"{filename} enables link teardown")
        if ours["ERROR_RATE_PER_LINK"] != "0.0000":
            fail(f"{filename} injects loss")
        if ours["BUFFER_SIZE"] != "32":
            fail(f"{filename} buffer is not the validated 32 MiB")
        for key in MAP_KEYS:
            original = parse_map(canon[key])
            updated = parse_map(ours[key])
            for rate, value in original.items():
                if updated.get(rate) != value:
                    fail(f"{filename} {key} changed existing rate {rate}")
            for rate, (kmin, kmax, pmax) in ADDED_RATES.items():
                expected = {"KMIN_MAP": str(kmin), "KMAX_MAP": str(kmax), "PMAX_MAP": str(pmax)}[key]
                if updated.get(rate) != expected:
                    fail(f"{filename} {key} missing copied threshold for {rate}")
        if set(parse_map(ours["KMIN_MAP"])) - set(parse_map(canon["KMIN_MAP"])) != set(ADDED_RATES):
            fail(f"{filename} ECN map has unexpected rates")
    pfc = parse_config(os.path.join(net.root(), "configs", "cca", "pfc-only.txt"))
    dcqcn = parse_config(os.path.join(net.root(), "configs", "cca", "dcqcn.txt"))
    hpcc = parse_config(os.path.join(net.root(), "configs", "cca", "hpcc.txt"))
    if pfc["CC_MODE"] != "0" or pfc["HAS_WIN"] != "0" or pfc["ENABLE_QCN"] != "0":
        fail("PFC-only mode changed")
    if dcqcn["CC_MODE"] != "1" or dcqcn["HAS_WIN"] != "0":
        fail("DCQCN HAS_WIN/mode changed")
    if hpcc["CC_MODE"] != "3" or hpcc["HAS_WIN"] != "1":
        fail("HPCC must stay HAS_WIN 1")
    print("cca: E0 settings preserved; 50G and 800G reuse existing ECN thresholds; HPCC HAS_WIN 1")


def check_ids_and_model():
    ids = [
        f"wan{net.scale_value(scale)}__{mapping}__{cca}"
        for scale in net.SCALES
        for mapping in net.MAPPINGS
        for cca in net.CCA_FILES
    ]
    if len(ids) != 18 or len(set(ids)) != 18:
        fail("run ids are not 18 unique values")
    rows = net.predictions()
    ok, reason = net.crossover_holds(rows)
    if not ok:
        fail(reason)
    print(f"run ids: 18 unique; {reason}")


def main():
    check_physical()
    check_rings_and_routes()
    check_workload_and_algorithm()
    check_cca()
    check_ids_and_model()
    print("E5 invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
