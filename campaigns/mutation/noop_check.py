#!/usr/bin/env python3
"""Find const0/const1 mutants that tie a cell input bit to the constant it already has.

usage: noop_check.py DESIGN_DIR  -> prints JSON {"noop_ids": [...], "count": N}

Parses base.il (the design every mutant is built from). A mutation is a
static no-op when its mode is const0/const1, the port is a cell input, and the
bit at -portbit is that same constant in the cell's connection.
"""
import json
import re
import sys
from pathlib import Path

OUTPUT_PORTS = {"\\Y", "\\Q"}


def bits(sig: str, widths: dict) -> list:
    """Expand an RTLIL SigSpec into bits, LSB first; constants become '0'/'1'/'x'."""
    sig = sig.strip()
    if sig.startswith("{"):
        tokens = []
        for t in sig[1:-1].split():
            if t.startswith("[") and tokens:
                tokens[-1] += " " + t  # index belongs to the preceding name
            else:
                tokens.append(t)
        out = []
        for t in reversed(tokens):  # concatenation is MSB first
            out += bits(t, widths)
        return out
    m = re.fullmatch(r"(\d+)'([01xz]+)", sig)
    if m:
        return list(reversed(m.group(2)))
    m = re.fullmatch(r"(\S+) \[(\d+)(?::(\d+))?\]", sig)
    if m:
        hi = int(m.group(2)); lo = int(m.group(3)) if m.group(3) else hi
        return [f"{m.group(1)}[{i}]" for i in range(lo, hi + 1)]
    return [f"{sig}[{i}]" for i in range(widths.get(sig, 1))]


def main() -> None:
    design = Path(sys.argv[1])
    widths, conns, cur = {}, {}, None
    for line in (design / "base.il").read_text().splitlines():
        s = line.strip()
        if s.startswith("wire "):
            t = s.split()
            w = int(t[t.index("width") + 1]) if "width" in t else 1
            widths[t[-1]] = w
        elif s.startswith("cell "):
            cur = s.split(" ", 2)[2]
            conns[cur] = {}
        elif s.startswith("connect ") and cur is not None:
            _, port, sig = s.split(" ", 2)
            conns[cur][port] = sig
        elif s == "end":
            cur = None
    noop = []
    for line in (design / "mutations.tsv").read_text().splitlines():
        mid, _, cmd = line.split("\t", 2)
        o = cmd.split()
        mode = o[o.index("-mode") + 1]
        if mode not in ("const0", "const1"):
            continue
        cell, port, pb = o[o.index("-cell") + 1], "\\" + o[o.index("-port") + 1], int(o[o.index("-portbit") + 1])
        if port in OUTPUT_PORTS or cell not in conns or port not in conns[cell]:
            continue
        b = bits(conns[cell][port], widths)
        if pb < len(b) and b[pb] == mode[-1]:
            noop.append(mid)
    print(json.dumps({"count": len(noop), "noop_ids": noop}))


if __name__ == "__main__":
    main()
