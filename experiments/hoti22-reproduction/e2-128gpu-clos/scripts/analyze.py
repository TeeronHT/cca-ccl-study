#!/usr/bin/env python3
"""Summarize Experiment 2. Plotting is optional."""

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
TOR0 = 128
N_TOR = 8
SPINE0 = TOR0 + N_TOR

CSV_FIELDS = [
    "layout",
    "cca",
    "flows",
    "collective_completion_ns",
    "max_tor_queue_bytes",
    "max_spine_queue_bytes",
    "pfc_pause_events",
    "ecn_marked_dequeues",
    "cnp_dequeues",
    "drops",
    "sender_rate",
]


def parse_fct(path):
    flows = []
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 8:
                continue
            flows.append({"start_ns": int(parts[5]), "fct_ns": int(parts[6])})
    return flows


def parse_qlen(path):
    tor_peak = 0
    spine_peak = 0
    samples = []
    with open(path) as handle:
        for line in handle:
            tokens = line.split()
            if len(tokens) < 3 or tokens[0] != "time":
                continue
            switch = int(tokens[2])
            ports = {}
            index = 3
            while index + 2 < len(tokens) and tokens[index] == "j":
                ports[int(tokens[index + 1])] = int(tokens[index + 2])
                index += 3
            if not ports:
                continue
            peak = max(ports.values())
            if TOR0 <= switch < SPINE0:
                tor_peak = max(tor_peak, peak)
            elif switch >= SPINE0:
                spine_peak = max(spine_peak, peak)
            samples.append((int(tokens[1]), switch, ports))
    return tor_peak, spine_peak, samples


def parse_pfc(path):
    pauses = 0
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pauses
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) == 5 and int(parts[4]) == 1:
                pauses += 1
    return pauses


def parse_trace(path):
    stats = {"ecn_marked_dequeues": 0, "cnp_dequeues": 0, "drops": 0}
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return stats
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < 8:
        return stats
    (nports,) = struct.unpack_from("<I", blob, 0)
    offset = 4 + nports * 11 + 4
    if offset > len(blob) or (len(blob) - offset) % TRACE_RECORD_BYTES != 0:
        raise SystemExit(f"{path}: trace record size is not {TRACE_RECORD_BYTES}")
    while offset + TRACE_RECORD_BYTES <= len(blob):
        node_type = blob[offset + 29]
        event = blob[offset + 27]
        l3 = blob[offset + 26]
        ecn = blob[offset + 28]
        flags = struct.unpack_from("<H", blob, offset + 36)[0]
        if event == EVENT_DROP:
            stats["drops"] += 1
        if node_type == 1 and event == EVENT_DEQUEUE:
            if l3 == L3_UDP and ecn == ECN_CE:
                stats["ecn_marked_dequeues"] += 1
            if l3 in (L3_ACK, L3_NACK) and flags & CNP_FLAG:
                stats["cnp_dequeues"] += 1
        offset += TRACE_RECORD_BYTES
    return stats


def summarize(run_dir, layout, cca):
    flows = parse_fct(os.path.join(run_dir, "fct.txt"))
    tor_peak, spine_peak, samples = parse_qlen(os.path.join(run_dir, "qlen.txt"))
    trace = parse_trace(os.path.join(run_dir, "mix.tr"))
    ends = [flow["start_ns"] + flow["fct_ns"] for flow in flows]
    summary = {
        "layout": layout,
        "cca": cca,
        "flows": len(flows),
        "collective_completion_ns": max(ends) if ends else None,
        "max_tor_queue_bytes": tor_peak,
        "max_spine_queue_bytes": spine_peak,
        "pfc_pause_events": parse_pfc(os.path.join(run_dir, "pfc.txt")),
        "ecn_marked_dequeues": trace["ecn_marked_dequeues"],
        "cnp_dequeues": trace["cnp_dequeues"],
        "drops": trace["drops"],
        "sender_rate": "not recorded; RdmaHw PRINT_LOG is 0",
    }
    with open(os.path.join(run_dir, "summary.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    return summary, samples


def write_csv(path, summaries):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for summary in summaries:
            writer.writerow({field: summary[field] for field in CSV_FIELDS})


def plot(rows, out_path):
    try:
        import matplotlib
    except ImportError:
        print("matplotlib is not installed; skipping plot")
        return
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for label, samples in rows:
        best = 0
        best_key = None
        for _, switch, ports in samples:
            for port, nbytes in ports.items():
                if nbytes > best:
                    best = nbytes
                    best_key = (switch, port)
        if best_key is None:
            continue
        switch, port = best_key
        xs = []
        ys = []
        for time_ns, sw, ports in samples:
            if sw == switch:
                xs.append(time_ns / 1e3)
                ys.append(ports.get(port, 0) / 1e6)
        ax.plot(xs, ys, label=f"{label} sw{switch} p{port}")
    ax.set_xlabel("time (us)")
    ax.set_ylabel("queue occupancy (MB)")
    ax.set_title("Experiment 2, busiest switch port")
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"plot: {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", help="layout:cca:directory")
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()

    summaries = []
    series = []
    for item in args.runs:
        layout, cca, run_dir = item.split(":", 2)
        summary, samples = summarize(run_dir, layout, cca)
        summaries.append(summary)
        series.append((f"{layout}/{cca}", samples))
        print(
            f"{layout}/{cca}: collective={summary['collective_completion_ns']} ns "
            f"tor={summary['max_tor_queue_bytes']} spine={summary['max_spine_queue_bytes']} "
            f"pfc={summary['pfc_pause_events']} ecn={summary['ecn_marked_dequeues']} "
            f"cnp={summary['cnp_dequeues']} drops={summary['drops']} flows={summary['flows']}"
        )
    parent = os.path.dirname(os.path.abspath(args.runs[0].split(":", 2)[2].rstrip("/")))
    # results/<layout>/<cca> -> results/
    parent = os.path.dirname(parent)
    csv_path = args.csv or os.path.join(parent, "summary.csv")
    write_csv(csv_path, summaries)
    print(f"csv: {csv_path}")
    if not args.no_plot:
        plot(series, os.path.join(parent, "plots", "e2_queue_occupancy.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
