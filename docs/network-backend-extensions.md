# Network backend extensions

This documents the research extensions added to the pinned ASTRA-sim and
astra-network-ns3 forks for the CCL × congestion-control experiments
(E3–E6). Every extension is off by default; with no new config key the
backend behaves as audited in `backend-audit.md`. No congestion-control
algorithm, constant, ECMP hash, Ring implementation or ASTRA collective was
changed.

## 1. Legacy behaviour

In the ns-3 frontend (`astra-sim/network_frontend/ns3/entry.h`) every
`sim_send` from the System layer becomes one `send_flow` call, which takes
the next source port for that `(src,dst)` pair (from 10000), installs an
`RdmaClient` application, and that application creates one
`RdmaQueuePair` sized to the message. The QP starts at line rate with fresh
DCQCN/HPCC state, is removed from the NIC's round-robin list and from
`m_qpMap` when its last byte is acknowledged, and its receiver-side
`RdmaRxQueuePair` is deleted at the same time. ACKs or CNPs that arrive
after that find no QP and are dropped.

## 2. Why one `sim_send` maps to one flow

The HPCC/ns-3 code base the backend descends from is a flow simulator: a
"flow" is `(sip, dip, sport, dport, size)` read from a flow file, and the
ASTRA port simply drives that interface from `sim_send`. ASTRA's completion
bookkeeping (`sim_send_waiting_hash`) is keyed by `(tag, src, dst, port)`
precisely because the port is the only unique per-message handle the ns-3
side has.

## 3. Why that behaviour is retained

E0–E2 reproduce HOTI'22-style results with exactly these semantics, upstream
ASTRA workloads implicitly depend on them, and the per-message flow model is
the right one for questions about instantaneous congestion (E3). It is kept
as `TRANSPORT_CONNECTION_MODE LEGACY`, the default, and
`experiments/backend-validation/legacy-regression` checks it against a
pre-extension baseline.

## 4. What PERSISTENT mode changes

`TRANSPORT_CONNECTION_MODE PERSISTENT` keeps a connection registry in the
frontend keyed by

```
ConnectionKey { src_node, dst_node, priority (pg, always 3), channel_id (0 unless ASTRA sets one) }
```

