# formal_eq: RTL-to-netlist sequential equivalence

`eq_check.py` proves that a gate-level netlist of the Tiny Tapeout top
`tt_um_teslacoilerow_protocol_emulator` produces the same outputs as the RTL
it was built from (`src/project.v` plus the core of the chosen variant), cycle
by cycle from reset, under the assumptions listed in "What is not covered"
below (the SRAM macros are cut points, cell functions come from the liberty
file, and registers without reset start at 0). The netlist
can come from a `tt_submission` artifact of the `gds` or `gds_6x4` workflow,
or from a LibreLane run. Results, the job ids behind them and what they mean
for the submission are in [docs/equivalence.md](../docs/equivalence.md).

LVS compares the layout with the netlist, and the gate-level cocotb suite
runs the netlist on the test vectors. Neither compares the netlist with the
RTL for all inputs. This check does, from the netlist that LibreLane writes
after routing and filler insertion.

## What is proven

Take the RTL (gold) and the netlist (gate), each inside `wrap.v`, which holds
`rst_n` low in the first cycle. Every flip-flop on both sides starts at 0.
Then, for every input sequence on `ui_in`, `uio_in`, `ena` and `rst_n`, and
for every sequence of SRAM read data, the following are equal in every cycle
on the two sides:

- the Tiny Tapeout outputs `uo_out`, `uio_out` and `uio_oe`;
- every input pin of each of the 8 `RM_IHPSG13_1P_64x16_c2` macros:
  `A_CLK`, `A_MEN`, `A_WEN`, `A_REN`, `A_ADDR`, `A_DIN` and `A_DLY`.

After the first cycle, `rst_n` is a free input, so later resets are covered
too.

**The SRAM macros are cut points.** Each macro is removed from both sides
(`expose -evert`). Its inputs become compared outputs, and its read data
`A_DOUT` becomes one free input shared by the two sides. The same macro
receiving the same inputs returns the same data, so equivalence for arbitrary
shared read data implies equivalence with the real macros. The free read data
over-approximates what a macro can return. So a difference that needs
impossible read data is reported as "not equivalent". That errs on the side
of failing, never of passing.

**Cell semantics** come from the `function` and `ff` attributes of the PDK's
typical-corner liberty file,
`ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib`
(`read_liberty`, not `-lib`). The PDK revision must be the one in the
artifact's `pdk.json`.

**Exit status.** The check fails closed: only 0 is a pass.

| Exit | Verdict | Meaning |
|---|---|---|
| 0 | `equivalent` | ABC proved the miter output 0 in every reachable state; with `--selftest`, every control also met its expectation |
| 1 | `not_equivalent` | ABC found a counterexample (the frame is in `result.json`); with `--selftest`, also any control that missed its expectation |
| 2 | `error` | bad input, a failed precondition, or a tool failure |
| 3 | `undecided` or `timeout` | ABC gave up, or `--timeout` was reached |

Undecided is a failure, not a pass. A single changed gate can be neither
proven nor refuted in hours (mutB in [docs/equivalence.md](../docs/equivalence.md)).

## Method

