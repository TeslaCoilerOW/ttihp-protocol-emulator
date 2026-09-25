# Formal depth: deeper proofs and new properties

This page extends the formal story in [`formal/README.md`](../formal/README.md)
with MIT Engaging cluster time. It records what was run, against which
revision, how deep each run went and how long it took, and what is still
unproven. The harnesses, job table and drivers are in
[`formal_depth/`](../formal_depth/README.md). Nothing in `formal/` was
changed.

**Revision.** Every run used the committed tree at `73536f0`. It was
exported with `git archive` into a private snapshot, so edits that other
work made to the working tree during the runs could not leak in. The RTL was
regenerated from that snapshot's `hardcaml/`:

- `formal/run.sh --generate-only` produced `processor_fv.v`,
  `processor_debug.v`, `engine.v` and `fifo.v`.
- `formal_depth/gen/generate_fd.ml` produced `protocol_processor_fd`.

**Tools.** OSS CAD Suite 2026-07-29: Yosys 0.67, SymbiYosys, and the solvers
yices, bitwuzla, boolector and z3 (through smtbmc), ABC (`bmc3`, `pdr`),
rIC3 1.5.2, suprove, btormc and pono.

**Configuration.** Every processor-level job uses
`configs/instruction-sram-32.json`: 4 engines, a 32-bit datapath, 64
instructions per engine and 8-word FIFOs. The instruction memory is the
exact IHP `RM_IHPSG13_1P_64x16_c2` FUNCTIONAL model, eight macros per
processor. No SRAM contents are initialised or assumed.

## What was added

### 1. Deeper runs of the existing jobs

The `formal/` harnesses were run unchanged, with one solver per Slurm array
task. The portfolio was yices, boolector, bitwuzla and z3 through smtbmc,
ABC `bmc3`, rIC3 BMC, btormc and pono. Each smtbmc log gives a
depth-against-time curve.

### 2. New properties

Each property set is one harness over the whole processor. For each one
there are:

- a `prove` task: k-induction with three SMT solvers, plus IC3/PDR through
  ABC `pdr`, rIC3 and suprove;
- a BMC task from reset;
- cover witnesses showing the claims are not vacuous;
- mutant negative controls. Each is a one- or two-line change to a private
  copy of `hardcaml/lib`, and each must produce a counterexample on a named
  `spec_*` assertion.

**Host-port atomicity and read snapshots** (`host_protocol.sv`). A model of
the documented nibble protocol watches only `ui_in`/`uo_out` and checks the
processor's side effects against it.

- A command, program word or TX word takes effect only on the eighth accepted
  nibble of one window, and it carries exactly the assembled word.
- A window change or reset discards a partial word. No command-controlled
  state (ownership, image, routes, triggers, selection, read-select) changes
  without an accepted command.
- A rejected command sets the sticky host fault on its eighth nibble, and
  only then.
- A host RX pop happens only on the eighth read nibble, and the popped RX
  word is the word the host read.
- A read returns the value its source had on the edge before the word was
  first presented: status, timestamp, levels, PC, event, count, held RX,
  version, or the RX head. This holds whatever the stalls.
- Read data does not change while it is not accepted.
- The RX head is reserved (frozen) while a window-3 read is in progress.
- A window change deasserts write-ready and read-valid in the same cycle.
  Window 3 never accepts writes.

**Program-load safety** (`program_load.sv`).

- An engine's instruction SRAM is written only while that engine is halted
  and loading, that is, after BEGIN and before COMMIT, with its image
  invalid. Both 16-bit halves are always written together.
- While an engine runs, its image is committed, 1 ≤ length ≤ 64, and every
  address below the length was written after the last BEGIN.
- The word the engine sees is always the SRAM word read at its PC on the
  previous edge. The macro refinement fetches exactly the PC's word.
- An issue slot with PC ≥ length faults with code 2 and releases the
  engine's enables. It pops, pushes and consumes nothing.
