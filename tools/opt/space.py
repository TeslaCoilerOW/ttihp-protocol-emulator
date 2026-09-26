# SPDX-License-Identifier: Apache-2.0
"""Search space of the physical-design optimizer (docs/optimization.md).

Every knob is an ordinary LibreLane 3.1.0.dev3 configuration variable that a
Tiny Tapeout project may set in src/config.json above the template's "DO NOT
CHANGE" line, or a floorplan file of floorplans/ (macro placement only). Names,
types and defaults were checked against the librelane package inside
librelane-3.1.0.dev3.sif (steps/pyosys.py, steps/openroad.py,
steps/common_variables.py, config/flow.py, flows/classic.py and
scripts/openroad/*.tcl); the file that defines each variable is listed in
KNOBS[...]["source"].

Never searched (kept exactly as in src/config.json and the TT template):
die size / tiles, CLOCK_PERIOD, the PDN keys (FP_PDN_*), pin placement
(FP_IO_*, the DEF template), RT_MAX_LAYER, everything below "DO NOT CHANGE",
and every timing-constraint variable (IO_DELAY_CONSTRAINT,
CLOCK_UNCERTAINTY_CONSTRAINT, CLOCK_TRANSITION_CONSTRAINT, OUTPUT_CAP_LOAD,
MAX_TRANSITION_CONSTRAINT, MAX_CAPACITANCE_CONSTRAINT, TIME_DERATING_CONSTRAINT,
the SDC files): changing those changes what is measured rather than the design.
MAX_FANOUT_CONSTRAINT is searched only at or below the PDK value 10, because it
also becomes the SDC set_max_fanout limit that the fan-out violation count uses.

GRT_ADJUSTMENT is not searched: scripts/openroad/common/set_layer_adjustments.tcl
applies it to every layer and then overrides each layer with
GRT_LAYER_ADJUSTMENTS, which the IHP PDK config sets for all five routing
layers, so GRT_ADJUSTMENT has no effect here. The per-layer values are searched
instead.

Standard library only (Python 3.6+); optuna is used only through the `trial`
object passed to suggest().
"""

import copy

# Floorplans (floorplans/<id>.json, 8x4) and the horizontal halo values that keep
# them legal. fp8_base/_trk: paired macros 32.96 um apart, so the halo must be
# exactly 16.48 (half the gap: halos meet, no std-cell rows and hence no short
# VPWR/VGND Metal4 straps between the pair -- docs/drc-triage.md section 6; any
# larger value fails macros/check_macro_floorplan.py "closer than two halos").
# fp8_wide(_trk): gaps 100.4 um; one lattice stripe pair runs through the middle
# of every gap (macro x + 286.99), so every row segment in a gap is crossed by a
# full-height stripe for any halo below ~50 um. fp8_spread_trk: gaps >= 167.8 um
# (two stripes per gap); the first top macro (x 213.36) must stay clear of the TT
# I/O pins at x <= 191.04 plus the halo, so the halo must be <= 22.32.
FLOORPLANS = ["fp8_base", "fp8_base_trk", "fp8_wide", "fp8_wide_trk", "fp8_spread_trk"]
HALO_FIXED = {"fp8_base": 16.48, "fp8_base_trk": 16.48}
HALO_WIDE = [16.48, 10.0, 12.5, 20.0, 25.0]
HALO_SPREAD = [16.48, 10.0, 12.5, 20.0]
REPO_HALO_H = 16.48  # src/config.json FP_MACRO_HORIZONTAL_HALO

SYNTH_STRATEGIES = ["AREA 0", "AREA 1", "AREA 2", "AREA 3",
                    "DELAY 0", "DELAY 1", "DELAY 2", "DELAY 3", "DELAY 4"]

