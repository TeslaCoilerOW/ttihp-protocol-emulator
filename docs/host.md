# Host library (`host/pe_host`)

`pe_host` drives the chip's nibble host port exactly as [isa.md](isa.md)
("Host interface") specifies. One API, `ProtocolEmulator`, runs unchanged on:

| Backend | Where | Module |
|---|---|---|
| Tiny Tapeout demo board | MicroPython on the board's RP2350 (or RP2040), through the Tiny Tapeout SDK (`ttboard`) | `pe_host.ports.ttboard.DemoBoardPort` |
| Raspberry Pi Pico or Pico 2 wired to an FPGA running the design | MicroPython, raw `machine.Pin` / SIO registers | `pe_host.ports.pico.PicoPort` |
| Reference model | CPython: `test/model/reference.py`, one clock per call | `pe_host.ports.model.ModelPort` |
| RTL or gate-level netlist in cocotb | CPython: cocotb bridge thread, lockstep with the model | `pe_host.ports.cocotb_port.CocotbPort` |
| Recorded trace | any interpreter: differential replay | `pe_host.ports.replay.ReplayPort` |

The same scripts run everywhere. `host/examples/selftest_demo.py` exercises
every host command through the host port alone. `host/examples/flagship_demo.py`
runs [`firmware/flagship-scenario.json`](../firmware/flagship-scenario.json):
UART TX, UART RX, SPI and I2C on four engines at once, plus the autonomous
UART-RX to SPI-TX route.

