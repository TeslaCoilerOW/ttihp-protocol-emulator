# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The line ticker over long runs, for fractions and delays that set every
bit, on every engine (docs/isa.md, "Line-unit extension").

Specified behaviour (docs/isa.md): op 30 LTIM "imm24 [7:0] half-period P
(0 stops the ticker), [15:8] fraction Q/256, [23:16] first-tick delay D
(0 means P); (re)starts the ticker"; "Ticker. LTIM issued in cycle c0 gives
ticks in cycles T0 = c0 + (D, or P when D = 0) and T(k+1) = T(k) + P +
carry(k), where carry(k) = (acc(k) + Q) >> 8, acc(k+1) = (acc(k) + Q) mod 256
and acc(0) = 0. Even ticks are bit boundaries, odd ticks mid-bit. The ticker
runs while the engine runs, also during WAIT and blocked instructions." A
drive-only line XFER writes the next line bit at each boundary tick and
"completes at the boundary tick of its last cell".

test_line_unit checks ten (P, Q, D) sets over one 32-bit XFER. Here a loop
streams four 32-bit XFERs of 0x55555555 (a pad transition at every boundary)
over 128 bit boundaries (256 ticks), so that a fraction error accumulates:
per engine, one run for each single-bit fraction 1, 2, 4, ..., 128 and for
255, 85 and 170, with half-periods 2 to 9 and delays 0 (= P), 1, 5 and 200.
Checked: every pad transition falls on a boundary tick of the formula (plus
one cycle, the pin register) and all 128 boundaries show one. The harness
compares the DUT with the reference model on every cycle.

At gate level (PE_GATE_LEVEL) the module is skipped unless PE_LINE_GL_FULL=1, as the long
line-unit demos are.
"""

from __future__ import annotations

import cocotb

from harness import CocotbHarness
from line_scenarios import Pads, engine_status, load_and_start, run_to_halt, ticks
from line_support import (HALT, SKIP_REASON, TX, X, count, dir_, lcfg, line_unit_enabled, load, loop, ltim, mov,
                          or_, pins, set_, shl, wait, waitpin, xline)
from scenarios import fault_of
from test_line_spec_common import SKIP

if not line_unit_enabled():
    print(f"test_line_spec_ticker: {SKIP_REASON}")

# (P, Q, D): every bit of Q set in some run, and Q = 255, 85, 170; D = 0 means P
RUNS = [(2, 1, 0), (3, 2, 5), (4, 4, 1), (5, 8, 0), (6, 16, 200), (7, 32, 1), (8, 64, 5), (9, 128, 0),
        (3, 255, 1), (4, 85, 0), (5, 170, 5)]
XFERS = 4


@cocotb.test(skip=SKIP)
async def test_ticker_fraction_over_256_ticks_every_engine(dut):
    """LTIM P/Q/D on every engine: 128 streamed bit boundaries land on the ticker formula's even ticks."""
    h = CocotbHarness(dut)
    for engine in range(4):
        data_pin, marker_pin = 2 * engine, 2 * engine + 1
        for period, frac, delay in RUNS:
            await h.start()
            marker: dict[str, int | None] = {"at": None}
            pads = Pads(lambda cycle, pads, m=marker_pin: 0xFF & ~(1 << m) | (
                (1 << m) if marker["at"] is not None and cycle >= marker["at"] else 0))
            h.pins = pads
            body = 13  # PC of the loop body (mov TX, X)
            program = [pins((data_pin + 1) % 8, data_pin, marker_pin), lcfg(), set_(0), dir_(1 << data_pin),
                       load(X, 0x5555), mov(TX, X), shl(X, 16), or_(X, TX), count(XFERS - 1), load(TX, 0),
                       waitpin(marker_pin, 1), ltim(period, frac, delay), wait(0),
                       mov(TX, X), xline(32, drive=True), loop(body), wait(2 * period + 8), HALT]
            assert program[body] == mov(TX, X)
            await load_and_start(h, engine, program, ownership=1 << data_pin)
            await h.idle(16)
            marker["at"] = h.cycle + 3
            c0 = marker["at"] + 3
            nbits = 32 * XFERS
            limit = (2 * period + 2) * (nbits + 8) + 800
            await run_to_halt(h, engine, limit, f"engine {engine} P={period} Q={frac} D={delay}")
            assert fault_of(await engine_status(h, engine)) == 0
            tick_list = ticks(c0, period, frac, delay, 2 * nbits + 8)
            boundaries = {tick_list[k] + 1 for k in range(0, len(tick_list), 2)}
            end = max(boundaries) + 2 * period
            seen = [t for t in range(c0, end) if pads.pad(t, data_pin, 0) != pads.pad(t - 1, data_pin, 0)]
            off = [t for t in seen if t not in boundaries]
            what = f"engine {engine}, P={period} Q={frac} D={delay}"
            assert not off, f"{what}: transitions off the formula's boundary ticks at {off[:5]}"
            assert len(seen) == nbits, f"{what}: {len(seen)} transitions, expected {nbits}"
        h.log("engine %d: %d LTIM settings over %d boundaries each", engine, len(RUNS), 32 * XFERS)
