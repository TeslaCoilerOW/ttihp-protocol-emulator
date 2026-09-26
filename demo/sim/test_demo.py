# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The demonstration scenarios (demo/pe_demo.py) on the simulated FPGA top.

DEMO_TB=pins (tb_fpga_pins.v, Cmod A7 host-clock build). The host library
(host/pe_host, the same code a Pico runs) drives every clock edge through
CocotbPort. By default the clock period is irregular (300-2000 ns low,
200-800 ns high, seeded), like a microcontroller toggling a GPIO in software.
The two jumper wires of docs/demo.md are modelled as a pad environment. With
DEMO_LOCKSTEP=1 the reference model runs in lockstep and every output is
compared on every cycle.

DEMO_TB=bridge (tb_fpga_bridge.v, Cmod A7 osc12 build: 12 MHz oscillator
clock, UART bridge at 1 Mbaud). fpga/host/pe_host.py drives the bridge's
USB-UART pins through a simulator transport, as test_fpga_bridge.py does; the
tb's two switchable jumpers are closed.

The logic-analyser view is the VCD written by demo_dump.v (DEMO_VCD);
demo/pe_capture.py analyses it exactly as it analyses a hardware capture.
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

try:  # cocotb 2.0
    from cocotb.task import bridge, resume
except ImportError:  # pragma: no cover
    from cocotb._bridge import bridge, resume

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
REPO = DEMO.parent
sys.path.insert(0, str(DEMO))
sys.path.insert(0, str(REPO / "host"))

import pe_demo  # noqa: E402

FIRMWARE_DIRS = [str(DEMO / "firmware"), str(REPO / "firmware")]
MODE = os.environ.get("DEMO_MODE", "idle")
CYCLES = int(os.environ.get("DEMO_CYCLES", "20000"))
SEED = int(os.environ.get("DEMO_SEED", "1"))
OUT = Path(os.environ.get("DEMO_OUT", HERE / "sim_build" / "out"))
LOCKSTEP = os.environ.get("DEMO_LOCKSTEP", "1") == "1"
CLOCK = os.environ.get("DEMO_CLOCK", "jitter")
TB = os.environ.get("DEMO_TB", "pins")
TAG = os.environ.get("DEMO_TAG", f"{TB}-{MODE}-{SEED}")
UART_BIT_NS = 1000         # bridge: 1 Mbaud
OSC_PS = 83333             # bridge: the Cmod A7's 12 MHz oscillator (83.333 ns)


def write_result(name: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(data, indent=1, default=str) + "\n")


async def pico_clock(dut, seed: int) -> None:
    """Host-driven clock with software-like irregular timing."""
    rng = random.Random(seed ^ 0x5EED)
    dut.clk.value = 0
    while True:
        if CLOCK == "fixed":
            low, high = 500, 500
        else:
            low, high = rng.randint(300, 2000), rng.randint(200, 800)
        await Timer(low, "ns")
        dut.clk.value = 1
        await Timer(high, "ns")
        dut.clk.value = 0


def lib_chip(dut, env, lockstep):
    from pe_host import ProtocolEmulator
    from pe_host.ports.cocotb_port import CocotbPort
    port = CocotbPort(dut, env=env, lockstep=lockstep)
    return pe_demo.LibChip(ProtocolEmulator(port, timeout=200000)), port


