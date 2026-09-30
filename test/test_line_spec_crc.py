# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The line unit's CRC on every engine, for every preset, bit order and feed
source (docs/isa.md, "Line-unit extension").

Specified behaviour (docs/isa.md):

* op 32 CRC: "c = 1: CRC := register b (low 16 bits), a = 0. c = 2:
  register a := CRC, b = 0. c = 3: polynomial preset b (0..3), a = 0."
* "CRC. One step per data bit: LSB first, feedback = crc[0] ^ bit and
  crc := (crc >> 1) ^ (feedback ? reflected polynomial : 0); MSB first,
  feedback = crc[15] ^ bit and crc := (crc << 1) ^ (feedback ? normal
  polynomial : 0). Presets: 0 CRC-5/USB (reflected 0x0014, normal 0x2800),
  1 CRC-16 (0xA001, 0x8005), 2 CRC-15/CAN (0x4CD1, 0x8B32), 3 CRC-16/CCITT
  (0x8408, 0x1021). A driving XFER feeds the data bits it sends, a sampling
  XFER the data bits it receives; a classic XFER with c bit 6 feeds the bits it
  shifts out, or the bits it samples."
* op 17 XFER: "c bit 6 feeds the CRC (also in a classic XFER)"; a line XFER
  "counts data bits"; the option "adds one line unit per engine".
* "Line XFER": a sampling XFER completes "at a mid-bit tick with both pair pins
  low when SE0 end is set"; the SE0 cell is not a data bit.

``crc_rule`` below is that step rule and preset table, written from the ISA
text. Each test runs on all four engines, with all four presets and both bit
orders: a driven line XFER, a sampled line XFER, a driven classic XFER and a
sampled classic XFER, each feeding the CRC; the CRC is set from and read into
each of the four registers (the read-back replaces a register value with high
bits set, so the read must zero-extend the 16-bit CRC). The value read back
must equal ``crc_rule`` over the bits sent or received. A sampling XFER that
ends on SE0 must leave the CRC of the data bits received before the SE0. The harness compares
the DUT with the reference model on every cycle.

