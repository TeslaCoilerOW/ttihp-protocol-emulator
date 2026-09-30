# Firmware and assembler

The assembler is implemented in OCaml independently of the Hardcaml decoder.
Run it from the project's `hardcaml` directory after installing the pinned tools:

```bash
dune exec bin/assemble.exe -- --firmware uart-tx --output /tmp/uart-tx.image.json --source-output /tmp/uart-tx.source.json
dune exec bin/assemble.exe -- --source /tmp/uart-tx.source.json --output /tmp/uart-tx.reassembled.json
dune exec bin/assemble.exe -- --firmware spi-controller --mode 3 --output /tmp/spi3.image.json
dune exec bin/assemble.exe -- --firmware i2c-repeated-start --output /tmp/i2c-rs.image.json
```

`--list` lists firmware examples. Built-in parameters are `--width 16|32`,
`--engines 2|4`, `--words 32|64|128`, `--fifo-words 8|32`, `--issue scalar|fused`,
`--prefetch`, `--half-period 8..255`, `--clock-hz HZ`, and SPI `--mode 0..3`.
The default clock annotation is 50 MHz and half-period is 32 clocks. These are
requested timing parameters, not physical qualification claims. Architecture
options configure built-ins; explicit source files carry their own architecture.

## Source and image contracts

Source schema `protocol-emulator.firmware-source.v1` has exactly these fields:
`schema_version`, `name`, `architecture`, `engine`, `owned_pins`, `open_drain`,
`clock_hz`, `instructions`, and `notes` (a list of support-limit strings).
Architecture is the complete `protocol-emulator.architecture.v1` object defined
by the project. The assembler rejects missing/unknown/duplicate fields, invalid
widths/masks, extra operands, absent engine masks, illegal pin writes, disabled
fused instructions, overflowing images and branches outside committed images.

Each instruction is an object with a required uppercase `mnemonic` and optional
integer `a`, `b`, `c`, `imm` (default zero), plus optional `label` and `target`.
`label` defines the current instruction PC; `target` resolves a branch label and
is mutually exclusive with `imm`. Labels use letters/digits/underscore and cannot
start with a digit. The exact operand meanings are in [isa.md](isa.md).

Image schema `protocol-emulator.firmware-image.v1` contains `isa_version: 2`,
`name`, `architecture`, `engine`, `owned_pins`, `open_drain`, `clock_hz`,
`source_sha256`, `bytecode_sha256`, `words`, `labels`, `timing`, and `notes`.
The source hash covers exact input bytes, including whitespace. The bytecode hash
covers consecutive 32-bit words in little-endian order. `words` are JSON integers.
`timing` supplies `{pc, minimum_cycles, blocking}` per instruction. Blocking values
are `none`, `tx_fifo_unbounded`, `rx_fifo_unbounded`, `pin_limit`, `event_limit`.
This is local instruction timing, not a claim about whole-program runtime or
fmax: control flow, external edges, events, and queue occupancy affect execution.

Load by SELECT, BEGIN, complete contiguous program-word writes, OWN, COMMIT,
then START. For a concurrent start, load every image and issue one START mask.
Do not release a partial image. Prefill queues before START for protocols whose
initial operation consumes TX data. Image ownership describes driven pins;
input-only firmware can own zero pins while observing other engines' pins.

## Executable example contracts

| Firmware | Pins | Framing and behavior |
|---|---|---|
| `uart-tx` | TX0 | 8N1, LSB first, exact bit period `2*half_period`, low-byte TX words |
| `uart-rx` | RX1 | 8N1, centered sampling, low-byte RX words, framing fault64; idle and start-bit waits bounded at twelve bit periods (fault 3) |
| `uart-rx-idle` | RX1 | 8N1 at 64 clocks per bit, low-byte RX words, framing fault64; idle and start-bit waits polled with no bound (no fault 3); engine 1 of the flagship scenario; a source file, not a built-in |
| `spi-controller` | SCK2/MOSI3/MISO4/CSn5 | modes0–3, MSB first, eight bits per CS assertion, queued full duplex |
| `spi-target` | same mapping | modes0–3, one byte per CS assertion, MISO released between frames |
| `i2c-write` | SCL6/SDA7 | address+W and one byte from TX; ACK checks; STOP; an address or data NACK ends with STOP and fault65 |
| `i2c-read` | SCL6/SDA7 | address+R from TX; one RX byte, NACK, STOP; an address NACK ends with STOP and fault65 |
| `i2c-repeated-start` | SCL6/SDA7 | address+W/register/address+R from TX; repeated START, one-byte read, NACK, STOP; a NACK ends with STOP and fault65; shared transmit routine, 61 of 64 words |
| `i2c-target-write` | SCL6/SDA7 | address0x42, one-byte write, stretch before ACK until RX space |
| `i2c-target-read` | SCL6/SDA7 | address0x42, one-byte read, stretch before address ACK until TX data |
| `jtag` | TCK0/TDI1/TDO2/TMS3 | reset TAP, enter Shift-DR, stream LSB-first eight-bit scan chunks |
| `waveform` | OUT0 | sixteen pulses plus timestamp in RX queue |
| `event-transmitter` | OUT0 | mailbox-triggered pulse |
| `swd-read` | SWCLK0/SWDIO1 | Arm SWD host: JTAG-to-SWD switch and line reset on request, read transactions (DPIDR, CTRL/STAT, ...) with turnaround, ACK and data parity check; faults 80/81/82 |
| `ws2812` | DOUT2 | WS2812B pixels (GRB, MSB first) with the WS2812B datasheet bit times; a flagged word ends the frame with a 300 µs reset |
| `ws2812b-v5` | DOUT2 | the same program with the WS2812B-V5 bit times |
| `ps2-device` | CLK4/DATA5 | PS/2 device-to-host frames (start, 8 data bits, odd parity, stop) at 12.5 kHz; idle check, abort and retransmission on host inhibit |
| `ps2-host` | CLK4/DATA5 | PS/2 host: host-to-device command with request to send and acknowledge, then N response frames read back |
| `onewire-master` | DQ7 | 1-Wire master: reset and presence detect, byte write/read in time slots (Read ROM and other ROM commands built by the host); fault 96 |

