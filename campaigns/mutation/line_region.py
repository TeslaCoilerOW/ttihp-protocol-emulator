#!/usr/bin/env python3
"""Locate the line-unit logic of a core structurally and write its mutation selection.

usage: line_region.py CORE.v BASE.il OUT_DIR [REFERENCE.v]

CORE.v       a core generated with the line unit (docs/extension.md), e.g. diet8_rec16
BASE.il      CORE.v after `prep` (gen_line_mutants.sh)
OUT_DIR      receives line_region.json, cells.tsv, regions.json, sel/line.txt and,
             with REFERENCE.v, sel/ext.txt and sel/ext-downstream.txt
REFERENCE.v  optional: the same variant generated without the unit (diet8 for
             diet8_rec16), for the added-or-changed comparison and the second
             population below

First population (sel/line.txt). The line unit's registers are the 19 of
Engine.line_register_names (line_*, stuff_*, arbitration_lost, crc_*; four
copies each in a four-engine core). region_map.py assigns every statement of the flat netlist to the named
state nearest in its forward cone; with these registers as an extra category
"line" (first in the tie-break order) the statements that compute line-unit
state are those of region "line" (sub-region "state"). The unit's outputs are
the first statements outside that region on every path out of it: starting
from the line-unit registers and the "state" statements whose value depends on
them in the same cycle, the combinational statements that read one of them;
pure wiring (bit selects, concatenations, renames; no RTLIL cell) is followed
through to the first statement that makes a cell (sub-region "out-<region>",
where <region> is region_map's region of the statement, e.g. out-pins for the
pin composition, out-engine_data for the multiplexer that loads the LSTAT word).
Clocked blocks of other registers are not selected. The selection is the
union of both; every RTLIL cell whose first src line lies in a selected
statement is selected, and prep's helper cells without a src tag follow the
cell that consumes them (as in region_map.py).

Added or changed (REFERENCE.v). The Hardcaml wire names (_1234) are not
stable between two generations, so statements are compared by function: a
named register is keyed by its name without the engine suffix (pc_2 -> pc;
engines are not identified by name, see formal/gen/generate_fv.ml), an
anonymous register by its width, an input or SRAM output by its port, and a
combinational wire by its defining statement's text with every operand replaced
by the operand's key (recursively). A statement of CORE.v whose key occurs
nowhere in REFERENCE.v was added or changed by the option; a change propagates
to every statement with a changed input.

Second population (sel/ext.txt; cells.tsv column "ext"). Combinational
statements outside the first population of two kinds: "tie", a line-unit
register is among the statement's nearest named states (the full list of
region_map.classify's walk) but the majority/tie-break rule gave it another
region; "no-line-input", added or changed with no line-unit register in its
same-cycle fan-in (the decode of the new opcodes, the encoding checks and the
next-value selections they feed). Counted, not selected: "downstream" (added
or changed, reads line-unit state in the same cycle, beyond the first
cell-producing statement of its path; sel/ext-downstream.txt), and the
clocked blocks and SRAM instances whose inputs changed. line_region.json holds
the counts ("changed_not_selected", "ext_*", "selected_not_changed_by_shape").
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import region_map  # noqa: E402

LINE_REGISTERS = ["line_run", "line_phase", "line_frac", "line_acc", "line_boundary_seen", "line_cfg",
                  "line_level", "line_rx_prev", "line_cell_bit", "line_man_pending", "line_se0",
                  "line_remaining", "line_trailing_stuff", "stuff_run", "stuff_last", "stuff_error",
                  "arbitration_lost", "crc_state", "crc_preset"]
TOKEN = re.compile(r"(\d*'[sS]?[bBhHdDoO][0-9a-fA-FxXzZ_]+)|([A-Za-z_][A-Za-z0-9_]*)")
KEEP = region_map.KEYWORDS | {"posedge", "negedge", "clk"}
# an assign that only selects, concatenates or renames bits (no RTLIL cell)
WIRING = re.compile(r"^assign\s+\w+\s*=\s*[{}\s\w\[\]:,']*;$")
CONSTANT = re.compile(r"^assign\s+\w+\s*=\s*\d+'[bBhHdDoO][0-9a-fA-F_]+;$")
COMPARE = re.compile(r"==|!=|<|>")
# the kinds of the second population that are selected (sel/ext.txt); "downstream" is only counted
EXT_SELECTED = ("tie", "no-line-input")


def shape(st: region_map.Statement) -> str:
    """constant / wiring (bit select, concatenation, rename) / compare / other"""
    if CONSTANT.match(st.text):
        return "constant"
    if WIRING.match(st.text):
        return "wiring"
    if st.kind == "assign" and COMPARE.search(st.text.split("=", 1)[1]):
        return "compare"
    return "other"


def declarations(core: Path) -> tuple[dict[str, int], set[str]]:
    widths, inputs = {}, set()
    for line in core.read_text().splitlines():
        m = re.match(r"(input|output|reg|wire)\s*(\[(\d+):(\d+)\])?\s*([A-Za-z_][A-Za-z0-9_]*)", line.strip())
        if m:
            widths[m.group(5)] = int(m.group(3)) - int(m.group(4)) + 1 if m.group(2) else 1
            if m.group(1) == "input":
                inputs.add(m.group(5))
    return widths, inputs


class Keys:
    """Structural keys of one core's signals and statements (added-or-changed statistic)."""

    def __init__(self, core: Path):
        _, self.stmts, _, self.memories, self.seq_regs, _ = region_map.parse(core)
        self.widths, self.inputs = declarations(core)
        self.define: dict[str, region_map.Statement] = {}
        self.sram_port: dict[str, str] = {}
        for st in self.stmts:
            if st.kind in ("assign", "comb"):
                for x in st.lhs:
                    self.define[x] = st
            elif st.kind == "inst":
                inst = next(x for x in st.lhs if x.startswith("inst:"))
                for x in st.lhs - {inst}:
                    self.sram_port[x] = "sram_dout_" + ("hi" if inst.endswith("hi") else "lo")
        self.memo: dict[str, str] = {}

    def leaf(self, sig: str) -> str | None:
        if sig in self.seq_regs:
            return f"anonreg/{self.widths.get(sig)}" if sig.startswith("_") else "reg/" + re.sub(r"_\d+$", "", sig)
        if sig in self.memories:
            return "mem"
        if sig in self.inputs:
            return "in/" + sig
        if sig in self.sram_port:
            return self.sram_port[sig]
        if sig not in self.define:
            return "undef/" + sig
        return None

    def canon(self, st: region_map.Statement) -> str:
        """The statement text with keys for its operands; a clocked block keeps its register's
        key, a combinational statement's target becomes LHS."""
        def sub(m):
            if m.group(1):
                return m.group(1)
            name = m.group(2)
            if name in KEEP:
                return name
            if name in st.lhs:
                return "{" + self.leaf(name) + "}" if st.kind == "seq" else "LHS"
            return "{" + self.key(name) + "}"
        text = st.text
        if st.kind == "inst":  # the instance name carries the engine index
            text = re.sub(r"instruction_sram_e\d+_", "instruction_sram_", text)
        return TOKEN.sub(sub, text)

    def key(self, sig: str) -> str:
        stack = [sig]
        while stack:  # iterative post-order: the combinational chains are deep
            s = stack[-1]
            if s in self.memo:
                stack.pop()
                continue
            leaf = self.leaf(s)
            if leaf is not None:
                self.memo[s] = leaf
                stack.pop()
                continue
            st = self.define[s]
            pending = [r for r in st.rhs if r not in self.memo and r not in st.lhs]
            if pending:
                stack.extend(pending)
                continue
            self.memo[s] = hashlib.sha1(self.canon(st).encode()).hexdigest()[:20]
            stack.pop()
        return self.memo[sig]

    def statement_key(self, st: region_map.Statement) -> str:
        for r in st.rhs:
            if r not in st.lhs:
                self.key(r)
        return hashlib.sha1((st.kind + ":" + self.canon(st)).encode()).hexdigest()[:20]


