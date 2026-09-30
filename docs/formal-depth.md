# Formal depth: deeper proofs and new properties

This page extends the formal story in [`formal/README.md`](../formal/README.md)
with MIT Engaging cluster time. It records what was run, against which
revision, how deep each run went and how long it took, and what is still
unproven. The harnesses, job table and drivers are in
[`formal_depth/`](../formal_depth/README.md). Nothing in `formal/` was
changed.

**Revision.** Two campaigns ran every job. The second, on 2026-09-30, used
the committed tree at `56f4b20`; its results are in
[Results at `56f4b20`](#results-at-56f4b20-second-campaign). The first, from
2026-09-25, used `73536f0`; its results are kept as history in
[Results at `73536f0`](#results-at-73536f0-first-campaign-history). Each
campaign exported its commit with `git archive` into a private snapshot, so
edits that other work made to the working tree during the runs could not
leak in. The RTL was regenerated from that snapshot's `hardcaml/`:

- `formal/run.sh --generate-only` produced `processor_fv.v`,
  `processor_debug.v`, `engine.v` and `fifo.v`.
- `formal_depth/gen/generate_fd.ml` produced `protocol_processor_fd`.

`formal_depth/run.sh` now takes the revision from `FD_REV` (default `HEAD`)
and records the commit in every result.

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

## Results at `56f4b20` (second campaign)

**Why a second campaign.** Six of the 13 mutant substitution sites stopped
matching the Hardcaml source: `load_partial_commit` at `c377d97`, the other
five at `131e793` (see "Mutant sites" below). From `c377d97` on, `formal_depth/run.sh generate` could not build the negative controls
of any newer commit, and `FD_REV` stayed pinned to `73536f0`, so neither the
deeper proofs nor their controls ran on the committed design. The sites were
repaired, and every job of [`formal_depth/jobs.py`](../formal_depth/jobs.py)
was run again on `56f4b20`: 56 jobs, 192 sby tasks (one per job and engine).
191 tasks finished; one bitwuzla cross-check of a negative control was
cancelled (see the pin row of the table below).
The results are in
[`formal_depth/results/56f4b20/summary.tsv`](../formal_depth/results/56f4b20/summary.tsv)
(its `revision` column names the commit) and `depth_curves.json` in the same
directory. The Slurm arrays are 24426336 and 24426337 (every task with the
41,400 s limit of the first campaign) and 24426338 and 24426339 (the 24
tasks listed under "Long tail" below, with a 14,400 s limit).

### What the jobs read

- **The netlists are byte-identical to the first campaign's.**
  `processor_fv.v`, `processor_debug.v`, `engine.v`, `fifo.v`,
  `processor_fv_mutant.v`, `processor_fd.v` and all 13 mutant netlists
  generated at `56f4b20` equal the files that the first campaign generated at
  `73536f0` (generation job 24425593). A second generation with the final
  scripts gave the same files again (job 24427432). The Hardcaml source
  changed in between (variant and timing options that the design of record
  leaves off, the line unit of a variant, and the refactor of `dd7dae3`), but
  the elaborated design of record did not.
- **The `formal/` harnesses of the deeper runs changed.** `engine_safety.sv`,
  `timing_isolation.sv`, `processor_invariants.sv`, `processor_inductive.sv`
  and `fifo_conservation.sv` gained branches for design variants. They are
  selected by preprocessor defines and by parameters whose defaults are the
  design of record. The deeper runs read the `56f4b20` versions, so
  `engine_safety_inductive.sv` differs from the first campaign's copy. The
  three invariants it adds are unchanged.
- **The `formal_depth/` harnesses are unchanged.**

### Mutant sites

| Mutant | Why the old site stopped matching | Site at `56f4b20` |
|---|---|---|
| `host_early_commit`, `host_live_read` | Since `131e793`, `host.ml` also holds the `host_nibble_slots` implementation of the host port, a timing knob that the design of record leaves off. It has its own copy of the write-strobe line and of the read multiplexer, so each pattern occurred twice. | The same lines, with a neighbouring line of the shifting-buffer implementation (the one that is elaborated) as context. |
| `load_partial_commit` | The COMMIT rule's "payload = words loaded" comparison gained a `narrow_image_regs` branch (`c377d97`). | The whole conjunct, both branches. |
| `mover_wrong_data`, `rr_no_advance` | `grant_index` and `grant_valid` became signals instead of wire variables when the grant gained its `split_command_decode` form (`131e793`). | The same substitutions without `.value`. |
| `mover_quota_on_eligible` | The quota decrement is wrapped in the `keep_counter_increments` helper (`131e793`). | Only the guard: `when_ grants.(k)` becomes `when_ eligible.(k)`. |

Where a new pattern also matches the older revisions it replaces the old one;
otherwise the old pattern is kept as an alternative. `mutants.py` uses the
first alternative that matches exactly once. Four checks back the repair:

- Every site resolves to exactly one alternative at `73536f0`, at
  `56f4b20`, and at each of the five commits between them that changed
  `hardcaml/lib` (`c377d97`, `fb79f31`, `131e793`, `76a81f5`, `dd7dae3`).
- On a `git archive` of `73536f0`, the updated `mutants.py` reproduces the
  first campaign's 13 netlists byte for byte (job 24425594).
- A mutant netlist equal to `processor_fd.v` is now an error.
- `generate` now also writes
  [`mutant_cones.txt`](../formal_depth/results/56f4b20/mutant_cones.txt)
  (`formal_depth/gen/mutant_cones.py`). For each mutant it lists the
  next-state functions and outputs whose combinational cone differs from
  `processor_fd.v`, by structural hashing with the state elements as
  leaves. Each list is the fan-out of the substituted expression:

| Mutant | Sinks that differ (state-element inputs and outputs; count) |
|---|---|
| `rr_no_advance` | The round-robin cursor (1). |
| `host_keep_partial` | The host write index (1). |
| `pin_od_drive_high` | `uio_out` (1). |
| `mover_quota_on_eligible` | The four route quotas (4). |
| `mover_wrong_data` | The write data of the four TX FIFO memories, `dbg_dma_data` and `dbg_tx_data` (6). |
| `mover_no_pop` | The four RX read pointers and RX levels, and `dbg_rx_pop` (9). |
| `fault_keeps_oe` | `running` and the logical enables of the four engines, and `uio_oe` (9). |
| `host_live_read` | `uo_out`. Also the six `fd_host_*` ports, which `generate_fd --lenient` ties to 0 in this mutant, and the clock and data inputs of the read-snapshot register, which has no reader left and is removed (9). |
| `fault_restart`, `load_start_uncommitted`, `load_partial_commit`, `pin_own_overlap` | The same 195 for all four: the fan-out of the command-acceptance signal, that is, every command's effect and, through START and STOP, every engine register (195). |
| `host_early_commit` | The same 195 (the snapshot register's data input appears as an unnamed register paired across the change), plus the SRAM read and write enables (16 macro pins and 4 observation ports) and `dbg_host_tx`, which carry program and TX words, and the six `fd_host_*` ports, tied to 0 as above (222). |

The `fd_host_*` ports are read only by `link_*` assertions, which the
mutant runs drop (`-DFD_SPEC_ONLY`).

### Results

PASS appears only for runs that were observed to finish with that status.
Times are wall-clock seconds for the whole sby task, including yosys model
generation, on 2 CPUs of `mit_preemptable` or `mit_normal`. They vary with
the node and the file-system load: `load_prove` took 70 to 90 s here and
4 to 5 s in the first campaign, most of the difference in yosys. Slurm ids
are `array_task`.

**Every result of the first campaign was reproduced.** Each unbounded proof
passed again with at least the engines that passed at `73536f0`, and each
BMC reached the same depth. Each cover that the first campaign reached was
reached again at the same step, and each negative control failed on the
same claim at the same step. Only the run times differ.

**Deeper runs of the `formal/` jobs.**

| Job | `formal/` today | Reached | Engines that passed (time, Slurm id) |
|---|---|---|---|
| `timing_isolation_bmc` | BMC 20 | **BMC 100** | 100: rIC3 2534 s (24426336_56). 60: rIC3 1207 s (24426336_2), bitwuzla 7903 s (_0), abc bmc3 20,544 s (_1). |
| `engine_safety` | BMC 24 | **unbounded**, plus BMC 128 | Unbounded: see the next table. 128: btormc 61 s (24426337_96), rIC3 72 s (_94), bitwuzla 175 s (_97), abc bmc3 622 s (_95). 64: btormc 33 s (24426337_4), rIC3 48 s (_6), pono 50 s (_5), boolector 81 s (_1), bitwuzla 108 s (_2), abc bmc3 209 s (_3), yices 540 s (_0). |
| `processor_invariants_bmc` | BMC 24 | **BMC 128** | 128: abc bmc3 180 s (24426336_58), btormc 420 s (_59), rIC3 1614 s (_57). 64: abc bmc3 54 s (24426336_6), btormc 84 s (_7), pono 136 s (_8), rIC3 172 s (_9), bitwuzla 315 s (_5), boolector 426 s (_4), yices 15,638 s (_3). |
| `processor_inductive_bmc` | BMC 8 | **BMC 96** | 96: rIC3 370 s (24426336_60), btormc 382 s (_62), abc bmc3 2503 s (_61). 48: btormc 58 s (24426336_13), pono 98 s (_14), rIC3 128 s (_15), abc bmc3 776 s (_12), boolector 926 s (_10), bitwuzla 1397 s (_11). |
| `fifo_conservation` | unbounded (abc pdr) | unbounded, 3 engines | abc pdr 260 s (24426337_15), rIC3 360 s (_16), suprove 1371 s (_18). |

**`engine_safety` made unbounded.**

| Harness | Method | Result (time, Slurm id) |
|---|---|---|
| unmodified `formal/engine_safety.sv` | IC3 (suprove) | **PASS**, unbounded, 99 s (24426337_8) |
| unmodified | k-induction, depths 8, 16 and 32, yices and bitwuzla | UNKNOWN in all 6 runs (24426337_9 to _14): the induction step fails on an unreachable state, as in the first campaign. Five of the six counterexamples to induction hit the fault-release assertion (`engine_safety.sv:118`, line 66 at `73536f0`). Bitwuzla at depth 16 hit the blocked-cycle limit assertion (`engine_safety.sv:201`) instead, through the counter wrap that the third strengthening invariant excludes. |
| unmodified | abc pdr, rIC3 IC3 | both reached the 14,400 s limit without a verdict (24426339_1, _2); in the first campaign they reached the 41,400 s limit |
| unmodified | avy | tool crash, as in the first campaign; not a result (24426337_7) |
| `engine_safety_inductive` (original assertions + 3 invariants) | k-induction, depth 2, 4 and 8, each with yices, bitwuzla and boolector | **PASS** in all 9 runs, 1 to 4 s each (24426337_83 to _91) |
| same | rIC3 IC3 | **PASS**, 12 s (24426337_92) |
| same | suprove, abc pdr | suprove UNKNOWN after 5092 s (24426337_93; the Slurm record also shows an out-of-memory event); abc pdr ran out of memory (8 GB) after 11,735 s (24426339_5), as in the first campaign |

**New properties.** "Unbounded" means an sby `prove` task finished with
PASS: k-induction at depth 4 (the base case from reset plus the induction
step) or IC3/PDR.

| Property set | Unbounded proof (engine, time, Slurm id) | BMC from reset | Witnesses (covers) | Negative controls: mutant → first failing claim (step) |
|---|---|---|---|---|
| Host-port atomicity and read snapshots | **PASS**: yices 6 s (24426337_19), bitwuzla 8 s (_20), boolector 12 s (_21), rIC3 174 s (_23), abc pdr 670 s (_22). suprove UNKNOWN after 4542 s (_24). | 40: boolector 195 s (24426336_18), bitwuzla 233 s (_17), abc bmc3 239 s (_19), btormc 406 s (_20), yices 730 s (_16) | All 6 reached from reset by step 45 (yices 370 s, 24426336_21) | `host_early_commit` → `spec_cmd_eighth` (8). `host_keep_partial` → `spec_tx_eighth` (10). `host_live_read` → `spec_read_stable` (10). Yices and bitwuzla agree (24426337_25 to _30). |
| Program-load safety, control part | **PASS**: yices 70 s (24426337_31), boolector 78 s (_33), bitwuzla 90 s (_32) | 48: abc bmc3 129 s (24426336_25), bitwuzla 320 s (_23), boolector 457 s (_24), btormc 728 s (_26), yices 1942 s (_22) | All 4 reached from reset by step 45 (yices 1316 s, 24426336_27) | `load_partial_commit` → `spec_run_committed` (26). `load_start_uncommitted` → `spec_run_committed` (10). Yices and bitwuzla agree (24426337_35 to _38). |
| Program-load SRAM data integrity (`spec_word_*`) | not proved (see "What is still unproven") | 48, same runs as above. 96: rIC3 11,021 s (24426336_63); abc bmc3 and btormc reached the 14,400 s limit (24426338_16, _17) | as above | covered by the control-part mutants |
| Mover conservation, quota and order | **PASS**: bitwuzla 31 s (24426337_40), boolector 46 s (_41), yices 80 s (_39), rIC3 162 s (_43), abc pdr 1282 s (_42). suprove UNKNOWN after 5287 s (_44). | 40: abc bmc3 74 s (24426336_31), bitwuzla 408 s (_29), yices 413 s (_28), btormc 480 s (_32), boolector 517 s (_30). From an arbitrary valid state, 24: abc bmc3 258 s (24426336_36), bitwuzla 1410 s (_34), boolector 2314 s (_35), yices 19,073 s (_33). | From reset (yices, depth 64, 6145 s, 24426336_37): the quota runs out and a tagged word is moved at step 45, and the word is pulled at step 54. As in the first campaign, the two-tag witness is not reached within 64 steps, so sby reports this cover run as FAIL. From an arbitrary valid state, all 4 are reached by step 5 (yices 21 s, 24426337_45). | `mover_wrong_data` → `spec_grant_data` (2). `mover_quota_on_eligible` → `spec_quota` (2). `mover_no_pop` → `spec_grant_pop` (2). Run from an arbitrary valid state; yices and bitwuzla agree (24426337_47 to _52). |
| Round-robin grant bound | **PASS** from every register state (no reset): yices 3 s (24426337_53), bitwuzla 4 s (_54), boolector 7 s (_55), suprove 12 s (_58), rIC3 17 s (_57), abc pdr 21 s (_56) | 24: abc bmc3 9 s (24426337_60), yices 70 s (_59) | The worst case is reached for all 4 sources at step 4 (yices 8 s, 24426337_61) | `rr_no_advance` → `spec_bound_grant` (4). Yices and bitwuzla agree (24426337_63, _64). |
| Fault stickiness and OE release | **PASS**: yices 4 s (24426337_65), bitwuzla 5 s (_66), boolector 6 s (_67), abc pdr 56 s (_68), rIC3 410 s (_69), suprove 870 s (_70) | 40: btormc 29 s (24426336_42), abc bmc3 61 s (_41), boolector 104 s (_40), bitwuzla 230 s (_39), yices 252 s (_38) | From reset, all 12 per-engine covers are reached by step 54 (yices 2412 s, 24426336_43). From an arbitrary valid state, by step 2 (yices 12 s, 24426337_71). | `fault_restart` → `spec_no_start_faulted` (43; 24426336_44). `fault_keeps_oe` → `spec_fault_stops` (37; 24426336_46). Bitwuzla agrees on both, at the same steps (`fault_restart` 23,676 s, 24426336_45; `fault_keeps_oe` 16,764 s, _47). |
| Pin ownership and open-drain safety | **PASS**: boolector 6 s (24426337_75), yices 6 s (_73), bitwuzla 8 s (_74), rIC3 92 s (_77), abc pdr 112 s (_76), suprove 642 s (_78) | 40: btormc 36 s (24426336_52), abc bmc3 69 s (_51), boolector 116 s (_50), bitwuzla 150 s (_49), yices 349 s (_48) | From reset, an open-drain pin pulls low at step 45 and is released by SET high at step 54 (24426336_53). As in the first campaign, two engines driving pins at once is not reached within 60 steps from reset, so that cover run is reported as FAIL (1949 s). From an arbitrary valid state, all 3 covers are reached by step 2 (yices 7 s, 24426337_79). | `pin_own_overlap` → `spec_own_disjoint` (26; 24426337_81). `pin_od_drive_high` → `spec_open_drain_high` and `spec_pin_model_out` (45; 24426336_54). Bitwuzla agrees on `pin_own_overlap` (10,567 s, 24426337_82). On `pin_od_drive_high` bitwuzla's engine log reports both failures (`spec_open_drain_high`, `spec_pin_model_out`) at step 45 after 3 min 10 s and 3 min 18 s, the same claims and step as yices; smtbmc then did not finish writing the VCD trace, and the task was cancelled after 8 h 34 min with no result recorded by sby (24426336_55). The first campaign's bitwuzla run of this control shows the same pattern. |

Notes on the table:

- The failure checks of the bit-level BMC engines were repeated on the
  timing-isolation miter at depth 12, on `formal/`'s two negative controls
  (`neg_mutant`, `neg_pull`). rIC3, abc bmc3 and btormc each returned FAIL
  on both. As in the first campaign, sby ends the rIC3 and abc runs in
  ERROR because it cannot replay their AIGER witness ("witness signal
  mismatch for a.dut.ena"), and btormc produced a full FAIL trace
  (24426336_64 to _69).
- For `fault_prove` and `pin_prove`, Slurm recorded out-of-memory kills of
  some processes of the rIC3 task at the 8 GB limit (24426337_69, _77). rIC3
  itself returned PASS (exit code 20).
- avy crashed again on `fifo_conservation` (24426337_17).

### Long tail

In the first campaign, these 24 tasks reached the 41,400 s limit, ran out
of memory after hours, or left no result (the z3 runs and yices on the
timing-isolation miter were cancelled as too slow, and the bitwuzla cover
runs did not complete). In the second they ran with a 14,400 s limit
(arrays 24426338 and 24426339). Five of them were
preempted on `mit_preemptable` and restarted once. None failed an
assertion, and no claim above depends on any of them.

| Tasks | Outcome at `56f4b20` (Slurm id) |
|---|---|
| `timing_isolation` BMC 60: btormc, pono | reached the limit (24426338_3, _4) |
| `timing_isolation` BMC 60: yices, z3 | reached the limit after checking steps 0 to 23 (yices) and 0 to 5 (z3) (24426338_0, _2) |
| `timing_isolation` BMC 60: boolector | out of memory (32 GB) after 13,396 s, while checking step 46 (24426338_1). First campaign: out of memory after 13,173 s at step 45. |
| `timing_isolation` BMC 100: btormc; abc bmc3 | btormc reached the limit (24426338_15); abc bmc3 reached it too, with frame 44 the last in its log (24426338_14) |
| `engine_safety` BMC 64: z3 | reached the limit after checking steps 0 to 52 (24426339_0) |
| `processor_invariants` BMC 64: z3 | reached the limit after checking steps 0 to 19 (24426338_5) |
| `processor_inductive` BMC 48: yices, z3 | reached the limit after checking steps 0 to 35 (yices) and 0 to 10 (z3) (24426338_6, _7) |
| unmodified `engine_safety`, unbounded: abc pdr, rIC3 | reached the limit (24426339_1, _2) |
| `engine_safety_inductive`, unbounded: abc pdr | out of memory (8 GB) after 11,735 s (24426339_5) |
| program-load data integrity, BMC 96: abc bmc3, btormc; unbounded: abc pdr, rIC3 | all four reached the limit (24426338_16, _17; 24426339_3, _4) |
| mover BMC 24 from an arbitrary valid state: btormc | reached the limit (24426338_10) |
| host-port covers from reset: bitwuzla | all 6 covers reached at the same steps as with yices, the last (step 45) after 4917 s; the task then did not finish writing that trace before the limit (24426338_8) |
| load, mover, fault and pin covers from reset: bitwuzla | reached the limit with 2 of 4, 1 of 4, 1 of 12 and 1 of 3 covers reached (24426338_9, _11, _12, _13) |

## Results at `73536f0` (first campaign, history)

This section is the first campaign's record, as first written, with the
outcomes of its late runs added in "What is still unproven". The second
campaign, above, supersedes it as the current status; its netlists are
byte-identical to these.

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
| unmodified | abc pdr, rIC3 IC3 | not converged: both reached the 41,400 s limit without a verdict (23752067_8, _9; the Slurm record of _9 also shows an out-of-memory event). When this page was first written they were still running, and pdr was at frame 6 after 77 min. |
| unmodified | avy | tool crash (segfault, rc 139); not a result |
| `engine_safety_inductive` = original assertions + 3 invariants (`faulted ⇒ ¬running`, `¬running ⇒ no transfer in flight`, `blocked_cycles < effective limit`) | k-induction, depth 2, 4 and 8, each with yices, bitwuzla and boolector | **PASS** in all 9 runs, about 10 s each (23757443_0 to _8) |
| same | rIC3 IC3 | **PASS**, 10 s (23757443_10) |
| same | suprove, abc pdr | suprove UNKNOWN after 4926 s (23767637_1). abc pdr ended in an error after 16,366 s, out of memory (23767637_0); when this page was first written it was at frame 919 after 53 min. |

### New properties

Every row below comes from the committed `73536f0` RTL with the real SRAM
models. "Unbounded" means an sby `prove` task finished with PASS. That is
k-induction (the base case from reset plus the induction step) or IC3/PDR.

| Property set | Unbounded proof (engine, time, Slurm id) | BMC from reset | Witnesses (covers) | Negative controls: mutant → first failing claim (step) |
|---|---|---|---|---|
| Host-port atomicity and read snapshots | **PASS**: boolector 10 s (23756999_2), yices 20 s (_0), bitwuzla 25 s (_1), rIC3 291 s (_4), abc pdr 656 s (_3) | 40: boolector 158 s, bitwuzla 275 s, btormc 367 s, abc bmc3 425 s, yices 874 s (23756997_2/_1/_4/_3/_0) | All 6 reached from reset by step 45 (yices, 574 s, 23756997_5). They include a stalled 8-nibble RX read of a word an engine pushed, a stalled timestamp read, a program-word write and a command after an abandoned partial word. | `host_early_commit` → `spec_cmd_eighth` (8). `host_keep_partial` → `spec_tx_eighth` (10). `host_live_read` → `spec_read_stable` (10). Yices and bitwuzla agree (23756999_6 to _11). |
| Program-load safety, control part | **PASS**: yices 4 s, bitwuzla 5 s, boolector 5 s (23756749_12/_13/_14) | 48: abc bmc3 253 s, boolector 348 s, bitwuzla 426 s, btormc 1222 s (23756748_10/_9/_8/_11) | All 4 reached from reset (yices, 1720 s, 23756748_12): an engine runs a committed two-word program to PC 1, the symbolic word is checked, an out-of-image fault, a rejected incomplete COMMIT. (This row said "All 5" until the second campaign; `program_load.sv` has four covers, and the log shows four reached.) | `load_partial_commit` → `spec_run_committed` (26; 23756749_18). `load_start_uncommitted` → `spec_run_committed` (10; 23756749_20). |
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
| smtbmc boolector | 162 | 1014 | 3650 | 10043 | (out of memory after step 45, 13173 s) | | |
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
| smtbmc yices | 2 | 117 | 950 | 16162* | | |
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
  only.** In the first campaign (`73536f0`) it was checked by BMC to depth
  48 from reset, with four engines passing, by BMC to depth 96 with rIC3
  (13,703 s, 23767782_0), and by the from-reset witness. At depth 96, abc bmc3 and btormc reached the
  41,400 s limit (23767782_1, _2). Its k-induction proof would need the
  macro array contents, and no port exposes them, so the `prove` task runs
  without it (`-DFD_NO_WORD`). IC3 has not settled it either:
  - suprove gave up and returned UNKNOWN after 5312 s (23756749_17);
  - abc pdr and rIC3 reached the 41,400 s limit without a verdict
    (23756749_15, _16; the Slurm record of _16 also shows an out-of-memory
    event). When this page was first written, pdr was at frame 41 after
    about 2 hours.

  The second campaign (`56f4b20`) gives the same picture: BMC 48 with five
  engines (24426336_22 to _26), BMC 96 with rIC3 (11,021 s,
  24426336_63) and the from-reset witness (24426336_27). abc bmc3 and btormc
  reached the 14,400 s limit at depth 96 (24426338_16, _17). On the
  unbounded task, abc pdr and rIC3 reached the same limit (24426339_3, _4),
  and suprove returned UNKNOWN after 8601 s (24426337_34).

  One way to get an unbounded proof is to prove the vendor model's
  read/write behaviour once at macro level, then use it as an abstraction.
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
- **Only `processor_fv.v` is checked against `src/`.** Like `formal/`,
  every processor-level result here is about
  `Processor.create_refinement ~debug:true` plus observation ports, not the
  committed `src/protocol_emulator_core.v`.
  - `processor_fv.v`, which the `timing_isolation` runs read, is checked.
    The copy these runs generated at `73536f0` is byte-identical (sha256
    `2a039bae…`) to the one generated at `c118027`, which
    `tools/timing/cert/equiv_src.py` proved sequentially equivalent to the
    committed `src/project.v` and `src/protocol_emulator_core.v` on the chip
    ports (ABC `dprove`, Slurm job 23987598;
    [timing-certificates.md](timing-certificates.md), section 6). The
    `formal` workflow generates the same file on `24f31f0` ([results.md](results.md)
    R95). This check came after this page was first written. The copy that
    the second campaign generated at `56f4b20` is byte-identical as well.
  - `processor_debug.v` and `protocol_processor_fd`, which the other
    processor-level runs read, are not checked. Their equivalence is argued
    from the generator (debug outputs and names only). The same kind of
    sequential equivalence job is the missing link.
- **The host is modelled as synchronous.** The host-port proofs assume the
  documented synchronous host (inputs change between edges). Nothing is said
  about metastability, gate-level behaviour, timing closure or the physical
  macros.
- **Runs of the first campaign that finished after this page was
  written.** As first written, this item listed runs that were still
  running, recorded but not claimed.
  All of them have ended. The outcomes below are from each task's result
  file in the work area (`deep/`, `deep2/`, `props2/`, `props4/`, `props6/`,
  `engind2/`, `results/<task>.json`). `formal_depth/results/summary.tsv`
  does not contain these rows. None failed an assertion. The last two rows
  were added during the second campaign, from result files that this list
  had missed.

  | Run | Outcome |
  |---|---|
  | boolector, `timing_isolation` BMC 60 | ERROR after 13,173 s, out of memory, at step 45 (23752066_1) |
  | abc bmc3 and btormc, `timing_isolation` BMC 100 | both reached the 41,400 s limit (23757105_1, _2); rIC3 had passed BMC 100 (above) |
  | yices, `processor_invariants` BMC 64 | **PASS**, 16,162 s (23752066_8) |
  | abc pdr and rIC3, unmodified `engine_safety` | both reached the 41,400 s limit (23752067_8, _9) |
  | abc pdr and rIC3, program-load data integrity, unbounded | both reached the 41,400 s limit (23756749_15, _16) |
  | rIC3, abc bmc3 and btormc, program-load data integrity BMC 96 | rIC3 **PASS**, 13,703 s (23767782_0); abc bmc3 and btormc reached the 41,400 s limit (_1, _2) |
  | abc pdr, `engine_safety_inductive` | ERROR after 16,366 s, out of memory (23767637_0) |
  | abc bmc3, `timing_isolation` BMC 60 | **PASS**, 26,803 s (23752066_4) |
  | yices, mover BMC 24 from an arbitrary valid state | **PASS**, 32,641 s (23760772_5); boolector had passed in 5662 s (23760772_7, in `summary.tsv`) |

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
FD_REV=56f4b20 formal_depth/run.sh generate       # snapshot (default HEAD), all netlists, mutant_cones.txt
formal_depth/run.sh submit $WORK                  # every job, one Slurm array per class
formal_depth/run.sh summarize $WORK --tsv summary.tsv --curves curves.json
python3 formal_depth/portfolio.py curves $WORK --jobs timing_isolation --depths 10,20,40,60,100
```

`formal_depth/README.md` describes each file, and the soundness notes cover
the observation ports, `$shiftx`, sampled assertions and rIC3's BMC status.
