# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Event mailboxes: pin triggers on every pin and mode, EVENT and SIGNAL masks.

Each engine has one input trigger (pin, rising/falling/high/low) that sets its
mailbox from the synchronized pins; host EVENT and engine SIGNAL set the
mailboxes named by a mask. The random and legacy tests configure a few
triggers; a detector that looks at the wrong previous sample or at the wrong
pin for one mode on one engine survived (``events`` survivors,
docs/mutation-push.md).

``trigger_matrix``: for every engine, mode and pin, the engine runs
``WAITEVENT; JMP`` (so every delivery is consumed one cycle later and the IRQ
pin pulses) while the host toggles the watched pin and its neighbours one at
a time. ``event_masks``: EVENT with every mask, then every engine runs a
bounded WAITEVENT (engines without an event time out); likewise SIGNAL with
every mask from every engine.
"""

from __future__ import annotations

import cocotb

from harness import (CLEAR, EVENT, RS_EVENT, SELECT, START, STOP, TRIGGER, CocotbHarness, Harness, immediate,
                     instruction)
from test_kill_common import GATE_LEVEL, HALT, JMP, LIMIT, SIGNAL, WAITEVENT, reload

CONSUMER = [immediate(LIMIT, 0xFFFFFF), instruction(WAITEVENT), immediate(JMP, 1)]
WAITER = [immediate(LIMIT, 12), instruction(WAITEVENT), instruction(HALT)]


async def trigger_matrix(h: Harness) -> None:
    await h.start()
    h.pins = 0
    for engine in range(4):
        await reload(h, engine, CONSUMER)
    for engine in range(4):
        for mode in range(4):
            for pin in range(8):
                await h.command(STOP, 1 << engine)
                await h.command(SELECT, engine)
                baseline = 0xFF if mode in (1, 3) else 0x00  # the watched level is inactive at rest
                h.pins = baseline
                await h.command(TRIGGER, pin | mode << 3 | 32)
                await h.command(START, 1 << engine)
                h.pins = baseline
                await h.idle(4)
                for toggled in (pin, (pin + 1) % 8, (pin + 7) % 8, pin ^ 4, pin):
                    h.pins = baseline ^ 1 << toggled
                    await h.idle(3)
                    h.pins = baseline
                    await h.idle(3)
        await h.command(STOP, 1 << engine)
        await h.command(SELECT, engine)
        await h.command(TRIGGER, 0)  # disable
    await h.command(STOP, 0b1111)
    h.assert_no_faults()


async def event_masks(h: Harness) -> None:
    await h.start()
    h.pins = 0
    for engine in range(4):
        await reload(h, engine, WAITER)
    for mask in range(1, 16):
        await h.command(EVENT, mask)
        for engine in range(4):
            await h.command(SELECT, engine)
            await h.status(RS_EVENT)
        await h.command(START, 0b1111)
        await h.idle(16)
        await h.command(CLEAR, 0b1111)
    for source in range(4):
        for mask in range(1, 16):
            await reload(h, source, [immediate(SIGNAL, mask), instruction(HALT)])
            await h.command(START, 0b1111)
            await h.idle(16)
            await h.command(CLEAR, 0b1111)
        await reload(h, source, WAITER)
    h.assert_no_faults()


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_trigger_matrix(dut):
    """Pin triggers: every engine, mode and pin, with neighbouring pins toggled."""
    await trigger_matrix(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_event_masks(dut):
    """EVENT and SIGNAL with every destination mask; bounded WAITEVENT on every engine."""
    await event_masks(CocotbHarness(dut))
