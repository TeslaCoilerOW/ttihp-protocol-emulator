# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""CRC reference against published vectors (reveng catalogue, USB-IF, binascii).

Run: python3 -m pytest test/stdref -q
"""

import binascii
import random

import pytest

from _ref import ref

CHECK_INPUT = b"123456789"


@pytest.mark.parametrize("alg", ref.CATALOGUE, ids=lambda a: a.name)
def test_catalogue_check_value(alg):
    assert alg.compute(CHECK_INPUT) == alg.check


@pytest.mark.parametrize("alg", ref.CATALOGUE, ids=lambda a: a.name)
def test_catalogue_residue(alg):
    rng = random.Random(alg.name)
    for data in (CHECK_INPUT, b"", bytes(rng.randrange(256) for _ in range(37))):
        bits = alg.message_bits(data)
        codeword = bits + alg.crc_field_bits(alg.compute_bits(bits))
        assert alg.residue_of(codeword) == alg.residue


def test_binascii_cross_check():
    """Independent implementations in the Python standard library."""
    rng = random.Random(1)
    xmodem = ref.CATALOGUE_BY_NAME["CRC-16/XMODEM"]
    ibm3740 = ref.CATALOGUE_BY_NAME["CRC-16/IBM-3740"]
    for _ in range(200):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
        assert xmodem.compute(data) == binascii.crc_hqx(data, 0)
        assert ibm3740.compute(data) == binascii.crc_hqx(data, 0xFFFF)
        assert ref.CRC32_ETHERNET.compute(data) == binascii.crc32(data)


def test_presets_match_the_contract_constants():
    for preset in ref.UNIT_PRESETS:
        refl, norm = ref.CONTRACT_PRESET_CONSTANTS[preset.number]
        assert (preset.reflected16, preset.normal16) == (refl, norm), preset.name
    # the generator polynomials as the standards write them
    assert ref.UNIT_PRESETS[0].poly == 0x05 and ref.UNIT_PRESETS[0].width == 5
    assert ref.UNIT_PRESETS[1].poly == 0x8005 and ref.UNIT_PRESETS[1].width == 16
    assert ref.UNIT_PRESETS[2].poly == 0x4599 and ref.UNIT_PRESETS[2].width == 15
    assert ref.UNIT_PRESETS[3].poly == 0x1021 and ref.UNIT_PRESETS[3].width == 16


@pytest.mark.parametrize("preset", range(4))
@pytest.mark.parametrize("msb_first", (True, False))
def test_contract_step_rule_equals_division_for_every_state(preset, msb_first):
    """The contract's serial rule and the division agree on all 2^17 inputs."""
    for reg in range(1 << 16):
        for bit in (0, 1):
            assert ref.unit_crc(reg, [bit], preset, msb_first) == \
                ref.unit_crc_step(reg, bit, preset, msb_first)


@pytest.mark.parametrize("preset", range(4))
@pytest.mark.parametrize("msb_first", (True, False))
def test_multi_bit_division_equals_repeated_steps(preset, msb_first):
    rng = random.Random(preset * 2 + msb_first)
    for _ in range(300):
        reg = rng.randrange(1 << 16)
        bits = [rng.randrange(2) for _ in range(rng.randrange(0, 70))]
        folded = reg
        for bit in bits:
            folded = ref.unit_crc_step(folded, bit, preset, msb_first)
        assert ref.unit_crc(reg, bits, preset, msb_first) == folded


def _emulated():
    out = []
    for alg in ref.CATALOGUE:
        if ref.unit_emulates(alg) is not None:
            out.append(alg)
    return out


def test_every_matching_catalogue_algorithm_is_emulated():
    names = {alg.name for alg in _emulated()}
    assert "CRC-5/USB" in names and "CRC-15/CAN" in names
    assert {"CRC-16/ARC", "CRC-16/USB", "CRC-16/UMTS", "CRC-16/IBM-3740",
            "CRC-16/KERMIT", "CRC-16/XMODEM", "CRC-16/IBM-SDLC"} <= names
    assert len(names) == 20      # all CRC-5, CRC-15 and CRC-16 entries above


@pytest.mark.parametrize("alg", _emulated(), ids=lambda a: a.name)
def test_unit_register_reproduces_catalogue(alg):
    """The unit's 16-bit register, used as firmware would, gives the check value."""
    preset, msb_first, reg0 = ref.unit_emulates(alg)
    bits = alg.message_bits(CHECK_INPUT)
    reg = ref.unit_crc(reg0, bits, preset, msb_first)
    assert ref.unit_result(alg, reg) == alg.check
    # the same through the contract's serial rule
    serial = reg0
    for bit in bits:
        serial = ref.unit_crc_step(serial, bit, preset, msb_first)
    assert serial == reg


