# E5: transport-aware collective ranking on a heterogeneous high-BDP inter-site fabric

Exploratory falsification test. Can a static collective cost model built
from path latency, link capacity and equal sharing correctly rank two Ring
AllReduce mappings once packet-level congestion-control feedback is included?
More strongly: can two mappings that are nearly equivalent under the static
model have different winners under different transport-control dynamics?

No novelty claim follows from this experiment alone. It does not propose a
planner.

## What this is not

Not a PFC-over-WAN study, not a packet-loss study, not a background-traffic
study, not a routing-reconfiguration or dynamic-TE study, and not a
DoubleZero reproduction. One job, one exact synchronous unicast Ring
AllReduce, fixed routes, packet queues, ECN. No multicast, no in-network
aggregation, no partial synchronization, no training objective. DCQCN and
HPCC are controlled feedback mechanisms here, not production WAN transport
recommendations.

## Fabric

16 GPUs in four sites of four (A: 0–3, B: 4–7, C: 8–11, D: 12–15 in physical
numbering). Each site is GPUs, one ToR, one border router; the borders form
a static full mesh.

| Link | Capacity | One-way delay at λ=1 |
| --- | --- | --- |
| GPU → ToR | 200 Gbps | 500 ns |
| ToR → border | 800 Gbps | 10 µs |
| A–B, C–D (shared) | 100 Gbps | 0.25 ms |
| B–C, D–A (low latency, lower bandwidth) | 50 Gbps | 0.50 ms |
| B–D, C–A (higher latency, higher bandwidth) | 100 Gbps | 1.842 ms |

λ ∈ {0.5, 1, 2} multiplies WAN propagation only. Capacities, local delays and
everything else stay fixed. Error rate 0, `LINK_DOWN 0 0 0`. Every cross-site
pair's hop-count shortest path is the direct WAN edge (`validate.py` replays
`CalculateRoute`).

## Ring mappings

ASTRA's 1D ring is logical node `i → i+1`; a neighbour list cannot be given.
Each mapping homes logical node `i` at the site of position `i` in the
desired ring order (`run_one.sh` emits that attachment per run):

- latency ring, site order A→B→C→D: `0,4,8,12, 1,5,9,13, 2,6,10,14, 3,7,11,15`, using A–B, B–C, C–D, D–A;
- bandwidth ring, site order A→B→D→C: `0,4,12,8, 1,5,13,9, 2,6,14,10, 3,7,15,11`, using A–B, B–D, D–C, C–A.

Both have 16 cross-site edges, four concurrent streams per edge, and 30 ring
steps over one 64 MiB AllReduce (one dataset split, 4 MiB chunk). The
validator reconstructs the logical `i → i+1` traffic and checks that the
physical site-to-site edges are exactly those.

## Static model

`static_model.py` / `net.py` use only propagation, capacity, the known four
streams per edge, chunk size and the ring step structure:
`cost_e = propagation_e + chunk_bits / (capacity_e / 4)`, step = max over
used edges, collective = 30 steps. No simulated quantity enters. It predicts
bandwidth ring at λ=0.5, a tie (0.006 %) at λ=1, latency ring at λ=2;
`run.sh` stops if that shape disappears.

## Transport modes (primary)

| Transport | Config | What adapts |
| --- | --- | --- |
| `fixed-rate` | E0 `dcqcn.txt` with `CC_MODE 0`, `HAS_WIN 0` | nothing: senders stay at line rate; no window, no pause; switches still mark ECN for diagnostics and the NIC ignores it |
| `dcqcn` | E0 `dcqcn.txt` (`HAS_WIN 0`) | DCQCN rate |
| `hpcc` | E0 `hpcc.txt` (`HAS_WIN 1`) | HPCC rate and window |

All three run with `TRANSPORT_CONNECTION_MODE PERSISTENT`: one queue pair per
directed rank pair for the whole run, so the 30 messages a rank sends to its
successor share one source port, one ECMP path and one controller state.
Fixed-rate uses it too so flow and path identity stay comparable.

PFC is not a primary transport (`ENABLE_PFC 0` everywhere, no PAUSE frames,
headroom 0). The reason is quantitative: with PFC on, the backend's headroom
rule (rate × one-way delay × 3 per port) would commit 47 MB at λ=0.5, 91 MB
at λ=1 and 179 MB at λ=2 on the busiest border router, against a 128 MiB
buffer; at λ=2 the pause threshold would wrap. `PFC_THRESHOLD_VALIDATION
STRICT` is set in every config anyway, so a PFC-on config can never run
silently with a wrapped threshold. DCTCP, TIMELY, background traffic, a
second job and failures are out of scope.

## Buffers, ECN, loss

