# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The flagship concurrent scenario (firmware/flagship-scenario.json) and its
idle-tolerant UART receiver (firmware/uart-rx-idle).

Engine 0 transmits UART on pin 0, engine 1 receives UART on pin 1, engine 2
is an SPI mode-0 controller on pins 2-5 and engine 3 an I2C controller on the
open-drain pins 6/7; a host-configured route moves every received UART byte
from engine 1's RX FIFO into engine 2's TX FIFO without host involvement.
Engine 1 runs uart-rx-idle, which waits for a start bit without a bound.

The uart-rx-idle tests run engine 1 alone (RTL only: skipped at gate level so
the gl_test runtime is unchanged). The source has an exact rational bit period
and places each frame at a rational time, so baud errors and sub-cycle start
phases are exact. Besides the received words they check where the reference
model, in lockstep with the DUT, sampled every data and stop bit:

* the schedule the image declares: frame bit n (start bit 0, stop bit 9) is
  sampled 64n + 31 + d clocks after the first edge whose pad sample shows the
  start bit, where d = 0..2 is the latency of the 3-clock start-bit poll;
* the margins of every sample to both edges of its bit at the transmitter's
  actual bit period (the sample-point analysis for a baud error of +-2 %).
"""

from __future__ import annotations

import math
import os
import random
from fractions import Fraction
from typing import Any

import cocotb

import scenarios
from harness import (CLEAR, RS_LEVELS, RS_PC, RS_STATUS, SELECT, START, STOP, CocotbHarness,
                     Harness, design_config, env_int, pad_value)

GATE_LEVEL = bool(os.environ.get("PE_GATE_LEVEL"))
IDLE_IMAGE = "uart-rx-idle"
BIT = 64          # clocks per bit declared by the image
POLL = 3          # start-bit poll period (IN, XOR, JZ)
FIRST_SAMPLE = 31  # bit n is sampled 64n+31+d clocks after the start edge is registered
SYNC = 2          # an IN on edge k reads the pad sampled on edge k-2 (isa.md Machine)
# Idle stretch (clocks) before the first frame, between frames and after the
# last one in test_uart_rx_idle_long_gaps: 1562.5 bit times by default.
LONG_IDLE = env_int("PE_UART_IDLE_CYCLES", 100_000)


def topup() -> bool:
    """Run firmware/flagship-scenario-topup.json instead of the prefill scenario.

    Automatic when the design's queues hold fewer than the eight prefilled UART
    words (PE_VARIANT diet4/diet2); PE_FLAGSHIP=topup forces it on any design,
    PE_FLAGSHIP=prefill forces the original.
    """
    choice = os.environ.get("PE_FLAGSHIP", "")
    if choice in ("topup", "prefill"):
        return choice == "topup"
    return design_config().fifo_words < 8


@cocotb.test()
async def test_flagship_concurrent(dut):
    """All four engines concurrently plus the autonomous UART-RX -> SPI-TX mover."""
    h = CocotbHarness(dut)
    if topup():
        result = await scenarios.flagship_topup(h)
        dut._log.info("host top-up flagship scenario (fifo_words=%d)", h.model.config.fifo_words)
    else:
        result = await scenarios.flagship(h)
    dut._log.info("flagship scenario complete: %d cycles after START, %d cycles total",
                  result["cycles_to_complete"], h.cycle)


# ----------------------------------------------------------------- uart-rx-idle
class RationalUart:
    """8N1 transmitter on one pad: idle high, LSB first, exact rational bit period.

    Frames start at rational times (in clock periods). The pad value supplied
    for edge k is the line level at time k, so an edge at time t is registered
    by the first clock edge k >= t. ``low_until`` holds the line low (a break)
    until that edge, e.g. across START.
    """

    def __init__(self, bit: Fraction, *, low_until: int = 0) -> None:
        self.bit = Fraction(bit)
        self.low_until = low_until
        self.frames: list[tuple[Fraction, int, int]] = []  # (start, word, stop-bit level)
        self._current = -1

    def send(self, start: Fraction, word: int, *, stop: int = 1) -> Fraction:
        """Queue a frame starting at ``start``; returns the end of its stop bit."""
        start = Fraction(start)
        if self.frames:
            assert start >= self.frames[-1][0] + 10 * self.bit, "frames overlap"
        self.frames.append((start, word & 0xFF, stop))
        return start + 10 * self.bit

    def level(self, cycle: int) -> int:
        if cycle < self.low_until:
            return 0
        while self._current + 1 < len(self.frames) and self.frames[self._current + 1][0] <= cycle:
            self._current += 1  # the harness asks for non-decreasing cycles
        if self._current < 0:
            return 1
        start, word, stop = self.frames[self._current]
        index = math.floor((cycle - start) / self.bit)
        if index == 0:
            return 0
        if index <= 8:
            return word >> (index - 1) & 1
        return stop if index == 9 else 1


class InSamples:
    """Every IN executed by one engine of the reference model: (pad edge it read, pc).

    The model runs in lockstep with the DUT, so these are the DUT's sample
    points as the ISA defines them."""

    def __init__(self, h: Harness, engine: int = 1) -> None:
        self.taken: list[tuple[int, int]] = []
        model = h.model
        watched = model.engines[engine]
        shift_in = model._shift_in

        def record(e: Any, pin: int, msb: bool) -> None:
            if e is watched:
                self.taken.append((h.cycle - SYNC, e.pc))
            shift_in(e, pin, msb)

        model._shift_in = record


def sample_points(samples: InSamples, source: RationalUart, image: dict[str, Any]) -> list[dict[str, Any]]:
    """Per frame: start edge, poll latency d and the (offset, early, late) of bits 1..9.

    offset: clocks from the first edge that registered the start bit to the
    pad sample; early/late: margins of that sample to the leading and trailing
    edge of its bit at the source's bit period."""
    labels = image["labels"]
    frame_pcs = (labels["data"] + 1, labels["stop"])  # data IN, stop-bit IN
    polls = [s for s, pc in samples.taken if pc == labels["poll"]]
    taken = [s for s, pc in samples.taken if pc in frame_pcs]
    assert len(taken) == 9 * len(source.frames), (len(taken), len(source.frames))
    frames = []
    for f, (start, _, _) in enumerate(source.frames):
        edge = math.ceil(start)  # first edge whose pad sample shows the start bit
        bits = taken[9 * f:9 * f + 9]
        detect = max(s for s in polls if s < bits[0])  # the poll sample that saw the start bit
        rows = [(s - edge, s - (start + n * source.bit), start + (n + 1) * source.bit - s)
                for n, s in enumerate(bits, start=1)]
        frames.append({"start": start, "latency": detect - edge, "bits": rows})
    return frames


