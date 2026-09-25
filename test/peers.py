# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Behavioral protocol peers that only see pad levels (never processor state).

Each peer is advanced exactly once per clock by a pin supplier, from the DUT's
actual uio_out/uio_oe of the previous edge, and returns the levels it drives.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from model.reference import Outputs
from model.scoreboards import I2CPeer, ProtocolViolation


def bit(value: int, pin: int) -> int:
    return value >> pin & 1


class UartSource:
    """8N1 transmitter driving one pad: idle high, LSB first, fixed bit period."""

    def __init__(self, words: Sequence[int], *, bit_cycles: int = 64, gap_cycles: int = 64,
                 start: int | None = None, bad_stop: Sequence[int] = ()) -> None:
        self.words = list(words)
        self.bad_stop = set(bad_stop)  # frame indices sent with a low stop bit
        self.bit_cycles, self.gap = bit_cycles, gap_cycles
        self.start = start  # absolute cycle of the first start bit; None = idle

    @property
    def frame_cycles(self) -> int:
        return 10 * self.bit_cycles + self.gap

    def end(self) -> int:
        assert self.start is not None
        return self.start + len(self.words) * self.frame_cycles

    def level(self, cycle: int) -> int:
        if self.start is None or cycle < self.start:
            return 1
        frame, offset = divmod(cycle - self.start, self.frame_cycles)
        if frame >= len(self.words):
            return 1
        index = offset // self.bit_cycles
        if index == 0:
            return 0
        if index <= 8:
            return self.words[frame] >> (index - 1) & 1
        return 0 if index == 9 and frame in self.bad_stop else 1


class UartMonitor:
    """Streaming 8N1 receiver on a pad: falling start edge, mid-bit sampling."""

    def __init__(self, *, bit_cycles: int = 64) -> None:
        self.bit_cycles = bit_cycles
        self.words: list[int] = []
        self.errors: list[str] = []
        self._previous = 1
        self._start: int | None = None
        self._bits: list[int] = []

    def sample(self, cycle: int, level: int) -> None:
        if self._start is None:
            if self._previous and not level:
                self._start, self._bits = cycle, []
        else:
            offset = cycle - self._start
            if offset % self.bit_cycles == self.bit_cycles // 2:
                self._bits.append(level)
                if len(self._bits) == 10:
                    if self._bits[0] != 0 or self._bits[9] != 1:
                        self.errors.append(f"framing error in frame starting at cycle {self._start}")
                    self.words.append(sum(b << i for i, b in enumerate(self._bits[1:9])))
                    self._start = None
        self._previous = level


@dataclass
class SpiTarget:
    """Mode 0-3 SPI target, MSB first, 8-bit frames framed by active-low CS.

    CPHA0: MISO presents a bit at CS assertion and after every trailing edge;
    MOSI is sampled on leading edges. CPHA1: MISO changes on leading edges and
    MOSI is sampled on trailing edges. Leading = away from the CPOL idle level.
    """

    mode: int
    responses: Sequence[int]
    sck: int = 2
    mosi: int = 3
    miso: int = 4
    cs: int = 5
    received: list[int] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    _previous_sck: int | None = None
    _previous_cs: int = 1
    _shift_in: int = 0
    _bits_in: int = 0
    _out: int = 0
    _miso: int = 1
    _index: int = 0

    def update(self, pads: int) -> int:
        cpol, cpha = self.mode >> 1 & 1, self.mode & 1
        cs, sck, mosi = bit(pads, self.cs), bit(pads, self.sck), bit(pads, self.mosi)
        if self._previous_cs and not cs:  # frame start
            self._out = self.responses[self._index % len(self.responses)] & 0xFF
            self._index += 1
            self._shift_in = self._bits_in = 0
            if sck != cpol:
                self.violations.append("SCK not idle at CS assertion")
            if not cpha:
                self._miso = self._out >> 7 & 1
        elif not self._previous_cs and cs:  # frame end
            if self._bits_in % 8:
                self.violations.append(f"CS released after {self._bits_in} bits")
        elif not cs and self._previous_sck is not None and sck != self._previous_sck:
            leading = sck != cpol
            if leading != bool(cpha):  # sample edge
                self._shift_in = (self._shift_in << 1 | mosi) & 0xFF
                self._bits_in += 1
                if self._bits_in % 8 == 0:
                    self.received.append(self._shift_in)
            elif cpha:  # CPHA1 leading edge: present next bit
                self._miso = self._out >> 7 & 1
                self._out = self._out << 1 & 0xFF
            else:  # CPHA0 trailing edge: advance to next bit
                self._out = self._out << 1 & 0xFF
                self._miso = self._out >> 7 & 1
        self._previous_cs, self._previous_sck = cs, sck
        return self._miso


class OpenDrainI2CBus:
    """Wired-AND I2C bus with pull-ups on SCL/SDA and one target peer.

    A pad is low when any driver pulls it low: the DUT pulls low where
    ``uio_oe & ~uio_out`` and the target pulls through the monorepo I2CPeer.
    Actively driving a bus pin high is a protocol violation.
    """

    def __init__(self, peer: I2CPeer) -> None:
        self.peer = peer
        self.mask = 1 << peer.scl | 1 << peer.sda

    def resolve(self, out: Outputs) -> int:
        if out.uio_out & out.uio_oe & self.mask:
            raise ProtocolViolation("DUT actively drove an open-drain I2C line high")
        dut_pull_low = out.uio_oe & ~out.uio_out & self.mask
        # I2CPeer models the target and the pull-ups from the controller's
        # low-drive enables; pass exactly the DUT's pull-downs.
        pads = self.peer.resolve(0, dut_pull_low)
        assert not (pads & dut_pull_low), "bus high while the DUT pulls it low"
        return pads
