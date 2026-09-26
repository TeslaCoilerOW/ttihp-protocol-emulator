#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Predict the short VPWR/VGND Metal4 straps that fail the TT precheck Pin check.

Tool-free geometric model (standard library only) of two LibreLane 3.1.0.dev3
steps on this design:

  * OpenROAD.CutRows: every CoreSite row is cut around each macro's bounding
    box grown by FP_MACRO_HORIZONTAL_HALO / FP_MACRO_VERTICAL_HALO; the cut
    ends snap to the 0.48 um site grid, and remnants narrower than one site
    disappear.
  * OpenROAD.GeneratePDN (src/sram_pdn_cfg.tcl): full-height Metal4 stripe
    pairs on the FP_PDN_V* lattice. A row segment that no VPWR stripe and no
    VGND stripe crosses cannot reach the grid through its Metal1 followpins, so
    pdngen adds a short VPWR/VGND strap pair over it. Magic.WriteLEF exports
    those straps as power ports, and the precheck rejects every Metal4 power
    port that does not reach within 10 um of both the top and the bottom edge
    (docs/drc-triage.md section 6).

FP_OBSTRUCTIONS boxes are modelled as blockages without halo: OpenROAD makes
no row under them (the PDN-stage runs of docs/6x4.md section 2).

A row segment with no stripe of either net over it is an "island". Islands in
adjacent rows that overlap in x form one channel, which is where pdngen puts
one strap pair.

The model reproduces the local evidence of docs/drc-triage.md: 4 channels for
the 8x4 fp8_base placement with the default 10 um halo (run2, job 23720702;
row segments x 393.12..405.60, y 3.78..79.38 in the e0 gap), 0 with 16.48 or
17 um, and 7 channels for the 6x4 fp6_tworow placement with 10 um (14 precheck
LEF errors, precheck job 23806048).

--derive reports, per floorplan, the smallest horizontal halo that closes
every stripe-free gap: half the gap between two macros, or the whole gap
between a macro and the core edge (a gap that a lattice stripe pair crosses
needs nothing). It also sweeps the halo on a 0.01 um grid and reports the
smallest value at which the model has no island left, which can be up to one
site (0.48 um) lower because of the site snapping.

Usage:
  variants6x4/row_islands.py                                  # the submission (src/config.json, info.yaml)
  variants6x4/row_islands.py --floorplan floorplans/fp6_tworow.json [--halo 16.48]
  variants6x4/row_islands.py --floorplan floorplans --derive  # every floorplan file in a directory
  variants6x4/row_islands.py --floorplan floorplans/fp6_tworow.json --derive --emit floorplans
                                                              # also write fp6_tworow_h<halo>.json
Exit status: 0 if every checked placement has no island, 1 otherwise.
"""

import argparse
import glob
import json
import math
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# tt-support-tools ihp-sg13cmos5l tile_sizes.yaml (d66cf179e); same table as
# macros/check_macro_floorplan.py.
TILE_SIZES = {
    "1x1": (202.08, 154.98), "1x2": (202.08, 313.74),
    "2x1": (419.52, 154.98), "2x2": (419.52, 313.74),
    "3x1": (636.96, 154.98), "3x2": (636.96, 313.74), "3x4": (636.96, 710.64),
    "4x1": (854.40, 154.98), "4x2": (854.40, 313.74), "4x4": (854.40, 710.64),
    "5x4": (1071.84, 710.64),
    "6x1": (1289.28, 154.98), "6x2": (1289.28, 313.74), "6x4": (1289.28, 710.64),
    "8x1": (1724.16, 154.98), "8x2": (1724.16, 313.74), "8x4": (1724.16, 710.64),
}
# tt_block_<tiles>_pgvdd.def: first ROW at (2.88, 3.78), CoreSite 0.48 x 3.78,
# rows fill the die minus 2.88 um left/right and 3.78 um top/bottom margins.
SITE_W = 0.48
ROW_H = 3.78
MARGIN_X = 2.88
MARGIN_Y = 3.78
DEFAULT_HALO = 10.0  # LibreLane FP_MACRO_{HORIZONTAL,VERTICAL}_HALO default
DBU = 1000  # database units per um (the DEF templates)


def dbu(x):
    return int(round(float(x) * DBU))


def um(x):
    return x / DBU


def core_box(tiles):
    w, h = TILE_SIZES[tiles]
    n_sites = int(round((w - 2 * MARGIN_X) / SITE_W))
    n_rows = int(round((h - 2 * MARGIN_Y) / ROW_H))
    return (dbu(MARGIN_X), dbu(MARGIN_Y), dbu(MARGIN_X) + n_sites * dbu(SITE_W),
            dbu(MARGIN_Y) + n_rows * dbu(ROW_H)), n_rows


def lef_size(lef_path, macro):
    text = open(lef_path).read()
    m = re.search(r"MACRO\s+%s\b.*?SIZE\s+([\d.]+)\s+BY\s+([\d.]+)" % re.escape(macro), text, re.S)
    if not m:
        raise SystemExit("no SIZE for %s in %s" % (macro, lef_path))
    return float(m.group(1)), float(m.group(2))


def resolve_dir_path(p, base):
    return os.path.normpath(os.path.join(base, p[len("dir::"):])) if p.startswith("dir::") else p


def macros_from_config(cfg, src_dir):
    """[(inst, x0, y0, x1, y1)] in dbu from a config's MACROS block."""
    out = []
    for macro, spec in cfg.get("MACROS", {}).items():
        lef = resolve_dir_path(spec["lef"][0], src_dir)
        w, h = lef_size(lef, macro)
        for inst, d in spec.get("instances", {}).items():
            x, y = map(float, d["location"])
            if d.get("orientation") not in ("N", "FS", "R0", "MX", None):
                raise SystemExit("%s: orientation %s not modelled" % (inst, d.get("orientation")))
            out.append((inst, dbu(x), dbu(y), dbu(x + w), dbu(y + h)))
    return out


