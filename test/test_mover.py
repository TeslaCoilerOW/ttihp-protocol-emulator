# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Mover (DMA) arbitration: round robin among 2 to 4 eligible routes, host priority.

docs/isa.md: at most one DMA word moves per edge; among the routes that can
move a word (source RX non-empty and not reserved by a host read, destination
TX not full, no host TX word accepted for the same destination on that edge,
descriptor not edited on that edge), the grant goes to the first source at or
after the round-robin cursor, and the cursor then moves to the granted source
plus one. The random campaign saw two or more routes compete on the same edge
in 1.7 of 1,000 default cases, so the grant order and the host priority were
nearly unobserved (mutants 979 and 1008 and 25 more on the cursor survived).

Each phase routes tagged words from 2, 3 or 4 sources (the fourth one a
self-route) into one destination engine whose TX queue the host has filled, so
that every route becomes eligible on the same edge once the destination starts
pulling. The destination forwards each word to its RX queue (PULL, MOV, PUSH),
where the host reads them: the grant order becomes the order of the words on
the read nibbles, which the lockstep check compares with the model every
cycle. In the last phases the host also writes TX words to the destination
while the routes compete.

``ArbitrationMonitor`` restates the arbitration clause independently of the
model: before every edge it computes the eligible sources and the expected
grant from the pre-edge state, and after the edge it checks the model's grant
and cursor (the model is in turn checked against the DUT). It also counts the
edges with 2, 3 and 4 eligible routes and the host-priority collisions; the
test requires each of them to occur.
"""

from __future__ import annotations

import cocotb

from harness import (FLUSH, ROUTE, RS_COUNT, RS_LEVELS, SELECT, START, STOP, UI_WVALID, UO_WREADY, CocotbHarness,
                     Harness, immediate, instruction)

HALT, JMP, PULL, PUSH = 1, 5, 6, 7
MOV, LOAD, ADD = 18, 19, 20
TX, RX, X = 0, 1, 2
ALL = 0b1111


def tag(source: int, sequence: int) -> int:
    """16-bit word naming its source (bit 15 set, so routed data bit 15 is exercised)."""
    return 0xA000 | source << 8 | sequence


def pusher(source: int, words: int) -> list[int]:
    """Push ``words`` tagged words (blocking while RX is full), then HALT."""
    return ([instruction(LOAD, RX, tag(source, 0) >> 8, 0), instruction(LOAD, X, 0, 1)]
            + [instruction(PUSH), instruction(ADD, RX, X)] * words + [instruction(HALT)])


def streamer(source: int, words: int) -> list[int]:
    """Push ``words`` copies of one tagged word, one per cycle (blocking while RX is full)."""
    return [instruction(LOAD, RX, tag(source, 0) >> 8, 0)] + [instruction(PUSH)] * words + [instruction(HALT)]


FORWARDER = [instruction(PULL), instruction(MOV, RX, TX), instruction(PUSH), immediate(JMP, 0)]
PULLER = [instruction(PULL)] * 63 + [immediate(JMP, 0)]  # one PULL per cycle, words discarded


class ArbitrationMonitor:
    """Checks every DMA grant against the arbitration rule of docs/isa.md."""

    def __init__(self) -> None:
        self.checked = 0
        self.grants = 0
        self.contended = {2: 0, 3: 0, 4: 0}  # edges with at least n eligible routes
        self.host_priority = 0  # edges where a host TX word pre-empted an eligible route
        self._expect: tuple[list[int], int | None, list] | None = None

    def before(self, h: Harness, ui: int, pins: int) -> None:
        m = h.model
        self._expect = None
        if getattr(m, "settling", False) or (getattr(m, "clear_active", None) and m.clear_active(False)):
            return
        n, depth = m.config.engines, m.config.fifo_words
        window = ui >> 6 & 3
        changed = window != m.window
        pre = m.outputs(ui)
        completes = bool(ui & UI_WVALID and pre.uo & UO_WREADY and m.write_index == 7)
        if completes and window == 0 and (m.write_word | (ui & 15) << 28) >> 24 in (ROUTE, FLUSH):
            return  # descriptor edits and flushes inhibit grants; not checked here
        host_dest = m.selected if completes and window == 2 else None
        reserved = m.selected if window == 3 and not changed else None
        eligible, pre_empted = [], False
        for source, route in enumerate(m.routes):
            if route is None or not m.engines[source].rx or source == reserved:
                continue
            if len(m.engines[route[0]].tx) >= depth:
                continue
            if route[0] == host_dest:
                pre_empted = True
                continue
            eligible.append(source)
        grant = next((s for s in ((m.round_robin + i) % n for i in range(n)) if s in eligible), None)
        self._expect = (eligible, grant, list(m.routes))
        self.host_priority += pre_empted
        for count in self.contended:
            self.contended[count] += len(eligible) >= count

    def after(self, h: Harness) -> None:
        if self._expect is None or h.model.timestamp == 0:  # nothing to check, or a reset edge
            return
        eligible, grant, routes = self._expect
        m = h.model
        moved = [s for s, before in enumerate(routes) if before is not None and m.routes[s] != before]
        assert moved == ([] if grant is None else [grant]), \
            f"cycle {h.cycle}: eligible {eligible}, cursor rule grants {grant}, model moved {moved}"
        if grant is not None:
            assert m.round_robin == (grant + 1) % m.config.engines, (grant, m.round_robin)
            self.grants += 1
        self.checked += 1


async def phase(h: Harness, dest: int, sources: list[int], count: int, host_words: int = 0) -> None:
    """Route ``count`` tagged words from each source into ``dest`` (all eligible on one edge);
    the destination forwards them to its RX queue, where the host reads them. With
    ``host_words``, the host writes that many TX words to ``dest`` between its reads."""
    depth = h.model.config.fifo_words
    counts = await prepare(h, dest, sources, count, FORWARDER)
    total = depth + sum(counts.values()) + host_words
    extra = [0xC000 | dest << 8 | i for i in range(host_words)]
    pending = list(extra)
    words: list[int] = []
    guard = h.cycle
    while len(words) < total:
        assert h.cycle - guard < 400 * total, f"phase dest={dest}: {len(words)} of {total} words"
        if pending and (len(words) % 2 == 0 or len(words) + len(pending) == total):
            await h.command(SELECT, dest)
            if await h.try_write(2, pending[0], max_wait=24):
                pending.pop(0)
        await h.command(SELECT, dest)
        value = await h.try_read(3, max_wait=24)
        if value is not None:
            words.append(value)
    assert all(route is None for route in h.model.routes), h.model.routes
    await h.command(STOP, ALL)
    # ISA-level expectations on the delivered stream: every word exactly once, and
    # without a self-route (which can send a word around twice) in queue order.
    prefill = [0xB000 | dest << 8 | i for i in range(depth)]
    expected = prefill + extra + [tag(s, i) for s in sources for i in range(counts[s])]
    assert sorted(words) == sorted(expected), [hex(w) for w in words]
    if dest not in sources:
        assert words[:depth] == prefill, [hex(w) for w in words[:depth]]
        assert [w for w in words if w >> 12 == 0xC] == extra, "host TX words out of order"
        for source in sources:
            mine = [w for w in words if w >> 12 == 0xA and w >> 8 & 15 == source]
            assert mine == [tag(source, i) for i in range(counts[source])], (source, [hex(w) for w in mine])
    h.log("mover phase dest=%d sources=%s: order %s", dest, sources,
          " ".join(f"{w >> 8 & 15}" if w >> 12 == 0xA else "h" for w in words[depth:]))


async def host_priority_phase(h: Harness, dest: int, sources: list[int], count: int, host_words: int) -> None:
    """Host TX writes to ``dest`` while routes into ``dest`` compete. The destination pulls
    one word per cycle, so its TX queue always has room: each host word completes on an
    edge where a route could move a word, and the host must win that edge. The host reads
    the FIFO levels between its writes (lockstep-compared with the model)."""
    counts = await prepare(h, dest, sources, count, PULLER, streamer)
    await h.command(SELECT, dest)
    for i in range(host_words):  # back to back, while every route still has words
        await h.write(2, 0xC000 | dest << 8 | i)
    guard = h.cycle
    while any(route is not None for route in h.model.routes):
        assert h.cycle - guard < 4000, "host-priority phase did not finish"
        for engine in (dest, *sources):
            await h.command(SELECT, engine)
            await h.status(RS_LEVELS)
    await h.idle(8)
    for source in sources:  # every routed word left its source; nothing is left behind
        await h.command(SELECT, source)
        assert await h.status(RS_LEVELS) == 0, source
    await h.command(SELECT, dest)
    assert await h.status(RS_LEVELS) == 0
    if getattr(getattr(h.model, "options", None), "debug_counters", True):
        pulled = h.model.engines[dest].completed
        assert await h.status(RS_COUNT) == pulled
    await h.command(STOP, ALL)
    h.log("mover host-priority phase dest=%d sources=%s counts=%s: %d host words", dest, sources, counts, host_words)


async def prepare(h: Harness, dest: int, sources: list[int], count: int, program: list[int],
                  source_program=None) -> dict[int, int]:
    """Clean slate; preload the sources' RX queues with tagged words; fill the destination's
    TX queue from the host; enable every route (none can move: the queue is full); start
    the destination with ``program``. Returns the word count of each route."""
    depth = h.model.config.fifo_words
    await h.command(STOP, ALL)
    for engine in range(4):
        await h.command(SELECT, engine)
        await h.command(FLUSH)  # empty queues, no routes
    counts = {s: (min(count, depth) if s == dest else count) for s in sources}
    for source in sources:
        await h.load(source, (source_program or pusher)(source, counts[source]))
    await h.command(START, sum(1 << s for s in sources))
    await h.run_until(lambda: all(len(h.model.engines[s].rx) == min(counts[s], depth) for s in sources),
                      200, "sources preloaded")
    if dest in sources:
        await h.run_until(lambda: not h.model.engines[dest].running, 50, "destination preload")
    await h.load(dest, program)
    await h.command(SELECT, dest)
    for i in range(depth):
        await h.write(2, 0xB000 | dest << 8 | i)
    for source in sources:
        await h.command(ROUTE, source | dest << 2 | 16 | counts[source] << 5)
    await h.command(START, 1 << dest)
    return counts


async def arbitration(h: Harness) -> None:
    """Round robin among 2, 3 and 4 simultaneously eligible routes, then with host TX writes."""
    await h.start()
    monitor = ArbitrationMonitor()
    h.observers.append(monitor)
    try:
        await phase(h, dest=0, sources=[1, 2], count=6)
        await phase(h, dest=1, sources=[0, 2, 3], count=6)
        await phase(h, dest=2, sources=[0, 1, 2, 3], count=6)  # includes the self-route 2->2
        await host_priority_phase(h, dest=3, sources=[0, 1], count=40, host_words=6)
        await host_priority_phase(h, dest=0, sources=[0, 1, 2, 3], count=30, host_words=6)
        await phase(h, dest=1, sources=[0, 1, 2, 3], count=8, host_words=8)
    finally:
        h.observers.remove(monitor)
    h.log("arbitration monitor: %d edges checked, %d grants, contention %s, host priority %d",
          monitor.checked, monitor.grants, monitor.contended, monitor.host_priority)
    assert monitor.contended[2] and monitor.contended[3] and monitor.contended[4], monitor.contended
    assert monitor.host_priority, "no host TX write met an eligible route to the same destination"
    h.assert_no_faults()


@cocotb.test()
async def test_mover_arbitration(dut):
    """DMA round robin among 2-4 eligible routes and host TX priority, checked per edge."""
    await arbitration(CocotbHarness(dut))
