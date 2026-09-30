# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""LSTAT into every register on every engine (docs/isa.md, "Line-unit extension").

Specified behaviour (docs/isa.md, op 33): "LSTAT: register a := [0] a sampling
XFER ended on SE0, [1] arbitration lost, [2] stuff error (bits 0-2 stay set
until LCFG or START), [3] TX queue has data, [4] RX queue has space, [5] line
level, [6] ticker running, [13:8] data bits remaining at SE0; b = c = 0", with
a in 0..3 (tx, rx, x, y). From "Line XFER": a sampling XFER completes "at a
mid-bit tick with both pair pins low when SE0 end is set (LSTAT then reports SE0
and the data bits left)". From "Arbitration": "a drive-and-sample XFER that
reads 0 while it drives 1 sets the lost flag". From "Stuffing": "A received
stuff bit with the wrong value sets the stuff-error flag". LCFG "Resets the
line state and flags".

Each scenario ends with LSTAT into tx, x, y and rx in consecutive
instructions (the state does not change in between) and pushes the four
words. On every engine: SE0 ends leaving 21, 10 and 32 data bits (every bit of
the 6-bit count both 0 and 1), a stuff error, an arbitration loss, and a
plain transfer with TX data present and with a full RX queue. Checked: the four
words are equal; bits 0-2 are the flags the scenario produces; bits 8-13 hold
the remaining count at SE0; bit 3 and bit 4 follow the queues; bit 6 is set
(the ticker runs); bits 7 and 14-31 are 0. The SE0 scenario also checks that
the flags stay set over a following XFER and are cleared by LCFG. Bit 5 is
compared through the lockstep with the reference model only. The harness
compares the DUT with the model on every cycle.

