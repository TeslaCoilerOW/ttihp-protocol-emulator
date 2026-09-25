## How it works

This design is a small programmable protocol processor. Four independent
engines run their own programs at the same time. Between them they drive the
eight bidirectional pins `uio[7:0]`. Each engine is loaded with a program for
one protocol, such as UART, SPI, I2C, JTAG or a custom timed waveform. No
protocol is hardwired: every protocol is an ordinary program in the instruction
set described below. A synchronous, nibble-wide host port on `ui_in` and
`uo_out` loads programs, fills and drains queues, starts and stops engines and
reads status.

This chip's configuration is 4 engines, a 32-bit datapath, 64 instructions per
engine and 8-word queues. It uses fused issue, so the `XFER` instruction is
available, and it has no prefetch. The ISA version register reads 2.

### Engines

Each engine has:

- A private program memory of 64 32-bit instructions. It is made from two IHP
  `RM_IHPSG13_1P_64x16_c2` SRAM macros per engine, eight macros in all.
  Instruction fetch is never shared or arbitrated between engines.
- A TX queue (host or mover to engine) and an RX queue (engine to host or
  mover). Each holds eight 32-bit words.
- Registers `tx`, `rx`, `x` and `y`. They are 32 bits wide and arithmetic
  wraps.
- A 16-bit repeat counter, a 24-bit interval/wait limit and local control state.
- One event mailbox bit and one programmable input trigger.

Instructions commit on rising clock edges. An ordinary instruction takes one
cycle, and `WAIT n` takes n + 1 cycles, so a program's pin timing is an exact
count of system clocks. Instruction fetch is private. Apart from `START`,
`STOP` and `BEGIN` (which stops the selected engine), the host port and the
queue mover never delay an engine's instructions.
A program's timing can change only at its own explicit stall points:

- `PULL` on an empty TX queue;
- a blocking `PUSH` on a full RX queue;
- `WAITPIN`, which waits for an input pin;
- `WAITEVENT`, which waits for a mailbox event.

### Pins

- Pin ownership is set with the `OWN` host command while the engine is halted.
  Ownership is disjoint between engines. An engine may drive, or change the
  output enable of, only the pins it owns. Any other write faults. Any engine
  may read any pin.
- Owned pins can be made open-drain. An open-drain pin's output value is always
  0, and its output enable is the logical enable AND NOT the logical value. A
  logical 1 therefore releases the pin. I2C uses this mode, with external
  pull-ups.
- Inputs pass through two flip-flops before the engines see them.
- Outputs are masked by ownership, by run state and by fault. A halted or
  faulted engine releases all its output enables.

### Timed transfers (XFER)

`PINS` selects the clock, TX and RX pins. `XFER` then performs a complete
synchronous serial transfer (SPI-style). It takes a bit count of 1 to 32, a
half-period of 1 to 255 cycles, clock polarity, clock phase and bit order, and
separate drive and sample enables. There are exactly 2 × (bit count) clock
transitions, each separated by the half-period. `XFER` does not touch the
queues while it runs. Its edges therefore stay equally spaced even when the
host or other engines saturate a queue. Firmware moves data between the queues
and `tx`/`rx` with `PULL` and `PUSH` before and after the transfer.

### Queues and the autonomous mover

The host writes TX words through window 2 and reads RX words through window 3.
An engine uses `PULL` to move the head of its TX queue into `tx`, and `PUSH`
to append `rx` to its RX queue:

- `PUSH` with a = 0 blocks while the RX queue is full.
- `PUSH` with a = 1 (strict) faults with code 4 instead. The rejected word
  stays in `rx`, where the host can read it (READ_SELECT 6).

The mover forwards words between engines without the host. The `ROUTE`
command gives each source engine one descriptor: a destination engine and a
16-bit word count. The mover works as follows:

- An accepted move pops the source's RX head, pushes the same word into the
  destination's TX queue and decrements the count.
- At most one word moves per clock. Sources are served round-robin.
- Host TX writes have priority over the mover for the same destination.
- A host read in window 3 reserves the selected engine's RX queue.

