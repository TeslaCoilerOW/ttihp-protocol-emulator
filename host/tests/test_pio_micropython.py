# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The lockstep port under MicroPython.

* Static: tools/upy_check.py over pe_host/ports/pio_lockstep.py (also in
  upy_check's default module list, with MicroPython's ``array`` module for
  the StateMachine.put bursts) and pio/upy_replay.py. The only message
  allowed is the replay's import of ``rp2_real``: MicroPython's own rp2.py,
  which test_assembled_by_micropython_rp2 copies in under that name.
* Dynamic (MicroPython unix port: $PE_HOST_MICROPYTHON or on PATH):
  differential replay, as tests/test_host_micropython.py does for the
  host-clocked ports. CPython runs the self-test and the flagship scenario
  (peers="external") on the co-simulated board with every hardware call of
  the driver logged (pio/replay.py); the unchanged library and driver then run
  under MicroPython (pio/upy_replay.py) against the log, from source and
  precompiled with mpy-cross, and must make the same calls and reach the same
  verdict. With $PE_HOST_RP2_PY_DIR (MicroPython's rp2.py files, see
  test_pio_asm.py) the program is also assembled under MicroPython by
  MicroPython's own asm_pio.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import support
from pe_host import selftest
from pe_host.flagship import FlagshipPeers, run_flagship
from pe_host.image import load_scenario
from pe_host.peers import Environment
from pio.board import Board, ModelChip
from pio.replay import Recorder, recorded_lockstep

sys.path.insert(0, str(support.HOST / "tools"))
import upy_check  # noqa: E402

MICROPYTHON = support.micropython_binary()
MPY_CROSS = os.environ.get("PE_HOST_MPY_CROSS") or shutil.which("mpy-cross")
SCENARIO = str(support.SCENARIO)
REPLAY = str(support.HOST / "pio" / "upy_replay.py")


class StaticSubset(unittest.TestCase):
    def test_driver_uses_the_subset(self):
        paths = [str(support.HOST / "pe_host" / "ports" / "pio_lockstep.py"), REPLAY]
        self.assertIn("pe_host/ports/pio_lockstep.py", upy_check.MICROPYTHON_MODULES)
        problems = [p for p in upy_check.check(paths) if "module rp2_real" not in p]
        self.assertEqual(problems, [])


def verdict(kind, result):
    if kind == "selftest":
        return "%s %d %s" % (result.passed, len(result.checks),
                             ",".join(c[0] + "/" + c[1] for c in result.failures))
    return "%s host_fault=%s faults=%s" % (
        result.passed, result.host_fault,
        ",".join("%d:%d" % (s.engine, s.fault) for s in result.fault_report.faulted))


def record(kind, path):
    rec = Recorder()
    env = None
    if kind == "external":
        peers = FlagshipPeers(load_scenario(SCENARIO))
        env = Environment([peers.spi, peers.i2c], pullups=0xFF)
    board = Board(ModelChip(env=env), put_cycles=450, get_cycles=450)
    pe = recorded_lockstep(board, rec)
    if kind == "selftest":
        result = selftest.run(pe)
    else:
        result = run_flagship(pe, SCENARIO, peers="external", free_run_cycles=3000)
    rec.dump(path, {"kind": kind})
    return verdict(kind, result), len(rec.log), board


@unittest.skipUnless(MICROPYTHON, "no MicroPython unix port ($PE_HOST_MICROPYTHON)")
class MicroPythonReplay(unittest.TestCase):
    KINDS = ("selftest", "external")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.recordings = {}
        for kind in cls.KINDS:
            path = os.path.join(cls.tmp, kind + ".json")
            cls.recordings[kind] = (path,) + record(kind, path)[:2]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def replay(self, host_dir, kind, rp2_dir=None):
        path, want, calls = self.recordings[kind]
        cmd = [MICROPYTHON, REPLAY, str(host_dir), path, kind, SCENARIO]
        if rp2_dir:
            cmd.append(rp2_dir)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        first = proc.stdout.splitlines()[0]
        self.assertEqual(first, "REPLAY_OK %s %d %s" % (kind, calls, want))
        return proc.stdout

    def test_recorded_verdicts(self):
        self.assertTrue(self.recordings["external"][1].startswith("True host_fault=False"))
        passed, checks, failed = self.recordings["selftest"][1].split(" ", 2)
        self.assertEqual(checks, "73")
        self.assertIn(failed, ("", "identity/timestamp advances once per host clock"))

    def test_source_modules(self):
        for kind in self.KINDS:
            out = self.replay(support.HOST, kind)
            self.assertIn("pe_host/host.py", out)

    @unittest.skipUnless(MPY_CROSS, "no mpy-cross ($PE_HOST_MPY_CROSS)")
    def test_mpy_cross_compiled_modules(self):
        package = tempfile.mkdtemp()
        try:
            names = [n for n in upy_check.MICROPYTHON_MODULES if n.startswith("pe_host/")]
            for name in names:
                target = os.path.join(package, name.replace(".py", ".mpy"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                subprocess.run([MPY_CROSS, "-o", target, str(support.HOST / name)], check=True,
                               capture_output=True, text=True)
            for kind in self.KINDS:
                out = self.replay(package, kind)
                self.assertIn("pe_host/host.mpy", out)
        finally:
            shutil.rmtree(package, ignore_errors=True)

    @unittest.skipUnless(os.environ.get("PE_HOST_RP2_PY_DIR"), "PE_HOST_RP2_PY_DIR not set")
    def test_assembled_by_micropython_rp2(self):
        """MicroPython's own rp2.asm_pio (latest file in PE_HOST_RP2_PY_DIR)
        assembles lockstep_program under MicroPython to the recorded words."""
        import glob
        files = sorted(glob.glob(os.path.join(os.environ["PE_HOST_RP2_PY_DIR"], "rp2_*.py")))
        rp2_dir = tempfile.mkdtemp()
        try:
            with open(os.path.join(rp2_dir, "_rp2.py"), "w") as handle:
                handle.write("class PIO:\n    SHIFT_LEFT = 0\n    SHIFT_RIGHT = 1\n"
                             "    JOIN_NONE = 0\n    JOIN_TX = 1\n    JOIN_RX = 2\n"
                             "    OUT_LOW = 2\n    OUT_HIGH = 3\n    IN_LOW = 0\n    IN_HIGH = 1\n")
            for path in files:
                shutil.copy(path, os.path.join(rp2_dir, "rp2_real.py"))
                out = self.replay(support.HOST, "external", rp2_dir)
                self.assertIn("ASSEMBLED_BY rp2.py", out, path)
                for stale in glob.glob(os.path.join(rp2_dir, "*.mpy")):
                    os.remove(stale)
        finally:
            shutil.rmtree(rp2_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
