#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""gen_cert: SymbiYosys timing certificates from pe_timing analyses.

For one firmware image, ``pe_timing.Analysis`` builds a boundary graph: one
node per (synchronization boundary, context), each with the exact schedule of
every path variant from the node's anchor to the next boundary. This tool
turns every node into a certificate harness around the design of record
(``protocol_processor_fv``, see ``formal/README.md``):

* **Precondition** (assumed at step 0, the anchor): engine K's execution
  registers equal the node's abstract start state (unknown data free), the
  image is committed and owned as declared, and the rest of the chip is the
  quiet run B of ``cert_env.vh``.
* **Assertions**, for every cycle up to the end of the segment:
  ``flow``   the issue slots and PCs follow one of the node's variants;
  ``pad``    every owned pad is in the variant's predicted state set;
  ``edge``   a pad changes only on an edge where the variant has a pad event;
  ``sample`` at every predicted input sample, ``rx`` shifts in the
             synchronized pin (the sampling instant and pin);
  ``post``   on the arrival cycle, engine K's state equals the successor
             node's abstract state (the next certificate's precondition), or,
             for a HALT/fault end, the engine has stopped with that code;
  ``env_push`` a strict PUSH succeeds in B (the composition needs it).
* **Covers**: the end of every variant is reachable.

The per-node BMC depth is the segment length plus two: yosys samples clocked
checks at the edge, so sby step k checks the values of step k-1, and depth D
covers steps 0..D-2. Each certificate is therefore complete for its segment. ``boundary_lemmas.sv`` (image independent) closes
the gaps between segments; ``docs/timing-certificates.md`` gives the
composition with the timing-isolation proof.

Negative controls: ``--mutant`` applies one of pe_validate's deliberately
wrong analyzers, ``--schedule-mutant`` perturbs one predicted item. Both
emit certificates that must fail.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TIMING = HERE.parent
if str(TIMING) not in sys.path:
    sys.path.insert(0, str(TIMING))

import pe_timing as T  # noqa: E402

GEN_VERSION = "1.0"
SCHEDULE_MUTANTS = ("pad-late", "pad-early", "pad-level", "issue-late", "post-limit")
P0, P1, PZ = T.P0, T.P1, T.PZ


def analyzer(mutant: str | None = None) -> types.ModuleType:
    """pe_timing, or a copy with one of pe_validate's MUTANTS applied."""
    if not mutant:
        return T
    pe_validate = importlib.import_module("pe_validate")
    old, new = pe_validate.MUTANTS[mutant]
    source = Path(T.__file__).read_text()
    if old not in source:
        raise SystemExit(f"gen_cert: mutation site of {mutant} not found in pe_timing.py")
    module = types.ModuleType(f"pe_timing_cert_{mutant.replace('-', '_')}")
    module.__file__ = T.__file__
    sys.modules[module.__name__] = module
    exec(compile(source.replace(old, new, 1), T.__file__, "exec"), module.__dict__)  # noqa: S102
    return module


# ----------------------------------------------------------------------------
# Certificate model
# ----------------------------------------------------------------------------

@dataclass
class VariantCert:
    index: int
    kind: str                    # boundary | halt | fault
    t_end: int                   # pe_timing end offset
    horizon: int                 # last checked cycle (step)
    attempts: dict[int, int]     # cycle -> pc of the issue attempt on the next edge
    pads: dict[int, list]        # pin -> [(from_step, state set)], piecewise constant
    changes: dict[int, set]      # pin -> steps on which the pad may change
    samples: list                # (step, pin, msb)
    strict_push: list            # cycles of strict PUSH attempts
    post: dict[str, Any]         # end state description
    succ: int | None = None      # successor node index
    pad_events: int = 0
    known_edges: int = 0

    def pad_at(self, pin: int, step: int) -> int:
        cur = 0
        for s, v in self.pads[pin]:
            if s <= step:
                cur = v
        return cur


@dataclass
class NodeCert:
    index: int
    label: str
    kind: str
    bpc: int
    pre: dict[str, Any]
    variants: list[VariantCert] = field(default_factory=list)
    mutation: str | None = None
    start: Any = None             # pe_timing AState at the anchor (for rtl_trace)
    chunk: int | None = None      # chunk index when a long segment is split
    chunk_start: int = 0          # segment step of the chunk's step 0
    group: int = 0                # state group at the chunk start

    @property
    def depth(self) -> int:
        return max(v.horizon for v in self.variants) + 2


def state_desc(st: Any, pc: int, tx_unknown: bool = False) -> dict[str, Any]:
    regs = list(st.regs)
    if tx_unknown:
        regs[0] = None
    return {"pc": pc, "regs": regs, "repeat": st.repeat, "limit": st.limit,
            "xpins": st.xpins[0] | st.xpins[1] << 3 | st.xpins[2] << 6,
            "vk": st.vk, "vb": st.vb & st.vk, "dk": st.dk, "db": st.db & st.dk}


