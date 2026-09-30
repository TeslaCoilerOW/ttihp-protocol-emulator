# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""The transaction-level unit reference and the pad-trace decoder."""

import random

import pytest

from _ref import ref

PINS = ref.Pins(clock=1, tx=0, rx=2)
LINE, CRC, MSB, DRIVE, SAMPLE = 1 << 5, 1 << 6, 1 << 2, 1 << 3, 1 << 4


def make_unit(ltim_cycle, ltim, lcfg, preset=0, crc=0):
    unit = ref.LineUnitRef()
    unit.lcfg(lcfg.encode())
    unit.ltim(ltim_cycle, ltim.encode())
    unit.crc_preset(preset)
    unit.crc_set(crc)
    return unit


def drive(unit, issue, a, c, tx):
    return unit.xfer(issue, a, c, tx, 0, PINS)


def traces(writes, length, initial_data=0, initial_pair=1):
    return (ref.render_trace(writes, PINS.tx, length, initial_data),
            ref.render_trace(writes, PINS.clock, length, initial_pair))


def lcfg(code=ref.NRZ, stuffing=False, either=False, run_length=1, pair=False,
         arbitration=False, se0_end=False, level=0):
    return ref.Lcfg(code, stuffing, either, run_length, pair, arbitration, se0_end, level)


@pytest.mark.parametrize("code", [ref.NRZ, ref.NRZI, ref.MANCHESTER])
@pytest.mark.parametrize("msb_first", [False, True])
def test_drive_and_decode_round_trip(code, msb_first):
    """Back-to-back XFERs (P >= 2, reissue within 3 cycles) decode exactly."""
    rng = random.Random(code * 2 + msb_first)
    for _ in range(40):
        period = rng.randrange(2, 9)
        ltim = ref.Ltim(period, rng.randrange(256), rng.randrange(0, 5))
        cfg = lcfg(code, stuffing=rng.random() < 0.6, either=rng.random() < 0.5,
                   run_length=rng.randrange(2, 9), pair=rng.random() < 0.5,
                   level=rng.randrange(2))
        preset = rng.randrange(4)
        crc0 = rng.randrange(1 << 16)
        unit = make_unit(5, ltim, cfg, preset, crc0)
        issue = 5 + rng.randrange(1, 4)
        words = [rng.randrange(1 << 32) for _ in range(3)]
        counts = [rng.randrange(1, 33) for _ in words]
        writes, sent = [], []
        c = LINE | CRC | DRIVE | (MSB if msb_first else 0)
        first = None
        for word, count in zip(words, counts):
            result = drive(unit, issue, count, c, word)
            first = first if first is not None else result.cells[0].boundary
            writes += result.writes
            sent += ref.word_to_bits(word, count, msb_first)
            assert result.data_sent == ref.word_to_bits(word, count, msb_first)
            issue = result.complete + 1 + rng.randrange(0, 3)
        ticker = ref.Ticker(5, ltim)
        k0 = ticker.first_after(first - 1, 0)
        assert ticker.tick(k0) == first
        length = ticker.tick(k0 + 4 * len(sent) + 40) + 2      # stuffing adds cells
        data_trace, pair_trace = traces(writes, length, initial_data=cfg.initial_level,
                                        initial_pair=cfg.initial_level ^ 1)
        decoded = ref.decode_pad_trace(data_trace, 5, ltim.encode(), cfg.encode(), k0,
                                       pair_trace=pair_trace if cfg.pair else None,
                                       count=len(sent), level=cfg.initial_level,
                                       crc_preset=preset, crc_init=crc0, msb_first=msb_first)
        assert decoded.violations == []
        assert decoded.data == sent
        assert decoded.crc == unit.crc


def test_gapless_nrz_stream_decodes_exactly():
    ltim = ref.Ltim(3, 0, 0)
    cfg = lcfg(ref.NRZ, stuffing=True, either=True, run_length=5, pair=True, level=1)
    unit = make_unit(0, ltim, cfg, preset=2)
    rng = random.Random(1)
    words = [rng.randrange(1 << 32) for _ in range(4)]
    issue, writes, sent = 1, [], []
    for word in words:
        result = drive(unit, issue, 32, LINE | CRC | DRIVE | MSB, word)
        writes += result.writes
        sent += ref.word_to_bits(word, 32, True)
        issue = result.complete + 3            # completes at a boundary; 3 cycles to reissue
    length = issue + 20
    data_trace, pair_trace = traces(writes, length, 1, 0)
    decoded = ref.decode_pad_trace(data_trace, 0, ltim.encode(), cfg.encode(), 0,
                                   pair_trace=pair_trace, count=len(sent),
                                   crc_preset=2, msb_first=True)
    assert decoded.violations == []
    assert decoded.data == sent
    assert decoded.crc == unit.crc == ref.unit_crc(0, sent, 2, True)
    cells, _ = ref.stuff(sent, ref.CAN_STUFFING)
    assert decoded.line_bits == [b for b, _ in cells]


