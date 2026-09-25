# formal_depth: deeper and wider formal verification

This directory extends [`formal/`](../formal/README.md) in two ways:

1. **Deeper.** It re-runs the `formal/` jobs with a portfolio of solvers
   (one Slurm array task per solver) to push every bounded job deeper, and
   it records depth against time.
2. **Wider.** It adds seven new property sets, each over the whole
   four-engine processor with the exact IHP FUNCTIONAL SRAM models. The seven
   are host-port atomicity, program-load safety, mover conservation, the
   round-robin grant bound, fault release, pin ownership and open-drain
   safety, and an unbounded `engine_safety`. Each set has non-vacuity covers
   and mutant negative controls.

Results, bounds, run times, Slurm job ids and what is still unproven are in
[`docs/formal-depth.md`](../docs/formal-depth.md). `formal/` itself is not
modified. The last section of that document lists what should move into
`formal/` and CI.

Everything here runs against a committed revision (`FD_REV`, default
`73536f0`), exported with `git archive`. It never reads the working tree.

## Layout

| Path | What it is |
|---|---|
| `gen/generate_fd.ml` | Observation generator. It elaborates exactly `Processor.create_refinement ~debug:true` (the circuit `formal/` proves) and adds output ports only. The new ports are the `fv_*` engine registers (as in `formal/gen/generate_fv.ml`), the host-port registers, the SRAM data-in pins, the FIFO heads and pointers, and the FIFO storage arrays (named for `expose_fifo_mem.py`). The result is `protocol_processor_fd`. |
| `gen/expose_fifo_mem.py` | Adds flat output ports that read the eight FIFO storage arrays. |
| `gen/make_dut_include.py` | Writes `harness/fd_dut.vh`, which declares one wire per DUT port and the instance. |
| `gen/mutants.py` | Builds the 13 negative-control netlists from a private copy of `hardcaml/lib`. Each one makes one or two exact source substitutions, and every substitution must match exactly once. |
| `gen/make_engine_inductive.py` | Copies `formal/engine_safety.sv` with every assertion unchanged, renames the module and splices in `harness/engine_safety_strengthen.vh`. |
| `harness/*.sv` | The property harnesses (see below). |
| `harness/fd_invariants.vh` | Common state invariants that every processor harness asserts as induction strengthening. With `-DFD_FROM_ANY` they are assumed on the first cycle instead. |
| `jobs.py` | The job table: harness, mode, depth, engines, expectation and resource class. |
| `portfolio.py` | Expands jobs into one sby file per (job, engine), runs one task (`run`), and summarizes (`summarize`), including per-step start times (depth vs. time) and the labels of any failed assertions. |
| `submit.sh`, `array.sbatch` | One Slurm array per resource class on `mit_preemptable,mit_normal` with `--requeue`. `light` is 2 CPUs / 8 GB and `heavy` is 2 CPUs / 32 GB. |
| `run_parallel.sh` | Runs a whole task list inside one allocation. Use it for `mit_quicktest` smoke runs, where a large array hits the per-user submit limit. |
| `run.sh` | The top-level driver: `generate`, `submit`, `local`, `summarize`. |
| `results/` | Committed summaries: `summary.tsv` (one row per job and engine; a later rerun of a task supersedes the earlier one; `engine_verdict` is the solver's own verdict before sby's witness replay) and `depth_curves.json` (per-step start times) from the runs quoted in the docs. |

## Running

```sh
formal_depth/run.sh generate                      # snapshot, formal RTL, processor_fd.v, mutants
formal_depth/run.sh submit /path/to/work          # the whole portfolio on Slurm
formal_depth/run.sh submit /path/to/work --only '^host_' --engine 'yices|pdr'
FD_PARALLEL=8 formal_depth/run.sh local /path/to/work --only '^neg_'   # without Slurm
formal_depth/run.sh summarize /path/to/work --tsv summary.tsv --curves curves.json
```

`generate` needs the OCaml toolchain (`OCAML_ENV` points at a shell file that
puts `dune` on `PATH`) and OSS CAD Suite 2026-07-29 (yosys 0.67, sby, yices,
bitwuzla, boolector, z3, abc, rIC3, suprove, btormc, pono). Work trees and
logs go under the work directory. Each run keeps its sby log and traces but
deletes its copied sources and model files to save inodes.

