# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""An engine's output values and enables change only where an instruction writes them.

Specified behaviour (docs/isa.md):
- SET: "imm24 low8 sets logical pin values"; DIR: "imm24 low8 sets logical
  output enables".
- OUT: "a pin, b=0, c direction 0=LSB/right shift, 1=MSB/left shift; update
  that output bit and shift tx". Only the addressed bit changes.
- HALT: "stop and release output enables".
- "Outputs are masked by ownership, run, and fault"; "Reading any pin is
  legal".
- No other instruction writes the logical values or enables, whether it
  completes on its issue edge, holds (WAIT), or blocks: "WAITPIN ... consume
  one issue edge if already true; otherwise block", "WAITEVENT ... consume own
  pending mailbox bit or wait", "PULL ... block without changing state when
  empty", "PUSH a=0 block when full", "PULL/PUSH expose stalled status but do
  not drop data".

``out_one_bit``: each engine in turn owns all eight pins (push-pull) and
enables them all, then runs six blocks of SET followed by eight OUTs. Four
blocks write ones onto a low background and zeros onto a high background, in
ascending and in descending pin order, LSB-first and MSB-first; two more write
a mixed bit string onto 0x5A and 0xA5 in a scrambled pin order. For every pin p,
written value v, other pin q and level w, some OUT writes v to p while q holds w
(the test asserts this coverage on its own program).

``enables_hold``: each engine in turn owns all eight pins and, for each of
eight enable patterns, runs DIR E; SET V; and then one instruction of every
kind that does not write the pins (NOP, WAIT, COUNT/LOOP, LIMIT, LOAD, the ALU
operations, shifts, NOT, MOV, TIME, JZ taken and not taken, JMP, PINS, IN,
SIGNAL), two OUTs (which change only their own value bits), WAITEVENT
consuming a pending event and blocking until a host EVENT,
WAITPIN blocking until the test changes the pin and completing at once, PULL
blocking on an empty TX queue until the host writes, PUSH filling the RX queue
and blocking until the host reads, then HALT. The host releases each blocking
instruction after it has blocked for six cycles. The eight patterns give every
pair of enable bits all four value combinations (``BYTE_PATTERNS``); at gate
level the test runs 0x55 and 0xAA only.

