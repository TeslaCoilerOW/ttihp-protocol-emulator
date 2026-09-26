# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Host commands: every payload bit that must be zero, OWN overlaps, image lengths.

Every host command rejects atomically, with the sticky host fault (uo7), when
its payload breaks the command's rule (docs/isa.md, "Host interface"). The
random test sends each command both accepted and rejected (generator 2), but
with few distinct payload bits, and OWN overlaps were tested on one pin of one
engine; image lengths other than the full capacity never ran off their end on
most engines (``host`` survivors, docs/mutation-push.md).

``command_sweep``: each command with each payload bit that must be zero set on
its own (and the first value past each bound), then CLEAR with bit 23 to clear
the host fault; opcodes 12 and above, and every opcode with bit 4..7 added. ``own_matrix``: engine k owns pin p; every
other engine's OWN of pin p is rejected, and an OWN of the other seven pins is
accepted. ``image_lengths``: on every engine, NOP images of 64, 47, 33 and 17
words run off their end and fault with code 2 at PC = length.
``image_end_ops``: full-capacity images ending in COUNT, LIMIT, PINS or TIME.
``paused_reads``: status words read with one- and two-cycle pauses between
nibbles (the presented nibble is compared while read-valid is high).
"""

from __future__ import annotations

import cocotb

from harness import (BEGIN, CLEAR, COMMIT, EVENT, FLUSH, OWN, READ_SELECT, ROUTE, RS_PC, RS_STATUS, SELECT, START,
                     STOP, TRIGGER, UO_FAULT, CocotbHarness, Harness, immediate, instruction)
from scenarios import fault_of
from test_kill_common import COUNT, GATE_LEVEL, HALT, LIMIT, NOP, PINS, TIME, WAIT, own, reload

HOST_FAULT_CLEAR = 1 << 23


def rejected_payloads(engines: int, capacity: int) -> list[tuple[int, int]]:
    """(opcode, payload) pairs that every design of record rejects in the prepared state
    (engine 0 selected, halted, image committed, not loading)."""
    cases: list[tuple[int, int]] = []
    for k in range(2, 24):
        cases.append((SELECT, 1 << k))
    for k in range(24):
        cases.append((BEGIN, 1 << k))
        cases.append((FLUSH, 1 << k))
    for k in range(0, 24):  # not loading: every COMMIT is rejected; lengths past capacity too
        cases.append((COMMIT, 1 << k))
    cases.append((COMMIT, capacity + 1))
    for k in range(16, 24):
        cases.append((OWN, 1 << k))
    for k in range(8, 16):
        cases.append((OWN, 1 << k))  # open-drain bit outside ownership
    for op in (START, STOP, EVENT):
        for k in range(engines, 24):
            cases.append((op, 1 << k))
    for k in range(engines, 23):
        cases.append((CLEAR, 1 << k))
    for k in range(21, 24):
        cases.append((ROUTE, 1 << k | 0x10 | 5 << 5))
    for k in range(3, 24):
        cases.append((READ_SELECT, 1 << k))
    for k in range(6, 24):
        cases.append((TRIGGER, 1 << k))
    for op in (12, 13, 15, 255):
        cases.append((op, 0))
    for op in range(12):  # every command opcode with bit 4, 5, 6 or 7 added
        for k in range(4, 8):
            cases.append((op | 1 << k, 0))
    return cases


async def command_sweep(h: Harness) -> None:
    await h.start()
    await reload(h, 0, [instruction(HALT)])  # engine 0: committed, halted
    await h.command(SELECT, 0)
    for opcode, payload in rejected_payloads(h.model.config.engines, h.model.config.program_words):
        await h.command(opcode, payload)
        assert h.model.host_fault and h.expected.uo & UO_FAULT, (opcode, hex(payload))
        await h.command(CLEAR, HOST_FAULT_CLEAR)
        assert not h.model.host_fault
    # COMMIT while loading three words: every length but 3 is rejected
    for k in range(24):
        await h.command(SELECT, 3)
        await h.command(BEGIN)
        for word in range(3):
            await h.write(1, instruction(NOP))
        length = 1 << k if 1 << k != 3 else 7
        await h.command(COMMIT, length)
        assert h.model.host_fault, k
        await h.command(CLEAR, HOST_FAULT_CLEAR)
        await h.command(COMMIT, 3 | (1 << k if k >= 16 else 0))
        assert h.model.engines[3].committed == (k < 16), k
        await h.command(CLEAR, HOST_FAULT_CLEAR)
    # START of an engine without a committed image, or with a fault; CLEAR of a running engine
    await h.command(SELECT, 1)
    await h.command(BEGIN)
    await h.command(START, 0b0010)
    assert h.model.host_fault
    await h.command(CLEAR, HOST_FAULT_CLEAR)
    await reload(h, 2, [immediate(WAIT, 200), instruction(HALT)])
    await h.command(START, 0b0100)
    for opcode, payload in ((CLEAR, 0b0100), (OWN, 1), (FLUSH, 0), (TRIGGER, 0x21), (BEGIN, 0), (COMMIT, 2)):
        await h.command(SELECT, 2)
        running = h.model.engines[2].running
        await h.command(opcode, payload)
        if running and opcode != BEGIN:
            assert h.model.host_fault, (opcode, payload)
        await h.command(CLEAR, HOST_FAULT_CLEAR)
    h.assert_no_faults()


async def own_matrix(h: Harness) -> None:
    await h.start()
    for owner in range(4):
        for pin in range(8):
            await own(h, owner, 1 << pin)
            for other in range(4):
                if other == owner:
                    continue
                await h.command(SELECT, other)
                await h.command(OWN, 1 << pin)
                assert h.model.host_fault, (owner, pin, other)
                await h.command(CLEAR, HOST_FAULT_CLEAR)
            other = (owner + 1 + pin) % 4
            other = other if other != owner else (owner + 1) % 4
            await h.command(SELECT, other)
            await h.command(OWN, 0xFF & ~(1 << pin))
            assert not h.model.host_fault and h.model.engines[other].ownership == 0xFF & ~(1 << pin)
            await h.command(OWN, 0)
        await own(h, owner, 0)
    h.assert_no_faults()


async def image_lengths(h: Harness) -> None:
    await h.start()
    capacity = h.model.config.program_words
    lengths = [n for n in (64, 47, 33, 17) if n <= capacity]
    for index in range(0, len(lengths), 1):
        chosen = [lengths[(index + e) % len(lengths)] for e in range(4)]
        for engine, length in enumerate(chosen):
            await reload(h, engine, [instruction(NOP)] * length)
        await h.command(START, 0b1111)
        await h.run_until(lambda: not any(e.running for e in h.model.engines), capacity + 40, "run off the end")
        for engine, length in enumerate(chosen):
            await h.command(SELECT, engine)
            assert fault_of(await h.status(RS_STATUS)) == 2
            assert await h.status(RS_PC) == length
        await h.command(CLEAR, 0b1111)
    h.assert_no_faults()


async def image_end_ops(h: Harness) -> None:
    """Full-capacity images whose last word is COUNT, LIMIT, PINS or TIME (each engine takes
    each in turn): the PC advances past the last word to the capacity, and the engine faults
    with code 2 there."""
    await h.start()
    capacity = h.model.config.program_words
    last = [immediate(COUNT, 5), immediate(LIMIT, 9), immediate(PINS, 0), instruction(TIME, 2)]
    for index in range(4):
        for engine in range(4):
            await reload(h, engine, [instruction(NOP)] * (capacity - 1) + [last[(index + engine) % 4]])
        await h.command(START, 0b1111)
        await h.run_until(lambda: not any(e.running for e in h.model.engines), capacity + 40, "run off the end")
        for engine in range(4):
            await h.command(SELECT, engine)
            assert await h.status(RS_PC) == capacity
        await h.command(CLEAR, 0b1111)
    h.assert_no_faults()


async def paused_reads(h: Harness) -> None:
    await h.start()
    await reload(h, 1, [instruction(HALT)])
    await h.command(START, 0b0010)
    await h.idle(4)
    for selection in range(8):
        await h.command(READ_SELECT, selection)
        for engine in range(4):
            await h.command(SELECT, engine)
            await h.set_window(1)
            await h.read(0, pauses=(1, 2, 1, 0, 2, 1, 3, 1))
    h.assert_no_faults()


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_command_sweep(dut):
    """Every host command with each must-be-zero payload bit set is rejected (host fault)."""
    await command_sweep(CocotbHarness(dut))


@cocotb.test()
async def test_kill_own_matrix(dut):
    """OWN of a pin another engine owns is rejected, for every engine pair and pin."""
    await own_matrix(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_image_lengths(dut):
    """Images of 1..64 words run off their end on every engine (fault 2, PC = length)."""
    await image_lengths(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_image_end_ops(dut):
    """Full images ending in COUNT, LIMIT, PINS or TIME run off their end at PC = capacity."""
    await image_end_ops(CocotbHarness(dut))


@cocotb.test()
async def test_kill_paused_reads(dut):
    """Status reads with pauses between nibbles, every READ_SELECT, every engine."""
    await paused_reads(CocotbHarness(dut))
