# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""A bounded wait times out on its LIMIT-th unsuccessful sample, and on no other
cycle, for a LIMIT just past each bit of the blocked-cycle count. Checked for
WAITEVENT and WAITPIN on every engine (docs/isa.md).

Specified behaviour (docs/isa.md):
- LIMIT: "imm24 nonzero maximum blocked cycles for WAITPIN/WAITEVENT".
- WAITPIN: "consume one issue edge if already true; otherwise block, fault after
  LIMIT consecutive unsuccessful samples".
- WAITEVENT: "consume own pending mailbox bit or wait bounded by LIMIT".
- Fault code 3 is the "bounded-wait timeout", and "Faults stop execution and
  release output enables".
- "Instructions commit on rising clk edges", "A nonblocking instruction costs
  one cycle", and "Outputs are masked by ownership, run, and fault".

Each round runs one program on all four engines. Engine e owns pin e and runs
``DIR 1<<e; LIMIT n; WAITEVENT`` (no event arrives) or ``DIR 1<<e; LIMIT n;
WAITPIN 7, 1`` (pin 7 is held low). The limits are n = 2^k + 2 for k = 0..23:
- On sample 2^k the count of unsuccessful samples has bit k set for the first
  time.
- The timeout is due two samples later, so a count that goes wrong on the
  sample where one of its bits is first set shows up as a timeout on another
  cycle.

Engine e's output enable (uio_oe bit e) rises on DIR's edge. LIMIT then takes one
edge, the wait's n samples take n more, and the fault's edge releases the
enable. The enable must therefore read high for exactly n + 1 edges, on every
engine, and each engine's status register must then report fault code 3.

Both checks read the DUT's pins and status registers, not the reference model.
The harness also compares the DUT with the model on every cycle; with
PE_SPEC_ONLY=1 that comparison is off, so that only this module's own checks can
fail (the model still runs alongside and tells how long to wait). Model-only runs
(``harness.run_model``) take the enables from the model.

For n > 16 the samples before 2^k - 6 are skipped with the time warp
(``Harness.warp``), which adds the skipped count to the core's blocked-cycle
registers; the samples from 2^k - 6 on, including the one that first sets bit k,
are simulated. The time warp needs the RTL register names. At gate level
(PE_GATE_LEVEL) the tests run the limits up to 2^8 + 2 without it, which the
netlist simulates in full, and leave out the larger ones.

