# E3: communication volume versus instantaneous traffic concentration

E3 asks whether transport feedback changes the tradeoff between sending less
cross-rack data and exposing more of it at once.

A flat Ring (`[16]`) sends more cross-rack bytes, but only one rank in each
rack is on a cross-rack edge, so a step emits one cross-rack flow per rack.
A multidimensional Ring (`[4,4]`) sends fewer cross-rack bytes, but during
its dimension-1 phase every rank sends across racks, four flows per rack at
once. Both move the same 1920 MiB in total, 120 MiB per rank. There is no
scale-up network. `[4,4]` is not a hierarchy with a cheap local tier. Same-rack
messages go GPU to ToR to GPU on the same 200 Gbps links as everything else.

The fabric sweep changes only ToR-to-spine bitrate. Every run has 16 GPUs,
4 ToRs, 4 spines, and 4 equal-cost uplinks per ToR.

| Condition | Each ToR to spine | Aggregate uplink | Host-facing |
| --- | --- | --- | --- |
| 1:1 | 200 Gbps | 800 Gbps | 800 Gbps |
| 2:1 | 100 Gbps | 400 Gbps | 800 Gbps |
| 4:1 | 50 Gbps | 200 Gbps | 800 Gbps |

GPU-to-ToR links stay 200 Gbps and 500 ns. ToR-to-spine delay stays 500 ns.

The number that matters is

```text
S = T(1D Ring [16]) / T(2D Ring [4,4])
```

S below 1 means the flat ring finished first. S above 1 means the 2D ring
finished first. The plot is `results/relative-speedup.png`, one curve per
transport. Absolute completion time is in `results/cct.png`. A transport that
only moves both curves up or down, without changing which collective is ahead,
is not evidence that congestion control changed the collective decision.

## E3-v1

`experiments/exploratory/e3-ccl-cca-oversubscription/` is the historical run.
It changed the spine count (4, 2, then 1) in order to change aggregate
capacity, so it also changed ECMP width and path assignment. Its 4:1 speedups
are not evidence that tightening the fabric helped `[4,4]`. Do not mix those
rows with this matrix.

## Transports

Primary matrix, 18 runs: 3 capacities × 2 collectives × 3 transports.
`TRANSPORT_CONNECTION_MODE` is `PERSISTENT`. Each directed rank pair keeps one
queue pair, one source port, and therefore one ECMP path. Stage A uses the
backend's built-in seed, the switch node id. A multi-seed sweep is not part of
this matrix.

| Transport | Definition | What adapts |
| --- | --- | --- |
| `fixed-rate` | E0 `dcqcn.txt` with `CC_MODE 0`, `HAS_WIN 0`, `ENABLE_PFC 0` | nothing. Senders stay at the 200 Gbps NIC rate. Switches still mark ECN; the sender ignores it |
| `dcqcn` | E0 `dcqcn.txt`, `HAS_WIN 0`, `ENABLE_PFC 0` | DCQCN rate |
| `hpcc` | E0 `hpcc.txt`, `HAS_WIN 1`, `ENABLE_PFC 0` | HPCC rate and window |

PFC is off on purpose. A pause would stall the ring independently of the rate
or window controller. PFC-only is not in this matrix.

HPCC can slow down with zero ECN marks, because the window tracks in-network
utilization. `hpcc_updates`, `hpcc_min_window_bytes`, and `hpcc_min_rate_bps`
are the record of that. Zero ECN is not "HPCC did nothing."

Fixed-rate and DCQCN still start at the NIC rate, 200 Gbps, even when the
uplink is 50 or 100 Gbps. HPCC's initial window is the path BDP, so it shrinks
when the uplink shrinks. That is HPCC's own rule, recorded per run, not an
extra topology change.

## ECN thresholds

The backend looks up Kmin and Kmax by exact bitrate. These files do not reuse
the stock 100 Gbps row (400 / 1600 KB) and do not borrow the 40 Gbps row for
50 Gbps. Thresholds are proportional to the validated E0 200 Gbps entry so
that Kmin is about the same amount of queueing time on each rate. This is an
experimental normalization, not a claim about canonical DCQCN parameters.
Pmax stays 0.2. Host-facing 200 Gbps ports keep 600 / 2400 KB.

