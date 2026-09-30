# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Record the lockstep driver's hardware calls for a differential replay.

``record_modules(board)`` wraps the board's stand-ins for MicroPython's
``rp2``, ``machine`` and ``time`` (board.fake_modules) and logs every call
the driver makes: FIFO puts and gets, FIFO level reads, forced instructions,
state-machine setup (with the pin numbers), the microsecond clock, sleeps,
and the reset/ena callbacks. host/pio/upy_replay.py runs the unchanged
driver under the MicroPython unix port against the log: every call must
match the recorded one (name and arguments) and receives the recorded
result, so any difference between CPython and MicroPython in the driver or
the host library shows up as a divergence at the first differing call.
"""

import json

from .board import FakePin, fake_modules


class Recorder:
    def __init__(self):
        self.log = []
        self.program = None

    def add(self, name, args, result):
        self.log.append([name, args, result])
        return result

    def dump(self, path, meta):
        with open(path, "w") as handle:
            json.dump({"meta": meta, "program": self.program, "log": self.log}, handle)


def _pin(value):
    if value is None:
        return None
    return value.gpio if isinstance(value, FakePin) else int(value)


class _RecSM:
    def __init__(self, rec, sm):
        self.rec = rec
        self.sm = sm

    def put(self, value):
        words = value if isinstance(value, int) else [int(w) for w in value]
        self.sm.put(value)
        self.rec.add("put", words, None)

    def get(self):
        return self.rec.add("get", None, self.sm.get())

    def rx_fifo(self):
        return self.rec.add("rx_fifo", None, self.sm.rx_fifo())

    def tx_fifo(self):
        return self.rec.add("tx_fifo", None, self.sm.tx_fifo())

    def exec(self, instr):
        self.sm.exec(instr)
        self.rec.add("exec", instr, None)

    def active(self, value=None):
        result = self.sm.active(value)
        return self.rec.add("active", value, result)

    def restart(self):
        self.sm.restart()
        self.rec.add("restart", None, None)


class _RecPIO:
    def __init__(self, rec, inner):
        self.rec = rec
        self.inner = inner

    def remove_program(self, program=None):
        self.rec.add("remove_program", self.inner.index, None)

    def gpio_base(self, pin=None):
        return self.rec.add("gpio_base", [self.inner.index, _pin(pin)], self.inner.gpio_base(pin))


class _RecRp2:
    def __init__(self, rec, rp2):
        self.rec = rec
        self.inner = rp2
        self.PIO = self._make_pio()

    def _make_pio(self):
        rec, inner = self.rec, self.inner

        class PIO:
            OUT_LOW = inner.PIO.OUT_LOW
            OUT_HIGH = inner.PIO.OUT_HIGH
            IN_LOW = inner.PIO.IN_LOW
            IN_HIGH = inner.PIO.IN_HIGH
            SHIFT_LEFT = inner.PIO.SHIFT_LEFT
            SHIFT_RIGHT = inner.PIO.SHIFT_RIGHT
            JOIN_NONE = inner.PIO.JOIN_NONE

            def __new__(cls, index):
                return _RecPIO(rec, inner.PIO(index))
        return PIO

    def asm_pio(self, **kw):
        inner = self.inner.asm_pio(**kw)
        rec = self.rec

        def dec(func):
            prog = inner(func)
            rec.program = {"words": list(prog[0]), "tail": [prog[i] for i in range(len(prog) - 5,
                                                                                  len(prog))]}
            return prog
        return dec

    def StateMachine(self, sm_id, prog, **kw):
        sm = self.inner.StateMachine(sm_id, prog, **kw)
        pins = {}
        for key in sorted(kw):
            pins[key] = _pin(kw[key]) if key.endswith(("base", "pin")) else kw[key]
        execctrl = prog[len(prog) - 5]
        self.rec.add("StateMachine", [sm_id, execctrl, pins], None)
        return _RecSM(self.rec, sm)


class _RecMachine:
    def __init__(self, rec, machine):
        self.rec = rec
        self.inner = machine
        outer = self

        class Pin(machine.Pin):
            def __init__(self, gpio, mode=None, value=None):
                machine.Pin.__init__(self, gpio, mode, value)
                outer.rec.add("Pin", [gpio, mode], None)

        self.Pin = Pin

    def freq(self):
        return self.rec.add("freq", None, self.inner.freq())


class _RecTime:
    def __init__(self, rec, time):
        self.rec = rec
        self.inner = time

    def ticks_us(self):
        return self.rec.add("ticks_us", None, self.inner.ticks_us())

    @staticmethod
    def ticks_diff(a, b):
        return a - b

    def sleep_us(self, us):
        self.inner.sleep_us(us)
        self.rec.add("sleep_us", int(us), None)


def record_modules(board, rec):
    rp2, machine, time = fake_modules(board)
    return _RecRp2(rec, rp2), _RecMachine(rec, machine), _RecTime(rec, time)


def recorded_lockstep(board, rec, sm_id=8, **kw):
    """LockstepEmulator on ``board`` whose hardware calls are logged in ``rec``."""
    from pe_host.ports.pio_lockstep import LockstepEmulator, LockstepPort, Rp2Backend
    from .board import HaltRecorder
    halts = HaltRecorder(board)

    def reset(active):
        rec.add("reset", bool(active), None)
        halts.reset(active)

    def ena(level):
        rec.add("ena", int(level), None)
        halts.ena(level)

    backend = Rp2Backend(board.pin_map, reset=reset, ena=ena, sm_id=sm_id,
                         modules=record_modules(board, rec))
    return LockstepEmulator(LockstepPort(backend), **kw)