- For a symbolic engine E and address A, the word E executes at PC = A is
  the last word the host wrote to A after BEGIN.

**Mover conservation across all engines** (`mover.sv`). Two tagged words
with symbolic values, pushed at symbolic times by a symbolic source engine,
are followed through RX → mover → TX → the destination engine's PULL.

- A tag is never lost silently. Only a FLUSH of the FIFO that holds it, or
  reset, drops it.
- A tag is never corrupted: the FIFO slot, the head, the pushed TX word and
  the destination engine's `tx` register after PULL all hold its value.
- The destination is the source's route at the time of the move, and the
  TX push is accepted.
- The older tag leaves RX first. If both tags go to the same queue, the
  older one is ahead of the other there and is pulled first.
- Every grant pops its source and pushes its destination in the same cycle
  (so there is no duplication), and only with quota left.
- The quota changes only by ROUTE (set), FLUSH (zero) or an accepted move
  (minus one). There is at most one grant per cycle.

**Round-robin grant bound** (`rr_bound.sv`, no reset assumed). The mover is
work-conserving: if any source is eligible, exactly one is granted. A source
that stays eligible is granted within 4 cycles, that is, after at most three
grants to other sources, which is the documented "within four accepted
grants". A cover shows the worst case is reached.

**Fault stickiness and output-enable release** (`fault_release.sv`).

- A nonzero fault code stays unchanged until an accepted CLEAR while the
  engine is halted, or reset. START is never accepted for a faulted engine.
- Only a running engine raises a fault, and a faulted engine is not
  running.
- A faulted, halted or reset engine drives none of its owned pins (their
  `uio_oe` and `uio_out` bits are 0). A halted engine's logical enables are
  0, so START cannot re-drive stale enables.
- An issued HALT stops the engine without a fault and releases its enables
  on that edge.
- After a reset edge, no engine runs and no fault is set.
- `uo[7]` is exactly host-fault OR any engine fault. `uo[6]` is exactly any
  mailbox OR any non-empty RX FIFO.

**Ownership disjointness end to end and open-drain safety**
(`pin_safety.sv`).

- `uio_oe` and `uio_out` equal an exact per-pin model of the engines'
  logical pins, ownership, open-drain masks, run state and fault state.
- No two engines ever drive the same pin's OE, and ownership stays pairwise
  disjoint.
- An open-drain pin never drives high (`uio_out` = 0). It pulls low exactly
  when its engine's logical value is 0 and its enable is 1, so SET high
  releases it.
- Ownership changes only through OWN for a halted engine.
- While an engine runs, its logical enables and values lie inside its
  ownership. The ownership mask in the pin mux is therefore defence in
  depth, not the only barrier.

**`engine_safety` made unbounded.** Two routes were used:

- The unmodified `formal/engine_safety.sv` was given to IC3-style engines.
- A copy strengthened by three invariants (`engine_safety_strengthen.vh`) was
  proved by plain k-induction. Every original assertion is unchanged in the
  copy.

### How the new harnesses see inside the chip

`protocol_processor_fd` is the circuit that `formal/` proves,
`Processor.create_refinement ~debug:true`, with extra output ports:

- the per-engine registers (the same structural attribution as
  `formal/gen/generate_fv.ml`);
- the host-port registers;
- the SRAM data-in pins;
- the FIFO heads, pointers and storage arrays.

No logic is added. In every run from reset, the ports are used only inside
assertions, as strengthening invariants (`link_*`), and never inside
assumptions. A wrong attribution could therefore make such a proof fail, but
not pass.

Runs marked "from an arbitrary valid state" (`-DFD_FROM_ANY`) instead
*assume* the common invariants of `formal_depth/harness/fd_invariants.vh` on
their first cycle. The `prove` tasks prove those invariants from reset. The
mutant negative controls run with every `link_*` assertion removed
(`-DFD_SPEC_ONLY`), so they fail on the property itself.

## Results

