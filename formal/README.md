# Formal verification

SymbiYosys (`sby`) proofs over RTL generated from `hardcaml/`. There are three
kinds of job: unbounded proofs (k-induction or IC3/PDR), bounded model checks
(BMC) and cover witnesses. There are also two negative controls, which must
produce counterexamples. CI runs every job in `.github/workflows/formal.yaml`.

```sh
formal/run.sh                  # generate RTL from hardcaml/, then run every job
formal/run.sh --list           # job names
formal/run.sh timing_isolation_prove_k0 timing_isolation_neg_pull
formal/run.sh --generate-only  # just (re)generate formal/build/rtl
formal/run.sh --no-generate JOB  # reuse formal/build/rtl (what CI's matrix does)
```

Requirements:

- The OCaml toolchain from `scripts/opam-deps.txt`, used for generation. It is
  found the same way as in `scripts/generate.sh`.
- OSS CAD Suite 2026-07-29: yosys 0.67, sby, yices, bitwuzla and abc.
- `python3`.

Outputs go to `formal/build/`, which is gitignored. `FORMAL_WORK=/some/dir`
turns `formal/build` into a symlink, so the build tree can live elsewhere.
`formal/build/summary.tsv` records the status and wall time of each job. Each
sby work directory under `formal/build/sby/<job>/` keeps its log and any
counterexample or witness trace (`engine_0/trace*.vcd`).

## What is verified

Configuration: `configs/instruction-sram-32.json`. That is the first hardening
target, with 4 engines, a 32-bit datapath, 64 instructions per engine in
private instruction SRAM, 8-word FIFOs, fused issue and no prefetch. The
processor jobs use the exact IHP `RM_IHPSG13_1P_64x16_c2` FUNCTIONAL models
from `models/`, eight macros per processor. No SRAM contents are initialised
or assumed, except for the stated program-image sharing in the timing-isolation
miter. The configuration was not reduced for any job.

| Job | Harness / DUT | Method | Claim |
|---|---|---|---|
| `reset_safety` | `reset_safety.sv` / committed `src/project.v` + `src/protocol_emulator_core.v` | k-induction (unbounded) | After any edge with `rst_n=0` or `ena=0`, `uio_oe==0` and `uio_out==0`. Inputs are free; the first edge is reset. |
| `fifo_conservation` | `fifo_conservation.sv` / `Fifo.create` (32×8) | IC3/PDR (unbounded) | The ring buffer equals an independent shift-queue model in level, full/empty, head word and order. |
| `engine_safety` | `engine_safety.sv` / `Engine.create` | BMC, 24 cycles from reset | Queue handshakes; reset/stop/fault release; START state; WAIT countdown; NOP/JMP; blocked PULL/PUSH; strict/legacy PUSH; LIMIT fault threshold. |
| `processor_invariants_bmc` | `processor_invariants.sv` / SRAM processor, debug export | BMC, 24 cycles from reset | Ownership/open-drain masking, event recurrence, one DMA grant, mover handshakes and word identity, host priority, round robin. |
| `processor_invariants_prove` | same | k-induction (unbounded) | Same assertions for all reachable states. The base case is from reset. |
| `processor_inductive_bmc` / `_prove` | `processor_inductive.sv` | BMC 8 / k-induction | Invariants, plus trigger/event and command-update properties, preserved from any valid state (ownership disjoint, open-drain contained, FIFO levels ≤ 8). |
| `processor_inductive_cover` | same | cover | A mover grant and an event consumption in the same cycle. |
| `timing_isolation_*` | `timing_isolation.sv` / two SRAM processors | see below | Timing isolation (non-interference) of one engine's pins. |

The harnesses `fifo_conservation.sv`, `engine_safety.sv`,
`processor_invariants.sv` and `processor_inductive.sv` are the monorepo
properties, unchanged, except that `processor_inductive.sv` gains the cover
statement. `reset_safety.sv` now instantiates the committed TT top instead of a
separately generated one. The monorepo ran the reset, engine and invariants
checks as `sat -seq 12` and preservation as `sat -seq 4`. The processor properties are now proved unboundedly, and the
bounded jobs go deeper.

RTL generation (`run.sh`):

- `fifo.v` and `engine.v`: `hardcaml/bin/generate_formal.exe`, using the
  architecture embedded in the refinement config.
