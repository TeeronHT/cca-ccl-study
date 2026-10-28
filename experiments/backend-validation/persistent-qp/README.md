# Backend validation: persistent queue pairs

Two messages from A to B, run under `TRANSPORT_CONNECTION_MODE LEGACY` and
`PERSISTENT`, for DCQCN and HPCC, on two topologies and two arrival cases.
This is the gate for using PERSISTENT in E4, E5 and E6.

```bash
bash scripts/run.sh          # 16 short runs, then the checker
bash scripts/run.sh --check  # checker only
```

| Topology | Links | Purpose |
| --- | --- | --- |
| `bottleneck` | A–S 200 Gbps, S–B 100 Gbps | queue at S→B so DCQCN decreases and HPCC updates happen during message 1 |
| `ecmp` | A–T1, T1–{S1,S2}, {S1,S2}–T2 at 200 Gbps, T2–B 100 Gbps | two equal-cost paths at T1, so port choice can change the path |

| Case | Sender trace |
| --- | --- |
| `b2b` | `SEND 16 MiB` → `SEND 4 MiB` (dependency only) |
| `gap` | `SEND 16 MiB` → `COMP 2000 µs` → `SEND 4 MiB` |

Network configs are the Experiment 0 `dcqcn.txt` / `hpcc.txt` with only the
paths and the extension keys changed (`ENABLE_CCA_TRACE 2`,
`ENABLE_FLOW_METADATA_TRACE 1`, `ENABLE_PATH_TRACE 1`, counters, link report,
`PFC_THRESHOLD_VALIDATION STRICT`, `QUEUE_LOG_MIN_BYTES 0`). No CCA
parameter is changed.

## What the checker requires

LEGACY: message 2 has a different QP id and source port than message 1, and
its `MESSAGE_START` record shows line rate, target rate = line rate,
alpha = 1 and HPCC reference rate = line rate.

PERSISTENT: both messages share one connection id, QP id and source port.
Message 2's `MESSAGE_START` state (rate, target rate, HPCC reference rate)
equals the last traced state of that QP, which is the `MESSAGE_COMPLETE` of
message 1 in the `b2b` case and the last DCQCN `RATE_INCREASE` timer event in
the `gap` case. The `gap` case must show at least one DCQCN increase during
the idle period and a rate that did not fall; HPCC must show no events during
the idle period and identical state afterwards, because HPCC has no timers.
Alpha must not grow across an idle gap without a CNP.

Both modes: both messages complete, `messages_outstanding = 0`, no MMU drops,
message 1 shows at least one DCQCN `RATE_DECREASE` (or HPCC `HPCC_UPDATE`).

Paths: the persistent connection has one path for both messages. On the
`ecmp` topology the two legacy flows may or may not hash to the same spine;
the checker prints both so that CC-state persistence and path persistence can
be told apart later. It does not try to separate them.

Outputs: `results/raw/<topology>__<cca>__<mode>__<case>/` with
`cca_trace.csv`, `flows.csv`, `paths.csv`, `counters.txt`, `link_report.txt`,
`fct.txt`, `qlen.txt`, `mix.tr`; `results/summary.json` with the side-by-side
numbers.
