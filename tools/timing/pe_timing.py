#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""pe_timing: static, cycle-exact timing analyzer for protocol-emulator images.

The analyzer reads a ``protocol-emulator.firmware-image.v1`` JSON file (ISA v2,
``docs/isa.md``), decodes it, and abstractly executes it from START. Execution
is split at *synchronization boundaries*: the instructions whose completion
time depends on the outside world.

====================  ============================================  =========
boundary              waits for                                     bound
====================  ============================================  =========
``PULL``              a TX FIFO word (host or mover)                none
``PUSH a=0``          RX FIFO space                                 none
``WAITPIN p,b``       synchronized pin ``p`` == ``b``               LIMIT-1
``WAITEVENT``         own event mailbox                             LIMIT-1
====================  ============================================  =========

Between two boundaries every instruction has a fixed cost (1 cycle, ``WAIT n``
= n+1, ``XFER a,b`` = 2ab+1), so the issue time of every instruction and the
tick of every pin write is an exact offset from the *anchor*: the edge on
which the previous boundary completed (or the START edge). Data-dependent
branches (``JZ`` on unknown data) fork into separate path variants, each of
which is exact. ``COUNT n``/``LOOP`` loops are unrolled exactly, because the
repeat counter is a static immediate. Registers, the repeat counter, LIMIT,
the PINS selection and the logical pin state are tracked as abstract values,
so a boundary is analyzed once per distinct *context* (e.g. once per loop
iteration when a boundary sits inside a counted loop).

