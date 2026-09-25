# Independent third-party peers

The cocotb suite in `test/` checks the design against the project's own
reference model and the project's own protocol peers, so it can only confirm
that the design agrees with the project's understanding of each protocol. The
suite in `test_ext/` (plan task 2.4) instead attaches protocol peers written by
other people and checks that the shipped firmware images talk to them
correctly at the pads. Every peer is vendored unmodified from a permissively
licensed upstream repository at a pinned commit (`test_ext/vendor/`).

## Peers

| peer | upstream (commit) | license | language | role against the DUT |
|---|---|---|---|---|
| `uart_rx`, `uart_tx` | alexforencich/verilog-uart (1b867e5) | MIT | Verilog | receiver on pin 0, transmitter on pin 1 |
| `UartSource`, `UartSink` | alexforencich/cocotbext-uart (2f67153) | MIT | Python | transmitter on pin 1, receiver on pin 0 |
| `spiflash` | YosysHQ/picorv32 `picosoc/spiflash.v` (ef203c2) | ISC | Verilog | SPI NOR flash (DUT is controller) |
| `SpiSlaveLoopback`, `SpiMaster` | schang412/cocotbext-spi (67857d0) | MIT | Python | SPI target (DUT controller), SPI controller (DUT target), modes 0-3 |
| `SPI_Master_With_Single_CS` | nandland/spi-master (1ab5819) | MIT | Verilog | SPI controller, modes 0-3 (DUT target) |
| `SPI_Slave` | nandland/spi-slave (e12af34) | MIT | Verilog | SPI target, modes 0-3 (DUT controller); defective, see below |
| `i2c_slave`, `i2c_master` | alexforencich/verilog-i2c (a65be40) | MIT | Verilog | I2C target (DUT controller; one defect, see below), I2C controller (DUT target) |
| `I2cMemory`, `I2cMaster` | alexforencich/cocotbext-i2c (a1a4d5d) | MIT | Python | 256-byte EEPROM-style target, I2C controller |
| `hazard3_jtag_dtm` | Wren6991/Hazard3 (8af9929) | Apache-2.0 | Verilog | JTAG TAP with the IEEE 1149.1 state machine and RISC-V JTAG-DTM registers (IDCODE, DTMCS, DMI, BYPASS); not fully 1149.1-conformant, see below |

Full URLs, upstream paths and file hashes are in `test_ext/vendor/README.md`
and `test_ext/vendor/SHA256SUMS`. No suitable permissive SPI flash model with a
JEDEC-ID command was found; `spiflash.v` implements release-from-power-down
(AB), power-down (B9), READ (03) and the dual/quad reads, which is what the
tests use.

## How the suite works

`test_ext/tb_ext.v` puts the Tiny Tapeout top on a model board: eight pads with
pull-ups (`tri1`), the DUT driving them through 1 ns or 5 ns of wire (the skew
corners below), and every peer attached to the pads. I2C peers are open drain;
every other peer drives its pad push-pull only while the test selects it. Two
drivers that disagree resolve to X, and an X on any pad fails the test
(`PadContention`). A separate counter fails the I2C tests if the DUT ever
drives an open-drain pad high.

The host side is the unmodified lockstep driver of `test/harness.py`:
`PadHarness` only replaces where the pin inputs come from. The DUT's `uio_in`
is the resolved pad value sampled on the falling clock edge (so timer-driven
Python peers cannot race the DUT's sampling edge), and the harness hands the
same register to the reference model after every rising edge. The DUT is
therefore still compared with the reference model on every cycle, while the
protocol outcome is judged by the third-party peers and by what the host reads
back.

Firmware: every protocol test loads the shipped image from `firmware/` with
its SHA-256 identity checks (UART TX/RX, SPI controller and target in modes
0-3, all five I2C images, JTAG, and the four flagship images). Two protocol
operations cannot be expressed with the shipped images, so the suite adds three
test-only programs in `test_ext/firmware/`, assembled from their source JSON by
the project's own OCaml assembler (built from commit 73536f0; it reproduces the
shipped `jtag`, `spi-controller-mode3` and `i2c-repeated-start` images byte for
byte):

