# Physical-design optimizer

`tools/opt/` is an autonomous search over LibreLane configuration knobs for
the 8x4 design of record, and, as a second track, for the variant cores that
the variant workflow publishes. It runs on the Engaging cluster through the
sweep harness of `docs/sweep.md`, which it uses unchanged. It records every
run in an append-only store, and it promotes its best configurations to a
full-flow run, the Tiny Tapeout precheck and the gate-level cocotb subset.

Like every local run, a result here is evidence. It is not the result of
record (`docs/hardening.md` sections 7 and 9). A configuration becomes the
design of record only after it is committed to `src/config.json` and the
GitHub gds, precheck and gl_test actions pass on that commit (see
[Adopting a configuration](#adopting-a-configuration)).

## Starting point

The design of record at tag `v0.1-hardened` (`c118027`) passed the official
gds, precheck and gl_test actions in GitHub run 36144357821. That run had
utilization 58.5% and, at 50 MHz (20 ns):

- typ setup worst slack (WS) +0.89 ns;
- slow-corner setup WS −8.52 ns;
- route DRC, LVS and antenna all 0.

The flow signs off setup only at the typ corner (`TIMING_VIOLATION_CORNERS`
`*typ*`), so the slow corner does not fail the action. Almost every
slow-corner violation starts at `rst_n`: 2,467 of the 2,482 in that run.

The optimizer's own baseline trial repeats the committed configuration
under the harness. The leaderboard shows it as "Baseline trial".

Two observations shaped the search space. The first comes from sweep run
`base-26a873-8x4-fp8_base-d60-p20-h0p1_0p05-fast-t32` (job 23749131_0,
before the halo change). The second comes from LibreLane itself.

1. **The post-CTS resizer barely acts on setup.**
   - In job 23749131_0, the post-CTS resizer log
     (`37-openroad-resizertimingpostcts`) reports `RSZ-0098 No setup
     violations found`, although all three corners are loaded
     (`RSZ_CORNERS` unset, so `STA_CORNERS`).
   - Post-route STA of the same run then shows slow-corner WS −5.09 ns.
   - The worst slow path is `rst_n` → input buffer → a chain of
     `sg13cmos5l_buf_1` fan-out buffers with slews of about 0.9 ns → logic →
     flip-flop. With placement-estimated parasitics, the path shows positive
     slack.
   - The optimizer's baseline trial of the design of record (job 23993203)
     behaves the same way. Its resizer found 5 violating endpoints and resized
     1 instance, and its routed slow-corner WS is −8.52 ns.
   - So the setup-repair margins (`PL_RESIZER_SETUP_SLACK_MARGIN`, and the
     post-GRT repair) are searched up to several nanoseconds, not around their
     0.05/0.025 ns defaults. The first wave confirmed this lever (see
     [Campaign record](#campaign-record)).
2. **`GRT_ADJUSTMENT` has no effect in this flow.**
   - `scripts/openroad/common/set_layer_adjustments.tcl` (LibreLane
     3.1.0.dev3) first applies `GRT_ADJUSTMENT` to every layer.
   - It then overrides each layer with `GRT_LAYER_ADJUSTMENTS`, and the IHP
     PDK sets that for all five routing layers (`0,0,0,0,0`).
   - The per-layer values are therefore searched instead.

## Objective

A run is ranked lexicographically (`tools/opt/objective.py`).

1. **Legal.** All of the following must hold:
   - the flow completed with LibreLane exit 0 and post-route multi-corner STA;
   - route DRC 0, antenna violations 0 and critical disconnected pins 0;
   - no setup violation at the typ corner (the gds action fails otherwise);
   - no hold violation at any corner;
   - no power-port violation. `tools/opt/checks.py` checks both:
     - the short VPWR/VGND Metal4 straps of `docs/drc-triage.md` section 6,
       on the `OpenROAD.GeneratePDN` DEF;
     - the precheck's power-port rule on the final LEF;
   - in a promoted full run, also LVS 0.
2. **Maximum of the minimum setup WS over the typ, fast and slow corners at
   20 ns.** The comparison uses 10 ps resolution.
3. **Minimum of the sum of the max-slew, max-cap and max-fan-out violation
   counts.** These are 285, 88 and 519 in run2 (`docs/hardening.md`
   section 7), and 290, 73 and 524 in the baseline trial.
4. **Maximum of the typ-corner fmax estimate**, `1000 / (20 − typ WS)` MHz.
   This is the sweep harness's estimate, with its caveats (`docs/sweep.md`,
   "Collecting").
5. **Minimum utilization.**

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
`floorplans/` (the `MACROS` instances). A Tiny Tapeout project may set these
in `src/config.json` above the template's "DO NOT CHANGE" line. The name,
type, default and effect of each knob were checked against the `librelane`
package inside `librelane-3.1.0.dev3.sif`: `steps/pyosys.py`,
`steps/openroad.py`, `steps/common_variables.py`, `config/flow.py`,
`flows/classic.py` and `scripts/openroad/*.tcl`. The "committed value" is the
effective value in the design of record: `src/config.json`, or the
LibreLane/PDK default recorded in the design of record's `resolved.json`. The
table is generated from `tools/opt/space.py`.

| Knob | LibreLane variable | Values searched | Committed value | Why |
|---|---|---|---|---|
| `floorplan` | `MACROS` instances | fp8_base, fp8_base_trk, fp8_wide, fp8_wide_trk, fp8_spread_trk | fp8_base | Macro placement sets the pin-access channels, the routing detours around the SRAMs and the reset/clock wire lengths across the 1724 um die. |
| `halo_h_wide` | `FP_MACRO_HORIZONTAL_HALO` | 16.48, 10, 12.5, 20, 25 | 16.48 | Only for fp8_wide(_trk). Horizontal keep-out around each macro (see below). |
| `halo_h_spread` | `FP_MACRO_HORIZONTAL_HALO` | 16.48, 10, 12.5, 20 | 16.48 | Only for fp8_spread_trk; ≤ 22.32 keeps the first top macro clear of the I/O pins. |
| `FP_MACRO_VERTICAL_HALO` | same | 10, 5, 15, 20 | 10 | Keep-out above/below the macros, which is the pin-access channel of the SRAM signal pins (all on the edge facing the logic). |
| `density` | `PL_TARGET_DENSITY_PCT` | 50..72 | 60 | Global-placement target density: wire length versus routing congestion. |
| `SYNTH_STRATEGY` | same | AREA 0-3, DELAY 0-4 | AREA 0 | ABC mapping script; DELAY scripts shorten logic depth at an area cost. |
| `abc_fine_tune` | `SYNTH_ABC_BUFFERING` / `SYNTH_SIZING` | none, buffering, sizing | none | `construct_abc_script.py` applies buffering first when both are set, so the two are one choice. |
| `MAX_FANOUT_CONSTRAINT` | same | 10, 8, 6 | 10 (PDK) | Fan-out limit for ABC buffering and repair_design. Only values ≤ 10 are searched, because the value is also the SDC `set_max_fanout` limit that objective 3 counts against. |
| `PL_TIMING_DRIVEN` | same | false, true | false | Timing-driven global placement. |
| `PL_ROUTABILITY_DRIVEN` | same | true, false | true | Routability-driven global placement. |
| `gpl_pad` | `GPL_CELL_PADDING` | 0, 2, 4 | 0 (PDK) | Global-placement padding (sites, split over both sides). |
| `dpl_pad` | `DPL_CELL_PADDING` | 0, 2 | 0 (PDK) | Only if `gpl_pad` ≥ 2, so that it never exceeds `GPL_CELL_PADDING`. |
| `DESIGN_REPAIR_MAX_WIRE_LENGTH` | same | 0, 150, 300, 500, 800 um | 0 (off) | Buffering of long wires; the reset and enable nets span the die. |
| `DESIGN_REPAIR_MAX_SLEW_PCT` | same | 10..50 step 5 | 20 | Slew margin of repair_design. |
| `DESIGN_REPAIR_MAX_CAP_PCT` | same | 10..50 step 5 | 20 | Capacitance margin of repair_design. |
| `RUN_POST_GRT_DESIGN_REPAIR` | same | false, true | false | repair_design with global-route parasitics (`OpenROAD.RepairDesignPostGRT`, marked experimental). |
| `PL_RESIZER_SETUP_SLACK_MARGIN` | same | 0..6 ns step 0.05 | 0.05 | Post-CTS setup-repair margin (see observation 1). |
| `pl_hold` | `PL_RESIZER_HOLD_SLACK_MARGIN` | 0.1, 0.05, 0.15, 0.2 | 0.1 | Post-CTS hold margin. In sweep job 23749131_12, margins 0/0 left a fast-corner hold violation. |
| `PL_RESIZER_SETUP_MAX_UTIL_PCT` | same | unset, 65, 75 | unset | Utilization cap of setup repair; bounds the area cost of large margins. |
| `RUN_POST_GRT_RESIZER_TIMING` | same | false, true | false | Timing repair with global-route parasitics (`OpenROAD.ResizerTimingPostGRT`, marked experimental). |
| `GRT_RESIZER_SETUP_SLACK_MARGIN` | same | 0..4 ns step 0.05 | 0.025 | Only if post-GRT timing repair is on; only that step reads it. |
| `grt_hold` | `GRT_RESIZER_HOLD_SLACK_MARGIN` | 0.05, 0.02, 0.1 | 0.05 | Only if post-GRT timing repair is on; only that step reads it. |
| `CTS_SINK_CLUSTERING_SIZE` | same | unset, 10, 16, 25, 35 | unset | Sinks per leaf cluster: clock latency and skew, which the input and reset paths see directly. |
| `CTS_SINK_CLUSTERING_MAX_DIAMETER` | same | unset, 30, 60, 100 um | unset | Leaf cluster diameter. |
| `CTS_MAX_SLEW` | same | unset, 0.4, 0.75, 1.2 ns | unset (lib: 2.5 ns) | CTS characterization slew limit. |
| `CTS_MAX_CAP` | same | unset, 0.1, 0.2 pF | unset (lib: 0.3 pF) | CTS characterization capacitance limit. |
| `CTS_CLK_MAX_WIRE_LENGTH` | same | 0, 250, 500 um | 0 | `repair_clock_nets` maximum wire length. |
| `CTS_OBSTRUCTION_AWARE` | same | unset, true | unset | Keeps clock buffers off the macros. |
| `grt_adj_m2/m3/m4` | `GRT_LAYER_ADJUSTMENTS[1..3]` | 0, 0.1, 0.2, 0.3 (Metal4: ≤ 0.2) | 0 (PDK) | Per-layer global-routing capacity reduction (see observation 2). Metal1 and TopMetal1 stay 0. |
| `GRT_MACRO_EXTENSION` | same | 0, 1 | 0 | GCells added around macro blockages. |

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

Every trial is still checked on its own PDN DEF and final LEF (Objective,
item 1), and every promoted configuration passes through the precheck.
`make_snapshot.py` runs the floorplan checker on every snapshot and refuses
a point that fails it.

### Never searched

- **Kept as in `src/config.json` and the TT template:** die size (tiles),
  `CLOCK_PERIOD`, the PDN keys (`FP_PDN_*`, `src/sram_pdn_cfg.tcl`), pin
  placement (`FP_IO_*`, the DEF template), `RT_MAX_LAYER`, and every key
  below "DO NOT CHANGE".
- **Every timing-constraint variable:** `IO_DELAY_CONSTRAINT`,
  `CLOCK_UNCERTAINTY_CONSTRAINT`, `CLOCK_TRANSITION_CONSTRAINT`,
  `OUTPUT_CAP_LOAD`, `MAX_TRANSITION_CONSTRAINT`,
  `MAX_CAPACITANCE_CONSTRAINT`, `TIME_DERATING_CONSTRAINT` and the SDC
  files. Changing them would change what STA measures, not the design.
- **`DRT_OPT_ITERS` (retired after launch):** it was searched over 64 and
  80. Both trials with 80 stopped in `OpenROAD.DetailedRouting` with
  `drt.tcl, 107 Wrong number of arguments :utl::warn` from the OpenROAD
  build in the SIF (jobs 23995145 and 23995228). Every trial with 64 got
  past that step. The knob is fixed at 64, the LibreLane default
  (`space.RETIRED`).
- **`OPENROAD_THREADS`:** trial runs use 32 threads (a local-only deviation
  recorded in `params.json`). Promoted runs use the committed value, 4.

## Method

### Tracks and studies

Each track has its own optuna study.

- The **primary track** is the design of record: `src/` of the frozen
  export.
- There is also **one track per core listed in
  `$PE_WORK/variants/cores/MANIFEST.sha256`**:
  - the driver rescans that file every 10 minutes, so new cores (for
    example reset-tree variants) are picked up automatically;
  - a core is used only if its sha256 matches the MANIFEST;
  - a core whose body equals the design of record (the published `base.v`)
    is skipped;
  - each core is copied to `optimizer/cores/<name>-<sha12>.v`, so a
    republish cannot change a track.
- The track id contains the core sha256 and the `src/config.json` sha256.
  A new committed configuration therefore starts a new primary track and
  never mixes with the old one.

### Sampler

The sampler is optuna 5.0.0 `TPESampler`:

- `multivariate=True` and `group=True` (the space is conditional);
- `constant_liar=True` (about 19 trials run at once);
- `n_startup_trials=12` and `n_ei_candidates=48`.

The study state is an optuna journal file per track
(`optimizer/studies/<track>.journal`). optuna is installed in a private venv
(`optimizer/venv`).

Each study starts with a designed first wave, `space.FIRST_WAVE`:

- the baseline (committed configuration);
- one mechanism at a time: setup margin 2 and 5 ns; post-GRT timing repair;
  DELAY 3 and DELAY 1; ABC buffering; fan-out 6; repair wire length 300 um;
  repair margins 40%; timing-driven placement; CTS slew/clustering;
  `fp8_base_trk`; `fp8_wide_trk` with halo 10; density 52 and 68; GRT
  Metal2/Metal3 adjustment; hold margin 0.05;
- one combination.

A variant track starts with the baseline, the combination, the 2 ns margin
and the three best legal configurations of the primary track. After that,
each variant track is also offered the primary track's current best, once
per distinct best.

### Allocation

- The primary track's first wave is submitted first.
- After that, each new trial goes to the track with the smallest
  (trials + 1) / weight. The weights are 0.6 for the primary track and
  0.4 shared equally by the variant tracks.
- A variant track stops receiving trials at 200.

### One trial

1. **Snapshot.** `scripts/sweep/make_snapshot.py` (`build()`, imported from
   the frozen tree) writes the snapshot: the committed files, the knob values
   as `make_snapshot` parameters (floorplan, density, hold margins) and
   LibreLane overrides, and the tt-support-tools config merge.
   - The run id is derived from the parameters. An identical effective
     configuration in the same track reuses the earlier result and runs no
     LibreLane job.
   - Values equal to the committed value are left out of the overrides.
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
   computes legality, the rank key and the scalar. It appends a
   `trial_done` event to the store, tells the study, and rewrites the
   leaderboard.

### Promotion

A promotion runs the same knob values in full mode, with
`OPENROAD_THREADS` 4 (the committed value), 8 CPUs and an 11 h limit.

If that run is legal, including LVS 0, two jobs follow:

- **Precheck.** `tools/opt/precheck_job.sh` runs the precheck reproduction
  of `docs/drc-triage.md` on a submission-like directory: `info.yaml` and
  the GDS, LEF and unpowered netlist, gunzipped from the run's `final/`.
  That reproduction is tt-support-tools `d66cf179e` `precheck.py`,
  unmodified, with KLayout from the SIF.
- **Gate-level tests.** `tools/opt/gl_job.sh` runs `make GATES=yes` in a
  copy of the frozen `test/`. It uses the final netlist, Icarus 13.0,
  cocotb 2.0.1 and the flow's PDK. For a variant track it sets
  `PE_VARIANT=<name>`, and skips the tests if the frozen tree has no
  `configs/variants/<name>.json`.

The verdict is PASS only if all three pass.

What gets promoted:

- **Control.** The committed configuration of the primary track goes
  through the whole pipeline once.
- **Best of a track.** The best legal trial of a track is promoted when its
  minimum WS is at least 0.05 ns above the best promoted trial of that
  track. A variant track first needs 20 finished trials.
- **Periodic.** Every 50 primary trials, the best not-yet-promoted trial
  among the top three is promoted.

At most three promotions are in flight at a time.

### Scheduling limits, preemption and pruning

**Limits.**

- All `pe-v2-optimizer-*` jobs together stay at or below 640 CPUs, queued
  and running, driver included. Every submission is charged against the
  CPUs free at the start of the loop.
- Submissions stop while this user has 400 or more jobs queued (the
  per-user limit is 448, shared by all workstreams).
- At most 20 submissions per loop.
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
  holds as running with no store record is closed as failed.
- About 30 minutes before its time limit, it submits its successor with
  `--dependency=afterany` on itself and exits.
- A driver that finds another driver running exits at once.

**Pruning.**

- A finished run keeps `params.json`, `result.json`, `opt_*.json` and
  `out/` (metrics, logs tarball). Its `snap/` is deleted.
- `out/final/` (the gzipped GDS, netlists and SPEF) is kept only for the ten
  best legal trials of each track, the baseline and promoted runs.
- Slurm logs are gzipped.

## Budget and stop conditions

The campaign stops starting new work at whichever comes first:

- **1,500 LibreLane runs** (trials plus promoted full runs). Trials stop 12
  runs earlier, to leave room for the last promotions.
- **2026-10-10 00:00** (cluster local time).
- **The file `$PE_WORK/optimizer/STOP` exists.**

On the date or the STOP file, trial jobs that have not started are
cancelled. Running jobs finish and are recorded. When nothing is in flight,
the driver writes the final leaderboard and `optimizer/FINISHED`, and does
not resubmit itself.

## Operation

```sh
export PE_WORK=<cluster work directory>
tools/opt/launch.sh                 # export HEAD + tools/opt, point optimizer/current at it,
                                    # create the venv if missing, submit the driver (no-op if queued)
P=$PE_WORK/optimizer/venv/bin/python
$P $PE_WORK/optimizer/current/tools/opt/driver.py status   # counts, promotions, jobs, lease
less $PE_WORK/optimizer/leaderboard.md                     # rewritten after every finished trial
```

| Path under `$PE_WORK/optimizer/` | Content |
|---|---|
| `store/events.jsonl` | Append-only record of every trial, submission, result and promotion (`tools/opt/store.py`) |
| `leaderboard.md`, `leaderboard.csv` | Ranking, best configuration and its diff, promotions, variant tracks, blockers; every trial with every metric |
| `manifest.json` | Every Slurm job id (driver, trials, promotion stages) |
| `runs/<track>/<run id>/` | Sweep-format run directories (`params.json`, `result.json`, `opt_post.json`, `out/`) |
| `promotions/<pid>/` | `sub/` (submission-like directory), `precheck/`, `gl/` with `result.json` |
| `driver.log`, `logs/` | Driver log; Slurm logs (gzipped when finished) |
| `tree/<commit>-<tools>/`, `current` | Frozen exports; the driver job uses `current` |

- **Stop:** `touch $PE_WORK/optimizer/STOP`. To stop at once, also run
  `scancel -n` on the job names.
- **Resume after a stop:** remove `STOP` and `FINISHED`, then run
  `tools/opt/launch.sh`.
- **Follow a new commit or updated tools:** run `tools/opt/launch.sh`
  again. It exports a new tree and moves `current`. The running driver
  keeps its tree until its successor starts. Cancel the driver job to
  switch earlier; running trials are not affected. If `src/` changed, the
  new configuration becomes a new primary track.

## Adopting a configuration

Only a promoted configuration with verdict PASS qualifies. That means a
full run that is legal including LVS 0, the precheck passing all checks and
the gate-level tests without failures. The leaderboard's "Promotions"
section shows these results with their job ids. It also prints the exact
difference from the committed `src/config.json`:

- The difference lists the keys to set, taken from `params.json`
  `config_changes_vs_repo` without the local-only `OPENROAD_THREADS`.
- If the floorplan differs, it says so. In that case, copy the
  `MACROS.RM_IHPSG13_1P_64x16_c2.instances` block from
  `floorplans/<id>.json`.

Steps:

1. Add the keys to `src/config.json` above the "DO NOT CHANGE" line, with a
   `"//"` comment that names the promotion id and its job ids. Do not edit
   anything below that line.
2. Run `python3 macros/check_macro_floorplan.py`. It must pass.
3. Optionally, rebuild a snapshot from the edited repository with
   `scripts/sweep/make_snapshot.py --mode full --threads 4`. Its
   `config_changes_vs_repo` must be empty, which confirms that the edit
   reproduces the promoted configuration.
4. Commit and push. The GitHub gds, precheck and gl_test actions on that
   commit are the result of record; compare their timing with the promoted
   run.

## Campaign record

**Launch.** The campaign was launched on 2026-09-26 from commit `be7dbda`.
`src/` at that commit is identical to `c118027`. Driver jobs:

| Driver job | Started | Why |
|---|---|---|
| 23993146 | 03:36 | First launch |
| 23994123 | 03:54 | Relaunch: `postprocess.py` records the resizer messages |
| 23995423 | 04:40 | Relaunch: `DRT_OPT_ITERS` retired |
| 23996205 | 04:53 | Relaunch: an unused variable removed from `driver.py`; no change in behavior |

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

**First wave, primary track.** All numbers are fast mode, from
`optimizer/store/events.jsonl` at 05:15. Trials 4 (DELAY 3), 6 (ABC
buffering) and 18 (combination) were still in detailed routing then; the
leaderboard has the current state.

| Trial | Job | Point | Legal | typ / fast / slow setup WS (ns) | Slow worst start | Utilization |
|---:|---|---|---|---|---|---:|
| 0 | 23993203 | Committed configuration | yes | +0.89 / +6.15 / −8.52 | `rst_n` | 0.5853 |
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

**What the first wave shows.**

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
- **TPE trial 23** (job 23995853, finished 05:20) is the first trial with
  positive setup WS at all three corners, in fast mode:
  - typ/fast/slow +6.40/+8.68/+0.73 ns; hold WS at the fast corner
    +0.109 ns;
  - slew/cap/fan-out violations 231/55/321; utilization 0.5777.
  - Its configuration combines `PL_TIMING_DRIVEN`, a 4.95 ns setup margin,
    density 70, `SYNTH_STRATEGY` AREA 2, GPL/DPL padding 4/2, vertical halo
    15, repair margins 30%/30% and `CTS_MAX_CAP` 0.1 (exact keys in the
    leaderboard).
  - It is promoted as p004. It is not a sign-off result until p004 has run.

**Promotions.** Full runs use `OPENROAD_THREADS` 4.

- **p001, the control: PASS.** The committed configuration went through the
  whole pipeline.
  - Full run (job 23993269): legal, LVS 0, typ/slow setup WS
    +0.885/−8.517 ns, utilization 0.5853.
  - TT precheck (job 23997237): 9/9 checks, 335 s.
  - Gate-level tests (job 23997238): 102 tests, 46 pass, 56 skip, 0 fail.
- **p002 and p003: full runs in flight at 05:20.**

| Promotion | Trial | Full-run job |
|---|---|---|
| p002 | 10 (timing-driven placement) | 23994350 |
| p003 | 2 (setup margin 5 ns) | 23994927 |
| p004 | 23 (TPE, slow WS +0.73 ns in fast mode) | 23997360 |

**Variant tracks.** The committed-configuration trials that had finished:

| Core | Job | typ WS | slow WS | Utilization |
|---|---|---:|---:|---:|
| diet4 | 23994351 | +5.49 ns | −0.65 ns | 0.4221 |
| diet2 | 23994947 | +4.92 ns | −2.06 ns | 0.3740 |
| cn | 23994948 | +2.75 ns | −5.58 ns | 0.5441 |
| cn_s2 | 23994815 | +2.40 ns | −6.64 ns | 0.5448 |
| rstreg | 23994929 | +1.42 ns | −7.33 ns | 0.5856 |

All five are legal in fast mode. Every variant changes the design's
contract in some way (`docs/variants.md` section 4); the diet cores also
change the ISA and the queue depth. These trials rank the variants on
physical design only.

## Limitations

- **Fast trials differ from promoted runs.**
  - Trials use `OPENROAD_THREADS` 32; the action uses 4. Four runs of the
    submission point gave identical results at 4, 32 and 48 threads
    (`docs/sweep.md`, "Repeatability"), but detailed routing can depend on
    the thread count. Promoted runs therefore use 4.
  - Trials do not run LVS.
- **The LEF check approximates the precheck.** It merges only vertically
  touching rectangles with the same x span; the precheck merges all touching
  rectangles. For the full-height vertical stripes of this design the two
  agree. The precheck itself runs on every promotion.
- **The slow corner is not a sign-off corner.** Objective 2 still ranks by
  it, because the goal is margin at all corners.
- **Variant-track gate-level tests** use the committed firmware images,
  because `build/variants/` is not in the frozen export. The results are
  reported as they are. The variant cores' own verification belongs to the
  variant workflow.
- **TPE is a heuristic.** The leaderboard reports only what was run. No
  claim is made that an optimum was reached.
