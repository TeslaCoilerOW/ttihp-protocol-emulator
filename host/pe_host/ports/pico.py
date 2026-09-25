# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""MicroPython backend: a Raspberry Pi Pico (RP2040) or Pico 2 (RP2350) wired
to an FPGA (or any board) running tt_um_teslacoilerow_protocol_emulator.

Default wiring PIN_MAP (docs/host.md has the diagram); every GPIO the Pico
exposes, for an FPGA top that brings uio out to the Pico:

    GP0..GP7    -> ui_in[0..7]      (Pico outputs)
    GP8..GP15   <- uo_out[0..7]     (Pico inputs)
    GP16        -> clk              (Pico output; the Pico supplies every edge)
    GP17        -> rst_n            (Pico output, active low)
    GP18..GP22  <-> uio[0..4]       (inputs; outputs only for software peers)
    GP26..GP28  <-> uio[5..7]
    ena is tied high on the FPGA side (no Pico pin left).

The FPGA host-clock builds of docs/fpga.md (task 4.1) use other wirings;
PIN_MAP_CMOD_A7_HOST and PIN_MAP_URBANA_HOST match them. A pin map is a dict:

    "ui_in"   8 GPIOs driving ui_in[0..7]
    "uo_out"  6 to 8 GPIOs reading uo_out[0..n-1]; unwired bits read 0 and
              port.uo_visible says which bits exist (without uo[7] the host
              cannot see FAULT, without uo[6] it cannot see IRQ)
    "clk"     GPIO driving the design clock
    "rst_n"   GPIO driving an active-low reset, or
    "rst"     GPIO driving an active-high reset (the Urbana build)
    "ena"     optional GPIO driving ena (held high; deselect() pulses it low)
    "uio"     0 to 8 GPIOs wired to uio[0..n-1]; software peers need all 8

The FPGA top must implement wired uio as tri-state pads (uio_oe high drives
uio_out, otherwise the pad is an input into uio_in). All signals are 3.3 V
LVCMOS.

