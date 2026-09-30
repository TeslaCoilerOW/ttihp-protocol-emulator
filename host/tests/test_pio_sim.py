# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""host/pio/sim.py against the PIO instruction semantics of the RP2040
datasheet (chapter 3, "PIO"; the RP2350 datasheet, chapter 11, keeps them).
Test docstrings name the datasheet topic (instruction or functional detail).

Each test names the rule it checks. Programs are assembled with host/pio/asm.py
(the MicroPython rp2.asm_pio DSL) and run on one state machine.
"""

import unittest

import support  # noqa: F401  (sys.path)
from pio.asm import asm_pio, assemble
from pio.sim import PioBlock, PioError, StateMachineConfig, bit_reverse

M32 = 0xFFFFFFFF


def load(func, rp2350=False, raw=0, **cfg):
    """Block with ``func`` at offset 0 on SM0 (enabled); returns (block, sm)."""
    kw = {k: cfg.pop(k) for k in list(cfg) if k in ("sideset_init", "out_init", "set_init",
                                                    "in_shiftdir", "out_shiftdir", "autopush",
                                                    "autopull", "push_thresh", "pull_thresh",
                                                    "side_pindir", "fifo_join")}
    prog, _ = assemble(func, **kw)
    blk = PioBlock(rp2350=rp2350)
    blk.load(list(prog[0]))
    sm = blk.sms[0]
    sm.configure(StateMachineConfig.from_program(prog, rp2350=rp2350, **cfg))
    sm.enabled = True
    return blk, sm


def run(blk, cycles, raw=0):
    for _ in range(cycles):
        blk.step(raw)


class SideSetAndDelay(unittest.TestCase):
    def test_side_set_at_issue_and_held_through_delay(self):
        """Side-set (datasheet "Side-set"): side-set happens at the start of the instruction and
        holds during its delay cycles; the delay follows completion."""
        def prog():
            nop()           .side(1)[3]
            nop()           .side(0)
        blk, sm = load(prog, sideset_init=2, sideset_count=1, sideset_base=5)
        levels = []
        for _ in range(6):
            blk.step(0)
            levels.append((blk.out >> 5) & 1)
        self.assertEqual(levels, [1, 1, 1, 1, 0, 1])

    def test_side_set_asserted_while_stalled_and_delay_after_stall(self):
        """Side-set is asserted when the instruction issues, also if it
        stalls; delay cycles do not start until the instruction completes."""
        def prog():
            pull()          .side(1)[2]
            nop()           .side(0)
        blk, sm = load(prog, sideset_init=2, sideset_count=1, sideset_base=0)
        run(blk, 5)
        self.assertEqual(blk.out & 1, 1)
        self.assertTrue(sm.stalled)
        self.assertEqual(sm.pc, 0)
        sm.put(7)
        seen = []
        for _ in range(4):
            blk.step(0)
            seen.append((sm.pc, blk.out & 1))
        # pull completes, then two delay cycles, then the nop drives side 0
        self.assertEqual(seen, [(1, 1), (1, 1), (1, 1), (0, 0)])

    def test_optional_side_set_keeps_pin_when_absent(self):
        """Optional side-set: with SIDE_EN, an instruction without side-set leaves the pin."""
        def prog():
            nop()           .side(1)
            nop()
            nop()           .side(0)
        blk, sm = load(prog, sideset_init=2, sideset_count=1, sideset_opt=True)
        run(blk, 1)
        self.assertEqual(blk.out & 1, 1)
        run(blk, 1)
        self.assertEqual(blk.out & 1, 1)
        run(blk, 1)
        self.assertEqual(blk.out & 1, 0)

    def test_side_pindir(self):
        """EXECCTRL.SIDE_PINDIR: side-set drives pin directions instead of levels."""
        def prog():
            nop()           .side(1)
        blk, sm = load(prog, sideset_init=2, sideset_count=1, sideset_base=3,
                       sideset_pindir=True)
        blk.oe = 0
        run(blk, 1)
        self.assertEqual((blk.oe >> 3) & 1, 1)
        self.assertEqual((blk.out >> 3) & 1, 0)

    def test_side_set_takes_precedence_over_set_out_and_mov(self):
        """GPIO mapping: "If a side-set overlaps with an OUT/SET performed by that
        state machine on the same cycle, the side-set takes precedence in the
        overlapping region" (MOV PINS as well). Levels and directions are
        separate writes: SET PINDIRS and a level side-set both apply."""
        def prog():
            set(pins, 0b11)     .side(0)
            out(pins, 2)        .side(1)
            mov(pins, null)     .side(1)
            set(pindirs, 0b11)  .side(0)
        blk, sm = load(prog, sideset_init=2, sideset_count=1, sideset_base=1,
                       set_base=0, set_count=2, out_base=0, out_count=2, out_shiftdir=1)
        blk.step(0)
        self.assertEqual(blk.out & 3, 0b01)       # SET wrote 1 to pin 1; side-set 0 wins
        sm.osr, sm.osr_count = 0b00, 0
        blk.step(0)
        self.assertEqual(blk.out & 3, 0b10)       # OUT wrote 0 to pin 1; side-set 1 wins
        blk.out |= 1
        blk.step(0)
        self.assertEqual(blk.out & 3, 0b10)       # MOV wrote 0 to pins 0 and 1; side-set 1 wins
        blk.step(0)
        self.assertEqual((blk.oe & 3, blk.out & 3), (0b11, 0b00))

    def test_highest_numbered_state_machine_wins(self):
        """Output priority: for each GPIO, PIO "applies the write from the
        highest-numbered state machine"."""
        def high():
            set(pins, 1)

        def low():
            set(pins, 0)
        for hi_sm, lo_sm, want in ((0, 2, 0), (1, 0, 1)):
            blk = PioBlock()
            blk.load([assemble(high)[0][0][0], assemble(low)[0][0][0]])
            for index, offset in ((hi_sm, 0), (lo_sm, 1)):
                sm = blk.sms[index]
                sm.configure(StateMachineConfig(set_base=4, set_count=1,
                                                wrap_bottom=offset, wrap_top=offset))
                sm.pc = offset
                sm.enabled = True
            blk.step(0)
            self.assertEqual((blk.out >> 4) & 1, want, (hi_sm, lo_sm))


