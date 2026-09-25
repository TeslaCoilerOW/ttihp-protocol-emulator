# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Extended (cross) functional coverage for the random campaign (VCAMP_XCOV=1).

``random_gen.Coverage`` counts per-opcode completions, stalls, faults, XFER
modes, mover transfers, host commands and host traffic. This observer adds
interaction bins it does not track: same-edge collisions between the host, the
mover (DMA) and engines on one FIFO, mover arbitration and blocking reasons,
input-trigger modes, simultaneous event delivery and consumption, synchronous
multi-engine start, STOP/BEGIN/reset of busy engines, FIFO-full levels,
concurrency, open-drain behaviour and branch outcomes. Bins are computed from
the reference model's state around each tick (the DUT is lockstep-equal on
every public pin, so the bins describe the stimulus both saw). The observer
only reads state; an internal error is counted as a bin, never raised.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

UI_WVALID, UI_RREADY = 16, 32
UO_WREADY, UO_RVALID = 16, 32
TRIGGER_MODES = ("rise", "fall", "high", "low")


def all_bins(engines: int = 4) -> list[str]:
    """Every bin this observer can produce (the hole list is the complement of what was hit)."""
    bins = []
    for i in range(engines):
        bins += [f"TX FIFO full e{i}", f"RX FIFO full e{i}"]
    bins += [
        "host TX write + engine PULL on same FIFO, same edge",
        "host RX pop + engine PUSH on same FIFO, same edge",
        "mover moved a word",
        "mover route to self moved a word",
        "mover route exhausted (count reached 0)",
        "mover pop + engine PUSH on same source, same edge",
        "mover push + engine PULL on same dest, same edge",
        "mover push into FIFO the host writes next edge (dest == selected, window 2)",
        "mover blocked: dest TX FIFO full",
        "mover blocked: source RX head reserved by host read",
        "mover blocked: host TX write to dest, same edge",
        "mover arbitration: >=2 routes eligible",
        "mover arbitration: 4 routes configured",
        "FLUSH cleared an active route",
        "ROUTE edited an active route",
        "EVENT command and SIGNAL on same edge",
        "WAITEVENT consumed event with simultaneous new delivery",
        "trigger delivered to engine blocked in WAITEVENT",
        "synchronous start of 2 engines", "synchronous start of 3 engines", "synchronous start of 4 engines",
        "STOP/BEGIN of engine inside XFER", "STOP/BEGIN of engine inside WAIT",
        "STOP/BEGIN of engine stalled on PULL/PUSH/WAITPIN/WAITEVENT",
        "reset/deselect with engines running", "reset/deselect inside XFER",
        "HALT with output enables active",
        "fault with output enables active",
        "strict PUSH overflow (fault 4) while host holds that RX head",
        "WAITPIN/WAITEVENT timeout with LIMIT 1",
        "engines running: 0", "engines running: 1", "engines running: 2", "engines running: 3",
        "engines running: 4",
        "engines in XFER: 2", "engines in XFER: 3", "engines in XFER: 4",
        "open-drain pin pulled low", "open-drain pin released (logical 1)",
        "push-pull pin driven high", "push-pull pin driven low",
        "host fault flag set", "host fault flag cleared",
        "IRQ from event only", "IRQ from RX data only", "IRQ from event and RX data",
        "host RX read abandoned mid-word (window change)",
        "XFER issued with half-period 1", "XFER issued with half-period >= 4",
        "WAIT >= 20 cycles issued",
        "LOOP taken", "LOOP fell through", "JZ taken", "JZ not taken",
        "WAITPIN satisfied on issue", "WAITPIN satisfied after blocking",
        "WAITEVENT satisfied on issue", "WAITEVENT satisfied after blocking",
    ]
    bins += [f"trigger mode {m} ({name}) detected" for m, name in enumerate(TRIGGER_MODES)]
    return bins


