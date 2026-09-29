# Sign-off history

This page keeps the detailed sign-off record that `README.md` carried until
2026-09-28: the status text, the table of official runs with the superseded
configurations, the 50 MHz re-analysis, the 6x4 notes, the provenance notes
and the milestone plan. The README now keeps a short status summary and links
here.

The two sections below are moved verbatim from `README.md` at commit
`24f31f0`: its "Status" section, and the paragraph of its "Hardening"
section that refers to it. Only the relative links were changed, to resolve
from this page, and each heading names its source. In the moved text, "the
badges above" and "the Status section" refer to `README.md`, "Milestones" is
the plan as it was listed there, and dates are as written on 2026-09-27.
Every number has its evidence in [results.md](results.md).

## Status (moved from README.md, as of 2026-09-27)

In short: the 8x4 build is constrained at 15 ns (66.7 MHz), and its
official gds, precheck and gl_test runs pass. The operating clock is
50 MHz, and operation above 50 MHz is not claimed. The 6x4 fallback build is
signed off at 20 ns. There is no silicon.

As of 2026-09-27, `src/config.json` constrains the Tiny Tapeout flow at
`CLOCK_PERIOD` 15 ns (66.7 MHz): optimizer promotion p018, committed in
`d76f1cc` ([docs/optimization.md](optimization.md), "Adoption of p018").
The flow therefore signs timing off at 66.7 MHz; in the official build,
setup and hold are met at that period at all three corners (table below). The operating clock is 50 MHz (`info.yaml` `clock_hz` 50000000),
which all firmware, the host library, the firmware timing analyzer and the
datasheet assume. In a local re-analysis (below), the layout signed off at
15 ns has more setup margin at 50 MHz than the 20 ns layouts. The RTL is
byte-identical to tag `v0.1-hardened` (`c118027`). There is **no silicon**,
and nothing has been run on an FPGA board yet.

The official `gds` run of `d76f1cc`,
[36298635436](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36298635436),
passed gds (4 h 24 min), gl_test and precheck (finished 2026-09-27
13:33 UTC). Its `metrics.csv` is byte-identical to that of the local
sign-off run of p018 (job 24042265), as for the two earlier adoptions. The
50 MHz row of that column is a re-analysis of the signed-off layout at 20 ns with
the flow's own STA script and SDC, whose two controls reproduce the flow's
numbers exactly ([docs/timing-closure.md](timing-closure.md)
section 10). The two other columns are the official results of earlier
configurations, kept as history: `131e793` (20 ns, promotion p010, adopted
in `25e331e`) and tag `v0.1-hardened`. All three are IHP SG13CMOS5L, 8x4
tiles, LibreLane 3.1.0.dev3.

| Check | `d76f1cc`, 15 ns, run [36298635436](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36298635436) (current) | `131e793`, 20 ns, run [36257636798](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36257636798) (superseded by `d76f1cc`) | `c118027`, tag `v0.1-hardened`, 20 ns, run [36144357821](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36144357821) (superseded) |
|---|---|---|---|
| gds | PASS: utilization 65.3% (standard cells 61.4%); route DRC 0; LVS 0; antenna 0 | PASS: utilization 61.9% (standard cells 57.6%); route DRC 0; LVS 0; antenna 0 | PASS: utilization 58.5% (standard cells 53.9%); route DRC 0; LVS 0; antenna 0 |
| Setup at the flow's period | 15 ns: met at all three corners: typical +5.95 ns, fast +6.92 ns, slow +2.24 ns; 0 violating endpoints | 20 ns: met at all three corners: typical +7.88 ns, fast +9.31 ns, slow +2.95 ns; 0 violating endpoints | 20 ns: typical +0.89 ns, fast +6.15 ns; slow −8.52 ns with 2,482 violating endpoints, almost all starting at `rst_n` |
| Setup at 50 MHz (operating clock) | Re-analysis at 20 ns of the p018 layout, whose netlist is byte-identical to the official one (job 24077956): typical +8.95 ns, fast +9.92 ns, slow +6.79 ns; 0 violating endpoints | as above (the flow's period was 20 ns) | as above |
| Hold | met at every corner (worst +0.165 ns, fast corner); the same at 20 ns | met at every corner (worst +0.11 ns, fast corner) | met at every corner (worst +0.11 ns, fast corner) |
| precheck | PASS, including the KLayout SG13CMOS5L DRC and the pin check | PASS, including the KLayout SG13CMOS5L DRC and the pin check | PASS |
| gl_test | PASS: 102 tests, 46 pass, 56 skipped by design at gate level, 0 fail | PASS: 102 tests, 46 pass, 56 skipped by design at gate level, 0 fail | PASS: 66 tests, 36 pass, 30 skipped, 0 fail |
| Netlist vs RTL equivalence (not a TT check) | Official netlist proven equivalent to the RTL with `formal_eq/` (local job 24093884; [docs/equivalence.md](equivalence.md)) | Official netlist proven equivalent (local job 24093885) | not checked |
| test, formal, regen, docs | Official: PASS on `d76f1cc`: 102/102 cocotb tests on RTL; all 16 SymbiYosys jobs meet their expectation | PASS on `131e793`: 102/102 cocotb tests on RTL; all 16 SymbiYosys jobs meet their expectation (proofs pass, both negative controls fail) | PASS on `c118027` with the 66-test suite |

The flow fails on a setup violation at the typical corner or a hold
violation at any corner; setup at the fast and slow corners is reported,
not gated. The slow corner was closed by LibreLane configuration alone
(floorplan, placement, timing-repair, clock-tree, routing and synthesis
settings), not by an RTL change; [docs/timing-closure.md](timing-closure.md)
sections 9 and 10 have the comparison and [docs/results.md](results.md)
sections 2b and 2d the evidence (R84 to R86 and R88 for the 15 ns configuration).

**At 50 MHz.** Re-timed at 20 ns, the layout signed off at 15 ns has 1.07,
0.62 and 3.84 ns more setup slack (typical, fast, slow) than the 20 ns
layout of `131e793`, and 1.09, 0.53 and 3.79 ns more than a local 20 ns
flow run with p018's knob values at `CLOCK_PERIOD` 20 (optimizer control promotion p027;
[docs/results.md](results.md) R88). The input and output delays of the timing constraints
are 20% of the period, so a path from an input pin to an output pin gains
only 3 ns from 15 to 20 ns. Such a path, `ui_in[7]` → `uo_out[5]` (host
window select to read-valid), is the worst path at the typical and fast
corners. These margins come from a local re-analysis, not from the official
flow ([docs/limitations.md](limitations.md) section 2).

