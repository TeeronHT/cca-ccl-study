#!/usr/bin/env python3
"""Summarize E5. Plotting is optional and is skipped when matplotlib is absent.

Primary number per transport c and latency scale λ:
    R(c, λ) = CCT(latency ring) / CCT(bandwidth ring)
compared with the static model's ratio. Runs with any packet drop are
invalid and excluded from the ranking.
"""

import argparse
import csv
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402
import backend_outputs as bo  # noqa: E402

TRACE_RECORD_BYTES = 56
EVENT_DEQUEUE = 2
EVENT_DROP = 3
L3_UDP = 0x11
L3_ACK = 0xFC
L3_NACK = 0xFD
ECN_CE = 0x03
CNP_FLAG = 1
LINE_RATE_BPS = 200_000_000_000
MIN_RATE_BPS = 100_000_000
EXPECTED_MESSAGES_PER_CONNECTION = net.RING_STEPS  # every rank sends 2(n-1) messages to its successor

CSV_FIELDS = [
    "run_id",
    "latency_scale",
    "mapping",
    "transport",
    "transport_connection_mode",
    "valid",
    "tensor_bytes",
    "ring_chunk_bytes",
    "collective_ns",
    "predicted_collective_ns",
    "model_error",
    "max_queue_bytes",
    "mean_wan_queue_bytes",
    "max_wan_queue_bytes",
    "max_border_queue_bytes",
    "pfc_pause_count",
    "ecn_count",
    "cnp_count",
    "drop_count",
    "flow_count",
    "logical_message_count",
    "connection_count",
    "qp_count",
    "qp_reuse_count",
    "messages_per_qp",
    "distinct_paths",
    "messages_outstanding",
    "latency_ring_to_bandwidth_ring_ratio",
    "static_ratio",
    "static_winner",
    "actual_winner",
    "ranking_agrees",
    "dcqcn_mean_rate_bps",
    "dcqcn_min_rate_bps",
    "dcqcn_rate_decreases",
    "dcqcn_rate_increases",
    "dcqcn_time_below_25pct",
    "dcqcn_time_below_50pct",
    "dcqcn_time_below_75pct",
    "hpcc_updates",
    "hpcc_mean_rate_bps",
    "hpcc_min_rate_bps",
    "hpcc_mean_window_bytes",
    "study_commit",
    "astra_commit",
    "ns3_commit",
    "seed",
]


def load_run(run_dir):
    meta = json.load(open(os.path.join(run_dir, "meta.json")))
    ports = json.load(open(meta["ports_file"]))
    wan = {}
    borders = set()
    for edge, ends in ports["wan_ports"].items():
        for node, iface in ends.items():
            wan[(int(node), int(iface))] = edge
            borders.add(int(node))
    return meta, ports, wan, borders


def parse_fct(path):
    ends = []
    if not os.path.exists(path):
        return ends
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 8:
                continue
            ends.append(int(parts[5]) + int(parts[6]))
    return ends


def parse_qlen(path, wan, borders):
    max_any = 0
    max_wan = 0
    max_border = 0
    wan_sum = 0
    wan_samples = 0
    names = {f"{left}-{right}": 0 for left, right in net.WAN_ORDER}
    if not os.path.exists(path):
        return max_any, max_wan, max_border, names, ""
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
                max_any = max(max_any, nbytes)
                if switch in borders:
                    max_border = max(max_border, nbytes)
                edge = wan.get((switch, port))
                if edge:
                    max_wan = max(max_wan, nbytes)
                    names[edge] = max(names[edge], nbytes)
                    wan_sum += nbytes
                    wan_samples += 1
                index += 3
    mean = (wan_sum / wan_samples) if wan_samples else ""
    return max_any, max_wan, max_border, names, mean


def parse_pfc(path, wan):
    total = 0
    per_edge = {f"{left}-{right}": 0 for left, right in net.WAN_ORDER}
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return total, per_edge
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) != 5 or int(parts[4]) != 1:
                continue
            total += 1
            edge = wan.get((int(parts[1]), int(parts[3])))
            if edge:
                per_edge[edge] += 1
    return total, per_edge