The flagship example below uses the mover to bridge UART receive into SPI
transmit.

### Events, triggers and the timestamp

- Each engine has one mailbox bit. It can be set in three ways, and the three
  are ORed:
  - `SIGNAL` from any engine;
  - the host `EVENT` command;
  - the engine's input trigger.
- `WAITEVENT` consumes the bit, or waits for it (bounded by `LIMIT`). If a new
  event arrives on the same edge, the bit stays pending. Pending bits coalesce;
  they do not count events.
- The input trigger is configured with the host `TRIGGER` command. It watches
  one pin for a rising edge, a falling edge, a high level or a low level.
- Triggers work while the engine is waiting, transferring, stalled, halted or
  faulted. They do not need pin ownership.
- For an input change that is stable before a clock edge, the mailbox is set on
  the third edge.
- A 32-bit timestamp counts system clocks and is common to all engines. `TIME`
  copies it into a register, and the host reads it with READ_SELECT 1.

### Faults and reset

A fault stops the engine and releases its output enables. The engine keeps the
fault code until the host sends `CLEAR` (while the engine is halted) or resets
the chip. The built-in codes are:

| Code | Meaning |
|-----:|---------|
| 1 | invalid opcode, operand or pin ownership |
| 2 | PC outside the committed image |
| 3 | bounded-wait timeout (`WAITPIN` or `WAITEVENT` exceeded `LIMIT`) |
| 4 | strict RX enqueue overflow (`PUSH` with a = 1 on a full RX queue) |

`FAULT n` stops with code n, which can be 1 to 255. The example firmware uses
codes 64 to 67 for protocol errors, such as a UART framing error or an I2C
NACK. An invalid host command is rejected with no effect, and it sets a sticky
host fault. The host fault is cleared by reset, or by `CLEAR` with payload bit
23 set. `uo[7]` is high while any engine has a fault or the host fault is set.

Reset (`rst_n` low) or deselection (`ena` low) stops all engines and releases
every output enable. It also invalidates all program images and clears the
queues, the mailboxes and the control state, including routes and trigger
configuration. The program memory itself is not reset, so reload the images
after every reset.

## Host interface

### Pins

| Pin | Dir | Function |
|-----------|-----|------------------------------------------------------|
| `ui[3:0]` | in | write nibble |
| `ui[4]` | in | write-valid |
| `ui[5]` | in | read-ready |
| `ui[7:6]` | in | window select (0 to 3) |
| `uo[3:0]` | out | read nibble |
| `uo[4]` | out | write-ready |
| `uo[5]` | out | read-valid |
| `uo[6]` | out | IRQ: any event mailbox pending, or any RX queue non-empty |
| `uo[7]` | out | FAULT: any engine fault, or the sticky host fault |
| `uio[7:0]` | in/out | protocol pins 0 to 7 |

The host port is synchronous to `clk`, and `ui_in` is not synchronized. The
host must therefore share the chip's clock and change `ui_in` only between
rising edges.

A nibble transfers on a rising edge where ready and valid are both high. A
32-bit word is eight nibbles, least significant nibble first. For example,
the command word `0x0400000F` is sent as F, 0, 0, 0, 0, 0, 4, 0.

`uo[4]` and `uo[5]` drop immediately when the window bits change. Sample
`uo_out` after driving `ui_in` and before the rising edge. The read nibble has
no defined value while read-valid is low.

### Windows

| Window | Write | Read |
|-------:|------------------------------------|------------------------------------|
| 0 | command word | status word chosen by READ_SELECT |
| 1 | next program word of the selected engine (address auto-increments) | none |
| 2 | TX queue word of the selected engine | none |
| 3 | none | RX queue word of the selected engine, popped when its last nibble is accepted |

Write-ready depends on the window:

| Window | Write-ready is high |
|-------:|---------------------|
| 0 | always |
| 1 | only while the selected engine is halted, after `BEGIN`, before `COMMIT` and below 64 words |
| 2 | only while that engine's TX queue has space |
| 3 | never |

