# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Functional coverage of the line unit (docs/extension.md, docs/isa.md "Line-unit
extension") for the random tests and the random campaign.

``LineCoverage`` is a harness observer (``before(h, ui, pins)`` / ``after(h)``
around each ``Reference.tick``) for a ``LineReference`` model. It counts the
bins of ``all_bins()`` from the model's state before and after each edge (the
DUT is lockstep-equal on every public pin, so the bins describe the stimulus
both saw). It only reads state, except that it wraps the model's ``_feed``
method to see which CRC steps happen. An internal error is counted as a bin
(``line-coverage error ...``), never raised.

Decode check. For every line-unit instruction issue (LTIM, LCFG, CRC, LSTAT,
and XFER with c bit 5, 6 or 7) the observer classifies the encoding as valid
or invalid from the contract text (``contract_reasons``, written from
docs/isa.md, not from the model) and compares that with whether the model
faulted. A disagreement is counted as ``decode check: ...`` bins (none are
expected); since the DUT and the model agree on every cycle, a disagreement
means that both differ from this reading of the contract.

Unreachable bins are listed in ``UNREACHABLE`` with the reason;
``holes(bins)`` excludes them and ``report(...)`` prints them.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .line_unit import LineTransfer
from .reference import Transfer

MODES = ("drive", "sample", "drive+sample")
CODES = ("NRZ", "NRZI", "Manchester")
FEED_SOURCES = ("line drive", "line sample", "classic drive", "classic sample")
P_CLASSES = ("0 (stops)", "1", "2", "3..15", "16..127", "128..253", "254", "255")
Q_CLASSES = ("0", "1", "2..127", "128", "129..254", "255")
D_CLASSES = ("0 (first tick after P)", "1", "2..15", "16..254", "255")
LSTAT_FIELDS = ("SE0", "arbitration lost", "stuff error", "TX data", "RX space", "line level",
                "ticker running")
LINE_OPS = (30, 31, 32, 33)


def p_class(p: int) -> str:
    if p <= 2:
        return P_CLASSES[p] if p else P_CLASSES[0]
    if p <= 15:
        return "3..15"
    if p <= 127:
        return "16..127"
    if p <= 253:
        return "128..253"
    return str(p)


def q_class(q: int) -> str:
    return {0: "0", 1: "1", 128: "128", 255: "255"}.get(q, "2..127" if q < 128 else "129..254")


def d_class(d: int) -> str:
    if d == 0:
        return D_CLASSES[0]
    if d == 1:
        return "1"
    if d <= 15:
        return "2..15"
    return "255" if d == 255 else "16..254"


def mode_of(flags: int) -> str | None:
    drive, sample = bool(flags & 8), bool(flags & 16)
    if drive and sample:
        return "drive+sample"
    return "drive" if drive else "sample" if sample else None