def build(an: Any, TT: types.ModuleType) -> list[NodeCert]:
    """One NodeCert per boundary-graph node of an exact analysis."""
    if not an.is_exact():
        raise SystemExit(f"gen_cert: {an.p.name}: analysis is not exact (widened or budget-cut)")
    index = {key: i for i, key in enumerate(an.order)}
    own = an.p.ownership
    certs = []
    for key in an.order:
        node = an.nodes[key]
        if node.kind == TT.BLOCK_BRANCH:
            raise SystemExit(f"gen_cert: {an.p.name}: soft branch boundaries are not supported")
        if node.kind == "start":
            start = TT.START_STATE
            pre = state_desc(start, 0)
        else:
            ins = an.p.ins[node.bpc]
            start = node.state._replace(pc=node.bpc + 1)
            pre = state_desc(node.state, node.bpc + 1, tx_unknown=ins.op == TT.PULL)
            if ins.op == TT.PULL:
                start = start._replace(regs=(None,) + tuple(start.regs[1:]))
        init_pads = an.pads(start)
        nc = NodeCert(index[key], TT.node_label(an, node), node.kind, node.bpc, pre, start=start)
        for vi, v in enumerate(node.variants):
            kind = v.kind
            if kind not in ("boundary", "halt", "fault"):
                raise SystemExit(f"gen_cert: {an.p.name}: variant end {v.end} not supported")
            t_end = v.t_end
            horizon = t_end - 1 if kind == "boundary" else t_end
            attempts: dict[int, int] = {}
            pads = {p: [(0, init_pads[p])] for p in range(8) if (own >> p) & 1}
            changes: dict[int, set] = {p: set() for p in pads}
            samples, strict = [], []
            n_pad = n_known = 0
            for e in v.events:
                t, k = e[TT.ET], e[TT.EK]
                if k in ("issue", "arrive"):
                    if t - 1 in attempts:
                        raise SystemExit(f"gen_cert: two issue attempts at +{t}")
                    attempts[t - 1] = e[TT.EPC]
                elif k == "pad":
                    p = e[TT.EPIN]
                    before, after = e[TT.EBEFORE], e[TT.EAFTER]
                    cur = pads[p][-1][1]
                    if cur != before:
                        raise SystemExit(f"gen_cert: pad timeline mismatch pin{p} +{t}: {cur} vs {before}")
                    if pads[p][-1][0] == t:
                        pads[p][-1] = (t, after)
                    else:
                        pads[p].append((t, after))
                    changes[p].add(t)
                    n_pad += 1
                    if before & after == 0:
                        n_known += 1
                elif k == "sample":
                    ins = an.p.ins[e[TT.EPC]]
                    msb = (ins.c & 1) if ins.op == TT.IN else (ins.c >> 2) & 1
                    samples.append((t, e[TT.EPIN], msb))
                elif k == "inter" and e[TT.ECAUSE] == "push_strict":
                    strict.append(t - 1)
            if len(strict) > an.p.arch.fifo_words:
                raise SystemExit("gen_cert: more strict PUSHes in one segment than RX FIFO words")
            if kind == "boundary":
                post = {"kind": "boundary", **state_desc(v.exit_state, v.end[2])}
            elif kind == "halt":
                post = {"kind": "halt"}
            else:
                post = {"kind": "fault", "code": v.end[2]}
            vc = VariantCert(vi, kind, t_end, horizon, attempts, pads, changes, samples, strict,
                             post, index.get(v.succ) if v.succ is not None else None, n_pad, n_known)
            nc.variants.append(vc)
        certs.append(nc)
    check_chain(certs)
    return certs


def check_chain(certs: list[NodeCert]) -> None:
    """Every boundary post must hand over exactly the successor's precondition,
    given the boundary lemmas: the completion advances the PC by one, clears
    the blocked count and leaves every other register alone, except that a
    PULL loads tx (free in the successor)."""
    for nc in certs:
        for v in nc.variants:
            if v.post["kind"] != "boundary":
                continue
            succ = certs[v.succ].pre
            post = {k: val for k, val in v.post.items() if k != "kind"}
            want = dict(post, pc=post["pc"] + 1)
            if succ["regs"][0] is None:
                want["regs"] = [None] + list(post["regs"][1:])
            if want != succ:
                raise SystemExit(f"gen_cert: chain broken n{nc.index} v{v.index} -> n{v.succ}: {want} vs {succ}")


# ----------------------------------------------------------------------------
# Cycle-level abstract simulation (for splitting long segments)
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class RState:
    """Engine K's RTL registers at one step: the ISA-level AState plus the
    WAIT timer and the XFER registers (None: unknown)."""
    a: Any
    timer: int = 0
    xrem: int = 0
    xtick: int | None = None
    xperiod: int | None = None
    xmode: int | None = None

    def desc(self) -> dict[str, Any]:
        return {**state_desc(self.a, self.a.pc), "timer": self.timer, "xrem": self.xrem,
                "xtick": self.xtick, "xperiod": self.xperiod, "xmode": self.xmode}