Reset, deselection and the window-change bubble also hold write-ready low.

Changing the window abandons any partly transferred word, with no side effect.
The cycle in which the window bits change is a bubble: ready and valid are
both low. In the next cycle, write-ready can be high, so the first write nibble
can be accepted at the end of that cycle. A read word is captured at the end
of that same cycle, and read-valid rises one cycle later. A read word is a
snapshot: it stays stable until all eight nibbles are accepted, however long
the host pauses.

A full queue does not accept a push on the same edge that first frees space.
An empty queue does not allow a pop on the same edge as its first push.

The timing sketch below writes one TX word in window 2 and then reads one
status word in window 0. Each column is one clock cycle. It shows what the host
drives and samples before the rising edge at the end of that cycle.

```
cycle      0   1   2  ..   8   9  10  11  12  ..  18
ui[7:6]    2   2   2       2   0   0   0   0       0
ui[4] WV   0   1   1       1   0   0   0   0       0
ui[3:0]    -  d0  d1      d7   -   -   -   -       -
uo[4] WR   0   1   1       1   0   1   1   1       1
ui[5] RR   0   0   0       0   0   1   1   1       1
uo[5] RV   0   0   0       0   0   0   1   1       1
uo[3:0]    -   -   -       -   -   -  q0  q1      q7
           ^ bubble            ^ bubble, then capture in cycle 10
```

### Commands (window 0 writes)

A command word has the opcode in bits 31:24 and the payload in bits 23:0. Any
payload bit not listed below must be zero. An "engine mask" has bit i set for
engine i, using bits 3:0.

| Op | Command | Payload | Effect and conditions |
|---:|-------------|--------------------------|---------------------------------------------------|
| 0 | SELECT | bits 1:0 engine | Selects the engine used by windows 1 to 3, per-engine reads and the commands below. |
| 1 | BEGIN | none | Stops the selected engine, invalidates its image and sets the program write address to 0. |
| 2 | COMMIT | bits 15:0 length | Accepts the image only if exactly that many words were written since `BEGIN`. The length must be 1 to 64. |
| 3 | OWN | bits 7:0 owned pins, bits 15:8 open-drain mask | Halted engine only. Rejected if ownership overlaps another engine or open-drain bits fall outside ownership. |
| 4 | START | engine mask | Starts all the selected engines on the same edge. Each must have a committed image and no fault. Resets PC, registers, timers, repeat state and logical outputs. Keeps queues, ownership, open-drain configuration and images. |
| 5 | STOP | engine mask | Stops the engines and releases their output enables. |
| 6 | ROUTE | bits 1:0 source, bits 3:2 destination, bit 4 enable, bits 20:5 word count | Sets the source's single mover descriptor. Enable 0 or count 0 disables it. Self-routes are legal. |
| 7 | CLEAR | engine mask; bit 23 clears the host fault | Clears engine faults. The engines must be halted. |
| 8 | READ_SELECT | bits 7:0 index 0 to 7 | Chooses the window 0 read word (see the next table). |
| 9 | EVENT | engine mask | Sets those engines' mailboxes. |
| 10 | FLUSH | none | Empties the selected engine's queues and disables every route touching it. Halted engine only. |
| 11 | TRIGGER | bits 2:0 pin, bits 4:3 mode (0 rising, 1 falling, 2 high, 3 low), bit 5 enable | Configures the selected engine's input trigger. Halted engine only. |

### READ_SELECT (window 0 reads)

| Index | Word read in window 0 |
|------:|----------------------------------------------------------------|
| 0 | Status of the selected engine: bit 0 running, bit 1 committed, bit 2 stalled, bit 3 fault, bits 15:8 fault code |
| 1 | Common 32-bit timestamp |
| 2 | Queue levels of the selected engine: TX in bits 15:0, RX in bits 31:16 |
| 3 | PC of the selected engine |
| 4 | Event pending for the selected engine (bit 0) |
| 5 | Completed-instruction count of the selected engine |
| 6 | Held RX register (`rx`) of the selected engine, for example a word rejected by a strict `PUSH` |
| 7 | ISA version (2) |

