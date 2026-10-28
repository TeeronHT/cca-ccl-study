#!/usr/bin/env python3
"""Summarize E4. Plotting is optional and is skipped when matplotlib is absent."""

import argparse
import csv
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))
import backend_outputs as bo  # noqa: E402

LINE_RATE_BPS = 200_000_000_000
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
    "transport_connection_mode",
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
    "logical_message_count",
    "connection_count",
    "qp_count",
    "messages_per_qp",
    "messages_outstanding",
    "median_flow_bytes",
    "congestion_slowdown",
    "cca_penalty_vs_pfc",
    "granularity_penalty",
    "rate_decrease_events",
    "rate_increase_events",
    "mean_sender_rate",
    "min_sender_rate",
    "fraction_time_rate_below_25pct",
    "fraction_time_rate_below_50pct",
    "fraction_time_rate_below_75pct",
    "chunks_with_times",
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


def per_chunk_times(flows):
    """(collective_id, chunk_id) -> first issue, last completion, message count.

    chunk_id is ASTRA's stream id; streams of one collective are created in
    order, so sorting by chunk_id within a collective gives the chunk index.
    """
    chunks = {}
    for row in flows:
        if row["kind"] != "payload" or row["chunk_id"] is None:
            continue
        key = (row["collective_id"], row["chunk_id"])
        entry = chunks.setdefault(key, {"issue_ns": row["issue_time_ns"], "completion_ns": row["completion_time_ns"], "messages": 0, "bytes": 0})
        entry["issue_ns"] = min(entry["issue_ns"], row["issue_time_ns"])
        entry["completion_ns"] = max(entry["completion_ns"], row["completion_time_ns"])
        entry["messages"] += 1
        entry["bytes"] += row["bytes"]
    return chunks


def write_per_chunk(run_dir, chunks):
    path = os.path.join(run_dir, "per_chunk.csv")
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["collective_id", "chunk_id", "chunk_index", "issue_ns", "completion_ns", "duration_ns", "messages", "bytes"])
        by_coll = {}
        for (coll, chunk), entry in chunks.items():
            by_coll.setdefault(coll, []).append((chunk, entry))
        for coll in sorted(by_coll, key=lambda c: (c is None, c)):
            for index, (chunk, entry) in enumerate(sorted(by_coll[coll], key=lambda item: item[0])):
                writer.writerow([coll, chunk, index, entry["issue_ns"], entry["completion_ns"],
                                 entry["completion_ns"] - entry["issue_ns"], entry["messages"], entry["bytes"]])
    return path


