# Limitations: what the verification does not establish

This page lists what the results in [results.md](results.md) do *not*
show. It covers the state at commit `c118027` (tag `v0.1-hardened`). Each
item names the evidence it refers to. Open defects are in
[bug-ledger.md](bug-ledger.md).

## 1. No hardware observation

- **No silicon.** Every result is from simulation, formal proof, or the
  hardening flow's own analyses: STA, DRC, LVS and the Tiny Tapeout
  precheck. Nothing has been measured on a chip.
- **No FPGA board run.** Bitstreams exist for the Cmod A7-35T and the
  Urbana. Their evidence is simulation of the FPGA tops, one formal
  equivalence and configuration readback ([fpga.md](fpga.md), "Status").
  No board has been programmed, so no protocol traffic has been observed on
  real pins. An FPGA result would also not be a silicon result: the SRAM
  macro is replaced by a LUT-RAM stand-in, and the clocking and pads differ.
- **The host library is untested on hardware.** It has not been run on the
  Tiny Tapeout demo board, a Pico or an FPGA. It was tested against SDK
  fakes, the reference model and RTL simulation ([host.md](host.md),
  "Not verified").

## 2. Timing closure

- **The slow corner fails setup at 50 MHz.** The official build has a
  setup worst slack of −8.52 ns at `nom_slow_1p08V_125C`, with 2,482
  violating endpoints (run 36144357821; R7 in results.md). In the local
  mirror of the same configuration, which gave identical metrics (job
  23850490):
  - 2,467 of these are paths from the `rst_n` input to a register;
  - 14 are from `rst_n` to an output;
  - one is register to register: −0.116 ns, from the `A_DOUT[1]` output of
    instruction SRAM `e2_hi`.

  Tiny Tapeout signs off the typical corner only, where setup is met
  (+0.89 ns). 50 MHz is therefore established at the typical and fast
  corners, not across process, voltage and temperature.
- **With every path delay unchanged**, the slow-corner worst path needs a
  period of about 28.52 ns (about 35 MHz). The input and output delays that
  Tiny Tapeout's constraints apply may scale with the period, so this is an
  estimate, not an analysis.
- **The reset variants do not close the slow corner.** In local runs at
  `73536f0`, `rstreg` gave −7.52 ns (register to register −0.96 ns) and
  `cn_s2` gave −4.99 ns, with its worst path register to register
  ([sweep.md](sweep.md)). The slow corner failed in every 8x4 base run of
  the sweep, at −5.09 to −10.88 ns.
- **Design-rule counts are not zero.** The official build reports max-slew,
  max-capacitance and max-fanout violations (typical corner: 42, 70 and 524;
  slow corner: 290, 70 and 524). They are reported but not fatal in this
  flow.
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
  debug or observation output ports added. Their equivalence to the
  committed `src/protocol_emulator_core.v` is argued from the generator, not
  checked by an equivalence job. Only `reset_safety` reads `src/` directly.
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
  bounded (BMC 48). No unbounded proof has finished
  ([formal-depth.md](formal-depth.md), "What is still unproven").

**Revision.** The `formal_depth/` results are for `73536f0`. `c118027` has
the same core, but those jobs have not been re-run on it.

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
- **Gate-level simulation is partial.** It runs 36 of the 66 tests. The long
  and time-warp tests skip, and so do 17 of the 25 legacy replays. It is
  zero-delay, without SDF, and uses the FUNCTIONAL SRAM models. It checks the
  netlist's logic, not its timing.
- **The time-warp tests are white-box.** They deposit reachable counter
  values into RTL registers, and they run only on RTL. Gap closure killed
  216 mutants that had survived before; 82 of them are killed only by the
  time warp.
- **Mutation testing is limited in scope.**
  - The mutants are single-bit Yosys `mutate` faults in
    `protocol_emulator_core` only. `src/project.v` and the SRAM macros are
    not mutated.
  - 244 of 2,304 non-equivalent mutants survive. Many of the 55 on
    `blocked_cycles` are probably unobservable hold-path mutations, but
    they were not classified.
  - The score counts as equivalent only the 116 mutants proven so.
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

- **Magic DRC is not run in the official flow.** `RUN_MAGIC_DRC` is false
  since `1e2cfb3`. The Tiny Tapeout precheck's KLayout SG13CMOS5L deck is
  the DRC gate, and it passed. The Magic markers of earlier local runs were
  all inside the SRAM macros ([drc-triage.md](drc-triage.md)).
- **The SRAM macros are the IHP open-source `RM_IHPSG13_1P_64x16_c2`
  views.** Their silicon behaviour and characterisation are taken as given.
  The 84 Magic illegal overlaps, power stripes over the macros' Metal4
  obstruction band, are waived by configuration.
- **Tile size.** The official build is 8x4 tiles. That the competition
  accepts 8x4 is not confirmed in this repository. The 6x4 candidate
  (`diet4` on `fp6_tworow`) has passed local full sign-off only (results.md,
  R52), not the official actions. Its GDS had 14 precheck Pin-check errors
  from the same short power straps as BL-6 ([drc-triage.md](drc-triage.md),
  section 6). No 6x4 build with the halo fix has been run through the
  precheck.
- **One tool version.** The results are for one flow and PDK revision:
  LibreLane 3.1.0.dev3 and IHP-Open-PDK `2bbec755`.
- **No power claim.** Power and IR drop are only the flow's estimates, and
  no power budget is claimed.

## 6. Evidence and process

- **Many campaign results are for `73536f0`.** This covers the random and
  mutation campaigns, `formal_depth/`, the peers, the host library, the
  timing analyzer and the FPGA simulations. `c118027` has the same
  `src/protocol_emulator_core.v` and `firmware/`; it changes the tests and
  the hardening configuration. Its official CI results are in results.md,
  section 2.
- **Not all raw data is public.** Per-seed and per-mutant results and the
  job logs stay on the cluster. The repository keeps summaries. The 89.4%
  mutation summary is not yet committed (results.md, section 9).
- **CI runs only part of the suite.** It runs `regen`, lint, the cocotb
  RTL suite, the 16 `formal/` jobs, `docs`, `gds`, `precheck` and
  `gl_test`. It does not run `formal_depth/`, `test_ext/`, the host tests,
  `pe_timing`, the variants, the FPGA flow or the campaigns;
  `scripts/reproduce.sh --full` runs the local ones.
- **The Pages viewer fails.** The `viewer` job fails because GitHub Pages is
  not enabled, so the `gds` workflow badge shows a failure although gds,
  precheck and gl_test passed.
- **Known firmware findings are open.** BL-2 and BL-3 in the bug ledger.
