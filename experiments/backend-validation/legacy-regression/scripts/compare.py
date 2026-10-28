#!/usr/bin/env python3
"""Config rewriting, per-run metrics and baseline comparison for the legacy regression.

  compare.py --write-config <src> <dst> <run_dir>   rewrite only output paths (+ LEGACY key)
  compare.py --summarize <run_dir>                   write <run_dir>/metrics.json
  compare.py --compare <baseline_dir> <current_dir> <tolerance>
"""

import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, str(Path(ROOT).parents[1] / "common"))
import netconfig  # noqa: E402

TRACE_RECORD_BYTES = 56
EVENT_DEQUEUE = 2
EVENT_DROP = 3
L3_UDP = 0x11
L3_ACK = 0xFC
L3_NACK = 0xFD
ECN_CE = 0x03
CNP_FLAG = 1

EXACT_KEYS = ("flows", "pfc_pause_events", "pfc_resume_events", "ecn_marked_dequeues", "cnp_dequeues",
              "trace_drops", "mmu_drop_lines", "total_bytes")
TOLERANCE_KEYS = ("last_completion_ns", "max_fct_ns", "min_fct_ns", "sum_fct_ns", "max_queue_bytes")


def write_config(src, dst, run_dir):
    rel = run_dir
    overrides = {
        "TRACE_OUTPUT_FILE": f"{rel}/mix.tr",
        "FCT_OUTPUT_FILE": f"{rel}/fct.txt",
        "PFC_OUTPUT_FILE": f"{rel}/pfc.txt",
        "QLEN_MON_FILE": f"{rel}/qlen.txt",
        # explicit; a pre-change binary ignores unknown keys
        "TRANSPORT_CONNECTION_MODE": "LEGACY",
        "COUNTERS_OUTPUT_FILE": f"{rel}/counters.txt",
    }
    netconfig.rewrite(src, dst, overrides)


def parse_fct(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path):
        parts = line.split()
        if len(parts) >= 8:
            rows.append((int(parts[4]), int(parts[5]), int(parts[6])))
    return rows


def parse_qlen(path):
    peak = 0
    if not os.path.exists(path):
        return peak
    for line in open(path):
        tokens = line.split()
        if len(tokens) < 3 or tokens[0] != "time":
            continue
        index = 3
        while index + 2 < len(tokens) and tokens[index] == "j":
            peak = max(peak, int(tokens[index + 2]))
            index += 3
    return peak


def parse_pfc(path):
    pauses = resumes = 0
    if os.path.exists(path):
        for line in open(path):
            parts = line.split()
            if len(parts) == 5:
                if parts[4] == "1":
                    pauses += 1
                elif parts[4] == "0":
                    resumes += 1
    return pauses, resumes


def parse_trace(path):
    import struct
    stats = {"ecn": 0, "cnp": 0, "drops": 0}
    if not os.path.exists(path) or os.path.getsize(path) < 8:
        return stats
    blob = open(path, "rb").read()
    (nports,) = struct.unpack_from("<I", blob, 0)
    offset = 4 + nports * 11 + 4
    while offset + TRACE_RECORD_BYTES <= len(blob):
        node_type = blob[offset + 29]
        event = blob[offset + 27]
        l3 = blob[offset + 26]
        ecn = blob[offset + 28]
        flags = struct.unpack_from("<H", blob, offset + 36)[0]
        if event == EVENT_DROP:
            stats["drops"] += 1
        if node_type == 1 and event == EVENT_DEQUEUE:
            if l3 == L3_UDP and ecn == ECN_CE:
                stats["ecn"] += 1
            if l3 in (L3_ACK, L3_NACK) and flags & CNP_FLAG:
                stats["cnp"] += 1
        offset += TRACE_RECORD_BYTES
    return stats


def summarize(run_dir):
    fct = parse_fct(os.path.join(run_dir, "fct.txt"))
    pauses, resumes = parse_pfc(os.path.join(run_dir, "pfc.txt"))
    trace = parse_trace(os.path.join(run_dir, "mix.tr"))
    stdout = os.path.join(run_dir, "stdout.log")
    mmu_lines = 0
    if os.path.exists(stdout):
        for line in open(stdout, errors="replace"):
            if "Headroom full" in line:
                mmu_lines += 1
    metrics = {
        "flows": len(fct),
        "total_bytes": sum(size for size, _, _ in fct),
        "last_completion_ns": max((start + dur for _, start, dur in fct), default=0),
        "max_fct_ns": max((dur for _, _, dur in fct), default=0),
        "min_fct_ns": min((dur for _, _, dur in fct), default=0),
        "sum_fct_ns": sum(dur for _, _, dur in fct),
        "max_queue_bytes": parse_qlen(os.path.join(run_dir, "qlen.txt")),
        "pfc_pause_events": pauses,
        "pfc_resume_events": resumes,
        "ecn_marked_dequeues": trace["ecn"],
        "cnp_dequeues": trace["cnp"],
        "trace_drops": trace["drops"],
        "mmu_drop_lines": mmu_lines,
    }
    with open(os.path.join(run_dir, "metrics.json"), "w") as handle:
        json.dump(metrics, handle, indent=2)
        handle.write("\n")
    print(f"{os.path.basename(run_dir)}: " + " ".join(f"{k}={v}" for k, v in metrics.items()))
    return metrics


def compare(baseline_dir, current_dir, tolerance):
    labels = sorted(d for d in os.listdir(current_dir) if os.path.isdir(os.path.join(current_dir, d)))
    failures = []
    print(f"\n{'run':<14}{'metric':<22}{'baseline':>16}{'current':>16}  status")
    for label in labels:
        base_path = os.path.join(baseline_dir, label, "metrics.json")
        cur_path = os.path.join(current_dir, label, "metrics.json")
        if not os.path.exists(base_path):
            failures.append(f"{label}: no baseline (run --record-baseline with the pre-change binary)")
            print(f"{label:<14}{'(no baseline)':<22}")
            continue
        base = json.load(open(base_path))
        cur = json.load(open(cur_path))
        for key in EXACT_KEYS + TOLERANCE_KEYS:
            b = base.get(key)
            c = cur.get(key)
            if key in EXACT_KEYS:
                same = b == c
            else:
                if b in (None, 0):
                    same = b == c
                else:
                    same = abs(c - b) <= tolerance * abs(b)
            status = "ok" if same else "DIFF"
            if not same:
                failures.append(f"{label}: {key} baseline={b} current={c}")
            print(f"{label:<14}{key:<22}{str(b):>16}{str(c):>16}  {status}")
    print()
    marker = os.path.join(ROOT, "results", "PASSED")
    if failures:
        if os.path.exists(marker):
            os.remove(marker)
        print("LEGACY REGRESSION FAILED. Do not proceed to persistent-mode experiments until this is explained:")
        for item in failures:
            print(f"  - {item}")
        return 1
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w") as handle:
        handle.write(f"legacy regression passed (tolerance {tolerance})\n")
    print(f"legacy regression passed (tolerance {tolerance}) for {', '.join(labels)}")
    return 0


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--write-config":
        write_config(sys.argv[2], sys.argv[3], sys.argv[4])
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "--summarize":
        summarize(sys.argv[2])
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "--compare":
        return compare(sys.argv[2], sys.argv[3], float(sys.argv[4]) if len(sys.argv) > 4 else 0.0)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
