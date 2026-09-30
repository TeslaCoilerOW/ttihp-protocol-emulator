# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Standards-based reference for the line unit's wire-level behaviour.

This module is written from the implementation contract (``docs/isa.md``,
section "Line-unit extension", and the base-ISA terms it uses) and from public
standards only.  It does not import, and was not derived from, the Hardcaml
RTL, the lockstep model (``test/model/line_unit.py``) or the formal reference.
Its purpose is to be a second, independent oracle for the pads of the
``diet8_rec16`` variant.

Sources
-------
* CRC: the Williams ("Rocksoft") parameter model and the check and residue
  values of the reveng CRC catalogue (CRC-5/USB, CRC-15/CAN, the CRC-16
  algorithms with polynomial 0x8005 and 0x1021, CRC-32/ISO-HDLC and three
  more CRC-32 variants).  The CRC is computed by polynomial division over
  GF(2) (``poly_mod``), not by a shift register; the contract's bit-serial
  update rule is implemented separately (``unit_crc_step``) only so that the
  tests can compare the two.
* USB 2.0: NRZI (section 7.1.8: a 0 is a change of level, a 1 keeps the
  level), bit stuffing (7.1.9: a 0 after six consecutive 1s, counted from the
  SYNC field and also before the EOP), SYNC = KJKJKJKK, EOP = SE0 then J;
  "Cyclic Redundancy Checks in USB" (USB-IF, Draft 1) for token and data CRCs.
* CAN: Bosch CAN Specification 2.0 part A and ISO 11898-1: NRZ, a
  complementary stuff bit after five equal bits from SOF to the end of the CRC
  sequence (a stuff bit starts the next run), CRC-15 x^15+x^14+x^10+x^8+x^7+
  x^4+x^3+1 over the destuffed SOF..data bits, arbitration by wired AND.
* IEEE 802.3 clause 7.3.1.1 (Manchester, used by clause 14 10BASE-T): the
  first half of a bit cell carries the complement of the bit, the second half
  the bit itself; preamble and SFD are 7 x 0x55 and 0xD5 sent LSB first; the
  FCS is CRC-32/ISO-HDLC.

Time convention
---------------
Cycle ``n`` is the clock period that ends with rising edge ``n``.  An
instruction "issued in cycle c" commits at the edge that ends cycle c.  A pin
value written by an instruction or by a line-unit tick in cycle ``t`` is on the
pad from cycle ``t + OUT_LATENCY``.  A pad level held during cycle ``n`` is seen
by an instruction or a tick in cycle ``n + IN_LATENCY`` (two synchronizer
flip-flops, base ISA "Machine" section).  Both constants are base-ISA facts;
the cocotb module measures them with base-ISA instructions (SET, WAITPIN).

Interpretation choices
----------------------
Where the contract leaves a detail open, the choice is a named field of
``Interpretation`` and the default is the reading that the published
standards imply.  ``test/test_line_stdref.py`` reports which choices the RTL
matches; the list of open points is ``Interpretation.__doc__``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

OUT_LATENCY = 1
IN_LATENCY = 2
WORD_BITS = 32
WORD_MASK = (1 << WORD_BITS) - 1


# ---------------------------------------------------------------------------
# GF(2) polynomial arithmetic and the CRC by definition
# ---------------------------------------------------------------------------

def poly_from_exponents(exponents: Iterable[int]) -> int:
    """Coefficient vector of sum(x^e): bit e is the coefficient of x^e."""
    value = 0
    for exponent in exponents:
        value ^= 1 << exponent
    return value


def poly_mod(dividend: int, divisor: int) -> int:
    """Remainder of dividend(x) / divisor(x) over GF(2) (long division)."""
    if divisor <= 0:
        raise ValueError("divisor must be a nonzero polynomial")
    degree = divisor.bit_length() - 1
    while dividend and dividend.bit_length() - 1 >= degree:
        dividend ^= divisor << (dividend.bit_length() - 1 - degree)
    return dividend


def reflect(value: int, width: int) -> int:
    """Reverse the order of the low ``width`` bits of ``value``."""
    out = 0
    for i in range(width):
        if (value >> i) & 1:
            out |= 1 << (width - 1 - i)
    return out


def bits_to_poly(bits: Sequence[int]) -> int:
    """M(x) for a bit sequence: the first bit is the highest coefficient."""
    value = 0
    for bit in bits:
        value = (value << 1) | (bit & 1)
    return value


def crc_register(bits: Sequence[int], width: int, poly: int, init: int) -> int:
    """Register of the direct (MSB-first) CRC after ``bits``, by division.

    The value is (init * x^n + M(x) * x^width) mod G(x) with
    G(x) = x^width + poly and n = len(bits).  This is the register that a
    shift register with feedback = register MSB xor bit holds, but it is
    computed here by long division.
    """
    generator = (1 << width) | poly
    n = len(bits)
    return poly_mod((init << n) ^ (bits_to_poly(bits) << width), generator)


def bytes_to_bits(data: bytes, lsb_first: bool) -> list[int]:
    """Serialize bytes in transmission order (LSB first when reflected)."""
    out: list[int] = []
    for byte in data:
        order = range(8) if lsb_first else range(7, -1, -1)
        out.extend((byte >> i) & 1 for i in order)
    return out


def word_to_bits(value: int, count: int, msb_first: bool, width: int = WORD_BITS) -> list[int]:
    """The ``count`` bits a shifting XFER sends from ``value`` (``width`` bits)."""
    if msb_first:
        return [(value >> (width - 1 - i)) & 1 for i in range(count)]
    return [(value >> i) & 1 for i in range(count)]


def int_to_bits(value: int, count: int, lsb_first: bool) -> list[int]:
    """A ``count``-bit field in transmission order."""
    if lsb_first:
        return [(value >> i) & 1 for i in range(count)]
    return [(value >> (count - 1 - i)) & 1 for i in range(count)]


def bits_to_int(bits: Sequence[int], lsb_first: bool) -> int:
    """Inverse of ``int_to_bits``."""
    value = 0
    if lsb_first:
        for i, bit in enumerate(bits):
            value |= (bit & 1) << i
    else:
        for bit in bits:
            value = (value << 1) | (bit & 1)
    return value


@dataclass(frozen=True)
class CrcAlgorithm:
    """A CRC in the Williams parameter model (reveng catalogue fields)."""

    name: str
    width: int
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int
    check: int
    residue: int

    def message_bits(self, data: bytes) -> list[int]:
        return bytes_to_bits(data, lsb_first=self.refin)

    def register(self, bits: Sequence[int]) -> int:
        return crc_register(bits, self.width, self.poly, self.init)

    def compute_bits(self, bits: Sequence[int]) -> int:
        reg = self.register(bits)
        if self.refout:
            reg = reflect(reg, self.width)
        return reg ^ self.xorout

    def compute(self, data: bytes) -> int:
        return self.compute_bits(self.message_bits(data))

    def crc_field_bits(self, crc: int) -> list[int]:
        """The CRC field in transmission order (LSB first when reflected)."""
        return int_to_bits(crc, self.width, lsb_first=self.refout)

    def residue_of(self, codeword_bits: Sequence[int]) -> int:
        """Catalogue residue: register after a codeword, reflected if refout."""
        reg = self.register(codeword_bits)
        return reflect(reg, self.width) if self.refout else reg


