# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Pad-level software protocol peers (MicroPython compatible).

Each peer sees only uio pad levels, never chip state, so the same objects run
on the reference-model backend and on a demo-board RP2 or a Pico wired to the
chip/FPGA, where they drive the RP2's own uio GPIOs in lockstep with the
host-supplied clock. ``step(cycle, pads)`` is called once per cycle with the
pad levels left by the previous edge and returns ``(enable_mask, value_mask)``:
the pins the peer drives and their levels for the coming edge.

UartSource, UartMonitor and SpiTarget follow test/peers.py (same framing and
timing); I2CTarget is a pad-level open-drain target written for hardware
(test/model/scoreboards.py I2CPeer reads the controller's output enables,
which a real RP2 cannot see).
"""


class Peer:
    mask = 0

    def step(self, cycle, pads):
        return 0, 0


class Environment:
    """Several peers on one set of pads; drivers of one pin combine as wired-AND."""

    def __init__(self, peers, pullups=0xFF):
        self.peers = list(peers)
        self.pullups = pullups
        mask = 0
        for peer in self.peers:
            mask |= peer.mask
        self.mask = mask

    def step(self, cycle, pads):
        en = 0
        val = 0xFF
        for peer in self.peers:
            e, v = peer.step(cycle, pads)
            if e:
                en |= e
                val &= v | (~e & 0xFF)
        return en, val & en


class UartSource(Peer):
    """8N1 transmitter on one pad: idle high, LSB first, fixed bit period.

    ``start`` is the absolute cycle of the first start bit (None = idle).
    ``bad_stop`` holds frame indices sent with a low stop bit.
    """

    def __init__(self, pin, words, bit_cycles=64, gap_cycles=64, start=None, bad_stop=()):
        self.pin = pin
        self.mask = 1 << pin
        self.words = list(words)
        self.bit_cycles = bit_cycles
        self.gap = gap_cycles
        self.start = start
        self.bad_stop = tuple(bad_stop)

    @property
    def frame_cycles(self):
        return 10 * self.bit_cycles + self.gap

    def end(self):
        return self.start + len(self.words) * self.frame_cycles

    def level(self, cycle):
        if self.start is None or cycle < self.start:
            return 1
        frame, offset = divmod(cycle - self.start, self.frame_cycles)
        if frame >= len(self.words):
            return 1
        index = offset // self.bit_cycles
        if index == 0:
            return 0
        if index <= 8:
            return (self.words[frame] >> (index - 1)) & 1
        return 0 if index == 9 and frame in self.bad_stop else 1

    def step(self, cycle, pads):
        return self.mask, self.level(cycle) << self.pin


class UartMonitor(Peer):
    """Streaming 8N1 receiver on a pad: falling start edge, mid-bit sampling."""

    def __init__(self, pin, bit_cycles=64):
        self.pin = pin
        self.bit_cycles = bit_cycles
        self.words = []
        self.errors = []
        self._previous = 1
        self._start = None
        self._bits = []

    def sample(self, cycle, level):
        if self._start is None:
            if self._previous and not level:
                self._start = cycle
                self._bits = []
        else:
            offset = cycle - self._start
            if offset % self.bit_cycles == self.bit_cycles // 2:
                self._bits.append(level)
                if len(self._bits) == 10:
                    if self._bits[0] != 0 or self._bits[9] != 1:
                        self.errors.append("framing error in frame starting at cycle %d"
                                           % self._start)
                    word = 0
                    for i in range(8):
                        word |= self._bits[1 + i] << i
                    self.words.append(word)
                    self._start = None
        self._previous = level

    def step(self, cycle, pads):
        self.sample(cycle, (pads >> self.pin) & 1)
        return 0, 0


class SpiTarget(Peer):
    """Mode 0-3 SPI target, MSB first, 8-bit frames framed by active-low CS.

    CPHA0: MISO presents a bit at CS assertion and after every trailing edge;
    MOSI is sampled on leading edges. CPHA1: MISO changes on leading edges and
    MOSI is sampled on trailing edges. Leading = away from the CPOL idle level.
    MISO is always driven (it is the only pin of the four the chip reads).
    """

    def __init__(self, mode, responses, sck=2, mosi=3, miso=4, cs=5):
        self.mode = mode
        self.responses = list(responses)
        self.sck, self.mosi, self.miso, self.cs = sck, mosi, miso, cs
        self.mask = 1 << miso
        self.received = []
        self.violations = []
        self._previous_sck = None
        self._previous_cs = 1
        self._shift_in = 0
        self._bits_in = 0
        self._out = 0
        self._miso = 1
        self._index = 0

    def update(self, pads):
        cpol, cpha = (self.mode >> 1) & 1, self.mode & 1
        cs, sck, mosi = (pads >> self.cs) & 1, (pads >> self.sck) & 1, (pads >> self.mosi) & 1
        if self._previous_cs and not cs:
            self._out = self.responses[self._index % len(self.responses)] & 0xFF
            self._index += 1
            self._shift_in = self._bits_in = 0
            if sck != cpol:
                self.violations.append("SCK not idle at CS assertion")
            if not cpha:
                self._miso = (self._out >> 7) & 1
        elif not self._previous_cs and cs:
            if self._bits_in % 8:
                self.violations.append("CS released after %d bits" % self._bits_in)
        elif not cs and self._previous_sck is not None and sck != self._previous_sck:
            leading = sck != cpol
            if leading != bool(cpha):
                self._shift_in = ((self._shift_in << 1) | mosi) & 0xFF
                self._bits_in += 1
                if self._bits_in % 8 == 0:
                    self.received.append(self._shift_in)
            elif cpha:
                self._miso = (self._out >> 7) & 1
                self._out = (self._out << 1) & 0xFF
            else:
                self._out = (self._out << 1) & 0xFF
                self._miso = (self._out >> 7) & 1
        self._previous_cs, self._previous_sck = cs, sck
        return self._miso

    def step(self, cycle, pads):
        return self.mask, self.update(pads) << self.miso


class I2CTarget(Peer):
    """Open-drain 7-bit I2C target on SCL/SDA pads (external or GPIO pull-ups).

    Receives write bytes (``received`` includes address bytes, like the test/
    I2CPeer), ACKs its address and every data byte except the byte indices in
    ``nack_bytes`` (0 = address byte), serves ``read_bytes`` to read
    transactions and records the controller's ACK/NACK in ``master_acks``.
    It changes SDA only right after an observed SCL falling edge, so its own
    drive can never look like START or STOP. ``stretch_cycles`` > 0 holds SCL
    low for that many cycles after every falling edge (a busy target).
    Drives only low: an enabled pin always has value 0.
    """

    def __init__(self, address=0x42, scl=6, sda=7, read_bytes=(0x5A,), stretch_cycles=0,
                 nack_bytes=()):
        self.address = address
        self.scl, self.sda = scl, sda
        self.mask = (1 << scl) | (1 << sda)
        self.read_bytes = list(read_bytes)
        self.stretch_cycles = stretch_cycles
        self.nack_bytes = tuple(nack_bytes)
        self.received = []
        self.master_acks = []
        self.starts = 0
        self.stops = 0
        self.repeated_starts = 0
        self.violations = []
        self._state = "idle"
        self._scl = None
        self._sda = 1
        self._bits = 0
        self._byte = 0
        self._sda_low = False
        self._stretch = 0
        self._read = False
        self._read_index = 0
        self._tx = 0
        self._ack_pending = False
        self._in_transaction = False
        self._byte_index = 0

    def _begin_byte(self):
        self._bits = 0
        self._byte = 0

    def step(self, cycle, pads):
        scl = (pads >> self.scl) & 1
        sda = (pads >> self.sda) & 1
        if self._scl is None:
            self._scl, self._sda = scl, sda
            return 0, 0
        pscl, psda = self._scl, self._sda
        if pscl and scl and psda != sda:
            if not sda:  # START or repeated START
                if self._in_transaction:
                    self.repeated_starts += 1
                self.starts += 1
                self._in_transaction = True
                self._state = "address"
                self._byte_index = 0
                self._sda_low = False
                self._begin_byte()
            else:        # STOP
                if self._state in ("address", "write") and self._bits not in (0, 1):
                    self.violations.append("STOP inside a byte at cycle %d" % cycle)
                self.stops += 1
                self._in_transaction = False
                self._state = "idle"
                self._sda_low = False
        elif not pscl and scl:  # rising edge: sample
            state = self._state
            if state in ("address", "write"):
                self._byte = ((self._byte << 1) | sda) & 0xFF
                self._bits += 1
            elif state == "read":
                self._bits += 1
            elif state == "master_ack":
                self.master_acks.append(sda == 0)
                self._ack_pending = sda == 0
        elif pscl and not scl:  # falling edge: change our drive
            if self.stretch_cycles:
                self._stretch = self.stretch_cycles
            state = self._state
            if state in ("address", "write") and self._bits == 8:
                byte = self._byte
                self.received.append(byte)
                ack = self._byte_index not in self.nack_bytes
                if state == "address":
                    if byte >> 1 != self.address:
                        ack = False
                    self._read = bool(byte & 1)
                self._byte_index += 1
                if ack:
                    self._sda_low = True
                    self._state = "ack"
                else:
                    self._sda_low = False
                    self._state = "ignore"
            elif state == "ack":
                if self._read:
                    self._state = "read"
                    self._tx = self.read_bytes[self._read_index % len(self.read_bytes)] & 0xFF
                    self._bits = 0
                    self._sda_low = not ((self._tx >> 7) & 1)
                else:
                    self._state = "write"
                    self._sda_low = False
                    self._begin_byte()
            elif state == "read":
                if self._bits < 8:
                    self._sda_low = not ((self._tx >> (7 - self._bits)) & 1)
                else:
                    self._sda_low = False
                    self._state = "master_ack"
            elif state == "master_ack":
                if self._ack_pending:
                    self._read_index += 1
                    self._tx = self.read_bytes[self._read_index % len(self.read_bytes)] & 0xFF
                    self._bits = 0
                    self._state = "read"
                    self._sda_low = not ((self._tx >> 7) & 1)
                else:
                    self._read_index += 1
                    self._state = "wait_stop"
                    self._sda_low = False
        self._scl, self._sda = scl, sda
        en = 0
        if self._sda_low:
            en |= 1 << self.sda
        if self._stretch:
            self._stretch -= 1
            en |= 1 << self.scl
        return en, 0


class PadCapture(Peer):
    """Record observed pad bytes (one byte per cycle) from ``start`` on, for a
    host-side logic-analyzer view; bounded by ``limit`` samples."""

    def __init__(self, limit=16384, start=0):
        self.first = None
        self.start = start
        self.limit = limit
        self.samples = bytearray()

    def step(self, cycle, pads):
        if cycle >= self.start and len(self.samples) < self.limit:
            if self.first is None:
                self.first = cycle
            self.samples.append(pads & 0xFF)
        return 0, 0
