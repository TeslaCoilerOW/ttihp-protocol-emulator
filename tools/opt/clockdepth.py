#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Clock-path depth of a final netlist: the number of cells between the `clk`
port and every flip-flop CLK pin and every SRAM macro A_CLK pin
(docs/notes/optimization.md, "Clock depth warning").

In a zero-delay gate-level simulation (the Tiny Tapeout gl_test and
tools/opt/gl_job.sh) every clock buffer costs at least one evaluation step, so
an SRAM macro whose A_CLK is many cells deeper than the clock of a flip-flop
that drives its inputs can sample the value that flip-flop launched at the same
edge: a simulation race that real timing (STA hold checks) does not have. This
is what failed promotion p026's gate-level tests. The check here is the cheap
part of that analysis: `sram_excess` = deepest SRAM A_CLK minus shallowest
flip-flop CLK. It is a warning, not a gate: a large excess is a risk (p025 had
the same depths as p026 and passed), and the gate-level tests decide.

  clockdepth.py NETLIST[.gz]      prints the summary as JSON

Standard library only; Python 3.6+.
"""

import gzip
import json
import re
import sys

# sram_excess of the final netlists of promotions p001-p027 (26 with a netlist): 1 to 5 for
# the 24 with CTS_MAX_SLEW unset, 7 for p025 and p026, the two with CTS_MAX_SLEW set
# (docs/notes/optimization.md, "Clock depth warning").
WARN_EXCESS = 6

OUT_PINS = frozenset(("X", "Y", "Q", "Q_N", "L_HI", "L_LO", "A_DOUT", "Z", "ZN", "GCLK"))
_INST = re.compile(r"^\s*([A-Za-z_][\w$]*)\s+(\\\S+|[A-Za-z_][\w$\[\]]*)\s*\((.*?)\);", re.S | re.M)
_PIN = re.compile(r"\.(\w+)\s*\(((?:[^()]|\([^()]*\))*)\)", re.S)
_SKIP = frozenset(("module", "input", "output", "inout", "wire", "assign", "reg", "endmodule"))
_FILLER = re.compile(r"sg13cmos5l_(fill|decap|antenna|filltap)")


def _nets(expr):
    expr = expr.strip()
    if expr.startswith("{"):
        parts, depth, cur = [], 0, []
        for ch in expr[1:-1]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        if cur:
            parts.append("".join(cur))
        return [p.replace(" ", "").strip() for p in parts]
    return [expr.replace(" ", "")]


def parse(text):
    """Flat netlist text -> {instance: (cell, {pin: [nets]})} (fill/decap/antenna cells skipped)."""
    insts = {}
    for m in _INST.finditer(text):
        cell, name, body = m.group(1), m.group(2), m.group(3)
        if cell in _SKIP or _FILLER.match(cell):
            continue
        insts[name] = (cell, {p.group(1): _nets(p.group(2)) for p in _PIN.finditer(body)})
    return insts


def read(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def depths(insts):
    """-> (flip-flop clock depths, SRAM clock depths, notes). A depth is the number of cells
    walked back from the sink's clock pin through single-input cells until a net without a
    driver (the clk port). A multi-input cell on the way (a clock gate) ends the walk and is
    listed in notes."""
    drv = {}
    for name, (cell, pins) in insts.items():
        for p, nets in pins.items():
            if p in OUT_PINS:
                for n in nets:
                    drv[n] = name
    memo = {}
    notes = set()

    def depth(net):
        path = []
        seen = set()
        while net in drv and net not in seen and net not in memo:
            seen.add(net)
            path.append(net)
            cell, pins = insts[drv[net]]
            ins = [p for p in pins if p not in OUT_PINS]
            if len(ins) != 1:
                notes.add("multi-input cell %s (%s) on a clock path" % (drv[net], cell))
                break
            net = pins[ins[0]][0]
        base = memo.get(net, 0)
        for i, n in enumerate(reversed(path)):
            memo[n] = base + i + 1
        return base + len(path)

    ff, sram = [], []
    for name, (cell, pins) in insts.items():
        if cell.startswith("RM_IHPSG13") and "A_CLK" in pins:
            sram.append(depth(pins["A_CLK"][0]))
        elif "CLK" in pins and "D" in pins:
            ff.append(depth(pins["CLK"][0]))
    return ff, sram, sorted(notes)


def summary(path):
    """Clock-depth summary of a netlist file (plain or .gz)."""
    ff, sram, notes = depths(parse(read(path)))
    out = {"n_ff": len(ff), "n_sram": len(sram), "notes": notes[:5]}
    if ff:
        out["ff_min"], out["ff_max"] = min(ff), max(ff)
    if sram:
        out["sram_min"], out["sram_max"] = min(sram), max(sram)
    if ff and sram:
        out["sram_excess"] = max(sram) - min(ff)
        out["warn"] = out["sram_excess"] >= WARN_EXCESS
    return out


def text(m):
    """Leaderboard cell from flat metrics (objective.flatten keys)."""
    if m.get("clk_ff_min") is None or m.get("clk_sram_max") is None:
        return "-"
    return "%s-%s / %s-%s%s" % (m["clk_ff_min"], m["clk_ff_max"], m["clk_sram_min"], m["clk_sram_max"],
                                " WARN +%s" % m["clk_sram_excess"] if m.get("clk_warn") else "")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps(summary(sys.argv[1]), indent=1))