def _alg(line: str) -> CrcAlgorithm:
    fields = dict(item.split("=", 1) for item in line.split())
    return CrcAlgorithm(
        name=fields["name"].strip('"'),
        width=int(fields["width"]),
        poly=int(fields["poly"], 16),
        init=int(fields["init"], 16),
        refin=fields["refin"] == "true",
        refout=fields["refout"] == "true",
        xorout=int(fields["xorout"], 16),
        check=int(fields["check"], 16),
        residue=int(fields["residue"], 16),
    )


# Parameter lines copied from the reveng CRC catalogue
# (https://reveng.sourceforge.io/crc-catalogue/), check = CRC of b"123456789".
CATALOGUE: tuple[CrcAlgorithm, ...] = tuple(_alg(line) for line in (
    'width=5 poly=0x05 init=0x1f refin=true refout=true xorout=0x1f check=0x19 residue=0x06 name="CRC-5/USB"',
    'width=15 poly=0x4599 init=0x0000 refin=false refout=false xorout=0x0000 check=0x059e residue=0x0000 name="CRC-15/CAN"',
    'width=16 poly=0x8005 init=0x0000 refin=true refout=true xorout=0x0000 check=0xbb3d residue=0x0000 name="CRC-16/ARC"',
    'width=16 poly=0x8005 init=0xffff refin=false refout=false xorout=0x0000 check=0xaee7 residue=0x0000 name="CRC-16/CMS"',
    'width=16 poly=0x8005 init=0x800d refin=false refout=false xorout=0x0000 check=0x9ecf residue=0x0000 name="CRC-16/DDS-110"',
    'width=16 poly=0x8005 init=0xffff refin=true refout=true xorout=0x0000 check=0x4b37 residue=0x0000 name="CRC-16/MODBUS"',
    'width=16 poly=0x8005 init=0x0000 refin=true refout=true xorout=0xffff check=0x44c2 residue=0xb001 name="CRC-16/MAXIM-DOW"',
    'width=16 poly=0x8005 init=0x0000 refin=false refout=false xorout=0x0000 check=0xfee8 residue=0x0000 name="CRC-16/UMTS"',
    'width=16 poly=0x8005 init=0xffff refin=true refout=true xorout=0xffff check=0xb4c8 residue=0xb001 name="CRC-16/USB"',
    'width=16 poly=0x1021 init=0xffff refin=false refout=false xorout=0xffff check=0xd64e residue=0x1d0f name="CRC-16/GENIBUS"',
    'width=16 poly=0x1021 init=0x0000 refin=false refout=false xorout=0xffff check=0xce3c residue=0x1d0f name="CRC-16/GSM"',
    'width=16 poly=0x1021 init=0xffff refin=false refout=false xorout=0x0000 check=0x29b1 residue=0x0000 name="CRC-16/IBM-3740"',
    'width=16 poly=0x1021 init=0xffff refin=true refout=true xorout=0xffff check=0x906e residue=0xf0b8 name="CRC-16/IBM-SDLC"',
    'width=16 poly=0x1021 init=0xc6c6 refin=true refout=true xorout=0x0000 check=0xbf05 residue=0x0000 name="CRC-16/ISO-IEC-14443-3-A"',
    'width=16 poly=0x1021 init=0x0000 refin=true refout=true xorout=0x0000 check=0x2189 residue=0x0000 name="CRC-16/KERMIT"',
    'width=16 poly=0x1021 init=0xffff refin=true refout=true xorout=0x0000 check=0x6f91 residue=0x0000 name="CRC-16/MCRF4XX"',
    'width=16 poly=0x1021 init=0xb2aa refin=true refout=true xorout=0x0000 check=0x63d0 residue=0x0000 name="CRC-16/RIELLO"',
    'width=16 poly=0x1021 init=0x1d0f refin=false refout=false xorout=0x0000 check=0xe5cc residue=0x0000 name="CRC-16/SPI-FUJITSU"',
    'width=16 poly=0x1021 init=0x89ec refin=true refout=true xorout=0x0000 check=0x26b1 residue=0x0000 name="CRC-16/TMS37157"',
    'width=16 poly=0x1021 init=0x0000 refin=false refout=false xorout=0x0000 check=0x31c3 residue=0x0000 name="CRC-16/XMODEM"',
    'width=32 poly=0x04c11db7 init=0xffffffff refin=false refout=false xorout=0xffffffff check=0xfc891918 residue=0xc704dd7b name="CRC-32/BZIP2"',
    'width=32 poly=0x04c11db7 init=0x00000000 refin=false refout=false xorout=0xffffffff check=0x765e7680 residue=0xc704dd7b name="CRC-32/CKSUM"',
    'width=32 poly=0x04c11db7 init=0xffffffff refin=true refout=true xorout=0xffffffff check=0xcbf43926 residue=0xdebb20e3 name="CRC-32/ISO-HDLC"',
    'width=32 poly=0x04c11db7 init=0xffffffff refin=false refout=false xorout=0x00000000 check=0x0376e6e7 residue=0x00000000 name="CRC-32/MPEG-2"',
))

CATALOGUE_BY_NAME = {alg.name: alg for alg in CATALOGUE}
CRC32_ETHERNET = CATALOGUE_BY_NAME["CRC-32/ISO-HDLC"]
CRC5_USB = CATALOGUE_BY_NAME["CRC-5/USB"]
CRC16_USB = CATALOGUE_BY_NAME["CRC-16/USB"]
CRC15_CAN = CATALOGUE_BY_NAME["CRC-15/CAN"]


# ---------------------------------------------------------------------------
# The unit's 16-bit CRC register and its four presets
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnitPreset:
    """A CRC preset: a generator polynomial given by its exponents."""

    number: int
    name: str
    exponents: tuple[int, ...]

    @property
    def width(self) -> int:
        return max(self.exponents)

    @property
    def poly(self) -> int:
        """Catalogue ``poly``: the generator without its x^width term."""
        return poly_from_exponents(self.exponents) ^ (1 << self.width)

    @property
    def normal16(self) -> int:
        """The poly left-aligned in 16 bits (the MSB-first constant)."""
        return (self.poly << (16 - self.width)) & 0xFFFF

    @property
    def reflected16(self) -> int:
        """The LSB-first constant: the 16-bit mirror of ``normal16``."""
        return reflect(self.normal16, 16)

    @property
    def generator16(self) -> int:
        """x^16 + normal16 = x^(16 - width) * G(x): the register's divisor."""
        return (1 << 16) | self.normal16


