# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for pe_timing (stdlib unittest; run: python3 -m unittest tools/timing/test_pe_timing.py).

Hand-written programs pin down the cycle rules of docs/isa.md; the firmware
tests analyze every committed image; the model test replays the directed
scenarios of test/scenarios.py against the reference model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("PE_TIMING_REPO", HERE.parent.parent)).resolve()
sys.path.insert(0, str(HERE))

import pe_contracts as C  # noqa: E402
import pe_timing as T  # noqa: E402


def ins(op: int, a: int = 0, b: int = 0, c: int = 0) -> int:
    return (op << 24) | (a << 16) | (b << 8) | c


def imm(op: int, value: int = 0) -> int:
    return (op << 24) | (value & 0xFFFFFF)


def analyze(words: list[int], own: int = 0xFF, od: int = 0, arch: T.Arch | None = None) -> T.Analysis:
    return T.Analysis(T.Program("t", arch or T.Arch(), 0, own, od, tuple(words)))


def pads(v: T.Variant, pin: int) -> list[tuple[int, str, str]]:
    return [(e[T.ET], T.PAD_TEXT[e[T.EBEFORE]], T.PAD_TEXT[e[T.EAFTER]]) for e in v.events
            if e[T.EK] == "pad" and e[T.EPIN] == pin]


class CycleRules(unittest.TestCase):
    def test_costs(self) -> None:
        self.assertEqual(T.minimum_cycles(T.decode(imm(T.WAIT, 0))), 1)
        self.assertEqual(T.minimum_cycles(T.decode(imm(T.WAIT, 62))), 63)
        self.assertEqual(T.minimum_cycles(T.decode(ins(T.XFER, 8, 32, 28))), 513)
        self.assertEqual(T.minimum_cycles(T.decode(ins(T.SET))), 1)

    def test_straight_line_schedule(self) -> None:
        an = analyze([imm(T.DIR, 1), imm(T.SET, 1), imm(T.WAIT, 3), imm(T.SET, 0), ins(T.HALT)], own=1)
        v = an.start_node().variants[0]
        self.assertEqual(v.issues(), [(1, 0), (2, 1), (3, 2), (7, 3), (8, 4)])
        self.assertEqual(pads(v, 0), [(1, "Z", "0"), (2, "0", "1"), (7, "1", "0"), (8, "0", "Z")])
        self.assertEqual(v.end, ("halt", 8))

    def test_count_loop_is_unrolled_exactly(self) -> None:
        # COUNT 2 -> 3 iterations of (SET 1, SET 0, LOOP)
        an = analyze([imm(T.DIR, 1), imm(T.COUNT, 2), imm(T.SET, 1), imm(T.SET, 0), imm(T.LOOP, 2),
                      ins(T.HALT)], own=1)
        rises = [t for t, a, b in pads(an.start_node().variants[0], 0) if b == "1"]
        self.assertEqual(rises, [3, 6, 9])

    def test_xfer_edges(self) -> None:
        # PINS clk=0 tx=1; XFER 2 bits, half 3, CPOL0 CPHA0 MSB drive
        an = analyze([imm(T.DIR, 3), imm(T.PINS, 0 | 1 << 3 | 2 << 6), ins(T.XFER, 2, 3, 4 | 8), ins(T.HALT)], own=3)
        v = an.start_node().variants[0]
        clk = [t for t, a, b in pads(v, 0)]
        self.assertEqual(clk, [1, 6, 9, 12, 15, 16])    # DIR, 4 transitions spaced 3, HALT release
        self.assertEqual(v.issues()[-1], (16, 3))      # XFER issued at 3 costs 2*2*3+1

    def test_pull_is_a_boundary(self) -> None:
        an = analyze([imm(T.SET, 1), imm(T.DIR, 1), ins(T.PULL), ins(T.OUT, 0, 0, 0), imm(T.JMP, 2)], own=1)
        self.assertEqual(an.start_node().variants[0].end, ("boundary", 3, 2))
        pull = [n for n in an.iter_nodes() if n.bpc == 2]
        self.assertTrue(pull and all(n.max_stall == T.INF and n.min_stall == 0 for n in pull))

    def test_waitpin_on_own_released_pin_needs_loopback_latency(self) -> None:
        # open-drain pin 6: drive low for 3 cycles, release on +4, wait for high from +5
        an = analyze([imm(T.DIR, 0x40), imm(T.NOP), imm(T.NOP), imm(T.DIR, 0), ins(T.WAITPIN, 6, 1),
                      ins(T.HALT)], own=0x40, od=0x40)
        node = [n for n in an.iter_nodes() if n.bpc == 4][0]
        self.assertEqual(node.min_stall, 2)            # released on +4, visible to the engine on +7
        self.assertEqual(node.max_stall, T.RESET_LIMIT - 1)

    def test_short_drive_is_invisible(self) -> None:
        # a one-cycle low pulse before the wait: the attempt reads the pad from 3 edges back
        an = analyze([imm(T.NOP), imm(T.NOP), imm(T.DIR, 0x40), imm(T.DIR, 0), ins(T.WAITPIN, 6, 1)],
                     own=0x40, od=0x40)
        node = [n for n in an.iter_nodes() if n.bpc == 4][0]
        self.assertEqual(node.min_stall, 0)

    def test_self_deadlock(self) -> None:
        an = analyze([imm(T.DIR, 1), imm(T.NOP), imm(T.NOP), imm(T.NOP), ins(T.WAITPIN, 0, 1), ins(T.HALT)],
                     own=1)
        node = [n for n in an.iter_nodes() if n.bpc == 4][0]
        self.assertIsNotNone(node.deadlock)
        # driven only one edge before the wait: the first attempts still read the older pad
        an = analyze([imm(T.DIR, 1), ins(T.WAITPIN, 0, 1), ins(T.HALT)], own=1)
        self.assertIsNone([n for n in an.iter_nodes() if n.bpc == 1][0].deadlock)

    def test_open_drain_set_high_releases(self) -> None:
        an = analyze([imm(T.DIR, 0x80), imm(T.SET, 0x80), ins(T.HALT)], own=0x80, od=0x80)
        self.assertEqual(pads(an.start_node().variants[0], 7), [(1, "Z", "0"), (2, "0", "Z")])

    def test_periodic_loop(self) -> None:
        an = analyze([imm(T.DIR, 1), imm(T.SET, 1), imm(T.WAIT, 1), imm(T.SET, 0), imm(T.JMP, 1)], own=1)
        v = an.start_node().variants[0]
        self.assertEqual(v.kind, "periodic")
        self.assertEqual(v.end[2], 5)                  # SET(1) WAIT1(2) SET(1) JMP(1)

    def test_data_dependent_exit_from_loop_is_a_soft_boundary(self) -> None:
        # loop: IN rx; JZ rx -> exit; JMP loop
        an = analyze([ins(T.IN, 1), ins(T.JZ, 1) | 3, imm(T.JMP, 0), ins(T.HALT)], own=0)
        self.assertEqual(an.soft, {1})
        kinds = {n.kind for n in an.iter_nodes()}
        self.assertIn(T.BLOCK_BRANCH, kinds)
        self.assertTrue(an.is_exact())

    def test_static_faults(self) -> None:
        an = analyze([imm(T.SET, 2), ins(T.HALT)], own=1)   # SET outside ownership
        self.assertEqual(an.start_node().variants[0].end, ("fault", 1, 1))
        an = analyze([ins(T.XFER, 8, 1, 0)], arch=T.Arch(fused=False))
        self.assertEqual(an.start_node().variants[0].end[0], "fault")

    def test_query_across_boundary(self) -> None:
        an = analyze([imm(T.DIR, 1), imm(T.SET, 1), imm(T.LIMIT, 10), ins(T.WAITPIN, 3, 1), imm(T.SET, 0),
                      ins(T.HALT)], own=1)
        rise = (lambda e: e[T.EK] == "pad" and e[T.EAFTER] == T.P1)
        fall = (lambda e: e[T.EK] == "pad" and e[T.EAFTER] == T.P0 and e[T.EBEFORE] == T.P1)
        q = an.query(fall)
        (node, vi, ei, e), = list(T.occurrences(an, rise))
        self.assertEqual(q.after(node, vi, ei)[:2], (2 + 0 + 1, 2 + 9 + 1))   # arrive +2, stall 0..9, SET +1


