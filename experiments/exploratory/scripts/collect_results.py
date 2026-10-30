#!/usr/bin/env python3
"""Copy each matrix CSV into exploratory/results/eN and write PNG plots there.

Raw traces stay in each experiment's results/raw/. This directory is only
summary.csv plus PNGs.
"""

import csv
import importlib.util
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "results")

EXPERIMENTS = (
    ("e3", "e3-ccl-cca-oversubscription"),
    ("e4", "e4-ccl-granularity-cca"),
    ("e5", "e5-wan-transport-ranking"),
    ("e6", "e6-temporal-asymmetry-feedback"),
)


def load_analyze(exp_dir):
    path = os.path.join(ROOT, exp_dir, "scripts", "analyze.py")
    spec = importlib.util.spec_from_file_location(f"analyze_{exp_dir}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _number(value):
    if value == "":
        return value
    try:
        if any(token in value for token in ".eE"):
            return float(value)
        return int(value)
    except ValueError:
        return value


def load_csv(path):
    with open(path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key, value in list(row.items()):
            row[key] = _number(value)
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    for short, exp_dir in EXPERIMENTS:
        dest = os.path.join(OUT, short)
        os.makedirs(dest, exist_ok=True)
        src_csv = os.path.join(ROOT, exp_dir, "results", "summary.csv")
        dest_csv = os.path.join(dest, "summary.csv")
        if os.path.exists(src_csv):
            shutil.copy2(src_csv, dest_csv)
            print(f"{short}: copied {src_csv}")
        elif os.path.exists(dest_csv):
            print(f"{short}: using existing {dest_csv}")
        else:
            print(f"{short}: no summary.csv yet (skip plots)")
            continue
        rows = load_csv(dest_csv)
        if not rows:
            print(f"{short}: empty csv")
            continue
        analyze = load_analyze(exp_dir)
        analyze.plot(rows, dest)
        pngs = sorted(name for name in os.listdir(dest) if name.endswith(".png"))
        print(f"{short}: {len(rows)} rows, pngs {pngs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
