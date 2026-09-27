# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Directed line-unit scenarios (docs/extension.md), runnable on the DUT
(cocotb, test_line_unit.py) or on the model alone (harness.run_model).

Under cocotb the harness compares the DUT with the reference model on every
cycle. On top of that each scenario checks its results against the
independent references of line_support.py: published CRC check values,
line encoders and decoders, and the ticker timing formula below.

Timing anchor: most scenarios start the line unit from ``WAITPIN pin, level``.
When the pad of that pin changes in cycle E, the engine sees the change
through the two-flop synchronizer in cycle E + 2, WAITPIN completes in that
cycle and the next instruction (LTIM) issues in cycle E + 3.

Ticker formula (docs/extension.md): LTIM issued in cycle c0 with half-period
P, fraction Q and delay D ticks in cycles T_0 = c0 + (D or P) and
T_{k+1} = T_k + P + carry_k, where carry_k = (acc_k + Q) >> 8,
acc_{k+1} = (acc_k + Q) & 255, acc_0 = 0. Even k are bit boundaries, odd k
mid-bit. A drive writes the pin at a boundary tick, so the pad shows the new
cell from cycle T_k + 1; a sample in cycle T_k reads the pad of cycle T_k - 2.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from typing import Callable

from harness import RS_PC, RS_STATUS, RS_VERSION, SELECT, START, STOP, Harness, pad_value
from line_support import (CRC_VECTORS, HALT, PULL, PUSH, RX, ST_LEVEL, ST_LOST, ST_RUN, ST_RX_SPACE, ST_SE0,
                          ST_STUFF_ERR, ST_TX_DATA, TX, X, Y, add, bits_lsb, bits_msb, crc_bits_normal,
                          crc_bits_reflected, crc_catalogue, crc_get, crc_preset, crc_set, destuff_either,
                          destuff_ones, dir_, from_bits_lsb, from_bits_msb, imm, ins, jz, lcfg, load, lstat,
                          ltim, mov, not_, nrzi_decode, nrzi_encode, or_, pins, set_, shl, stuff_either,
                          stuff_ones, wait, waitpin, xfer, xline)
from model.reference import Outputs
from scenarios import fault_of


# ------------------------------------------------------------------ helpers
class Pads:
    """Pin supplier that records the DUT's pad outputs in every cycle.

    ``external(cycle, pads)`` gives the level of every undriven pad (default:
    pull-ups on all pins); driven pads read back the DUT's own value."""

    def __init__(self, external: Callable[[int, "Pads"], int] | None = None) -> None:
        self.external = external or (lambda cycle, pads: 0xFF)
        self.first: int | None = None
        self.out = array("B")
        self.oe = array("B")

    def __call__(self, cycle: int, out: Outputs) -> int:
        if self.first is None:
            self.first = cycle
        while self.first + len(self.out) < cycle:  # not called in some cycle: repeat
            self.out.append(self.out[-1] if self.out else 0)
            self.oe.append(self.oe[-1] if self.oe else 0)
        if self.first + len(self.out) == cycle:
            self.out.append(out.uio_out)
            self.oe.append(out.uio_oe)
        return pad_value(out, self.external(cycle, self))

    def driven(self, cycle: int) -> tuple[int, int]:
        k = cycle - (self.first or 0)
        if 0 <= k < len(self.out):
            return self.out[k], self.oe[k]
        return 0, 0

    def pad(self, cycle: int, pin: int, pull: int = 1) -> int:
        """Pad level: the DUT's value when it drives the pin, else ``pull``."""
        value, enable = self.driven(cycle)
        return value >> pin & 1 if enable >> pin & 1 else pull

    def enabled(self, cycle: int, pin: int) -> bool:
        return bool(self.driven(cycle)[1] >> pin & 1)


def ticks(c0: int, period: int, frac: int, delay: int, count: int) -> list[int]:
    """Tick cycles T_0..T_{count-1} of an LTIM issued in cycle c0 (see module docstring)."""
    t = c0 + (delay or period)
    acc, out = 0, []
    for _ in range(count):
        out.append(t)
        total = acc + frac
        t += period + (total >> 8)
        acc = total & 255
    return out


async def load_and_start(h: Harness, engine: int, words: list[int], *, ownership: int = 0,
                         tx: list[int] = ()) -> None:
    await h.load(engine, words, ownership=ownership)
    await h.command(SELECT, engine)
    for word in tx:
        await h.write(2, word)
    await h.command(START, 1 << engine)


async def engine_status(h: Harness, engine: int) -> int:
    await h.command(SELECT, engine)
    return await h.status(RS_STATUS)


async def read_rx(h: Harness, engine: int, n: int) -> list[int]:
    await h.command(SELECT, engine)
    return [await h.read(3) for _ in range(n)]


async def run_to_halt(h: Harness, engine: int, limit: int, what: str) -> None:
    await h.run_until(lambda: not h.engine(engine).running, limit, what)


# ------------------------------------------------------------------ A. identity
async def version(h: Harness) -> None:
    """READ_SELECT 7 = ISA version 3 | capabilities 0xF5F << 8 (docs/extension.md)."""
    await h.start()
    value = await h.status(RS_VERSION)
    assert value == 0x000F5F03, f"READ_SELECT 7 = {value:#x}"
    assert value & 0xFF == 3 and value >> 8 & 1, "ISA version 3 with the line-unit capability bit"


