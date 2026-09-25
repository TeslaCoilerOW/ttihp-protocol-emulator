# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""CPython backend: the RTL (or gate-level netlist) in a cocotb simulation.

ProtocolEmulator is synchronous, cocotb is async. The library therefore runs
in a thread created by cocotb's ``bridge`` and every clock is one ``resume``d
coroutine (cocotb 2.0 names; 1.x called them ``external``/``function``)::

    @cocotb.test()
    async def test(dut):
        cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
        port = CocotbPort(dut)
        result = await run_in_bridge(lambda: selftest.run(ProtocolEmulator(port)))

Per clock, as test/harness.py CocotbHarness: at the falling edge read the
DUT's uio outputs, resolve the pads (software peers, pull-ups), drive ui_in,
uio_in, rst_n and ena, wait for ReadOnly, sample uo_out (the pre-edge value
the host sees) and let the rising edge happen.

lockstep=True also advances the reference model (test/model) with the same
inputs and compares uo_out/uio_out/uio_oe before and after every edge (the
read nibble only while read-valid is high), raising LockstepMismatch.
"""

from ..protocol import DESIGN_ARCHITECTURE, UO_RVALID
from .base import Port, resolve_pads


class LockstepMismatch(AssertionError):
    pass


def bridge_api():
    """(bridge, resume) for cocotb 2.x, falling back to the 1.x names."""
    try:
        from cocotb.task import bridge, resume
        return bridge, resume
    except ImportError:
        pass
    try:
        from cocotb._bridge import bridge, resume
        return bridge, resume
    except ImportError:
        from cocotb import external, function
        return external, function


async def run_in_bridge(func, *args):
    """Await a blocking function (the host library) from a cocotb test."""
    bridge, _ = bridge_api()
    return await bridge(func)(*args)


class CocotbPort(Port):
    name = "cocotb"
    simulated = True

    def __init__(self, dut, env=None, pins=None, pullups=0xFF, lockstep=True,
                 architecture=None, model=None, record=False):
        Port.__init__(self, env)
        from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
        _, resume = bridge_api()
        self.dut = dut
        self._falling = FallingEdge(dut.clk)
        self._readonly = ReadOnly()
        self._rising = RisingEdge(dut.clk)
        self._step_blocking = resume(self._step)
        self.pins = pins
        self.pullups = pullups
        self.model = None
        self.expected = None
        self.mismatches = 0
        self.trace = [] if record else None
        if lockstep:
            if model is None:
                from .model import config_from_architecture, import_model
                _, Reference, _ = import_model()
                model = Reference(config_from_architecture(architecture or DESIGN_ARCHITECTURE))
            self.model = model

    @staticmethod
    def _value(handle):
        value = handle.value
        if hasattr(value, "is_resolvable") and not value.is_resolvable:
            return None
        return int(value)

    def _compare(self, what, got_uo, got_out, got_oe, want, ui, pads):
        mask = 0xFF if want.uo & UO_RVALID else 0xF0
        if (got_uo is None or got_out is None or got_oe is None or (got_uo & mask) != want.uo
                or got_out != want.uio_out or got_oe != want.uio_oe):
            self.mismatches += 1
            raise LockstepMismatch(
                "%s mismatch at cycle %d (ui=%02x pads=%02x): DUT uo=%r uio_out=%r uio_oe=%r, "
                "model uo=%02x uio_out=%02x uio_oe=%02x" % (
                    what, self.count, ui, pads, got_uo, got_out, got_oe, want.uo, want.uio_out,
                    want.uio_oe))

    async def _step(self, ui, reset, enabled):
        dut = self.dut
        await self._falling
        out, oe = self._value(dut.uio_out), self._value(dut.uio_oe)
        if self.model is not None and self.expected is not None:
            self._compare("post-edge", self._value(dut.uo_out), out, oe, self.expected, ui, 0)
        if out is None or oe is None:
            out = oe = 0
        if self.pins is not None:
            from .model import import_model
            _, _, Outputs = import_model()
            post = Outputs(self._value(dut.uo_out) or 0, out, oe)
            pads = (self.pins(self.count, post) if callable(self.pins) else self.pins) & 0xFF
        elif self.env is None:
            pads = resolve_pads(out, oe, 0, 0, self.pullups)
        else:
            observed = resolve_pads(out, oe, self.drive_en, self.drive_val, self.pullups)
            self.drive_en, self.drive_val = self.env_drive(observed)
            pads = resolve_pads(out, oe, self.drive_en, self.drive_val, self.pullups)
        dut.ui_in.value = ui
        dut.uio_in.value = pads
        dut.rst_n.value = 0 if reset else 1
        dut.ena.value = 1 if enabled else 0
        await self._readonly
        uo = self._value(dut.uo_out)
        pre_out, pre_oe = self._value(dut.uio_out), self._value(dut.uio_oe)
        if self.model is not None and not reset and enabled:
            self._compare("pre-edge", uo, pre_out, pre_oe, self.model.outputs(ui), ui, pads)
        await self._rising
        if self.model is not None:
            self.expected = self.model.tick(ui, pads, reset=reset, enabled=enabled)
            if reset or not enabled:
                self.expected = None
        if self.trace is not None:
            self.trace.append((self.count, ui, pads, reset, enabled, uo, pre_oe))
        return 0 if uo is None else uo

    def cycle(self, ui):
        uo = self._step_blocking(ui, False, True)
        self.count += 1
        return uo

    def reset(self, cycles):
        for _ in range(cycles):
            self._step_blocking(0, True, True)
            self.count += 1

    def deselect(self, cycles):
        for _ in range(cycles):
            self._step_blocking(0, False, False)
            self.count += 1
