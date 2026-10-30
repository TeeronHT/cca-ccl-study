#!/usr/bin/env python3
"""Summarize the redesigned E3 and gate the smoke runs.

S = T(1D Ring [16]) / T(2D Ring [4,4]).
S < 1 means the flat ring finished first. S > 1 means the 2D ring did.
"""

import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "common"))
import backend_outputs as bo  # noqa: E402

LINE_RATE_BPS = model.GPU_RATE_BPS
TIE = 0.02

CSV_FIELDS = [
    "run_id",
    "fabric",
    "uplink_rate",
    "collective",
    "transport",
    "transport_connection_mode",
    "role",
    "ecmp_seed",
    "collective_ns",
    "speedup_1d_over_2d",
    "messages",
    "logical_bytes",
    "cross_rack_bytes",
    "same_rack_bytes",
    "connections",
    "qps_created",
    "qp_reuse_messages",
    "messages_per_qp_min",
    "messages_per_qp_max",
    "distinct_paths",
    "distinct_sports",
    "max_tor_uplink_queue_bytes",
    "max_spine_queue_bytes",
    "max_tor_downlink_queue_bytes",
    "ecn_count",
    "cnp_count",
    "pfc_pause_count",
    "drop_count",
    "messages_outstanding",
    "uplink_util_avg",
    "uplink_util_max",
    "peak_crossrack_flows_per_rack",
    "peak_flows_per_uplink",
    "dcqcn_rate_decreases",
    "dcqcn_min_rate_bps",
    "hpcc_updates",
    "hpcc_initial_window_bytes",
    "hpcc_min_window_bytes",
    "hpcc_min_rate_bps",
    "study_commit",
    "astra_commit",
    "ns3_commit",
]


def load_run(run_dir):
    meta = json.load(open(os.path.join(run_dir, "meta.json")))
    ports = json.load(open(meta["ports_file"]))
    return meta, ports


def parse_qlen(path, ports):
    uplinks = {int(node): set(indexes) for node, indexes in ports["tor_uplinks"].items()}
    downs = {int(node): set(indexes) for node, indexes in ports["tor_downlinks"].items()}
    spines = {int(node) for node in ports["spine_ports"]}
    maxima = {"uplink": 0, "spine": 0, "downlink": 0}
    if not os.path.exists(path):
        return maxima
    with open(path) as handle:
        for line in handle:
            tokens = line.split()
            if len(tokens) < 3 or tokens[0] != "time":
                continue
            switch = int(tokens[2])
            index = 3
            while index + 2 < len(tokens) and tokens[index] == "j":
                port = int(tokens[index + 1])
                nbytes = int(tokens[index + 2])
                if switch in uplinks and port in uplinks[switch]:
                    maxima["uplink"] = max(maxima["uplink"], nbytes)
                elif switch in downs and port in downs[switch]:
                    maxima["downlink"] = max(maxima["downlink"], nbytes)
                elif switch in spines:
                    maxima["spine"] = max(maxima["spine"], nbytes)
                index += 3
    return maxima


def parse_fct(path):
    ends = []
    if not os.path.exists(path):
        return ends
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) >= 8:
                ends.append(int(parts[5]) + int(parts[6]))
    return ends


def hop_list(path):
    hops = []
    for piece in (path or "").split(">"):
        if not piece:
            continue
        if ":" in piece:
            node, iface = piece.split(":")
            hops.append((int(node), int(iface)))
        else:
            hops.append((int(piece), None))
    return hops


def spine_of(path, ports):
    spines = {int(node) for node in ports["spine_ports"]}
    found = [node for node, _iface in hop_list(path) if node in spines]
    return found


