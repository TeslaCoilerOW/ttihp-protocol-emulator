# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The MicroPython ``rp2.asm_pio`` PIO assembler DSL, for CPython.

MicroPython assembles PIO programs written as Python functions::

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def prog():
        label("top")
        out(pins, 8)        .side(0) [2]
        jmp("top")          .side(1)

``asm_pio(**kw)(func)`` here returns the same program list as MicroPython's
``rp2.asm_pio`` (ports/rp2/modules/rp2.py, v1.26.0 and later):
``[array('H', instructions), offset_pio0, offset_pio1, offset_pio2, execctrl,
shiftctrl, out_init, set_init, sideset_init]``, with the wrap target and wrap
in EXECCTRL (WRAP_BOTTOM bits 11:7, WRAP_TOP bits 16:12). MicroPython v1.25
and earlier have no offset_pio2 field; ``execctrl_index(prog)`` works for both.
The encodings follow the RP2040 datasheet, section 3.4 (the RP2350 datasheet,
section 11.4, keeps them), and the DSL rules follow MicroPython's emitter:

* two passes (labels first); ``wrap()`` defaults to the last instruction;
* side-set is optional (EXECCTRL.SIDE_EN and one extra bit) unless every
  instruction has ``.side()``; the delay field shrinks by the side-set bits;
* ``push()``/``pull()`` block by default; ``noblock`` clears the block bit;
* ``nop()`` is ``mov y, y`` (0xA042).

