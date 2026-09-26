#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Collect the simulation-validation runs (demo/sim/submit.sh) into verdicts.

    python3 demo/sim/collect.py DEMO_WORK [--publish demo/results] [--jobs DEMO_WORK/jobs.tsv]

For each testbench (pins: host-clocked, cycles from the clock channel;
bridge: free-running 12 MHz, cycles recovered from a 120 MHz capture) it
compares every run's probe timing with the frame pattern that
tools/timing/pe_timing.py predicts from the probe image, both on the exact
simulation VCD and on the emulated analyser capture, and checks each verdict
against the expectation:

  base core, idle or loaded           same as the prediction
  mutant core, idle                   same (a mutant is invisible without load),
                                      except host-fetch (see expectation())
  mutant core, loaded                 different

It writes DEMO_WORK/results/{summary.json,summary.md,compare-*.txt,*.png}.
--publish copies the summary, comparison texts and figures (file names only,
no local paths) into the given directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pe_capture  # noqa: E402


def expectation(core: str, mode: str) -> str:
    """base: same. Mutants: different when loaded; same when idle, except
    host-fetch: its memory-enable gate also blocks the host's program writes
    to engine 3, so the probe's image is never loaded in either mode (the
    formal harness assumes the image instead of loading it)."""
    if core == "base":
        return "same"
    if core == "host-fetch":
        return "different"
    return "same" if mode == "idle" else "different"