Four mutants of the design of record that flip a count bit on the WAITEVENT path
of one engine once a higher count bit (13, 14, 18 or 21) is set pass the rest
of the default suite; this module fails on each of them (docs/mutation-push.md,
section 8).
"""

from __future__ import annotations

import os
from typing import Any

import cocotb

from harness import CLEAR, RS_STATUS, SELECT, START, CocotbHarness, Harness, immediate, instruction
from scenarios import fault_of
from test_kill_common import DIR, GATE_LEVEL, LIMIT, WAITEVENT, WAITPIN, own, reload

LIMITS = [(1 << k) + 2 for k in range(24)]
WARP_ABOVE = 16  # limits above this are reached with the time warp (RTL)
GL_LIMITS = [n for n in LIMITS if n <= (1 << 8) + 2]  # gate level: k = 0..8, simulated in full
IDLE_BEFORE_WARP = 4
# PE_SPEC_ONLY=1: no per-cycle comparison with the reference model
SPEC_ONLY = os.environ.get("PE_SPEC_ONLY", "") not in ("", "0")


class EnableEdges:
    """Harness observer: the cycle (simulated edges plus warped ones) on which each engine's
    output enable, uio_oe bit e, is first seen high and then first seen low again, read
    before each edge from the DUT (from the model in model-only runs)."""

    def __init__(self) -> None:
        self.rise: dict[int, int] = {}
        self.fall: dict[int, int] = {}

    def clear(self) -> None:
        self.rise, self.fall = {}, {}

    def before(self, h: Harness, ui: int, pins: int) -> None:
        dut = getattr(h, "dut", None)
        if dut is None:
            oe = h.model.outputs(ui).uio_oe
        else:
            value = dut.uio_oe.value
            if not value.is_resolvable:
                return
            oe = value.to_unsigned()
        now = h.cycle + h.warped
        for engine in range(4):
            high = bool(oe >> engine & 1)
            if high and engine not in self.rise:
                self.rise[engine] = now
            elif not high and engine in self.rise and engine not in self.fall:
                self.fall[engine] = now

    def after(self, h: Harness) -> None:
        pass


def image(engine: int, limit: int, bounded_wait: int) -> list[int]:
    return [immediate(DIR, 1 << engine), immediate(LIMIT, limit), bounded_wait]


async def timeout_round(h: Harness, edges: EnableEdges, limit: int, bounded_wait: int, name: str,
                        warp: bool) -> None:
    for engine in range(4):
        await reload(h, engine, image(engine, limit, bounded_wait))
    edges.clear()
    await h.command(START, 0b1111)
    if warp and limit > WARP_ABOVE:
        await h.idle(IDLE_BEFORE_WARP)
        blocked = h.model.engines[0].blocked
        assert all(e.blocked == blocked and e.stalled for e in h.model.engines), (name, limit)
        await h.warp(limit - 8 - blocked)
        budget = 40
    else:
        budget = limit + 40
    await h.run_until(lambda: not any(e.running for e in h.model.engines), budget, f"{name} LIMIT {limit}")
    await h.idle(2)
    for engine in range(4):
        assert engine in edges.rise, f"{name} LIMIT {limit}, engine {engine}: output enable never rose after DIR"
        assert engine in edges.fall, f"{name} LIMIT {limit}, engine {engine}: output enable not released by a timeout"
        held = edges.fall[engine] - edges.rise[engine]
        assert held == limit + 1, (f"{name} LIMIT {limit}, engine {engine}: output enable high for {held} edges, "
                                   f"expected {limit + 1} (LIMIT's edge and {limit} samples)")
    for engine in range(4):
        await h.command(SELECT, engine)
        fault = fault_of(await h.status(RS_STATUS))
        assert fault == 3, f"{name} LIMIT {limit}, engine {engine}: fault code {fault}, expected 3"
    await h.command(CLEAR, 0b1111)


async def limit_timeouts(h: Harness, bounded_wait: int, name: str) -> int:
    """Every engine: DIR 1<<e; LIMIT n; <bounded_wait> for each n of LIMITS (at gate level, or
    without the time warp, GL_LIMITS simulated in full). Returns the number of limits run."""
    await h.start()
    h.pins = 0  # every pad low: WAITPIN 7, 1 never succeeds
    for engine in range(4):
        await own(h, engine, 1 << engine)
    edges = EnableEdges()
    h.observers.append(edges)
    # On RTL the 24 limits need the time warp; a silent fallback to the
    # gate-level list would leave the high count bits untested.
    assert GATE_LEVEL or h.warp_supported(), "time warp unavailable on RTL"
    warp = h.warp_supported() and not GATE_LEVEL
    limits = LIMITS if warp else GL_LIMITS
    for limit in limits:
        await timeout_round(h, edges, limit, bounded_wait, name, warp)
    h.observers.remove(edges)
    return len(limits)


async def waitevent_timeouts(h: Harness) -> int:
    return await limit_timeouts(h, instruction(WAITEVENT), "WAITEVENT")


async def waitpin_timeouts(h: Harness) -> int:
    return await limit_timeouts(h, instruction(WAITPIN, 7, 1), "WAITPIN")


def harness(dut: Any) -> CocotbHarness:
    """CocotbHarness(dut); with PE_SPEC_ONLY=1, one whose lockstep comparison is always off."""
    if not SPEC_ONLY:
        return CocotbHarness(dut)

    class Unchecked(CocotbHarness):
        # Harness sets `checking` on reset edges; this class-level property ignores those writes.
        checking = property(lambda self: False, lambda self, value: None)

    h = Unchecked(dut)
    h.log("PE_SPEC_ONLY=1: the DUT is not compared with the reference model")
    return h


@cocotb.test()
async def test_waitevent_times_out_at_limit_past_each_count_bit(dut):
    """WAITEVENT with no event, LIMIT 2^k + 2 (k = 0..23; 0..8 at gate level), all four engines: enable high for LIMIT + 1 edges, fault 3."""
    h = harness(dut)
    count = await waitevent_timeouts(h)
    h.log("WAITEVENT timed out on the LIMIT-th sample for %d limits on every engine (%d cycles simulated, %d warped)",
          count, h.cycle, h.warped)


@cocotb.test()
async def test_waitpin_times_out_at_limit_past_each_count_bit(dut):
    """WAITPIN on a pin that stays low, LIMIT 2^k + 2 (k = 0..23; 0..8 at gate level), all four engines: enable high for LIMIT + 1 edges, fault 3."""
    h = harness(dut)
    count = await waitpin_timeouts(h)
    h.log("WAITPIN timed out on the LIMIT-th sample for %d limits on every engine (%d cycles simulated, %d warped)",
          count, h.cycle, h.warped)
