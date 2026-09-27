#!/usr/bin/env python3
"""Specs for equiv_inv.py: gold invariants plus, per mutant, the liveness condition of the
mutated register family (plain register names of the round-trip core).

usage: einv_specs.py DESCRIBE_TXT ENGINE_MAP_JSON OUT_JSON
           [--invariants-only] [--fifo-counts a,b,...] [--fifo-depth N] [--core CORE.v]

DESCRIBE_TXT     describe.py output for the mutants (unused with --invariants-only; pass -)
ENGINE_MAP_JSON  engine_map.py output for the core ({register: engine})
--invariants-only  write only the "default" entry (the invariant library; no liveness
                 masking). docs/mutation-push.md section 4.3 used this form.
--fifo-counts    the queue count registers (anonymous in the round-trip netlist); the
                 default is the design of record's eight 4-bit counts
--fifo-depth     queue depth (default 8); the invariant is count <= depth
--core           find the queue counts in this round-trip netlist instead: the anonymous
                 registers whose only comparison with a constant is `== <depth>` (the full
                 test), e.g. the eight 3-bit counts of variant diet4 with --fifo-depth 4

The invariants (per engine e, register names mapped through ENGINE_MAP_JSON):
  blocked count is 0 unless the engine is not running, or a WAIT timer/transfer is idle
  and the fetched instruction is WAITPIN (13) or WAITEVENT (15);
  a running engine's two SRAM macros last read its PC (model registers la/lv of
  equiv_inv.py); a running engine has fault code 0 and a nonzero LIMIT;
  image length and loaded count are at most 64; queue counts are at most the depth.
"""
import argparse
import json
import re

SRAM_ORDER = ["e0_hi", "e0_lo", "e1_hi", "e1_lo", "e2_hi", "e2_lo", "e3_hi", "e3_lo"]
FIFO_COUNTS = ["_1172", "_1494", "_3040", "_3146", "_3252", "_3358", "_528", "_850"]
RUN_FAMILIES = ("transfer_pins", "x", "y", "tx", "logical_output", "logical_enable", "wait_timer", "wait_limit",
                "repeat_count")


def find_fifo_counts(core: str, depth: int) -> list[str]:
    text = open(core).read()
    regs = re.findall(r"^  reg \[(\d+):0\] (_\d+);$", text, re.M)
    out = []
    for hi, name in regs:
        cmps = re.findall(re.escape(name) + r" == \d+'h([0-9a-f]+)\b", text)
        if cmps and all(int(c, 16) == depth for c in cmps):
            out.append(name)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("describe")
    ap.add_argument("engine_map")
    ap.add_argument("out")
    ap.add_argument("--invariants-only", action="store_true")
    ap.add_argument("--fifo-counts")
    ap.add_argument("--fifo-depth", type=int, default=8)
    ap.add_argument("--core")
    a = ap.parse_args()
    emap = json.load(open(a.engine_map))
    if a.fifo_counts:
        fifo_counts = a.fifo_counts.split(",")
    elif a.core:
        fifo_counts = find_fifo_counts(a.core, a.fifo_depth)
    else:
        fifo_counts = FIFO_COUNTS
    by = {e: {} for e in range(4)}
    for name, e in emap.items():
        if isinstance(e, int):
            by[e][re.sub(r"_\d$", "", name)] = name
    inv, live = [], {}
    for e, r in by.items():
        hi, lo = SRAM_ORDER.index(f"e{e}_hi"), SRAM_ORDER.index(f"e{e}_lo")
        run = f"({r['running']} && {r['fault_code']} == 8'd0)"
        idle = f"({r['wait_timer']} == 24'd0 && {r['transfer_edges']} == 7'd0)"
        op = f"instr{e}[31:24]"
        inv.append(f"{r['blocked_cycles']} == 24'd0 || !{run} || ({idle} && ({op} == 8'd13 || {op} == 8'd15))")
        inv.append(f"!{r['running']} || (lv{hi} && lv{lo} && la{hi} == {r['pc']}[5:0] && la{lo} == {r['pc']}[5:0])")
        inv.append(f"!{r['running']} || {r['fault_code']} == 8'd0")
        inv.append(f"!{r['running']} || {r['wait_limit']} != 24'd0")
        live[e] = {"blocked_cycles": f"{run} && ({r['wait_timer']} != 24'd0 || ({idle} && ({op} == 8'd13 || {op} == 8'd15)))",
                   "transfer": f"{r['transfer_edges']} != 7'd0", "run": run}
    for k in range(4):
        inv.append(f"image_length_{k} <= 16'd64")
        inv.append(f"image_loaded_{k} <= 16'd64")
    width = a.fifo_depth.bit_length()
    for c in fifo_counts:
        inv.append(f"{c} <= {width}'d{a.fifo_depth}")
    specs = {"default": {"invariants": inv}}
    if not a.invariants_only:
        text = open(a.describe).read()
        for block in re.split(r"(?m)^== ", text)[1:]:
            mid = block.split()[1]
            near = re.findall(r"nearest named state: \[(.*?)\]", block)
            names = [n.strip(" '") for n in near[0].split(",")] if near else []
            dead = {}
            for n in names:
                if n not in emap or not isinstance(emap[n], int):
                    continue
                e, base = emap[n], re.sub(r"_\d$", "", n)
                if base == "blocked_cycles":
                    dead[n] = live[e]["blocked_cycles"]
                elif base in ("transfer_tick", "transfer_period", "transfer_mode"):
                    dead[n] = live[e]["transfer"]
                elif base in RUN_FAMILIES:
                    dead[n] = live[e]["run"]
            specs[mid] = {"invariants": inv, "dead": dead}
    json.dump(specs, open(a.out, "w"), indent=1)
    print(len(specs) - 1, "per-mutant specs;", len(inv), "invariants;", "queue counts:", ",".join(fifo_counts))


if __name__ == "__main__":
    main()
