# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Host library for tt_um_teslacoilerow_protocol_emulator (docs/host.md).

One API (ProtocolEmulator) over interchangeable backends:

  pe_host.ports.model.ModelPort        CPython: reference model, cycle by cycle
  pe_host.ports.cocotb_port.CocotbPort CPython: cocotb RTL/GL simulation
  pe_host.ports.ttboard.DemoBoardPort  MicroPython: Tiny Tapeout demo board (ttboard SDK)
  pe_host.ports.pico.PicoPort          MicroPython: Raspberry Pi Pico GPIO to an FPGA
  pe_host.ports.replay.ReplayPort      any: replays a recorded trace (differential runs)

MicroPython compatible except the model and cocotb ports.
"""

from .errors import (CommandRejected, EngineFault, HostError, HostTimeout, ImageError,
                     IsaMismatch, PadContention, ReplayDivergence)
from .host import FaultReport, ProtocolEmulator
from .image import FirmwareImage, load_scenario
from .protocol import DESIGN_ARCHITECTURE, Status, fault_name

__version__ = "0.1.0"


def connect(backend=None, port_options=None, **options):
    """Create a ProtocolEmulator on the backend for this platform.

    backend: "ttboard", "pico" or "model"; None picks "ttboard" under
    MicroPython when the ttboard SDK is importable, else "pico", and "model"
    under CPython. port_options go to the port constructor, options to
    ProtocolEmulator.
    """
    import sys
    if port_options is None:
        port_options = {}
    if backend is None:
        if sys.implementation.name == "micropython":
            try:
                import ttboard  # noqa: F401
                backend = "ttboard"
            except ImportError:
                backend = "pico"
        else:
            backend = "model"
    if backend == "ttboard":
        from .ports.ttboard import DemoBoardPort as PortClass
    elif backend == "pico":
        from .ports.pico import PicoPort as PortClass
    elif backend == "model":
        from .ports.model import ModelPort as PortClass
    else:
        raise ValueError("unknown backend " + str(backend))
    return ProtocolEmulator(PortClass(**port_options), **options)