def all_bins() -> list[str]:
    """Every bin this observer can count (the hole list is the complement of what was hit)."""
    bins: list[str] = []
    bins += [f"LTIM P={c}" for c in P_CLASSES]
    bins += [f"LTIM Q={c}" for c in Q_CLASSES]
    bins += [f"LTIM D={c}" for c in D_CLASSES]
    bins += ["LTIM P=255 with Q=0 (valid)", "LTIM restarts a running ticker", "LTIM P=0 stops a running ticker",
             "LTIM invalid: P=255 with Q!=0"]
    bins += ["tick: bit boundary", "tick: mid-bit", "tick with fraction carry (P+1 cycles)",
             "tick with fraction carry at P=254 (255 cycles)", "ticks in consecutive cycles (P=1)",
             "ticker runs during WAIT", "ticker runs while stalled on PULL", "ticker runs while stalled on PUSH",
             "ticker runs while stalled on WAITPIN", "ticker runs while stalled on WAITEVENT",
             "classic XFER stops a running ticker"]
    bins += [f"LCFG line code {c}" for c in CODES]
    bins += [f"LCFG stuffing, runs of 1s, length {n}" for n in range(1, 9)]
    bins += [f"LCFG stuffing, runs of either polarity, length {n}" for n in range(2, 9)]
    bins += ["LCFG pair", "LCFG arbitration monitor", "LCFG SE0 end", "LCFG initial level 0",
             "LCFG initial level 1", "LCFG polarity bit without stuffing (ignored)",
             "LCFG clears set flags (SE0, lost, stuff error)",
             "LCFG invalid: line code 3", "LCFG invalid: bits 23..11 set",
             "LCFG invalid: stuffing on either polarity with run length 1"]
    bins += [f"CRC set from r{r}" for r in range(4)] + [f"CRC read into r{r}" for r in range(4)]
    bins += [f"CRC preset {k} selected" for k in range(4)]
    bins += ["CRC invalid: c=0", "CRC invalid: c>3", "CRC invalid: set with a!=0", "CRC invalid: set with b>3",
             "CRC invalid: read with a>3", "CRC invalid: read with b!=0", "CRC invalid: preset with a!=0",
             "CRC invalid: preset with b>3"]
    bins += [f"CRC fed: preset {k}, {order}, by {source}" for k in range(4) for order in ("LSB first", "MSB first")
             for source in FEED_SOURCES]
    bins += [f"LSTAT into r{r}" for r in range(4)]
    bins += ["LSTAT invalid: a>3", "LSTAT invalid: b!=0", "LSTAT invalid: c!=0"]
    bins += [f"LSTAT reads {name}={v}" for name in LSTAT_FIELDS for v in (0, 1)]
    bins += ["LSTAT reads data bits left at SE0 > 0"]
    bins += [f"line XFER started: {m}, {n} bits" for m in MODES for n in range(1, 33)]
    bins += [f"line XFER completed: {m}, {c}" for m in MODES for c in ("1 bit", "2..31 bits", "32 bits")]
    bins += [f"line XFER {m}, {o}" for m in MODES for o in ("LSB first", "MSB first")]
    bins += [f"line XFER {m}, {c}" for m in MODES for c in CODES if not (c == "Manchester" and "sample" in m)]
    bins += [f"line XFER {m} with pair" for m in MODES] + [f"line XFER {m} with CRC feed" for m in MODES]
    bins += ["line XFER with neither drive nor sample", "line XFER issued before the next boundary (no gap)",
             "line XFER invalid: b!=0", "line XFER invalid: c[1:0]!=0", "line XFER invalid: a=0",
             "line XFER invalid: a>32", "line XFER invalid: ticker stopped",
             "line XFER invalid: Manchester with sampling", "line XFER invalid: data pin not owned (drive)",
             "line XFER invalid: pair pin not owned (drive with pair)",
             "line XFER invalid: pair pin equals data pin (drive with pair)",
             "XFER invalid: c bit 7", "classic XFER with CRC feed (c bit 6)"]
    for kind in ("sent", "removed"):
        bins += [f"stuff bit {kind}: runs of 1s, length {n}" for n in range(1, 9)]
        bins += [f"stuff bit {kind}: either polarity, length {n}" for n in range(2, 9)]
    bins += [f"stuff bit sent with {c}" for c in CODES]
    bins += ["trailing stuff bit sent", "trailing stuff bit removed", "stuff error", "stuff error on a trailing stuff bit"]
    bins += ["arbitration lost (NRZ)", "arbitration lost (NRZI)", "data 0 sent as 1 after arbitration loss",
             "stuff cell sent as 1 after arbitration loss", "drive+sample with the monitor on completed without loss"]
    bins += ["SE0 end: 1 data bit left", "SE0 end: 2..31 data bits left", "SE0 end: 32 data bits left",
             "SE0 end in a drive+sample XFER", "SE0 end on a trailing stuff cell",
             "SE0 on the pair without SE0 end (ignored)"]
    bins += ["Manchester second half", "Manchester second half after the XFER completed",
             "Manchester second half with pair", "OUT on a Manchester second-half edge",
             "SET on a Manchester second-half edge", "classic XFER issued on a Manchester second-half edge"]
    bins += ["pair drive (complement on the clock-field pin)", "NRZI drive from initial level 1",
             "line drive on an open-drain pin", "sampling a pin another engine drives in a line XFER",
             "sampling the engine's own data pin while driving it"]
    bins += ["line XFERs on 2 engines at once", "line XFERs on 3 engines at once", "line XFERs on 4 engines at once",
             "mover moved a word during a line XFER", "host TX write to an engine in a line XFER",
             "host RX read from an engine in a sampling line XFER", "STOP of an engine in a line XFER",
             "BEGIN of an engine in a line XFER", "reset or deselect during a line XFER",
             "START clears non-zero line-unit state", "fault with the ticker running (unit state kept)"]
    bins += ["LTIM while a Manchester second half is pending", "LCFG while a Manchester second half is pending",
             "classic XFER while a Manchester second half is pending",
             "START while a Manchester second half is pending", "tick with fraction carry at P=1 (2 cycles)"]
    bins += [f"line XFER completed with a fraction (Q!=0): {m}" for m in MODES]
    bins += [f"line XFER completed after a trailing stuff bit: {m}" for m in MODES]
    bins += ["protocol-shaped: NRZI, stuffing runs of 1s length 6, pair, SE0 end, sampling XFER ended",
             "protocol-shaped: NRZ, stuffing either polarity length 5, monitor, drive+sample XFER completed",
             "protocol-shaped: Manchester drive with pair completed"]
    return bins


