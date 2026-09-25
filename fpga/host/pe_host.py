#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""PC host for the protocol-emulator FPGA prototype (UART bridge).

Talks to fpga/rtl/pe_uart_host_bridge.v over the board's USB serial port
(1 Mbaud 8N1) and implements the chip's host protocol (docs/isa.md, "Host
interface") on top of it: commands, status reads, image loading, TX/RX FIFO
access. Also runs the hardware bring-up tests of docs/fpga.md.

    python3 fpga/host/pe_host.py --port /dev/ttyUSB1 info
    python3 fpga/host/pe_host.py --port /dev/ttyUSB1 selftest
    python3 fpga/host/pe_host.py --port /dev/ttyUSB1 loopback
    python3 fpga/host/pe_host.py --port /dev/ttyUSB1 load uart-tx

Standard library only, plus pyserial for a real port (pip install pyserial).
The same code runs against the simulated FPGA top in fpga/sim
(test_fpga_bridge.py) through a simulator transport.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence

REPO = Path(__file__).resolve().parents[2]
FIRMWARE = Path(os.environ.get("PE_FIRMWARE_DIR", REPO / "firmware"))

# Host commands (docs/isa.md, window 0).
SELECT, BEGIN, COMMIT, OWN, START, STOP, ROUTE, CLEAR = range(8)
READ_SELECT, EVENT, FLUSH, TRIGGER = 8, 9, 10, 11
RS_STATUS, RS_TIMESTAMP, RS_LEVELS, RS_PC, RS_EVENT, RS_COUNT, RS_HELD_RX, RS_VERSION = range(8)
ISA_VERSION = 2

# Opcodes (docs/isa.md).
NOP, HALT, SET, DIR, WAIT, JMP = 0, 1, 2, 3, 4, 5

BOARDS = {1: "Cmod A7-35T", 2: "Urbana (xc7s50)"}
CLOCKS = {0: "pll50 (MMCM, board oscillator)", 1: "osc12 (12 MHz oscillator)", 2: "host clock",
          3: "pll40 (MMCM, board oscillator)"}


def instruction(op: int, a: int = 0, b: int = 0, c: int = 0) -> int:
    return (op << 24) | (a << 16) | (b << 8) | c


def immediate(op: int, value: int = 0) -> int:
    return (op << 24) | (value & 0xFFFFFF)


class BridgeError(RuntimeError):
    pass


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, count: int, timeout: float) -> bytes: ...


class SerialTransport:
    """pyserial transport. The FTDI latency timer bounds small-reply latency."""

    def __init__(self, port: str, baud: int = 1_000_000) -> None:
        import serial  # pyserial

        self.port = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.05)
        self.port.reset_input_buffer()

    def write(self, data: bytes) -> None:
        self.port.write(data)
        self.port.flush()

    def read(self, count: int, timeout: float) -> bytes:
        out = bytearray()
        deadline = time.monotonic() + timeout
        while len(out) < count and time.monotonic() < deadline:
            out += self.port.read(count - len(out))
        return bytes(out)


@dataclass(frozen=True)
class Version:
    protocol: int
    board: int
    clock: int
    clk_hz: int
    baud_div: int

    def describe(self) -> str:
        return (f"bridge protocol {self.protocol}, board {BOARDS.get(self.board, self.board)}, "
                f"clock {CLOCKS.get(self.clock, self.clock)}, {self.clk_hz / 1e6:g} MHz, "
                f"{self.clk_hz / self.baud_div / 1e6:g} Mbaud")


@dataclass(frozen=True)
class Sample:
    uo: int
    uio_pad: int
    uio_oe: int
    uio_out: int
    ui: int
    flags: int


