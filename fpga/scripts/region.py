# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# nextpnr-xilinx --pre-place script (fpga/scripts/build.sh REGION=...):
# constrain every slice cell (LUT, flip-flop, carry, wide mux) to a rectangle
# of SLICE sites. PE_REGION = "X0,Y0,X1,Y1" (inclusive, SLICE_X<x>Y<y>).
# Runs inside nextpnr's Python, where `ctx` is the design context.
import os
import re

x0, y0, x1, y1 = (int(v) for v in os.environ["PE_REGION"].split(","))
ctx.createRectangularRegion("pe_core", 1, 1, 0, 0)  # empty; SLICE bels added below
site = re.compile(r"^SLICE_X(\d+)Y(\d+)/")
nbel = 0
for bel in ctx.getBels():
    m = site.match(bel)
    if m and x0 <= int(m.group(1)) <= x1 and y0 <= int(m.group(2)) <= y1:
        ctx.addBelToRegion("pe_core", bel)
        nbel += 1
ncell = 0
for name, cell in ctx.cells:
    if cell.type in ("SLICE_LUTX", "SLICE_FFX", "CARRY4", "SELMUX2_1"):
        ctx.constrainCellToRegion(name, "pe_core")
        ncell += 1
print("pe region SLICE_X%d..%d Y%d..%d: %d bels, %d cells constrained" % (x0, x1, y0, y1, nbel, ncell))
