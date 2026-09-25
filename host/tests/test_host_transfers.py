# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Abandoned partial transfers and the window-change bubble (docs/isa.md,
"Host interface"): checked on the pins the library drives and samples, and
against the reference model's state."""

import unittest

import support  # noqa: F401
from pe_host import ProtocolEmulator
from pe_host import protocol as P
from pe_host.ports.model import ModelPort
from pe_host.selftest import ECHO

BUSY = P.UO_WREADY | P.UO_RVALID
HANDSHAKE = P.UI_WVALID | P.UI_RREADY


def state(model):
    """Architectural state a host transfer could change (not timestamps)."""
    engines = tuple((tuple(e.program), e.committed, e.writing, e.running, tuple(e.tx), tuple(e.rx),
                     e.ownership, e.open_drain, e.event, e.trigger, e.fault)
                    for e in model.engines)
    return (engines, tuple(model.routes), model.selected, model.read_select, model.host_fault)


class AbandonTest(unittest.TestCase):
    def setUp(self):
        self.pe = ProtocolEmulator(ModelPort(record=True))
        self.pe.reset()
        self.m = self.pe.port.model

    def test_partial_command_words(self):
        pe = self.pe
        for word in (P.command_word(P.SELECT, 3), P.command_word(P.EVENT, 0xF),
                     P.command_word(P.START, 1), P.command_word(12, 0)):
            for nibbles in range(1, 8):
                before = state(self.m)
                self.assertFalse(pe.try_write_word(P.W_COMMAND, word, max_wait=2, nibbles=nibbles))
                pe.idle(2)
                self.assertEqual(state(self.m), before, "word %08x, %d nibbles" % (word, nibbles))
                self.assertEqual(self.m.write_index, 0)
        pe.select(3)
        self.assertEqual(self.m.selected, 3, "a complete word after abandons still works")

    def test_partial_program_words(self):
        pe, m = self.pe, self.m
        pe.select(2)
        pe.begin()
        for nibbles in range(1, 8):
            pe.write_word(P.W_PROGRAM, ECHO[0])
            self.assertFalse(pe.try_write_word(P.W_PROGRAM, 0xFFFFFFFF, 2, nibbles=nibbles))
            self.assertEqual(m.engines[2].program, [ECHO[0]] * nibbles)
        pe.commit(7)
        self.assertTrue(m.engines[2].committed)

    def test_partial_tx_words(self):
        pe, m = self.pe, self.m
        pe.select(1)
        for nibbles in range(1, 8):
            before = state(m)
            self.assertFalse(pe.try_write_word(P.W_TX, 0xA5A5A5A5, 2, nibbles=nibbles))
            self.assertEqual(state(m), before)
        pe.tx_write(0x01234567)
        self.assertEqual(list(m.engines[1].tx), [0x01234567])

    def test_partial_rx_reads_do_not_pop(self):
        pe, m = self.pe, self.m
        pe.load_program(0, ECHO)
        pe.tx_write([0x11111111, 0x22222222])
        pe.start(1)
        pe.idle(10)
        pe.stop(1)
        for nibbles in range(1, 8):
            before = state(m)
            self.assertIsNone(pe.try_read_word(P.W_RX, 8, nibbles=nibbles))
            self.assertEqual(state(m), before)
        self.assertEqual(pe.rx_read_many(2), [0x11111111, 0x22222222])

    def test_partial_status_read_then_fresh_read(self):
        pe = self.pe
        pe.command(P.READ_SELECT, P.RS_VERSION)
        pe.bounce()
        self.assertIsNone(pe.try_read_word(P.W_COMMAND, 4, nibbles=3))
        self.assertEqual(pe.read_word(P.W_COMMAND), 2)

    def test_abandon_releases_the_rx_reservation(self):
        # isa.md: window 3 reserves the selected RX source from the DMA mover;
        # changing windows abandons the reservation.
        pe, m = self.pe, self.m
        pe.load_program(0, ECHO)
        pe.load_program(1, ECHO)
        pe.route(0, 1, 1)
        pe.select(0)
        pe.set_window(P.W_RX)        # reserve engine 0's RX
        pe.start(3)                  # (START goes through window 0 -> releases)
        pe.set_window(P.W_RX)
        pe.tx_write(0x5EED, engine=0)
        pe.set_window(P.W_RX)
        pe.idle(30)
        self.assertEqual(len(m.engines[0].rx), 1, "reserved: the mover may not take it")
        pe.set_window(P.W_COMMAND)
        pe.idle(30)
        self.assertEqual((len(m.engines[0].rx), len(m.engines[1].rx)), (0, 1))


class BubbleTest(unittest.TestCase):
    def run_mixed(self):
        pe = ProtocolEmulator(ModelPort(record=True))
        pe.reset()
        pe.load_program(0, ECHO)
        pe.tx_write([7, 8, 9], engine=0)
        pe.start(1)
        pe.idle(12)
        pe.rx_read_many(3)
        for index in range(8):
            pe.read_status(index)
        pe.try_write_word(P.W_TX, 0xFFFF, 1, nibbles=3)
        pe.try_read_word(P.W_RX, 3)
        return pe.port.trace

    def test_bubble_on_every_window_change(self):
        trace = self.run_mixed()
        changes = 0
        for previous, step in zip(trace, trace[1:]):
            _, ui, _, reset, _, pre, _ = step
            if reset or previous[3]:
                continue
            if (ui >> 6) != (previous[1] >> 6):
                changes += 1
                self.assertEqual(pre.uo & BUSY, 0, "ready/valid high in a bubble at %d" % step[0])
                self.assertEqual(ui & HANDSHAKE, 0, "library asserted valid/ready in a bubble")
        self.assertGreater(changes, 20)

    def test_read_valid_two_cycles_after_the_change(self):
        # info.md timing sketch: bubble, capture cycle, then read-valid.
        trace = self.run_mixed()
        checked = 0
        for i in range(1, len(trace) - 3):
            ui, prev_ui = trace[i][1], trace[i - 1][1]
            if (ui >> 6) != (prev_ui >> 6) and (ui >> 6) in (0, 3) and not trace[i][3]:
                if trace[i + 1][1] & P.UI_RREADY and (ui >> 6) == 0:
                    self.assertEqual(trace[i + 1][5].uo & P.UO_RVALID, 0, "capture cycle")
                    self.assertTrue(trace[i + 2][5].uo & P.UO_RVALID, "valid one cycle later")
                    checked += 1
        self.assertGreaterEqual(checked, 8)

    def test_write_ready_one_cycle_after_the_change(self):
        trace = self.run_mixed()
        checked = 0
        for i in range(1, len(trace) - 1):
            ui, prev_ui = trace[i][1], trace[i - 1][1]
            if (ui >> 6) != (prev_ui >> 6) and (ui >> 6) == 0 and not trace[i][3]:
                if trace[i + 1][1] & P.UI_WVALID:
                    self.assertTrue(trace[i + 1][5].uo & P.UO_WREADY)
                    checked += 1
        self.assertGreaterEqual(checked, 3)


if __name__ == "__main__":
    unittest.main()
