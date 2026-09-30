# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Line-unit cases for the random campaign (``VCAMP_VARIANT=line``, ``line-dense``,
``line-faulty``); design variants with ``options.line_unit`` only (docs/extension.md).

A case is a ``random_gen.Case`` (programs, TX prefill, routes, triggers, START
mask, host operations) plus an ``env`` object that describes the external pad
levels. It is generated from ``(seed, index)`` alone and serialized like the
upstream cases, so a failing case can be replayed (``PE_REPLAY``) and shrunk
(``VCAMP_MINIMIZE``). The lockstep check (harness.py) compares the DUT with the
reference model on every cycle; nothing else here is a checker.

Programs. Every loaded engine gets, with probability 0.85, a line program and
otherwise an upstream ``ProgramGenerator`` program. A line program starts with
DIR, SET, PINS, LCFG, LTIM and CRC set-up and then draws blocks: line XFERs of 1
to 32 bits (drive, sample, both, and rarely neither) with data from PULL, LOAD,
NOT or MOV and results made visible by PUSH; LTIM with edge values of P, Q and
D; LCFG with every line code, stuffing polarity and run length, pair,
arbitration monitor, SE0 end and initial level; CRC set/read/preset; LSTAT;
classic XFERs with and without the CRC bit; PINS changes; the upstream
instruction mix (loops, WAIT, PULL/PUSH, WAITPIN, SIGNAL/WAITEVENT, ALU, jumps,
FAULT n); and, at the case's fault rate, invalid line-unit encodings (annotated
with fault code 1 where the encoding alone makes them invalid).

Pads. Engine k usually owns pins 2k and 2k+1 (otherwise a random disjoint
assignment), some pins open-drain. RX pins are another engine's data pin, the
engine's own data pin, a pin driven by the environment, or any pin. The
environment (``LineEnvironment``) toggles undriven pads, copies, inverts or
ANDs (wired bus) other pads with a delay, pulls pin pairs low (SE0), and runs
line transmitters that send NRZ/NRZI frames with bit stuffing, a complementary
pair pin and an SE0 end at a random rate, often the rate of a receiving engine.