def parse_trace(path, wan, borders):
    """Packet-trace fallback (TRACE_LEVEL 2 runs only)."""
    stats = {
        "drops": 0,
        "ecn": 0,
        "cnp": 0,
        "bytes": {f"{left}-{right}": 0 for left, right in net.WAN_ORDER},
        "ecn_edge": {f"{left}-{right}": 0 for left, right in net.WAN_ORDER},
        "present": False,
    }
    if not os.path.exists(path) or os.path.getsize(path) < 8:
        return stats
    stats["present"] = True
    with open(path, "rb") as handle:
        blob = handle.read()
    (nports,) = struct.unpack_from("<I", blob, 0)
    offset = 4 + nports * 11 + 4
    if offset > len(blob) or (len(blob) - offset) % TRACE_RECORD_BYTES != 0:
        raise SystemExit(f"{path}: trace is not a whole number of records")
    while offset + TRACE_RECORD_BYTES <= len(blob):
        node = struct.unpack_from("<H", blob, offset + 8)[0]
        intf = blob[offset + 10]
        size = struct.unpack_from("<H", blob, offset + 24)[0]
        l3 = blob[offset + 26]
        event = blob[offset + 27]
        ecn = blob[offset + 28]
        flags = struct.unpack_from("<H", blob, offset + 36)[0]
        if event == EVENT_DROP:
            stats["drops"] += 1
        edge = wan.get((node, intf))
        if event == EVENT_DEQUEUE and edge:
            stats["bytes"][edge] += size
            if l3 == L3_UDP and ecn == ECN_CE:
                stats["ecn_edge"][edge] += 1
        if node in borders:
            if l3 == L3_UDP and ecn == ECN_CE and event == EVENT_DEQUEUE:
                stats["ecn"] += 1
            if l3 in (L3_ACK, L3_NACK) and flags & CNP_FLAG and event == EVENT_DEQUEUE:
                stats["cnp"] += 1
        offset += TRACE_RECORD_BYTES
    return stats


def edge_stats_from_counters(counters, wan):
    """Per WAN edge tx bytes and ECN marks (both directions) from counters.txt."""
    bytes_by_edge = {f"{left}-{right}": 0 for left, right in net.WAN_ORDER}
    ecn_by_edge = {f"{left}-{right}": 0 for left, right in net.WAN_ORDER}
    for port in counters.get("switch_ports", []):
        edge = wan.get((port.get("node"), port.get("ifindex")))
        if edge:
            bytes_by_edge[edge] += port.get("tx_bytes", 0)
            ecn_by_edge[edge] += port.get("ecn_marks", 0)
    return bytes_by_edge, ecn_by_edge


def hpcc_stats(trace):
    updates = [r for r in trace if r["event"] == "HPCC_UPDATE"]
    if not updates:
        return {"hpcc_updates": 0, "hpcc_mean_rate_bps": "", "hpcc_min_rate_bps": "", "hpcc_mean_window_bytes": ""}
    rates = [r["hp_cur_rate_bps"] for r in updates]
    windows = [r["window_bytes"] for r in updates]
    return {
        "hpcc_updates": len(updates),
        "hpcc_mean_rate_bps": int(sum(rates) / len(rates)),
        "hpcc_min_rate_bps": min(rates),
        "hpcc_mean_window_bytes": int(sum(windows) / len(windows)),
    }


def winner(ratio):
    if ratio == "" or ratio is None:
        return ""
    value = float(ratio)
    if value < 1 - net.TIE_BAND:
        return "latency_ring"
    if value > 1 + net.TIE_BAND:
        return "bandwidth_ring"
    return "tie"