# name -> definition. kind: cat (choices) | int (lo, hi, step) | float (lo, hi, step).
# default: the value that reproduces the design of record (src/config.json, or the
# LibreLane/PDK default recorded in a resolved.json of the design of record).
# key: LibreLane variable (None: composite, see materialize()).
KNOBS = {
    # ---- floorplan
    "floorplan": dict(kind="cat", choices=FLOORPLANS, default="fp8_base", key=None,
                      source="floorplans/*.json (MACROS.<macro>.instances)",
                      why="Macro placement sets the pin-access channels, the routing detours around the "
                          "SRAMs and the reset/clock wire lengths across the 1724 um die."),
    "halo_h_wide": dict(kind="cat", choices=HALO_WIDE, default=16.48, key="FP_MACRO_HORIZONTAL_HALO",
                        when=("floorplan", ("fp8_wide", "fp8_wide_trk")), source="steps/openroad.py",
                        why="Horizontal keep-out around each macro; only floorplans with wide gaps may use "
                            "values other than 16.48 without creating short power straps."),
    "halo_h_spread": dict(kind="cat", choices=HALO_SPREAD, default=16.48, key="FP_MACRO_HORIZONTAL_HALO",
                          when=("floorplan", ("fp8_spread_trk",)), source="steps/openroad.py",
                          why="As halo_h_wide; <= 22.32 keeps the first top macro clear of the I/O pins."),
    "FP_MACRO_VERTICAL_HALO": dict(kind="cat", choices=[10.0, 5.0, 15.0, 20.0], default=10.0,
                                   key="FP_MACRO_VERTICAL_HALO", source="steps/openroad.py",
                                   why="Keep-out above/below the macros = the pin-access channel of the "
                                       "SRAM signal pins (all on the edge facing the logic)."),
    "density": dict(kind="int", lo=50, hi=72, step=1, default=60, key="PL_TARGET_DENSITY_PCT",
                    source="steps/openroad.py (GlobalPlacement)",
                    why="Global-placement target density: wire length versus routing congestion."),
    # ---- synthesis
    "SYNTH_STRATEGY": dict(kind="cat", choices=SYNTH_STRATEGIES, default="AREA 0", key="SYNTH_STRATEGY",
                           source="steps/pyosys.py",
                           why="ABC mapping script; DELAY scripts shorten logic depth on the reset and "
                               "register paths at an area cost."),
    "abc_fine_tune": dict(kind="cat", choices=["none", "buffering", "sizing"], default="none", key=None,
                          source="steps/pyosys.py, scripts/pyosys/construct_abc_script.py",
                          why="SYNTH_ABC_BUFFERING (buffer -N fanout; upsize; dnsize) or SYNTH_SIZING; the "
                              "script applies buffering first when both are set, so they are one choice."),
    "MAX_FANOUT_CONSTRAINT": dict(kind="cat", choices=[10, 8, 6], default=10, key="MAX_FANOUT_CONSTRAINT",
                                  source="config/flow.py (PDK variable, IHP value 10); scripts/base.sdc",
                                  why="Fan-out limit for ABC buffering and repair_design; only values <= the "
                                      "PDK's 10 (stricter SDC limit) are searched."),
    # ---- placement
    "PL_TIMING_DRIVEN": dict(kind="cat", choices=[False, True], default=False, key="PL_TIMING_DRIVEN",
                             source="steps/openroad.py (GlobalPlacement)",
                             why="Timing-driven global placement (net weights from STA)."),
    "PL_ROUTABILITY_DRIVEN": dict(kind="cat", choices=[True, False], default=True, key="PL_ROUTABILITY_DRIVEN",
                                  source="steps/openroad.py (GlobalPlacement)",
                                  why="Routability-driven inflation during global placement."),
    "gpl_pad": dict(kind="cat", choices=[0, 2, 4], default=0, key="GPL_CELL_PADDING",
                    source="steps/openroad.py (PDK variable, IHP value 0)",
                    why="Global-placement cell padding in sites (split over both sides): pin access."),
    "dpl_pad": dict(kind="cat", choices=[0, 2], default=0, key="DPL_CELL_PADDING", when=("gpl_pad", (2, 4)),
                    source="steps/common_variables.py (PDK variable, IHP value 0)",
                    why="Detailed-placement padding; must not exceed GPL_CELL_PADDING."),
    # ---- design repair (repair_design after GPL)
    "DESIGN_REPAIR_MAX_WIRE_LENGTH": dict(kind="cat", choices=[0, 150, 300, 500, 800], default=0,
                                          key="DESIGN_REPAIR_MAX_WIRE_LENGTH", source="steps/openroad.py",
                                          why="Buffer wires longer than this (um); 0 = off. The reset and "
                                              "enable nets span the die."),
    "DESIGN_REPAIR_MAX_SLEW_PCT": dict(kind="int", lo=10, hi=50, step=5, default=20,
                                       key="DESIGN_REPAIR_MAX_SLEW_PCT", source="steps/openroad.py",
                                       why="Slew margin for repair_design (post-route slow corner shows "
                                           "~0.9 ns slews on buf_1 fan-out buffers)."),
    "DESIGN_REPAIR_MAX_CAP_PCT": dict(kind="int", lo=10, hi=50, step=5, default=20,
                                      key="DESIGN_REPAIR_MAX_CAP_PCT", source="steps/openroad.py",
                                      why="Capacitance margin for repair_design."),
    "RUN_POST_GRT_DESIGN_REPAIR": dict(kind="cat", choices=[False, True], default=False,
                                       key="RUN_POST_GRT_DESIGN_REPAIR", source="flows/classic.py",
                                       why="repair_design again with global-route parasitics "
                                           "(OpenROAD.RepairDesignPostGRT; marked experimental)."),
    # ---- resizer timing repair
    "PL_RESIZER_SETUP_SLACK_MARGIN": dict(kind="float", lo=0.0, hi=6.0, step=0.05, default=0.05,
                                          key="PL_RESIZER_SETUP_SLACK_MARGIN", source="steps/openroad.py; "
                                          "scripts/openroad/rsz_timing_postcts.tcl",
                                          why="Post-CTS repair_timing treats slack below this as violating. "
                                              "Its log reports 'No setup violations found' although routed "
                                              "slow-corner WS is negative: placement-estimated parasitics are "
                                              "optimistic, so a margin of ns is needed to act at all."),
    "pl_hold": dict(kind="cat", choices=[0.1, 0.05, 0.15, 0.2], default=0.1, key="PL_RESIZER_HOLD_SLACK_MARGIN",
                    source="steps/openroad.py",
                    why="Post-CTS hold margin (repo 0.1; 0/0 produced a fast-corner hold violation in the "
                        "sweep). Hold buffers sit on the reset and input paths."),
    "PL_RESIZER_SETUP_MAX_UTIL_PCT": dict(kind="cat", choices=[None, 65, 75], default=None,
                                          key="PL_RESIZER_SETUP_MAX_UTIL_PCT", source="steps/openroad.py",
                                          why="Utilization cap (-max_utilization) for setup repair; bounds "
                                              "the area cost of large margins."),
    "RUN_POST_GRT_RESIZER_TIMING": dict(kind="cat", choices=[False, True], default=False,
                                        key="RUN_POST_GRT_RESIZER_TIMING", source="flows/classic.py",
                                        why="repair_timing again with global-route parasitics "
                                            "(OpenROAD.ResizerTimingPostGRT; marked experimental)."),
    "GRT_RESIZER_SETUP_SLACK_MARGIN": dict(kind="float", lo=0.0, hi=4.0, step=0.05, default=0.025,
                                           key="GRT_RESIZER_SETUP_SLACK_MARGIN",
                                           when=("RUN_POST_GRT_RESIZER_TIMING", (True,)),
                                           source="steps/openroad.py; scripts/openroad/rsz_timing_postgrt.tcl",
                                           why="Setup margin of the post-GRT repair (only read by that step)."),
    "grt_hold": dict(kind="cat", choices=[0.05, 0.02, 0.1], default=0.05, key="GRT_RESIZER_HOLD_SLACK_MARGIN",
                     when=("RUN_POST_GRT_RESIZER_TIMING", (True,)),
                     source="steps/openroad.py; scripts/openroad/rsz_timing_postgrt.tcl",
                     why="Hold margin of the post-GRT repair (only read by that step; repo 0.05)."),
    # ---- clock tree
    "CTS_SINK_CLUSTERING_SIZE": dict(kind="cat", choices=[None, 10, 16, 25, 35], default=None,
                                     key="CTS_SINK_CLUSTERING_SIZE", source="steps/openroad.py (CTS)",
                                     why="Sinks per leaf cluster: clock latency/skew, which the input and "
                                         "reset paths see directly."),
    "CTS_SINK_CLUSTERING_MAX_DIAMETER": dict(kind="cat", choices=[None, 30, 60, 100], default=None,
                                             key="CTS_SINK_CLUSTERING_MAX_DIAMETER",
                                             source="steps/openroad.py (CTS)", why="Leaf cluster diameter (um)."),
    "CTS_MAX_SLEW": dict(kind="cat", choices=[None, 0.4, 0.75, 1.2], default=None, key="CTS_MAX_SLEW",
                         source="steps/openroad.py (CTS); scripts/openroad/cts.tcl",
                         why="CTS characterization slew limit (ns); unset = from the lib (2.5 ns)."),
    "CTS_MAX_CAP": dict(kind="cat", choices=[None, 0.1, 0.2], default=None, key="CTS_MAX_CAP",
                        source="steps/openroad.py (CTS); scripts/openroad/cts.tcl",
                        why="CTS characterization capacitance limit (pF); unset = from the lib (0.3 pF)."),
    "CTS_CLK_MAX_WIRE_LENGTH": dict(kind="cat", choices=[0, 250, 500], default=0, key="CTS_CLK_MAX_WIRE_LENGTH",
                                    source="steps/openroad.py (CTS); scripts/openroad/cts.tcl",
                                    why="repair_clock_nets maximum wire length (um); 0 = off."),
    "CTS_OBSTRUCTION_AWARE": dict(kind="cat", choices=[None, True], default=None, key="CTS_OBSTRUCTION_AWARE",
                                  source="steps/openroad.py (CTS)",
                                  why="Keep clock buffers off the macros (less legalizer displacement)."),
    # ---- routing
    "grt_adj_m2": dict(kind="cat", choices=[0.0, 0.1, 0.2, 0.3], default=0.0, key=None,
                       source="steps/common_variables.py GRT_LAYER_ADJUSTMENTS (PDK 0,0,0,0,0)",
                       why="Metal2 capacity reduction in global routing (spreads demand; DRT convergence)."),
    "grt_adj_m3": dict(kind="cat", choices=[0.0, 0.1, 0.2, 0.3], default=0.0, key=None,
                       source="as grt_adj_m2", why="Metal3 capacity reduction (run2's overflow was on Metal3)."),
    "grt_adj_m4": dict(kind="cat", choices=[0.0, 0.1, 0.2], default=0.0, key=None,
                       source="as grt_adj_m2", why="Metal4 capacity reduction (Metal4 also carries the PDN)."),
    "GRT_MACRO_EXTENSION": dict(kind="cat", choices=[0, 1], default=0, key="GRT_MACRO_EXTENSION",
                                source="steps/common_variables.py", why="GCells added around macro blockages."),
}

