# RTL mutation campaign

Scripts for the mutation-testing campaign reported in
[docs/verification-campaign.md](../../docs/verification-campaign.md#mutation-testing).
They mutate `src/protocol_emulator_core.v` of a frozen snapshot with Yosys
`mutate` (the mutation engine used by mcy) and run the snapshot's own cocotb
suite on every mutant as Slurm job arrays. Small result summaries are in
`results/` (`results/diet4/`: the diet4 campaign; `results/push-4bd30c8/` and
`results/diet4-102/`: the later additions of docs/mutation-push.md). The bulky
per-mutant files stay in the campaign directory `$CAMP`.

| File | Purpose |
|---|---|
| `campaign.env` | Commit, seeds, region quotas and paths used for the reported run |
| `campaign-diet4.env` | The same for the design-variant `diet4` campaign at c118027 (see "Design variants") |
| `campaign-push.env` | Parameters of the mutation score push of [docs/mutation-push.md](../../docs/mutation-push.md) (see "Mutation score push") |
| `campaign-diet4-102.env` | Parameters of the `diet4` re-run under the 102-test suite (see "Variant diet4 under the 102-test suite") |
| `campaign-heldout.env` | Parameters of the held-out sample of [docs/mutation-push.md](../../docs/mutation-push.md) section 9 (seed, quotas, exclusions) |
| `heldout_sample.py` | Draw the held-out sample: the raw `mutate -list` database per region, minus every (mode, cell, port) of the push's mutants, sampled uniformly per region with a recorded seed |
| `gen_mutants.sh` | `prep` the core, map regions, run one seeded `mutate -list` per region, write `mutations.tsv` |
| `region_map.py` | Assign each statement/cell of the flat Hardcaml netlist to a functional region (nearest named state in its forward cone) |
| `run_mutant.py` | Array-task body: build one mutant, run a stage, write `results/<stage>/<id>.json` (idempotent; requeue-safe) |
| `submit.sh` | Submit a stage as a job array and record it in `vcamp/manifest.json` (`MANIFEST` overrides); `DESIGN` and `RESULTS` select another design directory and result directory |
| `mk_design.sh` | Make a design directory that links the prepared core and mutant list of an existing one and packs a new test tree (another commit's suite); optional `kill_modules.txt` |
| `manifest.py` | Locked read-modify-write of the shared manifest |
| `equiv_mutant.py` | Yosys `equiv_make`/`equiv_simple`/`equiv_induct` proof of a survivor against the unmutated core |
| `formal_mutant.py`, `mk_miter.py` | Gold/mutant miter (SRAM macros cut out, shared read data, SRAM inputs asserted equal, power-up reset) written as AIGER and checked by ABC (`fold; strash; lcorr; scorr; pdr`); docs/mutation-push.md section 4.2 |
| `equiv_inv.py`, `einv_specs.py` | `equiv_induct` with an SRAM read model and design invariants added as correspondences; `einv_specs.py` writes the invariant library for a core; section 4.3 |
| `push_summary.py` | Combine simulation stages and proof methods into one status per mutant, the score and the per-region table |
| `mutant_diff.py` | The assignments of a mutant's netlist that differ from the unmutated core (survivor analysis) |
| `survivor_classes.py` | Group survivors by the named state the mutated statement feeds and by the RIP outcome (bookkeeping for a hand classification) |
| `noop_check.py` | Find const0/const1 mutants whose target bit already has that constant |
| `engine_map.py` | Map de-duplicated per-engine register names (`pc_0`, `x_2`, ...) to engine indices |
| `summarize.py` | Counts, score, per-region/per-mode tables, job ids |
| `evidence.py` | Per-survivor evidence: equivalence, RIP, deep random, directed tests |
| `describe.py` | Print a mutant's statement and its path to the nearest named state |
| `sample_classification.py` | Hand classification of the 48-survivor sample, merged with the evidence |
| `directed/test_mutation_gaps.py` | Directed tests written from the survivor analysis (not part of the repo suite) |

Stages of `run_mutant.py`:

| Stage | Modules | Notes |
|---|---|---|
| `fast` | smoke, protocols, flagship, random (8 cases) | stops at the first failing module |
| `full` | all 39 tests with default settings | fast-set survivors only |
| `rip` | all 39 tests on a wrapper holding the unmutated core and the mutant | the unmutated core drives the pins; every register, FIFO word and SRAM pin net is compared each cycle. The harness's time warp adds its deltas to the registers of both cores (`RIP_WARP_PATCH`, appended to the task tree's copy of `test/harness.py`). Runs before job 24055015 lacked the patch: there the tests of `test_timewarp` failed at their start (`warp_supported()` is false on the wrapper), the other tests that warp failed at their first warp (`WarpUnsupported`), and `test_kill_route_count_parity` skipped its warped cases, so those runs do not cover the rest of those tests. With the patch every test must pass on the wrapper; `survivor_classes.py` reports a run in which one fails as `incomplete` |
| `deep` | `test_random`, 256 cases, `PE_SEED=0xD33B2027` | full-suite survivors |
| `suite` | the snapshot's whole suite: the default `COCOTB_TEST_MODULES` of its `test/Makefile` (66 tests in nine modules at c118027, 102 tests in 20 modules from aa07868 on), stopping at the first failing module | fast-set survivors (used instead of `full` for the diet4 campaign); every mutant in the push (there called `head`, on a c118027 tree) and in the diet4 re-run |
| `kill` | the modules listed in the design directory's `kill_modules.txt`; every module runs, and `killed_modules` lists each one that fails | the push's `test_kill_*` iterations |
| `kill-rip` | the `kill` modules on the RIP wrapper of stage `rip` | survivor analysis |
| `directed` | `directed/test_mutation_gaps.py` | full-suite survivors and the two controls |
| `directed-rip` | the directed tests on the RIP wrapper | diagnosing a directed test that misses its target |

Reproduce (from a login node; every heavy step runs in Slurm):

```sh
source campaigns/mutation/campaign.env
mkdir -p $SNAPSHOT && git archive $SNAPSHOT_COMMIT | tar -x -C $SNAPSHOT   # from the repository root
export PATH=$OSS_CAD_SUITE/bin:$PATH   # OSS_CAD_SUITE = OSS CAD Suite root; PE_WORK = cluster work directory
srun -p mit_quicktest -c 2 --mem=6G -t 10 campaigns/mutation/gen_mutants.sh $SNAPSHOT $CAMP/design $QUOTAS $SEED
cd $CAMP && cut -f1 design/mutations.tsv > all_ids.txt && printf 'orig\n0\n' > baseline_ids.txt
S=/path/to/repo/campaigns/mutation/submit.sh
$S fast baseline_ids.txt 2 00:30:00 && $S full baseline_ids.txt 2 01:00:00     # controls must survive
$S fast all_ids.txt 240 02:00:00
python3 .../summarize.py . --write-survivors full_ids.txt && PAR=8 $S full full_ids.txt 13 03:00:00
python3 .../summarize.py . --write-final-survivors final_survivors.txt
PAR=8 $S equiv final_survivors.txt 8 01:00:00
PAR=4 $S rip final_survivors.txt 36 03:00:00
PAR=4 $S deep final_survivors.txt 40 03:00:00
mkdir -p design/directed && cp .../directed/test_mutation_gaps.py design/directed/
(printf 'orig\n0\n'; cat final_survivors.txt) > directed_ids.txt && PAR=16 $S directed directed_ids.txt 2 00:30:00
python3 -c "import random; ids=open('final_survivors.txt').read().split(); random.seed(20270118); print('\n'.join(sorted(random.sample(ids, 48), key=int)))" > classify_sample.txt
python3 .../summarize.py . --json summary.json
python3 .../sample_classification.py . sample_classification.tsv
```

`PAR=n` runs n runner processes per array task (one per CPU), which keeps the
number of queued jobs low; `RUNNER_ARGS="--deadline S"` stops a task from
starting new mutants after S seconds (for `mit_quicktest`). The equivalence
keep list (`design/equiv_keep_*.txt`) is created by the first `equiv` task.

## Design variants

Every addition below is inactive for the design of record, so the commands
above reproduce the 73536f0 campaign unchanged (the region map of that core,
for example, is byte-identical).

- **Mutating a variant core.** `PE_VARIANT=<name> gen_mutants.sh ...` mutates
  `SNAPSHOT_DIR/build/variants/<name>/protocol_emulator_core.v` (run
  `scripts/gen_variants.sh <name>` in the snapshot first; `MUTATE_CORE=<file>`
  overrides), packs `build/variants/<name>/` (the variant's firmware images, not
  its unmutated core) into `testtree.tgz` and writes `design/variant.txt`.
- **Running the suite on it.** `run_mutant.py` reads `design/variant.txt` and
  passes `PE_VARIANT=<name>` and `PE_CORE=<mutant>` to every `make`, so the
  snapshot's Makefile compiles the mutant and the harness configures the
  variant's reference model. For the base design it removes both variables
  from the environment.
- **Register queues.** The variants with `fifo_storage_reset` (`cn`, `cn_s2`,
  `diet4`, `diet2`) build each queue from anonymous registers rather than a
  memory. When a core has no memory arrays, `region_map.py` takes groups of
  two or more anonymous registers that load the same data signal under
  different enables as queue storage, so the `fifo` region exists there too
  (32 registers, 8 queues of 4 words, in `diet4`).
- **Asynchronous reset.** When `base.il` has asynchronously reset flops,
  `equiv_mutant.py` also keeps the outputs of `$adff` registers as matched
  points and runs `async2sync` on both copies before `equiv_make`.
- **Stages and summary.** Stage `suite` runs the snapshot's whole suite on the
  fast-set survivors. `summarize.py --second-stage suite` scores with it
  instead of `full`. `submit.sh` takes `MANIFEST`, `JOB_PREFIX` and
  `SNAPSHOT_COMMIT` from the environment, and records the design's variant.

```sh
source campaigns/mutation/campaign-diet4.env
mkdir -p $SNAPSHOT && git archive $SNAPSHOT_COMMIT | tar -x -C $SNAPSHOT
(cd $SNAPSHOT && scripts/gen_variants.sh diet4)      # core sha256 = $CORE_SHA256
PE_VARIANT=diet4 srun -p mit_quicktest -c 2 --mem=8G -t 15 campaigns/mutation/gen_mutants.sh $SNAPSHOT $CAMP/design $QUOTAS $SEED
export CAMP MANIFEST=$CAMP/../manifest.json JOB_PREFIX=pe-mut-diet4 SNAPSHOT_COMMIT
S=/path/to/repo/campaigns/mutation/submit.sh
cd $CAMP && cut -f1 design/mutations.tsv > all_ids.txt && printf 'orig\n0\n' > baseline_ids.txt
$S fast baseline_ids.txt 2 00:15:00 mit_quicktest && $S suite baseline_ids.txt 2 00:15:00 mit_quicktest   # controls must survive
PAR=8 $S fast all_ids.txt 8 02:00:00 mit_preemptable,mit_normal
python3 .../summarize.py . --second-stage suite --write-survivors suite_ids.txt
PAR=8 $S suite suite_ids.txt 8 01:00:00 mit_preemptable,mit_normal
python3 .../summarize.py . --second-stage suite --write-final-survivors final_survivors.txt
PAR=8 $S equiv final_survivors.txt 3 00:30:00 mit_preemptable,mit_normal
PAR=8 $S equiv-negctl equiv_negctl_ids.txt 1 00:15:00          # 40 fast-killed mutants, random.seed(20270118)
PAR=8 $S deep unproven_survivors.txt 4 01:00:00 mit_preemptable,mit_normal
python3 .../summarize.py . --second-stage suite --json summary.json --status-tsv mutant_status.tsv
```

On `mit_quicktest` (15 minutes) the same stages ran as repeated rounds with
`RUNNER_ARGS="--deadline 540"` or `600` on the ids still without a result;
every runner skips ids whose result file exists.

Results of that campaign: `results/diet4/`.

## Mutation score push

[docs/mutation-push.md](../../docs/mutation-push.md) re-ran the 2,420 mutants of
the 73536f0 campaign (same `design/`, same `base.il` and `mutations.tsv`) under
the committed suite and classified the survivors formally. The scripts it used
in its work directory are these (sha256 of the work-directory originals in
brackets):

| push script | here | changes |
|---|---|---|
| `run_mutant97.py` [`11f1c1cc…`] | `run_mutant.py` stages `suite` (the push's `head`, on a c118027 tree), `kill`, `kill-rip` | merged into the campaign runner; `kill` reads `kill_modules.txt` as before; stages without `all_modules` behave exactly as before, except that the RIP stages now apply the time warp to both cores (`run_mutant97.py` did not: in its `kill-rip` job 23984727, `test_kill_exact_timeout_limits` and `test_kill_time_high` stopped at their first warp) |
| `submit97.sh` [`0f71ca45…`], `submit_formal.sh` | `submit.sh` with `DESIGN`, `RESULTS`, `formal*`, `einv*` | no local paths; `PAR` and the frozen-runner copy as before |
| `mkdesign.sh` [`2c353ea1…`] | `mk_design.sh` | the snapshot and the kill-module list are arguments; `EXTRA_TESTS` adds uncommitted test files |
| `formal_mutant.py` [`e6a1939e…`], `mk_miter.py` [`20298fd5…`] | same names | byte-identical copies (job 23977941 ran `formal_mutant-5f45759b…`, which differs only by two unused ABC recipes and an explicit `MITER_MODE` pass-through; `mk_miter.py` was last changed before that job started) |
| `equiv_inv.py` [`d5bcb5e4…`] | `equiv_inv.py` | adds the asynchronous-reset handling of `equiv_mutant.py` (`$adff` outputs matched, `async2sync`) for design variants; on the design of record the Yosys script is unchanged |
| `einv_specs.py` [`96edaab2…`] | `einv_specs.py` | `--invariants-only` writes the spec of job 23986701 (byte-identical to the push's `einv_invonly.json`); queue counts can be given (`--fifo-counts`) or found (`--core`, `--fifo-depth`) |
| `summary97.py` [`ceaf0fee…`] | `push_summary.py` | stages and proof directories are arguments |
| `mkdiffs.sh` [`0595f7b3…`] + `cleandiff.py` [`60212d7b…`] | `mutant_diff.py` | same output |

The k-induction attempts of section 4.4 (`kind_mutant.py`, `kind_specs.py`,
the `SHARED`/`REG`/`EXTRA`/`MITER_STATE` modes of `mk_miter.py`) produced no
result that is counted and are not copied.

Summary of the push as run (its result directories, `campaign-push.env`):

```sh
source campaigns/mutation/campaign.env; source campaigns/mutation/campaign-push.env
H=$PWD/campaigns/mutation
python3 $H/push_summary.py $PUSH_DIR/design --sim head=$PUSH_DIR/results/head-design \
  --sim kill-final=$PUSH_DIR/results/kill-design_final --sim kill-k6=$PUSH_DIR/results/kill-design_k6 \
  --sim kill-k7=$PUSH_DIR/results/kill-design_k7 --proof equiv_induct=$CAMP/results/equiv \
  --proof miter-abc=$PUSH_DIR/results/formal1 --proof equiv_inv=$PUSH_DIR/results/einv-invonly
# killed 2223, equivalent 124 (116 + 6 + 2), survived 73, score 0.9682
```

Re-run with these scripts (login node; every heavy step is a Slurm array):

```sh
source campaigns/mutation/campaign.env; source campaigns/mutation/campaign-push.env
export PATH=$OSS_CAD_SUITE/bin:$PATH MANIFEST=$PUSH/manifest.json JOB_PREFIX=pe-mut-push SNAPSHOT_COMMIT=$HEAD_COMMIT
H=$PWD/campaigns/mutation; S=$H/submit.sh; SRC=$CAMP/design
for c in $HEAD_COMMIT $KILL_COMMIT; do mkdir -p $PUSH/snap-$c && git archive $c | tar -x -C $PUSH/snap-$c; done
$H/mk_design.sh $SRC $PUSH/snap-$HEAD_COMMIT $PUSH/design
EXTRA_TESTS="$(ls $PUSH/snap-$KILL_COMMIT/test/test_kill_*.py)" \
  $H/mk_design.sh $SRC $PUSH/snap-$HEAD_COMMIT $PUSH/design_kill $KILL_MODULES
cd $PUSH && (echo orig; cut -f1 design/mutations.tsv) > all_ids.txt
# 1. the c118027 suite on every mutant (the push's stage head; controls orig and 0 must survive)
CAMP=$PUSH DESIGN=$PUSH/design RESULTS=$PUSH/results/head PAR=8 $S suite all_ids.txt 31 02:00:00 mit_preemptable,mit_normal
python3 $H/push_summary.py design --sim head=results/head --write-survivors head_survivors.txt      # 360
# 2. the committed test_kill_* modules on those survivors (every module runs)
(printf 'orig\n0\n'; cat head_survivors.txt) > kill_ids.txt
CAMP=$PUSH DESIGN=$PUSH/design_kill RESULTS=$PUSH/results/kill PAR=8 $S kill kill_ids.txt 12 02:00:00 mit_preemptable,mit_normal
# 3. miter + ABC on the survivors not proven by the campaign, with the twelve controls
python3 $H/push_summary.py design --sim head=results/head --sim kill=results/kill \
  --proof equiv_induct=$CAMP/results/equiv --write-survivors formal_ids.txt
(cat formal_ids.txt; printf '%s\n' $FORMAL_CONTROLS) > formal1_ids.txt
CAMP=$PUSH PAR=8 MEM_PER_RUNNER=4 RUNNER_ARGS="$FORMAL_ARGS" $S formal formal1_ids.txt 8 01:00:00 mit_preemptable,mit_normal
# 4. equiv_induct with the invariant library on the rest, with three killed controls
python3 $H/einv_specs.py --invariants-only - $SRC/engine_map.json einv_invonly.json
python3 $H/push_summary.py design --sim head=results/head --sim kill=results/kill --proof equiv_induct=$CAMP/results/equiv \
  --proof miter-abc=results/formal --write-survivors einv_ids.txt
printf '%s\n' $EINV_CONTROLS >> einv_ids.txt
CAMP=$PUSH PAR=8 MEM_PER_RUNNER=4 RUNNER_ARGS="--spec $PUSH/einv_invonly.json" $S einv einv_ids.txt 2 01:00:00 mit_preemptable,mit_normal
# 5. score and survivor material
python3 $H/push_summary.py design --sim head=results/head --sim kill=results/kill --proof equiv_induct=$CAMP/results/equiv \
  --proof miter-abc=results/formal --proof equiv_inv=results/einv --json summary.json --status-tsv mutant_status.tsv \
  --write-survivors survivors.txt
python3 $H/describe.py design --ids-file survivors.txt > survivors.describe.txt
python3 $H/mutant_diff.py design --ids-file survivors.txt --out diffs --jobs 8                        # on a compute node
CAMP=$PUSH DESIGN=$PUSH/design_kill RESULTS=$PUSH/results/kill-rip PAR=8 $S kill-rip survivors.txt 2 02:00:00 mit_preemptable,mit_normal
```

Later additions (docs/mutation-push.md sections 4.5 and 6.1; results in
`results/push-4bd30c8/`): the whole committed suite in one stage, and the miter
with `formal_mutant.py`'s default limits on the 73 survivors:

```sh
mkdir -p $PUSH/snap-4bd30c8 && git archive 4bd30c8 | tar -x -C $PUSH/snap-4bd30c8
$H/mk_design.sh $SRC $PUSH/snap-4bd30c8 $PUSH/design_4bd30c8
cd $PUSH && (echo orig; cut -f1 design_4bd30c8/mutations.tsv) > all_ids.txt
CAMP=$PUSH DESIGN=$PUSH/design_4bd30c8 RESULTS=$PUSH/results/suite-4bd30c8 PAR=8 $S suite all_ids.txt 25 02:00:00 mit_preemptable,mit_normal
python3 $H/push_summary.py design_4bd30c8 --sim suite=results/suite-4bd30c8 --proof equiv_induct=$CAMP/results/equiv \
  --proof miter-abc=$PUSH_DIR/results/formal1 --proof equiv_inv=$PUSH_DIR/results/einv-invonly --write-survivors survivors73.txt
(cat survivors73.txt; printf '%s\n' $FORMAL_CONTROLS) > formal_long_ids.txt
CAMP=$PUSH DESIGN=$PUSH/design_4bd30c8 PAR=8 MEM_PER_RUNNER=5 RUNNER_ARGS="--props strict --pdr-seconds 600 --abc-timeout 900" \
  $S formal-long formal_long_ids.txt 11 01:30:00 mit_preemptable,mit_normal
python3 $H/push_summary.py design_4bd30c8 --sim suite=results/suite-4bd30c8 --proof equiv_induct=$CAMP/results/equiv \
  --proof miter-abc=$PUSH_DIR/results/formal1 --proof equiv_inv=$PUSH_DIR/results/einv-invonly \
  --proof miter-abc-900s=results/formal-long --json summary-longcap.json
```

The push itself reached stage 2 in iterations (k1 to k7, uncommitted versions of
the kill modules, `EXTRA_TESTS`); the committed modules are those of k7. The
ABC and `equiv_induct` caps are wall-clock limits, so an undecided mutant
("unknown", "timeout") can be decided on a faster node or not at all on a slower
one; only "equivalent" counts, and it does not depend on the node.

## Variant diet4 under the 102-test suite

The mutants of the `diet4` campaign above (same design directory) re-run under
the 102-test suite of 4bd30c8, then classified as in the push: the campaign's
`equiv_mutant.py` results are reused (same `base.il`), `formal_mutant.py` and
`equiv_inv.py` run on the rest. Results: `results/diet4-102/`; write-up:
[docs/mutation-push.md](../../docs/mutation-push.md), section 7.

```sh
source campaigns/mutation/campaign-diet4.env; source campaigns/mutation/campaign-diet4-102.env
export PATH=$OSS_CAD_SUITE/bin:$PATH
H=$PWD/campaigns/mutation; S=$H/submit.sh
mkdir -p $SNAPSHOT && git archive $SNAPSHOT_COMMIT | tar -x -C $SNAPSHOT
(cd $SNAPSHOT && DUNE_BUILD_DIR=$CAMP/../_build scripts/gen_variants.sh)   # every variant; its checks must pass
sha256sum $SNAPSHOT/build/variants/diet4/protocol_emulator_core.v variants6x4/protocol_emulator_core.v   # both $CORE_SHA256
(cd $SNAPSHOT/test && make PE_VARIANT=diet4)      # Icarus 13.0 + cocotb 2.0.1 (CI): 102 tests pass
$H/mk_design.sh $SRC_DESIGN $SNAPSHOT $CAMP/design
$H/mk_design.sh $SRC_DESIGN $SNAPSHOT $CAMP/design_rip $(sed -n 's/^COCOTB_TEST_MODULES ?= //p' $SNAPSHOT/test/Makefile | tr ',' ' ')
export CAMP MANIFEST=$CAMP/../manifest.json JOB_PREFIX=pe-mut-diet4-102 SNAPSHOT_COMMIT
cd $CAMP && (echo orig; cut -f1 design/mutations.tsv) > all_ids_orig.txt
# 1. the whole suite on every mutant (orig and 0 must survive)
PAR=8 $S suite all_ids_orig.txt 25 02:00:00 mit_preemptable,mit_normal
python3 $H/push_summary.py design --sim suite=results/suite --proof equiv_induct=$OLD_CAMP/results/equiv \
  --write-survivors formal_ids0.txt
# 2. controls (random.seed 20270118): 6 killed by test_smoke and 6 proven equivalent for the miter,
#    3 other killed mutants for equiv_inv
python3 - <<'PY'
import glob, json, os, random
res = {json.load(open(f))["id"]: json.load(open(f)) for f in glob.glob("results/suite/*.json")}
old = os.environ["OLD_CAMP"] + "/results/equiv/"
eq = sorted((i for i, r in res.items() if i not in ("orig", "0") and r["status"] == "survived"
             and json.load(open(old + i + ".json"))["status"] == "equivalent"), key=int)
smoke = sorted((i for i, r in res.items() if r["status"] == "killed" and r["killed_by"]["module"] == "test_smoke"), key=int)
random.seed(20270118)
c_eq, c_k = sorted(random.sample(eq, 6), key=int), sorted(random.sample(smoke, 6), key=int)
killed = sorted((i for i, r in res.items() if r["status"] == "killed" and i not in c_k), key=int)
c_ei = sorted(random.sample(killed, 3), key=int)
open("formal_controls.txt", "w").write("\n".join(c_k + c_eq) + "\n")
open("einv_controls.txt", "w").write("\n".join(c_ei) + "\n")
PY
cat formal_ids0.txt formal_controls.txt > formal_ids.txt; cat formal_ids0.txt einv_controls.txt > einv_ids.txt
(echo 0; cat formal_ids0.txt) > rip_ids.txt
# 3. miter + ABC, equiv_induct with the invariant library, and the RIP run of the whole suite
PAR=8 MEM_PER_RUNNER=4 RUNNER_ARGS="$FORMAL_ARGS" $S formal formal_ids.txt 8 01:00:00 mit_preemptable,mit_normal
python3 $H/engine_map.py design/core_orig.v > engine_map.json
python3 $H/einv_specs.py --invariants-only - engine_map.json einv_invonly.json --core design/base_roundtrip.v --fifo-depth $FIFO_DEPTH
PAR=8 MEM_PER_RUNNER=4 RUNNER_ARGS="--spec $CAMP/einv_invonly.json" $S einv einv_ids.txt 3 01:30:00 mit_preemptable,mit_normal
# RIP with the time warp on both cores (job 24055120; the first run, 24047832, predates RIP_WARP_PATCH)
DESIGN=$CAMP/design_rip RESULTS=$CAMP/results/suite-rip-warp PAR=8 $S kill-rip rip_ids.txt 15 02:00:00 mit_preemptable,mit_normal
# 4. score, status table and survivor classes
python3 $H/push_summary.py design --sim suite=results/suite --proof equiv_induct=$OLD_CAMP/results/equiv \
  --proof miter-abc=results/formal --proof equiv_inv=results/einv \
  --json summary.json --status-tsv mutant_status.tsv --write-survivors survivors.txt
python3 $H/survivor_classes.py design --ids-file survivors.txt --rip results/suite-rip-warp --tsv survivor_classes.tsv
```

The longer-cap miter of section 7.3 (not part of the push's procedure):

```sh
cat survivors.txt formal_controls.txt > formal_long_ids.txt
PAR=8 MEM_PER_RUNNER=5 RUNNER_ARGS="--props strict --pdr-seconds 600 --abc-timeout 900" \
  $S formal-long formal_long_ids.txt 15 01:30:00 mit_preemptable,mit_normal
python3 $H/push_summary.py design --sim suite=results/suite --proof equiv_induct=$OLD_CAMP/results/equiv \
  --proof miter-abc=results/formal --proof equiv_inv=results/einv --proof miter-abc-900s=results/formal-long \
  --json summary-longcap.json
```

`einv_specs.py --core ... --fifo-depth 4` finds the eight 3-bit queue counters
of `diet4` (the anonymous registers compared only with 4); the invariant
library is otherwise the push's, mapped through the variant's `engine_map.json`.