class Bridge:
    """Byte protocol of pe_uart_host_bridge.v (see the header of that file)."""

    MAX_IN_FLIGHT = 48  # bytes; the bridge's receive FIFO holds 64

    def __init__(self, transport: Transport, timeout: float = 2.0) -> None:
        self.t = transport
        self.timeout = timeout

    # -- framing
    def _reply(self, cmd: int) -> bytes:
        head = self.t.read(1, self.timeout)
        if not head:
            raise BridgeError(f"no reply to {chr(cmd)!r} (wrong port, bitstream or baud rate?)")
        code = head[0]
        if cmd == ord("V") and code == ord("V"):
            return head + self._more(9)
        if cmd == ord("S") and code == ord("S"):
            return head + self._more(6)
        if cmd == ord("C") and code == ord("C"):
            return head + self._more(4)
        if cmd == ord("R") and code == ord("K"):
            return head + self._more(4)
        if code == ord("T"):
            return head + self._more(1)
        return head

    def _more(self, count: int) -> bytes:
        data = self.t.read(count, self.timeout)
        if len(data) != count:
            raise BridgeError(f"short reply ({len(data)} of {count} bytes)")
        return data

    @staticmethod
    def _check(cmd: int, reply: bytes) -> bytes:
        code = chr(reply[0])
        if code == "T":
            raise BridgeError(f"{chr(cmd)}: timeout after {reply[1]} nibbles (core not ready)")
        if code == "E":
            raise BridgeError(f"{chr(cmd)}: bad window")
        if code == "D":
            raise BridgeError(f"{chr(cmd)}: host port owned by the external pin host (host select)")
        if code == "?":
            raise BridgeError(f"{chr(cmd)}: unknown command")
        return reply

    def transact(self, frames: Sequence[bytes]) -> list[bytes]:
        """Send frames (pipelined, bounded by the receive FIFO) and return the replies."""
        replies: list[bytes] = []
        i = 0
        while i < len(frames):
            chunk, size = [], 0
            while i < len(frames) and (not chunk or size + len(frames[i]) <= self.MAX_IN_FLIGHT):
                chunk.append(frames[i])
                size += len(frames[i])
                i += 1
            self.t.write(b"".join(chunk))
            # Read every reply of the chunk before checking, so an error reply
            # never leaves later replies queued for the next transaction.
            raw = [self._reply(frame[0]) for frame in chunk]
            replies += [self._check(frame[0], reply) for frame, reply in zip(chunk, raw)]
        return replies

    def one(self, frame: bytes) -> bytes:
        return self.transact([frame])[0]

    # -- commands
    def version(self) -> Version:
        r = self.one(b"V")
        return Version(r[1], r[2], r[3], int.from_bytes(r[4:8], "little"), int.from_bytes(r[8:10], "little"))

    def sample(self) -> Sample:
        r = self.one(b"S")
        return Sample(*r[1:7])

    def cycles(self) -> int:
        return int.from_bytes(self.one(b"C")[1:5], "little")

    def set_limit(self, cycles: int) -> None:
        self.one(b"L" + cycles.to_bytes(4, "little"))

    def reset(self, cycles: int = 4) -> None:
        self.one(bytes([ord("X"), cycles]))

    def set_ena(self, on: bool) -> None:
        self.one(bytes([ord("N"), int(on)]))

    @staticmethod
    def write_frame(window: int, word: int) -> bytes:
        return bytes([ord("W"), window]) + (word & 0xFFFFFFFF).to_bytes(4, "little")

    def write(self, window: int, word: int) -> None:
        self.one(self.write_frame(window, word))

    def write_many(self, window: int, words: Iterable[int]) -> None:
        self.transact([self.write_frame(window, w) for w in words])

    def read(self, window: int, *, bounce: bool = False) -> int:
        r = self.one(bytes([ord("R"), window | (0x80 if bounce else 0)]))
        return int.from_bytes(r[1:5], "little")


def load_image(name: str, firmware: Path = FIRMWARE) -> dict:
    """firmware/<name>.image.json with its source/bytecode identity checks (as test/harness.py)."""
    image = json.loads((firmware / f"{name}.image.json").read_text())
    if image["schema_version"] != "protocol-emulator.firmware-image.v1":
        raise ValueError(f"unsupported firmware image {name}")
    source = (firmware / f"{name}.source.json").read_bytes()
    if hashlib.sha256(source).hexdigest() != image["source_sha256"]:
        raise ValueError(f"firmware {name}: source identity mismatch")
    payload = b"".join(w.to_bytes(4, "little") for w in image["words"])
    if hashlib.sha256(payload).hexdigest() != image["bytecode_sha256"]:
        raise ValueError(f"firmware {name}: bytecode identity mismatch")
    return image