Host traffic. ``random_gen.host_op`` (TX writes, RX reads, status reads of all
eight selections, raw commands including ROUTE for the mover, partial
transfers, reloads, revive), line-program reloads, extra revive operations, and
the generation-2 additions of ``random_gen.extend_case`` (a mid-traffic
deselect, rejected command sequences), drawn from a separate random stream.
"""

from __future__ import annotations

import copy
import json
import random
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

import random_gen
from harness import ROUTE, SELECT, START, STOP, TRIGGER, pad_value
from line_support import nrzi_encode, stuff_either, stuff_ones
from random_gen import Case, EngineSpec, ProgramGenerator

LINE_GENERATION = 1
PROFILES = ("line", "line-dense", "line-faulty")
TX, RX, X, Y = range(4)


def _ins(op: int, a: int = 0, b: int = 0, c: int = 0) -> int:
    return op << 24 | a << 16 | b << 8 | c


def _imm(op: int, value: int) -> int:
    return op << 24 | (value & 0xFFFFFF)


def ltim(p: int, q: int = 0, d: int = 0) -> int:
    return _imm(30, p | q << 8 | d << 16)


def lcfg_word(code: int, stuff: int, either: bool, pair: bool, arb: bool, se0: bool, init: int) -> int:
    """LCFG immediate (docs/isa.md): stuff = run length 1..8 or 0 for no stuffing."""
    value = code | (4 | (stuff - 1) << 4 if stuff else 0) | 8 * either
    return _imm(31, value | 0x80 * pair | 0x100 * arb | 0x200 * se0 | init << 10)


def xline(bits: int, msb: bool, drive: bool, sample: bool, crc: bool) -> int:
    return _ins(17, bits, 0, 0x20 | 4 * msb | 8 * drive | 0x10 * sample | 0x40 * crc)


MNEMONIC = random_gen.MNEMONIC + ["LTIM", "LCFG", "CRC", "LSTAT"]


def disassemble(word: int) -> str:
    op = word >> 24
    if op == 17 and word & 0x20:
        c = word & 0xFF
        mode = "+".join(n for n, bit in (("drive", 8), ("sample", 16)) if c & bit) or "neither"
        return (f"XFER line bits={word >> 16 & 255} b={word >> 8 & 255} {mode} "
                f"{'MSB' if c & 4 else 'LSB'}{' crc' if c & 64 else ''} c={c:#04x}")
    if op in (30, 31):
        return f"{MNEMONIC[op]} {word & 0xFFFFFF:#08x}"
    if op in (32, 33):
        return f"{MNEMONIC[op]} a={word >> 16 & 255} b={word >> 8 & 255} c={word & 255}"
    return random_gen.disassemble(word)


def listing(words: list[int], expect: dict[int, int] | None = None) -> str:
    lines = []
    for pc, word in enumerate(words):
        note = f"   ; deliberate fault -> code {expect[pc]}" if expect and pc in expect else ""
        lines.append(f"      {pc:2d}: {word:08x}  {disassemble(word)}{note}")
    return "\n".join(lines)


@dataclass
class LineCase(Case):
    """``random_gen.Case`` plus the pad environment and the generator profile."""

    env: dict = field(default_factory=dict)
    profile: str = "line"
    generation: int = LINE_GENERATION

    @staticmethod
    def from_json(text: str) -> LineCase:
        data = json.loads(text)
        engines = [None if e is None else EngineSpec(e["words"], e["ownership"], e["open_drain"],
                                                     {int(k): v for k, v in e["expect"].items()})
                   for e in data.pop("engines")]
        return LineCase(engines=engines, **data)

    def describe(self) -> str:
        lines = [f"  line case seed={self.seed:#x} index={self.index} profile={self.profile} "
                 f"start_mask={self.start_mask:#x} cycles={self.cycles}",
                 f"  prefill={self.prefill} routes={self.routes} triggers={self.triggers}",
                 f"  env={json.dumps(self.env)}"]
        for e, spec in enumerate(self.engines):
            if spec is None:
                lines.append(f"  engine {e}: not loaded")
            else:
                lines.append(f"  engine {e}: own={spec.ownership:#04x} open_drain={spec.open_drain:#04x} "
                             f"{len(spec.words)} words")
                lines.append(listing(spec.words, spec.expect))
        lines.append(f"  host ops ({len(self.ops)}): " + "; ".join(random_gen.op_text(op) for op in self.ops[:60])
                     + (" ..." if len(self.ops) > 60 else ""))
        return "\n".join(lines)


def is_line_case_json(text: str) -> bool:
    return '"env"' in text and '"profile"' in text


# ---------------------------------------------------------------- programs
def draw_period(rng: random.Random) -> int:
    """LTIM half-period P, with edge values: 1, 2, small, medium, large, 254, 255."""
    r = rng.random()
    if r < 0.28:
        return 1
    if r < 0.52:
        return 2
    if r < 0.70:
        return rng.randint(3, 4)
    if r < 0.84:
        return rng.randint(5, 16)
    if r < 0.92:
        return rng.randint(17, 64)
    if r < 0.96:
        return rng.randint(65, 253)
    return rng.choice((254, 255))


def draw_fraction(rng: random.Random, period: int) -> int:
    if period == 255:
        return 0
    r = rng.random()
    if r < 0.45:
        return 0
    if r < 0.65:
        return rng.choice((1, 128, 255, 254, 2, 127, 129))
    return rng.randrange(256)


def draw_delay(rng: random.Random) -> int:
    r = rng.random()
    if r < 0.50:
        return 0
    if r < 0.60:
        return 1
    if r < 0.85:
        return rng.randint(2, 15)
    if r < 0.95:
        return rng.randint(16, 254)
    return 255


def draw_bits(rng: random.Random, period: int, frac: int) -> int:
    """Bit count 1..32, capped so that one XFER takes at most about 1,600 cycles."""
    cell = 2 * (period + (1 if frac else 0))
    cap = max(1, min(32, 1600 // max(cell, 1)))
    r = rng.random()
    if r < 0.30:
        bits = rng.randint(1, 32)
    elif r < 0.50:
        bits = rng.choice((1, 2, 31, 32))
    else:
        bits = rng.choice((8, 16, rng.randint(3, 12)))
    return min(bits, cap)


@dataclass
class LineConfig:
    code: int = 0
    stuff: int = 0
    either: bool = False
    pair: bool = False
    arb: bool = False
    se0: bool = False
    init: int = 0

    def word(self) -> int:
        return lcfg_word(self.code, self.stuff, self.either, self.pair, self.arb, self.se0, self.init)


def draw_lcfg(rng: random.Random) -> LineConfig:
    code = rng.choice((0, 0, 1, 1, 1, 2))
    stuff = rng.choice((0, 0, rng.randint(1, 8), rng.randint(1, 8), rng.randint(2, 6)))
    either = bool(stuff) and rng.random() < 0.5
    if either and stuff == 1:
        stuff = rng.randint(2, 8)
    elif not stuff and rng.random() < 0.08:
        either = True  # the polarity bit without stuffing is ignored (valid)
    return LineConfig(code, stuff, either, rng.random() < 0.4, rng.random() < 0.35, rng.random() < 0.35,
                      rng.randrange(2))


@dataclass
class Plan:
    """Pin plan of one case: ownership, open-drain, and per-engine data/pair pins."""
    ownership: list[int]
    open_drain: list[int]
    data_pin: list[int | None]
    pair_pin: list[int | None]
    env_pins: list[int]            # pins the environment transmitters drive
    env_pairs: list[tuple[int, int]]


class LineProgramGenerator:
    """Random line-unit programs (see the module docstring)."""

    def __init__(self, rng: random.Random, config: Any, plan: Plan, engine: int, fault_rate: float,
                 profile: str) -> None:
        self.rng, self.config, self.plan, self.engine = rng, config, plan, engine
        self.ownership = plan.ownership[engine]
        self.owned = [p for p in range(8) if self.ownership >> p & 1]
        self.fault_rate = fault_rate
        self.profile = profile
        self.base = ProgramGenerator(rng, config, self.ownership, 0.0)
        self.cfg = LineConfig()
        self.period, self.frac = 1, 0

    # -- operand helpers
    def rx_pin(self) -> int:
        rng, plan = self.rng, self.plan
        r = rng.random()
        others = [p for e, p in enumerate(plan.data_pin) if p is not None and e != self.engine]
        own = plan.data_pin[self.engine]
        if r < 0.32 and others:
            return rng.choice(others)
        if r < 0.50 and own is not None:
            return own
        if r < 0.78 and plan.env_pins:
            return rng.choice(plan.env_pins)
        return rng.randrange(8)

    def pins_word(self) -> int:
        """PINS: clock field = pair pin, TX field = data pin, RX field."""
        rng, plan = self.rng, self.plan
        data, pair = plan.data_pin[self.engine], plan.pair_pin[self.engine]
        if data is None or rng.random() < 0.06:
            data = rng.randrange(8)
        if pair is None or rng.random() < 0.06:
            pair = rng.randrange(8)
        rx = self.rx_pin()
        if rx in plan.env_pins and rng.random() < 0.6:
            for a, b in plan.env_pairs:  # receive an environment pair: RX = data, clock field = pair
                if rx == a:
                    pair = b
        return _imm(16, pair | data << 3 | rx << 6)

    def ltim_words(self) -> list[int]:
        rng = self.rng
        if rng.random() < 0.04:  # P = 0 stops the ticker, then usually restart it
            words = [ltim(0, rng.randrange(256), rng.randrange(256))]
            if rng.random() < 0.7:
                words.append(ltim(rng.randint(1, 4)))
            else:
                self.period = 0
            return words
        p = draw_period(rng)
        q = draw_fraction(rng, p)
        self.period, self.frac = p, q
        return [ltim(p, q, draw_delay(rng))]

    def data_prep(self) -> list[int]:
        rng = self.rng
        r = rng.random()
        if r < 0.30:
            return [_ins(6)]  # PULL
        if r < 0.45:
            return [_ins(19, TX, rng.randrange(256), rng.randrange(256))]  # LOAD tx, imm16
        if r < 0.52:
            return [_ins(27, TX)]  # NOT tx
        if r < 0.60:
            return [_ins(18, TX, rng.choice((RX, X, Y)))]  # MOV tx, r
        if r < 0.66:
            return [_ins(19, TX, rng.randrange(256), rng.randrange(256)), _ins(24, TX, 0, 16)]  # LOAD; SHL 16
        return []

    def xfer_block(self) -> list[int]:
        rng = self.rng
        words = self.data_prep()
        drive = rng.random() < 0.62
        sample = (not drive) or rng.random() < 0.45
        if self.cfg.code == 2 and sample:  # Manchester: TX only
            if rng.random() < 0.95:
                sample, drive = False, True
        if rng.random() < 0.01:
            drive = sample = False  # neither: waits for STOP/START (see line_coverage)
        bits = draw_bits(rng, max(self.period, 1), self.frac)
        words.append(xline(bits, rng.random() < 0.5, drive, sample, rng.random() < 0.5))
        r = rng.random()
        if sample and r < 0.45:
            words.append(_ins(7, int(rng.random() < 0.1)))  # PUSH rx
        elif r < 0.60:
            words += [_ins(32, rng.choice((X, Y, RX)), 0, 2)]  # CRC read
            if rng.random() < 0.6:
                words += [_ins(18, RX, words[-1] >> 16 & 255), _ins(7)]  # MOV rx, r; PUSH
        elif r < 0.70:
            words += [_ins(33, RX), _ins(7)]  # LSTAT rx; PUSH
        return words

    def lcfg_block(self) -> list[int]:
        self.cfg = draw_lcfg(self.rng)
        return [self.cfg.word()]

    def crc_block(self) -> list[int]:
        rng = self.rng
        r = rng.random()
        if r < 0.30:
            return [_ins(32, 0, rng.randrange(4), 3)]  # preset
        if r < 0.55:
            words = [_ins(19, X, rng.randrange(256), rng.randrange(256))] if rng.random() < 0.5 else []
            return words + [_ins(32, 0, rng.randrange(4), 1)]  # set from register
        reg = rng.randrange(4)
        words = [_ins(32, reg, 0, 2)]  # read
        if rng.random() < 0.6:
            words += [_ins(18, RX, reg), _ins(7)] if reg != RX else [_ins(7)]
        elif rng.random() < 0.3 and self.owned:
            words += [_ins(18, TX, reg), _ins(8, rng.choice(self.owned), 0, 1)]  # OUT
        return words

    def lstat_block(self) -> list[int]:
        rng = self.rng
        reg = rng.randrange(4)
        words = [_ins(33, reg)]
        r = rng.random()
        if r < 0.55:
            words += [_ins(18, RX, reg), _ins(7)] if reg != RX else [_ins(7)]
        elif r < 0.75:
            words.append(random_gen.instruction(26, reg, 0, 0) | rng.randrange(64))  # JZ
        return words

    def classic_block(self) -> list[int]:
        rng = self.rng
        c = rng.randrange(32) | (0x40 if rng.random() < 0.55 else 0)
        if len(self.owned) < 2:
            c &= ~8
        words = [_ins(17, rng.choice((1, 2, 3, 8, rng.randint(1, 32))), rng.choice((1, 1, 2, 3)), c)]
        if c & 16 and rng.random() < 0.4:
            words.append(_ins(7))
        if rng.random() < 0.75:
            words += self.ltim_words()  # restart the ticker (a classic XFER stops it)
        return words

    def manchester_block(self) -> list[int]:
        """A drive-only Manchester XFER followed by a pin write near the next mid-bit tick."""
        rng = self.rng
        self.cfg = LineConfig(2, rng.choice((0, 0, rng.randint(1, 8))), False, rng.random() < 0.5,
                              False, False, rng.randrange(2))
        words = [self.cfg.word()]
        words.append(xline(draw_bits(rng, max(self.period, 1), self.frac), rng.random() < 0.5, True, False,
                           rng.random() < 0.5))
        wait = rng.randint(0, max(0, min(self.period, 20) - 1))
        if wait:
            words.append(_imm(4, wait))
        pin = rng.choice(self.owned) if self.owned else rng.randrange(8)
        words.append(rng.choice((_ins(8, pin, 0, rng.randrange(2)), _imm(2, self.base.subset()),
                                 _ins(17, rng.randint(1, 4), 1, rng.randrange(8)))))
        return words

    def invalid(self) -> tuple[int, int | None]:
        """A deliberately invalid line-unit encoding: (word, 1) when the encoding alone is
        invalid, (word, None) when the fault depends on state (ticker stopped, ownership)."""
        rng = self.rng
        choices = [
            (ltim(255, rng.randint(1, 255), rng.randrange(256)), 1),
            (_imm(31, 3 | rng.randrange(1 << 11) & ~3), 1),                     # line code 3
            (_imm(31, rng.randint(1, 0x1FFF) << 11 | rng.randrange(1 << 11)), 1),  # bits 23..11
            (_imm(31, 0x0C | rng.randrange(4) | rng.randrange(8) << 7), 1),     # either, run 1
            (_ins(32, rng.randrange(256), rng.randrange(256), 0), 1),           # CRC c = 0
            (_ins(32, rng.randrange(256), rng.randrange(256), rng.randint(4, 255)), 1),
            (_ins(32, rng.randint(1, 255), rng.randrange(4), 1), 1),            # set, a != 0
            (_ins(32, 0, rng.randint(4, 255), 1), 1),                           # set, b > 3
            (_ins(32, rng.randint(4, 255), 0, 2), 1),                           # read, a > 3
            (_ins(32, rng.randrange(4), rng.randint(1, 255), 2), 1),            # read, b != 0
            (_ins(32, rng.randint(1, 255), rng.randrange(4), 3), 1),            # preset, a != 0
            (_ins(32, 0, rng.randint(4, 255), 3), 1),                           # preset, b > 3
            (_ins(33, rng.randint(4, 255)), 1),                                 # LSTAT a > 3
            (_ins(33, rng.randrange(4), rng.randint(1, 255)), 1),
            (_ins(33, rng.randrange(4), 0, rng.randint(1, 255)), 1),
            (_ins(17, rng.randint(1, 32), rng.randint(1, 255), 0x20 | rng.randrange(0x20, 0x60, 4) & 0x5C), 1),
            (_ins(17, rng.randint(1, 32), 0, 0x20 | rng.randint(1, 3) | rng.randrange(0, 0x60, 4) & 0x5C), 1),
            (_ins(17, 0, 0, 0x20 | rng.randrange(0, 0x60, 4) & 0x5C), 1),       # line, a = 0
            (_ins(17, rng.randint(33, 255), 0, 0x20 | rng.randrange(0, 0x60, 4) & 0x5C), 1),
            (_ins(17, rng.randint(1, 32), rng.randrange(256), 0x80 | rng.randrange(0x80)), 1),  # c bit 7
            (_imm(rng.randint(34, 255), rng.randrange(1 << 24)), 1),           # opcodes 34..255
            (xline(rng.randint(1, 32), rng.random() < 0.5, True, True, rng.random() < 0.5), None),
            (xline(rng.randint(1, 32), False, True, False, False), None),       # state-dependent
        ]
        return rng.choice(choices)

    # -- program
    def generate(self, length: int) -> EngineSpec:
        rng = self.rng
        words: list[int] = []
        expect: dict[int, int] = {}
        owned = self.owned
        words.append(_imm(3, sum(1 << p for p in owned if rng.random() < 0.9)))  # DIR
        words.append(_imm(2, self.base.subset()))  # SET
        words.append(self.pins_word())
        words += self.lcfg_block()
        words += self.ltim_words()
        words.append(_ins(32, 0, rng.randrange(4), 3))
        if rng.random() < 0.6:
            words += [_ins(19, X, rng.randrange(256), rng.randrange(256)), _ins(32, 0, X, 1)]
        if rng.random() < 0.2:
            words.append(_imm(12, rng.choice((rng.randint(1, 40), rng.randint(100, 3000)))))  # LIMIT
        loop_start = len(words)
        body = length - 1
        weights = {"xfer": 34, "lcfg": 7, "ltim": 6, "crc": 8, "lstat": 5, "classic": 5, "pins": 2,
                   "manchester": 3, "base": 20, "wait": 3, "waitpin": 2}
        if self.profile == "line-dense":
            weights.update(xfer=50, base=8, wait=1)
        kinds, w = list(weights), list(weights.values())
        while len(words) < body:
            if rng.random() < self.fault_rate:
                word, code = self.invalid()
                if code is not None:
                    expect[len(words)] = code
                words.append(word)
                continue
            kind = rng.choices(kinds, weights=w)[0]
            if kind == "xfer":
                block = self.xfer_block()
            elif kind == "lcfg":
                block = self.lcfg_block()
            elif kind == "ltim":
                block = self.ltim_words()
            elif kind == "crc":
                block = self.crc_block()
            elif kind == "lstat":
                block = self.lstat_block()
            elif kind == "classic":
                block = self.classic_block()
            elif kind == "pins":
                block = [self.pins_word()]
            elif kind == "manchester":
                block = self.manchester_block()
            elif kind == "wait":
                block = [_imm(4, rng.choice((1, 2, 3, rng.randint(4, 40))))]
            elif kind == "waitpin":
                block = [_ins(13, rng.randrange(8), rng.randrange(2))]
            else:
                block = self.base.instruction(len(words), body)
                for offset, word in enumerate(block):
                    if word >> 24 == 29:
                        expect[len(words) + offset] = word & 0xFF
                    elif self.base.byte_lane and word >> 24 in (24, 25) and word & 7:
                        expect[len(words) + offset] = 1
            words += block
        words = words[:body]
        expect = {pc: code for pc, code in expect.items() if pc < body}
        tail = rng.random()
        if tail < 0.80:
            words.append(_imm(5, loop_start if rng.random() < 0.7 else rng.randrange(len(words))))
        elif tail < 0.93:
            words.append(_ins(1))  # HALT
        else:
            words.append(_ins(0))  # fall off the image: fault 2
        return EngineSpec(words, self.ownership, self.plan.open_drain[self.engine], expect)


# ---------------------------------------------------------------- environment
def draw_transmitter(rng: random.Random, data: int, pair: int | None, period: int | None,
                     frac: int | None) -> dict:
    """An environment line transmitter (see LineEnvironment)."""
    stuff = rng.choice((None, None, [False, 6], [True, 5], [rng.random() < 0.5, rng.randint(2, 8)]))
    return {"data": data, "pair": -1 if pair is None else pair,
            "half": period if period else rng.choice((1, 2, 3, 4, rng.randint(5, 40))),
            "frac": frac if frac is not None else rng.choice((0, 0, rng.randrange(256))),
            "code": rng.choice((0, 1, 1)), "stuff": stuff, "se0_end": rng.random() < 0.6,
            "idle": rng.randrange(2), "bits": [rng.randint(1, 8), rng.randint(9, 72)],
            "gap": [rng.randint(0, 20), rng.randint(20, 400)], "seed": rng.getrandbits(32)}


class EnvTransmitter:
    """Sends frames on an environment pin: random data bits, bit stuffing (runs of 1s or of
    either polarity), NRZ or NRZI from the idle level, the complement on the pair pin, then an
    SE0 end (both pins low for two bit times, then one idle bit) or plain idle, then a gap.
    Half-bit durations follow the line unit's ticker: P cycles, plus one when an 8-bit
    accumulator that adds Q per half-bit carries."""

    def __init__(self, spec: dict) -> None:
        self.spec = spec
        self.rng = random.Random(spec["seed"])
        self.segments: deque[tuple[int, int, int]] = deque()  # (data level, pair level, cycles)
        self.acc = 0
        self.level = spec["idle"]
        self.data_level, self.pair_level = spec["idle"], 1 - spec["idle"]
        self.left = 0

    def _half(self) -> int:
        total = self.acc + self.spec["frac"]
        self.acc = total & 255
        return max(1, self.spec["half"] + (total >> 8))

    def _frame(self) -> None:
        rng, spec = self.rng, self.spec
        bits = [rng.randrange(2) for _ in range(rng.randint(*spec["bits"]))]
        if spec["stuff"]:
            either, n = spec["stuff"]
            bits = stuff_either(n, bits) if either else stuff_ones(n, bits)
        levels = nrzi_encode(self.level, bits) if spec["code"] == 1 else bits
        for level in levels:
            self.segments.append((level, 1 - level, self._half() + self._half()))
        if spec["se0_end"] and rng.random() < 0.8:
            self.segments.append((0, 0, 2 * (self._half() + self._half())))
            self.segments.append((spec["idle"], 1 - spec["idle"], self._half() + self._half()))
            self.level = spec["idle"]
        else:
            self.level = levels[-1] if levels else self.level
        self.segments.append((self.level, 1 - self.level, rng.randint(*spec["gap"]) + 1))

    def apply(self, external: int) -> int:
        if self.left == 0:
            if not self.segments:
                self._frame()
            self.data_level, self.pair_level, self.left = self.segments.popleft()
        self.left -= 1
        data, pair = self.spec["data"], self.spec["pair"]
        external = external & ~(1 << data) | self.data_level << data
        if pair >= 0:
            external = external & ~(1 << pair) | self.pair_level << pair
        return external


class LineEnvironment:
    """External pad levels of a line case (``h.pins`` supplier). Deterministic given the
    case's ``env`` and the chip's outputs, which the lockstep check keeps equal between the
    DUT and the model. Driven pads always show the chip's value (``pad_value``)."""

    def __init__(self, spec: dict) -> None:
        self.spec = spec
        self.rng = random.Random(spec["seed"])
        self.levels = spec["levels"]
        self.toggle = spec["toggle"]
        self.links = spec["links"]
        self.se0 = spec["se0"]
        self.transmitters = [EnvTransmitter(t) for t in spec["transmitters"]]
        self.history: deque[int] = deque(maxlen=16)
        self.se0_until = -1
        self.se0_pair: list[int] = []

    def __call__(self, cycle: int, out: Any) -> int:
        rng = self.rng
        for pin, rate in enumerate(self.toggle):
            if rate and rng.random() < rate:
                self.levels ^= 1 << pin
        pads = pad_value(out, self.levels)
        self.history.append(pads)
        external = self.levels
        for pin, kind, a, b, delay in self.links:
            source = self.history[-1 - delay] if len(self.history) > delay else self.history[0]
            value = source >> a & 1
            if kind == "not":
                value ^= 1
            elif kind == "and":
                value &= source >> b & 1
            external = external & ~(1 << pin) | value << pin
        for transmitter in self.transmitters:
            external = transmitter.apply(external)
        if self.se0["rate"] and cycle >= self.se0_until and rng.random() < self.se0["rate"]:
            self.se0_until = cycle + rng.randint(*self.se0["length"])
            self.se0_pair = rng.choice(self.se0["pairs"])
        if cycle < self.se0_until:
            for pin in self.se0_pair:
                external &= ~(1 << pin)
        return pad_value(out, external)


