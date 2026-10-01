# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The completed-instruction count counts completions only, every READ_SELECT word
reads what it names, and START restores the default LIMIT.

Specified behaviour (docs/isa.md):
- READ_SELECT "0 status,1 timestamp,2 FIFO levels,3 PC,4 event pending,5
  completed instruction count,6 held RX word,7 ISA version"; "Reads window0
  status: bit0 running,bit1 committed,bit2 stalled,bit3 fault; bits15:8 fault
  code. FIFO levels: TX low16, RX high16. PC and instruction count are ordinary
  unsigned words." "READ_SELECT6 zero-extends a 16-bit held RX register."
- "Instructions commit on rising clk edges"; "HALT completes like other
  instructions: the PC advances and the completed-instruction count
  increments". A blocked WAITPIN, WAITEVENT, PULL or PUSH, a WAIT's hold
  cycles and an XFER's transitions are not completions; WAIT completes on its
  issue edge ("WAIT n advances PC on issue") and XFER on its final idle edge
  ("PC advances at final idle edge").
- "Strict overflow preserves the rejected RX word, PC and completed-instruction
  count, halts only that engine, and releases its output enables." (fault
  code 4)
- LIMIT: "maximum blocked cycles for WAITPIN/WAITEVENT (reset default 65535)";
  "START resets PC, registers, ... it also clears the completed-instruction
  count and PINS and restores LIMIT to 65535"; WAITPIN "fault after LIMIT
  consecutive unsuccessful samples"; fault code 3 is the "bounded-wait timeout".
- On a design without debug counters (``debug_counters: false``, the diet
  variants) "READ_SELECT 5 reads 0" (docs/isa.md, "Configuration variants").

``count_through_holds``: all four engines run one program: WAITEVENT (an event
is already pending), WAITEVENT blocked, WAITPIN blocked until pin 7 rises,
WAITPIN blocked until it falls, PULL on an empty TX queue, LOAD rx, PUSH until
the RX queue is full, a blocking PUSH, PINS, XFER 8, 24, WAIT 400, COUNT 3
with a LOOP onto itself, and a strict PUSH that overflows (fault 4). While the
engines block in the second WAITEVENT the counts are set to a pattern P with
the harness's ``warp_completed`` (RTL), one round for each of the twelve
``COUNT_PATTERNS``, which give every pair of count bits 5..31 all four value
combinations (for P = 0 the warp wraps the count past 2^32 - 1). While all
four engines block (in each WAITEVENT, WAITPIN, PULL and PUSH stall),
transfer (XFER) or hold (WAIT), each engine's count is read three times, at
captures of both cycle parities, and must equal the completions so far; the
host then releases the blocked instruction. After the fault each engine's
eight READ_SELECT words are read back and compared with the values the rules
above give: status 0x40A, levels (fifo words) << 16, PC at the strict PUSH,
event pending 0 or 1 (every other round the host sends an EVENT before
reading), count P + 14 + fifo words (0 without debug counters), held RX
0x5Ae, the version word; the timestamp must increase from read to read. At
gate level (no warp) one round runs, from a count of 1.

