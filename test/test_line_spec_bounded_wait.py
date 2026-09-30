# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""A bounded wait after any line-unit instruction times out after exactly LIMIT
unsuccessful samples, on every engine (docs/isa.md).

Specified behaviour (docs/isa.md, "Instructions"): "WAITPIN a pin, b expected
bit 0/1, c=0; consume one issue edge if already true; otherwise block, fault
after LIMIT consecutive unsuccessful samples"; WAITEVENT likewise "wait bounded
by LIMIT"; LIMIT "imm24 nonzero maximum blocked cycles for WAITPIN/WAITEVENT";
fault code 3 is the "bounded-wait timeout". The count of unsuccessful samples
starts with the bounded wait, so what ran before it (a line XFER that holds
the engine for many cycles, one that ends on SE0, with a stuff error, after an
arbitration loss or with a trailing stuff bit, or LTIM, LCFG, CRC, LSTAT, or a
classic XFER with the CRC bit) does not change when the timeout comes.

As in test_kill_blocked.exact_timeout_paths, all four engines run
``LIMIT 3; <path>; WAITPIN`` (and ``WAITEVENT`` with LIMIT 3 and 5) on a pin
that never matches. The fault flag (uo_out bit 7) is compared with the
reference model on every cycle, so a count left behind by a path changes the
cycle of the fault; each engine's status register (read through the host
interface) must then report fault code 3. The environment
holds every pad low, so a sampling XFER reads 0s: with the pair set both pins
are low (SE0 at the first mid-bit tick), with stuffing on runs of either
polarity the stuff bit after two 0s is wrong, and a drive-and-sample XFER
that drives 1s with the monitor on loses arbitration.
"""

from __future__ import annotations

import cocotb

from harness import CLEAR, FLUSH, SELECT, START, CocotbHarness, Harness, immediate, instruction
from line_scenarios import engine_status
from line_support import (SKIP_REASON, TX, X, crc_get, crc_preset, crc_set, lcfg, line_unit_enabled, load, lstat,
                          ltim, not_, pins, wait, xfer, xline)
from scenarios import fault_of
from test_kill_common import LIMIT, WAITEVENT, WAITPIN, own, reload
from test_line_spec_common import SKIP

if not line_unit_enabled():
    print(f"test_line_spec_bounded_wait: {SKIP_REASON}")

LIMIT_SMALL = 3


def paths(engine: int) -> list[tuple[str, list[int]]]:
    """(label, instructions) of line-unit paths that complete right before the bounded wait.
    Engine e owns pins 2e (data) and 2e + 1 (pair / classic clock); neither is enabled."""
    data, pair = 2 * engine, 2 * engine + 1
    rx = (data + 2) % 8  # another engine's data pin: not enabled, reads 0
    base = [pins(pair, data, rx)]
    return [
        ("LTIM", [ltim(3, 85, 2)]),
        ("LTIM 0", [ltim(3), ltim(0)]),
        ("LCFG", [lcfg(1, 6, pair=True, se0=True, init=1)]),
        ("CRC set", [load(X, 0x1234), crc_set(X)]),
        ("CRC get", [crc_get(X)]),
        ("CRC preset", [crc_preset(2)]),
        ("LSTAT", [lstat(X)]),
        ("line XFER drive", base + [ltim(2), not_(TX), xline(8, drive=True)]),
        ("line XFER drive, trailing stuff bit", base + [ltim(2), lcfg(1, 3), not_(TX), xline(6, drive=True)]),
        ("line XFER Manchester, pair", base + [ltim(2), lcfg(2, pair=True), xline(5, drive=True, crc=True)]),
        ("line XFER sample", base + [ltim(2), xline(7, sample=True, crc=True)]),
        ("line XFER sample, SE0 end", base + [ltim(2), lcfg(1, pair=True, se0=True), xline(9, sample=True)]),
        ("line XFER sample, stuff error", base + [ltim(2), lcfg(0, 2, either=True), xline(8, sample=True)]),
        ("line XFER arbitration lost", base + [ltim(3), lcfg(arb=True, init=1), not_(TX),
                                               xline(6, drive=True, sample=True)]),
        ("line XFER after a WAIT with the ticker running", base + [ltim(4, 200, 1), wait(9), xline(3, drive=True)]),
        ("classic XFER with the CRC bit", base + [xfer(4, 1, 0x48)]),
        ("classic XFER sampling with the CRC bit", base + [xfer(3, 2, 0x50)]),
    ]


def program(limit: int, body: list[int], bounded_wait: int) -> list[int]:
    return [immediate(LIMIT, limit)] + body + [bounded_wait]


async def run_round(h: Harness, images: list[list[int]], label: str) -> None:
    """Load one image per engine, START all, wait for the four timeouts; each engine's status
    register must report fault code 3."""
    for engine, image in enumerate(images):
        await reload(h, engine, image)
    await h.command(START, 0b1111)
    await h.run_until(lambda: not any(e.running for e in h.model.engines), 2000, f"timeouts after {label}")
    for engine in range(4):
        assert h.model.engines[engine].fault == 3, (label, engine, h.model.engines[engine].fault)
        dut_fault = fault_of(await engine_status(h, engine))
        assert dut_fault == 3, (label, engine, dut_fault)
    await h.command(CLEAR, 0b1111)
    for engine in range(4):
        await h.command(SELECT, engine)
        await h.command(FLUSH)


async def setup(h: Harness) -> None:
    await h.start()
    h.pins = 0  # every pad low; the bounded waits expect pin 2e + 1 high
    for engine in range(4):
        await own(h, engine, 3 << 2 * engine)


@cocotb.test(skip=SKIP)
async def test_waitpin_timeout_exact_after_line_paths(dut):
    """LIMIT 3; <line-unit path>; WAITPIN on a pin that never matches: fault 3 on the model's cycle, all engines."""
    h = CocotbHarness(dut)
    await setup(h)
    for index, (label, _) in enumerate(paths(0)):
        images = [program(LIMIT_SMALL, paths(e)[index][1], instruction(WAITPIN, 2 * e + 1, 1)) for e in range(4)]
        await run_round(h, images, label)
    h.assert_no_faults()


@cocotb.test(skip=SKIP)
async def test_waitevent_timeout_exact_after_line_paths(dut):
    """LIMIT 3 or 5; <line-unit path>; WAITEVENT with no event: fault 3 on the model's cycle, all engines."""
    h = CocotbHarness(dut)
    await setup(h)
    for index, (label, _) in enumerate(paths(0)):
        images = [program(LIMIT_SMALL + (e & 1) * 2, paths(e)[index][1], instruction(WAITEVENT)) for e in range(4)]
        await run_round(h, images, "WAITEVENT " + label)
    h.assert_no_faults()