# ---------------------------------------------------------------- cases
def make_plan(rng: random.Random, engines: int) -> Plan:
    r = rng.random()
    if r < 0.6:  # engine k owns pins 2k and 2k+1
        ownership = [3 << 2 * e for e in range(engines)]
    elif r < 0.8:  # random disjoint ownership (upstream style)
        owner = [rng.choice([*range(engines), None]) for _ in range(8)]
        ownership = [sum(1 << p for p in range(8) if owner[p] == e) for e in range(engines)]
    else:  # a wide engine with three or four pins, others with one or none
        pins = list(range(8))
        rng.shuffle(pins)
        wide = rng.randrange(engines)
        ownership = [0] * engines
        count = rng.choice((3, 4))
        for p in pins[:count]:
            ownership[wide] |= 1 << p
        for p in pins[count:]:
            e = rng.choice([*range(engines), None])
            if e is not None and e != wide and not ownership[e]:
                ownership[e] |= 1 << p
    open_drain = [sum(1 << p for p in range(8) if own >> p & 1 and rng.random() < 0.15) for own in ownership]
    data_pin: list[int | None] = []
    pair_pin: list[int | None] = []
    for own in ownership:
        pins = [p for p in range(8) if own >> p & 1]
        rng.shuffle(pins)
        data_pin.append(pins[0] if pins else None)
        pair_pin.append(pins[1] if len(pins) > 1 else None)
    free = [p for p in range(8) if not any(own >> p & 1 for own in ownership)]
    env_pins, env_pairs = [], []
    candidates = free[:] if free else []
    rng.shuffle(candidates)
    while len(candidates) >= 2 and rng.random() < 0.8:
        a, b = candidates.pop(), candidates.pop()
        env_pins.append(a)
        env_pairs.append((a, b))
    if candidates and rng.random() < 0.5:
        env_pins.append(candidates.pop())
    if not env_pins and rng.random() < 0.5:
        # The environment also drives owned pins while their engine leaves them undriven.
        a, b = rng.sample(range(8), 2)
        env_pins.append(a)
        env_pairs.append((a, b))
    return Plan(ownership, open_drain, data_pin, pair_pin, env_pins, env_pairs)


