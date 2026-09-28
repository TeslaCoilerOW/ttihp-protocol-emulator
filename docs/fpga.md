# FPGA prototype

This page covers the FPGA builds of the chip, for testing firmware and
peripherals on real pins before silicon exists. Two boards are supported: the
Digilent Cmod A7-35T and the Real Digital Urbana (the MIT 6.205 board).

The FPGA image contains the Tiny Tapeout top `tt_um_teslacoilerow_protocol_emulator`
and the Hardcaml-generated core, both unchanged. Around them are four
FPGA-only additions:

- a board wrapper (pads, clocking, reset, LEDs);
- a replacement for the IHP SRAM macro, proved cycle-equivalent to the IHP model;
- a USB-UART bridge, so a PC can act as the host with no extra hardware;
- an on-board capture unit (bridge builds from 2026-09-27), which timestamps
  every pin change in core clock cycles, so the pin timing can be measured
  without a logic analyser.

**Status (2026-09-27).** Bitstreams exist for both boards. The 2026-09-27
release adds the on-board capture unit to the UART-bridge builds and exists
twice, from the same RTL and constraints: built with the open-source openXC7
flow, and built and timing-signed-off with AMD Vivado 2025.2 ("Vivado
sign-off flow": all seven builds meet timing, worst setup slack 4.98 ns at
20 ns). The 2026-09-26 and 2026-09-25 openXC7 bitstreams are kept. Nothing
has been checked on hardware: no board has been programmed yet, so nothing on
this page is a hardware observation. The "Verification" section lists the
simulations and formal checks that were run, and where.

Start with one command per board ("First hour with a board"), which programs
a capture-unit bitstream and runs the bring-up tests and the self-measured
timing-isolation experiment. Use the Vivado set first
(`vivado-2025.2-2026-09-27/`), the openXC7 set (`v3-2026-09-27-scope/`) as
the fully open-source alternative:

- **Cmod A7**: `pe_cmod_a7_pll50.bit` (PC over USB, 50 MHz);
  `pe_cmod_a7_osc12.bit` if the MMCM does not come up.
- **Urbana**: `pe_urbana_pll50.bit`.
- **Pico or another host-clocked pin host**: `pe_cmod_a7_host.bit`,
  `pe_urbana_host.bit` (no capture unit).

In Vivado, the 50 MHz builds imply 66.6–70.5 MHz; in nextpnr-xilinx's
timing model the openXC7 builds of the 2026-09-27 release reach 69–76 MHz,
and all fourteen openXC7 bitstreams of the 2026-09-26 and 2026-09-27 releases
meet their targets. See "Build results" and "Vivado sign-off flow".

## Contents of the FPGA image

```mermaid
flowchart LR
    PC["PC: fpga/host/pe_host.py, pe_scope.py"] -- "USB-UART 1 Mbaud" --> BR["pe_uart_host_bridge"]
    PICO["Pin host (Pico, ...)"] -- "ui/uo/rst_n/ena pins" --> MUX
    BR --> MUX{"host select"}
    BR -- "A F H Q P U" --> SC["pe_fpga_scope (block RAM records)"]
    TT -. "uio pads, uio_out/oe, uo_out, ui_in (read only)" .-> SC
    MUX --> TT["tt_um_teslacoilerow_protocol_emulator (unchanged)"]
    TT --> CORE["protocol_emulator_core (generated, unchanged)"]
    CORE --> SRAM["8 x RM_IHPSG13_1P_64x16_c2 = FPGA stand-in (LUT RAM)"]
    TT <-- "uio[7:0], IOBUF T = ~uio_oe" --> PMOD["Protocol Pmod pins"]
    CLK["Oscillator -> MMCM 50 MHz, or host clock pin"] --> TT
```

| Block | File | Notes |
|---|---|---|
| Board top | `fpga/rtl/pe_top_cmod_a7.v`, `fpga/rtl/pe_top_urbana.v` | Pads, clock source, straps/switches, LEDs |
| Shell | `fpga/rtl/pe_fpga_shell.v` | TT top instance; reset synchroniser; host-select mux; UART bridge; status |
| UART bridge | `fpga/rtl/pe_uart_host_bridge.v` | PC ↔ nibble host port, in the core clock domain; forwards the capture commands |
| Capture unit | `fpga/rtl/pe_fpga_scope.v` | pin-change records in block RAM, cycle timestamps; bridge builds only |
| Clocking | `fpga/rtl/pe_fpga_clkgen.v`, `fpga/rtl/pe_fpga_clkfwd.v` | MMCME2_ADV or BUFG; ODDR clock forwarding |
| SRAM stand-in | `fpga/rtl/pe_fpga_sram_64x16.v`, `fpga/rtl/RM_IHPSG13_1P_64x16_c2_fpga.v` | Same module name and ports as the IHP macro |

**Fidelity to the chip.**

- `ui_in`, `uo_out`, `rst_n` and `ena` connect to the TT ports with no added
  registers or synchronisers. The combinational window-change bubble on
  `uo_out` (docs/isa.md) therefore appears on the pins exactly as it would on
  silicon.
- The protocol pins go to IOBUFs with `T = ~uio_oe`, `I = uio_out` and
  `O = uio_in`. The core's own two-flop input synchronisers are the only ones.
- Open-drain (I²C) needs no special pad. The core already drives
  `uio_out = 0` with `uio_oe = logical OE AND NOT value` (docs/isa.md), and
  the IOBUF passes that through.
- Each protocol pin has the FPGA's weak internal pull-up (`PULLTYPE PULLUP`), so
  a released pin reads high. The pull-up is weak, so fit real 2.2–4.7 kΩ
  pull-ups on I²C lines.

### SRAM stand-in

The core instantiates eight `RM_IHPSG13_1P_64x16_c2` macros, two per engine,
to hold its instruction memory. It ties `A_DLY` high and drives:

- `A_MEN = ~clear`;
- `A_WEN` for host program writes;
- `A_REN = ~A_WEN`.

It reads the next instruction every cycle.

The IHP behavioral model (`models/RM_IHPSG13_1P_core_behavioral.v`, used
with `FUNCTIONAL`) behaves as follows on each rising edge with `A_MEN` high:

| `A_WEN` | `A_REN` | Memory | `A_DOUT` |
|---|---|---|---|
| 1 | 1 | written | the written data (write-through) |
| 1 | 0 | written | holds |
| 0 | 1 | unchanged | the stored word, registered |

When `A_MEN` is low, nothing changes.

`fpga/rtl/pe_fpga_sram_64x16.v` implements exactly this. The array is
asynchronous-read LUT RAM; Yosys maps it to 16 `RAM64X1S` per macro. The output
is a separate fabric register with clock enable, followed by a 2:1 mux for
write-through. A 7-series block RAM was not used because its output register
cannot provide "write without read holds `A_DOUT`" together with write-through:
in `WRITE_FIRST` mode the output also updates on writes where `A_REN` is low.

There is one difference, and the core cannot see it. The FPGA powers up with
zeros in the array and output register, while the macro's contents are
unknown (X in the model). COMMIT only accepts a fully written image, and
execution is bounded by the committed length, so no unwritten word is ever
executed.

`fpga/rtl/RM_IHPSG13_1P_64x16_c2_fpga.v` gives the stand-in the macro's
module name and ports, so the FPGA build uses the generated core file as it
is.

The equivalence is proved formally (`fpga/formal/`, see "Verification").

## On-board capture unit

The bridge bitstreams of the 2026-09-27 release (bridge protocol 2) contain a
capture unit, `fpga/rtl/pe_fpga_scope.v`. It records every change of the
chip's pins in core clock cycles, so the timing of an engine can be measured
without an external logic analyser. The self-measured isolation experiment
([demo.md](demo.md), "Experiment C") and the bring-up checks ("First hour
with a board") use it. The host-clocked `host` builds have no UART and no
capture unit.

The unit sits in the shell, next to the UART bridge. It only reads signals;
its outputs go to the bridge's transmit path and nowhere else. The Tiny
Tapeout top and the generated core are unchanged.

**Channels.** 24 channels, sampled at every rising edge of the core clock:

| Channel bits | Signals | Notes |
|---|---|---|
| 0–7 | `uio[0..7]`, the protocol pins | Pad view (default): the pad input, as a probe on the pin sees it. Core view (ARM mode bit 1): `uio_out` where `uio_oe` is set, the pad elsewhere |
| 8–15 | `uo_out[0..7]` | the core's host-port outputs |
| 16–23 | `ui_in[0..7]` | as applied to the core (UART bridge or pin host) |

**Timestamps.** A 48-bit counter counts clock edges since configuration. A
record's stamp is the index of the clock edge that launched the change: the
p-th rising edge since configuration, the value the bridge's `C` command
returns at that edge. Every channel passes the same two registers (sampled
at edge p + 1, compared at edge p + 2, written at edge p + 3), so all channels
have the same latency and the stamp needs no correction. For the pad view
this holds when the pad settles within one clock period of the launching
edge. That path (output buffer, pad, input buffer, first register) is not
timed by the openXC7 flow; the bring-up checks compare the pad view with the
core view of the running timing probe to confirm it on each board.

- **Inputs driven from outside the FPGA** (a UART adapter, a sensor, a
  jumper to another board) are asynchronous to the core clock. The unit
  samples them in its own register (`pad_s1`, `ASYNC_REG`), separately from
  the core's own two-flop synchroniser. A change close to a clock edge can
  land one cycle apart in the two, so the recorded stamp of an external edge
  can differ by ±1 cycle from the cycle in which the core sees it. The
  unit's second stage is not a dedicated synchroniser flop: in the released
  RTL it sits behind the pad/core-view multiplexer, which shortens the time
  a metastable first stage has to settle. The failure is contained: a late
  settling can only move that one edge by a cycle or record it at the value
  it settles to, and the records stay a consistent sequence (every enabled
  channel changes only in a record that says so, which `pe_scope.py`
  checks). The timing-isolation experiment records only pins the FPGA drives
  itself. A dedicated second stage is on the "Next rebuild" list.
- **Open-drain pins** (I²C): the release edge of an open-drain pin is the
  pull-up charging the line, so its timing depends on the pull-up and the
  line capacitance, and it can take longer than a clock period; that breaks
  the one-period assumption above. The core view does not help here: for a
  pin the core releases it also reads the pad. Only the falling edges of an
  open-drain pin mark the launching cycle; judge open-drain timing by them,
  or use push-pull pins for timing measurements.

**Records.** 72 bits, read back as 9 bytes, little endian:

| Bits | Field |
|---|---|
| 71:48 | values of all 24 channels after the change |
| 47:24 | mask: enabled channels that changed in this cycle |
| 23:22 | kind: 0 change, 1 start, 2 marker, 3 end |
| 21:0 | low 22 bits of the stamp |

- Only changes on enabled channels are recorded, one record per cycle with a
  change.
- Record 0 is the start record, written at the trigger cycle. Its mask holds
  the enabled channels that changed in that cycle; an enabled channel's level
  before the record is `values ^ mask`.
- A marker record (mask 0) is written whenever the stamp is a multiple of
  2^21 and nothing else is written. Consecutive records are therefore less
  than 2^22 cycles apart, and the full stamp of every record follows from the
  start stamp and the 22-bit differences.
- The last record is the end record, written at the halt cycle, or by the
  write that fills the buffer or reaches the ARM record limit. It carries
  that cycle's changes too.

**Overflow.** When the end record fills the buffer, the unit enters state
FULL: nothing more is stored, and every later cycle with an enabled change is
counted in `lost` until the next halt or ARM. The records are therefore
always a complete, gap-free account of the enabled channels from the start
stamp to the end stamp. `pe_scope.py` reports the window and the lost count
and never reads anything beyond the end record.

**Activity counters.** One 32-bit saturating counter per channel counts the
changes from the start record's cycle to the end record's cycle, enabled or
not. The self-measured experiment uses them as evidence of load inside each
capture window (host strobes on `ui_in[4]`, UART and SPI edges on the
protocol pins). For an enabled channel the counter must equal the number of
recorded edges; `pe_scope.py` checks this for every capture.

**Commands.** The bridge forwards six commands to the unit, whichever host
owns the TT host port. Payloads and replies are little endian:

| Command | Payload | Reply |
|---|---|---|
| `A` arm | enable mask[3], trigger mask[3], mode, record limit[2] | `A`. Clears the buffer and the counters. Mode bit 0 = 1: start at the first change of a trigger channel (or `F`); 0: start immediately. Mode bit 1 = 1: core view. Limit 2..depth, 0 = the whole buffer. Recording starts a few clock cycles after the command (settling time) |
| `F` force | – | `F`: start now (when waiting for the trigger) |
| `H` halt | – | `H`, once the halt has taken effect |
| `Q` status | – | `Q` + 38 bytes: version, state, flags, log2 depth, channels, records, lost, start / end / current stamp, masks, mode, limit |
| `P` activity | – | `P` + 24 counters of 4 bytes |
| `U` read back | first record[2], count[2] | `U`, n[2], n records of 9 bytes, CRC-16/CCITT-FALSE of the record bytes |

`pe_scope.py` reads the buffer in blocks of 512 records. The CRC covers the
record bytes only, not the `U` header's count or the `Q` and `P` replies, so
the client also checks those: the count must equal min(count, records stored
− first), and every `Q` field must lie in its range. A block or reply that
fails a check, or arrives short (a transport timeout), is requested again
after the input has been drained, at most 3 times; the capture's
cross-checks (start and end stamps against the records, activity counters
against the recorded edges) repeat the whole read once more if they fail.
Commands that change state (`A`, `F`, `H`) are not repeated. At 1 Mbaud, one
record takes 90 µs, so a full buffer takes 1.47 s (Cmod A7) or 2.95 s
(Urbana).

The bridge has no timeout while it forwards a capture-unit reply (state
`S_SCOPE`): the unit always ends its replies in the released RTL, but a
hardware fault that stopped it would leave the bridge waiting until the
board is reset (btn[0]). A watchdog is on the "Next rebuild" list.

**Depth.** The buffer is block RAM:

| Board | Records | Block RAM | Timing-probe frames in a full buffer | Window at 50 MHz |
|---|---|---|---|---|
| Cmod A7-35T | 16,384 | 32 RAMB36 of 50 | about 390 | about 2.7 ms |
| Urbana | 32,768 | 64 RAMB36 of 75 | about 780 | about 5.4 ms |

The timing probe changes its pins in 42 distinct cycles of its 344-cycle
frame, which is one record each. `PE_SCOPE_AW` (a Verilog define) sets another
depth, from 2 to 15 (at most 32,768 records: the status reply carries record
counts in 16 bits, and larger depths would be truncated silently, so
`build.sh` and `build.tcl` reject them), and `PE_NO_SCOPE` builds without the
unit (bridge protocol 1).

On the Urbana the buffer has a single address port: writes take it, and a
read-back read that falls on a write cycle is repeated (read-back normally
runs after the halt, when nothing is written). The reason is the toolchain.
Yosys maps the dual-port buffer to RAMB36 blocks with the write on port A
and the read on port B, and the Spartan-7 part of the prjxray database in
the openXC7 release used here has no configuration bits for the RAMB36
port-B widths (`BRAM36_READ_WIDTH_B_1`; the Artix-7 part has them).
`fasm2frames` rejected that Urbana build (job 24123603). The single-port
buffer uses port A only. `PE_SCOPE_1PORT` / `PE_SCOPE_2PORT` select either
buffer on either board.

**PC side.** `fpga/host/pe_scope.py` arms the unit, reads it back, rebuilds
per-channel waveforms in cycles and writes them as a VCD whose tick is one
core clock cycle. `demo/pe_capture.py` recognises these VCDs and uses the
ticks as cycle numbers directly, with no `--clock` or `--fclk`.

```sh
python3 fpga/host/pe_scope.py --port /dev/ttyUSB1 status
python3 fpga/host/pe_scope.py --port /dev/ttyUSB1 capture --channels uio6,uio7 \
    --trigger uio6,uio7 --limit 4096 --json cap.json --vcd cap.vcd
python3 fpga/host/pe_scope.py show cap.json
python3 demo/pe_capture.py analyze cap.vcd --channels probe6=uio6,probe7=uio7 --reference predicted.json
```

## Builds

| Build | Core clock | Host | Bitstream |
|---|---|---|---|
| `cmod_a7 pll50` | 12 MHz oscillator → MMCM (×50 ÷12, VCO 600 MHz) → 50 MHz | UART bridge (default) or DIP pin host synchronous to `host_clk` (output) | `pe_cmod_a7_pll50.bit` |
| `cmod_a7 pll50`, `BRIDGE_ONLY=1` | as above | UART bridge only (DIP host pins unused) | `pe_cmod_a7_pll50_bridgeonly.bit` |
| `cmod_a7 pll40` | 12 MHz oscillator → MMCM (×50 ÷15, VCO 600 MHz) → 40 MHz | as `pll50` | `pe_cmod_a7_pll40.bit` |
| `cmod_a7 osc12` | 12 MHz oscillator directly (no MMCM) | as `pll50` | `pe_cmod_a7_osc12.bit` |
| `cmod_a7 host` | `host_clk` pin, driven by the host | DIP pin host only | `pe_cmod_a7_host.bit` |
| `urbana pll50` | 100 MHz oscillator → MMCM (×10 ÷20, VCO 1000 MHz) → 50 MHz | UART bridge (sw[0] down) or Pmod pin host (sw[0] up) | `pe_urbana_pll50.bit` |
| `urbana pll40` | MMCM ×10 ÷25 → 40 MHz (option, not built) | as `urbana pll50` | – |
| `urbana host` | `host_clk` pin (JAB_1) | Pmod pin host only | `pe_urbana_host.bit` |

The firmware images count clocks, so every protocol rate scales with the core
clock. UART at 64 clocks per bit, for example, is 781.25 kbaud at 50 MHz,
625 kbaud at 40 MHz and 187.5 kbaud at 12 MHz. With a host clock, the rate is
whatever the host provides. The UART bridge runs at 1 Mbaud in every build:
its divider is 50, 40 or 12 clocks per bit.

`osc12` avoids the MMCM entirely. It is the fallback if the MMCM output does
not come up on real hardware: openXC7's MMCM support is recent (see
"Toolchain").

