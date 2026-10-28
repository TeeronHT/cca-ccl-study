#!/usr/bin/env python3
"""Summarize E6. Plotting is skipped when matplotlib is absent."""

import argparse
import csv
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))
import model
import backend_outputs as bo

LINE_RATE_BPS = 200_000_000_000
PHASE_PRECONDITION = 1
PHASE_COMPLETION = 2
TRACE_RECORD_BYTES = 56
EVENT_DEQUEUE = 2
EVENT_DROP = 3
L3_UDP = 0x11
L3_ACK = 0xFC
L3_NACK = 0xFD
ECN_CE = 0x03
CNP_FLAG = 1
HANDSHAKE = 8192

FIELDS = [
    "run_id",
    "topology",
    "schedule",
    "cca",
    "arrival_pattern",
    "tau_ns",
    "tau_over_tref",
    "tensor_bytes",
    "total_collective_ns",
    "post_last_arrival_ns",
    "max_queue_bytes",
    "max_tor_uplink_queue_bytes",
    "max_spine_queue_bytes",
    "pfc_pause_count",
    "ecn_count",
    "cnp_count",
    "drop_count",
    "flow_count",
    "qp_count",
    "transport_connection_mode",
    "logical_message_count",
    "connection_count",
    "messages_per_qp",
    "messages_outstanding",
    "bytes_transmitted_before_last_arrival",
    "completed_events_before_last_arrival",
    "pending_events_at_last_arrival",
    "precondition_connections",
    "completion_connections",
    "completion_qps_with_precondition_history",
    "qp_overlap_count",
    "qp_overlap_fraction",
    "active_qps_at_last_arrival",
    "mean_sender_rate_at_last_arrival",
    "min_sender_rate_at_last_arrival",
    "fraction_qps_below_50pct_at_last_arrival",
    "queued_bytes_at_last_arrival",
    "ecn_marks_at_last_arrival",
    "cnp_acks_at_last_arrival",
    "asymmetry_resilient_speedup",
    "residual_speedup",
    "observed_break_even_region",
    "static_predicted_time",
    "static_predicted_winner",
    "actual_winner",
    "ranking_agrees",
    "study_commit",
    "astra_commit",
    "ns3_commit",
    "seed",
]


def ip_to_node(ip):
    return (ip >> 8) & 0xFFFF


def load(run_dir):
    meta = json.load(open(os.path.join(run_dir, "meta.json")))
    ports = json.load(open(meta["ports_file"]))
    uplinks = {int(node): set(indexes) for node, indexes in ports["tor_uplinks"].items()}
    spines = {int(node) for node in ports["spine_ports"]}
    return meta, uplinks, spines


def parse_fct(path):
    flows = []
    if not os.path.exists(path):
        return flows
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 8:
                continue
            flows.append(
                {
                    # fct.txt prints sip/dip as %08x
                    "src": ip_to_node(int(parts[0], 16)),
                    "dst": ip_to_node(int(parts[1], 16)),
                    "sport": int(parts[2]),
                    "dport": int(parts[3]),
                    "bytes": int(parts[4]),
                    "start": int(parts[5]),
                    "end": int(parts[5]) + int(parts[6]),
                }
            )
    return flows


def parse_qlen(path, uplinks, spines, until=None):
    max_any = max_up = max_spine = 0
    queued_at = 0
    if not os.path.exists(path):
        return max_any, max_up, max_spine, queued_at
    with open(path) as handle:
        for line in handle:
            tokens = line.split()
            if len(tokens) < 3 or tokens[0] != "time":
                continue
            timestamp = int(tokens[1])
            if until is not None and timestamp > until:
                break
            switch = int(tokens[2])
            total = 0
            index = 3
            while index + 2 < len(tokens) and tokens[index] == "j":
                port = int(tokens[index + 1])
                nbytes = int(tokens[index + 2])
                total += nbytes
                max_any = max(max_any, nbytes)
                if switch in uplinks and port in uplinks[switch]:
                    max_up = max(max_up, nbytes)
                if switch in spines:
                    max_spine = max(max_spine, nbytes)
                index += 3
            queued_at = total
    return max_any, max_up, max_spine, queued_at