The examples use programmable instructions and ordinary FIFOs. No protocol is
hardwired into the processor. SPI controller scalar expansion supports all
modes but has instruction overhead between bits; fused transfer has exactly
`half_period` clocks between transitions. Quantify both using actual trace
comparisons. RX values occupy the low byte for all byte protocols.

SPI target assumes at least eight system clocks per external half-period and
between CS assertion and the first clock edge. It checks CS at frame boundaries;
a truncated frame reaches the bounded pin-wait fault. TX must be queued before
selection. I2C target requires at least sixteen clocks per low/high phase and
START setup, dedicated point-to-point use, and a controller that honors clock
stretching. I2C controller supports bounded stretching but does not implement
multi-controller arbitration. Both I2C pins require external pull-ups. A
mismatched target address/direction releases the bus with fault66, rather than
participating as a silent listener on an unrelated transaction. Target-read
expects a controller NACK after its one byte; ACK faults67. Target examples
require STOP between transactions; repeated START is in controller firmware.

UART receiver input is synchronized before timing begins. Its stop-bit check
includes a few instruction cycles of center offset; continuous traffic requires
`half_period >= 8` and RX queue service. The receiver uses strict PUSH: FIFO
saturation preserves the received word in the inspectable receive register and
halts with sticky fault4. Later physical UART characters cannot be recovered
without peer flow control. The idle and start-bit waits of `uart-rx` are
bounded at twelve bit periods (fault 3); use a custom LIMIT for longer idle
intervals, or `uart-rx-idle`, whose waits have no bound. `uart-rx-idle`
waits for the idle line and each start bit in a three-cycle `IN`/`XOR`/`JZ`
polling loop (0 to 2 cycles of detection latency). It samples frame bit n
(start bit 0, stop bit 9) 64n + 31 to 64n + 33 cycles after the first clock
edge that registers the start bit's falling edge (the middle of a 64-cycle
bit is 32), and declares a baud tolerance of ±2%
([info.md](info.md), "Example: UART TX, SPI and I2C at the same time"). It
is written for 64 clocks per bit and a 32-bit datapath.

The JTAG fixture demonstrates TAP navigation and scanning; device-specific IR
programming, scan-length discovery, and update/exit sequences require a program
for that device. It does not claim universal device programming support.

## I2C controller timing

The three controller images (`i2c-write`, `i2c-read`, `i2c-repeated-start`)
are built from shared routines in `hardcaml/lib/firmware.ml`: START, eight data
bits, the ACK clock, a received byte with the controller's NACK, and STOP. SCL
and SDA never change on the same clock edge. SDA changes only while SCL is low,
except for START and STOP. Every SCL release is followed by `WAITPIN SCL==1`, so
clock stretching is honoured, and LIMIT (`4096*half_period` clocks) bounds
each wait.

**Declared bus speed.** The last note of each controller image states the
UM10204 speed modes met at the image's clock annotation. The assembler computes
it from the bounds below, including the 4-clock tVD;DAT bound of the
fixed-latency paths, and the limits of UM10204 Rev. 7.0 Table 11.
With the defaults (50 MHz, half-period `p` = 32) the images declare
Fast-mode Plus. The notes also give the half-period each slower mode needs at
the annotated clock: `p >= 62` for Fast-mode and `p >= 247` for Standard-mode
at 50 MHz.

