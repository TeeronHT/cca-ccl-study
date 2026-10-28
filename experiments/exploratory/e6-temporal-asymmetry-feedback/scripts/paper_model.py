#!/usr/bin/env python3
"""Static arrival model. Beta is the 200 Gbps byte time, not a measured CCT."""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model

FIELDS = [
    "arrival_pattern",
    "tau_ns",
    "tau_over_tref",
    "schedule",
    "predicted_time",
    "predicted_winner",
    "theoretical_q_res_bytes",
]


def main():
    path = os.path.join(model.root(), "results", "arrivals.json")
    if not os.path.exists(path):
        print("missing results/arrivals.json", file=sys.stderr)
        return 1
    rows = []
    for case in json.load(open(path))["cases"]:
        predicted = model.predict(case["pattern"], case["tau_ns"])
        for schedule, key in (("ring", "ring_ns"), ("stragglar", "stragglar_ns")):
            rows.append(
                {
                    "arrival_pattern": case["pattern"],
                    "tau_ns": case["tau_ns"],
                    "tau_over_tref": case["tau_over_tref"],
                    "schedule": schedule,
                    "predicted_time": predicted[key],
                    "predicted_winner": predicted["winner"],
                    "theoretical_q_res_bytes": predicted["stragglar_q_res_bytes"],
                }
            )
    out = os.path.join(model.root(), "results", "static_predictions.csv")
    with open(out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
