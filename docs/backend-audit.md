# Backend audit: ASTRA-sim + astra-network-ns3 as checked out

This records what the checked-out source does before any extension was added.
File paths are relative to `cca-ccl-study/astra-sim/` unless noted. Line
numbers refer to the commits below.

## 1–3. Commits

| Tree | Commit | Remote |
| --- | --- | --- |
| study (`cca-ccl-study`) | `0566cdd` "Setting up experiments E3, E4, and E5" | `TeeronHT/cca-ccl-study` |
| `astra-sim` submodule | `c1cb32a28ec8cac0228282e8158cab3111f8380e` "Point ns-3 backend to research fork" | `TeeronHT/astra-sim` (upstream `astra-sim/astra-sim`) |
| `astra-sim/extern/network_backend/ns-3` | `f764bed27630ee45368c03e22e4f952b69fab30a` "Merge pull request #19 … headroom-size-patch" (ns-3.42) | `TeeronHT/astra-network-ns3` (upstream `astra-sim/astra-network-ns3`) |

Both submodule working trees were clean at audit time. The E6 directory and
one `__pycache__` were untracked in the study repo.

## 4. `sim_send`

`astra-sim/network_frontend/ns3/AstraSimNetwork.cc`,
`ASTRASimNetwork::sim_send` (line 122). It ignores `buffer`, `type` and
`request`, and calls `send_flow(src_id=rank, dst_id, message_size, msg_handler,
fun_arg, tag)`. `message_size` is `uint64_t` here but `send_flow` takes it as
`int maxPacketCount`, so a single logical message is limited to 2 GiB.

The System layer reaches it through `Sys::front_end_sim_send`
(`astra-sim/system/Sys.cc:1246`), which rewrites the tag into a range by
type (`NATIVE`, `COLLECTIVE`, `RENDEZVOUS` offsets in `Sys.hh:177`) and, when
`--rendezvous-protocol=true`, goes through `Sys::rendezvous_sim_send`
(`Sys.cc:1306`) first. Callers: `Ring::ready` (`Ring.cc:230`, also used by
`AllToAll`, i.e. the `direct` implementation), `HalvingDoubling`,
`DoubleBinaryTreeAllReduce`, `CustomAlgorithm`, and
`Workload::issue_send_comm` (`workload/Workload.cc:393`) for Chakra
`COMM_SEND_NODE`.

## 5. `send_flow`

`astra-sim/network_frontend/ns3/entry.h:127`. Per call it:

1. takes a new source port `portNumber[src][dst]++` (starts at 10000 for
   every host pair, `scratch/common.h:893`);
2. records `sender_src_port_map[(port, (src,dst))] = tag`;
3. stores a `MsgEvent` in `sim_send_waiting_hash[((tag,(src,dst)), port)]`;
4. builds an `RdmaClientHelper(pg=3, sip, dip, port, dport=100, size, win,
   baseRtt, …)` and installs an `RdmaClient` application on the source node
   with `appCon.Start(Time(0))`.

`Node::AddApplication` schedules `Application::Initialize` at +0, which
schedules `StartApplication` at +0, which calls
`RdmaDriver::AddQueuePair` (`src/applications/model/rdma-client.cc:122`). So
the queue pair is created two zero-delay events after `sim_send`, in a
separate event. `RdmaClient::SetFn` self-assigns its parameters
(`rdma-client.cc:112`), so the client never holds the ASTRA callback; the only
completion path is `qp_finish` below. One application object is leaked per
send.

Window: `has_win ? (global_t == 1 ? maxBdp : pairBdp[src][dst]) : 0`;
base RTT: `global_t == 1 ? maxRtt : pairRtt[src][dst]`.

## 6. Source ports

Per `(src,dst)` pair, `uint16_t` counter from 10000, incremented on every
`send_flow` (`entry.h:130`). Never reused. 55,536 sends per pair before wrap.
Destination port is always 100. Priority group `pg` is always 3.

## 7. `RdmaQueuePair` creation

