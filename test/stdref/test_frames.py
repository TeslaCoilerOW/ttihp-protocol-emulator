# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Standard frames: USB packets (USB-IF examples), CAN 2.0A, Ethernet."""

import binascii
import random

from _ref import ref


def _bits(text):
    return [int(ch) for ch in text if ch in "01"]


# NRZ packets of the USB-IF paper "Cyclic Redundancy Checks in USB" (Draft 1),
# without the "XX1" EOP.
USB_PACKETS = [
    ("SOF", ref.usb_sof_body, (0x710,), "00000001101001010000100011110100"),
    ("SETUP", ref.usb_token_body, (0x15, 0xE), "00000001101101001010100011110111"),
    ("OUT", ref.usb_token_body, (0x3A, 0xA), "00000001100001110101110010111100"),
    ("IN", ref.usb_token_body, (0x70, 0x4), "00000001100101100000111001001110"),
    ("SOF", ref.usb_sof_body, (0x001,), "00000001101001011000000000010111"),
    ("DATA0", ref.usb_data_body, (bytes([0, 1, 2, 3]),),
     "0000000111000011000000001000000001000000110000001111011101011110"),
    ("DATA1", ref.usb_data_body, (bytes([0x23, 0x45, 0x67, 0x89]),),
     "0000000111010010110001001010001011100110100100010111000000111000"),
]


def test_usb_whitepaper_packets():
    for pid, body, args, expected in USB_PACKETS:
        assert ref.usb_packet_bits(pid, body(*args)) == _bits(expected), (pid, args)


def test_usb_line_levels_start_with_sync():
    levels, cells = ref.usb_line_levels(ref.usb_packet_bits("IN", ref.usb_token_body(0x70, 4)))
    assert levels[:8] == [1, 0, 1, 0, 1, 0, 1, 1]
    # decoding the levels gives back the stuffed stream
    assert ref.nrzi_decode(levels, ref.USB_LS_J_DPLUS) == [b for b, _ in cells]


def test_usb_packet_with_stuffing():
    """0xFF data bytes force stuff bits; destuffing restores the packet."""
    packet = ref.usb_packet_bits("DATA0", ref.usb_data_body(b"\xff\xff"))
    levels, cells = ref.usb_line_levels(packet)
    stuffed = [i for i, (_, s) in enumerate(cells) if s]
    assert len(stuffed) >= 2
    back = ref.destuff(ref.nrzi_decode(levels, 0), ref.USB_STUFFING, count=len(packet))
    assert back.data == packet and back.stuff_errors == []


def test_can_frame_crc_and_stuffing():
    rng = random.Random(2)
    for _ in range(100):
        ident = rng.randrange(1 << 11)
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 9)))
        frame = ref.can_frame(ident, data)
        fields = frame["fields"]
        assert len(fields) == 19 + 8 * len(data)
        # CRC-15/CAN residue is 0: the CRC over fields + CRC field is zero
        assert ref.CRC15_CAN.register(fields + ref.int_to_bits(frame["crc"], 15, False)) == 0
        back = ref.destuff(frame["stuffed"], ref.CAN_STUFFING, count=len(fields) + 15)
        assert back.data == fields + ref.int_to_bits(frame["crc"], 15, False)
        assert back.stuff_errors == []


def test_can_crc_by_bosch_shift_register():
    """Bosch CAN 2.0 A 3.1.1: CRC_RG shift register, cross-checked with the division."""
    rng = random.Random(9)
    for _ in range(100):
        bits = [rng.randrange(2) for _ in range(rng.randrange(19, 83))]
        reg = 0
        for nxt in bits:
            crcnxt = nxt ^ ((reg >> 14) & 1)
            reg = (reg << 1) & 0x7FFF
            if crcnxt:
                reg ^= 0x4599
        assert reg == ref.can_crc15(bits)


def test_ethernet_frame_bits():
    frame = bytes(range(60))
    fcs = ref.ethernet_fcs(frame)
    bits = ref.ethernet_line_bits(frame + fcs)
    assert bits[:64] == [1, 0] * 31 + [1, 1]
    assert ref.CRC32_ETHERNET.residue_of(bits[64:]) == 0xDEBB20E3
    assert fcs == binascii.crc32(frame).to_bytes(4, "little")


def test_capability_word_from_the_contract():
    """isa.md Discovery: diet8_rec16 reads 0x000F5F03."""
    assert ref.capability_word(version=3, engines=0xF, crc32=False) == 0x000F5F03
