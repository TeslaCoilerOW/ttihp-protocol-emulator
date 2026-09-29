# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Declared protocol timing of the SWD, WS2812B, PS/2 and 1-Wire images.

``pe_contracts`` registers these checks (``ROLES``, ``FAMILY_CHECKS``) at the
end of its module. Each family rebuilds the pin waveform of one image from
pe_timing's exact boundary graph and compares it with the image's notes and
with the limits of the cited specification:

* ``swd-read``: Arm ADIv5 Serial Wire Debug (ADIv5.1 Supplement, DSA09-PRDC-008772:
  section 6.2.1 JTAG-to-SWD select sequence, 8.2 clocking after the data
  phase, 8.3.6 line reset, erratum 2.6 Park driven high).
* ``ws2812``: Worldsemi WS2812B datasheet, data transfer time table.
* ``ws2812b-v5``: Worldsemi WS2812B-V5 datasheet V1.0, data transfer time table.
* ``ps2-device``: A. Chapweske, "The PS/2 Mouse/Keyboard Protocol" (2003),
  device-to-host timing.
* ``onewire-master``: Maxim AN126 (rev. 052802) Table 1 operations, checked
  against the DS18B20 datasheet (rev. 042208) AC characteristics.

A *nominal path* joins the segments of one transaction with every boundary
completing after its minimum stall (the TX word is queued, the RX FIFO has
room, the lines are idle). Quantities inside a segment and across a
zero-stall soft boundary are exact; where a real stall can stretch a quantity,
the check says so. Times are digital schedules at the pads: pad, board, rise
and fall delays are not included.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import pe_timing as T
from pe_timing import EAFTER, EBEFORE, ECAUSE, EK, EPC, EPIN, ET, INF, P0, P1, PZ

SYNC = T.SYNC_LATENCY

# Pin roles, as in the images' notes and docs/firmware.md.
ROLES: dict[str, dict[str, int]] = {
    "swd-read": {"swclk": 0, "swdio": 1},
    "ws2812": {"dout": 2}, "ws2812b-v5": {"dout": 2},
    "ps2-device": {"clk": 4, "data": 5}, "ps2-host": {"clk": 4, "data": 5},
    "onewire-master": {"dq": 7},
}

# ----------------------------------------------------------------------------
# specification limits (seconds; None = no limit)
# ----------------------------------------------------------------------------

# Worldsemi WS2812B datasheet, "Data transfer time (TH+TL=1.25us+-600ns)":
# T0H 0.4 us, T1H 0.8 us, T0L 0.85 us, T1L 0.45 us, each +-150 ns; RES above 50 us.
WS2812B = {"name": "WS2812B datasheet",
           "T0H": (250e-9, 550e-9), "T1H": (650e-9, 950e-9), "T0L": (700e-9, 1000e-9),
           "T1L": (300e-9, 600e-9), "TH+TL": (650e-9, 1850e-9), "RES": (50e-6, None)}
# Worldsemi WS2812B-V5 datasheet V1.0, "Data Transfer Time":
# T0H 220-380 ns, T1H 580 ns-1 us, T0L 580 ns-1 us, T1L 580 ns-1 us, RES > 280 us.
WS2812B_V5 = {"name": "WS2812B-V5 datasheet V1.0",
              "T0H": (220e-9, 380e-9), "T1H": (580e-9, 1000e-9), "T0L": (580e-9, 1000e-9),
              "T1L": (580e-9, 1000e-9), "RES": (280e-6, None)}

# Chapweske, "The PS/2 Mouse/Keyboard Protocol": clock 10-16.7 kHz, high and low
# 30-50 us each; clock rise to data transition >= 5 us; data transition to clock
# fall 5-25 us; clock continuously high >= 50 us before the device transmits.
PS2 = {"clk_low": (30e-6, 50e-6), "clk_high": (30e-6, 50e-6), "clk_period": (1 / 16.7e3, 1 / 10e3),
       "rise_to_data": (5e-6, None), "data_to_fall": (5e-6, 25e-6), "idle_before": (50e-6, None),
       # host to device: CLK low >= 100 us before the request; the device clocks within 15 ms (a) of
       # the host pulling CLK low; a response arrives within 20 ms.
       "inhibit": (100e-6, None), "first_clock": (15e-3, None), "response": (20e-3, None)}

# Maxim AN126 Table 1 (standard speed): the recommended master delays.
AN126 = {"reset_low": 480e-6, "presence_sample": 70e-6, "reset_high": 480e-6,   # H, I, I+J
         "write1_low": 6e-6, "write0_low": 60e-6, "read_sample": 15e-6, "slot": 70e-6}
# DS18B20 datasheet AC characteristics (and its time-slot text).
DS18B20 = {"tRSTL": (480e-6, 960e-6),      # max: parasite power (note 2)
           "tRSTH": (480e-6, None),
           "presence": (60e-6, 75e-6),     # low for every tPDHIGH 15-60 us, tPDLOW 60-240 us
           "tLOW1": (1e-6, 15e-6), "tLOW0": (60e-6, 120e-6), "tRDV": (None, 15e-6),
           "tSLOT": (60e-6, 120e-6), "tREC": (1e-6, None),
           "write_sample": (15e-6, 60e-6)}  # the slave samples a write slot 15-60 us after the fall


def _c():
    import pe_contracts as C  # noqa: PLC0415 - pe_contracts imports this module at its end
    return C


def ns(seconds: float) -> str:
    if seconds >= 999.995e-6:
        return f"{seconds * 1e3:.1f} ms"
    return f"{seconds * 1e9:.0f} ns" if seconds < 9.9995e-6 else f"{seconds * 1e6:.2f} us"


def window_text(w: tuple) -> str:
    lo, hi = w
    if hi is None:
        return f">= {ns(lo)}"
    if lo is None:
        return f"<= {ns(hi)}"
    return f"{ns(lo)}..{ns(hi)}"


def clock_range(cycles: float, w: tuple) -> tuple[float, float]:
    """Clocks f for which ``cycles``/f lies inside the window ``w``."""
    lo, hi = w
    f_min = cycles / hi if hi else 0.0
    f_max = cycles / lo if lo else INF
    return f_min, f_max


