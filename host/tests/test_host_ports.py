# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The demo-board and Pico backends, unmodified, against register/GPIO-level
fakes (tests/fakes.py) with the reference model behind the pins.

For the self-test and for the flagship scenario with software peers, the
sequence of rising edges each hardware backend produces (ui_in, uio pads,
rst_n) must equal the ModelPort run exactly, in both the fast (mem32 /
ttboard.util.platform) and the slow (documented objects / machine.Pin) path.
"""

import unittest

import fakes
import support
from pe_host import ProtocolEmulator, selftest
from pe_host.flagship import run_flagship
from pe_host.ports.model import ModelPort


def model_edges(action):
    port = ModelPort(record=True)
    pe = ProtocolEmulator(port)
    outcome = action(pe)
    edges = [(ui, pads, reset) for (_c, ui, pads, reset, _e, _p, _a) in port.trace]
    return edges, outcome


def board_edges(chip):
    edges = list(chip.edges)
    first_reset = next(i for i, e in enumerate(edges) if e[2])
    return edges[first_reset:]   # drop the fast-path self-check edge(s)


def selftest_action(pe):
    return selftest.run(pe).passed


def flagship_action(pe):
    result = run_flagship(pe, str(support.SCENARIO))
    return (result.passed, result.engine2_rx_words, result.uart_tx_words)


class DemoBoardPortTest(unittest.TestCase):
    def tearDown(self):
        fakes.uninstall()

    def run_on_board(self, action, fast):
        chip = fakes.FakeChip()
        board = fakes.FakeDemoBoard(chip)
        fakes.install_ttboard(board)
        from pe_host.ports.ttboard import DemoBoardPort
        port = DemoBoardPort(fast=fast)
        self.assertEqual(port.fast, fast)
        self.assertEqual(board.enabled, "tt_um_teslacoilerow_protocol_emulator")
        self.assertEqual(board.mode, 1)
        self.assertFalse(board.manual_project_clock.monitoring)
        outcome = action(ProtocolEmulator(port))
        return board_edges(chip), outcome

    def test_selftest_and_flagship_edges_equal_model(self):
        for action in (selftest_action, flagship_action):
            want, want_outcome = model_edges(action)
            for fast in (True, False):
                got, outcome = self.run_on_board(action, fast)
                self.assertEqual(outcome, want_outcome)
                self.assertEqual(len(got), len(want))
                self.assertEqual(got, want, "%s fast=%s" % (action.__name__, fast))

    def test_unknown_project_is_refused(self):
        chip = fakes.FakeChip()
        board = fakes.FakeDemoBoard(chip, project="tt_um_other")
        fakes.install_ttboard(board)
        from pe_host.errors import HostError
        from pe_host.ports.ttboard import DemoBoardPort
        with self.assertRaises(HostError):
            DemoBoardPort()


class PicoPortTest(unittest.TestCase):
    def tearDown(self):
        fakes.uninstall()

    def run_on_pico(self, action, fast):
        chip = fakes.FakeChip()
        rp2 = fakes.FakeRP2(chip)
        fakes.install_machine(rp2)
        from pe_host.ports.pico import PicoPort
        port = PicoPort(fast=fast, chip="RP2040")
        self.assertEqual(port.fast, fast)
        outcome = action(ProtocolEmulator(port))
        return board_edges(chip), outcome, rp2

    def test_selftest_and_flagship_edges_equal_model(self):
        for action in (selftest_action, flagship_action):
            want, want_outcome = model_edges(action)
            for fast in (True, False):
                got, outcome, rp2 = self.run_on_pico(action, fast)
                self.assertEqual(outcome, want_outcome)
                self.assertEqual(got, want, "%s fast=%s" % (action.__name__, fast))

    def test_fast_path_needs_a_known_chip(self):
        chip = fakes.FakeChip()
        fakes.install_machine(fakes.FakeRP2(chip))
        from pe_host.ports.pico import PicoPort
        self.assertFalse(PicoPort(fast=True, chip="ESP32").fast)

    def pico_with(self, pin_map, fast):
        chip = fakes.FakeChip()
        fakes.install_machine(fakes.FakeRP2(chip, pin_map))
        from pe_host.ports.pico import PicoPort
        port = PicoPort(pin_map=pin_map, fast=fast, chip="RP2040")
        self.assertEqual(port.fast, fast)
        return chip, port

    def test_cmod_a7_host_build_map(self):
        """docs/fpga.md Cmod A7 host-clock wiring: GP18 drives ena, no uio.
        The self-test minus its uio-trigger section and a deselect() give the
        same edges (with ena) as the model backend."""
        from pe_host.errors import HostError
        from pe_host.ports.pico import PIN_MAP_CMOD_A7_HOST
        sections = ("identity", "fifo_loopback", "backpressure", "route", "event",
                    "engine_fault", "host_fault", "program_abandon", "reset")

        def action(pe):
            passed = selftest.run(pe, sections=sections).passed
            pe.select(2)
            pe.deselect(3)
            return passed, pe.selected, pe.status().word

        port = ModelPort(record=True)
        want_outcome = action(ProtocolEmulator(port))
        want = [(ui, pads, reset, enabled) for (_c, ui, pads, reset, enabled, _p, _a) in port.trace]
        self.assertTrue(want_outcome[0])
        for fast in (True, False):
            chip, port = self.pico_with(PIN_MAP_CMOD_A7_HOST, fast)
            self.assertEqual(port.uo_visible, 0xFF)
            outcome = action(ProtocolEmulator(port))
            first = next(i for i, e in enumerate(chip.edges) if e[2])
            got = [e + (en,) for e, en in zip(chip.edges[first:], chip.enables[first:])]
            self.assertEqual(outcome, want_outcome)
            self.assertEqual(got, want, "fast=%s" % fast)
            self.assertIn(False, [e[3] for e in got], "deselect() drove ena low")
            port.env = object()
            with self.assertRaises(HostError):
                port.cycle(0)          # software peers need uio wired

    def test_urbana_host_build_map(self):
        """docs/fpga.md Urbana host-clock wiring: uo_out[5:0] only, reset active
        high. Protocol traffic still works; FAULT/IRQ are reported unknown."""
        from pe_host.errors import HostError
        from pe_host.ports.pico import PIN_MAP_URBANA_HOST

        def action(pe):
            out = [pe.check_isa(), pe.command(P_SELECT, 7), pe.command(P_START, 1),
                   pe.select(2), pe.selected, pe.fault_visible]
            pe.load_program(1, selftest.ECHO)
            pe.start(2)
            pe.tx_write([0x12345678, 0x9ABCDEF0], engine=1)
            pe.idle(40)
            out.append(pe.rx_read_many(2, engine=1))
            report = pe.fault_report()
            out.append((report.fault_pin, report.host_fault, [s.engine for s in report.faulted]))
            return out

        for fast in (True, False):
            chip, port = self.pico_with(PIN_MAP_URBANA_HOST, fast)
            self.assertEqual(port.uo_visible, 0x3F)
            pe = ProtocolEmulator(port, strict=False)
            pe.reset()
            self.assertTrue(chip.edges[0][2], "active-high reset reached the chip")
            outcome = action(pe)
            self.assertEqual(outcome[:6], [2, False, None, True, 2, False])
            self.assertEqual(outcome[6], [0x12345678, 0x9ABCDEF0])
            self.assertEqual(outcome[7], (None, None, []))
            self.assertTrue(chip.model.host_fault, "the rejected commands did set the host fault")
            with self.assertRaises(HostError):
                pe.wait_irq(10)
            with self.assertRaises(HostError):
                pe.deselect()


P_SELECT, P_START = 0, 4


if __name__ == "__main__":
    unittest.main()