`RdmaHw::AddQueuePair` (`src/point-to-point/model/rdma-hw.cc:230`):
`CreateObject<RdmaQueuePair>(pg, sip, dip, sport, dport)`, sets
src/dest/tag/size/win/baseRtt/varWin and both app callbacks, picks the NIC
via `GetNicIdxOfQp` (`m_rtTable[dip][GetHash() % n]`), appends to that NIC's
`RdmaQueuePairGroup`, inserts into `m_qpMap[GetQpKey(dip, sport, pg)]`,
initialises rates to the NIC line rate (DCQCN target rate, HPCC `hp.m_curRate`
and per-hop `Rc`, TIMELY, PINT), then `dev->NewQp(qp)` which sets
`m_nextAvail = Now()` and calls `DequeueAndTransmit`.

`RdmaQueuePair` constructor (`rdma-queue-pair.cc:23`) sets
`startTime = Now()`, `snd_nxt = snd_una = 0`, `mlx.m_alpha = 1`,
`mlx.m_first_cnp = true`, `hp.u = 1`, etc. There is no QP id field.

The receive side: `RdmaHw::GetRxQp(..., create=true)` in `ReceiveUdp`
(`rdma-hw.cc:340`) creates an `RdmaRxQueuePair` keyed by
`(dip=sender ip, pg, dport=sender sport)` on first packet, with
`ReceiverNextExpectedSeq = 0`.

## 8. QP completion and destruction

`RdmaHw::ReceiveAck` (`rdma-hw.cc:438`): `qp->Acknowledge(seq)` then
`if (qp->IsFinished()) QpComplete(qp)` where `IsFinished` is
`snd_una >= m_size`. `RdmaHw::QpComplete` (`rdma-hw.cc:548`):

1. if `m_cc_mode == 1`, cancels `mlx.m_eventUpdateAlpha`,
   `mlx.m_eventDecreaseRate`, `mlx.m_rpTimer`;
2. fires `m_qpCompleteCallback(qp)` → `RdmaDriver::QpComplete` → trace
   `QpComplete` → `qp_finish(fout, qp)` in `entry.h:261`, which writes the
   `fct.txt` line, deletes the receiver's `RdmaRxQueuePair`
   (`DeleteRxQp(sip, pg, sport)`), looks up and erases the tag, calls
   `notify_sender_sending_finished` (erases the waiting `MsgEvent`, invokes
   the ASTRA send callback), then `notify_receiver_receive_data` (matches
   bytes against posted `sim_recv`s FIFO per `(tag,src,dst)`);
3. `qp->m_notifyAppFinish()` (`RdmaClient::Finish`, empty);
4. `DeleteQueuePair(qp)` erases `m_qpMap[key]`.

The QP object is also dropped from the NIC round-robin list by
`RdmaEgressQueue::GetNextQindex` (`qbb-net-device.cc:94`), which compacts
every QP whose `IsFinished()` is true out of `m_qpGrp->m_qps`. ACKs that
arrive after `DeleteQueuePair` find no QP (`GetQp` returns null) and are
ignored (`ReceiveAck` returns early), so congestion feedback generated by a
finished message is discarded.

Receiver notification happens when the *sender* sees the final ACK, not when
the last byte arrives.

## 9. DCQCN state

All per-QP, in `RdmaQueuePair` (`rdma-queue-pair.h:37`):

| State | Field |
| --- | --- |
| current rate | `m_rate` |
| target rate | `mlx.m_targetRate` |
| alpha | `mlx.m_alpha` |
| alpha timer | `mlx.m_eventUpdateAlpha` (every `ALPHA_RESUME_INTERVAL` µs, `UpdateAlphaMlx`) |
| decrease timer | `mlx.m_eventDecreaseRate` (every `RATE_DECREASE_INTERVAL` µs, `CheckRateDecreaseMlx`) |
| recovery timer | `mlx.m_rpTimer` (every `RP_TIMER` µs, `RateIncEventTimerMlx`), stage `mlx.m_rpTimeStage` |
| CNP flags | `mlx.m_alpha_cnp_arrived`, `mlx.m_decrease_cnp_arrived`, `mlx.m_first_cnp` |

Parameters (`m_g`, `m_rai`, `m_rhai`, `m_rpgThreshold`, `m_minRate`, …) live
on `RdmaHw`. The timers are started on the first CNP (`cnp_received_mlx`,
`rdma-hw.cc:686`) and run until `QpComplete` cancels them. Rate increase is
purely timer driven (`RateIncEventTimerMlx`), so a QP that stays alive keeps
recovering while idle.

