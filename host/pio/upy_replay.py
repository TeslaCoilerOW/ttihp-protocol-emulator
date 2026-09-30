# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""MicroPython side of the lockstep port's differential replay.

    micropython upy_replay.py HOST_DIR LOG KIND SCENARIO [RP2_DIR]

Runs pe_host (LockstepEmulator on Rp2Backend) under the MicroPython unix port
with stand-ins for rp2, machine and time that replay LOG, written by a
CPython run of the same scenario on the co-simulated board
(host/pio/replay.py). Every hardware call must match the log. KIND is
"selftest" or "external" (the flagship scenario with real peripherals, 3000
free-run cycles). With RP2_DIR (a directory holding MicroPython's own rp2.py
as rp2_real.py and a stub _rp2.py), the program is assembled by MicroPython's
asm_pio under MicroPython and must equal the recorded words.
Prints REPLAY_OK, the call count and the verdict.
"""

import json
import sys

host_dir, log_path, kind, scenario = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
rp2_dir = sys.argv[5] if len(sys.argv) > 5 else None
sys.path.insert(0, host_dir)

with open(log_path) as handle:
    data = json.load(handle)
LOG = data["log"]
POS = [0]


class ReplayDivergence(Exception):
    pass


def take(name, args):
    if POS[0] >= len(LOG):
        raise ReplayDivergence("log exhausted at %s %r" % (name, args))
    rec = LOG[POS[0]]
    if rec[0] != name or rec[1] != args:
        raise ReplayDivergence("call %d: %s %r, log has %s %r" % (POS[0], name, args, rec[0], rec[1]))
    POS[0] += 1
    return rec[2]


def pin_number(pin):
    if pin is None:
        return None
    return pin.gpio


class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 1

    def __init__(self, gpio, mode=None, value=None):
        self.gpio = gpio
        take("Pin", [gpio, mode])


class Machine:
    Pin = Pin

    @staticmethod
    def freq():
        return take("freq", None)


class Time:
    @staticmethod
    def ticks_us():
        return take("ticks_us", None)

    @staticmethod
    def ticks_diff(a, b):
        return a - b

    @staticmethod
    def sleep_us(us):
        take("sleep_us", int(us))


class StateMachine:
    def __init__(self, sm_id, prog, **kw):
        pins = {}
        for key in kw:
            value = kw[key]
            pins[key] = pin_number(value) if key.endswith("base") or key.endswith("pin") else value
        take("StateMachine", [sm_id, prog[len(prog) - 5], pins])

    def put(self, value):
        if isinstance(value, int):
            take("put", value)
        else:
            take("put", [int(w) for w in value])

    def get(self):
        return take("get", None)

    def rx_fifo(self):
        return take("rx_fifo", None)

    def tx_fifo(self):
        return take("tx_fifo", None)

    def exec(self, instr):
        take("exec", instr)

    def active(self, value=None):
        return take("active", value)

    def restart(self):
        take("restart", None)


class PIO:
    OUT_LOW = 2
    OUT_HIGH = 3
    IN_LOW = 0
    IN_HIGH = 1
    SHIFT_LEFT = 0
    SHIFT_RIGHT = 1
    JOIN_NONE = 0
    JOIN_TX = 1
    JOIN_RX = 2

    def __init__(self, index):
        self.index = index

    def remove_program(self, program=None):
        take("remove_program", self.index)

    def gpio_base(self, pin=None):
        return take("gpio_base", [self.index, pin_number(pin)])


class Rp2:
    PIO = PIO
    StateMachine = StateMachine
    assembled_by = "log"

    @staticmethod
    def asm_pio(**kw):
        def dec(func):
            program = data["program"]
            if rp2_dir is not None:
                sys.path.insert(0, rp2_dir)
                import rp2_real
                prog = rp2_real.asm_pio(**kw)(func)
                if list(prog[0]) != program["words"]:
                    raise ReplayDivergence("MicroPython's asm_pio assembled other words")
                tail = []
                for i in range(len(prog) - 5, len(prog)):
                    tail.append(list(prog[i]) if isinstance(prog[i], tuple) else prog[i])
                if tail != program["tail"]:
                    raise ReplayDivergence("MicroPython's asm_pio: other config fields")
                Rp2.assembled_by = "rp2.py"
                return prog
            return [list(program["words"]), -1, -1, -1] + list(program["tail"])
        return dec


def reset(active):
    take("reset", bool(active))


def ena(level):
    take("ena", int(level))


import pe_host.host  # noqa: E402
from pe_host import selftest  # noqa: E402
from pe_host.flagship import run_flagship  # noqa: E402
from pe_host.ports.pio_lockstep import (LockstepEmulator, LockstepPort, PIN_MAP_DB3,  # noqa: E402
                                        Rp2Backend)

from array import array  # noqa: E402
backend = Rp2Backend(PIN_MAP_DB3, reset=reset, ena=ena, sm_id=8,
                     modules=(Rp2, Machine, Time, array))
pe = LockstepEmulator(LockstepPort(backend))
if kind == "selftest":
    result = selftest.run(pe)
    verdict = "%s %d %s" % (result.passed, len(result.checks),
                            ",".join(c[0] + "/" + c[1] for c in result.failures))
elif kind == "external":
    result = run_flagship(pe, scenario, peers="external", free_run_cycles=3000)
    verdict = "%s host_fault=%s faults=%s" % (
        result.passed, result.host_fault,
        ",".join("%d:%d" % (s.engine, s.fault) for s in result.fault_report.faulted))
else:
    raise ValueError(kind)
if POS[0] != len(LOG):
    raise ReplayDivergence("%d recorded calls were not replayed" % (len(LOG) - POS[0]))
print("REPLAY_OK", kind, POS[0], verdict)
print("ASSEMBLED_BY", Rp2.assembled_by)
print("IMPORTED_FROM", getattr(pe_host.host, "__file__", "?"))