# Bins that no stimulus can hit, with the reason (kept empty unless a campaign hole is
# shown to be unreachable; see report()).
UNREACHABLE: dict[str, str] = {}


def contract_reasons(word: int, *, ticker_running: bool, code: int, pair: bool, ownership: int,
                     pins: tuple[int, int, int], width: int = 32) -> list[str]:
    """Why a line-unit instruction is invalid under docs/isa.md ("Invalid encodings"); []
    when it is valid. Written from the contract text, independently of the model."""
    op, a, b, c = word >> 24, word >> 16 & 255, word >> 8 & 255, word & 255
    imm24 = word & 0xFFFFFF
    reasons: list[str] = []
    if op == 30:
        if imm24 & 255 == 255 and imm24 >> 8 & 255:
            reasons.append("LTIM invalid: P=255 with Q!=0")
    elif op == 31:
        if imm24 >> 11:
            reasons.append("LCFG invalid: bits 23..11 set")
        if imm24 & 3 == 3:
            reasons.append("LCFG invalid: line code 3")
        if imm24 & 4 and imm24 & 8 and not imm24 >> 4 & 7:
            reasons.append("LCFG invalid: stuffing on either polarity with run length 1")
    elif op == 32:
        if c == 0:
            reasons.append("CRC invalid: c=0")
        elif c > 3:
            reasons.append("CRC invalid: c>3")
        elif c == 1:
            if a:
                reasons.append("CRC invalid: set with a!=0")
            if b > 3:
                reasons.append("CRC invalid: set with b>3")
        elif c == 2:
            if a > 3:
                reasons.append("CRC invalid: read with a>3")
            if b:
                reasons.append("CRC invalid: read with b!=0")
        else:
            if a:
                reasons.append("CRC invalid: preset with a!=0")
            if b > 3:
                reasons.append("CRC invalid: preset with b>3")
    elif op == 33:
        if a > 3:
            reasons.append("LSTAT invalid: a>3")
        if b:
            reasons.append("LSTAT invalid: b!=0")
        if c:
            reasons.append("LSTAT invalid: c!=0")
    elif op == 17:
        clock, data, _ = pins
        drive, sample = bool(c & 8), bool(c & 16)
        if c & 0x80:
            reasons.append("XFER invalid: c bit 7")
        if c & 0x20:
            if b:
                reasons.append("line XFER invalid: b!=0")
            if c & 3:
                reasons.append("line XFER invalid: c[1:0]!=0")
            if a == 0:
                reasons.append("line XFER invalid: a=0")
            elif a > width:
                reasons.append("line XFER invalid: a>32")
            if not ticker_running:
                reasons.append("line XFER invalid: ticker stopped")
            if code == 2 and sample:
                reasons.append("line XFER invalid: Manchester with sampling")
            if drive and not ownership >> data & 1:
                reasons.append("line XFER invalid: data pin not owned (drive)")
            # isa.md: "... the data pin not owned when driving, or with the pair set a pair pin
            # that is not owned or equals the data pin". Read here as applying to driving
            # XFERs (the pair pin is only written when driving); see the report.
            if pair and drive and not ownership >> clock & 1:
                reasons.append("line XFER invalid: pair pin not owned (drive with pair)")
            if pair and drive and clock == data:
                reasons.append("line XFER invalid: pair pin equals data pin (drive with pair)")
        else:
            # Classic XFER, base ISA (isa.md opcode table and XFER text): a bit count
            # 1..width, b half-period 1..255, the clock pin written (owned), and when driving
            # an owned TX pin that differs from the clock pin.
            if not 1 <= a <= width:
                reasons.append("classic XFER invalid: a outside 1..width")
            if b == 0:
                reasons.append("classic XFER invalid: b=0")
            if not ownership >> clock & 1:
                reasons.append("classic XFER invalid: clock pin not owned")
            if drive and not ownership >> data & 1:
                reasons.append("classic XFER invalid: TX pin not owned (drive)")
            if drive and clock == data:
                reasons.append("classic XFER invalid: clock equals TX (drive)")
    return reasons


