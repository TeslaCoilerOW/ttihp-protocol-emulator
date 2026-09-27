# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The extension study's demo scenarios on the whole chip (line_demos.py,
docs/extension.md), for variants with options.line_unit
(PE_VARIANT=diet8_rec16); skipped on other variants.

The firmware comes from firmware/ext (source and bytecode identity
checked). After START the host performs no transaction until the check, as
with a free-running chip clock. Every cycle is compared with the reference
model; pad waveforms are decoded by independent protocol references.
The 10BASE-T test runs 660,000 cycles (about 3.5 minutes) to reach the first
link pulse. At gate level it stops after the frame and the CAN tests are
skipped, unless PE_LINE_GL_FULL=1.
"""

from __future__ import annotations

import os

import cocotb

import line_demos as d
from harness import CocotbHarness
from line_support import SKIP_REASON, line_unit_enabled

SKIP = not line_unit_enabled()
GATE_LEVEL = bool(os.environ.get("PE_GATE_LEVEL"))
# Gate level runs the long demos only on request (PE_LINE_GL_FULL=1).
SHORT = GATE_LEVEL and not os.environ.get("PE_LINE_GL_FULL")
if SKIP:
    print(f"test_line_demos: {SKIP_REASON}")


@cocotb.test(skip=SKIP)
async def test_10base_t_udp(dut):
    """10base-t-udp + 1 relay: 76-byte frame decoded, CRC-32 residue, TP_IDL, NLP 640,000 cycles later."""
    await d.ethernet(CocotbHarness(dut), relays=1, until_nlp=not SHORT)


@cocotb.test(skip=SKIP)
async def test_10base_t_understaged(dut):
    """Negative control: without the relay only 8 of 17 words are staged and the frame breaks after them."""
    await d.ethernet(CocotbHarness(dut), relays=0, negative=True)


@cocotb.test(skip=SKIP or SHORT)
async def test_can_rx_minimum_spacing(dut):
    """can-node: TX with ACK, then RX of DLC 8/5 and DLC 0/4/1 frames at the minimum spacing, each ACKed in its slot."""
    await d.can_rx_min_spacing(CocotbHarness(dut))


@cocotb.test(skip=SKIP or SHORT)
async def test_can_back_to_back(dut):
    """can-node: two frames back to back, arbitration won against a simultaneous SOF, the loser received."""
    await d.can_back_to_back(CocotbHarness(dut))


@cocotb.test(skip=SKIP or SHORT)
async def test_can_arbitration_lost(dut):
    """can-node: arbitration lost to a simultaneous SOF: fault 72, TXD recessive afterwards."""
    await d.can_arbitration_lost(CocotbHarness(dut))


@cocotb.test(skip=SKIP or SHORT)
async def test_can_negative_revision2(dut):
    """Negative control: the revision-2 CAN listing fails at the minimum frame spacing."""
    await d.can_negative_rev2(CocotbHarness(dut))


@cocotb.test(skip=SKIP or SHORT)
async def test_can_negative_overflow(dut):
    """Negative control: RX at the minimum spacing without drain overflows the RX queue (fault 4)."""
    await d.can_negative_overflow(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_can_reset_mid_frame(dut):
    """Reset in the middle of a CAN frame: TXD released, engine idle, every line-unit register 0 (RTL)."""
    await d.can_reset_mid_frame(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_usb_ls_in_responder(dut):
    """usb-ls-in-responder: IN token in, DATA1 + 8 bytes + CRC16 out, turnaround 2..6.5 bit times."""
    await d.usb_in_responder(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_usb_ls_rejects_then_answers(dut):
    """usb-ls-in-responder: bad-CRC5 IN, OUT and SETUP tokens get no answer; a later IN does, with EOP SE0 then J."""
    await d.usb_rejects_then_answers(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_usb_ls_any_address(dut):
    """usb-ls-in-responder has no address or endpoint filter: an IN to address 0x05 endpoint 3 is answered."""
    await d.usb_any_address(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_crc16_stream(dut):
    """crc16-stream: CRC-16/IBM-3740 and XMODEM jobs over queued words."""
    await d.crc_stream(CocotbHarness(dut))
