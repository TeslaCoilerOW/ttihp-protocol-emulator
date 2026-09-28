#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Checks of the Vivado flow scripts that need no Vivado installation.

    python3 fpga/vivado/test_vivado_flow.py -v

* build.tcl under tclsh with every Vivado command replaced by a stub that
  logs its arguments: argument handling, the source list (the same as
  fpga/scripts/build.sh), part, top, Verilog defines, the clock XDC in
  CLOCKS=shared and CLOCKS=derived mode, and the verdict logic. Skipped when
  tclsh is not installed.
* timing_check.py on report excerpts written in the layout of Vivado's
  report_timing_summary / report_utilization / report_methodology
  (constructed for this test after the reports of the Vivado 2025.2 runs of
  docs/fpga.md; not copies of them).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FPGA = HERE.parent
sys.path.insert(0, str(HERE))

import timing_check  # noqa: E402

STUBS = r"""
set ::log [open $::env(STUB_LOG) w]
proc stub {name args} { puts $::log "$name $args"; flush $::log }
foreach c {read_verilog read_xdc write_checkpoint report_utilization opt_design place_design
           phys_opt_design route_design report_clocks write_bitstream} {
  proc $c {args} "stub $c \$args"
}
proc stub_rules {name args sev} {
  stub $name $args
  set fh [open [lindex $args [expr {[lsearch $args -file] + 1}]] w]
  puts $fh "| Rule      | Severity         | Description                      | Violations |"
  puts $fh "| LUTAR-1   | Warning          | LUT drives async reset alert     | 1          |"
  if {$sev ne ""} { puts $fh "| TIMING-4  | $sev | Invalid primary clock redefinition on a clock tree | 1      |" }
  close $fh
}
proc report_methodology {args} { stub_rules report_methodology $args $::env(STUB_METHOD) }
proc report_drc {args} { stub_rules report_drc $args $::env(STUB_DRC) }
proc report_timing_summary {args} {
  stub report_timing_summary $args
  set f [lindex $args [expr {[lsearch $args -file] + 1}]]
  set fh [open $f w]
  puts $fh "1. checking no_clock ($::env(STUB_NOCLOCK))"
  puts $fh "4. checking unconstrained_internal_endpoints (0)"
  puts $fh "5. checking no_input_delay (14)"
  close $fh
}
proc synth_design {args} { stub synth_design $args }
proc get_timing_paths {args} {
  if {[lsearch $args -slack_lesser_than] >= 0} {
    if {$::env(STUB_SLACK) < 0} { return [list p1] }
    return {}
  }
  if {[lsearch $args min] >= 0} { return [list hold] }
  return [list setup]
}
proc get_property {prop obj} {
  if {$obj eq "hold"} { return 0.05 }
  return $::env(STUB_SLACK)
}
set ::argv $::env(STUB_ARGS)
set ::argc [llength $::argv]
source $::env(STUB_SCRIPT)
"""


def build_sources(board="cmod_a7"):
    """The RTL list of fpga/scripts/build.sh."""
    text = (FPGA / "scripts" / "build.sh").read_text()
    block = text[text.index("sources=(") :text.index(")", text.index("sources=("))]
    return [Path(m.replace("${board}", board)).name for m in re.findall(r'"\$(?:repo|fpga)/([^"]+)"', block)]


