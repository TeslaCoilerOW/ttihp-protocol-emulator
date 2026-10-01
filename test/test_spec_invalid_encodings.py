# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Invalid opcodes and FAULT operands fault with code 1 on the engine that issues them.

Specified behaviour (docs/isa.md):
- "Other opcodes fault." Opcodes 0..29 are defined (on a design with the line
  unit also 30..33, docs/isa.md "Line-unit extension").
- FAULT: "imm24 low8 nonzero explicit fault code; high16 zero"; "FAULT uses
  its explicit nonzero code". A FAULT whose operand breaks that rule is an
  invalid operand.
- "Built-in fault codes are 1 invalid opcode/operand/ownership"; "Faults stop
  execution and release output enables"; uo7 is the fault pin; status "bit0
  running,bit1 committed,bit2 stalled,bit3 fault; bits15:8 fault code".
- A faulting instruction does not complete: PC and the completed-instruction
  count keep their values (docs/isa-spec.md section 2.2, from isa.md
  "Instructions commit on rising clk edges" and the HALT completion rule).

``invalid_opcodes``: every opcode that is not defined but differs from a
defined opcode in one bit (bit 5, 6 or 7 added to 0..29, and 30 and 31 next to
14, 15, 22, 23, 26..29), with operands that make it a valid instruction of
that defined opcode (``VALID_OPERANDS``), so that a decoder that ignored the
differing bit would execute it. ``fault_operands``: FAULT with low8 = 0 and
high16 = 0 or one high bit set, and FAULT with a valid low8 and one high bit
set. Each case is a one-word image on one engine at a time (the engine owns
all pins), so the shared fault pin shows that engine alone: before START the
pin is low, after the case it is high, the engine's status reads exactly
0x10A (halted, committed, fault, code 1), and CLEAR lowers the pin. On one of
the four engines per case (rotating) the PC and the count are read too and
must still be 0. At gate level a subset runs (the neighbours of 30 and 31 and
four FAULT operands).
"""

from __future__ import annotations

import cocotb

import variants
from harness import CLEAR, READ_SELECT, RS_COUNT, RS_PC, RS_STATUS, START, UO_FAULT, Harness, immediate, instruction
from test_kill_common import FAULT, GATE_LEVEL, own, reload
from test_spec_common import harness, read_word

FAULTED = 1 << 1 | 1 << 3 | 1 << 8  # committed, fault, code 1; not running, not stalled

# Operands (a, b, c) that make each defined opcode a valid instruction for an engine
# that owns all eight pins, right after START (clock pin 0, TX pin 0).
VALID_OPERANDS = {
    0: (0, 0, 0), 1: (0, 0, 0), 2: (0, 0, 1), 3: (0, 0, 1), 4: (0, 0, 2), 5: (0, 0, 0), 6: (0, 0, 0),
    7: (0, 0, 0), 8: (1, 0, 0), 9: (1, 0, 0), 10: (0, 0, 3), 11: (0, 0, 0), 12: (0, 0, 5), 13: (1, 1, 0),
    14: (0, 0, 1), 15: (0, 0, 0), 16: (0, 0, 0x0A), 17: (1, 1, 0), 18: (1, 2, 0), 19: (1, 0x12, 0x34),
    20: (1, 2, 0), 21: (1, 2, 0), 22: (1, 2, 0), 23: (1, 2, 0), 24: (1, 0, 8), 25: (1, 0, 8), 26: (1, 0, 0),
    27: (1, 0, 0), 28: (1, 0, 0), 29: (0, 0, 0x2A),
    # line-unit opcodes (designs with the line unit only): LTIM stop, LCFG NRZ, CRC preset 0, LSTAT x
    30: (0, 0, 0), 31: (0, 0, 0), 32: (0, 0, 3), 33: (1, 0, 0),
}
GL_OPCODES = {30, 31}
GL_FAULT_OPERANDS = [0, 1 << 16, 1 << 17, 1 << 23 | 0x2A]


def defined_opcodes() -> set[int]:
    line_unit = variants.options(variants.design_config()).line_unit != "none"
    return set(range(34 if line_unit else 30))


def opcode_cases(subset: bool) -> list[tuple[str, int]]:
    """(label, word): each undefined opcode one bit away from a defined one, once per such
    defined neighbour, with that neighbour's valid operands."""
    defined = defined_opcodes()
    cases = []
    for op in range(256):
        if op in defined or (subset and op not in GL_OPCODES):
            continue
        for k in range(8):
            neighbour = op ^ 1 << k
            if neighbour in defined:
                cases.append((f"opcode {op:#04x} (as {neighbour})", instruction(op, *VALID_OPERANDS[neighbour])))
    return cases


def fault_operand_cases(subset: bool) -> list[tuple[str, int]]:
    operands = [0] + [1 << k for k in range(8, 24)] + [1 << k | 0x2A for k in range(8, 24)]
    if subset:
        operands = GL_FAULT_OPERANDS
    return [(f"FAULT {value:#08x}", immediate(FAULT, value)) for value in operands]


def fault_pin(h: Harness) -> bool:
    return bool(h.last_pre.uo & UO_FAULT)


async def run_cases(h: Harness, cases: list[tuple[str, int]]) -> int:
    await h.start()
    h.pins = 0
    checked = 0
    for engine in range(4):
        await own(h, engine, 0xFF)
        await h.command(READ_SELECT, RS_STATUS)  # for the status reads below
        for index, (label, word) in enumerate(cases):
            what = f"engine {engine}, {label}"
            await reload(h, engine, [word], select=False)
            assert not fault_pin(h), f"{what}: fault pin high before START"
            await h.command(START, 1 << engine)
            await h.idle(2)
            assert fault_pin(h), f"{what}: no fault reported on the fault pin"
            status = await read_word(h)
            assert status == FAULTED, f"{what}: status {status:#x}, isa.md gives {FAULTED:#x} (fault code 1)"
            if (index + engine) % 4 == 0:
                pc, count = await read_word(h, RS_PC), await read_word(h, RS_COUNT)
                assert pc == 0 and count == 0, f"{what}: PC {pc}, count {count} after the fault (expected 0, 0)"
                await h.command(READ_SELECT, RS_STATUS)
            await h.command(CLEAR, 1 << engine)
            assert not fault_pin(h), f"{what}: CLEAR left the fault pin high"
            checked += 1
        await own(h, engine, 0)
    h.assert_no_faults()
    return checked


@cocotb.test()
async def test_spec_invalid_opcodes_fault(dut):
    """Every undefined opcode one bit from a defined one, with that opcode's valid operands, faults with code 1."""
    h = harness(dut)
    count = await run_cases(h, opcode_cases(GATE_LEVEL))
    h.log("%d invalid opcodes faulted with code 1 (%d cycles)", count, h.cycle)


@cocotb.test()
async def test_spec_invalid_fault_operands(dut):
    """FAULT with a zero code or a nonzero high16 faults with code 1 on every engine."""
    h = harness(dut)
    count = await run_cases(h, fault_operand_cases(GATE_LEVEL))
    h.log("%d invalid FAULT operands faulted with code 1 (%d cycles)", count, h.cycle)