PASS appears below only for runs that were observed to finish with that
status. Times are wall-clock seconds for the whole sby task, which includes
yosys model generation. Each task ran on 2 CPUs on `mit_preemptable` or
`mit_normal`. Slurm ids are `array_task`. The machine-readable summary is in
[`formal_depth/results/summary.tsv`](../formal_depth/results/summary.tsv),
and the job list is in the work area's `manifest.json`
(`<local work dir>/tt-work/formal-depth/`).

### Deeper runs of the `formal/` jobs

| Job | `formal/` today | Reached here | Engines that passed (time, Slurm id) |
|---|---|---|---|
| `timing_isolation_bmc` | BMC 20 (454 s, bitwuzla) | **BMC 100** | 100: rIC3 2643 s (23757105_0). 60: rIC3 1226 s (23752066_7), and independently smtbmc bitwuzla 7038 s (23752066_2). |
| `engine_safety` | BMC 24 | **unbounded**, plus BMC 128 | See the next table. BMC 128: rIC3 75 s (23757106_0), btormc 96 s (_2), bitwuzla 516 s (_3), abc bmc3 1158 s (_1). BMC 64: rIC3 31 s, btormc 37 s, pono 49 s, bitwuzla 53 s, boolector 119 s, abc bmc3 149 s, yices 609 s (23752067_7/_5/_6/_2/_1/_4/_0). |
| `processor_invariants_bmc` | BMC 24 | **BMC 128** | 128: abc bmc3 362 s (23757105_4), btormc 394 s (_5), rIC3 3000 s (_3). 64: abc bmc3 77 s, btormc 135 s, pono 264 s, rIC3 278 s, bitwuzla 349 s, boolector 700 s (23752066_12/_13/_14/_15/_10/_9). |
| `processor_inductive_bmc` | BMC 8 | **BMC 96** | 96: btormc 292 s (23757105_8), rIC3 699 s (_6), abc bmc3 4021 s (_7). 48: btormc 67 s, pono 161 s, rIC3 207 s, boolector 1006 s, abc bmc3 1361 s, bitwuzla 1489 s (23752066_21/_22/_23/_17/_20/_18). |
| `fifo_conservation` | unbounded (abc pdr) | unbounded, 3 engines | rIC3 159 s (23752067_19), abc pdr 218 s (_18), suprove 1303 s (_21). |

`processor_invariants_prove`, `processor_inductive_prove`, `reset_safety`
and `timing_isolation_prove_k0..3` were already unbounded in `formal/` and
were not rerun.

**The deepest BMC results come from rIC3, and a failure check backs them.**
rIC3 BMC reports "unknown" (exit 30) when it reaches its bound without a
counterexample, and sby maps that to PASS. Three checks back this up:

- On a toy counter, a bound below the failing step gave PASS and a bound
  above it gave FAIL.
