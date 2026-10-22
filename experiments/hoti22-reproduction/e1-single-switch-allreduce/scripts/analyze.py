#!/usr/bin/env python3
"""Summarize Experiment 1 AllReduce runs. Plotting is optional."""

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

CSV_FIELDS = [
    "run",
    "flows",
    "collective_completion_ns",
    "max_queue_bytes",
    "pfc_pause_events",
    "pfc_resume_events",
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
            sip = int(parts[0], 16)
            dip = int(parts[1], 16)
            flows.append(
                {
                    "src": (sip >> 8) & 0xFFFF,
                    "dst": (dip >> 8) & 0xFFFF,
                    "bytes": int(parts[4]),
                    "start_ns": int(parts[5]),
                    "fct_ns": int(parts[6]),
                }
            )
    return flows


def parse_qlen(path):
    samples = []
    with open(path) as handle:
        for line in handle:
            tokens = line.split()
            if len(tokens) < 3 or tokens[0] != "time":
                continue
            time_ns = int(tokens[1])
            ports = {}
            index = 3
            while index + 2 < len(tokens) and tokens[index] == "j":
                ports[int(tokens[index + 1])] = int(tokens[index + 2])
                index += 3
            samples.append((time_ns, ports))
    return samples


def parse_pfc(path):
    pauses = 0
    resumes = 0
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pauses, resumes
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) != 5:
                continue
            kind = int(parts[4])
            if kind == 1:
                pauses += 1
            elif kind == 0:
                resumes += 1
    return pauses, resumes


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
        raise SystemExit(
            f"{path}: trace size {len(blob)} is not a whole number of "
            f"{TRACE_RECORD_BYTES}-byte records after a {nports}-port header"
        )
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


def queue_series(samples):
    peak = 0
    peak_port = None
    for _, ports in samples:
        for port, nbytes in ports.items():
            if nbytes > peak:
                peak = nbytes
                peak_port = port
    if peak_port is None:
        return None, []
    return peak_port, [(time_ns, ports.get(peak_port, 0)) for time_ns, ports in samples]


def summarize(run_dir):
    flows = parse_fct(os.path.join(run_dir, "fct.txt"))
    samples = parse_qlen(os.path.join(run_dir, "qlen.txt"))
    pauses, resumes = parse_pfc(os.path.join(run_dir, "pfc.txt"))
    trace = parse_trace(os.path.join(run_dir, "mix.tr"))
    peak = 0
    for _, ports in samples:
        if ports:
            peak = max(peak, max(ports.values()))
    ends = [flow["start_ns"] + flow["fct_ns"] for flow in flows]
    summary = {
        "run": os.path.basename(run_dir.rstrip("/")),
        "flows": len(flows),
        "collective_completion_ns": max(ends) if ends else None,
        "max_queue_bytes": peak,
        "pfc_pause_events": pauses,
        "pfc_resume_events": resumes,
        "ecn_marked_dequeues": trace["ecn_marked_dequeues"],
        "cnp_dequeues": trace["cnp_dequeues"],
        "drops": trace["drops"],
        "sender_rate": "not recorded; RdmaHw PRINT_LOG is 0",
    }
    return summary, samples


def write_summary(run_dir, summary):
    with open(os.path.join(run_dir, "summary.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    with open(os.path.join(run_dir, "summary.txt"), "w") as handle:
        handle.write(f"flows: {summary['flows']}\n")
        handle.write(
            f"collective completion: {summary['collective_completion_ns']} ns\n"
        )
        handle.write(f"max queue: {summary['max_queue_bytes']} bytes\n")
        handle.write(
            f"PFC pause/resume: {summary['pfc_pause_events']}/"
            f"{summary['pfc_resume_events']}\n"
        )
        handle.write(f"ECN-marked switch dequeues: {summary['ecn_marked_dequeues']}\n")
        handle.write(f"CNP switch dequeues: {summary['cnp_dequeues']}\n")
        handle.write(f"drops: {summary['drops']}\n")
        handle.write(f"sender rate: {summary['sender_rate']}\n")


def write_csv(path, summaries):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for summary in summaries:
            writer.writerow({field: summary[field] for field in CSV_FIELDS})


def plot(runs, out_path):
    try:
        import matplotlib
    except ImportError:
        print("matplotlib is not installed; skipping plot")
        return
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for label, samples in runs:
        port, series = queue_series(samples)
        if not series:
            continue
        ax.plot(
            [time_ns / 1e3 for time_ns, _ in series],
            [nbytes / 1e6 for _, nbytes in series],
            label=f"{label} port {port}",
        )
    ax.set_xlabel("time (us)")
    ax.set_ylabel("queue occupancy (MB)")
    ax.set_title("Experiment 1 AllReduce, busiest switch port")
    ax.legend()
    ax.grid(True, alpha=0.3)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"plot: {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()

    summaries = []
    series = []
    for run_dir in args.runs:
        summary, samples = summarize(run_dir)
        write_summary(run_dir, summary)
        summaries.append(summary)
        series.append((summary["run"], samples))
        print(
            f"{summary['run']}: collective={summary['collective_completion_ns']} ns "
            f"max_queue={summary['max_queue_bytes']} B "
            f"pfc_pause={summary['pfc_pause_events']} "
            f"ecn={summary['ecn_marked_dequeues']} cnp={summary['cnp_dequeues']} "
            f"drops={summary['drops']} flows={summary['flows']}"
        )

    parent = os.path.dirname(os.path.abspath(args.runs[0].rstrip("/")))
    csv_path = args.csv or os.path.join(parent, "summary.csv")
    write_csv(csv_path, summaries)
    print(f"csv: {csv_path}")
    if not args.no_plot:
        plot(series, os.path.join(parent, "plots", "e1_queue_occupancy.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
