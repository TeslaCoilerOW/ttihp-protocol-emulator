# cocotb verification suite

Tests for `tt_um_teslacoilerow_protocol_emulator` (`../src/project.v` wrapping the
Hardcaml-generated `../src/protocol_emulator_core.v`). Every test runs the DUT in
**lockstep** with an independent pure-Python reference model of ISA v2
(`model/reference.py`, see `../docs/isa.md`): on every 20 ns clock the harness
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
| `harness.py` | lockstep harness (`CocotbHarness`, plus `ModelHarness` for model-only runs) and the nibble host driver: windows, ready/valid, eight little-endian nibbles per word, window-change bubble, SELECT/BEGIN/COMMIT/OWN/START/STOP/ROUTE/CLEAR/READ_SELECT/EVENT/FLUSH/TRIGGER, `firmware/*.image.json` loading with SHA-256 identity checks; the time warp (`warp`, `warp_completed`, `warp_routes`, see "Time warp") |
| `peers.py` | pad-level peers: UART source and streaming UART monitor, SPI target (modes 0-3), wired-AND open-drain I2C bus |
| `scenarios.py` | directed scenarios shared by the tests (runnable on the model alone) |
| `test_smoke.py` | reset/deselect, ISA version, loader (BEGIN/OWN/COMMIT/START/STOP), status registers, host-fault rejections, checker self-test |
| `test_protocols.py` | `uart-tx` (decoded from pin 0), `uart-rx` (pin 1 driven, RX FIFO read; idle timeout and framing faults), `spi-controller-mode0..3` against the SPI target, `i2c-write`/`i2c-read`/NACK against the open-drain target |
| `test_flagship.py` | `../firmware/flagship-scenario.json`: four engines concurrently (UART TX, UART RX, SPI, I2C) plus the autonomous UART-RX to SPI-TX route |
| `test_legacy.py` | lockstep replay of the 25 monorepo differential workloads (`model/verification.py`): queues, DMA congestion, SPI mode matrix, strict push, input triggers, JTAG, waveform, I2C target, UART overflow, ... |
| `test_random.py`, `random_gen.py` | constrained-random lockstep differential test with functional coverage and a minimizer |
| `test_directed.py` | directed tests written from the mutation campaign's survivor analysis (full-capacity image, OWN overlap, operand check, ROUTE counts above 4095, FLUSH of a routed engine, COUNT/LOOP, 40,003 completed instructions, blocked count after ALU/TIME, LIMIT 0x2108, SHR into bit 15, odd XFER half-period, far jump targets, XFER next to driven pins) |
| `test_mover.py` | mover (DMA) arbitration: round robin among 2, 3 and 4 simultaneously eligible routes (one a self-route) and host TX priority, with an independent per-edge monitor of the arbitration rule |
| `test_counters.py` | long-running counters read back: more than 2^16 completed instructions on every engine, a 0xFFFF-word ROUTE drained to zero, COUNT 0xFFFF loops, timestamp past 2^16, ROUTE counts draining exactly, LIMIT values with each bit 9..23 set, exact WAITPIN/WAITEVENT timeouts, exact WAIT ends |
| `test_timewarp.py` | counter carries up to bit 31 and the 2^32 timestamp rollover by time warp: timestamp/TIME, completed counts while every opcode runs, WAIT timers, blocked counts and LIMIT timeouts, blocked-count clearing by every kind of instruction, repeat counters, ROUTE counts |
| `model/` | the reference model, host recorder and wire scoreboards, copied from the asic-lab monorepo (stdlib only; provenance and hashes in `model/__init__.py`); `model/variant.py` (written here) adds the design variants |
| `variants.py`, `variant_workloads.py` | design-variant selection (`PE_VARIANT`) and the legacy workloads recorded on a variant; see "Design variants" |

## Running

Requirements: Icarus Verilog (the TT actions use 13.0), Python 3.11+, and
`pip install -r requirements.txt` (cocotb 2.0.1, pytest 8.4.2).

RTL (what the TT `test` action runs):

```sh
cd test
make clean
make
```

