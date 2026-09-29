# Limitations: what the verification does not establish

This page lists what the results in [results.md](results.md) do *not*
show. It was written for commit `c118027` (tag `v0.1-hardened`) and updated
on 2026-09-26 for `131e793`, the design of record from `25e331e` until `d76f1cc`: the same
RTL with the LibreLane settings of optimizer promotion p010 in
`src/config.json`, and on 2026-09-27 for `d76f1cc`, which constrains the
flow at 15 ns (66.7 MHz; optimizer promotion p018) while the operating
clock stays 50 MHz. On 2026-09-28 every item was checked again against
`24f31f0`. Items that no longer hold are kept and marked
**Resolved**, **Obsolete** or **Superseded**, with their evidence. Each item names the
evidence it refers to. Open defects are in [bug-ledger.md](bug-ledger.md).

## 1. No hardware observation

- **No silicon.** Every result is from simulation, formal proof, or the
  hardening flow's own analyses: STA, DRC, LVS and the Tiny Tapeout
  precheck. Nothing has been measured on a chip.
- **No FPGA board run.** Bitstreams exist for the Cmod A7-35T and the
  Urbana. Their evidence is simulation of the FPGA tops, one formal
  equivalence, configuration readback and, for the 2026-09-27 release, an
  AMD Vivado 2025.2 timing sign-off of all seven builds
  ([fpga.md](fpga.md), "Status"; results.md R62). A timing sign-off is a
  static analysis, not an observation.
  No board has been programmed, so no protocol traffic has been observed on
  real pins. An FPGA result would also not be a silicon result: the SRAM
  macro is replaced by a LUT-RAM stand-in, and the clocking and pads differ.
- **The host library is untested on hardware.** It has not been run on the
  Tiny Tapeout demo board, a Pico or an FPGA. It was tested against SDK
  fakes, the reference model and RTL simulation ([host.md](host.md),
  "Not verified").

## 2. Timing closure

