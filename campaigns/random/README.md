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
| `line_gen.py` | line-unit cases (`VCAMP_VARIANT=line`, `line-dense`, `line-faulty`; design variants with the line unit only): line-unit programs mixed with the upstream instruction mix, a pad environment (`LineEnvironment`) and upstream host traffic; the seeded model defects of the line-unit negative controls (`INJECT=line-crc`, `line-carry`). See "Line-unit campaigns" |
| `results/` | `campaigns.json` (per-campaign totals, holes, xcov bins, job ids), `coverage-all.json` (merged coverage of every campaign), `report.md` (generated tables) of the 73536f0 base campaign; `results/diet4/` the same files for design variant `diet4` at c118027; `results/diet8_rec16/` for the line-unit variant `diet8_rec16` (with `lcov`, the line-unit bins, and `rtl-defect-*.diff`, the RTL copies of two negative controls) |

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

## Line-unit campaigns

For a design variant with the line unit (`diet8_rec16`, docs/extension.md) the
driver also runs line-unit cases. `VCAMP_VARIANT=line` makes every case with
`line_gen.make_line_case(seed, index, config, cycles)`: engines get line-unit
programs (LTIM with edge values of P, Q and D; LCFG with every line code,
stuffing polarity and run length, pair, arbitration monitor, SE0 end and
initial level; CRC set/read/preset; LSTAT; line XFERs of 1 to 32 bits that
drive, sample or both, with and without the CRC bit; classic XFERs with the
CRC bit; invalid encodings) mixed with the upstream instruction mix, or (15%)
upstream programs; the pads are driven by `LineEnvironment` (toggling pads,
delayed copies, inversions and wired-AND of other pads, SE0 episodes, and line
transmitters that send stuffed NRZ/NRZI frames with a pair pin and an SE0
end); the host traffic is `random_gen.host_op` (including ROUTE for the mover
and reloads) plus line-program reloads and the generation-2 additions of
`random_gen.extend_case`. `line-dense` draws longer programs with more line
XFERs and fewer host idles; `line-faulty` raises the rate of invalid encodings.
A line case is a `random_gen.Case` with an `env` field (`LineCase`); a failing
one is saved as JSON and replays with `PE_REPLAY` through this driver.

On such a variant the driver attaches `test/model/line_coverage.py`
(`VCAMP_LCOV=1`, the default; `LCOV=0` in `submit.sh` turns it off): the
line-unit bins and a decode check that classifies every line-unit instruction
issue as valid or invalid from the text of docs/isa.md and counts a
disagreement with the fault that the DUT and the model produced. The result
JSON then has `lcov`; `merge.py` adds the hole list `lcov` and `report.py` a
per-bin table (both need the snapshot's `test/` on `PYTHONPATH`, which
`report_job.sh` sets from `SNAP`).

Negative controls: `INJECT=line-crc` (the model's LSB-first CRC step takes its
feedback from bit 1) and `INJECT=line-carry` (the model's ticker ignores the
fraction) seed a defect into the model; `EXPECT_FAIL=1` marks a campaign whose
`SIMVVP` simulates an RTL copy with a seeded defect (failures expected, no case
files saved). `report_job.sh` writes `negctl-<label>-detection.json` for the
labels in `MORE_NEGCTL`, and `collect.py --more-negctl` adds them to
`campaigns.json`.

`SIM_BIN=<dir>` in the environment of `build.sh`, `submit.sh` and
`upstream_run.sh` puts that iverilog/vvp first on `PATH` (the Icarus 13.0 of the
Tiny Tapeout actions for `results/diet8_rec16/`).

The commands of `results/diet8_rec16/` (branch `eval/diet8-rec16` at
f9e0bf9, whose `src/` carries the variant core; docs/extension.md section 12).
`results/diet8_rec16/campaigns.json` records each campaign's configuration and
Slurm array.

