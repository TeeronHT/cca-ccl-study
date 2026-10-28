# E6: temporal asymmetry × network feedback

Exploratory falsification test. It asks whether an asymmetry-resilient AllReduce can be ranked against Ring using only rank arrival times and a static byte cost, or whether the traffic sent before the last rank arrives changes congestion-control state enough to change the ranking.

No novelty claim follows from this experiment. It does not propose a scheduler.

The static picture is `T = max_i (tau_i + D_i * beta)`. Ring gives every rank essentially the full critical path, so a late rank delays the whole collective by that path. StragglAR uses the gap for a reduce-scatter among the ready ranks, then a shorter completion schedule. E6 asks whether beta stays fixed once that early traffic has marked ECN, generated CNPs, paused links, or cut sender rates.

## Schedules

Ring is ASTRA-sim's native 16-rank ring AllReduce of 67,108,864 bytes, one dataset split. Each rank has a COMP node, then the collective. `Workload::issue_coll_comm` starts that rank when its own COMP finishes. There is no collective-wide barrier. See `schedules/ring/mechanism.txt`.

StragglAR is not a new algorithm. `obtain_stragglar_schedule.py` runs the vendored power-of-two synthesizer from [arjundevraj/stragglar](https://github.com/arjundevraj/stragglar) at commit `3b3b6d2fd0310e03dbcd39c43940369ef2919150`, for `n = 16`, with rank 15 as the straggler. The artifact is `schedules/stragglar/schedule.json`. Provenance is `schedules/provenance/provenance.json`.

The translated workload has two phases:

- Precondition: a 15-rank ring reduce-scatter on ranks 0–14. Chunk sizes sum to 64 MiB (15 does not divide 64 MiB; four chunks are one byte larger).
- Completion: the synthesized matchings. Rank 15's sends depend on its COMP node.

Bulk bytes use ASTRA's existing `--rendezvous-protocol`. A send does not inject the payload until the peer has posted the receive, so rank 15 does not receive the completion payload before it is ready. The protocol adds an 8 KiB handshake per payload. That handshake is not part of the schedule's byte count; `flows.csv` labels it `kind=handshake`.

## Transport state across phases

E6 runs with `TRANSPORT_CONNECTION_MODE PERSISTENT` (set in
`configs/cca/*.txt`, see `docs/network-backend-extensions.md`). One queue
pair per directed rank pair carries every message between them, payload
and handshake alike, so DCQCN alpha/rate/timers and HPCC rate/window set
up by precondition traffic are what a completion message on the same pair
starts from. Queue occupancy carries as before.

Every StragglAR send node carries `comm_phase_id` (1 = precondition,
2 = completion) and `comm_chunk_id`; ASTRA copies them into the network
request and the frontend writes them to `flows.csv`, so phases are never
inferred from packet times. The state-overlap metric is

`completion QPs with precondition history / all completion QPs`

counted over connections (`completion_qps_with_precondition_history`,
`completion_connections`, `qp_overlap_fraction`). It does not assume every
completion connection was used in the precondition; StragglAR's
completion rounds pair ranks that never spoke during the reduce-scatter,
and those connections start fresh. The Ring has no phases, so the metric is
empty for it.

At the final rank's arrival (`tau`), `run_one.sh` asks the backend for a
state snapshot (`STATE_SNAPSHOT_TIMES 1 <tau>`): every live QP's rate,
target rate, alpha, HPCC rate and window, pending messages; every switch
port's occupancy and pause state; cumulative ECN marks, CNP-flagged ACKs,
drops, QPs and messages. `analyze.py` reports the active-QP count, mean and
minimum sender rate, the fraction below half line rate, queued bytes and
cumulative ECN/CNP at that instant (`*_at_last_arrival` columns) and keeps
the whole snapshot in `network_state_at_last_arrival.json`.

The two-late pattern still uses the one-late StragglAR schedule. It is not a two-straggler redesign.

## Fabric and CCAs

Same 16-GPU placement as E3. Primary fabric is the 2:1 Clos. The 1:1 fabric is only the six-run control at `tau = 0.50 T_ref`. Links are 200 Gbps and 500 ns. Buffers stay 32 MiB. CCA files are the E0 files: PFC and DCQCN with `HAS_WIN 0`, HPCC with `HAS_WIN 1`.

`T_ref` is the completion time of 2:1 / symmetric / Ring / PFC. Later gaps are 0.25, 0.50, and 1.00 times that value, rounded down to a microsecond because Chakra stores the delay in microseconds and ASTRA multiplies by 1000. A zero delay is replayed as 1 ns. The same absolute delays are used for every CCA.

## Matrix

42 primary runs on 2:1 (symmetric, one late rank, two clustered late ranks; Ring and StragglAR; PFC, DCQCN, HPCC). Plus 6 control runs on 1:1. The number that matters is Ring completion time divided by StragglAR completion time, and the smallest tested gap at which that ratio exceeds 1.

The static model in `paper_model.py` uses arrival time, the ring's `2(n-1)` chunks, and StragglAR's completion-round depth times the largest chunk. Beta is the 200 Gbps byte time, not a measured completion time. The synthesizer does not print a separate `q_res`, so that round depth is what the file stores, and the effective-beta column is omitted.

## Run

From `cca-ccl-study`, after the ns-3 frontend is built. Regenerating the schedule needs numpy.

```bash
bash experiments/exploratory/e6-temporal-asymmetry-feedback/scripts/run.sh --smoke
bash experiments/exploratory/e6-temporal-asymmetry-feedback/scripts/run.sh
```

Smoke 1 is the calibration. The script then builds the delayed workloads. Smoke 2 and 3 check that ready ranks move payload before `tau` and rank 15 does not send before `tau`. Smoke 4 requires, on the DCQCN StragglAR run, ECN or CNP, a PERSISTENT run whose connections carried more than one message, phase labels in `flows.csv`, and a backend snapshot at `tau`. A run with packet drops (including switch admission drops, now counted from `counters.txt`) is marked invalid. Plots are written only when matplotlib is installed.

Before the first E6 run, `experiments/backend-validation/legacy-regression`
and `experiments/backend-validation/persistent-qp` must have passed with the
same binary.