At gate level (PE_GATE_LEVEL) the module is skipped unless PE_LINE_GL_FULL=1, as the long
line-unit demos are.
"""

from __future__ import annotations

import cocotb

from harness import CocotbHarness, Harness
from line_scenarios import Pads, RxCase, engine_status, load_and_start, read_rx, run_to_halt, rx_cells, ticks
from line_support import (HALT, PUSH, RX, SKIP_REASON, ST_LOST, ST_RUN, ST_RX_SPACE, ST_SE0, ST_STUFF_ERR,
                          ST_TX_DATA, TX, X, Y, bits_msb, dir_, lcfg, line_unit_enabled, load, lstat, ltim, mov,
                          pins, set_, shl, waitpin, xline)
from scenarios import fault_of
from test_line_spec_common import SKIP

if not line_unit_enabled():
    print(f"test_line_spec_lstat: {SKIP_REASON}")

# LSTAT into every register, then push the four words (pushed in the order rx, x, y, tx)
LSTAT_ALL = [lstat(TX), lstat(X), lstat(Y), lstat(RX), PUSH, mov(RX, X), PUSH, mov(RX, Y), PUSH, mov(RX, TX), PUSH]
FLAGS = ST_SE0 | ST_LOST | ST_STUFF_ERR


def check_word(words: list[int], what: str, *, flags: int, remaining: int | None, tx_data: bool,
               rx_space: bool = True) -> int:
    """The four LSTAT words of one point in time (pushed in the order rx, x, y, tx): equal, and the
    specified fields."""
    assert len(set(words)) == 1, f"{what}: LSTAT into rx/x/y/tx differ: {[hex(w) for w in words]}"
    w = words[0]
    assert w & FLAGS == flags, f"{what}: flags {w & 7:#x}, expected {flags:#x} (word {w:#x})"
    assert bool(w & ST_TX_DATA) == tx_data and bool(w & ST_RX_SPACE) == rx_space, f"{what}: queue bits of {w:#x}"
    assert w & ST_RUN, f"{what}: ticker bit clear in {w:#x}"
    assert w & ~0x3F7F == 0, f"{what}: bits 7 or 14-31 set in {w:#x}"
    if remaining is not None:
        assert w >> 8 & 0x3F == remaining, f"{what}: remaining {w >> 8 & 0x3F}, expected {remaining} (word {w:#x})"
    return w


async def se0_and_stuffing(h: Harness, engine: int, case: RxCase, *, sticky: bool) -> list[int]:
    """A sample-only line XFER of an encoded waveform (test_line_unit's RX framing: start cell,
    then cells of 2P cycles, SE0 on both pair pins after ``case.se0_after`` cells), then LSTAT_ALL;
    with ``sticky`` a second XFER over the idle line, LSTAT into rx and PUSH, LCFG, LSTAT into rx and PUSH."""
    await h.start()
    levels, _ = rx_cells(case)
    p = case.period
    start: dict[str, int | None] = {"at": None}

    def source(cycle: int, pads: Pads) -> int:
        if start["at"] is None or cycle < start["at"]:
            d, dm = 1, 0
        else:
            k = (cycle - start["at"]) // (2 * p) - 1
            if k < 0:
                d, dm = 0, 1
            elif case.se0_after is not None and k == case.se0_after:
                d, dm = 0, 0  # SE0 for one cell, then idle J
            elif k < len(levels) and (case.se0_after is None or k < case.se0_after):
                d, dm = levels[k], 1 - levels[k]
            else:
                d, dm = 1, 0
        return 0xE7 | d << 3 | dm << 4

    h.pins = Pads(source)
    config = lcfg(case.code, case.stuff, case.either, case.pair, se0=case.se0_after is not None, init=0)
    program = [pins(4, 0, 3), config, waitpin(3, 0), ltim(p, 0, 2 * p - 3),
               xline(case.bits, msb=case.msb, sample=True)] + LSTAT_ALL
    if sticky:
        program += [xline(4, sample=True), lstat(RX), PUSH, config, lstat(RX), PUSH]
    program.append(HALT)
    await load_and_start(h, engine, program)
    await h.idle(20)
    start["at"] = h.cycle + 5
    await run_to_halt(h, engine, 2 * p * (len(levels) + 40) + 600, f"engine {engine} {case.name}")
    words = await read_rx(h, engine, 6 if sticky else 4)
    assert fault_of(await engine_status(h, engine)) == 0, f"engine {engine} {case.name}: fault"
    return words


async def arbitration_lost(h: Harness, engine: int) -> list[int]:
    """test_line_unit's wired-AND bus (TXD pin 0, RXD pin 1): another node forces the bus
    dominant in a cell where this engine sends a recessive bit, with the monitor on."""
    await h.start()
    p, n, forced = 8, 16, 2
    ident = 0b1011_0011_1100_0101
    marker: dict[str, int | None] = {"at": None}
    window: dict[str, tuple[int, int] | None] = {"cell": None}

    def bus(cycle: int, pads: Pads) -> int:
        ours = pads.pad(cycle, 0, 1)
        dominant = window["cell"] is not None and window["cell"][0] <= cycle < window["cell"][1]
        level = ours & (0 if dominant else 1)
        mark = 8 if marker["at"] is not None and cycle >= marker["at"] else 0
        return 0xF5 | level << 1 | mark

    h.pins = Pads(bus)
    program = [pins(0, 0, 1), lcfg(arb=True, init=1), set_(1), dir_(1), load(TX, ident), shl(TX, 16),
               waitpin(3, 1), ltim(p, 0, 4), xline(n, msb=True, drive=True, sample=True)] + LSTAT_ALL + [HALT]
    assert bits_msb(ident, n)[forced] == 1
    await load_and_start(h, engine, program, ownership=0x01)
    await h.idle(16)
    marker["at"] = h.cycle + 3
    c0 = marker["at"] + 3
    tick_list = ticks(c0, p, 0, 4, 2 * n + 4)
    window["cell"] = (tick_list[2 * forced] + 1, tick_list[2 * forced + 2] + 1)
    await run_to_halt(h, engine, 2 * p * (n + 8) + 400, f"engine {engine} arbitration")
    return await read_rx(h, engine, 4)


async def queues(h: Harness, engine: int) -> tuple[list[int], list[int]]:
    """LSTAT with a TX word queued (no flags), then with the RX queue full; the TX word stays queued
    over BEGIN and START ("queues remain available for prefill")."""
    await h.start()
    program = [ltim(3), lcfg(init=1)] + LSTAT_ALL + [HALT]
    await load_and_start(h, engine, program, tx=[0x1234_5678])
    await run_to_halt(h, engine, 300, f"engine {engine} LSTAT with TX data")
    with_tx = await read_rx(h, engine, 4)
    depth = h.model.config.fifo_words
    program = [ltim(3)] + [PUSH] * depth + [lstat(TX), lstat(X), lstat(Y), lstat(RX)] + LSTAT_ALL[4:] + [HALT]
    await load_and_start(h, engine, program)
    await h.idle(3 * depth + 60)
    await read_rx(h, engine, depth)  # drain: the four LSTAT words follow
    await run_to_halt(h, engine, 300, f"engine {engine} LSTAT with RX full")
    full = await read_rx(h, engine, 4)
    return with_tx, full


SE0_CASES = [  # (data cells before SE0, XFER length): remaining 21, 10, 32
    (11, 32), (22, 32), (0, 32),
]


@cocotb.test(skip=SKIP)
async def test_lstat_se0_remaining_every_register_engine(dut):
    """SE0 end: LSTAT bit 0 and the remaining count 21, 10, 32 into tx/x/y/rx on every engine;
    the flags stay set over a following XFER and LCFG clears them."""
    h = CocotbHarness(dut)
    for engine in range(4):
        for seen, bits in SE0_CASES:
            case = RxCase(f"SE0 after {seen} of {bits}", 0x1234_5678, bits, code=1, pair=True, se0_after=seen)
            words = await se0_and_stuffing(h, engine, case, sticky=True)
            what = f"engine {engine}, {case.name}"
            check_word(words[0:4], what, flags=ST_SE0, remaining=bits - seen, tx_data=False)
            check_word(words[4:5], what + ", after a second XFER", flags=ST_SE0, remaining=None, tx_data=False)
            check_word(words[5:6], what + ", after LCFG", flags=0, remaining=None, tx_data=False)
        h.log("engine %d: SE0 remaining counts %s", engine, [b - s for s, b in SE0_CASES])


@cocotb.test(skip=SKIP)
async def test_lstat_stuff_error_every_register_engine(dut):
    """A wrong received stuff bit: LSTAT bit 2 into tx/x/y/rx on every engine (NRZI, six 1s)."""
    h = CocotbHarness(dut)
    for engine in range(4):
        case = RxCase("NRZI, wrong stuff bit", 0x0000_FFFF, 24, code=1, stuff=6, bad_stuff=1)
        words = await se0_and_stuffing(h, engine, case, sticky=False)
        check_word(words, f"engine {engine}, {case.name}", flags=ST_STUFF_ERR, remaining=None, tx_data=False)


@cocotb.test(skip=SKIP)
async def test_lstat_arbitration_lost_every_register_engine(dut):
    """Arbitration lost: LSTAT bit 1 into tx/x/y/rx on every engine."""
    h = CocotbHarness(dut)
    for engine in range(4):
        words = await arbitration_lost(h, engine)
        check_word(words, f"engine {engine}, arbitration", flags=ST_LOST, remaining=None, tx_data=False)


@cocotb.test(skip=SKIP)
async def test_lstat_queue_bits_every_register_engine(dut):
    """LSTAT bits 3 and 4 into tx/x/y/rx on every engine: a TX word queued, then also a full RX queue."""
    h = CocotbHarness(dut)
    for engine in range(4):
        with_tx, full = await queues(h, engine)
        check_word(with_tx, f"engine {engine}, TX data", flags=0, remaining=None, tx_data=True)
        check_word(full, f"engine {engine}, RX full", flags=0, remaining=None, tx_data=True, rx_space=False)
