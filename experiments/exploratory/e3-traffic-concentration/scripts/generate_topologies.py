#!/usr/bin/env python3
"""Three fabrics that differ only in ToR->spine bitrate.

16 GPUs, 4 ToRs, 4 spines, and 4 equal-cost uplinks per ToR in every case.
GPU->ToR stays 200 Gbps. Propagation delay stays 500 ns on every link.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402


def build(uplink_rate):
    switches = list(range(model.TOR0, model.SPINE0 + model.N_SPINE))
    links = []
    for gpu in range(model.N_GPU):
        links.append((gpu, model.TOR0 + gpu // model.GPUS_PER_RACK, model.GPU_RATE))
    for tor_i in range(model.N_TOR):
        for spine_i in range(model.N_SPINE):
            links.append((model.TOR0 + tor_i, model.SPINE0 + spine_i, uplink_rate))
    ports = {"tor_uplinks": {}, "tor_downlinks": {}, "spine_ports": {}}
    for tor_i in range(model.N_TOR):
        tor = model.TOR0 + tor_i
        # Loopback is ifIndex 0. GPUs are installed first, then the four spines.
        ports["tor_downlinks"][str(tor)] = list(range(1, 1 + model.GPUS_PER_RACK))
        ports["tor_uplinks"][str(tor)] = list(
            range(1 + model.GPUS_PER_RACK, 1 + model.GPUS_PER_RACK + model.N_SPINE)
        )
    for spine_i in range(model.N_SPINE):
        ports["spine_ports"][str(model.SPINE0 + spine_i)] = list(range(1, 1 + model.N_TOR))
    ports["tor0"] = model.TOR0
    ports["n_tor"] = model.N_TOR
    ports["spine0"] = model.SPINE0
    ports["n_spine"] = model.N_SPINE
    ports["n_gpu"] = model.N_GPU
    ports["uplink_rate"] = uplink_rate
    return switches, links, ports


def write_fabric(directory, name, uplink_rate):
    switches, links, ports = build(uplink_rate)
    topo_path = os.path.join(directory, f"clos-{name}.txt")
    with open(topo_path, "w") as handle:
        handle.write(f"{model.N_GPU + len(switches)} {len(switches)} {len(links)}\n")
        handle.write(" ".join(str(switch) for switch in switches) + "\n")
        for src, dst, rate in links:
            handle.write(f"{src} {dst} {rate} {model.DELAY} 0\n")
    with open(os.path.join(directory, f"trace-{name}.txt"), "w") as handle:
        handle.write(f"{len(switches)}\n")
        for switch in switches:
            handle.write(f"{switch}\n")
    with open(os.path.join(directory, f"ports-{name}.json"), "w") as handle:
        json.dump(ports, handle, indent=2)
        handle.write("\n")
    return len(links)


def main():
    directory = os.path.join(model.root(), "configs", "physical")
    os.makedirs(directory, exist_ok=True)
    for name in model.FABRICS:
        rate, _bps = model.FABRIC_UPLINK[name]
        n_links = write_fabric(directory, name, rate)
        print(f"{name}: spines={model.N_SPINE} uplink={rate} links={n_links}")


if __name__ == "__main__":
    main()