| parameter (UM10204 Table 11) | bound (clocks) | p = 32 | at 25 MHz | at 50 MHz | at 66 MHz |
|---|---|---|---|---|---|
| SCL low, tLOW | p+3 | 35 | 1.40 µs | 0.70 µs | 0.53 µs |
| SCL high, tHIGH | p+4 | 36 | 1.44 µs | 0.72 µs | 0.55 µs |
| START and repeated-START hold, tHD;STA | p+2 | 34 | 1.36 µs | 0.68 µs | 0.52 µs |
| repeated-START set-up, tSU;STA | p+5 | 37 | 1.48 µs | 0.74 µs | 0.56 µs |
| STOP set-up, tSU;STO | p+5 | 37 | 1.48 µs | 0.74 µs | 0.56 µs |
| bus free between STOP and START, tBUF | 2p+8 | 72 | 2.88 µs | 1.44 µs | 1.09 µs |
| data set-up, tSU;DAT | p+2 | 34 | 1.36 µs | 0.68 µs | 0.52 µs |
| SCL frequency fSCL, from a period of at least 2p+7 clocks | 2p+7 | 71 | ≤ 352 kHz | ≤ 704 kHz | ≤ 930 kHz |
| data hold after SCL falls, tHD;DAT | 2 | 2 | 80 ns | 40 ns | 30 ns |
| data valid after SCL falls, tVD;DAT (maximum), fixed-latency paths | 4 | 4 | 160 ns | 80 ns | 61 ns |
| SDA change after SCL falls on a path through a non-blocking `PULL` (measured, maximum) | - | 7 (`i2c-write`), 12 (`i2c-repeated-start`) | 280 ns, 480 ns | 140 ns, 240 ns | 106 ns, 182 ns |

tHIGH, tSU;STA and tSU;STO include the two-flop input synchronizer: the
firmware counts from its observation of SCL high, which follows the pad's rise
by at least two clocks. `tools/timing/pe_timing.py` measures the same quantities
on the images ([timing-analysis.md](timing-analysis.md)). Its minima equal these
bounds except for tLOW of `i2c-write` (36, SCL period 72) and tBUF of
`i2c-repeated-start` (111).

The tVD;DAT bound of 4 clocks is the one `pe_timing` checks. It covers the SDA
changes whose distance from the SCL fall is fixed. Two bytes are sent after a
`PULL` that runs while SCL is held low: the data byte of `i2c-write` and the
register byte of `i2c-repeated-start`. (The address+R word is pulled before the
repeated START, so no SDA change follows that `PULL` while SCL is low.) On the
reference model, with the TX word already queued, the SDA changes of these two
bytes come 7 clocks (`i2c-write`) and 9 and 12 clocks (`i2c-repeated-start`)
after SCL falls, and their first SCL low phases last 41 and 46 clocks instead
of 35 to 37 (Slurm job 23984714). If the TX FIFO is empty, the `PULL` waits and SCL stays low until
the word arrives. In every case SDA settles at least 34 clocks (`p+2`) before
SCL is released. UM10204 Table 11 note [3] requires the tVD;DAT maximum only of
a device that does not stretch the LOW period of SCL; a device that does must
have the data valid by the set-up time before it releases the clock.

With these numbers every Fast-mode Plus minimum is met at 50 and 66 MHz, and
every Fast-mode and Fast-mode Plus minimum at 25 MHz. The measured SDA changes
stay within the Fast-mode Plus tVD;DAT maximum of 0.45 µs at 50 and 66 MHz. At
25 MHz the 12-clock change of `i2c-repeated-start` takes 0.48 µs, which exceeds
that maximum; it is covered only by note [3]. The Fast-mode Plus minima hold up
to 70 MHz (72 MHz for `i2c-write`) and the Fast-mode minima up to 26.9 MHz
(27.7 MHz); both limits come from tLOW or the SCL period. The analyzer also
gives lower clock limits (8.9 MHz for Fast-mode Plus, 4.4 MHz for Fast-mode).
They follow from its 4-clock tVD;DAT and do not cover the `PULL` paths. The
numbers are digital schedules at the pins; rise and fall times, pad delays and
board delays are not included.

**NACK.** When a target does not acknowledge a byte that the controller sent,
the firmware runs its normal STOP sequence:

1. SDA is pulled low 2 to 4 clocks after SCL fell.
2. SCL is released after being low for at least `p+4` clocks.
3. After SCL is seen high, the firmware waits `p+1` more clocks.
4. `FAULT 65` releases the owned pins.

SCL is already released at step 4, so the fault releases only SDA. That release
is the STOP condition: fault 65 and the STOP occur on the same clock edge, and
the bus is idle afterwards. A NACK can follow the address byte of any of the
three images, the data byte of `i2c-write`, and the register or address+R byte
of `i2c-repeated-start`. The controller's own NACK after the byte it reads is
the normal end of a read and is followed by STOP without a fault.