- `processor_debug.v`: `generate_refinement_formal.exe --target processor`,
  i.e. `Processor.create_refinement ~debug:true`.
- `processor_fv.v`: the same circuit plus observation ports, from
  `gen/generate_fv.ml` (see below).
- `processor_fv_mutant.v`: `mutate.py` applied to `processor_fv.v`.

`run.sh` builds these in a private dune workspace under `formal/build/hc`,
which holds copies of `hardcaml/{lib,bin}` plus `gen/`. It never writes to
`hardcaml/`.

## Timing-isolation proof

`timing_isolation.sv` builds a miter of two complete processors, A and B. The
claim is for engine K, a parameter.

**Shared between A and B:** `clk`, `rst_n`, `ena`, and the pin inputs
`uio_in`. Engine K's program image is also shared: whenever both copies read
the same address of engine K's instruction SRAM in the same cycle, they get
the same word. This is an assumption on the macro outputs, implied by equal
contents; engine K's SRAM is proven never to be written. It covers all 64
words, not only the committed image (addresses below `image_length`), so it is
slightly stronger than "the same program was loaded": two real loads of the
same image may differ in the words after it. Those words are never executed,
because the engine faults on `pc >= image_length` before using the fetched
word (`hardcaml/lib/engine.ml`), so the assumption hides no reachable
behaviour.

**Constrained host traffic:** each copy has its own free host port `ui_in`.
Only the commands that control engine K are constrained:

- START, STOP (including BEGIN_LOAD's implicit stop) and CLEAR-FAULT of engine
  K occur in the same cycles in both copies.
- Neither copy issues BEGIN_LOAD, COMMIT or OWNERSHIP while engine K is
  selected.

**Free and independent between the copies:**

- Other engines' programs, loads, commits, ownership, starts, stops and faults.
- ROUTE/mover descriptors and transfers.
- Host FIFO writes and reads to every engine, engine K included.
- SIGNAL events, pin-trigger configuration and FIFO flushes.
- Read-back, and the host protocol state itself.

**Initial state:** no reset is assumed. The following are equal in A and B:

- Engine K's 19 execution registers (PC, run/fault, tx/rx/x/y, repeat, timer,
  limit, blocked count, logical output/enable, XFER pins/edges/tick/period/mode,
  completed count).
- Its SRAM output latch.
- Its image length, valid, loaded and writing flags, ownership and open-drain
  mask.
- The timestamp and the pin synchronisers.

Each copy satisfies ownership disjointness and open-drain containment. These
are the auxiliary invariants reused from `processor_inductive.sv`. Engine K is
not mid-load. Everything else is arbitrary and independent: FIFO contents and
levels, mailboxes, other engines, the host and the mover.

**Claim:** the window is open from the last synchronised START or reset until
engine K moves a word through its TX/RX FIFO or consumes an event. That is, a
PULL, PUSH or WAITEVENT completes, observed in either copy through
`dbg_tx_pop`, `dbg_rx_push` or `dbg_event_clear`. While the window is open,
engine K's owned bits of `uio_out` and `uio_oe` are identical in A and B on
every cycle.

A PULL, PUSH or WAITEVENT that blocks identically in both copies does not
close the window. The theorem is therefore stronger than one that exempts
every *executed* PULL/PUSH/WAITEVENT, as `design.md` states it.

The auxiliary invariants make the claim 1-inductive:

- Engine K's registers are equal while the window is open.
- The SRAM output latch is equal while engine K runs.
- Configuration, timestamp and synchronisers are always equal.
- Engine K's SRAM is never written, and engine K is never loading.
- Ownership disjointness holds in each copy.

A sanity assertion checks that the observed `running` register equals the
production `dbg_running[K]` in both copies.

**Observation ports (`gen/generate_fv.ml`).** The Hardcaml RTL mangles
per-engine register names (`pc`, `pc_0`, ...) in an order unrelated to engine
index, and it has no per-engine hierarchy. The generator therefore elaborates
exactly `Processor.create_refinement ~debug:true` and attributes each engine
register structurally. A register belongs to engine k if and only if its
next-state logic reads the processor register `image_length_k`. The generator
aborts unless each engine owns exactly one register of each of the 19 names
and the result agrees with `dbg_running`. It then only adds output ports
(`fv_*`) that observe existing registers and SRAM instance pins. No logic
changes.

