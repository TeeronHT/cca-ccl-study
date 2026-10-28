#!/usr/bin/env python3
"""Turn T_ref into the absolute arrival delays shared by every CCA."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model

PATTERNS = ("symmetric", "one-late", "two-late")
FRACTIONS = {"symmetric": (0.0,), "one-late": (0.25, 0.5, 1.0), "two-late": (0.25, 0.5, 1.0)}
LATE = {"symmetric": (), "one-late": (15,), "two-late": (14, 15)}


def read_tref(path):
    text = open(path).read().strip().split()[0]
    return int(text)


def cases(tref):
    built = []
    for pattern in PATTERNS:
        for fraction in FRACTIONS[pattern]:
            requested = int(fraction * tref)
            applied = 0 if fraction == 0 else model.applied_delay_ns(requested)
            delays = [0] * model.N
            for rank in LATE[pattern]:
                delays[rank] = applied
            token = "tau0" if fraction == 0 else f"tau{fraction:.2f}"
            built.append(
                {
                    "pattern": pattern,
                    "tau_over_tref": fraction,
                    "tau_token": token,
                    "tau_requested_ns": requested,
                    "tau_ns": applied,
                    "late_ranks": list(LATE[pattern]),
                    "delay_ns": delays,
                }
            )
    return built


def main():
    path = os.path.join(model.root(), "results", "t_ref.txt")
    if not os.path.exists(path):
        print("missing results/t_ref.txt; run the PFC symmetric Ring calibration first", file=sys.stderr)
        return 1
    tref = read_tref(path)
    document = {"t_ref_ns": tref, "cases": cases(tref)}
    out = os.path.join(model.root(), "results", "arrivals.json")
    with open(out, "w") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    print(f"T_ref={tref} cases={len(document['cases'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
