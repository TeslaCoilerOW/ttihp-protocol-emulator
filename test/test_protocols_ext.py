# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""SWD, WS2812B, PS/2 and 1-Wire firmware images against spec-based peers.

Not part of the default suite or of the gate-level test (it is not in
Makefile's COCOTB_TEST_MODULES). Run it explicitly, RTL only:

    make COCOTB_TEST_MODULES=test_protocols_ext

Every cycle the harness compares the DUT with the reference model (lockstep,
harness.py). In addition, each image is checked at the pads by a peer written
here from the cited specification, independently of the firmware sources, the
reference model and tools/timing:

* ``SwdTarget``: an Arm ADIv5 SW-DP (ADIv5.1 Supplement DSA09-PRDC-008772:
  JTAG-to-SWD select 6.2.1, protocol errors and lockout 8.3.5, line reset and
  the reset state 8.3.6, turnaround 8.3.3). It answers DP reads (DPIDR,
  CTRL/STAT, RDBUFF) and reports contention and host timing errors.
* ``Ws2812Decoder``: classifies every high time with the datasheet's T0H/T1H
  windows, checks T0L/T1L and treats a low time above RES as a latch
  (Worldsemi WS2812B and WS2812B-V5 V1.0 tables).
* ``Ps2Host``: a PS/2 host on the open-drain CLK/DATA pair that reads DATA on
  CLK falling edges, checks frames (start 0, odd parity, stop 1) and the
  device-to-host timing of Chapweske's "The PS/2 Mouse/Keyboard Protocol",
  and can inhibit the device by holding CLK low.
* ``Ps2Device``: the device side of host-to-device commands (same source):
  request to send, bits read on the device's rising CLK edges, acknowledge,
  then response frames.
* ``OneWireSlave``: a DS18B20-like slave (DS18B20 datasheet timing): reset
  detection, presence pulse, ROM command, Read ROM (0x33) with a Maxim CRC-8.

The images are loaded from ``firmware/`` as they are. An image's tests are
skipped on a design that cannot run it unchanged: another engine count, data
width, queue depth or issue mode, or a word that the design's ISA-version-3
knobs change (``model/variant.py`` ``Options.image_differences``). Of the images
only ``ps2-host`` has such a word, its SHR by 21, which a design with byte-lane
shifts faults on; ``test_ps2_host_restricted`` checks that fault there.

These are digital simulations; they are not measurements of hardware.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence

import cocotb

import variants
from harness import CLEAR, CLOCK_NS, FIRMWARE, RS_PC, RS_STATUS, SELECT, START, STOP, Harness, design_config
from model.reference import Fault, Outputs

CLOCK_S = CLOCK_NS * 1e-9          # 20 ns: the 50 MHz clock of info.yaml
QUICK = os.environ.get("PE_EXT_QUICK", "") not in ("", "0")


def us(x: float) -> int:
    """Microseconds to clock cycles."""
    return round(x * 1e-6 / CLOCK_S)


def cycles_to_us(n: int) -> float:
    return n * CLOCK_S * 1e6


def fault_of(status: int) -> int:
    return status >> 8 & 0xFF if status & 8 else 0


def same_architecture(name: str, *, queue_depth: bool = True) -> bool:
    """The images target the design-of-record architecture (4 engines, 32 bits, 8-word FIFOs).
    True when the design under test has the image's engine count, data width, issue
    mode and, unless ``queue_depth`` is False, queue depth."""
    try:
        arch = json.loads((FIRMWARE / f"{name}.image.json").read_text())["architecture"]
    except (OSError, ValueError, KeyError):
        return False
    design = design_config()
    keep = 4 if queue_depth else 3
    return (design.engines, design.width, design.fused, design.fifo_words)[:keep] == (
        arch["engine_count"], arch["data_width"], arch["issue"] == "fused", arch["fifo_words"])[:keep]


def restricted_words(name: str) -> list[tuple[int, str]]:
    """(PC, reason) of the image's words that the design under test executes
    differently from the design of record: its ISA-version-3 knobs (byte-lane
    shifts, saturating 7-bit PC; docs/isa.md, "ISA version")."""
    words = json.loads((FIRMWARE / f"{name}.image.json").read_text())["words"]
    return variants.options(design_config()).image_differences(words)


def compatible(name: str) -> bool:
    """The image runs unchanged on the design under test: same architecture and
    no restricted word (ps2-host shifts by 21, which byte-lane designs fault on)."""
    return same_architecture(name) and not restricted_words(name)


async def load(h: Harness, name: str) -> dict:
    """Load firmware/<name>.image.json (these images are not in the variant image sets)."""
    old = os.environ.get("PE_FIRMWARE")
    os.environ["PE_FIRMWARE"] = str(FIRMWARE)
    try:
        return await h.load_firmware(name)
    finally:
        if old is None:
            del os.environ["PE_FIRMWARE"]
        else:
            os.environ["PE_FIRMWARE"] = old


def drive(out: Outputs, pin: int) -> tuple[int, int]:
    """(output enable, value) of one DUT pin."""
    return out.uio_oe >> pin & 1, out.uio_out >> pin & 1


# ----------------------------------------------------------------------------
# SWD target (Arm ADIv5 SW-DP)
# ----------------------------------------------------------------------------

JTAG_TO_SWD = 0xE79E        # 16 bits, LSB first (ADIv5.1 Supplement 6.2.1)
ACK_OK, ACK_WAIT, ACK_FAULT = 0b001, 0b010, 0b100


class SwdTarget:
    """SW-DP target model. SWCLK has a pull-down, SWDIO a pull-up (8.3.2).

    The target samples SWDIO on SWCLK rising edges and changes its own output
    ``delay`` clocks after a rising edge. After the 8-bit header it waits one
    turnaround edge, drives ACK[0:2], RDATA[0:31] and the parity bit on the
    following rising edges, releases the line on the next one and ignores one
    more (the second turnaround).
    """

    def __init__(self, clk: int = 0, dio: int = 1, *, delay: int = 0, dpidr: int = 0x2BA01477,
                 ctrl_stat: int = 0xF0000040, ack: int = ACK_OK, bad_parity: bool = False) -> None:
        self.clk, self.dio, self.delay = clk, dio, delay
        self.regs = {0: dpidr, 1: ctrl_stat, 3: 0}
        self.ack, self.bad_parity = ack, bad_parity
        self.mode = "jtag"          # the SWJ-DP powers up in JTAG operation
        self.state = "reset"        # SW interface state: reset, active, error, lockout
        self.history: list[int] = []
        self.ones = 0
        self.header: list[int] | None = None
        self.sending: list[int] | None = None
        self.skip = 0
        self.out: int | None = None   # target drive: None released, else level
        self.pending: list[tuple[int, int | None]] = []
        self.prev_clk = 0
        self.prev_host: tuple[int, int] | None = None
        self.host_change = -10
        self.edges = 0
        self.last_parity_edge: int | None = None
        self.selected_at: int | None = None
        self.line_resets = 0
        self.transactions: list[dict] = []
        self.violations: list[str] = []

    # -- pads
    def update(self, cycle: int, out: Outputs) -> tuple[int, int]:
        while self.pending and self.pending[0][0] <= cycle:
            self.out = self.pending.pop(0)[1]
        c_oe, c_val = drive(out, self.clk)
        clk = c_val if c_oe else 0
        host = drive(out, self.dio)
        if host[0] and self.out is not None:
            self.violations.append(f"cycle {cycle}: contention on SWDIO (host and target drive)")
        level = host[1] if host[0] else (self.out if self.out is not None else 1)
        if host != self.prev_host:
            if self.prev_host is not None and clk and self.prev_clk:
                self.violations.append(f"cycle {cycle}: host changed SWDIO while SWCLK was high")
            self.host_change = cycle
            self.prev_host = host
        if clk and not self.prev_clk:
            if host[0] and cycle - self.host_change < 1:
                self.violations.append(f"cycle {cycle}: SWDIO changed with the SWCLK rise")
            self.rise(cycle, level)
        self.prev_clk = clk
        return clk, level

    def launch(self, cycle: int, value: int | None) -> None:
        self.pending.append((cycle + self.delay, value))

    # -- protocol, one call per SWCLK rising edge
    def rise(self, cycle: int, level: int) -> None:
        self.edges += 1
        if self.sending is not None:
            if self.sending:
                bit = self.sending.pop(0)
                self.launch(cycle, bit)
                if not self.sending and self.transactions and self.transactions[-1]["ack"] == ACK_OK:
                    self.last_parity_edge = self.edges + 1   # the host samples it on the next rise
            else:
                self.launch(cycle, None)
                self.sending, self.skip = None, 1
            return
        if self.skip:
            self.skip -= 1
            return
        if self.mode == "jtag":
            self.history.append(level)
            h = self.history[-66:]
            if len(h) == 66 and all(h[:50]) and sum(b << k for k, b in enumerate(h[50:])) == JTAG_TO_SWD:
                self.mode, self.state, self.header, self.ones = "swd", "reset", None, 0
                self.selected_at = cycle
            return
        if level:
            self.ones += 1
        else:
            if self.ones >= 50:        # >= 50 cycles high, then an idle cycle: line reset (8.3.6)
                self.state, self.header = "reset", None
                self.line_resets += 1
                self.ones = 0
                return
            self.ones = 0
        if self.state == "lockout":
            return
        if self.header is None:
            if level:
                self.header = [1]
            return
        self.header.append(level)
        if len(self.header) == 8:
            self.decode(self.header)
            self.header = None

    def decode(self, b: list[int]) -> None:
        apndp, rnw, a = b[1], b[2], b[3] | b[4] << 1
        if b[6] != 0 or b[7] != 1 or b[5] != (b[1] ^ b[2] ^ b[3] ^ b[4]):
            self.state = "lockout" if self.state == "error" else "error"     # 8.3.5
            return
        dpidr_read = apndp == 0 and rnw == 1 and a == 0
        if self.state in ("reset", "error") and not dpidr_read:
            if self.state == "reset":
                self.violations.append("transaction other than a DPIDR read in the reset state")
            self.state = "lockout" if self.state == "error" else self.state
            return
        if not rnw:
            self.violations.append("write transaction (not modelled)")
            return
        if apndp:
            self.violations.append("AP read (not modelled)")
            return
        data = self.regs.get(a, 0)
        ack = self.ack
        bits = [ack >> k & 1 for k in range(3)]
        if ack == ACK_OK:
            parity = bin(data).count("1") & 1
            bits += [data >> k & 1 for k in range(32)] + [parity ^ int(self.bad_parity)]
            self.state = "active"
        self.transactions.append({"header": sum(v << k for k, v in enumerate(b)), "ack": ack,
                                  "data": data if ack == ACK_OK else None})
        self.sending = bits          # the next rising edge is the turnaround: ACK[0] follows it

    @property
    def clocks_after_parity(self) -> int:
        return 0 if self.last_parity_edge is None else self.edges - self.last_parity_edge


def swd_header(apndp: int, rnw: int, a: int) -> int:
    a2, a3 = a & 1, a >> 1 & 1
    parity = apndp ^ rnw ^ a2 ^ a3
    return 1 | apndp << 1 | rnw << 2 | a2 << 3 | a3 << 4 | parity << 5 | 0 << 6 | 1 << 7


DPIDR_READ, CTRL_STAT_READ = swd_header(0, 1, 0), swd_header(0, 1, 1)
CONNECT = 1 << 8


async def swd_session(h: Harness, target: SwdTarget, words: Sequence[int]) -> None:
    await h.start()
    h.pins = lambda cycle, out: _merge(out, dict(zip((target.clk, target.dio), target.update(cycle, out))))
    image = await load(h, "swd-read")
    assert image["engine"] == 0
    await h.command(SELECT, 0)
    for word in words:
        await h.write(2, word)
    await h.command(START, 1)


async def swd_reads(h: Harness, delay: int = 0) -> list[int]:
    """Connect and read DPIDR, read DPIDR again and CTRL/STAT without a new connect."""
    target = SwdTarget(delay=delay)
    await swd_session(h, target, [CONNECT | DPIDR_READ, DPIDR_READ, CTRL_STAT_READ])
    got = [await h.read(3) for _ in range(3)]
    await h.idle(64)
    assert target.mode == "swd" and target.selected_at is not None, "JTAG-to-SWD sequence not recognised"
    assert target.line_resets == 1, f"line resets seen: {target.line_resets}"
    assert target.violations == [], target.violations
    assert [t["header"] for t in target.transactions] == [DPIDR_READ, DPIDR_READ, CTRL_STAT_READ]
    assert target.clocks_after_parity >= 8, target.clocks_after_parity
    assert fault_of(await h.status(RS_STATUS)) == 0
    await h.command(STOP, 1)
    return got


async def swd_error(h: Harness, case: str) -> tuple[int, list[int]]:
    """One failing request: returns the fault code and the words the image pushed."""
    target = {"fault": SwdTarget(ack=ACK_FAULT), "wait": SwdTarget(ack=ACK_WAIT),
              "parity": SwdTarget(bad_parity=True)}.get(case, SwdTarget())
    word = {"write": swd_header(0, 0, 1),                  # a write header: rejected by the image
            "no-connect": DPIDR_READ}.get(case, CONNECT | DPIDR_READ)
    await swd_session(h, target, [word])
    await h.run_until(lambda: h.engine(0).fault != 0, 20000, f"SWD {case} fault")
    code = fault_of(await h.status(RS_STATUS))
    pushed = []
    while h.engine(0).rx:
        pushed.append(await h.read(3))
    if case == "write":
        assert target.edges == 0, "a rejected write header must not clock SWCLK"
    assert target.violations == [], target.violations
    await h.command(CLEAR, 1)
    return code, pushed


# ----------------------------------------------------------------------------
# WS2812B decoder
# ----------------------------------------------------------------------------

# Worldsemi WS2812B datasheet: T0H 0.4 us, T1H 0.8 us, T0L 0.85 us, T1L 0.45 us (+-150 ns), RES > 50 us.
WS2812B = {"T0H": (0.25, 0.55), "T1H": (0.65, 0.95), "T0L": (0.70, 1.00), "T1L": (0.30, 0.60), "RES": 50.0}
# Worldsemi WS2812B-V5 datasheet V1.0: T0H 220-380 ns, T1H 580-1000 ns, T0L and T1L 580-1000 ns, RES > 280 us.
WS2812B_V5 = {"T0H": (0.22, 0.38), "T1H": (0.58, 1.00), "T0L": (0.58, 1.00), "T1L": (0.58, 1.00), "RES": 280.0}


class Ws2812Decoder:
    """A WS2812B input as the datasheet describes it; the line has a pull-down."""

    def __init__(self, pin: int, table: dict) -> None:
        self.pin, self.t = pin, table
        self.level = 0
        self.rise = self.fall = self.driven_at = None
        self.bits: list[int] = []
        self.pixels: list[int] = []
        self.frames: list[list[int]] = []
        self.last_bit: int | None = None
        self.highs: set[int] = set()
        self.lows: set[int] = set()
        self.resets: list[int] = []
        self.violations: list[str] = []

    @staticmethod
    def inside(x: float, w: tuple) -> bool:
        return w[0] <= x <= w[1]

    def update(self, cycle: int, out: Outputs) -> int:
        oe, val = drive(out, self.pin)
        if oe and self.driven_at is None:
            self.driven_at = cycle
        level = val if oe else 0
        if level and not self.level:
            low_start = self.fall if self.fall is not None else self.driven_at
            low = cycle - low_start if low_start is not None else 0
            if self.fall is None or cycles_to_us(low) > self.t["RES"]:
                self.resets.append(low)
                if cycles_to_us(low) <= self.t["RES"]:
                    self.violations.append(f"cycle {cycle}: first rise after only {cycles_to_us(low):.2f} us low")
            else:
                self.lows.add(low)
                key = "T1L" if self.last_bit else "T0L"
                if not self.inside(cycles_to_us(low), self.t[key]):
                    self.violations.append(f"cycle {cycle}: low {cycles_to_us(low) * 1e3:.0f} ns after a "
                                           f"{self.last_bit} bit outside {key} {self.t[key]} us")
            self.rise = cycle
        if not level and self.level and self.rise is not None:
            high = cycle - self.rise
            self.highs.add(high)
            if self.inside(cycles_to_us(high), self.t["T0H"]):
                bit = 0
            elif self.inside(cycles_to_us(high), self.t["T1H"]):
                bit = 1
            else:
                bit = None
                self.violations.append(f"cycle {cycle}: high {cycles_to_us(high) * 1e3:.0f} ns is neither T0H "
                                       "nor T1H")
            if bit is not None:
                self.bits.append(bit)
                self.last_bit = bit
                if len(self.bits) == 24:
                    self.pixels.append(sum(b << (23 - k) for k, b in enumerate(self.bits)))
                    self.bits = []
            self.fall = cycle
        if not level and self.fall is not None and self.pixels and cycles_to_us(cycle - self.fall) > self.t["RES"]:
            if self.bits:
                self.violations.append(f"cycle {cycle}: latch with {len(self.bits)} bits of a partial pixel")
                self.bits = []
            self.frames.append(self.pixels)      # reset code: the chain latches its data
            self.pixels = []
        self.level = level
        return level


async def ws2812_frames(h: Harness, image: str, table: dict, other: dict,
                        frames: Sequence[Sequence[int]]) -> tuple[Ws2812Decoder, Ws2812Decoder]:
    """Send frames of GRB pixels; the last word of each frame has L = 1 (latch)."""
    await h.start()
    dec, cross = Ws2812Decoder(2, table), Ws2812Decoder(2, other)
    h.pins = lambda cycle, out: _merge(out, {2: (dec.update(cycle, out), cross.update(cycle, out))[0]})
    loaded = await load(h, image)
    assert loaded["engine"] == 1
    words = [(px << 8) | int(k == len(f) - 1) for f in frames for k, px in enumerate(f)]
    await h.command(SELECT, 1)
    for word in words[:8]:
        await h.write(2, word)
    await h.command(START, 2)
    for word in words[8:]:
        await h.write(2, word)
    await h.run_until(lambda: len(dec.frames) == len(frames), 20000 + 17000 * len(frames) + 1600 * len(words),
                      "WS2812 frames")
    assert dec.violations == [], dec.violations[:5]
    assert dec.frames == [list(f) for f in frames], [[hex(p) for p in f] for f in dec.frames]
    assert fault_of(await h.status(RS_STATUS)) == 0
    await h.command(STOP, 2)
    return dec, cross


# ----------------------------------------------------------------------------
# PS/2 host
# ----------------------------------------------------------------------------

class Ps2Host:
    """PS/2 host on open-drain CLK and DATA with pull-ups.

    It reads DATA on each CLK falling edge made by the device, assembles
    11-bit frames and checks Chapweske's device-to-host timing. ``inhibits``
    are callbacks ``f(host, cycle) -> bool``: while one returns True the host
    holds CLK low, and a frame cut before its 11th falling edge is discarded.
    """

    def __init__(self, clk: int = 4, dat: int = 5, inhibits: Sequence[Callable] = ()) -> None:
        self.clk, self.dat = clk, dat
        self.inhibits = list(inhibits)
        self.clk_level = self.dat_level = 1
        self.holding = False
        self.last_rise = 0
        self.last_fall: int | None = None
        self.last_data_change: int | None = None
        self.bits: list[int] = []
        self.frame_falls: list[int] = []
        self.bytes: list[int] = []
        self.aborted = 0
        self.inhibit_log: list[tuple[int, int]] = []
        self.falls = 0
        self.timing: dict[str, set[int]] = {"low": set(), "high": set(), "to_fall": set(), "from_rise": set(),
                                            "idle": set()}
        self.violations: list[str] = []
        self.suspect = False           # timing around an inhibit is not the device's

    def update(self, cycle: int, out: Outputs) -> tuple[int, int]:
        hold = any(f(self, cycle) for f in self.inhibits)
        if hold and not self.holding:
            self.inhibit_log.append((cycle, len(self.bits)))
            if 0 < len(self.bits) < 11:
                self.aborted += 1
            if len(self.bits) < 11:
                self.bits, self.frame_falls = [], []
            self.suspect = True
        self.holding = hold
        c_oe, c_val = drive(out, self.clk)
        d_oe, d_val = drive(out, self.dat)
        if (c_oe and c_val) or (d_oe and d_val):
            self.violations.append(f"cycle {cycle}: a PS/2 line driven high")
        dev_clk_low = bool(c_oe and not c_val)
        clk = 0 if (dev_clk_low or hold) else 1
        dat = 0 if (d_oe and not d_val) else 1
        us_ = cycles_to_us
        if dat != self.dat_level:
            if clk == 0 and not hold:
                self.violations.append(f"cycle {cycle}: DATA changed while CLK low")
            if self.bits and not self.suspect:
                self.timing["from_rise"].add(cycle - self.last_rise)
                if us_(cycle - self.last_rise) < 5:
                    self.violations.append(f"cycle {cycle}: DATA changed {us_(cycle - self.last_rise):.2f} us "
                                           "after the CLK rise (< 5 us)")
            if not self.bits and dat == 0 and clk:
                idle = cycle - self.last_rise
                self.timing["idle"].add(idle)
                if us_(idle) < 50:
                    self.violations.append(f"cycle {cycle}: start bit after only {us_(idle):.2f} us of CLK high")
            self.last_data_change = cycle
        if clk == 0 and self.clk_level == 1 and dev_clk_low and not hold:      # device falling edge
            self.falls += 1
            if self.bits and not self.suspect:
                high = cycle - self.last_rise
                self.timing["high"].add(high)
                if not 30 <= us_(high) <= 50:
                    self.violations.append(f"cycle {cycle}: CLK high {us_(high):.2f} us outside 30-50 us")
            if self.last_data_change is not None and self.last_data_change > self.last_rise:
                d = cycle - self.last_data_change
                self.timing["to_fall"].add(d)
                if not 5 <= us_(d) <= 25:
                    self.violations.append(f"cycle {cycle}: DATA change to CLK fall {us_(d):.2f} us outside 5-25 us")
            self.bits.append(dat)
            self.frame_falls.append(cycle)
            self.last_fall = cycle
            if len(self.bits) == 11:
                self.frame()
        if clk == 1 and self.clk_level == 0:
            if self.last_fall is not None and not self.suspect and self.last_fall > self.last_rise:
                low = cycle - self.last_fall
                self.timing["low"].add(low)
                if not 30 <= us_(low) <= 50:
                    self.violations.append(f"cycle {cycle}: CLK low {us_(low):.2f} us outside 30-50 us")
            self.last_rise = cycle
            if not hold and not self.bits:
                self.suspect = False
        self.clk_level, self.dat_level = clk, dat
        return clk, dat

    def frame(self) -> None:
        b = self.bits
        value = sum(v << k for k, v in enumerate(b[1:9]))
        if b[0] != 0 or b[10] != 1 or (sum(b[1:10]) & 1) != 1:
            self.violations.append(f"bad frame {b}")
        else:
            self.bytes.append(value)
        self.bits, self.frame_falls = [], []


def hold_after(frame: int, fall: int, delay: int, length: int) -> Callable:
    """Hold CLK low for ``length`` cycles, starting ``delay`` cycles after falling
    edge ``fall`` (1..11) of frame ``frame`` (0-based)."""
    state: dict[str, int] = {}

    def f(host: Ps2Host, cycle: int) -> bool:
        if "start" not in state and len(host.bytes) == frame and len(host.frame_falls) == fall:
            state["start"] = host.frame_falls[-1] + delay
        if "start" not in state and fall == 11 and len(host.bytes) == frame + 1 and host.last_fall is not None:
            state["start"] = host.last_fall + delay
        s = state.get("start")
        return s is not None and s <= cycle < s + length

    return f


async def ps2_session(h: Harness, data: Sequence[int], inhibits: Sequence[Callable] = (),
                      extra: int = 0) -> Ps2Host:
    await h.start()
    host = Ps2Host(inhibits=inhibits)
    h.pins = lambda cycle, out: _merge(out, dict(zip((4, 5), host.update(cycle, out))))
    image = await load(h, "ps2-device")
    assert image["engine"] == 2
    await h.command(SELECT, 2)
    for word in data:
        await h.write(2, word)
    await h.command(START, 4)
    await h.run_until(lambda: len(host.bytes) == len(data), 60000 * (len(data) + 1) + extra, "PS/2 frames")
    await h.idle(8000)
    assert host.violations == [], host.violations[:5]
    assert host.bytes == list(data), [hex(b) for b in host.bytes]
    assert fault_of(await h.status(RS_STATUS)) == 0
    await h.command(STOP, 4)
    return host


class Ps2Device:
    """PS/2 device (keyboard side) for the host-to-device direction.

    It waits for the host's request to send (CLK held low, then DATA low, then
    CLK released), clocks in 8 data bits, parity and stop on its own CLK
    (``half`` us low and high), reading DATA on each rising edge, acknowledges
    with DATA low during an 11th clock and then sends its response bytes as
    device-to-host frames (DATA changes in the middle of the CLK high phase).
    Checked on the host: CLK low >= 100 us before DATA goes low, DATA changed
    only while CLK is low, the stop bit released, odd parity, no host drive
    while the device sends.
    """

    RESPONSES = {0xFF: [0xFA, 0xAA], 0xEE: [0xEE], 0xF2: [0xFA, 0xAB, 0x83], 0xF4: [0xFA]}

    def __init__(self, clk: int = 4, dat: int = 5, *, half: float = 40, first_clock: float = 100,
                 present: bool = True) -> None:
        self.clk, self.dat, self.half, self.first_clock, self.present = clk, dat, half, first_clock, present
        self.state = "idle"
        self.dev_clk = self.dev_dat = 0          # 1: the device pulls the line low
        self.plan: list[tuple[int, str, int]] = []
        self.host_clk_since: int | None = None
        self.host_dat_since: int | None = None
        self.prev_host_dat = self.prev_host_clk = 0
        self.bits: list[int] = []
        self.commands: list[int] = []
        self.sent: list[int] = []
        self.violations: list[str] = []

    def schedule(self, cycle: int, what: str, value: int) -> None:
        self.plan.append((cycle, what, value))
        self.plan.sort()

    def update(self, cycle: int, out: Outputs) -> tuple[int, int]:
        c_oe, c_val = drive(out, self.clk)
        d_oe, d_val = drive(out, self.dat)
        if (c_oe and c_val) or (d_oe and d_val):
            self.violations.append(f"cycle {cycle}: a PS/2 line driven high")
        host_clk, host_dat = int(c_oe and not c_val), int(d_oe and not d_val)
        if host_clk and not self.prev_host_clk:
            self.host_clk_since = cycle
        if not host_clk:
            self.host_clk_since = None
        self.prev_host_clk = host_clk
        while self.plan and self.plan[0][0] <= cycle:
            _, what, value = self.plan.pop(0)
            if what == "clk":
                self.dev_clk = value
                if not value and self.state == "rx":
                    self.rise(cycle)
            elif what == "dat":
                self.dev_dat = value
            elif what == "state":
                self.state = ("idle", "rx", "tx")[value]
        clk = 0 if (host_clk or self.dev_clk) else 1
        if host_dat != self.prev_host_dat:
            if self.state == "rx" and clk:
                self.violations.append(f"cycle {cycle}: host changed DATA while CLK was high")
            if self.state == "tx":
                self.violations.append(f"cycle {cycle}: host drove DATA while the device was sending")
            if host_dat and self.state == "idle":
                if self.host_clk_since is None or cycles_to_us(cycle - self.host_clk_since) < 100:
                    self.violations.append(f"cycle {cycle}: DATA low without CLK held low for 100 us")
            self.prev_host_dat = host_dat
        if self.state in ("rx", "tx") and host_clk and not self.dev_clk:
            self.violations.append(f"cycle {cycle}: host pulled CLK low during a transfer")
        if self.state == "idle" and host_dat and not host_clk and self.present and not self.plan:
            self.bits = []
            self.state = "rx"
            first = cycle + us(self.first_clock)
            for k in range(11):                                  # 10 bits, then the acknowledge clock
                t = first + k * 2 * us(self.half)
                self.schedule(t, "clk", 1)
                self.schedule(t + us(self.half), "clk", 0)
        dat = 0 if (host_dat or self.dev_dat) else 1
        return clk, dat

    def rise(self, cycle: int) -> None:
        """The device releases CLK: read DATA (host to device)."""
        k = len(self.bits)
        if k < 10:
            dat = 0 if (self.prev_host_dat or self.dev_dat) else 1
            self.bits.append(dat)
        if len(self.bits) == 10 and k == 9:
            b = self.bits
            if b[9] != 1:
                self.violations.append(f"cycle {cycle}: no stop bit")
            if sum(b[:9]) & 1 != 1:
                self.violations.append(f"cycle {cycle}: parity error in {b}")
            cmd = sum(v << i for i, v in enumerate(b[:8]))
            self.commands.append(cmd)
            self.schedule(cycle + us(5), "dat", 1)             # acknowledge: DATA low for the 11th clock
            end = cycle + 2 * us(self.half)
            self.schedule(end + us(5), "dat", 0)
            self.schedule(end + us(10), "state", 0)
            t = end + us(100)
            for byte in self.RESPONSES.get(cmd, []):
                t = self.frame(t, byte)
                t += us(100)

    def frame(self, t: int, byte: int) -> int:
        bits = [0] + [byte >> i & 1 for i in range(8)] + [1 - (bin(byte).count("1") & 1), 1]
        h = us(self.half)
        self.schedule(t, "state", 2)
        for k, b in enumerate(bits):
            start = t + k * 2 * h
            self.schedule(start, "dat", 1 - b)                  # DATA change in the high phase
            self.schedule(start + h // 2, "clk", 1)             # CLK low
            self.schedule(start + h // 2 + h, "clk", 0)
        end = t + (len(bits) - 1) * 2 * h + h // 2 + h      # the 11th rising edge ends the frame
        self.schedule(end, "dat", 0)
        self.schedule(end, "state", 0)
        self.sent.append(byte)
        return end


async def ps2_host_session(h: Harness, device: Ps2Device, commands: Sequence[int]) -> list[int]:
    """Send commands (count << 8 | byte) and return the data bytes of the frames read back."""
    await h.start()
    h.pins = lambda cycle, out: _merge(out, dict(zip((4, 5), device.update(cycle, out))))
    image = await load(h, "ps2-host")
    assert image["engine"] == 2
    await h.command(SELECT, 2)
    await h.command(START, 4)
    data = []
    for word in commands:
        await h.write(2, word)
        for _ in range(word >> 8 & 15):
            frame = await h.read(3, timeout=400000)
            assert frame >> 11 == 0 and frame & 1 == 0 and frame >> 10 & 1 == 1, hex(frame)
            assert bin(frame >> 1 & 0x1FF).count("1") & 1 == 1, f"parity {frame:#x}"
            data.append(frame >> 1 & 0xFF)
    await h.idle(2000)
    assert device.violations == [], device.violations[:5]
    assert fault_of(await h.status(RS_STATUS)) == 0
    await h.command(STOP, 4)
    return data


# ----------------------------------------------------------------------------
# 1-Wire slave (DS18B20-like)
# ----------------------------------------------------------------------------

def crc8_maxim(data: Sequence[int]) -> int:
    """Dallas/Maxim CRC-8: polynomial x^8 + x^5 + x^4 + 1, LSB first, initial value 0."""
    crc = 0
    for byte in data:
        for k in range(8):
            mix = (crc ^ (byte >> k)) & 1
            crc >>= 1
            if mix:
                crc ^= 0x8C
    return crc


def rom_code(family: int, serial: int) -> list[int]:
    body = [family] + [serial >> (8 * k) & 0xFF for k in range(6)]
    return body + [crc8_maxim(body)]


class OneWireSlave:
    """One slave on an open-drain DQ line with a pull-up.

    Timing from the DS18B20 datasheet: a master low of at least 480 us is a
    reset (answered after ``t_pdhigh`` by a ``t_pdlow`` presence pulse); a
    write slot is sampled ``t_sample`` after the master's falling edge and DQ
    must not change between 15 us and 60 us; a 0 bit in a read slot holds DQ
    low for ``t_hold`` after the falling edge. The master's low times are
    checked: write 1 1-15 us, write 0 60-120 us, reset 480-960 us, slot >= 60 us,
    recovery >= 1 us, reset high time >= 480 us.
    """

    def __init__(self, pin: int = 7, rom: Sequence[int] = (), *, present: bool = True,
                 t_pdhigh: float = 30, t_pdlow: float = 120, t_sample: float = 30, t_hold: float = 30) -> None:
        self.pin, self.rom, self.present = pin, list(rom), present
        self.t_pdhigh, self.t_pdlow, self.t_sample, self.t_hold = t_pdhigh, t_pdlow, t_sample, t_hold
        self.master_low = False
        self.fall: int | None = None
        self.release: int | None = None
        self.reset_release: int | None = None
        self.pull: tuple[int, int] | None = None     # slave low from, until
        self.state = "idle"
        self.bits: list[int] = []
        self.tx: list[int] = []
        self.window: tuple[int, int] | None = None   # write-slot sample window: (cycle of 60 us, level)
        self.resets = 0
        self.commands: list[int] = []
        self.low_times: set[int] = set()
        self.violations: list[str] = []

    def update(self, cycle: int, out: Outputs) -> int:
        oe, val = drive(out, self.pin)
        if oe and val:
            self.violations.append(f"cycle {cycle}: DQ driven high")
        master = bool(oe and not val)
        if master and not self.master_low:
            self.falling(cycle)
        if not master and self.master_low:
            self.rising(cycle)
        self.master_low = master
        slave = self.pull is not None and self.pull[0] <= cycle < self.pull[1]
        level = 0 if (master or slave) else 1
        if self.window is not None:
            end, want = self.window
            if cycle >= end:
                self.window = None
            elif level != want:
                self.violations.append(f"cycle {cycle}: DQ changed inside the 15-60 us write sample window")
        if self.state == "rom" and self.fall is not None and cycle == self.fall + us(15):
            self.window = (self.fall + us(60), level)       # DQ must hold this level up to 60 us
        if self.state == "rom" and self.fall is not None and cycle == self.fall + us(self.t_sample):
            self.bits.append(level)
            if len(self.bits) == 8:
                cmd = sum(b << k for k, b in enumerate(self.bits))
                self.commands.append(cmd)
                self.bits = []
                if cmd == 0x33:
                    self.state, self.tx = "read", [b >> k & 1 for b in self.rom for k in range(8)]
                else:
                    self.state = "idle"
        return level

    def falling(self, cycle: int) -> None:
        if self.release is not None:
            gap = cycle - self.release
            if cycles_to_us(gap) < 1:
                self.violations.append(f"cycle {cycle}: recovery {cycles_to_us(gap):.2f} us < 1 us")
            if self.reset_release is not None and self.release == self.reset_release \
                    and cycles_to_us(gap) < 480:
                self.violations.append(f"cycle {cycle}: reset high time {cycles_to_us(gap):.2f} us < 480 us")
        if self.fall is not None and cycles_to_us(cycle - self.fall) < 60 and self.release != self.reset_release:
            self.violations.append(f"cycle {cycle}: slot shorter than 60 us")
        self.fall = cycle
        if self.state == "read" and self.tx:
            bit = self.tx.pop(0)
            if not bit:
                self.pull = (cycle, cycle + us(self.t_hold))
            if not self.tx:
                self.state = "idle"

    def rising(self, cycle: int) -> None:
        low = cycle - (self.fall or 0)
        self.low_times.add(low)
        self.release = cycle
        t = cycles_to_us(low)
        if t >= 480:
            if t > 960:
                self.violations.append(f"cycle {cycle}: reset low {t:.1f} us > 960 us")
            self.resets += 1
            self.reset_release = cycle
            self.state, self.bits = "rom", []
            if self.present:
                start = cycle + us(self.t_pdhigh)
                self.pull = (start, start + us(self.t_pdlow))
        elif not (1 <= t <= 15 or 60 <= t <= 120):
            self.violations.append(f"cycle {cycle}: master low {t:.2f} us is neither a write 1 (1-15 us), "
                                   "a write 0 (60-120 us) nor a reset (>= 480 us)")


ROM = rom_code(0x28, 0x0000_1A2B_3C4D)


async def onewire_read_rom(h: Harness, slave: OneWireSlave) -> list[int]:
    """Reset, presence, Read ROM (0x33) and eight read bytes; returns the RX words."""
    await h.start()
    h.pins = lambda cycle, out: _merge(out, {7: slave.update(cycle, out)})
    image = await load(h, "onewire-master")
    assert image["engine"] == 3
    await h.command(SELECT, 3)
    words = [0x100 | 0x33] + [0xFF] * 8
    for word in words[:8]:
        await h.write(2, word)
    await h.command(START, 8)
    got = []
    for word in words[8:]:
        got.append(await h.read(3, timeout=200000))
        await h.command(SELECT, 3)
        await h.write(2, word)
    await h.command(SELECT, 3)
    while len(got) < len(words):
        got.append(await h.read(3, timeout=200000))
    await h.idle(64)
    assert fault_of(await h.status(RS_STATUS)) == 0
    await h.command(STOP, 8)
    return got


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _merge(out: Outputs, levels: dict[int, int]) -> int:
    """Pad value: the peer-resolved levels on its pins, the DUT's drive (or 1) elsewhere."""
    pads = (out.uio_out & out.uio_oe) | (0xFF & ~out.uio_oe)
    for pin, level in levels.items():
        pads = (pads & ~(1 << pin)) | (level & 1) << pin
    return pads & 0xFF



# ----------------------------------------------------------------------------
# scenarios: each runs on the DUT (cocotb) or on the reference model alone
# (harness.ModelHarness with harness.run_model)
# ----------------------------------------------------------------------------

async def swd_connect_dpidr(h: Harness) -> None:
    got = await swd_reads(h)
    assert got == [0x2BA01477, 0x2BA01477, 0xF0000040], [hex(g) for g in got]


async def swd_target_delay(h: Harness, delay: int) -> None:
    target = SwdTarget(delay=delay)
    await swd_session(h, target, [CONNECT | DPIDR_READ])
    if delay < 30:
        assert await h.read(3) == 0x2BA01477
        assert target.violations == [], target.violations
    else:
        # every target bit reaches the sampled pad value one sample late: the ACK reads 0b110
        await h.run_until(lambda: h.engine(0).fault != 0, 20000, "SWD late target")
        assert fault_of(await h.status(RS_STATUS)) == 80
        assert await h.read(3) == 0b110
    await h.command(CLEAR, 1)


async def swd_errors(h: Harness, case: str) -> None:
    code, pushed = await swd_error(h, case)
    want = {"fault": (80, [0b001]), "wait": (80, [0b010]), "parity": (81, [0x2BA01477]),
            "write": (82, []), "no-connect": (80, [0b111])}[case]
    assert (code, pushed) == want, (code, [hex(p) for p in pushed])


async def ws2812_classic(h: Harness) -> None:
    frames = [[0x123456, 0xA55AFF, 0x008001], [0xFF0000]]
    if not QUICK:
        frames.append([0x000000, 0xFFFFFF] * 5)          # ten pixels: more words than the TX FIFO holds
    dec, cross = await ws2812_frames(h, "ws2812", WS2812B, WS2812B_V5, frames)
    assert dec.highs == {20, 40} and dec.lows == {43, 23}, (dec.highs, dec.lows)
    assert min(dec.resets) >= us(300), dec.resets
    assert cross.violations, "the WS2812B-V5 windows should reject T0H = 400 ns"


async def ws2812_v5(h: Harness) -> None:
    frames = [[0x123456, 0xA55AFF], [0x0F0F0F]]
    dec, cross = await ws2812_frames(h, "ws2812b-v5", WS2812B_V5, WS2812B, frames)
    assert dec.highs == {15, 32} and dec.lows == {48, 31}, (dec.highs, dec.lows)
    assert min(dec.resets) >= us(300), dec.resets
    assert cross.violations, "the WS2812B windows should reject T1H = 640 ns"


async def ps2_device(h: Harness) -> None:
    data = [0x1C, 0xF0, 0x1C] if QUICK else [0x1C, 0xF0, 0x1C, 0x00, 0xFF]
    host = await ps2_session(h, data)
    assert host.timing["low"] == {2000} and host.timing["high"] == {2000}, host.timing
    assert host.timing["to_fall"] == {1000} and host.timing["from_rise"] <= {1000}, host.timing
    assert min(host.timing["idle"]) >= us(50), host.timing["idle"]
    assert host.aborted == 0


async def ps2_inhibit(h: Harness, where: str) -> None:
    rule = {"high-phase": hold_after(0, 5, 2000 + 500, us(150)),     # 10 us into a CLK high phase
            "low-phase": hold_after(0, 3, 1000, us(150)),           # while the device holds CLK low
            "after-11th": hold_after(0, 11, 1000, us(200))}[where]  # like a host that inhibits after each byte
    host = await ps2_session(h, [0x5A, 0xA5], inhibits=[rule], extra=20000)
    assert len(host.inhibit_log) == 1, host.inhibit_log
    assert host.aborted == (0 if where == "after-11th" else 1)
    assert host.falls == 22 + (0 if where == "after-11th" else {"high-phase": 5, "low-phase": 3}[where])


async def ps2_host(h: Harness, half: float) -> None:
    device = Ps2Device(half=half)
    commands = [2 << 8 | 0xFF, 1 << 8 | 0xEE, 3 << 8 | 0xF2]
    got = await ps2_host_session(h, device, commands)
    assert device.commands == [0xFF, 0xEE, 0xF2], device.commands
    assert got == [0xFA, 0xAA, 0xEE, 0xFA, 0xAB, 0x83], [hex(g) for g in got]


async def ps2_host_no_device(h: Harness) -> None:
    device = Ps2Device(present=False)
    await h.start()
    h.pins = lambda cycle, out: _merge(out, dict(zip((4, 5), device.update(cycle, out))))
    await load(h, "ps2-host")
    await h.command(SELECT, 2)
    await h.command(START, 4)
    await h.write(2, 0xFF)
    await h.run_until(lambda: h.engine(2).blocked > 16, 20000, "wait for the first device clock")
    blocked_at = h.cycle - h.engine(2).blocked
    await h.run_until(lambda: h.engine(2).fault != 0, 900000, "fault 3")
    assert h.cycle - blocked_at == 800000, h.cycle - blocked_at          # LIMIT (16 ms) blocked attempts
    assert device.violations == [], device.violations
    assert fault_of(await h.status(RS_STATUS)) == 3
    assert not h.last_pre.uio_oe >> 4 & 3
    await h.command(CLEAR, 4)


async def ps2_host_restricted(h: Harness) -> None:
    """ps2-host on a design that faults on its one restricted word (the SHR by 21
    of a byte-lane design): the reset command is sent and acknowledged as on the
    design of record, and the engine faults with code 1 (invalid operand) at that
    word while it shifts the first response frame into place."""
    (pc, reason), = restricted_words("ps2-host")
    assert reason.startswith("SHR by 21"), reason
    device = Ps2Device(half=30)
    await h.start()
    h.pins = lambda cycle, out: _merge(out, dict(zip((4, 5), device.update(cycle, out))))
    await load(h, "ps2-host")
    await h.command(SELECT, 2)
    await h.command(START, 4)
    await h.write(2, 2 << 8 | 0xFF)
    await h.run_until(lambda: h.engine(2).fault != 0, 400000, "the fault at the restricted word")
    assert h.engine(2).fault == Fault.INVALID_OPERAND and h.engine(2).pc == pc, (h.engine(2).fault, h.engine(2).pc)
    assert device.commands == [0xFF], device.commands
    assert fault_of(await h.status(RS_STATUS)) == Fault.INVALID_OPERAND
    assert await h.status(RS_PC) == pc
    assert not h.last_pre.uio_oe >> 4 & 3
    await h.command(CLEAR, 4)


async def onewire_rom(h: Harness, corner: str) -> None:
    params = {"typical": {}, "early-short": dict(t_pdhigh=15, t_pdlow=60, t_sample=15, t_hold=15),
              "late-long": dict(t_pdhigh=60, t_pdlow=240, t_sample=60, t_hold=60)}[corner]
    slave = OneWireSlave(rom=ROM, **params)
    got = await onewire_read_rom(h, slave)
    assert got == [0x33] + ROM, [hex(g) for g in got]
    assert crc8_maxim(got[1:8]) == got[8]
    assert slave.resets == 1 and slave.commands == [0x33], (slave.resets, slave.commands)
    assert slave.violations == [], slave.violations[:5]
    assert slave.low_times == {us(500), us(6), us(65)}, slave.low_times


async def onewire_no_presence(h: Harness) -> None:
    slave = OneWireSlave(present=False)
    await h.start()
    h.pins = lambda cycle, out: _merge(out, {7: slave.update(cycle, out)})
    await load(h, "onewire-master")
    await h.command(SELECT, 3)
    await h.write(2, 0x133)
    await h.command(START, 8)
    await h.run_until(lambda: h.engine(3).fault != 0, 60000, "1-Wire presence fault")
    assert fault_of(await h.status(RS_STATUS)) == 96
    assert not h.last_pre.uio_oe >> 7 & 1
    assert slave.violations == [], slave.violations
    await h.command(CLEAR, 8)


async def four_engines(h: Harness) -> None:
    await h.start()
    swd = SwdTarget()
    led = Ws2812Decoder(2, WS2812B)
    ps2 = Ps2Host()
    ow = OneWireSlave(rom=ROM)

    def pins(cycle: int, out: Outputs) -> int:
        levels = dict(zip((0, 1), swd.update(cycle, out)))
        levels[2] = led.update(cycle, out)
        levels.update(zip((4, 5), ps2.update(cycle, out)))
        levels[7] = ow.update(cycle, out)
        return _merge(out, levels)

    h.pins = pins
    for name in ("swd-read", "ws2812", "ps2-device", "onewire-master"):
        await load(h, name)
    queued = {0: [CONNECT | DPIDR_READ], 1: [(0x123456 << 8) | 1], 2: [0x1C], 3: [0x133, 0xFF]}
    for engine, words in queued.items():
        await h.command(SELECT, engine)
        for word in words:
            await h.write(2, word)
    await h.command(START, 0xF)
    await h.run_until(lambda: len(ow.commands) == 1 and not ow.tx[56:] and len(ps2.bytes) == 1
                      and len(led.frames) == 1 and len(h.engine(3).rx) == 2, 400000, "four engines")
    await h.idle(4000)
    got = {}
    for engine, count in ((0, 1), (3, 2)):
        await h.command(SELECT, engine)
        got[engine] = [await h.read(3) for _ in range(count)]
    assert got == {0: [0x2BA01477], 3: [0x33, ROM[0]]}, got
    assert led.frames == [[0x123456]] and ps2.bytes == [0x1C]
    for peer in (swd, led, ps2, ow):
        assert peer.violations == [], (type(peer).__name__, peer.violations[:3])
    assert led.highs == {20, 40} and led.lows <= {43, 23}
    assert ps2.timing["low"] == {2000} and ps2.timing["high"] == {2000} and ps2.timing["to_fall"] == {1000}
    h.assert_no_faults()
    await h.command(STOP, 0xF)


# Every scenario with its images, for model-only runs (e.g. tools/timing validation).
SCENARIOS: dict[str, tuple[tuple[str, ...], Callable]] = {
    "swd_connect_dpidr": (("swd-read",), swd_connect_dpidr),
    **{f"swd_target_delay_{d}": (("swd-read",), lambda h, d=d: swd_target_delay(h, d)) for d in (29, 30)},
    **{f"swd_errors_{c}": (("swd-read",), lambda h, c=c: swd_errors(h, c))
       for c in ("fault", "wait", "parity", "write", "no-connect")},
    "ws2812": (("ws2812",), ws2812_classic),
    "ws2812b_v5": (("ws2812b-v5",), ws2812_v5),
    "ps2_device": (("ps2-device",), ps2_device),
    **{f"ps2_inhibit_{w}": (("ps2-device",), lambda h, w=w: ps2_inhibit(h, w))
       for w in ("high-phase", "low-phase", "after-11th")},
    **{f"ps2_host_{half}": (("ps2-host",), lambda h, half=half: ps2_host(h, half)) for half in (30, 50)},
    "ps2_host_no_device": (("ps2-host",), ps2_host_no_device),
    **{f"onewire_{c}": (("onewire-master",), lambda h, c=c: onewire_rom(h, c))
       for c in ("typical", "early-short", "late-long")},
    "onewire_no_presence": (("onewire-master",), onewire_no_presence),
    "four_engines": (("swd-read", "ws2812", "ps2-device", "onewire-master"), four_engines),
}


def skip_unless(*names: str) -> bool:
    return not all(compatible(n) for n in names)


def dut_harness(dut):  # noqa: ANN201 - harness.CocotbHarness
    from harness import CocotbHarness  # noqa: PLC0415 - needs a simulator
    return CocotbHarness(dut)


# ----------------------------------------------------------------------------
# cocotb tests
# ----------------------------------------------------------------------------

@cocotb.test(skip=skip_unless("swd-read"))
async def test_swd_connect_dpidr(dut):
    """swd-read: JTAG-to-SWD, line reset, DPIDR read, DPIDR and CTRL/STAT reads without reconnecting."""
    await swd_connect_dpidr(dut_harness(dut))


@cocotb.test(skip=skip_unless("swd-read"))
@cocotb.parametrize(delay=[29, 30])
async def test_swd_target_delay(dut, delay):
    """The 30-clock target output budget of pe_timing: a target delay of 29 clocks works, 30 does not."""
    await swd_target_delay(dut_harness(dut), delay)


@cocotb.test(skip=skip_unless("swd-read"))
@cocotb.parametrize(case=["fault", "wait", "parity", "write", "no-connect"])
async def test_swd_errors(dut, case):
    """swd-read error paths: ACK FAULT/WAIT (fault 80), data parity (81), write header (82), no connect (80)."""
    await swd_errors(dut_harness(dut), case)


@cocotb.test(skip=skip_unless("ws2812"))
async def test_ws2812(dut):
    """ws2812: frames decoded with the WS2812B datasheet windows; the V5 windows reject them."""
    await ws2812_classic(dut_harness(dut))


@cocotb.test(skip=skip_unless("ws2812b-v5"))
async def test_ws2812b_v5(dut):
    """ws2812b-v5: frames decoded with the WS2812B-V5 windows; the earlier WS2812B windows reject them."""
    await ws2812_v5(dut_harness(dut))


@cocotb.test(skip=skip_unless("ps2-device"))
async def test_ps2_device(dut):
    """ps2-device: scan-code bytes read by a PS/2 host, frame format and timing checked."""
    await ps2_device(dut_harness(dut))


@cocotb.test(skip=skip_unless("ps2-device"))
@cocotb.parametrize(where=["high-phase", "low-phase", "after-11th"])
async def test_ps2_inhibit(dut, where):
    """ps2-device: the host holds CLK low mid-frame (the frame is sent again) or after the 11th clock (it is not)."""
    await ps2_inhibit(dut_harness(dut), where)


@cocotb.test(skip=skip_unless("ps2-host"))
@cocotb.parametrize(half=[30, 50])
async def test_ps2_host(dut, half):
    """ps2-host: reset, echo and read-ID commands to a PS/2 device clocking at 30 us and 50 us half periods."""
    await ps2_host(dut_harness(dut), half)


# Its path ends in the wait for the first device clock, before the one word a
# byte-lane design faults on, so it runs on every design of the same architecture.
@cocotb.test(skip=not same_architecture("ps2-host"))
async def test_ps2_host_no_device(dut):
    """ps2-host without a device: the wait for the first device clock ends in fault 3 after LIMIT (16 ms)."""
    await ps2_host_no_device(dut_harness(dut))


# It queues one TX word and reads none, so it does not depend on the queue depth
# and also runs on the byte-lane designs with 4- and 2-word queues (diet4, the
# 6x4 build, and diet2).
@cocotb.test(skip=not (same_architecture("ps2-host", queue_depth=False) and restricted_words("ps2-host")))
async def test_ps2_host_restricted(dut):
    """A design with byte-lane shifts (ISA version 3) cannot run ps2-host, so test_ps2_host
    is skipped on it: the image runs up to its SHR by 21 and faults there with code 1
    (docs/isa.md, "ISA version"). Skipped on the design of record."""
    await ps2_host_restricted(dut_harness(dut))


@cocotb.test(skip=skip_unless("onewire-master"))
@cocotb.parametrize(corner=["typical", "early-short", "late-long"])
async def test_onewire_read_rom(dut, corner):
    """onewire-master: reset, presence, Read ROM; ROM and CRC-8 checked; slave timing corners."""
    await onewire_rom(dut_harness(dut), corner)


@cocotb.test(skip=skip_unless("onewire-master"))
async def test_onewire_no_presence(dut):
    """onewire-master: no presence pulse ends in fault 96 with DQ released."""
    await onewire_no_presence(dut_harness(dut))


@cocotb.test(skip=skip_unless("swd-read", "ws2812", "ps2-device", "onewire-master"))
async def test_four_engines(dut):
    """All four images at once (engines 0-3, disjoint pins): every peer passes and the
    WS2812B and PS/2 timing is the same clock count as in the single-engine tests."""
    await four_engines(dut_harness(dut))
