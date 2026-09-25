# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""CPython backend: the independent reference model (test/model/reference.py),
advanced one clock per ``cycle`` exactly like test/harness.py's ModelHarness.

CPython only (the reference model uses dataclasses). The model directory is
found at <repo>/test, or at $PE_HOST_TEST_DIR when set.

Pads: with a pad environment (pe_host.peers) the port resolves chip drive,
environment drive and pull-ups (default: pull-ups on all eight pads, as the
harness's pad_value(out, 0xFF)) and raises PadContention on a driver fight.
Alternatively ``pins`` may be a harness-style pin supplier: an int, or a
callable (cycle, Outputs) -> pad byte, exactly as test/harness.py uses them.

Recording: record=True keeps (cycle, ui, pads, reset, enabled, pre, post) per
cycle; wire_samples() converts it to the scoreboards' WireSample list and
render_testbench() to a self-checking Verilog testbench (test/model/host.py).
record_replay=True keeps the compact byte trace consumed by ReplayPort.
"""

import os
import sys
from pathlib import Path

from ..protocol import DESIGN_ARCHITECTURE
from .base import Port, resolve_pads

REPLAY_RECORD = 6  # flags, ui, uo, observed pads, env enable, env value


def test_dir():
    override = os.environ.get("PE_HOST_TEST_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "test"


def import_model():
    """Import test/model (Config, Reference, Outputs) from the repository."""
    directory = str(test_dir())
    if directory not in sys.path:
        sys.path.insert(0, directory)
    from model.reference import Config, Outputs, Reference
    return Config, Reference, Outputs


def config_from_architecture(architecture):
    Config, _, _ = import_model()
    return Config(engines=architecture["engine_count"], width=architecture["data_width"],
                  program_words=architecture["program_words"],
                  fifo_words=architecture["fifo_words"],
                  fused=architecture["issue"] == "fused", prefetch=architecture["prefetch"])


class ModelPort(Port):
    name = "model"
    simulated = True

    def __init__(self, architecture=None, model=None, env=None, pins=None, pullups=0xFF,
                 record=False, record_replay=False):
        Port.__init__(self, env)
        if model is None:
            _, Reference, _ = import_model()
            model = Reference(config_from_architecture(architecture or DESIGN_ARCHITECTURE))
        self.model = model
        self.pins = pins
        self.pullups = pullups
        self.trace = [] if record else None
        self.replay = bytearray() if record_replay else None

    def _pads(self, post):
        if self.pins is not None:
            pads = self.pins(self.count, post) if callable(self.pins) else self.pins
            return pads & 0xFF, 0
        if self.env is None:
            return resolve_pads(post.uio_out, post.uio_oe, 0, 0, self.pullups), 0
        observed = resolve_pads(post.uio_out, post.uio_oe, self.drive_en, self.drive_val,
                                self.pullups)
        self.drive_en, self.drive_val = self.env_drive(observed)
        return (resolve_pads(post.uio_out, post.uio_oe, self.drive_en, self.drive_val,
                             self.pullups), observed)

    def _step(self, ui, reset, enabled):
        post = self.model.outputs()
        pads, observed = self._pads(post)
        pre = self.model.outputs(ui, reset=reset, enabled=enabled)
        after = self.model.tick(ui, pads, reset=reset, enabled=enabled)
        if self.trace is not None:
            self.trace.append((self.count, ui, pads, reset, enabled, pre, after))
        if self.replay is not None:
            flags = (1 if reset else 0) | (0 if enabled else 2)
            en = self.drive_en if self.env is not None else 0
            val = self.drive_val if self.env is not None else 0
            self.replay.extend(bytes((flags, ui, pre.uo, observed, en, val)))
        self.count += 1
        return pre.uo

    def cycle(self, ui):
        return self._step(ui, False, True)

    def reset(self, cycles):
        for _ in range(cycles):
            self._step(0, True, True)

    def deselect(self, cycles):
        for _ in range(cycles):
            self._step(0, False, False)

    # -- recordings ----------------------------------------------------------
    def wire_samples(self, start=0):
        """Pad waveform as test/model/scoreboards.WireSample (cycle, pads, uio_oe)."""
        from model.scoreboards import WireSample
        return [WireSample(c, pads, pre.uio_oe) for (c, _ui, pads, _r, _e, pre, _a)
                in self.trace if c >= start]

    def render_testbench(self, top="tt_um_teslacoilerow_protocol_emulator"):
        """Self-checking Verilog testbench replaying this run's exact stimulus
        (test/model/host.py render_testbench: compares every public output)."""
        from model.host import Cycle, render_testbench
        cycles = [Cycle(ui, pads, reset, enabled, after)
                  for (_c, ui, pads, reset, enabled, _pre, after) in self.trace]
        return render_testbench(cycles, top=top)
