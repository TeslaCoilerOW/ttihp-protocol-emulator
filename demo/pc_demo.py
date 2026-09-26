#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""PC side of the hardware demonstration (docs/demo.md), UART-bridge builds.

Drives the FPGA prototype through fpga/host/pe_host.py (the bridge tool; it
is loaded from its file under the module name ``pe_bridge`` because host/
also provides a package called ``pe_host``) and runs the scenarios of
demo/pe_demo.py.

    python3 demo/pc_demo.py --port /dev/ttyUSB1 isolation --mode idle   --seconds 5 --json idle.json
    python3 demo/pc_demo.py --port /dev/ttyUSB1 isolation --mode loaded --seconds 5 --json loaded.json
    python3 demo/pc_demo.py --port /dev/ttyUSB1 four   --uart /dev/ttyUSB2 --rounds 20
    python3 demo/pc_demo.py --port /dev/ttyUSB1 bridge --uart /dev/ttyUSB2 --seconds 10

--port is the FPGA board's bridge serial port (Cmod A7 and Urbana: the second
of the two ports). --uart is a separate USB-UART adapter wired to the chip's
UART pins (four, bridge). Requires Python 3.8+ and pyserial.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import pe_demo  # noqa: E402

FIRMWARE_DIRS = (HERE / "firmware", REPO / "firmware")


