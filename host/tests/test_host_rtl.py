# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Replay host-library runs on the RTL with Icarus Verilog.

The exact per-cycle stimulus of a library run on the reference model (ui_in,
uio pads, reset) becomes a self-checking Verilog testbench
(test/model/host.py render_testbench) that compares uo_out (read nibble only
while read-valid is high), uio_out and uio_oe after every edge. Needs
iverilog/vvp ($PE_HOST_IVERILOG_BIN = their directory, or PATH) and the RTL
($PE_HOST_RTL_DIR = a repository checkout, default this repository).
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import support
from pe_host import ProtocolEmulator, selftest
from pe_host.flagship import run_flagship
from pe_host.ports.model import ModelPort

BIN = os.environ.get("PE_HOST_IVERILOG_BIN")
IVERILOG = os.path.join(BIN, "iverilog") if BIN else shutil.which("iverilog")
VVP = os.path.join(BIN, "vvp") if BIN else shutil.which("vvp")
RTL = Path(os.environ.get("PE_HOST_RTL_DIR") or support.REPO)
SOURCES = [RTL / "src" / "project.v", RTL / "src" / "protocol_emulator_core.v",
           RTL / "models" / "RM_IHPSG13_1P_64x16_c2.v",
           RTL / "models" / "RM_IHPSG13_1P_core_behavioral.v"]
AVAILABLE = bool(IVERILOG and VVP and os.path.exists(IVERILOG)
                 and all(p.exists() for p in SOURCES))


@unittest.skipUnless(AVAILABLE, "iverilog/vvp or RTL sources not available")
class RtlReplayTest(unittest.TestCase):
    def replay(self, name, action):
        port = ModelPort(record=True)
        pe = ProtocolEmulator(port)
        outcome = action(pe)
        tmp = Path(tempfile.mkdtemp(prefix="pe_host_rtl_"))
        tb = tmp / (name + "_tb.v")
        tb.write_text(port.render_testbench())
        sim = tmp / (name + ".vvp")
        subprocess.run([IVERILOG, "-g2012", "-DFUNCTIONAL", "-o", str(sim), "-s", "tb", str(tb)]
                       + [str(p) for p in SOURCES], check=True, capture_output=True, text=True,
                       timeout=1800)
        run = subprocess.run([VVP, "-n", str(sim)], capture_output=True, text=True, timeout=1800,
                             cwd=str(tmp))
        self.assertIn("DIFFERENTIAL_OK", run.stdout, run.stdout[-3000:] + run.stderr[-3000:])
        shutil.rmtree(tmp, ignore_errors=True)
        return outcome, port.count

    def test_selftest_stimulus_on_rtl(self):
        passed, cycles = self.replay("selftest", lambda pe: selftest.run(pe).passed)
        self.assertTrue(passed)
        self.assertGreater(cycles, 3000)

    def test_flagship_stimulus_on_rtl(self):
        passed, cycles = self.replay(
            "flagship", lambda pe: run_flagship(pe, str(support.SCENARIO)).passed)
        self.assertTrue(passed)
        self.assertGreater(cycles, 7000)


if __name__ == "__main__":
    unittest.main()
