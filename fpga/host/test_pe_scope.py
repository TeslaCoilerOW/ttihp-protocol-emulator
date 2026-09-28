#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of fpga/host/pe_scope.py (no simulator, standard library only).

    python3 fpga/host/test_pe_scope.py -v

* record format: pack/unpack, full stamps across 22-bit wraps via markers,
  every invariant the decoder enforces;
* the bridge framing and client: a fake bridge answers 'A' 'F' 'H' 'Q' 'P'
  'U' from a record list built like pe_fpga_scope.v builds it, including a
  corrupted 'U' reply (retried) and a permanently corrupted one (error);
* the VCD writer, read back by demo/pe_capture.py as a cycle-domain capture,
  and pe_capture's frame analysis of a synthetic timing-probe capture: exact
  frames -> same; one edge moved by one cycle -> different.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import pe_host  # noqa: E402
import pe_scope as sc  # noqa: E402


def load_capture_module():
    spec = importlib.util.spec_from_file_location("pe_capture", REPO / "demo" / "pe_capture.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pe_capture"] = mod
    spec.loader.exec_module(mod)
    return mod


def build_records(changes, enable, start, end, initial=0):
    """Records as pe_fpga_scope.v writes them for per-cycle channel values.

    changes: {cycle: new 24-bit value}; the capture starts at ``start`` (a
    start record) and ends with an end record at ``end``. Markers at
    multiples of 2^21 where nothing else is written."""
    raw, value, prev = [], initial, initial
    for cyc in range(start, end + 1):
        if cyc in changes:
            value = changes[cyc]
        mask = (value ^ prev) & enable
        prev = value
        if cyc == start:
            raw.append(sc.pack(cyc, sc.K_START, mask, value))
        elif cyc == end:
            raw.append(sc.pack(cyc, sc.K_END, mask, value))
        elif mask:
            raw.append(sc.pack(cyc, sc.K_CHANGE, mask, value))
        elif cyc % sc.MARK_INTERVAL == 0:
            raw.append(sc.pack(cyc, sc.K_MARK, 0, value))
    return raw


def status(records, start, end, enable, *, state=4, flags=2, lost=0, limit=16384, aw=14):
    return sc.Status(1, state, flags, aw, 24, records, lost, start, end, end + 100, enable, 0, 0, limit)


def activity(changes, enable, start, end, initial=0):
    act, prev = [0] * 24, initial
    value = initial
    for cyc in range(start, end + 1):
        if cyc in changes:
            value = changes[cyc]
        d = value ^ prev
        prev = value
        for i in range(24):
            act[i] += d >> i & 1
    return act


class RecordFormat(unittest.TestCase):
    def test_crc_check_value(self):
        self.assertEqual(sc.crc16(b"123456789"), 0x29B1)       # CRC-16/CCITT-FALSE

    def test_pack_unpack(self):
        w = sc.pack(0x3FFFFF, sc.K_END, 0xA5A5A5, 0x5A5A5A)
        self.assertEqual(sc.unpack(w), (0x3FFFFF, 3, 0xA5A5A5, 0x5A5A5A))
        self.assertLess(w, 1 << 72)

    def test_long_gap_across_wraps(self):
        """Two edges 3 x 2^22 cycles apart: markers carry the stamp across."""
        start = (1 << 22) * 5 - 7
        en = 1 << 6
        a, b = start + 3, start + 3 * (1 << 22) + 11
        changes = {a: 1 << 6, b: 0}
        raw = build_records(changes, en, start, b + 2)
        recs = sc.decode(raw, start, b + 2)
        self.assertEqual([r.stamp for r in recs if r.mask], [a, b])
        self.assertTrue(all(r.stamp % sc.MARK_INTERVAL == 0 for r in recs if r.kind == sc.K_MARK))
        # every multiple of 2^21 in (start, end) without a change: 10..16 x 2^21
        self.assertEqual([r.stamp for r in recs if r.kind == sc.K_MARK],
                         [k * sc.MARK_INTERVAL for k in range(10, 17)])
        cap = sc.Capture(status(len(raw), start, b + 2, en), raw, activity(changes, en, start, b + 2))
        self.assertEqual(cap.edges(6), [(a, 1), (b, 0)])
        self.assertEqual(cap.window(), (start, b + 2))

    def test_decoder_rejects(self):
        en = 1
        good = build_records({12: 1, 20: 0}, en, 10, 30)
        with self.assertRaisesRegex(sc.ScopeError, "expected start"):
            sc.decode(good[1:], 12)
        with self.assertRaisesRegex(sc.ScopeError, "does not match the start stamp"):
            sc.decode(good, 11)
        with self.assertRaisesRegex(sc.ScopeError, "does not advance"):
            sc.decode(good[:2] + [sc.pack(12, sc.K_CHANGE, 1, 0)], 10)
        with self.assertRaisesRegex(sc.ScopeError, "marker at stamp"):
            sc.decode(good[:1] + [sc.pack(15, sc.K_MARK, 0, 0)], 10)
        with self.assertRaisesRegex(sc.ScopeError, "follows the end record"):
            sc.decode(good + [sc.pack(40, sc.K_CHANGE, 1, 1)], 10)
        with self.assertRaisesRegex(sc.ScopeError, "end record stamp"):
            sc.decode(good, 10, 31)
        bad = list(good)
        bad[1] = sc.pack(12, sc.K_CHANGE, 1, 0)         # mask says changed, value did not
        with self.assertRaisesRegex(sc.ScopeError, "without a mask bit"):
            sc.Capture(status(len(bad), 10, 30, en), bad, [])
        with self.assertRaisesRegex(sc.ScopeError, "activity counter"):
            sc.Capture(status(len(good), 10, 30, en), good, [5] + [0] * 23)


