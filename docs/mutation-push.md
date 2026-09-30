# Mutation score push

This page continues the RTL mutation campaign of
[verification-campaign.md](verification-campaign.md) ("Mutation testing" and
"Gap closure"). It re-measures the survivors against the committed suite,
classifies them with formal equivalence checks, adds directed tests for the
ones that the tests can reach, and reports the new score. Added later: section
4.5 (a longer ABC cap), section 6 (the scripts in the repository, checked by
re-running them, and a re-run on the committed test tree), section 7 (the
same procedure on design variant `diet4` under the 102-test suite) and section
8 (a gap that the mutation campaign on the extension branch found, and
`test/test_wait_limit.py`, which closes it and kills survivor 192).

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

Later addition (section 8): `test/test_wait_limit.py` kills survivor 192, whose
argument in section 5 does not hold.

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
of the 73 equivalent; the table keeps all 73 with their arguments. Section 8
later found an observation for 192: `test/test_wait_limit.py` kills it, and its
argument below does not hold.

| group | survivors | argument |
|---|---|---|
| blocked-cycle count, stages of other instructions | 40, 50, 76, 92, 113, 126, 192 (killed later, section 8), 211, 261, 279, 303, 357, 421, 508 (engine_ctrl) | The mutation sits in the count's next-value selection for an instruction other than WAITPIN/WAITEVENT (or the PULL-stall hold): it forces a bit to 0, or flips a bit only while another bit of the count is 1. There the count is 0: every completion clears it and it only grows inside a bounded wait, which takes the WAITPIN/WAITEVENT path. |
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
(7.3). As the section "Variant diet4 (6x4) campaigns" of
verification-campaign.md notes, the two mutant sets are drawn from different
netlists, so the two scores are not a paired comparison.

**Note (added 2026-09-30): mutant 1860.** One of the 107 `equiv_induct`
proofs is of a clock inversion: mutant 1860 (`inv` of the `CLK` port of the
`$procdff` cell of `transfer_pins_2`, engine 0's `transfer_pins` register by
`results/diet4-102/engine_map.json`) moves that flip-flop to the falling
edge. `equiv_induct` treats every flip-flop as a delay of one step of the one
clock and does not look at the clock net, so it does not see the change, and
the proof does not show that the mutant is equivalent. (The same Yosys steps,
`async2sync`, `equiv_make`, `equiv_simple` and `equiv_induct`, also prove a
two-flip-flop module equal to a copy whose first flip-flop is clocked by the
inverted clock, although Icarus Verilog shows their outputs differ on 13 of
20 cycles; `<work dir>/ps2-port/clkinv/`.) Without it, `diet4` scores
2,199 / (2,420 − 110) = 95.19 % with 111 survivors, and with the longer ABC
cap (section 7.3) 2,199 / (2,420 − 144) = 96.62 % with 77 survivors (a recount of `results/diet4-102/mutant_status.tsv` against the
campaign's `mutations.tsv`, sha256 `8a24cf1d…`; Slurm job 24391204). The
campaign's seven other `CLK` mutants (756, 830, 1534, 1746, 1804, 2138 and
2203) are killed. The figures of this section keep the accounting as first
published. The design of record's three `CLK` mutants (857, 1390 and 2111)
are all killed, so its 96.82 % and 97.93 % do not change (the same recount
on `results/push-4bd30c8/`).

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
| 1981 | imem | bit 6 of engine 2's high macro output (instruction bit 22) inverted whenever bit 13 (instruction bit 29, opcode bit 5) is 1 | the opcodes are 0 to 29 (`docs/isa.md`; the list of `diet` changes in `docs/notes/variants.md` adds none), so a word with opcode bit 5 set faults as an invalid opcode whatever its bit 22 |

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

## 8. Later addition: WAITEVENT timeouts past each count bit

The mutation campaign on the extension branch (`eval/diet8-rec16`,
[extension.md](extension.md)) draws its own mutants of the extension
variant's core. Among its survivors were mutations of the blocked-cycle
count's next value that flip bit p of the value while its bit c is 1. The
same mutations applied to the design-of-record core (this campaign's
`base.il`, core sha256 `26a873db…`) gave four mutants that pass all 102 tests
of the push suite (Slurm array jobs 24366260 and 24370776, with the unmutated
core and id 0 as controls). All four are `mutate -mode cnot1` on the
WAITEVENT stage of one engine's count:

| id | cell | port | p, c | engine |
|---|---|---|---|---:|
| D331 | `$ternary$core_orig.v:8783$1163` | B | 10, 13 | 0 |
| D333 | `$ternary$core_orig.v:8783$1163` | Y | 11, 14 | 0 |
| D1438 | `$ternary$core_orig.v:11539$2365` | B | 16, 18 | 2 |
| D1442 | `$ternary$core_orig.v:11539$2365` | Y | 7, 21 | 2 |

This campaign's sample (2,420 mutants, drawn per region with the quotas and
seed of `campaigns/mutation/results/gen_params.txt`) does not contain them. It
has three other mutants on these two cells: 279 and 357 (`const0` on port A,
section 5) and 483 (`cnot1` on port B of `8783$1163`, bit 13 while bit 17 is
set, killed by `test_kill_blocked` in the push).

The core raises the timeout on a sample where the count plus one reaches
LIMIT. While the count is below 2^c its bit c is 0 and the flip does not act.
On the sample on which the count reaches 2^c the mutant stores 2^c + 2^p
instead. At LIMIT 2^c + 2 the next sample then has 2^c + 2^p + 1 ≥ 2^c + 2
and times out one sample early, where the unmutated count gives
2^c + 1 < 2^c + 2. The suite's other WAITEVENT timeouts above LIMIT 0x3FF
(`test_kill_blocked` at 2^k + 5 for k = 12, 17, 20 and 23, `test_timewarp` at
0xFFFFFE, and its WAITEVENT released by an EVENT just before 0xFFFFFD) did
not detect the four.

`test/test_wait_limit.py` ([test/README.md](../test/README.md), "LIMIT
timeouts") lets WAITEVENT and WAITPIN time out at LIMIT 2^k + 2 for k = 0..23
on every engine. It checks, from the DUT, that each engine's output enable
stays high for exactly LIMIT + 1 edges and that its status reports fault
code 3. On the test tree with the module (109 tests):

- **The four mutants.** Each fails the module's WAITEVENT test and passes the
  other 107 tests (job 24382109). With `PE_SPEC_ONLY=1` (no comparison with the
  reference model) the module's own check fails: the enable of engine 0
  (D331, D333) or engine 2 (D1438, D1442) is released after LIMIT edges
  instead of LIMIT + 1, at LIMIT 2^13 + 2, 2^14 + 2, 2^18 + 2 and 2^21 + 2.
- **The same bit pairs on the WAITPIN stage** of the same two engines (cells
  `8787$1167` and `11543$2369`) are killed by `test_kill_blocked` already
  (one of them also by `test_directed` and `test_timewarp`). The module
  kills all four as well.
- **Controls.** The unmutated core and id 0 pass all 109 tests.
- **The 73 survivors of section 5.** The module kills one of them, 192
  (`cnot1`, bit 5 while bit 21 is set, port A of
  `$ternary$core_orig.v:11541$2367`), and the other 72 pass it (job
  24382110). Cell `11541$2367` is the stage of another instruction, but its
  port A carries the value that the WAITEVENT stage before it selected, so
  the mutation acts on engine 2's WAITEVENT count. The argument of section 5
  does not hold for it. It passes the other 107 tests, and with
  `PE_SPEC_ONLY=1` the module's own check fails on it at LIMIT 2^21 + 2
  (job 24382495).

With 192 counted, the push's 102 tests and `test_wait_limit.py` together kill
2,224 of the 2,420 mutants: 2,224 / (2,420 − 124) = 96.86 %, with 72
survivors. With the proofs of section 4.5 (192 is not among them) the score
is 2,224 / (2,420 − 150) = 97.97 %, with 46 survivors. The four mutants
above are not in the sample and do not enter these figures. Arithmetic
checked with AXLE (`<work dir>/limit-port/axle/`).

Jobs, all with Icarus 13.0 and cocotb 2.0.1, the mutant core in place of
`src/protocol_emulator_core.v` (through `PE_CORE`) and one make call per
module, every module run: 24381935 (first checks), 24382109 (the default suite on the four
mutants, the four WAITPIN-stage mutants, id 0 and the unmutated core, and the
module alone with `PE_SPEC_ONLY=1`), 24382110 (the module on the 73
survivors) and 24382495 (192 with `PE_SPEC_ONLY=1` and under the default
suite). The job list is also in the work directory's
`limit-port/manifest.json`.

## 9. Held-out sample

*Added 2026-09-30.* The score of this page is in-sample: the `test_kill_*`
modules of section 3 were written against the survivors of the same 2,420
mutants, and `test/test_wait_limit.py` (section 8) against four mutants outside
the sample and against survivor 192. It does not estimate how well the suite
detects mutants that it was not written against. This section draws a new
sample of mutants of the same core that excludes those mutants, runs it under
the current suite, and classifies the survivors with the procedure of sections
4 and 5. No test was written or changed for it: a test aimed at a held-out
survivor would make the sample in-sample again. Parameters:
`campaigns/mutation/campaign-heldout.env`; sampling script:
`campaigns/mutation/heldout_sample.py`; results:
[`campaigns/mutation/results/heldout-56f4b20/`](../campaigns/mutation/results/heldout-56f4b20/).

### 9.1 Sample

| item | value |
|---|---|
| core | `src/protocol_emulator_core.v` at `56f4b20` (sha256 `26a873db…`, the core of the campaign and of this page); the campaign's `base.il` (sha256 `cb91afa0…`) and region selections (`sel/<region>.txt`) |
| population | for each of the eleven regions (all 3,579 cells of the core), Yosys `mutate`'s whole raw database: every bit of every port of every cell with the modes `inv`, `const0` and `const1`, and `cnot0` and `cnot1` with a control bit that `mutate` draws from its seed (as in the campaign; `mutate -list` with a count above the database size returns the database unreduced). 652,678 mutations |
| excluded | every mutation with the (mode, cell, port) of one of the 2,420 mutants of this page or of the eight mutants of section 8 (D331, D333, D1438, D1442 and their WAITPIN-stage analogues), whatever its port bit or control bit: 30,298 (4.64 % of the database), leaving 622,380 |
| draw | uniform without replacement within each region, 80 % of the campaign's region quota, in the order of `campaign.env`: engine_ctrl 440, host 320, mover 200, events 240, pins 200, engine_xfer 160, imem 48, shared 72, fifo 104, engine_data 144, timestamp 8; 1,936 mutants. The regions keep the campaign's weights, and the size keeps the survivors few enough to classify one by one |
| seed | 20260930: region k of that list (k = 1 … 11) uses `mutate -seed 20260930+k` and Python's `random.Random(20260930+k).sample` |
| suite | the 21 default modules of `test/Makefile` at `56f4b20` (109 tests), Icarus Verilog 13.0 and cocotb 2.0.1 as in CI, `run_mutant.py` stage `suite` (default order, stop at the first failing module) |

Within a region every mutation of the database is equally likely. The
campaign's `mutate -list` instead picked by coverage (`gen_mutants.sh`: the
per-statement and coverage queues), which spreads the picks over statements and
wires; a uniform draw weights a statement by its port bits, so the wide
multiplexers of the 24-bit counters and the 32-bit datapath get more mutants.
The held-out figures below therefore differ from the in-sample ones in two
ways: the sample is held out, and within a region it is drawn uniformly.