# Generator polynomials: USB 2.0 8.3.5.1 (x^5+x^2+1), USB 2.0 8.3.5.2 and
# CRC-16/ARC (x^16+x^15+x^2+1), Bosch CAN 2.0 part A 3.1.1 (CRC-15), ITU-T
# V.41 / X.25 (x^16+x^12+x^5+1).
UNIT_PRESETS: tuple[UnitPreset, ...] = (
    UnitPreset(0, "CRC-5/USB", (5, 2, 0)),
    UnitPreset(1, "CRC-16 (0x8005)", (16, 15, 2, 0)),
    UnitPreset(2, "CRC-15/CAN", (15, 14, 10, 8, 7, 4, 3, 0)),
    UnitPreset(3, "CRC-16/CCITT (0x1021)", (16, 12, 5, 0)),
)

# The constants that docs/isa.md states for the presets (reflected, normal).
CONTRACT_PRESET_CONSTANTS = {0: (0x0014, 0x2800), 1: (0xA001, 0x8005),
                             2: (0x4CD1, 0x8B32), 3: (0x8408, 0x1021)}


def unit_crc(reg: int, bits: Sequence[int], preset: int, msb_first: bool) -> int:
    """The unit's CRC register after feeding ``bits``, by polynomial division.

    MSB first the register is the direct CRC of width 16 with divisor
    x^16 + normal16.  LSB first the register is the bit mirror of that CRC,
    started from the mirror of ``reg`` (a reflected register holds the mirror
    of the direct one and consumes the bits in the same order).
    """
    p = UNIT_PRESETS[preset]
    reg &= 0xFFFF
    if not bits:
        return reg
    if msb_first:
        return crc_register(bits, 16, p.normal16, reg)
    return reflect(crc_register(bits, 16, p.normal16, reflect(reg, 16)), 16)


def unit_crc_step(reg: int, bit: int, preset: int, msb_first: bool) -> int:
    """One step of the contract's bit-serial update (docs/isa.md, CRC).

    Used only to compare the contract's rule with ``unit_crc``.
    """
    refl, norm = CONTRACT_PRESET_CONSTANTS[preset]
    if msb_first:
        feedback = ((reg >> 15) & 1) ^ bit
        reg = (reg << 1) & 0xFFFF
        return reg ^ (norm if feedback else 0)
    feedback = (reg & 1) ^ bit
    reg >>= 1
    return reg ^ (refl if feedback else 0)


def unit_emulates(alg: CrcAlgorithm) -> Optional[tuple[int, bool, int]]:
    """How the unit computes a catalogue algorithm: (preset, msb_first, reg0).

    Returns None when no preset has the algorithm's polynomial or the input
    and output reflection differ.  The result is read back with
    ``unit_result``.
    """
    if alg.refin != alg.refout:
        return None
    for p in UNIT_PRESETS:
        if p.width == alg.width and p.poly == alg.poly:
            shift = 16 - alg.width
            if alg.refin:
                return p.number, False, reflect(alg.init << shift, 16)
            return p.number, True, (alg.init << shift) & 0xFFFF
    return None


def unit_result(alg: CrcAlgorithm, reg: int) -> int:
    """The catalogue CRC value from a unit register (before xorout)."""
    shift = 16 - alg.width
    if alg.refin:
        value = reflect(reg, 16) >> shift
        return reflect(value, alg.width) ^ alg.xorout
    return (reg >> shift) ^ alg.xorout


# ---------------------------------------------------------------------------
# Line codes
# ---------------------------------------------------------------------------

NRZ, NRZI, MANCHESTER = 0, 1, 2


def nrzi_encode(bits: Sequence[int], level: int) -> list[int]:
    """USB 2.0 7.1.8: a 0 changes the level, a 1 keeps it."""
    out = []
    for bit in bits:
        if not bit:
            level ^= 1
        out.append(level)
    return out


def nrzi_decode(levels: Sequence[int], previous: int) -> list[int]:
    """Inverse of ``nrzi_encode``: a 1 when the level equals the previous."""
    out = []
    for level in levels:
        out.append(1 if level == previous else 0)
        previous = level
    return out


def manchester_encode(bits: Sequence[int]) -> list[tuple[int, int]]:
    """IEEE 802.3 7.3.1.1: (first half, second half) = (not bit, bit)."""
    return [(bit ^ 1, bit) for bit in bits]


def manchester_decode(halves: Sequence[tuple[int, int]]) -> tuple[list[int], list[int]]:
    """Bits from half-cell pairs; the second list holds cells with no transition."""
    bits, violations = [], []
    for index, (first, second) in enumerate(halves):
        if first == second:
            violations.append(index)
        bits.append(second)
    return bits, violations


# ---------------------------------------------------------------------------
# Bit stuffing (USB 2.0 7.1.9, Bosch CAN 2.0 part A section 5)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StuffRule:
    """``run_length`` equal line bits (``either``) or 1s are followed by a stuff bit."""

    run_length: int
    either: bool

    def due(self, state: "RunState") -> bool:
        return state.run >= self.run_length

    def value(self, state: "RunState") -> int:
        if self.either:
            return 1 - (state.last if state.last is not None else 0)
        return 0

    def after_data(self, state: "RunState", bit: int) -> "RunState":
        if self.either:
            if state.last is not None and bit == state.last:
                return RunState(bit, state.run + 1)
            return RunState(bit, 1)
        return RunState(bit, state.run + 1 if bit else 0)

    def after_stuff(self, value: int) -> "RunState":
        """State after a correct stuff bit: it starts the next run."""
        if self.either:
            return RunState(value, 1)
        return RunState(0, 0)


USB_STUFFING = StuffRule(run_length=6, either=False)
CAN_STUFFING = StuffRule(run_length=5, either=True)


@dataclass(frozen=True)
class RunState:
    last: Optional[int] = None
    run: int = 0


def stuff(bits: Sequence[int], rule: StuffRule, state: RunState = RunState(),
          trailing: bool = True) -> tuple[list[tuple[int, bool]], RunState]:
    """Line cells (bit, is_stuff) for ``bits``; ``trailing`` adds a final stuff bit."""
    cells: list[tuple[int, bool]] = []
    for bit in bits:
        if rule.due(state):
            value = rule.value(state)
            cells.append((value, True))
            state = rule.after_stuff(value)
        cells.append((bit, False))
        state = rule.after_data(state, bit)
    if trailing and rule.due(state):
        value = rule.value(state)
        cells.append((value, True))
        state = rule.after_stuff(value)
    return cells, state


@dataclass
class DestuffResult:
    data: list[int]
    stuff_errors: list[int]           # cell indexes of wrong stuff bits
    stuff_cells: list[int]            # cell indexes of stuff bits
    state: RunState
    consumed: int                     # cells consumed