def parse_pfc(path, until=None):
    count = 0
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return count
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) == 5 and int(parts[4]) == 1:
                if until is None or int(parts[0]) <= until:
                    count += 1
    return count


def parse_trace(path, until=None):
    stats = {"drops": 0, "ecn": 0, "cnp": 0, "bytes": 0}
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
        timestamp = struct.unpack_from("<Q", blob, offset)[0]
        if until is not None and timestamp > until:
            offset += TRACE_RECORD_BYTES
            continue
        event = blob[offset + 27]
        l3 = blob[offset + 26]
        ecn = blob[offset + 28]
        size = struct.unpack_from("<H", blob, offset + 24)[0]
        flags = struct.unpack_from("<H", blob, offset + 36)[0]
        if event == EVENT_DROP:
            stats["drops"] += 1
        if event == EVENT_DEQUEUE:
            stats["bytes"] += size
            if l3 == L3_UDP and ecn == ECN_CE:
                stats["ecn"] += 1
            if l3 in (L3_ACK, L3_NACK) and flags & CNP_FLAG:
                stats["cnp"] += 1
        offset += TRACE_RECORD_BYTES
    return stats


def payload(flows):
    return [flow for flow in flows if flow["bytes"] != HANDSHAKE]


def phase_overlap(flow_rows):
    """E6's state-overlap metric from flows.csv.

    completion QPs with precondition history / all completion QPs, where a
    QP (connection) has precondition history if it carried at least one
    precondition payload message. Handshakes are excluded. Returns a dict
    with counts; empty when the run has no phase labels (the Ring schedule).
    """
    pre = {r["connection_id"] for r in flow_rows if r["kind"] == "payload" and r["phase_id"] == PHASE_PRECONDITION}
    post = {r["connection_id"] for r in flow_rows if r["kind"] == "payload" and r["phase_id"] == PHASE_COMPLETION}
    if not post and not pre:
        return {}
    overlap = pre & post
    return {
        "precondition_connections": len(pre),
        "completion_connections": len(post),
        "overlap": len(overlap),
        "fraction": (len(overlap) / len(post)) if post else 0.0,
    }


def snapshot_at(run_dir, tau):
    """Backend snapshot requested at tau (STATE_SNAPSHOT_TIMES), if present."""
    snaps = bo.read_snapshots(os.path.join(run_dir, "snapshots.txt"))
    if not snaps or tau <= 0:
        return None
    exact = [s for s in snaps if s.get("requested_ns") == tau]
    return exact[0] if exact else snaps[0]


def snapshot_metrics(snap):
    if not snap:
        return {"active_qps": "", "mean_rate": "", "min_rate": "", "below_50": "", "queued": "", "ecn": "", "cnp": ""}
    active = [q for q in snap["qps"] if q.get("pending_msgs", 0) > 0 or q.get("bytes_left", 0) > 0]
    rates = [q["rate_bps"] for q in active]
    queued = sum(p.get("egress_bytes", 0) for p in snap["ports"])
    return {
        "active_qps": len(active),
        "mean_rate": int(sum(rates) / len(rates)) if rates else "",
        "min_rate": min(rates) if rates else "",
        "below_50": f"{sum(1 for r in rates if r < 0.5 * LINE_RATE_BPS) / len(rates):.6f}" if rates else "",
        "queued": queued,
        "ecn": snap["counters"].get("ecn_marks", ""),
        "cnp": snap["counters"].get("cnp_acks", ""),
    }


