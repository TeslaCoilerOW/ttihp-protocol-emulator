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
    (results.md R84). The `gl_test` job of run 36298635436 also concluded
    "success"; its log was not read, because the run was still in progress.
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

[optimization.md](optimization.md) records how the optimizer handles this
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

## 7. CI evaluation

[`formal_eq/ci-proposal.yaml`](../formal_eq/ci-proposal.yaml) is a proposed
workflow. It is not in `.github/workflows/` and has not run on GitHub. It
passes `actionlint` 1.7.12 without findings (shellcheck was not
available).

**What it needs.**

- **The artifact.** After a `gds` or `gds_6x4` run (`workflow_run`), or
  given a run id, it downloads that run's `tt_submission` artifact
  (`actions/download-artifact` with `run-id`, `actions: read`) and checks
  out the commit that was built.
- **The exact PDK.**
  - The revision is the artifact's `pdk.json` `PDK_VERSION`.
    `TinyTapeout/tt-gds-action@ihp-cmos5l` installs `ihp-sg13cmos5l` with
    `install_sg13cmos5l.sh`: a shallow fetch of IHP-Open-PDK at a pinned
    commit (`2bbec755`), plus a `SOURCES` line.
  - The proposal fetches only
    `ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/` of the `pdk.json`
    revision, by partial clone (`--filter=blob:none`, depth 1) and sparse
    checkout. It then writes the same `SOURCES` line and caches the result
    by revision.
  - On the cluster login node these commands took 1 s and left 13 MB. The
    typical liberty file was byte-identical to the flow's (`34163f3e…`).
    The job 24093889 above used that sparse checkout as its PDK root.
  - `eq_check.py` refuses a PDK whose revision differs from `pdk.json`.
- **Tools.** `YosysHQ/setup-oss-cad-suite@v4` with release 2026-07-29 (as
  `formal.yaml`), for yosys and yosys-abc. Python 3 needs only its standard
  library.
- **Fail closed.** The job fails unless all of the following hold:
  - `eq_check.py` exits 0 (equivalent, every control as expected);
  - the artifact's `src/` equals the checked-out RTL;
  - the unit tests pass.

**Runtime.** This has not been measured on GitHub; the estimate is from the
cluster runs above.

- **CPU basis.**
  - ABC is single-threaded. `--jobs 2` puts the netlist and mutA on the two
    physical cores of a standard 4-vCPU runner.
  - On the cluster, the 6x4 check with self-test took 96–97 s (EPYC 9654).
  - The 8x4 checks with self-test took 389–755 s (EPYC 9474F). The plain
    8x4 check took 483 s (EPYC 7513).
- **Estimate.** Assume a runner core runs these steps between as fast as
  and twice as slowly as the cluster cores, plus about 2 min for setup
  (tool download, PDK, artifact). A 6x4 job would then take about 3.5–5.5
  min and an 8x4 job about 8.5–27 min.
- **Limits.** The job limit (60 min) and the ABC limit (2,700 s per case)
  leave room above that estimate.
- **Cost of the self-test.** The 15 ns 8x4 mutA took 588 s to refute (frame
  44, by interpolation). That adds 588 s of CPU time to the 692 s of the
  check itself, but little wall time, because the two run side by side.
  Without `--selftest`, the job still runs the unit tests, which include the
  recipe controls.

**Not decided here.** Whether to add the workflow is a repository decision.
A job that follows `gds` does not lengthen the `gds` job itself, which is
the one near GitHub's 6 h limit.

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