def make_env(rng: random.Random, plan: Plan, periods: dict[int, tuple[int, int]]) -> dict:
    links = []
    for pin in range(8):
        if pin in plan.env_pins or rng.random() > 0.3:
            continue
        kind = rng.choice(("copy", "copy", "not", "and"))
        a, b = rng.randrange(8), rng.randrange(8)
        links.append([pin, kind, a, b, rng.choice((0, 0, 1, 2, 3, 7))])
    transmitters = []
    pair_of = dict(plan.env_pairs)
    for data in plan.env_pins:
        period = frac = None
        if periods and rng.random() < 0.6:
            period, frac = periods[rng.choice(sorted(periods))]
        transmitters.append(draw_transmitter(rng, data, pair_of.get(data), period, frac))
    pairs = [list(p) for p in plan.env_pairs] + [[plan.data_pin[e], plan.pair_pin[e]]
                                                  for e in range(len(plan.data_pin))
                                                  if plan.data_pin[e] is not None and plan.pair_pin[e] is not None]
    if not pairs:
        pairs = [sorted(random.Random(rng.getrandbits(32)).sample(range(8), 2))]
    return {"seed": rng.getrandbits(32), "levels": rng.randrange(256),
            "toggle": [rng.choice((0.0, 0.0, 0.002, 0.01, 0.05, 0.2)) for _ in range(8)],
            "links": links, "transmitters": transmitters,
            "se0": {"rate": rng.choice((0.0, 0.001, 0.003, 0.01)), "pairs": pairs,
                    "length": [rng.randint(2, 20), rng.randint(20, 200)]}}


