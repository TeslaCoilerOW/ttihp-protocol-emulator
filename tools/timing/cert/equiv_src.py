#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""equiv_src: the formal netlist equals the committed top on its chip ports.

The certificates (and formal/) prove properties of ``protocol_processor_fv``:
``Processor.create_refinement ~debug:true`` plus observation ports. The chip
is ``src/project.v`` + ``src/protocol_emulator_core.v``. This script checks
that the two are equivalent on the chip ports (uo_out, uio_out, uio_oe).

Default method (``abc``):

1. Both files declare their registers (and FIFO storage arrays) in the same
   order with the same widths, and the named ones have the same names. The
   script checks this and gives each anonymous register (``_1234``) the same
   canonical name in both copies. Nothing else is changed.
2. The gold design is the committed top; the gate design wraps
   ``protocol_processor_fv`` in the same ports. The observation ports are left
   unconnected and removed as dead logic.
3. Yosys builds one miter (``miter -equiv``) over both, maps every memory (the
   IHP FUNCTIONAL SRAM arrays and the FIFO arrays) to flip-flops, bit-blasts it
   and gives every flip-flop the initial value 0 (``setundef -init``), then
   writes AIGER.
4. ABC ``dprove`` proves the miter output constant 0: it runs BMC, then signal
   (latch) correspondence, which proves by induction that the latches of the
   two designs are pairwise equal, and fraiging.

The result is sequential equivalence from the all-zero state (every register,
SRAM bit and FIFO bit 0), for every input sequence.

The other methods (``merge``: structural merging of the miter; ``equiv``:
yosys ``equiv_make`` + ``equiv_induct``) did not settle it and are kept only
for comparison.

Usage: equiv_src.py --src SRC_DIR --rtl RTL_DIR --models MODELS_DIR --out DIR [--run]
                    [--method abc|merge|equiv] [--gate OTHER_NETLIST]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REG = re.compile(r"^    reg (\[[^\]]*\] )?(\w+)(\[[^\]]*\])?;$", re.M)
ANON = re.compile(r"_\d+")


def registers(text: str) -> list[tuple[str, str, str]]:
    return [(m.group(1) or "", m.group(2), m.group(3) or "") for m in REG.finditer(text)]


def canonical(gold: str, gate: str) -> tuple[str, str, int]:
    rg, rt = registers(gold), registers(gate)
    if len(rg) != len(rt):
        raise SystemExit(f"equiv_src: {len(rg)} registers in the core, {len(rt)} in the formal netlist")
    maps: tuple[dict[str, str], dict[str, str]] = ({}, {})
    for i, ((wg, ng, ag), (wt, nt, at)) in enumerate(zip(rg, rt)):
        if (wg, ag) != (wt, at):
            raise SystemExit(f"equiv_src: register {i}: {wg}{ng}{ag} vs {wt}{nt}{at}")
        anon_g, anon_t = bool(ANON.fullmatch(ng)), bool(ANON.fullmatch(nt))
        if anon_g != anon_t or (not anon_g and ng != nt):
            raise SystemExit(f"equiv_src: register {i}: names {ng} and {nt} do not correspond")
        if anon_g:
            maps[0][ng] = maps[1][nt] = f"cert_reg_{i}"
    for text in (gold, gate):
        if re.search(r"\bcert_reg_\d+\b", text):
            raise SystemExit("equiv_src: canonical name clash")

    def apply(text: str, table: dict[str, str]) -> str:
        return re.sub(r"(?<![\w$\\])_\d+(?![\w$])", lambda m: table.get(m.group(0), m.group(0)), text)

    return apply(gold, maps[0]), apply(gate, maps[1]), len(maps[0])


GATE_TOP = """module gate_top (
    input wire [7:0] ui_in, output wire [7:0] uo_out, input wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe,
    input wire ena, input wire clk, input wire rst_n);
    protocol_processor_fv core(.clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_in),
        .uio_in(uio_in), .uo_out(uo_out), .uio_out(uio_out), .uio_oe(uio_oe));
endmodule
"""

YS = """read_verilog -DFUNCTIONAL -DSYNTHESIS {models}/RM_IHPSG13_1P_core_behavioral.v {models}/RM_IHPSG13_1P_64x16_c2.v
read_verilog gold_core.v
read_verilog {src}/project.v
read_verilog gate_fv.v gate_top.v
hierarchy -check
proc
opt_clean
flatten
opt_clean
memory_map
opt_clean
equiv_make -inames tt_um_teslacoilerow_protocol_emulator gate_top equiv
hierarchy -top equiv
opt_clean
equiv_induct -seq 2
equiv_status -assert
"""


# Structural method: one miter over both designs; identical logic is merged
# (opt_merge also merges flip-flops with identical inputs, which is exactly
# the state correspondence), and the miter's trigger must become constant 0.
YS_MERGE = """read_verilog -DFUNCTIONAL -DSYNTHESIS {models}/RM_IHPSG13_1P_core_behavioral.v {models}/RM_IHPSG13_1P_64x16_c2.v
read_verilog gold_core.v
read_verilog {src}/project.v
read_verilog gate_fv.v gate_top.v
hierarchy -check
proc
flatten
memory_map
opt_clean
miter -equiv -flatten tt_um_teslacoilerow_protocol_emulator gate_top miter
hierarchy -top miter
opt -full
opt_merge -share_all
opt -full
stat
sat -verify -prove trigger 0
"""


