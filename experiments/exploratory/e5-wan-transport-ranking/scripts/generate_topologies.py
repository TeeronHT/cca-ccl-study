#!/usr/bin/env python3
"""Write the three WAN latency scales. Rank homing for the named files is latency-ring."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net


if __name__ == "__main__":
    net.write_canonical(os.path.join(net.root(), "configs", "physical"))
    for scale_name in net.SCALES:
        print(f"wan-latency-{scale_name}.txt")
