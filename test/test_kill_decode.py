# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Instruction operand checks: every field bit that must be zero or bounded.

docs/isa.md fixes, per opcode, which operand fields must be zero, which are
bounded (register numbers < 4, pins < 8, shift counts < datapath width, SIGNAL
masks below the engine count, ...) and which bits need pin ownership. An
instruction that breaks any of them faults with code 1 on issue. The random
test draws deliberate faults, but mostly with small field values, so a decoder
that ignored one high bit of one field on one engine went unnoticed (mutation
survivors in the ``events`` and ``engine_ctrl`` regions, docs/mutation-push.md).

``operand_sweep`` issues, on each of the four engines, one invalid instruction
per (opcode, field, bit): the field value has exactly that bit set (or the
first value past the bound) and every other field is valid, so the swept bit
is the only reason for the fault; likewise every opcode with bit 5, 6 or 7
added. Each image is a DIR of the engine's own pin followed by the case, so
the pin's output enable drops on the exact cycle of that engine's fault (the
shared fault pin would hide one engine behind the others); the four engines
run their cases from one START and are then cleared. The model decides the
expected fault (asserted to be code 1 for every case), and the fault code is
read back for one case per opcode. ``ownership_sweep`` varies the owned pins;
``fault_codes`` runs FAULT n with twelve codes on every engine and checks that
START is rejected while the fault is pending.
"""

from __future__ import annotations

import cocotb

from harness import CLEAR, RS_STATUS, SELECT, START, CocotbHarness, Harness, immediate, instruction
from scenarios import fault_of
from test_kill_common import (ADD, AND, COUNT, DIR, FAULT, GATE_LEVEL, HALT, IN, JZ, LIMIT, LOAD, MOV, NOP, NOT,
                              OR, OUT, PINS, PULL, PUSH, SET, SHL, SHR, SIGNAL, TIME, WAITEVENT, WAITPIN, XFER,
                              XOR, own, reload)

BYTE_BITS = range(8)


def owned_pins(engine: int) -> tuple[int, int]:
    """Engine e owns pins 2e and 2e+1 during the sweep."""
    return 2 * engine, 2 * engine + 1


def invalid_cases(engine: int, width: int) -> list[tuple[str, list[int]]]:
    """(label, image) for every single-bit operand violation on ``engine``; the image
    ends with the invalid instruction (XFER cases first select owned pins with PINS)."""
    p0, p1 = owned_pins(engine)
    cases: list[tuple[str, list[int]]] = []

    marker = immediate(DIR, 1 << p0)  # enables the engine's own pin until the fault releases it

    def add(label: str, word: int, prefix: tuple[int, ...] = ()) -> None:
        cases.append((label, [marker, *prefix, word]))

    # imm24 == 0: NOP (every bit), HALT/PULL/WAITEVENT (low, middle and top bit)
    for k in range(24):
        add(f"NOP bit{k}", immediate(NOP, 1 << k))
    for op, name in ((HALT, "HALT"), (PULL, "PULL"), (WAITEVENT, "WAITEVENT")):
        for k in (0, 11, 23):
            add(f"{name} bit{k}", immediate(op, 1 << k))
    # PUSH: a in {0,1}; b = c = 0
    for k in range(1, 8):
        add(f"PUSH a bit{k}", instruction(PUSH, 1 << k))
    for k in BYTE_BITS:
        add(f"PUSH b bit{k}", instruction(PUSH, 0, 1 << k))
        add(f"PUSH c bit{k}", instruction(PUSH, 0, 0, 1 << k))
    # SET/DIR: imm24 bits 23..8 zero; low8 inside ownership
    for k in range(8, 24):
        add(f"SET bit{k}", immediate(SET, 1 << k | 1 << p0))
        add(f"DIR bit{k}", immediate(DIR, 1 << k | 1 << p0))
    for pin in range(8):
        if pin not in (p0, p1):
            add(f"SET unowned pin{pin}", immediate(SET, 1 << pin | 1 << p0))
            add(f"DIR unowned pin{pin}", immediate(DIR, 1 << pin | 1 << p1))
    # OUT/IN: a pin < 8, b = 0, c in {0,1}; OUT needs the pin owned
    for op, name in ((OUT, "OUT"), (IN, "IN")):
        for k in range(3, 8):
            add(f"{name} a bit{k}", instruction(op, 1 << k | (p0 if op == OUT else 0)))
        for k in BYTE_BITS:
            add(f"{name} b bit{k}", instruction(op, p0, 1 << k))
        for k in range(1, 8):
            add(f"{name} c bit{k}", instruction(op, p0, 0, 1 << k))
    for pin in range(8):
        if pin not in (p0, p1):
            add(f"OUT unowned pin{pin}", instruction(OUT, pin, 0, 1))
    # COUNT: a = 0
    for k in BYTE_BITS:
        add(f"COUNT a bit{k}", instruction(COUNT, 1 << k, 0, 5))
    add("LIMIT 0", immediate(LIMIT, 0))
    # WAITPIN: a < 8, b in {0,1}, c = 0
    for k in range(3, 8):
        add(f"WAITPIN a bit{k}", instruction(WAITPIN, 1 << k, 1))
    for k in range(1, 8):
        add(f"WAITPIN b bit{k}", instruction(WAITPIN, p0, 1 << k))
    for k in BYTE_BITS:
        add(f"WAITPIN c bit{k}", instruction(WAITPIN, p0, 1, 1 << k))
    # SIGNAL: imm24 below the engine count (four engines: bits 4..23 zero)
    for k in range(4, 24):
        add(f"SIGNAL bit{k}", immediate(SIGNAL, 1 << k))
    # PINS: imm24 bits 23..9 zero
    for k in range(9, 24):
        add(f"PINS bit{k}", immediate(PINS, 1 << k))
    # XFER: a in 1..width, b != 0, c < 32, clock pin owned, and when driving the TX pin
    # owned and different from the clock pin (PINS first: clock p0, TX p1, RX p0)
    pins = (immediate(PINS, p0 | p1 << 3 | p0 << 6),)
    for a in (0, 64, 128, *(width | 1 << k for k in range(5))):
        add(f"XFER a={a}", instruction(XFER, a, 1, 0), pins)
    add("XFER b=0", instruction(XFER, 1, 0, 0), pins)
    for k in (5, 6, 7):
        add(f"XFER c bit{k}", instruction(XFER, 1, 1, 1 << k), pins)
    other = (p1 + 1) % 8
    add("XFER clock unowned", instruction(XFER, 1, 1, 0), (immediate(PINS, other | p1 << 3),))
    add("XFER drive TX unowned", instruction(XFER, 1, 1, 8), (immediate(PINS, p0 | other << 3),))
    add("XFER drive TX = clock", instruction(XFER, 1, 1, 8), (immediate(PINS, p0 | p0 << 3),))
    # two-register ALU and MOV: a < 4, b < 4, c = 0
    for op, name in ((MOV, "MOV"), (ADD, "ADD"), (XOR, "XOR"), (AND, "AND"), (OR, "OR")):
        for k in range(2, 8):
            add(f"{name} a bit{k}", instruction(op, 1 << k, 1))
            add(f"{name} b bit{k}", instruction(op, 1, 1 << k))
        for k in BYTE_BITS:
            add(f"{name} c bit{k}", instruction(op, 1, 2, 1 << k))
    # LOAD and JZ: a < 4
    for op, name in ((LOAD, "LOAD"), (JZ, "JZ")):
        for k in range(2, 8):
            add(f"{name} a bit{k}", instruction(op, 1 << k, 0, 0))
    # SHL/SHR: a < 4, b = 0, c < width
    for op, name in ((SHL, "SHL"), (SHR, "SHR")):
        for k in range(2, 8):
            add(f"{name} a bit{k}", instruction(op, 1 << k, 0, 8))
        for k in BYTE_BITS:
            add(f"{name} b bit{k}", instruction(op, 1, 1 << k, 8))
        for c in (width, 64, 128, *(width | 1 << k for k in range(5))):
            add(f"{name} c={c}", instruction(op, 1, 0, c))
    # NOT/TIME: a < 4, b = c = 0
    for op, name in ((NOT, "NOT"), (TIME, "TIME")):
        for k in range(2, 8):
            add(f"{name} a bit{k}", instruction(op, 1 << k))
        for k in BYTE_BITS:
            add(f"{name} b bit{k}", instruction(op, 1, 1 << k))
            add(f"{name} c bit{k}", instruction(op, 1, 0, 1 << k))
    # FAULT: imm24 in 1..255
    add("FAULT 0", immediate(FAULT, 0))
    for k in range(8, 24):
        add(f"FAULT bit{k}", immediate(FAULT, 1 << k | 1))
    # opcodes past FAULT, and every opcode with bit 5, 6 or 7 added
    for op in (30, 31, 255):
        add(f"opcode {op}", instruction(op))
    for op in range(32):
        for k in (5, 6, 7):
            add(f"opcode {op | 1 << k}", instruction(op | 1 << k))
    return cases


async def operand_sweep(h: Harness) -> None:
    await h.start()
    width = h.model.config.width
    for engine in range(4):
        await own(h, engine, sum(1 << p for p in owned_pins(engine)))
    per_engine = [invalid_cases(engine, width) for engine in range(4)]
    rounds = max(len(c) for c in per_engine)
    checked: set[str] = set()
    for index in range(rounds):
        mask = 0
        for engine in range(4):
            if index < len(per_engine[engine]):
                await reload(h, engine, per_engine[engine][index][1])
                mask |= 1 << engine
        await h.command(START, mask)
        await h.idle(4)
        for engine in range(4):
            if mask >> engine & 1:
                label = per_engine[engine][index][0]
                assert h.model.engines[engine].fault == 1, (engine, label, h.model.engines[engine].fault)
        # read the fault code back for the first case of each opcode (one engine per case)
        label = per_engine[index % 4][index][0] if index < len(per_engine[index % 4]) else ""
        name = label.split(" ")[0]
        if label and name not in checked:
            checked.add(name)
            await h.command(SELECT, index % 4)
            assert fault_of(await h.status(RS_STATUS)) == 1, label
        await h.command(CLEAR, mask)
    h.assert_no_faults()


async def ownership_sweep(h: Harness) -> None:
    """Each engine in turn owns every pin but q: SET/DIR of pin q, OUT q, XFER with its clock
    on q and a driving XFER with its TX pin on q fault with code 1. Owning q and its
    neighbour, the same instructions run without a fault."""
    await h.start()
    for engine in range(4):
        for q in range(8):
            n = (q + 1) % 8
            await own(h, engine, 0xFF & ~(1 << q))
            for image in ([immediate(SET, 1 << q)], [immediate(DIR, 1 << q)], [instruction(OUT, q, 0, 0)],
                          [immediate(PINS, q | n << 3), instruction(XFER, 1, 1, 0)],
                          [immediate(PINS, n | q << 3), instruction(XFER, 1, 1, 8)]):
                await reload(h, engine, image)
                await h.command(START, 1 << engine)
                await h.idle(len(image) + 1)
                assert h.model.engines[engine].fault == 1, (engine, q, [hex(w) for w in image])
                await h.command(CLEAR, 1 << engine)
            await own(h, engine, 1 << q | 1 << n)
            await reload(h, engine, [immediate(SET, 1 << q), immediate(DIR, 1 << q), instruction(OUT, q, 0, 1),
                                     immediate(PINS, q | n << 3), instruction(XFER, 1, 1, 0),
                                     immediate(PINS, n | q << 3), instruction(XFER, 2, 1, 8), immediate(HALT, 0)])
            await h.command(START, 1 << engine)
            await h.run_until(lambda e=engine: not h.model.engines[e].running, 40, "ownership program")
            assert not h.model.engines[engine].fault, (engine, q)
        await own(h, engine, 0)
    h.assert_no_faults()


async def fault_codes(h: Harness) -> None:
    """FAULT n on every engine stops it with code n (read back through READ_SELECT 0)."""
    await h.start()
    codes = [1, 2, 4, 8, 16, 32, 64, 128, 255, 0x5A, 0xA5, 3]
    for index in range(len(codes)):  # every engine reports every code
        chosen = [codes[(index + e) % len(codes)] for e in range(4)]
        for engine, code in enumerate(chosen):
            await reload(h, engine, [immediate(FAULT, code)])
        await h.command(START, 0b1111)
        await h.idle(2)
        for engine, code in enumerate(chosen):
            await h.command(SELECT, engine)
            assert fault_of(await h.status(RS_STATUS)) == code, (engine, code)
            await h.command(START, 1 << engine)  # rejected while the fault is pending
            assert h.model.host_fault and not h.model.engines[engine].running, (engine, code)
            await h.command(CLEAR, 1 << 23)
        await h.command(CLEAR, 0b1111)
    h.assert_no_faults()


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_ownership_sweep(dut):
    """Pin-ownership checks of SET, DIR, OUT and XFER against every single unowned pin."""
    await ownership_sweep(CocotbHarness(dut))


@cocotb.test()
async def test_kill_fault_codes(dut):
    """FAULT n reports code n on every engine."""
    await fault_codes(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_operand_sweep(dut):
    """Every operand field bit that must be zero or bounded faults with code 1, on every engine."""
    await operand_sweep(CocotbHarness(dut))
