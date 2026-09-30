# Mutation score push

This page continues the RTL mutation campaign of
[verification-campaign.md](verification-campaign.md) ("Mutation testing" and
"Gap closure"). It re-measures the survivors against the committed suite,
classifies them with formal equivalence checks, adds directed tests for the
ones that the tests can reach, and reports the new score. Added later: section
4.5 (a longer ABC cap), section 6 (the scripts in the repository, checked by
re-running them, and a re-run on the committed test tree) and section 7 (the
same procedure on design variant `diet4` under the 102-test suite).

The mutants, the prepared core (`base.il`) and the per-mutant build are those of
the campaign (2,420 mutants of `src/protocol_emulator_core.v` at commit
`73536f0`, Yosys `mutate`). The core is unchanged at `c118027` (tag
`v0.1-hardened`; `src/protocol_emulator_core.v` sha256 `26a873db…` in both), so
the mutants apply to the design of record as committed.

Score, as in the campaign: killed / (mutants − proven equivalent). A mutant
counts as proven equivalent only when a formal check proves that its pins
(`uo_out`, `uio_out`, `uio_oe`) equal the unmutated core's forever from reset,
for every input. Mutants that are argued (not proven) to be unobservable are
listed separately and stay in the denominator.

## Result

| | gap closure (`c118027`) | this push |
|---|---:|---:|
| mutants | 2,420 | 2,420 |
| proven equivalent, campaign method (`equiv_induct`) | 116 | 116 |
| proven equivalent, methods added here | – | 8 |
| killed | 2,060 | 2,223 |
| **score** | 89.4 % (2,060 / 2,304) | **96.8 % (2,223 / 2,296)** |
| survivors, not proven equivalent | 244 | 73 |
| of those, argued unobservable (listed below, not counted) | – | 73 |

The score is 2,223 / (2,420 − 124) = 96.82 %. Every one of the 73 remaining
survivors has a stated argument for why no test can observe it (section 5); the
arguments are not proofs, so these mutants stay in the denominator.

Later addition (section 4.5): the miter of section 4.2 with a longer ABC cap
proves 26 of the 73 equivalent. With those proofs the score is 2,223 / (2,420 −
150) = 97.93 %, with 47 survivors. The 96.82 % above, and the tables of this
section, are the push as run.

By region (proven equivalent includes both methods):

| region | mutants | proven equivalent | killed (gap closure) | killed (now) | score (now) | survivors |
|---|---:|---:|---:|---:|---:|---:|
| engine_ctrl | 550 | 27 | 456 | 488 | 93.3 % | 35 |
| host | 400 | 29 | 356 | 366 | 98.7 % | 5 |
| events | 300 | 32 | 236 | 267 | 99.6 % | 1 |
| mover | 250 | 6 | 221 | 244 | 100 % | 0 |
| pins | 250 | 7 | 221 | 237 | 97.5 % | 6 |
| engine_xfer | 200 | 9 | 161 | 178 | 93.2 % | 13 |
| engine_data | 180 | 2 | 145 | 172 | 96.6 % | 6 |
| fifo | 130 | 7 | 121 | 121 | 98.4 % | 2 |
| shared | 90 | 2 | 81 | 87 | 98.9 % | 1 |
| imem | 60 | 3 | 52 | 53 | 93.0 % | 4 |
| timestamp | 10 | 0 | 10 | 10 | 100 % | 0 |
| **total** | **2,420** | **124** | **2,060** | **2,223** | **96.8 %** | **73** |

## 1. Recomputation against the committed suite

The gap-closure score combined several stages (new modules on the old
survivors, a re-check of the random-only kills, carried-over kills). Here the
whole committed suite ran on every mutant in one stage (`head`): the nine
default modules of `test/Makefile` in their default order, stopping at the
first failing module, on a test tree taken with `git archive c118027`
(Icarus Verilog 14.0, cocotb 2.0.1, `WAVES=none`).

| | result |
|---|---|
| runs | 2,422 (2,420 mutants, the unmodified core `orig`, the no-op mutant `0`) |
| controls | `orig` and `0` survived |
| killed | 2,060 (first failing module: `test_random` 692, `test_smoke` 577, `test_legacy` 290, `test_protocols` 252, `test_directed` 88, `test_timewarp` 83, `test_flagship` 33, `test_mover` 28, `test_counters` 17) |
| survived | 360: the 244 survivors and the 116 proven-equivalent mutants of the gap closure, exactly |
| jobs | 23974560 (array, 31 × 8 CPUs), 23976875, 23979515 (`mit_quicktest`); 33.5 CPU-hours |

The per-region kill counts match the gap-closure table, so its 2,060 kills and
89.4 % are reproduced by a single uniform run.

## 2. Survivor analysis

For each survivor, the mutant netlist was diffed against the unmutated
round-trip netlist (the changed assignment, with its select condition decoded:
opcode compares, the engine's STOP/START/active terms, the fault condition,
transfer and WAIT counters). A reach/infect/propagate run (`kill-rip`: the
unmutated core drives the pins, the mutant runs beside it and every register is
compared each cycle) on the 46 survivors outside `engine_ctrl`, under the new
tests, separated "never infected" from "infected but never propagated" (job
23984727). In that run the harness could not apply its time warp to the
wrapper, so `test_kill_exact_timeout_limits` and `test_kill_time_high` stopped
at their first warp; the rest of those two tests is not covered by it (the
runner now warps both cores, section 6). Three kinds of survivor emerged:

* **Reachable gaps.** A field bit, pin, engine, edge or hold state that no test
  exercised in a way that exposes the change: an operand bit on one engine, an
  ownership check against one pin, a count left in the blocked-cycle counter by
  one completion path, a mover eligibility term that only matters when the host
  acts on another engine, a STOP on exactly the edge an instruction issues,
  etc. Section 3.