def judge(name: str, cycles: float, w: tuple, clock_hz: float) -> dict[str, Any]:
    """One quantity against one window at the image clock: PASS/FAIL, margins, clock range."""
    t = cycles / clock_hz
    lo, hi = w
    ok = (lo is None or t >= lo - 1e-15) and (hi is None or t <= hi + 1e-15)
    f_min, f_max = clock_range(cycles, w)
    margins = []
    if lo is not None:
        margins.append(f"{ns(t - lo)} above the minimum")
    if hi is not None:
        margins.append(f"{ns(hi - t)} below the maximum")
    return {"name": name, "cycles": cycles, "time": ns(t), "window": window_text(w), "ok": ok,
            "margin": ", ".join(margins), "f_min": f_min, "f_max": f_max}


def rows_check(C, cid: str, rows: list[dict], clock_hz: float, source: str, extra: str = "") -> dict:
    ok = all(r["ok"] for r in rows)
    f_min = max(r["f_min"] for r in rows)
    f_max = min(r["f_max"] for r in rows)
    span = (f"all hold for clocks from {f_min / 1e6:.2f} to "
            + ("any" if f_max == INF else f"{f_max / 1e6:.2f}") + " MHz")
    detail = (f"{source} at {clock_hz / 1e6:g} MHz: "
              + "; ".join(f"{r['name']} {r['cycles']:g} clocks = {r['time']} ({r['window']}"
                          + (f"; {r['margin']}" if r["margin"] else "") + ")" for r in rows)
              + f". {span}" + (f". {extra}" if extra else ""))
    return C.check(cid, "PASS" if ok and f_min <= clock_hz <= f_max else "FAIL", detail,
                   rows=rows, f_min_hz=f_min, f_max_hz=f_max)


# ----------------------------------------------------------------------------
# paths through the boundary graph
# ----------------------------------------------------------------------------

def first_pc(v: T.Variant) -> int | None:
    for e in v.events:
        if e[EK] in ("issue", "arrive"):
            return e[EPC]
    return None


def walk(an: T.Analysis, node: T.Node, vi: int, choose: Callable[[T.Node], int | None],
         max_nodes: int = 64) -> tuple[list[tuple[int, tuple]], list[T.Node]]:
    """Events of one path, on one time axis: variant ``vi`` of ``node``, then the
    variant ``choose(successor)`` of each successor, every boundary completing
    after its minimum stall. Stops when ``choose`` returns None or the path ends."""
    out: list[tuple[int, tuple]] = []
    nodes = [node]
    t0 = 0
    for _ in range(max_nodes):
        v = node.variants[vi]
        out += [(t0 + e[ET], e) for e in v.events]
        if v.kind != "boundary":
            break
        succ = an.nodes[v.succ]
        nxt = choose(succ)
        if nxt is None:
            break
        t0 += v.t_end + succ.min_stall
        node, vi = succ, nxt
        nodes.append(node)
    return out, nodes


def pad_events(path: list[tuple[int, tuple]], pin: int, *, drop_fault: bool = True) -> list[tuple[int, tuple]]:
    return [(t, e) for t, e in path if e[EK] == "pad" and e[EPIN] == pin
            and not (drop_fault and (e[ECAUSE].startswith("fault") or e[ECAUSE] == "HALT"))]


def samples(path: list[tuple[int, tuple]], pin: int) -> list[int]:
    return [t for t, e in path if e[EK] == "sample" and e[EPIN] == pin]


def nodes_of(an: T.Analysis, op: int) -> list[T.Node]:
    return [n for n in an.iter_nodes() if n.kind != "start" and an.p.ins[n.bpc].op == op]


# ----------------------------------------------------------------------------
# SWD
# ----------------------------------------------------------------------------

SELECT = 0xE79E            # JTAG-to-SWD select sequence, LSB first (ADIv5.1 Supplement 6.2.1)


def header_fields(h: int) -> dict[str, int]:
    return {"start": h & 1, "APnDP": h >> 1 & 1, "RnW": h >> 2 & 1, "A2": h >> 3 & 1, "A3": h >> 4 & 1,
            "parity": h >> 5 & 1, "stop": h >> 6 & 1, "park": h >> 7 & 1}


def header_ok(h: int) -> bool:
    f = header_fields(h)
    return (f["start"] == 1 and f["stop"] == 0 and f["park"] == 1
            and f["parity"] == (f["APnDP"] ^ f["RnW"] ^ f["A2"] ^ f["A3"]))


