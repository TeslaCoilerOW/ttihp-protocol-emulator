# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The self-measured isolation flow (pc_demo.py scope) on the reference model.

test_bridge_adapter.ModelBridge answers the UART bridge's byte protocol from
the reference model; ModelScope adds a stand-in for the capture-unit commands
('A' 'F' 'H' 'Q' 'P' 'U', fpga/rtl/pe_fpga_scope.v header) that records the
model's pads and applied ui_in every cycle in the same record format. This
checks the host side end to end (ScopeSession, pe_demo.isolation's monitor
hook, pe_scope decoding, the VCD hand-off to pe_capture.py and the verdict)
in seconds; the RTL capture unit itself is checked in fpga/sim
(test_fpga_scope.py) and demo/sim (run_scope_case.sh).
"""

import sys
import tempfile
import unittest
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
REPO = DEMO.parent
sys.path.insert(0, str(DEMO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "host"))

import pc_demo  # noqa: E402
import pe_demo  # noqa: E402
from pe_host import ProtocolEmulator  # noqa: E402
from pe_host.peers import Environment  # noqa: E402
from pe_host.ports.model import ModelPort  # noqa: E402
from test_bridge_adapter import CLK_HZ, DEFAULT_LIMIT, ModelBridge  # noqa: E402

sc = pc_demo.load_scope_module()


class UiPort(ModelPort):
    """ModelPort that remembers the ui_in of the cycle being stepped."""

    ui_now = 0

    def _step(self, ui, reset, enabled):
        self.ui_now = ui
        return ModelPort._step(self, ui, reset, enabled)


class ModelScope:
    """Capture-unit stand-in: an Environment peer that sees every cycle."""

    mask = 0

    def __init__(self, depth=4096):
        self.depth = depth
        self.port = None
        self.state, self.flags = 0, 0
        self.prev = None
        self.raw = []
        self.en = self.trig = self.mode = 0
        self.limit = depth
        self.force = self.halt = False
        self.lost = 0
        self.act = [0] * 24
        self.start = self.end = 0
        self.cycle = 0
        self.corrupt_next = 0

    def _write(self, kind, mask, values):
        self.raw.append(sc.pack(self.cycle, kind, mask, values))

    def step(self, cycle, pads):
        self.cycle = cycle
        cur = (pads & 0xFF) | ((self.port.ui_now if self.port else 0) & 0xFF) << 16
        diff = 0 if self.prev is None else cur ^ self.prev
        self.prev = cur
        chg = diff & self.en
        if self.state == 1:
            if self.halt:
                self.state, self.halt = 4, False
                self.flags |= 2
            elif self.force or diff & self.trig:
                self._write(sc.K_START, chg, cur)
                if not diff & self.trig:
                    self.flags |= 4
                self.start, self.state, self.force = cycle, 2, False
                self._count(diff)
        elif self.state == 2:
            self._count(diff)
            mark = cycle % sc.MARK_INTERVAL == 0
            if self.halt or (len(self.raw) + 1 == self.limit and (chg or mark)):
                self._write(sc.K_END, chg, cur)
                self.end = cycle
                if self.halt:
                    self.state, self.halt = 4, False
                    self.flags |= 2
                else:
                    self.state = 3
                    self.flags |= 1
            elif chg:
                self._write(sc.K_CHANGE, chg, cur)
            elif mark:
                self._write(sc.K_MARK, 0, cur)
        elif self.state == 3:
            if chg:
                self.lost += 1
            if self.halt:
                self.state, self.halt = 4, False
                self.flags |= 2
        return 0, 0

    def _count(self, diff):
        for i in range(24):
            self.act[i] += diff >> i & 1

    def command(self, frame):
        c = chr(frame[0])
        if c == "A":
            self.en = int.from_bytes(frame[1:4], "little")
            self.trig = int.from_bytes(frame[4:7], "little")
            self.mode = frame[7]
            lim = int.from_bytes(frame[8:10], "little")
            self.limit = lim if 2 <= lim <= self.depth else self.depth
            self.raw, self.lost, self.act, self.flags = [], 0, [0] * 24, 0
            self.start = self.end = 0
            self.force, self.halt, self.state = not self.mode & 1, False, 1
            return b"A"
        if c == "F":
            self.force = True
            return b"F"
        if c == "H":
            self.halt = True
            return None                       # replied after the next cycle
        if c == "Q":
            return (b"Q" + bytes([1, self.state, self.flags, 12, 24]) + len(self.raw).to_bytes(2, "little")
                    + self.lost.to_bytes(4, "little") + self.start.to_bytes(6, "little")
                    + self.end.to_bytes(6, "little") + (self.cycle - 2).to_bytes(6, "little")
                    + self.en.to_bytes(3, "little") + self.trig.to_bytes(3, "little") + bytes([self.mode])
                    + self.limit.to_bytes(2, "little"))
        if c == "P":
            return b"P" + b"".join(n.to_bytes(4, "little") for n in self.act)
        if c == "U":
            first, count = int.from_bytes(frame[1:3], "little"), int.from_bytes(frame[3:5], "little")
            recs = self.raw[first:first + count]
            body = b"".join(w.to_bytes(9, "little") for w in recs)
            crc = sc.crc16(body)
            if self.corrupt_next and body:
                self.corrupt_next -= 1
                body = bytes([body[0] ^ 0x40]) + body[1:]
            return b"U" + len(recs).to_bytes(2, "little") + body + crc.to_bytes(2, "little")
        return b"?"


class ScopeBridge(ModelBridge):
    SIZES = {"A": 10, "F": 1, "H": 1, "Q": 1, "P": 1, "U": 5}

    def __init__(self, pe, scope):
        ModelBridge.__init__(self, pe)
        self.scope = scope

    def _frame(self):
        if not self.inp:
            return False
        cmd = chr(self.inp[0])
        if cmd == "V":
            self._take(1)
            self.out += b"V" + bytes([2, 1, 0]) + CLK_HZ.to_bytes(4, "little") + (50).to_bytes(2, "little")
            return True
        if cmd not in self.SIZES:
            return ModelBridge._frame(self)
        frame = self._take(self.SIZES[cmd])
        if frame is None:
            return False
        self.pe.idle(20)
        reply = self.scope.command(frame)
        if reply is None:                     # 'H': reply once the halt took effect
            self.pe.idle(2)
            reply = b"H"
        self.out += reply
        return True


def scope_chip(depth=4096):
    scope = ModelScope(depth)
    env = Environment([pe_demo.Jumpers(), scope])
    port = UiPort(env=env)
    scope.port = port
    pe = ProtocolEmulator(port, timeout=DEFAULT_LIMIT)
    pe.strict = False
    mod = pc_demo.load_bridge_module()
    chip = mod.Chip(mod.Bridge(ScopeBridge(pe, scope)))
    adapter = pc_demo.BridgeChip(chip, mod.BridgeError, CLK_HZ,
                                 sleep=lambda seconds: pe.idle(int(seconds * CLK_HZ)))
    return adapter, chip, mod, scope


class ScopeFlowOnModel(unittest.TestCase):
    def test_self_measured_isolation(self):
        phases, results = {}, {}
        for mode in ("idle", "loaded"):
            adapter, chip, mod, scope = scope_chip()
            images = pc_demo.bridge_images(mod, pe_demo.ISOLATION_IMAGES)
            result, caps = pc_demo.scope_measure(adapter, chip.b, images, mode, fclk_hz=CLK_HZ, captures=2,
                                                 limit=1500, seed=3, max_cycles=4_000_000,
                                                 log=lambda s: None, poll_cycles=2000)
            self.assertTrue(result["probe_running"])
            self.assertEqual(len(caps), 2)
            for cap in caps:
                self.assertTrue(cap.complete and cap.status.full, cap.summary())
                self.assertEqual(len(cap.records), 1500)
            phases[mode], results[mode] = caps, result
        self.assertGreater(results["loaded"]["burst_operations"], 0)
        with tempfile.TemporaryDirectory() as tmp:
            log = []
            summary = pc_demo.scope_report(phases, Path(tmp), log=log.append, plot=False)
            text = "\n".join(log)
            self.assertTrue(summary["pass"], (summary, text))
            self.assertIn("NON-INTERFERENCE PASS", text)
            self.assertEqual(summary["jitter_pp_cycles"], {"idle": 0, "loaded": 0})
            ev = summary["load_evidence"]
            self.assertGreater(ev["loaded"]["edges"]["uio0"], 0)
            self.assertGreater(ev["loaded"]["edges"]["ui4"], 0)
            self.assertEqual(ev["idle"]["edges"]["ui4"], 0)
            for name in ("predicted.json", "idle.json", "loaded.json", "compare.txt", "verdict.json",
                         "idle-0.vcd", "loaded-1.json"):
                self.assertTrue((Path(tmp) / name).exists(), name)

    def test_verdict_needs_load_evidence(self):
        """Two idle phases labelled idle/loaded: frames agree, but the 'loaded'
        windows show no load, so the experiment must not pass."""
        phases = {}
        for label in ("idle", "loaded"):
            adapter, chip, mod, scope = scope_chip()
            images = pc_demo.bridge_images(mod, pe_demo.ISOLATION_IMAGES)
            _, caps = pc_demo.scope_measure(adapter, chip.b, images, "idle", fclk_hz=CLK_HZ, captures=1,
                                            limit=800, seed=1, max_cycles=2_000_000, log=lambda s: None,
                                            poll_cycles=2000)
            phases[label] = caps
        with tempfile.TemporaryDirectory() as tmp:
            summary = pc_demo.scope_report(phases, Path(tmp), log=lambda s: None, plot=False)
        self.assertFalse(summary["pass"])
        self.assertTrue(any("no load" in r for r in summary["reasons"]), summary["reasons"])

    def test_crc_error_is_retried(self):
        adapter, chip, mod, scope = scope_chip()
        images = pc_demo.bridge_images(mod, pe_demo.ISOLATION_IMAGES)
        scope.corrupt_next = 1
        result, caps = pc_demo.scope_measure(adapter, chip.b, images, "idle", fclk_hz=CLK_HZ, captures=1,
                                             limit=600, seed=1, max_cycles=2_000_000, log=lambda s: None,
                                             poll_cycles=2000)
        self.assertEqual(result["capture_retries"], 1)
        self.assertEqual(len(caps[0].records), 600)


if __name__ == "__main__":
    unittest.main()