## 10. HPCC state

Per QP: `m_rate` (applied rate), `hp.m_curRate` (reference rate),
`hp.m_incStage`, `hp.u`, `hp.m_lastUpdateSeq`, INT snapshots `hp.hop[]`, and
per-hop `hp.hopState[i].{u, Rc, incStage}` (`rdma-queue-pair.h:49`). The
window is `m_win` (set from BDP at creation when `HAS_WIN 1`); `GetWin()`
scales it by `m_rate/m_max_rate` when `VAR_WIN 1`. `UpdateRateHp`
(`rdma-hw.cc:802`) divides the queue term by `qp->m_win`, which is why
`HAS_WIN 0` breaks HPCC (division by zero → inf utilisation → `min_rate`).

## 11–12. ECMP

`SwitchNode::GetOutDev` (`switch-node.cc:63`): hashes
`{sip, dip, sport | dport<<16}` (UDP: `udp.sport/dport`; ACK/NACK:
`ack.sport/dport`) with `EcmpHash(buf, 12, m_ecmpSeed)` and indexes
`m_rtTable[dip]` (the next-hop list built by `CalculateRoute`'s hop-count
BFS in `scratch/common.h:210`). `m_ecmpSeed = m_id` (switch node id). The
source port is part of the hash, so every legacy `send_flow` can take a
different equal-cost path. On hosts, `RdmaHw::GetNicIdxOfQp` hashes the same
4-tuple to pick among multiple NICs; all study hosts have one NIC.

## 13. Completion callbacks ↔ flows

The ASTRA callback is stored only in `sim_send_waiting_hash` keyed by
`((tag,(src,dst)), sport)`; the tag is recovered from `sender_src_port_map`
by `(sport,(src,dst))`. The QP carries `m_tag`, `m_src`, `m_dest` but the
frontend does not use them. One QP ⇔ one `MsgEvent` ⇔ one callback.

## 14. Rendezvous

`Sys::rendezvous_sim_send` (`Sys.cc:1306`) does not inject payload. It posts a
`sim_recv` for an 8192-byte handshake with tag `tag + RENDEZVOUS`. The
receiver's `rendezvous_sim_recv` (`Sys.cc:1334`) issues that 8192-byte
`sim_send` toward the sender as soon as the receive is posted, then posts
the real receive. When the handshake arrives, `Sys::handleEvent`
(`RendezvousSend`) calls the deferred payload `sim_send`. Consequences: each
payload adds one extra 8 KiB network message in the reverse direction, that
message is an ordinary flow (own port/QP in legacy), and the payload cannot
be injected before the peer has posted its receive. `HardwareResource`
allows one in-flight `COMM_SEND_NODE`/`COMM_COLL_NODE` per rank; the node is
held through the handshake wait.

## 15. Buffer size

`scratch/common.h:743`: `sw->m_mmu->ConfigBufferSize(buffer_size * 1024 * 1024)`
for every switch; `BUFFER_SIZE` is a single global in MiB. `SwitchMmu`
also uses `reserve = 4 KiB` per port and `resume_offset = 3 KiB`.

## 16. PFC headroom and pause threshold

`scratch/common.h:732`:
`uint32_t headroom = rate * delay / 8 / 1000000000 * headroom_factor;`
(`rate` in bit/s, `delay` in ns time steps, `HEADROOM_FACTOR` default 3), per
switch port, then `ConfigHdrm`. `ConfigNPort` sums `total_hdrm` and
`total_rsrv` over ports. `SwitchMmu::GetPfcThreshold(port)` is
`(buffer_size - total_hdrm - total_rsrv - shared_used_bytes) >> pfc_a_shift[port]`
with every operand `uint32_t`. If `total_hdrm + total_rsrv >= buffer_size`
the subtraction wraps, the threshold becomes ~4 GiB, PFC never fires and
admission never drops. `pfc_a_shift` starts at 3 (α = 1/8) and is reduced by
one for each doubling of port rate above the first host's NIC rate.

PFC pause frames are generated by `SwitchNode::CheckAndSendPfc` and honoured
by `QbbNetDevice::Receive` when `QbbEnabled` is true. `QbbEnabled` is never
set from the config: PFC is always on, in every CC mode, including
`PFC-only` and `DCQCN`/`HPCC`. `ENABLE_QCN` only controls ECN marking
(`SwitchNode::EcnEnabled`) and the unused `QbbNetDevice::QcnEnabled` flag.

