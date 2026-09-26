# Hardware demonstration: procedure, expected results and validation

This page describes three demonstrations of the protocol emulator on the FPGA
prototype ([fpga.md](fpga.md)) and how their results are measured:

1. **Timing isolation, measured.** A logic analyser records one engine's pin
   edges while everything else is idle, and again while the other three
   engines carry traffic and the host issues a stream of host-port
   operations. The formal timing-isolation proof ([formal/README.md](../formal/README.md),
   "Timing-isolation proof") predicts that the two recordings are identical,
   cycle for cycle.
2. **Four protocols at once against real parts:** UART to a USB-UART adapter,
   SPI JEDEC-ID reads from a SPI NOR flash, I2C reads from a temperature
   sensor.
3. **A host-free chain through the mover:** a ticker engine requests I2C
   sensor reads, and the readings pass through a formatting engine to a UART
   engine. After setup, the host does nothing.

The tools are in [`demo/`](../demo/README.md).

**Status (2026-09-25).** No FPGA board or logic analyser has been connected;
nothing on this page is a hardware observation. Every step was run in
simulation on the FPGA top (board top, shell, Tiny Tapeout top, generated
core, FPGA SRAM stand-in), with emulated logic-analyser captures fed through
the same analysis tools a hardware capture goes through. The section
[Validation in simulation](#validation-in-simulation) lists what ran, with
Slurm job ids.

## Contents

1. [What the timing-isolation measurement tests](#what-the-timing-isolation-measurement-tests)
2. [Sampling resolution and the two measurement methods](#sampling-resolution-and-the-two-measurement-methods)
3. [Bill of materials](#bill-of-materials)
4. [Experiment A: host-clocked build, cycles from the clock pin](#experiment-a-host-clocked-build-cycles-from-the-clock-pin)
5. [Experiment B: free-running 12 MHz build, cycles recovered from time](#experiment-b-free-running-12-mhz-build-cycles-recovered-from-time)
6. [Four protocols against real parts](#four-protocols-against-real-parts)
7. [Host-free chain: I2C sensor to UART](#host-free-chain-i2c-sensor-to-uart)
8. [Analysis tool reference](#analysis-tool-reference)
9. [Validation in simulation](#validation-in-simulation)
10. [What the measurement does and does not show](#what-the-measurement-does-and-does-not-show)

## What the timing-isolation measurement tests

The formal property is stated for engine K. Two copies of the complete
processor share the clock, reset and pin inputs, load the same program into
engine K and start, stop and clear engine K on the same cycles. Everything
else is free and independent between the copies: the other engines'
programs and activity, mover routes and transfers, host FIFO traffic
(including to engine K), events, read-back and the host protocol state. Until
engine K moves a word through its own FIFOs or consumes an event, engine K's
owned pins are identical in the two copies on every cycle. The proof is
unbounded (k-induction) for each K = 0..3.

The measurement mirrors this with two runs on one board:

| | idle run | loaded run |
|---|---|---|
| Engine 3 | timing probe, started alone | the same probe, started with the same host sequence |
| Engines 0-2 | loaded, never started | UART TX (engine 0) → jumper → UART RX (engine 1) → mover → SPI controller (engine 2) → MOSI-MISO jumper → mover → UART TX: six bytes circulate without host service |
| Host port | idle after START | a stream of operations (below) |

**The timing probe** (`demo/firmware/timing-probe`, engine 3, pins 6 and 7)
never executes PULL, PUSH, WAITPIN or WAITEVENT, so an entire run stays
inside the window of the formal property. Each 344-cycle frame contains:

- a 12-cycle pulse on pin 6;
- three pulses on pin 7, 5 cycles high and 8 cycles low;
- a fused 16-bit XFER of the constant `0xB4A5`, with pin 6 as the clock and
  pin 7 as the data, and a 7-cycle half period;
- a 40-cycle wait.

That makes 54 edges per frame, with 14 distinct high and low durations
across the two pins (table under Experiment A).
`tools/timing/pe_timing.py` predicts the frame from the image, and
`pe_capture.py predict` turns that prediction into the reference pattern that
every capture is checked against.

**Host traffic in the loaded run** (`pe_demo.Hammer`, seeded). Each
operation is chosen at random from:

- status, level, PC, counter and timestamp reads of all four engines, the
  probe included;
- EVENTs to random engine masks, the probe included;
- writes to the probe's own TX FIFO, which the probe never drains: it fills,
  and later writes stall with write-valid held until the host gives up;
- read attempts on the probe's empty RX FIFO;
- ROUTE re-arms;
- a TRIGGER on the running probe, which the chip rejects (sticky host fault),
  followed by CLEAR of the host fault;
- TX writes to the probe's FIFO abandoned after 1 to 7 nibbles
  (host-clocked build only).

None of these operations starts, stops or clears engine 3, and none issues
BEGIN, COMMIT or OWN while engine 3 is selected. `demo/tests` checks this on
3,000 operations. These are the formal model's environment restrictions.

**What counts as identical.** Every edge of pins 6 and 7 is converted to a
cycle number. The capture is then split into frames at the frame's first
edge. A run is **same** when all of the following hold:

- every complete frame equals the predicted frame: the same 54 edges at the
  same cycle offsets;
- every frame is 344 cycles long;
- from the first frame on, every edge sits exactly on the periodic schedule.

A single edge one cycle early or late makes the run **different**.
`pe_capture.py compare` prints both runs' edge-interval histograms side by
side (counts per frame) and the verdicts.

## Sampling resolution and the two measurement methods

The chip runs at 50 MHz, so one cycle is 20 ns. A common FX2-based analyser
(sigrok `fx2lafw`) samples 8 channels at up to 24 MHz, one sample every
41.7 ns. It cannot place a 50 MHz edge to the cycle. Two methods avoid the
problem.

**A. Count clock edges (host-clocked build).** In the `host` builds the Pico
supplies every clock edge on `host_clk`. The analyser records that pin
together with the probe pins, and an edge's cycle number is the number of
rising clock edges at or before it (`--clock`). The Pico clocks the design at
kHz rates, far below the analyser's rate, so the cycle number is exact. It
does not depend on how irregular the Pico's software timing is. The host
traffic in this mode is dense: the host drives the port on every clock the
design sees. The design does not run at speed, which is a limitation of this
method.

**B. Recover cycles from time (free-running builds).** In the UART-bridge
builds the core clock comes from the board oscillator. Every pin change sits
on that clock's grid, and an analyser at rate fs records it within one sample
after the true edge. When fs is at least 2.2 times the design clock fclk,
`pe_capture.py analyze --fclk` recovers the exact cycle number of every edge:

- it tracks the clock phase from the set of phases consistent with the
  recent edges;
- it estimates the offset between the board's and the analyser's crystals
  from the data;
- afterwards it refits every block of 2,048 edges and checks that the
  residuals fit inside one sample (plus a jitter tolerance). A wrong cycle
  assignment would widen them.

**The fs = 2 fclk alias.** At fs = 2 fclk, a design clock slightly faster
than fs/2 and one slightly slower produce identical sample streams: fclk and
fs − fclk alias to each other. The direction of the phase drift is then
unobservable, and the recovered cycle numbers can slip by one cycle at every
phase wrap while still looking self-consistent. Simulation shows this: at
24 MHz on the 12 MHz build, recovery was wrong but self-consistent in 6 of 12
trials (see [Validation in simulation](#validation-in-simulation)). The tool
therefore refuses fs/fclk < 2.2 unless `--force` is given.

| Build | Core clock | fx2lafw, 24 MHz, 8 channels | Pico analyser (sigrok-pico), 120 MHz, ≤ 4 channels |
|---|---|---|---|
| Cmod A7 `host` + Pico host | host clock, kHz | **A, exact** | A, exact; capture depth limits the run length (below) |
| Cmod A7 `osc12` (UART bridge) | 12 MHz | B refused: fs/fclk = 2.0 (alias) | **B, exact**: fs/fclk = 10 |
| Cmod A7 `pll40` (UART bridge) | 40 MHz | B impossible | B, exact: fs/fclk = 3.0 |
| Cmod A7 `pll50` / Urbana `pll50` | 50 MHz | B impossible | B, exact: fs/fclk = 2.4 |

Without `--clock` or `--fclk`, the tool reports intervals in samples, and
`compare` then only measures a statistical distance between histograms (total
variation). That mode cannot see a single-cycle difference at 50 MHz and is
not used for the verdicts below.

**Pico analyser limits.** These are from the sigrok-pico user guide
(`pico-coder/sigrok-pico`, commit `9b7d144`):

- 120 Msps is available for 1 to 4 channels with at most 400,000 samples
  (3.3 ms at 120 MHz), and for 5 to 7 channels with at most 200,000 samples;
- longer captures stream over USB at much lower rates (500 ksps plus RLE for
  up to 7 channels);
- inputs are 0 to 3.3 V only;
- channels are GPIO 2 to 22 and must be enabled contiguously from D2.

Experiment B therefore takes several 400,000-sample captures during each run.
`pe_capture.py` analyses them together, keeping frames inside their own
capture. At 12 MHz, one capture holds about 116 probe frames.

## Bill of materials

| Item | Used in | Notes |
|---|---|---|
| Digilent Cmod A7-35T | all | Bitstreams `pe_cmod_a7_host.bit`, `pe_cmod_a7_osc12.bit` and `pe_cmod_a7_pll50_bridgeonly.bit` ([fpga.md](fpga.md), "Build results"; stored under `$PE_WORK/fpga/bitstreams/`, not in git). The Urbana `host` and `pll50` builds work for A and for B at 50 MHz; the Urbana has no 12 MHz build. |
| Raspberry Pi Pico or Pico 2, MicroPython | A | Host: runs `demo/pico_demo.py` with `host/pe_host` |
| FX2-based 8-channel logic analyser (sigrok `fx2lafw`) | A, four protocols | 24 MHz, 3.3 V inputs |
| Second Raspberry Pi Pico with the sigrok-pico firmware | B | 120 Msps at up to 4 channels. Alternatively any sigrok-supported analyser with fs ≥ 26.4 MHz for `osc12`, or ≥ 110 MHz for `pll50` |
| 2 jumper wires | A, B | Pmod JA pin 1 to pin 2 (uio0 to uio1), and pin 4 to pin 7 (uio3 to uio4) |
| About 25 Dupont wires, breadboard | all | Pico to Cmod DIP header, analyser leads |
| 3.3 V USB-UART adapter (CP2102, FT232R, CH340 or similar) | four protocols, chain | A separate USB serial port from the board's own |
| 3.3 V SPI NOR flash breakout (W25Qxx or similar) | four protocols | /WP and /HOLD tied high |
| TMP102 or LM75 breakout, 3.3 V, address 0x48 | four protocols, chain | Any target whose register pointer defaults to a readable register; see below |
| 2 × 4.7 kΩ resistors | four protocols, chain | I2C pull-ups to 3.3 V, if the breakout has none |
| PC with Python ≥ 3.8, pyserial, sigrok-cli; openFPGALoader; mpremote | all | matplotlib is optional (figures) |

All protocol I/O on the Cmod A7 is 3.3 V LVCMOS. Every peripheral must be a
3.3 V part.

## Experiment A: host-clocked build, cycles from the clock pin

**Wiring.** The Pico to Cmod A7 connections are those of [fpga.md](fpga.md),
"Pin host (Pico)": GP0–7 to DIP 1–8 (`ui_in`), GP8–15 to DIP 9–14, 17, 18
(`uo_out`), GP16 to DIP 46 (`host_clk`), GP17 to DIP 47 (`rst_n`), GP18 to
DIP 48 (`ena`), and GND to DIP 25. Fit the two loopback jumpers on Pmod JA
(pin 1 to 2 and pin 4 to 7). Connect the analyser (fx2lafw) as follows:

| Analyser | Signal | Where |
|---|---|---|
| D0 `clk` | host clock | DIP 46 (Pico GP16) |
| D1 `probe6` | uio6, probe pin 6 | Pmod JA pin 9 |
| D2 `probe7` | uio7, probe pin 7 | Pmod JA pin 10 |
| D3 `uart` | uio0, UART TX of engine 0 (load evidence) | Pmod JA pin 1 |
| D4 `sck` | uio2, SPI SCK of engine 2 (load evidence) | Pmod JA pin 3 |
| D5 `wvalid` | ui_in[4], host write-valid (load evidence) | DIP 5 (Pico GP4) |
| D6 `rready` | ui_in[5], host read-ready (load evidence) | DIP 6 (Pico GP5) |
| D7 `marker` | phase marker: high while the measured phase runs | Pico GP19 |
| GND | | DIP 25 or Pmod JA pin 5 |

**Procedure.**

1. Program the board and run the bring-up of [fpga.md](fpga.md)
   ("Hardware test procedure", step 4) to check the Pico wiring:
   `openFPGALoader -b cmoda7_35t pe_cmod_a7_host.bit`.
2. Copy the files to the Pico:

   ```sh
   mpremote mkdir :firmware
   mpremote cp -r host/pe_host :
   mpremote cp demo/pe_demo.py demo/pico_demo.py :
   for f in firmware/uart-tx firmware/uart-rx firmware/spi-controller-mode0 demo/firmware/timing-probe; do
     mpremote cp $f.image.json $f.source.json :firmware/
   done
   ```

3. Start a capture (30 s at 8 MHz) and, while it runs, the idle phase:

   ```sh
   demo/capture.sh isolation-clock idle.sr 30 &
   mpremote exec "import pico_demo; pico_demo.isolation('idle', 100000)"
   ```

   Then the loaded phase:

   ```sh
   demo/capture.sh isolation-clock loaded.sr 30 &
   mpremote exec "import pico_demo; pico_demo.isolation('loaded', 100000, seed=1)"
   ```

   Choose the cycle count so that each phase ends before its capture does.
   The Pico's clock rate with the host library has not been measured; the
   script prints its wall time (`wall_ms`) at the end. If a phase is cut
   off, the analysis simply sees fewer frames.

   The Pico's clock high pulse lasts one MicroPython statement (not
   measured). `analyze` reports the shortest clock phase in samples and
   warns below 2. If it warns, capture at a higher rate: every missed clock
   edge would shift all later cycle numbers.
4. Analyse and compare:

   ```sh
   python3 demo/pe_capture.py predict --image demo/firmware/timing-probe.image.json --json predicted.json
   for run in idle loaded; do
     python3 demo/pe_capture.py analyze $run.sr --clock clk --gate marker --channels probe6,probe7 \
       --context uart,sck,wvalid,rready --uart uart:64 --uart-expect 55,a3,0f,c6,39,e1 \
       --reference predicted.json --label $run --json $run.json
   done
   python3 demo/pe_capture.py compare predicted.json idle.json loaded.json \
     --labels predicted,idle,loaded --expect same,same,same --plot isolation.png
   ```

**Expected result.**

- Both runs are **same**: every complete frame equals the predicted
  344-cycle frame, there is no departure from the schedule, and the peak-to-peak
  jitter of every edge position is 0 cycles.
- The per-frame histograms equal the prediction:

  | Channel | High durations (cycles: count per frame) | Low durations (cycles: count per frame) |
  |---|---|---|
  | probe6 | 7: 16, 12: 1 | 7: 15, 44: 1, 71: 1 |
  | probe7 | 5: 3, 14: 5, 15: 1, 28: 1 | 8: 2, 10: 1, 14: 4, 28: 2, 78: 1 |

- The loaded run's context channels show the load:
  - the UART channel decodes only the six circulating bytes
    (`55 a3 0f c6 39 e1`, repeating), with 0 framing errors;
  - SCK toggles;
  - write-valid and read-ready are active.

  The idle run shows no activity on these channels.
- The clock channel's period range shows how irregular the Pico's clock is.
  The recovered cycle intervals are exact regardless.

## Experiment B: free-running 12 MHz build, cycles recovered from time

**Wiring.** Connect the board to the PC over USB; the UART bridge is the
board's second serial port. Fit the two loopback jumpers on Pmod JA, as in A.
Connect the Pico analyser (sigrok-pico firmware) as follows:

| Pico analyser | Signal | Where |
|---|---|---|
| GP2 (D2) `probe6` | uio6 | Pmod JA pin 9 |
| GP3 (D3) `probe7` | uio7 | Pmod JA pin 10 |
| GP4 (D4) `uart` | uio0 | Pmod JA pin 1 |
| GP5 (D5) `sck` | uio2 | Pmod JA pin 3 |
| GND | | Pmod JA pin 5 |

**Procedure.**

1. Program the board and confirm the clock:

   ```sh
   openFPGALoader -b cmoda7_35t pe_cmod_a7_osc12.bit
   python3 fpga/host/pe_host.py --port /dev/ttyUSB1 info
   ```

   `info` must report `clock osc12 (12 MHz oscillator), 12 MHz` and a
   measured core clock near 12 MHz. Also run `selftest` and `loopback`
   ([fpga.md](fpga.md), steps 1–2).
2. Idle phase, with ten 3.3 ms captures taken while it runs:

   ```sh
   python3 demo/pc_demo.py --port /dev/ttyUSB1 isolation --mode idle --seconds 20 --json idle-run.json &
   sleep 3; demo/capture.sh isolation-realtime idle 10
   ```

3. Loaded phase:

   ```sh
   python3 demo/pc_demo.py --port /dev/ttyUSB1 isolation --mode loaded --seconds 20 --seed 1 --json loaded-run.json &
   sleep 3; demo/capture.sh isolation-realtime loaded 10
   ```

4. Analyse and compare:

   ```sh
   for run in idle loaded; do
     python3 demo/pe_capture.py analyze $run-*.sr --fclk 12e6 --channels probe6,probe7 --context uart,sck \
       --uart uart:64 --uart-expect 55,a3,0f,c6,39,e1 --reference predicted.json --label $run --json $run.json
   done
   python3 demo/pe_capture.py compare predicted.json idle.json loaded.json \
     --labels predicted,idle,loaded --expect same,same,same --plot isolation-realtime.png
   ```

**Expected result.**

- For every capture, the conversion line reports:
  - `consistent=True`;
  - an arc of at most about 1 sample (limit 1.25);
  - an estimated crystal offset in the tens of ppm. It is the sum of the
    board oscillator's and the analyser's offsets. Its magnitude has not been
    measured on these boards.
- Frames, histograms and jitter are as in A.
- In the loaded run, the UART channel decodes the six loop bytes. The captures
  are short and start at arbitrary points of back-to-back UART frames, so a
  byte cut by a capture boundary can decode wrongly or with a framing error.
  In simulation this gave up to 10 such bytes per 16 captures.
- The host drives the port through the bridge at 1 Mbaud, so its traffic is
  sparse in cycle terms: one nibble transfer burst per bridge frame, about
  70 µs apart. The deliberate FIFO writes and reads that time out are the
  exception: they hold write-valid or read-ready for their timeout of about
  70 cycles. `pc_demo.py` prints the number of host operations.

If `analyze` stops with "cycle recovery inconsistent", do not trust that
capture. Common causes are a loose ground, crosstalk between the analyser
leads, or a sample rate that the analyser did not actually run at. The rate
comes from the capture's metadata.

## Four protocols against real parts

Bitstream: `pe_cmod_a7_pll50_bridgeonly.bit` (50 MHz, UART bridge). The
firmware is in `demo/firmware`. Each image is assembled from its own source:

| Engine | Image | Pins | Timing at 50 MHz |
|---|---|---|---|
| 0 | `uart-tx-115200` (built-in `uart-tx`, `--half-period 217`) | 0: TX | 434 clocks per bit = 115,207 baud |
| 1 | `uart-rx-poll-115200` | 1: RX | as above. Idle and start waits poll without a bound, so the line may stay idle indefinitely (the built-in `uart-rx` faults after 12 idle bit times) |
| 2 | `spi-xfer32` | 2 SCK, 3 MOSI, 4 MISO, 5 CS | mode 0, one 32-bit transfer with CS held low, 16-clock half period (1.56 MHz) |
| 3 | `i2c-read-100k` (built-in `i2c-read`, `--half-period 250`) | 6 SCL, 7 SDA, open drain | about 100 kHz SCL. The TX word is the address byte; one byte is read, then NACK and STOP |

The route 1 → 0 echoes every byte engine 1 receives through engine 0.

**Wiring (Pmod JA).**

- **USB-UART adapter:** its RXD to pin 1 (uio0), its TXD to pin 2 (uio1).
- **SPI flash:** CLK to pin 3, DI to pin 4, DO to pin 7, /CS to pin 8.
  /WP and /HOLD to 3.3 V.
- **Sensor:** SCL to pin 9, SDA to pin 10. 4.7 kΩ pull-ups to 3.3 V
  (pin 6 or 12); address pin to GND for 0x48.
- **Power and ground:** power the flash and the sensor from Pmod 3.3 V
  (pins 6 and 12). Connect every GND together.
- **Analyser (fx2lafw):** D0–D7 to Pmod pins 1, 2, 3, 4, 7, 8, 9, 10.

**Procedure.**

```sh
openFPGALoader -b cmoda7_35t pe_cmod_a7_pll50_bridgeonly.bit
demo/capture.sh four four.sr 2 &
python3 demo/pc_demo.py --port /dev/ttyUSB1 four --uart /dev/ttyUSB2 --rounds 10
```

**Expected output.** Each round prints:

- the flash's JEDEC ID (for example `ef 40 18` for a W25Q128);
- the sensor's temperature MSB in degrees Celsius;
- the echoed text.

The run ends with the four engines' statuses (all running, no fault) and
`FOUR-PROTOCOL PASS`. `capture.sh four` then decodes the same bus traffic
with sigrok's UART, SPI and I2C decoders.

**Part choice.**

- **TMP102 and LM75.** Their register pointer powers up on the temperature
  register, so the single-transaction read of `i2c-read` returns its MSB.
- **Parts with an ID register.** A part whose ID register needs a pointer
  write first (BME280 chip ID, MPU-6050 WHO_AM_I) needs `i2c-repeated-start`.
  For that image, `tools/timing/pe_timing.py` reports SCL held low for only
  7 cycles before the repeated START ([timing-analysis.md](timing-analysis.md),
  "Findings"). That is shorter than the I2C minimum low time at any clock rate
  used here. Do not use it with real parts until the firmware is fixed.
- **The runt warning.** `pe_timing.py` also reports, for `i2c-read-100k`
  (and the built-in `i2c-read`), a 2-cycle SCL runt on the NACK-fault path
  (explicit fault 65) without a STOP. It occurs only when the target does not
  acknowledge its address.

## Host-free chain: I2C sensor to UART

This uses the same bitstream and wiring as the four-protocol run; the flash is
not needed.

| Engine | Image | Role |
|---|---|---|
| 1 | `read-ticker` | pushes the I2C address byte `0x91` (0x48, read) into its RX FIFO every 5,000,000 clocks (10 per second at 50 MHz); a full queue pauses it |
| 3 | `i2c-read-100k` | reads one byte per request |
| 2 | `hex-formatter` | turns each byte into two ASCII hex digits and CR LF |
| 0 | `uart-tx-115200` | sends the text |

Three routes (1 → 3, 3 → 2, 2 → 0) connect the engines. Once the host has
loaded the four images, set the routes and issued START, it does nothing
more:

```sh
python3 demo/pc_demo.py --port /dev/ttyUSB1 bridge --uart /dev/ttyUSB2 --seconds 10
# or, after setup, any serial terminal on the adapter at 115200 baud:
screen /dev/ttyUSB2 115200        # lines such as "19" (0x19 = 25 degrees C)
```

Each route carries at most 65,535 words (a 16-bit count). At 10 readings per
second the 3 → 2 route runs out after about 1.8 hours, and the 2 → 0 route
(4 words per reading) after about 27 minutes. Re-issuing the routes restarts
them.

## Analysis tool reference

`demo/pe_capture.py` needs only the Python standard library. numpy speeds up
`.sr` decoding, and matplotlib is needed only for `--plot`.

**Readers.**

| Format | Notes |
|---|---|
| sigrok srzip `.sr` | exact sample indices. `probeN` in the metadata is bit N−1 of each sample, as libsigrok's own srzip reader has it. Channel names are the names given with `--channels D0=name` at capture time |
| VCD | sigrok's VCD: time is rounded to its timescale (100 ps at 24 MHz), and the sample rate is taken from its `$comment` and converted back to sample indices. Simulation VCDs: exact times, hierarchical names, vector bit selects such as `tb.pads[6]` |
| CSV | sigrok `-O csv:label=channel`, one row per sample. Files with a time column are rejected, because sigrok 0.5.2 writes that column truncated to an integer number of units. Re-export from the `.sr` instead |

Against sigrok-cli 0.7.2 (libsigrok 0.5.2), `demo/tests` checks two things:

- a `.sr` capture from sigrok's demo driver, and its sigrok-converted VCD and
  CSV, give identical edges;
- a `.sr` file written by `pe_capture.py resample` opens in sigrok-cli and
  converts back unchanged.

**Subcommands.**

- `analyze`: edges to cycles (`--clock`, `--fclk` or neither) and frames
  against `--reference`. It reports:
  - per-channel high and low interval histograms, in total and per frame;
  - per-position jitter across frames;
  - the first departure from the periodic schedule;
  - with `--context` and `--uart`, the activity of the context channels and
    8N1 decoding;
  - with `--gate`, only edges while a marker channel is high;
  - with `--window-ns`, only a time window.
- `compare`: side-by-side histograms, one verdict per run, `--expect`
  checking (exit status 1 on a mismatch), `--plot` for a figure.
- `predict`: the reference frame from the static timing analyser.
- `resample`: emulates an analyser on an exact-time VCD, with rate, crystal
  offset, phase, Gaussian edge jitter, a time window and a per-file sample
  limit. It writes srzip.
- `info`: lists a capture's channels.

## Validation in simulation

The full experiment was run against the simulated FPGA top. Every result
below comes from Slurm jobs on MIT Engaging. The tree used was commit
`c118027` plus the `demo/` files of this page. Tools:

- cocotb 2.0.1;
- Icarus Verilog 13.0, the version the `test` action uses;
- sigrok-cli 0.7.2, for the decoder checks.

Job ids are listed in `demo/results/summary.json`.

**Testbenches.**

- **`pins`** reuses `fpga/sim/tb_fpga_pins.v`: the Cmod A7 `host` build, with
  the host library driving every clock edge through `CocotbPort`. It is the
  same scenario code the Pico runs. The clock period is irregular (300–2,000
  ns low, 200–800 ns high, seeded), like a microcontroller toggling a GPIO.
  The two jumpers are a pad environment. For the unmutated core, the
  reference model (`test/model`) runs in lockstep and every output is
  compared on every cycle.
- **`bridge`** reuses `fpga/sim/tb_fpga_bridge.v`, compiled as the Cmod A7
  `osc12` build: a 12 MHz oscillator, 83.333 ns in simulation, and the UART
  bridge divider of 12. `fpga/host/pe_host.py` drives the bridge's UART pins
  at 1 Mbaud through a simulator transport, and the tb's jumpers are closed.

`demo/sim/demo_dump.v` writes a VCD of only the signals an analyser would
see. `pe_capture.py` then analyses two views of each run:

- **exact**: the simulation VCD itself, cycles counted from the clock;
- **analyser**:
  - `pins`: resampled at 24 MHz with 8 channels, the fx2lafw view, cycles
    counted from the clock channel;
  - `bridge`: resampled at 120 MHz in 400,000-sample captures, the Pico
    analyser view, with a seeded crystal offset of up to ±80 ppm and a seeded
    sampling phase, cycles recovered with `--fclk 12e6`.

The analysis window is the measured phase, from the probe's START to the end
of the idle wait or of the host traffic (`pe_demo`'s phase marker).

**Mutants** (`demo/sim/mutate.py`). Each is a one-line edit of the generated
core that couples engine 3 to other activity:

| Mutant | Coupling | Effect when triggered |
|---|---|---|
| `host-fetch` | engine 3's instruction SRAMs skip their fetch while the host drives write-valid. This is exactly the formal negative control `timing_isolation_neg_mutant` (`formal/mutate.py sram-host-stall`); the script checks the edit is byte-identical | stale instructions execute |
| `engine-fetch` | the same, triggered by engine 0's UART pin being driven low | stale instructions execute |
| `timer-host` | engine 3's WAIT countdown holds while the host drives write-valid | each WAIT stretches by the number of strobe cycles inside it: a pure timing shift |
| `timer-engine` | the same, triggered by engine 0's UART pin being driven low | as above |

The WAIT register of engine 3 is found with the rule
`formal/gen/generate_fv.ml` uses: the register whose next-state cone reads
`image_length_3`.

Expected verdicts:

- `engine-fetch`, `timer-host` and `timer-engine` behave like the real core
  while nothing else runs, so each must be **same** when idle and
  **different** when loaded.
- `host-fetch` is **different** in both modes. The memory-enable net it gates
  also enables the host's program writes, so the host's own load of the probe
  image is blocked: the probe runs unwritten (zero) words and stops with fault
  2. The formal harness never shows this effect, because it assumes engine
  K's image instead of loading it.

**Results** (batch v4: Slurm jobs 23983461–23983484; `demo/results/`, with
`summary.json` holding every number below). Every simulation passed, and
every verdict matched its expectation in both views (`collect.py`: ALL PASS).
The jobs ran the `demo/` files of this page, except for the plotting code in
`pe_capture.py` and `collect.py`, which changed afterwards. The figures were
drawn from the jobs' analysis files with the final plotting code.

| Case | Job | Expected | Exact VCD | Analyser view | Frames identical (analyser view) | Jitter p-p (cycles) | Host operations |
|---|---|---|---|---|---|---|---|
| pins idle, core | 23983461 | same | same | same | 580/580 | 0 | 0 |
| pins loaded, seed 1 | 23983462 | same | same | same | 580/580 | 0 | 9,612 |
| pins loaded, seed 2 | 23983463 | same | same | same | 580/580 | 0 | 9,714 |
| pins loaded, seed 3 | 23983464 | same | same | same | 580/580 | 0 | 9,746 |
| pins idle, `timer-host` | 23983465 | same | same | same | 580/580 | 0 | 0 |
| pins loaded, `timer-host` | 23983466 | different | different | different | 0/376 | 165 | 9,612 |
| pins idle, `timer-engine` | 23983467 | same | same | same | 580/580 | 0 | 0 |
| pins loaded, `timer-engine` | 23983468 | different | different | different | 52/413 | 319 | 9,612 |
| pins idle, `host-fetch` | 23983470 | different | different | different | 0/0 (probe faults, code 2) | – | 0 |
| pins loaded, `host-fetch` | 23983471 | different | different | different | 0/0 (probe faults, code 2) | – | 9,612 |
| pins loaded, `engine-fetch` | 23983472 | different | different | different | 0/0 (probe faults, code 2) | – | 9,612 |
| bridge idle, core | 23983474 | same | same | same | 1,728/1,728 | 0 | 0 |
| bridge loaded, seed 1 | 23983475 | same | same | same | 1,733/1,733 | 0 | 212 |
| bridge loaded, seed 2 | 23983476 | same | same | same | 1,729/1,729 | 0 | 213 |
| bridge idle, `timer-host` | 23983477 | same | same | same | 1,727/1,727 | 0 | 0 |
| bridge loaded, `timer-host` | 23983478 | different | different | different | 1,586/1,730 | 28 | 212 |
| bridge loaded, `timer-engine` | 23983479 | different | different | different | 173/1,235 | 319 | 212 |
| bridge idle, `host-fetch` | 23983480 | different | different | different | 0/0 (probe faults, code 2) | – | 0 |
| bridge loaded, `host-fetch` | 23983481 | different | different | different | 0/0 (probe faults, code 2) | – | 212 |

- **The pins runs.**
  - 200,000 host clocks each, 580 probe frames.
  - For the unmutated core, the reference model agreed with the RTL on every
    cycle (lockstep).
  - In the loaded runs, the host drove write-valid high in 63.3 % of all
    cycles.
  - The UART channel decoded 309 bytes, all of them loop bytes, with 0
    framing errors.
  - The fx2lafw-class view (24 MHz, cycles counted from the clock channel)
    gave the same frames and histograms as the exact VCD: the two comparison
    reports are identical.
- **The bridge runs.**
  - 600,000 cycles at 12 MHz (50 ms), about 1,730 frames.
  - The host's operations go through the 1 Mbaud bridge, so write-valid was
    high in only 0.56–0.63 % of cycles.
  - The exact view decoded 921–922 loop bytes with 0 framing errors. The
    Pico-analyser view (15–16 captures of 400,000 samples at 120 MHz) had 10
    framing errors per run and 20 bytes outside the loop set, all at capture
    boundaries.
  - In every capture, cycle recovery reported `consistent=True` with a
    maximum arc of 1.001–1.031 samples.
  - The estimated offsets agree within 0.3 ppm with the seeded analyser
    offset combined with the simulated oscillator's own offset. That own
    offset is +4 ppm, because an 83.333 ns period is 12.000048 MHz. For
    example, bridge loaded seed 2 has a seeded offset of −41.4 ppm and an
    estimate of −45.3 to −45.5 ppm. The short last capture of a run gives a
    coarser estimate.
  - The analyser view has about 15 fewer complete frames than the exact view,
    because a frame that spans a capture boundary is not complete in either
    capture. Its per-frame histograms equal the exact view's.
- **The mutants.**
  - The timer mutants stay same when idle. When loaded, edge positions within
    a frame spread by 28 to 319 cycles (per-position peak-to-peak), and the
    frame starts drift away from the undisturbed schedule (panel (a) of the
    figures).
  - `host-fetch` and `engine-fetch` stop the probe with fault 2, as
    described above.
  - Figures: `demo/results/isolation-pins.png` and
    `demo/results/isolation-bridge.png` (idle, loaded, `timer-host` loaded).
    Side-by-side histograms of every run are in `demo/results/compare-*.txt`.
- **Four protocols (`four-1`, job 23983482; lockstep, fixed 1 µs clock).**
  The flash model answered JEDEC ID `ef 40 18`, the I2C target returned
  `0x19`, and UART TX sent `OK` followed by the echo `hi\r\n`. sigrok-cli
  0.7.2's decoders on the emulated 2 MHz capture of all eight pads showed:
  - UART (2304 baud, the 434-cycle bit at 1 µs per cycle): `4F 4B 68 69 0D 0A`
    on pin 0 and `68 69 0D 0A` on pin 1;
  - SPI: MOSI `9F 00 00 00` and MISO `FF EF 40 18`;
  - I2C: START, address read 0x48, ACK, data 0x19, NACK, STOP.
- **Host-free chain (`bridge-chain-1`, job 23983483; lockstep).** UART text
  `19\r\n1A\r\nF6\r\n` for the target's readings. The target saw four
  read requests (`0x91`) in 130,000 cycles.

**Cycle recovery across analyser rates** (`demo/sim/recovery_sweep.py`, job
23983484, `demo/results/recovery-sweep.json`). The sweep takes the
`bridge-loaded-base-1` VCD (12 MHz; 109,627 probe edges over the whole
simulation) and emulates an analyser 84 times: 7 sample rates × 12 trials.
Each trial has a random crystal offset (±150 ppm), sampling phase and
Gaussian edge jitter (0, 200 or 500 ps rms). The recovered edge-to-edge
intervals are then compared with the exact ones.

| Analyser rate | fs/fclk | Exact | Wrong but self-consistent | Flagged inconsistent | `analyze --fclk` |
|---|---|---|---|---|---|
| 16 MHz | 1.33 | 3/12 | 9 | 0 | refuses |
| 21 MHz | 1.75 | 12/12 | 0 | 0 | refuses |
| 24 MHz | 2.00 | 4/12 | 6 | 2 | refuses |
| 26.4 MHz | 2.20 | 12/12 | 0 | 0 | accepts |
| 30 MHz | 2.50 | 12/12 | 0 | 0 | accepts |
| 48 MHz | 4.00 | 12/12 | 0 | 0 | accepts |
| 120 MHz | 10.0 | 12/12 | 0 | 0 | accepts |

At 24 MHz on the 12 MHz build, half the trials produced cycle numbers that
were wrong yet passed the tool's own consistency check. That is the fs = 2
fclk alias, and it is why the ratio rule exists. 21 MHz happened to recover
exactly in all 12 trials, but its margins are small, so the rule refuses it
too. Above the rule, all 48 trials recovered every interval exactly.

**Other checks run for this page** (login node; `demo/tests`, 24 tests):

- **Scenarios on the reference model.** Idle and loaded runs give identical
  probe edges; the loaded run issues more than 300 host operations in 12,000
  cycles.
- **The four-protocol scenario on the model, with pad-level peers:**
  - a SPI flash model answers JEDEC ID `ef 40 18`;
  - a TMP102-like I2C target returns `0x19`;
  - UART text goes out, and the UART echo comes back through the mover.
- **The host-free chain on the model** prints `19\r\n1A\r\nF6\r\n` for the
  target's readings `0x19, 0x1A, 0xF6`.
- **The PC adapter on the model.** `pc_demo.BridgeChip` drives
  `fpga/host/pe_host.py`, which talks to a stand-in for the UART bridge that
  implements its byte protocol on the model. The isolation scenario (idle
  and loaded probe edges identical), the four-protocol scenario and the
  host-free chain all pass through it. The CLI wrappers that also drive a
  real USB-UART adapter (`pc_demo.py four` and `bridge`) have not run.
- **MicroPython.** `pe_demo.py` passes the subset check of
  `host/tools/upy_check.py`. A differential replay under MicroPython 1.29
  (unix port) reproduces a CPython loaded run cycle for cycle (`ReplayPort`),
  with an identical result digest.
- **Firmware and mutants.**
  - `demo/firmware` equals what `demo/firmware/build.py` generates with the
    committed assembler.
  - Every image passes the identity checks of `pe_host.FirmwareImage`.
  - Each mutant differs from the core in exactly one line.
- **Capture tools.** Readers, clock counting, cycle recovery and frame
  verdicts are checked on synthetic captures, and against sigrok-cli output
  (above).

`pe_timing.py analyze` on the demo images:

- `timing-probe`: exact analysis, one path variant, periodic with period 344.
- `uart-rx-poll-115200`, `spi-xfer32`, `read-ticker` and `hex-formatter`:
  every check passes.
- `i2c-read-100k`: carries the fault-path runt warning of the built-in
  `i2c-read` (above).

**Reproduce.**

```sh
python3 -m unittest discover -s demo/tests -v    # MICROPYTHON=..., ASSEMBLER=..., SIGROK_CLI=... enable the optional tests
DEMO_WORK=$PE_WORK/demo-harness/run DEMO_ENV=env.sh DEMO_SBATCH_ARGS="-p PARTITION" demo/sim/submit.sh
python3 demo/sim/collect.py $PE_WORK/demo-harness/run --publish demo/results
python3 demo/sim/recovery_sweep.py $PE_WORK/demo-harness/run/runs/bridge-loaded-base-1/sim.vcd.gz --json sweep.json
```

`env.sh` puts cocotb 2.0.1, Icarus Verilog and Python on `PATH`. One case is
`demo/sim/run_case.sh TAG TB MODE CORE SEED CYCLES [TEST]`. It keeps the
simulation VCD gzipped, and `demo/sim/analyze_case.sh DIR` re-runs the
capture analysis of a finished case.

## What the measurement does and does not show

**Shows.** On the FPGA prototype, engine 3's pin edges fall on the same clock
cycles relative to its START whether or not the other engines and the host
are busy. The claim covers every edge over the recorded frames and matches
the pattern predicted statically from the image. The prototype runs the
generated core and the Tiny Tapeout top unchanged; the IHP SRAM macros are
replaced by a stand-in proved equivalent to their behavioural model
([fpga.md](fpga.md), "SRAM stand-in"). Together with the negative controls,
this shows that the measurement chain (capture, cycle conversion, frame
comparison) detects a coupling of one cycle.

**Does not show.**

- **Silicon.** The FPGA is not the IHP chip. Analogue effects that could
  couple engines on silicon are outside a cycle-level digital claim, and
  sub-sample timing is invisible at these sample rates. Examples: supply
  noise, ground bounce, crosstalk, pad delay that depends on switching
  activity. One sample is 41.7 ns at 24 MHz and 8.3 ns at 120 MHz; edges
  that move by less than that are not detected.
- **Speed.**
  - Experiment A runs the design at the Pico's clock rate, not at 50 MHz.
  - Experiment B runs it at 12 MHz. In a synchronous design that meets
    timing, cycle behaviour does not depend on the clock rate. Timing closure
    at 50 MHz is a separate question ([hardening.md](hardening.md),
    [fpga.md](fpga.md) "Build results").
- **Generality.**
  - It is one probe program, on one engine, for finite captures: a sample of
    behaviour, not a proof. The proof is the formal property; the measurement
    checks that the prototype is consistent with it.
  - The property, and so the expectation, only covers intervals in which the
    measured engine does not exchange FIFO words or events. A protocol engine
    that PULLs from its FIFO depends on when the host or the mover filled
    it, by design.
- **Host rate.** The Pico host's clock rate and the UART bridge's operation
  rate on real hardware have not been measured.
