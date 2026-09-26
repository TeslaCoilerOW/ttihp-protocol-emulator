# SPDX-License-Identifier: Apache-2.0
"""Search space of the physical-design optimizer (docs/optimization.md).

Every knob is an ordinary LibreLane 3.1.0.dev3 configuration variable that a
Tiny Tapeout project may set in src/config.json above the template's "DO NOT
CHANGE" line, or a floorplan file of floorplans/ (macro placement only). Names,
types and LibreLane/PDK defaults were checked against the librelane package
inside librelane-3.1.0.dev3.sif (steps/pyosys.py, steps/openroad.py,
steps/common_variables.py, config/flow.py, flows/classic.py and
scripts/openroad/*.tcl) and against the resolved.json of a run without any of
these keys; the file that defines each variable is in KNOBS[...]["source"].

Knob values are absolute. A track's committed configuration (its "base": the
frozen src/config.json, with the variants6x4 overlay for the 6x4 track) maps to
one knob set (base_knobs()), and materialize() turns any knob set into the
make_snapshot parameters plus the LibreLane overrides that make the snapshot's
config.json carry exactly those values: a key is written when the knob value
differs from the committed value, and deleted (null override) when the knob
value is the LibreLane/PDK default and the committed config sets the key. The
same knob set therefore gives the same effective configuration whatever the
committed configuration is.

Never searched (kept exactly as in src/config.json and the TT template):
die size / tiles, the PDN keys (FP_PDN_*), pin placement (FP_IO_*, the DEF
template), RT_MAX_LAYER, everything below "DO NOT CHANGE", and every
timing-constraint variable (IO_DELAY_CONSTRAINT, CLOCK_UNCERTAINTY_CONSTRAINT,
CLOCK_TRANSITION_CONSTRAINT, OUTPUT_CAP_LOAD, MAX_TRANSITION_CONSTRAINT,
MAX_CAPACITANCE_CONSTRAINT, TIME_DERATING_CONSTRAINT, the SDC files): changing
those changes what is measured rather than the design. CLOCK_PERIOD is a
property of a track (20, 15 or 13.33 ns), not a knob. MAX_FANOUT_CONSTRAINT is
searched only at or below the PDK value 10, because it also becomes the SDC
set_max_fanout limit that the fan-out violation count uses.

GRT_ADJUSTMENT is not searched: scripts/openroad/common/set_layer_adjustments.tcl
applies it to every layer and then overrides each layer with
GRT_LAYER_ADJUSTMENTS, which the IHP PDK config sets for all five routing
layers, so GRT_ADJUSTMENT has no effect here. The per-layer values are searched
instead.

Standard library only (Python 3.6+); optuna is used only through the `trial`
object passed to suggest() and in distributions().
"""

import copy
import json

# ---------------------------------------------------------------- floorplans
# 8x4 floorplans (floorplans/<id>.json) and the horizontal halo values that keep
# them free of short power straps. fp8_base/_trk: paired macros 32.96 um apart,
# so the halo must be exactly 16.48 (half the gap: halos meet, no std-cell rows
# and hence no short VPWR/VGND Metal4 straps between the pair --
# docs/drc-triage.md section 6; any larger value fails
# macros/check_macro_floorplan.py "closer than two halos"). fp8_wide(_trk): gaps
# 100.4 um; one lattice stripe pair runs through the middle of every gap (macro
# x + 286.99), so every row segment in a gap is crossed by a full-height stripe
# for any halo below ~50 um. fp8_spread_trk: gaps >= 167.8 um (two stripes per
# gap); the first top macro (x 213.36) must stay clear of the TT I/O pins at
# x <= 191.04 plus the halo, so the halo must be <= 22.32.
FLOORPLANS_8X4 = ["fp8_base", "fp8_base_trk", "fp8_wide", "fp8_wide_trk", "fp8_spread_trk"]
# 6x4 (docs/6x4.md): the committed variants6x4 placement with its FP_OBSTRUCTIONS
# box over the stripe-free gap at the right core edge. Its pair gaps are 32.96 um,
# so the horizontal halo is exactly 16.48 for the same reason as fp8_base; the
# vertical halo choices are restricted per track to the values for which
# variants6x4/row_islands.py predicts no island (tracks.py).
FLOORPLANS_6X4 = ["fp6_tworow_trk_top30_edgeobs"]
HALO_FIXED = {"fp8_base": 16.48, "fp8_base_trk": 16.48, "fp6_tworow_trk_top30_edgeobs": 16.48}
HALO_WIDE = [16.48, 10.0, 12.5, 20.0, 25.0]
HALO_SPREAD = [16.48, 10.0, 12.5, 20.0]