The `viewer` job of every `gds` run fails because GitHub Pages is not
enabled for this repository. That job publishes a preview and does not
check the design, but it is why the gds badge above shows a failure.

- **Results.** [docs/results.md](results.md) lists every headline
  number with its evidence (CI runs, Slurm job ids, result files) and the
  command that reproduces it. `make reproduce` runs the local checks with
  one command; `make reproduce-full` adds the longer ones.
- **Defects found.** [docs/bug-ledger.md](bug-ledger.md) lists each
  defect the verification found, the method that found it and its fix.
- **Limits.** [docs/limitations.md](limitations.md) lists what the
  verification does not establish.
- **Hardening notes.** [docs/hardening.md](hardening.md) covers the
  recipe and the earlier local runs.

- **Configuration.** The first hardening target is
  `configs/instruction-sram-32.json`: 4 engines, a 32-bit datapath, 64
  instructions per engine in eight `RM_IHPSG13_1P_64x16_c2` SRAM macros, 8-word
  queues and fused issue. It targets 8×4 tiles (1724.16 × 710.64 µm); its
  operating clock is 50 MHz and the flow constrains it at 15 ns (above).
- **Tile size.** 8×4 still has to be confirmed by the competition organizers.
  A 6×4 variant is kept in parallel as insurance; [docs/area-study.md](area-study.md)
  covers it, and [docs/6x4.md](6x4.md) describes the 6×4 build
  (`diet4`, workflow `gds_6x4`) and the status of its CI runs. The 6×4
  build stays at `CLOCK_PERIOD` 20, pinned by its overlay, so it is signed
  off at 50 MHz only; the 15 ns change of `d76f1cc` does not reach it. The
  6×4 build of `4bd30c8` passes gds, precheck and gl_test and meets setup at
  all three corners at 50 MHz (run
  [36274474540](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36274474540):
  typical +7.71 ns, fast +10.52 ns, slow +2.74 ns). The runs of `1e5b1d8`
  (36285537630) and `d76f1cc` (36298635404) passed every job with a
  byte-identical `metrics.csv` ([docs/results.md](results.md) R87,
  R89).
- **Earlier results.** The ISA, the reference model and the firmware come from
  earlier development in the author's asic-lab monorepo. There, the generated
  RTL was checked against an independent Python ISA model and with bounded
  formal properties.
- **This repository.** It is moving those checks onto the official Tiny
  Tapeout flow: cocotb tests, SymbiYosys proofs in CI, and the TT gds, precheck
  and gl_test actions.

The badges above show the current CI state. A milestone counts as met only
when its GitHub Actions run passes.

Milestones:

| Target date | Milestone |
|---|---|
| 2026-09-30 | Stabilize: source in git, organizer questions sent (8×4, SRAM macros, submission format) |
| 2026-10-23 | Tiny Tapeout baseline: Hardcaml generation checked in CI, per-block area breakdown, first green gds at 8×4 (6×4 fit in parallel), cocotb smoke/loader/UART/SPI/I2C tests on RTL and gate level; tag `v0.1-hardened` |
| 2026-11-06 | Go/no-go for a stretch capability (line coding, CRC, fractional-period timing), sized to the measured area headroom |
| 2026-11-20 | Verification showcase: constrained-random lockstep testing with coverage, formal timing-isolation proof in CI, existing formal properties in CI, independent protocol peers, mutation score, methodology write-up |
| 2026-12-18 | Stretch capability complete; host-free bridging demos (UART→SPI, I2C sensor→UART); FPGA and host-controller demonstration against real peripherals |
| 2027-01-04 | Design freeze |
| 2027-01-11 | Submission (competition deadline 2027-01-18) |

## Hardening (moved from README.md)

The Tiny Tapeout `gds` action (`TinyTapeout/tt-gds-action@ihp-cmos5l`) is the
flow of record. Local LibreLane runs use the same configuration and are for
iteration; until an official run finishes, the Status section quotes the
local sign-off of the committed configuration and labels it local. [docs/hardening.md](hardening.md) covers the recipe, the
SRAM macro integration and what remains open.
