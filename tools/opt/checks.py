#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Power-port legality checks used on every optimizer trial.

The Tiny Tapeout precheck's pin check (tt-support-tools precheck/pin_check.py,
d66cf179e) requires every VPWR/VGND port of the exported LEF to be on Metal4,
at least 2.1 um wide, within 10 um of both the bottom and the top edge of the
block, and inside it. pdngen adds short VPWR/VGND Metal4 straps when a
standard-cell row segment is not crossed by a lattice stripe, and
Magic.WriteLEF exports every Metal4 stripe as a port, so a short strap fails the
precheck (docs/drc-triage.md section 6). Two checks catch this without running
the precheck:

  def  the rule applied to the Metal4 VPWR/VGND special-net stripes and DEF pins
       of a DEF, normally the OpenROAD.GeneratePDN step's DEF (same logic as the
       short-stripe check of docs/drc-triage.md);
  lef  the rule applied to the final LEF's VPWR/VGND ports, after merging
       vertically touching rectangles with the same x span (the precheck merges
       all touching rectangles; for vertical stripes this is the same).

The precheck itself runs on every promoted configuration. Standard library
only; Python 3.6+.

Usage:
  checks.py def <design.def> [--out result.json]
  checks.py lef <design.lef[.gz]> [--out result.json]
