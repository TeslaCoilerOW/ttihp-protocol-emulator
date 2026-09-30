# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The lockstep PIO port (pe_host/ports/pio_lockstep.py) on the co-simulated
board (host/pio/board.py): the MicroPython driver, unchanged, on stand-ins for
rp2/machine/time whose state machine is the PIO interpreter, driving the
reference model at the pins.

Every run also checks what a logic analyser on the RP2's pins would show:
the project clock period is 6 system clocks at every rising edge outside the
two reset halts, and ui_in never changes within 3 system clocks of a rising
edge. ``cpu`` is the modelled CPU cost of one FIFO access in system clocks
(12: a C loop; 450: about 3 us, a MicroPython call at 150 MHz).
"""

import types
import unittest

import support
from pe_host import ProtocolEmulator, selftest
from pe_host import protocol as P
from pe_host.errors import HostError, HostTimeout
from pe_host.flagship import FlagshipPeers, run_flagship
from pe_host.image import load_scenario
from pe_host.peers import Environment
from pe_host.ports import pico
from pe_host.ports import pio_lockstep as pl
from pe_host.ports.model import ModelPort
from pio import scenarios as sc
from pio.board import Board, ModelChip, lockstep_on_board

SCENARIO = str(support.SCENARIO)
TIMESTAMP_CHECK = ("identity", "timestamp advances once per host clock")
CPU = (12, 450)


def board_and_pe(env=None, cpu=12, **kw):
    board = Board(ModelChip(env=env), put_cycles=cpu, get_cycles=cpu, **kw)
    board.watch = {23: []}       # edge count whenever a CYC/HALT result is sampled
    pe = lockstep_on_board(board)
    stamps = []
    real = pe.port._stamp

    def stamp(entry):
        real(entry)
        stamps.append(pe.port.count)
    pe.port._stamp = stamp
    return board, pe, stamps


class Clean(unittest.TestCase):
    def assert_clean(self, board, stamps=None, halts=None):
        self.assertEqual(board.check(), [])
        self.assertEqual(set(board.periods), {pl.FRAME_CYCLES})
        self.assertEqual(board.min_setup, 3)
        self.assertEqual(board.min_hold, 3)
        if halts is not None:
            self.assertEqual(len(board.halt_periods), halts)
        if stamps is not None:
            self.assertEqual(stamps, board.watch[23], "port.count differs from the edges")


class SelfTest(Clean):
    def test_selftest(self):
        """Every host command through the library; only the check that compares
        pe.cycles between calls with the chip's timestamp may differ, because
        the free clock runs on between host calls."""
        for cpu in CPU:
            board, pe, stamps = board_and_pe(cpu=cpu)
            result = selftest.run(pe)
            failed = [(c[0], c[1]) for c in result.failures]
            self.assertEqual(result.errors, [], result.summary())
            self.assertTrue(set(failed) <= {TIMESTAMP_CHECK}, result.summary())
            self.assertEqual(len(result.checks), 73)
            self.assert_clean(board, stamps, halts=24)

    def test_timestamp_counts_rising_edges(self):
        """READ_SELECT 1 snapshots are taken at the capture edge right after the
        RDB bubble frame; their difference equals the rising edges between."""
        board, pe, stamps = board_and_pe(cpu=450)
        pe.reset()
        board.watch[pl.ADDR_RD] = []
        t1 = pe.timestamp()
        pe.idle(500)
        t2 = pe.timestamp()
        edges = board.watch[pl.ADDR_RD]
        self.assertEqual(len(edges), 2)
        self.assertEqual(t2 - t1, edges[1] - edges[0])
        self.assertGreaterEqual(t2 - t1, 500)
        self.assert_clean(board, stamps)


class Flagship(Clean):
    def test_flagship_with_pad_level_peers(self):
        for cpu in CPU:
            peers, bind = sc.flagship_peers(SCENARIO)
            board, pe, stamps = board_and_pe(env=peers.env, cpu=cpu)
            bind(board)
            result = run_flagship(pe, SCENARIO, peers=sc.Handles(peers))
            self.assertTrue(result.passed, result.summary())
            self.assert_clean(board, stamps, halts=2)

    def test_flagship_external(self):
        """peers="external" as in the MicroPython replay test: SPI and I2C
        targets on the pads, UART idle, 3000 free-run cycles."""
        peers = FlagshipPeers(load_scenario(SCENARIO))
        env = Environment([peers.spi, peers.i2c], pullups=0xFF)
        board, pe, stamps = board_and_pe(env=env)
        result = run_flagship(pe, SCENARIO, peers="external", free_run_cycles=3000)
        self.assertTrue(result.passed, result.summary())
        self.assertEqual(result.host_fault, False)
        self.assertEqual(result.fault_report.faulted, [])
        self.assert_clean(board, stamps)


class Streaming(Clean):
    def test_uart_stream(self):
        """32 bytes through an 8-word TX queue while engine 0 transmits."""
        for cpu in CPU:
            peers = sc.UartPeers()
            board, pe, stamps = board_and_pe(env=peers.env, cpu=cpu)
            result = sc.uart_stream(pe, peers, sc.stream_words(32))
            self.assertTrue(result.passed, result.summary())
            self.assertEqual(result.host_writes, 24)
            self.assert_clean(board, stamps, halts=2)

    def test_uart_bridge(self):
        """24 bytes from uio1 to uio0 through the host (no ROUTE)."""
        for cpu in CPU:
            words = sc.stream_words(24, seed=7)
            peers = sc.UartPeers(source_words=words)
            board, pe, stamps = board_and_pe(env=peers.env, cpu=cpu)
            result = sc.uart_bridge(pe, peers, 24)
            self.assertTrue(result.passed, result.summary())
            self.assertEqual(result.host_reads, 24)
            self.assert_clean(board, stamps, halts=2)

    def test_bridge_needs_host_service(self):
        """Control: with the host absent after START, uart-rx-idle's RX queue
        fills and the ninth completed frame faults the engine (code 4)."""
        words = sc.stream_words(12, seed=7)
        peers = sc.UartPeers(source_words=words)
        board, pe, _ = board_and_pe(env=peers.env)
        pe.reset()
        pe.load_image(sc.image("uart-rx-idle"), engine=1)
        pe.start(0b10)
        peers.source.start = pe.cycles + 400
        pe.port.wait_frames(400 + 12 * (10 * sc.BIT_CYCLES + sc.GAP_CYCLES))
        status = pe.status(1)
        self.assertTrue(status.faulted and status.fault == 4, repr(status))


class AcceptanceOracle(Clean):
    """command() results and the selection record against the model's own
    decisions (tests/acceptance.py) for the harness-equivalence operation list
    and random host programs (tools/fuzz_host.py)."""

    def check(self, ops):
        from acceptance import AcceptanceOracle
        from ops import run_ops
        board, pe, stamps = board_and_pe()
        pe.strict = False
        view = types.SimpleNamespace(model=board.chip.model, uo_visible=0xFF)
        oracle = AcceptanceOracle(pe, view)
        run_ops(pe, ops, str(support.FIRMWARE), oracle)
        self.assertEqual(oracle.problems, [])
        self.assert_clean(board, stamps)
        return oracle.stats

    def test_operation_list(self):
        import test_host_harness_equivalence as eq
        stats = self.check(eq.OPS)
        self.assertGreater(stats["SELECT/READ_SELECT library False / model False"], 0)

    def test_random_programs(self):
        import test_host_harness_equivalence as eq
        from collections import Counter
        total = Counter()
        for ops in eq.random_programs(range(900000, 900010)):
            total.update(self.check(ops))
        self.assertGreater(total["other library False / model False"], 0)
        self.assertGreater(total["other library True / model True"], 0)


class Port(Clean):
    def test_pin_maps(self):
        self.assertEqual(pl.check_pin_map(pl.PIN_MAP_DB3), 16)
        self.assertEqual(pl.check_pin_map(pico.PIN_MAP), 0)
        self.assertEqual(pl.check_pin_map(pico.PIN_MAP_CMOD_A7_HOST), 0)
        self.assertEqual(pl.check_pin_map(pico.PIN_MAP_URBANA_HOST), 0)
        rp2040_demo_board = {"clk": 0, "ui_in": (9, 10, 11, 12, 17, 18, 19, 20),
                             "uo_out": (5, 6, 7, 8, 13, 14, 15, 16)}
        with self.assertRaises(HostError):
            pl.check_pin_map(rp2040_demo_board)
        with self.assertRaises(HostError):
            pl.check_pin_map({"clk": 0, "ui_in": tuple(range(17, 25)),
                              "uo_out": tuple(range(33, 41))})

    def test_pico_rp2040_map(self):
        """Pico (RP2040, 125 MHz) wired as pe_host.ports.pico.PIN_MAP: GPIO
        window from 0, 20.83 MHz project clock."""
        board = Board(ModelChip(), pin_map=pico.PIN_MAP, f_sys=125000000, rp2350=False)
        board.watch = {23: []}
        pe = lockstep_on_board(board, sm_id=4)
        self.assertEqual(pe.port.frame_hz, 125000000 // 6)
        result = selftest.run(pe, sections=("identity", "fifo_loopback", "route", "host_fault"))
        self.assertTrue(set((c[0], c[1]) for c in result.failures) <= {TIMESTAMP_CHECK},
                        result.summary())
        self.assert_clean(board)

    def test_urbana_six_uo_bits(self):
        board = Board(ModelChip(), pin_map=pico.PIN_MAP_URBANA_HOST, rp2350=False)
        pe = lockstep_on_board(board, sm_id=4)
        self.assertFalse(pe.fault_visible)
        pe.reset()
        self.assertEqual(pe.check_isa(), 2)
        pe.load_program(0, [0x05000000 | 0, 0x06000000])
        self.assertEqual(pe.levels(0), (0, 0))
        self.assertIsNone(pe.command(P.START, 1 << 3))   # uo[7] not wired: unknown
        self.assert_clean(board)

    def test_software_peers_refused(self):
        board, pe, _ = board_and_pe()
        pe.port.env = Environment([], pullups=0xFF)
        with self.assertRaises(HostError):
            pe.reset()

    def test_window_change_cycle_reports_ready_and_valid_low(self):
        board, pe, _ = board_and_pe()
        pe.reset()
        pe.idle(4)
        self.assertEqual(pe.port.window, 0)
        uo = pe.port.cycle(1 << 6 | P.UI_WVALID)     # window 0 -> 1 with valid set
        self.assertEqual(uo & 0x3F, 0)
        uo = pe.port.cycle(0)                        # back to window 0
        self.assertEqual(uo & 0x3F, 0)
        uo = pe.port.cycle(0)
        self.assertEqual(uo & P.UO_WREADY, P.UO_WREADY)

    def test_watchdog_restarts_a_stuck_handshake(self):
        """A TX write to a full queue of a halted engine never completes: the
        WR operations hold the PIO until the watchdog restarts the state
        machine (the clock pauses once) and HostTimeout is raised."""
        board = Board(ModelChip())
        pe = lockstep_on_board(board, watchdog_us=200)
        pe.reset()
        pe.select(0)
        for word in range(8):
            pe.write_word(P.W_TX, word)
        self.assertEqual(pe.levels(0), (8, 0))
        with self.assertRaises(HostTimeout):
            pe.write_word(P.W_TX, 0x99)
        self.assertEqual(pe.port.restarts, 1)
        self.assertFalse(pe.port.count_exact)
        pe.reset()
        self.assertEqual(pe.check_isa(), 2)
        self.assertEqual(pe.levels(0), (0, 0))

    def test_count_resolves_y_wraps_with_the_microsecond_clock(self):
        """Y carries 24 bits of the idle-frame count; a longer gap between two
        results is resolved with the put/get times of the operations."""
        class Backend:
            uo_visible = 0xFF
            frame_hz = 25000000
            now = 0

            def ticks_us(self):
                return self.now

            @staticmethod
            def ticks_diff(a, b):
                return a - b

            def put(self, word):
                pass

        backend = Backend()
        port = pl.LockstepPort(backend)
        entry = ["stamp", 2, 0, (0 - 100) & pl.Y_MASK, 10]
        port._stamp(entry)
        self.assertEqual(port.count, 102)
        # 1.5 s later: 37.5 M frames, of which 2 are operation frames
        frames = 37500000
        d = 100 + frames - 2
        backend.now = 1500000
        entry = ["stamp", 4, 1500000 - 5, (-d) & pl.Y_MASK, 1500000 + 5]
        port._stamp(entry)
        self.assertEqual(port.count, d + 4)
        self.assertTrue(port.count_exact)

    def test_model_port_reference_values(self):
        """With the engines halted, values read back equal those of the
        lockstep ModelPort for the same operations."""
        board, pe, _ = board_and_pe(cpu=450)
        ref = ProtocolEmulator(ModelPort())
        for emu in (pe, ref):
            emu.reset()
            emu.load_program(1, [0x05000000, 0x06000000, 0x07000000])
            emu.tx_write([0xDEADBEEF, 0x12345678], engine=1)
        self.assertEqual(pe.levels(1), ref.levels(1))
        self.assertEqual(pe.status(1), ref.status(1))
        self.assertEqual(pe.isa_version(), ref.isa_version())
        self.assertEqual(pe.fault_report().ok, ref.fault_report().ok)


if __name__ == "__main__":
    unittest.main()
