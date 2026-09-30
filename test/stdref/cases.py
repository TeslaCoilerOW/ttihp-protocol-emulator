# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Seeded random line programs with standard-encoded stimuli (stdref module).

A case gives one to four engines a straight-line program: engine e owns pin
2e (data) and pin 2e+1 (pair).  Each engine runs one scenario:

``tx``     drive-only line XFERs (NRZ, NRZI, Manchester; stuffing, pair, CRC).
``rx``     sample-only line XFERs; the testbench plays a line built by the
           standard encoders of line_std_ref (stuffing, NRZI, SE0, wrong stuff
           bits) on the engine's pins, aligned to the contract's tick schedule.
           A cell's level changes right after the previous sample; with
           ``strobe`` (half of the rx engines) the pins carry the complement
           in every cycle except the predicted sample cycle.
``loop``   drive-and-sample XFERs on the engine's own data pin (P >= 3), with
           the arbitration monitor and, sometimes, a rival node that shares a
           prefix and then wins (wired AND).
``mixed``  drive-only XFERs plus classic XFERs with the CRC bit, LTIM/LCFG
           changes, LSTAT and CRC reads, WAITs, and sometimes an invalid
           encoding at the end (fault 1).

Programs end with pushes of the CRC, LSTAT and rx and a HALT.  At most 8
words are pushed and at most 8 PULLs are used (queues of 8 words), so no
instruction blocks.  ``Transmitter`` is the testbench side: ``program.ProgramRef``
calls it when an LCFG or a line XFER issues and it builds the stimulus from
the standards' encoders; the cocotb module plays the same waveform.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from _ref import ref
from program import encode

LINE, CRCBIT, MSB, DRIVE, SAMPLE = 1 << 5, 1 << 6, 1 << 2, 1 << 3, 1 << 4
TX, RX, X, Y = 0, 1, 2, 3


def ins(m: str, a: int = 0, b: int = 0, c: int = 0) -> int:
    return encode(m, a, b, c)


def imm(m: str, value: int, a: int = 0) -> int:
    return encode(m, a=a, imm=value)


@dataclass
class RxPlan:
    bits: list[int]
    se0_at: Optional[int] = None          # data bit index replaced by SE0
    stuff_error: bool = False             # flip a stuff cell (the last one by default)
    error_cell: int = -1                  # which stuff cell to flip (index among stuff cells)
    trailing: bool = True                 # send a due trailing stuff bit after the last data bit


@dataclass
class RivalPlan:
    bits: list[int]
    raw: bool = False                     # send ``bits`` as line cells (no stuffing)


@dataclass
class EngineCase:
    engine: int
    scenario: str
    words: list[int]
    owned: int
    tx_words: list[int]
    rx_plans: list[RxPlan] = field(default_factory=list)
    rival: Optional[RivalPlan] = None
    notes: list[str] = field(default_factory=list)
    strobe: bool = False                  # rx: a cell's level only in its sample cycle

    @property
    def data_pin(self) -> int:
        return 2 * self.engine

    @property
    def pair_pin(self) -> int:
        return 2 * self.engine + 1


@dataclass
class Case:
    seed: int
    engines: list[EngineCase]


