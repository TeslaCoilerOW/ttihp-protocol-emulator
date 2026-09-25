# Local LibreLane sweep harness (MIT Engaging)

This harness runs many variants of the Tiny Tapeout hardening flow in parallel
on the Engaging cluster. It exists to explore clock period, placement density,
hold margins, macro floorplans, tile size and core variants. It does not replace
the GitHub gds action: a local result is evidence, not the result of record
(`docs/hardening.md` sections 7 and 9).

Every run reproduces the local action mirror of `docs/hardening.md` section 7.
That means the same LibreLane 3.1.0.dev3 image (SIF), the same IHP PDK revision
and the same tt-support-tools `d66cf179e` config merge. The only differences
are the parameters you choose, plus the local-only deviations listed below.

Files:

| Path | Role |
|---|---|
| `scripts/sweep/make_snapshot.py` | Builds one self-contained run snapshot plus `params.json` (hashes). |
| `scripts/sweep/run_one.sh` | Slurm job body. It stages the snapshot to node-local disk, runs LibreLane, writes `result.json` and prunes. |
| `scripts/sweep/extract_result.py` | Summarises any LibreLane run directory (finished, failed or partial) as JSON. |
| `scripts/sweep/submit.py` | Expands a matrix, builds snapshots, submits job arrays and appends to the manifest. Idempotent. |
| `scripts/sweep/collect.py` | Writes `results.csv` and `summary.md` from the results and from squeue/sacct. |
| `scripts/sweep/sweeplib.py` | Shared paths and helpers. Standard library only, Python 3.6+. |
| `scripts/sweep/gen_floorplans.py` | Writes `floorplans/*.json` from their derivations. |
| `scripts/sweep/matrices/*.yaml` | Sweep definitions: `baseline-8x4.yaml`, the variant-core sweep `variants.yaml` and its follow-ups `variants-followup.yaml`, and the harness smoke tests `smoke-pdn.yaml` and `smoke-m4obs.yaml`. |
| `floorplans/*.json` | Macro placements that a run can select. |
| `macros/check_macro_floorplan.py` | Tool-free floorplan checker. It now validates any floorplan file at any tile size. |

Nothing in the harness modifies the submission (`src/`, `info.yaml`,
`macros/RM_*`, `models/`, `.github/`). Snapshots are copies.

## Where things live

Everything is under `SWEEP_ROOT = $PE_WORK/sweep`
(override with `PE_SWEEP_ROOT`):

```
manifest.json            append-only: one record per (run, submission); submissions made
                         under another root (the smoke test) are mirrored here with
                         their sweep_root and kind "aux:<root>", and collect.py/submit.py
                         only consider records of their own root
runs/<run_id>/
  params.json            every parameter, sha256 of core/config/snapshot, deviations
  floorplan_check.txt    check_macro_floorplan.py output on the snapshot
  snap/                  the run input: src/, info.yaml, macros/, models/, tt/…/def, SNAPSHOT.sha256
  status.json            latest attempt: job id, node, local dir, state
  staging.log            one line per attempt (node + node-local path)
  result.json            written at the end of every attempt (result.prev.json = the one before)
  out/                   kept outputs (see "Pruning")
logs/<job-name>-<array>_<task>.out   Slurm output (appended across requeues)
arrays/*.list            run directory per array index
harness/<sha>/           frozen copy of run_one.sh + extract_result.py + sweeplib.py used by the jobs
results.csv, summary.md  written by collect.py
```

The flow inputs come from `PE_FLOW_ROOT = …/tt-work/sram-flow`:
`librelane-3.1.0.dev3.sif`, `pdk/` (sparse IHP-Open-PDK `2bbec755`) and `tt/`
(tt-support-tools `d66cf179e`, for `tile_sizes.yaml` and the DEF templates).

## Run identity and snapshots

`make_snapshot.py` takes these parameters:

* core Verilog and a short tag;
* tiles;
* clock period in ns;
* `PL_TARGET_DENSITY_PCT`;
* floorplan id;
* PL/GRT hold margins;
* mode (`fast` or `full`);
* local `OPENROAD_THREADS`;
* extra LibreLane overrides (`KEY=JSON`, where `null` deletes the key).

It starts from the repository files and changes only the following:

* `info.yaml`: `tiles` and `clock_hz` (1e9 / period, rounded). Every other byte
  is unchanged.
* `src/config.json`: `CLOCK_PERIOD`, `PL_TARGET_DENSITY_PCT`,
  `PL_RESIZER_HOLD_SLACK_MARGIN`, `GRT_RESIZER_HOLD_SLACK_MARGIN`,
  `OPENROAD_THREADS`, `MACROS.<macro>.instances` from the floorplan file, then
  any overrides. The file is loaded with `json.load` (as tt-support-tools does)
  and re-written. The duplicate `"//"` comment keys therefore collapse, and
  nothing else changes. `params.json` lists every key that differs from the
  repository (`config_changes_vs_repo`).
* `src/protocol_emulator_core.v` is replaced by the chosen core Verilog.

