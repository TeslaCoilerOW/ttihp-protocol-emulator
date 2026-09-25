# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""pe_host on the RTL: the bring-up self-test and the flagship scenario, driven
by the unchanged host library through CocotbPort (lockstep with the model)."""

import os

import cocotb
from cocotb.clock import Clock

from pe_host import ProtocolEmulator, selftest
from pe_host.flagship import run_flagship
from pe_host.ports.cocotb_port import CocotbPort, run_in_bridge

SCENARIO = os.environ.get("PE_HOST_SCENARIO") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "firmware",
    "flagship-scenario.json")


async def setup(dut):
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    return CocotbPort(dut)


@cocotb.test()
async def test_selftest_on_rtl(dut):
    """Every host command through the library, lockstep with the model."""
    port = await setup(dut)
    result = await run_in_bridge(lambda: selftest.run(ProtocolEmulator(port)))
    dut._log.info("%s (%d cycles, lockstep mismatches %d)", result.summary(), port.count,
                  port.mismatches)
    assert result.passed, result.summary()
    assert port.mismatches == 0


@cocotb.test()
async def test_flagship_on_rtl(dut):
    """firmware/flagship-scenario.json with the pe_host software peers."""
    port = await setup(dut)
    result = await run_in_bridge(lambda: run_flagship(ProtocolEmulator(port), SCENARIO))
    dut._log.info("%s", result.summary())
    assert result.passed, result.summary()
    assert port.mismatches == 0 and port.env_violations == 0
