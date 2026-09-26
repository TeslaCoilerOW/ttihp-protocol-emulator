#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the optimizer's search space, tracks and objective (no cluster,
no LibreLane, no optuna needed):

  python3 tools/opt/test_opt.py

They check the properties the driver relies on (docs/optimization.md):
  * the committed configuration maps to a knob set that reproduces it exactly;
  * knob values are absolute: a knob set gives the same effective configuration
    on any committed configuration that differs only in knob-controlled keys;
  * the 6x4 track's committed build equals what variants6x4/switch.py writes;
  * the 6x4 vertical-halo choices are the island-free ones of row_islands.py;
  * translation between the 8x4 and 6x4 spaces, and the per-corner fmax.
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
import objective as OBJ  # noqa: E402
import space as SPACE  # noqa: E402
import tracks as TR  # noqa: E402

DOR_CORE = os.path.join(REPO, "src", "protocol_emulator_core.v")


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
        cls.dor = TR.Track(REPO, "dor", "dor", DOR_CORE, "8x4", 20.0, "dor20", "base")

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
                                     period=20, threads=32)
            e2 = TR.effective_config(other, t.floorplans[kn["floorplan"]],
                                     *SPACE.materialize(kn, base2, other,
                                                        t.floorplans[kn["floorplan"]].get("config"), t.D),
                                     period=20, threads=32)
            self.assertEqual(TR.changes(strip(e1), strip(e2)), {}, kn)
            # and the knob set is recovered from its own effective configuration
            back = SPACE.base_knobs(e1, t.floorplans, t.D)
            self.assertEqual(SPACE.canonical(t.complete(back)), SPACE.canonical(kn))

    def test_unset_is_absent(self):
        """A knob at its LibreLane default leaves the key out of the effective configuration."""
        t = self.dor
        kn = t.complete({"PL_TIMING_DRIVEN": False, "CTS_SINK_CLUSTERING_SIZE": None, "SYNTH_STRATEGY": "AREA 0",
                         "grt_adj_m2": 0.0, "grt_adj_m4": 0.0})
        eff = t.effective(kn)
        for k in ("PL_TIMING_DRIVEN", "CTS_SINK_CLUSTERING_SIZE", "SYNTH_STRATEGY", "GRT_LAYER_ADJUSTMENTS"):
            self.assertNotIn(k, eff)
        self.assertEqual(t.materialize(kn)[1]["PL_TIMING_DRIVEN"], None)

    def test_frequency_track(self):
        t15 = TR.Track(REPO, "dor15", "freq", DOR_CORE, "8x4", 15.0, "dor15", "base")
        self.assertEqual(t15.diff_vs_repo({}), {"CLOCK_PERIOD": {"repo": 20, "run": 15}})
        self.assertEqual(t15.fixed, self.dor.fixed)
        self.assertNotEqual(t15.id, self.dor.id)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