``default_limit``: every engine runs ``DIR 1<<e; WAITEVENT`` (no event) or
``DIR 1<<e; WAITPIN 7, 1`` (pin 7 low) after reset, then the same with
``LIMIT 7`` in front of the wait, then again without it. Without a LIMIT
instruction the wait must time out on its 65,535th sample, so the engine's
output enable (uio_oe bit e, read from the DUT) stays high for exactly 65,535
edges (8 with LIMIT 7: LIMIT's edge and 7 samples), and the status reports
fault code 3. The samples before 65,535 - 8 are skipped with the time warp
(RTL only).
"""

from __future__ import annotations

import cocotb

from harness import (CLEAR, EVENT, FLUSH, READ_SELECT, RS_COUNT, RS_EVENT, RS_HELD_RX, RS_LEVELS, RS_PC, RS_STATUS,
                     RS_TIMESTAMP, RS_VERSION, SELECT, START, VERSION_WORD, Harness, immediate, instruction)
from scenarios import fault_of
from test_kill_common import (COUNT, DIR, GATE_LEVEL, LIMIT, LOAD, LOOP, PINS, PULL, PUSH, RX, WAIT, WAITEVENT,
                              WAITPIN, XFER, counters, own, reload)
from test_spec_common import harness, read_word
from test_wait_limit import EnableEdges

HOLD = 6
WAIT_PIN = 7
XFER_HALF_PERIOD = 24  # XFER 8, 24 lasts 384 cycles and WAIT 400 holds 400: the count reads fit inside
WAIT_CYCLES = 400
DEFAULT_LIMIT = 65535
# Bits 0..4 are left 0: the count then moves only in bits 0..4 during a round (it grows
# by 14 + fifo words, at most 22). For two bit positions p != q in 5..31, the pattern pairs split them
# by bit 0..4 of their index; 0 and 0xFFFFFFE0 give (0, 0) and (1, 1).
COUNT_PATTERNS = [0x55555540, 0xAAAAAAA0, 0x33333320, 0xCCCCCCC0, 0x0F0F0F00, 0xF0F0F0E0,
                  0x00FF00E0, 0xFF00FF00, 0x0000FFE0, 0xFFFF0000, 0xFFFFFFE0, 0x00000000]


def count_program(engine: int, fifo_words: int) -> list[int]:
    words = [instruction(WAITEVENT),  # 0: consumes the event sent before START
             instruction(WAITEVENT),  # 1: blocks; the count is warped here
             instruction(WAITPIN, WAIT_PIN, 1),  # 2: blocks until pin 7 rises
             instruction(WAITPIN, WAIT_PIN, 0),  # 3: blocks until pin 7 falls
             instruction(PULL),  # 4: blocks until the host writes a TX word
             instruction(LOAD, RX, 0x5A, engine)]  # 5
    words += [instruction(PUSH)] * fifo_words  # 6 .. 5 + F: fill the RX queue
    words += [instruction(PUSH),  # 6 + F: blocks until the host reads a word
              immediate(PINS, engine), instruction(XFER, 8, XFER_HALF_PERIOD, 0),  # clock on the engine's own pin
              immediate(WAIT, WAIT_CYCLES), immediate(COUNT, 3), immediate(LOOP, 11 + fifo_words),
              instruction(PUSH, 1)]  # 12 + F: strict PUSH into a full queue: fault 4
    return words


def completions_after_warp(fifo_words: int) -> int:
    """Instructions that complete from PC 1 on: PCs 1 .. 10 + F once each, the LOOP four times
    (COUNT 3), and not the faulting strict PUSH."""
    return (10 + fifo_words) + 4


async def holding(h: Harness, state, what: str, count: int | None = None) -> None:
    """Idle until every engine is in ``state`` (a predicate on the model's engine). With
    ``count`` (the completions so far by isa.md), read each engine's READ_SELECT 5 three
    times while all four stay in that state: two reads back to back and one a cycle later,
    so that the captures fall on both cycle parities; every read must give ``count`` (0
    without debug counters)."""
    engines = h.model.engines
    await h.run_until(lambda: all(state(e) for e in engines), 400, what)
    if count is None:
        return
    await h.idle(HOLD)
    await h.command(READ_SELECT, RS_COUNT)
    expected = count % (1 << 32) if counters(h) else 0
    for engine in range(4):
        await h.command(SELECT, engine)
        reads = [await read_word(h), await read_word(h)]
        await h.idle(1)
        reads.append(await read_word(h))
        assert reads == [expected] * 3, (f"engine {engine} in {what}: READ_SELECT 5 reads "
                                         f"{[hex(r) for r in reads]}, isa.md gives {expected:#x}")
    assert all(state(e) for e in engines), f"an engine left {what} during the reads"


def blocked_at(pc: int):
    return lambda e: e.pc == pc and e.stalled


async def count_round(h: Harness, pattern: int | None, event_before_read: bool, last_time: list[int]) -> None:
    fifo_words = h.model.config.fifo_words
    await h.command(CLEAR, 0b1111)
    for engine in range(4):
        await h.command(SELECT, engine)
        await h.command(FLUSH)
    await h.command(EVENT, 0b1111)  # pending for the first WAITEVENT
    await h.command(START, 0b1111)
    await holding(h, blocked_at(1), "the second WAITEVENT")
    base = 1  # the first WAITEVENT has completed
    if pattern is not None and counters(h):
        delta = (pattern - h.model.engines[0].completed) % (1 << 32)
        if delta:
            await h.warp_completed(delta)
        base = pattern
    # Straight-line code from PC 1: blocked at PC k the engine has completed k - 1 more
    # instructions; inside the XFER (PC 8 + F) 7 + F; in the WAIT's hold (WAIT completed) 9 + F.
    await holding(h, blocked_at(1), "the second WAITEVENT", base)
    await h.command(EVENT, 0b1111)
    await holding(h, blocked_at(2), "WAITPIN 7, 1", base + 1)
    h.pins = 1 << WAIT_PIN
    await holding(h, blocked_at(3), "WAITPIN 7, 0", base + 2)
    h.pins = 0
    await holding(h, blocked_at(4), "PULL", base + 3)
    for engine in range(4):
        await h.command(SELECT, engine)
        await h.write(2, 0x7E570000 | engine)
    await holding(h, blocked_at(6 + fifo_words), "the blocking PUSH", base + 5 + fifo_words)
    for engine in range(4):
        await h.command(SELECT, engine)
        word = await h.read(3)
        assert word == 0x5A00 | engine, f"engine {engine}: RX word {word:#x}, expected rx = {0x5A00 | engine:#x}"
    await holding(h, lambda e: e.transfer is not None, "the XFER", base + 7 + fifo_words)
    await holding(h, lambda e: e.wait > 0, "the WAIT", base + 9 + fifo_words)
    await h.run_until(lambda: not any(e.running for e in h.model.engines), 400, "the strict PUSH")
    if event_before_read:
        await h.command(EVENT, 0b1111)
    expected_count = (base + completions_after_warp(fifo_words)) % (1 << 32) if counters(h) else 0
    expected = {RS_STATUS: 1 << 1 | 1 << 3 | 4 << 8, RS_LEVELS: fifo_words << 16, RS_PC: 12 + fifo_words,
                RS_EVENT: int(event_before_read), RS_COUNT: expected_count, RS_VERSION: VERSION_WORD}
    for engine in range(4):
        await h.command(SELECT, engine)
        words = {selection: await read_word(h, selection) for selection in range(8)}
        for selection, value in expected.items():
            assert words[selection] == value, (f"P={pattern if pattern is None else hex(pattern)}, engine {engine}: "
                                               f"READ_SELECT {selection} reads {words[selection]:#x}, "
                                               f"isa.md gives {value:#x}")
        assert words[RS_HELD_RX] == 0x5A00 | engine, f"engine {engine}: held RX {words[RS_HELD_RX]:#x}"
        assert words[RS_TIMESTAMP] > last_time[0], f"timestamp {words[RS_TIMESTAMP]} after {last_time[0]}"
        last_time[0] = words[RS_TIMESTAMP]


async def count_through_holds(h: Harness, patterns: list[int | None]) -> int:
    await h.start()
    h.pins = 0
    for engine in range(4):
        await own(h, engine, 1 << engine)
        await reload(h, engine, count_program(engine, h.model.config.fifo_words))
    last_time = [-1]
    for index, pattern in enumerate(patterns):
        await count_round(h, pattern, bool(index & 1), last_time)
    await h.command(CLEAR, 0b1111)
    h.assert_no_faults()
    return len(patterns)


async def timeout_round(h: Harness, edges: EnableEdges, image: list[int], limit: int, warp: bool) -> None:
    for engine in range(4):
        await reload(h, engine, [immediate(DIR, 1 << engine), *image])
    edges.clear()
    await h.command(START, 0b1111)
    budget = limit + 40
    if warp:
        await h.idle(4)
        blocked = h.model.engines[0].blocked
        assert all(e.blocked == blocked and e.stalled for e in h.model.engines)
        await h.warp(limit - 8 - blocked)
        budget = 40
    await h.run_until(lambda: not any(e.running for e in h.model.engines), budget, f"timeout at LIMIT {limit}")
    await h.idle(2)
    held = 1 if len(image) == 2 else 0  # LIMIT's own edge
    for engine in range(4):
        assert engine in edges.rise and engine in edges.fall, f"engine {engine}: enable not raised and released"
        assert edges.fall[engine] - edges.rise[engine] == limit + held, (
            f"LIMIT {limit}, engine {engine}: enable high for {edges.fall[engine] - edges.rise[engine]} edges, "
            f"isa.md gives {limit + held}")
    for engine in range(4):
        await h.command(SELECT, engine)
        fault = fault_of(await read_word(h, RS_STATUS))
        assert fault == 3, f"LIMIT {limit}, engine {engine}: fault code {fault}, expected 3"
    await h.command(CLEAR, 0b1111)


async def default_limit(h: Harness) -> int:
    await h.start()
    h.pins = 0  # pin 7 low: WAITPIN 7, 1 never succeeds
    for engine in range(4):
        await own(h, engine, 1 << engine)
    edges = EnableEdges()
    h.observers.append(edges)
    rounds = 0
    for wait in (instruction(WAITEVENT), instruction(WAITPIN, WAIT_PIN, 1)):
        if rounds:
            await h.reset()  # the reset default, then START's restore after LIMIT 7
            for engine in range(4):
                await own(h, engine, 1 << engine)
        await timeout_round(h, edges, [wait], DEFAULT_LIMIT, warp=True)
        await timeout_round(h, edges, [immediate(LIMIT, 7), wait], 7, warp=False)
        await timeout_round(h, edges, [wait], DEFAULT_LIMIT, warp=True)
        rounds += 3
    h.observers.remove(edges)
    h.assert_no_faults()
    return rounds


@cocotb.test()
async def test_spec_count_and_read_select_words(dut):
    """Completed count through blocked waits, stalls, XFER, WAIT and strict overflow; all eight READ_SELECT words."""
    h = harness(dut)
    warp = h.warp_supported() and not GATE_LEVEL
    assert GATE_LEVEL or warp, "time warp unavailable on RTL"
    rounds = await count_through_holds(h, COUNT_PATTERNS if warp else [None])
    h.log("count and READ_SELECT words as isa.md gives them in %d rounds (%d cycles, %d instructions warped)",
          rounds, h.cycle, h.warped_instructions)


@cocotb.test(skip=GATE_LEVEL)
async def test_spec_start_restores_default_limit(dut):
    """WAITEVENT/WAITPIN without LIMIT time out on sample 65,535, after reset and after a START that follows LIMIT 7."""
    h = harness(dut)
    assert h.warp_supported(), "time warp unavailable on RTL"
    rounds = await default_limit(h)
    h.log("default LIMIT timeouts in %d rounds (%d cycles simulated, %d warped)", rounds, h.cycle, h.warped)
