# Backend validation: legacy fidelity

Runs Experiment 0 PFC-only, DCQCN and HPCC and Experiment 1 DCQCN with the
unchanged E0/E1 inputs and `TRANSPORT_CONNECTION_MODE LEGACY`, once with the
pre-extension binary (baseline) and once with the extended binary, and
compares flow count, bytes, completion times, max queue, PFC pause/resume
counts, ECN-marked and CNP dequeues, trace drops and MMU drop lines.

The simulator is deterministic, so the default tolerance is 0. Set
`TOLERANCE=0.001` if an incidental logging change reorders same-timestamp
events; any difference in counts still fails.

## Record the baseline (pre-change binary)

Either keep a copy of the old executable and pass it in:

```bash
BINARY=/path/to/pre-change/AstraSimNetwork bash scripts/run.sh --record-baseline
```

or check the submodules out at the audited commits, rebuild, record, then
return to the extended commits and rebuild:

```bash
cd astra-sim && git checkout c1cb32a28ec8cac0228282e8158cab3111f8380e
cd extern/network_backend/ns-3 && git checkout f764bed27630ee45368c03e22e4f952b69fab30a
# rebuild AstraSimNetwork, then from cca-ccl-study:
bash experiments/backend-validation/legacy-regression/scripts/run.sh --record-baseline
# restore the extended commits, rebuild, then:
bash experiments/backend-validation/legacy-regression/scripts/run.sh --compare
```

`baseline/<run>/metrics.json` and `baseline/provenance.txt` (binary path,
commits, dirty flags) are meant to be committed. `results/current/` is not.

## What must hold

Every exact-key metric identical; completion times and max queue within the
tolerance. If anything differs materially, stop: the extension changed legacy
behaviour and E4–E6 must not switch to PERSISTENT until the cause is found.

Note on E0 HPCC: the baseline is whatever the pre-change binary produces with
the current `hpcc.txt` (`HAS_WIN 1`). The 899 ms figure in the notes came from
the earlier `HAS_WIN 0` config and is not the reference here.
