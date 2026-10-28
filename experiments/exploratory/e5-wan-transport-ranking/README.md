# E5: transport-aware collective ranking on a heterogeneous WAN

Exploratory falsification test. It asks whether a static cost model built from topology, propagation, link capacity, and equal bandwidth sharing is enough to choose between two Ring AllReduce mappings once packet-level congestion control is included.

No novelty claim follows from this experiment alone. It does not propose a planner.

Existing WAN-aware collective systems generally reason about latency, bandwidth, topology, and path sharing. Packet-level transport also has feedback delay, queue evolution, rate-control state, and BDP-dependent behavior. E5 asks whether those omitted dynamics are large enough to change the mapping decision itself, or whether topology plus bandwidth plus latency already rank these mappings.

## What this is not

This is not a DoubleZero reproduction and not an extension of it. DoubleZero's distributed-training study uses partial synchronization among islands, rotating clique schedules, multicast, FPGA in-network aggregation, and a synchronization/staleness objective. Its network abstraction uses propagation, serialization, a bottleneck stream rate, and equal capacity division.

E5 is a controlled experiment motivated by a limitation of that kind of static abstraction. It uses one exact synchronous unicast Ring AllReduce, fixed routes, packet queues, and ECN, CNP, and PFC. There is no multicast, no FPGA aggregation, no partial synchronization, and no training-convergence objective.

It also does not study WAN path reconfiguration or concurrent local collectives. Routing stays fixed for the whole run. There is one job and no background traffic. The isolated interaction is collective mapping × WAN bandwidth-delay product × transport feedback.

The three transport modes are private packet-level controls. E5 does not claim that PFC, DCQCN, or HPCC, unchanged, are the right production WAN transports. The signal is whether the mapping decision moves with the transport, not which CCA "wins the WAN."

## Fabric

16 GPUs in four sites of four. Each site is GPUs, one ToR, one border router, then a full-mesh WAN.

| Link | Capacity | Delay |
| --- | --- | --- |
| GPU → ToR | 200 Gbps | 500 ns |
| ToR → border | 800 Gbps | 10 µs |
| A–B and C–D | 100 Gbps | 0.25 ms at λ=1 |
| B–C and D–A | 50 Gbps | 0.50 ms at λ=1 |
| B–D and C–A | 100 Gbps | 1.842 ms at λ=1 |

λ = 0.5, 1, and 2 multiplies only the WAN propagation. Capacities stay fixed. Local delays stay fixed. Error rate is 0. `LINK_DOWN` is `0 0 0`, so routes are not recomputed during a run.

At λ=1 a 4 MiB chunk with four-way equal share costs about 3.184 ms on both the 50 Gbps edge and the 1.842 ms edge. The static model is supposed to call the two rings a tie there, prefer the bandwidth ring at λ=0.5, and prefer the latency ring at λ=2. `static_model.py` computes that and stops the matrix if the shape is absent. The current computation does produce it: the λ=1 predictions differ by about 0.006%.

## How the rings are realized

ASTRA-sim's logical JSON only supplies dimension sizes. A 1D ring is node `i → i+1`. The custom `RingTopology(vector<int>)` constructor is not reachable from `GeneralComplexTopology`. The collective implementation is left as ring.

The mapping is rank-to-site homing. Node `i` is attached to the site of entry `i` in the ring order below. The files `wan-latency-*.txt` are the latency-ring homing. `run_one.sh` writes the bandwidth-ring homing into that run's directory from the same builder.

Latency ring, site order A→B→C→D, repeated:

`0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15`

Those nodes use A–B, B–C, C–D, and D–A.

Bandwidth ring, site order A→B→D→C, repeated:

`0, 4, 12, 8, 1, 5, 13, 9, 2, 6, 14, 10, 3, 7, 15, 11`

Those nodes use A–B, B–D, C–D, and C–A.

Both rings have 16 cross-site edges, four concurrent streams on each edge they use, and `2 × (16 − 1) = 30` ring steps over one 64 MiB AllReduce (`preferred-dataset-splits` 1, so the ring chunk is 4 MiB). The system JSON is the same for both mappings.

## Routing

ns-3 builds next hops with a hop-count BFS (`CalculateRoute` in `scratch/common.h`). Switches are expanded; hosts are not. `validate.py` replays that BFS on both homings. Every cross-site GPU pair has exactly one shortest path, and its border hop is the direct WAN edge. A path such as B→A→D is one hop longer and is not installed. ECMP has only one next hop on these paths, so the switch seed does not change the route.

## Buffers and ECN maps

`BUFFER_SIZE` is a single value applied to every switch. The topology line cannot name a per-tier buffer, and E5 does not modify ns-3 to add one. Every switch therefore keeps the validated 32 MiB. The requested 128 MiB border buffer is not representable. If any run drops a packet, that run is invalid: its completion time is not interpreted, and the scripts stop instead of raising the buffer.

`ConfigEcn` requires a threshold entry for every link bitrate and aborts otherwise. The validated map has 100 Gbps and 200 Gbps, but not 50 Gbps or 800 Gbps. Those two keys were added by copying thresholds already in the E0 map: 50 Gbps uses the 40 Gbps pair (Kmin 200, Kmax 800), and 800 Gbps uses the high-rate plateau (Kmin 800, Kmax 3200). Every pre-existing rate keeps its E0 threshold. No other CCA parameter changes. PFC and DCQCN stay `HAS_WIN 0`. HPCC stays `HAS_WIN 1`.

Sender-rate and window traces are not collected. The backend's rate log is compiled off, and enabling it would edit the congestion-control source.

## Matrix and metrics

3 latency scales × 2 rings × 3 CCAs = 18 runs. Ids look like `wan1.0__bandwidth-ring__dcqcn`.

The primary number is

`R(cca, λ) = CCT(latency ring) / CCT(bandwidth ring)`.

Below 1 the latency ring is faster, above 1 the bandwidth ring is faster, and a 1% band counts as a tie. The same band is applied to the static prediction. `ranking_agrees` is whether those winners match. With three λ points the crossover is reported only as before λ=1, around λ=1, after λ=1, or not in range.

`model_error = actual CCT / predicted CCT` is recorded, but absolute error is not the result. The static model omits headers, host serialization, and queueing on purpose.

## Run

From `cca-ccl-study`, after the ns-3 frontend is built:

```bash
bash experiments/exploratory/e5-wan-transport-ranking/scripts/run.sh --smoke
bash experiments/exploratory/e5-wan-transport-ranking/scripts/run.sh
```

The script regenerates topologies and the workload, writes `results/static_predictions.csv`, and runs `validate.py`. It then runs λ=1 PFC on both rings, checks that there are no drops, that flow counts match, and that only the intended WAN edges carry bytes. It repeats that pair for DCQCN. If that DCQCN pair already disagrees with the static tie, the queue traces in those run directories are left in place and the matrix continues. Finished runs are skipped.

`results/summary.csv`, `results/manifest.csv`, and `results/wan_edges.csv` are written from whatever has finished. Plots are written only when matplotlib is installed.

A follow-up that holds static edge cost fixed while changing the bandwidth/latency split is not in this harness. Add it only if the 18-run table shows the CCAs disagreeing on the winner.
