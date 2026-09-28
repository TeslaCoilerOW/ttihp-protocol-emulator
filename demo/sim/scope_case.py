#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Analysis of one self-measured simulation case (run_scope_case.sh).

usage: scope_case.py CASE_DIR --expect same|different

1. scope_vs_vcd.py: every capture's edges equal the simulation VCD's.
2. pe_capture.py: the captures (as pe_scope VCDs, cycles from the FPGA's own
   counter) against the static prediction of the timing probe, then
   ``compare predicted CASE --expect same,EXPECT``.
3. Load evidence from the capture unit's activity counters.
Writes CASE_DIR/scope-case.json; exit status 0 when 1 and 2 pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
sys.path.insert(0, str(DEMO))
sys.path.insert(0, str(HERE))

import pc_demo  # noqa: E402
import pe_capture  # noqa: E402
import scope_vs_vcd  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case")
    ap.add_argument("--expect", choices=("same", "different"), required=True)
    a = ap.parse_args(argv)
    case = Path(a.case)
    meta = json.loads((case / "case.json").read_text())
    tag = meta["tag"]
    sc = pc_demo.load_scope_module()
    paths = sorted(case.glob("scope-[0-9]*.json"), key=lambda p: int(p.stem.split("-")[1]))
    caps = [sc.Capture.load(p) for p in paths]
    out = {"tag": tag, "mode": meta["mode"], "core": meta["core"], "seed": meta["seed"], "expect": a.expect,
           "captures": [c.summary() for c in caps]}
    # 1. exact comparison with the simulation VCD
    rc = scope_vs_vcd.main([str(case / "sim.vcd.gz"), *map(str, paths), "--json", str(case / "scope-vs-vcd.json")])
    exact = json.loads((case / "scope-vs-vcd.json").read_text())
    out["exact"] = {"pass": rc == 0, "clock_edges": exact["clock_edges"],
                    "edges_compared": sum(v["edges"] for r in exact["captures"] for v in r.get("channels", {}).values()),
                    "origin_ok": all(r.get("origin_ok", True) for r in exact["captures"])}
    # 2. frame analysis against the prediction
    pred = case / "predicted.json"
    pe_capture.main(["predict", "--image", str(DEMO / "firmware" / "timing-probe.image.json"),
                     "--channels", "probe6=6,probe7=7", "--json", str(pred)])
    vcds = [str(p.with_suffix(".vcd")) for p, c in zip(paths, caps) if c.records]
    verdict, rep = "different", {}
    if vcds:
        pe_capture.main(["analyze", *vcds, "--channels", pc_demo.PROBE_CHANNELS, "--reference", str(pred),
                         "--label", tag, "--json", str(case / "scope-analysis.json"), "--quiet"])
        rep = json.loads((case / "scope-analysis.json").read_text())
        pe_capture.main(["compare", str(pred), str(case / "scope-analysis.json"), "--labels", f"predicted,{tag}",
                         "--expect", f"same,{a.expect}", "--text", str(case / "scope-compare.txt"),
                         "--json", str(case / "scope-compare.json")])
        verdict = json.loads((case / "scope-compare.json").read_text())["verdicts"][1]
    fr = rep.get("frames") or {}
    out.update({"verdict": verdict, "frames_complete": fr.get("frames_complete", 0),
                "frames_identical": fr.get("frames_identical", 0),
                "jitter_pp_cycles": (rep.get("jitter") or {}).get("max_peak_to_peak_cycles"),
                "load_evidence": pc_demo.load_evidence(caps)})
    result = case / "result.json"             # run_scope_case.sh: DEMO_TAG=result
    if result.exists():
        r = json.loads(result.read_text())
        out.update({"probe_running": r.get("probe_running"), "probe_fault": (r.get("probe_after") or {}).get("fault"),
                    "host_operations": r.get("host_operations", 0), "burst_operations": r.get("burst_operations", 0)})
    out["pass"] = out["exact"]["pass"] and verdict == a.expect
    (case / "scope-case.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"{tag}: exact {'PASS' if out['exact']['pass'] else 'FAIL'} ({out['exact']['edges_compared']} edges), "
          f"verdict {verdict} (expected {a.expect}), frames {out['frames_identical']}/{out['frames_complete']}, "
          f"jitter {out['jitter_pp_cycles']}: {'PASS' if out['pass'] else 'FAIL'}")
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