The RTL run compiles `../src/project.v`, `../src/protocol_emulator_core.v` and the
IHP SRAM behavioral models `../models/RM_IHPSG13_1P_64x16_c2.v` +
`../models/RM_IHPSG13_1P_core_behavioral.v` with `-DFUNCTIONAL`. The full suite
takes about 1.5 to 4 minutes (66 tests, about 0.55 M lockstep cycles at 3,500 to
8,000 cycles/s depending on the machine; 89 s for `make clean; make` with Icarus
13.0 on an MIT Engaging node, of which the gap-closure modules take 27 s).

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
make clean
make GATES=yes PDK_ROOT=/path/to/pdk
```

GL mode adds `$(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_{stdcell,io}/verilog`
and the same SRAM behavioral models (the netlist keeps the eight
`RM_IHPSG13_1P_64x16_c2` macros as instances). `GL_NETLIST=path` simulates a
netlist kept elsewhere. The Makefile exports `PE_GATE_LEVEL=1`, which scales the
workload down: 8 of the 25 legacy replays (`GL_SUBSET` in `test_legacy.py`; the
other 17 are reported as SKIP) and 2 random cases (override with `PE_LEGACY=all`
and `PE_RANDOM_ITERS`). Of the gap-closure modules, the tests that need more than
8,000 cycles and the time-warp tests (which need the RTL register names) are
reported as SKIP; 14 of their 27 tests run. A quick pre-hardening check
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

Generator generation 2 (the default; `PE_RANDOM_GEN=1` draws the cases of the
2026-09-25 verification campaigns, whose stimulus it reproduces except that the
trailing deselect of 10% of the cases now runs) adds two things from a separate
random stream, so every generation-1 operation is still drawn identically:

- **Deselection inside the traffic.** Generation 1 appended the optional deselect
  (10% of cases) after operations whose estimated cost already filled the
  budget, and `run_case` stopped at the budget, so it ran in about 0.3% of cases.
  It now sits at a random point in the first 70% of the traffic (ena drops while
  engines run; the rest of the case runs on the deselected chip), and
  `run_case` never skips a deselect for the budget. Measured on the model
  (1,088 cases): 107 of 107 deselects run, 99 of them inside the budget and 101
  with engines running (generation 1: 3 inside the budget).
- **Command-rejection sequences.** In 70% of cases one to three sequences the
  first generation never sends: BEGIN with a payload, COMMIT with a wrong, zero
  or oversized length or while not loading, STOP and EVENT naming absent
  engines, ROUTE with payload bits 21 to 23, and aborted or overfull reloads
  (BEGIN of a running engine, START before COMMIT, a 65th program word). The
  model decides acceptance; half of the sequences end with CLEAR bit 23. On the
  default seed all twelve commands are now both accepted and rejected
  (BEGIN/COMMIT/STOP/ROUTE/EVENT rejected 13/23/9/6/7 times).

| variable | default | meaning |
|---|---|---|
| `PE_SEED` | `0x5EED2027` | base seed (`random` picks one and logs it) |
| `PE_RANDOM_ITERS` | 64 (RTL), 2 (GL) | number of cases |
| `PE_RANDOM_FIRST` | 0 | first case index (rerun one case with `PE_RANDOM_ITERS=1`) |
| `PE_RANDOM_CYCLES` | 2000 | host-traffic cycles per case after START |
| `PE_RANDOM_GEN` | 2 | generator generation (1: the campaigns' cases) |
| `PE_MINIMIZE` / `PE_MINIMIZE_BUDGET` | 1 / 60 | shrink a failing case (greedy delta debugging over host ops, engines, setup, instructions); replays are shrunk too |
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

## Gap-closure tests

The random and mutation campaigns (`../docs/verification-campaign.md`) showed
what the suite above left unexercised: high counter bits, mover arbitration,
command rejections and a few per-engine corner cases. Four modules close those
gaps; every scenario is an `async def f(h)` on a Harness, so it also runs on the
model alone, and every cycle is still compared with the model.

| module | tests | cycles | what it adds |
|---|---:|---:|---|
| `test_directed.py` | 14 | 75,000 | the twelve directed tests of the mutation campaign (`../campaigns/mutation/directed/`), cleaned up and made variant-independent (queue depth, debug counters, byte-lane shifts), and two from the re-run of its survivors: JMP/LOOP/JZ to targets with each PC bit 6..23 set (fault 2, full target read back) and XFER on every TX pin next to driven pins |
| `test_mover.py` | 1 | 11,000 | six phases: 2, 3 and 4 routes (one a self-route) into one destination whose TX queue is full, so all become eligible on one edge; the destination forwards the words to the host (the grant order is on the read nibbles); host TX words written while routes compete, in two phases built so that every host word meets an eligible route to the same destination. `ArbitrationMonitor` restates the arbitration rule of `../docs/isa.md` and checks every grant and the cursor; the test requires edges with 2, 3 and 4 eligible routes and host-priority collisions |
| `test_counters.py` | 5 | 98,000 | every engine completes more than 2^16 instructions while a 0xFFFF-word route drains and COUNT 0xFFFF/0x7FFF/0x8001 loops run, READ_SELECT 1 and 5 read at checkpoints; ROUTE counts of 0x1A5B/0x1C3D drain exactly and 2^k + 2 counts stay enabled; LIMIT (1 << k) \| 24 for k = 9..23 must not time out early; exact timeouts at LIMIT 1..0x3FF; exact WAIT ends |
| `test_timewarp.py` | 7 | 15,000 simulated | counter carries up to bit 31 and the 2^32 timestamp rollover (below) |

### Time warp

Brute-force simulation reaches about 2^17 cycles in the CI budget; the counters
are 16 to 32 bits wide and the timestamp wraps after 2^32 cycles. The harness
therefore skips time when nothing but counting can happen:

- `h.warp(n)` checks on the model that the next `n` edges are pure counting
  (every engine halted, inside a WAIT that outlasts the skip, blocked in a
  WAITPIN/WAITEVENT short of its LIMIT, or spinning on a JMP/LOOP to itself;
  host idle; static pins already synchronized; no trigger; no DMA move
  possible), advances the model's timestamp, WAIT timers, blocked counts,
  completed counts and repeat counts by that arithmetic, and adds the same
  amounts to the core's registers at the next falling clock edge through the
  simulator. The deposit is relative, so a DUT that had already diverged keeps
  its divergence. Engines whose counters move must all move alike, because
  Hardcaml's de-duplicated register names (`wait_timer`, `wait_timer_0`, ...)
  do not follow the engine index.
- `h.warp_completed(n)` adds `n` to every completed-instruction count (read only
  by READ_SELECT 5 and its own update, so any engine activity may continue).
- `h.warp_routes(n)` takes `n` words off every enabled ROUTE descriptor
  (`route_remaining_<source>`).

Each warp is followed by ordinary lockstep cycles, so each scenario walks a
counter family across every 2^k boundary and then lets it reach an
architectural event on the exact cycle (WAIT end, LIMIT timeout, LOOP exit,
route expiry) or reads it back: `timestamp_rollover` (READ_SELECT 1 and TIME
across 2^16..2^32), `completed_rollover` (READ_SELECT 5 across 2^16..2^32 while
every engine loops through all opcodes), `blocked_paths` (each of 29 ways to
complete an instruction, then a WAITPIN with LIMIT 0xFFFFFF warped to four
cycles short of the limit: a count left behind times the DUT out),
`wait_itinerary` (WAIT 0xFFFFFF), `blocked_itinerary` (LIMIT 0xFFFFFx timeouts
and releases), `repeat_itinerary` (COUNT 0xFFFF) and `route_itinerary` (four
0xFFFF-word routes carrying words around a ring). The warps are white-box
stimulus; the checks stay black-box. They need the RTL register names, so the
module is skipped at gate level; `h.warp_supported()` tells.

## Design variants (PE_VARIANT)

The area and reset variants of `../docs/area-study.md` (defined in
`../docs/isa.md`, "Configuration variants") run the same suite:

```sh
make PE_VARIANT=diet4                       # ../build/variants/diet4/protocol_emulator_core.v
make PE_VARIANT=cn PE_CORE=/path/to/cn.v    # a core kept elsewhere
```

| name | reset | queue words | ISA knobs | ISA version |
|---|---|---:|---|---:|
| `base` (default) | `sync` | 8 | none | 2 |
| `rstreg` | `sync_registered` | 8 | none | 2 |
| `cn` | `async` (+ FIFO storage reset, narrow image registers) | 8 | none | 2 |
| `cn_s2` | `async_sync_release` (+ the same) | 8 | none | 2 |
| `diet4` | as `cn_s2` | 4 | no debug counters, 7-bit saturating PC, byte-lane shifts | 3 |
| `diet2` | as `cn_s2` | 2 | as `diet4` | 3 |

`base` is exactly the Tiny Tapeout CI run: the same file list
(`../src/protocol_emulator_core.v`), `sim_build/rtl`, and the verbatim
`model/reference.py`; its random cases and lockstep cycle counts are unchanged.
For any other name:

- **Core and configuration.** The core is `../build/variants/<name>/protocol_emulator_core.v`
  (`scripts/gen_variants.sh`) or `PE_CORE`. The model is configured from
  `../configs/variants/<name>.json`, which must match the independent table
  `variants.SPEC` (checked at import; a mislabelled config fails every test).
- **Firmware images.** The images reassembled for the variant
  (`../build/variants/<name>/firmware`) when present, else `../firmware/`;
  `PE_FIRMWARE=dir` overrides. A variant image must target the variant's
  architecture. Every image in `../firmware/` is valid on every variant (shift
  counts are all 24, targets at most 54).
- **Model.** `model/variant.py` subclasses the verbatim reference: queue depth,
  counters, saturating PC, byte-lane faults, ISA version, and the reset
  styles, modelled with the two synchronizer flops explicitly.
- **Reset latency.** `Harness.reset()` idles until a reset still in flight has
  been applied and released (two extra cycles for `rstreg`, `cn_s2`, `diet*`).
  With an asynchronous reset the pre-edge outputs are also checked to be
  released during reset. `test_smoke` checks that write-ready stays low for
  exactly the reset latency after `rst_n` rises.
- **Small queues.** `uart-tx` prefills `min(8, fifo_words)` words; the SPI tests
  top up TX and read MISO words as they arrive; `test_flagship` runs
  `../firmware/flagship-scenario-topup.json` (the host prefills at most
  `fifo_words` words and then polls: top up UART TX, drain SPI RX).
  `PE_FLAGSHIP=topup` forces that scenario on any design, `PE_FLAGSHIP=prefill`
  the original.
- **Random test.** Byte-lane designs draw byte-lane shift counts, 15% of them
  deliberately not a lane (expected fault code 1). Saturating-PC designs make
  8% of jumps and branches target 128 or more. The coverage summary counts
  byte-lane faults and saturated jumps.
- **Legacy replays.** The monorepo workloads are recorded against the variant
  model (`variant_workloads.py`): a host that waits out the reset latency, and
  READ_SELECT 7 checked against the variant's version. Workloads that state a
  base-only fact run an adapted copy (`variant_workloads.ADAPTED`; each is
  cycle-identical to the original under the base configuration) or a live
  substitute (`SUBSTITUTE`):

  | workload | adapted for | change |
  |---|---|---|
  | `dma`, `congestion`, `firmware_i2c_restart`, `firmware_jtag` | queues < 3 words | at most `fifo_words` words before START, the rest after START under backpressure |
  | `random_alu` | byte-lane shifts | shift counts rounded down to a lane |
  | `firmware_i2c_target_write` | queues < 8 words | RX prefilled to `fifo_words` words |
  | `firmware_waveform` | reset latency | TIME compared against the index of the last reset edge |
  | `firmware_flagship`, `firmware_flagship_fast` | queues < 8 words | substituted by the top-up flagship scenario (mode-0 and fast SPI image) |

Gate level works the same way (`make GATES=yes PE_VARIANT=<name> GL_NETLIST=...`),
running the gate-level subset against the variant model. With a synchronous
clear, flip-flops have `RESET_B` tied high and power up X in simulation.
Synthesis may then implement a cleared register's next state as reconvergent
logic that is 0 in hardware but X in simulation (for example
`D = ~(Q | ~Q)` while the clear is active), so the register never leaves X.
One such register was seen in a plain-Yosys netlist of `rstreg`, and it fails
the gate-level read-back of READ_SELECT 5. The asynchronous-reset variants
cannot hit this, because their flip-flops are reset through `RESET_B`.
Whether a netlist has such logic depends on the synthesis script. The
LibreLane-replica netlists (`docs/area-study/scripts/synth3.sh ll66`) of all
six variants, `rstreg` included, pass the gate-level subset
(`docs/variants.md` section 7.2). The hardened netlist that the Tiny Tapeout
`gl_test` runs is a different netlist again.

Model-only (no simulator) runs take the same variable:
`PE_VARIANT=diet2 python3 -c "import harness, scenarios; print(harness.run_model(scenarios.flagship_topup).cycle)"`.

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