class Chip:
    """The chip's host protocol over the bridge (same operations as test/harness.py)."""

    def __init__(self, bridge: Bridge) -> None:
        self.b = bridge

    def reset(self) -> None:
        self.b.reset(4)

    def command(self, opcode: int, payload: int = 0) -> None:
        if not 0 <= opcode <= 255 or not 0 <= payload < 1 << 24:
            raise ValueError("invalid host command")
        self.b.write(0, opcode << 24 | payload)

    def status(self, selection: int = RS_STATUS) -> int:
        """READ_SELECT, then read window 0 after a window bounce (fresh snapshot)."""
        self.command(READ_SELECT, selection)
        return self.b.read(0, bounce=True)

    def engine_status(self, engine: int) -> dict[str, int]:
        self.command(SELECT, engine)
        status = self.status(RS_STATUS)
        levels = self.status(RS_LEVELS)
        return {"running": status & 1, "committed": status >> 1 & 1, "stalled": status >> 2 & 1,
                "fault": status >> 8 & 0xFF if status & 8 else 0, "tx_level": levels & 0xFFFF,
                "rx_level": levels >> 16, "pc": self.status(RS_PC), "count": self.status(RS_COUNT)}

    def load(self, engine: int, words: Sequence[int], *, ownership: int = 0, open_drain: int = 0) -> None:
        self.command(SELECT, engine)
        self.command(BEGIN)
        self.command(OWN, ownership | open_drain << 8)
        self.b.write_many(1, words)
        self.command(COMMIT, len(words))

    def load_firmware(self, name: str, *, engine: int | None = None) -> dict:
        image = load_image(name)
        version = self.status(RS_VERSION)
        if version < image["isa_version"]:
            raise BridgeError(f"{name} needs ISA {image['isa_version']}, device reports {version}")
        self.load(image["engine"] if engine is None else engine, image["words"],
                  ownership=image["owned_pins"], open_drain=image["open_drain"])
        return image

    def push_tx(self, engine: int, words: Iterable[int]) -> None:
        self.command(SELECT, engine)
        self.b.write_many(2, words)

    def pop_rx(self, engine: int, count: int) -> list[int]:
        self.command(SELECT, engine)
        return [self.b.read(3) for _ in range(count)]

    def route(self, source: int, destination: int, count: int) -> None:
        self.command(ROUTE, source | destination << 2 | 16 | count << 5)


# ---------------------------------------------------------------- bring-up tests
class CheckFailure(AssertionError):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def info(bridge: Bridge, log: Callable[[str], None] = print, *, measure_s: float = 1.0) -> dict:
    """Identify the bitstream and measure the core clock against the PC clock."""
    v = bridge.version()
    log(f"bridge: {v.describe()}")
    s = bridge.sample()
    log(f"pins: uo={s.uo:02x} uio_pad={s.uio_pad:02x} uio_oe={s.uio_oe:02x} flags={s.flags:02x}")
    result = {"version": v.__dict__, "flags": s.flags}
    if measure_s > 0:
        c0, t0 = bridge.cycles(), time.monotonic()
        time.sleep(measure_s)
        c1, t1 = bridge.cycles(), time.monotonic()
        mhz = ((c1 - c0) & 0xFFFFFFFF) / (t1 - t0) / 1e6
        result["measured_mhz"] = mhz
        log(f"core clock: {mhz:.3f} MHz measured over {t1 - t0:.2f} s of PC time "
            f"(nominal {v.clk_hz / 1e6:g} MHz; includes USB latency jitter)")
    return result


def selftest(chip: Chip, log: Callable[[str], None] = print) -> None:
    """No wiring needed (leave the protocol Pmod unconnected)."""
    b = chip.b
    chip.reset()
    version = chip.status(RS_VERSION)
    log(f"ISA version {version}")
    expect(version == ISA_VERSION, f"ISA version {version}, expected {ISA_VERSION}")
    for engine in range(4):
        st = chip.engine_status(engine)
        expect(st["running"] == 0 and st["committed"] == 0 and st["fault"] == 0
               and st["tx_level"] == 0 and st["rx_level"] == 0,
               f"engine {engine} not idle after reset: {st}")
    log("4 engines idle after reset")
    # The core's timestamp and the bridge's cycle counter both count core
    # clocks. Each timestamp snapshot lies between two counter samples, so
    # the timestamp difference is bounded whatever the USB latency is.
    samples = []
    for _ in range(2):
        before = b.cycles()
        stamp = chip.status(RS_TIMESTAMP)
        samples.append((before, stamp, b.cycles()))
    (b0, t0, a0), (b1, t1, a1) = samples
    dt = (t1 - t0) & 0xFFFFFFFF
    lo, hi = (b1 - a0) & 0xFFFFFFFF, (a1 - b0) & 0xFFFFFFFF
    expect(lo <= dt <= hi, f"timestamp advanced {dt} cycles, bridge counter bounds [{lo}, {hi}]")
    log(f"timestamp advances with the core clock ({dt} cycles, bounds [{lo}, {hi}])")
    # Pads: engine 0 owns all eight pins and drives two patterns, then releases them.
    s = b.sample()
    expect(s.uio_oe == 0, f"pins driven after reset: uio_oe={s.uio_oe:02x}")
    for pattern in (0xA5, 0x5A):
        chip.load(0, [immediate(DIR, 0xFF), immediate(SET, pattern), immediate(JMP, 2)], ownership=0xFF)
        chip.command(START, 0b0001)
        s = b.sample()
        expect(s.uio_oe == 0xFF and s.uio_out == pattern and s.uio_pad == pattern,
               f"drive {pattern:02x}: oe={s.uio_oe:02x} out={s.uio_out:02x} pads={s.uio_pad:02x}")
        chip.command(STOP, 0b0001)
        s = b.sample()
        expect(s.uio_oe == 0, f"pins not released after STOP: uio_oe={s.uio_oe:02x}")
        log(f"pads drive {pattern:02x} and read back {s.uio_pad:02x} when released")
    expect(s.uio_pad == 0xFF, f"released pads read {s.uio_pad:02x}; the weak pull-ups should give ff "
           "(is something connected to the protocol Pmod?)")
    # Open-drain: pin 7 open-drain, SET 0 pulls low, SET 1 releases.
    chip.load(0, [immediate(DIR, 0x80), immediate(SET, 0x00), immediate(JMP, 2)],
              ownership=0x80, open_drain=0x80)
    chip.command(START, 0b0001)
    s = b.sample()
    expect(s.uio_oe == 0x80 and s.uio_pad & 0x80 == 0, f"open-drain low: oe={s.uio_oe:02x} pads={s.uio_pad:02x}")
    chip.command(STOP, 0b0001)
    chip.load(0, [immediate(DIR, 0x80), immediate(SET, 0x80), immediate(JMP, 2)],
              ownership=0x80, open_drain=0x80)
    chip.command(START, 0b0001)
    s = b.sample()
    expect(s.uio_oe == 0 and s.uio_pad & 0x80, f"open-drain release: oe={s.uio_oe:02x} pads={s.uio_pad:02x}")
    chip.command(STOP, 0b0001)
    log("open-drain pin pulls low and releases")
    chip.reset()
    log("SELFTEST PASS")