class Transmitter:
    """Testbench line for one engine: stimulus pins and the rival node."""

    def __init__(self, case: EngineCase, interp: ref.Interpretation) -> None:
        self.case = case
        self.interp = interp
        self.plans = list(case.rx_plans)
        self.rival_plan = case.rival
        self.cfg = ref.RESET_LCFG
        self.state = ref.RunState()
        self.level = 0
        self.queue: list[tuple[int, int]] = []       # (rx level, pair level) per sample
        self.pins = ref.Pins(case.pair_pin, case.data_pin, case.data_pin)
        self.idle = (1, 0) if case.scenario == "rx" else (1, 1)
        self.current = self.idle
        self.horizon = -1 << 60
        self.values: dict[int, tuple[int, int]] = {}
        self.rival: dict[int, int] = {}
        self.rival_used = False
        self.cells_sent: list[list[tuple[int, bool]]] = []

    # -- ProgramRef hooks --------------------------------------------------
    def on_lcfg(self, cycle: int, cfg: ref.Lcfg) -> None:
        self.cfg = cfg
        self.state = ref.RunState()
        self.level = cfg.initial_level

    def on_line_xfer(self, cycle: int, a: int, c: int, pins: ref.Pins, unit: ref.LineUnitRef) -> None:
        mode = ref.XferMode.decode(a, c)
        self.pins = pins
        if mode.sample and not mode.drive and self.case.scenario == "rx" and self.plans:
            self._queue_rx(self.plans.pop(0))
        if mode.drive and mode.sample and self.rival_plan is not None and not self.rival_used:
            self.rival_used = True
            self._place_rival(cycle, unit)

    def _queue_rx(self, plan: RxPlan) -> None:
        cfg = self.cfg
        bits = plan.bits
        if cfg.stuffing:
            cells, state = ref.stuff(bits, cfg.rule, self.state, trailing=plan.trailing)
        else:
            cells, state = [(b, False) for b in bits], self.state
        if plan.stuff_error:
            stuffed = [i for i, (_, s) in enumerate(cells) if s]
            if stuffed:
                i = stuffed[plan.error_cell]
                cells[i] = (cells[i][0] ^ 1, True)
        if plan.se0_at is not None:
            data_seen = 0
            cut = len(cells)
            for i, (_, s) in enumerate(cells):
                if not s:
                    if data_seen == plan.se0_at:
                        cut = i
                        break
                    data_seen += 1
            cells = cells[:cut]
        self.state = state
        self.cells_sent.append(cells)
        for bit, _ in cells:
            if cfg.code == ref.NRZI:
                self.level = self.level if bit else self.level ^ 1
            else:
                self.level = bit
            self.queue.append((self.level, self.level ^ 1))
        if plan.se0_at is not None:
            self.queue.append((0, 0))

    def _place_rival(self, issue: int, unit: ref.LineUnitRef) -> None:
        cfg = self.cfg
        bits = self.rival_plan.bits
        if cfg.stuffing and not self.rival_plan.raw:
            cells, _ = ref.stuff(bits, cfg.rule, ref.RunState(), trailing=True)
        else:
            cells = [(b, False) for b in bits]
        ticker = unit.ticker
        k = ticker.first_after(issue, 0, self.interp.first_tick_strict)
        for j, (bit, _) in enumerate(cells):
            start = ticker.tick(k + 2 * j) + ref.OUT_LATENCY
            end = ticker.tick(k + 2 * j + 2) + ref.OUT_LATENCY
            for n in range(start, end):
                self.rival[n] = bit

    # -- pad values ----------------------------------------------------------
    def sample_request(self, m: int) -> tuple[int, int]:
        """(rx, pair) levels in pad cycle m; a new m past the horizon takes the next cell."""
        if m in self.values:
            return self.values[m]
        if m > self.horizon:
            level = self.queue.pop(0) if self.queue else self.current
            self.current = level
            start = self.horizon + 1 if self.horizon > -1 << 59 else m
            # strobe: the cycles before the sample carry the complement, so a
            # sample taken one cycle early or late reads the wrong level
            before = (level[0] ^ 1, level[1] ^ 1) if self.case.strobe else level
            for n in range(start, m):
                self.values[n] = before
            self.values[m] = level
            self.horizon = m
            return level
        return self.current

    def played(self, n: int) -> tuple[int, int]:
        """Stimulus levels in pad cycle n, for the testbench (after prediction)."""
        if n in self.values:
            return self.values[n]
        if n > self.horizon:
            return self.current
        return self.idle

    def rival_level(self, n: int) -> int:
        return self.rival.get(n, 1)


# ---------------------------------------------------------------------------
# generator
# ---------------------------------------------------------------------------

def _lcfg(rng: random.Random, scenario: str, **force) -> ref.Lcfg:
    if scenario in ("tx", "mixed"):
        code = rng.choice((ref.NRZ, ref.NRZ, ref.NRZI, ref.NRZI, ref.MANCHESTER))
    elif scenario == "loop":
        code = rng.choice((ref.NRZ, ref.NRZ, ref.NRZI))
    else:
        code = rng.choice((ref.NRZ, ref.NRZI))
    stuffing = rng.random() < 0.6
    either = rng.random() < 0.5
    run_length = rng.choice((1, 2, 3, 4, 5, 6, 7, 8)) if stuffing else rng.randrange(1, 9)
    if either and run_length == 1:
        run_length = rng.randrange(2, 9)
    pair = rng.random() < 0.5
    arbitration = scenario == "loop" and code == ref.NRZ and rng.random() < 0.8
    se0 = scenario == "rx" and rng.random() < 0.35
    if se0:
        pair = True
    cfg = ref.Lcfg(code, stuffing, either, run_length, pair, arbitration, se0, rng.randrange(2))
    return ref.Lcfg(**{**cfg.__dict__, **force}) if force else cfg