- On the timing-isolation miter itself, rIC3, abc bmc3 and btormc each
  reported FAIL on both `formal/` negative controls, `neg_mutant` and
  `neg_pull` (job 23770778, BMC 12). For rIC3 and abc the sby job then ends
  in ERROR, because sby cannot replay their AIGER witness ("witness signal
  mismatch for a.dut.ena"). The engine verdict is FAIL in the log. btormc
  produced a full FAIL trace.
- smtbmc bitwuzla independently reproduced `timing_isolation` to depth 60
  (7038 s), and the SMT solvers reproduce every lower depth listed.

### `engine_safety` made unbounded

Plain k-induction on the unmodified harness fails at depths 8, 16 and 32
with both yices and bitwuzla (23752067_12 to _17), all on the fault-release assertion
at `engine_safety.sv:66`. The counterexample to induction is unreachable. It
is a faulted engine whose `running` flag is still set, with `clear_fault`
held high every cycle. `clear_fault` is ignored while the engine runs, and
the assertion's guard excludes cycles with `clear_fault`, so the loop never
reaches the assertion. Once the next strengthening invariant was added,
depth-2 induction failed on a blocked-cycle counter at `0xffffff`, whose
24-bit increment wraps before it reaches the limit.

| Harness | Method | Result (time, Slurm id) |
|---|---|---|
| unmodified `formal/engine_safety.sv` | IC3 (suprove) | **PASS**, unbounded, 98 s (23752067_11) |
| unmodified | abc pdr, rIC3 IC3 | not converged: still running when this was written (23752067_8, _9). pdr was at frame 6 after 77 min. |
| unmodified | avy | tool crash (segfault, rc 139); not a result |
| `engine_safety_inductive` = original assertions + 3 invariants (`faulted ⇒ ¬running`, `¬running ⇒ no transfer in flight`, `blocked_cycles < effective limit`) | k-induction, depth 2, 4 and 8, each with yices, bitwuzla and boolector | **PASS** in all 9 runs, about 10 s each (23757443_0 to _8) |
| same | rIC3 IC3 | **PASS**, 10 s (23757443_10) |
| same | suprove, abc pdr | suprove UNKNOWN after 4926 s (23767637_1). pdr was still running at frame 919 after 53 min (23767637_0). |

### New properties

Every row below comes from the committed `73536f0` RTL with the real SRAM
models. "Unbounded" means an sby `prove` task finished with PASS. That is
k-induction (the base case from reset plus the induction step) or IC3/PDR.

| Property set | Unbounded proof (engine, time, Slurm id) | BMC from reset | Witnesses (covers) | Negative controls: mutant → first failing claim (step) |
|---|---|---|---|---|
| Host-port atomicity and read snapshots | **PASS**: boolector 10 s (23756999_2), yices 20 s (_0), bitwuzla 25 s (_1), rIC3 291 s (_4), abc pdr 656 s (_3) | 40: boolector 158 s, bitwuzla 275 s, btormc 367 s, abc bmc3 425 s, yices 874 s (23756997_2/_1/_4/_3/_0) | All 6 reached from reset by step 45 (yices, 574 s, 23756997_5). They include a stalled 8-nibble RX read of a word an engine pushed, a stalled timestamp read, a program-word write and a command after an abandoned partial word. | `host_early_commit` → `spec_cmd_eighth` (8). `host_keep_partial` → `spec_tx_eighth` (10). `host_live_read` → `spec_read_stable` (10). Yices and bitwuzla agree (23756999_6 to _11). |
| Program-load safety, control part | **PASS**: yices 4 s, bitwuzla 5 s, boolector 5 s (23756749_12/_13/_14) | 48: abc bmc3 253 s, boolector 348 s, bitwuzla 426 s, btormc 1222 s (23756748_10/_9/_8/_11) | All 5 reached from reset (yices, 1720 s, 23756748_12): an engine runs a committed two-word program to PC 1, the symbolic word is checked, an out-of-image fault, a rejected incomplete COMMIT. | `load_partial_commit` → `spec_run_committed` (26; 23756749_18). `load_start_uncommitted` → `spec_run_committed` (10; 23756749_20). |
| Program-load SRAM data integrity (`spec_word_*`) | not proved (see below) | 48, same runs as above | as above | covered by the control-part mutants |
| Mover conservation, quota and order | **PASS**: boolector 45 s (23760775_2), bitwuzla 57 s (23760892_1), yices 81 s (23760892_0), rIC3 389 s (23760775_4), abc pdr 1170 s (23760775_3) | 40: abc bmc3 81 s, btormc 355 s, yices 418 s, bitwuzla 433 s, boolector 603 s (23760772_3/_4/_0/_1/_2). From an arbitrary valid state, 24: abc bmc3 427 s (23760772_8), bitwuzla 3007 s (_6). | From reset (yices, depth 64, 23760772_10): the quota runs out at step 45, a tagged word is moved to TX at step 45 and pulled by the destination engine at step 54. The two-tag witness is not reachable within 64 steps, so sby reports this cover run as FAIL. From an arbitrary valid state, all 4 are reached by step 5, including two tags pulled in order (yices 14 s, 23760775_6). | `mover_wrong_data` → `spec_grant_data` (2). `mover_quota_on_eligible` → `spec_quota` (2). `mover_no_pop` → `spec_grant_pop` (2). Run from an arbitrary valid state; yices and bitwuzla agree (23760775_8 to _13). |
| Round-robin grant bound | **PASS** from every register state (no reset): boolector 4 s, yices 4 s, bitwuzla 6 s, abc pdr 10 s, suprove 12 s, rIC3 16 s (23756749_36 to _41) | 24: abc bmc3 18 s, yices 88 s (23756749_43/_42) | The worst case (granted after exactly 3 other grants) is reached for all 4 sources (yices, 23756749_44) | `rr_no_advance` → `spec_bound_grant` (4; 23756749_46) |
| Fault stickiness and OE release | **PASS**: yices 3 s, bitwuzla 7 s, boolector 12 s, abc pdr 74 s, rIC3 123 s, suprove 545 s (23756749_48 to _53) | 40: btormc 57 s, abc bmc3 58 s, boolector 128 s, bitwuzla 158 s, yices 220 s (23756748_26 to _30) | From reset, all 12 per-engine covers are reached by step 54 for every engine: fault then CLEAR, a driving engine faults, an issued HALT (yices, 2779 s, 23767451_0). From an arbitrary valid state they are reached by step 3 (yices 14 s, 23767452_0). | `fault_restart` → `spec_no_start_faulted` (43; 23756748_33). `fault_keeps_oe` → `spec_fault_stops` (37; 23756748_35). |
| Pin ownership and open-drain safety | **PASS**: boolector 5 s, yices 5 s, bitwuzla 7 s, abc pdr 78 s, rIC3 129 s, suprove 711 s (23756749_54 to _59) | 40: btormc 32 s, abc bmc3 61 s, boolector 168 s, bitwuzla 216 s, yices 261 s (23756748_37 to _41) | From reset, an open-drain pin pulls low (step 45) and is released by SET high (step 54) (23756748_42). Two engines driving pins at once is not reachable within 60 steps from reset, which needs two full program loads. From an arbitrary valid state all covers are reached (yices 5 s, 23767452_2). | `pin_own_overlap` → `spec_own_disjoint` (26; 23756749_60). `pin_od_drive_high` → `spec_open_drain_high` and `spec_pin_model_out` (45; 23756748_44). |

Notes on the table:

- The table lists only the engines that passed. suprove also ran on every
  `prove` task. It passed on `rr`, `fault` and `pin`, but on `host_prove`
  (23756999_5) and `mover_prove` (23760775_5) it gave up and returned
  UNKNOWN after about 80 minutes. Those claims are proved by the other
  engines listed.

- A cover run in sby "FAILs" when any cover statement is unreached within
  the depth. That happened twice:
  - `pin_cover@yices` (1550 s): only `cover_two_drivers` was unreached by
    step 60;
  - `mover_cover@yices` (6458 s): only `cover_two_tags_pulled_in_order` was
    unreached by step 64.

  Both are reached from an arbitrary valid state.
- The first run of the mover harness dropped *unarmed* tags on reset. Since
  the first cycle is a reset, every tag claim was vacuous from reset. Its
  BMC and prove tasks passed. The from-reset cover (`mover_cover`, depth 64,
  job 23756748_24) found the problem, because no tag could ever be armed.
  All mover results above come from the fixed harness (jobs 23760772,
  23760775, 23760892). The earlier results are set aside in the work area
  under `props2/superseded/`.
- Two harness bugs were found by k-induction and BMC in the same way, not by
  the design:
  - an out-of-range initial value of a read-mask register in
    `host_protocol.sv`;
  - FIFO pointer/level consistency, which was missing as a strengthening
    invariant.

  No design bug was found.

### Depth against time

The times are seconds from the solver's start to the moment it began step
*d*, so the first *d* steps were all checked. smtbmc reports this per step
and abc bmc3 per frame. rIC3, btormc and pono report only their total, shown
with `*`, which includes model generation.

`timing_isolation` (two-processor miter, 20 was the `formal/` bound):

| Engine | d=10 | d=20 | d=30 | d=40 | d=50 | d=60 | d=100 |
|---|---:|---:|---:|---:|---:|---:|---:|
| rIC3 BMC | | | | | | 1226* | 2643* |
| smtbmc bitwuzla | 107 | 478 | 1199 | 2238 | 4438 | 7038* | |
| smtbmc boolector | 162 | 1014 | 3650 | | | | |
| abc bmc3 | 326 | 2397 | | | | | |
| smtbmc yices | 1057 | 5376 | | | | | |
| smtbmc z3 | step 3 after 3571 s; cancelled | | | | | | |

`processor_invariants` (one processor from reset):

| Engine | d=16 | d=32 | d=48 | d=64 | d=96 | d=128 |
|---|---:|---:|---:|---:|---:|---:|
| abc bmc3 | 14 | 26 | 40 | 73 | 191 | 362* |
| btormc | | | | 135* | | 394* |
| rIC3 BMC | | | | 278* | | 3000* |
| smtbmc bitwuzla | 8 | 58 | 150 | 348* | | |
| smtbmc boolector | 9 | 115 | 327 | 700* | | |
| smtbmc yices | (d=40: 407, d=50: 1714; still running) | | | | | |
| smtbmc z3 | (d=10: 245; step 17 after 4867 s; cancelled) | | | | | |

`engine_safety` (one engine, free instruction stream):

| Engine | d=16 | d=32 | d=48 | d=64 | d=96 | d=128 |
|---|---:|---:|---:|---:|---:|---:|
| rIC3 BMC | | | | 31* | | 75* |
| btormc | | | | 37* | | 96* |
| smtbmc bitwuzla | 9 | 43 | 95 | 144 | 311 | 516* |
| abc bmc3 | 5 | 20 | 75 | 177 | 581 | 1158* |
| smtbmc yices | 8 | 60 | 221 | 609* | | |

`processor_inductive` BMC (from an arbitrary valid state):

| Engine | d=16 | d=32 | d=48 | d=96 |
|---|---:|---:|---:|---:|
| btormc | | | 67* | 292* |
| rIC3 BMC | | | 207* | 699* |
| smtbmc boolector | 96 | 412 | 1006* | |
| smtbmc bitwuzla | 88 | 497 | 1489* | |
| abc bmc3 | 38 | 307 | 1360* | 4021* |

**Engine observations.**

- For deep BMC on this design, the bit-level engines (rIC3's BMC, btormc and
  abc `bmc3`) beat every SMT solver. On the timing-isolation miter, rIC3
  checked 100 steps in 44 minutes, while bitwuzla needed 2 hours (7038 s) for 60.
- Among the SMT solvers, bitwuzla is fastest on the miter and boolector on
  single-processor properties. yices, the `formal/` default, is fast for
  k-induction but slow for deep BMC. z3 is not competitive.
- For the new unbounded proofs, plain k-induction at depth 4 with the
  `link_*` invariants takes seconds. IC3/PDR (abc pdr, rIC3, suprove) proves
  the same claims without help in 10 to 1200 s. On the pin and fault
  harnesses, PDR even succeeded on a first version that lacked a needed
  strengthening invariant.
- avy crashes on these models.

## What is still unproven

- **SRAM data integrity (`program_load.sv`, `spec_word_*`) is bounded
  only.** It is checked by BMC to depth 48 from reset, with four engines
  passing, and by the from-reset witness. Its k-induction proof would need
  the macro array contents, and no port exposes them, so the `prove` task
  runs without it (`-DFD_NO_WORD`). IC3 has not settled it either:
  - suprove gave up and returned UNKNOWN after 5312 s (23756749_17);
  - abc pdr was still running at frame 41 after about 2 hours (23756749_15);
  - rIC3 was still running (23756749_16). BMC 96 with rIC3, abc bmc3 and btormc was still
  running (23767782). One way to get an unbounded proof is to prove the
  vendor model's read/write behaviour once at macro level, then use it as an
  abstraction.
- **Some witnesses exist only from an arbitrary valid state.** Two engines
  driving pins at once, and two tagged words pulled in order, need more host
  traffic than the from-reset cover depth (60 to 64) allows: two full program
  loads, or a four-instruction program. Their witnesses start from a
  state that satisfies every invariant proved in `fd_invariants.vh`, which
  is not necessarily a reachable state. From-reset witnesses exist for every
  other cover, including the fault/HALT covers of all four engines and a
  single tagged word, moved at step 45 and pulled at step 54.
- **Liveness is not claimed.** The mover claims are safety only: nothing is
  lost, duplicated, corrupted or reordered. Eventual delivery depends on
  host service and destination space, as `docs/architecture.md` says. The
  grant bound is conditional on continuous eligibility.
- **The debug and fd netlists are not checked against `src/`.** Like
  `formal/`, every processor-level result here is about
  `Processor.create_refinement ~debug:true` plus observation ports, not the
  committed `src/protocol_emulator_core.v`. Their equivalence is argued from
  the generator (debug outputs and names only), not checked. A sequential
  equivalence job (for example ABC `dsec`/`scorr` on a miter of the two
  netlists) is the missing link.
- **The host is modelled as synchronous.** The host-port proofs assume the
  documented synchronous host (inputs change between edges). Nothing is said
  about metastability, gate-level behaviour, timing closure or the physical
  macros.
- **Some runs had not finished when this was written.** They are recorded,
  not claimed:
  - boolector on `timing_isolation` BMC 60;
  - abc bmc3 and btormc on `timing_isolation` BMC 100;
  - yices on `processor_invariants` BMC 64;
  - abc pdr and rIC3 on the unmodified `engine_safety`;

  The z3 runs, and yices on the timing-isolation miter, were cancelled as
  too slow. Their partial curves are in the work area
  (`deep/cancelled_partial_curves.json`).

## What should move into `formal/` and CI

These are cheap and unbounded, and each would add a job of about a minute
or less to `.github/workflows/formal.yaml`. They need `generate_fd.ml` (or,
better, the `hardcaml/` change `formal/README.md` already proposes, so that
`Processor.create_with_memory ~debug:true` exports the observed state
itself).

1. `rr_prove`: 4 s. It needs only the existing `dbg_*` ports and
   `processor_debug.v`.
2. `fault_prove`, `pin_prove` and `load_prove` (`-DFD_NO_WORD`): 3 to 12 s
   each with yices, depth 4.
3. `host_prove`: 10 to 25 s. It needs the six host-port registers exported.
4. `mover_prove`: 45 to 80 s. It needs the FIFO arrays and pointers
   exported.
5. `engine_safety_inductive` replaces `engine_safety`'s BMC 24 with an
   unbounded proof in about 10 s. The three invariants could go straight
   into `formal/engine_safety.sv`.
6. One mutant per harness as an `expect fail` job, with the same pattern as
   `formal/`'s `neg_mutant`.

For the deep BMC jobs that stay bounded (`timing_isolation_bmc`), switching
CI's engine from `smtbmc bitwuzla` to `aiger rIC3` would take depth 20 to
about 60 in the same wall time. Keep one SMT engine as a cross-check,
because the AIGER witness replay currently fails in sby for these miters.

## Reproducing

```sh
formal_depth/run.sh generate                      # snapshot 73536f0, all netlists
formal_depth/run.sh submit $WORK                  # every job, one Slurm array per class
formal_depth/run.sh summarize $WORK --tsv summary.tsv --curves curves.json
python3 formal_depth/portfolio.py curves $WORK --jobs timing_isolation --depths 10,20,40,60,100
```

`formal_depth/README.md` describes each file, and the soundness notes cover
the observation ports, `$shiftx`, sampled assertions and rIC3's BMC status.