def destuff(cells: Sequence[int], rule: StuffRule, state: RunState = RunState(),
            count: Optional[int] = None, trailing: bool = True,
            on_error: str = "as_expected") -> DestuffResult:
    """Receive side: drop stuff cells, flag wrong ones.

    With ``count`` it stops after that many data bits (plus the trailing stuff
    cell when one is due and ``trailing``).  ``on_error`` selects how a wrong
    stuff bit updates the run state (see ``Interpretation.stuff_error_state``).
    """
    data: list[int] = []
    errors: list[int] = []
    stuffed: list[int] = []
    index = 0
    while index < len(cells):
        if count is not None and len(data) >= count and not (trailing and rule.due(state)):
            break
        bit = cells[index]
        if rule.due(state):
            expected = rule.value(state)
            stuffed.append(index)
            if bit != expected:
                errors.append(index)
                state = stuff_error_state(rule, state, bit, expected, on_error)
            else:
                state = rule.after_stuff(bit)
        else:
            data.append(bit)
            state = rule.after_data(state, bit)
        index += 1
    return DestuffResult(data, errors, stuffed, state, index)


def stuff_error_state(rule: StuffRule, state: RunState, received: int, expected: int,
                      mode: str) -> RunState:
    """Run state after a wrong stuff bit (not fixed by the contract)."""
    if mode == "as_expected":
        return rule.after_stuff(expected)
    if mode == "received":
        # the stuff cell counts as one bit of the level actually received
        if rule.either:
            return RunState(received, 1)
        return RunState(received, 1 if received else 0)
    if mode == "reset":
        # the stuff cell restarts the counter as a correct one would, but from
        # the received level: one bit of it (either polarity), or no 1s
        if rule.either:
            return RunState(received, 1)
        return RunState(0, 0)
    if mode == "count":
        return rule.after_data(state, received)
    raise ValueError(mode)


# ---------------------------------------------------------------------------
# Instruction fields (docs/isa.md, Line-unit extension)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ltim:
    period: int        # P, half-period in cycles, 0 stops the ticker
    fraction: int      # Q / 256
    delay: int         # D, first-tick delay, 0 means P

    @classmethod
    def decode(cls, imm: int) -> "Ltim":
        return cls(imm & 0xFF, (imm >> 8) & 0xFF, (imm >> 16) & 0xFF)

    def encode(self) -> int:
        return self.period | (self.fraction << 8) | (self.delay << 16)

    @property
    def invalid(self) -> bool:
        return self.period == 255 and self.fraction != 0


@dataclass(frozen=True)
class Lcfg:
    code: int
    stuffing: bool
    either: bool
    run_length: int
    pair: bool
    arbitration: bool
    se0_end: bool
    initial_level: int
    high_bits: int = 0

    @classmethod
    def decode(cls, imm: int) -> "Lcfg":
        return cls(code=imm & 3, stuffing=bool(imm >> 2 & 1), either=bool(imm >> 3 & 1),
                   run_length=((imm >> 4) & 7) + 1, pair=bool(imm >> 7 & 1),
                   arbitration=bool(imm >> 8 & 1), se0_end=bool(imm >> 9 & 1),
                   initial_level=(imm >> 10) & 1, high_bits=(imm >> 11) & 0x1FFF)

    def encode(self) -> int:
        return (self.code | self.stuffing << 2 | self.either << 3
                | (self.run_length - 1) << 4 | self.pair << 7 | self.arbitration << 8
                | self.se0_end << 9 | self.initial_level << 10 | self.high_bits << 11)

    @property
    def invalid(self) -> bool:
        return self.high_bits != 0 or self.code == 3 or (self.either and self.run_length == 1)

    @property
    def rule(self) -> StuffRule:
        return StuffRule(self.run_length, self.either)


RESET_LCFG = Lcfg.decode(0)


@dataclass(frozen=True)
class Pins:
    clock: int   # pair pin
    tx: int      # data pin
    rx: int

    @classmethod
    def decode(cls, imm: int) -> "Pins":
        return cls(imm & 7, (imm >> 3) & 7, (imm >> 6) & 7)

    def encode(self) -> int:
        return self.clock | self.tx << 3 | self.rx << 6


XFER_OP, LTIM_OP, LCFG_OP, CRC_OP, LSTAT_OP = 17, 30, 31, 32, 33


@dataclass(frozen=True)
class XferMode:
    count: int
    msb_first: bool
    drive: bool
    sample: bool
    line: bool
    feed_crc: bool

    @classmethod
    def decode(cls, a: int, c: int) -> "XferMode":
        return cls(count=a, msb_first=bool(c >> 2 & 1), drive=bool(c >> 3 & 1),
                   sample=bool(c >> 4 & 1), line=bool(c >> 5 & 1), feed_crc=bool(c >> 6 & 1))


def line_instruction_faults(op: int, a: int, b: int, c: int, *, owned: int, pins: Pins,
                            ticker_running: bool, cfg: Lcfg,
                            interp: "Interpretation" = None) -> bool:
    """True when docs/isa.md says the instruction faults with code 1.

    Covers opcodes 17 (the line-unit rules), 30..33 and 34..255.  Base-ISA
    rules of a classic XFER (b range, pin collision) are included because a
    classic XFER with c bit 6 is part of the extension's encoding space.
    """
    interp = interp or Interpretation()
    imm = (a << 16) | (b << 8) | c
    if op >= 34:
        return True
    if op == LTIM_OP:
        return Ltim.decode(imm).invalid
    if op == LCFG_OP:
        cfg = Lcfg.decode(imm)
        if cfg.high_bits or cfg.code == 3:
            return True
        if cfg.either and cfg.run_length == 1:
            return cfg.stuffing or interp.either_len1_invalid_without_stuffing
        return False
    if op == CRC_OP:
        if c == 1:
            return a != 0 or b > 3
        if c == 2:
            return b != 0 or a > 3
        if c == 3:
            return a != 0 or b > 3
        return True
    if op == LSTAT_OP:
        return a > 3 or b != 0 or c != 0
    if op == XFER_OP:
        if c & 0x80:
            return True
        mode = XferMode.decode(a, c)
        if mode.line:
            if b != 0 or c & 3 or not 1 <= a <= 32 or not ticker_running:
                return True
            if cfg.code == MANCHESTER and mode.sample:
                return True
            if mode.drive and not (owned >> pins.tx) & 1:
                return True
            if cfg.pair and (mode.drive or interp.pair_rule_without_drive):
                if not (owned >> pins.clock) & 1 or pins.clock == pins.tx:
                    return True
            return False
        # classic XFER (base ISA): a 1..32, b 1..255, pins differ when driving
        if not 1 <= a <= 32 or not 1 <= b <= 255:
            return True
        if mode.drive and (pins.clock == pins.tx or not (owned >> pins.tx) & 1):
            return True
        if not (owned >> pins.clock) & 1:
            return True
        return False
    return False


