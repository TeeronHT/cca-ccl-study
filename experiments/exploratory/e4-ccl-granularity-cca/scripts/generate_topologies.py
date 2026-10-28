#!/usr/bin/env python3
"""Write the 1:1 and 2:1 fabrics using Experiment 3's generator."""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
E3 = os.path.abspath(
    os.path.join(HERE, "..", "..", "e3-ccl-cca-oversubscription", "scripts", "generate_topologies.py")
)


def main():
    spec = importlib.util.spec_from_file_location("e3_topologies", E3)
    e3 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e3)
    directory = os.path.join(HERE, "..", "configs", "physical")
    os.makedirs(directory, exist_ok=True)
    for name in ("1to1", "2to1"):
        n_links = e3.write_fabric(directory, name, e3.FABRICS[name])
        print(f"{name}: spines={e3.FABRICS[name]} links={n_links}")


if __name__ == "__main__":
    main()
