# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""pc_demo.BridgeChip (the PC path) on the reference model.

fpga/host/pe_host.py talks to a stand-in for the UART bridge that implements
the bridge's byte protocol (fpga/rtl/pe_uart_host_bridge.v header) on top of
the host library and the reference model: every W/R frame becomes the same
nibble transfer the bridge performs, with the bridge's timeout, and time
passes between frames. This checks the PC adapter and the scenarios through
it; the RTL bridge itself is exercised by demo/sim (DEMO_TB=bridge) and
fpga/sim.
"""

import sys
import unittest
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
REPO = DEMO.parent
sys.path.insert(0, str(DEMO))
sys.path.insert(0, str(REPO / "host"))

import pc_demo  # noqa: E402
import pe_demo  # noqa: E402
from pe_host import ProtocolEmulator  # noqa: E402
from pe_host.peers import Environment, I2CTarget, PadCapture, UartMonitor, UartSource  # noqa: E402
from pe_host.ports.model import ModelPort  # noqa: E402

CLK_HZ = 50_000_000
FRAME_CYCLES = 300        # model cycles that pass per bridge frame (stand-in for UART time)
DEFAULT_LIMIT = 1_000_000


class ModelBridge:
    """Transport that answers the bridge protocol from the reference model."""

    def __init__(self, pe):
        self.pe = pe
        self.out = bytearray()
        self.inp = bytearray()
        self.limit = DEFAULT_LIMIT

    def write(self, data):
        self.inp += data
        while self._frame():
            pass

    def read(self, count, timeout):
        got, self.out = bytes(self.out[:count]), self.out[count:]
        return got

    def _take(self, n):
        if len(self.inp) < n:
            return None
        got, self.inp = bytes(self.inp[:n]), self.inp[n:]
        return got

    def _frame(self):
        if not self.inp:
            return False
        cmd = chr(self.inp[0])
        size = {"V": 1, "S": 1, "C": 1, "L": 5, "W": 6, "R": 2, "X": 2, "N": 2}.get(cmd, 1)
        frame = self._take(size)
        if frame is None:
            return False
        pe = self.pe
        pe.idle(FRAME_CYCLES)
        if cmd == "V":
            self.out += b"V" + bytes([1, 1, 0]) + CLK_HZ.to_bytes(4, "little") + (50).to_bytes(2, "little")
        elif cmd == "C":
            self.out += b"C" + (pe.cycles & 0xFFFFFFFF).to_bytes(4, "little")
        elif cmd == "L":
            value = int.from_bytes(frame[1:5], "little")
            self.limit = value or DEFAULT_LIMIT
            self.out += b"K"
        elif cmd == "X":
            pe.reset(max(1, frame[1]))
            self.out += b"K"
        elif cmd == "N":
            self.out += b"K"
        elif cmd == "W":
            window, word = frame[1], int.from_bytes(frame[2:6], "little")
            if window > 2:
                self.out += b"E"
            elif pe.try_write_word(window, word, self.limit):
                self.out += b"K"
            else:
                self.out += b"T\x00"
        elif cmd == "R":
            window, bounce = frame[1] & 0x7F, frame[1] & 0x80
            if window not in (0, 3):
                self.out += b"E"
                return True
            if bounce:
                pe.set_window(1 if window == 0 else 0)
            word = pe.try_read_word(window, self.limit)
            self.out += b"T\x00" if word is None else b"K" + word.to_bytes(4, "little")
        else:
            self.out += b"?"
        return True


def bridge_chip(env):
    port = ModelPort(env=env)
    pe = ProtocolEmulator(port, timeout=DEFAULT_LIMIT)
    pe.strict = False
    mod = pc_demo.load_bridge_module()
    chip = mod.Chip(mod.Bridge(ModelBridge(pe)))
    adapter = pc_demo.BridgeChip(chip, mod.BridgeError, CLK_HZ,
                                 sleep=lambda seconds: pe.idle(int(seconds * CLK_HZ)))
    return adapter, mod, pe


class BridgeAdapterOnModel(unittest.TestCase):
    def test_isolation_idle_and_loaded(self):
        runs = {}
        for mode in ("idle", "loaded"):
            cap = PadCapture(limit=10 ** 7)
            adapter, mod, pe = bridge_chip(Environment([pe_demo.Jumpers(), cap]))
            images = pc_demo.bridge_images(mod, pe_demo.ISOLATION_IMAGES)
            result = pe_demo.isolation(adapter, images, mode, 60000, seed=2, log=lambda s: None)
            s = cap.samples
            edges = [(cap.first + i, p, s[i] >> p & 1) for i in range(1, len(s)) for p in (6, 7)
                     if (s[i] ^ s[i - 1]) >> p & 1]
            runs[mode] = (result, [(c - edges[0][0], p, v) for c, p, v in edges])
        idle, loaded = runs["idle"], runs["loaded"]
        self.assertTrue(idle[0]["probe_running"] and loaded[0]["probe_running"])
        self.assertGreater(loaded[0]["host_operations"], 20)
        self.assertGreater(loaded[0]["operation_counts"].get("probe_tx", 0), 0)
        self.assertEqual(loaded[0]["stray_loop_bytes"], [])
        n = min(len(idle[1]), len(loaded[1]))
        self.assertGreater(n, 5000)
        self.assertEqual(idle[1][:n], loaded[1][:n])

    def test_four_and_chain(self):
        bit = 434
        source = UartSource(1, [0x41, 0x0A], bit_cycles=bit, gap_cycles=bit, start=None)
        monitor = UartMonitor(0, bit_cycles=bit)
        src = (DEMO / "sim" / "test_demo.py").read_text()
        ns = {}
        exec(src[src.index("class SpiFlash:"):src.index("@cocotb.test()", src.index("class SpiFlash:"))], ns)  # noqa: S102
        flash = ns["SpiFlash"]()
        i2c = I2CTarget(address=0x48, read_bytes=(0x1B,))
        adapter, mod, pe = bridge_chip(Environment([source, monitor, flash, i2c], pullups=0xFF))
        images = pc_demo.bridge_images(mod, pe_demo.FOUR_IMAGES)
        pe_demo.four_setup(adapter, images, log=lambda s: None)
        source.start = pe.cycles + 100
        pe_demo.four_round(adapter, b"OK", pe_demo.TMP_ADDRESS)
        spi, rd = pe_demo.four_collect(adapter, 1, 1, 400000)
        adapter.wait(20 * bit * 10)
        self.assertEqual(pe_demo.jedec_id(spi[0]), (0xEF, 0x40, 0x18))
        self.assertEqual(rd[0] & 0xFF, 0x1B)
        self.assertEqual(monitor.words, [0x4F, 0x4B, 0x41, 0x0A])
        for s in pe_demo.bridge_status(adapter):
            self.assertTrue(s["running"] and not s["fault"], s)
        # Host-free chain through the same adapter.
        monitor2 = UartMonitor(0, bit_cycles=bit)
        i2c2 = I2CTarget(address=0x48, read_bytes=(0x19, 0x1A))
        adapter, mod, pe = bridge_chip(Environment([monitor2, i2c2], pullups=0xFF))
        names = ("uart-tx-115200", "read-ticker-sim", "hex-formatter", "i2c-read-100k")
        pe_demo.bridge_setup(adapter, pc_demo.bridge_images(mod, names), log=lambda s: None)
        adapter.wait(90000)
        self.assertEqual(pe_demo.parse_hex_lines(bytes(monitor2.words))[:2], [0x19, 0x1A])


if __name__ == "__main__":
    unittest.main()
