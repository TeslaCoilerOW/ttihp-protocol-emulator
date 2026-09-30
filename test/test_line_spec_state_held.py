# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Instructions that do not use the line unit leave its state unchanged, on
every engine (docs/isa.md, "Line-unit extension").

Specified behaviour (docs/isa.md): the line unit's state is written by LTIM
("(re)starts the ticker"), LCFG ("Resets the line state and flags, not the
ticker or the CRC"), CRC ("CRC := register b", "polynomial preset b"), line
XFERs and classic XFERs with c bit 6 (the CRC; a classic XFER also stops the
ticker), and by START and reset ("START and reset clear the ticker, LCFG, the
line state and flags, the CRC and the preset"). LSTAT flags "stay set until
LCFG or START". "The ticker runs while the engine runs, also during WAIT and
blocked instructions." No other instruction is specified to change the unit.

On every engine, two unit states are set up: "set" (NRZI, stuffing on runs of
either polarity of length 7, pair, arbitration monitor, SE0 end, initial level
1, the SE0 flag with 9 data bits remaining, CRC 0xFFFF with preset 3, ticker
P = 5 with a fraction) and "clear" (LCFG 0, CRC 0 with preset 0, ticker P = 2,
no flags). Then each base instruction (NOP, SET, DIR, WAIT 0 and 7, COUNT,
LOOP taken and not taken, LIMIT, SIGNAL and a WAITEVENT that consumes it,
a satisfied WAITPIN, MOV, LOAD, ADD, XOR, AND, OR, SHL, SHR, NOT, TIME, JZ
taken and not taken, OUT, IN, PUSH, PULL and a stalled PULL, JMP) runs, followed
by LSTAT into rx and CRC into rx, both pushed. Checked: the LSTAT flags, the
remaining count and the ticker bit, and the CRC, equal their values before the
instruction. At the end a drive-only line XFER with the CRC bit shows the
configuration on the pins. The harness compares the DUT with the reference
model on every cycle (pins and every pushed word).
"""

from __future__ import annotations

import cocotb

from harness import CocotbHarness, Harness, immediate, instruction
from line_scenarios import engine_status, load_and_start
from line_support import (HALT, PULL, PUSH, RX, SKIP_REASON, TX, X, Y, add, and_, count, crc_get, crc_preset, crc_set,
                          dir_, jmp, jz, lcfg, limit, line_unit_enabled, load, loop, lstat, ltim, mov, not_, or_,
                          pins, set_, shl, shr, wait, waitpin, xline, xor)
from scenarios import fault_of
from test_kill_common import IN, OUT, SIGNAL, TIME, WAITEVENT
from test_line_spec_common import SKIP

if not line_unit_enabled():
    print(f"test_line_spec_state_held: {SKIP_REASON}")

PROBE = [lstat(RX), PUSH, crc_get(RX), PUSH]
KEEP = 0x3F47  # LSTAT fields that no base instruction may change: flags, ticker bit, remaining count
NEXT, NEXT16 = 0xFFFFFF, 0xFFFF  # jump targets patched to the next word


def held(engine: int) -> list[tuple[str, list[int]]]:
    """(label, words) of base instructions; jump targets are patched to the next word by ``build``."""
    data = 2 * engine
    mine = 1 << data
    return [
        ("NOP", [instruction(0)]), ("SET", [set_(mine)]), ("DIR", [dir_(0)]), ("WAIT 0", [wait(0)]),
        ("WAIT 7", [wait(7)]), ("COUNT, LOOP taken", [count(1), loop(NEXT)]), ("LOOP not taken", [loop(NEXT)]),
        ("LIMIT", [limit(9)]), ("SIGNAL, WAITEVENT", [immediate(SIGNAL, 1 << engine), instruction(WAITEVENT)]),
        ("WAITPIN satisfied", [waitpin(7, 0)]), ("MOV", [mov(X, Y)]), ("LOAD", [load(Y, 0xA5A5)]),
        ("ADD", [add(X, Y)]), ("XOR", [xor(X, Y)]), ("AND", [and_(X, Y)]), ("OR", [or_(X, Y)]),
        ("SHL", [shl(X, 8)]), ("SHR", [shr(X, 8)]), ("NOT", [not_(X)]), ("TIME", [instruction(TIME, X)]),
        ("JZ taken", [load(Y, 0), jz(Y, NEXT16)]), ("JZ not taken", [load(Y, 1), jz(Y, NEXT16)]),
        ("OUT", [instruction(OUT, data, 0, 0)]), ("IN", [instruction(IN, 7, 0, 0)]), ("PUSH", [PUSH]),
        ("PULL", [PULL]), ("PULL stall", [PULL]), ("JMP", [jmp(NEXT)]),
    ]


def build(prefix: list[int], items: list[tuple[str, list[int]]], suffix: list[int]) -> list[int]:
    """prefix, then each item followed by PROBE (jump targets NEXT patched to the next word), then suffix."""
    words = list(prefix)
    for _, body in items:
        for word in body:
            op = word >> 24
            if op in (5, 11) and word & 0xFFFFFF == 0xFFFFFF:  # JMP, LOOP
                word = word & ~0xFFFFFF | len(words) + 1
            elif op == 26 and word & 0xFFFF == 0xFFFF:  # JZ
                word = word & ~0xFFFF | len(words) + 1
            words.append(word)
        words += PROBE
    return words + suffix


def setups(engine: int) -> list[tuple[str, list[int], int, int]]:
    """(name, setup words, expected LSTAT fields in KEEP, expected CRC)."""
    data, pair = 2 * engine, 2 * engine + 1
    se0_bits = 9
    rx = (data + 2) % 8  # another engine's data pin, never enabled here: reads 0
    set_state = [pins(pair, data, rx), lcfg(1, 7, either=True, pair=True, arb=True, se0=True, init=1),
                 ltim(5, 171, 3), xline(se0_bits, sample=True), load(X, 0xFFFF), crc_set(X), crc_preset(3)]
    clear_state = [pins(pair, data, rx), lcfg(), ltim(2), load(X, 0), crc_set(X), crc_preset(0)]
    return [("set", set_state, 0x40 | 0x01 | se0_bits << 8, 0xFFFF), ("clear", clear_state, 0x40, 0x0000)]


async def run_and_service(h: Harness, engine: int, program: list[int], pushes: int, *, stall_at: int | None,
                          ownership: int) -> list[int]:
    """Run ``program``; supply one TX word per PULL (the stalled PULL's word only after 40 cycles)
    and read the pushed words while the engine runs."""
    await load_and_start(h, engine, program, ownership=ownership, tx=[0x1111_0000])
    words: list[int] = []
    supplied, stall_seen = 0, None
    for _ in range(6000):
        e = h.engine(engine)
        if not e.running and len(words) >= pushes:
            break
        if stall_at is not None and supplied == 0 and e.stalled:
            stall_seen = stall_seen if stall_seen is not None else h.cycle
            if h.cycle - stall_seen >= 40 and await h.try_write(2, 0x2222_0000, max_wait=20):
                supplied = 1
                continue
        word = await h.try_read(3, max_wait=4)
        if word is not None:
            words.append(word)
        else:
            await h.idle(2)
    assert len(words) == pushes, f"engine {engine}: {len(words)} words pushed, expected {pushes}"
    assert fault_of(await engine_status(h, engine)) == 0
    return words


@cocotb.test(skip=SKIP)
async def test_base_instructions_keep_line_state_every_engine(dut):
    """Every engine, two unit states: after each base instruction LSTAT (flags, ticker, SE0 count) and the
    CRC equal their values before it; a final line XFER shows the configuration on the pins."""
    h = CocotbHarness(dut)
    for engine in range(4):
        items = held(engine)
        for name, setup, keep, crc in setups(engine):
            for part in (items[0:7], items[7:14], items[14:21], items[21:]):
                await h.start()
                h.pins = 0  # pads low: the setup's sampling XFER ends on SE0 at once
                suffix = [not_(TX), xline(12, drive=True, crc=True), crc_get(RX), PUSH, HALT]
                program = build(setup, part, suffix)
                assert len(program) <= 64, len(program)
                labels = [label for label, _ in part]
                stall = labels.index("PULL stall") if "PULL stall" in labels else None
                pushes = 2 * len(part) + sum(label == "PUSH" for label in labels) + 1
                words = await run_and_service(h, engine, program, pushes, stall_at=stall,
                                              ownership=3 << 2 * engine)
                k = 0
                for label in labels:
                    if label == "PUSH":
                        k += 1  # the PUSH under test pushes rx (the previous probe's CRC)
                    status, value = words[k], words[k + 1]
                    k += 2
                    what = f"engine {engine}, state {name}, after {label}"
                    assert status & KEEP == keep, f"{what}: LSTAT {status:#x}, fields {keep:#x} expected"
                    assert value == crc, f"{what}: CRC {value:#x}, expected {crc:#06x}"
        h.log("engine %d: %d base instructions keep both unit states", engine, len(items))
