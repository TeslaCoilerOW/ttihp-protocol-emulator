# Copied verbatim (apart from this header) from the asic-lab monorepo:
#   projects/protocol-emulator/python/protocol_emulator/reference.py @ commit 18676a4
# Independent pure-Python ISA2 reference (stdlib only). Keep in sync with
# reference/isa.md; any semantic change must update RTL, assembler and model.
# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Independent executable ISA specification; no generated RTL is imported.

``outputs`` observes combinational pins before an edge; ``tick`` applies one
edge. FIFO eligibility uses occupancy before that edge. The host reserves an RX
word from first presentation through its last accepted nibble.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum


class Fault(IntEnum):
    INVALID_OPCODE = 1
    INVALID_OPERAND = 1
    PIN_OWNERSHIP = 1
    PC_RANGE = 2
    TIMEOUT = 3
    RX_OVERFLOW = 4


@dataclass(frozen=True)
class Config:
    engines: int = 4
    width: int = 32
    program_words: int = 64
    fifo_words: int = 8
    fused: bool = True
    prefetch: bool = False

    def __post_init__(self) -> None:
        if self.engines not in (2, 4) or self.width not in (16, 32):
            raise ValueError("unsupported engine count or datapath")
        if self.program_words not in (32, 64, 128) or self.fifo_words not in (8, 32):
            raise ValueError("unsupported storage capacity")


@dataclass
class Transfer:
    bits: int
    half_period: int
    flags: int
    remaining: int
    transitions: int = 0


@dataclass
class Engine:
    program: list[int] = field(default_factory=list)
    committed: bool = False
    writing: bool = False
    running: bool = False
    stalled: bool = False
    fault: int = 0
    pc: int = 0
    regs: list[int] = field(default_factory=lambda: [0] * 4)
    tx: deque[int] = field(default_factory=deque)
    rx: deque[int] = field(default_factory=deque)
    ownership: int = 0
    open_drain: int = 0
    values: int = 0
    direction: int = 0
    repeat: int = 0
    wait: int = 0
    limit: int = 65535
    blocked: int = 0
    event: bool = False
    trigger: tuple[int, int] | None = None
    pins: tuple[int, int, int] = (0, 0, 0)
    completed: int = 0
    transfer: Transfer | None = None


@dataclass(frozen=True)
class Outputs:
    uo: int
    uio_out: int
    uio_oe: int


def instruction(op: int, a: int = 0, b: int = 0, c: int = 0) -> int:
    """Encode fields for tests, independently of the OCaml assembler."""
    if any(not 0 <= item <= 255 for item in (op, a, b, c)):
        raise ValueError("instruction field outside byte")
    return (op << 24) | (a << 16) | (b << 8) | c


def immediate(op: int, value: int) -> int:
    if not 0 <= op <= 255 or not 0 <= value < 1 << 24:
        raise ValueError("instruction immediate out of range")
    return (op << 24) | value


