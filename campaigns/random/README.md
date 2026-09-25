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
| `campaign_random.py` | cocotb test module. For one seed it runs exactly the cases `test_random` runs for `PE_SEED=<seed>` (same `make_case`/`run_case`/`Coverage`), but keeps going after a failing case and writes per-case results and the coverage counters as JSON. Options: `VCAMP_VARIANT` (generator variants below), `VCAMP_XCOV=1` (extended coverage), `PE_INJECT_MODEL_BUG=xor` (negative control), `VCAMP_MINIMIZE=1` (upstream minimizer on failures, also for `PE_REPLAY` cases) |
| `xcov.py` | extended cross-coverage observer: 71 interaction bins that `random_gen.Coverage` does not track (same-edge FIFO collisions of host, mover and engines; mover arbitration and blocking; trigger modes; event races; synchronous start; STOP/BEGIN/reset of busy engines; FIFO-full levels; concurrency; pin modes; branch outcomes). Reads model state only |
| `build.sh` | compiles `sim.vvp` once per mode with the snapshot's own `test/Makefile` (RTL, or `GATES=yes GL_NETLIST=...`) |
| `run_task.sh` | one array task: `SEEDS_PER_TASK` seeds, `SLURM_CPUS_PER_TASK` in parallel, one `vvp` process per seed against the shared `sim.vvp`; idempotent (skips seeds whose JSON exists), so `--requeue` after preemption only redoes seeds in flight; node-local scratch deleted after each seed |
| `submit.sh` | writes a campaign config and submits the array (`DEPENDENCY=`, `INJECT=xor`, `XCOV=1` optional); records the job id in the shared `manifest.json` |
| `upstream_run.sh` | runs the unmodified upstream `test_random` via `make` for one seed or one `PE_REPLAY` case, on a private copy of the build (fidelity check, reproduction) |
| `merge.py` | merges per-seed JSON, sums the coverage bins, lists holes and failures |
| `report.py`, `report_job.sh` | per-campaign table, merged coverage tables and the xcov table (Markdown), run as a Slurm job |
| `manifest.py` | locked update of the shared campaign manifest |
| `results/` | `campaigns.json` (per-campaign totals, holes, xcov bins, job ids), `coverage-all.json` (merged coverage of every campaign), `report.md` (generated tables) |

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
