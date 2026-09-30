# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Cycle-accurate interpreter of an RP2040/RP2350 PIO block.

The semantics follow the RP2040 datasheet, chapter 3 (PIO; build 3184e62,
2025-02-20), and the RP2350 datasheet, chapter 11 (build d126e9e,
2025-07-29), which keeps the RP2040 instruction set and adds a few features
(``rp2350=True`` enables the ones modelled here: MOV to PINDIRS, the IN_COUNT
mask on pin reads, WAIT JMPPIN and the IRQ source of MOV STATUS).
host/tests/test_pio_sim.py checks each rule below against the datasheet text.
Section numbers below are the RP2040's; the RP2350 datasheet has the same text
in 11.4 (instructions; its MOV is 11.4.10) and 11.5 (functional details).

One ``PioBlock.step(raw)`` is one PIO clock (clock divider 1, or an integer
divider per state machine). ``raw`` holds the pin levels during this cycle,
bit i = GPIO (gpio_base + i) of the block's 32-pin window.

Timing model
------------
* Inputs pass a two-flop synchroniser (3.5.6.3: "adds two cycles of latency"):
  an instruction executing in cycle c sees ``raw`` of cycle c - 2. Bits set
  in ``input_sync_bypass`` see ``raw`` of cycle c.
* Outputs (OUT/MOV/SET to PINS or PINDIRS, side-set) take effect at the end of
  the cycle in which the instruction executes; the caller reads them from
  ``PioBlock.out``/``PioBlock.oe`` after ``step`` and they are part of
  ``raw`` from the next cycle on.
* Side-set is applied when an instruction is issued, also if it then stalls,
  and holds through its delay cycles. Delay cycles start once the instruction
  completes (a stalled instruction does not count down its delay).
* Output priority (3.5.6): "If a side-set overlaps with an OUT/SET performed
  by that state machine on the same cycle, the side-set takes precedence in
  the overlapping region" (modelled for MOV PINS/PINDIRS too). Between state
  machines the highest-numbered one wins (3.5.6.1), separately for levels
  and directions.
* JMP X--/Y-- always decrements; the condition uses the value before it.
* PULL NOBLOCK on an empty TX FIFO copies X to the OSR. PUSH NOBLOCK on a full
  RX FIFO drops the word, still clears the ISR and sets FDEBUG.RXSTALL.
* PUSH IFFULL / PULL IFEMPTY do nothing below the push/pull threshold, also
  without autopush/autopull (3.4.6.2, 3.4.7.2).
* MOV to OSR empties the output shift counter (OSR full); MOV to ISR clears
  the input shift counter; OUT to ISR sets it to the bit count.
