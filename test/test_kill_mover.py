# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Mover eligibility: what may and may not stop a route, and what FLUSH disables.

docs/isa.md: a route may move a word unless its source RX is empty or reserved
by a host read (window 3 with the source selected), its destination TX is full,
a host TX word for the same destination is accepted on that edge, its own
descriptor is edited on that edge, or a FLUSH of its source or destination is
accepted on that edge. FLUSH also disables every route touching the flushed
engine. ``test_mover`` exercises contention and host priority; the survivors on
``dma_round_robin`` and ``route_remaining`` (docs/mutation-push.md) are terms
of this rule that only matter when the host acts on a *different* engine, or
that compare engine numbers bit by bit.

``stream_disturbed``: for each (source, destination) pair of a rotation, the
source pushes a stream of words with alternating low bits through a route
while the destination drives each word's bit 0 on its pin (PULL; OUT; JMP), so
a grant moved by one cycle moves a pin edge. Meanwhile the host writes TX
words to a third engine, FLUSHes and re-routes a fourth, reads the source RX
through window 3 (a reservation), writes TX words to the destination, and
reads FIFO levels.

``flush_matrix``: for each flushed engine and each of four route maps
(s -> s + r mod 4), FLUSH must disable exactly the routes whose source or
destination is the flushed engine; afterwards every engine pushes two words
and the FIFO levels show which routes moved them. ``flush_on_grant_edge`` and
``route_edit_on_grant_edge``: FLUSH, and a ROUTE rewriting a moving descriptor,
landing on an edge where the route could move a word. ``route_count_parity``:
descriptor counts with high bits set (time warp) drain exactly.
"""

from __future__ import annotations

import cocotb

from harness import (FLUSH, ROUTE, RS_LEVELS, SELECT, START, STOP, CocotbHarness, Harness, immediate,
                     instruction)
from test_kill_common import ADD, ALL, COUNT, DIR, GATE_LEVEL, HALT, JMP, LOAD, LOOP, OUT, PULL, PUSH, RX, WAIT, X
from test_kill_pc import command_at

def pin_forwarder(engine: int) -> list[int]:
    """Drive bit 0 of every pulled word on the engine's own pin."""
    return [immediate(DIR, 1 << engine), instruction(PULL), instruction(OUT, engine, 0, 0), immediate(JMP, 1)]


PULLER = [instruction(PULL)] * 7 + [immediate(JMP, 0)]


async def clean(h: Harness) -> None:
    await h.command(STOP, ALL)
    for engine in range(4):
        await h.command(SELECT, engine)
        await h.command(FLUSH)


STREAM = 400  # descriptor count: the stream outlasts every host action below


def looping_pusher(source: int) -> list[int]:
    """Push 0x5A<source>0, +1, +2, ... forever (bit 0 alternates), two cycles per word."""
    return [instruction(LOAD, RX, 0x5A, source << 4), instruction(LOAD, X, 0, 1),
            instruction(PUSH), instruction(ADD, RX, X), immediate(JMP, 2)]


async def stream(h: Harness, source: int, dest: int, third: int, fourth: int) -> None:
    await clean(h)
    await h.load(source, looping_pusher(source), ownership=0)
    await h.load(dest, pin_forwarder(dest), ownership=1 << dest)
    await h.load(third, PULLER, ownership=0)
    await h.load(fourth, [instruction(HALT)], ownership=0)
    await h.command(ROUTE, source | dest << 2 | 16 | STREAM << 5)
    await h.command(START, 1 << source | 1 << dest | 1 << third)
    await h.idle(12)
    for step in range(14):
        action = step % 7
        assert h.model.routes[source] is not None, "the stream ended early"
        if action == 0:  # host TX to an engine that is not the destination
            await h.command(SELECT, third)
            await h.write(2, 0xC0 | step)
            await h.write(2, 0xC1 | step)
        elif action == 1:  # FLUSH of an unrelated, halted engine
            await h.command(SELECT, fourth)
            await h.command(FLUSH)
        elif action == 2:  # descriptor edits of another source
            await h.command(ROUTE, fourth | source << 2 | 16 | 3 << 5)
            await h.command(ROUTE, fourth | dest << 2)
        elif action == 3:  # window 3 on the source: reserved while the host sits there and reads
            await h.command(SELECT, source)
            await h.set_window(3)
            await h.idle(12)
            await h.try_read(3, max_wait=6, pauses=(3, 0, 5))
            await h.idle(6)
        elif action == 4:  # host TX to the destination (host priority)
            await h.command(SELECT, dest)
            await h.try_write(2, 0xD0 | step, max_wait=8)
        elif action == 5:  # the stream's own descriptor rewritten on an edge where it could move a word
            src, dst, depth = h.model.engines[source], h.model.engines[dest], h.model.config.fifo_words
            await command_at(h, ROUTE, source | dest << 2 | 16 | STREAM << 5,
                             lambda: src.rx and len(dst.tx) < depth)
        else:
            await h.command(SELECT, dest)
            await h.status(RS_LEVELS)
        await h.idle(6)
    await h.command(STOP, ALL)