It then writes `src/user_config.json` and `src/config_merged.json` exactly as
`tt_tool.py --create-user-config` does (`project.py` `create_user_config()` and
`create_merged_config()`, `config_utils.py` `read_json_config()`). The keys are
`DESIGN_NAME`, `VERILOG_FILES`, `DIE_AREA` from `tile_sizes.yaml`,
`FP_DEF_TEMPLATE dir::../tt/tech/ihp-sg13cmos5l/def/tt_block_<tiles>_pgvdd.def`,
`VDD_PIN`, `GND_PIN` and `RT_MAX_LAYER Metal4`, and the `"//"` key is dropped.
Check: with `--threads 4` and the defaults, the snapshot's `user_config.json`
is byte-identical to run2's. Its `config_merged.json` has the same keys and
values as run2's, apart from the `"//"` comment key that `run.sh` kept.

The run id is readable and derived from the parameters, for example
`base-26a873-8x4-fp8_base-d60-p20-h0p1_0p05-fast-t32`. The parts are the core
tag, the first 6 hex digits of the core sha256, tiles, floorplan, density,
period, hold margins, mode and threads. If there are overrides, a hash of them
is appended. The same parameters always give the same id. If an existing run
id has different snapshot content, the script refuses unless you pass
`--force`.

**Local-only deviations**, recorded in each `params.json`:

* `OPENROAD_THREADS` is set to the run's thread count. `src/config.json` and
  the action use 4. The thread count can change detailed-routing results.
* LibreLane runs in apptainer rather than docker. The image is the same.
* `--jobs` is set to the allocated CPU count (the runner has 4 vCPUs).
* In `fast` mode, the steps below are skipped.

## Modes

* **full**: the complete Classic flow, as in the action. This includes Magic
  DRC (non-fatal on cmos5l), SPICE extraction, the illegal-overlap check,
  Netgen LVS and all final checkers.
* **fast**: `--skip Magic.DRC --skip Checker.MagicDRC --skip
  Magic.SpiceExtraction --skip Checker.IllegalOverlap --skip Netgen.LVS --skip
  Checker.LVS`. Everything up to and including post-route STA runs, together
  with IR drop, the Magic and KLayout stream-outs, render, `Magic.WriteLEF`,
  the design antenna check, and the setup/hold/slew/cap checkers and
  manufacturability report.

  This is LibreLane's own mechanism. `librelane/flows/sequential.py`
  (`SequentialFlow.run`, 3.1.0.dev3 as installed in the SIF) resolves each
  `--skip` id against the flow's step ids at start-up, so an unknown id aborts
  the run at once. It then skips exactly those steps. The ids come from
  `steps/magic.py`, `steps/netgen.py` and `steps/checker.py` in the same SIF.
  `KLayout.DRC` and `KLayout.XOR` are already gated off by the TT template.
  `Checker.IllegalOverlap` only reads the `magic__illegal_overlap__count`
  metric that `Magic.SpiceExtraction` writes, so it is skipped with it.

  The alternative would be gating variables (`RUN_MAGIC_DRC`, `RUN_LVS`) in the
  config. That would change the config hash and would not stop
  `Magic.SpiceExtraction`. With `--skip`, `config_merged.json` stays identical
  between the fast and full runs of a point, apart from `OPENROAD_THREADS`.

Reference timings from run2 (job 23720702, 8 CPUs, `OPENROAD_THREADS` 4) are:
detailed routing 1 h 50 min, global routing 8 min, Magic DRC 44 min, SPICE
extraction 33 s and LVS 17 s. The whole flow took 2 h 52 min (10,330 s of
LibreLane time).

## Job body (`run_one.sh`)

1. Pick the node-local base. It uses `/scratch/$USER` if that is writable with
   at least `PE_MIN_LOCAL_GB` (40) GB free. Otherwise it uses `/tmp/$USER`, and
   as a last resort `RUN_DIR/work` on the pool. Some 64-core preemptable
   nodes have no `/scratch`, but their `/tmp` has about 420 GB.
2. Copy `snap/` to `<base>/pe-sweep/<run_id>.<job>` and verify
   `SNAPSHOT.sha256`.
3. Run the same command as `sram-flow/run.sh`:
   `apptainer exec --cleanenv --containall --no-home … python3 -m librelane
   --pdk-root <pdk> --pdk ihp-sg13cmos5l --manual-pdk --run-tag wokwi
   --force-run-dir runs/wokwi --hide-progress-bar --jobs $SLURM_CPUS_PER_TASK
   [--skip …] src/config_merged.json`. Apptainer is found through
   `module load apptainer/1.4.2`, or through the module's self-contained
   wrapper at `the cluster's apptainer module binary (`module load apptainer/1.4.2`)`.
4. Always write `result.json`, including after a LibreLane failure. Jobs are
   submitted with `--signal=B:USR1@900`. On that warning, 15 minutes before
   the time limit, the flow is stopped and the partial result is recorded
   with status `timeout`.
5. Prune (see below) and delete the node-local directory.

Preemption on `mit_preemptable` uses REQUEUE with `GraceTime=0`, so a
preempted task is killed at once. Slurm then requeues it, because it was
submitted with `--requeue`, and it restarts from the snapshot. A restart
removes any stale directories of the same run on that node. A directory left
on another node's local disk can be found through `staging.log`.

## Pruning (inode budget)

Each run keeps only the following in `runs/<id>/out/`:

* uncompressed: `resolved.json`, `error.log`, `warning.log`, `flow.log`,
  `metrics.json`, `metrics.csv` and `sta-postpnr-summary.rpt`;
