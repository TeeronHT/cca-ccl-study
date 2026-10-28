#!/usr/bin/env python3
"""Summarize E4. Plotting is optional and is skipped when matplotlib is absent."""

import argparse
import csv
import json
import os
import struct
import sys

TRACE_RECORD_BYTES = 56
EVENT_DEQUEUE = 2
EVENT_DROP = 3
L3_UDP = 0x11
L3_ACK = 0xFC
L3_NACK = 0xFD
ECN_CE = 0x03
CNP_FLAG = 1
MESSAGE_BYTES = 64 * 1024 * 1024

CSV_FIELDS = [
    "run_id",
    "topology",
    "oversubscription",
    "cca",
    "chunk_count",
    "chunk_bytes",
    "total_application_bytes",
    "collective_ns",
    "effective_bandwidth_gbps",
    "max_queue_bytes",
    "max_tor_uplink_queue_bytes",
    "max_spine_queue_bytes",
    "pfc_pause_count",
    "ecn_count",
    "cnp_count",
    "drop_count",
    "flow_count",
    "median_flow_bytes",
    "congestion_slowdown",
    "cca_penalty_vs_pfc",
    "granularity_penalty",
    "rate_decrease_events",
    "rate_increase_events",
    "min_sender_rate",
    "fraction_time_rate_below_50pct",
    "study_commit",
    "astra_commit",
    "ns3_commit",
    "seed",
]


def load_ports(run_dir):
    meta = json.load(open(os.path.join(run_dir, "meta.json")))
    ports = json.load(open(meta["ports_file"]))
    uplinks = {int(node): set(indexes) for node, indexes in ports["tor_uplinks"].items()}
    spines = {int(node) for node in ports["spine_ports"]}
    tors = {int(node) for node in ports["tor_uplinks"]}
    return meta, uplinks, tors, spines


def parse_fct(path):
    ends = []
    sizes = []
    if not os.path.exists(path):
        return ends, sizes
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 8:
                continue
            sizes.append(int(parts[4]))
            ends.append(int(parts[5]) + int(parts[6]))
    return ends, sizes


def parse_qlen(path, uplinks, spines):
    max_any = 0
    max_up = 0
    max_spine = 0
    if not os.path.exists(path):
        return max_any, max_up, max_spine
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
                if switch in uplinks and port in uplinks[switch]:
                    max_up = max(max_up, nbytes)
                if switch in spines:
                    max_spine = max(max_spine, nbytes)
                index += 3
    return max_any, max_up, max_spine


def parse_pfc(path):
    count = 0
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return count
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) == 5 and int(parts[4]) == 1:
                count += 1
    return count


def parse_trace(path, uplinks, tors, spines):
    stats = {"drops": 0, "ecn": 0, "cnp": 0}
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
        event = blob[offset + 27]
        l3 = blob[offset + 26]
        ecn = blob[offset + 28]
        flags = struct.unpack_from("<H", blob, offset + 36)[0]
        if event == EVENT_DROP:
            stats["drops"] += 1
        if node in tors or node in spines:
            if l3 == L3_UDP and ecn == ECN_CE and event == EVENT_DEQUEUE:
                stats["ecn"] += 1
            if l3 in (L3_ACK, L3_NACK) and flags & CNP_FLAG and event == EVENT_DEQUEUE:
                stats["cnp"] += 1
        offset += TRACE_RECORD_BYTES
    return stats