def rtl_trace(an: Any, TT: types.ModuleType, nc: NodeCert, vc: VariantCert) -> list[RState]:
    """Engine K's abstract RTL state on every step of one variant, following
    hardcaml/lib/engine.ml cycle by cycle (a second derivation of the schedule).

    Data-dependent JZ/LOOP outcomes are taken from the variant's next issue.
    The derived issue slots and pad sets must equal pe_timing's; any
    difference aborts. Returns the states of the steps on which the engine is
    still running the segment (up to the arrival step, or up to the HALT/FAULT
    issue step)."""
    p, width = an.p, an.p.arch.width
    mask = (1 << width) - 1
    last = vc.horizon if vc.kind == "boundary" else vc.horizon - 1
    rs = RState(nc.start)
    states = [rs]

    def shift(v, msb):
        return None if v is None else (((v << 1) if msb else (v >> 1)) & mask)

    def bit(v, msb):
        return None if v is None else ((v >> (width - 1)) & 1 if msb else v & 1)

    for step in range(last):
        a = rs.a
        slot = rs.timer == 0 and rs.xrem == 0
        if slot != (step in vc.attempts) or (slot and vc.attempts[step] != a.pc):
            raise SystemExit(f"gen_cert: rtl_trace n{nc.index} v{vc.index}: issue mismatch at step {step}")
        if rs.timer:
            rs = RState(a, rs.timer - 1, rs.xrem, rs.xtick, rs.xperiod, rs.xmode)
        elif rs.xrem:
            if rs.xtick is None or rs.xmode is None or rs.xperiod is None:
                raise SystemExit("gen_cert: rtl_trace: XFER registers unknown")
            if rs.xtick > 1:
                rs = RState(a, 0, rs.xrem, rs.xtick - 1, rs.xperiod, rs.xmode)
            else:
                old, m = rs.xrem, rs.xmode
                cpol, cpha, msb, drive, sampling = (bool(m >> i & 1) for i in range(5))
                active = not (old & 1)
                ck, out = a.xpins[0], a.xpins[1]
                a = TT._drive(a, ck, int(cpol != active))
                shifting = (active if cpha else not active) and drive
                tx, rx = a.regs[0], a.regs[1]
                data = tx if cpha else shift(tx, msb)
                if shifting and (cpha or old != 1):
                    a = TT._drive(a, out, bit(data, msb))
                if shifting:
                    tx = shift(tx, msb)
                if sampling and (active != cpha):
                    rx = None
                a = a._replace(regs=(tx, rx) + tuple(a.regs[2:]))
                if old == 1:
                    a = a._replace(pc=a.pc + 1)
                rs = RState(a, 0, old - 1, rs.xperiod, rs.xperiod, rs.xmode)
        else:
            ins = p.ins[a.pc]
            op, nxt = ins.op, a.pc + 1
            regs = a.regs
            timer, xrem, xtick, xperiod, xmode = 0, 0, rs.xtick, rs.xperiod, rs.xmode
            follow = vc.attempts.get(step + 1)
            if op in (TT.HALT, TT.FAULT, TT.PULL, TT.WAITPIN, TT.WAITEVENT) or (op == TT.PUSH and ins.a == 0) \
                    or p.faults[a.pc]:
                raise SystemExit(f"gen_cert: rtl_trace: unexpected end instruction at step {step}")
            if op == TT.SET:
                a = a._replace(vk=0xFF, vb=ins.imm24 & 0xFF)
            elif op == TT.DIR:
                a = a._replace(dk=0xFF, db=ins.imm24 & 0xFF)
            elif op == TT.WAIT:
                timer = ins.imm24
            elif op == TT.JMP:
                nxt = ins.imm24
            elif op == TT.OUT:
                b, tx = TT._shift_out(regs[0], bool(ins.c & 1), width, mask)
                a = TT._drive(a, ins.a, b)._replace(regs=(tx,) + tuple(regs[1:]))
            elif op == TT.IN:
                a = a._replace(regs=(regs[0], None) + tuple(regs[2:]))
            elif op == TT.COUNT:
                a = a._replace(repeat=ins.imm16)
            elif op == TT.LOOP:
                if a.repeat is None:
                    if follow == ins.imm24:
                        nxt = ins.imm24
                elif a.repeat:
                    a = a._replace(repeat=a.repeat - 1)
                    nxt = ins.imm24
            elif op == TT.LIMIT:
                a = a._replace(limit=ins.imm24)
            elif op == TT.PINS:
                a = a._replace(xpins=(ins.imm24 & 7, ins.imm24 >> 3 & 7, ins.imm24 >> 6 & 7))
            elif op == TT.XFER:
                c = ins.c
                cpol, cpha, msb, drive = (bool(c >> i & 1) for i in range(4))
                a = TT._drive(a, a.xpins[0], int(cpol))
                if drive and not cpha:
                    a = TT._drive(a, a.xpins[1], bit(regs[0], msb))
                xrem, xtick, xperiod, xmode = 2 * ins.a, ins.b, ins.b, c & 31
                nxt = a.pc
            elif op in (TT.MOV, TT.LOAD, TT.ADD, TT.XOR, TT.AND, TT.OR, TT.SHL, TT.SHR, TT.NOT, TT.TIME):
                a = a._replace(regs=TT._alu(ins, regs, mask))
            elif op == TT.JZ:
                value = regs[ins.a]
                if value is None:
                    if follow == ins.imm16 and ins.imm16 != a.pc + 1:
                        r = list(regs)
                        r[ins.a] = 0
                        a = a._replace(regs=tuple(r))
                        nxt = ins.imm16
                elif value == 0:
                    nxt = ins.imm16
            # NOP, SIGNAL, strict PUSH (succeeds in B): nothing else
            rs = RState(a._replace(pc=nxt), timer, xrem, xtick, xperiod, xmode)
        states.append(rs)
        pads = an.pads(rs.a)
        for pin in vc.pads:
            if pads[pin] != vc.pad_at(pin, step + 1):
                raise SystemExit(f"gen_cert: rtl_trace n{nc.index} v{vc.index}: pad {pin} differs at "
                                 f"step {step + 1}: {pads[pin]} vs {vc.pad_at(pin, step + 1)}")
    return states


