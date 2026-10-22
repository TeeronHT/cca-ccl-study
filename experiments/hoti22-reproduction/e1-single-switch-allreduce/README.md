# Experiment 1: single-switch AllReduce

Same network as Experiment 0. Eight hosts, one switch, 200 Gbps, 500 ns, 32 MiB buffer. The workload is one 10 MiB AllReduce on all eight ranks, issued as a Chakra collective node and run by ASTRA-sim’s native ring implementation (`configs/system.json`, four chunks).

Congestion-control files are the Experiment 0 files. PFC-only, DCQCN, DCTCP, and TIMELY keep `HAS_WIN 0`. HPCC keeps `HAS_WIN 1`. No other CCA parameter is changed.

```bash
bash scripts/run.sh
```

Run that from this directory after `AstraSimNetwork` is built. `results/summary.csv` has collective completion time (finish time of the last AllReduce message), maximum queue, PFC pauses, ECN marks, CNP events, and drops. The queue plot is written when matplotlib is installed and skipped otherwise.

Sender rate is not logged. `PRINT_LOG` in `rdma-hw.cc` is off.