# Knobs removed from the search after launch, with the only value still accepted in
# stored knob sets (older trials recorded them). DRT_OPT_ITERS was searched over
# {64, 80}: both trials with 80 (jobs 23995145, 23995228) stopped in
# OpenROAD.DetailedRouting with "drt.tcl, 107 Wrong number of arguments :utl::warn"
# from the detailed_route command of the OpenROAD build in the SIF; 64 is the
# LibreLane default and the only value used since.
RETIRED = {"DRT_OPT_ITERS": 64}

ORDER = list(KNOBS)


def active(name, knobs):
    cond = KNOBS[name].get("when")
    if not cond:
        return True
    parent, values = cond
    return parent in knobs and knobs[parent] in values


def defaults():
    """The knob values of the design of record (baseline trial)."""
    out = {}
    for n in ORDER:
        if active(n, out):
            out[n] = KNOBS[n]["default"]
    return out


def suggest(trial):
    """Define-by-run sampling with an optuna Trial. Returns {knob: value} (active knobs only)."""
    out = {}
    for n in ORDER:
        if not active(n, out):
            continue
        k = KNOBS[n]
        if k["kind"] == "cat":
            # dpl_pad is only active for gpl_pad >= 2, so all its choices (0, 2) respect
            # DPL_CELL_PADDING <= GPL_CELL_PADDING; choices never depend on other knobs
            # (optuna requires a fixed distribution per name).
            out[n] = trial.suggest_categorical(n, k["choices"])
        elif k["kind"] == "int":
            out[n] = trial.suggest_int(n, k["lo"], k["hi"], step=k["step"])
        else:
            out[n] = round(trial.suggest_float(n, k["lo"], k["hi"], step=k["step"]), 6)
    return out


