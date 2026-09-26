#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Summarize one openXC7 build directory (fpga/scripts/build.sh) as JSON.

Reads nextpnr's --report JSON (utilisation per BEL type, fmax per clock),
the nextpnr log (utilisation table, "Max frequency" lines, warnings) and the
Yosys `stat` output. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from pathlib import Path

# Datasheet capacities (DS180 Artix-7 / DS180 Spartan-7 family tables). The
# openXC7 chipdb for xc7a35t is the xc7a50t die (the same silicon), so
# nextpnr's "available" counts are the 50T's; percentages below use the
# part's datasheet capacity instead.
DEVICES = {
    "xc7a35tcpg236-1": {"luts": 20800, "ffs": 41600, "ramb36": 50, "dsp": 90, "user_io": 106},
    "xc7s50csga324-1": {"luts": 32600, "ffs": 65200, "ramb36": 75, "dsp": 120, "user_io": 210},
}


def yosys_cells(stat: Path) -> dict[str, int]:
    cells: dict[str, int] = {}
    if not stat.exists():
        return cells
    for line in stat.read_text().splitlines():
        m = re.match(r"^\s+(\d+)\s+([A-Z][A-Z0-9_]+)\s*$", line)
        if m:
            cells[m.group(2)] = cells.get(m.group(2), 0) + int(m.group(1))
    return cells


def nextpnr_log(log: Path) -> dict[str, object]:
    out: dict[str, object] = {"fmax_lines": [], "utilisation_lines": [], "warnings": 0, "errors": 0}
    if not log.exists():
        return out
    in_util = False
    for line in log.read_text(errors="replace").splitlines():
        if "Max frequency for clock" in line:
            out["fmax_lines"].append(line.replace("Info: ", "").strip())
        if line.startswith("Warning:"):
            out["warnings"] += 1
        if line.startswith("ERROR:"):
            out["errors"] += 1
        if "Device utilisation" in line:
            in_util = True
            out["utilisation_lines"] = []
            continue
        if in_util:
            m = re.match(r"^Info:\s+(\S+):\s+(\d+)/\s*(\d+)\s+(\d+)%", line)
            if m:
                out["utilisation_lines"].append(
                    {"bel": m.group(1), "used": int(m.group(2)), "available": int(m.group(3)),
                     "percent": int(m.group(4))})
            elif out["utilisation_lines"]:
                in_util = False
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--clock", required=True)
    ap.add_argument("--part", required=True)
    ap.add_argument("--best", required=True, help="PLACER:SEED of the run used for the bitstream")
    ap.add_argument("--times", type=int, nargs=3, metavar=("SYNTH", "PNR", "BIT"))
    ap.add_argument("dir", type=Path)
    a = ap.parse_args()
    d = a.dir
    name = next((p.stem for p in d.glob("pe_*.bit")), f"pe_{a.board}_{a.clock}")
    runs = []
    results = d / "pnr" / "results.txt"
    if results.exists():
        for line in results.read_text().split("\n"):
            if line.strip():
                run, value = line.split()
                runs.append({"run": run, "fmax_mhz": None if value == "FAILED" else float(value)})
    ok = sorted(r["fmax_mhz"] for r in runs if r["fmax_mhz"] is not None)
    target = {"osc12": 12.0, "pll40": 40.0}.get(a.clock, 50.0)
    run_stats = {"runs": len(runs), "failed": len(runs) - len(ok)}
    if ok:
        run_stats.update(min_mhz=ok[0], median_mhz=round(statistics.median(ok), 2), max_mhz=ok[-1],
                         meeting_target=sum(1 for f in ok if f >= target))
    recipe = json.loads((d / "recipe.json").read_text()) if (d / "recipe.json").exists() else {}
    report = json.loads((d / "report.json").read_text()) if (d / "report.json").exists() else {}
    util = report.get("utilization", {})
    fmax = report.get("fmax", {})
    bit = d / f"{name}.bit"
    log = nextpnr_log(d / "logs" / "nextpnr.log")
    cells = yosys_cells(d / "synth_stat.txt")
    lut = sum(v for k, v in cells.items() if re.fullmatch(r"LUT[1-6]", k))
    lutram = sum(v for k, v in cells.items() if k.startswith("RAM") and not k.startswith("RAMB"))
    dev = DEVICES.get(a.part, {})
    used = {k: v.get("used", 0) for k, v in util.items()}

    def pct(n: int, cap: int | None) -> float | None:
        return round(100.0 * n / cap, 1) if cap else None

    summary = {
        "schema": "protocol-emulator.fpga-build.v1",
        "board": a.board,
        "clock": a.clock,
        "part": a.part,
        "best_run": a.best,
        "runs": runs,
        "run_stats": run_stats,
        "recipe": recipe,
        "target_mhz": target,
        "utilisation_vs_part": {
            "lut_cells": used.get("SLICE_LUTX", 0),
            "lut_cells_pct": pct(used.get("SLICE_LUTX", 0), dev.get("luts")),
            "ffs": used.get("SLICE_FFX", 0),
            "ffs_pct": pct(used.get("SLICE_FFX", 0), dev.get("ffs")),
            "carry4": used.get("CARRY4", 0),
            "ramb18": used.get("RAMB18E1", 0),
            "ramb36": used.get("RAMB36E1", 0),
            "dsp": used.get("DSP48E1", 0),
            "io_pads": used.get("PAD", 0),
            "mmcm": used.get("MMCME2_ADV_MMCME2_ADV", 0),
            "bufg": used.get("BUFGCTRL", 0),
        },
        "fmax": fmax,
        "fmax_log": log["fmax_lines"],
        "nextpnr_utilisation": util,
        "nextpnr_utilisation_log": log["utilisation_lines"],
        "nextpnr_warnings": log["warnings"],
        "yosys_cells": cells,
        "yosys_lut_cells": lut,
        "yosys_lutram_cells": lutram,
        "device": dev,
        "bitstream": {"file": bit.name, "bytes": bit.stat().st_size,
                      "sha256": hashlib.sha256(bit.read_bytes()).hexdigest()} if bit.exists() else None,
        "seconds": dict(zip(("synth", "pnr", "bitgen"), a.times)) if a.times else None,
        "readback": json.loads((d / "readback.json").read_text()) if (d / "readback.json").exists() else None,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