def split(an: Any, TT: types.ModuleType, nc: NodeCert, size: int) -> list[NodeCert]:
    """Split a long segment into chunks of at most ``size`` steps.

    Chunk j covers segment steps [c_j, c_{j+1}]. Its precondition is the
    abstract RTL state at c_j (the node's own precondition for j = 0); a
    variant that runs past c_{j+1} gets the abstract state at c_{j+1} as its
    postcondition, which is the next chunk's precondition. Variants are
    grouped by their state at c_j; each group is one chunk certificate."""
    if nc.depth <= size + 2:
        return [nc]
    traces = {v.index: rtl_trace(an, TT, nc, v) for v in nc.variants}
    top = max(v.horizon for v in nc.variants)
    cuts = list(range(0, top, size)) + [top]
    out = []
    for j, (c0, c1) in enumerate(zip(cuts, cuts[1:])):
        running = [v for v in nc.variants if v.horizon > c0]
        groups: dict[Any, list] = {}
        for v in running:
            key = None if c0 == 0 else traces[v.index][c0]
            groups.setdefault(key, []).append(v)
        for g, (key, vs) in enumerate(groups.items()):
            pre = nc.pre if c0 == 0 else key.desc()
            ch = NodeCert(nc.index, f"{nc.label} [steps {c0}..{c1}]", nc.kind, nc.bpc, pre,
                          start=nc.start, chunk=j, chunk_start=c0, group=g)
            for n, v in enumerate(vs):
                end = min(v.horizon, c1)
                pads = {}
                for pin, tl in v.pads.items():
                    pads[pin] = [(0, v.pad_at(pin, c0))] + [(s - c0, val) for s, val in tl if c0 < s <= end]
                cv = VariantCert(
                    n, v.kind, v.t_end, end - c0,
                    {c - c0: pc for c, pc in v.attempts.items() if c0 <= c <= end},
                    pads, {pin: {s - c0 for s in ch_ if c0 < s <= end} for pin, ch_ in v.changes.items()},
                    [(s - c0, pin, msb) for s, pin, msb in v.samples if c0 < s <= end],
                    [c - c0 for c in v.strict_push if c0 <= c <= end],
                    v.post if v.horizon <= c1 else {"kind": "cut", **traces[v.index][c1].desc()},
                    v.succ,
                    sum(1 for tl in v.changes.values() for s in tl if c0 < s <= end),
                    0)
                cv.known_edges = sum(1 for pin, tl in v.pads.items() for i, (s, val) in enumerate(tl)
                                     if i and c0 < s <= end and not (tl[i - 1][1] & val))
                ch.variants.append(cv)
            out.append(ch)
    return out


def mutate_schedule(nc: NodeCert, kind: str) -> bool:
    """Perturb one predicted item of the node's first applicable variant.

    ``issue-late`` changes every variant: ``flow`` only needs some variant to
    match, so a single mutated variant would leave the others to match."""
    if kind == "issue-late":
        changed = False
        for v in nc.variants:
            cycles = sorted(v.attempts)
            for a, b in zip(cycles, cycles[1:]):
                if b - a > 1:
                    v.attempts = {(c + 1 if c >= b else c): pc for c, pc in v.attempts.items()}
                    v.horizon += 1
                    changed = True
                    break
        return changed
    for v in nc.variants:
        if kind in ("pad-late", "pad-early", "pad-level"):
            for p, tl in sorted(v.pads.items()):
                for i, (s, val) in enumerate(tl):
                    if i == 0 or s <= 0:
                        continue
                    prev = tl[i - 1][1]
                    if prev & val:          # only a certain (known-level) change
                        continue
                    nxt = tl[i + 1][0] if i + 1 < len(tl) else v.horizon + 1
                    if kind == "pad-late" and s + 1 < nxt:
                        tl[i] = (s + 1, val)
                        v.changes[p].discard(s)
                        v.changes[p].add(s + 1)
                        return True
                    if kind == "pad-early" and s - 1 > tl[i - 1][0]:
                        tl[i] = (s - 1, val)
                        v.changes[p].discard(s)
                        v.changes[p].add(s - 1)
                        return True
                    if kind == "pad-level" and val in (P0, P1):
                        tl[i] = (s, P1 if val == P0 else P0)
                        return True
        elif kind == "post-limit" and v.post["kind"] == "boundary":
            v.post["limit"] = (v.post["limit"] + 1) & 0xFFFFFF
            return True
    return False