class Jumps(unittest.TestCase):
    def test_x_dec_always_decrements(self):
        """JMP X--: "scratch X non-zero, prior to decrement"; the
        decrement happens whether or not the jump is taken."""
        def prog():
            label("top")
            jmp(x_dec, "top")
            label("end")
            jmp("end")
        blk, sm = load(prog)
        sm.x = 2
        run(blk, 3)    # X: 2 -> 1 (taken), 1 -> 0 (taken), 0 -> 0xFFFFFFFF (not taken)
        self.assertEqual(sm.x, M32)
        self.assertEqual(sm.pc, 1)

    def test_conditions(self):
        """JMP conditions: !X, !Y, X!=Y, PIN, !OSRE."""
        cases = [("not_x", dict(x=0), True), ("not_x", dict(x=5), False),
                 ("not_y", dict(y=0), True), ("x_not_y", dict(x=1, y=2), True),
                 ("x_not_y", dict(x=3, y=3), False), ("not_osre", dict(osr_count=31), True),
                 ("not_osre", dict(osr_count=32), False)]
        for cond, regs, taken in cases:
            src = ("def prog():\n    jmp(%s, 'far')\n    nop()\n    label('far')\n    nop()\n"
                   % cond)
            env = {}
            exec(src, env)
            blk, sm = load(env["prog"])
            for k, v in regs.items():
                setattr(sm, k, v)
            blk.step(0)
            self.assertEqual(sm.pc, 2 if taken else 1, (cond, regs))

    def test_jmp_pin_reads_through_the_synchroniser(self):
        """Input synchronisers: inputs pass a two-flop synchroniser (two cycles of latency);
        INPUT_SYNC_BYPASS removes it. JMP PIN uses EXECCTRL.JMP_PIN."""
        def prog():
            label("top")
            jmp(pin, "hit")
            jmp("top")
            label("hit")
            jmp("hit")
        blk, sm = load(prog, jmp_pin=7)
        blk.step(1 << 7)      # cycle 0: pin high now
        self.assertNotEqual(sm.pc, 2)
        blk.step(1 << 7)
        blk.step(1 << 7)      # cycle 2 sees cycle 0's level
        seen_at = sm.pc
        blk2, sm2 = load(prog, jmp_pin=7)
        blk2.input_sync_bypass = 1 << 7
        blk2.step(1 << 7)
        self.assertEqual(sm2.pc, 2)
        self.assertIn(seen_at, (2,))

    def test_wrap(self):
        """Program wrapping: after the instruction at WRAP_TOP the PC goes to WRAP_BOTTOM
        (no cycle cost); a taken jump there does not wrap."""
        def prog():
            set(x, 1)
            wrap_target()
            set(y, 2)
            set(y, 3)
            wrap()
            set(x, 9)
        blk, sm = load(prog)
        run(blk, 4)
        self.assertEqual((sm.pc, sm.x, sm.y), (2, 1, 2))

    def test_pc_increments_modulo_32(self):
        """The 5-bit program counter continues at 0 after address 31 when
        WRAP_TOP is elsewhere (the lockstep program's "rd" relies on it)."""
        blk = PioBlock()
        sm = blk.sms[0]
        sm.configure(StateMachineConfig(wrap_top=5, wrap_bottom=0))
        blk.mem[31] = 0xE021      # set x, 1
        blk.mem[0] = 0xE042       # set y, 2
        sm.pc = 31
        sm.enabled = True
        run(blk, 2)
        self.assertEqual((sm.x, sm.y, sm.pc), (1, 2, 1))