* `librelane-logs.tar.gz`: every file under 64 MB in the run directory except
  layout, netlist and timing-model binaries (`*.odb *.def *.gds *.mag *.spef
  *.sdf *.v *.lib *.guide *.png *.spice *.h.json *.lyrdb`). That covers step
  logs, reports, per-step configs, state JSON, runtimes, process stats, and
  the STA path reports;
* `final/` **only if `result.json` has `candidate: true`**: gzip-compressed
  `gds`, `nl.v`, `pnl.v`, per-corner SPEF, SDC and LEF, plus `SHA256SUMS`.

`candidate` means all of the following:

* the flow completed and post-route STA ran;
* 0 detailed-routing DRC errors, 0 antenna violations and 0 critical
  disconnected pins;
* no setup violations at the typical corner (the TT sign-off corner,
  `TIMING_VIOLATION_CORNERS`);
* no hold violations at any corner;
* in full mode, 0 LVS errors.

## Matrices and submission

```sh
cd <repo>
python3 scripts/sweep/submit.py scripts/sweep/matrices/baseline-8x4.yaml --dry-run   # snapshots + plan
python3 scripts/sweep/submit.py scripts/sweep/matrices/baseline-8x4.yaml             # submit
```

A matrix has `defaults` (run parameters plus a `slurm` block with
`partition`, `cpus`, `mem`, `time` and `requeue`) and a list of `runs`. Any
of `core`, `tiles`, `floorplan`, `density`, `period`, `mode` and `threads`
can be a list, and the lists expand as a cartesian product. `hold:
[[pl, grt], …]` gives hold-margin pairs. An optional `env` block is exported
to the jobs. The smoke test uses it for `PE_LL_EXTRA_ARGS`.

Runs are grouped by their Slurm resources. Each group becomes one job array
named `pe-sweep-<matrix>-<partition>-c<cpus>`. The harness scripts are frozen
into `harness/<sha>/` before submission, so editing the repository never
changes a queued job.

Re-running `submit.py` is safe. It skips runs that are queued or running, and
runs whose `result.json` status is `complete` or `complete_with_errors`. It
resubmits runs that ended without a result (preempted, node failure, killed)
or with status `timeout` or `terminated`. `failed` means LibreLane itself
errored, and those runs are resubmitted only with `--retry-failed`.
`--rerun-complete` resubmits everything, and `--only TAG` selects runs by tag.
Every submission appends records to `manifest.json`: run id, tag, matrix,
parameters, core sha256, `config_sha256`, `config_merged_sha256`,
`snapshot_sha256`, partition/cpus/mem/time, array id and task, job id
(`<array>_<task>`), harness sha and repository HEAD.

A run can pin its core file with `core_sha256: <hex>`. `submit.py` aborts
before submitting anything if the file's sha256 differs, for example because
a variant core was republished under the same name. The snapshot keeps its
own copy of the core, so a later republish never changes a queued run.

**CPU budget (`--cpu-budget N`).** This caps the CPUs of all of this user's
`pe-sweep-*` tasks on each partition at N, queued and running together. It
works like this:

* Runs that fit into the free capacity are submitted normally.
* Every other run is submitted as its own one-task array with
  `--dependency=afterany:<job>` on an earlier `pe-sweep` task. That task can
  be one already in the queue or one submitted earlier in the same call.
* The run starts only when that task ends, so it takes over the CPUs the
  task releases. Capacity is conserved, so the cap holds at every moment
  and nothing has to poll.
* A preempted and requeued task has not ended, so its dependents keep
  waiting. `afterany` also fires when a task fails, so its capacity is
  reused at once.

`submit.py` rebuilds the current capacity from `squeue` (`%E` shows open
dependencies). Running tasks, and queued tasks without an open dependency,
hold their CPUs now. A queued task with dependencies is charged to the
capacity its dependencies release. A later `--cpu-budget` call can therefore
add runs to the chains of an earlier one without double counting.

Dependency targets are chosen by estimated finish time (`EST_H`: fast 0.5 h,
full 1.5 h at 32 threads; the first fast run took 0.36 h). The estimates only affect the order in which runs
start, never the cap. Matrix order is priority order. `--dry-run` prints the
plan (`plan <run> <dependency>`).

Resources: `mit_normal` allows 96 CPUs per user and 12 h. `mit_preemptable`
allows 1024 CPUs per user and 2 days, and preempts with REQUEUE.
`mit_quicktest` allows 48 CPUs, 15 min and at most 8 submitted jobs. run2's
peak RSS was 6.9 GB on 8 CPUs, and detailed routing peaked at 5 GiB with 4
threads. The defaults are 32 CPUs / 64 GB for fast runs and 48 CPUs / 96 GB
for full runs on preemptable.

## Collecting

```sh
python3 scripts/sweep/collect.py                     # -> <sweep>/results.csv, <sweep>/summary.md
python3 scripts/sweep/collect.py --run-dir $PE_WORK/sram-flow/run2/runs/wokwi:run2 \
        --out-dir /tmp/x                             # any LibreLane run dir, e.g. the pre-harness run2
```

`collect.py` takes the latest manifest record of each run, then reads
`result.json`, or squeue/sacct when there is no result yet. Its outputs are:

* `results.csv`, with one row per run: status, job id and state, the
  parameters, wall time and step times, utilization, GPL target and minimum
  feasible density, GRT overflow per layer, DRT iteration-0 and final
  violations and the iteration count, the predicted-versus-observed DRT-0418
  count, antenna, Magic DRC, illegal overlaps, LVS, per-corner setup/hold
  WS/TNS and violation counts, slew/cap/fanout, area, instance count, hold
  buffers, wirelength, power, the candidate flag and its blockers;
