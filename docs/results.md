# Results and how to reproduce them

This page lists every headline number that the repository states. It was
compiled at commit `c118027` (tag `v0.1-hardened`). On 2026-09-26 and
2026-09-27 (UTC) it was updated with:

- the official results of `131e793`, the 20 ns configuration of record from
  `25e331e` until `d76f1cc` (section 2b);
- the local sign-off of the 15 ns configuration p018 (R84), committed in
  `d76f1cc`; its official run (R85), its timing re-analysed at the 50 MHz
  operating clock (R86) and the same knob values run at 20 ns (R88) are in
  section 2d;
- the 6x4 build (section 2c, R80 to R83, R87 and R89);
- the mutation score of `aa07868` (R23c) and the `diet4` mutation rerun
  (R23d).

A row that a later result replaces is marked **Superseded** and kept, with
its commit and run. Each row gives:

- the claim;
- where the repository states it;
- the evidence, that is, a GitHub Actions run, Slurm job ids, or a result
  file;
- the command that reproduces it, and where that command can run.

Each number was checked against its source when this page was written.
The claims that could not be checked, or that the source no longer supports,
are listed in [section 9](#9-claims-that-are-stale-or-not-fully-substantiated).
What the evidence does not establish is in [limitations.md](limitations.md).
The defects that verification found are in [bug-ledger.md](bug-ledger.md).

**Where a command runs:**

- **CI:** GitHub Actions. Re-run the workflow, or push a commit.
- **Local:** one workstation with the documented toolchain (below).
- **Cluster:** a Slurm cluster. The numbers came from the MIT Engaging
  cluster. Job ids are Slurm ids on that cluster.

`<work dir>` is the cluster work area (`$PE_WORK` in the scripts). Raw
per-seed and per-mutant results stay there and are not in git.

## 1. One-command reproduction

```sh
make reproduce          # scripts/reproduce.sh: the quick tier
make reproduce-full     # scripts/reproduce.sh --full: adds the longer local checks and prints the cluster commands
make reproduce REPRO_ARGS=--parallel     # independent steps run concurrently
scripts/reproduce.sh --list              # step names; --only/--skip select steps
```

The script prints a PASS/FAIL line per step and writes
`build/reproduce/summary.tsv`, with the logs in `build/reproduce/logs/`.
It never writes to `src/`.

| Step | Tier | What it checks | Reproduces |
|---|---|---|---|
| `regen` | quick | `hardcaml/` + `configs/instruction-sram-32.json` regenerates `src/protocol_emulator_core.v` byte for byte (into the work directory) | R10 |
| `lint` | quick | `scripts/lint.sh`: iverilog elaboration, `yosys hierarchy -check` with exactly 8 SRAM macros, `verilator -Wall` | R11 |
| `cocotb` | quick | the cocotb RTL suite (`cd test && make clean && make`); every test must pass | R12 |
| `formal` | quick | 12 of the 16 `formal/` jobs: `reset_safety`, `engine_safety`, `processor_invariants_prove`, `processor_inductive_prove`, `processor_inductive_cover`, `timing_isolation_prove_k0` to `_k3`, `timing_isolation_cover`, and both negative controls | R30 (part) |
| `timing` | quick | `pe_timing report` on `firmware/` equals the committed `tools/timing/report/` byte for byte; the analyzer's unit tests | R40 |
| `host` | quick | `python3 -m unittest discover -s host/tests` | R24 |
| `formal-rest` | full | the other 4 `formal/` jobs: `fifo_conservation`, `processor_invariants_bmc`, `processor_inductive_bmc`, `timing_isolation_bmc` | R30 (rest) |
| `peers` | full | `test_ext/` against the vendored third-party peers, RTL | R22 (fixed stimulus, RTL) |
| `variants` | full | `scripts/gen_variants.sh` and its three byte-identity checks | R26 (generation) |
| `hardcaml` | full | the Hardcaml unit tests (`dune test` in `hardcaml/`) | R26 (Hardcaml tests) |
| `timing-validate` | full | `pe_timing validate` with the scenarios, legacy, event, mutants and margins suites | R41 (base suites) |
| `gl` | full | the gate-level subset of `test/` on a hardened netlist; runs only when `PDK_ROOT` and `GL_NETLIST` are set | R3 |

**Toolchain.**

- OCaml 5.2.1 with the opam pins in `scripts/opam-deps.txt`. Set
  `OCAML_ENV` to a shell file that puts `dune` on `PATH` if it is not
  already there.
- OSS CAD Suite 2026-07-29: yosys 0.67, sby, yices, bitwuzla and verilator.
- Icarus Verilog 13.0, the build that CI uses, or 14.
- Python 3.10 or later with `test/requirements.txt` (cocotb 2.0.1).

**Measured on a fresh export.** The tiers were run on a fresh `git archive`
export of `c118027`, with the reproduction files of this change
(`scripts/reproduce.sh` and the `Makefile` targets) copied in, as Slurm jobs:

| Job | Tier | Toolchain | CPUs | Result | Wall time |
|---|---|---|---:|---|---:|
| 23974810 | quick, sequential | Icarus 13.0 (the CI build), cocotb 2.0.1, Python 3.12, OSS CAD Suite 2026-07-29, OCaml 5.2.1 | 2 | PASS, all 6 steps | 319 s |
| 23975260 | quick, sequential, `DUNE_CACHE=disabled` (no reuse of earlier OCaml builds) | as 23974810 | 2 | PASS, all 6 steps | 329 s |
| 23974811 | full, `--parallel`, with `PDK_ROOT` (IHP-Open-PDK `2bbec755`, the action's revision) and `GL_NETLIST` (the `tt_submission` netlist of run 36144357821) | Icarus 14, otherwise as above | 8 | PASS, all 12 steps | 1,111 s |

In 23974811 the `gl` step reproduced the official `gl_test` result (R3) on
the CI netlist: 66 tests, 36 pass, 30 skip, 0 fail. With `--parallel`, the
formal lane (`formal`, then `formal-rest`) sets the wall time; its longest
job is `timing_isolation_bmc`. The quick jobs ran an earlier revision of
`scripts/reproduce.sh` that differs from the one in this change only in the
text printed by `--print-cluster`. The job logs and summaries are in
`<work dir>/repro/` (`manifest.json`).

Per-step wall times in seconds:

| Step | 23974810 | 23975260 | 23974811 |
|---|---:|---:|---:|
| regen | 4 | 4 | 4 |
| lint | 1 | 1 | 1 |
| cocotb (66 tests) | 81 | 81 | 98 |
| formal (12 jobs) | 223 | 233 | 269 |
| timing | 1 | 2 | 2 |
| host (69 tests, 4 skipped without MicroPython) | 8 | 8 | 10 |
| formal-rest (4 jobs) | | | 837 |
| peers (65 tests) | | | 78 |
| variants (6 cores) | | | 5 |
| hardcaml (7 test executables) | | | 28 |
| timing-validate (608 engine runs) | | | 56 |
| gl: 66 tests, 36 pass, 30 skip | | | 87 |

## 2. Official Tiny Tapeout results (`v0.1-hardened`, commit `c118027`)

These were the results of record until `25e331e` changed
`src/config.json`; section 2b gives the 20 ns results that followed, and
section 2d the current 15 ns configuration. Run
[36144357821](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36144357821)
is the `gds` workflow on push of `c118027`, started 2026-09-25 13:58 UTC.
The metrics are from `tt_submission/stats/metrics.csv` in that run's
`tt_submission` artifact, whose `commit_id.json` names the same commit and
run. The design is 8x4 tiles at a 20 ns clock (50 MHz), with IHP-Open-PDK
`2bbec755` and LibreLane 3.1.0.dev3.

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R1 | gds PASS | README.md (Status); tag `v0.1-hardened` message | Run 36144357821, job `gds`: success, 1 h 53 min | CI (re-run `gds`). Local mirror of the action: [hardening.md](hardening.md) section 7 and [sweep.md](sweep.md) (cluster) |
| R2 | precheck PASS | README.md (Status) | Run 36144357821, job `precheck`: "Precheck passed". KLayout SG13CMOS5L DRC ran for 11,001 s; the zero-area, top-macro-name, forbidden-layer, prBoundary and pin checks also passed | CI. Local: the precheck reproduction in [drc-triage.md](drc-triage.md) section 9 (cluster) |
| R3 | **Superseded by R15** (`131e793`, 102-test suite). gl_test PASS: 66 tests, 36 pass, 30 skip, 0 fail | README.md (Status); [test/README.md](../test/README.md) (skips by design) | Run 36144357821, job `gl_test`: `TESTS=66 PASS=36 FAIL=0 SKIP=30` | CI. Local: `scripts/reproduce.sh --only gl` with `PDK_ROOT` and `GL_NETLIST` set to the artifact netlist |
| R4 | **Superseded by R18** (`131e793`: 61.92%). Utilization 58.5% (58.53%; standard cells alone 53.88%) | README.md; tag message | `design__instance__utilization` 0.585345, `__stdcell` 0.538803 | CI or local mirror |
| R5 | Route DRC 0, LVS 0, antenna 0 | README.md; tag message | `route__drc_errors` 0 (the last detailed-routing iteration, 25, ended at 0); every `design__lvs_*` counter 0; `route__antenna_violation__count` 0, with 84 antenna diodes inserted | CI or local mirror |
| R6 | **Superseded by R16** (`131e793`: typ +7.88, fast +9.31 ns). Setup met at the typical corner at 50 MHz, WS +0.89 ns; at the fast corner WS +6.15 ns | README.md; tag message | `timing__setup__ws` typ 0.8854, fast 6.1533; 0 setup violations at typ and fast | CI or local mirror |
| R7 | **Superseded by R16** (`131e793`: slow +2.95 ns, 0 violations). Slow corner (not Tiny Tapeout sign-off) misses setup, WS −8.52 ns | README.md; tag message | slow `timing__setup__ws` −8.5171; 2,482 violating endpoints; register-to-register WS −0.116 ns (1 violation). The local mirror with the same configuration (job 23850490) gave identical values for the typical and slow setup slack, the register-to-register slack and the utilization. Its report puts 2,467 of the violations on `rst_n` to a register, 14 on `rst_n` to an output, and the one register-to-register path from `instruction_sram_e2_hi` `A_DOUT[1]` | CI or local mirror |
| R8 | **Superseded by R17** (`131e793`). Hold met at every corner | tag message | hold WS: fast +0.107 ns, typ +0.297 ns, slow +0.629 ns; 0 hold violations | CI or local mirror |
| R9 | 84 Magic illegal overlaps, waived by configuration; Magic DRC not run. `131e793` has 86 (R18); the waiver and `RUN_MAGIC_DRC` false are unchanged | [drc-triage.md](drc-triage.md) sections 4 and 7 | `magic__illegal_overlap__count` 84; `RUN_MAGIC_DRC` false in `src/config.json` since `1e2cfb3` | CI or local mirror |

The same run's `viewer` job failed. It could not create a GitHub Pages
deployment (HTTP 404), because Pages is not enabled for the repository.
That job publishes a preview; it does not check the design. It is why the
`gds` badge shows the workflow as failed.

The push of the tag started a second run on the same commit,
[36188299524](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36188299524).
Its `gds`, `precheck` ("Precheck passed") and `gl_test` (`TESTS=66
PASS=36 FAIL=0 SKIP=30`) jobs also passed; its `viewer` job failed in the
same way.

**Other workflows on `c118027`** (all on push, 2026-09-25):

| # | Claim | Evidence | Reproduce |
|---|---|---|---|
| R10 | The committed core is current with `hardcaml/` | `regen` run [36144357930](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36144357930): success | Local: `reproduce.sh --only regen`, or `make check-generated` (which rewrites `src/` in place) |
| R11 | Lint is clean: 0 errors, 8 SRAM macros | `test` run [36144357839](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36144357839), job `lint`: iverilog OK; yosys OK with 8 macros; `verilator: rc=0 warnings=73 errors=0` (60 COMBDLY, 12 UNUSEDSIGNAL, 1 DECLFILENAME) | Local: `reproduce.sh --only lint` |
| R12 | **Superseded** by the 102-test suite of `aa07868`, which passes on `131e793` (section 2b). cocotb RTL suite: 66 tests pass | Same run, job `test`: `TESTS=66 PASS=66 FAIL=0 SKIP=0` (Icarus 13.0) | Local: `reproduce.sh --only cocotb` |
| R13 | All 16 formal jobs meet their expectation in CI | `formal` run [36144357811](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36144357811): the generation job and all 16 matrix jobs succeeded. The longest was `timing_isolation_bmc`, at 8 min 20 s | Local: `reproduce.sh --only formal,formal-rest` |
| R14 | Datasheet builds | `docs` run [36144357954](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36144357954): success | CI |

The runs on the tag push (`test` 36188299625, `formal` 36188299526, `regen`
36188299483, `docs` 36188299516) also succeeded.

## 2b. Official Tiny Tapeout results of `131e793` (20 ns configuration of record from `25e331e` until `d76f1cc`)

These were the results of record until `d76f1cc` set `CLOCK_PERIOD` 15 in
`src/config.json` (section 2d). They remain true for `131e793`; `src/` and
`info.yaml` are unchanged from `131e793` to `1e5b1d8`. Run
[36257636798](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36257636798)
is the `gds` workflow on push of `131e793`, started 2026-09-26 17:03 UTC. The
metrics are from `tt_submission/stats/metrics.csv` of that run's
`tt_submission` artifact, whose `commit_id.json` names the same commit and
run. Tiles, clock, PDK and LibreLane are as in section 2.

**What changed since `c118027`.** `git diff --stat c118027 131e793 -- src/`
lists only `src/config.json`; `src/project.v`,
`src/protocol_emulator_core.v` and `src/sram_pdn_cfg.tcl` are byte-identical.
`25e331e` set the LibreLane keys of optimizer promotion p010
([optimization.md](optimization.md)): `PL_TARGET_DENSITY_PCT` 60 → 61,
`PL_RESIZER_HOLD_SLACK_MARGIN` 0.1 → 0.15, the `fp8_spread_trk` macro
placement with `FP_MACRO_HORIZONTAL_HALO` 16.48 → 20, and new keys
`PL_TIMING_DRIVEN` true, `PL_RESIZER_SETUP_SLACK_MARGIN` 5.75,
`SYNTH_STRATEGY` "DELAY 4", `MAX_FANOUT_CONSTRAINT` 8,
`DESIGN_REPAIR_MAX_WIRE_LENGTH` 300, `DESIGN_REPAIR_MAX_SLEW_PCT` 35,
`DESIGN_REPAIR_MAX_CAP_PCT` 50, `CTS_MAX_CAP` 0.2, `CTS_OBSTRUCTION_AWARE`
true, `CTS_SINK_CLUSTERING_SIZE` 16 and `GRT_LAYER_ADJUSTMENTS`
[0, 0.2, 0, 0.1, 0].

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R15 | gds, precheck and gl_test PASS on `131e793`; gl_test: 102 tests, 46 pass, 56 skip, 0 fail | README.md (Status) | Run 36257636798: job `gds` success, 1 h 51 min; job `precheck` "Precheck passed" (KLayout SG13CMOS5L DRC 10,501 s; the zero-area, top-macro-name, forbidden-layer, prBoundary and pin checks also ran); job `gl_test` `TESTS=102 PASS=46 FAIL=0 SKIP=56`. Job `viewer` failed with "Creating Pages deployment failed" (HTTP 404), as in run 36144357821 | CI |
| R16 | **20 ns result of `131e793`**, superseded as the current configuration by `d76f1cc` (section 2d) and still true for that commit. Setup met at all three corners at 50 MHz: WS typ +7.88, fast +9.31, slow +2.95 ns, with 0 setup-violating endpoints at every corner. The gain over `c118027` (R6, R7) is +6.99, +3.15 and +11.47 ns, from configuration only | README.md (Status); [timing-closure.md](timing-closure.md) section 9; [limitations.md](limitations.md) section 2; [info.md](info.md), "Limitations" | `timing__setup__ws` typ 7.8797, fast 9.3075, slow 2.9539; `timing__setup_vio__count` 0 at every corner; slow `timing__setup__tns` 0 (was −9,744.1); slow register-to-register WS +2.954 ns, 0 violations (was −0.116 ns, 1 violation). Arithmetic checked with AXLE (Lean 4, `<work dir>/docs-timing/axle/`) | CI. Local: the promoted full run of p010 (R19) |
| R17 | **20 ns result of `131e793`**, superseded as the current configuration by `d76f1cc` (section 2d) and still true for that commit. Hold met at every corner: fast +0.112, typ +0.318, slow +0.657 ns; 0 hold violations | README.md (Status); limitations.md section 2 | `timing__hold__ws` per corner; `timing__hold_vio__count` 0 at every corner; 137 hold buffers (was 10) | CI |
| R18 | **20 ns result of `131e793`**, superseded as the current configuration by `d76f1cc` (section 2d) and still true for that commit. Utilization 61.9% (61.92%; standard cells 57.64%); route DRC 0; LVS 0; antenna 0; 94,248 instances; max-slew / max-cap / max-fan-out violations typ 1/0/3, fast 0/0/3, slow 4/0/3 (were 42/70/524, 13/73/524 and 290/70/524); 86 Magic illegal overlaps, waived as in R9 | README.md (Status); limitations.md sections 2 and 5 | `design__instance__utilization` 0.619173, `__stdcell` 0.576428; `route__drc_errors` 0 (the last detailed-routing iteration, 17, ended at 0); every `design__lvs_*` counter 0; `route__antenna_violation__count` 0, with 25 antenna diodes; `design__instance__count` 94,248 (41,005 standard cells); `design__max_{slew,cap,fanout}_violation__count__corner:*`; `magic__illegal_overlap__count` 86 | CI |
| R19 | **20 ns result of `131e793`**, superseded as the current configuration by `d76f1cc` (section 2d) and still true for that commit. The local prediction matched: the promoted full run of p010 produced a `metrics.csv` byte-identical to that of run 36257636798 and the same gate-level netlist; its fast-mode trial gave the same setup and hold slack at all three corners, utilization, instance count and slew/cap/fan-out counts | [optimization.md](optimization.md), "Starting point" | Trial `dor-26a873-c8bf57e#94`, job 24009268 (fast mode, 32 threads); promotion p010: full run job 24010051 (`OPENROAD_THREADS` 4, LVS 0), precheck job 24011934 (9/9), gate-level job 24011935 (102 tests, 46 pass, 56 skip, 0 fail). `cmp` of the full run's `out/metrics.csv` with the artifact's: identical; netlist sha256 `8ce27ccc…` for both the artifact's `tt_um_teslacoilerow_protocol_emulator.v` and the run's `final/nl`. The trial was compared field by field from `<work dir>/optimizer/store/events.jsonl`. Record: `<work dir>/docs-timing/artifact_comparisons.out` | Cluster: [optimization.md](optimization.md), "Operation" |
| R84 | Optimizer promotion p018 passes the local sign-off at 15 ns (66.7 MHz): full run legal with LVS 0 and route DRC 0; setup WS typ +5.95, fast +6.92, slow +2.24 ns at 15 ns with 0 violations; hold WS min +0.165 ns (fast); utilization 65.30%; precheck 9/9; gate level 102 tests, 46 pass, 56 skip, 0 fail. It differed from the `src/config.json` of `131e793` in four keys: `CLOCK_PERIOD` 15, `DESIGN_REPAIR_MAX_CAP_PCT` 45, `GRT_LAYER_ADJUSTMENTS` [0, 0.2, 0.2, 0.1, 0], `PL_RESIZER_SETUP_SLACK_MARGIN` 5.8. **Committed since `d76f1cc`**, which sets exactly these four keys; `info.yaml` `clock_hz` stays 50000000 (the operating clock). The official run of `d76f1cc` is R85. (Until `d76f1cc` this row read: "Not committed and not built by CI; the design of record stays 20 ns / 50 MHz".) | README.md (Status); [optimization.md](optimization.md), "Current results" and "Adoption of p018"; [limitations.md](limitations.md) section 2; [info.md](info.md), "Limitations"; [timing-closure.md](timing-closure.md) section 10 | Trial `dor15-26a873-8x4-p15-f174dc0#1`, job 24024343 (fast mode, same setup WS); full run job 24042265 (`OPENROAD_THREADS` 4; `out/metrics.csv`: `timing__setup__ws__corner:*` 5.9499 / 6.9240 / 2.2400, `timing__hold__ws` 0.1650, `design__instance__utilization` 0.653002, `route__drc_errors` 0, `design__lvs_net_difference__count` 0); precheck job 24053971 (9/9; its submission-like directory carries the snapshot's `info.yaml` with `clock_hz` 66666667, which the precheck does not read); gate-level job 24053972. The run's `params.json` `config_changes_vs_repo` lists exactly the four keys (the placement, `MACROS`, is equal and so not listed); `git show d76f1cc -- src/config.json` changes the same four keys | Cluster: [optimization.md](optimization.md), "Operation" |

**Other workflows on `131e793`** (all on push, 2026-09-26): `test`
[36257636760](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36257636760)
(job `lint` success; job `test` `TESTS=102 PASS=102 FAIL=0 SKIP=0`),
`formal` 36257636810 (the generation job and all 16 matrix jobs succeeded),
`regen` 36257636763 and `docs` 36257636924 all succeeded.

**Later commits.** `f0e9c19`, `4bd30c8`, `8a05de7`, `24b807b`, `fdc23f2`,
`d733cef` and `1e5b1d8` do not change `src/`.

- **`4bd30c8`.** `test` 36274474552, `regen` 36274474544, `docs`
  36274474541 and `formal` 36274474545 (all 17 jobs) succeeded. In `gds`
  36274474548 the jobs `gds`, `gl_test` (`TESTS=102 PASS=46 FAIL=0
  SKIP=56`) and `precheck` ("Precheck passed", finished 2026-09-27 01:57
  UTC) succeeded; `viewer` failed as in every run. The first version of
  this paragraph, written at about 2026-09-27 01:26 UTC for `1e5b1d8`,
  recorded the precheck as still running and gave the time as 01:40 UTC.
- **`1e5b1d8`** (pushed 2026-09-27 01:26 UTC). `test` 36285537637, `formal`
  36285537631 (all 17 jobs), `regen` 36285537627 and `docs` 36285537639
  succeeded. In `gds` 36285537636 the jobs `gds` (1 h 49 min) and `gl_test`
  (`TESTS=102 PASS=46 FAIL=0 SKIP=56`) succeeded and `viewer` failed; its
  `precheck` passed ("Precheck passed", finished 2026-09-27 06:41 UTC).
  `gds_6x4` 36285537630 passed all five jobs (R87).
- **`d76f1cc`** (pushed 2026-09-27 05:56 UTC) sets the 15 ns configuration
  in `src/config.json` (section 2d). `test` 36298635420 (job `lint` success;
  job `test` `TESTS=102 PASS=102 FAIL=0 SKIP=0`), `formal` 36298635454 (all
  17 jobs), `regen` 36298635381 and `docs` 36298635413 succeeded. `gds`
  36298635436 (R85) was in progress at 2026-09-27 08:40 UTC (its `gds` job, in the
  "Build GDS" step since 05:56 UTC). In `gds_6x4` 36298635404 the jobs
  `core_current`, `rtl_test`, `gds` and `gl_test` (`TESTS=102 PASS=46
  FAIL=0 SKIP=56`) and `precheck` ("Precheck passed") succeeded, with
  metrics byte-identical to the earlier 6x4 runs (section 2c).
- **`f511c97`** changes only `tools/opt/driver.py` and `tools/opt/tracks.py`
  (the optimizer's track weights; [optimization.md](optimization.md),
  "Allocation"). It had no CI run of its own when this was written.

## 2c. The 6x4 fallback build (`.github/workflows/gds_6x4.yaml`)

The workflow builds the `diet4` core on 6x4 tiles with
`variants6x4/config.overlay.json` merged into `src/config.json`
([6x4.md](6x4.md)). It has its own `tt_submission` artifact. The slow corner
is reported and not signed off, as for 8x4.

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R80 | **Superseded by R82 and R83** (configuration of `4bd30c8`). The pre-adoption 6x4 configuration (6x4.md section 4) passed `gds`, `precheck` and `gl_test` in CI with its local sign-off numbers: setup WS typ +4.30, fast +8.50, slow −2.80 ns (467 violating endpoints at slow); hold WS min +0.099 ns; utilization 56.28% (standard cells 49.45%); route DRC 0; LVS 0 | [6x4.md](6x4.md) section 5b | Runs 36225500529 (`be7dbda`) and 36238342669 (`39d21e8`): all five jobs succeeded; `gl_test` `TESTS=102 PASS=46 FAIL=0 SKIP=56` and "Precheck passed" in both. The two artifacts' `metrics.csv` files are byte-identical, and equal to that of the local sign-off run 23980402_9 (6x4.md section 3) | CI |
| R81 | `gds_6x4` failed on `131e793`, because the overlay set placement, obstruction, density and halo but no timing key, and so inherited the p010 timing keys of `src/config.json` | 6x4.md section 5b; [optimization.md](optimization.md), "Starting point" | Run 36257636751, job `gds` (4 h 10 min): `Checker.TrDRC` "69 Routing DRC errors found" (deferred), `Checker.IllegalOverlap` 73 (warning), Netgen LVS "Top level cell failed pin matching" (28,200 against 28,212 nets), then LibreLane stopped on a `JSONDecodeError` ("Invalid \escape") reading Netgen's output: "harden failed". `precheck` and `gl_test` were skipped; `core_current` and `rtl_test` (`TESTS=102 PASS=102`) passed. The same configuration locally: trial `diet4_6x4` #0 (job 24024332, fast mode) route DRC 69; control promotion p012, full run 24029191, route DRC 69 and stopped in `Netgen.LVS` | CI; cluster |
| R82 | Optimizer promotion p014 (trial `diet4_6x4` #8) passes the local sign-off: full run legal with LVS 0, route DRC 0 and antenna 0; setup WS typ +7.71, fast +10.52, slow +2.74 ns; hold WS min +0.051 ns (fast); utilization 58.90%; precheck 9/9; gate level 102 tests, 46 pass, 56 skip, 0 fail. `4bd30c8` puts it into the overlay, and the merged configuration equals the run's key for key | 6x4.md section 4b; commit message of `4bd30c8` | Trial job 24032431 (fast mode, same setup WS); full run job 24036160; precheck job 24041156 (`promotions/p014-diet4_6x4/precheck/result.json`: 9 checks, 0 fail); gate-level job 24041157 (`gl/result.json`, `PE_VARIANT` diet4). Key comparison: `<work dir>/docs-timing/check_overlay_equals_p014.py`, 0 differences over 47 keys (`OPENROAD_THREADS` excluded) | Cluster |
| R83 | The official `gds_6x4` run of `4bd30c8` passed `gds`, `precheck` and `gl_test` with the p014 configuration (R82): setup WS typ +7.71, fast +10.52, slow +2.74 ns, 0 setup and 0 hold violations; hold WS min +0.051 ns (fast); utilization 58.90% (standard cells 52.48%); route DRC 0; LVS 0; antenna 0. The artifact's `metrics.csv` is byte-identical to that of the local full run 24036160 | [6x4.md](6x4.md) sections 4b and 5b; README.md ("Tile size"); [limitations.md](limitations.md) section 5 | Run [36274474540](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36274474540): all five jobs succeeded; `gl_test` `TESTS=102 PASS=46 FAIL=0 SKIP=56`; "Precheck passed" (KLayout DRC 5,919 s). `tt_submission` artifact `stats/metrics.csv` (`timing__setup__ws` 2.7368, per corner typ 7.7060, fast 10.5185, slow 2.7368; `timing__hold__ws` 0.0513; `design__instance__utilization` 0.58896; `route__drc_errors` 0; `design__lvs_net_difference__count` 0; `antenna__violating__nets` 0); `cmp` with the full run's `metrics.csv` reports no difference | CI |
| R87 | The `gds_6x4` run of `1e5b1d8`, after `8a05de7` and `fdc23f2` made the overlay state every flow knob key and `clock_hz`, passed all five jobs, and its `metrics.csv` is byte-identical to that of run 36274474540 (`4bd30c8`, R83) | [6x4.md](6x4.md) section 5b; README.md ("Tile size") | Run [36285537630](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36285537630): `core_current`, `rtl_test` (`TESTS=102 PASS=102 FAIL=0 SKIP=0`), `gds`, `precheck` ("Precheck passed") and `gl_test` (`TESTS=102 PASS=46 FAIL=0 SKIP=56`) succeeded. The `tt_submission` artifact's `commit_id.json` names `1e5b1d8` and the run; `cmp` of its `stats/metrics.csv` with that of run 36274474540 reports no difference (artifact in `<work dir>/ci-artifacts/36285537630/`) | CI |
| R89 | The `gds_6x4` run of `d76f1cc` passed all five jobs, with a `metrics.csv` and gate-level netlist byte-identical to those of runs 36285537630 and 36274474540: the 15 ns `CLOCK_PERIOD` of `d76f1cc` does not reach the 6x4 build, which stays signed off at 20 ns | README.md ("Tile size"); [6x4.md](6x4.md) section 5b; [limitations.md](limitations.md) sections 2 and 5 | Run [36298635404](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36298635404): `core_current`, `rtl_test`, `gds`, `gl_test` (`TESTS=102 PASS=46 FAIL=0 SKIP=56`) and `precheck` ("Precheck passed", 08:37 UTC) succeeded. `commit_id.json` names `d76f1cc`; `cmp` of `stats/metrics.csv` with runs 36285537630 and 36274474540 and of the netlist with both: no difference (artifact in `<work dir>/ci-artifacts/36298635404/`). `switch.py apply` on exports of `1e5b1d8` and `d76f1cc`: byte-identical `src/` and `info.yaml` (`<work dir>/docs-15ns/check_6x4_merged.out`) | CI |

The official `gds_6x4` run of `4bd30c8`
([36274474540](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36274474540))
confirmed R82 with byte-identical metrics (R83). `8a05de7` then made the
overlay state every flow knob key without changing the merged
configuration (section 9, item 13).

The run of `1e5b1d8` confirmed that with byte-identical metrics (R87).
`d76f1cc` changes `CLOCK_PERIOD` in `src/config.json` to 15, but the overlay
pins `CLOCK_PERIOD` 20 and every other knob key, and
`variants6x4/info.overlay.json` pins `clock_hz` 50000000. `switch.py apply`
on exports of `1e5b1d8` and `d76f1cc` writes a byte-identical
`src/config.json`, `info.yaml` and `src/` (sha256 of each file in
`<work dir>/docs-15ns/check_6x4_merged.out`), so the 6x4 build is
unchanged and is signed off at 20 ns (50 MHz) only. In its `gds_6x4` run
on `d76f1cc`, 36298635404, `core_current`, `rtl_test` and `gds` passed; the
`tt_submission` `commit_id.json` names `d76f1cc`, and its
`stats/metrics.csv` and gate-level netlist are byte-identical to those of
runs 36285537630 and 36274474540 (`cmp`; artifact in
`<work dir>/ci-artifacts/36298635404/`). Its `gl_test` (`TESTS=102 PASS=46
FAIL=0 SKIP=56`) and `precheck` ("Precheck passed", finished 2026-09-27
08:37 UTC) passed, so the run passed all five jobs.

## 2d. The 15 ns sign-off of `d76f1cc` (current configuration)

`d76f1cc` puts optimizer promotion p018 (R84) into `src/config.json`:
`CLOCK_PERIOD` 15 (66.7 MHz), `DESIGN_REPAIR_MAX_CAP_PCT` 45,
`GRT_LAYER_ADJUSTMENTS` [0, 0.2, 0.2, 0.1, 0] and
`PL_RESIZER_SETUP_SLACK_MARGIN` 5.8; every other key is p010's
([timing-closure.md](timing-closure.md) section 10). `info.yaml` `clock_hz`
stays 50000000: the operating clock is 50 MHz, which the firmware, the
host library, `pe_timing` and the datasheet assume. The RTL is
byte-identical to `c118027`. Tiles, PDK and LibreLane are as in section 2.

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R85 | Official `gds`, `precheck` and `gl_test` of `d76f1cc`, the first official build at 15 ns. **In progress**: no official 15 ns result exists yet, and the local sign-off (R84) is the evidence until it does | README.md (Status); [timing-closure.md](timing-closure.md) section 10.2 | Run [36298635436](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36298635436), the `gds` workflow on push of `d76f1cc`, started 2026-09-27 05:56 UTC: job `gds` still in its "Build GDS" step at 08:40 UTC; `precheck` and `gl_test` wait for it. (p018's local full run took 7,833 s of flow time against 2,785 s for p010, whose official `gds` job took 1 h 51 min.) | CI |
| R86 | At the 50 MHz operating clock (20 ns), the p018 layout has setup WS typ +8.950, fast +9.924, slow +6.790 ns and hold WS typ +0.372, fast +0.165, slow +0.743 ns, with 0 setup and 0 hold violations. In the same analysis the 20 ns layout of `131e793` (p010) has +7.880 / +9.307 / +2.954 ns and +0.318 / +0.112 / +0.657 ns. From 15 to 20 ns the worst slack of each path class moves by +5 ns (register to register), +4 ns (input to register, register to output) and +3 ns (input to output); hold by 0, +1, +1 and +2 ns. This is a re-analysis with the flow's STA script, not a result of the official flow, which times the design at 15 ns | README.md (Status); [info.md](info.md), "Limitations"; [limitations.md](limitations.md) section 2; [timing-closure.md](timing-closure.md) sections 10.3 to 10.5 | Slurm job 24077956 (array 0 to 2): OpenSTA 2.7.0 in the `librelane-3.1.0.dev3` image, LibreLane's `corner.tcl` and `base.sdc` unmodified, the step's environment with only `CLOCK_PERIOD` and the netlist changed. Control (a): p018 at 15 ns equals the full run's `metrics.csv` (10 timing metrics at 3 corners, maximum absolute difference 0). Control (c): the `tt_submission` netlist and SPEF of run 36257636798 at 20 ns equal that run's `stats/metrics.csv` (maximum absolute difference 0). Per-class worst paths from `scripts/pe_extra.tcl`, the same start and end point at both periods. `<work dir>/sta50/` (`README.md`, `results.json`, `results.md`, `manifest.json`); 84 theorems checked with AXLE (`<work dir>/sta50/axle/`, `okay: true`) | Cluster: `<work dir>/sta50/README.md` ("Reproduce"), about one minute per corner run on 3 CPUs |
| R88 | With the same knob values, the flow run at 20 ns gives less margin at 50 MHz than the flow run at 15 ns: control promotion p027 (p018's knob set with `CLOCK_PERIOD` 20) has setup WS typ +7.864, fast +9.389, slow +2.998 ns, and the p018 layout re-timed at 20 ns (R86) has +1.085, +0.535 and +3.792 ns more. One pair of runs | [timing-closure.md](timing-closure.md) section 10.6; README.md (Status) | p027: full run job 24077610 (`OPENROAD_THREADS` 4; legal, LVS 0; hold WS +0.362 / +0.154 / +0.724 ns; utilization 0.6196), precheck job 24081008 (9/9), gate-level job 24081009 (102 tests, 46 pass, 56 skip, 0 fail); verdict PASS in the leaderboard of 2026-09-27 08:36 UTC. It is the committed configuration of the optimizer's `dor` track since tree `f511c97`, imported trial `#114` (= v1 `dor-26a873-c8bf57e#118`, fast mode, job 24014107). Arithmetic checked with AXLE (`<work dir>/docs-15ns/axle/`) | Cluster: [optimization.md](optimization.md), "Promotion" |

Until R85 finishes, README.md and [info.md](info.md) quote the local
numbers of R84 and label them local.

## 3. Simulation-based verification

The random and mutation campaigns ran on a frozen snapshot of `73536f0`.
`c118027` has the same `src/` core and `firmware/` as that commit; it changes
the tests (`test/`) and the hardening configuration.

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R20 | Random lockstep campaign: 8,704 seed runs, 536,064 cases, 3,207,184,824 lockstep cycles, 0 failures. RTL: 525,312 cases, 3,159,347,713 cycles. Gate level: 10,752 cases, 47,837,111 cycles | [verification-campaign.md](verification-campaign.md), "Campaigns and totals" | Recomputed for this page from [`campaigns/random/results/campaigns.json`](../campaigns/random/results/campaigns.json) by summing the 15 campaigns; the totals match. Arrays 23746017, 23750843, 23746365–23746369, 23747218, 23750844–23750846, 23752081, 23746061, 23752532, 23752533. About 269 simulator CPU-hours | Cluster: [campaigns/random/README.md](../campaigns/random/README.md). One seed of a `default` campaign, locally and exactly: `cd test && PE_SEED=<seed> make COCOTB_TEST_MODULES=test_random` in a checkout of `73536f0`. At `c118027`, `PE_RANDOM_GEN=1` draws the same cases, except that a trailing `deselect` now runs (BL-8) |
| R21 | The checker catches an injected model bug in 64 of 64 seeds (1,536 of 4,096 cases), and the minimizer shrinks the case to one host operation | verification-campaign.md, "Negative control" | `campaigns.json` `negative_control`; jobs 23749927 and 23749932 | Local: `cd test && PE_INJECT_MODEL_BUG=xor PE_SEED=1 make COCOTB_TEST_MODULES=test_random` must fail (at `c118027` add `PE_RANDOM_GEN=1` for the campaign's cases) |
| R22 | Independent third-party peers: 65/65 tests pass at RTL, at gate level and on Icarus 13.0; seeded campaign 33,280 RTL and 16,640 gate-level test runs, all pass; all 12 wrong-mode SPI substitutions detected | [independent-peers.md](independent-peers.md), "Results" | [`test_ext/results/summary.json`](../test_ext/results/summary.json); job 23792571 (fixed stimulus), arrays 23792569 (RTL) and 23792570 (gate level), job 23791858 (wrong-mode matrix). Design: `73536f0` | Local: `reproduce.sh --only peers` (RTL, fixed stimulus); `cd test_ext && PE_EXT_SEED=<n> make` per seed. Cluster for the campaign |
| R23 | Mutation score 80.2% (1,847 / (2,420 − 116)); 85.8% with deep random and 12 directed tests | verification-campaign.md, "Mutation testing" | [`campaigns/mutation/results/summary.json`](../campaigns/mutation/results/summary.json) (`killed` 1,847, `proven_equivalent` 116); the job list in that section | Cluster: [campaigns/mutation/README.md](../campaigns/mutation/README.md) ("Reproduce"), about 109 CPU-hours |
| R23b | **Superseded by R23c** (96.82%). Mutation score 89.4% (2,060 / 2,304) after gap closure; 85.8% (1,976) without `test_timewarp` | verification-campaign.md, "Gap closure"; commit message of `c118027` | `<work dir>/test-gaps/summary_gaps.json` (`killed` 2,060, `score` 0.8941, `killed_without_timewarp` 1,976); jobs 23813966, 23813967, 23817415, 23819701. **Not in git** (section 9) | Cluster |
| R23c | Mutation score 96.82% (2,223 / (2,420 − 124)) with the 102-test suite of `aa07868`. 124 mutants are proven equivalent (116 by the campaign method, 6 by an ABC gold/mutant miter, 2 by `equiv_induct` with invariants and an SRAM model); the 73 other survivors are argued unobservable but not proven, and count as survivors. A later miter run with a longer ABC time cap proved 26 of the 73 equivalent: 97.93% (2,223 / (2,420 − 150)), 47 survivors ([mutation-push.md](mutation-push.md) section 4.5; `summary-longcap.json`). 96.82% stays the score as run | [mutation-push.md](mutation-push.md), "Result"; commit message of `aa07868` | The committed suite of `c118027` re-run on all 2,420 mutants in one stage: 2,060 killed (array 23974560, jobs 23976875 and 23979515). The `test_kill_*` modules then killed 158 (final stage, job 23984831), 3 (k6, jobs 23987440 and 23987441) and 2 (k7, jobs 23988083 and 23988084) more: 2,223. Formal: job 23977941 (6 proven) and job 23986701 (2 proven). Per-mutant status: [`campaigns/mutation/results/push-4bd30c8/summary.json`](../campaigns/mutation/results/push-4bd30c8/summary.json) and `mutant_status.tsv` (section 9, item 11); raw logs in `<work dir>/mutation97/`. Arithmetic checked with AXLE (`<work dir>/docs-timing/axle/`) | Cluster: mutation-push.md section 6 |
| R23d | Variant `diet4` (the 6x4 build's core) under the 102-test suite: mutation score 95.24% (2,199 / (2,420 − 111)); 90.87% (2,199 / 2,420) without removing proven-equivalent mutants; 110 survivors, not argued one by one. With the longer ABC time cap: 96.66% (2,199 / (2,420 − 145)), 76 survivors (`summary-longcap.json`). The earlier `diet4` figure, 87.7% (2,028 / (2,420 − 107)) with the 66-test suite ([verification-campaign.md](verification-campaign.md)), is superseded for the current suite | [mutation-push.md](mutation-push.md) section 7 | [`campaigns/mutation/results/diet4-102/summary.json`](../campaigns/mutation/results/diet4-102/summary.json) (`killed` 2,199, `equivalent` 111, `survived` 110) and `survivor_classes.tsv`; jobs in mutation-push.md section 7.5. Arithmetic checked with AXLE | Cluster: [campaigns/mutation/README.md](../campaigns/mutation/README.md) (`campaign-diet4-102.env`) |
| R24 | Host library: 69 unit tests pass; 100,000 fuzz seeds (400,000 to 499,999) pass on the three-way differential and on Icarus 13.0 RTL replays, with 0 oracle problems | [host.md](host.md), "Verification of the library" | Job 23778831 (unit tests); jobs 23778347, 23778353, 23778832, 23778833, 23778835, 23778836 (fuzz) | Local: `reproduce.sh --only host`. Without MicroPython on `PATH`, 4 tests skip. Fuzz: `host/tools/fuzz_host.py` (cluster for 100,000 seeds) |
| R25 | cocotb suite after gap closure: 66/66 on RTL; 36 pass, 30 skip at gate level on the campaign netlist; 66/66 on each of the six variants | verification-campaign.md, "Suite" | Jobs 23819699, 23819700, 23819698 | Local (RTL); see R12 and R3 |
| R26 | Six design variants: 39/39 cocotb each, 16 extra random seeds each (24,576 cases, about 94.5 M cycles), all 16 formal jobs each (112 jobs), gate level 22/22 on LibreLane-replica netlists; TT-synthesis areas base 462,710 µm², `rstreg` 462,675, `cn` 414,536, `cn_s2` 413,522, `diet4` 298,478, `diet2` 257,254 | [variants.md](variants.md) sections 5 and 7.2 | Jobs 23758014 to 23758018 (23758015 has 107 array tasks), 23758039, 23758040, 23758041 (112 tasks), 23761206, 23761207. Suite at `73536f0` (39 tests); R25 covers the 66-test suite | Local: `reproduce.sh --only variants,hardcaml`; `cd test && PE_VARIANT=<name> make`; `formal/run.sh --variant <name>` |
| R27 | FPGA builds: the unchanged `test/` suite (39 tests at `73536f0`) passes on four FPGA-top configurations and on two post-synthesis netlists; the SRAM stand-in is proved equivalent to the IHP model (unbounded, ABC PDR) | [fpga.md](fpga.md), "Verification" | Jobs 23763625, 23757655, 23756607, 23756608, 23764155, 23768231 | Local: `fpga/sim/`, `fpga/formal/run.sh` |

## 4. Formal verification

**`formal/` (in CI; see [formal/README.md](../formal/README.md)).** The
design is `configs/instruction-sram-32.json` with the IHP FUNCTIONAL SRAM
models. The times are from the full local run, Slurm job 23716631 (2 CPUs,
15 min 10 s in total). CI run 36144357811 passed all 16 jobs on `c118027`.
On a fresh export of `c118027`, `scripts/reproduce.sh` met the expectation
of the 12 quick jobs in 223 s and 233 s (jobs 23974810 and 23975260, 2 CPUs),
and of all 16 in job 23974811.

| # | Job | Bound | Engine | Local time (s) |
|---|---|---|---|---:|
| R30 | `reset_safety` | unbounded (k-induction) | smtbmc yices | 3 |
| | `fifo_conservation` | unbounded (IC3/PDR) | abc pdr | 216 |
| | `engine_safety` | BMC 24 | smtbmc yices | 16 |
| | `processor_invariants_bmc` | BMC 24 | smtbmc yices | 29 |
| | `processor_invariants_prove` | unbounded (k-induction) | smtbmc yices | 3 |
| | `processor_inductive_bmc` | BMC 8 | smtbmc yices | 11 |
| | `processor_inductive_prove` | unbounded (k-induction), from any valid state | smtbmc yices | 2 |
| | `processor_inductive_cover` | cover | smtbmc yices | 4 |
| | `timing_isolation_prove_k0` to `_k3` | unbounded (k-induction, depth 2), one job per engine | smtbmc yices | 34–39 each |
| | `timing_isolation_bmc` | BMC 20 | smtbmc bitwuzla | 454 |
| | `timing_isolation_cover` | cover, 4 witnesses | smtbmc yices | 9 |
| | `timing_isolation_neg_pull` | negative control; must FAIL | smtbmc yices | 5 |
| | `timing_isolation_neg_mutant` | negative control; must FAIL | smtbmc yices | 7 |

**`formal_depth/` (cluster; see [formal-depth.md](formal-depth.md)).** These
ran against `73536f0`. The machine-readable results are in
[`formal_depth/results/summary.tsv`](../formal_depth/results/summary.tsv).
The rows below were checked against that file.

| # | Claim | Status | Engines that passed (Slurm id) |
|---|---|---|---|
| R31 | Timing-isolation miter BMC to depth 100 | bounded | rIC3 BMC, 2,643 s (23757105_0). Depth 60: rIC3 (23752066_7) and smtbmc bitwuzla, 7,038 s (23752066_2) |
| R32 | `engine_safety` unbounded | unbounded | suprove on the unmodified harness (23752067_11); k-induction depths 2, 4 and 8 with yices, bitwuzla and boolector (23757443_0 to _8) and rIC3 IC3 (23757443_10) on a copy with three added invariants |
| R33 | `processor_invariants` BMC 128; `processor_inductive` BMC 96 | bounded | abc bmc3, btormc, rIC3 (23757105_3 to _5; _6 to _8) |
| R34 | Host-port atomicity and read snapshots | unbounded | yices, bitwuzla, boolector, rIC3, abc pdr (23756999_0 to _4) |
| R35 | Program-load safety, control part | unbounded | yices, bitwuzla, boolector (23756749_12 to _14) |
| R35b | Program-load SRAM data integrity (`spec_word_*`) | **bounded only**, BMC 48 | abc bmc3, boolector, bitwuzla, btormc (23756748_8 to _11) |
| R36 | Mover conservation, quota and order | unbounded | boolector, bitwuzla, yices, rIC3, abc pdr (23760775_2 to _4, 23760892_0 and _1) |
| R37 | Round-robin grant bound, from every register state | unbounded | boolector, yices, bitwuzla, abc pdr, suprove, rIC3 (23756749_36 to _41) |
| R38 | Fault stickiness and output-enable release; pin ownership and open-drain safety | unbounded | 6 engines each (23756749_48 to _53; _54 to _59) |
| R39 | 13 mutant negative controls, all caught on the named assertion | — | `neg_*` rows of `summary.tsv` (23756748, 23756749, 23756999, 23760775) |

Reproduce: `formal_depth/run.sh generate`, then
`formal_depth/run.sh submit <work dir>` on a cluster, or
`FD_PARALLEL=8 formal_depth/run.sh local <work dir> --only '<regex>'`
without Slurm. The heavy classes need up to 32 GB per task.

## 5. Static timing analysis of the firmware

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R40 | Report on the committed images: 182 PASS, 1 FAIL, 3 WARN, 55 INFO. The FAIL is `i2c-repeated-start` `i2c-scl-low-phase`, a 7-cycle SCL low phase | [timing-analysis.md](timing-analysis.md), "Results" and "Findings" | [`tools/timing/report/checks.json`](../tools/timing/report/checks.json). A regeneration for this page gave the same `checks.json` and `timing-report.md`, byte for byte | Local: `reproduce.sh --only timing`, which takes seconds |
| R41 | Validation against the reference model: 1,303,659 engine runs, 38,037,698 of 38,037,698 pad changes on a predicted edge, 100% path coverage of the committed images; 7 of 7 deliberately wrong analyzers caught | timing-analysis.md, "Validation" | [`tools/timing/report/validation-summary.json`](../tools/timing/report/validation-summary.json) (`runs_ok` 1,303,659, `pad_changes_matched` 38,037,698); runs r2, r3, r4 and r7 (jobs 23757447–23757449, 23759925, 23759928, 23761093, 23762121, 23762123, 23774644, 23776236) | Local: `reproduce.sh --only timing-validate` (base suites only). Cluster: the stress and random arrays ([tools/timing/README.md](../tools/timing/README.md)) |

## 6. Physical exploration (local LibreLane runs; not results of record)

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R50 | The flow is deterministic: four runs of the submission point (8x4, `fp8_base`, density 60, 20 ns; 32 and 48 OpenROAD threads) gave identical metrics, equal to the 4-thread local run `run2` (job 23720702). All were at `73536f0`, before the halo change | [sweep.md](sweep.md), "Results" | `<work dir>/sweep/results.csv`: 23749129_0, 23749537_0, 23749132_0 (full) and 23749131_0 (fast): utilization 0.5854, typ WS +2.938, slow WS −5.09, LVS 0 | Cluster: [sweep.md](sweep.md) |
| R51 | Base 8x4 meets typical-corner setup down to 12.5 ns (80 MHz), WS +0.05 ns | sweep.md | 23749131_6 | Cluster |
| R52 | Full sign-off (LVS 0, route DRC 0, antenna 0) for `rstreg` and `cn_s2` at 8x4 and for `diet4` at 6x4 (`fp6_tworow`, density 65, utilization 56.2%, typ WS +2.85 ns) | sweep.md | 23751798_0, 23751802_0, 23763343_0 | Cluster |
| R53 | The reset variants do not close the slow corner: `rstreg` −7.52 ns, `cn_s2` −4.99 ns (at 73536f0: base −5.09 ns). Still true for these runs; the design of record's slow corner was later closed by configuration, not by RTL (R16) | sweep.md | same rows | Cluster |
| R54 | All 69,448 Magic DRC markers lie inside the SRAM macro footprints and reproduce on the macro alone; KLayout precheck DRC 0 of 332 rule categories on every GDS tested; the 84 illegal overlaps are 32 stripe-over-OBS crossings | [drc-triage.md](drc-triage.md), "Verdict" | `<work dir>/drc-triage/` (`manifest.json`) | Cluster: drc-triage.md section 9 |
| R55 | With `FP_MACRO_HORIZONTAL_HALO` 16.48 and `RUN_MAGIC_DRC` false, a local full run passes the unmodified precheck, all 9 checks | drc-triage.md section 6 | Jobs 23850490 (flow), 23856538 (precheck); confirmed officially by R2 | Cluster |
| R56 | 8x4 area study: 58.4% measured at 8x4; 6x4 needs `diet4`, predicted 55.2% | [area-study.md](area-study.md) section 1 | The study's scripts in `docs/area-study/`; superseded by R4 (58.53% official) and R52 (56.2% measured for `diet4` at 6x4) | Cluster |

## 7. FPGA builds (not run on a board)

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R60 | Seven openXC7 bitstreams, all meeting their clock target in nextpnr-xilinx's timing model after synthesis/place-and-route option and seed optimization (about 12,700 nextpnr runs): `cmod_a7 pll50` DIP pin host 79.69 MHz (was 46.39), bridge-only 78.38, `pll40` 73.91, `osc12` 76.45, `cmod_a7 host` 80.43, `urbana pll50` 86.95, `urbana host` 76.44 MHz; TT design and SRAM replacement unchanged (SRAM proof re-run) | [fpga.md](fpga.md), "Build results" | Release rebuild job 23998703 (each rebuild reproduces its sweep fmax; readback 0 missing/extra bits); bitstreams with SHA256SUMS in `<work dir>/fpga/bitstreams/` (not in git; the 2026-09-25 set in `v1-2026-09-25/`) | Local with the openXC7 toolchain: `fpga/scripts/release.sh <release>` (fpga/scripts/release.tsv) |
| R61 | Configuration readback of all 7 bitstreams | fpga.md, "Build results" | Job 23767112 | Local: `fpga/scripts/readback.sh` |

## 8. Extension study (prototypes outside the repository)

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R70 | Conditional GO for line coding plus CRC-16 at 8x4; prototype checks pass on four configurations (16 whole-chip checks each), with 6 of 6 seeded RTL bugs caught | [extension-study.md](extension-study.md) sections 1 and 5 | Job 23789566 and its log, in `<work dir>/extension/`. The prototype Hardcaml sources are not in git (section 9) | Cluster work area only |

## 9. Claims that are stale or not fully substantiated

These were found while this page was compiled. They are listed so the
owning documents can be corrected. No number above depends on them.

1. **README.md, previous Status section.** It said the design had not been
   hardened and quoted 58.4% utilization and a slow-corner WS of −5.09 ns.
   Those figures came from the local run of `73536f0` (`run2`, job 23720702).
   The official build of `c118027` has 58.53% and −8.52 ns (R4, R7). This
   change replaces that section.
2. **docs/info.md, "Limitations".** It says the design "has not yet passed
   the Tiny Tapeout gds flow". It also says a host library "is planned",
   although `host/` exists (R24). Both statements are stale as of
   `c118027`.
3. **docs/hardening.md, status paragraph and section 9.** They say nothing
   in the document has been run by the GitHub action. Its slow-corner figure,
   −5.09 ns, is the `run2` figure for `73536f0`. The official results (R1 to
   R9) now answer most of the section 9 items.
4. **Mutation score 89.4% (R23b).** The summary and the per-mutant results
   are only in the cluster work area (`<work dir>/test-gaps/`). The
   repository keeps only the 80.2% summary
   (`campaigns/mutation/results/summary.json`). Committing
   `summary_gaps.json` would make the 89.4% auditable from the repository.
5. **"39/39" results.** The FPGA simulations (R27), most of the variant
   results (R26) and the early gate-level figure of 22 pass and 17 skip used
   the 39-test suite of `73536f0`. Only the variant suites were re-run with
   the 66-test suite (R25, job 23819698). The FPGA tops have not been
   simulated with the 66-test suite.
6. **Extension study (R70).** Its prototype sources and tests are in the
   cluster work area, not in the repository, so its PASS results cannot be
   re-run from a clone.
7. **Earlier monorepo results (README.md, "Earlier results").** These are
   the independent-model checks and bounded formal properties of the private
   monorepo, for example Slurm jobs 22625543 and 22620770. They cannot be
   inspected or re-run from this repository.
8. **Gate-level `test_ext/`.** `test_ext/Makefile` does not add
   `sg13cmos5l_udp.v` for gate-level runs. `test/Makefile` gained that line
   in `88f89a1` (see [bug-ledger.md](bug-ledger.md), BL-5). The 65/65
   gate-level result (R22) used a PDK copy whose standard-cell file defines
   the UDPs inline. With the action's PDK revision, a gate-level `test_ext`
   run fails to elaborate (`Unknown module type: ihp_mux4`); with the line
   added it passes (job 23975124, `test_ext_uart`, 5 of 5). The R22
   gate-level figure therefore depends on the PDK copy used.
9. **Reproducing a campaign seed.** [verification-campaign.md](verification-campaign.md)
   says any seed of a `default` campaign reproduces with
   `PE_SEED=<seed> make COCOTB_TEST_MODULES=test_random`. That holds for
   the `73536f0` test tree. Since `c118027` the default generator is
   generation 2. `PE_RANDOM_GEN=1` draws the campaign's cases, except that a
   trailing `deselect` now runs ([test/README.md](../test/README.md)).
10. **Runs recorded as unfinished.** [formal-depth.md](formal-depth.md) lists
   runs that had not finished when it was written, for example abc pdr and
   rIC3 on the unmodified `engine_safety` and on the SRAM data-integrity
   proof. They are not claimed as results.
11. **Mutation score 96.82% (R23c).** The per-mutant status of the push,
   re-tallied on the `4bd30c8` tree, is in
   `campaigns/mutation/results/push-4bd30c8/` (`summary.json`,
   `mutant_status.tsv`); the stage and formal scripts are in
   `campaigns/mutation/` ([mutation-push.md](mutation-push.md) section 6).
   The raw per-test logs stay in `<work dir>/mutation97/`.
   `campaigns/mutation/results/summary.json` holds the 80.2% campaign
   summary (R23).
12. **The official 6x4 run of `4bd30c8` (R82, R83).** Resolved: run
   36274474540 passed all jobs, and its `metrics.csv` equals that of the
   local p014 full run byte for byte.
13. **What the 6x4 overlay pins.** At `4bd30c8`
   `variants6x4/config.overlay.json` restated 15 of the 33 keys that the
   optimizer treats as knobs (`tools/opt/space.py` `KNOB_KEYS`): the keys in
   which p014 differs from `src/config.json`. Five more (`SYNTH_STRATEGY`,
   `PL_TIMING_DRIVEN`, `DESIGN_REPAIR_MAX_WIRE_LENGTH`, `CLOCK_PERIOD`,
   `GRT_RESIZER_HOLD_SLACK_MARGIN`) came from `src/config.json`, and twelve
   were unset in both files, so a later 8x4 change of any of them would
   have reached the 6x4 build (as would one of `OPENROAD_THREADS`). Since
   `8a05de7` and `fdc23f2` the overlay states all 33 knob keys: a value, or
   `null` for LibreLane's default; `switch.py check` accepts a `null` for a
   key that `src/config.json` lacks, `tools/opt/test_opt.py` fails if a
   knob key is missing, and `variants6x4/info.overlay.json` restates
   `clock_hz` 50000000. `switch.py apply` writes the same configuration as
   at `4bd30c8` (R83), apart from the order of two keys.
14. **`docs/variants.md`** (section on reset styles) said that
   `sync_registered` removes the `rst_n` paths "that fail slow-corner setup",
   in the present tense. That described the `c118027` configuration (R7); the
   sentence now says so, and that the design of record meets the slow corner
   since `25e331e` (R16).
15. **The 50 MHz margins (R86).** The scripts, inputs, reports and the AXLE
   check of the re-analysis are in `<work dir>/sta50/`, not in git, so the
   figures cannot be re-run from a clone. They use only files that the
   repository's flow produces (the final netlist and nominal SPEF) and the
   LibreLane 3.1.0.dev3 image of the local sign-off runs. The official flow reports the
   design at 15 ns only (R85).