class ExtendedCoverage:
    """Observer (before/after around Reference.tick). ``coverage`` is the random_gen.Coverage
    attached for the current case; its ``_accepted`` list names the host commands accepted on
    this edge."""

    def __init__(self, coverage: Any) -> None:
        self.coverage = coverage
        self.bins: Counter[str] = Counter()
        self._pre: dict | None = None

    def before(self, h: Any, ui: int, pins: int) -> None:
        try:
            m = h.model
            self._pre = {
                "ui": ui, "out": m.outputs(ui), "window": m.window, "selected": m.selected,
                "write_index": m.write_index, "write_word": m.write_word, "read_index": m.read_index,
                "read_word": m.read_word, "read_engine": m.read_engine, "routes": list(m.routes),
                "host_fault": m.host_fault, "sync2": m.sync2, "previous": m.previous_pins,
                "engines": [{"running": e.running, "fault": e.fault, "wait": e.wait,
                             "xfer": e.transfer is not None, "completed": e.completed, "tx": len(e.tx),
                             "rx": len(e.rx), "event": e.event, "trigger": e.trigger, "stalled": e.stalled,
                             "oe": e.direction & e.ownership, "repeat": e.repeat, "regs": list(e.regs),
                             "blocked": e.blocked, "limit": e.limit,
                             "word": e.program[e.pc] if 0 <= e.pc < len(e.program) else None}
                            for e in m.engines],
            }
        except Exception as exc:  # noqa: BLE001
            self.bins[f"xcov-error {type(exc).__name__}"] += 1
            self._pre = None

    def after(self, h: Any) -> None:
        pre, self._pre = self._pre, None
        if pre is None:
            return
        try:
            self._after(h, pre)
        except Exception as exc:  # noqa: BLE001
            self.bins[f"xcov-error {type(exc).__name__}"] += 1

    def _after(self, h: Any, pre: dict) -> None:  # noqa: C901, PLR0912, PLR0915
        m, b = h.model, self.bins
        fifo, engines, pe = m.config.fifo_words, m.engines, pre["engines"]
        if m.timestamp == 0:  # reset or deselect edge
            if any(p["running"] for p in pe):
                b["reset/deselect with engines running"] += 1
            if any(p["xfer"] for p in pe):
                b["reset/deselect inside XFER"] += 1
            return
        ui, out = pre["ui"], pre["out"]
        window = ui >> 6 & 3
        changed = window != pre["window"]
        write_done = bool(not changed and ui & UI_WVALID and out.uo & UO_WREADY and pre["write_index"] == 7)
        host_tx = pre["selected"] if write_done and window == 2 else None
        read_done = bool(not changed and ui & UI_RREADY and out.uo & UO_RVALID and pre["read_index"] == 7)
        host_rx = pre["read_engine"] if read_done else None
        command = pre["write_word"] | (ui & 15) << 28 if write_done and window == 0 else None
        accepted = set(getattr(self.coverage, "_accepted", []) or [])
        cmd_op = command >> 24 if command is not None and command >> 24 in accepted else None
        # Per-engine outcomes.
        issued: dict[int, int] = {}  # engine -> opcode completed (non-XFER-transition) on this edge
        for i, (p, e) in enumerate(zip(pe, engines, strict=True)):
            word = p["word"]
            op = None if word is None else word >> 24
            active = p["running"] and not p["fault"] and not p["wait"] and not p["xfer"] and op is not None
            if active and e.completed != p["completed"]:
                issued[i] = op
                if op == 11:
                    b["LOOP taken" if p["repeat"] else "LOOP fell through"] += 1
                elif op == 26:
                    b["JZ taken" if p["regs"][word >> 16 & 3] == 0 else "JZ not taken"] += 1
                elif op in (13, 15):
                    name = "WAITPIN" if op == 13 else "WAITEVENT"
                    b[f"{name} satisfied after blocking" if p["blocked"] else f"{name} satisfied on issue"] += 1
                    if op == 15 and e.event:
                        b["WAITEVENT consumed event with simultaneous new delivery"] += 1
                elif op == 4 and (word & 0xFFFFFF) >= 20:
                    b["WAIT >= 20 cycles issued"] += 1
                elif op == 1 and p["oe"]:
                    b["HALT with output enables active"] += 1
            if active and op == 17 and e.transfer is not None:
                half = word >> 8 & 255
                if half == 1:
                    b["XFER issued with half-period 1"] += 1
                elif half >= 4:
                    b["XFER issued with half-period >= 4"] += 1
            if e.fault and not p["fault"]:
                if p["oe"]:
                    b["fault with output enables active"] += 1
                if e.fault == 3 and p["limit"] == 1:
                    b["WAITPIN/WAITEVENT timeout with LIMIT 1"] += 1
                if e.fault == 4 and pre["read_engine"] == i:
                    b["strict PUSH overflow (fault 4) while host holds that RX head"] += 1
            if p["running"] and not e.running and not e.fault and op != 1:
                if p["xfer"]:
                    b["STOP/BEGIN of engine inside XFER"] += 1
                elif p["wait"]:
                    b["STOP/BEGIN of engine inside WAIT"] += 1
                elif p["stalled"]:
                    b["STOP/BEGIN of engine stalled on PULL/PUSH/WAITPIN/WAITEVENT"] += 1
            if len(e.tx) == fifo:
                b[f"TX FIFO full e{i}"] += 1
            if len(e.rx) == fifo:
                b[f"RX FIFO full e{i}"] += 1
            if p["trigger"] is not None:
                pin, mode = p["trigger"]
                cur, prev = bool(pre["sync2"] >> pin & 1), bool(pre["previous"] >> pin & 1)
                if (cur and not prev, prev and not cur, cur, not cur)[mode]:
                    b[f"trigger mode {mode} ({TRIGGER_MODES[mode]}) detected"] += 1
                    if op == 15 and p["stalled"]:
                        b["trigger delivered to engine blocked in WAITEVENT"] += 1
        pulled = {i for i, op in issued.items() if op == 6}
        pushed = {i for i, op in issued.items() if op == 7}
        if host_tx is not None and host_tx in pulled:
            b["host TX write + engine PULL on same FIFO, same edge"] += 1
        if host_rx is not None and host_rx in pushed:
            b["host RX pop + engine PUSH on same FIFO, same edge"] += 1
        # Mover (DMA): a route's count decremented, or its last word consumed (not by a command).
        route_cmd = cmd_op in (6, 10)
        moved = None
        for s, route in enumerate(pre["routes"]):
            after = m.routes[s]
            if route is None:
                continue
            d, count = route
            if (count > 1 and after == (d, count - 1)) or (count == 1 and after is None and not route_cmd):
                moved = (s, d, count)
                break
        if moved is not None:
            s, d, count = moved
            b["mover moved a word"] += 1
            if s == d:
                b["mover route to self moved a word"] += 1
            if count == 1:
                b["mover route exhausted (count reached 0)"] += 1
            if s in pushed:
                b["mover pop + engine PUSH on same source, same edge"] += 1
            if d in pulled:
                b["mover push + engine PULL on same dest, same edge"] += 1
            if window == 2 and d == pre["selected"] and ui & UI_WVALID:
                b["mover push into FIFO the host writes next edge (dest == selected, window 2)"] += 1
        if sum(r is not None for r in pre["routes"]) == len(engines):
            b["mover arbitration: 4 routes configured"] += 1
        reserved = pre["selected"] if window == 3 and not changed else None
        eligible = 0
        edited_source = command & 3 if cmd_op == 6 else None
        for s, route in enumerate(pre["routes"]):
            if route is None or not pe[s]["rx"] or s == edited_source:
                continue
            d = route[0]
            if pe[d]["tx"] >= fifo:
                b["mover blocked: dest TX FIFO full"] += 1
            elif s == reserved:
                b["mover blocked: source RX head reserved by host read"] += 1
            elif host_tx is not None and host_tx == d:
                b["mover blocked: host TX write to dest, same edge"] += 1
            else:
                eligible += 1
        if eligible >= 2:
            b["mover arbitration: >=2 routes eligible"] += 1
        # Commands.
        starts = sum(1 for p, e in zip(pe, engines, strict=True) if not p["running"] and e.running)
        if starts >= 2:
            b[f"synchronous start of {starts} engines"] += 1
        if cmd_op == 10 and any(r is not None and a is None for r, a in zip(pre["routes"], m.routes, strict=True)):
            b["FLUSH cleared an active route"] += 1
        if cmd_op == 6 and pre["routes"][command & 3] is not None:
            b["ROUTE edited an active route"] += 1
        if cmd_op == 9 and 14 in issued.values():
            b["EVENT command and SIGNAL on same edge"] += 1
        # Flags, concurrency, pins.
        if m.host_fault and not pre["host_fault"]:
            b["host fault flag set"] += 1
        if pre["host_fault"] and not m.host_fault:
            b["host fault flag cleared"] += 1
        b[f"engines running: {sum(1 for e in engines if e.running)}"] += 1
        xfers = sum(1 for e in engines if e.transfer is not None)
        if xfers >= 2:
            b[f"engines in XFER: {xfers}"] += 1
        ev, rx = any(e.event for e in engines), any(e.rx for e in engines)
        if ev or rx:
            b["IRQ from event and RX data" if ev and rx else "IRQ from event only" if ev else "IRQ from RX data only"] += 1
        for e in engines:
            if not e.running or e.fault:
                continue
            enabled = e.direction & e.ownership
            od, pp = enabled & e.open_drain, enabled & ~e.open_drain
            if od & ~e.values:
                b["open-drain pin pulled low"] += 1
            if od & e.values:
                b["open-drain pin released (logical 1)"] += 1
            if pp & e.values:
                b["push-pull pin driven high"] += 1
            if pp & ~e.values & 0xFF:
                b["push-pull pin driven low"] += 1
        if changed and pre["window"] == 3 and pre["read_word"] is not None and pre["read_index"]:
            b["host RX read abandoned mid-word (window change)"] += 1
