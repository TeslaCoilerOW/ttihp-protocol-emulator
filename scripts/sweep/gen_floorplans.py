#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write the sweep floorplans (floorplans/*.json) from their derivations.

Every macro x is on the PDN stripe lattice x = 11.04 + 67.44 k (docs/hardening.md
section 3, src/sram_pdn_cfg.tcl), so the unchanged FP_PDN_V* keys power every
floorplan. Orientation is N or FS only: src/sram_pdn_cfg.tcl rejects anything
else, because a rotation turns the Metal4 power columns horizontal (no vertical
Metal4 stripe can then connect without shorting VDD to VSS) and a Y-mirror (FN/S)
moves the column lattice off the stripes.

"_trk" floorplans also put every macro signal pin on a Metal2 routing track.
Run2 (job 23720702) reported DRT-0418 "no pins on routing grid" for 15 pins on
each of the four FS macros and none on the N macros. The pins are 0.26 um Metal2
squares on the LEF bottom edge; tracks (PDK tracks.info, DEF TRACKS) are Metal2
x = 0.48 n, Metal2 y = 0.48 n and Metal3 y = 0.42 n. With the FS row at y 3.78
the pins span y 67.88..68.14, which holds no Metal2 y track (67.68 / 68.16), and
exactly the 15 pins per macro whose x span holds no Metal2 x track are flagged.
The N row at 638.82 has the y track 638.88 inside every pin. So pins are flagged
when neither their x nor their y span holds a Metal2 track, which reproduces all
60 warnings. At FS y 3.87 the pins span 67.97..68.23 and hold both 68.16
(Metal2) and 68.04 (Metal3), 0.07 um from the pin edges.
macros/check_macro_floorplan.py prints this prediction for every floorplan.

