# Verification campaigns

Cluster-scale verification campaigns run against a frozen snapshot of the
design (commit `73536f0`). Each campaign owns one section of this file.

## Random differential campaign

The constrained-random lockstep differential test (`test/test_random.py`,
`test/random_gen.py`; see [`test/README.md`](../test/README.md)) runs one seed
(64 cases) in CI. This campaign ran it for thousands of seeds on the MIT
Engaging cluster, at RTL and on the routed gate-level netlist, added generator
variants and an extended cross-coverage observer from outside the test
directory, and ran a negative control. Scripts: [`campaigns/random/`](../campaigns/random/README.md).

### Setup

| item | value |
|---|---|
| design under test | frozen `git archive` of commit `73536f0bb6aff03e5e3579442e7fb4b5438a4d0d` (`src/project.v` sha256 `2aacfd24…`, `src/protocol_emulator_core.v` sha256 `26a873db…`); the working tree was not used |
| stimulus, checker, coverage | the snapshot's `test/harness.py`, `test/random_gen.py`, `test/model/` imported unmodified |
| gate-level netlist | locally routed netlist after `52-openroad-fillinsertion` (`tt_um_teslacoilerow_protocol_emulator.nl.v`, sha256 `fe135632…`), hardened from byte-identical `src/*.v`; eight `RM_IHPSG13_1P_64x16_c2` macros simulated with the snapshot's behavioral models (`-DFUNCTIONAL`), IHP `sg13cmos5l` stdcell/IO Verilog models, zero-delay (no SDF) |
| simulator | Icarus Verilog 14.0 (devel, OSS CAD Suite 2026-07-29), cocotb 2.0.1, Python 3.12.12 (the TT actions use Icarus 13.0) |
| compute | Slurm `mit_preemptable` (preemptible, `--requeue`), job arrays of 8-CPU tasks each running 8 single-CPU simulators; job ids below and in `vcamp/manifest.json` |

How a seed runs: `campaigns/random/campaign_random.py` is a cocotb module that
calls the upstream `make_case(seed, i, config, cycles)` and `run_case` for
`i = 0..cases-1` in one simulator process per seed, exactly like
`PE_SEED=<seed> make COCOTB_TEST_MODULES=test_random`. It adds bookkeeping only:
it records a failing case and continues with the next one (upstream stops at the
first failure), and it writes per-case results and the `Coverage` counters as
JSON so that coverage can be merged across seeds. Fidelity check (job
23745559): seed 1 run through the unmodified upstream `make` flow and through
the campaign driver printed an identical functional-coverage summary and
identical per-case lockstep cycle counts for all 64 cases.

Seeds are deterministic: campaign `X` with offset `o` runs seeds `o+1 … o+n`
(for example `rtl-default` is seeds 1 to 4096). Any seed of a `default`
campaign reproduces locally with `PE_SEED=<seed> make COCOTB_TEST_MODULES=test_random`.

Generator variants (applied by wrapping upstream functions at import time; each
generated case is still a `random_gen.Case` that `PE_REPLAY` can replay):

| variant | change against the upstream generator |
|---|---|
| `default` | none |
| `dense` | every program 40 to 64 words (upstream mostly 3 to 24); 85% of host idle operations redrawn, so about 5 times more host operations per cycle |
| `faulty` | deliberate-fault rate at least 0.10 per instruction (upstream 0, 0.02 or 0.05); 30% of idles become "revive" (status read, CLEAR of faulted engines, restart of halted ones) |
| `hostile` | 12% of host operations start an illegal or aborted command sequence that upstream never emits: BEGIN with a payload, COMMIT with a wrong/zero/oversized length, STOP/EVENT with absent-engine bits, ROUTE with bits 21 to 23 set, program-window writes outside a load, START before COMMIT, a 65th program word, BEGIN of a running engine |
| `deselect` | the upstream trailing `deselect` operation (10% of cases) moved to a random point in the first 70% of the host traffic (see finding 2) |
| longer runs | `PE_RANDOM_CYCLES` 20,000 (`rtl-long`) and 200,000 (`rtl-xlong`) host-traffic cycles per case instead of 2,000 |

`xcov` campaigns additionally attach `campaigns/random/xcov.py`, an observer
that reads the reference model around every tick and counts 71 interaction bins
that `random_gen.Coverage` does not track (same-edge FIFO collisions between the
host, the mover and engines; mover arbitration and blocking reasons; trigger
modes; simultaneous event delivery and consumption; synchronous multi-engine
start; STOP/BEGIN/reset of busy engines; FIFO-full levels; concurrency; pin
drive modes; branch outcomes). It only reads state.

### Campaigns and totals

All runs on 2026-09-25 between 01:17 and 02:50 (EDT); the 4,096-seed
`rtl-default` array took 16.6 minutes of wall-clock time. "Lockstep cycles" are
clock cycles in which `uo_out`, `uio_out` and `uio_oe` were compared with the
reference model before and after the edge. "sim CPU-h" is the sum of per-seed
simulator wall time.

| campaign | generator | level | seeds | cases/seed | host cycles/case | cases run | passed | failed | infra errors | programs loaded + reloads | lockstep cycles | instructions completed | sim CPU-h | Slurm array |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `rtl-default` | default | RTL | 0x1..0x1000 (4096) | 64 | 2,000 | 262,144 | 262,144 | 0 | 0 | 922,535 + 210,106 | 1,018,395,195 | 518,291,035 | 71.4 | 23746017 |
| `rtl-xcov-default` | default | RTL | 0x1001..0x1800 (2048) | 64 | 2,000 | 131,072 | 131,072 | 0 | 0 | 461,218 + 105,613 | 508,953,932 | 259,520,748 | 41.2 | 23750843 |
| `rtl-long` | default | RTL | 0x100001..0x100100 (256) | 64 | 20,000 | 16,384 | 16,384 | 0 | 0 | 57,714 + 126,091 | 358,623,049 | 321,741,605 | 23.0 | 23746365 |
| `rtl-xlong` | default | RTL | 0x200001..0x200040 (64) | 16 | 200,000 | 1,024 | 1,024 | 0 | 0 | 3,591 + 78,286 | 206,726,151 | 229,357,705 | 16.2 | 23746366 |
| `rtl-dense` | dense | RTL | 0x300001..0x300100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,737 + 53,102 | 178,881,555 | 44,503,461 | 9.6 | 23746367 |
| `rtl-xcov-dense` | dense | RTL | 0x300101..0x300200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,607 + 53,593 | 178,847,322 | 44,475,241 | 12.3 | 23750844 |
| `rtl-faulty` | faulty | RTL | 0x400001..0x400100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,630 + 45,756 | 161,876,329 | 102,720,654 | 14.2 | 23746368 |
| `rtl-xcov-faulty` | faulty | RTL | 0x400101..0x400200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,745 + 45,438 | 161,880,381 | 103,304,672 | 15.1 | 23750845 |
| `rtl-hostile` | hostile | RTL | 0x500001..0x500100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,593 + 35,848 | 160,799,172 | 86,821,930 | 12.3 | 23747218 |
| `rtl-xcov-hostile` | hostile | RTL | 0x500101..0x500200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,811 + 35,907 | 160,820,358 | 86,343,718 | 10.5 | 23750846 |
| `rtl-xcov-deselect` | deselect | RTL | 0x600001..0x600100 (256) | 64 | 2,000 | 16,384 | 16,384 | 0 | 0 | 57,707 + 13,296 | 63,544,269 | 31,192,981 | 5.2 | 23752081 |
| `gl-default` | default | GL | 0x1..0x40 (64) | 8 | 2,000 | 512 | 512 | 0 | 0 | 1,833 + 402 | 1,990,207 | 982,956 | 1.9 | 23746061 |
| `gl-extended` | default | GL | 0x41..0x140 (256) | 32 | 2,000 | 8,192 | 8,192 | 0 | 0 | 28,891 + 6,500 | 31,823,649 | 16,240,618 | 24.7 | 23746369 |
| `gl-hostile` | hostile | GL | 0x500001..0x500040 (64) | 16 | 8,000 | 1,024 | 1,024 | 0 | 0 | 3,605 + 2,156 | 10,056,054 | 5,582,457 | 8.7 | 23752532 |
| `gl-deselect` | deselect | GL | 0x600001..0x600040 (64) | 16 | 2,000 | 1,024 | 1,024 | 0 | 0 | 3,619 + 792 | 3,967,201 | 1,836,679 | 2.6 | 23752533 |
| **total** | | | 8,704 | | | **536,064** | **536,064** | **0** | 0 | 1,886,836 + 812,886 | **3,207,184,824** | 1,852,916,460 | 269.0 | |

- **RTL**: 8,256 distinct seeds, 525,312 cases, 3,159,347,713 lockstep
  cycles, 0 failures. The required minimum (1,024 seeds x 64 cases) is seeds 1
  to 1024 of `rtl-default`; the campaign ran 6,144 default seeds
  (393,216 cases). Stress runs with a larger `PE_RANDOM_CYCLES`: 256 seeds at
  20,000 and 64 seeds at 200,000 host-traffic cycles per case.
- **Gate level** (routed netlist): 448 seeds, 10,752 cases, 47,837,111 lockstep
  cycles, 0 failures, including the required 64 seeds x 8 cases (`gl-default`).
  GL seeds reuse RTL stimuli (same seed and case index), and all 10,752 GL
  cases took exactly as many lockstep cycles as the same case at RTL.
