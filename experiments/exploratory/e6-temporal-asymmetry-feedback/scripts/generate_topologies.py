#!/usr/bin/env python3
"""1:1 control and 2:1 primary fabrics, from the E3 generator."""

import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model

E3 = os.path.abspath(
    os.path.join(model.root(), "..", "e3-ccl-cca-oversubscription", "scripts", "generate_topologies.py")
)


def main():
    spec = importlib.util.spec_from_file_location("e3_topologies", E3)
    e3 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e3)
    directory = os.path.join(model.root(), "configs", "physical")
    os.makedirs(directory, exist_ok=True)
    for name, spines in (("2to1", 2), ("1to1-control", 4)):
        links = e3.write_fabric(directory, name, spines)
        print(f"{name}: spines={spines} links={links}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
