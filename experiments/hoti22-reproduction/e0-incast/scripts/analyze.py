#!/usr/bin/env python3
"""Summarize Experiment 0 runs and optionally plot switch queue occupancy."""

import argparse
import csv
import json
import os
import struct
import sys

# TraceFormat is written by QbbHelper after a SimSetting header.
# Layout matches scratch's trace-format.h on this ABI: 56 bytes, ack.flags at 36.
TRACE_RECORD_BYTES = 56
EVENT_DEQUEUE = 2
EVENT_DROP = 3
L3_UDP = 0x11
L3_ACK = 0xFC
L3_NACK = 0xFD
ECN_CE = 0x03
CNP_FLAG = 1


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
                    "sport": int(parts[2]),
                    "dport": int(parts[3]),
                    "bytes": int(parts[4]),
                    "start_ns": int(parts[5]),
                    "fct_ns": int(parts[6]),
                    "standalone_fct_ns": int(parts[7]),
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


def _trace_records(path):
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < 8:
        return []
    (nports,) = struct.unpack_from("<I", blob, 0)
    offset = 4 + nports * 11 + 4
    if offset > len(blob) or (len(blob) - offset) % TRACE_RECORD_BYTES != 0:
        raise SystemExit(
            f"{path}: trace size {len(blob)} is not a whole number of "
            f"{TRACE_RECORD_BYTES}-byte records after a {nports}-port header"
        )
    records = []
    while offset + TRACE_RECORD_BYTES <= len(blob):
        node_type = blob[offset + 29]
        event = blob[offset + 27]
        l3 = blob[offset + 26]
        ecn = blob[offset + 28]
        flags = struct.unpack_from("<H", blob, offset + 36)[0]
        records.append((node_type, event, l3, ecn, flags))
        offset += TRACE_RECORD_BYTES
    return records


def parse_trace(path):
    stats = {"ecn_marked_dequeues": 0, "cnp_dequeues": 0, "drops": 0, "trace": False}
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return stats
    stats["trace"] = True
    for node_type, event, l3, ecn, flags in _trace_records(path):
        if event == EVENT_DROP:
            stats["drops"] += 1
        if node_type != 1 or event != EVENT_DEQUEUE:
            continue
        if l3 == L3_UDP and ecn == ECN_CE:
            stats["ecn_marked_dequeues"] += 1
        if l3 in (L3_ACK, L3_NACK) and flags & CNP_FLAG:
            stats["cnp_dequeues"] += 1
    return stats


def queue_series(samples):
    """Bytes on the port that reaches the highest occupancy, with gaps as 0."""
    peak = 0
    peak_port = None
    for _, ports in samples:
        for port, nbytes in ports.items():
            if nbytes > peak:
                peak = nbytes
                peak_port = port
    if peak_port is None:
        return None, []
    series = [(time_ns, ports.get(peak_port, 0)) for time_ns, ports in samples]
    return peak_port, series


def summarize(run_dir):
    flows = parse_fct(os.path.join(run_dir, "fct.txt"))
    samples = parse_qlen(os.path.join(run_dir, "qlen.txt"))
    pauses, resumes = parse_pfc(os.path.join(run_dir, "pfc.txt"))
    trace = parse_trace(os.path.join(run_dir, "mix.tr"))
    peak = 0
    for _, ports in samples:
        if ports:
            peak = max(peak, max(ports.values()))
    fcts = [flow["fct_ns"] for flow in flows]
    summary = {
        "run": os.path.basename(run_dir.rstrip("/")),
        "flows": len(flows),
        "flow_bytes": sorted({flow["bytes"] for flow in flows}),
        "fct_ns_per_flow": [
            {"src": flow["src"], "dst": flow["dst"], "fct_ns": flow["fct_ns"]}
            for flow in flows
        ],
        "incast_completion_ns": max(fcts) if fcts else None,
        "min_fct_ns": min(fcts) if fcts else None,
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
    path = os.path.join(run_dir, "summary.json")
    with open(path, "w") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    text = os.path.join(run_dir, "summary.txt")
    with open(text, "w") as handle:
        handle.write(f"flows: {summary['flows']} sizes={summary['flow_bytes']}\n")
        handle.write(f"incast completion: {summary['incast_completion_ns']} ns\n")
        handle.write(f"max queue: {summary['max_queue_bytes']} bytes\n")
        handle.write(
            f"PFC pause/resume: {summary['pfc_pause_events']}/"
            f"{summary['pfc_resume_events']}\n"
        )
        handle.write(
            f"ECN-marked switch dequeues: {summary['ecn_marked_dequeues']}\n"
        )
        handle.write(f"CNP switch dequeues: {summary['cnp_dequeues']}\n")
        handle.write(f"drops: {summary['drops']}\n")
        handle.write(f"sender rate: {summary['sender_rate']}\n")
    return text


CSV_FIELDS = [
    "run",
    "flows",
    "incast_completion_ns",
    "min_fct_ns",
    "max_queue_bytes",
    "pfc_pause_events",
    "pfc_resume_events",
    "ecn_marked_dequeues",
    "cnp_dequeues",
    "drops",
    "sender_rate",
]


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
        xs = [time_ns / 1e3 for time_ns, _ in series]
        ys = [nbytes / 1e6 for _, nbytes in series]
        ax.plot(xs, ys, label=f"{label} port {port}")
    ax.set_xlabel("time (us)")
    ax.set_ylabel("queue occupancy (MB)")
    ax.set_title("Experiment 0 incast, busiest switch port")
    ax.legend()
    ax.grid(True, alpha=0.3)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"plot: {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", help="result directories")
    parser.add_argument(
        "--plot",
        default=None,
        help="png path; default is <first-run>/../plots/e0_queue_occupancy.png",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="do not write a queue plot",
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="summary CSV path; default is <first-run>/../summary.csv",
    )
    args = parser.parse_args()

    collected = []
    summaries = []
    for run_dir in args.runs:
        summary, samples = summarize(run_dir)
        write_summary(run_dir, summary)
        summaries.append(summary)
        collected.append((summary["run"], samples))
        print(
            f"{summary['run']}: completion={summary['incast_completion_ns']} ns "
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
        plot_path = args.plot or os.path.join(parent, "plots", "e0_queue_occupancy.png")
        plot(collected, plot_path)

    if len(summaries) == 2:
        a, b = summaries
        queue_ratio = None
        if a["max_queue_bytes"] and b["max_queue_bytes"]:
            high = max(a["max_queue_bytes"], b["max_queue_bytes"])
            low = min(a["max_queue_bytes"], b["max_queue_bytes"])
            queue_ratio = high / low
        pauses_differ = a["pfc_pause_events"] != b["pfc_pause_events"]
        queues_differ = queue_ratio is not None and queue_ratio >= 1.5
        if not queues_differ and not pauses_differ:
            print(
                "queue occupancy and PFC pause counts are too similar "
                "to treat the two modes as distinct",
                file=sys.stderr,
            )
            return 1
        print(
            f"difference: max-queue ratio={queue_ratio} "
            f"pauses {a['pfc_pause_events']} vs {b['pfc_pause_events']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
