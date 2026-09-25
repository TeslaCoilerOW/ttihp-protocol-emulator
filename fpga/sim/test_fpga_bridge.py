# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""UART-bridge FPGA top (tb_fpga_bridge.v) driven by the PC host tool.

The hardware bring-up procedure of docs/fpga.md runs here unchanged:
fpga/host/pe_host.py (info / selftest / loopback) talks to the simulated
board through its USB-UART pins at 1 Mbaud, via a simulator transport that
replaces pyserial. The host code runs in a thread (cocotb bridge/resume), so
it is the same blocking code that runs against a real serial port.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer
from cocotb.utils import get_sim_time

try:  # cocotb 2.0
    from cocotb.task import bridge, resume
except ImportError:  # pragma: no cover
    from cocotb._bridge import bridge, resume

import pe_host

CLOCK_NS = 20      # 50 MHz core clock (MMCM output; a wire under PE_FPGA_SIM)
BIT_NS = 1000      # 1 Mbaud


class SimUart:
    """PC side of the USB-UART: 8N1 sender and a background receiver."""

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
    """pe_host.Transport whose blocking calls run in the simulator."""

    def __init__(self, uart: SimUart, timeout_ns: int = 3_000_000) -> None:
        self._send = resume(uart.send)
        self._recv = resume(uart.recv)
        self.timeout_ns = timeout_ns

    def write(self, data: bytes) -> None:
        self._send(data)

    def read(self, count: int, timeout: float) -> bytes:  # wall-clock timeout ignored
        return self._recv(count, self.timeout_ns)


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, CLOCK_NS, unit="ns").start())
    dut.btn0.value = 0
    dut.jumpers.value = 0
    dut.host_ext.value = 0
    uart = SimUart(dut)
    await Timer(2 * BIT_NS, "ns")
    chip = pe_host.Chip(pe_host.Bridge(SimTransport(uart)))
    return uart, chip


async def _wait_ns(ns: int) -> None:
    await Timer(ns, "ns")


wait_ns = resume(_wait_ns)


def run(fn):
    """Run blocking host code (pe_host) against the simulator."""
    return bridge(fn)()


@cocotb.test()
async def test_info_and_clock(dut):
    """'V' identifies the bitstream; the bridge cycle counter runs at 50 MHz."""
    uart, chip = await setup(dut)
    b = chip.b

    def host():
        v = b.version()
        c0 = b.cycles()
        t0 = uart.rx_done_ns[-1]
        wait_ns(100_000)
        c1 = b.cycles()
        t1 = uart.rx_done_ns[-1]
        return v, (c1 - c0) & 0xFFFFFFFF, t1 - t0, b.sample(), chip.status(pe_host.RS_VERSION)

    v, dc, dt, s, isa = await run(host)
    dut._log.info("%s; %d cycles in %.1f us; pins %s; ISA %d", v.describe(), dc, dt / 1e3, s, isa)
    board = 2 if os.environ.get("FPGA_BOARD") == "urbana" else 1
    assert (v.protocol, v.board, v.clock, v.clk_hz, v.baud_div) == (1, board, 0, 50_000_000, 50), v
    assert dc == round(dt / CLOCK_NS), (dc, dt)
    assert isa == pe_host.ISA_VERSION
    assert s.uio_oe == 0 and s.flags & 0b00100 and s.flags & 0b01000 and not s.flags & 0b11, s


@cocotb.test()
async def test_selftest(dut):
    """`pe_host.py selftest` (nothing connected to the protocol Pmod) passes."""
    _, chip = await setup(dut)
    log: list[str] = []
    await run(lambda: pe_host.selftest(chip, log.append))
    for line in log:
        dut._log.info("selftest: %s", line)
    assert log[-1] == "SELFTEST PASS"


@cocotb.test()
async def test_loopback(dut):
    """`pe_host.py loopback` with the two jumpers: UART -> route -> SPI round trip."""
    _, chip = await setup(dut)
    dut.jumpers.value = 0b11
    log: list[str] = []
    await run(lambda: pe_host.loopback(chip, log.append))
    for line in log:
        dut._log.info("loopback: %s", line)
    assert log[-1] == "LOOPBACK PASS"
    dut.jumpers.value = 0


@cocotb.test()
async def test_loopback_detects_missing_jumpers(dut):
    """Without the jumpers the loopback test must fail (it checks the wiring)."""
    _, chip = await setup(dut)

    def host():
        try:
            pe_host.loopback(chip, lambda _: None)
        except pe_host.CheckFailure as err:
            return str(err)
        return None

    failure = await run(host)
    dut._log.info("expected failure: %s", failure)
    assert failure is not None


@cocotb.test()
async def test_bridge_errors_and_timeout(dut):
    """Error replies, the transfer timeout ('T') and recovery, host-select ('D')."""
    _, chip = await setup(dut)
    b = chip.b

    def host():
        results = {}
        chip.reset()
        for frame, key in ((bytes([ord("R"), 1]), "read_window1"), (b"W\x03\0\0\0\0", "write_window3"),
                           (b"Z", "unknown")):
            try:
                b.one(frame)
            except pe_host.BridgeError as err:
                results[key] = str(err)
        # Engine 0 is halted, so its 8-word TX FIFO fills and the ninth write stalls.
        b.set_limit(2000)
        chip.command(pe_host.SELECT, 0)
        b.write_many(2, range(8))
        try:
            b.write(2, 8)
        except pe_host.BridgeError as err:
            results["timeout"] = str(err)
        b.set_limit(0)
        results["levels"] = chip.status(pe_host.RS_LEVELS)
        chip.reset()
        results["levels_after_reset"] = chip.status(pe_host.RS_LEVELS)
        return results

    results = await run(host)
    dut._log.info("%s", results)
    assert "bad window" in results["read_window1"]
    assert "bad window" in results["write_window3"]
    assert "unknown command" in results["unknown"]
    assert "timeout after 0 nibbles" in results["timeout"]
    assert results["levels"] == 8 and results["levels_after_reset"] == 0

    if os.environ.get("FPGA_BRIDGE_ONLY") == "1":
        return                        # BRIDGE_ONLY build: no host select, nothing to refuse
    dut.host_ext.value = 1            # external pin host selected: bridge refuses
    await Timer(1000, "ns")

    def owned():
        try:
            b.write(0, 0)
        except pe_host.BridgeError as err:
            return str(err), b.version().board
        return None, None

    message, board = await run(owned)
    dut.host_ext.value = 0
    assert message and "external pin host" in message and board in (1, 2)