class FakeBridge:
    """pe_host.Transport answering the capture-unit commands from records."""

    def __init__(self, raw, st, act, corrupt=0, bad_n=(), bad_q=0):
        self.raw, self.st, self.act = raw, st, act
        self.corrupt = corrupt           # corrupt this many 'U' replies (record bytes)
        self.bad_n = list(bad_n)         # add these offsets to the n of the next 'U' replies
        self.bad_q = bad_q               # corrupt this many 'Q' replies (state byte)
        self.out = bytearray()
        self.log = []

    def write(self, data):
        cmd = chr(data[0])
        self.log.append(cmd)
        if cmd == "Q":
            s = self.st
            self.out += (b"Q" + bytes([s.version, s.state, s.flags, s.aw, s.channels])
                         + s.records.to_bytes(2, "little") + s.lost.to_bytes(4, "little")
                         + s.start_stamp.to_bytes(6, "little") + s.end_stamp.to_bytes(6, "little")
                         + s.now.to_bytes(6, "little") + s.enable.to_bytes(3, "little")
                         + s.trigger.to_bytes(3, "little") + bytes([s.mode]) + s.limit.to_bytes(2, "little"))
            if self.bad_q:
                self.bad_q -= 1
                self.out[-37] = 9            # state byte out of range
        elif cmd == "P":
            self.out += b"P" + b"".join(n.to_bytes(4, "little") for n in self.act)
        elif cmd == "U":
            first, count = int.from_bytes(data[1:3], "little"), int.from_bytes(data[3:5], "little")
            recs = self.raw[first:first + count]
            body = b"".join(w.to_bytes(9, "little") for w in recs)
            crc = sc.crc16(body)
            if self.corrupt and body:
                self.corrupt -= 1
                body = bytes([body[0] ^ 1]) + body[1:]
            n = len(recs) + (self.bad_n.pop(0) if self.bad_n else 0)
            self.out += b"U" + n.to_bytes(2, "little") + body + crc.to_bytes(2, "little")
        elif cmd in "AFH":
            self.out += cmd.encode()
        else:
            self.out += b"?"

    def read(self, count, timeout):
        got, self.out = bytes(self.out[:count]), self.out[count:]
        return got