FIRMWARE = REPO / "firmware"


@unittest.skipUnless(FIRMWARE.exists(), "firmware directory not present")
class FirmwareImages(unittest.TestCase):
    def test_every_image(self) -> None:
        images = sorted(FIRMWARE.glob("*.image.json"))
        self.assertTrue(images)
        for path in images:
            with self.subTest(image=path.name):
                an = T.Analysis(T.Program.from_image(path))
                self.assertTrue(an.is_exact())
                self.assertEqual(T.assembler_timing_check(an)["status"], "PASS")
                name = path.name.removesuffix(".image.json")
                checks = C.check_program(an, name=name)
                self.assertEqual([c["id"] for c in checks if c["status"] == "FAIL"], [])

    def test_uart_tx_bit_period(self) -> None:
        an = T.Analysis(T.Program.from_image(FIRMWARE / "uart-tx.image.json"))
        pull = [n for n in an.iter_nodes() if n.bpc == 2][0]
        edges = [t for t, a, b in pads(pull.variants[0], 0)]
        self.assertEqual(edges, [2] + [66 + 64 * k for k in range(8)] + [578])


MODEL = REPO / "test" / "model" / "reference.py"


@unittest.skipUnless(MODEL.exists(), "reference model not present")
class GroundTruth(unittest.TestCase):
    def test_directed_scenarios_match_the_model(self) -> None:
        import pe_validate  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "v.json"
            args = argparse.Namespace(repo=str(REPO), out=str(out), suite=["scenarios"], seed=1, count=1,
                                      first=0, cycles=1000)
            self.assertEqual(pe_validate.main_validate(args), 0)
            result = json.loads(out.read_text())
            self.assertEqual(result["result"], "PASS")
            self.assertGreater(result["stats"]["pad_changes_matched"], 500)


if __name__ == "__main__":
    unittest.main()
