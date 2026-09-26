#!/usr/bin/env python3
"""Map every statement of the generated core to a functional region.

usage: region_map.py CORE.v BASE.il OUT_DIR

The Hardcaml netlist is flat and most nets are anonymous (_1234), so a
statement's region is taken from the state it feeds: a forward breadth-first
walk from the statement's left-hand side through combinational logic and
anonymous registers, stopping at the nearest *anchors*. Anchors are named
registers, the eight FIFO memories, the eight instruction-SRAM instances and
the module outputs. The region is the most common anchor category at the
minimum distance; when the nearest anchors span three or more categories the
statement is labelled "shared" (reset, window decode and similar fan-out
logic). In cores whose queues are registers rather than memories (design
variants with fifo_storage_reset), the queue-word registers take the place of
the FIFO memories (register_fifo_words).

Writes:
  OUT_DIR/regions.json         {line: region} for every statement line, plus stats
  OUT_DIR/cells.tsv            RTLIL cell name, type, width, first src line, region
  OUT_DIR/sel/<region>.txt     yosys `select -read` files, one per region
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

CATEGORIES = {
    "engine_ctrl": ["pc", "running", "fault_code", "wait_timer", "repeat_count", "wait_limit",
                    "blocked_cycles", "completed_instructions"],
    "engine_xfer": ["transfer_pins", "transfer_mode", "transfer_period", "transfer_tick", "transfer_edges"],
    "engine_data": ["x", "y", "tx", "rx"],
    "pins": ["logical_output", "logical_enable", "open_drain", "ownership", "pin_trigger", "uio_out", "uio_oe"],
    "host": ["image_valid", "image_writing", "image_loaded", "image_length", "host_read_select",
             "host_selected_engine", "host_fault", "uo_out"],
    "mover": ["route_remaining", "route_destination", "dma_round_robin"],
    "events": ["mailbox"],
    "timestamp": ["timestamp"],
}
NAME2CAT = {n: c for c, names in CATEGORIES.items() for n in names}
# tie-break order when two categories are equally common at the minimum distance
PRIORITY = ["engine_ctrl", "host", "mover", "events", "pins", "engine_xfer", "imem", "fifo",
            "engine_data", "timestamp"]
KEYWORDS = {"begin", "end", "if", "else", "case", "endcase", "default", "always", "posedge", "negedge",
            "assign", "or", "and", "not"}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
LITERAL = re.compile(r"\d*'[sS]?[bBhHdDoO][0-9a-fA-FxXzZ_]+")
LHS_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?\s*<?=(?!=)")


def idents(text: str) -> set[str]:
    return {t for t in IDENT.findall(LITERAL.sub(" ", text)) if t not in KEYWORDS}


def category_of(name: str) -> str | None:
    if name in NAME2CAT:
        return NAME2CAT[name]
    base = re.sub(r"_\d+$", "", name)
    if not name.startswith("_") and base in NAME2CAT:
        return NAME2CAT[base]
    return None


class Statement:
    def __init__(self, kind: str, first: int, last: int, text: str):
        self.kind, self.first, self.last, self.text = kind, first, last, text
        self.lhs: set[str] = set()
        self.rhs: set[str] = set()
        self.region = "unknown"
        self.anchors: list[str] = []


def parse(core: Path):
    lines = core.read_text().splitlines()
    stmts: list[Statement] = []
    regs, memories, seq_regs, outputs = set(), set(), set(), set()
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("output"):
            outputs.update(idents(s.split("]", 1)[-1] if "]" in s else s[6:]))
        if s.startswith("reg "):
            m = re.match(r"reg\s*(\[[^\]]*\])?\s*([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?\s*;", s)
            if m:
                (memories if m.group(3) else regs).add(m.group(2))
            i += 1
            continue
        if s.startswith("assign "):
            j = i
            text = s
            while not lines[j].rstrip().endswith(";"):
                j += 1
                text += " " + lines[j].strip()
            lhs, rhs = text[len("assign "):].split("=", 1)
            st = Statement("assign", i + 1, j + 1, text)
            st.lhs = idents(lhs.split("[")[0])
            st.rhs = idents(rhs)
            stmts.append(st)
            i = j + 1
            continue
        if s.startswith("always"):
            seq = "posedge" in s
            depth, j, body = 0, i, []
            while True:
                t = lines[j]
                depth += len(re.findall(r"\bbegin\b", t)) - len(re.findall(r"\bend\b", t))
                body.append(t.strip())
                if depth <= 0 and j > i:
                    break
                j += 1
            text = " ".join(body)
            st = Statement("seq" if seq else "comb", i + 1, j + 1, text)
            inner = " ".join(body[1:])
            for m in LHS_RE.finditer(inner):
                st.lhs.add(m.group(1))
            st.rhs = idents(inner) - st.lhs
            # a memory write's index is a read, the memory itself is the target
            for m in LHS_RE.finditer(inner):
                if m.group(2):
                    st.rhs |= idents(m.group(2))
            if seq:
                seq_regs |= st.lhs
            stmts.append(st)
            i = j + 1
            continue
        if s == "RM_IHPSG13_1P_64x16_c2":
            # SRAM macro instance: TYPE / NAME / ( .P(x), ... );
            j = i
            text = s
            while not lines[j].rstrip().endswith(";"):
                j += 1
                text += " " + lines[j].strip()
            inst = lines[i + 1].strip()
            st = Statement("inst", i + 1, j + 1, text)
            for port, expr in re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(([^()]*)\)", text):
                if port == "A_DOUT":
                    st.lhs |= idents(expr)
                else:
                    st.rhs |= idents(expr)
            st.lhs.add("inst:" + inst)
            stmts.append(st)
            i = j + 1
            continue
        i += 1
    return lines, stmts, regs, memories, seq_regs, outputs


ENABLED_LOAD = re.compile(r"\belse\s+if\s*\(\s*(\w+)\s*\)\s*(\w+)\s*<=\s*(\w+)\s*;\s*end\s*$")


def register_fifo_words(stmts) -> set[str]:
    """FIFO storage built from registers (variants with fifo_storage_reset: cn, cn_s2, diet*).

    Those cores have no memory arrays: every queue word is an anonymous register
    loaded by ``if (reset) r <= 0; else if (put & wr == j) r <= data`` (hardcaml/lib/fifo.ml).
    The words of one queue load the same data signal under different enables, so
    a group of two or more anonymous registers, each alone in a clocked block that
    ends in such an enabled load of one common signal, is taken as one queue's
    storage. Only used when the core has no memory arrays, so the design of record
    maps exactly as before.
    """
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for st in stmts:
        if st.kind != "seq" or len(st.lhs) != 1:
            continue
        (target,) = st.lhs
        m = ENABLED_LOAD.search(st.text)
        if target.startswith("_") and m and m.group(2) == target:
            groups[m.group(3)].append(target)
    return {r for words in groups.values() if len(words) >= 2 for r in words}


def classify(stmts, memories, seq_regs, outputs):
    consumers: dict[str, set[str]] = collections.defaultdict(set)
    for st in stmts:
        if st.kind == "inst":
            inst = next(x for x in st.lhs if x.startswith("inst:"))
            for r in st.rhs:
                consumers[r].add(inst)
            for o in st.lhs - {inst}:
                consumers[inst].add(o)
        else:
            for r in st.rhs:
                consumers[r] |= st.lhs

    def anchor_cat(sig: str) -> str | None:
        if sig.startswith("inst:"):
            return "imem"
        if sig in memories:
            return "fifo"
        if sig in outputs:
            return category_of(sig)
        if sig in seq_regs:
            return category_of(sig)
        return None

    for st in stmts:
        start = {x for x in st.lhs if not x.startswith("inst:")} if st.kind == "inst" else set(st.lhs)
        if st.kind == "inst":
            st.region, st.anchors = "imem", [x for x in st.lhs if x.startswith("inst:")]
            continue
        direct = [(x, anchor_cat(x)) for x in start if anchor_cat(x)]
        if direct:
            cats = collections.Counter(c for _, c in direct)
            st.region = sorted(cats, key=lambda c: (-cats[c], PRIORITY.index(c)))[0]
            st.anchors = sorted(x for x, _ in direct)
            continue
        seen = set(start)
        frontier = set(start)
        depth = 0
        while frontier and depth < 64:
            depth += 1
            nxt = set()
            for sig in frontier:
                for c in consumers.get(sig, ()):
                    if c not in seen:
                        seen.add(c)
                        nxt.add(c)
            hits = [(x, anchor_cat(x)) for x in nxt if anchor_cat(x)]
            if hits:
                cats = collections.Counter(c for _, c in hits)
                if len(cats) >= 3:
                    st.region = "shared"
                else:
                    st.region = sorted(cats, key=lambda c: (-cats[c], PRIORITY.index(c)))[0]
                st.anchors = sorted(x for x, _ in hits)[:12]
                break
            frontier = nxt


def parse_il(il: Path):
    cells = []
    src = None
    cur = None
    for line in il.read_text().splitlines():
        s = line.strip()
        if s.startswith("attribute \\src "):
            src = s.split(" ", 2)[2].strip('"')
        elif s.startswith("cell "):
            _, ctype, name = s.split(" ", 2)
            cur = {"name": name, "type": ctype, "src": src, "width": None, "conns": {}}
            cells.append(cur)
            src = None
        elif s.startswith("parameter ") and cur is not None:
            parts = s.split()
            if parts[1] in ("\\WIDTH", "\\Y_WIDTH") and cur["width"] is None:
                try:
                    cur["width"] = int(parts[2])
                except ValueError:
                    pass
        elif s.startswith("connect ") and cur is not None:
            _, port, sig = s.split(" ", 2)
            cur["conns"][port] = sig
        elif s == "end":
            cur = None
        elif s.startswith("wire ") or s.startswith("memory ") or s.startswith("connect "):
            if cur is None:
                src = None
    return cells


def main() -> None:
    core, il, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    lines, stmts, regs, memories, seq_regs, outputs = parse(core)
    fifo_registers: set[str] = set()
    if not memories:  # register-based queues (see register_fifo_words)
        fifo_registers = register_fifo_words(stmts)
        memories = memories | fifo_registers
    classify(stmts, memories, seq_regs, outputs)
    line_region: dict[int, str] = {}
    line_stmt: dict[int, Statement] = {}
    for st in stmts:
        for n in range(st.first, st.last + 1):
            line_region[n] = st.region
            line_stmt[n] = st
    cells = parse_il(il)
    by_region: dict[str, list[str]] = collections.defaultdict(list)
    rows = []
    for c in cells:
        region, line = "unknown", None
        if c["src"]:
            for part in c["src"].split("|"):
                m = re.search(r":(\d+)\.\d+", part)
                if m and int(m.group(1)) in line_region:
                    line = int(m.group(1))
                    region = line_region[line]
                    break
        if region == "unknown" and c["type"] == "$mem_v2":
            region = "fifo"  # the FIFO storage arrays themselves
        c["region"], c["line"] = region, line
    # helper cells created by `prep` without a src tag (opt_pmux's $reduce_or on
    # $pmux selects) take the region of the cell that consumes their output
    for c in cells:
        if c["region"] != "unknown" or "\\Y" not in c["conns"]:
            continue
        y = c["conns"]["\\Y"]
        for d in cells:
            if d is not c and d["region"] != "unknown" and any(
                    y in v.split() or y == v for p, v in d["conns"].items() if p != "\\Y"):
                c["region"], c["inherited"] = d["region"], d["name"]
                break
    for c in cells:
        region, line = c["region"], c["line"]
        by_region[region].append(c["name"])
        rows.append(f'{c["name"]}\t{c["type"]}\t{c["width"]}\t{line}\t{region}')
    (out / "sel").mkdir(parents=True, exist_ok=True)
    for region, names in by_region.items():
        (out / "sel" / f"{region}.txt").write_text(
            "".join(f"protocol_emulator_core/{n}\n" for n in names))
    (out / "cells.tsv").write_text("name\ttype\twidth\tline\tregion\n" + "\n".join(rows) + "\n")
    stmt_regions = collections.Counter(st.region for st in stmts)
    cell_regions = collections.Counter(c["region"] for c in cells)
    (out / "regions.json").write_text(json.dumps({
        "statements": len(stmts),
        "statement_regions": dict(stmt_regions.most_common()),
        "cells": len(cells),
        "cell_regions": dict(cell_regions.most_common()),
        **({"fifo_storage_registers": sorted(fifo_registers, key=lambda r: int(r[1:]) if r[1:].isdigit() else 0)}
           if fifo_registers else {}),
        "line_region": {str(k): v for k, v in sorted(line_region.items())},
        "line_anchor": {str(k): line_stmt[k].anchors[:4] for k in sorted(line_stmt)
                        if k == line_stmt[k].first},
    }, indent=0))
    print("statements", len(stmts), dict(stmt_regions.most_common()))
    print("cells", len(cells), dict(cell_regions.most_common()))


if __name__ == "__main__":
    main()