class Client(unittest.TestCase):
    def setUp(self):
        self.en = sc.channel_mask("uio6,uio7")
        self.changes = {100 + 7 * k: (k % 4) << 6 for k in range(1, 300)}
        self.raw = build_records(self.changes, self.en, 100, 2200)
        self.st = status(len(self.raw), 100, 2200, self.en)
        self.act = activity(self.changes, self.en, 100, 2200)

    def test_collect_and_chunks(self):
        fake = FakeBridge(self.raw, self.st, self.act)
        scope = sc.Scope(pe_host.Bridge(fake))
        scope.CHUNK = 64
        cap = scope.collect(50e6)
        self.assertEqual(cap.raw, self.raw)
        self.assertEqual(fake.log.count("U"), (len(self.raw) + 63) // 64)
        want6, level = [], 0
        for c, v in sorted(self.changes.items()):
            if c <= 2200 and (v >> 6 & 1) != level:
                level = v >> 6 & 1
                want6.append((c, level))
        self.assertEqual(cap.edges(6), want6)

    def test_crc_retry_and_failure(self):
        fake = FakeBridge(self.raw, self.st, self.act, corrupt=1)
        scope = sc.Scope(pe_host.Bridge(fake))
        self.assertEqual(scope.read_all(len(self.raw)), self.raw)
        self.assertEqual(scope.crc_retries, 1)
        fake = FakeBridge(self.raw, self.st, self.act, corrupt=10)
        with self.assertRaisesRegex(sc.ScopeError, "CRC mismatch"):
            sc.Scope(pe_host.Bridge(fake)).dump(0, 16, retries=2)

    def test_corrupt_count_and_status(self):
        """The 'U' header count and the 'Q' reply are not CRC-covered: a count
        one too high (short reply, transport timeout), one too low (the reply
        is misframed and leaves bytes behind) and an out-of-range status are
        each detected, the input is drained and the read is repeated."""
        fake = FakeBridge(self.raw, self.st, self.act, bad_n=[+1, -1], bad_q=1)
        scope = sc.Scope(pe_host.Bridge(fake, timeout=0.01))
        scope.CHUNK = 64
        cap = scope.collect(50e6)
        self.assertEqual(cap.raw, self.raw)
        self.assertEqual(scope.link_retries, 3)
        self.assertEqual(fake.out, bytearray())          # nothing left in the input
        fake = FakeBridge(self.raw, self.st, self.act, bad_n=[-1] * 10)
        with self.assertRaisesRegex(sc.ScopeError, "after 3 attempts"):
            sc.Scope(pe_host.Bridge(fake, timeout=0.01)).dump(0, 16, retries=2, expect=16)

    def test_arm_frame(self):
        fake = FakeBridge(self.raw, self.st, self.act)
        written = []
        orig = fake.write
        fake.write = lambda d: (written.append(bytes(d)), orig(d))
        sc.Scope(pe_host.Bridge(fake)).arm(0x0000C0, 0x100000, wait=True, core_view=True, limit=300)
        self.assertEqual(written[0], b"A\xc0\x00\x00\x00\x00\x10\x03\x2c\x01")

    def test_json_round_trip(self):
        cap = sc.Capture(self.st, self.raw, self.act, 12e6, {"x": 1})
        again = sc.Capture.from_json(json.loads(json.dumps(cap.to_json())))
        self.assertEqual(again.records, cap.records)
        self.assertEqual(again.summary(), cap.summary())


class VcdAndAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pc = load_capture_module()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.pred = cls.dir / "predicted.json"
        cls.pc.main(["predict", "--image", str(REPO / "demo" / "firmware" / "timing-probe.image.json"),
                     "--json", str(cls.pred)])
        ref = json.loads(cls.pred.read_text())["pattern"]
        cls.period, cls.events = ref["period_cycles"], ref["events"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def probe_capture(self, frames, shift=None):
        """The predicted frame repeated; shift=(frame, event) moves one edge by +1."""
        en = sc.channel_mask("uio6,uio7")
        per_cycle = {}
        base0 = 50_000
        for f in range(frames):
            for j, (off, ch, pol) in enumerate(self.events):
                cyc = base0 + f * self.period + off + (1 if shift == (f, j) else 0)
                per_cycle.setdefault(cyc, []).append((6 + ch, pol))
        changes, value = {}, 0
        for cyc in sorted(per_cycle):
            for bit, pol in per_cycle[cyc]:
                value = value & ~(1 << bit) | pol << bit
            changes[cyc] = value
        start, end = base0 - 60, base0 + frames * self.period + 5
        raw = build_records(changes, en, start, end)
        return sc.Capture(status(len(raw), start, end, en), raw, activity(changes, en, start, end), 50e6)

    def analyze(self, cap, name):
        vcd = self.dir / f"{name}.vcd"
        cap.write_vcd(vcd)
        out = self.dir / f"{name}.json"
        self.assertEqual(self.pc.main(["analyze", str(vcd), "--channels", "probe6=uio6,probe7=uio7",
                                       "--reference", str(self.pred), "--json", str(out), "--quiet"]), 0)
        return vcd, json.loads(out.read_text())

    def test_vcd_is_cycle_domain(self):
        cap = self.probe_capture(3)
        vcd, _ = self.analyze(cap, "rt")
        read = self.pc.read_vcd(vcd)
        self.assertTrue(read.cycle_domain)
        self.assertEqual(read.fclk_hz, 50e6)
        for ch in (6, 7):
            edges, initial = self.pc.edges_of(read.resolve(f"uio{ch}"))
            self.assertEqual(edges, cap.edges(ch))
            self.assertEqual(initial, cap.initial(ch))
        # Cycle numbers are already exact: --fclk (cycle recovery) is refused.
        self.assertEqual(self.pc.main(["analyze", str(vcd), "--channels", "probe6=uio6", "--fclk", "50e6"]), 2)

    def test_frames_same_and_different(self):
        _, rep = self.analyze(self.probe_capture(12), "same")
        fr = rep["frames"]
        self.assertEqual(rep["conversion"][0]["mode"], "counter")
        self.assertEqual(fr["frames_identical"], fr["frames_complete"])
        self.assertGreaterEqual(fr["frames_complete"], 10)
        self.assertIsNone(fr["grid_departure"])
        self.assertEqual(rep["jitter"]["max_peak_to_peak_cycles"], 0)
        _, bad = self.analyze(self.probe_capture(12, shift=(6, 20)), "shifted")
        ref = [tuple(p) for p in json.loads(self.pred.read_text())["pattern"]["events"]]
        self.assertEqual(self.pc.verdict(rep, ref, self.period)[0], "same")
        v, reasons = self.pc.verdict(bad, ref, self.period)
        self.assertEqual(v, "different", reasons)
        self.assertEqual(bad["jitter"]["max_peak_to_peak_cycles"], 1)


if __name__ == "__main__":
    unittest.main()
