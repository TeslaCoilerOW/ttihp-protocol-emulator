#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the optimizer's search space, tracks and objective (no cluster,
no LibreLane, no optuna needed):

  python3 tools/opt/test_opt.py

They check the properties the driver relies on (docs/optimization.md):
  * the committed configuration maps to a knob set that reproduces it exactly;
  * knob values are absolute: a knob set gives the same effective configuration
    on any committed configuration that differs only in knob-controlled keys;
  * the 6x4 track's committed build equals what variants6x4/switch.py writes,
    and the 6x4 overlay states every knob key (a value or null);
  * the 6x4 vertical-halo choices are the island-free ones of row_islands.py;
  * translation between the 8x4 and 6x4 spaces, and the per-corner fmax;
  * FIXED knobs (CTS_MAX_SLEW) are never sampled, the committed configurations are
    inside the space, and stored knob sets outside it still reproduce their run;
  * the promotion gates (gates.py): the equivalence stage reads formal_eq's
    result.json and passes only on "equivalent" with exit code 0 for the expected
    inputs (schema, sha256, PDK root, variant); only a time limit is retried, in
    gates.eq_should_retry() and in Driver.poll_promos(); a promotion is PASS only if
    the full run, the precheck, the gate-level tests and the equivalence check all
    pass;
  * the clock-depth warning (clockdepth.py) on a small netlist.