class Wait(unittest.TestCase):
    def test_wait_gpio_pin_irq(self):
        """WAIT: GPIO (absolute index), PIN (IN_BASE + index), IRQ; WAIT 1
        IRQ clears the flag when satisfied."""
        def prog():
            wait(1, gpio, 4)
            wait(0, pin, 1)
            wait(1, irq, 3)
            set(x, 7)
        blk, sm = load(prog, in_base=10)
        blk.input_sync_bypass = M32
        blk.step(0)
        self.assertEqual(sm.pc, 0)
        blk.step(1 << 4)
        self.assertEqual(sm.pc, 1)
        blk.step(1 << 11)
        self.assertEqual(sm.pc, 1)
        blk.step(0)
        self.assertEqual(sm.pc, 2)
        blk.step(0)
        self.assertEqual(sm.pc, 2)
        blk.irq |= 1 << 3
        blk.step(0)
        self.assertEqual(sm.pc, 3)
        self.assertEqual(blk.irq, 0)


class InOut(unittest.TestCase):
    def test_in_shift_directions_and_count(self):
        """IN: shift left puts new bits at the LSBs, shift right at the
        MSBs; the input shift count saturates at 32."""
        def prog():
            in_(x, 4)
            in_(y, 4)
        blk, sm = load(prog)
        sm.x, sm.y = 0xA, 0x5
        run(blk, 2)
        self.assertEqual((sm.isr, sm.isr_count), (0xA5, 8))
        blk, sm = load(prog, in_shiftdir=1)
        sm.x, sm.y = 0xA, 0x5
        run(blk, 2)
        self.assertEqual((sm.isr, sm.isr_count), (0x5A000000, 8))

    def test_in_pins_rotates_from_in_base(self):
        """GPIO mapping: IN PINS reads from IN_BASE upward, wrapping modulo 32."""
        def prog():
            in_(pins, 8)
        blk, sm = load(prog, in_base=28)
        blk.input_sync_bypass = M32
        blk.step(0xF0000003)   # GPIO 28..31 high, 0..1 high
        self.assertEqual(sm.isr, 0x3F)

    def test_rp2350_in_count_masks_pin_reads(self):
        """RP2350 SHIFTCTRL.IN_COUNT masks IN PINS and MOV PINS above the count."""
        def prog():
            mov(x, pins)
        blk, sm = load(prog, rp2350=True, in_count=3)
        blk.input_sync_bypass = M32
        blk.step(0xFF)
        self.assertEqual(sm.x, 0x7)

    def test_autopush_and_stall_when_full(self):
        """Autopush: pushes when the count reaches PUSH_THRESH; with the RX
        FIFO full the IN stalls."""
        def prog():
            label("top")
            in_(x, 8)
            jmp("top")
        blk, sm = load(prog, autopush=True, push_thresh=16)
        sm.x = 0x11
        run(blk, 4)
        self.assertEqual(list(sm.rx), [0x1111])
        run(blk, 60)
        self.assertEqual(len(sm.rx), 4)
        self.assertTrue(sm.stalled)
        self.assertEqual(sm.pc, 0)

    def test_out_shift_directions(self):
        """OUT: shift right takes the LSBs, shift left the MSBs."""
        def prog():
            out(x, 8)
            out(y, 8)
        blk, sm = load(prog, out_shiftdir=1)
        sm.osr, sm.osr_count = 0x12345678, 0
        run(blk, 2)
        self.assertEqual((sm.x, sm.y, sm.osr_count), (0x78, 0x56, 16))
        blk, sm = load(prog)
        sm.osr, sm.osr_count = 0x12345678, 0
        run(blk, 2)
        self.assertEqual((sm.x, sm.y), (0x12, 0x34))

    def test_out_pins_only_touch_the_out_group(self):
        """GPIO mapping: OUT PINS writes OUT_COUNT pins from OUT_BASE; data bits
        beyond the bit count are zero."""
        def prog():
            out(pins, 4)
        blk, sm = load(prog, out_base=8, out_count=6, out_shiftdir=1)
        blk.out = 0xFFFFFFFF
        sm.osr, sm.osr_count = 0xA, 0
        blk.step(0)
        self.assertEqual((blk.out >> 8) & 0x3F, 0x0A)
        self.assertEqual(blk.out & 0xFF, 0xFF)
        self.assertEqual(blk.out >> 14, 0x3FFFF)

    def test_out_pc_isr_exec(self):
        """OUT: OUT PC jumps; OUT ISR sets the ISR and its count to the bit
        count; OUT EXEC runs the data as the next instruction."""
        def prog():
            out(pc, 5)
        blk, sm = load(prog, out_shiftdir=1)
        sm.osr, sm.osr_count = 17, 0
        blk.step(0)
        self.assertEqual(sm.pc, 17)

        def prog2():
            out(isr, 12)
        blk, sm = load(prog2, out_shiftdir=1)
        sm.osr, sm.osr_count = 0xABC, 0
        blk.step(0)
        self.assertEqual((sm.isr, sm.isr_count), (0xABC, 12))

        def prog3():
            out(exec, 16)
            set(y, 1)
        blk, sm = load(prog3, out_shiftdir=1)
        sm.osr, sm.osr_count = 0xE025, 0       # set x, 5
        blk.step(0)
        blk.step(0)
        self.assertEqual((sm.x, sm.y, sm.pc), (5, 0, 1))
        blk.step(0)
        self.assertEqual(sm.y, 1)

    def test_out_exec_and_mov_exec_ignore_their_own_delay(self):
        """OUT EXEC: "Delay cycles on the initial OUT are ignored, but the
        executee may insert delay cycles as normal"; MOV EXEC: "Delay cycles on
        MOV EXEC are ignored". The executee runs on the next cycle and does not
        advance the PC."""
        def prog():
            out(exec, 16)   [3]
            set(y, 1)
        blk, sm = load(prog, out_shiftdir=1)
        sm.osr, sm.osr_count = 0xE225, 0          # set x, 5 [2]
        blk.step(0)
        blk.step(0)                               # the executee, right after the OUT
        self.assertEqual((sm.x, sm.y, sm.pc, sm.delay), (5, 0, 1, 2))
        run(blk, 2)                               # the executee's two delay cycles
        self.assertEqual(sm.y, 0)
        blk.step(0)
        self.assertEqual(sm.y, 1)

        def prog2():
            mov(exec, x)    [3]
            set(y, 1)
        blk, sm = load(prog2)
        sm.x = 0xE025                             # set x, 5
        blk.step(0)
        blk.step(0)
        self.assertEqual((sm.x, sm.y, sm.pc), (5, 0, 1))
        blk.step(0)
        self.assertEqual(sm.y, 1)

    def test_autopull_stalls_empty_and_refills(self):
        """Autopull details (pseudocode for 'OUT' cycles): an OUT on an empty OSR
        stalls; it refills from the TX FIFO in that cycle and shifts on the
        next ("it cannot fill an empty OSR and 'OUT' it on the same cycle"). An
        OUT that empties the OSR refills it in the same cycle."""
        def prog():
            label("top")
            out(x, 32)
            jmp("top")
        blk, sm = load(prog, autopull=True, pull_thresh=32, out_shiftdir=1)
        run(blk, 3)
        self.assertTrue(sm.stalled)
        self.assertTrue(sm.txstall)
        sm.put(1)
        sm.put(2)
        blk.step(0)                    # refill from the FIFO, still stalled
        self.assertEqual((sm.x, sm.stalled, sm.pc, sm.osr_count, len(sm.tx)), (0, True, 0, 0, 1))
        blk.step(0)                    # out x: 1; the emptied OSR refills with 2
        self.assertEqual((sm.x, sm.stalled, sm.osr, sm.osr_count, len(sm.tx)), (1, False, 2, 0, 0))
        run(blk, 2)                    # jmp, then out x: 2 without a stall
        self.assertEqual((sm.x, sm.stalled), (2, False))

    def test_autopull_refills_on_other_cycles(self):
        """Autopull details (pseudocode for non-'OUT' cycles): other instructions
        and delay cycles refill an empty OSR when the TX FIFO has data, so the
        next OUT does not stall."""
        def prog():
            nop()
            out(x, 32)      [2]
            out(y, 32)
        blk, sm = load(prog, autopull=True, pull_thresh=32, out_shiftdir=1)
        sm.put(5)
        blk.step(0)                    # nop: the empty OSR (count 32 after restart) refills
        self.assertEqual((sm.osr, sm.osr_count, len(sm.tx)), (5, 0, 0))
        blk.step(0)                    # out x without a stall, OSR empty again
        self.assertEqual((sm.x, sm.stalled, sm.osr_count), (5, False, 32))
        sm.put(6)
        run(blk, 2)                    # the OUT's two delay cycles refill the OSR
        self.assertEqual((sm.osr, sm.osr_count, len(sm.tx)), (6, 0, 0))
        blk.step(0)
        self.assertEqual((sm.y, sm.stalled), (6, False))