Time convention (the reference model's ``tick``): an instruction *issues* on
an edge; its pin write is visible on the pads right after that edge. Offsets
are edges after the anchor; the first instruction after a boundary issues at
+1. An engine observes a pad value two edges after the edge that sampled it,
so its own write on edge ``w`` is visible to its own ``WAITPIN``/``IN`` no
earlier than edge ``w+3`` (``LOOPBACK_LATENCY``).

Only the Python standard library is used. See ``tools/timing/README.md`` and
``docs/timing-analysis.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple

INF = math.inf
TOOL_VERSION = "1.0"

MNEMONICS = ("NOP", "HALT", "SET", "DIR", "WAIT", "JMP", "PULL", "PUSH", "OUT", "IN",
             "COUNT", "LOOP", "LIMIT", "WAITPIN", "SIGNAL", "WAITEVENT", "PINS", "XFER",
             "MOV", "LOAD", "ADD", "XOR", "AND", "OR", "SHL", "SHR", "JZ", "NOT", "TIME",
             "FAULT")
(NOP, HALT, SET, DIR, WAIT, JMP, PULL, PUSH, OUT, IN, COUNT, LOOP, LIMIT, WAITPIN, SIGNAL,
 WAITEVENT, PINS, XFER, MOV, LOAD, ADD, XOR, AND, OR, SHL, SHR, JZ, NOT, TIME, FAULT) = range(30)
REG_NAMES = ("tx", "rx", "x", "y")

RESET_LIMIT = 65535
SYNC_LATENCY = 2        # pad sampled on edge k is seen by an engine on edge k+2
LOOPBACK_LATENCY = 3    # own write on edge w is seen by the same engine on edge w+3

# Pad states are small bit sets: driven low, driven high, released (high-Z).
P0, P1, PZ = 1, 2, 4
PAD_TEXT = {1: "0", 2: "1", 4: "Z", 3: "0|1", 5: "0|Z", 6: "1|Z", 7: "?"}

# Blocking classes, spelled as the assembler's image ``timing`` table.
BLOCK_NONE, BLOCK_TX, BLOCK_RX, BLOCK_PIN, BLOCK_EVENT = (
    "none", "tx_fifo_unbounded", "rx_fifo_unbounded", "pin_limit", "event_limit")
BLOCK_BRANCH = "branch"   # zero-stall soft boundary at a data-dependent loop exit


def pad_levels(mask: int) -> int:
    """Possible *pad levels* (bit0: level 0, bit1: level 1) for a pad-state set.

    A released pad (Z) can be at either level: its level is set by the outside
    world (pull-up, peer, another driver)."""
    levels = 0
    if mask & P0:
        levels |= 1
    if mask & P1:
        levels |= 2
    if mask & PZ:
        levels |= 3
    return levels


def pad_mask(pin: int, vk: int, vb: int, dk: int, db: int, open_drain: int) -> int:
    """Possible pad states of an owned pin from abstract value/direction bits.

    ``vk``/``dk`` are known-bit masks, ``vb``/``db`` the known values. Push-pull:
    OE = dir, out = value. Open-drain (isa.md Machine): out = 0 and
    OE = dir AND NOT value, so SET high releases an enabled open-drain pin."""
    dirs = ((db >> pin) & 1,) if (dk >> pin) & 1 else (0, 1)
    vals = ((vb >> pin) & 1,) if (vk >> pin) & 1 else (0, 1)
    mask = 0
    od = (open_drain >> pin) & 1
    for d in dirs:
        for v in vals:
            if od:
                mask |= P0 if (d and not v) else PZ
            else:
                mask |= (P1 if v else P0) if d else PZ
    return mask


# ----------------------------------------------------------------------------
# Program images
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Arch:
    engines: int = 4
    width: int = 32
    program_words: int = 64
    fifo_words: int = 8
    fused: bool = True
    prefetch: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Arch":
        return cls(engines=int(data["engine_count"]), width=int(data["data_width"]),
                   program_words=int(data["program_words"]), fifo_words=int(data["fifo_words"]),
                   fused=data["issue"] == "fused", prefetch=bool(data["prefetch"]))

    def to_json(self) -> dict[str, Any]:
        return {"engine_count": self.engines, "data_width": self.width,
                "program_words": self.program_words, "fifo_words": self.fifo_words,
                "issue": "fused" if self.fused else "scalar", "prefetch": self.prefetch}


class Ins(NamedTuple):
    pc: int
    word: int
    op: int
    a: int
    b: int
    c: int
    imm24: int
    imm16: int

    @property
    def mnemonic(self) -> str:
        return MNEMONICS[self.op] if self.op < len(MNEMONICS) else f"OP{self.op}"

    def text(self, labels: dict[int, str] | None = None) -> str:
        op, a, b, c, imm24, imm16 = self.op, self.a, self.b, self.c, self.imm24, self.imm16
        m = self.mnemonic

        def target(value: int) -> str:
            name = (labels or {}).get(value)
            return f"{value}" + (f" <{name}>" if name else "")

        if op in (NOP, HALT, PULL, WAITEVENT):
            return m
        if op in (SET, DIR):
            return f"{m} 0x{imm24:02x}"
        if op in (WAIT, COUNT, LIMIT, FAULT):
            return f"{m} {imm24 if op != COUNT else imm16}"
        if op in (JMP, LOOP):
            return f"{m} {target(imm24)}"
        if op == PUSH:
            return "PUSH strict" if a else "PUSH"
        if op in (OUT, IN):
            return f"{m} pin{a} {'msb' if c else 'lsb'}"
        if op == WAITPIN:
            return f"WAITPIN pin{a}=={b}"
        if op == SIGNAL:
            return f"SIGNAL 0x{imm24:x}"
        if op == PINS:
            return f"PINS clk=pin{imm24 & 7} tx=pin{imm24 >> 3 & 7} rx=pin{imm24 >> 6 & 7}"
        if op == XFER:
            flags = [f"CPOL{c & 1}", f"CPHA{c >> 1 & 1}", "MSB" if c & 4 else "LSB"]
            if c & 8:
                flags.append("drive")
            if c & 16:
                flags.append("sample")
            return f"XFER bits={a} half={b} {'/'.join(flags)}"
        if op in (MOV, ADD, XOR, AND, OR):
            return f"{m} {REG_NAMES[a & 3]}, {REG_NAMES[b & 3]}"
        if op == LOAD:
            return f"LOAD {REG_NAMES[a & 3]}, {imm16}"
        if op == JZ:
            return f"JZ {REG_NAMES[a & 3]}, {target(imm16)}"
        if op in (SHL, SHR):
            return f"{m} {REG_NAMES[a & 3]}, {c}"
        if op in (NOT, TIME):
            return f"{m} {REG_NAMES[a & 3]}"
        return f"{m} a={a} b={b} c={c}"


def decode(word: int, pc: int = 0) -> Ins:
    word &= 0xFFFFFFFF
    return Ins(pc, word, word >> 24, word >> 16 & 255, word >> 8 & 255, word & 255,
               word & 0xFFFFFF, word & 0xFFFF)


def static_fault(ins: Ins, arch: Arch, ownership: int) -> int:
    """Fault code raised on issue independent of run-time state, else 0.

    Mirrors the operand checks of the reference model (``test/model/reference.py``
    ``Reference._step``) and isa.md. XFER pin ownership depends on the PINS
    state and is checked during exploration."""
    op, a, b, c, imm24 = ins.op, ins.a, ins.b, ins.c, ins.imm24
    if op in (NOP, HALT, PULL, WAITEVENT):
        invalid = bool(imm24)
    elif op == PUSH:
        invalid = a > 1 or b != 0 or c != 0
    elif op in (SET, DIR):
        invalid = bool(imm24 >> 8)
    elif op in (OUT, IN):
        invalid = a > 7 or b != 0 or c > 1
    elif op == COUNT:
        invalid = a != 0
    elif op == LIMIT:
        invalid = imm24 == 0
    elif op == WAITPIN:
        invalid = a > 7 or b > 1 or c != 0
    elif op == SIGNAL:
        invalid = bool(imm24 >> arch.engines)
    elif op == PINS:
        invalid = bool(imm24 >> 9)
    elif op == XFER:
        invalid = not arch.fused or not 1 <= a <= arch.width or b == 0 or c > 31
    elif op in (MOV, ADD, XOR, AND, OR):
        invalid = a > 3 or b > 3 or c != 0
    elif op in (LOAD, JZ):
        invalid = a > 3
    elif op in (SHL, SHR):
        invalid = a > 3 or b != 0 or c >= arch.width
    elif op in (NOT, TIME):
        invalid = a > 3 or b != 0 or c != 0
    elif op == FAULT:
        invalid = not 0 < imm24 <= 255
    elif op in (WAIT, JMP, LOOP):
        invalid = False
    else:
        return 1  # invalid opcode
    if invalid:
        return 1
    if (op in (SET, DIR) and imm24 & ~ownership) or (op == OUT and not (ownership >> a) & 1):
        return 1  # pin ownership
    return 0


def blocking_class(ins: Ins) -> str:
    if ins.op == PULL:
        return BLOCK_TX
    if ins.op == PUSH and ins.a == 0:
        return BLOCK_RX
    if ins.op == WAITPIN:
        return BLOCK_PIN
    if ins.op == WAITEVENT:
        return BLOCK_EVENT
    return BLOCK_NONE


def minimum_cycles(ins: Ins) -> int:
    """Cycles from issue to the next issue when nothing blocks."""
    if ins.op == WAIT:
        return ins.imm24 + 1
    if ins.op == XFER:
        return 2 * ins.a * ins.b + 1
    return 1


@dataclass
class Program:
    name: str
    arch: Arch
    engine: int
    ownership: int
    open_drain: int
    words: tuple[int, ...]
    labels: dict[str, int] = field(default_factory=dict)
    clock_hz: int = 50_000_000
    timing: list[dict[str, Any]] | None = None
    notes: list[str] = field(default_factory=list)
    path: str | None = None
    source_sha256: str | None = None
    bytecode_sha256: str | None = None

    def __post_init__(self) -> None:
        self.ins = tuple(decode(w, pc) for pc, w in enumerate(self.words))
        self.faults = tuple(static_fault(i, self.arch, self.ownership) for i in self.ins)
        self.label_at = {pc: name for name, pc in sorted(self.labels.items(), key=lambda kv: kv[1])}

    @classmethod
    def from_image(cls, path: str | Path, *, arch_override: Arch | None = None) -> "Program":
        path = Path(path)
        data = json.loads(path.read_text())
        if data.get("schema_version") != "protocol-emulator.firmware-image.v1":
            raise ValueError(f"{path}: not a protocol-emulator firmware image")
        if data.get("isa_version") not in (1, 2):
            raise ValueError(f"{path}: unsupported ISA version {data.get('isa_version')}")
        arch = arch_override or Arch.from_json(data["architecture"])
        return cls(name=data["name"], arch=arch, engine=int(data["engine"]),
                   ownership=int(data["owned_pins"]), open_drain=int(data["open_drain"]),
                   words=tuple(int(w) for w in data["words"]), labels=dict(data.get("labels", {})),
                   clock_hz=int(data.get("clock_hz", 50_000_000)), timing=data.get("timing"),
                   notes=list(data.get("notes", [])), path=str(path),
                   source_sha256=data.get("source_sha256"), bytecode_sha256=data.get("bytecode_sha256"))

    def identity(self) -> dict[str, Any]:
        """Check the image's bytecode hash (and source hash when the source is present)."""
        payload = b"".join(w.to_bytes(4, "little") for w in self.words)
        result: dict[str, Any] = {"bytecode_sha256": hashlib.sha256(payload).hexdigest()}
        result["bytecode_ok"] = self.bytecode_sha256 in (None, result["bytecode_sha256"])
        if self.path:
            source = Path(self.path.replace(".image.json", ".source.json"))
            if source.exists():
                result["source_ok"] = hashlib.sha256(source.read_bytes()).hexdigest() == self.source_sha256
        return result


# ----------------------------------------------------------------------------
# Abstract execution
# ----------------------------------------------------------------------------

class AState(NamedTuple):
    """Abstract engine state before issuing ``pc``. ``None`` means unknown."""
    pc: int
    repeat: int | None
    limit: int
    regs: tuple
    xpins: tuple          # PINS selection (clock, tx, rx)
    vk: int               # logical output value: known-bit mask
    vb: int               #   known values
    dk: int               # logical output enable: known-bit mask
    db: int               #   known values


START_STATE = AState(0, 0, RESET_LIMIT, (0, 0, 0, 0), (0, 0, 0), 0xFF, 0, 0xFF, 0)

# An event is a tuple (t, kind, pin, pc, before, after, cause):
#   anchor  t=0: the boundary (or START) completion edge; pin = boundary pc (-1: START),
#           pc = first pc of the segment
#   issue   instruction issued at pc (cause = mnemonic)
#   pad     owned pad state change; before/after are pad-state sets (P0|P1|PZ)
#   sample  input pin sampled (IN, XFER sample transition); sees the pad 2 edges earlier
#   inter   FIFO/event interaction that does not block (cause push_strict, signal, time)
#   arrive  first attempt of the next boundary instruction (pc)
ET, EK, EPIN, EPC, EBEFORE, EAFTER, ECAUSE = range(7)


@dataclass
class Variant:
    events: list
    end: tuple                      # ('boundary', t, pc) | ('halt', t) | ('fault', t, code)
                                    # | ('periodic', t_first, period) | ('budget', t)
    exit_state: AState | None = None
    wait_min: int | None = None     # WAITPIN on an owned pin: minimum stall (None: deadlock)
    succ: Any = None                # successor node key
    loop_from: int | None = None    # periodic: index of the first repeating event
    conditional_faults: list = field(default_factory=list)

    @property
    def kind(self) -> str:
        return self.end[0]

    @property
    def t_end(self) -> int:
        return self.end[1]

    def issues(self) -> list[tuple[int, int]]:
        return [(e[ET], e[EPC]) for e in self.events if e[EK] in ("issue", "arrive")]

    def pad_events(self) -> list[tuple]:
        return [e for e in self.events if e[EK] == "pad"]


@dataclass
class Node:
    key: Any
    kind: str                     # 'start' or boundary class
    bpc: int                      # boundary pc (-1 for START)
    state: AState                 # state at the boundary (before it completes)
    min_stall: int = 0
    max_stall: float = 0
    deadlock: str | None = None
    variants: list = field(default_factory=list)
    incomplete: bool = False
    widened: bool = False
    preds: int = 0
    stall_set: bool = False
    dirty: bool = False


@dataclass
class Limits:
    max_contexts: int = 512        # contexts per boundary pc before widening
    max_variants: int = 4096       # path variants per segment
    max_events: int = 400_000      # events per variant (unrolling budget)
    total_events: int = 4_000_000  # events over the whole analysis (memory bound)


class _Path:
    __slots__ = ("st", "t", "ev", "li", "seen", "cfaults")   # li: own pad states at the anchor

    def __init__(self, st: AState, t: int, ev: list, li: tuple, seen: dict, cfaults: list) -> None:
        self.st, self.t, self.ev, self.li, self.seen, self.cfaults = st, t, ev, li, seen, cfaults

    def fork(self, st: AState, t: int) -> "_Path":
        return _Path(st, t, list(self.ev), self.li, dict(self.seen), list(self.cfaults))


class Analysis:
    """Boundary graph of one program: nodes are (boundary pc, context)."""

    def __init__(self, program: Program, limits: Limits | None = None) -> None:
        self.p = program
        self.limits = limits or Limits()
        self.width_mask = (1 << program.arch.width) - 1
        self.own = program.ownership
        self.od = program.open_drain
        self.nodes: dict[Any, Node] = {}
        self.order: list[Any] = []
        self.contexts: dict[int, list] = defaultdict(list)
        self.widened_state: dict[int, AState] = {}
        self.warnings: list[str] = []
        self.total_events = 0
        self.truncated = False       # global budget hit: some nodes left unexplored
        self.soft = self._soft_branches()
        self._explore()

    # ------------------------------------------------------------ helpers
    def pads(self, st: AState) -> tuple:
        return tuple(pad_mask(p, st.vk, st.vb, st.dk, st.db, self.od) if (self.own >> p) & 1 else 0
                     for p in range(8))

    def _pad_events(self, path: _Path, pc: int, cause: str, old: AState, new: AState, pins: int,
                    *, value: bool = True) -> None:
        """Record possible pad changes of ``pins`` when the value (or direction) bits are written.

        The bit that is not written keeps its concrete value, so a write can
        change a pad only if some concrete completion of the old state differs
        from the corresponding completion of the new one."""
        t = path.t
        od = self.od
        for p in range(8):
            if not ((pins & self.own) >> p) & 1:
                continue
            vs = ((old.vb >> p) & 1,) if (old.vk >> p) & 1 else (0, 1)
            ds = ((old.db >> p) & 1,) if (old.dk >> p) & 1 else (0, 1)
            if value:
                nv = ((new.vb >> p) & 1,) if (new.vk >> p) & 1 else (0, 1)
                change = any(_pad1(v, d, od, p) != _pad1(w, d, od, p) for v in vs for d in ds for w in nv)
            else:
                nd = ((new.db >> p) & 1,) if (new.dk >> p) & 1 else (0, 1)
                change = any(_pad1(v, d, od, p) != _pad1(v, e, od, p) for v in vs for d in ds for e in nd)
            if not change:
                continue
            before = pad_mask(p, old.vk, old.vb, old.dk, old.db, od)
            after = pad_mask(p, new.vk, new.vb, new.dk, new.db, od)
            path.ev.append((t, "pad", p, pc, before, after, cause))

    def _wait_min_stall(self, path: "_Path", pin: int, level: int, t_arr: int) -> int | None:
        """Minimum stall of ``WAITPIN pin==level`` arriving at ``t_arr`` on an owned pin.

        The attempt on edge T reads the pad as left by edge T-3 (LOOPBACK_LATENCY),
        so only the engine's own pad states after edges t_arr-3 .. t_arr-1 matter;
        the last one persists while the engine waits. Before the anchor the pad
        is unknown and treated as possibly at the level (a sound lower bound).
        Returns None when the engine itself forces the opposite level."""
        want = 1 << level
        for stall in range(LOOPBACK_LATENCY):
            x = t_arr - LOOPBACK_LATENCY + stall
            if x < 0:
                return stall
            state = path.li[pin]
            for e in path.ev:
                if e[ET] > x:
                    break
                if e[EK] == "pad" and e[EPIN] == pin:
                    state = e[EAFTER]
            if pad_levels(state) & want:
                return stall
        return None

    def _release(self, path: _Path, pc: int, cause: str) -> None:
        st = path.st
        for p in range(8):
            if not (self.own >> p) & 1:
                continue
            before = pad_mask(p, st.vk, st.vb, st.dk, st.db, self.od)
            if before != PZ:
                path.ev.append((path.t, "pad", p, pc, before, PZ, cause))


    # ------------------------------------------------------------ graph
    def _explore(self) -> None:
        start = Node(("START",), "start", -1, START_STATE)
        self.nodes[start.key] = start
        self.order.append(start.key)
        queue = [start.key]
        while queue:
            key = queue.pop(0)
            node = self.nodes[key]
            if node.deadlock:
                continue
            if self.total_events > self.limits.total_events:
                if not self.truncated:
                    self.warnings.append(f"analysis event budget {self.limits.total_events} exceeded; "
                                         "remaining contexts unexplored")
                self.truncated = True
                node.incomplete = True
                continue
            if node.kind == "start":
                starts = [START_STATE]
            elif node.kind == BLOCK_BRANCH:
                starts = self._branch_starts(node)
            else:
                st = node.state
                ins = self.p.ins[node.bpc]
                regs = st.regs
                if ins.op == PULL:
                    regs = (None,) + regs[1:]
                starts = [st._replace(pc=node.bpc + 1, regs=regs)]
            node.variants, node.incomplete = [], False
            for st in starts:
                variants, incomplete = self._segment(st, 1, node.bpc)
                node.variants += variants
                node.incomplete |= incomplete
            for v in node.variants:
                if v.kind != "boundary":
                    continue
                succ = self._node_for(v)
                v.succ = succ.key
                succ.preds += 1
                if succ.key not in self.order:
                    self.order.append(succ.key)
                    queue.append(succ.key)
                elif succ.dirty:
                    queue.append(succ.key)
                succ.dirty = False

    def _node_for(self, v: Variant) -> Node:
        st, bpc = v.exit_state, v.end[2]
        key = (bpc, st)
        node = self.nodes.get(key)
        if node is None:
            ctx = self.contexts[bpc]
            if len(ctx) < self.limits.max_contexts:
                ctx.append(st)
                node = Node(key, self._kind(bpc), bpc, st)
                self.nodes[key] = node
            else:
                # Too many contexts: merge further ones into one over-approximate node.
                wkey = ("W", bpc)
                old = self.widened_state.get(bpc)
                joined = st if old is None else _join(old, st)
                if old is not None and joined.xpins != st.xpins:
                    self.warnings.append(f"boundary pc{bpc}: PINS differs between merged contexts")
                node = self.nodes.get(wkey)
                if node is None or joined != old:
                    if node is None:
                        self.warnings.append(f"boundary pc{bpc}: more than {self.limits.max_contexts} "
                                             "contexts; widened (over-approximate)")
                    self.widened_state[bpc] = joined
                    fresh = Node(wkey, self._kind(bpc), bpc, joined, widened=True,
                                 dirty=node is not None, preds=node.preds if node else 0)
                    if node is not None:
                        fresh.min_stall, fresh.stall_set = node.min_stall, node.stall_set
                    node = fresh
                    self.nodes[wkey] = node
        self._stall_bounds(node, v)
        return node

    def _stall_bounds(self, node: Node, v: Variant) -> None:
        """Stall range at a boundary for one incoming variant (the node keeps the smallest)."""
        ins = self.p.ins[node.bpc]
        st = node.state
        if node.kind == BLOCK_BRANCH:
            node.max_stall, node.min_stall, node.stall_set = 0, 0, True
            return
        if ins.op in (PULL, PUSH):
            node.max_stall, node.min_stall, node.stall_set = INF, 0, True
            return
        node.max_stall = st.limit - 1
        minimum = 0
        if ins.op == WAITPIN and (self.own >> ins.a) & 1:
            if v.wait_min is None:
                node.deadlock = (f"WAITPIN pin{ins.a}=={ins.b} while the engine itself forces the "
                                 f"opposite level: guaranteed timeout (fault 3) after {st.limit} attempts")
                minimum = st.limit - 1
            else:
                minimum = v.wait_min
        node.min_stall = minimum if not node.stall_set else min(node.min_stall, minimum)
        node.stall_set = True

    def _kind(self, bpc: int) -> str:
        return BLOCK_BRANCH if bpc in self.soft else blocking_class(self.p.ins[bpc])

    def _soft_branches(self) -> set[int]:
        """JZ/LOOP instructions on a CFG cycle that contains no blocking instruction.

        A data-dependent exit from such a loop can happen on any iteration, so
        the analysis anchors a zero-stall *soft boundary* there instead of
        unrolling; the loop then becomes a cycle of the boundary graph."""
        n = len(self.p.ins)
        succs: dict[int, list] = {}
        for i in self.p.ins:
            op = i.op
            if (self.p.faults[i.pc] or op in (HALT, FAULT, PULL, WAITPIN, WAITEVENT)
                    or (op == PUSH and i.a == 0)):
                nxt = []
            elif op == JMP:
                nxt = [i.imm24]
            elif op == LOOP:
                nxt = [i.imm24, i.pc + 1]
            elif op == JZ:
                nxt = [i.imm16, i.pc + 1]
            else:
                nxt = [i.pc + 1]
            succs[i.pc] = [(0, s) for s in nxt if 0 <= s < n]
        cyc = _cyclic(list(range(n)), succs)
        return {pc for pc in cyc if self.p.ins[pc].op in (JZ, LOOP)}

    def _branch_starts(self, node: Node) -> list[AState]:
        st, ins = node.state, self.p.ins[node.bpc]
        if ins.op == JZ:
            regs = list(st.regs)
            regs[ins.a] = 0
            return [st._replace(pc=ins.imm16, regs=tuple(regs)), st._replace(pc=ins.pc + 1)]
        return [st._replace(pc=ins.imm24), st._replace(pc=ins.pc + 1, repeat=0)]

    # ------------------------------------------------------------ segments
    def _segment(self, st: AState, t0: int, bpc: int = -1) -> tuple[list[Variant], bool]:
        p = self.p
        n = len(p.ins)
        mask = self.width_mask
        width = p.arch.width
        limits = self.limits
        out: list[Variant] = []
        incomplete = False
        stack = [_Path(st, t0, [(0, "anchor", bpc, st.pc, 0, 0, "")], self.pads(st), {}, [])]
        while stack:
            if len(out) >= limits.max_variants:
                incomplete = True
                self.warnings.append(f"segment variant budget {limits.max_variants} exceeded")
                break
            path = stack.pop()
            while True:
                st, t, ev = path.st, path.t, path.ev
                pc = st.pc
                if len(ev) > limits.max_events or self.total_events + len(ev) > limits.total_events:
                    out.append(Variant(ev, ("budget", t)))
                    incomplete = True
                    break
                if not 0 <= pc < n:
                    ev.append((t, "issue", -1, pc, 0, 0, "PC-RANGE"))
                    self._release(path, pc, "fault2")
                    out.append(Variant(ev, ("fault", t, 2), conditional_faults=path.cfaults))
                    break
                ins = p.ins[pc]
                op = ins.op
                if p.faults[pc]:
                    ev.append((t, "issue", -1, pc, 0, 0, ins.mnemonic))
                    self._release(path, pc, "fault1")
                    out.append(Variant(ev, ("fault", t, 1), conditional_faults=path.cfaults))
                    break
                if pc in self.soft and ((op == JZ and st.regs[ins.a] is None)
                                        or (op == LOOP and st.repeat is None)):
                    # data-dependent branch inside a boundary-free loop: zero-stall soft boundary
                    ev.append((t, "arrive", -1, pc, 0, 0, ins.mnemonic))
                    out.append(Variant(ev, ("boundary", t, pc), exit_state=st, wait_min=0,
                                       conditional_faults=path.cfaults))
                    break
                if op in (PULL, WAITPIN, WAITEVENT) or (op == PUSH and ins.a == 0):
                    ev.append((t, "arrive", -1, pc, 0, 0, ins.mnemonic))
                    wmin = (self._wait_min_stall(path, ins.a, ins.b, t)
                            if op == WAITPIN and (self.own >> ins.a) & 1 else 0)
                    out.append(Variant(ev, ("boundary", t, pc), exit_state=st, wait_min=wmin,
                                       conditional_faults=path.cfaults))
                    break
                ev.append((t, "issue", -1, pc, 0, 0, ins.mnemonic))
                if op == HALT:
                    self._release(path, pc, "HALT")
                    out.append(Variant(ev, ("halt", t), conditional_faults=path.cfaults))
                    break
                if op == FAULT:
                    self._release(path, pc, f"fault{ins.imm24}")
                    out.append(Variant(ev, ("fault", t, ins.imm24), conditional_faults=path.cfaults))
                    break
                nxt, dt = pc + 1, 1
                new = st
                regs = st.regs
                if op == SET:
                    new = st._replace(vk=0xFF, vb=ins.imm24 & 0xFF)
                    self._pad_events(path, pc, "SET", st, new, 0xFF)
                elif op == DIR:
                    new = st._replace(dk=0xFF, db=ins.imm24 & 0xFF)
                    self._pad_events(path, pc, "DIR", st, new, 0xFF, value=False)
                elif op == WAIT:
                    dt = ins.imm24 + 1
                elif op == JMP:
                    nxt = ins.imm24
                elif op == PUSH:  # strict: never blocks, faults 4 on a full RX FIFO
                    ev.append((t, "inter", -1, pc, 0, 0, "push_strict"))
                    path.cfaults.append((t, pc, 4))
                elif op == OUT:
                    bit, tx = _shift_out(regs[0], bool(ins.c), width, mask)
                    new = _drive(st, ins.a, bit)._replace(regs=(tx,) + regs[1:])
                    self._pad_events(path, pc, "OUT", st, new, 1 << ins.a)
                elif op == IN:
                    ev.append((t, "sample", ins.a, pc, 0, 0, "IN"))
                    new = st._replace(regs=(regs[0], None, regs[2], regs[3]))
                elif op == COUNT:
                    new = st._replace(repeat=ins.imm16)
                elif op == LOOP:
                    if st.repeat is None:
                        self._fork(stack, out, path, st._replace(pc=ins.imm24), t + 1, pc)
                    elif st.repeat:
                        new = st._replace(repeat=st.repeat - 1)
                        nxt = ins.imm24
                elif op == LIMIT:
                    new = st._replace(limit=ins.imm24)
                elif op == SIGNAL:
                    ev.append((t, "inter", -1, pc, 0, 0, "signal"))
                elif op == PINS:
                    new = st._replace(xpins=(ins.imm24 & 7, ins.imm24 >> 3 & 7, ins.imm24 >> 6 & 7))
                elif op == XFER:
                    result = self._xfer(path, ins)
                    if result is None:
                        self._release(path, pc, "fault1")
                        out.append(Variant(path.ev, ("fault", t, 1), conditional_faults=path.cfaults))
                        break
                    new, dt = result
                elif op in (MOV, LOAD, ADD, XOR, AND, OR, SHL, SHR, NOT, TIME):
                    new = st._replace(regs=_alu(ins, regs, mask))
                    if op == TIME:
                        ev.append((t, "inter", -1, pc, 0, 0, "time"))
                elif op == JZ:
                    value = regs[ins.a]
                    if value is None:
                        taken = list(regs)
                        taken[ins.a] = 0
                        self._fork(stack, out, path, st._replace(pc=ins.imm16, regs=tuple(taken)), t + 1, pc)
                    elif value == 0:
                        nxt = ins.imm16
                # NOP: nothing
                new = new._replace(pc=nxt)
                path.st, path.t = new, t + dt
                if nxt <= pc:  # backward branch: detect a boundary-free periodic loop
                    seen = path.seen.get(new)
                    if seen is not None:
                        t_first, idx = seen
                        v = Variant(ev, ("periodic", t_first, path.t - t_first), loop_from=idx,
                                    conditional_faults=path.cfaults)
                        out.append(v)
                        break
                    path.seen[new] = (path.t, len(ev))
            if out:
                self.total_events += len(out[-1].events)
        return out, incomplete

    @staticmethod
    def _fork(stack: list, out: list, path: _Path, st: AState, t: int, pc: int) -> None:
        child = path.fork(st, t)
        if st.pc <= pc:
            seen = child.seen.get(st)
            if seen is not None:
                out.append(Variant(child.ev, ("periodic", seen[0], t - seen[0]), loop_from=seen[1],
                                   conditional_faults=child.cfaults))
                return
            child.seen[st] = (t, len(child.ev))
        stack.append(child)

    def _xfer(self, path: _Path, ins: Ins) -> tuple[AState, int] | None:
        st, t = path.st, path.t
        clock, txp, rxp = st.xpins
        c = ins.c
        cpol, cpha, msb, drive, sample = (bool(c >> i & 1) for i in range(5))
        required = (1 << clock) | ((1 << txp) if drive else 0)
        if required & ~self.own or (drive and clock == txp):
            return None
        mask, width = self.width_mask, self.p.arch.width
        pc = ins.pc
        tx, rx = st.regs[0], st.regs[1]
        cur = _drive(st, clock, int(cpol))
        self._pad_events(path, pc, "XFER-clk", st, cur, 1 << clock)
        if drive and not cpha:
            bit = None if tx is None else ((tx >> (width - 1)) & 1 if msb else tx & 1)
            nxt = _drive(cur, txp, bit)
            self._pad_events(path, pc, "XFER-data", cur, nxt, 1 << txp)
            cur = nxt
        bits, half = ins.a, ins.b
        for k in range(2 * bits):
            path.t = t + (k + 1) * half
            active = k % 2 == 0
            prev = cur
            cur = _drive(cur, clock, int(cpol != active))
            self._pad_events(path, pc, "XFER-clk", prev, cur, 1 << clock)
            if drive and cpha and active:
                bit, tx = _shift_out(tx, msb, width, mask)
                prev = cur
                cur = _drive(cur, txp, bit)
                self._pad_events(path, pc, "XFER-data", prev, cur, 1 << txp)
            if drive and not cpha and not active:
                tx = None if tx is None else (((tx << 1) if msb else (tx >> 1)) & mask)
                if k < 2 * bits - 1:
                    bit = None if tx is None else ((tx >> (width - 1)) & 1 if msb else tx & 1)
                    prev = cur
                    cur = _drive(cur, txp, bit)
                    self._pad_events(path, pc, "XFER-data", prev, cur, 1 << txp)
            if sample and (not active if cpha else active):
                path.ev.append((path.t, "sample", rxp, pc, 0, 0, "XFER"))
                rx = None
        path.t = t
        regs = (tx, rx) + st.regs[2:]
        return cur._replace(regs=regs), 2 * bits * half + 1

    # ------------------------------------------------------------ queries
    def start_node(self) -> Node:
        return self.nodes[("START",)]

    def iter_nodes(self) -> Iterable[Node]:
        for key in self.order:
            yield self.nodes[key]

    def boundary_nodes(self) -> list[Node]:
        return [n for n in self.iter_nodes() if n.kind != "start"]

    def is_exact(self) -> bool:
        return not self.truncated and not any(n.widened or n.incomplete for n in self.iter_nodes())

    def query(self, pred: Callable[[tuple], bool]) -> "Query":
        return Query(self, pred)


def _join(a: AState, b: AState) -> AState:
    def j(x, y):
        return x if x == y else None
    regs = tuple(j(x, y) for x, y in zip(a.regs, b.regs))
    vk = a.vk & b.vk & ~(a.vb ^ b.vb)
    dk = a.dk & b.dk & ~(a.db ^ b.db)
    xpins = a.xpins if a.xpins == b.xpins else a.xpins  # PINS is static per path in practice
    return AState(a.pc, j(a.repeat, b.repeat), max(a.limit, b.limit), regs, xpins,
                  vk & 0xFF, a.vb & vk, dk & 0xFF, a.db & dk)


def _pad1(v: int, d: int, open_drain: int, pin: int) -> int:
    if (open_drain >> pin) & 1:
        return P0 if (d and not v) else PZ
    return (P1 if v else P0) if d else PZ


def _drive(st: AState, pin: int, bit: int | None) -> AState:
    m = 1 << pin
    if bit is None:
        return st._replace(vk=st.vk & ~m, vb=st.vb & ~m)
    return st._replace(vk=st.vk | m, vb=(st.vb & ~m) | (m if bit else 0))


def _shift_out(tx: int | None, msb: bool, width: int, mask: int) -> tuple[int | None, int | None]:
    if tx is None:
        return None, None
    bit = (tx >> (width - 1)) & 1 if msb else tx & 1
    return bit, ((tx << 1) if msb else (tx >> 1)) & mask


def _alu(ins: Ins, regs: tuple, mask: int) -> tuple:
    r = list(regs)
    op, a, b = ins.op, ins.a, ins.b
    x, y = r[a], r[b] if b < 4 else None
    if op == MOV:
        r[a] = y
    elif op == LOAD:
        r[a] = ins.imm16
    elif op == ADD:
        r[a] = None if x is None or y is None else (x + y) & mask
    elif op == XOR:
        r[a] = 0 if a == b else (None if x is None or y is None else x ^ y)
    elif op == AND:
        r[a] = 0 if (x == 0 or y == 0) else (None if x is None or y is None else x & y)
    elif op == OR:
        r[a] = mask if (x == mask or y == mask) else (None if x is None or y is None else x | y)
    elif op == SHL:
        r[a] = None if x is None else (x << ins.c) & mask
    elif op == SHR:
        r[a] = None if x is None else x >> ins.c
    elif op == NOT:
        r[a] = None if x is None else x ^ mask
    elif op == TIME:
        r[a] = None
    return tuple(r)


# ----------------------------------------------------------------------------
# Event-distance queries over the boundary graph
# ----------------------------------------------------------------------------

class Query:
    """Distance from an event to the next event matching ``pred``.

    Within one path variant the distance is exact. When the search crosses a
    boundary, the stall of that boundary is added: the minimum uses its
    minimum stall (0, or the loopback latency for a pin the engine released
    itself) and the maximum uses LIMIT-1 (WAITPIN/WAITEVENT) or infinity
    (PULL/PUSH). ``miss`` is True when some path never reaches a match
    (it halts, faults, or cycles without one)."""

    def __init__(self, analysis: Analysis, pred: Callable[[tuple], bool]) -> None:
        self.an = analysis
        self.pred = pred
        self.first: dict[tuple, int | None] = {}
        for node in analysis.iter_nodes():
            for i, v in enumerate(node.variants):
                self.first[(node.key, i)] = self._first_in(v, 0, None)
        self._min = self._solve_min()
        self._max, self._miss = self._solve_max()

    def _first_in(self, v: Variant, start: int, t_from: int | None) -> int | None:
        """Time of the first matching event at index >= start (periodic loops wrap)."""
        for e in v.events[start:]:
            if self.pred(e):
                return e[ET]
        if v.kind == "periodic" and v.loop_from is not None:
            period = v.end[2]
            body = v.events[v.loop_from:]
            for e in body:
                if self.pred(e):
                    return e[ET] + period
        return None

    def _edges(self, node: Node):
        for i, v in enumerate(node.variants):
            yield i, v, self.first[(node.key, i)]

    def _solve_min(self) -> dict:
        an = self.an
        best = {k: INF for k in an.order}
        changed = True
        rounds = 0
        while changed and rounds <= len(an.order) + 2:
            changed = False
            rounds += 1
            for key in an.order:
                node = an.nodes[key]
                value = INF
                for i, v, tb in self._edges(node):
                    if tb is not None:
                        value = min(value, tb)
                    elif v.kind == "boundary":
                        s = an.nodes[v.succ]
                        if not s.deadlock:
                            value = min(value, v.t_end + s.min_stall + best[s.key])
                if value < best[key]:
                    best[key] = value
                    changed = True
        return best

    def _solve_max(self) -> tuple[dict, dict]:
        an = self.an
        succs: dict[Any, list] = {k: [] for k in an.order}   # match-free continuations
        miss: dict[Any, bool] = {k: False for k in an.order}
        local: dict[Any, float] = {k: -INF for k in an.order}
        for key in an.order:
            node = an.nodes[key]
            for i, v, tb in self._edges(node):
                if tb is not None:
                    local[key] = max(local[key], tb)
                elif v.kind == "boundary":
                    s = an.nodes[v.succ]
                    if s.deadlock:
                        miss[key] = True
                    else:
                        succs[key].append((v.t_end + s.max_stall, s.key))
                else:
                    miss[key] = True       # halts, faults or loops without a match
        cyclic = _cyclic(an.order, succs)
        result: dict = {}
        out_miss: dict = {}
        for key in _postorder(an.order, succs):
            value, m = local[key], miss[key]
            if key in cyclic:
                value, m = INF, True       # a match-free cycle can repeat forever
            for cost, w in succs[key]:
                if w in result:
                    value = max(value, cost + result[w])
                    m = m or out_miss[w]
                else:                      # back edge: only inside a cycle
                    value, m = INF, True
            result[key] = value
            out_miss[key] = m
        return result, out_miss

    # -- public
    def from_anchor(self, node: Node) -> tuple[float, float, bool]:
        return self._min[node.key], self._max[node.key], self._miss[node.key]

    def after(self, node: Node, vi: int, ei: int) -> tuple[float, float, bool]:
        """Distance from event ``ei`` of variant ``vi`` of ``node`` to the next match."""
        v = node.variants[vi]
        t_a = v.events[ei][ET]
        for e in v.events[ei + 1:]:
            if self.pred(e):
                d = e[ET] - t_a
                return d, d, False
        if v.kind == "periodic" and v.loop_from is not None:
            period = v.end[2]
            for e in v.events[v.loop_from:]:
                if self.pred(e):
                    d = e[ET] + period - t_a
                    return d, d, False
            return INF, INF, True
        if v.kind != "boundary":
            return INF, -INF, True
        s = self.an.nodes[v.succ]
        if s.deadlock:
            return INF, -INF, True
        base = v.t_end - t_a
        return (base + s.min_stall + self._min[s.key], base + s.max_stall + self._max[s.key],
                self._miss[s.key])


def _postorder(order: list, succs: dict) -> list:
    seen: set = set()
    out: list = []
    for root in order:
        if root in seen:
            continue
        stack = [(root, iter(succs[root]))]
        seen.add(root)
        while stack:
            node, it = stack[-1]
            advanced = False
            for _, w in it:
                if w not in seen:
                    seen.add(w)
                    stack.append((w, iter(succs[w])))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                out.append(node)
    return out


def _cyclic(order: list, succs: dict) -> set:
    """Nodes on a cycle of ``succs`` (iterative Tarjan SCC)."""
    index: dict = {}
    low: dict = {}
    onstack: set = set()
    stack: list = []
    cyclic: set = set()
    counter = 0
    for root in order:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                onstack.add(v)
            edges = succs[v]
            descended = False
            while i < len(edges):
                w = edges[i][1]
                i += 1
                if w not in index:
                    work.append((v, i))
                    work.append((w, 0))
                    descended = True
                    break
                if w in onstack:
                    low[v] = min(low[v], index[w])
            if descended:
                continue
            if low[v] == index[v]:
                members = []
                while True:
                    w = stack.pop()
                    onstack.discard(w)
                    members.append(w)
                    if w == v:
                        break
                if len(members) > 1 or any(e[1] == v for e in succs[v]):
                    cyclic.update(members)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return cyclic


def occurrences(an: Analysis, pred: Callable[[tuple], bool]) -> Iterable[tuple[Node, int, int, tuple]]:
    for node in an.iter_nodes():
        for vi, v in enumerate(node.variants):
            for ei, e in enumerate(v.events):
                if pred(e):
                    yield node, vi, ei, e


# ----------------------------------------------------------------------------
# Summaries
# ----------------------------------------------------------------------------

def fmt_cycles(x: float) -> str:
    if x == INF:
        return "inf"
    if x == -INF:
        return "-"
    return str(int(x))


def node_label(an: Analysis, node: Node) -> str:
    if node.kind == "start":
        return "START"
    ins = an.p.ins[node.bpc]
    ctx = []
    if node.state.repeat not in (None, 0):
        ctx.append(f"repeat={node.state.repeat}")
    if node.widened:
        ctx.append("widened")
    return f"pc{node.bpc} {ins.text(an.p.label_at)}" + (f" [{', '.join(ctx)}]" if ctx else "")


def assembler_timing_check(an: Analysis) -> dict[str, Any]:
    """Compare every instruction's cost/blocking class with the image ``timing`` table."""
    table = an.p.timing
    if table is None:
        return {"status": "N/A", "detail": "image has no timing table"}
    problems = []
    if len(table) != len(an.p.ins):
        problems.append(f"timing table has {len(table)} entries for {len(an.p.ins)} words")
    for entry in table:
        pc = entry["pc"]
        if not 0 <= pc < len(an.p.ins):
            problems.append(f"pc{pc} outside image")
            continue
        ins = an.p.ins[pc]
        want = (minimum_cycles(ins), blocking_class(ins))
        got = (entry["minimum_cycles"], entry["blocking"])
        if want != got:
            problems.append(f"pc{pc} {ins.text()}: image says {got}, ISA gives {want}")
    return {"status": "PASS" if not problems else "FAIL", "entries": len(table), "problems": problems}


def pin_spacing(an: Analysis) -> dict[int, dict[str, Any]]:
    """Per owned pin: spacing between consecutive possible pad changes."""
    result: dict[int, dict[str, Any]] = {}
    for pin in range(8):
        if not (an.own >> pin) & 1:
            continue
        pred = (lambda e, pin=pin: e[EK] == "pad" and e[EPIN] == pin)
        q = an.query(pred)
        within: dict[int, int] = defaultdict(int)
        across: list[tuple] = []
        changes = 0
        for node, vi, ei, e in occurrences(an, pred):
            changes += 1
            mn, mx, miss = q.after(node, vi, ei)
            if mn == mx and mn != INF:
                v = node.variants[vi]
                # exact: either inside the variant or crossing zero-width stalls only
                within[int(mn)] += 1
            elif mn != INF:
                across.append((mn, mx, node.key, vi, e[EPC]))
        mins = [k for k in within] + [a[0] for a in across]
        maxs = [k for k in within] + [a[1] for a in across]
        result[pin] = {
            "events": changes,
            "exact_spacings": dict(sorted(within.items())),
            "variable_spacings": sorted({(fmt_cycles(a[0]), fmt_cycles(a[1])) for a in across}),
            "min": min(mins) if mins else None,
            "max": max(maxs) if maxs else None,
        }
    return result


def loop_periods(an: Analysis) -> list[dict[str, Any]]:
    """Per backward-branch header: time between consecutive issues of the header."""
    headers: dict[int, list[str]] = defaultdict(list)
    for ins in an.p.ins:
        if ins.op in (JMP, LOOP) and ins.imm24 <= ins.pc:
            headers[ins.imm24].append(f"{ins.mnemonic}@pc{ins.pc}")
        elif ins.op == JZ and ins.imm16 <= ins.pc:
            headers[ins.imm16].append(f"JZ@pc{ins.pc}")
    rows = []
    for h, via in sorted(headers.items()):
        if not 0 <= h < len(an.p.ins):
            continue
        hi = an.p.ins[h]
        boundary = blocking_class(hi) != BLOCK_NONE
        pred = ((lambda e, h=h: e[EK] == "arrive" and e[EPC] == h) if boundary
                else (lambda e, h=h: e[EK] == "issue" and e[EPC] == h))
        q = an.query(pred)
        values: dict[tuple, int] = defaultdict(int)
        for node, vi, ei, e in occurrences(an, pred):
            mn, mx, miss = q.after(node, vi, ei)
            if mn == INF:
                continue
            values[(mn, mx)] += 1
        rows.append({"header_pc": h, "header": hi.text(an.p.label_at), "via": via,
                     "periods": sorted(({"min": fmt_cycles(k[0]), "max": fmt_cycles(k[1]),
                                         "count": c} for k, c in values.items()),
                                       key=lambda r: (float(r["min"]) if r["min"] != "inf" else INF))})
    return rows


def segment_rows(an: Analysis) -> list[dict[str, Any]]:
    rows = []
    for node in an.iter_nodes():
        for vi, v in enumerate(node.variants):
            end = v.end
            if end[0] == "boundary":
                ends = f"arrive pc{end[2]} {an.p.ins[end[2]].text(an.p.label_at)} at +{end[1]}"
            elif end[0] == "periodic":
                ends = f"boundary-free periodic loop, period {end[2]} from +{end[1]}"
            elif end[0] == "fault":
                ends = f"fault{end[2]} at +{end[1]}"
            else:
                ends = f"{end[0]} at +{end[1]}"
            rows.append({
                "node": node_label(an, node), "variant": vi, "end": ends,
                "wcet": end[1],
                "pads": [(e[ET], e[EPIN], PAD_TEXT[e[EBEFORE]], PAD_TEXT[e[EAFTER]], e[EPC], e[ECAUSE])
                         for e in v.events if e[EK] == "pad"],
                "samples": [(e[ET], e[EPIN], e[EPC], e[ECAUSE]) for e in v.events if e[EK] == "sample"],
                "interactions": [(e[ET], e[EPC], e[ECAUSE]) for e in v.events if e[EK] == "inter"],
            })
    return rows


def boundary_rows(an: Analysis) -> list[dict[str, Any]]:
    rows = []
    for node in an.iter_nodes():
        pads = an.pads(node.state) if node.kind != "start" else tuple(PZ if (an.own >> p) & 1 else 0
                                                                     for p in range(8))
        wcets = [v.t_end for v in node.variants if v.kind in ("boundary", "halt", "fault")]
        rows.append({
            "node": node_label(an, node), "class": node.kind,
            "limit": node.state.limit if node.kind in (BLOCK_PIN, BLOCK_EVENT) else None,
            "min_stall": node.min_stall, "max_stall": fmt_cycles(node.max_stall),
            "deadlock": node.deadlock,
            "holding_pads": {f"pin{p}": PAD_TEXT[m] for p, m in enumerate(pads) if m},
            "variants": len(node.variants),
            "segment_wcet": max(wcets) if wcets else None,
            "segment_bcet": min(wcets) if wcets else None,
            "successors": sorted({node_label(an, an.nodes[v.succ]) for v in node.variants
                                  if v.kind == "boundary"}),
        })
    return rows


def summarize(an: Analysis) -> dict[str, Any]:
    p = an.p
    ident = p.identity()
    segs = segment_rows(an)
    bnds = boundary_rows(an)
    wcet = max((r["wcet"] for r in segs if r["wcet"] is not None), default=None)
    return {
        "tool": f"pe_timing {TOOL_VERSION}",
        "name": p.name, "path": p.path, "engine": p.engine,
        "architecture": p.arch.to_json(), "clock_hz": p.clock_hz,
        "owned_pins": p.ownership, "open_drain": p.open_drain, "notes": p.notes,
        "identity": ident,
        "exact": an.is_exact(), "warnings": an.warnings,
        "listing": [{"pc": i.pc, "word": f"0x{i.word:08x}", "text": i.text(p.label_at),
                     "label": p.label_at.get(i.pc), "cycles": minimum_cycles(i),
                     "blocking": blocking_class(i), "static_fault": p.faults[i.pc]} for i in p.ins],
        "assembler_timing": assembler_timing_check(an),
        "graph": {"nodes": len(an.order),
                  "boundary_pcs": sorted({n.bpc for n in an.iter_nodes() if n.kind != "start"}),
                  "variants": sum(len(n.variants) for n in an.iter_nodes())},
        "wcet_between_boundaries": wcet,
        "boundaries": bnds,
        "segments": segs,
        "pin_spacing": {f"pin{k}": v for k, v in pin_spacing(an).items()},
        "loops": loop_periods(an),
    }


# ----------------------------------------------------------------------------
# Text rendering
# ----------------------------------------------------------------------------

def render_schedule(an: Analysis, *, max_rows: int = 40) -> str:
    """Human-readable per-segment edge schedules (deduplicated across contexts)."""
    lines: list[str] = []
    groups: dict[tuple, list[str]] = defaultdict(list)
    for node in an.iter_nodes():
        for vi, v in enumerate(node.variants):
            sig = tuple((e[ET], e[EK], e[EPIN], e[EPC], e[EBEFORE], e[EAFTER]) for e in v.events
                        if e[EK] in ("pad", "sample", "arrive", "inter")) + (v.end[:3],)
            groups[sig].append(f"{node_label(an, node)} v{vi}")
    for sig, where in groups.items():
        head = where[0] if len(where) == 1 else f"{where[0]} (+{len(where) - 1} identical contexts)"
        lines.append(f"  from {head}:")
        count = 0
        for item in sig[:-1]:
            t, kind, pin, pc, before, after = item
            if kind == "pad":
                text = f"pin{pin} {PAD_TEXT[before]}->{PAD_TEXT[after]}"
            elif kind == "sample":
                text = f"sample pin{pin}"
            elif kind == "inter":
                text = "interaction"
            else:
                text = f"arrive {an.p.ins[pc].text(an.p.label_at)}"
            if count < max_rows:
                lines.append(f"    +{t:<6} pc{pc:<3} {text}")
            count += 1
        if count > max_rows:
            lines.append(f"    ... {count - max_rows} more events")
        end = sig[-1]
        if end[0] != "boundary":
            lines.append(f"    end: {end[0]}{end[2] if end[0] == 'fault' else ''} at +{end[1]}")
    return "\n".join(lines)


def render_text(an: Analysis, summary: dict[str, Any], checks: list[dict[str, Any]] | None = None) -> str:
    p = an.p
    out = [f"== {p.name}  (engine {p.engine}, owned 0x{p.ownership:02x}, open-drain 0x{p.open_drain:02x}, "
           f"{'exact' if summary['exact'] else 'OVER-APPROXIMATE'})"]
    out.append(f"assembler timing table: {summary['assembler_timing']['status']}")
    out.append(f"boundary graph: {summary['graph']['nodes']} nodes, {summary['graph']['variants']} path variants; "
               f"WCET between boundaries {summary['wcet_between_boundaries']} cycles")
    out.append("boundaries (holding pads, stall range, segment WCET):")
    for row in summary["boundaries"]:
        pads = " ".join(f"{k}={v}" for k, v in row["holding_pads"].items())
        out.append(f"  {row['node']:<44} stall[{row['min_stall']},{row['max_stall']}] "
                   f"seg {row['segment_bcet']}..{row['segment_wcet']}  {pads}"
                   + (f"  DEADLOCK: {row['deadlock']}" if row["deadlock"] else ""))
    out.append("pin spacing (cycles between possible pad changes):")
    for pin, row in summary["pin_spacing"].items():
        out.append(f"  {pin}: exact {row['exact_spacings']} variable {row['variable_spacings']} "
                   f"min {fmt_cycles(row['min']) if row['min'] is not None else '-'}")
    out.append("loop periods:")
    for row in summary["loops"]:
        periods = ", ".join(f"{r['min']}" + (f"..{r['max']}" if r['max'] != r['min'] else "") + f" x{r['count']}"
                            for r in row["periods"])
        out.append(f"  pc{row['header_pc']} {row['header']} via {','.join(row['via'])}: {periods}")
    if checks:
        out.append("protocol checks:")
        for c in checks:
            out.append(f"  [{c['status']}] {c['id']}: {c['detail']}")
    out.append("schedules:")
    out.append(render_schedule(an))
    return "\n".join(out)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _load_contracts():
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import pe_contracts  # noqa: PLC0415
    return pe_contracts


def _clocks(values: list[str] | None) -> list[float]:
    if not values:
        return []
    out = []
    for v in values:
        for part in v.split(","):
            part = part.strip().lower()
            scale = 1.0
            if part.endswith("mhz"):
                part, scale = part[:-3], 1e6
            elif part.endswith("khz"):
                part, scale = part[:-3], 1e3
            elif part.endswith("hz"):
                part = part[:-2]
            out.append(float(part) * scale)
    return out


def cmd_analyze(args: argparse.Namespace) -> int:
    contracts = _load_contracts()
    status = 0
    for image in args.images:
        prog = Program.from_image(image, arch_override=_arch_override(args, image))
        an = Analysis(prog)
        summary = summarize(an)
        checks = contracts.check_program(an, clocks=_clocks(args.clock) or None)
        summary["checks"] = checks
        if any(c["status"] == "FAIL" for c in checks) or summary["assembler_timing"]["status"] == "FAIL":
            status = 1
        if args.json:
            Path(args.json).write_text(json.dumps(summary, indent=1, default=_json_default))
        if not args.quiet:
            print(render_text(an, summary, checks))
            print()
    return status


def _arch_override(args: argparse.Namespace, image: str) -> Arch | None:
    if not getattr(args, "issue", None):
        return None
    data = json.loads(Path(image).read_text())
    arch = Arch.from_json(data["architecture"])
    return Arch(arch.engines, arch.width, arch.program_words, arch.fifo_words,
                args.issue == "fused", arch.prefetch)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, float) and math.isinf(obj):
        return "inf" if obj > 0 else "-inf"
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, tuple):
        return list(obj)
    return str(obj)


