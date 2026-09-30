# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""CRC read-back and LSTAT write register a and no other register, on every
engine (docs/isa.md, "Line-unit extension").

Specified behaviour (docs/isa.md): op 32 CRC "c = 2: register a := CRC, b = 0"
and "c = 1: CRC := register b (low 16 bits)"; op 33 "LSTAT: register a := ..."
(bits listed there, [6] ticker running); registers "0=tx,1=rx,2=x,3=y" (MOV).
An instruction that assigns register a leaves the other three registers
unchanged, and the 16-bit CRC is zero-extended into the 32-bit register.

On every engine, for every destination a and for both CRC read-back and
LSTAT: the four registers are loaded with 32-bit patterns (a second round uses
their complements, so every bit of every register is seen both 0 and 1), the
instruction runs, and all four registers are pushed. Checked: register a holds
the CRC (set before from a register whose high half is nonzero) or an LSTAT
word with bits 7 and 14-31 zero and the ticker bit as configured (stopped in the
first round, running after LTIM in the second); the other three registers hold
their patterns. The harness compares the DUT with the reference model on every
cycle.

At gate level (PE_GATE_LEVEL) the module is skipped unless PE_LINE_GL_FULL=1, as the long
line-unit demos are.
"""

from __future__ import annotations

import random

import cocotb

from harness import CocotbHarness
from line_scenarios import engine_status, load_and_start, read_rx, run_to_halt
from line_support import (HALT, PULL, PUSH, RX, SKIP_REASON, ST_RUN, TX, X, Y, crc_get, crc_set, line_unit_enabled,
                          lstat, ltim, mov)
from scenarios import fault_of
from test_line_spec_common import SKIP

if not line_unit_enabled():
    print(f"test_line_spec_registers: {SKIP_REASON}")

NAMES = {TX: "tx", RX: "rx", X: "x", Y: "y"}
# fill tx, rx, x, y from four queued words (the last PULL leaves the fourth word in tx)
FILL = [PULL, mov(X, TX), PULL, mov(Y, TX), PULL, mov(RX, TX), PULL]
# push rx, x, y, tx (in that order)
PUSH_ALL = [PUSH, mov(RX, X), PUSH, mov(RX, Y), PUSH, mov(RX, TX), PUSH]


def block(op: int) -> list[int]:
    return FILL + [op] + PUSH_ALL


@cocotb.test(skip=SKIP)
async def test_crc_read_and_lstat_write_only_register_a(dut):
    """CRC c=2 and LSTAT into each register a on every engine: a gets the value (CRC zero-extended),
    the other registers keep their 32-bit patterns; LSTAT's ticker bit 0 before LTIM, 1 after."""
    h = CocotbHarness(dut)
    rng = random.Random(0x5EC0A0)
    for engine in range(4):
        for dest in (TX, RX, X, Y):
            await h.start()
            crc_value = rng.randrange(1 << 16)
            patterns = [rng.randrange(1 << 32) for _ in range(4)]  # x, y, rx, tx after FILL
            second = [p ^ 0xFFFF_FFFF for p in patterns]
            source = rng.randrange(1 << 16) << 16 | crc_value  # high half nonzero: only the low 16 bits count
            # round 1: ticker stopped; round 2: LTIM running
            program = ([PULL, crc_set(TX)] + block(crc_get(dest)) + block(lstat(dest)) + [ltim(5)]
                       + block(crc_get(dest)) + block(lstat(dest)) + [HALT])
            assert len(program) <= 64
            tx = [source] + patterns + patterns + second + second
            # the TX queue holds 8 words: the host tops it up while the program runs
            await load_and_start(h, engine, program, tx=tx[:8])
            pushed: list[int] = []
            pending = tx[8:]
            for _ in range(4000):
                if not h.engine(engine).running and not pending:
                    break
                if pending and await h.try_write(2, pending[0], max_wait=20):
                    pending.pop(0)
                if len(pushed) < 16:
                    word = await h.try_read(3, max_wait=20)
                    if word is not None:
                        pushed.append(word)
            await run_to_halt(h, engine, 2000, f"engine {engine} register program")
            pushed += await read_rx(h, engine, 16 - len(pushed))
            assert fault_of(await engine_status(h, engine)) == 0
            for k, (pats, what) in enumerate(((patterns, "CRC"), (patterns, "LSTAT"), (second, "CRC"),
                                              (second, "LSTAT"))):
                x_pat, y_pat, rx_pat, tx_pat = pats
                got = dict(zip((RX, X, Y, TX), pushed[4 * k:4 * k + 4]))
                want = {TX: tx_pat, RX: rx_pat, X: x_pat, Y: y_pat}
                for reg in (TX, RX, X, Y):
                    if reg == dest:
                        continue
                    assert got[reg] == want[reg], (f"engine {engine}, {what} into {NAMES[dest]} (round {k // 2 + 1}): "
                                                   f"{NAMES[reg]} = {got[reg]:#010x}, expected unchanged "
                                                   f"{want[reg]:#010x}")
                value = got[dest]
                if what == "CRC":
                    assert value == crc_value, (f"engine {engine}, CRC into {NAMES[dest]}: {value:#x}, "
                                                f"expected {crc_value:#06x}")
                else:
                    running = k >= 2
                    assert value & ~0x3F7F == 0 and bool(value & ST_RUN) == running, (
                        f"engine {engine}, LSTAT into {NAMES[dest]} (ticker {'running' if running else 'stopped'}): "
                        f"{value:#x}")
        h.log("engine %d: CRC read-back and LSTAT write only their destination", engine)