def line_program(rng: random.Random, config: Any, plan: Plan, engine: int, fault_rate: float,
                 profile: str, length: int | None = None) -> EngineSpec:
    generator = LineProgramGenerator(rng, config, plan, engine, fault_rate, profile)
    if length is None:
        if profile == "line-dense":
            length = rng.randint(40, config.program_words)
        else:
            length = rng.choice((rng.randint(10, 24), rng.randint(20, 48), rng.randint(40, config.program_words)))
    return generator.generate(length)


def make_line_case(seed: int, index: int, config: Any, *, cycles: int, profile: str = "line") -> LineCase:
    if profile not in PROFILES:
        raise ValueError(f"unknown line profile {profile!r}")
    rng = random.Random((seed * 1_000_003 + index) ^ 0x11AE2026)
    engines = config.engines
    plan = make_plan(rng, engines)
    fault_rate = (rng.choice((0.08, 0.12, 0.2)) if profile == "line-faulty"
                  else rng.choice((0.0, 0.01, 0.02, 0.04)))
    specs: list[EngineSpec | None] = []
    periods: dict[int, tuple[int, int]] = {}
    for e in range(engines):
        if rng.random() < 0.12:
            specs.append(None)
            continue
        if rng.random() < 0.85:
            spec = line_program(rng, config, plan, e, fault_rate, profile)
            for word in spec.words:
                if word >> 24 == 30 and word & 255:  # remember the engine's first ticker rate
                    periods.setdefault(e, (word & 255, word >> 8 & 255))
        else:
            length = rng.choice([rng.randint(3, 10), rng.randint(8, 24), rng.randint(24, config.program_words)])
            spec = ProgramGenerator(rng, config, plan.ownership[e], fault_rate).generate(length)
            spec.open_drain = plan.open_drain[e]
        specs.append(spec)
    loaded = [e for e in range(engines) if specs[e] is not None]
    prefill = [[rng.getrandbits(32) for _ in range(rng.randint(0, config.fifo_words))] for _ in range(engines)]
    routes = [[rng.randrange(engines), rng.randrange(engines), rng.randint(1, 12)]
              for _ in range(rng.choice([0, 1, 1, 2, 3]))]
    triggers = [[rng.randrange(engines), rng.randrange(8) | rng.randrange(4) << 3 | 32]
                for _ in range(rng.choice([0, 1, 2]))]
    start_mask = sum(1 << e for e in loaded if rng.random() < 0.92)
    ops: list[list[Any]] = []
    budget = 0
    dense = profile == "line-dense"
    while budget < cycles:
        r = rng.random()
        if r < 0.05:
            e = rng.randrange(engines)
            spec = line_program(rng, config, plan, e, fault_rate, profile, length=rng.randint(8, 32))
            op = ["reload", e, asdict(spec)]
        elif r < 0.13:
            op = ["revive", rng.randrange(1, 1 << engines)]
        else:
            op = random_gen.host_op(rng, config, plan.ownership)
            while dense and op[0] == "idle" and rng.random() < 0.85:
                op = random_gen.host_op(rng, config, plan.ownership)
        ops.append(op)
        budget += {"idle": op[1] if op[0] == "idle" else 0, "tx": 22, "rx": 24, "status": 34, "cmd": 11,
                   "partial": 14, "partial_read": 14, "reload": 300}.get(op[0], 10)
    if rng.random() < 0.1:
        ops.append(["deselect", rng.randint(1, 3)])
    env = make_env(rng, plan, periods)
    case = LineCase(seed, index, specs, prefill, routes, triggers, start_mask, ops, pin_seed=rng.getrandbits(32),
                    toggle=0.0, cycles=cycles, env=env, profile=profile)
    random_gen.extend_case(case, config, plan.ownership, random.Random((seed * 1_000_003 + index) ^ 0x6A9C2027))
    return case


