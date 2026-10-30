#!/usr/bin/env python3
"""Write experiment-local CCA files from the validated E0 configs.

ECN maps contain only the bitrates this experiment uses (50, 100, 200 Gbps).
Thresholds scale from the E0 200 Gbps row in proportion to rate. The stock
100 Gbps row (Kmin 400 KB, Kmax 1600 KB) is not reused.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "common"))
import netconfig  # pyright: ignore[reportMissingImports]

def ecn_maps():
    kmin, kmax, pmax = {}, {}, {}
    for rate in model.ecn_rates():
        kmin[rate] = str(model.kmin_kb(rate))
        kmax[rate] = str(model.kmax_kb(rate))
        pmax[rate] = model.PMAX
    return {
        "KMIN_MAP": netconfig.format_map(kmin),
        "KMAX_MAP": netconfig.format_map(kmax),
        "PMAX_MAP": netconfig.format_map(pmax),
    }


def overrides_for(transport):
    values = dict(model.EXTENSION_KEYS)
    values.update(ecn_maps())
    if transport == "pfc":
        # E0 PFC-only: CC_MODE 0, ENABLE_QCN 0, HAS_WIN 0. Pause is on.
        # ECN map entries are still required; the backend looks them up for
        # every link rate even when QCN marking is disabled.
        values["ENABLE_PFC"] = "1"
        values["CC_MODE"] = "0"
        values["HAS_WIN"] = "0"
        values["ENABLE_QCN"] = "0"
    elif transport == "fixed-rate":
        # Diagnostic only. Line rate, no window, no pause. A thinner uplink
        # can drop and never finish; those runs are not numeric results.
        values["ENABLE_PFC"] = "0"
        values["CC_MODE"] = "0"
        values["HAS_WIN"] = "0"
        values["ENABLE_QCN"] = "1"
    else:
        values["ENABLE_PFC"] = "0"
    return values


def main():
    out_dir = os.path.join(model.root(), "configs", "cca")
    os.makedirs(out_dir, exist_ok=True)
    for transport in model.TRANSPORTS:
        src = os.path.join(model.e0_configs(), model.TRANSPORT_BASE[transport])
        dst = os.path.join(out_dir, model.TRANSPORT_FILE[transport])
        netconfig.rewrite(src, dst, overrides_for(transport))
        print(f"{model.TRANSPORT_FILE[transport]} <- E0 {model.TRANSPORT_BASE[transport]}")
    for key, value in ecn_maps().items():
        print(f"  {key} {value}")


if __name__ == "__main__":
    main()
