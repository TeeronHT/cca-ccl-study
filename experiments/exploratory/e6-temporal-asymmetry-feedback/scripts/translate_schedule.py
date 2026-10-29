#!/usr/bin/env python3
"""Turn the synthesized schedule into per-rank Chakra send/recv graphs."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chakra
import model


def _events(document):
    """Logical events before Chakra encoding. Sends carry the producer they wait on."""
    by_rank = {rank: [] for rank in range(model.N)}
    for edge in document["precondition"]:
        by_rank[edge["src"]].append(("rs-send", edge))
        by_rank[edge["dst"]].append(("rs-recv", edge))
    for edge in document["completion"]:
        by_rank[edge["src"]].append(("send", edge))
        by_rank[edge["dst"]].append(("recv", edge))
    return by_rank


def build_graphs(document, delay_ns):
    """Return rank -> list of dicts with id, deps, kind, and the chakra body."""
    graphs = {}
    for rank in range(model.N):
        nodes = []
        next_id = 1
        comp = next_id
        next_id += 1
        duration = model.delay_us(delay_ns[rank])
        nodes.append(
            {
                "id": comp,
                "deps": [],
                "kind": "comp",
                "rank": rank,
                "body": chakra.node(
                    comp,
                    f"ready-{rank}",
                    chakra.COMP,
                    [],
                    duration,
                    [chakra.cpu_off()],
                ),
            }
        )
        rs_sends = {}
        rs_recvs = {}
        if rank != model.STRAGGLER:
            for step in range(model.HEALTHY - 1):
                send_chunk = (rank - step - 1) % model.HEALTHY
                recv_chunk = (rank - step - 2) % model.HEALTHY
                prev = (rank - 1) % model.HEALTHY
                nxt = (rank + 1) % model.HEALTHY
                send_id = next_id
                recv_id = next_id + 1
                next_id += 2
                send_dep = [comp] if step == 0 else [rs_recvs[step - 1]]
                rs_sends[step] = send_id
                rs_recvs[step] = recv_id
                nodes.append(
                    {
                        "id": send_id,
                        "deps": send_dep,
                        "kind": "send",
                        "phase": "precondition",
                        "src": rank,
                        "dst": nxt,
                        "chunk": send_chunk,
                        "bytes": document["chunk_bytes"][send_chunk],
                        "body": None,
                    }
                )
                nodes.append(
                    {
                        "id": recv_id,
                        "deps": [comp],
                        "kind": "recv",
                        "phase": "precondition",
                        "src": prev,
                        "dst": rank,
                        "chunk": recv_chunk,
                        "bytes": document["chunk_bytes"][recv_chunk],
                        "body": None,
                    }
                )
        producers = {}
        if rank == model.STRAGGLER:
            for chunk in range(model.HEALTHY):
                producers[chunk] = comp
        else:
            producers[(rank) % model.HEALTHY] = rs_recvs[model.HEALTHY - 2]
        tag_base = rank * 100000
        local_tag = 0
        completion = [edge for edge in document["completion"] if edge["src"] == rank or edge["dst"] == rank]
        rounds = {}
        for edge in document["completion"]:
            rounds.setdefault(edge["round"], []).append(edge)
        for round_id in sorted(rounds):
            made = []
            for edge in rounds[round_id]:
                if edge["src"] != rank:
                    continue
                producer = producers.get(edge["chunk"])
                if producer is None:
                    raise RuntimeError(f"rank {rank} sends chunk {edge['chunk']} before it exists")
                node_id = next_id
                next_id += 1
                local_tag += 1
                nodes.append(
                    {
                        "id": node_id,
                        "deps": [producer],
                        "kind": "send",
                        "phase": "completion",
                        "src": rank,
                        "dst": edge["dst"],
                        "chunk": edge["chunk"],
                        "bytes": edge["bytes"],
                        "round": round_id,
                        "tag": tag_base + local_tag,
                        "body": None,
                    }
                )
            for edge in rounds[round_id]:
                if edge["dst"] != rank:
                    continue
                node_id = next_id
                next_id += 1
                local_tag += 1
                nodes.append(
                    {
                        "id": node_id,
                        "deps": [comp],
                        "kind": "recv",
                        "phase": "completion",
                        "src": edge["src"],
                        "dst": rank,
                        "chunk": edge["chunk"],
                        "bytes": edge["bytes"],
                        "round": round_id,
                        "tag": None,
                        "body": None,
                    }
                )
                made.append((edge["chunk"], node_id))
            for chunk, node_id in made:
                producers[chunk] = node_id
        graphs[rank] = nodes
    _assign_tags(graphs)
    return graphs


# Phase ids carried on every send node (uint64 attribute comm_phase_id).
# Workload::issue_send_comm copies them into sim_request.phase_id and the
# ns-3 frontend writes them to flows.csv, so precondition and completion
# traffic can be told apart without inferring it from packet times.
PHASE_ID = {"precondition": 1, "completion": 2}


def _encode(nodes):
    """Rebuild send/recv protobuf after tags are assigned.

    Encoding before _assign_tags left every recv (and every precondition send)
    with comm_tag 0 in the ET. ASTRA matches NATIVE send/recv on (src, dst,
    tag), so completion payloads never paired and ranks never finished.
    """
    for item in nodes:
        if item["kind"] == "comp":
            continue
        tag = item.get("tag")
        if not tag:
            raise RuntimeError(
                f"node {item['id']} ({item['kind']} {item['src']}->{item['dst']} "
                f"chunk {item['chunk']}) has no matching comm_tag"
            )
        kind = chakra.SEND if item["kind"] == "send" else chakra.RECV
        attrs = [
            chakra.cpu_off(),
            ("comm_src", 11, item["src"]),
            ("comm_dst", 11, item["dst"]),
            ("comm_size", 13, item["bytes"]),
            ("comm_tag", 11, tag),
            ("comm_phase_id", 13, PHASE_ID[item["phase"]]),
            ("comm_chunk_id", 13, item["chunk"]),
            ("comm_collective_id", 13, 1),
        ]
        item["body"] = chakra.node(
            item["id"],
            f"{item['phase']}-{item['kind']}-{item['src']}-{item['dst']}-{item['chunk']}",
            kind,
            item["deps"],
            0,
            attrs,
        )
    return nodes


def _assign_tags(graphs):
    """One tag per directed payload, shared by the send and its recv."""
    tag = 1
    index = {}
    for rank, nodes in graphs.items():
        for item in nodes:
            if item["kind"] != "send":
                continue
            key = (item["src"], item["dst"], item["chunk"], item["phase"], item.get("round"))
            if key in index:
                raise RuntimeError(f"duplicate send {key}")
            item["tag"] = tag
            index[key] = tag
            tag += 1
    for rank, nodes in graphs.items():
        for item in nodes:
            if item["kind"] != "recv":
                continue
            key = (item["src"], item["dst"], item["chunk"], item["phase"], item.get("round"))
            if item["phase"] == "precondition":
                key = (item["src"], item["dst"], item["chunk"], item["phase"], None)
            if key not in index:
                raise RuntimeError(f"recv has no send: {key}")
            item["tag"] = index[key]
    for nodes in graphs.values():
        _encode(nodes)


def write_traces(directory, graphs):
    os.makedirs(directory, exist_ok=True)
    for rank, nodes in graphs.items():
        chakra.write_trace(
            os.path.join(directory, f"trace.{rank}.et"),
            [item["body"] for item in nodes],
        )


def ring_nodes(rank, delay_ns):
    comp = chakra.node(1, f"ready-{rank}", chakra.COMP, [], model.delay_us(delay_ns), [chakra.cpu_off()])
    coll = chakra.node(
        2,
        "ring-allreduce",
        chakra.COLL,
        [1],
        0,
        [
            chakra.cpu_off(),
            ("comm_type", 9, 0),
            ("comm_size", 13, model.TENSOR),
        ],
    )
    return [comp, coll]


def main():
    path = os.path.join(model.root(), "schedules", "stragglar", "schedule.json")
    if not os.path.exists(path):
        print("missing schedule.json; run obtain_stragglar_schedule.py", file=sys.stderr)
        return 1
    document = json.load(open(path))
    graphs = build_graphs(document, {rank: 0 for rank in range(model.N)})
    summary = {
        "ranks": model.N,
        "events": {str(rank): len(nodes) for rank, nodes in graphs.items()},
        "payload_sends": sum(1 for nodes in graphs.values() for item in nodes if item["kind"] == "send"),
    }
    out = os.path.join(model.root(), "schedules", "stragglar", "translated.json")
    with open(out, "w") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    print(f"payload sends {summary['payload_sends']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
