#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Summarize sweep results: one row per (build, placer, options) group.

    collect.py RUNS_DIR [RUNS_DIR ...] [--target 50] [--json]

For each group: runs, failed runs, fmax min / median / mean / max, the number
of runs at or above the target, and the best run (seed).
"""
import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("runs", nargs="+", type=Path)
ap.add_argument("--target", type=float, default=50.0)
ap.add_argument("--json", action="store_true")
a = ap.parse_args()
groups = defaultdict(list)
for d in a.runs:
    for p in sorted(d.glob("*/result.json")):
        r = json.loads(p.read_text())
        groups[(Path(r["build"]).name, r["placer"], r["options"])].append(r)
rows = []
for (build, placer, opts), rs in sorted(groups.items()):
    ok = sorted((r for r in rs if r.get("fmax") is not None), key=lambda r: r["fmax"])
    row = {"build": build, "placer": placer, "options": opts, "runs": len(rs), "failed": len(rs) - len(ok)}
    if ok:
        f = [r["fmax"] for r in ok]
        row.update(min=f[0], median=round(statistics.median(f), 2), mean=round(statistics.mean(f), 2),
                   max=f[-1], at_target=sum(1 for v in f if v >= a.target),
                   best_seed=ok[-1]["seed"], best_run=ok[-1]["run_id"])
    rows.append(row)
rows.sort(key=lambda r: -r.get("median", 0))
if a.json:
    print(json.dumps(rows, indent=1))
else:
    for r in rows:
        head = f"{r['build']} {r['placer']} {r['options']}"
        if "median" in r:
            print(f"{head:60s} n={r['runs']:4d} failed={r['failed']:3d} min={r['min']:6.2f} "
                  f"median={r['median']:6.2f} max={r['max']:6.2f} >={a.target:g}: {r['at_target']:4d} "
                  f"best seed {r['best_seed']}")
        else:
            print(f"{head:60s} n={r['runs']:4d} failed={r['failed']:3d}")
