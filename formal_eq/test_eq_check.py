#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the tool-free parts of formal_eq/eq_check.py (parsers,
structural preconditions, mutations, verdict mapping, input resolution), plus
the recipe controls when yosys is available.

  python3 -m unittest formal_eq/test_eq_check.py
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eq_check as eq  # noqa: E402

TINY_LIB = r"""
/* comment */
library (tiny) {
  time_unit : "1ns";
  capacitive_load_unit (1,pf);
  cell (sg13cmos5l_buf_1) {
    area : 1;
    pin (A) { direction : input; capacitance : 0.001; }
    pin (X) { direction : output; function : "A"; }
  }
  cell (sg13cmos5l_nand2_1) {
    pin (A) { direction : input; }
    pin (B) { direction : input; }
    pin (Y) { direction : output; function : "!(A&B)"; }
  }
  cell (sg13cmos5l_nor2_1) {
    pin (A) { direction : input; }
    pin (B) { direction : input; }
    pin (Y) { direction : output; function : "!(A|B)"; }
  }
  cell (sg13cmos5l_inv_1) {
    pin (A) { direction : input; }
    pin (Y) { direction : output; function : "!A"; }
  }
  cell (sg13cmos5l_dfrbpq_1) {
    ff (IQ,IQN) { next_state : "D"; clocked_on : "CLK"; clear : "RESET_B'"; }
    pin (CLK) { direction : input; clock : true; }
    pin (D) { direction : input; }
    pin (RESET_B) { direction : input; }
    pin (Q) { direction : output; function : "IQ"; }
  }
  cell (sg13cmos5l_fill_1) { area : 1; }
  cell (sg13cmos5l_tiehi) {
    pin (L_HI) { direction : output; function : "1"; }
  }
  cell (sg13cmos5l_ebufn_2) {
    pin (A) { direction : input; }
    pin (TE_B) { direction : input; }
    pin (Z) { direction : output; function : "A"; three_state : "TE_B"; }
  }
  cell (dfneg) {
    ff (IQ,IQN) { next_state : "D"; clocked_on : "!CLK"; }
    pin (CLK) { direction : input; }
    pin (D) { direction : input; }
    pin (Q) { direction : output; function : "IQ"; }
  }
  cell (lat) {
    latch (IQ,IQN) { enable : "G"; data_in : "D"; }
    pin (G) { direction : input; }
    pin (D) { direction : input; }
    pin (Q) { direction : output; function : "IQ"; }
  }
  cell (nofunc) {
    pin (A) { direction : input; }
    pin (Y) { direction : output; }
  }
}
"""

HEADER = """module tt_um_teslacoilerow_protocol_emulator (clk,
    ena,
    rst_n,
    ui_in,
    uio_in,
    uio_oe,
    uio_out,
    uo_out);
 input clk;
 input ena;
 input rst_n;
 input [7:0] ui_in;
 input [7:0] uio_in;
 output [7:0] uio_oe;
 output [7:0] uio_out;
 output [7:0] uo_out;

 wire n1;
 wire n2;
 wire ck1;
 wire ck2;
 wire one;
 wire \\core.q[0] ;
"""

SRAM = """ RM_IHPSG13_1P_64x16_c2 \\core.mem  (.A_CLK(ck1),
    .A_REN(ui_in[2]),
    .A_WEN(ui_in[3]),
    .A_MEN(one),
    .A_DLY(one),
    .A_ADDR({ui_in[5],
    ui_in[4],
    ui_in[3],
    ui_in[2],
    ui_in[1],
    ui_in[0]}),
    .A_DIN({uio_in[7],
    uio_in[6],
    uio_in[5],
    uio_in[4],
    uio_in[3],
    uio_in[2],
    uio_in[1],
    uio_in[0],
    ui_in[7],
    ui_in[6],
    ui_in[5],
    ui_in[4],
    \\core.q[0] ,
    n1,
    ui_in[1],
    ui_in[0]}),
    .A_DOUT({d15, d14, d13, d12, d11, d10, d9, d8, d7, d6, d5, d4, d3, d2, d1, d0}));
"""

