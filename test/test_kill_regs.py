# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Register file: every register observed after every kind of instruction.

The random test computes with the four registers (tx, rx, x, y) and often
pushes the results, but it rarely pushes a register that the last instructions
did not write, and rarely holds all-ones or all-zeros in it. A multiplexer
input that drops or flips one bit of a *held* register (the register an
instruction does not write), or one stage of the barrel shifter, therefore
went unobserved on some engines (``engine_data`` survivors,
docs/mutation-push.md).

``hold_matrix``: for every instruction kind (each one writing a different
destination in turn), all four registers are first set to all-zeros or
all-ones, the instruction runs, and rx ^ x ^ y ^ tx is pushed and read by the
host, on all four engines at once. ``shift_sweep``: SHL and SHR of two dense
patterns by every count that selects one shifter stage alone or all stages but
one (byte-lane designs: the four lane counts), the destination register
rotating over all four, results pushed.
``alu_chain``: every two-register operation on every ordered register pair,
NOT and shifts, with four distinct dense values, folded into rx and pushed
after every four operations. ``repeat_hold``: COUNT 2^k, a WAIT, XFER or PULL
stall, then a LOOP that must be taken. ``time_high``: TIME into each register at
timestamps with all bits below bit k set (time warp, RTL only).
"""

from __future__ import annotations

import cocotb

from harness import SELECT, START, CocotbHarness, Harness, immediate, instruction
from test_kill_common import (ADD, AND, COUNT, DIR, GATE_LEVEL, HALT, IN, JMP, JZ, LIMIT, LOAD, LOOP, MOV, NOP,
                              NOT, OR, OUT, PINS, PULL, PUSH, RX, SET, SHL, SHR, SIGNAL, TIME, TX, WAIT, WAITEVENT,
                              WAITPIN, X, XFER, XOR, Y, byte_lane, own, reload, run_drain)

REGS = (TX, RX, X, Y)


def setup(ones: bool) -> list[int]:
    words = [instruction(LOAD, X, 0, 0)] + ([instruction(NOT, X)] if ones else [])
    return words + [instruction(MOV, Y, X), instruction(MOV, TX, X), instruction(MOV, RX, X)]


CHECKSUM = [instruction(XOR, RX, X), instruction(XOR, RX, Y), instruction(XOR, RX, TX), instruction(PUSH)]


def kinds(engine: int) -> list[list[int]]:
    """One entry per instruction kind; destinations rotate over the four registers.
    Jump targets are patched to the next word by ``image``."""
    mine = 1 << engine
    out: list[list[int]] = [
        [instruction(NOP)], [immediate(SET, mine)], [immediate(DIR, mine)], [immediate(WAIT, 0)],
        [immediate(WAIT, 2)], [immediate(JMP, 0)], [instruction(OUT, engine, 0, 1)], [instruction(OUT, engine, 0, 0)],
        [instruction(IN, 7, 0, 0)], [instruction(IN, 7, 0, 1)], [immediate(COUNT, 2)],
        [immediate(COUNT, 1), immediate(LOOP, 0)], [immediate(LOOP, 0)], [immediate(LIMIT, 9)],
        [instruction(WAITPIN, 7, 0)], [immediate(SIGNAL, mine), instruction(WAITEVENT)],
        [immediate(PINS, engine | engine << 3 | 7 << 6)],
        [immediate(PINS, engine | engine << 3 | 7 << 6), instruction(XFER, 2, 1, 0b10010)],
        [instruction(JZ, X, 0, 0)], [instruction(PUSH)], [instruction(PULL)],
    ]
    for index, op in enumerate((MOV, ADD, XOR, AND, OR)):
        for dest in REGS:
            src = REGS[(dest + 1 + index) % 4]
            out.append([instruction(op, dest, src)])
    for index, dest in enumerate(REGS):
        out += [[instruction(LOAD, dest, 0x5A, 0xC3)], [instruction(SHL, dest, 0, 8)],
                [instruction(SHR, dest, 0, 16)], [instruction(NOT, dest)], [instruction(TIME, dest)]]
    return out


def image(cases: list[list[int]]) -> list[int]:
    words: list[int] = []
    for body in cases:
        for word in body:
            op = word >> 24
            target = len(words) + 1
            if op in (JMP, LOOP):
                word = word & ~0xFFFFFF | target
            elif op == JZ:
                word = word & ~0xFFFF | target
            words.append(word)
    return words + [instruction(HALT)]


async def run_cases(h: Harness, per_engine: list[list[list[int]]]) -> None:
    """Pack the cases into images of at most the program capacity and run them (TX
    prefilled with one word per PULL in the image)."""
    capacity = h.model.config.program_words
    index = 0
    total = len(per_engine[0])
    while index < total:
        size, count = 1, 0
        while index + count < total and size + len(per_engine[0][index + count]) <= capacity:
            size += len(per_engine[0][index + count])
            count += 1
        for engine in range(4):
            words = image(per_engine[engine][index:index + count])
            await reload(h, engine, words)
            pulls = sum(1 for w in words if w >> 24 == PULL)
            assert pulls <= h.model.config.fifo_words
            for _ in range(pulls):
                await h.write(2, 0x0F0F0F0F)
        await run_drain(h)
        index += count


async def hold_matrix(h: Harness) -> None:
    await h.start()
    h.pins = 0  # pin 7 low: WAITPIN 7,0 is satisfied
    for engine in range(4):
        await own(h, engine, 1 << engine)
    for ones in (False, True):
        per_engine = []
        for engine in range(4):
            per_engine.append([[*setup(ones), *body, *CHECKSUM] for body in kinds(engine)])
        await run_cases(h, per_engine)
    h.assert_no_faults()


async def shift_sweep(h: Harness) -> None:
    await h.start()
    counts = [0, 8, 16, 24] if byte_lane(h) else [0, 1, 2, 4, 8, 16, 31, 30, 29, 27, 23, 15, 3, 12]
    for hi, lo in ((0xFFFF, 0xFFFF), (0xA5C3, 0x965A)):
        base = [instruction(LOAD, X, hi >> 8, hi & 255), instruction(SHL, X, 0, 16),
                instruction(LOAD, Y, lo >> 8, lo & 255), instruction(OR, X, Y)]
        for op in (SHL, SHR):
            cases = []
            for index, c in enumerate(counts):  # the destination rotates over the four registers
                dest = (RX, X, Y, TX)[index % 4]
                if dest == X:  # x holds the pattern: save it in tx, shift x, push, restore
                    cases.append([instruction(MOV, TX, X), instruction(op, X, 0, c), instruction(MOV, RX, X),
                                  instruction(PUSH), instruction(MOV, X, TX)])
                else:
                    cases.append([instruction(MOV, dest, X), instruction(op, dest, 0, c)]
                                 + ([instruction(MOV, RX, dest)] if dest != RX else []) + [instruction(PUSH)])
            per_engine = [[base] + cases for _ in range(4)]
            await run_cases(h, per_engine)
    h.assert_no_faults()


def alu_setup() -> list[int]:
    """tx, rx, x, y = four distinct dense words."""
    return [instruction(LOAD, X, 0x5A, 0x3C), instruction(SHL, X, 0, 16), instruction(LOAD, Y, 0x0F, 0xF0),
            instruction(OR, X, Y), instruction(LOAD, Y, 0xC3, 0xA5), instruction(SHL, Y, 0, 16),
            instruction(LOAD, TX, 0x99, 0x66), instruction(OR, Y, TX), instruction(LOAD, TX, 0x33, 0xCC),
            instruction(NOT, TX), instruction(LOAD, RX, 0x7E, 0x81)]


def alu_ops(shift_counts: list[int]) -> list[int]:
    """Every two-register operation on every ordered register pair, NOT of every register and
    shifts, in an order that keeps the values dense (MOV, AND and OR alternate with ADD/XOR)."""
    ops = []
    pairs = [(d, s) for d in REGS for s in REGS if d != s]
    for index, (d, s) in enumerate(pairs):
        for op in (AND, ADD, OR, XOR, MOV) if index % 2 else (XOR, MOV, ADD, AND, OR):
            ops.append(instruction(op, d, s))
    for index, d in enumerate(REGS):
        ops += [instruction(NOT, d), instruction(SHL, d, 0, shift_counts[index % len(shift_counts)]),
                instruction(ADD, d, REGS[(index + 1) % 4]),
                instruction(SHR, d, 0, shift_counts[(index + 1) % len(shift_counts)])]
    return ops


async def alu_chain(h: Harness) -> None:
    """A chain of every ALU operation on every register pair with four distinct dense
    values; after every four operations rx ^= x, rx += y, rx ^= tx and rx is pushed. An
    operation mistaken for another (MOV for AND, say) or an operand bit dropped changes
    every later pushed word."""
    await h.start()
    counts = [8, 16] if byte_lane(h) else [5, 11, 8, 3]
    blocks = []
    ops = alu_ops(counts)
    for start in range(0, len(ops), 4):
        blocks.append(ops[start:start + 4] + [instruction(XOR, RX, X), instruction(ADD, RX, Y),
                                              instruction(XOR, RX, TX), instruction(PUSH)])
    capacity = h.model.config.program_words
    images, current = [], []
    for block in blocks:
        if len(alu_setup()) + len(current) + len(block) + 1 > capacity:
            images.append(alu_setup() + current + [instruction(HALT)])
            current = []
        current += block
    images.append(alu_setup() + current + [instruction(HALT)])
    for words in images:
        for engine in range(4):
            await reload(h, engine, words)
        await run_drain(h)
    h.assert_no_faults()


async def repeat_hold(h: Harness) -> None:
    """COUNT 2^k, then a WAIT 1 (k = 0..15), an XFER or a stalled PULL (k = 0, 7, 12, 15):
    the repeat counter must hold, so the LOOP that follows is taken (to a SET of the
    engine's pin)."""
    await h.start()
    h.pins = 0
    for engine in range(4):
        await own(h, engine, 3 << 2 * engine)
    for name, bits in (("WAIT", range(16)), ("XFER", (0, 7, 12, 15)), ("PULL", (0, 7, 12, 15))):
        for k in bits:
            for engine in range(4):
                p0 = 2 * engine
                hold = {"WAIT": [immediate(WAIT, 1)], "PULL": [instruction(PULL)],
                        "XFER": [immediate(PINS, p0), instruction(XFER, 1, 2, 0)]}[name]
                body = [immediate(DIR, 1 << p0), immediate(COUNT, 1 << k)] + hold
                target = len(body) + 2
                body += [immediate(LOOP, target), instruction(HALT), immediate(SET, 1 << p0), instruction(HALT)]
                await reload(h, engine, body)
            await h.command(START, 0b1111)
            if name == "PULL":
                await h.idle(6)
                for engine in range(4):
                    await h.command(SELECT, engine)
                    await h.write(2, k)
            await h.run_until(lambda: not any(e.running for e in h.model.engines), 60, "repeat hold")
    h.assert_no_faults()


async def time_high(h: Harness) -> None:
    """TIME into each register when the timestamp has all bits below bit k set (k = 13, 15,
    .., 31), each value then pushed."""
    await h.start()
    program = [instruction(TIME, d) for d in REGS] + [instruction(PUSH), instruction(MOV, RX, X), instruction(PUSH),
                                                      instruction(MOV, RX, Y), instruction(PUSH),
                                                      instruction(MOV, RX, TX), instruction(PUSH), instruction(HALT)]
    for engine in range(4):
        await reload(h, engine, program)
    await run_drain(h)
    for k in range(13, 33, 2):
        target = ((1 << k) - 1 - 0x2C0) & 0xFFFFFFFF  # bits 0..k-1 set except a few low ones
        await h.warp(target - h.model.timestamp)
        await run_drain(h)
    h.assert_no_faults()


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_hold_matrix(dut):
    """Every register, all-zeros and all-ones, observed after every kind of instruction."""
    await hold_matrix(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_shift_sweep(dut):
    """SHL/SHR of dense patterns by single-stage and all-but-one-stage counts."""
    await shift_sweep(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_alu_chain(dut):
    """Every ALU operation on every register pair with distinct dense values, every engine."""
    await alu_chain(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_repeat_hold(dut):
    """The repeat counter holds every bit across WAIT, XFER and a PULL stall."""
    await repeat_hold(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_time_high(dut):
    """TIME into every register with high timestamp bits set (time warp; RTL only)."""
    await time_high(CocotbHarness(dut))
