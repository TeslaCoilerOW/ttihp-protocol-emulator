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

As of 2026-09-25, this design has **not been hardened** and there is **no
silicon**. It does not yet have a passing Tiny Tapeout gds, precheck or
gate-level result. A local LibreLane run of the same configuration (for
iteration only, not a TT result) placed the design at 58.4% utilization and
finished routing with 0 DRC and 0 antenna violations. Post-route STA meets
50 MHz at the typical and fast corners; at the slow corner setup fails by
5.09 ns, almost entirely on paths from the `rst_n` input. Its Magic DRC and
LVS steps had not finished when this was written. See
[docs/hardening.md](docs/hardening.md).

- **Configuration.** The first hardening target is
  `configs/instruction-sram-32.json`: 4 engines, a 32-bit datapath, 64
  instructions per engine in eight `RM_IHPSG13_1P_64x16_c2` SRAM macros, 8-word
  queues and fused issue. It targets 8×4 tiles (1724.16 × 710.64 µm) at 50 MHz.
- **Tile size.** 8×4 still has to be confirmed by the competition organizers.
  A 6×4 variant is kept in parallel as insurance; [docs/area-study.md](docs/area-study.md)
  covers it.
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
iteration only. [docs/hardening.md](docs/hardening.md) covers the recipe, the
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