The status word is snapshotted when it is first presented. After a
READ_SELECT, a word captured earlier may still be waiting. Switch to window 1
for one cycle and then back to window 0 to capture a fresh word.

### Loading and starting

1. `SELECT` the engine, then send `BEGIN`.
2. Write every program word in window 1.
3. Send `OWN`, then `COMMIT` with the image length.
4. Repeat steps 1 to 3 for each engine.
5. Prefill TX queues (window 2) for programs whose first action is a `PULL`.
6. Set up routes.
7. Start all engines together with one `START` mask.

`START` does not clear the queues. The assembled images in `firmware/*.image.json`
carry the words, the owned pins and the open-drain mask for each engine.

## ISA quick reference

Every instruction is 32 bits. The fields are op = bits 31:24, a = bits 23:16,
b = bits 15:8 and c = bits 7:0. Immediates are imm24 = bits 23:0 and
imm16 = bits 15:0. Unused operands must be zero. Registers are numbered
0 = `tx`, 1 = `rx`, 2 = `x` and 3 = `y`. Pins are numbered 0 to 7.

Timing and faults:

- Each non-blocking instruction takes one cycle. `WAIT n` holds for n more
  cycles, and `XFER` runs until its last transition.
- Unlisted opcodes fault with code 1.
- A PC outside the committed image faults with code 2.
- An open-drain pin's logical 1 releases the pin.

| Op | Mnemonic | Operands | Operation |
|---:|-----------|----------------------------|------------------------------------------------|
| 0 | NOP | none | no operation |
| 1 | HALT | none | stop and release output enables; queues and diagnostic registers are kept |
| 2 | SET | imm24: low 8 bits; high 16 bits zero | set the logical pin values (owned pins only) |
| 3 | DIR | imm24: low 8 bits; high 16 bits zero | set the logical output enables (owned pins only) |
| 4 | WAIT | imm24 = n | advance PC, then hold for n additional cycles |
| 5 | JMP | imm24 = target | jump to target PC |
| 6 | PULL | none | `tx` := TX queue head; blocks, with no state change, while the queue is empty |
| 7 | PUSH | a = 0 or 1; b = c = 0 | append `rx` to the RX queue; when it is full, a = 0 blocks and a = 1 faults with code 4 |
| 8 | OUT | a = pin; b = 0; c = direction | drive the pin from `tx` and shift `tx`; c = 0: bit 0, shift right; c = 1: MSB, shift left |
| 9 | IN | a = pin; b = 0; c = direction | c = 0: shift `rx` right and insert the input at the MSB; c = 1: shift left and insert at bit 0 |
| 10 | COUNT | imm16 = n; a = 0 | load the repeat counter; `COUNT n` gives n + 1 loop iterations |
| 11 | LOOP | imm24 = target | if repeat is not 0, decrement it and jump; otherwise continue |
| 12 | LIMIT | imm24, nonzero | maximum blocked cycles for `WAITPIN` and `WAITEVENT` (default 65535) |
| 13 | WAITPIN | a = pin; b = expected bit; c = 0 | continue when the synchronized pin equals b (one cycle if it already does); code 3 after `LIMIT` consecutive unsuccessful samples |
| 14 | SIGNAL | imm24: engine mask | set the recipients' event mailboxes |
| 15 | WAITEVENT | none | consume this engine's pending mailbox bit, or wait (bounded by `LIMIT`) |
| 16 | PINS | imm24: bits 2:0 clock, 5:3 TX, 8:6 RX pin | select the pins used by `XFER` |
| 17 | XFER | a = bits 1 to 32; b = half-period 1 to 255; c = flags | timed serial transfer; c bit 0 CPOL, 1 CPHA, 2 MSB-first, 3 drive, 4 sample; other c bits zero |
| 18 | MOV | a = dest; b = source; c = 0 | dest := source |
| 19 | LOAD | a = dest; imm16 | dest := imm16, zero-extended |
| 20 | ADD | a = dest; b = source; c = 0 | dest := dest + source |
| 21 | XOR | a = dest; b = source; c = 0 | dest := dest XOR source |
| 22 | AND | a = dest; b = source; c = 0 | dest := dest AND source |
| 23 | OR | a = dest; b = source; c = 0 | dest := dest OR source |
| 24 | SHL | a = reg; b = 0; c = count | shift left by c (c less than 32) |
| 25 | SHR | a = reg; b = 0; c = count | shift right by c (c less than 32) |
| 26 | JZ | a = reg; imm16 = target | jump to target if the register is zero |
| 27 | NOT | a = reg; b = c = 0 | invert the register |
| 28 | TIME | a = reg; b = c = 0 | reg := common timestamp |
| 29 | FAULT | imm24: low 8 bits nonzero; high 16 bits zero | stop with that fault code |

