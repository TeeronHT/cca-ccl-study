#!/usr/bin/env python3
"""Shared topology, placement, routing, buffers, ECN and static-cost definitions for E5.

ASTRA-sim builds a 1D ring as node i → i+1. The logical JSON cannot supply a
custom neighbor list, and the custom RingTopology(vector<int>) constructor is
not reachable from GeneralComplexTopology. Each mapping is realized by homing
node i to the site of ring_order[i]. The collective algorithm stays ring.
"""

import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(Path(HERE).parents[2] / "common"))
import netconfig  # noqa: E402

N_GPU = 16
GPUS_PER_SITE = 4
SITES = ("A", "B", "C", "D")
MESSAGE_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = MESSAGE_BYTES // N_GPU
RING_STEPS = 2 * (N_GPU - 1)
STREAMS_PER_EDGE = 4
TIE_BAND = 0.01

SITE_INDEX = {site: index for index, site in enumerate(SITES)}
TOR0 = N_GPU
BORDER0 = N_GPU + len(SITES)

# Base one-way delays at λ = 1, in nanoseconds. 1.842 ms is the section-8 value.
WAN_BASE_NS = {
    "shared": 250_000,
    "latency": 500_000,
    "bandwidth": 1_842_000,
}
WAN_BPS = {
    "shared": 100_000_000_000,
    "latency": 50_000_000_000,
    "bandwidth": 100_000_000_000,
}
LOCAL_GPU_TOR = ("200Gbps", 500, 200_000_000_000)
LOCAL_TOR_BORDER = ("800Gbps", 10_000, 800_000_000_000)
SCALES = {"0.5x": 1, "1x": 2, "2x": 4}

# Undirected site pairs.
WAN_CLASS = {
    frozenset(("A", "B")): "shared",
    frozenset(("C", "D")): "shared",
    frozenset(("B", "C")): "latency",
    frozenset(("D", "A")): "latency",
    frozenset(("B", "D")): "bandwidth",
    frozenset(("C", "A")): "bandwidth",
}
WAN_ORDER = (
    ("A", "B"),
    ("A", "C"),
    ("A", "D"),
    ("B", "C"),
    ("B", "D"),
    ("C", "D"),
)

MAPPINGS = ("latency-ring", "bandwidth-ring")
MAPPING_FILE = {
    "latency-ring": "ring-latency-favoring.json",
    "bandwidth-ring": "ring-bandwidth-favoring.json",
}
# Primary transport modes. fixed-rate is CC_MODE 0 with PFC off: senders stay
# at line rate, nothing adapts. PFC-only is not part of E5 (see README).
TRANSPORTS = ("fixed-rate", "dcqcn", "hpcc")
TRANSPORT_FILE = {"fixed-rate": "fixed-rate.txt", "dcqcn": "dcqcn.txt", "hpcc": "hpcc.txt"}
TRANSPORT_BASE = {"fixed-rate": "dcqcn.txt", "dcqcn": "dcqcn.txt", "hpcc": "hpcc.txt"}
CCA_FILES = TRANSPORT_FILE  # backwards-compatible name

# Buffers per switch tier (MiB). BUFFER_SIZE stays 32 (the validated value);
# the border routers are overridden through SWITCH_BUFFER_MAP_FILE.
BUFFER_MIB = {"tor": 32, "border": 128}
RESERVE_BYTES_PER_PORT = 4 * 1024
HEADROOM_FACTOR = 3
REFERENCE_RATE_BPS = 200_000_000_000
LINK_RATES_BPS = (50_000_000_000, 100_000_000_000, 200_000_000_000, 800_000_000_000)

# Extension keys shared by every E5 config (values are strings as written).
EXTENSION_KEYS = {
    "TRANSPORT_CONNECTION_MODE": "PERSISTENT",
    "ENABLE_PFC": "0",
    "PFC_THRESHOLD_VALIDATION": "STRICT",
    "QUEUE_LOG_MIN_BYTES": "0",
    "QLEN_MON_INTERVAL": "10000",
    "TRACE_LEVEL": "1",
    "ENABLE_CCA_TRACE": "1",
    "ENABLE_FLOW_METADATA_TRACE": "1",
    "ENABLE_PATH_TRACE": "1",
    "SWITCH_BUFFER_MAP_FILE": "configs/physical/buffers.txt",
}


