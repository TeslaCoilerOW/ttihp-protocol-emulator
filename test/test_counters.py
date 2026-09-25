# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Long-running counters and timers, read back through the host port.

The mutation campaign (docs/verification-campaign.md) found that most mutants
the suite missed sit in high bits of the engine counters: the completed-
instruction count (READ_SELECT 5), the LIMIT/blocked-cycle counter, the WAIT
timer, the COUNT/LOOP repeat counter, the ROUTE word count and the timestamp.
Random cases run 2,000 host cycles, so these bits were never set, or were set
and never read. The scenarios here set them in simulated time, with all four
engines working in parallel so that each engine's own copy of a counter is
exercised, and read them back:

* ``long_run_counters``: every engine completes more than 2^16 instructions
  (COUNT 0xFFFF and COUNT 0x7FFF/0x8001 loops, a push stream and a pull
  stream), a ROUTE of 0xFFFF words drains completely, and the timestamp
  passes 2^16; READ_SELECT 1/5 are read at checkpoints (about 67,000 cycles).
* ``route_drains``: two routes of 0x1A5B and 0x1C3D words drain completely;
  then counts of 2^k + 2 (k = 13..15) on all four sources must survive two
  words.
* ``limit_sweep``: LIMIT values with each bit k = 9..23 set must not time out
  early; ``limit_timeouts``: WAITPIN/WAITEVENT timeouts at exact small LIMITs.
* ``wait_timers``: WAIT n of 4,096 to 4,915 cycles ends on the exact cycle.