A simpler long-term alternative needs a change to `hardcaml/`, which this
directory does not own. `Processor.create_with_memory ~debug:true` would
export the per-engine state itself: the 19 engine registers packed as the
existing `dbg_*` vectors are, plus `image_writing`/`image_loaded` and each
SRAM's MEN/WEN/REN/ADDR/DOUT. Alternatively, `Engine.create` could take an
index and name its registers `e<k>_<name>`. Either change would let
`generate_fv.ml` be deleted.

**Jobs:**

| Job | Method | Result it must give |
|---|---|---|
| `timing_isolation_prove_k0..k3` | k-induction, depth 2, yices | PASS (unbounded) for each engine K = 0..3 |
| `timing_isolation_bmc` | BMC 20 cycles, bitwuzla | PASS |
| `timing_isolation_cover` | cover, depth 6 | Four witnesses: (1) engine K toggles an owned, enabled pin inside the window while the copies' accepted commands, running engines or mover grants have differed; (2) the same while another engine runs in A but not in B and A accepts a non-K command that B does not; (3) the same during an XFER while the mover grants differ between the copies; (4) engine K completes a FIFO/event interaction, which ends the window. |
| `timing_isolation_neg_pull` | BMC 12, `-DNI_NO_EXEMPTION` | FAIL, i.e. a counterexample (expected) |
| `timing_isolation_neg_mutant` | BMC 12 on `processor_fv_mutant.v`, `-DNI_PINS_ONLY` | FAIL, i.e. a counterexample (expected) |

**Negative controls:**

- `neg_pull` asserts pin equality even after interaction. The observed
  counterexample, 2 steps: engine 0 issues a strict `PUSH` (a=1). In copy B
  the RX FIFO has space, so the word is pushed. In copy A the RX FIFO is full,
  because A's host had not drained it, so engine 0 raises fault 4 and releases
  its output enable (`uio_oe` 0x08 in B, 0x00 in A). Host FIFO traffic does
  reach the pins once the engine interacts, which is why the exemption is
  needed.
- `neg_mutant` gates engine 0's instruction-SRAM memory-enable net with the
  host write strobe `ui_in[4]`, a shared or badly arbitrated program-memory
  port. The pin property is violated at step 3. B's host strobes, so B's
  macro skips a fetch and keeps presenting the stale word `FAULT 0x0b`, which
  B then executes, while A fetches and executes `DIR 0x01`. `uio_oe` becomes 0x01 in A and 0x00 in B. The
  environment assumptions therefore do not mask host-to-engine timing
  coupling.

## Observed results

Local runs used OSS CAD Suite 2026-07-29 (Yosys 0.67+111). Each ran on a
Slurm node (2 CPUs) or on the login node at `nice 19`. Times are wall-clock
seconds.

Full suite: Slurm job 23716631 (`mit_normal`, 2 CPUs), running
`formal/run.sh --no-generate` on RTL generated from `hardcaml/` on
2026-09-24. Wall time was 15:10 in total, and peak RSS was 2.16 GB (during
`timing_isolation_bmc`).

| Job | Outcome | Expected | s |
|---|---|---|---:|
| reset_safety | PASS, k-induction | PASS | 3 |
| fifo_conservation | PASS, abc pdr | PASS | 216 |
| engine_safety | PASS, BMC 24 | PASS | 16 |
| processor_invariants_bmc | PASS, BMC 24 | PASS | 29 |
| processor_invariants_prove | PASS, k-induction | PASS | 3 |
| processor_inductive_bmc | PASS, BMC 8 | PASS | 11 |
| processor_inductive_prove | PASS, k-induction | PASS | 2 |
| processor_inductive_cover | PASS, witness at step 2 | PASS | 4 |
| timing_isolation_prove_k0 | PASS, k-induction | PASS | 39 |
| timing_isolation_prove_k1 | PASS, k-induction | PASS | 39 |
| timing_isolation_prove_k2 | PASS, k-induction | PASS | 39 |
| timing_isolation_prove_k3 | PASS, k-induction | PASS | 34 |
| timing_isolation_bmc | PASS, BMC 20 | PASS | 454 |
| timing_isolation_cover | PASS, all 4 covers at step 2 | PASS | 9 |
| timing_isolation_neg_pull | FAIL, assertion at step 2 | FAIL | 5 |
| timing_isolation_neg_mutant | FAIL, assertion at step 3 | FAIL | 7 |

