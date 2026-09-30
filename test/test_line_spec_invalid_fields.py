# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Every bit of every constrained field of the line-unit instructions is
checked: an encoding that breaks a rule in one bit faults with code 1, and
its valid neighbour completes, on every engine (docs/isa.md, "Line-unit
extension").

Specified behaviour (docs/isa.md, "Invalid encodings"): "Invalid encodings
fault with code 1: LTIM P = 255 with Q != 0; LCFG with bits 23..11 set, line
code 3, or stuffing on either polarity with run length 1; CRC with c = 0 or
c > 3 or a field outside its rule; LSTAT with a > 3, b != 0 or c != 0; XFER
with c bit 7; a line XFER with b != 0, c[1:0] != 0, a outside 1..32, the ticker
stopped, ...; opcodes 34..255." The field rules (op table): LTIM "[7:0]
half-period P, [15:8] fraction Q/256"; CRC "c = 1: CRC := register b (low 16
bits), a = 0. c = 2: register a := CRC, b = 0. c = 3: polynomial preset b
(0..3), a = 0"; LSTAT "register a := ...; b = c = 0" (a names one of the four
registers 0..3); line XFER "a bit count 1..32, b = 0, c[1:0] = 0". Fault code
1 is "invalid opcode/operand/ownership" ("Instructions").

The cases vary one field of a valid instruction at a time, one bit at a time:
Q = each single bit with P = 255 (and P = 255 with one bit cleared and Q = 255,
valid); each of LCFG bits 11 to 23; CRC c = 1, 2, 3 with each of bits 2-7 of c
set, each bit of a field that must be 0, each of bits 2-7 of a register or
preset field; LSTAT with each of bits 2-7 of a and each bit of b and c; a line
XFER with each bit of b, bit count 0, 33 to 48 by single bits, 65 and 129 (and
the counts 1, 2, 4, 8, 16 and 32, valid); and opcodes 30 to 33 with one more
bit set that gives an opcode of 34 or more. Each program is the instruction
(after PINS and LTIM for an XFER) and HALT; checked from the DUT's status
register and PC: fault code 1 at the instruction's own PC, or no fault. The
harness also compares the DUT with the reference model on every cycle (off
with PE_SPEC_ONLY=1, test_line_spec_common.spec_harness).

At gate level (PE_GATE_LEVEL) the module is skipped unless PE_LINE_GL_FULL=1, as the long
line-unit demos are.
"""

from __future__ import annotations

import cocotb

from harness import CLEAR, RS_PC, STOP
from line_scenarios import engine_status, load_and_start, run_to_halt
from line_support import HALT, SKIP_REASON, imm, ins, line_unit_enabled, ltim, pins, xfer
from scenarios import fault_of
from test_line_spec_common import SKIP, spec_harness

if not line_unit_enabled():
    print(f"test_line_spec_invalid_fields: {SKIP_REASON}")

LTIM, LCFG, CRC, LSTAT = 30, 31, 32, 33
XFER_SETUP = [pins(1, 0, 3), ltim(5)]  # a sample-only line XFER needs the ticker and no owned pin


def cases() -> list[tuple[str, list[int], bool]]:
    """(name, program, faults): the program's last instruction is the one under test."""
    out: list[tuple[str, list[int], bool]] = []
    for k in range(8):
        out.append((f"LTIM P=255 Q={1 << k}", [ltim(255, 1 << k)], True))
        out.append((f"LTIM P={255 ^ 1 << k} Q=255", [ltim(255 ^ 1 << k, 255)], False))
    out.append(("LTIM P=255 Q=0", [ltim(255)], False))
    for k in range(11, 24):
        out.append((f"LCFG bit {k}", [imm(LCFG, 1 << k)], True))
    out.append(("LCFG bits 10..0 set, code 2", [imm(LCFG, 0x7FE)], False))
    # CRC: (c, a, b) of a valid instruction and which of a and b must be 0 or name 0..3
    for c, a, b, zero, small in ((1, 0, 1, "a", "b"), (2, 1, 0, "b", "a"), (3, 0, 2, "a", "b")):
        base = {"a": a, "b": b}
        out.append((f"CRC c={c} a={a} b={b}", [ins(CRC, a, b, c)], False))
        for k in range(2, 8):
            out.append((f"CRC c={c | 1 << k} a={a} b={b}", [ins(CRC, a, b, c | 1 << k)], True))
        for k in range(8):
            f = dict(base, **{zero: 1 << k})
            out.append((f"CRC c={c} {zero}={1 << k}", [ins(CRC, f["a"], f["b"], c)], True))
        for k in range(2, 8):
            f = dict(base, **{small: base[small] | 1 << k})
            out.append((f"CRC c={c} {small}={f[small]}", [ins(CRC, f["a"], f["b"], c)], True))
        for v in range(4):
            f = dict(base, **{small: v})
            out.append((f"CRC c={c} {small}={v}", [ins(CRC, f["a"], f["b"], c)], False))
    out.append(("CRC c=0", [ins(CRC, 0, 1, 0)], True))
    out.append(("LSTAT a=1", [ins(LSTAT, 1)], False))
    for k in range(2, 8):
        out.append((f"LSTAT a={1 | 1 << k}", [ins(LSTAT, 1 | 1 << k)], True))
    for k in range(8):
        out.append((f"LSTAT b={1 << k}", [ins(LSTAT, 1, 1 << k)], True))
        out.append((f"LSTAT c={1 << k}", [ins(LSTAT, 1, 0, 1 << k)], True))
    for k in range(8):
        out.append((f"line XFER b={1 << k}", XFER_SETUP + [xfer(8, 1 << k, 0x30)], True))
    for n in [0, 65, 129] + [32 | 1 << k for k in range(5)]:
        out.append((f"line XFER a={n}", XFER_SETUP + [xfer(n, 0, 0x30)], True))
    for k in range(6):
        out.append((f"line XFER a={1 << k}", XFER_SETUP + [xfer(1 << k, 0, 0x30)], False))
    for op in (LTIM, LCFG, CRC, LSTAT):
        for k in range(8):
            other = op | 1 << k
            if other != op and other >= 34:
                out.append((f"opcode {other} (opcode {op} with bit {k})", [imm(other)], True))
    return out


@cocotb.test(skip=SKIP)
async def test_line_encoding_rules_every_field_bit(dut):
    """Every engine: each rule-breaking field bit of LTIM, LCFG, CRC, LSTAT, a line XFER and the opcode
    faults with code 1 at its PC; each valid neighbour completes."""
    h = spec_harness(dut)
    all_cases = cases()
    for engine in range(4):
        await h.start()
        for name, program, faults in all_cases:
            await load_and_start(h, engine, program + [HALT])
            await run_to_halt(h, engine, 1500, f"engine {engine}, {name}")
            status = await engine_status(h, engine)
            pc = await h.status(RS_PC)
            what = f"engine {engine}, {name}: status {status:#x}, pc {pc}"
            if faults:
                assert fault_of(status) == 1 and pc == len(program) - 1, what
            else:
                assert fault_of(status) == 0 and not status & 1, what
            await h.command(STOP, 1 << engine)
            await h.command(CLEAR, 1 << engine)
        h.log("engine %d: %d encodings", engine, len(all_cases))