def swd_checks(ctx: Any, clocks: list[float]) -> list[dict]:
    C = _c()
    an, p = ctx.an, ctx.p
    clk, dio = ROLES["swd-read"]["swclk"], ROLES["swd-read"]["swdio"]
    half, _ = C.parse_int(r"SWCLK half period (\d+) clocks", p.notes)
    n_high, _ = C.parse_int(r"Connect: (\d+) SWCLK cycles with SWDIO high", p.notes)
    n_high2, _ = C.parse_int(r"then (\d+) SWCLK cycles with SWDIO high \(line reset\)", p.notes)
    n_idle, _ = C.parse_int(r"starts with (\d+) idle cycles", p.notes)
    if None in (half, n_high, n_high2, n_idle):
        return [C.check("swd-declaration", "FAIL", "notes must declare 'SWCLK half period N clocks', "
                        "'Connect: N SWCLK cycles with SWDIO high', 'then N SWCLK cycles with SWDIO high "
                        "(line reset)' and 'starts with N idle cycles'")]
    m = next((re.search(r"0x([0-9A-Fa-f]{2}) reads DP DPIDR", n) for n in p.notes
              if re.search(r"0x([0-9A-Fa-f]{2}) reads DP DPIDR", n)), None)
    dpidr = int(m.group(1), 16) if m else None
    out: list[dict] = []

    # Declared DPIDR read header.
    if dpidr is None:
        out.append(C.check("swd-dpidr-header", "FAIL", "no '0xNN reads DP DPIDR' declaration"))
    else:
        f = header_fields(dpidr)
        good = header_ok(dpidr) and f["APnDP"] == 0 and f["RnW"] == 1 and f["A2"] == f["A3"] == 0
        out.append(C.check("swd-dpidr-header", "PASS" if good else "FAIL",
                           f"declared header 0x{dpidr:02X} = " + ", ".join(f"{k} {v}" for k, v in f.items())
                           + ": a DP read of address 0x0 (DPIDR) with even parity over APnDP, RnW, A[2:3], "
                           "Stop 0 and Park 1 (ADIv5.1 Supplement 8.3.5)"))

    def tr(v: T.Variant, t0: int = 0) -> list[tuple[int, tuple]]:
        return [(t0 + e[ET], e) for e in v.events]

    rises_all, variants = [], []
    for node in nodes_of(an, T.PULL):
        for vi, v in enumerate(node.variants):
            if v.kind == "fault":
                variants.append(("fault", node, vi, v, [], []))
                continue
            path = tr(v)
            tail = None
            if v.kind == "boundary" and an.p.ins[v.end[2]].op == T.PUSH:
                sv = an.nodes[v.succ].variants[0]
                if sv.kind == "fault" and sv.end[2] == 80:   # ACK not OK: a turnaround after the PUSH
                    tail = [(v.t_end + e[ET], e) for e in sv.events]
            full = path + (tail or [])
            rises = [(t, ctx.pads_at(e)[dio]) for t, e in full
                     if e[EK] == "pad" and e[EPIN] == clk and e[EAFTER] == P1]
            smp = samples(full, dio)
            variants.append(("ok" if tail is None else "ack", node, vi, v, rises, smp))
            rises_all += rises

    def bits(value: int, n: int) -> list[int]:
        return [P1 if value >> k & 1 else P0 for k in range(n)]

    connect = [P1] * n_high + bits(SELECT, 16) + [P1] * n_high2
    problems, seen = [], set()
    for kind, node, vi, v, rises, smp in variants:
        label = f"{T.node_label(an, node)} v{vi}"
        if kind == "fault":
            clk_edges = [e for e in v.events if e[EK] == "pad" and e[EPIN] == clk and e[EAFTER] == P1]
            if v.end[2] != 82 or clk_edges:
                problems.append(f"{label}: fault {v.end[2]} with {len(clk_edges)} SWCLK rises")
            seen.add("fault82")
            continue
        states = [s for _, s in rises]
        pos = 0
        has_connect = states[:len(connect)] == connect
        if has_connect:
            pos = len(connect)
            seen.add("connect")
        if states[pos:pos + n_idle] != [P0] * n_idle:
            problems.append(f"{label}: {n_idle} idle cycles with SWDIO low expected at rise {pos}")
            continue
        pos += n_idle
        head = states[pos:pos + 8]
        if len(head) != 8 or any(s == PZ or s & PZ for s in head):
            problems.append(f"{label}: header bits not driven")
            continue
        pos += 8
        r0 = pos                                     # index of the turnaround rise R9
        want_z = 1 + 3 + (32 + 1 + 1 if kind == "ok" else 1)
        if states[r0:r0 + want_z] != [PZ] * want_z:
            problems.append(f"{label}: SWDIO not released at the {want_z} target/turnaround rises")
            continue
        rest = states[r0 + want_z:]
        want_idle = [P0] * n_idle if kind == "ok" else []
        if rest != want_idle:
            problems.append(f"{label}: after the transaction {rest[:10]} instead of {len(want_idle)} idle rises")
            continue
        want_samples = [rises[r0 + k][0] for k in range(1, 1 + 3 + (33 if kind == "ok" else 0))]
        if smp != want_samples:
            problems.append(f"{label}: samples at {smp[:6]}... not at the ACK/data/parity rises")
            continue
        seen.add(kind + ("-connect" if has_connect else "-request"))
    ok = not problems and {"connect", "ok-connect", "ok-request", "ack-connect", "ack-request", "fault82"} <= seen
    out.append(C.check(
        "swd-sequence", "PASS" if ok else "FAIL",
        (f"SWDIO at every SWCLK rise, all {len(variants)} request paths: connect = {n_high} x 1, 0xE79E LSB "
         f"first, {n_high2} x 1 (ADIv5.1 Supplement 6.2.1 and 8.3.6: at least 50 x 1, then at least one "
         f"idle cycle); every request: {n_idle} idle rises with SWDIO low, 8 driven header bits, then SWDIO "
         "released at the turnaround rise, the 3 ACK rises and, after ACK OK, the 32 data rises, the parity "
         f"rise and the second turnaround rise, then {n_idle} idle rises with SWDIO driven low. Samples are "
         "taken exactly at the ACK, data and parity rises (36; 3 when the ACK is not OK). A header with "
         "RnW = 0 faults 82 with no SWCLK edge")
        if ok else "; ".join(problems[:4]) or f"paths seen: {sorted(seen)}"))

    # SWCLK: every high phase lasts exactly the declared half period, every low phase at least that.
    fall = C.pad_is(clk, after=P0, before=P1)
    highs = sorted({(r["min"], r["max"]) for r in ctx.distances("rise", C.pad_is(clk, after=P1), "fall", fall)
                    if r["min"] != INF})
    lows = [r["min"] for r in ctx.distances("fall", fall, "rise", C.pad_is(clk, after=P1)) if r["min"] != INF]
    ok = highs == [(half, half)] and min(lows, default=0) >= half
    out.append(C.check("swd-clock", "PASS" if ok else "FAIL",
                       f"SWCLK high phases {highs} clocks, low phases at least {min(lows, default=0)} clocks "
                       f"(declared half period {half}; a low phase between two XFERs is longer)"))

    # Host drive: SWDIO changes only while SWCLK is low, at least a half period from any rise.
    dio_changes = lambda e: (e[EK] == "pad" and e[EPIN] == dio and not e[ECAUSE].startswith("fault")
                             and e[ECAUSE] != "HALT")
    rise = C.pad_is(clk, after=P1)
    high = [e for n in an.iter_nodes() for v in n.variants for e in v.events
            if dio_changes(e) and ctx.pads_at(e)[clk] != P0]
    setup = min((r["min"] for r in ctx.distances("dio", dio_changes, "rise", rise)), default=INF)
    hold = min((r["min"] for r in ctx.distances("rise", rise, "dio", dio_changes)), default=INF)
    ok = not high and setup >= half and hold >= half
    out.append(C.check("swd-host-drive", "PASS" if ok else "FAIL",
                       f"every host SWDIO change (drive, release, re-drive) happens while SWCLK is low; set-up to "
                       f"the next SWCLK rise >= {C.fc(setup)} clocks, hold after a rise >= {C.fc(hold)} clocks "
                       f"(half period {half}); Park is driven before the release (ADIv5.1 Supplement erratum 2.6)"
                       if ok else f"{len(high)} SWDIO changes while SWCLK is not low; set-up {C.fc(setup)}, "
                       f"hold {C.fc(hold)} (half period {half})"))

    # Target output budget: a sample at edge s reads the pad of edge s-2; the target launched at the previous rise.
    gaps = []
    for kind, node, vi, v, rises, smp in variants:
        rt = [t for t, _ in rises]
        for s in smp:
            prev = max((t for t in rt if t < s), default=None)
            if prev is not None:
                gaps.append(s - prev)
    gap = min(gaps, default=0)
    budget = gap - SYNC
    out.append(C.check("swd-target-budget", "PASS" if gaps and gap == 2 * half and budget > 0 else "FAIL",
                       f"each ACK, data or parity sample is taken at a SWCLK rise at least {gap} clocks after the "
                       f"rise on which the target launched the bit, from the pad value {SYNC} clocks earlier: the "
                       f"target's output delay plus board delay must stay below {budget} clocks "
                       f"({C.fmt_ns(budget / p.clock_hz)} at {p.clock_hz / 1e6:g} MHz)",
                       clocks=C.clock_rows(budget, clocks)))

    # Clocking after the data phase (ADIv5.1 Supplement 8.2): >= 8 rises after the data parity bit.
    after = []
    for kind, node, vi, v, rises, smp in variants:
        if kind == "ok" and smp:
            after.append(sum(1 for t, _ in rises if t > smp[-1]))
    ok = bool(after) and min(after) >= 8
    out.append(C.check("swd-clock-after-data", "PASS" if ok else "FAIL",
                       f"{min(after, default=0)} SWCLK rises after the data parity bit before SWCLK stops (one "
                       f"turnaround and {n_idle} idle cycles); ADIv5.1 Supplement 8.2 asks for at least 8"))

    # Holding points: SWCLK low; SWDIO driven high while waiting for a request (8.3.2: line held high).
    pulls = nodes_of(an, T.PULL)
    ok = bool(pulls) and all(an.pads(n.state)[clk] == P0 and an.pads(n.state)[dio] == P1 for n in pulls)
    out.append(C.check("swd-idle-line", "PASS" if ok else "FAIL",
                       "SWCLK low and SWDIO driven high at every PULL holding point "
                       "(ADIv5.1 Supplement 8.3.2: the host holds the line high between uses)"))
    out.append(C.check("swd-rate", "INFO",
                       f"SWCLK = clock/{2 * half}: " + ", ".join(f"{f / 1e6:g}MHz {f / (2 * half) / 1e6:.4g} MHz"
                                                                   for f in clocks)
                       + f"; SWCLK stretches between instructions (ADIv5 has no minimum SWCLK frequency); "
                       f"longest request path {max((v.t_end for n in pulls for v in n.variants), default=0)} "
                       "clocks"))
    return out


