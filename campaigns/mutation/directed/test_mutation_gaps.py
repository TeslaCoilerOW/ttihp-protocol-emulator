# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Directed tests written from the mutation-campaign survivor analysis.

Each test encodes the stimulus named in campaigns/mutation/sample_classification.py
for one or more surviving mutants. They run on the snapshot's lockstep harness,
so every cycle is still compared with the reference model; the explicit asserts
state the ISA-level expectation on top of that. The mutation campaign copies
this file into test/ of its per-task tree (stage "directed"); it is not part of
the repository's cocotb suite.

Engine numbers: the Hardcaml de-duplication suffixes of per-engine registers do
not follow the engine index (repeat_count_0 is engine 3, x_2 is engine 0, ...);
campaigns/mutation/engine_map.py derives the mapping from the netlist.
"""

from __future__ import annotations

import cocotb

from harness import (BEGIN, FLUSH, OWN, ROUTE, RS_COUNT, RS_LEVELS, RS_STATUS, SELECT, START,
                     CocotbHarness)
from model.reference import immediate, instruction
from scenarios import fault_of

NOP, HALT, DIR, WAIT, JMP, PUSH, COUNT, LOOP, LIMIT, WAITPIN = 0, 1, 3, 4, 5, 7, 10, 11, 12, 13
PINS, XFER, LOAD, XOR, SHL, SHR, TIME = 16, 17, 19, 21, 24, 25, 28
TX, RX, X = 0, 1, 2


async def started(dut) -> CocotbHarness:
    h = CocotbHarness(dut)
    await h.start()
    return h


async def status_of(h: CocotbHarness, engine: int, selection: int = RS_STATUS) -> int:
    await h.command(SELECT, engine)
    return await h.status(selection)


async def full_image(dut, prefix: list[int]) -> None:
    h = await started(dut)
    await h.load(1, prefix + [instruction(NOP)] * (64 - len(prefix)))
    await h.command(START, 0b0010)
    await h.idle(100)
    assert fault_of(await status_of(h, 1)) == 2


@cocotb.test()
async def test_gap_full_image_runs_off_end(dut):
    """Mutant 852: a full 64-word image on engine 1 that falls through faults 2 at PC 64."""
    await full_image(dut, [])


@cocotb.test()
async def test_gap_full_image_runs_off_end_odd(dut):
    """Mutant 852, other cycle parity (WAIT 1 first): the mutated length alternates 64/192."""
    await full_image(dut, [immediate(WAIT, 1)])


@cocotb.test()
async def test_gap_own_overlap_pin5(dut):
    """Mutant 696: OWN of a pin that engine 1 already owns is rejected (sticky host fault)."""
    h = await started(dut)
    await h.load(1, [instruction(HALT)], ownership=0x20)
    await h.command(SELECT, 2)
    await h.command(BEGIN)
    await h.command(OWN, 0x20)  # overlaps engine 1: must be rejected
    await h.idle(4)
    await h.status(RS_STATUS)


@cocotb.test()
async def test_gap_operand_check_engine1(dut):
    """Mutant 1416: TIME with a nonzero c field on engine 1 faults with code 1."""
    h = await started(dut)
    await h.load(1, [instruction(TIME, X, 0, 1), instruction(HALT)])
    await h.command(START, 0b0010)
    await h.idle(12)
    assert fault_of(await status_of(h, 1)) == 1


@cocotb.test()
async def test_gap_route_count_above_4095(dut):
    """Mutant 952: a ROUTE descriptor of 4097 words from engine 2 keeps moving words after the first."""
    h = await started(dut)
    program = [instruction(LOAD, RX, 0x12, 0x34)] + [instruction(PUSH)] * 3 + [instruction(HALT)]
    await h.load(2, program)
    await h.command(ROUTE, 2 | 3 << 2 | 1 << 4 | 4097 << 5)
    await h.command(START, 0b0100)
    await h.idle(40)
    assert await status_of(h, 3, RS_LEVELS) & 0xFFFF == 3


@cocotb.test()
async def test_gap_flush_disables_route(dut):
    """Mutant 1152: FLUSH of a route's source engine disables the route."""
    h = await started(dut)
    await h.load(1, [instruction(LOAD, RX, 0, 5), instruction(PUSH), instruction(PUSH), instruction(HALT)])
    await h.command(ROUTE, 1 | 3 << 2 | 1 << 4 | 10 << 5)
    await h.command(SELECT, 1)
    await h.command(FLUSH)
    await h.command(START, 0b0010)
    await h.idle(30)
    assert await status_of(h, 3, RS_LEVELS) & 0xFFFF == 0