def summarize(run_dir):
    meta, uplinks, tors, spines = load_ports(run_dir)
    ends, sizes = parse_fct(os.path.join(run_dir, "fct.txt"))
    max_any, max_up, max_spine = parse_qlen(os.path.join(run_dir, "qlen.txt"), uplinks, spines)
    trace = parse_trace(os.path.join(run_dir, "mix.tr"), uplinks, tors, spines)
    collective = max(ends) if ends else 0
    chunk_count = int(meta["chunk_count"])
    ordered = sorted(sizes)
    median = ordered[len(ordered) // 2] if ordered else 0
    bandwidth = (MESSAGE_BYTES * 8 / collective) if collective else 0
    return {
        "run_id": meta["run_id"],
        "topology": meta["topology"],
        "oversubscription": meta["oversubscription"],
        "cca": meta["cca"],
        "chunk_count": chunk_count,
        "chunk_bytes": MESSAGE_BYTES // chunk_count,
        "total_application_bytes": MESSAGE_BYTES,
        "collective_ns": collective,
        "effective_bandwidth_gbps": f"{bandwidth:.6f}",
        "max_queue_bytes": max_any,
        "max_tor_uplink_queue_bytes": max_up,
        "max_spine_queue_bytes": max_spine,
        "pfc_pause_count": parse_pfc(os.path.join(run_dir, "pfc.txt")),
        "ecn_count": trace["ecn"],
        "cnp_count": trace["cnp"],
        "drop_count": trace["drops"],
        "flow_count": len(sizes),
        "median_flow_bytes": median,
        "congestion_slowdown": "",
        "cca_penalty_vs_pfc": "",
        "granularity_penalty": "",
        "rate_decrease_events": "",
        "rate_increase_events": "",
        "min_sender_rate": "",
        "fraction_time_rate_below_50pct": "",
        "study_commit": meta.get("study_commit", ""),
        "astra_commit": meta.get("astra_commit", ""),
        "ns3_commit": meta.get("ns3_commit", ""),
        "seed": meta.get("seed", ""),
    }


def lookup(rows, topology, cca, chunk_count):
    for row in rows:
        if (
            row["topology"] == topology
            and row["cca"] == cca
            and int(row["chunk_count"]) == chunk_count
            and int(row["collective_ns"])
        ):
            return row
    return None


def ratio(numerator, denominator):
    if not denominator or not int(denominator["collective_ns"]):
        return ""
    return f"{int(numerator['collective_ns']) / int(denominator['collective_ns']):.6f}"


def add_derived(rows):
    for row in rows:
        pfc = lookup(rows, row["topology"], "pfc", int(row["chunk_count"]))
        base = lookup(rows, row["topology"], row["cca"], 1)
        control = lookup(rows, "1to1", row["cca"], int(row["chunk_count"]))
        if pfc:
            row["cca_penalty_vs_pfc"] = ratio(row, pfc)
        if base:
            row["granularity_penalty"] = ratio(row, base)
        if row["topology"] == "2to1" and control:
            row["congestion_slowdown"] = ratio(row, control)


def write_csv(path, rows):
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_manifest(path, run_dirs):
    fields = [
        "run_id",
        "study_commit",
        "astra_commit",
        "ns3_commit",
        "topology_sha256",
        "system_sha256",
        "logical_sha256",
        "cca_sha256",
        "workload_sha256",
        "chunking_mechanism",
        "qp_state_persists",
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


def print_run(row):
    print(
        f"{row['topology']} k{row['chunk_count']} {row['cca']}:\n"
        f"    total={row['collective_ns']}\n"
        f"    effective_bw={row['effective_bandwidth_gbps']}\n"
        f"    max_tor_queue={row['max_tor_uplink_queue_bytes']}\n"
        f"    pfc={row['pfc_pause_count']}\n"
        f"    ecn={row['ecn_count']}\n"
        f"    cnp={row['cnp_count']}\n"
        f"    drops={row['drop_count']}\n"
        f"    phases={row['chunk_count']}"
    )


def print_table(title, rows, value_key, ccas):
    chunks = [1, 4, 16, 64]
    print(title)
    header = " " * 8 + "".join(f"{('K=' + str(count)):>12}" for count in chunks)
    print(header)
    for cca in ccas:
        cells = []
        for count in chunks:
            match = [
                row[value_key]
                for row in rows
                if row["cca"] == cca and int(row["chunk_count"]) == count and row[value_key] != ""
            ]
            cells.append(f"{match[0]:>12}" if match else f"{'':>12}")
        print(f"{cca:<8}{''.join(cells)}")


def print_results(rows):
    for topology in ("1to1", "2to1"):
        selected = [row for row in rows if row["topology"] == topology]
        print_table(
            f"\nCCA penalty vs PFC on {topology} (CCT_cca / CCT_pfc)",
            selected,
            "cca_penalty_vs_pfc",
            ("dcqcn", "hpcc"),
        )
        print_table(
            f"\ngranularity penalty on {topology} (CCT_K / CCT_K1)",
            selected,
            "granularity_penalty",
            ("pfc", "dcqcn", "hpcc"),
        )
    slowdown = [row for row in rows if row["topology"] == "2to1"]
    print_table(
        "\ncongestion slowdown (CCT_2to1 / CCT_1to1)",
        slowdown,
        "congestion_slowdown",
        ("pfc", "dcqcn", "hpcc"),
    )


def plot(rows, directory):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed; skipping plots")
        return
    os.makedirs(directory, exist_ok=True)
    chunks = [1, 4, 16, 64]

    def series(topology, cca, key):
        values = []
        for count in chunks:
            match = lookup(rows, topology, cca, count)
            values.append(float(match[key]) if match and match[key] != "" else None)
        return values

    for topology in ("1to1", "2to1"):
        plt.figure()
        for cca in ("pfc", "dcqcn", "hpcc"):
            plt.plot(chunks, series(topology, cca, "collective_ns"), marker="o", label=cca)
        plt.xscale("log", base=2)
        plt.xticks(chunks, [str(count) for count in chunks])
        plt.xlabel("chunk count")
        plt.ylabel("total CCT (ns)")
        plt.legend()
        plt.tight_layout()
        plt.savefig(os.path.join(directory, f"cct-{topology}.png"))
        plt.close()

    plt.figure()
    for cca in ("pfc", "dcqcn", "hpcc"):
        plt.plot(chunks, series("2to1", cca, "congestion_slowdown"), marker="o", label=cca)
    plt.xscale("log", base=2)
    plt.xticks(chunks, [str(count) for count in chunks])
    plt.xlabel("chunk count")
    plt.ylabel("CCT 2:1 / CCT 1:1")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "congestion-slowdown.png"))
    plt.close()

    plt.figure()
    for cca in ("dcqcn", "hpcc"):
        plt.plot(chunks, series("2to1", cca, "cca_penalty_vs_pfc"), marker="o", label=cca)
    plt.xscale("log", base=2)
    plt.xticks(chunks, [str(count) for count in chunks])
    plt.xlabel("chunk count")
    plt.ylabel("CCT / CCT_PFC")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "cca-penalty-2to1.png"))
    plt.close()

    plt.figure()
    for cca in ("pfc", "dcqcn", "hpcc"):
        plt.plot(
            chunks, series("2to1", cca, "max_tor_uplink_queue_bytes"), marker="o", label=cca
        )
    plt.xscale("log", base=2)
    plt.xticks(chunks, [str(count) for count in chunks])
    plt.xlabel("chunk count")
    plt.ylabel("max ToR uplink queue (bytes)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(directory, "tor-uplink-queue-2to1.png"))
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


def gate_contention(run_dir):
    row = summarize(run_dir)
    print_run(row)
    contended = (
        int(row["max_tor_uplink_queue_bytes"]) > 0
        or int(row["pfc_pause_count"]) > 0
        or int(row["ecn_count"]) > 0
        or int(row["cnp_count"]) > 0
    )
    if not contended:
        print(
            "2:1 K=1 DCQCN produced no ToR-uplink queue, PFC, ECN, or CNP. "
            "Stopping. Do not add 4:1 from this script.",
            file=sys.stderr,
        )
        return 1
    print("contention smoke test: fabric feedback is present")
    return 0


def gate_granularity(coarse, fine):
    coarse_row = summarize(coarse)
    fine_row = summarize(fine)
    print_run(coarse_row)
    print_run(fine_row)
    if int(fine_row["flow_count"]) <= int(coarse_row["flow_count"]):
        print("K=64 did not create more flows than K=1. Stopping.", file=sys.stderr)
        return 1
    if int(fine_row["median_flow_bytes"]) >= int(coarse_row["median_flow_bytes"]):
        print(
            "K=1 and K=64 have the same message size, so the granularity knob "
            "did not change the traffic. Stopping.",
            file=sys.stderr,
        )
        return 1
    print("granularity smoke test: K=64 uses shorter messages than K=1")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=None)
    parser.add_argument("--one", default=None)
    parser.add_argument("--gate-contention", default=None)
    parser.add_argument("--gate-granularity", nargs=2, default=None)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if args.gate_contention:
        return gate_contention(args.gate_contention)
    if args.gate_granularity:
        return gate_granularity(*args.gate_granularity)
    if args.one:
        print_run(summarize(args.one))
        return 0
    raw = args.raw or os.path.join(base, "results", "raw")
    run_dirs = discover(raw)
    rows = [summarize(run_dir) for run_dir in run_dirs]
    add_derived(rows)
    results = os.path.join(base, "results")
    write_csv(os.path.join(results, "summary.csv"), rows)
    write_manifest(os.path.join(results, "manifest.csv"), run_dirs)
    print_results(rows)
    if not args.no_plot and rows:
        plot(rows, os.path.join(results, "plots"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