def load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def run_compare(analyses, labels, expect, reference, out_text, plot=None, title=None):
    args = argparse.Namespace(analyses=[str(a) for a in analyses], labels=",".join(labels),
                              expect=",".join(expect), reference=str(reference), plot=plot, title=title,
                              text=str(out_text), json=None, tv_limit=0.05)
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        status = pe_capture.compare(args)
    return status == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work")
    ap.add_argument("--publish")
    ap.add_argument("--jobs", help="jobs.tsv from submit.sh (job ids go into the summary)")
    a = ap.parse_args()
    work = Path(a.work)
    runs = work / "runs"
    res = work / "results"
    res.mkdir(parents=True, exist_ok=True)
    jobs = {}
    jobs_file = Path(a.jobs) if a.jobs else work / "jobs.tsv"
    if jobs_file.exists():
        for line in jobs_file.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                jobs[parts[1].split("-demo-harness-")[-1].split("pe-demo-")[-1]] = parts[0]
    cases = []
    for d in sorted(p for p in runs.iterdir() if p.is_dir()):
        case = load(d / "case.json")
        if case is None:
            continue
        case["job"] = jobs.get(d.name)
        case["dir"] = d
        cases.append(case)
    reference = next((c["dir"] / "predicted.json" for c in cases if (c["dir"] / "predicted.json").exists()), None)
    summary = {"reference": None, "cases": [], "comparisons": [], "other_tests": []}
    if reference:
        shutil.copy(reference, res / "predicted.json")
        ref = load(reference)
        summary["reference"] = {"source": ref["pattern"]["source"], "period_cycles": ref["pattern"]["period_cycles"],
                                "events_per_frame": ref["pattern"]["events_per_frame"]}
    all_ok = True
    for case in cases:
        d = case["dir"]
        row = {k: case[k] for k in ("tag", "tb", "mode", "core", "seed", "cycles", "test", "make_status",
                                    "wall_seconds", "tests", "failures", "job")}
        result = load(d / "result.json") or {}
        if case["test"] == "test_isolation":
            row["lockstep"] = result.get("lockstep")
            row["host_operations"] = result.get("host_operations", 0)
            row["probe_running_at_end"] = result.get("probe_running")
            row["probe_fault_at_end"] = (result.get("probe_after") or {}).get("fault")
            row["expected"] = expectation(case["core"], case["mode"])
            for view in ("exact", "la"):
                rep = load(d / f"{view}.json")
                if rep is None or reference is None:
                    row[view] = None
                    all_ok = False
                    continue
                v, reasons = pe_capture.verdict(rep, [tuple(p) for p in ref["pattern"]["events"]],
                                                ref["pattern"]["period_cycles"])
                fr = rep.get("frames") or {}
                ctx = rep.get("context", {})
                uart = (ctx.get("uart") or {}).get("uart") or {}
                row[view] = {
                    "verdict": v, "reasons": reasons, "events": rep["events"],
                    "frames": fr.get("frames_complete"), "identical": fr.get("frames_identical"),
                    "frame_lengths": fr.get("frame_lengths"),
                    "jitter_pp_cycles": (rep.get("jitter") or {}).get("max_peak_to_peak_cycles"),
                    "departure_frame": (fr.get("grid_departure") or {}).get("frame"),
                    "conversion": [{k: c.get(k) for k in ("mode", "estimated_ratio", "relative_offset_ppm",
                                                          "max_arc_samples", "consistent", "clock_edges")
                                    if k in c} for c in rep["conversion"]],
                    "uart_bytes": uart.get("bytes"), "uart_framing_errors": uart.get("framing_errors"),
                    "uart_bytes_outside_loop": uart.get("bytes_outside_expected_set"),
                    "host_write_valid_high": (ctx.get("wvalid") or {}).get("high_fraction"),
                    "host_write_valid_edges": (ctx.get("wvalid") or {}).get("edges"),
                    "sck_edges": (ctx.get("sck") or {}).get("edges"),
                }
                row[view]["match"] = v == row["expected"]
                all_ok &= row[view]["match"]
            ok_sim = case["make_status"] == 0 and case["failures"] == 0
            row["simulation_ok"] = ok_sim
            all_ok &= ok_sim
        else:
            row["simulation_ok"] = case["make_status"] == 0 and case["failures"] == 0 and case["tests"] > 0
            row["result"] = {k: v for k, v in result.items() if k in ("spi_words", "i2c_bytes", "uart_tx_bytes",
                                                                   "flash_commands", "readings", "uart_text",
                                                                   "i2c_received", "lockstep")}
            dec = {}
            for name in ("uart-tx", "uart-rx", "spi", "i2c"):
                f = d / f"sigrok-{name}.txt"
                if f.exists():
                    dec[name] = f.read_text().splitlines()[:40]
            if dec:
                row["sigrok_decode"] = dec
            all_ok &= row["simulation_ok"]
            summary["other_tests"].append(row)
            continue
        summary["cases"].append(row)
    # Side-by-side comparisons and figures per testbench and view.
    for tb, title in (("pins", "host-clocked build (Pico host), cycles from the clock channel"),
                      ("bridge", "osc12 build (UART bridge), cycles recovered from a 120 MHz capture")):
        for view in ("exact", "la"):
            sel = [c for c in cases if c["tb"] == tb and c["test"] == "test_isolation" and (c["dir"] / f"{view}.json").exists()]
            if not sel or reference is None:
                continue
            order = sorted(sel, key=lambda c: (c["core"] != "base", c["core"], c["mode"] != "idle", c["seed"]))
            labels = [c["tag"].replace(f"{tb}-", "") for c in order]
            ok = run_compare([c["dir"] / f"{view}.json" for c in order], labels,
                             [expectation(c["core"], c["mode"]) for c in order], reference,
                             res / f"compare-{tb}-{view}.txt")
            summary["comparisons"].append({"tb": tb, "view": view, "runs": labels, "pass": ok,
                                           "text": f"compare-{tb}-{view}.txt"})
            all_ok &= ok
            # Figure: idle base, loaded base (seed 1) and the loaded timer-host mutant.
            pick = [c for c in order if (c["core"], c["mode"], c["seed"]) in
                    (("base", "idle", 1), ("base", "loaded", 1), ("timer-host", "loaded", 1))]
            if view == "la" and len(pick) >= 2:
                plot_labels = ["idle", "loaded", "timer-host mutant loaded"][:len(pick)]
                run_compare([c["dir"] / "la.json" for c in pick], plot_labels,
                            [expectation(c["core"], c["mode"]) for c in pick], reference,
                            res / f"figure-{tb}.txt", plot=str(res / f"isolation-{tb}.png"),
                            title=f"Timing probe (engine 3), simulation: {title}")
    summary["pass"] = all_ok
    for row in summary["cases"] + summary["other_tests"]:
        row.pop("dir", None)
    (res / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    lines = ["| case | job | sim | expected | exact | analyser view | frames identical | host ops | UART bytes |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in summary["cases"]:
        ex, la = r.get("exact") or {}, r.get("la") or {}
        lines.append(f"| {r['tag']} | {r['job']} | {'ok' if r['simulation_ok'] else 'FAIL'} | {r['expected']} | "
                     f"{ex.get('verdict')} | {la.get('verdict')} | {la.get('identical')}/{la.get('frames')} | "
                     f"{r.get('host_operations')} | {la.get('uart_bytes')} |")
    for r in summary["other_tests"]:
        lines.append(f"| {r['tag']} | {r['job']} | {'ok' if r['simulation_ok'] else 'FAIL'} | pass | - | - | - | - | - |")
    lines.append("")
    lines.append("ALL " + ("PASS" if all_ok else "FAIL"))
    (res / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    if a.publish:
        dest = Path(a.publish)
        dest.mkdir(parents=True, exist_ok=True)
        for f in ["summary.json", "summary.md", "predicted.json"] + [p.name for p in res.glob("compare-*.txt")] + \
                 [p.name for p in res.glob("isolation-*.png")]:
            if (res / f).exists():
                shutil.copy(res / f, dest / f)
        print(f"published to {dest}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