At gate level (PE_GATE_LEVEL) the module is skipped unless PE_LINE_GL_FULL=1, as the long
line-unit demos are.
"""

from __future__ import annotations

import random

import cocotb

from harness import CocotbHarness, Harness
from line_scenarios import Pads, RxCase, engine_status, load_and_start, read_rx, run_to_halt, rx_cells
from line_support import (HALT, PULL, PUSH, RX, SKIP_REASON, TX, X, Y, crc_get, crc_preset,
                          crc_set, dir_, lcfg, line_unit_enabled, load, ltim, mov, pins, waitpin, xfer, xline)
from scenarios import fault_of
from test_line_spec_common import SKIP, crc_rule, sent_bits

if not line_unit_enabled():
    print(f"test_line_spec_crc: {SKIP_REASON}")

REGISTERS = (TX, RX, X, Y)
ORDERS = [(preset, msb) for preset in range(4) for msb in (False, True)]


def transfer_block(case: dict, feed: int) -> list[int]:
    """PULL the initial value into TX, CRC := <src>, select the preset, PULL the data,
    run the feeding transfer ``feed``, overwrite <dst> with a 32-bit value, <dst> := CRC, push it."""
    src, dst = case["src"], case["dst"]
    words = [PULL]
    if src != TX:
        words.append(mov(src, TX))
    words += [crc_set(src), crc_preset(case["preset"]), PULL, feed]
    if dst != TX:
        words.append(mov(dst, TX))  # high bits of the shifted data word
    words.append(crc_get(dst))
    if dst != RX:
        words.append(mov(RX, dst))
    words.append(PUSH)
    return words


async def driven_programs(h: Harness, rng: random.Random, *, classic: bool) -> None:
    for engine in range(4):
        await h.start()
        data_pin, clock_pin = 2 * engine, 2 * engine + 1
        cases = []
        for k, (preset, msb) in enumerate(ORDERS):
            cases.append(dict(preset=preset, msb=msb, n=rng.choice((1, 5, 8, 13, 16, 17, 24, 31, 32)),
                              src=REGISTERS[(k + engine) % 4], dst=REGISTERS[(k + engine + 1) % 4],
                              init=rng.randrange(1 << 32), data=rng.randrange(1 << 32)))
        for group in (cases[:4], cases[4:]):
            if classic:  # drive-only classic XFER (mode 0) with c bit 6; it owns clock and data pins
                setup, ownership = [pins(clock_pin, data_pin, data_pin)], 3 << data_pin
                feeds = [xfer(c["n"], 1, 0x48 | 4 * c["msb"]) for c in group]
            else:  # drive-only line XFER at P = 1 with the CRC bit; the data pin is owned, not enabled
                setup, ownership = [pins(clock_pin, data_pin, data_pin), ltim(1), lcfg()], 1 << data_pin
                feeds = [xline(c["n"], msb=c["msb"], drive=True, crc=True) for c in group]
            program = setup + [w for c, f in zip(group, feeds) for w in transfer_block(c, f)] + [HALT]
            assert len(program) <= 64
            tx = [w for c in group for w in (c["init"], c["data"])]
            await load_and_start(h, engine, program, ownership=ownership, tx=tx)
            await run_to_halt(h, engine, 4000, f"engine {engine} CRC program")
            got = await read_rx(h, engine, len(group))
            assert fault_of(await engine_status(h, engine)) == 0
            for c, value in zip(group, got):
                want = crc_rule(sent_bits(c["data"], c["n"], c["msb"]), c["preset"], c["msb"], c["init"])
                assert value == want, (f"engine {engine} {'classic' if classic else 'line'} XFER, preset {c['preset']}, "
                                       f"{'MSB' if c['msb'] else 'LSB'} first, {c['n']} bits, CRC := r{c['src']}, "
                                       f"r{c['dst']} := CRC: read {value:#x}, rule {want:#06x}")
        h.log("engine %d: %d driven %s CRC cases match the rule", engine, len(cases), "classic" if classic else "line")


@cocotb.test(skip=SKIP)
async def test_crc_line_drive_presets_orders_engines(dut):
    """A driven line XFER feeds the bits it sends: every engine, preset and bit order, CRC set from and
    read into every register, against the ISA step rule."""
    await driven_programs(CocotbHarness(dut), random.Random(0x5EC0C1), classic=False)


@cocotb.test(skip=SKIP)
async def test_crc_classic_drive_presets_orders_engines(dut):
    """A driven classic XFER with c bit 6 feeds the bits it shifts out: every engine, preset and bit
    order, against the ISA step rule."""
    await driven_programs(CocotbHarness(dut), random.Random(0x5EC0C2), classic=True)


@cocotb.test(skip=SKIP)
async def test_crc_line_sample_presets_orders_engines(dut):
    """A sampling line XFER feeds the bits it receives: every engine, preset and bit order. The
    environment sends a start cell (0) and then NRZ cells of 2P cycles on the engine's RX pin; the
    program anchors on the start cell (WAITPIN) and starts the ticker so that each mid-bit tick falls
    inside a cell (as test_line_unit's RX scenarios do)."""
    h = CocotbHarness(dut)
    rng = random.Random(0x5EC0C3)
    period = 4
    for engine in range(4):
        rx_pin = 2 * engine + 1
        for preset, msb in ORDERS:
            await h.start()
            n = rng.choice((8, 13, 16, 32))
            bits = [rng.randrange(2) for _ in range(n)]
            init = rng.randrange(1 << 16)
            start: dict[str, int | None] = {"at": None}

            def source(cycle: int, pads: Pads, bits=bits) -> int:
                level = 1
                if start["at"] is not None and cycle >= start["at"]:
                    k = (cycle - start["at"]) // (2 * period) - 1  # cell -1 is the start cell
                    level = 0 if k < 0 else bits[k] if k < len(bits) else 1
                return (0xFF & ~(1 << rx_pin)) | level << rx_pin

            h.pins = Pads(source)
            program = [pins(2 * engine, 2 * engine, rx_pin), lcfg(), load(X, init), crc_set(X),
                       crc_preset(preset), waitpin(rx_pin, 0), ltim(period, 0, 2 * period - 3),
                       xline(n, msb=msb, sample=True, crc=True), crc_get(RX), PUSH, HALT]
            await load_and_start(h, engine, program)
            await h.idle(20)
            start["at"] = h.cycle + 5
            await run_to_halt(h, engine, 2 * period * (n + 12) + 400, f"engine {engine} sampling CRC")
            (value,) = await read_rx(h, engine, 1)
            assert fault_of(await engine_status(h, engine)) == 0
            want = crc_rule(bits, preset, msb, init)
            assert value == want, (f"engine {engine} sampled line XFER, preset {preset}, {'MSB' if msb else 'LSB'} "
                                   f"first, {n} bits: read {value:#x}, rule {want:#06x}")
        h.log("engine %d: sampled line CRC matches the rule for 8 preset/order pairs", engine)


@cocotb.test(skip=SKIP)
async def test_crc_classic_sample_presets_orders_engines(dut):
    """A sampling classic XFER (mode 0) with c bit 6 feeds the bits it samples: every engine, preset and
    bit order. The environment presents bit k after the k-th falling edge of the engine's clock pin;
    mode 0 samples on the rising edges."""
    h = CocotbHarness(dut)
    rng = random.Random(0x5EC0C4)
    for engine in range(4):
        clock_pin, rx_pin = 2 * engine + 1, 2 * engine
        for preset, msb in ORDERS:
            await h.start()
            n = rng.choice((8, 12, 16, 32))
            bits = [rng.randrange(2) for _ in range(n)]
            init = rng.randrange(1 << 16)
            state = {"k": 0, "last": 0}

            def external(cycle: int, pads: Pads, bits=bits, state=state) -> int:
                clock = pads.pad(cycle, clock_pin, 0)
                if state["last"] == 1 and clock == 0:
                    state["k"] += 1
                state["last"] = clock
                k = state["k"]
                return (0xFF & ~(1 << rx_pin)) | (bits[k] if k < len(bits) else 1) << rx_pin

            h.pins = Pads(external)
            program = [pins(clock_pin, rx_pin, rx_pin), dir_(1 << clock_pin), load(X, init), crc_set(X),
                       crc_preset(preset), xfer(n, 4, 0x50 | 4 * msb), crc_get(RX), PUSH, HALT]
            await load_and_start(h, engine, program, ownership=1 << clock_pin)
            await run_to_halt(h, engine, 8 * n + 400, f"engine {engine} classic sampling CRC")
            (value,) = await read_rx(h, engine, 1)
            assert fault_of(await engine_status(h, engine)) == 0
            want = crc_rule(bits, preset, msb, init)
            assert value == want, (f"engine {engine} sampled classic XFER, preset {preset}, "
                                   f"{'MSB' if msb else 'LSB'} first, {n} bits: read {value:#x}, rule {want:#06x}")
        h.log("engine %d: sampled classic CRC matches the rule for 8 preset/order pairs", engine)


@cocotb.test(skip=SKIP)
async def test_crc_sampled_until_se0_every_engine(dut):
    """A sampling line XFER with the CRC bit that ends on SE0 feeds only the data bits before the SE0:
    every engine, both bit orders, NRZI with the pair (test_line_unit's RX framing)."""
    h = CocotbHarness(dut)
    rng = random.Random(0x5EC0C5)
    for engine in range(4):
        for msb in (False, True):
            case = RxCase("SE0 with CRC", rng.randrange(1 << 32), 32, code=1, pair=True, msb=msb,
                          se0_after=rng.randrange(3, 29))
            preset, init = rng.randrange(4), rng.randrange(1 << 16)
            await h.start()
            levels, data = rx_cells(case)
            p = case.period
            start: dict[str, int | None] = {"at": None}

            def source(cycle: int, pads: Pads, levels=levels, case=case, p=p) -> int:
                d, dm = 1, 0
                if start["at"] is not None and cycle >= start["at"]:
                    k = (cycle - start["at"]) // (2 * p) - 1
                    if k < 0:
                        d, dm = 0, 1
                    elif k == case.se0_after:
                        d, dm = 0, 0
                    elif k < case.se0_after:
                        d, dm = levels[k], 1 - levels[k]
                return 0xE7 | d << 3 | dm << 4

            h.pins = Pads(source)
            program = [pins(4, 0, 3), lcfg(1, pair=True, se0=True), load(X, init), crc_set(X), crc_preset(preset),
                       waitpin(3, 0), ltim(p, 0, 2 * p - 3), xline(32, msb=msb, sample=True, crc=True),
                       crc_get(RX), PUSH, HALT]
            await load_and_start(h, engine, program)
            await h.idle(20)
            start["at"] = h.cycle + 5
            await run_to_halt(h, engine, 2 * p * 50 + 400, f"engine {engine} SE0 CRC")
            (value,) = await read_rx(h, engine, 1)
            assert fault_of(await engine_status(h, engine)) == 0
            want = crc_rule(data[:case.se0_after], preset, msb, init)
            assert value == want, (f"engine {engine}, SE0 after {case.se0_after} bits, preset {preset}, "
                                   f"{'MSB' if msb else 'LSB'} first: CRC {value:#06x}, rule {want:#06x}")
        h.log("engine %d: CRC of the bits before SE0 matches the rule", engine)
