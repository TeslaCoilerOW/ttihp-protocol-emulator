# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Constrained-random programs and host traffic for the lockstep differential test.

A ``Case`` is a declarative, JSON-serializable description of one random run:
per-engine programs with pin ownership/open-drain masks, TX prefill, routes,
input triggers, a START mask, a list of host operations and an external pin
stimulus seed. ``run_case`` executes it on any Harness (DUT+model under cocotb,
or model only). Because a case is data, a failing case can be saved, replayed
(PE_REPLAY) and shrunk (``minimize``).

Program generation follows docs/isa.md: operands are legal and pin
writes stay inside the engine's ownership unless an instruction is a
deliberate fault, which is annotated with its expected fault code.
"""

from __future__ import annotations

import copy
import json
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from harness import (BEGIN, CLEAR, COMMIT, EVENT, FLUSH, OWN, READ_SELECT, ROUTE, SELECT, START, STOP, TRIGGER,
                     UO_FAULT, UO_IRQ, Harness, LockstepMismatch, immediate, instruction, pad_value)
from model.reference import Config
from variants import options as variant_options

MNEMONIC = ["NOP", "HALT", "SET", "DIR", "WAIT", "JMP", "PULL", "PUSH", "OUT", "IN", "COUNT", "LOOP",
            "LIMIT", "WAITPIN", "SIGNAL", "WAITEVENT", "PINS", "XFER", "MOV", "LOAD", "ADD", "XOR", "AND",
            "OR", "SHL", "SHR", "JZ", "NOT", "TIME", "FAULT"]
IMM24 = {2, 3, 4, 5, 11, 12, 14, 16, 29}
COMMAND_NAMES = {0: "SELECT", 1: "BEGIN", 2: "COMMIT", 3: "OWN", 4: "START", 5: "STOP", 6: "ROUTE",
                 7: "CLEAR", 8: "READ_SELECT", 9: "EVENT", 10: "FLUSH", 11: "TRIGGER"}


def disassemble(word: int) -> str:
    op, a, b, c = word >> 24, word >> 16 & 255, word >> 8 & 255, word & 255
    if op >= len(MNEMONIC):
        return f"<invalid op {op}> {word:08x}"
    name = MNEMONIC[op]
    if op in IMM24:
        return f"{name} {word & 0xFFFFFF:#x}"
    if op in (19, 26, 10):
        return f"{name} a={a} imm16={word & 0xFFFF:#x}"
    return f"{name} a={a} b={b} c={c}"


def listing(words: list[int], expect: dict[int, int] | None = None) -> str:
    lines = []
    for pc, word in enumerate(words):
        note = f"   ; deliberate fault -> code {expect[pc]}" if expect and pc in expect else ""
        lines.append(f"      {pc:2d}: {word:08x}  {disassemble(word)}{note}")
    return "\n".join(lines)


@dataclass
class EngineSpec:
    words: list[int]
    ownership: int
    open_drain: int
    expect: dict[int, int] = field(default_factory=dict)  # pc -> deliberate fault code


@dataclass
class Case:
    seed: int
    index: int
    engines: list[EngineSpec | None]
    prefill: list[list[int]]
    routes: list[list[int]]          # [source, dest, count]
    triggers: list[list[int]]        # [engine, payload]
    start_mask: int
    ops: list[list[Any]]
    pin_seed: int
    toggle: float
    cycles: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @staticmethod
    def from_json(text: str) -> Case:
        data = json.loads(text)
        engines = [None if e is None else EngineSpec(e["words"], e["ownership"], e["open_drain"],
                                                     {int(k): v for k, v in e["expect"].items()})
                   for e in data.pop("engines")]
        return Case(engines=engines, **data)

    def describe(self) -> str:
        lines = [f"  case seed={self.seed:#x} index={self.index} start_mask={self.start_mask:#x} "
                 f"cycles={self.cycles} pin_seed={self.pin_seed} toggle={self.toggle}",
                 f"  prefill={self.prefill} routes={self.routes} triggers={self.triggers}"]
        for e, spec in enumerate(self.engines):
            if spec is None:
                lines.append(f"  engine {e}: not loaded")
            else:
                lines.append(f"  engine {e}: own={spec.ownership:#04x} open_drain={spec.open_drain:#04x} "
                             f"{len(spec.words)} words")
                lines.append(listing(spec.words, spec.expect))
        lines.append(f"  host ops ({len(self.ops)}): " + "; ".join(op_text(op) for op in self.ops[:60])
                     + (" ..." if len(self.ops) > 60 else ""))
        return "\n".join(lines)


def op_text(op: list[Any]) -> str:
    kind = op[0]
    if kind == "cmd":
        word = op[1]
        return f"{COMMAND_NAMES.get(word >> 24, f'cmd{word >> 24}')}({word & 0xFFFFFF:#x})"
    if kind == "reload":
        return f"reload(e{op[1]}, {len(op[2]['words'])} words)"
    return f"{kind}{tuple(op[1:])}"


# ---------------------------------------------------------------- generation
class ProgramGenerator:
    def __init__(self, rng: random.Random, config: Config, ownership: int, fault_rate: float) -> None:
        self.rng, self.config = rng, config
        self.ownership = ownership
        self.owned = [p for p in range(8) if ownership >> p & 1]
        self.fault_rate = fault_rate
        # Variant restrictions (variants.py). The base design draws exactly the
        # same random numbers as before variants existed.
        options = variant_options(config)
        self.byte_lane = options.shift == "byte_lane"
        self.saturating_pc = options.pc_bits == "saturating_7"

    def shift_count(self) -> int:
        """An SHL/SHR count: any c < width; on byte_lane designs a byte lane (0/8/16/24),
        or in 15% of cases a non-lane count (a deliberate code-1 fault, see generate)."""
        if self.byte_lane:
            if self.rng.random() < 0.15:
                return self.rng.choice([c for c in range(1, self.config.width) if c % 8])
            return self.rng.randrange(0, self.config.width, 8)
        return self.rng.randrange(self.config.width)

    def subset(self) -> int:
        return sum(1 << p for p in self.owned if self.rng.random() < 0.5)

    def generate(self, length: int) -> EngineSpec:
        rng, words, expect = self.rng, [], {}
        if self.owned:
            words.append(immediate(3, self.subset()))  # DIR
            words.append(immediate(2, self.subset()))  # SET
        if len(self.owned) >= 2:
            clock, tx = rng.sample(self.owned, 2)
            words.append(immediate(16, clock | tx << 3 | rng.randrange(8) << 6))
        elif self.owned:
            words.append(immediate(16, self.owned[0] | rng.randrange(8) << 3 | rng.randrange(8) << 6))
        if rng.random() < 0.3:
            words.append(immediate(12, rng.choice([rng.randint(1, 40), rng.randint(100, 1500)])))  # LIMIT
        body = max(length - 1, len(words) + 1)
        while len(words) < body:
            if rng.random() < self.fault_rate:
                word, code = self.bad(len(words), length)
                expect[len(words)] = code
                words.append(word)
            else:
                for word in self.instruction(len(words), body):
                    if word >> 24 == 29:  # explicit FAULT n
                        expect[len(words)] = word & 0xFF
                    elif self.byte_lane and word >> 24 in (24, 25) and word & 7:
                        expect[len(words)] = 1  # non-lane shift count on a byte_lane design
                    words.append(word)
        words = words[:body]
        expect = {pc: code for pc, code in expect.items() if pc < body}
        tail = rng.random()
        if tail < 0.75:
            words.append(immediate(5, rng.randrange(min(4, len(words)))))  # JMP back
        elif tail < 0.9:
            words.append(instruction(1))  # HALT
        else:
            words.append(instruction(0))  # fall off the image: PC-range fault 2
        return EngineSpec(words, self.ownership, 0, expect)

    def instruction(self, pc: int, length: int) -> list[int]:
        rng, width = self.rng, self.config.width
        kind = rng.choices(
            ["nop", "set", "dir", "wait", "jmp", "pull", "push", "out", "in", "loop", "limit", "waitpin",
             "signal", "waitevent", "pins", "xfer", "mov", "load", "alu", "shift", "jz", "not", "time",
             "fault", "halt"],
            weights=[2, 6, 3, 4, 2, 3, 5, 5, 5, 3, 2, 3, 3, 2, 1, 7, 3, 4, 8, 3, 3, 2, 2, 0.3, 0.4])[0]
        reg = lambda: rng.randrange(4)  # noqa: E731
        if kind == "nop":
            return [instruction(0)]
        if kind == "set":
            return [immediate(2, self.subset())]
        if kind == "dir":
            return [immediate(3, self.subset())]
        if kind == "wait":
            return [immediate(4, rng.choice([0, 0, 1, 2, rng.randint(3, 12), rng.randint(20, 120)]))]
        if kind == "jmp":
            if self.saturating_pc and rng.random() < 0.08:  # saturates to PC 127: fault 2
                return [immediate(5, rng.randint(128, 0xFFFFFF))]
            return [immediate(5, rng.randrange(length))]
        if kind == "pull":
            return [instruction(6)]
        if kind == "push":
            return [instruction(7, int(rng.random() < 0.25))]
        if kind == "out":
            if not self.owned:
                return [instruction(9, rng.randrange(8), 0, rng.randrange(2))]
            return [instruction(8, rng.choice(self.owned), 0, rng.randrange(2))]
        if kind == "in":
            return [instruction(9, rng.randrange(8), 0, rng.randrange(2))]
        if kind == "loop":
            count = immediate(10, rng.randint(0, 4))
            body = [w for _ in range(rng.randint(1, 3)) for w in self.simple()]
            return [count, *body, immediate(11, pc + 1)]
        if kind == "limit":
            return [immediate(12, rng.choice([rng.randint(1, 30), rng.randint(31, 600)]))]
        if kind == "waitpin":
            return [instruction(13, rng.randrange(8), rng.randrange(2))]
        if kind == "signal":
            return [immediate(14, rng.randrange(1 << self.config.engines))]
        if kind == "waitevent":
            return [instruction(15)]
        if kind == "pins":
            if len(self.owned) >= 2:
                clock, tx = rng.sample(self.owned, 2)
            elif self.owned:
                clock, tx = self.owned[0], rng.randrange(8)
            else:
                clock, tx = rng.randrange(8), rng.randrange(8)
            return [immediate(16, clock | tx << 3 | rng.randrange(8) << 6)]
        if kind == "xfer":
            if not self.owned:  # would fault: clock pin must be owned
                return [instruction(21, reg(), reg())]
            bits = rng.choice([1, 2, 3, rng.randint(4, 8), rng.randint(9, width), width])
            flags = rng.randrange(32)
            if len(self.owned) < 2:
                flags &= ~8  # driving needs distinct owned clock and TX pins
            return [instruction(17, bits, rng.choice([1, 1, 2, 3, rng.randint(4, 9)]), flags)]
        if kind in ("mov", "load", "alu", "shift", "not", "time"):
            dest = reg()
            if kind == "mov":
                word = instruction(18, dest, reg())
            elif kind == "load":
                word = instruction(19, dest, rng.randrange(256), rng.randrange(256))
            elif kind == "alu":
                word = instruction(rng.choice([20, 21, 22, 23]), dest, reg())
            elif kind == "shift":
                word = instruction(rng.choice([24, 25]), dest, 0, self.shift_count())
            elif kind == "not":
                word = instruction(27, dest)
            else:
                word = instruction(28, dest)
            return [word, *self.observe(dest, length)]
        if kind == "jz":
            if self.saturating_pc and rng.random() < 0.08:  # taken: saturates to PC 127
                return [instruction(26, reg()) | rng.randint(128, 0xFFFF)]
            return [instruction(26, reg(), 0, rng.randrange(length))]
        if kind == "fault":
            return [immediate(29, rng.randint(1, 255))]
        return [instruction(1)]  # halt

    def observe(self, register: int, length: int) -> list[int]:
        """Often make a computed register observable: push it, shift it out, or branch on it."""
        rng = self.rng
        choice = rng.random()
        if choice < 0.35:
            return [instruction(18, 1, register), instruction(7, int(rng.random() < 0.2))]  # MOV rx; PUSH
        if choice < 0.55 and self.owned:
            pin, direction = rng.choice(self.owned), rng.randrange(2)
            return [instruction(18, 0, register), *[instruction(8, pin, 0, direction)] * rng.randint(1, 3)]
        if choice < 0.65:
            return [instruction(26, register, 0, rng.randrange(length))]  # JZ
        return []

    def simple(self) -> list[int]:
        rng = self.rng
        choice = rng.randrange(6)
        if choice == 0:
            return [instruction(rng.choice([20, 21, 22, 23]), rng.randrange(4), rng.randrange(4))]
        if choice == 1 and self.owned:
            return [instruction(8, rng.choice(self.owned), 0, rng.randrange(2))]
        if choice == 2:
            return [instruction(9, rng.randrange(8), 0, rng.randrange(2))]
        if choice == 3:
            return [immediate(4, rng.randint(0, 3))]
        if choice == 4:
            return [instruction(7)]  # PUSH (blocking)
        return [instruction(28, rng.randrange(4))]

    def bad(self, pc: int, length: int) -> tuple[int, int]:
        """A deliberately faulting instruction and its expected fault code."""
        rng = self.rng
        unowned = [p for p in range(8) if not self.ownership >> p & 1]
        options: list[tuple[int, int]] = [
            (instruction(rng.randint(30, 255), rng.randrange(256), rng.randrange(256), rng.randrange(256)), 1),
            (instruction(7, rng.randint(2, 255)), 1),                   # PUSH a>1
            (instruction(8, rng.randint(8, 255), 0, 0), 1),             # OUT pin > 7
            (instruction(24, rng.randrange(4), 0, rng.randint(self.config.width, 255)), 1),  # SHL too far
            (immediate(12, 0), 1),                                      # LIMIT 0
            (instruction(17, rng.randint(1, 8), 0, 0), 1),              # XFER half-period 0
            (instruction(18, rng.randint(4, 255), 0), 1),               # MOV bad register
            (immediate(29, rng.randint(1, 255)), -1),                   # FAULT n -> n
            (immediate(29, 0), 1),                                      # FAULT 0 is invalid
            (immediate(14, rng.randint(1 << self.config.engines, 0xFFFFFF)), 1),  # SIGNAL absent engine
            (instruction(0, rng.randint(1, 255)), 1),                   # NOP with operand
            (immediate(16, rng.randint(1 << 9, 0xFFFFFF)), 1),          # PINS high bits
            (immediate(5, length + rng.randint(0, 40)), 2),            # JMP outside image
        ]
        if unowned:
            pin = rng.choice(unowned)
            options.append((immediate(2, 1 << pin), 1))                # SET unowned pin
            options.append((immediate(3, 1 << pin), 1))                # DIR unowned pin
            options.append((instruction(8, pin, 0, rng.randrange(2)), 1))  # OUT unowned pin
        if self.byte_lane:
            # SHL/SHR by a count that is not a byte lane (but < width): invalid operand.
            count = rng.choice([c for c in range(1, self.config.width) if c % 8])
            options += [(instruction(rng.choice([24, 25]), rng.randrange(4), 0, count), 1)] * 3
        if self.saturating_pc:
            # Targets beyond the 7-bit PC saturate to 127 (outside every image: fault 2 on the
            # next issue, PC readback 127). The JZ/LOOP forms fault only when taken.
            options += [(immediate(5, rng.choice([128, rng.randint(129, 0xFFFF), rng.randint(0x10000, 0xFFFFFF)])), 2),
                        (instruction(26, rng.randrange(4), 0, 0) | rng.randint(128, 0xFFFF), 2),
                        (immediate(11, rng.randint(128, 0xFFFFFF)), 2)]
        word, code = rng.choice(options)
        if code == -1:
            code = word & 0xFF
        return word, code


# Generator generation. 1 draws the cases of the 2026-09-25 verification
# campaigns (docs/verification-campaign.md); PE_RANDOM_GEN=1 selects it. Their
# stimulus is reproduced up to the trailing deselect, which run_case no longer
# drops at the budget. 2 adds, from a separate random stream so that every
# generation-1 operation is still drawn identically, a mid-traffic deselect and
# host-command sequences that the model rejects (extend_case).
GENERATION = 2


def make_case(seed: int, index: int, config: Config, *, cycles: int, generation: int = GENERATION) -> Case:
    case, ownership = _make_case_v1(seed, index, config, cycles=cycles)
    if generation >= 2:
        extend_case(case, config, ownership, random.Random((seed * 1_000_003 + index) ^ 0x6A9C2027))
    return case


def _make_case_v1(seed: int, index: int, config: Config, *, cycles: int) -> tuple[Case, list[int]]:
    rng = random.Random(seed * 1_000_003 + index)
    engines = config.engines
    owner = [rng.choice([*range(engines), None, None]) for _ in range(8)]
    ownership = [sum(1 << p for p in range(8) if owner[p] == e) for e in range(engines)]
    fault_rate = rng.choice([0.0, 0.02, 0.05])
    specs: list[EngineSpec | None] = []
    for e in range(engines):
        if rng.random() < 0.12:
            specs.append(None)
            continue
        length = rng.choice([rng.randint(3, 10), rng.randint(8, 24), rng.randint(24, config.program_words)])
        spec = ProgramGenerator(rng, config, ownership[e], fault_rate).generate(length)
        spec.open_drain = sum(1 << p for p in range(8) if ownership[e] >> p & 1 and rng.random() < 0.25)
        specs.append(spec)
    loaded = [e for e in range(engines) if specs[e] is not None]
    prefill = [[rng.getrandbits(32) & ((1 << config.width) - 1) for _ in range(rng.randint(0, config.fifo_words))]
               for _ in range(engines)]
    routes = [[rng.randrange(engines), rng.randrange(engines), rng.randint(1, 12)]
              for _ in range(rng.choice([0, 1, 1, 2]))]
    triggers = [[rng.randrange(engines), rng.randrange(8) | rng.randrange(4) << 3 | 32]
                for _ in range(rng.choice([0, 1, 2]))]
    start_mask = sum(1 << e for e in loaded if rng.random() < 0.9)
    ops: list[list[Any]] = []
    budget = 0
    while budget < cycles:
        op = host_op(rng, config, ownership)
        ops.append(op)
        budget += {"idle": op[1] if op[0] == "idle" else 0, "tx": 22, "rx": 24, "status": 34, "cmd": 11,
                   "partial": 14, "partial_read": 14, "reload": 300}.get(op[0], 10)
    if rng.random() < 0.1:
        ops.append(["deselect", rng.randint(1, 3)])
    return Case(seed, index, specs, prefill, routes, triggers, start_mask, ops,
                pin_seed=rng.getrandbits(32), toggle=rng.choice([0.0, 0.01, 0.05, 0.2, 0.5]),
                cycles=cycles), ownership


def extend_case(case: Case, config: Config, ownership: list[int], rng: random.Random) -> None:
    """Generation-2 additions (docs/verification-campaign.md, "Gap closure").

    1. Generation 1 appends the optional deselect (10% of cases) after operations
       whose estimated cost already fills the budget, so it ran in 0.26% of cases.
       It moves to a random point in the first 70% of the traffic: ena drops while
       engines run and the rest of the case runs on the deselected state.
    2. One to three host-command sequences in 70% of cases, inserted at random
       points: BEGIN with a payload, bad COMMIT lengths, STOP/EVENT naming absent
       engines, ROUTE with payload bits 21..23, and aborted or overfull reloads
       (BEGIN of a running engine, START before COMMIT, a 65th program word).
       Generation 1 only sends well-formed forms of these commands. Whether each
       command is accepted is left to the model (and checked on the DUT); half of
       the sequences end with CLEAR bit 23 so that the sticky host fault, and the
       fault pin with it, keeps changing.
    """
    ops = case.ops
    if ops and ops[-1][0] == "deselect":
        ops.insert(rng.randrange(max(1, int((len(ops) - 1) * 0.7))), ops.pop())
    for _ in range(rng.choice([0, 0, 0, 1, 1, 1, 1, 2, 2, 3])):
        position = rng.randrange(max(1, int(len(ops) * 0.8)))
        ops[position:position] = rejected_sequence(rng, config, ownership)


def rejected_sequence(rng: random.Random, config: Config, ownership: list[int]) -> list[list[Any]]:
    """Host operations that exercise command-rejection paths (see extend_case)."""
    engines, capacity = config.engines, config.program_words
    e = rng.randrange(engines)
    kind = rng.choices(["begin", "commit", "stop", "event", "route", "reload", "overfull"],
                       weights=[3, 3, 2, 2, 2, 3, 0.5])[0]
    if kind == "begin":
        sequence = [["cmd", BEGIN << 24 | rng.randint(1, 0xFFFFFF), e]]
    elif kind == "commit":  # the engine is not being written, or the length is wrong/zero/too big
        length = rng.choice([0, rng.randint(1, capacity), capacity + rng.randint(1, 40), rng.randint(1, 0xFF) << 16])
        sequence = [["cmd", COMMIT << 24 | length, e]]
    elif kind == "stop":
        sequence = [["cmd", STOP << 24 | rng.randint(1 << engines, 0xFFFFFF), e]]
    elif kind == "event":
        sequence = [["cmd", EVENT << 24 | rng.randint(1 << engines, 0xFFFFFF), e]]
    elif kind == "route":
        sequence = [["cmd", ROUTE << 24 | 1 << rng.randint(21, 23) | rng.getrandbits(21), e]]
    else:
        spec = ProgramGenerator(rng, config, ownership[e], 0.03).generate(
            capacity if kind == "overfull" else rng.randint(3, 16))
        words = spec.words
        sequence = [["cmd", BEGIN << 24, e]]  # legal; stops the engine if it runs
        if kind == "overfull":
            # A full image, one word too many (write-ready stays low: abandoned), then COMMIT/START.
            sequence += [["prog", w] for w in words] + [["prog", rng.getrandbits(32)]]
            sequence += [["cmd", COMMIT << 24 | len(words), e], ["cmd", START << 24 | 1 << e, e]]
        else:
            written = rng.randint(0, len(words))
            sequence += [["prog", w] for w in words[:written]]
            sequence.append(["cmd", START << 24 | 1 << e, e])  # not committed
            sequence.append(["cmd", COMMIT << 24 | rng.choice([written + 1, max(written - 1, 0), capacity + 1]), e])
            if rng.random() < 0.5:  # finish the load properly
                sequence += [["prog", w] for w in words[written:]]
                sequence += [["cmd", COMMIT << 24 | len(words), e], ["cmd", START << 24 | 1 << e, e]]
    if rng.random() < 0.5:
        sequence.append(["cmd", CLEAR << 24 | 1 << 23, e])
    return sequence


def host_op(rng: random.Random, config: Config, ownership: list[int]) -> list[Any]:
    engines = config.engines
    mask = (1 << engines) - 1
    kind = rng.choices(["idle", "tx", "rx", "status", "cmd", "partial", "partial_read", "reload", "revive"],
                       weights=[30, 26, 16, 10, 14, 3, 3, 1.5, 6])[0]
    if kind == "revive":
        return ["revive", rng.randrange(1, mask + 1)]
    if kind == "rx" and rng.random() < 0.5:
        return ["drain", rng.randrange(1, mask + 1), rng.randint(1, config.fifo_words)]
    if kind == "idle":
        return ["idle", rng.choice([1, 2, 5, rng.randint(5, 40), rng.randint(40, 200)]), int(rng.random() < 0.5)]
    if kind == "tx":
        return ["tx", rng.randrange(engines), rng.getrandbits(32) & ((1 << config.width) - 1), rng.randint(0, 30)]
    if kind == "rx":
        return ["rx", rng.randrange(engines), rng.choice([0, 5, 40, 150]), [rng.choice([0, 0, 1, 3]) for _ in range(8)]]
    if kind == "status":
        return ["status", rng.randrange(engines), rng.randrange(8)]
    if kind == "partial":
        return ["partial", rng.choice([0, 1, 2]), rng.randint(1, 7), rng.getrandbits(32)]
    if kind == "partial_read":
        return ["partial_read", rng.randrange(engines), rng.choice([0, 3]), rng.randint(1, 7)]
    if kind == "reload":
        engine = rng.randrange(engines)
        spec = ProgramGenerator(rng, config, ownership[engine], 0.03).generate(rng.randint(3, 16))
        return ["reload", engine, asdict(spec)]
    # Raw host commands, mostly legal, some rejected on purpose.
    choice = rng.choices(["event", "route", "stop", "start", "clear", "flush", "trigger", "own", "select",
                          "read_select", "invalid"],
                         weights=[10, 4, 3, 5, 4, 1, 2, 1, 2, 2, 1])[0]
    if choice == "event":
        word = EVENT << 24 | rng.randrange(1, mask + 1)
    elif choice == "route":
        word = ROUTE << 24 | rng.randrange(engines) | rng.randrange(engines) << 2 | rng.choice([16, 16, 0]) \
            | rng.randint(0, 10) << 5
    elif choice == "stop":
        word = STOP << 24 | rng.randrange(mask + 1)
    elif choice == "start":
        word = START << 24 | rng.randrange(1, mask + 1)
    elif choice == "clear":
        word = CLEAR << 24 | rng.randrange(mask + 1) | (1 << 23 if rng.random() < 0.5 else 0)
    elif choice == "flush":
        word = FLUSH << 24
    elif choice == "trigger":
        word = TRIGGER << 24 | rng.randrange(8) | rng.randrange(4) << 3 | rng.choice([0, 32])
    elif choice == "own":
        engine_owned = rng.choice(ownership)
        word = OWN << 24 | engine_owned | (engine_owned & rng.randrange(256)) << 8
    elif choice == "select":
        word = SELECT << 24 | rng.randrange(engines + 2)  # engines/engines+1 reject
    elif choice == "read_select":
        word = READ_SELECT << 24 | rng.randrange(12)  # >7 rejects
    else:
        word = rng.randint(12, 255) << 24 | rng.getrandbits(24)
    return ["cmd", word, rng.randrange(engines)]


# ---------------------------------------------------------------- execution
def pin_supplier(case: Case):
    rng = random.Random(case.pin_seed)
    state = {"external": rng.getrandbits(8)}

    def pins(cycle: int, out) -> int:
        if case.toggle:
            flips = sum(1 << p for p in range(8) if rng.random() < case.toggle)
            state["external"] ^= flips
        return pad_value(out, state["external"])

    return pins


async def run_case(h: Harness, case: Case, coverage: Coverage | None = None) -> None:
    h.context = case.describe
    await h.reset(2)
    h.pins = pin_supplier(case)
    if coverage is not None:
        coverage.attach(h, case)
    for engine, payload in case.triggers:
        await h.command(SELECT, engine)
        await h.command(TRIGGER, payload)
    for source, dest, count in case.routes:
        await h.command(ROUTE, source | dest << 2 | 16 | count << 5)
    # Load, prefill and start engine by engine, so engines run while others load.
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
        # Stop issuing traffic once the budget is spent, except a deselect: that
        # still runs, so it is never lost to the budget (at most one per case).
        if h.cycle - start >= case.cycles and op[0] != "deselect":
            continue
        await execute(h, op, noise, coverage)
    # Epilogue: halt everything, read every engine's diagnostics, drain RX FIFOs.
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


async def execute(h: Harness, op: list[Any], noise: random.Random, coverage: Coverage | None) -> None:
    kind = op[0]
    tally = coverage.host if coverage is not None else Counter()
    if kind == "idle":
        for _ in range(op[1]):
            await h.step(h.window << 6 | (noise.randrange(16) if op[2] else 0))
    elif kind == "tx":
        await h.command(SELECT, op[1])
        tally["tx word accepted" if await h.try_write(2, op[2], max_wait=op[3]) else "tx write abandoned (full)"] += 1
    elif kind == "rx":
        await h.command(SELECT, op[1])
        value = await h.try_read(3, max_wait=op[2], pauses=op[3])
        tally["rx word read" if value is not None else "rx read abandoned (empty)"] += 1
    elif kind == "status":
        await h.command(SELECT, op[1])
        await h.status(op[2])
        tally[f"status read select={op[2]}"] += 1
    elif kind == "cmd":
        await h.command(SELECT, op[2])
        await h.command(op[1] >> 24, op[1] & 0xFFFFFF)
    elif kind == "partial":
        await h.try_write(op[1], op[3], max_wait=2, nibbles=op[2])
        tally[f"partial write abandoned (window {op[1]})"] += 1
    elif kind == "partial_read":
        await h.command(SELECT, op[1])
        await h.try_read(op[2], max_wait=2, nibbles=op[3])
        tally[f"partial read abandoned (window {op[2]})"] += 1
    elif kind == "prog":  # one program word to the selected engine (window 1), outside load()
        written = await h.try_write(1, op[1], max_wait=2)
        tally["program word written" if written else "program word not accepted"] += 1
    elif kind == "reload":
        spec = op[2]
        await h.load(op[1], spec["words"], ownership=spec["ownership"], open_drain=spec["open_drain"])
        await h.command(START, 1 << op[1])
        tally["engine reprogrammed"] += 1
    elif kind == "drain":
        # A host that reads FIFO levels, then that many RX words (up to a cap).
        for e in range(h.model.config.engines):
            if op[1] >> e & 1:
                await h.command(SELECT, e)
                level = await h.status(2) >> 16
                for _ in range(min(level, op[2])):
                    value = await h.try_read(3, max_wait=8)
                    tally["rx word read" if value is not None else "rx read abandoned (empty)"] += 1
    elif kind == "revive":
        # A host that inspects status: clear faulted engines, restart halted ones.
        for e in range(h.model.config.engines):
            if op[1] >> e & 1:
                await h.command(SELECT, e)
                status = await h.status(0)
                if status & 8:
                    await h.command(CLEAR, 1 << e)
                    tally["revive: cleared fault"] += 1
                if status & 2 and not status & 1:
                    await h.command(START, 1 << e)
                    tally["revive: restarted"] += 1
    elif kind == "deselect":
        await h.reset(op[1], deselect=True)
        tally["deselect (ena low)"] += 1
    else:
        raise ValueError(f"unknown host op {kind}")


# ---------------------------------------------------------------- coverage
def allowed_fault_codes(word: int | None) -> set[int]:
    """Fault codes an instruction may legally raise (isa.md built-in codes)."""
    if word is None:
        return {2}
    op, imm24 = word >> 24, word & 0xFFFFFF
    if op == 29 and 0 < imm24 <= 255:
        return {imm24}
    if op == 7:
        return {1, 4}
    if op in (13, 15):
        return {1, 3}
    return {1}


class Coverage:
    """Functional coverage bins, collected from the model around each tick."""

    def __init__(self) -> None:
        self.executed: dict[int, Counter[str]] = defaultdict(Counter)
        self.stalls: Counter[str] = Counter()
        self.faults: Counter[str] = Counter()
        self.deliberate: Counter[str] = Counter()
        self.xfer: Counter[str] = Counter()
        self.commands: Counter[str] = Counter()
        self.host: Counter[str] = Counter()
        self.mover = 0
        self.misc: Counter[str] = Counter()
        self.cases = self.cycles = 0
        self._case: Case | None = None
        self._pre: list[tuple[Any, ...]] = []
        self._accepted: list[int] = []
        self._routes: list[Any] = []

    def attach(self, h: Harness, case: Case) -> None:
        self._case = case
        self.cases += 1
        model = h.model
        original = type(model).command.__get__(model)

        def command(word: int) -> bool:
            accepted = original(word)
            op = word >> 24
            self.commands[f"{COMMAND_NAMES.get(op, 'invalid-op')} {'accepted' if accepted else 'rejected'}"] += 1
            if accepted:
                self._accepted.append(op)
            return accepted

        model.command = command
        h.observers.append(self)

    def detach(self, h: Harness) -> None:
        if self in h.observers:
            h.observers.remove(self)
        h.model.__dict__.pop("command", None)
        self._case = None

    def before(self, h: Harness, ui: int, pins: int) -> None:
        model = h.model
        self._accepted = []
        self._routes = list(model.routes)
        self._pre = [(e.running, e.fault, e.wait, e.transfer is not None, e.pc, e.completed, e.program)
                     for e in model.engines]

    def after(self, h: Harness) -> None:
        model = h.model
        self.cycles += 1
        if h.expected is None or model.timestamp == 0:  # reset/deselect edge
            return
        out = h.expected
        if out.uo & UO_IRQ:
            self.misc["cycles with IRQ asserted"] += 1
        if out.uo & UO_FAULT:
            self.misc["cycles with fault output asserted"] += 1
        if out.uio_oe:
            self.misc["cycles with any pin driven"] += 1
        if not self._pre or len(self._pre) != len(model.engines):
            return
        control = {START, STOP, 1} & set(self._accepted)  # START/STOP/BEGIN pre-empt issue
        for index, e in enumerate(model.engines):
            running, fault, wait, in_transfer, pc, completed, program = self._pre[index]
            if START in self._accepted and e.running and not running:
                self.misc["engine starts"] += 1
            if not running or fault or control:
                continue
            word = program[pc] if 0 <= pc < len(program) else None
            op = None if word is None else word >> 24
            name = "PC-RANGE" if op is None else (MNEMONIC[op] if op < len(MNEMONIC) else "INVALID")
            if e.fault and not fault:
                allowed = allowed_fault_codes(word)
                assert e.fault in allowed, (f"engine {index} fault {e.fault} at pc {pc} ({name}) not in "
                                            f"allowed codes {sorted(allowed)}")
                self.faults[f"code {e.fault} from {name}"] += 1
                if op in (24, 25) and word & 7 and (word & 255) < model.config.width:
                    self.misc["byte-lane shift count faults (code 1)"] += 1
                spec = self._case.engines[index] if self._case else None
                if spec is not None and program == spec.words and pc in spec.expect:
                    assert e.fault == spec.expect[pc], (f"deliberate fault at engine {index} pc {pc}: got "
                                                        f"{e.fault}, expected {spec.expect[pc]}")
                    self.deliberate[f"code {e.fault} ({name})"] += 1
                continue
            if wait:
                continue
            if in_transfer:
                if e.transfer is None and e.completed != completed:
                    self.executed[index]["XFER"] += 1
                continue
            if e.completed != completed:
                if op != 17:
                    self.executed[index][name] += 1
                if op in (5, 11, 26) and e.pc == 127 and word & (0xFFFF if op == 26 else 0xFFFFFF) > 127:
                    self.misc["jump targets saturated to PC 127"] += 1
            elif op == 17 and e.transfer is not None:
                c = word & 0xFF
                self.xfer[f"CPOL{c & 1} CPHA{c >> 1 & 1} {'MSB' if c & 4 else 'LSB'}-first "
                          f"drive={c >> 3 & 1} sample={c >> 4 & 1}"] += 1
                bits = word >> 16 & 255
                self.xfer["bits=1" if bits == 1 else "bits=width" if bits == model.config.width
                          else "bits 2..width-1"] += 1
            elif op in (6, 7, 13, 15) and e.pc == pc:
                self.stalls[name] += 1
        if ROUTE not in self._accepted and FLUSH not in self._accepted:
            for source, before in enumerate(self._routes):
                after = model.routes[source]
                if before is not None and (after is None or after[1] == before[1] - 1) and after != before:
                    self.mover += 1

    def summary(self) -> str:
        lines = [f"FUNCTIONAL COVERAGE: {self.cases} cases, {self.cycles} lockstep cycles"]
        all_ops = set(MNEMONIC) - {"FAULT"}
        for index in sorted(self.executed):
            counts = self.executed[index]
            hit = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            missing = sorted(all_ops - set(counts))
            lines.append(f"  engine {index} executed: {hit}")
            lines.append(f"  engine {index} opcodes never completed: {missing or 'none'}")
        lines.append("  stalls (blocked issue cycles): " + (", ".join(f"{k}={v}" for k, v in
                                                                     sorted(self.stalls.items())) or "none"))
        lines.append("  faults: " + (", ".join(f"{k}={v}" for k, v in sorted(self.faults.items())) or "none"))
        lines.append("  deliberate faults hit with expected code: "
                     + (", ".join(f"{k}={v}" for k, v in sorted(self.deliberate.items())) or "none"))
        lines.append(f"  XFER modes issued ({sum(v for k, v in self.xfer.items() if k.startswith('CPOL'))}): "
                     + ", ".join(f"{k}={v}" for k, v in sorted(self.xfer.items())))
        modes = {k for k in self.xfer if k.startswith("CPOL")}
        lines.append(f"  XFER flag combinations covered: {len(modes)}/32")
        lines.append(f"  mover (DMA) word transfers: {self.mover}")
        lines.append("  host commands: " + ", ".join(f"{k}={v}" for k, v in sorted(self.commands.items())))
        lines.append("  host traffic: " + ", ".join(f"{k}={v}" for k, v in sorted(self.host.items())))
        lines.append("  other: " + ", ".join(f"{k}={v}" for k, v in sorted(self.misc.items())))
        return "\n".join(lines)


# ---------------------------------------------------------------- minimizer
async def fails(h: Harness, case: Case) -> bool:
    try:
        await run_case(h, case)
    except LockstepMismatch:
        return True
    finally:
        h.context = None
    return False


async def minimize(h: Harness, case: Case, budget: int, log) -> Case:  # noqa: ANN001
    """Greedy delta-debugging: drop host ops, engines, setup items; NOP out instructions."""
    attempts = 0
    best = copy.deepcopy(case)

    async def try_candidate(candidate: Case) -> bool:
        nonlocal attempts, best
        if attempts >= budget:
            return False
        attempts += 1
        if await fails(h, candidate):
            best = candidate
            return True
        return False

    # 1. Host operations: chunks, then singles.
    chunk = max(1, len(best.ops) // 2)
    while chunk >= 1 and attempts < budget:
        start = 0
        while start < len(best.ops) and attempts < budget:
            candidate = copy.deepcopy(best)
            del candidate.ops[start:start + chunk]
            if not await try_candidate(candidate):
                start += chunk
        chunk //= 2
    # 2. Whole engines, prefill, routes, triggers.
    for e in range(len(best.engines)):
        if best.engines[e] is not None:
            candidate = copy.deepcopy(best)
            candidate.engines[e] = None
            candidate.start_mask &= ~(1 << e)
            candidate.prefill[e] = []
            await try_candidate(candidate)
    for name in ("prefill", "routes", "triggers"):
        candidate = copy.deepcopy(best)
        setattr(candidate, name, [[] for _ in candidate.prefill] if name == "prefill" else [])
        await try_candidate(candidate)
    if best.toggle:
        candidate = copy.deepcopy(best)
        candidate.toggle = 0.0
        await try_candidate(candidate)
    # 3. Replace instructions with NOP (keeps every jump target valid).
    for e, spec in enumerate(best.engines):
        if spec is None:
            continue
        for pc in range(len(spec.words)):
            current = best.engines[e]
            if current is None or current.words[pc] == 0:
                continue
            candidate = copy.deepcopy(best)
            candidate.engines[e].words[pc] = 0
            candidate.engines[e].expect.pop(pc, None)
            await try_candidate(candidate)
    log(f"minimizer: {attempts} re-runs; ops {len(case.ops)} -> {len(best.ops)}, program words "
        f"{sum(len(s.words) for s in case.engines if s)} -> "
        f"{sum(sum(1 for w in s.words if w) for s in best.engines if s)} non-NOP")
    return best


class BugInjector:
    """Checker self-test (PE_INJECT_MODEL_BUG=xor): corrupt the model after XOR executes."""

    def __init__(self) -> None:
        self._pre: list[tuple[int, int]] = []

    def before(self, h: Harness, ui: int, pins: int) -> None:
        self._pre = [(e.completed, e.pc) for e in h.model.engines]

    def after(self, h: Harness) -> None:
        for (completed, pc), e in zip(self._pre, h.model.engines, strict=True):
            if e.completed != completed and 0 <= pc < len(e.program) and e.program[pc] >> 24 == 21:
                e.regs[e.program[pc] >> 16 & 3] ^= 1