`XFER` details:

- On issue, it sets the clock pin to its idle level. With CPHA 0 and drive
  enabled, it also presents the first data bit.
- It then waits the half-period before the first transition.
- With CPHA 0, data is sampled on active edges and shifted out on idle edges.
  With CPHA 1, data is driven on active edges and sampled on idle edges.
- MSB-first samples by shifting `rx` left; LSB-first samples by shifting `rx`
  right.
- The PC advances at the final idle edge.
- When drive is enabled, the clock and TX pins must differ, or the engine
  faults with code 1 before any transition.

## Example: UART TX, SPI and I2C at the same time

This is the flagship scenario, `firmware/flagship-scenario.json`. Four engines
run concurrently on all eight pins, and the mover forwards received UART bytes
to the SPI engine without host service.

| Engine | Firmware | Pins | OWN owned / open-drain | Words |
|-------:|-------------------------|-------------------------------------|---------------|------:|
| 0 | `uart-tx` | TX on 0 | 0x01 / 0x00 | 12 |
| 1 | `uart-rx` | RX on 1 (input only) | 0x00 / 0x00 | 18 |
| 2 | `spi-controller-mode0` | SCK 2, MOSI 3, MISO 4 (input), CS_N 5 | 0x2C / 0x00 | 11 |
| 3 | `i2c-write` | SCL 6, SDA 7 (open-drain) | 0xC0 / 0xC0 | 64 |

The images are assembled for a 50 MHz clock. UART runs 8N1, LSB first, at 64
clocks per bit (781,250 baud). SPI mode 0 uses 32-cycle half-periods, MSB
first, with one CS_N assertion per byte.

Host sequence:

| Step | Window | Words | Meaning |
|-----:|-------:|-----------------------------------------|---------------------------------------|
| 1 | 0, 1, 0 | `0x08000007`, one cycle in window 1, then read window 0 | READ_SELECT 7: the version must read 2 |
| 2 | 0 | `0x00000000`, `0x01000000` | SELECT 0, BEGIN |
| 3 | 1 | 12 words from `uart-tx.image.json` | program engine 0 |
| 4 | 0 | `0x03000001`, `0x0200000C` | OWN pin 0, COMMIT 12 |
| 5 | 0, 1, 0 | `0x00000001`, `0x01000000`, 18 words, `0x03000000`, `0x02000012` | engine 1: `uart-rx`, owns no pins |
| 6 | 0, 1, 0 | `0x00000002`, `0x01000000`, 11 words, `0x0300002C`, `0x0200000B` | engine 2: `spi-controller-mode0` |
| 7 | 0, 1, 0 | `0x00000003`, `0x01000000`, 64 words, `0x0300C0C0`, `0x02000040` | engine 3: `i2c-write`, pins 6 and 7 open-drain |
| 8 | 0, 2 | `0x00000000`, then `0x43 0x4F 0x4E 0x43 0x55 0x52 0x52 0x45` | engine 0 TX: "CONCURRE" |
| 9 | 0, 2 | `0x00000003`, then `0x84 0x5A` | engine 3 TX: address 0x42 + write, then data 0x5A |
| 10 | 0 | `0x06000119` | ROUTE engine 1 to engine 2, 8 words |
| 11 | 0 | `0x0400000F` | START engines 0 to 3 on the same edge |

