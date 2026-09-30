# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Line codes and bit stuffing against USB 2.0, IEEE 802.3 and CAN 2.0 rules."""

import random

import pytest

from _ref import ref

J, K = 0, 1     # low-speed USB on D+: idle J is D+ low


def test_usb_sync_is_kjkjkjkk():
    """USB 2.0 8.2: SYNC appears on the bus as idle then KJKJKJKK."""
    assert ref.nrzi_encode(ref.USB_SYNC, J) == [K, J, K, J, K, J, K, K]


def test_nrzi_zero_toggles_one_holds():
    assert ref.nrzi_encode([0, 0, 1, 1, 0], 0) == [1, 0, 0, 0, 1]
    assert ref.nrzi_encode([1, 1, 1], 1) == [1, 1, 1]


def test_nrzi_round_trip():
    rng = random.Random(7)
    for _ in range(500):
        bits = [rng.randrange(2) for _ in range(rng.randrange(0, 50))]
        start = rng.randrange(2)
        assert ref.nrzi_decode(ref.nrzi_encode(bits, start), start) == bits


def test_manchester_802_3_convention():
    """First half = complement, second half = bit: a 1 rises at mid-cell."""
    assert ref.manchester_encode([1, 0]) == [(0, 1), (1, 0)]
    preamble = ref.bytes_to_bits(b"\x55", lsb_first=True)
    assert preamble == [1, 0, 1, 0, 1, 0, 1, 0]
    halves = ref.manchester_encode(preamble)
    flat = [level for pair in halves for level in pair]
    assert flat == [0, 1, 1, 0] * 4
    sfd = ref.bytes_to_bits(b"\xd5", lsb_first=True)
    assert sfd == [1, 0, 1, 0, 1, 0, 1, 1]


def test_manchester_round_trip_and_violation():
    rng = random.Random(3)
    bits = [rng.randrange(2) for _ in range(64)]
    decoded, violations = ref.manchester_decode(ref.manchester_encode(bits))
    assert decoded == bits and violations == []
    _, violations = ref.manchester_decode([(1, 1), (0, 1)])
    assert violations == [0]


def cells_bits(cells):
    return [bit for bit, _ in cells]


def test_usb_stuff_after_six_ones():
    cells, _ = ref.stuff([1] * 6 + [1], ref.USB_STUFFING)
    assert cells_bits(cells) == [1] * 6 + [0, 1]
    assert [s for _, s in cells] == [False] * 6 + [True, False]


def test_usb_sync_final_one_counts():
    """USB 2.0 7.1.9: the 1 that ends SYNC is the first 1 of a sequence."""
    cells, _ = ref.stuff(ref.USB_SYNC + [1] * 5 + [0], ref.USB_STUFFING)
    assert cells_bits(cells) == ref.USB_SYNC + [1] * 5 + [0, 0]
    assert cells[13] == (0, True)


def test_usb_stuff_bit_before_eop():
    """USB 2.0 7.1.9.1: a due stuff bit is sent even if it is the last bit."""
    cells, _ = ref.stuff([0, 1, 1, 1, 1, 1, 1], ref.USB_STUFFING, trailing=True)
    assert cells[-1] == (0, True)
    cells, _ = ref.stuff([0, 1, 1, 1, 1, 1, 1], ref.USB_STUFFING, trailing=False)
    assert cells[-1] == (1, False)


def test_usb_seven_ones_is_a_stuff_error():
    result = ref.destuff([1] * 7, ref.USB_STUFFING)
    assert result.data == [1] * 6 and result.stuff_errors == [6]


def test_can_stuff_complement_after_five_and_stuff_bit_counts():
    """Bosch CAN 2.0 A section 5: five equal bits, then the complement; the
    stuff bit is part of the transmitted stream and starts the next run."""
    cells, _ = ref.stuff([0] * 5 + [1] * 4 + [0], ref.CAN_STUFFING)
    assert cells_bits(cells) == [0] * 5 + [1] + [1] * 4 + [0] + [0]
    assert [i for i, (_, s) in enumerate(cells) if s] == [5, 10]


def test_can_stuff_error_is_the_sixth_equal_level():
    result = ref.destuff([1] * 6, ref.CAN_STUFFING)
    assert result.stuff_errors == [5]


@pytest.mark.parametrize("rule", [ref.USB_STUFFING, ref.CAN_STUFFING,
                                  ref.StuffRule(2, True), ref.StuffRule(1, False),
                                  ref.StuffRule(8, True), ref.StuffRule(3, False)])
def test_stuff_destuff_round_trip(rule):
    rng = random.Random(rule.run_length * 10 + rule.either)
    for _ in range(300):
        bits = [rng.choice((0, 1, 1)) if not rule.either else rng.randrange(2)
                for _ in range(rng.randrange(0, 60))]
        cells, state = ref.stuff(bits, rule)
        result = ref.destuff(cells_bits(cells), rule, count=len(bits))
        assert result.data == bits
        assert result.stuff_errors == []
        assert result.consumed == len(cells)
        assert result.state == state
        # no run in the stuffed stream is longer than the run length
        run, last = 0, None
        for bit in cells_bits(cells):
            if rule.either:
                run = run + 1 if bit == last else 1
            else:
                run = run + 1 if bit else 0
            last = bit
            assert run <= rule.run_length


def test_stuffing_is_continuous_across_calls():
    """Splitting a stream (one line XFER after another) does not change it."""
    rng = random.Random(11)
    for _ in range(200):
        bits = [rng.randrange(2) for _ in range(rng.randrange(2, 80))]
        cut = rng.randrange(1, len(bits))
        whole, _ = ref.stuff(bits, ref.CAN_STUFFING)
        first, state = ref.stuff(bits[:cut], ref.CAN_STUFFING)
        second, _ = ref.stuff(bits[cut:], ref.CAN_STUFFING, state)
        assert first + second == whole


def test_lcfg_fields_of_the_demos_name_the_standard_rules():
    """The LCFG words of firmware/ext select the standards' stuffing rules."""
    usb_rx = ref.Lcfg.decode(1749)
    assert usb_rx.code == ref.NRZI and usb_rx.stuffing and usb_rx.rule == ref.USB_STUFFING
    assert usb_rx.pair and usb_rx.se0_end and usb_rx.initial_level == 1 and not usb_rx.invalid
    usb_tx = ref.Lcfg.decode(213)
    assert usb_tx.rule == ref.USB_STUFFING and usb_tx.pair and usb_tx.initial_level == 0
    can = ref.Lcfg.decode(1356)
    assert can.code == ref.NRZ and can.stuffing and can.rule == ref.CAN_STUFFING
    assert can.arbitration and can.initial_level == 1 and not can.pair
    eth = ref.Lcfg.decode(130)
    assert eth.code == ref.MANCHESTER and eth.pair and not eth.stuffing
    for imm in (1749, 213, 1356, 130, 1024):
        assert ref.Lcfg.decode(imm).encode() == imm


def test_lcfg_invalid_encodings():
    assert ref.Lcfg.decode(3).invalid                       # line code 3
    assert ref.Lcfg.decode(1 << 11).invalid                 # bits 23..11
    assert ref.Lcfg.decode(0b1100).invalid                  # either polarity, run length 1
    assert not ref.Lcfg.decode(0b0100).invalid              # runs of 1s, length 1
    assert not ref.Lcfg.decode(0b1_1100).invalid            # either polarity, length 2
    assert ref.Ltim(255, 1, 0).invalid and not ref.Ltim(255, 0, 0).invalid
    assert not ref.Ltim(254, 255, 0).invalid
