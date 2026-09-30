# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Cycle schedule of one engine's program, predicted from the contract.

``ProgramRef`` executes a program image for one engine from its START and
records every pin write (instructions and the line unit), output-enable
change, pushed word and the end state.  The line unit is
``line_std_ref.LineUnitRef``; the base-ISA instructions follow docs/isa.md
("Instructions").  It covers the instructions that the stdref programs and
the firmware/ext demos use; a blocking PULL on an empty queue or a blocking
PUSH on a full one is outside its scope (``Unsupported``).

The input pads are a function ``pad_in(cycle) -> 8-bit value`` supplied by
the caller.  A caller that models a bus connected to the engine's own outputs
uses ``ProgramRef.output_at(cycle)``, which is exact for every cycle whose
writes are already decided (the line unit reports its writes before it
samples, see ``LineUnitRef.xfer``).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Callable, Optional

from _ref import ref

MASK32 = 0xFFFFFFFF

OPS = {
    "NOP": 0, "HALT": 1, "SET": 2, "DIR": 3, "WAIT": 4, "JMP": 5, "PULL": 6, "PUSH": 7,
    "OUT": 8, "IN": 9, "COUNT": 10, "LOOP": 11, "LIMIT": 12, "WAITPIN": 13, "SIGNAL": 14,
    "WAITEVENT": 15, "PINS": 16, "XFER": 17, "MOV": 18, "LOAD": 19, "ADD": 20, "XOR": 21,
    "AND": 22, "OR": 23, "SHL": 24, "SHR": 25, "JZ": 26, "NOT": 27, "TIME": 28, "FAULT": 29,
    "LTIM": 30, "LCFG": 31, "CRC": 32, "LSTAT": 33,
}
IMM24 = {"SET", "DIR", "WAIT", "JMP", "LOOP", "LIMIT", "SIGNAL", "PINS", "FAULT", "LTIM", "LCFG"}
IMM16 = {"COUNT", "LOAD", "JZ"}


def encode(mnemonic: str, a: int = 0, b: int = 0, c: int = 0, imm: Optional[int] = None) -> int:
    op = OPS[mnemonic]
    if imm is not None:
        if mnemonic in IMM16:
            return (op << 24) | (a << 16) | (imm & 0xFFFF)
        return (op << 24) | (imm & 0xFFFFFF)
    return (op << 24) | (a << 16) | (b << 8) | c


class Unsupported(Exception):
    """The program needs behaviour this schedule does not model."""


@dataclass
class Write:
    cycle: int
    priority: int        # 0 line unit, 1 instruction (an instruction writes on top)
    mask: int
    value: int
    source: str


@dataclass
class Outcome:
    start: int
    end_cycle: int                     # issue cycle of HALT or of the faulting instruction
    end_pc: int
    fault: int                         # 0 halted, -1 running at stop_cycle, -2 blocked on PULL
    writes: list[Write]
    dir_writes: list[tuple[int, int]]  # (cycle, oe value)
    pushes: list[tuple[int, int]]      # (cycle, word)
    push_is_time: list[bool]           # the pushed rx came from TIME
    xfers: list[ref.XferResult]
    issue_cycles: list[tuple[int, int]]  # (pc, cycle)
    unit: ref.LineUnitRef


