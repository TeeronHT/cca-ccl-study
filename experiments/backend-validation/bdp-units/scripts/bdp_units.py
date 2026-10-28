#!/usr/bin/env python3
"""BDP / time-unit / headroom sanity test against the backend's own numbers.

For each (rate, one-way delay) case the script
  1. computes the analytic one-way BDP, pair RTT, RTT-BDP and PFC headroom
     with the backend's documented conventions (ns time steps, bit/s rates,
     integer division as in scratch/common.h);
  2. optionally runs the simulator on a 2-host, 1-switch topology whose two
     links both have that rate and delay, with LINK_REPORT_FILE and
     PFC_THRESHOLD_VALIDATION STRICT, and compares the report line by line;
  3. checks that STRICT aborted exactly when headroom + reserve >= buffer.

Usage:
  bdp_units.py --analytic            print the analytic table only
  bdp_units.py --prepare             write topologies, workloads and configs
  bdp_units.py --compare             read results/raw/<case>/ and compare
"""

import csv
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, str(Path(ROOT).parents[1] / "common"))
import backend_outputs as bo  # noqa: E402
import chakra_et  # noqa: E402
import netconfig  # noqa: E402

E0_CONFIGS = os.path.abspath(
    os.path.join(ROOT, "..", "..", "hoti22-reproduction", "e0-incast", "configs")
)
CASES = [
    ("200G-500ns", 200_000_000_000, 500),
    ("100G-1ms", 100_000_000_000, 1_000_000),
    ("100G-1.842ms", 100_000_000_000, 1_842_000),
    ("50G-500us", 50_000_000_000, 500_000),
]
PACKET_PAYLOAD = 1000
HEADROOM_FACTOR = 3
BUFFER_MIB = 32
RESERVE_PER_PORT = 4 * 1024
MESSAGE_BYTES = 1024 * 1024
PORTS_PER_SWITCH = 2


def analytic(rate_bps, delay_ns):
    """Mirror of the integer arithmetic in scratch/common.h."""
    one_way_bdp = rate_bps * delay_ns // 8 // 1_000_000_000
    headroom = one_way_bdp * HEADROOM_FACTOR
    tx_per_hop = PACKET_PAYLOAD * 1_000_000_000 * 8 // rate_bps
    rtt = 2 * (2 * delay_ns) + 2 * tx_per_hop
    rtt_bdp = rtt * rate_bps // 1_000_000_000 // 8
    buffer_bytes = BUFFER_MIB * 1024 * 1024
    committed = PORTS_PER_SWITCH * headroom + PORTS_PER_SWITCH * RESERVE_PER_PORT
    return {
        "rate_bps": rate_bps,
        "delay_ns": delay_ns,
        "one_way_bdp_bits": rate_bps * delay_ns // 1_000_000_000,
        "one_way_bdp_bytes": one_way_bdp,
        "headroom_bytes": headroom,
        "pair_rtt_ns": rtt,
        "pair_rtt_bdp_bytes": rtt_bdp,
        "buffer_bytes": buffer_bytes,
        "committed_bytes": committed,
        "pfc_config_valid": committed < buffer_bytes,
    }


def print_analytic():
    print(f"{'case':<14}{'rate':>8}{'delay_ns':>10}{'1-way BDP':>14}{'headroom x3':>14}{'RTT ns':>12}{'RTT BDP':>12}{'PFC ok':>8}")
    for name, rate, delay in CASES:
        a = analytic(rate, delay)
        print(f"{name:<14}{rate / 1e9:>7.0f}G{delay:>10}{a['one_way_bdp_bytes']:>14}{a['headroom_bytes']:>14}"
              f"{a['pair_rtt_ns']:>12}{a['pair_rtt_bdp_bytes']:>12}{str(a['pfc_config_valid']):>8}")
    print("1-way BDP = rate x delay / 8 / 1e9 bytes (integer division, as the backend does); "
          "headroom = 1-way BDP x 3; RTT = 4 x delay + 2 x 8000/rate ns; "
          f"PFC ok means 2 x headroom + 2 x {RESERVE_PER_PORT} < {BUFFER_MIB} MiB.")


