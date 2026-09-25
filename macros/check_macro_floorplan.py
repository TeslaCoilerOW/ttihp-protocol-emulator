#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the SRAM macro floorplan in src/config.json without running any EDA tool.

This is a local pre-push check. It reproduces the geometry rules that the
flow (src/sram_pdn_cfg.tcl), the TT precheck and the placer enforce:

  * src/config.json parses, and every file referenced from MACROS exists;
  * MACROS instance keys equal the flattened SRAM instance paths in
    src/project.v + src/protocol_emulator_core.v, and each instance has
    PDN_MACRO_CONNECTIONS for every LEF power/ground pin;
  * each placement is inside the core, with an orientation that keeps the
    Metal4 power columns vertical (N or FS);
  * no two macros overlap, including their halos, and no top-edge macro sits
    under the TT I/O pins;
  * the FP_PDN_V* stripe grid (as pdngen lays it out) puts every stripe that
    crosses a macro inside same-net LEF power columns, over the macro's full
    height. The only allowed gap is up to 7 um between two same-net pins. Every
    macro supply pin also carries at least one stripe.

It also predicts detailed-routing pin access (never fatal): a macro signal pin
whose x span holds no Metal2 x track and whose y span holds no Metal2 y track
(both 0.48 um pitch, PDK tracks.info) is reported by OpenROAD as DRT-0418 "no
pins on routing grid". This rule reproduces all 60 DRT-0418 warnings of local
run2 (job 23720702): 15 on each FS macro on core row 0, none on the N macros.

Usage:
  python3 macros/check_macro_floorplan.py [--repo DIR]
      checks src/config.json at the tile size in info.yaml (the submission).
  python3 macros/check_macro_floorplan.py --floorplan floorplans/fp6_block_trk.json
      checks src/config.json with MACROS placements replaced by the floorplan
      file's, at the floorplan's tile size (or --tiles). --floorplan may be
      repeated or name a directory (every *.json in it); --summary adds a table.
  Other options: --config FILE, --info FILE, --core FILE, --project FILE, --tiles WxH.
