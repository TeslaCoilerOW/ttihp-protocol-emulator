# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""XFER: every half-period bit, every mode, the PINS register across waits and START.

An XFER loads its half-period, mode and pin selection into per-engine
registers and counts transitions down with a tick counter. The suite runs
XFER mostly with small half-periods (the protocol firmware) and one slow
transfer on one engine, so a tick or period bit that is dropped or flipped
only when a high bit is set, on one engine, survived; so did a PINS register
that changes while an engine waits (``engine_xfer`` survivors,
docs/mutation-push.md).

``half_periods``: on all four engines at once (engine e owns pins 2e, 2e+1),
two-bit transfers with every single-bit half-period 1..128, 255, 0xAA, 0x55, 9
and 200, the mode cycling through CPOL/CPHA/bit order with drive and sample
on; the pins loop back, so rx collects the driven bits, and rx is pushed.
``modes``: all 32 mode values with an 8-bit transfer of a dense word, rx
pushed. ``pins_register``: PINS, then WAIT 1 or WAIT 2, then a driving XFER
(the TX pin must be the one PINS selected); an XFER without drive next to an
enabled TX pin (the pin must not move); a driving XFER straight after START,
whose clock and TX pin both default to pin 0, faults with code 1.
``pins_parity``: sampling XFERs after odd and even numbers of instructions since
PINS, each engine reading another engine's TX pin.
"""

from __future__ import annotations

import cocotb

from harness import CLEAR, START, CocotbHarness, Harness, immediate, instruction, pad_value
from test_kill_common import (DIR, GATE_LEVEL, HALT, LOAD, MOV, NOP, NOT, OR, PINS, PUSH, SET, SHL, TX, WAIT, X, XFER,
                              Y, own, reload, run_drain)

HALF_PERIODS = [1, 2, 4, 8, 16, 32, 64, 128, 255, 0xAA, 0x55, 9, 200, 3]


def loopback(h: Harness, external: int = 0xA5) -> None:
    """Driven pins read back their driven level; undriven pins read ``external``."""
    h.pins = lambda cycle, out: pad_value(out, external)


def dense(word: int) -> list[int]:
    return [instruction(LOAD, TX, word >> 24, word >> 16 & 255), instruction(SHL, TX, 0, 16),
            instruction(LOAD, Y, word >> 8 & 255, word & 255), instruction(OR, TX, Y)]


def pins_of(engine: int) -> tuple[int, int]:
    return 2 * engine, 2 * engine + 1


async def half_periods(h: Harness) -> None:
    await h.start()
    loopback(h)
    for engine in range(4):
        await own(h, engine, 3 << 2 * engine)
    per_engine = []
    for engine in range(4):
        ck, out = pins_of(engine)
        program = [immediate(DIR, 3 << 2 * engine), immediate(PINS, ck | out << 3 | out << 6)]
        program += dense(0xC3A55A3C ^ engine * 0x11111111)
        for index, b in enumerate(HALF_PERIODS):
            mode = 0b11000 | (index + engine) % 8  # drive + sample, CPOL/CPHA/MSB cycling
            program += [instruction(XFER, 2, b, mode), instruction(PUSH)]
        per_engine.append(program + [instruction(HALT)])
    for engine in range(4):
        await reload(h, engine, per_engine[engine])
    await run_drain(h, limit=20000)
    h.assert_no_faults()


async def modes(h: Harness) -> None:
    await h.start()
    loopback(h, 0x3C)
    for engine in range(4):
        await own(h, engine, 3 << 2 * engine)
    for first in range(0, 32, 8):
        for engine in range(4):
            ck, out = pins_of(engine)
            program = [immediate(DIR, 3 << 2 * engine), immediate(PINS, ck | out << 3 | (out ^ 2) << 6)]
            program += dense(0x96C3A55A)
            for mode in range(first, first + 8):
                program += [instruction(MOV, X, TX), instruction(XFER, 8, 1 + mode % 3, mode), instruction(PUSH),
                            instruction(MOV, TX, X), instruction(NOT, TX)]
            await reload(h, engine, program + [instruction(HALT)])
        await run_drain(h)
    h.assert_no_faults()


async def pins_register(h: Harness) -> None:
    await h.start()
    loopback(h)
    for engine in range(4):
        await own(h, engine, 3 << 2 * engine)
    for wait in (1, 2):
        for engine in range(4):
            ck, out = pins_of(engine)
            await reload(h, engine, [immediate(DIR, 3 << 2 * engine), *dense(0x5AF00FA5),
                                     immediate(PINS, ck | out << 3 | out << 6), immediate(WAIT, wait),
                                     instruction(XFER, 8, 1, 0b01000), immediate(WAIT, wait),
                                     instruction(XFER, 8, 2, 0b11100), instruction(PUSH), instruction(HALT)])
        await run_drain(h)
    for engine in range(4):  # no drive: the TX pin (set high, enabled) must not move
        ck, out = pins_of(engine)
        await reload(h, engine, [immediate(DIR, 3 << 2 * engine), immediate(SET, 1 << out), *dense(0x55555555),
                                 immediate(PINS, ck | out << 3 | out << 6), instruction(XFER, 8, 1, 0b10000),
                                 instruction(XFER, 8, 1, 0b10110), instruction(PUSH), instruction(HALT)])
    await run_drain(h)
    for engine in range(4):  # clock and TX pin default to pin 0 after START
        for other in range(4):
            await own(h, other, 0)
        await own(h, engine, 0b101)
        await reload(h, engine, [instruction(XFER, 1, 1, 0b01000), instruction(HALT)])
        await h.command(START, 1 << engine)
        await h.idle(3)
        assert h.model.engines[engine].fault == 1, engine
        await h.command(CLEAR, 1 << engine)
        await own(h, engine, 0)
    h.assert_no_faults()


async def pins_parity(h: Harness) -> None:
    """PINS, then an odd or an even number of other instructions before each sampling XFER
    (the RX pin is another engine's TX pin, whose neighbours carry other data): the PINS
    selection must hold for any number of cycles."""
    await h.start()
    loopback(h, 0x96)
    for engine in range(4):
        await own(h, engine, 3 << 2 * engine)
    for engine in range(4):
        ck, out = pins_of(engine)
        rx = (out + 2) % 8
        program = [immediate(DIR, 3 << 2 * engine), immediate(PINS, ck | out << 3 | rx << 6),
                   *dense(0x3CA5965A ^ engine * 0x01010101), instruction(NOP)]
        for gap in (1, 2, 3, 0):
            program += [instruction(XFER, 8, 1, 0b11000 | gap), instruction(PUSH)] + [instruction(NOP)] * gap
        await reload(h, engine, program + [instruction(HALT)])
    await run_drain(h)
    h.assert_no_faults()


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_pins_parity(dut):
    """PINS selection held across odd and even numbers of instructions before sampling XFERs."""
    await pins_parity(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_half_periods(dut):
    """Two-bit XFERs with every single-bit half-period and dense ones, on every engine."""
    await half_periods(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_modes(dut):
    """All 32 XFER modes with an 8-bit dense word, rx pushed, on every engine."""
    await modes(CocotbHarness(dut))


@cocotb.test()
async def test_kill_pins_register(dut):
    """PINS across WAITs, XFER without drive next to an enabled TX pin, XFER after START."""
    await pins_register(CocotbHarness(dut))