class _Snap:
    __slots__ = ("engine", "line", "running", "fault", "wait", "transfer", "flags", "remaining", "committed",
                 "word", "stalled", "pins", "ownership", "open_drain", "tx0", "run", "count", "period", "phase",
                 "frac", "acc", "cfg", "level", "rx_prev", "man", "cell", "srun", "slast", "trail", "seen", "lost",
                 "serr", "se0", "crc", "preset", "tx_len", "rx_len")


class LineCoverage:
    """Observer counting ``all_bins()`` (see the module docstring)."""

    def __init__(self) -> None:
        self.bins: Counter[str] = Counter()
        self.examples: dict[str, str] = {}  # first occurrence of each decode-check disagreement
        self._pre: list[_Snap] | None = None
        self._model: Any = None
        self._feeds: list[tuple[int, int, bool]] = []  # (id(line), preset, msb) this edge
        self._started: dict[int, tuple[int, int]] = {}  # engine -> (bits, flags) of its line XFER
        self._since_done: dict[int, int] = {}  # engine -> boundary ticks since its last line XFER completed
        self._routes: list[Any] = []
        self._sync2 = 0
        self._host_tx: int | None = None
        self._host_rx: int | None = None

    # ------------------------------------------------------------ plumbing
    def _hook(self, model: Any) -> None:
        if self._model is model:
            return
        self._model = model
        original = type(model)._feed.__get__(model)
        feeds = self._feeds

        def feed(line: Any, bit: int, msb: bool) -> None:
            feeds.append((id(line), line.preset, bool(msb)))
            model_feed(line, bit, msb)

        model_feed = model.__dict__.get("_feed", original)
        model._feed = feed

    def before(self, h: Any, ui: int, pins: int) -> None:
        try:
            m = h.model
            self._hook(m)
            self._feeds.clear()
            snaps = []
            for e in m.engines:
                s = _Snap()
                line = getattr(e, "line", None)
                s.engine, s.line = e, line
                s.running, s.fault, s.wait, s.committed = e.running, e.fault, e.wait, e.committed
                t = e.transfer
                s.transfer = t
                s.flags = t.flags if t is not None else 0
                s.remaining = t.remaining if isinstance(t, LineTransfer) else 0
                s.word = e.program[e.pc] if e.committed and 0 <= e.pc < len(e.program) else None
                s.stalled, s.pins, s.ownership, s.open_drain = e.stalled, e.pins, e.ownership, e.open_drain
                s.tx0, s.tx_len, s.rx_len = e.regs[0], len(e.tx), len(e.rx)
                if line is None:
                    s.run = False
                    s.count = s.period = s.phase = s.frac = s.acc = s.cfg = s.level = s.rx_prev = 0
                    s.man = s.trail = s.seen = s.lost = s.serr = s.se0 = False
                    s.cell = s.srun = s.slast = s.crc = s.preset = 0
                else:
                    s.run, s.count, s.period, s.phase = line.run, line.count, line.period, line.phase
                    s.frac, s.acc, s.cfg, s.level, s.rx_prev = line.frac, line.acc, line.cfg, line.level, line.rx_prev
                    s.man, s.cell, s.srun, s.slast, s.trail = line.man, line.cell, line.srun, line.slast, line.trail
                    s.seen, s.lost, s.serr, s.se0, s.crc = line.seen, line.lost, line.serr, line.se0, line.crc
                    s.preset = line.preset
                snaps.append(s)
            self._pre = snaps
            self._sync2 = m.sync2
            self._routes = list(m.routes)
            # Host FIFO transfers that complete on this edge (harness host protocol, isa.md).
            self._host_tx = self._host_rx = None
            if m.window == ui >> 6 & 3:
                if ui & 16 and m.window == 2 and m.write_index == 7 and \
                        len(m.engines[m.selected].tx) < m.config.fifo_words:
                    self._host_tx = m.selected
                if ui & 32 and m.window == 3 and m.read_index == 7 and m.read_word is not None:
                    self._host_rx = m.read_engine
        except Exception as exc:  # noqa: BLE001
            self.bins[f"line-coverage error {type(exc).__name__}"] += 1
            self._pre = None

    def after(self, h: Any) -> None:
        pre, self._pre = self._pre, None
        if pre is None:
            return
        try:
            self._after(h.model, pre)
        except Exception as exc:  # noqa: BLE001
            self.bins[f"line-coverage error {type(exc).__name__}"] += 1

    # ------------------------------------------------------------ bins
    def _after(self, m: Any, pre: list[_Snap]) -> None:  # noqa: C901, PLR0912, PLR0915
        b = self.bins
        if m.timestamp == 0 or any(e is not s.engine for e, s in zip(m.engines, pre, strict=True)):
            if any(isinstance(s.transfer, LineTransfer) and s.running for s in pre):
                b["reset or deselect during a line XFER"] += 1
            self._started.clear()
            self._since_done.clear()
            return
        feeds_by_line: dict[int, list[tuple[int, bool]]] = {}
        for key, preset, msb in self._feeds:
            feeds_by_line.setdefault(key, []).append((preset, msb))
        sync2 = self._sync2
        active = [i for i, s in enumerate(pre) if s.running and isinstance(s.transfer, LineTransfer)]
        if len(active) >= 2:
            b[f"line XFERs on {len(active)} engines at once"] += 1
        if active:
            for source, before in enumerate(self._routes):
                after = m.routes[source]
                if before is not None and after != before and (after is None and before[1] == 1
                                                                or after is not None and after[1] == before[1] - 1):
                    b["mover moved a word during a line XFER"] += 1
                    break
            if self._host_tx in active:
                b["host TX write to an engine in a line XFER"] += 1
            if self._host_rx in active and pre[self._host_rx].flags & 16:
                b["host RX read from an engine in a sampling line XFER"] += 1
        for i, (s, e) in enumerate(zip(pre, m.engines, strict=True)):
            line = getattr(e, "line", None)
            if line is None:
                continue
            if s.line is not None and line is not s.line:  # START on this edge
                if s.crc or s.cfg or s.run or s.lost or s.serr or s.se0 or s.preset:
                    b["START clears non-zero line-unit state"] += 1
                if s.man and s.running:
                    b["START while a Manchester second half is pending"] += 1
                self._started.pop(i, None)
                continue
            if not s.running:
                continue
            host_stopped = not e.running and not e.fault and not (s.word is not None and s.word >> 24 == 1
                                                                   and s.transfer is None and not s.wait)
            if host_stopped:
                if isinstance(s.transfer, LineTransfer):
                    b["BEGIN of an engine in a line XFER" if not e.committed else "STOP of an engine in a line XFER"] += 1
                continue
            # Ticker (pre-edge state decides the tick).
            tick = s.run and s.count == 1
            boundary, middle = tick and s.phase == 0, tick and s.phase == 1
            if tick:
                b["tick: bit boundary" if boundary else "tick: mid-bit"] += 1
                if s.acc + s.frac >= 256:
                    b["tick with fraction carry (P+1 cycles)"] += 1
                    if s.period == 254:
                        b["tick with fraction carry at P=254 (255 cycles)"] += 1
                    elif s.period == 1:
                        b["tick with fraction carry at P=1 (2 cycles)"] += 1
                elif s.period == 1:
                    b["ticks in consecutive cycles (P=1)"] += 1
                if s.wait:
                    b["ticker runs during WAIT"] += 1
                if s.stalled and s.word is not None and s.transfer is None:
                    name = {6: "PULL", 7: "PUSH", 13: "WAITPIN", 15: "WAITEVENT"}.get(s.word >> 24)
                    if name:
                        b[f"ticker runs while stalled on {name}"] += 1
                if boundary and i in self._since_done:
                    self._since_done[i] += 1
            code = s.cfg & 3
            pair = bool(s.cfg >> 7 & 1)
            if middle and s.man:
                b["Manchester second half"] += 1
                if s.transfer is None:
                    b["Manchester second half after the XFER completed"] += 1
                if pair:
                    b["Manchester second half with pair"] += 1
            if e.fault and not s.fault and s.run:
                b["fault with the ticker running (unit state kept)"] += 1
            # CRC steps on this edge.
            for preset, msb in feeds_by_line.get(id(line), ()):
                if isinstance(s.transfer, LineTransfer):
                    source = "line sample" if s.flags & 16 else "line drive"
                else:
                    source = "classic sample" if s.flags & 16 else "classic drive"
                b[f"CRC fed: preset {preset}, {'MSB first' if msb else 'LSB first'}, by {source}"] += 1
            if isinstance(s.transfer, LineTransfer) and not s.wait:
                self._line_transfer(i, s, e, line, boundary, middle, code, pair, sync2, pre)
            elif s.transfer is None and not s.wait and s.word is not None:
                self._issue(i, s, e, line, m, middle, code, pair)

    def _line_transfer(self, i: int, s: _Snap, e: Any, line: Any, boundary: bool, middle: bool, code: int,
                       pair: bool, sync2: int, pre: list[_Snap]) -> None:
        b = self.bins
        clock, data, rx = s.pins
        drive, sample, msb = bool(s.flags & 8), bool(s.flags & 16), bool(s.flags & 4)
        stuff_en, either, run_n = bool(s.cfg & 4), bool(s.cfg & 8), (s.cfg >> 4 & 7) + 1
        polarity = "either polarity" if either else "runs of 1s"
        if boundary and drive:
            is_stuff = stuff_en and s.srun == run_n
            if is_stuff:
                b[f"stuff bit sent: {polarity}, length {run_n}"] += 1
                b[f"stuff bit sent with {CODES[code]}"] += 1
                if s.trail:
                    b["trailing stuff bit sent"] += 1
                if s.lost:
                    b["stuff cell sent as 1 after arbitration loss"] += 1
            elif s.lost:
                bit = (s.tx0 >> 31 & 1) if msb else (s.tx0 & 1)
                if not bit:
                    b["data 0 sent as 1 after arbitration loss"] += 1
            if pair:
                b["pair drive (complement on the clock-field pin)"] += 1
            if s.open_drain >> data & 1:
                b["line drive on an open-drain pin"] += 1
        if middle and sample and (not drive or s.seen):
            raw, pair_raw = sync2 >> rx & 1, sync2 >> clock & 1
            for j, other in enumerate(pre):
                if j != i and other.running and isinstance(other.transfer, LineTransfer) and other.flags & 8 and \
                        (other.pins[1] == rx or (other.cfg >> 7 & 1 and other.pins[0] == rx)):
                    b["sampling a pin another engine drives in a line XFER"] += 1
                    break
            if drive and rx == data:
                b["sampling the engine's own data pin while driving it"] += 1
            if pair and not raw and not pair_raw:
                if s.cfg >> 9 & 1:
                    left = s.remaining
                    b["SE0 end: 1 data bit left" if left == 1 else "SE0 end: 32 data bits left" if left == 32
                      else "SE0 end: 2..31 data bits left"] += 1
                    if drive:
                        b["SE0 end in a drive+sample XFER"] += 1
                    if s.trail:
                        b["SE0 end on a trailing stuff cell"] += 1
                    if s.cfg & 0x2FF == 0x2D5:  # NRZI, stuffing of runs of 1s, length 6, pair, SE0 end
                        b["protocol-shaped: NRZI, stuffing runs of 1s length 6, pair, SE0 end, sampling XFER ended"] += 1
                    self._done(i, s, se0=True)
                    return
                b["SE0 on the pair without SE0 end (ignored)"] += 1
            if s.cfg >> 8 & 1 and drive and s.cell and not raw and not s.lost:
                b[f"arbitration lost ({CODES[code]})"] += 1
            if stuff_en and s.srun == run_n:
                b[f"stuff bit removed: {polarity}, length {run_n}"] += 1
                if s.trail:
                    b["trailing stuff bit removed"] += 1
                decoded = (1 - (raw ^ s.rx_prev)) if code == 1 else raw
                if decoded != int(either and not s.slast):
                    b["stuff error"] += 1
                    if s.trail:
                        b["stuff error on a trailing stuff bit"] += 1
        if e.transfer is None and not e.fault:
            self._done(i, s)

    def _done(self, i: int, s: _Snap, *, se0: bool = False) -> None:
        b = self.bins
        started = self._started.pop(i, None)
        mode = mode_of(s.flags)
        if started is not None and mode is not None and not se0:
            bits = started[0]
            b[f"line XFER completed: {mode}, {'1 bit' if bits == 1 else '32 bits' if bits == 32 else '2..31 bits'}"] += 1
            if mode == "drive+sample" and s.cfg >> 8 & 1 and not s.lost:
                b["drive+sample with the monitor on completed without loss"] += 1
            if s.frac:
                b[f"line XFER completed with a fraction (Q!=0): {mode}"] += 1
            if s.trail:
                b[f"line XFER completed after a trailing stuff bit: {mode}"] += 1
            cfg = s.cfg & 0x3FF
            if mode == "drive+sample" and cfg & 0x17F == 0x14C:  # NRZ, stuffing either polarity length 5, monitor
                b["protocol-shaped: NRZ, stuffing either polarity length 5, monitor, drive+sample XFER completed"] += 1
            if mode == "drive" and cfg & 0x83 == 0x82:  # Manchester with pair
                b["protocol-shaped: Manchester drive with pair completed"] += 1
        self._since_done[i] = 0

    def _issue(self, i: int, s: _Snap, e: Any, line: Any, m: Any, middle: bool, code: int, pair: bool) -> None:
        b = self.bins
        word = s.word
        op, a, bb, c = word >> 24, word >> 16 & 255, word >> 8 & 255, word & 255
        faulted = bool(e.fault) and not s.fault
        if middle and s.man and not faulted:
            if op == 8:
                b["OUT on a Manchester second-half edge"] += 1
            elif op == 2:
                b["SET on a Manchester second-half edge"] += 1
            elif op == 17 and not c & 0x20:
                b["classic XFER issued on a Manchester second-half edge"] += 1
        if s.man and not faulted:
            if op == 30:
                b["LTIM while a Manchester second half is pending"] += 1
            elif op == 31:
                b["LCFG while a Manchester second half is pending"] += 1
            elif op == 17 and not c & 0x20:
                b["classic XFER while a Manchester second half is pending"] += 1
        if op not in LINE_OPS and op != 17:
            return
        reasons = contract_reasons(word, ticker_running=s.run, code=code, pair=pair, ownership=s.ownership,
                                   pins=s.pins, width=m.config.width)
        if op == 17 and c & 0x20 and not c & 8 and pair and not faulted and \
                (not s.ownership >> s.pins[0] & 1 or s.pins[0] == s.pins[1]):
            # isa.md lists "with the pair set a pair pin that is not owned or equals the data
            # pin" without "when driving"; RTL and model accept it for a sample-only XFER.
            b["decode note: sample-only line XFER with pair, pair pin not owned or equal to the data pin, "
              "accepted"] += 1
        if bool(reasons) != faulted:
            key = (f"decode check: op {op} {'faulted' if faulted else 'did not fault'}, contract reading says "
                   f"{'invalid' if reasons else 'valid'}")
            b[key] += 1
            self.examples.setdefault(key, f"word {word:#010x} ticker_running={s.run} cfg={s.cfg:#05x} "
                                          f"ownership={s.ownership:#04x} pins={s.pins} fault={e.fault}")
        if faulted:
            for reason in reasons:
                b[reason] += 1
            return
        imm24 = word & 0xFFFFFF
        if op == 30:
            p, q, d = imm24 & 255, imm24 >> 8 & 255, imm24 >> 16
            b[f"LTIM P={p_class(p)}"] += 1
            b[f"LTIM Q={q_class(q)}"] += 1
            b[f"LTIM D={d_class(d)}"] += 1
            if p == 255:
                b["LTIM P=255 with Q=0 (valid)"] += 1
            if s.run:
                b["LTIM restarts a running ticker" if p else "LTIM P=0 stops a running ticker"] += 1
        elif op == 31:
            b[f"LCFG line code {CODES[imm24 & 3]}"] += 1
            if imm24 & 4:
                n = (imm24 >> 4 & 7) + 1
                b[f"LCFG stuffing, runs of either polarity, length {n}" if imm24 & 8
                  else f"LCFG stuffing, runs of 1s, length {n}"] += 1
            elif imm24 & 8:
                b["LCFG polarity bit without stuffing (ignored)"] += 1
            for bit, name in ((0x80, "LCFG pair"), (0x100, "LCFG arbitration monitor"), (0x200, "LCFG SE0 end")):
                if imm24 & bit:
                    b[name] += 1
            b[f"LCFG initial level {imm24 >> 10 & 1}"] += 1
            if s.se0 or s.lost or s.serr:
                b["LCFG clears set flags (SE0, lost, stuff error)"] += 1
        elif op == 32:
            if c == 1:
                b[f"CRC set from r{bb}"] += 1
            elif c == 2:
                b[f"CRC read into r{a}"] += 1
            else:
                b[f"CRC preset {bb} selected"] += 1
        elif op == 33:
            b[f"LSTAT into r{a}"] += 1
            value = e.regs[a]
            for k, name in enumerate(LSTAT_FIELDS):
                b[f"LSTAT reads {name}={value >> k & 1}"] += 1
            if value >> 8 & 63:
                b["LSTAT reads data bits left at SE0 > 0"] += 1
        elif op == 17 and c & 0x20:
            mode = mode_of(c)
            if mode is None:
                b["line XFER with neither drive nor sample"] += 1
            else:
                b[f"line XFER started: {mode}, {a} bits"] += 1
                b[f"line XFER {mode}, {'MSB first' if c & 4 else 'LSB first'}"] += 1
                b[f"line XFER {mode}, {CODES[code]}"] += 1
                if pair:
                    b[f"line XFER {mode} with pair"] += 1
                if c & 0x40:
                    b[f"line XFER {mode} with CRC feed"] += 1
                if code == 1 and s.level and c & 8:
                    b["NRZI drive from initial level 1"] += 1
                if self._since_done.get(i) == 0:
                    b["line XFER issued before the next boundary (no gap)"] += 1
            self._started[i] = (a, c)
            self._since_done.pop(i, None)
        elif op == 17:
            if c & 0x40:
                b["classic XFER with CRC feed (c bit 6)"] += 1
            if s.run:
                b["classic XFER stops a running ticker"] += 1