def summarize(run_dir):
    meta, _ports, wan, borders = load_run(run_dir)
    ends = parse_fct(os.path.join(run_dir, "fct.txt"))
    max_any, max_wan, max_border, edge_queue, mean_wan = parse_qlen(os.path.join(run_dir, "qlen.txt"), wan, borders)
    pauses, edge_pfc = parse_pfc(os.path.join(run_dir, "pfc.txt"), wan)
    trace = parse_trace(os.path.join(run_dir, "mix.tr"), wan, borders)
    counters = bo.read_counters(os.path.join(run_dir, "counters.txt"))
    flows = bo.read_flows(os.path.join(run_dir, "flows.csv"))
    paths = bo.read_paths(os.path.join(run_dir, "paths.csv"))
    cca_trace = bo.read_cca_trace(os.path.join(run_dir, "cca_trace.csv"))
    payload = bo.payload_flows(flows)
    conns = bo.connection_summary(flows)
    qp_ids = {qp for entry in conns.values() for qp in entry["qp_ids"]}
    if counters.get("present"):
        edge_bytes, edge_ecn = edge_stats_from_counters(counters, wan)
    else:
        edge_bytes, edge_ecn = trace["bytes"], trace["ecn_edge"]
    collective = max((r["completion_time_ns"] for r in payload), default=0) if payload else (max(ends) if ends else 0)
    scale_name = meta["scale"]
    mapping = meta["mapping"]
    transport = meta.get("transport", meta.get("cca"))
    predicted = net.static_prediction(scale_name, mapping)["predicted_collective_ns"]
    error = (collective / predicted) if collective and predicted else ""
    drops = bo.drop_count(run_dir, trace["drops"])
    finished = counters.get("all_ranks_finished", 1) == 1 and counters.get("messages_outstanding", 0) == 0
    rate = bo.dcqcn_rate_stats(cca_trace, LINE_RATE_BPS) if (cca_trace and transport == "dcqcn") else {}
    hp = hpcc_stats(cca_trace) if transport == "hpcc" else {}
    conn_paths = {p["connection_id"]: p["path"] for p in paths if p["record"] == "connection"}
    row = {
        "run_id": meta["run_id"],
        "latency_scale": meta["latency_scale"],
        "mapping": mapping.replace("-", "_"),
        "transport": transport,
        "transport_connection_mode": meta.get("transport_connection_mode", "LEGACY"),
        "valid": "true" if (drops == 0 and finished and collective) else "false",
        "tensor_bytes": net.MESSAGE_BYTES,
        "ring_chunk_bytes": net.CHUNK_BYTES,
        "collective_ns": collective,
        "predicted_collective_ns": predicted,
        "model_error": f"{error:.6f}" if error != "" else "",
        "max_queue_bytes": max_any,
        "mean_wan_queue_bytes": f"{mean_wan:.1f}" if mean_wan != "" else "",
        "max_wan_queue_bytes": max_wan,
        "max_border_queue_bytes": max_border,
        "pfc_pause_count": counters.get("pfc_pause_frames", pauses),
        "ecn_count": counters.get("ecn_marks", trace["ecn"]),
        "cnp_count": counters.get("cnp_acks", trace["cnp"]),
        "drop_count": drops,
        "flow_count": len(ends),
        "logical_message_count": len(payload) if flows else len(ends),
        "connection_count": len(conns) if flows else "",
        "qp_count": len(qp_ids) if flows else "",
        "qp_reuse_count": (len(payload) - len(conns)) if flows else "",
        "messages_per_qp": f"{len(payload) / len(qp_ids):.3f}" if qp_ids else "",
        "distinct_paths": len(set(conn_paths.values())) if conn_paths else "",
        "messages_outstanding": counters.get("messages_outstanding", ""),
        "latency_ring_to_bandwidth_ring_ratio": "",
        "static_ratio": "",
        "static_winner": "",
        "actual_winner": "",
        "ranking_agrees": "",
        "dcqcn_mean_rate_bps": rate.get("mean_rate_bps", ""),
        "dcqcn_min_rate_bps": rate.get("min_rate_bps", ""),
        "dcqcn_rate_decreases": rate.get("rate_decreases", ""),
        "dcqcn_rate_increases": rate.get("rate_increases", ""),
        "dcqcn_time_below_25pct": rate.get("time_below_25pct", ""),
        "dcqcn_time_below_50pct": rate.get("time_below_50pct", ""),
        "dcqcn_time_below_75pct": rate.get("time_below_75pct", ""),
        "hpcc_updates": hp.get("hpcc_updates", ""),
        "hpcc_mean_rate_bps": hp.get("hpcc_mean_rate_bps", ""),
        "hpcc_min_rate_bps": hp.get("hpcc_min_rate_bps", ""),
        "hpcc_mean_window_bytes": hp.get("hpcc_mean_window_bytes", ""),
        "study_commit": meta.get("study_commit", ""),
        "astra_commit": meta.get("astra_commit", ""),
        "ns3_commit": meta.get("ns3_commit", ""),
        "seed": meta.get("seed", ""),
        "_scale_name": scale_name,
        "_mapping": mapping,
        "_edge_bytes": edge_bytes,
        "_edge_ecn": edge_ecn,
        "_edge_queue": edge_queue,
        "_edge_pfc": edge_pfc,
        "_flows": flows,
        "_conns": conns,
        "_paths": paths,
        "_conn_paths": conn_paths,
        "_cca_trace": cca_trace,
        "_counters": counters,
        "_meta": meta,
        "_wan": wan,
    }
    with open(os.path.join(run_dir, "summary.json"), "w") as handle:
        json.dump({k: v for k, v in row.items() if not k.startswith("_")}, handle, indent=2)
        handle.write("\n")
    return row