class ProgramRef:
    def __init__(self, words: list[int], *, owned: int, open_drain: int = 0,
                 tx_words: Optional[list[int]] = None, fifo_words: int = 8,
                 interp: ref.Interpretation = ref.Interpretation(),
                 pad_in: Optional[Callable[[int], int]] = None, max_cycles: int = 200_000,
                 stop_cycle: Optional[int] = None, hooks=None):
        self.words = list(words)
        self.owned = owned
        self.open_drain = open_drain
        self.tx_queue = list(tx_words or [])
        self.fifo_words = fifo_words
        self.interp = interp
        self.pad_in = pad_in or (lambda cycle: 0)
        self.max_cycles = max_cycles
        self.stop_cycle = stop_cycle
        self.hooks = hooks        # optional: on_lcfg(cycle, cfg), on_line_xfer(cycle, a, c, pins, unit)
        self.dont_care: list[tuple[int, int, int]] = []   # (from cycle, to cycle, pin mask)
        self.limit = 65535
        self.writes: list[Write] = []
        self.dir_writes: list[tuple[int, int]] = []
        self._timeline_len = -1
        self._times: list[int] = []
        self._values: list[int] = []

    # -- pad state ---------------------------------------------------------
    def output_at(self, cycle: int) -> int:
        """Logical output register as seen on the pads in ``cycle``."""
        if self._timeline_len != len(self.writes):
            ordered = sorted(self.writes, key=lambda w: (w.cycle, w.priority))
            self._times, self._values = [], []
            value = 0
            for write in ordered:
                value = (value & ~write.mask) | (write.value & write.mask)
                if self._times and self._times[-1] == write.cycle:
                    self._values[-1] = value
                else:
                    self._times.append(write.cycle)
                    self._values.append(value)
            self._timeline_len = len(self.writes)
        index = bisect.bisect_right(self._times, cycle - ref.OUT_LATENCY) - 1
        return self._values[index] & 0xFF if index >= 0 else 0

    def oe_at(self, cycle: int) -> int:
        value = 0
        for when, oe in self.dir_writes:
            if when + ref.OUT_LATENCY <= cycle:
                value = oe
        return value

    def _seen(self, cycle: int) -> int:
        return self.pad_in(cycle - ref.IN_LATENCY)

    # -- execution ---------------------------------------------------------
    def run(self, start: int = 0) -> Outcome:
        unit = ref.LineUnitRef(self.interp)
        regs = [0, 0, 0, 0]          # tx, rx, x, y
        pins = ref.Pins(0, 0, 0)
        pushes: list[tuple[int, int]] = []
        xfers: list[ref.XferResult] = []
        issues: list[tuple[int, int]] = []
        repeat = 0
        pc, cycle = 0, start
        fault = 0
        time_tag = [False] * 4
        push_is_time: list[bool] = []

        def unit_write(write: ref.PinWrite) -> None:
            self.writes.append(Write(write.cycle, 0, 1 << write.pin, write.value << write.pin,
                                     "unit-" + write.kind))

        def sample_fn(tick: int) -> dict:
            seen = self._seen(tick)
            return {p: (seen >> p) & 1 for p in range(8)}

        while True:
            if self.stop_cycle is not None and cycle >= self.stop_cycle:
                fault = -1                     # still running at stop_cycle
                break
            if cycle - start > self.max_cycles:
                raise Unsupported("program did not end")
            if pc >= len(self.words):
                fault = 2
                break
            word = self.words[pc]
            op, a, b, c = word >> 24, (word >> 16) & 0xFF, (word >> 8) & 0xFF, word & 0xFF
            # registers an instruction writes lose their TIME tag (TIME sets it again)
            if op in (18, 19, 20, 21, 22, 23, 24, 25, 27, 28, ref.LSTAT_OP) or (op == ref.CRC_OP and c == 2):
                time_tag[a & 3] = op == 28
            if op == 17:
                time_tag[0] = time_tag[1] = False
            if op == 6:
                time_tag[0] = False
            imm24, imm16 = word & 0xFFFFFF, word & 0xFFFF
            issues.append((pc, cycle))
            if op >= ref.XFER_OP and (op == ref.XFER_OP or op >= ref.LTIM_OP):
                if ref.line_instruction_faults(op, a, b, c, owned=self.owned, pins=pins,
                                               ticker_running=unit.ticker_running,
                                               cfg=unit.cfg, interp=self.interp):
                    fault = 1
                    break
            duration = 1
            next_pc = pc + 1
            if op == 0:
                pass
            elif op == 1:
                break
            elif op in (2, 3):
                if imm24 & ~self.owned & 0xFFFFFF:
                    fault = 1
                    break
                if op == 2:
                    self.writes.append(Write(cycle, 1, 0xFF, imm24 & 0xFF, "SET"))
                else:
                    self.dir_writes.append((cycle, imm24 & 0xFF))
            elif op == 4:
                duration = 1 + imm24
            elif op == 5:
                next_pc = imm24
            elif op == 6:
                if not self.tx_queue:
                    fault = -2                 # blocked on an empty TX queue
                    break
                regs[0] = self.tx_queue.pop(0)
            elif op == 7:
                if len(pushes) >= self.fifo_words:
                    if a == 1:
                        fault = 4
                        break
                    raise Unsupported("PUSH on a full RX queue")
                pushes.append((cycle, regs[1]))
                push_is_time.append(time_tag[1])
            elif op == 8:
                if not (self.owned >> a) & 1:
                    fault = 1
                    break
                if c == 0:
                    bit = regs[0] & 1
                    regs[0] >>= 1
                else:
                    bit = (regs[0] >> 31) & 1
                    regs[0] = (regs[0] << 1) & MASK32
                self.writes.append(Write(cycle, 1, 1 << a, bit << a, "OUT"))
            elif op == 10:
                repeat = imm16
            elif op == 11:
                if repeat:
                    repeat -= 1
                    next_pc = imm24
            elif op == 12:
                self.limit = imm24
            elif op == 13:
                # WAITPIN: one issue edge if already true, else block; fault 3
                # after LIMIT consecutive unsuccessful samples
                misses = 0
                t = cycle
                while (self._seen(t) >> a) & 1 != b:
                    misses += 1
                    if misses >= self.limit:
                        break
                    t += 1
                    if self.stop_cycle is not None and t >= self.stop_cycle:
                        break
                if misses >= self.limit:
                    cycle = t
                    fault = 3
                    break
                duration = t - cycle + 1
            elif op == 16:
                pins = ref.Pins.decode(imm24)
            elif op == 17:
                mode = ref.XferMode.decode(a, c)
                if mode.line:
                    if self.hooks is not None:
                        self.hooks.on_line_xfer(cycle, a, c, pins, unit)
                    result = unit.xfer(cycle, a, c, regs[0], regs[1], pins, sample_fn, unit_write)
                    regs[0], regs[1] = result.tx, result.rx
                    xfers.append(result)
                    duration = result.complete - cycle + 1
                else:
                    duration = self._classic_xfer(cycle, a, b, c, regs, pins, unit)
            elif op == 18:
                regs[a] = regs[b]
            elif op == 19:
                regs[a] = imm16
            elif op == 20:
                regs[a] = (regs[a] + regs[b]) & MASK32
            elif op == 21:
                regs[a] ^= regs[b]
            elif op == 22:
                regs[a] &= regs[b]
            elif op == 23:
                regs[a] |= regs[b]
            elif op == 24:
                regs[a] = (regs[a] << c) & MASK32
            elif op == 25:
                regs[a] >>= c
            elif op == 26:
                if regs[a] == 0:
                    next_pc = imm16
            elif op == 27:
                regs[a] ^= MASK32
            elif op == 28:
                regs[a] = cycle & MASK32          # relative timestamp: issue cycle
            elif op == 29:
                fault = imm24 & 0xFF
                break
            elif op == ref.LTIM_OP:
                if self.interp.ltim_pending_half == "cancel":
                    self._cancel_pending_half(cycle, unit)
                unit.ltim(cycle, imm24)
                if self.interp.ltim_pending_half == "next_mid":
                    self._reschedule_pending_half(cycle, unit)
            elif op == ref.LCFG_OP:
                if self.interp.lcfg_cancels_pending_half:
                    self._cancel_pending_half(cycle, unit)
                unit.lcfg(imm24, cycle)
                if self.hooks is not None:
                    self.hooks.on_lcfg(cycle, unit.cfg)
            elif op == ref.CRC_OP:
                if c == 1:
                    unit.crc_set(regs[b])
                elif c == 2:
                    regs[a] = unit.crc
                else:
                    unit.crc_preset(b)
            elif op == ref.LSTAT_OP:
                regs[a] = unit.lstat(bool(self.tx_queue), len(pushes) < self.fifo_words, cycle)
            else:
                raise Unsupported(f"opcode {op}")
            pc = next_pc
            cycle += duration
        return Outcome(start, cycle, pc, fault, self.writes, self.dir_writes, pushes,
                       push_is_time, xfers, issues, unit)

    def _cancel_pending_half(self, cycle: int, unit: ref.LineUnitRef) -> None:
        """Drop Manchester second halves that are due after ``cycle``."""
        self.writes[:] = [w for w in self.writes if not (w.source == "unit-mid" and w.cycle > cycle)]
        unit.level_updates = [u for u in unit.level_updates if u[0] <= cycle]
        self._timeline_len = -1

    def _reschedule_pending_half(self, cycle: int, unit: ref.LineUnitRef) -> None:
        """Move second halves due after ``cycle`` to T(1) of the restarted ticker."""
        if unit.ticker is None:
            self._cancel_pending_half(cycle, unit)
            return
        tick = unit.ticker.tick(1)
        for write in self.writes:
            if write.source == "unit-mid" and write.cycle > cycle:
                write.cycle = tick
        unit.level_updates = [(tick if when > cycle else when, level)
                              for when, level in unit.level_updates]
        self._timeline_len = -1

    def _classic_xfer(self, cycle: int, a: int, b: int, c: int, regs: list[int],
                      pins: ref.Pins, unit: ref.LineUnitRef) -> int:
        """Base-ISA XFER (fused): clock, CPHA/CPOL, drive/sample, c bit 6 CRC feed."""
        cpol, cpha = c & 1, (c >> 1) & 1
        msb, drive, sample, feed = (c >> 2) & 1, (c >> 3) & 1, (c >> 4) & 1, (c >> 6) & 1
        unit.stop_ticker()
        clock_mask = 1 << pins.clock
        self.writes.append(Write(cycle, 1, clock_mask, cpol << pins.clock, "XFER-clock"))
        tx = regs[0]
        rx = regs[1]
        sent, received = [], []

        def out_bit(value: int) -> int:
            return (value >> 31) & 1 if msb else value & 1

        def shift(value: int) -> int:
            return (value << 1) & MASK32 if msb else value >> 1

        if drive:
            # the data pin after the last bit is not fixed by the contract
            self.dont_care.append((cycle + 2 * a * b + ref.OUT_LATENCY, 1 << 60, 1 << pins.tx))
        if drive and cpha == 0:
            self.writes.append(Write(cycle, 1, 1 << pins.tx, out_bit(tx) << pins.tx, "XFER-data"))
        for j in range(1, 2 * a + 1):
            t = cycle + j * b
            active = j % 2 == 1
            level = (cpol ^ 1) if active else cpol
            self.writes.append(Write(t, 1, clock_mask, level << pins.clock, "XFER-clock"))
            sample_now = sample and (active if cpha == 0 else not active)
            if sample_now:
                bit = (self._seen(t) >> pins.rx) & 1
                received.append(bit)
                rx = ((rx << 1) | bit) & MASK32 if msb else (rx >> 1) | (bit << 31)
            if drive:
                if cpha == 0 and not active:
                    sent.append(out_bit(tx))
                    tx = shift(tx)
                    if j < 2 * a:
                        self.writes.append(Write(t, 1, 1 << pins.tx, out_bit(tx) << pins.tx,
                                                 "XFER-data"))
                elif cpha == 1 and active:
                    self.writes.append(Write(t, 1, 1 << pins.tx, out_bit(tx) << pins.tx, "XFER-data"))
                    sent.append(out_bit(tx))
                    tx = shift(tx)
        if feed:
            if drive and sample:
                bits = sent if self.interp.classic_ds_crc_basis == "sent" else received
            else:
                bits = sent if drive else received
            unit.feed(bits, bool(msb))
        regs[0], regs[1] = tx, rx
        return 2 * a * b + 1
