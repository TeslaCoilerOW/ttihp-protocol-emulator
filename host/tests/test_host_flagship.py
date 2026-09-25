# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The flagship scenario end to end through the host library.

1. Portable software peers (pe_host.peers) on the reference-model backend; the
   recorded pad waveform is decoded with the scoreboards used in test/
   (test/model/scoreboards.py uart_decode, spi_decode, i2c_decode,
   assert_open_drain) and a test/peers.py UartMonitor.
2. The same run with the test/ peers (SpiTarget, OpenDrainI2CBus+I2CPeer with
   clock stretching, UartSource) and test/scenarios.py's completion predicate:
   the whole ui/uio waveform must equal a test/harness.py ModelHarness run of
   scenarios.flagship cycle for cycle.
3. peers="external" with SPI and I2C targets on the pads and an idle UART
   line: engine 1's expected fault 3, a real host fault, an unexpected engine
   fault, and a port without uo[7].
"""

import json
import unittest

import support
from pe_host import ProtocolEmulator
from pe_host.errors import PadContention
from pe_host.flagship import FlagshipPeers, run_flagship
from pe_host.peers import Environment
from pe_host.ports.model import ModelPort

SCENARIO = json.loads(support.SCENARIO.read_text())
EXPECTED = SCENARIO["expected"]
STIMULUS = SCENARIO["stimulus"]


def run(stretch=0, **kw):
    pe = ProtocolEmulator(ModelPort(record=True))
    result = run_flagship(pe, str(support.SCENARIO), i2c_stretch=stretch, **kw)
    return pe, result


class PortablePeersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pe, cls.result = run()

    def test_passes(self):
        self.assertTrue(self.result.passed, self.result.summary())
        self.assertEqual(self.result.engine2_rx_words, EXPECTED["engine2_rx_words"])
        self.assertEqual(self.pe.port.env_violations, 0)

    def test_scoreboards_decode_the_pad_waveform(self):
        from model.scoreboards import assert_open_drain, i2c_decode, spi_decode, uart_decode
        samples = self.pe.port.wire_samples()
        bit = STIMULUS["uart_bit_cycles"]
        self.assertEqual(uart_decode(samples, pin=0, bit_cycles=bit), EXPECTED["uart_tx_words"])
        self.assertEqual(uart_decode(samples, pin=1, bit_cycles=bit), STIMULUS["uart_rx_words"],
                         "the software UART source on pin 1 is itself well formed")
        self.assertEqual(spi_decode(samples, clock=2, data=3, select=5),
                         EXPECTED["spi_mosi_words"])
        miso = spi_decode(samples, clock=2, data=4, select=5)
        self.assertEqual(miso, [STIMULUS["spi_return_word"]] * len(EXPECTED["spi_mosi_words"]))
        decoded = i2c_decode(samples, scl=6, sda=7)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(list(decoded[0].bytes), EXPECTED["i2c_write_words"])
        self.assertEqual(decoded[0].acknowledged, (True, True))
        self.assertTrue(decoded[0].stopped)
        assert_open_drain(samples, 0xC0)

    def test_streaming_uart_monitor_from_test_peers(self):
        from peers import UartMonitor, bit
        monitor = UartMonitor(bit_cycles=STIMULUS["uart_bit_cycles"])
        for sample in self.pe.port.wire_samples():
            monitor.sample(sample.cycle, bit(sample.values, 0))
        self.assertEqual(monitor.words, EXPECTED["uart_tx_words"])
        self.assertEqual(monitor.errors, [])

    def test_i2c_stretching_is_honoured(self):
        from model.scoreboards import i2c_decode
        pe, result = run(stretch=120)
        self.assertTrue(result.passed, result.summary())
        stretched = [s.cycle for s in pe.port.wire_samples() if not (s.values >> 6) & 1]
        plain = [s.cycle for s in self.pe.port.wire_samples() if not (s.values >> 6) & 1]
        self.assertGreater(stretched[-1] - stretched[0], plain[-1] - plain[0] + 18 * 60,
                           "every low SCL phase lasts at least the stretch")
        decoded = i2c_decode(pe.port.wire_samples(), scl=6, sda=7)
        self.assertEqual(list(decoded[0].bytes), EXPECTED["i2c_write_words"])

    def test_i2c_nack_is_reported_as_fault_65(self):
        pe = ProtocolEmulator(ModelPort())
        peers = FlagshipPeers(SCENARIO)
        peers.i2c.nack_bytes = (0,)
        from pe_host.errors import EngineFault
        with self.assertRaises(EngineFault) as ctx:
            run_flagship(pe, str(support.SCENARIO), peers=peers)
        faults = [(s.engine, s.fault) for s in ctx.exception.report.faulted]
        self.assertEqual(faults, [(3, 65)])

    def test_peer_never_fights_a_push_pull_pin(self):
        pe = ProtocolEmulator(ModelPort())
        peers = FlagshipPeers(SCENARIO)
        peers.spi.miso = 3           # miswired: MOSI is driven by the chip
        peers.spi.mask = 1 << 3
        with self.assertRaises(PadContention):
            run_flagship(pe, str(support.SCENARIO), peers=peers)


class NarrowModelPort(ModelPort):
    """uo_out[5:0] only, like PicoPort on the Urbana host-clock build: uo[6]
    (IRQ) and uo[7] (FAULT) are not wired and read 0."""
    uo_visible = 0x3F

    def cycle(self, ui):
        return ModelPort.cycle(self, ui) & 0x3F


class ExternalPeersTest(unittest.TestCase):
    """peers="external" as on real hardware: an SPI target and an I2C target
    on the pads (pad-level pe_host peers), nothing sending on the UART line,
    so the uart-rx engine reaches its bounded idle wait (fault 3)."""

    def external(self, port_class=ModelPort, i2c=True, inject=None, cycles=3000):
        peers = FlagshipPeers(SCENARIO)
        attached = [peers.spi] + ([peers.i2c] if i2c else [])
        pe = ProtocolEmulator(port_class(env=Environment(attached, pullups=0xFF)),
                              strict=inject is None)
        on_reset = (lambda: inject(pe)) if inject is not None else None
        result = run_flagship(pe, str(support.SCENARIO), peers="external",
                              free_run_cycles=cycles, on_reset=on_reset)
        return pe, peers, result

    @staticmethod
    def faults(report):
        return [(s.engine, s.fault) for s in report.faulted]

    def test_idle_uart_receiver_fault_is_expected(self):
        pe, peers, result = self.external()
        self.assertTrue(result.passed, result.summary())
        self.assertEqual(self.faults(result.fault_report), [(1, 3)])
        self.assertTrue(result.fault_report.fault_pin)
        self.assertIsNone(result.fault_report.host_fault, "masked by the engine 1 fault")
        self.assertTrue(result.fault_report_after_clear.ok, result.summary())
        self.assertIs(result.host_fault, False)
        self.assertEqual(peers.i2c.received, EXPECTED["i2c_write_words"])
        self.assertEqual(peers.i2c.stops, 1)
        # Engine 2 only transmits what the ROUTE brings from engine 1, and the
        # UART line stayed idle.
        self.assertEqual(peers.spi.received, [])
        self.assertEqual(result.engine2_rx_words, [])
        self.assertEqual(result.levels[1], (0, 0))
        self.assertEqual(pe.port.env_violations, 0)

    def test_sticky_host_fault_is_still_detected(self):
        """A rejected command (unknown opcode 12) right after reset."""
        _, _, result = self.external(inject=lambda pe: pe.command(12, 0))
        self.assertFalse(result.passed)
        self.assertIs(result.host_fault, True)
        self.assertIn("host fault: got True, expected False", result.mismatches)

    def test_other_engine_faults_fail(self):
        """No I2C target: the address byte is not acknowledged (fault 65)."""
        _, _, result = self.external(i2c=False)
        self.assertFalse(result.passed)
        self.assertEqual(sorted(self.faults(result.fault_report)), [(1, 3), (3, 65)])
        self.assertTrue(any(m.startswith("unexpected faults") for m in result.mismatches),
                        result.summary())
        self.assertIsNone(result.fault_report_after_clear, "nothing is cleared")

    def test_port_without_fault_pin(self):
        """Engine faults are still read through READ_SELECT; the sticky host
        fault is reported as not checked instead of failing the run."""
        _, _, result = self.external(port_class=NarrowModelPort)
        self.assertTrue(result.passed, result.summary())
        self.assertEqual(self.faults(result.fault_report), [(1, 3)])
        self.assertIsNone(result.fault_report.fault_pin)
        self.assertIsNone(result.host_fault)
        self.assertTrue(any("not checked" in n for n in result.notes), result.summary())
        self.assertEqual(repr(result.fault_report_after_clear),
                         "<FaultReport no engine faults; host fault unknown "
                         "(uo[7] not wired to this host)>")


class HarnessEquivalenceTest(unittest.TestCase):
    """Cycle-for-cycle equality with test/scenarios.py flagship()."""

    def test_same_waveform_as_the_cocotb_harness_scenario(self):
        import harness
        import scenarios
        from model.scoreboards import I2CPeer
        from peers import OpenDrainI2CBus, SpiTarget, UartMonitor, UartSource, bit

        class Recording(harness.ModelHarness):
            def __init__(self):
                super().__init__()
                self.log = []

            async def step(self, *args, **kw):
                pre = await super().step(*args, **kw)
                s = self.history[-1]
                self.log.append((s.ui, s.pins, s.reset, s.enabled, s.pre.uo))
                return pre

        reference = harness.run_model(scenarios.flagship, Recording())

        class TestPeers:
            pass
        peers = TestPeers()
        peers.i2c = I2CPeer(stretch_cycles=3)
        bus = OpenDrainI2CBus(peers.i2c)
        peers.spi = SpiTarget(0, [STIMULUS["spi_return_word"]])
        peers.uart_in = UartSource(STIMULUS["uart_rx_words"], bit_cycles=STIMULUS["uart_bit_cycles"],
                                   gap_cycles=STIMULUS["uart_interframe_idle_cycles"])
        peers.uart_out = UartMonitor(bit_cycles=STIMULUS["uart_bit_cycles"])

        def pins(cycle, out):
            pads = harness.pad_value(out, 0xFF)
            pads = (pads & ~bus.mask) | (bus.resolve(out) & bus.mask)
            miso = peers.spi.update(pads)
            pads = (pads & ~0x12) | peers.uart_in.level(cycle) << 1 | miso << 4
            peers.uart_out.sample(cycle, bit(pads, 0))
            return pads

        port = ModelPort(pins=0, record=True)
        pe = ProtocolEmulator(port, strict=False)
        total = len(EXPECTED["spi_mosi_words"])

        def done():
            return (len(peers.uart_out.words) >= len(EXPECTED["uart_tx_words"])
                    and len(peers.spi.received) >= total and peers.i2c.stops >= 1
                    and len(port.model.engines[2].rx) >= total)

        def on_reset():
            port.pins = pins
        result = run_flagship(pe, str(support.SCENARIO), peers=peers, done=done,
                              on_reset=on_reset)
        self.assertTrue(result.passed, result.summary())
        mine = [(ui, pads, reset, enabled, pre.uo)
                for (_c, ui, pads, reset, enabled, pre, _a) in port.trace]
        self.assertGreater(len(reference.log), 7000)
        self.assertGreaterEqual(len(mine), len(reference.log))
        for i, (a, b) in enumerate(zip(reference.log, mine)):
            self.assertEqual(a, b, "first difference at cycle %d" % i)


if __name__ == "__main__":
    unittest.main()
