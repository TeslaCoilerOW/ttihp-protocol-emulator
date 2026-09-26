#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Sweep the analyser parameters of ``pe_capture.py analyze --fclk``.

Takes one exact simulation VCD with a free-running clock (the bridge
testbench's tb_bridge.clk), counts the true cycle of every probe edge from the
clock, then emulates a logic analyser at each sample rate with random crystal
offsets, sampling phases and edge jitter, recovers the cycles with
pe_capture's tracker and checks every edge-to-edge interval against the truth.

    python3 demo/sim/recovery_sweep.py SIM.vcd --json sweep.json \\
        [--rates 16e6,24e6,30e6,48e6,120e6] [--trials 12] [--fclk 12e6]

A trial passes when the recovered intervals equal the true ones exactly; the
report also records whether the tracker flagged its own result as
inconsistent (arc check) and whether the ratio rule (fs >= 2.2 fclk) would
have refused the capture.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pe_capture as pc  # noqa: E402

CLOCK = "tb_bridge.clk"
PROBES = ("tb_bridge.pads[6]", "tb_bridge.pads[7]")


def intervals(cycles):
    return [b - a for a, b in zip(cycles, cycles[1:])]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vcd")
    ap.add_argument("--rates", default="16e6,21e6,24e6,30e6,48e6,120e6")
    ap.add_argument("--trials", type=int, default=12)
    ap.add_argument("--fclk", type=float, default=12e6)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--json")
    a = ap.parse_args()
    cap = pc.read_capture(a.vcd)
    clk = cap.resolve(CLOCK)
    truth = {}
    for spec in PROBES:
        ticks = [t for t, _ in pc.edges_of(cap.resolve(spec))[0]]
        truth[spec] = intervals(pc.clock_cycles(clk, ticks)[0])
    exact = {s: [t for t, _ in pc.edges_of(cap.resolve(s))[0]] for s in PROBES}
    ts = cap.tick_seconds
    rng = random.Random(a.seed)
    rows = []
    for rate in (float(x) for x in a.rates.split(",")):
        for k in range(a.trials):
            ppm = round(rng.uniform(-150, 150), 1)
            phase = round(rng.random(), 3)
            jitter = rng.choice([0, 200, 500])
            # The analyser model of pe_capture.py resample: a change at time T
            # is seen by the first sample at or after T (+ Gaussian jitter).
            period = 1 / (pc.Fraction(rate) * (1 + pc.Fraction(str(ppm)) / 10**6))
            noise = random.Random(k)
            per = {}
            for s, ticks in exact.items():
                per[s] = [math.ceil((t * ts + pc.Fraction(noise.gauss(0.0, jitter * 1e-12)).limit_denominator(10**15)
                                     - phase * period) / period) for t in ticks]
            ticks = sorted({t for v in per.values() for t in v})
            ratio = rate / a.fclk
            tol = max(0.25, (2e-9 + 8 * jitter * 1e-12) * rate)
            ns, info = pc.recover_cycles(ticks, ratio, tolerance=tol)
            m = dict(zip(ticks, ns))
            ok = all(intervals([m[t] for t in per[s]]) == truth[s] for s in PROBES)
            rows.append({"rate_hz": rate, "ratio": ratio, "ppm": ppm, "phase": phase, "jitter_ps": jitter,
                         "exact": ok, "consistent": info["consistent"],
                         "refused_by_ratio_rule": ratio < 2.2,
                         "max_arc_samples": info["max_arc_samples"],
                         "estimated_offset_ppm": info["relative_offset_ppm"]})
    by_rate = {}
    for r in rows:
        s = by_rate.setdefault(r["rate_hz"], {"ratio": r["ratio"], "trials": 0, "exact": 0,
                                              "flagged_inconsistent": 0, "wrong_but_consistent": 0,
                                              "refused_by_ratio_rule": r["refused_by_ratio_rule"]})
        s["trials"] += 1
        s["exact"] += r["exact"]
        s["flagged_inconsistent"] += not r["consistent"]
        s["wrong_but_consistent"] += (not r["exact"]) and r["consistent"]
    report = {"vcd": Path(a.vcd).name, "fclk_hz": a.fclk, "edges": {s: len(v) + 1 for s, v in truth.items()},
              "by_rate": {f"{k / 1e6:g} MHz": v for k, v in by_rate.items()}, "trials": rows}
    for name, s in report["by_rate"].items():
        print(f"{name:>9}  fs/fclk {s['ratio']:.3f}  exact {s['exact']}/{s['trials']}  "
              f"flagged {s['flagged_inconsistent']}  wrong-but-consistent {s['wrong_but_consistent']}  "
              f"{'refused by the ratio rule' if s['refused_by_ratio_rule'] else ''}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1) + "\n")
    # Pass: above the ratio rule, no wrong result is ever reported as consistent.
    bad = sum(s["wrong_but_consistent"] for s in by_rate.values() if not s["refused_by_ratio_rule"])
    print("SWEEP", "PASS" if bad == 0 else "FAIL")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
