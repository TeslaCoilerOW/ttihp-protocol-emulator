# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Scenario, firmware and mutant checks that need no HDL simulator.

    python3 -m unittest discover -s demo/tests -v

* the demonstration scenarios on the reference model (test/model), with the
  jumper wires and pad-level peers as environment;
* the MicroPython subset check (host/tools/upy_check.py) and, when a
  ``micropython`` binary is available (MICROPYTHON or PATH), a differential
  replay of the isolation scenario under MicroPython;
* the mutants of demo/sim/mutate.py;
* with ASSEMBLER=path/to/assemble.exe, that demo/firmware is what
  demo/firmware/build.py generates.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
REPO = DEMO.parent
sys.path.insert(0, str(DEMO))
sys.path.insert(0, str(REPO / "host"))
sys.path.insert(0, str(REPO / "host" / "tools"))

import pe_demo  # noqa: E402
from pe_host import ProtocolEmulator  # noqa: E402
from pe_host.peers import Environment, I2CTarget, PadCapture, UartMonitor, UartSource  # noqa: E402
from pe_host.ports.model import ModelPort  # noqa: E402

FIRMWARE = [str(DEMO / "firmware"), str(REPO / "firmware")]


def spi_flash_class():
    """SpiFlash from demo/sim/test_demo.py, without importing cocotb."""
    src = (DEMO / "sim" / "test_demo.py").read_text()
    start = src.index("class SpiFlash:")
    end = src.index("@cocotb.test()", start)
    ns = {}
    exec(src[start:end], ns)  # noqa: S102 - our own source
    return ns["SpiFlash"]


def probe_edges(capture):
    s = capture.samples
    out = []
    for i in range(1, len(s)):
        d = s[i] ^ s[i - 1]
        for pin in (6, 7):
            if d >> pin & 1:
                out.append((capture.first + i, pin, s[i] >> pin & 1))
    return out


def run_isolation(mode, cycles, seed=1, record=False):
    cap = PadCapture(limit=10 ** 7)
    port = ModelPort(env=Environment([pe_demo.Jumpers(), cap]), record_replay=record)
    chip = pe_demo.LibChip(ProtocolEmulator(port, timeout=200000))
    images = pe_demo.lib_images(FIRMWARE, pe_demo.ISOLATION_IMAGES)
    result = pe_demo.isolation(chip, images, mode, cycles, seed=seed, log=lambda s: None)
    return result, probe_edges(cap), port


class IsolationOnModel(unittest.TestCase):
    def test_idle_and_loaded_probe_edges_identical(self):
        idle, e_idle, _ = run_isolation("idle", 12000)
        loaded, e_loaded, _ = run_isolation("loaded", 12000, seed=5)
        self.assertTrue(idle["probe_running"] and loaded["probe_running"])
        self.assertGreater(loaded["host_operations"], 300)
        self.assertEqual(loaded["stray_loop_bytes"], [])
        self.assertEqual(loaded["engine_restarts"], 0)
        rel = lambda edges: [(c - edges[0][0], p, v) for c, p, v in edges]  # noqa: E731
        n = min(len(e_idle), len(e_loaded))
        self.assertGreater(n, 1500)
        self.assertEqual(rel(e_idle)[:n], rel(e_loaded)[:n])
        # Same START cycle in both modes (identical host sequence before START).
        self.assertEqual(idle["probe_start_cycle"], loaded["probe_start_cycle"])
        self.assertEqual(e_idle[0][0], e_loaded[0][0])

    def test_hammer_never_touches_the_probe_controls(self):
        """The hammer issues no START/STOP/CLEAR with the probe's bit and no
        BEGIN/COMMIT/OWN while the probe is selected (the formal environment)."""
        seen = []

        class Spy(pe_demo.LibChip):
            def command(self, op, payload=0):
                seen.append((op, payload, self.pe.selected))
                return pe_demo.LibChip.command(self, op, payload)

        port = ModelPort(env=Environment([pe_demo.Jumpers()]))
        chip = Spy(ProtocolEmulator(port, timeout=200000))
        images = pe_demo.lib_images(FIRMWARE, pe_demo.ISOLATION_IMAGES)
        pe_demo.isolation_setup(chip, images, log=lambda s: None)
        seen.clear()
        chip.command(pe_demo.START, pe_demo.PROBE_MASK)
        seen.clear()
        hammer = pe_demo.Hammer(chip, 9)
        for _ in range(3000):
            hammer.step()
        for op, payload, selected in seen:
            if op in (pe_demo.START, pe_demo.STOP):
                self.assertFalse(payload & pe_demo.PROBE_MASK, (op, payload))
            if op == pe_demo.CLEAR:
                self.assertFalse(payload & pe_demo.PROBE_MASK, (op, payload))
            if op in (pe_demo.BEGIN, pe_demo.COMMIT, pe_demo.OWN):
                self.assertNotEqual(selected, pe_demo.PROBE_ENGINE)
        self.assertEqual(set(hammer.counts), set(pe_demo.Hammer.OPS))


