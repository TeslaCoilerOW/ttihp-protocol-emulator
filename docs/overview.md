# Overview

State as of 2026-09-29. This page summarizes what the chip does, how it is
built, and how it compares with RP2040 PIO and with three other public
entries. The verification argument is in [verification.md](verification.md),
the evidence for every number in [results.md](results.md), and the limits in
[limitations.md](limitations.md).

## What the chip does

The chip is a programmable protocol processor for Tiny Tapeout on IHP
SG13CMOS5L. A host loads a program into each of four engines. Each program
implements one side of a serial protocol (UART, SPI, I2C, JTAG) or a timed
waveform on pins that engine owns, out of eight shared bidirectional pins. The
engines run concurrently and signal each other through events. An on-chip mover
forwards received words from one engine to another, so a bridge such as UART
receive into SPI transmit runs without the host. The design of record is the
8x4-tile build, signed off at 15 ns and operated at 50 MHz; 8x4 confirmed by
the organizers (2026-09-28), and a 6x4 build is kept green in CI as a
fallback. There is no silicon and no hardware result yet.

## Architecture in brief

- **Engines.** Each of the four has a private program memory of 64 32-bit
  instructions (two IHP 64×16 SRAM macros), 8-word TX and RX queues, registers
  `tx`, `rx`, `x` and `y`, a 16-bit repeat counter and a 24-bit wait limit.
  Instruction fetch is never shared or arbitrated.
- **Timing.** An ordinary instruction takes one cycle and `WAIT n` takes n + 1.
  An engine's timing can change only at its own stall points: `PULL` on an
  empty queue, a blocking `PUSH` on a full one, `WAITPIN` and `WAITEVENT`.
  `XFER` fixes its bit count (1 to 32), half-period (1 to 255 cycles) and
  clock mode at issue and makes no queue access until it ends.
- **Pins.** The host assigns disjoint pin ownership and open-drain masks while
  an engine is halted; overlapping ownership is rejected. Reset, halt and
  faults release the output enables.
- **Coordination.** Each engine has an event mailbox bit and a programmable
  input trigger, and all share a 32-bit timestamp. The mover has one
  descriptor per source engine, naming a destination TX queue and a finite
  word count; it moves at most one word per clock and serves sources
  round-robin.
- **Faults.** A strict `PUSH` on a full RX queue halts the engine with fault 4
  and keeps the word and PC for the host. `WAITPIN` and `WAITEVENT` time out
  into sticky faults.
- **Host.** A synchronous nibble-wide port on `ui_in`/`uo_out`. Its inputs are
  not synchronized, so the host shares the chip's clock: the host library
  supplies the clock edges while it services the chip, and it cannot service
  queues while the clock free-runs ([extension-study.md](extension-study.md), risk R1).

[architecture.md](architecture.md), [isa.md](isa.md) and the datasheet
[info.md](info.md) have the details.

## Compared with RP2040 PIO

Source: [the RP2040 datasheet][rp2040], build-date 2025-02-20, chapter 3;
section numbers in brackets.

**What is the same.** Small sequencers, each with two shift registers and two
scratch registers, run programs that a host loads at run time. An instruction
takes one cycle unless it stalls [3.2.2]. Blocking `PULL` and `PUSH` on 32-bit
TX and RX FIFOs are stall points [3.2.4]. Programs wait on pins, and units
synchronize through flags: IRQ flags in PIO [3.2.7], event mailboxes here.

| | RP2040 PIO | This chip |
|---|---|---|
| Units | 2 blocks of 4 state machines [3.1] | 4 engines |
| Program store | 32 16-bit slots per block, shared by its 4 state machines, which all read it in the same cycle without stalling [3.2.7, 3.4.1, 3.7] | 64 32-bit instructions per engine in private SRAM, loaded only while that engine is halted |
| Timing in the instruction | 5-bit delay/side-set field; side-set drives pins alongside the main operation [3.4.1, 3.5.1]; a 16.8 fractional clock divider [3.1] | No delay or side-set field and no divider: `WAIT n`, instruction counts and the `XFER` half-period. Programs need more instructions. |
| Shifting to pins | `OUT`/`IN` with autopull/autopush; with autopull, an `OUT` stalls while the OSR is exhausted and the TX FIFO is empty [3.2.4, 3.5.4] | `XFER` commits the whole transfer at issue and makes no queue access until it ends |
| FIFOs | 4 words each way, or 8 in one direction [3.1] | 8 words each way |
| Data between units | State machines cannot pass data to each other [3.2.7]; the system DMA moves FIFO data [2.5] | On-chip mover from an RX queue to a TX queue, with a finite quota |
| Pins | Any state machine can drive any GPIO; on simultaneous writes the highest-numbered one wins [3.1, 3.5.6.1] | Disjoint ownership, set while halted and checked by the host port |
| Full RX FIFO | `PUSH noblock` continues; the word is lost and `FDEBUG_RXSTALL` is set [3.4.6.2] | Strict `PUSH` halts with fault 4 and keeps the word and PC |
| Scale and host | Up to 30 GPIOs, attached to the microcontroller's bus fabric [3.1] | 8 pins at 50 MHz; a synchronous nibble port to an external host |
| Timing evidence | Cycle rules in the datasheet [3.2.2] | Per-image certificates, the isolation proof and netlist equivalence ([verification.md](verification.md)) |