BODY = """ sg13cmos5l_fill_1 FILLER_0 ();
 sg13cmos5l_tiehi t0 (.L_HI(one));
 sg13cmos5l_buf_1 cb1 (.A(clk),
    .X(ck1));
 sg13cmos5l_buf_1 cb2 (.A(ck1),
    .X(ck2));
 sg13cmos5l_dfrbpq_1 ff0 (.CLK(ck2),
    .D(n1),
    .RESET_B(rst_n),
    .Q(\\core.q[0] ));
 sg13cmos5l_nand2_1 g1 (.A(ui_in[0]),
    .B(\\core.q[0] ),
    .Y(n1));
 sg13cmos5l_nand2_1 g2 (.A(n1),
    .B(ui_in[1]),
    .Y(n2));
 sg13cmos5l_buf_1 ob0 (.A(n2),
    .X(uo_out[0]));
"""


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="pe-eq-test-")
        self.lib_path = self.write("tiny.lib", TINY_LIB)
        self.lib = eq.parse_liberty(self.lib_path)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, name, text):
        p = os.path.join(self.d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        return p

    def netlist(self, body=BODY, sram=SRAM):
        return eq.Netlist(self.write("nl.v", HEADER + body + sram + "endmodule\n"))


class TestLiberty(Tmp):
    def test_roles(self):
        roles = {c: eq.cell_role(v) for c, v in self.lib.items()}
        self.assertEqual(roles["sg13cmos5l_buf_1"], "identity")
        self.assertEqual(roles["sg13cmos5l_nand2_1"], "comb")
        self.assertEqual(roles["sg13cmos5l_inv_1"], "comb")
        self.assertEqual(roles["sg13cmos5l_dfrbpq_1"], "flop")
        self.assertEqual(roles["sg13cmos5l_fill_1"], "physical")
        self.assertEqual(roles["sg13cmos5l_tiehi"], "comb")
        self.assertTrue(roles["sg13cmos5l_ebufn_2"].startswith("unsupported: three-state"))
        self.assertTrue(roles["dfneg"].startswith("unsupported: clocked_on"))
        self.assertTrue(roles["lat"].startswith("unsupported: latch"))
        self.assertTrue(roles["nofunc"].startswith("unsupported: output without function"))

    def test_wanted_subset(self):
        lib = eq.parse_liberty(self.lib_path, wanted={"sg13cmos5l_buf_1"})
        self.assertEqual(list(lib), ["sg13cmos5l_buf_1"])


class TestNetlist(Tmp):
    def test_parse(self):
        nl = self.netlist()
        self.assertEqual(nl.module, eq.DESIGN)
        self.assertEqual(nl.ports["uo_out"], "output")
        self.assertEqual(nl.expand("ui_in")[0], "ui_in[7]")
        self.assertEqual(nl.insts["ff0"]["pins"]["Q"], ["\\core.q[0]"])
        self.assertEqual(nl.insts["g1"]["pins"]["B"], ["\\core.q[0]"])
        din = nl.insts["\\core.mem"]["pins"]["A_DIN"]
        self.assertEqual(len(din), 16)
        self.assertEqual(din[-1], "ui_in[0]")
        self.assertEqual(nl.insts["FILLER_0"]["pins"], {})

    def test_const_bits(self):
        self.assertEqual(eq.const_bits("1'b0"), ["1'b0"])
        self.assertEqual(eq.const_bits("4'ha"), ["1'b1", "1'b0", "1'b1", "1'b0"])
        self.assertEqual(eq.const_bits("2'bx1"), ["1'bx", "1'b1"])
        self.assertEqual(eq.const_bits("3'd5"), ["1'b1", "1'b0", "1'b1"])

    def test_stops_at_first_endmodule(self):
        p = self.write("two.v", HEADER + "endmodule\nmodule x (a);\n input a;\nendmodule\n")
        # the first module ends at endmodule; the parser stops there
        nl = eq.Netlist(p)
        self.assertEqual(nl.module, eq.DESIGN)


class TestStructure(Tmp):
    def test_clean(self):
        nl = self.netlist()
        st = eq.analyze_structure(nl, self.lib)
        self.assertEqual(st["errors"], [])
        self.assertEqual(st["flip_flops"], 1)
        self.assertEqual(st["clock"]["ff_depth_histogram"], {"2": 1})
        self.assertEqual(st["clock"]["sram_depth"], {"\\core.mem": 1})
        self.assertEqual(st["srams"], ["\\core.mem"])

    def test_gated_clock(self):
        body = BODY.replace(" sg13cmos5l_buf_1 cb2 (.A(ck1),", " sg13cmos5l_nand2_1 cb2 (.A(ck1), .B(ui_in[7]),")
        body = body.replace(".X(ck2));", ".Y(ck2));")
        st = eq.analyze_structure(self.netlist(body), self.lib)
        self.assertTrue(any("not a buffer" in e for e in st["errors"]), st["errors"])

    def test_clock_from_other_port(self):
        body = BODY.replace(" sg13cmos5l_buf_1 cb1 (.A(clk),", " sg13cmos5l_buf_1 cb1 (.A(ena),")
        st = eq.analyze_structure(self.netlist(body), self.lib)
        self.assertTrue(any("comes from port ena" in e for e in st["errors"]), st["errors"])

    def test_two_drivers(self):
        body = BODY + " sg13cmos5l_buf_1 dup (.A(ui_in[3]),\n    .X(n1));\n"
        st = eq.analyze_structure(self.netlist(body), self.lib)
        self.assertTrue(any("two drivers" in e for e in st["errors"]), st["errors"])

    def test_unsupported_cell(self):
        body = BODY + " sg13cmos5l_ebufn_2 tb (.A(n1),\n    .TE_B(ui_in[4]),\n    .Z(uio_out[0]));\n"
        st = eq.analyze_structure(self.netlist(body), self.lib)
        self.assertTrue(any("three-state" in e for e in st["errors"]), st["errors"])

    def test_unknown_cell(self):
        body = BODY + " mystery_cell m0 (.A(n1));\n"
        st = eq.analyze_structure(self.netlist(body), self.lib)
        self.assertTrue(any("not in the liberty" in e for e in st["errors"]), st["errors"])


class TestMutations(Tmp):
    def test_near_output_nand(self):
        nl = self.netlist()
        text, rec = eq.mutate_near_output_nand(nl, self.lib)
        self.assertEqual(rec["instance"], "g2")          # drives the buffer on uo_out[0]
        self.assertEqual(rec["levels_from_output"], 1)
        self.assertIn(" sg13cmos5l_nor2_1 g2 (", text)
        self.assertIn(" sg13cmos5l_nand2_1 g1 (", text)
        self.assertEqual(len(text), len(nl.text) + len("nor2") - len("nand2"))

    def test_sram_din_swap(self):
        nl = self.netlist()
        text, rec = eq.mutate_sram_din_swap(nl)
        self.assertEqual(rec["bits"], [3, 2])
        self.assertEqual(rec["nets"], {"A_DIN[3]": "\\core.q[0]", "A_DIN[2]": "n1"})
        p = self.write("mut.v", text)
        din = eq.Netlist(p).insts["\\core.mem"]["pins"]["A_DIN"]
        self.assertEqual(din[12:14], ["n1", "\\core.q[0]"])
        self.assertEqual(sorted(din), sorted(nl.insts["\\core.mem"]["pins"]["A_DIN"]))

    def test_sram_din_swap_fallback(self):
        # bits 3 and 2 on the same net: the first distinct adjacent pair from bit 0 is used
        sram = SRAM.replace("    n1,\n    ui_in[1]", "    \\core.q[0] ,\n    ui_in[1]")
        nl = self.netlist(sram=sram)
        _, rec = eq.mutate_sram_din_swap(nl)
        self.assertEqual(rec["bits"], [1, 0])


class TestVerdict(unittest.TestCase):
    STATS = "miter : i/o =  147/    1  lat = 5139  and = 124305  lev = 93\n"

    def v(self, body, rc=0, to=False, stats=None):
        return eq.parse_abc((self.STATS if stats is None else stats) * 2 + body, rc, to)[0]

    def test_equivalent(self):
        self.assertEqual(self.v("No output failed in 12 frames.\nNetworks are equivalent.  Time = 63.73 sec\n"),
                         "equivalent")

    def test_not_equivalent(self):
        v, info = eq.parse_abc(self.STATS + 'Output 0 of miter "miter" was asserted in frame 8. Time = 0.02 sec\n'
                               "Networks are not equivalent.\n", 0, False)
        self.assertEqual((v, info["frame"]), ("not_equivalent", 8))

    def test_abc_spellings(self):
        # dprove's other spellings (ABC in OSS CAD Suite 2026-07-29)
        v, info = eq.parse_abc(self.STATS + "Property DISPROVED in frame 44 using interpolation.  Time = 98.85 sec\n"
                               "Networks are NOT EQUIVALENT.  Time =   905.89 sec\n"
                               'Output 0 of miter "miter" was asserted in frame 44.\n', 0, False)
        self.assertEqual((v, info["frame"]), ("not_equivalent", 44))
        self.assertEqual(self.v("Networks are equivalent after structural hashing.\n"), "equivalent")
        self.assertEqual(self.v("Networks are undecided (resource limits is reached).\n"), "undecided")
        self.assertEqual(self.v("Property DISPROVED in frame 3.\nNetworks are equivalent.\n"), "error")
        self.assertEqual(self.v("Networks are NOT EQUIVALENT.\nNetworks are equivalent.\n"), "error")

    def test_undecided_and_timeout(self):
        self.assertEqual(self.v("Networks are UNDECIDED.   Time =  1466.19 sec\n"), "undecided")
        self.assertEqual(self.v("", rc=None, to=True), "timeout")
        self.assertEqual(eq.EXIT["undecided"], 3)
        self.assertEqual(eq.EXIT["timeout"], 3)

    def test_fail_closed(self):
        self.assertEqual(self.v(""), "error")                                           # no verdict
        self.assertEqual(self.v("Networks are equivalent.\n", rc=1), "error")           # tool failure
        self.assertEqual(self.v("Networks are equivalent.\nNetworks are not equivalent.\n"), "error")
        self.assertEqual(self.v("Networks are equivalent.\n",
                                stats="m : i/o = 3/ 2  lat = 1  and = 4  lev = 1\n"), "error")  # 2 outputs
        self.assertEqual(self.v("  Networks are equivalent.\n"), "error")                # not at line start
        self.assertEqual(self.v("Networks are equivalent.\n", stats=""), "error")        # no statistics
        self.assertEqual(eq.EXIT["equivalent"], 0)
        self.assertTrue(all(code != 0 for k, code in eq.EXIT.items() if k != "equivalent"))

    def test_cec_mode(self):
        cec = "The network has no latches. Running CEC.\n"
        self.assertEqual(self.v(cec + "UNSATISFIABLE  Time =     0.01 sec\n"), "equivalent")
        self.assertEqual(self.v(cec + "SATISFIABLE    Time =     0.01 sec\n"), "not_equivalent")
        self.assertEqual(self.v(cec), "error")


class TestRecipe(unittest.TestCase):
    def test_no_ignore_gold_x(self):
        # -ignore_gold_x + setundef -zero masks every mismatch where the gold bit is 0 (control c1)
        self.assertNotIn("ignore_gold_x", eq.MITER_SCRIPT)

    def test_assertions_present(self):
        self.assertIn("select -assert-none t:* t:$_AND_ t:$_NOT_ t:$_DFF_P_ %u %u %d", eq.MITER_SCRIPT)
        self.assertIn("w:in_clk %d", eq.MITER_SCRIPT)
        self.assertIn("check -assert", eq.gold_script([], 8))
        self.assertIn("check -assert", eq.gate_script([], 8))

    def test_controls_declared(self):
        ctl = eq.load_controls()
        names = [c["name"] for c in ctl]
        self.assertIn("c1_gold0_mask", names)
        self.assertTrue(all(c["expect"] in ("equivalent", "not_equivalent", "error") for c in ctl))


class TestInputs(Tmp):
    def test_tt_submission(self):
        root = os.path.join(self.d, "sub")
        nl = self.write("sub/tt_submission/%s.v" % eq.DESIGN, HEADER + BODY + SRAM + "endmodule\n")
        self.write("sub/tt_submission/pdk.json", json.dumps({"PDK": "ihp-sg13cmos5l", "PDK_VERSION": "a" * 40}))
        self.write("sub/tt_submission/commit_id.json", json.dumps({"commit": "b" * 40}))
        self.write("sub/src/project.v", "// top\n")
        path, meta = eq.resolve_run_dir(root)
        self.assertEqual(path, nl)
        self.assertEqual(meta["layout"], "tt_submission")
        self.assertEqual(meta["pdk"]["PDK_VERSION"], "a" * 40)
        self.assertIn("project.v", meta["artifact_src"])

    def test_sweep_run_checksum(self):
        p = self.write("run/out/final/nl/%s.nl.v" % eq.DESIGN, "module x; endmodule\n")
        with open(p, "rb") as f:
            good = hashlib.sha256(f.read()).hexdigest()
        self.write("run/out/final/SHA256SUMS", "%s  ./nl/%s.nl.v\n" % (good, eq.DESIGN))
        path, meta = eq.resolve_run_dir(os.path.join(self.d, "run"))
        self.assertEqual((path, meta["layout"], meta["sha256sums_check"]), (p, "sweep_run", "ok"))
        self.write("run/out/final/SHA256SUMS", "%s  ./nl/%s.nl.v\n" % ("0" * 64, eq.DESIGN))
        with self.assertRaises(eq.EqError):
            eq.resolve_run_dir(os.path.join(self.d, "run"))

    def test_pdk_mismatch_refused(self):
        root = os.path.join(self.d, "sub")
        self.write("sub/tt_submission/%s.v" % eq.DESIGN, HEADER + BODY + SRAM + "endmodule\n")
        self.write("sub/tt_submission/pdk.json", json.dumps({"PDK": "ihp-sg13cmos5l", "PDK_VERSION": "a" * 40}))
        pdk_root = os.path.join(self.d, "pdk")
        self.write("pdk/ihp-sg13cmos5l/SOURCES", "IHP-Open-PDK %s\n" % ("c" * 40))
        self.write("pdk/ihp-sg13cmos5l/" + eq.LIBERTY_REL, TINY_LIB)

        class A(object):
            netlist = None
            run_dir = root
            variant = "base"
            core = None
            top = None
            liberty = None
            pdk = None
            allow_pdk_mismatch = False
            rtl_from_artifact = False
        A.pdk_root = pdk_root
        with self.assertRaises(eq.EqError) as cm:
            eq.resolve_inputs(A)
        self.assertIn("install that revision", str(cm.exception))
        A.allow_pdk_mismatch = True
        inp = eq.resolve_inputs(A)
        self.assertFalse(inp["pdk"]["matches_netlist"])
        self.write("pdk/ihp-sg13cmos5l/SOURCES", "IHP-Open-PDK %s\n" % ("a" * 40))
        A.allow_pdk_mismatch = False
        self.assertTrue(eq.resolve_inputs(A)["pdk"]["matches_netlist"])

    def test_variant_header(self):
        class A(object):
            netlist = None
            run_dir = None
            variant = "diet4"
            core = os.path.join(eq.REPO, "src", "protocol_emulator_core.v")   # the base core
            top = None
            liberty = None
            pdk = None
            pdk_root = None
            allow_pdk_mismatch = False
            rtl_from_artifact = False
        A.netlist = self.write("n.v", "module x; endmodule\n")
        A.liberty = self.lib_path
        with self.assertRaises(eq.EqError) as cm:
            eq.resolve_inputs(A)
        self.assertIn("another configuration", str(cm.exception))


def _have_yosys():
    try:
        eq.find_tools()
        return True
    except eq.EqError:
        return False


@unittest.skipUnless(_have_yosys(), "yosys / yosys-abc not available")
class TestControlsWithYosys(unittest.TestCase):
    def test_controls(self):
        d = tempfile.mkdtemp(prefix="pe-eq-ctl-")
        try:
            runner = eq.Runner(eq.find_tools(), 300, 300)
            res = eq.run_controls(runner, d, 2)
            missed = [(c["name"], c["expect"], c["verdict"]) for c in res if not c["met_expectation"]]
            self.assertEqual(missed, [])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