**Holding points.** Each transaction pulls its first TX word while both lines
are released, so an engine waiting for a new transaction leaves the bus idle.
`i2c-write` takes its data byte, and `i2c-repeated-start` its register and
address+R bytes, with SCL held low; UM10204 sets no maximum SCL low time.
Queue the complete transaction before START if the target should not see these
pauses. The received byte is pushed with SCL held low (`PUSH`, blocking when
the RX FIFO is full).

**Repeated START.** After the register byte's ACK clock, SCL stays low for at
least 42 clocks before it is released for the repeated START. The path contains
a `WAIT half_period`. The target therefore releases its ACK before SCL rises,
provided it does so within tVD;ACK (0.45 µs in Fast-mode Plus, 22.5 clocks at
50 MHz and 29.7 clocks at 66 MHz). On the reference model, a target that
releases this ACK up to 42 clocks after SCL falls still sees a repeated START;
at 43 clocks it sees a STOP and then a START (job 23984841). The same sweeps
cover the other phases in which the target drives SDA. Its read data may become
valid up to 35 clocks after SCL falls, and the latest tolerated ACK release is
37 to 46 clocks after SCL falls, depending on the phase (jobs 23984785,
23984841).

**Not covered.** SDA changes at least 2 clocks after the controller pulls SCL
low (40 ns at 50 MHz). UM10204 Rev. 7.0 Table 11 note [2] asks that SCL drop
below 0.3 VDD before SDA enters the 0.3 VDD to 0.7 VDD range, and Fast-mode Plus
allows an SCL fall time of up to 120 ns. The note adds that a controller that
cannot observe the SCL falling edge should delay SDA by a measured SCL fall
time. These images can observe SCL (`WAITPIN`) but do not wait for SCL low
before changing SDA. A trial build that adds `WAITPIN SCL==0` after every SCL
fall that the controller follows with an SDA change needs 65 words for
`i2c-write`, 59 for `i2c-read` and 65 for `i2c-repeated-start`; the assembler
rejects the two 65-word images (job 23984728). Whether 2 clocks cover the SCL
fall time of a particular pad and bus has not been analyzed. Only one
controller may be on the bus; there is no arbitration-loss detection.

**Changes from the earlier images.** The images before this revision were
analyzed at commit 73536f0 ([timing-analysis.md](timing-analysis.md),
Findings 1 to 3). Three problems were fixed:

- `i2c-repeated-start` released SCL for the repeated START 7 clocks after
  pulling it low.
- On a NACK, all three images released SCL 2 clocks after pulling it low and
  sent no STOP.
- `i2c-repeated-start` generated the next transaction's START before it waited
  for TX data, so it held a started bus with SCL low between transactions.

To fit the fixes, redundant instructions were removed: a duplicated `DIR`, the
`NOP` branch targets and a repeated `SET`. `i2c-repeated-start` now pulls the
first TX word of a transaction before its START, and the address+R word before
the repeated START. The images are 60 (`i2c-write`,
previously 64), 55 (`i2c-read`, 58) and 61 (`i2c-repeated-start`, 63) words
long. The other 16 images are byte-identical to their previous versions. The
three I2C images of every `build/variants/` set change in the same way. Run
`scripts/gen_variants.sh` to regenerate them.

**Evidence for the regenerated images.** All runs used the c118027 design and
the TT CI tool versions (cocotb 2.0.1, Icarus Verilog 13.0) where a simulator
was involved. Slurm job ids:

- Assembler: the assembler test passes, the RTL regenerates byte for byte and
  the 16 other images are unchanged (23977525).
- Static timing: `pe_timing` report (23975273, 23986537), and validation
  against the reference model (23975500, array 23975501; run r8, jobs
  23984750 to 23984754); see [timing-analysis.md](timing-analysis.md).
- `test/` suite, 66 of 66 on the design of record and on each of the six
  `PE_VARIANT` variants (23975686).
- `test/` at gate level on the c118027 CI netlist: 36 pass, 30 skipped by the
  gate-level subset, none fail (23975498).
- `test_ext`, 65 of 65 at RTL and at gate level (23975495, 23975497), plus
  seeded runs of its I2C and flagship modules (arrays 23977094, 23977096); see
  [independent-peers.md](independent-peers.md).
- `host/` unit tests: 69, of which 4 are skipped without MicroPython
  (23975499).
- Bus-level measurement on the reference model against a target model written
  for it: 23 directed cases (stretching included), ACK-release and data-valid
  sweeps, and the datasheet's host sequence for engine 3 (`COMMIT` 60
  accepted, 64 rejected) (23984714, 23984785, 23984841).
- The comment-only revision of `hardcaml/lib/firmware.ml` reassembles all 19
  images byte for byte, passes the assembler test and regenerates the RTL
  unchanged (23984728).

## Flagship concurrent setup

