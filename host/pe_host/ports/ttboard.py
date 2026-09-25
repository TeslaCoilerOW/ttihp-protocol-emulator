# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""MicroPython backend for the Tiny Tapeout demo board (RP2040 or RP2350).

Uses the Tiny Tapeout MicroPython SDK ("ttboard", TinyTapeout/tt-micropython-
firmware; reviewed at commit d485c7a, SDK v3):

* ``DemoBoard.get()`` returns the board singleton created by the SDK's boot
  ``main.py`` (README "Initialization").
* ``tt.mode = RPMode.ASIC_RP_CONTROL``: the RP2 drives ui_in and reads uo_out
  (src/ttboard/mode.py; pins.py begin_asiconboard refuses to drive ui_in pins
  that read high, so all ui_in DIP switches must be off).
* ``tt.shuttle.has(name)`` / ``tt.shuttle.get(name).enable()`` selects the
  project through the chip's mux (src/ttboard/project_mux.py).
* ``tt.clock_project_stop()`` stops a PWM clock a config.ini may have started;
  ``tt.pins.project_clk_driven_by_RP2(True)`` then makes the project clock an
  RP2 output again (demoboard.py: clock_project_stop releases it to an input).
* ``tt.manual_project_clock.monitoring = False`` disables the v3.3+ button
  timer that would otherwise insert clock edges (README "Project clocking").
* ``tt.reset_project(True/False)`` drives rst_n.
* Fast I/O (README "Fast I/O"): ``ttboard.util.platform`` provides
  write_ui_in_byte, read_uo_out_byte, read_uio_byte, write_uio_byte,
  write_uio_outputenable and write_clock, implemented with machine.mem32 SIO
  register accesses for each board's GPIO map (rp2040.py, rp2350.py).

The RP2 supplies every clock edge (the host port is synchronous and ui_in is
not synchronized, docs/isa.md). fast=False uses only the documented port
objects (tt.ui_in.value, tt.uo_out.value, tt.clock_project_once()).

Software peers: with ``env`` the RP2 drives its own uio GPIOs (pads shared
with the chip's uio pins) in lockstep, as open-drain (enable low) for I2C and
push-pull for UART RX/MISO. ``pullups`` enables the RP2's internal pull-ups on
those uio pins (the I2C pins need them when no external resistors are fitted).
"""

from ..errors import HostError
from .base import Port

PROJECT = "tt_um_teslacoilerow_protocol_emulator"


class DemoBoardPort(Port):
    name = "ttboard"

    def __init__(self, tt=None, project=PROJECT, enable=True, fast=True, env=None,
                 pullups=0):
        Port.__init__(self, env)
        from ttboard.mode import RPMode
        if tt is None:
            from ttboard.demoboard import DemoBoard
            tt = DemoBoard.get()
        self.tt = tt
        if tt.mode != RPMode.ASIC_RP_CONTROL:
            tt.mode = RPMode.ASIC_RP_CONTROL
        if enable and project:
            if not tt.shuttle.has(project):
                raise HostError("project %s is not on this chip's shuttle" % project)
            tt.shuttle.get(project).enable()
        tt.clock_project_stop()
        manual = getattr(tt, "manual_project_clock", None)
        if manual is not None:
            manual.monitoring = False
        tt.uio_oe_pico.value = 0
        tt.ui_in.value = 0
        self._drive_clock_pin()
        tt.clk(0)
        self.set_pullups(pullups)
        self.fast = False
        if fast:
            self.fast = self._bind_fast()

    def _drive_clock_pin(self):
        """Make the project clock an RP2 output (SDK v3 name; v2 had ...RP2040)."""
        pins = self.tt.pins
        drive = getattr(pins, "project_clk_driven_by_RP2", None)
        if drive is None:
            drive = pins.project_clk_driven_by_RP2040
        drive(True)

    def _bind_fast(self):
        try:
            import ttboard.util.platform as platform
            self._write_ui = platform.write_ui_in_byte
            self._read_uo = platform.read_uo_out_byte
            self._read_uio = platform.read_uio_byte
            self._write_uio = platform.write_uio_byte
            self._write_oe = platform.write_uio_outputenable
            self._clock = platform.write_clock
        except (ImportError, AttributeError):
            return False
        # Check the fast functions against the documented port objects before
        # trusting them (a board with another GPIO map falls back to the slow
        # path). Toggling the clock here adds idle edges with ui_in = 0.
        tt = self.tt
        for probe in (0xA5, 0x5A, 0x00):
            self._write_ui(probe)
            if int(tt.ui_in.value) != probe:
                return False
        self._clock(1)
        high = tt.clk()
        self._clock(0)
        return high == 1 and tt.clk() == 0

    def set_pullups(self, mask):
        """Enable the RP2 pull-up on each uio pin in mask (other pins untouched)."""
        self.pullups = mask
        if not mask:
            return
        from machine import Pin
        for i in range(8):
            if (mask >> i) & 1:
                getattr(self.tt.pins, "uio%d" % i).pull = Pin.PULL_UP

    # -- one host clock -------------------------------------------------------
    def cycle(self, ui):
        if self.fast:
            if self.env is not None:
                en, val = self.env_drive(self._read_uio())
                self._write_uio(val)
                self._write_oe(en)
            self._write_ui(ui)
            uo = self._read_uo()
            self._clock(1)
            self._clock(0)
        else:
            tt = self.tt
            if self.env is not None:
                en, val = self.env_drive(int(tt.uio_out.value))
                tt.uio_in.value = val
                tt.uio_oe_pico.value = en
            tt.ui_in.value = ui
            uo = int(tt.uo_out.value)
            tt.clock_project_once()
        self.count += 1
        return uo

    def reset(self, cycles):
        tt = self.tt
        tt.reset_project(True)
        for _ in range(cycles):
            self.cycle(0)
        tt.reset_project(False)

    def free_run(self, cycles, freq_hz):
        """PWM-clock the project for about cycles/freq_hz seconds, ui_in = 0."""
        import time
        if self.env is not None:
            raise HostError("software peers need host-supplied edges; detach env first")
        tt = self.tt
        if self.fast:
            self._write_ui(0)
        else:
            tt.ui_in.value = 0
        tt.clock_project_PWM(int(freq_hz))
        time.sleep_ms(max(1, (cycles * 1000) // int(freq_hz)))
        tt.clock_project_stop()
        self._drive_clock_pin()
        tt.clk(0)
        self.count += cycles

    def release(self):
        """Stop driving uio and hand the clock back to the SDK."""
        self.env = None
        self.tt.uio_oe_pico.value = 0
        self.tt.clock_project_stop()
