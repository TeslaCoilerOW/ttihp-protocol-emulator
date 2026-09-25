# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Directed tests written from the mutation campaign's survivor analysis.

The RTL mutation campaign (docs/verification-campaign.md, "Mutation testing")
classified a random sample of the mutants that the suite did not detect and
wrote down, for each killable one, the stimulus that would detect it. These
twelve tests encode that stimulus (they started as
campaigns/mutation/directed/test_mutation_gaps.py). Each docstring names the
mutants it was written for; they are campaign ids, see
campaigns/mutation/results/sample_classification.tsv.

Two more tests come from re-running the survivors against the extended suite
(docs/verification-campaign.md, "Gap closure"): far jump targets with every
PC bit set (JMP, LOOP, JZ) and XFER on every TX pin next to driven pins.

As everywhere in this suite, every cycle is compared with the reference model;
the asserts add the ISA-level expectation. Every scenario is an
``async def f(h)`` on a Harness, so it also runs on the model alone
(``harness.run_model``). The designs under test differ in queue depth, debug
counters and shift unit (PE_VARIANT); the scenarios use only what every variant
supports and read their expectations from the model configuration. The three
scenarios that need more than 8,000 cycles are skipped at gate level.

Engine numbers in the mutant notes: Hardcaml's de-duplication suffixes on
per-engine registers do not follow the engine index (``repeat_count_0`` is
engine 3, ``x_2`` is engine 0); campaigns/mutation/engine_map.py derives the
mapping from the netlist.
"""

from __future__ import annotations

import os

import cocotb

from harness import (BEGIN, CLEAR, FLUSH, OWN, ROUTE, RS_COUNT, RS_LEVELS, RS_PC, RS_STATUS, SELECT, START,
                     UO_FAULT, CocotbHarness, Harness, immediate, instruction)
from scenarios import fault_of

GATE_LEVEL = bool(os.environ.get("PE_GATE_LEVEL"))

# Opcodes (docs/isa.md) and register numbers.
NOP, HALT, SET, DIR, WAIT, JMP, PUSH, COUNT, LOOP, LIMIT, WAITPIN = 0, 1, 2, 3, 4, 5, 7, 10, 11, 12, 13
PINS, XFER, LOAD, XOR, SHL, SHR, JZ, TIME = 16, 17, 19, 21, 24, 25, 26, 28
TX, RX, X = 0, 1, 2


def counters(h: Harness) -> bool:
    """Does the design have completed-instruction counters (READ_SELECT 5)?"""
    return getattr(getattr(h.model, "options", None), "debug_counters", True)


async def status_of(h: Harness, engine: int, selection: int = RS_STATUS) -> int:
    await h.command(SELECT, engine)
    return await h.status(selection)


# ------------------------------------------------------------- scenarios
async def full_image(h: Harness, prefix: list[int]) -> None:
    """Mutant 852: an image of exactly the program capacity on engine 1 falls off its
    end and faults with code 2 at PC = capacity. In the mutant the committed length
    register alternates between 64 and 192 every cycle."""
    await h.start()
    capacity = h.model.config.program_words
    await h.load(1, prefix + [instruction(NOP)] * (capacity - len(prefix)))
    await h.command(START, 0b0010)
    await h.idle(capacity + 36)
    assert fault_of(await status_of(h, 1)) == 2
    assert await h.status(RS_PC) == capacity


async def full_image_nops(h: Harness) -> None:
    await full_image(h, [])


async def full_image_after_wait(h: Harness) -> None:
    """The other cycle parity: a WAIT 1 first shifts the fall-through by one cycle."""
    await full_image(h, [immediate(WAIT, 1)])


async def own_overlap(h: Harness) -> None:
    """Mutant 696: OWN of a pin that engine 1 already owns (pin 5) is rejected with the
    sticky host fault, and ownership is unchanged."""
    await h.start()
    await h.load(1, [instruction(HALT)], ownership=0x20)
    await h.command(SELECT, 2)
    await h.command(BEGIN)
    await h.command(OWN, 0x20)  # overlaps engine 1: rejected
    assert h.model.host_fault and h.expected.uo & UO_FAULT
    assert h.model.engines[2].ownership == 0 and h.model.engines[1].ownership == 0x20
    await h.idle(4)
    await h.status(RS_STATUS)


async def operand_check_engine1(h: Harness) -> None:
    """Mutant 1416: engine 1's operand check. TIME with a nonzero c field faults with code 1."""
    await h.start()
    await h.load(1, [instruction(TIME, X, 0, 1), instruction(HALT)])
    await h.command(START, 0b0010)
    await h.idle(12)
    assert fault_of(await status_of(h, 1)) == 1


