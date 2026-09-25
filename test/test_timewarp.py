# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Counter carries up to bit 31 and the 32-bit timestamp rollover, by time warp.

Brute-force simulation reaches about 2^17 cycles in a CI budget; the counters
are 16 to 32 bits wide, and the timestamp wraps after 2^32 cycles (86 s of
chip time, days of simulation). ``Harness.warp(n)`` skips n cycles in which
nothing but counting can happen: it checks that on the reference model (every
engine halted, in a WAIT that outlasts the skip, blocked in a WAITPIN/WAITEVENT
short of its LIMIT, or spinning on a JMP/LOOP to itself; host idle; pins
static; no DMA move possible), advances the model's counters by the same
arithmetic, and adds the same amounts to the core's registers through the
simulator (a relative deposit, so an earlier DUT divergence is kept).
``Harness.warp_routes(n)`` likewise takes n words off every enabled ROUTE
descriptor. Lockstep comparison resumes on the next cycle.

Each scenario walks one counter family through every carry boundary: warp to
just below 2^k, simulate across it in lockstep, read the value back through the
host port or let it reach an architectural event (WAIT end, LIMIT timeout,
LOOP exit, route expiry), and repeat for k = 31 (or 23, 15) down to a few. All
four engines take part so that each engine's own register is exercised.

