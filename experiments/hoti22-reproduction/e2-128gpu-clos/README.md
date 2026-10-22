# Experiment 2: 128-GPU two-level Clos AllReduce

HOTI’22 comparison of 1D and hierarchical 2D AllReduce on a 128-GPU Clos. Congestion control is copied from Experiment 0 at run time. Only the topology path and output paths are rewritten. HPCC keeps `HAS_WIN 1`. The other four modes keep `HAS_WIN 0`. No CCA parameter is changed.

```bash
bash scripts/run.sh
```

Run that from this directory after `AstraSimNetwork` is built. `results/summary.csv` has one row per layout and algorithm. The queue plot is written when matplotlib is installed and skipped otherwise.

## Topology

128 GPUs, 8 per server, 2 servers per rack, 8 racks. Ranks are numbered server-major: rank `8 * server + local`. Servers 0 and 1 share the first ToR, and so on.

| Nodes | Ids | Links |
| --- | --- | --- |
| GPUs | 0–127 | 200 Gbps, 500 ns, to that rack’s ToR |
| ToR | 128–135 | 16 downlinks each |
| Spine | 136–151 | 200 Gbps, 500 ns, from every ToR to every spine |

Each ToR has 16×200 Gbps down and 16×200 Gbps up, so the fabric is 1:1. This simulator stores one interface per neighbor pair, so the 16 uplinks are 16 spine switches rather than two parallel links onto 8 spines. Every switch buffer is `BUFFER_SIZE 32` (32 MiB), the same value as Experiment 0. `scripts/generate_topology.py` writes `configs/topology.txt` and `configs/trace.txt`. The trace lists every ToR and spine so ECN, CNP, and drop counts cover both tiers.

There is no separate NVLink network. Both dimensions of the 2D AllReduce use this Clos. A GPU talks to the other GPU in its server through their ToR.

## Workload

One 128 MiB AllReduce, `128 * 1024 * 1024` bytes, as a Chakra `COMM_COLL_NODE` on every rank. ASTRA-sim’s native direct algorithm runs it. `preferred-dataset-splits` is 4.

| Layout | Logical dims | System file |
| --- | --- | --- |
| 1D | `["128"]` | `configs/system-1d.json`, `all-reduce-implementation: ["direct"]` |
| 2D | `["8", "16"]` | `configs/system-2d.json`, `["direct", "direct"]` |

Dimension 0 is the 8 GPUs of one server. Dimension 1 is the 16 servers, taken at the same local GPU index. 2D is reduce-scatter then all-gather on dimension 0, then the same on dimension 1.

## Metrics

Collective completion time is the finish time of the last AllReduce message. Max ToR queue is the largest egress sample on nodes 128–135. Max spine queue is the largest sample on nodes 136–151. PFC pauses, ECN-marked dequeues, CNP dequeues, and drops are totals. Flow count is the number of lines in `fct.txt`. Sender rate is not logged.

## CCA

`scripts/run.sh` reads `../e0-incast/configs/{pfc-only,dcqcn,dctcp,timely,hpcc}.txt` and writes `results/<layout>/<cca>/network.txt`. `CC_MODE` is 0, 1, 8, 7, 3. `ENABLE_QCN` is 0 only for PFC-only.
