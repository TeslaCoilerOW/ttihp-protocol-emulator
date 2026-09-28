#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Collect the self-measured isolation cases (submit_scope.sh) into a summary.

    python3 demo/sim/collect_scope.py DEMO_WORK [--publish demo/results] [--job ID]

Reads DEMO_WORK/runs/scope-*/scope-case.json (demo/sim/scope_case.py) and
writes DEMO_WORK/results/scope-summary.{json,md}, a side-by-side comparison
of the base idle run, the base loaded run and the loaded timer-host mutant
(compare-scope.txt; isolation-scope.png when matplotlib is available).
--publish copies them into the given directory (file names only, no paths).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pe_capture  # noqa: E402

FIGURE = ("scope-idle-base-1", "scope-loaded-base-1", "scope-loaded-timer-host-1")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work", type=Path)
    ap.add_argument("--publish", type=Path)
    ap.add_argument("--job", help="Slurm job id to record")
    a = ap.parse_args(argv)
    runs = a.work / "runs"
    cases = []
    for p in sorted(runs.glob("scope-*/scope-case.json")):
        c = json.loads(p.read_text())
        meta = json.loads((p.parent / "case.json").read_text())
        c["wall_seconds"] = meta.get("wall_seconds")
        c["sim_status"] = meta.get("make_status")
        cases.append(c)
    if not cases:
        raise SystemExit(f"no scope cases under {runs}")
    ok = all(c["pass"] and c["sim_status"] == 0 for c in cases)
    out = a.work / "results"
    out.mkdir(parents=True, exist_ok=True)
    summary = {"job": a.job, "pass": ok, "cases": cases}
    (out / "scope-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    rows = ["| Case | Expected | Verdict | Captures (records) | Frames identical | Jitter p-p (cycles) "
            "| Scope edges = VCD edges | Host operations (burst) | Load evidence in the windows: uio0 / uio2 / ui4 edges |",
            "|---|---|---|---|---|---|---|---|---|"]
    for c in cases:
        ev = c["load_evidence"]["edges"]
        recs = "+".join(str(x["records"]) for x in c["captures"])
        rows.append(f"| {c['tag']} | {c['expect']} | {c['verdict']} | {len(c['captures'])} ({recs}) | "
                    f"{c['frames_identical']}/{c['frames_complete']} | {c['jitter_pp_cycles'] if c['jitter_pp_cycles'] is not None else '-'} | "
                    f"{'yes' if c['exact']['pass'] else 'NO'} ({c['exact']['edges_compared']} edges) | "
                    f"{c.get('host_operations', 0)} ({c.get('burst_operations', 0)}) | "
                    f"{ev['uio0']} / {ev['uio2']} / {ev['ui4']} |")
    rows.append("")
    rows.append("ALL PASS" if ok else "FAILURES: " + ", ".join(c["tag"] for c in cases if not c["pass"]))
    (out / "scope-summary.md").write_text("\n".join(rows) + "\n")
    print("\n".join(rows))
    # Side-by-side comparison and figure
    have = [t for t in FIGURE if (runs / t / "scope-analysis.json").exists()]
    if len(have) == len(FIGURE):
        pred = runs / FIGURE[0] / "predicted.json"
        args = ["compare", str(pred), *(str(runs / t / "scope-analysis.json") for t in FIGURE),
                "--labels", "predicted,idle,loaded,timer-host loaded", "--expect", "same,same,same,different",
                "--text", str(out / "compare-scope.txt")]
        try:
            import matplotlib  # noqa: F401
            args += ["--plot", str(out / "isolation-scope.png"),
                     "--title", "On-board capture, Cmod A7 osc12 top (simulation)"]
        except ImportError:
            pass
        with contextlib.redirect_stdout(io.StringIO()):
            rc = pe_capture.main(args)
        print(f"compare-scope.txt written (compare exit {rc})")
    if a.publish:
        a.publish.mkdir(parents=True, exist_ok=True)
        for name in ("scope-summary.json", "scope-summary.md", "compare-scope.txt", "isolation-scope.png"):
            if (out / name).exists():
                shutil.copy(out / name, a.publish / name)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
