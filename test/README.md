# cocotb verification suite

Tests for `tt_um_teslacoilerow_protocol_emulator` (`../src/project.v` wrapping the
Hardcaml-generated `../src/protocol_emulator_core.v`). Every test runs the DUT in
**lockstep** with an independent pure-Python reference model of ISA v2
(`model/reference.py`, see `../reference/isa.md`): on every 20 ns clock the harness
compares `uo_out`, `uio_out` and `uio_oe` both after the edge and before the next
edge (the combinational window-change bubble on ready/valid). Read nibbles are
compared while read-valid is high, which covers every word the host reads. On top
of that, protocol results are checked at the pads by independent monitors and
behavioral peers.

## Layout

| file | content |
|---|---|
| `tb.v` | testbench top: instantiates the TT top, dumps `tb.fst` (TT ports only by default) |
| `Makefile` | cocotb makefile: RTL (default) and gate level (`GATES=yes`) |
| `harness.py` | lockstep harness (`CocotbHarness`, plus `ModelHarness` for model-only runs) and the nibble host driver: windows, ready/valid, eight little-endian nibbles per word, window-change bubble, SELECT/BEGIN/COMMIT/OWN/START/STOP/ROUTE/CLEAR/READ_SELECT/EVENT/FLUSH/TRIGGER, `firmware/*.image.json` loading with SHA-256 identity checks |
| `peers.py` | pad-level peers: UART source and streaming UART monitor, SPI target (modes 0-3), wired-AND open-drain I2C bus |
| `scenarios.py` | directed scenarios shared by the tests (runnable on the model alone) |
| `test_smoke.py` | reset/deselect, ISA version, loader (BEGIN/OWN/COMMIT/START/STOP), status registers, host-fault rejections, checker self-test |
| `test_protocols.py` | `uart-tx` (decoded from pin 0), `uart-rx` (pin 1 driven, RX FIFO read; idle timeout and framing faults), `spi-controller-mode0..3` against the SPI target, `i2c-write`/`i2c-read`/NACK against the open-drain target |
| `test_flagship.py` | `../firmware/flagship-scenario.json`: four engines concurrently (UART TX, UART RX, SPI, I2C) plus the autonomous UART-RX to SPI-TX route |
| `test_legacy.py` | lockstep replay of the 25 monorepo differential workloads (`model/verification.py`): queues, DMA congestion, SPI mode matrix, strict push, input triggers, JTAG, waveform, I2C target, UART overflow, ... |
| `test_random.py`, `random_gen.py` | constrained-random lockstep differential test with functional coverage and a minimizer |
| `model/` | the reference model, host recorder and wire scoreboards, copied from the asic-lab monorepo (stdlib only; provenance and hashes in `model/__init__.py`) |

## Running

Requirements: Icarus Verilog (the TT actions use 13.0), Python 3.11+, and
`pip install -r requirements.txt` (cocotb 2.0.1, pytest 8.4.2).

RTL (what the TT `test` action runs):

```sh
cd test
make -B
```

The RTL run compiles `../src/project.v`, `../src/protocol_emulator_core.v` and the
IHP SRAM behavioral models `../models/RM_IHPSG13_1P_64x16_c2.v` +
`../models/RM_IHPSG13_1P_core_behavioral.v` with `-DFUNCTIONAL`. The full suite
takes about 70-120 s (about 3,500 lockstep cycles/s).

Useful variables:

```sh
make COCOTB_TEST_MODULES=test_protocols         # one module
make COCOTB_TEST_FILTER=test_uart_tx            # tests matching a regex
make SIM_BUILD=/scratch/me/sim_build            # keep build output elsewhere
make WAVES=all                                  # dump the whole hierarchy (slow, large)
make WAVES=none                                 # no waveform
```

Gate level (what the TT `gl_test` action runs after hardening; it copies the
hardened netlist to `test/gate_level_netlist.v`):

```sh
cp /path/to/hardened/tt_um_teslacoilerow_protocol_emulator.v gate_level_netlist.v
make -B GATES=yes PDK_ROOT=/path/to/pdk
```

GL mode adds `$(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_{stdcell,io}/verilog`
and the same SRAM behavioral models (the netlist keeps the eight
`RM_IHPSG13_1P_64x16_c2` macros as instances). `GL_NETLIST=path` simulates a
netlist kept elsewhere. The Makefile exports `PE_GATE_LEVEL=1`, which scales the
workload down: a representative subset of the legacy replays and 2 random cases
(override with `PE_LEGACY=all` and `PE_RANDOM_ITERS`). A quick pre-hardening check
is possible with a Yosys netlist (`synth -flatten`, `dfflibmap`/`abc` to the
`sg13cmos5l_stdcell` liberty, SRAM macro read as a blackbox).

## Random lockstep test

`test_random.py` generates `PE_RANDOM_ITERS` independent cases from `PE_SEED`.
Each case assigns disjoint pin ownership (with random open-drain subsets), loads a
random legal program into each engine (NOP/HALT/SET/DIR/WAIT/JMP/PULL/PUSH/OUT/IN/
COUNT/LOOP/LIMIT/WAITPIN/SIGNAL/WAITEVENT/PINS/XFER/MOV/LOAD/ALU/SHL/SHR/JZ/NOT/
TIME/FAULT; computed registers are often pushed, shifted out or branched on so
that differences become observable), prefills TX FIFOs, configures routes and
input triggers, starts the engines and then runs random host traffic: TX writes
under backpressure, RX reads and drains, status reads of every READ_SELECT,
EVENT/ROUTE/STOP/START/CLEAR/FLUSH/TRIGGER/OWN commands (some rejected on
purpose), abandoned partial reads and writes, reprogramming, fault recovery and
deselection. External pins toggle randomly; driven pins loop back. A few
instructions per program are deliberate faults (invalid opcode/operand, pin
ownership, PC range, FAULT n); every fault must carry a code allowed for the
faulting instruction and deliberate ones must carry their annotated code.

| variable | default | meaning |
|---|---|---|
| `PE_SEED` | `0x5EED2027` | base seed (`random` picks one and logs it) |
| `PE_RANDOM_ITERS` | 64 (RTL), 2 (GL) | number of cases |
| `PE_RANDOM_FIRST` | 0 | first case index (rerun one case with `PE_RANDOM_ITERS=1`) |
| `PE_RANDOM_CYCLES` | 2000 | host-traffic cycles per case after START |
| `PE_MINIMIZE` / `PE_MINIMIZE_BUDGET` | 1 / 60 | shrink a failing case (greedy delta debugging over host ops, engines, setup, instructions) |
| `PE_REPLAY` | | run a saved case JSON instead of generating |
| `PE_INJECT_MODEL_BUG` | | `xor`: corrupt the model after XOR, to demonstrate detection and minimization |

On a mismatch the test logs the seed, case index, cycle, the last 24 cycles of
pins, the program listings and host operations, writes
`output/random-failure-seed<seed>-case<n>.json` (and `-min.json` after
minimization) and prints a one-line reproduction command. A functional-coverage
summary (opcodes completed per engine, stall cycles per blocking opcode, faults by
code and instruction, deliberate faults, XFER CPOL/CPHA/bit-order/drive/sample
combinations and bit counts, mover transfers, host commands accepted/rejected,
host traffic) is printed at the end of every run.

## Model-only development

`ModelHarness` runs the same scenario coroutines against the reference model alone,
without a simulator, for fast iteration:

```sh
cd test
python3 -c "import harness, scenarios; h = harness.run_model(scenarios.flagship); print(h.cycle)"
```

## Waveforms

```sh
gtkwave tb.fst tb.gtkw
```