* `spi-flash-mode0` / `spi-flash-mode3`: the shipped SPI controller asserts
  CS for exactly one byte, but a flash READ needs a command, a 24-bit address
  and data in one CS frame. Bit 8 of each TX word says whether to release CS.
* `jtag-vector`: the shipped `jtag` image only walks to Shift-DR. This 12-word
  program shifts 16 (TMS, TDI) pairs per TX word and returns 16 TDO samples,
  so the host can perform any TAP walk, IR scan or DR scan.

`PE_EXT_SEED=<n>` switches the tests from their fixed stimulus to random
payload bytes, flash addresses, I2C stretch delays, JTAG patterns and a random
UART baud error of up to 3 %, drawn from a generator seeded by the seed and the
test name.

### Skew corners and edge checks

The DUT changes every output on the rising edge of its clock. When a clock pin
and a data pin change on the same edge, a zero-delay simulation delivers both
to a peer in the same time step, and an edge-triggered peer then samples the new
data value. On a board this is a set-up or hold violation and the peer may see
either value. With a single 1 ns delay for every DUT output (the board model
before this change), the simulator therefore forgave a controller that changes
data on the peer's sampling edge. For example, the shipped mode-1 SPI program
run in place of mode 0 still passed the `SpiSlaveLoopback` test, and mode 3 run
in place of mode 2 passed every third-party SPI test (see the wrong-mode matrix
below).

`tb_ext.v` now delays the DUT's clock pins (TCK pad 0, SCK pad 2, SCL pad 6) and
its other outputs by different amounts. There are two corners:

* `data`: clock pins 1 ns, every other DUT output 5 ns. A data change on a
  clock edge arrives after the edge, so the peer samples the old value, as with
  a hold-time requirement.
* `clock`: clock pins 5 ns, the others 1 ns. The data change arrives first,
  which also catches a chip select that moves on the same edge as the preceding
  SCK edge.

Every test in which the DUT drives a clock and the data that a peer samples on
it runs once per corner: the SPI controller and flash tests, every I2C test
with the DUT on the bus (controller and clock-stretching target), both JTAG
tests and both flagship variants. The other tests (UART, SPI target, where the
DUT drives only MISO, and the two DUT-idle adjudication tests) run in the `data`
corner. A design that never moves data on a peer's sampling edge behaves the
same in both corners. Both delays are shorter than half a clock period, so the
pads sampled on the falling edge, which are the inputs of the DUT and of the
reference model, are the same in every corner.

The board also counts, for each clock pin the DUT drives, how often a data pin
moved on the same clock edge as a rising or a falling clock edge (`co_*`
registers in `tb_ext.v`; a move counts only if the DUT changed its drive and
the pad followed). The tests log these counts and check the protocol rule
directly:

* SPI: inside a frame, MOSI never moves on the edge where the target samples
  (rising in modes 0 and 3, falling in modes 1 and 2), and CSn never moves on an
  SCK edge.
* JTAG: TDI and TMS never move on a rising TCK edge.
* I2C: SDA never moves on an SCL edge.

These checks are this project's reading of the specifications, added to the
third-party verdicts. `PE_EXT_NO_EDGE_CHECK=1` switches them off, so that only
the peers judge; the wrong-mode matrix below uses this. The shipped firmware
moves MOSI only on the shift edge in every mode: on falling SCK edges in modes
0 and 3, and on rising edges in modes 1 and 2. It never moves CSn on an SCK
edge, moves TDI and TMS only on falling TCK edges, and never moves SDA on an SCL
edge, either as I2C controller or as clock-stretching target. The counts are the
same in both corners.

## What each test checks

Tests marked "x2" run once in each skew corner. A full run is 65 tests: 42
test cases, 23 of them in two corners.

