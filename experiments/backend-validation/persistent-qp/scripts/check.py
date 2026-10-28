#!/usr/bin/env python3
"""Compare LEGACY and PERSISTENT runs of the two-message microbenchmark.

For every (topology, cca, case) the checker reports, side by side:
  QP ids, source ports, connection ids, ECMP paths,
  controller state at the end of message 1 and at the start of message 2,
  the controller events that fired between those two instants.
It fails when the mode does not behave as specified:
  LEGACY      new QP, new port, line-rate initial state for message 2
  PERSISTENT  same QP/port/connection; message 2 starts from the state the
              controller left after message 1, modified only by traced timer
              events (DCQCN increases) that fired in between.
"""

import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, str(Path(ROOT).parents[1] / "common"))
import backend_outputs as bo  # noqa: E402  # type: ignore[import-not-found]

LINE_RATE = 200_000_000_000
TOPOLOGIES = ("bottleneck", "ecmp")
CCAS = ("dcqcn", "hpcc")
CASES = ("b2b", "gap")
MODES = ("legacy", "persistent")
STATE_KEYS = ("rate_bps", "target_rate_bps", "alpha", "hp_cur_rate_bps", "window_bytes")

failures = []


def fail(message):
    failures.append(message)
    print(f"  FAIL {message}")


def ok(message):
    print(f"  ok   {message}")


def load(topology, cca, mode, case):
    run_dir = os.path.join(ROOT, "results", "raw", f"{topology}__{cca}__{mode}__{case}")
    if not os.path.isdir(run_dir):
        return None
    flows = bo.read_flows(os.path.join(run_dir, "flows.csv"))
    trace = bo.read_cca_trace(os.path.join(run_dir, "cca_trace.csv"))
    paths = bo.read_paths(os.path.join(run_dir, "paths.csv"))
    counters = bo.read_counters(os.path.join(run_dir, "counters.txt"))
    payload = [row for row in flows if row["kind"] == "payload"]
    msgs = {row["chunk_id"]: row for row in payload if row["chunk_id"] in (1, 2)}
    return {"dir": run_dir, "flows": flows, "trace": trace, "paths": paths, "counters": counters, "msgs": msgs}


def state_of(row):
    return {key: row[key] for key in STATE_KEYS}


def fmt_state(state):
    if not state:
        return "-"
    return (f"rate={state['rate_bps'] / 1e9:.3f}G target={state['target_rate_bps'] / 1e9:.3f}G "
            f"alpha={state['alpha']:.6f} hp_cur={state['hp_cur_rate_bps'] / 1e9:.3f}G win={state['window_bytes']}")


def events_for(trace, qp_id):
    return sorted((row for row in trace if row["qp_id"] == qp_id), key=lambda r: (r["time_ns"], r["event"] != "QP_CREATE"))


def message_records(run, mode):
    """Return (end1, start2, between) trace rows."""
    msg1, msg2 = run["msgs"].get(1), run["msgs"].get(2)
    if not msg1 or not msg2:
        return None, None, []
    rows1 = events_for(run["trace"], msg1["qp_id"])
    rows2 = events_for(run["trace"], msg2["qp_id"])
    if mode == "legacy":
        end1 = next((r for r in reversed(rows1) if r["event"] == "MESSAGE_COMPLETE"), None)
        start2 = next((r for r in rows2 if r["event"] == "MESSAGE_START"), None)
        between = []
    else:
        end1 = next((r for r in rows1 if r["event"] == "MESSAGE_COMPLETE" and r["extra"] == msg1["msg_id"]), None)
        start2 = next((r for r in rows2 if r["event"] == "MESSAGE_START" and r["extra"] == msg2["msg_id"]), None)
        between = []
        if end1 and start2:
            for r in rows1:
                if r is end1 or r is start2:
                    continue
                if end1["time_ns"] <= r["time_ns"] <= start2["time_ns"] and r["event"] not in ("QP_CREATE", "QP_DESTROY"):
                    between.append(r)
    return end1, start2, between


def path_rows(run, mode):
    if mode == "legacy":
        return {row["sport"]: row["path"] for row in run["paths"] if row["record"] == "flow"}
    return {row["sport"]: row["path"] for row in run["paths"] if row["record"] == "connection"}