def lookup(rows, scale, transport, mapping):
    for row in rows:
        if (
            float(row["latency_scale"]) == float(scale)
            and row["transport"] == transport
            and row["mapping"] == mapping
            and row["valid"] == "true"
            and int(row["collective_ns"])
        ):
            return row
    return None


def add_derived(rows):
    static = {}
    for row in net.predictions():
        static.setdefault(row["latency_scale"], {})[row["mapping"]] = row["predicted_collective_ns"]
    for row in rows:
        scale = float(row["latency_scale"])
        latency = lookup(rows, scale, row["transport"], "latency_ring")
        bandwidth = lookup(rows, scale, row["transport"], "bandwidth_ring")
        static_ratio = static[scale]["latency_ring"] / static[scale]["bandwidth_ring"]
        row["static_ratio"] = f"{static_ratio:.6f}"
        row["static_winner"] = winner(static_ratio)
        if latency and bandwidth:
            actual = int(latency["collective_ns"]) / int(bandwidth["collective_ns"])
            row["latency_ring_to_bandwidth_ring_ratio"] = f"{actual:.6f}"
            row["actual_winner"] = winner(actual)
            row["ranking_agrees"] = str(row["static_winner"] == row["actual_winner"]).lower()


def print_run(row):
    print(
        f"wan{row['latency_scale']} {row['_mapping']} {row['transport']} [{row['transport_connection_mode']}]:\n"
        f"    predicted={row['predicted_collective_ns']} actual={row['collective_ns']} valid={row['valid']}\n"
        f"    max_wan_queue={row['max_wan_queue_bytes']} mean_wan_queue={row['mean_wan_queue_bytes']}\n"
        f"    ecn={row['ecn_count']} cnp={row['cnp_count']} drops={row['drop_count']} pfc={row['pfc_pause_count']}\n"
        f"    messages={row['logical_message_count']} connections={row['connection_count']} qps={row['qp_count']} "
        f"messages_per_qp={row['messages_per_qp']} paths={row['distinct_paths']} outstanding={row['messages_outstanding']}"
    )
    if row["transport"] == "dcqcn":
        print(f"    dcqcn mean_rate={row['dcqcn_mean_rate_bps']} min_rate={row['dcqcn_min_rate_bps']} "
              f"decreases={row['dcqcn_rate_decreases']} increases={row['dcqcn_rate_increases']} "
              f"below50={row['dcqcn_time_below_50pct']}")
    if row["transport"] == "hpcc":
        print(f"    hpcc updates={row['hpcc_updates']} mean_rate={row['hpcc_mean_rate_bps']} "
              f"min_rate={row['hpcc_min_rate_bps']} mean_window={row['hpcc_mean_window_bytes']}")
    if int(row["drop_count"]) > 0:
        print(
            f"INVALID: {row['run_id']} dropped {row['drop_count']} packets. "
            "Not interpreting this CCT and not changing buffers or CCA parameters.",
            file=sys.stderr,
        )
        return 1
    if row["valid"] != "true":
        print(f"INVALID: {row['run_id']} did not finish (outstanding messages or no completion).", file=sys.stderr)
        return 1
    return 0


