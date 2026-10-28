"""Readers for the optional outputs of the extended ns-3 backend.

Files (all opt-in through the network config):
  counters.txt   COUNTERS_OUTPUT_FILE          key=value lines
  flows.csv      ENABLE_FLOW_METADATA_TRACE 1  one row per logical message
  cca_trace.csv  ENABLE_CCA_TRACE 1|2          controller events
  paths.csv      ENABLE_PATH_TRACE 1           connection / flow paths
  snapshots.txt  STATE_SNAPSHOT_TIMES          state dumps at requested times
  link_report.txt LINK_REPORT_FILE             per-link BDP/headroom/buffer

Every reader returns plain dicts/lists and tolerates a missing file.
"""

import csv
import os

UNSPECIFIED = ""


def _int(value, default=0):
    if value is None or value == "":
        return default
    return int(value)


def _kv_line(line):
    out = {}
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            out[key] = value
    return out


def read_counters(path):
    """counters.txt -> dict of ints (plus 'switch_ports': list of dicts)."""
    result = {"present": False, "switch_ports": []}
    if not os.path.exists(path):
        return result
    result["present"] = True
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if line.startswith("switch_port "):
                result["switch_ports"].append(
                    {k: _int(v) for k, v in _kv_line(line[len("switch_port "):]).items()}
                )
                continue
            key, _, value = line.partition("=")
            try:
                result[key] = int(value)
            except ValueError:
                result[key] = value
    return result


def read_flows(path):
    """flows.csv -> list of dict rows with ints where sensible."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            item = dict(row)
            for key in (
                "msg_id",
                "connection_id",
                "qp_id",
                "src",
                "dst",
                "sport",
                "dport",
                "tag",
                "bytes",
                "issue_time_ns",
                "network_start_time_ns",
                "completion_time_ns",
            ):
                item[key] = _int(row.get(key))
            for key in (
                "collective_id",
                "operation_id",
                "phase_id",
                "chunk_id",
                "communicator_id",
                "channel_id",
            ):
                item[key] = None if row.get(key, "") == "" else int(row[key])
            rows.append(item)
    return rows


def read_cca_trace(path):
    """cca_trace.csv -> list of dict rows (numeric fields converted)."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            item = dict(row)
            for key in (
                "time_ns",
                "conn_id",
                "qp_id",
                "src",
                "dst",
                "sport",
                "dport",
                "cc_mode",
                "rate_bps",
                "target_rate_bps",
                "hp_cur_rate_bps",
                "window_bytes",
                "snd_nxt",
                "snd_una",
                "size_bytes",
                "extra",
            ):
                item[key] = _int(row.get(key))
            item["alpha"] = float(row.get("alpha") or 0.0)
            rows.append(item)
    return rows


