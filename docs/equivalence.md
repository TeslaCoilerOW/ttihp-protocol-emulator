# RTL-to-netlist equivalence

This page records a formal check that the submitted gate-level netlists
implement their RTL, and the controls that show the check can fail. It also
records two findings made on the way:

- a defect in the first version of the check;
- the cause of optimizer promotion p026's gate-level failure: its netlist is
  equivalent to its RTL, and the failure is a race in the zero-delay
  simulation.

The tool is [`formal_eq/`](../formal_eq/README.md). That README gives the
method, the controls and the exit codes in full. The work directory on the
cluster is written `<work dir>` below; the job records are in
`<work dir>/eq-repo/manifest.json`.

**Setup.**

- **Tools.** OSS CAD Suite 2026-07-29 (Yosys 0.67+111 and its ABC), the
  release `formal.yaml` pins.
- **PDK.** IHP-Open-PDK `2bbec755`, the revision in the artifacts'
  `pdk.json`. The typical liberty file has sha256 `34163f3e…`.
- **Arithmetic.** The sums, differences and estimates on this page were
  checked with AXLE (Lean 4, `<work dir>/eq-repo/axle/`, `okay: true`).
- **Where.** Slurm jobs on the MIT Engaging cluster, run from this
  repository at `45de462` with `formal_eq/` not yet committed. Each
  `result.json` records the sha256 of every tool file (`tool_files`). All
  runs in section 1 except mutB used the same tool files (mutB: see "A
  first round" below).

## 1. Results

| Netlist | Source | RTL | Verdict | ABC | Job |
|---|---|---|---|---|---|
| 6x4 submission | `gds_6x4` run [36298635404](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36298635404), commit `d76f1cc` | `diet4` | **equivalent**, self-test passed | 67 s | 24093883 |
| 8x4 submission, 15 ns | `gds` run [36298635436](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36298635436), commit `d76f1cc` | `base` | **equivalent**, self-test passed | 692 s | 24093884 |
| 8x4, 20 ns | `gds` run [36257636798](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36257636798), commit `131e793` | `base` | **equivalent**, self-test passed | 349 s | 24093885 |
| p018, local full run | the optimizer's run directory (`out/final/nl`, checked against `SHA256SUMS`) | `base` | equivalent | 446 s | 24093888 |
| p025 | promotion netlist | `diet4` | equivalent | 69 s | 24093887 |
| p026 | promotion netlist | `diet4` | equivalent | 71 s | 24093886 |
| 6x4 submission, with the CI proposal's PDK | as 36298635404, with the PDK root made by the proposal's sparse checkout | `diet4` | equivalent, self-test passed | 69 s | 24093889 |
| mutB (one gate of p014 changed) | the investigation's mutant | `diet4` | **undecided** (exit 3) | 1,710 s | 24093081 |

**Which netlists these are.**

- **Official artifacts.** The three netlists are each artifact's
  `tt_submission/tt_um_teslacoilerow_protocol_emulator.v`.
- **6x4.** The 6x4 artifact's netlist (sha256 `f085554e…`) is byte-identical
  to the netlist of promotion p014, the configuration the 6x4 overlay
  carries.
- **8x4, 15 ns.** The artifact's netlist (`09841e31…`) is byte-identical to
  the decompressed final netlist of p018's local full run. The two checks
  built the same miter (AIG sha256 `df863faf…`).
- **Sources.** In each artifact, `src/project.v` and
  `src/protocol_emulator_core.v` equal the RTL the check used
  (`inputs.artifact_src_matches`: all true).
- **PDK revision.** The PDK revision at the check equals each artifact's
  `pdk.json` (`inputs.pdk.matches_netlist`: true).

**Self-test.** With `--selftest`, three more things run next to the check,
and all must meet their expectation:

- two mutants of the netlist under test, which must be `not_equivalent`;
- the nine recipe controls of `formal_eq/controls/`.

| Artifact | mutA (nand2 → nor2 nearest to an output) | mutC (`A_DIN[3]` ↔ `A_DIN[2]` of `core.instruction_sram_e0_hi`) | Recipe controls |
|---|---|---|---|
| 6x4, 36298635404 | `_32857_`, 4 levels from `uo_out`/`uio_out`: not equivalent, frame 8, 0.2 s | not equivalent, frame 6, 0.3 s | 9 of 9 as expected |
| 8x4 15 ns, 36298635436 | `_31308_`, 6 levels: not equivalent, frame 44, 588 s (by interpolation) | not equivalent, frame 4, 0.6 s | 9 of 9 |
| 8x4 20 ns, 36257636798 | `_35237_`, 5 levels: not equivalent, frame 6, 0.3 s | not equivalent, frame 4, 0.3 s | 9 of 9 |

**Size and cost.**

- **Miter size.** The 6x4 miters have 147 inputs, 5,139 latches and
  124,305–124,314 AND nodes. The 8x4 miters have 147 inputs, 7,795 latches
  and 141,265–141,319 AND nodes. The 147 inputs are the 19 Tiny Tapeout
  input bits plus 128 SRAM read-data bits (8 macros of 16 bits).
- **Wall time.**
  - The 6x4 checks took 92–97 s of wall time on AMD EPYC 9654 nodes, with
    or without the self-test.
  - The 8x4 checks with self-test took 389 s (20 ns) and 755 s (15 ns) on
    AMD EPYC 9474F nodes.
  - The 15 ns check ran its netlist and its slow mutA side by side, and its
    ABC took 692 s. The same miter took 446 s alone on an AMD EPYC 7513 node
    (job 24093888).
- **Memory.** The largest single process peaked at 1,297,848 kB (about
  1.3 GB).

**A first round.** Jobs 24093075 to 24093081 ran the same cases with an
earlier version of the tool.

- **The 8x4 mutA case.** Its verdict parser did not know the upper-case
  spelling `Networks are NOT EQUIVALENT.`, which `dprove` prints when
  interpolation finds the counterexample. So the 15 ns 8x4 mutA case came
  out as `error`. That is a failure, not a pass, and the self-test was
  marked failed.
- **The fix and round 2.** The parser was corrected (`formal_eq/README.md`,
  "Method", step 6, lists the spellings), and every case was run again
  (round 2, the jobs in the table).
- **mutB.** Its long run (24093081) is from that first round and was not
  repeated. The corrected tool builds a byte-identical miter AIG for it
  (`c330c0ef…`, build job 24093890 with a 120 s ABC limit), so ABC was given
  the same problem.

## 2. What is proven

For each netlist, starting from reset (`rst_n` held low in the first cycle,
every flip-flop at 0), for every input sequence and every sequence of SRAM
read data, the following are equal in every cycle to those of the RTL:

- the Tiny Tapeout outputs;
- every input pin of the 8 SRAM macros.

The macros are cut points: their read data is one free input shared by both
sides. Cell functions come from the liberty file.

LVS already ties the layout to this netlist. This check ties the netlist to
the RTL. Together they cover the path from RTL to layout, except for the
items in section 6. The gate-level cocotb suite runs the same netlist, but
only on its test vectors: section 4 has a one-gate change that the suite
passes.

## 3. A defect in the first recipe

The first version of the check was written in the p026 investigation
(`<work dir>/gleq/eq_check.sh`, jobs 24086842, 24087083, 24087438, 24087634,
24087778, 24087807 and 24088368). It built the miter with `miter -equiv -ignore_gold_x` and
later ran `setundef -zero` to remove x constants.

**Why that is wrong.** With `-ignore_gold_x`, yosys compares each output bit
through `gold_x = $eqx(gold_bit, 1'bx)`. `setundef -zero` rewrites that `x`
to 0. The test then becomes `gold_bit == 0`, and every bit where the RTL
output is 0 is ignored. The miter could only fire where the RTL has 1 and
the netlist 0.

**What it did to the results.**

- **"Equivalent" results.** For p014, p018, p025 and p026, "equivalent"
  proved only that no compared bit is ever 1 in the RTL and 0 in the
  netlist.
- **"Not equivalent" results.** The counterexamples for mutA and mutC were
  genuine differences.

The defect was found while packaging the check. It is now covered by
controls. Job 24094085 (`<work dir>/eq-repo/ablation/ablation.json`) ran the
nine controls through three versions of the miter script:

| Miter script | c1 (gate `a\|b`, gold `a`) | c4 (difference only at count 13, where gold = 0) | c8 (gated clock) | c9 (falling edge) | Others |
|---|---|---|---|---|---|
| `formal_eq` as packaged | not equivalent | not equivalent (frame 14) | error | error | as expected |
| with `-ignore_gold_x` (the first recipe) | **equivalent** | **equivalent** | error | error | as expected |
| without the two `select -assert-none` checks | not equivalent | not equivalent | **equivalent** | **equivalent** | as expected |

With the packaged recipe, which has no `-ignore_gold_x`, p014 (as the 6x4
artifact), p018 (as the 8x4 artifact and its local run), p025 and p026 are
equivalent (section 1).

## 4. mutB and why the check fails closed

mutB is promotion p014's netlist with one `nand2_1`, `_30297_`, replaced by
a `nor2_1`. The instance was chosen at random by the investigation's
`mutate.py`, and `diff` shows that one line as the only difference.

- **Sequential check.** With the packaged recipe, ABC `dprove` stopped with
  "Networks are UNDECIDED" after 1,708 s by its own clock (1,710 s wall),
  well inside the 7,200 s limit (job 24093081). The phases:
  - BMC found no failure in 13 frames;
  - latch correspondence and k-step induction up to K = 4 did not close;
  - interpolation was undecided;
  - the final PDR stage reached its 60 s limit in frame 24.
- **Combinational check.** With the flip-flops matched as cut points, ABC
  `cec` finds a state and input in which one flip-flop's next state differs
  between p014 and mutB (job 24091084; netlist against netlist, so the
  defect of section 3 does not apply).
- **Simulation.** The gate-level cocotb suite passes mutB: 102 tests, 46
  pass, 56 skip, 0 fail (job 24088368).

So the change is either unreachable from reset or needs an input sequence
too long for these engines. Neither simulation nor this check can show which.
A gate that treated "undecided" as a pass would therefore accept a netlist
with a changed gate. `eq_check.py` exits 3 for undecided and for a time
limit, and only 0 counts as a pass. Every other netlist in section 1 was
decided within its limit.

## 5. p026: an equivalent netlist that fails zero-delay simulation

p026 (`diet4_6x4` trial #43) had a legal full run with LVS 0 (job
24076184) and passed the precheck (job 24078116). It failed 8 of the 46
gate-level tests that run (job 24078117).

**The netlist is equivalent to its RTL** (job 24093886). So synthesis and
repair did not change its function.

**The failure is a race of the zero-delay simulation.** The evidence is
from the investigation (`<work dir>/gleq/`), except where a `formal_eq` job
is named.

- **Deterministic.** A rerun of the full suite on the same netlist gave 38
  pass, 56 skip and 8 fail, the same 8 tests (job 24085496).
- **Only the SRAM clocks matter.**
  - With every clock pin rewired to the `clk` port, the suite passes: 46
    pass, 0 fail (job 24085496). The clock tree is buffers only, so the
    logic is unchanged.
  - With only the 8 SRAM `A_CLK` pins rewired, it also passes, 46 and 0
    (job 24086215).
  - With only the flip-flop clock pins rewired, 42 tests fail (job
    24086215).
- **Where.** A race monitor compares every flip-flop and SRAM input at the
  `clk` edge with its value at the sink's own clock edge.
  - In p026 its summary line counts 0 flip-flop and 3,762 SRAM events
    (job 24086215). The first 400 events are printed. 395 of them are
    readable; in the other 5, simulator output is interleaved into the line.
    All 395 are at `core.instruction_sram_e0_lo`, and in each the only input
    that changed is `A_DIN[3]`.
  - It counted none in p014 or p025 (job 24086215), nor in p018 (job
    24088391).
  - The first divergence from the RTL is in cycle 232, a read of that macro
    that returns 0x0036 instead of 0x003e (job 24085806).
- **Mechanism.** The chain was checked with `formal_eq`'s netlist parser
  (`<work dir>/eq-repo/racing_path_p026.txt`).
  - `A_DIN[3]` of `e0_lo` is driven by flip-flop `_37730_` (Q =
    `core._1653[7]`, the host write-data shift register) through three
    cells: `hold9564` (`dlygate4sd3_1`), `place7763` (`buf_1`) and
    `hold9565` (`dlygate4sd3_1`).
  - That flip-flop's clock passes through 12 buffers.
  - The macro's `A_CLK` passes through 18: 13 `buf_8`, 4 `buf_2` and 1
    `buf_1`.
  - In a zero-delay simulation every cell adds an evaluation step. So the
    new flip-flop value reaches `A_DIN[3]` before the SRAM model's
    `always @(posedge ...)` block reads it, and the macro writes the next
    cycle's bit.
- **Real timing is the other way round.**
  - In p026's post-route clock report for the fast corner, the earliest
    clock arrival is at an SRAM clock pin: 0.796 ns at
    `core.instruction_sram_e1_lo/A_CLK`. The latest is at a flip-flop:
    1.478 ns at `_39725_/CLK` (job 24087092).
  - The run's `metrics.csv` has hold WS +0.114 ns (fast corner, the worst)
    and 0 hold violations.
  - The SRAM liberty has hold arcs on `A_DIN`, so STA checks exactly this
    path.

**The knob.** The clock depths come from the `formal_eq` structure report
(`structure.clock` in `result.json`):

| Netlist | Flip-flop clock depth | SRAM `A_CLK` depth | `CTS_MAX_SLEW` | `CTS_CLK_MAX_WIRE_LENGTH` | Gate level | Job |
|---|---|---|---|---|---|---|
| p014 = 6x4 submission | 5–7 | 9 | unset | 500 | pass | 24093883 |
| p018 = 8x4 submission, 15 ns | 5–6 | 7 | unset | 0 | pass | 24093884 |
| 8x4, 20 ns | 5–6 | 7 | unset | 0 | pass | 24093885 |
| p025 | 11–13 | 15–18 | 0.75 | 250 | pass | 24093887 |
| p026 | 11–13 | 15–18 | 1.2 | 250 | **fail** | 24093886 |

- **Where the gate-level results come from.**
  - 6x4 submission: `gl_test` of run 36298635404, with 102 tests, 46 pass,
    56 skip and 0 fail ([results.md](results.md) R89).
  - p018: local gate-level job 24053972, with 46 pass and 0 fail
    (results.md R84). The `gl_test` job of run 36298635436 also passed,
    with 102 tests, 46 pass, 56 skip and 0 fail (results.md R85).
  - 8x4, 20 ns: `gl_test` of run 36257636798 (results.md R15).
  - p025: promotion job 24073831, with 46 pass and 0 fail.
  - p026: job 24078117.
- **Where the knobs come from.** The values for p025 and p026 are from the
  investigation's survey of the promotions' configurations (job 24086842)
  and p026's `resolved.json`. Those for the three artifacts are from their
  `resolved.json`, which leaves `CTS_MAX_SLEW` unset.
- **Which promotions set the knob.** Only p025 and p026 among the promotions
  set `CTS_MAX_SLEW`. Their clock trees are deeper: every SRAM clock pin is
  2 to 5 buffers deeper than the deepest flip-flop clock pin (15–18 against
  13).
- **The depth alone does not decide the outcome.** p025 has the same depths
  as p026, macro by macro, and passes. Whether a path races depends on the
  simulator's event order on that path. The adopted netlists also have
  their SRAM clocks deeper than their deepest flip-flop clock, by 2 (9
  against 5–7) and by 1 (7 against 5–6), and pass.

**Consequence.** Tiny Tapeout's `gl_test` action runs the same kind of
zero-delay Icarus simulation (the same `test/` suite, Icarus 13), so a layout
like p026 is expected to fail there too; this was inferred from the local
runs, not tried on GitHub. Rejecting it
is correct even though its function and timing are right. Equivalence and
gate-level simulation answer different questions, and both are needed:

- equivalence shows that the netlist computes the RTL;
- the gate-level suite shows that the netlist passes the simulation that
  Tiny Tapeout runs.

[notes/optimization.md](notes/optimization.md) records how the optimizer handles this
case.

## 6. What is not covered

- **The SRAM macros themselves.** They are cut points; their behaviour and
  timing are outside the proof.
- **The liberty functions against the cell layouts.** The check trusts the
  liberty `function` attributes. LVS ties the layout to the netlist, not a
  cell's layout to its liberty function.
- **Timing.** The model advances one clock cycle at a time, so it does not
  see timing, including zero-delay simulation races (section 5). STA and
  the gate-level suite cover those.
- **Asynchronous behaviour.** Asynchronous resets are modelled at
  clock-cycle granularity.
- **Power-up state.** Every flip-flop starts at 0 and the first cycle is a
  reset.
  - The diet4 core resets all its 2,587 register bits asynchronously.
  - The base core has 64 32-bit registers (2,048 bits) with no reset; its
    other 1,877 bits have a synchronous reset. The 2,048 bits start at 0 on
    both sides. Equivalence from other power-up values of those bits is not
    covered.
  - The counts are from Yosys after `proc`, `opt_dff` and `memory_map`
    (`<work dir>/eq-repo/register_bits.txt`).
- **RTL x values** are fixed to 0 on the RTL side. A netlist that chose 1
  would fail the check (a false failure, never a false pass). The diet4 core
  has none.
- **Common-mode front end.** The RTL is read by the same Yosys front-end
  family that LibreLane uses for synthesis.
- **Proof certificates.** ABC is trusted; no proof certificate is checked.

## 7. CI

[`.github/workflows/equiv.yaml`](../.github/workflows/equiv.yaml) (workflow
`equivalence`) runs `eq_check.py check --selftest` on GitHub. It checks the
`tt_submission` artifact of every `gds` and `gds_6x4` run of `main` whose
`gds` job succeeded, and of any such run given by hand (builds of other
branches, such as a variant under evaluation, are checked by hand). Its steps, pins and hardening
are listed in the CI section of
[formal_eq/README.md](../formal_eq/README.md#ci). It replaced the inactive
proposal `formal_eq/ci-proposal.yaml` (removed in `a15f6f2`), whose sparse
PDK checkout the section 1 row "with the CI proposal's PDK" used.

This section was first written before the workflow had run on GitHub. The
design, the static checks, the emulation on the cluster and the runtime
estimate below are from then. Since then the workflow has checked on
GitHub the official netlists of `d76f1cc` and every later build of `main`
that finished after it was added ("Runs on GitHub", at the end of this
section).

**Design.**

- **Which runs.** Run 36298635436 ended `failure` although its `gds`,
  `precheck` and `gl_test` jobs succeeded: only its `viewer` job, which
  needs GitHub Pages, failed. So the workflow reads the `gds` job's own
  conclusion from the API and does not use the run's conclusion. It also
  requires an unexpired `tt_submission` artifact with a digest.
- **Which tool.** `formal_eq/` comes from the workflow's own commit, and
  the built commit supplies only `src/` and `variants6x4/`. After
  `workflow_run`, the workflow file and `formal_eq/` both come from the
  default branch, so a pushed branch cannot change the check applied to its
  netlist. Commits older than `formal_eq/` can be checked too. `d76f1cc`,
  the commit of both official artifacts, is one of them.
- **Which PDK.** Only the typical liberty file of the `pdk.json` revision is
  fetched. Its sha256 must match `LIBERTY_PINS`, so a new PDK revision needs
  a new pin. No `SOURCES` file is written: `eq_check.py` reads the revision
  from the checkout's HEAD.
- **Which tools.** The workflow downloads the OSS CAD Suite 2026-07-29
  archive itself and checks it against a pinned sha256 (`89ea1152…`, the
  digest GitHub reports for the release asset). The archive downloaded in
  the emulation matched it.
- **Parallelism.** It uses `--jobs 2`. With `--selftest` on the 15 ns 8x4
  netlist, both the netlist and mutA are long ABC runs, and they run side by
  side.

**Static checks.** `actionlint` 1.7.12 found nothing, with shellcheck 0.11.0
on `PATH`. shellcheck 0.11.0 with every optional check (`-o all`) at
severity warning was also run on each extracted `run:` script (at the
default severity it also lists style notes SC2250, SC2292 and SC2312). It
reported only SC2154 (variables that `env:`
sets, which actionlint also disables) and one missing default `case`
branch, which was then added. No `run:` script contains `${{`.

**Emulation.** `<work dir>/eq-ci/emulate.py` runs the workflow file on the
cluster:

- Every `run:` script runs verbatim with
  `bash --noprofile --norc -eo pipefail`, the command `shell: bash` gives on
  a runner.
- The `${{ }}` expressions of `env:`, `with:`, `if:`, `outputs:` and job
  names are evaluated from an event built from the API. For
  `workflow_dispatch`, the event is built from the input.
- `GITHUB_OUTPUT`, `GITHUB_ENV` and `GITHUB_STEP_SUMMARY` are read back
  after each step.
- `actions/checkout` is replaced by the equivalent git commands (sparse
  checkout, depth-1 fetch of the commit), and `actions/upload-artifact` by a
  copy.
- `github.token` is the operator's `gh` token.
- `RUNNER_TEMP` is node-local; the workspace is on the cluster file system.

Each case ran in a clean directory. The job ids are in
`<work dir>/eq-ci/manifest.json`. Both official artifacts pass:

| Case | Event | Slurm job | CPU | Steps before the check | Check step | Check job | Result |
|---|---|---|---|---|---|---|---|
| 6x4, `gds_6x4` run 36298635404 | `workflow_run` | 24119482 | AMD EPYC 9474F, 4 Slurm CPUs (8 threads) | 22.6 s (OSS CAD Suite 17.8 s) | 86.1 s | 109.0 s | equivalent, self-test passed, every gate passed |
| 8x4, `gds` run 36298635436 | `workflow_dispatch` | 24119483 | AMD EPYC 7542, 4 Slurm CPUs (4 threads) | 28.5 s (OSS CAD Suite 22.6 s) | 1,032.7 s | 1,061.4 s | equivalent, self-test passed, every gate passed |

In both cases the `select` job took 1.0 s. The unit tests ran all 28 tests,
including the yosys recipe controls, in under 1 s.

- **6x4.** ABC proved the netlist in 61.9 s. mutA and mutC were refuted at
  frames 8 and 6 in 0.2 s each.
- **8x4.** ABC proved the netlist in 693.0 s, on the same miter as
  section 1 (AIG sha256 `df863faf…`). mutA was refuted at frame 44 in
  958.5 s, and mutC at frame 4 in 0.4 s.
- **Critical path.** In the 8x4 case, mutA is the critical path of the
  check. On the EPYC 9474F of section 1 it took 588 s.
- **Summary.** The job summary had the verdict, the self-test, the ABC time,
  the netlist sha256, the commit, the run link, the artifact digest, the
  PDK and the tool commit.
- **Upload.** The uploaded files came to 6.2 MB for the 8x4 case, before
  compression.

**Negative cases.** Each of these ended with the job failing, at the step
named. The `select` cases ran on the login node, since they only call the
API:

| Case | Where it stopped |
|---|---|
| `run_id` `36298635436; id`, `$(id)`, or with an appended line `go=true` | `select`: not a run id |
| `run_id` of a `test` run (36323174079) | `select`: not a run of `gds.yaml` or `gds_6x4.yaml` |
| `gds_6x4` run 36257636751, whose `gds` job failed, by `workflow_dispatch` | `select`: the `gds` job did not succeed |
| cancelled `gds` run 36120104740, by `workflow_dispatch` | `select`: the `gds` job was cancelled |
| `workflow_run` event whose `head_sha` differs from the API | `select`: the event and the API disagree |
| `workflow_run` event whose `path` carries shell text | `select`: not `gds.yaml` or `gds_6x4.yaml` |
| variant `diet4` forced for the 8x4 run | `check`: the artifact's core differs from `variants6x4/` |
| wrong artifact digest | `check`: download |
| wrong liberty pin | `check`: liberty fetch |
| `pdk.json` revision not pinned | `check`: `pdk.json` check |
| `head_sha` of another commit | `check`: `commit_id.json` check |
| `run_id` `1;id` passed to `check` | `check`: selection validation |
| wrong OSS CAD Suite digest | `check`: OSS CAD Suite install |

The seven `check` cases (Slurm job 24119484) replace a `select` output or a
pin inside the emulator. They show that `check` does not rely on `select`
alone. In every one, the summary and upload steps still ran. Two cases skip
without failing:

- After `workflow_run`, run 36257636751 was skipped: `select` succeeded with
  `go=false`, and `check` did not run.
- After `workflow_run`, run 36120104740 was skipped by the `select` job's
  `if:`.

**What the emulation does not cover.**

- whether GitHub fires `workflow_run` (only for a workflow file on the
  default branch);
- the permissions and the concurrency queue;
- the two actions themselves;
- runner start-up and the runner's CPU.

The emulation used `jq` 1.6 and `gh` 2.95.0. The runner image has its own
versions.

**Runtime on GitHub (estimate, made before the first run).** For 8x4, ABC
takes nearly all of the time, and its time depends on the CPU and on what
else runs on it:

- The 8x4 netlist took 446 s alone on an EPYC 7513 (job 24093888).
- It took 692 s next to mutA on an EPYC 9474F (job 24093884).
- It took 693 s next to mutA on an EPYC 7542 (job 24119483, the emulation).
- It took 1,006 s alone on a Xeon Gold 6230 (job 24095479).

All four runs used the same miter (AIG sha256 `df863faf…`).

The public-repository `ubuntu-24.04` runner has 4 vCPUs, and `--jobs 2`
keeps two ABC runs on them. Suppose each step on the runner takes between
as long as in the emulation and twice as long. Then the check job would
take 109–218 s for 6x4 and 1,061–2,123 s (18–35 min) for 8x4, plus runner
start-up and the download of the 737 MB OSS CAD Suite archive. The limits
are well above that:

- 3,600 s per ABC run, more than 3.5 times the longest ABC run measured on
  an official netlist or its self-test mutants (1,006 s);
- 120 min per `check` job, more than 6 times the emulated 8x4 job;
- both well under GitHub's 6 h job limit.

A run that reaches the ABC limit ends `timeout` (exit 3), and the job
fails.

The sums, ratios and roundings in this section were checked with AXLE
(Lean 4, `<work dir>/eq-ci/axle/`, `okay: true`).

**Activation.** A workflow runs on GitHub only once it is on the default
branch; this one reached `main` with `a15f6f2` (pushed 2026-09-27 19:35
UTC). `workflow_run` then fires for the `gds` and `gds_6x4` runs that
complete afterwards. `127e8e8` limited `workflow_run` to builds of `main`;
a build of another branch is checked with `workflow_dispatch`. The two
official builds of `d76f1cc` had finished before, so they were checked by
hand:

```sh
gh workflow run equiv.yaml -f run_id=36298635404   # gds_6x4, 6x4 (diet4)
gh workflow run equiv.yaml -f run_id=36298635436   # gds, 8x4 (base)
```

**Runs on GitHub.** Every run that had finished by 2026-09-28 08:40 UTC.
The builds of `main` that finished between `d76f1cc`'s and the arrival of
the workflow were not checked: `gds` 36308043760 and `gds_6x4` 36308043804
of `4c30622`, and `gds_6x4` 36323174037 of `fff6746` (the same `src/` as
`d76f1cc`).
Each verdict is from the job's log, where the check prints
`{"verdict": "equivalent", "exit_code": 0, "pass": true, "wall_s": …}`,
and the built run is the one the `select` step names. The job passes only
when the self-test passes too, so every row below had its two netlist
mutants refuted and its nine recipe controls met. Job time runs from the
check job's start to its end as GitHub reports them; wall time is
`eq_check.py`'s own (`wall_s`). The results are also R93 in
[results.md](results.md).

| Built commit | Build run | Variant | Equivalence run | Event | Verdict | Check job | Wall time |
|---|---|---|---|---|---|---|---:|
| `d76f1cc` | `gds_6x4` [36298635404](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36298635404) | `diet4` | [36344927204](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36344927204) | `workflow_dispatch` | equivalent, self-test passed | 2 min 41 s | 119.3 s |
| `d76f1cc` | `gds` [36298635436](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36298635436) | `base` | [36344929368](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36344929368) | `workflow_dispatch` | equivalent, self-test passed | 10 min 16 s | 583.4 s |
| `fff6746` | `gds` 36323174075 | `base` | [36351319648](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36351319648) | `workflow_run` | equivalent, self-test passed | 15 min 5 s | 873.6 s |
| `a15f6f2` | `gds_6x4` 36344917858 | `diet4` | [36353833650](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36353833650) | `workflow_run` | equivalent, self-test passed | 2 min 45 s | 124.4 s |
| `a15f6f2` | `gds` 36344917852 | `base` | [36368252100](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36368252100) | `workflow_run` | equivalent, self-test passed | 10 min 2 s | 568.5 s |
| `127e8e8` | `gds_6x4` 36360078870 | `diet4` | [36369225191](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36369225191) | `workflow_run` | equivalent, self-test passed | 2 min 32 s | 112.9 s |
| `65cb65c` | `gds_6x4` 36369316684 | `diet4` | [36379351458](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36379351458) | `workflow_run` | equivalent, self-test passed | 2 min 41 s | 122.8 s |
| `127e8e8` | `gds` 36360078852 | `base` | [36382409490](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36382409490) | `workflow_run` | equivalent, self-test passed | 14 min 6 s | 818.0 s |
| `76a81f5` | `gds` 36360006594 | `base` | [36391888473](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36391888473) | `workflow_run` | equivalent, self-test passed | 14 min 52 s | 860.4 s |
| `76a81f5` | `gds_6x4` 36360006612, cancelled by a newer push | – | [36360097584](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36360097584) | `workflow_run` | skipped: "the gds job of run 36360006612 concluded 'cancelled', so there is no netlist to check" | – | – |

**Measured runtime against the estimate.** The 8x4 check jobs took 10 min
2 s to 15 min 5 s, less than the estimate of 18 to 35 min. The 6x4 check
jobs took 2 min 32 s to 2 min 45 s, inside the estimate of 109 to 218 s
plus start-up. Every job ended far inside its 120 min limit.

## 8. Reproducing

```sh
gh run download <run id> -R TeslaCoilerOW/ttihp-protocol-emulator -n tt_submission -D <dir>
export OSS_CAD_SUITE=<OSS CAD Suite 2026-07-29> PDK_ROOT=<root holding ihp-sg13cmos5l/ at 2bbec755>
python3 formal_eq/eq_check.py check --run-dir <dir> --variant diet4 --selftest --out <out>   # gds_6x4 runs
python3 formal_eq/eq_check.py check --run-dir <dir> --variant base  --selftest --out <out>   # gds runs
```

On the cluster, `formal_eq/slurm_job.sh` takes the same arguments (4 CPUs,
12 GB, 1 h). The runs in section 1 used `-p mit_quicktest` for the 6x4 cases
and `-p mit_preemptable,mit_normal --requeue` for the 8x4 cases.