def print_ratio_table(rows):
    print("\nmapping ratio CCT_latency_ring / CCT_bandwidth_ring")
    print(f"{'':<12}{'λ=0.5':>12}{'λ=1':>12}{'λ=2':>12}")
    print(f"{'static':<12}", end="")
    for scale in (0.5, 1.0, 2.0):
        sample = next((row for row in rows if float(row["latency_scale"]) == scale), None)
        cell = sample["static_ratio"] if sample else ""
        print(f"{cell:>12}", end="")
    print()
    for transport in net.TRANSPORTS:
        print(f"{transport:<12}", end="")
        for scale in (0.5, 1.0, 2.0):
            sample = lookup(rows, scale, transport, "latency_ring")
            cell = sample["latency_ring_to_bandwidth_ring_ratio"] if sample else ""
            print(f"{cell:>12}", end="")
        print()
    print("\ncrossover against y=1, using a 1% indifference band")
    for transport in net.TRANSPORTS:
        points = []
        for scale in (0.5, 1.0, 2.0):
            sample = lookup(rows, scale, transport, "latency_ring")
            if not sample or sample["actual_winner"] == "":
                points.append(None)
            else:
                points.append(sample["actual_winner"])
        print(f"  {transport}: {describe_crossover(points)}")
    # the two findings that would count
    fixed = {s: (lookup(rows, s, "fixed-rate", "latency_ring") or {}).get("actual_winner") for s in (0.5, 1.0, 2.0)}
    for scale in (0.5, 1.0, 2.0):
        winners = {t: (lookup(rows, scale, t, "latency_ring") or {}).get("actual_winner") for t in net.TRANSPORTS}
        known = {t: w for t, w in winners.items() if w}
        if len(known) >= 2 and len(set(known.values())) > 1:
            print(f"  λ={scale}: transports disagree on the winner: {known}")
    if all(fixed.values()):
        print(f"  fixed-rate winners by λ: {fixed}")


def describe_crossover(points):
    if any(point is None for point in points):
        return "incomplete"
    at_half, at_one, at_two = points
    if at_half == at_one == at_two:
        if at_one == "tie":
            return "no switch; the mappings stay near-equal"
        return f"no switch; {at_one} stays faster"
    if at_half == "bandwidth_ring" and at_one == "latency_ring":
        return "switch before λ=1"
    if at_one == "tie" or (at_half != at_two and at_one == "tie"):
        return "switch around λ=1"
    if at_one == "bandwidth_ring" and at_two == "latency_ring":
        return "switch after λ=1"
    if at_half == "latency_ring" and at_two == "bandwidth_ring":
        return "switch in the opposite direction from the static model"
    return f"winners at 0.5/1/2: {at_half}, {at_one}, {at_two}"


def write_csv(path, rows):
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_edges(path, rows):
    fields = ["run_id", "edge", "bytes", "max_queue_bytes", "ecn_count", "pfc_pause_count"]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            for left, right in net.WAN_ORDER:
                edge = f"{left}-{right}"
                writer.writerow(
                    {
                        "run_id": row["run_id"],
                        "edge": edge,
                        "bytes": row["_edge_bytes"][edge],
                        "max_queue_bytes": row["_edge_queue"][edge],
                        "ecn_count": row["_edge_ecn"][edge],
                        "pfc_pause_count": row["_edge_pfc"][edge],
                    }
                )


def write_paths(path, rows):
    fields = ["run_id", "connection_id", "qp_id", "src", "dst", "sport", "messages", "path"]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            for key, entry in sorted(row["_conns"].items(), key=lambda kv: (kv[1]["src"], kv[1]["dst"])):
                conn_id = key if isinstance(key, int) else 0
                writer.writerow({
                    "run_id": row["run_id"],
                    "connection_id": conn_id,
                    "qp_id": ",".join(map(str, sorted(entry["qp_ids"]))),
                    "src": entry["src"],
                    "dst": entry["dst"],
                    "sport": ",".join(map(str, sorted(entry["sports"]))),
                    "messages": entry["messages"],
                    "path": row["_conn_paths"].get(conn_id, ""),
                })