The runner takes the first `COCOTB_TEST_MODULES ?=` line of `test/Makefile`,
which since `76a81f5` is the list of the line-unit variant; every run therefore
also ran `test_line_unit`, `test_line_demos` and `test_line_random` after the 21
default modules, and their 25 tests skip on the design of record. Kills and
first failing modules are those of the 21 default modules. The unmutated core
and the no-op mutant 0 pass all 109 tests (job 24425574, and in the stage).

**How far the sample is from the push's.** The exclusion is by (mode, cell,
port). Many held-out mutants still sit near a push mutant: 628 of the 1,936
share (cell, port) with a push mutant under another mode, and 1,410 share a
cell.

**The same runner on this page's sample.** Stage `suite` on the 2,420 mutants
of this page with the same tree, runner and simulator (job 24425864) kills
2,224: the 2,223 of section 6.1 and survivor 192 (by `test_wait_limit.py`), and
no other mutant changes status. With the proofs of sections 4.1 to 4.3 this is
2,224 / (2,420 − 124) = 96.86 %, with those of section 4.5
2,224 / (2,420 − 150) = 97.97 %, the in-sample figures of section 8
(`insample-56f4b20.json`).

### 9.2 Classification

| step | held-out result | jobs |
|---|---|---|
| stage `suite` | 1,655 of 1,936 killed, 281 survive; all four clock-input mutants (499, 523, 796, 1262) are killed, so the caveat of section 7.2 on mutant 1860 does not arise | 24425862 (24 × 8 CPUs), 24429175 (seven ids whose result could not be written: the file-count quota of the work file system was exhausted) |
| `equiv_mutant.py` (4.1) | 148 of the 281 proven equivalent | 24428688, 24430160, 24431210 |
| miter, PDR 60 s, ABC cap 150 s (4.2) | on the 133 others: 16 proven, 5 counterexamples (SRAM address permutations), 112 undecided. Controls drawn with `random.Random(20260930)`: of 6 mutants killed by `test_smoke` (568, 673, 835, 1224, 1686, 1697) none proven; of 6 proven by `equiv_mutant.py` (145, 379, 1017, 1195, 1554, 1796) 4 proven | 24429181, 24430279 |
| `equiv_inv.py` with the invariant library (4.3) | 1 proven (1594); the three killed controls (1104, 1260, 1636) not proven. The library was regenerated with `einv_specs.py --invariants-only`; its 32 invariants are those recorded in the results of job 23986701 | 24429182, 24430280 |
| miter, PDR 600 s, ABC cap 900 s (4.5) | on the 133: 64 proven (47 of them not proven by the three methods above; at most 866 s of ABC time), 7 counterexamples (747 and six SRAM address permutations), 62 undecided. Controls: 5 of the 6 equivalent proven (1796 undecided); none of the 6 killed (568 a counterexample, 5 undecided) | 24429183, 24430281 |
| RIP (stage `kill-rip`: the 21 modules on the wrapper, time warp on both cores) | on the 133 and mutant 0: all 109 tests pass on the wrapper in every run, none skipped; mutant 0 shows no difference in 1,350,346 cycles. Of the 69 survivors left after the longer cap, 33 were never infected, 35 infected without a pin difference, and one (747) changed the read nibble | 24429184, 24430282 |
| by hand | the 69 left: 48 argued equivalent (9.4), 21 real test gaps (9.5) | – |

