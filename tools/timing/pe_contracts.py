# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Declared protocol timing, checked against pe_timing's static schedules.

Every firmware image carries timing declarations in its ``notes`` (emitted by
the OCaml assembler, see ``docs/firmware.md``), e.g. "exact bit period 64
clocks", "Fused transfers have 32-cycle half periods", "each external half
period at least 8 clocks". This module parses them, derives the protocol
quantities from the boundary graph (bit periods, SCK half periods, I2C
tLOW/tHIGH/tSU;STA/..., sample points, reaction latencies, holding-point pad
states) and reports PASS/FAIL with margins, in cycles and at several clocks.

Latency conventions (isa.md Machine, ``pe_timing`` docstring):

* an input transition sampled on edge ``e`` is seen by the engine on ``e+2``;
  a ``WAITPIN`` that was already waiting therefore completes on ``e+2``;
* ``IN``/``XFER`` sampling on edge ``s`` reads the pad as sampled on ``s-2``;
* an asynchronous transition can occur anywhere in the cycle before the edge
  that samples it, so an extra cycle is added where the phase is unknown.

Bus-level quantities exclude pad, board and rise/fall delays; they are the
digital schedule at the chip's pins, i.e. the budget those delays must fit in.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import pe_timing as T
from pe_timing import EAFTER, EBEFORE, ECAUSE, EK, EPC, EPIN, ET, INF, P0, P1, PZ

DEFAULT_CLOCKS = (10e6, 25e6, 50e6, 66e6, 100e6)
SYNC = T.SYNC_LATENCY

# NXP UM10204 "I2C-bus specification and user manual", Rev. 7.0 (1 October 2021),
# Table 11: characteristics of the SDA and SCL bus lines (seconds; None = no limit).
I2C_MODES: dict[str, dict[str, tuple[float | None, float | None]]] = {
    "Sm": {"fSCL_period": (1 / 100e3, None), "tHD;STA": (4.0e-6, None), "tLOW": (4.7e-6, None),
           "tHIGH": (4.0e-6, None), "tSU;STA": (4.7e-6, None), "tHD;DAT": (0.0, None),
           "tSU;DAT": (250e-9, None), "tSU;STO": (4.0e-6, None), "tBUF": (4.7e-6, None),
           "tVD;DAT": (None, 3.45e-6)},
    "Fm": {"fSCL_period": (1 / 400e3, None), "tHD;STA": (0.6e-6, None), "tLOW": (1.3e-6, None),
           "tHIGH": (0.6e-6, None), "tSU;STA": (0.6e-6, None), "tHD;DAT": (0.0, None),
           "tSU;DAT": (100e-9, None), "tSU;STO": (0.6e-6, None), "tBUF": (1.3e-6, None),
           "tVD;DAT": (None, 0.9e-6)},
    "Fm+": {"fSCL_period": (1 / 1000e3, None), "tHD;STA": (0.26e-6, None), "tLOW": (0.5e-6, None),
            "tHIGH": (0.26e-6, None), "tSU;STA": (0.26e-6, None), "tHD;DAT": (0.0, None),
            "tSU;DAT": (50e-9, None), "tSU;STO": (0.26e-6, None), "tBUF": (0.5e-6, None),
            "tVD;DAT": (None, 0.45e-6)},
}

# Pin roles from docs/firmware.md "Executable example contracts".
ROLES = {
    "uart-tx": {"tx": 0}, "uart-rx": {"rx": 1},
    "spi-controller": {"sck": 2, "mosi": 3, "miso": 4, "cs": 5},
    "spi-target": {"sck": 2, "mosi": 3, "miso": 4, "cs": 5},
    "i2c": {"scl": 6, "sda": 7},
    "jtag": {"tck": 0, "tdi": 1, "tdo": 2, "tms": 3},
    "waveform": {"out": 0}, "event-transmitter": {"out": 0},
}


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def family(name: str) -> str:
    if name.startswith("spi-controller"):
        return "spi-controller"
    if name.startswith("spi-target"):
        return "spi-target"
    if name.startswith("i2c-target"):
        return "i2c-target"
    if name.startswith("i2c-"):
        return "i2c-controller"
    return name


def fmt_ns(seconds: float) -> str:
    return f"{seconds * 1e9:.1f} ns" if seconds < 1e-6 else f"{seconds * 1e6:.3f} us"


def fc(x: float) -> str:
    return T.fmt_cycles(x)


