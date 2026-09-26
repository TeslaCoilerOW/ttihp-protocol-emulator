# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""STOP and START on the exact edge an instruction would issue.

docs/isa.md: STOP and START take priority over execution; on their edge the
engine executes nothing, so a PULL, PUSH, SIGNAL or WAITEVENT that would have
issued on that edge neither pops, pushes, signals nor consumes. An engine's
queue handshakes and event outputs are gated by that priority; a gate that
ignored the STOP or START edge survived (``shared`` survivors on the
engine-active term, docs/mutation-push.md), because no test placed a STOP or a
restarting START on exactly that edge.

``stop_start_edges``: all four engines run ``WAIT; PULL; LOAD; PUSH; SIGNAL;
WAITEVENT; HALT`` from one START; the host has sent seven nibbles of STOP (or
of START, which restarts running engines) and sends the eighth so that the
command lands on the edge where PULL, PUSH, SIGNAL or WAITEVENT would issue.
FIFO levels and pending events are then read back (the IRQ pin already shows
a stray push or event on the next cycle). ``strict_push_status``: the status
word captured on the edge where a strict PUSH meets a full RX queue (the host
changes windows one edge earlier, predicted on a copy of the model) shows the
engine running and not stalled; a blocking PUSH would stall there.
"""

from __future__ import annotations

import copy

import cocotb

from harness import (CLEAR, FLUSH, READ_SELECT, RS_EVENT, RS_LEVELS, RS_STATUS, SELECT, START, STOP, CocotbHarness,
                     Harness, immediate, instruction)
from test_kill_common import GATE_LEVEL, HALT, LIMIT, LOAD, PULL, PUSH, RX, SIGNAL, WAIT, WAITEVENT, reload
from test_kill_pc import command_at

TARGETS = {1: "PULL", 3: "PUSH", 4: "SIGNAL", 5: "WAITEVENT"}


def program(engine: int) -> list[int]:
    return [immediate(WAIT, 12), instruction(PULL), instruction(LOAD, RX, 0x77, engine), instruction(PUSH),
            immediate(SIGNAL, 1 << engine), instruction(WAITEVENT), instruction(HALT)]


async def stop_start_edges(h: Harness) -> None:
    await h.start()
    h.pins = 0
    for command in (STOP, START):
        for index in TARGETS:
            for engine in range(4):
                await reload(h, engine, program(engine))
                await h.write(2, 0xE0 + engine)
            await h.command(START, 0b1111)
            engines = h.model.engines
            ready = lambda i=index: all(e.running and e.pc == i and not e.wait for e in engines)  # noqa: E731
            await command_at(h, command, 0b1111, ready)
            await h.idle(2)
            for engine in range(4):
                await h.command(SELECT, engine)
                await h.status(RS_LEVELS)
                await h.status(RS_EVENT)
            await h.command(STOP, 0b1111)
            for engine in range(4):
                await h.command(SELECT, engine)
                await h.command(FLUSH)
            for engine in range(4):  # consume any pending event (or time out), then clear
                await reload(h, engine, [immediate(LIMIT, 2), instruction(WAITEVENT), instruction(HALT)])
            await h.command(START, 0b1111)
            await h.idle(6)
            await h.command(CLEAR, 0b1111)
    h.assert_no_faults()


async def snapshot_when(h: Harness, predicate) -> int:  # noqa: ANN001
    """Read window 0 with the status snapshot taken on the first edge whose pre-edge state
    satisfies ``predicate`` (predicted on a copy of the model): the window changes to 0 one
    edge earlier, and the edge after a window change captures the word."""
    await h.set_window(1)
    for _ in range(400):
        future = copy.deepcopy(h.model)
        future.tick(0, h.pins if isinstance(h.pins, int) else 0)
        if predicate(future):
            break
        await h.step()
    else:
        raise AssertionError("snapshot point not reached")
    return await h.read(0)


async def strict_push_status(h: Harness) -> None:
    """The status word captured on the edge where a strict PUSH meets a full RX queue: the
    engine faults with code 4 on that edge and is not reported as stalled (only a blocking
    PUSH stalls)."""
    await h.start()
    depth = h.model.config.fifo_words
    for engine in range(4):
        program = [instruction(LOAD, RX, 0x5A, engine)] + [instruction(PUSH)] * depth + [
            immediate(WAIT, 6), instruction(PUSH, 1), instruction(HALT)]
        await reload(h, engine, program)
        await h.command(READ_SELECT, RS_STATUS)
        await h.command(START, 1 << engine)
        e = h.model.engines[engine]
        status = await snapshot_when(h, lambda m: m.engines[engine].running and m.engines[engine].pc == depth + 2
                                     and not m.engines[engine].wait)
        assert status & 0b101 == 0b001, hex(status)  # running, not stalled
        await h.run_until(lambda: not e.running, 20, "strict PUSH fault")
        assert e.fault == 4
        await h.command(CLEAR, 1 << engine)
        await h.command(FLUSH)
    h.assert_no_faults()


@cocotb.test()
async def test_kill_strict_push_status(dut):
    """Status captured on the edge a strict PUSH meets a full queue: running, not stalled."""
    await strict_push_status(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_stop_start_edges(dut):
    """STOP/START landing on the edge where PULL, PUSH, SIGNAL or WAITEVENT would issue."""
    await stop_start_edges(CocotbHarness(dut))
