#!/usr/bin/env python3
"""Transport-agnostic ring cost. Uses propagation, capacity, fan-in, and chunk size only."""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net

FIELDS = [
    "latency_scale",
    "mapping",
    "predicted_step_ns",
    "predicted_collective_ns",
    "predicted_rank",
]


def main():
    rows = net.predictions()
    ok, reason = net.crossover_holds(rows)
    destination = os.path.join(net.root(), "results", "static_predictions.csv")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(reason)
    for row in rows:
        print(
            f"  λ={row['latency_scale']} {row['mapping']}: "
            f"step={row['predicted_step_ns']} collective={row['predicted_collective_ns']} "
            f"rank={row['predicted_rank']}"
        )
    print(f"wrote {destination}")
    if not ok:
        print("STOP: static model does not produce the intended crossover.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
