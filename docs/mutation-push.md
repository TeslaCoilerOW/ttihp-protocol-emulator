# Mutation score push

This page continues the RTL mutation campaign of
[verification-campaign.md](verification-campaign.md) ("Mutation testing" and
"Gap closure"). It re-measures the survivors against the committed suite,
classifies them with formal equivalence checks, adds directed tests for the
ones that the tests can reach, and reports the new score.

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
23984727). Three kinds of survivor emerged:

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

## 5. Remaining survivors

73 survivors are neither killed nor proven equivalent. Each is argued
unobservable below, from its netlist diff and the design's register semantics
(`hardcaml/lib/engine.ml`, `processor.ml`, `host.ml`); for none of them is a
reachable observation known. The arguments are not proofs. Engine numbers
follow `campaigns/mutation/results/engine_map.json`.

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

The stage runner (a copy of `campaigns/mutation/run_mutant.py` with the stages
`head`: the committed suite in default order, first failure stops; `kill`: the
`test_kill_*` modules, every module runs; `kill-rip`: the `kill` modules on the
reach/infect/propagate wrapper), the miter generator, the ABC and Yosys runners
(`formal_mutant.py`, `equiv_inv.py`, `kind_mutant.py`), the survivor diffs and
the per-mutant JSON results are in the cluster work directory of this push
(`<work dir>/mutation97/`, `manifest.json` lists every job with its purpose) and
are not part of this repository. They use `PE_WORK` and `OSS_CAD_SUITE` like the
campaign scripts. Every stage re-runs the same `mutations.tsv` on the same
`base.il`; only the test tree changes.
