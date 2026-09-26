# Random-differential campaign (Slurm)

Scales the constrained-random lockstep differential test (`test/test_random.py`,
`test/random_gen.py`) from one seed in CI to thousands of seeds on a Slurm cluster
(MIT Engaging, `mit_preemptable`), at RTL and gate level. Results and findings:
[`docs/verification-campaign.md`](../../docs/verification-campaign.md), section
"Random differential campaign"; merged numbers in [`results/`](results/).

Everything runs against a frozen `git archive` snapshot of one commit, not the
working tree. The stimulus, the lockstep checker and the coverage are the
upstream ones: the campaign imports `harness`, `random_gen` and `model` from the
snapshot's `test/` directory and does not modify them.

| file | role |
|---|---|
| `campaign_random.py` | cocotb test module. For one seed it runs exactly the cases `test_random` runs for `PE_SEED=<seed>` (same `make_case`/`run_case`/`Coverage`), but keeps going after a failing case and writes per-case results and the coverage counters as JSON. Options: `VCAMP_VARIANT` (generator variants below), `VCAMP_XCOV=1` (extended coverage), `PE_INJECT_MODEL_BUG=xor` (negative control), `VCAMP_MINIMIZE=1` (upstream minimizer on failures, also for `PE_REPLAY` cases), `VCAMP_GEN` (generator generation), `PE_VARIANT` (design variant, below) |
| `xcov.py` | extended cross-coverage observer: 71 interaction bins that `random_gen.Coverage` does not track (same-edge FIFO collisions of host, mover and engines; mover arbitration and blocking; trigger modes; event races; synchronous start; STOP/BEGIN/reset of busy engines; FIFO-full levels; concurrency; pin modes; branch outcomes). Reads model state only |
| `build.sh` | compiles `sim.vvp` once per mode with the snapshot's own `test/Makefile` (RTL, or `GATES=yes GL_NETLIST=...`) |
| `run_task.sh` | one array task: `SEEDS_PER_TASK` seeds, `SLURM_CPUS_PER_TASK` in parallel, one `vvp` process per seed against the shared `sim.vvp`; idempotent (skips seeds whose JSON exists), so `--requeue` after preemption only redoes seeds in flight; node-local scratch deleted after each seed |
| `submit.sh` | writes a campaign config and submits the array (`DEPENDENCY=`, `INJECT=xor`, `XCOV=1` optional); records the job id in the shared `manifest.json` |
| `upstream_run.sh` | runs the unmodified upstream `test_random` via `make` for one seed or one `PE_REPLAY` case, on a private copy of the build (fidelity check, reproduction) |
| `merge.py` | merges per-seed JSON, sums the coverage bins, lists holes and failures |
| `report.py`, `report_job.sh` | per-campaign table, merged coverage tables and the xcov table (Markdown), run as a Slurm job |
| `manifest.py` | locked update of the shared campaign manifest |
| `collect.py` | assembles `campaigns.json`, `coverage-all.json` and `report.md` for `results/` from a campaign root's summaries (cluster paths replaced by `<work dir>`) |
| `results/` | `campaigns.json` (per-campaign totals, holes, xcov bins, job ids), `coverage-all.json` (merged coverage of every campaign), `report.md` (generated tables) of the 73536f0 base campaign; `results/diet4/` the same files for design variant `diet4` at c118027 |

Array tasks count against the per-user submit limit (448 jobs on
`mit_preemptable`), so a task is 8 CPUs running 8 single-CPU simulators rather
than one simulator per job.

## Generator variants

`VCAMP_VARIANT` re-weights the upstream generator by wrapping its functions at
import time; generated cases are ordinary `random_gen.Case` objects and can be
replayed by the upstream test with `PE_REPLAY`.

| variant | change |
|---|---|
| `default` | none (identical to `test_random`) |
| `dense` | every program 40..64 words (upstream: mostly 3..24); 85% of host idle operations redrawn, so the host issues about 5x more operations per cycle |
| `faulty` | deliberate-fault rate at least 0.10 per instruction (upstream 0, 0.02 or 0.05); 30% of idles become "revive" (read status, CLEAR faulted engines, restart halted ones) |
| `hostile` | 12% of host operations start an illegal or aborted command sequence upstream never emits (BEGIN with payload, bad COMMIT lengths, STOP/EVENT with absent-engine bits, ROUTE with high bits, program writes outside a load, START before COMMIT, a 65th program word) |
| `deselect` | the upstream trailing `deselect` op (10% of cases), which rarely executes because it sits after the cycle budget, is moved into the first 70% of the host traffic |