- **The flow's timing constraint is 15 ns; the operating clock is 50 MHz.**
  Since `d76f1cc`, `src/config.json` sets `CLOCK_PERIOD` 15, so the flow
  repairs and signs off timing at 66.7 MHz; `info.yaml` `clock_hz` stays
  50000000, and the firmware, the host library, `pe_timing` and the
  datasheet assume 50 MHz ([timing-closure.md](timing-closure.md)
  section 10). What this does and does not establish:
  - **Official 15 ns result.** The `gds` run of `d76f1cc` (36298635436;
    results.md R85) passed gds, precheck and gl_test: setup WS typ/fast/slow
    +5.95/+6.92/+2.24 ns with 0 violations, hold WS min +0.165 ns, with
    metrics byte-identical to p018's local sign-off (R84). The flow gates
    setup at the typical corner only; the slow-corner figure is reported.
  - **The 50 MHz margins are a re-analysis, not a result of the official
    flow.** The flow times the design at 15 ns only. The margins at 20 ns
    (setup WS +8.95/+9.92/+6.79 ns, hold unchanged; R86) come from running
    the flow's own STA script and SDC on p018's final netlist and parasitics
    with `CLOCK_PERIOD` 20 (Slurm job 24077956). Two controls reproduce the
    flow's numbers exactly. The re-analysis is local: its scripts are in
    `tools/sta/`, but p018's netlist and parasitics are cluster files (the
    netlist is byte-identical to the official artifact's; results.md
    section 9, item 15).
  - **The input and output delays are an assumption.** `base.sdc` sets
    every input and output delay to 20% of the period
    (`IO_DELAY_CONSTRAINT` 20, not set in `src/config.json`): 3.0 ns at the
    15 ns sign-off and 4.0 ns in the 20 ns re-analysis, applied to setup
    and hold alike. They are not derived from the Tiny Tapeout multiplexer,
    the pads or the demo board. Input-to-output paths, which carry the
    delay at both ends, gain only 3 ns from 15 to 20 ns and are the worst
    typ and fast paths (`ui_in[7]` → `uo_out[5]`); they lose margin first if
    the real delays are larger.
  - **The 6x4 fallback is signed off at 20 ns only.** Its overlay pins
    `CLOCK_PERIOD` 20 and `clock_hz` 50000000, so the 15 ns change does not
    reach it ([6x4.md](6x4.md); results.md section 2c).
  - **No 13.33 ns (75.0 MHz) configuration is signed off by the official
    flow on `main`.** The optimizer's first 13.33 ns promotion, p024, passed
    the full run (LVS 0; setup WS typ/fast/slow +4.96/+6.10/+0.76 ns at
    13.33 ns), the precheck (9/9), the gate-level tests (0 fail) and the
    equivalence check (leaderboard of 2026-09-27 13:37 UTC). Its official
    `gds` job is projected at 7.37 h, beyond GitHub's 6-hour job limit, so
    it cannot be adopted ([optimization.md](optimization.md), "The 75 MHz
    plan"). Any 13.33 ns result would be sign-off margin only: the
    operating clock stays 50 MHz.
- **Resolved: the slow corner meets setup at 50 MHz.** The official build
  of `131e793` (run 36257636798; R16 in results.md) has setup worst slack
  +7.88 ns at `nom_typ_1p20V_25C`, +9.31 ns at `nom_fast_1p32V_m40C` and
  +2.95 ns at `nom_slow_1p08V_125C`, with 0 violating endpoints at every
  corner. The RTL is byte-identical to `c118027`; only `src/config.json`
  changed (timing-driven placement, a 5.75 ns post-CTS setup-repair margin,
  repair and fan-out limits, clock-tree settings, `SYNTH_STRATEGY`
  "DELAY 4", density and macro placement; [timing-closure.md](timing-closure.md)
  section 9). The promoted full run of that configuration (job 24010051)
  had produced a byte-identical `metrics.csv`, and its fast-mode trial (job
  24009268) the same slack values (R19). This is the 20 ns build of
  `131e793`; since `d76f1cc` the slow corner also meets setup at 15 ns in
  the local sign-off (+2.24 ns, R84), and the same layout re-timed at 20 ns
  has +6.79 ns (R86).

  The item as first written, for `c118027` (run 36144357821; R7): setup
  worst slack −8.52 ns at the slow corner, with 2,482 violating endpoints.
  In the local mirror of that configuration, which gave identical metrics
  (job 23850490), 2,467 were paths from the `rst_n` input to a register, 14
  from `rst_n` to an output, and one register to register (−0.116 ns, from
  the `A_DOUT[1]` output of instruction SRAM `e2_hi`). The typical corner
  met setup (+0.89 ns).
- **Obsolete: the 35 MHz estimate.** For `c118027` this page said that,
  with every path delay unchanged, the slow-corner worst path needed a
  period of about 28.52 ns (about 35 MHz). That described the `c118027`
  build and no longer applies.
- **The slow corner is reported, not gated.** The flow signs off setup at
  the typical corner only (`TIMING_VIOLATION_CORNERS` `*typ*`). The fast and
  slow results above are the flow's STA reports; a later configuration that
  missed the slow corner would still pass the gds action. The optimizer
  ranks its trials by the minimum slack over all three corners
  ([optimization.md](optimization.md), "Objective").
- **Superseded by `d76f1cc` (first item of this section): "No clock above
  50 MHz is signed off by the official flow or committed."** A 15 ns
  configuration is now committed, and its official build passed (R85).
  The item as written before `d76f1cc`: the optimizer's 15 ns (66.7 MHz) promotion p018 passed the
  local sign-off pipeline (full run with LVS 0, precheck 9/9, gate-level
  tests with 0 failures; setup WS typ/fast/slow +5.95/+6.92/+2.24 ns;
  results.md R84), but `src/config.json` and `info.yaml` still state 20 ns
  and 50 MHz, so no GitHub action has built it. The 13.33 ns (75.0 MHz)
  track had fast-mode trials only (best slow WS +0.76 ns) as of
  2026-09-27 01:20 UTC.
- **fmax figures derived from one period are extrapolations.** Frequencies
  derived from the 20 ns slack, 1000 / (20 − WS) (typ 82.5 MHz, slow
  58.7 MHz for `131e793`), assume that every path gains the full period
  change, but the constrained input and output delays scale with the period
  ([optimization.md](optimization.md), "Frequency tracks and the SDC";
  [timing-closure.md](timing-closure.md) section 10).
- **Hold margin at the fast corner is small.** Hold is met at every corner.
  In p018's local sign-off (R84) the worst slack is +0.165 ns at the fast
  corner (typ +0.372 ns, slow +0.743 ns); the worst hold paths are
  register to register, so the value is the same at 15 and 20 ns (R86).
  The official build of `131e793` had +0.11 ns at the fast corner (typ
  +0.32 ns, slow +0.66 ns; R17), and `c118027` +0.107 ns (R8).
- **The RTL variants did not close the slow corner.** In local runs at
  `73536f0`, `rstreg` gave −7.52 ns (register to register −0.96 ns) and
  `cn_s2` gave −4.99 ns, with its worst path register to register
  ([sweep.md](sweep.md)). The slow corner failed in every 8x4 base run of
  the sweep, at −5.09 to −10.88 ns. The behaviour-preserving timing options
  and the named variants `rstreg_timing` and `cn_s2_timing` did not close
  it either ([timing-closure.md](timing-closure.md) section 7). These
  results stand; the closure of `131e793` comes from the flow settings.
- **Design-rule counts are not zero.** p018's local full run (R84) reports
  max-slew / max-capacitance / max-fan-out violations of 1/0/1 at the
  typical corner, 0/1/1 at the fast corner and 4/0/1 at the slow corner, the
  same at 15 and 20 ns. The official build of `131e793`
  reports 1/0/3 at
  the typical corner, 0/0/3 at the fast corner and 4/0/3 at the slow corner
  (R18). For `c118027` they were 42/70/524 (typical) and 290/70/524 (slow).
  They are reported but not fatal in this flow.
- **Parasitics.** STA used the flow's nominal extraction only
  (`nom_*` corners).
- **Asynchronous resets are not timed.** No recovery/removal analysis was
  run for the asynchronous-reset variants (`cn`, `cn_s2`, `diet4`,
  `diet2`). The design of record uses a synchronous reset.
- **Inputs are synchronized, the host port is not.** Every pad input passes
  a two-flop synchronizer. The host port (`ui_in`) is not synchronized, so
  the host must be synchronous to the chip clock ([info.md](info.md),
  "Limitations").

## 3. What the formal proofs assume

**Timing isolation** (`formal/timing_isolation.sv`; see
[formal/README.md](../formal/README.md), "Timing-isolation proof" and
"Limits"). The claim covers engine K's owned pins in two copies of the
processor, under these assumptions:

- **Program image.** Whenever both copies read the same address of engine
  K's instruction SRAM in the same cycle, they get the same word. This is
  an assumption on the macro outputs, for all 64 words. It is justified by
  equal images and by proven non-writing. The proof does not model two
  loads of the same image, which may differ past the image length; an
  engine never executes those words.
- **Engine K's control commands** (START, STOP, CLEAR) occur on the same
  cycles in both copies. Neither copy issues BEGIN, COMMIT or OWN while
  engine K is selected. Clock, reset, `ena` and the pad inputs are shared.
- **The window closes** at the first completed FIFO or event interaction of
  engine K. Timing coupling through FIFO contents, mailboxes or the mover is
  excluded by design; the negative control `neg_pull` shows that it exists.
- **The initial state is not reset.** Engine K's slice is equal in both
  copies; the rest is arbitrary but constrained by auxiliary invariants.
  The cover witnesses show that the assumptions leave room for divergent
  traffic. They do not show that every witness context is reachable from
  reset.

**Scope of every RTL proof** (`formal/` and `formal_depth/`):

- **RTL only.** The proofs are about the generated RTL with the vendor
  FUNCTIONAL SRAM models. They say nothing about the gate-level netlist,
  timing or the physical SRAM macros.
- **Debug netlists.** Most processor proofs read `processor_debug.v`,
  `processor_fv.v` or `protocol_processor_fd`: the same generator with
  debug or observation output ports added. Only `reset_safety` reads `src/`
  directly.
  - `processor_fv.v`, the netlist of the timing-isolation proof and of the
    timing certificates, is checked: `tools/timing/cert/equiv_src.py`
    proves it sequentially equivalent to the committed `src/project.v` and
    `src/protocol_emulator_core.v` on the chip ports, from the all-zero
    state (ABC `dprove`, Slurm job 23987598, on the netlist generated at
    `c118027`). A netlist with one extra input flip-flop is found not
    equivalent (job 23989727) ([timing-certificates.md](timing-certificates.md),
    section 6). The `processor_fv.v` that the `formal` workflow generated on
    `24f31f0` is byte-identical to that netlist (results.md R95).
  - `processor_debug.v` (the `processor_invariants` and
    `processor_inductive` jobs), the component netlists `engine.v` and
    `fifo.v`, and `protocol_processor_fd` (`formal_depth/`) are not checked
    by an equivalence job; their equivalence is argued from the generator.
- **Asynchronous resets** are modelled by sby's `async2sync`, which makes an
  asynchronous reset take effect within the cycle. Recovery, removal and
  metastability are outside the model.
- **The host is modelled as synchronous**, with inputs that change between
  edges.
- **One configuration.** Each proof is for one configuration: 4 engines,
  32-bit data, 64 words and 8-word FIFOs for the design of record. The
  variants were proved separately with `--variant`.
- **No liveness.** The mover properties are safety only: nothing is lost,
  duplicated, corrupted or reordered. Delivery depends on host service and
  on space at the destination.

**Bounded results.** Some properties are proved only to a depth:

- In CI: `engine_safety` (BMC 24), `processor_invariants_bmc` (24),
  `processor_inductive_bmc` (8) and `timing_isolation_bmc` (20).
- `engine_safety` is unbounded only in the `formal_depth/` runs: suprove,
  or k-induction with three added invariants. Those runs are on the
  cluster, not in CI.
- The program-load SRAM data-integrity properties (`spec_word_*`) are
  bounded: BMC 48 with four engines, and BMC 96 with rIC3 alone. No
  unbounded proof has finished: IC3 ended without a verdict
  ([formal-depth.md](formal-depth.md), "What is still unproven").

**Revision.** The `formal_depth/` results are for `73536f0`. `c118027`,
`131e793`, `d76f1cc` and every later commit up to `24f31f0` have the same
`src/protocol_emulator_core.v`, but those jobs have not been re-run on
them.

## 4. What simulation evidence does not cover

- **Model and RTL share one specification.** The lockstep check compares
  the RTL with the Python reference model (`test/model/`). Both implement
  the ISA of [isa.md](isa.md). A mistake in that specification, or the same
  misreading in both implementations, passes every lockstep comparison,
  including the 536,064 random cases. The checks that do not use the model
  as the reference are:
  - the third-party protocol peers ([independent-peers.md](independent-peers.md));
  - the protocol checks of the static timing analyzer, which use UM10204
    and the other specifications ([timing-analysis.md](timing-analysis.md));
  - the formal properties, which are written from the specification.

  These cover protocol behaviour at the pins and selected properties, not
  the whole ISA.
- **Observability.** A fault inside the design that never reaches `uo_out`,
  `uio_out` or `uio_oe` is invisible to the lockstep check. The sticky host
  fault flag kept `uo[7]` high in 69% of default random cycles, so fault-pin
  timing is mostly checked through status reads
  ([verification-campaign.md](verification-campaign.md), finding 5).
- **Coverage is only as good as the bins.** Every bin that the generator and
  the extended observer define was hit. Some interactions are rare, for
  example a synchronous START of all four engines, at 0.36 per 1,000 default
  cases. Behaviour outside the defined bins is not measured.
- **Gate-level simulation is partial.** In the official gl_test of `131e793`
  it runs 46 of the 102 tests; 56 skip (R15). For `c118027` it ran 36 of
  66 (R3). The 56 skipped tests (from the run's `results.xml`) are the 7
  time-warp tests, 17 of the 25 legacy replays, 26 of the 36 `test_kill_*`
  tests, 3 of the 5 counter tests and 3 of the 14 directed tests: long
  tests, and tests that use the time warp, which works only on RTL. It is
  zero-delay, without SDF, and uses the FUNCTIONAL SRAM models. It checks the netlist's logic, not its timing.
- **The time-warp tests are white-box.** They deposit reachable counter
  values into RTL registers, and they run only on RTL. Gap closure killed
  216 mutants that had survived before; 82 of them are killed only by the
  time warp.
- **Mutation testing is limited in scope.**
  - The mutants are single-bit Yosys `mutate` faults in
    `protocol_emulator_core` only. `src/project.v` and the SRAM macros are
    not mutated.
  - After the mutation push of `aa07868`, 73 of 2,296 non-equivalent
    mutants survive (score 96.82%, R23c). Each has a written argument for
    why no test can observe it, but the arguments are not proofs, so they
    count as survivors ([mutation-push.md](mutation-push.md) section 5).
    A later miter run with a longer ABC time cap proved 26 of the 73
    equivalent (97.93%, 47 survivors; mutation-push.md section 4.5); the
    headline stays 96.82%, the score as run. At `c118027` it was 244 of
    2,304 (89.4%, R23b), including 55 on `blocked_cycles` that were not
    classified then.
  - The score counts as equivalent only mutants proven so: 124 since
    `aa07868` (116 before).
- **The peers are models, not devices.** Two of the third-party peers have
  defects of their own (BL-19). The skew corners model the order of
  same-edge changes, not pad delays, slew, set-up or hold. The JTAG peer is
  a RISC-V debug transport module, not a conformant IEEE 1149.1 TAP.
- **The timing analyzer is validated against the model.** It works at ISA
  level and was checked against the reference model, not against the RTL
  directly. The end-to-end timing argument ([timing-analysis.md](timing-analysis.md),
  "End-to-end guarantee") combines it with lockstep equivalence and the
  timing-isolation proof, and inherits their assumptions.
- **Simulator versions differ.** CI uses Icarus Verilog 13.0. Most
  cluster campaigns used Icarus 14.0 (a development build). The
  peers and host results include Icarus 13.0 runs.

## 5. Physical implementation

- **Netlist equivalence has a stated scope.** `formal_eq/` proves that the
  official netlists compute the same outputs as the RTL (results.md R90),
  but not the SRAM macros (they are cut points), the match between a cell's
  liberty function and its layout, timing, or behaviour from power-up
  values other than 0 of the 2,048 register bits of the base core that no
  reset initialises. It runs locally, in the optimizer's promotions and in
  CI after every `gds` and `gds_6x4` build of `main`
  (`.github/workflows/equiv.yaml`; [equivalence.md](equivalence.md)
  section 7). On GitHub it has proved the official netlists of `d76f1cc`
  and of every later build of `main` that finished after the workflow was
  added equivalent (results.md R93, runs to 2026-09-28 08:40 UTC). A
  netlist
  mutant with one random cell changed stayed undecided, so the check fails
  closed on "undecided" ([equivalence.md](equivalence.md)).
- **Magic DRC is not run in the official flow.** `RUN_MAGIC_DRC` is false
  since `1e2cfb3`. The Tiny Tapeout precheck's KLayout SG13CMOS5L deck is
  the DRC gate, and it passed. The Magic markers of earlier local runs were
  all inside the SRAM macros ([drc-triage.md](drc-triage.md)).
- **The SRAM macros are the IHP open-source `RM_IHPSG13_1P_64x16_c2`
  views.** Their silicon behaviour and characterisation are taken as given.
  The Magic illegal overlaps, power stripes over the macros' Metal4
  obstruction band, are waived by configuration: 86 in the build of
  `131e793`, 84 in that of `c118027`.
- **Tile size.** The official build is 8x4 tiles. That the competition
  accepts 8x4 is not confirmed in this repository. The 6x4 fallback
  (`diet4`, [6x4.md](6x4.md)) is built by the `gds_6x4` workflow at
  `CLOCK_PERIOD` 20, so it is signed off at 50 MHz only, not at the 15 ns
  of the 8x4 build (section 2):
  - Its configuration before `25e331e` passed gds, precheck and gl_test in
    runs 36225500529 and 36238342669, with slow-corner setup −2.80 ns
    (results.md, R80).
  - On `131e793` the build failed with 69 routing DRC errors, because the
    overlay inherited the new 8x4 timing keys (R81).
  - `4bd30c8` puts optimizer promotion p014 into the overlay (local
    sign-off: full run, LVS 0, precheck 9/9, gate level 0 fail; slow
    +2.74 ns; R82). Its official run, 36274474540, passed with metrics
    byte-identical to the local run (R83).
  - Since `8a05de7` and `fdc23f2` the overlay states all 33 keys the
    optimizer treats as flow knobs, and the `info.yaml` overlay restates
    `clock_hz` (results.md section 9, item 13). A key outside that list can
    still reach the 6x4 build from `src/config.json`. The run of `1e5b1d8`
    (36285537630) passed with metrics byte-identical to those of `4bd30c8`
    (R87), and the 6x4 build files of `d76f1cc` are byte-identical to those
    of `1e5b1d8`; the run of `d76f1cc` (36298635404) passed all five jobs
    with the same `metrics.csv` and netlist (R89).

  Earlier state, kept for reference: the first 6x4 candidate (`diet4` on
  `fp6_tworow`, results.md R52) had 14 precheck Pin-check errors from the
  same short power straps as BL-6 ([drc-triage.md](drc-triage.md),
  section 6); the `edgeobs` floorplan of the current overlay removes them.
- **One tool version.** The results are for one flow and PDK revision:
  LibreLane 3.1.0.dev3 and IHP-Open-PDK `2bbec755`.
- **No power claim.** Power and IR drop are only the flow's estimates, and
  no power budget is claimed.

## 6. Evidence and process

- **Many campaign results are for `73536f0`.** This covers the random and
  mutation campaigns, `formal_depth/`, the peers, the host library, the
  timing analyzer and the FPGA simulations. `c118027`, `131e793`,
  `d76f1cc` and every later commit up to `24f31f0` have the same
  `src/protocol_emulator_core.v`. After `c118027`, `firmware/` changed in
  the three I²C controller images (`fb79f31`, the I²C timing fix) and gained
  `firmware/ext/`, the images of the extension variant (`76a81f5`), which
  the design of record does not run. Otherwise these commits change the
  tests, the hardening configuration, the tools and the documentation.
  Their official CI results are in results.md, sections 2, 2b and 2d.
- **The timing certificates cover 16 of the 19 images at `24f31f0`.** They
  were proved for the images committed at `c118027`; the three I²C
  controller images changed in `fb79f31` and await re-certification
  (results.md R42).
- **Not all raw data is public.** Per-seed results, raw per-test logs and
  the job logs stay on the cluster. The repository keeps summaries. The
  89.4% gap-closure summary is not committed (results.md, section 9, item
  4). The per-mutant status of the 96.82% push and of the `diet4` rerun is
  in `campaigns/mutation/results/push-4bd30c8/` and `diet4-102/`
  (`summary.json`, `mutant_status.tsv`; item 11).
- **CI runs only part of the suite.** On every push it runs `regen`, lint,
  the cocotb RTL suite, the 16 `formal/` jobs, `docs`, `gds`, `precheck`
  and `gl_test`; on each push of `main` also the 6x4 build (`gds_6x4`);
  and after each `gds` and `gds_6x4` build of `main` the RTL-vs-netlist
  equivalence check (`formal_eq/`). The
  `consistency` workflow checks the documentation against the repository
  (`tools/evidence/`). CI does not run `formal_depth/`, `test_ext/`, the
  host tests, `pe_timing`, the timing certificates, the variants, the FPGA
  flow or the campaigns; `scripts/reproduce.sh --full` runs the local
  ones.
- **The Pages viewer fails.** The `viewer` job fails because GitHub Pages is
  not enabled, so the `gds` workflow badge shows a failure although gds,
  precheck and gl_test passed.
- **Resolved in `fb79f31`, in part: the firmware timing findings.** At
  `c118027`, BL-2 (a 7-cycle SCL low phase before the repeated START) and
  BL-3 of the bug ledger were open. `fb79f31` fixed BL-2 and the NACK path
  of BL-3; `pe_timing` reports no FAIL and no WARN on the images of
  `fb79f31` (results.md R40b).
  Two INFO items of BL-3 remain open: the SPI target's 9-cycle CS-high
  minimum is not declared in its notes, and
  `firmware/spi-controller-fast.image.json` carries the name
  `spi-controller-mode0`.
