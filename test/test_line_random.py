# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Constrained-random lockstep test of the line unit (line_random.py,
docs/extension.md) for variants with options.line_unit; skipped otherwise.

Environment:
  PE_SEED                 base seed (default 0x11AE5EED; "random" picks one)
  PE_LINE_RANDOM_ITERS    number of cases (default 16 RTL, 1 gate level)
  PE_LINE_RANDOM_FIRST    first case index (default 0)
  PE_LINE_RANDOM_CYCLES   cycles per case after START (default 6000)
Case k uses seed (base + k). The functional coverage of the run (events of
model/line_unit.py) is logged at the end.
"""

from __future__ import annotations

import os
import time
from collections import Counter

import cocotb

from harness import CocotbHarness, env_int
from line_random import make_case, run_case
from line_support import SKIP_REASON, line_unit_enabled

SKIP = not line_unit_enabled()
if SKIP:
    print(f"test_line_random: {SKIP_REASON}")


def seed_from_env() -> int:
    value = os.environ.get("PE_SEED", "")
    if value == "random":
        return int(time.time() * 1000) & 0xFFFFFFFF
    return int(value, 0) if value else 0x11AE5EED


@cocotb.test(skip=SKIP)
async def test_line_random_lockstep(dut):
    """Random line-unit programs on up to four engines, random pads and host traffic, lockstep every cycle."""
    gate_level = bool(os.environ.get("PE_GATE_LEVEL"))
    seed = seed_from_env()
    iterations = env_int("PE_LINE_RANDOM_ITERS", 1 if gate_level else 16)
    first = env_int("PE_LINE_RANDOM_FIRST", 0)
    cycles = env_int("PE_LINE_RANDOM_CYCLES", 6000)
    h = CocotbHarness(dut)
    await h.start()
    total: Counter[str] = Counter()
    start_cycle = h.cycle
    for k in range(first, first + iterations):
        case = make_case(seed + k, fifo=h.model.config.fifo_words, cycles=cycles)
        h.context = lambda k=k: f"line random case {k} (seed {seed + k:#x})"
        h.model.events.clear()
        await run_case(h, case)
        total.update(h.model.events)
    dut._log.info("line random: seed %#x, cases %d..%d, %d cycles", seed, first, first + iterations - 1,
                  h.cycle - start_cycle)
    for name, count in sorted(total.items()):
        dut._log.info("  coverage %-32s %d", name, count)