def root():
    return os.path.abspath(os.path.join(HERE, ".."))


def e0_configs():
    return os.path.abspath(
        os.path.join(root(), "..", "..", "hoti22-reproduction", "e0-incast", "configs")
    )


def scale_value(name):
    return {"0.5x": 0.5, "1x": 1.0, "2x": 2.0}[name]


def scale_token(value):
    return {0.5: "0.5x", 1.0: "1x", 2.0: "2x"}[value]


def ring_order(mapping):
    path = os.path.join(root(), "configs", "logical", MAPPING_FILE[mapping])
    document = json.load(open(path))
    order = [int(gpu) for gpu in document["ring-order"]]
    if len(order) != N_GPU or sorted(order) != list(range(N_GPU)):
        raise SystemExit(f"{path} is not a permutation of 0..15")
    return order


def site_of_gpu(gpu):
    return SITES[gpu // GPUS_PER_SITE]


def site_of_node(mapping, node):
    return site_of_gpu(ring_order(mapping)[node])


def tor_id(site):
    return TOR0 + SITE_INDEX[site]


def border_id(site):
    return BORDER0 + SITE_INDEX[site]


def wan_delay_ns(kind, scale_name):
    return WAN_BASE_NS[kind] * SCALES[scale_name] // 2


def wan_rate_token(kind):
    gbps = WAN_BPS[kind] // 1_000_000_000
    return f"{gbps}Gbps"


def edge_pairs(mapping):
    sites = [site_of_node(mapping, node) for node in range(N_GPU)]
    pairs = []
    for index, site in enumerate(sites):
        pairs.append((site, sites[(index + 1) % N_GPU]))
    return pairs


def build(scale_name, mapping):
    """Return (lines, ports). GPU i is attached to the site of ring_order[i]."""
    switches = list(range(TOR0, BORDER0 + len(SITES)))
    links = []
    homes = {site: [] for site in SITES}
    for node in range(N_GPU):
        site = site_of_node(mapping, node)
        homes[site].append(node)
        links.append((node, tor_id(site), LOCAL_GPU_TOR[0], f"{LOCAL_GPU_TOR[1]}ns"))
    for site in SITES:
        links.append(
            (tor_id(site), border_id(site), LOCAL_TOR_BORDER[0], f"{LOCAL_TOR_BORDER[1]}ns")
        )
    wan_ports = {}
    next_if = {border_id(site): 2 for site in SITES}
    for left, right in WAN_ORDER:
        kind = WAN_CLASS[frozenset((left, right))]
        delay = wan_delay_ns(kind, scale_name)
        src = border_id(left)
        dst = border_id(right)
        links.append((src, dst, wan_rate_token(kind), f"{delay}ns"))
        wan_ports[f"{left}-{right}"] = {str(src): next_if[src], str(dst): next_if[dst]}
        next_if[src] += 1
        next_if[dst] += 1
    header = f"{N_GPU + len(switches)} {len(switches)} {len(links)}"
    body = [header, " ".join(str(switch) for switch in switches)]
    for src, dst, rate, delay in links:
        body.append(f"{src} {dst} {rate} {delay} 0")
    ports = {
        "tor": {site: tor_id(site) for site in SITES},
        "border": {site: border_id(site) for site in SITES},
        "tor_uplink": {str(tor_id(site)): [1 + GPUS_PER_SITE] for site in SITES},
        "border_tor_port": {str(border_id(site)): 1 for site in SITES},
        "wan_ports": wan_ports,
        "homes": {site: homes[site] for site in SITES},
        "scale": scale_name,
        "mapping": mapping,
    }
    return body, ports


def buffer_map():
    """node_id -> MiB for the switches that differ from BUFFER_SIZE."""
    return {border_id(site): BUFFER_MIB["border"] for site in SITES}


def write_buffer_map(path):
    with open(path, "w") as handle:
        handle.write("# node_id buffer_MiB  (border routers; ToRs keep BUFFER_SIZE)\n")
        for node, mib in sorted(buffer_map().items()):
            handle.write(f"{node} {mib}\n")


def e0_reference_thresholds():
    """(kmin_kb, kmax_kb, pmax) of the validated 200 Gbps entry in E0."""
    base = netconfig.as_dict(os.path.join(e0_configs(), "dcqcn.txt"))
    kmin = netconfig.parse_map(base["KMIN_MAP"])
    kmax = netconfig.parse_map(base["KMAX_MAP"])
    pmax = netconfig.parse_map(base["PMAX_MAP"])
    return int(kmin[REFERENCE_RATE_BPS]), int(kmax[REFERENCE_RATE_BPS]), pmax[REFERENCE_RATE_BPS]


def ecn_maps():
    """Proportional rule: K(rate) = K_200G x rate / 200G (KB units); PMAX unchanged."""
    kmin200, kmax200, pmax200 = e0_reference_thresholds()
    kmin, kmax, pmax = {}, {}, {}
    for rate in LINK_RATES_BPS:
        kmin[rate] = str(kmin200 * rate // REFERENCE_RATE_BPS)
        kmax[rate] = str(kmax200 * rate // REFERENCE_RATE_BPS)
        pmax[rate] = pmax200
    return {
        "KMIN_MAP": netconfig.format_map(kmin),
        "KMAX_MAP": netconfig.format_map(kmax),
        "PMAX_MAP": netconfig.format_map(pmax),
    }


def write_canonical(directory):
    """Write the three named latency files using latency-ring homing.

    Bandwidth-ring homing is a different GPU-to-site attachment. The runner
    emits that attachment into the run directory from the same builder.
    """
    os.makedirs(directory, exist_ok=True)
    for scale_name in SCALES:
        body, _ports = build(scale_name, "latency-ring")
        path = os.path.join(directory, f"wan-latency-{scale_name}.txt")
        with open(path, "w") as handle:
            handle.write("\n".join(body) + "\n")
    switches = list(range(TOR0, BORDER0 + len(SITES)))
    with open(os.path.join(directory, "trace.txt"), "w") as handle:
        handle.write(f"{len(switches)}\n")
        for switch in switches:
            handle.write(f"{switch}\n")
    _body, ports = build("1x", "latency-ring")
    with open(os.path.join(directory, "ports.json"), "w") as handle:
        json.dump(ports, handle, indent=2)
        handle.write("\n")
    write_buffer_map(os.path.join(directory, "buffers.txt"))


def neighbors_from_links(links):
    graph = {node: [] for node in range(N_GPU + 2 * len(SITES))}
    for src, dst, _rate, _delay, _error in links:
        graph[src].append(dst)
        graph[dst].append(src)
    return graph


def parse_topology(path):
    lines = open(path).read().splitlines()
    node_count, switch_count, link_count = map(int, lines[0].split())
    switches = list(map(int, lines[1].split()))
    links = []
    for line in lines[2:]:
        src, dst, rate, delay, error = line.split()
        links.append((int(src), int(dst), rate, delay, error))
    return node_count, switch_count, link_count, switches, links


def shortest_next(graph):
    """Hop-count BFS matching scratch/common.h CalculateRoute.

    Switches are expanded. Hosts are recorded but not expanded. next_hop[node][dest]
    lists neighbors of node that lie on a shortest path toward dest.
    """
    switches = set(range(TOR0, BORDER0 + len(SITES)))
    next_hop = {node: {dest: [] for dest in range(N_GPU)} for node in graph}
    for dest in range(N_GPU):
        distance = {dest: 0}
        queue = [dest]
        index = 0
        while index < len(queue):
            now = queue[index]
            index += 1
            depth = distance[now]
            for nxt in graph[now]:
                if nxt not in distance:
                    distance[nxt] = depth + 1
                    if nxt in switches:
                        queue.append(nxt)
                if depth + 1 == distance[nxt]:
                    next_hop[nxt][dest].append(now)
    return next_hop


def wan_edges_used(graph, src, dest):
    next_hop = shortest_next(graph)
    found = set()

    def walk(node, seen):
        if node == dest:
            return
        for nxt in next_hop[node][dest]:
            if nxt in seen:
                continue
            if node >= BORDER0 and nxt >= BORDER0:
                left = SITES[node - BORDER0]
                right = SITES[nxt - BORDER0]
                found.add(frozenset((left, right)))
            walk(nxt, seen | {nxt})

    walk(src, {src})
    return found


def pfc_headroom_table(scale_name):
    """What PFC headroom would be per border router if PFC were enabled.

    Documents why PFC is not an E5 transport: headroom = rate x delay / 8 x 3
    per port; the sum plus reserve must stay below the border buffer for the
    pause threshold to be meaningful (SwitchMmu::GetPfcThreshold is unsigned).
    """
    rows = []
    buffer_bytes = BUFFER_MIB["border"] * 1024 * 1024
    for site in SITES:
        ports = [("tor", LOCAL_TOR_BORDER[2], LOCAL_TOR_BORDER[1])]
        for other in SITES:
            if other == site:
                continue
            kind = WAN_CLASS[frozenset((site, other))]
            ports.append((f"wan-{other}", WAN_BPS[kind], wan_delay_ns(kind, scale_name)))
        headroom = {name: rate * delay // 8 // 1_000_000_000 * HEADROOM_FACTOR for name, rate, delay in ports}
        total = sum(headroom.values()) + RESERVE_BYTES_PER_PORT * len(ports)
        rows.append({
            "site": site,
            "scale": scale_name,
            "headroom_bytes": headroom,
            "total_committed_bytes": total,
            "buffer_bytes": buffer_bytes,
            "pfc_valid": total < buffer_bytes,
        })
    return rows


def static_edge_ns(kind, scale_name):
    rate = WAN_BPS[kind] // STREAMS_PER_EDGE
    serialization = (CHUNK_BYTES * 8 * 1_000_000_000) // rate
    return wan_delay_ns(kind, scale_name) + serialization


def static_prediction(scale_name, mapping):
    pairs = edge_pairs(mapping)
    counts = {}
    for left, right in pairs:
        key = frozenset((left, right))
        counts[key] = counts.get(key, 0) + 1
    costs = {}
    for key in counts:
        kind = WAN_CLASS[key]
        costs[key] = static_edge_ns(kind, scale_name)
    step = max(costs.values())
    return {
        "latency_scale": scale_value(scale_name),
        "mapping": mapping.replace("-", "_"),
        "predicted_step_ns": step,
        "predicted_collective_ns": step * RING_STEPS,
        "edge_counts": {"".join(sorted(key)): count for key, count in counts.items()},
        "edge_costs_ns": {"".join(sorted(key)): costs[key] for key in costs},
    }


def assign_ranks(rows):
    ordered = sorted(rows, key=lambda row: row["predicted_collective_ns"])
    best = ordered[0]["predicted_collective_ns"]
    for row in rows:
        if abs(row["predicted_collective_ns"] - best) / best <= TIE_BAND:
            row["predicted_rank"] = 1
        else:
            row["predicted_rank"] = 2


def predictions():
    rows = []
    for scale_name in SCALES:
        group = [static_prediction(scale_name, mapping) for mapping in MAPPINGS]
        assign_ranks(group)
        rows.extend(group)
    return rows


def crossover_holds(rows):
    """Return (ok, reason). Qualitative shape required before the packet matrix."""
    by_scale = {}
    for row in rows:
        by_scale.setdefault(row["latency_scale"], {})[row["mapping"]] = row
    for scale, group in by_scale.items():
        latency = group["latency_ring"]["predicted_collective_ns"]
        bandwidth = group["bandwidth_ring"]["predicted_collective_ns"]
        ratio = latency / bandwidth
        if scale == 0.5 and not ratio > 1 + TIE_BAND:
            return False, f"λ=0.5 ratio {ratio:.4f}; bandwidth ring is not predicted faster"
        if scale == 1.0 and abs(ratio - 1) > TIE_BAND:
            return False, f"λ=1 ratio {ratio:.4f}; rings are not predicted near-equal"
        if scale == 2.0 and not ratio < 1 - TIE_BAND:
            return False, f"λ=2 ratio {ratio:.4f}; latency ring is not predicted faster"
    return True, "static model: bandwidth faster at 0.5, near tie at 1, latency faster at 2"


def run_id(scale_name, mapping, transport):
    return f"wan{scale_value(scale_name)}__{mapping}__{transport}"


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "emit":
        scale_name, mapping, destination = sys.argv[2:]
        body, ports = build(scale_name, mapping)
        with open(destination, "w") as handle:
            handle.write("\n".join(body) + "\n")
        with open(destination.replace("topology.txt", "ports.json"), "w") as handle:
            json.dump(ports, handle, indent=2)
            handle.write("\n")
        return 0
    write_canonical(os.path.join(root(), "configs", "physical"))
    print("wrote canonical latency-scaled topologies and buffers.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