def write_manifest(path, run_dirs):
    fields = [
        "run_id", "study_commit", "astra_commit", "ns3_commit", "transport", "transport_connection_mode",
        "rendezvous", "cc_mode", "has_win", "enable_qcn", "enable_pfc", "pfc_threshold_validation",
        "cca_config_hash", "cca_sha256", "topology_sha256", "logical_sha256", "system_sha256", "workload_sha256",
        "buffer_size_mib", "switch_buffer_map", "switch_buffer_map_sha256", "ecn_maps", "trace_level",
        "queue_log_min_bytes", "qlen_mon_interval_ns", "route", "seed", "hostname", "start", "end",
    ]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for run_dir in run_dirs:
            meta = json.load(open(os.path.join(run_dir, "meta.json")))
            if isinstance(meta.get("ecn_maps"), dict):
                meta["ecn_maps"] = json.dumps(meta["ecn_maps"], sort_keys=True)
            writer.writerow(meta)


def plot(rows, directory):
    try:
        import matplotlib
    except ImportError:
        print("matplotlib is not installed; skipping plots")
        return
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(directory, exist_ok=True)
    scales = [0.5, 1.0, 2.0]

    def series(transport, mapping, key):
        values = []
        for scale in scales:
            match = lookup(rows, scale, transport, mapping)
            values.append(float(match[key]) if match and match[key] != "" else None)
        return values

    for transport in net.TRANSPORTS:
        plt.figure()
        for mapping in ("latency_ring", "bandwidth_ring"):
            plt.plot(scales, series(transport, mapping, "collective_ns"), marker="o", label=mapping)
        plt.xticks(scales)
        plt.xlabel("WAN latency scale λ")
        plt.ylabel("collective completion time (ns)")
        plt.title(transport)
        plt.legend()
        plt.tight_layout()
        plt.savefig(os.path.join(directory, f"cct-{transport}.png"))
        plt.close()

    plt.figure()
    static_line = []
    for scale in scales:
        sample = next((row for row in rows if float(row["latency_scale"]) == scale), None)
        static_line.append(float(sample["static_ratio"]) if sample else None)
    plt.plot(scales, static_line, marker="s", linestyle="--", color="black", label="static model")
    for transport in net.TRANSPORTS:
        plt.plot(scales, series(transport, "latency_ring", "latency_ring_to_bandwidth_ring_ratio"), marker="o", label=transport)
    plt.axhline(1.0, color="gray", linewidth=0.8)
    plt.xticks(scales)
    plt.xlabel("WAN latency scale λ")
    plt.ylabel("CCT latency ring / CCT bandwidth ring")
    plt.title("E5 mapping ratio (y=1 is the crossover)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "mapping-ratio.png"))
    plt.close()

    plt.figure()
    for transport in net.TRANSPORTS:
        for mapping in ("latency_ring", "bandwidth_ring"):
            plt.plot(scales, series(transport, mapping, "model_error"), marker="o", label=f"{transport} {mapping}")
    plt.xticks(scales)
    plt.xlabel("WAN latency scale λ")
    plt.ylabel("actual CCT / static prediction")
    plt.legend(fontsize=7)
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "model-error.png"))
    plt.close()

    plt.figure()
    for transport in net.TRANSPORTS:
        for mapping in ("latency_ring", "bandwidth_ring"):
            plt.plot(scales, series(transport, mapping, "max_wan_queue_bytes"), marker="o", label=f"{transport} {mapping}")
    plt.xticks(scales)
    plt.xlabel("WAN latency scale λ")
    plt.ylabel("max WAN queue (bytes)")
    plt.legend(fontsize=7)
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "wan-queue.png"))
    plt.close()

    plt.figure()
    for mapping in ("latency_ring", "bandwidth_ring"):
        plt.plot(scales, series("dcqcn", mapping, "dcqcn_mean_rate_bps"), marker="o", label=f"dcqcn mean {mapping}")
        plt.plot(scales, series("hpcc", mapping, "hpcc_mean_rate_bps"), marker="x", label=f"hpcc mean {mapping}")
    plt.xticks(scales)
    plt.xlabel("WAN latency scale λ")
    plt.ylabel("mean sender rate while busy (bit/s)")
    plt.legend(fontsize=7)
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "controller-rate.png"))
    plt.close()
    print(f"plots: {directory}")


