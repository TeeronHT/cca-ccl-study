#!/usr/bin/env python3
"""Topologies, workloads and network configs for the persistent-QP microbenchmark.

Sender A (node 0) sends message 1 then message 2 to receiver B (node 1).
Two topologies:
  bottleneck  A -200G- S -100G- B            one path, S->B is the bottleneck
  ecmp        A -200G- T1 =200G= {S1,S2} =200G= T2 -100G- B   two equal paths at T1
Two arrival cases:
  b2b   message 2 depends on message 1 (posted as soon as 1 completes)
  gap   message 2 depends on a GAP_US compute node that depends on message 1
"""

import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, str(Path(ROOT).parents[1] / "common"))
import chakra_et  # noqa: E402
import netconfig  # noqa: E402

E0_CONFIGS = os.path.abspath(
    os.path.join(ROOT, "..", "..", "hoti22-reproduction", "e0-incast", "configs")
)
MSG1_BYTES = 16 * 1024 * 1024
MSG2_BYTES = 4 * 1024 * 1024
GAP_US = 2000
CASES = ("b2b", "gap")
TOPOLOGIES = {
    "bottleneck": {
        "switches": [2],
        "links": [(0, 2, "200Gbps"), (2, 1, "100Gbps")],
    },
    "ecmp": {
        "switches": [2, 3, 4, 5],
        "links": [
            (0, 2, "200Gbps"),
            (2, 4, "200Gbps"),
            (2, 5, "200Gbps"),
            (4, 3, "200Gbps"),
            (5, 3, "200Gbps"),
            (3, 1, "100Gbps"),
        ],
    },
}
CCAS = {"dcqcn": "dcqcn.txt", "hpcc": "hpcc.txt"}
MODES = ("LEGACY", "PERSISTENT")


def write_topologies():
    directory = os.path.join(ROOT, "configs")
    os.makedirs(directory, exist_ok=True)
    for name, spec in TOPOLOGIES.items():
        nodes = 2 + len(spec["switches"])
        with open(os.path.join(directory, f"topology-{name}.txt"), "w") as handle:
            handle.write(f"{nodes} {len(spec['switches'])} {len(spec['links'])}\n")
            handle.write(" ".join(map(str, spec["switches"])) + "\n")
            for src, dst, rate in spec["links"]:
                handle.write(f"{src} {dst} {rate} 500ns 0\n")
        with open(os.path.join(directory, f"trace-{name}.txt"), "w") as handle:
            handle.write(f"{len(spec['switches'])}\n")
            for switch in spec["switches"]:
                handle.write(f"{switch}\n")
    with open(os.path.join(directory, "flow.txt"), "w") as handle:
        handle.write("0\n")
    with open(os.path.join(directory, "logical.json"), "w") as handle:
        json.dump({"logical-dims": ["2"]}, handle, indent=4)
        handle.write("\n")
    with open(os.path.join(directory, "memory.json"), "w") as handle:
        json.dump({"memory-type": "NO_MEMORY_EXPANSION"}, handle, indent=4)
        handle.write("\n")
    system = {
        "scheduling-policy": "FIFO",
        "endpoint-delay": 0,
        "active-chunks-per-dimension": 1,
        "preferred-dataset-splits": 1,
        "all-reduce-implementation": ["ring"],
        "all-gather-implementation": ["ring"],
        "reduce-scatter-implementation": ["ring"],
        "all-to-all-implementation": ["ring"],
        "collective-optimization": "localBWAware",
        "local-mem-bw": 1600,
        "boost-mode": 0,
        "roofline-enabled": 0,
        "peak-perf": 900,
    }
    with open(os.path.join(directory, "system.json"), "w") as handle:
        json.dump(system, handle, indent=4)
        handle.write("\n")


def write_workloads():
    for case in CASES:
        directory = os.path.join(ROOT, "workload", case)
        os.makedirs(directory, exist_ok=True)
        meta1 = [chakra_et.attr_u64("comm_chunk_id", 1), chakra_et.attr_u64("comm_phase_id", 1)]
        meta2 = [chakra_et.attr_u64("comm_chunk_id", 2), chakra_et.attr_u64("comm_phase_id", 2)]
        sender = [chakra_et.send_node(1, "msg1", 0, 1, MSG1_BYTES, 1, [], meta1)]
        if case == "b2b":
            sender.append(chakra_et.send_node(2, "msg2", 0, 1, MSG2_BYTES, 2, [1], meta2))
        else:
            sender.append(chakra_et.comp_node(2, "idle-gap", GAP_US, [1]))
            sender.append(chakra_et.send_node(3, "msg2", 0, 1, MSG2_BYTES, 2, [2], meta2))
        receiver = [
            chakra_et.recv_node(1, "recv1", 0, 1, MSG1_BYTES, 1),
            chakra_et.recv_node(2, "recv2", 0, 1, MSG2_BYTES, 2),
        ]
        chakra_et.write_trace(os.path.join(directory, "trace.0.et"), sender)
        chakra_et.write_trace(os.path.join(directory, "trace.1.et"), receiver)


def run_id(topology, cca, mode, case):
    return f"{topology}__{cca}__{mode.lower()}__{case}"


def write_network_config(topology, cca, mode, case, run_dir):
    rel = os.path.relpath(run_dir, ROOT)
    overrides = {
        "TOPOLOGY_FILE": f"configs/topology-{topology}.txt",
        "FLOW_FILE": "configs/flow.txt",
        "TRACE_FILE": f"configs/trace-{topology}.txt",
        "TRACE_OUTPUT_FILE": f"{rel}/mix.tr",
        "FCT_OUTPUT_FILE": f"{rel}/fct.txt",
        "PFC_OUTPUT_FILE": f"{rel}/pfc.txt",
        "QLEN_MON_FILE": f"{rel}/qlen.txt",
        "TRANSPORT_CONNECTION_MODE": mode,
        "PFC_THRESHOLD_VALIDATION": "STRICT",
        "QUEUE_LOG_MIN_BYTES": "0",
        "TRACE_LEVEL": "2",
        "ENABLE_CCA_TRACE": "2",
        "CCA_TRACE_FILE": f"{rel}/cca_trace.csv",
        "ENABLE_FLOW_METADATA_TRACE": "1",
        "FLOW_TRACE_FILE": f"{rel}/flows.csv",
        "ENABLE_PATH_TRACE": "1",
        "PATH_TRACE_FILE": f"{rel}/paths.csv",
        "COUNTERS_OUTPUT_FILE": f"{rel}/counters.txt",
        "LINK_REPORT_FILE": f"{rel}/link_report.txt",
    }
    netconfig.rewrite(os.path.join(E0_CONFIGS, CCAS[cca]), os.path.join(run_dir, "network.txt"), overrides)


def main():
    write_topologies()
    write_workloads()
    if len(sys.argv) == 6 and sys.argv[1] == "config":
        _, _, topology, cca, mode, case = sys.argv
        run_dir = os.path.join(ROOT, "results", "raw", run_id(topology, cca, mode, case))
        os.makedirs(run_dir, exist_ok=True)
        write_network_config(topology, cca, mode, case, run_dir)
        print(run_dir)
        return 0
    print(f"topologies: {', '.join(TOPOLOGIES)}; cases: {', '.join(CASES)}; "
          f"msg1={MSG1_BYTES} msg2={MSG2_BYTES} gap_us={GAP_US}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
