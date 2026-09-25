#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Run the Pico host driver (pico_host.py) against the Python reference model.

The port below is the reference model of docs/isa.md (test/model/reference.py)
with the board wiring of the loopback test: weak pull-ups on all protocol
pins, jumpers uio0-uio1 and uio3-uio4. It checks the driver's cycle
discipline and handshakes without hardware.

    python3 fpga/host/test_pico_host.py            # uses ../../test/model
    PE_TEST_DIR=/path/to/test python3 fpga/host/test_pico_host.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEST_DIR = Path(os.environ.get("PE_TEST_DIR", REPO / "test"))
FIRMWARE = Path(os.environ.get("PE_FIRMWARE_DIR", TEST_DIR.parent / "firmware"))
sys.path[:0] = [str(HERE), str(TEST_DIR)]

import pico_host  # noqa: E402
from model.reference import Config, Reference  # noqa: E402


class ModelPort:
    """pico_host port backed by the reference model plus the loopback wiring."""

    def __init__(self, jumpers: bool) -> None:
        arch = json.loads((REPO / "configs" / "instruction-sram-32.json").read_text())["architecture"]
        self.model = Reference(Config(engines=arch["engine_count"], width=arch["data_width"],
                                      program_words=arch["program_words"], fifo_words=arch["fifo_words"],
                                      fused=arch["issue"] == "fused", prefetch=arch["prefetch"]))
        self.jumpers = jumpers
        self.cycles = 0

    def pads(self) -> int:
        out = self.model.outputs()
        pads = (out.uio_out & out.uio_oe) | (0xFF & ~out.uio_oe)   # weak pull-ups
        if self.jumpers:
            for a, b in ((0, 1), (3, 4)):                            # wired pairs
                level = (pads >> a & 1) & (pads >> b & 1)             # a driven 0 wins over a pull-up
                if out.uio_oe >> a & 1:
                    level = out.uio_out >> a & 1
                elif out.uio_oe >> b & 1:
                    level = out.uio_out >> b & 1
                pads = pads & ~(1 << a | 1 << b) | level << a | level << b
        return pads

    def step(self, ui: int, reset: bool = False, enabled: bool = True) -> int:
        pre = self.model.outputs(ui, reset=reset, enabled=enabled).uo
        self.model.tick(ui, self.pads(), reset=reset, enabled=enabled)
        self.cycles += 1
        return pre


def images() -> dict:
    return {n: json.loads((FIRMWARE / f"{n}.image.json").read_text())
            for n in ("uart-tx", "uart-rx", "spi-controller-mode0")}


def main() -> int:
    failures = 0
    host = pico_host.NibbleHost(ModelPort(jumpers=True))
    host.reset()
    version = host.status(pico_host.RS_VERSION)
    print(f"ISA version {version}")
    failures += version != 2
    for engine in range(4):
        st = host.engine_status(engine)
        failures += any(st.values())
    ok = pico_host.loopback(host, images())
    failures += not ok
    print(f"loopback with jumpers: {'PASS' if ok else 'FAIL'} ({host.cycles} host cycles)")
    host = pico_host.NibbleHost(ModelPort(jumpers=False))
    bad = pico_host.loopback(host, images(), log=lambda _: None)
    failures += bad
    print(f"loopback without jumpers detected: {'PASS' if not bad else 'FAIL'}")
    print("PICO HOST MODEL TEST", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