The warps are white-box stimulus (the checks stay black-box: pins and host
reads against the model). RTL only: skipped at gate level, where the core
registers are not visible. Every scenario also runs on the model alone
(``harness.run_model``).
"""

from __future__ import annotations

import os

import cocotb

from harness import (EVENT, FLUSH, RS_COUNT, RS_LEVELS, RS_PC, RS_STATUS, RS_TIMESTAMP, SELECT, START, STOP,
                     CocotbHarness, Harness, immediate, instruction)
from scenarios import fault_of

GATE_LEVEL = bool(os.environ.get("PE_GATE_LEVEL"))

NOP, HALT, SET, DIR, WAIT, JMP, PULL, PUSH, OUT, IN = range(10)
COUNT, LOOP, LIMIT, WAITPIN, SIGNAL, WAITEVENT, PINS, XFER = range(10, 18)
MOV, LOAD, ADD, XOR, AND, OR, SHL, SHR, JZ, NOT, TIME = range(18, 29)
TX, RX, X, Y = range(4)
ALL = 0b1111
ROUTE = 6


def counters(h: Harness) -> bool:
    return getattr(getattr(h.model, "options", None), "debug_counters", True)


async def warp_by(h: Harness, cycles: int) -> None:
    """Warp forward when the next boundary is still ahead (the low ones are simulated)."""
    if cycles > 0:
        await h.warp(cycles)


async def read_select(h: Harness, engine: int, selection: int) -> int:
    await h.command(SELECT, engine)
    return await h.status(selection)


# ------------------------------------------------------------- scenarios
async def timestamp_rollover(h: Harness) -> None:
    """The timestamp (READ_SELECT 1, and TIME into a register) across 2^k for k = 16..31
    and across the 2^32 rollover."""
    await h.start()
    await h.load(0, [instruction(TIME, RX), instruction(PUSH), instruction(HALT)])
    history: list[int] = []
    for k in range(16, 33):
        boundary = 1 << k
        await h.warp(boundary - 64 - h.model.timestamp)
        before = await read_select(h, 0, RS_TIMESTAMP)
        await h.run_until(lambda b=boundary: (h.model.timestamp - b) % (1 << 32) < 1 << 31, 100,
                          f"timestamp past 2^{k}")
        await h.command(START, 0b0001)  # TIME rx; PUSH; HALT
        after = await read_select(h, 0, RS_TIMESTAMP)
        await h.command(SELECT, 0)
        stamp = await h.read(3)
        wrapped = boundary & 0xFFFFFFFF
        assert boundary - 64 <= before < boundary, (k, hex(before))
        assert wrapped <= stamp < after < wrapped + 256, (k, hex(stamp), hex(after))
        history += [before, stamp, after]
    assert history[:-3] == sorted(history[:-3]) and history[-2] < history[-4], [hex(v) for v in history]


def every_opcode(engine: int) -> list[int]:
    """A loop through every opcode that completes (engine ``engine`` owns pin ``engine``;
    pin 7 is an input held at 0; a self-route moves each PUSHed word back for the PULL)."""
    own = 1 << engine
    return [immediate(DIR, own), immediate(SET, own), immediate(LIMIT, 100), immediate(COUNT, 1),
            instruction(LOAD, X, 0x12, 0x34), instruction(LOAD, Y, 0, 3), instruction(MOV, TX, X),   # 4
            instruction(ADD, X, Y), instruction(XOR, X, Y), instruction(AND, X, Y), instruction(OR, X, Y),
            instruction(SHL, X, 0, 8), instruction(SHR, X, 0, 8), instruction(NOT, X), instruction(TIME, Y),
            instruction(JZ, X, 0, 16), instruction(LOAD, RX, 0, 0), instruction(JZ, RX, 0, 18),       # 15
            instruction(OUT, engine, 0, 0), instruction(IN, 7, 0, 0),                                  # 18
            immediate(PINS, engine | engine << 3 | 7 << 6), instruction(XFER, 1, 1, 0),              # 20
            immediate(SIGNAL, own), instruction(WAITEVENT), instruction(WAITPIN, 7, 0),              # 22
            instruction(LOAD, RX, 0xE0, engine), instruction(PUSH), instruction(PULL),                # 25
            immediate(WAIT, 2), immediate(LOOP, 4), instruction(NOP), immediate(JMP, 0)]              # 28


async def completed_rollover(h: Harness) -> None:
    """Completed-instruction counts (READ_SELECT 5) across 2^k for k = 16..31 and across
    2^32 while every engine loops through all opcodes (the count has one update path per
    kind of instruction). ``warp_completed`` raises the counts; the loops run in lockstep
    across each boundary."""
    await h.start()
    for engine in range(4):
        await h.load(engine, every_opcode(engine), ownership=1 << engine)
        await h.command(ROUTE, engine | engine << 2 | 16 | 0xFFFF << 5)
    await h.command(START, ALL)
    await h.idle(8)
    reads: list[list[int]] = []
    for k in range(16, 33):
        await h.warp_completed(((1 << k) - 40 - h.model.engines[0].completed) % (1 << 32))
        await h.idle(100)
        reads.append([await read_select(h, e, RS_COUNT) for e in range(4)])
    if counters(h):
        for k, row in zip(range(16, 33), reads, strict=True):
            if k < 32:
                assert all(1 << k < v < (1 << k) + 400 for v in row), (k, [hex(v) for v in row])
            else:  # wrapped past 2^32
                assert all(v < 400 for v in row), [hex(v) for v in row]
    else:
        assert all(v == 0 for row in reads for v in row)
    await h.command(STOP, ALL)
    h.assert_no_faults()


def finishing_instructions(engine: int) -> list[list[int]]:
    """One entry per way an instruction completes (each list runs without blocking):
    every opcode that finishes, taken and untaken LOOP/JZ, WAIT 0 and WAIT n,
    WAITEVENT with its event pending, WAITPIN already satisfied."""
    own = 1 << engine
    return [[instruction(NOP)], [immediate(SET, own)], [immediate(DIR, own)], [immediate(WAIT, 0)],
            [immediate(WAIT, 3)], [immediate(COUNT, 1), immediate(LOOP, 0)], [immediate(LOOP, 0)],
            [immediate(LIMIT, 0xFFFFFF)], [immediate(SIGNAL, own), instruction(WAITEVENT)],
            [instruction(WAITPIN, 7, 0)], [immediate(PINS, engine | engine << 3 | 7 << 6)],
            [instruction(XFER, 1, 1, 0)], [instruction(MOV, X, Y)], [instruction(LOAD, Y, 0, 1)],
            [instruction(ADD, X, Y)], [instruction(XOR, X, Y)], [instruction(AND, X, Y)],
            [instruction(OR, X, Y)], [instruction(SHL, X, 0, 8)], [instruction(SHR, X, 0, 8)],
            [instruction(NOT, X)], [instruction(TIME, X)], [instruction(JZ, X, 0, 0)],
            [instruction(LOAD, Y, 0, 0), instruction(JZ, Y, 0, 0)], [instruction(OUT, engine, 0, 0)],
            [instruction(IN, 7, 0, 0)], [instruction(PUSH)], [instruction(PULL)], [immediate(JMP, 0)]]


async def blocked_paths(h: Harness) -> None:
    """Every completed instruction clears the blocked-cycle count (the next bounded wait
    counts from zero). Each way of completing an instruction is followed by a WAITPIN on
    pin 6 with LIMIT 0xFFFFFF; the count is warped to four short of the limit and the
    host then releases the wait: the model sees no timeout, and any count left over by
    the preceding instruction times the DUT out."""
    await h.start()
    steps = finishing_instructions(0)
    for first, chunk in ((0, steps[:15]), (15, steps[15:])):
        for engine in range(4):
            body = [immediate(DIR, 1 << engine), immediate(LIMIT, 0xFFFFFF)]
            level = 1
            for item in finishing_instructions(engine)[first:first + len(chunk)]:
                for word in item:  # jumps, loops and branches go to the next word
                    target = len(body) + 1
                    if word >> 24 in (JMP, LOOP):
                        word = word & ~0xFFFFFF | target
                    elif word >> 24 == JZ:
                        word = word & ~0xFFFF | target
                    body.append(word)
                body.append(instruction(WAITPIN, 6, level))
                level ^= 1
            body.append(instruction(HALT))
            await h.load(engine, body, ownership=1 << engine)
            await h.command(SELECT, engine)
            await h.write(2, 0xD0000000 | engine)  # for the PULL
        h.pins = 0
        await h.idle(4)
        await h.command(START, ALL)
        level = 1
        while any(e.running for e in h.model.engines):
            await h.run_until(lambda: all(e.stalled or not e.running for e in h.model.engines), 64,
                              "engines blocked on pin 6")
            if not any(e.running for e in h.model.engines):
                break
            await h.warp(0xFFFFFF - 4 - h.model.engines[0].blocked)
            h.pins = level << 6  # release
            level ^= 1
            await h.idle(3)
        for engine in range(4):
            status = await read_select(h, engine, RS_STATUS)
            assert status == 0b010, (engine, hex(status))  # halted, no fault
        for engine in range(4):  # empty the queues for the next chunk
            await h.command(SELECT, engine)
            await h.command(FLUSH)
    h.assert_no_faults()


async def wait_itinerary(h: Harness) -> None:
    """WAIT 0xFFFFFF - 3e on engine e, timer walked across 2^k for k = 23..4; each
    engine's pin must rise on the exact cycle its WAIT ends."""
    await h.start()
    for engine in range(4):
        await h.load(engine, [immediate(DIR, 1 << engine), immediate(WAIT, 0xFFFFFF - 3 * engine),
                              immediate(SET, 1 << engine), instruction(HALT)], ownership=1 << engine)
    await h.command(START, ALL)
    await h.idle(4)
    for k in range(23, 3, -1):
        await warp_by(h, h.model.engines[0].wait - ((1 << k) + 12))
        await h.idle(16)
    started = h.cycle
    rises: dict[int, int] = {}
    while len(rises) < 4:
        assert h.cycle - started < 64, "WAIT did not end"
        await h.step()
        for engine in range(4):
            if engine not in rises and h.last_pre.uio_out >> engine & 1:
                rises[engine] = h.cycle
    assert [rises[e] - rises[3] for e in range(4)] == [9, 6, 3, 0], rises
    for engine in range(4):
        assert await read_select(h, engine, RS_STATUS) == 0b010
        assert await h.status(RS_PC) == 4
    h.assert_no_faults()