# ----------------------------------------------------------------------------
# WS2812B
# ----------------------------------------------------------------------------

def ws2812_checks(ctx: Any, clocks: list[float]) -> list[dict]:
    C = _c()
    an, p = ctx.an, ctx.p
    name = "ws2812b-v5" if p.name == "ws2812b-v5" else "ws2812"
    pin = ROLES[name]["dout"]
    table = WS2812B_V5 if "WS2812B-V5" in p.notes[0] else WS2812B
    m = next((re.search(r"T0H (\d+), T1H (\d+), T0L (\d+), T1L (\d+) clocks", n) for n in p.notes
              if re.search(r"T0H (\d+), T1H (\d+), T0L (\d+), T1L (\d+) clocks", n)), None)
    period, _ = C.parse_int(r"bit period (\d+) clocks", p.notes)
    r = next((re.search(r"Reset low time: (\d+) clocks .* after START and (\d+) clocks", n) for n in p.notes
              if re.search(r"Reset low time: (\d+) clocks .* after START and (\d+) clocks", n)), None)
    if m is None or period is None or r is None:
        return [C.check("ws2812-declaration", "FAIL", "notes must declare 'T0H a, T1H b, T0L c, T1L d clocks', "
                        "'bit period N clocks' and 'Reset low time: N clocks ... after START and M clocks'")]
    t0h, t1h, t0l, t1l = (int(g) for g in m.groups())
    rst_start, rst_latch = int(r.group(1)), int(r.group(2))
    out: list[dict] = []
    rise = C.pad_is(pin, after=P1)
    q = ctx.query("ws-rise", rise)
    highs0, highs1, lows0, lows1, resets, counts, periods = set(), set(), set(), set(), set(), set(), set()
    for node in nodes_of(an, T.PULL):
        for vi, v in enumerate(node.variants):
            ev = [(ei, e) for ei, e in enumerate(v.events) if e[EK] == "pad" and e[EPIN] == pin]
            rises = [(ei, e) for ei, e in ev if e[EAFTER] == P1]
            outs = [(ei, e) for ei, e in ev if e[ECAUSE] == "OUT"]
            lows = [(ei, e) for ei, e in ev if e[ECAUSE] == "SET" and e[EAFTER] == P0]
            counts.add((len(rises), len(outs), len(lows)))
            for (_, a), (_, b) in zip(rises, rises[1:]):
                periods.add(b[ET] - a[ET])
            for (_, a), (_, o), (_, l) in zip(rises, outs, lows):
                highs0.add(o[ET] - a[ET])
                highs1.add(l[ET] - a[ET])
            for group, target in ((outs, lows0), (lows, lows1)):
                for ei, e in group:
                    mn, _, _ = q.after(node, vi, ei)
                    (target if mn < 5e-6 * p.clock_hz else resets).add(mn)
    start = an.start_node()
    first_low = [(ei, e) for ei, e in enumerate(start.variants[0].events)
                 if e[EK] == "pad" and e[EPIN] == pin and e[EAFTER] == P0]
    start_reset = q.after(start, 0, first_low[0][0])[0] if first_low else INF
    exact = (highs0 == {t0h} and highs1 == {t1h} and lows0 == {t0l} and lows1 == {t1l}
             and periods == {period} and t0h + t0l == period == t1h + t1l and counts == {(24, 24, 24)})
    out.append(C.check("ws2812-bit-schedule", "PASS" if exact else "FAIL",
                       f"every pixel: 24 bits, MSB first (OUT c=1); high {t0h} clocks then the bit (OUT), low at "
                       f"{t1h}; next bit {period} clocks after the previous one, also across the pixel boundary "
                       f"when the next TX word is queued: T0H {sorted(highs0)}, T1H {sorted(highs1)}, T0L "
                       f"{sorted(lows0)}, T1L {sorted(lows1)}, bit period {sorted(periods)} (declared T0H {t0h}, "
                       f"T1H {t1h}, T0L {t0l}, T1L {t1l}, period {period}); rises/OUTs/falls per pixel "
                       f"{sorted(counts)}"))
    rows = [judge("T0H", t0h, table["T0H"], p.clock_hz), judge("T1H", t1h, table["T1H"], p.clock_hz),
            judge("T0L", t0l, table["T0L"], p.clock_hz), judge("T1L", t1l, table["T1L"], p.clock_hz)]
    if "TH+TL" in table:
        rows.append(judge("TH+TL", period, table["TH+TL"], p.clock_hz))
    out.append(rows_check(C, "ws2812-datasheet-bits", rows, p.clock_hz, table["name"]))
    res = sorted(resets | {start_reset})
    rows = [judge("reset after START", start_reset, table["RES"], p.clock_hz),
            judge("reset after a latch word", min(resets, default=0), table["RES"], p.clock_hz)]
    ok_decl = start_reset == rst_start and min(resets, default=0) == rst_latch
    c = rows_check(C, "ws2812-reset", rows, p.clock_hz, table["name"],
                   f"low times ending a frame {res} clocks (declared {rst_start} after START, {rst_latch} after "
                   "a latch word); DOUT is low at the PULL holding point, so a TX FIFO underrun also latches "
                   f"once the stall exceeds {int(table['RES'][0] * p.clock_hz) - min(t0l, t1l)} clocks")
    if not ok_decl:
        c["status"] = "FAIL"
    out.append(c)
    hold = [T.node_label(an, n) for n in nodes_of(an, T.PULL) if an.pads(n.state)[pin] != P0]
    slack = int(table["T0L"][1] * p.clock_hz) - t0l, int(table["T1L"][1] * p.clock_hz) - t1l
    out.append(C.check("ws2812-underrun", "PASS" if not hold else "FAIL",
                       f"DOUT is driven low at every PULL holding point. A PULL stall of s clocks between pixels "
                       f"lengthens the last bit's low time by s: T0L stays within {table['name']} for s <= "
                       f"{slack[0]}, T1L for s <= {slack[1]}; one TX word is consumed every {24 * period} clocks "
                       f"({C.fmt_ns(24 * period / p.clock_hz)} at {p.clock_hz / 1e6:g} MHz)"
                       if not hold else f"DOUT not low at {hold}"))
    return out