def summarize(run_dir):
    meta, uplinks, spines = load(run_dir)
    flows = parse_fct(os.path.join(run_dir, "fct.txt"))
    flow_rows = bo.read_flows(os.path.join(run_dir, "flows.csv"))
    counters = bo.read_counters(os.path.join(run_dir, "counters.txt"))
    tau = int(meta["tau_ns"])
    done = max((flow["end"] for flow in flows), default=0)
    at_any, at_up, at_spine, queued = parse_qlen(
        os.path.join(run_dir, "qlen.txt"), uplinks, spines, tau
    )
    whole_q, whole_up, whole_spine, _ = parse_qlen(
        os.path.join(run_dir, "qlen.txt"), uplinks, spines, None
    )
    trace_before = parse_trace(os.path.join(run_dir, "mix.tr"), tau)
    trace_all = parse_trace(os.path.join(run_dir, "mix.tr"), None)
    if flow_rows:
        # flows.csv labels rendezvous handshakes explicitly (kind=handshake),
        # so payload is not inferred from the 8 KiB size any more.
        payload_rows = bo.payload_flows(flow_rows)
        messages = [
            {"src": r["src"], "dst": r["dst"], "sport": r["sport"], "dport": r["dport"], "bytes": r["bytes"],
             "start": r["issue_time_ns"], "end": r["completion_time_ns"]}
            for r in payload_rows
        ]
    else:
        payload_rows = []
        messages = payload(flows)
    finished = [flow for flow in messages if flow["end"] <= tau]
    pending = max(0, int(meta.get("payload_sends", 0)) - len(finished))
    overlap = phase_overlap(flow_rows)
    conns = bo.connection_summary(flow_rows) if flow_rows else {}
    qp_ids = {qp for entry in conns.values() for qp in entry["qp_ids"]}
    snap = snapshot_at(run_dir, tau)
    snapm = snapshot_metrics(snap)
    predicted = model.predict(meta["arrival_pattern"], tau)
    static_time = predicted["ring_ns"] if meta["schedule"] == "ring" else predicted["stragglar_ns"]
    row = {
        "run_id": meta["run_id"],
        "topology": meta["topology"],
        "schedule": meta["schedule"],
        "cca": meta["cca"],
        "arrival_pattern": meta["arrival_pattern"],
        "tau_ns": tau,
        "tau_over_tref": meta["tau_over_tref"],
        "tensor_bytes": model.TENSOR,
        "total_collective_ns": done,
        "post_last_arrival_ns": max(0, done - tau),
        "max_queue_bytes": whole_q,
        "max_tor_uplink_queue_bytes": whole_up,
        "max_spine_queue_bytes": whole_spine,
        "pfc_pause_count": parse_pfc(os.path.join(run_dir, "pfc.txt")),
        "ecn_count": counters.get("ecn_marks", trace_all["ecn"]),
        "cnp_count": counters.get("cnp_acks", trace_all["cnp"]),
        "drop_count": bo.drop_count(run_dir, trace_all["drops"]),
        "flow_count": len(flows),
        "qp_count": len(qp_ids) if flow_rows else len({(f["src"], f["dst"], f["sport"], f["dport"]) for f in flows}),
        "transport_connection_mode": meta.get("transport_connection_mode", "LEGACY"),
        "logical_message_count": len(payload_rows) if flow_rows else len(messages),
        "connection_count": len(conns) if flow_rows else "",
        "messages_per_qp": f"{len(flow_rows) / len(qp_ids):.3f}" if qp_ids else "",
        "messages_outstanding": counters.get("messages_outstanding", ""),
        "bytes_transmitted_before_last_arrival": trace_before["bytes"],
        "completed_events_before_last_arrival": len(finished),
        "pending_events_at_last_arrival": pending,
        "precondition_connections": overlap.get("precondition_connections", ""),
        "completion_connections": overlap.get("completion_connections", ""),
        "completion_qps_with_precondition_history": overlap.get("overlap", ""),
        "qp_overlap_count": overlap.get("overlap", ""),
        "qp_overlap_fraction": f"{overlap['fraction']:.6f}" if overlap else "",
        "active_qps_at_last_arrival": snapm["active_qps"],
        "mean_sender_rate_at_last_arrival": snapm["mean_rate"],
        "min_sender_rate_at_last_arrival": snapm["min_rate"],
        "fraction_qps_below_50pct_at_last_arrival": snapm["below_50"],
        "queued_bytes_at_last_arrival": snapm["queued"],
        "ecn_marks_at_last_arrival": snapm["ecn"],
        "cnp_acks_at_last_arrival": snapm["cnp"],
        "asymmetry_resilient_speedup": "",
        "residual_speedup": "",
        "observed_break_even_region": "",
        "static_predicted_time": int(static_time),
        "static_predicted_winner": predicted["winner"],
        "actual_winner": "",
        "ranking_agrees": "",
        "study_commit": meta.get("study_commit", ""),
        "astra_commit": meta.get("astra_commit", ""),
        "ns3_commit": meta.get("ns3_commit", ""),
        "seed": meta.get("seed", ""),
        "_queued_at_tau": queued,
        "_pfc_before": parse_pfc(os.path.join(run_dir, "pfc.txt"), tau),
        "_ecn_before": trace_before["ecn"],
        "_cnp_before": trace_before["cnp"],
        "_flows": messages,
        "_flow_rows": flow_rows,
    }
    state = {
        "tau_ns": tau,
        "queued_bytes_at_last_sample": queued,
        "max_queue_bytes_by_tau": at_any,
        "max_tor_uplink_queue_bytes_by_tau": at_up,
        "max_spine_queue_bytes_by_tau": at_spine,
        "pfc_pause_count_by_tau": row["_pfc_before"],
        "ecn_count_by_tau": trace_before["ecn"],
        "cnp_count_by_tau": trace_before["cnp"],
        "bytes_dequeued_by_tau": trace_before["bytes"],
        "payload_flows_completed_by_tau": len(finished),
        "state_overlap": overlap,
        "backend_snapshot": snap if snap else "no STATE_SNAPSHOT_TIMES snapshot (tau 0 or old binary)",
    }
    with open(os.path.join(run_dir, "network_state_at_last_arrival.json"), "w") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
    return row