* Autopull (3.5.4.2 pseudocode): an OUT that finds the OSR empty (shift
  count >= threshold) stalls; when the TX FIFO has data it refills the OSR in
  that cycle and shifts on the next ("it cannot fill an empty OSR and 'OUT' it
  on the same cycle"). An OUT that empties the OSR refills it in the same
  cycle when the FIFO has data. On the running state machine's other cycles
  (other instructions, stalls and delay cycles) an empty OSR is refilled when
  the FIFO has data, after that cycle's instruction.
* PULL with autopull enabled is a no-op when the OSR is full (3.4.7.2 note,
  3.5.4.2). "Full" is read here as "not empty" by the pull threshold (the
  test of IfEmpty and JMP !OSRE): the reading under which 3.5.4.2's "either
  an autopull has already taken place ... or the program will stall on the
  'PULL'" also holds for a partly shifted OSR. The two readings differ only
  there.
* Autopush: an IN that reaches the threshold pushes in the same cycle and
  stalls (without effect) while the RX FIFO is full.
* Instructions from SMx_INSTR (``exec``) run in the next cycle in place of
  the program, their delay is ignored and the PC is not advanced (unless
  they jump) (3.5.7). OUT EXEC / MOV EXEC run the data on the next cycle;
  "Delay cycles on the initial OUT are ignored, but the executee may insert
  delay cycles as normal" (3.4.5.2; MOV EXEC likewise, 3.4.8.2).

Not modelled
------------
* RP2350: the IRQ index modes PREV and NEXT (flags of neighbouring PIO
  blocks) and MOV to/from the RX FIFO registers (FJOIN_RX_PUT/GET): both
  raise PioError. CTRL.NEXTPREV_* operations, GPIOBASE (the caller works in
  window coordinates) and the Secure/Non-secure GPIO restrictions.
* Both chips: fractional clock dividers (integer only), CLKDIV_RESTART and
  SM_RESTART timing; EXECCTRL.OUT_STICKY, INLINE_OUT_EN and OUT_EN_SEL;
  system interrupts (IRQ0/IRQ1) and DREQ; FDEBUG apart from TXSTALL, RXSTALL
  and the CPU-side TXOVER; the EXEC_STALLED flag; GPIO overrides and pad
  behaviour outside the PIO block. MOV from the OSR while autopull is enabled
  is undefined in the datasheet (a race against the FIFO); the model is
  deterministic (the background refill follows the cycle's instruction).
* Reserved encodings (WAIT source 11 on the RP2040, the RP2040's
  PUSH/PULL bits 4:0, MOV destination 011 on the RP2040, the reserved MOV
  operation, reserved IN and MOV sources, reserved SET destinations) raise
  PioError.
* rp2040js 1.4.0 differs from the datasheet (and from this model) in PUSH
  IfFull / PULL IfEmpty without autopush/autopull, the autopull refill
  timing and the PULL fence; host/tests/test_pio_sim.py follows the
  datasheet.
"""

from collections import deque

M32 = 0xFFFFFFFF


def _mask(n):
    return M32 if n >= 32 else (1 << n) - 1


def rotate_right(value, n):
    n &= 31
    return ((value >> n) | (value << (32 - n))) & M32 if n else value & M32


def bit_reverse(value):
    out = 0
    for i in range(32):
        out = (out << 1) | ((value >> i) & 1)
    return out


class PioError(Exception):
    pass


class StateMachineConfig:
    """Register fields of one state machine (CLKDIV, EXECCTRL, SHIFTCTRL, PINCTRL)."""

    def __init__(self, **kw):
        self.clkdiv = 1
        self.wrap_bottom = 0
        self.wrap_top = 31
        self.sideset_count = 0       # side-set data bits (without the enable bit)
        self.sideset_opt = False     # EXECCTRL.SIDE_EN
        self.sideset_pindir = False  # EXECCTRL.SIDE_PINDIR
        self.sideset_base = 0
        self.out_base = 0
        self.out_count = 0
        self.set_base = 0
        self.set_count = 5
        self.in_base = 0
        self.in_count = 0            # RP2350 IN_COUNT (0 = no masking)
        self.jmp_pin = 0
        self.in_shift_right = False
        self.out_shift_right = False
        self.autopush = False
        self.autopull = False
        self.push_thresh = 32
        self.pull_thresh = 32
        self.fjoin_tx = False
        self.fjoin_rx = False
        self.status_sel = 0          # 0 TX level, 1 RX level, 2 IRQ flag (RP2350)
        self.status_n = 0
        for key, value in kw.items():
            if not hasattr(self, key):
                raise PioError("unknown config field %s" % key)
            setattr(self, key, value)

    @classmethod
    def from_program(cls, prog, offset=0, rp2350=False, **kw):
        """Config from a MicroPython asm_pio program list loaded at ``offset``
        (as MicroPython's StateMachine.init builds it; either list layout)."""
        base = len(prog) - 5
        execctrl, shiftctrl = prog[base], prog[base + 1]
        sideset_init = prog[base + 4]
        if sideset_init is None:
            count = 0
        elif isinstance(sideset_init, int):
            count = 1
        else:
            count = len(sideset_init)
        fields = dict(
            wrap_bottom=((execctrl >> 7) & 0x1F) + offset,
            wrap_top=((execctrl >> 12) & 0x1F) + offset,
            sideset_count=count, sideset_opt=bool(execctrl >> 30 & 1),
            sideset_pindir=bool(execctrl >> 29 & 1),
            status_sel=execctrl >> 5 & 3 if rp2350 else execctrl >> 4 & 1,
            status_n=execctrl & (0x1F if rp2350 else 0xF),
            in_shift_right=bool(shiftctrl >> 18 & 1), out_shift_right=bool(shiftctrl >> 19 & 1),
            autopush=bool(shiftctrl >> 16 & 1), autopull=bool(shiftctrl >> 17 & 1),
            push_thresh=(shiftctrl >> 20 & 0x1F) or 32, pull_thresh=(shiftctrl >> 25 & 0x1F) or 32,
            fjoin_tx=bool(shiftctrl >> 30 & 1), fjoin_rx=bool(shiftctrl >> 31 & 1),
        )
        fields.update(kw)
        return cls(**fields)


class StateMachine:
    def __init__(self, block, index):
        self.block = block
        self.index = index
        self.config = StateMachineConfig()
        self.enabled = False
        self.reset_state()

    def reset_state(self):
        self.pc = 0
        self.x = 0
        self.y = 0
        self.isr = 0
        self.osr = 0
        self.isr_count = 0
        self.osr_count = 32
        self.delay = 0
        self.div_count = 0
        self.forced = None       # instruction from SMx_INSTR
        self.exec_next = None    # instruction from OUT/MOV EXEC
        self.tx = deque()
        self.rx = deque()
        self.stalled = False
        self.rxstall = False     # FDEBUG.RXSTALL (sticky)
        self.txstall = False     # FDEBUG.TXSTALL (sticky)
        self.txover = False
        self.executed = 0        # instructions completed
        self.last_pc = None      # address of the instruction issued this cycle

    # ------------------------------------------------------------- configuration
    def configure(self, config):
        self.config = config
        self.restart()
        self.tx.clear()
        self.rx.clear()

    def restart(self):
        """SM_RESTART: clears shift counters, delay and stall state (not X, Y, FIFOs)."""
        self.isr = 0
        self.isr_count = 0
        self.osr_count = 32
        self.delay = 0
        self.div_count = 0
        self.stalled = False
        self.exec_next = None

    @property
    def tx_depth(self):
        c = self.config
        return 8 if c.fjoin_tx else (0 if c.fjoin_rx else 4)

    @property
    def rx_depth(self):
        c = self.config
        return 8 if c.fjoin_rx else (0 if c.fjoin_tx else 4)

    # ----------------------------------------------------------------- CPU side
    def put(self, word):
        """CPU write to TXF; returns False (TXOVER) when the FIFO is full."""
        if len(self.tx) >= self.tx_depth:
            self.txover = True
            return False
        self.tx.append(word & M32)
        return True

    def get(self):
        """CPU read of RXF; None when empty."""
        if not self.rx:
            return None
        return self.rx.popleft()

    def exec(self, instr):
        self.forced = instr & 0xFFFF

    # ------------------------------------------------------------------- helpers
    def _status(self):
        c = self.config
        if c.status_sel == 0:
            return M32 if len(self.tx) < c.status_n else 0
        if c.status_sel == 1:
            return M32 if len(self.rx) < c.status_n else 0
        if c.status_sel == 2 and self.block.rp2350:
            return M32 if self.block.irq >> (c.status_n & 7) & 1 else 0
        raise PioError("reserved STATUS_SEL")

    def _pins_in(self, visible):
        c = self.config
        value = rotate_right(visible, c.in_base)
        if self.block.rp2350 and c.in_count:
            value &= _mask(c.in_count)
        return value

    def _write_pins(self, base, count, value, dirs=False):
        blk = self.block
        for i in range(count):
            pin = (base + i) & 31
            bit = (value >> i) & 1
            if dirs:
                blk.oe = (blk.oe & ~(1 << pin)) | (bit << pin)
            else:
                blk.out = (blk.out & ~(1 << pin)) | (bit << pin)

    def _irq_index(self, index):
        if self.block.rp2350 and index & 0x08:
            # IdxMode 01 (PREV) / 11 (NEXT): IRQ flags of a neighbouring block
            raise PioError("IRQ index mode PREV/NEXT (RP2350) is not modelled")
        if index & 0x10:
            return (index & 0x4) | ((index + self.index) & 3)
        return index & 7

    def _side_set(self, instr):
        c = self.config
        if not c.sideset_count and not c.sideset_opt:
            return None
        bits = c.sideset_count + (1 if c.sideset_opt else 0)
        field = (instr >> 8) & 0x1F
        if c.sideset_opt and not field & 0x10:
            return None
        return (field >> (5 - bits)) & _mask(c.sideset_count)

    def _delay(self, instr):
        c = self.config
        bits = c.sideset_count + (1 if c.sideset_opt else 0)
        return (instr >> 8) & _mask(5 - bits)

    def _refill(self):
        self.osr = self.tx.popleft()
        self.osr_count = 0

    def _background_pull(self):
        """Autopull on a cycle that is not an OUT (3.5.4.2)."""
        c = self.config
        if c.autopull and self.osr_count >= c.pull_thresh and self.tx:
            self._refill()

    def _shift_out(self, n):
        n = n or 32
        if self.config.out_shift_right:
            data = self.osr & _mask(n)
            self.osr = 0 if n >= 32 else self.osr >> n
        else:
            data = (self.osr >> (32 - n)) & _mask(n) if n < 32 else self.osr
            self.osr = 0 if n >= 32 else (self.osr << n) & M32
        self.osr_count = min(32, self.osr_count + n)
        return data

    def _shift_in_value(self, data, n):
        n = n or 32
        data &= _mask(n)
        if n >= 32:
            return data, 32
        if self.config.in_shift_right:
            isr = (self.isr >> n) | (data << (32 - n))
        else:
            isr = ((self.isr << n) | data) & M32
        return isr & M32, min(32, self.isr_count + n)

    # ------------------------------------------------------------------ one clock
    def clock(self, visible):
        """Advance one PIO clock. Returns True when an instruction was issued."""
        self.last_pc = None
        if not self.enabled and self.forced is None:
            return False
        c = self.config
        if self.enabled and self.forced is None:
            self.div_count += 1
            if self.div_count < c.clkdiv:
                return False
            self.div_count = 0
            if self.delay:
                self.delay -= 1
                self._background_pull()
                return False
        if self.forced is not None:
            instr, source = self.forced, "forced"
        elif self.exec_next is not None:
            instr, source = self.exec_next, "exec"
            self.exec_next = None
        else:
            instr, source = self.block.mem[self.pc], "mem"
            self.last_pc = self.pc
        stalled, jumped = self._execute(instr, visible)
        # Side-set after the instruction's own pin write: where both write the
        # same pin in this cycle, side-set takes precedence (3.5.6).
        side = self._side_set(instr)
        if side is not None:
            self._write_pins(c.sideset_base, c.sideset_count, side, dirs=c.sideset_pindir)
        if self.enabled and instr >> 13 != 3:
            self._background_pull()
        self.stalled = stalled
        if stalled:
            if source == "forced":
                self.forced = instr   # a stalled forced instruction stays pending
            elif source == "exec":
                self.exec_next = instr
            return True
        self.executed += 1
        if source == "forced":
            self.forced = None
            return True
        # OUT EXEC / MOV EXEC: the executee runs on the next cycle and the
        # delay of the OUT/MOV itself is ignored (3.4.5.2, 3.4.8.2).
        self.delay = 0 if self.exec_next is not None else self._delay(instr)
        if source == "mem" and not jumped:
            self.pc = c.wrap_bottom if self.pc == c.wrap_top else (self.pc + 1) & 31
        return True

    def _execute(self, instr, visible):
        """Execute one instruction; returns (stalled, jumped)."""
        c = self.config
        blk = self.block
        op = instr >> 13
        low = instr & 0xFF
        if op == 0:  # JMP
            cond = (low >> 5) & 7
            addr = low & 0x1F
            if cond == 0:
                take = True
            elif cond == 1:
                take = self.x == 0
            elif cond == 2:
                take = self.x != 0
                self.x = (self.x - 1) & M32
            elif cond == 3:
                take = self.y == 0
            elif cond == 4:
                take = self.y != 0
                self.y = (self.y - 1) & M32
            elif cond == 5:
                take = self.x != self.y
            elif cond == 6:
                take = bool((visible >> c.jmp_pin) & 1)
            else:
                take = self.osr_count < c.pull_thresh
            if take:
                self.pc = addr
            return False, take
        if op == 1:  # WAIT
            pol = (low >> 7) & 1
            src = (low >> 5) & 3
            index = low & 0x1F
            if src == 0:
                level = (visible >> index) & 1
            elif src == 1:
                level = (visible >> ((c.in_base + index) & 31)) & 1
            elif src == 2:
                flag = self._irq_index(index)
                level = (blk.irq >> flag) & 1
                if level == pol and pol == 1:
                    blk.irq &= ~(1 << flag)
                    return False, False
            elif blk.rp2350:
                level = (visible >> ((c.jmp_pin + (index & 3)) & 31)) & 1
            else:
                raise PioError("reserved WAIT source")
            return level != pol, False
        if op == 2:  # IN
            src = (low >> 5) & 7
            n = low & 0x1F
            if src == 0:
                data = self._pins_in(visible)
            elif src == 1:
                data = self.x
            elif src == 2:
                data = self.y
            elif src == 3:
                data = 0
            elif src == 6:
                data = self.isr
            elif src == 7:
                data = self.osr
            else:
                raise PioError("reserved IN source")
            isr, count = self._shift_in_value(data, n)
            if c.autopush and count >= c.push_thresh:
                if len(self.rx) >= self.rx_depth:
                    self.rxstall = True
                    return True, False
                self.rx.append(isr)
                self.isr, self.isr_count = 0, 0
            else:
                self.isr, self.isr_count = isr, count
            return False, False
        if op == 3:  # OUT
            dest = (low >> 5) & 7
            n = low & 0x1F
            if c.autopull and self.osr_count >= c.pull_thresh:
                # 3.5.4.2: refill if possible, and stall either way
                if self.tx:
                    self._refill()
                else:
                    self.txstall = True
                return True, False
            data = self._shift_out(n)
            if c.autopull and self.osr_count >= c.pull_thresh and self.tx:
                self._refill()
            bits = n or 32
            if dest == 0:
                self._write_pins(c.out_base, c.out_count, data)
            elif dest == 1:
                self.x = data
            elif dest == 2:
                self.y = data
            elif dest == 3:
                pass
            elif dest == 4:
                self._write_pins(c.out_base, c.out_count, data, dirs=True)
            elif dest == 5:
                self.pc = data & 0x1F
                return False, True
            elif dest == 6:
                self.isr, self.isr_count = data, bits
            else:
                self.exec_next = data & 0xFFFF
            return False, False
        if op == 4:  # PUSH / PULL
            if low & 0x1F:
                if blk.rp2350 and low & 0x10:
                    raise PioError("MOV to/from the RX FIFO registers (RP2350) is not modelled")
                raise PioError("reserved PUSH/PULL encoding")
            if_flag = (low >> 6) & 1
            block = (low >> 5) & 1
            if low & 0x80:  # PULL
                # IfEmpty; with autopull every PULL is a no-op on a full OSR
                if (if_flag or c.autopull) and self.osr_count < c.pull_thresh:
                    return False, False
                if not self.tx:
                    if block:
                        self.txstall = True
                        return True, False
                    self.osr, self.osr_count = self.x, 0
                    return False, False
                self._refill()
                return False, False
            if if_flag and self.isr_count < c.push_thresh:
                return False, False
            if len(self.rx) >= self.rx_depth:
                self.rxstall = True
                if block:
                    return True, False
                self.isr, self.isr_count = 0, 0
                return False, False
            self.rx.append(self.isr)
            self.isr, self.isr_count = 0, 0
            return False, False
        if op == 5:  # MOV
            dest = (low >> 5) & 7
            operation = (low >> 3) & 3
            src = low & 7
            if src == 0:
                value = self._pins_in(visible)
            elif src == 1:
                value = self.x
            elif src == 2:
                value = self.y
            elif src == 3:
                value = 0
            elif src == 5:
                value = self._status()
            elif src == 6:
                value = self.isr
            elif src == 7:
                value = self.osr
            else:
                raise PioError("reserved MOV source")
            if operation == 1:
                value = ~value & M32
            elif operation == 2:
                value = bit_reverse(value)
            elif operation == 3:
                raise PioError("reserved MOV operation")
            if dest == 0:
                self._write_pins(c.out_base, c.out_count, value)
            elif dest == 1:
                self.x = value
            elif dest == 2:
                self.y = value
            elif dest == 3:
                if not blk.rp2350:
                    raise PioError("MOV PINDIRS needs RP2350")
                self._write_pins(c.out_base, c.out_count, value, dirs=True)
            elif dest == 4:
                self.exec_next = value & 0xFFFF
            elif dest == 5:
                self.pc = value & 0x1F
                return False, True
            elif dest == 6:
                self.isr, self.isr_count = value, 0
            else:
                self.osr, self.osr_count = value, 0
            return False, False
        if op == 6:  # IRQ
            clear = (low >> 6) & 1
            wait = (low >> 5) & 1
            flag = self._irq_index(low & 0x1F)
            if self.stalled and wait and not clear:
                return bool((blk.irq >> flag) & 1), False
            if clear:
                blk.irq &= ~(1 << flag)
                return False, False
            blk.irq |= 1 << flag
            return bool(wait), False
        # SET
        dest = (low >> 5) & 7
        data = low & 0x1F
        if dest == 0:
            self._write_pins(c.set_base, c.set_count, data)
        elif dest == 1:
            self.x = data
        elif dest == 2:
            self.y = data
        elif dest == 4:
            self._write_pins(c.set_base, c.set_count, data, dirs=True)
        else:
            raise PioError("reserved SET destination")
        return False, False


class PioBlock:
    """Four state machines sharing 32 instruction words, IRQ flags and pins."""

    def __init__(self, rp2350=False, gpio_base=0):
        self.rp2350 = rp2350
        self.gpio_base = gpio_base
        self.mem = [0] * 32
        self.irq = 0
        self.out = 0              # output levels, window coordinates
        self.oe = 0               # output enables
        self.input_sync_bypass = 0
        self.sync1 = 0
        self.sync2 = 0
        self.cycle = 0
        self.sms = [StateMachine(self, i) for i in range(4)]

    def load(self, instructions, offset=0):
        if offset + len(instructions) > 32:
            raise PioError("program does not fit at offset %d" % offset)
        for i, word in enumerate(instructions):
            self.mem[offset + i] = word & 0xFFFF

    def step(self, raw):
        """One PIO clock with pin levels ``raw`` (window coordinates)."""
        raw &= M32
        visible = (self.sync2 & ~self.input_sync_bypass) | (raw & self.input_sync_bypass)
        for sm in self.sms:
            sm.clock(visible)
        self.sync2 = self.sync1
        self.sync1 = raw
        self.cycle += 1