## Compared with other public entries

Checked on 2026-09-28 at the head of each repository: loom at `3832179`
(committed 2026-09-28), MarcosAsh at `f2a093d` (2026-09-28) and kaikino at
`00e5951` (2026-09-21). Each cell cites a file at that commit. These projects
change quickly; check again before quoting.

| | This entry | [loom] | [MarcosAsh] | [kaikino] |
|---|---|---|---|---|
| HDL, tiles, clock | Hardcaml; 8x4 (confirmed by the organizers, 2026-09-28); 50 MHz, signed off at 15 ns | Verilog [L-hdl]; 6x4; 50 MHz [L-tiles], about 42 MHz at the slow corner [L-clk] | Hardcaml [M-hdl]; 6x4; 50 MHz [M-die] | SystemVerilog; 8x4; 40 MHz [K-info] |
| Execution | 4 engines, private fetch | 4 threads on one pipeline, strict round robin [L-exec] | 2 cores [M-exec] | 2 PIO engines [K-exec] |
| Program store | 64 × 32 bits per engine, SRAM | 512 × 16 bits, shared SRAM [L-mem] | 512 words per core, SRAM, plus a shared 512-word data SRAM [M-mem] | 128 × 16 bits per engine, flops [K-tab], [K-flops] |
| Timing in the ISA | `WAIT`, `XFER`; no delay or side-set field | per-thread deadline (`WAITD`) checked by the assembler [L-time] | delay field, side-set of up to 2 pins, deadline register [M-isa], [M-fifo] | one clock per instruction, 12-bit `DELAY` [K-tab], [K-time] |
| Queues | 8 + 8 words per engine | 4 deep [L-fifo] | two 8-deep per core [M-fifo] | one 128-byte host FIFO [K-tab] |
| Between engines | mover, mailboxes | shared flags and memory [L-flags], [L-fifo] | internal wires (OR of drivers), shared data SRAM [M-wires], [M-mem] | a `PEER` input; pin collisions fault [K-peer], [K-pins] |
| Timing proof | per image on the RTL ([timing-certificates.md](timing-certificates.md)), extended by an unbounded isolation proof per engine in which host traffic is free except the commands that control that engine | per-thread isolation on a two-copy miter, with the host debug port quiet [L-iso], [L-quiet] | any program its kernel accepts meets its deadlines, per engine; composing two engines not yet proved; host traffic never reaches the pins [M-kernel], [M-one], [M-host] | nine safety properties proved, including pin sharing [K-formal], [K-pins] |
| Host link | synchronous nibble port; no queue service while the clock free-runs | SPI, SCK sampled by the core clock, at least 8 clocks per period [L-spi] | SPI, oversampled by the core clock, SCK at most clk/8 [M-spi], [M-spi2] | SPI mode 0, oversampled by the core clock [K-spi] |
| Netlist vs RTL | formal sequential equivalence in CI ([equivalence.md](equivalence.md)), gate-level tests | gate-level cocotb suite in CI [L-gl] | gate-level tests; "No proof says it equals the RTL" [M-net] | the same suite on RTL, FPGA and gate-level netlists [K-net] |
| Firmware | UART (including a receiver that tolerates an idle line), SPI and I2C (controller and target), JTAG, SWD reads, WS2812B, PS/2 (device and host), a 1-Wire master, a timed waveform, an event transmitter ([firmware/](../firmware/README.md)); simulation only | 13 programs, including WS2812, PS/2, SWD, CAN and a USB-LS HID mouse [L-fw] | 18, including USB-LS keyboard and mouse (the board answers control requests), 10BASE-T UDP transmit, 1-Wire, PS/2, WS2812 [M-fw], [M-usb] | 20 microprograms, including SWD, PS/2, CAN transmit, USB-LS packets and Manchester Ethernet frames [K-ex], [K-lim] |
| Other | | | | delay lines timing edges to about 0.22 ns; a trace buffer [K-tdc] |
| Hardware runs | none | none [L-hw] | none [M-hw] | not stated in the [README][K-readme] or [submission.md][K-sub] |

**Where this entry is behind.**

- Protocol breadth: the design of record has no CAN or USB firmware (the
  extension variant below adds the line unit they need), and its SWD image
  only reads. The SWD, WS2812B, PS/2 and 1-Wire images, added in `e64cd6b`,
  are checked against peers written in this repository, not third-party
  ones, and have no timing certificate yet.
