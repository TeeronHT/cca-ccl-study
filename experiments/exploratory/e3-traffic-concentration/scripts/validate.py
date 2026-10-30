#!/usr/bin/env python3
"""Fail if the redesigned E3 does not match the audited traffic structure.

Does not start the simulator. Confirms the three fabrics differ only in
ToR->spine bitrate, and that the logical schedules still have the audited
byte and fan-out counts.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "common"))
import netconfig  # noqa: E402

PATH_KEYS = {
    "TOPOLOGY_FILE",
    "FLOW_FILE",
    "TRACE_FILE",
    "TRACE_OUTPUT_FILE",
    "FCT_OUTPUT_FILE",
    "PFC_OUTPUT_FILE",
    "QLEN_MON_FILE",
}


def fail(message):
    print(f"INVALID: {message}", file=sys.stderr)
    sys.exit(1)


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
    if node_count != model.N_GPU + len(switches):
        fail(f"{path}: node count {node_count}")
    return switches, links


def check_schedule():
    want = model.expected()
    for collective in model.COLLECTIVES:
        got = model.schedule(collective)
        for key, value in want[collective].items():
            if got[key] != value:
                fail(f"{collective} {key}={got[key]}, expected {value}")
        pairs = {(p["src"], p["dst"]): p for p in got["pairs"]}
        if len(pairs) != got["directed_pairs"]:
            fail(f"{collective} has duplicate directed pairs")
        per_rank = {}
        for pair in got["pairs"]:
            per_rank[pair["src"]] = per_rank.get(pair["src"], 0) + pair["bytes"]
        if set(per_rank) != set(range(model.N_GPU)):
            fail(f"{collective} does not send from every rank")
        if any(nbytes != want[collective]["per_rank_bytes"] for nbytes in per_rank.values()):
            fail(f"{collective} per-rank volume is not uniform")
    one = model.schedule("ring16")["total_bytes"]
    two = model.schedule("ring44")["total_bytes"]
    if one != two:
        fail(f"total logical volume differs: ring16 {one} ring44 {two}")
    print(
        "schedule: both collectives send 1920 MiB; "
        "ring16 cross-rack 480 MiB / 4 pairs / 1 flow per rack; "
        "ring44 cross-rack 384 MiB / 16 pairs / 4 flows per rack"
    )


def check_logical():
    logical = os.path.join(model.root(), "configs", "logical")
    for collective, dims in model.LOGICAL_DIMS.items():
        path = os.path.join(logical, model.LOGICAL_FILE[collective])
        if not os.path.exists(path):
            fail(f"missing {path}")
        document = json.load(open(path))
        if document.get("logical-dims") != dims:
            fail(f"{path} logical-dims {document.get('logical-dims')} != {dims}")
    print("logical: [16] and [4,4]")


def check_topologies():
    physical = os.path.join(model.root(), "configs", "physical")
    graphs = {}
    port_bodies = {}
    for name in model.FABRICS:
        path = os.path.join(physical, f"clos-{name}.txt")
        if not os.path.exists(path):
            fail(f"missing {path}; run generate_topologies.py")
        switches, links = parse_topology(path)
        spines = [switch for switch in switches if switch >= model.SPINE0]
        tors = [switch for switch in switches if model.TOR0 <= switch < model.SPINE0]
        if spines != list(range(model.SPINE0, model.SPINE0 + model.N_SPINE)):
            fail(f"{name}: spines {spines}")
        if tors != list(range(model.TOR0, model.TOR0 + model.N_TOR)):
            fail(f"{name}: tors {tors}")
        gpu_links = []
        uplink_links = []
        for src, dst, rate, delay, error in links:
            if delay != model.DELAY or error != "0":
                fail(f"{name}: link {src}-{dst} is {rate} {delay} {error}")
            if src < model.N_GPU and dst in tors:
                if rate != model.GPU_RATE:
                    fail(f"{name}: GPU {src} link is {rate}")
                if dst != model.TOR0 + src // model.GPUS_PER_RACK:
                    fail(f"{name}: GPU {src} is on ToR {dst}")
                gpu_links.append((src, dst))
            elif src in tors and dst in spines:
                uplink_links.append((src, dst, rate))
            else:
                fail(f"{name}: unexpected link {src}-{dst}")
        if len(gpu_links) != model.N_GPU:
            fail(f"{name}: {len(gpu_links)} GPU links")
        expected_rate = model.FABRIC_UPLINK[name][0]
        if len(uplink_links) != model.N_TOR * model.N_SPINE:
            fail(f"{name}: {len(uplink_links)} uplinks")
        for tor in tors:
            peers = sorted(dst for src, dst, _rate in uplink_links if src == tor)
            if peers != spines:
                fail(f"{name}: ToR {tor} uplinks {peers} != {spines}")
            rates = {rate for src, _dst, rate in uplink_links if src == tor}
            if rates != {expected_rate}:
                fail(f"{name}: ToR {tor} uplink rates {rates}")
        # Identity of the graph, ignoring the uplink rate token.
        graphs[name] = (tuple(gpu_links), tuple(sorted((s, d) for s, d, _r in uplink_links)))
        ports = json.load(open(os.path.join(physical, f"ports-{name}.json")))
        if ports["n_spine"] != model.N_SPINE or ports["n_tor"] != model.N_TOR:
            fail(f"{name}: ports file spine/tor count")
        if any(len(indexes) != model.N_SPINE for indexes in ports["tor_uplinks"].values()):
            fail(f"{name}: a ToR does not have {model.N_SPINE} uplink ifIndexes")
        port_bodies[name] = (
            ports["tor_uplinks"],
            ports["tor_downlinks"],
            ports["spine_ports"],
        )
    if len(set(graphs.values())) != 1:
        fail("fabrics do not share the same nodes and links")
    if len(set(json.dumps(body, sort_keys=True) for body in port_bodies.values())) != 1:
        fail("port index maps differ across fabrics")
    rates = [model.FABRIC_UPLINK[name][0] for name in model.FABRICS]
    if len(set(rates)) != 3:
        fail(f"uplink rates are not three distinct values: {rates}")
    print("topologies: 4 spines and 4 ECMP uplinks in every fabric; rates 200/100/50 Gbps")


def check_v1_untouched():
    path = os.path.join(model.v1_root(), "configs", "physical", "clos-4to1.txt")
    if not os.path.exists(path):
        fail(f"E3-v1 topology missing at {path}")
    _switches, links = parse_topology(path)
    spines = {dst for src, dst, *_rest in links if dst >= model.SPINE0}
    spines |= {src for src, dst, *_rest in links if src >= model.SPINE0}
    if spines != {model.SPINE0}:
        fail(f"E3-v1 clos-4to1.txt spines are {sorted(spines)}; the historical file was overwritten")
    print("E3-v1 clos-4to1.txt still has a single spine")


def check_cca():
    cca_dir = os.path.join(model.root(), "configs", "cca")
    maps = None
    parsed = {}
    for transport in model.TRANSPORTS:
        path = os.path.join(cca_dir, model.TRANSPORT_FILE[transport])
        if not os.path.exists(path):
            fail(f"missing {path}; run generate_configs.py")
        ours = netconfig.as_dict(path)
        canon = netconfig.as_dict(os.path.join(model.e0_configs(), model.TRANSPORT_BASE[transport]))
        allowed = set(PATH_KEYS) | set(model.EXTENSION_KEYS) | {"KMIN_MAP", "KMAX_MAP", "PMAX_MAP"}
        if transport == "fixed-rate":
            allowed |= {"CC_MODE", "HAS_WIN", "ENABLE_QCN"}
        for key, value in canon.items():
            if key in allowed:
                continue
            if ours.get(key) != value:
                fail(f"{transport} {key}={ours.get(key)} differs from E0 {value}")
        if ours.get("TRANSPORT_CONNECTION_MODE") != "PERSISTENT":
            fail(f"{transport} is not PERSISTENT")
        parsed[transport] = ours
        if maps is None:
            maps = (ours["KMIN_MAP"], ours["KMAX_MAP"], ours["PMAX_MAP"])
        elif (ours["KMIN_MAP"], ours["KMAX_MAP"], ours["PMAX_MAP"]) != maps:
            fail("CCA files do not share one ECN map")
    kmin = netconfig.parse_map(maps[0])
    kmax = netconfig.parse_map(maps[1])
    pmax = netconfig.parse_map(maps[2])
    if set(kmin) != set(model.ecn_rates()):
        fail(f"KMIN rates {sorted(kmin)} != {list(model.ecn_rates())}")
    for rate in model.ecn_rates():
        if int(kmin[rate]) != model.kmin_kb(rate) or int(kmax[rate]) != model.kmax_kb(rate):
            fail(f"ECN at {rate} is kmin {kmin[rate]} kmax {kmax[rate]}")
        if pmax[rate] != model.PMAX:
            fail(f"PMAX at {rate} is {pmax[rate]}")
    # The stock HPCC/DCQCN 100 Gbps row is 400/1600. This experiment must not use it.
    if int(kmin[100_000_000_000]) == 400:
        fail("100 Gbps Kmin is the stock 400 KB row")
    if parsed["pfc"]["CC_MODE"] != "0" or parsed["pfc"]["HAS_WIN"] != "0" or parsed["pfc"]["ENABLE_QCN"] != "0":
        fail("PFC-only is not E0 CC_MODE 0 / HAS_WIN 0 / ENABLE_QCN 0")
    if parsed["pfc"]["ENABLE_PFC"] != "1":
        fail("PFC-only ENABLE_PFC is not 1")
    if parsed["fixed-rate"]["CC_MODE"] != "0" or parsed["fixed-rate"]["HAS_WIN"] != "0":
        fail("fixed-rate is not CC_MODE 0 / HAS_WIN 0")
    if parsed["fixed-rate"]["ENABLE_PFC"] != "0" or parsed["dcqcn"]["ENABLE_PFC"] != "0" or parsed["hpcc"]["ENABLE_PFC"] != "0":
        fail("DCQCN, HPCC, or fixed-rate has PFC enabled")
    if parsed["dcqcn"]["CC_MODE"] != "1" or parsed["dcqcn"]["HAS_WIN"] != "0":
        fail("DCQCN is not CC_MODE 1 / HAS_WIN 0")
    if parsed["hpcc"]["CC_MODE"] != "3" or parsed["hpcc"]["HAS_WIN"] != "1":
        fail("HPCC is not CC_MODE 3 / HAS_WIN 1")
    if parsed["dcqcn"]["ENABLE_QCN"] != "1" or parsed["hpcc"]["ENABLE_QCN"] != "1":
        fail("DCQCN or HPCC ENABLE_QCN is not 1")
    print(
        "cca: PFC-only pauses, DCQCN/HPCC do not; ECN 200G 600/2400, "
        "100G 300/1200, 50G 150/600 KB"
    )


def check_workload():
    directory = os.path.join(model.root(), "workload")
    missing = [
        os.path.join(directory, f"allreduce.{rank}.et")
        for rank in range(model.N_GPU)
        if not os.path.exists(os.path.join(directory, f"allreduce.{rank}.et"))
    ]
    if missing:
        fail("workload traces missing; run generate_workload.py")
    print("workload: 16 traces")


def main():
    check_schedule()
    check_logical()
    check_topologies()
    check_v1_untouched()
    check_cca()
    check_workload()
    print("E3 traffic-concentration: valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
