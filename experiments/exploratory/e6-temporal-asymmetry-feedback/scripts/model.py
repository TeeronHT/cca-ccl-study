"""Ring and StragglAR schedules for one 64 MiB AllReduce."""

import hashlib
import importlib.util
import json
import os

N = 16
STRAGGLER = 15
HEALTHY = 15
TENSOR = 64 * 1024 * 1024
BETA_NS_PER_BYTE = 0.04  # 8 / 200e9 seconds, in nanoseconds
SOURCE_COMMIT = "3b3b6d2fd0310e03dbcd39c43940369ef2919150"
SYNTHESIS = "python schedules/stragglar/synthesizer_pow2.py 16"


def root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def chunk_sizes():
    base, rem = divmod(TENSOR, HEALTHY)
    return [base + (1 if index < rem else 0) for index in range(HEALTHY)]


def ring_message():
    return TENSOR // N


def load_synthesizer():
    path = os.path.join(root(), "schedules", "stragglar", "synthesizer_pow2.py")
    spec = importlib.util.spec_from_file_location("stragglar_syn", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completion_edges(schedule):
    edges = []
    for round_id, rnd in enumerate(schedule):
        for matching in rnd:
            kind = type(matching).__name__
            if kind == "StragglerMatching":
                pairs = (
                    (matching.gpu.rank, matching.straggler_gpu.rank, matching.chunk_id),
                    (matching.straggler_gpu.rank, matching.gpu.rank, matching.chunk_id),
                )
            elif kind == "OneWayMatching":
                pairs = ((matching.send_gpu.rank, matching.recv_gpu.rank, matching.chunk_id),)
            elif kind == "TwoWayMatching":
                pairs = (
                    (matching.gpu1.rank, matching.gpu2.rank, matching.chunk_id1),
                    (matching.gpu2.rank, matching.gpu1.rank, matching.chunk_id2),
                )
            else:
                raise RuntimeError(kind)
            for src, dst, chunk in pairs:
                edges.append(
                    {
                        "round": round_id,
                        "kind": kind,
                        "src": src,
                        "dst": dst,
                        "chunk": chunk,
                    }
                )
    return edges


def precondition_edges():
    edges = []
    for rank in range(HEALTHY):
        for step in range(HEALTHY - 1):
            edges.append(
                {
                    "step": step,
                    "src": rank,
                    "dst": (rank + 1) % HEALTHY,
                    "chunk": (rank - step) % HEALTHY,
                    "phase": "precondition",
                }
            )
    return edges


def build_stragglar():
    module = load_synthesizer()
    gpus = [module.GPU(rank, N) for rank in range(N)]
    schedule = module.construct_schedule(gpus, print_result=False)
    sizes = chunk_sizes()
    completion = completion_edges(schedule)
    for edge in completion:
        edge["bytes"] = sizes[edge["chunk"]]
    pre = precondition_edges()
    for edge in pre:
        edge["bytes"] = sizes[edge["chunk"]]
    document = {
        "n": N,
        "straggler": STRAGGLER,
        "tensor_bytes": TENSOR,
        "chunk_bytes": sizes,
        "precondition": pre,
        "completion": completion,
        "completion_rounds": len(schedule),
        "theoretical_q_res_bytes": len(schedule) * max(sizes),
        "q_res_meaning": (
            "completion rounds times the largest chunk. "
            "The synthesizer does not print a separate q_res."
        ),
    }
    return document


def provenance_ok(document):
    """Reduce-scatter partials, then the synthesized exchanges. Every output is complete."""
    sizes = document["chunk_bytes"]
    state = [[set() for _ in sizes] for _ in range(N)]
    for rank in range(HEALTHY):
        state[rank][rank] = set(range(HEALTHY))
    for chunk in range(HEALTHY):
        state[STRAGGLER][chunk] = {STRAGGLER}
    rounds = {}
    for edge in document["completion"]:
        rounds.setdefault(edge["round"], []).append(edge)
    for round_id in sorted(rounds):
        snapshot = [[set(chunk) for chunk in rank] for rank in state]
        for edge in rounds[round_id]:
            payload = snapshot[edge["src"]][edge["chunk"]]
            if not payload:
                return False
            state[edge["dst"]][edge["chunk"]] |= payload
    full = set(range(N))
    return all(state[rank][chunk] == full for rank in range(N) for chunk in range(HEALTHY))


def delay_us(tau_ns):
    """ASTRA multiplies duration_micros by 1000. Zero becomes a 1 ns replay."""
    if tau_ns <= 0:
        return 0
    return tau_ns // 1000


def applied_delay_ns(tau_ns):
    if tau_ns <= 0:
        return 1
    return (tau_ns // 1000) * 1000


def predict(pattern, tau_ns):
    sizes = chunk_sizes()
    pre_bytes = (HEALTHY - 1) * min(sizes)
    # The checked-in schedule has 18 completion rounds. Read it if present.
    path = os.path.join(root(), "schedules", "stragglar", "schedule.json")
    rounds = 18
    q_res = rounds * max(sizes)
    if os.path.exists(path):
        doc = json.load(open(path))
        rounds = doc["completion_rounds"]
        q_res = doc["theoretical_q_res_bytes"]
    ring_bytes = 2 * (N - 1) * ring_message()
    ring_ns = tau_ns + ring_bytes * BETA_NS_PER_BYTE
    pre_ns = pre_bytes * BETA_NS_PER_BYTE
    post_ns = q_res * BETA_NS_PER_BYTE
    strag_ns = max(tau_ns, pre_ns) + post_ns
    if pattern == "symmetric":
        ring_ns = ring_bytes * BETA_NS_PER_BYTE
        strag_ns = pre_ns + post_ns
    winner = "stragglar" if strag_ns < ring_ns else "ring"
    if abs(strag_ns - ring_ns) / max(ring_ns, 1) <= 0.01:
        winner = "tie"
    return {
        "ring_ns": int(ring_ns),
        "stragglar_ns": int(strag_ns),
        "winner": winner,
        "ring_critical_bytes": ring_bytes,
        "stragglar_q_res_bytes": q_res,
        "beta_ns_per_byte": BETA_NS_PER_BYTE,
    }


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()