async def stream_disturbed(h: Harness) -> None:
    await h.start()
    for source in range(4):
        for dest in ((source + 1) % 4, (source + 3) % 4):
            third, fourth = [e for e in range(4) if e not in (source, dest)][::1 if source % 2 else -1]
            await stream(h, source, dest, third, fourth)
    await clean(h)
    h.assert_no_faults()


async def flush_matrix(h: Harness) -> None:
    await h.start()
    for engine in range(4):
        await h.load(engine, [instruction(LOAD, RX, engine, 0x77), instruction(PUSH), instruction(PUSH),
                              instruction(HALT)], ownership=0)
    for flushed in range(4):
        for shift in range(4):
            await clean(h)
            for source in range(4):  # RX queues are empty: nothing is eligible yet
                await h.command(ROUTE, source | ((source + shift) % 4) << 2 | 16 | 5 << 5)
            await h.command(SELECT, flushed)
            await h.command(FLUSH)
            kept = [s for s in range(4) if h.model.routes[s] is not None]
            assert kept == [s for s in range(4) if flushed not in (s, (s + shift) % 4)], (flushed, shift, kept)
            await h.command(START, ALL)
            await h.idle(24)
            for engine in range(4):
                await h.command(SELECT, engine)
                await h.status(RS_LEVELS)
    h.assert_no_faults()


async def flush_on_grant_edge(h: Harness) -> None:
    """FLUSH of a route's halted source, or of its halted destination, landing on an edge where
    the route would move a word: the grant is inhibited on that edge (the flushed queue is
    cleared, the route disabled); the other engine's queue level shows a stray move."""
    await h.start()
    for source in range(4):
        dest = (source + 1) % 4
        for flushed in (source, dest):
            await clean(h)
            if flushed == source:  # source halted with words left; destination pulls slowly
                pushes = 2 * h.model.config.fifo_words + 12  # more than both queues hold
                await h.load(source, [instruction(LOAD, RX, 0x5A, source)] + [instruction(PUSH)] * pushes
                             + [instruction(HALT)], ownership=0)
                await h.load(dest, [instruction(PULL), immediate(WAIT, 3), immediate(JMP, 0)], ownership=0)
            else:  # destination halted with room; source pushes slowly
                await h.load(dest, [instruction(HALT)], ownership=0)
                await h.load(source, [immediate(WAIT, 40), instruction(LOAD, RX, 0x5A, source), instruction(PUSH),
                                      immediate(WAIT, 3), immediate(JMP, 2)], ownership=0)
            await h.command(ROUTE, source | dest << 2 | 16 | 100 << 5)
            await h.command(START, 1 << source | (1 << dest if flushed == source else 0))
            await h.idle(2)
            src, dst = h.model.engines[source], h.model.engines[dest]
            depth = h.model.config.fifo_words
            eligible = (lambda: not src.running and src.rx and len(dst.tx) < depth) if flushed == source else \
                (lambda: src.rx and len(dst.tx) < depth)
            await h.command(SELECT, flushed)
            await command_at(h, FLUSH, 0, eligible)
            assert h.model.routes[source] is None
            await h.idle(2)
            for engine in (source, dest):
                await h.command(SELECT, engine)
                await h.status(RS_LEVELS)
    await clean(h)
    h.assert_no_faults()