def read_paths(path):
    """paths.csv -> list of dict rows."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            item = dict(row)
            for key in ("connection_id", "qp_id", "src", "dst", "sport", "dport", "time_ns"):
                item[key] = _int(row.get(key))
            rows.append(item)
    return rows


def read_snapshots(path):
    """snapshots.txt -> list of {requested_ns, time_ns, counters, qps, ports}."""
    snapshots = []
    if not os.path.exists(path):
        return snapshots
    current = None
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            kind, _, rest = line.partition(" ")
            if kind == "snapshot":
                current = {"qps": [], "ports": [], "counters": {}}
                current.update({k: _int(v) for k, v in _kv_line(rest).items()})
                snapshots.append(current)
            elif current is None:
                continue
            elif kind == "counters":
                current["counters"] = {k: _int(v) for k, v in _kv_line(rest).items()}
            elif kind == "qp":
                values = _kv_line(rest)
                item = {k: (float(v) if k == "alpha" else _int(v)) for k, v in values.items()}
                current["qps"].append(item)
            elif kind == "port":
                current["ports"].append({k: _int(v) for k, v in _kv_line(rest).items()})
    return snapshots


def read_link_report(path):
    """link_report.txt -> {'config':{}, 'links':[], 'ports':[], 'switches':[], 'pairs':[], 'global':{}}."""
    out = {"config": {}, "links": [], "ports": [], "switches": [], "pairs": [], "global": {}}
    if not os.path.exists(path):
        return out
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            kind, _, rest = line.partition(" ")
            values = _kv_line(rest)
            parsed = {}
            for key, value in values.items():
                try:
                    parsed[key] = int(value)
                except ValueError:
                    try:
                        parsed[key] = float(value)
                    except ValueError:
                        parsed[key] = value
            if kind == "config":
                out["config"] = parsed
            elif kind == "global":
                out["global"] = parsed
            elif kind in ("link", "port", "switch", "pair"):
                out[kind + ("es" if kind == "switch" else "s")].append(parsed)
    return out


def mmu_drops_from_stdout(path):
    """Count 'Headroom full' drop lines in stdout.log (pre-counter builds)."""
    if not os.path.exists(path):
        return 0
    count = 0
    with open(path, errors="replace") as handle:
        for line in handle:
            if "Drop: queue:" in line and "Headroom full" in line:
                count += 1
    return count


def drop_count(run_dir, trace_drops):
    """Authoritative drop count: counters when present, else stdout + trace."""
    counters = read_counters(os.path.join(run_dir, "counters.txt"))
    if counters["present"] and "mmu_drops" in counters:
        return int(counters["mmu_drops"]) + int(trace_drops)
    return mmu_drops_from_stdout(os.path.join(run_dir, "stdout.log")) + int(trace_drops)


def payload_flows(flows):
    return [row for row in flows if row.get("kind") == "payload"]


def connection_summary(flows):
    """Per connection: message count, qp ids, ports. Works for both modes."""
    conns = {}
    for row in flows:
        key = row["connection_id"] if row["connection_id"] else ("legacy", row["src"], row["dst"], row["sport"])
        entry = conns.setdefault(
            key,
            {"src": row["src"], "dst": row["dst"], "sports": set(), "qp_ids": set(), "messages": 0, "bytes": 0},
        )
        entry["sports"].add(row["sport"])
        entry["qp_ids"].add(row["qp_id"])
        entry["messages"] += 1
        entry["bytes"] += row["bytes"]
    return conns


def dcqcn_rate_stats(trace, line_rate_bps, start_ns=None, end_ns=None):
    """Time-weighted DCQCN rate statistics from a cca trace, per QP, then pooled.

    The trace records the rate after each event. Between events the rate is
    held. Only intervals while the QP has unacknowledged work are counted
    (snd_una < size_bytes at the start of the interval), so idle periods do
    not dilute the average.
    """
    by_qp = {}
    for row in trace:
        by_qp.setdefault(row["qp_id"], []).append(row)
    total_time = 0
    weighted_rate = 0.0
    below = {0.25: 0, 0.5: 0, 0.75: 0}
    min_rate = None
    decreases = 0
    increases = 0
    for rows in by_qp.values():
        rows.sort(key=lambda r: r["time_ns"])
        for index, row in enumerate(rows):
            if row["event"] == "RATE_DECREASE":
                decreases += 1
            elif row["event"] == "RATE_INCREASE":
                increases += 1
            if row["event"] in ("QP_DESTROY",):
                continue
            if index + 1 >= len(rows):
                continue
            nxt = rows[index + 1]
            t0 = row["time_ns"]
            t1 = nxt["time_ns"]
            if start_ns is not None:
                t0 = max(t0, start_ns)
            if end_ns is not None:
                t1 = min(t1, end_ns)
            if t1 <= t0:
                continue
            if row["snd_una"] >= row["size_bytes"]:
                continue  # idle
            rate = row["rate_bps"]
            if min_rate is None or rate < min_rate:
                min_rate = rate
            span = t1 - t0
            total_time += span
            weighted_rate += rate * span
            for fraction in below:
                if rate < fraction * line_rate_bps:
                    below[fraction] += span
    if total_time == 0:
        return {
            "mean_rate_bps": "",
            "min_rate_bps": "" if min_rate is None else min_rate,
            "rate_decreases": decreases,
            "rate_increases": increases,
            "time_below_25pct": "",
            "time_below_50pct": "",
            "time_below_75pct": "",
        }
    return {
        "mean_rate_bps": int(weighted_rate / total_time),
        "min_rate_bps": min_rate,
        "rate_decreases": decreases,
        "rate_increases": increases,
        "time_below_25pct": f"{below[0.25] / total_time:.6f}",
        "time_below_50pct": f"{below[0.5] / total_time:.6f}",
        "time_below_75pct": f"{below[0.75] / total_time:.6f}",
    }