def _ltim(rng: random.Random, scenario: str) -> ref.Ltim:
    if scenario == "loop":
        period = rng.choice((3, 3, 4, 5, 7, 12))
    elif scenario == "rx":
        period = rng.choice((1, 2, 2, 3, 4, 6, 9))
    else:
        period = rng.choice((1, 1, 2, 2, 3, 4, 5, 8, 16))
    fraction = rng.choice((0, 0, 0, 1, 85, 128, 171, 255))
    delay = rng.choice((0, 0, 1, 2, 5, 17))
    return ref.Ltim(period, fraction, delay)


def _count(rng: random.Random) -> int:
    return rng.choice((1, 2, 3, 5, 7, 8, 11, 13, 16, 16, 24, 31, 32, 32))


def _data_bits(rng: random.Random, n: int) -> list[int]:
    style = rng.random()
    if style < 0.3:     # long runs to exercise stuffing
        bits, level = [], rng.randrange(2)
        while len(bits) < n:
            bits += [level] * rng.randrange(1, 10)
            level ^= 1
        return bits[:n]
    if style < 0.4:
        return [1] * n
    return [rng.randrange(2) for _ in range(n)]


def _load_word(words: list[int], rng: random.Random, value: int) -> None:
    """tx := value with LOAD/SHL/OR (x as scratch)."""
    words.append(imm("LOAD", value >> 16, a=TX))
    words.append(ins("SHL", TX, 0, 16))
    words.append(imm("LOAD", value & 0xFFFF, a=X))
    words.append(ins("OR", TX, X))


def make_engine(rng: random.Random, engine: int, scenario: str) -> EngineCase:
    d, q = 2 * engine, 2 * engine + 1
    owned = (1 << d) | (1 << q)
    words: list[int] = []
    tx_words: list[int] = []
    pushes = 0
    case = EngineCase(engine, scenario, words, owned, tx_words)
    cfg = _lcfg(rng, scenario)
    ltim = _ltim(rng, scenario)
    rx_pin = d
    words.append(imm("PINS", ref.Pins(q, d, rx_pin).encode()))
    words.append(imm("SET", (rng.randrange(4) << d) & owned))
    if scenario == "rx":
        words.append(imm("DIR", 0))
    else:
        words.append(imm("DIR", owned if rng.random() < 0.8 else 1 << d))
    words.append(imm("LCFG", cfg.encode()))
    words.append(imm("LTIM", ltim.encode()))
    words.append(ins("CRC", 0, rng.randrange(4), 3))
    words.append(imm("LOAD", rng.randrange(1 << 16), a=X))
    words.append(ins("CRC", 0, X, 1))
    segments = rng.randrange(1, 6)
    pulls = 0
    manchester_pending = False
    for segment in range(segments):
        if scenario == "rx":
            count = _count(rng)
            plan = RxPlan(_data_bits(rng, count))
            if cfg.se0_end and rng.random() < 0.4:
                plan.se0_at = rng.randrange(0, count + 1) if rng.random() < 0.8 else None
            if cfg.stuffing and rng.random() < 0.15:
                # runs of 1s with run length 1: after a wrong stuff bit every
                # cell of an idle (1) line is a stuff cell, the XFER would not end
                plan.stuff_error = cfg.either or cfg.run_length > 1
            case.rx_plans.append(plan)
            c = LINE | SAMPLE | (MSB if rng.random() < 0.5 else 0) | (CRCBIT if rng.random() < 0.6 else 0)
            words.append(ins("XFER", count, 0, c))
            if plan.se0_at is not None:
                # after an SE0 end: read the flags, then start a fresh line
                if pushes < 5 and rng.random() < 0.7:
                    words.append(ins("LSTAT", RX))
                    words.append(ins("PUSH"))
                    pushes += 1
                cfg = _lcfg(rng, scenario)
                words.append(imm("LCFG", cfg.encode()))
            continue
        # data for a driving XFER
        if pulls < 8 and rng.random() < 0.5:
            value = rng.randrange(1 << 32)
            tx_words.append(value)
            words.append(ins("PULL"))
            pulls += 1
        else:
            value = rng.randrange(1 << 32)
            if rng.random() < 0.3:
                value = rng.choice((0xFFFFFFFF, 0, 0x0000FFFF, 0xFFFF0000, 0xAAAAAAAA, 0x7FFFFFFF))
            _load_word(words, rng, value)
        count = _count(rng)
        msb = rng.random() < 0.5
        c = LINE | DRIVE | (MSB if msb else 0) | (CRCBIT if rng.random() < 0.6 else 0)
        if scenario == "loop":
            c |= SAMPLE
            if cfg.arbitration and case.rival is None and segment == 0 and rng.random() < 0.8:
                ours = ref.word_to_bits(value, count, msb)
                ones = [i for i, b in enumerate(ours) if b]
                if ones:
                    m = rng.choice(ones)
                    rival = ours[:m] + [0] + [rng.randrange(2) for _ in range(count - m - 1)]
                    case.rival = RivalPlan(rival)
                    case.notes.append(f"rival wins at data bit {m}")
        words.append(ins("XFER", count, 0, c))
        manchester_pending = cfg.code == ref.MANCHESTER
        r = rng.random()
        if r < 0.15 and pushes < 5:
            words.append(ins("TIME", RX))
            words.append(ins("PUSH"))
            pushes += 1
        elif r < 0.25 and pushes < 5:
            words.append(ins("LSTAT", RX))
            words.append(ins("PUSH"))
            pushes += 1
        elif r < 0.35 and pushes < 5:
            words.append(ins("CRC", RX, 0, 2))
            words.append(ins("PUSH"))
            pushes += 1
        elif r < 0.45:
            words.append(imm("WAIT", rng.randrange(0, 40)))
        if scenario == "mixed":
            r = rng.random()
            if r < 0.2:
                # classic XFER with the CRC bit (sample only, on the pair pin clock); stops the ticker
                words.append(imm("WAIT", 3 * ltim.period + 4))
                words.append(imm("PINS", ref.Pins(q, d, d).encode()))
                classic_c = 0x50 | rng.choice((0, 1, 2, 3)) | (MSB if rng.random() < 0.5 else 0)
                words.append(ins("XFER", rng.randrange(1, 9), rng.randrange(1, 4), classic_c))
                ltim = _ltim(rng, scenario)
                words.append(imm("LTIM", ltim.encode()))
                manchester_pending = False
            elif r < 0.35:
                if manchester_pending:
                    words.append(imm("WAIT", 3 * ltim.period + 4))
                cfg = _lcfg(rng, scenario)
                words.append(imm("LCFG", cfg.encode()))
                manchester_pending = False
            elif r < 0.45:
                if manchester_pending:
                    words.append(imm("WAIT", 3 * ltim.period + 4))
                ltim = _ltim(rng, scenario)
                words.append(imm("LTIM", ltim.encode()))
                manchester_pending = False
            elif r < 0.5:
                words.append(ins("CRC", 0, rng.randrange(4), 3))
    # epilogue
    if manchester_pending:
        words.append(imm("WAIT", 3 * ltim.period + 4))
    words.append(ins("CRC", RX, 0, 2))
    words.append(ins("PUSH"))
    words.append(ins("LSTAT", RX))
    words.append(ins("PUSH"))
    if scenario != "rx":
        words.append(ins("MOV", RX, TX))
        words.append(ins("PUSH"))
    else:
        words.append(ins("PUSH"))           # rx after the last sample
    if scenario == "mixed" and rng.random() < 0.3:
        words.append(rng.choice(_invalid_words(rng, ref.Pins(q, d, d))))
        case.notes.append("ends with an invalid encoding")
    words.append(ins("HALT"))
    assert len(words) <= 64, len(words)
    case.strobe = scenario == "rx" and rng.random() < 0.5
    return case