* **Changes the design never observes.** The mutated register changes only
  while its value is dead (it is re-initialised or overwritten before any
  instruction, output or host read uses it), or only in states the design
  cannot reach (an invariant masks it). Section 4 proves some of these;
  section 5 lists the rest with the argument.
* **Netlist artefacts.** Mutations of a don't-care (`x`) of the round-trip
  netlist, and SRAM address permutations that are applied identically to reads
  and writes. Section 5.

## 3. New tests: `test/test_kill_*.py`

Eleven modules with 36 tests (list in [`test/README.md`](../test/README.md),
"Mutation-kill tests"; shared helpers in `test/test_kill_common.py`). As in the
rest of the suite, every scenario runs the DUT in lockstep with the reference
model (every cycle's `uo_out`, `uio_out`, `uio_oe` compared) and also runs on
the model alone. Techniques that the existing modules did not use:

* **Per-engine fault visibility.** The shared fault pin is an OR over the
  engines, so one engine's fault hides behind another's. The operand sweep
  runs a DIR of the engine's own pin before each case, so that engine's output
  enable drops on the exact cycle of its fault.
* **Edge-exact host commands and status captures.** `command_at` holds back the
  eighth nibble of a command until the model shows the target state, so a STOP,
  START, FLUSH or ROUTE lands on the edge where an instruction would issue or a
  route could move a word. `snapshot_when` predicts, on a copy of the model, the
  edge on which the status word is captured and changes windows one edge
  earlier.
* **Dense and distinct register values**, all-zeros and all-ones held across
  every kind of instruction, and every ordered register pair, so a dropped or
  flipped bit in a held register or an operation mistaken for another changes a
  pushed word.
* **Exact timing instead of margins.** Bounded waits time out on the exact
  cycle after every completion path and hold state; the gap-closure test
  released the wait four cycles early, which hides a count of 1 left behind.
* **Time warp** (the harness's `warp`, `warp_routes`) for LIMIT values 2^k up to
  2^23 and descriptor counts with bits 5, 9 and 12 set.

Iterations (kill stage: the new modules only, every module runs, on the
survivors of the previous iteration; `orig` and `0` survived every stage):

| stage | modules | mutants | killed | jobs |
|---|---|---:|---:|---|
| k1 | decode, blocked, pc, mover (first versions) | 244 | 46 | 23976302 |
| k3 | + regs, xfer, host, events, pins, edges, fifo | 198 | 83 | 23977946, 23980396 |
| k4 | mover: long streams, FLUSH on a grant edge, count parity | 115 | 12 | 23982173 |
| k5 | decode (own-pin marker, opcode bits 5..7), pins, alu chain, strict-PUSH status, host opcode bits | 103 | 16 | 23983517, 23983518 |
| **final** | all eleven modules as committed except the five tests added below | 360 | 158 of 244 (0 of the 116 proven equivalent) | 23984831 |
| k6 | + ROUTE rewrite on a grant edge, PINS parity, full images ending in COUNT/LIMIT/PINS/TIME | 86 | 3 | 23987440, 23987441 |
| k7 | + WAITEVENT timeouts at large LIMIT, repeat counter held across WAIT/XFER/PULL | 75 | 2 | 23988083, 23988084 |

The final stage ran the complete module set of that time on all 360 survivors
of the committed suite, including the 116 proven-equivalent mutants (none was
killed, as a consistency check). k6 and k7 ran the complete module set on the
remaining survivors; the tests they added are new test functions, the tests of
the final stage are unchanged, so the final stage's kills stand for the
committed suite.

Kills by test (final stage and k6; a mutant can count for several tests; in
parentheses the mutants that only this test kills): `operand_sweep` 21 (12),
`hold_matrix` 21 (5), `stream_disturbed` 17 (8), `half_periods` 16 (15),
`alu_chain` 15 (2), `exact_timeout_paths` 15 (9), `pin_matrix` 14 (11),
`exact_timeout_limits` 13 (11), `shift_sweep` 12 (4), `ownership_sweep` 12 (3),
`flush_matrix` 9 (0), `flush_on_grant_edge` 7 (2), `xfer_tx_pins` 7 (3),
`exact_timeout_waitevent` 6 (0), `modes` 4 (0), `command_sweep` 4 (4),
`time_high` 4 (0), `stop_start_edges` 4 (2), `trigger_matrix` 4 (4),
`pins_register` 3 (1), `route_count_parity` 3 (3), and 1 or 2 each for
`fault_codes`, `image_lengths`, `own_matrix`, `far_jump_alias`,
`far_jump_stop`, `paused_writes`, `strict_push_status`, `pins_parity`,
`route_edit_on_grant_edge`, `image_end_ops`. `paused_reads`, `full_queues` and
`event_masks` kill none of the campaign's mutants.

### Cost and portability

| run | result | job |
|---|---|---|
| CI-equivalent `make clean; make` (Icarus 13.0, conda-forge build of tag `v13_0`; cocotb 2.0.1; default `WAVES`) | 102 tests (66 + 36) pass; 132 s of test time, 2 min 15 s wall (gap closure: 83 s and 89 s) | 23988085 |
| all six variants (`PE_VARIANT`), full default module list, Icarus 14.0 | 102 pass on each | 23988086 |
| gate level (`GATES=yes`), the eleven `test_kill_*` modules, the campaign's routed netlist (sha256 `fe135632…`) | 10 pass, 26 skip, 0 fail; 36 s of simulation | 23988087 |

At gate level only the tests under 5,000 cycles run; the others report SKIP
(the time-warp tests need the RTL register names anyway).

## 4. Formal classification

### 4.1 Campaign method

The campaign's `equiv_mutant.py` (Yosys `equiv_make`, `equiv_simple -seq 2`,
`equiv_induct -seq 2`, every register output, port and SRAM pin net matched)
proved 116 mutants equivalent. Those results are reused unchanged; the same
mutants and `base.il` are used here.

### 4.2 Gold/mutant miter with ABC (6 proven)

A miter of the unmutated core and the mutant (`mk_miter.py`): each of the eight
SRAM macros is cut out of both copies; its read data comes from one free input
shared by the two copies, and its inputs (enable, write enable, read enable,
address, data) are asserted equal, so equal SRAM input histories give equal
read data for any memory contents. The miter asserts `uo_out`, `uio_out` and
`uio_oe` equal every cycle after a power-up reset (all flip-flops start at
zero). Yosys writes an AIGER model the way SymbiYosys does; ABC runs `fold;
strash; lcorr; scorr; pdr` (register correspondence and signal correspondence by
induction, then IC3/PDR), capped at 150 s per mutant.

Controls: 5 of 6 randomly chosen mutants of the 116 proven equivalent were
proven (the sixth timed out); 0 of 6 mutants killed by `test_smoke` were
proven. None of the proven mutants is killed by any test.

ABC reports the proof, not its lemmas; the right-hand column states the design
fact that makes each change invisible.

| mutant | region | change | design fact |
|---|---|---|---|
| 18, 65, 226, 363 | engine_ctrl | a PC bit (9, 15, 14, 8) dropped or flipped in the next PC of PINS, PULL or PUSH | COMMIT accepts at most 64 words, so an instruction issues only with PC < 64 and those bits of PC and PC + 1 are 0 |
| 663 | host | bit 9 of the selected engine's loaded-word count dropped (window 1 write-ready, COMMIT check) | the count is at most 64 |
| 1978 | imem | bit 1 of the host write buffer stuck at 1 | a word is assembled by shifting nibbles in from the top; the buffer's low nibble never reaches a completed word |

Job 23977941 (all 244 survivors and the 12 controls; 6 survivors proven, the
rest undecided within the cap, one counterexample: mutant 1962, section 5).

### 4.3 `equiv_induct` with invariants and an SRAM model (2 proven)

`equiv_inv.py` extends the campaign method: (1) each SRAM macro is replaced, in
both copies, by its registered-read behaviour over unknown contents (the output
changes only on a read; a repeated read of the last address with no write in
between returns the same word; any other read returns a free word shared by
both copies; the model's registers and a write-port vector are matched, so both
copies must read the same addresses and write the same words); (2) invariants
of the unmutated core are added as correspondences to the constant 1, so
`equiv_induct` assumes them on the previous steps and proves them on the next.
The library: an engine's blocked-cycle count is zero unless it is blocked in a
WAITPIN/WAITEVENT or halted/faulted; a running engine's macros last read its PC;
a running engine has no fault and a nonzero LIMIT; image length and loaded
counts are at most 64; queue counts at most 8. The result counts only when
`equiv_status` reports every correspondence proven.

| mutant | region | change | design fact |
|---|---|---|---|
| 1988, 2009 | imem | the read enable of engine 1's or engine 2's macros held at 1, so a program write also reads | a write only happens while the engine is halted, and the next ordinary read of the PC address restores the macro's output before the engine can start |

Job 23986701 (the 87 survivors after k5 and three killed mutants, 43, 1010 and
1099, as negative controls; the controls were not proven, one survivor timed
out).

### 4.4 Methods tried without a result

* A k-induction proof (SymbiYosys `smtbmc yices`) of a strengthened miter
  (every register pair equal, registers allowed to differ while dead, the
  invariant library) did not finish within 820 s at depth 1 even for the no-op
  mutant (jobs 23980738, 23982092). ABC `pdr` on the same miter was undecided
  after 600 s (job 23983809).
* ABC `&scorr; pdr` on dead-value mutants merged about 200 of 7,960 latches and
  was undecided after 780 s (job 23983017).
* Liveness masking in `equiv_inv.py` (match `live ? reg : 0` instead of the
  register) did not prove the no-op mutant for any register tried
  (blocked count, transfer tick, x), so it was not used for any claim (jobs
  23986558, 23986660, 23986715, 23987746).

### 4.5 Longer ABC cap (26 proven; added later)

The miter of section 4.2 ran again on the 73 remaining survivors and the same
12 controls with `formal_mutant.py`'s default limits (PDR 600 s, ABC cap 900 s
instead of 60 s and 150 s), from `campaigns/mutation/` (job 24050236, 11 × 8
CPUs, 17.8 CPU-hours; results in
[`campaigns/mutation/results/push-4bd30c8/`](../campaigns/mutation/results/push-4bd30c8/)).
26 survivors are proven equivalent (the slowest in 743 s
of ABC time), 3 give a counterexample and 44 stay undecided. Controls: 5 of the
6 proven-equivalent mutants are proven (1332 stays undecided, as at 150 s),
none of the 6 killed ones. The assumptions are those of section 4.2.

