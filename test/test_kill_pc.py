# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The PC between a far jump and its fault, caught by a STOP on the exact edge.

A JMP, LOOP or JZ to a target outside the image writes the whole target into
the PC; the next issue edge faults with code 2 and holds the PC.
``test_directed.jump_targets`` reads the PC after the fault. A mutant that
corrupts some PC bits on the jump and flips them back on the fault edge (both
writes pass through the same PC multiplexer), or that corrupts the value only
on the jump, is invisible there (survivors on ``pc``, docs/mutation-push.md).

``far_jump_stop``: all four engines jump on the same edge; the host has sent
seven nibbles of STOP and sends the eighth so that STOP is accepted on the
edge after the jump. A STOP takes priority over execution, so the engines halt
with the jump target in the PC and no fault, and READ_SELECT 3 reads it. Each
of JMP, LOOP (taken) and JZ (taken) runs with every target bit 6..23 (JZ: its
16-bit field) and with dense bit patterns.
"""

from __future__ import annotations

import cocotb

from harness import (CLEAR, OWN, READ_SELECT, RS_PC, SELECT, START, STOP, UI_WVALID, UO_WREADY, CocotbHarness,
                     Harness, immediate, instruction)
from test_kill_common import COUNT, DIR, GATE_LEVEL, HALT, JMP, JZ, LOOP, SET, WAIT, X, options, reload


async def command_at(h: Harness, opcode: int, payload: int, when) -> None:  # noqa: ANN001
    """Send a command whose eighth nibble is accepted on the first edge at which
    ``when()`` holds (checked before each edge, on the model)."""
    word = opcode << 24 | payload
    await h.set_window(0)
    for nibble in range(8):
        if nibble == 7:
            await h.run_until(when, 400, "command timing point")
        ui = UI_WVALID | (word >> (4 * nibble) & 15)
        for _ in range(100):
            if (await h.step(ui)).uo & UO_WREADY:
                break
        else:
            raise TimeoutError("command nibble not accepted")
    await h.step()


def targets(kind: str) -> list[int]:
    bits = range(6, 16) if kind == "JZ" else range(6, 24)
    mask = 0xFFFF if kind == "JZ" else 0xFFFFFF
    dense = [0xFFFFC0, 0xAAAAAA, 0x555555 & ~0x3F | 0x40, 0xF0F0C0, 0x0F0FC0]
    return [1 << k for k in bits] + [d & mask for d in dense]


async def far_jump_stop(h: Harness) -> None:
    await h.start()
    saturating = getattr(options(h), "pc_bits", "full") == "saturating_7"
    for kind in ("JMP", "LOOP", "JZ"):
        for base in targets(kind):
            want = [base | (3 * engine + 1) for engine in range(4)]
            for engine, target in enumerate(want):
                jump = {"JMP": [immediate(JMP, target)],
                        "LOOP": [immediate(COUNT, 1), immediate(LOOP, target)],
                        "JZ": [instruction(JZ, X, target >> 8, target & 255)]}[kind]
                await reload(h, engine, [immediate(WAIT, 24 - len(jump))] + jump)
            await h.command(START, 0b1111)
            jumped = lambda: all(e.pc == (min(t, 127) if saturating else t) and e.running  # noqa: E731
                                 for e, t in zip(h.model.engines, want, strict=True))
            await command_at(h, STOP, 0b1111, jumped)
            await h.command(READ_SELECT, RS_PC)
            for engine, target in enumerate(want):
                await h.command(SELECT, engine)
                await h.set_window(1)  # drop the snapshot taken before SELECT
                assert await h.read(0) == (min(target, 127) if saturating else target), (kind, engine, hex(target))
            assert not any(e.fault or e.running for e in h.model.engines)
    h.assert_no_faults()


async def far_jump_alias(h: Harness) -> None:
    """A far target whose low bits name a word inside the image: JMP (1 << k) | 3 in an
    eight-word image faults with code 2; a PC range check that ignored bit k would run
    word 3 (SET) instead. k = 6..23 (the SRAM address is the PC's low six bits)."""
    await h.start()
    saturating = getattr(options(h), "pc_bits", "full") == "saturating_7"
    for engine in range(4):
        await h.command(SELECT, engine)
        await h.command(OWN, 1 << engine)
    for k in range(6, 24):
        for engine in range(4):
            image = [immediate(DIR, 1 << engine), immediate(JMP, 1 << k | 3), immediate(HALT, 0),
                     immediate(SET, 1 << engine), immediate(HALT, 0), immediate(HALT, 0), immediate(HALT, 0),
                     immediate(HALT, 0)]
            await reload(h, engine, image)
        await h.command(START, 0b1111)
        await h.idle(5)
        for engine in range(4):
            assert h.model.engines[engine].fault == 2, (k, engine)
            assert h.model.engines[engine].pc == (min(1 << k | 3, 127) if saturating else 1 << k | 3)
        await h.command(CLEAR, 0b1111)
    h.assert_no_faults()


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_far_jump_alias(dut):
    """JMP to (1 << k) | 3 in an eight-word image faults with code 2 (k = 6..23)."""
    await far_jump_alias(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_far_jump_stop(dut):
    """JMP/LOOP/JZ to far targets; STOP on the next edge keeps the target in the PC (no fault)."""
    await far_jump_stop(CocotbHarness(dut))