def check_sample_points(frames: list[dict[str, Any]]) -> tuple[Fraction, Fraction, set[int]]:
    """Assert the declared schedule and in-bit sampling; return the minimum margins and latencies."""
    early_min = late_min = Fraction(10 ** 9)
    latencies = set()
    for f, frame in enumerate(frames):
        d = frame["latency"]
        assert 0 <= d < POLL, f"frame {f}: start bit seen {d} clocks late (poll period {POLL})"
        latencies.add(d)
        for n, (offset, early, late) in enumerate(frame["bits"], start=1):
            assert offset == BIT * n + FIRST_SAMPLE + d, f"frame {f} bit {n}: sampled at +{offset}"
            assert early >= 0 and late > 0, f"frame {f} bit {n}: sample outside its bit ({early}, {late})"
            early_min, late_min = min(early_min, early), min(late_min, late)
    return early_min, late_min, latencies


async def idle_receiver(h: Harness, gaps: list[Fraction], words: list[int], *, bit: Fraction = Fraction(BIT),
                        lead: int = 64, break_cycles: int = 0, tail: int = 0) -> dict[str, Any]:
    """uart-rx-idle alone on engine 1: frame i follows ``gaps[i]`` idle clocks.

    The line is held low (a break) for ``break_cycles`` clocks across START,
    then idle for ``lead`` clocks, so frame 0 starts ``lead + gaps[0]`` clocks
    after the break (or after START). The host drains the RX FIFO while frames
    arrive; afterwards the line idles ``tail`` more clocks and the engine must
    still be polling."""
    await h.start()
    source = RationalUart(bit)
    h.pins = lambda cycle, out: pad_value(out, 0xFD | source.level(cycle) << 1)
    image = await h.load_firmware(IDLE_IMAGE)
    samples = InSamples(h)
    await h.command(SELECT, 1)
    if break_cycles:
        source.low_until = h.cycle + break_cycles
    await h.command(START, 2)
    assert not break_cycles or h.cycle < source.low_until, "START must fall inside the break"
    t = Fraction(max(h.cycle, source.low_until) + lead)
    for gap, word in zip(gaps, words, strict=True):
        t = source.send(t + gap, word)
    engine, got = h.engine(1), []
    for word in words:
        waited = await h.run_until(lambda: engine.rx or engine.fault, int(max(gaps) + 40 * bit) + lead
                                   + break_cycles, "UART RX word")
        assert not engine.fault, f"uart-rx-idle fault {engine.fault} after receiving {got}"
        got.append(await h.read(3))
        assert got[-1] == word, f"UART RX read {got[-1]:#x}, expected {word:#x} (after {waited} idle cycles)"
    await h.run_until(lambda: h.cycle >= t, int(12 * bit), "end of the last stop bit")
    await h.idle(tail)
    assert not engine.fault and engine.running, "uart-rx-idle must still be polling"
    pc = await h.status(RS_PC)
    assert pc in range(image["labels"]["poll"], image["labels"]["poll"] + POLL), f"PC {pc} outside the start-bit poll"
    assert await h.status(RS_LEVELS) == 0
    h.assert_no_faults()
    frames = sample_points(samples, source, image)
    early, late, latencies = check_sample_points(frames)
    polls = [s for s, pc in samples.taken if pc == image["labels"]["poll"]]
    assert polls[0] >= source.low_until, "start-bit poll began before the line went idle after START"
    h.log("uart-rx-idle: %d frames at %s clocks/bit; poll latencies %s; min margin to the bit's leading "
          "edge %.3f, trailing edge %.3f clocks", len(frames), bit, sorted(latencies), early, late)
    return {"image": image, "source": source, "frames": frames, "early": early, "late": late,
            "latencies": latencies}