def load_bridge_module(path: Path = REPO / "fpga" / "host" / "pe_host.py"):
    """fpga/host/pe_host.py under the name pe_bridge (no clash with host/pe_host)."""
    if "pe_bridge" in sys.modules:
        return sys.modules["pe_bridge"]
    spec = importlib.util.spec_from_file_location("pe_bridge", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pe_bridge"] = module
    spec.loader.exec_module(module)
    return module


def bridge_images(bridge_mod, names, directories=FIRMWARE_DIRS) -> dict:
    """Images with the bridge tool's identity checks (source and bytecode SHA-256)."""
    out = {}
    for name in names:
        for directory in directories:
            if (Path(directory) / f"{name}.image.json").exists():
                img = bridge_mod.load_image(name, firmware=Path(directory))
                out[name] = pe_demo.ImageRef(name, img["engine"], img["words"], img["owned_pins"],
                                             img["open_drain"])
                break
        else:
            raise pe_demo.DemoError(f"image {name} not found in {[str(d) for d in directories]}")
    return out


class BridgeChip:
    """pe_demo chip interface over the UART bridge. The core clock free-runs,
    so ``now`` reads the bridge's cycle counter and ``wait`` sleeps."""

    host_clocked = False

    def __init__(self, chip, bridge_error, clk_hz: float, sleep=time.sleep, try_limit: int = 64):
        self.chip = chip
        self.b = chip.b
        self.error = bridge_error
        self.clk_hz = clk_hz
        self.sleep = sleep
        self.try_limit = try_limit
        self._limit = None
        self._last = None
        self._high = 0

    def _set_limit(self, cycles: int) -> None:
        if self._limit != cycles:
            self.b.set_limit(cycles)
            self._limit = cycles

    def reset(self):
        self._set_limit(0)
        self.chip.reset()

    def isa(self):
        return self.chip.status(pe_demo.RS_VERSION)

    def load(self, engine, image):
        self.chip.load(engine, image.words, ownership=image.owned_pins, open_drain=image.open_drain)

    def command(self, op, payload=0):
        self.chip.command(op, payload)
        return None                     # acceptance is not reported by the bridge

    def select(self, engine):
        self.chip.command(pe_demo.SELECT, engine)

    def read_select(self, index, engine):
        self.select(engine)
        return self.chip.status(index)

    def levels(self, engine):
        word = self.read_select(pe_demo.RS_LEVELS, engine)
        return word & 0xFFFF, word >> 16

    def status(self, engine):
        return self.read_select(pe_demo.RS_STATUS, engine)

    def tx_write(self, engine, words):
        self._set_limit(0)
        self.chip.push_tx(engine, list(words))

    def tx_try(self, engine, word, max_wait):
        self._set_limit(max(1, max_wait) + self.try_limit)
        self.select(engine)
        try:
            self.b.write(pe_demo.W_TX, word)
            return True
        except self.error:
            return False

    def rx_read(self, engine, count):
        self._set_limit(0)
        return self.chip.pop_rx(engine, count) if count else []

    def rx_try(self, engine, max_wait):
        self._set_limit(max(1, max_wait) + self.try_limit)
        self.select(engine)
        try:
            return self.b.read(pe_demo.W_RX)
        except self.error:
            return None

    def now(self):
        value = self.b.cycles()
        if self._last is not None and value < self._last:
            self._high += 1 << 32
        self._last = value
        return self._high + value

    def wait(self, cycles):
        self.sleep(cycles / self.clk_hz)


def connect(port: str, baud: int = 1_000_000):
    bridge_mod = load_bridge_module()
    bridge = bridge_mod.Bridge(bridge_mod.SerialTransport(port, baud))
    version = bridge.version()
    chip = bridge_mod.Chip(bridge)
    return bridge_mod, chip, version


def cmd_isolation(a, bridge_mod, chip, version) -> int:
    images = bridge_images(bridge_mod, pe_demo.ISOLATION_IMAGES)
    adapter = BridgeChip(chip, bridge_mod.BridgeError, version.clk_hz)
    cycles = int(a.seconds * version.clk_hz)
    t = time.monotonic()
    result = pe_demo.isolation(adapter, images, a.mode, cycles, seed=a.seed, log=print)
    result["wall_seconds"] = time.monotonic() - t
    result["board"] = version.describe()
    print(json.dumps({k: v for k, v in result.items() if not k.startswith("engines_")}, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1) + "\n")
    return 0 if result["probe_running"] else 1


def _open_uart(path: str, baud: int):
    import serial  # pyserial
    port = serial.Serial(path, baud, timeout=0.05)
    port.reset_input_buffer()
    return port


def _read_for(port, seconds: float) -> bytes:
    out = bytearray()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        out += port.read(4096)
    return bytes(out)


def cmd_four(a, bridge_mod, chip, version) -> int:
    images = bridge_images(bridge_mod, pe_demo.FOUR_IMAGES)
    adapter = BridgeChip(chip, bridge_mod.BridgeError, version.clk_hz)
    uart = _open_uart(a.uart, a.uart_baud)
    pe_demo.four_setup(adapter, images, i2c_address=a.i2c_address, log=print)
    greeting = b"engine 0: UART TX\r\n"
    adapter.tx_write(0, list(greeting))
    got = _read_for(uart, 0.2)
    print(f"UART TX -> adapter: {got!r}")
    ok = got == greeting
    for rnd in range(a.rounds):
        text = b"echo %d\r\n" % rnd
        uart.write(text)                        # engine 1 receives, the mover echoes through engine 0
        pe_demo.four_round(adapter, b"", a.i2c_address)
        spi, i2c = pe_demo.four_collect(adapter, 1, 1, int(0.5 * version.clk_hz), log=print)
        echoed = _read_for(uart, 0.05)
        man, typ, cap = pe_demo.jedec_id(spi[0]) if spi else (0, 0, 0)
        temp = pe_demo.tmp_celsius(i2c[0] & 0xFF) if i2c else None
        print(f"round {rnd}: SPI JEDEC {man:02x} {typ:02x} {cap:02x}" if spi else f"round {rnd}: no SPI reply",
              f"| I2C 0x{a.i2c_address:02x}: {temp} C" if i2c else "| no I2C reply",
              f"| UART echo {echoed!r}")
        ok &= bool(spi) and bool(i2c) and (spi[0] & 0xFFFFFF) not in (0, 0xFFFFFF) and echoed == text
    statuses = pe_demo.bridge_status(adapter)
    for s in statuses:
        print(s)
    ok &= all(s["running"] and not s["fault"] for s in statuses)
    print("FOUR-PROTOCOL", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_bridge(a, bridge_mod, chip, version) -> int:
    names = ("uart-tx-115200", "read-ticker", "hex-formatter", "i2c-read-100k")
    images = bridge_images(bridge_mod, names)
    adapter = BridgeChip(chip, bridge_mod.BridgeError, version.clk_hz)
    uart = _open_uart(a.uart, a.uart_baud)
    pe_demo.bridge_setup(adapter, images, log=print)
    print(f"host idle; reading the UART adapter for {a.seconds} s")
    data = _read_for(uart, a.seconds)
    readings = pe_demo.parse_hex_lines(data)
    print(f"{len(data)} bytes, {len(readings)} readings: "
          + " ".join(f"{pe_demo.tmp_celsius(r)}C" for r in readings[:20]))
    for s in pe_demo.bridge_status(adapter):
        print(s)
    return 0 if readings else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="FPGA bridge serial port")
    ap.add_argument("--bridge-baud", type=int, default=1_000_000, help="bridge baud rate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("isolation", help="timing-isolation measurement phase")
    p.add_argument("--mode", choices=("idle", "loaded"), required=True)
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--json")
    p = sub.add_parser("four", help="four protocols against real parts")
    p.add_argument("--uart", required=True, help="USB-UART adapter port wired to pins 0/1")
    p.add_argument("--uart-baud", type=int, default=115200)
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--i2c-address", type=lambda s: int(s, 0), default=pe_demo.TMP_ADDRESS)
    p = sub.add_parser("bridge", help="host-free I2C sensor -> UART chain")
    p.add_argument("--uart", required=True)
    p.add_argument("--uart-baud", type=int, default=115200)
    p.add_argument("--seconds", type=float, default=10.0)
    a = ap.parse_args(argv)
    bridge_mod, chip, version = connect(a.port, a.bridge_baud)
    print("bridge:", version.describe())
    handler = {"isolation": cmd_isolation, "four": cmd_four, "bridge": cmd_bridge}[a.cmd]
    try:
        return handler(a, bridge_mod, chip, version)
    except (bridge_mod.BridgeError, pe_demo.DemoError) as err:
        print(f"FAIL: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