- Every seed produced a result file: no timeouts, no simulator crashes. Array
  tasks preempted on `mit_preemptable` were requeued and redid only the seeds in
  flight.

### Coverage

Upstream bins (`random_gen.Coverage`, counted on the model in lockstep), merged
over all campaigns: every bin was hit. All 29 non-FAULT opcodes completed on
each of the 4 engines (fewest: engine 3 HALT, 249,216). Stall cycles: PULL
400,900,456, PUSH 1,021,998,618, WAITPIN 201,824,927, WAITEVENT 345,532,771.
Faults: all 17 (code, instruction) bins the generator aims at, 16 built-in ones
(fewest: code 1 from PINS, 110,726) plus `FAULT 0`, and all 255 explicit
`FAULT n` codes. XFER: 32/32
CPOL/CPHA/bit-order/drive/sample combinations (257,153 to 821,634 issues each),
bit counts 1, 2..31 and 32. Mover (DMA): 1,115,787 word transfers, at least one
in every seed. Host commands: all 12 commands both accepted and rejected. Host
traffic: every bin, including all eight READ_SELECT values (about 676,000 reads
each). Full tables: [`campaigns/random/results/report.md`](../campaigns/random/results/report.md).

Holes of the **upstream generator** (393,216 cases of `rtl-default` +
`rtl-xcov-default`): five host-command rejection bins were never hit,
`BEGIN rejected`, `COMMIT rejected`, `STOP rejected`, `ROUTE rejected` and
`EVENT rejected` (see finding 3). The `hostile` variant hit them 91,467 to
181,965 times each (33,792 cases, RTL and GL), all passing.

Extended cross coverage (`xcov.py`, 196,608 cases in five campaigns): all 71
bins were hit, 0 observer errors. The rarest ones, per 1,000 cases:

| bin | default (131,072 cases) | best variant (16,384 cases each) |
|---|---:|---:|
| synchronous START of 4 engines | 0.36 (47 hits) | 1.65 (`dense`) |
| mover blocked by a host TX write to its destination on the same edge | 2.3 | 4.1 (`dense`) |
| host TX write and engine PULL on the same FIFO, same edge | 2.0 | 9.7 (`dense`) |
| mover arbitration with at least 2 eligible routes | 1.7 | 41.6 (`dense`) |
| reset/deselect while engines run | 2.4 | 90 (`deselect`) |
| synchronous START of 3 engines | 4.9 | 20.6 (`dense`) |
| WAITPIN/WAITEVENT timeout with LIMIT 1 | 11.2 | 55.9 (`dense`) |
| mover push and engine PULL on the same destination, same edge | 21.2 | 76.5 (`dense`) |
| four engines inside XFER at once | 20.0 | 204 (`dense`) |

Stimulus efficiency, from the same bins: in the default generator no engine is
running in 38% of lockstep cycles (engines halted or faulted while host traffic
continues), and the fault output `uo[7]` is high in 69% of cycles (92.7% at
20,000 host cycles per case, 97.3% at 200,000).

### Negative control

With the upstream checker self-test `PE_INJECT_MODEL_BUG=xor` (the model's XOR
destination register flips bit 0), the same stimulus as `rtl-default` seeds 1
to 64 (job 23749927) failed in all 64 seeds and in 1,536 of 4,096 cases
(37.5%), every one a lockstep mismatch. In the other cases the flipped bit
never reaches a compared output. The unmodified upstream flow on seed 1 (job 23749932)
reported the mismatch at cycle 1,872 of case 0, wrote the case JSON and the
reproduction command, and its minimizer shrank the case in 38 re-runs from 54
host operations and 100 program words to 1 host operation (`idle(105)`) and one
engine with 6 non-NOP words (`PINS`, `ADD`, `MOV rx,y`, `PUSH`, `XOR x,tx`,
`JMP 1`), which still mismatches on a host read nibble. The checker and minimizer
work in this environment, and a pass means the compared outputs matched.

### Findings

1. **No DUT/model mismatch.** 536,064 cases (525,312 RTL, 10,752 gate level)
   and 3.2 billion lockstep cycles, 0 failures. No RTL, model or test bug
   surfaced, so there was nothing to reproduce, minimize or triage. This is
   evidence at the level of this generator and its coverage (above), not a
   proof. Faults inside the design that never reach `uo_out`/`uio_*` stay
   invisible to the lockstep check.
2. **Test gap: the random `deselect` operation almost never executes.**
   `make_case` appends `["deselect", n]` to 10% of cases *after* the host
   operations whose estimated cost already reaches `PE_RANDOM_CYCLES`, and
   `run_case` stops issuing operations once the budget is spent
   (`if h.cycle - start >= case.cycles: break`). Measured: seeds 1 to 256 generate
   1,652 cases (10.1%) ending in a deselect, and 48 of those executed.
   `rtl-default` executed 669 deselects in 262,144 cases (0.26%, about 1/40 of
   the intent), `rtl-long` and `rtl-xlong` 0. Mid-run deselection (ena low while
   engines run, which must stop engines, invalidate images and clear queues)
   was therefore exercised far less than designed. The `deselect` variant moves
   the operation into the traffic: 1,653 deselects in 16,384 cases, "reset or
   deselect while engines run" 37 times more often (90 vs 2.4 per 1,000 cases),
   all passing at RTL and gate level (`gl-deselect`, 119 deselects). Suggested
   fix (in `test/random_gen.py`, owned by another workflow, not changed here):
   in `run_case`, replace the `break` with
   `if h.cycle - start >= case.cycles and op[0] != "deselect": continue`. This
   keeps every existing seed's stimulus identical and only lets the trailing
   deselect run. Alternatively, insert it at a random position in `make_case`
   using a separate RNG (as the variant does), so the main RNG stream is not
   disturbed.
3. **Generator gap: five host-command rejection paths are never generated.**
   `host_op` only emits well-formed BEGIN/COMMIT/STOP/EVENT/ROUTE payloads,
   and only writes program words inside `load()`. So BEGIN with a non-zero
   payload, COMMIT with a wrong/zero/oversized length or while not writing,
   STOP/EVENT with bits for absent engines, ROUTE with payload bits 21 to 23,
   program-window writes outside a load and a 65th program word were
   never exercised by the random test. `test_smoke.py` checks a few directed
   rejections (for example COMMIT of an incomplete image). The `hostile` variant (`campaign_random.hostile_host_op`) adds these
   sequences. They were hit 91,467 to 181,965 times each (RTL + GL) with no
   mismatch. Suggested fix: add those sequences as host-op kinds in
   `random_gen.host_op` (they only need the existing `cmd` and `partial` op
   kinds, see the variant).
4. **Weakly covered interactions** (table above): simultaneous 4-engine START,
   host-TX-write/PULL and host-TX-write/mover collisions on one FIFO, and
   multi-route mover arbitration occur about 0.4 to 2.3 times per 1,000 default
   cases. The `dense` variant raises the arbitration bin 25 times and the others
   2 to 5 times. Suggested generator changes: configure 2 to 4 routes more often,
   with source RX FIFOs prefilled by PUSH-heavy programs; aim host TX writes at
   route destinations; use START masks with 3 or 4 bits after loading all
   engines.
5. **Observability limit.** The host-fault flag is sticky (cleared only by CLEAR
   with bit 23), and the generator rejects commands on purpose. `uo[7]` is
   therefore high in 69% of default cycles, and `uo[6]` (IRQ) in 91%. Most engine
   fault and event timing is checked through READ_SELECT status reads and output
   enable release rather than through `uo[7]`/`uo[6]` edges. Clearing the host
   fault (CLEAR bit 23) more often would make fault-pin timing observable in more
   cycles.
6. **Upstream minimizer is disabled for replays.** `test_random.report_failure`
   returns before minimizing whenever `PE_REPLAY` is set. A saved case from a
   campaign or a variant therefore cannot be shrunk with the upstream test alone.
   Suggested fix: gate on `PE_MINIMIZE` only. `campaign_random.py` offers
   `VCAMP_MINIMIZE=1` for this, with the same `random_gen.minimize`.
7. **Campaign infrastructure incident (resolved).** At 01:43:45 the upstream
   negative-control run (`upstream_run.sh`, `make` on a copied snapshot with
   newer file times) rebuilt the shared RTL `sim.vvp` in place while campaign
   tasks were loading it. A fresh build from the frozen snapshot (job 23752228)
   is identical to the rebuilt file after normalizing source paths and the
   pointer-valued labels that Icarus writes (its output is not byte-reproducible
   between builds), and no seed recorded an infrastructure error. So results are
   unaffected. `upstream_run.sh` now builds in a private copy.

Raw per-seed results (one JSON per seed, with per-case cycles and the coverage
counters) are in `<work dir>/vcamp/random/<campaign>/results/`
on the cluster; job ids in `<work dir>/vcamp/manifest.json`;
per-campaign totals, holes and xcov bins in
[`campaigns/random/results/campaigns.json`](../campaigns/random/results/campaigns.json).

## Mutation testing

This campaign measures how many injected RTL faults the cocotb suite detects
(plan task 2.5). It generated 2,420 single-bit mutants of the base core,
ran a fast kill-oriented test set and then the full 39-test suite on every
mutant, and analysed the survivors in four ways: a formal equivalence proof, a
reach/infect/propagate (RIP) differential simulation, 256 extra random cases,
and directed tests written from the survivor analysis. Scripts and small result
files: [`campaigns/mutation/`](../campaigns/mutation/README.md).