class PushPull(unittest.TestCase):
    def test_pull_noblock_copies_x(self):
        """PULL: "a nonblocking PULL on an empty FIFO has the same effect
        as MOV OSR, X"; it empties the output shift count."""
        def prog():
            pull(noblock)
        blk, sm = load(prog)
        sm.x = 0x1234
        blk.step(0)
        self.assertEqual((sm.osr, sm.osr_count), (0x1234, 0))

    def test_pull_ifempty(self):
        """PULL IfEmpty does nothing unless the output shift count reached
        PULL_THRESH."""
        def prog():
            pull(ifempty, block)
        blk, sm = load(prog, pull_thresh=16)
        sm.put(9)
        sm.osr, sm.osr_count = 5, 8
        blk.step(0)
        self.assertEqual((sm.osr, len(sm.tx)), (5, 1))

    def test_pull_with_autopull_is_a_fence(self):
        """PULL note: "When autopull is enabled, any PULL instruction is a no-op
        when the OSR is full"; on an empty OSR it pulls, and stalls (Block)
        while the FIFO is empty. "Full" is read as not empty by PULL_THRESH (see
        sim.py), so a partly shifted OSR is kept as well."""
        def prog():
            pull(block)
        blk, sm = load(prog, autopull=True, pull_thresh=32)
        sm.osr, sm.osr_count = 0x1234, 0
        sm.put(0x99)
        blk.step(0)                    # full OSR: no-op
        self.assertEqual((sm.osr, sm.osr_count, len(sm.tx), sm.stalled), (0x1234, 0, 1, False))
        sm.osr_count = 8
        blk.step(0)                    # partly shifted: no-op
        self.assertEqual((sm.osr, sm.osr_count, len(sm.tx)), (0x1234, 8, 1))
        sm.osr_count = 32
        blk.step(0)                    # empty: pulls
        self.assertEqual((sm.osr, sm.osr_count, len(sm.tx)), (0x99, 0, 0))
        sm.osr_count = 32
        blk.step(0)                    # empty, FIFO empty: stalls
        self.assertEqual((sm.stalled, sm.txstall), (True, True))
        blk2, sm2 = load(prog, pull_thresh=32)   # autopull off: PULL always pulls
        sm2.osr, sm2.osr_count = 0x1234, 0
        sm2.put(0x99)
        blk2.step(0)
        self.assertEqual((sm2.osr, len(sm2.tx)), (0x99, 0))

    def test_push_noblock_full_drops_and_clears(self):
        """PUSH: without Block, a full RX FIFO leaves the FIFO unchanged,
        still clears the ISR and sets FDEBUG_RXSTALL."""
        def prog():
            push(noblock)
        blk, sm = load(prog)
        for i in range(4):
            sm.rx.append(i)
        sm.isr, sm.isr_count = 0x55, 8
        blk.step(0)
        self.assertEqual((list(sm.rx), sm.isr, sm.isr_count, sm.rxstall),
                         ([0, 1, 2, 3], 0, 0, True))

    def test_push_block_stalls_and_iffull(self):
        """PUSH: Block stalls on a full FIFO; IfFull pushes only at PUSH_THRESH."""
        def prog():
            push(iffull, block)
            push(block)
        blk, sm = load(prog, push_thresh=8)
        sm.isr, sm.isr_count = 3, 4
        blk.step(0)
        self.assertEqual(list(sm.rx), [])
        blk.step(0)
        self.assertEqual(list(sm.rx), [3])
        blk2, sm2 = load(prog, push_thresh=8)
        for i in range(4):
            sm2.rx.append(i)
        sm2.isr_count = 8
        blk2.step(0)
        self.assertTrue(sm2.stalled)

    def test_fifo_join_depths(self):
        """FIFO joining: gives one 8-deep FIFO and disables the other."""
        blk = PioBlock()
        sm = blk.sms[0]
        sm.configure(StateMachineConfig(fjoin_tx=True))
        self.assertEqual((sm.tx_depth, sm.rx_depth), (8, 0))


