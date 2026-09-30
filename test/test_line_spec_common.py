# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers of the test_line_spec_* modules (no tests of its own).

The test_line_spec_* modules check behaviour that docs/isa.md specifies
(mostly in "Line-unit extension") and that the line-unit mutation campaign
found unchecked (campaigns/mutation/README.md, "Line unit (diet8_rec16)"). Each
module states the specified behaviour it checks and cites the ISA text; the
expected values come from that text (``crc_rule`` below, the ticker formula,
the independent encoders of line_support.py), and the harness also compares
the DUT with the reference model on every cycle.

``spec_harness`` builds the modules' cocotb harness. With PE_SPEC_ONLY=1 it
turns that per-cycle comparison off, so that only a module's own assertions
can fail. The model still runs alongside, and the modules use it to know how
long to wait. The line-unit mutation campaign uses this switch to count the
kills that a module's assertions make without the reference model. Modules
that construct CocotbHarness directly do not take part.
"""

from __future__ import annotations

import os
from typing import Any

from line_support import bits_lsb, bits_msb, line_unit_enabled

# Gate level (PE_GATE_LEVEL): the modules are skipped unless PE_LINE_GL_FULL=1, like the long line-unit demos
GATE_LEVEL_SKIP = bool(os.environ.get("PE_GATE_LEVEL")) and not os.environ.get("PE_LINE_GL_FULL")
SKIP = not line_unit_enabled() or GATE_LEVEL_SKIP
# PE_SPEC_ONLY=1: no per-cycle comparison with the reference model (spec_harness)
SPEC_ONLY = os.environ.get("PE_SPEC_ONLY", "") not in ("", "0")


def spec_harness(dut: Any):  # noqa: ANN201 - harness.CocotbHarness
    """CocotbHarness(dut); with PE_SPEC_ONLY=1, one whose lockstep comparison is always off."""
    from harness import CocotbHarness

    if not SPEC_ONLY:
        return CocotbHarness(dut)

    class Unchecked(CocotbHarness):
        # Harness sets `checking` on reset edges; this class-level property ignores those writes.
        checking = property(lambda self: False, lambda self, value: None)

    h = Unchecked(dut)
    h.log("PE_SPEC_ONLY=1: the DUT is not compared with the reference model")
    return h

# docs/isa.md, "CRC": preset -> (reflected polynomial for LSB first, normal polynomial for MSB first)
PRESETS = {0: (0x0014, 0x2800), 1: (0xA001, 0x8005), 2: (0x4CD1, 0x8B32), 3: (0x8408, 0x1021)}


def crc_rule(bits: list[int], preset: int, msb: bool, init: int) -> int:
    """The ISA's CRC step rule, one step per data bit in the order sent or received."""
    reflected, normal = PRESETS[preset]
    crc = init & 0xFFFF
    for bit in bits:
        if msb:
            feedback = (crc >> 15 & 1) ^ bit
            crc = (crc << 1 & 0xFFFF) ^ (normal if feedback else 0)
        else:
            feedback = (crc & 1) ^ bit
            crc = (crc >> 1) ^ (reflected if feedback else 0)
    return crc


def sent_bits(word: int, n: int, msb: bool) -> list[int]:
    """The n bits a transfer shifts out of a 32-bit register: bits 31, 30, ... MSB first, else 0, 1, ..."""
    return bits_msb(word, 32)[:n] if msb else bits_lsb(word, n)
