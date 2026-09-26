# Physical-design optimizer

`tools/opt/` is an autonomous search over LibreLane configuration knobs. It
runs several **tracks**, each with its own optuna study:

- the 8x4 design of record at `CLOCK_PERIOD` 20 ns (50 MHz), the period of
  the submission;
- the same design at 15 ns (66.7 MHz) and at 13.33 ns (75.0 MHz), the
  **frequency tracks**;
- the 6x4 fallback: the `diet4` core with the committed `variants6x4/`
  overlay on the 6x4 die (`docs/6x4.md`);
- one track per variant core that the variant workflow publishes
  (`docs/variants.md`).

It runs on the Engaging cluster through the sweep harness of `docs/sweep.md`,
which it uses unchanged. It records every run in an append-only store, and it
promotes the best configurations of each track to a full-flow run, the Tiny
Tapeout precheck and the gate-level cocotb suite.

Like every local run, a result here is evidence. It is not the result of
record (`docs/hardening.md` sections 7 and 9). A configuration becomes the
design of record only after it is committed to `src/config.json` and the
GitHub gds, precheck and gl_test actions pass on that commit (see
[Adopting a configuration](#adopting-a-configuration)).

The first version of the optimizer (v1, 2026-09-26 03:36 to 13:40) searched
the design of record at 20 ns and the variant cores. Its record is in
[Campaign record](#campaign-record). The current version (v2) added the
frequency tracks, the 6x4 track, absolute knob values, the import of
identical earlier trials, weighted allocation and retirement.

## Starting point

**`v0.1-hardened`.** The design of record at tag `v0.1-hardened` (`c118027`)
passed the official gds, precheck and gl_test actions in GitHub run
36144357821. That run had utilization 58.5% and, at 50 MHz (20 ns):

- typ setup worst slack (WS) +0.89 ns;
- slow-corner setup WS −8.52 ns;
- route DRC, LVS and antenna all 0.

The flow signs off setup only at the typ corner (`TIMING_VIOLATION_CORNERS`
`*typ*`), so the slow corner does not fail the action. Almost every
slow-corner violation starts at `rst_n`: 2,467 of the 2,482 in that run.

**Adopted v1 result.** Commit `25e331e` adopted v1's promotion p010 (trial
`dor-26a873-c8bf57e#94`) into `src/config.json`. Its promoted full run
(job 24010051, `OPENROAD_THREADS` 4) had setup WS typ/fast/slow
+7.88/+9.31/+2.95 ns at 20 ns, slew/cap/fan-out violations 4/0/3, typ fmax
estimate 82.5 MHz and utilization 61.9%; the TT precheck passed 9/9 (job
24011934) and the gate-level suite had 0 failures (job 24011935). The RTL did
not change. For `131e793`, which carries that configuration:

- the GitHub gds run (36257636798) was still in progress when this page was
  written;
- the gds_6x4 run (36257636751) failed in "Build GDS" with `69 Routing DRC
  errors found`. The 6x4 build inherits the adopted keys through
  `variants6x4/config.overlay.json`, which restates only the density and
  the halo. The optimizer's trial of the same configuration shows the same
  count ([Campaign record](#campaign-record), v2).

**Two observations from v1 that still shape the search space.** The first
comes from sweep run `base-26a873-8x4-fp8_base-d60-p20-h0p1_0p05-fast-t32`
(job 23749131_0, before the halo change), the second from LibreLane itself.

1. **The post-CTS resizer barely acts on setup at small margins.**
   - In job 23749131_0, the post-CTS resizer log
     (`37-openroad-resizertimingpostcts`) reports `RSZ-0098 No setup
     violations found`, although all three corners are loaded
     (`RSZ_CORNERS` unset, so `STA_CORNERS`).
   - Post-route STA of the same run then shows slow-corner WS −5.09 ns. The
     worst slow path is `rst_n` → input buffer → a chain of
     `sg13cmos5l_buf_1` fan-out buffers with slews of about 0.9 ns → logic →
     flip-flop. With placement-estimated parasitics, the path shows positive
     slack.
   - So the setup-repair margins (`PL_RESIZER_SETUP_SLACK_MARGIN`, and the
     post-GRT repair) are searched up to several nanoseconds. v1 confirmed
     the lever: its best 20 ns trials use 5.75 to 6.0 ns, the top of its
     0–6 ns range, so v2 searches 0–9 ns.
2. **`GRT_ADJUSTMENT` has no effect in this flow.**
   - `scripts/openroad/common/set_layer_adjustments.tcl` (LibreLane
     3.1.0.dev3) first applies `GRT_ADJUSTMENT` to every layer.
   - It then overrides each layer with `GRT_LAYER_ADJUSTMENTS`, and the IHP
     PDK sets that for all five routing layers (`0,0,0,0,0`).
   - The per-layer values are therefore searched instead.

## Tracks

`tools/opt/tracks.py` derives the tracks from the frozen tree the driver runs
on. Each track has a core, a die, a clock period, a search space and a
**committed configuration** (its "base"): what the repository builds for that
combination today.

| Track | Core | Die | `CLOCK_PERIOD` | Committed configuration | Weight | Gate-level `PE_VARIANT` |
|---|---|---|---|---|---:|---|
| `dor` | `src/protocol_emulator_core.v` (sha256 `26a873db…`) | 8x4 | 20 ns (50 MHz) | `src/config.json` | 0.35 | `base` |
| `dor15` | same | 8x4 | 15 ns (66.7 MHz) | `src/config.json` with `CLOCK_PERIOD` 15; `info.yaml` `clock_hz` 66666667 | 0.22 | `base` |
| `dor13` | same | 8x4 | 13.33 ns (75.0 MHz) | `src/config.json` with `CLOCK_PERIOD` 13.33; `clock_hz` 75018755 | 0.08 | `base` |
| `diet4_6x4` | `variants6x4/protocol_emulator_core.v` (`diet4`, sha256 `cc27c465…`) | 6x4 | 20 ns | `src/config.json` with `variants6x4/config.overlay.json` merged (RFC 7386, as `variants6x4/switch.py apply` does); `info.yaml` `tiles` "6x4" | 0.20 | `diet4` |
| one per variant | `$PE_WORK/variants/cores/<name>.v` (sha256 as in its `MANIFEST.sha256`) | 8x4 | 20 ns | `src/config.json` | 0.15, shared | `<name>` |

The weights are the shares of new trials ([Allocation](#allocation)). The
frequency tracks together get 0.30, split 0.22/0.08 because 15 ns is the
primary target: the Tiny Tapeout demo board clock goes up to about 66.5 MHz
(TT clock page, quoted in `docs/extension-study.md` section 3).

A variant core is used only if its sha256 matches the MANIFEST; a core whose
body equals the design of record is skipped; every core is copied to
`optimizer/cores/<name>-<sha12>.v`, so that a republish cannot change a
track. The driver rescans the MANIFEST every 10 minutes.

### Absolute knob values

A trial is a set of knob values ([Search space](#search-space)). In v2 these
values are absolute:

- `space.base_knobs()` maps a committed configuration to its knob set. For
  the design of record at `25e331e` this is exactly the knob set of v1 trial
  `#94` (p010).
- `space.materialize()` turns any knob set into the `make_snapshot.py`
  parameters and LibreLane overrides. A key is written when the knob value
  differs from the committed value. It is deleted (a `null` override) when
  the knob value is the LibreLane/PDK default and the committed config sets
  the key. The defaults were checked against the `resolved.json` of a run
  without any of these keys.
- So a knob set gives the same effective configuration whatever the
  committed configuration is. `tools/opt/test_opt.py` checks this on 300
  random knob sets and two committed configurations: `src/config.json`, and
  the same file with every knob-controlled timing key removed and the
  `fp8_base` placement with halo 16.48 (as before the adoption). It also
  checks that the committed configuration needs no override, and that a
  knob set is recovered from its own effective configuration.

**Why this changed.** v1 stored knob values relative to the configuration
committed at its launch (`c118027`: density 60, halo 16.48, `fp8_base`, no
timing keys) and left out every value equal to those defaults. After
`25e331e` changed the committed configuration, the v1 tools on tree
`131e793` (driver job 24020736, started 13:35) produced wrong configurations:

- every knob set with floorplan `fp8_base` kept the committed halo 20, which
  `macros/check_macro_floorplan.py` rejects ("closer than two halos"); 33
  trials were rejected that way;
- a knob at its old default (for example `PL_TIMING_DRIVEN` false) silently
  kept the new committed value, so the recorded knobs of the three trials
  that were submitted (jobs 24020881, 24020882, 24021036) do not describe
  their configurations.

That driver was cancelled at 13:40. Its trials stay in the store under
the earlier track `dor-26a873-ccca9e6` and are not imported (below).

### Track identity and imported trials

A track id is `<name>-<core sha6>-<die>-p<period>-f<fixed sha6>`, for example
`dor15-26a873-8x4-p15-f174dc0`. The "fixed part" is the committed
configuration without its knob-controlled keys and comments (`tracks.fixed_part()`).
Because knob values are absolute, adopting a new knob set keeps the tracks;
only a change of a fixed key (PDN, pins, template keys, ...) starts new ones.

A finished trial of an earlier track is **imported** into a current track
when:

1. the two tracks have the same core, die and period; and
2. the earlier trial's effective configuration equals the effective
   configuration of its knob set on the current tree. The earlier
   configuration is its frozen tree's `src/config.json` with the trial's
   recorded `config_changes_vs_repo` applied. The current one is computed
   by `tracks.effective_config()`, a mirror of `make_snapshot.py`'s config
   edit.

An imported trial is a `trial_import` event: the same metrics, legality and
value, the original run directory and job id, and a note of the trial it
stands for. It is added to the track's optuna study with
`Study.add_trial()`, so the sampler continues from it. No LibreLane run is
repeated. The driver imports at every loop, so earlier trials that finish
later are imported too.

At the v2 launch (driver job 24024261) the driver imported 207 trials:

- 196 at start: 122 into `dor` and 9 to 12 into each variant track, from the
  v1 tracks `*-c8bf57e`;
- 11 in the first loop, from v1 trial jobs that had finished while no driver
  ran.

Not imported:

- the two v1 trials with `DRT_OPT_ITERS` 80 (a retired value);
- every trial of `*-ccca9e6`, whose recorded knobs do not match their
  configurations.

Before the launch, the same equality was checked offline for every finished
trial of the v1 tracks `*-c8bf57e`. The only trials it failed for were
those two `DRT_OPT_ITERS` trials.

Every trial the driver builds is also checked the other way: its
`make_snapshot.py` `config_changes_vs_repo` must equal the mirror's. A
difference is logged and recorded as `mirror_mismatch` in the trial.

### Seeds

A new track starts with a queue of designed trials ("seeds"). After them,
TPE samples.

- **`dor`:** the committed configuration. It duplicates the imported
  trial that stands for v1 `#94`, so it runs nothing.
- **`dor15`, `dor13`:** the committed configuration at the track's period,
  and the 8 best legal 20 ns configurations of `dor`. The committed
  configuration is among those 8, so there are 8 distinct seeds.
- **`diet4_6x4`:** 8 distinct seeds:
  - the committed 6x4 build;
  - the 6x4 point signed off before the p010 adoption
    (`docs/6x4.md` section 4: every timing key at its LibreLane default,
    density 60, hold margin 0.1; typ +4.29, slow −2.80 ns in full mode, run
    23980402_9);
  - the 5 best 20 ns configurations of `dor` and the 3 best 8x4 `diet4`
    trials, translated into the 6x4 space (`space.translate()`). The 6x4
    floorplan and halo replace theirs, and any value outside the 6x4 space
    falls back to the committed one.
- **Variant tracks:** the committed configuration and the 3 best 20 ns
  configurations of `dor`.

After that, every other track is offered the current best legal
configuration of `dor` once per distinct best, unless it has already run
or is already queued.

**Seed revisions.** Seeds designed after a track exists are added once to
every active track whose recorded revision is lower (`SEED_REVISION` in
`driver.py`, recorded as `track_seeds` events).

- Revision 1 is the list above.
- Revision 2 (2026-09-26 17:17) adds up to 9 seeds to `dor15` and `dor13`:
  the committed configuration and the 8 best 20 ns configurations of `dor`,
  with `PL_RESIZER_SETUP_SLACK_MARGIN` scaled by period / 20 ns and rounded
  to 0.05 ns (for example 5.75 → 4.30 ns at 15 ns and 3.85 ns at 13.33 ns).
- The reason for revision 2: the first 13.33 ns trial (the committed
  configuration, margin 5.75 ns, job 24024333) was still in
  `OpenROAD.ResizerTimingPostCTS` after almost 3 hours. At 17:12 its log
  showed 3,773 endpoints below the margin.

## Frequency tracks and the SDC

The frequency tracks change only `CLOCK_PERIOD` (and `info.yaml`
`clock_hz`). Whether they are a valid Tiny Tapeout submission, and what their
slacks mean, depends on how LibreLane and tt-support-tools use the period.

**What LibreLane 3.1.0.dev3 constrains.** The constraints were read in the
`librelane` package of `librelane-3.1.0.dev3.sif`: `scripts/base.sdc`
(sha256 `34c8f331…`), `steps/openroad.py`, `config/flow.py` and
`scripts/pyosys/construct_abc_script.py`. The values below are from a v1
run's `resolved.json`.

- **Which SDC.** `PNR_SDC_FILE` and `SIGNOFF_SDC_FILE` are null in this
  project. Every OpenROAD step, including the post-route multi-corner STA
  (`OpenROAD.STAPostPNR`), therefore reads `FALLBACK_SDC`, whose default is
  `scripts/base.sdc`: `_SDC_IN = PNR_SDC_FILE or FALLBACK_SDC`, and
  `SIGNOFF_SDC_FILE` only if it is set.
- **Clock.** `create_clock` on `clk` with `-period $CLOCK_PERIOD`.
- **I/O delays scale with the period.** `set_input_delay` (all inputs except
  the clock) and `set_output_delay` (all outputs) are both
  `CLOCK_PERIOD × IO_DELAY_CONSTRAINT / 100`, with `IO_DELAY_CONSTRAINT` 20:
  4.0 ns at 20 ns, 3.0 ns at 15 ns and 2.67 ns at 13.33 ns.
- **Fixed terms.** The following do not depend on the period:
  - clock uncertainty `CLOCK_UNCERTAINTY_CONSTRAINT` 0.25 ns;
  - clock transition `CLOCK_TRANSITION_CONSTRAINT` 0.15 ns;
  - timing derate ±`TIME_DERATING_CONSTRAINT` 5%;
  - `set_max_fanout MAX_FANOUT_CONSTRAINT` (a knob);
  - driving cell `sg13cmos5l_buf_4/X` (`SYNTH_DRIVING_CELL`);
  - output load `OUTPUT_CAP_LOAD` 6 fF;
  - no `MAX_TRANSITION_CONSTRAINT` or `MAX_CAPACITANCE_CONSTRAINT` (the
    liberty values apply);
  - propagated clocks at sign-off.
- **Synthesis depends on the period for some strategies.**
  `construct_abc_script.py` passes `CLOCK_PERIOD` × 1000 ps as the `-D`
  target of `retime` and of the `upsize`/`dnsize` fine-tuning in the
  AREA 0–2 and DELAY 0–3 scripts. The AREA 3 and DELAY 4 scripts (the ORFS
  scripts; DELAY 4 is the committed strategy) do not use it.
- **Placement and repair depend on the period.** Timing-driven placement,
  the setup repair with its margin, CTS and the post-GRT repair all work
  on slacks at the constrained period. So a frequency track is not the
  20 ns layout timed at a shorter period, which is one reason for a
  separate study per period.

So at period T, a register-to-register path must fit in T − 0.25 ns, and an
input-to-register or register-to-output path in 0.8·T − 0.25 ns (before
derate, clock skew and the flip-flop's setup time). The **fmax estimates**
of the leaderboard are
`1000 / (T − WS)` per corner, plus a register-to-register-only version,
as in `docs/sweep.md`. For a register-to-register worst path this
extrapolates correctly from fixed delays. For an I/O worst path the slack
changes by 0.8 ns per ns of period, so the estimate is conservative when WS
is positive and optimistic when it is negative. The frequency tracks do not
depend on the estimate: their STA runs at 15 or 13.33 ns, and a
non-negative WS at a corner means that corner meets that period in the model.

**What Tiny Tapeout takes from `info.yaml`.** In tt-support-tools
`d66cf179e` (the revision of the precheck reproduction, `docs/drc-triage.md`):

- `project_info.py` requires `clock_hz` to be an integer.
- `project.py` and `doc_utils.py` use it only in generated documentation
  (the datasheet's clock line, `DocsHelper.pretty_clock`, and the project
  index).
- `create_user_config()` writes `DESIGN_NAME`, `VERILOG_FILES`, `DIE_AREA`,
  `FP_DEF_TEMPLATE`, `VDD_PIN`, `GND_PIN`, `RT_MAX_LAYER` and the tech's
  LibreLane config. It does not write `CLOCK_PERIOD`, which comes from
  `src/config.json`.
- The precheck does not read `clock_hz`.

So a 15 ns configuration is a Tiny Tapeout submission with `CLOCK_PERIOD`
15 in `src/config.json`. `clock_hz` 66666667 in `info.yaml` keeps the
datasheet consistent. `make_snapshot.py` writes both in every frequency-track
snapshot, and a promotion's precheck runs on that `info.yaml`. The gds action
fails on a typ-corner setup violation at the configured period
(`TIMING_VIOLATION_CORNERS` `*typ*`), which is legality rule 1 below.

## Objective

A run is ranked lexicographically (`tools/opt/objective.py`). Everything is
evaluated at the track's `CLOCK_PERIOD`: STA ran at that period.

1. **Legal.** All of the following must hold:
   - the flow completed with LibreLane exit 0 and post-route multi-corner STA;
   - route DRC 0, antenna violations 0 and critical disconnected pins 0;
   - no setup violation at the typ corner at the track's period (the gds
     action fails otherwise);
   - no hold violation at any corner;
   - no power-port violation. `tools/opt/checks.py` checks both:
     - the short VPWR/VGND Metal4 straps of `docs/drc-triage.md` section 6,
       on the `OpenROAD.GeneratePDN` DEF;
     - the precheck's power-port rule on the final LEF;
   - in a promoted full run, also LVS 0.
2. **Maximum of the minimum setup WS over the typ, fast and slow corners at
   the track's period.** The comparison uses 10 ps resolution.
3. **Minimum of the sum of the max-slew, max-cap and max-fan-out violation
   counts.** These were 290, 73 and 524 in v1's trial of `c118027`, and
   4, 0 and 3 in p010.
4. **Maximum of the typ-corner fmax estimate** `1000 / (T − typ WS)`. At a
   fixed period this only breaks ties of criteria 2 and 3.
5. **Minimum utilization.**

The leaderboard also reports the fmax estimate at every corner and for
register-to-register paths only (see the previous section).

TPE needs a single number. The scalar it maximizes is the minimum setup WS in
nanoseconds, adjusted as follows:

- minus 10⁻⁵ per slew/cap/fan-out violation;
- minus 10⁻³ × utilization;
- an illegal result is further lowered by 15 + 2·log10(1 + number of
  violations);
- a run without post-route STA gets −60. This covers a failed flow, a
  timeout, or a job that never produced a result.

A legal result therefore always scores above an illegal one. The leaderboard
uses the exact lexicographic order, not the scalar.

### How the two power-port checks were validated

**LEF check.**

- On the CI artifact of `c118027`, the final LEF has 26 VPWR and 26 VGND
  ports and 0 violations.
- On the final LEF of sweep run 23749131_0 (before `FP_MACRO_HORIZONTAL_HALO`
  16.48), the check reports 8 violating ports. Each is 627.26 um from one
  edge. These are the 8 LEF errors that failed the precheck in
  `docs/drc-triage.md`.

**DEF check.** The drc-triage PDN-only runs give the same counts as the
drc-triage script:

- the `pdnexp/base` DEF: 8 short stripes;
- the `pdnexp/halo16p48` DEF: 0.

## Search space

Every knob is an ordinary LibreLane variable, or a floorplan file of
`floorplans/` (the `MACROS` instances and the file's `config` keys). A Tiny
Tapeout project may set these in `src/config.json` above the template's "DO
NOT CHANGE" line. The name, type, default and effect of each knob were
checked against the `librelane` package inside `librelane-3.1.0.dev3.sif`:
`steps/pyosys.py`, `steps/openroad.py`, `steps/common_variables.py`,
`config/flow.py`, `flows/classic.py` and `scripts/openroad/*.tcl`. The
"committed value" is the value in `src/config.json` at `25e331e`, or the
LibreLane/PDK default when `src/config.json` does not set the key. The table
summarizes `tools/opt/space.py`; `space.table_rows()` prints every knob with
the committed value of a track.

| Knob | LibreLane variable | Values searched | Committed value | Why |
|---|---|---|---|---|
| `floorplan` | `MACROS` instances | fp8_base, fp8_base_trk, fp8_wide, fp8_wide_trk, fp8_spread_trk | fp8_spread_trk | Macro placement sets the pin-access channels, the routing detours around the SRAMs and the reset/clock wire lengths across the die. |
| `halo_h_wide` | `FP_MACRO_HORIZONTAL_HALO` | 16.48, 10, 12.5, 20, 25 | (inactive) | Only for fp8_wide(_trk). Horizontal keep-out around each macro (see below). |
| `halo_h_spread` | `FP_MACRO_HORIZONTAL_HALO` | 16.48, 10, 12.5, 20 | 20 | Only for fp8_spread_trk; ≤ 22.32 keeps the first top macro clear of the I/O pins. |
| `FP_MACRO_VERTICAL_HALO` | same | 10, 5, 15, 20 | 10 | Keep-out above/below the macros, which is the pin-access channel of the SRAM signal pins (all on the edge facing the logic). |
| `density` | `PL_TARGET_DENSITY_PCT` | 50..72 | 61 | Global-placement target density: wire length versus routing congestion. |
| `SYNTH_STRATEGY` | same | AREA 0-3, DELAY 0-4 | DELAY 4 | ABC mapping script; DELAY scripts shorten logic depth at an area cost. AREA 0-2 and DELAY 0-3 use `CLOCK_PERIOD` as their `-D` target; AREA 3 and DELAY 4 do not. |
| `abc_fine_tune` | `SYNTH_ABC_BUFFERING` / `SYNTH_SIZING` | none, buffering, sizing | none | `construct_abc_script.py` applies buffering first when both are set, so the two are one choice. |
| `MAX_FANOUT_CONSTRAINT` | same | 10, 8, 6 | 8 | Fan-out limit for ABC buffering and repair_design. Only values ≤ 10 (the PDK value) are searched, because the value is also the SDC `set_max_fanout` limit that objective 3 counts against. |
| `PL_TIMING_DRIVEN` | same | false, true | true | Timing-driven global placement. |
| `PL_ROUTABILITY_DRIVEN` | same | true, false | true | Routability-driven global placement. |
| `gpl_pad` | `GPL_CELL_PADDING` | 0, 2, 4 | 0 (PDK) | Global-placement padding (sites, split over both sides). |
| `dpl_pad` | `DPL_CELL_PADDING` | 0, 2 | 0 (PDK) | Only if `gpl_pad` ≥ 2, so that it never exceeds `GPL_CELL_PADDING`. |
| `DESIGN_REPAIR_MAX_WIRE_LENGTH` | same | 0, 150, 300, 500, 800 um | 300 | Buffering of long wires; the reset and enable nets span the die. |
| `DESIGN_REPAIR_MAX_SLEW_PCT` | same | 10..50 step 5 | 35 | Slew margin of repair_design. |
| `DESIGN_REPAIR_MAX_CAP_PCT` | same | 10..50 step 5 | 50 | Capacitance margin of repair_design. |
| `RUN_POST_GRT_DESIGN_REPAIR` | same | false, true | false | repair_design with global-route parasitics (`OpenROAD.RepairDesignPostGRT`, marked experimental). |
| `PL_RESIZER_SETUP_SLACK_MARGIN` | same | 0..9 ns step 0.05 | 5.75 | Post-CTS setup-repair margin (see observation 1). v1 searched 0..6. |
| `pl_hold` | `PL_RESIZER_HOLD_SLACK_MARGIN` | 0.1, 0.05, 0.15, 0.2 | 0.15 | Post-CTS hold margin. In sweep job 23749131_12, margins 0/0 left a fast-corner hold violation. |
| `PL_RESIZER_SETUP_MAX_UTIL_PCT` | same | unset, 65, 75 | unset | Utilization cap of setup repair; bounds the area cost of large margins. |
| `RUN_POST_GRT_RESIZER_TIMING` | same | false, true | false | Timing repair with global-route parasitics (`OpenROAD.ResizerTimingPostGRT`, marked experimental). |
| `GRT_RESIZER_SETUP_SLACK_MARGIN` | same | 0..4 ns step 0.05 | (inactive; default 0.025) | Only if post-GRT timing repair is on; only that step reads it. |
| `grt_hold` | `GRT_RESIZER_HOLD_SLACK_MARGIN` | 0.05, 0.02, 0.1 | (inactive; 0.05) | Only if post-GRT timing repair is on; only that step reads it. |
| `CTS_SINK_CLUSTERING_SIZE` | same | unset, 10, 16, 25, 35 | 16 | Sinks per leaf cluster: clock latency and skew, which the input and reset paths see directly. |
| `CTS_SINK_CLUSTERING_MAX_DIAMETER` | same | unset, 30, 60, 100 um | unset | Leaf cluster diameter. |
| `CTS_MAX_SLEW` | same | unset, 0.4, 0.75, 1.2 ns | unset (lib: 2.5 ns) | CTS characterization slew limit. |
| `CTS_MAX_CAP` | same | unset, 0.1, 0.2 pF | 0.2 | CTS characterization capacitance limit (lib: 0.3 pF). |
| `CTS_CLK_MAX_WIRE_LENGTH` | same | 0, 250, 500 um | 0 | `repair_clock_nets` maximum wire length. |
| `CTS_OBSTRUCTION_AWARE` | same | unset, true | true | Keeps clock buffers off the macros. |
| `grt_adj_m2/m3/m4` | `GRT_LAYER_ADJUSTMENTS[1..3]` | 0, 0.1, 0.2, 0.3 (Metal4: ≤ 0.2) | 0.2 / 0 / 0.1 | Per-layer global-routing capacity reduction (see observation 2). Metal1 and TopMetal1 stay 0. |
| `GRT_MACRO_EXTENSION` | same | 0, 1 | 0 | GCells added around macro blockages. |

**6x4 space.** The `diet4_6x4` track searches the same knobs with three
restrictions (`space.defs("6x4")`, `tracks.Track.island_free_restrict()`):

- the floorplan is fixed to `fp6_tworow_trk_top30_edgeobs`, the placement of
  `variants6x4/config.overlay.json`, including its `FP_OBSTRUCTIONS` box;
- the horizontal halo is fixed to 16.48;
- `FP_MACRO_VERTICAL_HALO` is limited to the island-free values 10 and 5
  (next section).

### Macro halo and short power straps

The halo values follow the short-strap rule of `docs/drc-triage.md` section
6. pdngen adds a short strap when a standard-cell row segment between
macros is not crossed by a lattice stripe.

- **`fp8_base` and `fp8_base_trk`.** The paired macros are 32.96 um apart,
  so the halo stays exactly 16.48. At that value the halos meet and the gap
  holds no rows. A larger value fails `macros/check_macro_floorplan.py`
  ("closer than two halos").
- **`fp8_wide(_trk)`.** The gaps are 100.4 um. A lattice stripe pair runs
  through the middle of every gap, at macro x + 286.99, so any halo below
  about 50 um leaves every row segment crossed.
- **`fp8_spread_trk`.** The gaps are at least 167.8 um, with two stripes
  per gap. The halo is limited by the I/O pin clearance of the first top
  macro.
- **`fp6_tworow_trk_top30_edgeobs` (6x4).**
  - The pair gaps are 32.96 um again, so the halo is exactly 16.48.
  - The stripe-free gap between the top row's last macro and the right core
    edge is covered by the floorplan's `FP_OBSTRUCTIONS` box
    (`docs/6x4.md` section 2). That box starts at the top row's y minus the
    default 10 um vertical halo.
  - `variants6x4/row_islands.py` finds, with halo 16.48, 0 island row
    segments for vertical halos 5 and 10. It finds 1 for 15 and 3 for 20,
    each of which would get a short strap.

**Checks on every trial.**

- For every 8x4 floorplan and halo combination of the table,
  `row_islands.py` predicts 0 islands at every vertical halo.
- Before any LibreLane run, the driver runs `row_islands.py` on every
  snapshot's `config.json` and rejects a snapshot with an island.
- `make_snapshot.py` runs the floorplan checker on every snapshot and
  refuses a point that fails it.
- Every trial is still checked on its own PDN DEF and final LEF (Objective,
  item 1), and every promoted configuration passes through the precheck.

### Never searched

- **Kept as in `src/config.json` and the TT template:** die size (tiles,
  except that the 6x4 track uses the 6x4 die), the PDN keys (`FP_PDN_*`,
  `src/sram_pdn_cfg.tcl`), pin placement (`FP_IO_*`, the DEF template),
  `RT_MAX_LAYER`, and every key below "DO NOT CHANGE".
- **`CLOCK_PERIOD`** is a property of a track (20, 15 or 13.33 ns), not a
  knob.
- **Every timing-constraint variable:** `IO_DELAY_CONSTRAINT`,
  `CLOCK_UNCERTAINTY_CONSTRAINT`, `CLOCK_TRANSITION_CONSTRAINT`,
  `OUTPUT_CAP_LOAD`, `MAX_TRANSITION_CONSTRAINT`,
  `MAX_CAPACITANCE_CONSTRAINT`, `TIME_DERATING_CONSTRAINT` and the SDC
  files. Changing them would change what STA measures, not the design.
- **`DRT_OPT_ITERS` (retired in v1):** it was searched over 64 and 80. Both
  trials with 80 stopped in `OpenROAD.DetailedRouting` with `drt.tcl, 107
  Wrong number of arguments :utl::warn` from the OpenROAD build in the SIF
  (jobs 23995145 and 23995228). Every trial with 64 got past that step. The
  knob is fixed at 64, the LibreLane default (`space.RETIRED`).
- **`OPENROAD_THREADS`:** trial runs use 32 threads (a local-only deviation
  recorded in `params.json`). Promoted runs use the committed value, 4.

## Method

### Sampler

The sampler is optuna 5.0.0 `TPESampler`, one study per track:

- `multivariate=True` and `group=True` (the space is conditional);
- `constant_liar=True` (up to about 21 trials run at once);
- `n_startup_trials=12` and `n_ei_candidates=48`.

The study state is an optuna journal file per track
(`optimizer/studies/<track>.journal`). optuna is installed in a private venv
(`optimizer/venv`). Imported trials count as completed trials of the study,
so `dor` and the variant tracks go straight to TPE sampling.

### Allocation

The driver shares new trials between the tracks that may receive one:

- **Weights:** `dor` 0.35, `dor15` 0.22, `dor13` 0.08, `diet4_6x4` 0.20,
  and 0.15 for the variant tracks together. A variant track gets 0.15
  divided by the number of variant tracks still receiving trials.
- **What counts:** a track's count is the number of its trials submitted
  to LibreLane. Imported, duplicate and rejected trials use no CPUs and do
  not count.
- **Start:** a track with no submitted trial goes first, in weight order.
  So every track gets a trial in the first loop.
- **After that:** the next trial goes to the track with the smallest
  (count + 1) / weight.
- **Tracks that stop receiving trials** (retired, or a variant track at 200
  trials) drop out. The others keep their proportions.
- **Duplicates and rejections:** a track that yields 4 of them in one loop
  waits for the next loop.

### Retirement

A variant track is **retired**, which means it receives no new trials, when:

- it has at least 40 finished trials (imported ones included); and
- either:
  - it has no legal trial; or
  - its best legal minimum setup WS is at least 0.25 ns below the best of
    `dor` (the design of record at 20 ns), and its utilization is not 0.05
    or more below the utilization of that best.

The reasoning:

- Every variant changes the design's contract (`docs/variants.md`
  section 4). It is worth keeping only if it buys margin or area over the
  design of record.
- 0.25 ns is 25 times the 10 ps resolution of the ranking and 5 times the
  0.05 ns promotion threshold.
- 0.05 utilization separates the `diet` cores (0.40 to 0.45 at their best)
  from the rest (0.59 to 0.66).

Retirement is recorded as a `track_retire` event with the numbers that
triggered it, and it is permanent. It applies only to variant tracks: the
design of record, the frequency tracks and the 6x4 fallback are never
retired.

At the v2 launch no variant track had 40 finished trials (they had 10 to
13). The comparison is always with the current best of `dor`.

- Against v1's best (3.00 ns), `cn`, `cn_s2`, `cn_s2_timing` and `rstreg`
  (best min WS 2.40 to 2.51 ns, utilization within 0.05) met the margin
  condition. `diet2` and `diet4` (utilization 0.40 and 0.45) and
  `rstreg_timing` (3.53 ns) did not.
- The best of `dor` rose to 4.28 ns by 17:00 (trial #150). Against that,
  `rstreg_timing` also meets the margin condition unless it improves.
- The rule acts only when a track reaches 40 finished trials.

### One trial

1. **Snapshot.** `scripts/sweep/make_snapshot.py` (`build()`, imported from
   the frozen tree) writes the snapshot. It contains the committed files,
   the track's core, die and period, the knob values as `make_snapshot`
   parameters (floorplan, density, hold margins) and LibreLane overrides
   (`space.materialize()`), and the tt-support-tools config merge.
   `info.yaml` gets the track's `tiles` and `clock_hz`.
   - A knob set identical to one already run, running or imported in the
     same track is recorded as a duplicate and reuses that result. No
     LibreLane job runs.
   - `row_islands.py` runs on the snapshot. A snapshot with an island is
     rejected.
2. **Slurm job.** One job per trial: `tools/opt/trial_job.sh`, 32 CPUs,
   64 GB, 6 h limit, on `mit_preemptable,mit_normal` with `--requeue` and
   `--signal=B:USR1@900`. It runs `scripts/sweep/run_one.sh` in fast mode.
   - Fast mode skips `Magic.DRC`, `Magic.SpiceExtraction`, the
     illegal-overlap check and LVS.
   - Post-route STA at all corners, antenna, the stream-outs and
     `Magic.WriteLEF` still run.
   - The wrapper forwards the USR1 time-limit warning to `run_one.sh`, which
     records a `timeout` result.
   - Around the run, the wrapper also:
     - runs the PDN DEF check as soon as `OpenROAD.GeneratePDN` finishes
       (`run_one.sh` deletes the DEF at the end);
     - runs `postprocess.py` afterwards: the LEF check, and the worst setup
       path start and end points per corner;
     - finally writes `opt_done.json`.
3. **Result.** The driver reads `result.json` and `opt_post.json` and
   computes legality, the rank key and the scalar at the track's period. It
   appends a `trial_done` event to the store, tells the study, and rewrites
   the leaderboard.

### Promotion

A promotion runs the same knob values in full mode, with the track's die and
period, `OPENROAD_THREADS` 4 (the committed value), 8 CPUs and an 11 h limit.

If that run is legal, including LVS 0, two jobs follow:

- **Precheck.** `tools/opt/precheck_job.sh` runs the precheck reproduction
  of `docs/drc-triage.md` on a submission-like directory: the snapshot's
  `info.yaml` (with the track's `tiles` and `clock_hz`) and the GDS, LEF and
  unpowered netlist, gunzipped from the run's `final/`. That reproduction is
  tt-support-tools `d66cf179e` `precheck.py`, unmodified, with KLayout from
  the SIF.
- **Gate-level tests.** `tools/opt/gl_job.sh` runs `make GATES=yes` in a
  copy of the frozen `test/`. It uses the final netlist, Icarus 13.0,
  cocotb 2.0.1 and the flow's PDK, with `PE_VARIANT` of the track: `base`
  for the design-of-record tracks, `diet4` for `diet4_6x4`, and the variant
  name for a variant track. A variant without `configs/variants/<name>.json`
  in the frozen tree is reported as not run.

The verdict is PASS only if all three pass.

What gets promoted (at most 5 promotions in flight):

- **Control:** the committed configuration of `dor` and of
  `diet4_6x4` goes through the whole pipeline once. For `dor` this was
  already done: the committed configuration is v1's p010, which is PASS.
- **Best of a track:** the best legal trial of a track is promoted when
  its minimum WS is at least 0.05 ns above the best promoted trial of that
  track. Promotions of the trials an imported trial stands for count here.
  A track first needs this many finished trials: `dor` 0, `dor15`/`dor13`
  10, `diet4_6x4` 10, variant 20.
- **Periodic:** for `dor`, `dor15` and `dor13`, once a track has at least
  50 × (its promotions + 1) trials of its own (imported ones not counted),
  the best not-yet-promoted trial among its top three is promoted. For
  `dor`, the ten v1 promotions count, so this starts at 550 own trials.

### Scheduling limits, preemption and pruning

**Limits.**

- All `pe-v2-optimizer-*` jobs together stay at or below 700 CPUs, queued
  and running, driver included. Every submission is charged against the
  CPUs free at the start of the loop.
- Submissions stop while this user has 400 or more jobs queued (the
  per-user limit is 448, shared by all workstreams).
- At most 24 submissions per loop.
- A failed `sbatch` backs off for 5 minutes.
- Sign-off jobs go first, then promotions, then trials.

**Preemption.**

- A preempted trial is requeued by Slurm and restarts from its snapshot.
- A job that ends without a result is resubmitted up to twice. The driver
  waits 10 minutes for the pool to show a late `result.json` first.

**Driver.**

- The driver is itself a job: 2 CPUs on `mit_preemptable`, 2-day limit,
  `--requeue`.
- It rebuilds its state from the store at every start. A trial that optuna
  holds as running with no store record is closed as failed. An import
  that reached the study but not the store is recorded.
- About 30 minutes before its time limit, it submits its successor with
  `--dependency=afterany` on itself and exits.
- A driver that finds another driver running exits at once.

**Pruning.**

- A finished run keeps `params.json`, `result.json`, `opt_*.json` and
  `out/` (metrics, logs tarball). Its `snap/` is deleted.
- `out/final/` (the gzipped GDS, netlists and SPEF) is kept only for the ten
  best legal trials of each track, the committed-configuration trials and
  promoted runs.
- Slurm logs are gzipped.
- The driver never prunes a directory outside its own optimizer root.

## Budget and stop conditions

The campaign stops starting new work at whichever comes first:

- **3,000 LibreLane runs** (trials plus promoted full runs, counted since
  v1's first launch; 227 had been submitted when v2 started). Trials stop 20
  runs earlier, to leave room for the last promotions.
- **2026-10-24 00:00** (cluster local time).
- **The file `$PE_WORK/optimizer/STOP` exists.**

On the date or the STOP file, trial jobs that have not started are
cancelled. Running jobs finish and are recorded. When nothing is in flight,
the driver writes the final leaderboard and `optimizer/FINISHED`, and does
not resubmit itself.

## Operation

```sh
export PE_WORK=<cluster work directory>
python3 tools/opt/test_opt.py       # unit tests (standard library, no cluster)
srun -p mit_quicktest -c 4 --mem 8G -t 15 tools/opt/dry_run.sh $PE_WORK/<scratch dir> 2
                                    # dry run: copy of the store, 2 loops, nothing submitted
tools/opt/launch.sh                 # export HEAD + tools/opt, point optimizer/current at it,
                                    # create the venv if missing, submit the driver (no-op if queued)
P=$PE_WORK/optimizer/venv/bin/python
$P $PE_WORK/optimizer/current/tools/opt/driver.py status   # per track: trials by state, imports, retirement
less $PE_WORK/optimizer/leaderboard.md                     # rewritten after every finished trial
```

- **Dry runs.** `dry_run.sh` works like `launch.sh`:
  - it exports HEAD with the working tree's `tools/opt` and copies the
    store;
  - it runs `driver.py once --dry-run` against the copy;
  - a dry driver records the job id `dry` for every submission and plans
    for the whole CPU cap;
  - it neither polls nor prunes run directories outside its own root, so
    the campaign's runs are untouched;
  - its snapshots, `driver.log` and `leaderboard.md` show what a real loop
    would do.

The files under `$PE_WORK/optimizer/`:

| Path | Content |
|---|---|
| `store/events.jsonl` | Append-only record of every track, trial, import, submission, result, retirement and promotion (`tools/opt/store.py`) |
| `leaderboard.md`, `leaderboard.csv` | One section per track (committed configuration, best legal configuration with its exact config difference, promotions, top trials), the earlier tracks, blockers; every trial with every metric |
| `manifest.json` | Every Slurm job id (drivers, trials, promotion stages) |
| `runs/<track>/<run id>/` | Sweep-format run directories (`params.json`, `result.json`, `opt_post.json`, `out/`) |
| `promotions/<pid>/` | `sub/` (submission-like directory), `precheck/`, `gl/` with `result.json` |
| `driver.log`, `logs/` | Driver log; Slurm logs (gzipped when finished) |
| `tree/<commit>-<tools>/`, `current` | Frozen exports; the driver job uses `current` |

- **Stop:** `touch $PE_WORK/optimizer/STOP`. To stop at once, also run
  `scancel -n` on the job names.
- **Stop only the driver** (to relaunch with new tools): `scancel` the
  `pe-v2-optimizer-driver` job. Trial and promotion jobs are separate jobs
  and keep running; the next driver records their results from the store
  and the run directories.
- **Resume after a stop:** remove `STOP` and `FINISHED`, then run
  `tools/opt/launch.sh`.
- **Follow a new commit or updated tools:** run `tools/opt/launch.sh`
  again. It exports a new tree and moves `current`. The running driver
  keeps its tree until its successor starts; cancel it to switch earlier.
  - If a fixed key of `src/config.json` changed, new tracks start.
  - If only knob-controlled keys changed (an adopted configuration), the
    tracks continue with the new committed configuration as their
    baseline.

## Adopting a configuration

Only a promoted configuration with verdict PASS qualifies. That means a
full run that is legal including LVS 0, the precheck passing all checks and
the gate-level tests without failures. Each track's section of the
leaderboard shows its promotions with their job ids. It also prints the
exact difference of its best legal configuration from the frozen
`src/config.json`:

- The difference lists the keys to set and the keys to remove. It is
  computed from the knob values (`tracks.Track.diff_vs_repo()`), without the
  local-only `OPENROAD_THREADS`, and equals `make_snapshot.py`'s
  `config_changes_vs_repo`.
- If the floorplan differs, it says so. In that case, copy the
  `MACROS.RM_IHPSG13_1P_64x16_c2.instances` block from
  `floorplans/<id>.json`.

Steps for the 8x4 design of record at 20 ns:

1. Add the keys to `src/config.json` above the "DO NOT CHANGE" line, and
   remove the listed keys. Add a `"//"` comment that names the promotion id
   and its job ids. Do not edit anything below that line.
2. Run `python3 macros/check_macro_floorplan.py`. It must pass.
3. Optionally, rebuild a snapshot from the edited repository with
   `scripts/sweep/make_snapshot.py --mode full --threads 4`. Its
   `config_changes_vs_repo` must be empty, which confirms that the edit
   reproduces the promoted configuration.
4. Commit and push. The GitHub gds, precheck and gl_test actions on that
   commit are the result of record; compare their timing with the promoted
   run.

**A frequency-track configuration** (`dor15` or `dor13`): the difference
also contains `CLOCK_PERIOD`. In addition, set `info.yaml` `clock_hz` to
the value the leaderboard gives (66666667 for 15 ns). The datasheet
(`docs/info.md`) and any text that states 50 MHz then need the same change.

**A 6x4 configuration** (`diet4_6x4`): the leaderboard prints a second
difference, from the committed 6x4 build (`src/config.json` with
`variants6x4/config.overlay.json` merged, as `variants6x4/switch.py apply`
writes it).

- Those keys go into `variants6x4/config.overlay.json`; a key to remove
  becomes `null` there.
- A key that `src/config.json` does not have must also be listed in
  `variants6x4/PROVENANCE.json` `overlay_new_keys`, or `switch.py check`
  fails.
- Then run `python3 variants6x4/switch.py check`. The CI workflow
  `gds_6x4.yaml` is the result of record for the 6x4 build.

## Campaign record

### v1 (2026-09-26 03:36 to 13:40)

**Launch.** The campaign was launched on 2026-09-26 from commit `be7dbda`.
`src/` at that commit is identical to `c118027`. Driver jobs:

| Driver job | Started | Why |
|---|---|---|
| 23993146 | 03:36 | First launch |
| 23994123 | 03:54 | Relaunch: `postprocess.py` records the resizer messages |
| 23995423 | 04:40 | Relaunch: `DRT_OPT_ITERS` retired |
| 23996205 | 04:53 | Relaunch: an unused variable removed from `driver.py`; no change in behavior |
| 24020736 | 13:35 | Relaunch on tree `131e793` with the same tools; cancelled at 13:40 (see [Absolute knob values](#absolute-knob-values)) |

Each relaunch resumed from the store and submitted nothing twice: the store
held the same 19 trial submissions before and after the first relaunch.

**Pre-launch smoke tests.** These ran on compute nodes; the job ids are in
`optimizer/smoke/SMOKE.json`.

- **PDN watcher on the design-of-record PDN** (job 23993041): 26 VPWR and
  26 VGND stripes, 0 short.
- **Precheck job body on the `c118027` CI GDS** (job 23992848): 9/9 checks
  pass.
- **Gate-level job body on the `c118027` CI netlist** (job 23992849): 102
  tests, 46 pass, 56 skip, 0 fail.

**First wave, design of record.** All numbers are fast mode, from
`optimizer/store/events.jsonl` at 05:15. Trials 4 (DELAY 3), 6 (ABC
buffering) and 18 (combination) were still in detailed routing then.

| Trial | Job | Point | Legal | typ / fast / slow setup WS (ns) | Slow worst start | Utilization |
|---:|---|---|---|---|---|---:|
| 0 | 23993203 | Committed configuration (`c118027`) | yes | +0.89 / +6.15 / −8.52 | `rst_n` | 0.5853 |
| 1 | 23993204 | Setup margin 2 ns | yes | +2.93 / +6.66 / −5.10 | `rst_n` | 0.5856 |
| 2 | 23993205 | Setup margin 5 ns | yes | +5.06 / +7.32 / −1.10 | a flip-flop | 0.5861 |
| 3 | 23993206 | Post-GRT timing repair, margin 1 ns | yes | +2.08 / +6.12 / −6.65 | `ena` | 0.5859 |
| 5 | 23993208 | `SYNTH_STRATEGY` DELAY 1 | no: route DRC 18, 1 typ setup violation | −0.04 / +4.11 / −9.34 | `ui_in[7]` | 0.6012 |
| 7 | 23993210 | `MAX_FANOUT_CONSTRAINT` 6 | yes | +1.85 / +5.66 / −7.00 | `rst_n` | 0.5990 |
| 8 | 23993212 | Repair max wire length 300 um | no: route DRC 8 | +4.37 / +7.64 / −2.63 | `rst_n` | 0.5983 |
| 9 | 23993214 | Repair slew/cap margins 40% | no: 23 typ setup violations | −0.38 / +5.35 / −10.50 | `rst_n` | 0.5861 |
| 10 | 23993215 | Timing-driven placement | yes | +3.67 / +7.88 / −3.71 | `ui_in[3]` | 0.5843 |
| 11 | 23993217 | CTS slew 0.75 ns, clusters of 16 | no: 43 typ setup violations | −0.54 / +5.24 / −10.68 | `rst_n` | 0.5926 |
| 12 | 23993219 | `fp8_base_trk` | no: 7 typ setup violations | −0.37 / +5.39 / −10.50 | `rst_n` | 0.5853 |
| 13 | 23993220 | `fp8_wide_trk`, halo 10 | yes | +1.19 / +6.35 / −8.01 | `rst_n` | 0.5850 |
| 14 | 23993221 | Density 52 | yes | +1.36 / +5.92 / −7.78 | `rst_n` | 0.5852 |
| 15 | 23993223 | Density 68 | yes | +0.78 / +5.52 / −8.54 | `rst_n` | 0.5855 |
| 16 | 23993224 | GRT Metal2/Metal3 adjustment 0.2 | yes | +1.42 / +5.91 / −7.73 | `rst_n` | 0.5858 |
| 17 | 23993226 | Hold margin 0.05 | yes | +0.23 / +5.89 / −9.77 | `rst_n` | 0.5784 |

**What the first wave showed.**

- **The baseline trial reproduces the official result.** It matches the
  typ and slow WS of GitHub run 36144357821 (+0.89 / −8.52 ns). Its
  slew/cap/fan-out counts are 290/73/524.
- **Setup margin.** At 5 ns (trial 2), the post-CTS resizer found 2,282
  endpoints below the margin, inserted 79 buffers and resized 58 instances
  (`RSZ-0062`: not all repaired). Slow-corner WS moved from −8.52 to
  −1.10 ns, and typ WS to +5.06 ns.
- **Timing-driven placement** (trial 10) reached slow-corner WS −3.71 ns.
  Detailed routing converged in 6 iterations (46 in run2), and global
  routing ended with 0 overflow.
- **Trials 19 and 20** (TPE) were the two trials with `DRT_OPT_ITERS` 80
  and failed as described under "Never searched".
- **TPE trial 23** (job 23995853) was the first trial with positive setup
  WS at all three corners in fast mode: typ/fast/slow +6.40/+8.68/+0.73 ns.

**Promotions of v1.** All ten passed the whole pipeline (full run legal with
LVS 0, precheck 9/9, gate-level 102 tests: 46 pass, 56 skip, 0 fail). Full
runs use `OPENROAD_THREADS` 4; WS is from the full run, at 20 ns.

| Promotion | Trial | Full run | Precheck | Gate level | typ / fast / slow setup WS (ns) | Utilization |
|---|---:|---|---|---|---|---:|
| p001 (control, `c118027`) | 0 | 23993269 | 23997237 | 23997238 | +0.885 / +6.153 / −8.517 | 0.5853 |
| p002 | 10 | 23994350 | 23997459 | 23997460 | +3.675 / +7.878 / −3.709 | 0.5843 |
| p003 | 2 | 23994927 | 23997682 | 23997683 | +5.059 / +7.321 / −1.102 | 0.5861 |
| p004 | 23 | 23997360 | 23998685 | 23998686 | +6.404 / +8.676 / +0.732 | 0.5777 |
| p005 | 33 | 23999287 | 24002102 | 24002103 | +6.509 / +8.945 / +0.918 | 0.6004 |
| p006 | 39 | 24000165 | 24003018 | 24003019 | +7.038 / +8.679 / +1.683 | 0.5990 |
| p007 | 41 | 24000424 | 24003204 | 24003205 | +7.751 / +9.375 / +2.085 | 0.6111 |
| p008 | 45 | 24002310 | 24004838 | 24004839 | +7.323 / +9.212 / +2.197 | 0.6018 |
| p009 | 56 | 24003206 | 24004529 | 24004530 | +7.367 / +8.857 / +2.629 | 0.6177 |
| p010 (adopted in `25e331e`) | 94 | 24010051 | 24011934 | 24011935 | +7.880 / +9.307 / +2.954 | 0.6192 |

**Variant tracks of v1.** Each had 12 trials, including the committed
configuration and transfers of the design of record's best. Best legal trial
per core, fast mode, at 20 ns:

| Core | Best min WS (ns) | typ / slow WS (ns) | Utilization |
|---|---:|---|---:|
| rstreg_timing | 3.529 | +9.31 / +3.53 | 0.6635 |
| diet2 | 3.464 | +8.26 / +3.46 | 0.4005 |
| diet4 (8x4) | 2.933 | +8.36 / +2.93 | 0.4516 |
| rstreg | 2.508 | +7.58 / +2.51 | 0.6226 |
| cn | 2.495 | +7.66 / +2.50 | 0.5882 |
| cn_s2 | 2.433 | +8.05 / +2.43 | 0.5887 |
| cn_s2_timing | 2.401 | +9.05 / +2.40 | 0.6396 |

Every variant changes the design's contract in some way
(`docs/variants.md` section 4); the diet cores also change the ISA and the
queue depth. These trials rank the variants on physical design only. The
best legal trial of the design of record at that point was v1 `#118`
(min WS 2.998 ns, utilization 0.6196), which was not promoted: its gain over
p010's 2.954 ns is below the 0.05 ns promotion threshold.

### v2 (from 2026-09-26 14:11)

**Before the launch.**

- `tools/opt/test_opt.py`: 8 tests pass.
- Three dry runs on `mit_quicktest` against copies of the store (jobs
  24023899, 24024091 and 24024248). They created the 11 tracks, imported
  196 trials, enqueued the seeds and planned full loops. They also built
  15 ns, 13.33 ns and 6x4 snapshots:
  - the 15 ns snapshot's only config change is `CLOCK_PERIOD` 15, with
    `info.yaml` `clock_hz` 66666667;
  - the 6x4 committed-build snapshot's changes are exactly the overlay's
    (`FP_MACRO_HORIZONTAL_HALO` 16.48, `FP_OBSTRUCTIONS`, `MACROS`,
    `PL_TARGET_DENSITY_PCT` 60), with `tiles` "6x4", the
    `tt_block_6x4_pgvdd.def` template and the `diet4` core. The floorplan
    checker passed.
- The driver of v1 (job 24020736) was cancelled at 13:40. Its 19 trial jobs
  kept running. The v2 driver recorded the 13 that had finished in its first
  loop (11 of them imported).

**Launch.** `tools/opt/launch.sh` at 14:11 on tree `131e793` with tools
`05d186a8` (driver job 24024261). It created the tracks
`dor-26a873-8x4-p20-f174dc0`, `dor15-26a873-8x4-p15-f174dc0`,
`dor13-26a873-8x4-p13p33-f174dc0`, `diet4_6x4-cc27c4-6x4-p20-f174dc0` and one
track per variant core (`cn`, `cn_s2`, `cn_s2_timing`, `diet2`, `diet4`,
`rstreg`, `rstreg_timing`), imported 207 trials in its first loop, and
submitted 15 trials (all the CPUs the cap left next to the 6 v1 trial jobs
still running). Every track got one; `dor` got three, `dor15` and `diet4_6x4`
two each.

**First trials of the new tracks** (fast mode, from the store; the
leaderboard has the current state). WS is at the track's period.

| Trial | Job | Seed | Legal | typ / fast / slow setup WS (ns) | fmax estimate typ / fast / slow (MHz) | Utilization |
|---|---|---|---|---|---|---:|
| `dor15` #0 | 24024331 | committed configuration at 15 ns | yes | +5.92 / +6.82 / +1.46 | 110.1 / 122.3 / 73.9 | 0.6512 |
| `dor15` #1 | 24024343 | 20 ns best #1 (v1 `#118`) | yes | +5.95 / +6.92 / +2.24 | 110.5 / 123.8 / 78.4 | 0.6530 |
| `dor15` #3 | 24026026 | 20 ns best #4 | yes | +6.11 / +7.02 / +1.65 | 112.5 / 125.3 / 74.9 | 0.6645 |
| `dor15` #5 | 24028165 | 20 ns best #6 | yes | +4.91 / +6.18 / +1.66 | 99.1 / 113.4 / 75.0 | 0.6448 |
| `diet4_6x4` #0 | 24024332 | committed 6x4 build | no: route DRC 69, LibreLane exit 2 | +5.65 / +9.29 / −0.63 | – | 0.6097 |
| `diet4_6x4` #1 | 24024344 | 6x4 point signed off before the adoption | yes | +4.30 / +8.50 / −2.80 | 63.7 / 86.9 / 43.9 | 0.5628 |
| `diet4_6x4` #2 | 24025965 | 20 ns 8x4 best #1, translated | yes | +6.72 / +9.89 / +1.26 | 75.3 / 98.9 / 53.4 | 0.6059 |
| `diet4_6x4` #4 | 24027464 | 20 ns 8x4 best #3, translated | yes | +7.29 / +10.28 / +2.06 | 78.7 / 102.9 / 55.8 | 0.5888 |
| `dor` #134 | 24024341 | TPE | yes | +8.14 / +9.49 / +3.55 | 84.3 / 95.1 / 60.8 | 0.6061 |
| `dor` #143 | 24028378 | TPE | yes | +8.12 / +9.55 / +3.63 | 84.2 / 95.6 / 61.1 | 0.6187 |

- **15 ns.**
  - The committed configuration meets setup at all three corners at
    15 ns in fast mode, with +1.46 ns at the slow corner.
  - The STA logs show the SDC scaling described above: input and output
    delay 3.0 ns in `dor15` #0 (job 24024331) against 4.0 ns in the 20 ns
    trial `dor` #134 (job 24024341). Uncertainty 0.25 ns, transition
    0.15 ns and derate 5% are the same in both.
  - 15 ns trials take longer than 20 ns ones: 66 to 118 minutes against a
    v1 median of 32.
- **6x4.**
  - The committed 6x4 build (the overlay on the adopted `src/config.json`)
    ended with 69 route DRC violations in fast mode. The GitHub gds_6x4 run
    of `131e793` (36257636751) failed with the same count: `69 Routing DRC
    errors found`. The control promotion p012 runs the same configuration
    in full mode with `OPENROAD_THREADS` 4 (full run job 24029191).
  - The pre-adoption 6x4 point reproduced its full-mode sign-off numbers of
    `docs/6x4.md` (+4.29 / +8.50 / −2.80 ns, utilization 56.3%, hold
    minimum +0.099 ns) to within 0.01 ns.
  - Two translated 20 ns configurations are legal with positive setup WS
    at all corners (+1.26 and +2.06 ns at the slow corner).
- **20 ns.**
  - TPE trial #134 (setup margin 6.35 ns, outside v1's range) was promoted
    as p011: **PASS**.
    - Full run job 24026087 (`OPENROAD_THREADS` 4): legal, LVS 0, setup WS
      typ/fast/slow +8.138/+9.487/+3.548 ns, the same as the trial's.
    - Precheck job 24033557: 9/9.
    - Gate-level job 24033558: 102 tests, 46 pass, 56 skip, 0 fail.
  - Trial #143 was promoted as p013: **PASS**.
    - Full run job 24030864: legal, LVS 0, setup WS typ/fast/slow
      +8.120/+9.545/+3.634 ns.
    - Precheck job 24036014: 9/9.
    - Gate-level job 24036159: 102 tests, 46 pass, 56 skip, 0 fail.
  - By 17:00, TPE trial #150 (job 24033993) reached +7.64/+9.04/+4.28 ns
    with 3/2/0 slew/cap/fan-out violations and utilization 0.6178. It is
    promoted as p015 (full run job 24037612).
  - In the same period, 6x4 trial #10 (job 24035515) reached +2.76 ns at
    the slow corner and is legal. The best 6x4 trial at 16:39, #8
    (+2.74 ns, job 24032431), is promoted as p014 (full run job 24036160).
- **13.33 ns.** The first finished trial was `dor13` #4 (job 24037546,
  seed "20 ns best #5", 27 minutes):
  - not legal: one fast-corner hold violation (hold WS −0.022 ns), so
    LibreLane exited with 2;
  - setup WS typ/fast/slow +2.49/+5.29/−2.35 ns (register-to-register
    +4.69/+11.30/−0.50); the worst slow path starts at `ui_in[3]`;
  - its STA log sets input and output delay to 2.666 ns, 20% of 13.33 ns;
  - its `info.yaml` has `clock_hz` 75018755.

  Trials #0 to #3 were still running at 17:25. #0 was in post-CTS repair
  (see [Seeds](#seeds), revision 2).

**Relaunches.** Each relaunch cancelled only the driver job; trial and
promotion jobs kept running, and each new driver resumed from the store.
None of them created a track, imported a trial twice or submitted a trial
twice.

| Driver job | Started | Tools | Why |
|---|---|---|---|
| 24024261 | 14:11 | `05d186a8` | v2 launch |
| 24037129 | 16:52 | `caa2d93f` | Documentation strings only (`space.py` text, `README.md`) |
| 24038948 | 17:17 | `9b5326e2` | Seed revision 2. `dry_run.sh` also copies the study journals. Dry run 24038928 checked it on a copy of the live store first. |

## Limitations

- **Fast trials differ from promoted runs.**
  - Trials use `OPENROAD_THREADS` 32; the action uses 4. Four runs of the
    submission point gave identical results at 4, 32 and 48 threads
    (`docs/sweep.md`, "Repeatability"), but detailed routing can depend on
    the thread count. Promoted runs therefore use 4.
  - Trials do not run LVS.
- **Imported trials rely on determinism.** An imported trial is the result
  of an identical effective configuration on an earlier frozen tree. The
  flow inputs are identical: only `src/config.json` changed between
  `be7dbda` and `131e793` among the flow's inputs, and only in
  knob-controlled keys. The result is not re-run.
- **The LEF check approximates the precheck.** It merges only vertically
  touching rectangles with the same x span; the precheck merges all touching
  rectangles. For the full-height vertical stripes of this design the two
  agree. The precheck itself runs on every promotion.
- **The island model is a model.** `row_islands.py` reproduces every
  earlier strap observation (`docs/6x4.md` section 2). The PDN DEF and LEF
  checks of every trial and the precheck of every promotion remain the
  checks that count.
- **The slow corner is not a sign-off corner.** Objective 2 still ranks by
  it, because the goal is margin at all corners.
- **fmax estimates extrapolate** from one period (see "Frequency tracks and
  the SDC"). The frequency tracks measure at their period instead.
- **Variant-track gate-level tests** use the committed firmware images,
  because `build/variants/` is not in the frozen export. The results are
  reported as they are. The variant cores' own verification belongs to the
  variant workflow.
- **TPE is a heuristic.** The leaderboard reports only what was run. No
  claim is made that an optimum was reached.
