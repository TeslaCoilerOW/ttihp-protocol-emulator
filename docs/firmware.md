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
| `i2c-write` | SCL6/SDA7 | address+W and one byte from TX; ACK checks; STOP |
| `i2c-read` | SCL6/SDA7 | address+R from TX; one RX byte, NACK, STOP |
| `i2c-repeated-start` | SCL6/SDA7 | address+W/register/address+R from TX; repeated START, one-byte read, NACK, STOP; shared transmit routine fits 64 words |
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