**Result.** 1,847 of 2,420 mutants are killed by the existing suite (1,433 by
the fast set, 414 more by the full suite), 0 time out and 0 error. 116
survivors are proven equivalent. The mutation score is
**1,847 / (2,420 − 116) = 80.2%**. 457 survivors are not proven equivalent. Of
these, 130 are killed by the deep random stage or the new directed tests, which
gives 1,977 / 2,304 = 85.8% for the suite plus those additions.

### Setup

| item | value |
|---|---|
| design under test | frozen `git archive` of commit `73536f0` (`src/protocol_emulator_core.v` sha256 `26a873db…`); only that module is mutated; `src/project.v` and the eight `RM_IHPSG13_1P_64x16_c2` macros are not (the nets the core drives into the macros can be) |
| mutation engine | Yosys 0.67+111 `mutate`, the engine behind mcy. `mcy` is in the OSS CAD Suite, but its local task runner was replaced by Slurm job arrays |
| mutant construction | `read_verilog` + `prep -top protocol_emulator_core` → `base.il` (3,579 cells). A mutant is `base.il` plus exactly one `mutate -mode …` command, written with `write_verilog -noattr`. Modes: `inv` (586), `const0` (569), `const1` (615), `cnot0` (314: bit XNOR another bit of the same port), `cnot1` (336: bit XOR another bit). No two mutants share a command |
| controls | `orig` (the committed core, unmodified) and mutant `0` (the Yosys round trip without a mutation). Both pass the fast set, the full suite and the directed tests |
| simulator | Icarus Verilog 14.0 (OSS CAD Suite 2026-07-29), cocotb 2.0.1, the snapshot's own `test/Makefile` (`-DFUNCTIONAL`, `WAVES=none`), node-local build directory per mutant |
| fast set | `test_smoke` (3), `test_protocols` (9), `test_flagship` (1), `test_random` with `PE_RANDOM_ITERS=8` (the first 8 cases of the default seed); modules run in that order, stopping at the first module with a failing test. Baseline 32 s; budget 900 s |
| full suite | all 39 tests with defaults (25 legacy replays, 64 random cases); fast-set survivors only. Baseline 120 to 126 s; budget 2,400 s |
| kill | a test fails. Every test compares the DUT with the reference model on every cycle, so a kill is a lockstep or protocol-check failure. No mutant timed out or hit a simulator error |
| compute | 108.6 CPU-hours in job arrays of 1-CPU simulators on `mit_preemptable` (with `--requeue`), `mit_normal` and `mit_quicktest`. Every task is idempotent (one JSON per mutant and stage; skipped if present). Job ids are listed at the end of this section and in `vcamp/manifest.json` |

#### Where the mutants go