# ----------------------------------------------------------------------------
# SystemVerilog emission
# ----------------------------------------------------------------------------

def sv_state(d: dict[str, Any]) -> str:
    terms = [f"k_pc == 24'd{d['pc']}", "k_running", "k_fault == 8'd0",
             f"k_timer == 24'd{d.get('timer', 0)}", f"k_xrem == 7'd{d.get('xrem', 0)}", "k_blocked == 24'd0"]
    for name, width in (("xtick", 8), ("xperiod", 8), ("xmode", 5)):
        if d.get(name) is not None:
            terms.append(f"k_{name} == {width}'d{d[name]}")
    for name, val in zip(("tx", "rx", "x", "y"), d["regs"]):
        if val is not None:
            terms.append(f"k_{name} == 32'd{val}")
    if d["repeat"] is not None:
        terms.append(f"k_repeat == 16'd{d['repeat']}")
    terms.append(f"k_limit == 24'd{d['limit']}")
    terms.append(f"k_xpins == 9'd{d['xpins']}")
    terms.append(f"(k_values & 8'h{d['vk']:02x}) == 8'h{d['vb']:02x}")
    terms.append(f"(k_enables & 8'h{d['dk']:02x}) == 8'h{d['db']:02x}")
    return "\n        && ".join(terms)


def sv_node(image: dict[str, Any], nc: NodeCert, module: str) -> str:
    K = image["engine"]
    words = image["words"]
    L = []
    w = L.append
    w(f"// {nc.label}: {len(nc.variants)} variant(s), BMC depth {nc.depth}"
      + (f"; NEGATIVE CONTROL ({nc.mutation})" if nc.mutation else ""))
    w(f"module {module} (input wire clk);")
    w(f"    localparam K = {K};")
    w(f"    localparam [15:0] IMG_N = 16'd{len(words)};")
    w(f"    localparam [7:0] IMG_OWN = 8'h{image['owned_pins']:02x}, IMG_OD = 8'h{image['open_drain']:02x};")
    w("    wire rst_n = 1'b1, ena = 1'b1;")
    w("    wire [7:0] ui_in = 8'h00;")
    w("    (* anyseq *) reg [7:0] uio_in;")
    w("    function automatic [31:0] img(input [5:0] a);")
    w("        case (a)")
    for a, word in enumerate(words):
        w(f"            6'd{a}: img = 32'h{word:08x};")
    w("            default: img = 32'h0;")
    w("        endcase")
    w("    endfunction")
    w('`include "cert_dut.vh"')
    w(f"    wire k_pre = {sv_state(nc.pre)};")
    w('`include "cert_env.vh"')
    nv = len(nc.variants)
    w("    reg [15:0] off = 0;")
    w("    always @(posedge clk) if (off != 16'hffff) off <= off + 16'd1;")
    w("    reg [23:0] prev_pads = 0;")
    w("    reg [31:0] prev_rx = 0;")
    w("    reg [7:0] prev_synced = 0;")
    w("    reg [7:0] uio_d1 = 0, uio_d2 = 0, uio_d3 = 0;")
    w("    always @(posedge clk) begin prev_pads <= k_pads; prev_rx <= k_rx; prev_synced <= synced;")
    w("        uio_d1 <= uio_in; uio_d2 <= uio_d1; uio_d3 <= uio_d2; end")
    w("    wire [24:0] obs = {k_slot, k_slot ? k_pc : 24'd0};")
    for v in nc.variants:
        i = v.index
        w(f"    function automatic [24:0] att{i}(input [15:0] c);")
        w("        case (c)")
        for c in sorted(v.attempts):
            w(f"            16'd{c}: att{i} = {{1'b1, 24'd{v.attempts[c]}}};")
        w(f"            default: att{i} = 25'd0;")
        w("        endcase")
        w("    endfunction")
        for p, tl in sorted(v.pads.items()):
            w(f"    function automatic [2:0] pad{i}_{p}(input [15:0] c);")
            w(f"        pad{i}_{p} = 3'd{tl[0][1]};")
            for s, val in tl[1:]:
                w(f"        if (c >= 16'd{s}) pad{i}_{p} = 3'd{val};")
            w("    endfunction")
            w(f"    function automatic chg{i}_{p}(input [15:0] c);")
            w("        case (c)")
            for s in sorted(v.changes[p]):
                w(f"            16'd{s}: chg{i}_{p} = 1'b1;")
            w(f"            default: chg{i}_{p} = 1'b0;")
            w("        endcase")
            w("    endfunction")
    w(f"    reg [{nv - 1}:0] alive = {nv}'b{'1' * nv};")
    w("    reg done = 0;")
    w(f"    wire [{nv - 1}:0] m, at_end;")
    for v in nc.variants:
        i = v.index
        w(f"    assign m[{i}] = alive[{i}] && off <= 16'd{v.horizon} && obs == att{i}(off);")
        w(f"    assign at_end[{i}] = off == 16'd{v.horizon};")
    w("    always @(posedge clk) begin")
    w("        alive <= m;")
    w("        if ((m & at_end) != 0) done <= 1;")
    w("    end")
    w("    always @(posedge clk) if (!done) begin")
    w("        flow: assert(m != 0);")
    for v in nc.variants:
        i = v.index
        w(f"        if (alive[{i}] && off <= 16'd{v.horizon}) begin")
        for p in sorted(v.pads):
            w(f"            pad_v{i}_p{p}: assert((k_pads[{3 * p}+:3] & pad{i}_{p}(off)) != 3'd0);")
            w(f"            if (past_valid && k_pads[{3 * p}+:3] != prev_pads[{3 * p}+:3])")
            w(f"                edge_v{i}_p{p}: assert(chg{i}_{p}(off));")
        for n, (s, pin, msb) in enumerate(v.samples):
            # From step 3 on, the sampled bit is the pad input three cycles
            # earlier (two synchronizer flops, then the engine's edge), which
            # certifies the synchronizer latency too; before that the harness
            # has no pad history and the synchronized value is used.
            bit = f"uio_d3[{pin}]" if s >= 3 else f"prev_synced[{pin}]"
            want = f"{{prev_rx[30:0], {bit}}}" if msb else f"{{{bit}, prev_rx[31:1]}}"
            w(f"            if (past_valid && off == 16'd{s}) sample_v{i}_{n}: assert(k_rx == {want});")
        w("        end")
        for n, c in enumerate(v.strict_push):
            w(f"        if (m[{i}] && off == 16'd{c}) env_push_v{i}_{n}: assert(k_rx_push);")
        post = v.post
        if post["kind"] in ("boundary", "cut"):
            cond = sv_state(post)
        elif post["kind"] == "halt":
            cond = "!k_running && k_fault == 8'd0 && k_enables == 8'd0"
        else:
            cond = f"!k_running && k_fault == 8'd{post['code']}"
        w(f"        if (m[{i}] && off == 16'd{v.horizon}) post_v{i}: assert({cond});")
    w("    end")
    w("`ifdef CERT_COVER")
    w("    always @(posedge clk) if (!done) begin")
    for v in nc.variants:
        w(f"        cover_v{v.index}: cover(m[{v.index}] && off == 16'd{v.horizon});")
    w("    end")
    w("`endif")
    w("endmodule")
    return "\n".join(L) + "\n"