def complete(partial):
    """Fill a partial knob dict (e.g. a designed trial) with defaults; validates choices/ranges."""
    out = {}
    for n in ORDER:
        if not active(n, out):
            continue
        v = partial.get(n, KNOBS[n]["default"])
        k = KNOBS[n]
        if k["kind"] == "cat" and v not in k["choices"]:
            raise ValueError("knob %s: %r not in %r" % (n, v, k["choices"]))
        if k["kind"] in ("int", "float") and not (k["lo"] - 1e-9 <= v <= k["hi"] + 1e-9):
            raise ValueError("knob %s: %r outside [%s, %s]" % (n, v, k["lo"], k["hi"]))
        out[n] = v
    for n, v in RETIRED.items():
        if n in partial and partial[n] != v:
            raise ValueError("knob %s=%r is retired (only %r is accepted)" % (n, partial[n], v))
    unknown = set(partial) - set(KNOBS) - set(RETIRED)
    if unknown:
        raise ValueError("unknown knobs %s" % sorted(unknown))
    return out


def _num(x):
    x = float(x)
    return int(x) if x == int(x) else round(x, 6)


def materialize(knobs):
    """knobs -> (snapshot parameters for make_snapshot, LibreLane overrides).

    Values equal to the design-of-record's effective value are left out, so the
    baseline has no overrides and identical effective configurations get the same
    run id (make_snapshot hashes the overrides)."""
    kn = complete(knobs)
    snap = {"floorplan": kn["floorplan"], "density": kn["density"], "pl_hold": kn["pl_hold"],
            "grt_hold": kn.get("grt_hold", KNOBS["grt_hold"]["default"])}
    ov = {}
    fp = kn["floorplan"]
    halo = HALO_FIXED.get(fp)
    if halo is None:
        halo = kn["halo_h_wide"] if "halo_h_wide" in kn else kn["halo_h_spread"]
    if abs(halo - REPO_HALO_H) > 1e-9:
        ov["FP_MACRO_HORIZONTAL_HALO"] = _num(halo)
    for n in ("FP_MACRO_VERTICAL_HALO", "SYNTH_STRATEGY", "MAX_FANOUT_CONSTRAINT", "PL_TIMING_DRIVEN",
              "PL_ROUTABILITY_DRIVEN", "DESIGN_REPAIR_MAX_WIRE_LENGTH", "DESIGN_REPAIR_MAX_SLEW_PCT",
              "DESIGN_REPAIR_MAX_CAP_PCT", "RUN_POST_GRT_DESIGN_REPAIR", "PL_RESIZER_SETUP_SLACK_MARGIN",
              "PL_RESIZER_SETUP_MAX_UTIL_PCT", "RUN_POST_GRT_RESIZER_TIMING", "GRT_RESIZER_SETUP_SLACK_MARGIN",
              "CTS_SINK_CLUSTERING_SIZE", "CTS_SINK_CLUSTERING_MAX_DIAMETER", "CTS_MAX_SLEW", "CTS_MAX_CAP",
              "CTS_CLK_MAX_WIRE_LENGTH", "CTS_OBSTRUCTION_AWARE", "GRT_MACRO_EXTENSION"):
        if n not in kn:
            continue
        v, d = kn[n], KNOBS[n]["default"]
        if v is None or v == d:
            continue
        key = KNOBS[n]["key"]
        ov[key] = _num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    if kn["abc_fine_tune"] == "buffering":
        ov["SYNTH_ABC_BUFFERING"] = True
    elif kn["abc_fine_tune"] == "sizing":
        ov["SYNTH_SIZING"] = True
    if kn["gpl_pad"]:
        ov["GPL_CELL_PADDING"] = kn["gpl_pad"]
        if kn.get("dpl_pad"):
            ov["DPL_CELL_PADDING"] = kn["dpl_pad"]
    if kn.get("RUN_POST_GRT_DESIGN_REPAIR") and kn["DESIGN_REPAIR_MAX_WIRE_LENGTH"]:
        ov["GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"] = kn["DESIGN_REPAIR_MAX_WIRE_LENGTH"]
    adj = [0.0, kn["grt_adj_m2"], kn["grt_adj_m3"], kn["grt_adj_m4"], 0.0]
    if any(adj):
        ov["GRT_LAYER_ADJUSTMENTS"] = [_num(a) for a in adj]
    return snap, ov