### 9.3 Result

Wilson 95 % score intervals (z = 1.96) in brackets. They cover the sampling
error only, and treat each sample as a simple random sample. The held-out
sample is drawn per region in proportion to the region quotas, so the pooled
score estimates the regions weighted by their quotas, as in the campaign, not
the 622,380 mutations weighted by database size (`engine_data` is 43.9 % of the
population and 7.4 % of the sample; weighting the per-region estimates by
population size gives a slightly lower score). Relative to that quota-weighted
mixture the stratified draw can only reduce the sampling variance, so its
intervals are conservative; the
campaign's coverage-weighted pick is not a random draw of a stated population,
so the in-sample intervals are for comparison only.

| | in-sample (this page) | held-out |
|---|---:|---:|
| mutants | 2,420 | 1,936 |
| killed (109 tests, `56f4b20`) | 2,224 | 1,655 |
| proven equivalent, methods of 4.1–4.3 | 124 (116 + 6 + 2) | 165 (148 + 16 + 1) |
| **score** | **96.86 %** (2,224 / 2,296) [96.07, 97.50] | **93.45 %** (1,655 / 1,771) [92.20, 94.51] |
| proven equivalent with the longer ABC cap of 4.5 | 150 | 212 |
| score with the longer cap | 97.97 % (2,224 / 2,270) [97.31, 98.48] | 96.00 % (1,655 / 1,724) [94.97, 96.83] |
| survivors after the longer cap | 46 | 69 |
| of those argued equivalent | 46 | 48 |
| of those neither proven nor argued (real test gaps) | 0 | 21 |
| share neither proven nor argued, of the mutants not proven equivalent (longer cap) | 0 % (0 / 2,270) [0.00, 0.17] | 1.22 % (21 / 1,724) [0.80, 1.86] |

The in-sample arguments are those of section 5; the one for 192 did not hold
(section 8). The 48 held-out arguments are not proofs and stay in the
denominator; counted as equivalent they would give 1,655 / 1,676 = 98.75 %.

Kills by the two groups of modules (first failing module; proofs of 4.1–4.3):

| | in-sample | held-out |
|---|---:|---:|
| killed by the nine modules of the gap-closure suite (`test_smoke` to `test_timewarp`) | 2,061 | 1,540 |
| left by them and not proven equivalent | 235 | 231 |
| of those killed by the twelve modules written against survivors (`test_kill_*`, `test_wait_limit`) | 163: 69.36 % [63.20, 74.91] | 115: 49.78 % [43.39, 56.18] |

First failing module on the held-out sample: `test_random` 541, `test_smoke` 344,
`test_legacy` 243, `test_protocols` 242, `test_timewarp` 74, `test_directed` 51,
`test_kill_decode` 35, `test_kill_regs` 24, `test_flagship` 21,
`test_kill_blocked` 16, `test_counters` 14, `test_kill_xfer` 12,
`test_kill_pins` 11, `test_mover` 10, `test_kill_host` 8, `test_kill_mover` 7,
`test_kill_pc` 2 (`test_kill_events`, `test_kill_edges`, `test_kill_fifo` and
`test_wait_limit` are never the first to fail).

By region (proofs of 4.1–4.3 in the scores; in-sample: the `56f4b20` run of 9.1):

