# FPGA prototype

FPGA builds of the Tiny Tapeout design `tt_um_teslacoilerow_protocol_emulator`
(`../src/project.v` and the generated `../src/protocol_emulator_core.v`, both
unchanged) for two boards:

| Board | Part | Protocol pins | Host port |
|---|---|---|---|
| Digilent Cmod A7-35T | xc7a35tcpg236-1 | Pmod JA | USB-UART bridge, or a pin host on the DIP header |
| Real Digital Urbana (MIT 6.205) | xc7s50csga324-1 | Pmod A | USB-UART bridge, or a pin host on Pmod B + GPIO |

[`../docs/fpga.md`](../docs/fpga.md) has the pin maps, wiring, build results
(utilisation, timing), programming commands and the hardware test procedure.

## Quick start (board on the desk, one USB cable, two jumper wires)

```sh
pip install -r fpga/host/requirements.txt
PE_BITSTREAMS=/path/to/vivado-2025.2-2026-09-27 fpga/scripts/bringup.sh cmod_a7 /dev/ttyUSB1
PE_BITSTREAMS=/path/to/vivado-2025.2-2026-09-27 fpga/scripts/bringup.sh urbana  /dev/ttyUSB1
```

One command per board: it programs the capture-unit bitstream with
openFPGALoader, then runs `host/bringup.py`: link and clock check, the
selftest (nothing connected), a prompt to fit two jumper wires (protocol pin
0 to pin 1, pin 3 to pin 4), the loopback test, the on-board capture unit's
checks, and the self-measured timing-isolation experiment with its verdict.
The run ends with `BRING-UP PASS`. docs/fpga.md, "First hour with a board",
has the wiring, the expected output and what to do when a step fails. None of
this has run on hardware yet; the whole sequence has run against the
simulated board top.

The individual tools still work on their own:

```sh
openFPGALoader -b cmoda7_35t pe_cmod_a7_pll50.bit           # Cmod A7
openFPGALoader -b arty_s7_50 pe_urbana_pll50.bit            # Urbana (6.205's command)
python3 fpga/host/pe_host.py --port /dev/ttyUSB1 info       # bitstream id, clock
python3 fpga/host/pe_host.py --port /dev/ttyUSB1 selftest   # nothing connected
python3 fpga/host/pe_host.py --port /dev/ttyUSB1 loopback   # two jumper wires
python3 fpga/host/pe_scope.py --port /dev/ttyUSB1 status    # capture unit
```

The bitstreams, with SHA-256 sums, are in `$PE_WORK/fpga/bitstreams/`, or
you can build them yourself (below). The 2026-09-27 release exists twice,
from the same RTL and constraints, with the same file names:

- `vivado-2025.2-2026-09-27/`: built with AMD Vivado 2025.2 by
  `vivado/build.tcl`; all seven builds meet timing in Vivado's sign-off
  analysis (docs/fpga.md, "Vivado sign-off flow"). Program these first.
- `v3-2026-09-27-scope/`: built with the open-source openXC7 flow; timing
  from nextpnr-xilinx's model, post-synthesis netlists simulated.

## Layout

| Path | Contents |
|---|---|
| `rtl/pe_top_cmod_a7.v`, `rtl/pe_top_urbana.v` | board tops: pads, clocking, strap/switch inputs, LEDs |
| `rtl/pe_fpga_shell.v` | board-independent shell: TT top, reset, host select, UART bridge, status |
| `rtl/pe_uart_host_bridge.v` | UART (1 Mbaud) to nibble host port bridge, FPGA only |
| `rtl/pe_fpga_scope.v` | on-board capture unit: timestamps every change of uio / uo_out / ui_in in core clock cycles, block-RAM record buffer, read back through the bridge |
| `rtl/pe_fpga_clkgen.v`, `rtl/pe_fpga_clkfwd.v` | MMCM / BUFG clocking, clock forwarding (ODDR) |
| `rtl/pe_fpga_sram_64x16.v` | FPGA stand-in for the IHP `RM_IHPSG13_1P_64x16_c2` SRAM (LUT RAM + output register) |
| `rtl/RM_IHPSG13_1P_64x16_c2_fpga.v` | drop-in module with the macro's name and ports |
| `constraints/` | board XDCs (pins) and per-build clock constraints |
| `scripts/build.sh`, `scripts/pnr_run.sh`, `scripts/summarize.py` | openXC7 flow: Yosys, nextpnr-xilinx (one `pnr_run.sh` call per seed), fasm2frames, xc7frames2bit; build summary |
| `scripts/release.tsv`, `scripts/release.sh` | the options and placer seed of each published bitstream; rebuild one with `release.sh NAME OUT_DIR` |
| `scripts/abc9/` | Yosys ABC9 LUT-mapping scripts selectable with `ABC9_SCRIPT` |
| `scripts/region.py` | nextpnr pre-place script: confine slice cells to a rectangle (`REGION=`) |
| `scripts/sweep/` | seed and option sweeps on Slurm: `plan.py`, `worker.sbatch`, `run_task.sh`, `collect.py` |
| `scripts/readback.sh`, `scripts/bit_readback.py` | bit-level readback: the `.bit` must hold exactly the FASM's frames |
| `formal/` | SRAM stand-in vs IHP model: SymbiYosys equivalence (RTL and synthesized netlist), mutants |
| `sim/` | cocotb simulations of the full FPGA top: `test/` lockstep suite, UART bridge + host tool, Pico driver, capture unit (`test_fpga_scope.py`), bring-up (`test_fpga_bringup.py`) |
| `host/pe_host.py` | PC host over the UART bridge; `info`, `selftest`, `loopback`, `load`, `status` |
| `host/pe_scope.py` | capture-unit client: arm, read back (CRC-checked), waveforms in cycles, VCD for `demo/pe_capture.py` |
| `host/bringup.py`, `scripts/bringup.sh` | first-hour bring-up: program, self-test, capture checks, self-measured isolation experiment |
| `host/requirements.txt` | PC-side Python dependency (pyserial) |
| `host/test_pe_scope.py` | capture-unit client unit tests (record format, CRC retry, VCD hand-off to `pe_capture.py`) |
| `vivado/build.tcl`, `vivado/timing_check.py` | AMD Vivado non-project batch build and timing PASS/FAIL (vendor sign-off; run with Vivado 2025.2 on all seven builds) |
| `vivado/test_vivado_flow.py` | checks of the Vivado scripts without Vivado (Tcl stubs, report parser) |
| `host/pico_host.py` | MicroPython (Pico) host that clocks the design itself (CLOCK=host builds) |
| `host/test_pico_host.py` | the Pico driver against the Python reference model |

