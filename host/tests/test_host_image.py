# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Encoders, status decoding and firmware-image verification."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import support
from pe_host import ProtocolEmulator
from pe_host import protocol as P
from pe_host.errors import ImageError
from pe_host.image import FirmwareImage, bytecode_sha256, isa3_differences, load_scenario
from pe_host.ports.model import ModelPort


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

    def test_isa3_rule_of_docs_isa(self):
        """isa3_differences names exactly the words docs/isa.md ("ISA version")
        names: SHL/SHR counts that are not byte lanes and JMP/LOOP/JZ targets of
        128 or more; check_isa applies it on version-3 devices only, reading
        bits 7..0 of READ_SELECT 7 (docs/isa.md, "Discovery")."""
        words = [24 << 24 | 1 << 16 | 8, 25 << 24 | 1 << 16 | 12, 5 << 24 | 127, 5 << 24 | 128,
                 11 << 24 | 200, 26 << 24 | 2 << 16 | 0x80, 26 << 24 | 2 << 16 | 0x7F, 19 << 24 | 0x80,
                 24 << 24 | 1 << 16 | 0, 25 << 24 | 1 << 16 | 24, 24 << 24 | 1 << 16 | 1]
        self.assertEqual([pc for pc, _ in isa3_differences(words)], [1, 3, 4, 5, 10])
        image = FirmwareImage.load(str(support.FIRMWARE / "ps2-host.image.json"))
        image.check_isa(2)
        for version in (3, 0x000F5F03):
            with self.assertRaisesRegex(ImageError, "word 45: SHR by 21 is not a byte lane"):
                image.check_isa(version)
        FirmwareImage.load(str(support.FIRMWARE / "uart-rx-idle.image.json")).check_isa(3)

    def test_committed_images_under_the_isa3_rule(self):
        """Of the committed images only ps2-host breaks the rule, at its SHR by
        21 (word 45). Cross-checked on the sources: ps2-host is the only one
        with a shift count that is not a multiple of 8, every JMP/LOOP/JZ target
        is a label, and no image has more than 64 words, so every target is
        below 128."""
        def source(name):
            with open(str(support.FIRMWARE / (name + ".source.json"))) as handle:
                return json.load(handle)["instructions"]
        names = support.image_names()
        self.assertEqual(len(names), 26)
        non_lane = [name for name in names
                    if any(ins["mnemonic"] in ("SHL", "SHR") and ins.get("c", 0) % 8
                           for ins in source(name))]
        self.assertEqual(non_lane, ["ps2-host"])
        for name in names:
            image = FirmwareImage.load(str(support.FIRMWARE / (name + ".image.json")))
            self.assertLessEqual(len(image.words), 64)
            for ins in source(name):
                if ins["mnemonic"] in ("JMP", "LOOP", "JZ"):
                    self.assertIsInstance(ins["target"], str, name)
            with self.subTest(image=name):
                if name == "ps2-host":
                    self.assertEqual(isa3_differences(image.words),
                                     [(45, "SHR by 21 is not a byte lane")])
                    with self.assertRaises(ImageError):
                        image.check_isa(3)
                else:
                    self.assertEqual(isa3_differences(image.words), [])
                    image.check_isa(3)

    def test_byte_lane_devices_refuse_ps2_host(self):
        """On reference models of the byte-lane variants (READ_SELECT 7 = 3),
        load_image refuses ps2-host before sending a word and loads the other
        25 images. diet8 has the design of record's architecture; diet4 (the
        6x4 build) has 4-word queues, which the device does not report, so a
        host left at the default architecture binds these images to it. With
        check_isa=False the loader does not read the version and ps2-host
        loads."""
        try:
            import variants
            from model.variant import make_reference
        except ImportError as exc:  # a PE_HOST_TEST_DIR without the variant model
            self.skipTest("variant model not available: %s" % exc)
        names = support.image_names()
        for variant in ("diet8", "diet4"):
            config = variants.spec_config(variant)
            self.assertEqual(config.options.isa_version, 3)
            pe = ProtocolEmulator(ModelPort(model=make_reference(config, settled=True)),
                                  isa_versions=(2, 3))
            for name in names:
                image = FirmwareImage.load(str(support.FIRMWARE / (name + ".image.json")))
                pe.reset()
                with self.subTest(device=variant, image=name):
                    if name == "ps2-host":
                        cycles = pe.cycles
                        with self.assertRaisesRegex(ImageError, "word 45: SHR by 21"):
                            pe.load_image(image, verify=True)
                        self.assertFalse(pe.port.model.engines[image.engine].committed)
                        self.assertLess(pe.cycles - cycles, 40, "only READ_SELECT 7 was read")
                        pe.load_image(image, check_isa=False, verify=True)
                    else:
                        pe.load_image(image, verify=True)
                    self.assertEqual(pe.port.model.engines[image.engine].program, image.words)

    def test_scenario_binding(self):
        scenario = load_scenario(str(support.SCENARIO))
        self.assertEqual(scenario["start_mask"], 15)
        with self.assertRaisesRegex(ImageError, "engine_count"):
            load_scenario(str(support.SCENARIO), dict(P.DESIGN_ARCHITECTURE, engine_count=2))


if __name__ == "__main__":
    unittest.main()
