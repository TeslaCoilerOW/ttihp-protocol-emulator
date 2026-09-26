# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Output pins: every value, enable and open-drain combination on every pin and engine.

uio_out and uio_oe combine, per pin, the owning engine's logical value and
enable, its open-drain mask and its run state (docs/isa.md). The suite drives
the pins of each protocol and a few directed patterns; a pin path that is
wrong only for one engine, one pin and one open-drain setting, or while the
engine blocks in WAITPIN/WAITEVENT, or on one XFER bit order next to one TX
pin, survived (``pins`` survivors, docs/mutation-push.md).

``pin_matrix``: each engine in turn owns all eight pins with open-drain masks
0x00, 0xFF, 0x0F, 0xF0, 0x55, 0xAA, 0x33 and 0xCC, and runs SET/DIR patterns,
OUT of a one onto every pin while the other pins are low and of a zero while
they are high (both bit orders), and WAITEVENTs and WAITPINs that block until
they time out with enable patterns including none and all (outputs held while
blocked, released on the fault). ``xfer_tx_pins``: each engine in turn drives
an 8-bit XFER on every TX pin (clock on the next pin) in all four CPHA/bit-order
combinations while the other pins are all low, then all high.
"""

from __future__ import annotations

import cocotb

from harness import CLEAR, START, CocotbHarness, Harness, immediate, instruction
from test_kill_common import (DIR, GATE_LEVEL, HALT, LIMIT, LOAD, MOV, NOT, OR, OUT, PINS, SET, SHL, TX, WAITEVENT,
                              WAITPIN, X, XFER, Y, own, reload)

OPEN_DRAIN = [0x00, 0xFF, 0x0F, 0xF0, 0x55, 0xAA, 0x33, 0xCC]


def dense_x(word: int) -> list[int]:
    return [instruction(LOAD, X, word >> 24, word >> 16 & 255), instruction(SHL, X, 0, 16),
            instruction(LOAD, Y, word >> 8 & 255, word & 255), instruction(OR, X, Y), instruction(MOV, TX, X)]


async def run_to_fault(h: Harness, engine: int, program: list[int]) -> None:
    await reload(h, engine, program)
    await h.command(START, 1 << engine)
    await h.run_until(lambda: not h.model.engines[engine].running, 400, "program end")
    await h.command(CLEAR, 1 << engine)


async def pin_matrix(h: Harness) -> None:
    await h.start()
    h.pins = 0
    for engine in range(4):
        for drain in OPEN_DRAIN:
            await own(h, engine, 0xFF, drain)
            program = [immediate(DIR, 0xFF)] + [immediate(SET, v) for v in (0x00, 0xFF, 0x5A, 0xA5)]
            program += [immediate(DIR, v) for v in (0x0F, 0xF0, 0x3C, 0xC3, 0xFF)]
            # OUT of a one onto every pin while the others are low, then of a zero while they are high
            program += [instruction(LOAD, TX, 0, 0), instruction(NOT, TX), immediate(SET, 0x00)]
            program += [instruction(OUT, pin, 0, pin & 1) for pin in range(8)]
            program += [instruction(LOAD, TX, 0, 0), immediate(SET, 0xFF)]
            program += [instruction(OUT, pin, 0, ~pin & 1) for pin in range(7, -1, -1)]
            program += [immediate(SET, 0x5A), immediate(DIR, 0x00), immediate(LIMIT, 6), instruction(WAITEVENT)]
            await run_to_fault(h, engine, program)
            for enables, values in ((0x5F, 0xA5), (0x00, 0xFF), (0xA0, 0x0A)):
                if drain not in (0x00, 0xFF) and enables != 0x5F:
                    continue
                await run_to_fault(h, engine, [immediate(DIR, 0xFF), immediate(SET, values), immediate(DIR, enables),
                                               immediate(LIMIT, 6), instruction(WAITPIN, 7, 1)])
                await run_to_fault(h, engine, [immediate(DIR, 0xFF), immediate(SET, values ^ 0xFF),
                                               immediate(DIR, enables ^ 0xFF), immediate(LIMIT, 5),
                                               instruction(WAITEVENT)])
        await own(h, engine, 0)
    h.assert_no_faults()


async def xfer_tx_pins(h: Harness) -> None:
    await h.start()
    h.pins = 0
    for engine in range(4):
        await own(h, engine, 0xFF)
        for pins, rest in ((range(4), 0x00), (range(4, 8), 0x00), (range(4), 0xFF), (range(4, 8), 0xFF)):
            program = [immediate(DIR, 0xFF), immediate(SET, rest), *dense_x(0xC35AA53C ^ engine)]
            for tx in pins:
                program.append(immediate(PINS, (tx + 1) % 8 | tx << 3 | tx << 6))
                for mode in (0b01000, 0b01100, 0b01010, 0b01110):
                    program += [instruction(MOV, TX, X), instruction(XFER, 8, 1, mode)]
            program.append(instruction(HALT))
            await reload(h, engine, program)
            await h.command(START, 1 << engine)
            await h.run_until(lambda e=engine: not h.model.engines[e].running, 2000, "XFER sweep")
        await own(h, engine, 0)
    h.assert_no_faults()


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_pin_matrix(dut):
    """SET/DIR/OUT and blocked waits on every pin, engine and open-drain mask."""
    await pin_matrix(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_xfer_tx_pins(dut):
    """Driving XFER on every TX pin in all CPHA/bit-order combinations, every engine."""
    await xfer_tx_pins(CocotbHarness(dut))