`pe_host.py info` reports the build and measures the core clock against the
PC clock.

From 2026-09-27 every UART-bridge build (`pll50`, `pll50` + `BRIDGE_ONLY=1`,
`pll40`, `osc12`) contains the on-board capture unit (bridge protocol 2);
`VERILOG_DEFINES=-DPE_NO_SCOPE` builds without it. The `host` builds are
unchanged. The 2026-09-27 release is described first below; the 2026-09-26
release (no capture unit) follows.

### Build results, capture-unit release (2026-09-27)

These bitstreams were built on MIT Engaging through Slurm on 2026-09-27 with
the openXC7 2026-09-24 toolchain and Yosys 0.67+111, from this tree (`src/`
unchanged; `protocol_emulator_core.v` SHA-256 `26a873db…`). Job ids and
results are in `$PE_WORK/fpga-scope/manifest.json`.

- **fmax** is nextpnr-xilinx's post-route estimate for the core clock `clk`
  (`--report`, prjxray's -1 timing data). It is not a vendor sign-off; the
  Vivado sign-off of the same design is under "Vivado sign-off flow".
- **Options.** The synthesis options of the 2026-09-26 builds (ABC9 script
  `flow3mfs`, wire delay 1000 ps, with or without `-nowidelut` per build),
  and placer seeds 1–40 at timing weights 80 and 160 with the default
  router2 (job 24125836, 560 runs, one CPU each). The fastest run of each
  build became the bitstream: `fpga/scripts/release.sh` rebuilt each one from
  scratch (job 24126323), every rebuild gave the fmax of its sweep run, and
  every bitstream passed the readback check (0 missing, 0 extra
  configuration bits). `fpga/scripts/release.tsv` records seed and options.
- **All seven builds were rebuilt**, the `host` builds too, although they have
  no capture unit and their logic is unchanged: Yosys' LUT mapping depends on
  the text of the input files, and the shared shell and board tops changed.
  Rebuilt with the 2026-09-26 seeds and options, the `host` netlists differ
  from the 2026-09-26 ones and reach 62.84 MHz (Cmod A7) and 72.60 MHz
  (Urbana) instead of 80.43 and 76.44 MHz (job 24123603), so they took part in
  the seed sweep as well.
- **LUT cells, FFs, CARRY4** are nextpnr's placed bels, as in the tables of
  the 2026-09-26 release; RAMB36 counts 36 Kb block RAMs. Percentages are of
  the part's datasheet capacity (xc7a35t: 20,800 LUTs, 41,600 FFs, 50 RAMB36;
  xc7s50: 32,600, 65,200, 75).

| Build | Options | Placer seed | **fmax** | Target | LUT cells | FFs | CARRY4 | RAMB36 | Capture buffer |
|---|---|---|---|---|---|---|---|---|---|
| `cmod_a7 pll50` | `-nowidelut`; timing weight 160 | 18 | **71.36 MHz** | 50 MHz | 12,734 (61.2%) | 4,203 (10.1%) | 561 | 32 (64%) | 16,384 records |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | `-nowidelut`; timing weight 80 | 40 | **73.19 MHz** | 50 MHz | 12,713 (61.1%) | 4,201 (10.1%) | 561 | 32 (64%) | 16,384 records |
| `cmod_a7 pll40` | `-nowidelut`; timing weight 160 | 30 | **66.66 MHz** | 40 MHz | 12,828 (61.7%) | 4,203 (10.1%) | 562 | 32 (64%) | 16,384 records |
| `cmod_a7 osc12` | `-nowidelut`; timing weight 160 | 26 | **73.83 MHz** | 12 MHz | 12,902 (62.0%) | 4,203 (10.1%) | 561 | 32 (64%) | 16,384 records |
| `urbana pll50` | timing weight 160 | 5 | **76.04 MHz** | 50 MHz | 13,423 (41.2%) | 4,212 (6.5%) | 562 | 64 (85%) | 32,768 records, single port |
| `cmod_a7 host` | timing weight 160 | 23 | **69.31 MHz** | 50 MHz | 8,865 (42.6%) | 2,030 (4.9%) | 258 | 0 | – |
| `urbana host` | `-nowidelut`; timing weight 160 | 40 | **74.32 MHz** | 50 MHz | 8,777 (26.9%) | 2,030 (3.1%) | 258 | 0 | – |

All builds use `flow3mfs` with W 1000. The capture unit adds about 3,000 LUT
cells, 1,730 FFs and 240 CARRY4 to a bridge build (Cmod A7 `pll50`: 9,777
LUT cells, 2,471 FFs and 318 CARRY4 in the 2026-09-26 release), mostly for
the 24 activity counters, the 48-bit stamps and the status snapshot. The
MMCM counter settings in the FASM are those of the 2026-09-26 table below.

Seed distributions (job 24125836). "Completed" excludes the 5 of 560 runs that
ended in a nextpnr error:

| Build | `-nowidelut` | `TIMING_WEIGHT` | Runs | Completed | fmax min / median / max (MHz) | Completed runs ≥ target |
|---|---|---|---|---|---|---|
| `cmod_a7 pll50` | yes | 80 | 40 | 40 | 53.48 / 57.49 / 63.49 | 40 |
| `cmod_a7 pll50` | yes | 160 | 40 | 40 | 55.41 / 61.47 / 71.36 | 40 |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | yes | 80 | 40 | 40 | 52.76 / 62.42 / 73.19 | 40 |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | yes | 160 | 40 | 40 | 48.90 / 61.00 / 66.30 | 39 |
| `cmod_a7 pll40` | yes | 80 | 40 | 39 | 54.80 / 60.79 / 65.88 | 39 |
| `cmod_a7 pll40` | yes | 160 | 40 | 39 | 52.10 / 58.28 / 66.66 | 39 |
| `cmod_a7 osc12` | yes | 80 | 40 | 38 | 53.67 / 60.82 / 66.67 | 38 |
| `cmod_a7 osc12` | yes | 160 | 40 | 40 | 54.95 / 66.14 / 73.83 | 40 |
| `urbana pll50` | no | 80 | 40 | 40 | 57.67 / 69.92 / 75.67 | 40 |
| `urbana pll50` | no | 160 | 40 | 39 | 59.85 / 66.95 / 76.04 | 39 |
| `cmod_a7 host` | no | 80 | 40 | 40 | 47.87 / 58.20 / 66.14 | 38 |
| `cmod_a7 host` | no | 160 | 40 | 40 | 48.91 / 60.09 / 69.31 | 38 |
| `urbana host` | yes | 80 | 40 | 40 | 50.43 / 60.80 / 69.31 | 40 |
| `urbana host` | yes | 160 | 40 | 40 | 58.38 / 66.44 / 74.32 | 40 |

**Timing findings.**

- Every published build meets its clock target in nextpnr's model, and 550
  of the 555 completed sweep runs do; the exceptions are one
  `BRIDGE_ONLY=1` run (48.90 MHz) and four `cmod_a7 host` runs (47.87–49.78
  MHz).
- Critical paths of the published runs. In six of the seven, the path starts
  at a core register (for example `shell.tt.core._1623` or
  `host_selected_engine`) and ends in the core or its host-port logic, as in
  the 2026-09-26 builds. In the published Cmod A7 `pll50` run it does not:
  it starts at the UART bridge's transmit-FIFO read pointer
  (`shell.g_bridge.bridge.u_txf.rp[1]`), runs through the "FIFO not full"
  term of the bridge's `sc_rsp_ready` into the capture unit's reply
  handshake, and ends at the clock enable of the unit's 312-bit reply shift
  register `sh` (`pe_fpga_scope.v`, responder): 14.0 ns, 1.2 ns of logic
  and 12.8 ns of routing, of which 4.1 ns is the last net, the enable's
  fan-out. It meets 50 MHz (71.36 MHz). Across the sweep, paths start at core
  registers, at the host-select synchroniser (`status[3]`) or, as here, in
  the bridge; routing dominates in all of them.
- The capture unit takes area and 32 or 64 block RAMs across the die: the
  Cmod A7 `pll50` median fell from about 70 MHz (2026-09-26 sweep) to
  57–61 MHz, and the fastest run from 79.69 to 71.36 MHz. Registering
  `sc_rsp_ready` and splitting the `sh` enable are on the "Next rebuild"
  list.
- An earlier sweep of the same design (job 24120781, 768 runs, a slightly
  different text of `pe_fpga_scope.v`, dual-port buffer) measured the
  buffer depth on the Urbana: with 16,384 records the router2 medians were
  70.9–71.1 MHz, with 32,768 records 66.2–66.9 MHz. Of its 162 router1
  runs, 21 finished within the 25-minute limit; router1 was not used for the
  final sweep.

**Bitstreams.** `$PE_WORK/fpga/bitstreams/v3-2026-09-27-scope/`, with their
summaries, options, readback reports and `SHA256SUMS`; the 2026-09-26 set
stays in `$PE_WORK/fpga/bitstreams/` and the 2026-09-25 set in `v1-2026-09-25/`.

| File | Bytes | SHA-256 | Readback: configuration bits, frames |
|---|---|---|---|
| `pe_cmod_a7_pll50.bit` | 2,192,126 | `a505222368852c9b73cad20d5dd3457bdb049b99d74c10581eb1d99defcae250` | 605,277 bits, 2,711 frames: 0 missing, 0 extra |
| `pe_cmod_a7_pll50_bridgeonly.bit` | 2,192,137 | `166435cac44356d6e3ed129c390fca8e3602607becb95434e40b8a68198c989d` | 605,360 bits, 3,432 frames: 0 missing, 0 extra |
| `pe_cmod_a7_pll40.bit` | 2,192,126 | `6f264f95d5a29268f7f0ea7c2b006db4e38857b91268a3f0cc5a4944ab77e2b8` | 614,475 bits, 2,945 frames: 0 missing, 0 extra |
| `pe_cmod_a7_osc12.bit` | 2,192,126 | `558969cfd213401dbdd5f64f365ff0d1e9c6fc27688cdea0cafb4c843fea074b` | 610,954 bits, 2,569 frames: 0 missing, 0 extra |
| `pe_urbana_pll50.bit` | 2,192,125 | `63fad9234ab8d709f556f76629aaaf1624b7cd89ed15b3c353e1bafceaa1efb4` | 662,402 bits, 2,680 frames: 0 missing, 0 extra |
| `pe_cmod_a7_host.bit` | 2,192,125 | `4644f1d207e12d366108086486c6ec346ccdf172a075c5a654874ba3d5655f60` | 418,436 bits, 1,762 frames: 0 missing, 0 extra |
| `pe_urbana_host.bit` | 2,192,124 | `4edc7febb28eeee7ee717c02680869fd903485be138b7ee7701bf371468d2846` | 400,355 bits, 1,976 frames: 0 missing, 0 extra |

**Recommended bitstreams (2026-09-27).** PC over USB: `pe_cmod_a7_pll50.bit`
(Cmod A7, with the DIP pin host available too) or
`pe_cmod_a7_pll50_bridgeonly.bit`, and `pe_urbana_pll50.bit`; all three
contain the capture unit. `pe_cmod_a7_osc12.bit` if the MMCM does not come up.
Pin hosts: `pe_cmod_a7_host.bit`, `pe_urbana_host.bit`.

### Build results

This subsection is the 2026-09-26 release, without the capture unit (bridge
protocol 1). Its bitstreams stay in `$PE_WORK/fpga/bitstreams/`.

The bitstreams below were built on MIT Engaging through Slurm on 2026-09-26
with the openXC7 2026-09-24 toolchain, from the design sources of commit
be7dbda (`src/` unchanged since 27ae5e1) and this `fpga/` tree.

- **fmax** is nextpnr-xilinx's post-route estimate for the core clock `clk`
  (its `--report`, for the finished design), using prjxray's -1 timing data.
  It is not a Vivado sign-off.
- **Options** are the `build.sh` options of each bitstream, recorded in
  `fpga/scripts/release.tsv`:
  - synthesis: ABC9 script `flow3mfs` with a 1000 ps wire delay
    (`ABC9_SCRIPT=flow3mfs ABC9_W=1000`), with or without `-nowidelut`
    (`SYNTH_OPTS`), whichever had the higher median fmax for that build in a
    first sweep of 32 seeds at each of the timing weights 80, 160 and 320
    (job 23993904);
  - placement: `TIMING_WEIGHT`, nextpnr's `placerHeap/timingWeight`;
  - routing: nextpnr's default `router2`, or `router1` (`ROUTER=router1`).
  See "Implementation study" for how these were found.
- **Placer seed** is the fastest run of the seed sweeps with the build's
  synthesis option set (repository sweep harness, `fpga/scripts/sweep/`;
  second table below). Each bitstream was rebuilt from scratch with
  `fpga/scripts/release.sh` (job 23998703): every rebuild gave the same fmax
  as its sweep run, and its `synth_netlist.v` is byte-identical to the
  netlist simulated under "Verification".
- **2026-09-25** is the fmax of the previous bitstream (default options, best
  of 8 or 16 seeds; build jobs 23760779, 23760777, 23763553, 23760781,
  23763555, 23760780 and 23763558 in table order, readback job 23767112).
- **LUT cells** are nextpnr's placed LUT bels, which include LUT RAM and
  route-through LUTs. Percentages are against the part's datasheet capacity:
  20,800 LUTs and 41,600 FFs for the xc7a35t; 32,600 and 65,200 for the
  xc7s50. Resource use is within 3 % of the 2026-09-25 builds.