Message id, chunk id and collective phase are deliberately not part of the
key. The first message on a key allocates one source port (same allocator,
so the first connection of a pair uses the port the first legacy flow would
have used), creates one `RdmaQueuePair` with an empty sequence space
(`RdmaHw::CreatePersistentQp`), and records the ECMP path. Every message on
that key, including rendezvous handshakes, is then posted as a work request
(`RdmaHw::PostMessage` → `RdmaQueuePair::PostWork`): the QP's `m_size`
grows by the message length, the work request remembers its `[begin, end)`
byte range, and the NIC is kicked. The QP, its port, its DCQCN/HPCC state
and its DCQCN timers live until the workload finishes, when
`finalize_backend` destroys every connection (`DestroyPersistentQp`, which
cancels the timers and removes the sender QP and the receiver's rx QP) and
records `qp_create_time`, destroy time and messages carried in `paths.csv`.

An idle persistent QP is compacted out of the NIC's round-robin array like
any finished QP; `PostMessage` puts it back without touching `m_nextAvail`,
so the rate limiter's next-send time survives a back-to-back post. Packets
never straddle two work requests (`GetNxtPacket` stops at the work-request
boundary), as on a real QP.

The 32-bit sequence number limits one connection to 4 GiB over its
lifetime; `PostMessage` aborts with a clear message rather than wrapping.
E4–E6 connections carry about 120 MiB.

## 5. What PERSISTENT does not change

Packet formats, DCQCN/HPCC/TIMELY/DCTCP arithmetic and timers, PFC and ECN
marking, the switch MMU, ECMP hashing, routing, ASTRA's collective
algorithms and dependency semantics, the rendezvous protocol, and the
receiver notification convention (the receiver's callback fires when the
sender sees the final ACK of that message, as in legacy). Nothing is copied
between QPs: persistence is the absence of destruction, not a state
transfer.

## 6. Independent completion callbacks

Each logical message keeps its own `LogicalMessage` record (callback,
argument, tag, bytes, metadata) keyed by a frontend-wide message id. When an
ACK advances `snd_una`, `RdmaHw::CompleteWorkRequests` pops every work
request whose `end <= snd_una`, in order, and fires the QP's
`m_notifyMsgComplete` once per work request. The frontend
(`persistent_message_complete`) then writes the `fct.txt` line and the
flow-log row, calls that message's ASTRA handler, and runs the unchanged
receiver matching (`notify_receiver_receive_data`). Message 1's callback
never waits for message 2.

ASTRA can issue several messages on one connection before the previous one
completes: the HW resource allows one in-flight send or collective per
rank, but rendezvous handshakes bypass it and several receives from the same
peer post several 8 KiB handshakes at once. Persistent mode models these as
ordered work on one QP (FIFO), which can place a handshake behind payload in
the same direction; legacy ran them as parallel QPs. No second QP is created
because a connection is busy. If a separate control channel is ever wanted,
`channel_id` is already part of the key.

## 7. ECMP and path identity

`SwitchNode::GetOutDev` hashes `(sip, dip, sport, dport)`, so a persistent
connection keeps one path for all its messages while legacy flows can
spread across equal-cost paths. `ENABLE_PATH_TRACE 1` records, for every
connection (and every legacy flow), the host NIC choice and every switch's
next hop computed with the switches' own hash and tables
(`SwitchNode::LookupDataNextHop`, const) into `paths.csv` and the flow log.
CC-state persistence and path persistence are not separated by the
backend; the microbenchmark's `ecmp` topology records both so later
experiments can tell them apart.

## 8. PFC validation and ENABLE_PFC

`PFC_THRESHOLD_VALIDATION STRICT` aborts setup when, on any switch,
`total_headroom + total_reserve >= buffer` (the unsigned subtraction in
`SwitchMmu::GetPfcThreshold` would wrap), or when a single port's headroom
overflows 32 bits. The report lists switch id, every port's rate, delay and
headroom, the buffer, and the wrapped threshold. `LEGACY` (default) prints
the same report as a warning and proceeds exactly as the old binary did. No
clamping is performed in either mode.

`ENABLE_PFC 0` makes switches stop generating PAUSE/RESUME frames
(`SwitchNode::PfcEnabled`), makes every device ignore received PAUSE
frames (`QbbNetDevice::QbbEnabled false`), and sets headroom to 0, since
headroom only exists to absorb in-flight data after a pause. The dynamic
threshold then acts as a drop threshold; drops are counted (`mmu_drops`).
Default is 1 (PFC always on, as before).

## 9. Per-switch buffers

`SWITCH_BUFFER_MAP_FILE path` with `node_id buffer_MiB` lines overrides
`BUFFER_SIZE` for the listed switches only. Referenced nodes must exist and
be switches, values must be positive, and buffers above 4 GiB are refused
because `SwitchMmu` counts bytes in 32 bits. Effective per-switch values are
written to the link report. Without the key, behaviour is exactly the
global `BUFFER_SIZE`.

## 10. CCA tracing

`ENABLE_CCA_TRACE 0|1|2` with `CCA_TRACE_FILE` (observational,
`rdma-cc-trace.cc`). Level 1 records state changes: `QP_CREATE`,
`MESSAGE_START`, `MESSAGE_COMPLETE`, `QP_DESTROY`, `RATE_DECREASE`,
`RATE_INCREASE`, `HPCC_UPDATE`, DCTCP/TIMELY rate changes, enough to rebuild
each QP's rate timeline at timer/RTT granularity. Level 2 adds per-packet
feedback: `CNP_RECEIVED`, `ECN_RECEIVED`, `HPCC_FAST_REACT`. Each CSV row
carries time, event, connection and QP ids, endpoints, CC mode, current
rate, DCQCN target rate and alpha, HPCC reference rate, window, sequence
state and size. The old `PRINT_LOG` is untouched and still off.

`STATE_SNAPSHOT_TIMES n t1 … tn` (ns) with `SNAPSHOT_OUTPUT_FILE` dumps,
at each time, every live QP's controller state, every switch port's
occupancy, pause mask and tx bytes, and the cumulative counters. E6 uses it
at the final rank's arrival.

## 11. Message metadata and flow logging

`AstraSim::sim_request` gained optional `collective_id, operation_id,
phase_id, chunk_id, message_id, communicator_id, channel_id`
(`SIM_REQUEST_UNSPECIFIED` by default; other backends compile unchanged and
ignore them). `Ring::ready` sets `collective_id` (dataset id), `chunk_id`
(stream id, ordered by chunk index) and `phase_id` (collective type);
`Workload::issue_send_comm` sets `operation_id` (Chakra node id) and reads
optional Chakra attributes `comm_collective_id`, `comm_phase_id`,
`comm_chunk_id`, `comm_channel_id`.

`ENABLE_FLOW_METADATA_TRACE 1` with `FLOW_TRACE_FILE` writes one row per
logical message in both modes: message id, kind (payload/handshake),
the metadata, connection and QP ids, endpoints, ports, tag, bytes, issue
time, network start time (first byte for persistent, QP creation for
legacy), completion time and path. `fct.txt` keeps its format; in
PERSISTENT mode each row is a logical message and rows of one connection
share the port.

## 12. Counters, trace levels, queue log

`COUNTERS_OUTPUT_FILE` writes, at exit, sends issued by ASTRA, messages
accepted by ns-3, messages completed and outstanding, connections and QPs
created and destroyed, ECN marks, CNP-flagged ACKs, MMU drops, PAUSE/RESUME
frames, and per-switch-port tx bytes, marks, drops and pauses. The same
line is printed to stdout in every run. `Simulator::Run` returning before
all ranks finished now exits 2 with that line, so a stall is not analysed as
a result.

`TRACE_LEVEL 2` (default) keeps the packet trace and the queue monitor; 1
drops the packet trace; 0 drops both. `QUEUE_LOG_MIN_BYTES` (default 1000)
is the old per-port suppression threshold; `QLEN_MON_INTERVAL` (default
100 ns) is the sampling period. `LINK_REPORT_FILE` writes every link's rate,
delay and one-way BDP, every switch port's headroom and ECN thresholds,
every switch's buffer and validity, and every host pair's RTT and BDP as
the backend computed them.

## 13. Which experiment uses which mode

| Experiment | Mode | Notes |
| --- | --- | --- |
| E0 | LEGACY | configs untouched; legacy-regression baseline |
| E1 | LEGACY | configs untouched |
| E2 | LEGACY | configs untouched; `TRACE_LEVEL 1` is available for long runs but not applied |
| E3 | LEGACY primary | `run.sh --sensitivity` reruns the matrix PERSISTENT under a `__persistent` suffix |
| E4 | PERSISTENT required | `validate.py` refuses otherwise; `analyze.py --gate-persistence` must see reuse, carried state, chunk ids, paths |
| E5 | PERSISTENT required | fixed-rate / DCQCN / HPCC, PFC off, STRICT, border buffers 128 MiB, proportional ECN maps |
| E6 | PERSISTENT required + rendezvous | phase ids on every send, snapshot at tau, state-overlap metric |

## 14. Gates before any PERSISTENT experiment

1. `experiments/backend-validation/legacy-regression` passes against the
   pre-extension baseline.
2. `experiments/backend-validation/persistent-qp` passes (new QP/port and
   fresh state in LEGACY; same QP/port/connection and inherited state in
   PERSISTENT, for DCQCN and HPCC, back-to-back and with an idle gap).
3. `experiments/backend-validation/bdp-units` passes (analytic BDP,
   headroom and RTT equal the backend's; STRICT aborts exactly the
   over-committed cases).

E5's `run.sh` checks the three `results/PASSED` markers before starting.