"""

import argparse
import gzip
import json
import re
import sys

POWER_NETS = ("VPWR", "VGND")
LAYER = "Metal4"
MIN_WIDTH = 2.1
EDGE = 10.0
EPS = 1e-6


def _open(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def check_def(path):
    units = 1000.0
    die = None
    pins, stripes = [], []
    sect = None
    pin = None
    pend = []
    net = None
    with _open(path) as f:
        for line in f:
            s = line.strip()
            if s.startswith("UNITS DISTANCE MICRONS"):
                units = float(s.split()[3])
            elif s.startswith("DIEAREA"):
                n = list(map(int, re.findall(r"-?\d+", s)))
                die = (n[0] / units, n[1] / units, n[2] / units, n[3] / units)
            elif s.startswith("PINS "):
                sect = "pins"
            elif s.startswith("END PINS"):
                sect = None
            elif s.startswith("SPECIALNETS"):
                sect = "snets"
            elif s.startswith("END SPECIALNETS"):
                break
            elif sect == "pins":
                if s.startswith("- "):
                    pin = s.split()[1]
                    pend = []
                if s.startswith("+ PORT"):
                    pend = []
                m = re.search(r"\+ LAYER (\S+) \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)", s)
                if m:
                    pend.append((m.group(1),) + tuple(int(m.group(i)) / units for i in range(2, 6)))
                m = re.search(r"\+ (FIXED|PLACED|COVER) \( (-?\d+) (-?\d+) \) (\S+)", s)
                if m and pend and pin in POWER_NETS:
                    x, y = int(m.group(2)) / units, int(m.group(3)) / units
                    for lay, a, b, c, d in pend:
                        if lay == LAYER:
                            pins.append((pin, x + a, y + b, x + c, y + d))
                    pend = []
            elif sect == "snets":
                if s.startswith("- "):
                    net = s.split()[1]
                m = re.search(LAYER + r" (\d+) \+ SHAPE STRIPE \( (-?\d+) (-?\d+) \) \( (\*|-?\d+) (\*|-?\d+) \)", s)
                if m and net in POWER_NETS:
                    w = int(m.group(1)) / units
                    x1, y1 = int(m.group(2)) / units, int(m.group(3)) / units
                    x2 = x1 if m.group(4) == "*" else int(m.group(4)) / units
                    y2 = y1 if m.group(5) == "*" else int(m.group(5)) / units
                    if x1 == x2:
                        stripes.append((net, x1 - w / 2, min(y1, y2), x1 + w / 2, max(y1, y2)))
    if die is None:
        return {"check": "def", "file": path, "ok": False, "error": "no DIEAREA"}
    bad_pins = [p for p in pins if p[2] - die[1] > EDGE or die[3] - p[4] > EDGE or p[3] - p[1] < MIN_WIDTH - EPS]
    bad_str = [p for p in stripes if p[2] - die[1] > EDGE or die[3] - p[4] > EDGE]
    per_net = {n: {"pins": sum(1 for p in pins if p[0] == n), "bad_pins": sum(1 for p in bad_pins if p[0] == n),
                   "stripes": sum(1 for p in stripes if p[0] == n),
                   "short_stripes": sum(1 for p in bad_str if p[0] == n)} for n in POWER_NETS}
    return {"check": "def", "file": path, "die": die, "per_net": per_net,
            "port_violations": len(bad_pins), "short_stripes": len(bad_str),
            "examples": ["%s x %.3f..%.3f y %.3f..%.3f" % p for p in (bad_pins + bad_str)[:8]],
            "ok": not bad_pins and not bad_str and sum(v["stripes"] for v in per_net.values()) > 0}


def _merge_vertical(rects):
    """Merge rectangles with the same x span whose y ranges touch or overlap."""
    by_x = {}
    for lx, by, rx, ty in rects:
        by_x.setdefault((round(lx, 3), round(rx, 3)), []).append((by, ty))
    out = []
    for (lx, rx), spans in by_x.items():
        spans.sort()
        cur = list(spans[0])
        for b, t in spans[1:]:
            if b <= cur[1] + EPS:
                cur[1] = max(cur[1], t)
            else:
                out.append((lx, cur[0], rx, cur[1]))
                cur = [b, t]
        out.append((lx, cur[0], rx, cur[1]))
    return out


def check_lef(path):
    size = None
    ports = {n: [] for n in POWER_NETS}
    other_layers = []
    pin = None
    layer = None
    in_port = False
    with _open(path) as f:
        for line in f:
            s = line.strip()
            m = re.match(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", s)
            if m and size is None:
                size = (float(m.group(1)), float(m.group(2)))
            m = re.match(r"PIN\s+(\S+)", s)
            if m:
                pin = m.group(1)
                continue
            if pin and s == "END " + pin:
                pin = None
                continue
            if pin in POWER_NETS:
                if s.startswith("PORT"):
                    in_port = True
                elif s == "END" and in_port:
                    in_port = False
                m = re.match(r"LAYER\s+(\S+)", s)
                if m and in_port:
                    layer = m.group(1)
                m = re.match(r"RECT\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)", s)
                if m and in_port:
                    r = tuple(float(m.group(i)) for i in range(1, 5))
                    if layer == LAYER:
                        ports[pin].append(r)
                    else:
                        other_layers.append((pin, layer) + r)
    if size is None:
        return {"check": "lef", "file": path, "ok": False, "error": "no SIZE"}
    w, h = size
    bad = []
    per_net = {}
    for n in POWER_NETS:
        merged = _merge_vertical(ports[n])
        nb = 0
        for lx, by, rx, ty in merged:
            why = []
            if rx - lx < MIN_WIDTH - EPS:
                why.append("width %.3f" % (rx - lx))
            if by > EDGE + EPS:
                why.append("bottom gap %.2f" % by)
            if h - ty > EDGE + EPS:
                why.append("top gap %.2f" % (h - ty))
            if lx < -EPS or rx > w + EPS or by < -EPS or ty > h + EPS:
                why.append("outside the block")
            if why:
                nb += 1
                bad.append("%s x %.3f..%.3f y %.3f..%.3f: %s" % (n, lx, rx, by, ty, ", ".join(why)))
        per_net[n] = {"ports": len(merged), "bad": nb}
    missing = [n for n in POWER_NETS if not ports[n]]
    return {"check": "lef", "file": path, "size": size, "per_net": per_net, "port_violations": len(bad),
            "wrong_layer_ports": len(other_layers), "missing_nets": missing, "examples": bad[:8],
            "ok": not bad and not other_layers and not missing}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kind", choices=("def", "lef"))
    ap.add_argument("path")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = check_def(a.path) if a.kind == "def" else check_lef(a.path)
    text = json.dumps(res, indent=2)
    if a.out:
        with open(a.out + ".tmp", "w") as f:
            f.write(text + "\n")
        import os
        os.replace(a.out + ".tmp", a.out)
    print(text)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