def capability_word(version: int, engines: int, *, line_unit: bool = True, fraction: bool = True,
                    stuffing: bool = True, arbitration: bool = True, crc16: bool = True,
                    crc32: bool = False, presets: bool = True) -> int:
    """READ_SELECT 7 (isa.md "Discovery"): capability bits 23..8, version 7..0."""
    bits = (line_unit << 8 | fraction << 9 | stuffing << 10 | arbitration << 11
            | crc16 << 12 | crc32 << 13 | presets << 14 | (engines & 0xF) << 16)
    return bits | (version & 0xFF)


# ---------------------------------------------------------------------------
# Ticker (docs/isa.md, "Ticker")
# ---------------------------------------------------------------------------

def ticker_ticks(c0: int, ltim: Ltim, count: int) -> list[int]:
    """T(0..count-1) by the contract's recursion.

    T(0) = c0 + (D, or P when D = 0); T(k+1) = T(k) + P + carry(k) with
    carry(k) = (acc(k) + Q) >> 8, acc(k+1) = (acc(k) + Q) mod 256, acc(0) = 0.
    """
    ticks = []
    tick = c0 + (ltim.delay if ltim.delay else ltim.period)
    acc = 0
    for _ in range(count):
        ticks.append(tick)
        total = acc + ltim.fraction
        tick += ltim.period + (total >> 8)
        acc = total & 0xFF
    return ticks


def ticker_tick_closed_form(c0: int, ltim: Ltim, k: int) -> int:
    """T(k) = T(0) + k*P + floor(k*Q/256).

    Proved from the recursion by induction in test/stdref/ticker_closed_form.lean
    (Lean 4 + Mathlib); test_ticker.py also compares both forms numerically.
    """
    return c0 + (ltim.delay if ltim.delay else ltim.period) + k * ltim.period + (k * ltim.fraction) // 256


class Ticker:
    """Tick times of one LTIM, computed on demand."""

    def __init__(self, c0: int, ltim: Ltim):
        self.c0 = c0
        self.ltim = ltim
        self._ticks: list[int] = []
        self._acc = 0
        self._next = c0 + (ltim.delay if ltim.delay else ltim.period)

    def tick(self, k: int) -> int:
        while len(self._ticks) <= k:
            self._ticks.append(self._next)
            total = self._acc + self.ltim.fraction
            self._next += self.ltim.period + (total >> 8)
            self._acc = total & 0xFF
        return self._ticks[k]

    def first_after(self, cycle: int, parity: int, strict: bool = True) -> int:
        """Index of the first tick with the given parity after ``cycle``."""
        k = parity
        while True:
            t = self.tick(k)
            if t > cycle or (not strict and t == cycle):
                return k
            k += 2


# ---------------------------------------------------------------------------
# Interpretation choices
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Interpretation:
    """Readings of points that docs/isa.md does not fix.

    first_tick_strict      A line XFER issued in cycle c uses the first tick
                           of the needed parity in a later cycle (True) or also
                           a tick in cycle c (False).  isa.md says "3 cycles to
                           issue before the next boundary".
    ds_stuff_basis         Which bits run the stuffing counter of a
                           drive-and-sample XFER: "received" (the CAN receiver
                           rule; required to follow the winner's frame after a
                           lost arbitration) or "sent".
    ds_crc_basis           Which data bits a drive-and-sample XFER feeds to the
                           CRC: "received" or "sent" (the line bits it drives,
                           1 after a lost arbitration) or "tx" (the tx register
                           bits).
    stuff_error_state      How a wrong received stuff bit updates the run
                           counter: "as_expected" (as if the stuff bit had
                           been right), "received" (one bit of the received
                           level), "reset" ("received" for either polarity,
                           "as_expected" for runs of 1s) or "count" (the stuff
                           cell continues the run).
    se0_pins               "rx_pair": SE0 = RX pin and pair pin low;
                           "data_pair": data (TX field) pin and pair pin low.
    se0_needs_pair         SE0 end only with LCFG pair set.
    lstat_level_from_samples  A sample sets the line level that LSTAT[5]
                           reports (else only driving sets it).
    drive_only_sets_prev_sample  A drive-only XFER also sets the NRZI
                           receiver's previous-sample level (with both True,
                           the line level and the previous sample act as one
                           register).
    pair_rule_without_drive  The "pair pin owned and != data pin" rule also
                           applies to a sample-only XFER.
    arbitration_in_stuff   A stuff cell driven 1 and read 0 also loses.
    lost_persists          After a lost arbitration later XFERs also drive 1
                           (the flag stays set until LCFG or START).
    init_level_counts_in_run  LCFG's initial level counts as the previous line
                           bit of the stuffing counter.
    either_len1_invalid_without_stuffing  LCFG with bit 3 set and run length 1
                           faults also when bit 2 (stuffing) is clear.
    classic_ds_crc_basis   A classic XFER that drives and samples with c bit 6
                           feeds the bits it shifts out ("sent") or samples.
    lcfg_cancels_pending_half  LCFG issued between the last boundary of a
                           Manchester XFER and its mid-bit tick drops the
                           second half (LCFG resets the line state).
    se0_left_counts_trailing_stuff  LSTAT[13:8] after an SE0 in the cell of a
                           due trailing stuff bit (all data bits received):
                           0 (False, "data bits remaining") or 1 (True).
    ltim_pending_half      The second half pending when LTIM restarts the
                           ticker: "keep" (written at the old mid-bit tick),
                           "cancel" (dropped) or "next_mid" (written at the
                           next mid-bit tick, T(1) of the restarted ticker).
    """

    first_tick_strict: bool = True
    ds_stuff_basis: str = "received"
    ds_crc_basis: str = "received"
    stuff_error_state: str = "as_expected"
    se0_pins: str = "rx_pair"
    se0_needs_pair: bool = False
    lstat_level_from_samples: bool = True
    drive_only_sets_prev_sample: bool = True
    pair_rule_without_drive: bool = True
    arbitration_in_stuff: bool = True
    lost_persists: bool = True
    init_level_counts_in_run: bool = False
    either_len1_invalid_without_stuffing: bool = True
    classic_ds_crc_basis: str = "sent"
    lcfg_cancels_pending_half: bool = False
    ltim_pending_half: str = "keep"
    se0_left_counts_trailing_stuff: bool = False


# ---------------------------------------------------------------------------
# Transaction-level reference of one engine's line unit
# ---------------------------------------------------------------------------

@dataclass
class PinWrite:
    cycle: int          # tick cycle in which the unit writes
    pin: int
    value: int
    kind: str           # "boundary", "mid" (Manchester second half)


@dataclass
class Cell:
    boundary: Optional[int]   # boundary tick cycle (driving XFERs)
    mid: Optional[int]        # mid-bit tick cycle (sampled or Manchester)
    line_bit: int             # the line bit (before line coding) sent or received
    stuff: bool
    level: Optional[int] = None       # pin level of the first half (driving)
    sample: Optional[int] = None      # raw pin level read at the mid-bit tick
    stuff_error: bool = False


