# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Reset, identification and program-loader tests (lockstep-checked every cycle)."""

from __future__ import annotations

import cocotb

from harness import (BEGIN, CLEAR, COMMIT, EVENT, FLUSH, ISA_VERSION, OWN, ROUTE, RS_COUNT, RS_EVENT,
                     RS_HELD_RX, RS_LEVELS, RS_PC, RS_STATUS, RS_TIMESTAMP, RS_VERSION, SELECT, START,
                     STOP, TRIGGER, UO_FAULT, UO_IRQ, UO_WREADY, CocotbHarness, LockstepMismatch,
                     immediate, instruction)

# Opcodes used below (isa.md).
NOP, HALT, SET, DIR, WAIT, JMP, PULL, PUSH = range(8)
LOAD, ADD, MOV, TIME = 19, 20, 18, 28


@cocotb.test()
async def test_reset_and_version(dut):
    """Outputs are released in reset; ISA version reads 2; status/timestamp sane."""
    h = CocotbHarness(dut)
    await h.start()
    for _ in range(3):
        await h.step(0, reset=True)
    assert h.expected.uo == 0 and h.expected.uio_oe == 0
    # Window 0 is write-ready right after reset, read-valid follows capture.
    pre = await h.step(0)
    assert pre.uo & UO_WREADY, f"write-ready low after reset: uo={pre.uo:02x}"
    assert not pre.uo & UO_FAULT and not pre.uo & UO_IRQ
    assert await h.status(RS_VERSION) == ISA_VERSION
    for engine in range(4):
        await h.command(SELECT, engine)
        assert await h.status(RS_STATUS) == 0  # halted, no image, no fault
        assert await h.status(RS_LEVELS) == 0
    t0 = await h.status(RS_TIMESTAMP)
    t1 = await h.status(RS_TIMESTAMP)
    assert t1 > t0, (t0, t1)
    # Deselection (ena low) behaves as reset.
    await h.step(enabled=False)
    await h.step(enabled=False)
    assert h.expected.uio_oe == 0 and h.expected.uo == 0
    assert await h.status(RS_VERSION) == ISA_VERSION
    dut._log.info("reset/version OK after %d cycles", h.cycle)


@cocotb.test()
async def test_program_load_commit_start(dut):
    """BEGIN/OWN/program words/COMMIT/START/STOP and the status registers."""
    h = CocotbHarness(dut)
    await h.start()
    # Engine 1: drive pin 1 high, count, push rx, halt.
    program = [immediate(DIR, 0x02), immediate(SET, 0x02), instruction(LOAD, 1, 0x12, 0x34),
               instruction(PUSH), immediate(WAIT, 5), instruction(TIME, 2), instruction(HALT)]
    await h.load(1, program, ownership=0x02)
    status = await h.status(RS_STATUS)
    assert status == 0b010, f"committed image expected, status={status:#x}"
    assert not h.model.host_fault
    await h.command(START, 0b0010)
    await h.idle(3)
    assert h.last_pre.uio_oe & 0x02 and h.last_pre.uio_out & 0x02, "engine 1 should drive pin 1 high"
    await h.run_until(lambda: not h.engine(1).running, 200, "engine 1 HALT")
    assert await h.status(RS_PC) == len(program)  # HALT advances the PC
    assert await h.status(RS_COUNT) == len(program)
    assert await h.status(RS_LEVELS) == 1 << 16
    assert await h.status(RS_HELD_RX) == 0x1234
    assert h.expected.uo & UO_IRQ, "IRQ must be asserted while an RX FIFO is non-empty"
    assert await h.read(3) == 0x1234
    assert await h.status(RS_LEVELS) == 0
    assert h.expected.uio_oe == 0, "HALT releases output enables"

    # Rejections set the sticky host fault atomically; CLEAR bit23 clears it.
    await h.command(SELECT, 1)
    await h.command(BEGIN)
    await h.write(1, instruction(NOP))
    await h.command(COMMIT, 2)  # incomplete image
    assert h.model.host_fault and h.expected.uo & UO_FAULT
    await h.command(START, 0b0010)  # not committed -> reject
    await h.command(CLEAR, 1 << 23)
    assert not h.model.host_fault and not h.expected.uo & UO_FAULT
    await h.command(COMMIT, 1)
    assert await h.status(RS_STATUS) == 0b010
    await h.command(SELECT, 0)
    await h.command(OWN, 0x02)  # overlaps engine 1 ownership -> reject
    assert h.model.host_fault
    await h.command(CLEAR, 1 << 23)
    await h.command(OWN, 0x0101)  # open-drain within ownership: accepted
    await h.command(OWN, 0x0200 | 0x01)  # open-drain outside ownership -> reject
    assert h.model.host_fault
    await h.command(CLEAR, 1 << 23)
    await h.command(0x3F)  # unknown command
    assert h.model.host_fault
    await h.command(CLEAR, 1 << 23)

    # Running engine: an infinite loop; STOP halts it; faults via FAULT/PC range.
    await h.load(2, [instruction(ADD, 2, 3), immediate(JMP, 0)])
    await h.load(3, [immediate(JMP, 9)])  # PC outside the committed image -> fault 2
    await h.command(START, 0b1100)
    await h.idle(8)
    await h.command(SELECT, 2)
    assert await h.status(RS_STATUS) & 1
    await h.command(SELECT, 3)
    assert await h.status(RS_STATUS) == (2 << 8) | 0b1010
    assert h.expected.uo & UO_FAULT
    await h.command(STOP, 0b0100)
    await h.command(CLEAR, 0b1000)
    assert await h.status(RS_STATUS) == 0b010
    # Queue/route/event/trigger commands accepted on halted engines.
    await h.command(ROUTE, 2 | 3 << 2 | 16 | 4 << 5)
    await h.command(EVENT, 0b0100)
    await h.command(SELECT, 2)
    assert await h.status(RS_EVENT) == 1
    await h.command(TRIGGER, 3 | 0 << 3 | 32)
    await h.command(FLUSH)
    assert not h.model.host_fault
    await h.idle(4)
    dut._log.info("loader test OK after %d cycles", h.cycle)


@cocotb.test()
async def test_checker_detects_divergence(dut):
    """Self-test of the lockstep checker: a corrupted model register must be caught."""
    h = CocotbHarness(dut)
    await h.start()
    # Engine 0 drives its TX register's LSB on pin 0 forever: OUT pin0 then JMP.
    await h.load(0, [immediate(DIR, 1), instruction(LOAD, 0, 0, 1), instruction(8, 0, 0, 0),
                     immediate(JMP, 1)], ownership=1)
    await h.command(START, 1)
    await h.idle(10)
    h.model.engines[0].regs[0] ^= 0  # no-op: still in lockstep
    await h.idle(4)
    h.model.engines[0].values ^= 1  # corrupt the model's pin-0 output latch
    h.dut._log.info("expecting a LockstepMismatch now (deliberate)")
    try:
        await h.idle(4)
    except LockstepMismatch:
        return
    raise AssertionError("lockstep checker did not detect a corrupted model output")