## Builds

```sh
export OPENXC7=/path/to/openxc7          # unpacked tools-openxc7 package + chipdb (docs/fpga.md)
fpga/scripts/release.sh --list           # the published builds: seed and options of each
fpga/scripts/release.sh pe_cmod_a7_pll50 build/pe_cmod_a7_pll50
fpga/scripts/build.sh urbana pll50 build/urbana_pll50 heap:1 heap:2 heap:3 heap:4   # default options
```

`CLOCK` selects the core clock:

- `pll50`: MMCM, 50 MHz, UART bridge.
- `pll40`: the same at 40 MHz.
- `host`: the clock comes from the host pin, like the Tiny Tapeout demo board.
- `osc12`: Cmod A7 only; the 12 MHz oscillator, with no MMCM.

`BRIDGE_ONLY=1` (Cmod A7 bridge builds) leaves the DIP pin-host pins unused.
Several `PLACER:SEED` runs go in parallel, and the fastest becomes the
bitstream. Implementation options (synthesis and placer settings) are
environment variables listed at the top of `scripts/build.sh`;
`scripts/release.tsv` records those of each published bitstream, and
`scripts/sweep/` runs seed and option sweeps on Slurm (docs/fpga.md,
"Seed and option sweeps").

## Checks

```sh
fpga/formal/run.sh /tmp/fpga-formal                 # SRAM equivalence (SymbiYosys)
SYNTH_OPTS=-nowidelut ABC9_SCRIPT=flow3mfs ABC9_W=1000 \
  fpga/formal/run.sh /tmp/fpga-formal-f3m netlist_prove  # the SRAM mapped with a build's synthesis options
make -C fpga/sim                                    # test_smoke + test_flagship on the FPGA top
make -C fpga/sim FPGA_TB=bridge                     # UART bridge + pe_host.py bring-up tests
make -C fpga/sim FPGA_TB=bridge COCOTB_TEST_MODULES=test_fpga_scope    # capture unit vs an edge monitor
make -C fpga/sim FPGA_TB=bridge COCOTB_TEST_MODULES=test_fpga_bringup  # host/bringup.py end to end
python3 fpga/host/test_pe_scope.py                  # capture-unit client
python3 fpga/vivado/test_vivado_flow.py             # Vivado scripts (stubs; no Vivado needed)
vivado -mode batch -nojournal -source fpga/vivado/build.tcl -tclargs cmod_a7 pll50 build/vivado_cmod_a7_pll50
make -C fpga/sim COCOTB_TEST_MODULES=test_fpga_pico # Pico driver, host-clocked
python3 fpga/host/test_pico_host.py                 # Pico driver vs reference model
make -C fpga/sim FPGA_CLOCK=pll50 FPGA_NETLIST=$PWD/build/pe_cmod_a7_pll50/synth_netlist.v  # after synthesis
```

`fpga/sim` runs `test/test_flagship.py` without the gate-level skips, so the
five `uart-rx-idle` tests added in `e64cd6b` run there too (6 of 6 on the FPGA
top in job 24167292, about 100 s). On a slow post-synthesis netlist
(`FPGA_NETLIST=...`), `PE_UART_IDLE_CYCLES` (default 100000) shortens their
idle stretches.