async def route_count_above_4095(h: Harness) -> None:
    """Mutant 952: a ROUTE descriptor of 4097 words from engine 2 keeps moving words after
    the first (the mutant truncates the count to 1)."""
    await h.start()
    words = min(3, h.model.config.fifo_words)
    program = [instruction(LOAD, RX, 0x12, 0x34)] + [instruction(PUSH)] * words + [instruction(HALT)]
    await h.load(2, program)
    await h.command(ROUTE, 2 | 3 << 2 | 1 << 4 | 4097 << 5)
    await h.command(START, 0b0100)
    await h.idle(40)
    assert await status_of(h, 3, RS_LEVELS) & 0xFFFF == words
    assert h.model.routes[2] == (3, 4097 - words)


async def flush_disables_route(h: Harness) -> None:
    """Mutant 1152: FLUSH of a route's source engine disables the route."""
    await h.start()
    await h.load(1, [instruction(LOAD, RX, 0, 5), instruction(PUSH), instruction(PUSH), instruction(HALT)])
    await h.command(ROUTE, 1 | 3 << 2 | 1 << 4 | 10 << 5)
    await h.command(SELECT, 1)
    await h.command(FLUSH)
    assert h.model.routes[1] is None
    await h.command(START, 0b0010)
    await h.idle(30)
    assert await status_of(h, 3, RS_LEVELS) & 0xFFFF == 0
    assert await status_of(h, 1, RS_LEVELS) == 2 << 16  # both words stay in engine 1's RX


async def repeat_counts(h: Harness) -> None:
    """Mutants 268 and 451 (repeat_count_0, engine 3) and 271 (engine 1): COUNT 72 on
    engines 0 and 3, COUNT 0x2000 on engine 1 (the mutants change the iteration count)."""
    await h.start()
    loop72 = [immediate(COUNT, 72), immediate(LOOP, 1), instruction(HALT)]
    await h.load(0, loop72)
    await h.load(3, loop72)
    await h.load(1, [immediate(COUNT, 0x2000), immediate(LOOP, 1), instruction(HALT)])
    await h.command(START, 0b1011)
    await h.idle(120)
    expected = 1 + 73 + 1 if counters(h) else 0  # COUNT, 73 LOOPs, HALT
    assert await status_of(h, 0, RS_COUNT) == expected
    assert await status_of(h, 3, RS_COUNT) == expected
    assert await status_of(h, 1) & 1  # engine 1 is still looping (8193 iterations)
    assert await h.status(RS_PC) == 1


async def long_loop_count(h: Harness) -> None:
    """Mutant 423 (completed_instructions_0, engine 2) and its kind: more than 32,768
    completed instructions, HALT, then READ_SELECT 5 while halted."""
    await h.start()
    program = [immediate(COUNT, 40000), immediate(LOOP, 1), instruction(HALT)]
    await h.load(2, program)
    await h.load(3, program)
    await h.command(START, 0b1100)
    await h.idle(40010)
    expected = 1 + 40001 + 1 if counters(h) else 0
    assert await status_of(h, 2, RS_COUNT) == expected
    assert await status_of(h, 3, RS_COUNT) == expected


async def blocked_count_after_alu(h: Harness) -> None:
    """Mutants 98 and 436 (engine 1) and 478 (engine 2): a TIME or ALU instruction directly
    before a WAITPIN that times out. Every completed instruction clears the blocked-cycle
    count; the mutants leave 2, 16 or 32 in it, so fault 3 would arrive early."""
    await h.start()
    for engine, alu in ((1, instruction(TIME, X)), (2, instruction(XOR, X, X))):
        await h.load(engine, [immediate(LIMIT, 40), alu, instruction(WAITPIN, 7, 1), instruction(HALT)])
    await h.command(START, 0b0110)
    await h.idle(80)
    assert fault_of(await status_of(h, 1)) == 3
    assert fault_of(await status_of(h, 2)) == 3