def connections_from_flows(flows, ports):
    grouped = {}
    for row in bo.payload_flows(flows):
        key = (row["src"], row["dst"])
        entry = grouped.setdefault(
            key,
            {
                "src": row["src"],
                "dst": row["dst"],
                "messages": 0,
                "bytes": 0,
                "msg_bytes": set(),
                "sports": set(),
                "paths": set(),
                "qp_ids": set(),
                "connection_ids": set(),
                "intervals": [],
            },
        )
        entry["messages"] += 1
        entry["bytes"] += row["bytes"]
        entry["msg_bytes"].add(row["bytes"])
        entry["sports"].add(row["sport"])
        entry["paths"].add(row.get("path") or "")
        entry["qp_ids"].add(row["qp_id"])
        entry["connection_ids"].add(row["connection_id"])
        entry["intervals"].append((row["network_start_time_ns"], row["completion_time_ns"]))
    rows = []
    for entry in grouped.values():
        paths = entry["paths"]
        path = next(iter(paths)) if len(paths) == 1 else ""
        spines = spine_of(path, ports) if path else []
        rows.append(
            {
                "src": entry["src"],
                "dst": entry["dst"],
                "src_rack": model.rack_of(entry["src"]),
                "dst_rack": model.rack_of(entry["dst"]),
                "cross_rack": int(model.rack_of(entry["src"]) != model.rack_of(entry["dst"])),
                "messages": entry["messages"],
                "bytes": entry["bytes"],
                "msg_bytes": ";".join(str(v) for v in sorted(entry["msg_bytes"])),
                "sport": next(iter(entry["sports"])) if len(entry["sports"]) == 1 else "",
                "sports": len(entry["sports"]),
                "qp_id": next(iter(entry["qp_ids"])) if len(entry["qp_ids"]) == 1 else "",
                "connection_id": next(iter(entry["connection_ids"])) if len(entry["connection_ids"]) == 1 else "",
                "path": path,
                "n_paths": len(paths),
                "spine": spines[0] if len(spines) == 1 else "",
                "n_spines": len(spines),
                "intervals": entry["intervals"],
            }
        )
    return rows


def peak_overlap(intervals):
    events = []
    for start, end in intervals:
        if end < start:
            start, end = end, start
        if end == start:
            end = start + 1
        events.append((start, 1))
        events.append((end, -1))
    events.sort()
    current = 0
    peak = 0
    for _time, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def concentration(conn_rows, ports):
    uplinks = {int(node): set(indexes) for node, indexes in ports["tor_uplinks"].items()}
    by_uplink = defaultdict(list)
    by_rack = defaultdict(list)
    for row in conn_rows:
        if not row["cross_rack"]:
            continue
        by_rack[row["src_rack"]].extend(row["intervals"])
        for node, iface in hop_list(row["path"]):
            if node in uplinks and iface in uplinks[node]:
                by_uplink[(node, iface)].extend(row["intervals"])
    per_rack = [peak_overlap(intervals) for intervals in by_rack.values()] or [0]
    per_uplink = [peak_overlap(intervals) for intervals in by_uplink.values()] or [0]
    return max(per_rack), max(per_uplink)


def uplink_utilization(counters, ports, uplink_bps, collective_ns):
    uplinks = {int(node): set(indexes) for node, indexes in ports["tor_uplinks"].items()}
    tx = {}
    for row in counters.get("switch_ports", []):
        node = row.get("node")
        iface = row.get("ifindex")
        if node in uplinks and iface in uplinks[node]:
            tx[(node, iface)] = row.get("tx_bytes", 0)
    n_uplinks = sum(len(indexes) for indexes in uplinks.values())
    if not collective_ns or uplink_bps <= 0:
        return "", ""
    capacity = uplink_bps / 8 * (collective_ns * 1e-9)
    utils = [tx.get((node, iface), 0) / capacity for node, indexes in uplinks.items() for iface in indexes]
    if len(utils) != n_uplinks:
        return "", ""
    return sum(utils) / len(utils), max(utils)


def hpcc_stats(trace):
    updates = [row for row in trace if row["event"] == "HPCC_UPDATE"]
    windows = []
    for row in trace:
        if row["event"] in ("QP_CREATE", "MESSAGE_START") and row["window_bytes"] > 0:
            windows.append(row["window_bytes"])
    update_windows = [row["window_bytes"] for row in updates if row["window_bytes"] > 0]
    rates = [row["hp_cur_rate_bps"] for row in updates if row["hp_cur_rate_bps"] > 0]
    rates += [row["rate_bps"] for row in updates if row["rate_bps"] > 0]
    return {
        "hpcc_updates": len(updates),
        "hpcc_initial_window_bytes": min(windows) if windows else "",
        "hpcc_min_window_bytes": min(update_windows) if update_windows else "",
        "hpcc_min_rate_bps": min(rates) if rates else "",
    }


