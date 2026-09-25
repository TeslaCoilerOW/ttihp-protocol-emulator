# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Encoders, status decoding and firmware-image verification."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import support
from pe_host import protocol as P
from pe_host.errors import ImageError
from pe_host.image import FirmwareImage, bytecode_sha256, load_scenario


class ProtocolTest(unittest.TestCase):
    def test_nibble_order_matches_datasheet_example(self):
        # docs/info.md: 0x0400000F is sent as F, 0, 0, 0, 0, 0, 4, 0.
        self.assertEqual(P.nibbles(0x0400000F), [0xF, 0, 0, 0, 0, 0, 4, 0])
        self.assertEqual(P.command_word(P.START, 0xF), 0x0400000F)

    def test_payload_encoders(self):
        self.assertEqual(P.route_payload(1, 2, 8), 1 | 2 << 2 | 16 | 8 << 5)
        self.assertEqual(P.route_payload(3, 0, 0, enable=False), 3)
        self.assertEqual(P.trigger_payload(5, P.TRIG_LOW), 5 | 3 << 3 | 32)
        self.assertEqual(P.own_payload(0xC0, 0xC0), 0xC0C0)
        self.assertEqual(P.split_levels(0x00030005), (5, 3))
        for bad in ((4, 0, 1), (0, 4, 1), (0, 0, 1 << 16)):
            with self.assertRaises(ValueError):
                P.route_payload(*bad)
        with self.assertRaises(ValueError):
            P.command_word(0, 1 << 24)

    def test_status_decode(self):
        s = P.Status(0x4108 | 0x7, engine=2)
        self.assertTrue(s.running and s.committed and s.stalled and s.faulted)
        self.assertEqual(s.fault, 0x41)
        self.assertIn("i2c controller", s.fault_name)
        self.assertEqual(P.Status(0x4100).fault, 0, "code is valid only with bit 3")
        self.assertEqual(P.fault_name(4)[:6], "strict")
        self.assertEqual(P.fault_name(0x99), "firmware FAULT 153")


class ImageTest(unittest.TestCase):
    def test_every_committed_image_verifies_with_source(self):
        names = support.image_names()
        self.assertGreaterEqual(len(names), 19)
        for needed in ("uart-tx", "uart-rx", "spi-controller-mode0", "i2c-write"):
            self.assertIn(needed, names)
        for name in names:
            image = FirmwareImage.load(str(support.FIRMWARE / (name + ".image.json")),
                                       source="required", architecture=P.DESIGN_ARCHITECTURE)
            self.assertTrue(image.source_verified, name)
            self.assertEqual(bytecode_sha256(image.words), image.bytecode_sha256)

    def _tampered(self, mutate, with_source=True):
        src = support.FIRMWARE / "uart-tx.image.json"
        data = json.loads(src.read_text())
        mutate(data)
        tmp = Path(tempfile.mkdtemp())
        (tmp / "uart-tx.image.json").write_text(json.dumps(data))
        if with_source:
            source = (support.FIRMWARE / "uart-tx.source.json").read_bytes()
            (tmp / "uart-tx.source.json").write_bytes(source)
        return str(tmp / "uart-tx.image.json")

    def test_bytecode_tamper_is_rejected(self):
        def flip(d):
            d["words"][3] ^= 1
        with self.assertRaisesRegex(ImageError, "bytecode SHA-256"):
            FirmwareImage.load(self._tampered(flip))

    def test_source_tamper_is_rejected(self):
        def wrong_source(d):
            d["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ImageError, "source SHA-256"):
            FirmwareImage.load(self._tampered(wrong_source))
        image = FirmwareImage.load(self._tampered(wrong_source, with_source=False))
        self.assertFalse(image.source_verified)
        with self.assertRaisesRegex(ImageError, "not found"):
            FirmwareImage.load(self._tampered(wrong_source, with_source=False), source="required")

    def test_structural_checks(self):
        cases = (
            (lambda d: d.update(schema_version="x"), "schema_version"),
            (lambda d: d.update(open_drain=2), "open_drain"),
            (lambda d: d.update(engine=4), "engine"),
            (lambda d: d.update(words=[]), "non-empty"),
            (lambda d: d.update(words=[0] * 65), "capacity"),
            (lambda d: d["words"].__setitem__(0, 1 << 32), "32 bits"),
            (lambda d: d["architecture"].pop("fifo_words"), "lacks fifo_words"),
        )
        for mutate, message in cases:
            with self.assertRaisesRegex(ImageError, message):
                FirmwareImage.load(self._tampered(mutate), source=False)

    def test_architecture_binding(self):
        image = FirmwareImage.load(str(support.FIRMWARE / "spi-controller-mode0.image.json"))
        image.check_binding(P.DESIGN_ARCHITECTURE)
        other = copy.deepcopy(P.DESIGN_ARCHITECTURE)
        other["fifo_words"] = 4
        with self.assertRaisesRegex(ImageError, "fifo_words"):
            image.check_binding(other)
        image.check_binding(other, ignore=("fifo_words",))
        scalar = dict(P.DESIGN_ARCHITECTURE, issue="scalar")
        with self.assertRaisesRegex(ImageError, "issue"):
            FirmwareImage.load(str(support.FIRMWARE / "uart-tx.image.json"), architecture=scalar)

    def test_isa_requirement(self):
        image = FirmwareImage.load(str(support.FIRMWARE / "uart-rx.image.json"))
        image.check_isa(2)
        with self.assertRaisesRegex(ImageError, "needs ISA 2"):
            image.check_isa(1)

    def test_scenario_binding(self):
        scenario = load_scenario(str(support.SCENARIO))
        self.assertEqual(scenario["start_mask"], 15)
        with self.assertRaisesRegex(ImageError, "engine_count"):
            load_scenario(str(support.SCENARIO), dict(P.DESIGN_ARCHITECTURE, engine_count=2))


if __name__ == "__main__":
    unittest.main()