The engine choices are measured. For the 20-cycle miter BMC, bitwuzla took
502 s (2.2 GB) and boolector 830 s (3.5 GB). yices reached only step 8, and
`abc bmc3` step 7, within 15 minutes. With smtbmc/yices, `fifo_conservation` BMC stalled around step 14 after 9
minutes, and `abc bmc3` at depth 24 did not finish in 300 s. `abc pdr` proves
it unboundedly in 216 s (353 s on the login node).
`engine_safety` is not inductive as written (k-induction depth 4 fails on an
unreachable state), so it stays a BMC job.

## Design variants

```sh
formal/run.sh --variant diet4            # generate, then every job, for configs/variants/diet4.json
formal/run.sh --variant cn --no-generate timing_isolation_prove_k0
VARIANT_CORES=/path/to/cores formal/run.sh --variant cn_s2   # also check the core against cores/cn_s2.v
```

`--variant NAME` (or `FORMAL_VARIANT`) runs the same 16 jobs on a design variant
(plus the 15 line-unit jobs when the variant has the line unit, section "Line unit")
(`docs/isa.md`, "Configuration variants"). Without it nothing changes. The
build tree is `formal/build/variants/NAME/`.

- **RTL.** `processor_debug.v` and `processor_fv.v` come from the same
  generators, which read the refinement's `"options"`. The standalone
  `fifo.v` and `engine.v` come from `gen/variant/generate_blocks.ml`, which
  calls `Fifo.create_with` and `Engine.create ~options` as the processor does,
  because `hardcaml/bin/generate_formal.exe` reads an architecture config with
  no options. In the asynchronous reset styles the FIFO has a separate
  asynchronous chip reset `rst`, and `clear` is FLUSH. `reset_safety` reads the
  variant core, generated with `generate_refinement.exe`. With `VARIANT_CORES`
  it must equal the published `NAME.v` below its header line.
- **Harness settings.** `variant_sby.py` writes the `.sby` files to
  `formal/build/variants/NAME/sbyfiles/`, with the defines below on every
  `read -formal` line. It sets `DEPTH` to `fifo_words`, `PCW` (engine and miter)
  to the PC width and `IW` (miter) to the image-register width.

| option | defines | harness effect |
|---|---|---|
| `reset: sync_registered` | `RESET_SYNC_REGISTERED CLEAR_AT_START RESET_SYNC` | `reset_safety`: outputs released after every edge that follows a raw reset request three steps back; processor BMC starts from a clearing edge (`assume(clear)`); miter: the two synchronizer flops are shared state |
| `reset: async` | `RESET_ASYNC ASYNC_RESET` | `reset_safety` also asserts released outputs while raw is high; FIFO/engine/processor next-state assertions apply while the clear is low, and the reset state is asserted while it is high |
| `reset: async_sync_release` | `RESET_ASYNC_SYNC_RELEASE ASYNC_RESET RESET_SYNC` | as `async`, plus outputs released after every edge within two edges of a raw request; synchronizer flops shared in the miter |
| `debug_counters: false` | `NO_COUNTERS` | `engine_safety`: the completed count stays 0; the miter observes it as 0 (`generate_fv.ml` exports zeros) |
| `pc_bits: saturating_7` | `PC_SAT`, `PCW=7` | `engine_safety`: a JMP target, a LOOP target (repeat count non-zero) or a taken JZ target of 128 or more gives PC 127; LOOP also decrements the repeat count, and with a zero count it falls through |
| `shift: byte_lane` | `BYTE_LANE` | `engine_safety`: an issuable SHL/SHR with a non-lane count faults with code 1 and keeps its PC; a lane count completes, and a lane shift of RX (register 1) leaves RX shifted by the count |
| `line_unit: rec16` | `LINE_UNIT` | miter: line-unit state shared, LSTAT ends the window; the line-unit jobs (section "Line unit") |

The PC-saturation and lane-shift assertions are checked for non-vacuity by
three controls that are not part of the job list. Each replaces one assertion
by a wrong expectation and must give a counterexample from that assertion:
`-DVNEG_LOOP` (a saturating LOOP lands on `target[6:0]`), `-DVNEG_JZ` (a JZ
to 128 or more never branches) and `-DVNEG_LANE` (a lane shift of RX leaves it
unchanged). Add the define to the `read -formal` line of the derived
`formal/build/variants/NAME/sbyfiles/engine_safety.sby` and run its `bmc` task.