class RealPartScenariosOnModel(unittest.TestCase):
    def test_four_protocols(self):
        names = ("uart-tx-115200", "uart-rx-poll-115200", "spi-xfer32", "i2c-read-100k")
        images = pe_demo.lib_images(FIRMWARE, names)
        bit = 434
        sent = [0x68, 0x69, 0x0D, 0x0A]
        source = UartSource(1, sent, bit_cycles=bit, gap_cycles=bit, start=None)
        monitor = UartMonitor(0, bit_cycles=bit)
        flash = spi_flash_class()()
        i2c = I2CTarget(address=0x48, read_bytes=(0x19,))
        port = ModelPort(env=Environment([source, monitor, flash, i2c], pullups=0xFF))
        chip = pe_demo.LibChip(ProtocolEmulator(port, timeout=200000))
        pe_demo.four_setup(chip, images, log=lambda s: None)
        source.start = chip.now() + 200
        pe_demo.four_round(chip, b"OK", pe_demo.TMP_ADDRESS)
        spi, rd = pe_demo.four_collect(chip, 1, 1, 150000)
        chip.wait(12 * bit * 10)
        self.assertEqual(pe_demo.jedec_id(spi[0]), (0xEF, 0x40, 0x18))
        self.assertEqual(rd[0] & 0xFF, 0x19)
        self.assertEqual(monitor.words, [ord("O"), ord("K")] + sent)
        self.assertEqual(monitor.errors, [])
        self.assertEqual(flash.commands, [0x9F])
        for s in pe_demo.bridge_status(chip):
            self.assertTrue(s["running"] and not s["fault"], s)

    def test_host_free_chain(self):
        names = ("uart-tx-115200", "read-ticker-sim", "hex-formatter", "i2c-read-100k")
        images = pe_demo.lib_images(FIRMWARE, names)
        readings = (0x19, 0x1A, 0xF6)
        i2c = I2CTarget(address=0x48, read_bytes=readings)
        monitor = UartMonitor(0, bit_cycles=434)
        port = ModelPort(env=Environment([monitor, i2c], pullups=0xFF))
        chip = pe_demo.LibChip(ProtocolEmulator(port, timeout=200000))
        pe_demo.bridge_setup(chip, images, log=lambda s: None)
        chip.wait(130000)
        self.assertEqual(bytes(monitor.words), b"19\r\n1A\r\nF6\r\n")
        self.assertEqual(pe_demo.parse_hex_lines(bytes(monitor.words)), list(readings))
        self.assertTrue(all(r == 0x91 for r in i2c.received))


class MicroPythonCompatibility(unittest.TestCase):
    def test_subset(self):
        import upy_check
        problems = [p for p in upy_check.check([str(DEMO / "pe_demo.py"), str(DEMO / "pico_demo.py")])
                    if "pe_demo is not on the MicroPython allow-list" not in p]
        self.assertEqual(problems, [])

    def test_differential_replay(self):
        binary = os.environ.get("MICROPYTHON") or shutil.which("micropython")
        if not binary:
            self.skipTest("no micropython binary (set MICROPYTHON)")
        result, _, port = run_isolation("loaded", 4000, seed=3, record=True)
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.bin"
            trace.write_bytes(bytes(port.replay))
            proc = subprocess.run([binary, str(DEMO / "tests" / "upy_replay.py"), str(REPO / "host"), str(DEMO),
                                   str(trace), "loaded", "4000", "3"] + FIRMWARE,
                                  capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = proc.stdout.strip().splitlines()[-1]
        self.assertEqual(line, "REPLAY_OK %d %s" % (port.count, pe_demo.digest(result)))


class Mutants(unittest.TestCase):
    def mutate(self, name):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "core.v"
            subprocess.run([sys.executable, str(DEMO / "sim" / "mutate.py"), name, "3",
                            str(REPO / "src" / "protocol_emulator_core.v"), str(out)],
                           check=True, capture_output=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            return out.read_text()

    def test_each_mutant_is_one_line(self):
        base = (REPO / "src" / "protocol_emulator_core.v").read_text().splitlines()
        for name in ("host-fetch", "engine-fetch", "timer-host", "timer-engine"):
            lines = self.mutate(name).splitlines()
            self.assertEqual(len(lines), len(base))
            diff = [(a, b) for a, b in zip(base, lines) if a != b]
            self.assertEqual(len(diff), 1, name)

    def test_timer_register_attribution_is_one_to_one(self):
        sys.path.insert(0, str(DEMO / "sim"))
        import mutate
        rtl = (REPO / "src" / "protocol_emulator_core.v").read_text()
        regs = {mutate.engine_register(rtl, "wait_timer", k) for k in range(4)}
        self.assertEqual(len(regs), 4)


class FirmwareBuild(unittest.TestCase):
    def test_images_match_the_generator(self):
        assembler = os.environ.get("ASSEMBLER")
        if not assembler:
            self.skipTest("set ASSEMBLER to hardcaml's assemble.exe")
        proc = subprocess.run([sys.executable, str(DEMO / "firmware" / "build.py"), "--assembler", assembler,
                               "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_image_identities(self):
        from pe_host import FirmwareImage
        for path in sorted((DEMO / "firmware").glob("*.image.json")):
            image = FirmwareImage.load(str(path), source="required")
            data = json.loads(path.read_text())
            self.assertEqual(image.name + ".image.json", path.name)
            self.assertEqual(data["isa_version"], 2)


if __name__ == "__main__":
    unittest.main()