# ---------------------------------------------------------------- execution
async def run_line_case(h: Any, case: Case, coverage: Any = None) -> None:
    """``random_gen.run_case`` with the line environment on the pads (plain cases run upstream)."""
    if not isinstance(case, LineCase):
        await UPSTREAM_RUN_CASE(h, case, coverage)
        return
    h.context = case.describe
    await h.reset(2)
    h.pins = LineEnvironment(case.env)
    if coverage is not None:
        coverage.attach(h, case)
    for engine, payload in case.triggers:
        await h.command(SELECT, engine)
        await h.command(TRIGGER, payload)
    for source, dest, count in case.routes:
        await h.command(ROUTE, source | dest << 2 | 16 | count << 5)
    for e, spec in enumerate(case.engines):
        if spec is not None:
            await h.load(e, spec.words, ownership=spec.ownership, open_drain=spec.open_drain)
        if case.prefill[e]:
            await h.command(SELECT, e)
            for word in case.prefill[e]:
                await h.try_write(2, word, max_wait=4)
        if case.start_mask >> e & 1:
            await h.command(START, 1 << e)
    start = h.cycle
    noise = random.Random(case.pin_seed ^ 0x5A5A)
    for op in case.ops:
        if h.cycle - start >= case.cycles and op[0] != "deselect":
            continue
        await random_gen.execute(h, op, noise, coverage)
    await h.command(STOP, (1 << h.model.config.engines) - 1)
    for e in range(h.model.config.engines):
        await h.command(SELECT, e)
        for selection in range(8):
            await h.status(selection)
        for _ in range(h.model.config.fifo_words):
            if await h.try_read(3, max_wait=3) is None:
                break
    if coverage is not None:
        coverage.detach(h)
    h.context = None