def schedule_problems(conn_rows, collective):
    problems = []
    want = {(p["src"], p["dst"]): p for p in model.schedule(collective)["pairs"]}
    got = {(row["src"], row["dst"]): row for row in conn_rows}
    if set(got) != set(want):
        missing = sorted(set(want) - set(got))[:5]
        extra = sorted(set(got) - set(want))[:5]
        problems.append(f"directed pairs differ; missing {missing} extra {extra}")
        return problems
    for key, pair in want.items():
        row = got[key]
        if row["messages"] != pair["messages"] or row["bytes"] != pair["bytes"]:
            problems.append(
                f"{key[0]}->{key[1]} messages/bytes {row['messages']}/{row['bytes']} "
                f"!= {pair['messages']}/{pair['bytes']}"
            )
            break
    return problems


def persistence_problems(conn_rows):
    problems = []
    if not conn_rows:
        return ["no payload flows"]
    for row in conn_rows:
        if row["sports"] != 1 or row["n_paths"] != 1 or not row["path"]:
            problems.append(f"{row['src']}->{row['dst']} does not keep one port and one path")
            break
        if row["messages"] < 2:
            problems.append(f"{row['src']}->{row['dst']} carried {row['messages']} messages")
            break
        if row["cross_rack"] and row["n_spines"] != 1:
            problems.append(f"cross-rack {row['src']}->{row['dst']} path uses spines {row['n_spines']}: {row['path']}")
            break
        if not row["cross_rack"] and row["n_spines"] != 0:
            problems.append(f"same-rack {row['src']}->{row['dst']} entered a spine: {row['path']}")
            break
    return problems


def fabric_problems(run_dir, meta):
    problems = []
    report = bo.read_link_report(os.path.join(run_dir, "link_report.txt"))
    if not report["links"]:
        return ["link_report.txt is missing"]
    uplink_bps = model.FABRIC_UPLINK[meta["fabric"]][1]
    spines = set()
    for link in report["links"]:
        src, dst = link["src"], link["dst"]
        if link["delay_ns"] != model.DELAY_NS:
            problems.append(f"link {src}-{dst} delay {link['delay_ns']}")
            break
        if src >= model.SPINE0 or dst >= model.SPINE0:
            spines.add(src if src >= model.SPINE0 else dst)
            if link["rate_bps"] != uplink_bps:
                problems.append(f"uplink {src}-{dst} is {link['rate_bps']} bps, expected {uplink_bps}")
                break
        elif src < model.N_GPU or dst < model.N_GPU:
            if link["rate_bps"] != model.GPU_RATE_BPS:
                problems.append(f"GPU link {src}-{dst} is {link['rate_bps']} bps")
                break
    if spines != set(range(model.SPINE0, model.SPINE0 + model.N_SPINE)):
        problems.append(f"spines in the link report are {sorted(spines)}")
    kmin = model.kmin_kb(uplink_bps) * 1000
    kmax = model.kmax_kb(uplink_bps) * 1000
    host_kmin = model.KMIN_KB_AT_200G * 1000
    ports = json.load(open(meta["ports_file"]))
    uplinks = {int(node): set(indexes) for node, indexes in ports["tor_uplinks"].items()}
    downs = {int(node): set(indexes) for node, indexes in ports["tor_downlinks"].items()}
    seen_up = 0
    for port in report["ports"]:
        node, iface = port["node"], port["ifindex"]
        if node in uplinks and iface in uplinks[node]:
            seen_up += 1
            if port["rate_bps"] != uplink_bps or port["kmin_bytes"] != kmin or port["kmax_bytes"] != kmax:
                problems.append(
                    f"ToR {node} uplink {iface}: rate {port['rate_bps']} "
                    f"kmin {port['kmin_bytes']} kmax {port['kmax_bytes']}"
                )
                break
        elif node in downs and iface in downs[node]:
            if port["kmin_bytes"] != host_kmin:
                problems.append(f"ToR {node} downlink {iface} kmin {port['kmin_bytes']} != {host_kmin}")
                break
    if seen_up != model.N_TOR * model.N_SPINE:
        problems.append(f"link report has {seen_up} ToR uplinks, expected {model.N_TOR * model.N_SPINE}")
    return problems