* `summary.md`: a sorted table and the best candidates per tile size.
* `flow_error`, for every run that did not end `complete`: LibreLane's final
  error block, read from the tail of the run's Slurm log. LibreLane prints a
  deferred checker error only on the console and leaves `error.log` empty,
  so `result.json` cannot carry it. An example is `Setup violations found in
  the following corners: * nom_typ_1p20V_25C`, which fails the action.
  `summary.md` lists these runs under "Completed with a LibreLane error
  exit".
* `drt_residual`, for every flow that completed with route DRC errors: the
  final detailed-routing violations, split into those inside and those
  outside a macro footprint, by type and layer. They come from the final
  `<NN>-openroad-detailedrouting/<design>.drc` in the run's
  `librelane-logs.tar.gz`. The footprints come from the snapshot's `MACROS`
  instances and the LEF `SIZE`. The answer is cached in
  `out/drt_residual.json`.

The **fmax estimate** is `1000 / (period − worst setup slack)` MHz per corner,
plus a register-to-register-only version. It assumes every path scales with
the period, which the SDC's I/O delays (a fraction of the period) and the
SRAM's fixed clock-to-output only approximate. Confirm a candidate period by
running it.

Test fixture: `collect.py --run-dir …/sram-flow/run2/runs/wokwi:run2` on the
real run2 (job 23720702, which completed at 01:29 on 2026-09-25) reproduces
the numbers in `docs/hardening.md` section 7:

* utilization 0.585;
* GRT overflow 617 (Metal2 24, Metal3 574, Metal4 19);
* DRT 34,515 violations at iteration 0, 0 after iteration 46;
* 60 DRT-0418 pins;
* typ/fast/slow setup WS +2.94/+6.34/−5.09 ns;
* hold WS min +0.109 ns.

It also shows run2's final state: Magic DRC 69,448 (in-macro, non-fatal),
illegal overlaps 84, LVS 0 errors, LibreLane exit 0 after 10,330 s.

## Floorplans

`floorplans/<id>.json` holds `id`, `tiles`, `macro`, `description`,
`derivation` and `instances` (flattened instance path → `location` and
`orientation`). It may also hold a `config` object with LibreLane keys that
belong to the floorplan. `scripts/sweep/gen_floorplans.py` writes every file
from its derivation.

All placements keep the PDN-stripe alignment of `src/sram_pdn_cfg.tcl`:

* macro x = 11.04 + 67.44 k (the unchanged `FP_PDN_V*` keys);
* orientation N or FS only. A rotation would turn the Metal4 power columns
  horizontal, and a Y-mirror moves them off the stripe lattice. The PDN
  script rejects both.

The `_trk` variants also put every macro signal pin on a Metal2 routing
track. The checker's pin-access rule reproduces run2's 60 DRT-0418 warnings
pin for pin: a pin is flagged when neither its x span nor its y span holds a
Metal2 track (0.48 um pitch in both directions). With the FS row raised
0.09 um to y 3.87, every FS pin holds the Metal2 track at y 68.16 and the
Metal3 track at 68.04.

| id | tiles | placement | minimum macro gap (µm) | macro x off site grid | predicted DRT-0418 |
|---|---|---|---:|---:|---:|
| `fp8_base` | 8x4 | = `src/config.json` (bottom FS k 2/6/14/18 at y 3.78, top N k 6/10/16/20) | 33.0 | 0 | 60 |
| `fp8_base_trk` | 8x4 | fp8_base, FS row at y 3.87 | 33.0 | 0 | 0 |
| `fp8_wide` | 8x4 | `docs/hardening.md` §8: bottom k 2/7/13/18, top k 6/11/16/21 | 100.4 | 4 | 78 |
| `fp8_wide_trk` | 8x4 | fp8_wide, FS row at y 3.87 (best-guess alternative) | 100.4 | 4 | 0 |
| `fp8_spread_trk` | 8x4 | bottom k 0/7/14/21, top k 3/9/15/21, FS y 3.87 | 167.8 | 6 | 0 |
| `fp6_tworow` | 6x4 | `docs/hardening.md` §4 insurance: bottom FS k 0/4/8/12, top N k 3/7/11/15 | 33.0 | 4 | 60 |
| `fp6_tworow_trk` | 6x4 | fp6_tworow, FS y 3.87 | 33.0 | 4 | 0 |
| `fp6_spread_trk` | 6x4 | bottom FS k 0/5/10/15, top N k 3/7/11/15 | 33.0 | 6 | 0 |
| `fp6_block_trk` | 6x4 | compact 2×4 block on the bottom edge: FS row y 3.87 facing an N row at y 158.76, 90.5 µm channel, k 2/6/10/14 | 33.0 | 0 | 0 |
| `fp6_columns_trk` | 6x4 | two stacks of four FS macros at k 0 and k 15, ~100 µm bays, central band macro-free | 100.3 | 4 | 0 |
| `<fp6 id>_m4obs` (5 files) | 6x4 | the same placement plus a Metal4 routing obstruction over every macro footprint (see below) | as base | as base | as base |
| `fp6_tworow_trk_top30` | 6x4 | fp6_tworow_trk, top N row lowered to y 612.36 (row 162): 30.1 µm macro-free channel under the top core edge | 33.0 | 4 | 0 |
| `fp6_tworow_trk_top60` | 6x4 | the same, top row at y 582.12 (row 154): 60.4 µm channel | 33.0 | 4 | 0 |

