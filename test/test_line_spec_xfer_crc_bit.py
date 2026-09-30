# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""An XFER changes the CRC only with c bit 6; with the bit, a drive-and-sample
XFER on a loopback feeds the bits it sends and receives, on every engine
(docs/isa.md, "Line-unit extension").

Specified behaviour (docs/isa.md): op 17 XFER "c bit 6 feeds the CRC (also in
a classic XFER)"; "CRC. One step per data bit ... A driving XFER feeds the data
bits it sends, a sampling XFER the data bits it receives; a classic XFER with
c bit 6 feeds the bits it shifts out, or the bits it samples." Without c bit 6
no bit is fed, so the CRC keeps its value. Classic XFER (docs/isa.md,
"Instructions"): "CPHA0 samples on active edges and shifts output for the next
bit on idle edges. CPHA1 drives/shifts on active edges and samples on idle
edges"; a line XFER that drives and samples "samples only after its first
boundary".

On every engine the XFER's data pin is also its RX pin and is enabled, so the
engine samples the level it drives (a loopback): the bits sent are the bits
received, and a drive-and-sample XFER with c bit 6 must leave the CRC of those
bits whichever of the two the unit feeds. Cases: classic XFERs in all four
modes (CPOL, CPHA) driving and sampling with c bit 6, and classic XFERs that
drive, sample or both without it; line XFERs that drive, sample or both
without c bit 6, and a line XFER that drives and samples with it, LSB and MSB
first. Checked: the CRC read back equals the ISA's CRC step rule over the bits
sent (with c bit 6) or its value before the XFER (without). The harness
compares the DUT with the reference model on every cycle.
"""

from __future__ import annotations

import random

import cocotb

from harness import CocotbHarness
from line_scenarios import Pads, engine_status, load_and_start, read_rx, run_to_halt
from line_support import (HALT, PULL, PUSH, RX, SKIP_REASON, X, crc_get, crc_preset, crc_set, dir_, lcfg,
                          line_unit_enabled, load, ltim, pins, set_, xfer, xline)
from scenarios import fault_of
from test_line_spec_common import SKIP, crc_rule, sent_bits

if not line_unit_enabled():
    print(f"test_line_spec_xfer_crc_bit: {SKIP_REASON}")


def cases(rng: random.Random) -> list[dict]:
    out = []
    for mode in range(4):  # classic, drive and sample, CRC bit, every mode
        out.append(dict(kind="classic", c=mode | 0x18 | 0x40, crc=True))
    for c in (0x08, 0x08 | 3, 0x10, 0x10 | 1, 0x18, 0x18 | 2):  # classic without the CRC bit
        out.append(dict(kind="classic", c=c, crc=False))
    for drive, sample in ((True, False), (False, True), (True, True)):  # line without the CRC bit
        out.append(dict(kind="line", drive=drive, sample=sample, crc=False))
    out.append(dict(kind="line", drive=True, sample=True, crc=True))
    for case in out:
        case["msb"] = rng.random() < 0.5
        # an odd bit count without the CRC bit: a change applied on every bit would not cancel out
        case["n"] = rng.choice((5, 8, 13, 16, 24, 32) if case["crc"] else (5, 13, 21, 31))
        case["preset"] = rng.randrange(4)
        case["init"] = rng.randrange(1 << 16)
        case["data"] = rng.randrange(1 << 32)
    return out


def block(case: dict) -> list[int]:
    if case["kind"] == "classic":
        feed = [xfer(case["n"], 3, case["c"] | 4 * case["msb"])]
    else:  # the classic XFERs stop the ticker: restart it; P = 3 so that a loopback sample sees the cell
        feed = [ltim(3), xline(case["n"], msb=case["msb"], drive=case["drive"], sample=case["sample"],
                               crc=case["crc"])]
    return [load(X, case["init"]), crc_set(X), crc_preset(case["preset"]), PULL] + feed + [crc_get(RX), PUSH]


@cocotb.test(skip=SKIP)
async def test_xfer_crc_bit_classic_and_line_loopback_every_engine(dut):
    """Every engine: XFERs without c bit 6 keep the CRC; drive-and-sample XFERs with it on a loopback
    leave the CRC step rule over the bits sent (classic in all four modes, line LSB/MSB first)."""
    h = CocotbHarness(dut)
    rng = random.Random(0x5EC0E0)
    for engine in range(4):
        data_pin, clock_pin = 2 * engine, 2 * engine + 1
        all_cases = cases(rng)
        for group in (all_cases[0:5], all_cases[5:10], all_cases[10:]):
            await h.start()
            h.pins = Pads(lambda cycle, pads: 0)  # undriven pads low; driven pads read back (loopback)
            program = [pins(clock_pin, data_pin, data_pin), lcfg(), set_(0), dir_(3 << data_pin)]
            for case in group:
                program += block(case)
            program.append(HALT)
            assert len(program) <= 64, len(program)
            await load_and_start(h, engine, program, ownership=3 << data_pin, tx=[c["data"] for c in group])
            await run_to_halt(h, engine, 8000, f"engine {engine} CRC-bit program")
            got = await read_rx(h, engine, len(group))
            assert fault_of(await engine_status(h, engine)) == 0
            for case, value in zip(group, got):
                if case["crc"]:
                    want = crc_rule(sent_bits(case["data"], case["n"], case["msb"]), case["preset"], case["msb"],
                                    case["init"])
                else:
                    want = case["init"]
                what = (f"engine {engine}, {case['kind']} XFER "
                        f"{'c=%#x' % case['c'] if case['kind'] == 'classic' else 'drive=%s sample=%s' % (case['drive'], case['sample'])}"
                        f"{' (MSB first)' if case['msb'] else ''}, {case['n']} bits, CRC bit {case['crc']}")
                assert value == want, f"{what}: CRC {value:#06x}, expected {want:#06x}"
        h.log("engine %d: %d XFERs with and without the CRC bit", engine, len(all_cases))
