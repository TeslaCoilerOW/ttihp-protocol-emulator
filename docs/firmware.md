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
| `uart-rx` | RX1 | 8N1, centered sampling, low-byte RX words, framing fault64 |
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
without peer flow control. RX idle/start waits are bounded
at twelve bit periods; use a custom LIMIT for longer idle intervals.

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