- No block RAM and no DSP are used. The eight SRAM stand-ins take
  128 RAM64X1S; the core's other small memories take 48 RAM32M and 6 RAM64M
  (Yosys cell counts; "LUT RAM" counts these cells). The `pll50` and `pll40`
  builds use one MMCM.

| Build | Options | Placer seed | **fmax** | Target | 2026-09-25 | LUT cells | FFs | CARRY4 | LUT RAM | IO pads | Jobs (sweeps; build) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `cmod_a7 pll50` | `flow3mfs` W 1000, `-nowidelut`; timing weight 80; router1 | 41 | **79.69 MHz** | 50 MHz | 46.39 MHz | 9,777 (47.0%) | 2,471 (5.9%) | 318 | 182 | 38 | 23993904, 23994656, 23997747; 23998703 |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | `flow3mfs` W 1000, `-nowidelut`; timing weight 80; router2 | 190 | **78.38 MHz** | 50 MHz | 55.82 MHz | 9,646 (46.4%) | 2,469 (5.9%) | 316 | 182 | 38 | 23993904, 23994656; 23998703 |
| `cmod_a7 pll40` | `flow3mfs` W 1000, `-nowidelut`; timing weight 160; router2 | 214 | **73.91 MHz** | 40 MHz | 50.83 MHz | 9,596 (46.1%) | 2,471 (5.9%) | 316 | 182 | 38 | 23993904, 23994656; 23998703 |
| `cmod_a7 osc12` | `flow3mfs` W 1000, `-nowidelut`; timing weight 80; router1 | 30 | **76.45 MHz** | 12 MHz | 46.20 MHz | 9,745 (46.9%) | 2,471 (5.9%) | 316 | 182 | 38 | 23993904, 23994656, 23997747; 23998703 |
| `cmod_a7 host` | `flow3mfs` W 1000; timing weight 80; router1 | 124 | **80.43 MHz** | 50 MHz | 51.42 MHz | 8,970 (43.1%) | 2,030 (4.9%) | 261 | 176 | 38 | 23993904, 23994656, 23997747; 23998703 |
| `urbana pll50` | `flow3mfs` W 1000; timing weight 160; router1 | 23 | **86.95 MHz** | 50 MHz | 54.35 MHz | 10,120 (31.0%) | 2,471 (3.8%) | 319 | 182 | 63 | 23993904, 23994656, 23997747; 23998703 |
| `urbana host` | `flow3mfs` W 1000, `-nowidelut`; timing weight 160; router1 | 15 | **76.44 MHz** | 50 MHz | 61.92 MHz | 8,703 (26.7%) | 2,030 (3.1%) | 261 | 176 | 63 | 23993904, 23994656, 23997747; 23998703 |

Seed distributions of the final synthesis option set of each build, per
timing weight and router (sweeps 23993904, 23994656 and, for router1,
23997747). "Completed" excludes router1 runs stopped at the 25-minute limit
and nextpnr runs that ended in an error:

| Build | Synthesis | `TIMING_WEIGHT` | Router | Runs | Completed | fmax min / median / max (MHz) | Completed runs ≥ target |
|---|---|---|---|---|---|---|---|
| `cmod_a7 pll50` | `flow3mfs` W 1000, `-nowidelut` | 80 | router1 | 48 | 14 | 65.98 / 71.00 / 79.69 | 14 |
| `cmod_a7 pll50` | `flow3mfs` W 1000, `-nowidelut` | 80 | router2 | 432 | 432 | 54.32 / 70.60 / 79.66 | 432 |
| `cmod_a7 pll50` | `flow3mfs` W 1000, `-nowidelut` | 160 | router2 | 432 | 432 | 58.38 / 70.45 / 79.53 | 432 |
| `cmod_a7 pll50` | `flow3mfs` W 1000, `-nowidelut` | 320 | router2 | 32 | 32 | 56.89 / 67.03 / 78.03 | 32 |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | `flow3mfs` W 1000, `-nowidelut` | 80 | router1 | 48 | 28 | 59.69 / 71.46 / 77.29 | 28 |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | `flow3mfs` W 1000, `-nowidelut` | 80 | router2 | 432 | 432 | 54.83 / 68.94 / 78.38 | 432 |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | `flow3mfs` W 1000, `-nowidelut` | 160 | router2 | 432 | 432 | 56.63 / 70.14 / 78.20 | 432 |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | `flow3mfs` W 1000, `-nowidelut` | 320 | router2 | 32 | 32 | 59.21 / 68.52 / 76.62 | 32 |
| `cmod_a7 pll40` | `flow3mfs` W 1000, `-nowidelut` | 80 | router1 | 76 | 47 | 55.61 / 66.53 / 72.61 | 47 |
| `cmod_a7 pll40` | `flow3mfs` W 1000, `-nowidelut` | 80 | router2 | 432 | 432 | 54.08 / 65.25 / 71.92 | 432 |
| `cmod_a7 pll40` | `flow3mfs` W 1000, `-nowidelut` | 160 | router2 | 432 | 432 | 50.70 / 63.36 / 73.91 | 432 |
| `cmod_a7 pll40` | `flow3mfs` W 1000, `-nowidelut` | 320 | router2 | 32 | 32 | 52.33 / 62.00 / 67.63 | 32 |
| `cmod_a7 osc12` | `flow3mfs` W 1000, `-nowidelut` | 80 | router1 | 135 | 115 | 64.10 / 70.14 / 76.45 | 115 |
| `cmod_a7 osc12` | `flow3mfs` W 1000, `-nowidelut` | 80 | router2 | 432 | 432 | 57.61 / 66.17 / 72.94 | 432 |
| `cmod_a7 osc12` | `flow3mfs` W 1000, `-nowidelut` | 160 | router2 | 432 | 432 | 52.22 / 66.62 / 75.99 | 432 |
| `cmod_a7 osc12` | `flow3mfs` W 1000, `-nowidelut` | 320 | router2 | 32 | 32 | 53.73 / 62.45 / 71.49 | 32 |
| `cmod_a7 host` | `flow3mfs` W 1000 | 80 | router1 | 226 | 167 | 54.70 / 69.88 / 80.43 | 167 |
| `cmod_a7 host` | `flow3mfs` W 1000 | 80 | router2 | 432 | 432 | 53.03 / 66.58 / 76.98 | 432 |
| `cmod_a7 host` | `flow3mfs` W 1000 | 160 | router2 | 432 | 432 | 53.26 / 66.19 / 79.83 | 432 |
| `cmod_a7 host` | `flow3mfs` W 1000 | 320 | router2 | 32 | 32 | 54.97 / 64.18 / 75.32 | 32 |
| `urbana pll50` | `flow3mfs` W 1000 | 160 | router1 | 48 | 13 | 67.22 / 80.78 / 86.95 | 13 |
| `urbana pll50` | `flow3mfs` W 1000 | 80 | router2 | 432 | 430 | 65.16 / 74.53 / 82.19 | 430 |
| `urbana pll50` | `flow3mfs` W 1000 | 160 | router2 | 432 | 427 | 58.61 / 76.41 / 84.65 | 427 |
| `urbana pll50` | `flow3mfs` W 1000 | 320 | router2 | 32 | 30 | 56.74 / 70.28 / 80.97 | 30 |
| `urbana host` | `flow3mfs` W 1000, `-nowidelut` | 160 | router1 | 48 | 30 | 60.35 / 71.03 / 76.44 | 30 |
| `urbana host` | `flow3mfs` W 1000, `-nowidelut` | 80 | router2 | 32 | 32 | 55.60 / 63.38 / 71.67 | 32 |
| `urbana host` | `flow3mfs` W 1000, `-nowidelut` | 160 | router2 | 432 | 432 | 54.32 / 67.93 / 76.12 | 432 |
| `urbana host` | `flow3mfs` W 1000, `-nowidelut` | 320 | router2 | 432 | 432 | 54.28 / 67.47 / 75.44 | 432 |

**Timing findings.**

- Every build meets its clock target in nextpnr's model, and so does every
  completed run of the final option sets (second table). The chosen runs are
  53–74 % above 50 MHz for the 50 MHz builds (76.44–86.95 MHz).
- The Cmod A7 `pll50` build with the DIP pin host went from 46.39 MHz (best of
  16 seeds; 0 of 398 seeds reached 50 MHz with the 2026-09-25 options, job
  23992344) to a median of 70.60 MHz over 432 seeds at timing weight 80,
  lowest run 54.32 MHz. The pin host no longer costs fmax: the bridge-only
  build has a median of 68.94 MHz with the same options.
- The gain comes from the implementation options measured in
  "Implementation study": the placer's timing weight (median over seeds
  43.95 → 51.66 MHz), the LUT mapping (→ 69.05 MHz) and the router (about
  +3 MHz on the same placement). The published runs are the fastest seeds of
  these distributions.
- In the final router2 builds the critical path starts at a core register
  (net `shell.tt.core._1623`) and has 1.7–1.8 ns of logic and 10.9–11.8 ns of
  routing, against 1.7 ns and 19.9 ns in the 2026-09-25 Cmod A7 `pll50` build.
- **router1.** On the same placement (same options and seed), router1 gave a
  higher fmax than the default router2 in 383 of 414 paired runs (median
  +3.04 MHz). It is less predictable: of 629 router1 runs, 414 completed,
  each within 4.2 minutes, 3 ended in a nextpnr placement error, and 212 had
  not finished after the 25-minute limit (`SWEEP_RUN_TIMEOUT`). Five of the
  seven published bitstreams come from router1 runs; their place and route
  takes 2.5–3.2 minutes.
- **Reported fmax.** All fmax values here are nextpnr's `--report` for the
  finished design. With router1, this nextpnr version also prints router1's
  own timing analysis, made before constant-net routing and the post-route
  fixups; it is higher than the report (95.18 against 79.69 MHz for the
  published Cmod A7 `pll50` run) and is not used. With router2, the last
  printed analysis and the report agree.

**Recommended bitstreams.**

- PC over USB at 50 MHz: `pe_cmod_a7_pll50_bridgeonly.bit` or
  `pe_cmod_a7_pll50.bit` (Cmod A7), `pe_urbana_pll50.bit` (Urbana).
- Pico or another host-clocked pin host: `pe_cmod_a7_host.bit`,
  `pe_urbana_host.bit`.
- A pin host synchronous to the forwarded 50 MHz clock: `pe_cmod_a7_pll50.bit`
  (its core now meets 50 MHz in nextpnr's model with the DIP pin host
  included; the pin paths themselves are not timed, see "Limitations"), or
  `pe_cmod_a7_pll40.bit` at 40 MHz.
- `pe_cmod_a7_osc12.bit` if the MMCM does not come up on hardware.

**Bitstreams.** They are stored with their summaries (`.summary.json`),
options (`.recipe.json`) and readback reports in `$PE_WORK/fpga/bitstreams/`
(not in git). The 2026-09-25 set is kept in
`$PE_WORK/fpga/bitstreams/v1-2026-09-25/` with its own `SHA256SUMS`.

A rebuild reproduces the same placement and fmax: each release build above
reproduced the fmax of its sweep run. The `.bit` header also carries the build
time, so compare configuration data (`readback.json`), not file hashes; two
rebuilds of the 2026-09-25 `urbana host` bitstream (jobs 23767384 and
23768209) differed from the recorded file in 3 and 4 header bytes only.

| File | Bytes | SHA-256 | Readback: configuration bits, frames |
|---|---|---|---|
| `pe_cmod_a7_pll50_bridgeonly.bit` | 2,192,137 | `593e7494417c99f38fb26c2cf4b93d528e466ebff471670e32021f024808a28c` | 438,587 bits, 2,351 frames: 0 missing, 0 extra |
| `pe_cmod_a7_pll50.bit` | 2,192,126 | `d1fb0c12d6e228e2c92914c115967e133a9c786368f32029e451102f34fa76da` | 467,129 bits, 1,688 frames: 0 missing, 0 extra |
| `pe_cmod_a7_pll40.bit` | 2,192,126 | `fb54de691047845f18c2ec3275e694c27a88b1737cff95bcefab986aacf2c4df` | 444,056 bits, 1,830 frames: 0 missing, 0 extra |
| `pe_cmod_a7_osc12.bit` | 2,192,126 | `ee9d36ca3e203eb8700d49af1d6db2224ff6bb47435510eaf886682cafdefbef` | 460,743 bits, 1,666 frames: 0 missing, 0 extra |
| `pe_cmod_a7_host.bit` | 2,192,125 | `09728304cadfbbfb1d8c9a18137cb638db84c2ccd4757c955cea410bed2d1437` | 422,606 bits, 2,128 frames: 0 missing, 0 extra |
| `pe_urbana_pll50.bit` | 2,192,125 | `f1fd42dcfc202b171c2698b91b5bc70d6961797627263de0e5026a7afdc8c1eb` | 471,834 bits, 2,030 frames: 0 missing, 0 extra |
| `pe_urbana_host.bit` | 2,192,124 | `f09aab8e77e92fa3c43210a0faa9e3bcff12e7d878be5927d7b2643adcd016a5` | 408,430 bits, 1,835 frames: 0 missing, 0 extra |

**Readback.** `fpga/scripts/readback.sh` checks each bitstream at the bit
level; `build.sh` runs it after bit generation (job 23998703, all 7 PASS):

- It decodes the `.bit` with prjxray's `bitread`.
- It compares the set bits with the frames `fasm2frames` produces from
  nextpnr's FASM.
- Each bitstream must carry exactly those configuration bits, 0 missing and 0
  extra.
- `bitread` ignores the per-frame ECC word.

This checks bitstream assembly, not the FASM semantics.

The MMCM counter settings in the final FASM match the intended divisors:

| Build | DIVCLK | CLKFBOUT high/low | CLKOUT0 high/low |
|---|---|---|---|
| Cmod A7 `pll50`, `pll50` + `BRIDGE_ONLY=1` | no-count (÷1) | 25/25 (×50) | 6/6 (÷12) |
| Cmod A7 `pll40` | no-count (÷1) | 25/25 (×50) | 7/8 with EDGE (÷15) |
| Urbana `pll50` | no-count (÷1) | 5/5 (×10) | 10/10 (÷20) |

### Implementation study

The 2026-09-25 flow (default synthesis and placer settings, best of 16
seeds) left the Cmod A7 `pll50` build with the DIP pin host at 46.39 MHz. The
study below used that build to find which implementation options move its
fmax. It ran on MIT Engaging through Slurm on 2026-09-26: 5,121 nextpnr runs
of 1–3 minutes each, one CPU per run, up to 250 at a time (jobs 23992344,
23992656, 23993321 and 23993322); the final seed sweeps of all seven builds
("Build results") added 7,573. Each row below is a set of placer seeds with
one option changed. The design sources are those of commit be7dbda (`src/`
unchanged since 27ae5e1).

**Runtime.** In the 2026-09-25 build jobs (16 runs in parallel in one 16-CPU
job), nextpnr's analytic placer spent 373 s in its equation solver (job
23760779). With one CPU per run it spends 3–4 s, and a whole run takes 1–2
minutes instead of about 7; the result is the same (seed 1: 44.88 MHz in both,
jobs 23760779 and 23991965). `build.sh` now sets `OMP_NUM_THREADS=1`.

**Placer timing weight.** nextpnr's HeAP placer weights each connection by
`1 + timingWeight × criticality^7` (nextpnr default `timingWeight` 10). Raising
it moves the whole seed distribution:

