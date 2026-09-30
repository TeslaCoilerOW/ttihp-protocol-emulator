# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The lockstep PIO program driving the RTL (or the FPGA host build) in cocotb.

The PIO interpreter (host/pio/sim.py) runs one system clock at a time; its
side-set pin is the design's clk and its OUT pins are ui_in, and uo_out feeds
its IN pins through the modelled two-flop synchroniser. Simulation time
advances by 1/f_sys per system clock (6.667 ns at 150 MHz), so the waveform
is the one the RP2350 would produce. The MicroPython driver
(pe_host/ports/pio_lockstep.py) and the host library run unchanged in a
cocotb bridge thread on stand-ins for rp2/machine/time; every FIFO access
advances the simulation by the modelled CPU cost.

Pad-level peers (pe_host.peers) act as external hardware on uio: stepped once
per rising edge, their drive is applied at the falling edge. The reference
model (test/model) runs alongside: at every rising edge the DUT's uo_out,
uio_out and uio_oe must equal the model's (the read nibble only while
read-valid is high), as in CocotbPort.

Tests: flagship (pad-level peers), uart_bridge, uart_stream, selftest. Each
checks that the clock never stopped outside the reset halts and writes a
short trace (clock edges, ui_in, uo_out) to $PE_PIO_TRACE_DIR when set.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(os.path.dirname(HERE))
# fpga/sim puts fpga/host (which has its own pe_host.py) on PYTHONPATH first.
sys.path.insert(0, HOST)
for name in list(sys.modules):
    if name == "pe_host" or name.startswith("pe_host."):
        del sys.modules[name]

import cocotb  # noqa: E402
from cocotb.triggers import ReadOnly, Timer  # noqa: E402

from pe_host import selftest  # noqa: E402
from pe_host.flagship import run_flagship  # noqa: E402
from pe_host.ports.base import resolve_pads  # noqa: E402
from pe_host.ports.cocotb_port import bridge_api, run_in_bridge  # noqa: E402
from pe_host.ports import pio_lockstep as pl  # noqa: E402
from pe_host.ports.model import config_from_architecture, import_model  # noqa: E402
from pe_host.protocol import DESIGN_ARCHITECTURE, UO_RVALID  # noqa: E402
from pio import scenarios as sc  # noqa: E402
from pio.board import Board, lockstep_on_board  # noqa: E402

SCENARIO = os.environ.get("PE_HOST_SCENARIO") or os.path.join(HOST, "..", "firmware",
                                                              "flagship-scenario.json")
F_SYS = int(os.environ.get("PE_PIO_F_SYS", "150000000"))
CPU = int(os.environ.get("PE_PIO_CPU_CYCLES", "450"))


class RtlMismatch(AssertionError):
    pass


