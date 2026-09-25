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

## Quick start (board on the desk, one USB cable)

```sh
openFPGALoader -b cmoda7_35t pe_cmod_a7_pll50_bridgeonly.bit   # Cmod A7
openFPGALoader -b arty_s7_50 pe_urbana_pll50.bit                # Urbana (6.205's command)
pip install pyserial
python3 fpga/host/pe_host.py --port /dev/ttyUSB1 info       # bitstream id, clock
python3 fpga/host/pe_host.py --port /dev/ttyUSB1 selftest   # nothing connected
python3 fpga/host/pe_host.py --port /dev/ttyUSB1 loopback   # two jumper wires
```

Each test ends with `SELFTEST PASS` / `LOOPBACK PASS`. The loopback test needs
jumpers from protocol pin 0 to pin 1 and from pin 3 to pin 4 (docs/fpga.md).
The bitstreams, with SHA-256 sums, are in
`$PE_WORK/fpga/bitstreams/`, or you can build
them yourself (below). docs/fpga.md lists which builds meet 50 MHz in
nextpnr's timing model.

## Layout

| Path | Contents |
|---|---|
| `rtl/pe_top_cmod_a7.v`, `rtl/pe_top_urbana.v` | board tops: pads, clocking, strap/switch inputs, LEDs |
| `rtl/pe_fpga_shell.v` | board-independent shell: TT top, reset, host select, UART bridge, status |
| `rtl/pe_uart_host_bridge.v` | UART (1 Mbaud) to nibble host port bridge, FPGA only |
| `rtl/pe_fpga_clkgen.v`, `rtl/pe_fpga_clkfwd.v` | MMCM / BUFG clocking, clock forwarding (ODDR) |
| `rtl/pe_fpga_sram_64x16.v` | FPGA stand-in for the IHP `RM_IHPSG13_1P_64x16_c2` SRAM (LUT RAM + output register) |
| `rtl/RM_IHPSG13_1P_64x16_c2_fpga.v` | drop-in module with the macro's name and ports |
| `constraints/` | board XDCs (pins) and per-build clock constraints |
| `scripts/build.sh`, `scripts/summarize.py` | openXC7 flow: Yosys, nextpnr-xilinx, fasm2frames, xc7frames2bit; build summary |
| `scripts/readback.sh`, `scripts/bit_readback.py` | bit-level readback: the `.bit` must hold exactly the FASM's frames |
| `formal/` | SRAM stand-in vs IHP model: SymbiYosys equivalence (RTL and synthesized netlist), mutants |
| `sim/` | cocotb simulations of the full FPGA top: `test/` lockstep suite, UART bridge + host tool, Pico driver |
| `host/pe_host.py` | PC host over the UART bridge; `info`, `selftest`, `loopback`, `load`, `status` |
| `host/pico_host.py` | MicroPython (Pico) host that clocks the design itself (CLOCK=host builds) |
| `host/test_pico_host.py` | the Pico driver against the Python reference model |

## Builds

```sh
export OPENXC7=/path/to/openxc7          # unpacked tools-openxc7 package + chipdb (docs/fpga.md)
fpga/scripts/build.sh cmod_a7 pll50 build/cmod_a7_pll50 heap:1 heap:2 heap:3 heap:4
fpga/scripts/build.sh urbana  pll50 build/urbana_pll50  heap:1 heap:2 heap:3 heap:4
```

`CLOCK` selects the core clock:

- `pll50`: MMCM, 50 MHz, UART bridge.
- `pll40`: the same at 40 MHz.
- `host`: the clock comes from the host pin, like the Tiny Tapeout demo board.
- `osc12`: Cmod A7 only; the 12 MHz oscillator, with no MMCM.

`BRIDGE_ONLY=1` (Cmod A7 bridge builds) leaves the DIP pin-host pins unused.
Several `PLACER:SEED` runs go in parallel, and the fastest becomes the
bitstream.

## Checks

```sh
fpga/formal/run.sh /tmp/fpga-formal                 # SRAM equivalence (SymbiYosys)
make -C fpga/sim                                    # test_smoke + test_flagship on the FPGA top
make -C fpga/sim FPGA_TB=bridge                     # UART bridge + pe_host.py bring-up tests
make -C fpga/sim COCOTB_TEST_MODULES=test_fpga_pico # Pico driver, host-clocked
python3 fpga/host/test_pico_host.py                 # Pico driver vs reference model
```
