# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The flagship concurrent scenario (firmware/flagship-scenario.json).

Engine 0 transmits UART on pin 0, engine 1 receives UART on pin 1, engine 2
is an SPI mode-0 controller on pins 2-5 and engine 3 an I2C controller on the
open-drain pins 6/7; a host-configured route moves every received UART byte
from engine 1's RX FIFO into engine 2's TX FIFO without host involvement.
"""

from __future__ import annotations

import cocotb

import scenarios
from harness import CocotbHarness


@cocotb.test()
async def test_flagship_concurrent(dut):
    """All four engines concurrently plus the autonomous UART-RX -> SPI-TX mover."""
    h = CocotbHarness(dut)
    result = await scenarios.flagship(h)
    dut._log.info("flagship scenario complete: %d cycles after START, %d cycles total",
                  result["cycles_to_complete"], h.cycle)