| section 5 group | proven | survivors |
|---|---:|---|
| register changed on the STOP edge | 6 of 7 | 251, 425, 495, 1513, 1751, 1840 |
| register changed on the edge the engine faults | 5 of 9 | 143, 1578, 1768, 1829, 1949 |
| register of a halted or faulted engine | 8 of 12 | 189, 325, 1577, 1620, 1634, 1761, 1841, 1873 |
| transfer mode, tick or period outside a transfer | 3 of 3 | 1801, 1859, 1888 |
| reset or BEGIN value | 3 of 8 | 1494, 1580, 1828 |
| masked by a design invariant | 1 of 3 | 488 |

The counterexamples are 1962, 1967 and 1984, the SRAM address permutations of
section 5, whose argument already explains them (the miter requires equal SRAM
addresses). None of the blocked-cycle-count survivors is decided.

## 5. Remaining survivors

73 survivors are neither killed nor proven equivalent by the push. Each is argued
unobservable below, from its netlist diff and the design's register semantics
(`hardcaml/lib/engine.ml`, `processor.ml`, `host.ml`); for none of them is a
reachable observation known. The arguments are not proofs. Engine numbers
follow `campaigns/mutation/results/engine_map.json`. Section 4.5 later proved 26
of the 73 equivalent; the table keeps all 73 with their arguments.