## Harnesses (in plain words)

| Harness | Claim |
|---|---|
| `host_protocol.sv` | A pin-level model of the nibble port runs next to the chip. A command, a program word or a TX word takes effect only on the eighth accepted nibble of one window, and it carries exactly the assembled word. A window change or reset discards a partial word with no side effect. A host RX pop happens only on the eighth read nibble, and the popped word is the word that was read. A read returns the value its source had on the edge before the word was first presented, whatever the stalls, and read data holds still until it is accepted. The RX head stays reserved while a window-3 read is in progress. |
| `program_load.sv` | Instruction SRAM is written only while the engine is halted and loading. A running engine's image is committed, and every address below its length was written after the last BEGIN. The word the engine sees is always the SRAM word at its PC. An issue slot with PC past the image faults with code 2 and has no side effect. For a symbolic engine and address, the word executed is the last word written there. |
| `mover.sv` | Two symbolic tagged words from a symbolic source are followed from the RX push to the destination engine's PULL. Neither is lost (only FLUSH or reset drops one), corrupted or duplicated, the destination is the route's, and the order is kept. Every grant pops and pushes in the same cycle, only while quota remains. The quota changes only by ROUTE, FLUSH or an accepted move. |
| `rr_bound.sv` | From any register state, the mover is work-conserving, and a continuously eligible source is granted within 4 cycles, that is, after at most 3 grants to other sources. The bound is tight. |
| `fault_release.sv` | A fault code is sticky until CLEAR (while halted) or reset, and START is never accepted while an engine is faulted. Only a running engine raises a fault. A faulted, halted or reset engine drives none of its pins, and a halted engine's logical enables are zero. HALT stops the engine without a fault. `uo[7]` and `uo[6]` are exactly the fault and IRQ functions. |
| `pin_safety.sv` | `uio_oe` and `uio_out` equal an exact per-pin model. No two engines ever drive the same pin. An open-drain pin never drives high, and it pulls low exactly when its logical value is 0 and it is enabled. Ownership changes only through OWN while the engine is halted. A running engine's logical pins stay inside its ownership, so the ownership mask in the pin mux is defence in depth. |
| `engine_safety_strengthen.vh` | Three invariants that make `formal/engine_safety.sv` k-inductive: a faulted engine is not running, an idle engine has no transfer in flight, and the blocked-cycle counter stays below the wait limit. |

## Soundness notes

- **Observation ports and assumptions.** In every run from reset (all
  `prove` tasks, BMC from reset, covers from reset and most mutant runs), the
  observation ports appear only in assertions. The only assumptions are the
  free inputs and reset on the first cycle. An attribution error in
  `generate_fd.ml` could therefore make those proofs fail, but not pass.
  The `-DFD_FROM_ANY` runs are different. These are the arbitrary-state BMC,
  the `*_any_cover` witnesses and the three mover mutants. They assume
  `fd_invariants.vh` on the first cycle, and those invariants read observation
  ports. This is justified because the same invariants are asserted and proved
  from reset in the `prove` tasks, provided the attribution is right. The
  checks in the generator, and the fact that the proofs pass, support that.
- **Variable part-selects** (`x[4*i+:4]`) become yosys `$shiftx` cells, whose
  out-of-range fill is `x`. The AIGER flows reject `x`, so every job maps
  `$shiftx` to logic and ties the fill to 0 (`setundef -zero`). All indices
  used are in range, so the fill never reaches an assertion.
- **Clocked immediate assertions** are sampled at the clock edge (yosys 0.67
  lowers `$check` through `async2sync`). In a k-induction trace, step 0's
  sampled checks are therefore unconstrained, and the effective induction
  depth is one less than the sby `depth`.
- **Mutants** run with `-DFD_SPEC_ONLY`, which drops every `link_*`
  strengthening assertion. A negative control counts only if a `spec_*`
  claim fails. The summary records which assertion fired.
- **rIC3 in BMC mode** reports "unknown" (exit 30) when it reaches the bound
  without a counterexample. sby maps that to PASS. A toy counter confirmed
  the mapping and that the bound is honoured: depth 30 gives PASS, and depth
  50 gives FAIL for a property that breaks at step 40.