@dataclass
class XferResult:
    issue: int
    complete: int                     # cycle in which the XFER completes
    tx: int
    rx: int
    writes: list[PinWrite]
    cells: list[Cell]
    data_sent: list[int]
    data_received: list[int]
    crc_fed: list[int]
    se0: bool = False
    bits_left: int = 0
    lost_at: Optional[int] = None


# sample_fn(tick_cycle) -> {pin: level} as seen by the unit in that cycle
SampleFn = Callable[[int], dict]


class XferDoesNotEnd(Exception):
    """A sampling line XFER whose cells never complete its data bits.

    Possible when received stuff bits keep being wrong (for example runs of 1s
    with run length 1 and a line held at 1): every cell is then a stuff cell.
    ``LineUnitRef.xfer`` raises it after ``max_cells`` cells.
    """


class LineUnitRef:
    """The line unit of one engine, at tick level, from the contract."""

    def __init__(self, interp: Interpretation = Interpretation()):
        self.interp = interp
        self.start()

    # START and reset clear everything (isa.md "START, reset and faults").
    def start(self) -> None:
        self.ticker: Optional[Ticker] = None
        self.cfg = RESET_LCFG
        self.level = 0
        self.prev_sample = 0
        self.run = RunState()
        self.crc = 0
        self.preset = 0
        self.flag_se0 = False
        self.flag_lost = False
        self.flag_stuff = False
        self.bits_left = 0
        self.level_updates: list[tuple[int, int]] = []   # (cycle, level): Manchester second halves

    @property
    def ticker_running(self) -> bool:
        return self.ticker is not None

    def ltim(self, cycle: int, imm: int) -> None:
        ltim = Ltim.decode(imm)
        self.ticker = Ticker(cycle, ltim) if ltim.period else None

    def stop_ticker(self) -> None:
        """A classic XFER stops the ticker."""
        self.ticker = None

    def settle(self, cycle: int) -> int:
        """Apply line-level updates of ticks before ``cycle``; the level seen in ``cycle``."""
        due = [u for u in self.level_updates if u[0] < cycle]
        for _, level in due:
            self.level = level
        self.level_updates = [u for u in self.level_updates if u[0] >= cycle]
        return self.level

    def lcfg(self, imm: int, cycle: Optional[int] = None) -> None:
        if cycle is not None:
            self.settle(cycle)
        if self.interp.lcfg_cancels_pending_half:
            self.level_updates = []
        self.cfg = Lcfg.decode(imm)
        self.level = self.cfg.initial_level
        self.prev_sample = self.cfg.initial_level
        if self.interp.init_level_counts_in_run and self.cfg.stuffing:
            init = self.cfg.initial_level
            self.run = RunState(init, 1 if (self.cfg.either or init) else 0)
        else:
            self.run = RunState()
        self.flag_se0 = self.flag_lost = self.flag_stuff = False
        self.bits_left = 0

    def crc_set(self, value: int) -> None:
        self.crc = value & 0xFFFF

    def crc_preset(self, preset: int) -> None:
        self.preset = preset & 3

    def feed(self, bits: Sequence[int], msb_first: bool) -> None:
        self.crc = unit_crc(self.crc, bits, self.preset, msb_first)

    def lstat(self, tx_has_data: bool, rx_has_space: bool, cycle: Optional[int] = None) -> int:
        if cycle is not None:
            self.settle(cycle)
        return (int(self.flag_se0) | int(self.flag_lost) << 1 | int(self.flag_stuff) << 2
                | int(tx_has_data) << 3 | int(rx_has_space) << 4 | self.level << 5
                | int(self.ticker_running) << 6 | (self.bits_left & 0x3F) << 8)

    # -- line XFER ---------------------------------------------------------
    def xfer(self, issue: int, a: int, c: int, tx: int, rx: int, pins: Pins,
             sample_fn: Optional[SampleFn] = None,
             write_fn: Optional[Callable[[PinWrite], None]] = None,
             max_cells: int = 1024) -> XferResult:
        """Run a valid line XFER issued in ``issue``; returns its schedule.

        ``sample_fn(t)`` gives the pin levels the unit sees in tick cycle t.
        ``write_fn`` is called with every pin write as soon as it is decided
        (before the sample of the same cell), so a caller can model a bus that
        depends on the unit's own output (loopback, wired AND).
        """
        mode = XferMode.decode(a, c)
        assert mode.line and self.ticker is not None
        cfg, rule, ticker, interp = self.cfg, self.cfg.rule, self.ticker, self.interp
        strict = interp.first_tick_strict
        writes: list[PinWrite] = []
        cells: list[Cell] = []
        sent: list[int] = []
        received: list[int] = []
        crc_fed: list[int] = []
        tx_bits_used: list[int] = []
        se0 = False
        lost_at = None
        data_done = 0
        stuffing = cfg.stuffing
        if mode.drive:
            k = ticker.first_after(issue, 0, strict)
        else:
            k = ticker.first_after(issue, 1, strict)
        complete = issue
        while True:
            if len(cells) >= max_cells:
                raise XferDoesNotEnd(f"{a}-bit line XFER issued in cycle {issue}: "
                                     f"{len(cells)} cells, {data_done} data bits")
            need_trailing = stuffing and rule.due(self.run)
            if data_done >= a and not need_trailing:
                break
            is_stuff = stuffing and rule.due(self.run)
            boundary = ticker.tick(k) if mode.drive else None
            mid = ticker.tick(k + 1) if mode.drive else ticker.tick(k)
            cell = Cell(boundary=boundary, mid=mid if (mode.sample or cfg.code == MANCHESTER) else None,
                        line_bit=0, stuff=is_stuff)
            # driving half of the cell
            if mode.drive:
                self.settle(boundary)
                if is_stuff:
                    bit = rule.value(self.run)
                    tx_bit = None
                else:
                    tx_bit = (tx >> 31) & 1 if mode.msb_first else tx & 1
                    bit = tx_bit
                if self.flag_lost and (interp.lost_persists or lost_at is not None):
                    bit = 1          # isa.md: after a lost arbitration it drives 1
                cell.line_bit = bit
                if cfg.code == NRZ:
                    level = bit
                elif cfg.code == NRZI:
                    level = self.level if bit else self.level ^ 1
                else:
                    level = bit ^ 1
                cell.level = level
                self.level = level
                new_writes = [PinWrite(boundary, pins.tx, level, "boundary")]
                if cfg.pair:
                    new_writes.append(PinWrite(boundary, pins.clock, level ^ 1, "boundary"))
                if cfg.code == MANCHESTER:
                    new_writes.append(PinWrite(mid, pins.tx, bit, "mid"))
                    if cfg.pair:
                        new_writes.append(PinWrite(mid, pins.clock, bit ^ 1, "mid"))
                    self.level_updates.append((mid, bit))
                if not mode.sample and interp.drive_only_sets_prev_sample:
                    self.prev_sample = bit if cfg.code == MANCHESTER else level
                writes.extend(new_writes)
                if write_fn:
                    for write in new_writes:
                        write_fn(write)
                if not is_stuff:
                    if mode.msb_first:
                        tx = (tx << 1) & WORD_MASK
                    else:
                        tx >>= 1
                    tx_bits_used.append(tx_bit)
                    sent.append(bit)
                complete = boundary
            # sampling half of the cell
            received_bit = None
            if mode.sample:
                seen = sample_fn(mid) if sample_fn else {}
                sample = seen.get(pins.rx, 0)
                cell.sample = sample
                if cfg.se0_end and (cfg.pair or not interp.se0_needs_pair):
                    first = pins.rx if interp.se0_pins == "rx_pair" else pins.tx
                    if seen.get(first, 0) == 0 and seen.get(pins.clock, 0) == 0:
                        se0 = True
                        complete = mid
                        cells.append(cell)
                        break
                if cfg.code == NRZI:
                    received_bit = 1 if sample == self.prev_sample else 0
                else:
                    received_bit = sample
                self.prev_sample = sample
                if interp.lstat_level_from_samples and not mode.drive:
                    self.level = sample
                if mode.drive and cfg.arbitration and not self.flag_lost:
                    if cell.line_bit == 1 and received_bit == 0 and (not is_stuff or interp.arbitration_in_stuff):
                        lost_at = len(cells)
                        self.flag_lost = True
                if is_stuff:
                    expected = rule.value(self.run)
                    if received_bit != expected:
                        cell.stuff_error = True
                        self.flag_stuff = True
                else:
                    received.append(received_bit)
                    if mode.msb_first:
                        rx = ((rx << 1) | received_bit) & WORD_MASK
                    else:
                        rx = (rx >> 1) | (received_bit << 31)
                complete = mid
            # run counter and CRC
            if mode.drive and mode.sample:
                basis_bit = received_bit if interp.ds_stuff_basis == "received" else cell.line_bit
            elif mode.drive:
                basis_bit = cell.line_bit
            else:
                basis_bit = received_bit
            if stuffing:
                if is_stuff:
                    expected = rule.value(self.run)
                    if basis_bit != expected:
                        self.run = stuff_error_state(rule, self.run, basis_bit, expected,
                                                     interp.stuff_error_state)
                    else:
                        self.run = rule.after_stuff(basis_bit)
                else:
                    self.run = rule.after_data(self.run, basis_bit)
            if not is_stuff:
                data_done += 1
                if mode.feed_crc:
                    if mode.drive and mode.sample:
                        fed = {"received": received_bit, "sent": cell.line_bit,
                               "tx": tx_bits_used[-1]}[interp.ds_crc_basis]
                    elif mode.drive:
                        fed = cell.line_bit
                    else:
                        fed = received_bit
                    crc_fed.append(fed)
                    self.crc = unit_crc(self.crc, [fed], self.preset, mode.msb_first)
            cell.line_bit = cell.line_bit if mode.drive else received_bit
            cells.append(cell)
            k += 2
        bits_left = a - data_done
        if se0 and data_done >= a and interp.se0_left_counts_trailing_stuff:
            bits_left = 1        # the pending trailing stuff cell counted as one bit
        if se0:
            self.flag_se0 = True
            self.bits_left = bits_left
        return XferResult(issue=issue, complete=complete, tx=tx, rx=rx, writes=writes,
                          cells=cells, data_sent=sent, data_received=received,
                          crc_fed=crc_fed, se0=se0, bits_left=bits_left if se0 else 0,
                          lost_at=lost_at)