# ------------------------------------------------------------------ B. CRC
async def crc_vectors(h: Harness) -> None:
    """Every catalogued CRC of CRC_VECTORS over b"123456789", computed by the
    unit from a line XFER that drives the message (not enabled on the pad):
    the published check value (after the catalogue's xorout) must come out,
    and the independent catalogue model must reproduce the same value."""
    await h.start()
    message = b"123456789"
    for v in CRC_VECTORS:
        reference = crc_catalogue(message, width=v.width, poly=v.poly, init=v.init, refin=v.refin,
                                  refout=v.refout, xorout=v.xorout)
        assert reference == v.check, f"{v.name}: catalogue model {reference:#x} != published {v.check:#x}"
        msb = not v.refin
        init = v.init << (16 - v.width) if msb else v.init
        if msb:
            words = [message[0] << 24 | message[1] << 16 | message[2] << 8 | message[3],
                     message[4] << 24 | message[5] << 16 | message[6] << 8 | message[7], message[8] << 24]
        else:
            words = [message[0] | message[1] << 8 | message[2] << 16 | message[3] << 24,
                     message[4] | message[5] << 8 | message[6] << 16 | message[7] << 24, message[8]]
        program = [pins(0, 2, 0), ltim(1), lcfg(), load(X, init), crc_set(X), crc_preset(v.preset),
                   PULL, xline(32, msb=msb, drive=True, crc=True), PULL,
                   xline(32, msb=msb, drive=True, crc=True), PULL, xline(8, msb=msb, drive=True, crc=True),
                   crc_get(RX), PUSH, HALT]
        await load_and_start(h, 0, program, ownership=0x04, tx=words)
        await run_to_halt(h, 0, 2000, f"{v.name} program")
        (register,) = await read_rx(h, 0, 1)
        value = (register >> (16 - v.width) if msb else register & ((1 << v.width) - 1)) ^ v.xorout
        assert value == v.check, f"{v.name}: unit {value:#x} (register {register:#06x}) != {v.check:#x}"
        assert fault_of(await engine_status(h, 0)) == 0
        h.log("CRC %s: %#x (published %#x)", v.name, value, v.check)


async def crc_classic_and_sampled(h: Harness) -> None:
    """The CRC fed by a classic XFER (c bit 6): a drive-only SPI-style transfer
    feeds the shifted-out bits, a sampling one the sampled bits. And by a
    sample-only line XFER that receives the message from the environment.
    Expected: CRC-16/IBM-3740 (CCITT-FALSE) 0x29B1 and CRC-16/KERMIT 0x2189."""
    await h.start()
    message = list(b"123456789")
    # classic drive-only, MSB first, preset 3 init 0xFFFF: CCITT-FALSE
    words = [message[0] << 24 | message[1] << 16 | message[2] << 8 | message[3],
             message[4] << 24 | message[5] << 16 | message[6] << 8 | message[7], message[8] << 24]
    program = [pins(1, 2, 0), load(X, 0xFFFF), crc_set(X), crc_preset(3),
               PULL, xfer(32, 1, 0x4C), PULL, xfer(32, 1, 0x4C), PULL, xfer(8, 1, 0x4C),
               crc_get(RX), PUSH, HALT]
    await load_and_start(h, 0, program, ownership=0x06, tx=words)
    await run_to_halt(h, 0, 2000, "classic CRC program")
    (value,) = await read_rx(h, 0, 1)
    assert value == 0x29B1, f"classic drive-only CRC {value:#x} != 0x29B1"
    # classic sampling (mode 0, LSB first) of pin 3, which the environment
    # drives with the message bits relative to the XFER's own clock pin
    bits = [b for byte in message for b in bits_lsb(byte, 8)]
    state = {"k": 0, "last_clock": 0}

    def external(cycle: int, pads: Pads) -> int:
        # the data bit changes on each falling clock edge seen on pin 1
        clock = pads.pad(cycle, 1, 0)
        if state["last_clock"] == 1 and clock == 0:
            state["k"] += 1
        state["last_clock"] = clock
        k = state["k"]
        return 0xF7 | ((bits[k] if k < len(bits) else 1) << 3)

    h.pins = Pads(external)
    program = [pins(1, 2, 3), dir_(0x02), load(X, 0), crc_set(X), crc_preset(3),
               xfer(32, 4, 0x50), xfer(32, 4, 0x50), xfer(8, 4, 0x50), crc_get(RX), PUSH, HALT]
    await load_and_start(h, 0, program, ownership=0x02)
    await run_to_halt(h, 0, 4000, "classic sampling CRC program")
    (value,) = await read_rx(h, 0, 1)
    assert value == 0x2189, f"classic sampled CRC {value:#x} != 0x2189 (CRC-16/KERMIT)"
    # line sample-only: NRZ cells of 2P cycles after a start cell on pin 3
    period = 6
    cells = [0] + bits  # start cell (0), then the message LSB first
    start = {"at": None}

    def line_source(cycle: int, pads: Pads) -> int:
        if start["at"] is None or cycle < start["at"]:
            level = 1
        else:
            k = (cycle - start["at"]) // (2 * period)
            level = cells[k] if k < len(cells) else 1
        return 0xF7 | level << 3

    h.pins = Pads(line_source)
    program = [pins(0, 0, 3), lcfg(), load(X, 0), crc_set(X), crc_preset(3), waitpin(3, 0),
               ltim(period, delay=2 * period - 3)] + [xline(8, sample=True, crc=True)] * 9 + [
               crc_get(RX), PUSH, HALT]
    await load_and_start(h, 0, program)
    await h.idle(20)
    start["at"] = h.cycle + 5
    await run_to_halt(h, 0, 2 * period * 100, "line sampling CRC program")
    (value,) = await read_rx(h, 0, 1)
    assert value == 0x2189, f"line sampled CRC {value:#x} != 0x2189 (CRC-16/KERMIT)"
    assert fault_of(await engine_status(h, 0)) == 0


