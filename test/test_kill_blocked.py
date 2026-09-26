# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Bounded waits time out on the exact cycle, whatever ran before them.

Every completed instruction clears an engine's blocked-cycle count, and the
count holds while the engine waits (WAIT), transfers (XFER) or stalls on a
queue. A WAITPIN/WAITEVENT that follows then times out after exactly LIMIT
unsuccessful samples. ``test_timewarp.blocked_paths`` checks that no large count
is left behind (it releases the wait four cycles before the limit), so a count
of 1 left by one completion path, or a count that one hold path corrupts only in
its high bits, survived (48 mutation survivors on ``blocked_cycles``,
docs/mutation-push.md).

``exact_timeout_paths``: for each way of completing an instruction and each
hold state (WAIT countdown, XFER, empty-TX PULL stall, full-RX PUSH stall), all
four engines run ``LIMIT 3; <path>; WAITPIN`` on a pin that never matches; the
lockstep comparison checks the cycle on which each engine's fault appears, so
any count left behind (1 or more) is visible. ``exact_timeout_waitevent`` does
the same for WAITEVENT. ``exact_timeout_limits`` (RTL, time warp) lets every
engine time out at LIMIT 2^k and 2^k + 5, with the count warped to a few cycles
before the limit, so every carry into the count's high bits meets the
comparison on the exact cycle, and no single LIMIT bit is mistaken for the
reset default (a LIMIT of 0 means 65535); ``exact_timeout_waitevent_limits``
does the same for WAITEVENT at a few large limits.
"""

from __future__ import annotations

import cocotb

from harness import CLEAR, FLUSH, RS_STATUS, SELECT, START, CocotbHarness, Harness, immediate, instruction
from scenarios import fault_of
from test_kill_common import (ADD, AND, COUNT, DIR, GATE_LEVEL, IN, JMP, JZ, LIMIT, LOAD, LOOP, MOV, NOP, NOT, OR,
                              OUT, PINS, PULL, PUSH, RX, SET, SHL, SHR, SIGNAL, TIME, WAIT, WAITEVENT, WAITPIN, X,
                              XFER, XOR, Y, own, reload)

LIMIT_SMALL = 3


def paths(engine: int, fifo_words: int) -> list[tuple[str, list[int]]]:
    """(label, instructions) that complete (or hold) right before the bounded wait.
    Jumps, loops and branches target the next word (patched by ``program``)."""
    mine = 1 << engine
    pins = immediate(PINS, engine | engine << 3 | 7 << 6)
    return [
        ("NOP", [instruction(NOP)]), ("SET", [immediate(SET, mine)]), ("DIR", [immediate(DIR, mine)]),
        ("WAIT 0", [immediate(WAIT, 0)]), ("WAIT 1", [immediate(WAIT, 1)]), ("WAIT 2", [immediate(WAIT, 2)]),
        ("WAIT 7", [immediate(WAIT, 7)]),
        ("LOOP taken", [immediate(COUNT, 1), immediate(LOOP, 0)]), ("LOOP not taken", [immediate(LOOP, 0)]),
        ("COUNT", [immediate(COUNT, 3)]), ("LIMIT", [immediate(LIMIT, LIMIT_SMALL)]),
        ("SIGNAL", [immediate(SIGNAL, 1 << (engine ^ 1))]),
        ("WAITEVENT pending", [immediate(SIGNAL, mine), instruction(WAITEVENT)]),
        ("WAITPIN satisfied", [instruction(WAITPIN, 7, 0)]),
        ("PINS", [pins]), ("XFER", [pins, instruction(XFER, 1, 1, 0)]),
        ("XFER long", [pins, instruction(XFER, 3, 2, 0b10011)]),
        ("MOV", [instruction(MOV, X, Y)]), ("LOAD", [instruction(LOAD, Y, 0, 1)]),
        ("ADD", [instruction(ADD, X, Y)]), ("XOR", [instruction(XOR, X, Y)]), ("AND", [instruction(AND, X, Y)]),
        ("OR", [instruction(OR, X, Y)]), ("SHL", [instruction(SHL, X, 0, 8)]), ("SHR", [instruction(SHR, X, 0, 8)]),
        ("NOT", [instruction(NOT, X)]), ("TIME", [instruction(TIME, X)]),
        ("JZ taken", [instruction(JZ, X, 0xFF, 0xFF)]),
        ("JZ not taken", [instruction(NOT, Y), instruction(JZ, Y, 0xFF, 0xFF)]),
        ("OUT", [instruction(OUT, engine, 0, 0)]), ("IN", [instruction(IN, 7, 0, 0)]),
        ("PUSH", [instruction(PUSH)]), ("PUSH strict", [instruction(PUSH, 1)]),
        ("PULL", [instruction(PULL)]), ("JMP", [immediate(JMP, 0)]),
        ("PULL stall", [instruction(PULL)]),
        ("PUSH stall", [instruction(LOAD, RX, 0, 7)] + [instruction(PUSH)] * (fifo_words + 1)),
    ]


def program(limit: int, body: list[int], wait: int) -> list[int]:
    """LIMIT, the path (jump targets patched to the next word), then the bounded wait."""
    words = [immediate(LIMIT, limit)]
    for word in body:
        target = len(words) + 1
        op = word >> 24
        if op in (JMP, LOOP):
            word = word & ~0xFFFFFF | target
        elif op == JZ:
            word = word & ~0xFFFF | target
        words.append(word)
    return words + [wait]


async def run_round(h: Harness, images: list[list[int]], label: str, *, prefill: bool, drain: bool) -> None:
    """Load one image per engine, START all, service the queue stalls, wait for the faults."""
    for engine, image in enumerate(images):
        await reload(h, engine, image)
        if prefill:
            await h.write(2, 0xA0 + engine)
    await h.command(START, 0b1111)
    if label == "PULL stall":
        await h.idle(6)
        for engine in range(4):
            await h.command(SELECT, engine)
            await h.write(2, 0xB0 + engine)
    if drain:
        await h.run_until(lambda: all(e.stalled or not e.running for e in h.model.engines), 200, "PUSH stalls")
        for engine in range(4):
            await h.command(SELECT, engine)
            await h.read(3)
    await h.run_until(lambda: not any(e.running for e in h.model.engines), 400, f"timeouts after {label}")
    for engine in range(4):
        assert h.model.engines[engine].fault == 3, (label, engine, h.model.engines[engine].fault)
    await h.command(CLEAR, 0b1111)
    for engine in range(4):  # empty the queues for the next round
        await h.command(SELECT, engine)
        await h.command(FLUSH)


async def exact_timeout_paths(h: Harness) -> None:
    await h.start()
    h.pins = 0  # pin 7 stays low; the bounded waits expect it high
    fifo_words = h.model.config.fifo_words
    for engine in range(4):
        await own(h, engine, 1 << engine)
    for index, (label, _) in enumerate(paths(0, fifo_words)):
        images = [program(LIMIT_SMALL, paths(e, fifo_words)[index][1], instruction(WAITPIN, 7, 1))
                  for e in range(4)]
        await run_round(h, images, label, prefill=label == "PULL",
                        drain=label == "PUSH stall")
    h.assert_no_faults()


async def exact_timeout_waitevent(h: Harness) -> None:
    """WAITEVENT after the same paths (every fourth one, rotated over the engines), LIMIT 3
    and 5, and a WAITEVENT whose event arrives on its last sample (no timeout)."""
    await h.start()
    h.pins = 0
    fifo_words = h.model.config.fifo_words
    for engine in range(4):
        await own(h, engine, 1 << engine)
    table = [paths(e, fifo_words) for e in range(4)]
    labels = [label for label, _ in table[0] if label not in ("PULL stall", "PUSH stall", "SIGNAL",
                                                               "WAITEVENT pending")]
    for start in range(0, len(labels), 4):
        chosen = labels[start:start + 4]
        chosen += labels[:4 - len(chosen)]
        images = []
        for engine in range(4):
            label = chosen[(engine + start // 4) % 4]
            body = dict(table[engine])[label]
            images.append(program(LIMIT_SMALL + (engine & 1) * 2, body, instruction(WAITEVENT)))
        await run_round(h, images, "WAITEVENT " + ",".join(chosen), prefill=True, drain=False)
    h.assert_no_faults()


async def exact_timeout_limits(h: Harness) -> None:
    """Every engine: LIMIT 2^k (k = 0..23) and 2^k + 5 (k = 4..23), a WAITPIN that never
    matches, the count warped to eight cycles before the limit; the timeout must come on the
    exact cycle."""
    await h.start()
    h.pins = 0
    for limit in [1 << k for k in range(24)] + [(1 << k) + 5 for k in range(4, 24)]:
        k = limit.bit_length() - 1
        for engine in range(4):
            await reload(h, engine, [immediate(LIMIT, limit), instruction(WAITPIN, 7, 1)])
        await h.command(START, 0b1111)
        if limit > 16:
            await h.idle(4)
            blocked = h.model.engines[0].blocked
            assert all(e.blocked == blocked and e.stalled for e in h.model.engines), k
            await h.warp(limit - 8 - blocked)
        await h.run_until(lambda: not any(e.running for e in h.model.engines), 40, f"timeout at LIMIT {limit:#x}")
        await h.command(SELECT, k % 4)
        assert fault_of(await h.status(RS_STATUS)) == 3, k
        await h.command(CLEAR, 0b1111)
    h.assert_no_faults()


async def exact_timeout_waitevent_limits(h: Harness) -> None:
    """The same for WAITEVENT (no event arrives): LIMIT 2^k + 5 for k = 12, 17, 20, 23."""
    await h.start()
    h.pins = 0
    for k in (12, 17, 20, 23):
        limit = (1 << k) + 5
        for engine in range(4):
            await reload(h, engine, [immediate(LIMIT, limit), instruction(WAITEVENT)])
        await h.command(START, 0b1111)
        await h.idle(4)
        blocked = h.model.engines[0].blocked
        assert all(e.blocked == blocked and e.stalled for e in h.model.engines), k
        await h.warp(limit - 8 - blocked)
        await h.run_until(lambda: not any(e.running for e in h.model.engines), 40, f"timeout at LIMIT {limit:#x}")
        await h.command(CLEAR, 0b1111)
    h.assert_no_faults()


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_exact_timeout_paths(dut):
    """LIMIT 3; <each completion path or hold state>; WAITPIN times out on the exact cycle."""
    await exact_timeout_paths(CocotbHarness(dut))


@cocotb.test()
async def test_kill_exact_timeout_waitevent(dut):
    """The same with WAITEVENT (LIMIT 3 and 5)."""
    await exact_timeout_waitevent(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_exact_timeout_waitevent_limits(dut):
    """Exact WAITEVENT timeouts at LIMIT 2^k + 5, k = 12, 17, 20, 23 (time warp; RTL only)."""
    await exact_timeout_waitevent_limits(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_exact_timeout_limits(dut):
    """Exact WAITPIN timeouts at LIMIT 2^k and 2^k + 5 (time warp; RTL only)."""
    await exact_timeout_limits(CocotbHarness(dut))
