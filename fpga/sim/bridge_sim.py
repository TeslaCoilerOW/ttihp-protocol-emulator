# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""PC side of the board's USB-UART for cocotb tests of the bridge builds.

SimUart is an 8N1 sender and background receiver on tb_bridge.uart_rx /
uart_tx; SimTransport is a pe_host.Transport whose blocking calls run in the
simulator (cocotb bridge/resume), so fpga/host/pe_host.py and pe_scope.py run
unchanged against the simulated board (as in test_fpga_bridge.py).
"""

from __future__ import annotations

import cocotb
from cocotb.triggers import FallingEdge, Timer
from cocotb.utils import get_sim_time

try:  # cocotb 2.0
    from cocotb.task import bridge, resume
except ImportError:  # pragma: no cover
    from cocotb._bridge import bridge, resume

BIT_NS = 1000      # 1 Mbaud


class SimUart:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.rx: list[int] = []
        self.rx_done_ns: list[float] = []
        dut.uart_rx.value = 1
        cocotb.start_soon(self._monitor())

    async def _monitor(self) -> None:
        tx = self.dut.uart_tx
        while True:
            await FallingEdge(tx)
            await Timer(BIT_NS // 2, "ns")
            if str(tx.value) != "0":
                continue
            byte = 0
            for i in range(8):
                await Timer(BIT_NS, "ns")
                byte |= int(str(tx.value) == "1") << i
                assert str(tx.value) in "01", f"uart_tx is {tx.value}"
            await Timer(BIT_NS, "ns")
            assert str(tx.value) == "1", "UART stop bit missing"
            self.rx.append(byte)
            self.rx_done_ns.append(get_sim_time("ns"))

    async def send(self, data: bytes) -> None:
        for byte in data:
            for bit in [0] + [byte >> i & 1 for i in range(8)] + [1]:
                self.dut.uart_rx.value = bit
                await Timer(BIT_NS, "ns")

    async def recv(self, count: int, timeout_ns: int) -> bytes:
        waited = 0
        while len(self.rx) < count and waited < timeout_ns:
            await Timer(BIT_NS, "ns")
            waited += BIT_NS
        out, self.rx[:] = bytes(self.rx[:count]), self.rx[count:]
        return out


class SimTransport:
    """timeout_ns bounds the simulated wait for one read (a 512-record 'U'
    reply takes 46 ms at 1 Mbaud)."""

    def __init__(self, uart: SimUart, timeout_ns: int = 100_000_000) -> None:
        self._send = resume(uart.send)
        self._recv = resume(uart.recv)
        self.timeout_ns = timeout_ns

    def write(self, data: bytes) -> None:
        self._send(data)

    def read(self, count: int, timeout: float) -> bytes:  # wall-clock timeout ignored
        return self._recv(count, self.timeout_ns)


async def _wait_ns(ns: float) -> None:
    await Timer(max(1, int(ns)), "ns")


async def _now_ns() -> float:
    return get_sim_time("ns")


wait_ns = resume(_wait_ns)
now_ns = resume(_now_ns)


def run(fn):
    """Run blocking host code against the simulator."""
    return bridge(fn)()