class Mov(unittest.TestCase):
    def test_invert_reverse_null(self):
        """MOV: operations invert (~) and bit-reverse (::)."""
        def prog():
            mov(x, invert(null))
            mov(y, reverse(x))
            mov(isr, reverse(y))
        blk, sm = load(prog)
        sm.x = 0
        run(blk, 1)
        self.assertEqual(sm.x, M32)
        sm.x = 1
        run(blk, 1)
        self.assertEqual(sm.y, 0x80000000)
        run(blk, 1)
        self.assertEqual((sm.isr, sm.isr_count), (1, 0))
        self.assertEqual(bit_reverse(0x12345678), 0x1E6A2C48)

    def test_status_tx_level(self):
        """MOV STATUS: all-ones if the FIFO level (STATUS_SEL) is below
        STATUS_N, else all-zeros."""
        def prog():
            label("top")
            mov(x, status)
            jmp("top")
        blk, sm = load(prog, status_sel=0, status_n=1)
        blk.step(0)
        self.assertEqual(sm.x, M32)
        sm.put(1)
        run(blk, 2)
        self.assertEqual(sm.x, 0)

    def test_mov_osr_fills_and_mov_pc(self):
        """MOV: MOV to OSR resets the output shift counter (OSR full, !OSRE
        true); MOV to PC jumps."""
        def prog():
            mov(osr, x)
            jmp(not_osre, "full")
            nop()
            label("full")
            mov(pc, y)
        blk, sm = load(prog)
        sm.x, sm.y = 7, 0
        run(blk, 2)
        self.assertEqual(sm.pc, 3)
        blk.step(0)
        self.assertEqual(sm.pc, 0)

    def test_mov_pindirs_needs_rp2350(self):
        """MOV destination 011 is PINDIRS on the RP2350 and reserved on the
        RP2040. (MicroPython's DSL maps ``pindirs`` to 4, which for MOV is EXEC,
        so the instruction is written as a raw word.)"""
        def prog():
            word(0xA061)          # mov pindirs, x
        blk, sm = load(prog, out_count=4)
        with self.assertRaises(PioError):
            blk.step(0)
        blk, sm = load(prog, rp2350=True, out_count=4)
        sm.x = 0xF
        blk.step(0)
        self.assertEqual(blk.oe & 0xF, 0xF)


