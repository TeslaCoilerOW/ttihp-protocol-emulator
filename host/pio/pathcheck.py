# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Exhaustive check of the lockstep program's clock and pin schedule.

Every reachable (instruction, frame slot) pair is explored, with both
outcomes of every conditional jump and every handler address the host can
send through ``out pc``. On every path:

* each instruction's side-set value holds the clock at the frame's level for
  all of its cycles (slots 0-2 high, 3-5 low), so the project clock period is
  exactly FRAME_CYCLES system clocks whatever the data;
* ui_in (OUT/SET/MOV to PINS) changes only at slot 3, and uo_out is read
  (IN/MOV from PINS, JMP PIN, WAIT) only at slot 2;
* a JMP Y-- whose target is not its own successor would change control flow
  when Y reaches zero; every JMP Y-- must fall through to its target;
* the only blocking instructions are the dispatch PULL (preceded by a TX
  level test) and the HALT PULL.

Data-dependent properties (results, frame counts, protocol behaviour) are the
subject of the simulations in host/tests/test_pio_lockstep.py.
"""

import os
import sys

HOST = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOST not in sys.path:
    sys.path.insert(0, HOST)

from pe_host.ports import pio_lockstep as pl  # noqa: E402

from .asm import assemble, disassemble  # noqa: E402

HIGH_SLOTS = (0, 1, 2)
DRIVE_SLOT = 3
SAMPLE_SLOT = 2


def template_side(slot):
    return 1 if slot in HIGH_SLOTS else 0


def check_program(prog=None, labels=None, handlers=None):
    """Return a report dict; report["errors"] is empty when every rule holds."""
    if prog is None:
        prog, labels = assemble(pl.lockstep_program, **pl.asm_options(_Pio))
    mem = list(prog[0])
    execctrl = prog[len(prog) - 5]
    wrap_bottom, wrap_top = (execctrl >> 7) & 0x1F, (execctrl >> 12) & 0x1F
    if handlers is None:
        handlers = (pl.ADDR_RD, pl.ADDR_RDB, pl.ADDR_WR, pl.ADDR_HALT, pl.ADDR_CYC)
    errors = []
    seen = set()
    start = (pl.ADDR_FETCH, DRIVE_SLOT)
    todo = [start]
    drives = set()
    samples = set()
    blocking = set()
    while todo:
        state = todo.pop()
        if state in seen:
            continue
        seen.add(state)
        pc, slot = state
        instr = mem[pc]
        text = disassemble(instr, 1, False)
        side = (instr >> 12) & 1
        delay = (instr >> 8) & 0xF
        for k in range(delay + 1):
            s = (slot + k) % 6
            if template_side(s) != side:
                errors.append("%d %r at slot %d: side %d over slot %d" % (pc, text, slot, side, s))
        op = instr >> 13
        low = instr & 0xFF
        writes_pins = ((op == 3 and (low >> 5) & 7 in (0, 4)) or (op == 7 and (low >> 5) & 7 in (0, 4))
                       or (op == 5 and (low >> 5) & 7 in (0, 3)))
        reads_pins = ((op == 2 and (low >> 5) & 7 == 0) or (op == 5 and low & 7 == 0)
                      or (op == 0 and (low >> 5) & 7 == 6) or op == 1)
        if writes_pins:
            drives.add(pc)
            if slot != DRIVE_SLOT:
                errors.append("%d %r drives ui_in at slot %d" % (pc, text, slot))
        if reads_pins:
            samples.add(pc)
            if slot != SAMPLE_SLOT:
                errors.append("%d %r samples uo_out at slot %d" % (pc, text, slot))
        if op == 4 and low & 0x20:
            blocking.add(pc)
        nxt_slot = (slot + delay + 1) % 6
        fall = wrap_bottom if pc == wrap_top else (pc + 1) & 31
        succ = []
        if op == 0:
            cond = (low >> 5) & 7
            target = low & 0x1F
            if cond == 4 or cond == 2:
                if target != fall:
                    errors.append("%d %r: X--/Y-- target differs from fall-through" % (pc, text))
                succ = [target]
            elif cond == 0:
                succ = [target]
            else:
                succ = [target, fall]
        elif op == 3 and (low >> 5) & 7 == 5:
            bits = low & 0x1F
            succ = list(handlers) if bits == 5 else list(range(1 << bits))
        else:
            succ = [fall]
        for target in succ:
            todo.append((target, nxt_slot))
    allowed_blocking = {labels["disp"], labels["halt"]} if labels else set()
    for pc in blocking - allowed_blocking:
        errors.append("%d %r blocks" % (pc, disassemble(mem[pc], 1, False)))
    reached = sorted({pc for pc, _ in seen})
    unreached = [pc for pc in range(len(mem)) if pc not in reached]
    return {
        "errors": errors,
        "states": len(seen),
        "reached": reached,
        "unreached": unreached,
        "drive_instructions": sorted(drives),
        "sample_instructions": sorted(samples),
        "blocking": sorted(blocking),
        "slots": sorted(seen),
    }


class _Pio:
    OUT_LOW = 2
    SHIFT_RIGHT = 1


def listing():
    """Assembled program with addresses, encodings, slots and pioasm text."""
    prog, labels = assemble(pl.lockstep_program, **pl.asm_options(_Pio))
    report = check_program(prog, labels)
    slots = {}
    for pc, slot in report["slots"]:
        slots.setdefault(pc, set()).add(slot)
    names = {}
    for name, addr in labels.items():
        names.setdefault(addr, []).append(name)
    lines = []
    for pc, instr in enumerate(prog[0]):
        tag = ",".join(names.get(pc, []))
        where = ",".join(str(s) for s in sorted(slots.get(pc, ()))) or "-"
        lines.append("%2d  %04x  slot %-5s %-10s %s" % (pc, instr, where, tag,
                                                          disassemble(instr, 1, False)))
    return "\n".join(lines)


if __name__ == "__main__":
    print(listing())
    rep = check_program()
    print("states %d, unreached %s, errors %d" % (rep["states"], rep["unreached"], len(rep["errors"])))
    for e in rep["errors"]:
        print("ERROR", e)