What happens next:

- Engine 0 transmits "CONCURRE" on pin 0.
- A UART peer sends 8 bytes into pin 1 ("JANESTRT" in the test). Engine 1
  receives each byte with a strict `PUSH`. The mover forwards each byte to
  engine 2's TX queue.
- Engine 2 sends each byte on SPI and queues the byte returned on MISO in its
  RX queue.
- Engine 3 writes 0x5A to the I2C target at address 0x42, checks both ACKs and
  sends STOP. A NACK stops engine 3 with fault code 65.
- IRQ (`uo[6]`) is high while any RX queue holds data, which here is mainly
  engine 2's SPI responses. `SELECT 2`, then read window 3 eight times to
  collect them. READ_SELECT 2
  shows queue levels, and READ_SELECT 0 shows each engine's status.
- `uart-rx` sets `LIMIT` to 12 bit periods (768 cycles). If the line is not
  idle, or no start bit arrives, within that time, it faults with code 3. When
  the transfer is done, send STOP `0x0500000F`, or use a receiver with a longer
  `LIMIT`.

The same scenario runs in simulation as `test/test_flagship.py`, with UART,
SPI and I2C peer models on the pins.

## How to test

### Simulation

```
cd test
pip install -r requirements.txt
make clean
make
```

The cocotb suite runs the RTL (with the IHP SRAM behavioral models) in
lockstep with an independent Python reference model of the ISA, which is in
`test/model`. It covers loading, status, faults, UART, SPI, I2C, the flagship
scenario and constrained-random programs. The Tiny Tapeout gl_test action
copies the hardened netlist to `test/gate_level_netlist.v` and runs the suite
at gate level with `make GATES=yes`. Because gate-level simulation is much
slower, that run replays 8 of the 25 legacy workloads and 2 random cases.

### Tiny Tapeout demo board

The demo board's RP2040 or RP2350 drives `ui_in` and reads `uo_out` from
MicroPython. A host library with the same API on a Raspberry Pi Pico and on the
demo board is planned. Until it exists, the sketch below implements the host
protocol with the Tiny Tapeout MicroPython SDK. It has not yet been run on
hardware.

The host port is synchronous, so the RP2 supplies every clock edge with
`clock_project_once()`. It sets `ui_in` before each edge and samples `uo_out`
before each edge.

```python
from ttboard.demoboard import DemoBoard
from ttboard.mode import RPMode
tt = DemoBoard.get()
tt.mode = RPMode.ASIC_RP_CONTROL    # the RP2 drives ui_in
tt.shuttle.tt_um_teslacoilerow_protocol_emulator.enable()
tt.clock_project_stop()          # the host supplies every clock edge
tt.uio_oe_pico.value = 0         # RP2 leaves the protocol pins alone

window = 0

def cycle(ui):
    tt.ui_in.value = ui
    uo = int(tt.uo_out.value)    # outputs before the rising edge
    tt.clock_project_once()
    return uo

def set_window(w):
    global window
    if w != window:
        window = w
        cycle(w << 6)            # bubble cycle: ready and valid are low

def write_word(w, word, limit=100000):
    set_window(w)
    for n in range(8):
        ui = (w << 6) | 0x10 | ((word >> (4 * n)) & 0xF)
        for _ in range(limit):
            if cycle(ui) & 0x10:  # write-ready was high: nibble accepted
                break
        else:
            raise RuntimeError("write-ready stayed low")

def read_word(w, limit=100000):
    set_window(w)
    word = 0
    for n in range(8):
        for _ in range(limit):
            uo = cycle((w << 6) | 0x20)
            if uo & 0x20:        # read-valid was high: nibble accepted
                word |= (uo & 0xF) << (4 * n)
                break
        else:
            raise RuntimeError("read-valid stayed low")
    return word

def command(op, payload=0):
    write_word(0, (op << 24) | payload)

def read_select(index):
    command(8, index)
    set_window(1)                # drop a stale snapshot
    return read_word(0)

tt.reset_project(True)
for _ in range(4):
    cycle(0)
tt.reset_project(False)
print("ISA version", read_select(7))   # expect 2
```

