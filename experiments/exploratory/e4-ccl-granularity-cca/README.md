# E4: collective granularity × congestion control

Exploratory mechanism experiment. It asks whether the duration of each collective communication unit, relative to the feedback and recovery time of the transport, changes how PFC, DCQCN, and HPCC behave.

The total application payload is 64 MiB in every condition. The collective is one flat ring AllReduce over 16 GPUs. Rank placement, links, buffers, routing, and the CCA configuration stay fixed. The only sweep is how that 64 MiB is divided.

The 1:1 Clos is the control. It shows what repeated collective startup costs when the fabric is not oversubscribed. The 2:1 Clos is the constrained fabric. A 4:1 fabric is not part of this matrix. No novelty claim follows from a result until it has been checked against prior work.

## What is held fixed

16 GPUs, 4 racks of 4. Rack 0 holds ranks 0–3, rack 1 holds 4–7, rack 2 holds 8–11, rack 3 holds 12–15. Each rack has one ToR. Every link is 200 Gbps and 500 ns, with error rate 0. Switch buffers are 32 MiB. Packet size, PFC, ECN thresholds, and ECMP are the Experiment 0 settings. The routing seed is the switch node id.

Logical topology is `[16]`. The ring is 0→1→…→15→0. There is no 2D comparison here.

CCA files are copies of the validated Experiment 0 configs. PFC-only and DCQCN use `HAS_WIN 0`. HPCC uses `HAS_WIN 1`. Nothing is retuned for a chunk count.

## Granularity

One Chakra node issues one AllReduce of 67,108,864 bytes. ASTRA-sim divides it with `preferred-dataset-splits`:

| K | splits | chunk |
| --- | --- | --- |
| 1 | 1 | 67,108,864 B |
| 4 | 4 | 16,777,216 B |
| 16 | 16 | 4,194,304 B |
| 64 | 64 | 1,048,576 B |

`Sys::determine_chunk_size` sets the chunk to `size / preferred_dataset_splits`. For these four values the chunk stays above the 16-rank floor, so the total remains 64 MiB. `generate_collective` inserts K streams. `active-chunks-per-dimension` is 1 and scheduling is FIFO, so the streams run one after another with no added compute. That scheduling choice orders the chunks. It does not change a CCA parameter.

Serialization time of one chunk at 200 Gbps, ignoring collective and network overhead, is about 2.68 ms, 0.67 ms, 0.17 ms, and 0.042 ms. Those numbers only explain why this range is worth measuring. They are not predicted completion times. DCQCN's validated rate-increase timer is 900 µs, so the shorter chunks are below that recovery interval.

## Transport state does not persist

This is native dataset splitting, not K separate AllReduce nodes. It is still not NCCL-style chunking on one queue pair.

`send_flow` allocates a new source port on every `sim_send`, which creates a new queue pair. DCQCN's alpha and rate, and HPCC's rate and window, are initialized on that queue pair. The next ring hop and the next chunk both start from line rate. A later chunk cannot begin while the sender is still throttled from the previous chunk, because there is no previous queue pair to inherit.

The measured question is therefore whether a fresh congestion-control reaction fits inside one short message, not whether rate state carries across chunks. `configs/chunking.txt` records this. The scripts do not change `send_flow` or any congestion-control algorithm. Sender-rate traces are not collected: the backend's rate log is compiled off, and turning it on would edit the congestion-control source.

## Matrix

2 fabrics × 4 chunk counts × 3 CCAs = 24 runs. Run ids look like `2to1__k16__dcqcn`.

Derived numbers, filled once both sides of the ratio exist:

- CCA penalty: `CCT(cca, K) / CCT(PFC, K)` on each fabric.
- Granularity penalty: `CCT(cca, K) / CCT(cca, K=1)` on each fabric.
- Congestion slowdown: `CCT(2:1) / CCT(1:1)` at the same K and CCA.

K=64 being slower than K=1 is ordinary repeated collective startup. The result that matters is a slowdown, or a CCA penalty, that changes with K on the 2:1 fabric and does not change the same way on 1:1.

Per-chunk completion times are not reported. Flow records do not label which dataset split they belong to, and boundaries are not inferred from packet times.

## Run

From this directory, after the ns-3 frontend is built:

```bash
bash scripts/run.sh --smoke
bash scripts/run.sh
```

Smoke runs 1:1/K=1/PFC, 1:1/K=64/PFC, 2:1/K=1/DCQCN, and 2:1/K=64/DCQCN. The matrix stops if K=64 does not use shorter messages than K=1, or if 2:1/K=1/DCQCN shows no queue, PFC, ECN, or CNP. Finished runs are skipped. `results/summary.csv` and `results/manifest.csv` are rewritten from whatever has finished. Plots are written only when matplotlib is installed.

An idle gap between chunks is not part of this run. Add that only if the 24-run table shows a DCQCN penalty that grows with K.