| region | mutants | killed | proven (4.1–4.3) | proven (4.1–4.3 and the longer cap) | argued | gaps | score (held-out) | score (in-sample) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| engine_ctrl | 440 | 375 | 25 | 32 | 27 | 6 | 90.4 % | 93.5 % |
| host | 320 | 270 | 38 | 42 | 5 | 3 | 95.7 % | 98.7 % |
| events | 240 | 196 | 42 | 42 | 0 | 2 | 99.0 % | 99.6 % |
| mover | 200 | 190 | 9 | 9 | 1 | 0 | 99.5 % | 100.0 % |
| pins | 200 | 170 | 18 | 23 | 0 | 7 | 93.4 % | 97.5 % |
| engine_xfer | 160 | 121 | 8 | 33 | 3 | 3 | 79.6 % | 93.2 % |
| engine_data | 144 | 135 | 2 | 6 | 3 | 0 | 95.1 % | 96.6 % |
| fifo | 104 | 96 | 5 | 6 | 2 | 0 | 97.0 % | 98.4 % |
| shared | 72 | 58 | 13 | 14 | 0 | 0 | 98.3 % | 98.9 % |
| imem | 48 | 36 | 5 | 5 | 7 | 0 | 83.7 % | 93.0 % |
| timestamp | 8 | 8 | 0 | 0 | 0 | 0 | 100.0 % | 100.0 % |
| **total** | **1,936** | **1,655** | **165** | **212** | **48** | **21** | **93.45 %** | **96.86 %** |

By mode (held-out): `inv` 415 / (446 − 8) = 94.7 %, `const0` 364 / (439 − 58) = 95.5 %, `const1` 383 / (425 − 22) = 95.0 %, `cnot0` 300 / (321 − 4) = 94.6 %, `cnot1` 193 / (305 − 73) = 83.2 %. 15 of the 21 gaps are `cnot1` mutants (a bit flipped only while another bit is 1).

### 9.4 Survivors argued equivalent (48)

Engine numbers follow `campaigns/mutation/results/engine_map.json`. Per-survivor
detail (cell, port, bits, miter results, RIP outcome) is in `survivors.tsv`.

