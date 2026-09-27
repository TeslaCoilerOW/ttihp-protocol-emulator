![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/formal/badge.svg) ![](../../workflows/regen/badge.svg)

# Protocol Emulator: four concurrent programmable I/O engines

- [Read the documentation for project](docs/info.md): the datasheet, with the host protocol, ISA reference and a worked example.

This is a Tiny Tapeout project for the IHP SG13CMOS5L 130 nm process. It is an
entry in the
[Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/).

The chip is a small programmable protocol processor. Four independent engines
run their own programs concurrently on eight shared bidirectional pins. Each
engine has:

- a private 64-instruction program memory (two IHP 64×16 SRAM macros per engine);
- 8-word TX and RX queues;
- shift registers, scratch registers, a repeat counter and a wait timer.

UART, SPI, I2C, JTAG and custom timed waveforms are all firmware; none is
hardwired. The engines are coordinated by:

- event mailboxes;
- per-engine input triggers;
- a common timestamp;
- an autonomous mover that forwards queue words between engines without the
  host, for example UART receive into SPI transmit.

A synchronous nibble-wide host port on `ui_in`/`uo_out` loads and controls
everything.

The hardware is written in [Hardcaml](https://github.com/janestreet/hardcaml)
(OCaml). `src/protocol_emulator_core.v` is generated from `hardcaml/` and must
not be edited by hand. `src/project.v` is a thin Tiny Tapeout wrapper
(`tt_um_teslacoilerow_protocol_emulator`) around it.

## Status

In short: the 8x4 build is constrained at 15 ns (66.7 MHz), and its
official gds, precheck and gl_test runs pass. The operating clock is
50 MHz, and operation above 50 MHz is not claimed. The 6x4 fallback build is
signed off at 20 ns. There is no silicon.

As of 2026-09-27, `src/config.json` constrains the Tiny Tapeout flow at
`CLOCK_PERIOD` 15 ns (66.7 MHz): optimizer promotion p018, committed in
`d76f1cc` ([docs/optimization.md](docs/optimization.md), "Adoption of p018").
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
numbers exactly ([docs/timing-closure.md](docs/timing-closure.md)
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
| Netlist vs RTL equivalence (not a TT check) | Official netlist proven equivalent to the RTL with `formal_eq/` (local job 24093884; [docs/equivalence.md](docs/equivalence.md)) | Official netlist proven equivalent (local job 24093885) | not checked |
| test, formal, regen, docs | Official: PASS on `d76f1cc`: 102/102 cocotb tests on RTL; all 16 SymbiYosys jobs meet their expectation | PASS on `131e793`: 102/102 cocotb tests on RTL; all 16 SymbiYosys jobs meet their expectation (proofs pass, both negative controls fail) | PASS on `c118027` with the 66-test suite |

The flow fails on a setup violation at the typical corner or a hold
violation at any corner; setup at the fast and slow corners is reported,
not gated. The slow corner was closed by LibreLane configuration alone
(floorplan, placement, timing-repair, clock-tree, routing and synthesis
settings), not by an RTL change; [docs/timing-closure.md](docs/timing-closure.md)
sections 9 and 10 have the comparison and [docs/results.md](docs/results.md)
sections 2b and 2d the evidence (R84 to R86 and R88 for the 15 ns configuration).

**At 50 MHz.** Re-timed at 20 ns, the layout signed off at 15 ns has 1.07,
0.62 and 3.84 ns more setup slack (typical, fast, slow) than the 20 ns
layout of `131e793`, and 1.09, 0.53 and 3.79 ns more than a local 20 ns
flow run with p018's knob values at `CLOCK_PERIOD` 20 (optimizer control promotion p027;
[docs/results.md](docs/results.md) R88). The input and output delays of the timing constraints
are 20% of the period, so a path from an input pin to an output pin gains
only 3 ns from 15 to 20 ns. Such a path, `ui_in[7]` → `uo_out[5]` (host
window select to read-valid), is the worst path at the typical and fast
corners. These margins come from a local re-analysis, not from the official
flow ([docs/limitations.md](docs/limitations.md) section 2).

The `viewer` job of every `gds` run fails because GitHub Pages is not
enabled for this repository. That job publishes a preview and does not
check the design, but it is why the gds badge above shows a failure.

- **Results.** [docs/results.md](docs/results.md) lists every headline
  number with its evidence (CI runs, Slurm job ids, result files) and the
  command that reproduces it. `make reproduce` runs the local checks with
  one command; `make reproduce-full` adds the longer ones.
- **Defects found.** [docs/bug-ledger.md](docs/bug-ledger.md) lists each
  defect the verification found, the method that found it and its fix.
- **Limits.** [docs/limitations.md](docs/limitations.md) lists what the
  verification does not establish.
- **Hardening notes.** [docs/hardening.md](docs/hardening.md) covers the
  recipe and the earlier local runs.

- **Configuration.** The first hardening target is
  `configs/instruction-sram-32.json`: 4 engines, a 32-bit datapath, 64
  instructions per engine in eight `RM_IHPSG13_1P_64x16_c2` SRAM macros, 8-word
  queues and fused issue. It targets 8×4 tiles (1724.16 × 710.64 µm); its
  operating clock is 50 MHz and the flow constrains it at 15 ns (above).
- **Tile size.** 8×4 still has to be confirmed by the competition organizers.
  A 6×4 variant is kept in parallel as insurance; [docs/area-study.md](docs/area-study.md)
  covers it, and [docs/6x4.md](docs/6x4.md) describes the 6×4 build
  (`diet4`, workflow `gds_6x4`) and the status of its CI runs. The 6×4
  build stays at `CLOCK_PERIOD` 20, pinned by its overlay, so it is signed
  off at 50 MHz only; the 15 ns change of `d76f1cc` does not reach it. The
  6×4 build of `4bd30c8` passes gds, precheck and gl_test and meets setup at
  all three corners at 50 MHz (run
  [36274474540](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36274474540):
  typical +7.71 ns, fast +10.52 ns, slow +2.74 ns). The runs of `1e5b1d8`
  (36285537630) and `d76f1cc` (36298635404) passed every job with a
  byte-identical `metrics.csv` ([docs/results.md](docs/results.md) R87,
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

## Repository layout

| Path | Contents |
|---|---|
| `info.yaml` | Tiny Tapeout project metadata and pinout |
| `docs/info.md` | Datasheet (rendered by the Tiny Tapeout docs action) |
| `docs/isa.md`, `docs/architecture.md`, `docs/firmware.md` | ISA and host-protocol contract, architecture notes, firmware and assembler contract |
| `docs/hardening.md`, `docs/area-study.md` | Hardening recipe and area study (working notes; `docs/area-study/` holds the study's patch and scripts) |
| `src/project.v` | Tiny Tapeout top module, a thin wrapper |
| `src/protocol_emulator_core.v` | Generated from `hardcaml/`; do not edit |
| `src/config.json`, `src/sram_pdn_cfg.tcl` | LibreLane configuration, including the SRAM macros and their power hookup |
| `hardcaml/` | Hardcaml hardware description, assembler, firmware library and generators |
| `configs/` | Architecture and SRAM-refinement configurations |
| `firmware/` | Assembled firmware images and sources, plus `flagship-scenario.json` |
| `test/` | cocotb tests, lockstep reference model (`test/model/`) and protocol peers |
| `formal/` | SymbiYosys harnesses and `run.sh` |
| `formal_eq/` | RTL-vs-netlist sequential equivalence of a hardened netlist ([docs/equivalence.md](docs/equivalence.md)) |
| `macros/` | Vendored IHP SRAM views (GDS, LEF, Liberty, CDL) |
| `models/` | IHP SRAM behavioral simulation models and an interface-only blackbox |
| `scripts/` | `generate.sh` (Hardcaml to Verilog), `lint.sh`, pinned opam packages |

## Build (Hardcaml to Verilog)

Requirements:

- OCaml 5.2.1;
- the opam packages pinned in `scripts/opam-deps.txt`: dune 3.20.2,
  hardcaml v0.17.0, yojson 2.2.2 and digestif 1.3.1.

```sh
make generate            # scripts/generate.sh configs/instruction-sram-32.json
make check-generated     # regenerate and fail if the committed core differs (what the regen action checks)
make lint                # iverilog, yosys hierarchy and verilator -Wall
```

`scripts/generate.sh CONFIG` generates the core from another configuration.
It uses the SRAM generator for a refinement configuration and the
register-store generator for an architecture configuration. `DUNE_BUILD_DIR`
moves the dune build directory. The firmware assembler is `hardcaml/bin/assemble.ml`
(`dune exec bin/assemble.exe -- --help` from `hardcaml/`; see
[docs/firmware.md](docs/firmware.md)). The ISA and the host protocol are
specified in [docs/isa.md](docs/isa.md) and summarized in the datasheet,
[docs/info.md](docs/info.md).

## Test (cocotb)

```sh
cd test
pip install -r requirements.txt   # cocotb 2.0.1, pytest 8.4.2
make clean && make                # RTL, with the IHP SRAM behavioral models
```

Every test runs the design in lockstep with an independent Python model of the
ISA. Protocol results are also checked at the pins by UART, SPI and I2C peers.
[test/README.md](test/README.md) covers the test variables, the gate-level
runs (`GATES=yes`) and the constrained-random test.

## Formal

```sh
formal/run.sh --list     # job names
formal/run.sh            # generate RTL from hardcaml/, then run every job
```

The jobs are unbounded proofs, bounded checks, cover witnesses and negative
controls. They cover reset safety, queue conservation, engine and processor
invariants, and the timing-isolation miter between two processor instances.
[formal/README.md](formal/README.md) lists each claim and method. The `formal`
action runs every job.

## Hardening

The Tiny Tapeout `gds` action (`TinyTapeout/tt-gds-action@ihp-cmos5l`) is the
flow of record. Local LibreLane runs use the same configuration and are for
iteration; until an official run finishes, the Status section quotes the
local sign-off of the committed configuration and labels it local. [docs/hardening.md](docs/hardening.md) covers the recipe, the
SRAM macro integration and what remains open.

## License

- **This project.** It is licensed under the Apache License 2.0; see
  [LICENSE](LICENSE) and [NOTICE](NOTICE). This covers the Hardcaml sources,
  the generated Verilog, the firmware, the tests and the documentation.
- **IHP SRAM views.** The SRAM views in `macros/RM_IHPSG13_1P_64x16_c2/` and
  the simulation models in `models/` are unmodified IHP-Open-PDK files;
  `models/blackbox/` is an interface-only derivative. They are under
  Apache-2.0 from the IHP PDK Authors; see `macros/LICENSE.IHP-Open-PDK` and
  `models/NOTICE.IHP-Open-PDK`. `macros/README.md` and
  `macros/check_macro_floorplan.py` were written for this project.
- **Template.** The Tiny Tapeout template is Apache-2.0.

## What is Tiny Tapeout?

Tiny Tapeout is an educational project that aims to make it easier and cheaper than ever to get your digital and analog designs manufactured on a real chip.

To learn more and get started, visit https://tinytapeout.com.

## Resources

- [FAQ](https://tinytapeout.com/faq/)
- [Digital design lessons](https://tinytapeout.com/digital_design/)
- [Learn how semiconductors work](https://tinytapeout.com/siliwiz/)
- [Join the community](https://tinytapeout.com/discord)
- [Build your design locally](https://www.tinytapeout.com/guides/local-hardening/)
- [Enabling GitHub Pages](https://tinytapeout.com/faq/#my-github-action-is-failing-on-the-pages-part) for the results page
