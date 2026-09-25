# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Replay backend for differential runs of the library under another interpreter.

A CPython run on ModelPort(record_replay=True) writes six bytes per cycle:
flags (bit0 reset, bit1 deselect), ui, uo, observed pads, environment enable,
environment value. ReplayPort runs the same host code (for example under the
MicroPython unix port) and, on every cycle, checks that the code drives the
recorded ui byte and that the pad environment chooses the recorded drive, then
returns the recorded uo. Any behavioural difference between the interpreters
raises ReplayDivergence at the first differing cycle.

MicroPython compatible.
"""

from ..errors import ReplayDivergence
from .base import Port

RECORD = 6


class ReplayPort(Port):
    name = "replay"
    simulated = True

    def __init__(self, data, env=None):
        Port.__init__(self, env)
        self.data = data
        self.pos = 0
        self.strict_env = False  # the recorded drive already reflects masking

    @property
    def remaining(self):
        return (len(self.data) - self.pos) // RECORD

    def _next(self, want_flags, ui):
        if self.pos + RECORD > len(self.data):
            raise ReplayDivergence("trace exhausted at cycle %d" % self.count)
        d = self.data
        p = self.pos
        flags, rui, uo, observed, en, val = d[p], d[p + 1], d[p + 2], d[p + 3], d[p + 4], d[p + 5]
        self.pos = p + RECORD
        if flags != want_flags:
            raise ReplayDivergence("cycle %d: flags %d, trace has %d" % (self.count, want_flags, flags))
        if self.env is not None:
            got_en, got_val = self.env_drive(observed)
            if got_en != en or got_val != val:
                raise ReplayDivergence("cycle %d: environment drive %02x/%02x, trace has %02x/%02x"
                                       % (self.count, got_en, got_val, en, val))
            self.drive_en, self.drive_val = got_en, got_val
        if rui != ui:
            raise ReplayDivergence("cycle %d: ui 0x%02x, trace has 0x%02x" % (self.count, ui, rui))
        self.count += 1
        return uo

    def cycle(self, ui):
        return self._next(0, ui)

    def reset(self, cycles):
        for _ in range(cycles):
            self._next(1, 0)

    def deselect(self, cycles):
        for _ in range(cycles):
            self._next(2, 0)