Admission drops (`SwitchMmu::CheckIngressAdmission` false) `return` from
`SwitchNode::SendToDev` without firing any trace source. They print
`"<time> <node> Drop: queue:<port>,<q>: Headroom full"` to stdout. The
`mix.tr` `Drop` event only covers link-down cleanup and dead-link receives,
so `drop_count` derived from `mix.tr` does not see MMU drops.

## 17. Units

| Quantity | Unit |
| --- | --- |
| link delay in topology file | ns-3 time string (`500ns`, `250000ns`); stored as `GetDelay().GetTimeStep()` = nanoseconds (default resolution, `Time::SetResolution` is never called) |
| simulator time | ns (`Simulator::Now().GetTimeStep()`, `GetNanoSeconds()`); ASTRA `sim_get_time` returns `NS` |
| rate | bit/s (`DataRate::GetBitRate()`) |
| `pairRtt` | ns = `2 * one-way propagation + sum of per-hop serialization of one MTU` |
| `pairBdp` | bytes = `rtt * bw / 1e9 / 8` with `bw` the path bottleneck bit rate |
| headroom | bytes = `rate * delay_ns / 8 / 1e9 * HEADROOM_FACTOR` (one-way) |
| `BUFFER_SIZE` | MiB |
| `KMIN_MAP`/`KMAX_MAP` | KB (×1000 in `ConfigEcn`), keyed by exact bit rate |
| DCQCN timers | µs (`MicroSeconds(...)`) |
| Chakra `duration_micros` | µs, multiplied by 1000 in `Workload` (zero becomes 1 ns) |

## 18. ECN maps

`ReadConf` (`scratch/common.h:470`) reads `KMAX_MAP n rate k ...`,
`KMIN_MAP`, `PMAX_MAP` into `unordered_map<uint64_t, …>` keyed by the exact
bit rate. `SetupNetwork` asserts an entry exists for every switch port rate
(`NS_ASSERT_MSG(... "must set kmin for each link speed")`) and calls
`ConfigEcn(port, kmin*1000, kmax*1000, pmax)`. Marking is per egress
queue in `SwitchMmu::ShouldSendCN`: always above `kmax`, linearly
probabilistic between `kmin` and `kmax`.

## 19. Queue monitor

`monitor_buffer` (`scratch/common.h:158`) runs every `QLEN_MON_INTERVAL`
(100 ns) from `QLEN_MON_START`, sums `egress_bytes[port][*]` for each switch
port, and writes a port only `if (size >= 1000)`. `QLEN_MON_END` is parsed
and never used; the monitor runs until the process exits.

## 20. Existing connection-like concepts

None at the transport layer. There is no connection, session, communicator
or channel object in the ns-3 backend; every logical message is its own
`RdmaQueuePair` and `RdmaRxQueuePair`. ASTRA has `CommunicatorGroup`
(collective membership) and per-dimension queues (`vnet`), but neither
reaches `send_flow`. `sim_request` (`astra-sim/system/Common.hh:28`, duplicated
byte-for-byte in `astra-sim/common/Common.hh` under the same include guard)
carries `srcRank, dstRank, tag, reqType, reqCount, vnet, layerNum` and is
ignored by the ns-3 frontend.

## Other facts that constrain the design

- `SeqTsHeader`/`CustomHeader` sequence numbers are 32-bit; a QP whose
  cumulative byte count exceeds 4 GiB would wrap.
- `MsgEvent::remaining_msg_bytes` is `int`.
- `RdmaClient::StartApplication` is the only caller of `AddQueuePair`.
- `SIMULATOR_STOP_TIME` is parsed and never applied; the run ends when the
  completion tracker calls `Simulator::Stop(); Simulator::Destroy(); exit(0)`
  after every rank calls `sim_notify_finished`.
- CNP is carried as a flag on ACK/NACK (`qbbHeader::FLAG_CNP`); standalone
  `0xFF` CNP packets are never generated. `ReceiveCnp` is dead code.
- `PCIePause` is a no-op with `NIC_TOTAL_PAUSE_TIME 0`.