fast=True uses machine.mem32 on the SIO block for pin values (RP2040
datasheet 2.3.1.7 / RP2350 datasheet 3.1.11; the same offsets the ttboard SDK
uses in util/platform/rp2040.py and rp2350.py): GPIO_IN at +0x004 and GPIO_OUT
at +0x010, identical on both chips. Pin directions always change through
machine.Pin. The fast path needs ui_in and uo_out on contiguous GPIO runs and
os.uname().machine naming an RP2040 or RP2350; otherwise the port uses
machine.Pin calls only (fast=False).
"""

from ..errors import HostError
from .base import Port

SIO_BASE = 0xD0000000
GPIO_IN = SIO_BASE + 0x004
GPIO_OUT = SIO_BASE + 0x010
FAST_CHIPS = ("RP2040", "RP2350")

PIN_MAP = {
    "ui_in": (0, 1, 2, 3, 4, 5, 6, 7),
    "uo_out": (8, 9, 10, 11, 12, 13, 14, 15),
    "clk": 16,
    "rst_n": 17,
    "uio": (18, 19, 20, 21, 22, 26, 27, 28),
}

# docs/fpga.md, "Pin host (Pico)": the Cmod A7 host-clock build (DIP 1-8 ui,
# DIP 9-14/17/18 uo, DIP 46 host_clk, DIP 47 rst_n, DIP 48 ena). uio is not
# wired to the Pico, so software peers are unavailable.
PIN_MAP_CMOD_A7_HOST = {
    "ui_in": (0, 1, 2, 3, 4, 5, 6, 7),
    "uo_out": (8, 9, 10, 11, 12, 13, 14, 15),
    "clk": 16,
    "rst_n": 17,
    "ena": 18,
    "uio": (),
}

# docs/fpga.md: the Urbana host-clock build brings out only uo_out[5:0]
# (GP8-12 JAB_0/2/3/4/5, GP13 SERVO1), reset is active high on GP17 (SERVO0)
# and ena comes from sw[0]. Without uo[6..7] the host cannot see IRQ or FAULT.
PIN_MAP_URBANA_HOST = {
    "ui_in": (0, 1, 2, 3, 4, 5, 6, 7),
    "uo_out": (8, 9, 10, 11, 12, 13),
    "clk": 16,
    "rst": 17,
    "uio": (),
}


def chip_name():
    """'RP2040', 'RP2350' or None, from os.uname().machine."""
    try:
        import os
        machine_text = os.uname().machine
    except (ImportError, AttributeError):
        return None
    for name in ("RP2040", "RP2350"):
        if name in machine_text:
            return name
    return None


def _run_start(gpios):
    """First GPIO if the tuple is a contiguous ascending run, else None."""
    for i, gpio in enumerate(gpios):
        if gpio != gpios[0] + i:
            return None
    return gpios[0]


class PicoPort(Port):
    name = "pico"

    def __init__(self, pin_map=None, fast=True, env=None, pullups=0, chip=None):
        Port.__init__(self, env)
        from machine import Pin
        self.Pin = Pin
        self.map = PIN_MAP if pin_map is None else pin_map
        m = self.map
        if len(m["ui_in"]) != 8 or not 6 <= len(m["uo_out"]) <= 8 or len(m.get("uio", ())) > 8:
            raise HostError("pin map needs 8 ui_in, 6..8 uo_out and at most 8 uio GPIOs")
        if ("rst_n" in m) == ("rst" in m):
            raise HostError("pin map needs exactly one of rst_n (active low) or rst (active high)")
        self.uo_visible = (1 << len(m["uo_out"])) - 1
        self.ui_pins = [Pin(g, Pin.OUT, value=0) for g in m["ui_in"]]
        self.uo_pins = [Pin(g, Pin.IN) for g in m["uo_out"]]
        self.clk = Pin(m["clk"], Pin.OUT, value=0)
        # Reset pin level while reset is asserted / released.
        if "rst" in m:
            self._rst_on, self._rst_off = 1, 0
            self.rst_pin = Pin(m["rst"], Pin.OUT, value=0)
        else:
            self._rst_on, self._rst_off = 0, 1
            self.rst_pin = Pin(m["rst_n"], Pin.OUT, value=1)
        self.ena_pin = Pin(m["ena"], Pin.OUT, value=1) if m.get("ena") is not None else None
        self.pullups = pullups
        self.uio_pins = []
        uio = tuple(m.get("uio", ()))
        self._uio = uio
        for i, g in enumerate(uio):
            if (pullups >> i) & 1:
                self.uio_pins.append(Pin(g, Pin.IN, Pin.PULL_UP))
            else:
                self.uio_pins.append(Pin(g, Pin.IN))
        self._uio_mask = 0
        for g in uio:
            self._uio_mask |= 1 << g
        self._en = 0
        self.chip = chip_name() if chip is None else chip
        self.fast = False
        if fast:
            ui0, uo0 = _run_start(tuple(m["ui_in"])), _run_start(tuple(m["uo_out"]))
            if ui0 is not None and uo0 is not None and self.chip in FAST_CHIPS:
                import machine
                self.mem32 = machine.mem32
                self._ui_shift, self._uo_shift = ui0, uo0
                self._ui_mask = 0xFF << ui0
                self._clk_bit = 1 << m["clk"]
                self.fast = True

    # -- uio helpers ----------------------------------------------------------
    def _read_uio(self):
        if len(self._uio) != 8:
            raise HostError("software peers need all eight uio pins wired to the Pico")
        if self.fast:
            raw = self.mem32[GPIO_IN]
            value = 0
            for i, g in enumerate(self._uio):
                value |= ((raw >> g) & 1) << i
            return value
        value = 0
        for i, pin in enumerate(self.uio_pins):
            value |= pin.value() << i
        return value

    def _drive_uio(self, en, val):
        Pin = self.Pin
        changed = en ^ self._en
        if self.fast:
            out_bits = 0
            for i, g in enumerate(self._uio):
                if (val >> i) & 1:
                    out_bits |= 1 << g
            mem = self.mem32
            mem[GPIO_OUT] = (mem[GPIO_OUT] & ~self._uio_mask) | out_bits
        for i, pin in enumerate(self.uio_pins):
            bit = 1 << i
            if en & bit:
                if not self.fast:
                    pin.value((val >> i) & 1)
                if changed & bit:
                    pin.init(Pin.OUT, value=(val >> i) & 1)
            elif changed & bit:
                if (self.pullups >> i) & 1:
                    pin.init(Pin.IN, Pin.PULL_UP)
                else:
                    pin.init(Pin.IN)
        self._en = en

    # -- one host clock ---------------------------------------------------------
    def cycle(self, ui):
        if self.env is not None:
            en, val = self.env_drive(self._read_uio())
            self._drive_uio(en, val)
        if self.fast:
            mem = self.mem32
            mem[GPIO_OUT] = (mem[GPIO_OUT] & ~self._ui_mask) | (ui << self._ui_shift)
            uo = (mem[GPIO_IN] >> self._uo_shift) & self.uo_visible
            mem[GPIO_OUT] = mem[GPIO_OUT] | self._clk_bit
            mem[GPIO_OUT] = mem[GPIO_OUT] & ~self._clk_bit
        else:
            for i, pin in enumerate(self.ui_pins):
                pin.value((ui >> i) & 1)
            uo = 0
            for i, pin in enumerate(self.uo_pins):
                uo |= pin.value() << i
            self.clk.value(1)
            self.clk.value(0)
        self.count += 1
        return uo

    def reset(self, cycles):
        self.rst_pin.value(self._rst_on)
        for _ in range(cycles):
            self.cycle(0)
        self.rst_pin.value(self._rst_off)

    def deselect(self, cycles):
        """Hold ena low for ``cycles`` edges (only with an "ena" GPIO)."""
        if self.ena_pin is None:
            Port.deselect(self, cycles)
        self.ena_pin.value(0)
        for _ in range(cycles):
            self.cycle(0)
        self.ena_pin.value(1)

    def free_run(self, cycles, freq_hz):
        """PWM the clock pin for about cycles/freq_hz seconds with ui_in = 0."""
        import time
        from machine import PWM
        if self.env is not None:
            raise HostError("software peers need host-supplied edges; detach env first")
        for pin in self.ui_pins:
            pin.value(0)
        pwm = PWM(self.clk)
        pwm.freq(int(freq_hz))
        pwm.duty_u16(32768)
        time.sleep_ms(max(1, (cycles * 1000) // int(freq_hz)))
        pwm.deinit()
        self.clk.init(self.Pin.OUT, value=0)
        self.count += cycles

    def release(self):
        """Stop driving uio (all uio GPIOs back to inputs)."""
        self.env = None
        if self._uio:
            self._drive_uio(0, 0)