@cocotb.test(skip=GATE_LEVEL)
async def test_uart_rx_idle_long_gaps(dut):
    """uart-rx-idle: LONG_IDLE clocks of idle line before the first frame, between frames and after the last."""
    h = CocotbHarness(dut)
    words = [0x4A, 0x53]
    # The bounded uart-rx would end each of these stretches in fault 3 after 768 clocks.
    await idle_receiver(h, [Fraction(LONG_IDLE)] * len(words), words, tail=LONG_IDLE)
    h.log("idle stretches of %d clocks (%.1f bit times); %d cycles simulated", LONG_IDLE, LONG_IDLE / BIT, h.cycle)
    await h.command(STOP, 2)


@cocotb.test(skip=GATE_LEVEL)
async def test_uart_rx_idle_sparse(dut):
    """uart-rx-idle: a break across START, sparse frames at every poll phase, then a framing error."""
    h = CocotbHarness(dut)
    rng = random.Random(0x1D1E)
    words = [0x00, 0xFF, 0x55, 0xAA, 0x01, 0x80] + [rng.randrange(256) for _ in range(12)]
    # Whole and fractional gaps; 0, 1 and 2 move the start edge through the three poll phases.
    gaps = [*(Fraction(g) for g in (0, 1, 2, 3, 5, 64, 97)), Fraction(1, 3), Fraction(5, 2),
            *(Fraction(rng.randrange(4000)) + Fraction(rng.randrange(64), 64) for _ in range(len(words) - 9))]
    result = await idle_receiver(h, gaps, words, break_cycles=3 * BIT)
    assert result["latencies"] == {0, 1, 2}, f"poll phases exercised: {sorted(result['latencies'])}"
    # A low stop bit: explicit fault 64 and nothing enqueued.
    source, engine = result["source"], h.engine(1)
    source.send(Fraction(h.cycle + 40), 0xA5, stop=0)
    await h.run_until(lambda: engine.fault != 0, 12 * BIT, "uart-rx-idle framing fault")
    assert scenarios.fault_of(await h.status(RS_STATUS)) == 64
    assert await h.status(RS_LEVELS) == 0
    await h.command(CLEAR, 2 | 1 << 23)


@cocotb.test(skip=GATE_LEVEL)
@cocotb.parametrize(baud_error_percent=[-2, 0, 2])
async def test_uart_rx_idle_baud_tolerance(dut, baud_error_percent):
    """uart-rx-idle: back-to-back frames (one stop bit, no idle) from a transmitter 2 % slow, exact or 2 % fast."""
    h = CocotbHarness(dut)
    bit = Fraction(BIT) / (1 + Fraction(baud_error_percent, 100))
    words = [0x00, 0xFF, 0x55, 0xAA, 0x0F, 0xF0, 0x01, 0x80, *b"Jane Street"]
    # Sub-cycle start phase; every later frame follows the previous stop bit immediately.
    gaps = [Fraction(7, 13)] + [Fraction(0)] * (len(words) - 1)
    result = await idle_receiver(h, gaps, words, bit=bit)
    # At +-2 % every sample stays at least a quarter bit (16 clocks) inside its bit.
    assert min(result["early"], result["late"]) >= BIT // 4
    h.log("baud error %+d %%: bit period %.4f clocks, sample margins >= %.3f (leading) / %.3f (trailing) clocks",
          baud_error_percent, float(bit), float(result["early"]), float(result["late"]))
    await h.command(STOP, 2)