**Status.** No hardware exists yet. Nothing here has run on a demo board, a
Pico or an FPGA. The demo-board and Pico backends ran unmodified against
register- and GPIO-level fakes of the SDK and of `machine`, with the reference
model behind the pins. The library also ran under the MicroPython 1.29 unix
port. Both are listed under [Verification](#verification-of-the-library).

## Contents

1. [Quick start](#quick-start)
2. [Wiring](#wiring)
3. [How one host clock works](#how-one-host-clock-works)
4. [API reference](#api-reference)
5. [Running the same scripts on silicon](#running-the-same-scripts-on-silicon)
6. [Verification of the library](#verification-of-the-library)
7. [Sources](#sources)

## Quick start

CPython, reference model (from the repository root):

```sh
python3 host/examples/selftest_demo.py      # 73 checks, every host command
python3 host/examples/flagship_demo.py      # the flagship scenario with software peers
make -C host test                           # the library's unit tests
```

```python
import sys; sys.path.insert(0, "host")
from pe_host import ProtocolEmulator, FirmwareImage
from pe_host.ports.model import ModelPort

pe = ProtocolEmulator(ModelPort())
pe.reset()
pe.check_isa()                                    # READ_SELECT 7 == 2
pe.load_image(FirmwareImage.load("firmware/uart-tx.image.json"))
pe.tx_write([0x48, 0x69], engine=0)               # prefill engine 0's TX FIFO
pe.start(0b0001)
pe.run(2000)                                      # 2000 host clocks, raises on FAULT
print(pe.status(0), pe.levels(0))
```

On the demo board or a Pico, only the port changes. `pe_host.connect()` picks
`DemoBoardPort` when the `ttboard` SDK is importable, `PicoPort` on other
MicroPython boards, and `ModelPort` under CPython.

```python
import pe_host
pe = pe_host.connect()          # or connect("pico"), connect("model")
```

## Wiring

### Tiny Tapeout demo board

The chip sits on a carrier plugged into the demo board. The RP2 reaches every
project pin through the PCB, so the host needs no wiring. The table below
gives the GPIO numbers from the SDK's GPIO maps. The library never uses them
directly; it goes through the SDK.

```
                      Tiny Tapeout demo board
  +------------------------------+          +-----------------------------------+
  | RP2350B (TT ETR, DB v3)      |          | chip: tt_um_teslacoilerow_        |
  |                              |          |       protocol_emulator (8x4)     |
  | GPIO17..24 ---- ui_in[0..7] -+--------->| ui[3:0] nibble, ui4 WVALID,       |
  |                              |          | ui5 RREADY, ui[7:6] window        |
  | GPIO33..40 <--- uo_out[0..7]-+----------| uo[3:0] nibble, uo4 WREADY,       |
  |                              |          | uo5 RVALID, uo6 IRQ, uo7 FAULT    |
  | GPIO16 -------- clk ---------+--------->| clk (every edge from the RP2)     |
  | GPIO14 -------- rst_n -------+--------->| rst_n                             |
  | GPIO25..32 <--> uio[0..7] ---+<-------->| uio[7:0] protocol pins            |
  +------------------------------+    |     +-----------------------------------+
                                      |
                              uio PMOD header: UART / SPI / I2C peripherals
                              (not needed with software peers)
```

| Signal | DB v3 (RP2350B, SDK v3, `gpio_map_dbv3.py`) | TT04 to TT06 boards (RP2040, `util/platform/rp2040.py`) |
|---|---|---|
| ui_in[7:0] | GPIO 17..24 | GPIO 9..12 (bits 3:0), 17..20 (bits 7:4) |
| uo_out[7:0] | GPIO 33..40 | GPIO 5..8 (bits 3:0), 13..16 (bits 7:4) |
| uio[7:0] | GPIO 25..32 | GPIO 21..28 |
| project clock | GPIO 16 | GPIO 0 |
| rst_n | GPIO 14 | via the SDK's `reset_project()` |

Rules:

- Set all `ui_in` DIP switches to off. In `ASIC_RP_CONTROL` mode the SDK
  refuses to drive any ui_in pin that reads high, to avoid a fight with the
  switch (`pins.py`, `begin_asiconboard`).
- Software peers (the flagship demo with nothing attached): the RP2 drives
  uio1 (UART into the chip) and uio4 (MISO) push-pull. It pulls uio6/uio7
  (SCL/SDA) low or releases them. `pullups=0xC0` enables the RP2's internal
  pull-ups on SCL/SDA. Leave the uio PMOD header empty in this mode.
- Real peripherals on the PMOD header (`peers="external"`): as in
  [info.md](info.md), "External hardware". Connect a UART adapter (chip TX on
  uio0, chip RX on uio1), an SPI target (SCK 2, MOSI 3, MISO 4, CS_N 5) and
  an I2C target at 0x42 (SCL 6, SDA 7) with pull-ups, for example 4.7 kOhm, to
  the chip's I/O supply. The RP2 leaves uio as inputs (`uio_oe_pico = 0`), so
  it can still read the pads.

### Raspberry Pi Pico wired to an FPGA

`PicoPort`'s default map (`PIN_MAP`) uses all 26 GPIOs that the Pico and
Pico 2 expose. It is meant for an FPGA top that brings uio out to the Pico, so
the Pico can also act as the software peers (UART, SPI, I2C). The FPGA
host-clock builds that task 4.1 produces use other wirings, and two presets
match them (see [FPGA host-clock builds](#fpga-host-clock-builds)).
For any other wiring, pass `PicoPort(pin_map={...})`.

```
   Raspberry Pi Pico / Pico 2                     FPGA board running the design
  +----------------------------+                +------------------------------------+
  | GP0..GP7   (out) ----------+-- ui_in[0..7] -->| ui_in[7:0]                        |
  | GP8..GP15  (in)  <---------+-- uo_out[0..7] --| uo_out[7:0]                       |
  | GP16       (out) ----------+-- clk ---------->| clk   (clock-capable input pin)   |
  | GP17       (out) ----------+-- rst_n -------->| rst_n                             |
  | GP18..GP22 (in*) <---------+-- uio[0..4] ---->| uio[4:0]  tri-state pads          |
  | GP26..GP28 (in*) <---------+-- uio[5..7] ---->| uio[7:5]  tri-state pads          |
  | GND -----------------------+-- GND ---------->| GND                               |
  +----------------------------+                | ena tied high inside the top      |
     * outputs only while software peers        +------------------------------------+
       drive a pin
```

| Pico GPIO | Direction (Pico) | Design port |
|---|---|---|
| GP0..GP7 | output | ui_in[0..7] |
| GP8..GP15 | input | uo_out[0..7] |
| GP16 | output | clk |
| GP17 | output | rst_n (active low) |
| GP18, GP19, GP20, GP21, GP22 | input (output for software peers) | uio[0..4] |
| GP26, GP27, GP28 | input (output for software peers) | uio[5..7] |

For the default map, the FPGA top must:

- instantiate `tt_um_teslacoilerow_protocol_emulator` with `ena = 1`, or
  drive `ena` from a Pico GPIO named `"ena"` in the pin map;
- implement each uio pin as a tri-state pad, with
  `pad = uio_oe[i] ? uio_out[i] : 'z` and `uio_in[i] = pad`, so the chip reads
  its own driven level, as on silicon;
- take `clk` from GP16 on a clock-capable pin, with no PLL (the host supplies
  single edges; a PLL would not lock at kHz manual rates);
- use 3.3 V LVCMOS I/O banks for every signal above.

The IHP SRAM macros need an FPGA memory substitute (task 4.1). The template's
iCE40UP5K target is too small ([.github/workflows/fpga.yaml](../.github/workflows/fpga.yaml)).

#### FPGA host-clock builds

[fpga.md](fpga.md) ("Pin host (Pico) and the host clock option") documents
the Pico wiring of the Cmod A7 and Urbana `host` builds. That document is
task 4.1's work and was not yet committed when these presets were written, so
check the presets against it when it lands. These builds do not
bring uio to the Pico. `pe_host.ports.pico` provides pin maps for both builds:

| Preset | ui_in | uo_out | clk | reset | ena | uio |
|---|---|---|---|---|---|---|
| `PIN_MAP` (default) | GP0..7 | GP8..15 | GP16 | GP17 `rst_n` (active low) | tied high on the FPGA | GP18..22, GP26..28 |
| `PIN_MAP_CMOD_A7_HOST` | GP0..7 (DIP 1..8) | GP8..15 (DIP 9..14, 17, 18) | GP16 (DIP 46) | GP17 `rst_n` (DIP 47) | GP18 (DIP 48); `deselect()` pulses it low | not wired |
| `PIN_MAP_URBANA_HOST` | GP0..7 (PMOD B) | GP8..13 = uo_out[0..5] only | GP16 (JAB_1) | GP17 `rst` (SERVO0, active high) | sw[0] | not wired |

```python
from pe_host import ProtocolEmulator
from pe_host.ports.pico import PicoPort, PIN_MAP_CMOD_A7_HOST
pe = ProtocolEmulator(PicoPort(pin_map=PIN_MAP_CMOD_A7_HOST))
```

A pin map is a dict with these keys:

- `"ui_in"`: 8 GPIOs.
- `"uo_out"`: 6 to 8 GPIOs for uo_out[0..n-1]. The bits that are not wired
  read 0, and `port.uo_visible` lists the wired bits.
- `"clk"`: the clock GPIO.
- `"rst_n"` (active low) or `"rst"` (active high): exactly one of the two.
- `"ena"`: optional.
- `"uio"`: 0 to 8 GPIOs. The software peers need all eight.

What each build supports:

| Feature | Default map | Cmod A7 host build | Urbana host build |
|---|---|---|---|
| Every command, program load, TX/RX, READ_SELECT | yes | yes | yes |
| Command acceptance (`command()` returns True or False) | yes | yes | SELECT and READ_SELECT only; the others return None, because uo[7] is not wired |
| `wait_irq()` | yes | yes | raises HostError, because uo[6] is not wired; poll `levels()` instead |
| `fault_report()` | engine faults, and the host fault inferred from uo[7] | same | engine faults only; `host_fault` is None |
| Self-test (`selftest.run`) | all sections | all except `trigger`, which needs uio0 | no: it checks uo[7] |
| Software peers and the flagship demo | yes | no | no |
| Flagship demo with `peers="external"` | yes | yes | yes, but the sticky host fault cannot be checked (`result.notes` says so) |

`host/tests/test_host_ports.py` runs both presets against GPIO-level fakes
with the reference model behind the pins. On the Cmod A7 map, the self-test
minus `trigger`, followed by a `deselect()`, produces the same edges (ui_in,
uio, reset and ena) as the model backend. The Urbana map runs a program load,
TX and RX traffic and rejected commands, all with the active-high reset.

The FPGA workstream also ships its own Pico host, `fpga/host/pico_host.py`,
and a PC-side UART-bridge host, `fpga/host/pe_host.py`. They are separate
tools: `pico_host.PicoPort` has a different constructor (`ui=`, `uo=`, `rst=`,
`rst_active_high=`, `ena=`) and a `step()` interface. The module name
`fpga/host/pe_host.py` is the same as this library's package `pe_host`, so do
not put `fpga/host/` and `host/` on the same `sys.path`. Renaming one of them
is left to the release reconciliation of tasks 4.1 and 4.2.

#### Fast GPIO path

`fast=True` (the default) uses `machine.mem32` on the SIO block for pin values:
GPIO_IN at 0xD0000004 and GPIO_OUT at 0xD0000010. The offsets are the same on
RP2040 and RP2350, and the SDK uses them in `util/platform/rp2040.py` and
`rp2350.py`. The fast path needs the ui_in and uo_out GPIOs to be contiguous
runs, and `os.uname().machine` must name an RP2040 or RP2350. Otherwise, or
with `fast=False`, the port uses only `machine.Pin` calls. Pin directions always
change through `machine.Pin`.

## How one host clock works

The host port is synchronous and `ui_in` is not synchronized ([isa.md](isa.md)),
so the host supplies every clock edge. A backend implements one primitive,
`port.cycle(ui)`:

1. With software peers attached: read the uio pad levels left by the previous
   edge, let the peers choose their drive, and apply it. A peer may never drive
   a pin that the chip owns push-pull; see [Safety interlock](#safety-interlock).
2. Drive `ui_in = ui`.
3. Sample `uo_out` before the edge. This sample shows the window-change bubble
   and the current ready/valid bits.
4. Apply one rising edge, then return the clock low.

The library's handshakes are cycle-identical to the cocotb harness's host
driver (`test/harness.py`, checked cycle for cycle):

- A window change costs one bubble cycle with valid and ready low.
- A word is eight little-endian nibbles. Write-valid is held on each nibble
  until write-ready was sampled high. One idle cycle follows the word, and its
  uo sample shows the FAULT state after the word.
- Read-ready is held until read-valid was sampled high, nibble by nibble.
- A READ_SELECT read is: the command, one cycle in window 1 (this drops a stale
  snapshot), then a window-0 read. The word is captured one cycle after the
  bubble, and read-valid rises one cycle later.

Example (the [info.md](info.md) timing sketch): writing one TX word and
then reading status takes a 1-cycle bubble, 8 nibble cycles, 1 idle cycle,
a 1-cycle bubble, 1 capture cycle and 8 read cycles.

## API reference

All cycle counts are host clocks. `timeout` arguments bound one blocked nibble
handshake; the default is `ProtocolEmulator.timeout`, 100000.

### `ProtocolEmulator(port, timeout=100000, architecture=None, isa_versions=(2,), strict=True, log=None)`

- `architecture`: the device architecture that images must be bound to. The
  default is `protocol.DESIGN_ARCHITECTURE`, the design of record
  `configs/instruction-sram-32.json`: 4 engines, 32-bit, 64 words, FIFO 8,
  fused, no prefetch.
- `isa_versions`: the READ_SELECT 7 values that `check_isa()` and
  `load_image()` accept.
- `strict`: raise `CommandRejected` when uo[7] rises across a command word.
- `log`: an optional print-like callable that logs every command and its
  outcome.

Attributes: `uo` (the last uo sample), `irq`, `fault_pin`, `fault_visible`
(False when the port does not see uo[7]), `cycles`, `window`, `selected`,
`read_selected`, `device_isa`, `last_command_ok`.

**Clock, reset and windows**

| Method | Effect |
|---|---|
| `cycle(ui=None)` | One clock; the default ui is the current window with no valid or ready. Returns uo. |
| `idle(n=1)` | n idle clocks in the current window. |
| `reset(cycles=4)` | Holds reset asserted (rst_n low) for `cycles` edges. Invalidates images, queues and selection. Always call it first. |
| `deselect(cycles=2)` | Holds ena low. Same effect as reset. Works on the model and cocotb backends, and on a `PicoPort` whose pin map has an `"ena"` GPIO. |
| `set_window(w)`, `bounce()` | Changes the window (one bubble). `bounce()` leaves the window for one cycle, which abandons any partial transfer. |

**Words**

| Method | Effect |
|---|---|
| `write_word(window, word, timeout=None)` | Windows 0, 1 and 2. On timeout, abandons the partial word (bounce) and raises `HostTimeout`. |
| `read_word(window=0, timeout=None, pauses=None)` | Windows 0 (status) and 3 (RX FIFO; pops at the 8th nibble). `pauses[i]` idle cycles before nibble i. |
| `try_write_word(window, word, max_wait, nibbles=8)` | Gives up after `max_wait` stalled cycles, or after `nibbles` < 8 on purpose. Returns True only for a complete word. |
| `try_read_word(window, max_wait, nibbles=8, pauses=None)` | Returns None when abandoned. An abandoned RX read does not pop the word. |

**Commands.** Every command method writes one window-0 word and returns
`True` (FAULT low after the word), `False` (FAULT rose: the chip rejected the
word and set its sticky host fault) or `None` (FAULT was already high, so
acceptance cannot be inferred). With `strict`, `False` raises
`CommandRejected`. The chip does not report which word was rejected. An
engine that faults on the same cycles cannot be told apart, so call
`fault_report()`.

SELECT and READ_SELECT are the exception. Whether the chip accepts them
depends only on the payload: SELECT needs an engine below `engine_count`, and
READ_SELECT an index from 0 to 7 ([isa.md](isa.md); `test/model/reference.py`).
For these two commands `command()` returns that answer, True or False, even
when FAULT is already high, when an engine faults during the word, or when
the port cannot see uo[7]. `selected` and `read_selected` change only when
the command is accepted, so the library's record always matches the chip.
Earlier versions returned None under a high FAULT pin and still recorded the
payload, so a raw `command(SELECT, 7)` could leave `selected` at 7 and make
`fault_report()` raise. `test_selection_tracking_under_fault` covers this
case. On a port without uo[7], every other command returns None.

| Method | Command (isa.md) |
|---|---|
| `command(op, payload=0, check=None)` | Any raw command. |
| `select(engine)` / `use(engine)` | SELECT. `use` sends it only when the engine is not already selected. |
| `begin(engine=None)` | BEGIN. |
| `commit(length)` | COMMIT. |
| `own(pins, open_drain=0, engine=None)` | OWN. |
| `start(mask)` / `stop(mask=None)` | START / STOP (default: all engines). |
| `route(source, destination, count, enable=True)` / `unroute(source)` | ROUTE. |
| `clear(mask=0, host_fault=False)` | CLEAR. `host_fault` sets payload bit 23. |
| `event(mask)` | EVENT. |
| `flush(engine=None)` | FLUSH. |
| `trigger(pin, mode, enable=True, engine=None)` / `disable_trigger(engine=None)` | TRIGGER. Modes are `protocol.TRIG_RISING`, `TRIG_FALLING`, `TRIG_HIGH` and `TRIG_LOW`. |

**Status reads.** Each read is READ_SELECT, a bounce and a window-0 read.
`engine=None` means the selected engine.

| Method | READ_SELECT |
|---|---|
| `read_status(index)` | Any index, raw word. |
| `status(engine=None)` | 0, decoded into `protocol.Status`: `running`, `committed`, `stalled`, `faulted`, `fault`, `fault_name`. |
| `timestamp()` | 1. |
| `levels(engine=None)` | 2, returned as `(tx_level, rx_level)`. |
| `pc(engine=None)` | 3. |
| `event_pending(engine=None)` | 4. |
| `completed(engine=None)` | 5. |
| `held_rx(engine=None)` | 6, for example the word a strict PUSH rejected. |
| `isa_version()` / `check_isa()` | 7. `check_isa` raises `IsaMismatch` for a version outside `isa_versions`. |

**Loading**

- `load_program(engine, words, owned=0, open_drain=0, verify=False)` sends
  SELECT, BEGIN, OWN, the program words in window 1, then COMMIT.
  `verify=True` reads the status back. [firmware.md](firmware.md) recommends
  a different order: SELECT, BEGIN, the program words, OWN, COMMIT. Both
  orders are legal under [isa.md](isa.md), because OWN only needs a halted
  engine and BEGIN has just halted it. The library uses the order of
  `test/harness.py` `load()`, so its waveforms stay cycle-identical to the
  harness and to the `scenarios.flagship` reference run. For the other order,
  call `select`, `begin`, `write_word(W_PROGRAM, ...)`, `own` and `commit`
  yourself.
- `load_image(image_or_path, engine=None, check_isa=True, verify=False)`
  checks the image's architecture binding. It reads the ISA version (at least
  the image's `isa_version`, and in `isa_versions`), then loads the image on
  `image.engine`.

**Queues**

- `tx_write(word_or_words, engine=None, timeout=None)` blocks under
  backpressure and raises `HostTimeout` when the budget runs out.
- `tx_try_write(word, max_wait=0, engine=None)` returns True or False.
- `rx_read(engine=None, timeout=None)` and `rx_read_many(count, ...)`.
- `rx_try_read(max_wait=4, engine=None)` returns a word, or None when no word
  became valid in time.
- `rx_drain(engine=None)` reads as many words as READ_SELECT 2 reports.

**Faults and waiting**

- `fault_report()` reads every engine's status and restores the selection.
  It returns a `FaultReport` with `statuses`, `faulted`, `fault_pin`, `ok` and
  `host_fault`. `host_fault` is True, False, or None when an engine fault
  masks it or the port cannot see uo[7] (`fault_pin` is then None too).
- `clear_faults(mask=None, host_fault=True)` with no mask clears exactly the
  faulted engines. A mask that includes a running engine would make the whole
  CLEAR reject.
- `raise_on_fault()` raises `EngineFault` carrying the report.
- `protocol.fault_name(code)` names built-in codes 1 to 4 and the example
  firmware's explicit codes 64 to 67.
- `wait_until(predicate, timeout)`, `wait_irq(timeout=None)` (raises
  `HostError` on a port that cannot see uo[6]), `run(cycles, watch_fault=True)`.
- `free_run(cycles, freq_hz=None)`: idle in window 0. With `freq_hz`, hardware
  ports switch to a PWM clock for `cycles / freq_hz` seconds, so the edge
  count is approximate.

### Firmware images: `pe_host.image`

`FirmwareImage.load(path, source=True, architecture=None)` verifies the image
as [firmware.md](firmware.md) specifies:

- the schema version;
- 32-bit words;
- `bytecode_sha256` over the little-endian words;
- `source_sha256` over the exact bytes of the sibling `.source.json` when it
  is present (`source="required"` makes it mandatory, `False` skips it);
- 8-bit pin masks, with open-drain pins inside the owned pins;
- the engine range and program capacity;
- with `architecture`, the binding to that architecture.

`image.check_binding(arch, ignore=())` and `image.check_isa(version)` raise
`ImageError`. `load_scenario(path, architecture=None)` loads a
`protocol-emulator.firmware-scenario.v1` file and checks its binding. All of
this uses only `json`, `hashlib.sha256`, `binascii` and `struct`, so it runs
on the board too.

### Software peers: `pe_host.peers`

Each peer sees only pad levels. `step(cycle, pads)` returns
`(enable_mask, value_mask)` for the next edge. `Environment(peers,
pullups=0xFF)` combines peers as a wired-AND. Attach an environment with
`port.env = env`.

| Peer | Behaviour |
|---|---|
| `UartSource(pin, words, bit_cycles, gap_cycles, start=None, bad_stop=())` | 8N1 sender, as `test/peers.py`. |
| `UartMonitor(pin, bit_cycles)` | Streaming 8N1 receiver: `words`, `errors`. |
| `SpiTarget(mode, responses, sck=2, mosi=3, miso=4, cs=5)` | Modes 0 to 3, as `test/peers.py`: `received`, `violations`. |
| `I2CTarget(address=0x42, scl=6, sda=7, read_bytes=(0x5A,), stretch_cycles=0, nack_bytes=())` | Pad-level open-drain target: `received`, `master_acks`, `starts`, `stops`. It changes SDA only after an observed SCL falling edge. `stretch_cycles` holds SCL low after each falling edge. |
| `PadCapture(limit, start)` | Records the observed pads, one byte per cycle. |

### Scenarios

- `pe_host.selftest.run(pe, trigger_pin=0, log=None, sections=None)` runs a
  bring-up self-test in 10 independent sections and 73 checks, using only
  the host port. The "trigger" section drives uio0 for a few cycles; nothing
  else leaves the chip. It returns `SelfTestResult` (`passed`, `summary()`).
- `pe_host.flagship.run_flagship(pe, scenario_path, firmware_dir=None,
  peers="software", i2c_stretch=0, done=None, max_cycles=None,
  free_run_cycles=200000, free_run_hz=None, on_reset=None, log=None)` returns
  `FlagshipResult` (`passed`, `mismatches`, `summary()`). With
  `peers="software"`, the result is checked against the scenario's `expected`
  block: the UART bytes seen on pin 0, the SPI MOSI bytes, the I2C bytes and a
  single STOP, engine 2's RX words, empty route queues and no faults.
  `peers="external"` lets the engines free-run with real peripherals and
  reports what the host reads back. In that mode the `uart-rx` engine (engine
  1) normally ends in fault 3: its idle and start-bit waits are bounded at
  twelve bit periods ([firmware.md](firmware.md)), and with the default
  200,000 free-run cycles the UART line is idle for longer than that before
  STOP. That fault is expected (a run without it passes too); any other
  engine fault fails the run. uo[7] is the OR of the sticky host fault and every
  engine fault, so it says nothing about the host fault while engine 1 is
  faulted. After STOP, `run_flagship` therefore CLEARs engine 1 (CLEAR is
  accepted for a halted engine whatever the host fault) and reads the fault
  state again. `result.fault_report` is the report from before the CLEAR,
  `result.fault_report_after_clear` the one after it, and
  `result.host_fault` the host fault read from uo[7] with no engine faulted.
  A host fault fails the run. On a port without uo[7] (the Urbana build)
  the host fault cannot be read. The run then passes on the engine faults
  alone, and `result.notes` records that the host fault was not checked.

### Ports

`Port` is the contract that every port implements: `cycle(ui)`,
`reset(cycles)`, `deselect(cycles)`, `free_run(cycles, freq_hz)`, `count`,
`env`, `forbidden`, and `uo_visible`, the mask of uo_out bits the port can
see. It is 0xFF except on a partly wired `PicoPort`.

| Port | Constructor |
|---|---|
| `ModelPort` | `(architecture=None, model=None, env=None, pins=None, pullups=0xFF, record=False, record_replay=False)`. `pins` is a harness-style supplier: an int or `(cycle, Outputs) -> pads`. `record` enables `wire_samples()`, which returns the scoreboards' `WireSample`s, and `render_testbench()`, which returns a self-checking Verilog testbench. |
| `CocotbPort` | `(dut, env=None, pins=None, pullups=0xFF, lockstep=True, ...)`. Use it from `await run_in_bridge(func)`. |
| `DemoBoardPort` | `(tt=None, project="tt_um_teslacoilerow_protocol_emulator", enable=True, fast=True, env=None, pullups=0)`. `release()` returns the board to the SDK. |
| `PicoPort` | `(pin_map=None, fast=True, env=None, pullups=0, chip=None)`. `pin_map` is `PIN_MAP` (the default), `PIN_MAP_CMOD_A7_HOST`, `PIN_MAP_URBANA_HOST` or your own dict; see [FPGA host-clock builds](#fpga-host-clock-builds). |
| `ReplayPort` | `(data, env=None)`. |

Errors are `HostError` and its subclasses: `HostTimeout`, `CommandRejected`,
`ImageError`, `IsaMismatch`, `EngineFault`, `PadContention` and
`ReplayDivergence`. `HostTimeout` does not derive from `TimeoutError`, which
MicroPython lacks.

### Safety interlock

Every OWN the library sends adds the pins it owns without open drain to
`port.forbidden`. A reset clears the set. The ports never let software peers
drive a forbidden pin:

- The model and cocotb ports raise `PadContention`. They also raise it on
  any simulated driver fight.
- On hardware, the port masks the drive (`strict_env = False`) or raises
  (the default).

A miswired peer is therefore caught before the RP2 and the chip drive the
same pad against each other. A test in
`host/tests/test_host_flagship.py` checks this.

## Running the same scripts on silicon

1. **Flash the SDK.** Install the Tiny Tapeout demo-board firmware: a UF2
   with MicroPython and the frozen `ttboard` SDK. The v3 releases (v3.1.1 is
   the latest) ship an RP2350 UF2 only. RP2040 boards run earlier releases
   (see the SDK README's compatibility matrix). Those releases were not
   reviewed here.
2. **Copy the library and firmware.** Precompiling saves RAM:

   ```sh
   make -C host mpy MPY_ARCH=armv7emsp      # RP2350; armv6m for RP2040
   mpremote cp -r host/build/mpy/pe_host :   # or: mpremote cp -r host/pe_host :
   mpremote mkdir :firmware
   mpremote cp firmware/flagship-scenario.json firmware/uart-tx.image.json \
       firmware/uart-rx.image.json firmware/spi-controller-mode0.image.json \
       firmware/i2c-write.image.json :firmware/
   ```

   The `.mpy` version must match the board's MicroPython. Check it with
   `import sys; sys.implementation._mpy`. If you are unsure, copy the `.py`
   sources. Copying the `.source.json` files as well lets `FirmwareImage`
   also verify `source_sha256`. Without them it verifies the bytecode only
   and reports `source_verified=False`.
3. **Run the bring-up self-test with nothing attached.** Put all DIP switches
   off, then run `mpremote run host/examples/selftest_demo.py`. It selects the
   project through `tt.shuttle`, takes the clock (`clock_project_stop`, then
   RP2-driven), disables the v3.3 manual-clock button timer, resets the chip
   and runs the 73 checks.
4. **Run the flagship scenario with software peers.** Keep the PMOD header
   empty and run `mpremote run host/examples/flagship_demo.py`. The RP2 plays
   the UART sender and receiver, the SPI target and the I2C target on its own
   uio GPIOs, with internal pull-ups on SCL/SDA. The script prints the same
   `FlagshipResult` summary as the model run.
5. **Use real peripherals** with `flagship_demo.main(peers="external",
   free_run_hz=F)`. The host loads the images, prefills, routes and starts
   under manual clocking. It then PWM-clocks the chip
   (`tt.clock_project_PWM`), stops, and drains engine 2's RX. The example
   firmware counts time in system clocks, so the peripheral rates scale with
   `F`. The unchanged `uart-tx`/`uart-rx` images use 64 clocks per bit, so
   F = 7.3728 MHz gives 115200 baud (7372800 / 64 = 115200). Or reassemble
   the firmware with `--half-period` ([firmware.md](firmware.md)).
   Engine 1 ends in its expected fault 3 (see [Scenarios](#scenarios)), which
   `run_flagship` clears after STOP so it can read the host fault. Because of
   that bounded wait, engine 1 only receives characters whose start bit
   comes within twelve bit periods of START or of the previous character's
   stop bit. The UART-to-SPI route therefore carries data only if the sender
   is already transmitting back to back when START is issued; characters
   typed by hand are not received. This has not been tried on hardware. For
   longer gaps, reassemble `uart-rx` with a larger LIMIT
   ([firmware.md](firmware.md)).
6. **Capture the pads.** Attach a logic analyzer to uio[7:0], or record from
   the RP2 with `peers.PadCapture` during a software-peer run.

Performance on the RP2 has not been measured. Each host clock is a few Python
calls, so manual clocking runs far below 50 MHz. That is harmless for
correctness, because every protocol in the example firmware counts system
clocks.

Memory: the precompiled package is 39,133 bytes of `.mpy` for either
architecture. The smallest MicroPython heap that ran the replayed flagship
scenario was 190 KiB. The self-test needed 129 KiB (the bisection varies by a
few KiB between runs: 123 to 129 KiB in this round). Both figures come from
the 64-bit unix port (`tools/upy_heap.py`, job 23778831), include the replay
trace (47 KB and 20 KB), and do not include the `ttboard` SDK's own heap use. They are
estimates for a 32-bit board, not measurements on one. The RP2350's 520 KB of
SRAM leaves room. On an RP2040 (264 KB), the flagship scenario with software
peers may be tight next to the SDK.

SHA-256: `image.py` uses `hashlib.sha256` when the firmware provides it,
otherwise a pure-Python SHA-256. The fallback was checked against `hashlib`
under CPython and under the MicroPython unix port.

## Verification of the library

All runs used the committed snapshot `73536f0`: reference model, firmware
images, harness and RTL. The host code was the working-tree `host/`, copied
into each job's run directory. Results come from the Slurm jobs named in the
table; the work manifest records every job. The latest round (jobs 23778831
and later) ran a frozen copy of the current `host/`.

Two Icarus Verilog builds were used for the RTL replays: 14.0 (devel,
s20260301) from the OSS CAD Suite, and 13.0 from TinyTapeout's
`iverilog_13.0-1_amd64.deb`, the build that `.github/workflows/test.yaml`
installs in CI.

| Check | What it shows | Result |
|---|---|---|
| `host/tests/test_host_commands.py` | Every command and READ_SELECT against the model's internal state. Also: rejections, TX/RX timeouts, read pauses, strict-overflow held RX, all 19 committed images loaded, a self-test that fails on a model with an injected host-port bug, and raw SELECT/READ_SELECT under a high FAULT pin (the library's selection stays equal to the model's, and `fault_report()` still works). | PASS (job 23778831) |
| `test_host_transfers.py` | Abandoned partial command, program, TX, RX and status transfers (1 to 7 nibbles) have no side effect. A window change abandons the RX reservation. Every window change is a bubble with ready and valid low. Read-valid comes two cycles and write-ready one cycle after the change. | PASS (23778831) |
| `test_host_image.py` | SHA-256 and structure checks, architecture binding and ISA requirement, including tampered images. | PASS (23778831) |
| `test_host_harness_equivalence.py` | An operation list covering every command, accepted and rejected, gives cycle-identical ui/uio/rst/ena/uo and identical returned values on the library and on `test/harness.py`. The harness driver returns nothing for commands, so an acceptance oracle (`tests/acceptance.py`) checks `command()`'s results separately. It wraps the model's own command decoder and compares each result with the model's accept/reject decision. It also checks that the library's `selected` and `read_selected` equal the model's after every operation. The oracle runs on the operation list and on 30 random programs, each on a full port and on a 6-bit uo port. A mutant with the old SELECT/READ_SELECT rule is caught. | PASS (23778831) |
| `test_host_flagship.py` | The flagship scenario with pe_host peers. The pad waveform is decoded by `test/model/scoreboards.py` (`uart_decode` on pins 0 and 1, `spi_decode` MOSI and MISO, `i2c_decode`, `assert_open_drain`) and by `test/peers.py`'s `UartMonitor`. Also covered: I2C clock stretching, NACK reported as fault 65, and the contention interlock. With the `test/` peers, the whole flagship waveform (over 7,000 cycles) equals `test/scenarios.py` `flagship()` on the harness, cycle for cycle. `peers="external"`, with SPI and I2C targets on the pads and an idle UART line, has four tests. (1) Engine 1's fault 3 is expected: the run passes, and after the CLEAR, no fault and no host fault remain. (2) A host fault injected right after reset is still detected. (3) An extra engine fault (no I2C target, so fault 65) fails the run. (4) On a 6-bit uo port the run passes, with the host fault reported as not checked. | PASS (23778831) |
| `test_host_ports.py` | `DemoBoardPort` (fast and slow) and `PicoPort` (fast and slow) against SDK/GPIO fakes. For the self-test and the flagship scenario, their rising-edge sequence equals `ModelPort`'s. `PIN_MAP_CMOD_A7_HOST`: the self-test minus `trigger`, then `deselect()` through the ena GPIO, gives the same edges as `ModelPort`, ena included. `PIN_MAP_URBANA_HOST`: active-high reset, uo_out[5:0] only, program load, TX/RX traffic, and SELECT/READ_SELECT acceptance without uo[7]. | PASS (23778831) |
| `test_host_rtl.py` | The library's exact stimulus for the self-test (3,324 cycles) and the flagship scenario (7,827 cycles), rendered as a self-checking testbench and run on the RTL, once with Icarus 14 and once with Icarus 13.0 (the CI build). | PASS on both (23778831) |
| `test_host_makefile.py` | `make -C host rtl-cocotb-paths` from the repository root (also with a bogus `$PWD`), `make rtl-cocotb-paths` in `host/` and `make paths` in `host/tests/rtl_cocotb/` all resolve the same testbench, RTL, model and scenario. Command-line overrides work, and a stale `PE_HOST_SCENARIO` in the environment is ignored. | PASS (23778831) |
| `tests/rtl_cocotb` | The library drives the RTL live through cocotb 2.0.1 (`CocotbPort`, bridge thread), lockstep with the model before and after every edge. The self-test and the flagship scenario both pass, with 0 mismatches. In a fresh export of `73536f0` plus this `host/`, all three documented entry points ran on Icarus 13.0, each giving 2 of 2: `make -C host rtl-cocotb` from the repository root, `cd host && make rtl-cocotb` and `make` in `host/tests/rtl_cocotb/`. So did the first form with a stale `PE_HOST_SCENARIO` in the environment, and the first form on Icarus 14. | PASS, 2 of 2 in each of the 5 runs (23778831) |
| `test_host_micropython.py` | `tools/upy_check.py` subset check. The self-test, the flagship scenario (with and without I2C stretching) and the `peers="external"` flagship run replayed under the MicroPython 1.29 unix port, from `.py` sources and from `mpy-cross` `.mpy` files. Every ui byte and peer drive matched the CPython run, and so did the verdict (for `external`: pass, host fault False, faults `1:3`). Also checks the pure-Python SHA-256. | PASS (23778831) |
| `peers="external"` with the default 200,000 free-run cycles (`external_default.py` in the work directory) | SPI and I2C targets on the pads and an idle UART line, on a full port and on a 6-bit uo port. | PASS on both: 201,581 host cycles each. Faults `[(1, 3)]`, none after the CLEAR. Host fault False on the full port and not checked on the 6-bit port. I2C bytes `84 5a` (23778831) |
| `tools/fuzz_host.py`, seeds 0 to 59,999 | Random host-operation programs, run on the harness driver, the CPython library and the MicroPython library (three-way differential). | 60,000 of 60,000 PASS: 114.4 M cycles, 4.38 M ops (jobs 23755318, 23755319) |
| `tools/fuzz_host.py --rtl`, seeds 100,000 to 102,999 and 200,000 to 239,999 | The same three-way differential, and each seed's stimulus also replayed on the RTL with Icarus 14, checking every public output after every edge. | 43,000 of 43,000 PASS: 81.7 M RTL cycles (jobs 23756016, 23756557) |
| `tools/fuzz_host.py --rtl`, seeds 300,000 to 399,999, on the current code (SELECT/READ_SELECT acceptance rule, generalized `PicoPort`) | The three-way differential plus the Icarus 14 RTL replay of every seed. | 100,000 of 100,000 PASS: 190.5 M cycles, 7.30 M ops, 100,000 MicroPython and 100,000 RTL replays (jobs 23767497, 23767986, 23767987, 23767988) |
| `tools/fuzz_host.py --rtl`, seeds 400,000 to 499,999, current code, RTL replays on Icarus 13.0 | The three-way differential, the acceptance oracle on every command of every seed, a repeat on a 6-bit uo port for every fourth seed (same stimulus, same oracle), and the Icarus 13.0 RTL replay of every seed. | 100,000 of 100,000 PASS: 190.5 M cycles, 7.30 M ops, 100,000 MicroPython and 100,000 RTL replays, 25,000 6-bit repeats, 0 oracle problems (jobs 23778347, 23778353, 23778832, 23778833, 23778835, 23778836) |

In job 23778831 the full unit suite ran 69 tests with none skipped, with
MicroPython 1.29, mpy-cross and Icarus 14 available; `test_host_rtl.py` then
ran again on Icarus 13.0 and passed. A CI-like run (Python 3.11,
`PATH=/usr/bin:/bin`, no `PE_HOST_*` variables, `python3 -m unittest
discover -s host/tests` from the repository root) ran 69 tests with 6
skipped: the four MicroPython replay tests and the two Icarus replays. The
host tests are not in CI yet. If `python3 -m unittest discover -s
host/tests` is added to `.github/workflows/test.yaml` after its Icarus 13.0
install step, the two Icarus replays run as well, and they pass on that
build.

The fuzz programs mix every command with valid and invalid payloads,
abandoned writes in windows 1 and 2 and abandoned reads in windows 0 and 3,
read pauses, bounces, program and firmware loads, and mid-run resets and
deselection. Seeds 0 to 59,999 produced:

- 417,341 READ_SELECT status reads;
- 468,921 abandoned and 115,671 completed bounded reads;
- 341,895 abandoned and 243,297 completed bounded writes.

Every defined command (opcodes 0 to 11) was seen accepted and seen rejected,
and was also sent while FAULT was already high. COMMIT was accepted only 28
times in seeds 0 to 59,999, because it needs the exact written length.
Unknown opcodes were always rejected.

`command()` never returns None for SELECT and READ_SELECT, by construction,
so counting None results proves nothing. The evidence that the results are
right comes from the acceptance oracle. It compares every result with the
model's own decision, and the library's selection record with the model's
after every operation. In seeds 400,000 to 499,999 it found no problems in
any of these cases (library result / model decision):

- SELECT and READ_SELECT on a full port: 208,942 True/accepted and 71,821
  False/rejected. On a 6-bit uo port: 52,482 and 18,058.
- Other commands on a full port: 861,937 True/accepted and 255,934
  False/rejected. 583,971 None/accepted and 274,722 None/rejected, each
  with FAULT already high.
- 239 False/accepted, each with an engine fault rising during the word.
  uo[7] cannot tell this case from a rejection, which is why the library
  documents it.
- Other commands on a 6-bit port: None in all 495,272 cases.

The replay found one real MicroPython incompatibility. An exception subclass
called `HostError.__init__(self, ...)`, which raises AttributeError on
MicroPython. It now uses `super().__init__`, and `upy_check` flags the pattern.

Not verified: any run on a demo board, a Pico or an FPGA; the real `ttboard`
SDK (only the fakes); SDK releases older than v3; RP2 clocking speed;
`free_run` PWM behaviour; and `peers="external"` with real peripherals (only
pad-level software targets on the model).

## Sources

- Tiny Tapeout MicroPython SDK, <https://github.com/TinyTapeout/tt-micropython-firmware>,
  reviewed at commit `d485c7a9986336024e35d790568fca33be1c5665` (September
  2026, release v3.1.1):
  - README "Initialization", "Selecting and loading projects", "Interacting
    with I/O", "Project clocking and reset" and "Fast I/O";
  - `src/ttboard/demoboard.py` (`DemoBoard.get`, `reset_project`,
    `clock_project_once`, `clock_project_PWM`, `clock_project_stop`);
  - `src/ttboard/mode.py` (`RPMode`);
  - `src/ttboard/project_mux.py` (`shuttle.has`, `shuttle.get`,
    `Design.enable`);
  - `src/ttboard/pins/pins.py` (ports, `project_clk_driven_by_RP2`,
    `begin_asiconboard`);
  - `src/ttboard/pins/gpio_map_dbv3.py` (DB v3 GPIO map);
  - `src/ttboard/pins/manual_clock.py` (v3.3 button timer);
  - `src/ttboard/util/platform/rp2040.py` and `rp2350.py` (fast I/O, SIO
    register offsets);
  - `.github/workflows/release.yml` (frozen SDK, MicroPython from source) and
    the README compatibility matrix.
- RP2040 datasheet section 2.3.1.7 and RP2350 datasheet section 3.1.11 (SIO
  GPIO registers), as cited in the SDK's platform files.
- MicroPython 1.29.0 unix port and mpy-cross: conda-forge package
  `micropython-1.29.0-h8d769aa_0`, used for the replay and subset checks.