def summarize(run_dir):
    meta, uplinks, tors, spines = load_ports(run_dir)
    ends, sizes = parse_fct(os.path.join(run_dir, "fct.txt"))
    max_any, max_up, max_spine = parse_qlen(os.path.join(run_dir, "qlen.txt"), uplinks, spines)
    trace = parse_trace(os.path.join(run_dir, "mix.tr"), uplinks, tors, spines)
    counters = bo.read_counters(os.path.join(run_dir, "counters.txt"))
    flows = bo.read_flows(os.path.join(run_dir, "flows.csv"))
    cca_trace = bo.read_cca_trace(os.path.join(run_dir, "cca_trace.csv"))
    conns = bo.connection_summary(flows)
    qp_ids = {qp for entry in conns.values() for qp in entry["qp_ids"]}
    chunks = per_chunk_times(flows)
    if flows:
        write_per_chunk(run_dir, chunks)
    rate = bo.dcqcn_rate_stats(cca_trace, LINE_RATE_BPS) if cca_trace else {}
    collective = max(ends) if ends else 0
    chunk_count = int(meta["chunk_count"])
    ordered = sorted(sizes)
    median = ordered[len(ordered) // 2] if ordered else 0
    bandwidth = (MESSAGE_BYTES * 8 / collective) if collective else 0
    payload = bo.payload_flows(flows)
    return {
        "run_id": meta["run_id"],
        "topology": meta["topology"],
        "oversubscription": meta["oversubscription"],
        "cca": meta["cca"],
        "transport_connection_mode": meta.get("transport_connection_mode", "LEGACY"),
        "chunk_count": chunk_count,
        "chunk_bytes": MESSAGE_BYTES // chunk_count,
        "total_application_bytes": MESSAGE_BYTES,
        "collective_ns": collective,
        "effective_bandwidth_gbps": f"{bandwidth:.6f}",
        "max_queue_bytes": max_any,
        "max_tor_uplink_queue_bytes": max_up,
        "max_spine_queue_bytes": max_spine,
        "pfc_pause_count": parse_pfc(os.path.join(run_dir, "pfc.txt")),
        "ecn_count": counters.get("ecn_marks", trace["ecn"]),
        "cnp_count": counters.get("cnp_acks", trace["cnp"]),
        "drop_count": bo.drop_count(run_dir, trace["drops"]),
        "flow_count": len(sizes),
        "logical_message_count": len(payload) if flows else len(sizes),
        "connection_count": len(conns) if flows else "",
        "qp_count": len(qp_ids) if flows else "",
        "messages_per_qp": f"{len(payload) / len(qp_ids):.3f}" if qp_ids else "",
        "messages_outstanding": counters.get("messages_outstanding", ""),
        "median_flow_bytes": median,
        "congestion_slowdown": "",
        "cca_penalty_vs_pfc": "",
        "granularity_penalty": "",
        "rate_decrease_events": rate.get("rate_decreases", ""),
        "rate_increase_events": rate.get("rate_increases", ""),
        "mean_sender_rate": rate.get("mean_rate_bps", ""),
        "min_sender_rate": rate.get("min_rate_bps", ""),
        "fraction_time_rate_below_25pct": rate.get("time_below_25pct", ""),
        "fraction_time_rate_below_50pct": rate.get("time_below_50pct", ""),
        "fraction_time_rate_below_75pct": rate.get("time_below_75pct", ""),
        "chunks_with_times": len(chunks),
        "study_commit": meta.get("study_commit", ""),
        "astra_commit": meta.get("astra_commit", ""),
        "ns3_commit": meta.get("ns3_commit", ""),
        "seed": meta.get("seed", ""),
        "_flows": flows,
        "_conns": conns,
        "_cca_trace": cca_trace,
        "_counters": counters,
        "_paths": bo.read_paths(os.path.join(run_dir, "paths.csv")),
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
        "transport_connection_mode",
        "rendezvous",
        "cca",
        "cc_mode",
        "has_win",
        "cca_config_hash",
        "cca_sha256",
        "topology_sha256",
        "system_sha256",
        "logical_sha256",
        "workload_sha256",
        "buffer_size_mib",
        "switch_buffer_map",
        "ecn_maps",
        "enable_pfc",
        "pfc_threshold_validation",
        "trace_level",
        "queue_log_min_bytes",
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
            if isinstance(meta.get("ecn_maps"), dict):
                meta["ecn_maps"] = json.dumps(meta["ecn_maps"], sort_keys=True)
            writer.writerow(meta)


def gate_persistence(run_dir, require_carried_state):
    """Refuse the experiment unless persistence is actually observed.

    1. (granularity is checked separately)
    2. every (src,dst) pair with traffic has exactly one connection, QP and port
    3. later messages on a QP start from carried controller state, not a
       line-rate re-initialisation (DCQCN/HPCC; requires congestion to be
       distinguishable, so it is enforced only where require_carried_state)
    4. every payload row carries collective_id and chunk_id
    5. per-chunk completion times are recoverable (per_chunk.csv written)
    6. one ECMP path is recorded per connection
    """
    row = summarize(run_dir)
    print_run(row)
    problems = []
    flows = row["_flows"]
    conns = row["_conns"]
    counters = row["_counters"]
    if row["transport_connection_mode"] != "PERSISTENT":
        problems.append("run was not made with TRANSPORT_CONNECTION_MODE PERSISTENT")
    if not flows:
        problems.append("flows.csv missing or empty (ENABLE_FLOW_METADATA_TRACE)")
    pairs = {}
    for conn_key, entry in conns.items():
        pairs.setdefault((entry["src"], entry["dst"]), []).append(entry)
    multi = [pair for pair, entries in pairs.items() if len(entries) != 1]
    if multi:
        problems.append(f"{len(multi)} rank pairs use more than one connection, e.g. {multi[:3]}")
    reuse = [entry for entry in conns.values() if entry["messages"] > 1]
    if conns and not reuse:
        problems.append("no connection carried more than one message; nothing was reused")
    bad_ports = [entry for entry in conns.values() if len(entry["sports"]) != 1 or len(entry["qp_ids"]) != 1]
    if bad_ports:
        problems.append(f"{len(bad_ports)} connections span several ports/QPs")
    if counters.get("present"):
        if counters.get("qps_created") != counters.get("connections_created"):
            problems.append(f"qps_created {counters.get('qps_created')} != connections_created {counters.get('connections_created')}")
        if counters.get("messages_outstanding", 0) != 0:
            problems.append(f"messages_outstanding = {counters.get('messages_outstanding')}")
    payload = bo.payload_flows(flows)
    missing = [r for r in payload if r["chunk_id"] is None or r["collective_id"] is None]
    if payload and missing:
        problems.append(f"{len(missing)} payload rows lack collective_id/chunk_id")
    if row["chunks_with_times"] != int(row["chunk_count"]) and payload:
        problems.append(f"recovered {row['chunks_with_times']} chunks, expected {row['chunk_count']}")
    conn_paths = {p["connection_id"] for p in row["_paths"] if p["record"] == "connection" and p["path"]}
    if conns and len(conn_paths) != len(conns):
        problems.append(f"paths recorded for {len(conn_paths)} of {len(conns)} connections")
    # carried state: a MESSAGE_START that is not the first on its QP and is
    # not at line rate proves state was inherited. Only required where
    # congestion is expected (2:1 DCQCN/HPCC).
    trace = row["_cca_trace"]
    starts = [r for r in trace if r["event"] == "MESSAGE_START"]
    first_seen = set()
    carried = 0
    later = 0
    for r in sorted(starts, key=lambda r: r["time_ns"]):
        if r["qp_id"] in first_seen:
            later += 1
            if r["rate_bps"] < LINE_RATE_BPS or (r["cc_mode"] == 3 and r["hp_cur_rate_bps"] < LINE_RATE_BPS):
                carried += 1
        else:
            first_seen.add(r["qp_id"])
    creates = sum(1 for r in trace if r["event"] == "QP_CREATE")
    if trace and creates != len(conns):
        problems.append(f"QP_CREATE events {creates} != connections {len(conns)}: QPs were re-created")
    if require_carried_state and row["cca"] in ("dcqcn", "hpcc") and later and carried == 0:
        problems.append("no later message on any QP started below line rate; controller state did not carry "
                        "(or no congestion occurred, in which case the 2:1 contention gate should have failed)")
    print(f"persistence: connections={len(conns)} reused={len(reuse)} later_starts={later} carried_state_starts={carried} "
          f"chunks={row['chunks_with_times']} paths={len(conn_paths)}")
    if problems:
        print("E4 REFUSES TO RUN AS A VALID EXPERIMENT:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("persistence gate: connections, QPs and ports are reused; chunk ids and paths are recorded")
    return 0


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
        import matplotlib
    except ImportError:
        print("matplotlib is not installed; skipping plots")
        return
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

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
    parser.add_argument("--gate-persistence", default=None, help="run directory that must show QP reuse")
    parser.add_argument("--require-carried-state", action="store_true",
                        help="with --gate-persistence: also require a later message to start below line rate")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if args.gate_contention:
        return gate_contention(args.gate_contention)
    if args.gate_granularity:
        return gate_granularity(*args.gate_granularity)
    if args.gate_persistence:
        return gate_persistence(args.gate_persistence, args.require_carried_state)
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