async def long_bounded_wait(h: Harness) -> None:
    """Mutants 174 (blocked_cycles_1, engine 1) and 195 (blocked_cycles, engine 0): a bounded
    wait longer than 8,192 cycles. LIMIT 0x2108, not a multiple of 32: with 0x2100 the
    bit-4 mutant (195) oscillates back onto the same timeout cycle."""
    await h.start()
    for engine in (0, 1):
        await h.load(engine, [immediate(LIMIT, 0x2108), instruction(WAITPIN, 7, 1), instruction(HALT)])
    await h.command(START, 0b0011)
    await h.idle(0x2108 + 20)
    assert fault_of(await status_of(h, 0)) == 3
    assert fault_of(await status_of(h, 1)) == 3


async def shift_into_rx_bit15(h: Harness) -> None:
    """Mutant 2279 (rx_0, engine 1): an SHR result with bit 15 set, pushed and read by the
    host. rx = 0x80000000 is built with byte-lane shifts so that every variant runs it."""
    await h.start()
    await h.load(1, [instruction(LOAD, RX, 0, 0x80), instruction(SHL, RX, 0, 24), instruction(SHR, RX, 0, 16),
                     instruction(PUSH), instruction(HALT)])
    await h.command(START, 0b0010)
    await h.idle(12)
    await h.command(SELECT, 1)
    assert await h.read(3) == 0x8000


async def slow_xfer_period(h: Harness) -> None:
    """Mutant 1753 (transfer_period, engine 2): XFER with half-period 201. Bit 7 is set and
    the period is odd: the mutated period register alternates every cycle, so an even
    period would reload in phase and hide it."""
    await h.start()
    await h.load(2, [immediate(DIR, 0x01), immediate(PINS, 0 | 1 << 3 | 2 << 6),
                     instruction(XFER, 2, 201, 0), instruction(HALT)], ownership=0x01)
    await h.command(START, 0b0100)
    await h.idle(4 * 201 + 40)
    assert fault_of(await status_of(h, 2)) == 0
    assert await h.status(RS_PC) == 4


# ------------------------------------------------ from the re-measurement
async def jump_targets(h: Harness) -> None:
    """Far jump targets: JMP, LOOP (taken) and JZ (taken) to a target with bit k set, for
    k = 6..23 (JZ: its 16-bit field, k = 6..15), on all four engines. The next issue
    faults with code 2 and READ_SELECT 3 reads the whole target (127 where the PC
    saturates). The random programs only jump below 104, so no mutant on the upper
    PC bits of a jump path was ever exercised (27 survivors of the first re-run)."""
    await h.start()
    saturating = getattr(getattr(h.model, "options", None), "pc_bits", "full") == "saturating_7"
    cases = [(kind, k) for k in range(6, 24) for kind in ("JMP", "LOOP", "JZ") if kind != "JZ" or k < 16]
    for kind, k in cases:
        targets = [1 << k | 3 * engine + 1 for engine in range(4)]
        for engine, target in enumerate(targets):
            program = {"JMP": [immediate(JMP, target)],
                       "LOOP": [immediate(COUNT, 1), immediate(LOOP, target)],
                       "JZ": [instruction(JZ, X, target >> 8, target & 255)]}[kind]
            await h.load(engine, program)
        await h.command(START, 0b1111)
        await h.idle(4)
        for engine, target in enumerate(targets):
            assert fault_of(await status_of(h, engine)) == 2, (kind, k, engine)
            assert await h.status(RS_PC) == (min(target, 127) if saturating else target), (kind, k, engine)
        await h.command(CLEAR, 0b1111)