| `TIMING_WEIGHT` | Seeds | Min | Median | Max | Runs ≥ 50 MHz | Job |
|---|---|---|---|---|---|---|
| 3 | 45 | 39.22 | 42.80 | 46.47 | 0 | 23992344 |
| 10 (nextpnr default) | 398 | 38.34 | 43.95 | 49.50 | 0 | 23992344 |
| 20 | 42 | 41.99 | 46.01 | 49.85 | 0 | 23992344 |
| 40 | 28 | 43.31 | 49.08 | 53.99 | 10 | 23992344 |
| 80 | 48 | 46.66 | 50.95 | 57.48 | 28 | 23992656 |
| 160 | 48 | 46.10 | 51.66 | 57.92 | 36 | 23992656 |
| 320 | 48 | 43.62 | 50.26 | 58.83 | 25 | 23992656 |
| 640 | 48 | 41.11 | 47.75 | 56.07 | 11 | 23992656 |
| 1280 | 48 | 40.44 | 47.04 | 54.83 | 8 | 23992656 |
| 2560 | 48 | 42.43 | 46.72 | 53.90 | 11 | 23992656 |

**LUT mapping.** The critical path starts at the core register
`host_selected_engine` in 68 of 79 inspected default runs and passes 12–13
LUTs, with about 1.7 ns of logic and 20 ns of routing. Yosys' ABC9 maps the
combinational logic between registers; its wire-delay parameter `-W`
(default 300 ps for xc7) and its script decide how many LUT levels a path gets.
ABC reports 15 LUT levels for the default mapping and 8 for the `flow3mfs`
script (Yosys' `abc9.script.flow3mfs`) with `-W 1000`, for 2 % more LUTs.
ABC9 does not move registers, and ABC checks every mapped network against its
input ("Networks are equivalent"). `-nowidelut` maps to LUTs of at most six
inputs (no MUXF7/MUXF8). Results at timing weight 160, seeds 1–32 per row,
best first (job 23993321). Except for the first synthesis batch (job
23991872: the default script at W 300–2000, `flow2` and `flow3mfs` at W 300,
`flow2` at W 1000, and `-nowidelut` alone), the exploration netlists were
synthesized from a working copy whose Cmod A7 top also held the disabled
I/O-register option described below; that changes source line numbers and
net names, not logic. The final builds were synthesized again from this tree
(see "Build results").

| Synthesis options | ABC LUTs | ABC LUT levels | Min | Median | Max |
|---|---|---|---|---|---|
| `-nowidelut`, `flow3mfs`, W 1000 | 6,789 | 8 | 60.69 | 69.05 | 77.81 |
| `flow3mfs`, W 1000 | 6,898 | 8 | 55.09 | 65.94 | 70.69 |
| `-nowidelut`, `flow2`, W 1000 | 6,617 | 10 | 50.78 | 62.47 | 70.74 |
| `-nowidelut`, `flow3mfs`, W 300 | 6,653 | 11 | 49.56 | 60.56 | 67.47 |
| W 600 | 6,862 | 13 | 50.03 | 58.60 | 63.56 |
| `-nowidelut`, W 1000 | 6,819 | 12 | 49.03 | 58.46 | 64.38 |
| `flow2`, W 1000 | 6,771 | 10 | 50.63 | 58.27 | 65.54 |
| `-nowidelut`, W 2000 | 6,751 | 12 | 49.79 | 58.04 | 64.96 |
| `flow3mfs`, W 300 | 6,651 | 12 | 51.40 | 57.55 | 65.66 |
| `flow2`, W 300 | 6,524 | 14 | 47.64 | 57.08 | 64.53 |
| `-nowidelut` | 6,713 | 14 | 48.91 | 56.69 | 63.81 |
| W 2000 | 6,949 | 10 | 48.61 | 55.86 | 63.89 |
| `-nowidelut`, W 600 | 6,710 | 14 | 46.76 | 55.30 | 60.10 |
| `-nowidelut`, `flow3`, W 300 | 6,319 | 14 | 47.31 | 55.25 | 62.33 |
| W 1000 | 6,897 | 11 | 48.13 | 54.88 | 58.60 |
| `-nowidelut`, `flow2`, W 300 | 6,476 | 14 | 46.81 | 52.66 | 58.76 |
| default script, W 300 (the 2026-09-25 synthesis) | 6,786 | 15 | 46.21 | 51.86 | 57.92 |

Around the chosen point (job 23993322 and, for timing weights 80/160/320,
23993321; 32 seeds each, median fmax in MHz):

| `flow3mfs` wire delay `W` | 500 | 700 | 800 | 1000 | 1200 | 1500 | 2000 |
|---|---|---|---|---|---|---|---|
| timing weight 120 | 64.36 | 67.12 | 65.02 | 66.17 | 66.58 | 67.46 | 63.78 |
| timing weight 240 | 66.72 | 64.47 | 62.69 | 62.95 | 65.52 | 67.06 | 62.03 |
| timing weight 120, `-nowidelut` | – | 63.73 | – | **71.34** | 62.81 | 65.97 | – |
| timing weight 240, `-nowidelut` | – | 59.34 | – | 68.19 | 62.19 | 63.60 | – |

Without `-nowidelut` the medians stay within 62–68 MHz over W 500–2000. With
`-nowidelut`, W 1000 is clearly better than its neighbours (the mapping of
this design at that point, not a smooth optimum), which is why the final
builds chose between the two by a per-build sweep. For `-nowidelut`, `flow3mfs`
W 1000, the median over timing weights 80 / 120 / 160 / 240 / 320 / 480 is
69.92 / 71.34 / 69.05 / 68.19 / 67.56 / 66.45 MHz.

**Other options** (default synthesis, timing weight 320, seeds 1–24, job
23992656):

| Option | Seeds | Min | Median | Max | Runs ≥ 50 MHz | Failed |
|---|---|---|---|---|---|---|
| none (reference) | 24 | 43.62 | 49.64 | 58.83 | 10 | 0 |
| `--router router1` (10 seeds; the same seeds without it: 48.20 / 50.21 / 57.16) | 10 | 49.14 | 53.36 | 56.27 | 7 | 0 |
| `NEXTPNR_PLACER_BETA=0.3` / `0.5` / `0.6` | 24 each | 40.62 / 40.96 / 35.13 | 48.87 / 48.72 / 40.98 | 55.46 / 55.18 / 51.22 | 10 / 10 / 1 | 0 / 0 / 8 |
| `NEXTPNR_PLACER_ALPHA=0.04` / `0.15` | 24 each | 44.21 / 44.31 | 47.90 / 50.09 | 55.25 / 55.82 | 4 / 12 | 0 |
| `NEXTPNR_SPREAD_SCALE_X,Y` = 1,1 / 2,2 / 3,1 | 24 each | 43.27 / 44.13 / 43.15 | 48.97 / 49.08 / 52.02 | 56.69 / 53.03 / 56.16 | 10 / 11 / 20 | 0 |
| `router2/estimateWeight` 1.0 / 1.25 / 1.5 (default 1.75) | 24 each | 40.95 / 40.72 / 40.88 | 49.96 / 50.23 / 49.33 | 59.62 / 59.41 / 58.30 | 12 / 13 / 11 | 0 |
| `REGION` 0,20,47,119 / 0,40,65,109 / 10,25,57,124 | 24 each | 45.77 / 39.98 / 35.20 | 50.49 / 49.73 / 40.61 | 56.54 / 56.48 / 55.22 | 15 / 12 / 1 | 0 |

- A smaller core-clock period for the placer and router (16 or 18 ns instead
  of 20 ns) gave the same result as 20 ns for every seed: nextpnr's timing
  weights are relative to the critical path.
- `-widemux 5` produced MUXF8 cells nextpnr could not place (24 of 24 runs
  failed); `-retime` (ABC retiming) lowered the median to 38.7 MHz.
- Floorplan rectangles (`REGION`) did not raise the median; one of three was
  much worse.
- The simulated-annealing placer (`sa`) instead of HeAP gave 31–34 MHz
  (job 23757415, 2026-09-25).
- I/O-tile registers on the pin-host port (IDDR/ODDR on `ui`, `rst_n`, `ena`
  and `uo`, one cycle each way; an experiment outside this tree): median
  53.46 MHz over 21 of the seeds 1–24, against 49.64 MHz without. This changes
  the pin timing contract, and the LUT-mapping change gives more without it,
  so it was not adopted: the published builds keep the pins combinational, as
  on the chip.

## Pin maps

### Digilent Cmod A7-35T

Source: Digilent's
[Cmod-A7-Master.xdc](https://github.com/Digilent/digilent-xdc/blob/master/Cmod-A7-Master.xdc).
The constraints are in `fpga/constraints/cmod_a7_35t.xdc`. All I/O is
LVCMOS33, and the board runs from USB. "DIP n" is pin n of the 48-pin DIP
header. Pin 24 is VU (USB 5 V) and pin 25 is GND.

Protocol pins on Pmod JA (pins 5 and 11 are GND, 6 and 12 are 3.3 V):

| Signal | Pmod JA pin | FPGA pin |
|---|---|---|
| uio[0] | 1 | G17 |
| uio[1] | 2 | G19 |
| uio[2] | 3 | N18 |
| uio[3] | 4 | L18 |
| uio[4] | 7 | H17 |
| uio[5] | 8 | H19 |
| uio[6] | 9 | J19 |
| uio[7] | 10 | K18 |

Host port on the DIP header:

| Signal | DIP pins | FPGA pins |
|---|---|---|
| ui_in[0..7] (host → chip) | 1, 2, 3, 4, 5, 6, 7, 8 | M3, L3, A16, K3, C15, H1, A15, B15 |
| uo_out[0..7] (chip → host) | 9, 10, 11, 12, 13, 14, 17, 18 | A14, J3, J1, K2, L1, L2, M1, N3 |
| host select (in, pull-up): open = UART bridge, GND = pin host | 45 | U7 |
| host_clk: output (pll50/osc12) or input (host build) | 46 | W7 (MRCC, clock capable) |
| rst_n from the pin host (in, pull-up) | 47 | U8 |
| ena from the pin host (in, pull-up) | 48 | V8 |

`ui_in` bit meanings are from docs/isa.md:

- ui[3:0]: write nibble
- ui[4]: write-valid
- ui[5]: read-ready
- ui[7:6]: window

`uo_out` bit meanings:

- uo[3:0]: read nibble
- uo[4]: write-ready
- uo[5]: read-valid
- uo[6]: event/IRQ
- uo[7]: fault

Board I/O:

- USB-UART (FTDI): J17 is FTDI → FPGA, J18 is FPGA → FTDI. It appears as the
  second serial port of the board's USB device.
- btn[0] (A18) resets the board logic and the core.
- led[0] is a heartbeat (about 0.75 Hz at 50 MHz); led[1] is event/IRQ (uo[6]).
- The RGB LED is active low: red = fault (uo[7]), green = UART traffic,
  blue = pin host selected.

### Real Digital Urbana

Sources:

- Real Digital's `urbana.xdc` ([board page](https://www.realdigital.org/hardware/urbana));
- the MIT 6.205
  [top_level.xdc](https://fpga.mit.edu/6205/_static/F25/default_files/week07/top_level.xdc);
- the Urbana schematic, sheet 5 (PMOD HEADERS).

Constraints are in `fpga/constraints/urbana_xc7s50.xdc`. The official XDC
repeats JA2's pins (H13/H14) for JB2. The 6.205 file corrects this to
K14/J15 (IO_L23P/N_T3_15), and that correction is used here.

Everything goes through the 30-pin Pmod+ header J1. Its pin numbering runs
counter-clockwise: J1.1–15 along one row, J1.16–30 back along the other.

- PMOD A takes J1.1–6 and J1.25–30.
- PMOD B takes J1.10–15 and J1.16–21.
- Six GPIO (JAB_0–5) sit in the middle, J1.7–9 and J1.22–24.
- GND is J1.5, 14, 17 and 26; 3.3 V is J1.6, 15, 16 and 25.

Each signal has a 200 Ω series resistor on the board.

| Signal | Connector pin | J1 pin | FPGA pin |
|---|---|---|---|
| uio[0] | PMOD A 1 | 1 | F14 |
| uio[1] | PMOD A 2 | 2 | F15 |
| uio[2] | PMOD A 3 | 3 | H13 |
| uio[3] | PMOD A 4 | 4 | H14 |
| uio[4] | PMOD A 7 | 30 | J13 |
| uio[5] | PMOD A 8 | 29 | J14 |
| uio[6] | PMOD A 9 | 28 | E14 |
| uio[7] | PMOD A 10 | 27 | E15 |
| ui_in[0..3] | PMOD B 1, 2, 3, 4 | 10, 11, 12, 13 | H18, G18, K14, J15 |
| ui_in[4..7] | PMOD B 7, 8, 9, 10 | 21, 20, 19, 18 | H16, H17, K16, J16 |
| uo_out[0] | JAB_0 | 7 | D11 |
| host_clk: output (pll50) or input (host build) | JAB_1 | 8 | C12 (MRCC, clock capable) |
| uo_out[1] | JAB_2 | 9 | E16 |
| uo_out[2] | JAB_3 | 22 | G16 |
| uo_out[3] | JAB_4 | 23 | C11 |
| uo_out[4] (write-ready) | JAB_5 | 24 | D10 |
| uo_out[5] (read-valid) | servo J4 signal (SERVO1) | – | L17 |
| host reset, active high (pull-down) | servo J3 signal (SERVO0) | – | L18 |

The Pmod+ header has 22 signals; the pin host needs 24. The two lowest-rate
host signals therefore use servo-header signal pins:

- read-valid (`uo_out[5]`), on SERVO1;
- the host's reset, on SERVO0 (active high).

These pins have a 510 Ω series resistor on the board, which is fine for
host-clocked rates. **Never connect the servo headers' middle pin: it carries
+5 V servo power.**

`uo_out[7:6]` (fault, event) are shown on LEDs only. They can also be read as
status over the host port.

Switches and LEDs:

- sw[0]: host select (down = UART bridge, up = pin host).
- sw[1]: up = deselect (`ena` low) while the pin host is selected.
- btn[0]: board reset.
- LED[0]: heartbeat
- LED[1]: event/IRQ
- LED[2]: fault
- LED[3]: pin host selected
- LED[4]: UART traffic
- LED[5]: clock locked
- LED[6]: some protocol pin driven
- LED[7]: core out of reset
- LED[15:8]: live levels of uio[0..7]

USB-UART (FTDI FT2232H channel B): B16 is FTDI → FPGA, A16 is FPGA → FTDI.
Switches and buttons are on the 2.5 V bank 35 (LVCMOS25); everything else is
LVCMOS33.

## Hosts

### PC over USB (UART bridge, pll50/pll40/osc12 builds)

`fpga/rtl/pe_uart_host_bridge.v` receives 8N1 at 1 Mbaud; the divider is an
exact integer at 50, 40 and 12 MHz. It performs the nibble handshakes of
docs/isa.md in the core clock domain. It drives `ui_in` from registers and
registers `uo_out` at every edge.

Each nibble takes two cycles:

1. Valid (or ready) is high for one edge.
2. The registered `uo_out` then shows whether the core accepted the nibble at
   that edge.

As a result, no bridge logic sits on the core's combinational `uo_out` paths.

Commands are a command byte plus a fixed payload, little endian. The file
header has the full table. In summary:

- `V`: identify the build.
- `S`: sample pins.
- `C`: read a free-running cycle counter.
- `L`: set the transfer timeout.
- `W`: write a word to window 0–2.
- `R`: read a word from window 0 or 3, optionally after a window bounce for a
  fresh status snapshot.
- `X`: hold `rst_n` low for n clocks.
- `N`: drive `ena`.
- `A`, `F`, `H`, `Q`, `P`, `U` (bridge protocol 2, the 2026-09-27 bridge
  builds): the on-board capture unit ("On-board capture unit"). They work
  whichever host owns the TT host port; a protocol-1 bitstream answers `?`.

A stalled transfer times out, 20 ms by default. The bridge then abandons it by
changing window and replies `T`.

`fpga/host/pe_host.py` needs Python 3 and pyserial. It is the PC side, with
the same operations as `test/harness.py`: `command`, `status`, `load`,
`load_firmware` (with the SHA-256 identity checks), `push_tx`, `pop_rx`,
`route`, `engine_status`.

The CLI commands are `info`, `selftest`, `loopback`, `load NAME`, `status` and
`reset`. The same code runs against the simulated board in
`fpga/sim/test_fpga_bridge.py`.

On Linux, the Cmod A7 and the Urbana each enumerate two serial ports. The
UART is normally the second one, e.g. `/dev/ttyUSB1`. The first one is JTAG.

### Pin host (Pico) and the host clock option

The Tiny Tapeout demo board lets its RP2040/RP2350 clock the project. The
`host` builds do the same: the core clock is the `host_clk` pin.
`fpga/host/pico_host.py` (MicroPython) drives one full cycle per call:

1. Set `ui_in`, `rst_n` and `ena` with the clock low.
2. Sample `uo_out`; this is the pre-edge value.
3. Pulse the clock.

This is the cycle discipline of `test/harness.py`. With plain `machine.Pin`
calls it reaches roughly a few thousand cycles per second (not measured), which is
enough to load programs and watch the protocol pins at a proportionally
scaled rate.

Wiring for a Pico and the Cmod A7 `host` build (all signals 3.3 V; the
`PicoPort()` defaults):

| Pico | Cmod A7 | Signal |
|---|---|---|
| GP0–GP7 | DIP 1–8 | ui_in[0..7] |
| GP8–GP15 | DIP 9–14, 17, 18 | uo_out[0..7] |
| GP16 | DIP 46 | host_clk |
| GP17 | DIP 47 | rst_n |
| GP18 | DIP 48 | ena (optional; pulled up on the FPGA) |
| GND | DIP 25 | common ground |

For the Urbana `host` build:

- **ui_in**: GP0–7 go to PMOD B pins 1–4 and 7–10.
- **uo_out**: GP8–12 read JAB_0, JAB_2, JAB_3, JAB_4 and JAB_5 (uo_out[0..4]),
  and GP13 reads the SERVO1 signal (uo_out[5]).
- **Clock and reset**: GP16 drives JAB_1 (host_clk); GP17 drives the SERVO0
  signal (reset, active high).
- **Switches**: set sw[0] up.
- **Driver setup**: `PicoPort(uo=(8, 9, 10, 11, 12, 13), rst_active_high=True, ena=None)`.

The `pll50` and `pll40` builds also accept a pin host. Select it with DIP 45
to GND (Cmod A7) or sw[0] up (Urbana). In those builds `host_clk` is an output
carrying the core clock, for a host fast enough to be synchronous to it.
Examples are another FPGA or a logic-analyser pattern generator; a Pico cannot
keep up. The pin-host paths themselves (pin to core register, core register to
pin) are not timed by nextpnr; see "Limitations".

## Toolchain

Earlier Cmod A7 work in the monorepo (`projects/protocol-emulator/fpga/`,
`tools/fpga-*.sbatch`, `docs/fpga.md`) used openXC7 packages 0.9.3/0.9.4 and
later a locally patched nextpnr-xilinx. It hit timing-reconstruction
assertions and constant-net holdouts, and ran at 12 MHz. Those packages are
still at
`<cluster scratch>/protocol-emulator/fpga-implementation-toolchain*`.
The chipdbs there match their own releases only.

This flow uses a newer upstream release, with no local patches:
[FPGAwars tools-openxc7](https://github.com/FPGAwars/tools-openxc7) release
`2026-09-24`.

- It contains nextpnr-xilinx `0eae9fbb19`, prjxray-db, fasm2frames and
  xc7frames2bit.
- The chipdb assets for `xc7a35tcpg236` and `xc7s50csga324` come from the same
  release. A chipdb only works with the package of the same tag.
- Synthesis is Yosys 0.67 from OSS CAD Suite `20260729`.
- On the cluster it is unpacked at
  `$PE_WORK/fpga/toolchain/openxc7-20260924`.
- The openXC7 chipdb for the xc7a35t is the xc7a50t die; the two parts are the
  same silicon. nextpnr's "available" counts are therefore the 50T's.

To reproduce the toolchain elsewhere:

```sh
gh release download 2026-09-24 -R FPGAwars/tools-openxc7 \
  -p 'apio-openxc7-linux-x86-64-20260924.tgz' \
  -p 'apio-xilinx-chipdb-xc7a35tcpg236-20260924.bin.tgz' \
  -p 'apio-xilinx-chipdb-xc7s50csga324-20260924.bin.tgz' -p SHA256SUMS
grep -E 'linux-x86-64|xc7a35tcpg236|xc7s50csga324' SHA256SUMS | sha256sum -c
mkdir openxc7 && tar xzf apio-openxc7-linux-x86-64-20260924.tgz -C openxc7
tar xzf apio-xilinx-chipdb-xc7a35tcpg236-20260924.bin.tgz -C openxc7/chipdb
tar xzf apio-xilinx-chipdb-xc7s50csga324-20260924.bin.tgz -C openxc7/chipdb
export OPENXC7=$PWD/openxc7
```

To build (Yosys on `PATH`):

```sh
fpga/scripts/release.sh --list                        # published builds, seeds and options
fpga/scripts/release.sh pe_cmod_a7_pll50 build/pe_cmod_a7_pll50
# or any options and seeds:
SYNTH_OPTS=-nowidelut ABC9_SCRIPT=flow3mfs ABC9_W=1000 TIMING_WEIGHT=160 \
  fpga/scripts/build.sh cmod_a7 pll50 build/cmod_a7_pll50 $(echo heap:{1..16})
```

The flow has three steps:

1. **Synthesis**: `synth_xilinx -flatten -abc9`, plus `SYNTH_OPTS`, and the
   ABC9 script and wire delay of `ABC9_SCRIPT` / `ABC9_W` if set.
2. **Place and route**: each `PLACER:SEED` run is one
   `fpga/scripts/pnr_run.sh` call, a nextpnr-xilinx run with
   `--freq 50 --timing-allow-fail` and the placer settings of the options.
   The runs go in parallel, one CPU each (`OMP_NUM_THREADS=1`). The run with
   the highest fmax for the core clock goes through `fasm2frames` and
   `xc7frames2bit`.
3. **Outputs**: `summary.json` (options, utilisation, fmax of every run and
   their min / median / max, bitstream SHA-256), the synthesized netlist
   `synth_netlist.v`, the exact inputs under `src/`, and `readback.json` from
   `fpga/scripts/readback.sh`, which build.sh runs after bit generation.

A run takes 1–3 minutes on one CPU and about 1 GB of memory, so a 16-seed
build fits a 15-minute `mit_quicktest` job on 16 CPUs.

`fasm2frames` prints a file-locking warning on stdout on some network
filesystems. The script keeps only the frame lines, because a polluted frames
file makes `xc7frames2bit` abort.

### Seed and option sweeps

`fpga/scripts/sweep/` runs many place-and-route runs of one synthesized
build, one nextpnr run per CPU, as Slurm array workers:

| Path | Role |
|---|---|
| `scripts/pnr_run.sh` | one nextpnr run on a synthesized build directory; `build.sh` uses the same script, so a sweep result and a `build.sh` run with the same seed and options are the same nextpnr invocation |
| `scripts/sweep/plan.py` | appends one task line per seed: run id, build directory, `PLACER:SEED`, options |
| `scripts/sweep/run_task.sh` | runs one task line and writes `runs/<table>/<run_id>/result.json` (fmax, critical-path logic/routing split, utilisation) |
| `scripts/sweep/worker.sbatch` | Slurm array worker, one run per CPU: its own share of the lines first, then lines no other worker has claimed; lines with a result are skipped, so a requeued worker resumes |
| `scripts/sweep/collect.py` | min / median / max fmax, runs at or above the target, and the best seed, per (build, options) group |

With `OPENXC7` and Yosys set up as above:

```sh
# 1. synthesize once, with the synthesis options of the build
SYNTH_ONLY=1 SYNTH_OPTS=-nowidelut ABC9_SCRIPT=flow3mfs ABC9_W=1000 \
  fpga/scripts/build.sh cmod_a7 pll50 $PE_WORK/sw/c50p
# 2. one task line per seed and option set
python3 fpga/scripts/sweep/plan.py $PE_WORK/sw/t.tsv --build $PE_WORK/sw/c50p \
  --tag tw160 --seeds 1-1000 --opt TIMING_WEIGHT=160
# 3. 8 workers x 30 CPUs
sbatch -p mit_preemptable --requeue --array=0-7 -c 30 --mem=64G -J pe-fpga-sweep \
  fpga/scripts/sweep/worker.sbatch "$PWD/fpga/scripts/sweep" $PE_WORK/sw/t.tsv
# 4. summary
python3 fpga/scripts/sweep/collect.py $PE_WORK/sw/runs/t
```

Several workers can share one task table: each run is claimed with an atomic
`mkdir` before it starts, and a worker whose own lines are done takes
unclaimed lines of the others. With `SWEEP_STOP_AFTER=660` a worker fits a
15-minute `mit_quicktest` job, and `SWEEP_RUN_TIMEOUT=1500` stops runs
that take longer than 25 minutes (router1 runs either finished within about
4 minutes or ran on past 25). Each worker runs a private copy of
`fpga/scripts`: on the cluster's parallel file system, replacing a script
while jobs were executing it ended those runs with "Stale file handle"
errors (the nextpnr results were intact and were recovered from the run
directories).

## Vivado sign-off flow

The vendor timing sign-off for these parts is AMD Vivado's static timing
analysis. `fpga/vivado/build.tcl` ran with Vivado 2025.2 (ML Standard
edition, no license needed for the xc7a35t and the xc7s50) on MIT Engaging
through Slurm on 2026-09-27, for all seven builds of the 2026-09-27 release:
the same RTL, pin constraints and clock constraints as the openXC7 builds
(jobs 24149331 and 24149924; results in `$PE_WORK/fpga-scope/manifest.json`).
**All seven meet timing in Vivado.** Nothing has run on hardware.

```sh
vivado -mode batch -nojournal -source fpga/vivado/build.tcl -tclargs cmod_a7 pll50 build/vivado_cmod_a7_pll50
vivado -mode batch -nojournal -source fpga/vivado/build.tcl -tclargs urbana pll50 build/vivado_urbana_pll50
python3 fpga/vivado/timing_check.py build/vivado_cmod_a7_pll50/timing_summary.rpt \
    --utilization build/vivado_cmod_a7_pll50/utilization.rpt \
    --methodology build/vivado_cmod_a7_pll50/methodology.rpt --drc build/vivado_cmod_a7_pll50/drc.rpt
```

`build.tcl` is a non-project batch flow:

- **Sources and constraints.** The RTL list of `fpga/scripts/build.sh`, the
  same pin XDC, the port clocks of the same clock XDC (below), and the same
  Verilog defines per build (`CLOCK` argument, `BRIDGE_ONLY=1`,
  `DEFINES=PE_SCOPE_AW=14,...`). Part `xc7a35tcpg236-1` (Cmod A7-35T) or
  `xc7s50csga324-1` (Urbana).
- **Steps.** `synth_design`, `opt_design`, `place_design`,
  `phys_opt_design`, `route_design` with Vivado's default directives;
  `report_timing_summary`, `report_utilization`, `report_clocks`,
  `report_drc`, `report_methodology`; `write_bitstream` on PASS.
- **Verdict.** PASS needs WNS ≥ 0, TNS = 0, WHS ≥ 0, THS = 0; no unclocked
  register (`no_clock`) and no unconstrained internal endpoint in
  `check_timing`; no "Critical Warning" in the methodology report and no
  "Error" in the DRC report. The script writes `vivado_timing.txt`, prints
  `VIVADO TIMING PASS` or `FAIL`, exits 1 on FAIL, and writes the bitstream
  only on PASS (`BITSTREAM=always` writes it anyway, `BITSTREAM=0` never).
  `timing_check.py` gives the same verdict from the reports, adds the
  pulse-width columns (WPWS ≥ 0, TPWS = 0), and summarises the worst setup
  path with the fmax it implies, 1 / (period − WNS).

**Results** (job 24149924). Implied fmax is 1 / (period − WNS) of the worst
setup path. Vivado optimises only until the constraint is met, so this is a
lower bound on what Vivado could reach, and it is far below the other builds
for `osc12`, whose constraint is 83.333 ns.

| Build | WNS | TNS | WHS | THS | WPWS | `check_timing` | Implied fmax | nextpnr fmax | Slice LUTs | Registers | Block RAM tiles |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `cmod_a7 pll50` | 5.467 ns | 0 | 0.047 ns | 0 | 8.750 ns | clean | 68.81 MHz | 71.36 MHz | 7,482 (35.97%) | 4,194 (10.08%) | 36 of 50 |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | 5.089 ns | 0 | 0.038 ns | 0 | 8.750 ns | clean | 67.06 MHz | 73.19 MHz | 7,497 (36.04%) | 4,194 (10.08%) | 36 of 50 |
| `cmod_a7 pll40` | 9.871 ns | 0 | 0.030 ns | 0 | 11.250 ns | clean | 66.10 MHz | 66.66 MHz | 7,483 (35.98%) | 4,194 (10.08%) | 36 of 50 |
| `cmod_a7 osc12` | 64.445 ns | 0 | 0.014 ns | 0 | 40.416 ns | clean | 52.94 MHz | 73.83 MHz | 7,486 (35.99%) | 4,194 (10.08%) | 36 of 50 |
| `urbana pll50` | 5.821 ns | 0 | 0.028 ns | 0 | 3.000 ns | clean | 70.53 MHz | 76.04 MHz | 7,477 (22.94%) | 4,147 (6.36%) | 72 of 75 |
| `cmod_a7 host` | 5.808 ns | 0 | 0.077 ns | 0 | 8.750 ns | clean | 70.46 MHz | 69.31 MHz | 5,810 (27.93%) | 2,008 (4.83%) | 0 |
| `urbana host` | 4.979 ns | 0 | 0.046 ns | 0 | 8.750 ns | clean | 66.57 MHz | 74.32 MHz | 5,819 (17.85%) | 2,008 (3.08%) | 0 |

"clean": `no_clock` 0 and `unconstrained_internal_endpoints` 0. The only
non-zero `check_timing` counts are `no_input_delay` and `no_output_delay`
(10–21 and 15–24 ports): the board's asynchronous inputs and the pin-host
port, which neither flow constrains. All pins are placed by the pin XDC,
with the XDC's I/O standards and pull-ups (`report_io` on the routed
designs, job 24150720).

**Worst setup paths.**

| Build | From → to | Data path | Logic levels |
|---|---|---|---|
| `cmod_a7 pll50` | bridge `ui_reg[6]` (host-port window) → core LUT-RAM write enable | 13.38 ns (2.01 logic, 11.37 route) | 10 |
| `cmod_a7 pll50` + `BRIDGE_ONLY=1` | bridge `core_rst_n_reg` → instruction SRAM stand-in output register (engine 3) | 14.38 ns (2.48 logic, 11.90 route) | 11 |
| `cmod_a7 pll40` | SRAM stand-in (engine 1, low half) → SRAM stand-in output register (high half) | 14.73 ns (2.18 logic, 12.55 route) | 11 |
| `cmod_a7 osc12` | board reset synchroniser `rst_sync_reg[2]` → core `tx_1_reg` clock enable | 18.28 ns (2.36 logic, 15.92 route) | 9 |
| `urbana pll50` | core `host_selected_engine_reg[0]` → core `wait_limit_0_reg` reset | 13.34 ns (3.26 logic, 10.08 route) | 12 |
| `cmod_a7 host` | SRAM stand-in (engine 0, low half) → SRAM stand-in output register (high half) | 14.05 ns (2.55 logic, 11.50 route) | 11 |
| `urbana host` | board reset synchroniser → core LUT-RAM write enable | 14.34 ns (2.69 logic, 11.65 route) | 10 |

All of them are paths into or through the core, with routing 75–87 % of the
delay. None ends in the capture unit; the reply-handshake path that is the
critical path of the openXC7 Cmod A7 `pll50` build ("Build results,
capture-unit release") is not critical in Vivado.

**Comparison with nextpnr-xilinx.**

- Timing agrees in kind: both tools meet every target with a large margin.
  Vivado's implied fmax is 0.56–6.13 MHz below nextpnr's for the four
  bridge builds at 40 and 50 MHz, 7.75 MHz below for the Urbana `host` build
  and 1.15 MHz above for the Cmod A7 `host` build. The nextpnr figures are the fastest
  of 80 placer seeds per build; Vivado ran once per build with its default
  directives.
- The LUT counts are not comparable: nextpnr's "LUT cells" count placed LUT
  bels including route-through LUTs (12,713–13,423 for the bridge builds),
  Vivado's "Slice LUTs" count LUTs used for logic and memory (7,477–7,497).
  Register counts agree within 1.6 % (Vivado 4,147–4,194 against nextpnr's
  4,201–4,212 for the bridge builds, 2,008 against 2,030 for the host
  builds).
- Block RAM: Vivado builds the 16,384 × 72 capture buffer from 36 RAMB36
  (16K × 2 each) instead of Yosys' 32 (4K × 9), and the Urbana's 32,768 × 72
  buffer from 72 (32K × 1) instead of 64, which uses 72 of the xc7s50's 75
  block RAM tiles. The Vivado placement stays within the xc7a35t's 50 block
  RAMs.

**Where Vivado differs from the open-source flow.**

- **The core clock constraint.** The shared clock XDCs define the core clock
  twice: on the board oscillator (or `host_clk`) port, and on the net `clk`
  after the MMCM and BUFG, because nextpnr-xilinx needs the latter. Vivado
  accepts both, but its methodology check reports a critical warning for
  the net clock in every build (job 24149331): TIMING-2 (a primary clock
  created on the BUFG output) and TIMING-4 (a primary clock defined
  downstream of the MMCM's generated clock, which overrides its insertion
  delay). `build.tcl` therefore reads only the port clocks by default
  (`CLOCKS=derived`): Vivado derives the core clock through the MMCM or BUFG
  from the port clock, including the MMCM's jitter. `CLOCKS=shared` reads
  the XDC as is. For the builds run both ways, WNS differed by at most
  0.18 ns between the modes, and every run met timing in both.
- **Warnings that remain** (no critical warnings, no DRC errors):
  - DRC REQP-1839 on the Cmod A7 bridge builds: Vivado's block-RAM power
    optimisation gates the read-port enable of the capture buffer with the
    board reset synchroniser, which has an asynchronous reset; an
    asynchronous reset may then corrupt a read in progress. It affects only
    read-back while the board is being reset (button or MMCM lock). On the
    "Next rebuild" list.
  - Methodology LUTAR-1: the board reset `btn[0] | !locked` is a LUT that
    drives the asynchronous reset of the reset synchroniser; a glitch of that
    LUT could reset the board logic. On the "Next rebuild" list.
  - Methodology TIMING-18 (host builds): missing input/output delays on the
    pin-host port, which is not constrained in either flow ("Limitations").
- **Configuration voltage.** `build.tcl` writes a Vivado-only XDC with
  `CFGBVS VCCO` and `CONFIG_VOLTAGE 3.3` for Vivado's DRC.
- **Memory inference, synthesis options.** Vivado infers its own
  distributed RAM for the SRAM stand-in and its own block-RAM arrangement
  for the capture buffer (above); the SRAM stand-in's equivalence proof
  (`fpga/formal/`) covers the RTL and Yosys' netlist, not Vivado's. The
  Yosys options and nextpnr seeds of the open-source builds have no Vivado
  counterpart.
- **Primitives.** The MMCM (`MMCME2_ADV`), `BUFG`, `ODDR` and the tri-state
  pads (`IOBUF` from `T = ~uio_oe`) are the same primitives in both flows;
  the MMCM counter settings come from the same RTL parameters.

**Bitstreams.** `$PE_WORK/fpga/bitstreams/vivado-2025.2-2026-09-27/`, with
each build's `vivado_timing.txt`, reports and `SHA256SUMS`. They have the
same file names as the openXC7 sets. They are Vivado's output from the same
RTL and constraints; Vivado's netlists have not been simulated (the
post-synthesis simulations under "Verification" are of Yosys' netlists).

| File | Bytes | SHA-256 |
|---|---|---|
| `pe_cmod_a7_pll50.bit` | 2,192,138 | `44caece537a8d9f13e1aebb40f61382dc1620c122b4e5d107dbbcdf35597768e` |
| `pe_cmod_a7_pll50_bridgeonly.bit` | 2,192,138 | `25c5ebf4124a18ffc28611ee144c5abb6c6b91377362901cc6818cd7e733504d` |
| `pe_cmod_a7_pll40.bit` | 2,192,138 | `9e71d49d06910bab10e9a0122fda61d9bbca36cc87e04fe92315751a0a29315f` |
| `pe_cmod_a7_osc12.bit` | 2,192,138 | `2919824b9331b17007ea6486a31d0052f8f168251b73552e9b2d9c7a030f365a` |
| `pe_urbana_pll50.bit` | 2,192,137 | `b5c91cce6b7d51169826cfdddd37df19bc200cbf3a2367b679d3170c4d88af13` |
| `pe_cmod_a7_host.bit` | 2,192,138 | `170ad2da4a02a27c019f3ca313130ffe2c9fec0bf0014eb27a85dd7353433070` |
| `pe_urbana_host.bit` | 2,192,137 | `388ac54a7a9baec2a41caaebf8827fe71796ed0d745b3d4698e48702fd638be7` |

**Which set to program first.** The Vivado set, for the first bring-up: its
timing is signed off by the vendor's analysis, and its bitstreams are
generated by the vendor's tool, so neither prjxray's block-RAM
configuration bits nor the question of block RAMs outside the xc7a35t's 50
applies ("Limitations"). The openXC7 set (`v3-2026-09-27-scope/`) is the
fully open-source alternative, with post-synthesis simulations of its
netlists. Both are built from the same RTL; if a step fails with one set,
repeating it with the other separates a tool problem from a design problem.

## Programming

[openFPGALoader](https://github.com/trabucayre/openFPGALoader) writes to the
FPGA's configuration SRAM, so the image is lost at power-off:

```sh
openFPGALoader -b cmoda7_35t pe_cmod_a7_pll50_bridgeonly.bit
openFPGALoader -b arty_s7_50 pe_urbana_pll50.bit
```

The capture-unit bitstreams have the same file names in their own
directories (`vivado-2025.2-2026-09-27/`, `v3-2026-09-27-scope/`);
`fpga/scripts/bringup.sh` programs the right one and runs the bring-up
("First hour with a board").

The Urbana's USB-JTAG is an FT2232H channel A, like Digilent boards. 6.205
documents `-b arty_s7_50` for Urbana bitstreams
([6.205 openFPGA page](https://fpga.mit.edu/6205/F24/documentation/openFPGA)).
Add `-f` to write the SPI flash instead, which survives power cycles.
openFPGALoader loads its own SPI bridge for that.

On Linux, install the udev rules from the openFPGALoader repository, or run
it as root, before first use.

## First hour with a board

This is the bring-up kit for the capture-unit bitstreams: one command per
board programs the FPGA, tests the board, the pins and the capture unit, and
then runs the self-measured timing-isolation experiment. Nothing here has run
on hardware yet. The whole sequence has run against the simulated board top
(`fpga/sim/test_fpga_bringup.py`, see "Verification"), which is where the
expected output below comes from.

**What you need.**

- The board and its USB cable. On the Urbana, all switches down.
- Two jumper wires (female-female for the Cmod A7's Pmod header). They go on
  only when the script asks for them.
- A PC with Python 3.8 or later, `pip install -r fpga/host/requirements.txt`
  (pyserial 3.5), and openFPGALoader. The recorded tool is openFPGALoader
  v1.1.1 from OSS CAD Suite 20260729.
- The capture-unit bitstreams, copied to the PC with their `SHA256SUMS`:
  the Vivado set `$PE_WORK/fpga/bitstreams/vivado-2025.2-2026-09-27/`
  (first choice, see "Vivado sign-off flow") and the openXC7 set
  `$PE_WORK/fpga/bitstreams/v3-2026-09-27-scope/` (the fully open-source
  alternative). The file names are the same in both.
- On Linux: openFPGALoader's udev rules (or root), and permission to open
  the serial port (for example the `dialout` group). Each board enumerates
  two serial ports; the UART is the second one, `/dev/ttyUSB1` if no other
  USB serial device is present.

**One command per board.**

```sh
PE_BITSTREAMS=/path/to/vivado-2025.2-2026-09-27 fpga/scripts/bringup.sh cmod_a7 /dev/ttyUSB1
PE_BITSTREAMS=/path/to/vivado-2025.2-2026-09-27 fpga/scripts/bringup.sh urbana  /dev/ttyUSB1
# the same with the openXC7 set:
PE_BITSTREAMS=/path/to/v3-2026-09-27-scope fpga/scripts/bringup.sh cmod_a7 /dev/ttyUSB1
```

`bringup.sh` prints the tool versions, checks the bitstream against
`SHA256SUMS`, runs `openFPGALoader -b cmoda7_35t --detect` (or
`-b arty_s7_50`, the board name 6.205 documents for the Urbana), programs
`pe_cmod_a7_pll50.bit` (or `pe_urbana_pll50.bit`) into configuration SRAM,
and starts `fpga/host/bringup.py`. The steps and what each one needs:

| Step | Needs | Checks | Passes with |
|---|---|---|---|
| 1 link | USB only | `V` names the board, the clock and the capture unit; the core clock measured against the PC clock over 1 s is within 2 % of nominal; capture unit idle | `[link] PASS` |
| 2 selftest | nothing on the protocol Pmod | `pe_host.py selftest`: ISA version, idle engines, timestamp against the bridge counter, pads drive `a5`/`5a`, pull-ups, open drain | `SELFTEST PASS` |
| (prompt) | – | fit the two jumpers: Cmod A7 Pmod JA pin 1 to pin 2 and pin 4 to pin 7; Urbana PMOD A pin 1 to pin 2 and pin 4 to pin 7; then press Enter | – |
| 3 loopback | the jumpers | UART TX → jumper → UART RX → route → SPI → jumper → SPI RX | `LOOPBACK PASS` |
| 4 capture | the jumpers (unused here) | stamps against the bridge counter and two markers; the timing probe in pad view and in core view, 4,096 records each, every frame equal to the static prediction; pad and core view at the same frame phase; one capture that fills the whole buffer (16,384 or 32,768 records, every RAMB36 of it), read back and checked the same way; the overflow path | `[capture] PASS` |
| 5 isolation | the jumpers | the self-measured experiment ([demo.md](demo.md), "Experiment C"): 4 captures of a full buffer per phase, idle and loaded; verdict | `NON-INTERFERENCE PASS` |

The run ends with a table of the five steps and `BRING-UP PASS` or
`BRING-UP FAIL`, and it stops at the first failing step. Everything goes to
`bringup-<board>-<date>/`: `bringup.json` (each step's result), the capture
files (`*.json`, and `*.vcd` for GTKWave or `pe_capture.py`), the analyses and
`isolation/verdict.json`. After a failure, fix the cause and resume, for
example with `PE_SKIP_PROGRAM=1 fpga/scripts/bringup.sh cmod_a7 /dev/ttyUSB1
--skip selftest --yes` once the jumpers are on.

**Expected output.** This is the transcript of the simulated Cmod A7 `osc12`
board (job 24140400). Three things differ on a board: the capture sizes
were reduced to keep the simulated UART read-back short (256 records per
check capture, one marker, 512 records for the whole-buffer capture, one
capture of 1,024 records per phase), so on a board the capture lines show
4,096 records, two markers, the whole buffer (16,384 or 32,768 records) and
four full buffers per phase, with frame counts to match; the clock line shows a value
measured over 1 s of PC time, near 50 MHz for the `pll50` bitstreams; and
the numbers of host operations depend on the USB latency. The side-by-side
histograms that `pe_capture.py compare` prints before the load-evidence
table are omitted here (they are in `isolation/compare.txt`). The PASS lines
are the same.

```text
[link]
bridge: bridge protocol 2, board Cmod A7-35T, clock osc12 (12 MHz oscillator), 12 MHz, 1 Mbaud, capture unit
core clock: 12.000 MHz measured over 0.00206 s (nominal 12 MHz)
capture unit v1: state idle, 0/16384 records (depth 16384), lost 0, enable -, trigger -, pad view of uio
[link] PASS
[selftest]
ISA version 2
4 engines idle after reset
timestamp advances with the core clock (3192 cycles, bounds [732, 5652])
pads drive a5 and read back ff when released
pads drive 5a and read back ff when released
open-drain pin pulls low and releases
SELFTEST PASS
[selftest] PASS
>>> Fit the two jumper wires: Pmod JA pin 1 to pin 2 (uio0-uio1) and pin 4 to pin 7 (uio3-uio4). Nothing else on the Pmod.
>>> (simulation: the testbench closes both jumpers)
[loopback]
loaded uart-tx on engine 0
loaded uart-rx on engine 1
loaded spi-controller-mode0 on engine 2
engine 2 received b'FPGA OK!' over SPI
LOOPBACK PASS
[loopback] PASS
[capture]
stamps: start 205385 between bridge counter readings 203569..205621 (mod 2^32); markers: 1, every 2097152 cycles
check-pad: 256 records, 2102 cycles, frames 5/5 identical to the prediction, jitter 0 cycles
check-core: 256 records, 2095 cycles, frames 5/5 identical to the prediction, jitter 0 cycles
pad and core views: same frame phase (the pad path settles within one clock period)
check-full: 512 records, 4177 cycles, frames 11/11 identical to the prediction, jitter 0 cycles
whole buffer: 512 of 16384 records written and read back (limit 512 in this run)
overflow: state full, 64 records, end record last, 45107 changes counted as lost after it
[capture] PASS
[isolation]
--- idle phase
ISA version 2
loaded timing-probe, uart-tx, uart-rx, spi-controller-mode0
probe started on engine 3 (idle mode)
capture 0: 1024 records, 8379 cycles, buffer/limit full, 2423 changes after the end
--- loaded phase
ISA version 2
loaded timing-probe, uart-tx, uart-rx, spi-controller-mode0
probe started on engine 3 (loaded mode)
capture 0: 1024 records, 8378 cycles, buffer/limit full, 6956 changes after the end
loaded: 16 host operations, 0 engine restarts, UART engine completed 58288 instructions

load evidence (activity counters inside the capture windows, edges):
  phase        cycles    uio0    uio1    uio2    uio3    uio4     ui4     ui5
  idle           8379       0       0       0       0       0       0       0
  loaded         8378      72      72     209      61      61     144      54

NON-INTERFERENCE PASS: engine 3's pin edges are cycle-identical to the prediction in every phase (idle 23, loaded 23 complete frames; jitter idle 0, loaded 0 cycles peak-to-peak)
[isolation] PASS

  link       PASS
  selftest   PASS
  loopback   PASS
  capture    PASS
  isolation  PASS
BRING-UP PASS
```

**If a step fails.**

| Symptom | Likely cause | What to do |
|---|---|---|
| openFPGALoader finds no cable | udev rules, charge-only cable, hub | `openFPGALoader --scan-usb`; install the udev rules; another cable or port |
| `--detect` shows another part | wrong `-b` board | `cmoda7_35t` for the Cmod A7-35T, `arty_s7_50` for the Urbana |
| `no reply to 'V'` | wrong serial port, or the board is not configured | use the board's second port (`ls /dev/ttyUSB*` before and after plugging in); the heartbeat LED must blink (Cmod A7 `led[0]`, Urbana LED0) |
| `bitstream is for board id ...` | the other board's bitstream | program the matching file |
| `bridge protocol 1` | a bitstream without the capture unit | use the `vivado-2025.2-2026-09-27` or `v3-2026-09-27-scope` set |
| core clock not within 2 % | MMCM not locked or misconfigured | Cmod A7: `PE_BITSTREAM=.../pe_cmod_a7_osc12.bit` (no MMCM); the rest of the sequence works at 12 MHz |
| selftest: released pads not `ff` | something on the protocol Pmod | remove the jumpers for step 2 |
| loopback fails | jumpers on the wrong pins | pins 1–2 and 4–7 of the protocol Pmod (uio0–uio1, uio3–uio4) |
| capture: pad and core view differ in phase | the pad input takes more than one clock period | report it with the files of `bringup-*/`; the core view and the `pll40` or `osc12` bitstream are unaffected |
| capture: frames differ from the prediction | unexpected; the probe's timing is static | keep the output directory and report it |
| capture: the whole-buffer capture fails (CRC or decode errors, wrong records) while the 4,096-record captures pass | a block RAM beyond the first 4,096 records misconfigured or not usable (see "Limitations") | keep the output directory and report it; a rebuild with a smaller buffer (`VERILOG_DEFINES=-DPE_SCOPE_AW=12`, 4,096 records) uses fewer block RAMs |
| isolation: `loaded windows show no load` | jumpers missing, or no host operation fell into a window | check the jumpers; see "Host traffic density" below |
| isolation: `NON-INTERFERENCE FAIL` with differing frames | the property the experiment tests does not hold on this board | keep the output directory: `compare.txt` and the capture files show which edges moved |
| `CRC mismatch` messages | USB errors during read-back | the client repeats the block; many retries point at the cable or hub |
| a step fails with one bitstream set and passes with the other | a toolchain problem (synthesis, placement, bitstream generation) rather than the design | keep both output directories and report it |

**Host traffic density.** In the loaded phase the host's operations reach the
core through USB round trips. Each capture's ARM command is sent in the same
USB write as 12 host-port frames, so those always execute inside the
window; the sequential operations that follow are as frequent as the USB
latency allows. On Linux the FTDI driver's latency timer (16 ms by default)
dominates; `bringup.sh` prints it, and
`echo 1 | sudo tee /sys/bus/usb-serial/devices/ttyUSB1/latency_timer` sets
1 ms. The load-evidence table of step 5 shows how many host strobes
(`ui4`) each phase's windows actually contained.

## Hardware test procedure

The steps below are the individual tests behind the bring-up kit, for the
2026-09-26 bitstreams (no capture unit) or for running one test on its own.
Each step lists what to connect, the command to run and what a pass looks
like. The messages quoted below are the ones `pe_host.py` prints.
`fpga/sim/test_fpga_bridge.py` runs the same `selftest` and `loopback` code
against the simulated board, and it passes (job ids in "Verification"). On
hardware, only the measured clock and the timestamp numbers should differ.

### 1. Bring-up, nothing connected (UART bridge, pll50 build)

Connect only the USB cable, and leave the protocol Pmod empty. On the Urbana,
all switches must be down.

```sh
openFPGALoader -b cmoda7_35t pe_cmod_a7_pll50_bridgeonly.bit   # or: -b arty_s7_50 pe_urbana_pll50.bit
python3 fpga/host/pe_host.py --port /dev/ttyUSB1 info
python3 fpga/host/pe_host.py --port /dev/ttyUSB1 selftest
```

**Pass criteria:**

- The heartbeat LED blinks.
- `info` prints
  `bridge: bridge protocol 1, board Cmod A7-35T, clock pll50 (MMCM, board oscillator), 50 MHz, 1 Mbaud`,
  followed by `core clock: 50.0xx MHz measured ...` and `ISA version 2`.
  A measured clock far from 50 MHz means the MMCM is not doing what it
  should; try `pe_cmod_a7_osc12.bit`, whose `info` must report 12 MHz.
- `selftest` ends with `SELFTEST PASS`. It checks:
  - the ISA version;
  - all four engines idle after reset;
  - that the core timestamp advances at exactly the bridge's cycle rate;
  - that all eight pads drive `a5`/`5a` and read back;
  - that released pads read `ff` through the pull-ups;
  - that an open-drain pin pulls low and releases.

**If it fails:**

- **No reply at all**: wrong serial port, bitstream or baud rate.
- **Released pads not `ff`**: something is connected to the Pmod.

### 2. Loopback, two jumper wires

Fit two jumpers on the protocol Pmod:

- uio[0] ↔ uio[1]: Cmod JA pin 1 ↔ 2, or Urbana PMOD A pin 1 ↔ 2.
- uio[3] ↔ uio[4]: Cmod JA pin 4 ↔ 7, or Urbana PMOD A pin 4 ↔ 7.

Then run:

```sh
python3 fpga/host/pe_host.py --port /dev/ttyUSB1 loopback
```

The test runs this chain:

1. `uart-tx` on engine 0 sends `FPGA OK!` at 781.25 kbaud on pin 0.
2. `uart-rx` on engine 1 receives it on pin 1.
3. The autonomous route forwards each byte to engine 2's TX FIFO, with no
   host involvement.
4. `spi-controller-mode0` shifts the bytes out on MOSI (pin 3) and reads MISO
   (pin 4) through the jumper.

**Pass:** `engine 2 received b'FPGA OK!' over SPI`, then `LOOPBACK PASS`.
The UART receiver ends with its bounded idle timeout (fault 3) after the last
byte; the test expects that. Without the jumpers the test fails, and
`fpga/sim` checks that it does.

### 3. Real peripherals

Load the firmware for each engine with
`pe_host.py --port ... load <name>` (see `firmware/`). Then wire the
peripherals as in `firmware/flagship-scenario.json` (`pin_connections`):

- pin 0: UART TX
- pin 1: UART RX
- pins 2–5: SPI SCK, MOSI, MISO and CS
- pins 6–7: I²C SCL and SDA, with 4.7 kΩ pull-ups to 3.3 V

For bench runs, use `pe_host.Chip` from Python in the order of
`test/scenarios.py`:

1. Load the images.
2. `push_tx`.
3. `route`.
4. `command(START, mask)`.
5. `pop_rx`.

Capture the pins with a logic analyser and decode them with its UART, SPI and
I²C decoders.

### 4. Pico host (host-clock build)

Program `pe_cmod_a7_host.bit` and wire the Pico as in "Pin host". Copy
`fpga/host/pico_host.py` and the three image JSON files to the Pico. Then run:

```python
import json, pico_host
imgs = {n: json.load(open(n + ".image.json")) for n in ("uart-tx", "uart-rx", "spi-controller-mode0")}
host = pico_host.NibbleHost(pico_host.PicoPort())
host.reset(); print(host.status(pico_host.RS_VERSION))    # 2
pico_host.loopback(host, imgs)                             # jumpers as in step 2
```

**Pass:** `2`, then `LOOPBACK PASS`. The UART runs at the Pico's clock rate
divided by 64.

## Verification

### Checks of the capture-unit tree (2026-09-27)

These ran on MIT Engaging through Slurm on 2026-09-27, from a snapshot of
this tree (`src/` unchanged: `protocol_emulator_core.v` SHA-256
`26a873db…`; the RTL, constraints and bitstreams of the release come from
the same snapshot). Job ids and results are in `$PE_WORK/fpga-scope/manifest.json`.
Tools: cocotb 2.0.1, Icarus Verilog 14.0 (devel), Yosys 0.67+111,
openXC7 2026-09-24.

**Capture unit in the full board top, RTL** (`fpga/sim/test_fpga_scope.py`,
UART-bridge testbench driven only through its USB-UART by `pe_host.py` and
`pe_scope.py`). An independent monitor in the testbench samples the channel
sources after every rising clock edge; each test compares the host tool's
reconstructed edges with the monitor's, edge for edge and cycle for cycle.

| Test | What it checks | Cmod A7 `pll50` | Urbana `pll50` (single-port buffer) | Cmod A7 `osc12` | Job |
|---|---|---|---|---|---|
| `test_scope_basics` | `V` = protocol 2; idle status, depth (16,384 / 32,768); empty read-back; forced trigger (start and end records only); capture commands while the pin host owns the port; stamp origin: the stamp register after the n-th clock edge since t = 0 is n − 2 | PASS | PASS | PASS | 24125728 |
| `test_scope_probe_pad_and_core` | timing probe, pad view (trigger on the probe pins) and core view, 256 records each: all edges equal the monitor's; start record holds the trigger edge; both views at the same frame phase | PASS | PASS | PASS | 24125728 |
| `test_scope_overflow_and_halt` | 24-record limit: FULL, end record last, lost count growing until the halt and frozen after it, records still equal the monitor's | PASS | PASS | PASS | 24125728 |
| `test_scope_readback_while_recording` | 64 records read back while the probe keeps writing, then again after the halt: identical; on the single-port buffer the responder repeated 6 reads that fell on write cycles, on the dual-port buffer none | PASS | PASS | PASS | 24125728 |
| `test_scope_host_port_channels` | `ui_in` / `uo_out` channels during a status read, triggered on write-valid: equal to the monitor's; activity counter = recorded edges | PASS | PASS | PASS | 24125728 |
| `test_scope_markers_and_wrap` | no channel enabled, cycles 1,959 to 6,362,900: start record, markers at 2,097,152, 4,194,304 and 6,291,456 (the multiples of 2^21), end record; the capture crosses the 22-bit stamp field's wrap at 2^22 = 4,194,304; start stamp between two `C` readings (143 and 2,195) | – | – | PASS | 24125728 |

**Regressions with the capture unit in the tree** (job 24125728): UART bridge
tests (`test_fpga_bridge.py`, now expecting protocol 2) 5/5 on Cmod A7
`pll50`, Urbana `pll50` and Cmod A7 `BRIDGE_ONLY=1`; `test_smoke` and
`test_flagship` of `test/` on the pin-host tops (Cmod A7 `host` build, Urbana
`pll50` build in pin-host mode) 4/4 each. The same tests had passed on earlier
texts of the tree (jobs 24120568, 24120869, 24121750).

**Bring-up end to end** (`fpga/sim/test_fpga_bringup.py`): `bringup.py` runs
all five steps against the simulated board, with the jumper prompt answered
by closing the testbench's jumpers and time taken from the simulator
(job 24125728): Cmod A7 `osc12` top, `BRING-UP PASS` in 493 s of wall time;
Urbana `pll50` top, `BRING-UP PASS` in 1,420 s. The transcript under "First
hour with a board" is the Cmod A7 run of the re-run after review (job
24140400, below), which adds the whole-buffer check.

**Post-synthesis simulation of the release netlists.** Each
`synth_netlist.v` below is the netlist of a published 2026-09-27 bitstream,
simulated with Yosys' `cells_sim.v` and `fpga/sim/netlist_prims.v` (MMCM,
ODDR). Yosys' `RAMB36E1` model has timing only and drives no data (the first
run, job 24126758, read back `x`), so `fpga/sim/Makefile` replaces it with
`fpga/sim/netlist_bram.v`: a behavioural model of exactly the configuration
Yosys emits for the capture buffer (true dual-port mode used as write on A,
read on B, or port A only; 4,096 × 9; READ_FIRST; no output register), which
stops the simulation on any other configuration. It checks Yosys' mapping
(address slices, data and parity bits, bank multiplexing); it is not AMD's
unisim model. In netlist mode the monitor sees the pads and the kept
`uio_out`/`uio_oe` wires only, so `test_scope_host_port_channels` is skipped,
and the captures of `test_scope_probe_pad_and_core` hold 128 or 64 records.

| Netlist | Tests | Result | Job |
|---|---|---|---|
| Cmod A7 `osc12` | `test_fpga_scope` (128-record captures) | 4/4 PASS | 24127217 |
| Cmod A7 `pll50` | `test_fpga_scope` (64-record captures); `test_fpga_bridge` | 4/4 PASS; 5/5 PASS | 24130995; 24127217 |
| Cmod A7 `pll50` + `BRIDGE_ONLY=1` | `test_fpga_scope` (64); `test_fpga_bridge` | 4/4 PASS; 5/5 PASS | 24130995; 24127217 |
| Cmod A7 `pll40` | `test_fpga_scope` (64) | 4/4 PASS | 24130995 |
| Urbana `pll50` (single-port buffer) | `test_fpga_scope` (64); `test_fpga_bridge` | 4/4 PASS; 5/5 PASS | 24130995; 24127217 |
| Cmod A7 `host`, Urbana `host` | `test_smoke`, `test_flagship` of `test/` | 4/4 PASS each | 24126758 |

A netlist simulation of the 40 and 50 MHz builds took 1.1–1.7 hours per
configuration on one CPU.

**Without a simulator** (job 24140487): `fpga/host/test_pe_scope.py` 11/11
(record format and decoder invariants, 22-bit wraps, CRC-16 check value
0x29B1, CRC retry and failure, a corrupted read-back count one too high or
too low and an out-of-range status reply each detected, drained and
repeated, VCD read back by `pe_capture.py` as a cycle-domain capture, frames
same and one-cycle shift different); `demo/tests` 27 tests, OK (3 optional
tests skipped: `MICROPYTHON`, `ASSEMBLER`, `SIGROK_CLI` not set), including
`test_scope_adapter.py` (the self-measured flow against a capture-unit
stand-in on the reference model: PASS; a run without load evidence must not
pass; a CRC error is retried); `fpga/vivado/test_vivado_flow.py` 10/10 (Tcl
flow under `tclsh` with stubbed Vivado commands: sources, defines, clock
XDC modes, the verdict with `check_timing`, no bitstream on FAIL, the
`PE_SCOPE_AW` limit in `build.tcl` and `build.sh`; report parser), and
13/13 after the changes made with the Vivado runs (derived clocks by default,
methodology and DRC severities in the verdict; login node);
`fpga/host/test_pico_host.py` PASS.

**After review.** An independent review of this work led to host-side and
documentation changes only (read-back validation and retries in
`pe_scope.py`, the whole-buffer check in `bringup.py`, the Vivado verdict,
the `PE_SCOPE_AW` check); the RTL, and so the released bitstreams, are
unchanged. The RTL-level tests were run again on the changed host code
(job 24140400): `test_fpga_scope` 5/5 on Cmod A7 `pll50`, Urbana `pll50`
and Cmod A7 `osc12`, `test_fpga_bridge` 5/5 on Cmod A7 `pll50`, and the
bring-up end to end with the whole-buffer check (Cmod A7 `osc12` 1,055 s, Urbana `pll50` 3,314 s of wall time, both `BRING-UP PASS`); the self-measured matrix
(job 24140401) gave the same 9 verdicts and numbers as job 24125844.

**Self-measured isolation experiment** (demo/sim, 9 cases, job 24125844):
see [demo.md](demo.md), "Self-measured isolation in simulation".

### Checks of the 2026-09-26 bitstreams

These ran on MIT Engaging through Slurm on 2026-09-26, with `test/` and
`firmware/` from a `git archive` of commit be7dbda and this tree's `src/` and
`fpga/`. The RTL of `fpga/rtl/` is unchanged since the 2026-09-25 checks; the
synthesis options are new, so the SRAM mapping proof and the post-synthesis
simulations were run again on the new netlists. Job ids and results are in
`$PE_WORK/fpga-opt/manifest.json`. Tools: cocotb 2.0.1, Icarus Verilog 14,
Yosys 0.67, SymbiYosys.

**SRAM stand-in, mapped with the new synthesis options.** `fpga/formal/run.sh`
maps the SRAM with the build's `SYNTH_OPTS`, `ABC9_W` and `ABC9_SCRIPT`. Both
option sets of the published builds give the same mapping as before
(16 RAM64X1S, 16 FDRE, 16 LUT6, 1 LUT2):

| Options | `netlist_prove` (ABC PDR, unbounded) | `netlist_bmc` (ABC bmc3, depth 12) | Job |
|---|---|---|---|
| `ABC9_SCRIPT=flow3mfs ABC9_W=1000` | PASS | PASS | 23993635 |
| the same + `SYNTH_OPTS=-nowidelut` | PASS | PASS | 23993636 |

The RTL-level proofs do not depend on synthesis options. With the updated
`run.sh` in its default mode, `rtl_prove`, `netlist_prove`, the four mutants
(all killed) and `rtl_cover` pass again (job 23999245); `rtl_bmc` and the
other 2026-09-25 results below still apply.

**Full FPGA top, RTL, current `test/` suite** (job 23993138). The `test/`
suite of be7dbda has 102 tests in 20 modules. On each of the four
configurations (Cmod A7 and Urbana; `host` build and `pll50` build in
pin-host mode) 91 pass and the same 11 fail. None of the 11 can run on a
board-level top:

- 10 reach into the Tiny Tapeout testbench's design instance
  (`tb.user_project`): the 7 tests of `test_timewarp`, which move internal
  counters forward, and `test_kill_exact_timeout_limits`,
  `test_kill_exact_timeout_waitevent_limits` (`test_kill_blocked`) and
  `test_kill_time_high` (`test_kill_regs`);
- `test_kill_pin_matrix` drives `uio_in` against pins the design itself
  drives; on a real pad the driven value wins, so the core reads a different
  value than the reference model assumes.

The runs below exclude exactly these 11 tests (`COCOTB_TEST_FILTER`; job
23994714 checks that the filter removes `test_kill_pin_matrix` and nothing
else from `test_kill_pins`).

**Post-synthesis simulation of the final netlists.** Each `synth_netlist.v`
below is the netlist of the published bitstream (see "Build results"),
simulated with Yosys' `cells_sim.v` plus `fpga/sim/netlist_prims.v` (MMCM and
ODDR models for the `pll50`, `pll40` and `osc12` builds, which could not be
simulated after synthesis before):

| Netlist | Suite | Result | Job |
|---|---|---|---|
| Cmod A7 `pll50` (pin-host mode) | the 91 tests | 91/91 PASS | 23994813 |
| Cmod A7 `pll40` (pin-host mode) | the 91 tests | 91/91 PASS | 23994813 |
| Cmod A7 `osc12` (pin-host mode) | the 91 tests | 91/91 PASS | 23994813 |
| Cmod A7 `host` | the 91 tests | 91/91 PASS | 23994813 |
| Urbana `pll50` (pin-host mode) | the 91 tests | 91/91 PASS | 23994813 |
| Urbana `host` | the 91 tests | 91/91 PASS | 23994813 |
| Cmod A7 `pll50` + `BRIDGE_ONLY=1` | UART bridge tests (`test_fpga_bridge.py`) | 5/5 PASS | 23994813 |
| Cmod A7 `pll50`, Urbana `pll50` | UART bridge tests | 5/5 PASS each | 23994846 |
| Cmod A7 `host`, Urbana `host` | Pico driver (`test_fpga_pico.py`) | 1/1 PASS each | 23994846 |

**RTL, bridge and Pico checks on this tree** (job 23994846): UART bridge
tests 5/5 on Cmod A7 `pll50`, Urbana `pll50` and Cmod A7 `BRIDGE_ONLY=1`;
Pico driver 1/1 on both `host` tops; Pico driver against the reference model
PASS; the pad-fault checker self-test fails as required.

Each `synth_netlist.v` simulated above is byte-identical to the one written
by the release rebuild of the published bitstream (job 23998703); the
netlists do not depend on the placer seed or the router.

### Checks of the 2026-09-25 bitstreams

Everything in this subsection ran on MIT Engaging through Slurm on 2026-09-25.

- Job ids and results are in
  `$PE_WORK/fpga/manifest.json`, and logs are
  under `.../tt-work/fpga/logs/`.
- The simulations used this tree's `src/` and `fpga/`.
- They took `test/` and `firmware/` from a `git archive` of commit 73536f0
  (`PE_TEST_DIR`, `PE_FIRMWARE_DIR`), because those directories were being
  edited in parallel.
- Tools: cocotb 2.0.1, Icarus Verilog 14, Yosys 0.67, SymbiYosys.

**SRAM stand-in ≡ IHP model** (`fpga/formal/run.sh`, `sram_equiv.sby`,
`sram_equiv_miter.sv`).

Inputs, SHA-256:

- `pe_fpga_sram_64x16.v`: `10d1d068…`
- `RM_IHPSG13_1P_core_behavioral.v`: `5aa61ac4…`

The miter drives both memories with free inputs every cycle. It compares
`A_DOUT` whenever the IHP model's output is defined by the history: last
loaded from a written address, or by write-through. The IHP model's power-up
state is left free.

| Task | Engine | Checks | Result | Job |
|---|---|---|---|---|
| `rtl_prove` | ABC PDR, unbounded | FPGA RTL ≡ `SRAM_1P_behavioral #(16,6)` | PASS | 23756607 |
| `netlist_prove` | ABC PDR, unbounded | the `synth_xilinx` netlist (16 RAM64X1S, 16 FDRE, 17 LUTs) with Yosys' Xilinx cell models ≡ IHP model | PASS | 23756607 |
| `rtl_bmc`, `netlist_bmc` | ABC bmc3, depth 24 / 12 | same property, second engine | PASS, PASS | 23756608 |
| `rtl_cover` | smtbmc (yices) | write-through, write-hold, registered read and `men`-low hold each reached with the checked output | PASS | 23756607 |
| mutants | ABC bmc3, depth 8 | four wrong models must fail: write updates dout (block-RAM `WRITE_FIRST`), read-first write-through, dout not held, `men` ignored | all 4 killed | 23756607 |
| `rtl_smt` | smtbmc (yices), depth 12 | third engine | no failure through step 10; still in step 11 after 25 min when this was written (the SMT array encoding is slow here; PDR above is the unbounded result) | 23760933 |

The `netlist_prove` result covers the SRAM module synthesized on its own.
Inside the full design, the same mapping is covered by the post-synthesis
simulations below.

**Full FPGA top, RTL simulation.** `fpga/sim/tb_fpga_pins.v` gives the board
top the Tiny Tapeout port names. The unchanged `test/` suite then checks the
FPGA prototype against the Python reference model every cycle:

- lockstep outputs;
- protocol peers and scoreboards.

The FPGA prototype here means the board top, shell, TT top, core and FPGA
SRAM, connected only through the board's pads.

The testbench also checks the pads on every cycle:

- each pad equals `uio_out` when driven, and the environment otherwise;
- the core sees the pad;
- the `uo_out` pins equal the core's.

A violation ends the run with `$fatal`, which fails the test. The final run
(job 23763625) used the final tree:

| Configuration | Suite | Result |
|---|---|---|
| Cmod A7, `host` build | smoke, protocols, flagship, 25 legacy replays, random lockstep | 39/39 PASS |
| Urbana, `host` build | same | 39/39 PASS |
| Cmod A7, `pll50` build, pin-host mode | same | 39/39 PASS |
| Urbana, `pll50` build, pin-host mode | same | 39/39 PASS |
| Cmod A7 and Urbana `pll50`, UART bridge driven by `pe_host.py` (`test_fpga_bridge.py`) | `info`/50 MHz cycle counter, `selftest`, `loopback`, loopback without jumpers must fail, error replies / timeout / host-select refusal | 5/5 PASS each |
| Cmod A7 `pll50` + `BRIDGE_ONLY=1` (job 23764155) | the same bridge tests | 5/5 PASS |
| Cmod A7 and Urbana `host`, Pico driver (`test_fpga_pico.py`; final `pico_host.py`, job 23768231) | host-clocked version check, idle engines, jumper loopback | 1/1 PASS each |
| Pico driver vs reference model (`fpga/host/test_pico_host.py`, job 23768231) | loopback with jumpers passes; without jumpers it fails | PASS |
| Checker self-test: pad 1 shorted to ground (`-DPE_TB_INJECT_PAD_FAULT`) | `test_smoke` | fails as required (`PAD ERROR pin 1 … $fatal`) |

**Post-synthesis simulation.** The `synth_xilinx` netlists of the two `host`
builds were simulated with Yosys' `cells_sim.v` under the same testbench and
the full suite (job 23757655):

- Cmod A7: 39/39 PASS in 577 s.
- Urbana: 39/39 PASS in 540 s.

These are the flattened netlists written by the same synthesis run as the
bitstream (`synth_netlist.v`). The final builds' netlists are byte-identical
to the ones simulated.

Summary of what is and is not covered:

- **Covered:**
  - equivalence of the SRAM replacement;
  - the unchanged `test/` suite on the FPGA top in RTL and after synthesis;
  - the UART bridge and PC host tool;
  - the Pico driver;
  - bit-level bitstream assembly.
- **Not covered:**
  - place-and-route correctness beyond nextpnr's own checks;
  - the MMCM, which is not simulated;
  - I/O timing;
  - anything on hardware.

## Limitations and open items

- **No hardware observations yet.** MMCM lock, pad behaviour, the UART bridge
  at 1 Mbaud, openFPGALoader on the Urbana, the Pico host speed, the capture
  unit and the bring-up kit are all to be confirmed on the boards.
- **Timing.** The openXC7 figures come from nextpnr-xilinx's model
  (prjxray timing data) and are not a vendor sign-off. The 2026-09-27 release
  is also signed off in Vivado 2025.2 ("Vivado sign-off flow"), which covers
  the Vivado bitstreams; Vivado's netlists have not been simulated.
  - The published nextpnr fmax is the fastest of many placer seeds; the seed
    distributions under "Build results" show what the same options give
    typically.
  - No I/O timing is constrained. For a pin host, the margin that matters is
    the host's own setup/hold around `host_clk`, which is large at
    Pico-driven rates. A synchronous pin host at 50 MHz depends on the
    unconstrained pin-to-register and register-to-pin paths; registering the
    pins in the I/O tiles would bound them but adds a cycle each way (see
    "Implementation study").
- **Urbana pin-host limits.** The Urbana pin host uses two servo-header pins
  and exposes only `uo_out[5:0]` on pins. For a fast synchronous external
  host, use the Cmod A7 `pll40` build (all sixteen host signals on the DIP
  header) or the UART bridge.
  - The servo pins' 510 Ω series resistor, and a 10 kΩ resistor of unknown
    direction, were read from the schematic's text. Check them with a meter
    before relying on those two pins.
  - The Urbana Pmod+ pin order (J1 counter-clockwise, JB2 = K14/J15) was
    derived from the schematic netlist and the 6.205 constraints file. It
    should be confirmed with a continuity check on the first board.
- **Capture unit.**
  - The pad view assumes that a pad driven by the FPGA settles within one
    clock period of the launching edge; that path is not timed. The bring-up
    checks compare the pad and core views on the board.
  - A capture window is at most the buffer depth (16,384 records on the Cmod
    A7, 32,768 on the Urbana: about 2.7 ms and 5.4 ms of the timing probe at
    50 MHz). Longer measurements are sequences of captures, with read-back
    gaps between them.
  - The host-clocked `host` builds have no UART and no capture unit.
  - The activity counters and the 48-bit stamps saturate or wrap after 2^32
    changes and 2^48 cycles (65 days at 50 MHz).
  - Externally driven inputs can be stamped ±1 cycle from the core's own
    view of them, and open-drain release edges depend on the pull-up (see
    "On-board capture unit").
- **Block RAM configuration of the openXC7 bitstreams is unverified on
  hardware.** (The Vivado bitstreams are generated by the vendor's tool for
  the actual part, and Vivado places the buffer within the xc7a35t's 50
  block RAMs; this item does not apply to them.) The capture buffer is the
  first block RAM use in these builds. The readback check only shows
  that each bitstream carries the frames of its FASM; it does not show that
  prjxray's block-RAM configuration bits are complete and correct. The
  Spartan-7 part of the prjxray database in this openXC7 release is known to
  lack some of them (the RAMB36 port-B widths, which is why the Urbana buffer
  is single-port), and the others have not been checked against hardware.
  On the Cmod A7, the chipdb is the xc7a50t die: the buffer's 32 RAMB36 are
  placed in columns X6, X30 and X37 of that die's grid, at rows up to Y145.
  prjxray's part description of the xc7a35t lists the same block-RAM
  configuration columns as the xc7a50t, but whether every one of these
  block RAMs works on a 35T part (datasheet: 50 RAMB36) is not documented by
  AMD and has not been tested. Step 4 of the bring-up writes the whole
  buffer once and checks every record read back ("First hour with a board"),
  so a faulty block RAM shows up there, before the isolation experiment; the
  same step with the Vivado bitstream then tells a toolchain problem from a
  design problem.
- **Post-synthesis simulation uses Yosys' Xilinx cell models**
  (`cells_sim.v`), not Xilinx's unisims, and for the capture buffer's block
  RAM a behavioural model written for this project
  (`fpga/sim/netlist_bram.v`), because Yosys' `RAMB36E1` model has no data
  behaviour.
- **The MMCM is not simulated.** Its settings stay within the datasheet
  limits noted in `pe_fpga_clkgen.v`, and the counter values in the final
  FASM match them (see "Build results"). Post-synthesis simulation replaces
  it with a pass-through model (`fpga/sim/netlist_prims.v`).

## Next rebuild

RTL changes found necessary or useful after the 2026-09-27 release. They are
not in its bitstreams, which stay as published; each needs a new build,
sweep, simulation and readback before it replaces them.

1. **Capture unit reply timing.** Register the bridge's `sc_rsp_ready` (or
   the transmit FIFO's "not full" term it uses) and split or duplicate the
   clock enable of the 312-bit reply shift register `sh`. That path is the
   critical path of the published Cmod A7 `pll50` run ("Build results,
   capture-unit release").
2. **Dedicated synchroniser stage.** Give the pad inputs a second
   `ASYNC_REG` flop directly after `pad_s1`, ahead of the pad/core-view
   multiplexer, so an asynchronous external input has a full clock period
   to settle.
3. **Bridge watchdog.** Leave state `S_SCOPE` with an error reply if the
   capture unit sends no byte for a bounded number of cycles.
4. **Depth check at elaboration.** Stop elaboration of `pe_fpga_scope.v`
   for `AW` outside 2..15 (today only the build scripts reject it), or widen
   the status fields for deeper buffers.
5. **CRC on every reply.** Cover the `U` header count and the `Q` and `P`
   replies with the CRC (a bridge protocol change).
6. **Enable view.** A channel mode that records `uio_oe`, so the release
   edges of open-drain pins are stamped at their launching cycle.
7. **Reset of the capture buffer's read port.** Vivado's block-RAM power
   optimisation gates the Cmod A7 buffer's read-port enable with the board
   reset synchroniser, which has an asynchronous reset (DRC REQP-1839). Drive
   the capture unit from a reset without an asynchronous path, or keep the
   buffer's enables out of the reset logic.
8. **Board reset LUT.** `btn[0] | !locked` is a LUT driving the asynchronous
   reset of the reset synchroniser (methodology LUTAR-1); register or
   synchronise the button and the lock signal before they reach it.
9. **Comment.** The header of `pe_fpga_scope.v` says recording starts after
   a 3-cycle settling time. In the RTL the unit is in its settling state for
   4 cycles, and with an immediate start it writes the start record at the
   fifth clock edge after the edge that accepts the `A` command.

