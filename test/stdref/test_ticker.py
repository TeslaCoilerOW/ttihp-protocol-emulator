# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Ticker schedule T(k) from the contract formula (docs/isa.md, Ticker)."""

import random
from fractions import Fraction

import pytest

from _ref import ref


@pytest.mark.parametrize("period", [1, 2, 3, 16, 50, 127, 254, 255])
def test_recursion_equals_closed_form(period):
    fractions = range(256) if period != 255 else [0]
    for fraction in fractions:
        for delay in (0, 1, 7):
            ltim = ref.Ltim(period, fraction, delay)
            ticks = ref.ticker_ticks(100, ltim, 600)
            for k in (0, 1, 2, 255, 256, 257, 511, 512, 599):
                assert ticks[k] == ref.ticker_tick_closed_form(100, ltim, k)


def test_first_tick_delay():
    assert ref.ticker_ticks(10, ref.Ltim(5, 0, 0), 2) == [15, 20]
    assert ref.ticker_ticks(10, ref.Ltim(5, 0, 3), 2) == [13, 18]
    # the carry of tick k lengthens the interval after tick k
    assert ref.ticker_ticks(0, ref.Ltim(2, 128, 0), 5) == [2, 4, 7, 9, 12]


def test_usb_low_speed_example():
    """isa.md / extension.md 3.2: LTIM 16, frac=171 at 50 MHz."""
    ltim = ref.Ltim(16, 171, 0)
    ticks = ref.ticker_ticks(0, ltim, 257)
    # 128 bits = 256 ticks take 4267 cycles, 33.3359375 cycles per bit
    assert ticks[256] - ticks[0] == 4267
    assert Fraction(4267, 128) == Fraction(2 * (16 * 256 + 171), 256) == Fraction(333359375, 10**7)
    # bit rate / 1.5 Mbit/s = 12800 / 12801: 78 ppm slow
    rate_ratio = Fraction(50_000_000, 1) / Fraction(4267, 128) / 1_500_000
    assert rate_ratio == Fraction(12800, 12801)
    assert 78 <= (1 - rate_ratio) * 10**6 < 79


def test_10base_t_and_can_examples():
    eth = ref.ticker_ticks(0, ref.Ltim(2, 0, 0), 9)
    assert [eth[2 * n] - eth[0] for n in range(5)] == [0, 4, 8, 12, 16]      # 4 cycles per bit
    can = ref.ticker_ticks(0, ref.Ltim(50, 0, 0), 5)
    assert can[2] - can[0] == 100 and can[1] - can[0] == 50                  # sample point 50 %


def test_ticker_first_after():
    ticker = ref.Ticker(0, ref.Ltim(3, 0, 0))            # ticks 3, 6, 9, 12, ...
    assert ticker.first_after(2, 0) == 0
    assert ticker.first_after(3, 0) == 2                 # strict: tick 3 is not after 3
    assert ticker.first_after(3, 0, strict=False) == 0
    assert ticker.first_after(3, 1) == 1
    assert ticker.first_after(6, 1) == 3


def test_random_fraction_average():
    rng = random.Random(4)
    for _ in range(200):
        period = rng.randrange(1, 255)
        fraction = rng.randrange(256)
        ticks = ref.ticker_ticks(0, ref.Ltim(period, fraction, 0), 1025)
        assert ticks[1024] - ticks[0] == 1024 * period + 4 * fraction