# ------------------------------------------------------------------ C. ticker
TICKER_CASES = [  # (P, Q, D, bits)
    (1, 0, 2, 32), (2, 0, 0, 32), (2, 128, 2, 32), (3, 85, 5, 32), (13, 85, 7, 32),
    (16, 171, 33, 32), (7, 255, 9, 32), (255, 0, 255, 4), (1, 1, 3, 32), (4, 200, 1, 16),
]


async def ticker_timing(h: Harness, cases: list[tuple[int, int, int, int]] = TICKER_CASES) -> None:
    """LTIM P/Q/D: every bit boundary of a drive-only line XFER of 0x55555555
    (a pad transition at every boundary) lands where the ticker formula puts it."""
    for period, frac, delay, nbits in cases:
        await h.start()
        pads = Pads(lambda cycle, pads: 0xF7 | (1 << 3 if marker["at"] is not None and cycle >= marker["at"] else 0))
        marker: dict[str, int | None] = {"at": None}
        h.pins = pads
        program = [pins(0, 0, 0), lcfg(), set_(0), dir_(1), load(TX, 0x5555), mov(X, TX), shl(X, 16),
                   or_(TX, X), waitpin(3, 1), ltim(period, frac, delay), xline(nbits, drive=True),
                   wait(2 * period + 8), HALT]
        await load_and_start(h, 0, program, ownership=0x01)
        await h.idle(16)
        marker["at"] = h.cycle + 3
        c0 = marker["at"] + 3
        await run_to_halt(h, 0, (2 * period + 2) * (nbits + 4) + 600, f"ticker P={period}")
        tick_list = ticks(c0, period, frac, delay, 2 * nbits + 6)
        # the XFER is armed from c0 + 2: its first boundary is the first even tick at or after that
        first = next(k for k in range(0, len(tick_list), 2) if tick_list[k] >= c0 + 2)
        expected = [tick_list[first + 2 * j] + 1 for j in range(nbits)]
        seen = [t for t in range(c0, c0 + expected[-1] - c0 + 2)
                if pads.pad(t, 0, 0) != pads.pad(t - 1, 0, 0)]
        assert seen[:nbits] == expected, (f"P={period} Q={frac} D={delay}: transitions "
                                          f"{seen[:6]}... expected {expected[:6]}...")
        assert fault_of(await engine_status(h, 0)) == 0
    # P = 0 stops the ticker; P = 255 with a fraction is an invalid operand
    await h.start()
    program = [ltim(9), lstat(X), ltim(0), lstat(Y), mov(RX, X), PUSH, mov(RX, Y), PUSH, HALT]
    await load_and_start(h, 0, program)
    await run_to_halt(h, 0, 200, "LTIM 0 program")
    running, stopped = await read_rx(h, 0, 2)
    assert running & ST_RUN and not stopped & ST_RUN, f"LSTAT run bit {running:#x} / {stopped:#x}"
    await load_and_start(h, 0, [ltim(255), ltim(255 | 1 << 8), HALT])
    await h.idle(10)
    status = await engine_status(h, 0)
    assert fault_of(status) == 1, f"LTIM P=255 Q=1: status {status:#x}"


# ------------------------------------------------------------------ D. TX line coding
@dataclass(frozen=True)
class TxCase:
    name: str
    data: int
    bits: int
    code: int = 0        # 0 NRZ, 1 NRZI, 2 Manchester
    stuff: int = 0       # run length 1..8, 0 = off
    either: bool = False  # stuffing on runs of either polarity (CAN), else runs of 1s
    init: int = 0        # LCFG initial line level (and idle level driven with SET)
    pair: bool = False
    msb: bool = False
    period: int = 3