class SetIrq(unittest.TestCase):
    def test_set(self):
        """SET: 5-bit immediate to PINS (SET_BASE/SET_COUNT), X, Y, PINDIRS."""
        def prog():
            set(pins, 0b101)
            set(pindirs, 0b111)
            set(x, 31)
        blk, sm = load(prog, set_base=6, set_count=3)
        run(blk, 3)
        self.assertEqual(((blk.out >> 6) & 7, (blk.oe >> 6) & 7, sm.x), (5, 7, 31))

    def test_irq_set_clear_wait_rel(self):
        """IRQ: set, clear, wait (stall until cleared); REL adds the state
        machine number modulo 4 to the flag index."""
        def prog():
            irq(rel(1))
            irq(clear, 1)
            irq(block, 2)
            set(x, 1)
        blk = PioBlock()
        prog_list, _ = assemble(prog)
        blk.load(list(prog_list[0]))
        sm = blk.sms[2]
        sm.configure(StateMachineConfig.from_program(prog_list))
        sm.enabled = True
        blk.step(0)
        self.assertEqual(blk.irq, 1 << 3)        # flag (1 + 2) mod 4
        blk.irq |= 1 << 1
        blk.step(0)
        self.assertEqual(blk.irq, 1 << 3)
        run(blk, 3)
        self.assertEqual((sm.pc, blk.irq & 4), (2, 4))
        blk.irq &= ~4
        run(blk, 2)
        self.assertEqual(sm.x, 1)

    def test_unmodelled_and_reserved_encodings_raise(self):
        """Not modelled (sim.py docstring): the RP2350's IRQ index modes PREV (01)
        and NEXT (11), for IRQ and WAIT IRQ, and its MOV to/from the RX FIFO
        registers. PUSH/PULL with bits 4:0 set is reserved on the RP2040."""
        cases = [(0xC00B, True),    # irq prev 3
                 (0xC01B, True),    # irq next 3
                 (0x20CB, True),    # wait 1 irq prev 3
                 (0x8010, True),    # mov rxfifo[y], isr
                 (0x8090, True),    # mov osr, rxfifo[y]
                 (0x8010, False)]   # reserved on the RP2040
        for word_, rp2350 in cases:
            blk = PioBlock(rp2350=rp2350)
            blk.load([word_])
            sm = blk.sms[0]
            sm.configure(StateMachineConfig(wrap_top=0))
            sm.enabled = True
            with self.assertRaises(PioError, msg=hex(word_)):
                blk.step(0)
        blk = PioBlock()                           # RP2040: bit 3 of the index is not a mode bit
        blk.load([0xC00B])
        sm = blk.sms[0]
        sm.configure(StateMachineConfig(wrap_top=0))
        sm.enabled = True
        blk.step(0)
        self.assertEqual(blk.irq, 1 << 3)