Exit status 0 means that all checks passed (for every floorplan checked).
"""

import argparse
import copy
import glob
import json
import math
import os
import re
import sys

# tt-support-tools branch ihp-sg13cmos5l, tech/ihp-sg13cmos5l/tile_sizes.yaml at
# d66cf179e (2026-09-21; 8x4 added there). Die sizes in um.
TILE_SIZES = {
    "1x1": (202.08, 154.98), "1x2": (202.08, 313.74),
    "2x1": (419.52, 154.98), "2x2": (419.52, 313.74),
    "3x1": (636.96, 154.98), "3x2": (636.96, 313.74), "3x4": (636.96, 710.64),
    "4x1": (854.40, 154.98), "4x2": (854.40, 313.74), "4x4": (854.40, 710.64),
    "5x4": (1071.84, 710.64),
    "6x1": (1289.28, 154.98), "6x2": (1289.28, 313.74), "6x4": (1289.28, 710.64),
    "8x1": (1724.16, 154.98), "8x2": (1724.16, 313.74), "8x4": (1724.16, 710.64),
}
SITE_W = 0.48  # CoreSite width (tt_block_*_pgvdd.def ROW step)
ROW_H = 3.78  # CoreSite height
# TT I/O pins on the top block edge, the same span in every tt_block_*_pgvdd.def
# (checked 1x1, 3x4, 6x4, 8x2, 8x4): Metal4 pins x 29.76..191.04, 0.3 um wide.
IO_PIN_X = (29.76 - 0.15, 191.04 + 0.15)
DEFAULT_HALO = 10.0  # LibreLane FP_MACRO_{HORIZONTAL,VERTICAL}_HALO default
MAX_PIN_GAP = 7.0  # same as ::pe_sram_max_pin_gap in src/sram_pdn_cfg.tcl
M2_PITCH = 0.48  # Metal2 x and y tracks: multiples of 0.48 um from the die origin
EPS = 1e-6


def fail(errors, msg):
    errors.append(msg)
    print(f"FAIL {msg}")


def resolve(src_dir, ref):
    if ref.startswith("dir::"):
        return os.path.normpath(os.path.join(src_dir, ref[len("dir::"):]))
    return ref  # pdk_dir:: or absolute: not checked here


def parse_lef(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    size = re.search(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", text)
    width, height = float(size.group(1)), float(size.group(2))
    pins = {}
    for m in re.finditer(r"^\s*PIN\s+(\S+)(.*?)^\s*END\s+\1\s*$", text, re.S | re.M):
        name, body = m.group(1), m.group(2)
        use = re.search(r"USE\s+(\w+)", body)
        rects = []
        layer = None
        for line in body.splitlines():
            lm = re.match(r"\s*LAYER\s+(\S+)", line)
            if lm:
                layer = lm.group(1)
            rm = re.match(r"\s*RECT\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", line)
            if rm:
                rects.append((layer, *map(float, rm.groups())))
        pins[name] = {"use": use.group(1) if use else None, "rects": rects}
    return width, height, pins


def instance_paths(project_v, core_v, macro):
    top = open(project_v, encoding="utf-8").read()
    wrap = re.search(r"\bprotocol_emulator_core\s+(\w+)\s*\(", top)
    core = open(core_v, encoding="utf-8").read()
    names = re.findall(rf"\b{re.escape(macro)}\s+(?:#\s*\(.*?\)\s*)?(\w+)\s*\(", core, re.S)
    if wrap is None:
        return None, names
    return wrap.group(1), [f"{wrap.group(1)}.{n}" for n in names]


def _track_inside(lo, hi, pitch=M2_PITCH):
    n = math.ceil(lo / pitch - 1e-9)
    return n * pitch <= hi + 1e-9


def oriented(rect, mx0, my0, my1, o):
    """LEF-local rect -> die coordinates for N/R0 or FS/MX."""
    lx0, ly0, lx1, ly1 = rect
    if o in ("N", "R0"):
        return mx0 + lx0, my0 + ly0, mx0 + lx1, my0 + ly1
    return mx0 + lx0, my1 - ly1, mx0 + lx1, my1 - ly0  # FS / MX: mirror about the x axis


def check(cfg, src_dir, tiles, project_v, core_v, label=None):
    """Run every check on one configuration. Returns (errors, summary dict)."""
    errors = []
    summary = {"label": label, "tiles": tiles}
    if tiles not in TILE_SIZES:
        fail(errors, f"tiles {tiles!r} has no known die size here")
        return errors, summary
    die_w, die_h = TILE_SIZES[tiles]
    left = cfg.get("LEFT_MARGIN_MULT", 6) * SITE_W
    bottom = cfg.get("BOTTOM_MARGIN_MULT", 1) * ROW_H
    right = cfg.get("RIGHT_MARGIN_MULT", 6) * SITE_W
    top = cfg.get("TOP_MARGIN_MULT", 1) * ROW_H
    n_sites = int((die_w - left - right) / SITE_W + EPS)
    n_rows = int((die_h - bottom - top) / ROW_H + EPS)
    core = (left, bottom, left + n_sites * SITE_W, bottom + n_rows * ROW_H)
    print(f"INFO tiles {tiles}: die {die_w} x {die_h}; core x {core[0]:.2f}..{core[2]:.2f} "
          f"y {core[1]:.2f}..{core[3]:.2f}")

    macros = cfg.get("MACROS", {})
    if not macros:
        fail(errors, "no MACROS in the configuration")
        return errors, summary
    placed = []  # (name, macro, x0, y0, x1, y1, orient, pins)
    for macro, spec in macros.items():
        for key in ("gds", "lef", "nl", "spice"):
            for ref in spec.get(key, []):
                p = resolve(src_dir, ref)
                if not os.path.isfile(p):
                    fail(errors, f"{macro}.{key}: {ref} -> {p} does not exist")
        for corner, refs in spec.get("lib", {}).items():
            for ref in refs:
                p = resolve(src_dir, ref)
                if not os.path.isfile(p):
                    fail(errors, f"{macro}.lib[{corner}]: {ref} -> {p} does not exist")
        lef = resolve(src_dir, spec["lef"][0])
        w, h, pins = parse_lef(lef)
        m = re.search(r"MACRO\s+(\S+)", open(lef, encoding="utf-8", errors="replace").read())
        if m.group(1) != macro:
            fail(errors, f"LEF {lef} defines MACRO {m.group(1)}, config key is {macro}")
        print(f"INFO {macro}: LEF SIZE {w} x {h}; supply pins "
              f"{sorted(n for n, p in pins.items() if p['use'] in ('POWER', 'GROUND'))}")

        wrap, expected = instance_paths(project_v, core_v, macro)
        got = set(spec.get("instances", {}))
        if set(expected) != got:
            fail(errors, f"{macro}: MACROS instances {sorted(got)} != flattened RTL instances {sorted(expected)}")
        else:
            print(f"PASS {macro}: {len(got)} MACROS instance keys equal the flattened RTL paths "
                  f"(wrapper instance '{wrap}')")

        supplies = {n: p["use"] for n, p in pins.items() if p["use"] in ("POWER", "GROUND")}
        conns = {}
        for line in cfg.get("PDN_MACRO_CONNECTIONS", []):
            inst, vdd, gnd, ppin, gpin = line.split()
            conns.setdefault(inst, []).append((vdd, gnd, ppin, gpin))
        for inst in got:
            hooked = set()
            for vdd, gnd, ppin, gpin in conns.get(inst, []):
                if supplies.get(ppin) != "POWER":
                    fail(errors, f"{inst}: '{ppin}' is not a LEF POWER pin of {macro}")
                if supplies.get(gpin) != "GROUND":
                    fail(errors, f"{inst}: '{gpin}' is not a LEF GROUND pin of {macro}")
                if (vdd, gnd) != ("VPWR", "VGND"):
                    fail(errors, f"{inst}: connects to {vdd}/{gnd}, expected VPWR/VGND")
                hooked |= {ppin, gpin}
            if hooked != set(supplies):
                fail(errors, f"{inst}: PDN_MACRO_CONNECTIONS cover {sorted(hooked)}, LEF has {sorted(supplies)}")
        print(f"INFO PDN_MACRO_CONNECTIONS checked for {len(got)} instances")

        for inst, data in spec.get("instances", {}).items():
            x, y = map(float, data["location"])
            o = data["orientation"]
            if o not in ("N", "R0", "FS", "MX"):
                fail(errors, f"{inst}: orientation {o} rotates or X-mirrors the Metal4 power columns")
            placed.append((inst, macro, x, y, x + w, y + h, o, pins))

    hh = float(cfg.get("FP_MACRO_HORIZONTAL_HALO", DEFAULT_HALO))
    vh = float(cfg.get("FP_MACRO_VERTICAL_HALO", DEFAULT_HALO))
    off_site = 0
    for inst, _, x0, y0, x1, y1, o, _ in placed:
        if x0 < core[0] - EPS or y0 < core[1] - EPS or x1 > core[2] + EPS or y1 > core[3] + EPS:
            fail(errors, f"{inst}: bbox {x0:.2f},{y0:.2f}..{x1:.2f},{y1:.2f} leaves the core")
        if abs(x0 / SITE_W - round(x0 / SITE_W)) > 1e-6:
            off_site += 1
            print(f"WARN {inst}: x {x0} is not on the {SITE_W} um site grid")
        if abs(x0 / 0.005 - round(x0 / 0.005)) > 1e-6 or abs(y0 / 0.005 - round(y0 / 0.005)) > 1e-6:
            fail(errors, f"{inst}: location {x0},{y0} is not on the 0.005 um grid "
                         f"(Odb.ManualMacroPlacement would move it)")
        if y1 + vh > core[3] - ROW_H and x0 - hh < IO_PIN_X[1] and x1 + hh > IO_PIN_X[0]:
            fail(errors, f"{inst}: top-edge macro overlaps the TT I/O pin span x {IO_PIN_X}")
    min_gap = None
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            if (a[2] - hh < b[4] + hh and b[2] - hh < a[4] + hh and
                    a[3] - vh < b[5] + vh and b[3] - vh < a[5] + vh):
                # halos may touch but the macros themselves need >= 2 halos between them
                if not (a[2] >= b[4] + 2 * hh - EPS or b[2] >= a[4] + 2 * hh - EPS or
                        a[3] >= b[5] + 2 * vh - EPS or b[3] >= a[5] + 2 * vh - EPS):
                    fail(errors, f"{a[0]} and {b[0]} are closer than two halos ({hh}/{vh} um)")
            gx = max(b[2] - a[4], a[2] - b[4], 0.0)
            gy = max(b[3] - a[5], a[3] - b[5], 0.0)
            g = math.hypot(gx, gy)
            min_gap = g if min_gap is None else min(min_gap, g)
    if not [e for e in errors if "core" in e or "halo" in e or "I/O" in e]:
        print(f"PASS {len(placed)} placements inside the core, >= 2 halos apart "
              f"(h {hh}, v {vh} um), clear of the I/O pin span")

    # --- stripe grid, as pdngen builds it (centre = core xMin + VOFFSET + n * VPITCH)
    width = float(cfg["FP_PDN_VWIDTH"])
    pitch = float(cfg["FP_PDN_VPITCH"])
    spacing = float(cfg["FP_PDN_VSPACING"])
    offset = float(cfg["FP_PDN_VOFFSET"])
    stripes = []  # (net, x0, x1)
    n = 0
    while True:
        pc = core[0] + offset + n * pitch
        gc = pc + spacing + width
        if gc + width / 2 > core[2] + EPS:
            if pc - width / 2 < core[2] - EPS:
                # pdngen could drop or clip this pair; a clipped stripe
                # narrower than VWIDTH fails the precheck power-port rule.
                fail(errors, f"stripe pair {n} (x {pc - width / 2:.2f}..{gc + width / 2:.2f}) "
                             f"straddles the core right edge {core[2]:.2f}; adjust FP_PDN_VOFFSET")
            break
        stripes.append(("VPWR", pc - width / 2, pc + width / 2))
        stripes.append(("VGND", gc - width / 2, gc + width / 2))
        n += 1
    print(f"INFO {n} stripe pairs fit the core (last GROUND stripe ends at "
          f"{stripes[-1][2]:.2f} <= {core[2]:.2f})")
    net_of = {"POWER": "VPWR", "GROUND": "VGND"}
    for inst, _, mx0, my0, mx1, my1, o, pins in placed:
        cols = []  # (net, pin, x0, y0, x1, y1) die coordinates
        for pname, p in pins.items():
            if p["use"] not in net_of:
                continue
            for layer, lx0, ly0, lx1, ly1 in p["rects"]:
                if layer != "Metal4":
                    continue
                cols.append((net_of[p["use"]], pname) + oriented((lx0, ly0, lx1, ly1), mx0, my0, my1, o))
        hits = {pn: 0 for pn, p in pins.items() if p["use"] in net_of}
        crossing = [s for s in stripes if s[2] > mx0 + EPS and s[1] < mx1 - EPS]
        for net, sx0, sx1 in crossing:
            cover = sorted((c for c in cols if c[0] == net and c[2] <= sx0 + EPS and c[4] >= sx1 - EPS),
                           key=lambda c: c[3])
            cur, why, first = my0, None, True
            used = set()
            for c in cover:
                if c[3] > cur + EPS:
                    if first or c[3] - cur > MAX_PIN_GAP:
                        why = f"no same-net pin from y {cur:.3f} to {c[3]:.3f}"
                        break
                cur = max(cur, c[5])
                used.add(c[1])
                first = False
            if why is None and cur < my1 - EPS:
                why = f"no same-net pin from y {cur:.3f} to {my1:.3f}"
            if why:
                fail(errors, f"{inst}: {net} stripe x {sx0:.3f}..{sx1:.3f}: {why}")
            else:
                for u in used:
                    hits[u] += 1
                margin = min(min(sx0 - c[2], c[4] - sx1) for c in cover)
                print(f"INFO {inst}: {net} stripe centre {(sx0 + sx1) / 2:.2f} "
                      f"(local {(sx0 + sx1) / 2 - mx0:.2f}) inside {sorted(used)}, "
                      f"min edge margin {margin:.3f} um")
        for pn, k in hits.items():
            if k == 0:
                fail(errors, f"{inst}: supply pin {pn} carries no stripe")
        if all(hits.values()):
            print(f"PASS {inst}: {len(crossing)} stripes inside same-net columns; "
                  + ", ".join(f"{pn} x{k}" for pn, k in sorted(hits.items())))

    # --- pin access prediction (DRT-0418), never fatal
    off_grid_total = 0
    for inst, _, mx0, my0, mx1, my1, o, pins in placed:
        n_sig = off = 0
        for pname, p in pins.items():
            if p["use"] in ("POWER", "GROUND"):
                continue
            n_sig += 1
            on = False
            for layer, lx0, ly0, lx1, ly1 in p["rects"]:
                x0, y0, x1, y1 = oriented((lx0, ly0, lx1, ly1), mx0, my0, my1, o)
                if _track_inside(x0, x1) or _track_inside(y0, y1):
                    on = True
            off += 0 if on else 1
        off_grid_total += off
        if off:
            print(f"WARN {inst}: {off}/{n_sig} signal pins hold no Metal2 track in x or y "
                  f"(predicted DRT-0418 'no pins on routing grid')")
    print(f"INFO pin access: {off_grid_total} macro signal pins predicted off the Metal2 routing grid")

    core_area = (core[2] - core[0]) * (core[3] - core[1])
    macro_area = sum((p[4] - p[2]) * (p[5] - p[3]) for p in placed)
    print(f"INFO core {core_area:,.0f} um2; macros {macro_area:,.0f} um2 ({100 * macro_area / core_area:.1f}%)")
    summary.update(macros=len(placed), macro_pct=100 * macro_area / core_area, stripe_pairs=n,
                   off_site=off_site, drt0418_pred=off_grid_total,
                   min_gap=min_gap if min_gap is not None else float("nan"), errors=len(errors))
    return errors, summary


def apply_floorplan(cfg, fp):
    """Return a copy of cfg with MACROS[<macro>].instances (and any fp 'config' keys) from floorplan fp."""
    cfg = copy.deepcopy(cfg)
    macro = fp.get("macro")
    macros = cfg.get("MACROS", {})
    if macro is None:
        if len(macros) != 1:
            raise ValueError("floorplan has no 'macro' and the config has %d macro types" % len(macros))
        macro = next(iter(macros))
    if macro not in macros:
        raise ValueError(f"floorplan macro {macro} is not in the config MACROS")
    macros[macro]["instances"] = copy.deepcopy(fp["instances"])
    for k, v in fp.get("config", {}).items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    return cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--config", help="LibreLane config (default <repo>/src/config.json); dir:: resolves "
                                     "relative to its directory")
    ap.add_argument("--info", help="info.yaml for the tile size (default <repo>/info.yaml)")
    ap.add_argument("--tiles", help="tile size override, e.g. 6x4")
    ap.add_argument("--core", help="core Verilog for the flattened instance names "
                                   "(default <repo>/src/protocol_emulator_core.v)")
    ap.add_argument("--project", help="wrapper Verilog (default <repo>/src/project.v)")
    ap.add_argument("--floorplan", action="append", default=[],
                    help="floorplan JSON (floorplans/*.json) or a directory of them; repeatable")
    ap.add_argument("--summary", action="store_true", help="print a one-line-per-floorplan table at the end")
    args = ap.parse_args()
    repo = args.repo

    cfg_path = args.config or os.path.join(repo, "src/config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    print(f"PASS {os.path.relpath(cfg_path, repo) if not args.config else cfg_path} is valid JSON")
    src_dir = os.path.dirname(os.path.abspath(cfg_path))
    project_v = args.project or os.path.join(repo, "src/project.v")
    core_v = args.core or os.path.join(repo, "src/protocol_emulator_core.v")

    fps = []
    for f in args.floorplan:
        if os.path.isdir(f):
            fps.extend(sorted(glob.glob(os.path.join(f, "*.json"))))
        else:
            fps.append(f)

    if not fps:
        if args.tiles:
            tiles = args.tiles
        else:
            info = open(args.info or os.path.join(repo, "info.yaml"), encoding="utf-8").read()
            tiles = re.search(r'^\s*tiles:\s*"([^"]+)"', info, re.M).group(1)
        errors, _ = check(cfg, src_dir, tiles, project_v, core_v)
        print("RESULT", "PASS" if not errors else f"FAIL ({len(errors)} error(s))")
        return 0 if not errors else 1

    results = []
    for f in fps:
        fp = json.load(open(f, encoding="utf-8"))
        label = fp.get("id", os.path.splitext(os.path.basename(f))[0])
        tiles = args.tiles or fp.get("tiles")
        print(f"==== floorplan {label} ({f}), tiles {tiles}")
        try:
            fcfg = apply_floorplan(cfg, fp)
        except ValueError as e:
            print(f"FAIL {e}")
            results.append(({"label": label, "tiles": tiles, "errors": 1}, [str(e)]))
            print(f"RESULT {label} FAIL (1 error(s))")
            continue
        errors, summ = check(fcfg, src_dir, tiles, project_v, core_v, label)
        results.append((summ, errors))
        print(f"RESULT {label}", "PASS" if not errors else f"FAIL ({len(errors)} error(s))")

    nfail = sum(1 for _, e in results if e)
    if args.summary:
        print()
        lw = max([18] + [len(str(s["label"])) for s, _ in results])
        print(f"{'floorplan':<{lw}} {'tiles':<5} {'result':<6} {'macro%':>6} {'pairs':>5} "
              f"{'offsite':>7} {'minGap':>7} {'DRT0418':>7}")
        for s, e in results:
            print(f"{s['label']:<{lw}} {s['tiles'] or '?':<5} {'FAIL' if e else 'PASS':<6} "
                  f"{s.get('macro_pct', float('nan')):6.1f} {s.get('stripe_pairs', 0):5d} "
                  f"{s.get('off_site', 0):7d} {s.get('min_gap', float('nan')):7.1f} "
                  f"{s.get('drt0418_pred', -1):7d}")
    print("RESULT", "PASS" if not nfail else f"FAIL ({nfail} of {len(results)} floorplan(s))")
    return 0 if not nfail else 1


if __name__ == "__main__":
    sys.exit(main())
