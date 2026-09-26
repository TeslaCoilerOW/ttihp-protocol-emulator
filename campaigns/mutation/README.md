# RTL mutation campaign

Scripts for the mutation-testing campaign reported in
[docs/verification-campaign.md](../../docs/verification-campaign.md#mutation-testing).
They mutate `src/protocol_emulator_core.v` of a frozen snapshot with Yosys
`mutate` (the mutation engine used by mcy) and run the snapshot's own cocotb
suite on every mutant as Slurm job arrays. Small result summaries are in
`results/`. The bulky per-mutant files stay in the campaign directory `$CAMP`.

| File | Purpose |
|---|---|
| `campaign.env` | Commit, seeds, region quotas and paths used for the reported run |
| `campaign-diet4.env` | The same for the design-variant `diet4` campaign at c118027 (see "Design variants") |
| `gen_mutants.sh` | `prep` the core, map regions, run one seeded `mutate -list` per region, write `mutations.tsv` |
| `region_map.py` | Assign each statement/cell of the flat Hardcaml netlist to a functional region (nearest named state in its forward cone) |
| `run_mutant.py` | Array-task body: build one mutant, run a stage, write `results/<stage>/<id>.json` (idempotent; requeue-safe) |
| `submit.sh` | Submit a stage as a job array and record it in `vcamp/manifest.json` |
| `manifest.py` | Locked read-modify-write of the shared manifest |
| `equiv_mutant.py` | Yosys `equiv_make`/`equiv_simple`/`equiv_induct` proof of a survivor against the unmutated core |
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
| `rip` | all 39 tests on a wrapper holding the unmutated core and the mutant | the unmutated core drives the pins; every register, FIFO word and SRAM pin net is compared each cycle |
| `deep` | `test_random`, 256 cases, `PE_SEED=0xD33B2027` | full-suite survivors |
| `suite` | the snapshot's whole suite: the default `COCOTB_TEST_MODULES` of its `test/Makefile` (66 tests in nine modules at c118027), stopping at the first failing module | fast-set survivors (used instead of `full` for the diet4 campaign) |
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
