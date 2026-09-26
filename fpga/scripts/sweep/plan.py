#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Append lines to a sweep task table (run_task.sh, worker.sbatch).

    plan.py TASKS_TSV --build DIR --tag NAME --seeds 1-400 [--placer heap]
            [--opt "TIMING_WEIGHT=160 NEXTPNR_PLACER_BETA=0.5"]

One line per seed: run_id (TAG_s<seed>), the SYNTH_ONLY=1 build directory,
PLACER:SEED and the place-and-route options (build.sh environment names,
space-separated, no spaces inside a value). Existing run ids are kept, so
re-running plan.py does not duplicate work.
"""
import argparse
from pathlib import Path


def seeds(spec):
    out = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


ap = argparse.ArgumentParser()
ap.add_argument("tasks", type=Path)
ap.add_argument("--build", required=True, type=Path)
ap.add_argument("--tag", required=True)
ap.add_argument("--seeds", required=True)
ap.add_argument("--placer", default="heap")
ap.add_argument("--opt", default="")
a = ap.parse_args()
build = a.build.resolve()
if not (build / "synth.json").exists():
    ap.error(f"{build} has no synth.json (run build.sh with SYNTH_ONLY=1)")
have = set()
if a.tasks.exists():
    have = {l.split("\t")[0] for l in a.tasks.read_text().splitlines() if l.strip()}
opts = " ".join(a.opt.split()) or "-"
new = [f"{a.tag}_s{s}\t{build}\t{a.placer}:{s}\t{opts}" for s in seeds(a.seeds) if f"{a.tag}_s{s}" not in have]
with a.tasks.open("a") as f:
    f.writelines(l + "\n" for l in new)
print(f"{len(new)} lines added to {a.tasks}")
