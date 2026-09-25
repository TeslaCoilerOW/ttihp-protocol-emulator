# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Every host command, READ_SELECT, queue access and fault path against the
reference model; results are checked both through the host port and against
the model's internal state."""

import unittest

import support
from pe_host import ProtocolEmulator, selftest
from pe_host import protocol as P
from pe_host.errors import CommandRejected, HostTimeout, IsaMismatch
from pe_host.image import FirmwareImage
from pe_host.ports.model import ModelPort
from pe_host.selftest import ECHO, FAULTER, HALT, WAITEV, imm


def fresh(**kw):
    pe = ProtocolEmulator(ModelPort(record=True), **kw)
    pe.reset()
    return pe


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.pe = fresh()
        self.m = self.pe.port.model

    def assertNoHostFault(self):
        self.assertFalse(self.m.host_fault)

    def test_select(self):
        for engine in (3, 1, 0, 2):
            self.assertTrue(self.pe.select(engine))
            self.assertEqual(self.m.selected, engine)
            self.assertEqual(self.pe.selected, engine)
        with self.assertRaises(CommandRejected):
            self.pe.command(P.SELECT, 4)
        self.assertTrue(self.m.host_fault)
        self.assertEqual(self.m.selected, 2, "a rejected SELECT is atomic")

    def test_begin_own_commit(self):
        pe, m = self.pe, self.m
        pe.select(1)
        pe.begin()
        e = m.engines[1]
        self.assertTrue(e.writing and not e.committed)
        pe.own(0x3C, open_drain=0x0C)
        self.assertEqual((e.ownership, e.open_drain), (0x3C, 0x0C))
        for w in ECHO:
            pe.write_word(P.W_PROGRAM, w)
        self.assertEqual(e.program, ECHO)
        pe.commit(len(ECHO))
        self.assertTrue(e.committed and not e.writing)
        self.assertNoHostFault()
        with self.assertRaises(CommandRejected):
            pe.commit(len(ECHO))   # no longer writing
        pe.clear(host_fault=True)
        self.assertNoHostFault()
        # The library forbids software peers from the chip's push-pull pins.
        self.assertEqual(pe.port.forbidden, 0x30)

    def test_start_stop_and_status(self):
        pe, m = self.pe, self.m
        pe.load_program(2, ECHO, verify=True)
        pe.start(4)
        self.assertTrue(m.engines[2].running)
        status = pe.status(2)
        self.assertTrue(status.running and status.committed)
        self.assertEqual(status.word, m._status() if m.read_select == 0 else None)
        pe.stop(4)
        self.assertFalse(m.engines[2].running)
        with self.assertRaises(ValueError):
            pe.start(16)

    def test_every_read_select(self):
        pe, m = self.pe, self.m
        pe.load_program(0, ECHO)
        pe.tx_write([5, 6, 7])
        pe.start(1)
        pe.idle(6)   # engine consumes two words and stalls pushing? no: RX has space
        for index in range(8):
            got = pe.read_status(index)
            self.assertEqual(m.read_select, index)
            # the word is a snapshot taken when first presented: recompute from
            # the model right away and compare where the value is static
            if index in (0, 2, 4, 5, 6, 7):
                self.assertEqual(got, m._status(), "READ_SELECT %d" % index)
        self.assertEqual(pe.isa_version(), 2)
        t1 = pe.timestamp()
        t2 = pe.timestamp()
        self.assertGreater(t2, t1)
        self.assertEqual(pe.levels(), (len(m.engines[0].tx), len(m.engines[0].rx)))

    def test_route_and_unroute(self):
        pe, m = self.pe, self.m
        pe.route(1, 2, 8)
        self.assertEqual(m.routes[1], (2, 8))
        pe.route(3, 3, 1)
        self.assertEqual(m.routes[3], (3, 1), "self-route is legal")
        pe.unroute(1)
        self.assertIsNone(m.routes[1])
        pe.route(0, 1, 0)
        self.assertIsNone(m.routes[0], "zero count disables")
        with self.assertRaises(CommandRejected):
            pe.command(P.ROUTE, 1 << 21)
        self.assertEqual(m.routes[3], (3, 1))

    def test_selection_tracking_under_fault(self):
        """Raw SELECT/READ_SELECT under a high FAULT pin: acceptance follows the
        payload rule (docs/isa.md), so the library's selection never diverges
        from the chip and fault_report() keeps working (verifier finding)."""
        pe, m = self.pe, self.m
        pe.load_program(3, FAULTER)
        pe.start(8)
        pe.idle(4)
        self.assertTrue(pe.uo & P.UO_FAULT)
        pe.strict = False
        for bad in (4, 7, 0xFFFFFF):
            self.assertIs(pe.command(P.SELECT, bad), False)
            self.assertEqual((pe.selected, m.selected), (3, 3))
        self.assertIs(pe.command(P.READ_SELECT, 8), False)
        self.assertEqual(pe.read_selected, m.read_select)
        self.assertIs(pe.command(P.SELECT, 1), True)
        self.assertEqual((pe.selected, m.selected), (1, 1))
        self.assertIs(pe.command(P.READ_SELECT, 5), True)
        self.assertEqual((pe.read_selected, m.read_select), (5, 5))
        report = pe.fault_report()
        self.assertEqual([s.engine for s in report.faulted], [3])
        self.assertEqual((pe.selected, m.selected), (1, 1))
        # A state-dependent command under FAULT stays "unknown".
        self.assertIsNone(pe.command(P.START, 1))
        pe.strict = True
        with self.assertRaises(CommandRejected):
            pe.command(P.SELECT, 7)
        self.assertEqual((pe.selected, m.selected), (1, 1))

    def test_clear_and_faults(self):
        pe, m = self.pe, self.m
        pe.load_program(3, FAULTER)
        pe.start(8)
        pe.idle(4)
        self.assertEqual(m.engines[3].fault, 0x55)
        report = pe.fault_report()
        self.assertEqual([(s.engine, s.fault) for s in report.faulted], [(3, 0x55)])
        self.assertIsNone(report.host_fault)
        self.assertEqual((pe.selected, m.selected), (3, 3), "fault_report restores the selection")
        pe.clear(8)
        self.assertEqual(m.engines[3].fault, 0)
        # CLEAR of a running engine rejects atomically.
        pe.load_program(1, ECHO)
        pe.start(2)
        with self.assertRaises(CommandRejected):
            pe.clear(2)
        self.assertTrue(m.host_fault)
        report = pe.fault_report()
        self.assertTrue(report.host_fault)
        pe.clear(0, host_fault=True)
        self.assertNoHostFault()
        self.assertTrue(pe.fault_report().ok)

    def test_clear_faults_only_touches_faulted_engines(self):
        pe, m = self.pe, self.m
        pe.load_program(0, ECHO)
        pe.load_program(3, FAULTER)
        pe.start(9)
        pe.idle(4)
        pe.clear_faults()
        self.assertEqual(m.engines[3].fault, 0)
        self.assertTrue(m.engines[0].running)
        self.assertNoHostFault()

    def test_event(self):
        pe, m = self.pe, self.m
        pe.event(0b1010)
        self.assertEqual([e.event for e in m.engines], [False, True, False, True])
        self.assertTrue(pe.event_pending(1))
        pe.idle(1)
        self.assertTrue(pe.irq)
        pe.load_program(1, WAITEV)
        pe.start(2)
        pe.idle(6)
        self.assertFalse(m.engines[1].event, "WAITEVENT consumed the mailbox")
        self.assertEqual(len(m.engines[1].rx), 1)

    def test_flush(self):
        pe, m = self.pe, self.m
        pe.select(2)
        pe.tx_write([1, 2, 3])
        pe.route(2, 0, 4)
        pe.route(1, 2, 4)
        pe.route(3, 0, 4)
        pe.flush()
        self.assertEqual(len(m.engines[2].tx), 0)
        self.assertEqual(m.routes[:3], [None, None, None])
        self.assertEqual(m.routes[3], (0, 4), "routes not touching the engine survive")
        pe.load_program(2, ECHO)
        pe.start(4)
        with self.assertRaises(CommandRejected):
            pe.flush()

    def test_trigger(self):
        pe, m = self.pe, self.m
        pe.trigger(6, P.TRIG_FALLING, engine=3)
        self.assertEqual(m.engines[3].trigger, (6, 1))
        pe.disable_trigger()
        self.assertIsNone(m.engines[3].trigger)
        with self.assertRaises(CommandRejected):
            pe.command(P.TRIGGER, 1 << 6)
        pe.clear(host_fault=True)
        pe.load_program(3, ECHO)
        pe.start(8)
        with self.assertRaises(CommandRejected):
            pe.trigger(0, P.TRIG_HIGH)

    def test_tx_backpressure_timeout(self):
        pe, m = self.pe, self.m
        pe.select(0)
        pe.tx_write(list(range(8)))
        with self.assertRaises(HostTimeout):
            pe.tx_write(99, timeout=20)
        self.assertEqual(len(m.engines[0].tx), 8)
        self.assertEqual(m.write_index, 0, "timeout abandoned the partial word")
        self.assertNotEqual(pe.window, P.W_TX)
        self.assertFalse(pe.tx_try_write(99, max_wait=5))

    def test_rx_timeout_and_try_read(self):
        pe, m = self.pe, self.m
        with self.assertRaises(HostTimeout):
            pe.rx_read(engine=1, timeout=30)
        self.assertIsNone(m.read_word, "the abandoned read left no reservation")
        self.assertIsNone(pe.rx_try_read(max_wait=3))
        pe.load_program(1, ECHO)
        pe.tx_write([0xCAFE0001, 0xCAFE0002])
        pe.start(2)
        pe.idle(10)
        self.assertEqual(pe.rx_drain(), [0xCAFE0001, 0xCAFE0002])

    def test_read_pauses_do_not_change_the_word(self):
        pe, m = self.pe, self.m
        pe.load_program(0, ECHO)
        pe.tx_write(0x89ABCDEF)
        pe.start(1)
        pe.idle(8)
        self.assertEqual(pe.read_word(P.W_RX, pauses=[0, 3, 0, 7, 1, 0, 0, 12]), 0x89ABCDEF)
        self.assertEqual(len(m.engines[0].rx), 0)

    def test_held_rx_after_strict_overflow(self):
        # PUSH a=1 faults 4 when RX is full and keeps the rejected word.
        pe, m = self.pe, self.m
        strict = [imm(6), (18 << 24) | (1 << 16), (7 << 24) | (1 << 16), imm(5, 0)]
        pe.load_program(0, strict)
        pe.tx_write(list(range(0x100, 0x108)))
        pe.start(1)
        pe.idle(40)
        pe.tx_write(0x1FF)
        pe.idle(10)
        status = pe.status(0)
        self.assertEqual(status.fault, 4)
        self.assertEqual(pe.held_rx(), 0x1FF)
        self.assertIn("overflow", status.fault_name)

    def test_isa_check_and_image_load(self):
        pe = self.pe
        self.assertEqual(pe.check_isa(), 2)
        for name in support.image_names():
            image = FirmwareImage.load(str(support.FIRMWARE / (name + ".image.json")))
            pe.reset()
            pe.load_image(image, verify=True)
            e = self.pe.port.model.engines[image.engine]
            self.assertEqual(e.program, image.words, name)
            self.assertEqual((e.ownership, e.open_drain), (image.owned_pins, image.open_drain))
        strict = fresh(isa_versions=(3,))
        with self.assertRaises(IsaMismatch):
            strict.check_isa()

    def test_deselect_resets(self):
        pe, m = self.pe, self.m
        pe.load_program(0, [imm(HALT)])
        pe.deselect(2)
        self.assertFalse(m.engines[0].committed)
        self.assertEqual((pe.window, pe.selected), (0, 0))

    def test_selftest_passes_and_detects_a_model_bug(self):
        pe = ProtocolEmulator(ModelPort())
        result = selftest.run(pe)
        self.assertTrue(result.passed, result.summary())
        self.assertGreaterEqual(len(result.checks), 70)
        # A deliberately broken chip whose partial writes survive a window
        # change (docs/isa.md forbids it) must make the self-test fail.
        broken = ProtocolEmulator(ModelPort())
        model = broken.port.model
        original = model.tick

        def sticky_tick(ui=0, pins=0, *, reset=False, enabled=True):
            index, word = model.write_index, model.write_word
            changed = (ui >> 6 & 3) != model.window
            out = original(ui, pins, reset=reset, enabled=enabled)
            if changed and not reset and enabled:
                model.write_index, model.write_word = index, word
            return out
        model.tick = sticky_tick
        bad = selftest.run(broken, sections=("fifo_loopback",))
        self.assertFalse(bad.passed, bad.summary())

if __name__ == "__main__":
    unittest.main()