# ---- designed first-wave trials (one knob or one mechanism at a time, then combinations);
# the rest of each study is sampled by TPE.
FIRST_WAVE = [
    ("baseline (committed src/config.json)", {}),
    ("setup margin 2 ns", {"PL_RESIZER_SETUP_SLACK_MARGIN": 2.0}),
    ("setup margin 5 ns", {"PL_RESIZER_SETUP_SLACK_MARGIN": 5.0}),
    ("post-GRT timing repair, margin 1 ns", {"RUN_POST_GRT_RESIZER_TIMING": True,
                                             "GRT_RESIZER_SETUP_SLACK_MARGIN": 1.0}),
    ("SYNTH_STRATEGY DELAY 3", {"SYNTH_STRATEGY": "DELAY 3"}),
    ("SYNTH_STRATEGY DELAY 1", {"SYNTH_STRATEGY": "DELAY 1"}),
    ("ABC buffering", {"abc_fine_tune": "buffering"}),
    ("MAX_FANOUT_CONSTRAINT 6", {"MAX_FANOUT_CONSTRAINT": 6}),
    ("repair max wire 300 um", {"DESIGN_REPAIR_MAX_WIRE_LENGTH": 300}),
    ("repair slew/cap margins 40%", {"DESIGN_REPAIR_MAX_SLEW_PCT": 40, "DESIGN_REPAIR_MAX_CAP_PCT": 40}),
    ("timing-driven placement", {"PL_TIMING_DRIVEN": True}),
    ("CTS slew 0.75 ns, clusters of 16", {"CTS_MAX_SLEW": 0.75, "CTS_SINK_CLUSTERING_SIZE": 16}),
    ("floorplan fp8_base_trk", {"floorplan": "fp8_base_trk"}),
    ("floorplan fp8_wide_trk, halo 10", {"floorplan": "fp8_wide_trk", "halo_h_wide": 10.0}),
    ("density 52", {"density": 52}),
    ("density 68", {"density": 68}),
    ("GRT Metal2/Metal3 adjustment 0.2", {"grt_adj_m2": 0.2, "grt_adj_m3": 0.2}),
    ("hold margin 0.05", {"pl_hold": 0.05}),
    ("combination: margin 3, post-GRT, DELAY 3, wire 300",
     {"PL_RESIZER_SETUP_SLACK_MARGIN": 3.0, "RUN_POST_GRT_RESIZER_TIMING": True,
      "GRT_RESIZER_SETUP_SLACK_MARGIN": 1.0, "SYNTH_STRATEGY": "DELAY 3", "DESIGN_REPAIR_MAX_WIRE_LENGTH": 300}),
]

