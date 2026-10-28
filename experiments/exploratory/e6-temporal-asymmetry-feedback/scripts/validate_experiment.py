#!/usr/bin/env python3
"""Check E6 files. Does not start the simulator."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model

PATH_KEYS = {
    "TOPOLOGY_FILE",
    "FLOW_FILE",
    "TRACE_FILE",
    "TRACE_OUTPUT_FILE",
    "FCT_OUTPUT_FILE",
    "PFC_OUTPUT_FILE",
    "QLEN_MON_FILE",
}
CCAS = {"pfc": "pfc-only.txt", "dcqcn": "dcqcn.txt", "hpcc": "hpcc.txt"}


def fail(message):
    print(f"INVALID: {message}", file=sys.stderr)
    sys.exit(1)


def parse_config(path):
    values = {}
    for line in open(path):
        line = line.strip()
        if line:
            key, rest = line.split(" ", 1)
            values[key] = rest
    return values


def check_topology():
    physical = os.path.join(model.root(), "configs", "physical")
    expect = {
        "clos-2to1.txt": (22, 6, 24, 2),
        "clos-1to1-control.txt": (24, 8, 32, 4),
    }
    for name, (nodes, switches, links, spines) in expect.items():
        path = os.path.join(physical, name)
        if not os.path.exists(path):
            fail(f"missing {name}")
        rows = open(path).read().splitlines()
        header = list(map(int, rows[0].split()))
        if header != [nodes, switches, links]:
            fail(f"{name} header {rows[0]}")
        body = [line.split() for line in rows[2:]]
        if any(item[2] != "200Gbps" or item[3] != "500ns" or item[4] != "0" for item in body):
            fail(f"{name} link is not 200Gbps 500ns")
        tors = range(16, 20)
        spine_ids = range(20, 20 + spines)
        up = {tor: 0 for tor in tors}
        for src, dst, *_rest in body:
            if int(src) in up and int(dst) in spine_ids:
                up[int(src)] += 1
        if any(count != spines for count in up.values()):
            fail(f"{name} uplink counts {up}")
    print("topologies: 16 GPUs, 2:1 and 1:1, every link 200Gbps 500ns")


REQUIRED_EXTENSIONS = {
    "TRANSPORT_CONNECTION_MODE": "PERSISTENT",
    "PFC_THRESHOLD_VALIDATION": "STRICT",
    "ENABLE_FLOW_METADATA_TRACE": "1",
    "ENABLE_CCA_TRACE": "1",
    "ENABLE_PATH_TRACE": "1",
}


def check_cca():
    e0 = os.path.abspath(
        os.path.join(model.root(), "..", "..", "hoti22-reproduction", "e0-incast", "configs")
    )
    for token, filename in CCAS.items():
        ours = parse_config(os.path.join(model.root(), "configs", "cca", filename))
        canon = parse_config(os.path.join(e0, filename))
        for key, value in canon.items():
            if key in PATH_KEYS:
                continue
            if ours.get(key) != value:
                fail(f"{filename} {key} differs from E0")
        for key, value in REQUIRED_EXTENSIONS.items():
            if ours.get(key) != value:
                fail(f"{filename} {key}={ours.get(key)}; E6 requires {value}")
        if ours["BUFFER_SIZE"] != "32":
            fail("buffer is not 32 MiB")
    if parse_config(os.path.join(model.root(), "configs", "cca", "hpcc.txt"))["HAS_WIN"] != "1":
        fail("HPCC HAS_WIN")
    if parse_config(os.path.join(model.root(), "configs", "cca", "dcqcn.txt"))["HAS_WIN"] != "0":
        fail("DCQCN HAS_WIN")
    if open(os.path.join(model.root(), "configs", "flow.txt")).read().strip() != "0":
        fail("background flows")
    print("cca: E0 keys preserved; HPCC HAS_WIN 1; DCQCN HAS_WIN 0; PERSISTENT connections; traces on")


def run_ids():
    ids = []
    for schedule in ("ring", "stragglar"):
        for cca in CCAS:
            ids.append(f"2to1__symmetric__{schedule}__{cca}")
            for token in ("tau0.25", "tau0.50", "tau1.00"):
                ids.append(f"2to1__one-late__{token}__{schedule}__{cca}")
                ids.append(f"2to1__two-late__{token}__{schedule}__{cca}")
            ids.append(f"1to1__one-late__tau0.50__{schedule}__{cca}")
    return ids


def check_ids():
    ids = run_ids()
    if len(ids) != 48 or len(set(ids)) != 48:
        fail(f"run ids {len(ids)}")
    text = open(os.path.join(model.root(), "scripts", "run_one.sh")).read()
    if "rendezvous-protocol" not in text:
        fail("run_one.sh does not enable rendezvous")
    if "STATE_SNAPSHOT_TIMES" not in text:
        fail("run_one.sh does not request the backend snapshot at tau")
    note = open(os.path.join(model.root(), "schedules", "ring", "mechanism.txt")).read()
    if "transport_connection_mode=PERSISTENT" not in note:
        fail("ring mechanism note does not record PERSISTENT")
    print("run ids: 48; rendezvous is on; snapshot at tau requested")


def check_full():
    arrivals = os.path.join(model.root(), "results", "arrivals.json")
    if not os.path.exists(arrivals):
        fail("results/arrivals.json is missing")
    document = json.load(open(arrivals))
    if len(document["cases"]) != 7:
        fail("expected 7 arrival cases")
    for case in document["cases"]:
        for schedule in ("ring", "stragglar"):
            directory = os.path.join(
                model.root(),
                "workload",
                f"{schedule}__{case['pattern']}__{case['tau_token']}",
            )
            if not os.path.exists(os.path.join(directory, "trace.15.et")):
                fail(f"missing traces in {directory}")
    print("workloads: both schedules, all seven arrival cases")


def main():
    check_topology()
    check_cca()
    check_ids()
    if "--full" in sys.argv:
        check_full()
    print("E6 invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
