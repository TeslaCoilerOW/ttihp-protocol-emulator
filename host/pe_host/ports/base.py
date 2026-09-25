# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Backend contract shared by every port (MicroPython compatible).

A port owns the clock. ``cycle(ui)`` performs one host clock cycle:

1. if a pad environment (software protocol peers, pe_host.peers) is attached:
   observe the uio pad levels left by the previous edge, let the environment
   choose its drive, and apply that drive to the pads;
2. drive ``ui`` onto ui_in;
3. sample uo_out (combinational outputs before the edge, as docs/info.md
   requires: "Sample uo_out after driving ui_in and before the rising edge");
4. apply one rising edge (and return the clock low);
5. return the sampled uo_out byte.

``forbidden`` is the set of uio pins the chip may drive push-pull (every pin
ever given to OWN without its open-drain bit since the last reset). A port
never lets the environment drive those pins: on hardware that would be a
driver fight. The model and cocotb ports raise PadContention instead.
"""

from ..errors import HostError, PadContention


class Port:
    name = "port"
    simulated = False
    # uo_out bits this port can see; bits outside the mask read 0. Only a
    # partially wired PicoPort narrows it (the host then knows that FAULT and
    # IRQ are unobservable, see ProtocolEmulator.fault_visible).
    uo_visible = 0xFF

    def __init__(self, env=None):
        self.env = env
        self.count = 0
        self.forbidden = 0
        self.strict_env = True
        self.env_violations = 0
        self.drive_en = 0
        self.drive_val = 0

    # -- to implement -----------------------------------------------------
    def cycle(self, ui):
        raise NotImplementedError

    def reset(self, cycles):
        raise NotImplementedError

    def deselect(self, cycles):
        raise HostError("port %s cannot drive ena (deselection)" % self.name)

    def free_run(self, cycles, freq_hz):
        """Default: supply the edges by hand."""
        for _ in range(cycles):
            self.cycle(0)

    # -- shared helpers ------------------------------------------------------
    def env_drive(self, pads):
        """Ask the environment for its drive given observed pad levels."""
        en, val = self.env.step(self.count, pads)
        bad = en & self.forbidden
        if bad:
            self.env_violations += 1
            if self.strict_env:
                raise PadContention("environment tried to drive uio pins 0x%02x that the chip "
                                    "owns push-pull (cycle %d)" % (bad, self.count))
            en &= ~bad
        en &= 0xFF
        return en, val & en


def resolve_pads(chip_out, chip_oe, env_en, env_val, pullups):
    """Pad levels from the chip's and the environment's drivers.

    Undriven pads read their pull (``pullups`` bit = 1 -> high, else low). A pad
    driven by both sides must agree (wired-AND of two low drivers is fine; a
    high/low fight raises PadContention).
    """
    both = chip_oe & env_en
    if both & (chip_out ^ env_val):
        raise PadContention("pad fight on uio 0x%02x (chip out 0x%02x oe 0x%02x, env 0x%02x/0x%02x)"
                            % (both & (chip_out ^ env_val), chip_out, chip_oe, env_val, env_en))
    driven = chip_oe | env_en
    return ((pullups & ~driven) | (chip_out & chip_oe) | (env_val & env_en)) & 0xFF