def summarize(run_dir):
    meta, ports = load_run(run_dir)
    flows = bo.read_flows(os.path.join(run_dir, "flows.csv"))
    counters = bo.read_counters(os.path.join(run_dir, "counters.txt"))
    trace = bo.read_cca_trace(os.path.join(run_dir, "cca_trace.csv"))
    conn_rows = connections_from_flows(flows, ports)
    ends = parse_fct(os.path.join(run_dir, "fct.txt"))
    collective_ns = max(ends) if ends else ""
    queues = parse_qlen(os.path.join(run_dir, "qlen.txt"), ports)
    uplink_bps = model.FABRIC_UPLINK[meta["fabric"]][1]
    util_avg, util_max = uplink_utilization(counters, ports, uplink_bps, collective_ns or 0)
    peak_rack, peak_uplink = concentration(conn_rows, ports)
    rate = bo.dcqcn_rate_stats(trace, LINE_RATE_BPS)
    hpcc = hpcc_stats(trace)
    cross = sum(row["bytes"] for row in conn_rows if row["cross_rack"])
    same = sum(row["bytes"] for row in conn_rows if not row["cross_rack"])
    messages = sum(row["messages"] for row in conn_rows)
    per_qp = [row["messages"] for row in conn_rows]
    reuse = messages - len(conn_rows) if conn_rows else ""
    summary = {
        "run_id": meta["run_id"],
        "fabric": meta["fabric"],
        "uplink_rate": meta["uplink_rate"],
        "collective": meta["collective"],
        "transport": meta["transport"],
        "transport_connection_mode": meta.get("transport_connection_mode", ""),
        "role": "primary" if meta["transport"] in model.PRIMARY_TRANSPORTS else "diagnostic",
        "ecmp_seed": meta.get("ecmp_seed", ""),
        "collective_ns": collective_ns,
        "speedup_1d_over_2d": "",
        "messages": messages,
        "logical_bytes": cross + same,
        "cross_rack_bytes": cross,
        "same_rack_bytes": same,
        "connections": len(conn_rows),
        "qps_created": counters.get("qps_created", ""),
        "qp_reuse_messages": reuse,
        "messages_per_qp_min": min(per_qp) if per_qp else "",
        "messages_per_qp_max": max(per_qp) if per_qp else "",
        "distinct_paths": len({row["path"] for row in conn_rows}),
        "distinct_sports": len({row["sport"] for row in conn_rows if row["sport"] != ""}),
        "max_tor_uplink_queue_bytes": queues["uplink"],
        "max_spine_queue_bytes": queues["spine"],
        "max_tor_downlink_queue_bytes": queues["downlink"],
        "ecn_count": counters.get("ecn_marks", ""),
        "cnp_count": counters.get("cnp_acks", ""),
        "pfc_pause_count": counters.get("pfc_pause_frames", ""),
        "drop_count": counters.get("mmu_drops", ""),
        "messages_outstanding": counters.get("messages_outstanding", ""),
        "uplink_util_avg": util_avg,
        "uplink_util_max": util_max,
        "peak_crossrack_flows_per_rack": peak_rack,
        "peak_flows_per_uplink": peak_uplink,
        "dcqcn_rate_decreases": rate["rate_decreases"],
        "dcqcn_min_rate_bps": rate["min_rate_bps"],
        "study_commit": meta.get("study_commit", ""),
        "astra_commit": meta.get("astra_commit", ""),
        "ns3_commit": meta.get("ns3_commit", ""),
    }
    summary.update(hpcc)
    summary["_conn_rows"] = conn_rows
    summary["_trace"] = trace
    summary["_counters"] = counters
    summary["_meta"] = meta
    summary["_schedule_problems"] = schedule_problems(conn_rows, meta["collective"])
    summary["_persistence_problems"] = persistence_problems(conn_rows)
    summary["_fabric_problems"] = fabric_problems(run_dir, meta)
    write_connections(run_dir, conn_rows)
    public = {key: summary[key] for key in CSV_FIELDS}
    with open(os.path.join(run_dir, "summary.json"), "w") as handle:
        json.dump(public, handle, indent=2)
        handle.write("\n")
    return summary


