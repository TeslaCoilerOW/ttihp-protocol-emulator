# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""An instruction in the last word of a full 64-word image moves the PC out of
the image: the engine faults with code 2 and the PC reads 64, for every
instruction that advances the PC by one, on every engine (docs/isa.md).

Specified behaviour (docs/isa.md, "Machine"): "program words 32/64/128
(flagship 64)", this variant has 64; "PC is bounded by committed image
length; falling outside the image faults"; "A nonblocking instruction costs
one cycle". ("Faults" section) "Built-in fault codes are 1 invalid
opcode/operand/ownership, 2 PC outside the committed image, ...". WAITPIN
"consume one issue edge if already true"; WAITEVENT "consume own pending
mailbox bit". The line-unit instructions LTIM, LCFG, CRC (c = 1, 2, 3), LSTAT
and a line XFER ("Line-unit extension") advance the PC like the others.

Each case fills words 0-62 with NOP and word 63 with the instruction under
test; the cases that need a setup (the XFERs, WAITEVENT, LOOP and JZ) put it
in the words just before. The four engines run each case together; engine e
owns pin e (OUT and the classic XFER's clock pin), and PULL finds one word in
its TX queue. After the instruction completes, the next PC (64) is outside the
64-word image: each engine must report fault code 2, and READ_SELECT 3 (PC)
must read 64. The next-PC logic has one path per opcode, so the cases cover
every opcode that can advance the PC by one: NOP, SET, DIR, WAIT, PULL, PUSH,
OUT, IN, COUNT, LOOP (not taken), LIMIT, WAITPIN (already true), SIGNAL,
WAITEVENT (event pending), PINS, XFER (classic and line), MOV, LOAD, ADD, XOR,
AND, OR, SHL, SHR, JZ (not taken), NOT, TIME, LTIM, LCFG, CRC (c = 1, 2, 3)
and LSTAT. HALT, JMP, a taken LOOP or JZ, and FAULT do not advance the PC by
one. The fault code and PC are read from the DUT; the harness also compares
the DUT with the reference model on every cycle (off with PE_SPEC_ONLY=1,
test_line_spec_common.spec_harness).

At gate level (PE_GATE_LEVEL) the module is skipped unless PE_LINE_GL_FULL=1, as the long
line-unit demos are.
"""

from __future__ import annotations

import cocotb

from harness import CLEAR, RS_PC, START
from line_scenarios import engine_status
from line_support import (SKIP_REASON, crc_get, crc_preset, crc_set, imm, ins, jz, lcfg, line_unit_enabled, load, lstat,
                          ltim, pins, xfer, xline)
from scenarios import fault_of
from test_kill_common import own, reload
from test_line_spec_common import SKIP, spec_harness

if not line_unit_enabled():
    print(f"test_line_spec_image_end: {SKIP_REASON}")

WORDS = 64
NOP = ins(0)


def cases(engine: int) -> list[tuple[str, list[int]]]:
    """(name, the last words of the image); the last word is the instruction under test."""
    return [
        ("NOP", [NOP]), ("SET", [imm(2, 0)]), ("DIR", [imm(3, 0)]), ("WAIT 0", [imm(4, 0)]),
        ("PUSH", [ins(7)]), ("IN", [ins(9, 0, 0, 0)]), ("COUNT", [imm(10, 0)]), ("LIMIT", [imm(12, 1)]),
        ("WAITPIN, already true", [ins(13, 0, 0, 0)]), ("SIGNAL", [imm(14, 0)]),
        ("WAITEVENT, event pending", [imm(14, 1 << engine), imm(15, 0)]), ("PINS", [imm(16, 0)]),
        ("MOV", [ins(18, 0, 1)]), ("LOAD", [ins(19, 2) | 5]), ("ADD", [ins(20, 2, 3)]), ("XOR", [ins(21, 2, 3)]),
        ("AND", [ins(22, 2, 3)]), ("OR", [ins(23, 2, 3)]), ("SHL", [ins(24, 2, 0, 8)]), ("SHR", [ins(25, 2, 0, 8)]),
        ("NOT", [ins(27, 2)]), ("TIME", [ins(28, 2)]), ("LTIM", [ltim(3)]), ("LCFG", [lcfg()]),
        ("CRC c=1", [crc_set(1)]), ("CRC c=2", [crc_get(1)]), ("CRC c=3", [crc_preset(2)]), ("LSTAT", [lstat(1)]),
        ("line XFER", [pins(1, 0, 3), ltim(2), xline(4, sample=True)]),
        ("PULL, TX word queued", [ins(6)]), ("OUT", [ins(8, engine, 0, 0)]),
        ("LOOP, not taken", [imm(10, 0), imm(11, 0)]),
        ("classic XFER", [pins(engine, engine, 7), xfer(1, 1, 0)]),
        ("JZ, not taken", [load(2, 5), jz(2, 0)]),
    ]


@cocotb.test(skip=SKIP)
async def test_last_image_word_then_pc_outside_image(dut):
    """Every PC-advancing instruction in word 63 of a 64-word image, on all four engines: fault code 2, PC 64."""
    h = spec_harness(dut)
    await h.start()
    h.pins = 0  # WAITPIN pin 0 expecting 0 is already true
    for engine in range(4):
        await own(h, engine, 1 << engine)
    for index in range(len(cases(0))):
        name = cases(0)[index][0]
        for engine in range(4):
            tail = cases(engine)[index][1]
            await reload(h, engine, [NOP] * (WORDS - len(tail)) + tail)
            if name.startswith("PULL"):
                await h.write(2, 0x5EC0_0000 | engine)  # into the TX queue of the engine reload selected
        await h.command(START, 0b1111)
        await h.run_until(lambda: not any(e.running for e in h.model.engines), 400, f"{name} in word 63")
        for engine in range(4):
            fault = fault_of(await engine_status(h, engine))
            pc = await h.status(RS_PC)
            assert fault == 2, f"{name}, engine {engine}: fault code {fault}, expected 2"
            assert pc == WORDS == h.model.engines[engine].pc, f"{name}, engine {engine}: PC {pc}, expected {WORDS}"
        await h.command(CLEAR, 0b1111)
    h.log("%d instructions in word 63 on every engine: fault 2, PC 64", len(cases(0)))