async def blocked_itinerary(h: Harness) -> None:
    """Blocked-cycle counts walked across 2^k for k = 4..23 on all four engines, then:
    engines 0 and 1 (WAITPIN and WAITEVENT, LIMIT 0xFFFFFF and 0xFFFFFE) time out at the
    exact cycle; engines 2 and 3 (LIMIT 0xFFFFFF and 0xFFFFFD) are released just before
    their limit by pin 6 and a host EVENT, and their next bounded waits (LIMIT 40, 33)
    must count from zero."""
    await h.start()
    programs = {
        0: [immediate(LIMIT, 0xFFFFFF), instruction(WAITPIN, 7, 1)],
        1: [immediate(LIMIT, 0xFFFFFE), instruction(WAITEVENT)],
        2: [immediate(LIMIT, 0xFFFFFF), instruction(WAITPIN, 6, 1), immediate(LIMIT, 40),
            instruction(WAITPIN, 6, 0)],
        3: [immediate(LIMIT, 0xFFFFFD), instruction(WAITEVENT), immediate(LIMIT, 33), instruction(WAITEVENT)],
    }
    for engine, body in programs.items():
        await h.load(engine, [immediate(DIR, 1 << engine), immediate(SET, 1 << engine), *body,
                              instruction(HALT)], ownership=1 << engine)
    await h.command(START, ALL)
    await h.idle(6)
    for k in range(4, 24):
        await warp_by(h, (1 << k) - 12 - h.model.engines[0].blocked)
        await h.idle(16)
    await h.warp(0xFFFFFD - 80 - h.model.engines[0].blocked)
    h.pins = 0x40  # releases engine 2's WAITPIN 6,1 after the input synchronizers
    await h.command(EVENT, 0b1000)  # releases engine 3's WAITEVENT
    await h.run_until(lambda: all(e.fault for e in h.model.engines), 200, "bounded-wait timeouts")
    for engine, pc in enumerate((3, 3, 5, 5)):
        status = await read_select(h, engine, RS_STATUS)
        assert fault_of(status) == 3 and await h.status(RS_PC) == pc, (engine, hex(status))
    assert h.last_pre.uio_oe == 0