ENGINES = {
    "bitwuzla": "smtbmc bitwuzla",
    "boolector": "smtbmc boolector",
    "yices": "smtbmc yices",
    "bmc3": "abc bmc3",
    "ric3": "aiger rIC3",
}


def sby_text(sv_name: str, module: str, depth: int, engines: list[str], rtl: Path, models: Path,
             certdir: Path, expect_fail: bool = False) -> str:
    L = ["[tasks]", "bmc", "cover", "", "[options]", "bmc: mode bmc", f"bmc: depth {depth}",
         "cover: mode cover", f"cover: depth {depth}"]
    if expect_fail:
        L.append("bmc: expect fail")
    L += ["", "[engines]"]
    L += [f"bmc: {ENGINES[e]}" for e in engines]
    L += ["cover: smtbmc yices", "", "[script]",
          "read_verilog -DFUNCTIONAL -DSYNTHESIS RM_IHPSG13_1P_core_behavioral.v RM_IHPSG13_1P_64x16_c2.v",
          "read_verilog processor_fv.v",
          f"bmc: read -formal {sv_name}",
          f"cover: read -formal -DCERT_COVER {sv_name}",
          f"prep -top {module}", "", "[files]",
          str(models / "RM_IHPSG13_1P_core_behavioral.v"),
          str(models / "RM_IHPSG13_1P_64x16_c2.v"),
          str(rtl / "processor_fv.v"),
          str(certdir / "cert_dut.vh"),
          str(certdir / "cert_env.vh"),
          sv_name]
    return "\n".join(L) + "\n"


def image_tag(path: Path) -> str:
    return path.name.replace(".image.json", "")


