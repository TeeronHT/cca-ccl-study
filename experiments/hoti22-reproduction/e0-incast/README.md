# Experiment 0: single-switch incast

Seven hosts each send 10 MiB (10485760 bytes) to host 0 at the same time. One switch connects all eight hosts. This is the PFC-only vs DCQCN case from the HOTI’22 RoCE congestion-control setup. DCTCP, TIMELY, and HPCC use the same files with `CC_MODE` changed; they are not run here.

The simulator sources are not modified. Traffic goes through the existing ASTRA-sim send path.

## Run

From this directory:

```bash
./scripts/run.sh
```

The script records the study repo, ASTRA-sim, and ns-3 commits in `results/commits.txt`, builds `AstraSimNetwork` if it is missing, writes the Chakra traces, runs PFC-only and DCQCN, and writes a queue plot. Rebuild with `./scripts/run.sh --rebuild`. Each run is killed if it is still going after 900 seconds. OpenMPI is required: `qbb-helper.cc` includes `ns3/mpi-interface.h` even for a one-process run, and `./ns3 configure --enable-mpi` is what `build/astra_ns3/build.sh` uses. On macOS, `src/point-to-point/CMakeLists.txt` passes `-undefined dynamic_lookup` when linking that library, because `SeqTsHeader` is compiled into `libapplications` while `libapplications` also links point-to-point. `scratch/CMakeLists.txt` defines `FMT_USE_CONSTEVAL=0` so this Apple Clang can compile the bundled spdlog, and `spdlog_setup` uses `std::invoke_result` because ns-3 builds in C++20, which removed `std::result_of`. Neither change alters packet handling. If `cmake --version` has a pre-release suffix, the script puts a wrapper on `PATH` that reports only the numeric version, because `ns3`'s version check rejects strings like `4.2.0-rc3`.

Outputs, per mode, under `results/pfc-only/` and `results/dcqcn/`:

| File | Contents |
| --- | --- |
| `fct.txt` | Per-flow completion time |
| `qlen.txt` | Switch egress occupancy over time |
| `pfc.txt` | PFC pause and resume events |
| `mix.tr` | Binary packet trace for the switch, including ECN and CNP |
| `summary.txt` | Parsed completion time, max queue, pause count, ECN count, CNP count |
| `stdout.log` | Simulator log |

`results/plots/e0_queue_occupancy.png` overlays the busiest switch port from both runs. Sender rate is not in any of these files.

## Why the packet trace lists one switch

`configs/trace.txt` does not turn ECN on. It only names nodes whose packet events are dumped. This topology has one switch, node 8, and the incast queue sits on that switch’s link toward host 0. ECN marks are applied there. Hosts do not mark ECN, so tracing them would copy the same packets without adding marks.

ECN marking itself is `ENABLE_QCN` in the network config. It is applied to every switch. PFC-only sets it to 0. DCQCN sets it to 1.

## What each file controls

Paths below are relative to `astra-sim/` unless noted.

### Congestion-control mode

`CC_MODE` in `configs/pfc-only.txt` and `configs/dcqcn.txt`.

Parsed in `extern/network_backend/ns-3/scratch/common.h` (`ReadConf`) into `cc_mode`, then set on every host as `ns3::RdmaHw::CcMode` and on every switch as `ns3::SwitchNode::CcMode`.

| Value | Behavior | Where the algorithm lives |
| --- | --- | --- |
| 0 | No rate reaction. Sender stays at line rate. PFC still runs. This is PFC-only. | `rdma-hw.cc` has no branch for mode 0 |
| 1 | DCQCN | `RdmaHw::cnp_received_mlx` and the `UpdateAlphaMlx` / `CheckRateDecreaseMlx` / `RateIncEventMlx` path |
| 3 | HPCC | `HandleAckHp`; switch pushes INT when `m_ccMode == 3` in `switch-node.cc` |
| 7 | TIMELY | `HandleAckTimely` |
| 8 | DCTCP | `HandleAckDctcp` |
| 10 | HPCC-PINT | `HandleAckHpPint` |

`ENABLE_QCN` is separate. `0` disables ECN marking (`SwitchNode::EcnEnabled` in `switch-node.cc`). `1` enables it. PFC-only uses `CC_MODE 0` and `ENABLE_QCN 0`. DCQCN uses `CC_MODE 1` and `ENABLE_QCN 1`.

DCQCN’s numeric knobs are the values already in `extern/network_backend/ns-3/scratch/config/config.txt`. They are copied, not retuned:

| Key | Role | Code |
| --- | --- | --- |
| `ALPHA_RESUME_INTERVAL` | Alpha update period, microseconds | `RdmaHw::AlphaResumInterval` |
| `RATE_DECREASE_INTERVAL` | How often a CNP can cut the rate, microseconds | `RdmaHw::RateDecreaseInterval` |
| `CLAMP_TARGET_RATE` | Clamp the target rate on a decrease | `RdmaHw::ClampTargetRate` |
| `RP_TIMER` | Rate-increase timer, microseconds | `RdmaHw::RPTimer` |
| `EWMA_GAIN` | DCQCN/DCTCP EWMA gain `g` | `RdmaHw::EwmaGain` |
| `FAST_RECOVERY_TIMES` | Fast-recovery steps before additive increase | `RdmaHw::FastRecoveryTimes` |
| `RATE_AI`, `RATE_HAI`, `MIN_RATE` | Additive increase, hyper increase, floor | `RdmaHw::RateAI`, `RateHAI`, `MinRate` |
| `DCTCP_RATE_AI` | DCTCP additive increase only | `RdmaHw::DctcpRateAI` |

