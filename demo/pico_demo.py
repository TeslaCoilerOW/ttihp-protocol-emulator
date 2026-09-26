# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Raspberry Pi Pico side of the hardware demonstration (docs/demo.md).

MicroPython, for the Cmod A7 host-clock build (pe_cmod_a7_host.bit) wired as
docs/fpga.md "Pin host (Pico)" describes. The Pico supplies every clock edge
through the host library (host/pe_host, PicoPort with PIN_MAP_CMOD_A7_HOST).

Copy to the Pico's filesystem (for example with mpremote):
    host/pe_host/          -> /pe_host/
    demo/pe_demo.py        -> /pe_demo.py
    demo/pico_demo.py      -> /pico_demo.py
    firmware/{uart-tx,uart-rx,spi-controller-mode0}.{image,source}.json -> /firmware/
    demo/firmware/timing-probe.{image,source}.json                    -> /firmware/

Then, at the REPL, with the logic analyser armed:
    import pico_demo
    pico_demo.isolation("idle", 200000)
    pico_demo.isolation("loaded", 200000, seed=1)

MARKER_GPIO (default GP19) is driven high while the measured phase runs; wire
it to an analyser channel and pass it to ``pe_capture.py analyze --gate``.
"""

import json

import pe_demo
from pe_host import ProtocolEmulator
from pe_host.ports.pico import PIN_MAP_CMOD_A7_HOST, PicoPort

FIRMWARE_DIRS = ("/firmware", "/", "firmware")
MARKER_GPIO = 19


def connect(pin_map=None):
    port = PicoPort(pin_map=pin_map or PIN_MAP_CMOD_A7_HOST)
    return pe_demo.LibChip(ProtocolEmulator(port, timeout=200000))


def _marker():
    from machine import Pin
    pin = Pin(MARKER_GPIO, Pin.OUT, value=0)
    return pin.value


def isolation(mode, cycles, seed=1, out=None, pin_map=None):
    """One measurement phase; prints and returns the result dict. For the
    Urbana host build pass pin_map=pe_host.ports.pico.PIN_MAP_URBANA_HOST."""
    import time
    chip = connect(pin_map)
    images = pe_demo.lib_images(FIRMWARE_DIRS, pe_demo.ISOLATION_IMAGES)
    t0 = time.ticks_ms()
    result = pe_demo.isolation(chip, images, mode, cycles, seed=seed, log=print, marker=_marker())
    result["wall_ms"] = time.ticks_diff(time.ticks_ms(), t0)
    text = json.dumps(result)
    print(text)
    if out:
        with open(out, "w") as handle:
            handle.write(text)
    return result