TX_CASES = [
    TxCase("NRZ, 32 bits LSB first", 0xC3A5_0F1E, 32),
    TxCase("NRZ, 17 bits MSB first, P=5", 0x8001_F0A5, 17, msb=True, period=5),  # sends bits 31..15
    TxCase("NRZI, stuffing after six 1s, trailing stuff bit (USB)", 0x3FFF_FFFF, 30, code=1, stuff=6),
    TxCase("NRZI, six 1s stuffing, pair, idle J (init 1)", 0x7E7E_FF01, 32, code=1, stuff=6, init=1, pair=True),
    TxCase("NRZ, stuffing after 5 equal bits, trailing stuff bit (CAN)", 0b0000011111 << 22, 10, stuff=5,
           either=True, init=1, msb=True),
    TxCase("NRZ, 5 equal bits stuffing, 32 bits MSB first", 0x00FF_00F0, 32, stuff=5, either=True, init=1, msb=True),
    TxCase("NRZ, stuffing after 2 equal bits (either polarity)", 0xB4C3, 16, stuff=2, either=True),
    TxCase("NRZ, stuffing after eight 1s (run 8)", 0xFFFF_0FFF, 32, stuff=8),
    TxCase("NRZI, stuffing after every 1 (run 1, ones)", 0x0000_F00D, 16, code=1, stuff=1),
    TxCase("Manchester, pair, P=2 (10BASE-T)", 0xD555_5555, 32, code=2, pair=True, period=2),
    TxCase("Manchester, P=5, MSB first", 0x1234_5678, 32, code=2, msb=True, period=5),
]


def shifted_out(value: int, n: int, msb: bool) -> list[int]:
    """The n bits a 32-bit TX register sends: bits 31, 30, ... MSB first, else 0, 1, ..."""
    return bits_msb(value, 32)[:n] if msb else bits_lsb(value, n)


def reference_cells(case: TxCase) -> list[int]:
    """Line bits of the case (data, then stuff bits), from the independent encoders."""
    bits = shifted_out(case.data, case.bits, case.msb)
    if case.stuff:
        bits = stuff_either(case.stuff, bits) if case.either else stuff_ones(case.stuff, bits)
    return bits