def test_glitch_and_pair_errors_are_reported():
    ltim = ref.Ltim(4, 0, 0)
    cfg = lcfg(ref.NRZ, pair=True)
    unit = make_unit(0, ltim, cfg)
    result = drive(unit, 1, 8, LINE | DRIVE, 0b10110010)
    data_trace, pair_trace = traces(result.writes, 80)
    clean = ref.decode_pad_trace(data_trace, 0, ltim.encode(), cfg.encode(), 0,
                                 pair_trace=pair_trace, count=8)
    assert clean.violations == [] and clean.data == [0, 1, 0, 0, 1, 1, 0, 1]
    glitched = list(data_trace)
    glitched[result.cells[2].boundary + 1 + 3] ^= 1
    bad = ref.decode_pad_trace(glitched, 0, ltim.encode(), cfg.encode(), 0,
                               pair_trace=pair_trace, count=8)
    assert any("cell 2" in v for v in bad.violations)
    pair_bad = list(pair_trace)
    pair_bad[result.cells[5].boundary + 2] ^= 1
    bad = ref.decode_pad_trace(data_trace, 0, ltim.encode(), cfg.encode(), 0,
                               pair_trace=pair_bad, count=8)
    assert any("pair" in v for v in bad.violations)


def test_manchester_missing_transition_is_reported():
    ltim = ref.Ltim(2, 0, 0)
    cfg = lcfg(ref.MANCHESTER)
    unit = make_unit(0, ltim, cfg)
    result = drive(unit, 1, 8, LINE | DRIVE, 0x55)
    data_trace, _ = traces(result.writes, 60)
    ok = ref.decode_pad_trace(data_trace, 0, ltim.encode(), cfg.encode(), 0, count=8)
    assert ok.violations == [] and ok.data == [1, 0] * 4
    broken = list(data_trace)
    mid = result.cells[3].mid + 1
    for n in range(mid, mid + 2):
        broken[n] ^= 1
    bad = ref.decode_pad_trace(broken, 0, ltim.encode(), cfg.encode(), 0, count=8)
    assert any("cell 3" in v for v in bad.violations)


def test_drive_completion_points():
    """Drive-only completes at the boundary tick of its last cell."""
    ltim = ref.Ltim(5, 0, 2)                  # ticks 2, 7, 12, ...
    unit = make_unit(0, ltim, lcfg())
    result = drive(unit, 1, 3, LINE | DRIVE, 0b101)
    assert [c.boundary for c in result.cells] == [2, 12, 22]
    assert result.complete == 22
    # trailing stuff bit: one more cell
    unit = make_unit(0, ltim, lcfg(stuffing=True, run_length=3))
    result = drive(unit, 1, 3, LINE | DRIVE, 0b111)
    assert [c.stuff for c in result.cells] == [False, False, False, True]
    assert result.complete == 32


def pad_from_cells(levels_per_cell, sample_ticks, length, idle=1, in_latency=ref.IN_LATENCY):
    """Drive each cell's level so that the tick of that cell reads it."""
    pad = [idle] * length
    previous = -1
    for level, tick in zip(levels_per_cell, sample_ticks):
        seen = tick - in_latency
        for n in range(previous + 1, seen + 1):
            pad[n] = level
        previous = seen
    for n in range(previous + 1, length):
        pad[n] = idle
    return pad


