# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""fpga/host/bringup.py end to end against the simulated board.

The whole bring-up sequence runs unchanged against the UART-bridge FPGA top
(tb_fpga_bridge.v): link, selftest (no jumpers), the jumper prompt (answered
by closing the testbench's two jumpers), loopback, capture-unit checks and
the self-measured isolation experiment with its verdict. Time runs in the
simulator (sleep and clock functions are passed in), and the capture sizes
are reduced to keep the read-back over the 1 Mbaud UART short (256 records
per check capture, 512 for the "whole buffer" capture, 1,024 per isolation
capture, one marker):

    make FPGA_TB=bridge EXTRA_DEFINES=-DPE_CLOCK_OSC FPGA_SIM_CLOCK_NS=83.333 \\
         COCOTB_TEST_MODULES=test_fpga_bringup BRINGUP_OUT=/path/out      # Cmod A7 osc12
    make FPGA_TB=bridge FPGA_BOARD=urbana COCOTB_TEST_MODULES=test_fpga_bringup BRINGUP_OUT=/path/out

The transcript is written to BRINGUP_OUT/transcript.txt.
"""

from __future__ import annotations

import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.task import resume
from cocotb.triggers import Timer

import bringup
import pe_host
from bridge_sim import SimTransport, SimUart, now_ns, run, wait_ns

CLOCK_NS = float(os.environ.get("FPGA_SIM_CLOCK_NS", "20"))
BOARD = "urbana" if os.environ.get("FPGA_BOARD") == "urbana" else "cmod_a7"
OUT = Path(os.environ.get("BRINGUP_OUT", Path(__file__).resolve().parent / "sim_build" / "bringup"))


@cocotb.test()
async def test_bringup(dut):
    period_ps = round(CLOCK_NS * 1000)
    cocotb.start_soon(Clock(dut.clk, period_ps, unit="ps", period_high=period_ps // 2).start(start_high=False))
    dut.btn0.value = 0
    dut.jumpers.value = 0
    dut.host_ext.value = 0
    uart = SimUart(dut)
    await Timer(2000, "ns")
    bridge = pe_host.Bridge(SimTransport(uart, timeout_ns=300_000_000), timeout=1e9)

    async def _jumpers(value):
        dut.jumpers.value = value
        await Timer(100, "ns")

    set_jumpers = resume(_jumpers)
    lines: list[str] = []

    def log(text=""):
        lines.append(text)

    def prompt(text):
        log(">>> " + text)
        log(">>> (simulation: the testbench closes both jumpers)")
        set_jumpers(0b11)

    OUT.mkdir(parents=True, exist_ok=True)
    report = await run(lambda: bringup.run(
        bridge, BOARD, OUT, log=log, prompt=prompt, sleep=lambda s: wait_ns(s * 1e9),
        now=lambda: now_ns() / 1e9, measure_s=0.002, check_limit=int(os.environ.get("BRINGUP_CHECK_LIMIT", "256")),
        markers=1, full_limit=int(os.environ.get("BRINGUP_FULL_LIMIT", "512")),
        captures=int(os.environ.get("BRINGUP_CAPTURES", "1")),
        limit=int(os.environ.get("BRINGUP_LIMIT", "1024")), max_seconds=0.5))
    (OUT / "transcript.txt").write_text("\n".join(lines) + "\n")
    for line in lines:
        dut._log.info("%s", line)
    assert report["pass"], report.get("failed_step")
