# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers of the test_spec_* modules (no tests of its own).

The test_spec_* modules check behaviour that docs/isa.md specifies and that the
rest of the suite runs but does not observe. Each module's docstring quotes the
isa.md text it checks. As everywhere in the suite, the DUT runs in lockstep with
the reference model (every cycle's uo_out, uio_out and uio_oe are compared) and
every scenario also runs on the model alone (``harness.run_model``). On top of
that each test makes its own checks from the DUT's pins and read-back words
against values derived from isa.md, not from the model. With PE_SPEC_ONLY=1 the
per-cycle comparison with the model is off, so that only those checks can fail;
the model still runs alongside and tells the test when to act.
"""

from __future__ import annotations

import os
from typing import Any

from harness import READ_SELECT, CocotbHarness, Harness

# PE_SPEC_ONLY=1: no per-cycle comparison with the reference model
SPEC_ONLY = os.environ.get("PE_SPEC_ONLY", "") not in ("", "0")

# Eight 8-bit patterns: for any two distinct bit positions p and q, some pattern
# has (p, q) = (0, 1) and another (1, 0) (0x55/0xAA, 0x33/0xCC and 0x0F/0xF0 split
# the positions by bit 0, 1 and 2 of their index), and 0x00 and 0xFF give (0, 0)
# and (1, 1).
BYTE_PATTERNS = [0x00, 0xFF, 0x55, 0xAA, 0x33, 0xCC, 0x0F, 0xF0]


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


class PinTrace:
    """Harness observer: the DUT's uo_out, uio_out and uio_oe as sampled before every
    edge (from the model in model-only runs), one entry per simulated cycle."""

    def __init__(self) -> None:
        self.samples: list[tuple[int, int, int]] = []
        self.recording = False

    def start(self) -> None:
        self.samples, self.recording = [], True

    def stop(self) -> list[tuple[int, int, int]]:
        self.recording = False
        return self.samples

    def before(self, h: Harness, ui: int, pins: int) -> None:
        if not self.recording:
            return
        dut = getattr(h, "dut", None)
        if dut is None:
            out = h.model.outputs(ui)
            self.samples.append((out.uo, out.uio_out, out.uio_oe))
            return
        values = (dut.uo_out.value, dut.uio_out.value, dut.uio_oe.value)
        if all(v.is_resolvable for v in values):
            self.samples.append(tuple(v.to_unsigned() for v in values))
        else:
            self.samples.append((-1, -1, -1))  # never equal to an expected value

    def after(self, h: Harness) -> None:
        pass


def runs(sequence: list[Any]) -> list[Any]:
    """The sequence with consecutive repeats removed."""
    out: list[Any] = []
    for item in sequence:
        if not out or out[-1] != item:
            out.append(item)
    return out


async def read_word(h: Harness, selection: int | None = None) -> int:
    """Read the selected engine's window-0 word; with ``selection``, send READ_SELECT first.
    The one-cycle window change in front of the read drops any earlier snapshot."""
    if selection is not None:
        await h.command(READ_SELECT, selection)
    h.window = 1
    await h.step()
    return await h.read(0)
