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
contents; engine K's SRAM is proven never to be written.

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
  committed `src/` files directly.
- The `.sby` `chparam` values assume 4 engines, 32-bit data and 8-word FIFOs.
  `run.sh` checks this against the config before running.