| test | shipped firmware | third-party peer(s) | checks |
|---|---|---|---|
| `test_uart_tx_vs_third_party_receivers` | `uart-tx` | `uart_rx` + `UartSink` on the same pin | 21 bytes (8 prefilled, 13 under TX backpressure) at the baud rate the image declares (50 MHz / 64 = 781 250 baud) decoded by both receivers, no `uart_rx` framing error, pin released after STOP |
| `test_uart_rx_vs_verilog_uart_tx` | `uart-rx` | `uart_tx` | 25 back-to-back frames read from the RX FIFO while it is drained concurrently |
| `test_uart_rx_vs_cocotbext_source` | `uart-rx` | `UartSource` | the same with the Python transmitter |
| `test_uart_rx_baud_tolerance[-3,+3]` | `uart-rx` | `UartSource` at 3 % slow / fast | 25 back-to-back frames still received |
| `test_spi_flash_read[mode 0,3]` x2 | `spi-flash-mode*` | `spiflash` | AB, READ at two addresses returns the flash image bytes, READ after B9 returns no data, READ after a second AB works again; SPI edge check |
| `test_spi_controller_vs_cocotbext_loopback[0-3]` x2 | `spi-controller-mode*` | `SpiSlaveLoopback` | every MISO byte equals the previous MOSI byte, the last MOSI byte is held by the peer; SPI edge check |
| `test_spi_controller_vs_nandland_slave[0-3]` x2 | `spi-controller-mode*` | `SPI_Slave` | adjudicated disagreement: MOSI and MISO bytes as predicted per mode and corner (see below); SPI edge check |
| `test_nandland_slave_vs_cocotbext_master[0-3]` | none (DUT idle) | `SpiMaster` + `SPI_Slave` | reproduces the disagreement without the DUT |
| `test_spi_target_vs_nandland_master[0-3]` | `spi-target-mode*` | `SPI_Master_With_Single_CS` | 6 frames: controller receives the queued TX bytes, DUT RX FIFO holds the controller's bytes, MISO released |
| `test_spi_target_vs_cocotbext_master[0-3]` | `spi-target-mode*` | `SpiMaster` at 1 MHz | the same with the Python controller |
| `test_i2c_write_vs_verilog_slave` x2 | `i2c-write` | `i2c_slave` @0x42 | address and data byte arrive, STOP seen |
| `test_i2c_read_vs_verilog_slave_with_stretching[first bit 0,1]` x2 | `i2c-read` | `i2c_slave`, stretching SCL 400 clocks | returned byte in the RX FIFO (0xFF when the peer's stretch defect applies, see below), controller NACK ends the read, STOP seen |
| `test_i2c_slave_stretch_defect_without_dut[0x43,0xC3]` | none (DUT idle) | `i2c_master` + `i2c_slave` | reproduces the `i2c_slave` stretch defect without the DUT |
| `test_i2c_repeated_start_vs_verilog_slave` x2 | `i2c-repeated-start` | `i2c_slave` | register byte written, repeated START, byte read back; bus state afterwards (see below) |
| `test_i2c_address_nack_vs_verilog_slave` x2 | `i2c-write` | `i2c_slave` @0x43 | address NACK gives fault 65, both pins released |
| `test_i2c_eeprom_reads_vs_cocotbext_memory` x2 | `i2c-write`, `i2c-read`, `i2c-repeated-start` | `I2cMemory` @0x42 | pointer write, current-address read and random read return the EEPROM contents |
| `test_i2c_address_nack_vs_cocotbext_memory` x2 | `i2c-repeated-start` | `I2cMemory` @0x50 | NACK gives fault 65, pins released |
| `test_i2c_target_vs_verilog_master` x2 | `i2c-target-write`, `i2c-target-read` | `i2c_master` | write lands in the RX FIFO; a read started before the host queues the byte is stretched by the DUT (SCL held low for 3 000 clocks) and then completes with NACK |
| `test_i2c_target_vs_cocotbext_master` x2 | the same | `I2cMaster` at 400 kHz | the same without the delayed TX byte |
| `test_jtag_firmware_idcode_scan` x2 | `jtag` | `hazard3_jtag_dtm` | after TAP reset the first four scanned bytes are the IDCODE (LSB first), the next four are the first four TDI bytes (32-bit DR); TAP ends in Shift-DR with IR = IDCODE; JTAG edge check |
| `test_jtag_vector_ir_dr_scans` x2 | `jtag-vector` | `hazard3_jtag_dtm` | IDCODE after reset, IR scan to DTMCS and DTMCS = 0x4071, IR scan to BYPASS and a one-bit BYPASS delay, Test-Logic-Reset restoring IDCODE, a 64-bit scan showing the 32-bit IDCODE register; the value shifted out of IR (0x01, then 0x10) is Hazard3-specific: this TAP captures the current IR, where IEEE 1149.1 requires a capture pattern ending in `01` (`hazard3_jtag_dtm.v` line 110); JTAG edge check |
| `test_flagship_verilog_peers` x2 | the four flagship images + route | `uart_tx`, `uart_rx`, `UartSink`, `SpiSlaveLoopback`, `SPI_Slave` (mode 0, listening only), `i2c_slave` | `firmware/flagship-scenario.json`: all four engines and the UART-RX to SPI-TX route run at once; UART TX words at both receivers, SPI MOSI words at both SPI peers, I2C write at `i2c_slave`, route exhausted; SPI and I2C edge checks |
| `test_flagship_cocotbext_peers` x2 | the same | `UartSource`, `UartSink`, `uart_rx`, `SpiSlaveLoopback`, `I2cMemory` @0x42 and a second `I2cMemory` @0x50 | the same with Python peers; the device at 0x50 must not react |

The flagship runs attach one or more third-party peers to every interface the
scenario uses, not all nine peers at once. The pins do not allow that. JTAG
shares pins 0-3 with UART and SPI and is not part of the scenario. Only one
SPI target can drive MISO, so `spiflash` and the `SPI_Slave` MISO stay
disconnected. The I2C and SPI controller peers (`i2c_master`, `I2cMaster`,
`SPI_Master_With_Single_CS`, `SpiMaster`) would contend with the DUT, which is
the controller in the scenario.

## Clock phase: the wrong-mode matrix

To measure what the SPI tests can tell apart, each of the 12 wrong-mode
substitutions was run. In each one, the image set (`PE_FIRMWARE`) holds the
shipped `spi-controller-mode<Y>` program under the name
`spi-controller-mode<X>`, and the suite's SPI controller tests for mode X (and,
for X = 0, the two flagship tests) run against it. Each substitution ran three
ways. "Old board" is `PE_EXT_SKEW=none`, which gives every DUT output the same
delay, as before this change. "Peers only" uses both skew corners with
`PE_EXT_NO_EDGE_CHECK=1`. "Default" is the suite as shipped, with both corners
and the edge checks. The table lists the tests that failed; the substitution
counts as detected when at least one test fails. Controls: every unmodified
mode passed all three ways. All runs are RTL, Slurm job 23791858, on the
`test/` harness of commit 0ec5138, which has the same `src/` and `firmware/`
as 73536f0 and adds `PE_FIRMWARE`.