def discover(raw):
    found = []
    if not os.path.isdir(raw):
        return found
    for name in sorted(os.listdir(raw)):
        run_dir = os.path.join(raw, name)
        if os.path.exists(os.path.join(run_dir, "meta.json")) and os.path.exists(os.path.join(run_dir, "fct.txt")):
            found.append(run_dir)
    return found


def edge_name(pair):
    left, right = sorted(pair)
    return f"{left}-{right}"


def check_edges(row):
    """Bytes only on the mapping's WAN edges; every required edge used."""
    problems = []
    used = {edge_name(pair) for pair in net.edge_pairs(row["_mapping"])}
    for left_site, right_site in net.WAN_ORDER:
        edge = f"{left_site}-{right_site}"
        nbytes = row["_edge_bytes"][edge]
        if edge in used and nbytes <= 0:
            problems.append(f"sent nothing on required edge {edge}")
        if edge not in used and nbytes != 0:
            problems.append(f"used unexpected WAN edge {edge} ({nbytes} bytes)")
    return problems


def check_persistent_paths(row):
    """One connection per directed rank pair; its path crosses the direct WAN edge."""
    problems = []
    conns = row["_conns"]
    pairs = {}
    for key, entry in conns.items():
        pairs.setdefault((entry["src"], entry["dst"]), []).append((key, entry))
    multi = [pair for pair, entries in pairs.items() if len(entries) != 1]
    if multi:
        problems.append(f"{len(multi)} rank pairs have more than one connection: {multi[:3]}")
    if conns and not all(len(e["sports"]) == 1 and len(e["qp_ids"]) == 1 for e in conns.values()):
        problems.append("a connection spans several source ports or QPs")
    wan = row["_wan"]
    mapping = row["_mapping"]
    for key, entry in conns.items():
        conn_id = key if isinstance(key, int) else 0
        path = row["_conn_paths"].get(conn_id, "")
        if not path:
            problems.append(f"connection {conn_id} ({entry['src']}->{entry['dst']}) has no recorded path")
            continue
        s1, s2 = net.site_of_node(mapping, entry["src"]), net.site_of_node(mapping, entry["dst"])
        hops = [h for h in path.split(">") if ":" in h]
        wan_hops = []
        for hop in hops:
            node, iface = hop.split(":")
            if iface.isdigit() and (int(node), int(iface)) in wan:
                wan_hops.append(wan[(int(node), int(iface))])
        if s1 == s2 and wan_hops:
            problems.append(f"intra-site connection {entry['src']}->{entry['dst']} crosses {wan_hops}")
        if s1 != s2 and wan_hops != [edge_name((s1, s2))]:
            problems.append(f"connection {entry['src']}->{entry['dst']} path {path} uses {wan_hops}, expected {edge_name((s1, s2))}")
    return problems


def base_checks(row, expect_reuse):
    problems = check_edges(row)
    if int(row["drop_count"]) != 0:
        problems.append(f"{row['drop_count']} packet drops")
    if row["valid"] != "true":
        problems.append("run did not finish cleanly")
    if row["transport_connection_mode"] != "PERSISTENT":
        problems.append("not a PERSISTENT run")
    problems += check_persistent_paths(row)
    conns = row["_conns"]
    if expect_reuse and conns:
        per = [e["messages"] for e in conns.values()]
        if min(per) < 2:
            problems.append("a connection carried fewer than 2 messages; QPs were not reused")
        if max(per) != EXPECTED_MESSAGES_PER_CONNECTION:
            problems.append(f"messages per connection {sorted(set(per))}, expected {EXPECTED_MESSAGES_PER_CONNECTION} on ring edges")
    return problems


def gate_fixed(run_dir):
    row = summarize(run_dir)
    print_run(row)
    problems = base_checks(row, expect_reuse=True)
    trace = row["_cca_trace"]
    adaptive = [r for r in trace if r["event"] in ("RATE_DECREASE", "RATE_INCREASE", "HPCC_UPDATE", "HPCC_FAST_REACT")]
    if adaptive:
        problems.append(f"fixed-rate run has {len(adaptive)} rate-adaptation events")
    starts = [r for r in trace if r["event"] == "MESSAGE_START"]
    if starts and any(r["rate_bps"] != LINE_RATE_BPS for r in starts):
        problems.append("fixed-rate: a message started below line rate")
    if row["_meta"].get("enable_pfc") != "0" or int(row["pfc_pause_count"]) != 0:
        problems.append("PFC frames were generated in a fixed-rate run")
    return finish_gate("smoke A (fixed-rate)", problems)


