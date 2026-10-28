#!/usr/bin/env python3
"""Write one trace directory per schedule and arrival case."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chakra
import model
import translate_schedule


def write_case(schedule, case):
    directory = os.path.join(
        model.root(), "workload", f"{schedule}__{case['pattern']}__{case['tau_token']}"
    )
    os.makedirs(directory, exist_ok=True)
    delays = case["delay_ns"]
    if schedule == "ring":
        for rank, delay in enumerate(delays):
            chakra.write_trace(
                os.path.join(directory, f"trace.{rank}.et"),
                translate_schedule.ring_nodes(rank, delay),
            )
        sends = model.N * 2 * (model.N - 1)
    else:
        document = json.load(open(os.path.join(model.root(), "schedules", "stragglar", "schedule.json")))
        graphs = translate_schedule.build_graphs(document, {rank: delays[rank] for rank in range(model.N)})
        translate_schedule.write_traces(directory, graphs)
        sends = sum(1 for nodes in graphs.values() for item in nodes if item["kind"] == "send")
    meta = {
        "schedule": schedule,
        "pattern": case["pattern"],
        "tau_ns": case["tau_ns"],
        "tau_over_tref": case["tau_over_tref"],
        "late_ranks": case["late_ranks"],
        "payload_sends": sends,
        "tensor_bytes": model.TENSOR,
    }
    with open(os.path.join(directory, "case.json"), "w") as handle:
        json.dump(meta, handle, indent=2)
        handle.write("\n")
    return directory


def main():
    if "--bootstrap" in sys.argv:
        case = {
            "pattern": "symmetric",
            "tau_over_tref": 0,
            "tau_token": "tau0",
            "tau_ns": 0,
            "late_ranks": [],
            "delay_ns": [0] * model.N,
        }
        for schedule in ("ring", "stragglar"):
            print(write_case(schedule, case))
        return 0
    path = os.path.join(model.root(), "results", "arrivals.json")
    if not os.path.exists(path):
        print("missing results/arrivals.json", file=sys.stderr)
        return 1
    document = json.load(open(path))
    for case in document["cases"]:
        for schedule in ("ring", "stragglar"):
            print(write_case(schedule, case))
    return 0


if __name__ == "__main__":
    sys.exit(main())