def test_sample_nrzi_usb_token_with_se0():
    """Receive an IN token as the usb-ls-in-responder demo does."""
    packet = ref.usb_packet_bits("IN", ref.usb_token_body(0x70, 4))
    levels, cells = ref.usb_line_levels(packet)
    ltim = ref.Ltim(16, 171, 33)
    cfg = lcfg(ref.NRZI, stuffing=True, either=False, run_length=6, pair=True, se0_end=True, level=1)
    unit = make_unit(0, ltim, cfg, preset=0)
    ticker = ref.Ticker(0, ltim)
    mids = [ticker.tick(2 * i + 1) for i in range(len(levels) + 2)]
    # the first K is seen by WAITPIN before LTIM; the unit samples from the 2nd SYNC bit
    dplus = pad_from_cells(levels[1:] + [0, 0], mids, mids[-1] + 10, idle=0)
    dminus = pad_from_cells([l ^ 1 for l in levels[1:]] + [0, 0], mids, mids[-1] + 10, idle=1)

    def sample_fn(t):
        return {0: dplus[t - ref.IN_LATENCY], 1: dminus[t - ref.IN_LATENCY]}

    pins = ref.Pins(clock=1, tx=0, rx=0)
    rx = 0
    r1 = unit.xfer(1, 7, LINE | SAMPLE, 0, rx, pins, sample_fn)
    assert r1.data_received == [0, 0, 0, 0, 0, 0, 1]
    r2 = unit.xfer(r1.complete + 1, 8, LINE | SAMPLE, 0, r1.rx, pins, sample_fn)
    assert r2.data_received == ref.usb_pid_bits(0x69)
    unit.crc_set(0x1F)
    r3 = unit.xfer(r2.complete + 1, 16, LINE | SAMPLE | CRC, 0, r2.rx, pins, sample_fn)
    assert r3.data_received == ref.usb_token_body(0x70, 4)
    assert unit.crc == 0x06                       # CRC5 residue, as the demo checks
    r4 = unit.xfer(r3.complete + 1, 8, LINE | SAMPLE, 0, r3.rx, pins, sample_fn)
    assert r4.se0 and r4.bits_left == 8 and r4.data_received == []
    assert (r4.rx >> 8) & 0xFF == 0x69           # the demo's PID test
    lstat = unit.lstat(False, True)
    assert lstat & 1 and (lstat >> 8) & 0x3F == 8 and not lstat & 4


def test_sample_stuff_error_sets_flag_and_drops_the_bit():
    ltim = ref.Ltim(4, 0, 0)
    cfg = lcfg(ref.NRZ, stuffing=True, either=True, run_length=3)
    unit = make_unit(0, ltim, cfg)
    ticker = ref.Ticker(0, ltim)
    received = [1, 1, 1, 1, 0, 1]             # the 4th cell should be a stuff 0
    mids = [ticker.tick(2 * i + 1) for i in range(len(received))]
    pad = pad_from_cells(received, mids, mids[-1] + 5)
    result = unit.xfer(1, 5, LINE | SAMPLE | MSB, 0, 0, PINS, lambda t: {PINS.rx: pad[t - 2]})
    assert result.data_received == [1, 1, 1, 0, 1]
    assert [c.stuff_error for c in result.cells] == [False, False, False, True, False, False]
    assert unit.flag_stuff and result.rx == 0b11101


def test_arbitration_loss_then_drives_recessive():
    """CAN-style wired AND: the node reading 0 while sending 1 loses."""
    ltim = ref.Ltim(10, 0, 0)
    cfg = lcfg(ref.NRZ, stuffing=True, either=True, run_length=5, arbitration=True, level=1)
    unit = make_unit(0, ltim, cfg, preset=2)
    ours = ref.can_frame(0x123, b"\x11")
    theirs = ref.can_frame(0x122, b"\x22")
    ticker = ref.Ticker(0, ltim)
    length = ticker.tick(2 * len(theirs["stuffed"]) + 4) + 5
    their_cells = theirs["stuffed"]
    bounds = [ticker.tick(2 * i) for i in range(len(their_cells))]
    other = [1] * length
    for i, bit in enumerate(their_cells):
        for n in range(bounds[i] + 1, (bounds[i + 1] + 1) if i + 1 < len(bounds) else length):
            other[n] = bit
    our_pad = {}

    def write_fn(write):
        if write.pin == 0:
            our_pad[write.cycle + ref.OUT_LATENCY] = write.value

    def sample_fn(t):
        # bus = wired AND of our pad and the other node, seen IN_LATENCY later
        n = t - ref.IN_LATENCY
        level = 1
        for cycle, value in sorted(our_pad.items()):
            if cycle <= n:
                level = value
        return {PINS.rx: level & other[n]}

    fields = ours["fields"]
    word = ref.bits_to_int(fields, lsb_first=False) << (32 - len(fields))
    result = unit.xfer(0, len(fields), LINE | DRIVE | SAMPLE | MSB | CRC, word, 0,
                       ref.Pins(clock=1, tx=0, rx=2), sample_fn, write_fn)
    # the first difference is the last ID bit (0x123 vs 0x122): data bit 11
    assert unit.flag_lost
    assert result.lost_at is not None
    lost_cell = result.cells[result.lost_at]
    assert lost_cell.line_bit == 1 and lost_cell.sample == 0
    assert all(c.line_bit == 1 for c in result.cells[result.lost_at + 1:])
    # it kept receiving the winner's bits and fed them to the CRC
    assert result.data_received == theirs["fields"][:len(fields)]
    assert unit.crc == ref.unit_crc(0, theirs["fields"][:len(fields)], 2, True)
