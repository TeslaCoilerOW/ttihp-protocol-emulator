#!/usr/bin/env python3
"""Write harness/fd_dut.vh from processor_fd.v's output ports.

    make_dut_include.py processor_fd.v formal_depth/harness/fd_dut.vh
"""
import re
import sys

text = open(sys.argv[1]).read()
outs = re.findall(r"^\s*output (\[\d+:0\] )?(\w+);", text, re.M)
lines = ["// Generated from processor_fd.v's port list: declares one wire per DUT output",
         "// and instantiates protocol_processor_fd as `dut`. The including module must",
         "// declare clk, rst_n, ena, ui_in[7:0] and uio_in[7:0].",
         "// Regenerate with: formal_depth/gen/make_dut_include.py"]
for w, n in outs:
    lines.append(f"wire {w or ''}{n};")
conns = [".clk(clk)", ".rst_n(rst_n)", ".ena(ena)", ".ui_in(ui_in)", ".uio_in(uio_in)"]
conns += [f".{n}({n})" for _, n in outs]
lines.append("protocol_processor_fd dut(")
lines.append(",\n".join("    " + c for c in conns))
lines.append(");")
open(sys.argv[2], "w").write("\n".join(lines) + "\n")
print(f"make_dut_include: {len(outs)} outputs")