```sh
export RC_ROOT SNAP COMMIT=<full sha> DESIGN_VARIANT=diet8_rec16 SIM_BIN=<icarus 13 bin>
export PARTITION=mit_preemptable,mit_normal NL=<netlist of the official gds action>
PE_VARIANT=diet8_rec16 PE_CORE=$SNAP/src/protocol_emulator_core.v build.sh rtl $SNAP $RC_ROOT/build/rtl
PE_VARIANT=diet8_rec16 build.sh gl $SNAP $RC_ROOT/build/gl $NL
# label variant mode nseeds offset seeds/task cases cycles timeout time cpus mem throttle
submit.sh r16-rtl-line line rtl 4096 0 64 64 6000 1500 02:00:00 8 8G 20
submit.sh r16-rtl-line-dense line-dense rtl 256 $((0x300000)) 32 64 6000 1500 02:00:00 8 8G 4
submit.sh r16-rtl-line-faulty line-faulty rtl 256 $((0x400000)) 32 64 6000 1500 02:00:00 8 8G 4
submit.sh r16-rtl-line-long line rtl 256 $((0x100000)) 32 16 50000 3600 02:00:00 8 8G 4
submit.sh r16-rtl-default default rtl 4096 0 64 64 2000 900 01:30:00 8 6G 12
XCOV=1 submit.sh r16-rtl-xcov-default default rtl 1024 $((0x1000)) 64 64 2000 900 01:30:00 8 6G 4
submit.sh r16-rtl-long default rtl 256 $((0x100000)) 16 64 20000 3600 02:00:00 8 8G 8
XCOV=1 submit.sh r16-rtl-xcov-dense dense rtl 256 $((0x300100)) 32 64 8000 2400 02:30:00 8 8G 4
XCOV=1 submit.sh r16-rtl-xcov-faulty faulty rtl 256 $((0x400100)) 32 64 8000 2400 02:30:00 8 8G 4
XCOV=1 submit.sh r16-rtl-xcov-hostile hostile rtl 256 $((0x500000)) 32 64 8000 2400 02:30:00 8 8G 4
XCOV=1 submit.sh r16-rtl-xcov-deselect deselect rtl 256 $((0x600000)) 32 64 2000 1500 02:00:00 8 8G 2
NETLIST=$NL submit.sh r16-gl-line line gl 128 0 16 8 6000 3600 02:00:00 8 8G 1
NETLIST=$NL submit.sh r16-gl-line-extended line gl 256 128 32 16 6000 3600 02:00:00 8 8G 4
NETLIST=$NL submit.sh r16-gl-default default gl 64 0 16 8 2000 3600 01:30:00 8 8G 1
NETLIST=$NL submit.sh r16-gl-extended default gl 256 64 32 32 2000 3600 02:00:00 8 8G 4
NETLIST=$NL submit.sh r16-gl-hostile hostile gl 64 $((0x500000)) 16 16 8000 5400 03:00:00 8 8G 2
NETLIST=$NL submit.sh r16-gl-deselect deselect gl 64 $((0x600000)) 16 16 2000 3600 02:00:00 8 8G 1
# negative controls: two model defects, two RTL copies with a seeded defect, the base campaign's xor
INJECT=line-crc submit.sh r16-negctl-model-crc line rtl 64 0 16 16 6000 1500 01:00:00 8 8G 1
SIMVVP=<crc defect build>/sim.vvp EXPECT_FAIL=1 submit.sh r16-negctl-rtl-crc line rtl 64 0 16 16 6000 1500 01:00:00 8 8G 1
SIMVVP=<carry defect build>/sim.vvp EXPECT_FAIL=1 submit.sh r16-negctl-rtl-carry line rtl 64 0 16 16 6000 1500 01:00:00 8 8G 1
INJECT=xor submit.sh r16-negctl-xor default rtl 64 0 16 64 2000 900 01:00:00 8 6G 1
# merge and collect
SNAP=$SNAP LABELS="r16-rtl-line ... r16-gl-deselect" DEFAULT_LABEL=r16-rtl-default NEGCTL=r16-negctl-xor \
  MORE_NEGCTL="r16-negctl-model-crc r16-negctl-rtl-crc r16-negctl-rtl-carry" GL_PAIRS='{...}' report_job.sh
PYTHONPATH=$SNAP/test collect.py $RC_ROOT results/diet8_rec16 --commit <full sha> --netlist $NL --labels ... \
  --negctl r16-negctl-xor --more-negctl r16-negctl-model-crc r16-negctl-rtl-crc r16-negctl-rtl-carry
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