| group | survivors | argument |
|---|---|---|
| blocked-cycle count, outside a bounded wait | 51, 60, 61, 65, 66, 95, 106, 107, 114, 376, 380, 382, 383, 385, 395, 422 | Blocked-cycle count outside a bounded wait: the mutation forces a bit to 0, or flips a bit only while another bit of the count is 1, in the count's next value for an instruction other than WAITPIN/WAITEVENT (the default of the opcode chain, reached by XFER and FAULT), during an XFER, or in a PULL/PUSH stall; there the count is 0 (every completion and START clear it, and only a bounded wait increments it). |
| blocked-cycle count at an XFER or FAULT | 59, 105, 416 | Blocked-cycle count inverted at the issue of an XFER or FAULT (the default of the opcode chain): the count is 2^k during the transfer and is cleared at its last edge, or by START after a STOP or the FAULT; no bounded wait runs in between. |
| register changed on the edge the engine faults | 96, 130, 410, 411, 415, 1842, 1864, 1891 | Register changed on the edge the engine faults (LIMIT, repeat counter, blocked count at a WAITEVENT timeout, x, y, tx): not readable by the host, and START re-initialises it before the engine runs again. |
| edge count of a halted engine | 1545 | The edge counter's select of its active branch is tied to 1, so a halted engine whose PC points at an XFER loads the edge count and counts it down while halted; only an active engine uses the count, and START clears it. |
| transfer counters with the same edges | 1422, 1425 | Transfer counters that differ but give the same edges: 1422 flips bit 2 of an even period every cycle, and an even number of cycles separates each reload of the tick from the XFER's load; 1425 changes the remaining-edge count by 16 on alternate edges while it is above 32, with the same bit 0 and the same edge at which it reaches 1 and 0, and only those are used (a model of the counter agrees with the unmutated one for every bit count 1..32 and half-period 1..255). |
| PC during an XFER | 37 | PC bit 13 flipped while bit 18 is set, on the transfer path: an XFER issues from inside the image, so the PC is below 64 during a transfer. |
| repeat counter during an XFER | 41 | Repeat counter bit 6 flipped on every transfer cycle while bit 5 is 0: a transfer lasts 2ab cycles, an even number, so the counter is unchanged at its end; a STOP in between halts the engine and START clears the counter. |
| reset value | 536, 546, 551, 552, 820, 1565 | Reset value (image loaded count, image length, route destination, host write buffer; for 552 the image length is not cleared by reset at all): BEGIN, COMMIT or ROUTE writes the register before any use, and the write buffer's initial value is shifted out before the eighth nibble completes a word. |
| masked by a design invariant | 103 | The default used when LIMIT is 0 is changed; LIMIT is never 0 while the engine runs (START sets 65535, LIMIT 0 is invalid). |
| SRAM address permutation | 1569, 1570, 1575, 1597, 1599, 1604 | SRAM address permutation: an address bit of one macro (or of an engine's two macros) is inverted, unconditionally or depending on another address bit, on the address used for both reads and writes; the mapping is a bijection, so every read returns the word written for that PC. |
| write to an undefined address | 1767, 1780 | Queue storage write of one data bit enabled on cycles without a push; the round-trip netlist's write address is x on those cycles, and RTL simulation ignores a write to an x address (as survivor 2103 of section 5; the effect on hardware depends on how synthesis resolves the x). |
| read nibble while read-valid is low | 747 | The read buffer's bit 2 is cleared on a window change; the next capture overwrites the buffer, so the read nibble differs only while read-valid is low, where docs/isa.md leaves it unspecified (as survivor 717 of section 5). |

### 9.5 Real test gaps (21)

Each of these changes a value that the host or the pins can observe, in a
reachable situation that the suite does not produce or does not check. The
description is of the unobserved behaviour, not a test; no test was written.
"RIP" is the outcome of the RIP run: "not infected" means that no register of
the mutant differed from the unmutated core's in any simulated cycle,
"infected" that some did, without a pin difference.

| survivor | region, mode | unobserved behaviour | RIP |
|---|---|---|---|
| 101 | engine_ctrl, `const1` | START sets engine 2's LIMIT to 0x10FFFF instead of 65535, so a WAITPIN or WAITEVENT that relies on the default LIMIT times out 1,048,576 samples late; no test lets a bounded wait under the default LIMIT time out on engine 2 | infected |
| 492 | host, `const0` | COMMIT with length 0 right after BEGIN (loaded count 0) is accepted instead of rejected with the host fault | not infected |
| 531 | host, `cnot1` | Once engine 3's image holds 64 words, its loaded count alternates between 64 and 1,088 every cycle, so COMMIT 64 is accepted only on every other edge; the suite commits full engine-3 images at one parity only | infected |
| 589 | host, `cnot1` | READ_SELECT 4 (event pending) returns bit 13 of the selected engine's completed-instruction count in its bit 14; no test reads the event-pending word while that count has bit 13 set | not infected |
| 1133 | events, `const0` | Engine 2 treats the invalid opcode 0x41 as HALT (the mutated comparison with opcode 1 ignores opcode bit 6): its PC and completed count advance and no fault is raised; `test_kill_decode` runs the opcode on engine 2, but another engine's fault hides the fault pin then and engine 2's status, PC and count are not read (RIP: its fault code differs in 11 cycles, its PC and count in 208) | infected |
| 1151 | events, `cnot1` | Engine 3 executes FAULT with the invalid operand 0, 0x010000, 0x020000 or 0x030000 (the validity term of NOT, a < 4 and b = c = 0, is XORed into that of FAULT): it stops with fault code 0, so no fault is reported, where the unmutated core faults with code 1; `test_kill_decode` runs such a word on engine 3, but another engine's fault hides the fault pin then and engine 3's status is not read (RIP: engine 3's fault code differs in 40 cycles) | infected |
| 276 | engine_ctrl, `cnot1` | Engine 2's completed-instruction count flips bit 27 on every cycle of a blocked WAITEVENT while its bit 12 is set (after 4,096 or more completed instructions); READ_SELECT 5 then differs | not infected |
| 309 | engine_ctrl, `cnot1` | Engine 1's completed-instruction count flips bit 27 on every cycle of a blocked WAITPIN while its bit 12 is set; READ_SELECT 5 then differs | not infected |
| 268 | engine_ctrl, `const0` | Engine 2's completed-instruction count loses bit 28 during an XFER (only once the count has reached 2^28, or after a warp of the count); READ_SELECT 5 | not infected |
| 274 | engine_ctrl, `cnot1` | Engine 2's completed-instruction count flips bit 17 on every cycle of a PUSH stall or strict overflow while its bit 27 is set (count at least 2^27); READ_SELECT 5 | not infected |
| 275 | engine_ctrl, `cnot1` | Engine 2's completed-instruction count flips bit 1 on every cycle of a blocked WAITEVENT while its bit 29 is set (count at least 2^29); READ_SELECT 5 | not infected |
| 1233 | pins, `cnot0` | An OUT to pin 0 on engine 0 also clears engine 0's output bit 2; visible when engine 0 drives pin 2 high | not infected |
| 1369 | pins, `cnot1` | An OUT of a 1 to pin 1 on engine 1 also sets engine 1's output bit 7; visible when engine 1 owns and enables pin 7 | infected |
| 1285 | pins, `cnot1` | Engine 3's output enable 4 toggles on every cycle of a PUSH stall (full RX queue, a = 0) while its enable 6 is set; visible when engine 3 owns pin 4 | infected |
| 1287 | pins, `cnot1` | Engine 3's output enable 5 flips when a WAITPIN completes while its enable 1 is set; visible when engine 3 owns pin 5 | infected |
| 1295 | pins, `inv` | Engine 2's output enable 4 flips when a WAITEVENT consumes its event; visible when engine 2 owns pin 4 | infected |
| 1318 | pins, `cnot1` | Engine 0's output enable 1 toggles on every cycle of a PUSH stall (a = 0) while its enable 5 is set; visible when engine 0 owns pin 1 | infected |
| 1319 | pins, `cnot1` | Engine 0's output enable 7 toggles on every cycle of a PUSH stall (a = 0) while its enable 4 is set; visible when engine 0 owns pin 7 | not infected |
| 1433 | engine_xfer, `cnot1` | An XFER on engine 2 with a bit count a whose 2a has bit 3 set (a mod 8 in 4..7) and an even half-period b makes a different number of clock edges (by a model of the edge counter, a = 4, b = 2 gives 24 instead of 8) | infected |
| 1488 | engine_xfer, `cnot1` | An XFER on engine 1 with a mod 8 in 4..7 and an even b makes a different number of edges (by the same model, a = 4, b = 2 gives 12 instead of 8) | infected |
| 1489 | engine_xfer, `cnot1` | A 32-bit XFER on engine 1 with an even b makes 72 edges instead of 64 (by the same model) | not infected |

Three of them (268, 274, 275) act only once engine 2 has completed at least
2^27 instructions; hardware reaches that count, and the harness can raise it
with its `warp_completed` time warp. 101 needs a bounded wait of 65,535
samples under the default LIMIT (or a warp of the blocked count); the others
need at most about 8,000 cycles (589 needs 8,192 completed instructions; the
rest a few thousand cycles or fewer).

### 9.6 Jobs and data

| step | jobs | CPU-hours (sum of per-mutant run times) |
|---|---|---:|
| draw the sample (`heldout_sample.py`, `mit_quicktest`) | 24425559 | – |
| controls `orig` and 0, stage `suite` | 24425574 | – |
| stage `suite`, held-out | 24425862, 24429175 | 75.4 |
| stage `suite`, this page's 2,420 mutants | 24425864 | 70.8 |
| `equiv_mutant.py` | 24428688, 24430160, 24431210 | 2.4 |
| miter, ABC cap 150 s | 24429181, 24430279 | 6.3 |
| `equiv_inv.py` | 24429182, 24430280 | 2.3 |
| miter, ABC cap 900 s | 24429183, 24430281 | 25.9 |
| RIP | 24429184, 24430282 | 22.0 |
| netlist diffs (`mutant_diff.py`) | 24429191 | – |

The control mutants of the miter and `equiv_inv.py` stages were drawn with
`random.Random(20260930)` from the pools as they stood after job 24428688
(147 proven equivalent; ids sorted as integers), in this order: 6 killed by
`test_smoke` (568 673 835 1224 1686 1697), 6 proven equivalent (145 379 1017
1195 1554 1796), then 3 more killed (1104 1260 1636).

Committed in `campaigns/mutation/results/heldout-56f4b20/`: `sample.json`
(seed, quotas, per-region database, exclusion and draw counts, sha256 of the
gzip files of the databases as written, of `base.il` and of the drawn
`mutations.tsv`; the gzip hashes include gzip's header timestamp, so a re-run
reproduces the database contents and `mutations.tsv` byte for byte (job
24433331) but not those hashes; `heldout_sample.py` now also records the
sha256 of the uncompressed text), `exclude_section8.tsv`,
`summary.json` and `summary-longcap.json` (`push_summary.py`),
`mutant_status.tsv` (per mutant: region, mode, cell, port, bits, status and
method, both accountings, class), `survivors.tsv` (the 69 with their group or
gap description) and `insample-56f4b20.json`. The per-mutant JSON results, the
databases and the job list (`manifest.json`) stay in the work directory
(`<work dir>/mut-heldout/`). Arithmetic checked with AXLE
(`<work dir>/mut-heldout/axle/`).

Re-run (login node; every heavy step is a Slurm array; `PE_WORK/cocotb/bin`
must hold Icarus Verilog 13.0 for the simulation stages):

```sh
source campaigns/mutation/campaign.env; source campaigns/mutation/campaign-heldout.env
export PATH=$OSS_CAD_SUITE/bin:$PATH MANIFEST=$HELDOUT/manifest.json JOB_PREFIX=pe-mut-heldout SNAPSHOT_COMMIT=$HEAD_COMMIT
H=$PWD/campaigns/mutation; S=$H/submit.sh
mkdir -p $HELDOUT/snap && git archive $HEAD_COMMIT | tar -x -C $HELDOUT/snap
srun -p mit_quicktest -c 2 --mem=12G -t 15 python3 $H/heldout_sample.py $CAMP/design $HELDOUT/sample \
  --quotas $HELDOUT_QUOTAS --seed $HELDOUT_SEED --exclude $CAMP/design/mutations.tsv --exclude $HELDOUT_EXCLUDE_EXTRA
M=$(sed -n 's/^COCOTB_TEST_MODULES ?= //p' $HELDOUT/snap/test/Makefile | tail -1 | tr ',' ' ')
$H/mk_design.sh $CAMP/design $HELDOUT/snap $HELDOUT/design; $H/mk_design.sh $CAMP/design $HELDOUT/snap $HELDOUT/design_rip $M
for d in design design_rip; do rm $HELDOUT/$d/mutations.tsv; cp $HELDOUT/sample/mutations.tsv $HELDOUT/$d/; done
cd $HELDOUT && (echo orig; cut -f1 design/mutations.tsv) > all_ids.txt
CAMP=$HELDOUT DESIGN=$HELDOUT/design RESULTS=$HELDOUT/results/suite PAR=8 $S suite all_ids.txt 24 02:00:00 mit_preemptable,mit_normal
python3 $H/push_summary.py design --sim suite=results/suite --write-survivors survivors.txt        # 281
CAMP=$HELDOUT DESIGN=$HELDOUT/design RESULTS=$HELDOUT/results/equiv PAR=8 $S equiv survivors.txt 8 01:30:00 mit_preemptable,mit_normal
python3 $H/push_summary.py design --sim suite=results/suite --proof equiv_induct=results/equiv --write-survivors unproven.txt   # 133
(cat unproven.txt; printf '%s\n' $HELDOUT_FORMAL_CONTROLS) > formal_ids.txt; (cat unproven.txt; printf '%s\n' $HELDOUT_EINV_CONTROLS) > einv_ids.txt
python3 $H/einv_specs.py --invariants-only - $CAMP/design/engine_map.json einv_invonly.json
CAMP=$HELDOUT DESIGN=$HELDOUT/design RESULTS=$HELDOUT/results/formal PAR=8 MEM_PER_RUNNER=4 RUNNER_ARGS="$FORMAL_ARGS" $S formal formal_ids.txt 8 01:00:00 mit_preemptable,mit_normal
CAMP=$HELDOUT DESIGN=$HELDOUT/design RESULTS=$HELDOUT/results/einv PAR=8 MEM_PER_RUNNER=4 RUNNER_ARGS="--spec $HELDOUT/einv_invonly.json" $S einv einv_ids.txt 3 01:30:00 mit_preemptable,mit_normal
CAMP=$HELDOUT DESIGN=$HELDOUT/design RESULTS=$HELDOUT/results/formal-long PAR=8 MEM_PER_RUNNER=5 RUNNER_ARGS="$FORMAL_LONG_ARGS" $S formal-long formal_ids.txt 12 02:30:00 mit_preemptable,mit_normal
(echo 0; cat unproven.txt) > rip_ids.txt
CAMP=$HELDOUT DESIGN=$HELDOUT/design_rip RESULTS=$HELDOUT/results/kill-rip PAR=8 $S kill-rip rip_ids.txt 12 03:00:00 mit_preemptable,mit_normal
P="--sim suite=results/suite --proof equiv_induct=results/equiv --proof miter-abc=results/formal --proof equiv_inv=results/einv"
python3 $H/push_summary.py design $P --json summary.json
python3 $H/push_summary.py design $P --proof miter-abc-900s=results/formal-long --json summary-longcap.json
```

The controls are held-out mutants, drawn with one `random.Random(20260930)`
from those killed by `test_smoke` (six for the miter, then three for
`equiv_inv.py`) and from those proven by `equiv_mutant.py` (six); their ids are
`HELDOUT_FORMAL_CONTROLS` and `HELDOUT_EINV_CONTROLS` in the env file.