"""

import copy
import json
import os
import random
import sys
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import clockdepth as CD  # noqa: E402
import gates as GATES  # noqa: E402
import objective as OBJ  # noqa: E402
import report as REPORT  # noqa: E402
import space as SPACE  # noqa: E402
import tracks as TR  # noqa: E402
from store import State  # noqa: E402

DOR_CORE = os.path.join(REPO, "src", "protocol_emulator_core.v")
# The committed CLOCK_PERIOD (15 ns since the p018 adoption; 20 ns before). The
# design-of-record track at this period is the one whose committed knob set
# round-trips; OTHER_PERIOD is a frequency track that differs only in it.
with open(os.path.join(REPO, "src", "config.json")) as _f:
    COMMITTED_PERIOD = float(json.load(_f)["CLOCK_PERIOD"])
OTHER_PERIOD = 20.0 if COMMITTED_PERIOD != 20.0 else 15.0


def random_knobs(D, rng):
    out = {}
    for n in SPACE.ORDER:
        if not SPACE.active(n, out, D):
            continue
        k = D[n]
        if k["kind"] == "cat":
            out[n] = rng.choice(k["choices"])
        elif k["kind"] == "int":
            out[n] = rng.randrange(k["lo"], k["hi"] + 1, k["step"])
        else:
            steps = int(round((k["hi"] - k["lo"]) / k["step"]))
            out[n] = round(k["lo"] + k["step"] * rng.randint(0, steps), 6)
    return out


def strip(cfg):
    c = copy.deepcopy(cfg)
    c.pop("//", None)
    c.pop("OPENROAD_THREADS", None)
    return c


class Space(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dor = TR.Track(REPO, "dor", "dor", DOR_CORE, "8x4", COMMITTED_PERIOD, "dor", "base")

    def test_committed_roundtrip(self):
        t = self.dor
        snap, ov = t.materialize({})
        self.assertEqual(ov, {}, "the committed knob set needs no override")
        self.assertEqual(t.diff_vs_repo({}), {})
        self.assertEqual(strip(t.effective({}, threads=4)), strip(t.repo_cfg))

    def test_absolute_semantics(self):
        """Remove every knob-controlled key except the snapshot keys from the committed config (a
        different committed knob set with the same fixed part): every knob set must give the
        same effective configuration on both."""
        t = self.dor
        other = copy.deepcopy(t.repo_cfg)
        for k in SPACE.KNOB_KEYS:
            if k not in ("CLOCK_PERIOD", "PL_TARGET_DENSITY_PCT", "PL_RESIZER_HOLD_SLACK_MARGIN",
                         "GRT_RESIZER_HOLD_SLACK_MARGIN", "OPENROAD_THREADS", "FP_MACRO_HORIZONTAL_HALO"):
                other.pop(k, None)
        other["FP_MACRO_HORIZONTAL_HALO"] = 16.48
        other["MACROS"][TR.MACRO]["instances"] = t.floorplans["fp8_base"]["instances"]
        self.assertEqual(TR.fixed_sha(other), TR.fixed_sha(t.repo_cfg))
        base2 = SPACE.base_knobs(other, t.floorplans, t.D)
        rng = random.Random(7)
        cases = [t.complete({}), SPACE.complete({}, base2, t.D)] + [random_knobs(t.D, rng) for _ in range(300)]
        for kn in cases:
            kn = t.complete(kn)
            e1 = TR.effective_config(t.repo_cfg, t.floorplans[kn["floorplan"]],
                                     *SPACE.materialize(kn, t.base, t.repo_cfg,
                                                        t.floorplans[kn["floorplan"]].get("config"), t.D),
                                     period=COMMITTED_PERIOD, threads=32)
            e2 = TR.effective_config(other, t.floorplans[kn["floorplan"]],
                                     *SPACE.materialize(kn, base2, other,
                                                        t.floorplans[kn["floorplan"]].get("config"), t.D),
                                     period=COMMITTED_PERIOD, threads=32)
            self.assertEqual(TR.changes(strip(e1), strip(e2)), {}, kn)
            # and the knob set is recovered from its own effective configuration
            back = SPACE.base_knobs(e1, t.floorplans, t.D)
            self.assertEqual(SPACE.canonical(t.complete(back)), SPACE.canonical(kn))

    def test_unset_is_absent(self):
        """A knob at its LibreLane default leaves the key out of the effective configuration."""
        t = self.dor
        kn = t.complete({"PL_TIMING_DRIVEN": False, "CTS_SINK_CLUSTERING_SIZE": None, "SYNTH_STRATEGY": "AREA 0",
                         "grt_adj_m2": 0.0, "grt_adj_m3": 0.0, "grt_adj_m4": 0.0})
        eff = t.effective(kn)
        for k in ("PL_TIMING_DRIVEN", "CTS_SINK_CLUSTERING_SIZE", "SYNTH_STRATEGY", "GRT_LAYER_ADJUSTMENTS"):
            self.assertNotIn(k, eff)
        self.assertEqual(t.materialize(kn)[1]["PL_TIMING_DRIVEN"], None)

    def test_frequency_track(self):
        t2 = TR.Track(REPO, "dor_other", "freq", DOR_CORE, "8x4", OTHER_PERIOD, "dor_other", "base")
        num = lambda p: int(p) if float(p).is_integer() else p  # noqa: E731
        self.assertEqual(t2.diff_vs_repo({}),
                         {"CLOCK_PERIOD": {"repo": num(COMMITTED_PERIOD), "run": num(OTHER_PERIOD)}})
        self.assertEqual(t2.fixed, self.dor.fixed)
        self.assertNotEqual(t2.id, self.dor.id)


class SixByFour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = TR.Track(REPO, "diet4_6x4", "6x4", os.path.join(REPO, TR.SIXBY4["core"]), "6x4", 20.0, "6x4",
                         "diet4", overlay=os.path.join(REPO, TR.SIXBY4["overlay"]))

    def test_matches_switch_py(self):
        sys.path.insert(0, os.path.join(REPO, "variants6x4"))
        import switch  # noqa: E402
        errors, _, outputs = switch.plan(REPO)
        if errors:
            self.skipTest("switch.py check fails on this checkout: %s" % errors)
        merged = json.loads(outputs["src/config.json"].decode())
        self.assertEqual(TR.changes(strip(merged), strip(self.t.effective({}, threads=4))), {})
        self.assertEqual(self.t.diff_vs_base({}), {})

    def test_overlay_states_every_knob(self):
        # docs/6x4.md section 4c: a later src/config.json change of a knob key
        # for the 8x4 build must not reach the 6x4 build
        sys.path.insert(0, os.path.join(REPO, "variants6x4"))
        import switch  # noqa: E402
        ov = switch.load_overlay(switch.CONFIG_OVERLAY)
        self.assertEqual(set(ov) - {"MACROS"}, set(SPACE.KNOB_KEYS))

    def test_island_free_vertical_halo(self):
        self.assertEqual(self.t.restrict, {"FP_MACRO_VERTICAL_HALO": [10.0, 5.0]})
        for vh in self.t.D["FP_MACRO_VERTICAL_HALO"]["choices"]:
            cfg = self.t.effective(self.t.complete({"FP_MACRO_VERTICAL_HALO": vh}))
            self.assertEqual(TR.islands(REPO, cfg, os.path.join(REPO, "src"), "6x4"), 0)

    def test_translate_from_8x4(self):
        dor = TR.Track(REPO, "dor", "dor", DOR_CORE, "8x4", 20.0, "dor20", "base")
        kn = dor.complete({"floorplan": "fp8_wide_trk", "halo_h_wide": 12.5, "FP_MACRO_VERTICAL_HALO": 15.0,
                           "density": 57})
        tr = SPACE.translate(kn, self.t.base, self.t.D)
        self.assertEqual(tr["floorplan"], "fp6_tworow_trk_top30_edgeobs")
        self.assertEqual(tr["FP_MACRO_VERTICAL_HALO"], self.t.base["FP_MACRO_VERTICAL_HALO"])
        self.assertEqual(tr["density"], 57)
        self.assertNotIn("halo_h_wide", tr)
        self.assertEqual(self.t.effective(tr)["FP_MACRO_HORIZONTAL_HALO"], 16.48)


class Objective(unittest.TestCase):
    def test_fmax_per_corner(self):
        res = {"status": "complete", "exit_code": 0, "flow_complete": True, "sta_source": "post-route",
               "sta": {"typ": {"setup_ws": 3.0, "setup_r2r_ws": 4.0}, "fast": {"setup_ws": 5.0},
                       "slow": {"setup_ws": -1.0, "setup_r2r_ws": -1.0}}}
        m = OBJ.flatten(res, {}, period=15.0)
        self.assertAlmostEqual(m["typ_fmax_mhz"], 1000.0 / 12.0, places=3)
        self.assertAlmostEqual(m["slow_fmax_mhz"], 1000.0 / 16.0, places=3)
        self.assertAlmostEqual(m["typ_fmax_r2r_mhz"], 1000.0 / 11.0, places=3)
        self.assertEqual(m["fmax_all_corners_mhz"], m["slow_fmax_mhz"])
        self.assertEqual((m["min_ws"], m["min_ws_corner"]), (-1.0, "slow"))


class FakeTrial(object):
    """Stands in for an optuna Trial: records which parameters were sampled."""

    def __init__(self, rng):
        self.rng, self.asked = rng, []

    def suggest_categorical(self, name, choices):
        self.asked.append(name)
        return self.rng.choice(choices)

    def suggest_int(self, name, lo, hi, step=1):
        self.asked.append(name)
        return self.rng.randrange(lo, hi + 1, step)

    def suggest_float(self, name, lo, hi, step=None):
        self.asked.append(name)
        return lo + step * self.rng.randint(0, int(round((hi - lo) / step)))


class Fixed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dor = TR.Track(REPO, "dor", "dor", DOR_CORE, "8x4", COMMITTED_PERIOD, "dor", "base")
        cls.six = TR.Track(REPO, "diet4_6x4", "6x4", os.path.join(REPO, TR.SIXBY4["core"]), "6x4", 20.0, "6x4",
                           "diet4", overlay=os.path.join(REPO, TR.SIXBY4["overlay"]))

    def test_cts_max_slew_fixed_unset(self):
        self.assertEqual(SPACE.FIXED, {"CTS_MAX_SLEW": None})

    def test_never_sampled(self):
        rng = random.Random(3)
        for t in (self.dor, self.six):
            for _ in range(200):
                tr = FakeTrial(rng)
                kn = SPACE.fix(t.complete(SPACE.suggest(tr, t.D)))
                for n in SPACE.FIXED:
                    self.assertNotIn(n, tr.asked)
                    self.assertEqual(kn[n], SPACE.FIXED[n])
                self.assertEqual(SPACE.outside(kn), {})
                self.assertNotIn("CTS_MAX_SLEW", t.effective(kn))

    def test_committed_configurations_inside(self):
        # a committed CTS_MAX_SLEW would make the baseline trial differ from the committed build
        for t in (self.dor, self.six):
            self.assertEqual(SPACE.outside(t.base), {})
            self.assertEqual(SPACE.fix(t.complete({})), t.complete({}))

    def test_study_distribution_unchanged(self):
        # optuna refuses a changed categorical choice list for a parameter name in a study
        # that already has it, so the definition keeps the values earlier trials used
        self.assertEqual(SPACE.KNOBS["CTS_MAX_SLEW"]["choices"], [None, 0.4, 0.75, 1.2])

    def test_stored_knob_set_outside_still_reproduces(self):
        t = self.six
        kn = t.complete({"CTS_MAX_SLEW": 1.2, "CTS_CLK_MAX_WIRE_LENGTH": 250})
        self.assertEqual(SPACE.outside(kn), {"CTS_MAX_SLEW": 1.2})
        self.assertEqual(t.effective(kn)["CTS_MAX_SLEW"], 1.2)          # the stored run is reproduced
        fixed = SPACE.fix(kn)                                              # a new seed from it is not
        self.assertEqual(SPACE.outside(fixed), {})
        self.assertNotIn("CTS_MAX_SLEW", t.effective(fixed))
        self.assertEqual(t.effective(fixed)["CTS_CLK_MAX_WIRE_LENGTH"], 250)
        self.assertEqual(SPACE.outside({"CTS_MAX_SLEW": None}), {})
        self.assertEqual(SPACE.outside(None), {})


SHA_CORE, SHA_NL, SHA_TOP, SHA_LIB = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
SHA_CHECK = "e" * 64
EXPECT = {"netlist": SHA_NL, "core": SHA_CORE, "project_v": SHA_TOP, "pdk_root": "/w/sram-flow/pdk",
          "variant": "diet4", "job": "1", "checker": SHA_CHECK}
FE_EXIT = {"equivalent": 0, "not_equivalent": 1, "error": 2, "undecided": 3, "timeout": 3}


def fe_result(verdict="equivalent", exit_code=None, passed=None, schema=GATES.FE_SCHEMA, frame=None, why=None,
              timed_out=(), **inputs):
    """A formal_eq result.json (formal_eq/eq_check.py cmd_check) of a 6x4 netlist."""
    code = FE_EXIT.get(verdict, 2) if exit_code is None else exit_code
    inp = {"netlist_source": {"path": "/w/sub/top.v", "sha256": SHA_NL}, "variant": "diet4",
           "top": {"path": "/w/tree/src/project.v", "sha256": SHA_TOP},
           "core": {"path": "/w/cores/core.v", "sha256": SHA_CORE, "header": "// ... diet4.json"},
           "pdk": {"name": "ihp-sg13cmos5l", "root": "/w/sram-flow/pdk", "version": "2bbec755"},
           "liberty": {"path": "/w/sram-flow/pdk/x.lib", "sha256": SHA_LIB},
           "netlist": {"path": "/local/inputs/top.v", "sha256": SHA_NL}}
    for k, v in inputs.items():
        if k == "variant":
            inp["variant"] = v
        elif k == "pdk_root":
            inp["pdk"]["root"] = v
        else:
            inp[k] = dict(inp[k], sha256=v)
    steps = {s: {"rc": 0, "wall_s": w, "timed_out": s in timed_out} for s, w in (("gate", 5.1), ("miter", 16.1))}
    steps["abc"] = {"rc": 0, "wall_s": 70.51, "timed_out": verdict == "timeout", "time_limit_s": 3600}
    line = {"equivalent": "Networks are equivalent.  Time =    69.85 sec",
            "not_equivalent": "Networks are not equivalent.", "undecided": "Networks are UNDECIDED."}.get(verdict)
    return {"schema": schema, "verdict": verdict, "exit_code": code, "pass": code == 0 if passed is None else passed,
            "why": why, "slurm_job_id": "1", "host": "n1", "argv": ["check"],
            "tool_git": {"commit": None, "formal_eq_modified": None},
            "tool_files": {"formal_eq/eq_check.py": SHA_CHECK, "formal_eq/wrap.v": "f" * 64},
            "tools": {"yosys": "/oss/bin/yosys", "yosys_abc": "/oss/bin/yosys-abc", "oss_cad_suite": "/oss",
                      "yosys_version": "Yosys 0.67+111"},
            "inputs": inp, "gold_build": {"rc": 0, "wall_s": 0.9, "timed_out": "gold" in timed_out, "ok": True},
            "structure": {"clock": {"ff_depth_min": 5}},
            "case": {"steps": steps, "verdict": verdict, "miter_aig_sha256": "0" * 64,
                     "abc": {"aig_first": {"inputs": 147, "outputs": 1, "latches": 5139, "and_nodes": 124314},
                             "verdict_line": line, "frame": frame, "mode": "sequential"}}}


def eq(fe, outer=None, expect=EXPECT, problem=None):
    """The stage's record of a formal_eq result (outer: exit status of the checker process)."""
    if outer is None:
        outer = fe["exit_code"] if fe else 2
    parsed = GATES.parse_eq(fe, outer, problem)
    return GATES.eq_record(parsed, fe, expect, "1", 3600, outer, 100, {"path": "/t/formal_eq/eq_check.py"},
                           abc_version="UC Berkeley, ABC 1.01")


