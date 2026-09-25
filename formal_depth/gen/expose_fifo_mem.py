#!/usr/bin/env python3
"""Expose the FIFO storage arrays of processor_fd.v as flat output ports.

generate_fd names each FIFO memory fd_tx_mem_<k> / fd_rx_mem_<k>. This adds,
for each, an output port <name>_flat = {mem[DEPTH-1], ..., mem[0]} (word i in
bits [32*i+31:32*i]). It only adds ports and continuous assignments that read
existing arrays; no existing line is changed. Usage:

    expose_fifo_mem.py IN.v OUT.v
"""
import re
import sys

src, dst = sys.argv[1], sys.argv[2]
text = open(src).read()
decls = re.findall(r"^\s*reg \[(\d+):0\] (fd_(?:tx|rx)_mem_\d+)\[0:(\d+)\];$", text, re.M)
names = sorted(d[1] for d in decls)
expected = sorted(f"fd_{d}_mem_{k}" for d in ("tx", "rx") for k in range(4))
if names != expected:
    raise SystemExit(f"expose_fifo_mem: expected {expected}, found {names}")
header = re.match(r"module (\w+) \(\n", text)
if not header or header.group(1) != "protocol_processor_fd":
    raise SystemExit("expose_fifo_mem: unexpected module header")
ports, body = [], []
for msb, name, last in sorted(decls, key=lambda d: d[1]):
    width, depth = int(msb) + 1, int(last) + 1
    ports.append(f"    {name}_flat,\n")
    body.append(f"    output [{width * depth - 1}:0] {name}_flat;\n")
    body.append(f"    assign {name}_flat = {{" +
                ", ".join(f"{name}[{i}]" for i in reversed(range(depth))) + "};\n")
text = text[:header.end()] + "".join(ports) + text[header.end():]
end = text.rindex("endmodule")
text = text[:end] + "".join(body) + text[end:]
open(dst, "w").write(text)
print(f"expose_fifo_mem: exposed {len(decls)} arrays")