1. **Inputs and provenance.** The tool copies the netlist into `OUT/inputs/`
   (decompressing `.gz`) and records the sha256 of every input. For a
   `tt_submission` artifact it compares the PDK revision at `PDK_ROOT` with
   `pdk.json` and refuses a mismatch. The revision is read from
   `ihp-sg13cmos5l/SOURCES` (written by ciel and by Tiny Tapeout's installer),
   or from the git HEAD of a checkout. It also compares the artifact's
   `src/project.v` and `src/protocol_emulator_core.v` with the RTL used
   (`inputs.artifact_src_matches`). The core's first line must name the
   variant's configuration: `configs/instruction-sram-32.json` for `base`,
   `configs/variants/diet4.json` for `diet4`.
2. **Structural preconditions (Python, on the netlist).** Any failure here
   gives `error`:
   - Every cell is in the liberty file, or is the SRAM macro.
   - Every output pin has a `function`.
   - There are no latches, three-state outputs or state tables.
   - Every flip-flop is a liberty `ff` whose `clocked_on` is a plain input
     pin (a rising edge).
   - No net has two drivers.
   - Every flip-flop clock pin and every SRAM `A_CLK` reaches the `clk` port
     through buffers only. A buffer here is a cell with one input and one
     output whose function is that input.

   The number of buffers on each clock path is recorded (see "Clock depth").
3. **Gold build (yosys).** The steps are:
   - read the SRAM blackbox, `project.v`, the core and `wrap.v`;
   - `proc`, `flatten`, `memory -nomap`, `memory_map`;
   - assert the macro count, then `expose -evert` the macros;
   - `check -assert`, then write `gold.il`.
4. **Gate build (yosys).** The same steps, except that it reads the liberty
   file (`read_liberty -ignore_miss_func`, which only skips cells without
   outputs, such as fill and decap; step 2 has rejected every other
   functionless cell), the blackbox, the netlist and `wrap.v`.
5. **Miter.** The steps are:
   - `miter -equiv -flatten gold gate` (no `-ignore_gold_x`; see control c1);
   - `async2sync`, `dffunmap`, then every flip-flop's initial value set to 0;
   - `techmap`, then the remaining x constants set to 0 (these are the RTL's
     don't-cares and out-of-range reads of its register arrays);
   - `aigmap`, then write `miter.aig`.

   Before writing the AIG, two assertions must hold:
   - only AND, NOT and rising-edge D flip-flops remain;
   - every flip-flop is clocked by the `clk` input.

   The AIG has one implicit clock, so either violation would be silently
   mis-modelled (controls c8 and c9).
6. **ABC.** The command is
   `read_aiger miter.aig; print_stats; strash; print_stats; dprove -v`.
   `dprove` runs BMC, then induction-based signal correspondence, then further
   engines. It either proves the single output constant 0 or returns a
   counterexample. ABC matches registers itself; the tool matches no names.
   The miter must have exactly one output, and exactly one verdict line must
   appear. `dprove` has several spellings: `Networks are equivalent.`,
   `Networks are not equivalent.` or `Networks are NOT EQUIVALENT.`, and
   `Networks are UNDECIDED.`. A verdict of equivalent next to a
   counterexample, or anything else, gives `error`.

## Recipe controls

`controls/*.v` are small designs, each with a gold version and a gate version
(`` `ifdef PE_GATE ``) and a declared expected verdict. They run through
exactly the same yosys and ABC scripts (`eq_check.py controls`, and
`check --selftest`). They test the recipe itself, not the design.

| Control | Gate change | Expected | Guards against |
|---|---|---|---|
| c1_gold0_mask | output `a` becomes `a\|b` (differs only where gold = 0) | not equivalent | a miter that masks gold-0 bits: `-ignore_gold_x` followed by `setundef -zero` turns the x test into `gold == 0` |
| c2_gold1_mask | `a\|b` becomes `a` (differs only where gold = 1) | not equivalent | the mirror case |
| c3_reencoded | counter stored inverted, reset to 4'hf | equivalent | a recipe that needs matching register encodings or names, or misses the forced reset |
| c4_deep | output differs only at count 13 | not equivalent (frame >= 13) | a search that stops at shallow depth |
| c5_sram_same | same macro inputs, restructured | equivalent | a broken cut point |
| c6_sram_din_swap | `A_DIN[1]` and `A_DIN[0]` swapped | not equivalent | macro inputs not compared |
| c7_sram_dout_swap | `A_DOUT[1]` and `A_DOUT[0]` swapped | not equivalent | read data not shared bit for bit |
| c8_gated_clock | register clocked by `clk & ui_in[7]` | error | a gated clock silently modelled as `clk` |
| c9_negedge | register on the falling edge | error | a falling-edge flip-flop silently modelled as rising |

The controls were also run through two weakened versions of the miter
script (job 24094085, docs/equivalence.md section 3):

- **Without the two assertions of step 5,** c8 and c9 are reported as
  equivalent.
- **With `miter -ignore_gold_x` and the later `setundef -zero`,** c1 and c4
  are reported as equivalent. The recipe of the first investigation had
  exactly this defect.

## Netlist controls (`--selftest`)

`--selftest` makes two mutants of the netlist under test and requires both to
be `not_equivalent`:

- **mutA** (`near-output-nand`): the `sg13cmos5l_nand2_1` nearest to a
  `uo_out`/`uio_out` bit becomes `sg13cmos5l_nor2_1`. "Nearest" is by a
  breadth-first search backwards from the ports through every cell except
  the macros, with ties broken by instance name.
- **mutC** (`sram-din-swap`): bits 3 and 2 of `A_DIN` are swapped on the
  first SRAM instance by name. If those two bits are the same net or a
  constant, the first adjacent distinct pair from bit 0 is used instead.

Both are deterministic, and the instance and nets are recorded in
`result.json`. They show that the check rejects a one-gate change and a
change visible only at a macro input, on this very netlist. `eq_check.py
mutate` writes either mutant for manual use.

## What is not covered

- **The SRAM macros themselves.** They are cut points: their contents,
  timing and internal behaviour are outside the proof.
- **The liberty functions against the layout.** Cell semantics are taken
  from the liberty file. LVS in the flow ties the layout to the netlist;
  nothing here ties a cell's layout to its liberty function.
- **Timing.** The model advances one clock cycle at a time. Setup and hold,
  clock skew, and races in a zero-delay simulation (the p026 case in
  docs/equivalence.md) are not visible to it.
- **Asynchronous behaviour.** Asynchronous resets are modelled at clock-cycle
  granularity (`async2sync`).
- **Power-up state.** Every flip-flop starts at 0 and the first cycle is a
  reset.
  - The 6x4 (diet4) core resets every register asynchronously: 2,587 bits
    after `proc`.
  - The base core has 64 32-bit registers (2,048 bits) that no reset
    initialises; the other 1,877 bits have a synchronous reset. Those 2,048
    bits start at 0 on both sides.
  - Equivalence from other power-up values of those registers is not
    covered.
- **RTL x values** are fixed to 0 on the gold side. A netlist that chose 1
  for a don't-care would be reported as not equivalent: a false failure,
  never a false pass.
- **Common-mode front end.** The RTL is read by Yosys. Synthesis in
  LibreLane also uses Yosys, so a Verilog front-end bug that both share
  would not be seen.
- **Proof certificates.** ABC's `dprove` is trusted. No independent
  certificate is produced or checked.

## Clock depth

`result.json` → `structure.clock` holds the number of buffers between the
`clk` port and each flip-flop clock pin (as a histogram) and each SRAM
`A_CLK`. It is information, not a pass criterion. In a zero-delay gate-level
simulation, a macro whose clock is several buffers deeper than a launching
flip-flop can sample that flip-flop's new value. That is what made p026 fail
its gate-level tests while its netlist is equivalent (docs/equivalence.md,
section 5). The depth alone does not predict the race: p025 has the same
depths as p026 and passes.

## Usage

```sh
export OSS_CAD_SUITE=<OSS CAD Suite root>   # yosys, yosys-abc (else PATH)
export PDK_ROOT=<root holding ihp-sg13cmos5l/>

# an official artifact (gh run download <id> -n tt_submission -D <dir>)
python3 formal_eq/eq_check.py check --run-dir <dir> --variant diet4 --selftest --out <out>

# a LibreLane run, a scripts/sweep or tools/opt run, or a bare netlist
python3 formal_eq/eq_check.py check --run-dir <run> --variant base --out <out>
python3 formal_eq/eq_check.py check --netlist <file.v[.gz]> --variant base --out <out>

# as a Slurm job (from the repository root)
sbatch -p <partition> --job-name=<name> --output=<log> \
    formal_eq/slurm_job.sh --run-dir <dir> --variant base --selftest --out <out>

# recipe controls only (seconds, no PDK); structure and clock depths only
python3 formal_eq/eq_check.py controls --out <out>
python3 formal_eq/eq_check.py structure --netlist <file.v>

# unit tests (the controls run too when yosys is found)
python3 -m unittest formal_eq/test_eq_check.py
```

**Options of `check`:**

- `--variant base|diet4`: default `$PE_VARIANT`, else `base`.
  - `base` uses `src/protocol_emulator_core.v` (the 8x4 design of record).
  - `diet4` uses `variants6x4/protocol_emulator_core.v` (the 6x4 build).
  - Any other variant needs `--core` (`$PE_CORE`).
- `--rtl-from-artifact`: take `project.v` and the core from the artifact's
  `src/` instead of this checkout.
- `--liberty` or `--pdk-root`: the liberty file, or the PDK root it is
  found under (see the table below).
- `--allow-pdk-mismatch`: run although the PDK revision differs from
  `pdk.json`.
- `--timeout`: the time limit of each ABC run, default 1800 s. Reaching it
  gives exit 3.
- `--build-timeout`: the time limit of each yosys step, default 1200 s.
- `--jobs`: cases run in parallel under `--selftest`; default
  `$SLURM_CPUS_PER_TASK`, else 1.
- `--out`: default `build/formal_eq/<label>`, which git ignores.

**Run directories** that `--run-dir` recognises:

| Layout | Netlist read |
|---|---|
| `tt_submission` (artifact root or its `tt_submission/`) | `tt_submission/tt_um_teslacoilerow_protocol_emulator.v`; `pdk.json`, `commit_id.json` and `src/` are recorded |
| LibreLane run | `final/nl/<design>.nl.v[.gz]` |
| `scripts/sweep` or `tools/opt` run | `out/final/nl/<design>.nl.v.gz`, checked against `out/final/SHA256SUMS` |
| `final/` directory | `nl/<design>.nl.v[.gz]` |

**Environment:**

| Variable | Meaning | Default |
|---|---|---|
| `OSS_CAD_SUITE` | tool root; `bin/yosys` and `bin/yosys-abc` | `PATH` |
| `PE_PDK_ROOT` or `PDK_ROOT` | PDK root holding `ihp-sg13cmos5l/` | `$PE_FLOW_ROOT/pdk` |
| `PE_FLOW_ROOT` | flow mirror | `$PE_WORK/sram-flow` |
| `PE_WORK` | work directory | `<repo>/build/tt-work` |
| `PE_VARIANT`, `PE_CORE` | defaults for `--variant`, `--core` | `base`, the variant's core |
| `PE_EQ_TOOL` | `slurm_job.sh` only: directory of `eq_check.py` | `$SLURM_SUBMIT_DIR/formal_eq` |

The runs in docs/equivalence.md used OSS CAD Suite 2026-07-29 (Yosys 0.67+111,
the release `formal.yaml` pins) and IHP-Open-PDK `2bbec755`. The typical
liberty file of that revision has sha256
`34163f3e7de9f854afac57332ba06f1739b78e7ac35660a8a5ddd39c570124ad`. The host
needs Python 3.6 or later, standard library only.

**Resources.** ABC is single-threaded. The measured runs are in
docs/equivalence.md, section 1.

- **6x4.** A 6x4 check took 92–97 s of wall time, with ABC 67–71 s (AMD EPYC
  9654).
- **8x4.** An 8x4 check took 483 s alone (ABC 446 s, AMD EPYC 7513). With
  `--selftest` it took 389–755 s (AMD EPYC 9474F). The longer figure
  includes a mutant that ABC needed 588 s to refute.
- **Memory.** The largest single process peaked at about 1.3 GB.

## Outputs

`OUT/result.json` (schema `pe-formal-eq/1`):

| Key | Contents |
|---|---|
| `verdict`, `exit_code`, `pass`, `why` | the outcome; `why` for anything but `equivalent` |
| `inputs` | netlist (source and copy, sha256), top and core (sha256, header line), variant, liberty (sha256), PDK root and revision, `pdk.json` revision and match, artifact `commit_id.json`, `artifact_src_matches` |
| `structure` | instance counts by role, flip-flop cells, SRAM instances, clock depths, precondition errors |
| `case` | per-step return codes and times, miter AIG sha256 and size (inputs, latches, AND nodes), ABC verdict line and counterexample frame |
| `selftest` | with `--selftest`: each netlist control (mutation record, verdict, frame) and each recipe control, the controls that missed, and `pass` |
| `timings_s`, `wall_s`, `peak_child_rss_kb` | step times; the peak resident set of the largest child process |
| `tool_git`, `tool_files`, `tools` | commit of this checkout and whether `formal_eq/` was modified, sha256 of the tool files (driver, wrapper, blackbox, controls), yosys path and version |
| `host`, `cpu_model`, `slurm_job_id` | where it ran |

Per case, the directory `OUT/<case>/` (`netlist`, `mutA`, `mutC`,
`controls/<name>/case`) holds the yosys scripts and logs, `miter.aig` and
`abc.log`. `OUT/gold/` holds the RTL build.

## CI proposal

[`ci-proposal.yaml`](ci-proposal.yaml) is a proposed workflow. It is
deliberately not in `.github/workflows/`, so it is not active.

- **Trigger.** It runs after a `gds` or `gds_6x4` run, or by hand with a
  run id.
- **Artifact.** It downloads that run's `tt_submission` artifact.
- **PDK.** It fetches only the standard-cell liberty directory of the
  revision in `pdk.json`, by partial clone and sparse checkout: 13 MB on
  disk, 1 s in a test on the cluster login node. It writes the same
  `SOURCES` line as Tiny Tapeout's installer.
- **Tools.** It sets up OSS CAD Suite 2026-07-29.
- **Check.** It runs the unit tests, then `check --selftest --jobs 2
  --timeout 2700` on the built commit. It fails unless the verdict is
  `equivalent`, every control passed, and the artifact's `src/` equals the
  checkout.

The evaluation, with a runtime estimate for a 4-vCPU runner, is in
docs/equivalence.md, section 7.

## Files

| File | Purpose |
|---|---|
| `eq_check.py` | the driver: `check`, `controls`, `mutate`, `structure` |
| `wrap.v` | the wrapper around both sides (reset forced in the first cycle) |
| `controls/c*.v` | recipe controls (gold and `PE_GATE` versions, expected verdict in the header) |
| `slurm_job.sh` | optional Slurm body for `eq_check.py check` |
| `test_eq_check.py` | unit tests (parsers, preconditions, mutations, verdict mapping, inputs; the controls when yosys is found) |
| `ci-proposal.yaml` | the proposed GitHub Actions workflow (inactive) |