# For a variant core: the baseline and the combination; the driver adds the current
# best configurations of the primary track.
VARIANT_FIRST_WAVE = [FIRST_WAVE[0], FIRST_WAVE[-1], FIRST_WAVE[1]]


def distributions():
    """optuna distributions for every knob (used when rebuilding a study from the results store)."""
    import optuna.distributions as D
    out = {}
    for n in ORDER:
        k = KNOBS[n]
        if k["kind"] == "cat":
            out[n] = D.CategoricalDistribution(copy.copy(k["choices"]))
        elif k["kind"] == "int":
            out[n] = D.IntDistribution(k["lo"], k["hi"], step=k["step"])
        else:
            out[n] = D.FloatDistribution(k["lo"], k["hi"], step=k["step"])
    return out


def table_rows():
    """(knob, LibreLane key, values, default, rationale) for documentation."""
    rows = []
    for n in ORDER:
        k = KNOBS[n]
        if k["kind"] == "cat":
            vals = ", ".join("unset" if c is None else str(c) for c in k["choices"])
        else:
            vals = "%s..%s step %s" % (k["lo"], k["hi"], k["step"])
        key = k["key"] or {"floorplan": "MACROS instances", "abc_fine_tune": "SYNTH_ABC_BUFFERING / SYNTH_SIZING",
                           "grt_adj_m2": "GRT_LAYER_ADJUSTMENTS[1]", "grt_adj_m3": "GRT_LAYER_ADJUSTMENTS[2]",
                           "grt_adj_m4": "GRT_LAYER_ADJUSTMENTS[3]"}.get(n, n)
        cond = k.get("when")
        rows.append((n, key, vals, "unset" if k["default"] is None else str(k["default"]),
                     (("only if %s in %s. " % (cond[0], list(cond[1]))) if cond else "") + k["why"]))
    return rows