Unlike MicroPython, the function runs with a private globals dict, so a
program function can live in a module that is also imported on the board.
"""

import types
from array import array

# PIO constants as MicroPython's rp2.PIO exposes them (rp2_pio.c): for pin
# init values, bit 1 is the direction and bit 0 the level.
OUT_LOW = 2
OUT_HIGH = 3
IN_LOW = 0
IN_HIGH = 1
SHIFT_LEFT = 0
SHIFT_RIGHT = 1
JOIN_NONE = 0
JOIN_TX = 1
JOIN_RX = 2

(P_INSTR, P_OFFSET_PIO0, P_OFFSET_PIO1, P_OFFSET_PIO2, P_EXECCTRL, P_SHIFTCTRL, P_OUT_INIT,
 P_SET_INIT, P_SIDESET_INIT) = range(9)


def execctrl_index(prog):
    """Index of EXECCTRL in a MicroPython program list of either layout."""
    return len(prog) - 5


class PIOASMError(Exception):
    pass


class PIOASMEmit:
    def __init__(self, *, out_init=None, set_init=None, sideset_init=None, side_pindir=False,
                 in_shiftdir=0, out_shiftdir=0, autopush=False, autopull=False,
                 push_thresh=32, pull_thresh=32, fifo_join=JOIN_NONE):
        self.labels = {}
        execctrl = int(bool(side_pindir)) << 29
        shiftctrl = (fifo_join << 30 | (pull_thresh & 0x1F) << 25 | (push_thresh & 0x1F) << 20
                     | out_shiftdir << 19 | in_shiftdir << 18 | int(bool(autopull)) << 17
                     | int(bool(autopush)) << 16)
        self.prog = [array("H"), -1, -1, -1, execctrl, shiftctrl, out_init, set_init,
                     sideset_init]
        self.wrap_used = False
        if sideset_init is None:
            self.sideset_count = 0
        elif isinstance(sideset_init, int):
            self.sideset_count = 1
        else:
            self.sideset_count = len(sideset_init)
        self.pass_ = 0
        self.num_instr = 0
        self.num_sideset = 0
        self.delay_max = 31
        self.sideset_opt = False

    def start_pass(self, pass_):
        if pass_ == 1:
            if not self.wrap_used and self.num_instr:
                self.wrap()
            self.delay_max = 31
            if self.sideset_count:
                self.sideset_opt = self.num_sideset != self.num_instr
                if self.sideset_opt:
                    self.prog[P_EXECCTRL] |= 1 << 30
                    self.sideset_count += 1
                self.delay_max >>= self.sideset_count
        self.pass_ = pass_
        self.num_instr = 0
        self.num_sideset = 0

    def __getitem__(self, key):
        return self.delay(key)

    def delay(self, delay):
        if self.pass_ > 0:
            if delay > self.delay_max:
                raise PIOASMError("delay too large")
            self.prog[P_INSTR][-1] |= delay << 8
        return self

    def side(self, value):
        self.num_sideset += 1
        if self.pass_ > 0:
            if self.sideset_count == 0:
                raise PIOASMError("no sideset")
            elif value >= (1 << self.sideset_count):
                raise PIOASMError("sideset too large")
            set_bit = 13 - self.sideset_count
            self.prog[P_INSTR][-1] |= int(self.sideset_opt) << 12 | value << set_bit
        return self

    def wrap_target(self):
        self.prog[P_EXECCTRL] |= self.num_instr << 7

    def wrap(self):
        if not self.num_instr:
            raise PIOASMError("wrap before any instruction")
        self.prog[P_EXECCTRL] |= (self.num_instr - 1) << 12
        self.wrap_used = True

    def label(self, label):
        if self.pass_ == 0:
            if label in self.labels:
                raise PIOASMError("duplicate label {}".format(label))
            self.labels[label] = self.num_instr

    def word(self, instr, label=None):
        self.num_instr += 1
        if self.pass_ > 0:
            if label is None:
                label = 0
            else:
                if label not in self.labels:
                    raise PIOASMError("unknown label {}".format(label))
                label = self.labels[label]
            self.prog[P_INSTR].append(instr | label)
        return self

    def nop(self):
        return self.word(0xA042)

    def jmp(self, cond, label=None):
        if label is None:
            label = cond
            cond = 0
        return self.word(0x0000 | cond << 5, label)

    def wait(self, polarity, src, index):
        if src == 6:
            src = 1  # "pin"
        elif src != 0:
            src = 2  # "irq"
        return self.word(0x2000 | polarity << 7 | src << 5 | index)

    def in_(self, src, data):
        if not 0 < data <= 32:
            raise PIOASMError("invalid bit count {}".format(data))
        return self.word(0x4000 | src << 5 | data & 0x1F)

    def out(self, dest, data):
        if dest == 8:
            dest = 7  # exec
        if not 0 < data <= 32:
            raise PIOASMError("invalid bit count {}".format(data))
        return self.word(0x6000 | dest << 5 | data & 0x1F)

    def push(self, value=0, value2=0):
        value |= value2
        if not value & 1:
            value |= 0x20  # block by default
        return self.word(0x8000 | (value & 0x60))

    def pull(self, value=0, value2=0):
        value |= value2
        if not value & 1:
            value |= 0x20  # block by default
        return self.word(0x8080 | (value & 0x60))

    def mov(self, dest, src):
        if dest == 8:
            dest = 4  # exec
        return self.word(0xA000 | dest << 5 | src)

    def irq(self, mod, index=None):
        if index is None:
            index = mod
            mod = 0
        return self.word(0xC000 | (mod & 0x60) | index)

    def set(self, dest, data):
        return self.word(0xE000 | dest << 5 | data)


PIO_FUNCS = {
    "gpio": 0,
    "pins": 0,
    "x": 1,
    "y": 2,
    "null": 3,
    "pindirs": 4,
    "pc": 5,
    "status": 5,
    "isr": 6,
    "osr": 7,
    "exec": 8,
    "invert": lambda x: x | 0x08,
    "reverse": lambda x: x | 0x10,
    "not_x": 1,
    "x_dec": 2,
    "not_y": 3,
    "y_dec": 4,
    "x_not_y": 5,
    "pin": 6,
    "not_osre": 7,
    "noblock": 0x01,
    "block": 0x21,
    "iffull": 0x40,
    "ifempty": 0x40,
    "clear": 0x40,
    "rel": lambda x: x | 0x10,
}


def asm_pio(**kw):
    """Decorator: the program list of ``func`` exactly as MicroPython builds it.
    The label addresses are left in ``asm_pio.last_labels``."""
    emit = PIOASMEmit(**kw)

    def dec(func):
        gl = dict(PIO_FUNCS)
        gl["__builtins__"] = __builtins__
        for name in ("wrap_target", "wrap", "label", "word", "nop", "jmp", "wait", "in_",
                     "out", "push", "pull", "mov", "irq", "set"):
            gl[name] = getattr(emit, name)
        body = types.FunctionType(func.__code__, gl, func.__name__, func.__defaults__,
                                  func.__closure__)
        emit.start_pass(0)
        body()
        emit.start_pass(1)
        body()
        asm_pio.last_labels = dict(emit.labels)
        return emit.prog

    return dec


def assemble(func, **kw):
    """(program list, labels) for ``func``; labels maps names to addresses."""
    prog = asm_pio(**kw)(func)
    return prog, dict(asm_pio.last_labels)


# ---------------------------------------------------------------- disassembly
_JMP_COND = ("", "!x", "x--", "!y", "y--", "x!=y", "pin", "!osre")
_SRC_IN = ("pins", "x", "y", "null", "reserved", "reserved", "isr", "osr")
_DST_OUT = ("pins", "x", "y", "null", "pindirs", "pc", "isr", "exec")
_DST_MOV = ("pins", "x", "y", "pindirs", "exec", "pc", "isr", "osr")
_SRC_MOV = ("pins", "x", "y", "null", "reserved", "status", "isr", "osr")
_DST_SET = ("pins", "x", "y", "reserved", "pindirs", "reserved", "reserved", "reserved")


def disassemble(instr, sideset_count=0, sideset_opt=False):
    """pioasm-style text of one 16-bit instruction."""
    op = instr >> 13
    field = (instr >> 8) & 0x1F
    side_bits = sideset_count + (1 if sideset_opt else 0)
    delay = field & ((1 << (5 - side_bits)) - 1)
    side = None
    if sideset_count:
        if not sideset_opt or field & 0x10:
            side = (field >> (5 - side_bits)) & ((1 << sideset_count) - 1)
    low = instr & 0xFF
    if op == 0:
        cond = (low >> 5) & 7
        text = "jmp %s%d" % ((_JMP_COND[cond] + ", ") if cond else "", low & 0x1F)
    elif op == 1:
        src = ("gpio", "pin", "irq", "jmppin")[(low >> 5) & 3]
        text = "wait %d %s %d" % (low >> 7, src, low & 0x1F)
    elif op == 2:
        text = "in %s, %d" % (_SRC_IN[(low >> 5) & 7], (low & 0x1F) or 32)
    elif op == 3:
        text = "out %s, %d" % (_DST_OUT[(low >> 5) & 7], (low & 0x1F) or 32)
    elif op == 4:
        if low & 0x80:
            text = "pull%s %s" % (" ifempty" if low & 0x40 else "", "block" if low & 0x20 else "noblock")
        else:
            text = "push%s %s" % (" iffull" if low & 0x40 else "", "block" if low & 0x20 else "noblock")
    elif op == 5:
        if instr == 0xA042:
            text = "nop"
        else:
            operation = ("", "~", "::", "?")[(low >> 3) & 3]
            text = "mov %s, %s%s" % (_DST_MOV[(low >> 5) & 7], operation, _SRC_MOV[low & 7])
    elif op == 6:
        mode = "clear" if low & 0x40 else ("wait" if low & 0x20 else "set")
        text = "irq %s %d" % (mode, low & 0x1F)
    else:
        text = "set %s, %d" % (_DST_SET[(low >> 5) & 7], low & 0x1F)
    if side is not None:
        text += " side %d" % side
    if delay:
        text += " [%d]" % delay
    return text