# ----------------------------------------------------------------------------
# PS/2 device to host
# ----------------------------------------------------------------------------

def ps2_checks(ctx: Any, clocks: list[float]) -> list[dict]:
    C = _c()
    an, p = ctx.an, ctx.p
    clk, dat = ROLES["ps2-device"]["clk"], ROLES["ps2-device"]["data"]
    decl = {k: C.parse_int(pat, p.notes)[0] for k, pat in (
        ("data_to_fall", r"DATA change to CLK fall (\d+) clocks"), ("low", r"CLK low (\d+) clocks"),
        ("rise_to_data", r"CLK rise to DATA change (\d+) clocks"), ("high", r"CLK high (\d+) clocks"))}
    m = next((re.search(r"then (\d+) CLK samples (\d+) clocks apart", n) for n in p.notes
              if re.search(r"then (\d+) CLK samples (\d+) clocks apart", n)), None)
    if None in decl.values() or m is None:
        return [C.check("ps2-declaration", "FAIL", "notes must declare the four frame timings and "
                        "'then N CLK samples M clocks apart'")]
    n_samp, spacing = int(m.group(1)), int(m.group(2))
    out: list[dict] = []
    waits = [n for n in nodes_of(an, T.WAITPIN) if an.p.ins[n.bpc].a == dat]
    starts = [(n, vi) for n in waits for vi, v in enumerate(n.variants)
              if v.kind == "boundary" and an.nodes[v.succ].kind == T.BLOCK_BRANCH]

    def fall_through(node: T.Node) -> int | None:
        if node.kind != T.BLOCK_BRANCH:
            return None
        want = node.bpc + 1
        return next((i for i, v in enumerate(node.variants) if first_pc(v) == want), None)

    if not starts:
        return [C.check("ps2-frame", "FAIL", "no path from the idle check into the frame")]
    path, nodes = walk(an, starts[0][0], starts[0][1], fall_through)
    falls = [t for t, e in pad_events(path, clk) if e[EAFTER] == P0]
    rels = [t for t, e in pad_events(path, clk) if e[EAFTER] == PZ and e[EBEFORE] == P0]
    outs = [t for t, e in pad_events(path, dat) if e[ECAUSE] == "OUT"]
    smp = samples(path, clk)
    idle = [s for s in smp if s < outs[0]] if outs else []
    checks_ = [s for s in smp if outs and s > outs[0]]
    ok = len(falls) == len(rels) == len(outs) == 11 and all(o < f < r for o, f, r in zip(outs, falls, rels))
    out.append(C.check("ps2-frame", "PASS" if ok else "FAIL",
                       f"one frame without inhibit ({len(nodes)} segments joined at zero stall): {len(outs)} DATA "
                       f"changes (OUT, LSB first), each followed by a CLK fall and a CLK release: "
                       f"{len(falls)} clock pulses. Start, parity and stop values are computed from the TX byte "
                       "(checked at the pins by test/test_protocols_ext.py)"))
    if not ok:
        return out
    q = {"data_to_fall": {f - o for o, f in zip(outs, falls)}, "low": {r - f for f, r in zip(falls, rels)},
         "high": {f - r for r, f in zip(rels, falls[1:])}, "rise_to_data": {o - r for r, o in zip(rels, outs[1:])},
         "period": {b - a for a, b in zip(falls, falls[1:])}}
    exact = all(q[k] == {decl[k]} for k in decl) and len(q["period"]) == 1
    out.append(C.check("ps2-frame-timing", "PASS" if exact else "FAIL",
                       "inside a frame, exact in every bit: " + ", ".join(f"{k} {sorted(v)}" for k, v in q.items())
                       + " clocks; declared " + ", ".join(f"{k} {v}" for k, v in decl.items())))
    per = next(iter(q["period"]))
    rows = [judge("CLK low", decl["low"], PS2["clk_low"], p.clock_hz),
            judge("CLK high", decl["high"], PS2["clk_high"], p.clock_hz),
            judge("CLK period", per, PS2["clk_period"], p.clock_hz),
            judge("CLK rise to DATA change", decl["rise_to_data"], PS2["rise_to_data"], p.clock_hz),
            judge("DATA change to CLK fall", decl["data_to_fall"], PS2["data_to_fall"], p.clock_hz)]
    out.append(rows_check(C, "ps2-spec-timing", rows, p.clock_hz, "PS/2 device-to-host limits (Chapweske)"))
    gaps = {b - a for a, b in zip(idle, idle[1:])}
    span = idle[-1] - idle[0] if idle else 0
    lead = outs[0] - idle[-1] if idle else INF
    ok = len(idle) == n_samp and gaps == {spacing}
    row = judge("CLK sampled high", span, PS2["idle_before"], p.clock_hz)
    c = rows_check(C, "ps2-idle-before-frame", [row], p.clock_hz, "PS/2 device-to-host limits (Chapweske)",
                   f"{len(idle)} CLK samples {sorted(gaps)} clocks apart after WAITPIN CLK and WAITPIN DATA, all "
                   f"high, span {span} clocks; the start bit follows the last sample by {lead} clocks. Between "
                   f"samples the line is not observed: a low pulse shorter than {spacing} clocks can be missed "
                   "(a host inhibit lasts at least 100 us)")
    if not ok:
        c["status"] = "FAIL"
    out.append(c)
    before = [min((f - s for s in checks_ if s < f), default=INF) for f in falls]
    after11 = [s for s in checks_ if s > falls[-1]]
    ok = len(checks_) == 11 and all(b == before[0] for b in before) and not after11
    # Abort paths: every soft branch node's taken variant releases both lines before waiting.
    aborts = []
    for n in an.iter_nodes():
        if n.kind != T.BLOCK_BRANCH:
            continue
        for v in n.variants:
            if first_pc(v) == n.bpc + 1 or v.kind != "boundary":
                continue
            succ = an.nodes[v.succ]
            falls_ab = [e for e in v.events if e[EK] == "pad" and e[EPIN] == clk and e[EAFTER] == P0]
            aborts.append((T.node_label(an, n), an.pads(succ.state)[clk], an.pads(succ.state)[dat],
                           falls_ab, an.p.ins[succ.bpc].op, v.t_end))
    ab_ok = aborts and all(c_ == PZ and d == PZ and not fl and op == T.WAITPIN for _, c_, d, fl, op, _ in aborts)
    limit = max((n.state.limit for n in waits), default=0)
    out.append(C.check("ps2-inhibit", "PASS" if ok and ab_ok else "FAIL",
                       f"CLK is sampled {before[0]} clocks before each of the 11 CLK falls (the pad value "
                       f"{before[0] + SYNC} clocks before the fall) and not after the 11th; a low sample branches "
                       f"to the retry path, which releases CLK and DATA and waits for the idle bus "
                       f"({len(aborts)} abort paths, WAITPIN reached {max((a[5] for a in aborts), default=0)} "
                       f"clocks later at most, no CLK fall on the way). The idle waits are bounded by LIMIT {limit} "
                       f"({ns(limit / p.clock_hz)} at {p.clock_hz / 1e6:g} MHz), then fault 3"
                       if ok and ab_ok else f"checks before falls {before}, after the 11th {after11}, aborts {aborts}"))
    hold = [T.node_label(an, n) for n in nodes_of(an, T.PULL)
            if an.pads(n.state)[clk] != PZ or an.pads(n.state)[dat] != PZ]
    bad = sorted({e[EAFTER] for n in an.iter_nodes() for v in n.variants for e in v.events
                  if e[EK] == "pad" and e[EPIN] in (clk, dat) and e[EAFTER] & P1})
    out.append(C.check("ps2-open-drain", "PASS" if not hold and not bad else "FAIL",
                       "CLK and DATA are only pulled low or released (no pad state drives high) and both are "
                       "released at the PULL holding point" if not hold and not bad else f"{hold} {bad}"))
    return out