Usage: python3 scripts/sweep/gen_floorplans.py [--out floorplans] [--check]
"""

import argparse
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

MACRO = "RM_IHPSG13_1P_64x16_c2"
W, H = 236.80, 64.36
PIN_H = 0.26
ROW = 3.78
Y_FS_ROW0 = 3.78      # base: FS row on core row 0 (pins off the Metal2 y grid)
Y_FS_TRK = 3.87       # FS row with the pins on Metal2 and Metal3 y tracks
Y_N_TOP = 638.82      # row 169; pin y 638.82..639.08 holds the Metal2 track 638.88


def kx(k):
    return round(11.04 + 67.44 * k, 2)


def _margin(lo, hi, pitch):
    best = -1.0
    n = math.ceil(lo / pitch - 1e-9)
    while n * pitch <= hi + 1e-9:
        best = max(best, min(n * pitch - lo, hi - n * pitch))
        n += 1
    return best


def fs_y_on_track(target, min_margin=0.06):
    """Smallest y >= target (0.005 grid) whose FS pin band holds Metal2 and Metal3 y tracks."""
    y = math.ceil(target / 0.005 - 1e-9) * 0.005
    for _ in range(2000):
        lo, hi = y + H - PIN_H, y + H
        if _margin(lo, hi, 0.48) >= min_margin and _margin(lo, hi, 0.42) >= min_margin:
            return round(y, 3)
        y += 0.005
    raise ValueError("no on-track FS y near %s" % target)


def n_y_on_track(target):
    """Smallest row boundary y = 3.78 m >= target with m = 2 (mod 8): pin band holds Metal2 track, 0.12 um margin."""
    m = int(math.ceil(target / ROW - 1e-9))
    while m % 8 != 2:
        m += 1
    return round(ROW * m, 2)


def inst(name, k, y, orient):
    return name, {"location": [kx(k), round(y, 3)], "orientation": orient}


def two_rows(bottom_k, top_k, fs_y, top_y=Y_N_TOP):
    names_b = ["e0_lo", "e0_hi", "e1_lo", "e1_hi"]
    names_t = ["e2_lo", "e2_hi", "e3_lo", "e3_hi"]
    out = []
    for n, k in zip(names_b, bottom_k):
        out.append(inst("core.instruction_sram_" + n, k, fs_y, "FS"))
    for n, k in zip(names_t, top_k):
        out.append(inst("core.instruction_sram_" + n, k, top_y, "N"))
    return out


def floorplans():
    fps = []

    # ---------------------------------------------------------------- 8x4
    fps.append(dict(
        id="fp8_base", tiles="8x4",
        description="The placement in src/config.json at 73536f0 (docs/hardening.md section 4): bottom row FS "
                    "on core row 0, top row N clear of the I/O pins, pairs 4 stripe pitches apart (33 um gaps).",
        derivation={"bottom_k": [2, 6, 14, 18], "top_k": [6, 10, 16, 20], "fs_y": Y_FS_ROW0, "n_y": Y_N_TOP},
        instances=two_rows([2, 6, 14, 18], [6, 10, 16, 20], Y_FS_ROW0)))
    fps.append(dict(
        id="fp8_base_trk", tiles="8x4",
        description="fp8_base with the FS row raised 0.09 um to y 3.87 so every FS signal pin holds a Metal2 "
                    "(68.16) and a Metal3 (68.04) y track: removes the 60 DRT-0418 off-grid pins of run2.",
        derivation={"bottom_k": [2, 6, 14, 18], "top_k": [6, 10, 16, 20], "fs_y": Y_FS_TRK, "n_y": Y_N_TOP},
        instances=two_rows([2, 6, 14, 18], [6, 10, 16, 20], Y_FS_TRK)))
    fps.append(dict(
        id="fp8_wide", tiles="8x4",
        description="docs/hardening.md section 8 troubleshooting row: macros 5 stripe pitches apart (100 um "
                    "gaps). Top k 6/11/16/21, bottom k 2/7/13/18; odd k is off the 0.48 site grid.",
        derivation={"bottom_k": [2, 7, 13, 18], "top_k": [6, 11, 16, 21], "fs_y": Y_FS_ROW0, "n_y": Y_N_TOP},
        instances=two_rows([2, 7, 13, 18], [6, 11, 16, 21], Y_FS_ROW0)))
    fps.append(dict(
        id="fp8_wide_trk", tiles="8x4",
        description="fp8_wide with the FS row at the on-track y 3.87 (see fp8_base_trk). Best-guess alternative: "
                    "wider pin-edge channels plus on-grid pin access.",
        derivation={"bottom_k": [2, 7, 13, 18], "top_k": [6, 11, 16, 21], "fs_y": Y_FS_TRK, "n_y": Y_N_TOP},
        instances=two_rows([2, 7, 13, 18], [6, 11, 16, 21], Y_FS_TRK)))
    fps.append(dict(
        id="fp8_spread_trk", tiles="8x4",
        description="Maximum spread along both edges: bottom k 0/7/14/21 (235 um gaps), top k 3/9/15/21 "
                    "(168 um gaps, first macro just clear of the I/O pins); FS row on-track at y 3.87. "
                    "Spreads the 344 macro pins over the full width instead of four 500 um pair sites.",
        derivation={"bottom_k": [0, 7, 14, 21], "top_k": [3, 9, 15, 21], "fs_y": Y_FS_TRK, "n_y": Y_N_TOP},
        instances=two_rows([0, 7, 14, 21], [3, 9, 15, 21], Y_FS_TRK)))

    # ---------------------------------------------------------------- 6x4
    # core x 2.88..1286.40: k <= 15. The top row must start at k >= 3 (I/O pins
    # x 29.76..191.04 plus a 10 um halo), so four N macros fit only at k 3/7/11/15.
    fps.append(dict(
        id="fp6_tworow", tiles="6x4",
        description="The 6x4 insurance placement of docs/hardening.md section 4: bottom FS k 0/4/8/12 on row 0, "
                    "top N k 3/7/11/15 (odd k, off the site grid).",
        derivation={"bottom_k": [0, 4, 8, 12], "top_k": [3, 7, 11, 15], "fs_y": Y_FS_ROW0, "n_y": Y_N_TOP},
        instances=two_rows([0, 4, 8, 12], [3, 7, 11, 15], Y_FS_ROW0)))
    fps.append(dict(
        id="fp6_tworow_trk", tiles="6x4",
        description="fp6_tworow with the FS row at the on-track y 3.87.",
        derivation={"bottom_k": [0, 4, 8, 12], "top_k": [3, 7, 11, 15], "fs_y": Y_FS_TRK, "n_y": Y_N_TOP},
        instances=two_rows([0, 4, 8, 12], [3, 7, 11, 15], Y_FS_TRK)))
    fps.append(dict(
        id="fp6_spread_trk", tiles="6x4",
        description="Bottom FS row spread to k 0/5/10/15 (100 um gaps, same right edge as the top row); top N "
                    "k 3/7/11/15 (the only 4-macro top row); FS on-track at y 3.87.",
        derivation={"bottom_k": [0, 5, 10, 15], "top_k": [3, 7, 11, 15], "fs_y": Y_FS_TRK, "n_y": Y_N_TOP},
        instances=two_rows([0, 5, 10, 15], [3, 7, 11, 15], Y_FS_TRK)))

    # compact block: two facing rows at the bottom edge, ~90 um channel between them
    yb = n_y_on_track(155.0)
    blk = []
    for n, k in zip(["e0_lo", "e0_hi", "e1_lo", "e1_hi"], [2, 6, 10, 14]):
        blk.append(inst("core.instruction_sram_" + n, k, Y_FS_TRK, "FS"))
    for n, k in zip(["e2_lo", "e2_hi", "e3_lo", "e3_hi"], [2, 6, 10, 14]):
        blk.append(inst("core.instruction_sram_" + n, k, yb, "N"))
    fps.append(dict(
        id="fp6_block_trk", tiles="6x4",
        description="Compact 2x4 block on the bottom edge: row A FS at y 3.87 (pins up), row B N at y %.2f "
                    "(pins down) facing it across a %.1f um channel that holds logic; both rows k 2/6/10/14. "
                    "Leaves the top %.0f um of the core macro-free, including the I/O corner."
                    % (yb, yb - (Y_FS_TRK + H), 706.86 - (yb + H)),
        derivation={"row_a": {"k": [2, 6, 10, 14], "y": Y_FS_TRK, "orient": "FS"},
                    "row_b": {"k": [2, 6, 10, 14], "y": yb, "orient": "N", "rule": "y = 3.78 m, m = 2 mod 8"}},
        instances=blk))

    # columnar: two stacks of four FS macros at the left/right core edges, pins up into ~100 um bays
    ys = []
    y = Y_FS_TRK
    for _ in range(4):
        y = fs_y_on_track(y)
        ys.append(y)
        y = y + H + 100.0
    col = []
    for n, yy in zip(["e0_lo", "e0_hi", "e2_lo", "e2_hi"], ys):
        col.append(inst("core.instruction_sram_" + n, 0, yy, "FS"))
    for n, yy in zip(["e1_lo", "e1_hi", "e3_lo", "e3_hi"], ys):
        col.append(inst("core.instruction_sram_" + n, 15, yy, "FS"))
    fps.append(dict(
        id="fp6_columns_trk", tiles="6x4",
        description="Columnar: two stacks of four FS macros (pins up) at the left (k 0) and right (k 15) core "
                    "edges, y %s, ~100 um bays above each pin edge open towards the centre; the central "
                    "x 258..1012 band is macro-free over the full height. Rotated (E/W) placements are not "
                    "possible: the Metal4 power columns would run horizontally." % "/".join("%.3f" % v for v in ys),
        derivation={"left_k": 0, "right_k": 15, "fs_y": ys, "bay_um": 100.0},
        instances=col))

    # 6x4 tworow_trk with the top row lowered to leave a horizontal routing channel between it and
    # the top core edge. Every I/O pin is a Metal4 pin at x 29.8..191.0 on the top edge, so at 6x4
    # (first top macro at k 3, x 213.36) the I/O nets and the logic reaching them share a ~210 um
    # corridor; the residual DRT shorts of the diet4 6x4 runs cross the top-row macros (e.g. net3110
    # through e2_lo at x 344..369 in 23751806_0 and again, on Metal2/Metal3, with Metal4 obstructed,
    # in 23768772_0). A channel above the top row gives those nets a legal path. y = 3.78 m with
    # m = 2 (mod 8) keeps the N pin band on a Metal2 track (as n_y_on_track).
    for m_row, tag in ((162, "top30"), (154, "top60")):
        ytop = round(ROW * m_row, 2)
        chan = 706.86 - (ytop + H)
        fps.append(dict(
            id="fp6_tworow_trk_" + tag, tiles="6x4",
            description="fp6_tworow_trk with the top N row lowered to y %.2f (row %d), leaving a %.1f um "
                        "macro-free channel between it and the top core edge (706.86) for nets to reach the "
                        "I/O pins at x 29.8..191.0." % (ytop, m_row, chan),
            derivation={"bottom_k": [0, 4, 8, 12], "top_k": [3, 7, 11, 15], "fs_y": Y_FS_TRK, "n_y": ytop,
                        "top_channel_um": round(chan, 2)},
            instances=two_rows([0, 4, 8, 12], [3, 7, 11, 15], Y_FS_TRK, top_y=ytop)))

    # 6x4 + Metal4 routing obstructions over every macro footprint. The first diet4 6x4 runs
    # (matrix var, 2026-09-25) that did not route clean all ended with residual DRT shorts
    # inside macro footprints, on Metal4 (full macro height, 0.2 um wide), against VPWR/VGND or
    # the macro instance: signal nets crossing a macro vertically on Metal4, the only routing
    # layer left over it (Metal1/Metal3 are fully blocked, Metal2 nearly), between the LEF's
    # full-height VDD!/VSS!/VDDARRAY! Metal4 pins and OBS columns. LibreLane adds
    # ROUTING_OBSTRUCTIONS in Odb.AddRoutingObstructions after OpenROAD.GeneratePDN and removes
    # them in Odb.RemoveRoutingObstructions after OpenROAD.DetailedRouting (Classic flow order in
    # the 3.1.0.dev3 SIF), so the PDN over the macros is unaffected and signals must go around.
    for base in [f for f in fps if f["tiles"] == "6x4" and not f["id"].endswith(("_top30", "_top60"))]:
        obs = []
        for name, v in base["instances"]:
            x, y = v["location"]
            obs.append(["Metal4", round(x, 3), round(y, 3), round(x + W, 3), round(y + H, 3)])
        fps.append(dict(
            id=base["id"] + "_m4obs", tiles="6x4",
            description=base["description"] + " Plus a Metal4 routing obstruction over every macro "
                        "footprint (ROUTING_OBSTRUCTIONS): no signal crosses a macro on Metal4.",
            derivation={"base": base["id"], "routing_obstructions": "Metal4 over each macro bbox "
                        "(location .. location + %.2f x %.2f um)" % (W, H)},
            instances=list(base["instances"]),
            config={"ROUTING_OBSTRUCTIONS": obs}))
    return fps


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(REPO, "floorplans"))
    ap.add_argument("--check", action="store_true", help="run macros/check_macro_floorplan.py on each file")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    paths = []
    for fp in floorplans():
        doc = {
            "id": fp["id"],
            "tiles": fp["tiles"],
            "macro": MACRO,
            "description": fp["description"],
            "derivation": fp["derivation"],
            "generator": "scripts/sweep/gen_floorplans.py",
            "instances": dict(fp["instances"]),
        }
        if fp.get("config"):
            doc["config"] = fp["config"]
        path = os.path.join(args.out, fp["id"] + ".json")
        with open(path, "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        paths.append(path)
        print("wrote", os.path.relpath(path, REPO), flush=True)
    if args.check:
        rc = subprocess.call([sys.executable, os.path.join(REPO, "macros", "check_macro_floorplan.py"),
                              "--summary"] + ["--floorplan=" + p for p in paths])
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