def nearest_anchor_sets(stmts, memories, seq_regs, outputs, todo) -> dict[int, set[str]]:
    """All nearest anchors of the statements `todo` (region_map.classify's forward walk,
    without its cut of the anchor list to 12 names)."""
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

    def is_anchor(sig: str) -> bool:
        return sig.startswith("inst:") or sig in memories or sig in outputs or sig in seq_regs

    out: dict[int, set[str]] = {}
    for i in todo:
        st = stmts[i]
        if st.kind == "inst":
            continue
        start = set(st.lhs)
        direct = {x for x in start if is_anchor(x)}
        if direct:
            out[i] = direct
            continue
        seen, frontier, depth = set(start), set(start), 0
        while frontier and depth < 64:
            depth += 1
            nxt = set()
            for sig in frontier:
                for c in consumers.get(sig, ()):
                    if c not in seen:
                        seen.add(c)
                        nxt.add(c)
            hits = {x for x in nxt if is_anchor(x)}
            if hits:
                out[i] = hits
                break
            frontier = nxt
    return out


def main() -> None:
    core, il, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    ref = Path(sys.argv[4]) if len(sys.argv) > 4 else None
    region_map.CATEGORIES["line"] = LINE_REGISTERS
    region_map.NAME2CAT.update({n: "line" for n in LINE_REGISTERS})
    if "line" not in region_map.PRIORITY:
        region_map.PRIORITY.insert(0, "line")
    _, stmts, _, memories, seq_regs, outputs = region_map.parse(core)
    if not memories:
        memories = memories | region_map.register_fifo_words(stmts)
    region_map.classify(stmts, memories, seq_regs, outputs)
    line_regs = {r for r in seq_regs if region_map.category_of(r) == "line"}
    readers: dict[str, list[int]] = collections.defaultdict(list)
    for i, st in enumerate(stmts):
        for r in st.rhs:
            readers[r].append(i)
    comb = {i for i, st in enumerate(stmts) if st.kind in ("assign", "comb")}
    # statements whose value depends combinationally on line-unit state (same cycle)
    dep, frontier = set(), list(line_regs)
    while frontier:
        sig = frontier.pop()
        for i in readers[sig]:
            if i in comb and i not in dep:
                dep.add(i)
                frontier.extend(stmts[i].lhs)
    label: dict[int, str] = {i: "state" for i, st in enumerate(stmts) if st.region == "line"}
    # the unit's outputs: from the line-unit registers and the "state" statements that depend
    # on them, the first cell-producing combinational statement outside "state" on each path;
    # pure wiring (bit selects, concatenations, renames) produces no cell and is followed through
    frontier = list(line_regs) + [x for i in label if i in dep for x in stmts[i].lhs]
    seen = set(frontier)
    while frontier:
        sig = frontier.pop()
        for i in readers[sig]:
            if i not in comb or i in label:
                continue
            label[i] = "out-" + stmts[i].region
            if WIRING.match(stmts[i].text):
                for x in stmts[i].lhs - seen:
                    seen.add(x)
                    frontier.append(x)
    changed: set[int] = set()
    if ref is not None:
        k_core, k_ref = Keys(core), Keys(ref)
        ref_keys = {k_ref.statement_key(st) for st in k_ref.stmts}
        for i, (a, b) in enumerate(zip(k_core.stmts, stmts)):
            assert (a.first, a.last) == (b.first, b.last)
            if k_core.statement_key(a) not in ref_keys:
                changed.add(i)
    # second population (option logic outside the first selection; needs REFERENCE.v):
    #   tie            a line-unit register is among the statement's nearest named states, but
    #                  region_map's majority / tie-break rule gave it another region
    #   no-line-input  added or changed, and no line-unit register in its same-cycle fan-in
    #                  (decode of the new opcodes, their encoding checks, and what they feed)
    #   downstream     added or changed, reads line-unit state in the same cycle, and lies
    #                  beyond the first cell-producing statement on its path (not selected)
    # only combinational statements; `root` marks a changed statement none of whose
    # combinational inputs is changed (the change starts there)
    ext: dict[int, str] = {}
    roots: set[int] = set()
    if ref is not None:
        define = {x: i for i in comb for x in stmts[i].lhs}
        near = nearest_anchor_sets(stmts, memories, seq_regs, outputs,
                                   [i for i in comb if i not in label])
        for i, names in near.items():
            if names & line_regs:
                ext[i] = "tie"
        for i in (changed & comb) - set(label) - set(ext):
            ext[i] = "downstream" if i in dep else "no-line-input"
        for i in changed & comb:
            if not {define[r] for r in stmts[i].rhs if r in define and r not in stmts[i].lhs} & changed:
                roots.add(i)
    line_of: dict[int, int] = {}
    for i, st in enumerate(stmts):
        for n in range(st.first, st.last + 1):
            line_of[n] = i
    cells = region_map.parse_il(il)
    for c in cells:
        c["sub"], c["ext"], c["line"] = "-", "-", None
        for part in (c["src"] or "").split("|"):
            m = re.search(r":(\d+)\.\d+", part)
            if m and int(m.group(1)) in line_of:
                c["line"] = int(m.group(1))
                c["sub"] = label.get(line_of[c["line"]], "-")
                c["ext"] = ext.get(line_of[c["line"]], "-")
                break
    for c in cells:  # prep helper cells without a src tag follow their consumer
        if c["src"] or "\\Y" not in c["conns"]:
            continue
        y = c["conns"]["\\Y"]
        for d in cells:
            if d is not c and d["src"] and any(y in v.split() or y == v
                                               for p, v in d["conns"].items() if p != "\\Y"):
                c["sub"], c["ext"], c["inherited"] = d["sub"], d["ext"], d["name"]
                break
    chosen = [c for c in cells if c["sub"] != "-"]
    (out / "sel").mkdir(parents=True, exist_ok=True)
    (out / "sel" / "line.txt").write_text("".join(f"protocol_emulator_core/{c['name']}\n" for c in chosen))
    for kinds, name in (EXT_SELECTED, "ext.txt"), (("downstream",), "ext-downstream.txt"):
        (out / "sel" / name).write_text("".join(f"protocol_emulator_core/{c['name']}\n"
                                                for c in cells if c["ext"] in kinds and c["sub"] == "-"))
    # names as `mutate -list` prints them (no RTLIL escape backslash)
    (out / "cells.tsv").write_text("name\ttype\twidth\tline\tsubregion\text\n" + "".join(
        f"{c['name'].lstrip(chr(92))}\t{c['type']}\t{c['width']}\t{c['line']}\t{c['sub']}\t{c['ext']}\n"
        for c in cells))
    sub_stmts = collections.Counter(label.values())
    summary = {
        "core": core.name,
        "statements": len(stmts),
        "statements_by_region": dict(collections.Counter(st.region for st in stmts).most_common()),
        "line_registers": len(line_regs),
        "selected_statements": len(label),
        "selected_statements_by_subregion": dict(sub_stmts.most_common()),
        "cells": len(cells),
        "selected_cells": len(chosen),
        "selected_cells_by_subregion": dict(collections.Counter(c["sub"] for c in chosen).most_common()),
        "selected_cells_by_type": dict(collections.Counter(c["type"] for c in chosen).most_common()),
    }
    if ref is not None:
        summary["reference"] = ref.name
        summary["changed_statements"] = len(changed)
        summary["changed_selected"] = len(changed & set(label))
        summary["selected_not_changed"] = dict(collections.Counter(
            label[i] for i in set(label) - changed).most_common())
        summary["changed_not_selected_by_region"] = dict(collections.Counter(
            stmts[i].region for i in changed - set(label)).most_common())
        summary["selected_not_changed_by_shape"] = dict(collections.Counter(
            shape(stmts[i]) for i in set(label) - changed).most_common())
        unsel = changed - set(label)
        summary["changed_not_selected"] = dict(collections.Counter(
            "ext/" + ext[i] if i in ext else stmts[i].kind for i in unsel).most_common())
        summary["ext_statements"] = dict(collections.Counter(ext.values()).most_common())
        summary["ext_statements_changed"] = dict(collections.Counter(
            ext[i] for i in ext if i in changed).most_common())
        summary["ext_statements_root"] = dict(collections.Counter(
            ext[i] for i in ext if i in roots).most_common())
        summary["ext_statements_by_region"] = {
            k: dict(collections.Counter(stmts[i].region for i in ext if ext[i] == k).most_common())
            for k in ("tie", "no-line-input", "downstream")}
        summary["ext_cells"] = dict(collections.Counter(
            c["ext"] for c in cells if c["ext"] != "-" and c["sub"] == "-").most_common())
        summary["ext_selected_kinds"] = list(EXT_SELECTED)
    (out / "line_region.json").write_text(json.dumps(summary, indent=1) + "\n")
    # region_map-compatible regions.json (describe.py reads line_region and line_anchor)
    (out / "regions.json").write_text(json.dumps({
        "statements": len(stmts),
        "line_region": {str(n): "line/" + label[i] if i in label else
                        "ext/" + ext[i] if i in ext else stmts[i].region
                        for n, i in sorted(line_of.items())},
        "line_anchor": {str(n): stmts[i].anchors[:4] for n, i in sorted(line_of.items())
                        if n == stmts[i].first},
    }, indent=0))
    print(json.dumps({k: v for k, v in summary.items() if k != "selected_cells_by_type"}, indent=1))


if __name__ == "__main__":
    main()