Counter bits above 16 and the 2^32 timestamp rollover are covered by
``test_timewarp.py``. Every cycle is compared with the reference model; the
asserts restate the ISA-level expectation. All scenarios run on the model alone
too (``harness.run_model``). The long ones are skipped at gate level.
"""

from __future__ import annotations

import itertools
import os

import cocotb

from harness import (CLEAR, FLUSH, ROUTE, RS_COUNT, RS_LEVELS, RS_PC, RS_STATUS, RS_TIMESTAMP, SELECT, START,
                     STOP, CocotbHarness, Harness, immediate, instruction, pad_value)
from scenarios import fault_of

GATE_LEVEL = bool(os.environ.get("PE_GATE_LEVEL"))

NOP, HALT, SET, DIR, WAIT, JMP, PULL, PUSH = range(8)
COUNT, LOOP, LIMIT, WAITPIN, WAITEVENT = 10, 11, 12, 13, 15
LOAD, OR, SHL, TIME = 19, 23, 24, 28
TX, RX, X = 0, 1, 2
ALL = 0b1111


def counters(h: Harness) -> bool:
    return getattr(getattr(h.model, "options", None), "debug_counters", True)


def route(source: int, dest: int, words: int) -> int:
    """ROUTE payload: enable a descriptor of ``words`` words from source to dest."""
    return source | dest << 2 | 16 | words << 5


def pusher(constant: int) -> list[int]:
    """rx := constant (32 bits, built with byte-lane shifts), then one PUSH per cycle forever."""
    return [instruction(LOAD, RX, constant >> 8 & 255, constant & 255),
            instruction(LOAD, X, constant >> 24 & 255, constant >> 16 & 255),
            instruction(SHL, X, 0, 16), instruction(OR, RX, X)] + [instruction(PUSH)] * 59 + [immediate(JMP, 4)]


PULLER = [instruction(PULL)] * 63 + [immediate(JMP, 0)]  # one PULL per cycle forever


async def read_select(h: Harness, engine: int, selection: int) -> int:
    await h.command(SELECT, engine)
    return await h.status(selection)


# ------------------------------------------------------------- scenarios
async def long_run_counters(h: Harness) -> None:
    """More than 2^16 completed instructions on every engine, a 0xFFFF-word ROUTE drained
    to zero, COUNT/LOOP with repeat counts 0xFFFF, 0x7FFF and 0x8001, and a timestamp
    past 2^16, with READ_SELECT 1 and 5 read back at checkpoints."""
    await h.start()
    depth = h.model.config.fifo_words
    await h.load(0, [immediate(COUNT, 0xFFFF), immediate(LOOP, 1), instruction(TIME, RX), instruction(PUSH),
                     instruction(HALT)])
    await h.load(2, [immediate(COUNT, 0x7FFF), immediate(LOOP, 1), immediate(COUNT, 0x8001), immediate(LOOP, 3),
                     instruction(TIME, RX), instruction(PUSH), instruction(HALT)])
    await h.load(1, pusher(0x84218421))  # bits 15 and 31 set in every routed word
    await h.load(3, PULLER)
    await h.command(ROUTE, route(1, 3, 0xFFFF))
    await h.command(START, ALL)
    stamps: list[int] = []
    counts: list[list[int]] = []

    def running() -> bool:
        return h.model.engines[0].running or h.model.engines[2].running or h.model.routes[1] is not None

    while running():  # checkpoint every 8,000 cycles
        assert len(stamps) < 10, "long run did not finish"
        for _ in range(8000):
            if not running():
                break
            await h.step()
        stamps.append(await read_select(h, 0, RS_TIMESTAMP))
        counts.append([await read_select(h, e, RS_COUNT) for e in range(4)])
    await h.idle(20)
    stamps.append(await read_select(h, 0, RS_TIMESTAMP))
    assert all(a < b for a, b in itertools.pairwise(stamps)), stamps
    assert stamps[-1] > 1 << 16, stamps
    for engine in range(4):  # a running engine's count only grows (no wrap below 2^32)
        seen = [row[engine] for row in counts]
        assert seen == sorted(seen), (engine, seen)
    final = [await read_select(h, e, RS_COUNT) for e in range(4)]
    if counters(h):
        assert final[0] == 1 + 65536 + 3 and final[2] == 1 + 32768 + 1 + 32770 + 3, final
        assert min(final) > 1 << 16, final
    else:
        assert final == [0, 0, 0, 0], final
    assert await read_select(h, 0, RS_PC) == 5
    assert await read_select(h, 2, RS_PC) == 7
    for engine in (0, 2):  # TIME values pushed after the loops: past 2^16, before now
        await h.command(SELECT, engine)
        stamp = await h.read(3)
        assert 1 << 16 < stamp < stamps[-1], (engine, stamp)
    assert h.model.routes[1] is None, "the 0xFFFF-word route must have drained"
    assert await read_select(h, 1, RS_STATUS) == 0b111  # running, committed, stalled on PUSH
    assert await h.status(RS_LEVELS) == depth << 16
    assert await read_select(h, 3, RS_STATUS) == 0b111  # stalled on PULL, TX empty
    assert await h.status(RS_LEVELS) == 0
    h.assert_no_faults()
    await h.command(STOP, ALL)


async def route_drains(h: Harness) -> None:
    """ROUTE descriptors drain to exactly zero; counts with high bits set keep moving words."""
    await h.start()
    depth = h.model.config.fifo_words
    await h.load(0, pusher(0x00A5005A))
    await h.load(2, pusher(0x5A00A500))
    await h.load(1, PULLER)
    await h.load(3, PULLER)
    await h.command(ROUTE, route(0, 1, 0x1A5B))
    await h.command(ROUTE, route(2, 3, 0x1C3D))
    await h.command(START, ALL)
    await h.run_until(lambda: h.model.routes[0] is None and h.model.routes[2] is None, 16000, "route drain")
    await h.idle(8)
    for source in (0, 2):
        assert await read_select(h, source, RS_LEVELS) == depth << 16  # RX full, route gone
        assert await read_select(h, source + 1, RS_LEVELS) == 0         # everything routed was pulled
    await h.command(STOP, ALL)
    for engine in range(4):
        await h.command(SELECT, engine)
        await h.command(FLUSH)
    # Large counts: 2^k + 2 words for k = 13, 14, 15 on every source. Each source pushes
    # four words to its neighbour, which is halted, so min(4, depth) words move and the
    # route must stay enabled (a count bit forced to 0 would stop it after two words).
    moved = min(4, depth)
    for k in (13, 14, 15):
        for engine in range(4):
            await h.load(engine, [instruction(LOAD, RX, k, engine)] + [instruction(PUSH)] * 4 + [instruction(HALT)])
        for source in range(4):
            await h.command(ROUTE, route(source, (source + 1) % 4, (1 << k) + 2))
        await h.command(START, ALL)
        await h.idle(40)
        for engine in range(4):
            assert await read_select(h, engine, RS_LEVELS) == moved | (4 - moved) << 16, (k, engine)
            assert h.model.routes[engine] == ((engine + 1) % 4, (1 << k) + 2 - moved)
        for engine in range(4):
            await h.command(SELECT, engine)
            await h.command(FLUSH)  # empties the queues and disables the routes touching it
    h.assert_no_faults()


def toggling_pin6(h: Harness, period: int = 64):
    """Pin 6 toggles every ``period`` cycles from now; the other pads follow the DUT."""
    origin = h.cycle

    def pins(cycle: int, out) -> int:
        return pad_value(out, ((cycle - origin) // period & 1) << 6)

    return pins


async def limit_sweep(h: Harness) -> None:
    """LIMIT (1 << k) | 24 for k = 9..23 on every engine, each followed by a WAITPIN on pin 6
    that succeeds after about 60 cycles: none may time out. A LIMIT bit forced to 0 would
    leave a limit of 24 and fault 3 early."""
    await h.start()
    program = [immediate(LIMIT, 0x7FFFFF), instruction(WAITPIN, 6, 1)]
    level = 0
    for k in range(9, 24):
        program += [immediate(LIMIT, 1 << k | 24), instruction(WAITPIN, 6, level)]
        level ^= 1
    program.append(instruction(HALT))
    for engine in range(4):
        await h.load(engine, program)
    h.pins = toggling_pin6(h)
    await h.command(START, ALL)
    await h.run_until(lambda: not any(e.running for e in h.model.engines), 20 * 64 + 200, "LIMIT sweep")
    for engine in range(4):
        assert await read_select(h, engine, RS_STATUS) == 0b010, engine  # halted, no fault
        assert await h.status(RS_PC) == len(program)
    h.assert_no_faults()


async def limit_timeouts(h: Harness) -> None:
    """Bounded waits time out after exactly LIMIT unsuccessful samples: WAITPIN with
    LIMIT 1, 2, 33 and 0x1FF, then WAITEVENT with LIMIT 3, 64, 0x101 and 0x3FF (the
    lockstep check pins the cycle of each fault through the released pin)."""
    await h.start()
    for limits, wait in (((1, 2, 33, 0x1FF), instruction(WAITPIN, 7, 1)), ((3, 64, 0x101, 0x3FF), instruction(WAITEVENT))):
        for engine, limit in enumerate(limits):
            await h.load(engine, [immediate(DIR, 1 << engine), immediate(SET, 1 << engine), immediate(LIMIT, limit),
                                  wait, instruction(HALT)], ownership=1 << engine)
        await h.command(START, ALL)
        await h.run_until(lambda: all(e.fault for e in h.model.engines), max(limits) + 40, "timeouts")
        for engine in range(4):
            status = await read_select(h, engine, RS_STATUS)
            assert fault_of(status) == 3 and await h.status(RS_PC) == 3, (engine, status)
        assert h.last_pre.uio_oe == 0
        await h.command(CLEAR, ALL)


async def wait_timers(h: Harness) -> None:
    """WAIT n for n = 0x1000 + 0x111 * engine: each engine's pin rises exactly n + 3 cycles
    after START (DIR, WAIT issue, n wait cycles, SET)."""
    await h.start()
    for engine in range(4):
        await h.load(engine, [immediate(DIR, 1 << engine), immediate(WAIT, 0x1000 + 0x111 * engine),
                              immediate(SET, 1 << engine), instruction(HALT)], ownership=1 << engine)
    await h.command(START, ALL)
    started = h.cycle
    rises = {}
    while len(rises) < 4:
        assert h.cycle - started < 0x1000 + 0x111 * 3 + 20, "WAIT did not end"
        await h.step()
        for engine in range(4):
            if engine not in rises and h.last_pre.uio_out >> engine & 1:
                rises[engine] = h.cycle - started
    deltas = [rises[e] - rises[0] for e in range(4)]
    assert deltas == [0x111 * e for e in range(4)], rises
    for engine in range(4):
        assert await read_select(h, engine, RS_PC) == 4
    h.assert_no_faults()


# ------------------------------------------------------------- cocotb tests
@cocotb.test(skip=GATE_LEVEL)
async def test_long_run_counters(dut):
    """> 2^16 instructions per engine, a 0xFFFF-word route, timestamp past 2^16."""
    await long_run_counters(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_route_drains(dut):
    """ROUTE counts 0x1A5B/0x1C3D drain exactly; 2^k + 2 counts stay enabled."""
    await route_drains(CocotbHarness(dut))


@cocotb.test()
async def test_limit_sweep(dut):
    """LIMIT values with each of bits 9..23 set do not time out early."""
    await limit_sweep(CocotbHarness(dut))


@cocotb.test()
async def test_limit_timeouts(dut):
    """WAITPIN/WAITEVENT timeouts at LIMIT 1, 2, 3, 33, 64, 0x101, 0x1FF, 0x3FF."""
    await limit_timeouts(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_wait_timers(dut):
    """WAIT 0x1000..0x1333 ends on the exact cycle on every engine."""
    await wait_timers(CocotbHarness(dut))