| test expects | program run | old board (peers) | peers only, skew corners | default (skew corners + edge checks) |
|---|---|---|---|---|
| mode 0 | mode 1 | `SPI_Slave` (MISO prediction only); flagship not detected | loopback `data`; `SPI_Slave` both; both flagship variants `data` | all 4 SPI tests, all 4 flagship tests |
| mode 0 | mode 2 | `SPI_Slave` (MOSI log); flagship verilog (its `SPI_Slave` listener) | `SPI_Slave` `clock`; flagship verilog `clock` | all 4 SPI, all 4 flagship |
| mode 0 | mode 3 | **not detected** | loopback `data`; both flagship variants `data` | the same |
| mode 1 | mode 0 | loopback, `SPI_Slave` | loopback both; `SPI_Slave` `clock` | all 4 |
| mode 1 | mode 2 | loopback | loopback both | loopback both |
| mode 1 | mode 3 | `SPI_Slave` (MISO prediction) | `SPI_Slave` both | all 4 |
| mode 2 | mode 0 | **not detected** | `SPI_Slave` `clock` | all 4 |
| mode 2 | mode 1 | `SPI_Slave` (MISO prediction) | loopback `data`; `SPI_Slave` both | the same |
| mode 2 | mode 3 | **not detected** | loopback `data`; `SPI_Slave` `clock` | all 4 |
| mode 3 | mode 0 | loopback, `SPI_Slave` | all 4 | all 4 |
| mode 3 | mode 1 | `SPI_Slave` (MISO prediction) | `SPI_Slave` both | all 4 |
| mode 3 | mode 2 | loopback, `SPI_Slave` | all 4 | all 4 |