def check_one(topology, cca, case):
    print(f"\n== {topology} / {cca} / {case} ==")
    runs = {mode: load(topology, cca, mode, case) for mode in MODES}
    if any(run is None for run in runs.values()):
        fail("missing run directory")
        return {}
    report = {}
    for mode in MODES:
        run = runs[mode]
        msg1, msg2 = run["msgs"].get(1), run["msgs"].get(2)
        if not msg1 or not msg2:
            fail(f"{mode}: flows.csv does not contain both payload messages")
            continue
        for label, msg in (("msg1", msg1), ("msg2", msg2)):
            if msg["completion_time_ns"] <= 0:
                fail(f"{mode}: {label} has no completion time")
        if not run["counters"].get("all_ranks_finished", 0):
            fail(f"{mode}: all_ranks_finished != 1")
        if run["counters"].get("messages_outstanding", 1) != 0:
            fail(f"{mode}: messages_outstanding = {run['counters'].get('messages_outstanding')}")
        if run["counters"].get("mmu_drops", 0) != 0:
            fail(f"{mode}: mmu_drops = {run['counters'].get('mmu_drops')}")
        end1, start2, between = message_records(run, mode)
        paths = path_rows(run, mode)
        report[mode] = {
            "msg1": {"qp_id": msg1["qp_id"], "sport": msg1["sport"], "conn_id": msg1["connection_id"],
                     "issue_ns": msg1["issue_time_ns"], "completion_ns": msg1["completion_time_ns"],
                     "path": paths.get(msg1["sport"], "")},
            "msg2": {"qp_id": msg2["qp_id"], "sport": msg2["sport"], "conn_id": msg2["connection_id"],
                     "issue_ns": msg2["issue_time_ns"], "start_ns": msg2["network_start_time_ns"],
                     "completion_ns": msg2["completion_time_ns"], "path": paths.get(msg2["sport"], "")},
            "end1": state_of(end1) if end1 else None,
            "start2": state_of(start2) if start2 else None,
            "end1_time_ns": end1["time_ns"] if end1 else None,
            "start2_time_ns": start2["time_ns"] if start2 else None,
            "between": [(r["time_ns"], r["event"], r["rate_bps"]) for r in between],
            "counters": {k: v for k, v in run["counters"].items() if k != "switch_ports"},
        }
        print(f"  [{mode}] msg1 qp={msg1['qp_id']} sport={msg1['sport']} conn={msg1['connection_id']} "
              f"path={paths.get(msg1['sport'], '?')}")
        print(f"  [{mode}] msg2 qp={msg2['qp_id']} sport={msg2['sport']} conn={msg2['connection_id']} "
              f"path={paths.get(msg2['sport'], '?')}")
        print(f"  [{mode}] end of msg1   @ {end1['time_ns'] if end1 else '?'} ns: {fmt_state(report[mode]['end1'])}")
        print(f"  [{mode}] start of msg2 @ {start2['time_ns'] if start2 else '?'} ns: {fmt_state(report[mode]['start2'])}")
        if between:
            kinds = {}
            for r in between:
                kinds[r["event"]] = kinds.get(r["event"], 0) + 1
            print(f"  [{mode}] events between: {kinds}")

        if end1 is None or start2 is None:
            fail(f"{mode}: MESSAGE_COMPLETE(msg1) or MESSAGE_START(msg2) missing from cca_trace.csv")
            continue

        decreases = [r for r in events_for(run["trace"], msg1["qp_id"]) if r["event"] == "RATE_DECREASE" and r["time_ns"] <= end1["time_ns"]]
        if cca == "dcqcn":
            if not decreases:
                fail(f"{mode}: message 1 produced no DCQCN rate decrease; the bottleneck did not bite")
            else:
                ok(f"{mode}: {len(decreases)} DCQCN rate decreases during message 1")
            if end1["rate_bps"] >= LINE_RATE:
                fail(f"{mode}: rate at end of msg1 is still line rate")
        else:
            updates = [r for r in events_for(run["trace"], msg1["qp_id"]) if r["event"] == "HPCC_UPDATE" and r["time_ns"] <= end1["time_ns"]]
            if not updates:
                fail(f"{mode}: message 1 produced no HPCC_UPDATE")
            else:
                ok(f"{mode}: {len(updates)} HPCC full-RTT updates during message 1")

        if mode == "legacy":
            if msg1["qp_id"] == msg2["qp_id"]:
                fail("legacy: msg1 and msg2 share a QP")
            else:
                ok("legacy: msg2 has a new QP")
            if msg1["sport"] == msg2["sport"]:
                fail("legacy: msg1 and msg2 share a source port")
            else:
                ok(f"legacy: new source port {msg1['sport']} -> {msg2['sport']}")
            # Fresh = line rate plus the active algorithm's own state at its
            # initial value. A DCQCN QP never initialises the HPCC fields and
            # vice versa, so only the relevant ones are checked.
            if cca == "dcqcn":
                fresh = start2["rate_bps"] == LINE_RATE and start2["target_rate_bps"] == LINE_RATE and \
                    abs(start2["alpha"] - 1.0) < 1e-12
            else:
                fresh = start2["rate_bps"] == LINE_RATE and start2["hp_cur_rate_bps"] == LINE_RATE
            if fresh:
                ok("legacy: msg2 starts from line-rate initial controller state")
            else:
                fail(f"legacy: msg2 initial state is not fresh: {fmt_state(state_of(start2))}")
        else:
            if msg1["qp_id"] != msg2["qp_id"] or msg1["sport"] != msg2["sport"] or msg1["connection_id"] != msg2["connection_id"]:
                fail("persistent: msg2 did not reuse msg1's connection/QP/port")
            else:
                ok(f"persistent: same connection {msg1['connection_id']}, QP {msg1['qp_id']}, port {msg1['sport']}")
            # state continuity: compare msg2's start with the most recent traced
            # state of the QP before it (end1, or the last timer event since).
            reference = between[-1] if between else end1
            same_rate = start2["rate_bps"] == reference["rate_bps"] and start2["target_rate_bps"] == reference["target_rate_bps"] \
                and start2["hp_cur_rate_bps"] == reference["hp_cur_rate_bps"]
            if same_rate:
                ok(f"persistent: msg2 starts from the controller's last traced state "
                   f"({'after %d intervening events' % len(between) if between else 'end of msg1'})")
            else:
                fail(f"persistent: msg2 start state {fmt_state(state_of(start2))} != reference {fmt_state(state_of(reference))}")
            # Events at exactly end1's timestamp belong to the ACK that
            # completed msg1 (CNP flag, HPCC update on the same ACK); they are
            # processed after the completion callback and are legitimate.
            later = [r for r in between if r["time_ns"] > end1["time_ns"]]
            if cca == "dcqcn":
                if start2["time_ns"] == end1["time_ns"]:
                    cnp_same_ack = any(r["event"] == "CNP_RECEIVED" for r in between)
                    if abs(start2["alpha"] - end1["alpha"]) > 1e-12 and not cnp_same_ack:
                        fail("persistent b2b: alpha changed with no time elapsed")
                    else:
                        ok("persistent: alpha unchanged across the back-to-back boundary")
                else:
                    cnp_between = any(r["event"] == "CNP_RECEIVED" for r in between)
                    if not cnp_between and start2["alpha"] > end1["alpha"] + 1e-12:
                        fail("persistent: alpha grew during an idle gap without CNPs")
                    else:
                        ok(f"persistent: alpha {end1['alpha']:.6f} -> {start2['alpha']:.6f} over {start2['time_ns'] - end1['time_ns']} ns idle")
                if case == "gap":
                    incs = [r for r in between if r["event"] == "RATE_INCREASE"]
                    if not incs:
                        fail("persistent gap: no DCQCN RATE_INCREASE fired during the idle gap")
                    elif start2["rate_bps"] < end1["rate_bps"]:
                        fail("persistent gap: rate did not recover during the idle gap")
                    else:
                        ok(f"persistent gap: {len(incs)} rate-increase timer events; rate "
                           f"{end1['rate_bps'] / 1e9:.3f}G -> {start2['rate_bps'] / 1e9:.3f}G")
                if case == "b2b" and start2["rate_bps"] == LINE_RATE and end1["rate_bps"] != LINE_RATE:
                    fail("persistent b2b: msg2 began at line rate although msg1 ended throttled")
            else:
                if later:
                    fail(f"persistent hpcc: unexpected events during idle: {[r['event'] for r in later]}")
                if start2["rate_bps"] != reference["rate_bps"] or start2["hp_cur_rate_bps"] != reference["hp_cur_rate_bps"]:
                    fail("persistent hpcc: rate/window state did not carry over")
                else:
                    ok("persistent hpcc: rate and reference rate carried over unchanged (HPCC has no idle timers)")
            if run["counters"].get("connections_created") != 1:
                fail(f"persistent: connections_created = {run['counters'].get('connections_created')} (expected 1)")

    # cross-mode path identity
    if report.get("legacy") and report.get("persistent"):
        lp = report["legacy"]
        pp = report["persistent"]
        print(f"  paths legacy: msg1={lp['msg1']['path']} msg2={lp['msg2']['path']}")
        print(f"  paths persistent: {pp['msg1']['path']}")
        if pp["msg1"]["path"] != pp["msg2"]["path"]:
            fail("persistent: path identity changed between messages")
        if topology == "ecmp":
            note = "differ" if lp["msg1"]["path"] != lp["msg2"]["path"] else "coincide for these two ports"
            print(f"  note: legacy flows on the ECMP topology {note}; persistent keeps one path by construction")
    return report


def main():
    summary = {}
    for topology in TOPOLOGIES:
        for cca in CCAS:
            for case in CASES:
                summary[f"{topology}__{cca}__{case}"] = check_one(topology, cca, case)
    out = os.path.join(ROOT, "results", "summary.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as handle:
        json.dump(summary, handle, indent=2, default=str)
        handle.write("\n")
    print(f"\nwrote {out}")
    marker = os.path.join(ROOT, "results", "PASSED")
    if failures:
        if os.path.exists(marker):
            os.remove(marker)
        print(f"\n{len(failures)} check(s) failed:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    with open(marker, "w") as handle:
        handle.write("persistent-qp microbenchmark passed\n")
    print("\npersistent-qp microbenchmark: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