class RtlBoard(Board):
    """Board whose chip is the DUT; the reference model checks it per edge."""

    def __init__(self, dut, env=None, pullups=0xFF, trace_limit=10000000, **kw):
        Board.__init__(self, None, f_sys=F_SYS, put_cycles=CPU, get_cycles=CPU,
                       trace_limit=trace_limit, **kw)
        self.dut = dut
        self.env = env
        self.pullups = pullups
        self.drive_en = 0
        self.drive_val = 0
        self.step_ps = int(round(1e12 / F_SYS))
        self.pending_ps = 0
        _, Reference, _ = import_model()
        self.model = Reference(config_from_architecture(DESIGN_ARCHITECTURE))
        self.mismatches = 0
        self.marks = []           # (system clock, address) of watched instructions
        self.synced = False
        self.compared = 0
        self.pads = pullups
        self.rst_applied = None
        self.ena_applied = None
        _, resume = bridge_api()
        self._advance_blocking = resume(self._advance)

    # -- the bridge thread calls these (FakeStateMachine, FakeTime, reset) -----------
    def advance(self, cycles):
        if cycles > 0:
            self._advance_blocking(int(cycles))

    def step(self):
        self._advance_blocking(1)

    def set_rst_n(self, level):
        self.rst_n = 1 if level else 0

    def set_ena(self, level):
        self.ena = 1 if level else 0

    # -- simulation ----------------------------------------------------------------
    @staticmethod
    def _value(handle):
        value = handle.value
        if hasattr(value, "is_resolvable") and not value.is_resolvable:
            return None
        return int(value)

    def _compare(self, ui):
        dut = self.dut
        want = self.model.outputs(ui, reset=not self.rst_n, enabled=bool(self.ena))
        got_uo, got_out, got_oe = (self._value(dut.uo_out), self._value(dut.uio_out),
                                   self._value(dut.uio_oe))
        self.compared += 1
        mask = 0xFF if want.uo & UO_RVALID else 0xF0
        if (got_uo is None or got_out is None or got_oe is None or (got_uo & mask) != want.uo
                or got_out != want.uio_out or got_oe != want.uio_oe):
            self.mismatches += 1
            raise RtlMismatch("edge %d (ui %02x): DUT uo=%r uio_out=%r uio_oe=%r, model "
                              "uo=%02x uio_out=%02x uio_oe=%02x"
                              % (self.edges, ui, got_uo, got_out, got_oe, want.uo,
                                 want.uio_out, want.uio_oe))

    async def _advance(self, cycles):
        dut = self.dut
        # reset/ena change only while the program is halted with the clock low:
        # apply them now (1 ps on, out of the ReadOnly phase), before any
        # further system clock
        if self.rst_n != self.rst_applied or self.ena != self.ena_applied:
            await Timer(1, unit="ps")
            self.pending_ps -= 1
            dut.rst_n.value = self.rst_n
            dut.ena.value = self.ena
            self.rst_applied = self.rst_n
            self.ena_applied = self.ena
        for _ in range(cycles):
            rose, ui_changed, clk_changed = self.core_step()
            self.pending_ps += self.step_ps
            if self.watch and self.sm is not None and self.sm.last_pc in self.watch:
                self.watch[self.sm.last_pc].append(self.edges + (1 if rose else 0))
                self.marks.append((self.cycle - 1, self.sm.last_pc))
            if not (rose or ui_changed or clk_changed):
                continue
            await Timer(self.pending_ps, unit="ps")
            self.pending_ps = 0
            if rose:
                # pre-edge outputs (settled since the last event) against the
                # model, from the first reset edge on (the DUT is X before it)
                if self.synced and self.rst_n and self.ena:
                    self._compare(self.ui)
                if not self.rst_n or not self.ena:
                    self.synced = True
                self.model.tick(self.ui, self.pads, reset=not self.rst_n,
                                enabled=bool(self.ena))
                self.edges += 1
                dut.clk.value = 1
            else:
                if clk_changed:
                    dut.clk.value = self.clk
                # ui_in (and the pad drive) change with the falling edge; they
                # are written 1 ps after it, so that testbench code triggered
                # by the edge (fpga/sim's pad checker) sees settled values
                await Timer(1, unit="ps")
                self.pending_ps -= 1
                if clk_changed and not self.clk:
                    # the pad environment chooses its drive for the next rising
                    # edge from the pads left by the previous one
                    out, oe = self._value(dut.uio_out) or 0, self._value(dut.uio_oe) or 0
                    if self.env is not None:
                        observed = resolve_pads(out, oe, self.drive_en, self.drive_val,
                                                self.pullups)
                        self.drive_en, self.drive_val = self.env.step(self.edges, observed)
                    self.pads = resolve_pads(out, oe, self.drive_en, self.drive_val,
                                             self.pullups)
                    dut.uio_in.value = self.pads
                dut.ui_in.value = self.ui
            await ReadOnly()
            uo = self._value(dut.uo_out)
            self.uo = 0 if uo is None else uo


async def setup(dut, env=None):
    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0xFF
    dut.clk.value = 0
    await Timer(10, unit="ns")
    board = RtlBoard(dut, env=env)
    board.watch = {23: [], pl.ADDR_RDB: []}
    return board


def lockstep(board):
    pe = lockstep_on_board(board)
    stamps = []
    real = pe.port._stamp

    def stamp(entry):
        real(entry)
        stamps.append(pe.port.count)
    pe.port._stamp = stamp
    return pe, stamps