def loopback(chip: Chip, log: Callable[[str], None] = print,
             message: bytes = b"FPGA OK!") -> None:
    """Flagship-style bridge with two jumper wires on the protocol Pmod:
    uio0 -> uio1 (UART TX -> UART RX) and uio3 -> uio4 (SPI MOSI -> MISO).

    Engine 0 sends `message` as UART (64 clocks/bit), engine 1 receives it,
    the autonomous route forwards every byte to engine 2's TX FIFO, engine 2
    shifts it out as SPI mode 0 and captures MISO (= MOSI through the
    jumper). The host then reads engine 2's RX FIFO: it must equal `message`.
    """
    words = list(message[:8])
    chip.reset()
    for name in ("uart-tx", "uart-rx", "spi-controller-mode0"):
        image = chip.load_firmware(name)
        log(f"loaded {name} on engine {image['engine']}")
    chip.push_tx(0, words)
    chip.route(1, 2, len(words))
    chip.command(START, 0b0111)
    deadline = time.monotonic() + 2.0
    while True:
        st = chip.engine_status(2)
        if st["rx_level"] >= len(words) or time.monotonic() > deadline:
            break
    chip.command(STOP, 0b0111)
    uart = chip.engine_status(1)
    spi = chip.engine_status(2)
    got = chip.pop_rx(2, spi["rx_level"])
    log(f"engine 2 received {bytes(w & 0xFF for w in got)!r} over SPI")
    # The UART receiver ends with a bounded-wait timeout (fault 3) once the line
    # stays idle for 12 bit periods after the last byte; that is expected.
    expect(uart["fault"] in (0, 3), f"UART RX engine fault {uart['fault']}")
    expect(spi["fault"] == 0, f"SPI engine fault {spi['fault']}")
    expect(got == words, f"engine 2 RX {got}, expected {words} (check the two jumpers)")
    chip.reset()
    log("LOOPBACK PASS")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. /dev/ttyUSB1 or COM5")
    ap.add_argument("--baud", type=int, default=1_000_000)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="identify the bitstream, measure the core clock")
    sub.add_parser("selftest", help="bring-up test with nothing connected")
    sub.add_parser("loopback", help="UART->route->SPI test with two jumpers")
    p = sub.add_parser("load", help="load firmware/<name>.image.json into its engine")
    p.add_argument("name")
    p = sub.add_parser("status", help="status of all four engines")
    p = sub.add_parser("reset", help="pulse the core reset")
    a = ap.parse_args(argv)
    bridge = Bridge(SerialTransport(a.port, a.baud))
    chip = Chip(bridge)
    try:
        if a.cmd == "info":
            info(bridge)
            print(f"ISA version {chip.status(RS_VERSION)}")
        elif a.cmd == "selftest":
            selftest(chip)
        elif a.cmd == "loopback":
            loopback(chip)
        elif a.cmd == "load":
            image = chip.load_firmware(a.name)
            print(f"loaded {a.name} ({len(image['words'])} words) on engine {image['engine']}")
        elif a.cmd == "status":
            for e in range(4):
                print(e, chip.engine_status(e))
        elif a.cmd == "reset":
            chip.reset()
    except (CheckFailure, BridgeError) as err:
        print(f"FAIL: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
