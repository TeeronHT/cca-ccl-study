#!/usr/bin/env python3
"""Summarize E3. Plotting is optional. The primary number is CCT_1D / CCT_2D."""

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
LINK_BPS = 200 * 10**9
FABRIC_SPINES = {"1to1": 4, "2to1": 2, "4to1": 1}

CSV_FIELDS = [
    "run_id",
    "oversubscription",
    "spine_count",
    "collective",
    "cca",
    "message_bytes",
    "collective_ns",
    "max_queue_bytes",
    "max_tor_uplink_queue_bytes",
    "max_spine_queue_bytes",
    "p95_tor_uplink_queue_bytes",
    "pfc_pause_count",
    "pfc_pause_endpoint",
    "pfc_pause_tor",
    "pfc_pause_spine",
    "ecn_count",
    "ecn_tor",
    "ecn_spine",
    "cnp_count",
    "cnp_tor",
    "cnp_spine",
    "drop_count",
    "flow_count",
    "interrack_bytes",
    "static_interrack_payload_bytes",
    "static_lb_ns",
    "tor_uplink_util_avg",
    "tor_uplink_util_max",
    "hierarchy_speedup",
    "cca_sensitivity",
    "slowdown_vs_1to1",
    "astra_commit",
    "ns3_commit",
    "study_commit",
    "seed",
]


def load_ports(run_dir):
    meta_path = os.path.join(run_dir, "meta.json")
    meta = json.load(open(meta_path))
    ports = json.load(open(meta["ports_file"]))
    uplinks = {int(node): set(indexes) for node, indexes in ports["tor_uplinks"].items()}
    spines = {int(node) for node in ports["spine_ports"]}
    tors = {int(node) for node in ports["tor_uplinks"]}
    return meta, uplinks, tors, spines


def parse_fct(path):
    ends = []
    count = 0
    if not os.path.exists(path):
        return ends, count
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 8:
                continue
            ends.append(int(parts[5]) + int(parts[6]))
            count += 1
    return ends, count


def parse_qlen(path, uplinks, tors, spines):
    max_any = 0
    max_up = 0
    max_spine = 0
    uplink_samples = []
    if not os.path.exists(path):
        return max_any, max_up, max_spine, uplink_samples
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
                    uplink_samples.append(nbytes)
                if switch in spines:
                    max_spine = max(max_spine, nbytes)
                index += 3
    return max_any, max_up, max_spine, uplink_samples


def percentile(values, fraction):
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def parse_pfc(path, tors, spines):
    counts = {"all": 0, "endpoint": 0, "tor": 0, "spine": 0}
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return counts
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) != 5 or int(parts[4]) != 1:
                continue
            node = int(parts[1])
            counts["all"] += 1
            if node in tors:
                counts["tor"] += 1
            elif node in spines:
                counts["spine"] += 1
            else:
                counts["endpoint"] += 1
    return counts


