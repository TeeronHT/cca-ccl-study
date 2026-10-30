#!/usr/bin/env python3
"""Write configs/cca/{fixed-rate,dcqcn,hpcc}.txt for E5 from the validated E0 files.

Only these things differ from E0:
  * ECN maps: exactly the four link rates E5 uses, scaled from the E0 200 Gbps
    entry by KMIN(rate) = KMIN_200 x rate / 200G (same for KMAX); PMAX unchanged.
  * extension keys: PERSISTENT connections, PFC off, STRICT headroom check,
    per-switch buffer map, queue log threshold 0 at a 10 us sampling period,
    flow/path/CCA traces, no packet trace (TRACE_LEVEL 1).
  * fixed-rate: the DCQCN file with CC_MODE 0 (no rate adaptation). ENABLE_QCN
    stays 1 so switches still mark, for diagnostics; CC_MODE 0 ignores CNPs.
Every DCQCN/HPCC parameter keeps its E0 value. Output paths are placeholders;
run_one.sh rewrites them per run.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net  # noqa: E402
import netconfig  # pyright: ignore[reportMissingImports]

def overrides_for(transport):
    values = dict(net.EXTENSION_KEYS)
    values.update(net.ecn_maps())
    if transport == "fixed-rate":
        values["CC_MODE"] = "0"
        values["HAS_WIN"] = "0"
        values["ENABLE_QCN"] = "1"
    return values


def main():
    out_dir = os.path.join(net.root(), "configs", "cca")
    os.makedirs(out_dir, exist_ok=True)
    for transport in net.TRANSPORTS:
        src = os.path.join(net.e0_configs(), net.TRANSPORT_BASE[transport])
        dst = os.path.join(out_dir, net.TRANSPORT_FILE[transport])
        netconfig.rewrite(src, dst, overrides_for(transport))
        print(f"{net.TRANSPORT_FILE[transport]} <- E0 {net.TRANSPORT_BASE[transport]}")
    stale = os.path.join(out_dir, "pfc-only.txt")
    if os.path.exists(stale):
        os.remove(stale)
        print("removed pfc-only.txt (PFC is not an E5 transport)")
    maps = net.ecn_maps()
    kmin200, kmax200, pmax200 = net.e0_reference_thresholds()
    print(f"ECN reference 200G: kmin {kmin200} KB kmax {kmax200} KB pmax {pmax200}")
    for key in ("KMIN_MAP", "KMAX_MAP", "PMAX_MAP"):
        print(f"  {key} {maps[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
