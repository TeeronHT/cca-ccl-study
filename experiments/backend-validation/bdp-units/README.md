# Backend validation: BDP, time units and PFC headroom

Checks that the backend's internal bandwidth-delay arithmetic matches an
analytic computation done outside the simulator, and that
`PFC_THRESHOLD_VALIDATION STRICT` refuses exactly the configurations whose
headroom cannot fit the switch buffer.

```bash
python3 scripts/bdp_units.py --analytic   # table only, no simulator
bash scripts/run.sh                       # prepare, run four cases, compare
```

Each case is host 0 — switch 2 — host 1 with both links at the case's rate
and one-way delay, one 1 MiB message, `BUFFER_SIZE 32`, `HEADROOM_FACTOR 3`,
`LINK_REPORT_FILE` on, `TRACE_LEVEL 0`.

| Case | One-way BDP | Headroom per port | 2 ports + reserve vs 32 MiB |
| --- | --- | --- | --- |
| 200 Gbps, 500 ns | 12,500 B | 37,500 B | valid |
| 100 Gbps, 1 ms | 12,500,000 B (100 Mb) | 37,500,000 B | invalid: STRICT must abort |
| 100 Gbps, 1.842 ms | 23,025,000 B | 69,075,000 B | invalid: STRICT must abort |
| 50 Gbps, 500 µs | 3,125,000 B | 9,375,000 B | valid |

Conventions the comparison relies on (from `docs/backend-audit.md`): link
delays are stored as nanosecond time steps; rates are bit/s; one-way BDP is
`rate × delay / 8 / 1e9` with integer division; headroom is that value times
`HEADROOM_FACTOR`; the pair RTT is `2 × (sum of one-way delays) + per-hop
serialization of one 1000-byte payload`; the RTT-BDP is `rtt × bottleneck /
1e9 / 8`.

For every case the script prints configured rate and delay, the delay the
backend stored, the time unit, expected and backend one-way BDP, expected
and backend headroom, expected and backend pair RTT and RTT-BDP, and whether
STRICT aborted. Aborts must carry the switch id, port ids, link rates and
delays, headroom and buffer in the message. Results go to
`results/bdp_units.csv`; the script exits non-zero on any mismatch.