@cocotb.test()
async def test_gap_repeat_counts(dut):
    """Mutants 268/451 (repeat_count_0 = engine 3) and 271 (engine 1): COUNT 72 and COUNT 0x2000."""
    h = await started(dut)
    loop72 = [immediate(COUNT, 72), immediate(LOOP, 1), instruction(HALT)]
    await h.load(0, loop72)
    await h.load(3, loop72)
    await h.load(1, [immediate(COUNT, 0x2000), immediate(LOOP, 1), instruction(HALT)])
    await h.command(START, 0b1011)
    await h.idle(120)
    assert await status_of(h, 0, RS_COUNT) == 1 + 73 + 1  # COUNT, 73 LOOPs, HALT
    assert await status_of(h, 3, RS_COUNT) == 1 + 73 + 1
    assert await status_of(h, 1) & 1  # engine 1 still looping (8193 iterations)


@cocotb.test()
async def test_gap_long_loop_count(dut):
    """Mutants 423 (completed_instructions_0 = engine 2) and similar: >= 32768 instructions,
    HALT, then READ_SELECT 5 while halted."""
    h = await started(dut)
    program = [immediate(COUNT, 40000), immediate(LOOP, 1), instruction(HALT)]
    await h.load(2, program)
    await h.load(3, program)
    await h.command(START, 0b1100)
    await h.idle(40010)
    assert await status_of(h, 2, RS_COUNT) == 1 + 40001 + 1
    assert await status_of(h, 3, RS_COUNT) == 1 + 40001 + 1


@cocotb.test()
async def test_gap_blocked_count_after_alu(dut):
    """Mutants 98/436 (engine 1) and 478 (engine 2): an ALU/TIME result instruction directly
    before a WAITPIN that times out; fault 3 must arrive exactly LIMIT samples later."""
    h = await started(dut)
    for engine, alu in ((1, instruction(TIME, X)), (2, instruction(XOR, X, X))):
        await h.load(engine, [immediate(LIMIT, 40), alu, instruction(WAITPIN, 7, 1), instruction(HALT)])
    await h.command(START, 0b0110)
    await h.idle(80)
    assert fault_of(await status_of(h, 1)) == 3
    assert fault_of(await status_of(h, 2)) == 3


@cocotb.test()
async def test_gap_long_bounded_wait(dut):
    """Mutants 174 (blocked_cycles_1 = engine 1) and 195 (blocked_cycles = engine 0):
    a bounded wait longer than 8192 cycles. LIMIT 0x2108, not a multiple of 32: with
    0x2100 the bit-4 variant (195) oscillates back onto the same timeout cycle."""
    h = await started(dut)
    for engine in (0, 1):
        await h.load(engine, [immediate(LIMIT, 0x2108), instruction(WAITPIN, 7, 1), instruction(HALT)])
    await h.command(START, 0b0011)
    await h.idle(0x2108 + 20)
    assert fault_of(await status_of(h, 0)) == 3
    assert fault_of(await status_of(h, 1)) == 3


@cocotb.test()
async def test_gap_shift_into_rx_bit15(dut):
    """Mutant 2279 (rx_0 = engine 1): SHR into rx producing bit 15, PUSH, host reads it."""
    h = await started(dut)
    await h.load(1, [instruction(LOAD, RX, 0, 1), instruction(SHL, RX, 0, 31), instruction(SHR, RX, 0, 16),
                     instruction(PUSH), instruction(HALT)])
    await h.command(START, 0b0010)
    await h.idle(12)
    await h.command(SELECT, 1)
    assert await h.read(3) == 0x8000


@cocotb.test()
async def test_gap_slow_xfer_period(dut):
    """Mutant 1753 (transfer_period = engine 2): XFER with half-period 201 (bit 7 set, odd:
    the mutated period register alternates every cycle, so an even period reloads in phase)."""
    h = await started(dut)
    await h.load(2, [instruction(DIR, 0, 0, 0x01), immediate(PINS, 0 | 1 << 3 | 2 << 6),
                     instruction(XFER, 2, 201, 0), instruction(HALT)], ownership=0x01)
    await h.command(START, 0b0100)
    await h.idle(4 * 201 + 40)
    assert fault_of(await status_of(h, 2)) == 0
