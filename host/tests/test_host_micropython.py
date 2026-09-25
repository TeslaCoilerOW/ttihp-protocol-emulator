# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""MicroPython compatibility.

* Static: tools/upy_check.py over every module that must run on MicroPython.
* Dynamic (when a MicroPython unix port is available: $PE_HOST_MICROPYTHON or
  `micropython` on PATH): differential replay. CPython records the self-test
  and the flagship scenario (software peers) on the reference model; the
  unchanged library then runs under MicroPython on ReplayPort, which checks
  every cycle's ui byte and peer drive against the recording. Also repeated
  with the modules precompiled by mpy-cross ($PE_HOST_MPY_CROSS or on PATH),
  the form deployed to a board.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support
from pe_host import ProtocolEmulator, selftest
from pe_host.flagship import run_flagship
from pe_host.ports.model import ModelPort

sys.path.insert(0, str(support.HOST / "tools"))
import upy_check  # noqa: E402

MICROPYTHON = support.micropython_binary()
MPY_CROSS = os.environ.get("PE_HOST_MPY_CROSS") or shutil.which("mpy-cross")


class StaticSubsetTest(unittest.TestCase):
    def test_micropython_modules_use_the_subset(self):
        paths = [str(support.HOST / name) for name in upy_check.MICROPYTHON_MODULES]
        self.assertEqual(upy_check.check(paths), [])

    def test_pure_python_sha256(self):
        import hashlib
        import os
        from pe_host import image
        for n in list(range(0, 130)) + [1000, 4097]:
            data = os.urandom(n)
            self.assertEqual(image.sha256_py(data), hashlib.sha256(data).digest())

    def test_checker_catches_cpython_only_code(self):
        bad = support.HOST / "pe_host" / "ports" / "model.py"
        problems = upy_check.check([str(bad)])
        self.assertTrue(any("pathlib" in p for p in problems), problems)


EXTERNAL_FREE_RUN = 3000


def external_verdict(result):
    """Verdict line for peers="external" (shared with upy/replay_main.py)."""
    return "%s host_fault=%s faults=%s" % (
        result.passed, result.host_fault,
        ",".join("%d:%d" % (s.engine, s.fault) for s in result.fault_report.faulted))


def record(kind):
    env = None
    if kind == "external":
        # SPI and I2C targets on the pads, UART line idle: engine 1's expected
        # fault 3 is cleared after STOP to read the host fault.
        from pe_host.flagship import FlagshipPeers
        from pe_host.image import load_scenario
        from pe_host.peers import Environment
        peers = FlagshipPeers(load_scenario(str(support.SCENARIO)))
        env = Environment([peers.spi, peers.i2c], pullups=0xFF)
    port = ModelPort(env=env, record_replay=True)
    pe = ProtocolEmulator(port)
    if kind == "selftest":
        result = selftest.run(pe)
        verdict = "%s %d" % (result.passed, len(result.checks))
    elif kind == "external":
        result = run_flagship(pe, str(support.SCENARIO), peers="external",
                              free_run_cycles=EXTERNAL_FREE_RUN)
        verdict = external_verdict(result)
    else:
        stretch = int(kind.split(":")[1]) if ":" in kind else 0
        result = run_flagship(pe, str(support.SCENARIO), i2c_stretch=stretch)
        verdict = "%s %s" % (result.passed, " ".join("%02x" % w for w in result.engine2_rx_words))
    return bytes(port.replay), port.count, verdict


def compile_package(tmp):
    """mpy-cross every MicroPython module into tmp/pe_host (the deployed form)."""
    for name in upy_check.MICROPYTHON_MODULES:
        if not name.startswith("pe_host/"):
            continue
        source = support.HOST / name
        target = Path(tmp) / name.replace(".py", ".mpy")
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([MPY_CROSS, "-o", str(target), str(source)], check=True,
                       capture_output=True, text=True)
    return tmp


@unittest.skipUnless(MICROPYTHON, "no MicroPython unix port ($PE_HOST_MICROPYTHON)")
class MicroPythonReplayTest(unittest.TestCase):
    KINDS = ("selftest", "flagship", "flagship:40", "external")

    def test_external_recording_passes(self):
        self.assertEqual(self.recordings["external"][2], "True host_fault=False faults=1:3")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.recordings = {}
        for kind in cls.KINDS:
            data, cycles, verdict = record(kind)
            path = os.path.join(cls.tmp, kind.replace(":", "_") + ".trace")
            Path(path).write_bytes(data)
            cls.recordings[kind] = (path, cycles, verdict)

    def replay(self, host_dir, kind):
        path, cycles, verdict = self.recordings[kind]
        proc = subprocess.run([MICROPYTHON, str(support.HOST / "tests" / "upy" / "replay_main.py"),
                               str(host_dir), kind, path, str(support.SCENARIO)],
                              capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        first = proc.stdout.splitlines()[0]
        self.assertEqual(first, "REPLAY_OK %s %d %s" % (kind, cycles, verdict), proc.stdout)
        return proc.stdout

    def test_source_modules(self):
        for kind in self.KINDS:
            out = self.replay(support.HOST, kind)
            self.assertIn("pe_host/host.py", out)

    def test_sha256_fallback_on_micropython(self):
        names = ["uart-tx", "i2c-write", "spi-controller-mode0"]
        proc = subprocess.run([MICROPYTHON, str(support.HOST / "tests" / "upy" / "sha_check.py"),
                               str(support.HOST), str(support.FIRMWARE)] + names,
                              capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.stdout.strip(), "SHA_OK 3", proc.stdout + proc.stderr)

    @unittest.skipUnless(MPY_CROSS, "no mpy-cross ($PE_HOST_MPY_CROSS)")
    def test_mpy_cross_compiled_modules(self):
        package = compile_package(tempfile.mkdtemp())
        for kind in self.KINDS:
            out = self.replay(package, kind)
            self.assertIn("pe_host/host.mpy", out)


if __name__ == "__main__":
    unittest.main()