In 6x4 the top row must start at k ≥ 3, clear of the I/O pins plus the halo,
and end at k ≤ 15. So `k 3/7/11/15` is the only four-macro top row.

**Metal4 obstruction floorplans (`*_m4obs`, added 2026-09-25).** Each of the
five 6x4 floorplans also exists as a `_m4obs` copy. The placement is the
same, and the floorplan's `config` block adds `ROUTING_OBSTRUCTIONS`: one
`Metal4` rectangle per macro footprint (location to location + 236.8 × 64.36 µm).

Why: the first diet4 6x4 runs that did not route clean all stopped with
residual DRT shorts on Metal4 inside a macro footprint. Each short was
0.2 µm wide and the full macro height, against VPWR, VGND or the macro
instance itself:

* `fp6_tworow_trk` d60, 3 shorts, `drt-run` report in the run's log tarball;
* `fp6_spread_trk` d65, 23 Metal4 shorts and 2 Metal3 shorts, over both rows;
* `fp6_block_trk` d55, still running, with 163 violations, all on Metal4.

These are signal nets crossing a macro vertically. Over the macro,
Metal1 and Metal3 are fully blocked and Metal2 is nearly blocked. Only
Metal4 is left, between the LEF's full-height `VDD!`/`VSS!`/`VDDARRAY!`
Metal4 pins and its OBS columns. Some of these nets are I/O nets: `uo_out[1]`
crosses the top row, and its pin is at x 114.24 on the top edge. All 43 I/O
pins are Metal4 pins at x 29.8–191.0 on the top edge in both the 6x4 and the
8x4 DEF templates.

How the obstruction applies: in the 3.1.0.dev3 Classic flow,
`Odb.AddRoutingObstructions` runs right after `OpenROAD.GeneratePDN`, and
`Odb.RemoveRoutingObstructions` runs right after `OpenROAD.DetailedRouting`.
So the PDN over the macros is built as before, and signal nets have to go
around the macros.

Smoke test `smoke-m4obs` (array 23767683_0–1, sweep-smoke root, diet4,
4 CPUs) ran `fp6_tworow_m4obs` and `fp6_block_trk_m4obs` through
`Odb.AddRoutingObstructions`. Each log shows eight "Creating an obstruction
on Metal4" lines and the PDN checks (8 macros, 0 BAD).

`ROUTING_OBSTRUCTIONS` is an ordinary LibreLane key, so the submission's
`src/config.json` could carry it if these runs route clean.

The checker:

```sh
python3 macros/check_macro_floorplan.py                       # the submission (unchanged behaviour + pin-access WARN lines)
python3 macros/check_macro_floorplan.py --floorplan floorplans --summary   # every floorplan file
python3 macros/check_macro_floorplan.py --floorplan floorplans/fp6_block_trk.json --core <variant core.v>
```

Status on 2026-09-25: all ten floorplans PASS the tool-free check. The
`smoke-pdn` matrix (array 23748246, tasks 0–9, mit_preemptable, 4 CPUs each,
base core) ran each of them through `OpenROAD.GeneratePDN` in the
LibreLane 3.1.0.dev3 SIF. That is the step where `src/sram_pdn_cfg.tcl`
checks the macro power hookup. All ten completed the step with exit 0. Each
run's PDN log shows:

* 8 `SRAMPDN macro` lines, with the configured bounding boxes (FS reported as
  MX);
* 64 stripe × macro crossings inside same-net columns;
* no `SRAMPDN BAD` line.

This covers the five 6x4 floorplans too. The base core does not fit 6x4
(78% predicted), but the flow stops before placement, so that does not
matter here. The smoke test uses a separate sweep root
(`…/tt-work/sweep-smoke`), because a `--to` run looks "complete" to the
harness.

## Smoke test

```sh
PE_SWEEP_ROOT=$PE_WORK/sweep-smoke \
  python3 scripts/sweep/submit.py scripts/sweep/matrices/smoke-pdn.yaml
PE_SWEEP_ROOT=$PE_WORK/sweep-smoke python3 scripts/sweep/collect.py
```

`PE_LL_EXTRA_ARGS` (here `--to OpenROAD.GeneratePDN`) is for smoke tests
only. It is recorded in `result.json` as `ll_extra_args`.

## Baseline launch, 2026-09-25 (matrix `base8`, `matrices/baseline-8x4.yaml`)

All runs use the base core `src/protocol_emulator_core.v` (sha256 `26a873db…`)
at 8x4. Unless a row says otherwise, they use `fp8_base`, density 60, 20 ns,
hold 0.1/0.05 and `OPENROAD_THREADS` = CPUs (local-only). Submitted at
01:36–01:39; the preemptable tasks were running at 01:39. Results appear in
`<sweep>/runs/<id>/result.json`. Refresh with `collect.py`.