| group | survivors | argument |
|---|---|---|
| blocked-cycle count, stages of other instructions | 40, 50, 76, 92, 113, 126, 192, 211, 261, 279, 303, 357, 421, 508 (engine_ctrl) | The mutation sits in the count's next-value selection for an instruction other than WAITPIN/WAITEVENT (or the PULL-stall hold): it forces a bit to 0, or flips a bit only while another bit of the count is 1. There the count is 0: every completion clears it and it only grows inside a bounded wait, which takes the WAITPIN/WAITEVENT path. |
| blocked-cycle count during an XFER | 25, 47, 62, 118, 233, 285, 528 (engine_ctrl) | The count changes only while a transfer runs; every transfer ends with a completion that clears it, and no bounded wait starts during a transfer. |
| PC during an XFER | 94, 327, 390 (engine_ctrl) | A PC bit of 8 or above is dropped or flipped on the transfer path; an XFER issues from inside the image, so the PC is below 64 there. |
| register changed on the STOP edge | 251, 425, 495, 1513, 1751, 1840, 2235 | LIMIT, WAIT timer, output enables, transfer tick, PINS selection or x change on the edge a STOP halts the engine. A halted engine's outputs are masked, and START re-initialises every one of these registers before the engine runs again. |
| register changed on the edge the engine faults | 105, 143, 191, 531, 1578, 1768, 1787, 1829, 1949 | LIMIT, WAIT timer, blocked count, output enables, transfer tick, PINS selection or transfer mode change on the edge where the engine faults (invalid instruction, PC range, timeout, strict overflow). The faulted engine's outputs are masked and only START (after CLEAR) runs it again, which re-initialises these registers. |
| register of a halted or faulted engine | 189, 325, 532, 1577, 1620, 1634, 1761, 1841, 1873, 2245, 2321, 2344 | The mutation acts on the hold path of an engine that is not active (halted or faulted): blocked count, WAIT timer, outputs, enables, transfer tick/edges/mode, x, tx. Outputs are masked while the engine is not running; START re-initialises all of these registers. (rx, which READ_SELECT 6 reads while halted, is not among them.) |
| transfer mode, tick or period outside a transfer | 1801, 1859, 1888 | These registers are read only during a transfer, and every XFER loads them at issue. |
| reset or BEGIN value | 565, 570, 618, 674, 1494, 1580, 1828, 2237 | The mutated value is written by reset (image loaded/length counts, outputs, transfer period, y, the synchronizer's previous-sample register) or by BEGIN (image length 256 instead of 0). BEGIN, COMMIT or START write each of these registers before any use; the previous-sample register feeds only the input triggers, which reset disables and which need a TRIGGER command (at least nine cycles) to re-enable, by which time the register has been re-sampled. |
| x during an XFER | 2407 (engine_data) | Bit 21 of x is inverted on every cycle with a transfer in progress, 2·a·b cycles in all, an even number, so x is unchanged when the transfer ends; x is not read during a transfer, and a STOP in the middle leaves the engine halted (START clears x). |
| SRAM address permutation | 1962, 1967, 1984 (imem) | An address bit is inverted depending on another address bit, on the address that one macro (or both of an engine's macros) uses for both reads and writes. The mapping is a bijection applied to every access, so every read returns the word written for that PC. The miter of section 4.2 requires equal SRAM addresses and reports a counterexample for 1962 (job 23977941), which that requirement explains. |
| host write buffer | 1969 (imem) | On a window change the buffer is set to 0x00800000 instead of 0. Nibbles shift in from the top; the initial value is shifted out before the eighth nibble completes a word, and the buffer is not used before that. |
| read nibble while read-valid is low | 717 (host) | The read index is set to 1 instead of 0 on a window change; the next capture resets it to 0. In between, read-valid is low, and `docs/isa.md` leaves the read nibble unspecified then (the test harness does not compare it). The reach/infect/propagate run shows `uo_out[3:0]` differing in 34,122 cycles, none with read-valid high (job 23984727). A formal check of this contract property did not finish within its limit (job 23987319). |
| masked by a design invariant | 488 (engine_ctrl), 2020 (shared), 2215 (fifo) | 488: the LIMIT-zero default (65535) is dropped; LIMIT is never 0 while the engine runs (START sets 65535, LIMIT 0 is invalid). 2020: bit 5 of the fault code is ignored in the engine's active term; a running engine has no fault. 2215: bit 0 is ignored in an RX queue's full test; a count of 9 cannot occur. |
| write to an undefined address | 2103 (fifo) | The mutation enables a TX queue's storage write of data bit 18 on cycles without a push. On those cycles the round-trip netlist's write address is the undefined value `x` (the Hardcaml write port has no address then), and RTL simulation ignores a write to an `x` address. The effect on hardware would depend on how synthesis resolves the `x`. |

## 6. Scripts and data

When this page was first written (commit `aa07868`), the stage runner, the
submission scripts, the miter generator and the ABC and Yosys runners of this
push were only in its cluster work directory (`<work dir>/mutation97/`;
`manifest.json` there lists every job with its purpose). The ones needed to
reproduce the result are now in
[`campaigns/mutation/`](../campaigns/mutation/README.md), whose section
"Mutation score push" lists each work-directory script with its sha256, what
changed in the copy, and the commands of a re-run:

| work directory | `campaigns/mutation/` |
|---|---|
| `run_mutant97.py` (stages `head`, `kill`, `kill-rip`) | `run_mutant.py`: stage `suite` on a c118027 test tree is the push's `head` (same modules, order, stop rule and budget); stages `kill` and `kill-rip`. The RIP stages now also apply the harness's time warp to both cores of the wrapper (`RIP_WARP_PATCH`, appended to the task tree's copy of `test/harness.py`); `run_mutant97.py` did not, so on its wrapper the tests that warp stopped at their first warp |
| `submit97.sh`, `submit_formal.sh`, `mkdesign.sh` | `submit.sh` (with `DESIGN`, `RESULTS`, stages `formal*` and `einv*`), `mk_design.sh` |
| `formal_mutant.py`, `mk_miter.py` (section 4.2) | the same files, byte-identical to the work directory's current copies. Job 23977941 ran an earlier frozen copy of `formal_mutant.py` (sha256 `5f45759b…`), which lacks two ABC recipes that the default options do not use and the explicit `MITER_MODE` pass-through; `mk_miter.py` was last changed before that job started |
| `equiv_inv.py`, `einv_specs.py` (section 4.3) | the same, plus the asynchronous-reset handling of `equiv_mutant.py` for design variants (unchanged for the design of record) and options for another core's queue counters |
| `summary97.py`, `mkdiffs.sh` + `cleandiff.py` | `push_summary.py`, `mutant_diff.py` |

The k-induction attempts of section 4.4 (`kind_mutant.py`) are not copied. The
per-mutant JSON results and the survivor diffs stay in the work directory. The
scripts use `PE_WORK` and `OSS_CAD_SUITE` like the campaign scripts. Every
stage re-runs the same `mutations.tsv` on the same `base.il`; only the test
tree changes.

Checks of the copied scripts, run from the repository location (2026-09-26):

| check | result | job |
|---|---|---|
| `push_summary.py` on the push's result directories | killed 2,223, proven equivalent 124 (116 + 6 + 2), 73 survivors: the same 73 ids as the push's own summary | none (login node) |
| `einv_specs.py --invariants-only` on the campaign's `engine_map.json` | byte-identical to the invariant spec of job 23986701 | none |
| `mutant_diff.py` on mutants 1751, 717, 2407 | output identical to that of `mkdiffs.sh` + `cleandiff.py` | 24043570 |
| stage `kill`: the eleven `test_kill_*` modules as committed in `aa07868`, on a c118027 tree, on the 360 survivors of section 1 and the two controls | 163 killed: the same 163 mutants that stages final, k6 and k7 killed together; `orig` and `0` survive; 10.4 CPU-hours. With section 1 this gives 2,223 / (2,420 − 124) = 96.82 % and the same 73 survivors in one stage with the committed modules | 24044905 (8-mutant sample first: 24043553) |
| stage `kill-rip` on 1751 and 1494 | each diverges in the same single register as in job 23984727 (`transfer_tick_1` for 1751, the anonymous register `_3813` for 1494). The harness could not warp the wrapper, as in job 23984727 (where two tests stopped at their first warp): the three tests of the committed modules that warp (`test_kill_exact_timeout_limits`, `test_kill_exact_timeout_waitevent_limits`, `test_kill_time_high`) failed at their first warp in both runs | 24043554 |
| stage `kill-rip` with the time warp applied to both cores (`RIP_WARP_PATCH` in `run_mutant.py`) on 0, 1751 and 1494 | all 36 tests pass on the wrapper in each run, none skipped; 425,810 cycles each. 0: no difference. 1494: `_3813` differs in 144 cycles, as before. 1751: `transfer_tick_1` differs in 101,682 cycles (97,552 without the warp) | 24055121 |
| `formal_mutant.py` (`--props strict --pdr-seconds 60 --abc-timeout 150`) on 18, 663, 1962, 43 | 18 and 663 proven equivalent; 1962 and 43 undecided. 1962 was a counterexample in job 23977941, where ABC finished in 125 s; here it had not finished when the 150 s wall-clock cap was reached. Whether a run is decided within the cap depends on the node; a proof, once found, does not | 24043558 |
| `equiv_inv.py` with the regenerated spec on 1988, 2009, 43 | 1988 and 2009 proven equivalent, 43 not proven | 24043559 |

### 6.1 The committed test tree

Sections 1 and 3 ran on c118027 test trees (section 3 with the kill modules
copied in). The committed tree also carries the Makefile module list of
`aa07868` and the three I2C firmware images of `fb79f31` (`i2c-read`,
`i2c-write`, `i2c-repeated-start`), which those trees did not have. Stage
`suite` on a `git archive` of `4bd30c8` (102 tests in 20 modules in the default
order, stopping at the first failing module; Icarus Verilog 14.0, cocotb
2.0.1) ran on all 2,420 mutants and both controls with the repository scripts:

| | result |
|---|---|
| controls | `orig` and `0` survive |
| killed | 2,223: the same mutants that sections 1 and 3 killed together. First failing module: `test_random` 691, `test_smoke` 577, `test_legacy` 293, `test_protocols` 251, `test_directed` 88, `test_timewarp` 83, `test_flagship` 33, `test_kill_decode` 29, `test_kill_regs` 28, `test_mover` 28, `test_kill_blocked` 27, `test_kill_mover` 25, `test_kill_xfer` 18, `test_counters` 17, `test_kill_pins` 14, `test_kill_host` 9, `test_kill_pc` 4, `test_kill_events` 4, `test_kill_edges` 3, `test_kill_fifo` 1 |
| score | with the 124 proofs of section 4: 2,223 / (2,420 − 124) = 96.82 %, the same 73 survivors |
| first failing module | differs from section 1 for 10 mutants: 873, 2241, 2272 and 2333 are now first killed by `test_random` (before: `test_protocols` or `test_legacy`), 1232, 2295, 2309, 2329 and 2398 by `test_legacy` (test `test_legacy_firmware_i2c_restart`; before: `test_random`), and 2341, which the nine modules of section 1 did not kill (section 3 did), by the same test |
| jobs | 24043457 (6 tasks × 8 CPUs; two tasks preempted and requeued were cancelled and their remaining ids run by 24048013, 5 × 8); 38.3 CPU-hours. Results: [`campaigns/mutation/results/push-4bd30c8/`](../campaigns/mutation/results/push-4bd30c8/) |

## 7. Variant diet4 under the 102-test suite

The `diet4` campaign of
[verification-campaign.md](verification-campaign.md#variant-diet4-6x4-campaigns)
(the 6x4 fallback, ISA version 3) ran the 66-test suite of `c118027` and scored
87.7 % (2,028 / (2,420 − 107)). Here the same 2,420 mutants run under the
102-test suite of `4bd30c8`, and the survivors are classified with the
procedure of sections 1 and 4. Commands:
[`campaigns/mutation/README.md`](../campaigns/mutation/README.md), "Variant
diet4 under the 102-test suite"; parameters:
`campaigns/mutation/campaign-diet4-102.env`; results:
[`campaigns/mutation/results/diet4-102/`](../campaigns/mutation/results/diet4-102/).

### 7.1 Setup

| item | value |
|---|---|
| test tree | `git archive` of `4bd30c8`, with `build/variants/` regenerated in it by `scripts/gen_variants.sh` (all eight variants and their firmware images; the script's three checks pass; job 24042631) |
| core | `build/variants/diet4/protocol_emulator_core.v`, sha256 `cc27c465…`: byte-identical (`cmp`) to `variants6x4/protocol_emulator_core.v` and to the core the mutants were drawn from (job 23974949) |
| firmware images | the working tree's `build/variants/` had been generated before `fb79f31`. Against it, the regenerated cores are unchanged, and in each of the six older variants the source and image files of `i2c-read`, `i2c-write` and `i2c-repeated-start` differ (the firmware fix of `fb79f31`); `cn_s2_timing` and `rstreg_timing` are new. The working tree's `build/variants/` was replaced by the regenerated files. The earlier `diet4` campaign used the images of `c118027`, which also predate `fb79f31` |
| suite check | `make PE_VARIANT=diet4` in `test/` with Icarus Verilog 13.0 and cocotb 2.0.1, as in CI: 102 tests, 102 pass, 0 fail, 0 skipped, 139 s (job 24043247) |
| mutants | the 2,420 of the earlier campaign: same `base.il` (sha256 `833c7cc3…`) and `mutations.tsv` (`8a24cf1d…`); `mk_design.sh` packs the new test tree |
| stage | `suite`: the 20 default modules of `test/Makefile` (102 tests) in their default order, stopping at the first failing module, on every mutant; Icarus Verilog 14.0 and cocotb 2.0.1 as in section 1; every `make` gets `PE_VARIANT=diet4` and `PE_CORE=<mutant>` |
| controls | `orig` and `0` pass all 102 tests with none skipped, in their own job (24043006) and in the stage |
| equivalence | the 107 `equiv_mutant.py` proofs of the earlier campaign apply unchanged (same `base.il`). The miter of section 4.2 (`formal_mutant.py`, the options of job 23977941: strict property, PDR 60 s, ABC cap 150 s) and `equiv_inv.py` of section 4.3 run on the survivors without a proof. For `diet4`, `equiv_inv.py` keeps the `$adff` register outputs as matched points and runs `async2sync`, as `equiv_mutant.py` does, and `einv_specs.py` maps the invariant library to the variant's register names (`engine_map.py`); the queue-count invariant uses the eight 3-bit counters, bound 4 |

### 7.2 Result

| | earlier (`c118027`, 66 tests) | now (`4bd30c8`, 102 tests) |
|---|---:|---:|
| mutants | 2,420 | 2,420 |
| killed | 2,028 | 2,199 |
| proven equivalent, campaign method (`equiv_induct`) | 107 | 107 |
| proven equivalent, section 4.2 miter (ABC cap 150 s) | – | 0 |
| proven equivalent, section 4.3 invariants | – | 4 |
| **score**, killed / (mutants − proven equivalent) | 87.7 % (2,028 / 2,313) | **95.24 % (2,199 / 2,309)** |
| killed / mutants (no mutant removed as equivalent) | 83.80 % | 90.87 % |
| survivors, neither killed nor proven equivalent | 285 | 110 |

For comparison, the design of record: 96.82 % (2,223 / 2,296), and 91.86 %
(2,223 / 2,420) without removing equivalent mutants; with the longer ABC cap of
section 4.5 on both, 97.93 % for the design of record and 96.66 % for `diet4`
(7.3).

**Note (added 2026-09-30): mutant 1860.** One of the 107 `equiv_induct`
proofs is of a clock inversion: mutant 1860 (`inv` of the `CLK` port of the
`$procdff` cell of engine 0's `transfer_pins` register, `transfer_pins_2` in
the netlist, `results/diet4-102/engine_map.json`) moves that flip-flop
to the falling edge. `equiv_induct` models every flip-flop as a one-cycle
delay of the one clock and does not see the change; the line-unit campaign
found the same limit with its mutant 231 ([extension.md](extension.md)
section 11.4), and `push_summary.py --no-clock-proofs` ignores such proofs.
Without that proof, `diet4` scores 2,199 / (2,420 − 110) = 95.19 % with 111
survivors, and with the longer cap of 7.3 2,199 / (2,420 − 144) = 96.62 %
with 77 survivors (recount of the recorded results, Slurm job 24383267). The
figures of this section keep the accounting as first published. The design
of record's three `CLK` mutants (857, 1390, 2111) are all killed, so its
96.82 % and 97.93 % do not change (the same recount on the push, 24383364). As the section "Variant diet4 (6x4) campaigns" of
verification-campaign.md notes, the two mutant sets are drawn from different
netlists, so the two scores are not a paired comparison.

* Every mutant the earlier suite killed is killed again; the 221 survivors of
  the stage are a subset of the earlier 392. Of the earlier 285 unproven
  survivors, 171 are killed now. The 107 proven-equivalent mutants all survive.
* The earlier campaign's `deep` stage (256 random cases of another seed, not
  part of the suite) had killed 79 of the 285; the 102-test suite kills 73 of
  them.
* First failing module: `test_random` 628, `test_smoke` 586, `test_legacy` 338,
  `test_protocols` 287, `test_directed` 58, `test_flagship` 44,
  `test_timewarp` 43, `test_kill_decode` 43, `test_kill_blocked` 30,
  `test_counters` 28, `test_kill_regs` 26, `test_kill_mover` 23, `test_mover`
  19, `test_kill_host` 14, `test_kill_xfer` 13, `test_kill_pins` 12,
  `test_kill_fifo` 4, `test_kill_events` 3 (`test_kill_pc` and
  `test_kill_edges` are never the first to fail).

By region (proven equivalent: all methods of the table above):

| region | mutants | proven equivalent | killed (earlier) | killed (now) | score (earlier) | score (now) | survivors |
|---|---:|---:|---:|---:|---:|---:|---:|
| engine_ctrl | 550 | 23 | 432 | 470 | 82.0 % | 89.2 % | 57 |
| host | 400 | 13 | 370 | 384 | 95.6 % | 99.2 % | 3 |
| events | 300 | 29 | 229 | 268 | 84.5 % | 98.9 % | 3 |
| mover | 250 | 11 | 217 | 239 | 90.8 % | 100 % | 0 |
| pins | 250 | 13 | 216 | 228 | 91.1 % | 96.2 % | 9 |
| engine_xfer | 200 | 13 | 150 | 162 | 80.2 % | 86.6 % | 25 |
| engine_data | 180 | 0 | 148 | 176 | 82.2 % | 97.8 % | 4 |
| fifo | 130 | 0 | 123 | 127 | 94.6 % | 97.7 % | 3 |
| shared | 90 | 3 | 85 | 86 | 97.7 % | 98.9 % | 1 |
| imem | 60 | 6 | 48 | 49 | 82.8 % | 90.7 % | 5 |
| timestamp | 10 | 0 | 10 | 10 | 100 % | 100 % | 0 |
| **total** | **2,420** | **111** | **2,028** | **2,199** | **87.7 %** | **95.24 %** | **110** |

(The "score (earlier)" column divides by the earlier 107 proofs; in `imem`
the proven-equivalent count is 2 there and 6 now.)

By mutation mode: `inv` 588 killed / (606 − 7) = 98.2 %, `const0` 520 / (599 −
41) = 93.2 %, `const1` 573 / (607 − 16) = 97.0 %, `cnot0` 296 / (315 − 6) =
95.8 %, `cnot1` 222 / (293 − 41) = 88.1 %.

### 7.3 Formal classification

**Miter, section 4.2 options** (job 24047830): the 114 survivors without a
campaign proof and 12 controls drawn with `random.seed(20270118)` (6 killed by
`test_smoke`: 684, 1118, 1557, 2059, 2072, 2415; 6 of the 107 proven
equivalent: 486, 647, 1127, 1182, 1388, 2097). None of the 114 was decided
within the 150 s cap; 4 of the 6 equivalent controls were proven, none of the
killed controls.

**`equiv_induct` with invariants, section 4.3** (job 24047831): the 114 and 3
killed controls (464, 481, 669, same seed). 4 proven, the controls not proven.
Before the run, the invariant library was checked on `diet4`: with it, every
correspondence of mutant 0 and of the campaign-proven mutants 6 and 24 is
proven, and killed mutants 2 and 171 are not (job 24043514).

| mutant | region | change | design fact |
|---|---|---|---|
| 1973, 1989, 2000 | imem | the read enable of engine 3's, 0's or 1's two macros held at 1, so a program write also reads | as 1988 and 2009 of section 4.3 |
| 1981 | imem | bit 6 of engine 2's high macro output (instruction bit 22) inverted whenever bit 13 (instruction bit 29, opcode bit 5) is 1 | the opcodes are 0 to 29 (`docs/isa.md`; the list of `diet` changes in `docs/variants.md` adds none), so a word with opcode bit 5 set faults as an invalid opcode whatever its bit 22 |

**Longer ABC cap** (not part of the push's procedure; job 24049008): the miter
with `formal_mutant.py`'s default limits (PDR 600 s, ABC cap 900 s) on the
110 remaining survivors and the same 12 controls. 34 survivors are proven
equivalent (ABC took at most 414 s on them), 10 give a counterexample and 66 stay
undecided. All 6 equivalent controls are proven; of the killed controls, 4 give
a counterexample and 2 stay undecided. With these proofs the score is
2,199 / (2,420 − 145) = 96.66 %, with 76 survivors. A counterexample means that
the miter's property (equal pins and equal SRAM inputs every cycle) fails for
some input sequence; it can come from a difference in SRAM traffic alone and is
not a test.

### 7.4 Survivors

Reach/infect/propagate: stage `kill-rip` with the 20 suite modules (102
tests) on the RIP wrapper, for the 114 survivors without a campaign proof and
for mutant 0.

* First run (job 24047832), before the runner applied the time warp to the
  wrapper. The harness's warp writes the core's registers under
  `user_project.core`; on the wrapper that scope holds the two cores as
  instances, so the harness found no registers to warp. In every run, mutant 0
  included, the ten tests that warp failed: the seven of `test_timewarp` at
  their start (they assert `warp_supported()` before simulating), and
  `test_kill_exact_timeout_limits`, `test_kill_exact_timeout_waitevent_limits`
  (`test_kill_blocked`) and `test_kill_time_high` (`test_kill_regs`) at their
  first warp (`WarpUnsupported`). `test_kill_route_count_parity` skipped its
  warped cases. The run does not cover the rest of those tests.
  Mutant 0 showed no difference in 911,272 cycles; the split of the 110 was
  51 not infected and 59 infected.
* Second run (job 24055120) with `RIP_WARP_PATCH` (section 6): every warp adds
  its delta to the registers of both cores. All 102 tests passed on the
  wrapper in each of the 115 runs, none skipped. Mutant 0 showed no
  difference in 945,373 cycles, and no survivor made a pin differ. The table
  below is from this run. Against the first run, only 168 and 351
  (blocked-cycle count) change: their count first differs after a warp, in
  `test_timewarp` (794 cycles) and in `test_kill_blocked` (2,327 cycles).

`survivor_classes.py` groups the 110 survivors of 7.2 by the named state that
the mutated statement feeds (`describe.py`) and by the RIP outcome ("not
infected": no register differed from the unmutated core's in any simulated
cycle of the second run):

| class | survivors | not infected | infected, no pin differed | proven with the longer ABC cap | counterexample (longer cap) | undecided |
|---|---:|---:|---:|---:|---:|---:|
| blocked-cycle count | 48 | 37 | 11 | 4 | 0 | 44 |
| XFER state (pins, period, tick, edges, mode) | 25 | 2 | 23 | 18 | 0 | 7 |
| pin outputs and output enables | 11 | 5 | 6 | 7 | 3 | 1 |
| WAIT timer, LIMIT, repeat counter | 8 | 1 | 7 | 4 | 0 | 4 |
| x, y, tx, PC, image length | 6 | 3 | 3 | 1 | 0 | 5 |
| instruction SRAM pins | 5 | 1 | 4 | 0 | 5 | 0 |
| event mailbox | 3 | 0 | 3 | 0 | 2 | 1 |
| anonymous registers (region `fifo`) | 3 | 0 | 3 | 0 | 0 | 3 |
| several states (2032) | 1 | 0 | 1 | 0 | 0 | 1 |
| **total** | **110** | **49** | **61** | **34** | **10** | **66** |

These classes are bookkeeping, not the arguments of section 5: the 110 were
not argued one by one. Observations:

* The blocked-cycle count is the largest class, as on the design of record
  (by the same grouping, 24 of its 73 survivors); for 37 of the 48 the count
  never differs in the second RIP run. The warp skips cycles without
  simulating them, so "not infected" covers the simulated cycles only.
* Four survivors change an engine's fault code during `test_kill_decode`
  without a pin difference: 1327, 1393 and 1481 (the event-mailbox class; the
  fault code differs in 40 cycles) and 2032 (region `shared`, a fault-code
  comparison `fault_code == …`; an engine's `running` flag, `host_fault` and the
  fault code differ in 1, 9 and 1 cycles). The miter with the longer cap gives
  a counterexample for 1327 and 1393. They are candidates for a test that
  reads the fault code; they were not analysed further.
* The five survivors of the instruction-SRAM class invert a macro address bit
  after the read/write address selection (1968 unconditionally, the others
  depending on another address bit; 1972 at one macro's address pin), like
  1962, 1967 and 1984 of section 5 (SRAM address permutation). The miter
  requires equal SRAM addresses, and all five give a counterexample.

### 7.5 Jobs

| step | jobs | tasks × CPUs | ids |
|---|---|---|---:|
| `gen_variants.sh` in the snapshot | 24042631 | 1 × 8 | – |
| suite check, Icarus 13.0 (`mit_quicktest`) | 24043247 (24042891 was preempted after one minute and cancelled) | 1 × 4 | – |
| controls, stage `suite` | 24043006 | 1 × 2 | 2 |
| stage `suite` | 24043449 (25 × 8; one task on a slow node was cancelled after every id had a result), 24046661 (the ids still without a result, 5 × 8) | | 2,422 |
| miter, 150 s cap | 24047830 | 8 × 8 | 126 |
| `equiv_induct` with invariants | 24047831 (invariant check: 24043514; miter pipeline check: 24043513) | 3 × 8 | 117 |
| RIP, whole suite, first run (without the warp on the wrapper) | 24047832 | 8 × 8 | 115 |
| RIP, whole suite, second run (warp applied to both cores) | 24055120 (warp check on 0, 17, 18 with `test_timewarp`, `test_kill_blocked`, `test_kill_regs`, `test_kill_mover`: 24055015, `mit_quicktest`) | 15 × 8 | 115 |
| miter, 900 s cap | 24049008 | 15 × 8 | 122 |

Compute (sums of per-mutant run times): stage `suite` 53.6 CPU-hours
(including the slow node), miter 6.7 and 22.3, `equiv_induct` with invariants
1.2, RIP 14.0 (first run) and 12.3 (second run). The job ids are also in the work directory's `manifest.json`.