def prepare():
    os.makedirs(os.path.join(ROOT, "configs"), exist_ok=True)
    workload = os.path.join(ROOT, "workload")
    os.makedirs(workload, exist_ok=True)
    chakra_et.write_trace(os.path.join(workload, "trace.0.et"),
                          [chakra_et.send_node(1, "probe", 0, 1, MESSAGE_BYTES, 1)])
    chakra_et.write_trace(os.path.join(workload, "trace.1.et"),
                          [chakra_et.recv_node(1, "probe", 0, 1, MESSAGE_BYTES, 1)])
    for name, path in (("flow.txt", "0\n"), ("trace.txt", "1\n2\n")):
        with open(os.path.join(ROOT, "configs", name), "w") as handle:
            handle.write(path)
    with open(os.path.join(ROOT, "configs", "logical.json"), "w") as handle:
        json.dump({"logical-dims": ["2"]}, handle, indent=4)
        handle.write("\n")
    with open(os.path.join(ROOT, "configs", "memory.json"), "w") as handle:
        json.dump({"memory-type": "NO_MEMORY_EXPANSION"}, handle, indent=4)
        handle.write("\n")
    e0_system = json.load(open(os.path.join(E0_CONFIGS, "system.json")))
    with open(os.path.join(ROOT, "configs", "system.json"), "w") as handle:
        json.dump(e0_system, handle, indent=4)
        handle.write("\n")
    base = netconfig.as_dict(os.path.join(E0_CONFIGS, "dcqcn.txt"))
    # ECN entries for every rate used here; 50 Gbps follows the proportional
    # rule from the 200 Gbps entry (this test does not exercise marking).
    kmin = netconfig.parse_map(base["KMIN_MAP"])
    kmax = netconfig.parse_map(base["KMAX_MAP"])
    pmax = netconfig.parse_map(base["PMAX_MAP"])
    kmin[50_000_000_000] = str(int(kmin[200_000_000_000]) * 50 // 200)
    kmax[50_000_000_000] = str(int(kmax[200_000_000_000]) * 50 // 200)
    pmax[50_000_000_000] = pmax[200_000_000_000]
    for name, rate, delay in CASES:
        run_dir = os.path.join(ROOT, "results", "raw", name)
        os.makedirs(run_dir, exist_ok=True)
        gbps = rate // 1_000_000_000
        with open(os.path.join(run_dir, "topology.txt"), "w") as handle:
            handle.write("3 1 2\n2\n")
            handle.write(f"0 2 {gbps}Gbps {delay}ns 0\n")
            handle.write(f"2 1 {gbps}Gbps {delay}ns 0\n")
        rel = os.path.relpath(run_dir, ROOT)
        overrides = {
            "TOPOLOGY_FILE": f"{rel}/topology.txt",
            "FLOW_FILE": "configs/flow.txt",
            "TRACE_FILE": "configs/trace.txt",
            "TRACE_OUTPUT_FILE": f"{rel}/mix.tr",
            "FCT_OUTPUT_FILE": f"{rel}/fct.txt",
            "PFC_OUTPUT_FILE": f"{rel}/pfc.txt",
            "QLEN_MON_FILE": f"{rel}/qlen.txt",
            "KMIN_MAP": netconfig.format_map(kmin),
            "KMAX_MAP": netconfig.format_map(kmax),
            "PMAX_MAP": netconfig.format_map(pmax),
            "BUFFER_SIZE": str(BUFFER_MIB),
            "HEADROOM_FACTOR": str(HEADROOM_FACTOR),
            "PFC_THRESHOLD_VALIDATION": "STRICT",
            "TRACE_LEVEL": "0",
            "LINK_REPORT_FILE": f"{rel}/link_report.txt",
            "COUNTERS_OUTPUT_FILE": f"{rel}/counters.txt",
        }
        netconfig.rewrite(os.path.join(E0_CONFIGS, "dcqcn.txt"), os.path.join(run_dir, "network.txt"), overrides)
        with open(os.path.join(run_dir, "expected.json"), "w") as handle:
            json.dump(analytic(rate, delay), handle, indent=2)
            handle.write("\n")
    print("prepared", len(CASES), "cases under results/raw/")


def compare():
    rows = []
    failures = 0
    for name, rate, delay in CASES:
        run_dir = os.path.join(ROOT, "results", "raw", name)
        expected = analytic(rate, delay)
        report = bo.read_link_report(os.path.join(run_dir, "link_report.txt"))
        status_path = os.path.join(run_dir, "exit_status.txt")
        exit_status = int(open(status_path).read().strip()) if os.path.exists(status_path) else None
        stdout = open(os.path.join(run_dir, "stdout.log"), errors="replace").read() if os.path.exists(os.path.join(run_dir, "stdout.log")) else ""
        row = {"case": name, "configured_rate_bps": rate, "configured_one_way_delay_ns": delay,
               "internal_time_unit": "ns (Time::GetTimeStep, default resolution)",
               "expected_one_way_bdp_bytes": expected["one_way_bdp_bytes"],
               "expected_headroom_bytes": expected["headroom_bytes"],
               "expected_pair_rtt_ns": expected["pair_rtt_ns"],
               "expected_pair_rtt_bdp_bytes": expected["pair_rtt_bdp_bytes"],
               "expected_pfc_valid": expected["pfc_config_valid"],
               "backend_delay_ns": "", "backend_one_way_bdp_bytes": "", "backend_headroom_bytes": "",
               "backend_pair_rtt_ns": "", "backend_pair_rtt_bdp_bytes": "", "backend_pfc_valid": "",
               "strict_exit_status": exit_status, "result": ""}
        problems = []
        if not report["links"]:
            problems.append("no link report (binary not run?)")
        else:
            link = report["links"][0]
            row["backend_delay_ns"] = link["delay_ns"]
            row["backend_one_way_bdp_bytes"] = link["one_way_bdp_bytes"]
            if link["delay_ns"] != delay:
                problems.append(f"delay {link['delay_ns']} != {delay}")
            if link["rate_bps"] != rate:
                problems.append(f"rate {link['rate_bps']} != {rate}")
            if link["one_way_bdp_bytes"] != expected["one_way_bdp_bytes"]:
                problems.append(f"bdp {link['one_way_bdp_bytes']} != {expected['one_way_bdp_bytes']}")
            ports = report["ports"]
            if ports:
                row["backend_headroom_bytes"] = ports[0]["headroom_bytes"]
                if any(p["headroom_bytes"] != expected["headroom_bytes"] for p in ports):
                    problems.append(f"headroom {[p['headroom_bytes'] for p in ports]} != {expected['headroom_bytes']}")
            if report["switches"]:
                sw = report["switches"][0]
                row["backend_pfc_valid"] = bool(sw["pfc_config_valid"])
                if bool(sw["pfc_config_valid"]) != expected["pfc_config_valid"]:
                    problems.append("backend validity flag disagrees with analytic one")
                if sw["buffer_bytes"] != expected["buffer_bytes"]:
                    problems.append(f"buffer {sw['buffer_bytes']} != {expected['buffer_bytes']}")
            pairs = [p for p in report["pairs"] if p["src"] == 0 and p["dst"] == 1]
            if pairs:
                row["backend_pair_rtt_ns"] = pairs[0]["rtt_ns"]
                row["backend_pair_rtt_bdp_bytes"] = pairs[0]["rtt_bdp_bytes"]
                if pairs[0]["rtt_ns"] != expected["pair_rtt_ns"]:
                    problems.append(f"rtt {pairs[0]['rtt_ns']} != {expected['pair_rtt_ns']}")
                if pairs[0]["rtt_bdp_bytes"] != expected["pair_rtt_bdp_bytes"]:
                    problems.append(f"rtt bdp {pairs[0]['rtt_bdp_bytes']} != {expected['pair_rtt_bdp_bytes']}")
            elif expected["pfc_config_valid"]:
                problems.append("valid configuration but no pair line (run did not reach routing)")
        if exit_status is not None:
            aborted = exit_status != 0
            if expected["pfc_config_valid"] and aborted:
                problems.append(f"STRICT aborted a valid configuration (exit {exit_status})")
            if not expected["pfc_config_valid"]:
                if not aborted:
                    problems.append("STRICT did not abort an invalid configuration")
                elif "INVALID PFC CONFIGURATION" not in stdout:
                    problems.append("abort without the INVALID PFC CONFIGURATION report")
                else:
                    for needle in ("switch 2", "port 1", "port 2", "headroom", "buffer"):
                        if needle not in stdout:
                            problems.append(f"abort message lacks '{needle}'")
            if expected["pfc_config_valid"] and not aborted:
                counters = bo.read_counters(os.path.join(run_dir, "counters.txt"))
                if counters.get("messages_completed") != 1:
                    problems.append("valid case did not complete its probe message")
        row["result"] = "PASS" if not problems else "FAIL: " + "; ".join(problems)
        if problems:
            failures += 1
        rows.append(row)
        print(f"{name}: {row['result']}")
        print(f"    rate={rate} delay_ns={delay} internal_delay={row['backend_delay_ns']} unit=ns")
        print(f"    one-way BDP expected={expected['one_way_bdp_bytes']} backend={row['backend_one_way_bdp_bytes']} "
              f"(= {expected['one_way_bdp_bits']} bits)")
        print(f"    headroom expected={expected['headroom_bytes']} backend={row['backend_headroom_bytes']}")
        print(f"    pair RTT expected={expected['pair_rtt_ns']} backend={row['backend_pair_rtt_ns']}; "
              f"RTT-BDP expected={expected['pair_rtt_bdp_bytes']} backend={row['backend_pair_rtt_bdp_bytes']}")
        print(f"    PFC config valid expected={expected['pfc_config_valid']} backend={row['backend_pfc_valid']} "
              f"strict_exit={exit_status}")
    out = os.path.join(ROOT, "results", "bdp_units.csv")
    with open(out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out}")
    marker = os.path.join(ROOT, "results", "PASSED")
    if failures:
        if os.path.exists(marker):
            os.remove(marker)
        return 1
    with open(marker, "w") as handle:
        handle.write("bdp-units passed\n")
    return 0


def main():
    if "--analytic" in sys.argv:
        print_analytic()
        return 0
    if "--prepare" in sys.argv:
        prepare()
        return 0
    if "--compare" in sys.argv:
        return compare()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
