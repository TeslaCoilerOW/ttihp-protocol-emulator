# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Pico host driver (fpga/host/pico_host.py) on the simulated FPGA top.

Runs with tb_fpga_pins.v in the CLOCK=host build: the driver clocks the
board's host_clk pin itself, cycle by cycle, exactly as the MicroPython
code does on a Pico (set ui/rst/ena with the clock low, sample uo_out,
pulse the clock). The protocol Pmod has weak pull-ups and the two loopback
jumpers (uio0-uio1, uio3-uio4), modelled by the port below.

    make COCOTB_TEST_MODULES=test_fpga_pico            (FPGA_CLOCK=host, the default)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import cocotb
from cocotb.triggers import Timer

try:  # cocotb 2.0
    from cocotb.task import bridge, resume
except ImportError:  # pragma: no cover
    from cocotb._bridge import bridge, resume

import pico_host

FIRMWARE = Path(os.environ.get("PE_FIRMWARE_DIR", Path(__file__).resolve().parents[2] / "firmware"))


class SimPort:
    """pico_host port: one call = one host-driven clock cycle of the FPGA top."""

    def __init__(self, dut, jumpers: bool) -> None:
        self.dut = dut
        self.jumpers = jumpers
        self._step = resume(self._astep)

    def _pads(self) -> int:
        out = int(self.dut.uio_out.value)
        oe = int(self.dut.uio_oe.value)
        pads = (out & oe) | (0xFF & ~oe)
        if self.jumpers:
            for a, b in ((0, 1), (3, 4)):
                if oe >> a & 1:
                    level = out >> a & 1
                elif oe >> b & 1:
                    level = out >> b & 1
                else:
                    level = 1
                pads = pads & ~(1 << a | 1 << b) | level << a | level << b
        return pads

    async def _astep(self, ui: int, reset: bool, enabled: bool) -> int:
        d = self.dut
        d.ui_in.value = ui
        d.rst_n.value = 0 if reset else 1
        d.ena.value = 1 if enabled else 0
        d.uio_in.value = self._pads() if d.uio_oe.value.is_resolvable else 0xFF
        await Timer(5, "ns")
        uo = d.uo_out.value
        uo = uo.to_unsigned() if uo.is_resolvable else 0
        d.clk.value = 1
        await Timer(10, "ns")
        d.clk.value = 0
        await Timer(5, "ns")
        return uo

    def step(self, ui: int, reset: bool = False, enabled: bool = True) -> int:
        return self._step(ui, reset, enabled)


def images() -> dict:
    return {n: json.loads((FIRMWARE / f"{n}.image.json").read_text())
            for n in ("uart-tx", "uart-rx", "spi-controller-mode0")}


async def start(dut) -> None:
    dut.clk.value = 0
    dut.rst_n.value = 0
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0xFF
    await Timer(20, "ns")


@cocotb.test()
async def test_pico_host_loopback(dut):
    """Version, idle engines, then the jumper loopback, driven like a Pico."""
    await start(dut)
    host = pico_host.NibbleHost(SimPort(dut, jumpers=True))
    log: list[str] = []

    def run():
        host.reset()
        version = host.status(pico_host.RS_VERSION)
        idle = [host.engine_status(e) for e in range(4)]
        ok = pico_host.loopback(host, images(), log=log.append)
        return version, idle, ok

    version, idle, ok = await bridge(run)()
    for line in log:
        dut._log.info("pico: %s", line)
    dut._log.info("%d host-clocked cycles", host.cycles)
    assert version == 2
    assert all(not any(st.values()) for st in idle), idle
    assert ok and log[-1] == "LOOPBACK PASS"