def lookup(rows, topology, pattern, tau, cca, schedule):
    for row in rows:
        if (
            row["topology"] == topology
            and row["arrival_pattern"] == pattern
            and float(row["tau_over_tref"]) == float(tau)
            and row["cca"] == cca
            and row["schedule"] == schedule
            and int(row["total_collective_ns"])
        ):
            return row
    return None


def add_derived(rows):
    for row in rows:
        ring = lookup(rows, row["topology"], row["arrival_pattern"], row["tau_over_tref"], row["cca"], "ring")
        other = lookup(
            rows, row["topology"], row["arrival_pattern"], row["tau_over_tref"], row["cca"], "stragglar"
        )
        if not ring or not other:
            continue
        speed = int(ring["total_collective_ns"]) / int(other["total_collective_ns"])
        residual = int(ring["post_last_arrival_ns"]) / max(1, int(other["post_last_arrival_ns"]))
        winner = "stragglar" if speed > 1 else "ring"
        if abs(speed - 1) < 1e-9:
            winner = "tie"
        row["asymmetry_resilient_speedup"] = f"{speed:.6f}"
        row["residual_speedup"] = f"{residual:.6f}"
        row["actual_winner"] = winner
        row["ranking_agrees"] = str(winner == row["static_predicted_winner"]).lower()
    for topology in ("2to1", "1to1"):
        for pattern in ("one-late", "two-late"):
            for cca in ("pfc", "dcqcn", "hpcc"):
                region = break_even(rows, topology, pattern, cca)
                for row in rows:
                    if row["topology"] == topology and row["arrival_pattern"] == pattern and row["cca"] == cca:
                        row["observed_break_even_region"] = region


def break_even(rows, topology, pattern, cca):
    points = []
    for tau in (0.25, 0.5, 1.0):
        sample = lookup(rows, topology, pattern, tau, cca, "ring")
        if not sample or sample["asymmetry_resilient_speedup"] == "":
            return ""
        points.append((tau, float(sample["asymmetry_resilient_speedup"]) > 1))
    if points[0][1]:
        return "<= 0.25 T_ref"
    if points[1][1]:
        return "between 0.25 and 0.50 T_ref"
    if points[2][1]:
        return "between 0.50 and 1.00 T_ref"
    return "> 1.00 T_ref"


