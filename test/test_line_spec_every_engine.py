# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Every engine has its own line unit with the same contract (docs/isa.md,
"Line-unit extension": option ``rec16`` "adds one line unit per engine").

The directed scenarios of line_scenarios.py (test_line_unit.py) run their
programs on engine 0. Here the same scenarios run on engines 1, 2 and 3: an
``EngineView`` hands each scenario the harness with engine 0 exchanged for the
engine under test in every host command that names an engine (SELECT, the
START/STOP/CLEAR/EVENT masks, ROUTE endpoints), in program loading and in the
model lookup. The programs, pins and expected values are unchanged, so each
engine must meet the same checks: the published CRC check values, the ticker
formula, the independent line encoders and decoders, the arbitration rule,
the invalid-encoding list (fault code 1 at the instruction's own PC), the
LSTAT bits, START/STOP/reset, the classic XFER's use of the shared ticker and
the completion points. The harness compares the DUT with the reference model
on every cycle, as in every other module.

At gate level (PE_GATE_LEVEL) the module is skipped unless PE_LINE_GL_FULL=1, as the long
line-unit demos are.
"""

from __future__ import annotations

from typing import Any

import cocotb

import line_scenarios as s
from harness import CLEAR, EVENT, ROUTE, SELECT, START, STOP, CocotbHarness
from line_support import SKIP_REASON, line_unit_enabled
from test_line_spec_common import SKIP

if not line_unit_enabled():
    print(f"test_line_spec_every_engine: {SKIP_REASON}")

ENGINES = (1, 2, 3)


class EngineView:
    """The harness seen by a single-engine scenario, with engine 0 and engine ``k`` exchanged.

    Attribute reads and writes go to the wrapped harness (so ``h.pins = ...``,
    ``h.cycle`` and ``h.model`` behave as usual); ``load``, ``command`` and
    ``engine`` translate engine numbers. Instruction words are not rewritten:
    the scenarios' programs name pins, not engines.
    """

    def __init__(self, harness: CocotbHarness, engine: int) -> None:
        object.__setattr__(self, "_h", harness)
        object.__setattr__(self, "_k", engine)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._h, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._h, name, value)

    def _index(self, index: int) -> int:
        return self._k if index == 0 else 0 if index == self._k else index

    def _mask(self, mask: int) -> int:
        low, high = mask & 0xF, mask & ~0xF
        out = 0
        for bit in range(4):
            if low >> bit & 1:
                out |= 1 << self._index(bit)
        return high | out

    async def load(self, engine: int, words: Any, **kwargs: Any) -> None:
        await self._h.load(self._index(engine), words, **kwargs)

    async def command(self, opcode: int, payload: int = 0) -> None:
        if opcode == SELECT:
            payload = payload & ~3 | self._index(payload & 3)
        elif opcode in (START, STOP, CLEAR, EVENT):
            payload = self._mask(payload)
        elif opcode == ROUTE:
            src, dst = self._index(payload & 3), self._index(payload >> 2 & 3)
            payload = payload & ~0xF | dst << 2 | src
        await self._h.command(opcode, payload)

    def engine(self, index: int) -> Any:
        return self._h.engine(self._index(index))


async def on_every_engine(dut: Any, scenario: Any) -> None:
    h = CocotbHarness(dut)
    for engine in ENGINES:
        h.log("engine %d", engine)
        await scenario(EngineView(h, engine))


@cocotb.test(skip=SKIP)
async def test_every_engine_crc_check_values(dut):
    """CRC: the nine catalogued check values on engines 1-3 (as test_line_unit on engine 0)."""
    await on_every_engine(dut, s.crc_vectors)


@cocotb.test(skip=SKIP)
async def test_every_engine_crc_classic_and_sampled(dut):
    """CRC fed by classic XFERs (c bit 6, driven and sampled bits) and by a sampling line XFER, engines 1-3."""
    await on_every_engine(dut, s.crc_classic_and_sampled)


@cocotb.test(skip=SKIP)
async def test_every_engine_ltim_ticker(dut):
    """LTIM: tick times against the ticker formula for ten (P, Q, D) sets; P = 0 stops; P = 255 with Q faults; engines 1-3."""
    await on_every_engine(dut, s.ticker_timing)


@cocotb.test(skip=SKIP)
async def test_every_engine_tx_line_coding(dut):
    """Drive-only line XFERs: NRZ, NRZI, Manchester, stuffing, pair pin, bit order against the encoders; engines 1-3."""
    await on_every_engine(dut, s.tx_line_coding)


@cocotb.test(skip=SKIP)
async def test_every_engine_rx_line_decoding(dut):
    """Sample-only line XFERs: decoding, destuffing, stuff errors, SE0 end and its count, sampled CRC; engines 1-3."""
    await on_every_engine(dut, s.rx_line_decoding)


@cocotb.test(skip=SKIP)
async def test_every_engine_arbitration(dut):
    """Arbitration monitor: loss only on a recessive bit with the monitor on, recessive drive after it; engines 1-3."""
    await on_every_engine(dut, s.arbitration)


@cocotb.test(skip=SKIP)
async def test_every_engine_invalid_encodings(dut):
    """The 43 encodings: each invalid one faults with code 1 at its PC, each valid neighbour completes; engines 1-3."""
    await on_every_engine(dut, s.invalid_encodings)


@cocotb.test(skip=SKIP)
async def test_every_engine_lstat_bits(dut):
    """LSTAT queue, line-level and ticker bits; engines 1-3."""
    await on_every_engine(dut, s.lstat_bits)


@cocotb.test(skip=SKIP)
async def test_every_engine_start_clears(dut):
    """START clears ticker, LCFG, flags, CRC and preset; STOP and reset mid-XFER release the pins; engines 1-3."""
    await on_every_engine(dut, s.start_clears)


@cocotb.test(skip=SKIP)
async def test_every_engine_classic_xfer_stops_ticker(dut):
    """A classic XFER stops the shared ticker and a following line XFER faults with code 1; engines 1-3."""
    await on_every_engine(dut, s.classic_stops_ticker)


@cocotb.test(skip=SKIP)
async def test_every_engine_completion_points(dut):
    """Drive-only XFERs complete at the last boundary tick, sampling ones at the last mid tick; engines 1-3."""
    await on_every_engine(dut, s.completion_points)