def cmd_report(args: argparse.Namespace) -> int:
    contracts = _load_contracts()
    return contracts.full_report(Path(args.firmware), Path(args.out), clocks=_clocks(args.clock) or None,
                                 validation=Path(args.validation) if args.validation else None)


def cmd_validate(args: argparse.Namespace) -> int:
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import pe_validate  # noqa: PLC0415
    return pe_validate.main_validate(args)


def cmd_merge(args: argparse.Namespace) -> int:
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import pe_validate  # noqa: PLC0415
    merged = pe_validate.merge_results([Path(p) for p in args.inputs])
    Path(args.out).write_text(json.dumps(merged, indent=1, default=_json_default))
    s = merged["stats"]
    print(f"{merged['result']}: {merged['files']} files, {s.get('runs_ok', 0)}/{s.get('runs', 0)} engine runs, "
          f"{s.get('ticks_checked', 0)} edges, {s.get('pad_changes_matched', 0)}/{s.get('pad_changes_observed', 0)} "
          f"pad changes")
    return 0 if merged["result"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pe_timing", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="analyze image(s): schedules, spacing, WCET, protocol checks")
    a.add_argument("images", nargs="+")
    a.add_argument("--json", help="write the (last) image summary as JSON")
    a.add_argument("--clock", action="append", help="extra clock(s) for checks, e.g. 25MHz,66.67MHz")
    a.add_argument("--issue", choices=("fused", "scalar"), help="override the image's issue mode")
    a.add_argument("--quiet", action="store_true")
    a.set_defaults(func=cmd_analyze)
    r = sub.add_parser("report", help="analyze every image in a firmware directory plus the flagship scenario")
    r.add_argument("--firmware", required=True, help="firmware directory (images + flagship-scenario.json)")
    r.add_argument("--out", required=True, help="output directory (report.json, report.md)")
    r.add_argument("--clock", action="append")
    r.add_argument("--validation", help="validation summary JSON to fold into the report")
    r.set_defaults(func=cmd_report)
    v = sub.add_parser("validate", help="check static schedules against the Python reference model")
    v.add_argument("--repo", required=True, help="repository root providing test/ and firmware/")
    v.add_argument("--out", required=True, help="output JSON")
    v.add_argument("--suite", action="append",
                   help="scenarios|legacy|event|stress|random (default: all but random)")
    v.add_argument("--seed", type=lambda s: int(s, 0), default=0x7131)
    v.add_argument("--count", type=int, default=4, help="stress runs per image / random cases")
    v.add_argument("--first", type=int, default=0, help="first stress/random index")
    v.add_argument("--cycles", type=int, default=30000, help="cycles per stress run")
    v.add_argument("--extra-images", action="append", help="directory of additional *.image.json (stress suite)")
    v.set_defaults(func=cmd_validate)
    mg = sub.add_parser("merge", help="merge validation JSON files (Slurm array tasks) into one summary")
    mg.add_argument("inputs", nargs="+")
    mg.add_argument("--out", required=True)
    mg.set_defaults(func=cmd_merge)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