def _invalid_words(rng: random.Random, pins: ref.Pins) -> list[int]:
    return [
        imm("LTIM", ref.Ltim(255, rng.randrange(1, 256), 0).encode()),
        imm("LCFG", 3),
        imm("LCFG", 1 << (11 + rng.randrange(13))),
        imm("LCFG", 0b1100 | (rng.randrange(2) << 7)),
        ins("CRC", 0, 0, 0), ins("CRC", 0, 0, 4 + rng.randrange(252)), ins("CRC", 1, 0, 1),
        ins("CRC", 0, 1, 2), ins("CRC", 1, 0, 3), ins("CRC", 0, 4, 3),
        ins("LSTAT", 4), ins("LSTAT", 0, 1, 0), ins("LSTAT", 0, 0, 1),
        ins("XFER", 8, 0, 0x80 | LINE | DRIVE), ins("XFER", 8, 1, LINE | DRIVE),
        ins("XFER", 8, 0, LINE | DRIVE | 1), ins("XFER", 0, 0, LINE | DRIVE),
        ins("XFER", 33, 0, LINE | DRIVE),
        (34 + rng.randrange(222)) << 24,
    ]


def make_case(seed: int, *, scenarios: tuple[str, ...] = ("tx", "rx", "loop", "mixed")) -> Case:
    rng = random.Random(seed)
    engines = sorted(rng.sample(range(4), rng.choice((1, 2, 2, 3, 4))))
    built = []
    for e in engines:
        scenario = rng.choice(scenarios)
        while True:
            try:
                built.append(make_engine(rng, e, scenario))
                break
            except AssertionError:
                continue
    return Case(seed, built)
