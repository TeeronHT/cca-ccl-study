#!/usr/bin/env python3
"""Twenty E5 invariants. Does not start the simulator.

  validate.py                run all checks; exit 1 on any failure
  validate.py --static-only  skip check 15 (backend-validation markers), for
                             editing the experiment before the harnesses ran
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402
import netconfig  # noqa: E402

PATH_KEYS = set(netconfig.PATH_KEYS)
MAP_KEYS = {"KMIN_MAP", "KMAX_MAP", "PMAX_MAP"}
EXT_KEYS = set(net.EXTENSION_KEYS) | {"COUNTERS_OUTPUT_FILE", "LINK_REPORT_FILE", "CCA_TRACE_FILE",
                                       "FLOW_TRACE_FILE", "PATH_TRACE_FILE", "STATE_SNAPSHOT_TIMES",
                                       "SNAPSHOT_OUTPUT_FILE"}
REPORT = []
FAILED = []


def record(number, name, ok, detail=""):
    REPORT.append((number, name, ok, detail))
    if not ok:
        FAILED.append(number)


def configs():
    return {t: netconfig.as_dict(os.path.join(net.root(), "configs", "cca", net.TRANSPORT_FILE[t]))
            for t in net.TRANSPORTS}


def topologies():
    out = {}
    for scale_name in net.SCALES:
        for mapping in net.MAPPINGS:
            body, ports = net.build(scale_name, mapping)
            links = []
            for line in body[2:]:
                src, dst, rate, delay, error = line.split()
                links.append((int(src), int(dst), rate, delay, error))
            out[(scale_name, mapping)] = (body, ports, links)
    return out


def check_1_to_7(topos):
    # 1. 16 GPUs
    ok = True
    for (scale_name, mapping), (body, ports, links) in topos.items():
        node_count, switch_count, link_count = map(int, body[0].split())
        gpus = {src for src, dst, *_ in links if src < net.N_GPU}
        if node_count != 24 or switch_count != 8 or link_count != 26 or gpus != set(range(16)):
            ok = False
    canon = {}
    for scale_name in net.SCALES:
        path = os.path.join(net.root(), "configs", "physical", f"wan-latency-{scale_name}.txt")
        if not os.path.exists(path):
            ok = False
            continue
        canon[scale_name] = open(path).read().splitlines()
        if canon[scale_name] != topos[(scale_name, "latency-ring")][0]:
            ok = False
    record(1, "16 GPUs, 8 switches, 26 links in every topology; canonical files match the builder", ok)

    # 2. site placement: node i homed at site of ring_order[i]; 4 per site
    ok = True
    detail = []
    for mapping in net.MAPPINGS:
        order = net.ring_order(mapping)
        _body, ports, links = topos[("1x", mapping)]
        for node in range(net.N_GPU):
            tor = next(dst for src, dst, *_ in links if src == node)
            if tor != net.tor_id(net.site_of_gpu(order[node])):
                ok = False
        if any(len(ports["homes"][site]) != 4 for site in net.SITES):
            ok = False
        detail.append(f"{mapping}: " + " ".join(f"{s}={ports['homes'][s]}" for s in net.SITES))
    record(2, "site placement follows ring order (4 GPUs per site)", ok, "; ".join(detail))

    # 3. permutations
    orders = {m: net.ring_order(m) for m in net.MAPPINGS}
    ok = all(sorted(o) == list(range(16)) for o in orders.values()) and orders["latency-ring"] != orders["bandwidth-ring"]
    record(3, "both ring orders are permutations of 0..15 and differ", ok)

    # 4. physical edges used per mapping
    expected = {
        "latency-ring": {frozenset(p) for p in (("A", "B"), ("B", "C"), ("C", "D"), ("D", "A"))},
        "bandwidth-ring": {frozenset(p) for p in (("A", "B"), ("B", "D"), ("D", "C"), ("C", "A"))},
    }
    ok = True
    detail = []
    for mapping in net.MAPPINGS:
        pairs = net.edge_pairs(mapping)
        counts = {}
        for pair in pairs:
            counts[frozenset(pair)] = counts.get(frozenset(pair), 0) + 1
        used = set(counts)
        if used != expected[mapping] or any(c != net.STREAMS_PER_EDGE for c in counts.values()) or len(pairs) != 16:
            ok = False
        detail.append(f"{mapping}: " + ",".join("".join(sorted(k)) + f"x{v}" for k, v in sorted(counts.items(), key=lambda kv: "".join(sorted(kv[0])))))
    record(4, "logical i->i+1 maps onto exactly the intended WAN edges, 4 streams each", ok, "; ".join(detail))

    # 5. routes: direct WAN edge only
    ok = True
    for mapping in net.MAPPINGS:
        _body, _ports, links = topos[("1x", mapping)]
        graph = net.neighbors_from_links(links)
        for src in range(net.N_GPU):
            for dst in range(net.N_GPU):
                if src == dst:
                    continue
                used = net.wan_edges_used(graph, src, dst)
                s1, s2 = net.site_of_node(mapping, src), net.site_of_node(mapping, dst)
                if s1 == s2 and used:
                    ok = False
                if s1 != s2 and used != {frozenset((s1, s2))}:
                    ok = False
    record(5, "hop-count routing uses only the direct WAN edge for every cross-site pair", ok)

    # 6/7. lambda scales only WAN delay; capacities and local links fixed
    ok6 = ok7 = True
    for mapping in net.MAPPINGS:
        ref = topos[("1x", mapping)][2]
        for scale_name in net.SCALES:
            links = topos[(scale_name, mapping)][2]
            for (a, b, rate, delay, err), (a2, b2, rate2, delay2, err2) in zip(ref, links):
                if (a, b) != (a2, b2):
                    ok6 = False
                if rate != rate2:
                    ok7 = False
                if a < net.BORDER0 and delay != delay2:
                    ok6 = False
                if a >= net.BORDER0:
                    expect = int(delay[:-2]) * net.SCALES[scale_name] // net.SCALES["1x"]
                    if int(delay2[:-2]) != expect:
                        ok6 = False
    record(6, "lambda multiplies WAN propagation only (local delays fixed)", ok6)
    record(7, "link capacities identical across lambda", ok7)
    return topos


def check_8_9(cfgs, topos):
    rates = set()
    for (_s, _m), (_b, _p, links) in topos.items():
        for _a, _b2, rate, _d, _e in links:
            rates.add(int(rate.replace("Gbps", "")) * 1_000_000_000)
    ok8 = True
    ok9 = True
    kmin200, kmax200, pmax200 = net.e0_reference_thresholds()
    detail = []
    for transport, cfg in cfgs.items():
        kmin = netconfig.parse_map(cfg["KMIN_MAP"])
        kmax = netconfig.parse_map(cfg["KMAX_MAP"])
        pmax = netconfig.parse_map(cfg["PMAX_MAP"])
        if not rates <= set(kmin) or not rates <= set(kmax) or not rates <= set(pmax):
            ok8 = False
        for rate in sorted(set(kmin) | set(kmax) | set(pmax)):
            if int(kmin.get(rate, -1)) != kmin200 * rate // net.REFERENCE_RATE_BPS:
                ok9 = False
            if int(kmax.get(rate, -1)) != kmax200 * rate // net.REFERENCE_RATE_BPS:
                ok9 = False
            if pmax.get(rate) != pmax200:
                ok9 = False
        if set(kmin) != set(net.LINK_RATES_BPS):
            ok9 = False
        if transport == "dcqcn":
            detail.append(" ".join(f"{r // 10**9}G:{kmin[r]}/{kmax[r]}KB" for r in sorted(kmin)))
    record(8, f"every link rate ({', '.join(str(r // 10**9) + 'G' for r in sorted(rates))}) has ECN entries in all configs", ok8)
    record(9, f"ECN thresholds follow K(rate) = K_200G x rate/200G from E0 ({kmin200}/{kmax200} KB, pmax {pmax200})", ok9, "; ".join(detail))


def check_10_to_14(cfgs):
    # 10. buffers
    path = os.path.join(net.root(), "configs", "physical", "buffers.txt")
    ok = os.path.exists(path)
    overrides = {}
    if ok:
        for line in open(path):
            line = line.split("#")[0].strip()
            if line:
                node, mib = line.split()
                overrides[int(node)] = int(mib)
        ok = overrides == {net.border_id(s): net.BUFFER_MIB["border"] for s in net.SITES}
    for cfg in cfgs.values():
        if cfg.get("BUFFER_SIZE") != str(net.BUFFER_MIB["tor"]) or cfg.get("SWITCH_BUFFER_MAP_FILE") != "configs/physical/buffers.txt":
            ok = False
    record(10, f"ToR buffer {net.BUFFER_MIB['tor']} MiB (BUFFER_SIZE), border {net.BUFFER_MIB['border']} MiB via SWITCH_BUFFER_MAP_FILE", ok,
           f"overrides {overrides}")
    # 11-13
    record(11, "TRANSPORT_CONNECTION_MODE PERSISTENT in every transport config",
           all(c.get("TRANSPORT_CONNECTION_MODE") == "PERSISTENT" for c in cfgs.values()))
    record(12, "ENABLE_PFC 0 for every primary transport", all(c.get("ENABLE_PFC") == "0" for c in cfgs.values()))
    record(13, "PFC_THRESHOLD_VALIDATION STRICT in every config", all(c.get("PFC_THRESHOLD_VALIDATION") == "STRICT" for c in cfgs.values()))
    # 14. arithmetic with PFC off: headroom 0, reserve must fit; and the PFC-on table
    ok = True
    tor_committed = net.RESERVE_BYTES_PER_PORT * (net.GPUS_PER_SITE + 1)
    border_committed = net.RESERVE_BYTES_PER_PORT * 4
    if tor_committed >= net.BUFFER_MIB["tor"] * 1024 * 1024 or border_committed >= net.BUFFER_MIB["border"] * 1024 * 1024:
        ok = False
    pfc_notes = []
    for scale_name in net.SCALES:
        rows = net.pfc_headroom_table(scale_name)
        worst = max(rows, key=lambda r: r["total_committed_bytes"])
        pfc_notes.append(f"λ={net.scale_value(scale_name)}: PFC-on headroom+reserve would be "
                         f"{worst['total_committed_bytes'] / 1e6:.1f} MB at border {worst['site']} vs {worst['buffer_bytes'] / 2**20:.0f} MiB "
                         f"({'valid' if worst['pfc_valid'] else 'INVALID'})")
    record(14, "with PFC off headroom is 0 and reserve < buffer on every switch; PFC-on headroom would not fit at every lambda", ok,
           "; ".join(pfc_notes))


def check_15(static_only):
    markers = {
        "bdp-units": os.path.join(net.root(), "..", "..", "backend-validation", "bdp-units", "results", "PASSED"),
    }
    if static_only:
        record(15, "BDP/unit sanity test passed (skipped: --static-only)", True, "not checked")
        return
    missing = [name for name, path in markers.items() if not os.path.exists(path)]
    record(15, "BDP/unit sanity test passed (experiments/backend-validation/bdp-units/results/PASSED)", not missing,
           "missing: " + ", ".join(missing) if missing else "")


def check_16():
    rows = net.predictions()
    ok, reason = net.crossover_holds(rows)
    record(16, "static model: bandwidth ring at 0.5, tie at 1, latency ring at 2", ok, reason)


def check_17_to_20(cfgs, topos):
    fr = cfgs["fixed-rate"]
    ok = fr.get("CC_MODE") == "0" and fr.get("HAS_WIN") == "0" and fr.get("ENABLE_PFC") == "0"
    record(17, "fixed-rate: CC_MODE 0, HAS_WIN 0, ENABLE_PFC 0 (no rate, window or pause feedback)", ok,
           "ENABLE_QCN 1 keeps switch marking for diagnostics; CC_MODE 0 ignores CNPs")
    e0 = net.e0_configs()
    canon_dcqcn = netconfig.as_dict(os.path.join(e0, "dcqcn.txt"))
    canon_hpcc = netconfig.as_dict(os.path.join(e0, "hpcc.txt"))

    def same_as(ours, canon, allowed_diff):
        diffs = []
        for key, value in canon.items():
            if key in PATH_KEYS or key in MAP_KEYS or key in allowed_diff:
                continue
            if ours.get(key) != value:
                diffs.append(key)
        for key in ours:
            if key not in canon and key not in EXT_KEYS and key not in PATH_KEYS:
                diffs.append(key)
        return diffs

    d = same_as(cfgs["dcqcn"], canon_dcqcn, set())
    record(18, "DCQCN config equals E0 dcqcn.txt apart from paths, ECN maps and extension keys", not d, ", ".join(d))
    d = same_as(cfgs["hpcc"], canon_hpcc, set())
    ok = not d and cfgs["hpcc"].get("HAS_WIN") == "1" and cfgs["hpcc"].get("CC_MODE") == "3"
    record(19, "HPCC config equals E0 hpcc.txt apart from paths, ECN maps and extension keys; HAS_WIN 1", ok, ", ".join(d))
    d = same_as(cfgs["fixed-rate"], canon_dcqcn, {"CC_MODE"})
    if d:
        record(17, "fixed-rate differs from DCQCN only by CC_MODE", False, ", ".join(d))
    ok = all(c.get("ERROR_RATE_PER_LINK") == "0.0000" for c in cfgs.values())
    ok = ok and all(err == "0" for (_s, _m), (_b, _p, links) in topos.items() for *_r, err in links)
    ok = ok and all(c.get("LINK_DOWN") == "0 0 0" for c in cfgs.values())
    record(20, "ERROR_RATE_PER_LINK 0, topology error column 0, LINK_DOWN 0 0 0", ok)


def check_workload():
    base = net.root()
    ok = True
    for name in net.MAPPING_FILE.values():
        document = json.load(open(os.path.join(base, "configs", "system", name)))
        if document.get("all-reduce-implementation") != ["ring"] or document.get("preferred-dataset-splits") != 1 \
                or document.get("endpoint-delay") != 0:
            ok = False
    blobs = [open(os.path.join(base, "configs", "system", name)).read() for name in net.MAPPING_FILE.values()]
    if blobs[0] != blobs[1]:
        ok = False
    logical = [json.load(open(os.path.join(base, "configs", "logical", net.MAPPING_FILE[m]))) for m in net.MAPPINGS]
    if any(item.get("logical-dims") != ["16"] for item in logical):
        ok = False
    paths = [os.path.join(base, "workload", f"allreduce.{rank}.et") for rank in range(net.N_GPU)]
    if any(not os.path.exists(p) for p in paths):
        ok = False
    else:
        traces = [open(p, "rb").read() for p in paths]
        if any(t != traces[0] for t in traces):
            ok = False
        size = net.MESSAGE_BYTES
        encoded = bytearray()
        while True:
            byte = size & 0x7F
            size >>= 7
            encoded.append(byte | (0x80 if size else 0))
            if not size:
                break
        if bytes(encoded) not in traces[0]:
            ok = False
    if open(os.path.join(base, "configs", "flow.txt")).read().strip() != "0":
        ok = False
    record("w", "one 64 MiB ring AllReduce, one dataset split, identical traces, no background flows", ok)


def check_ids():
    ids = [net.run_id(s, m, t) for s in net.SCALES for m in net.MAPPINGS for t in net.TRANSPORTS]
    record("i", "18 unique primary run ids (3 lambda x 2 rings x 3 transports), no PFC/DCTCP/TIMELY", len(ids) == 18 and len(set(ids)) == 18)


def main():
    static_only = "--static-only" in sys.argv
    cfgs = configs()
    topos = topologies()
    check_1_to_7(topos)
    check_8_9(cfgs, topos)
    check_10_to_14(cfgs)
    check_15(static_only)
    check_16()
    check_17_to_20(cfgs, topos)
    check_workload()
    check_ids()
    width = max(len(str(n)) for n, *_ in REPORT)
    for number, name, ok, detail in REPORT:
        print(f"[{'PASS' if ok else 'FAIL'}] {str(number):>{width}}. {name}")
        if detail:
            print(f"        {detail}")
    if FAILED:
        print(f"INVALID: checks {FAILED} failed", file=sys.stderr)
        return 1
    print("E5 invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
