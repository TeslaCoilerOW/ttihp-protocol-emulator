# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers of the test_kill_* modules (no tests of its own).

The test_kill_* modules were written from the survivors of the RTL mutation
campaign after the gap-closure tests (docs/mutation-push.md). Like the rest of
the suite, every scenario is an ``async def f(h)`` on a Harness: under cocotb
the DUT runs in lockstep with the reference model (every cycle's uo_out,
uio_out and uio_oe are compared), and every scenario also runs on the model
alone (``harness.run_model``).
"""

from __future__ import annotations

import os

from harness import BEGIN, CLEAR, COMMIT, OWN, SELECT, START, STOP, Harness

GATE_LEVEL = bool(os.environ.get("PE_GATE_LEVEL"))
ALL = 0b1111

# Opcodes (docs/isa.md) and register numbers.
(NOP, HALT, SET, DIR, WAIT, JMP, PULL, PUSH, OUT, IN, COUNT, LOOP, LIMIT, WAITPIN, SIGNAL, WAITEVENT,
 PINS, XFER, MOV, LOAD, ADD, XOR, AND, OR, SHL, SHR, JZ, NOT, TIME, FAULT) = range(30)
TX, RX, X, Y = range(4)


def options(h: Harness):  # noqa: ANN201 - model.variant.Options or None
    return getattr(h.model, "options", None)


def counters(h: Harness) -> bool:
    """Does the design have completed-instruction counters (READ_SELECT 5)?"""
    return getattr(options(h), "debug_counters", True)


def byte_lane(h: Harness) -> bool:
    return getattr(options(h), "shift", "barrel") == "byte_lane"


async def reload(h: Harness, engine: int, words: list[int], *, select: bool = True) -> None:
    """BEGIN, program words, COMMIT (ownership unchanged; BEGIN keeps it)."""
    if select:
        await h.command(SELECT, engine)
    await h.command(BEGIN)
    for word in words:
        await h.write(1, word)
    await h.command(COMMIT, len(words))


async def own(h: Harness, engine: int, pins: int, open_drain: int = 0) -> None:
    await h.command(SELECT, engine)
    await h.command(OWN, pins | open_drain << 8)


async def run_all(h: Harness, mask: int = ALL, *, limit: int = 20000) -> None:
    """START ``mask`` and idle until every started engine has halted or faulted."""
    await h.command(START, mask)
    await h.run_until(lambda: not any(h.model.engines[e].running for e in range(4) if mask >> e & 1),
                      limit, "engines to stop")


async def stop_clear(h: Harness, mask: int = ALL) -> None:
    await h.command(STOP, mask)
    await h.command(CLEAR, mask)


async def run_drain(h: Harness, mask: int = 0b1111, *, limit: int = 20000) -> int:
    """START the engines in ``mask`` and read their RX words until all have halted and
    every RX queue is empty. Returns the number of words read."""
    await h.command(START, mask)
    words = 0
    start = h.cycle
    while any(h.model.engines[e].running or h.model.engines[e].rx for e in range(4) if mask >> e & 1):
        assert h.cycle - start < limit, "engines did not finish"
        progressed = False
        for engine in range(4):
            if mask >> engine & 1 and h.model.engines[engine].rx:
                await h.command(SELECT, engine)
                await h.read(3)
                words += 1
                progressed = True
        if not progressed:
            await h.idle(4)
    return words
