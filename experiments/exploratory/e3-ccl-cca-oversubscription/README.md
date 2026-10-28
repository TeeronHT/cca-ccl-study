# Experiment 3: collective organization × congestion control under oversubscription

E3 is exploratory. It is not claimed as a novel result. It asks whether the relative value of a rack-hierarchical AllReduce, compared with a flat AllReduce, depends on the congestion-control algorithm once the fabric is oversubscribed.

The workload, buffer size, per-link rate, latency, transport parameters, and routing policy stay fixed. The only changes are the number of spine uplinks, the collective organization, and the congestion-control mode.

```bash
bash scripts/run.sh --smoke   # three checks, then stop
bash scripts/run.sh           # those checks, then the other 15 runs
```

`results/summary.csv` is the full table. The number that matters is printed after analysis:

```text
hierarchy speedup = CCT_1D / CCT_2D
```

A value above 1 means the hierarchical AllReduce finished sooner. If the PFC, DCQCN, and HPCC columns stay close as oversubscription grows, collective choice is largely independent of CCA in this single-job setting. If they separate, the two layers are interacting. Do not treat that as a new result until it has been checked against prior work.

## Transport connection mode

The primary matrix runs with `TRANSPORT_CONNECTION_MODE LEGACY`: every
ASTRA message is its own queue pair with fresh congestion-control state, as
in E0–E2. That is the right setting for E3's first question, which is about
the instantaneous congestion each collective structure creates. The mode is
recorded in `meta.json`, `summary.csv` and `manifest.csv`.

`bash scripts/run.sh --sensitivity` reruns the 18 conditions with
`TRANSPORT_CONNECTION_MODE PERSISTENT` (one queue pair per rank pair, state
carried across messages) under run ids with a `__persistent` suffix. Those
rows are summarized separately and never mixed into the LEGACY ratios. See
`docs/network-backend-extensions.md`.

Every run also writes `counters.txt`, `flows.csv` and `paths.csv`
(observational; CCA parameters are untouched). `drop_count` now includes
switch admission drops, which the packet trace never recorded.

## What is held fixed

CCA files in `configs/cca/` are the Experiment 0 files. PFC-only and DCQCN use `HAS_WIN 0`. HPCC uses `HAS_WIN 1`. ECN thresholds, DCQCN timers, `U_TARGET`, minimum rate, PFC, packet size, and the 32 MiB buffer are unchanged. `scripts/validate.py` compares those keys with E0 and exits if they differ.

Every fabric link is 200 Gbps, 500 ns, error rate 0. Endpoint links are not slowed down. ECMP is the existing switch hash. Its seed is the switch node id (`SwitchNode` sets `m_ecmpSeed = m_id`). There is no extra random seed.

## Fabrics

16 GPUs. Ranks 0–3, 4–7, 8–11, and 12–15 are the four racks. Each rack has one ToR. `scripts/generate_topologies.py` connects every ToR to every spine:

| Fabric | Spines | Uplinks per ToR | Endpoint capacity | Uplink capacity |
| --- | --- | --- | --- | --- |
| 1:1 | 4 | 4 × 200 Gbps | 800 Gbps | 800 Gbps |
| 2:1 | 2 | 2 × 200 Gbps | 800 Gbps | 400 Gbps |
| 4:1 | 1 | 1 × 200 Gbps | 800 Gbps | 200 Gbps |

GPU ids are 0–15, ToRs are 16–19, and spines start at 20. On each ToR the first four Qbb interfaces face the GPUs and the rest face the spines. That follows installation order, with the loopback at ifIndex 0.

## Collectives

Both use one 64 MiB AllReduce (`64 * 1024 * 1024` bytes) and the native ring algorithm, with four dataset splits, the same system chunking as E1.

`[16]` is one clockwise ring `0→1→…→15→0`. The only rack-crossing edges are `3→4`, `7→8`, `11→12`, and `15→0`.

`[4,4]` is the hierarchy this code already builds. Dimension 0, stride 1, is `{0,1,2,3}`, `{4,5,6,7}`, `{8,9,10,11}`, `{12,13,14,15}`. Dimension 1, stride 4, is `{0,4,8,12}`, `{1,5,9,13}`, `{2,6,10,14}`, `{3,7,11,15}`. No rank remapping is applied. `validate.py` checks these groups.

## Metrics

Collective completion time is the finish time of the last AllReduce message. Inter-rack bytes are packet bytes dequeued on ToR uplinks, including headers. The static inter-rack payload is a ring-arithmetic lower bound on collective payload that must leave a rack, not a performance model. `static_lb_ns` divides that payload by the aggregate uplink capacity. Queue samples under 1000 bytes are absent from `qlen.txt`, so the p95 is over reported samples only.

Packet traces (`mix.tr`) and simulator logs are gitignored. Queue samples, flow times, PFC counts, and the summary files are not.

## Smoke gate

The first three runs are 1:1/1D/PFC, 1:1/2D/PFC, and 4:1/2D/DCQCN. The last must show a ToR-uplink queue, a PFC pause, an ECN mark, or a CNP. If it does not, the script stops instead of running the other 15 conditions.
