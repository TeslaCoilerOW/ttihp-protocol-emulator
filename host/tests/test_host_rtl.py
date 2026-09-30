# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Replay host-library runs on the RTL with Icarus Verilog.

The exact per-cycle stimulus of a library run on the reference model (ui_in,
uio pads, reset) becomes a self-checking Verilog testbench
(test/model/host.py render_testbench) that compares uo_out (read nibble only
while read-valid is high), uio_out and uio_oe after every edge. Needs
iverilog/vvp ($PE_HOST_IVERILOG_BIN = their directory, or PATH) and the RTL
($PE_HOST_RTL_DIR = a repository checkout, default this repository).

Each known device (pe_host.protocol.DEVICES) runs against its own model and
core: src/protocol_emulator_core.v for the device of the checkout's design
selection (scripts/design_selection.sh), build/variants/<device>/
protocol_emulator_core.v (scripts/gen_variants.sh, gitignored) for the other;
a device whose core is absent is skipped.
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
COMMON = [RTL / "src" / "project.v", RTL / "models" / "RM_IHPSG13_1P_64x16_c2.v",
          RTL / "models" / "RM_IHPSG13_1P_core_behavioral.v"]
AVAILABLE = bool(IVERILOG and VVP and os.path.exists(IVERILOG)
                 and all(p.exists() for p in COMMON))
# Configurations a device's core may be generated from (its header line names one).
CONFIGS = {"base": ("configs/instruction-sram-32.json", "configs/variants/base.json")}


def core_of(device):
    if device == support.selected_device(RTL):
        return RTL / "src" / "protocol_emulator_core.v"
    return RTL / "build" / "variants" / device / "protocol_emulator_core.v"


@unittest.skipUnless(AVAILABLE, "iverilog/vvp or RTL sources not available")
class RtlReplayTest(unittest.TestCase):
    def replay(self, device, name, action):
        core = core_of(device)
        if not core.exists():
            self.skipTest("no %s core at %s (scripts/gen_variants.sh %s)" % (device, core, device))
        with open(core) as handle:
            header = handle.readline()
        configs = CONFIGS.get(device, ("configs/variants/%s.json" % device,))
        self.assertTrue(any("hardcaml/ + %s -- " % c in header for c in configs),
                        "%s does not name %s: %s" % (core, " or ".join(configs), header))
        port = ModelPort(record=True, device=device)
        pe = ProtocolEmulator(port, device=device)
        outcome = action(pe)
        tmp = Path(tempfile.mkdtemp(prefix="pe_host_rtl_"))
        tb = tmp / (name + "_tb.v")
        tb.write_text(port.render_testbench())
        sim = tmp / (name + ".vvp")
        sources = [COMMON[0], core] + COMMON[1:]
        subprocess.run([IVERILOG, "-g2012", "-DFUNCTIONAL", "-o", str(sim), "-s", "tb", str(tb)]
                       + [str(p) for p in sources], check=True, capture_output=True, text=True,
                       timeout=1800)
        run = subprocess.run([VVP, "-n", str(sim)], capture_output=True, text=True, timeout=1800,
                             cwd=str(tmp))
        self.assertIn("DIFFERENTIAL_OK", run.stdout, run.stdout[-3000:] + run.stderr[-3000:])
        shutil.rmtree(tmp, ignore_errors=True)
        return outcome, port.count

    def selftest_on(self, device):
        result, cycles = self.replay(device, "selftest_" + device, lambda pe: selftest.run(pe))
        self.assertTrue(result.passed, result.summary())
        self.assertGreater(cycles, 3000)

    def flagship_on(self, device):
        passed, cycles = self.replay(
            device, "flagship_" + device,
            lambda pe: run_flagship(pe, str(support.SCENARIO)).passed)
        self.assertTrue(passed)
        self.assertGreater(cycles, 7000)

    def test_selftest_stimulus_on_base_rtl(self):
        self.selftest_on("base")

    def test_flagship_stimulus_on_base_rtl(self):
        self.flagship_on("base")

    def test_selftest_stimulus_on_diet8_rec16_rtl(self):
        """Includes the line_unit section on all four engines and the masked
        READ_SELECT 7 word 0x000F5F03."""
        self.selftest_on("diet8_rec16")

    def test_flagship_stimulus_on_diet8_rec16_rtl(self):
        self.flagship_on("diet8_rec16")


if __name__ == "__main__":
    unittest.main()
