# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""XFER makes exactly 2a clock transitions, b cycles apart, for every bit count a.

Specified behaviour (docs/isa.md, XFER):
- "a bit count 1..datapath width, b half-period 1..255 cycles, c bits0 CPOL".
- "XFER sets clock idle on issue, ..., then waits b clocks to its first
  transition."
- "Exactly 2*a transitions occur, each separated by b cycles. PC advances at
  final idle edge."
- "A nonblocking instruction costs one cycle"; DIR "sets logical output
  enables"; HALT "stop and release output enables".

So an XFER issued on edge T sets the clock pin to CPOL on T, toggles it on
edges T + kb for k = 1 .. 2a, and the next instruction issues on edge
T + 2ab + 1.

``xfer_edges``: all four engines at once; engine e owns pins 2e and 2e + 1 and
runs ``PINS clock=2e; DIR 1<<2e; XFER a, b, CPOL`` for every a from 1 to 32
in turn, then HALT. CPOL alternates in pairs of bit counts (so the clock is
also set idle on issue at a level other than the one the last transfer left).
One program per half-period b = 1, 2, 3 and 4. An observer reads each
engine's clock pin (uio_out and uio_oe bit 2e) from the DUT before every edge;
from the edge its enable rises to the edge HALT releases it, the pin must take
exactly the levels of the rule above (``expected_clock``), computed here, not
by the reference model. At gate level the test runs b = 2 only.
"""

from __future__ import annotations

import cocotb

from harness import START, Harness, immediate, instruction
from test_kill_common import DIR, GATE_LEVEL, HALT, PINS, XFER, own, reload
from test_spec_common import PinTrace, harness

HALF_PERIODS = [1, 2, 3, 4]
GL_HALF_PERIODS = [2]


def transfers(width: int, b: int) -> list[tuple[int, int, int]]:
    """(a, b, CPOL) of every XFER of one program."""
    return [(a, b, a >> 1 & 1) for a in range(1, width + 1)]


def program(engine: int, xfers: list[tuple[int, int, int]]) -> list[int]:
    clock, tx = 2 * engine, 2 * engine + 1
    words = [immediate(PINS, clock | tx << 3 | tx << 6), immediate(DIR, 1 << clock)]
    words += [instruction(XFER, a, b, cpol) for a, b, cpol in xfers]
    return words + [instruction(HALT)]


def expected_clock(xfers: list[tuple[int, int, int]]) -> list[tuple[int, int]]:
    """(enable, level) of the clock pin after each edge, from DIR's edge to HALT's edge."""
    level = 0  # START cleared the logical values
    states = [(1, level)]  # DIR's edge
    for a, b, cpol in xfers:
        level = cpol  # issue edge: clock idle
        states.append((1, level))
        for _ in range(2 * a):
            states += [(1, level)] * (b - 1)
            level ^= 1
            states.append((1, level))  # edges T + kb; the last is the final idle edge
    states.append((0, 0))  # HALT's edge: enable released, value masked by run
    return states


def clock_levels(samples: list[tuple[int, int, int]], pin: int) -> list[tuple[int, int]]:
    return [(oe >> pin & 1, out >> pin & 1) if oe >= 0 else (-1, -1) for _, out, oe in samples]


async def xfer_edges(h: Harness, half_periods: list[int]) -> int:
    await h.start()
    h.pins = 0
    width = h.model.config.width
    for engine in range(4):
        await own(h, engine, 0b11 << 2 * engine)
    trace = PinTrace()
    h.observers.append(trace)
    checked = 0
    for b in half_periods:
        xfers = transfers(width, b)
        for engine in range(4):
            await reload(h, engine, program(engine, xfers))
        expected = expected_clock(xfers)
        trace.start()
        await h.command(START, 0b1111)
        await h.run_until(lambda: not any(e.running for e in h.model.engines), len(expected) + 40, f"XFERs b={b}")
        await h.idle(2)
        samples = trace.stop()
        for engine in range(4):
            levels = clock_levels(samples, 2 * engine)
            rise = next((i for i, (oe, _) in enumerate(levels) if oe == 1), None)
            assert rise is not None, f"b={b}, engine {engine}: the clock pin's enable never rose"
            assert set(levels[:rise]) == {(0, 0)}, f"b={b}, engine {engine}: clock pin moved before DIR"
            seen = levels[rise:rise + len(expected)]
            if seen != expected:
                bad = next(i for i, (x, y) in enumerate(zip(seen + [None] * len(expected), expected)) if x != y)
                raise AssertionError(f"b={b}, engine {engine}: clock pin (enable, level) {seen[bad:bad + 6]} on edges "
                                     f"{bad}.. after DIR, isa.md gives {expected[bad:bad + 6]}")
            assert set(levels[rise + len(expected):]) <= {(0, 0)}, f"b={b}, engine {engine}: pin moved after HALT"
            checked += 1
    h.observers.remove(trace)
    h.assert_no_faults()
    return checked


@cocotb.test()
async def test_spec_xfer_transitions_every_bit_count(dut):
    """XFER a = 1..32, b = 1..4 (b = 2 at gate level), every engine: clock pin levels follow isa.md edge by edge."""
    h = harness(dut)
    count = await xfer_edges(h, GL_HALF_PERIODS if GATE_LEVEL else HALF_PERIODS)
    h.log("XFER clock pins as isa.md gives them in %d engine programs (%d cycles)", count, h.cycle)