def write_connections(run_dir, rows):
    fields = [
        "src", "dst", "src_rack", "dst_rack", "cross_rack", "messages", "bytes",
        "msg_bytes", "sport", "qp_id", "connection_id", "path", "spine",
    ]
    path = os.path.join(run_dir, "connections.csv")
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows, key=lambda item: (item["src"], item["dst"])):
            writer.writerow(row)


def base_problems(row):
    problems = []
    problems.extend(row["_schedule_problems"])
    problems.extend(row["_persistence_problems"])
    problems.extend(row["_fabric_problems"])
    if row["transport_connection_mode"] != "PERSISTENT":
        problems.append("transport mode is not PERSISTENT")
    if str(row["drop_count"]) != "0":
        problems.append(f"drop_count={row['drop_count']!r}, expected 0")
    if str(row["messages_outstanding"]) != "0":
        problems.append(f"messages_outstanding={row['messages_outstanding']!r}, expected 0")
    finished = row["_counters"].get("all_ranks_finished")
    if finished not in (1, "1"):
        problems.append(f"all_ranks_finished={finished}")
    pfc_on = str(row["_meta"].get("enable_pfc")) == "1"
    if row["transport"] == "pfc" and not pfc_on:
        problems.append("PFC-only run does not have ENABLE_PFC 1")
    if not pfc_on and int(row["pfc_pause_count"] or 0) != 0:
        problems.append(f"PFC pauses={row['pfc_pause_count']} with ENABLE_PFC 0")
    want = model.expected()[row["collective"]]
    if row["logical_bytes"] != want["total_bytes"]:
        problems.append(f"logical bytes {row['logical_bytes']} != {want['total_bytes']}")
    if row["messages"] != want["messages"] or row["cross_rack_bytes"] != want["cross_rack_bytes"]:
        problems.append(
            f"traffic {row['messages']} msgs / {row['cross_rack_bytes']} cross-rack bytes "
            f"!= {want['messages']} / {want['cross_rack_bytes']}"
        )
    if row["same_rack_bytes"] != want["same_rack_bytes"]:
        problems.append(f"same-rack bytes {row['same_rack_bytes']} != {want['same_rack_bytes']}")
    if row["qps_created"] != "" and int(row["qps_created"]) != row["connections"]:
        problems.append(f"qps_created {row['qps_created']} != connections {row['connections']}")
    return problems


def gate_pfc(row):
    problems = base_problems(row)
    if row["transport"] != "pfc":
        problems.append("not a PFC-only run")
    if str(row["_meta"].get("cc_mode")) != "0" or str(row["_meta"].get("has_win")) != "0":
        problems.append("PFC-only is not CC_MODE 0 / HAS_WIN 0")
    if str(row["_meta"].get("enable_qcn")) != "0":
        problems.append("PFC-only has ENABLE_QCN on")
    return problems


def gate_fixed(row):
    problems = base_problems(row)
    if row["transport"] != "fixed-rate":
        problems.append("not a fixed-rate run")
    adaptive = [
        item for item in row["_trace"]
        if item["event"] in ("RATE_DECREASE", "RATE_INCREASE", "HPCC_UPDATE", "HPCC_FAST_REACT")
    ]
    if adaptive:
        problems.append(f"fixed-rate has {len(adaptive)} adaptation events")
    starts = [item for item in row["_trace"] if item["event"] == "MESSAGE_START"]
    if starts and any(item["rate_bps"] != LINE_RATE_BPS for item in starts):
        problems.append("a fixed-rate message started below the 200 Gbps NIC rate")
    return problems


