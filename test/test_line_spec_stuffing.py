# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Bit stuffing and destuffing for every run length and both run kinds, on
every engine (docs/isa.md, "Line-unit extension").

Specified behaviour (docs/isa.md): LCFG "[2] stuffing; [3] stuffing counts
runs of either polarity (else runs of 1s); [6:4] run length - 1 (length 1 with
[3] set is invalid)". "Stuffing. When the run of equal line bits (either
polarity) or of 1s reaches the run length, the next cell is a stuff bit: the
complement of the last bit, or 0 for runs of 1s. This also applies after the
last data bit (a trailing stuff bit extends the XFER by one cell). Stuff bits
are not shifted, counted or fed to the CRC. A received stuff bit with the
wrong value sets the stuff-error flag and is still dropped."

On every engine, for runs of 1s of length 1 to 8 and runs of either polarity
of length 2 to 8, NRZ, with data that contains runs of the length and ends
with one (a trailing stuff bit): a drive-only line XFER must put on the data
pin the cells of the independent encoder (line_support.stuff_ones /
stuff_either), and a sample-only line XFER that receives those cells must
deliver the data bits with no stuff error; both feed the CRC, which must equal
the ISA's CRC step rule over the data bits only. For run lengths 1, 3 and 6
(runs of 1s) and 2, 4 and 7 (either polarity), a received stream with its
first stuff bit inverted must set the stuff-error flag (LSTAT bit 2). The
harness compares the DUT with the reference model on every cycle.
"""

from __future__ import annotations

import random

import cocotb

from harness import CocotbHarness, Harness
from line_scenarios import Pads, engine_status, load_and_start, read_rx, run_to_halt, ticks
from line_support import (HALT, PUSH, RX, SKIP_REASON, ST_STUFF_ERR, TX, X, crc_get, crc_preset, crc_set, dir_,
                          from_bits_lsb, lcfg, line_unit_enabled, load, lstat, ltim, mov, or_, pins, set_, shl,
                          stuff_either, stuff_ones, wait, waitpin, xline)
from scenarios import fault_of
from test_line_spec_common import SKIP, crc_rule

if not line_unit_enabled():
    print(f"test_line_spec_stuffing: {SKIP_REASON}")

PERIOD, NBITS = 2, 24
CASES = [(n, False) for n in range(1, 9)] + [(n, True) for n in range(2, 9)]
BAD = {(1, False), (3, False), (6, False), (2, True), (4, True), (7, True)}


def case_bits(rng: random.Random, n: int, either: bool) -> list[int]:
    """NBITS data bits with runs of length n (1s, or both levels) and a run of n at the end."""
    bits: list[int] = []
    level = 1
    while len(bits) < NBITS - n:
        run = n + rng.randrange(3) if rng.random() < 0.6 else rng.randrange(1, n + 1)
        bits += [level] * run
        level = 1 - level if either or rng.random() < 0.5 else level
        if not either:
            bits.append(0)
    bits = bits[:NBITS - n]
    tail = 1 if not either else 1 - bits[-1]
    return bits + [tail] * n


def stuffed(n: int, either: bool, bits: list[int]) -> list[int]:
    return stuff_either(n, bits) if either else stuff_ones(n, bits)


async def drive(h: Harness, engine: int, n: int, either: bool, bits: list[int], init: int) -> None:
    data_pin, marker_pin = 2 * engine, 2 * engine + 1
    word = from_bits_lsb(bits)
    marker: dict[str, int | None] = {"at": None}
    pads = Pads(lambda cycle, pads: 0xFF & ~(1 << marker_pin) | (
        (1 << marker_pin) if marker["at"] is not None and cycle >= marker["at"] else 0))
    h.pins = pads
    program = [pins((data_pin + 1) % 8, data_pin, marker_pin), lcfg(0, n, either, init=1), set_(1 << data_pin),
               dir_(1 << data_pin), load(X, init), crc_set(X), crc_preset(3), load(TX, word & 0xFFFF),
               load(X, word >> 16), shl(X, 16), or_(TX, X), waitpin(marker_pin, 1), ltim(PERIOD, 0, 4),
               xline(NBITS, drive=True, crc=True), wait(6 * PERIOD + 8), crc_get(RX), PUSH, HALT]
    await load_and_start(h, engine, program, ownership=1 << data_pin)
    await h.idle(16)
    marker["at"] = h.cycle + 3
    c0 = marker["at"] + 3
    cells = stuffed(n, either, bits)
    await run_to_halt(h, engine, 2 * PERIOD * (len(cells) + 12) + 400, f"engine {engine} stuffing TX")
    (crc,) = await read_rx(h, engine, 1)
    assert fault_of(await engine_status(h, engine)) == 0
    tick_list = ticks(c0, PERIOD, 0, 4, 2 * len(cells) + 4)
    got = [pads.pad(tick_list[2 * j] + 1 + PERIOD, data_pin) for j in range(len(cells))]
    what = f"engine {engine}, TX, run {n} ({'either polarity' if either else 'ones'})"
    assert got == cells, f"{what}: line {got} != encoder {cells}"
    want = crc_rule(bits, 3, False, init)
    assert crc == want, f"{what}: CRC {crc:#06x} != rule over the data bits {want:#06x}"


async def receive(h: Harness, engine: int, n: int, either: bool, bits: list[int], init: int,
                  *, bad: bool) -> None:
    rx_pin = 2 * engine + 1
    cells = stuffed(n, either, bits)
    if bad:  # invert the first stuff bit: it follows the first run of n (1s, or equal bits)
        first = next(i for i in range(n - 1, len(bits))
                     if len(set(bits[i - n + 1:i + 1])) == 1 and (either or bits[i] == 1))
        j = first + 1  # no stuff bit before it, so cell index = data index + 1
        cells = cells[:j] + [1 - cells[j]] + cells[j + 1:]
    start: dict[str, int | None] = {"at": None}

    def source(cycle: int, pads: Pads) -> int:
        level = 1
        if start["at"] is not None and cycle >= start["at"]:
            k = (cycle - start["at"]) // (2 * period) - 1
            level = 0 if k < 0 else cells[k] if k < len(cells) else 1
        return 0xFF & ~(1 << rx_pin) | level << rx_pin

    period = 3
    h.pins = Pads(source)
    program = [pins(2 * engine, 2 * engine, rx_pin), lcfg(0, n, either), load(X, init), crc_set(X), crc_preset(3),
               waitpin(rx_pin, 0), ltim(period, 0, 2 * period - 3), xline(NBITS, sample=True, crc=True),
               lstat(X), PUSH, mov(RX, X), PUSH, crc_get(RX), PUSH, HALT]
    await load_and_start(h, engine, program)
    await h.idle(20)
    start["at"] = h.cycle + 5
    await run_to_halt(h, engine, 2 * period * (len(cells) + 14) + 400, f"engine {engine} stuffing RX")
    rx, status, crc = await read_rx(h, engine, 3)
    assert fault_of(await engine_status(h, engine)) == 0
    what = f"engine {engine}, RX, run {n} ({'either polarity' if either else 'ones'}){', wrong stuff bit' if bad else ''}"
    assert bool(status & ST_STUFF_ERR) == bad, f"{what}: LSTAT {status:#x}"
    if not bad:
        value = rx >> (32 - NBITS)
        assert value == from_bits_lsb(bits), f"{what}: received {value:#x} != {from_bits_lsb(bits):#x}"
        want = crc_rule(bits, 3, False, init)
        assert crc == want, f"{what}: CRC {crc:#06x} != rule over the data bits {want:#06x}"


@cocotb.test(skip=SKIP)
async def test_stuffing_every_run_length_every_engine(dut):
    """Every engine, runs of 1s of length 1-8 and of either polarity of length 2-8: TX cells equal the
    encoder, RX recovers the data without a stuff error, the CRC covers the data bits only; wrong stuff
    bits set the stuff-error flag."""
    h = CocotbHarness(dut)
    rng = random.Random(0x5EC0D0)
    for engine in range(4):
        for n, either in CASES:
            bits = case_bits(rng, n, either)
            init = rng.randrange(1 << 16)
            await h.start()
            await drive(h, engine, n, either, bits, init)
            await h.start()
            await receive(h, engine, n, either, bits, init, bad=False)
            if (n, either) in BAD:
                await h.start()
                await receive(h, engine, n, either, bits, init, bad=True)
        h.log("engine %d: %d stuffing configurations, TX and RX", engine, len(CASES))
