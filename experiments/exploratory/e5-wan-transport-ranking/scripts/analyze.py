#!/usr/bin/env python3
"""Summarize E5. Plotting is optional and is skipped when matplotlib is absent."""

import argparse
import csv
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net

TRACE_RECORD_BYTES = 56
EVENT_DEQUEUE = 2
EVENT_DROP = 3
L3_UDP = 0x11
L3_ACK = 0xFC
L3_NACK = 0xFD
ECN_CE = 0x03
CNP_FLAG = 1

CSV_FIELDS = [
    "run_id",
    "latency_scale",
    "mapping",
    "cca",
    "tensor_bytes",
    "ring_chunk_bytes",
    "collective_ns",
    "predicted_collective_ns",
    "model_error",
    "max_queue_bytes",
    "max_wan_queue_bytes",
    "max_border_queue_bytes",
    "pfc_pause_count",
    "ecn_count",
    "cnp_count",
    "drop_count",
    "flow_count",
    "latency_ring_to_bandwidth_ring_ratio",
    "static_ratio",
    "static_winner",
    "actual_winner",
    "ranking_agrees",
    "representative_avg_sender_rate",
    "representative_min_sender_rate",
    "representative_rate_decrease_count",
    "representative_fraction_below_50pct",
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
    names = {f"{left}-{right}": 0 for left, right in net.WAN_ORDER}
    if not os.path.exists(path):
        return max_any, max_wan, max_border, names
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
                index += 3
    return max_any, max_wan, max_border, names


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
    stats = {
        "drops": 0,
        "ecn": 0,
        "cnp": 0,
        "bytes": {f"{left}-{right}": 0 for left, right in net.WAN_ORDER},
        "ecn_edge": {f"{left}-{right}": 0 for left, right in net.WAN_ORDER},
    }
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return stats
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < 8:
        return stats
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
    max_any, max_wan, max_border, edge_queue = parse_qlen(
        os.path.join(run_dir, "qlen.txt"), wan, borders
    )
    pauses, edge_pfc = parse_pfc(os.path.join(run_dir, "pfc.txt"), wan)
    trace = parse_trace(os.path.join(run_dir, "mix.tr"), wan, borders)
    collective = max(ends) if ends else 0
    scale_name = meta["scale"]
    mapping = meta["mapping"]
    predicted = net.static_prediction(scale_name, mapping)["predicted_collective_ns"]
    error = (collective / predicted) if collective and predicted else ""
    row = {
        "run_id": meta["run_id"],
        "latency_scale": meta["latency_scale"],
        "mapping": mapping.replace("-", "_"),
        "cca": meta["cca"],
        "tensor_bytes": net.MESSAGE_BYTES,
        "ring_chunk_bytes": net.CHUNK_BYTES,
        "collective_ns": collective,
        "predicted_collective_ns": predicted,
        "model_error": f"{error:.6f}" if error != "" else "",
        "max_queue_bytes": max_any,
        "max_wan_queue_bytes": max_wan,
        "max_border_queue_bytes": max_border,
        "pfc_pause_count": pauses,
        "ecn_count": trace["ecn"],
        "cnp_count": trace["cnp"],
        "drop_count": trace["drops"],
        "flow_count": len(ends),
        "latency_ring_to_bandwidth_ring_ratio": "",
        "static_ratio": "",
        "static_winner": "",
        "actual_winner": "",
        "ranking_agrees": "",
        "representative_avg_sender_rate": "",
        "representative_min_sender_rate": "",
        "representative_rate_decrease_count": "",
        "representative_fraction_below_50pct": "",
        "study_commit": meta.get("study_commit", ""),
        "astra_commit": meta.get("astra_commit", ""),
        "ns3_commit": meta.get("ns3_commit", ""),
        "seed": meta.get("seed", ""),
        "_scale_name": scale_name,
        "_mapping": mapping,
        "_edge_bytes": trace["bytes"],
        "_edge_ecn": trace["ecn_edge"],
        "_edge_queue": edge_queue,
        "_edge_pfc": edge_pfc,
    }
    return row


def lookup(rows, scale, cca, mapping):
    for row in rows:
        if (
            float(row["latency_scale"]) == float(scale)
            and row["cca"] == cca
            and row["mapping"] == mapping
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
        latency = lookup(rows, scale, row["cca"], "latency_ring")
        bandwidth = lookup(rows, scale, row["cca"], "bandwidth_ring")
        static_ratio = static[scale]["latency_ring"] / static[scale]["bandwidth_ring"]
        row["static_ratio"] = f"{static_ratio:.6f}"
        row["static_winner"] = winner(static_ratio)
        if latency and bandwidth:
            actual = int(latency["collective_ns"]) / int(bandwidth["collective_ns"])
            row["latency_ring_to_bandwidth_ring_ratio"] = f"{actual:.6f}"
            row["actual_winner"] = winner(actual)
            row["ranking_agrees"] = str(row["static_winner"] == row["actual_winner"]).lower()


def print_run(row):
    scale = row["latency_scale"]
    text = (
        f"wan{scale} {row['_mapping']} {row['cca']}:\n"
        f"    predicted={row['predicted_collective_ns']}\n"
        f"    actual={row['collective_ns']}\n"
        f"    max_wan_queue={row['max_wan_queue_bytes']}\n"
        f"    pfc={row['pfc_pause_count']}\n"
        f"    ecn={row['ecn_count']}\n"
        f"    cnp={row['cnp_count']}\n"
        f"    drops={row['drop_count']}\n"
        f"    flows={row['flow_count']}"
    )
    print(text)
    if int(row["drop_count"]) > 0:
        print(
            f"INVALID: {row['run_id']} dropped packets. "
            "Not interpreting this CCT and not changing buffers.",
            file=sys.stderr,
        )
        return 1
    return 0


def print_ratio_table(rows):
    print("\nmapping ratio CCT_latency_ring / CCT_bandwidth_ring")
    print(f"{'':<8}{'λ=0.5':>12}{'λ=1':>12}{'λ=2':>12}")
    print(f"{'static':<8}", end="")
    for scale in (0.5, 1.0, 2.0):
        sample = next((row for row in rows if float(row["latency_scale"]) == scale), None)
        cell = sample["static_ratio"] if sample else ""
        print(f"{cell:>12}", end="")
    print()
    for cca in ("pfc", "dcqcn", "hpcc"):
        print(f"{cca:<8}", end="")
        for scale in (0.5, 1.0, 2.0):
            sample = lookup(rows, scale, cca, "latency_ring")
            cell = sample["latency_ring_to_bandwidth_ring_ratio"] if sample else ""
            print(f"{cell:>12}", end="")
        print()
    print("\ncrossover against y=1, using a 1% indifference band")
    for cca in ("pfc", "dcqcn", "hpcc"):
        points = []
        for scale in (0.5, 1.0, 2.0):
            sample = lookup(rows, scale, cca, "latency_ring")
            if not sample or sample["actual_winner"] == "":
                points.append(None)
            else:
                points.append(sample["actual_winner"])
        print(f"  {cca}: {describe_crossover(points)}")


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


def write_manifest(path, run_dirs):
    fields = [
        "run_id",
        "study_commit",
        "astra_commit",
        "ns3_commit",
        "topology_sha256",
        "logical_sha256",
        "system_sha256",
        "cca_sha256",
        "workload_sha256",
        "route",
        "seed",
        "hostname",
        "start",
        "end",
    ]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for run_dir in run_dirs:
            meta = json.load(open(os.path.join(run_dir, "meta.json")))
            writer.writerow(meta)


def plot(rows, directory):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed; skipping plots")
        return
    os.makedirs(directory, exist_ok=True)
    scales = [0.5, 1.0, 2.0]

    def series(cca, mapping, key):
        values = []
        for scale in scales:
            match = lookup(rows, scale, cca, mapping)
            values.append(float(match[key]) if match and match[key] != "" else None)
        return values

    for cca in ("pfc", "dcqcn", "hpcc"):
        plt.figure()
        for mapping, label in (("latency_ring", "latency_ring"), ("bandwidth_ring", "bandwidth_ring")):
            plt.plot(scales, series(cca, mapping, "collective_ns"), marker="o", label=label)
        plt.xticks(scales)
        plt.xlabel("WAN latency scale")
        plt.ylabel("collective completion time (ns)")
        plt.legend()
        plt.tight_layout()
        plt.savefig(os.path.join(directory, f"cct-{cca}.png"))
        plt.close()

    plt.figure()
    for cca in ("pfc", "dcqcn", "hpcc"):
        plt.plot(scales, series(cca, "latency_ring", "latency_ring_to_bandwidth_ring_ratio"), marker="o", label=cca)
    static_line = []
    for scale in scales:
        sample = next((row for row in rows if float(row["latency_scale"]) == scale), None)
        static_line.append(float(sample["static_ratio"]) if sample else None)
    plt.plot(scales, static_line, marker="o", linestyle="--", label="static")
    plt.axhline(1.0)
    plt.xticks(scales)
    plt.xlabel("WAN latency scale")
    plt.ylabel("CCT latency ring / CCT bandwidth ring")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "mapping-ratio.png"))
    plt.close()

    plt.figure()
    for cca in ("pfc", "dcqcn", "hpcc"):
        for mapping in ("latency_ring", "bandwidth_ring"):
            plt.plot(scales, series(cca, mapping, "model_error"), marker="o", label=f"{cca} {mapping}")
    plt.xticks(scales)
    plt.xlabel("WAN latency scale")
    plt.ylabel("actual CCT / static prediction")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "model-error.png"))
    plt.close()

    plt.figure()
    for cca in ("pfc", "dcqcn", "hpcc"):
        for mapping in ("latency_ring", "bandwidth_ring"):
            plt.plot(
                scales,
                series(cca, mapping, "max_wan_queue_bytes"),
                marker="o",
                label=f"{cca} {mapping}",
            )
    plt.xticks(scales)
    plt.xlabel("WAN latency scale")
    plt.ylabel("max WAN queue (bytes)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "wan-queue.png"))
    plt.close()


def discover(raw):
    found = []
    if not os.path.isdir(raw):
        return found
    for name in sorted(os.listdir(raw)):
        run_dir = os.path.join(raw, name)
        if os.path.exists(os.path.join(run_dir, "meta.json")) and os.path.exists(
            os.path.join(run_dir, "fct.txt")
        ):
            found.append(run_dir)
    return found


def edge_name(pair):
    left, right = sorted(pair)
    return f"{left}-{right}"


def gate_pair(left_dir, right_dir):
    left = summarize(left_dir)
    right = summarize(right_dir)
    status = print_run(left)
    status = print_run(right) or status
    if status:
        return status
    if int(left["flow_count"]) != int(right["flow_count"]) or int(left["flow_count"]) == 0:
        print("smoke runs do not have equivalent flow counts", file=sys.stderr)
        return 1
    for row in (left, right):
        used = {edge_name(pair) for pair in net.edge_pairs(row["_mapping"])}
        for left_site, right_site in net.WAN_ORDER:
            edge = f"{left_site}-{right_site}"
            nbytes = row["_edge_bytes"][edge]
            if edge in used and nbytes <= 0:
                print(f"{row['run_id']} sent nothing on required edge {edge}", file=sys.stderr)
                return 1
            if edge not in used and nbytes != 0:
                print(f"{row['run_id']} used unexpected WAN edge {edge}", file=sys.stderr)
                return 1
    if left["cca"] == "dcqcn":
        ratio = int(left["collective_ns"]) / int(right["collective_ns"])
        if left["_mapping"] != "latency-ring":
            ratio = 1 / ratio
        if ratio < 0.9 or ratio > 1.1:
            print(
                "λ=1 DCQCN mapping ratio is outside 0.9–1.1 while the static model "
                "predicts a tie. Queue traces in these run directories are kept."
            )
    print("smoke pair: no drops, matching flow counts, WAN edges match the mapping")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=None)
    parser.add_argument("--one", default=None)
    parser.add_argument("--gate-pair", nargs=2, default=None)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    base = net.root()
    if args.one:
        return print_run(summarize(args.one))
    if args.gate_pair:
        return gate_pair(*args.gate_pair)
    raw = args.raw or os.path.join(base, "results", "raw")
    run_dirs = discover(raw)
    rows = [summarize(run_dir) for run_dir in run_dirs]
    add_derived(rows)
    results = os.path.join(base, "results")
    write_csv(os.path.join(results, "summary.csv"), rows)
    write_edges(os.path.join(results, "wan_edges.csv"), rows)
    write_manifest(os.path.join(results, "manifest.csv"), run_dirs)
    print_ratio_table(rows)
    invalid = [row["run_id"] for row in rows if int(row["drop_count"]) > 0]
    if invalid:
        print("INVALID runs with drops: " + ", ".join(invalid), file=sys.stderr)
    if not args.no_plot and rows:
        plot(rows, os.path.join(results, "plots"))
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