# Stands in for formal_eq/eq_check.py in test_eq_job_script: writes a result.json from a
# template with the sha256s of the files it was given and exits with the verdict's code.
FAKE_CHECKER = r"""
import argparse, hashlib, json, os, sys
ap = argparse.ArgumentParser()
ap.add_argument("cmd")
for o in ("--netlist", "--variant", "--core", "--top", "--pdk-root", "--timeout", "--build-timeout", "--jobs",
          "--label", "--out"):
    ap.add_argument(o)
a = ap.parse_args()
h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
r = json.load(open(os.environ["FAKE_TEMPLATE"]))
v = os.environ["FAKE_VERDICT"]
code = {"equivalent": 0, "not_equivalent": 1, "error": 2, "undecided": 3, "timeout": 3}[v]
r.update(verdict=v, exit_code=code, argv=sys.argv[1:], slurm_job_id=None, tool_files={})
r["pass"] = code == 0
r["case"]["verdict"] = v
r["inputs"]["netlist_source"] = {"path": a.netlist, "sha256": h(a.netlist)}
r["inputs"]["netlist"] = {"path": os.path.join(a.out, "inputs", "top.v"), "sha256": h(a.netlist)}
r["inputs"]["core"]["sha256"], r["inputs"]["top"]["sha256"] = h(a.core), h(a.top)
r["inputs"]["pdk"]["root"], r["inputs"]["variant"] = a.pdk_root, a.variant
os.makedirs(os.path.join(a.out, "netlist"))
open(os.path.join(a.out, "netlist", "abc.log"), "w").write("Networks are equivalent.")
json.dump(r, open(os.path.join(a.out, "result.json"), "w"))
sys.exit(code)
"""