| Link rate | Kmin | Kmax |
| --- | --- | --- |
| 200 Gbps | 600 KB | 2400 KB |
| 100 Gbps | 300 KB | 1200 KB |
| 50 Gbps | 150 KB | 600 KB |

## What the collectives actually send

Both use one 64 MiB AllReduce, four dataset splits, and ASTRA's ring
implementation. `localBWAware` here selects the phase list. It does not
reorder dimensions by measured bandwidth.

`[16]`, one clockwise ring, four sequential 16 MiB chunks. Each chunk is one
all-reduce of 30 steps of 1 MiB, all to rank+1. Reduce-scatter and all-gather
are the two halves of that phase, not separate neighbor sets.

| | `[16]` | `[4,4]` |
| --- | --- | --- |
| Messages | 1920 | 768 |
| Total logical bytes | 1920 MiB | 1920 MiB |
| Per rank | 120 MiB | 120 MiB |
| Same-rack payload | 1440 MiB | 1536 MiB |
| Cross-rack payload | 480 MiB | 384 MiB |
| Cross-rack directed pairs | 4 | 16 |
| Simultaneous cross-rack flows per rack | 1 | 4, during dimension 1 |

`[4,4]` per 16 MiB chunk, in order: reduce-scatter on dimension 0 (clockwise,
4 MiB), ring all-reduce on dimension 1 (clockwise stride 4, 1 MiB), all-gather
on dimension 0 (anticlockwise, 4 MiB). Dimension 0 of the next chunk can
overlap dimension 1 of the current one. The two dimension-0 directions share
one queue and do not overlap.

`scripts/validate.py` recomputes this from the same placement rules as
`GeneralComplexTopology` and exits if a count moves. `analyze.py` then checks
`flows.csv` against the same pairs. The logical schedule does not depend on
the uplink rate. Persistent paths for one collective must also match across
the three capacities; a mismatch means placement changed and the capacity
sweep is no longer clean.

## Metrics

Each run writes `results/raw/<fabric>__<collective>__<transport>/`:

- `meta.json`, the manifest: commits, rates, ECN map, seed, mode, buffer
- `connections.csv`, one persistent pair with its port, path, and spine
- `summary.json`, completion time, queues by location, ECN, CNP, drops,
  utilization, peak concurrent cross-rack flows, DCQCN rate decreases, HPCC
  window and rate
- `flows.csv`, `paths.csv`, `counters.txt`, `link_report.txt`, `cca_trace.csv`,
  `qlen.txt`

Accepted runs have zero admission drops and zero outstanding messages.
Queues are reported separately for ToR uplinks, spine ports, and ToR ports
that face GPUs. The v1 congestion was not only on uplinks.

`results/summary.csv` is the Stage A table. Plots:

- `relative-speedup.png`
- `cct.png`
- `queue-by-location.png`
- `traffic-concentration.png`, from the fixed-rate runs, which have no controller
- `cca-response.png`, minimum rate and HPCC update count on the 4:1 2D ring

## How to run

```bash
bash scripts/run.sh --smoke
bash scripts/run.sh
```

The smoke runs are 1:1 / both collectives / fixed-rate, then 2:1 and 4:1 /
2D Ring / DCQCN. They check four spines, a stable persistent path, the logical
byte counts, the ECN row actually programmed onto the uplink, and DCQCN
feedback when an uplink queue crosses Kmin. The other 14 runs start only after
that.

## What would count

A strong result is a ranking that depends on the transport: fixed-rate prefers
one collective and DCQCN or HPCC prefers the other, or the capacity at which
they cross is not the same. A weak result is three copies of the same S curve
with different absolute times. That means the controller changed a constant
and not the collective decision. That outcome is reported as negative evidence.
It is not a reason to add another knob.

## Limits that remain

Persistent mode removes per-message ECMP churn. It does not remove hash
collision. Four dimension-1 flows can still land on one of the four uplinks.
Stage A is one seed. If that seed is badly unbalanced, the concentration plot
will show it; a five-seed repeat is a follow-up, not part of this matrix, and
the backend has no seed override yet (the seed is the switch id).

ACK traffic is hashed with the ports swapped, so the return path can differ
from the data path. Both are stable for a given connection.

There is still no NVLink. Comparing `[16]` with `[4,4]` compares two ring
schedules on one Clos, not a scale-up hierarchy with a scale-out fabric.