Both tests compare the DUT's (uio_oe, uio_out) before every edge, with
consecutive repeats removed, with the sequence that the rules above give for
the program, computed here by a small interpreter of the instructions used
(``expected_pins``), not by the reference model. The test drives uio_in
itself (WAITPIN reads it); the pins the engine drives are not looped back.
"""

from __future__ import annotations

import cocotb

from harness import EVENT, FLUSH, START, Harness, immediate, instruction
from test_kill_common import (ADD, AND, COUNT, DIR, FAULT, GATE_LEVEL, HALT, IN, JMP, JZ, LIMIT, LOAD, LOOP, MOV, NOP,
                              NOT, OR, OUT, PINS, PULL, PUSH, RX, SET, SHL, SHR, SIGNAL, TIME, TX, WAIT, WAITEVENT,
                              WAITPIN, XOR, X, Y, own, reload)
from test_spec_common import BYTE_PATTERNS, PinTrace, harness, runs

HOLD = 6  # cycles each blocking instruction stays blocked before the host releases it
WAIT_PIN = 7  # the pin WAITPIN watches (driven by the test on uio_in)
GL_PATTERNS = [0x55, 0xAA]  # enable patterns of enables_hold at gate level


def expected_pins(program: list[int], width: int) -> list[tuple[int, int]]:
    """(uio_oe, uio_out) after each instruction of ``program``, run once from START by an
    engine that owns all eight pins push-pull, by the isa.md rules for SET, DIR, OUT, LOAD,
    NOT and HALT (every other opcode used here leaves the pins alone). Starts with the
    state after START (values and enables cleared) and ends with the HALT."""
    mask = (1 << width) - 1
    regs = [0, 0, 0, 0]
    values = enables = 0
    states = [(0, 0)]
    pc = 0
    while True:
        word = program[pc]
        op, a, c, imm16, imm24 = word >> 24, word >> 16 & 255, word & 255, word & 0xFFFF, word & 0xFFFFFF
        pc += 1
        if op == HALT:
            states.append((0, 0))  # released: outputs are masked by run
            return states
        if op == SET:
            values = imm24
        elif op == DIR:
            enables = imm24
        elif op == LOAD:
            regs[a] = imm16
        elif op == NOT:
            regs[a] ^= mask
        elif op == OUT:
            bit = regs[TX] >> (width - 1) & 1 if c else regs[TX] & 1
            regs[TX] = (regs[TX] << 1 if c else regs[TX] >> 1) & mask
            values = values & ~(1 << a) | bit << a
        elif op == JZ and regs[a] == 0:
            pc = imm16
        elif op == JMP:
            pc = imm24
        states.append((enables, values))


def out_program() -> list[int]:
    ones = [instruction(LOAD, TX, 0, 0), instruction(NOT, TX)]
    zeros = [instruction(LOAD, TX, 0, 0)]
    up, down = list(range(8)), list(range(7, -1, -1))
    program = [immediate(DIR, 0xFF)]
    for tx, background, order, msb in ((ones, 0x00, up, 0), (zeros, 0xFF, up, 1),
                                       (ones, 0x00, down, 1), (zeros, 0xFF, down, 0)):
        program += tx + [immediate(SET, background)] + [instruction(OUT, p, 0, msb) for p in order]
    for bits, background, order in ((0x9A5C, 0x5A, [3, 6, 0, 5, 2, 7, 1, 4]),
                                    (0x63A9, 0xA5, [5, 1, 7, 2, 4, 0, 6, 3])):
        program += [instruction(LOAD, TX, bits >> 8, bits & 255), immediate(SET, background)]
        program += [instruction(OUT, p, 0, 0) for p in order]
    return program + [instruction(HALT)]


def out_coverage(program: list[int], width: int) -> set[tuple[int, int, int, int]]:
    """(p, v, q, w): an OUT writes v to pin p while pin q != p holds w."""
    states = expected_pins(program, width)
    covered = set()
    for index, word in enumerate(program):
        if word >> 24 == OUT:
            before, after = states[index][1], states[index + 1][1]
            p = word >> 16 & 255
            for q in range(8):
                if q != p:
                    covered.add((p, after >> p & 1, q, before >> q & 1))
    return covered


# PCs of the blocking steps of hold_program (the PUSH that blocks is at BLOCKING_PUSH + fifo words)
BLOCKING = {"WAITEVENT": 29, "WAITPIN 1": 30, "WAITPIN 0": 32, "PULL": 33}
BLOCKING_PUSH = 35


def hold_program(engine: int, enables: int, values: int, fifo_words: int) -> list[int]:
    """Program of ``enables_hold``; ``run_traced`` releases its blocking steps (``BLOCKING``)."""
    program = [immediate(DIR, enables), immediate(SET, values), immediate(LIMIT, 1000), instruction(NOP),
               immediate(WAIT, 3), immediate(COUNT, 2), immediate(LOOP, 6),
               instruction(LOAD, X, 0x12, 0x34), instruction(ADD, X, X), instruction(XOR, Y, X),
               instruction(AND, Y, X), instruction(OR, Y, X), instruction(SHL, X, 0, 8), instruction(SHR, X, 0, 8),
               instruction(NOT, X), instruction(MOV, TX, X), instruction(TIME, Y), instruction(LOAD, Y, 0, 0),
               instruction(JZ, Y, 0, 20), immediate(FAULT, 0x77),  # JZ taken skips the FAULT
               instruction(JZ, X, 0, 0), immediate(JMP, 22),  # JZ not taken (x != 0)
               immediate(PINS, 5 | 6 << 3 | 7 << 6), instruction(IN, WAIT_PIN, 0, 0),
               instruction(LOAD, TX, 0, 0xA5), instruction(OUT, 3, 0, 0), instruction(OUT, 4, 0, 1),
               immediate(SIGNAL, 1 << engine), instruction(WAITEVENT),  # 28: consumes the pending event
               instruction(WAITEVENT),  # 29: blocks until the host's EVENT
               instruction(WAITPIN, WAIT_PIN, 1),  # 30: blocks until the pin rises
               instruction(WAITPIN, WAIT_PIN, 1),  # 31: already true
               instruction(WAITPIN, WAIT_PIN, 0),  # 32: blocks until the pin falls
               instruction(PULL),  # 33: blocks until the host writes a TX word
               instruction(LOAD, RX, 0x5A, engine)]
    program += [instruction(PUSH)] * fifo_words  # 35 ..: fill the RX queue
    program += [instruction(PUSH), instruction(HALT)]  # blocks until the host reads a word
    assert program[BLOCKING["WAITEVENT"]] == program[28] == instruction(WAITEVENT)
    assert program[BLOCKING["PULL"]] == instruction(PULL)
    assert program[BLOCKING_PUSH - 1] == instruction(LOAD, RX, 0x5A, engine)
    return program


async def released(h: Harness, engine: int, pc: int, what: str) -> None:
    """Idle until ``engine`` has stood blocked at ``pc`` for HOLD cycles."""
    e = h.model.engines[engine]
    await h.run_until(lambda: e.pc == pc and e.stalled, 400, what)
    await h.idle(HOLD)
    assert e.pc == pc and e.stalled, f"engine {engine} left {what} on its own"


async def run_traced(h: Harness, trace: PinTrace, engine: int, program: list[int], blocking: bool) -> list:
    """Load ``program`` on ``engine``, START it, release its blocking steps (``blocking``),
    and return the (uio_oe, uio_out) runs the DUT showed from START to after the HALT."""
    await reload(h, engine, program)
    trace.start()
    await h.command(START, 1 << engine)
    if blocking:
        fifo_words = h.model.config.fifo_words
        await released(h, engine, BLOCKING["WAITEVENT"], "WAITEVENT")
        await h.command(EVENT, 1 << engine)
        await released(h, engine, BLOCKING["WAITPIN 1"], "WAITPIN 1")
        h.pins = 1 << WAIT_PIN
        await released(h, engine, BLOCKING["WAITPIN 0"], "WAITPIN 0")
        h.pins = 0
        await released(h, engine, BLOCKING["PULL"], "PULL")
        await h.write(2, 0xC0DE0000 | engine)
        await released(h, engine, BLOCKING_PUSH + fifo_words, "PUSH")
        await h.read(3)
    await h.run_until(lambda: not h.model.engines[engine].running, 400, "HALT")
    await h.idle(2)
    seen = runs([(oe, out) for _, out, oe in trace.stop()])
    assert not h.model.engines[engine].fault, f"engine {engine} faulted"
    if blocking:
        await h.command(FLUSH)  # the RX queue still holds fifo_words words
    return seen


async def out_one_bit(h: Harness) -> int:
    await h.start()
    h.pins = 0
    width = h.model.config.width
    program = out_program()
    assert len(program) <= h.model.config.program_words
    covered = out_coverage(program, width)
    assert len(covered) == 8 * 2 * 7 * 2, f"OUT program covers {len(covered)} of 224 (p, v, q, w)"
    expected = runs(expected_pins(program, width))
    trace = PinTrace()
    h.observers.append(trace)
    for engine in range(4):
        await own(h, engine, 0xFF)
        seen = await run_traced(h, trace, engine, program, blocking=False)
        assert seen == expected, (f"engine {engine}: (uio_oe, uio_out) runs differ from isa.md\n"
                                  f"  seen     {[(f'{a:02x}', f'{b:02x}') for a, b in seen]}\n"
                                  f"  expected {[(f'{a:02x}', f'{b:02x}') for a, b in expected]}")
        await own(h, engine, 0)
    h.observers.remove(trace)
    h.assert_no_faults()
    return len(expected)


async def enables_hold(h: Harness, patterns: list[int] = BYTE_PATTERNS) -> int:
    await h.start()
    h.pins = 0
    width = h.model.config.width
    trace = PinTrace()
    h.observers.append(trace)
    rounds = 0
    for engine in range(4):
        await own(h, engine, 0xFF)
        for index, enables in enumerate(patterns):
            values = patterns[(index + 2) % len(patterns)]
            program = hold_program(engine, enables, values, h.model.config.fifo_words)
            expected = runs(expected_pins(program, width))
            seen = await run_traced(h, trace, engine, program, blocking=True)
            assert seen == expected, (f"engine {engine}, DIR {enables:02x}, SET {values:02x}: (uio_oe, uio_out) "
                                      f"runs {[(f'{a:02x}', f'{b:02x}') for a, b in seen]}, isa.md gives "
                                      f"{[(f'{a:02x}', f'{b:02x}') for a, b in expected]}")
            rounds += 1
        await own(h, engine, 0)
    h.observers.remove(trace)
    h.assert_no_faults()
    return rounds


@cocotb.test()
async def test_spec_out_writes_only_its_pin(dut):
    """OUT changes only the addressed output bit, for every pin, value and level of every other pin, on every engine."""
    h = harness(dut)
    states = await out_one_bit(h)
    h.log("OUT: %d pin states per engine as isa.md gives them (%d cycles)", states, h.cycle)


@cocotb.test()
async def test_spec_enables_hold_through_every_instruction(dut):
    """Enables and values from DIR/SET hold through every non-writing instruction, wait and stall; HALT releases."""
    h = harness(dut)
    rounds = await enables_hold(h, GL_PATTERNS if GATE_LEVEL else BYTE_PATTERNS)
    h.log("enables and values held in %d rounds (%d cycles)", rounds, h.cycle)