def report(dut, board, stamps, name):
    problems = board.check()
    periods = sorted(board.periods.items())
    dut._log.info("%s: %s", name, board.summary())
    dut._log.info("%s: halts (start cycle, period) %r", name, board.halt_periods)
    dut._log.info("%s: %d rising edges compared with the reference model, %d mismatches",
                  name, board.compared, board.mismatches)
    trace_dir = os.environ.get("PE_PIO_TRACE_DIR")
    if trace_dir:
        import gzip
        os.makedirs(trace_dir, exist_ok=True)
        head = ("# %s\n# system clock %d Hz; one line per change of clk or ui_in: system "
                "clock, clk and ui_in after the change, uo_out just before it\n"
                % (board.summary(), F_SYS))
        with gzip.open(os.path.join(trace_dir, name + ".trace.txt.gz"), "wt") as handle:
            handle.write(head)
            for cycle, clk, ui, uo in board.trace:
                handle.write("%9d %d %02x %02x\n" % (cycle, clk, ui, uo))
        # excerpt: a window-3 read (RDB) in the middle of the run
        rdbs = [c for c, pc in board.marks if pc == pl.ADDR_RDB]
        if rdbs:
            centre = rdbs[len(rdbs) // 2]
            with open(os.path.join(trace_dir, name + ".excerpt.txt"), "w") as handle:
                handle.write(head)
                handle.write("# RDB (window-change bubble, then RD) dispatched at system clock %d\n"
                             % centre)
                for cycle, clk, ui, uo in board.trace:
                    if centre - 60 <= cycle <= centre + 150:
                        handle.write("%9d %d %02x %02x\n" % (cycle, clk, ui, uo))
    assert problems == [], problems
    assert set(dict(periods)) == {6}, periods
    assert board.mismatches == 0
    if stamps is not None:
        assert stamps == board.watch[23][:len(stamps)], "port.count differs from the edges"


@cocotb.test()
async def test_flagship_lockstep(dut):
    """firmware/flagship-scenario.json on a free-running PIO clock."""
    peers, bind = sc.flagship_peers(SCENARIO)
    board = await setup(dut, env=peers.env)
    bind(board)

    def body():
        pe, stamps = lockstep(board)
        return run_flagship(pe, SCENARIO, peers=sc.Handles(peers)), stamps
    result, stamps = await run_in_bridge(body)
    dut._log.info("%s", result.summary())
    report(dut, board, stamps, "flagship")
    assert result.passed, result.summary()


@cocotb.test()
async def test_uart_bridge_lockstep(dut):
    """24 bytes uio1 -> engine 1 -> host -> engine 0 -> uio0 while both run."""
    words = sc.stream_words(24, seed=7)
    peers = sc.UartPeers(source_words=words)
    board = await setup(dut, env=peers.env)

    def body():
        pe, stamps = lockstep(board)
        return sc.uart_bridge(pe, peers, len(words)), stamps
    result, stamps = await run_in_bridge(body)
    dut._log.info("uart_bridge: %s", result.summary())
    report(dut, board, stamps, "uart_bridge")
    assert result.passed, result.summary()


@cocotb.test()
async def test_uart_stream_lockstep(dut):
    """32 bytes streamed through engine 0's 8-word TX queue while it transmits."""
    peers = sc.UartPeers()
    board = await setup(dut, env=peers.env)

    def body():
        pe, stamps = lockstep(board)
        return sc.uart_stream(pe, peers, sc.stream_words(32)), stamps
    result, stamps = await run_in_bridge(body)
    dut._log.info("uart_stream: %s", result.summary())
    report(dut, board, stamps, "uart_stream")
    assert result.passed, result.summary()


@cocotb.test()
async def test_selftest_lockstep(dut):
    """The bring-up self-test (every host command) on the free-running clock."""
    board = await setup(dut)

    def body():
        pe, stamps = lockstep(board)
        return selftest.run(pe), stamps
    result, stamps = await run_in_bridge(body)
    dut._log.info("%s", result.summary())
    report(dut, board, stamps, "selftest")
    failed = {(c[0], c[1]) for c in result.failures}
    assert not result.errors, result.summary()
    assert failed <= {("identity", "timestamp advances once per host clock")}, result.summary()
