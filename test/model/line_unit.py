# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Line-unit extension of the reference model (docs/extension.md, docs/isa.md
"Line-unit extension"). ``reference.py`` stays verbatim and ``variant.py``
selects this class only for configurations with ``options.line_unit`` set
(default off).

Independence: this model implements the ISA contract, but it was written by
the author of the RTL and follows the structure of the RTL step for step
(``hardcaml/lib/engine.ml`` until 16cfc20, ``engine_line.ml`` after it: ticker,
Manchester second half, boundary and mid-bit actions, stuffing counters). A
misreading of the contract shared by both would pass the lockstep comparison.
The tests' independent checks are the references of ``test/line_support.py``
(published CRC check values, line encoders and decoders, the behavioural CAN
node, USB host and Ethernet decoder) and the ticker timing formula of
``test/line_scenarios.py``.

Per engine the unit adds (feature set ``rec16``):

* a bit ticker: LTIM sets a half-period P (0 stops it), a fraction Q/256 and
  a first-tick delay D. A tick happens in every cycle in which the count is 1;
  the count then reloads with P, plus 1 whenever the 8-bit phase accumulator
  (+Q per tick) carries. Ticks alternate bit boundary and mid-bit, starting
  with a boundary D (or P when D = 0) cycles after LTIM issues. A classic XFER
  stops the ticker (it shares the tick/period registers).
* LCFG: line code (0 NRZ, 1 NRZI, 2 Manchester TX only), bit stuffing (enable,
  either polarity or ones only, run length 1..8), pair (the PINS clock field
  names a pin driven with the complement of the data pin), arbitration
  monitor, end of an RX XFER on SE0 (both pair pins low) and the initial line
  level. It resets the line state and flags, not the ticker or the CRC.
* line-mode XFER (c bit 5): a bits, drive (c3), sample (c4), MSB first (c2),
  CRC feed (c6). Drive happens at boundary ticks, sampling at mid-bit ticks.
  Stuff bits are inserted/removed when the run is reached, also after the last
  data bit (a trailing stuff bit); they are never shifted, counted or fed to
  the CRC.
* CRC (16 bits) with four presets; the XFER bit order selects the reflected
  (LSB first) or the normal left-aligned (MSB first) form of the polynomial.
* LSTAT: status bits of the unit and the two queue flags.
* START and reset clear everything above.

Cycle semantics follow the ISA text: every read in a cycle sees the state
before the edge; the ticker, the autonomous Manchester second half and then
the instruction (or the transfer step) act in that order, and an instruction
that writes pins in the cycle of a Manchester second half writes on top of it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .reference import Engine, Fault, Transfer
from .variant import VariantConfig, VariantReference

LINE_UNITS = ("none", "rec16")

# (name, reflected polynomial, normal polynomial left-aligned in 16 bits)
PRESETS = (
    ("CRC-5/USB", 0x0014, 0x05 << 11),
    ("CRC-16/ARC", 0xA001, 0x8005),
    ("CRC-15/CAN", 0x4CD1, 0x4599 << 1),
    ("CRC-16/CCITT", 0x8408, 0x1021),
)



def capabilities(line_unit: str, engines: int = 4) -> int:
    """READ_SELECT 7 bits 23..8: [0] line unit, [1] fraction, [2] stuffing,
    [3] arbitration monitor, [4] CRC-16, [5] CRC-32, [6] CRC presets,
    [11:8] engines with the unit. rec16: all but CRC-32, on every engine."""
    if line_unit == "none":
        return 0
    return 0x5F | (((1 << engines) - 1) << 8)


def crc_step(crc: int, bit: int, poly: int, msb: bool) -> int:
    """One bit into a 16-bit CRC register (see module docstring)."""
    if msb:
        feedback = (crc >> 15 & 1) ^ bit
        crc = (crc << 1) & 0xFFFF
    else:
        feedback = (crc & 1) ^ bit
        crc >>= 1
    return crc ^ (poly if feedback else 0)


def preset_polynomial(preset: int, msb: bool) -> int:
    _, reflected, normal = PRESETS[preset & 3]
    return normal if msb else reflected


@dataclass
class LineState:
    run: bool = False
    count: int = 0
    period: int = 0
    phase: int = 0  # 0: next tick is a bit boundary
    frac: int = 0
    acc: int = 0
    seen: bool = False
    cfg: int = 0
    level: int = 0
    rx_prev: int = 0
    cell: int = 0
    man: bool = False
    se0: bool = False
    rem: int = 0
    trail: bool = False
    srun: int = 0
    slast: int = 0
    serr: bool = False
    lost: bool = False
    crc: int = 0
    preset: int = 0

    # LCFG fields
    @property
    def code(self) -> int:
        return self.cfg & 3

    @property
    def stuff_en(self) -> bool:
        return bool(self.cfg >> 2 & 1)

    @property
    def stuff_any(self) -> bool:
        return bool(self.cfg >> 3 & 1)

    @property
    def run_n(self) -> int:
        return (self.cfg >> 4 & 7) + 1

    @property
    def pair(self) -> bool:
        return bool(self.cfg >> 7 & 1)

    @property
    def arb(self) -> bool:
        return bool(self.cfg >> 8 & 1)

    @property
    def se0_end(self) -> bool:
        return bool(self.cfg >> 9 & 1)


