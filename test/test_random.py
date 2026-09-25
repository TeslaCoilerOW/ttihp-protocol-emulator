# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Constrained-random lockstep differential test (DUT vs model.reference).

Random legal programs for all four engines (with a few deliberate faults) plus
random host traffic; uo_out, uio_out and uio_oe (hence every host read nibble)
are compared with the Python reference on every clock. See random_gen.py.

Environment:
  PE_SEED              base seed (default 0x5EED2027; "random" picks one)
  PE_RANDOM_ITERS      number of cases (default 64 RTL, 2 gate level)
  PE_RANDOM_FIRST      first case index (default 0); use with ITERS=1 to rerun one case
  PE_RANDOM_CYCLES     host-traffic cycles per case after START (default 2000)
  PE_RANDOM_GEN        generator generation (default 2; 1 = the cases of the 2026-09-25 campaigns)
  PE_MINIMIZE          1 (default) shrinks a failing case; 0 disables
  PE_MINIMIZE_BUDGET   maximum re-runs for the minimizer (default 60)
  PE_REPLAY            path of a saved case JSON to run instead of generating
  PE_INJECT_MODEL_BUG  "xor": corrupt the model after XOR (checker/minimizer self-test)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import cocotb

from harness import CocotbHarness, LockstepMismatch, env_int
from random_gen import GENERATION, BugInjector, Case, Coverage, make_case, minimize, run_case

OUTPUT = Path(__file__).resolve().parent / "output"


def seed_from_env() -> int:
    value = os.environ.get("PE_SEED", "")
    if value == "random":
        return int(time.time() * 1000) & 0xFFFFFFFF
    return int(value, 0) if value else 0x5EED2027


@cocotb.test()
async def test_random_lockstep(dut):
    """Constrained-random programs + host traffic, lockstep-compared every cycle."""
    gate_level = bool(os.environ.get("PE_GATE_LEVEL"))
    seed = seed_from_env()
    iterations = env_int("PE_RANDOM_ITERS", 2 if gate_level else 64)
    first = env_int("PE_RANDOM_FIRST", 0)
    cycles = env_int("PE_RANDOM_CYCLES", 2000)
    generation = env_int("PE_RANDOM_GEN", GENERATION)
    replay = os.environ.get("PE_REPLAY")
    h = CocotbHarness(dut)
    if os.environ.get("PE_INJECT_MODEL_BUG") == "xor":
        h.observers.append(BugInjector())
    coverage = Coverage()
    await h.start()
    if replay:
        cases = [Case.from_json(Path(replay).read_text())]
        dut._log.info("replaying %s", replay)
    else:
        dut._log.info("random lockstep: PE_SEED=%#x cases %d..%d, %d host cycles each, generator %d",
                      seed, first, first + iterations - 1, cycles, generation)
        cases = (make_case(seed, index, h.model.config, cycles=cycles, generation=generation)
                 for index in range(first, first + iterations))
    started = time.time()
    try:
        for case in cases:
            before = h.cycle
            try:
                await run_case(h, case, coverage)
            except LockstepMismatch as mismatch:
                coverage.detach(h)
                await report_failure(dut, h, case, mismatch)
                raise
            dut._log.info("case %d (seed %#x): %d cycles in lockstep, OK", case.index, case.seed, h.cycle - before)
    finally:
        dut._log.info("%s", coverage.summary())
        dut._log.info("random lockstep: %d cycles in %.1f s", h.cycle, time.time() - started)


async def report_failure(dut, h: CocotbHarness, case: Case, mismatch: LockstepMismatch) -> None:
    OUTPUT.mkdir(exist_ok=True)
    stem = f"random-failure-seed{case.seed:#x}-case{case.index}"
    (OUTPUT / f"{stem}.json").write_text(case.to_json())
    dut._log.error("REPRODUCE: PE_SEED=%#x PE_RANDOM_FIRST=%d PE_RANDOM_ITERS=1 make COCOTB_TEST_MODULES=test_random"
                   "  (or PE_REPLAY=%s)", case.seed, case.index, OUTPUT / f"{stem}.json")
    dut._log.error("failing case:\n%s", case.describe())
    if os.environ.get("PE_MINIMIZE", "1") == "0":  # replays too: a saved campaign case can be shrunk
        return
    budget = env_int("PE_MINIMIZE_BUDGET", 60)
    dut._log.info("minimizing (budget %d re-runs) ...", budget)
    h.quiet = True
    try:
        small = await minimize(h, case, budget, lambda text: dut._log.info("%s", text))
    finally:
        h.quiet = False
    (OUTPUT / f"{stem}-min.json").write_text(small.to_json())
    dut._log.error("minimized case (PE_REPLAY=%s):\n%s", OUTPUT / f"{stem}-min.json", small.describe())
    h.quiet = True
    try:
        await run_case(h, small)
    except LockstepMismatch as again:
        dut._log.error("minimized mismatch:\n%s", str(again).split("\n  case seed")[0])
    finally:
        h.quiet = False
    dut._log.error("original mismatch:\n%s", str(mismatch).split("\n  case seed")[0])
