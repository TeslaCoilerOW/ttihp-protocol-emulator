# Static timing analysis of firmware images

`tools/timing/pe_timing.py` is a static, cycle-exact timing analyzer for
protocol-emulator firmware images (`firmware/*.image.json`, ISA v2). For every
image it computes when each pad changes, down to the clock edge. Timing is
measured from the most recent point where the firmware waits for the outside
world. The tool then checks the protocol timing the image declares. This page
explains the method and states what is and is not guaranteed. It also covers
how the results combine with the formal timing-isolation proof
(`formal/README.md`) into an end-to-end claim. Finally it gives the results for
the 19 committed images and the flagship scenario. The numbers come from
commit 73536f0, except those for the three I2C controller images regenerated
after it (Results, Findings 1 to 3 and 8).

Validation is described in [Validation](#validation). All of it was run for
this page and passes. One *protocol* check failed on a 73536f0 image; no check
fails on the regenerated images; see [Findings](#findings). The formal proofs were not re-run for this page; their
results are quoted from `formal/README.md`.

## Timing model

These rules come from `docs/isa.md` and follow the executable reference
model (`test/model/reference.py`):

- An instruction *issues* on a clock edge. Its pin write is visible on the pads
  right after that edge.
- A nonblocking instruction costs 1 cycle. `WAIT n` costs n+1. `LOOP` and `JZ`
  cost 1 whether or not the branch is taken. `COUNT n` gives n+1 iterations.
- `XFER a,b` (fused issue only) does the following:
  - On its issue edge, it drives the clock to idle and, for CPHA0 with drive
    enabled, the first data bit.
  - It then makes exactly `2a` clock transitions, one every `b` edges.
  - The PC advances on the last transition, so the next instruction issues
    `2ab+1` edges after `XFER`.
  - CPHA0 samples on active transitions and shifts on idle ones. CPHA1 does the
    opposite.
  - In scalar configurations `XFER` faults with code 1.
- `PULL`, `PUSH a=0`, `WAITPIN` and `WAITEVENT` can block. `PUSH a=1` (strict)
  never blocks: on a full RX FIFO it faults with code 4 instead.
  - `WAITPIN` and `WAITEVENT` fault with code 3 on their LIMIT-th unsuccessful
    attempt. They can therefore stall for at most LIMIT-1 edges.
  - A blocking instruction that finds its condition already true costs one
    edge, like any other instruction.
- Every pad input passes through two flip-flops. A value sampled on edge `e` is
  seen by the engine on edge `e+2`. An engine's own write on edge `w` therefore
  reaches its own `WAITPIN`/`IN` on edge `w+3` at the earliest (*loopback
  latency*).
- An open-drain pin's physical output enable is (logical OE AND NOT value), and
  its output value is always 0. `SET` high therefore *releases* an enabled
  open-drain pin.
- `HALT`, any fault, `STOP` and reset release every owned pad on that edge.

The analyzer tracks each owned pad as a set of possible physical states:
driven 0, driven 1, or released (Z). A *pad event* is an instruction or `XFER`
transition that can change that state. The validation observes the same
quantity on the model's public `uio_out`/`uio_oe`.

## Method

**Boundaries and anchors.** Execution is cut at the four blocking instructions
above. These are the *synchronization boundaries*. The edge on which a boundary
completes, or the START edge, is the *anchor* of the next segment. Its first
instruction issues at anchor+1. Between boundaries every cost is static, so each
issue time and each pad event is an exact offset from the anchor.

**Abstract execution.** The analyzer decodes the image and executes it
symbolically from START. It tracks the following state; `None` means the value
is unknown:

- the four registers, with 32-bit wrap;
- the repeat counter, LIMIT and the `PINS` selection;
- the logical value and output-enable bits of each pin.

Unknown values come from `PULL` (TX word), `IN`/`XFER` sampling and `TIME`.

- **Counted loops.** `COUNT`/`LOOP` loops are unrolled exactly, because the
  repeat counter is a static immediate.
- **Data-dependent branches.** A `JZ` on an unknown register forks the path into
  *variants*, and each variant is exact. The taken side learns that the
  register is 0. This constant propagation resolves, for example, the
  three-byte counter of `i2c-repeated-start`. The analyzer therefore knows the
  repeated START follows the second byte and not some other one.
- **Boundary-free loops.** A loop that returns to an identical state without
  crossing a boundary is reported as periodic, with its exact period. A
  data-dependent exit from such a loop (`JZ` inside it) becomes a zero-stall
  *soft boundary*, so exits on any iteration are represented exactly. This
  matters for the scalar SPI/JTAG expansions and for random programs, not for
  the committed fused images.

**Boundary graph.** Each boundary is analyzed once per distinct *context*
(abstract state at arrival). Examples are one context per iteration of the
`COUNT 7` bit loop in the I2C images, or per byte of `i2c-repeated-start`. The
nodes are (boundary, context) and the edges are path variants. Every committed
image gives a finite exact graph with 2 to 67 contexts, with no widening and no
budget cut. The tool can merge contexts (widening) and will then say the result
is over-approximate, but no committed image needs it.

**Stall bounds.** Each boundary has a minimum and maximum stall.

- Maximum: infinite for `PULL`/`PUSH`, LIMIT-1 for `WAITPIN`/`WAITEVENT`.
- Minimum: 0, except for a `WAITPIN` on a pin the engine drives itself. That
  wait reads the pad state of 3, 2 and 1 edges before arrival, and the last of
  these persists while it waits. For example, an I2C controller that releases
  SCL on edge `w` cannot see it high before `w+3`.
- If the engine forces the opposite level at all three look-back points, the
  wait can only time out. The tool reports this as a *self-deadlock*.

**Queries.** Spacings and protocol parameters use one operation: the
distance from an event to the next event matching a predicate.

- Inside a variant, the distance is exact.
- Across boundaries, the minimum uses minimum stalls (shortest-path fixpoint
  over the graph).
- The maximum uses LIMIT-1 or infinity (longest path). It becomes infinite if a
  match-free cycle exists.

The tool derives the following from these queries: per-pin minimum and maximum
edge spacing, loop-carried periods (header issue to header issue), worst-case
cycles between boundaries (WCET), and every protocol parameter below.

## Declared timing and protocol checks

The images declare their timing in three places:

- **Per-instruction timing table.** The assembler emits `{pc, minimum_cycles,
  blocking}` for every instruction. The tool checks each entry against its own
  cost model. All 19 tables agree.
- **Notes.** The tool parses the timing declarations in each image's `notes`,
  for example:
  - "exact bit period 64 clocks";
  - "64 clocks per bit", "bounded by 12 bit periods";
  - "Fused transfers have 32-cycle half periods";
  - "each external half period at least 8 clocks", "asserted at least 8 system
    clocks before first edge";
  - "each low/high phase at least 16 system clocks".
- **`docs/firmware.md`.** It supplies the pin roles and the default half-period
  of 32 clocks, which the waveform, event-transmitter and I2C phases use.

The I2C bus limits come from NXP UM10204 Rev. 7.0 (2021), Table 11
(Standard-mode, Fast-mode and Fast-mode Plus).

All values are digital schedules at the chip's pins, in clock cycles. Where an
external edge's phase within the clock period is unknown, one cycle is added.
The tables convert cycles to time at 10, 25, 50, 66 and 100 MHz. For
requirements stated in absolute time, they give the clock range over which the
requirement holds.

### Results (50 MHz annotation)

The images were first analyzed at commit 73536f0: 182 PASS, 1 FAIL, 3 WARN,
55 INFO. The FAIL and the three WARNs are Findings 1 and 2 below.

The three I2C controller images were then regenerated from
`hardcaml/lib/firmware.ml` with Findings 1 to 3 addressed; the other 16 images
are byte-identical. For the regenerated set the report gives 183 PASS, 0 FAIL,
0 WARN, 55 INFO (Slurm jobs 23975273 and 23986537). Apart from the I2C
controller rows, every check has the same status and text as before. The
flagship engine-3 WCET between boundaries changes from 107 to 106 cycles.
`tools/timing/report/` holds the report for the regenerated set (every check,
boundary table and edge schedule), with the validation summary of run r8
below. The table describes the regenerated set.

| image(s) | key static results |
|---|---|
| `uart-tx` | Start bit and all 8 data bits last exactly 64 cycles (declared) in every context. The stop bit is at least 68 cycles (4 cycles of PULL/loop overhead). The line idles high at the PULL holding point. Back-to-back frame period is at least 644 cycles, so 781 250 Bd at 50 MHz. |
| `uart-rx` | Data bits are sampled 32 to 33 cycles into each 64-cycle bit and the stop bit 35 to 36 cycles in. The receiver re-arms 619 cycles after a start edge, 20 cycles before the next back-to-back start. Sampling tolerates a transmitter bit period between -4.37% and +6.08% of nominal. Continuous traffic with one stop bit needs the transmitter to be no more than 3.12% fast. Idle wait LIMIT is 768 = 12 x 64 (declared). |
| `spi-controller-mode0..3`, `-fast` | 16 SCK edges per byte, spaced exactly 32 (or 16) cycles (declared). SCK sits at CPOL and CS is high at every holding point. CS setup is 33 (17) cycles, CS hold after the last edge 1 cycle, CS high between frames at least 6 cycles. MOSI is stable at least 32 (16) cycles around each target sampling edge. In modes 1/3 the last bit's hold is 1 cycle. MISO round-trip budget is `b-2` = 30 (14) cycles = 600 (280) ns at 50 MHz. |
| `spi-target-mode0..3` | MISO is valid 4 to 5 cycles (modes 0/2) or 3 to 4 cycles (modes 1/3) after the launching edge is sampled: margin 3 or 4 cycles against the declared 8-cycle half period. MOSI is read 1 to 2 cycles after the sampling edge (margin 6). The firmware waits for the next edge within 3 cycles. CS-to-first-edge work takes 2 to 3 cycles against the declared 8. MISO is released at the holding point. |
| `i2c-write`, `i2c-read`, `i2c-repeated-start` | Open-drain only (never driven high). Every SCL release is followed by `WAITPIN SCL==1` before SCL is driven low again (stretch-safe). SDA changes only while SCL is low, except at START/STOP. tLOW 36 (35 for `i2c-read` and `i2c-repeated-start`), tHIGH at least 36 (stretch-aware), tHD;STA 34, tSU;STA at least 37, tSU;STO at least 37, tBUF at least 72 (111 for `i2c-repeated-start`), tSU;DAT at least 34 and tHD;DAT at least 2 cycles. tVD;DAT is at most 4 cycles on fixed-latency paths; after a non-blocking `PULL` the SDA changes come 7 cycles after SCL falls in `i2c-write` and 12 in `i2c-repeated-start` (Finding 3). Every SCL low phase is at least 35 cycles, including the one before the repeated START (at least 42). At 50 MHz this meets **Fast-mode Plus**, whose minima hold up to 72 (70) MHz. Fast-mode needs a clock of at most 27.7 (26.9) MHz; Standard-mode at most 7.2 (7.1) MHz. No fault releases SCL: a NACK ends with a STOP condition whose SDA release is the `FAULT 65` pin release. Each image declares Fast-mode Plus in its notes. Before the fix: tLOW 7 in `i2c-repeated-start` (FAIL) and a 2-cycle SCL low phase on NACK in all three (WARN); see Findings 1 and 2. |
| `i2c-target-write`, `-read` | SDA is driven at most 7 to 8 cycles after SCL falls, against the declared minimum phase of 16 cycles (margin 8). The target reaches the next pin wait within 6 (read) or 4 (write) cycles, or pulls SCL low to stretch within 7 to 8 cycles. SDA is sampled 1 to 2 cycles after SCL rises. SCL is held low (stretch) at every FIFO holding point. |
| `jtag` | TMS/TDI change only while TCK is low, with at least 32 cycles of setup and hold. TCK phases are at least 32 cycles. Six TCK rising edges with TMS high reset the TAP. TDO is sampled exactly on TCK rising edges, with a 30-cycle return budget. |
| `waveform` | 16 pulses, high exactly 32 and low exactly 64 cycles (declared). `TIME` issues 1540 cycles after START, 64 after the last falling edge. |
| `event-transmitter` | The low pulse lasts exactly 32 cycles and starts 1 cycle after `WAITEVENT` completes. Pending events give at least 3 cycles high between pulses. |
| flagship scenario | Ownership is disjoint and the pin roles match. Each engine's schedule is exact. The UART-RX-to-SPI bridge is rate-safe: UART RX delivers at most one word per 640 cycles, and the SPI engine is back at PULL 520 cycles after taking a word, a margin of 120 cycles per word. The 8 SPI return words fit the 8-word RX FIFO. |

**Validation of the regenerated set (run r8).** The analysis was checked
against the reference model in the same way as below (see
[Validation](#validation)), with the committed tool (SHA-256 sums equal to
those of r7) and these Slurm jobs:

- 23984750: suites scenarios, legacy, event, mutants and margins;
- 23984751: stress, 128 tasks x 25 runs x 19 images x 30 000 edges;
- 23984752 and 23984753: stress with the six scalar images, 2 x 16 tasks x
  4 runs x 25 images (seeds 0x3131 and 0x4141, as r3 and r4);
- 23984754: random, 128 tasks x 500 cases (seed 0x5151, as r7);
- 23986537: merge and report.

Results:

- 1 193 441 of 1 193 441 engine runs consistent.
- 2.60 x 10^9 checked edges and 1.28 x 10^9 issue attempts.
- 35 303 947 of 35 303 947 pad changes on a predicted edge.
- 36 114 LIMIT timeouts and 47 132 strict-PUSH faults where predicted.
- All 7 mutants detected; the 3 margin experiments agree with the prediction.
- 64 000 random cases ran 297 112 distinct programs, analyzed on the fly; 844
  analyses were over-approximate, and no run was left unchecked.

An earlier, smaller run on the same images (job 23975500 and array 23975501,
34 636 engine runs) also passed.

Every path variant of the 19 images and of the six scalar images was observed
except one of the 28 in `i2c-read`. That variant is infeasible: it branches from the end of the STOP
sequence back to `transaction` after an address NACK. The fall-through side
of `JZ rx` at pc28 leaves rx nonzero, and nothing writes rx before the `JZ rx`
at pc50. The analyzer does not record nonzero facts, so it keeps the branch.

### Findings

1. **Fixed: `i2c-repeated-start` had a 7-cycle SCL low phase before the
   repeated START.** In the image analyzed at 73536f0 the sequence was:
   - After the register byte's ACK clock, `DIR 0x40` at pc29 pulled SCL low
     (+36 after the ACK-clock `WAITPIN`).
   - `JZ`, `ADD`, `JZ`, `LOAD`, `XOR`, `JZ` followed.
   - `start: DIR 0x00` at pc3 released SCL at +43.

   Every other SCL low phase was at least 35 cycles. This one was 140 ns at
   50 MHz and violated tLOW for every UM10204 mode above 14 MHz (Fm+) or
   5.4 MHz (Fm). There was also a protocol hazard. The target may still be
   driving its ACK low when SCL rises. The spec lets it take up to
   tVD;ACK = 0.45 us (Fm+), about 22 cycles. If it released SDA after SCL was
   already high, the bus would see a STOP before the controller's START,
   instead of a repeated START. The test peer releases SDA immediately, so
   simulation passed.

   The regenerated image starts the repeated-START path with `start: WAIT 32`
   (pc9). The SCL low phase before the repeated START is now at least
   42 cycles (0.84 us at 50 MHz, against 22.5 cycles of tVD;ACK), and
   `i2c-scl-low-phase` passes (job 23975273). On the reference model, a target
   that releases the register byte's ACK up to 42 cycles after SCL falls still
   sees a repeated START; at 43 cycles it sees a STOP and then a START (job
   23984841, a target model written for this check, not the `test_ext` peers).
2. **Fixed: on a NACK (fault 65) the I2C controllers produced a runt SCL pulse
   and no STOP.** In the 73536f0 images, `DIR 0x40` (SCL low) was followed two
   cycles later by `FAULT 65`, which released the bus. SCL was therefore low
   for 2 cycles, 40 ns at 50 MHz. That is shorter than the 50 ns spike filter
   (tSP) of Fm/Fm+ targets only above 40 MHz. It happened at pc28 in
   `i2c-read`, pc29 in `i2c-repeated-start`, and pc28/51 in `i2c-write`, and
   was reported as WARN.

   In the regenerated images a NACK leads into the STOP sequence:
   - SDA is pulled low 2 to 4 cycles after SCL falls.
   - SCL is released after at least 36 cycles low.
   - tSU;STO is at least 37 cycles.
   - `FAULT 65` then releases the owned pins. SCL is already released, so the
     fault releases only SDA, and that release is the STOP condition.

   `i2c-fault-release` is no longer reported, and the fault code is unchanged.
   In `test_ext`, verilog-i2c's `i2c_slave` sees this STOP after an address NACK
   (its `bus_active` falls) in both skew corners, at RTL and at gate level
   (docs/independent-peers.md).
3. **At 50 MHz the I2C controller images are Fast-mode Plus devices only; the
   images now declare it.** The SCL period is at least 72 cycles, about
   694 kHz; 71 cycles, 704 kHz, for `i2c-read` and `i2c-repeated-start`. tLOW
   is 35 to 36 cycles (0.70 to 0.72 us), below the Fm minimum of 1.3 us.
   Fm-only (400 kHz) targets need a clock of at most 26.9 MHz or a larger
   half-period. The 73536f0 images declared no speed mode. The regenerated
   images carry a note, "Declared I2C bus speed at the 50000000 Hz annotation:
   Fast-mode Plus (...)". The note also gives the half-period each slower mode
   needs: 62 for Fm and 247 for Sm at 50 MHz. The checker does not parse this
   note, so these checks stay INFO. The note was compared with the analyzer
   for half-periods 8, 21, 22, 61, 62, 246, 247 and 255 and for clock
   annotations of 10, 25, 66 and 100 MHz (job 23976959). It never declares a
   mode the analyzer reports as not met. For `i2c-write` at half-periods 21 and
   61 the analyzer reports one mode more than the note, whose bounds are shared
   by the three images and are one cycle short of `i2c-write`'s tLOW. The
   analyzer's `i2c-scl-low-phase` check compares each SCL low phase with the
   documented default half-period of 32, not with the image's own. It passes
   down to half-period 28 for `i2c-write` and 29 for `i2c-read` and
   `i2c-repeated-start`, and fails below (job 23984870).

   The tVD;DAT used by the analyzer, and by the note, is at most 4 cycles. It
   covers only SDA changes at a fixed distance from the SCL fall; distances
   that cross a `PULL` are excluded. Two bytes are sent after a `PULL` with SCL
   held low: the data byte of `i2c-write` and the register byte of
   `i2c-repeated-start`. On the reference model, with the TX word queued,
   their SDA changes come 7 cycles (`i2c-write`) and 9 and 12 cycles
   (`i2c-repeated-start`) after SCL falls. The SCL low phases that contain them
   are 41 and 46 cycles, and SDA is set up at least 34 cycles before SCL rises
   (job 23984714). At 50 and 66 MHz, 12 cycles are 240 and 182 ns, within the
   Fm+ maximum of 0.45 us. At 25 MHz they are 480 ns. The Fm+ maximum holds for
   the 12-cycle change from 26.7 MHz and for the 7-cycle change from 15.6 MHz
   (the Fm maximum of 0.9 us from 13.3 and 7.8 MHz). The analyzer's
   lower clock limits (Fm+ 8.9 MHz, Fm 4.4 MHz) and the note's declarations at
   low clock annotations therefore do not cover the `PULL` paths. UM10204
   Table 11 note [3] requires the tVD;DAT maximum only of a device that does
   not stretch the LOW period of SCL. A device that stretches it must have the
   data valid by the set-up time before it releases the clock, which holds on
   these paths.
4. **SPI target needs CS high for at least 9 cycles between bytes (not
   declared).** After the CS==1 `WAITPIN` at the end of a frame, the firmware
   re-checks CS==1 8 cycles later, after `DIR`, `PUSH`, `JMP`, `PULL`, `SHL`,
   `LOAD` and `COUNT`. A shorter deassertion is missed. The margin sweep below
   confirms the value of 9 exactly. The notes only say "CS must be high between
   bytes".
5. **I2C targets are faster than declared.** They work with 8-cycle SCL phases
   (predicted and confirmed by the sweep) against the declared 16. The
   declaration is safe, with a factor-2 margin.
6. **SPI modes 1/3 hold MOSI 1 cycle after the last sampling edge.** The
   post-transfer `SET` rewrites MOSI together with CS. That is 20 ns at 50 MHz,
   before pad skew. It is enough for typical parts but is the tightest edge in
   the SPI images. CS rises 1 cycle after the last SCK edge in all modes.
7. `firmware/spi-controller-fast.image.json` carries `"name":
   "spi-controller-mode0"` (INFO).
8. **Fixed: `i2c-repeated-start` held a started bus between transactions**
   (reported in docs/independent-peers.md). In the 73536f0 image the only
   `PULL` of the first byte followed the START, so the holding points were
   `pc11 PULL [pin6=0 pin7=0]` and `pc54 PUSH`. The regenerated image pulls
   each transaction's first word before the START. Its `PULL` at pc5 holds
   `[pin6=Z pin7=Z]` between transactions and `[pin6=0 pin7=Z]` within one.
   `i2c-write` and `i2c-read` already pulled before the START.
9. **Not analyzed: SDA hold after the controller's own SCL fall.** SDA changes
   at least 2 cycles after the controller pulls SCL low (tHD;DAT at least 2,
   40 ns at 50 MHz), before and after the fix. UM10204 Rev. 7.0 Table 11 note
   [2] asks that SCL drop below 0.3 VDD before SDA enters the 0.3 VDD to
   0.7 VDD range, and Fm+ allows an SCL fall time of up to 120 ns. For a
   controller that cannot observe the SCL falling edge, the note asks for an
   SDA delay based on a measured SCL fall time. These images can observe SCL
   but do not wait for it to read low before changing SDA. A trial build that
   inserts `WAITPIN SCL==0` after each SCL fall that the controller follows
   with an SDA change needs 65 words for `i2c-write`, 59 for `i2c-read` and 65
   for `i2c-repeated-start`, and the assembler rejects the 65-word images (job
   23984728). The analyzer works in clock cycles and does not model the SCL
   fall time, so whether 2 cycles are enough on a given pad and bus is open.

### Fused versus scalar issue

`firmware.md` asks to "quantify both using actual trace comparisons". The committed assembler (73536f0) was built in a private dune build dir (Slurm job
23757661). Its fused SPI images are byte-identical to the committed ones.
Scalar images were generated and analyzed; these images are not committed.

| image | SCK high / low (cycles) | CS-low frame | notes |
|---|---|---|---|
| `spi-controller-mode0` fused | 32 / 32 exactly | 514 | `XFER` |
| scalar mode0 (half-period 32) | 33 / 39-41 | 588 | the low phase depends on the next data bit (`JZ` path) |
| scalar mode1 | 33 / 38-40 | 580 | |
| scalar mode0, half-period 16 | 17 / 23-25 | 332 | |
| `jtag` scalar | TCK phases 34-36 | | |

The scalar images come from `assemble.exe --firmware spi-controller --mode M
--issue scalar [--half-period 16]` and `--firmware jtag --issue scalar`.

Scalar issue therefore adds 7 to 9 cycles of data-dependent jitter per bit and
14% to each frame. The analyzer represents it exactly: the bit loop's `JZ` is a
soft boundary, with 36 variants per SPI image. The scalar images were also
validated against a reference model configured for scalar issue (stress suite,
100% variant coverage in the local run).

## Validation

**Ground truth.** The reference model is the executable ISA specification.
`pe_validate.py` wraps `Reference.tick`, `_step` and `command` without changing
them. For every engine run (START to STOP, reset or restart) it records:

- on every edge, whether the engine attempted an issue, at which pc, and the
  outcome;
- the physical state of each owned pad, from `uio_out`/`uio_oe`.

It then replays the run against the analysis as a set of hypotheses (graph
node, variant, anchor edge). A hypothesis survives an edge only if all of these
hold:

- the attempt pc and outcome are exactly as predicted at that offset;
- every owned pad is within the predicted set;
- pads change only on predicted edges.

At a boundary, the observed stall must lie in the node's range. A LIMIT
timeout must occur exactly LIMIT-1 edges after arrival. The successor is
anchored at the observed completion edge. A run fails as soon as no hypothesis
survives.

**Workloads.**

- *scenarios*: the directed scenarios of `test/scenarios.py`, run on the model
  alone. These are the ones `test_protocols.py` and `test_flagship.py` run.
- *legacy*: all 25 monorepo workloads from `test_legacy.py`, including JTAG,
  waveform, SPI/I2C targets, triggers, strict PUSH and DMA congestion.
- *event*: `event-transmitter` under random host `EVENT`s and a pin-7 trigger.
- *stress*: every image under random host traffic, random input pins (with
  loopback and random open-drain pull-downs), random events, STOP/START and
  CLEAR.
- *random*: `test/random_gen.py` cases. These are random legal programs on all
  four engines plus host traffic, analyzed on the fly.

**Negative controls.** Seven deliberately wrong analyzers are run on the
scenario and legacy workloads. Each must fail:

- `WAIT n` costing n cycles;
- one `XFER` edge one cycle late;
- `COUNT n` giving n iterations;
- loopback latency of 4;
- open-drain modelled as push-pull;
- `HALT` keeping the pins driven;
- `SET` costing 2 cycles.

All seven are detected.

**Margin experiments.** These test the protocol conclusions, not just the
edges. Each sweeps an external timing parameter with an independent wire-level
peer against the model and compares the first passing setting with the
analyzer's synchronous prediction.

| experiment | predicted | observed |
|---|---|---|
| `uart-rx` back-to-back frames, bit period 56..72 cycles | 62..67 pass | 62..67 pass. Every one of 34 settings (back-to-back and with gaps) matches. |
| `spi-target` half period, modes 0/2, 1/3 | >= 5, >= 4 | first pass 5, 4 |
| `spi-target` CS setup, modes 0/2, 1/3 | >= 5, >= 2 | first pass 5, 1 (conservative by 1: a late-detected first edge is absorbed by slack) |
| `spi-target` CS high between bytes | >= 9 | first pass 9 |
| `i2c-target-write`/`-read` SCL phase | >= 8 | first pass 8 |
| `i2c-target-*` at the declared 16-cycle phase: two transactions, a mismatched second address, and (read) a controller that ACKs the data byte | pass, fault 66, fault 67 | as predicted |

Every setting predicted safe passed, so the predictions are sound. They are
exact except for the CPHA1 CS setup.

**Bugs this found in the analyzer.** Earlier versions had two errors, both
caught by validation and fixed before the runs below:

- A 1-cycle SCL drive before a `WAITPIN` was assumed to be visible. The margin
  runs of `i2c-target-write` showed the wait completing earlier than predicted,
  and the fix is the 3-edge look-back.
- Periodic loops with a data-dependent exit were missing their later-iteration
  exits. 27 random programs exposed it, and the fix is the soft boundaries.

The same runs also found a checker bug: a LIMIT=1 wait that times out on its
arrival edge was being rejected.

**Numbers.** Runs r2 to r7 validated the 73536f0 images; the manifest is at
`tt-work/timing/manifest.json`. After the I2C controller images were
regenerated, run r8 repeated the suites on the new set, and
`tools/timing/report/validation-summary.json` now holds r8 (see Results,
"Validation of the regenerated set"). Each run used a frozen copy of the tool,
with SHA-256 sums stored next to it.

| run | jobs | content | result |
|---|---|---|---|
| r2 | 23757447, 23757448 (128 tasks), 23757449 | Base suites, mutants, margins. Stress: 25 runs x 19 images x 30 000 edges per task. 7 random tasks x 1000 cases completed. | PASS |
| r3 | 23759925, 23759928 (16 tasks) | Base; stress including the 6 scalar images | PASS |
| r4 | 23761093, 23762121 (16 tasks), 23762123 (4 tasks completed) | Final code: base, mutants, margins (including the I2C coverage trials); stress including scalar; random | PASS |
| r7 | 23774644 (128 tasks), 23776236 | Final code: 128 x 500 random cases; base, mutants, margins (this base job replaces r4's in the totals) | PASS |
| r8 | 23984750, 23984751 (128 tasks), 23984752 and 23984753 (16 tasks each), 23984754 (128 tasks) | Regenerated I2C controller images, final code: base, mutants, margins; stress as r2; stress including scalar as r3 and r4; 128 x 500 random cases as r7. 1 193 441 of 1 193 441 engine runs consistent | PASS |

Totals of r2 to r7 (73536f0 images):

- **1 303 659 engine runs, all consistent.**
  - 2.73 x 10^9 checked edges and 1.36 x 10^9 issue attempts.
  - 38 037 698 of 38 037 698 pad changes happened on a predicted edge.
  - 14.95 M segments completed and 14.50 M boundary completions.
  - 42 377 LIMIT timeouts occurred exactly LIMIT-1 edges after arrival.
  - 57 414 strict-PUSH faults occurred where predicted.
- **Coverage.** Every path variant of every committed image and of the six
  scalar images was observed at least once (100%).
- **Random programs.** 73 000 cases ran 334 356 distinct programs, analyzed on
  the fly. For 973 of them the analysis was over-approximate (context or
  unroll budget); their runs still matched, and no run had to be left
  unchecked.
- **Stalls.** Observed stalls equalled the predicted minimum in 8.1 M of 14.5 M
  boundary completions and never fell below it.

Two infrastructure problems appeared and were fixed; neither was a timing
mismatch:

- **Out of memory.** The large random arrays (r2 random, r5, r6) ran out of
  memory. The validator cached every random program's analysis. Worse, when
  several hypotheses had identical timing, each spawned every successor
  variant, so the hypothesis set grew at every boundary. The fixes are a
  bounded cache, a global analysis budget and hypothesis de-duplication. r7
  re-ran the same seed afterwards; peak memory was 79 MB per task.
- **Superseded runs.** The completed tasks of r5 and r6 passed. They are not
  counted, because r7 covers the same cases.

## End-to-end guarantee

Three results combine.

1. **Static schedule (this tool, ISA level).** For every boundary context and
   every data-dependent branch outcome, the issue edge of every instruction and
   every possible pad change is an exact offset from the anchor. This is shown
   against the executable ISA (the reference model) on the workloads above:
   every observed edge matched.
2. **Timing isolation (formal, RTL level).** `formal/timing_isolation.sv` is a
   two-copy miter of the complete processor with the IHP SRAM models. It is
   proved unboundedly for each engine K, by `timing_isolation_prove_k0..3` with
   k-induction (PASS in Slurm job 23716631 according to `formal/README.md`; not
   re-run here). The proof has these properties:
   - **Initial state.** It starts from an arbitrary state in which K's
     execution registers, image, ownership, timestamp and synchronizers are
     equal.
   - **Inputs.** The two copies share the pad inputs.
   - **K's control commands.** START, STOP and CLEAR of K occur on the same
     cycles in both copies.
   - **Everything else is free** and differs between the copies: other
     engines, host FIFO traffic (including to K), mover grants, SIGNALs and
     trigger configuration.
   - **Conclusion.** Until K completes a `PULL`, `PUSH` or `WAITEVENT`, K's
     owned `uio_out`/`uio_oe` bits are identical in both copies on every cycle.
   - Because the initial state is arbitrary, the conclusion holds for every
     window that starts at any such completion. It does not only hold for
     windows that start at START.
3. **RTL conforms to the ISA.** The cocotb suites (`test_legacy`,
   `test_protocols`, `test_flagship`, `test_random`) run the generated RTL in
   lockstep with the same reference model. They compare `uio_out`/`uio_oe` on
   every cycle. See `test/README.md`; they were not re-run for this page.

**What these give together.** Take a firmware image as committed. The tool
checks it by hash, loads it, and checks that its ownership is as declared. Then:

- Between two consecutive boundary completions, each pad edge of K happens
  exactly on the edge the tool predicts, relative to the first completion. The
  only exception is a fault, which releases the pins.
- No other engine, host transfer, mover transfer or event to another engine can
  move that edge. The proof shows the pads cannot differ. The static schedule
  says what they are.
- The composition also works across boundaries that the proof does not treat
  as window ends:
  - `WAITPIN` depends only on the pad inputs, which the proof shares between
    the copies, so its completion edge cannot be perturbed either.
  - A `JZ` on data from `IN` selects among the enumerated variants, all exact.
  - A strict `PUSH` either completes in one cycle, and the next window starts
    from an equal state, or faults and releases the pads.

**What is not guaranteed.**

- **When boundaries complete.**
  - `PULL`/`PUSH` wait for the host or the mover, with no bound.
  - `WAITEVENT` waits for `SIGNAL`, host `EVENT` or a trigger. `WAITPIN`
    waits for a pin. Both are bounded only by LIMIT-1, then fault 3.
  - Each completion re-anchors the schedule, and the tool reports the stall
    range of every boundary. A strict `PUSH` on a full FIFO (the host did not
    drain it) ends the schedule with fault 4. The formal negative control
    `timing_isolation_neg_pull` shows exactly this coupling.
- **Input timing.**
  - Asynchronous inputs have an unknown phase within the clock period. This
    gives one cycle of jitter on each anchor derived from a pin, and the
    protocol checks add that cycle.
  - Synchronizer latency is fixed: 2 edges from sampling, 3 for the engine's
    own loopback. The checks include it.
  - Metastability MTBF of the 2-flop synchronizer is not analyzed.
- **Physical delays.** Clock-to-pad, pad-to-pad skew, board delays and I2C rise
  and fall times are not included. The reported numbers are the digital
  budgets these must fit in: for example, the 600 ns SPI MISO round trip or the
  34-cycle I2C tSU;DAT. Gate-level timing closure is a separate question
  (`docs/hardening.md`).
- **Refinement.** The link between the ISA-level schedule and the RTL is
  shown by lockstep simulation, not by proof. A formal per-instruction cycle
  refinement would close this gap. For example: an issued `WAIT n` is followed
  by the next issue exactly n+1 edges later, and an `XFER` transition occurs
  every `b` edges. Today `engine_safety` checks a WAIT countdown only as
  24-cycle BMC.
- **Assumptions of the proof.**
  - Host commands that control K (START, STOP, CLEAR, BEGIN, OWN) are the
    firmware owner's to schedule. They obviously perturb K.
  - Reset and `ena` are shared.
  - The proof covers only the design-of-record configuration (4 engines,
    32-bit, 64 words, 8-word FIFOs, fused), the same one the images target.
  - Pads that another engine drives and K reads are external inputs to the
    proof. That coupling enters K only through `WAITPIN`/`IN`, that is, at
    boundaries or as branch data, and these are covered above.
- **Scope of the analysis.** The analysis is exact for every committed image.
  For arbitrary programs it can widen contexts or cut an unrolling budget, and
  then says so. Its results stay sound but may include infeasible paths.

## Running it

```sh
# one image: schedules, spacing, loop periods, checks (exit 1 on FAIL)
python3 tools/timing/pe_timing.py analyze firmware/uart-tx.image.json
# all images + flagship -> report.md, report.json, checks.json
python3 tools/timing/pe_timing.py report --firmware firmware --out build/timing
# ground truth against the reference model
python3 tools/timing/pe_timing.py validate --repo . --out build/timing/validate.json \
    --suite scenarios --suite legacy --suite event --suite mutants --suite margins
python3 -m unittest discover -s tools/timing -p 'test_*.py'
```

`tools/timing/README.md` documents the options and the Slurm job script. Only
the Python standard library is needed. The tool gives a `report` exit status
and a `checks.json`, so it can serve as the declared-timing gate that
`design.md` differentiator 4 asks of the assembler. Wiring it into CI or the
OCaml assembler is not done here.