@dataclass
class LineTransfer:
    """A line-mode XFER in progress (occupies ``Engine.transfer``)."""
    remaining: int
    flags: int

    @property
    def msb(self) -> bool:
        return bool(self.flags & 4)

    @property
    def drive(self) -> bool:
        return bool(self.flags & 8)

    @property
    def sample(self) -> bool:
        return bool(self.flags & 16)

    @property
    def crc(self) -> bool:
        return bool(self.flags & 64)


def line_state(e: Engine) -> LineState:
    state = getattr(e, "line", None)
    if state is None:
        state = LineState()
        e.line = state  # type: ignore[attr-defined]
    return state


class LineReference(VariantReference):
    """``VariantReference`` plus the line unit on every engine."""

    def __init__(self, config: VariantConfig, *, settled: bool = False) -> None:
        if config.options.line_unit not in LINE_UNITS[1:]:
            raise ValueError("LineReference needs options.line_unit")
        if config.width != 32 or not config.fused:
            raise ValueError("the line unit needs a 32-bit datapath and fused issue")
        # Event counts for functional coverage (read by test_line_random.py).
        self.events: Counter[str] = Counter()
        super().__init__(config, settled=settled)

    # ------------------------------------------------------------ status
    def _status(self) -> int:
        if self.read_select == 7:
            return self.options.isa_version | capabilities(self.options.line_unit,
                                                           self.config.engines) << 8
        return super()._status()

    def _start(self, e: Engine) -> None:  # replaces Reference._start (static there)
        Reference_start(e)
        e.line = LineState()  # type: ignore[attr-defined]

    # ------------------------------------------------------------ helpers
    def _finish(self, e: Engine) -> None:
        e.pc += 1
        e.completed = (e.completed + 1) & 0xFFFFFFFF
        e.blocked = 0

    def _write_pair(self, e: Engine, line: LineState, level: int) -> None:
        _, out, _ = e.pins
        clock = e.pins[0]
        self._drive(e, out, level)
        if line.pair:
            self._drive(e, clock, 1 - level)

    def _tx_bit(self, e: Engine, msb: bool) -> int:
        return e.regs[0] >> (self.config.width - 1) & 1 if msb else e.regs[0] & 1

    def _shift_tx(self, e: Engine, msb: bool) -> None:
        e.regs[0] = ((e.regs[0] << 1) if msb else (e.regs[0] >> 1)) & self.mask

    def _sample_rx(self, e: Engine, bit: int, msb: bool) -> None:
        e.regs[1] = ((e.regs[1] << 1 | bit) if msb else
                     (e.regs[1] >> 1 | bit << (self.config.width - 1))) & self.mask

    def _feed(self, line: LineState, bit: int, msb: bool) -> None:
        line.crc = crc_step(line.crc, bit, preset_polynomial(line.preset, msb), msb)

    @staticmethod
    def _run_after(line: LineState, bit: int) -> int:
        if line.stuff_any:
            return (line.srun + 1) & 15 if bit == line.slast else 1
        return (line.srun + 1) & 15 if bit else 0

    @staticmethod
    def _stuff_value(line: LineState) -> int:
        return int(line.stuff_any and not line.slast)

    @staticmethod
    def _run_reset(line: LineState) -> int:
        return 1 if line.stuff_any else 0

    def _count_data(self, e: Engine, line: LineState, t: LineTransfer, run_new: int) -> None:
        remaining = t.remaining - 1
        if remaining == 0:
            if line.stuff_en and run_new == line.run_n:
                t.remaining = 1
                line.trail = True
            else:
                e.transfer = None
                self._finish(e)
        else:
            t.remaining = remaining

    def _end_stuff(self, e: Engine, line: LineState, trail_before: bool) -> None:
        if trail_before:
            e.transfer = None
            line.trail = False
            self._finish(e)

    # ------------------------------------------------------------ engine step
    def _line_opcode(self, e: Engine) -> bool:
        if e.wait or e.transfer is not None or not e.committed or not 0 <= e.pc < len(e.program):
            return False
        return e.program[e.pc] >> 24 in (17, 30, 31, 32, 33)

    def _step(self, e: Engine, tx_available: bool, rx_space: bool) -> tuple[bool, bool, int]:
        e.stalled = False
        if not e.running:
            return False, False, 0
        line = line_state(e)
        level_before = line.level
        # Ticker (state before the edge decides the tick).
        tick = line.run and line.count == 1
        boundary = tick and line.phase == 0
        middle = tick and line.phase == 1
        if line.run:
            if line.count <= 1:
                total = line.acc + line.frac
                line.count = (line.period + (total >> 8)) & 0xFF
                line.phase ^= 1
                line.acc = total & 0xFF
            else:
                line.count -= 1
        # Manchester second half.
        if middle and line.man:
            self.events["manchester second half"] += 1
            line.level = line.cell
            self._write_pair(e, line, line.cell)
            line.man = False
        t = e.transfer
        if isinstance(t, LineTransfer) and not e.wait:
            self._line_transfer(e, line, t, boundary, middle)
            return False, False, 0
        if isinstance(t, Transfer) and not e.wait:
            self._classic_transfer(e, line, t)
            return False, False, 0
        if not self._line_opcode(e):
            return super()._step(e, tx_available, rx_space)
        return self._line_instruction(e, line, level_before, tx_available, rx_space)

    def _line_instruction(self, e: Engine, line: LineState, level_before: int,
                          tx_available: bool, rx_space: bool) -> tuple[bool, bool, int]:
        word = e.program[e.pc]
        op, a, b, c = word >> 24, word >> 16 & 255, word >> 8 & 255, word & 255
        imm24 = word & 0xFFFFFF
        fault_before = e.fault
        result = self._line_instruction_body(e, line, level_before, tx_available, rx_space, op, a, b, c, imm24)
        name = {17: "XFER line" if c & 0x20 else "XFER classic", 30: "LTIM", 31: "LCFG", 32: "CRC",
                33: "LSTAT"}[op]
        self.events[f"{name} {'fault' if e.fault and not fault_before else 'issued'}"] += 1
        return result

    def _line_instruction_body(self, e: Engine, line: LineState, level_before: int, tx_available: bool,
                               rx_space: bool, op: int, a: int, b: int, c: int,
                               imm24: int) -> tuple[bool, bool, int]:
        if op == 17:
            return self._xfer(e, line, a, b, c)
        if op == 30:
            period, frac, delay = imm24 & 255, imm24 >> 8 & 255, imm24 >> 16
            if period == 255 and frac:
                self._fail(e, Fault.INVALID_OPERAND)
                return False, False, 0
            line.period = period
            line.count = delay if delay else period
            line.frac = frac
            line.run = period != 0
            line.acc = 0
            line.phase = 0
        elif op == 31:
            # reserved bits, line code 3, or stuffing on either polarity with run length 1
            if imm24 >> 11 or imm24 & 3 == 3 or imm24 & 0x7C == 0x0C:
                self._fail(e, Fault.INVALID_OPERAND)
                return False, False, 0
            init = imm24 >> 10 & 1
            line.cfg = imm24 & 0x3FF
            line.level = line.rx_prev = line.slast = init
            line.srun = line.rem = line.cell = 0
            line.se0 = line.serr = line.lost = line.man = False
        elif op == 32:
            ok = ((c == 1 and a == 0 and b < 4) or (c == 2 and a < 4 and b == 0)
                  or (c == 3 and a == 0 and b < 4))
            if not ok:
                self._fail(e, Fault.INVALID_OPERAND)
                return False, False, 0
            if c == 1:
                line.crc = e.regs[b] & 0xFFFF
            elif c == 2:
                e.regs[a] = line.crc
            else:
                line.preset = b
        else:  # 33 LSTAT
            if a > 3 or b or c:
                self._fail(e, Fault.INVALID_OPERAND)
                return False, False, 0
            e.regs[a] = (int(line.se0) | int(line.lost) << 1 | int(line.serr) << 2
                         | int(tx_available) << 3 | int(rx_space) << 4 | level_before << 5
                         | int(line.run) << 6 | (line.rem & 63) << 8)
        self._finish(e)
        return False, False, 0

    def _xfer(self, e: Engine, line: LineState, a: int, b: int, c: int) -> tuple[bool, bool, int]:
        clock, tx, _ = e.pins
        width = self.config.width
        if c & 0x20:  # line mode
            drive, sample = bool(c & 8), bool(c & 16)
            valid = (not c & 0x80 and 1 <= a <= width and b == 0 and not c & 3 and line.run
                     and not (line.code == 2 and sample)
                     and (not drive or e.ownership >> tx & 1)
                     and (not (drive and line.pair) or (e.ownership >> clock & 1 and clock != tx)))
            if not valid:
                self._fail(e, Fault.INVALID_OPERAND)
                return False, False, 0
            e.transfer = LineTransfer(a, c & 0x7F)
            line.seen = False
            line.trail = False
            return False, False, 0
        # classic XFER; bit 6 feeds the CRC
        if c & 0x80 or not 1 <= a <= width or b == 0:
            self._fail(e, Fault.INVALID_OPERAND)
            return False, False, 0
        required = (1 << clock) | ((1 << tx) if c & 8 else 0)
        if required & ~e.ownership or (c & 8 and clock == tx):
            self._fail(e, Fault.PIN_OWNERSHIP)
            return False, False, 0
        self._drive(e, clock, c & 1)
        if c & 8 and not c & 2:
            self._drive(e, tx, self._tx_bit(e, bool(c & 4)))
        e.transfer = Transfer(a, b, c, b)
        line.run = False  # the classic XFER takes over the shared tick registers
        return False, False, 0

    def _classic_transfer(self, e: Engine, line: LineState, t: Transfer) -> None:
        """Reference's classic XFER edge sequence plus the CRC feed (c bit 6)."""
        t.remaining -= 1
        if t.remaining:
            return
        clock, tx, rx = e.pins
        active = t.transitions % 2 == 0
        cpol, cpha, msb, drive, sample = (bool(t.flags & (1 << i)) for i in range(5))
        feed = bool(t.flags & 64)
        self._drive(e, clock, int(cpol != active))
        shifting = drive and (active if cpha else not active)
        if shifting and feed and not sample:
            self._feed(line, self._tx_bit(e, msb), msb)
        if drive and cpha and active:
            self._shift_out(e, tx, msb)
        if drive and not cpha and not active:
            e.regs[0] = ((e.regs[0] << 1) if msb else (e.regs[0] >> 1)) & self.mask
            if t.transitions < 2 * t.bits - 1:
                self._drive(e, tx, e.regs[0] >> (self.config.width - 1) if msb else e.regs[0])
        if sample and (not active if cpha else active):
            bit = self.sync2 >> rx & 1
            self._shift_in(e, rx, msb)
            if feed:
                self._feed(line, bit, msb)
                self.events["classic CRC feed"] += 1
        t.transitions += 1
        if t.transitions == 2 * t.bits:
            e.transfer = None
            self._finish(e)
        else:
            t.remaining = t.half_period

    def _line_transfer(self, e: Engine, line: LineState, t: LineTransfer, boundary: bool,
                       middle: bool) -> None:
        clock, _, rx = e.pins
        trail_before = line.trail
        if boundary and t.drive:
            is_stuff = line.stuff_en and line.srun == line.run_n
            data = 1 if line.lost else self._tx_bit(e, t.msb)
            bit = 1 if line.lost else (self._stuff_value(line) if is_stuff else data)
            code = line.code
            encoded = (bit, line.level if bit else 1 - line.level, 1 - bit, bit)[code]
            line.level = encoded
            self._write_pair(e, line, encoded)
            line.man = code == 2
            line.cell = bit
            line.seen = True
            if not is_stuff:
                self._shift_tx(e, t.msb)
            if t.drive and not t.sample:
                if is_stuff:
                    self.events["stuff bit sent" + (" (trailing)" if trail_before else "")] += 1
                    value = self._stuff_value(line)
                    line.srun = self._run_reset(line)
                    line.slast = value
                    self._end_stuff(e, line, trail_before)
                else:
                    if t.crc:
                        self._feed(line, data, t.msb)
                    run_new = self._run_after(line, data)
                    line.srun = run_new
                    line.slast = data
                    self._count_data(e, line, t, run_new)
            return
        if middle and t.sample and (not t.drive or line.seen):
            raw = self.sync2 >> rx & 1
            pair_raw = self.sync2 >> clock & 1
            if line.se0_end and line.pair and not raw and not pair_raw:
                self.events["SE0 end"] += 1
                line.se0 = True
                line.rem = t.remaining & 63
                line.trail = False
                e.transfer = None
                self._finish(e)
                return
            previous = line.rx_prev
            line.rx_prev = raw
            if line.arb and t.drive and line.cell and not raw:
                self.events["arbitration lost"] += 1
                line.lost = True
            decoded = (1 - (raw ^ previous)) if line.code == 1 else raw
            if line.stuff_en and line.srun == line.run_n:
                self.events["stuff bit removed"] += 1
                if decoded != self._stuff_value(line):
                    self.events["stuff error"] += 1
                    line.serr = True
                line.srun = self._run_reset(line)
                line.slast = decoded
                self._end_stuff(e, line, trail_before)
            else:
                self._sample_rx(e, decoded, t.msb)
                if t.crc:
                    self._feed(line, decoded, t.msb)
                run_new = self._run_after(line, decoded)
                line.srun = run_new
                line.slast = decoded
                self._count_data(e, line, t, run_new)


def Reference_start(e: Engine) -> None:
    """``Reference._start`` (a staticmethod there), called by name."""
    from .reference import Reference
    Reference._start(e)