async def xfer_neighbour_pins(h: Harness) -> None:
    """XFER with drive on every TX pin 0..7 (clock on the next pin) while the engine owns and
    drives all eight pins with a pattern: an XFER may change only its clock and TX pins.
    Each engine in turn owns all pins (engines 1 and 3 with open-drain pins 0x33)."""
    await h.start()
    for engine in range(4):
        program = [immediate(DIR, 0xFF), immediate(SET, 0x5A)]
        for tx in range(8):
            flags = 8 | (4 if tx & 1 else 0) | (2 if tx & 2 else 0) | (1 if tx & 4 else 0)
            program += [instruction(LOAD, TX, 0xA5, 0x5A ^ tx), immediate(PINS, (tx + 1) % 8 | tx << 3 | 7 << 6),
                        instruction(XFER, 8, 1 + tx % 3, flags), immediate(SET, 0xA5 ^ 1 << tx)]
        program.append(instruction(HALT))
        if engine:
            await h.load(engine - 1, [instruction(HALT)])  # release its pins
        await h.load(engine, program, ownership=0xFF, open_drain=0x33 if engine & 1 else 0)
        await h.command(START, 1 << engine)
        await h.run_until(lambda e=engine: not h.model.engines[e].running, 1200, "XFER sweep")
        assert fault_of(await status_of(h, engine)) == 0, engine
        assert await h.status(RS_PC) == len(program)


# ------------------------------------------------------------- cocotb tests
@cocotb.test()
async def test_gap_full_image_runs_off_end(dut):
    """Mutant 852: a full-capacity image on engine 1 falls off its end (fault 2)."""
    await full_image_nops(CocotbHarness(dut))


@cocotb.test()
async def test_gap_full_image_runs_off_end_odd(dut):
    """Mutant 852, other cycle parity (WAIT 1 first)."""
    await full_image_after_wait(CocotbHarness(dut))


@cocotb.test()
async def test_gap_own_overlap_pin5(dut):
    """Mutant 696: an OWN that overlaps engine 1's pin 5 is rejected."""
    await own_overlap(CocotbHarness(dut))


@cocotb.test()
async def test_gap_operand_check_engine1(dut):
    """Mutant 1416: TIME with c != 0 on engine 1 faults with code 1."""
    await operand_check_engine1(CocotbHarness(dut))


@cocotb.test()
async def test_gap_route_count_above_4095(dut):
    """Mutant 952: a 4097-word ROUTE descriptor from engine 2 moves every word."""
    await route_count_above_4095(CocotbHarness(dut))


@cocotb.test()
async def test_gap_flush_disables_route(dut):
    """Mutant 1152: FLUSH of the source engine disables its route."""
    await flush_disables_route(CocotbHarness(dut))


@cocotb.test()
async def test_gap_repeat_counts(dut):
    """Mutants 268, 451, 271: COUNT 72 and COUNT 0x2000 loops."""
    await repeat_counts(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_gap_long_loop_count(dut):
    """Mutant 423: 40,003 completed instructions read back after HALT."""
    await long_loop_count(CocotbHarness(dut))


@cocotb.test()
async def test_gap_blocked_count_after_alu(dut):
    """Mutants 98, 436, 478: a timing-out WAITPIN right after TIME/XOR."""
    await blocked_count_after_alu(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_gap_long_bounded_wait(dut):
    """Mutants 174, 195: a WAITPIN timeout after LIMIT 0x2108 cycles."""
    await long_bounded_wait(CocotbHarness(dut))


@cocotb.test()
async def test_gap_shift_into_rx_bit15(dut):
    """Mutant 2279: SHR into rx sets bit 15; the host reads 0x8000."""
    await shift_into_rx_bit15(CocotbHarness(dut))


@cocotb.test()
async def test_gap_slow_xfer_period(dut):
    """Mutant 1753: XFER with an odd half-period of 201 cycles."""
    await slow_xfer_period(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_gap_jump_targets(dut):
    """JMP/LOOP/JZ to targets with bit k set fault 2 and read back the whole target."""
    await jump_targets(CocotbHarness(dut))


@cocotb.test()
async def test_gap_xfer_neighbour_pins(dut):
    """XFER on every TX pin changes only its clock and TX pins."""
    await xfer_neighbour_pins(CocotbHarness(dut))