# ------------------------------------------------------------------ bridge tb
class SimUart:
    """PC side of the USB-UART at 1 Mbaud (as fpga/sim/test_fpga_bridge.py)."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.rx: list[int] = []
        dut.uart_rx.value = 1
        cocotb.start_soon(self._monitor())

    async def _monitor(self) -> None:
        tx = self.dut.uart_tx
        while True:
            await FallingEdge(tx)
            await Timer(UART_BIT_NS // 2, "ns")
            if str(tx.value) != "0":
                continue
            byte = 0
            for i in range(8):
                await Timer(UART_BIT_NS, "ns")
                byte |= int(str(tx.value) == "1") << i
            await Timer(UART_BIT_NS, "ns")
            self.rx.append(byte)

    async def send(self, data: bytes) -> None:
        for byte in data:
            for bit in [0] + [byte >> i & 1 for i in range(8)] + [1]:
                self.dut.uart_rx.value = bit
                await Timer(UART_BIT_NS, "ns")

    async def recv(self, count: int, timeout_ns: int) -> bytes:
        waited = 0
        while len(self.rx) < count and waited < timeout_ns:
            await Timer(UART_BIT_NS, "ns")
            waited += UART_BIT_NS
        out, self.rx[:] = bytes(self.rx[:count]), self.rx[count:]
        return out


class SimTransport:
    def __init__(self, uart: SimUart, timeout_ns: int = 30_000_000) -> None:
        self._send = resume(uart.send)
        self._recv = resume(uart.recv)
        self.timeout_ns = timeout_ns

    def write(self, data: bytes) -> None:
        self._send(data)

    def read(self, count: int, timeout: float) -> bytes:
        return self._recv(count, self.timeout_ns)


async def _sleep_ns(ns: int) -> None:
    await Timer(max(1, int(ns)), "ns")


async def _time_ns() -> float:
    from cocotb.utils import get_sim_time
    return get_sim_time("ns")


sim_sleep_ns = resume(_sleep_ns)
sim_time_ns = resume(_time_ns)


# ---------------------------------------------------------------- isolation
@cocotb.test()
async def test_isolation(dut):
    """One phase of the timing-isolation measurement (DEMO_MODE, DEMO_CYCLES)."""
    if TB == "bridge":
        cocotb.start_soon(Clock(dut.clk, OSC_PS, unit="ps", period_high=OSC_PS // 2).start())
        dut.btn0.value = 0
        dut.host_ext.value = 0
        dut.jumpers.value = 0b11
        uart = SimUart(dut)
        await Timer(2 * UART_BIT_NS, "ns")
        import pc_demo
        bridge_mod = pc_demo.load_bridge_module()
        chip = bridge_mod.Chip(bridge_mod.Bridge(SimTransport(uart), timeout=1e9))
        adapter = pc_demo.BridgeChip(chip, bridge_mod.BridgeError, 12e6,
                                     sleep=lambda s: sim_sleep_ns(s * 1e9))
        images = pc_demo.bridge_images(bridge_mod, pe_demo.ISOLATION_IMAGES)
        lockstep = False
    else:
        cocotb.start_soon(pico_clock(dut, SEED))
        from pe_host.peers import Environment
        adapter, port = lib_chip(dut, Environment([pe_demo.Jumpers()]), LOCKSTEP)
        images = pe_demo.lib_images(FIRMWARE_DIRS, pe_demo.ISOLATION_IMAGES)
        lockstep = LOCKSTEP
    log: list[str] = []
    marks: list[float] = []
    # The analyser window: the measured phase, from the probe's START to the
    # end of the idle wait or of the host traffic (pe_demo's marker).
    result = await bridge(lambda: pe_demo.isolation(adapter, images, MODE, CYCLES, SEED, log=log.append,
                                                   marker=lambda level: marks.append(sim_time_ns())))()
    result["phase_ns"] = marks
    for line in log:
        dut._log.info("%s", line)
    result.update({"tb": TB, "lockstep": lockstep, "clock": CLOCK if TB == "pins" else "osc12",
                   "core": os.environ.get("CORE_TAG", "base"), "sim_time_ns": _now_ns()})
    write_result(TAG, result)
    dut._log.info("result: %s", {k: v for k, v in result.items() if not k.startswith("engines_")})
    if os.environ.get("CORE_TAG", "base") == "base":
        assert result["probe_running"], "probe engine stopped"


def _now_ns() -> float:
    from cocotb.utils import get_sim_time
    return get_sim_time("ns")


# ------------------------------------------------------- four protocols (sim)
class SpiFlash:
    """Pad-level SPI flash (mode 0): answers JEDEC ID (0x9F) with ``ident``;
    MISO released (pull-up) otherwise. MSB first; samples MOSI on SCK rising,
    shifts MISO after SCK falling; state resets on CS high."""

    def __init__(self, ident=(0xEF, 0x40, 0x18), sck=2, mosi=3, miso=4, cs=5):
        self.ident = ident
        self.sck, self.mosi, self.miso, self.cs = sck, mosi, miso, cs
        self.mask = 1 << miso
        self.commands: list[int] = []
        self._reset()
        self._prev_sck = 0

    def _reset(self):
        self.bit = 0
        self.byte = 0
        self.nbytes = 0
        self.out = None
        self.out_bits = []

    def step(self, cycle, pads):
        sck = pads >> self.sck & 1
        if pads >> self.cs & 1:
            self._reset()
            self._prev_sck = sck
            return 0, 0
        if sck and not self._prev_sck:          # rising: sample MOSI
            self.byte = (self.byte << 1 | (pads >> self.mosi & 1)) & 0xFF
            self.bit += 1
            if self.bit == 8:
                if self.nbytes == 0:
                    self.commands.append(self.byte)
                    if self.byte == 0x9F:
                        self.out_bits = [b >> (7 - i) & 1 for b in self.ident for i in range(8)]
                self.nbytes += 1
                self.bit = 0
        elif not sck and self._prev_sck:        # falling: next MISO bit
            if self.nbytes >= 1 and self.out_bits:
                self.out = self.out_bits.pop(0)
            else:
                self.out = None
        self._prev_sck = sck
        if self.out is None:
            return 0, 0
        return self.mask, self.out << self.miso


@cocotb.test()
async def test_four(dut):
    """Four protocols at once with pad-level peers (pins tb): UART TX to a
    receiver, UART RX from a sender with the echo route, SPI JEDEC ID from a
    flash model, I2C read from a TMP102-like target."""
    if TB != "pins":
        return
    from pe_host.peers import Environment, I2CTarget, UartMonitor, UartSource
    cocotb.start_soon(pico_clock(dut, SEED))
    names = ("uart-tx-115200", "uart-rx-poll-115200", "spi-xfer32", "i2c-read-100k")
    images = pe_demo.lib_images(FIRMWARE_DIRS, names)
    bit = 434
    sent = [0x68, 0x69, 0x0D, 0x0A]
    source = UartSource(1, sent, bit_cycles=bit, gap_cycles=bit, start=4000)
    monitor = UartMonitor(0, bit_cycles=bit)
    flash = SpiFlash()
    i2c = I2CTarget(address=0x48, read_bytes=(0x19,))
    env = Environment([source, monitor, flash, i2c], pullups=0xFF)
    chip, port = lib_chip(dut, env, LOCKSTEP)

    def host():
        pe_demo.four_setup(chip, images, log=dut._log.info)
        source.start = chip.now() + 200
        pe_demo.four_round(chip, b"OK", pe_demo.TMP_ADDRESS)
        spi, rd = pe_demo.four_collect(chip, 1, 1, 150000, log=dut._log.info)
        chip.wait(12 * bit * 10)
        return spi, rd, pe_demo.bridge_status(chip)

    spi, rd, statuses = await bridge(host)()
    dut._log.info("SPI %s I2C %s UART out %s flash commands %s statuses %s",
                  [hex(w) for w in spi], rd, monitor.words,
                  flash.commands, statuses)
    got_uart = list(monitor.words)
    result = {"spi_words": spi, "i2c_bytes": rd, "uart_tx_bytes": got_uart, "flash_commands": flash.commands,
              "i2c_received": i2c.received, "statuses": statuses, "lockstep": LOCKSTEP}
    write_result(TAG, result)
    assert spi and pe_demo.jedec_id(spi[0]) == (0xEF, 0x40, 0x18), spi
    assert rd and rd[0] & 0xFF == 0x19, rd
    assert got_uart[:2] == [ord("O"), ord("K")], got_uart
    assert got_uart[2:6] == sent, got_uart              # echo of the UART RX bytes
    assert all(s["running"] and not s["fault"] for s in statuses), statuses


@cocotb.test()
async def test_bridge_chain(dut):
    """Host-free chain (pins tb): ticker -> I2C read -> hex formatter -> UART.
    After loading, routing and START the host only supplies clock edges."""
    if TB != "pins":
        return
    from pe_host.peers import Environment, I2CTarget, UartMonitor
    cocotb.start_soon(pico_clock(dut, SEED))
    names = ("uart-tx-115200", "read-ticker-sim", "hex-formatter", "i2c-read-100k")
    images = pe_demo.lib_images(FIRMWARE_DIRS, names)
    readings = (0x19, 0x1A, 0xF6)
    i2c = I2CTarget(address=0x48, read_bytes=readings)
    monitor = UartMonitor(0, bit_cycles=434)
    env = Environment([monitor, i2c], pullups=0xFF)
    chip, port = lib_chip(dut, env, LOCKSTEP)

    def host():
        pe_demo.bridge_setup(chip, images, log=dut._log.info)
        chip.wait(int(os.environ.get("DEMO_CYCLES", "130000")))
        return pe_demo.bridge_status(chip)

    statuses = await bridge(host)()
    text = bytes(monitor.words)
    parsed = pe_demo.parse_hex_lines(text)
    dut._log.info("UART text %r -> %s; statuses %s", text, parsed, statuses)
    write_result(TAG, {"uart_text": text.decode("latin-1"), "readings": parsed,
                                          "i2c_received": i2c.received, "statuses": statuses,
                                          "lockstep": LOCKSTEP})
    assert len(parsed) >= 3, text
    assert parsed[:3] == list(readings), parsed
    assert all(r == (0x48 << 1 | 1) for r in i2c.received), i2c.received