async def repeat_itinerary(h: Harness) -> None:
    """COUNT 0xFFFF - e and a LOOP to itself on engine e; the repeat counters are walked
    across 2^k for k = 15..3 and the loops must exit on the exact cycle."""
    await h.start()
    for engine in range(4):
        await h.load(engine, [immediate(DIR, 1 << engine), immediate(COUNT, 0xFFFF - engine), immediate(LOOP, 2),
                              immediate(SET, 1 << engine), instruction(HALT)], ownership=1 << engine)
    await h.command(START, ALL)
    await h.idle(4)
    for k in range(15, 2, -1):
        await warp_by(h, h.model.engines[0].repeat - ((1 << k) + 12))
        await h.idle(16)
    await h.run_until(lambda: not any(e.running for e in h.model.engines), 64, "LOOP exit")
    for engine in range(4):
        assert await read_select(h, engine, RS_PC) == 5
        expected = 0x10000 - engine + 4 if counters(h) else 0  # DIR, COUNT, loops, SET, HALT
        assert await h.status(RS_COUNT) == expected, engine
    h.assert_no_faults()


async def route_itinerary(h: Harness) -> None:
    """Four words circulate on the ring of routes 0->1->2->3->0 (each engine: PULL, MOV rx,
    PUSH), all descriptors 0xFFFF words. The descriptor counts are walked across 2^k for
    k = 15..3 by ``warp_routes``, then run out; the words must arrive unchanged."""
    await h.start()
    seeds = [0x80008000 | e << 24 | 0x5A << 16 | e << 4 | 0x0F for e in range(4)]
    for engine in range(4):
        await h.load(engine, [instruction(PULL), instruction(MOV, RX, TX), instruction(PUSH), immediate(JMP, 0)])
        await h.command(SELECT, engine)
        await h.write(2, seeds[engine])
    for source in range(4):
        await h.command(ROUTE, source | ((source + 1) % 4) << 2 | 16 | 0xFFFF << 5)
    await h.command(START, ALL)
    await h.idle(40)
    for k in range(15, 2, -1):
        lowest = min(route[1] for route in h.model.routes if route is not None)
        if lowest > (1 << k) + 8:
            await h.warp_routes(lowest - ((1 << k) + 8))
        await h.idle(80)
    await h.run_until(lambda: all(route is None for route in h.model.routes), 400, "route expiry")
    await h.idle(16)
    await h.command(STOP, ALL)
    words = []
    for engine in range(4):
        level = await read_select(h, engine, RS_LEVELS)
        await h.command(SELECT, engine)
        words += [await h.read(3) for _ in range(level >> 16)]
    assert len(set(words)) == len(words) and set(words) <= set(seeds), [hex(w) for w in words]
    h.assert_no_faults()


# ------------------------------------------------------------- cocotb tests
async def warped(dut, scenario) -> None:
    h = CocotbHarness(dut)
    assert h.warp_supported(), "RTL core registers not visible: the time warp cannot run"
    await scenario(h)
    dut._log.info("%s: %d cycles simulated; warped %d cycles, %d route words, %d instructions", scenario.__name__,
                  h.cycle, h.warped, h.warped_words, h.warped_instructions)


@cocotb.test(skip=GATE_LEVEL)
async def test_timestamp_rollover(dut):
    """Timestamp and TIME across 2^16..2^31 and the 2^32 rollover."""
    await warped(dut, timestamp_rollover)


@cocotb.test(skip=GATE_LEVEL)
async def test_completed_rollover(dut):
    """READ_SELECT 5 across 2^16..2^31 and 2^32 while all opcodes execute."""
    await warped(dut, completed_rollover)


@cocotb.test(skip=GATE_LEVEL)
async def test_blocked_paths(dut):
    """Each way of completing an instruction leaves a blocked-cycle count of zero."""
    await warped(dut, blocked_paths)


@cocotb.test(skip=GATE_LEVEL)
async def test_wait_itinerary(dut):
    """WAIT 0xFFFFFF: timers across every 2^k boundary, exact end cycles."""
    await warped(dut, wait_itinerary)


@cocotb.test(skip=GATE_LEVEL)
async def test_blocked_itinerary(dut):
    """LIMIT 0xFFFFFx bounded waits: blocked counts across every 2^k, exact timeouts."""
    await warped(dut, blocked_itinerary)


@cocotb.test(skip=GATE_LEVEL)
async def test_repeat_itinerary(dut):
    """COUNT 0xFFFF loops: repeat counters across every 2^k, exact loop exits."""
    await warped(dut, repeat_itinerary)


@cocotb.test(skip=GATE_LEVEL)
async def test_route_itinerary(dut):
    """ROUTE counts of 0xFFFF on four routes across every 2^k, then expiry."""
    await warped(dut, route_itinerary)