def test_short_presets_in_the_other_bit_order():
    """CRC-5 MSB first and CRC-15 LSB first are the w-bit CRCs, aligned."""
    rng = random.Random(5)
    for _ in range(200):
        bits = [rng.randrange(2) for _ in range(rng.randrange(1, 60))]
        init5 = rng.randrange(32)
        init15 = rng.randrange(1 << 15)
        # MSB first: left-aligned in 16 bits
        assert ref.unit_crc(init5 << 11, bits, 0, True) == \
            ref.crc_register(bits, 5, 0x05, init5) << 11
        assert ref.unit_crc(init15 << 1, bits, 2, True) == \
            ref.crc_register(bits, 15, 0x4599, init15) << 1
        # LSB first: mirrored w-bit register in the low bits
        assert ref.unit_crc(ref.reflect(init5, 5), bits, 0, False) == \
            ref.reflect(ref.crc_register(bits, 5, 0x05, init5), 5)
        assert ref.unit_crc(ref.reflect(init15, 15), bits, 2, False) == \
            ref.reflect(ref.crc_register(bits, 15, 0x4599, init15), 15)


def _bits(text):
    return [int(ch) for ch in text if ch in "01"]


# USB-IF, "Cyclic Redundancy Checks in USB", Draft 1: crc5 / crc16 of the
# given NRZ field streams (transmission order) and the CRC fields they give.
USB_CRC5_EXAMPLES = [
    ("00001000111", "10100"),   # SOF timestamp 0x710
    ("10101000111", "10111"),   # SETUP addr 0x15 endp 0xe
    ("01011100101", "11100"),   # OUT addr 0x3a endp 0xa
    ("00001110010", "01110"),   # IN addr 0x70 endp 0x4
    ("10000000000", "10111"),   # SOF timestamp 0x001
]
USB_CRC16_EXAMPLES = [
    ("00000000100000000100000011000000", "1111011101011110"),   # 00 01 02 03
    ("11000100101000101110011010010001", "0111000000111000"),   # 23 45 67 89
]


@pytest.mark.parametrize("stream,field", USB_CRC5_EXAMPLES)
def test_usb_whitepaper_crc5(stream, field):
    bits = _bits(stream)
    crc = ref.CRC5_USB.compute_bits(bits)
    assert ref.CRC5_USB.crc_field_bits(crc) == _bits(field)
    # the unit, preset 0, LSB first, register preloaded with ones
    reg = ref.unit_crc(0x1F, bits, 0, msb_first=False)
    assert ref.int_to_bits((reg & 0x1F) ^ 0x1F, 5, lsb_first=True) == _bits(field)
    # the receiver's residue after the CRC field (the usb-ls-in-responder demo compares with 6)
    assert ref.unit_crc(0x1F, bits + _bits(field), 0, msb_first=False) == 0x06


@pytest.mark.parametrize("stream,field", USB_CRC16_EXAMPLES)
def test_usb_whitepaper_crc16(stream, field):
    bits = _bits(stream)
    crc = ref.CRC16_USB.compute_bits(bits)
    assert ref.CRC16_USB.crc_field_bits(crc) == _bits(field)
    reg = ref.unit_crc(0xFFFF, bits, 1, msb_first=False)
    assert ref.int_to_bits(reg ^ 0xFFFF, 16, lsb_first=True) == _bits(field)


def test_usb_whitepaper_residuals():
    """Whitepaper: token residual 01100 (x^4+x^3), data 1000000000001101."""
    token = _bits("00001000111") + _bits("10100")
    assert ref.crc_register(token, 5, 0x05, 0x1F) == 0b01100
    data = _bits(USB_CRC16_EXAMPLES[0][0]) + _bits(USB_CRC16_EXAMPLES[0][1])
    assert ref.crc_register(data, 16, 0x8005, 0xFFFF) == 0b1000000000001101


def test_ethernet_fcs_residue():
    rng = random.Random(8023)
    for _ in range(50):
        frame = bytes(rng.randrange(256) for _ in range(rng.randrange(60, 120)))
        fcs = ref.ethernet_fcs(frame)
        assert fcs == binascii.crc32(frame).to_bytes(4, "little")
        bits = ref.bytes_to_bits(frame + fcs, lsb_first=True)
        assert ref.CRC32_ETHERNET.residue_of(bits) == ref.CRC32_ETHERNET.residue