def print_run(row):
    print(
        f"{row['run_id']}:\n"
        f"    total={row['total_collective_ns']}\n"
        f"    post_last={row['post_last_arrival_ns']}\n"
        f"    max_tor_queue={row['max_tor_uplink_queue_bytes']}\n"
        f"    pfc={row['pfc_pause_count']} ecn={row['ecn_count']} "
        f"cnp={row['cnp_count']} drops={row['drop_count']}\n"
        f"    bytes_before_tau={row['bytes_transmitted_before_last_arrival']} "
        f"state_overlap={row['completion_qps_with_precondition_history']}/{row['completion_connections']} "
        f"({row['qp_overlap_fraction']})\n"
        f"    mode={row['transport_connection_mode']} connections={row['connection_count']} "
        f"messages={row['logical_message_count']} outstanding={row['messages_outstanding']}\n"
        f"    at tau: active_qps={row['active_qps_at_last_arrival']} mean_rate={row['mean_sender_rate_at_last_arrival']} "
        f"min_rate={row['min_sender_rate_at_last_arrival']} below50={row['fraction_qps_below_50pct_at_last_arrival']} "
        f"queued={row['queued_bytes_at_last_arrival']}"
    )
    if int(row["drop_count"]) > 0:
        print(f"INVALID: {row['run_id']} dropped packets.", file=sys.stderr)
        return 1
    return 0


def print_tables(rows):
    print("\nStragglAR speedup on 2:1 (Ring CCT / StragglAR CCT)")
    for pattern in ("one-late", "two-late"):
        print(pattern)
        print(f"{'':<8}{'tau=0.25':>12}{'tau=0.50':>12}{'tau=1.00':>12}")
        for cca in ("pfc", "dcqcn", "hpcc"):
            print(f"{cca:<8}", end="")
            for tau in (0.25, 0.5, 1.0):
                sample = lookup(rows, "2to1", pattern, tau, cca, "ring")
                cell = sample["asymmetry_resilient_speedup"] if sample else ""
                print(f"{cell:>12}", end="")
            print()


def write_csv(path, rows):
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_manifest(path, run_dirs):
    fields = [
        "run_id", "study_commit", "astra_commit", "ns3_commit", "transport_connection_mode", "rendezvous",
        "cca", "cc_mode", "has_win", "cca_config_hash", "cca_sha256", "topology_sha256",
        "ring_schedule_sha256", "stragglar_schedule_sha256", "workload_sha256",
        "stragglar_source_commit", "synthesis_command", "arrival_sha256", "buffer_size_mib",
        "switch_buffer_map", "ecn_maps", "enable_pfc", "pfc_threshold_validation", "trace_level",
        "queue_log_min_bytes", "snapshot_times_ns", "t_ref_ns", "seed", "hostname", "start", "end",
    ]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for run_dir in run_dirs:
            meta = json.load(open(os.path.join(run_dir, "meta.json")))
            if isinstance(meta.get("ecn_maps"), dict):
                meta["ecn_maps"] = json.dumps(meta["ecn_maps"], sort_keys=True)
            writer.writerow(meta)


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


def write_tref(run_dir):
    row = summarize(run_dir)
    if print_run(row):
        return 1
    path = os.path.join(model.root(), "results", "t_ref.txt")
    with open(path, "w") as handle:
        handle.write(f"{row['total_collective_ns']}\n")
    print(f"T_ref={row['total_collective_ns']}")
    return 0


def gate_late(run_dir):
    row = summarize(run_dir)
    if print_run(row):
        return 1
    tau = int(row["tau_ns"])
    messages = row["_flows"]
    early = [flow for flow in messages if flow["src"] != 15 and flow["end"] <= tau]
    leaked = [flow for flow in messages if flow["src"] == 15 and flow["start"] < tau]
    if leaked:
        print("rank 15 sent payload before tau", file=sys.stderr)
        return 1
    if not early:
        print("no ready-rank payload finished before tau", file=sys.stderr)
        return 1
    print("late-rank smoke: ready ranks moved bytes before tau; rank 15 did not send")
    return 0