`BUFFER_SIZE 32` applies to the ToRs; `configs/physical/buffers.txt`
(`SWITCH_BUFFER_MAP_FILE`) sets the four border routers to 128 MiB. Without
PFC the dynamic threshold is the drop threshold; a primary run with any
drop (`mmu_drops` in `counters.txt`, which the packet trace never recorded)
is marked invalid and excluded from the ranking. Buffers and CCA parameters
are not changed to suppress a drop.

ECN thresholds are generated from the validated E0 200 Gbps entry by
`K(rate) = K_200G × rate / 200 G` (KB units), `PMAX` unchanged:

| Rate | Kmin | Kmax | Pmax |
| --- | --- | --- | --- |
| 50 Gbps | 150 KB | 600 KB | 0.2 |
| 100 Gbps | 300 KB | 1.2 MB | 0.2 |
| 200 Gbps | 600 KB | 2.4 MB | 0.2 |
| 800 Gbps | 2.4 MB | 9.6 MB | 0.2 |

This keeps queue bytes / link rate constant across rates. The maps are
identical in all three transport files and do not change with λ or mapping.
`generate_configs.py` writes them; `validate.py` check 9 recomputes them.

## Matrix and metric

3 λ × 2 mappings × 3 transports = 18 primary runs, ids like
`wan1.0__bandwidth-ring__dcqcn`. Per transport `c` and scale λ,

`R(c, λ) = CCT(latency ring) / CCT(bandwidth ring)`

below 1 the latency ring is faster, above 1 the bandwidth ring, with a 1 %
tie band; the same band is applied to the static ratio. The main comparison
is static vs fixed-rate vs DCQCN vs HPCC, plotted as y = R against x = λ with
the y = 1 crossover line (`results/plots/mapping-ratio.png`).

What counts: at the same λ the static model/fixed-rate prefer one ring but a
CCA prefers the other, or DCQCN and HPCC disagree, or the crossover λ moves
materially with the transport. What does not count: WAN latency raising
CCT, 50 G being slower than 100 G, one transport having smaller queues or
more CNPs, or one ring being globally faster, unless it changes the ranking.

## Transport-dynamics metrics

Per run (`summary.csv`): CCT, max/mean WAN queue, ECN marks, CNP-flagged
ACKs, drops, flow/message counts, connection and QP counts, QP reuse count,
messages per QP, distinct paths, outstanding messages; for DCQCN the
time-weighted mean and minimum rate while busy, decrease and increase
events, and the busy-time fraction below 25/50/75 % of line rate; for HPCC
the number of full-RTT updates, mean and minimum reference rate and mean
window. `wan_edges.csv` has bytes, max queue, ECN marks per WAN edge;
`paths.csv` has every connection's path. These come from the observational
counters, `flows.csv`, `paths.csv` and `cca_trace.csv` (`ENABLE_CCA_TRACE
1`: rate changes, not per-packet events). `TRACE_LEVEL 1` turns the packet
trace off; the queue log keeps every port at a 10 µs period
(`QUEUE_LOG_MIN_BYTES 0`, `QLEN_MON_INTERVAL 10000`).

## Validator and smoke tests

`validate.py` prints 20 checks: GPU count, site placement, permutations,
intended edges, direct routes, λ scales delay only, fixed capacities, ECN
entry coverage, the scaling rule, buffer tiers, PERSISTENT, PFC off, STRICT,
headroom arithmetic (with the PFC-on table), BDP unit test passed, static
crossover, fixed-rate has no feedback, DCQCN = E0, HPCC = E0 with
`HAS_WIN 1`, zero error rate.

`run.sh` also requires `experiments/backend-validation/{legacy-regression,
persistent-qp,bdp-units}/results/PASSED` from the same binary, then runs:

- Smoke A, λ=1 latency ring fixed-rate: finishes, zero drops, bytes only on the intended WAN edges, one connection per directed rank pair whose recorded path crosses the direct edge, no rate-adaptation events, every message starts at line rate, no PAUSE frames.
- Smoke B/C, λ=1 latency and bandwidth ring DCQCN: the above plus 30 messages per connection, at least one `RATE_DECREASE`, later messages starting below line rate (carried state), non-zero ECN and CNP counts.
- Smoke D, λ=1 both rings HPCC: the above plus `HAS_WIN 1`, `HPCC_UPDATE` events present, no update at `MIN_RATE` (the `HAS_WIN 0` collapse), mean reference rate well above the floor.

```bash
bash experiments/exploratory/e5-wan-transport-ranking/scripts/run.sh --smoke
bash experiments/exploratory/e5-wan-transport-ranking/scripts/run.sh
```

Finished runs are skipped. `summary.csv`, `wan_edges.csv`, `paths.csv`,
`manifest.csv` (commits, mode, CCA, `HAS_WIN`, config hash, topology and
workload hashes, buffers, ECN maps, trace level, queue-log threshold, seed)
are rewritten from whatever has finished. Plots need matplotlib and are
skipped otherwise.

A follow-up that holds static edge cost fixed while changing the
bandwidth/latency split is not in this harness.
