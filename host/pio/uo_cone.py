# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Which input bits reach each output combinationally (RTL, bit level).

The lockstep program samples uo_out 3 system clocks (20 ns at 150 MHz) after
it drives ui_in, which is too early for a combinational path from ui_in to
uo_out through the Tiny Tapeout pads. It relies on this: within a window,
uo_out depends combinationally only on the window bits ui_in[7:6] (docs/isa.md:
ready and valid "drop immediately when the window bits change"), and the
program never changes the window in a frame whose sample it uses. This tool
checks the first half on the RTL:

    python3 -m pio.uo_cone [--yosys yosys] [--repo DIR] [--json out.json]

Yosys reads src/project.v and src/protocol_emulator_core.v (the IHP SRAM
macro as a black box with the ports of models/RM_IHPSG13_1P_64x16_c2.v, so
its outputs count as registered: the macro reads synchronously), flattens and
maps to single-bit gates (synth -flatten, no ABC), and writes JSON; the tool
then walks every output bit's fan-in back to flip-flops, black-box outputs
or top-level inputs.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

TOP = "tt_um_teslacoilerow_protocol_emulator"
SEQUENTIAL_PREFIXES = ("$_DFF", "$_SDFF", "$_DFFE", "$_SDFFE", "$_SDFFCE", "$_ALDFF",
                       "$_DLATCH", "$_SR", "$_DFFSR")


SRAM_STUB = """(* blackbox *)
module RM_IHPSG13_1P_64x16_c2 (input A_CLK, input A_MEN, input A_WEN, input A_REN,
    input [5:0] A_ADDR, input [15:0] A_DIN, input A_DLY, output [15:0] A_DOUT);
endmodule
"""


def run_yosys(yosys, repo, out_json):
    src = os.path.join(repo, "src")
    stub = out_json + ".sram_stub.v"
    with open(stub, "w") as handle:
        handle.write(SRAM_STUB)   # ports as in models/RM_IHPSG13_1P_64x16_c2.v
    script = "; ".join([
        "read_verilog %s" % stub,
        "read_verilog -DFUNCTIONAL %s %s" % (os.path.join(src, "project.v"),
                                             os.path.join(src, "protocol_emulator_core.v")),
        "synth -flatten -top %s -noabc" % TOP,
        "write_json %s" % out_json,
    ])
    subprocess.run([yosys, "-q", "-p", script], check=True)


def cones(netlist):
    module = netlist["modules"][TOP]
    ports = module["ports"]
    driver = {}                      # bit -> list of input bits of its combinational driver
    stop = set()                     # bits driven by sequential cells or black boxes
    for cell in module["cells"].values():
        kind = cell["type"]
        dirs = cell.get("port_directions", {})
        conns = cell["connections"]
        outs = [b for p, bits in conns.items() if dirs.get(p) == "output" for b in bits]
        ins = [b for p, bits in conns.items() if dirs.get(p) == "input" for b in bits]
        sequential = kind.startswith(SEQUENTIAL_PREFIXES) or not kind.startswith("$")
        for bit in outs:
            if isinstance(bit, int):
                if sequential:
                    stop.add(bit)
                else:
                    driver.setdefault(bit, []).extend(b for b in ins if isinstance(b, int))
    names = {}                       # bit -> "port[i]" for top-level inputs
    for name, port in ports.items():
        if port["direction"] == "input":
            for i, bit in enumerate(port["bits"]):
                if isinstance(bit, int):
                    names[bit] = "%s[%d]" % (name, i)
    result = {}
    for name, port in ports.items():
        if port["direction"] != "output":
            continue
        for i, bit in enumerate(port["bits"]):
            seen, todo, reached = set(), [bit], set()
            while todo:
                b = todo.pop()
                if not isinstance(b, int) or b in seen:
                    continue
                seen.add(b)
                if b in names:
                    reached.add(names[b])
                if b in stop:
                    continue
                todo.extend(driver.get(b, ()))
            result["%s[%d]" % (name, i)] = sorted(reached)
    return result


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("--repo", default=os.path.abspath(os.path.join(here, "..", "..")))
    parser.add_argument("--json", help="write the per-output cones here")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "net.json")
        run_yosys(args.yosys, args.repo, path)
        with open(path) as handle:
            result = cones(json.load(handle))
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(result, handle, indent=1, sort_keys=True)
    bad = []
    for out, ins in sorted(result.items()):
        ui = [n for n in ins if n.startswith("ui_in")]
        print("%-11s <- %s" % (out, ", ".join(ins) or "(registers only)"))
        if out.startswith("uo_out") and set(ui) - {"ui_in[6]", "ui_in[7]"}:
            bad.append(out)
    print("uo_out depends combinationally on ui_in[5:0]: %s" % (", ".join(bad) or "no"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