@unittest.skipUnless(shutil.which("tclsh"), "tclsh not installed")
class BuildTcl(unittest.TestCase):
    def run_tcl(self, args, slack="1.5", no_clock="0", method="", drc=""):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "stub.tcl"
            stub.write_text(STUBS)
            log = Path(tmp) / "log.txt"
            out = Path(tmp) / "out"
            env = {"STUB_LOG": str(log), "STUB_SCRIPT": str(HERE / "build.tcl"), "STUB_SLACK": slack,
                   "STUB_NOCLOCK": no_clock, "STUB_METHOD": method, "STUB_DRC": drc,
                   "STUB_ARGS": " ".join(a.replace("OUT", str(out)) for a in args), "PATH": "/usr/bin:/bin"}
            env["PATH"] = str(Path(shutil.which("tclsh")).parent) + ":" + env["PATH"]
            p = subprocess.run(["tclsh", str(stub)], env=env, capture_output=True, text=True, timeout=60)
            calls = log.read_text() if log.exists() else ""
            files = {f.name: f.read_text() for f in out.glob("*") if f.is_file()} if out.exists() else {}
            return p, calls, files

    def test_cmod_pll50_shared(self):
        p, calls, files = self.run_tcl(["cmod_a7", "pll50", "OUT", "CLOCKS=shared"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("synth_design {-top pe_top_cmod_a7 -part xc7a35tcpg236-1}", calls)   # no empty define list
        srcs = [Path(l).name for l in files["sources.txt"].splitlines() if l.startswith("/")]
        self.assertEqual(srcs[:-2], build_sources())
        self.assertEqual(srcs[-2:], ["cmod_a7_35t.xdc", "clock_cmod_a7_pll50.xdc"])
        self.assertEqual(files["clock.xdc"], (FPGA / "constraints" / "clock_cmod_a7_pll50.xdc").read_text())
        self.assertIn("CFGBVS VCCO", files["vivado_only.xdc"])
        self.assertIn("VIVADO TIMING PASS", files["vivado_timing.txt"])
        self.assertIn("write_bitstream {-force", calls)
        self.assertTrue(calls.index("read_xdc") < calls.index("synth_design"))

    def test_default_is_derived(self):
        p, calls, files = self.run_tcl(["urbana", "pll50", "OUT"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("# (CLOCKS=derived) create_clock -period 20.000 [get_nets { clk }]", files["clock.xdc"])
        self.assertEqual(self.run_tcl(["urbana", "pll50", "OUT", "CLOCKS=netonly"])[0].returncode, 2)

    def test_methodology_and_drc_fail(self):
        p, calls, files = self.run_tcl(["cmod_a7", "pll50", "OUT"], method="Critical Warning")
        self.assertEqual(p.returncode, 1)
        self.assertIn("methodology TIMING-4 (Critical Warning", files["vivado_timing.txt"])
        self.assertNotIn("write_bitstream", calls)
        p, calls, files = self.run_tcl(["cmod_a7", "pll50", "OUT"], drc="Error")
        self.assertEqual(p.returncode, 1)
        self.assertIn("drc TIMING-4 (Error", files["vivado_timing.txt"])
        p, calls, files = self.run_tcl(["cmod_a7", "pll50", "OUT"], method="Warning")
        self.assertEqual(p.returncode, 0, files.get("vivado_timing.txt"))

    def test_derived_clocks_and_defines(self):
        p, calls, files = self.run_tcl(["cmod_a7", "osc12", "OUT", "CLOCKS=derived", "BRIDGE_ONLY=1",
                                        "DEFINES=PE_SCOPE_AW=13,PE_X", "BITSTREAM=0"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("-verilog_define {PE_CLOCK_OSC PE_NO_PIN_HOST PE_SCOPE_AW=13 PE_X}", calls)
        clk = files["clock.xdc"]
        self.assertIn("create_clock -period 83.333 [get_ports { sysclk }]", clk)
        self.assertNotIn("\ncreate_clock -period 83.333 [get_nets", clk)
        self.assertIn("# (CLOCKS=derived) create_clock", clk)
        self.assertNotIn("write_bitstream", calls)

    def test_urbana_and_errors(self):
        p, calls, files = self.run_tcl(["urbana", "host", "OUT"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("-top pe_top_urbana -part xc7s50csga324-1 -verilog_define PE_CLOCK_HOST", calls)
        self.assertEqual(self.run_tcl(["urbana", "osc12", "OUT"])[0].returncode, 2)
        self.assertEqual(self.run_tcl(["cmod_a7", "host", "OUT", "BRIDGE_ONLY=1"])[0].returncode, 2)
        self.assertEqual(self.run_tcl(["cmod_a7", "pll50", "OUT", "NOPE=1"])[0].returncode, 2)

    def test_negative_slack_fails(self):
        p, calls, files = self.run_tcl(["cmod_a7", "pll50", "OUT"], slack="-0.25")
        self.assertEqual(p.returncode, 1)
        self.assertIn("VIVADO TIMING FAIL", files["vivado_timing.txt"])
        self.assertNotIn("write_bitstream", calls)                        # FAIL: no bitstream
        p, calls, files = self.run_tcl(["cmod_a7", "pll50", "OUT", "BITSTREAM=always"], slack="-0.25")
        self.assertEqual(p.returncode, 1)
        self.assertIn("write_bitstream {-force", calls)

    def test_check_timing_fails(self):
        p, calls, files = self.run_tcl(["urbana", "pll50", "OUT"], no_clock="3")
        self.assertEqual(p.returncode, 1)
        self.assertIn("check_timing no_clock 3", files["vivado_timing.txt"])
        self.assertNotIn("write_bitstream", calls)

    def test_scope_depth_limit(self):
        self.assertEqual(self.run_tcl(["cmod_a7", "pll50", "OUT", "DEFINES=PE_SCOPE_AW=16"])[0].returncode, 2)
        self.assertEqual(self.run_tcl(["cmod_a7", "pll50", "OUT", "BITSTREAM=yes"])[0].returncode, 2)
        p, calls, _ = self.run_tcl(["cmod_a7", "pll50", "OUT", "DEFINES=PE_SCOPE_AW=15"])
        self.assertEqual(p.returncode, 0)
        self.assertIn("-verilog_define PE_SCOPE_AW=15", calls)


@unittest.skipUnless(shutil.which("bash"), "bash not installed")
class BuildShDepthCheck(unittest.TestCase):
    def test_rejects_depth_above_15(self):
        """fpga/scripts/build.sh stops before synthesis for PE_SCOPE_AW > 15."""
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": "/usr/bin:/bin", "OPENXC7": tmp, "VERILOG_DEFINES": "-DPE_SCOPE_AW=16"}
            p = subprocess.run(["bash", str(FPGA / "scripts" / "build.sh"), "cmod_a7", "pll50", tmp + "/b"],
                               env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 2, p.stderr)
            self.assertIn("supports 2..15", p.stderr)
            self.assertFalse((Path(tmp) / "b").exists())


SUMMARY = """
------------------------------------------------------------------------------------------------
| Design Timing Summary
| ---------------------
------------------------------------------------------------------------------------------------

    WNS(ns)      TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints      WHS(ns)      THS(ns)  THS Failing Endpoints  THS Total Endpoints     WPWS(ns)     TPWS(ns)  TPWS Failing Endpoints  TPWS Total Endpoints
    -------      -------  ---------------------  -------------------      -------      -------  ---------------------  -------------------     --------     --------  ----------------------  --------------------
{row}

{met}
"""
CHECKS = """
check_timing report

Table of Contents
-----------------
1. checking no_clock ({no_clock})
2. checking constant_clock (0)
3. checking pulse_width_clock (0)
4. checking unconstrained_internal_endpoints (0)
5. checking no_input_delay (14)
6. checking no_output_delay (29)
"""
UTIL = """
+----------------------------+------+-------+------------+-----------+-------+
|          Site Type         | Used | Fixed | Prohibited | Available | Util% |
+----------------------------+------+-------+------------+-----------+-------+
| Slice LUTs*                | 9120 |     0 |          0 |     20800 | 43.85 |
|   LUT as Logic             | 8700 |     0 |          0 |     20800 | 41.83 |
|   LUT as Memory            |  420 |     0 |          0 |      9600 |  4.38 |
| Slice Registers            | 4200 |     0 |          0 |     41600 | 10.10 |
+----------------------------+------+-------+------------+-----------+-------+
| Block RAM Tile    |   32 |     0 |          0 |        50 | 64.00 |
"""


UTIL_METHOD = """
+-----------+------------------+----------------------------------+------------+
| Rule      | Severity         | Description                      | Violations |
+-----------+------------------+----------------------------------+------------+
| TIMING-2  | Critical Warning | Invalid primary clock source pin | 1          |
| LUTAR-1   | Warning          | LUT drives async reset alert     | 1          |
| TIMING-18 | Warning          | Missing input or output delay    | 45         |
+-----------+------------------+----------------------------------+------------+
"""


def summary(wns, tns, whs, ths, total=4000, no_clock=0, met="All user specified timing constraints are met."):
    row = (f"{wns:>11} {tns:>12} {0 if float(tns) == 0 else 7:>22} {total:>20} {whs:>12} {ths:>12} "
           f"{0 if float(ths) == 0 else 3:>22} {total:>20} {'8.750':>12} {'0.000':>12} {0:>23} {1200:>21}")
    return SUMMARY.format(row=row, met=met) + CHECKS.format(no_clock=no_clock)


class TimingCheck(unittest.TestCase):
    def check(self, text, util=None):
        with tempfile.TemporaryDirectory() as tmp:
            rpt = Path(tmp) / "timing_summary.rpt"
            rpt.write_text(text)
            args = [str(rpt), "--json", str(Path(tmp) / "t.json")]
            if util:
                (Path(tmp) / "u.rpt").write_text(util)
                args += ["--utilization", str(Path(tmp) / "u.rpt")]
            rc = timing_check.main(args)
            import json
            return rc, json.loads((Path(tmp) / "t.json").read_text())

    def test_pass(self):
        rc, r = self.check(summary("5.432", "0.000", "0.041", "0.000"), UTIL)
        self.assertEqual(rc, 0, r)
        self.assertEqual(r["summary"]["WNS"], 5.432)
        self.assertEqual(r["check_timing"]["no_input_delay"], 14)
        self.assertEqual(r["utilization"]["Slice LUTs"]["used"], 9120)
        self.assertEqual(r["utilization"]["Block RAM Tile"]["available"], 50)
        self.assertEqual(r["utilization"]["LUT as Memory"]["used"], 420)

    def test_failures(self):
        self.assertEqual(self.check(summary("-0.120", "-3.400", "0.041", "0.000"))[0], 1)
        self.assertEqual(self.check(summary("1.000", "0.000", "-0.010", "-0.050"))[0], 1)
        rc, r = self.check(summary("1.000", "0.000", "0.041", "0.000", no_clock=4))
        self.assertEqual(rc, 1)
        self.assertIn("check_timing no_clock: 4", r["reasons"])
        rc, r = self.check(summary("inf", "0.000", "inf", "0.000", total=0))
        self.assertEqual(rc, 1)
        self.assertIn("no constrained setup endpoints", r["reasons"])

    def test_rule_tables(self):
        rows = timing_check.parse_rules(UTIL_METHOD)
        self.assertEqual([(r["id"], r["severity"], r["violations"]) for r in rows],
                         [("TIMING-2", "Critical Warning", 1), ("LUTAR-1", "Warning", 1), ("TIMING-18", "Warning", 45)])
        with tempfile.TemporaryDirectory() as tmp:
            rpt, meth = Path(tmp) / "t.rpt", Path(tmp) / "m.rpt"
            rpt.write_text(summary("1.000", "0.000", "0.041", "0.000"))
            meth.write_text(UTIL_METHOD)
            self.assertEqual(timing_check.main([str(rpt), "--methodology", str(meth)]), 1)
            self.assertEqual(timing_check.main([str(rpt)]), 0)

    def test_no_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            rpt = Path(tmp) / "x.rpt"
            rpt.write_text("nothing here\n")
            self.assertEqual(timing_check.main([str(rpt)]), 2)


if __name__ == "__main__":
    unittest.main()