def gate_dcqcn(row):
    problems = base_problems(row)
    if row["transport"] != "dcqcn":
        problems.append("not a DCQCN run")
    uplink_bps = model.FABRIC_UPLINK[row["fabric"]][1]
    kmin = model.kmin_kb(uplink_bps) * 1000
    kmax = model.kmax_kb(uplink_bps) * 1000
    queue = int(row["max_tor_uplink_queue_bytes"] or 0)
    ecn = int(row["ecn_count"] or 0)
    decreases = int(row["dcqcn_rate_decreases"] or 0)
    if queue >= kmax and (ecn == 0 or decreases == 0):
        problems.append(
            f"uplink queue {queue} >= Kmax {kmax} but ecn={ecn} rate_decreases={decreases}"
        )
    elif queue > kmin and ecn == 0 and decreases == 0:
        problems.append(f"uplink queue {queue} entered Kmin {kmin} with no ECN and no rate decrease")
    elif queue <= kmin:
        print(f"    uplink queue {queue} stayed at or under Kmin {kmin}; no DCQCN reaction required")
    else:
        print(f"    DCQCN feedback: ecn={ecn} rate_decreases={decreases} min_rate={row['dcqcn_min_rate_bps']}")
    return problems


def finish_gate(label, problems):
    if problems:
        print(f"{label} FAILED:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"{label}: passed")
    return 0


def add_speedups(rows):
    index = {(row["fabric"], row["transport"], row["collective"]): row for row in rows}
    for row in rows:
        one = index.get((row["fabric"], row["transport"], "ring16"))
        two = index.get((row["fabric"], row["transport"], "ring44"))
        if (
            row["role"] == "primary"
            and one
            and two
            and one["role"] == "primary"
            and two["role"] == "primary"
            and one["collective_ns"]
            and two["collective_ns"]
        ):
            row["speedup_1d_over_2d"] = float(one["collective_ns"]) / float(two["collective_ns"])


def path_map(row):
    return {(item["src"], item["dst"]): item["path"] for item in row["_conn_rows"]}


def path_stability(rows):
    """The same collective must pick the same persistent paths at every capacity."""
    problems = []
    by_collective = defaultdict(list)
    for row in rows:
        by_collective[row["collective"]].append(row)
    for collective, group in by_collective.items():
        if len({row["fabric"] for row in group}) < 2:
            continue
        # Compare within one transport. fixed-rate is enough; check every transport
        # that has more than one fabric.
        by_transport = defaultdict(list)
        for row in group:
            by_transport[row["transport"]].append(row)
        for transport, runs in by_transport.items():
            if len(runs) < 2:
                continue
            reference = path_map(runs[0])
            for other in runs[1:]:
                if path_map(other) != reference:
                    problems.append(
                        f"{collective}/{transport}: persistent paths differ between "
                        f"{runs[0]['fabric']} and {other['fabric']}"
                    )
    return problems


def write_csv(path, rows):
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _ms(value):
    return float(value) / 1e6


def plot(rows, directory):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = list(model.FABRICS)
    labels = [model.FABRIC_LABEL[name] for name in order]
    colors = {"pfc": "C0", "dcqcn": "C1", "hpcc": "C2", "fixed-rate": "0.6"}
    primary = [row for row in rows if row["role"] == "primary"]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for transport in model.PRIMARY_TRANSPORTS:
        series = []
        for fabric in order:
            match = next(
                (row for row in rows if row["fabric"] == fabric and row["transport"] == transport and row["collective"] == "ring16"),
                None,
            )
            if match and match["role"] == "primary" and isinstance(match["speedup_1d_over_2d"], float):
                series.append(match["speedup_1d_over_2d"])
            else:
                series = None
                break
        if not series:
            continue
        ax.plot(labels, series, marker="o", color=colors[transport], label=transport)
    ax.axhline(1.0, color="black", linewidth=0.8)
    ax.set_ylabel("T(1D Ring [16]) / T(2D Ring [4,4])")
    ax.set_xlabel("ToR-spine capacity")
    ax.set_title("Relative collective completion")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(directory, "relative-speedup.png"), dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for collective, style in (("ring16", "-"), ("ring44", "--")):
        for transport in model.PRIMARY_TRANSPORTS:
            xs, ys = [], []
            for fabric, label in zip(order, labels):
                match = next(
                    (row for row in rows if row["fabric"] == fabric and row["collective"] == collective and row["transport"] == transport and row["collective_ns"] != ""),
                    None,
                )
                if match:
                    xs.append(label)
                    ys.append(_ms(match["collective_ns"]))
            if ys:
                ax.plot(xs, ys, marker="o", linestyle=style, color=colors[transport], label=f"{model.COLLECTIVE_LABEL[collective]} / {transport}")
    ax.set_ylabel("collective completion (ms)")
    ax.set_title("Completion time")
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(directory, "cct.png"), dpi=120)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(11, 4), sharey=False)
    fields = (
        ("max_tor_uplink_queue_bytes", "ToR uplink"),
        ("max_spine_queue_bytes", "Spine"),
        ("max_tor_downlink_queue_bytes", "ToR GPU downlink"),
    )
    for ax, (field, title) in zip(axes, fields):
        for collective, style in (("ring16", "-"), ("ring44", "--")):
            for transport in model.PRIMARY_TRANSPORTS:
                ys = []
                ok = True
                for fabric in order:
                    match = next(
                        (row for row in rows if row["fabric"] == fabric and row["collective"] == collective and row["transport"] == transport),
                        None,
                    )
                    if not match:
                        ok = False
                        break
                    ys.append(int(match[field]) / 1e6)
                if ok:
                    ax.plot(labels, ys, marker="o", linestyle=style, color=colors[transport], label=f"{collective}/{transport}")
        ax.set_title(title)
        ax.set_ylabel("max queue (MB)")
        ax.grid(True, alpha=0.3)
    axes[0].legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(os.path.join(directory, "queue-by-location.png"), dpi=120)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    for ax, field, title in (
        (axes[0], "peak_crossrack_flows_per_rack", "Peak cross-rack flows from one rack"),
        (axes[1], "peak_flows_per_uplink", "Peak flows on one ToR uplink"),
    ):
        for collective, style in (("ring16", "-"), ("ring44", "--")):
            for transport in model.PRIMARY_TRANSPORTS:
                ys = []
                ok = True
                for fabric in order:
                    match = next(
                        (row for row in rows if row["fabric"] == fabric and row["collective"] == collective and row["transport"] == transport),
                        None,
                    )
                    if not match:
                        ok = False
                        break
                    ys.append(int(match[field]))
                if ok:
                    ax.plot(labels, ys, marker="o", linestyle=style, label=model.COLLECTIVE_LABEL[collective])
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(directory, "traffic-concentration.png"), dpi=120)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    target = [row for row in primary if row["fabric"] == "4to1" and row["collective"] == "ring44"]
    if not target:
        target = rows
    axes[0].bar(
        [row["transport"] for row in target],
        [float(row["dcqcn_min_rate_bps"] or 0) / 1e9 for row in target],
    )
    axes[0].set_ylabel("minimum recorded rate (Gbps)")
    axes[0].set_title("4:1 2D Ring, minimum sender rate")
    axes[1].bar(
        [row["transport"] for row in target],
        [int(row["hpcc_updates"] or 0) for row in target],
        color="C2",
    )
    axes[1].set_ylabel("HPCC_UPDATE count")
    axes[1].set_title("HPCC updates (zero means the window did not run)")
    fig.tight_layout()
    fig.savefig(os.path.join(directory, "cca-response.png"), dpi=120)
    plt.close(fig)


