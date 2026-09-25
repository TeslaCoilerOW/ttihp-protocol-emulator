# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Register/GPIO-level fakes of the demo board SDK and of a Pico's machine
module, with the reference model as the chip behind the pins.

They let the ttboard and pico backends run unmodified under CPython: every
ui_in write, uo_out read, uio read/drive and clock pin write goes through the
same calls the real SDK or MicroPython provide, and the fake chip only
advances on a 0->1 transition of the clock pin.
"""

import sys
import types

import support  # noqa: F401  (paths)
from pe_host.ports.base import resolve_pads
from pe_host.ports.model import config_from_architecture, import_model
from pe_host.protocol import DESIGN_ARCHITECTURE


class FakeChip:
    """Reference model behind physical-looking pins."""

    def __init__(self, pullups=0xFF):
        _, Reference, _ = import_model()
        self.model = Reference(config_from_architecture(DESIGN_ARCHITECTURE))
        self.ui = 0
        self.rst_n = 1
        self.ena = 1
        self.clk = 0
        self.host_out = 0      # host (RP2) output latch on the uio pads
        self.host_oe = 0       # host output enables on the uio pads
        self.pullups = pullups
        self.edges = []        # (ui, pads, reset) per rising edge
        self.enables = []      # ena per rising edge

    def uo(self):
        return self.model.outputs(self.ui, reset=not self.rst_n, enabled=bool(self.ena)).uo

    def pads(self):
        post = self.model.outputs()
        return resolve_pads(post.uio_out, post.uio_oe, self.host_oe, self.host_out & self.host_oe,
                            self.pullups)

    def set_clock(self, level):
        level = 1 if level else 0
        if level and not self.clk:
            pads = self.pads()
            reset = not self.rst_n
            self.edges.append((self.ui, pads, reset))
            self.enables.append(bool(self.ena))
            self.model.tick(self.ui, pads, reset=reset, enabled=bool(self.ena))
        self.clk = level


# ----------------------------------------------------------------- ttboard
class _Value:
    def __init__(self, getter, setter=None):
        self._get, self._set = getter, setter

    @property
    def value(self):
        return self._get()

    @value.setter
    def value(self, v):
        self._set(int(v))


class _SdkPin:
    def __init__(self, getter=None, setter=None):
        self._get, self._set = getter, setter
        self.pull = None
        self.mode = None

    def __call__(self, value=None):
        if value is None:
            return self._get()
        self._set(value)


class FakeDemoBoard:
    def __init__(self, chip, project="tt_um_teslacoilerow_protocol_emulator"):
        self.chip = chip
        self.mode = 0
        self.enabled = None
        self.clock_driven = False
        self.pwm = None
        self.calls = []
        board = self

        class Design:
            def __init__(self, name):
                self.name = name

            def enable(self):
                board.enabled = self.name
                board.calls.append("enable " + self.name)

        class Shuttle:
            def has(self, name):
                return name == project

            def get(self, name):
                return Design(name)

        class Pins:
            def project_clk_driven_by_RP2(self, on):
                board.clock_driven = bool(on)

        self.shuttle = Shuttle()
        self.pins = Pins()
        for i in range(8):
            setattr(self.pins, "uio%d" % i, _SdkPin())
        self.manual_project_clock = types.SimpleNamespace(monitoring=True)
        self.ui_in = _Value(lambda: chip.ui, lambda v: setattr(chip, "ui", v & 0xFF))
        self.uo_out = _Value(chip.uo)
        self.uio_out = _Value(chip.pads)
        self.uio_in = _Value(chip.pads, lambda v: setattr(chip, "host_out", v & 0xFF))
        self.uio_oe_pico = _Value(lambda: chip.host_oe, lambda v: setattr(chip, "host_oe", v & 0xFF))
        self.clk = _SdkPin(lambda: chip.clk, self._write_clock)

    def _write_clock(self, level):
        if not self.clock_driven:
            raise AssertionError("clock written while the RP2 does not drive it")
        self.chip.set_clock(level)

    def clock_project_stop(self):
        self.pwm = None
        self.clock_driven = False

    def clock_project_once(self):
        self.clock_driven = True
        self._write_clock(1)
        self._write_clock(0)

    def clock_project_PWM(self, freq):
        self.pwm = freq

    def reset_project(self, on):
        self.chip.rst_n = 0 if on else 1


def install_ttboard(board):
    """Register fake ttboard.* and machine modules; returns the module dict."""
    chip = board.chip
    ttboard = types.ModuleType("ttboard")
    mode = types.ModuleType("ttboard.mode")
    mode.RPMode = types.SimpleNamespace(SAFE=0, ASIC_RP_CONTROL=1, ASIC_MANUAL_INPUTS=2)
    demoboard = types.ModuleType("ttboard.demoboard")
    demoboard.DemoBoard = types.SimpleNamespace(get=lambda: board)
    util = types.ModuleType("ttboard.util")
    platform = types.ModuleType("ttboard.util.platform")
    platform.write_ui_in_byte = lambda v: setattr(chip, "ui", v & 0xFF)
    platform.read_uo_out_byte = chip.uo
    platform.read_uio_byte = chip.pads
    platform.write_uio_byte = lambda v: setattr(chip, "host_out", v & 0xFF)
    platform.write_uio_outputenable = lambda v: setattr(chip, "host_oe", v & 0xFF)
    platform.write_clock = board._write_clock
    ttboard.mode, ttboard.demoboard, ttboard.util = mode, demoboard, util
    util.platform = platform
    machine = types.ModuleType("machine")
    machine.Pin = types.SimpleNamespace(PULL_UP=1, PULL_DOWN=2, IN=0, OUT=1)
    modules = {"ttboard": ttboard, "ttboard.mode": mode, "ttboard.demoboard": demoboard,
               "ttboard.util": util, "ttboard.util.platform": platform, "machine": machine}
    sys.modules.update(modules)
    return modules


# -------------------------------------------------------------------- pico
PICO_UI = tuple(range(8))
PICO_UO = tuple(range(8, 16))
PICO_CLK, PICO_RST = 16, 17
PICO_UIO = (18, 19, 20, 21, 22, 26, 27, 28)
GPIO_IN, GPIO_OUT = 0xD0000004, 0xD0000010


class FakeRP2:
    """GPIO block of an RP2 wired to FakeChip; wiring = a PicoPort pin map
    (default: pe_host.ports.pico.PIN_MAP). Unwired chip inputs read their
    board default: rst_n released, ena high."""

    def __init__(self, chip, pin_map=None):
        self.chip = chip
        m = pin_map or {"ui_in": PICO_UI, "uo_out": PICO_UO, "clk": PICO_CLK,
                        "rst_n": PICO_RST, "uio": PICO_UIO}
        self.ui_g, self.uo_g, self.clk_g = tuple(m["ui_in"]), tuple(m["uo_out"]), m["clk"]
        self.rst_g = m.get("rst_n", m.get("rst"))
        self.rst_active_high = "rst" in m
        self.ena_g = m.get("ena")
        self.uio_g = tuple(m.get("uio", ()))
        self.out = 0
        self.oe = 0
        self.pulls = {}
        self.reads = self.writes = 0

    def _level(self, g, default):
        return (self.out >> g) & 1 if (self.oe >> g) & 1 else default

    def _sync(self):
        chip = self.chip
        ui = 0
        for i, g in enumerate(self.ui_g):
            if (self.oe >> g) & 1:
                ui |= ((self.out >> g) & 1) << i
        chip.ui = ui
        if self.rst_active_high:
            chip.rst_n = 1 - self._level(self.rst_g, 0)
        else:
            chip.rst_n = self._level(self.rst_g, 1)
        chip.ena = 1 if self.ena_g is None else self._level(self.ena_g, 1)
        host_out = host_oe = 0
        for i, g in enumerate(self.uio_g):
            host_out |= ((self.out >> g) & 1) << i
            host_oe |= ((self.oe >> g) & 1) << i
        chip.host_out, chip.host_oe = host_out, host_oe
        chip.set_clock(self._level(self.clk_g, 0))

    def gpio_in(self):
        self.reads += 1
        value = self.out & self.oe
        uo = self.chip.uo()
        for i, g in enumerate(self.uo_g):
            value |= ((uo >> i) & 1) << g
        pads = self.chip.pads()
        for i, g in enumerate(self.uio_g):
            value = (value & ~(1 << g)) | (((pads >> i) & 1) << g)
        return value

    def write_out(self, value):
        self.writes += 1
        self.out = value & 0xFFFFFFFF
        self._sync()


def install_machine(rp2):
    machine = types.ModuleType("machine")

    class Pin:
        IN, OUT, PULL_UP, PULL_DOWN = 0, 1, 1, 2

        def __init__(self, gpio, mode=None, pull=None, value=None):
            self.gpio = gpio
            self.init(mode, pull, value=value)

        def init(self, mode=None, pull=None, value=None):
            if value is not None:
                self.value(value)
            if mode is not None:
                bit = 1 << self.gpio
                rp2.oe = (rp2.oe | bit) if mode == Pin.OUT else (rp2.oe & ~bit)
                rp2.pulls[self.gpio] = pull
                rp2._sync()

        def value(self, v=None):
            if v is None:
                return (rp2.gpio_in() >> self.gpio) & 1
            bit = 1 << self.gpio
            rp2.write_out((rp2.out | bit) if v else (rp2.out & ~bit))

    class Mem32:
        def __getitem__(self, address):
            if address == GPIO_IN:
                return rp2.gpio_in()
            if address == GPIO_OUT:
                return rp2.out
            raise AssertionError("unexpected mem32 read 0x%08x" % address)

        def __setitem__(self, address, value):
            if address != GPIO_OUT:
                raise AssertionError("unexpected mem32 write 0x%08x" % address)
            rp2.write_out(value)

    machine.Pin = Pin
    machine.mem32 = Mem32()
    sys.modules["machine"] = machine
    return machine


def uninstall():
    for name in ("ttboard", "ttboard.mode", "ttboard.demoboard", "ttboard.util",
                 "ttboard.util.platform", "machine"):
        sys.modules.pop(name, None)