class Gates(unittest.TestCase):
    def test_equivalent_passes(self):
        r = eq(fe_result())
        self.assertEqual((r["verdict"], r["pass"], r["latches"], r["abc_s"]), ("equivalent", True, 5139, 71))
        self.assertEqual(r["input_mismatches"], [])
        fe = r["formal_eq"]
        self.assertEqual(fe["tool_files"]["formal_eq/eq_check.py"], SHA_CHECK)
        self.assertEqual(fe["tools"]["yosys_version"], "Yosys 0.67+111")
        self.assertEqual(fe["tools"]["abc_version"], "UC Berkeley, ABC 1.01")
        self.assertEqual(fe["inputs"]["liberty"]["sha256"], SHA_LIB)
        self.assertEqual(r["build_s"], {"gold": 0.9, "gate": 5.1, "miter": 16.1})

    def test_fail_closed(self):
        cases = [
            (eq(fe_result("not_equivalent", frame=8)), "not equivalent"),
            (eq(fe_result("undecided")), "undecided"),
            (eq(fe_result("timeout")), "timeout"),                      # ABC's --timeout
            (eq(fe_result("error", why="gate build failed (time limit)", timed_out=("gate",))), "timeout"),
            (eq(fe_result("error", why="structural precondition failed: x")), "error"),
            (eq(None, 124), "timeout"),                                 # eq_job.sh's limit on the whole checker
            (eq(None, 137), "timeout"),
            (eq(None, 2), "error"),                                     # no result.json
            (eq(fe_result(), 124), "timeout"),
            (eq(fe_result(exit_code=1)), "error"),                      # inconsistent record
            (eq(fe_result(passed=False)), "error"),
            (eq(fe_result(passed="true")), "error"),
            (eq(fe_result(), 1), "error"),                              # process exit differs from the record
            (eq(fe_result("maybe", exit_code=0, passed=True)), "error"),
        ]
        for r, verdict in cases:
            self.assertEqual(r["verdict"], verdict, r["reason"])
            self.assertIs(r["pass"], False, r)
        r = eq(fe_result("not_equivalent", frame=8))
        self.assertEqual(r["cex_frame"], 8)
        self.assertIn("frame 8", r["reason"])
        fe = fe_result()
        fe["case"]["verdict"] = "not_equivalent"
        self.assertEqual(eq(fe)["verdict"], "error")

    def test_schema_mismatch(self):
        for schema in ("pe-formal-eq/2", None):
            fe = fe_result(schema=schema)
            if schema is None:
                del fe["schema"]
            r = eq(fe)
            self.assertEqual((r["verdict"], r["pass"]), ("error", False))
            self.assertIn("schema", r["reason"])

    def test_sha_mismatch(self):
        bad = "9" * 64
        for kw, word in (({"netlist_source": bad}, "netlist"), ({"netlist": bad}, "netlist copy"),
                         ({"core": bad}, "core"), ({"top": bad}, "src/project.v"),
                         ({"pdk_root": "/other/pdk"}, "PDK root"), ({"variant": "base"}, "variant")):
            r = eq(fe_result(**kw))
            self.assertEqual((r["verdict"], r["pass"]), ("error", False), kw)
            self.assertIn(word, r["reason"])
            self.assertEqual(len(r["input_mismatches"]), 1, r["input_mismatches"])
        for role in ("netlist", "core", "project_v", "job", "checker"):
            exp = dict(EXPECT)
            exp[role] = "7" * 64 if role not in ("job",) else "2"
            r = eq(fe_result(), expect=exp)
            self.assertEqual((r["verdict"], r["pass"]), ("error", False), role)
        exp = dict(EXPECT, netlist=None)                            # the job could not hash its netlist
        self.assertEqual(eq(fe_result(), expect=exp)["verdict"], "error")
        # a mismatch never turns a failure into something else: it is reported as an error
        r = eq(fe_result("not_equivalent", core=bad))
        self.assertEqual(r["verdict"], "error")

    def test_invalid_result_json(self):
        import tempfile
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "result.json")
            self.assertEqual(GATES.load_result(p), (None, "formal_eq wrote no result.json"))
            for text in ("{", "[1, 2]"):
                with open(p, "w") as f:
                    f.write(text)
                fe, problem = GATES.load_result(p)
                self.assertIsNone(fe)
                r = eq(fe, 0, problem=problem)
                self.assertEqual((r["verdict"], r["pass"]), ("error", False))
                self.assertIn("result.json", r["reason"])
        finally:
            import shutil
            shutil.rmtree(d)

    def test_retry_policy(self):
        R = GATES.eq_should_retry
        self.assertTrue(R(eq(fe_result("timeout")), 1, 3))           # timeout -> retry
        self.assertTrue(R(eq(None, 124), 2, 3))
        self.assertFalse(R(eq(fe_result("timeout")), 3, 3))          # the third timeout is final
        for fe in (fe_result("undecided"), fe_result("not_equivalent", frame=3), fe_result("error", why="x"),
                   fe_result()):
            self.assertFalse(R(eq(fe), 1, 3), fe["verdict"])        # undecided / not equivalent -> final
        self.assertFalse(R(None, 1, 3))

    def test_eq_collect(self):
        """gates.py eq-collect (eq_job.sh) on real files: PASS, then a changed netlist and a core
        whose sha256 differs from the track's record."""
        import hashlib
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        try:
            files = {}
            for name, text in (("sub/top.v", NETLIST % (chain(1), 1)), ("core.v", "// diet4.json\n"),
                               ("tree/src/project.v", "module p; endmodule\n"), ("fe/eq_check.py", "#\n")):
                path = os.path.join(d, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(text)
                files[name] = (path, hashlib.sha256(text.encode()).hexdigest())
            fe = fe_result(netlist_source=files["sub/top.v"][1], netlist=files["sub/top.v"][1],
                           core=files["core.v"][1], top=files["tree/src/project.v"][1], pdk_root="/w/pdk")
            fe["tool_files"]["formal_eq/eq_check.py"] = files["fe/eq_check.py"][1]
            fe["tools"]["yosys_abc"] = os.path.join(d, "no-such-abc")
            fe_path = os.path.join(d, "out", "formal_eq", "result.json")
            os.makedirs(os.path.dirname(fe_path))

            def run(fe, nl_sha=files["sub/top.v"][1], core_sha=files["core.v"][1], outer=0):
                with open(fe_path, "w") as f:
                    json.dump(fe, f)
                argv = ["eq-collect", "--fe-result", fe_path, "--out", os.path.join(d, "out"),
                        "--netlist", files["sub/top.v"][0], "--netlist-sha", nl_sha, "--core", files["core.v"][0],
                        "--core-sha", core_sha, "--tree", os.path.join(d, "tree"), "--variant", "diet4",
                        "--pdk-root", "/w/pdk", "--pdk-source", "test", "--checker", files["fe/eq_check.py"][0],
                        "--job", "1", "--limit", "3600", "--outer-exit", str(outer), "--wall", "90"]
                import contextlib
                import io
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(GATES.main(argv), 0)
                with open(os.path.join(d, "out", "result.json")) as f:
                    return json.load(f)

            r = run(fe)
            self.assertEqual((r["verdict"], r["pass"], r["job_id"]), ("equivalent", True, "1"), r["reason"])
            self.assertEqual(r["checker"]["sha256"], files["fe/eq_check.py"][1])
            self.assertEqual(r["clock_depth"]["ff_min"], 1)
            self.assertTrue(str(r["formal_eq"]["tools"]["abc_version"]).startswith("unavailable"))
            r = run(fe, nl_sha="1" * 64)                               # the netlist changed during the job
            self.assertEqual((r["verdict"], r["pass"]), ("error", False))
            self.assertIn("changed during the job", r["reason"])
            r = run(fe, core_sha="2" * 64)                             # not the track's core
            self.assertEqual((r["verdict"], r["pass"]), ("error", False))
            self.assertIn("the track records", r["reason"])
            r = run(dict(fe, schema="pe-formal-eq/0"))                # schema mismatch
            self.assertEqual((r["verdict"], r["pass"]), ("error", False))
        finally:
            shutil.rmtree(d)

    def test_eq_job_script(self):
        """eq_job.sh end to end with a stand-in for formal_eq/eq_check.py: run from a copy in
        another directory (Slurm runs a spooled copy), it must find the checker and gates.py of
        the tree PE_OPT_HARNESS names, pass the job's inputs, and record the verdict."""
        import shutil
        import subprocess
        import tempfile
        d = tempfile.mkdtemp()
        try:
            tree = os.path.join(d, "tree")
            os.makedirs(os.path.join(tree, "tools", "opt"))
            os.makedirs(os.path.join(tree, "formal_eq"))
            for f in ("gates.py", "clockdepth.py"):
                shutil.copy(os.path.join(HERE, f), os.path.join(tree, "tools", "opt", f))
            with open(os.path.join(d, "template.json"), "w") as f:
                json.dump(fe_result(), f)
            with open(os.path.join(tree, "formal_eq", "eq_check.py"), "w") as f:
                f.write(FAKE_CHECKER)
            spool = os.path.join(d, "spool")
            os.makedirs(spool)
            shutil.copy(os.path.join(HERE, "eq_job.sh"), os.path.join(spool, "slurm_script"))
            build = os.path.join(d, "build")
            os.makedirs(os.path.join(build, "src"))
            files = {"nl": os.path.join(d, "sub", "top.v"), "core": os.path.join(d, "core.v"),
                     "top": os.path.join(build, "src", "project.v")}
            os.makedirs(os.path.dirname(files["nl"]))
            for k, text in (("nl", NETLIST % (chain(1), 1)), ("core", "// diet4\n"), ("top", "module p; endmodule\n")):
                with open(files[k], "w") as f:
                    f.write(text)
            env = {k: v for k, v in os.environ.items() if k not in ("PE_EQ_CHECK", "SLURM_JOB_ID")}
            env.update(PE_OPT_HARNESS=os.path.join(tree, "tools", "opt"), PE_OPT_PY=sys.executable, TMPDIR=d,
                       FAKE_TEMPLATE=os.path.join(d, "template.json"), PYTHONDONTWRITEBYTECODE="1")
            for verdict, want in (("equivalent", "equivalent"), ("timeout", "timeout"),
                                  ("not_equivalent", "not equivalent")):
                env["FAKE_VERDICT"] = verdict
                out = os.path.join(d, "out-" + verdict)
                r = subprocess.run(["bash", os.path.join(spool, "slurm_script"), files["nl"], files["core"], "-", build,
                                    out, "60", "diet4", "/w/sram-flow/pdk", "test"], env=env, cwd=spool,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                self.assertEqual(r.returncode, 0, r.stdout)
                with open(os.path.join(out, "result.json")) as f:
                    rec = json.load(f)
                self.assertEqual((rec["verdict"], rec["pass"]), (want, verdict == "equivalent"), rec["reason"])
                self.assertEqual(rec["checker"]["path"], os.path.join(tree, "formal_eq", "eq_check.py"))
                self.assertEqual(rec["formal_eq"]["argv"][:3], ["check", "--netlist", files["nl"]])
                self.assertTrue(os.path.exists(os.path.join(out, "formal_eq", "result.json")))
                self.assertEqual([n for n in os.listdir(d) if n.startswith("pe-opt-eq.")], [])  # local dir removed
        finally:
            shutil.rmtree(d)

    def test_driver_retry(self):
        """Driver.poll_promos() on a finished equivalence job: a time limit is resubmitted
        (attempt + 1) until the third job; undecided and not equivalent are final at once."""
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        env = {k: os.environ.get(k) for k in ("PE_WORK", "PE_OPT_ROOT")}
        try:
            os.environ["PE_WORK"], os.environ["PE_OPT_ROOT"] = d, os.path.join(d, "opt")
            sys.modules.pop("driver", None)
            import driver as D
            cases = [(fe_result("timeout"), 1, True), (fe_result("timeout"), 2, True),
                     (fe_result("timeout"), D.MAX_ATTEMPTS, False), (fe_result("undecided"), 1, False),
                     (fe_result("not_equivalent", frame=4), 1, False), (fe_result(), 1, False)]
            for n, (fe, attempt, retry) in enumerate(cases):
                shutil.rmtree(os.path.join(d, "opt"), ignore_errors=True)
                drv = D.Driver(dry_run=False)
                pid = "p%03d-diet4_6x4" % n
                drv.emit("track_new", track="tk", name="diet4_6x4", kind="6x4", gl_variant="diet4", period=20.0)
                drv.emit("promo_new", pid=pid, uid="tk#1", track="tk", run_dir=os.path.join(d, "opt", "runs", "r"))
                for stage, res in (("full", {"legal": True}), ("precheck", {"pass": True}), ("gl", {"pass": True})):
                    drv.emit("promo_submit", pid=pid, stage=stage, job_id="10", attempt=1)
                    drv.emit("promo_done", pid=pid, stage=stage, result=res)
                out = os.path.join(d, "opt", "promotions", pid, "eq")
                os.makedirs(out)
                rec = eq(fe)
                rec["job_id"] = "11"
                with open(os.path.join(out, "result.json"), "w") as f:
                    json.dump(rec, f)
                drv.emit("promo_submit", pid=pid, stage="eq", job_id="11", attempt=attempt, out=out)
                calls = []
                drv.submit_signoff = lambda p, stage, att: calls.append((p["pid"], stage, att)) or "12"
                import contextlib
                import io
                with contextlib.redirect_stdout(io.StringIO()):
                    drv.poll_promos({})
                st = drv.state.promos[pid]["stages"]["eq"]
                if retry:
                    self.assertEqual(calls, [(pid, "eq", attempt + 1)], fe["verdict"])
                    self.assertEqual(st["state"], "submitted")
                else:
                    self.assertEqual(calls, [], (fe["verdict"], attempt))
                    self.assertEqual((st["state"], st["result"]["verdict"]), ("done", rec["verdict"]))
                    verdict, passed = GATES.promotion_verdict(drv.state.promos[pid]["stages"])
                    self.assertIs(passed, rec["verdict"] == "equivalent", verdict)
        finally:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(d)

    def stages(self, pc=True, gl=True, eqv="equivalent", legal=True):
        st = {"full": {"state": "done", "result": {"legal": legal, "blockers": [] if legal else ["LVS errors 3"]}}}
        for name, ok in (("precheck", pc), ("gl", gl)):
            if ok is not None:
                st[name] = {"state": "done", "result": {"pass": ok, "n_checks": 9, "n_fail": 0 if ok else 1,
                                                        "tests": 46, "failed": 0 if ok else 8}}
        if eqv is not None:
            st["eq"] = {"state": "done", "result": {"pass": eqv == "equivalent", "verdict": eqv, "reason": eqv}}
        return st

    def test_verdict(self):
        V = GATES.promotion_verdict
        self.assertEqual(V(self.stages()), ("PASS", True))
        for kw in ({"pc": False}, {"gl": False}, {"eqv": "undecided"}, {"eqv": "timeout"},
                   {"eqv": "not equivalent"}, {"eqv": "error"}, {"legal": False}):
            text, passed = V(self.stages(**kw))
            self.assertIs(passed, False, kw)
            self.assertTrue(text.startswith("FAIL"), text)
        text, passed = V(self.stages(eqv=None))               # promotion from before the stage existed
        self.assertEqual((text, passed), ("sign-off in progress (equivalence)", None))
        text, passed = V(self.stages(gl=False, eqv=None))     # a failed stage decides at once
        self.assertIs(passed, False)
        self.assertIn("pending: equivalence", text)
        st = self.stages()
        st["gl"]["result"] = {"pass": None, "not_run": True}
        self.assertIsNone(V(st)[1])
        self.assertTrue(V(st)[0].startswith("INCOMPLETE"))
        self.assertEqual(V({"full": {"state": "submitted"}}), ("full run submitted", None))
        self.assertTrue(GATES.eq_text({"state": "done", "result": {"pass": False, "verdict": "undecided",
                                                                    "abc_s": 1466}}).startswith("FAIL (undecided"))

    def test_report_promotion_row(self):
        ev = [{"t": "0", "ev": "track_new", "track": "tk", "name": "dor15", "kind": "freq", "period": 15.0},
              {"t": "0", "ev": "trial_new", "uid": "tk#1", "track": "tk", "number": 1,
               "knobs": {"CTS_MAX_SLEW": 0.75}},
              {"t": "0", "ev": "promo_new", "pid": "p001-dor15", "uid": "tk#1", "track": "tk", "run_dir": "/x"}]
        st = self.stages(eqv=None)
        for stage in ("full", "precheck", "gl"):
            ev.append({"t": "0", "ev": "promo_submit", "pid": "p001-dor15", "stage": stage, "job_id": "1%d" % len(ev)})
            ev.append({"t": "0", "ev": "promo_done", "pid": "p001-dor15", "stage": stage,
                       "result": st[stage]["result"]})
        ev.append({"t": "0", "ev": "promo_submit", "pid": "p001-dor15", "stage": "eq", "job_id": "99"})
        state = State(ev)
        row = REPORT.promo_rows(state, list(state.promos.values()))[0]
        self.assertEqual((row["eq"], row["eq_jobs"], row["passed"]), ("submitted", "99", None))
        self.assertEqual(row["outside"], "CTS_MAX_SLEW=0.75")
        state.apply({"t": "1", "ev": "promo_done", "pid": "p001-dor15", "stage": "eq",
                     "result": {"pass": False, "verdict": "undecided", "abc_s": 1466}})
        row = REPORT.promo_rows(state, list(state.promos.values()))[0]
        self.assertIs(row["passed"], False)
        self.assertIn("equivalence", row["verdict"])
        self.assertIn("| FAIL (undecided after 1466 s) (99) |", REPORT.promo_line(row))


NETLIST = """module top (clk, d, q);
 input clk;
 sg13cmos5l_buf_8 b1 (.A(clk), .X(n1));
 sg13cmos5l_buf_8 b2 (.A(n1), .X(n2));
 sg13cmos5l_dfrbpq_1 ff1 (.CLK(n1), .D(d), .RESET_B(r), .Q(q1));
 sg13cmos5l_dfrbpq_1 \\core.ff2  (.CLK(n2), .D(q1), .RESET_B(r), .Q(q));
 sg13cmos5l_fill_1 f1 ();
%s
 RM_IHPSG13_1P_64x16_c2 \\core.sram  (.A_CLK(s%d), .A_DIN({q1, q}), .A_DOUT({o1, o0}));
endmodule
"""


def chain(n):
    return "\n".join(" sg13cmos5l_buf_1 c%d (.A(%s), .X(s%d));" % (i, "clk" if i == 1 else "s%d" % (i - 1), i)
                     for i in range(1, n + 1))


class ClockDepth(unittest.TestCase):
    def test_depths_and_warning(self):
        for n, warn in ((3, False), (1 + CD.WARN_EXCESS, True)):
            ff, sram, notes = CD.depths(CD.parse(NETLIST % (chain(n), n)))
            self.assertEqual((sorted(ff), sram, notes), ([1, 2], [n], []))
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".v", delete=False) as f:
            f.write(NETLIST % (chain(1 + CD.WARN_EXCESS), 1 + CD.WARN_EXCESS))
        try:
            s = CD.summary(f.name)
        finally:
            os.remove(f.name)
        self.assertEqual((s["ff_min"], s["ff_max"], s["sram_max"], s["sram_excess"], s["warn"]),
                         (1, 2, 1 + CD.WARN_EXCESS, CD.WARN_EXCESS, True))
        m = OBJ.flatten({}, {"clock_depth": s})
        self.assertTrue(m["clk_warn"])
        self.assertIn("WARN", CD.text(m))


if __name__ == "__main__":
    unittest.main(verbosity=2)