def parse_trace(path, uplinks, tors, spines):
    stats = {
        "drops": 0,
        "ecn": 0,
        "ecn_tor": 0,
        "ecn_spine": 0,
        "cnp": 0,
        "cnp_tor": 0,
        "cnp_spine": 0,
        "interrack_bytes": 0,
        "uplink_bytes": {},
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
        if event == EVENT_DEQUEUE and node in uplinks and intf in uplinks[node]:
            stats["interrack_bytes"] += size
            stats["uplink_bytes"][(node, intf)] = stats["uplink_bytes"].get((node, intf), 0) + size
        if node in tors or node in spines:
            marked = l3 == L3_UDP and ecn == ECN_CE and event == EVENT_DEQUEUE
            cnp = l3 in (L3_ACK, L3_NACK) and flags & CNP_FLAG and event == EVENT_DEQUEUE
            if marked:
                stats["ecn"] += 1
                if node in tors:
                    stats["ecn_tor"] += 1
                else:
                    stats["ecn_spine"] += 1
            if cnp:
                stats["cnp"] += 1
                if node in tors:
                    stats["cnp_tor"] += 1
                else:
                    stats["cnp_spine"] += 1
        offset += TRACE_RECORD_BYTES
    return stats


def static_interrack_bytes(collective):
    """Payload that must leave a rack. Headers and ACKs are not included."""
    if collective == "1d":
        steps = 2 * (16 - 1)
        chunk = MESSAGE_BYTES // 16
        return 4 * steps * chunk
    steps = 2 * (4 - 1)
    chunk = (MESSAGE_BYTES // 4) // 4
    return 4 * 4 * steps * chunk


def summarize(run_dir):
    meta, uplinks, tors, spines = load_ports(run_dir)
    ends, flows = parse_fct(os.path.join(run_dir, "fct.txt"))
    max_any, max_up, max_spine, uplink_samples = parse_qlen(
        os.path.join(run_dir, "qlen.txt"), uplinks, tors, spines
    )
    pfc = parse_pfc(os.path.join(run_dir, "pfc.txt"), tors, spines)
    trace = parse_trace(os.path.join(run_dir, "mix.tr"), uplinks, tors, spines)
    collective_ns = max(ends) if ends else None
    n_uplinks = sum(len(ports) for ports in uplinks.values())
    util_avg = ""
    util_max = ""
    if collective_ns:
        capacity_bytes = LINK_BPS / 8 * (collective_ns * 1e-9)
        utils = [nbytes / capacity_bytes for nbytes in trace["uplink_bytes"].values()]
        # Uplinks that sent nothing are absent from the trace.
        utils.extend([0.0] * (n_uplinks - len(utils)))
        util_avg = sum(utils) / len(utils) if utils else 0
        util_max = max(utils) if utils else 0
    static_bytes = static_interrack_bytes(meta["collective"])
    static_lb = static_bytes * 8 / (n_uplinks * LINK_BPS) * 1e9
    summary = {
        "run_id": meta["run_id"],
        "oversubscription": meta["oversubscription"],
        "spine_count": meta["spine_count"],
        "collective": meta["collective"],
        "cca": meta["cca"],
        "message_bytes": MESSAGE_BYTES,
        "collective_ns": collective_ns if collective_ns is not None else "",
        "max_queue_bytes": max_any,
        "max_tor_uplink_queue_bytes": max_up,
        "max_spine_queue_bytes": max_spine,
        "p95_tor_uplink_queue_bytes": percentile(uplink_samples, 0.95),
        "pfc_pause_count": pfc["all"],
        "pfc_pause_endpoint": pfc["endpoint"],
        "pfc_pause_tor": pfc["tor"],
        "pfc_pause_spine": pfc["spine"],
        "ecn_count": trace["ecn"],
        "ecn_tor": trace["ecn_tor"],
        "ecn_spine": trace["ecn_spine"],
        "cnp_count": trace["cnp"],
        "cnp_tor": trace["cnp_tor"],
        "cnp_spine": trace["cnp_spine"],
        "drop_count": trace["drops"],
        "flow_count": flows,
        "interrack_bytes": trace["interrack_bytes"],
        "static_interrack_payload_bytes": static_bytes,
        "static_lb_ns": int(static_lb),
        "tor_uplink_util_avg": util_avg,
        "tor_uplink_util_max": util_max,
        "hierarchy_speedup": "",
        "cca_sensitivity": "",
        "slowdown_vs_1to1": "",
        "astra_commit": meta.get("astra_commit", ""),
        "ns3_commit": meta.get("ns3_commit", ""),
        "study_commit": meta.get("study_commit", ""),
        "seed": meta.get("seed", ""),
    }
    with open(os.path.join(run_dir, "summary.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    return summary


def add_derived(rows):
    by_key = {(row["oversubscription"], row["collective"], row["cca"]): row for row in rows}
    for row in rows:
        one_d = by_key.get((row["oversubscription"], "1d", row["cca"]))
        two_d = by_key.get((row["oversubscription"], "2d", row["cca"]))
        if one_d and two_d and one_d["collective_ns"] and two_d["collective_ns"]:
            row["hierarchy_speedup"] = float(one_d["collective_ns"]) / float(two_d["collective_ns"])
        same = [
            item["collective_ns"]
            for item in rows
            if item["oversubscription"] == row["oversubscription"]
            and item["collective"] == row["collective"]
            and item["collective_ns"] != ""
        ]
        if same:
            row["cca_sensitivity"] = max(map(float, same)) / min(map(float, same))
        base = by_key.get(("1:1", row["collective"], row["cca"]))
        if base and base["collective_ns"] and row["collective_ns"] != "":
            row["slowdown_vs_1to1"] = float(row["collective_ns"]) / float(base["collective_ns"])


def print_speedup(rows):
    print("hierarchy speedup = CCT_1D / CCT_2D")
    print(f"{'':8} {'PFC':>12} {'DCQCN':>12} {'HPCC':>12}")
    labels = {"1to1": "1:1", "2to1": "2:1", "4to1": "4:1"}
    for fabric in ("1to1", "2to1", "4to1"):
        cells = []
        for cca in ("pfc", "dcqcn", "hpcc"):
            match = [
                row["hierarchy_speedup"]
                for row in rows
                if row["run_id"] == f"{fabric}__1d__{cca}"
            ]
            value = match[0] if match else ""
            cells.append(f"{value:.3f}" if isinstance(value, float) else "")
        print(f"{labels[fabric]:8} {cells[0]:>12} {cells[1]:>12} {cells[2]:>12}")


def write_csv(path, rows):
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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
        "seed",
        "hostname",
        "start",
        "end",
    ]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for run_dir in run_dirs:
            meta = json.load(open(os.path.join(run_dir, "meta.json")))
            writer.writerow({field: meta.get(field, "") for field in fields})


def plot(rows, directory):
    try:
        import matplotlib
    except ImportError:
        print("matplotlib is not installed; skipping plots")
        return
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(directory, exist_ok=True)
    order = ["1:1", "2:1", "4:1"]
    os_of = {"1to1": "1:1", "2to1": "2:1", "4to1": "4:1"}
    for row in rows:
        row["_x"] = os_of[row["oversubscription"]]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for collective, cca, style in (
        ("1d", "pfc", "C0"),
        ("1d", "dcqcn", "C1"),
        ("1d", "hpcc", "C2"),
        ("2d", "pfc", "C0"),
        ("2d", "dcqcn", "C1"),
        ("2d", "hpcc", "C2"),
    ):
        series = [row for row in rows if row["collective"] == collective and row["cca"] == cca]
        series.sort(key=lambda row: order.index(row["_x"]))
        ax.plot(
            [row["_x"] for row in series],
            [float(row["collective_ns"]) / 1e6 for row in series if row["collective_ns"] != ""],
            marker="o",
            linestyle="-" if collective == "1d" else "--",
            color=style,
            label=f"{collective}/{cca}",
        )
    ax.set_ylabel("collective completion (ms)")
    ax.set_title("E3 completion time")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(directory, "cct.png"), dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for cca, color in (("pfc", "C0"), ("dcqcn", "C1"), ("hpcc", "C2")):
        series = []
        for fabric, label in (("1to1", "1:1"), ("2to1", "2:1"), ("4to1", "4:1")):
            match = next((row for row in rows if row["run_id"] == f"{fabric}__1d__{cca}"), None)
            if match is None or not isinstance(match["hierarchy_speedup"], float):
                continue
            series.append((label, match["hierarchy_speedup"]))
        if not series:
            continue
        ax.plot(
            [item[0] for item in series],
            [item[1] for item in series],
            marker="o",
            color=color,
            label=cca,
        )
    ax.axhline(1.0, color="black", linewidth=0.8)
    ax.set_ylabel("CCT_1D / CCT_2D")
    ax.set_title("E3 hierarchy speedup")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(directory, "hierarchy_speedup.png"), dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for collective, cca in (("1d", "pfc"), ("1d", "dcqcn"), ("1d", "hpcc"), ("2d", "pfc"), ("2d", "dcqcn"), ("2d", "hpcc")):
        series = [row for row in rows if row["collective"] == collective and row["cca"] == cca]
        series.sort(key=lambda row: order.index(row["_x"]))
        ax.plot(
            [row["_x"] for row in series],
            [row["max_tor_uplink_queue_bytes"] / 1e6 for row in series],
            marker="o",
            label=f"{collective}/{cca}",
        )
    ax.set_ylabel("max ToR uplink queue (MB)")
    ax.set_title("E3 ToR uplink queue")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(directory, "tor_uplink_queue.png"), dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    labels = [row["run_id"] for row in rows]
    ax.bar(range(len(rows)), [row["pfc_pause_count"] for row in rows], label="PFC")
    ax.plot(range(len(rows)), [row["ecn_count"] for row in rows], marker="o", label="ECN")
    ax.plot(range(len(rows)), [row["cnp_count"] for row in rows], marker="x", label="CNP")
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(labels, rotation=90, fontsize=7)
    ax.set_title("E3 congestion signals")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(directory, "signals.png"), dpi=120)
    plt.close(fig)
    print(f"plots: {directory}")


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


def print_run(row):
    print(
        f"{row['run_id']}: collective={row['collective_ns']} "
        f"tor_uplink_queue={row['max_tor_uplink_queue_bytes']} "
        f"spine_queue={row['max_spine_queue_bytes']} "
        f"pfc={row['pfc_pause_count']} ecn={row['ecn_count']} "
        f"cnp={row['cnp_count']} drops={row['drop_count']} "
        f"interrack_bytes={row['interrack_bytes']} flows={row['flow_count']}"
    )


def gate(run_dir):
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
            "4:1 2D DCQCN produced no ToR-uplink queue, PFC, ECN, or CNP. "
            "Not running the rest of the matrix.",
            file=sys.stderr,
        )
        return 1
    if not row["collective_ns"] or int(row["flow_count"]) == 0:
        print("smoke run has no completed flows", file=sys.stderr)
        return 1
    print("contention smoke test: fabric feedback is present")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=None)
    parser.add_argument("--gate", default=None, help="run directory that must show contention")
    parser.add_argument("--one", default=None, help="print one finished run")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    raw = args.raw or os.path.join(base, "results", "raw")
    if args.gate:
        return gate(args.gate)
    if args.one:
        print_run(summarize(args.one))
        return 0
    run_dirs = discover(raw)
    rows = [summarize(run_dir) for run_dir in run_dirs]
    add_derived(rows)
    results = os.path.join(base, "results")
    write_csv(os.path.join(results, "summary.csv"), rows)
    write_manifest(os.path.join(results, "manifest.csv"), run_dirs)
    print_speedup(rows)
    print(f"csv: {os.path.join(results, 'summary.csv')}")
    if not args.no_plot and rows:
        plot(rows, os.path.join(results, "plots"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