UPSTREAM_RUN_CASE = random_gen.run_case


def install() -> None:
    """Route ``random_gen.run_case`` (used by ``random_gen.minimize``) through run_line_case."""
    random_gen.run_case = run_line_case


def copy_case(case: Case) -> Case:
    return copy.deepcopy(case)


# ---------------------------------------------------------------- negative controls
def inject_model_defect(model: Any, name: str) -> None:
    """Seed a defect into the reference model (negative control; the DUT is unchanged).

    ``line-carry``  LTIM ignores the fraction: the ticker never adds the carry cycle.
    ``line-crc``    the LSB-first CRC step takes its feedback from crc bit 1 instead of bit 0
                    (the Hardcaml ``crc_tap`` defect).
    """
    from model import line_unit

    if name == "line-carry":
        body = model._line_instruction_body

        def ltim_without_fraction(e, line, level_before, tx_available, rx_space, op, a, b, c, imm24):  # noqa: ANN001, ANN202
            result = body(e, line, level_before, tx_available, rx_space, op, a, b, c, imm24)
            if op == 30 and not e.fault:
                line.frac = 0
            return result

        model._line_instruction_body = ltim_without_fraction
    elif name == "line-crc":
        def feed_wrong_tap(line, bit, msb):  # noqa: ANN001, ANN202
            poly = line_unit.preset_polynomial(line.preset, msb)
            if msb:
                line.crc = line_unit.crc_step(line.crc, bit, poly, True)
            else:
                feedback = (line.crc >> 1 & 1) ^ bit
                line.crc = (line.crc >> 1) ^ (poly if feedback else 0)

        model._feed = feed_wrong_tap
    else:
        raise ValueError(f"unknown model defect {name!r}")
