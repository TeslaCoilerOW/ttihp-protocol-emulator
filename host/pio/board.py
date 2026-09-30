# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""An RP2 running the lockstep PIO program, wired to the chip, in CPython.

``Board`` steps the PIO interpreter (sim.py) one system clock at a time and
connects its GPIO window to a chip:

* ``ModelChip``: the reference model (test/model/reference.py), zero delay:
  uo_out follows ui_in combinationally and changes at rising clock edges, as
  the model's outputs() and tick() define;
* the RTL in cocotb (host/pio/cocotb/), through the same ``Board.core_step``.

The MicroPython driver (pe_host/ports/pio_lockstep.py) runs unchanged on top
of ``fake_modules(board)``: stand-ins for MicroPython's ``rp2``, ``machine``
and ``time`` whose StateMachine is the interpreter. Every FIFO access and
sleep of the driver advances the board by a configurable number of system
clocks (the CPU's latency), so the chip keeps running between host actions.

The board records what a logic analyser on the RP2's pins would show: every
rising edge of the project clock and every ui_in change, in system clocks,
and checks them (``check()``): constant clock period outside the reset
halts, and ui_in changes no closer than ``min_ui_spacing`` clocks to a
rising edge.
"""

import os
import sys

from .asm import (JOIN_NONE, OUT_HIGH, OUT_LOW, SHIFT_LEFT, SHIFT_RIGHT, asm_pio)
from .sim import PioBlock, StateMachineConfig

HOST = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOST not in sys.path:
    sys.path.insert(0, HOST)

from pe_host.ports.base import resolve_pads  # noqa: E402
from pe_host.ports.pio_lockstep import (FRAME_CYCLES, PIN_MAP_DB3, check_pin_map)  # noqa: E402


class ModelChip:
    """test/model Reference behind the pins; ``env`` is a pe_host.peers
    Environment on the uio pads (external peripherals)."""

    def __init__(self, architecture=None, env=None, pullups=0xFF):
        from pe_host.ports.model import config_from_architecture, import_model
        from pe_host.protocol import DESIGN_ARCHITECTURE
        _, Reference, _ = import_model()
        self.model = Reference(config_from_architecture(architecture or DESIGN_ARCHITECTURE))
        self.env = env
        self.pullups = pullups
        self.drive_en = 0
        self.drive_val = 0
        self.pads = pullups

    def uo(self, ui, rst_n, ena):
        return self.model.outputs(ui, reset=not rst_n, enabled=bool(ena)).uo

    def rising_edge(self, ui, rst_n, ena, index):
        post = self.model.outputs()
        if self.env is not None:
            observed = resolve_pads(post.uio_out, post.uio_oe, self.drive_en, self.drive_val,
                                    self.pullups)
            self.drive_en, self.drive_val = self.env.step(index, observed)
        pads = resolve_pads(post.uio_out, post.uio_oe, self.drive_en, self.drive_val,
                            self.pullups)
        self.pads = pads
        self.model.tick(ui, pads, reset=not rst_n, enabled=bool(ena))


class Board:
    """PIO block + GPIO window + chip; one ``core_step`` per system clock."""

    def __init__(self, chip=None, pin_map=None, f_sys=150000000, rp2350=True,
                 put_cycles=12, get_cycles=12, poll_cycles=4, burst_cycles=2,
                 min_ui_spacing=3, trace_limit=0):
        self.pin_map = pin_map or PIN_MAP_DB3
        self.gpio_base = check_pin_map(self.pin_map)
        self.block = PioBlock(rp2350=rp2350, gpio_base=self.gpio_base)
        self.chip = chip
        self.f_sys = f_sys
        self.put_cycles = put_cycles
        self.get_cycles = get_cycles
        self.poll_cycles = poll_cycles
        self.burst_cycles = burst_cycles
        self.min_ui_spacing = min_ui_spacing
        base = self.gpio_base
        self.clk_bit = self.pin_map["clk"] - base
        self.ui_shift = self.pin_map["ui_in"][0] - base
        self.uo_shift = self.pin_map["uo_out"][0] - base
        self.uo_mask = (1 << len(self.pin_map["uo_out"])) - 1
        self.cycle = 0
        self.rst_n = 1
        self.ena = 1
        self.clk = 0
        self.ui = 0
        self.uo = 0
        self.edges = 0
        self.last_edge = None
        self.last_ui_change = None
        self.periods = {}          # period in system clocks -> count
        self.halt_periods = []     # longer periods (clock deliberately halted)
        self.min_setup = None      # system clocks from a ui change to the next rising edge
        self.min_hold = None       # system clocks from a rising edge to the next ui change
        self.violations = []
        self.trace = []            # (cycle, clk, ui, uo before the change) per clk/ui change
        self.trace_limit = trace_limit
        self.sm = None
        self.watch = {}            # instruction address -> rising-edge counts when it issues
        self._refresh()

    # -- pins ------------------------------------------------------------------
    def _raw(self):
        blk = self.block
        driven = blk.out & blk.oe
        return driven | ((self.uo & self.uo_mask) << self.uo_shift)

    def _refresh(self):
        if self.chip is not None:
            self.uo = self.chip.uo(self.ui, self.rst_n, self.ena)

    def core_step(self):
        """One system clock. Returns (clk_rose, ui_changed, clk_changed)."""
        blk = self.block
        blk.step(self._raw())
        driven = blk.out & blk.oe
        clk = (driven >> self.clk_bit) & 1
        ui = (driven >> self.ui_shift) & 0xFF
        rose = clk and not self.clk
        ui_changed = ui != self.ui
        clk_changed = clk != self.clk
        c = self.cycle
        if ui_changed:
            if self.last_edge is not None:
                hold = c - self.last_edge
                self.min_hold = hold if self.min_hold is None else min(self.min_hold, hold)
                if hold < self.min_ui_spacing:
                    self.violations.append("ui_in changed %d clocks after a rising edge "
                                           "(cycle %d)" % (hold, c))
            self.last_ui_change = c
        if rose:
            if ui_changed:
                self.violations.append("ui_in changed with the rising edge (cycle %d)" % c)
            if self.last_ui_change is not None:
                setup = c - self.last_ui_change
                if not ui_changed:
                    self.min_setup = setup if self.min_setup is None else min(self.min_setup, setup)
                    if setup < self.min_ui_spacing:
                        self.violations.append("ui_in changed %d clocks before a rising edge "
                                               "(cycle %d)" % (setup, c))
            if self.last_edge is not None:
                period = c - self.last_edge
                if period == FRAME_CYCLES:
                    self.periods[period] = self.periods.get(period, 0) + 1
                else:
                    self.halt_periods.append((self.last_edge, period))
            self.last_edge = c
        self.clk = clk
        self.ui = ui
        if self.trace_limit and (clk_changed or ui_changed) and len(self.trace) < self.trace_limit:
            self.trace.append((c, clk, ui, self.uo))
        self.cycle = c + 1
        return rose, ui_changed, clk_changed

    def step(self):
        rose, ui_changed, clk_changed = self.core_step()
        if rose:
            if self.chip is not None:
                self.chip.rising_edge(self.ui, self.rst_n, self.ena, self.edges)
            self.edges += 1
        if self.watch and self.sm is not None and self.sm.last_pc in self.watch:
            self.watch[self.sm.last_pc].append(self.edges)
        if self.chip is not None and (rose or ui_changed):
            self._refresh()

    def advance(self, cycles):
        for _ in range(int(cycles)):
            self.step()

    def set_rst_n(self, level):
        self.rst_n = 1 if level else 0
        self._refresh()

    def set_ena(self, level):
        self.ena = 1 if level else 0
        self._refresh()

    @property
    def time_us(self):
        return self.cycle * 1000000 // self.f_sys

    def check(self):
        """Violations of the clock and ui_in schedule seen so far (a list)."""
        problems = list(self.violations)
        for start, period in self.halt_periods:
            if not self.halted_between(start, start + period):
                problems.append("clock period %d system clocks at cycle %d outside a halt"
                                % (period, start))
        return problems

    # halts requested by the driver (reset / deselect), as (start, end) cycles
    def halted_between(self, start, end):
        for a, b in getattr(self, "halts", []):
            if a <= end and b >= start:
                return True
        return False

    def summary(self):
        periods = ", ".join("%d x %d clocks" % (n, p) for p, n in sorted(self.periods.items()))
        return ("%d rising edges in %d system clocks; periods: %s; %d halts; "
                "min setup %s, min hold %s system clocks; %d violations"
                % (self.edges, self.cycle, periods or "none", len(self.halt_periods),
                   self.min_setup, self.min_hold, len(self.violations)))


# ------------------------------------------------------------------ fake modules
class _PioConstants:
    OUT_LOW = OUT_LOW
    OUT_HIGH = OUT_HIGH
    SHIFT_LEFT = SHIFT_LEFT
    SHIFT_RIGHT = SHIFT_RIGHT
    JOIN_NONE = JOIN_NONE
    IN_LOW = 0
    IN_HIGH = 1

    def __init__(self, index):
        self.index = index
        self.base = 0

    def remove_program(self, program=None):
        return None

    def gpio_base(self, pin=None):
        if pin is not None:
            self.base = int(pin)
        return self.base


class FakePin:
    OUT = 1
    IN = 0
    PULL_UP = 1

    def __init__(self, board, gpio, mode=None, value=None):
        self.board = board
        self.gpio = gpio
        self._value = value or 0

    def value(self, v=None):
        if v is None:
            return self._value
        self._value = v
        return None

    def __int__(self):
        return self.gpio


class FakeStateMachine:
    """rp2.StateMachine on the interpreter: the calls MicroPython's driver uses."""

    def __init__(self, board, sm_id, prog, freq=-1, sideset_base=None, out_base=None,
                 set_base=None, in_base=None, jmp_pin=None):
        self.board = board
        self.sm_id = sm_id
        self.init(prog, freq, sideset_base, out_base, set_base, in_base, jmp_pin)

    def _index(self, pin):
        return pin.gpio - self.board.gpio_base

    def init(self, prog, freq=-1, sideset_base=None, out_base=None, set_base=None, in_base=None,
             jmp_pin=None):
        """As MicroPython's StateMachine.init (rp2_pio.c): load the program with
        pio_add_program (a 32-word program lands at offset 0), reset the state
        machine at the program start, apply the config and the pin init values
        (bit 1 direction, bit 0 level)."""
        board = self.board
        blk = board.block
        base = len(prog) - 5
        instructions = list(prog[0])
        offset = 32 - len(instructions)   # pico-sdk: highest free offset
        if freq not in (-1, None) and freq != board.f_sys:
            raise ValueError("the board model runs the state machine at the system clock")
        blk.load(instructions, offset)
        out_init, set_init, sideset_init = prog[base + 2], prog[base + 3], prog[base + 4]

        def count(init):
            if init is None:
                return 0
            return 1 if isinstance(init, int) else len(init)

        cfg = StateMachineConfig.from_program(
            prog, offset=offset, rp2350=blk.rp2350,
            out_base=self._index(out_base) if out_base else 0, out_count=count(out_init),
            set_base=self._index(set_base) if set_base else 0, set_count=count(set_init),
            sideset_base=self._index(sideset_base) if sideset_base else 0,
            in_base=self._index(in_base) if in_base else 0,
            jmp_pin=self._index(jmp_pin) if jmp_pin else 0)
        sm = blk.sms[self.sm_id % 4]
        sm.enabled = False
        sm.reset_state()
        sm.configure(cfg)
        sm.pc = offset
        for pin_base, init in ((cfg.out_base, out_init), (cfg.set_base, set_init),
                               (cfg.sideset_base, sideset_init)):
            if init is None:
                continue
            values = (init,) if isinstance(init, int) else tuple(init)
            for i, value in enumerate(values):
                bit = 1 << ((pin_base + i) & 31)
                blk.oe = (blk.oe & ~bit) | (bit if value >> 1 & 1 else 0)
                blk.out = (blk.out & ~bit) | (bit if value & 1 else 0)
        self.sm = sm
        board.sm = sm

    # -- CPU access (each costs system clocks) --------------------------------------
    def put(self, value):
        """An int, or an array of words written by one C loop (burst_cycles apart)."""
        board = self.board
        words = [value] if isinstance(value, int) else list(value)
        board.advance(board.put_cycles)
        for i, word in enumerate(words):
            if i:
                board.advance(board.burst_cycles)
            while len(self.sm.tx) >= self.sm.tx_depth:
                board.step()
            self.sm.put(word)

    def get(self):
        board = self.board
        board.advance(board.get_cycles)
        while not self.sm.rx:
            board.step()
        return self.sm.get()

    def rx_fifo(self):
        self.board.advance(self.board.poll_cycles)
        return len(self.sm.rx)

    def tx_fifo(self):
        self.board.advance(self.board.poll_cycles)
        return len(self.sm.tx)

    def exec(self, instr):
        self.sm.exec(instr)
        self.board.step()

    def active(self, value=None):
        if value is None:
            return int(self.sm.enabled)
        self.sm.enabled = bool(value)
        return None

    def restart(self):
        self.sm.restart()


class FakeRp2:
    def __init__(self, board):
        self.board = board

        class PIO(_PioConstants):
            pass

        self.PIO = PIO

    asm_pio = staticmethod(asm_pio)

    def StateMachine(self, sm_id, prog, **kw):
        return FakeStateMachine(self.board, sm_id, prog, **kw)


class FakeMachine:
    def __init__(self, board):
        self.board = board
        outer = board

        class Pin(FakePin):
            def __init__(self, gpio, mode=None, value=None):
                FakePin.__init__(self, outer, gpio, mode, value)

        self.Pin = Pin

    def freq(self):
        return self.board.f_sys


class FakeTime:
    def __init__(self, board):
        self.board = board

    def ticks_us(self):
        return self.board.time_us

    @staticmethod
    def ticks_diff(a, b):
        return a - b

    def sleep_us(self, us):
        self.board.advance(int(us) * self.board.f_sys // 1000000)

    def sleep_ms(self, ms):
        self.sleep_us(1000 * ms)


def fake_modules(board):
    return FakeRp2(board), FakeMachine(board), FakeTime(board)


class HaltRecorder:
    """reset/ena callables for Rp2Backend that drive the board and record the
    halts, so Board.check() accepts the long clock periods around them."""

    def __init__(self, board):
        self.board = board
        board.halts = []

    def reset(self, active):
        self.board.halts.append((self.board.cycle - FRAME_CYCLES, self.board.cycle + 64))
        self.board.set_rst_n(0 if active else 1)

    def ena(self, level):
        self.board.halts.append((self.board.cycle - FRAME_CYCLES, self.board.cycle + 64))
        self.board.set_ena(level)


def lockstep_on_board(board, emulator=True, sm_id=8, watchdog_us=None, **kw):
    """LockstepEmulator (or LockstepPort) running the MicroPython driver on
    ``board`` through the fake modules."""
    from pe_host.ports.pio_lockstep import LockstepEmulator, LockstepPort, Rp2Backend
    halts = HaltRecorder(board)
    backend = Rp2Backend(board.pin_map, reset=halts.reset, ena=halts.ena, sm_id=sm_id,
                         modules=fake_modules(board))
    port = LockstepPort(backend, watchdog_us=watchdog_us)
    return LockstepEmulator(port, **kw) if emulator else port