def holes(bins: dict[str, int]) -> list[str]:
    return [name for name in all_bins() if not bins.get(name) and name not in UNREACHABLE]


def anomalies(bins: dict[str, int]) -> dict[str, int]:
    """Decode-check disagreements and observer errors (expected: none)."""
    return {k: v for k, v in bins.items() if k.startswith(("decode check:", "line-coverage error"))}


def notes(bins: dict[str, int]) -> dict[str, int]:
    """Counted observations about the contract wording (not failures)."""
    return {k: v for k, v in bins.items() if k.startswith("decode note:")}


def report(bins: dict[str, int], cases: int, title: str = "Line-unit coverage") -> str:
    """Per-bin Markdown table (hits, hits per 1,000 cases) with holes and unreachable bins."""
    lines = [f"{title}: {len(all_bins())} bins, {sum(1 for n in all_bins() if bins.get(n))} hit, "
             f"{len(holes(bins))} holes, {len(UNREACHABLE)} declared unreachable; {cases:,} cases.", "",
             "| bin | hits | per 1,000 cases |", "|---|---:|---:|"]
    for name in all_bins():
        hits = bins.get(name, 0)
        rate = f"{1000 * hits / cases:.3g}" if cases else "-"
        mark = " (unreachable)" if name in UNREACHABLE else "" if hits else " **(hole)**"
        lines.append(f"| {name}{mark} | {hits:,} | {rate} |")
    odd = anomalies(bins)
    lines += ["", "Decode-check disagreements and observer errors: " + (", ".join(f"{k}: {v}" for k, v in odd.items())
                                                                     if odd else "none")]
    lines += ["", "Decode notes: " + (", ".join(f"{k}: {v:,}" for k, v in notes(bins).items()) or "none")]
    if UNREACHABLE:
        lines += ["", "Unreachable bins:"] + [f"- {k}: {v}" for k, v in UNREACHABLE.items()]
    return "\n".join(lines)
