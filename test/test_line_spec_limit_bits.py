# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""A bounded wait times out on its LIMIT-th unsuccessful sample, and on no
other cycle, for a LIMIT just past each bit of the blocked-cycle count. Checked
for WAITEVENT and WAITPIN on every engine (docs/isa.md).

Specified behaviour (docs/isa.md, "Instructions"):
- LIMIT "imm24 nonzero maximum blocked cycles for WAITPIN/WAITEVENT".
- WAITPIN "consume one issue edge if already true; otherwise block, fault
  after LIMIT consecutive unsuccessful samples".
- WAITEVENT "consume own pending mailbox bit or wait bounded by LIMIT".
- Fault code 3 is the "bounded-wait timeout" ("Faults").
- ("Machine") "Instructions commit on rising clk edges", "A nonblocking
  instruction costs one cycle", and "Outputs are masked by ownership, run, and
  fault".

Each round runs one program on all four engines. Engine e owns pin e and runs
``DIR 1<<e; LIMIT n; WAITEVENT`` (no event arrives) or ``DIR 1<<e; LIMIT n;
WAITPIN 7, 1`` (pin 7 is held low). The limits are n = 2^k + 2 for
k = 0..23:
- On sample 2^k the count of unsuccessful samples reaches a value with bit k
  set for the first time.
- The timeout is due two samples later.
- So the first time each of the count's 24 bits becomes 1 lies in the last
  samples before its limit.

Engine e's output enable (uio_oe bit e) rises on DIR's edge. LIMIT then takes
one edge, the wait's n samples take n more, and the fault's edge releases the
enable. The enable must therefore read high for exactly n + 1 edges, on every
engine, and each engine's status register must then report fault code 3.

Both checks read the DUT's pins and status registers, not the reference model.
The harness also compares the DUT with the model on every cycle; with
PE_SPEC_ONLY=1 that comparison is off (test_line_spec_common.spec_harness).

For n > 16 the samples before 2^k - 6 are skipped with the time warp
(harness.warp), which adds the skipped count to the core's blocked-cycle
registers. The time warp needs the RTL, so the module is skipped at gate
level. It does not use the line unit and is not skipped on variants without
one.
"""

from __future__ import annotations

from typing import Any

import cocotb

from harness import CLEAR, START, Harness, immediate, instruction
from line_scenarios import engine_status
from scenarios import fault_of
from test_kill_common import DIR, GATE_LEVEL, LIMIT, WAITEVENT, WAITPIN, own, reload
from test_line_spec_common import spec_harness

LIMITS = [(1 << k) + 2 for k in range(24)]
WARP_ABOVE = 16  # limits above this are reached with the time warp
IDLE_BEFORE_WARP = 4


class EnableEdges:
    """Harness observer: the cycle (simulated edges plus warped ones) on which each engine's
    output enable, uio_oe bit e, is first seen high and then first seen low again, read
    from the DUT before each edge."""

    def __init__(self) -> None:
        self.rise: dict[int, int] = {}
        self.fall: dict[int, int] = {}

    def clear(self) -> None:
        self.rise, self.fall = {}, {}

    def before(self, h: Harness, ui: int, pins: int) -> None:
        value = h.dut.uio_oe.value
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


async def timeout_round(h: Any, edges: EnableEdges, limit: int, bounded_wait: int, name: str) -> None:
    for engine in range(4):
        await reload(h, engine, image(engine, limit, bounded_wait))
    edges.clear()
    await h.command(START, 0b1111)
    if limit > WARP_ABOVE:
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
        fault = fault_of(await engine_status(h, engine))
        assert fault == 3, f"{name} LIMIT {limit}, engine {engine}: fault code {fault}, expected 3"
    await h.command(CLEAR, 0b1111)


async def setup(dut: Any) -> tuple[Any, EnableEdges]:
    h = spec_harness(dut)
    await h.start()
    h.pins = 0  # every pad low: WAITPIN 7, 1 never succeeds
    for engine in range(4):
        await own(h, engine, 1 << engine)
    edges = EnableEdges()
    h.observers.append(edges)
    return h, edges


@cocotb.test(skip=GATE_LEVEL)
async def test_waitevent_times_out_at_limit_past_each_count_bit(dut):
    """WAITEVENT with no event, LIMIT 2^k + 2 (k = 0..23), all four engines: enable high for LIMIT + 1 edges, fault 3."""
    h, edges = await setup(dut)
    for limit in LIMITS:
        await timeout_round(h, edges, limit, instruction(WAITEVENT), "WAITEVENT")
    h.log("WAITEVENT timed out on the LIMIT-th sample for %d limits on every engine", len(LIMITS))


@cocotb.test(skip=GATE_LEVEL)
async def test_waitpin_times_out_at_limit_past_each_count_bit(dut):
    """WAITPIN on a pin that stays low, LIMIT 2^k + 2 (k = 0..23), all four engines: enable high for LIMIT + 1 edges, fault 3."""
    h, edges = await setup(dut)
    for limit in LIMITS:
        await timeout_round(h, edges, limit, instruction(WAITPIN, 7, 1), "WAITPIN")
    h.log("WAITPIN timed out on the LIMIT-th sample for %d limits on every engine", len(LIMITS))