class ForcedExec(unittest.TestCase):
    def test_exec_on_disabled_sm(self):
        """Forced instructions (SMx_INSTR): executes immediately, also while the state machine
        is disabled (pico-sdk pio_sm_init uses it to set the PC)."""
        blk = PioBlock()
        sm = blk.sms[0]
        sm.configure(StateMachineConfig())
        sm.exec(0x0000 | 12)     # jmp 12
        blk.step(0)
        self.assertEqual(sm.pc, 12)
        sm.exec(0xE043)          # set y, 3
        blk.step(0)
        self.assertEqual(sm.y, 3)


class OutputTiming(unittest.TestCase):
    def test_output_visible_to_input_three_cycles_later(self):
        """Outputs change at the end of the executing cycle; the synchroniser
        adds two cycles: an IN reads its own output three cycles later."""
        def prog():
            set(pins, 1)
            label("l")
            jmp(pin, "hit")
            jmp("l")
            label("hit")
            set(x, 1)
        blk, sm = load(prog, set_base=0, set_count=1, jmp_pin=0)
        blk.oe = 1
        cycles = 0
        while sm.x != 1 and cycles < 20:
            blk.step(blk.out & blk.oe)
            cycles += 1
        # set (cycle 0) -> pin high during cycle 1 -> seen by the jmp in cycle 3
        self.assertEqual(cycles, 5)


class Assembler(unittest.TestCase):
    def test_decorator_matches_function_form(self):
        def prog():
            nop()
        a = asm_pio()(prog)
        self.assertEqual(list(a[0]), [0xA042])
        self.assertEqual(len(a), 9)


if __name__ == "__main__":
    unittest.main()