def stripes(cfg, core):
    """[(net, x0, x1)] in dbu, as pdngen lays out the FP_PDN_V* lattice (see check_macro_floorplan.py)."""
    width = dbu(cfg["FP_PDN_VWIDTH"])
    pitch = dbu(cfg["FP_PDN_VPITCH"])
    spacing = dbu(cfg["FP_PDN_VSPACING"])
    offset = dbu(cfg["FP_PDN_VOFFSET"])
    out = []
    n = 0
    while True:
        pc = core[0] + offset + n * pitch
        gc = pc + spacing + width
        if gc + width // 2 > core[2]:
            break
        out.append(("VPWR", pc - width // 2, pc + width // 2))
        out.append(("VGND", gc - width // 2, gc + width // 2))
        n += 1
    return out


def obstructions(cfg):
    """FP_OBSTRUCTIONS boxes in dbu: hard placement blockages created before the rows, so
    OpenROAD makes no CoreSite row under them (LibreLane 3.1.0.dev3 floorplan.tcl)."""
    return [tuple(dbu(v) for v in box) for box in cfg.get("FP_OBSTRUCTIONS") or []]


def row_segments(core, n_rows, macros, hh, vh, obs=()):
    """{row: [(x0, x1)]} after cut_rows with halo (hh, vh) in dbu, and without the FP_OBSTRUCTIONS boxes."""
    site = dbu(SITE_W)
    segs = {}
    for r in range(n_rows):
        ry0 = core[1] + r * dbu(ROW_H)
        ry1 = ry0 + dbu(ROW_H)
        blk = sorted([(mx0 - hh, mx1 + hh) for _, mx0, my0, mx1, my1 in macros
                      if my0 - vh < ry1 and my1 + vh > ry0] +
                     [(ox0, ox1) for ox0, oy0, ox1, oy1 in obs if oy0 < ry1 and oy1 > ry0])
        out = []
        start = core[0]
        for bx0, bx1 in blk:
            # snap the segment end down and the next start up to the row's site grid
            end = core[0] + max(0, (bx0 - core[0]) // site) * site
            if end - start >= site:
                out.append((start, min(end, core[2])))
            nxt = core[0] + -(-(bx1 - core[0]) // site) * site
            start = max(start, nxt)
        if core[2] - start >= site:
            out.append((start, core[2]))
        segs[r] = out
    return segs


def islands(core, n_rows, macros, strp, hh, vh, obs=()):
    """Row segments that no VPWR or no VGND stripe crosses: [(row, x0, x1, y0, y1)]."""
    segs = row_segments(core, n_rows, macros, hh, vh, obs)
    out = []
    for r, lst in segs.items():
        ry0 = core[1] + r * dbu(ROW_H)
        for x0, x1 in lst:
            nets = {n for n, s0, s1 in strp if s0 < x1 and s1 > x0}
            if not {"VPWR", "VGND"} <= nets:
                out.append((r, x0, x1, ry0, ry0 + dbu(ROW_H)))
    return out


def channels(isl):
    """Group islands of adjacent rows that overlap in x: [(x0, y0, x1, y1, n_segments)]."""
    groups = []
    for r, x0, x1, y0, y1 in sorted(isl):
        for g in groups:
            if g["last_row"] == r - 1 and g["x0"] < x1 and g["x1"] > x0:
                g.update(x0=min(g["x0"], x0), x1=max(g["x1"], x1), y1=y1, last_row=r, n=g["n"] + 1)
                break
        else:
            groups.append(dict(x0=x0, x1=x1, y0=y0, y1=y1, last_row=r, n=1))
    return [(g["x0"], g["y0"], g["x1"], g["y1"], g["n"]) for g in groups]


def closure_halo(core, macros, strp, vh):
    """Smallest horizontal halo that closes every stripe-free gap, and the gaps (dbu)."""
    need = 0
    gaps = []
    # horizontal gaps inside each group of macros that share rows
    for inst, mx0, my0, mx1, my1 in macros:
        # neighbours to the right that share at least one row band, and the core edges
        right = [(ox0, oinst) for oinst, ox0, oy0, ox1, oy1 in macros
                 if ox0 >= mx1 and oy0 - vh < my1 + vh and oy1 + vh > my0 - vh]
        left_edge = not [1 for oinst, ox0, oy0, ox1, oy1 in macros
                         if ox1 <= mx0 and oy0 - vh < my1 + vh and oy1 + vh > my0 - vh]
        if right:
            ox0, oinst = min(right)
            a, b = mx1, ox0
            kind, other = "macro", oinst
        else:
            a, b = mx1, core[2]
            kind, other = "edge", "core right edge"
        crossed = {n for n, s0, s1 in strp if s0 >= a and s1 <= b}
        if b > a and not {"VPWR", "VGND"} <= crossed:
            h = (b - a + 1) // 2 if kind == "macro" else b - a
            gaps.append((inst, other, a, b, h))
            need = max(need, h)
        if left_edge:
            a, b = core[0], mx0
            crossed = {n for n, s0, s1 in strp if s0 >= a and s1 <= b}
            if b > a and not {"VPWR", "VGND"} <= crossed:
                gaps.append(("core left edge", inst, a, b, b - a))
                need = max(need, b - a)
    return need, gaps


def load_case(args, fp_path):
    config_path = args.config or os.path.join(REPO, "src", "config.json")
    cfg = json.load(open(config_path))
    src_dir = os.path.dirname(os.path.abspath(config_path))
    if not os.path.isdir(os.path.join(src_dir, "..", "macros")):
        src_dir = os.path.join(REPO, "src")  # an overlay kept outside src/ still resolves dir:: against src/
    tiles = args.tiles
    label = os.path.relpath(config_path, REPO) if config_path.startswith(REPO) else config_path
    if fp_path:
        fp = json.load(open(fp_path))
        macro = fp.get("macro", "RM_IHPSG13_1P_64x16_c2")
        cfg["MACROS"][macro]["instances"] = fp["instances"]
        cfg.update(fp.get("config", {}))
        tiles = tiles or fp.get("tiles")
        label = fp.get("id", os.path.basename(fp_path))
    if not tiles:
        info = open(os.path.join(REPO, "info.yaml")).read()
        tiles = re.search(r'^\s*tiles:\s*"([^"]+)"', info, re.M).group(1)
    if args.halo is not None:
        cfg["FP_MACRO_HORIZONTAL_HALO"] = args.halo
    return label, tiles, cfg, src_dir


def run_case(args, fp_path):
    label, tiles, cfg, src_dir = load_case(args, fp_path)
    core, n_rows = core_box(tiles)
    macros = macros_from_config(cfg, src_dir)
    strp = stripes(cfg, core)
    hh = dbu(cfg.get("FP_MACRO_HORIZONTAL_HALO", DEFAULT_HALO))
    vh = dbu(cfg.get("FP_MACRO_VERTICAL_HALO", DEFAULT_HALO))
    obs = obstructions(cfg)
    isl = islands(core, n_rows, macros, strp, hh, vh, obs)
    ch = channels(isl)
    print("%s (%s): halo h %.2f v %.2f um, %d stripe pairs, %d FP_OBSTRUCTIONS box(es); "
          "%d island row segment(s) in %d channel(s)"
          % (label, tiles, um(hh), um(vh), len(strp) // 2, len(obs), len(isl), len(ch)))
    for x0, y0, x1, y1, n in ch:
        print("  CHANNEL x %.2f..%.2f y %.2f..%.2f (%d row segments): predicted short VPWR/VGND strap pair"
              % (um(x0), um(x1), um(y0), um(y1), n))
    result = {"floorplan": label, "tiles": tiles, "halo_h": um(hh), "halo_v": um(vh),
              "islands": len(isl), "channels": [[um(c) for c in c4[:4]] for c4 in ch]}
    if args.derive:
        need, gaps = closure_halo(core, macros, strp, vh)
        for a_name, b_name, a, b, h in gaps:
            print("  GAP %s .. %s: x %.2f..%.2f (%.2f um, no stripe pair) needs halo >= %.2f"
                  % (a_name, b_name, um(a), um(b), um(b - a), um(h)))
        lo = None
        for h in range(dbu(DEFAULT_HALO), dbu(80) + 1, 10):
            if not islands(core, n_rows, macros, strp, h, vh, obs):
                lo = h
                break
        print("  DERIVED closure halo %.2f um (gap geometry); model island-free from %s um"
              % (um(max(need, 0)), "%.2f" % um(lo) if lo is not None else "> 80"))
        result.update(closure_halo=um(need), model_threshold=um(lo) if lo is not None else None,
                      gaps=[[a_name, b_name, um(a), um(b), um(h)] for a_name, b_name, a, b, h in gaps])
        if args.emit and fp_path and isl and need > hh:
            emit_floorplan(args.emit, fp_path, um(need), result)
    return result


def emit_floorplan(out_dir, fp_path, halo, result):
    """Write <out_dir>/<id>_h<halo>.json: the same placement with the derived halo as a config key."""
    fp = json.load(open(fp_path))
    tag = ("%.2f" % halo).rstrip("0").rstrip(".").replace(".", "p")
    new_id = "%s_h%s" % (fp["id"], tag)
    gaps = [g for g in result["gaps"] if abs(g[4] - halo) < 1e-9]
    out = {
        "id": new_id,
        "tiles": fp["tiles"],
        "macro": fp.get("macro", "RM_IHPSG13_1P_64x16_c2"),
        "description": "%s with FP_MACRO_HORIZONTAL_HALO %.2f: the smallest halo that leaves no CoreSite row "
                       "segment without a VPWR/VGND lattice stripe, so pdngen adds no short power strap "
                       "(docs/6x4.md). Placement unchanged." % (fp["id"], halo),
        "derivation": {
            "base_floorplan": fp["id"],
            "rule": "halo >= half of every stripe-free macro-to-macro gap and >= every stripe-free "
                    "macro-to-core-edge gap",
            "binding_gaps_um": [[g[0], g[1], round(g[2], 2), round(g[3], 2)] for g in gaps],
            "islands_at_repo_halo": result["islands"],
            "model_island_free_from_um": result.get("model_threshold"),
        },
        "generator": "variants6x4/row_islands.py --derive --emit",
        "instances": fp["instances"],
        "config": dict(fp.get("config", {}), FP_MACRO_HORIZONTAL_HALO=halo),
    }
    path = os.path.join(out_dir, new_id + ".json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print("  WROTE %s" % (os.path.relpath(path, REPO) if path.startswith(REPO) else path))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", help="LibreLane config JSON (default src/config.json)")
    ap.add_argument("--floorplan", action="append", default=[],
                    help="floorplans/<id>.json or a directory of them (MACROS instances + optional config)")
    ap.add_argument("--tiles", help="tile size (default: the floorplan's, else info.yaml)")
    ap.add_argument("--halo", type=float, help="override FP_MACRO_HORIZONTAL_HALO")
    ap.add_argument("--derive", action="store_true", help="report the halo that closes every stripe-free gap")
    ap.add_argument("--emit", metavar="DIR",
                    help="with --derive: write DIR/<id>_h<halo>.json for every floorplan that needs a larger halo")
    ap.add_argument("--json", help="also write the results to this JSON file")
    args = ap.parse_args()
    files = []
    for f in args.floorplan:
        files += sorted(glob.glob(os.path.join(f, "*.json"))) if os.path.isdir(f) else [f]
    results = [run_case(args, f) for f in (files or [None])]
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
            f.write("\n")
    bad = [r["floorplan"] for r in results if r["islands"]]
    print("RESULT %s: %d of %d placement(s) predicted to get short power straps%s"
          % ("FAIL" if bad else "PASS", len(bad), len(results), (": " + ", ".join(bad)) if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
