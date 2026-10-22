# Experiment 0: single-switch incast

Eight hosts connect to one switch. Hosts 1–7 each send 10 MiB to host 0 at the same time. Links are 200 Gbps and 500 ns. The switch buffer is 32 MiB. The runs are PFC-only, DCQCN, DCTCP, TIMELY, and HPCC. This is the incast in Khan et al., HOTI 2022.

```bash
bash scripts/run.sh
```

Run that from this directory after `AstraSimNetwork` is built (`astra-sim/build/astra_ns3/build.sh`). The script writes the Chakra traces and runs all five modes. `results/summary.csv` has completion time, maximum queue, PFC pauses, ECN marks, CNP events, and drops for each mode. A queue plot is written to `results/plots/e0_queue_occupancy.png` when matplotlib is installed. Without it, the run still finishes and prints that the plot was skipped.

HOTI'22 reports tuning DCQCN parameters independently for each experiment but does not publish the resulting parameter values. We therefore use the current ASTRA/ns-3 DCQCN configuration, with the transport window disabled to permit congestion buildup. The resulting behavior qualitatively reproduces the reported incast dynamics: DCQCN converts large PFC-driven queues into ECN/CNP-driven rate control and eliminates PFC, at the cost of lower throughput under this parameterization.

## What controls each setting

All five modes share `configs/topology.txt`, `configs/flow.txt`, and `configs/trace.txt`. Each mode file copies `scratch/config/config.txt` and changes only `CC_MODE`, `ENABLE_QCN` for PFC-only, and the output paths.

| Setting | Where |
| --- | --- |
| Algorithm | `CC_MODE`: 0 PFC-only (`ENABLE_QCN 0`), 1 DCQCN, 8 DCTCP, 7 TIMELY, 3 HPCC. Parsed in `scratch/common.h` and applied as `RdmaHw::CcMode`. ECN marking is `ENABLE_QCN`, on for every mode except PFC-only |
| Other CCA knobs | Unchanged from `scratch/config/config.txt`, including `DCTCP_RATE_AI`. TIMELY’s alpha, beta, and delay thresholds are the `rdma-hw.cc` attribute defaults; they are not config keys |
| Transport window | `HAS_WIN 0` for PFC-only, DCQCN, DCTCP, and TIMELY, so the ~52 KB BDP does not cap those runs. HPCC uses `HAS_WIN 1`, because its utilization formula divides by that window |
| Buffer | `BUFFER_SIZE 32` is mebibytes (`ConfigBufferSize`) |
| ECN thresholds | `KMIN_MAP` / `KMAX_MAP` / `PMAX_MAP`. The 200 Gbps entry is used: Kmin 600000 bytes, Kmax 2400000 bytes, Pmax 0.2 |
| Bandwidth and latency | `configs/topology.txt`: hosts 0–7, switch 8, `200Gbps`, `500ns` |
| Traffic | `scripts/generate_workload.py` writes `workload/incast.<rank>.et`. `configs/flow.txt` is only a count of 0; this port does not schedule that file |
| Queue, PFC, FCT | `QLEN_MON_FILE`, `PFC_OUTPUT_FILE`, `FCT_OUTPUT_FILE`. The switch packet trace (`configs/trace.txt` lists node 8) is `mix.tr` |

Sender rate is not logged. `PRINT_LOG` in `rdma-hw.cc` is off.