async def route_count_parity(h: Harness) -> None:
    """Descriptor counts with a high bit set (0x28, 0x208, 0x1010) drain exactly: the larger two
    are warped down to 6 words (RTL only) after an odd or an even number of cycles; the source
    pushes the remaining count + 4 words, the route moves the count, 4 words stay behind (fewer
    on designs with smaller queues)."""
    await h.start()
    for source in range(4):
        dest = (source + 1) % 4
        for count, keep in ((0x28, 0x28), (0x208, 6), (0x1010, 6)):
            for extra in (0, 1):
                if keep != count and not h.warp_supported():
                    continue
                await clean(h)
                await h.load(dest, PULLER, ownership=0)
                left = min(4, h.model.config.fifo_words)  # words that stay behind in the source RX
                await h.load(source, [instruction(LOAD, RX, 0x5A, source), immediate(COUNT, keep + left - 1),
                                      instruction(PUSH), immediate(LOOP, 2), instruction(HALT)], ownership=0)
                await h.command(ROUTE, source | dest << 2 | 16 | count << 5)
                await h.idle(5 + extra)
                if keep != count:
                    await h.warp_routes(count - keep)
                await h.command(START, 1 << source | 1 << dest)
                src = h.model.engines[source]
                await h.run_until(lambda: not src.running and h.model.routes[source] is None, 4 * keep + 200,
                                  "route drain")
                await h.idle(4)
                await h.command(SELECT, source)
                levels = await h.status(RS_LEVELS)
                assert levels == left << 16, hex(levels)
    await clean(h)
    h.assert_no_faults()


@cocotb.test()
async def test_kill_flush_on_grant_edge(dut):
    """FLUSH of a route's source or destination on an edge where the route could move a word."""
    await flush_on_grant_edge(CocotbHarness(dut))


async def route_edit_on_grant_edge(h: Harness) -> None:
    """A ROUTE that rewrites a moving descriptor to 3 words, landing on an edge where the route
    could move a word: no word moves on that edge, then exactly 3 (the source queue level
    shows a stray move on the edit edge)."""
    await h.start()
    depth = h.model.config.fifo_words
    for source in range(4):
        dest = (source + 1) % 4
        await clean(h)
        await h.load(source, [instruction(LOAD, RX, 0x5A, source)] + [instruction(PUSH)] * (2 * depth + 12)
                     + [instruction(HALT)], ownership=0)
        await h.load(dest, [instruction(PULL), immediate(WAIT, 3), immediate(JMP, 0)], ownership=0)
        await h.command(ROUTE, source | dest << 2 | 16 | 100 << 5)
        await h.command(START, 1 << source | 1 << dest)
        src, dst = h.model.engines[source], h.model.engines[dest]
        await h.idle(2)
        await command_at(h, ROUTE, source | dest << 2 | 16 | 3 << 5, lambda: src.rx and len(dst.tx) < depth)
        await h.run_until(lambda: h.model.routes[source] is None, 600, "edited route")
        await h.idle(8)
        await h.command(STOP, ALL)
        for engine in (source, dest):
            await h.command(SELECT, engine)
            await h.status(RS_LEVELS)
    await clean(h)
    h.assert_no_faults()


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_route_edit_on_grant_edge(dut):
    """ROUTE rewriting a moving descriptor on an edge where it could move a word."""
    await route_edit_on_grant_edge(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_route_count_parity(dut):
    """Routes of 0x28, 0x208 and 0x1010 words (warped) drain exactly, every source."""
    await route_count_parity(CocotbHarness(dut))


@cocotb.test(skip=GATE_LEVEL)
async def test_kill_stream_disturbed(dut):
    """A routed stream (grant timing on a pin) while the host acts on other engines."""
    await stream_disturbed(CocotbHarness(dut))


@cocotb.test()
async def test_kill_flush_matrix(dut):
    """FLUSH of each engine disables exactly the routes touching it (four route maps)."""
    await flush_matrix(CocotbHarness(dut))