SYNTH_STRATEGIES = ["AREA 0", "AREA 1", "AREA 2", "AREA 3",
                    "DELAY 0", "DELAY 1", "DELAY 2", "DELAY 3", "DELAY 4"]

# name -> definition. kind: cat (choices) | int (lo, hi, step) | float (lo, hi, step).
# key: LibreLane variable (None: composite, see materialize()).
# unset: the value the flow uses when the committed config does not set the key
#        (LibreLane/PDK default, checked in a resolved.json); "explicit" for knobs
#        that make_snapshot always writes (floorplan, density, hold margins, halo).
KNOBS = {
    # ---- floorplan
    "floorplan": dict(kind="cat", choices=FLOORPLANS_8X4, unset="explicit", key=None,
                      source="floorplans/*.json (MACROS.<macro>.instances, + the file's config keys)",
                      why="Macro placement sets the pin-access channels, the routing detours around the "
                          "SRAMs and the reset/clock wire lengths across the die."),
    "halo_h_wide": dict(kind="cat", choices=HALO_WIDE, unset="explicit", key="FP_MACRO_HORIZONTAL_HALO",
                        when=("floorplan", ("fp8_wide", "fp8_wide_trk")), source="steps/openroad.py",
                        why="Horizontal keep-out around each macro; only floorplans with wide gaps may use "
                            "values other than 16.48 without creating short power straps."),
    "halo_h_spread": dict(kind="cat", choices=HALO_SPREAD, unset="explicit", key="FP_MACRO_HORIZONTAL_HALO",
                          when=("floorplan", ("fp8_spread_trk",)), source="steps/openroad.py",
                          why="As halo_h_wide; <= 22.32 keeps the first top macro clear of the I/O pins."),
    "FP_MACRO_VERTICAL_HALO": dict(kind="cat", choices=[10.0, 5.0, 15.0, 20.0], unset=10.0,
                                   key="FP_MACRO_VERTICAL_HALO", source="steps/openroad.py",
                                   why="Keep-out above/below the macros = the pin-access channel of the "
                                       "SRAM signal pins (all on the edge facing the logic)."),
    "density": dict(kind="int", lo=50, hi=72, step=1, unset="explicit", key="PL_TARGET_DENSITY_PCT",
                    source="steps/openroad.py (GlobalPlacement)",
                    why="Global-placement target density: wire length versus routing congestion."),
    # ---- synthesis
    "SYNTH_STRATEGY": dict(kind="cat", choices=SYNTH_STRATEGIES, unset="AREA 0", key="SYNTH_STRATEGY",
                           source="steps/pyosys.py",
                           why="ABC mapping script; DELAY scripts shorten logic depth on the reset and "
                               "register paths at an area cost. AREA 0-2 and DELAY 0-3 pass CLOCK_PERIOD "
                               "as the -D target of retime/upsize/dnsize; AREA 3 and DELAY 4 (the ORFS "
                               "scripts) do not use it (scripts/pyosys/construct_abc_script.py)."),
    "abc_fine_tune": dict(kind="cat", choices=["none", "buffering", "sizing"], unset="none", key=None,
                          source="steps/pyosys.py, scripts/pyosys/construct_abc_script.py",
                          why="SYNTH_ABC_BUFFERING (buffer -N fanout; upsize; dnsize) or SYNTH_SIZING; the "
                              "script applies buffering first when both are set, so they are one choice."),
    "MAX_FANOUT_CONSTRAINT": dict(kind="cat", choices=[10, 8, 6], unset=10, key="MAX_FANOUT_CONSTRAINT",
                                  source="config/flow.py (PDK variable, IHP value 10); scripts/base.sdc",
                                  why="Fan-out limit for ABC buffering and repair_design; only values <= the "
                                      "PDK's 10 (stricter SDC limit) are searched."),
    # ---- placement
    "PL_TIMING_DRIVEN": dict(kind="cat", choices=[False, True], unset=False, key="PL_TIMING_DRIVEN",
                             source="steps/openroad.py (GlobalPlacement)",
                             why="Timing-driven global placement (net weights from STA)."),
    "PL_ROUTABILITY_DRIVEN": dict(kind="cat", choices=[True, False], unset=True, key="PL_ROUTABILITY_DRIVEN",
                                  source="steps/openroad.py (GlobalPlacement)",
                                  why="Routability-driven inflation during global placement."),
    "gpl_pad": dict(kind="cat", choices=[0, 2, 4], unset=0, key="GPL_CELL_PADDING",
                    source="steps/openroad.py (PDK variable, IHP value 0)",
                    why="Global-placement cell padding in sites (split over both sides): pin access."),
    "dpl_pad": dict(kind="cat", choices=[0, 2], unset=0, key="DPL_CELL_PADDING", when=("gpl_pad", (2, 4)),
                    source="steps/common_variables.py (PDK variable, IHP value 0)",
                    why="Detailed-placement padding; must not exceed GPL_CELL_PADDING."),
    # ---- design repair (repair_design after GPL)
    "DESIGN_REPAIR_MAX_WIRE_LENGTH": dict(kind="cat", choices=[0, 150, 300, 500, 800], unset=0,
                                          key="DESIGN_REPAIR_MAX_WIRE_LENGTH", source="steps/openroad.py",
                                          why="Buffer wires longer than this (um); 0 = off. The reset and "
                                              "enable nets span the die."),
    "DESIGN_REPAIR_MAX_SLEW_PCT": dict(kind="int", lo=10, hi=50, step=5, unset=20,
                                       key="DESIGN_REPAIR_MAX_SLEW_PCT", source="steps/openroad.py",
                                       why="Slew margin for repair_design (post-route slow corner showed "
                                           "~0.9 ns slews on buf_1 fan-out buffers)."),
    "DESIGN_REPAIR_MAX_CAP_PCT": dict(kind="int", lo=10, hi=50, step=5, unset=20,
                                      key="DESIGN_REPAIR_MAX_CAP_PCT", source="steps/openroad.py",
                                      why="Capacitance margin for repair_design."),
    "RUN_POST_GRT_DESIGN_REPAIR": dict(kind="cat", choices=[False, True], unset=False,
                                       key="RUN_POST_GRT_DESIGN_REPAIR", source="flows/classic.py",
                                       why="repair_design again with global-route parasitics "
                                           "(OpenROAD.RepairDesignPostGRT; marked experimental)."),
    # ---- resizer timing repair
    "PL_RESIZER_SETUP_SLACK_MARGIN": dict(kind="float", lo=0.0, hi=9.0, step=0.05, unset=0.05,
                                          key="PL_RESIZER_SETUP_SLACK_MARGIN", source="steps/openroad.py; "
                                          "scripts/openroad/rsz_timing_postcts.tcl",
                                          why="Post-CTS repair_timing treats slack below this as violating. "
                                              "Placement-estimated parasitics are optimistic, so a margin of "
                                              "ns is needed to act at all. Widened from 0..6 to 0..9 ns in v2: "
                                              "the best 20 ns trials of v1 used 5.75 to 6.0."),
    "pl_hold": dict(kind="cat", choices=[0.1, 0.05, 0.15, 0.2], unset="explicit",
                    key="PL_RESIZER_HOLD_SLACK_MARGIN", source="steps/openroad.py",
                    why="Post-CTS hold margin (0/0 produced a fast-corner hold violation in the sweep). Hold "
                        "buffers sit on the reset and input paths."),
    "PL_RESIZER_SETUP_MAX_UTIL_PCT": dict(kind="cat", choices=[None, 65, 75], unset=None,
                                          key="PL_RESIZER_SETUP_MAX_UTIL_PCT", source="steps/openroad.py",
                                          why="Utilization cap (-max_utilization) for setup repair; bounds "
                                              "the area cost of large margins."),
    "RUN_POST_GRT_RESIZER_TIMING": dict(kind="cat", choices=[False, True], unset=False,
                                        key="RUN_POST_GRT_RESIZER_TIMING", source="flows/classic.py",
                                        why="repair_timing again with global-route parasitics "
                                            "(OpenROAD.ResizerTimingPostGRT; marked experimental)."),
    "GRT_RESIZER_SETUP_SLACK_MARGIN": dict(kind="float", lo=0.0, hi=4.0, step=0.05, unset=0.025,
                                           key="GRT_RESIZER_SETUP_SLACK_MARGIN",
                                           when=("RUN_POST_GRT_RESIZER_TIMING", (True,)),
                                           source="steps/openroad.py; scripts/openroad/rsz_timing_postgrt.tcl",
                                           why="Setup margin of the post-GRT repair (only read by that step)."),
    "grt_hold": dict(kind="cat", choices=[0.05, 0.02, 0.1], unset="explicit", key="GRT_RESIZER_HOLD_SLACK_MARGIN",
                     when=("RUN_POST_GRT_RESIZER_TIMING", (True,)),
                     source="steps/openroad.py; scripts/openroad/rsz_timing_postgrt.tcl",
                     why="Hold margin of the post-GRT repair (only read by that step)."),
    # ---- clock tree
    "CTS_SINK_CLUSTERING_SIZE": dict(kind="cat", choices=[None, 10, 16, 25, 35], unset=None,
                                     key="CTS_SINK_CLUSTERING_SIZE", source="steps/openroad.py (CTS)",
                                     why="Sinks per leaf cluster: clock latency/skew, which the input and "
                                         "reset paths see directly."),
    "CTS_SINK_CLUSTERING_MAX_DIAMETER": dict(kind="cat", choices=[None, 30, 60, 100], unset=None,
                                             key="CTS_SINK_CLUSTERING_MAX_DIAMETER",
                                             source="steps/openroad.py (CTS)", why="Leaf cluster diameter (um)."),
    "CTS_MAX_SLEW": dict(kind="cat", choices=[None, 0.4, 0.75, 1.2], unset=None, key="CTS_MAX_SLEW",
                         source="steps/openroad.py (CTS); scripts/openroad/cts.tcl",
                         why="CTS characterization slew limit (ns); unset = from the lib (2.5 ns)."),
    "CTS_MAX_CAP": dict(kind="cat", choices=[None, 0.1, 0.2], unset=None, key="CTS_MAX_CAP",
                        source="steps/openroad.py (CTS); scripts/openroad/cts.tcl",
                        why="CTS characterization capacitance limit (pF); unset = from the lib (0.3 pF)."),
    "CTS_CLK_MAX_WIRE_LENGTH": dict(kind="cat", choices=[0, 250, 500], unset=0, key="CTS_CLK_MAX_WIRE_LENGTH",
                                    source="steps/openroad.py (CTS); scripts/openroad/cts.tcl",
                                    why="repair_clock_nets maximum wire length (um); 0 = off."),
    "CTS_OBSTRUCTION_AWARE": dict(kind="cat", choices=[None, True], unset=None, key="CTS_OBSTRUCTION_AWARE",
                                  source="steps/openroad.py (CTS)",
                                  why="Keep clock buffers off the macros (less legalizer displacement)."),
    # ---- routing
    "grt_adj_m2": dict(kind="cat", choices=[0.0, 0.1, 0.2, 0.3], unset=0.0, key=None,
                       source="steps/common_variables.py GRT_LAYER_ADJUSTMENTS (PDK 0,0,0,0,0)",
                       why="Metal2 capacity reduction in global routing (spreads demand; DRT convergence)."),
    "grt_adj_m3": dict(kind="cat", choices=[0.0, 0.1, 0.2, 0.3], unset=0.0, key=None,
                       source="as grt_adj_m2", why="Metal3 capacity reduction (run2's overflow was on Metal3)."),
    "grt_adj_m4": dict(kind="cat", choices=[0.0, 0.1, 0.2], unset=0.0, key=None,
                       source="as grt_adj_m2", why="Metal4 capacity reduction (Metal4 also carries the PDN)."),
    "GRT_MACRO_EXTENSION": dict(kind="cat", choices=[0, 1], unset=0, key="GRT_MACRO_EXTENSION",
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
# Value of an "explicit" knob that a seed activates without giving it and the base leaves
# inactive: the LibreLane default of the post-GRT hold margin, and the halo that suits every
# floorplan with a free halo choice.
EXPLICIT_FALLBACK = {"grt_hold": 0.05, "halo_h_wide": 16.48, "halo_h_spread": 16.48}

# Knobs written one-to-one to a LibreLane key by materialize() (the others are composite
# or make_snapshot parameters).
SIMPLE = ("FP_MACRO_VERTICAL_HALO", "SYNTH_STRATEGY", "MAX_FANOUT_CONSTRAINT", "PL_TIMING_DRIVEN",
          "PL_ROUTABILITY_DRIVEN", "DESIGN_REPAIR_MAX_WIRE_LENGTH", "DESIGN_REPAIR_MAX_SLEW_PCT",
          "DESIGN_REPAIR_MAX_CAP_PCT", "RUN_POST_GRT_DESIGN_REPAIR", "PL_RESIZER_SETUP_SLACK_MARGIN",
          "PL_RESIZER_SETUP_MAX_UTIL_PCT", "RUN_POST_GRT_RESIZER_TIMING", "GRT_RESIZER_SETUP_SLACK_MARGIN",
          "CTS_SINK_CLUSTERING_SIZE", "CTS_SINK_CLUSTERING_MAX_DIAMETER", "CTS_MAX_SLEW", "CTS_MAX_CAP",
          "CTS_CLK_MAX_WIRE_LENGTH", "CTS_OBSTRUCTION_AWARE", "GRT_MACRO_EXTENSION")
# Every LibreLane key a knob can write (materialize() decides each of them for every trial).
COMPOSITE_KEYS = ("FP_MACRO_HORIZONTAL_HALO", "SYNTH_ABC_BUFFERING", "SYNTH_SIZING", "GPL_CELL_PADDING",
                  "DPL_CELL_PADDING", "GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH", "GRT_LAYER_ADJUSTMENTS")
# Keys make_snapshot writes from its own parameters (and the floorplan file's config block).
SNAPSHOT_KEYS = ("CLOCK_PERIOD", "PL_TARGET_DENSITY_PCT", "PL_RESIZER_HOLD_SLACK_MARGIN",
                 "GRT_RESIZER_HOLD_SLACK_MARGIN", "OPENROAD_THREADS", "FP_OBSTRUCTIONS")
KNOB_KEYS = tuple(KNOBS[n]["key"] for n in SIMPLE) + COMPOSITE_KEYS + SNAPSHOT_KEYS
ABSENT = object()
UNSET_GRT_LAYER_ADJUSTMENTS = [0, 0, 0, 0, 0]


def defs(space="8x4", restrict=None):
    """Knob definitions of one track's search space: "8x4" (the fp8_* floorplans) or "6x4"
    (the committed 6x4 floorplan). restrict: {knob: [allowed choices]} (a subset, in order)."""
    D = copy.deepcopy(KNOBS)
    if space == "6x4":
        D["floorplan"]["choices"] = list(FLOORPLANS_6X4)
    elif space != "8x4":
        raise ValueError("unknown space %r" % space)
    for n, allowed in (restrict or {}).items():
        if D[n]["kind"] != "cat":
            raise ValueError("restrict: %s is not categorical" % n)
        bad = [v for v in allowed if v not in D[n]["choices"]]
        if bad or not allowed:
            raise ValueError("restrict %s: %r not a non-empty subset of %r" % (n, allowed, D[n]["choices"]))
        D[n]["choices"] = [c for c in D[n]["choices"] if c in allowed]
    return D


def active(name, knobs, D=KNOBS):
    cond = D[name].get("when")
    if not cond:
        return True
    parent, values = cond
    return parent in knobs and knobs[parent] in values


def suggest(trial, D=KNOBS):
    """Define-by-run sampling with an optuna Trial. Returns {knob: value} (active knobs only)."""
    out = {}
    for n in ORDER:
        if not active(n, out, D):
            continue
        k = D[n]
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


def _check(n, v, D):
    """Validate a knob value and return its canonical form (the matching choice object, so that
    20 and 20.0 are one value; ints for int knobs; floats rounded to 1e-6 for float knobs)."""
    k = D[n]
    if k["kind"] == "cat":
        for c in k["choices"]:
            if same(c, v):
                return c
        raise ValueError("knob %s: %r not in %r" % (n, v, k["choices"]))
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (k["lo"] - 1e-9 <= v <= k["hi"] + 1e-9):
        raise ValueError("knob %s: %r outside [%s, %s]" % (n, v, k["lo"], k["hi"]))
    return int(round(v)) if k["kind"] == "int" else round(float(v), 6)


def complete(partial, base, D=KNOBS):
    """Fill a partial knob dict (a seed) with the track's base values (for a knob the base
    leaves inactive: its unset value); validate choices/ranges. Retired knobs are dropped
    (only their accepted value is allowed)."""
    out = {}
    for n in ORDER:
        if not active(n, out, D):
            continue
        if n in partial:
            v = partial[n]
        elif n in base:
            v = base[n]
        else:
            v = D[n]["unset"]
            if v == "explicit":  # a knob the base leaves inactive (e.g. halo_h_wide on fp8_spread_trk)
                v = EXPLICIT_FALLBACK[n]
        out[n] = _check(n, v, D)
    for n, v in RETIRED.items():
        if n in partial and partial[n] != v:
            raise ValueError("knob %s=%r is retired (only %r is accepted)" % (n, partial[n], v))
    unknown = set(partial) - set(D) - set(RETIRED)
    if unknown:
        raise ValueError("unknown knobs %s" % sorted(unknown))
    return out


def canonical(knobs):
    """Identity of a complete knob set (the effective configuration within a track)."""
    return json.dumps(knobs, sort_keys=True, separators=(",", ":"))


def _num(x):
    x = float(x)
    return int(x) if x == int(x) else round(x, 6)


def _jval(v):
    return _num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def halo_of(kn):
    h = HALO_FIXED.get(kn["floorplan"])
    if h is None:
        h = kn["halo_h_wide"] if "halo_h_wide" in kn else kn["halo_h_spread"]
    return h


def targets(kn, D=KNOBS):
    """Effective value of every knob-controlled LibreLane key (ABSENT = key not set, i.e. the
    LibreLane/PDK default) for a complete knob set."""
    t = {"FP_MACRO_HORIZONTAL_HALO": _num(halo_of(kn))}
    for n in SIMPLE:
        key = KNOBS[n]["key"]
        if n not in kn:  # inactive conditional knob: its step does not run; leave it at the default
            t[key] = ABSENT
            continue
        v, u = kn[n], KNOBS[n]["unset"]
        t[key] = ABSENT if (v is None or v == u) else _jval(v)
    t["SYNTH_ABC_BUFFERING"] = True if kn["abc_fine_tune"] == "buffering" else ABSENT
    t["SYNTH_SIZING"] = True if kn["abc_fine_tune"] == "sizing" else ABSENT
    t["GPL_CELL_PADDING"] = kn["gpl_pad"] if kn["gpl_pad"] else ABSENT
    t["DPL_CELL_PADDING"] = kn["dpl_pad"] if kn.get("dpl_pad") else ABSENT
    t["GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"] = (kn["DESIGN_REPAIR_MAX_WIRE_LENGTH"]
                                              if kn.get("RUN_POST_GRT_DESIGN_REPAIR")
                                              and kn["DESIGN_REPAIR_MAX_WIRE_LENGTH"] else ABSENT)
    adj = [0.0, kn["grt_adj_m2"], kn["grt_adj_m3"], kn["grt_adj_m4"], 0.0]
    t["GRT_LAYER_ADJUSTMENTS"] = [_num(a) for a in adj] if any(adj) else ABSENT
    return t


def materialize(knobs, base, repo_cfg, fp_config=None, D=KNOBS):
    """knobs -> (snapshot parameters for make_snapshot, LibreLane overrides).

    repo_cfg: the frozen src/config.json (json.load); fp_config: the floorplan file's
    "config" block, which make_snapshot applies before the overrides. An override is
    emitted only where the effective value differs from what make_snapshot would
    otherwise write, so identical effective configurations get identical overrides and
    run ids (make_snapshot hashes the overrides)."""
    kn = complete(knobs, base, D)
    ref = dict(repo_cfg)
    ref.update(fp_config or {})
    snap = {"floorplan": kn["floorplan"], "density": kn["density"], "pl_hold": kn["pl_hold"],
            "grt_hold": kn.get("grt_hold", repo_cfg.get("GRT_RESIZER_HOLD_SLACK_MARGIN", 0.05))}
    ov = {}
    for key, want in targets(kn, D).items():
        have = ref.get(key, ABSENT)
        if want is ABSENT:
            if have is not ABSENT:
                ov[key] = None
        elif have is ABSENT or not same(have, want):
            ov[key] = want
    return snap, ov


def same(a, b):
    """JSON value equality that keeps booleans apart from 0/1."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def base_knobs(cfg, floorplans, D=KNOBS):
    """The knob set of a committed configuration.

    cfg: the effective config.json of the track's committed build (src/config.json, with
    the variants6x4 overlay merged for the 6x4 track); floorplans: {id: floorplan dict}.
    Raises ValueError if the committed configuration is outside the searched set."""
    inst = ((cfg.get("MACROS") or {}).get("RM_IHPSG13_1P_64x16_c2") or {}).get("instances")
    fps = [fid for fid in D["floorplan"]["choices"]
           if fid in floorplans and floorplans[fid].get("instances") == inst
           and all(cfg.get(k) == v for k, v in (floorplans[fid].get("config") or {}).items())]
    if len(fps) != 1:
        raise ValueError("committed MACROS instances match floorplans %r of %r"
                         % (fps, D["floorplan"]["choices"]))
    kn = {"floorplan": fps[0]}
    halo = float(cfg.get("FP_MACRO_HORIZONTAL_HALO", 10.0))
    if fps[0] in HALO_FIXED:
        if abs(halo - HALO_FIXED[fps[0]]) > 1e-9:
            raise ValueError("committed FP_MACRO_HORIZONTAL_HALO %s differs from the fixed %s of %s"
                             % (halo, HALO_FIXED[fps[0]], fps[0]))
    elif fps[0] in ("fp8_wide", "fp8_wide_trk"):
        kn["halo_h_wide"] = halo
    else:
        kn["halo_h_spread"] = halo
    kn["density"] = int(cfg["PL_TARGET_DENSITY_PCT"])
    kn["pl_hold"] = float(cfg["PL_RESIZER_HOLD_SLACK_MARGIN"])
    kn["grt_hold"] = float(cfg.get("GRT_RESIZER_HOLD_SLACK_MARGIN", 0.05))
    for n in SIMPLE:
        key = KNOBS[n]["key"]
        v = cfg.get(key, ABSENT)
        kn[n] = KNOBS[n]["unset"] if v is ABSENT or v is None else v
        if KNOBS[n]["kind"] == "float":
            kn[n] = round(float(kn[n]), 6)
    if cfg.get("SYNTH_ABC_BUFFERING"):
        kn["abc_fine_tune"] = "buffering"
    elif cfg.get("SYNTH_SIZING"):
        kn["abc_fine_tune"] = "sizing"
    else:
        kn["abc_fine_tune"] = "none"
    kn["gpl_pad"] = int(cfg.get("GPL_CELL_PADDING") or 0)
    kn["dpl_pad"] = int(cfg.get("DPL_CELL_PADDING") or 0)
    adj = cfg.get("GRT_LAYER_ADJUSTMENTS") or UNSET_GRT_LAYER_ADJUSTMENTS
    if len(adj) != 5 or adj[0] or adj[4]:
        raise ValueError("committed GRT_LAYER_ADJUSTMENTS %r outside the searched set" % (adj,))
    kn["grt_adj_m2"], kn["grt_adj_m3"], kn["grt_adj_m4"] = (float(adj[1]), float(adj[2]), float(adj[3]))
    # every active knob must be inside the searched set; inactive ones are kept (they are the
    # values a seed that activates them starts from) when they are
    out = {}
    act = {}
    for n in ORDER:
        if active(n, act, D):
            act[n] = out[n] = _check(n, kn[n], D)
        else:
            try:
                out[n] = _check(n, kn[n], D)
            except (ValueError, KeyError):
                pass
    want = targets(act, D)["GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"]
    if not same(cfg.get("GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH", ABSENT), want):
        raise ValueError("committed GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH is not the searched coupling")
    return out


def translate(knobs, base, D):
    """A knob set from another track's space, moved into space D: floorplan and halo knobs
    come from `base`; any other value outside D's choices is replaced by the base value."""
    part = {n: v for n, v in knobs.items() if n in D and n not in ("floorplan", "halo_h_wide", "halo_h_spread")}
    part["floorplan"] = base["floorplan"]
    for n in ("halo_h_wide", "halo_h_spread"):
        if n in base:
            part[n] = base[n]
    for n in list(part):
        try:
            part[n] = _check(n, part[n], D)
        except ValueError:
            part[n] = base.get(n, D[n]["unset"])
    return complete(part, base, D)


def distributions(D=KNOBS):
    """optuna distributions for every knob (used when importing trials into a study)."""
    import optuna.distributions as OD
    out = {}
    for n in ORDER:
        k = D[n]
        if k["kind"] == "cat":
            out[n] = OD.CategoricalDistribution(copy.copy(k["choices"]))
        elif k["kind"] == "int":
            out[n] = OD.IntDistribution(k["lo"], k["hi"], step=k["step"])
        else:
            out[n] = OD.FloatDistribution(k["lo"], k["hi"], step=k["step"])
    return out


def table_rows(base, D=KNOBS):
    """(knob, LibreLane key, values, committed value, rationale) for documentation."""
    rows = []
    for n in ORDER:
        k = D[n]
        if k["kind"] == "cat":
            vals = ", ".join("unset" if c is None else str(c) for c in k["choices"])
        else:
            vals = "%s..%s step %s" % (k["lo"], k["hi"], k["step"])
        key = k["key"] or {"floorplan": "MACROS instances", "abc_fine_tune": "SYNTH_ABC_BUFFERING / SYNTH_SIZING",
                           "grt_adj_m2": "GRT_LAYER_ADJUSTMENTS[1]", "grt_adj_m3": "GRT_LAYER_ADJUSTMENTS[2]",
                           "grt_adj_m4": "GRT_LAYER_ADJUSTMENTS[3]"}.get(n, n)
        cond = k.get("when")
        b = base.get(n, "(inactive)")
        rows.append((n, key, vals, "unset" if b is None else str(b),
                     (("only if %s in %s. " % (cond[0], list(cond[1]))) if cond else "") + k["why"]))
    return rows