# ----------------------------------------------------------------------------
# 1-Wire
# ----------------------------------------------------------------------------

def onewire_checks(ctx: Any, clocks: list[float]) -> list[dict]:
    C = _c()
    an, p = ctx.an, ctx.p
    dq = ROLES["onewire-master"]["dq"]
    f = p.clock_hz
    decl = {k: C.parse_int(pat, p.notes)[0] for k, pat in (
        ("reset_low", r"DQ low (\d+) clocks"), ("presence", r"presence sampled (\d+) clocks"),
        ("reset_high", r"first slot (\d+) clocks"), ("release1", r"a 1 bit releases DQ at (\d+) clocks"),
        ("sample", r"DQ sampled (\d+) clocks"), ("release0", r"a 0 bit releases DQ at (\d+) clocks"),
        ("slot", r"next slot at (\d+) clocks"))}
    if None in decl.values():
        return [C.check("onewire-declaration", "FAIL", f"missing declarations: "
                        f"{[k for k, v in decl.items() if v is None]}")]
    out: list[dict] = []
    pull = nodes_of(an, T.PULL)[0]

    def to_push(node: T.Node) -> int | None:
        return 0 if node.kind == T.BLOCK_RX else None

    found = {}
    for vi, v in enumerate(pull.variants):
        if v.kind != "boundary":
            continue
        path, _ = walk(an, pull, vi, to_push)
        ev = pad_events(path, dq)
        falls = [t for t, e in ev if e[EAFTER] == P0]
        rels = [t for t, e in ev if e[EAFTER] == PZ and e[ECAUSE] == "SET"]
        ones = [t for t, e in ev if e[ECAUSE] == "OUT"]
        smp = samples(path, dq)
        found["reset" if falls and (min((r for r in rels if r > falls[0]), default=0) - falls[0]) > 10000
              else "touch"] = (falls, rels, ones, smp)
    if set(found) != {"reset", "touch"}:
        return [C.check("onewire-paths", "FAIL", f"expected a reset path and a byte-only path, found {sorted(found)}")]
    falls, rels, ones, smp = found["reset"]
    reset_low = rels[0] - falls[0]
    presence = smp[0] - rels[0]
    reset_high = falls[1] - rels[0]
    rows_reset = [judge("reset low (tRSTL)", reset_low, DS18B20["tRSTL"], f),
                  judge("presence sample, pad value", presence - SYNC, DS18B20["presence"], f),
                  judge("release to first slot (tRSTH)", reset_high, DS18B20["tRSTH"], f)]
    q = {}
    for name, (fl, rl, on, sm) in (("reset", (falls[1:], rels[1:], ones, smp[1:])), ("touch", found["touch"])):
        slots = []
        for k, t in enumerate(fl):
            nxt = fl[k + 1] if k + 1 < len(fl) else INF
            slots.append((min((o for o in on if t < o < nxt), default=INF) - t,
                          min((s for s in sm if t < s < nxt), default=INF) - t,
                          min((r for r in rl if t < r < nxt), default=INF) - t,
                          nxt - t))
        q[name] = slots
    all_slots = q["reset"] + q["touch"]
    rel1 = {s[0] for s in all_slots}
    smpl = {s[1] for s in all_slots}
    rel0 = {s[2] for s in all_slots}
    per = {s[3] for s in all_slots if s[3] != INF}
    exact = (len(q["reset"]) == len(q["touch"]) == 8 and rel1 == {decl["release1"]} and smpl == {decl["sample"]}
             and rel0 == {decl["release0"]} and per == {decl["slot"]} and reset_low == decl["reset_low"]
             and presence == decl["presence"] and reset_high == decl["reset_high"])
    out.append(C.check("onewire-schedule", "PASS" if exact else "FAIL",
                       f"reset path: DQ low {reset_low}, presence sample {presence} after release, first slot "
                       f"{reset_high} after release; 8 slots per byte on both paths: OUT (a 1 releases DQ) at "
                       f"{sorted(rel1)}, sample at {sorted(smpl)}, release at {sorted(rel0)}, next slot at "
                       f"{sorted(per)} clocks (declared {decl})"))
    out.append(rows_check(C, "onewire-reset", rows_reset, f, "DS18B20 limits",
                          f"AN126 Table 1 uses {ns(AN126['reset_low'])} low, samples {ns(AN126['presence_sample'])} "
                          f"after release and waits {ns(AN126['reset_high'])} in total; the presence window is where "
                          "every DS18B20 presence pulse (tPDHIGH 15-60 us, tPDLOW 60-240 us) holds DQ low"))
    s1, ss, s0, sp = decl["release1"], decl["sample"], decl["release0"], decl["slot"]
    rows = [judge("write-1 low (tLOW1)", s1, DS18B20["tLOW1"], f),
            judge("write-0 low (tLOW0)", s0, DS18B20["tLOW0"], f),
            judge("read sample, pad value (tRDV)", ss - SYNC, DS18B20["tRDV"], f),
            judge("time slot (tSLOT)", sp, DS18B20["tSLOT"], f),
            judge("recovery (tREC)", sp - s0, DS18B20["tREC"], f),
            judge("write-1 released before the slave's window", s1, (None, DS18B20["write_sample"][0]), f),
            judge("write-0 held through the slave's window", s0, (DS18B20["write_sample"][1], None), f)]
    out.append(rows_check(C, "onewire-slot", rows, f, "DS18B20 limits",
                          f"AN126 Table 1: write-1 low {ns(AN126['write1_low'])}, write-0 low "
                          f"{ns(AN126['write0_low'])}, sample {ns(AN126['read_sample'])} after the fall, slot "
                          f"{ns(AN126['slot'])}. Rise time after a release is not included"))
    rq = ctx.query("ow-fall", C.pad_is(dq, after=P0))
    gaps = []
    for n in nodes_of(an, T.PUSH) + nodes_of(an, T.PULL):
        for vi, v in enumerate(n.variants):
            for ei, e in enumerate(v.events):
                if e[EK] == "pad" and e[EPIN] == dq and e[EAFTER] == PZ and e[ECAUSE] == "SET":
                    gaps.append(rq.after(n, vi, ei)[0])
    between = min((g for g in gaps if g != INF), default=INF)
    od = sorted({e[EAFTER] for n in an.iter_nodes() for v in n.variants for e in v.events
                 if e[EK] == "pad" and e[EPIN] == dq and e[EAFTER] & P1})
    hold = [T.node_label(an, n) for n in an.iter_nodes() if n.kind in (T.BLOCK_TX, T.BLOCK_RX)
            and an.pads(n.state)[dq] != PZ]
    ok = not od and not hold and between >= DS18B20["tREC"][0] * f
    out.append(C.check("onewire-open-drain", "PASS" if ok else "FAIL",
                       f"DQ is only pulled low or released, released at every PULL/PUSH holding point and by "
                       f"fault 96; the shortest recovery from a release to the next fall, across bytes, is "
                       f"{C.fc(between)} clocks ({ns(between / f)}; tREC >= 1 us); 1-Wire sets no maximum time "
                       "between slots" if ok else f"drives high {od}, holding {hold}, recovery {between}"))
    return out