def gate_stragglar(run_dir):
    status = gate_late(run_dir)
    if status:
        return status
    row = summarize(run_dir)
    tau = int(row["tau_ns"])
    pre = [
        flow
        for flow in row["_flows"]
        if flow["src"] < 15 and flow["dst"] < 15 and flow["end"] <= tau
    ]
    if not pre:
        print("no precondition flow among ranks 0-14 finished before tau", file=sys.stderr)
        return 1
    print("stragglar smoke: precondition traffic finished before tau")
    return 0


def gate_dcqcn(run_dir):
    row = summarize(run_dir)
    if print_run(row):
        return 1
    if int(row["ecn_count"]) == 0 and int(row["cnp_count"]) == 0:
        print("DCQCN smoke produced no ECN or CNP.", file=sys.stderr)
        return 1
    problems = []
    if row["transport_connection_mode"] != "PERSISTENT":
        problems.append("run is not PERSISTENT")
    if row["qp_overlap_fraction"] == "":
        problems.append("no phase labels in flows.csv; precondition/completion cannot be told apart")
    if row["active_qps_at_last_arrival"] == "" and int(row["tau_ns"]) > 0:
        problems.append("no backend snapshot at tau (STATE_SNAPSHOT_TIMES)")
    conns = bo.connection_summary(row["_flow_rows"]) if row["_flow_rows"] else {}
    if conns and max(e["messages"] for e in conns.values()) < 2:
        problems.append("no connection carried more than one message; QPs were not reused")
    if problems:
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"dcqcn smoke: ECN/CNP present; persistent QPs reused; phase labels present; "
          f"state overlap {row['completion_qps_with_precondition_history']}/{row['completion_connections']}; "
          f"snapshot at tau has {row['active_qps_at_last_arrival']} active QPs")
    return 0


def plot(rows, directory):
    try:
        import matplotlib
    except ImportError:
        print("matplotlib is not installed; skipping plots")
        return
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(directory, exist_ok=True)
    taus = [0.25, 0.5, 1.0]
    for pattern in ("one-late", "two-late"):
        plt.figure()
        for cca in ("pfc", "dcqcn", "hpcc"):
            values = []
            for tau in taus:
                sample = lookup(rows, "2to1", pattern, tau, cca, "ring")
                values.append(float(sample["asymmetry_resilient_speedup"]) if sample and sample["asymmetry_resilient_speedup"] else None)
            plt.plot(taus, values, marker="o", label=cca)
        plt.axhline(1.0)
        plt.xlabel("tau / T_ref")
        plt.ylabel("Ring CCT / StragglAR CCT")
        plt.legend()
        plt.tight_layout()
        plt.savefig(os.path.join(directory, f"speedup-{pattern}.png"))
        plt.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--one", default=None)
    parser.add_argument("--tref", default=None)
    parser.add_argument("--gate-late", default=None)
    parser.add_argument("--gate-stragglar", default=None)
    parser.add_argument("--gate-dcqcn", default=None)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    if args.tref:
        return write_tref(args.tref)
    if args.one:
        return print_run(summarize(args.one))
    if args.gate_late:
        return gate_late(args.gate_late)
    if args.gate_stragglar:
        return gate_stragglar(args.gate_stragglar)
    if args.gate_dcqcn:
        return gate_dcqcn(args.gate_dcqcn)
    raw = os.path.join(model.root(), "results", "raw")
    run_dirs = discover(raw)
    rows = [summarize(run_dir) for run_dir in run_dirs]
    add_derived(rows)
    results = os.path.join(model.root(), "results")
    write_csv(os.path.join(results, "summary.csv"), rows)
    write_manifest(os.path.join(results, "manifest.csv"), run_dirs)
    print_tables(rows)
    if not args.no_plot and rows:
        plot(rows, os.path.join(results, "plots"))
    invalid = [row["run_id"] for row in rows if int(row["drop_count"]) > 0]
    if invalid:
        print("INVALID runs with drops: " + ", ".join(invalid), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
