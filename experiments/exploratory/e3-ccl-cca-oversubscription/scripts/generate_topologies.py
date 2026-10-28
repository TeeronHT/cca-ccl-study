#!/usr/bin/env python3
"""Write the three Clos fabrics. Oversubscription is spine count only."""

import json
import os

N_GPU = 16
GPUS_PER_RACK = 4
N_TOR = 4
TOR0 = N_GPU
FABRICS = {"1to1": 4, "2to1": 2, "4to1": 1}
RATE = "200Gbps"
DELAY = "500ns"


def build(n_spine):
    spine0 = TOR0 + N_TOR
    switches = list(range(TOR0, spine0 + n_spine))
    links = []
    for gpu in range(N_GPU):
        links.append((gpu, TOR0 + gpu // GPUS_PER_RACK))
    for tor_i in range(N_TOR):
        for spine_i in range(n_spine):
            links.append((TOR0 + tor_i, spine0 + spine_i))
    ports = {"tor_uplinks": {}, "spine_ports": {}, "tor_downlinks": {}}
    for tor_i in range(N_TOR):
        tor = TOR0 + tor_i
        # Loopback is ifIndex 0. GPU devices are installed first, then spines.
        ports["tor_downlinks"][str(tor)] = list(range(1, 1 + GPUS_PER_RACK))
        ports["tor_uplinks"][str(tor)] = list(
            range(1 + GPUS_PER_RACK, 1 + GPUS_PER_RACK + n_spine)
        )
    for spine_i in range(n_spine):
        ports["spine_ports"][str(spine0 + spine_i)] = list(range(1, 1 + N_TOR))
    return switches, links, ports, spine0


def write_fabric(directory, name, n_spine):
    switches, links, ports, spine0 = build(n_spine)
    topo_path = os.path.join(directory, f"clos-{name}.txt")
    with open(topo_path, "w") as handle:
        handle.write(f"{N_GPU + len(switches)} {len(switches)} {len(links)}\n")
        handle.write(" ".join(str(switch) for switch in switches) + "\n")
        for src, dst in links:
            handle.write(f"{src} {dst} {RATE} {DELAY} 0\n")
    trace_path = os.path.join(directory, f"trace-{name}.txt")
    with open(trace_path, "w") as handle:
        handle.write(f"{len(switches)}\n")
        for switch in switches:
            handle.write(f"{switch}\n")
    ports["tor0"] = TOR0
    ports["n_tor"] = N_TOR
    ports["spine0"] = spine0
    ports["n_spine"] = n_spine
    ports["n_gpu"] = N_GPU
    with open(os.path.join(directory, f"ports-{name}.json"), "w") as handle:
        json.dump(ports, handle, indent=2)
        handle.write("\n")
    return len(links)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    directory = os.path.join(here, "..", "configs", "physical")
    os.makedirs(directory, exist_ok=True)
    for name, n_spine in FABRICS.items():
        n_links = write_fabric(directory, name, n_spine)
        print(f"{name}: spines={n_spine} links={n_links}")


if __name__ == "__main__":
    main()