def emit(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rtl = Path(args.rtl).resolve()
    models = Path(args.models).resolve()
    engines = args.engines.split(",")
    for e in engines:
        if e not in ENGINES:
            raise SystemExit(f"gen_cert: unknown engine {e}")
    TT = analyzer(args.mutant)
    manifest = []
    for img_path in [Path(p) for p in args.images]:
        image = json.loads(img_path.read_text())
        program = TT.Program.from_image(img_path)
        an = TT.Analysis(program)
        certs = build(an, TT)
        tag = image_tag(img_path)
        suffix = ""
        if args.mutant:
            suffix = "_neg_" + args.mutant.replace("-", "_")
        if args.schedule_mutant:
            suffix = "_neg_" + args.schedule_mutant.replace("-", "_")
        if args.rtl_mutant:
            suffix = "_" + args.rtl_mutant
        selected = certs
        if args.nodes:
            wanted = {int(n) for n in args.nodes.split(",")}
            selected = [c for c in certs if c.index in wanted]
        emitted = []
        for nc in selected:
            if args.schedule_mutant:
                if not mutate_schedule(nc, args.schedule_mutant):
                    continue
                nc.mutation = args.schedule_mutant
            elif args.mutant:
                nc.mutation = args.mutant
            elif args.rtl_mutant:
                nc.mutation = args.rtl_mutant
            emitted.append(nc)
        if args.mutant:
            # A pe_validate mutant changes the analysis, not every segment:
            # keep only nodes whose certificate differs from the real one.
            # Keep nodes with the same precondition as the real certificate but a
            # different segment claim: those must fail on their own. (A node whose
            # precondition also differs can be self-consistent; the mutant is then
            # caught by its predecessor's postcondition.)
            real = {c.index: c for c in build(T.Analysis(T.Program.from_image(img_path)), T)}
            emitted = [c for c in emitted if c.index in real and c.pre == real[c.index].pre
                       and _differs(c, real[c.index])]
        if args.chunk and not (args.mutant or args.schedule_mutant):
            emitted = [ch for nc in emitted for ch in split(an, TT, nc, args.chunk)]
            for ch in emitted:
                ch.mutation = args.rtl_mutant
        if args.pick_one and emitted:
            # One negative control per image: the cheapest node the mutation reaches.
            emitted = [min(emitted, key=lambda c: (c.depth, -sum(v.pad_events for v in c.variants)))]
        for nc in emitted:
            module = f"cert_{tag.replace('-', '_')}_n{nc.index}"
            if nc.chunk is not None:
                module += f"_c{nc.chunk}" + (f"g{nc.group}" if nc.group else "")
            module += suffix
            sv_name = f"{module}.sv"
            (out / sv_name).write_text(sv_node(image, nc, module))
            (out / f"{module}.sby").write_text(
                sby_text(sv_name, module, nc.depth, engines, rtl, models, HERE,
                         expect_fail=bool(nc.mutation)))
            manifest.append({
                "certificate": module, "image": tag, "engine_index": image["engine"],
                "node": nc.index, "chunk": nc.chunk, "chunk_start": nc.chunk_start,
                "label": nc.label, "kind": nc.kind, "depth": nc.depth,
                "variants": len(nc.variants),
                "pad_events": sum(v.pad_events for v in nc.variants),
                "known_level_edges": sum(v.known_edges for v in nc.variants),
                "issue_attempts": sum(len(v.attempts) for v in nc.variants),
                "samples": sum(len(v.samples) for v in nc.variants),
                "ends": [f"{v.kind}@+{v.t_end}" + (f"->n{v.succ}" if v.succ is not None else "")
                         for v in nc.variants],
                "expect": "FAIL" if nc.mutation else "PASS", "mutation": nc.mutation,
                "image_sha256": hashlib.sha256(img_path.read_bytes()).hexdigest(),
            })
    mpath = out / (args.manifest or "manifest.json")
    old = json.loads(mpath.read_text()) if mpath.exists() and args.append else []
    mpath.write_text(json.dumps({"generator": GEN_VERSION, "pe_timing": T.TOOL_VERSION,
                                 "certificates": (old["certificates"] if old else []) + manifest},
                                indent=1) + "\n")
    print(f"gen_cert: {len(manifest)} certificate(s) in {out}")
    return 0


def crosscheck(args: argparse.Namespace) -> int:
    """Re-derive every variant's issue slots and pad sets cycle by cycle
    (rtl_trace, following engine.ml) and compare them with pe_timing's."""
    TT = analyzer(args.mutant)
    total = {"images": 0, "nodes": 0, "variants": 0, "steps": 0}
    for img_path in [Path(p) for p in args.images]:
        an = TT.Analysis(TT.Program.from_image(img_path))
        certs = build(an, TT)
        total["images"] += 1
        for nc in certs:
            total["nodes"] += 1
            for v in nc.variants:
                total["variants"] += 1
                total["steps"] += len(rtl_trace(an, TT, nc, v))
    print(f"gen_cert crosscheck: {total['images']} images, {total['nodes']} nodes, {total['variants']} variants, "
          f"{total['steps']} steps: issue slots and pad sets agree with pe_timing")
    return 0


def emit_lemmas(args: argparse.Namespace) -> int:
    """boundary_lemmas.sby: one BMC-2 task per engine, covers and a negative control."""
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rtl, models = Path(args.rtl).resolve(), Path(args.models).resolve()
    tasks = [f"k{k}" for k in range(4)] + [f"cover_k{k}" for k in range(4)] + ["neg"]
    # Task groups (sby: "name group..."): bmcg = proofs and the control,
    # covg = covers, plain = the proofs, eK = engine index K.
    L = ["[tasks]"] + [f"k{k} bmcg plain e{k}" for k in range(4)] + \
        [f"cover_k{k} covg e{k}" for k in range(4)] + ["neg bmcg e0", "", "[options]",
        "bmcg: mode bmc", "bmcg: depth 4", "covg: mode cover", "covg: depth 3", "neg: expect fail",
        "", "[engines]", "bmcg: smtbmc yices", "bmcg: smtbmc bitwuzla", "covg: smtbmc yices", "",
        "[script]",
        "read_verilog -DFUNCTIONAL -DSYNTHESIS RM_IHPSG13_1P_core_behavioral.v RM_IHPSG13_1P_64x16_c2.v",
        "read_verilog processor_fv.v",
        "plain: read -formal boundary_lemmas.sv",
        "covg: read -formal -DLEMMA_COVER boundary_lemmas.sv",
        "neg: read -formal -DLEMMA_NEG boundary_lemmas.sv"]
    for k in range(1, 4):
        L.append(f"e{k}: chparam -set K {k} boundary_lemmas")
    L += ["prep -top boundary_lemmas", "", "[files]",
          str(models / "RM_IHPSG13_1P_core_behavioral.v"), str(models / "RM_IHPSG13_1P_64x16_c2.v"),
          str(rtl / "processor_fv.v"), str(HERE / "cert_dut.vh"), str(HERE / "boundary_lemmas.sv")]
    (out / "boundary_lemmas.sby").write_text("\n".join(L) + "\n")
    S = ["[tasks]", "prove", "bmc", "cover", "neg", "", "[options]", "prove: mode prove",
         "bmc: mode bmc", "bmc: depth 24", "cover: mode cover", "cover: depth 8",
         "neg: mode bmc", "neg: depth 8", "neg: expect fail", "", "[engines]",
         "prove: abc pdr", "bmc: smtbmc yices", "cover: smtbmc yices", "neg: smtbmc yices", "",
         "[script]",
         "read_verilog -DFUNCTIONAL -DSYNTHESIS RM_IHPSG13_1P_core_behavioral.v RM_IHPSG13_1P_64x16_c2.v",
         "prove: read -formal sram_lemma.sv", "bmc: read -formal sram_lemma.sv",
         "cover: read -formal -DSRAM_COVER sram_lemma.sv", "neg: read -formal -DSRAM_NEG sram_lemma.sv",
         "prep -top sram_lemma", "", "[files]",
         str(models / "RM_IHPSG13_1P_core_behavioral.v"), str(models / "RM_IHPSG13_1P_64x16_c2.v"),
         str(HERE / "sram_lemma.sv")]
    (out / "sram_lemma.sby").write_text("\n".join(S) + "\n")
    print(f"gen_cert: {out / 'boundary_lemmas.sby'} ({len(tasks)} tasks), {out / 'sram_lemma.sby'} (4 tasks)")
    return 0


def _differs(a: NodeCert, b: NodeCert | None) -> bool:
    if b is None or len(a.variants) != len(b.variants):
        return True
    for x, y in zip(a.variants, b.variants):
        if (x.attempts, x.pads, x.samples, x.post, x.horizon) != (y.attempts, y.pads, y.samples, y.post, y.horizon):
            return True
    return a.pre != b.pre


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit", help="emit certificate harnesses and sby files")
    e.add_argument("images", nargs="+", help="firmware/*.image.json")
    e.add_argument("--out", required=True)
    e.add_argument("--rtl", required=True, help="directory with processor_fv.v (formal/run.sh --generate-only)")
    e.add_argument("--models", required=True, help="directory with the IHP SRAM models (models/)")
    e.add_argument("--engines", default="bitwuzla,boolector,yices", help=f"BMC portfolio from {sorted(ENGINES)}")
    e.add_argument("--nodes", help="comma-separated node indices (default: all)")
    e.add_argument("--chunk", type=int, default=0,
                   help="split segments longer than this many steps into chunks (0: never)")
    e.add_argument("--mutant", help="pe_validate MUTANTS name: certificates from a wrong analyzer")
    e.add_argument("--schedule-mutant", choices=SCHEDULE_MUTANTS)
    e.add_argument("--rtl-mutant", help="name of the RTL mutant --rtl points at (rtl_mutants.py): the "
                   "certificates are real, the netlist is wrong, so each run is a negative control")
    e.add_argument("--pick-one", action="store_true",
                   help="with a mutant: emit only the cheapest affected node per image")
    e.add_argument("--manifest", help="manifest file name (default manifest.json)")
    e.add_argument("--append", action="store_true", help="append to an existing manifest")
    le = sub.add_parser("emit-lemmas", help="emit boundary_lemmas.sby")
    le.add_argument("--out", required=True)
    le.add_argument("--rtl", required=True)
    le.add_argument("--models", required=True)
    cc = sub.add_parser("crosscheck", help="cycle-level re-derivation of every schedule")
    cc.add_argument("images", nargs="+")
    cc.add_argument("--mutant", help="check a pe_validate mutant instead (must report a difference)")
    args = ap.parse_args(argv)
    return {"emit": emit, "emit-lemmas": emit_lemmas, "crosscheck": crosscheck}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