| Job | Partition, CPUs | Mode | Run (what varies) |
|---|---|---|---|
| 23749129_0 | submitted to mit_normal (`--no-requeue`), 32 | full | canary: submission config, threads 32. At first it was pending on Priority (Slurm estimated a start at 08:31). At 01:41 it started on **mit_preemptable** (node1627) with `Requeue=1`: `scontrol show job` shows the partition changed after submission. The harness did not do that. `result.json` records the real partition. |
| 23749537_0 | mit_preemptable, 32 | full | the same canary, submitted to preemptable because of that wait (`…-full-t32-pre`). With 23749129_0 it is also a same-config, same-thread-count repeatability pair. |
| 23749132_0 | mit_preemptable, 48 | full | canary with 48 CPUs/threads (scaling) |
| 23749131_0 | mit_preemptable, 32 | fast | reference point |
| 23749131_1–3 | mit_preemptable, 32 | fast | density 50 / 55 / 65 |
| 23749131_4–6 | mit_preemptable, 32 | fast | period 16.667 / 15 / 12.5 ns |
| 23749131_7–10 | mit_preemptable, 32 | fast | fp8_wide / fp8_base_trk / fp8_wide_trk / fp8_spread_trk |
| 23749131_11–12 | mit_preemptable, 32 | fast | hold 0.05/0.02, 0/0 |
| 23749131_13–14 | mit_preemptable, 32 | fast | fp8_wide_trk, density 55, 20 / 15 ns (best-guess alternative) |
| 23749131_15–16 | mit_preemptable, 32 | fast | extra: density 70; period 10 ns |
| 23749816_0–2 | mit_preemptable, 32 | fast | extra2 at density 55: fp8_base_trk 20 ns, fp8_spread_trk 20 ns, fp8_wide_trk 16.667 ns |

Total: 23 runs, 752 CPUs on mit_preemptable, none left on mit_normal.

`manifest.json` holds the exact mapping from array task to run id.

## Variant launch, 2026-09-25 (matrix `var`, `matrices/variants.yaml`)

The cores are the variant workflow's published files in
`$PE_WORK/variants/cores/`, as listed in
`MANIFEST.sha256` (written 01:25). `docs/variants.md` section 4 describes
them. Every core passed `sha256sum -c` before submission, and the matrix pins
each hash:

| core | sha256 |
|---|---|
| `rstreg` | `d7e4c677fdd793e8b2a78d6080f21521a6740490ddc69d84461bfc063b15dec5` |
| `cn` | `0416d60832990e26ad75530fab18444253fc3ef3f634a6a092f7233aeed237a4` |
| `cn_s2` | `24857b088a4d56b9dfeb070db55980b6cfec1844742118282d32ec7b96693b91` |
| `diet4` | `cc27c465c877f0ab3b9491de1ae0fb2bf084e97e001a83e768fb676ac8b6ebba` |
| `diet2` | `f885b0a0e8597840094e66ceb5f103b15ca81f49b23341a62cfe53035d758d39` |

Each variant keeps module `protocol_emulator_core` and the eight SRAM
instances behind the unchanged `src/project.v`. For every one of the 38
snapshots, `check_macro_floorplan.py` passes on that variant's own netlist.

All 38 runs use mit_preemptable, 32 CPUs, 64 GB and `OPENROAD_THREADS` 32
(local only), with hold margins 0.1/0.05. Fast runs have a 10 h limit and full
runs 24 h. The runs were submitted at 01:56 with `--cpu-budget 768` and frozen
harness `0b19f2b1e646`.

At submission the 23 baseline tasks held 752 of the 768 CPUs. So every
variant run waits in an `afterany` chain behind one baseline task, or behind
a variant run that is itself chained:

* the first 20 variant runs start as the 20 baseline fast tasks
  (23749131_0–16, 23749816_0–2) end;
* the next 17 start as those variant runs end;
* `cn_s2` at 15 ns follows the 48-CPU canary 23749132_0.

The best 6x4 guess is diet4 with `fp6_tworow_trk` at density 60. Diet4 is the
"items 1–6" insurance variant of `docs/area-study.md` §8b. `fp6_tworow_trk` is
the §4 edge-row topology of `docs/hardening.md`, the same topology as the 8x4
run that routed, with the FS pins on track (0 predicted DRT-0418). Density 60
is the density that routed at 8x4.