TIMELY’s alpha, beta, TLow, THigh, and MinRtt are not config keys. They are the `TimelyAlpha`, `TimelyBeta`, `TimelyTLow`, `TimelyTHigh`, and `TimelyMinRtt` attribute defaults in `src/point-to-point/model/rdma-hw.cc`. HPCC’s extra keys are `U_TARGET`, `MI_THRESH`, `INT_MULTI`, `MULTI_RATE`, `SAMPLE_FEEDBACK`, and `FAST_REACT`.

PFC is on by default (`QbbNetDevice::QbbEnabled`, default true). `USE_DYNAMIC_PFC_THRESHOLD 1` selects the dynamic threshold in `SwitchMmu::GetPfcThreshold`. `PAUSE_TIME` is not set, so the `common.h` default of 5 microseconds is used. `HEADROOM_FACTOR` is not set, so headroom is `rate * delay / 8 / 1e9 * 3` (`SetupNetwork` in `common.h`). `NIC_TOTAL_PAUSE_TIME 0` disables the extra PCIe pause model.

### Switch buffer

`BUFFER_SIZE 32` is mebibytes. `SetupNetwork` calls `SwitchMmu::ConfigBufferSize(buffer_size * 1024 * 1024)`, so the pool is 32 MiB.

ECN thresholds are `KMIN_MAP`, `KMAX_MAP`, and `PMAX_MAP`. Each entry is `rate_bps k`. The rate must match the link bitrate exactly. For 200 Gbps the entry is `200000000000`, and `ConfigEcn` multiplies `k` by 1000, so Kmin is 600000 bytes and Kmax is 2400000 bytes. `PMAX` at that rate is 0.2.

### Link bandwidth and latency

`configs/topology.txt`. First line: `node_count switch_count link_count` (9, 1, 8). Next line: the switch id, 8. Each remaining line is `src dst rate delay error_rate`. Hosts are 0–7. Every link is `200Gbps` and `500ns`, read by `QbbHelper::SetDeviceAttribute("DataRate")` and `SetChannelAttribute("Delay")` in `SetupNetwork`.

### Point-to-point traffic

`configs/flow.txt` is only the flow count `0`. `SetupNetwork` reads that count and does not schedule flows from the file. Messages start in `astra-sim/network_frontend/ns3/entry.h` `send_flow()`, which `ASTRASimNetwork::sim_send` calls.

`scripts/generate_workload.py` writes `workload/incast.<rank>.et`. Rank 0 has seven `COMM_RECV_NODE`s. Ranks 1–7 each have one `COMM_SEND_NODE` of 10485760 bytes to rank 0, with the tag equal to the sender rank. Those nodes have no dependencies, so `Workload::fire` issues them before `Simulator::Run`, and `send_flow` starts them at time 0.

`HAS_WIN 0` turns off the BDP window (`RdmaQueuePair::IsWinBound` is false when the window is 0). At 500 ns per link the one-way host-to-host delay is 1000 ns and the BDP is about 52 KB, below both Kmin (600 KB) and the PFC threshold. With the window left on, neither ECN nor PFC would engage and the two modes would look the same. Sending is still capped by `RATE_BOUND 1` at `RdmaQueuePair::m_rate`, which starts at line rate and which DCQCN then reduces.

`configs/logical-topology.json` asks for 8 ranks. `configs/memory.json` disables remote memory. `configs/system.json` sets `endpoint-delay` to 0. Its collective-algorithm entries are not used, because these traces are send/recv nodes rather than collective nodes.

### Queue occupancy

`QLEN_MON_FILE` and `QLEN_MON_START`. `monitor_buffer` in `common.h` samples switch egress bytes and writes a line when a port holds at least 1000 bytes:

```text
time <ns> <switch_id> j <port> <bytes> j <port> <bytes> ...
```

The sample period is `qlen_mon_interval`, hardcoded to 100 ns in `common.h`. `QLEN_MON_END` is parsed and not consulted. The monitor stops when the simulation stops.

### PFC

`PFC_OUTPUT_FILE`. Every Qbb device calls `get_pfc` on its `QbbPfc` trace. A line is:

```text
<ns> <node_id> <node_type> <ifindex> <type>
```

`type` 1 is pause and 0 is resume (`QbbNetDevice` trace source: 0 resume, 1 pause). The record is written on receive, so pauses show up on the hosts that the switch paused.

### ECN, CNP, and sender rate

`ENABLE_TRACE 1` plus `configs/trace.txt` (one node, id 8) writes `mix.tr`. The file starts with the `SimSetting` port-speed table from `sim-setting.h`, then fixed 56-byte `TraceFormat` records (`trace-format.h`). ECN is the `ecn` field. A CNP is an ACK or NACK (`l3Prot` `0xFC` or `0xFD`) whose flag bit 0 is set (`qbbHeader::FLAG_CNP`). The parser counts switch dequeues so each packet is counted once.

Sender rate is not logged. `PRINT_LOG` in `rdma-hw.cc` is 0, and changing it would touch the congestion-control source.

### Flow completion time

`FCT_OUTPUT_FILE`, written by `qp_finish_print_log` in `entry.h`:

```text
<sip hex> <dip hex> <sport> <dport> <bytes> <start_ns> <fct_ns> <standalone_fct_ns>
```

Node id is `(ip >> 8) & 0xffff`. The incast completion time is the maximum `fct_ns`, because every flow starts at time 0. The run ends when all eight ranks finish their Chakra traces (`NS3BackendCompletionTracker` calls `Simulator::Stop`). `SIMULATOR_STOP_TIME` is parsed in `ReadConf` and never applied.

## Parsed check

`scripts/analyze.py` prints completion time, maximum queue, PFC pauses, ECN-marked dequeues, and CNP dequeues. With both result directories it exits nonzero if the maximum queues are within 1.5x of each other and the pause counts are equal.