- Host link: serving queues needs the host to clock the chip, while the other
  three have SPI host ports oversampled by the core clock [L-spi], [M-spi2],
  [K-spi].
- Generality of the timing proof: certificates are per image. The 19
  images committed at `24f31f0` are certified; the 7 added in `e64cd6b` are
  not yet, and a cluster campaign on `6a3ea08` is to certify them.
  MarcosAsh's kernel covers every accepted program on one engine.
- Code density: 32-bit instructions without delay or side-set fields, 64 per
  engine; the others use 16-bit instructions [L-isa], [M-isa], [K-exec] and
  128 or 512 words.
- Oracle: the Python reference model was written from the same specification
  as the RTL; the third-party peers in `test_ext/` are the independent checks.

**Where it differs in its favour.** Of the four, it is the only one whose
documents cited here describe a formal proof that the hardened netlist is
sequentially equivalent to the RTL, and that proof runs in CI. Its isolation
proof leaves the host port free except for the commands that control the
engine under proof, where loom's holds with the debug port quiet. It has four
engines with independent fetch and a hardware mover between their queues.

## Extension variant: an open option

`diet8_rec16` adds a line unit to each engine: a bit ticker with an 8-bit
fraction, NRZ/NRZI/Manchester (transmit) coding, bit stuffing, a complementary
pin pair with SE0, an arbitration monitor and a 16-bit CRC. With it, the
firmware in `firmware/ext/` sends a 10BASE-T UDP frame, runs a subset of a CAN
2.0A node and answers a USB low-speed IN token, all in simulation of the whole
chip. The variant passed the official `gds`, `precheck` and `gl_test` actions
on branch `eval/diet8-rec16`, and its official netlist is equivalent to its
RTL. It is not the design of record. Adopting it changes `src/` and the ISA
version. It was built at 8x4, the tile size the organizers confirmed
(2026-09-28); on the 6x4 fallback the study keeps only a degraded form with
2-word queues. The decision is open
([extension.md](extension.md) section 9, [extension-study.md](extension-study.md) section 9).

[rp2040]: https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf
[loom]: https://github.com/thomasgilbert481/tt_um_loom/tree/38321793ba9117411a74474187ae3a31c12bd55d
[MarcosAsh]: https://github.com/MarcosAsh/protocol-emulator/tree/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0
[kaikino]: https://github.com/kaikino/core-asic/tree/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185
[L-isa]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/docs/ARCHITECTURE.md#L350
[L-exec]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/README.md#L9-L14
[L-clk]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/README.md#L28
[L-tiles]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/info.yaml#L8-L13
[L-hdl]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/README.md#L197-L200
[L-mem]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/docs/ARCHITECTURE.md#L34
[L-time]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/README.md#L69-L75
[L-fifo]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/docs/ARCHITECTURE.md#L522-L524
[L-flags]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/docs/ARCHITECTURE.md#L120-L122
[L-iso]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/README.md#L31
[L-quiet]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/README.md#L130-L132
[L-spi]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/docs/HOST_PROTOCOL.md#L17-L18
[L-gl]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/docs/VERIFICATION_REPORT.md#L42
[L-fw]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/README.md#L94-L106
[L-hw]: https://github.com/thomasgilbert481/tt_um_loom/blob/38321793ba9117411a74474187ae3a31c12bd55d/README.md#L122-L125
[M-spi2]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/src/host_spi.mli#L1-L2
[M-hdl]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L3-L4
[M-exec]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L8
[M-die]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L195
[M-mem]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/docs/info.md#L12-L17
[M-isa]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/docs/info.md#L22-L23
[M-fifo]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/docs/info.md#L30-L34
[M-wires]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/docs/info.md#L63-L65
[M-spi]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/docs/info.md#L67-L68
[M-kernel]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L97-L120
[M-one]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L160-L163
[M-host]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L90-L92
[M-net]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L185-L186
[M-fw]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L40-L65
[M-usb]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L211-L213
[M-hw]: https://github.com/MarcosAsh/protocol-emulator/blob/f2a093d3b79f5249ff23ad85d6ed4f0c395401f0/README.md#L189
[K-exec]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/README.md#L5-L9
[K-tab]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/README.md#L13-L22
[K-info]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/info.yaml#L7-L11
[K-flops]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/README.md#L63-L64
[K-time]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/README.md#L53-L54
[K-peer]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/docs/isa.md#L42
[K-pins]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/docs/submission.md#L24-L28
[K-formal]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/docs/submission.md#L80-L82
[K-spi]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/docs/submission.md#L34-L36
[K-net]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/docs/submission.md#L83-L85
[K-ex]: https://github.com/kaikino/core-asic/tree/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/examples
[K-lim]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/docs/submission.md#L108-L110
[K-tdc]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/README.md#L18-L20
[K-readme]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/README.md
[K-sub]: https://github.com/kaikino/core-asic/blob/00e5951dbe5b5ec29ebcbf44adaed7978a6d2185/docs/submission.md
