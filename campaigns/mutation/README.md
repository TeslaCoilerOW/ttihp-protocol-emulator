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
