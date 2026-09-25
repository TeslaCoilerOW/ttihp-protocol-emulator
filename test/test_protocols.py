# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""UART/SPI/I2C firmware images against wire monitors and behavioral peers.

The DUT is compared with the reference model on every cycle; protocol results
are also checked at the pads by independent monitors (see scenarios.py).
"""

from __future__ import annotations

import cocotb

import scenarios
from harness import CocotbHarness


@cocotb.test()
async def test_uart_tx(dut):
    """uart-tx: 13 host bytes (more than the 8-word FIFO) decoded from pin 0."""
    await scenarios.uart_tx(CocotbHarness(dut), list(b"Tiny Tapeout!"))


@cocotb.test()
async def test_uart_rx(dut):
    """uart-rx: frames driven on pin 1 read back from the RX FIFO; timeout and framing faults."""
    await scenarios.uart_rx(CocotbHarness(dut), [0x00, 0xFF, 0x55, 0xA5, 0x31, 0x7E])


@cocotb.test()
@cocotb.parametrize(mode=[0, 1, 2, 3])
async def test_spi_controller(dut, mode):
    """spi-controller-mode<N>: MOSI bytes at the target, MISO bytes in the RX FIFO."""
    await scenarios.spi_controller(CocotbHarness(dut), mode, [0xA6, 0x01, 0xFF], [0x5A, 0x80, 0x3C])


@cocotb.test()
async def test_i2c_write(dut):
    """i2c-write: open-drain controller write to a stretching target."""
    await scenarios.i2c_write(CocotbHarness(dut))


@cocotb.test()
async def test_i2c_read(dut):
    """i2c-read: controller read of one byte, NACK, STOP."""
    await scenarios.i2c_read(CocotbHarness(dut))


@cocotb.test()
async def test_i2c_nack(dut):
    """i2c-write against a NACKing target: explicit fault 65, bus released."""
    await scenarios.i2c_nack(CocotbHarness(dut))
