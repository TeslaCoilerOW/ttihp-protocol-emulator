# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""COMMIT accepts exactly the image that BEGIN and the program words wrote.

Specified behaviour (docs/isa.md, "Host interface"):
- "BEGIN selected engine: stop, invalidate image, program write address=0";
  "BEGIN must precede program words"; window 1 "writes program words with
  auto-increment".
- "COMMIT low16 length: accept only contiguous fully written image of that
  length, nonzero and <= capacity".
- "Invalid host commands reject atomically and set sticky host fault, cleared
  by reset or CLEAR with payload bit23 set"; uo7 is the fault pin.
- Status: "bit0 running,bit1 committed"; START needs "committed image and no
  fault"; HALT "stop and release output enables" and, like every instruction,
  advances the PC.

``commit_lengths``: on every engine, COMMIT 0 straight after BEGIN, COMMIT 0 and
COMMIT 2 after one word, COMMIT 1 again once the image is committed, and COMMIT
0 after a BEGIN that invalidated a committed image are rejected: the fault pin
rises on the command's edge and the status shows no committed image (the image
stays committed after the repeated COMMIT 1); CLEAR with bit 23 lowers the pin.
The rejections are atomic, so the load continues: COMMIT 1 is then accepted
(fault pin low, committed bit set).

``full_image_parity``: on every engine, a full-capacity image (DIR of the
engine's own pin, NOPs, HALT as the last word) is committed with zero and with
one idle cycle between the last program word and COMMIT, so that the COMMIT
edge falls at both parities relative to the last word. Each COMMIT must be
accepted (fault pin low, status committed), the image must run from its first
to its last word (the engine's output enable, read from the DUT, stays high
for exactly capacity - 1 edges) and the PC must then read the capacity.

The checks read the DUT's fault pin, output enable and status words; the
expected values come from the rules above, not from the reference model.
"""

from __future__ import annotations

import cocotb

from harness import BEGIN, CLEAR, COMMIT, RS_PC, RS_STATUS, SELECT, START, UO_FAULT, Harness, immediate, instruction
from test_kill_common import DIR, GATE_LEVEL, HALT, NOP, own
from test_spec_common import harness, read_word
from test_wait_limit import EnableEdges

HOST_FAULT_CLEAR = 1 << 23
COMMITTED = 1 << 1


def fault_pin(h: Harness) -> bool:
    """The fault pin (uo7) as the DUT showed it before the last edge."""
    return bool(h.last_pre.uo & UO_FAULT)


async def expect_rejected(h: Harness, length: int, committed: bool, what: str) -> None:
    assert not fault_pin(h), f"{what}: fault pin already high"
    await h.command(COMMIT, length)
    assert fault_pin(h), f"{what}: COMMIT {length} accepted (fault pin low)"
    status = await read_word(h, RS_STATUS)
    assert bool(status & COMMITTED) == committed, f"{what}: status {status:#x} after the rejected COMMIT {length}"
    await h.command(CLEAR, HOST_FAULT_CLEAR)
    await h.step()
    assert not fault_pin(h), f"{what}: CLEAR bit 23 left the fault pin high"


async def expect_accepted(h: Harness, length: int, what: str) -> None:
    await h.command(COMMIT, length)
    assert not fault_pin(h), f"{what}: COMMIT {length} rejected (fault pin high)"
    status = await read_word(h, RS_STATUS)
    assert status & COMMITTED and not status & 1, f"{what}: status {status:#x} after COMMIT {length}"


async def commit_lengths(h: Harness) -> int:
    await h.start()
    checked = 0
    for engine in range(4):
        what = f"engine {engine}"
        await h.command(SELECT, engine)
        await h.command(BEGIN)
        await expect_rejected(h, 0, False, f"{what}, COMMIT 0 after BEGIN")
        await h.write(1, instruction(HALT))
        await expect_rejected(h, 0, False, f"{what}, COMMIT 0 after one word")
        await expect_rejected(h, 2, False, f"{what}, COMMIT 2 after one word")
        await expect_accepted(h, 1, f"{what}, COMMIT 1 after one word and two rejected COMMITs")
        await expect_rejected(h, 1, True, f"{what}, COMMIT 1 of a committed image")
        await h.command(BEGIN)
        await expect_rejected(h, 0, False, f"{what}, COMMIT 0 after BEGIN of a committed image")
        await h.write(1, instruction(HALT))
        await expect_accepted(h, 1, f"{what}, COMMIT 1 after a rejected COMMIT 0")
        checked += 7
    h.assert_no_faults()
    return checked


async def full_image_parity(h: Harness) -> int:
    await h.start()
    h.pins = 0
    capacity = h.model.config.program_words
    edges = EnableEdges()
    h.observers.append(edges)
    runs = 0
    for engine in range(4):
        await own(h, engine, 1 << engine)
        image = [immediate(DIR, 1 << engine)] + [instruction(NOP)] * (capacity - 2) + [instruction(HALT)]
        for idle in (0, 1):
            what = f"engine {engine}, {idle} idle cycle(s) before COMMIT"
            await h.command(BEGIN)
            for word in image:
                await h.write(1, word)
            await h.idle(idle)
            await expect_accepted(h, capacity, what)
            edges.clear()
            await h.command(START, 1 << engine)
            await h.run_until(lambda e=engine: not h.model.engines[e].running, capacity + 20, "HALT")
            await h.idle(2)
            held = edges.fall.get(engine, -1) - edges.rise.get(engine, 0)
            assert held == capacity - 1, f"{what}: enable high for {held} edges, the image gives {capacity - 1}"
            pc = await read_word(h, RS_PC)
            assert pc == capacity, f"{what}: PC {pc} after the HALT in word {capacity - 1}"
            runs += 1
        await own(h, engine, 0)
    h.observers.remove(edges)
    h.assert_no_faults()
    return runs


@cocotb.test()
async def test_spec_commit_lengths(dut):
    """COMMIT 0, COMMIT of an unwritten length and of a committed image are rejected; the written length is accepted."""
    h = harness(dut)
    count = await commit_lengths(h)
    h.log("%d COMMIT outcomes as isa.md gives them (%d cycles)", count, h.cycle)


@cocotb.test(skip=GATE_LEVEL)
async def test_spec_full_image_commit_parity(dut):
    """Full-capacity images committed 0 and 1 extra cycles after their last word are accepted and run, every engine."""
    h = harness(dut)
    count = await full_image_parity(h)
    h.log("%d full images committed and run (%d cycles)", count, h.cycle)