| Job | Tag | Core | Tiles, floorplan | Density | T (ns) | Mode | Waits for |
|---|---|---|---|---:|---:|---|---|
| 23751798_0 | v8-full | rstreg | 8x4 fp8_base | 60 | 20 | full | 23749131_0 |
| 23751800_0 | v8-full | cn | 8x4 fp8_base | 60 | 20 | full | 23749131_1 |
| 23751802_0 | v8-full | cn_s2 | 8x4 fp8_base | 60 | 20 | full | 23749131_2 |
| 23751804_0 | v8-diet-ref | diet4 | 8x4 fp8_base | 60 | 20 | fast | 23749131_3 |
| 23751806_0 | v6-best | diet4 | 6x4 fp6_tworow_trk | 60 | 20 | fast | 23749131_4 |
| 23751808_0 | v6-best | diet4 | 6x4 fp6_tworow_trk | 60 | 15 | fast | 23749131_5 |
| 23751810_0, 23751812_0, 23751815_0 | v6-grid | diet4 | 6x4 fp6_tworow | 55 / 60 / 65 | 20 | fast | 23749131_6 / _7 / _8 |
| 23751816_0, 23751818_0 | v6-grid | diet4 | 6x4 fp6_tworow_trk | 55 / 65 | 20 | fast | 23749131_9 / _10 |
| 23751820_0, 23751822_0, 23751825_0 | v6-grid | diet4 | 6x4 fp6_spread_trk | 55 / 60 / 65 | 20 | fast | 23749131_11 / _12 / _13 |
| 23751827_0, 23751829_0, 23751831_0 | v6-grid | diet4 | 6x4 fp6_block_trk | 55 / 60 / 65 | 20 | fast | 23749131_14 / _15 / _16 |
| 23751833_0, 23751835_0, 23751837_0 | v6-grid | diet4 | 6x4 fp6_columns_trk | 55 / 60 / 65 | 20 | fast | 23749816_0 / _1 / _2 |
| 23751839_0, 23751841_0, 23751843_0 | v6-grid | diet2 | 6x4 fp6_tworow | 55 / 60 / 65 | 20 | fast | 23751804_0 / 23751806_0 / 23751808_0 |
| 23751845_0, 23751847_0, 23751849_0 | v6-grid | diet2 | 6x4 fp6_tworow_trk | 55 / 60 / 65 | 20 | fast | 23751810_0 / 23751812_0 / 23751815_0 |
| 23751851_0, 23751853_0, 23751855_0 | v6-grid | diet2 | 6x4 fp6_spread_trk | 55 / 60 / 65 | 20 | fast | 23751816_0 / 23751818_0 / 23751820_0 |
| 23751857_0, 23751859_0, 23751861_0 | v6-grid | diet2 | 6x4 fp6_block_trk | 55 / 60 / 65 | 20 | fast | 23751822_0 / 23751825_0 / 23751827_0 |
| 23751863_0, 23751865_0, 23751867_0 | v6-grid | diet2 | 6x4 fp6_columns_trk | 55 / 60 / 65 | 20 | fast | 23751829_0 / 23751831_0 / 23751833_0 |
| 23751869_0, 23751871_0, 23751873_0 | v8-15ns | rstreg / cn / cn_s2 | 8x4 fp8_base | 60 | 15 | fast | 23751835_0 / 23751837_0 / 23749132_0 |

Re-running the submission is safe. It skips queued, running and complete runs:

```sh
python3 scripts/sweep/submit.py scripts/sweep/matrices/variants.yaml --cpu-budget 768
```

## Variant follow-ups (matrix `var2`, `matrices/variants-followup.yaml`)

I added these from the first results. They were submitted between 02:58 and
04:40 with `--cpu-budget 768 --exclude node1621,node1918`. Each task is
listed with its job id in `manifest.json` (tag in brackets):

* `[v6-full]` 23763343_0: diet4 `fp6_tworow` d65 20 ns, full flow.
* `[v6-clock]` 23763344_0 and 23763345_0: the same point at 15 and 12.5 ns.
* `[v6-m4obs]` 23768771_0–1 and 23768772_0–23768801_0: diet4 on all five
  `_m4obs` floorplans at d60/d65, and diet2 on all five at d60.
* `[v8-12p5]` 23774540_0–23774543_0: rstreg, cn, cn_s2 and diet4 at 8x4,
  12.5 ns.
* `[v6-diet2-clock]` 23774544_0 and 23774545_0: diet2 `fp6_tworow` d60 at 15
  and 12.5 ns.
* `[v6-topchan*]` 23785361_0–2, 23785362_0 and 23793630_0–23793636_0: diet4
  on `fp6_tworow_trk_top30` and `_top60` at d55/60/65, at 20, 15 and 12.5 ns,
  a full-mode run of each at d60, and diet2 on `top30`.

## Results as of 2026-09-25 04:40

These come from `collect.py`. Everything below is a fast-mode run unless it
says full. `results.csv` and `summary.md` have every column.

**Repeatability.** Four runs of the submission point (8x4 `fp8_base` d60,
20 ns) gave identical results:

* the full-mode runs 23749129_0 (32 threads), 23749537_0 (32 threads) and
  23749132_0 (48 threads);
* the fast-mode run 23749131_0 (32 threads).

All four show utilization 0.5854, GRT overflow 617, 34,515 → 0 DRT
violations in 46 iterations, typ/fast/slow setup WS +2.94/+6.34/−5.09 ns,
and 4,574 hold buffers. The three full runs also show LVS 0, Magic DRC
69,448 and 84 illegal overlaps. These are run2's numbers (4 threads, job
23720702). At this point `OPENROAD_THREADS` 4, 32 and 48 did not change the
result.

The full runs took 1.6–1.9 h, of which Magic DRC was 46–56 min. Peak batch
RSS was 8.5–9.8 GiB, so 64 GB is generous.

**DRT-0418 rule.** The checker's prediction matched the observed count in
all 57 finished runs across 13 floorplans. Predicted 60 gave 60 (fp8_base,
fp6_tworow), predicted 78 gave 78 (fp8_wide), and every `_trk` floorplan
gave 0.

**8x4, base core.**

* All of these are candidates:
  * `fp8_base` at 16.667, 15 and 12.5 ns (23749131_4–6);
  * densities 50, 55 and 65;
  * hold margins 0.05/0.02;
  * `fp8_base_trk`, `fp8_wide_trk` and `fp8_spread_trk` at d55/d60,
    including `fp8_wide_trk` d55 at 15 and 16.667 ns.
* These exit with a LibreLane error, so the action would fail:
  * 10 ns: typ setup WS −1.48 ns (194 violations) and 4 fast-corner hold
    violations (23749131_16);
  * hold margins 0/0: 1 fast-corner hold violation (23749131_12);
  * `fp8_wide` d60: 95 typ setup violations (23749131_7).