def preference(speedup):
    if abs(speedup - 1.0) <= TIE:
        return "tie"
    if speedup < 1:
        return "1D Ring [16]"
    return "2D Ring [4,4]"


def interpret(rows):
    print("S = T(1D Ring [16]) / T(2D Ring [4,4])")
    print("Primary transports are PFC-only, DCQCN, and HPCC. Fixed-rate is not on this curve.")
    signs = {}
    missing = False
    for transport in model.PRIMARY_TRANSPORTS:
        signs[transport] = {}
        for fabric in model.FABRICS:
            match = next(
                (
                    row for row in rows
                    if row["fabric"] == fabric and row["transport"] == transport and row["collective"] == "ring16" and row["role"] == "primary"
                ),
                None,
            )
            if not match or not isinstance(match["speedup_1d_over_2d"], float):
                missing = True
                print(f"  {model.FABRIC_LABEL[fabric]} {transport}: missing")
                continue
            speedup = match["speedup_1d_over_2d"]
            choice = preference(speedup)
            signs[transport][fabric] = choice
            print(f"  {model.FABRIC_LABEL[fabric]} {transport}: S={speedup:.3f} -> {choice}")
    if missing:
        print("Primary matrix is incomplete. No CCL×CCA claim.")
        return
    changed = False
    for fabric in model.FABRICS:
        choices = {signs[transport][fabric] for transport in model.PRIMARY_TRANSPORTS}
        if len(choices) > 1:
            changed = True
            print(f"ranking at {model.FABRIC_LABEL[fabric]} depends on transport: {choices}")
    if not changed:
        print(
            "PFC, DCQCN, and HPCC keep the same collective ranking at each capacity. "
            "Adaptive rate control does not change which traffic structure wins. "
            "That is negative evidence for a CCL×CCA interaction. "
            "A difference in absolute completion time or queue depth is not that result."
        )
    else:
        print(
            "The preferred collective depends on the transport at one or more capacities. "
            "PFC is lossless backpressure without an end-host rate controller. "
            "A difference from DCQCN or HPCC is the cross-layer result."
        )