def render_trace(writes: Iterable[PinWrite], pin: int, length: int, initial: int = 0,
                 out_latency: int = OUT_LATENCY) -> list[int]:
    """Per-cycle pad level of ``pin`` from unit writes (later writes win)."""
    changes = {}
    for write in writes:
        if write.pin == pin:
            changes[write.cycle + out_latency] = write.value
    trace, level = [], initial
    for n in range(length):
        level = changes.get(n, level)
        trace.append(level)
    return trace


# ---------------------------------------------------------------------------
# Pad-trace decoder
# ---------------------------------------------------------------------------

@dataclass
class PadDecode:
    line_bits: list[int]              # every cell, before destuffing
    data: list[int]                   # data bits after destuffing
    stuff_cells: list[int]
    stuff_errors: list[int]
    se0_cell: Optional[int]           # cell index at which SE0 was seen
    violations: list[str]             # level changes off the tick grid, pair errors
    boundaries: list[int]             # boundary tick cycle of every decoded cell
    crc: Optional[int] = None         # unit CRC register after the data bits
    end_tick: Optional[int] = None    # boundary tick that follows the last cell

    def word(self, msb_first: bool, start: int = 0) -> int:
        """Pack data bits as a shifting XFER receives them into a zero register."""
        value = start
        for bit in self.data:
            if msb_first:
                value = ((value << 1) | bit) & WORD_MASK
            else:
                value = (value >> 1) | (bit << 31)
        return value