def gate_dcqcn(run_dir):
    row = summarize(run_dir)
    print_run(row)
    problems = base_checks(row, expect_reuse=True)
    trace = row["_cca_trace"]
    decreases = [r for r in trace if r["event"] == "RATE_DECREASE"]
    if not decreases:
        problems.append("DCQCN produced no RATE_DECREASE; no controller state evolution to study")
    carried = 0
    seen = set()
    for r in sorted((r for r in trace if r["event"] == "MESSAGE_START"), key=lambda r: r["time_ns"]):
        if r["qp_id"] in seen and r["rate_bps"] < LINE_RATE_BPS:
            carried += 1
        seen.add(r["qp_id"])
    if decreases and carried == 0:
        problems.append("no later message started below line rate although decreases occurred; state did not carry")
    if int(row["ecn_count"]) == 0 or int(row["cnp_count"]) == 0:
        problems.append(f"ECN/CNP implausible: ecn={row['ecn_count']} cnp={row['cnp_count']}")
    print(f"    carried-state message starts: {carried}; decreases: {len(decreases)}")
    return finish_gate("smoke B/C (DCQCN)", problems)


def gate_hpcc(run_dir):
    row = summarize(run_dir)
    print_run(row)
    problems = base_checks(row, expect_reuse=True)
    if row["_meta"].get("has_win") != "1":
        problems.append("HPCC run without HAS_WIN 1")
    trace = row["_cca_trace"]
    updates = [r for r in trace if r["event"] == "HPCC_UPDATE"]
    if not updates:
        problems.append("no HPCC_UPDATE events")
    floor_hits = [r for r in updates if r["hp_cur_rate_bps"] <= MIN_RATE_BPS]
    if floor_hits:
        problems.append(f"HPCC collapsed to MIN_RATE in {len(floor_hits)} updates (the HAS_WIN 0 failure mode)")
    if row["hpcc_mean_rate_bps"] != "" and int(row["hpcc_mean_rate_bps"]) < 10 * MIN_RATE_BPS:
        problems.append(f"HPCC mean rate {row['hpcc_mean_rate_bps']} is near the floor")
    return finish_gate("smoke D (HPCC)", problems)


def finish_gate(label, problems):
    if problems:
        print(f"{label} FAILED:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"{label}: passed")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=None)
    parser.add_argument("--one", default=None)
    parser.add_argument("--gate-fixed", default=None)
    parser.add_argument("--gate-dcqcn", default=None)
    parser.add_argument("--gate-hpcc", default=None)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    base = net.root()
    if args.one:
        return print_run(summarize(args.one))
    if args.gate_fixed:
        return gate_fixed(args.gate_fixed)
    if args.gate_dcqcn:
        return gate_dcqcn(args.gate_dcqcn)
    if args.gate_hpcc:
        return gate_hpcc(args.gate_hpcc)
    raw = args.raw or os.path.join(base, "results", "raw")
    run_dirs = discover(raw)
    rows = [summarize(run_dir) for run_dir in run_dirs]
    add_derived(rows)
    results = os.path.join(base, "results")
    os.makedirs(results, exist_ok=True)
    write_csv(os.path.join(results, "summary.csv"), rows)
    write_edges(os.path.join(results, "wan_edges.csv"), rows)
    write_paths(os.path.join(results, "paths.csv"), rows)
    write_manifest(os.path.join(results, "manifest.csv"), run_dirs)
    print_ratio_table(rows)
    invalid = [row["run_id"] for row in rows if row["valid"] != "true"]
    if invalid:
        print("INVALID runs (drops or incomplete), excluded from the ranking: " + ", ".join(invalid), file=sys.stderr)
    if not args.no_plot and rows:
        plot(rows, os.path.join(results, "plots"))
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
