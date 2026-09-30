# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""A driving line XFER writes the data pin named by PINS and, with the pair,
the pair pin, and no other pin, on every engine and for every pin number
(docs/isa.md, "Line-unit extension").

Specified behaviour (docs/isa.md, "Line XFER"): "Driving: at each boundary
tick the next line bit is written to the data pin (PINS TX field) and, with the
pair set, its complement to the pair pin (PINS clock field). NRZ writes the
bit, ... Manchester writes the complement and the unit writes the bit itself at
the next mid-bit tick." Op 16 PINS: "bits2:0 clock pin, 5:3 TX pin, 8:6 RX
pin". Invalid encodings: "with the pair set a pair pin that is not owned or
equals the data pin" (so every other pair pin is valid).

On every engine and for every data pin 0-7: an NRZ XFER without the pair and
a Manchester XFER with the pair on another pin. The engine owns and enables
every pin except the one the environment uses as the start marker, and sets
them to a random idle pattern first. Checked, cell by cell at the times the
ticker formula gives (as in test_line_unit's TX scenarios): the data pin
carries the bits (Manchester: the complement in the first half, the bit in the
second), the pair pin their complement, and every other enabled pin keeps its
idle level for the whole XFER. The harness compares the DUT with the reference
model on every cycle.

At gate level (PE_GATE_LEVEL) the module is skipped unless PE_LINE_GL_FULL=1, as the long
line-unit demos are.
"""

from __future__ import annotations

import random

import cocotb

from harness import CocotbHarness
from line_scenarios import Pads, engine_status, load_and_start, run_to_halt, ticks
from line_support import (HALT, SKIP_REASON, TX, X, bits_lsb, dir_, lcfg, line_unit_enabled, load, ltim, or_, pins,
                          set_, shl, wait, waitpin, xline)
from scenarios import fault_of
from test_line_spec_common import SKIP

if not line_unit_enabled():
    print(f"test_line_spec_pins: {SKIP_REASON}")


@cocotb.test(skip=SKIP)
async def test_line_drive_writes_only_data_and_pair_pins(dut):
    """Every engine, every data pin: NRZ without the pair and Manchester with a pair pin; the data and
    pair pins carry the cells, every other enabled pin keeps its SET level."""
    h = CocotbHarness(dut)
    rng = random.Random(0x5EC0B0)
    period, nbits = 3, 12
    for engine in range(4):
        for data_pin in range(8):
            for manchester in (False, True):
                await h.start()
                pair_pin = (data_pin + 1 + rng.randrange(7)) % 8 if manchester else None
                used = {data_pin} | ({pair_pin} if pair_pin is not None else set())
                marker_pin = rng.choice([p for p in range(8) if p not in used])
                owned = 0xFF & ~(1 << marker_pin)
                word = rng.randrange(1 << 32)
                init = rng.randrange(2)
                idle = rng.randrange(256) & owned
                idle = idle & ~(1 << data_pin) | init << data_pin
                if pair_pin is not None:
                    idle = idle & ~(1 << pair_pin) | (1 - init) << pair_pin
                marker: dict[str, int | None] = {"at": None}
                pads = Pads(lambda cycle, pads, m=marker_pin: 0xFF & ~(1 << m) | (
                    (1 << m) if marker["at"] is not None and cycle >= marker["at"] else 0))
                h.pins = pads
                clock_field = pair_pin if pair_pin is not None else (data_pin + 1) % 8
                program = [pins(clock_field, data_pin, marker_pin),
                           lcfg(2 if manchester else 0, pair=pair_pin is not None, init=init),
                           set_(idle), dir_(owned), load(TX, word & 0xFFFF), load(X, word >> 16), shl(X, 16),
                           or_(TX, X), waitpin(marker_pin, 1), ltim(period, 0, 4), xline(nbits, drive=True),
                           wait(6 * period + 8), HALT]
                await load_and_start(h, engine, program, ownership=owned)
                await h.idle(16)
                marker["at"] = h.cycle + 3
                c0 = marker["at"] + 3
                await run_to_halt(h, engine, 2 * period * (nbits + 8) + 300, f"engine {engine} pin {data_pin}")
                assert fault_of(await engine_status(h, engine)) == 0
                cells = bits_lsb(word, nbits)
                tick_list = ticks(c0, period, 0, 4, 2 * nbits + 4)
                what = (f"engine {engine}, data pin {data_pin}, "
                        f"{'Manchester, pair pin %d' % pair_pin if manchester else 'NRZ'}")
                for j, bit in enumerate(cells):
                    start = tick_list[2 * j] + 1
                    halves = [(start + period // 2, 1 - bit), (start + period + period // 2, bit)] if manchester \
                        else [(start + period, bit)]
                    for t, level in halves:
                        assert pads.pad(t, data_pin) == level, f"{what}: cell {j} at {t}: data pin != {level}"
                        if pair_pin is not None:
                            assert pads.pad(t, pair_pin) == 1 - level, f"{what}: cell {j} at {t}: pair pin"
                first, last = tick_list[0] + 1, tick_list[2 * nbits] + 1
                for t in range(first, last):
                    out, enable = pads.driven(t)
                    others = owned & ~(1 << data_pin) & ~(1 << pair_pin if pair_pin is not None else 0)
                    assert enable & owned == owned, f"{what}: output enables {enable:#04x} at {t}"
                    assert out & others == idle & others, (f"{what}: at {t} pins {out:#04x}, other pins must keep "
                                                           f"{idle & others:#04x}")
        h.log("engine %d: 8 data pins, NRZ and Manchester with a pair", engine)
