# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""SE0 end reads the pair pin that the PINS clock field names, for every pin
and on every engine (docs/isa.md, "Line-unit extension").

Specified behaviour (docs/isa.md, "Line XFER"): "Sampling: at each mid-bit
tick the RX pin (PINS RX field) is read"; a sampling XFER completes "at a
mid-bit tick with both pair pins low when SE0 end is set (LSTAT then reports
SE0 and the data bits left)"; the pair pin is the one of "(PINS clock
field)". Op 16 PINS: "bits2:0 clock pin, 5:3 TX pin, 8:6 RX pin". LCFG: "[7]
pair; ... [9] end a sampling XFER on SE0". Op 33 LSTAT: "[0] a sampling XFER
ended on SE0, ... [13:8] data bits remaining at SE0".

On every engine and for every pair pin q in 0-7 (RX pin q + 3 mod 8, TX field
q + 1 mod 8): a sample-only NRZI XFER of 16 bits with the pair and SE0 end,
while the environment drives the line levels on the RX pin, their complement
on pin q, and both low (SE0) in the cell after 5 data cells. Every other pin
is held high in one run and low in another: a pair-pin read from any other pin
misses the SE0 in the first run and sees an SE0 in the first data cell with a
low level in the second. Checked: LSTAT reports SE0 with 11 data bits left and
no other flag, and the engine has no fault (read from the DUT). The harness
also compares the DUT with the reference model on every cycle (off with
PE_SPEC_ONLY=1, test_line_spec_common.spec_harness).

At gate level (PE_GATE_LEVEL) the module is skipped unless PE_LINE_GL_FULL=1, as the long
line-unit demos are.
"""

from __future__ import annotations

import cocotb

from harness import Harness
from line_scenarios import Pads, RxCase, engine_status, load_and_start, read_rx, run_to_halt, rx_cells
from line_support import (HALT, PUSH, RX, SKIP_REASON, ST_LOST, ST_SE0, ST_STUFF_ERR, lcfg, line_unit_enabled,
                          lstat, ltim, pins, waitpin, xline)
from scenarios import fault_of
from test_line_spec_common import SKIP, spec_harness

if not line_unit_enabled():
    print(f"test_line_spec_se0_pins: {SKIP_REASON}")

BITS, SE0_AFTER = 16, 5


async def se0_on_pair_pin(h: Harness, engine: int, pair_pin: int, others_high: bool) -> int:
    """One sample-only XFER with SE0 end on the given pair pin; returns the LSTAT word."""
    await h.start()
    rx_pin, tx_field = (pair_pin + 3) % 8, (pair_pin + 1) % 8
    case = RxCase(f"pair pin {pair_pin}", 0x9C35, BITS, code=1, pair=True, se0_after=SE0_AFTER)
    levels, _ = rx_cells(case)
    p = case.period
    others = (0xFF if others_high else 0x00) & ~(1 << rx_pin | 1 << pair_pin)
    start: dict[str, int | None] = {"at": None}

    def source(cycle: int, pads: Pads) -> int:
        if start["at"] is None or cycle < start["at"]:
            d, dm = 1, 0  # idle J
        else:
            k = (cycle - start["at"]) // (2 * p) - 1  # cell -1 is the start cell
            if k < 0:
                d, dm = 0, 1
            elif k == SE0_AFTER:
                d, dm = 0, 0  # SE0 for one cell, then idle J
            elif k < SE0_AFTER:
                d, dm = levels[k], 1 - levels[k]
            else:
                d, dm = 1, 0
        return others | d << rx_pin | dm << pair_pin

    h.pins = Pads(source)
    program = [pins(pair_pin, tx_field, rx_pin), lcfg(1, pair=True, se0=True, init=0), waitpin(rx_pin, 0),
               ltim(p, 0, 2 * p - 3), xline(BITS, sample=True), lstat(RX), PUSH, HALT]
    await load_and_start(h, engine, program)
    await h.idle(20)
    start["at"] = h.cycle + 5
    what = f"engine {engine}, {case.name}, other pins {'high' if others_high else 'low'}"
    await run_to_halt(h, engine, 2 * p * (BITS + 40) + 600, what)
    (word,) = await read_rx(h, engine, 1)
    assert fault_of(await engine_status(h, engine)) == 0, f"{what}: fault"
    flags = word & (ST_SE0 | ST_LOST | ST_STUFF_ERR)
    assert flags == ST_SE0, f"{what}: LSTAT flags {flags:#x}, expected SE0 only (word {word:#x})"
    remaining = word >> 8 & 0x3F
    assert remaining == BITS - SE0_AFTER, f"{what}: {remaining} bits left at SE0, expected {BITS - SE0_AFTER}"
    return word


@cocotb.test(skip=SKIP)
async def test_se0_end_reads_the_pins_clock_field_pin(dut):
    """Every engine, every pair pin 0-7, other pins high and low: SE0 ends the XFER with 11 bits left."""
    h = spec_harness(dut)
    for engine in range(4):
        for pair_pin in range(8):
            for others_high in (True, False):
                await se0_on_pair_pin(h, engine, pair_pin, others_high)
        h.log("engine %d: SE0 detected on every pair pin", engine)
    h.assert_no_faults()