async def tx_line_coding(h: Harness, cases: list[TxCase] = TX_CASES) -> None:
    """Drive-only line XFERs: the data pin (and the pair pin) sampled in every
    cell equals the independent encoding (stuffing, then NRZ/NRZI/Manchester)."""
    for case in cases:
        await h.start()
        marker: dict[str, int | None] = {"at": None}
        pads = Pads(lambda cycle, pads: 0xF7 | (8 if marker["at"] is not None and cycle >= marker["at"] else 0))
        h.pins = pads
        idle = case.init | ((1 - case.init) << 1 if case.pair else 0)
        program = [pins(1, 0, 0), lcfg(case.code, case.stuff, case.either, case.pair, init=case.init),
                   set_(idle), dir_(3 if case.pair else 1), load(TX, case.data & 0xFFFF),
                   load(X, case.data >> 16), shl(X, 16), or_(TX, X), waitpin(3, 1),
                   ltim(case.period, 0, 4), xline(case.bits, msb=case.msb, drive=True),
                   wait(6 * case.period + 8), HALT]
        await load_and_start(h, 0, program, ownership=3 if case.pair else 1)
        await h.idle(16)
        marker["at"] = h.cycle + 3
        c0 = marker["at"] + 3
        cells = reference_cells(case)
        await run_to_halt(h, 0, 2 * case.period * (len(cells) + 8) + 300, case.name)
        tick_list = ticks(c0, case.period, 0, 4, 2 * len(cells) + 4)
        p = case.period
        got, first_half, pair_ok = [], [], True
        for j in range(len(cells)):
            start = tick_list[2 * j] + 1
            if case.code == 2:
                first_half.append(pads.pad(start + p // 2, 0))
                sample = start + p + p // 2
            else:
                sample = start + p
            got.append(pads.pad(sample, 0))
            if case.pair:
                pair_ok &= pads.pad(sample, 1) == 1 - got[-1]
                if case.code == 2:
                    pair_ok &= pads.pad(start + p // 2, 1) == 1 - first_half[-1]
        if case.code == 0:
            expected = cells
        elif case.code == 1:
            expected = nrzi_encode(case.init, cells)
        else:
            expected = cells  # second half = the bit
            assert first_half == [1 - b for b in cells], f"{case.name}: Manchester first halves {first_half}"
        assert got == expected, f"{case.name}: line {got} != reference {expected}"
        assert pair_ok, f"{case.name}: pair pin is not the complement"
        if case.code == 1:
            data = (destuff_ones(case.stuff, nrzi_decode(case.init, got))[0] if case.stuff
                    else nrzi_decode(case.init, got))
            want = shifted_out(case.data, case.bits, case.msb)
            assert data == want, f"{case.name}: decoded {data}"
        assert fault_of(await engine_status(h, 0)) == 0
        h.log("TX %s: %d cells match", case.name, len(cells))


# ------------------------------------------------------------------ E. RX decoding
@dataclass(frozen=True)
class RxCase:
    name: str
    data: int
    bits: int
    code: int = 0
    stuff: int = 0
    either: bool = False
    msb: bool = False
    period: int = 4
    pair: bool = False
    se0_after: int | None = None  # SE0 on both pair pins after this many data cells
    bad_stuff: int | None = None  # invert the k-th stuff bit
    crc: bool = False


RX_CASES = [
    RxCase("NRZ, 32 bits LSB first", 0x5A5A_C3E1, 32),
    RxCase("NRZ, 11 bits MSB first", 0x5A3, 11, msb=True),
    RxCase("NRZI, six 1s destuffing (USB), pair", 0xFFFF_FF7E, 32, code=1, stuff=6, pair=True),
    RxCase("NRZI, destuffing a trailing stuff bit", 0x3F, 6, code=1, stuff=6),
    RxCase("NRZ, 5 equal bits destuffing (CAN), MSB first", 0x07E0_001F, 32, stuff=5, either=True, msb=True),
    RxCase("NRZ, 5 equal bits destuffing, trailing stuff bit", 0b11111, 5, stuff=5, either=True),
    RxCase("NRZI, wrong stuff bit (stuff error)", 0x0000_FFFF, 24, code=1, stuff=6, bad_stuff=1),
    RxCase("NRZ, wrong CAN stuff bit (stuff error)", 0x0003_E0F8, 24, stuff=5, either=True, bad_stuff=2),
    RxCase("NRZI, SE0 after 13 of 32 bits (pair)", 0x1234_5678, 32, code=1, pair=True, se0_after=13),
    RxCase("NRZI, SE0 after 0 of 8 bits (pair)", 0xA5, 8, code=1, pair=True, se0_after=0),
    RxCase("NRZ, P=7, CRC fed by sampled bits", 0xDEAD_BEEF, 32, period=7, crc=True),
]


def rx_cells(case: RxCase) -> tuple[list[int], list[int]]:
    """(line levels per cell after the start cell, destuffed data bits)."""
    data = bits_msb(case.data, case.bits) if case.msb else bits_lsb(case.data, case.bits)
    bits = data
    if case.stuff:
        stuffed = stuff_either(case.stuff, data) if case.either else stuff_ones(case.stuff, data)
        if case.bad_stuff is not None:
            # index of every stuff bit in the stuffed stream (the stuffing rule again)
            stuff_at, run, last, k = [], 0, None, 0
            for b in data:
                run = (run + 1 if b == last else 1) if case.either else (run + 1 if b else 0)
                last = b
                k += 1
                if run == case.stuff:
                    stuff_at.append(k)
                    k += 1
                    last, run = (1 - b, 1) if case.either else (last, 0)
            index = stuff_at[case.bad_stuff]
            assert stuffed[index] == (1 - stuffed[index - 1] if case.either else 0)
            stuffed = stuffed[:index] + [1 - stuffed[index]] + stuffed[index + 1:]
        bits = stuffed
    levels = nrzi_encode(0, bits) if case.code == 1 else list(bits)
    return levels, data


async def rx_line_decoding(h: Harness, cases: list[RxCase] = RX_CASES) -> None:
    """Sample-only line XFERs of a waveform from the independent encoders: the
    received word, the stuff-error flag, SE0 end with the remaining bit count,
    and the CRC of the sampled bits."""
    for case in cases:
        await h.start()
        levels, data = rx_cells(case)
        p = case.period
        start: dict[str, int | None] = {"at": None}
        se0_cell = None if case.se0_after is None else case.se0_after  # data cells before SE0 (no stuffing)

        def source(cycle: int, pads: Pads) -> int:
            if start["at"] is None or cycle < start["at"]:
                d, dm = 1, 0  # idle: J-like (data 1, pair 0)
            else:
                k = (cycle - start["at"]) // (2 * p) - 1  # cell -1 is the start cell (level 0)
                if k < 0:
                    d, dm = 0, 1
                elif se0_cell is not None and k >= se0_cell:
                    d, dm = 0, 0
                elif k < len(levels):
                    d, dm = levels[k], 1 - levels[k]
                else:
                    d, dm = 1, 0
            return 0xE7 | d << 3 | dm << 4

        pads = Pads(source)
        h.pins = pads
        program = [pins(4, 0, 3), lcfg(case.code, case.stuff, case.either, case.pair,
                                       se0=case.se0_after is not None, init=0),
                   load(X, 0xFFFF), crc_set(X), crc_preset(3), waitpin(3, 0), ltim(p, 0, 2 * p - 3),
                   xline(case.bits, msb=case.msb, sample=True, crc=case.crc), lstat(X), PUSH, mov(RX, X), PUSH,
                   crc_get(RX), PUSH, HALT]
        await load_and_start(h, 0, program)
        await h.idle(20)
        start["at"] = h.cycle + 5
        await run_to_halt(h, 0, 2 * p * (len(levels) + 10) + 400, case.name)
        rx, status, crc = await read_rx(h, 0, 3)
        assert fault_of(await engine_status(h, 0)) == 0
        received = case.bits if case.se0_after is None else case.se0_after
        if case.msb:
            value = rx & ((1 << received) - 1) if received else 0
            want = from_bits_msb(data[:received])
        else:
            value = rx >> (32 - received) if received else 0
            want = from_bits_lsb(data[:received])
        # A wrong CAN stuff bit also desynchronizes the run count, so only
        # the flag is checked there; a wrong USB stuff bit (1 for 0) is not.
        if not (case.bad_stuff is not None and case.either):
            assert value == want, f"{case.name}: received {value:#x} != {want:#x} (rx {rx:#010x})"
        assert bool(status & ST_STUFF_ERR) == (case.bad_stuff is not None), f"{case.name}: status {status:#x}"
        if case.se0_after is not None:
            assert status & ST_SE0 and status >> 8 & 63 == case.bits - case.se0_after, \
                f"{case.name}: SE0 status {status:#x}"
        else:
            assert not status & ST_SE0, f"{case.name}: status {status:#x}"
        if case.crc:
            bits = data  # LSB first, CRC-16/X-25 register (preset 3, init 0xFFFF)
            want_crc = crc_bits_reflected(bits, poly=0x8408, init=0xFFFF)
            assert crc == want_crc, f"{case.name}: CRC {crc:#06x} != {want_crc:#06x}"
        h.log("RX %s: %#x status %#x", case.name, value, status)


# ------------------------------------------------------------------ F. arbitration
async def arbitration(h: Harness) -> None:
    """Drive-and-sample line XFERs with the arbitration monitor on a modelled
    wired-AND bus (TXD pin 0, RXD pin 1): another node forces the bus dominant
    in one cell. When that cell carries a recessive (1) bit of ours, LSTAT
    reports the loss and TXD stays recessive from the next cell on; the RX
    register holds the bus. When our bit is dominant anyway, nothing is lost.
    Without the monitor enabled nothing is lost either."""
    p, n = 8, 16
    ident = 0b1011_0011_1100_0101  # sent MSB first from the top of TX
    for forced, arb in ((1, True), (4, True), (2, True), (0, True), (0, False)):
        await h.start()
        marker: dict[str, int | None] = {"at": None}
        window: dict[str, tuple[int, int] | None] = {"cell": None}

        def bus(cycle: int, pads: Pads) -> int:
            ours = pads.pad(cycle, 0, 1)
            dominant = window["cell"] is not None and window["cell"][0] <= cycle < window["cell"][1]
            level = ours & (0 if dominant else 1)
            mark = 8 if marker["at"] is not None and cycle >= marker["at"] else 0
            return 0xF5 | level << 1 | mark

        pads = Pads(bus)
        h.pins = pads
        program = [pins(0, 0, 1), lcfg(arb=arb, init=1), set_(1), dir_(1), load(TX, ident), shl(TX, 16),
                   waitpin(3, 1), ltim(p, 0, 4), xline(n, msb=True, drive=True, sample=True), lstat(X),
                   PUSH, mov(RX, X), PUSH, wait(4 * p), HALT]
        await load_and_start(h, 0, program, ownership=0x01)
        await h.idle(16)
        marker["at"] = h.cycle + 3
        c0 = marker["at"] + 3
        tick_list = ticks(c0, p, 0, 4, 2 * n + 4)
        window["cell"] = (tick_list[2 * forced] + 1, tick_list[2 * forced + 2] + 1)
        await run_to_halt(h, 0, 2 * p * (n + 8) + 300, "arbitration program")
        rx, status = await read_rx(h, 0, 2)
        sent = bits_msb(ident, n)
        lost = arb and sent[forced] == 1
        assert bool(status & ST_LOST) == lost, f"forced cell {forced}, arb {arb}: status {status:#x}"
        line = [pads.pad(tick_list[2 * j] + 1 + p, 0) for j in range(n)]
        want_line = [b if not lost or j <= forced else 1 for j, b in enumerate(sent)]
        assert line == want_line, f"forced cell {forced}: TXD {line} != {want_line}"
        bus_bits = [0 if j == forced else want_line[j] for j in range(n)]
        assert rx & 0xFFFF == from_bits_msb(bus_bits), f"forced cell {forced}: rx {rx:#x}"
        h.log("arbitration: dominant forced in cell %d (our bit %d, monitor %s): lost %s", forced, sent[forced],
              arb, lost)


# ------------------------------------------------------------------ G. invalid encodings
def invalid_cases() -> list[tuple[str, list[int], bool]]:
    """(name, program, faults): the last instruction is the one under test;
    ``faults`` says whether it must fault with code 1 (else it completes)."""
    ok_setup = [pins(1, 0, 3), ltim(5)]
    cases = [
        ("LTIM P=255 Q=1", [ltim(255, 1)], True),
        ("LTIM P=255 Q=0", [ltim(255)], False),
        ("LTIM P=254 Q=255 D=255", [ltim(254, 255, 255)], False),
        ("LCFG line code 3", [lcfg(code=3)], True),
        ("LCFG bit 11", [imm(31, 1 << 11)], True),
        ("LCFG bit 23", [imm(31, 1 << 23)], True),
        ("LCFG all fields, code 2", [imm(31, 0x7FE)], False),
        ("LCFG either polarity, run 1", [lcfg(stuff=1, either=True)], True),
        ("LCFG ones, run 1", [lcfg(stuff=1)], False),
        ("LCFG either polarity, run 2", [lcfg(stuff=2, either=True)], False),
        ("CRC c=0", [ins(32, 0, 1, 0)], True),
        ("CRC c=1 a=1", [ins(32, 1, 1, 1)], True),
        ("CRC c=1 b=4", [ins(32, 0, 4, 1)], True),
        ("CRC c=2 a=4", [ins(32, 4, 0, 2)], True),
        ("CRC c=2 b=1", [ins(32, 1, 1, 2)], True),
        ("CRC c=3 a=1", [ins(32, 1, 0, 3)], True),
        ("CRC c=3 b=4", [ins(32, 0, 4, 3)], True),
        ("CRC c=4", [ins(32, 0, 0, 4)], True),
        ("CRC c=3 b=3", [crc_preset(3)], False),
        ("LSTAT a=4", [ins(33, 4)], True),
        ("LSTAT b=1", [ins(33, 0, 1)], True),
        ("LSTAT c=1", [ins(33, 0, 0, 1)], True),
        ("LSTAT a=3", [lstat(3)], False),
        ("line XFER, ticker stopped", [pins(1, 0, 3), xline(8, sample=True)], True),
        ("line XFER, ticker stopped by LTIM 0", ok_setup + [ltim(0), xline(8, sample=True)], True),
        ("line XFER, b=1", ok_setup + [xfer(8, 1, 0x30)], True),
        ("line XFER, c bit 0", ok_setup + [xfer(8, 0, 0x31)], True),
        ("line XFER, c bit 1", ok_setup + [xfer(8, 0, 0x32)], True),
        ("line XFER, c bit 7", ok_setup + [xfer(8, 0, 0xB0)], True),
        ("line XFER, a=0", ok_setup + [xfer(0, 0, 0x30)], True),
        ("line XFER, a=33", ok_setup + [xfer(33, 0, 0x30)], True),
        ("line XFER, Manchester with sampling", ok_setup + [lcfg(code=2), xline(8, drive=True, sample=True)], True),
        ("line XFER, Manchester sample only", ok_setup + [lcfg(code=2), xline(8, sample=True)], True),
        ("line XFER, drive, data pin not owned", ok_setup + [pins(1, 2, 3), xline(8, drive=True)], True),
        ("line XFER, pair drive, pair pin not owned", ok_setup + [pins(4, 0, 3), lcfg(pair=True),
                                                                 xline(8, drive=True)], True),
        ("line XFER, pair drive, pair pin = data pin", ok_setup + [pins(0, 0, 3), lcfg(pair=True),
                                                                  xline(8, drive=True)], True),
        ("line XFER, sample only, nothing owned", ok_setup + [pins(4, 5, 3), xline(1, sample=True)], False),
        ("classic XFER, c bit 7", [pins(1, 0, 3), xfer(8, 2, 0x80)], True),
        ("classic XFER, c bit 5 (line) with b=2", ok_setup + [xfer(8, 2, 0x28)], True),
        ("classic XFER with the CRC bit", [pins(1, 0, 3), xfer(1, 2, 0x48)], False),
        ("opcode 34", [imm(34)], True),
        ("opcode 63", [imm(63)], True),
        ("opcode 255", [imm(255)], True),
    ]
    return cases


async def invalid_encodings(h: Harness) -> None:
    """Each invalid encoding faults with code 1 at its own PC; each valid
    neighbour completes (docs/extension.md, "Invalid encodings")."""
    await h.start()
    for name, program, faults in invalid_cases():
        words = program + [HALT]
        await load_and_start(h, 0, words, ownership=0x03)
        await h.idle(40)
        status = await engine_status(h, 0)
        pc = await h.status(RS_PC)
        if faults:
            assert fault_of(status) == 1 and pc == len(program) - 1, f"{name}: status {status:#x} pc {pc}"
        else:
            assert fault_of(status) == 0 and not status & 1, f"{name}: status {status:#x} pc {pc}"
        await h.command(STOP, 1)
        await h.command(7, 1)  # CLEAR the fault
    h.log("%d encodings checked", len(invalid_cases()))


# ------------------------------------------------------------------ H. LSTAT
async def lstat_bits(h: Harness) -> None:
    """LSTAT bits 3 (TX queue has data), 4 (RX queue has space), 5 (line
    level), 6 (ticker running); the queue bits follow the queues without
    moving a word."""
    await h.start()
    depth = h.model.config.fifo_words
    # TX empty / non-empty; ticker off / on; level 0 / 1
    program = [lstat(X), mov(RX, X), PUSH, lcfg(init=1), ltim(3), lstat(X), mov(RX, X), PUSH, HALT]
    await load_and_start(h, 0, program)
    await run_to_halt(h, 0, 200, "LSTAT program 1")
    first, second = await read_rx(h, 0, 2)
    assert first & 0x7F == ST_RX_SPACE, f"LSTAT after START: {first:#x}"
    assert second & 0x7F == ST_RX_SPACE | ST_LEVEL | ST_RUN, f"LSTAT after LCFG/LTIM: {second:#x}"
    await load_and_start(h, 0, [lstat(X), mov(RX, X), PUSH, HALT], tx=[0x1234])
    await run_to_halt(h, 0, 200, "LSTAT program 2")
    (third,) = await read_rx(h, 0, 1)
    assert third & ST_TX_DATA and third & ST_RX_SPACE, f"LSTAT with a TX word: {third:#x}"
    # RX full: fill the queue, then LSTAT, then push the status once there is space
    program = [PUSH] * depth + [lstat(X), mov(RX, X), PUSH, HALT]
    await load_and_start(h, 0, program)
    await h.idle(3 * depth + 40)
    values = await read_rx(h, 0, depth + 1)
    assert not values[-1] & ST_RX_SPACE, f"LSTAT with a full RX queue: {values[-1]:#x}"
    assert fault_of(await engine_status(h, 0)) == 0


# ------------------------------------------------------------------ I. START, STOP, reset
async def start_clears(h: Harness) -> None:
    """START clears the unit: after a program that sets the ticker, LCFG, the
    CRC, its preset and flags (a lost arbitration), a second START reads
    everything back as zero. A reset in the middle of a line XFER releases the
    pins; so does STOP."""
    await h.start()
    h.pins = Pads(lambda cycle, pads: 0xFD)  # RXD (pin 1) dominant: the XFER loses at once
    program = [pins(2, 0, 1), set_(1), dir_(1), lcfg(code=1, stuff=3, either=True, pair=True, arb=True, init=1),
               ltim(4, 77, 9), load(X, 0xBEEF), crc_set(X), crc_preset(2), not_(TX),
               xline(4, drive=True, sample=True), HALT]
    await load_and_start(h, 0, program, ownership=0x05)
    await run_to_halt(h, 0, 300, "setup program")
    check = [lstat(X), mov(RX, X), PUSH, crc_get(RX), PUSH, HALT]
    await load_and_start(h, 0, check)
    await run_to_halt(h, 0, 200, "check program")
    status, crc = await read_rx(h, 0, 2)
    assert status & ~(ST_RX_SPACE | ST_TX_DATA) == 0 and crc == 0, f"after START: status {status:#x} crc {crc:#x}"
    # STOP and reset in the middle of a line XFER release the pins
    h.pins = Pads()
    long_xfer = [pins(1, 0, 3), set_(1), dir_(3), lcfg(code=2, pair=True), ltim(20), not_(TX),
                 xline(32, drive=True), HALT]
    await load_and_start(h, 0, long_xfer, ownership=0x03)
    await h.idle(200)
    assert h.last_pre.uio_oe & 3 == 3
    await h.command(STOP, 1)
    await h.idle(2)
    assert h.last_pre.uio_oe == 0, "STOP did not release the pins"
    await load_and_start(h, 0, long_xfer, ownership=0x03)
    await h.idle(200)
    assert h.last_pre.uio_oe & 3 == 3
    await h.reset(2)
    await h.idle(2)
    assert h.last_pre.uio_oe == 0, "reset did not release the pins"
    await h.command(SELECT, 0)
    assert await h.status(RS_STATUS) == 0


# ------------------------------------------------------------------ J. ticker sharing
async def classic_stops_ticker(h: Harness) -> None:
    """A classic XFER takes over the shared tick/period registers and stops
    the ticker: LSTAT bit 6 clears and the next line XFER faults with code 1."""
    await h.start()
    program = [pins(1, 0, 3), ltim(6), lstat(X), mov(RX, X), PUSH, xfer(2, 3, 0x00), lstat(X), mov(RX, X), PUSH,
               xline(4, sample=True), HALT]
    await load_and_start(h, 0, program, ownership=0x02)
    await h.idle(120)
    before, after = await read_rx(h, 0, 2)
    assert before & ST_RUN and not after & ST_RUN, f"LSTAT {before:#x} / {after:#x}"
    status = await engine_status(h, 0)
    assert fault_of(status) == 1 and await h.status(RS_PC) == 9, f"status {status:#x}"


# ------------------------------------------------------------------ K. completion points
async def completion_points(h: Harness) -> None:
    """Where a line XFER completes (docs/extension.md): drive-only at the
    boundary tick of its last cell, sample-only and drive+sample at the mid
    tick of their last bit. The instruction after it (TIME) records the
    cycle; the difference to the ticker formula's tick is fixed."""
    p = 10
    results = {}
    for name, flags in (("drive", dict(drive=True)), ("sample", dict(sample=True)),
                        ("drive+sample", dict(drive=True, sample=True))):
        await h.start()
        marker: dict[str, int | None] = {"at": None}
        h.pins = Pads(lambda cycle, pads: 0xF7 | (8 if marker["at"] is not None and cycle >= marker["at"] else 0))
        program = [pins(1, 0, 4), lcfg(), dir_(1), waitpin(3, 1), ltim(p, 0, 4), ins(28, X), xline(3, **flags),
                   ins(28, Y), mov(RX, X), PUSH, mov(RX, Y), PUSH, HALT]
        await load_and_start(h, 0, program, ownership=0x01)
        await h.idle(16)
        marker["at"] = h.cycle + 3
        c0 = marker["at"] + 3
        await run_to_halt(h, 0, 400, name)
        before, after = await read_rx(h, 0, 2)
        tick_list = ticks(c0, p, 0, 4, 10)
        # TIME issues in cycle c0 + 1; the TIME after the XFER issues in the
        # cycle after the XFER's completion cycle T: T = c0 + (after - before)
        completion = c0 + (after - before)
        last = tick_list[4] if name == "drive" else tick_list[5]
        assert completion == last, f"{name}: completes in cycle {completion}, expected tick {last}"
        results[name] = after - before
    h.log("XFER 3 bits at P=%d: TIME differences %s", p, results)