def commit_groups(rows):
    return {(row["study_commit"], row["astra_commit"], row["ns3_commit"]) for row in rows}


def main():
    parser_args = sys.argv[1:]
    if parser_args[:1] == ["--one"] and len(parser_args) == 2:
        row = summarize(parser_args[1])
        print(
            f"{row['run_id']} cct_ns={row['collective_ns']} messages={row['messages']} "
            f"cross_rack_bytes={row['cross_rack_bytes']} connections={row['connections']} "
            f"qps={row['qps_created']} reuse={row['qp_reuse_messages']} "
            f"paths={row['distinct_paths']} drops={row['drop_count']} "
            f"outstanding={row['messages_outstanding']}"
        )
        return 0
    if parser_args[:1] == ["--gate"] and len(parser_args) == 3:
        kind, run_dir = parser_args[1], parser_args[2]
        row = summarize(run_dir)
        if kind == "fixed":
            problems = gate_fixed(row)
        elif kind == "pfc":
            problems = gate_pfc(row)
        elif kind == "dcqcn":
            problems = gate_dcqcn(row)
        else:
            print(f"unknown gate {kind}", file=sys.stderr)
            return 2
        return finish_gate(f"smoke {kind} {row['run_id']}", problems)

    raw = os.path.join(model.root(), "results", "raw")
    rows = []
    if os.path.isdir(raw):
        for name in sorted(os.listdir(raw)):
            run_dir = os.path.join(raw, name)
            if os.path.isfile(os.path.join(run_dir, "meta.json")):
                rows.append(summarize(run_dir))
    if not rows:
        print("no runs", file=sys.stderr)
        return 1
    add_speedups(rows)
    out_dir = os.path.join(model.root(), "results")
    rejected = []
    for row in rows:
        problems = base_problems(row)
        label = f"{row['role']} {row['run_id']}"
        if problems:
            rejected.append(row["run_id"])
            print(f"REJECT {label}")
            for item in problems:
                print(f"  - {item}")
        else:
            print(
                f"ACCEPT {label} messages={row['messages']} "
                f"logical_bytes={row['logical_bytes']} cross_rack_bytes={row['cross_rack_bytes']} "
                f"connections={row['connections']} qps={row['qps_created']} "
                f"drops={row['drop_count']} outstanding={row['messages_outstanding']}"
            )
    write_csv(os.path.join(out_dir, "summary.csv"), rows)
    write_csv(os.path.join(out_dir, "manifest.csv"), rows)
    groups = commit_groups(rows)
    if len(groups) != 1:
        print("REFUSING to plot: runs come from more than one simulator commit", file=sys.stderr)
        for study, astra, ns3 in groups:
            print(f"  study {study} astra {astra} ns3 {ns3}", file=sys.stderr)
        return 2
    if rejected:
        print(f"rejected {len(rejected)} run(s); not a basis for the speedup curve", file=sys.stderr)
        return 1
    problems = path_stability([row for row in rows if row["role"] == "primary"])
    for item in problems:
        print(f"PATH: {item}", file=sys.stderr)
    plot(rows, out_dir)
    interpret(rows)
    diagnostic = [row["run_id"] for row in rows if row["role"] == "diagnostic"]
    if diagnostic:
        print("diagnostic fixed-rate runs kept out of the speedup curve: " + ", ".join(diagnostic))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