Host-traffic length is the upstream `PE_RANDOM_CYCLES`.

Snapshots from c118027 on draw generator generation 2 by default
(`random_gen.GENERATION`), which already moves the trailing deselect into the
traffic. On such a snapshot `deselect` inserts one mid-traffic deselect into
every case that has none, from the same separate random stream; on a
generation-1 snapshot it behaves as described above. `GENERATION=<n>` in the
campaign config (`VCAMP_GEN`) pins the generation on snapshots whose
`make_case` takes it. The result JSON records `generation`.

## Design variants

The campaign runs any design variant of `test/variants.py` (`PE_VARIANT`,
see `test/README.md`, "Design variants"). Every knob defaults to the base
campaign, so the commands below reproduce it unchanged.

- **Build.** Run `build.sh` with `PE_VARIANT=<name>` exported. At RTL it
  compiles `<snapshot>/build/variants/<name>/protocol_emulator_core.v`
  (`scripts/gen_variants.sh <name>` in the snapshot) or `PE_CORE`, and writes
  the core's sha256 next to `sim.vvp`. At gate level the netlist is given as
  before.
- **Tasks.** `DESIGN_VARIANT=<name>` in the environment of `submit.sh` goes into
  the campaign config, and `run_task.sh` exports it as `PE_VARIANT`, so the
  reference model is configured for the variant. The result JSON records
  `design_variant`, `fifo_words` and the model options.
- **Roots.** `RC_ROOT`, `SNAP`, `COMMIT`, `SIMVVP`, `NETLIST`, `MANIFEST`,
  `JOB_PREFIX` and `PARTITION` select another campaign root, snapshot,
  simulation, netlist, manifest, job-name prefix and partition list (see the
  header of `submit.sh`).
- **Merging.** `merge.py` adds the bins that only exist for some runs to the
  hole lists when the merged results say they apply: the two program-word
  bins of generation 2, the byte-lane shift fault (`code 1 from SHR` and the
  byte-lane counter) and the saturated-jump counter of the restricted-ISA
  variants. `report.py --default-label` and the `LABELS`, `DEFAULT_LABEL`,
  `NEGCTL` and `GL_PAIRS` variables of `report_job.sh` name the campaigns.

```sh
# diet4 at c118027 (see docs/verification-campaign.md, "Variant diet4 (6x4) campaigns")
(cd $SNAP && scripts/gen_variants.sh diet4)
PE_VARIANT=diet4 build.sh rtl $SNAP $RC_ROOT/build/rtl
PE_VARIANT=diet4 build.sh gl  $SNAP $RC_ROOT/build/gl $RC_ROOT/netlist/tt_um_teslacoilerow_protocol_emulator.nl.v
export RC_ROOT SNAP COMMIT=<full sha> DESIGN_VARIANT=diet4 PARTITION=mit_preemptable,mit_normal
submit.sh d4-rtl-default default rtl 2048 0 32 64 2000 900 00:45:00 8 6G 12
RC_ROOT=... LABELS="d4-rtl-default ..." DEFAULT_LABEL=d4-rtl-default NEGCTL=d4-negctl-xor GL_PAIRS='{...}' report_job.sh
collect.py $RC_ROOT results/diet4 --commit <full sha> --netlist <netlist> --negctl d4-negctl-xor --labels ...
```

## Reproducing

```sh
# build once (on a compute node)
build.sh rtl <snapshot> <build>/rtl
build.sh gl  <snapshot> <build>/gl <netlist.nl.v>
# a campaign: label variant mode nseeds offset seeds/task cases cycles timeout time cpus mem throttle
submit.sh rtl-default default rtl 4096 0 64 64 2000 900 00:45:00 8 4G 24
XCOV=1 submit.sh rtl-xcov-hostile hostile rtl 256 $((0x500100)) 32 64 8000 3600 01:30:00 8 8G 3
# merge (on a compute node; thousands of small files)
report_job.sh
# one seed with the unmodified upstream test (prints the same coverage summary)
upstream_run.sh <snapshot> <build>/rtl seed1.log PE_SEED=1
```

Any seed of a `default` campaign reproduces locally with
`cd test && PE_SEED=<seed> make COCOTB_TEST_MODULES=test_random`.