* The slow corner, which is reported but not signed off, fails in every
  8x4 base run (−5.09 to −10.88 ns).

**8x4 variants.**

* rstreg full, 20 ns (23751798_0): candidate. LVS 0, Magic DRC 69,452,
  utilization 0.586, typ +1.47 ns, slow −7.52 ns.
* cn_s2 full, 20 ns (23751802_0): candidate. LVS 0, Magic DRC 69,453,
  utilization 0.545, typ +3.30 ns, slow −4.99 ns.
* rstreg at 12.5 ns (23774540_0): candidate.
* cn_s2 at 15 ns (23751873_0): fails, with 24 typ setup violations (WS
  −0.77 ns) and 1 antenna violation.
* diet4 at 8x4, 20 ns (23751804_0): candidate, utilization 0.421.
* cn full, 20 ns (23751800_0) failed. DRT ended with 6 residual Metal4
  shorts inside macro footprints after 60 iterations (GRT overflow 876).
  Then `Netgen.LVS` raised a Python `JSONDecodeError` ("Invalid \escape")
  while it parsed netgen's LVS statistics. This is LibreLane 3.1.0.dev3,
  `steps/netgen.py` line 265, and it happens after a route with shorts.
  So LVS gave no count at all rather than reporting mismatches.

**6x4, diet2.** All 12 finished runs are clean candidates at utilization
0.50. They cover `fp6_tworow`, `fp6_tworow_trk` and `fp6_spread_trk` at
d55/60/65 (9 runs), plus three `_m4obs` twins. Each took 16–28 min.

**6x4, diet4.** Utilization is 0.562. The candidates are:

* `fp6_tworow` d65 (23751815_0, GRT overflow 166), and its `_m4obs` twin;
* `fp6_tworow_trk_top30` d60 (23785361_0: GRT overflow 4, DRT clean after
  26 iterations, typ +3.08 ns).
* `fp6_tworow_trk_top60` d65 (23785362_0: GRT overflow 36, DRT clean after
  7 iterations, typ +1.80 ns; finished 04:45).

`fp6_tworow_trk_top60` d60 (23785361_2: GRT overflow 1, DRT clean after 5
iterations) has 1 antenna violation, so it is not a candidate.

The full flow of `fp6_tworow` d65 at 20 ns (23763343_0, finished 04:44) is a
candidate. LVS 0, Magic DRC 65,613, illegal overlaps 84, and routing, timing
and utilization identical to its fast run. Its final GDS, netlists and SPEF
are kept, gzipped, in `runs/<id>/out/final/`.

Thirteen diet4 6x4 runs ended with 3–25 residual DRT violations, 170 in
total. Every one lies inside a macro footprint (see `drt_residual`). Without
the obstruction they are mostly Metal4 shorts. These runs had GRT overflow
of 535–2,817.

The Metal4 obstruction did not fix this; the shorts moved mostly to Metal2
and Metal3:

* `fp6_tworow_trk_m4obs` d60 (23768772_0): 4 shorts, Metal2 2 and Metal3 2.
  They are on net3110 through `e2_lo`, the net that shorted on Metal4
  without the obstruction (23751806_0).
* `fp6_tworow_m4obs` d60: 16 shorts, 12 of them on Metal3.
* `fp6_spread_trk_m4obs` d60: 13 shorts, 11 of them on Metal3.

In each case GRT overflow equals the unobstructed run's.

Lowering the top row to open a channel above it cut GRT overflow at d60
from 1,085 to 4 (`top30`) and 1 (`top60`). At d65 it was 36 (`top60`),
against 2,817 for `fp6_tworow_trk`. All three top-channel runs finished so
far routed with 0 DRT violations. The runs in flight test this at other
densities and clocks, and in full mode.

diet4 at 6x4 fails typ setup at 15 ns: `fp6_tworow` d65 has 123 violations
(WS −0.48 ns), and `fp6_tworow_trk` d60 has 23.

**Still running or queued at 04:40:** 18 running and 25 queued.
`fp6_block_trk` and `fp6_columns_trk` have no finished run yet, and neither
do the cn runs or most of the top-channel follow-ups. Refresh with
`python3 scripts/sweep/collect.py`.

**Harness bug found and fixed at 04:43.** Array list files were named
`<group>.<time>.list`. Arrays 23793630 (4 fast tasks) and 23793631 (2 full
tasks) belong to the same group and were submitted within one second, so the
second list overwrote the first. What happened:

* 23793630_0 and _1 started the two top-channel full runs, duplicating
  23793631_0 and _1.
* 23793630_2 and _3 read empty lines and failed after 4 s.

I cancelled 23793630_0 and _1 at 04:42. Their `terminated` `result.json`
sits in the two full-run directories until 23793631_0 and _1 finish and
overwrite it. `collect.py` shows those runs as running meanwhile, because the
job ids differ. The four affected runs were resubmitted as 23794722_0–3.

A check of every Slurm log against the manifest found no other job that ran
a run other than its recorded one (82 jobs checked).

The fixes in `submit.py`:

* list names now carry the pid and a content hash, and the file is created
  with `O_EXCL`;
* `--dry-run` no longer writes list files;
* a task that ended less than 5 min ago but has no visible `result.json` is
  skipped, not resubmitted. The pool showed this lag twice, for 23763343_0
  and 23768784_0.
