# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Queue storage and host words: full queues whose data input changes, paused writes.

A queue stores a word only on an accepted push; when the queue is full, its
write slot is the head word, which must not change while the data input does
(an engine's rx register for an RX queue, the host word or the mover data for
a TX queue). The host assembles each written word from eight nibbles and may
pause between them. Neither situation had a directed test (``fifo`` and
``imem`` survivors, docs/mutation-push.md).

``full_queues``: for every engine, its RX queue is filled with zeros and the
engine then holds all-ones in rx; its TX queue is filled with zeros while
engine 0's RX head holds all-ones (the mover's data input); every word is then
read back through the host (TX words forwarded by the engine). ``paused_writes``:
program words, TX words and commands written with pauses of one to three
cycles between nibbles; the program pushes what it computed and forwards the
TX words, and the host reads them.
"""

from __future__ import annotations

import cocotb

from harness import (FLUSH, SELECT, START, STOP, UI_WVALID, UO_WREADY, CocotbHarness, Harness, immediate,
                     instruction)
from test_kill_common import HALT, JMP, LOAD, MOV, NOT, PULL, PUSH, RX, TX, WAIT, X, reload, run_drain

FORWARD = [instruction(PULL), instruction(MOV, RX, TX), instruction(PUSH), immediate(JMP, 0)]


async def full_queues(h: Harness) -> None:
    await h.start()
    depth = h.model.config.fifo_words
    for engine in range(4):
        # RX full of zeros, then rx = all ones while the queue stays full
        await reload(h, engine, [instruction(LOAD, RX, 0, 0)] + [instruction(PUSH)] * depth
                     + [instruction(NOT, RX), immediate(WAIT, 12), instruction(HALT)])
        await run_drain(h, 1 << engine)
    for engine in range(4):
        await h.command(STOP, 0b1111)
        for other in range(4):
            await h.command(SELECT, other)
            await h.command(FLUSH)
        source = 1 if engine == 0 else 0
        await reload(h, source, [instruction(LOAD, RX, 0, 0), instruction(NOT, RX), instruction(PUSH),
                                 instruction(HALT)])
        await h.command(START, 1 << source)
        await h.idle(6)  # the source's RX head: all ones
        await h.command(SELECT, engine)
        for _ in range(depth):
            await h.write(2, 0)
        await h.idle(8)
        await reload(h, engine, FORWARD)
        await h.command(START, 1 << engine)
        for _ in range(depth):
            await h.command(SELECT, engine)
            await h.read(3)
        await h.command(STOP, 0b1111)
    h.assert_no_faults()


async def write_paused(h: Harness, window: int, word: int, pauses: tuple[int, ...]) -> None:
    await h.set_window(window)
    for nibble in range(8):
        await h.idle(pauses[nibble % len(pauses)])
        ui = window << 6 | UI_WVALID | (word >> (4 * nibble) & 15)
        for _ in range(1000):
            if (await h.step(ui)).uo & UO_WREADY:
                break
        else:
            raise TimeoutError("paused write not accepted")
    await h.step()


async def paused_writes(h: Harness) -> None:
    await h.start()
    depth = h.model.config.fifo_words
    patterns = [0xFFFFFFFF, 0x00000000, 0xA5A5A5A5, 0x5A5A5A5A, 0xF0F00F0F]
    for engine in range(4):
        await write_paused(h, 0, SELECT << 24 | engine, (1, 2))
        await write_paused(h, 0, 1 << 24, (2, 1, 3))  # BEGIN
        program = [instruction(LOAD, X, 0xFF, 0xFF), instruction(MOV, RX, X), instruction(NOT, RX),
                   instruction(PUSH), instruction(LOAD, RX, 0xA5, 0x5A), instruction(PUSH)] + FORWARD
        program[-1] = immediate(JMP, 6)
        for index, word in enumerate(program):
            await write_paused(h, 1, word, (1, 3, 2) if index % 2 else (2, 1))
        await write_paused(h, 0, 2 << 24 | len(program), (1,))  # COMMIT
        for index in range(min(depth, len(patterns))):
            await write_paused(h, 2, patterns[index], (index % 3 + 1, 1))
        await h.command(START, 1 << engine)
        for _ in range(2 + min(depth, len(patterns))):
            await h.command(SELECT, engine)
            await h.read(3)
        await h.command(STOP, 1 << engine)
    h.assert_no_faults()


@cocotb.test()
async def test_kill_full_queues(dut):
    """Full RX and TX queues keep their words while the queue's data input changes."""
    await full_queues(CocotbHarness(dut))


@cocotb.test()
async def test_kill_paused_writes(dut):
    """Commands, program words and TX words written with pauses between nibbles."""
    await paused_writes(CocotbHarness(dut))