Load the example above with `command`, and use `write_word(1, ...)` for program
words and `write_word(2, ...)` for TX words. Protocol timing is counted in
system clocks, so each protocol runs at the clock rate the host provides. To
run the engines at a steady rate, you can use this procedure. It has not been
tried on hardware.

1. Load and start the engines with manual clocking.
2. Hold `ui_in` at 0 (window 0, no valid, no ready).
3. Run `clock_project_PWM(frequency)`.
4. Call `clock_project_stop()` before any further host transfer.

The assembler (`hardcaml/bin/assemble.exe`) rebuilds the example firmware for
other bit rates. `--half-period` sets the timing, and `uart-tx` uses a bit
period of exactly 2 × half-period clocks. For example, `--half-period 217`
gives 434 clocks per bit, which is 115,207 baud at 50 MHz.

## External hardware

- Host: none beyond the Tiny Tapeout demo board. Its RP2040/RP2350 drives the
  host port.
- Protocol peers on the `uio` PMOD header, for the example:
  - a UART adapter (chip TX on pin 0, chip RX on pin 1);
  - an SPI target (SCK 2, MOSI 3, MISO 4, CS_N 5);
  - an I2C target at address 0x42 (SCL 6, SDA 7).
- I2C needs pull-up resistors from SCL and SDA to the chip's I/O supply, for
  example 4.7 kΩ. The chip only pulls these pins low or releases them.
- Check the demo board documentation for the I/O voltage. Peripherals at a
  different voltage need level shifting. Never pull an open-drain pin up to a
  higher voltage than the chip's I/O supply.
- A logic analyzer on `uio[7:0]` is the easiest way to watch the concurrent
  protocols.

## Limitations

- There is no silicon yet. The design has not yet passed the Tiny Tapeout gds
  flow, and 50 MHz is the design target, not a measured frequency. A local
  post-route timing analysis meets 50 MHz at the typical and fast corners but
  not at the slow corner, where the reset input path is the limit.
- The host port is synchronous and its inputs are not synchronized, so the host
  must share the chip clock.
- UART has no flow-control wire. When engine 1's RX queue is full, the strict
  `PUSH` halts `uart-rx` with fault code 4. It keeps that byte in `rx`, where
  READ_SELECT 6 reads it. Read it before `CLEAR` and `START`, because `START`
  resets the datapath registers. Bytes that arrive after the fault are lost, so the
  sender must not send faster than the host or the mover drains the queue.
- `WAITPIN` and `WAITEVENT` are bounded. They fault with code 3 after `LIMIT`
  blocked cycles. `LIMIT` defaults to 65535 cycles (about 1.3 ms at 50 MHz),
  and the maximum is 16,777,215. `uart-rx` waits at most 12 bit periods for an
  idle line or a start bit. `PULL` and a blocking `PUSH` wait with no limit.
- Mailboxes are single pending bits, so repeated events coalesce.
- Inputs are sampled through a two-flip-flop synchronizer. Pulses must satisfy
  that sampled-input contract; asynchronous pulse capture is not supported. SPI
  return data and target modes need timing margin. No maximum external rate is
  claimed.
- The I2C controller examples support only a single controller: there is no
  arbitration. Clock stretching is supported, with a bounded wait. The I2C
  target examples need a dedicated point-to-point bus and at least 16 clocks
  per SCL phase. They also need a controller that honors clock stretching and
  a STOP between transactions. The SPI target
  examples need at least 8 system clocks per external half-period.
- Each engine holds 64 instructions, and each queue holds 8 words. Programs
  cannot be changed while an engine runs.
- Reset and deselection invalidate all program images. Reload after each one.
- The example firmware implements the protocol subsets listed in
  `docs/firmware.md` in the project repository. A protocol name does not imply
  every optional feature of that standard.