def decode_pad_trace(data_trace: Sequence[int], ltim_cycle: int, ltim_imm: int, lcfg_imm: int,
                     first_boundary: int, *, pair_trace: Optional[Sequence[int]] = None,
                     count: Optional[int] = None, cells: Optional[int] = None,
                     run_state: RunState = RunState(), level: Optional[int] = None,
                     crc_preset: Optional[int] = None, crc_init: int = 0,
                     msb_first: bool = False, stop_on_se0: bool = False,
                     check_until: Optional[int] = None,
                     out_latency: int = OUT_LATENCY) -> PadDecode:
    """Decode a driven line from pad traces.

    ``data_trace[n]`` (and ``pair_trace[n]``) is the pad level in cycle n.
    The cell grid comes from the LTIM issued in ``ltim_cycle`` with ``ltim_imm``
    (boundary = even tick, shifted by ``out_latency``); the line code, stuffing
    and pair from ``lcfg_imm``.  Decoding starts at the boundary tick with even
    index ``first_boundary`` and stops after ``count`` data bits (and a due
    trailing stuff bit), after ``cells`` cells, at SE0 (``stop_on_se0``) or at
    the end of the trace.  Every cycle of every decoded cell is checked: the
    level must be constant inside a half cell (NRZ, NRZI: the whole cell), a
    Manchester cell must change at mid-bit, and the pair pin must be the
    complement of the data pin.  ``level`` is the NRZI level before the first
    cell (default: the trace just before it).
    """
    cfg = Lcfg.decode(lcfg_imm)
    ticker = Ticker(ltim_cycle, Ltim.decode(ltim_imm))
    rule = cfg.rule
    violations: list[str] = []
    line_bits: list[int] = []
    boundaries: list[int] = []
    data: list[int] = []
    stuffed: list[int] = []
    errors: list[int] = []
    se0_cell = None
    state = run_state
    k = first_boundary
    limit = len(data_trace) if check_until is None else min(check_until, len(data_trace))
    if level is None:
        start = ticker.tick(k) + out_latency
        level = data_trace[start - 1] if start - 1 < len(data_trace) else 0
    end_tick = None
    while True:
        if cells is not None and len(line_bits) >= cells:
            break
        if count is not None and len(data) >= count and not (cfg.stuffing and rule.due(state)):
            break
        t0 = ticker.tick(k) + out_latency
        tm = ticker.tick(k + 1) + out_latency
        t1 = ticker.tick(k + 2) + out_latency
        if t0 >= limit:
            break
        window_end = min(t1, limit)
        first = data_trace[t0]
        if pair_trace is not None and stop_on_se0 and first == 0 and pair_trace[t0] == 0:
            se0_cell = len(line_bits)
            end_tick = t0 - out_latency
            break
        if cfg.code == MANCHESTER:
            first_half = [data_trace[n] for n in range(t0, min(tm, window_end))]
            second_half = [data_trace[n] for n in range(tm, window_end)]
            if any(v != first for v in first_half):
                violations.append(f"cell {len(line_bits)}: first half not constant")
            if second_half and any(v != second_half[0] for v in second_half):
                violations.append(f"cell {len(line_bits)}: second half not constant")
            if second_half and second_half[0] == first:
                violations.append(f"cell {len(line_bits)}: no mid-bit transition")
            bit = first ^ 1
            level = bit
        else:
            window = [data_trace[n] for n in range(t0, window_end)]
            if any(v != first for v in window):
                violations.append(f"cell {len(line_bits)}: level changes inside the cell")
            if cfg.code == NRZI:
                bit = 1 if first == level else 0
                level = first
            else:
                bit = first
        if pair_trace is not None and cfg.pair:
            for n in range(t0, window_end):
                if pair_trace[n] != data_trace[n] ^ 1:
                    violations.append(f"cell {len(line_bits)}: pair pin not complementary in cycle {n}")
                    break
        index = len(line_bits)
        line_bits.append(bit)
        boundaries.append(t0 - out_latency)
        if cfg.stuffing and rule.due(state):
            expected = rule.value(state)
            stuffed.append(index)
            if bit != expected:
                errors.append(index)
                state = stuff_error_state(rule, state, bit, expected, "as_expected")
            else:
                state = rule.after_stuff(bit)
        else:
            data.append(bit)
            if cfg.stuffing:
                state = rule.after_data(state, bit)
        k += 2
        end_tick = ticker.tick(k)
    crc = None
    if crc_preset is not None:
        crc = unit_crc(crc_init, data, crc_preset, msb_first)
    return PadDecode(line_bits, data, stuffed, errors, se0_cell, violations, boundaries, crc, end_tick)


# ---------------------------------------------------------------------------
# Standard frames
# ---------------------------------------------------------------------------

# USB 2.0 (8.3.1 PIDs, 8.2 SYNC).  Levels are D+ values; low speed idle J is
# D+ low / D- high, so on D+ J = 0 and K = 1.
USB_SYNC = [0, 0, 0, 0, 0, 0, 0, 1]
USB_PID = {"OUT": 0xE1, "IN": 0x69, "SOF": 0xA5, "SETUP": 0x2D,
           "DATA0": 0xC3, "DATA1": 0x4B, "ACK": 0xD2, "NAK": 0x5A}
USB_LS_J_DPLUS = 0


def usb_pid_bits(pid: int) -> list[int]:
    return int_to_bits(pid, 8, lsb_first=True)


def usb_token_body(addr: int, endp: int) -> list[int]:
    """ADDR (7 bits) and ENDP (4 bits) LSB first, then the CRC5 field."""
    body = int_to_bits(addr, 7, True) + int_to_bits(endp, 4, True)
    crc = CRC5_USB.compute_bits(body)
    return body + CRC5_USB.crc_field_bits(crc)


def usb_sof_body(frame: int) -> list[int]:
    body = int_to_bits(frame, 11, True)
    return body + CRC5_USB.crc_field_bits(CRC5_USB.compute_bits(body))


def usb_data_body(data: bytes) -> list[int]:
    body = bytes_to_bits(data, lsb_first=True)
    return body + CRC16_USB.crc_field_bits(CRC16_USB.compute_bits(body))


def usb_packet_bits(pid: str, body: Sequence[int]) -> list[int]:
    """SYNC, PID and body in transmission order (before stuffing and NRZI)."""
    return USB_SYNC + usb_pid_bits(USB_PID[pid]) + list(body)


def usb_line_levels(packet_bits: Sequence[int], idle_dplus: int = USB_LS_J_DPLUS) -> tuple[list[int], list[tuple[int, bool]]]:
    """D+ level per bit time: stuffing (from SYNC on, also before EOP), NRZI from idle."""
    cells, _ = stuff(packet_bits, USB_STUFFING)
    levels = nrzi_encode([bit for bit, _ in cells], idle_dplus)
    return levels, cells


# CAN 2.0A data frame (Bosch CAN 2.0 part A section 3.1.1).
def can_frame_fields(ident: int, data: bytes, rtr: int = 0) -> list[int]:
    """SOF, 11-bit ID, RTR, IDE, r0, DLC, data (MSB first): the CRC input."""
    bits = [0] + int_to_bits(ident, 11, False) + [rtr, 0, 0] + int_to_bits(len(data), 4, False)
    bits += bytes_to_bits(data, lsb_first=False)
    return bits


def can_crc15(fields: Sequence[int]) -> int:
    return CRC15_CAN.compute_bits(fields)


def can_frame(ident: int, data: bytes, ack: bool = True) -> dict:
    """Destuffed and stuffed bit streams of a CAN 2.0A data frame."""
    fields = can_frame_fields(ident, data)
    crc = can_crc15(fields)
    stuffed_part = fields + int_to_bits(crc, 15, False)
    cells, _ = stuff(stuffed_part, CAN_STUFFING)
    tail = [1, 0 if ack else 1, 1] + [1] * 7
    return {"fields": fields, "crc": crc, "stuffed": [b for b, _ in cells],
            "cells": cells, "tail": tail}


# Ethernet (IEEE 802.3 clause 3): preamble, SFD, frame, FCS.
ETH_PREAMBLE_SFD = bytes([0x55] * 7 + [0xD5])


def ethernet_fcs(frame_without_fcs: bytes) -> bytes:
    """FCS: CRC-32/ISO-HDLC, appended least significant byte first."""
    return CRC32_ETHERNET.compute(frame_without_fcs).to_bytes(4, "little")


def ethernet_line_bits(frame_with_fcs: bytes) -> list[int]:
    return bytes_to_bits(ETH_PREAMBLE_SFD + frame_with_fcs, lsb_first=True)
