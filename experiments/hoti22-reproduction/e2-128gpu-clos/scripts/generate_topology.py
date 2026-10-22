#!/usr/bin/env python3
"""Write the 128-GPU two-level Clos used by Experiment 2.

8 GPUs per server, 2 servers per rack, 8 racks. Each rack is one ToR.
Each ToR has 16x200 Gbps down to GPUs. One interface is stored per
neighbor pair, so 1:1 uplink bandwidth is 16 spine switches at 200 Gbps,
not two parallel links onto 8 spines.
"""

import os

N_GPU = 128
GPUS_PER_RACK = 16
N_TOR = N_GPU // GPUS_PER_RACK
N_SPINE = GPUS_PER_RACK  # 1:1 with one 200 Gbps link per spine
TOR0 = N_GPU
SPINE0 = TOR0 + N_TOR


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    config = os.path.join(here, "..", "configs")
    os.makedirs(config, exist_ok=True)
    switches = list(range(TOR0, SPINE0 + N_SPINE))
    links = []
    for gpu in range(N_GPU):
        links.append((gpu, TOR0 + gpu // GPUS_PER_RACK))
    for tor in range(N_TOR):
        for spine in range(N_SPINE):
            links.append((TOR0 + tor, SPINE0 + spine))

    topo = os.path.join(config, "topology.txt")
    with open(topo, "w") as handle:
        handle.write(f"{N_GPU + N_TOR + N_SPINE} {N_TOR + N_SPINE} {len(links)}\n")
        handle.write(" ".join(str(switch) for switch in switches) + "\n")
        for src, dst in links:
            handle.write(f"{src} {dst} 200Gbps 500ns 0\n")

    trace = os.path.join(config, "trace.txt")
    with open(trace, "w") as handle:
        handle.write(f"{len(switches)}\n")
        for switch in switches:
            handle.write(f"{switch}\n")
    print(f"nodes={N_GPU + N_TOR + N_SPINE} tors={TOR0}-{SPINE0 - 1} spines={SPINE0}-{SPINE0 + N_SPINE - 1} links={len(links)}")


if __name__ == "__main__":
    main()
