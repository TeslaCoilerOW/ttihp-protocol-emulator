# Results and how to reproduce them

This page lists every headline number that the repository states, as of
commit `c118027` (tag `v0.1-hardened`). Each row gives:

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

These are the results of record. Run
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
| R3 | gl_test PASS: 66 tests, 36 pass, 30 skip, 0 fail | README.md (Status); [test/README.md](../test/README.md) (skips by design) | Run 36144357821, job `gl_test`: `TESTS=66 PASS=36 FAIL=0 SKIP=30` | CI. Local: `scripts/reproduce.sh --only gl` with `PDK_ROOT` and `GL_NETLIST` set to the artifact netlist |
| R4 | Utilization 58.5% (58.53%; standard cells alone 53.88%) | README.md; tag message | `design__instance__utilization` 0.585345, `__stdcell` 0.538803 | CI or local mirror |
| R5 | Route DRC 0, LVS 0, antenna 0 | README.md; tag message | `route__drc_errors` 0 (the last detailed-routing iteration, 25, ended at 0); every `design__lvs_*` counter 0; `route__antenna_violation__count` 0, with 84 antenna diodes inserted | CI or local mirror |
| R6 | Setup met at the typical corner at 50 MHz, WS +0.89 ns; at the fast corner WS +6.15 ns | README.md; tag message | `timing__setup__ws` typ 0.8854, fast 6.1533; 0 setup violations at typ and fast | CI or local mirror |
| R7 | Slow corner (not Tiny Tapeout sign-off) misses setup, WS −8.52 ns | README.md; tag message | slow `timing__setup__ws` −8.5171; 2,482 violating endpoints; register-to-register WS −0.116 ns (1 violation). The local mirror with the same configuration (job 23850490) gave identical values for the typical and slow setup slack, the register-to-register slack and the utilization. Its report puts 2,467 of the violations on `rst_n` to a register, 14 on `rst_n` to an output, and the one register-to-register path from `instruction_sram_e2_hi` `A_DOUT[1]` | CI or local mirror |
| R8 | Hold met at every corner | tag message | hold WS: fast +0.107 ns, typ +0.297 ns, slow +0.629 ns; 0 hold violations | CI or local mirror |
| R9 | 84 Magic illegal overlaps, waived by configuration; Magic DRC not run | [drc-triage.md](drc-triage.md) sections 4 and 7 | `magic__illegal_overlap__count` 84; `RUN_MAGIC_DRC` false in `src/config.json` since `1e2cfb3` | CI or local mirror |

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
| R12 | cocotb RTL suite: 66 tests pass | Same run, job `test`: `TESTS=66 PASS=66 FAIL=0 SKIP=0` (Icarus 13.0) | Local: `reproduce.sh --only cocotb` |
| R13 | All 16 formal jobs meet their expectation in CI | `formal` run [36144357811](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36144357811): the generation job and all 16 matrix jobs succeeded. The longest was `timing_isolation_bmc`, at 8 min 20 s | Local: `reproduce.sh --only formal,formal-rest` |
| R14 | Datasheet builds | `docs` run [36144357954](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36144357954): success | CI |

The runs on the tag push (`test` 36188299625, `formal` 36188299526, `regen`
36188299483, `docs` 36188299516) also succeeded.

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
| R23b | Mutation score 89.4% (2,060 / 2,304) after gap closure; 85.8% (1,976) without `test_timewarp` | verification-campaign.md, "Gap closure"; commit message of `c118027` | `<work dir>/test-gaps/summary_gaps.json` (`killed` 2,060, `score` 0.8941, `killed_without_timewarp` 1,976); jobs 23813966, 23813967, 23817415, 23819701. **Not in git** (section 9) | Cluster |
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
| R53 | The reset variants do not close the slow corner: `rstreg` −7.52 ns, `cn_s2` −4.99 ns (at 73536f0: base −5.09 ns) | sweep.md | same rows | Cluster |
| R54 | All 69,448 Magic DRC markers lie inside the SRAM macro footprints and reproduce on the macro alone; KLayout precheck DRC 0 of 332 rule categories on every GDS tested; the 84 illegal overlaps are 32 stripe-over-OBS crossings | [drc-triage.md](drc-triage.md), "Verdict" | `<work dir>/drc-triage/` (`manifest.json`) | Cluster: drc-triage.md section 9 |
| R55 | With `FP_MACRO_HORIZONTAL_HALO` 16.48 and `RUN_MAGIC_DRC` false, a local full run passes the unmodified precheck, all 9 checks | drc-triage.md section 6 | Jobs 23850490 (flow), 23856538 (precheck); confirmed officially by R2 | Cluster |
| R56 | 8x4 area study: 58.4% measured at 8x4; 6x4 needs `diet4`, predicted 55.2% | [area-study.md](area-study.md) section 1 | The study's scripts in `docs/area-study/`; superseded by R4 (58.53% official) and R52 (56.2% measured for `diet4` at 6x4) | Cluster |

## 7. FPGA builds (not run on a board)

| # | Claim | Stated in | Evidence | Reproduce |
|---|---|---|---|---|
| R60 | Seven openXC7 bitstreams built; the four recommended ones meet 50 MHz in nextpnr's timing model (`cmod_a7 pll50` bridge-only 55.82 MHz, `cmod_a7 host` 51.42 MHz, `urbana pll50` 54.35 MHz, `urbana host` 61.92 MHz); `cmod_a7 pll50` with the DIP pin host reaches 46.39 MHz and does not meet 50 MHz | [fpga.md](fpga.md), "Build results" | Jobs 23760777, 23760779, 23763553, 23760781, 23763555, 23760780, 23763558; bitstreams and summaries in `<work dir>/fpga/bitstreams/` (not in git) | Local with the openXC7 toolchain: `fpga/scripts/build.sh <board> <clock> <dir> heap:1 ...` |
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