`firmware/flagship-scenario.json` allocates engine0 UART TX, engine1 UART RX,
engine2 SPI controller, and engine3 I2C controller across all eight pins. Its
bounded route forwards engine1 RX low-byte words into engine2 TX without host
service. The host separately supplies engine0 and engine3 TX and drains engines2
and3 RX. Route and FIFO counters must show accepted transfers; a blocked SPI
sink eventually backs up the UART RX queue. The next UART strict PUSH then
halts with sticky fault4 and preserves that word for inspection. The fault
makes overload visible; an unthrottled wire can continue sending after the halt.

This scenario is a bench recipe requiring an external UART peer, SPI responder,
and open-drain I2C responder or an independent simulator scoreboard. Source
images and a wiring recipe alone do not establish a passing hardware demo.

## Executed firmware-only demonstrations

The JTAG example resets an independent TAP peer (`test/model/jtag.py`), enters
Shift-DR and transmits `69 B4 02` while returning `D2 3C A5`. It remains a
continuous scan example; device-specific instructions and scan-update sequences
are outside its contract. The peer follows the
[AMD UG570 TAP state diagram](https://docs.amd.com/r/en-US/ug570-ultrascale-configuration/TAP-Controller-and-Architecture).

The custom waveform firmware produces 16 pulses: 32 cycles high and 64 cycles
between pulses, then returns the expected common-counter timestamp and halts
with outputs released. Both demonstrations load different firmware into the
same RTL.

In this repository, `test/test_legacy.py` replays all 25 reference workloads
(including these two) against the generated RTL in lockstep with the Python
reference model, and `test/test_protocols.py` checks UART, SPI and I2C at the
pins against independent peers (`test/README.md`). These are digital
simulation observations, not recorded FPGA or ASIC operation. The monorepo's
earlier native-simulation evidence for the same firmware (jobs 22625902 and
22626932) is summarized in its own status records and is not repeated here.

## SWD, WS2812B, PS/2 and 1-Wire images

Six images add four protocol families with no RTL change: they run on the
design of record exactly as the images above do. Each is an explicit source
file (`firmware/<name>.source.json`, not an OCaml built-in) assembled by the
OCaml assembler; assembling a source twice gives the same image bytes:

```bash
dune exec bin/assemble.exe -- --source ../firmware/swd-read.source.json --output /tmp/swd-read.image.json
```

`scripts/gen_variants.sh` does not reassemble them for the variants, and the
tests below skip a design whose engine count, data width, FIFO depth or
issue mode differs from the design of record. They also skip the tests of an
image on a design whose ISA-version-3 knobs change one of its words
([isa.md](isa.md), "ISA version"): `ps2-host` shifts by 21 (word 45), which a
design with byte-lane shifts faults on with code 1. There
`test_ps2_host_restricted` checks that fault instead
([test/README.md](../test/README.md)), and the host library refuses to load
`ps2-host` on a device that reports ISA version 3 ([host.md](host.md),
`pe_host.image`).

| Image | Engine, pins | Words of 64 | Protocol subset | Timing at 50 MHz | Cited document |
|---|---|---:|---|---|---|
| `swd-read` | 0: SWCLK0, SWDIO1, push-pull | 60 | JTAG-to-SWD switch and line reset (on request), any read transaction, ACK and parity checks | SWCLK 1.5625 MHz | Arm ADIv5 (IHI 0031) and ADIv5.1 Supplement (DSA09-PRDC-008772) |
| `ws2812` | 1: DOUT2, push-pull | 22 | GRB pixels, reset (latch) | T0H 0.40, T1H 0.80, T0L 0.86, T1L 0.46 µs, reset 300 µs | Worldsemi WS2812B datasheet |
| `ws2812b-v5` | 1: DOUT2, push-pull | 22 | same | T0H 0.30, T1H 0.64, T0L 0.96, T1L 0.62 µs, reset 300 µs | Worldsemi WS2812B-V5 datasheet V1.0 |
| `ps2-device` | 2: CLK4, DATA5, open-drain | 45 | device-to-host frames, host inhibit | 12.5 kHz, 40 µs low and high | A. Chapweske, "The PS/2 Mouse/Keyboard Protocol" (2003) |
| `ps2-host` | 2: CLK4, DATA5, open-drain | 49 | host-to-device command, acknowledge, response frames | device-clocked | same |
| `onewire-master` | 3: DQ7, open-drain | 28 | reset, presence, byte write and read (Read ROM and other ROM commands) | 75 µs slots, 500 µs reset | Maxim AN126 (rev. 052802), DS18B20 datasheet (rev. 042208) |

The four families use disjoint pins and engines, so one image of each can run
at the same time. The TT pads are 3.3 V: the 5 V buses (PS/2, and a WS2812B at
VDD = 5 V) need level shifting, and every open-drain line needs an external
pull-up.
<!-- ENTRANT: authoring record (time and tools) for the SWD, WS2812B, PS/2 and 1-Wire images, in your own words. -->

### SWD (`swd-read`)

A TX word holds a request header in bits 7..0 (Start, APnDP, RnW, A[2:3],
Parity, Stop, Park, sent LSB first; the host computes the parity) and a
connect flag in bit 8. With the flag set, the image first sends 56 SWCLK cycles
with SWDIO high, the JTAG-to-SWD select sequence 0xE79E (LSB first) and 56
more cycles high (ADIv5.1 Supplement 6.2.1 and 8.3.6: at least 50, then the
line reset). Every request then sends 8 idle cycles (SWDIO low) and the
header, whose last bit (Park, 1 in a valid header) is driven before SWDIO is
released for one turnaround cycle (erratum 2.6), reads ACK[0:2], and after ACK OK reads RDATA[0:31] and the parity bit, clocks
one more turnaround cycle and 8 idle cycles (9 SWCLK rising edges after the
parity bit; 8.2 asks for 8), checks even parity and pushes the data word.
`0x1A5` connects and reads DPIDR; `0x0A5` and `0x08D` read DPIDR and CTRL/STAT
without a new line reset.

- SWDIO changes only while SWCLK is low, 16 clocks before and after each
  SWCLK rising edge; the image samples ACK, data and parity at SWCLK rising
  edges from the pad value 2 clocks earlier. A target must therefore put a bit
  on the pad less than 30 clocks (600 ns at 50 MHz) after the SWCLK rise that
  launched it. `test_swd_target_delay` checks both sides of that limit in
  simulation: a target delay of 29 clocks reads DPIDR, 30 clocks shifts every
  bit by one sample.
- Connect and DPIDR read take 6220 clocks (124.4 µs) from the TX word to the
  RX push; a read without connect 2112 clocks (42.24 µs).
- Errors: an ACK other than OK pushes the three ACK bits (4 OK, 2 WAIT,
  1 FAULT, 7 no response) and faults 80; a data parity error pushes the data
  word and faults 81; a header with RnW = 0 faults 82 before any SWCLK edge.
  Faults release both pins.
- Not supported: write transactions (so no AP access, which needs a SELECT
  write), WAIT retry, the ADIv5.2 dormant-state wake-up, multi-drop
  TARGETSEL. SWCLK is not free-running: it stops low between requests, with
  SWDIO driven high (8.3.2).

### WS2812B (`ws2812`, `ws2812b-v5`)

A TX word is `G<<24 | R<<16 | B<<8 | L`. Each of the 24 bits (MSB first)
starts with a rising edge; `OUT` writes the bit at T0H, so a 0 falls there, and
a `SET` pulls the line low at T1H. The last bit of a pixel is unrolled so the
bit period stays exact across the pixel boundary while the next word is
queued. A nonzero `L` ends the frame with a reset low time.

| | T0H | T1H | T0L | T1L | bit | reset | clock range meeting every limit |
|---|---|---|---|---|---|---|---|
| `ws2812`, clocks | 20 | 40 | 43 | 23 | 63 | ≥ 15004 | |
| at 50 MHz | 400 ns | 800 ns | 860 ns | 460 ns | 1.26 µs | 300.08 µs | 43.00 to 61.43 MHz |
| WS2812B datasheet | 250-550 ns | 650-950 ns | 700-1000 ns | 300-600 ns | 0.65-1.85 µs | > 50 µs | |
| `ws2812b-v5`, clocks | 15 | 32 | 48 | 31 | 63 | ≥ 15004 | |
| at 50 MHz | 300 ns | 640 ns | 960 ns | 620 ns | 1.26 µs | 300.08 µs | 48.00 to 53.45 MHz |
| WS2812B-V5 datasheet V1.0 | 220-380 ns | 580-1000 ns | 580-1000 ns | 580-1000 ns | | > 280 µs | |

Two images are needed: the T1L windows of the two datasheets (300-600 ns and
580-1000 ns) overlap only between 580 and 600 ns, one clock at 50 MHz, so no
single bit period meets both tables with margin. The reset after a latch word
is 15025 clocks (300.50 µs; 15033 for `ws2812b-v5`).

- One TX word is consumed every 1512 clocks (30.24 µs); the TX FIFO holds 8.
  DOUT is low while `PULL` waits, so a late word stretches the last bit's
  low time: by more than 7 clocks it leaves the WS2812B T0L/T1L window (2
  clocks for the V5 T0L window), and beyond the reset time the LEDs latch
  early. The host must keep the FIFO from running empty inside a frame.
- The WS2812B datasheet gives VIH = 0.7 VDD (3.5 V at 5 V), above the 3.3 V
  pad level; the WS2812B-V5 datasheet gives VIH ≥ 2.7 V.

### PS/2 device to host (`ps2-device`)

A TX word is a byte, sent as start 0, 8 data bits LSB first, odd parity and
stop 1. Before each frame the image waits for CLK high and DATA high and then
samples CLK 56 times, 50 clocks apart (55 µs from first to last sample); any
low sample restarts the wait. Within a frame, at 50 MHz:

| | clocks | at 50 MHz | Chapweske |
|---|---:|---|---|
| DATA change to CLK fall | 1000 | 20 µs | 5-25 µs |
| CLK low | 2000 | 40 µs | 30-50 µs |
| CLK rise to DATA change | 1000 | 20 µs | ≥ 5 µs |
| CLK high | 2000 | 40 µs | 30-50 µs |
| clock period | 4000 | 80 µs (12.5 kHz) | 10-16.7 kHz |
| CLK high before the start bit | ≥ 2750 | ≥ 55 µs | ≥ 50 µs |

Every limit holds for clocks from 40 to 55 MHz. The image samples CLK 2 clocks
before each of its 11 CLK falls (the pad value 4 clocks before the fall). If
the host holds CLK low, the image releases both lines and sends the whole frame
again after the idle check; there is no check after the 11th clock, so a host
that inhibits after each byte does not get it twice. The waits for CLK and
DATA high are bounded by LIMIT 16777215 (335.5 ms at 50 MHz): a longer
inhibit or request to send ends in fault 3. The idle check samples the line: a
low pulse shorter than 1 µs between two samples can be missed (a host inhibit
lasts at least 100 µs).

Not supported: receiving host-to-device commands in the same image. The only
TX-queue test of the design-of-record ISA is `PULL`, which blocks, so one
engine cannot wait for a queued byte and a host request to send at the same
time. (The line-unit extension's `LSTAT` bit 3, "TX queue has data", would
allow it; that variant is not the design of record.)

### PS/2 host (`ps2-host`)

A TX word holds a command byte in bits 7..0 and the number of response frames
to read in bits 11..8. The image holds CLK low for 6000 clocks (120 µs; at
least 100 µs), pulls DATA low, releases CLK 250 clocks later, waits until it
sees CLK high, and then puts each of the 10 bits (8 data bits, odd parity,
stop) on DATA one clock after it sees the device pull CLK low; the device
reads it on the rising edge. It then waits for the acknowledge (DATA low), one
more clock pulse and DATA high, releases both lines and reads the response
frames: DATA is sampled one clock after each of the 11 CLK falls is seen, and
each frame is pushed as received (bit 0 start, bits 8..1 data, bit 9 parity,
bit 10 stop); the TT host checks the framing. `0x2FF` sends Reset (0xFF) and
reads two frames (the test's device answers 0xFA, 0xAA).

- Waits during the command are bounded by LIMIT 800000 (16 ms; a device must
  start clocking within 15 ms of the host pulling CLK low), waits for response
  frames by LIMIT 1100000 (22 ms; a response is due within 20 ms). No device,
  no acknowledge or no answer ends in fault 3. Responses use strict `PUSH`:
  more than 8 unread frames end in fault 4.
- Not supported: holding the device inhibited between commands. Frames the
  device sends on its own (key presses) while the image waits for a command
  are not read.
- The first version of this image waited for the device's first CLK fall
  directly after releasing CLK. The input synchronizer still showed the
  image's own inhibit for 3 clocks, so that wait ended at once and every bit
  went out one device clock early, while CLK was high. pe_timing cannot see
  this (the CLK level after the release is set outside the chip); the
  `Ps2Device` peer reported it ("host changed DATA while CLK was high"). The
  image now waits for CLK high after the release.

### 1-Wire (`onewire-master`)

A TX word holds a byte in bits 7..0; bit 8 asks for a reset and presence
detect first. The byte is sent LSB first in 8 time slots that also sample DQ,
and the 8 samples are pushed: a written byte comes back unchanged, and
`0x0FF` reads a byte from the slave. Read ROM is `0x133` followed by eight
`0x0FF` words; the RX words are 0x33, the family code, the 48-bit serial
number and the CRC-8, which the TT host checks. No presence pulse faults 96.

| step | AN126 Table 1 | image, clocks | at 50 MHz | DS18B20 limit |
|---|---|---:|---|---|
| reset low | 480 µs | 25000 | 500 µs | tRSTL 480-960 µs |
| presence sample after release | 70 µs | 3500 | 70 µs (pad value 69.96 µs) | 60-75 µs: low for every presence pulse (tPDHIGH 15-60, tPDLOW 60-240 µs) |
| release to the first slot | 480 µs | 25000 | 500 µs | tRSTH ≥ 480 µs |
| write 1: DQ released | 6 µs | 300 | 6 µs | tLOW1 1-15 µs |
| sample after the fall | 15 µs | 700 | 14 µs (pad value 13.96 µs) | tRDV ≤ 15 µs |
| write 0: DQ released | 60 µs | 3250 | 65 µs | tLOW0 60-120 µs |
| slot | 70 µs | 3750 | 75 µs | tSLOT 60-120 µs |
| recovery | 10 µs | 500 | 10 µs | tREC ≥ 1 µs |

The image follows AN126 except where an AN126 value lies on a DS18B20 limit
(reset low, reset high time, write-0 low time, sample point): there it keeps
a margin, so every limit holds for clocks from 46.64 to 52.08 MHz. A byte
takes 30006 clocks (600.12 µs), a reset and a byte 80005 clocks (1600.1 µs).
DQ is released at every holding point and by fault 96. Not supported:
overdrive speed, the strong pull-up of parasite-powered slaves, Search ROM
(it needs bit-level read-read-write steps, not byte slots) and the extended
presence pulse of the DS2404/DS1994.

### Verification of these images

- **Timing contracts.** `tools/timing/pe_contracts_ext.py` (registered by
  `pe_contracts.py`) rebuilds each image's pin waveform from pe_timing's
  boundary graph and checks it against the image's notes and the cited
  limits: SWD bit sequence at every SWCLK rise on every request path,
  SWCLK phases, host set-up and hold, turnaround placement, sample points and
  the target delay budget; WS2812B bit times, pixel boundary and reset times;
  PS/2 frame timing, idle window, inhibit checks and abort paths; PS/2 host
  request to send, bit and sample placement, handshake and timeouts; 1-Wire
  reset, presence, slot and recovery times and open-drain use. A clock range
  is derived for each. `pe_timing.py report --firmware firmware` runs them
  with the other images; no check of the six images fails.
  `tools/timing/test_pe_contracts_ext.py` holds negative controls: changing
  one instruction (or declaring an out-of-spec value) makes the named check
  fail.
- **Pin-level tests.** `test/test_protocols_ext.py` runs each image in
  lockstep with the reference model and checks the pins with peers written
  from the cited documents, independently of the images and of
  `tools/timing`: an ADIv5 SW-DP target (JTAG-to-SWD detection, line reset,
  reset state, protocol-error and lockout rules, turnaround, contention and
  set-up checks), WS2812B decoders for both datasheet tables (each rejects the
  other image's waveform), a PS/2 host decoder that can inhibit, a PS/2
  device that clocks in commands and answers, and a DS18B20-like 1-Wire slave
  (reset and presence timing, write-slot sampling window, read slots, Read ROM
  with CRC-8), run at the slave-timing corners. `test_four_engines` runs the
  SWD, WS2812B, PS/2 device and 1-Wire images at the same time on the four
  engines and checks that the WS2812B and PS/2 clock counts equal those of the
  single-engine runs. The module is not in `COCOTB_TEST_MODULES`, so neither
  `make` nor the gate-level test runs it. CI runs it on RTL in its own job,
  `protocols-ext`, of [`test.yaml`](../.github/workflows/test.yaml); to run it
  locally:

  ```sh
  cd test
  make COCOTB_TEST_MODULES=test_protocols_ext
  ```

  Its 22 tests take about seven minutes with cocotb 2.0.1 and Icarus
  Verilog 13.0. A 23rd, `test_ps2_host_restricted`, runs only on a design
  that cannot run `ps2-host` unchanged (above) and is skipped on the design
  of record.
- **Ground truth for the static schedules.** pe_timing's validator traced the
  reference model through every scenario of `test_protocols_ext.py` and
  through its random-traffic stress suite on the six images (8 seeds of
  60000 clocks each): all 747 engine runs matched, including all 16552 pad
  changes, every issue attempt and the one LIMIT timeout.
- **Timing certificates.** The campaign on `6a3ea08` certified these six
  images and `uart-rx-idle` (results.md R42c;
  [timing-certificates.md](timing-certificates.md) section 8). Their
  longest segments between boundaries are much longer than those of the
  earlier images, so they are proved as chains of 96-step chunks: 80005 clocks (`onewire-master`), 16514 (`ws2812`,
  `ws2812b-v5`), 6290 (`ps2-host`), 6220 (`swd-read`) and 4000
  (`ps2-device`), most of it spent in `WAIT` and `XFER` countdowns. No
  third-party peer was added for these protocols. Nothing here has run on
  hardware.

**Evidence.** Design of record at `24f31f0`, cocotb 2.0.1 and Icarus Verilog
13.0, Slurm job ids: RTL lockstep run of all 22 tests (24170373; an earlier
run of the first 19 tests: 24169435 to 24169439); validator trace and stress
runs (24170374); `tools/timing` and `tools/timing/cert` unit tests and the
pe_timing report over `firmware/` (24170375); reference-model runs of every
scenario (24169388, 24170172).
