#!/usr/bin/env python3
"""Check the StragglAR translation and the Ring trace shape. Does not start ns-3."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model
import translate_schedule


def fail(message):
    print(f"INVALID: {message}", file=sys.stderr)
    sys.exit(1)


def check_stragglar():
    path = os.path.join(model.root(), "schedules", "stragglar", "schedule.json")
    if not os.path.exists(path):
        fail("missing schedule.json")
    document = json.load(open(path))
    if document["straggler"] != 15 or document["tensor_bytes"] != model.TENSOR:
        fail("schedule is not the 16-rank 64 MiB AllReduce")
    if sum(document["chunk_bytes"]) != model.TENSOR:
        fail("chunks do not sum to 64 MiB")
    if not model.provenance_ok(document):
        fail("provenance does not deliver every source rank")
    pre_ranks = {edge["src"] for edge in document["precondition"]} | {
        edge["dst"] for edge in document["precondition"]
    }
    if 15 in pre_ranks:
        fail("precondition touches rank 15")
    graphs = translate_schedule.build_graphs(document, {rank: 0 for rank in range(model.N)})
    if len(graphs) != 16:
        fail("not 16 ranks")
    sends = []
    recvs = []
    for rank, nodes in graphs.items():
        kinds = [item["kind"] for item in nodes]
        if kinds[0] != "comp":
            fail(f"rank {rank} does not start at its readiness node")
        comp = nodes[0]["id"]
        for item in nodes:
            if item["kind"] == "send" and item["src"] == 15 and comp not in _ancestors(nodes, item["id"]):
                fail("rank 15 sends without waiting for its arrival")
            if item["kind"] == "send":
                sends.append(item)
            if item["kind"] == "recv":
                recvs.append(item)
    pairs = {}
    for item in sends:
        pairs.setdefault(item["tag"], {})["send"] = item
    for item in recvs:
        slot = pairs.setdefault(item["tag"], {})
        if "recv" in slot:
            fail(f"tag {item['tag']} matches more than one recv")
        slot["recv"] = item
    for tag, pair in pairs.items():
        if "send" not in pair or "recv" not in pair:
            fail(f"tag {tag} is not a send/recv pair")
        send, recv = pair["send"], pair["recv"]
        if (send["src"], send["dst"], send["chunk"], send["bytes"]) != (
            recv["src"],
            recv["dst"],
            recv["chunk"],
            recv["bytes"],
        ):
            fail(f"tag {tag} endpoints differ")
    schedule_sends = len(document["precondition"]) + len(document["completion"])
    if len(sends) != schedule_sends:
        fail(f"translated sends {len(sends)} != schedule sends {schedule_sends}")
    for item in sends:
        if b"comm_phase_id" not in item["body"] or b"comm_chunk_id" not in item["body"]:
            fail("a send node lacks the comm_phase_id/comm_chunk_id attributes")
    pre_sends = [s for s in sends if s["phase"] == "precondition"]
    post_sends = [s for s in sends if s["phase"] == "completion"]
    if not pre_sends or not post_sends:
        fail("translation lacks a precondition or completion phase")
    print(
        f"stragglar: {len(sends)} sends ({len(pre_sends)} precondition, {len(post_sends)} completion), "
        f"provenance holds, rank 15 is behind its arrival, precondition is ranks 0-14, phase ids attached"
    )


def _ancestors(nodes, node_id):
    by_id = {item["id"]: item for item in nodes}
    seen = set()
    stack = list(by_id[node_id]["deps"])
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(by_id[current]["deps"])
    return seen


def check_ring_notes():
    text = open(os.path.join(model.root(), "schedules", "ring", "mechanism.txt")).read()
    if "barrier=false" not in text or "rendezvous=true" not in text:
        fail("ring mechanism note is missing")
    nodes = translate_schedule.ring_nodes(15, 1000)
    if len(nodes) != 2:
        fail("ring trace is not readiness plus one collective")
    print("ring: one native AllReduce after that rank's readiness node")


def main():
    check_stragglar()
    check_ring_notes()
    print("schedule translation holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