class Reference:
    def __init__(self, config: Config | None = None) -> None:
        if config is None:
            config = Config()
        self.config = config
        self.mask = (1 << config.width) - 1
        self.reset()

    def reset(self) -> None:
        self.engines = [Engine() for _ in range(self.config.engines)]
        self.timestamp = 0
        self.sync1 = self.sync2 = self.previous_pins = 0
        self.selected = self.read_select = 0
        self.host_fault = False
        self.routes: list[tuple[int, int] | None] = [None] * self.config.engines
        self.round_robin = 0
        self.window = 0
        self.write_index = self.write_word = 0
        self.read_index = 0
        self.read_word: int | None = None
        self.read_engine: int | None = None
        self._starts: set[int] = set()
        self._stops: set[int] = set()

    def _fail(self, engine: Engine, code: int) -> None:
        engine.fault = code
        engine.running = engine.stalled = False
        engine.direction = 0
        engine.transfer = None

    def _status(self) -> int:
        e = self.engines[self.selected]
        if self.read_select == 0:
            return (int(e.running) | int(e.committed) << 1 | int(e.stalled) << 2
                    | int(bool(e.fault)) << 3 | e.fault << 8)
        if self.read_select == 1:
            return self.timestamp
        if self.read_select == 2:
            return len(e.tx) | len(e.rx) << 16
        if self.read_select == 3:
            return e.pc
        if self.read_select == 4:
            return int(e.event)
        if self.read_select == 5:
            return e.completed
        if self.read_select == 6:
            return e.regs[1]
        return 2

    def _read(self, window: int) -> tuple[bool, int, int]:
        current = window == self.window
        index = self.read_index if current else 0
        if current and self.read_word is not None:
            return True, self.read_word, index
        if window == 0:
            return True, self._status(), 0
        if window == 3 and self.engines[self.selected].rx:
            return True, self.engines[self.selected].rx[0], 0
        return False, 0, 0

    def outputs(self, ui: int = 0, *, reset: bool = False, enabled: bool = True) -> Outputs:
        if reset or not enabled:
            return Outputs(0, 0, 0)
        window = ui >> 6 & 3
        current = window == self.window
        selected = self.engines[self.selected]
        ready = current and (window == 0 or (window == 1 and not selected.running
                             and selected.writing and len(selected.program) < self.config.program_words)
                             or (window == 2 and len(selected.tx) < self.config.fifo_words))
        valid = current and self.read_word is not None
        word = self.read_word if current and self.read_word is not None else 0
        read_index = self.read_index if current else 0
        irq = any(e.event or e.rx for e in self.engines)
        fault = self.host_fault or any(e.fault for e in self.engines)
        uo = ((word >> (read_index * 4) & 15) | int(ready) << 4
              | int(valid) << 5 | int(irq) << 6 | int(fault) << 7)
        values = enables = 0
        for e in self.engines:
            if e.running and not e.fault:
                values |= e.values & e.ownership & ~e.open_drain
                enables |= (e.direction & e.ownership
                            & ~(e.open_drain & e.values))
        return Outputs(uo, values & 255, enables & 255)

    def command(self, word: int) -> bool:
        """Apply an accepted complete command; invalid commands are atomic."""
        op, payload = word >> 24, word & 0xFFFFFF
        e = self.engines[self.selected]
        mask = (1 << self.config.engines) - 1
        reject = False
        if op == 0:
            reject = payload >= self.config.engines
            if not reject:
                self.selected = payload
        elif op == 1:
            reject = payload != 0
            if not reject:
                e.running = e.committed = False
                e.direction = 0
                e.program = []
                e.writing = True
                self._stops.add(self.selected)
        elif op == 2:
            reject = (payload >> 16 != 0 or e.running or not e.writing
                      or not 0 < payload <= self.config.program_words
                      or payload != len(e.program))
            if not reject:
                e.committed, e.writing = True, False
        elif op == 3:
            owned, drain = payload & 255, payload >> 8 & 255
            other = 0
            for i, peer in enumerate(self.engines):
                if i != self.selected:
                    other |= peer.ownership
            reject = bool(payload >> 16 or e.running or owned & other or drain & ~owned)
            if not reject:
                e.ownership, e.open_drain = owned, drain
        elif op in (4, 5, 7, 9):
            selected_mask = payload & ~(1 << 23) if op == 7 else payload
            reject = bool(selected_mask & ~mask)
            chosen = [i for i in range(self.config.engines) if selected_mask >> i & 1]
            if op == 4:
                reject |= any(not self.engines[i].committed or self.engines[i].fault
                              for i in chosen)
            if op == 7:
                reject |= any(self.engines[i].running for i in chosen)
            if not reject:
                if op == 7 and payload >> 23 & 1:
                    self.host_fault = False
                for i in chosen:
                    item = self.engines[i]
                    if op == 4:
                        self._start(item)
                        self._starts.add(i)
                    elif op == 5:
                        item.running = False
                        item.direction = 0
                        self._stops.add(i)
                    elif op == 7:
                        item.fault = 0
                    else:
                        item.event = True
        elif op == 6:
            source, dest = payload & 3, payload >> 2 & 3
            enable, count = bool(payload & 16), payload >> 5 & 65535
            reject = bool(payload >> 21 or source >= self.config.engines
                          or dest >= self.config.engines)
            if not reject:
                self.routes[source] = (dest, count) if enable and count else None
        elif op == 8:
            reject = payload > 7
            if not reject:
                self.read_select = payload
        elif op == 10:
            reject = bool(payload or e.running)
            if not reject:
                e.tx.clear()
                e.rx.clear()
                for source, route in enumerate(self.routes):
                    if source == self.selected or (route and route[0] == self.selected):
                        self.routes[source] = None
        elif op == 11:
            reject = bool(payload >> 6 or e.running)
            if not reject:
                e.trigger = (payload & 7, payload >> 3 & 3) if payload & 32 else None
        else:
            reject = True
        if reject:
            self.host_fault = True
        return not reject

    @staticmethod
    def _start(e: Engine) -> None:
        e.running, e.stalled = True, False
        e.pc = e.values = e.direction = e.repeat = e.wait = e.blocked = e.completed = 0
        e.regs = [0] * 4
        e.limit = 65535
        e.pins = (0, 0, 0)
        e.transfer = None

    def program_word(self, word: int) -> bool:
        e = self.engines[self.selected]
        if e.running or not e.writing or len(e.program) == self.config.program_words:
            self.host_fault = True
            return False
        e.program.append(word & 0xFFFFFFFF)
        return True

    def _drive(self, e: Engine, pin: int, value: int) -> None:
        e.values = (e.values & ~(1 << pin)) | ((value & 1) << pin)

    def _shift_out(self, e: Engine, pin: int, msb: bool) -> None:
        self._drive(e, pin, e.regs[0] >> (self.config.width - 1) if msb else e.regs[0])
        e.regs[0] = ((e.regs[0] << 1) if msb else (e.regs[0] >> 1)) & self.mask

    def _shift_in(self, e: Engine, pin: int, msb: bool) -> None:
        bit = self.sync2 >> pin & 1
        e.regs[1] = ((e.regs[1] << 1 | bit) if msb else
                     (e.regs[1] >> 1 | bit << (self.config.width - 1))) & self.mask

    def _step(self, e: Engine, tx_available: bool, rx_space: bool) -> tuple[bool, bool, int]:
        """Return accepted TX pop, RX push and SIGNAL destination mask."""
        e.stalled = False
        if not e.running:
            return False, False, 0
        if e.wait:
            e.wait -= 1
            return False, False, 0
        if e.transfer is not None:
            t = e.transfer
            t.remaining -= 1
            if t.remaining:
                return False, False, 0
            clock, tx, rx = e.pins
            active = t.transitions % 2 == 0
            cpol, cpha, msb, drive, sample = (bool(t.flags & (1 << i)) for i in range(5))
            self._drive(e, clock, int(cpol != active))
            if drive and cpha and active:
                self._shift_out(e, tx, msb)
            if drive and not cpha and not active:
                e.regs[0] = ((e.regs[0] << 1) if msb else (e.regs[0] >> 1)) & self.mask
                if t.transitions < 2*t.bits-1:
                    self._drive(e, tx, e.regs[0] >> (self.config.width - 1) if msb else e.regs[0])
            if sample and (not active if cpha else active):
                self._shift_in(e, rx, msb)
            t.transitions += 1
            if t.transitions == 2 * t.bits:
                e.transfer = None
                e.pc += 1
                e.completed = (e.completed + 1) & 0xFFFFFFFF
            else:
                t.remaining = t.half_period
            return False, False, 0
        if not 0 <= e.pc < len(e.program) or not e.committed:
            self._fail(e, Fault.PC_RANGE)
            return False, False, 0
        word = e.program[e.pc]
        op, a, b, c = word >> 24, word >> 16 & 255, word >> 8 & 255, word & 255
        imm24, imm16 = word & 0xFFFFFF, word & 65535
        next_pc, pop, push, signal = e.pc + 1, False, False, 0
        invalid = False
        if op in (0, 1, 6, 15):
            invalid = bool(imm24)
        elif op == 7:
            invalid = a > 1 or b != 0 or c != 0
        elif op in (2, 3):
            invalid = bool(imm24 >> 8)
        elif op in (8, 9):
            invalid = a > 7 or b != 0 or c > 1
        elif op == 10:
            invalid = a != 0
        elif op == 12:
            invalid = imm24 == 0
        elif op == 13:
            invalid = a > 7 or b > 1 or c != 0
        elif op == 14:
            invalid = bool(imm24 >> self.config.engines)
        elif op == 16:
            invalid = bool(imm24 >> 9)
        elif op == 17:
            invalid = not self.config.fused or not 1 <= a <= self.config.width or b == 0 or c > 31
        elif op in (18, 20, 21, 22, 23):
            invalid = a > 3 or b > 3 or c != 0
        elif op in (19, 26):
            invalid = a > 3
        elif op in (24, 25):
            invalid = a > 3 or b != 0 or c >= self.config.width
        elif op in (27, 28):
            invalid = a > 3 or b != 0 or c != 0
        elif op == 29:
            invalid = not 0 < imm24 <= 255
        elif op not in (4, 5, 11):
            self._fail(e, Fault.INVALID_OPCODE)
            return False, False, 0
        if invalid:
            self._fail(e, Fault.INVALID_OPERAND)
            return False, False, 0
        if (op in (2, 3) and imm24 & ~e.ownership) or (op == 8 and not e.ownership >> a & 1):
            self._fail(e, Fault.PIN_OWNERSHIP)
            return False, False, 0
        if op == 0:
            pass
        elif op == 1:
            e.running = False
            e.direction = 0
        elif op == 2:
            e.values = imm24
        elif op == 3:
            e.direction = imm24
        elif op == 4:
            e.wait = imm24
        elif op == 5:
            next_pc = imm24
        elif op == 6:
            if not tx_available:
                e.stalled = True
                return False, False, 0
            e.regs[0], pop = e.tx[0], True
        elif op == 7:
            if not rx_space:
                if a:
                    self._fail(e, Fault.RX_OVERFLOW)
                else:
                    e.stalled = True
                return False, False, 0
            push = True
        elif op == 8:
            self._shift_out(e, a, bool(c))
        elif op == 9:
            self._shift_in(e, a, bool(c))
        elif op == 10:
            e.repeat = imm16
        elif op == 11:
            if e.repeat:
                e.repeat -= 1
                next_pc = imm24
        elif op == 12:
            e.limit = imm24
        elif op in (13, 15):
            ready = (self.sync2 >> a & 1) == b if op == 13 else e.event
            if not ready:
                e.blocked += 1
                e.stalled = True
                if e.blocked >= e.limit:
                    self._fail(e, Fault.TIMEOUT)
                return False, False, 0
            if op == 15:
                e.event = False
            e.blocked = 0
        elif op == 14:
            signal = imm24
        elif op == 16:
            e.pins = (imm24 & 7, imm24 >> 3 & 7, imm24 >> 6 & 7)
        elif op == 17:
            clock, tx, _ = e.pins
            required = (1 << clock) | ((1 << tx) if c & 8 else 0)
            if required & ~e.ownership or (c & 8 and clock == tx):
                self._fail(e, Fault.PIN_OWNERSHIP)
                return False, False, 0
            self._drive(e, clock, c & 1)
            if c & 8 and not c & 2:
                self._drive(e, tx, e.regs[0] >> (self.config.width - 1) if c & 4 else e.regs[0])
            e.transfer = Transfer(a, b, c, b)
            return False, False, 0
        elif op == 18:
            e.regs[a] = e.regs[b]
        elif op == 19:
            e.regs[a] = imm16
        elif op == 20:
            e.regs[a] = (e.regs[a] + e.regs[b]) & self.mask
        elif op == 21:
            e.regs[a] ^= e.regs[b]
        elif op == 22:
            e.regs[a] &= e.regs[b]
        elif op == 23:
            e.regs[a] |= e.regs[b]
        elif op == 24:
            e.regs[a] = e.regs[a] << c & self.mask
        elif op == 25:
            e.regs[a] >>= c
        elif op == 26:
            if e.regs[a] == 0:
                next_pc = imm16
        elif op == 27:
            e.regs[a] ^= self.mask
        elif op == 28:
            e.regs[a] = self.timestamp & self.mask
        else:
            self._fail(e, imm24)
            return False, False, 0
        e.pc = next_pc
        e.completed = (e.completed + 1) & 0xFFFFFFFF
        return pop, push, signal

    def _stalled(self, e: Engine) -> bool:
        if not e.running or e.fault or e.wait or e.transfer is not None or not 0 <= e.pc < len(e.program):
            return False
        word = e.program[e.pc]
        op, a, b = word >> 24, word >> 16 & 7, word >> 8 & 1
        return ((op == 6 and not e.tx) or (op == 7 and not (word & 0xFFFFFF)
                                         and len(e.rx) == self.config.fifo_words)
                or (op == 13 and (self.sync2 >> a & 1) != b) or (op == 15 and not e.event))

    def tick(self, ui: int = 0, pins: int = 0, *, reset: bool = False,
             enabled: bool = True) -> Outputs:
        if reset or not enabled:
            self.reset()
            return self.outputs(ui, reset=reset, enabled=enabled)
        window = ui >> 6 & 3
        changed = window != self.window
        pre = self.outputs(ui)
        if changed:
            self.window = window
            self.write_index = self.write_word = self.read_index = 0
            self.read_word = self.read_engine = None
        tx_counts = [len(e.tx) for e in self.engines]
        rx_counts = [len(e.rx) for e in self.engines]
        rx_heads = [e.rx[0] if e.rx else 0 for e in self.engines]
        host_tx: tuple[int, int] | None = None
        host_rx: int | None = None
        event_before = [e.event for e in self.engines]
        external_events = 0
        for index, engine in enumerate(self.engines):
            if engine.trigger is not None:
                pin, mode = engine.trigger
                current, previous = bool(self.sync2 >> pin & 1), bool(self.previous_pins >> pin & 1)
                detected = (current and not previous, previous and not current, current, not current)[mode]
                external_events |= int(detected) << index
        valid, word, _ = self._read(window)
        if valid and self.read_word is None and not changed:
            self.read_word = word
            self.read_engine = self.selected if window == 3 else None
        reserved = self.selected if window == 3 and not changed else None
        if ui & 32 and pre.uo & 32:
            if self.read_index == 7:
                host_rx = self.read_engine
                self.read_index = 0
                self.read_word = self.read_engine = None
            else:
                self.read_index += 1
        host_event = 0
        edited_route: int | None = None
        if ui & 16 and pre.uo & 16:
            self.write_word |= (ui & 15) << (4 * self.write_index)
            if self.write_index == 7:
                accepted_word = self.write_word
                self.write_index = self.write_word = 0
                if window == 0:
                    if accepted_word >> 24 == 9:
                        host_event = accepted_word & 0xFFFFFF
                    accepted_command = self.command(accepted_word)
                    if not accepted_command:
                        host_event = 0
                    elif accepted_word >> 24 == 6:
                        edited_route = accepted_word & 3
                    elif accepted_word >> 24 == 11:
                        external_events &= ~(1 << self.selected)
                elif window == 1:
                    self.program_word(accepted_word)
                elif window == 2:
                    host_tx = self.selected, accepted_word & self.mask
            else:
                self.write_index += 1
        # Queue movement sees pre-edge state and a host-reserved RX head.
        dma: tuple[int, int, int] | None = None
        for offset in range(self.config.engines):
            source = (self.round_robin + offset) % self.config.engines
            route = self.routes[source]
            if route is None or source == edited_route:
                continue
            dest, count = route
            if (rx_counts[source] and source != reserved and tx_counts[dest] < self.config.fifo_words
                    and (host_tx is None or host_tx[0] != dest)):
                dma = source, dest, rx_heads[source]
                self.routes[source] = (dest, count - 1) if count > 1 else None
                self.round_robin = (source + 1) % self.config.engines
                break
        signals = host_event | external_events
        changes: list[tuple[bool, bool, int]] = []
        for i, e in enumerate(self.engines):
            e.event = event_before[i]
            if i in self._starts or i in self._stops:
                changes.append((False, False, 0))
                continue
            change = self._step(e, bool(tx_counts[i]), rx_counts[i] < self.config.fifo_words)
            signals |= change[2]
            changes.append(change)
        for i, (pop, push, _) in enumerate(changes):
            e = self.engines[i]
            if pop:
                e.tx.popleft()
            if host_rx == i or (dma is not None and dma[0] == i):
                e.rx.popleft()
            if push:
                e.rx.append(e.regs[1])
            if signals >> i & 1:
                e.event = True
        if host_tx is not None:
            self.engines[host_tx[0]].tx.append(host_tx[1])
        if dma is not None:
            self.engines[dma[1]].tx.append(dma[2])
        self._starts.clear()
        self._stops.clear()
        self.previous_pins, self.sync2, self.sync1 = self.sync2, self.sync1, pins & 255
        self.timestamp = (self.timestamp + 1) & 0xFFFFFFFF
        for e in self.engines:
            e.stalled = bool(self._stalled(e))
        return self.outputs(ui)
