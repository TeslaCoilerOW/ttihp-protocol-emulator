# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""READ_SELECT 7 capability bits (docs/isa.md, "Discovery") on both known
devices: the design of record ("base", ISA 2, no capability bits) and the
line-unit extension variant ("diet8_rec16", 0x000F5F03).

* the version word splits into the ISA version (bits 7..0) and the capability
  bits (23..8), and the self-test compares only bits 7..0 with an ISA version;
* the host's device table agrees with the reference model's configurations;
* images need the capabilities their words use (plus an optional "requires"
  declaration), and the loader refuses an image that needs the line unit on a
  device, or an engine, without it;
* the self-test passes on each device with its profile, fails with the other
  device's profile, and its line_unit section detects a unit that keeps the CRC
  across START and a capability word that does not match the hardware.
"""

import binascii
import copy
import json
import unittest

import support
from pe_host import ProtocolEmulator, selftest
from pe_host import protocol as P
from pe_host.errors import CapabilityMismatch, ImageError, IsaMismatch
from pe_host.image import FirmwareImage
from pe_host.ports.model import ModelPort

DEVICES = ("base", "diet8_rec16")


def device(name):
    pe = ProtocolEmulator(ModelPort(device=name), device=name)
    pe.reset()
    return pe


def ext_image(name):
    return FirmwareImage.load(str(support.FIRMWARE / "ext" / (name + ".image.json")),
                              source="required")


def source_capabilities(name):
    """The capabilities an ext image needs, from its source instructions
    (mnemonics and operand fields), independently of the word decoder."""
    with open(str(support.FIRMWARE / "ext" / (name + ".source.json"))) as handle:
        source = json.load(handle)
    need = 0
    for ins in source["instructions"]:
        m, c, imm = ins["mnemonic"], ins.get("c", 0), ins.get("imm", 0)
        if m in ("LTIM", "LCFG", "CRC", "LSTAT") or (m == "XFER" and c & 0x20):
            need |= P.CAP_LINE_UNIT
        if m == "LTIM" and (imm >> 8) & 0xFF:
            need |= P.CAP_FRACTION
        if m == "LCFG" and imm & 0x4:
            need |= P.CAP_STUFFING
        if m == "LCFG" and imm & 0x100:
            need |= P.CAP_ARBITRATION
        if m == "CRC" or (m == "XFER" and c & 0x40):
            need |= P.CAP_LINE_UNIT | P.CAP_CRC16
        if m == "CRC" and c == 3:
            need |= P.CAP_CRC_PRESETS
    return need


class VersionWordTest(unittest.TestCase):
    def test_version_word_splits_into_isa_version_and_capability_bits(self):
        self.assertEqual(P.split_version(0x000F5F03), (3, 0x0F5F))
        self.assertEqual(P.split_version(0x00000002), (2, 0))

    def test_diet8_rec16_capability_bits_decode_as_docs_isa_discovery_lists(self):
        caps = P.Capabilities.from_word(0x000F5F03)
        self.assertEqual(caps.names(), ["line_unit", "fraction", "stuffing", "arbitration",
                                        "crc16", "crc_presets"])
        self.assertTrue(caps.line_unit)
        self.assertEqual(caps.line_engines, 0xF)
        self.assertEqual(caps.problems(4), [])
        none = P.Capabilities.from_word(2)
        self.assertFalse(none.line_unit)
        self.assertEqual((none.names(), none.line_engines, none.problems(4)), ([], 0, []))

    def test_malformed_capability_fields_are_reported(self):
        cases = {0x0F5F | 0x8000: "reserved", 0x0F5F | 0x1000: "reserved",
                 0x0010: "without the line unit", 0x0001: "engine mask",
                 0x0F00: "engine mask"}
        for bits, text in cases.items():
            with self.subTest(bits=hex(bits)):
                problems = P.Capabilities(bits).problems(4)
                self.assertTrue(any(text in p for p in problems), problems)
        self.assertTrue(any("beyond 1" in p for p in P.Capabilities(0x0F5F).problems(2)))

    def test_known_devices_match_the_reference_model_configurations(self):
        from model.variant import load_config
        for name in DEVICES:
            with self.subTest(device=name):
                path = support.REPO / "configs" / ("instruction-sram-32.json" if name == "base"
                                                   else "variants/%s.json" % name)
                config = load_config(path)
                profile = P.DEVICES[name]
                self.assertEqual(config.options.version_word, profile["version_word"])
                self.assertEqual(config.options.debug_counters, profile["debug_counters"])
                with open(str(path)) as handle:
                    self.assertEqual(json.load(handle)["architecture"], profile["architecture"])


class ImageCapabilityTest(unittest.TestCase):
    def test_ext_images_need_the_capabilities_their_sources_use(self):
        names = support.ext_image_names()
        self.assertEqual(names, ["10base-t-udp", "can-node", "crc16-stream", "relay",
                                 "usb-ls-in-responder"])
        for name in names:
            with self.subTest(image=name):
                image = ext_image(name)
                self.assertEqual(image.required_capabilities, source_capabilities(name))
                self.assertEqual(image.isa_version, 2, "isa_version keeps its meaning")
        self.assertEqual(ext_image("relay").required_capabilities, 0)
        self.assertTrue(ext_image("can-node").required_capabilities & P.CAP_ARBITRATION)
        self.assertTrue(ext_image("usb-ls-in-responder").required_capabilities & P.CAP_FRACTION)

    def test_design_of_record_images_need_no_capability(self):
        for name in support.image_names():
            image = FirmwareImage.load(str(support.FIRMWARE / (name + ".image.json")))
            self.assertEqual(image.required_capabilities, 0, name)

    def _data(self, name):
        with open(str(support.FIRMWARE / "ext" / (name + ".image.json"))) as handle:
            return json.load(handle)

    def test_requires_declaration_must_cover_the_words(self):
        data = self._data("crc16-stream")
        used = ext_image("crc16-stream").required_capabilities
        ok = copy.deepcopy(data)
        ok["requires"] = P.capability_names(used) + ["crc32"]
        image = FirmwareImage(ok)
        self.assertEqual(image.required_capabilities, used | P.CAP_CRC32)
        short = copy.deepcopy(data)
        short["requires"] = ["line_unit"]
        with self.assertRaises(ImageError) as ctx:
            FirmwareImage(short)
        self.assertIn("crc16", str(ctx.exception))
        for bad in (["line_unit", "crc64"], "line_unit", None):
            broken = copy.deepcopy(data)
            broken["requires"] = bad
            if bad is None:
                self.assertEqual(FirmwareImage(broken).required_capabilities, used)
                continue
            with self.subTest(requires=bad), self.assertRaises(ImageError):
                FirmwareImage(broken)

    def test_capability_check_names_the_missing_feature_and_engine(self):
        image = ext_image("crc16-stream")               # engine 0
        image.check_capabilities(0x0F5F)
        image.check_capabilities(P.Capabilities(0x0F5F), engine=3)
        with self.assertRaises(CapabilityMismatch) as ctx:
            image.check_capabilities(0)
        self.assertIn("capability line_unit", str(ctx.exception))
        only_engine_1 = 0x0200 | 0x005F
        with self.assertRaises(CapabilityMismatch) as ctx:
            image.check_capabilities(only_engine_1)
        self.assertIn("line unit on engine 0", str(ctx.exception))
        image.check_capabilities(only_engine_1, engine=1)
        with self.assertRaises(CapabilityMismatch):
            image.check_capabilities(0x0F4F)            # no CRC-16
        self.assertTrue(issubclass(CapabilityMismatch, ImageError))
        ext_image("relay").check_capabilities(0)


class DeviceTest(unittest.TestCase):
    def test_isa_version_is_bits_7_to_0_of_read_select_7(self):
        for name, isa in (("base", 2), ("diet8_rec16", 3)):
            with self.subTest(device=name):
                pe = device(name)
                self.assertEqual(pe.version_word(), P.DEVICES[name]["version_word"])
                self.assertEqual(pe.isa_version(), isa)
                self.assertEqual(pe.check_isa(), isa)
                self.assertEqual(pe.identify(), name)
                self.assertEqual(pe.capabilities().bits, P.DEVICES[name]["version_word"] >> 8)
        with self.assertRaises(IsaMismatch):
            ProtocolEmulator(ModelPort(device="diet8_rec16")).check_isa()
        with self.assertRaises(ValueError):
            ProtocolEmulator(ModelPort(), device="diet4_unknown")

    def test_line_unit_images_are_refused_on_the_base_device(self):
        pe = device("base")
        for name in support.ext_image_names():
            image = ext_image(name)
            pe.reset()
            with self.subTest(image=name):
                if not image.required_capabilities:
                    pe.load_image(image, verify=True)
                    continue
                with self.assertRaises(CapabilityMismatch):
                    pe.load_image(image)
                self.assertFalse(pe.port.model.engines[image.engine].committed,
                                 "nothing is loaded after the refusal")

    def test_line_unit_images_load_on_the_diet8_rec16_device(self):
        pe = device("diet8_rec16")
        for name in support.ext_image_names():
            image = ext_image(name)
            pe.reset()
            with self.subTest(image=name):
                pe.load_image(image, verify=True)
                self.assertEqual(pe.port.model.engines[image.engine].program, image.words)

    def test_design_of_record_images_load_on_both_devices(self):
        for dev in DEVICES:
            pe = device(dev)
            for name in support.image_names():
                image = FirmwareImage.load(str(support.FIRMWARE / (name + ".image.json")))
                pe.reset()
                with self.subTest(device=dev, image=name):
                    pe.load_image(image, verify=True)

    def test_crc16_stream_image_computes_ccitt_crcs_on_the_diet8_rec16_device(self):
        """Loaded through the capability check, the image's pushed CRCs equal
        binascii.crc_hqx (CRC-16/CCITT, non-reflected) of the words' bytes,
        big-endian per word, with initial values 0xFFFF and 0."""
        pe = device("diet8_rec16")
        image = pe.load_image(ext_image("crc16-stream"), verify=True)
        words = [0x31323334, 0x35363738]
        data = b"".join(w.to_bytes(4, "big") for w in words)
        pe.start(1 << image.engine)
        results = []
        for init in (0xFFFF, 0):
            pe.tx_write([init, len(words)] + words, engine=image.engine)
            results.append(pe.rx_read(engine=image.engine, timeout=5000))
        self.assertEqual(results, [binascii.crc_hqx(data, 0xFFFF), binascii.crc_hqx(data, 0)])
        pe.stop(1 << image.engine)


class SelfTestDeviceTest(unittest.TestCase):
    def test_selftest_passes_on_each_device_with_its_profile(self):
        for name in DEVICES:
            with self.subTest(device=name):
                result = selftest.run(ProtocolEmulator(ModelPort(device=name), device=name))
                self.assertTrue(result.passed, result.summary())
                names = [c[1] for c in result.checks]
                self.assertIn("READ_SELECT 7 of device %s" % name, names)
                self.assertIn("capability bits consistent", names)

    def test_selftest_fails_with_the_other_device_profile(self):
        for model, profile in (("base", "diet8_rec16"), ("diet8_rec16", "base")):
            with self.subTest(model=model, profile=profile):
                result = selftest.run(ProtocolEmulator(ModelPort(device=model), device=profile))
                failed = [c[1] for c in result.failures]
                self.assertIn("isa_version", failed)
                self.assertIn("READ_SELECT 7 of device %s" % profile, failed)

    def test_selftest_without_a_device_profile_masks_the_version(self):
        """isa_versions=(3,) without a device: the ISA-3 device passes, with
        READ_SELECT 5 accepted as the count or 0 (debug counters unknown)."""
        pe = ProtocolEmulator(ModelPort(device="diet8_rec16"), isa_versions=(3,))
        self.assertIsNone(pe.expected_version_word)
        result = selftest.run(pe)
        self.assertTrue(result.passed, result.summary())
        names = [c[1] for c in result.checks]
        self.assertIn("completed instructions (or 0 without debug counters)", names)

    def test_line_unit_section_detects_a_crc_kept_across_start(self):
        pe = ProtocolEmulator(ModelPort(device="diet8_rec16"), device="diet8_rec16")
        model = pe.port.model
        original = model._start

        def start_keeps_crc(engine):
            line = getattr(engine, "line", None)       # created at the first START
            crc = line.crc if line is not None else 0
            original(engine)
            engine.line.crc = crc
        model._start = start_keeps_crc
        result = selftest.run(pe, sections=("line_unit",))
        self.assertFalse(result.passed)
        self.assertTrue(all("run 1" in c[1] for c in result.failures), result.summary())

    def test_line_unit_section_detects_a_capability_word_that_does_not_match(self):
        # Claims the unit, has none (base model reporting diet8_rec16's word) ...
        pe = ProtocolEmulator(ModelPort(device="base"), device="base")
        model = pe.port.model
        status = model._status
        model._status = lambda: 0x000F5F02 if model.read_select == 7 else status()
        result = selftest.run(pe, sections=("line_unit",))
        self.assertFalse(result.passed, result.summary())
        # ... and has the unit, claims none.
        pe = ProtocolEmulator(ModelPort(device="diet8_rec16"), device="diet8_rec16")
        model = pe.port.model
        status2 = model._status
        model._status = lambda: 0x00000003 if model.read_select == 7 else status2()
        result = selftest.run(pe, sections=("line_unit",))
        self.assertFalse(result.passed, result.summary())
        self.assertIn("LSTAT faults with code 1 without the line unit",
                      [c[1] for c in result.failures])


if __name__ == "__main__":
    unittest.main()