With the old board, three substitutions went undetected and five more were
caught only by `SPI_Slave`. Four of those five failed only on the MISO bytes
predicted from the peer's own defect. With the skew corners, the third-party
peers alone detect all 12, and `SpiSlaveLoopback` alone detects 8. That includes
the two cases found in review: mode 1 run as mode 0, and mode 3 run as mode 2.

Modes 0 and 3 sample and shift on the same physical SCK edges, and so do modes
1 and 2. Within each pair only the SCK idle level differs, so the edge check
cannot object, and detection depends on how a peer handles the idle level at
the start of a frame. `SpiSlaveLoopback` rejects mode 3 run as mode 0 and mode 1
run as mode 2 only in the `data` corner, and rejects mode 2 run as mode 1 in
both corners. Mode 1 run as mode 2 also fails `SPI_Slave`, and mode 0 run as
mode 3 fails every test. For the same reason, many real devices accept both
mode 0 and mode 3.

## Disagreements and how they were resolved

### nandland `SPI_Slave`: the peer is wrong

With `SPI_Slave` as the target, the DUT controller read other MISO bytes than
the peer was given. The upstream source explains why: both the MOSI sampler and
the MISO shifter are clocked on the rising edge of `w_SPI_Clk = CPHA ? ~SCK :
SCK` (`SPI_Slave.v` lines 69, 75, 138, 154), `w_CPOL` is computed but never used
(line 60; upstream issue #5), and after the preload has put bit 7 on MISO at CS
assertion the shift counter still starts at bit 7 (line 158; upstream issue #11
reports the same-edge problem). Against the SPI mode definitions (CPHA=0:
sample on the leading edge, change on the trailing edge; CPHA=1: change on the
leading edge, sample on the trailing edge; CPOL=1 inverts which physical edge
is leading):

| mode | peer samples MOSI on | peer changes MISO on | predicted MOSI at peer | predicted MISO at controller |
|---|---|---|---|---|
| 0 | rising = leading (correct) | rising = leading, the controller's sampling edge | exact | `(b & 0x80) \| (b >> 1)` |
| 1 | falling = trailing (correct) | falling = trailing, the controller's sampling edge | exact | `(b & 0x80) \| (b >> 1)` |
| 2 | rising = trailing (wrong edge, where the controller changes MOSI) | rising = trailing (correct edge), counter repeats bit 7 | `data` corner: exact; `clock` corner: one bit early, `(b << 1) & 0xFE` in bits 7-1 | `(b & 0x80) \| (b >> 1)` |
| 3 | falling = leading (wrong edge, where the controller changes MOSI) | falling = leading (correct edge) | `data` corner: one bit late, `b >> 1` in bits 6-0; `clock` corner: exact | exact |

In modes 2 and 3 the peer samples MOSI on the edge where a correct controller
changes it, so what it logs depends on which arrives first. The skew corners
make that deterministic. The bit that falls outside the byte is the MOSI level
between bytes, which SPI does not define, and is not checked.

Evidence that the fault is in the peer and not in the DUT:

* The DUT read exactly the predicted bytes in every mode and both corners, e.g.
  responses `5A 80 3C C3 01 FE` were read as `2D C0 1E E1 00 FF` in modes 0, 1
  and 2 and as `5A 80 3C C3 01 FE` in mode 3. `SPI_Slave` received the DUT's
  MOSI bytes `A6 01 FF 5A 3C 80` exactly in modes 0 and 1 (both corners), in
  mode 2 `data` and in mode 3 `clock`, and one bit off as predicted in the
  other two cases (mode 2 `clock`: `4C 03 FF B4 78 00`; mode 3 `data`:
  `53 00 7F 2D 1E 40`).
* With the DUT held idle, the independent cocotbext-spi `SpiMaster` read the
  same bytes from `SPI_Slave` in all four modes
  (`test_nandland_slave_vs_cocotbext_master`).
* `SpiMaster` driving `SPI_Slave` shows the same wrong-edge sampling: in mode 3
  the peer logged `D3 80 FF AD 9E C0` for `A6 01 FF 5A 3C 80`, one bit late.
* The other third-party SPI peers agree with the DUT in both skew corners:
  `SpiSlaveLoopback` in all four modes, `spiflash` in modes 0 and 3 (the only
  modes it supports); as SPI target the DUT agrees with
  `SPI_Master_With_Single_CS` and `SpiMaster` in all four modes.

The test therefore asserts the prediction table, including the defect, so a
change of either side shows up as a failure.

### verilog-i2c `i2c_slave`: the peer is wrong

The first seeded campaign (seeds 1-128 RTL, Slurm array 23761868, and 44
seeds at gate level, array 23761869) found
`test_i2c_read_vs_verilog_slave_with_stretching` and
`test_i2c_repeated_start_vs_verilog_slave` failing in 113 of their 256 RTL runs
and 43 of their 88 gate-level runs, and no other test failed (4 879 passing
test runs in RTL, 1 171 at gate level): the DUT read 0xFF instead of the
target's byte. Every failing case had a byte with bit 7 set and a
supply delay of at least 113 clocks, i.e. `i2c_slave` stretched SCL before the
first data bit; no case with bit 7 clear failed. The cause is in
`i2c_slave.v` lines 387-395: while stretching it holds SCL low with SDA still
at the ACK level (0); when the data arrives it releases SCL and drives bit 7 in
the same clock. With bit 7 = 1, SDA rises together with SCL, which is a STOP
condition (UM10204: SDA may only change while SCL is low; data set-up time
before the SCL rising edge). `i2c_slave` detects that STOP itself (line 238):
in a per-cycle trace (job 23762837) its state went from read-data to idle and
`bus_active` fell during the first data bit; from then on it leaves SDA
released and every bit reads 1.

Evidence that the fault is in the peer: the same author's `i2c_master`,
reading from `i2c_slave` with the DUT idle, gets 0x43 for 0x43 and 0xFF for
0xC3 with the same 400-clock stretch
(`test_i2c_slave_stretch_defect_without_dut`); `cocotbext-i2c` `I2cMemory`
and the project's own peers return correct data; and the DUT reads correctly
from `i2c_slave` whenever it does not stretch or bit 7 is 0. The upstream
issue tracker has no report of this.

A second campaign (arrays 23765327/23765328) showed that a stretch with bit 7
set is necessary but not sufficient: when the data arrives while the DUT still
holds SCL low itself, the peer's release does not raise SCL and the byte reads
correctly (9 of 768 RTL runs and 6 of 384 gate-level runs of the three
`i2c_slave` read tests, on the same seeds in both; nothing else failed in
10 743 RTL and 5 370 gate-level test runs). The tests therefore flag the event itself:
`vi2cs_self_stop` in `tb_ext.v` records `i2c_slave` detecting a STOP while in
its read-data state. They require 0xFF exactly when it is set, the byte
otherwise, and assert that it is only ever set after a stretch with bit 7 = 1.

### UART receiver missed the first frame: the test stimulus was wrong

In the first runs, `uart-rx` lost the first byte (and in the flagship raised
its framing fault 64) whenever the Python `UartSource` started its first start
bit within a few clocks of the host's START command. A per-cycle trace (pin 1,
engine PC) showed the engine still at its `WAITPIN idle-high` instruction
during the whole first frame: the firmware waits for an idle line and then for
a falling edge, which is how a UART receiver finds frame boundaries, and a line
that is already low when the receiver starts cannot be told apart from a frame
in progress. The Verilog `uart_tx` feeder passed only because of its few clocks
of start-up latency. The suite now idles the line for one bit period after START (the
project's own `test/scenarios.py` uses 50 clocks, the flagship recipe 96). This
was a test error, not a design or firmware defect.

### `i2c-repeated-start` keeps the bus started between transactions

Not a disagreement but a firmware property the third-party target makes
visible: after the STOP of a transaction, `i2c-repeated-start` loops to
`transaction`, generates the next START and only then blocks at PULL for the
next TX words, holding SCL low. `i2c_slave` therefore reports an active,
addressed bus (`bus_active` = 1, SCL low) until the host queues the next three
words; `test_i2c_repeated_start_vs_verilog_slave` asserts this state. A host
that instead stops the engine releases SCL and SDA in the same cycle, which is
neither a clean STOP nor a START for a target that was waiting for an address
byte. The EEPROM test runs the repeated-start transaction last for this reason
(the opposite order was not run). `i2c-write` and `i2c-read` block before
their START and leave the bus idle. `docs/firmware.md` says that FIFO holding
points keep SCL low; it does not say that the idle point of this image is
inside a started transaction. Moving the first PULL before the START, as the
other controller images do, would avoid it.

### Integration notes on the third-party models (no DUT involvement)

* verilog-i2c relies on implicit net declarations; `tb_ext.v` restores
  `` `default_nettype wire`` at its end so that the files compiled after it
  elaborate.
* `SPI_Master_With_Single_CS` sizes its CS-inactive counter with
  `$clog2(CS_INACTIVE_CLKS)` bits (line 69), so a power-of-two value such as
  32 truncates to 0 (found by reading the code; the suite uses 100).
* `hazard3_jtag_dtm` loads the current IR into the IR shift register in
  Capture-IR (lines 109-110). IEEE 1149.1 requires a fixed capture pattern
  whose two least significant bits are `01`, so this is a RISC-V debug TAP
  rather than a conformant 1149.1 TAP, and the IR values the JTAG test reads
  back (0x01, then 0x10) are specific to this peer.
* `hazard3_jtag_dtm`'s TDO flop is reset only by a falling `trst_n` or TCK
  edge, and `SPI_Slave` initialises its SPI-clock-domain registers only on a
  rising CS; the board generates those edges during the peer reset.
* `spiflash.v` drives MISO during the command phase (it shifts MOSI through its
  data buffer, so the controller reads the previous byte back), where a real
  flash leaves MISO undriven; the flash test checks this echo and the data
  bytes. Its 16 MB memory array makes each simulator process use about
  700 MB (RTL) to 860 MB (gate level).
* cocotbext-uart's `UartSink` does not check the stop bit (verilog-uart's
  `uart_rx` does, and the test requires zero framing errors from it); it also
  emits a cocotb 2.0 deprecation warning for `setimmediatevalue`.

## Results

All results below are simulations of commit 73536f0 (`src/`, `models/`,
`firmware/`, `test/` taken from a frozen snapshot of that commit) with cocotb
2.0.1 and Icarus Verilog 14, run on MIT Engaging through Slurm. Gate level is
the locally routed LibreLane netlist
(`sram-flow/run2/runs/wokwi/final/nl/tt_um_teslacoilerow_protocol_emulator.nl.v`,
identical to the fill-insertion `nl.v`, SHA-256 `fe135632...a181`; run2 was
hardened from the same `src/` files as 73536f0) with the IHP `sg13cmos5l`
standard-cell and SRAM functional models, no SDF timing. Every run also
compared the DUT with the reference model on every cycle.

| run | stimulus | simulator / DUT | tests | result | Slurm job |
|---|---|---|---|---|---|
| default, RTL | fixed | Icarus 14, RTL | 65 | 65 PASS | 23792571 |
| default, gate level | fixed | Icarus 14, routed netlist | 65 | 65 PASS | 23792571 |
| default, RTL | fixed | Icarus 13.0 (the TT CI version), RTL | 65 | 65 PASS | 23792571 |
| default, RTL, `test/` of commit 0ec5138 (current `test/harness.py`) | fixed | Icarus 14, RTL | 65 | 65 PASS | 23792571 |
| seeded campaign, RTL | seeds 1-512 | Icarus 14, RTL | 33 280 test runs (65 per seed) | 33 280 PASS | array 23792569 (64 tasks) |
| seeded campaign, gate level | seeds 1-256 | Icarus 14, routed netlist | 16 640 test runs (65 per seed) | 16 640 PASS | array 23792570 (64 tasks) |
| SPI wrong-mode matrix | fixed | Icarus 14, RTL | 12 substitutions, 3 ways | see the matrix | 23791858 |

The seeded campaign simulated 212.8 million lockstep clock cycles (4.26 s of
50 MHz operation) in RTL and 106.4 million (2.13 s) at gate level, using about
12 and 73 CPU-hours. Every test that has a skew parameter passed in both
corners for every seed.

Not every seeded `i2c_slave` read checks the DUT's data. When the random byte
has bit 7 set and the random supply delay makes `i2c_slave` the last device
holding SCL, its own defect ends the read, and the test checks only that the
controller then reads 0xFF. Of the 3 072 `i2c_slave` reads in the RTL
campaign, 1 604 compared the byte the DUT read with the byte the peer supplied
(only 112 of them with bit 7 set), and 1 468 (48 %) checked only the defect
prediction. At gate level the figures are 810 (66 with bit 7 set) and 726 of
1 536. Bytes with bit 7 set are read and compared through the other
I2C targets: `I2cMemory` returns random pattern bytes in the seeded EEPROM
test, and 0xFE at register 0x37 with the fixed stimulus.

`test_ext/results/summary.json` holds the per-test outcomes of the four
fixed-stimulus runs, every outcome of the wrong-mode matrix and the campaign
totals, together with the SHA-256 of the netlist and of the `test_ext/` files
that were run. Job logs and per-seed results stay on the cluster
(`$PE_WORK/peers/`, `manifest.json`,
`campaign4/summary.json`).

Earlier results, before the skew corners were added (one 1 ns delay for all
DUT outputs, 42 tests): RTL, gate level and Icarus 13.0 each 42/42 PASS
(job 23771062), and seeded campaigns of 10 752 RTL and 5 376 gate-level test
runs, all PASS (arrays 23771060 and 23771061). The two campaigns before those
(arrays 23761868/23761869 and 23765327/23765328) found the `i2c_slave` defect
described above.

## Running

```sh
cd test_ext
make                                   # RTL, all modules, both skew corners (about 3 minutes on one core)
make COCOTB_TEST_MODULES=test_ext_jtag # one module
PE_EXT_SEED=7 make                     # random stimulus, reproducible by seed
PE_EXT_SKEW=clock make                 # one skew corner only (data, clock or none)
PE_EXT_NO_EDGE_CHECK=1 make            # leave the timing verdict to the peers alone
make GATES=yes PDK_ROOT=/path/to/pdk GL_NETLIST=/path/to/tt_um_teslacoilerow_protocol_emulator.nl.v
```

Requirements are those of `test/` (Icarus Verilog, cocotb 2.0.1). The
cocotbext packages are vendored and put on `PYTHONPATH` by the Makefile, so
nothing else needs to be installed. The suite imports `test/harness.py` and
`test/model/` unchanged. Each simulator process needs about 0.7 GB of memory
(0.86 GB at gate level) because of the 16 MB array in `spiflash.v`.

## Limits

These are digital simulations against behavioural models, not measurements.
The board model has ideal pull-ups. The skew corners model the order in which
same-edge changes reach a peer, not real pad delays, slew or set-up and hold
margins. The pads are sampled on the falling clock edge before the DUT's own
two-flop input synchronizer, which adds up to one clock of input latency. The
gate-level runs are functional (zero delay, no SDF). The edge checks are this
project's reading of the SPI, JTAG and I2C rules, not a third-party verdict.
The peers are models of devices, not the devices: two have defects of their own
and others simplify (above). `hazard3_jtag_dtm` is a RISC-V debug transport
module rather than a boundary-scan TAP, and its IR capture value differs from
IEEE 1149.1. The JTAG IR/DR scans and the SPI flash transactions use the
test-only programs described above, not shipped images. Modes 0 and 3, and
modes 1 and 2, differ only in the SCK idle level, and whether a peer rejects
the wrong one of such a pair depends on that peer (see the wrong-mode matrix).
