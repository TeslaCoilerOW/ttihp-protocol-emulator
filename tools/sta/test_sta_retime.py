#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the tool-free parts of sta_retime.py (standard library only):
python3 -m unittest tools/sta/test_sta_retime.py"""
import os
import sys
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sta_retime as S  # noqa: E402


class TclEncoding(unittest.TestCase):
    def test_decimal_and_int(self):
        # LibreLane's TclStep.value_to_tcl: integral Decimals keep one decimal.
        self.assertEqual(S.value_to_tcl(Decimal("15")), "15.0")
        self.assertEqual(S.value_to_tcl(Decimal("20.0")), "20.0")
        self.assertEqual(S.value_to_tcl(Decimal("0.25")), "0.25")
        self.assertEqual(S.value_to_tcl(Decimal("13.33")), "13.33")
        self.assertEqual(S.value_to_tcl(8), "8")
        self.assertEqual(S.value_to_tcl(True), "1")

    def test_typed(self):
        self.assertEqual(S.value_to_tcl(S.typed(15, "Decimal")), "15.0")
        self.assertEqual(S.value_to_tcl(S.typed(Decimal("6"), "Decimal")), "6.0")
        self.assertEqual(S.typed(8, "int"), 8)
        self.assertIsNone(S.typed(None, "Decimal"))
        with self.assertRaises(S.Fail):
            S.typed(Decimal("8.5"), "int")

    def test_escape_and_lists(self):
        self.assertEqual(S.tcl_escape("sg13cmos5l_buf_4/X"), "sg13cmos5l_buf_4/X")
        self.assertEqual(S.tcl_escape(""), '""')
        self.assertEqual(S.tcl_escape("a b"), '"a b"')
        self.assertEqual(S.tcl_escape('x"$[y'), '"x\\"\\$\\[y"')
        # as in a LibreLane _env*.tcl: set ::env(TECH_LEFS) "\"nom_*\" /p/a.lib"
        self.assertEqual(S.tcl_escape(S.value_to_tcl({"nom_*": "/p/a.lib"})),
                         '"\\"nom_*\\" /p/a.lib"')
        self.assertEqual(S.tcl_join(["/a/x.lib", "/b/y.lib"]), "/a/x.lib /b/y.lib")


class Views(unittest.TestCase):
    def test_filter_views(self):
        views = {"*_typ_*": ["t.lib"], "*_fast_*": "f.lib", "!*_slow_*": ["ns.lib"]}
        self.assertEqual(S.filter_views(views, "nom_typ_1p20V_25C"), ["t.lib", "ns.lib"])
        self.assertEqual(S.filter_views(views, "nom_fast_1p32V_m40C"), ["f.lib", "ns.lib"])
        self.assertEqual(S.filter_views(views, "nom_slow_1p08V_125C"), [])
        self.assertEqual(S.filter_views({}, "nom_typ"), [])

    def test_remap_and_liberty_clock(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "pdkx", "libs.ref", "c", "lib", "c_typ.lib")
            os.makedirs(os.path.dirname(lib))
            with open(lib, "w") as f:
                f.write("cell(M) {\n bus(A_ADDR) {\n  pin(A_ADDR[0]) { }\n }\n"
                        " pin(A_CLK) {\n  direction : input;\n  clock : \"true\" ;\n }\n"
                        " pg_pin(VDD) { }\n pin(A_DOUT) { direction : output; }\n}\n")
            self.assertEqual(S.remap_pdk_path("/elsewhere/pdkx/libs.ref/c/lib/c_typ.lib", "pdkx", d),
                             os.path.realpath(lib))
            with self.assertRaises(S.Fail):
                S.remap_pdk_path("/elsewhere/other/c_typ.lib", "pdkx", d)
            self.assertEqual(S.liberty_clock_pins(lib), ["A_CLK"])

    def test_bind_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "a", "b")
            os.makedirs(sub)
            f = os.path.join(sub, "x.txt")
            open(f, "w").close()
            self.assertEqual(S.bind_dirs([d, sub, f]), [os.path.realpath(d)])


class Shifts(unittest.TestCase):
    def test_expected_shifts_15_to_20(self):
        # base.sdc: I/O delay = 20 % of the period: 3 ns at 15 ns, 4 ns at 20 ns.
        e = S.expected_shifts(Decimal(5), Decimal(1))
        self.assertEqual(e["setup"], {"r2r": 5, "in2reg": 4, "reg2out": 4, "in2out": 3})
        self.assertEqual(e["hold"], {"r2r": 0, "in2reg": 1, "reg2out": 1, "in2out": 2})

    def test_compare_prediction(self):
        def case(label, period, io, cls_setup, ws_setup, cls_hold, ws_hold):
            def classes(vals):
                d = {"all": {"slack": str(min(vals.values())), "start": "s", "end": "e"}}
                d.update({k: {"slack": v, "start": "s" + k, "end": "e" + k} for k, v in vals.items()})
                return d
            return {"label": label, "period": period, "io_delay_ns": io,
                    "inputs": {"netlist": "n", "spef": "p"}, "env_vars": {"CLOCK_PERIOD": period},
                    "corners": {"c": {"metrics": {"timing__setup__ws": ws_setup,
                                                  "timing__hold__ws": ws_hold,
                                                  "timing__setup_r2r__ws": "0",
                                                  "timing__hold_r2r__ws": "0"},
                                      "classes": {"setup": classes(cls_setup),
                                                  "hold": classes(cls_hold)}}}}
        a = case("a", "15", "3",
                 {"r2r": "2.239966", "in2reg": "2.789646", "reg2out": "5.944792", "in2out": "4.317587"},
                 "2.239966", {"r2r": "0.743079", "in2reg": "1.259909", "reg2out": "5.427528",
                              "in2out": "7.765114"}, "0.743079")
        b = case("b", "20", "4",
                 {"r2r": "7.239967", "in2reg": "6.789646", "reg2out": "9.944792", "in2out": "7.317587"},
                 "6.789646", {"r2r": "0.743079", "in2reg": "2.259909", "reg2out": "6.427528",
                              "in2out": "9.765114"}, "0.743079")
        cmp = S.compare(a, b)
        self.assertTrue(cmp["ok"], cmp)
        self.assertEqual(cmp["corners"]["c"]["setup_prediction"]["limiting_class"], "in2reg")
        b["corners"]["c"]["classes"]["setup"]["r2r"]["slack"] = "7.24"
        self.assertFalse(S.compare(a, b)["ok"])


if __name__ == "__main__":
    unittest.main()