def ps2_host_checks(ctx: Any, clocks: list[float]) -> list[dict]:
    C = _c()
    an, p = ctx.an, ctx.p
    clk, dat = ROLES["ps2-host"]["clk"], ROLES["ps2-host"]["data"]
    f = p.clock_hz
    m = next((re.search(r"CLK low (\d+) clocks .* then DATA low, then CLK released (\d+) clocks", n) for n in p.notes
              if re.search(r"CLK low (\d+) clocks .* then DATA low, then CLK released (\d+) clocks", n)), None)
    t_a, _ = C.parse_int(r"during the command is bounded by LIMIT (\d+)", p.notes)
    t_r, _ = C.parse_int(r"while reading responses by LIMIT (\d+)", p.notes)
    if m is None or None in (t_a, t_r):
        return [C.check("ps2-host-declaration", "FAIL", "notes must declare 'CLK low N clocks ... then DATA low, "
                        "then CLK released M clocks' and the two LIMIT bounds")]
    inh, rts = int(m.group(1)), int(m.group(2))
    out: list[dict] = []
    pull = nodes_of(an, T.PULL)
    reqs = set()
    for n in pull:
        for v in n.variants:
            ev = [(e[ET], e[EPIN], e[EBEFORE], e[EAFTER]) for e in v.events if e[EK] == "pad"]
            c_low = [t for t, q, b, a in ev if q == clk and a == P0]
            d_low = [t for t, q, b, a in ev if q == dat and a == P0]
            c_rel = [t for t, q, b, a in ev if q == clk and a == PZ]
            if c_low and d_low and c_rel:
                reqs.add((d_low[0] - c_low[0], c_rel[0] - d_low[0]))
    ok = reqs == {(inh, rts)}
    first = [n for n in nodes_of(an, T.WAITPIN) if an.p.ins[n.bpc].a == clk and an.p.ins[n.bpc].b == 0
             and n.state.limit == t_a]
    rows = [judge("CLK low before DATA low", inh, PS2["inhibit"], f),
            judge("CLK low to the host's first-clock timeout", inh + rts + t_a, PS2["first_clock"], f)]
    c = rows_check(C, "ps2-host-request", rows, f, "PS/2 host-to-device limits (Chapweske)",
                   f"request-to-send schedule (CLK low to DATA low, DATA low to CLK release) {sorted(reqs)} clocks "
                   f"(declared {inh}, {rts}): CLK low, then DATA low, then CLK released. The host waits for each "
                   f"device clock for up to LIMIT {t_a} clocks, so a device that starts clocking within the 15 ms "
                   "allowed after CLK goes low is never timed out")
    if not ok or not first:
        c["status"] = "FAIL"
    out.append(c)
    # Bits: each OUT on DATA one clock after a WAITPIN CLK==0, before the next WAITPIN CLK==1.
    sends, recvs, bad = set(), set(), []
    for n in nodes_of(an, T.WAITPIN):
        ins = an.p.ins[n.bpc]
        if (ins.a, ins.b) != (clk, 0):
            continue
        for v in n.variants:
            outs = [e[ET] for e in v.events if e[EK] == "pad" and e[EPIN] == dat and e[ECAUSE] == "OUT"]
            smp = [e[ET] for e in v.events if e[EK] == "sample" and e[EPIN] == dat]
            nxt = an.p.ins[v.end[2]] if v.kind == "boundary" else None
            if outs or smp:
                if not (nxt is not None and nxt.op == T.WAITPIN and (nxt.a, nxt.b) == (clk, 1)) \
                        or (outs + smp) != [1]:
                    bad.append(T.node_label(an, n))
                (sends if outs else recvs).add(n.state.repeat)
    ok = not bad and sends == set(range(10)) and recvs == set(range(11))
    out.append(C.check("ps2-host-bits", "PASS" if ok else "FAIL",
                       f"command: {len(sends)} bits (8 data LSB first, parity, stop), each put on DATA one clock "
                       f"after WAITPIN sees the device's CLK low and held until WAITPIN sees CLK high (the device "
                       f"reads it on the rising edge); response: {len(recvs)} DATA samples per frame, each one clock "
                       f"after WAITPIN sees CLK low, from the pad value {SYNC - 1} to {SYNC} clocks after the CLK fall"
                       if ok else f"bits {sorted(sends)}, samples {sorted(recvs)}, bad {bad}"))
    waits = {n.bpc: an.p.ins[n.bpc] for n in nodes_of(an, T.WAITPIN)}
    chain = [(waits[pc].a, waits[pc].b) for pc in sorted(waits)]
    want = [(clk, 1), (clk, 0), (clk, 1), (dat, 0), (clk, 0), (clk, 1), (dat, 1), (clk, 0), (clk, 1)]
    hold = [T.node_label(an, n) for n in pull if an.pads(n.state)[clk] != PZ or an.pads(n.state)[dat] != PZ]
    limits = sorted({n.state.limit for n in nodes_of(an, T.WAITPIN)})
    rows = [judge("response wait", t_r, PS2["response"], f)]
    c = rows_check(C, "ps2-host-handshake", rows, f, "PS/2 host-to-device limits (Chapweske)",
                   f"WAITPIN sequence {chain} (CLK seen released, bit clocking, acknowledge DATA low, the "
                   f"acknowledge clock, DATA released, response clocking); LIMIT values {limits}; both lines "
                   "released at every PULL holding point")
    if chain != want or hold or limits != sorted({t_a, t_r}):
        c["status"] = "FAIL"
    out.append(c)
    bad_od = sorted({e[EAFTER] for n in an.iter_nodes() for v in n.variants for e in v.events
                     if e[EK] == "pad" and e[EPIN] in (clk, dat) and e[EAFTER] & P1})
    out.append(C.check("ps2-host-open-drain", "PASS" if not bad_od else "FAIL",
                       "CLK and DATA are only pulled low or released" if not bad_od else f"{bad_od}"))
    return out


FAMILY_CHECKS: dict[str, Callable] = {
    "swd-read": swd_checks, "ws2812": ws2812_checks, "ws2812b-v5": ws2812_checks,
    "ps2-device": ps2_checks, "ps2-host": ps2_host_checks, "onewire-master": onewire_checks,
}