sby's default `async2sync` models an asynchronous reset as taking effect
within the cycle in which it is asserted. The asynchronous-style assertions are
written for that model.

`generate_fv.ml` attributes 18 engine registers when there are no debug
counters. It exports the PC and image registers at their native widths, and
the reset synchronizer as `fv_reset_sync`. For the design of record its
output is unchanged.

## Line unit

A variant with the line-unit option (`options.line_unit`, `docs/extension.md`;
the variant `diet8_rec16`) gets:

- **The existing 16 jobs with `-DLINE_UNIT`.** `generate_fv.ml` also attributes
  and exports the 19 line-unit registers of each engine (`fv_line_run` ...
  `fv_crc_preset`; `fv_transfer_mode` is 7 bits wide). The timing-isolation
  miter adds them to engine K's shared state, and an LSTAT of engine K, seen
  through the debug export `dbg_queue_status_read`, ends the window: LSTAT
  reads the TX-data and RX-space flags of engine K's queues, which the two
  copies may disagree on. `engine_safety` checks the engine with the unit.
- **15 line-unit jobs**, registered in `run.sh` only for such a variant. They
  run on `engine_line.v` (the production engine with the unit plus observation
  ports, `gen/variant/generate_blocks.ml --target engine_line`) and
  `crc_step.v` (the unit's CRC step function, `--target crc_step`):

| Job | Harness | Method | Claim |
|---|---|---|---|
| `line_crc_equiv` | `line_crc.sv` | prove | The CRC step equals a reference written from the published generator polynomials (`line_ref.vh`) for every state, bit, preset and bit order |
| `line_crc_e2e` | `line_crc.sv` | BMC 42, bitwuzla | A driven CRC line XFER of 1..10 bits at P = 1 (any data, initial value, preset, bit order, line code; stuffing off or runs of 4..8; pair off) leaves the reference CRC over the data bits |
| `line_codec_bmc`, `line_codec_bmc_p2` | `line_codec.sv` | BMC 46 | Engine B sampling engine A's pins receives A's N data bits (1..8 at P = 1, 1..4 at P = 2; no fraction; complements for Manchester), without stuff error or SE0, with an equal CRC; any legal LCFG except stuffing with Manchester; B uses A's LCFG with NRZ for Manchester and the arbitration bit cleared |
| `line_pins_bmc` | `line_engine.sv -DP_PINS` | BMC 20 | Outside SET and OUT the engine changes only the two pins named by PINS |
| `line_reset_bmc` | `line_engine.sv -DP_RESET` | BMC 16 | Every line-unit register is 0 after reset and after START |
| `line_decode_bmc` | `line_engine.sv -DP_DECODE` | BMC 16 | Invalid line-unit encodings fault with code 1 and change no line-unit configuration, flag or CRC; valid ones complete or start |

Each has a negative control on a mutant generated with `--mutation`
(`Line_unit.mutation`): `line_crc_equiv_neg` and `line_crc_e2e_neg`
(`crc_tap`), `line_codec_neg_nrzi` (`nrzi_decode`), `line_codec_neg_destuff`
(`rx_destuff_run`), `line_codec_neg_manchester` (`manchester_halves`),
`line_pins_neg` (`pin_leak`), `line_reset_neg` (`start_keeps_crc`) and
`line_decode_neg` (`ltim_overflow`). Not covered by these jobs: other P and
fractions, longer transfers, stuffing runs 1..3 and the pair in the CRC
property, the CRC of sampled bits against the reference (only B's CRC equals
A's), Manchester with stuffing, arbitration and SE0 on a shared bus; the
cocotb tests cover these by simulation (`docs/extension.md` section 6.3). A
line-unit negative control meets its
expectation only if the assertion that names it in a `target of:` comment
fired. `line_codec_bmc` took 1,588 s and 2,336 s with yices on two cluster
nodes, more than the default `SBY_TIMEOUT` of 1,500 s; run it with
`SBY_TIMEOUT=3600`. Results:
`docs/extension.md` section 6.4.

## Instruction-level specification

`formal/isa/` is an instruction-level specification of the design of record in
the style of riscv-formal's per-instruction checks. It was written from
`docs/isa.md` without reading `hardcaml/lib`, the generated RTL's logic or the
reference model (the earlier harnesses `engine_safety.sv` and
`processor_inductive.sv`, which also state instruction semantics, were read
for the port conventions; docs/isa-spec.md section 1), and is checked against `processor_fv.v` through the observation ports above.
[`docs/isa-spec.md`](../docs/isa-spec.md) has the method, the results per
instruction and the disagreements it found.

| File | Content |
|---|---|
| `isa/isa_insn.sv`, `isa/isa_insn.vh` | One module per instruction (opcodes 0..29 and the invalid opcodes 30..255): the architectural state after an issue edge as a function of the state before it and the image word at PC |
| `isa/isa_cmd.sv` | One property module per state-changing host command (SELECT, BEGIN, COMMIT, OWN, START, STOP, ROUTE, CLEAR, EVENT, FLUSH, TRIGGER) |
| `isa/isa_spec.sv` | The harness: observation-port abstraction, symbolic program image, the invariant V, the checks selected by the parameter `CHECK`, covers (`-DISA_COVER`) |
| `isa/isa.sby`, `isa/gen_sby.py` | The sby tasks; `gen_sby.py` writes `isa.sby` and lists the jobs and groups for `run.sh` |
| `isa/mutate_isa.py` | One seeded defect of engine 0 per negative control, located structurally in `processor_fv.v` |

Method. Engine `k`, image address `A` and the word `W` that engine `k`'s
instruction SRAM holds at `A` are symbolic (`anyconst`): a read of `A`
returns `W` and `A` is not written. Each one-step job assumes the invariant V
in its first state and asserts V together with its own properties; `sby prove`
(k-induction, depth 3) then shows that V holds in every reachable state and
that the properties hold on every edge out of one. `isa_reset` (BMC, one reset
edge from an arbitrary state) shows that reset lands in V. Clocked assertions
report one step after the step they sample, so a one-step check needs depth 3.
Five checks are bounded: the TX and RX queue data (`isa_queue_tx` depth 21,
`isa_queue_rx` depth 16, from a FLUSH of engine `k`) and the XFER pin and data
schedule for small transfers (three `isa_xfer_e2e*` jobs). Each took up to
about 1,600 s; unless `SBY_TIMEOUT` is set, `run.sh` gives them the limits of
`formal/isa/gen_sby.py --timeouts` instead of 1500 s.

Jobs, registered in `run.sh` for the design of record only (not with
`--variant`): 53 checks `isa_<name>`, 51 covers `isa_<name>_cover` and 49
negative controls `isa_<name>_neg`, 153 jobs. A negative control meets its
expectation only if one of the assertions that fired names it in a
`target of:` comment. `run.sh` generates the mutants with the rest of the RTL.

CI-sized groups (in `--list`; `run.sh GROUP` runs the group's jobs in turn):
`isa_ci_insn_a`, `isa_ci_insn_b` (the instruction checks), `isa_ci_other`
(PC bound, WAIT hold, halted engines, pins, V, reset and the command checks),
`isa_ci_cover` and `isa_ci_neg`. They contain every one-step job; the bounded
`isa_queue_*` and `isa_xfer_e2e*` jobs and their covers and negative controls
are not in a group. Measured wall times are in `docs/isa-spec.md`.

## Limits

- The timing-isolation result holds for the environment above. Engine K's
  program image, ownership and open-drain mask are not changed during the
  window, and host commands that control engine K are synchronised between
  the copies.
- Timing coupling that enters through a FIFO or event interaction is excluded
  by design. The window closes at the first completed interaction, and a
  synchronised START reopens it.
- These are RTL proofs of the generated Hardcaml Verilog with the vendor
  FUNCTIONAL SRAM models. They say nothing about the gate-level netlist,
  timing closure or the physical macros.
- `processor_fv.v` and `processor_debug.v` are debug variants of the circuit
  in `src/`: the same logic plus output ports. Only `reset_safety` reads the
  committed `src/` files directly. The equivalence of the debug variants to
  `src/protocol_emulator_core.v` is argued from the generator (the `debug`
  flag only adds output ports), not checked by a job.
- The timing-isolation cover witnesses start from an arbitrary state in which
  engine K's slice is equal in both copies, not from reset. They show that the
  assumptions leave room for divergent traffic around engine K; they are not a
  claim that each witness context is reachable from reset.
- The `.sby` `chparam` values assume 4 engines, 32-bit data and 8-word FIFOs.
  `run.sh` checks this against the config before running. With `--variant`
  the FIFO depth and the variant parameters come from the config instead.