def check(cid: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    out = {"id": cid, "status": status, "detail": detail}
    out.update(extra)
    return out


def clock_rows(cycles: float, clocks: list[float], t_min: float | None = None,
               t_max: float | None = None) -> dict[str, Any]:
    """Absolute-time view of a cycle count; limits on the clock it implies."""
    rows = {}
    for f in clocks:
        t = cycles / f
        ok = (t_min is None or t >= t_min - 1e-15) and (t_max is None or t <= t_max + 1e-15)
        rows[f"{f / 1e6:g}MHz"] = {"time": fmt_ns(t), "ok": ok}
    limits = {}
    if t_min:
        limits["max_clock_hz"] = cycles / t_min
    if t_max:
        limits["min_clock_hz"] = cycles / t_max
    return {"cycles": cycles, "at": rows, **limits}


class Ctx:
    """An analysis plus per-event own-pad snapshots and cached queries."""

    def __init__(self, an: T.Analysis) -> None:
        self.an = an
        self.p = an.p
        self.snap: dict[int, tuple] = {}
        for node in an.iter_nodes():
            base = (list(an.pads(node.state)) if node.kind != "start"
                    else [PZ if (an.own >> q) & 1 else 0 for q in range(8)])
            for v in node.variants:
                cur = list(base)
                for e in v.events:
                    if e[EK] == "pad":
                        cur[e[EPIN]] = e[EAFTER]
                    self.snap[id(e)] = tuple(cur)
        self._queries: dict[str, T.Query] = {}

    def pads_at(self, e: tuple) -> tuple:
        return self.snap[id(e)]

    def query(self, name: str, pred: Callable[[tuple], bool]) -> T.Query:
        q = self._queries.get(name)
        if q is None:
            q = self._queries[name] = self.an.query(pred)
        return q

    def distances(self, name_from: str, pred_from: Callable, name_to: str, pred_to: Callable) -> list[dict]:
        q = self.query(name_to, pred_to)
        rows = []
        for node, vi, ei, e in T.occurrences(self.an, pred_from):
            mn, mx, miss = q.after(node, vi, ei)
            rows.append({"min": mn, "max": mx, "miss": miss, "pc": e[EPC], "t": e[ET],
                         "node": T.node_label(self.an, node), "event": e})
        return rows

    def waitpin_pcs(self, pin: int, level: int) -> set[int]:
        return {i.pc for i in self.p.ins if i.op == T.WAITPIN and i.a == pin and i.b == level}

    def anchor_after(self, pcs: set[int]) -> Callable[[tuple], bool]:
        """Anchor (completion) events of boundaries at ``pcs``."""
        return lambda e: e[EK] == "anchor" and e[EPIN] in pcs

    def arrive_at(self, pcs: set[int]) -> Callable[[tuple], bool]:
        return lambda e: e[EK] == "arrive" and e[EPC] in pcs

    def holding_nodes(self) -> list[T.Node]:
        return [n for n in self.an.iter_nodes() if n.kind in (T.BLOCK_TX, T.BLOCK_RX)]

    def first_of(self, node: T.Node, vi: int, ei: int, preds: dict[str, Callable]) -> set[str]:
        """Which predicate matches first along every continuation of an event."""
        an = self.an

        def scan(events: list, start: int) -> str | None:
            for e in events[start:]:
                for name, pred in preds.items():
                    if pred(e):
                        return name
            return None

        found: set[str] = set()
        frontier: list = []

        def cont(v: T.Variant) -> None:
            if v.kind == "boundary":
                s = an.nodes[v.succ]
                if s.deadlock:
                    found.add("deadlock")
                else:
                    frontier.append(s.key)
            elif v.kind == "periodic":
                found.add(scan(v.events[v.loop_from:], 0) or "never")
            else:
                found.add(v.kind)

        v = node.variants[vi]
        r = scan(v.events, ei + 1)
        if r:
            return {r}
        cont(v)
        seen: set = set()
        while frontier:
            k = frontier.pop()
            if k in seen:
                continue
            seen.add(k)
            for v2 in an.nodes[k].variants:
                r = scan(v2.events, 0)
                if r:
                    found.add(r)
                else:
                    cont(v2)
        return found


def pad_is(pin: int, after: int | None = None, before: int | None = None, cause: str | None = None):
    def pred(e: tuple) -> bool:
        return (e[EK] == "pad" and e[EPIN] == pin and (after is None or e[EAFTER] == after)
                and (before is None or e[EBEFORE] == before) and (cause is None or e[ECAUSE] == cause))
    return pred


def summarize_rows(rows: list[dict]) -> tuple[float, float, dict]:
    mins = [r["min"] for r in rows if r["min"] != INF]
    maxs = [r["max"] for r in rows if r["min"] != INF]
    hist: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["min"] == INF:
            continue
        key = fc(r["min"]) if r["min"] == r["max"] else f"{fc(r['min'])}..{fc(r['max'])}"
        hist[key] += 1
    return (min(mins) if mins else INF), (max(maxs) if maxs else -INF), dict(hist)


def parse_int(pattern: str, notes: list[str], default: int | None = None) -> tuple[int | None, str | None]:
    for note in notes:
        m = re.search(pattern, note)
        if m:
            return int(m.group(1)), note
    return default, None


# ----------------------------------------------------------------------------
# generic checks (every image)
# ----------------------------------------------------------------------------

def generic_checks(ctx: Ctx) -> list[dict]:
    an, p = ctx.an, ctx.p
    out = []
    ident = p.identity()
    ok = ident.get("bytecode_ok", True) and ident.get("source_ok", True)
    out.append(check("identity", "PASS" if ok else "FAIL",
                     f"bytecode sha256 {'matches' if ident.get('bytecode_ok') else 'MISMATCH'}"
                     + ("" if "source_ok" not in ident else
                        f", source sha256 {'matches' if ident['source_ok'] else 'MISMATCH'}")))
    at = T.assembler_timing_check(an)
    out.append(check("assembler-timing-table", at["status"],
                     f"{at.get('entries', 0)} per-instruction entries (minimum_cycles, blocking) agree with the "
                     "ISA cost model" if at["status"] == "PASS" else "; ".join(at.get("problems", [])[:5])))
    out.append(check("exact-analysis", "PASS" if an.is_exact() else "FAIL",
                     f"{len(an.order)} boundary contexts, {sum(len(n.variants) for n in an.iter_nodes())} path "
                     "variants, no widening or budget cut" if an.is_exact() else "; ".join(an.warnings[:3])))
    dead = [T.node_label(an, n) + ": " + n.deadlock for n in an.iter_nodes() if n.deadlock]
    out.append(check("no-self-deadlock", "PASS" if not dead else "FAIL",
                     "no WAITPIN waits for a level the engine itself forces" if not dead else "; ".join(dead)))
    faults = sorted({(v.end[2], v.end[1]) for n in an.iter_nodes() for v in n.variants if v.kind == "fault"})
    explicit = sorted({i.imm24 for i in p.ins if i.op == T.FAULT})
    static = [f"pc{pc}" for pc, f in enumerate(p.faults) if f]
    reach_static = sorted({code for code, _ in faults if code in (1, 2)})
    out.append(check("reachable-faults", "PASS" if not reach_static else "FAIL",
                     f"only explicit FAULT codes {explicit or '[]'} reachable"
                     + (f"; static operand faults at {static} are unreachable" if static else "")
                     if not reach_static else f"reachable ISA fault codes {reach_static}"))
    # bounded waits
    waits = sorted({(n.bpc, n.state.limit) for n in an.iter_nodes() if n.kind in (T.BLOCK_PIN, T.BLOCK_EVENT)})
    if waits:
        worst = max(w[1] for w in waits)
        out.append(check("bounded-waits", "INFO",
                         f"{len(waits)} WAITPIN/WAITEVENT contexts; LIMIT values "
                         f"{sorted({w[1] for w in waits})}; longest bounded stall {worst - 1} cycles "
                         f"({fmt_ns((worst - 1) / p.clock_hz)} at {p.clock_hz / 1e6:g} MHz) before fault 3"))
    hold = [n for n in ctx.holding_nodes()]
    if hold:
        states = sorted({(T.node_label(an, n).split(" [")[0],
                          " ".join(f"pin{q}={T.PAD_TEXT[m]}" for q, m in enumerate(an.pads(n.state)) if m))
                         for n in hold})
        out.append(check("holding-points", "INFO",
                         "unbounded FIFO waits hold: " + "; ".join(f"{a} [{b or 'no owned pins'}]" for a, b in states)))
    return out


# ----------------------------------------------------------------------------
# UART
# ----------------------------------------------------------------------------

def uart_tx_checks(ctx: Ctx, clocks: list[float]) -> list[dict]:
    an, p = ctx.an, ctx.p
    pin = ROLES["uart-tx"]["tx"]
    n_bit, note = parse_int(r"exact bit period (\d+) clocks", p.notes)
    out = []
    if n_bit is None:
        return [check("uart-tx-declaration", "FAIL", "no 'exact bit period N clocks' declaration in notes")]
    rows = ctx.distances("tx", pad_is(pin), "tx", pad_is(pin))
    start = [r for r in rows if r["event"][EAFTER] == P0 and r["event"][EBEFORE] == P1]
    data = [r for r in rows if r["event"][ECAUSE] == "OUT"]
    stop = [r for r in rows if r["event"][ECAUSE] == "SET" and r["event"][EAFTER] == P1
            and r["event"][EBEFORE] != PZ]
    exact = start + data
    bad = [r for r in exact if not (r["min"] == r["max"] == n_bit)]
    out.append(check("uart-tx-bit-period", "PASS" if exact and not bad else "FAIL",
                     f"start bit and all 8 data bits last exactly {n_bit} cycles in every context "
                     f"({len(exact)} bit boundaries); declared: \"{note}\"" if not bad else
                     f"{len(bad)} bit boundaries differ from {n_bit}: "
                     + ", ".join(f"pc{r['pc']} {fc(r['min'])}..{fc(r['max'])}" for r in bad[:4]),
                     cycles=n_bit, clocks=clock_rows(n_bit, clocks)))
    per_frame = defaultdict(int)
    for r in data:
        per_frame[r["node"]] += 1
    frames_ok = all(v % 8 == 0 for v in per_frame.values()) and per_frame
    out.append(check("uart-tx-frame-8N1", "PASS" if frames_ok and start and stop else "FAIL",
                     f"each frame: start (1->0), 8 OUT data bits, stop (->1); contexts {dict(per_frame)}"))
    smin = min((r["min"] for r in stop), default=INF)
    out.append(check("uart-tx-stop-bit", "PASS" if stop and smin >= n_bit else "FAIL",
                     f"stop bit (idle high before the next start bit) >= {fc(smin)} cycles, required >= {n_bit}; "
                     f"margin {fc(smin - n_bit)} cycles (loop/PULL overhead), unbounded while the TX FIFO is empty"))
    idle = [n for n in ctx.holding_nodes() if an.pads(n.state)[pin] != P1]
    out.append(check("uart-tx-idle-high", "PASS" if not idle else "FAIL",
                     "TX pin driven high at every FIFO holding point (PULL)" if not idle else
                     f"holding points not idle-high: {[T.node_label(an, n) for n in idle]}"))
    frame = min((r["min"] for r in start), default=INF)
    q = ctx.query("start-start", lambda e: e[EK] == "pad" and e[EPIN] == pin and e[EAFTER] == P0
                  and e[EBEFORE] == P1)
    per = [q.after(node, vi, ei)[0] for node, vi, ei, e in T.occurrences(an, lambda e: e[EK] == "pad" and e[EPIN] == pin and e[EAFTER] == P0 and e[EBEFORE] == P1)]
    period = min(per) if per else INF
    bauds = {f"{f / 1e6:g}MHz": f / n_bit for f in clocks}
    out.append(check("uart-tx-rate", "INFO",
                     f"baud = clock/{n_bit}: " + ", ".join(f"{k} {v:.0f} Bd" for k, v in bauds.items())
                     + f"; back-to-back frame period >= {fc(period)} cycles (10 bits = {10 * n_bit}); "
                     f"standard 115200 Bd would need a {n_bit * 115200 / 1e6:g} MHz clock",
                     frame_period_min=period))
    return out


def uart_rx_checks(ctx: Ctx, clocks: list[float]) -> list[dict]:
    an, p = ctx.an, ctx.p
    pin = ROLES["uart-rx"]["rx"]
    n_bit, note = parse_int(r"(\d+) clocks per bit", p.notes)
    periods, note2 = parse_int(r"bounded by (\d+) bit periods", p.notes)
    out = []
    if n_bit is None:
        return [check("uart-rx-declaration", "FAIL", "no 'N clocks per bit' declaration")]
    start_pcs = ctx.waitpin_pcs(pin, 0)
    idle_pcs = ctx.waitpin_pcs(pin, 1)
    nodes = [n for n in an.iter_nodes() if n.bpc in start_pcs]
    samples_sets = set()
    for node in nodes:
        for v in node.variants:
            samples_sets.add(tuple(e[ET] for e in v.events if e[EK] == "sample" and e[EPIN] == pin))
    ok_center = True
    eps_hi, eps_lo = INF, -INF
    details = []
    for samples in samples_sets:
        if len(samples) != 9:
            ok_center = False
            details.append(f"{len(samples)} samples per frame")
            continue
        for j, d in enumerate(samples):
            m = j + 1                              # bit index in the frame (start bit = 0)
            pos = d - m * n_bit                    # sampled pad instant within the bit, true edge at 0..1 cycle earlier
            early, late = pos, n_bit - (pos + 1)
            if j < 8 and abs(pos + 0.5 - n_bit / 2) > 1:
                ok_center = False
            if early < 0 or late < 0:
                ok_center = False
            eps_hi = min(eps_hi, d / (m * n_bit) - 1)
            eps_lo = max(eps_lo, (d + 1) / ((m + 1) * n_bit) - 1)
            details.append(f"bit{m}: +{pos}..{pos + 1} of {n_bit}")
    out.append(check("uart-rx-center-sampling", "PASS" if ok_center and samples_sets else "FAIL",
                     f"data bits sampled {n_bit // 2}..{n_bit // 2 + 1} cycles into the bit (centre {n_bit / 2:g}), "
                     f"stop bit {details[-1].split(': ')[1] if details else '?'}; offsets from start-edge "
                     f"sample: {sorted(samples_sets)[0] if samples_sets else []}; declared: \"{note}\"",
                     sample_positions=details))
    out.append(check("uart-rx-baud-tolerance", "INFO",
                     f"all 9 samples stay inside their bit for a transmitter bit period within "
                     f"{eps_lo * 100:+.2f}% .. {eps_hi * 100:+.2f}% of {n_bit} cycles (includes the 1-cycle "
                     "asynchronous start-edge uncertainty)", eps_min=eps_lo, eps_max=eps_hi))
    # re-arm before the next start bit (back-to-back frames, one stop bit)
    q = ctx.query("rx-arm", ctx.arrive_at(start_pcs))
    arm = min((q.from_anchor(n)[0] for n in nodes), default=INF)
    frame = 10 * n_bit
    margin = frame - (arm + 1)
    out.append(check("uart-rx-rearm", "PASS" if margin >= 0 else "FAIL",
                     f"re-armed at the start-bit WAITPIN {fc(arm)} cycles after the start edge is seen; the "
                     f"next start edge (1 stop bit) is {frame} cycles after the previous one: margin "
                     f"{fc(margin)} cycles; continuous traffic tolerates a transmitter up to "
                     f"{((arm + 1) / frame - 1) * 100:+.2f}% fast without late start detection",
                     rearm_cycles=arm))
    limits = sorted({n.state.limit for n in an.iter_nodes() if n.bpc in idle_pcs | start_pcs})
    want = (periods or 0) * n_bit
    out.append(check("uart-rx-idle-bound", "PASS" if limits == [want] else "FAIL",
                     f"idle/start WAITPIN LIMIT {limits} = {periods} x {n_bit}-cycle bit periods; "
                     f"declared: \"{note2}\""))
    out.append(check("uart-rx-rate", "INFO", "baud = clock/" + str(n_bit) + ": "
                     + ", ".join(f"{f / 1e6:g}MHz {f / n_bit:.0f} Bd" for f in clocks)))
    return out


# ----------------------------------------------------------------------------
# SPI
# ----------------------------------------------------------------------------

def _mode(name: str, notes: list[str], word: str) -> int | None:
    m, _ = parse_int(word + r" mode(\d)", notes)
    if m is None:
        mm = re.search(r"mode(\d)", name)
        m = int(mm.group(1)) if mm else None
    return m


def spi_controller_checks(ctx: Ctx, clocks: list[float]) -> list[dict]:
    an, p = ctx.an, ctx.p
    r = ROLES["spi-controller"]
    sck, mosi, miso, cs = r["sck"], r["mosi"], r["miso"], r["cs"]
    half, note = parse_int(r"Fused transfers have (\d+)-cycle half periods", p.notes)
    mode = _mode(p.name, p.notes, "SPI controller")
    out = []
    cpol, cpha = mode >> 1, mode & 1
    xfers = [i for i in p.ins if i.op == T.XFER]
    ok = bool(xfers) and all(i.b == half and (i.c & 3) == (cpol | cpha << 1) and i.a == 8 and i.c & 4
                             and i.c & 24 == 24 for i in xfers)
    out.append(check("spi-xfer-encoding", "PASS" if ok else "FAIL",
                     f"{len(xfers)} XFER: 8 bits, half-period {half}, CPOL{cpol}/CPHA{cpha}, MSB first, drive+sample; "
                     f"declared: \"{note}\" and mode{mode}"))
    frames = []
    for node in an.iter_nodes():
        for v in node.variants:
            ev = v.events
            sck_t = [e[ET] for e in ev if e[EK] == "pad" and e[EPIN] == sck and e[ECAUSE] == "XFER-clk"
                     and e[EBEFORE] != e[EAFTER]]
            if not sck_t:
                continue
            cs_fall = [e[ET] for e in ev if e[EK] == "pad" and e[EPIN] == cs and e[EAFTER] == P0]
            cs_rise = [e[ET] for e in ev if e[EK] == "pad" and e[EPIN] == cs and e[EAFTER] == P1 and e[EBEFORE] == P0]
            mosi_t = [e[ET] for e in ev if e[EK] == "pad" and e[EPIN] == mosi]
            samples = [e[ET] for e in ev if e[EK] == "sample" and e[EPIN] == miso]
            frames.append((node, sck_t, cs_fall, cs_rise, mosi_t, samples, ev))
    bad = []
    setups, holds, cs_setup, cs_hold, sample_ok = [], [], [], [], True
    for node, sck_t, cs_fall, cs_rise, mosi_t, samples, ev in frames:
        diffs = {b - a for a, b in zip(sck_t, sck_t[1:])}
        if len(sck_t) != 16 or diffs != {half}:
            bad.append(f"{T.node_label(an, node)}: {len(sck_t)} SCK edges, spacings {sorted(diffs)}")
        if cs_fall and cs_rise:
            cs_setup.append(sck_t[0] - cs_fall[0])
            cs_hold.append(cs_rise[-1] - sck_t[-1])
        # target samples MOSI on the leading edge for CPHA0, trailing for CPHA1
        target_edges = sck_t[cpha::2]
        for t_edge in target_edges:
            before = [t for t in mosi_t if t <= t_edge]
            after = [t for t in mosi_t if t > t_edge]
            if before:
                setups.append(t_edge - before[-1])
            holds.append((after[0] if after else (cs_rise[-1] if cs_rise else t_edge)) - t_edge)
        # both sides sample on the same edge set: leading (CPHA0) or trailing (CPHA1)
        if sorted(samples) != sorted(target_edges):
            sample_ok = False
    out.append(check("spi-sck-half-period", "PASS" if frames and not bad else "FAIL",
                     f"every frame has 16 SCK edges spaced exactly {half} cycles "
                     f"({len(frames)} frame contexts)" if not bad else "; ".join(bad[:3]),
                     clocks=clock_rows(half, clocks)))
    idle_bad = []
    for n in ctx.holding_nodes():
        pads = an.pads(n.state)
        if pads[sck] != (P1 if cpol else P0) or pads[cs] != P1:
            idle_bad.append(f"{T.node_label(an, n)} SCK={T.PAD_TEXT[pads[sck]]} CS={T.PAD_TEXT[pads[cs]]}")
    out.append(check("spi-idle-at-holding-points", "PASS" if not idle_bad else "FAIL",
                     f"SCK at CPOL level {cpol} and CS deasserted at every PULL/PUSH holding point"
                     if not idle_bad else "; ".join(idle_bad)))
    q = ctx.query("cs-fall", pad_is(cs, after=P0))
    highs = [q.after(node, vi, ei)[0] for node, vi, ei, e in
             T.occurrences(an, lambda e: e[EK] == "pad" and e[EPIN] == cs and e[EAFTER] == P1 and e[EBEFORE] == P0)]
    cs_high = min(highs) if highs else INF
    out.append(check("spi-cs-timing", "INFO",
                     f"CS setup (CS low to first SCK edge) {sorted(set(cs_setup))} cycles; CS hold (last SCK "
                     f"edge to CS high) {sorted(set(cs_hold))} cycles; CS high between frames >= {fc(cs_high)} "
                     f"cycles ({fmt_ns(cs_high / p.clock_hz)} at {p.clock_hz / 1e6:g} MHz)"))
    last_hold = sorted({h for h in holds if h < half})
    out.append(check("spi-mosi-setup-hold", "PASS" if setups and min(setups) >= half and min(holds) >= 1 else "FAIL",
                     f"MOSI stable >= {min(setups) if setups else '?'} cycles before and >= "
                     f"{min(holds) if holds else '?'} cycles after every target sampling edge "
                     f"({'leading' if cpha == 0 else 'trailing'})"
                     + (f"; the last bit's hold is only {last_hold} cycle(s) because the post-transfer SET "
                        "rewrites MOSI together with CS" if last_hold else "")))
    budget = half - SYNC
    out.append(check("spi-miso-sampling", "PASS" if sample_ok and budget > 0 else "FAIL",
                     f"MISO sampled on every {'leading' if cpha == 0 else 'trailing'} SCK edge; the sample reads "
                     f"the pad {SYNC} cycles before the edge, so target clock-to-out plus board round trip must "
                     f"fit in {budget} cycles ({fmt_ns(budget / p.clock_hz)} at {p.clock_hz / 1e6:g} MHz)",
                     clocks=clock_rows(budget, clocks)))
    out.append(check("spi-sck-frequency", "INFO", ", ".join(f"{f / 1e6:g}MHz: SCK {f / (2 * half) / 1e6:g} MHz"
                                                              for f in clocks)))
    return out


def spi_target_checks(ctx: Ctx, clocks: list[float]) -> list[dict]:
    an, p = ctx.an, ctx.p
    r = ROLES["spi-target"]
    sck, mosi, miso, cs = r["sck"], r["mosi"], r["miso"], r["cs"]
    mode = _mode(p.name, p.notes, "SPI target")
    cpol, cpha = mode >> 1, mode & 1
    s_min, note1 = parse_int(r"asserted at least (\d+) system clocks before first edge", p.notes)
    h_min, note2 = parse_int(r"each external half period at least (\d+) clocks", p.notes)
    out = []
    lead, trail = 1 - cpol, cpol                      # SCK level after leading / trailing edge
    w_cs0, w_cs1 = ctx.waitpin_pcs(cs, 0), ctx.waitpin_pcs(cs, 1)
    w_lead, w_trail = ctx.waitpin_pcs(sck, lead), ctx.waitpin_pcs(sck, trail)
    out_pred = pad_is(miso, cause="OUT")
    q_out = ctx.query("miso-out", out_pred)
    launch_pcs = (w_cs0 | w_trail) if cpha == 0 else w_lead
    worst = {}
    for node in an.iter_nodes():
        if node.bpc not in launch_pcs:
            continue
        first_out = []
        for v in node.variants:
            ts = [e[ET] for e in v.events if out_pred(e)]
            if ts:
                first_out.append(ts[0])
        if not first_out:
            continue
        lat = SYNC + max(first_out)
        kind = "CS" if node.bpc in w_cs0 else "SCK"
        worst[kind] = max(worst.get(kind, 0), lat)
    fails = []
    parts = []
    for kind, lat in sorted(worst.items()):
        budget = s_min if kind == "CS" else h_min
        margin = budget - (lat + 1)
        parts.append(f"after {kind} edge: MISO valid {lat}..{lat + 1} cycles later, budget {budget} "
                     f"(margin {margin})")
        if margin < 0:
            fails.append(kind)
    out.append(check("spi-target-miso-latency", "PASS" if worst and not fails else "FAIL",
                     "; ".join(parts) + f"; declared: \"{note1}\", \"{note2}\""))
    # MOSI sampling offset after the sampling edge
    samp_pcs = w_lead if cpha == 0 else w_trail
    offs = []
    for node in an.iter_nodes():
        if node.bpc in samp_pcs:
            for v in node.variants:
                offs += [e[ET] for e in v.events if e[EK] == "sample" and e[EPIN] == mosi]
    k = max(offs) if offs else None
    out.append(check("spi-target-mosi-hold", "PASS" if k is not None and h_min - (k + 1) >= 0 else "FAIL",
                     f"MOSI sampled from the pad {k}..{(k or 0) + 1} cycles after the sampling SCK edge; the "
                     f"controller keeps it for >= {h_min} cycles (margin {h_min - ((k or 0) + 1)})"))
    # re-arm within a half period
    wait_all = w_lead | w_trail | w_cs0 | w_cs1
    q_arm = ctx.query("arm", ctx.arrive_at(wait_all))
    arms = []
    for node in an.iter_nodes():
        if node.bpc in (w_lead | w_trail):
            arms.append(q_arm.from_anchor(node)[0])
    a = max(arms) if arms else INF
    out.append(check("spi-target-rearm", "PASS" if a <= h_min else "FAIL",
                     f"after each SCK edge is seen the firmware waits for the next edge within {fc(a)} cycles "
                     f"(<= {h_min}: no edge observed late; margin {fc(h_min - a)})"))
    arm_cs = max((q_arm.from_anchor(n)[0] for n in an.iter_nodes() if n.bpc in w_cs0), default=INF)
    out.append(check("spi-target-cs-setup", "PASS" if arm_cs <= s_min else "FAIL",
                     f"after CS assertion is seen, waiting for the first SCK edge within {fc(arm_cs)} cycles "
                     f"(declared CS setup {s_min}; margin {fc(s_min - arm_cs)})"))
    rel = [n for n in ctx.holding_nodes() if an.pads(n.state)[miso] != PZ]
    out.append(check("spi-target-miso-released", "PASS" if not rel else "FAIL",
                     "MISO released (high-Z) at the PULL holding point between frames" if not rel else
                     f"MISO driven at {[T.node_label(an, n) for n in rel]}"))
    return out


# ----------------------------------------------------------------------------
# I2C
# ----------------------------------------------------------------------------

def i2c_controller_checks(ctx: Ctx, clocks: list[float]) -> list[dict]:
    an, p = ctx.an, ctx.p
    scl, sda = ROLES["i2c"]["scl"], ROLES["i2c"]["sda"]
    out = []
    drive_high = [e for n in an.iter_nodes() for v in n.variants for e in v.events
                  if e[EK] == "pad" and e[EPIN] in (scl, sda) and e[EAFTER] & P1]
    out.append(check("i2c-open-drain", "PASS" if not drive_high and p.open_drain & 0xC0 == 0xC0 else "FAIL",
                     "SCL/SDA are open-drain and never driven high (pull-ups define the high level)"))
    scl_fall = pad_is(scl, after=P0)
    scl_rel = (lambda e: pad_is(scl, after=PZ)(e) and not e[ECAUSE].startswith("fault")
               and e[ECAUSE] != "HALT")
    scl_any_rel = pad_is(scl, after=PZ)
    w_scl = ctx.waitpin_pcs(scl, 1)
    arrive_w = ctx.arrive_at(w_scl)
    anchor_w = ctx.anchor_after(w_scl)
    unsafe = []
    for node, vi, ei, e in T.occurrences(an, scl_rel):
        if e[ECAUSE].startswith("fault") or e[ECAUSE] == "HALT":
            continue
        first = ctx.first_of(node, vi, ei, {"wait": arrive_w, "fall": scl_fall})
        if "fall" in first:
            unsafe.append(f"pc{e[EPC]}")
    out.append(check("i2c-stretch-safe", "PASS" if not unsafe else "FAIL",
                     "every SCL release is followed by WAITPIN SCL==1 before SCL is driven low again "
                     "(clock stretching honoured; LIMIT bounds it)" if not unsafe else
                     f"SCL driven low again without observing it high after releases at {sorted(set(unsafe))}"))

    def is_start(e: tuple) -> bool:
        return (e[EK] == "pad" and e[EPIN] == sda and e[EAFTER] == P0 and e[EBEFORE] == PZ
                and ctx.pads_at(e)[scl] == PZ)

    def is_stop(e: tuple) -> bool:
        return (e[EK] == "pad" and e[EPIN] == sda and e[EAFTER] == PZ and e[EBEFORE] == P0
                and ctx.pads_at(e)[scl] == PZ)

    def is_data(e: tuple) -> bool:
        return e[EK] == "pad" and e[EPIN] == sda and ctx.pads_at(e)[scl] == P0

    bad_sda = [e for n in an.iter_nodes() for v in n.variants for e in v.events
               if e[EK] == "pad" and e[EPIN] == sda and ctx.pads_at(e)[scl] != P0
               and not is_start(e) and not is_stop(e) and not e[ECAUSE].startswith("fault")
               and e[ECAUSE] != "HALT"]
    out.append(check("i2c-sda-only-while-scl-low", "PASS" if not bad_sda else "FAIL",
                     "SDA changes only while SCL is held low, except START/STOP conditions" if not bad_sda else
                     f"SDA changes with SCL released at pcs {sorted({e[EPC] for e in bad_sda})}"))

    def mn(rows: list[dict]) -> float:
        return min((r["min"] for r in rows), default=INF)

    runts = [r for r in ctx.distances("scl_fall", scl_fall, "scl_any_rel", scl_any_rel)
             if r["min"] == r["max"] and r["min"] < mn(ctx.distances("scl_fall", scl_fall, "scl_rel", scl_rel))]
    runt_text = sorted({(r["pc"], int(r["min"])) for r in runts})
    if runts:
        faults = sorted({v.end[2] for n in an.iter_nodes() for v in n.variants
                         if v.kind == "fault" and v.end[2] not in (1, 2)})
        out.append(check("i2c-fault-release", "WARN",
                         f"on an explicit fault {faults} SCL is released {sorted({t for _, t in runt_text})} "
                         f"cycle(s) after being driven low at pcs {sorted({pc for pc, _ in runt_text})}: a "
                         f"runt SCL low pulse ({fmt_ns(min(t for _, t in runt_text) / p.clock_hz)} at "
                         f"{p.clock_hz / 1e6:g} MHz, below tSP=50 ns only above 40 MHz) and no STOP condition "
                         "before the bus is released"))

    params: dict[str, float] = {}
    lows = ctx.distances("scl_fall", scl_fall, "scl_rel", scl_rel)
    half, src = _half_period(p)
    short = sorted({(r["pc"], fc(r["min"])) for r in lows if r["min"] < half})
    by_len = defaultdict(int)
    for r in lows:
        if r["min"] != INF:
            by_len[fc(r["min"]) if r["min"] == r["max"] else f"{fc(r['min'])}..{fc(r['max'])}"] += 1
    detail = (f"SCL low phases (cycles: occurrences) {dict(by_len)}; every phase >= half-period {half} ({src})"
              if not short else
              f"SCL low phase shorter than the half-period {half} ({src}) after SCL is pulled low at "
              + ", ".join(f"pc{pc} ({n} cycles)" for pc, n in short)
              + f"; all phases {dict(by_len)}")
    out.append(check("i2c-scl-low-phase", "PASS" if not short else "FAIL", detail))
    params["tLOW"] = mn(lows)
    params["tHIGH"] = mn(ctx.distances("w_scl", anchor_w, "scl_fall", scl_fall)) + SYNC
    params["tHD;STA"] = mn(ctx.distances("start", is_start, "scl_fall", scl_fall))
    params["tSU;STA"] = mn(ctx.distances("w_scl", anchor_w, "start", is_start)) + SYNC
    params["tSU;STO"] = mn(ctx.distances("w_scl", anchor_w, "stop", is_stop)) + SYNC
    params["tBUF"] = mn(ctx.distances("stop", is_stop, "start", is_start))
    params["tSU;DAT"] = mn(ctx.distances("data", is_data, "scl_rel", scl_rel))
    hd = ctx.distances("scl_fall", scl_fall, "data", is_data)
    params["tHD;DAT"] = mn(hd)
    params["fSCL_period"] = params["tLOW"] + params["tHIGH"]
    vd = [r["min"] for r in hd if r["min"] == r["max"] and r["min"] < params["tLOW"]]
    params["tVD;DAT"] = max(vd) if vd else INF
    out.append(check("i2c-bus-timing-cycles", "INFO",
                     "; ".join(f"{k} {'>=' if k != 'tVD;DAT' else '<='} {fc(v)}" for k, v in params.items())
                     + " cycles (tHIGH/tSU;STA/tSU;STO counted from the synchronized observation of SCL high, "
                     f"+{SYNC} cycles input latency; stretch-aware)", params={k: v for k, v in params.items()}))
    for mode, limits in I2C_MODES.items():
        fmax, fmin, worst = INF, 0.0, []
        for key, (t_min, t_max) in limits.items():
            cyc = params.get(key, INF)
            if cyc == INF:
                continue
            if t_min:
                fmax = min(fmax, cyc / t_min)
                if cyc / t_min == fmax:
                    worst.append(key)
            if t_max:
                fmin = max(fmin, cyc / t_max)
        at = {f"{f / 1e6:g}MHz": ("PASS" if fmin <= f <= fmax else "FAIL") for f in clocks}
        nominal = "PASS" if fmin <= p.clock_hz <= fmax else "FAIL"
        limiting = min(((params[k] / v[0], k) for k, v in limits.items() if v[0] and params.get(k, INF) != INF),
                       default=(INF, "-"))[1]
        out.append(check(f"i2c-{mode}", "INFO",
                         f"UM10204 {mode}: met for clocks {fmin / 1e6:.3g}..{fmax / 1e6:.3g} MHz "
                         f"(limited by {limiting}); at {p.clock_hz / 1e6:g} MHz {nominal}; "
                         + ", ".join(f"{k} {v}" for k, v in at.items()),
                         max_clock_hz=fmax, min_clock_hz=fmin, at=at))
    return out


def i2c_target_checks(ctx: Ctx, clocks: list[float]) -> list[dict]:
    an, p = ctx.an, ctx.p
    scl, sda = ROLES["i2c"]["scl"], ROLES["i2c"]["sda"]
    phase, note = parse_int(r"each low/high phase at least (\d+) system clocks", p.notes)
    out = []
    w_low, w_high = ctx.waitpin_pcs(scl, 0), ctx.waitpin_pcs(scl, 1)
    sda_ev = pad_is(sda)
    lat = []
    outside = []
    for node in an.iter_nodes():
        for v in node.variants:
            for e in v.events:
                if not sda_ev(e) or e[ECAUSE].startswith("fault") or e[ECAUSE] == "HALT":
                    continue
                if node.bpc in w_low or (node.kind in (T.BLOCK_TX, T.BLOCK_RX) and an.pads(node.state)[scl] == P0) \
                        or ctx.pads_at(e)[scl] == P0:
                    if node.bpc in w_low:
                        lat.append(e[ET] + SYNC)
                else:
                    outside.append(e[EPC])
    worst = max(lat) if lat else INF
    margin = phase - (worst + 1)
    out.append(check("i2c-target-sda-latency", "PASS" if lat and margin >= 0 else "FAIL",
                     f"SDA driven {fc(worst)}..{fc(worst + 1)} cycles after SCL falls (input latency included); "
                     f"controller keeps SCL low >= {phase} cycles: tSU;DAT margin {fc(margin)} cycles "
                     f"({fmt_ns(margin / p.clock_hz)} at {p.clock_hz / 1e6:g} MHz); declared: \"{note}\""))
    out.append(check("i2c-target-sda-only-after-scl-low", "PASS" if not outside else "FAIL",
                     "the target changes SDA only after observing SCL low (or while stretching SCL)"
                     if not outside else f"SDA changed without SCL low at pcs {sorted(set(outside))}"))
    # Re-arm: after a pin wait completes, the next pin wait must be reached before the
    # controller's next edge, unless the target first pulls SCL low (stretch), which
    # holds the controller until the target releases it.
    waits = w_low | w_high | ctx.waitpin_pcs(sda, 0) | ctx.waitpin_pcs(sda, 1)
    arm, grab = -1, -1
    for n in an.iter_nodes():
        if n.bpc not in waits:
            continue
        for v in n.variants:
            for e in v.events:
                if e[EK] == "pad" and e[EPIN] == scl and e[EAFTER] == P0:
                    grab = max(grab, e[ET] + SYNC)
                    break
                if e[EK] == "arrive" and e[EPC] in waits:
                    arm = max(arm, e[ET])
                    break
    worst = max(arm, grab + 1)
    out.append(check("i2c-target-rearm", "PASS" if worst <= phase else "FAIL",
                     f"after each observed edge the firmware reaches the next pin wait within {arm} cycles, or "
                     f"grabs SCL (stretch) within {grab}..{grab + 1} cycles of the edge; both <= {phase} "
                     f"(margin {phase - worst})"))
    samples = [e[ET] for n in an.iter_nodes() if n.bpc in w_high for v in n.variants for e in v.events
               if e[EK] == "sample" and e[EPIN] == sda]
    k = max(samples) if samples else INF
    out.append(check("i2c-target-sample-hold", "PASS" if k + 1 <= phase else "FAIL",
                     f"SDA sampled from the pad {fc(k)}..{fc(k + 1)} cycles after SCL rises "
                     f"(data held by the controller >= {phase} cycles; margin {fc(phase - k - 1)})"))
    stretch = [T.node_label(an, n) for n in ctx.holding_nodes() if an.pads(n.state)[scl] != P0]
    out.append(check("i2c-target-stretch-at-holding", "PASS" if not stretch else "FAIL",
                     "SCL held low (clock stretch) at every FIFO holding point" if not stretch else
                     f"SCL not held low at {stretch}"))
    return out


# ----------------------------------------------------------------------------
# JTAG, waveform, event transmitter
# ----------------------------------------------------------------------------

def jtag_checks(ctx: Ctx, clocks: list[float]) -> list[dict]:
    an, p = ctx.an, ctx.p
    r = ROLES["jtag"]
    tck, tdi, tdo, tms = r["tck"], r["tdi"], r["tdo"], r["tms"]
    out = []
    changes = [e for n in an.iter_nodes() for v in n.variants for e in v.events
               if e[EK] == "pad" and e[EPIN] in (tdi, tms) and not e[ECAUSE].startswith("fault")
               and e[ECAUSE] != "HALT" and e[EBEFORE] != PZ]
    high = [e for e in changes if ctx.pads_at(e)[tck] != P0]
    out.append(check("jtag-tms-tdi-change-on-tck-low", "PASS" if not high else "FAIL",
                     f"all {len(changes)} TMS/TDI writes happen while TCK is low" if not high else
                     f"TMS/TDI written while TCK high at pcs {sorted({e[EPC] for e in high})}"))
    rise = pad_is(tck, after=P1)
    tms_tdi = (lambda e: e[EK] == "pad" and e[EPIN] in (tdi, tms))
    setup = min((r["min"] for r in ctx.distances("tt", tms_tdi, "rise", rise)), default=INF)
    hold = min((r["min"] for r in ctx.distances("rise", rise, "tt", tms_tdi)), default=INF)
    q = ctx.query("tck", pad_is(tck))
    spac = [q.after(n, vi, ei)[0] for n, vi, ei, e in T.occurrences(an, pad_is(tck))
            if not e[ECAUSE].startswith("fault")]
    tck_min = min((s for s in spac if s != INF), default=INF)
    out.append(check("jtag-setup-hold", "PASS" if setup >= 1 and hold >= 1 else "FAIL",
                     f"TMS/TDI setup to TCK rise >= {fc(setup)} cycles, hold after TCK rise >= {fc(hold)} cycles; "
                     f"TCK low/high phases >= {fc(tck_min)} cycles"))
    samp = [(e[ET], e[EPC]) for n in an.iter_nodes() for v in n.variants for e in v.events
            if e[EK] == "sample" and e[EPIN] == tdo]
    rises = {(e[ET], e[EPC]) for n in an.iter_nodes() for v in n.variants for e in v.events if rise(e)}
    ok = samp and all(s in rises for s in samp)
    xf = [i for i in p.ins if i.op == T.XFER]
    budget = min((i.b for i in xf), default=0) - SYNC
    out.append(check("jtag-tdo-sampling", "PASS" if ok and budget > 0 else "FAIL",
                     f"TDO sampled exactly on TCK rising edges; TDO launched on the falling edge must "
                     f"reach the pad within {budget} cycles ({fmt_ns(budget / p.clock_hz)} at "
                     f"{p.clock_hz / 1e6:g} MHz)", clocks=clock_rows(budget, clocks)))
    start = an.start_node()
    count, stop = 0, False
    for e in start.variants[0].events:
        if e[EK] == "pad" and e[EPIN] == tms and e[EAFTER] == P0 and e[EBEFORE] == P1:
            break
        if rise(e) and ctx.pads_at(e)[tms] == P1:
            count += 1
    out.append(check("jtag-tap-reset", "PASS" if count >= 5 else "FAIL",
                     f"{count} TCK rising edges with TMS high before the first TMS low (>= 5 resets any TAP)"))
    return out


def waveform_checks(ctx: Ctx, clocks: list[float]) -> list[dict]:
    an, p = ctx.an, ctx.p
    pin = ROLES["waveform"]["out"]
    half, src = _half_period(p)
    count_word = re.search(r"(sixteen|\d+) pin0 pulses", " ".join(p.notes))
    count = 16 if count_word and count_word.group(1) == "sixteen" else int(count_word.group(1)) if count_word else None
    v = an.start_node().variants[0]
    rises = [e[ET] for e in v.events if e[EK] == "pad" and e[EPIN] == pin and e[EAFTER] == P1]
    falls = [e[ET] for e in v.events if e[EK] == "pad" and e[EPIN] == pin and e[EAFTER] == P0 and e[EBEFORE] == P1]
    highs = {b - a for a, b in zip(rises, falls)}
    lows = {b - a for a, b in zip(falls, rises[1:])}
    ok = len(rises) == count and highs == {half} and lows == {2 * half}
    out = [check("waveform-pulses", "PASS" if ok else "FAIL",
                 f"{len(rises)} pulses, high {sorted(highs)} cycles, low {sorted(lows)} cycles; declared "
                 f"{count} pulses high {half} / low {2 * half} (half-period {half}: {src})",
                 clocks=clock_rows(half, clocks))]
    t_time = [e[ET] for e in v.events if e[EK] == "inter" and e[ECAUSE] == "time"]
    out.append(check("waveform-timestamp", "INFO",
                     f"TIME issues {t_time[0] if t_time else '?'} cycles after the START edge, "
                     f"{(t_time[0] - falls[-1]) if t_time and falls else '?'} cycles after the last falling edge"))
    return out


def _half_period(p: T.Program) -> tuple[int, str]:
    if p.path:
        doc = Path(p.path).resolve().parent.parent / "docs" / "firmware.md"
        if doc.exists():
            m = re.search(r"half-period is (\d+) clocks", doc.read_text())
            if m:
                return int(m.group(1)), "docs/firmware.md default"
    return 32, "assumed default"


def event_checks(ctx: Ctx, clocks: list[float]) -> list[dict]:
    an, p = ctx.an, ctx.p
    pin = ROLES["event-transmitter"]["out"]
    half, src = _half_period(p)
    fall = pad_is(pin, after=P0, before=P1)
    rise = pad_is(pin, after=P1, before=P0)
    widths = ctx.distances("fall", fall, "rise", rise)
    gaps = ctx.distances("rise", rise, "fall", fall)
    w = sorted({(r["min"], r["max"]) for r in widths})
    g = min((r["min"] for r in gaps), default=INF)
    w_pcs = {i.pc for i in p.ins if i.op == T.WAITEVENT}
    lat = [e[ET] for n in an.iter_nodes() if n.bpc in w_pcs for v in n.variants for e in v.events if fall(e)]
    ok = w == [(half, half)]
    return [check("event-pulse", "PASS" if ok else "FAIL",
                  f"low pulse exactly {w[0][0] if w else '?'} cycles (half-period {half}, {src}); pulse starts "
                  f"{lat[0] if lat else '?'} cycle(s) after WAITEVENT completes; minimum high time between "
                  f"pulses {fc(g)} cycles when events are already pending", clocks=clock_rows(half, clocks))]


FAMILY_CHECKS = {
    "uart-tx": uart_tx_checks, "uart-rx": uart_rx_checks,
    "spi-controller": spi_controller_checks, "spi-target": spi_target_checks,
    "i2c-controller": i2c_controller_checks, "i2c-target": i2c_target_checks,
    "jtag": jtag_checks, "waveform": waveform_checks, "event-transmitter": event_checks,
}


def check_program(an: T.Analysis, clocks: list[float] | None = None, name: str | None = None) -> list[dict]:
    clocks = list(clocks or DEFAULT_CLOCKS)
    if an.p.clock_hz not in clocks:
        clocks.append(float(an.p.clock_hz))
    clocks.sort()
    ctx = Ctx(an)
    out = generic_checks(ctx)
    fam = family(name or (Path(an.p.path).name.removesuffix(".image.json") if an.p.path else an.p.name))
    fn = FAMILY_CHECKS.get(fam)
    if fn is not None:
        try:
            out += fn(ctx, clocks)
        except Exception as exc:  # noqa: BLE001 - report, never hide
            out.append(check(f"{fam}-checks", "FAIL", f"check raised {type(exc).__name__}: {exc}"))
    return out


# ----------------------------------------------------------------------------
# flagship scenario and full report
# ----------------------------------------------------------------------------

def flagship_checks(firmware: Path, analyses: dict[str, T.Analysis]) -> list[dict]:
    path = firmware / "flagship-scenario.json"
    if not path.exists():
        return [check("flagship", "N/A", "no flagship-scenario.json")]
    sc = json.loads(path.read_text())
    out = []
    names = {e["engine"]: e["source"].removesuffix(".source.json") for e in sc["images"]}
    progs = {eng: analyses[n].p for eng, n in names.items() if n in analyses}
    engines_ok = all(progs[e].engine == e for e in progs)
    owned = [progs[e].ownership for e in sorted(progs)]
    disjoint = all(not (a & b) for i, a in enumerate(owned) for b in owned[i + 1:])
    out.append(check("flagship-ownership", "PASS" if engines_ok and disjoint else "FAIL",
                     "images on engines " + ", ".join(f"{e}:{n} (0x{progs[e].ownership:02x})" for e, n in sorted(names.items()))
                     + "; ownership disjoint and matches each image's engine"))
    conn = {c["pin"]: c["signal"] for c in sc["pin_connections"]}
    role_ok = conn.get(0) == "uart_tx" and conn.get(1) == "uart_rx" and conn.get(2) == "spi_sck" \
        and conn.get(6) == "i2c_scl" and conn.get(7) == "i2c_sda"
    out.append(check("flagship-pin-roles", "PASS" if role_ok else "FAIL",
                     "pin connections match the image pin roles of docs/firmware.md"))
    # independent schedules: each engine's segments are analyzed in isolation (formal timing isolation)
    for e, n in sorted(names.items()):
        an = analyses[n]
        out.append(check(f"flagship-engine{e}-schedule", "PASS" if an.is_exact() else "FAIL",
                         f"{n}: {len(an.order)} contexts, WCET between boundaries "
                         f"{max((v.t_end for nd in an.iter_nodes() for v in nd.variants), default=0)} cycles; "
                         "schedule exact per engine (no widening). Isolation from the other engines, host and "
                         f"mover rests on formal job timing_isolation_prove_k{e} (formal/README.md; not re-run "
                         "by this tool)"))
    # bridge rate: UART RX produces one word per >= 10 bit periods; SPI consumes one per loop period
    route = sc["routes"][0] if sc.get("routes") else None
    if route:
        src_an, dst_an = analyses[names[route["source_engine"]]], analyses[names[route["destination_engine"]]]
        bit = sc["stimulus"]["uart_bit_cycles"]
        produce = 10 * bit
        pulls = {i.pc for i in dst_an.p.ins if i.op == T.PULL}
        q = dst_an.query(lambda e: e[EK] == "arrive" and e[EPC] in pulls)
        service = max((q.from_anchor(nd)[0] for nd in dst_an.iter_nodes() if nd.bpc in pulls), default=INF)
        # a PULL-to-PULL cycle includes the blocking PUSH of the received word (0 stall when RX has room)
        fifo = dst_an.p.arch.fifo_words
        expected = len(sc["expected"].get("engine2_rx_words", []))
        out.append(check("flagship-bridge-rate", "PASS" if service <= produce else "FAIL",
                         f"UART RX (engine{route['source_engine']}) delivers at most one word per {produce} cycles "
                         f"(10 x {bit}); the SPI engine returns to PULL {fc(service)} cycles after taking a word "
                         f"when its RX FIFO has room: the route never backs up at line rate (margin "
                         f"{fc(produce - service)} cycles/word)"))
        out.append(check("flagship-spi-rx-capacity", "PASS" if expected <= fifo else "FAIL",
                         f"{expected} SPI return words fit the {fifo}-word RX FIFO without host service; the "
                         f"{fifo + 1}th would stall the SPI PUSH, back up the route and end in the UART strict-PUSH "
                         "fault4 (documented overload behaviour)"))
    return out


def full_report(firmware: Path, out_dir: Path, clocks: list[float] | None = None,
                validation: Path | None = None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    clocks = list(clocks or DEFAULT_CLOCKS)
    started = time.time()
    analyses: dict[str, T.Analysis] = {}
    results: dict[str, Any] = {}
    status = 0
    for path in sorted(firmware.glob("*.image.json")):
        name = path.name.removesuffix(".image.json")
        prog = T.Program.from_image(path)
        an = T.Analysis(prog)
        analyses[name] = an
        summary = T.summarize(an)
        summary["image_name_field"] = prog.name
        checks = check_program(an, clocks, name)
        if prog.name != name:
            checks.append(check("image-name", "INFO", f"image 'name' field is \"{prog.name}\" "
                                f"but the file is {path.name}"))
        summary["checks"] = checks
        summary["schedule_text"] = T.render_schedule(an, max_rows=400)
        results[name] = summary
        if any(c["status"] == "FAIL" for c in checks):
            status = 1
    flag = flagship_checks(firmware, analyses)
    if any(c["status"] == "FAIL" for c in flag):
        status = 1
    val = None
    if validation and validation.exists():
        val = json.loads(validation.read_text())
    report = {"tool": f"pe_timing {T.TOOL_VERSION}", "firmware": str(firmware),
              "clocks_hz": clocks, "images": results, "flagship": flag, "validation": val,
              "seconds": round(time.time() - started, 2)}
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=T._json_default))
    (out_dir / "report.md").write_text(render_markdown(report))
    (out_dir / "checks.json").write_text(json.dumps(
        {"images": {k: v["checks"] for k, v in results.items()}, "flagship": flag},
        indent=1, default=T._json_default))
    n_pass = sum(c["status"] == "PASS" for r in results.values() for c in r["checks"]) + \
        sum(c["status"] == "PASS" for c in flag)
    n_fail = sum(c["status"] == "FAIL" for r in results.values() for c in r["checks"]) + \
        sum(c["status"] == "FAIL" for c in flag)
    print(f"{len(results)} images + flagship: {n_pass} PASS, {n_fail} FAIL -> {out_dir}/report.md")
    return status


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Static timing report", "",
             f"Generated by `tools/timing/pe_timing.py report` ({report['tool']}) from `{report['firmware']}`. "
             "All times are exact clock-cycle offsets from the previous synchronization boundary "
             "(START, PULL, blocking PUSH, WAITPIN, WAITEVENT) unless a range is given; ranges that cross a "
             "boundary use its minimum stall and LIMIT-1 (or unbounded) maximum.", ""]
    val = report.get("validation")
    if val:
        s = val.get("stats", {})
        lines += ["## Ground truth (reference model)", "",
                  f"Result **{val.get('result')}**: {s.get('runs_ok')}/{s.get('runs')} engine runs, "
                  f"{s.get('ticks_checked')} edges, {s.get('attempts_checked')} issue attempts and "
                  f"{s.get('pad_changes_matched')}/{s.get('pad_changes_observed')} pad changes matched the static "
                  f"schedules exactly; {s.get('segments_completed')} complete segments, "
                  f"{s.get('boundaries_completed')} boundary completions, {s.get('timeouts_matched')} LIMIT "
                  "timeouts at the predicted edge"
                  + (f"; {s.get('runs_unchecked_budget')} runs of random programs whose analysis hit a budget "
                     "were left unchecked (neither passed nor failed)" if s.get("runs_unchecked_budget") else "")
                  + ".", ""]
        mut = val.get("mutants") or {}
        if mut:
            lines += ["Negative controls (deliberately wrong analyzers, each must be caught): "
                      + ", ".join(f"`{k}` {'caught' if v.get('detected') else 'MISSED'}" for k, v in mut.items()), ""]
        mar = val.get("margins") or {}
        for k, v in mar.items():
            lines += [f"Margin experiment `{k}`: {'agrees' if v.get('agrees') else 'DISAGREES'}"
                      f"{' exactly' if v.get('exact') else ''}. {v.get('summary', '')}", ""]
        runs = s.get("per_program_runs", {})
        if s.get("custom_programs") is not None or val.get("custom_programs"):
            cp = val.get("custom_programs", {})
            lines += [f"Random and legacy programs analyzed on the fly: {cp.get('count', 0)} "
                      f"(not exact: {cp.get('not_exact_count', len(cp.get('not_exact', [])))}).", ""]
    cov = ((val or {}).get("stats") or {}).get("variant_coverage", {})
    runs = ((val or {}).get("stats") or {}).get("per_program_runs", {})
    lines += ["## Summary", "", "| image | engine | contexts | variants | WCET between boundaries | model runs | "
              "variants observed | checks |", "|---|---:|---:|---:|---:|---:|---:|---|"]
    for name, r in report["images"].items():
        cs = r["checks"]
        tally = f"{sum(c['status'] == 'PASS' for c in cs)} PASS"
        fails = [c["id"] for c in cs if c["status"] == "FAIL"]
        warns = [c["id"] for c in cs if c["status"] == "WARN"]
        if fails:
            tally += f", FAIL: {', '.join(fails)}"
        if warns:
            tally += f", WARN: {', '.join(warns)}"
        c = cov.get(name)
        seen = f"{c['observed']}/{c['variants']}" if c else "-"
        lines.append(f"| {name} | {r['engine']} | {r['graph']['nodes']} | {r['graph']['variants']} | "
                     f"{r['wcet_between_boundaries']} | {runs.get(name, '-')} | {seen} | {tally} |")
    lines += ["", "## Flagship scenario", ""]
    for c in report["flagship"]:
        lines.append(f"- **{c['status']}** `{c['id']}`: {c['detail']}")
    for name, r in report["images"].items():
        lines += ["", f"## {name}", "",
                  f"Engine {r['engine']}, owned 0x{r['owned_pins']:02x}, open-drain 0x{r['open_drain']:02x}, "
                  f"{r['architecture']['issue']}, {r['clock_hz'] / 1e6:g} MHz annotation. Notes: "
                  + " ".join(f"\"{n}\"" for n in r["notes"]), "", "### Checks", ""]
        for c in r["checks"]:
            lines.append(f"- **{c['status']}** `{c['id']}`: {c['detail']}")
        lines += ["", "### Boundaries", "", "| boundary (context) | stall min..max | segment BCET..WCET | holding pads |",
                  "|---|---|---|---|"]
        for b in r["boundaries"]:
            lines.append(f"| {b['node']} | {b['min_stall']}..{b['max_stall']} | {b['segment_bcet']}..{b['segment_wcet']} | "
                         + " ".join(f"{k}={v}" for k, v in b["holding_pads"].items()) + " |")
        lines += ["", "### Pin spacing", ""]
        for pin, s in r["pin_spacing"].items():
            lines.append(f"- {pin}: exact spacings {s['exact_spacings']}; across boundaries "
                         f"{s['variable_spacings']}; minimum {s['min']}")
        lines += ["", "### Loop periods", ""]
        for lp in r["loops"]:
            per = ", ".join(f"{x['min']}" + (f"..{x['max']}" if x['max'] != x['min'] else "") + f" (x{x['count']})"
                            for x in lp["periods"])
            lines.append(f"- pc{lp['header_pc']} `{lp['header']}` via {', '.join(lp['via'])}: {per}")
        lines += ["", "### Edge schedules", "", "```", r["schedule_text"], "```"]
    return "\n".join(lines) + "\n"
