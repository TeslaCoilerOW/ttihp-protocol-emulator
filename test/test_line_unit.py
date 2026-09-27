# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Directed tests of the line unit (docs/extension.md), one or more per
instruction and edge case, for variants with options.line_unit
(PE_VARIANT=diet8_rec16). On other variants every test is skipped.

The DUT is compared with the reference model (model/line_unit.py) on every
cycle; each scenario (line_scenarios.py) also checks its results against
independent references: published CRC check values, bit-level line encoders
and decoders, and the ticker timing formula.
"""

from __future__ import annotations

import cocotb

import line_scenarios as s
from harness import CocotbHarness
from line_support import SKIP_REASON, line_unit_enabled

SKIP = not line_unit_enabled()
if SKIP:
    print(f"test_line_unit: {SKIP_REASON}")


@cocotb.test(skip=SKIP)
async def test_version_word(dut):
    """READ_SELECT 7: ISA version 3 plus the capability bits (0x000F5F03)."""
    await s.version(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_crc_check_values(dut):
    """CRC: nine catalogued CRCs over "123456789" through a driven line XFER
    (CCITT-FALSE 0x29B1, XMODEM, KERMIT, X-25, ARC 0xBB3D, USB 0xB4C8, UMTS,
    CAN-15 0x059E, CRC-5/USB)."""
    await s.crc_vectors(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_crc_classic_and_sampled(dut):
    """CRC fed by classic XFERs (driven and sampled bits) and by a sampling line XFER."""
    await s.crc_classic_and_sampled(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_ltim_ticker(dut):
    """LTIM: boundary times for ten (P, Q, D) sets against the ticker formula; P = 0 stops; P = 255 with Q faults."""
    await s.ticker_timing(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_lcfg_tx_line_coding(dut):
    """LCFG + drive-only line XFER: NRZ, NRZI, Manchester, stuffing (ones and either polarity,
    runs 2..8, trailing stuff bit), pair pin, MSB/LSB first, against independent encoders."""
    await s.tx_line_coding(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_lcfg_rx_line_decoding(dut):
    """Sample-only line XFER: NRZ/NRZI decoding, destuffing (with a trailing stuff bit),
    stuff errors, SE0 end with the remaining count, CRC of the sampled bits."""
    await s.rx_line_decoding(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_arbitration_monitor(dut):
    """Drive+sample line XFER on a wired-AND bus: loss only on a recessive bit with the monitor on;
    TXD recessive after the loss; RX holds the bus."""
    await s.arbitration(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_invalid_encodings(dut):
    """43 encodings: each invalid one faults with code 1 at its PC, each valid neighbour completes."""
    await s.invalid_encodings(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_lstat_bits(dut):
    """LSTAT: queue bits (TX data, RX space, full RX), line level and ticker bits."""
    await s.lstat_bits(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_start_stop_reset(dut):
    """START clears ticker, LCFG, flags, CRC and preset; STOP and reset mid-XFER release the pins."""
    await s.start_clears(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_classic_xfer_stops_ticker(dut):
    """A classic XFER stops the shared ticker; a line XFER after it faults with code 1."""
    await s.classic_stops_ticker(CocotbHarness(dut))


@cocotb.test(skip=SKIP)
async def test_completion_points(dut):
    """Drive-only XFERs complete at the last boundary tick, sampling ones at the last mid tick."""
    await s.completion_points(CocotbHarness(dut))