The netlist is flat and most nets are anonymous, so `region_map.py` assigns
each statement to the functional region of the nearest named state in its
forward cone. Named state is a named register, a FIFO array, an SRAM instance
or an output port. `gen_mutants.sh` then runs one seeded `mutate -list` per
region (`select -read` of that region's cells) with a fixed quota. The engine
control, host/loader and mover logic therefore get more mutants than their
cell share, and the 32-bit datapath gets fewer. Within a region, the
`-cfg` options switch off the two per-bit sampling queues, which favour wide
buses. They also raise the weight of the per-source-statement queue and the
coverage queue, so a region's mutants spread over its Hardcaml statements
(`weight_pq_b 0`, `weight_pq_mb 0`, `weight_pq_s 300`, `weight_cover 2000`,
`pick_cover_prcnt 90`). The base seed is 20270118, and region k uses seed+k.
The parameters are in `campaigns/mutation/campaign.env`.

### Results by region and mutation type

| region | cells | mutants | killed (fast) | killed (full) | survived | proven equivalent | score | killed by deep random or directed tests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| engine_ctrl | 847 | 550 | 259 | 84 | 207 | 23 | 65.1% | 38 |
| host | 310 | 400 | 326 | 23 | 51 | 28 | 93.8% | 6 |
| events | 579 | 300 | 184 | 38 | 78 | 32 | 82.8% | 23 |
| mover | 164 | 250 | 120 | 60 | 70 | 6 | 73.8% | 12 |
| pins | 333 | 250 | 115 | 81 | 54 | 7 | 80.7% | 23 |
| engine_xfer | 232 | 200 | 117 | 38 | 45 | 9 | 81.2% | 9 |
| engine_data | 724 | 180 | 82 | 64 | 34 | 2 | 82.0% | 19 |
| fifo | 268 | 130 | 96 | 21 | 13 | 7 | 95.1% | 0 |
| shared | 83 | 90 | 77 | 4 | 9 | 2 | 92.0% | 0 |
| imem | 36 | 60 | 49 | 1 | 10 | 0 | 83.3% | 0 |
| timestamp | 3 | 10 | 8 | 0 | 2 | 0 | 80.0% | 0 |
| **total** | 3,579 | 2,420 | 1,433 | 414 | 573 | 116 | 80.2% | 130 |

| mode | mutants | killed (fast) | killed (full) | survived | proven equivalent | score | killed by deep random or directed tests |
|---|---:|---:|---:|---:|---:|---:|---:|
| `inv` | 586 | 464 | 78 | 44 | 5 | 93.3% | 9 |
| `const0` | 569 | 254 | 105 | 210 | 36 | 67.4% | 45 |
| `const1` | 615 | 421 | 94 | 100 | 19 | 86.4% | 29 |
| `cnot0` | 314 | 217 | 62 | 35 | 5 | 90.3% | 16 |
| `cnot1` | 336 | 77 | 75 | 184 | 51 | 53.3% | 31 |

Region names: `engine_ctrl` (PC, run/fault, WAIT timer, LIMIT and blocked
count, COUNT/LOOP, completed count), `engine_xfer` (XFER edge/tick/period/mode
and pins), `engine_data` (tx, rx, x, y), `pins` (logical outputs and enables,
ownership, open drain, triggers), `host` (window protocol, loader, readback,
host fault), `mover` (ROUTE descriptors, DMA grant, round robin), `events`
(mailboxes and the opcode decode that feeds them), `imem` (instruction-SRAM
address, data and enables), `fifo` (the eight queues), `timestamp`, and
`shared` (reset, window and select decode feeding three or more regions).
A score here is killed / (mutants − proven equivalent) by the existing suite.

`cnot1` mutants flip a bit only while another bit of the same port is 1,
often a high bit of a counter, so they are the hardest to activate.

Which tests kill:

- **Fast set:** `test_smoke` 577, `test_random` (8 cases) 571,
  `test_protocols` 252, `test_flagship` 33.
- **Full suite, fast-set survivors only:** `test_random` (64 cases) 344,
  `test_legacy` 70.

The random lockstep test is the single most effective test: it kills 915 of
the 1,847 mutants, counting only the first failing module per mutant.

### Survivor analysis

**Formal equivalence** (`equiv_mutant.py`, all 573 survivors).

- **Copies.** The unmutated and mutated cores are both read back with
  `read_verilog -mem2reg`, so the FIFO arrays become named registers.
- **Matched nets.** Only register outputs, ports and SRAM pin nets keep their
  names. Everything else is hidden, so a difference masked downstream of the
  mutated cell is not a mismatch.
- **SRAM.** The SRAM instances are shared: their inputs must be proven equal,
  and both copies read the same word.
- **Proof.** `equiv_make`, `equiv_simple -seq 2`, then
  `equiv_induct -ignore-unknown-cells -seq 2`. This proves that copies which
  agree on every register agree forever. It is sufficient but not necessary,
  so a mutant that differs only in unreachable states stays "not proven".
- **Controls.** Mutant 0 is proven. All 40 negative controls, a sample of
  fast-killed mutants (job 23748253), are "not proven".
- **Result.** 116 of the 573 survivors are proven equivalent. 29 of those are
  static no-ops: `mutate -list` also proposes tying a bit to the constant it
  already has (`noop_check.py` finds exactly 29, and all 29 are proven).

**RIP analysis** (stage `rip`, all 573 survivors).

- **How it runs.** The full suite runs on a wrapper holding the unmutated core
  and the mutant side by side. The unmutated core drives the pins, so the
  stimulus is exactly what the suite applied. Every register, FIFO word and
  SRAM pin net is compared, and so are the four output groups (read nibble,
  host flags, `uio_out`, `uio_oe`), at every clock.
- **Controls.** Mutant 0 never diverges in 341,457 cycles. Killed mutant 7
  diverges in state and in all four output groups.
- **Result** (457 survivors not proven equivalent):
  - **288** never diverge in any state element. The suite does not activate
    them.
  - **168** corrupt internal state that never reaches a pin.
  - **1** (mutant 717) changes the read nibble only while read-valid is low.
    The nibble is unspecified there and the harness does not compare it, so
    this is not a checker gap.
- **Pre-reset artifact.** Three proven-equivalent mutants differ from the
  unmutated core only in the first 5 cycles of `test_smoke`, before reset,
  where the unmutated register is X and the mutant forces a bit. This is
  consistent with the 2-valued proof.

**Deep random** (stage `deep`: `test_random`, 256 cases, `PE_SEED=0xD33B2027`,
all 573 survivors). It kills 108, so the default 64-case budget leaves many
survivors that more random stimulus reaches.

**Directed tests** (`campaigns/mutation/directed/test_mutation_gaps.py`, 12
tests on the snapshot's lockstep harness; the controls pass all 12).

- **Origin.** Each test encodes the "kill" stimulus from the sample
  classification below.
- **Result.** They kill 31 of the 573 survivors, 22 of which the deep random
  stage did not kill. No proven-equivalent mutant is killed.
- **Revisions.** It took two revisions to kill every targeted sample
  mutant. The first version killed 14 survivors, the second 27. Each miss
  exposed a real subtlety (`results/directed-v1` and `-v2` are kept):
  - Mutant 852 changes a register's output, and its hold path writes the
    mutated value back, so the length alternates 64/192 every cycle. A
    64-NOP image falls off the end on a "64" cycle; a `WAIT 1` prefix kills
    it.
  - Mutants 268 and 451 are on engine 3, not engine 0. Hardcaml's name
    de-duplication does not follow the engine index: `repeat_count_0` is
    engine 3 and `x_2` is engine 0 (`engine_map.py`).
  - Mutants 195 and 1753 oscillate by one bit. They need a LIMIT that is not
    a multiple of 32 (0x2108 rather than 0x2100) and an odd XFER half-period
    (201 rather than 200).
  - Mutant 2279 corrupts the SHR result, not SHL.

After all four analyses, 327 survivors are neither proven equivalent nor
killed:

| region | survivors neither proven nor killed |
|---|---:|
| `engine_ctrl` | 146 |
| `mover` | 52 |
| `engine_xfer` | 27 |
| `pins` | 24 |
| `events` | 23 |
| `host` | 17 |
| `engine_data` | 13 |
| `imem` | 10 |
| `shared` | 7 |
| `fifo` | 6 |
| `timestamp` | 2 |

### Classified sample

48 of the 573 survivors were drawn at random (`random.seed(20270118)`) and
classified by hand. `describe.py` and `region_map.py` give each mutant's
statement and cone, and the Hardcaml source gives the semantics. The classes:

- **EQ-proven:** the formal proof above.
- **EQ-manual:** equivalent in every reachable state, argued below. The
  induction proof fails because the argument needs an invariant.
- **GAP:** killable, with the stimulus that kills it.

Engine numbers come from `engine_map.py`. "Hold path" is the else branch of a
Hardcaml `if`, taken while the engine is inactive (halted, faulted, or on a
START/STOP/clear edge).

**Sample result:** 11 EQ-proven, 9 EQ-manual, 28 GAP.

- **Confirmed gaps.** 19 of the 28 gaps are confirmed by a kill: 16 by the
  directed tests and 4 by deep random, with 1 killed by both.
- **Unconfirmed gaps.** The other 9 need runs beyond practical simulation
  (185, 297, 313: 2^20 to 2^23 cycles or instructions) or scenarios not yet
  written (396, 979, 989, 1008, 1521, 2121).
- **No contradiction.** No EQ mutant is killed by any stage.
- **Estimate.** 9 of the 37 sampled survivors that are not proven equivalent
  were argued equivalent by hand: 24%, Wilson 95% interval 13% to 40%.
  Extrapolated to the 457, that is about 111 more equivalent mutants and a
  suite score of about 84% (82% to 87%). This is an estimate, not a
  measurement. The reported score subtracts only the 116 proven.

| id | region | mutation | class | RIP in full suite | killed by | reason / killing stimulus |
|---:|---|---|---|---|---|---|
| 98 | engine_ctrl | const1 A[1] | GAP | internal only (`blocked_cycles_1`, 36,026 cycles) | deep random, directed | blocked_cycles of engine 1 is left at 2 instead of 0 after ALU/SHL/SHR/JZ/NOT/TIME (opcodes 20-28). Only visible as a WAITPIN/WAITEVENT timeout two samples early. Kill: an ALU instruction immediately followed by a WAITPIN on a pin that never matches, with a small LIMIT; the fault-3 edge moves. |
| 174 | engine_ctrl | cnot1 A[0] ctrl 13 | GAP | never diverged | directed | blocked_cycles of engine 1: bit 0 flips once the count has bit 13 set. Needs a bounded wait of >= 8192 cycles; no test blocks that long. Kill: LIMIT 0x2108 then WAITPIN on a static pin; fault 3 arrives at a different cycle. |
| 185 | engine_ctrl | cnot1 B[4] ctrl 21 | GAP | never diverged | - | wait_timer countdown of engine 2: bit 4 flips while bit 21 is set, i.e. only for WAIT n with n >= 2^21. Kill: WAIT 0x200010 followed by a pin toggle (about 2.1 M cycles of simulation), or an inductive property timer' = timer - 1. |
| 195 | engine_ctrl | cnot1 A[4] ctrl 13 | GAP | never diverged | directed | Same as 174 for engine 0 (blocked_cycles): bit 4 flips when bit 13 is set; needs a bounded wait of >= 8192 cycles. The mutated count oscillates by 16, so LIMIT must not be a multiple of 32 (0x2100 does not kill it, 0x2108 does). |
| 268 | engine_ctrl | cnot1 D[2] ctrl 3 | GAP | never diverged | directed | repeat_count_0 (engine 3), D input: bit 2 flips when bit 3 is set. Needs COUNT n with bit 3 set (n >= 8) and a LOOP on engine 3; iteration count changes. Never happened in the suite. |
| 271 | engine_ctrl | const0 Y[13] | GAP | never diverged | directed | repeat_count of engine 1 stuck below 8192 (bit 13 forced 0). Kill: COUNT 0x2000 + LOOP on engine 1; loop exits early (visible in pin timing or the completed-instruction count). |
| 277 | engine_ctrl | const0 B[20] | EQ-proven | never diverged | - | Bit 20 of pc+1 on the instruction-execution path, which is only taken while pc < image_length (a 16-bit register), so the bit is always 0. |
| 279 | engine_ctrl | const0 A[22] | EQ-manual | never diverged | - | Clears blocked_cycles bit 22 on the XFER/FAULT hold path. blocked_cycles is nonzero only while a WAITPIN/WAITEVENT is issuing (every other completed instruction resets it; fault is followed by START, which resets it), so the path never sees a nonzero value. |
| 297 | engine_ctrl | cnot1 A[17] ctrl 20 | GAP | never diverged | - | completed_instructions of engine 1: bit 17 flips when bit 20 is set; needs >= 2^20 completed instructions before READ_SELECT 5. Long run (about 1 M cycles) or an inductive counter property. |
| 313 | engine_ctrl | cnot1 A[1] ctrl 23 | GAP | never diverged | - | completed_instructions of engine 1: bit 1 flips when bit 23 is set; needs >= 2^23 instructions before READ_SELECT 5. Practical only with a formal counter property (count' = count + finished). |
| 396 | engine_ctrl | const0 Y[10] | GAP | never diverged | - | completed_instructions of engine 3: bit 10 forced 0 on one opcode's update path. Needs >= 1024 completed instructions including that opcode, then READ_SELECT 5. |
| 423 | engine_ctrl | const0 A[15] | GAP | never diverged | directed | completed_instructions_0 (engine 2): bit 15 cleared on the hold path while halted; the count is readable while halted. Kill: run >= 32768 instructions, HALT, READ_SELECT 5. |
| 436 | engine_ctrl | cnot0 Y[4] ctrl 16 | GAP | internal only (`blocked_cycles_1`, 14,025 cycles) | directed | blocked_cycles of engine 1 left at 16 after TIME (and FAULT-class paths). Only visible as a WAITPIN/WAITEVENT timeout 16 samples early. Kill: TIME then a WAITPIN that times out. |
| 451 | engine_ctrl | cnot1 Y[12] ctrl 6 | GAP | never diverged | directed | repeat_count_0 (engine 3): bit 12 flips whenever bit 6 is set. Kill: COUNT 72 + LOOP on engine 3; iteration count wrong. The suite never used COUNT >= 64 on engine 3. |
| 478 | engine_ctrl | cnot0 A[5] ctrl 12 | GAP | internal only (`blocked_cycles_2`, 67,341 cycles) | directed | blocked_cycles_2 (engine 2) left at 32 after opcodes 21-28. Kill: XOR/AND/OR/SHL/SHR/JZ/NOT/TIME immediately followed by a WAITPIN that times out. |
| 565 | host | inv B[0] | EQ-manual | internal only (`image_loaded_2`, 113,642 cycles) | - | Reset value of image_loaded_2 becomes 1. The value is only used while image_writing is set, which only BEGIN sets, and BEGIN zeroes image_loaded; so the reset value is never used. |
| 570 | host | inv B[8] | EQ-manual | internal only (`image_length_0`, 19,882 cycles) | - | BEGIN sets image_length_0 to 0x100 instead of 0. The length is only used by a running engine, and START needs a COMMIT, which overwrites it. |
| 696 | host | const0 A[5] | GAP | never diverged | directed | OWN overlap check ignores engine 1 owning pin 5. Kill: engine 1 owns pin 5; another engine then OWNs pin 5; must be rejected with the sticky host fault and unchanged ownership. |
| 747 | host | cnot1 A[0] ctrl 2 | EQ-proven | never diverged | - | Read-nibble counter compare for case item 0: flipping bit 0 only when bit 2 is set can never make the value 0. |
| 852 | host | cnot1 Q[7] ctrl 6 | GAP | never diverged | directed | image_length_1 (engine 1): bit 7 of the register output is XORed with bit 6, and the hold path feeds the mutated value back, so a committed length of exactly 64 alternates 64/192 every cycle. Kill: a full 64-word image that falls through on a cycle where the length reads 192 (fault 2 expected at PC 64; the mutant runs on). With 64 NOPs the fall-through lands on a 64 cycle; a WAIT 1 prefix shifts it. |
| 952 | mover | const0 A[12] | GAP | internal only (`route_remaining_2`, 5 cycles) | directed | ROUTE word count of source engine 2 truncated below 4096 (bit 12 forced 0). Kill: ROUTE count 4097 from engine 2 and stream more than 1 word; the mutant disables the route after 1 word. |
| 979 | mover | const1 A[0] | GAP | internal only (`dma_round_robin`, 293,756 cycles) | - | DMA round-robin cursor bit 0 forced to 1 on its hold path (differs in 293,756 cycles). Only matters when two routes compete on the same edge. Kill: two enabled routes whose sources both hold a word and whose destinations accept on the same edge; check grant order. |
| 989 | mover | cnot1 Y[3] ctrl 5 | GAP | never diverged | - | route_remaining of source engine 0: bit 3 flips when bit 5 is set. Kill: ROUTE count >= 32 from engine 0 and transfer until the descriptor expires; the number of words moved changes. |
| 1008 | mover | const0 A[0] | GAP | never diverged | - | Host TX write priority over DMA for route 0's destination is lost (grant not inhibited). Kill: host window-2 write to engine D on the same edge as a DMA word from route source 0 to D. |
| 1040 | mover | const0 B[0] | EQ-proven | never diverged | - | No-op: the reset value of route_destination_3 is the constant 2'b00, whose bit 0 is already 0. |
| 1112 | mover | const0 A[0] | GAP | never diverged | deep random | Mover destination-3 decode. Killed by the deep random stage (256 cases from a new seed): the 64-case default budget is too small. |
| 1152 | mover | const0 Y[0] | GAP | never diverged | directed | FLUSH no longer disables route 1 when the flushed engine is its source or destination. Kill: enable route 1->D, FLUSH engine 1 (or D) while halted, then check no DMA word moves. |
| 1222 | events | cnot1 A[2] ctrl 4 | EQ-proven | never diverged | - | Compare-with-zero in the trigger/event path: bit 2 flips only when bit 4 is set, and the value is nonzero either way. |
| 1317 | events | cnot1 A[0] ctrl 6 | EQ-proven | never diverged | - | Compare-with-zero in the trigger/event path: bit 0 flips only when bit 6 is set, and the value is nonzero either way. |
| 1342 | events | const1 A[4] | EQ-proven | never diverged | - | Case-item compare (item 29) in engine 1's opcode-validity decode; proved by equiv_induct. |
| 1345 | events | const1 B[2] | EQ-proven | never diverged | - | No-op: the bound is the constant 4, whose bit 2 is already 1. |
| 1356 | events | cnot1 Y[3] ctrl 0 | GAP | internal only (`logical_output_2`, 8,461 cycles) | deep random | Ownership-masked pin decode on engine 0's event path; in the suite it changed logical_output_2 for 8,461 cycles without reaching a pin. Killed by the deep random stage. |
| 1371 | events | const0 B[2] | EQ-proven | never diverged | - | No-op: case-item constant 11 (LOOP) in engine 1's validity decode already has bit 2 = 0. |
| 1416 | events | const1 Y[0] | GAP | never diverged | directed | Engine 1 operand check bc_zero forced true: PUSH/NOT/TIME with nonzero b or c fields are executed instead of faulting with code 1. Kill: engine 1 runs e.g. TIME x with c != 0 and must fault 1. |
| 1521 | pins | cnot1 B[4] ctrl 3 | GAP | internal only (`logical_output_2`, 5,155 cycles) | - | logical_output_2 (engine 0) XFER data-pin update: when the data pin is 3 and the bit is 1, bit 4 also toggles (differs 5,155 cycles). Kill: XFER on engine 0 with data on pin 3 while engine 0 owns and drives pin 4. |
| 1577 | pins | inv A[3] | EQ-manual | internal only (`logical_enable`, 114,442 cycles) | - | logical_enable (engine 3) bit 3 toggles on the hold path, which is only used while the engine is inactive; its pins are masked by run/fault then, and START/STOP zero the register (differs 114,442 cycles, never visible). |
| 1634 | pins | inv A[7] | EQ-manual | internal only (`logical_enable_2`, 97,191 cycles) | - | Same as 1577 for logical_enable_2 (engine 0) bit 7. |
| 1694 | pins | cnot1 B[0] ctrl 1 | GAP | never diverged | deep random | logical_output_0 (engine 2) XFER output update corrupts pin 0 (clears it, or keeps it when the data pin is 0). Kill: SPI-style XFER with drive on engine 2 while engine 2 also owns and drives pin 0. Killed by the deep random stage. |
| 1753 | engine_xfer | cnot1 Y[5] ctrl 7 | GAP | never diverged | directed | transfer_period (engine 2): during a transfer the period register alternates between b and b^32 every cycle when bit 7 is set; half-periods >= 128 are never used. Kill: XFER with an odd half-period >= 128 (201) on an owned, enabled clock pin; with an even period every reload lands on the same phase. |
| 1761 | engine_xfer | const0 A[2] | EQ-manual | internal only (`transfer_tick_2`, 4,581 cycles) | - | transfer_tick_2 (engine 0) bit 2 cleared on the hold path while the engine is inactive; a transfer only runs while active, STOP zeroes transfer_edges and START zeroes the tick. |
| 1943 | engine_xfer | cnot1 Y[0] ctrl 3 | EQ-proven | never diverged | - | transfer_edges_2 update path; proved by equiv_induct. |
| 1978 | imem | const1 Q[1] | EQ-manual | internal only (`_1623`, 282,346 cycles) | - | Bit 1 of the host write-nibble shift register stuck at 1. The assembled word is {new nibble, reg[31:4]}, so reg[3:0] is shifted out and never read (differs 282,346 cycles, never visible). |
| 2121 | fifo | cnot1 A[3] ctrl 15 | GAP | internal only (`_7456[6]`, 12,065 cycles) | - | DMA word data: bit 3 flips when bit 15 is set, corrupting routed words in the destination TX FIFO (FIFO words differ). Kill: route words with bit 15 set and have the destination engine PULL and transmit them on an owned pin, or route them back to an RX FIFO the host reads. |
| 2147 | fifo | const1 B[0] | EQ-proven | never diverged | - | FIFO pointer update enable: forcing one AND operand to 1 is harmless because the other operand implies it; proved by equiv_induct. |
| 2245 | engine_data | inv A[3] | EQ-manual | internal only (`x_2`, 97,224 cycles) | - | x_2 (engine 0) bit 3 toggles on the hold path while inactive; x is not host-readable and START resets it. |
| 2279 | engine_data | const0 B[15] | GAP | never diverged | directed | SHR result written to rx_0 (engine 1): bit 15 forced 0. Kill: rx = 0x80000000 (LOAD 1, SHL 31), SHR 16, PUSH; the host reads 0x8000. |
| 2313 | engine_data | cnot1 A[1] ctrl 0 | EQ-proven | never diverged | - | Compare of engine 3's 2-bit register select with 0: bit 1 flips only when bit 0 is set, and the value is nonzero either way. |
| 2344 | engine_data | const0 A[6] | EQ-manual | internal only (`tx_0`, 13,210 cycles) | - | tx_0 (engine 1) bit 6 cleared on the hold path while inactive; tx is not host-readable and START resets it. |

### Test gaps

These are ordered by the number of survivors involved. Each links the evidence
to a change in stimulus, checking or formal.

1. **Long-horizon counters are never exercised or never read back.** Most of
   the 146 remaining `engine_ctrl` survivors are high bits of the
   completed-instruction count (READ_SELECT 5), the LIMIT/blocked-cycle
   counter, the WAIT timer, the COUNT/LOOP repeat counter, ROUTE word counts
   and the timestamp.
   - Random cases run 2,000 host cycles. The RIP run confirms that the suite
     never sets these bits.
   - Two cheap directed tests, COUNT 40000 with a READ_SELECT 5 after HALT
     and a LIMIT 0x2108 timeout, kill 8 survivors.
   - Bits at 2^20 and above are out of reach for simulation. Inductive formal
     properties would cover them, for example `completed' = completed +
     finished`, `timer' = timer - 1` while waiting, and `blocked' = blocked + 1`
     while blocked. They are cheap next to the existing
     `processor_invariants` proofs.
2. **Timeout timing after arbitrary instructions.** Every completed
   instruction resets the blocked-cycle count. Mutants that leave it at 2, 16
   or 32 after ALU, SHL, SHR, NOT or TIME instructions change nothing unless
   a WAITPIN/WAITEVENT that times out follows immediately.
   - In the suite, `blocked_cycles` was corrupted for 67,341 cycles
     (mutant 478) with no visible effect.
   - One directed test kills 10 survivors. The random generator should place
     timing-out waits directly after every opcode class.
3. **Parity-sensitive behaviour.** A mutated register output that feeds its
   own hold path makes the value oscillate every cycle. The mutant is visible
   only with odd timing, so the generator should randomize parities:
   - odd XFER half-periods ≥ 128 (the suite uses none);
   - LIMIT values that are not multiples of 32;
   - odd-length preludes before a program falls off a full 64-word image.
4. **Host command corner cases per engine and pin.** The suite never
   exercises:
   - an OWN that overlaps engine 1's pin 5 (696);
   - invalid b/c operand fields of PUSH/NOT/TIME on engine 1 (1416);
   - FLUSH disabling a route (1152);
   - ROUTE counts ≥ 4096 (952);
   - a full-capacity 64-word image falling off the end (852).

   The deliberate faults and rejected commands in the random generator are
   sparse across the opcode × field × engine and command × engine × pin
   spaces. A per-engine sweep of rejections would close this.
5. **Mover arbitration is unobservable.** In mutant 979 the round-robin
   cursor is wrong for 293,756 cycles of the suite without effect, because
   two routes never compete on the same edge. Mutant 1008 loses host-over-DMA
   priority on a same-edge collision. The fix is contention scenarios whose
   result depends on grant order, for example two sources routed to one
   destination that then transmits the words.
6. **Data that is written but never observed.** Examples:
   - routed words with bit 15 set that the destination engine never
     transmits (2121);
   - high bits reaching rx only through SHR (2279);
   - XFER data-pin updates on pins the engine does not also drive (1521).

   The random generator pushes or branches on computed registers "often". A
   rule that every computed value reaches the host (PUSH plus read, or
   READ_SELECT 6) would make these propagate.

Not gaps: 9 of the 48 sampled survivors are equivalent by argument.

- **Hold-path mutations** of `logical_enable`, `x`, `tx` and
  `transfer_tick` while the engine is inactive: pins are masked by run and
  fault, and START resets these registers.
- **A `blocked_cycles` path** (279) that only ever sees 0: the count is
  nonzero only while a WAITPIN/WAITEVENT issues.
- **Loader values** (`image_loaded`, `image_length`) that are overwritten
  before any use.
- **Dead bits** of the host nibble shift register.

`processor_inductive`-style invariants, for example "`blocked_cycles` ≠ 0
only while a WAITPIN/WAITEVENT issues", would let the equivalence proof
cover these too.

### Jobs

| stage | job ids | tasks × CPUs | mutants |
|---|---|---|---:|
| baseline, invalid: the test tarball lacked `configs/`, every test errored in about 2 s, results deleted | 23746524, 23746526 | 2 × 1 | controls |
| baseline | 23746971 (fast), 23746972 (full) | 2 × 1 | controls |
| fast set | 23747742 (`mit_preemptable`), 23747893 (`mit_quicktest`); 23747834 cancelled (40 CPUs did not fit a quicktest node) | 240 × 1, 2 × 16 | 2,420 |
| full suite | 23748233, 23750029 (`mit_preemptable`), 23750030 (`mit_quicktest`), 23750194 (`mit_normal`) | 13 × 8, 56 × 4, 2 × 16, 12 × 8 | 987 |
| equivalence | 23748253 (negative controls), 23750059, 23751969, 23751980 | 1 × 8, 1 × 12, 8 × 8, 2 × 16 | 41 + 573 |
| deep random | 23751970 (`mit_preemptable`), 23751971 (`mit_normal`) | 40 × 4, 12 × 8 | 573 |
| RIP | 23752696, 23752709 | 36 × 4, 1 × 12 | 573 |
| directed tests | 23757440 (v1), 23760944 (v2), 23766410 (final) | 2 × 16 | 573 + controls |

Short `srun` steps on `mit_quicktest` ran mutant generation and the checks
(for example 23746129 and 23755060). The per-user CPU cap on
`mit_preemptable` was shared with the other campaigns, so the arrays ran on
44 to about 250 CPUs at a time. The fast stage finished about 12 minutes after
its tasks started, and the full stage about 15 minutes after.
Per-mutant results are kept under
`<work dir>/vcamp/mutation/results/<stage>/<id>.json`.
The repository keeps the summaries:

- `campaigns/mutation/results/summary.json`;
- `mutant_status.tsv`, one row per mutant;
- `survivor_evidence.tsv`;
- `sample_classification.tsv`;
- `gen_params.txt`, with the mutation-list sha256.

## Gap closure

The findings above were closed in `test/` (the four gap-closure modules and
generator generation 2, see [`test/README.md`](../test/README.md), "Gap-closure
tests"), and the mutation score was measured again on the same 2,420 mutants.
Job ids and per-mutant results are in `<work dir>/test-gaps/` (`manifest.json`,
`mutation/summary_gaps.json`, `mutation/results/<stage>/<id>.json`).

### Changes

- **Deselect (random finding 2).** The suggested fix is right as far as it
  goes. With `continue` instead of `break` in `run_case`, every case that draws
  a deselect runs it, and every operation before the budget is unchanged
  (model-only, default seed plus seeds 1 to 16, 1,088 cases: 107 of 107
  deselects run, against 3 with `break`). But the deselect still runs after the
  budget, just before the epilogue, so nothing but status reads follows it.
  Generation 2 therefore also moves it to a random point in the first 70% of
  the traffic, drawn from a separate random stream (99 of the 107 now run inside
  the budget, 101 with engines running). `PE_RANDOM_GEN=1` draws the old cases.
- **Rejections (random finding 3).** Generation 2 inserts one to three command
  sequences in 70% of cases: BEGIN with a payload, bad COMMIT lengths, STOP and
  EVENT naming absent engines, ROUTE with payload bits 21 to 23, and aborted or
  overfull reloads. The model decides acceptance. On the default seed BEGIN,
  COMMIT, STOP, ROUTE and EVENT are rejected 13, 23, 9, 6 and 7 times, so all
  twelve commands are now both accepted and rejected. Finding 6: `PE_MINIMIZE`
  now shrinks replays too.
- **Directed tests.** The twelve directed tests are `test/test_directed.py`,
  made variant-independent. Two tests from the re-run below were added there:
  far JMP/LOOP/JZ targets with each PC bit 6 to 23 set, and XFER on every TX pin
  next to driven pins.
- **Long counters (mutation test gap 1).** `test/test_counters.py`: every
  engine completes more than 2^16 instructions while a 0xFFFF-word route drains,
  exact ROUTE drains, LIMIT and WAIT sweeps. `test/test_timewarp.py`: counter
  carries up to bit 31 and the 2^32 timestamp rollover. The harness skips time
  only in states that the model shows to be pure counting, and deposits the
  same relative change into the RTL registers. RTL only.
- **Mover (mutation test gap 5).** `test/test_mover.py`: 2, 3 and 4 routes
  (one a self-route) eligible on one edge, and host TX writes against eligible
  routes. An independent monitor checks every grant against the arbitration
  rule. On the design of record: 258 edges with at least 2 eligible routes, 164
  with 3, 81 with 4, and 12 host-priority collisions.

### Suite

The suite has 66 tests (39 before, 27 new).

| run | result | job |
|---|---|---|
| `make clean; make` (CI), cocotb 2.0.1, Icarus 13.0 (conda-forge build of tag `v13_0`) | 66 pass; 83 s of test time, 89 s wall, of which the four new modules take 27 s | 23819699 |
| all six variants (`PE_VARIANT`), Icarus 14 | 66 pass each | 23819698 |
| gate level (`GATES=yes`, the campaign's routed netlist `fe135632…`) | 36 pass, 30 skip, 0 fail (long and time-warp tests skip) | 23819700 |

### Mutation score

Same mutants, runner and `base.il`. Only the test tree changes.

- **Survivors (stage `gaps`).** The 573 survivors ran the five new or changed
  modules. Every module ran, so that each kill can be attributed.
- **Recheck.** The 915 mutants that the old suite killed only with
  `test_random` (whose stimulus changed) ran the new random cases, legacy and
  the new modules, stopping at the first failure.
- **Stage `gaps2`.** The 415 mutants still alive ran `test_directed` with the
  two tests added afterwards.
- **Carried over.** The 932 kills by unchanged modules (smoke, protocols,
  flagship, legacy) were carried over.
- **Controls.** `orig` and mutant 0 survived every stage. None of the 116
  proven-equivalent mutants was killed.

| | old suite | extended suite |
|---|---:|---:|
| killed | 1,847 | 2,060 |
| score, killed / (2,420 − 116) | 80.2% | **89.4%** |
| score without `test_timewarp` | 80.2% | 85.8% (1,976) |

- **Old survivors killed.** The extended suite kills 216 of the 457 survivors
  that were not proven equivalent. By module: `test_timewarp` 118,
  `test_directed` 87, `test_mover` 28, `test_counters` 23, `test_random` 2.
  One mutant can count for several modules. 82 of the 216 are killed only by
  the time warp.
- **Kills lost.** Three mutants that old random cases killed survive the new
  random stimulus (2341, 2391 and 2394, all `engine_data`).
- **Time-warp caveat.** The time warp is white-box stimulus: it deposits
  reachable counter values. Its kills are still observed at the pins and host
  reads. Mutant 185 (WAIT timer, about 2.1 M cycles) and mutant 313 (completed
  count, 2^23 instructions) are killed this way.

| region | mutants | proven equivalent | killed, old suite | killed, extended suite |
|---|---:|---:|---:|---:|
| engine_ctrl | 550 | 23 | 343 (65.1%) | 456 (86.5%) |
| host | 400 | 28 | 349 (93.8%) | 356 (95.7%) |
| events | 300 | 32 | 222 (82.8%) | 236 (88.1%) |
| mover | 250 | 6 | 180 (73.8%) | 221 (90.6%) |
| pins | 250 | 7 | 196 (80.7%) | 221 (90.9%) |
| engine_xfer | 200 | 9 | 155 (81.2%) | 161 (84.3%) |
| engine_data | 180 | 2 | 146 (82.0%) | 145 (81.5%) |
| fifo | 130 | 7 | 117 (95.1%) | 121 (98.4%) |
| shared | 90 | 2 | 81 (92.0%) | 81 (92.0%) |
| imem | 60 | 0 | 50 (83.3%) | 52 (86.7%) |
| timestamp | 10 | 0 | 8 (80.0%) | 10 (100.0%) |

By mode, extended suite (old suite in parentheses):

| mode | score |
|---|---:|
| `cnot1` | 80.4% (53.3%) |
| `const0` | 83.1% (67.4%) |
| `const1` | 90.8% (86.4%) |
| `inv` | 95.4% (93.3%) |
| `cnot0` | 94.8% (90.3%) |

**Survivors.** 244 survivors are neither proven equivalent nor killed.

| region | survivors |
|---|---:|
| engine_ctrl | 71 (55 of them on blocked_cycles) |
| engine_data | 33 |
| events | 32 |
| engine_xfer | 30 |
| mover | 23 |
| pins | 22 |
| host | 16 |
| imem | 8 |
| shared | 7 |
| fifo | 2 |

The blocked_cycles survivors outlive a test that follows every way of
completing an instruction with a bounded wait (`blocked_paths`). By the argument
for mutant 279 above, many are likely hold-path mutations that cannot be
observed. They were not classified here.

Jobs: `gaps` 23813966, `recheck` 23813967, `gaps2` 23817415, pilot 23811152,
final-tree controls (stage `suite`) 23819701, generator measurement 23806285.

## Variant diet4 (6x4) campaigns

The 6x4 candidate is design variant `diet4` ([`variants.md`](variants.md);
ISA version 3: 4-word queues built from registers, asynchronous reset with
synchronized release, no debug counters, 7-bit saturating PC, byte-lane
shifts). The campaigns above ran on the design of record (`base`) at
`73536f0`. This section repeats the random lockstep campaign and the mutation
campaign on `diet4`, with the suite and generator of `c118027` (tag
`v0.1-hardened`: 66 tests, generator generation 2), so that both numbers exist
for either tile size. The scripts are the ones in `campaigns/random/` and
`campaigns/mutation/`, with the design-variant options described in each
README ("Design variants"). Summaries:
[`campaigns/random/results/diet4/`](../campaigns/random/results/diet4/) and
[`campaigns/mutation/results/diet4/`](../campaigns/mutation/results/diet4/).
Job ids: `<work dir>/sixby4-verify/manifest.json`. All runs are from
2026-09-25 21:40 to 2026-09-26 01:00 (EDT).

### Setup

| item | value |
|---|---|
| design under test | frozen `git archive` of `c11802730ea10a9501ce16517ca6218fd8b7c43e`; `build/variants/diet4/protocol_emulator_core.v` generated in the snapshot by `scripts/gen_variants.sh diet4` (job 23974763; its checks regenerate `src/protocol_emulator_core.v` byte for byte), sha256 `cc27c465…`, the core of the diet4 runs in [`sweep.md`](sweep.md); `src/project.v` sha256 `2aacfd24…` |
| reference model | the snapshot's `test/model/variant.py`, configured from `configs/variants/diet4.json` by `PE_VARIANT=diet4` (`test/variants.py` checks the file against its own table). Every result file records the variant, `fifo_words` 4 and the model options |
| stimulus | `random_gen.make_case`, generation 2 (the snapshot default: mid-traffic deselect and rejected command sequences). On `diet4` the generator also draws byte-lane shift counts (15% deliberately off-lane, fault code 1) and jumps to targets of 128 or more (8%, saturated to PC 127) |
| gate-level netlist | the routed netlist of sweep run `diet4-cc27c4-6x4-fp6_tworow-d65-p20-h0p1_0p05-full-t32` (job 23763343_0, full flow, LVS 0; [`sweep.md`](sweep.md)), `final/nl`, sha256 `98b31b96…` (gzip `c4a2ddfb…`). The eight SRAM macros are simulated with the behavioral models, the cells with the IHP `sg13cmos5l` Verilog models, zero delay. The 6x4 sign-off runs of [`6x4.md`](6x4.md) had no final netlist yet at 2026-09-26 01:00; the gate-level sample should be repeated on the netlist that is submitted |
| simulator | Icarus Verilog 14.0 (devel, OSS CAD Suite 2026-07-29), cocotb 2.0.1, Python 3.12 |
| compute | Slurm `mit_normal`, `mit_quicktest` and `mit_preemptable` (with `--requeue`); `mit_preemptable` started few tasks during these runs |

Fidelity check (job 23975155): seed 1 through the unmodified upstream flow
(`upstream_run.sh`, `make COCOTB_TEST_MODULES=test_random` with
`PE_VARIANT=diet4`, which compiled its own simulation from the same sources)
passed 64 cases. Its per-case lockstep cycle counts (64 of 64) and its
18-line coverage summary are identical to seed 1 of `d4-rtl-default` run
through the campaign driver.

### Random lockstep campaign

Generator variants as in "Random differential campaign" above. On a
generation-2 snapshot the `deselect` variant inserts one mid-traffic deselect
into every case that has none (generation 2 already moves the optional one),
so that every case of `d4-rtl-xcov-deselect` has one mid-traffic deselect
(16,384 deselects in 16,384 cases).

| campaign | generator | level | seeds | cases/seed | host cycles/case | cases run | passed | failed | infra errors | lockstep cycles | sim CPU-h | Slurm array |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `d4-rtl-default` | default | RTL | 0x1..0x800 (2048) | 64 | 2,000 | 131,072 | 131,072 | 0 | 0 | 495,478,116 | 26.9 | 23974967 |
| `d4-rtl-xcov-default` | default | RTL | 0x801..0xc00 (1024) | 64 | 2,000 | 65,536 | 65,536 | 0 | 0 | 247,744,648 | 16.6 | 23974968 |
| `d4-rtl-long` | default | RTL | 0x100001..0x100100 (256) | 64 | 20,000 | 16,384 | 16,384 | 0 | 0 | 356,937,617 | 23.0 | 23974969 |
| `d4-rtl-xcov-dense` | dense | RTL | 0x300001..0x300100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 176,974,205 | 10.9 | 23974972 |
| `d4-rtl-xcov-faulty` | faulty | RTL | 0x400001..0x400100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 160,250,428 | 11.2 | 23974973 |
| `d4-rtl-xcov-hostile` | hostile | RTL | 0x500001..0x500100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 158,958,462 | 12.7 | 23974974 |
| `d4-rtl-xcov-deselect` | deselect | RTL | 0x600001..0x600100 (256) | 64 | 2,000 | 16,384 | 16,384 | 0 | 0 | 61,540,549 | 5.3 | 23974976 |
| `d4-gl-default` | default | GL | 0x1..0x40 (64) | 8 | 2,000 | 512 | 512 | 0 | 0 | 1,941,597 | 0.7 | 23975172 |
| `d4-gl-extended` | default | GL | 0x41..0x140 (256) | 32 | 2,000 | 8,192 | 8,192 | 0 | 0 | 30,945,971 | 9.9 | 23975174 |
| `d4-gl-hostile` | hostile | GL | 0x500001..0x500040 (64) | 16 | 8,000 | 1,024 | 1,024 | 0 | 0 | 9,940,328 | 3.3 | 23975175 |
| `d4-gl-deselect` | deselect | GL | 0x600001..0x600040 (64) | 16 | 2,000 | 1,024 | 1,024 | 0 | 0 | 3,845,946 | 1.2 | 23975176 |
| **total** | | | 4,800 seeds | | | **289,280** | **289,280** | **0** | 0 | **1,704,557,867** | 121.9 | |

- **RTL:** 4,352 seeds, 278,528 cases, 1,657,884,025 lockstep cycles, 0
  failures. The required 2,048 seeds x 64 cases of the default generator are
  `d4-rtl-default`; with `d4-rtl-xcov-default` the default generator ran 3,072
  seeds (196,608 cases). `d4-rtl-long` ran 256 seeds at 20,000 host-traffic
  cycles per case.
- **Gate level:** 448 seeds, 10,752 cases, 46,673,842 lockstep cycles, 0
  failures. Each GL case took exactly as many lockstep cycles as the same seed
  and case at RTL (10,752 of 10,752 paired with `d4-rtl-default`,
  `d4-rtl-xcov-hostile` and `d4-rtl-xcov-deselect`).
- Every seed produced a result file: no timeouts, no simulator crashes, no
  infrastructure errors. There was no failure to triage.

Coverage (`random_gen.Coverage`, merged over all campaigns): no hole in any
bin list of `merge.py`, and none in `d4-rtl-default` alone.

- All 29 non-FAULT opcodes completed on each of the 4 engines (fewest:
  engine 2 HALT, 115,927).
- All 18 targeted fault bins were hit, the 17 of the base campaign plus
  `code 1 from SHR` (fewest: code 1 from PINS, 37,302), and all 255 explicit
  `FAULT n` codes.
- XFER: 32/32 flag combinations (110,604 to 373,647 issues each) and every bit
  count class.
- Mover: 373,011 word transfers.
- Host commands: all twelve both accepted and rejected, in `d4-rtl-default`
  alone as well (BEGIN, COMMIT, STOP, ROUTE and EVENT rejected 25,316, 50,566,
  16,830, 16,588 and 16,843 times). These are the five bins that the
  generation-1 generator never hit on the base design (finding 3 above).
- Host traffic: every bin, including the two program-word bins of
  generation 2.
- `diet4`-specific bins: 249,635 byte-lane shift faults (off-lane count,
  code 1) and 171,894 jumps saturated to PC 127.
- Deselect: 13,091 deselects in the 131,072 cases of `d4-rtl-default` (10.0%).
  The xcov bin "reset/deselect with engines running" is hit 87.8 times per
  1,000 default cases (the generation-1 base campaign: 2.4) and 889 times per
  1,000 `deselect` cases.

Extended cross coverage (`xcov.py`, 131,072 cases in five campaigns): all 71
bins hit, 0 observer errors. The rarest, per 1,000 cases:

| bin | default (65,536 cases) | best variant (16,384 cases each) |
|---|---:|---:|
| synchronous start of 4 engines | 0.20 (13 hits) | 1.77 (`dense`) |
| host TX write and engine PULL on the same FIFO, same edge | 0.90 | 4.7 (`dense`) |
| mover blocked by a host TX write to its destination on the same edge | 1.14 | 1.89 (`dense`) |
| mover arbitration with at least 2 eligible routes | 2.75 | 39.8 (`dense`) |
| four engines inside XFER at once | 4.12 | 48.5 (`faulty`) |
| synchronous start of 3 engines | 4.14 | 15.7 (`dense`) |
| WAITPIN/WAITEVENT timeout with LIMIT 1 | 8.6 | 42.9 (`dense`) |

The rarest bins are largely the same as on the base design ("Coverage"
above). In default cases the fault output `uo[7]` is high in 69.8% of lockstep
cycles (92.2% at 20,000 host cycles per case), and no engine runs in 40.3% of
cycles.

Negative control (`PE_INJECT_MODEL_BUG=xor`, the stimulus of
`d4-rtl-default` seeds 1 to 64, array 23974977): all 64 seeds and 1,400 of
4,096 cases (34.2%) failed, every one a lockstep mismatch.

Full tables: [`campaigns/random/results/diet4/report.md`](../campaigns/random/results/diet4/report.md).

### Mutation campaign

Same method as "Mutation testing" above: Yosys `mutate`, region-biased with
the same quotas and seed (`campaigns/mutation/campaign-diet4.env`), the fast
set, then the whole suite on the fast-set survivors, then the equivalence
proof on the survivors. What differs:

| item | value |
|---|---|
| mutated core | the `diet4` core above (`base.il`: 3,387 cells, against 3,579 for `base`); 2,420 mutants, `inv` 606, `const0` 599, `const1` 607, `cnot0` 315, `cnot1` 293 (job 23974949) |
| regions | `diet4` has no FIFO memories: each queue word is an anonymous register. `region_map.py` now takes groups of anonymous registers that load the same data signal under different enables as queue storage when a core has no memory arrays; it finds the 32 words (8 queues x 4) and maps 376 cells to `fifo`. The base core's region map is unchanged, byte for byte |
| second stage | stage `suite`: the snapshot's whole suite, the nine modules of `test/Makefile` (66 tests: smoke, protocols, flagship, legacy, random, directed, mover, counters, timewarp), defaults, stopping at the first failing module, budget 3,600 s. It replaces the 39-test `full` stage |
| variant plumbing | every `make` gets `PE_VARIANT=diet4` and `PE_CORE=<mutant>`; the variant's firmware images are in the test tree and its unmutated core is not, so a run that did not use the mutant would fail to build |
| equivalence | the `diet4` flops have asynchronous resets: the proof keeps the `$adff` outputs as matched points and runs `async2sync` on both copies before `equiv_make` |
| controls | `orig` and mutant 0 pass the fast set (job 23975067) and all 66 tests of the suite with none skipped (job 23975068; `test_timewarp` runs its 7 tests on the variant). Mutant 0 is proven equivalent (job 23975069). All 40 negative controls, a random sample (`random.seed(20270118)`) of fast-killed mutants, are "not proven" (job 23976517). All 38 static no-op mutants (`noop_check.py`) are proven equivalent |

**Result.** 2,028 of 2,420 mutants are killed (1,346 by the fast set, 682 more
by the suite), 0 time out and 0 error. 107 of the 392 survivors are proven
equivalent. The mutation score is **2,028 / (2,420 − 107) = 87.7%**. 285
survivors are neither killed nor proven equivalent.

| region | cells | mutants | killed (fast) | killed (suite) | survived | proven equivalent | not proven | score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| engine_ctrl | 664 | 550 | 243 | 189 | 118 | 23 | 95 | 82.0% |
| host | 286 | 400 | 345 | 25 | 30 | 13 | 17 | 95.6% |
| events | 580 | 300 | 168 | 61 | 71 | 29 | 42 | 84.5% |
| mover | 155 | 250 | 95 | 122 | 33 | 11 | 22 | 90.8% |
| pins | 313 | 250 | 118 | 98 | 34 | 13 | 21 | 91.1% |
| engine_xfer | 212 | 200 | 98 | 52 | 50 | 13 | 37 | 80.2% |
| engine_data | 680 | 180 | 72 | 76 | 32 | 0 | 32 | 82.2% |
| fifo | 376 | 130 | 77 | 46 | 7 | 0 | 7 | 94.6% |
| shared | 83 | 90 | 78 | 7 | 5 | 3 | 2 | 97.7% |
| imem | 36 | 60 | 44 | 4 | 12 | 2 | 10 | 82.8% |
| timestamp | 2 | 10 | 8 | 2 | 0 | 0 | 0 | 100.0% |
| **total** | 3,387 | 2,420 | 1,346 | 682 | 392 | 107 | 285 | **87.7%** |

| mode | mutants | killed (fast) | killed (suite) | survived | proven equivalent | not proven | score |
|---|---:|---:|---:|---:|---:|---:|---:|
| inv | 606 | 432 | 135 | 39 | 7 | 32 | 94.7% |
| const0 | 599 | 268 | 192 | 139 | 40 | 99 | 82.3% |
| const1 | 607 | 387 | 141 | 79 | 14 | 65 | 89.0% |
| cnot0 | 315 | 190 | 99 | 26 | 6 | 20 | 93.5% |
| cnot1 | 293 | 69 | 115 | 109 | 40 | 69 | 72.7% |
| **total** | 2,420 | 1,346 | 682 | 392 | 107 | 285 | **87.7%** |

Which module kills first: fast set `test_smoke` 586, `test_random` (8 cases)
427, `test_protocols` 289, `test_flagship` 44; suite (on fast-set survivors)
`test_random` (64 cases) 383, `test_legacy` 152, `test_directed` 58,
`test_timewarp` 43, `test_counters` 28, `test_mover` 18.

**Survivors not proven equivalent** (285), by the nearest named state that the
mutated statement feeds (`describe.py`): `blocked_cycles` 77, `mailbox` 42,
`transfer_tick` 13, host read/engine select 12, `dma_round_robin` 12,
`route_remaining` 10, `logical_output` 10, the rest 9 or fewer each. They were
not classified by hand.

**Deep random** (stage `deep`: `test_random`, 256 cases, `PE_SEED=0xD33B2027`,
on the 285): it kills 79 (`engine_data` 20, `engine_ctrl` 17, `events` 12,
`pins` 12, `host` 8, `engine_xfer` 5, `mover` 3, `fifo` 2). With them,
(2,028 + 79) / 2,313 = 91.1%; this is the suite plus 256 random cases of one
more seed, not the suite. 206 survivors remain neither killed nor proven
equivalent.

**Comparison with the base campaign.** The base core scored 89.4% with the
same 66-test suite ("Gap closure" above) and 80.2% with the 39-test suite.
The two mutant sets are drawn with the same quotas and seed from different
netlists, so the two scores describe two designs and are not a paired
comparison. In both, `engine_ctrl` holds the largest group of unproven
survivors (`diet4` 95; `base` with the same suite 71, 55 of them on
`blocked_cycles`).

Compute: fast set 11.1 CPU-hours, suite 26.8 CPU-hours, equivalence 3.6
CPU-hours, deep random 13.2 CPU-hours (sums of per-mutant run times).

With the design-variant options unused, the modified scripts reproduce the
committed summaries of the base campaigns byte for byte
(`campaigns/mutation/results/summary.json` and `mutant_status.tsv`,
`campaigns/random/results/report.md`; jobs 23981605 and 23986500, the second
with the final scripts), and `region_map.py`
reproduces the base core's region map and selections byte for byte.

| stage | job ids | tasks x CPUs | mutants |
|---|---|---|---:|
| mutant generation | 23974822 (stopped: no cells in region `fifo`, before the register-queue mapping), 23974949 | 1 x 2 | 2,420 |
| controls | 23975067 (fast), 23975068 (suite), 23975069 (equivalence probe, ids 0 to 7) | 2 x 1, 2 x 1, 1 x 8 | `orig`, 0 |
| fast set | 23975134, 23975255, 23975687, 23976372 (`mit_quicktest`); 23975133 cancelled before it started | 2 x 8, 3 x 8, 3 x 8, 2 x 8 | 2,420 |
| suite | 23976505 (`mit_preemptable`/`mit_normal`; its preempted tasks were cancelled once their ids had run in other arrays), 23976507, 23977852, 23980054, 23982956, 23983953 (`mit_quicktest`); 23983632 cancelled before it started | 8 x 8, 4 x 8, 5 x 8, 2 x 8, 2 x 8, 1 x 4 | 1,074 |
| equivalence | 23982705, 23983639, 23984313 | 3 x 8, 1 x 8, 1 x 4 | 392 |
| equivalence negative controls | 23976517 | 1 x 8 | 40 |
| deep random | 23984397 (`mit_normal`; two pending tasks cancelled once their ids had run in other arrays), 23984398, 23984950, 23985826 (`mit_quicktest`) | 4 x 8, 2 x 8, 3 x 8, 3 x 8 | 285 |