# ABC method: one miter over both designs, bit-blasted to AIGER with every
# register (SRAM and FIFO array bits included) initialised to 0, then ABC's
# sequential equivalence check (dprove: signal correspondence and induction,
# which finds the register correspondence itself).
YS_ABC = """read_verilog -DFUNCTIONAL -DSYNTHESIS {models}/RM_IHPSG13_1P_core_behavioral.v {models}/RM_IHPSG13_1P_64x16_c2.v
read_verilog gold_core.v
read_verilog {src}/project.v
read_verilog gate_fv.v gate_top.v
hierarchy -check
proc
flatten
memory_map
opt_clean
miter -equiv -flatten tt_um_teslacoilerow_protocol_emulator gate_top miter
hierarchy -top miter
flatten
opt -fast -nodffe -nosdff
async2sync
dffunmap
techmap
opt -fast -nodffe -nosdff
dffunmap
aigmap
opt_clean
setundef -zero -undriven -init
stat
write_aiger -zinit miter.aig
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, help="the committed src/ directory")
    ap.add_argument("--rtl", required=True, help="formal/run.sh output (processor_fv.v)")
    ap.add_argument("--models", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run", action="store_true", help="run yosys and report")
    ap.add_argument("--frames", type=int, default=20,
                    help="BMC frames of ABC dprove before induction (default 20; a counterexample to "
                         "the negative control needs about 45: load, commit and start engine 0)")
    ap.add_argument("--bmc-only", type=int, default=0,
                    help="run only ABC bmc3 to this many frames (for the negative control: a "
                         "counterexample must be found)")
    ap.add_argument("--gate", help="netlist to compare instead of RTL/processor_fv.v "
                                   "(negative control: processor_fv_mutant.v must NOT be equivalent)")
    ap.add_argument("--method", choices=("abc", "merge", "equiv"), default="abc",
                    help="abc: AIGER miter + ABC dprove (default); merge: structural merge of one miter; "
                         "equiv: yosys equiv_* by names")
    args = ap.parse_args(argv)
    src, rtl, models, out = (Path(p).resolve() for p in (args.src, args.rtl, args.models, args.out))
    out.mkdir(parents=True, exist_ok=True)
    gate_file = Path(args.gate).resolve() if args.gate else rtl / "processor_fv.v"
    gold_text, gate_text = (src / "protocol_emulator_core.v").read_text(), gate_file.read_text()
    try:
        gold, gate, n = canonical(gold_text, gate_text)
    except SystemExit as exc:
        # The ABC method does not use names; only the equiv_* method needs them.
        if args.method == "equiv":
            raise
        print(f"{exc} (names left as they are; the {args.method} method does not use them)")
        gold, gate, n = gold_text, gate_text, 0
    (out / "gold_core.v").write_text(gold)
    (out / "gate_fv.v").write_text(gate)
    (out / "gate_top.v").write_text(GATE_TOP)
    script = {"merge": YS_MERGE, "equiv": YS, "abc": YS_ABC}[args.method]
    (out / "equiv.ys").write_text(script.format(models=models, src=src))
    print(f"equiv_src: {len(registers(gold))} registers, {n} anonymous ones renamed; script {out / 'equiv.ys'}")
    if not args.run:
        return 0
    rc = subprocess.call(["yosys", "-ql", "equiv.log", "equiv.ys"], cwd=out)
    if args.method == "abc" and rc == 0:
        with open(out / "abc.log", "w") as f:
            cmd = (f"read_aiger miter.aig; print_stats; strash; bmc3 -v -F {args.bmc_only}" if args.bmc_only
                   else f"read_aiger miter.aig; print_stats; strash; dprove -v -F {args.frames}")
            rc = subprocess.call(["yosys-abc", "-c", cmd],
                                 cwd=out, stdout=f, stderr=subprocess.STDOUT)
        text = (out / "abc.log").read_text(errors="replace")
        print("\n".join(text.splitlines()[-8:]))
        if args.bmc_only:
            cex = "was asserted in frame" in text
            print(f"equiv_src: {'COUNTEREXAMPLE (not equivalent)' if cex else 'no counterexample'} "
                  f"(ABC bmc3, {args.bmc_only} frames)")
            return 1 if cex else 0
        proved = rc == 0 and ("Networks are equivalent" in text or "Property proved" in text)
        print(f"equiv_src: {'EQUIVALENT' if proved else 'NOT PROVED'} (ABC dprove)")
        return 0 if proved else 1
    log = (out / "equiv.log").read_text(errors="replace")
    status = [line for line in log.splitlines() if "Found" in line and "$equiv" in line]
    print("\n".join(status[-3:]))
    print(f"equiv_src: yosys exit {rc}: {'EQUIVALENT' if rc == 0 else 'NOT PROVED'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
