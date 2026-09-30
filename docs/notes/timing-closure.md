# Slow-corner timing closure by RTL restructuring

Status, 2026-09-27: since `d76f1cc`, `src/config.json` constrains the flow
at `CLOCK_PERIOD` 15 ns (66.7 MHz; optimizer promotion p018), and the operating
clock is still 50 MHz. Section 10 describes that sign-off and the
slack of the same layout re-timed at 20 ns. The 20 ns results below remain
true for the commits they name.

Status, 2026-09-26 (updated): the slow corner of the design of record is
closed at 50 MHz, by LibreLane configuration and not by RTL. The official
build of `131e793` (GitHub run 36257636798), whose RTL is byte-identical to
`c118027`, meets setup at all three corners, with +2.95 ns at the slow corner
(section 9). The RTL options and the two named variants of sections 4 to 7
did not close it (section 7.4); that result stands, and sections 1 and 3
remain the analysis of the original failure.

Earlier status line, before `25e331e`: the options, the two named variants
and their verification are complete; the slow corner is not closed
(section 7.4).

The official hardening of `v0.1-hardened` (commit `c118027`, GitHub run
36144357821) meets setup at the typical corner, which is the Tiny Tapeout
sign-off corner, and misses it at the slow corner (`nom_slow_1p08V_125C`) by
8.52 ns at 20 ns. This document explains why, describes RTL options that
restructure the failing logic without changing behaviour, and reports how they
were verified and what they do in LibreLane. Section 9 compares that build
with the configuration-based closure.

Nothing here changes the submission. `src/protocol_emulator_core.v` is still
generated from `configs/instruction-sram-32.json`, and the new options emit
nothing unless a config sets them.

## 1. The failing build

This is the build of `c118027`, the design of record until `25e331e`.

| Corner | Setup WS | Setup violations | Reg-to-reg WS | Hold WS |
|---|---:|---:|---:|---:|
| `nom_typ_1p20V_25C` | +0.89 ns | 0 | +13.64 ns | +0.30 ns |
| `nom_fast_1p32V_m40C` | +6.15 ns | 0 | +17.21 ns | +0.11 ns |
| `nom_slow_1p08V_125C` | −8.52 ns | 2,482 | −0.12 ns (1) | +0.63 ns |

Source: `stats/metrics.csv` of the run's `tt_submission` artifact. The flow
checks setup only at the typical corner (`TIMING_VIOLATION_CORNERS ["*typ*"]`).

Two properties of the flow matter for everything below:

- **The resizer does not see the routed slow corner.** Timing repair runs once,
  after CTS, on placement-estimated parasitics
  (`OpenROAD.ResizerTimingPostCTS`). In the sweep's `rstreg` run it reported
  "No setup violations found", and the mid-flow STA reported +7.53 ns at the
  typical corner where the routed result was +1.47 ns
  (`36-openroad-stamidpnr-1` against `55-openroad-stapostpnr` of sweep run
  23751798_0). `RUN_POST_GRT_RESIZER_TIMING` is false, so nothing repairs the
  routed netlist (section 7.3 tries it).
- **Most cells on the failing paths are the smallest drive strength.** Long
  nets are driven by `nand2_1`, `o21ai_1` and `buf_1` cells, with 1 to 5 ns
  transitions at the slow corner.

So, with the flow settings of `c118027`, the slow-corner slack was set by the
RTL structure: logic depth, the fanout of late signals, and flop-to-flop
paths that attract hold buffers. Other flow settings move it on the same RTL:
section 9 closes the slow corner by configuration alone (slow-corner WS from
−8.52 to +2.95 ns, a gain of 11.47 ns).

## 2. Method

- **STA.** OpenSTA 2.7.0 from the LibreLane 3.1.0.dev3 image, reading the
  artifact's final netlist and `nom` SPEF with the slow-corner standard-cell,
  I/O and SRAM liberty files and LibreLane's `base.sdc` with the Tiny Tapeout
  values (20 ns, 20 % I/O delay, 0.25 ns uncertainty, 5 % derate). It
  reproduces the artifact's numbers exactly: WNS −8.5171 ns, 2,482 violating
  endpoints, typical WNS +0.8854 ns.
- **Reports.** For the whole design and separately for paths starting at
  `rst_n`/`ena`, at `ui_in`/`uio_in`, at registers, at the SRAM macros, and
  ending at outputs: every violating endpoint with its worst start point, and
  the worst path of each of the 500 worst endpoints with every stage.
- **Classification.** Flop instances are mapped to RTL registers through the
  Q net names of the netlist. Hardcaml's anonymous registers (queue pointers
  and storage, host registers, pin synchronizers) were named in a scratch copy
  of the generator; the generated file differs from the production file only by
  a one-to-one renaming of identifiers, which gives the name map. Each stage of
  a path is classified as input delay, launch (flop or SRAM clock-to-output),
  hold buffer (`dlygate`), repair buffer (`fanout*`, `wire*`, `max_cap*`,
  `load_slew*`), logic, or wire.

The scripts and all reports are in the workstream directory (section 8).

## 3. Why the slow corner fails

### 3.1 The design of record at `c118027` (official build)

Violating endpoints at the slow corner, grouped by the start point of each
endpoint's worst path:

| Start | End | Endpoints | Worst slack |
|---|---|---:|---:|
| `rst_n` | queue storage: all 1,024 TX bits, 471 RX bits | 1,495 | −8.52 ns |
| `rst_n` | engine registers (377 in `tx`/`rx`/`x`/`y`, 186 timers, 116 completed counter, 58 PC, 61 other) | 798 | −6.63 ns |
| `rst_n` | engine control (image, routes, mailboxes) | 68 | −4.97 ns |
| `rst_n` | queue pointers and counts | 65 | −8.04 ns |
| `rst_n` | other registers, outputs, SRAM pins | 55 | −5.29 ns |
| SRAM | engine register | 1 | −0.12 ns |
| total | | 2,482 | −8.52 ns |

The reset input hides everything else, because each endpoint is counted once.
Restricting the start points shows the other failing structures:

| Paths starting at | Violating endpoints | WNS (slow) | WNS (typ) |
|---|---:|---:|---:|
| `rst_n`, `ena` | 2,481 | −8.52 ns | +0.89 ns |
| `ui_in`, `uio_in` | 1,076 | −3.75 ns | +3.77 ns |
| registers and SRAM | 1,219 | −4.69 ns | +4.68 ns |
| SRAM macros only | 20 | −1.65 ns | +6.59 ns |
| to outputs | 14 | −1.69 ns | +3.50 ns |

Worst path of each group, stage by stage (slow corner, ns):

| Path | Arrival | Input delay / clock | Launch | Hold buffers | Repair buffers | Logic (levels) | Wire |
|---|---:|---:|---:|---:|---:|---:|---:|
| `rst_n` → TX queue 3 storage | 29.43 | 4.00 | — | 0 | 9.64 | 15.11 (22) | 0.58 |
| `ui_in[3]` → TX queue 3 storage | 24.66 | 4.00 | — | 0 | 4.99 | 14.82 (23) | 0.58 |
| host write buffer → TX queue 3 storage | 25.71 | 1.62 | 0.41 | 1.96 | 7.21 | 14.04 (21) | 0.47 |
| SRAM → engine 3 completed counter | 22.63 | 1.40 | 5.31 | 1.24 | 2.81 | 11.45 (25) | 0.41 |
| `rst_n` → `uio_oe[5]` | 17.44 | 4.00 | — | 0 | 5.05 | 7.86 (5) | 0.43 |

What the failing paths have in common:

1. **One cycle from a host nibble to a queue write.** The last nibble of a
   host word (`ui_in`) and the buffered part of the word (host write buffer)
   feed the command decoder, whose result feeds the mover's eligibility
   (ROUTE edits, FLUSH, a host push to the same TX queue), the rotating
   arbiter, the TX push, the queue's `put`, and finally the write enable of
   every storage word: 8 words × 32 bits per queue. The reset net enters the
   same chain through the host's write-ready term, the eligibility and `put`.
2. **The reset input is a pin with a 4 ns input delay and ~3,900 loads.** In
   the design of record `~(rst_n & ena)` is the synchronous clear of every
   register and gates engines, queues, host strobes and pin outputs
   combinationally.
3. **A 256-way multiplexer in each decoder.** The host command validity and
   the engine's instruction validity are 256-way multiplexers on the 8-bit
   code; the engine's completed-instruction counter is assigned in almost every
   branch of the instruction switch.
4. **Hold buffers on setup-critical nets.** The host write buffer shifts by
   one nibble per accepted nibble, a direct flop-to-flop path. Hold repair
   inserts two delay cells (1.96 ns at the slow corner) on the buffer's
   outputs, which also feed the command decoder.

### 3.2 The registered-reset variants

The earlier sweep hardened two reset variants at 8x4, 20 ns (before the
`FP_MACRO_HORIZONTAL_HALO` change; `docs/notes/sweep.md`). Re-analysed the same way:

| Variant (sweep job) | Slow WS | From `ui_in` | From registers | From SRAM | From `reset_sync_2` |
|---|---:|---:|---:|---:|---:|
| `rstreg` (23751798_0) | −7.52 ns | −7.52 ns (1,275) | −6.07 ns (1,173) | −2.03 ns (68) | −5.49 ns (1,093) |
| `cn_s2` (23751802_0) | −4.99 ns | −4.54 ns (1,524) | −4.99 ns (1,792) | −4.13 ns (76) | −3.34 ns (863) |

Registering the reset removes the `rst_n` input delay but not the structures
of section 3.1: the worst paths still run from the host nibble or write buffer
through the command decoder and the mover into queue storage, and from the SRAM
through the instruction decoder into the completed counter. The registered
clear (`reset_sync_2`, one flop) reaches the same chains through the host
write-ready term and the engines' `active` term: −5.49 ns in `rstreg`, and
−3.34 ns in `cn_s2`, where its recovery checks at every asynchronous reset pin
pass with +13.03 ns. The reset columns count endpoints whose paths from
`reset_sync_2` fail; the other columns count endpoints per start group, so an
endpoint can appear in several columns.

## 4. The timing options

Eight options, all in the refinement config's `"options"` object next to the
variant knobs of `docs/notes/variants.md` (`hardcaml/lib/timing_options.ml`). Each
changes how the logic is built, never what the pins do. With all of them false
(the default), every generator path is the one that existed before:
`scripts/generate.sh` reproduces `src/protocol_emulator_core.v` and the six
published variants byte for byte, and `generate_refinement_formal` reproduces
their formal debug circuits byte for byte.

| Option | Where | Change | Structure removed |
|---|---|---|---|
| `host_nibble_slots` | `host.ml` | Nibble *i* of a host word (*i* = 0..6) is loaded in place into a 4-bit slot; the eighth nibble is used live from `ui_in`. The shifting 32-bit buffer is gone. | The flop-to-flop shift path and the hold buffers it attracts on the decoder inputs. |
| `split_command_decode` | `processor.ml`, `host.ml` | `command c` = strobe AND (code = c) AND rule(c) for c < 12, instead of `accepted AND (code = c)` behind a 256-way validity multiplexer. The strobes of windows 0, 1 and 2 are the raw last-nibble strobe AND the window's own write-ready term. The mover grant is a flat rotating-priority function. | Decoder depth between the last nibble and every command effect, including the mover's interlocks. |
| `split_engine_issue` | `engine.ml` | The engine's next-state logic is enabled by `running & fault = 0 & ~clear`; START and STOP gate only the issue-derived outputs that act outside the engine (queue pop and push, event consume and signal, stalled). | START/STOP (host commands) at the root of every engine next-state function. |
| `split_instruction_decode` | `engine_decode.ml` (validity), `engine.ml` (counter); until `16cfc20` both in `engine.ml` | Instruction validity is an OR of per-opcode terms; the completed counter has its own increment enable (and its own `compile`) instead of being assigned in every completing branch. | The 256-way multiplexer between the SRAM output and the counter. |
| `keep_counter_increments` | `engine.ml`, `processor.ml` | The incremented or decremented value of each wide counter with a late enable (PC, completed, blocked, repeat and wait counters; image load count; route count) carries a Verilog `keep` attribute. | The enable merged into the carry chain by ABC's area mapping (below). |
| `fifo_write_staging` | `fifo.ml` (`Fifo.create_staged`) | A queue registers the incoming word, the slot and the accepted push every cycle; the staged word is written to its slot at the next edge, and a read of that slot returns the staged word. | The late push and data fanning out to every storage word (256 loads per queue). |
| `fifo_write_free_slot` | `fifo.ml` (`Fifo.create_free_slot`) | Alternative to staging without extra registers: the word at the write pointer is written with the incoming data in every cycle in which the queue is not full. | The late push in the storage write enable (the data stays late). |
| `clear_outputs_only` | `processor.ml`, `engine.ml`, `host.ml`, `fifo.ml` | The chip clear gates only what is visible or not reset: the pins, the host's ready/valid bits, the SRAM enable and write. It no longer gates next-state logic (engine enables and issue, host strobe, mover eligibility, pin triggers, queue acceptance with staging). | The registered clear's buffer tree, which has about 3,900 register loads, in front of that logic. |

**The carry-chain effect.** For `q <= en ? q + 1 : q` with a flop that has no
enable pin (the case in SG13CMOS5L), ABC's area mapping shares `en` into the
carry prefix: bit *i* becomes `q[i] ^ (en & q[0] & ... & q[i-1])` built as one
chain that starts with `en`. A small Yosys experiment (32-bit counter,
`dfflegalize` to plain flops, `abc -g AND,NAND,OR,NOR,XOR,XNOR,MUX`) measured
32 gate levels from `en` to the flops, and 1 level with `(* keep *)` on the
`q + 1` wire. In the routed `cn_s2_all` run (section 7.1, job 23995073) the
SRAM-to-counter path ends in ten AND cells (4.6 ns at the slow corner) after
the decoder.

### 4.1 Why each option preserves behaviour

- `host_nibble_slots`: both buffers hold the seven earlier nibbles of the
  current word in arrival order when the eighth is accepted, so the completed
  word is equal. Every consumer of the word (command decoder, program write,
  TX push, fault flag) is gated by the host write strobe, or with
  `split_command_decode` by the last-nibble strobe. Only the partial word
  differs, which reaches the debug exports `dbg_command_code` and
  `dbg_command_payload` outside accepted cycles.
- `split_command_decode`: codes 12 to 255 have the constant rule `gnd`, so the
  256-way multiplexer equals the OR over c < 12 of (code = c) AND rule(c).
  Window 0's write-ready term is constant 1, and the host's write strobe is
  `write_ready & last_nibble_strobe`. The flat arbiter grants the first
  eligible source in the rotation that starts at the pointer, which is what the
  priority chain selects; its index encoding is 0 when nothing is granted, the
  chain's default.
- `split_engine_issue`: the next-state logic sits in the else branches of
  STOP and START, where both are low, so `running & fault = 0 & ~clear` equals
  the old `active` there.
- `split_instruction_decode`: same argument as for the command decoder; the
  counter's enable is the disjunction of the branch conditions under which the
  switch executes `finish` or JMP.
- `keep_counter_increments`: an attribute; the logic is unchanged.
- `fifo_write_staging`: at most one word is staged, and it is the newest
  entry; a read of its slot bypasses to it, and every other valid slot was
  written at least one edge earlier. Ready, valid, level and head are those of
  the unstaged queue at every cycle; only storage words that are not
  observable change one edge later.
- `fifo_write_free_slot`: the slot at the write pointer is outside the valid
  entries whenever the queue is not full, and a push writes the same word the
  unmodified queue writes. Only unobservable storage (and the head of an empty
  queue) differs.
- `clear_outputs_only`: every register takes the chip clear as its synchronous
  clear or asynchronous reset, so its next state while the clear is asserted
  does not depend on the gated logic. The pins, the host's ready/valid bits
  and the SRAM keep the gating, so nothing visible and no state without a
  reset changes. One exception is intended: with `fifo_write_staging` and a
  synchronous reset style, a queue's staging data register (no reset, loaded
  every cycle) can take a different value while the clear is asserted. It is
  read only while the staging valid flag is set, and that flag is cleared by
  the clear, so the difference is not observable (section 6.1). In the debug
  build (formal harnesses) the exports of the now ungated activity (starts,
  stops, pushes, pops, grants, accepted command, ...) are ANDed with `~clear`,
  so they report what takes effect, as before.

Section 6 lists the checks that back these arguments.

### 4.2 Tried and not adopted

**A replicated reset tree.** A reset distributed through several identical
registered copies (one per engine or block) cannot be expressed in the RTL for
this flow. LibreLane's synthesis script runs `opt_merge`, which merges
flip-flops that have the same clock and the same D input, with or without a
`keep` attribute on the register. Checked with the Yosys 0.66 in the LibreLane
image (`read_verilog; proc; opt_merge -nomux`: two `(* keep *)` registers with
the same D and clock become one `$dff`).

**A second, structurally distinct clear flop.** A flop loaded with
`~reset_sync_1` equals the chip clear after every edge and is not merged,
because its D differs from `reset_sync_2`'s. It was implemented to drive the
combinational uses of the clear (it passed lockstep simulation and the cocotb
suite), but `timing_isolation` fails on it in 7 s: the harness starts the two
copies from an arbitrary state in which only `reset_sync_1`/`reset_sync_2` are
shared, so the new flop can differ between them and the owned pins differ at
step 1 (formal job 23996018). `clear_outputs_only` removes the same buffer tree
from the critical logic without adding state, and replaced it.

## 5. Variants

Two named variants in `configs/variants/`, one per registered reset style. The
timing options themselves change no pin behaviour, so each variant has exactly
the contract of the variant it is built on (`docs/notes/variants.md` section 4).

| Variant | Built on | Variant options | Timing options | Contract |
|---|---|---|---|---|
| `rstreg_timing` | `rstreg` | `reset: sync_registered`, `narrow_image_regs` | `host_nibble_slots`, `split_command_decode`, `split_engine_issue`, `split_instruction_decode`, `keep_counter_increments`, `fifo_write_staging`, `clear_outputs_only` | As `rstreg`: reset assertion and release take effect two edges later than in the design of record. `narrow_image_regs` only narrows the debug export `dbg_image_length`. |
| `cn_s2_timing` | `cn_s2` | `reset: async_sync_release`, `fifo_storage_reset`, `narrow_image_regs` | the same seven | As `cn_s2`: asynchronous assertion, release two edges after `rst_n` and `ena` are high. |

Neither is the submission. Both keep module `protocol_emulator_core` and the
eight SRAM instances, so each can replace `src/protocol_emulator_core.v` behind
the unchanged `src/project.v`. Generate them with
`scripts/generate.sh --variant rstreg_timing` (or `cn_s2_timing`); the cocotb
suite runs them with `PE_VARIANT=<name>` (`test/variants.py` lists both in
`SPEC`), and `formal/run.sh --variant <name>` runs the formal job list.

Not in either variant:
- `fifo_write_free_slot`: it adds no registers (staging in its place adds
  21K to 24K µm², section 6.4), but the incoming data still reaches
  every storage word's input in the same cycle; staging removes both the push
  and the data from that path. The fast runs of section 7 do not rank the two
  (one free-slot run is the best `cn_s2` result, the other the worst `rstreg`
  result).
- the design-of-record reset (`sync`): the `rst_n` input delay stays on the
  reset net, so the options cannot remove the section 3.1 reset paths.

## 6. Verification

The options are checked in three independent ways: formal equivalence of each
option against the same design without it (6.1), the repository's own
verification on the two named variants (6.2), and random lockstep simulation
of the named variants against the design of record (6.3). Section 6.4 gives
the synthesis area.

Abbreviations in the tables: `slots` `host_nibble_slots`, `cmd`
`split_command_decode`, `eng` `split_engine_issue`, `dec`
`split_instruction_decode`, `keep` `keep_counter_increments`, `staging`
`fifo_write_staging`, `free` `fifo_write_free_slot`, `co`
`clear_outputs_only`, `narrow` `narrow_image_regs`.

### 6.1 Formal equivalence

Each check compares a design with some options (gate) against the same design
without them (gold).

**Register-for-register equivalence (Yosys `equiv_*`)** for `cmd`, `eng`,
`dec`, `keep` and `co`. They add no registers and leave the next state of
every register unchanged (under `co` because the chip clear overrides the
ungated logic), so every register of the gate design must equal its
counterpart in the gold design.

1. Both designs are generated from a verification-only copy of `hardcaml/`
   whose anonymous registers (queue pointers and storage, host registers, pin
   synchronizers) and engine registers get fixed names (`fv_e<k>_pc`, ...;
   the production name mangler numbers `pc`, `pc_0`, ... in an order that
   depends on the whole design, so production names do not identify the
   engine). A token-by-token comparison checks that each named file differs
   from the production file only by a one-to-one renaming of identifiers.
2. Remaining anonymous nets get a per-design prefix, so only named signals
   (registers, ports, SRAM instances) are paired. Queue memories become one
   named register per word.
3. `equiv_make; equiv_struct; equiv_simple -short; equiv_induct` must prove
   every `$equiv` cell (`equiv_status -assert`). The eight SRAM macros are
   black boxes; `equiv_make` merges the pairs of instances and proves their
   inputs equal.
4. In the asynchronous reset styles the reset structure is identical in gold
   and gate, and every `always @(posedge clk or posedge r)` is rewritten to
   `always @(posedge clk)` in both before the proof (Yosys `async2sync` failed
   to prove a design against itself, so it is not used).

| Gold | Gate adds | `$equiv` cells | Result | Job |
|---|---|---:|---|---|
| `rstreg` + `narrow` | `cmd`, `eng`, `dec`, `keep`, `co` | 4,145 | all proven | 23999418 |
| `cn_s2` | `cmd`, `eng`, `dec`, `keep`, `co` | 3,889 | all proven | 23999419 |
| `cn_s2` + `staging` | `cmd`, `eng`, `dec`, `keep`, `co` | 4,177 | all proven | 24001146 |
| `rstreg` + `narrow` + `staging` | `cmd`, `eng`, `dec`, `keep` | 4,177 | all proven | 24001381 |
| `rstreg` + `narrow` + `staging` | `cmd`, `eng`, `dec`, `keep`, `co` | 4,177 | 128 unproven: every bit of the four TX queues' staging data registers | 24001144 |
| the same pair, TX staging data registers not paired | | 4,049 | all proven (1,152 of them by `equiv_induct -seq 3`) | 24002062 |

The last two rows are the exception described in section 4.1: in the
synchronous style with `staging`, `co` lets a TX queue's staging data register
(no reset, loaded every cycle) take a different value while the clear is
asserted. The last row leaves those four registers out of the pairing
(`equiv_make -blacklist`), so gold and gate each keep their own copy, and
proves every paired register, SRAM input and output equal whatever the two
copies hold.

Controls:

| Gold | Gate | Result | Job |
|---|---|---|---|
| `rstreg` + `narrow` + `staging` + `cmd`, `eng`, `dec`, `keep`, `co` | itself | 4,177 of 4,177 proven | 24001147 |
| `cn_s2` | itself | 3,889 of 3,889 proven | 23997217 |
| `base` | `rstreg` (reset two edges later) | 54 unproven | 23995094 |
| `cn` | `cn_s2` (different reset release) | 51 unproven | 23997219 |
| `rstreg` + `narrow` | + `staging` (storage written one edge later) | 2,208 unproven | 24001149 |
| as in the unpaired-register row | gate with bit 0 of TX queue 0's staging data inverted | 9 unproven | 24002063 |

**Observational equivalence (SymbiYosys, `abc pdr`)** for `slots` and
`staging`, which change the state encoding, for `staging` together with `co`,
and for `free`. Each miter drives both blocks with the same free inputs
(except where the table says otherwise), starts from a reset and proves the
properties for all time. Asynchronous forms use
SymbiYosys's `async2sync` model.

| Block | Gold / gate | Forms | Properties | Result | Job |
|---|---|---|---|---|---|
| host | shifting buffer / slots, both with `cmd`'s last-nibble strobe; strobe gate = clear (without `co`) and = 0 (with `co`) | sync, async | equal write strobe, last-nibble strobe, window, read strobe, read lock and host pin outputs; equal word whenever the last-nibble strobe is high | 4 of 4 pass | 24002178 |
| host | design-of-record host / slots with `cmd`, strobe gate = clear | sync, async | equal write strobe, window, read strobe, read lock and host pin outputs; equal word whenever the write strobe is high; `write = write_ready & last_nibble_strobe` | 2 of 2 pass | 24001181 |
| queue | `Fifo.create_with` / `Fifo.create_staged` | memory depth 8 and 4, memory with asynchronous reset, registers with synchronous clear, registers with asynchronous reset depth 8, 4 and 2; data width 2 and 32 | equal ready, valid and level every cycle; equal head whenever valid | 14 of 14 pass | 24001152, 24001154 |
| queue | `Fifo.create_staged` / staged queue whose acceptance is not gated by the chip clear (as with `co`) | memory depth 8 (width 2 and 32) and 4, registers with synchronous clear | push and data free in each copy while the chip clear is asserted and equal otherwise; equal ready, valid and level; equal head whenever valid | 4 of 4 pass | 24001379 |
| queue | `Fifo.create_with` / `Fifo.create_free_slot` | as for staging | as for staging | 14 of 14 pass | 24002217, 24002218 |

Negative controls: the host miters with equal words asserted also outside
the strobe (partial words differ; both reset forms in 24002178, one in
24001181), the queue miters with equal heads asserted also while empty (depth
8, memory, widths 2 and 32), and the clear-gating queue miter with the head
asserted equal to the current input. Each fails: the
`abc pdr` engine returns FAIL with a counterexample. SymbiYosys then reports
ERROR rather than FAIL for some of them, because converting the trace fails
on a renamed signal; the engine result is the one used.

**How the results compose.** For each named variant (each arrow is a row
above):

- `rstreg_timing`: `rstreg` + `narrow` → + `staging` (queue miter, memory,
  synchronous) → + `cmd`, `eng`, `dec`, `keep` (24001381) → + `co` (24002062;
  the queue-level view in 24001379) → + `slots` (host miter, strobe gate 0,
  synchronous).
- `cn_s2_timing`: `cn_s2` → + `staging` (queue miter, registers with
  asynchronous reset) → + `cmd`, `eng`, `dec`, `keep`, `co` (24001146) →
  + `slots` (host miter, strobe gate 0, asynchronous).

The block-level steps rely on the processor using the host word only when the
last-nibble strobe is high and a queue head only when the queue's valid is
high. That was checked by reading `processor.ml`, not by a tool. The
whole-core simulations of 6.2 and 6.3 test the composed designs directly.
`narrow` is an existing variant knob (`docs/notes/variants.md`).

### 6.2 Repository verification of the named variants

All on one snapshot of the working tree whose `hardcaml/` sources are the
ones in this change. Tools: cocotb 2.0.1 and Icarus Verilog 13 as in CI, OSS
CAD Suite 2026-07-29 (Yosys 0.67) for formal as in `formal/README.md`. The
equivalence checks of 6.1 and the miters use the same OSS CAD Suite.

| Check | Scope | Result | Job |
|---|---|---|---|
| Generation identity | the core generated from `configs/instruction-sram-32.json` against `src/protocol_emulator_core.v`; the six variant cores against the published files; the formal debug circuits (processor and engine) of the design of record and of the six variants against those of the generator at `HEAD` | all byte-identical | 24001126, 24002116, 24009247 |
| cocotb, full suite | `PE_VARIANT=<name>`, as the CI job | `base` 102 of 102, `rstreg_timing` 102 of 102, `cn_s2_timing` 102 of 102 | 24001127 |
| cocotb, top-up flagship | `test_flagship`, `PE_FLAGSHIP=topup` | 1 of 1 per variant | 24001127 |
| cocotb, random | `test_random`, 16 seeds (`PE_SEED=0xa11ce001` to `0xa11ce010`) × 256 cases, about 1.0 M cycles per seed | 16 of 16 per variant | 24001127 |
| Formal job list | `formal/run.sh --variant <name>`: reset safety, queue conservation, engine safety, processor invariants and inductive proofs, timing isolation (prove k0 to k3, BMC, cover) and its two negative controls | 16 of 16 expectations met for the default design, `rstreg_timing` and `cn_s2_timing` (negative controls fail as expected) | 24001130 |
| Engine and queue blocks with the options | `engine_safety` and `fifo_conservation` with a scratch copy of `formal/gen/variant/generate_blocks.ml` that also passes the timing options (the repository file passes only the variant options, so in the job list above these two jobs check the blocks without `staging`, `eng`, `dec`, `keep`) | pass for both variants | 24001190, 24001191 |
| Hardcaml tests | `dune test` in `hardcaml/` | exit 0 | 24001189, 24009247 |
| Lint | `scripts/lint.sh` behind `src/project.v` | 0 errors; 70 (`rstreg_timing`) and 78 (`cn_s2_timing`) warnings against 73 for `base`, classes `COMBDLY`, `DECLFILENAME`, `UNUSEDSIGNAL` | 24001128 |
| Gate level | cocotb with `GATES=yes` on netlists from the TT synthesis replica | 46 passed, 0 failed, 56 skipped (not marked for gate level) per variant | 24001193, 24001194 |

### 6.3 Lockstep simulation against the design of record

`hardcaml/test/variant_test.ml` runs a variant in Cyclesim next to the design
of record and compares every output of the debug circuit (pins and debug
exports) before and after every edge, through the variant's documented
reset-timing transformation. A scratch copy of the harness builds the device
under test with the timing options. The one difference it allows: with
`slots`, `dbg_command_code` and `dbg_command_payload` are compared only in
cycles with an accepted command, since they show the partial word otherwise.

| Variant | Seeds × host operations | Cycles | Comparisons | Result | Job |
|---|---|---:|---:|---|---|
| `rstreg_timing` | 32 × 3,000 | 8,366,384 | 16,728,772 | 32 of 32 pass | 24001250 |
| `cn_s2_timing` | 32 × 3,000 | 8,347,047 | 16,690,116 | 32 of 32 pass | 24001250 |

Coverage summed over the 32 runs of `rstreg_timing`: 1,855,615 engine-running
cycles, 13,184 mover grants, 288,925 accepted commands, 3,546 reset cycles.

### 6.4 Area (TT synthesis replica)

Same flows and model as `docs/notes/variants.md` section 5 (typical-corner liberty,
SRAM macros as black boxes; U and D predicted with the area study's model,
not placed). The same job re-synthesized `base` and got that section's
wrapper figure, 462,710.3 µm².

| Variant | TT synth, core (µm²) | TT synth, wrapper | Plain | Flops | Ties | U 8x4 | D 8x4 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `base` | 462,171.9 | 462,710.3 | 442,554.7 | 3,921 | 3,929 | 58.4% | 53.7% |
| `rstreg` | 464,803.8 | 462,674.6 | 443,925.2 | 3,923 | 3,931 | 58.6% | 53.9% |
| `rstreg_timing` | 483,987.8 | 482,086.6 | 471,888.7 | 4,139 | 4,147 | 60.7% | 56.3% |
| `cn_s2` | 412,416.2 | 413,522.4 | 400,461.0 | 3,827 | 9 | 53.7% | 48.5% |
| `cn_s2_timing` | 438,449.6 | 438,395.5 | 423,311.9 | 4,115 | 9 | 56.5% | 51.6% |

Job 24001192 for the two timing variants; the other rows are from
`docs/notes/variants.md`. The added flops are the staging registers, 8 queues ×
(32 data + 3 slot + 1 valid) = 288, less 72 for `narrow` in `rstreg_timing`
(`cn_s2` already has it). Wrapper areas of the exploratory builds of section
7 (jobs 23993545, 23996560, 23999002) separate the options: replacing `free`
by `staging` in otherwise equal builds adds 21.3K µm² (`cn_s2_kf` →
`cn_s2_k`) and 24.1K µm² (`rstreg_kf` → `rstreg_k`), while `slots`, `cmd`,
`eng`, `dec`, `keep` and `free` together add 1.9K µm² (`cn_s2` → `cn_s2_kf`)
and 4.0K µm² (`rstreg` → `rstreg_kf`), inside the area study's noise band of
about 5K µm².

## 7. LibreLane results

Every run uses the sweep harness (`scripts/sweep/`, `docs/notes/sweep.md`) with the
`src/config.json` of `c118027` (the runs predate `25e331e`): 8x4 tiles,
floorplan `fp8_base`, density 60, 20 ns, the LibreLane 3.1.0.dev3 image. Slack and violation counts are LibreLane's
post-route STA (`nom` parasitics); "violations" are setup-violating endpoints.
Runs of one core are not always reproducible (section 7.2): LibreLane's
synthesis step can emit the same design with a different net order (same
area and cell counts; the order is visible in the `insbuf` lines of the
synthesis log), and global placement diverges from there. Five runs of one
core spread over about 1 ns at the slow corner.

### 7.1 Fast runs of option sets

Fast mode (Magic DRC, SPICE extraction and LVS skipped), `OPENROAD_THREADS`
32. Each core was generated from the options listed on top of the base
variant. `rstreg_kc` and `cn_s2_kc` are the cores of `rstreg_timing` and
`cn_s2_timing` (same generated body).

| Run | Options over the base variant | Job | Typ WS | Slow WS | Slow violations | Slow reg-to-reg WS | Route DRC / antenna | Utilization |
|---|---|---|---:|---:|---:|---:|---|---:|
| `rstreg` | none (published core) | 23993033 | +1.42 | −7.33 | 1,379 | −1.75 | 0 / 0 | 58.6% |
| `rstreg_slots` | `slots` | 23993036 | +3.04 | −4.80 | 1,428 | −2.14 | 0 / 0 | 58.6% |
| `rstreg_cmd` | `cmd` | 23993037 | +4.25 | −5.26 | 1,120 | −5.26 | 0 / 0 | 58.6% |
| `rstreg_eng` | `eng` | 23993038 | +2.31 | −6.15 | 1,164 | +0.11 | 0 / 0 | 58.5% |
| `rstreg_keep` | `keep` | 23997331 | +2.31 | −6.92 | 1,648 | −6.92 | 0 / 0 | 58.9% |
| `rstreg_all` | `slots`, `cmd`, `eng`, `dec`, `staging` | 23995072 | +2.08 | −6.66 | 289 | −2.64 | 0 / 0 | 61.5% |
| `rstreg_kf` | `slots`, `cmd`, `eng`, `dec`, `free`, `keep` | 23997895 | −0.21 (1) | −9.99 | 1,453 | −1.90 | 6 / 0 | 58.7% |
| `rstreg_narrow_k` | `narrow`, `slots`, `cmd`, `eng`, `dec`, `staging`, `keep` | 23997330 | +5.21 | −1.76 | 62 | −1.76 | 0 / 0 | 60.9% |
| `rstreg_kc` | the same + `co` (= `rstreg_timing`) | 23998891 | +4.58 | −3.93 | 423 | −3.93 | 20 / 0 | 61.0% |
| `cn_s2` | none (published core) | 23993034 | +2.40 | −6.64 | 1,202 | −6.64 | 0 / 0 | 54.5% |
| `cn_s2_all` | `slots`, `cmd`, `eng`, `dec`, `staging` | 23995073 | +3.46 | −5.75 | 385 | −5.75 | 0 / 0 | 57.2% |
| `cn_s2_k` | the same + `keep` | 23997329 | +3.26 | −6.36 | 857 | −6.36 | 0 / 1 | 57.5% |
| `cn_s2_kf` | `slots`, `cmd`, `eng`, `dec`, `free`, `keep` | 23997896 | +4.64 | −2.12 | 237 | +0.33 | 0 / 1 | 54.7% |
| `cn_s2_kc` | `slots`, `cmd`, `eng`, `dec`, `staging`, `keep`, `co` (= `cn_s2_timing`) | 23998892 | +1.73 | −7.27 | 1,113 | +7.21 | 0 / 0 | 57.3% |

`rstreg_kf` also has hold violations at the fast corner (worst −0.09 ns, 2
endpoints); every other run meets hold at all corners. Runs not listed: two
runs of an earlier state of the options (23993031, 23993032), three runs with
the second clear flop of section 4.2 (23995188, 23995815, 23995816), and four
runs cancelled before routing finished to stay within the CPU share
(23997328, 23998893, 24000245, 24000246). A repeat of `rstreg_narrow_k`
(24004771) was still in detailed routing, with 4 violations left after 57
iterations, when this was written.

What the table shows:

- **No run closes the slow corner.** Every run but `rstreg_kf` meets the
  typical corner, the sign-off corner.
- **The reset and decoder paths are nearly gone.** In `rstreg_narrow_k`, the best
  run, 62 endpoints fail: 60 on paths from `ui_in` into engine counters,
  timers, PC and registers (worst −1.48 ns), one from an SRAM macro into an
  engine register (−1.76 ns) and one from `reset_sync_2` (−0.13 ns) (path
  analysis job 23998563).
- **What remains is the host input.** In `cn_s2_kc` all 1,113 violating
  endpoints start at `ui_in` (worst −7.27 ns, `ui_in[7]`); paths from the
  SRAM macros reach −1.75 ns (65 endpoints) and the registered clear passes
  with +0.81 ns (path analysis job 24002156). The failing structure is the
  same as in `rstreg_narrow_k`: the host input bits (window, strobe and the
  live high nibble of the command code) pass the host's window-change
  comparison and the command decoder, and START/STOP of each engine select
  the next state of all of its registers (334 flops per engine). The logic
  depth of these paths is similar (13 levels on average against 12); the
  delay of stages that drive 0.1 pF or more is not: on average 15.8 ns of a
  27.3 ns arrival in `cn_s2_kc` and 8.0 ns of 21.0 ns in `rstreg_narrow_k`.
  On the worst `rstreg_narrow_k` path, a `nand4_1` and a `nor2_1` drive
  0.13 pF and 0.18 pF with 2.2 ns and 2.8 ns stage delays.
- **Single runs do not rank the options.** The slack of these paths is set
  by net delay, which changes with placement whenever the netlist changes:
  adding `co` moved the worst slack from −1.76 to −3.93 ns (`rstreg`) and
  from −6.36 to −7.27 ns (`cn_s2`), and the two runs with `free` are the best
  `cn_s2` run and the worst `rstreg` run.

### 7.2 The named variants, full and repeated runs

Full mode is the submission flow of `src/config.json` (Magic DRC is off
there, `RUN_MAGIC_DRC: false`); it adds SPICE extraction, Netgen LVS and the
final checkers. The published cores were used (same generated body as
`rstreg_kc` and `cn_s2_kc` above).

| Variant | Mode, threads | Job | Typ WS | Fast WS | Slow WS | Slow violations | Slow reg-to-reg WS | Route DRC | LVS | Utilization | Flow |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| design of record (reference) | fast, 32 | 24004770 | +0.89 | +6.15 | −8.52 | 2,482 | −0.12 | 0 | | 58.5% | complete |
| `rstreg_timing` | fast, 32 (as `rstreg_kc`) | 23998891 | +4.58 | +8.71 | −3.93 | 423 | −3.93 | 20 | | 61.0% | route DRC errors |
| `rstreg_timing` | fast, 32 | 24004158 | +3.88 | +8.01 | −3.88 | 483 | −3.88 | 26 | | 60.9% | route DRC errors |
| `rstreg_timing` | fast, 32 | 24004159 | +3.54 | +8.12 | −4.32 | 471 | −2.44 | 6 | | 60.9% | route DRC errors |
| `rstreg_timing` | full, 32 | 24001257 | +4.27 | +8.19 | −3.30 | 497 | −0.85 | 23 | not reported | 60.8% | stopped in Netgen.LVS |
| `rstreg_timing` | full, 4 | 24001258 | +3.54 | +8.12 | −4.32 | 471 | −2.44 | 6 | not reported | 60.9% | stopped in Netgen.LVS |
| `cn_s2_timing` | fast, 32 (as `cn_s2_kc`) | 23998892 | +1.73 | +6.91 | −7.27 | 1,113 | +7.21 | 0 | | 57.3% | complete |
| `cn_s2_timing` | fast, 32 | 24004161 | +1.42 | +6.65 | −7.72 | 1,092 | −0.30 | 0 | | 57.2% | complete, 1 antenna violation |
| `cn_s2_timing` | fast, 32 | 24004163 | +1.42 | +6.65 | −7.72 | 1,092 | −0.30 | 0 | | 57.2% | complete, 1 antenna violation |
| `cn_s2_timing` | full, 32 | 24001259 | +1.73 | +6.91 | −7.27 | 1,113 | +7.21 | 0 | 0 errors | 57.3% | complete |
| `cn_s2_timing` | full, 4 | 24001261 | +1.40 | +6.74 | −7.87 | 777 | −1.69 | 0 | 0 errors | 57.3% | complete |

Findings:
- **Neither variant closes the slow corner in any run.** Both meet the
  typical corner, the sign-off corner, in every run, and hold is met at every
  corner.
- **`rstreg_timing`**: slow −3.30 to −4.32 ns with 423 to 497 violations,
  against −8.52 ns (2,482) for the design of record and −7.33 ns (1,379) for
  `rstreg` (23993033) in the same harness; typical margin +3.54 to +4.58 ns
  against +0.89 and +1.42 ns. Every run leaves 6 to 26 detailed-routing DRC
  errors (in 24001258 all six are shorts to `VGND`), at 60.8 to 61.0 %
  utilization against 58.6 % for `rstreg`. In both full runs LibreLane then
  stops in `Netgen.LVS` because it cannot parse Netgen's JSON output
  ("Invalid \escape"), so no LVS result is reported.
- **`cn_s2_timing`**: routes clean and passes LVS in both full runs, but the
  slow corner (−7.27 to −7.87 ns) is not better than `cn_s2` (−6.64 ns,
  23993034) and the typical margin is lower (+1.40 to +1.73 ns against
  +2.40 ns).
- **Spread.** Five runs of each core gave four (`rstreg_timing`) and three
  (`cn_s2_timing`) distinct results. Runs with the same result also occur
  across thread counts and modes (24004159 and 24001258; 23998892 and
  24001259), so the thread count is not what separates them (section 7
  introduction).

### 7.3 Exploratory: flow override and registered inputs

Neither of these is a submission configuration; they measure what is left.

**Post-global-routing timing repair** (`RUN_POST_GRT_RESIZER_TIMING: true`,
fast mode, 32 threads). The submission config leaves it off (section 1).

| Core | Without the override | With it | Job |
|---|---|---|---|
| design of record | typ +0.89, slow −8.52 (2,482) (24004770) | typ +2.56, slow −5.96 (1,460), 2 route DRC errors | 24001096 |
| `rstreg_narrow_k` | typ +5.21, slow −1.76 (62) (23997330) | typ +4.24, slow −3.24 (348) | 24001095 |
| `rstreg_timing` | table of 7.2 | typ +4.20, slow −4.32 (804), 8 route DRC errors, 6 hold violations at the fast corner (−0.04 ns) | 24003894 |
| `cn_s2_timing` | table of 7.2 | typ +2.02, slow −6.88 (557) | 24001094 |

The fast-mode run of the design of record without the override (24004770)
reproduces the official build: typ +0.89 ns, fast +6.15 ns, slow −8.52 ns
with 2,482 violations, slow reg-to-reg −0.12 ns. The repair helps the design
of record but closes nothing, and in the one run of each `rstreg_narrow_k`
came out worse with it.

**Registered host inputs.** A scratch copy of the generator registers
`ui_in` and `uio_in` at the core boundary before any use. This is a contract
change: every response to the inputs comes one cycle later, and the ready and
valid bits on `uo_out` then refer to the previous cycle's `ui_in`. It is not
in the repository and was not verified; the two builds (fast mode, 12
threads) only measure what the input paths cost.

| Core | Typ WS | Slow WS | Slow violations | Route DRC | Remaining slow paths | Job |
|---|---:|---:|---:|---:|---|---|
| `rstreg_timing` + registered inputs | +5.45 | −1.83 | 102 | 1 | 97 endpoints from the instruction SRAM outputs into registers; 5 from one flop to `uio_out` | 24002926 |
| `cn_s2_timing` + registered inputs | +4.82 | −2.12 | 68 | 5 | 63 register-to-register endpoints from one flop (worst −1.18 ns); 5 from another flop, through a chain of `buf_1` repair buffers, to `uio_out` (−2.12 ns) | 24002927 |

### 7.4 What remains

This subsection is the state before `25e331e`. The flow settings that closed
the slow corner afterwards are in section 9; "configuration" below means the
RTL option sets and the one flow override of section 7.3.

- No configuration tried closes the slow corner, including the flow
  override and the registered inputs of section 7.3.
- After the options, three structures fail at the slow corner:
  1. the host inputs through the command decoder to START/STOP, which select
     the next state of the 334 flops of each engine, on long nets driven by
     the smallest cells (section 7.1). Registering the inputs removes these
     paths (section 7.3), but that is a contract change;
  2. the instruction SRAM outputs through the instruction decode into
     registers in the same cycle: −1.75 to −1.83 ns wherever they are
     visible (`rstreg_narrow_k`, `cn_s2_kc` where they end in the engines'
     completed counters, `rstreg_timing` with registered inputs). Pipelining
     the fetch would change the engines' cycle timing and was not attempted;
  3. register-to-output paths into `uio_out` through repair-buffer chains
     (−2.12 ns in `cn_s2_timing` with registered inputs).
- `rstreg_timing` does not route clean at 8x4, density 60 with the current
  configuration; `cn_s2_timing` does, but gains nothing at the slow corner.
- Runs of one core vary by about 1 ns (−3.30 to −4.32 ns for
  `rstreg_timing`), so comparisons between option sets need several runs
  each.

## 8. Reproduction

In the repository:

```sh
scripts/generate.sh --variant rstreg_timing        # or cn_s2_timing
cd test && make PE_VARIANT=rstreg_timing            # cocotb suite (test/README.md)
formal/run.sh --variant rstreg_timing               # formal job list
```

The generated cores equal the published ones below their header line:
`rstreg_timing.v` (sha256 `9177c0327356bb4c53a10db2e2b4a4f2884bb6922e3476b133fdd65fe8cdb122`)
and `cn_s2_timing.v`
(`fd9ad76ea9ec3e451a34d4a2a13a1b55e9ca4d9a57e0dc85c878b5d9624357f6`), listed
in `MANIFEST.sha256` of the variants work directory's `cores/`. LibreLane
runs use `scripts/sweep/make_snapshot.py --core <core> --tiles 8x4
--floorplan fp8_base --density 60 --period 20 --mode fast|full` and
`scripts/sweep/run_one.sh` as described in `docs/notes/sweep.md`.

Outside the repository, in the workstream directory (`rtl-timing/` next to
the variants work directory): the STA and classification scripts and every
report of sections 3 and 7 (`scripts/`, `sta/`), the equivalence flow
(`equiv/`: naming patch, renaming check, anonymous-net separation, memory
expansion, Yosys scripts and logs), the SymbiYosys miters (`fvhost/`), the
lockstep harness patch and campaign (`lockstep/`), the verification snapshot
and its results (`verif/final1/`), the LibreLane runs (`sweep/`), and
`manifest.json`, which lists every Slurm job with its purpose.

## 9. Closure by physical-design configuration (optimizer promotion p010)

The physical-design optimizer (`tools/opt/`, [optimization.md](optimization.md))
searches LibreLane settings for an unchanged core. Commit `25e331e` put its
promotion p010 (trial `dor-26a873-c8bf57e#94`) into `src/config.json`.
`git diff --stat c118027 131e793 -- src/` lists only `src/config.json`:
`src/project.v`, `src/protocol_emulator_core.v` and `src/sram_pdn_cfg.tcl`
are byte-identical, so the closure below is a property of the flow settings.
This section describes the 20 ns configuration committed from `25e331e`
until `d76f1cc`; section 10 describes the 15 ns configuration that replaced
it.

### 9.1 Before and after (official builds)

Both columns are from `tt_submission/stats/metrics.csv` of the `gds`
workflow run on the named commit (8x4 tiles, 20 ns, LibreLane 3.1.0.dev3,
IHP-Open-PDK `2bbec755`). Both runs passed gds, precheck and gl_test.

| | `c118027`, run 36144357821 | `131e793`, run 36257636798 |
|---|---:|---:|
| Setup WS, typ | +0.89 ns | +7.88 ns |
| Setup WS, fast | +6.15 ns | +9.31 ns |
| Setup WS, slow | −8.52 ns | +2.95 ns |
| Setup-violating endpoints, slow | 2,482 | 0 |
| Setup TNS, slow | −9,744.1 ns | 0 |
| Register-to-register setup WS, slow | −0.12 ns (1 endpoint) | +2.95 ns (0) |
| Hold WS, typ / fast / slow | +0.30 / +0.11 / +0.63 ns | +0.32 / +0.11 / +0.66 ns |
| Max-slew / max-cap / max-fan-out violations, slow | 290 / 70 / 524 | 4 / 0 / 3 |
| Utilization (standard cells) | 58.53% (53.88%) | 61.92% (57.64%) |
| Instances | 97,553 | 94,248 |
| Timing-repair buffers / hold buffers | 8,819 / 10 | 10,375 / 137 |
| Route DRC / LVS / antenna violations | 0 / 0 / 0 | 0 / 0 / 0 |
| fmax estimate 1000 / (20 − WS), typ / fast / slow | 52.3 / 72.2 / 35.1 MHz | 82.5 / 93.5 / 58.7 MHz |

The setup slack gained is +6.99 ns (typ), +3.15 ns (fast) and +11.47 ns
(slow). The fmax estimates extrapolate from the 20 ns run and are not a
sign-off at another period (optimization.md, "Frequency tracks and the
SDC"). The differences, estimates and percentages were checked with AXLE
(Lean 4, `lean-4.28.0`, result `okay: true`; the Lean file and the result
are in `<work dir>/docs-timing/axle/`).

**The local prediction matched.** The promoted full run of this
configuration (job 24010051, 4 OpenROAD threads, LVS 0; precheck job
24011934 9/9; gate-level job 24011935, 0 failures) wrote a `metrics.csv`
byte-identical to that of run 36257636798, and the same gate-level netlist.
Its fast-mode trial (job 24009268, 32 threads) gave the same setup and hold
slack at all three corners, utilization, instance count and
slew/cap/fan-out counts (results.md, R19).

### 9.2 What changed

`25e331e` changed these keys of `src/config.json` (and the comments that
describe them); no other key changed, including everything below the
template's "DO NOT CHANGE" line.

| Group | Key | `c118027` | `25e331e` |
|---|---|---|---|
| Placement | `PL_TIMING_DRIVEN` | not set | true |
| | `PL_TARGET_DENSITY_PCT` | 60 | 61 |
| | `MACROS` instances | `fp8_base` (macros in pairs) | `fp8_spread_trk` (spread over the full width) |
| | `FP_MACRO_HORIZONTAL_HALO` | 16.48 | 20 |
| Timing repair | `PL_RESIZER_SETUP_SLACK_MARGIN` | not set | 5.75 ns |
| | `PL_RESIZER_HOLD_SLACK_MARGIN` | 0.1 | 0.15 |
| | `DESIGN_REPAIR_MAX_WIRE_LENGTH` | not set | 300 µm |
| | `DESIGN_REPAIR_MAX_SLEW_PCT` / `_CAP_PCT` | not set | 35 / 50 |
| | `MAX_FANOUT_CONSTRAINT` | not set | 8 |
| Clock tree | `CTS_MAX_CAP` | not set | 0.2 pF |
| | `CTS_OBSTRUCTION_AWARE` | not set | true |
| | `CTS_SINK_CLUSTERING_SIZE` | not set | 16 |
| Global routing | `GRT_LAYER_ADJUSTMENTS` | not set (PDK: 0 on all five layers) | [0, 0.2, 0, 0.1, 0] |
| Synthesis | `SYNTH_STRATEGY` | not set | "DELAY 4" |

### 9.3 What single settings did

The optimizer's first wave changed one setting at a time from the `c118027`
configuration (fast mode, 32 threads, no LVS; optimization.md, "Campaign
record", v1). Slow-corner setup WS at 20 ns:

| Trial | Job | Change from `c118027` | Slow WS | Worst slow path starts at |
|---:|---|---|---:|---|
| 0 | 23993203 | none | −8.52 ns | `rst_n` |
| 1 | 23993204 | setup-repair margin 2 ns | −5.10 ns | `rst_n` |
| 2 | 23993205 | setup-repair margin 5 ns | −1.10 ns | a flip-flop |
| 10 | 23993215 | timing-driven placement | −3.71 ns | `ui_in[3]` |
| 8 | 23993212 | repair maximum wire length 300 µm | −2.63 ns (not legal: route DRC 8) | `rst_n` |
| 7 | 23993210 | `MAX_FANOUT_CONSTRAINT` 6 | −7.00 ns | `rst_n` |
| 14 | 23993221 | density 52 | −7.78 ns | `rst_n` |

Trials 2 and 10 were promoted and gave the same slack in full mode (p003,
job 23994927, −1.102 ns; p002, job 23994350, −3.709 ns). The first trial
with positive slack at all three corners was TPE trial 23 (job 23995853,
slow +0.73 ns; p004, job 23997360). p010 (trial #94) reached +2.954 ns.

The largest single step is the post-CTS setup-repair margin. Section 1
describes why: timing repair runs once, after CTS, on placement-estimated
parasitics, and with the default margin it reported no setup violations in a
sweep run whose routed slow corner failed. With a margin of several
nanoseconds it repairs paths that look positive before routing; in trial 2
(5 ns) it found 2,282 endpoints below the margin (optimization.md). No run
removed one key at a time from p010, so the contribution of each key to the
final +2.95 ns is not measured, and the single-setting steps above are not
additive.

### 9.4 What limits the slow corner now

In the p010 full run (job 24010051), whose metrics equal the official
run's, the worst slow-corner path starts at the output of instruction SRAM
`core.instruction_sram_e1_lo`, and the slow register-to-register slack equals
the overall worst slack (+2.954 ns). Instruction SRAM outputs are item 2 of
section 7.4. The worst paths at the typical and fast corners still start at
`rst_n`, with +7.88 and +9.31 ns. Source: the `slow_worst_start`,
`typ_worst_start` and `fast_worst_start` fields of the p010 records in
`<work dir>/optimizer/store/events.jsonl`.

### 9.5 The RTL variants with optimizer settings

The optimizer also runs each variant core (sections 5 and 6;
[variants.md](variants.md)) with transferred configurations. In fast mode at
20 ns, the best trial of every variant track meets the slow corner
(leaderboard of 2026-09-26 17:55 cluster time): `rstreg_timing` +3.53 ns,
`cn_s2_timing` +2.49 ns, `rstreg` +2.61 ns, `cn_s2` +2.81 ns, `cn`
+2.97 ns, `diet4` (8x4) +3.67 ns and `diet2` +3.85 ns. The best
design-of-record trial is +4.83 ns (trial #154, job 24036312); it was
promoted as p016 and passed the local sign-off with the same slack
([optimization.md](optimization.md)). The variant figures are fast-mode
trials; no variant configuration has been promoted. The RTL
analysis of sections 3 and 7 is unchanged by this: with the `c118027`
settings, the variants did not close the slow corner.

### 9.6 Reproduction

- **Official:** runs 36144357821 and 36257636798, artifact `tt_submission`,
  `stats/metrics.csv`.
- **Local:** the promotion pipeline of [optimization.md](optimization.md)
  ("Promotion", "Operation"). The commit message of `25e331e` records that a
  snapshot rebuilt from that commit with `scripts/sweep/make_snapshot.py`
  has no configuration differences from the promoted run.

## 10. Sign-off at 15 ns, operation at 50 MHz (optimizer promotion p018, `d76f1cc`)

Commit `d76f1cc` put optimizer promotion p018 (trial
`dor15-26a873-8x4-p15-f174dc0#1`) into `src/config.json`. It sets
`CLOCK_PERIOD` 15 (66.7 MHz), so the flow's timing repair and its post-route
multi-corner STA work at 15 ns. `info.yaml` `clock_hz` stays 50000000: the
operating clock is 50 MHz, and the firmware images, the host library,
`pe_timing` and the datasheet assume that clock. tt-support-tools
`d66cf179e` checks only that `clock_hz` is an integer and uses it in
generated documentation; it does not read `CLOCK_PERIOD`
([optimization.md](optimization.md), "Frequency tracks and the SDC"). The
RTL did not change.

The flow fails on a setup violation at the typical corner or a hold
violation at any corner; setup at the fast and slow corners is reported,
not gated ([limitations.md](../limitations.md) section 2).

### 10.1 What changed

`git show d76f1cc -- src/config.json` changes four keys (and adds
comments). Every other key is p010's (section 9.2).

| Key | `131e793` (p010) | `d76f1cc` (p018) |
|---|---|---|
| `CLOCK_PERIOD` | 20 | 15 |
| `DESIGN_REPAIR_MAX_CAP_PCT` | 50 | 45 |
| `GRT_LAYER_ADJUSTMENTS` | [0, 0.2, 0, 0.1, 0] | [0, 0.2, 0.2, 0.1, 0] |
| `PL_RESIZER_SETUP_SLACK_MARGIN` | 5.75 | 5.8 |

These four keys are the `config_changes_vs_repo` of p018's full run against
the tree it ran on (`131e793`; [results.md](../results.md) R84), so the
committed file is the promoted configuration. Why p018 and not the other
15 ns promotions: [optimization.md](optimization.md), "Adoption of p018".

### 10.2 Results at 15 ns

The local column is p018's promoted full run (Slurm job 24042265,
`OPENROAD_THREADS` 4, `out/metrics.csv`). The official column is the `gds`
workflow run on `d76f1cc`, which passed all its jobs; its `metrics.csv`
is byte-identical to the local run's, and so is its gate-level netlist
(results.md R85). The 20 ns build of `131e793` is shown for comparison.

| | `131e793`, run 36257636798, 20 ns (superseded) | p018 full run 24042265, 15 ns (local) | `d76f1cc`, run 36298635436, 15 ns (official) |
|---|---|---|---|
| Setup WS typ / fast / slow | +7.880 / +9.307 / +2.954 ns | +5.950 / +6.924 / +2.240 ns | +5.950 / +6.924 / +2.240 ns |
| Setup-violating endpoints | 0 at every corner | 0 at every corner | 0 at every corner |
| Hold WS typ / fast / slow | +0.318 / +0.112 / +0.657 ns | +0.372 / +0.165 / +0.743 ns | +0.372 / +0.165 / +0.743 ns |
| Max-slew / max-cap / max-fan-out violations, typ; fast; slow | 1/0/3; 0/0/3; 4/0/3 | 1/0/1; 0/1/1; 4/0/1 | 1/0/1; 0/1/1; 4/0/1 |
| Utilization (standard cells) | 61.92% (57.64%) | 65.30% (61.41%) | 65.30% (61.41%) |
| Instances (standard cells) | 94,248 (41,005) | 92,526 (42,919) | 92,526 (42,919) |
| Timing-repair / setup / hold buffers | 10,375 / 0 / 137 | 12,261 / 1,232 / 4,134 | 12,261 / 1,232 / 4,134 |
| Route DRC / LVS / antenna | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Precheck | PASS | 9/9 (job 24053971) | PASS (finished 2026-09-27 13:33 UTC) |
| Gate level | 102 tests, 46 pass, 56 skip, 0 fail | 102 tests, 46 pass, 56 skip, 0 fail (job 24053972) | 102 tests, 46 pass, 56 skip, 0 fail |

The buffer counts are the `metrics.csv` keys
`design__instance__count__class:timing_repair_buffer`,
`design__instance__count__setup_buffer` and
`design__instance__count__hold_buffer`. The worst setup path at 15 ns is
`ui_in[7]` → `uo_out[5]` at the typical and fast corners and
`core.instruction_sram_e1_hi/A_DOUT[0]` → `_62360_/D` at the slow corner.

### 10.3 Margin at 50 MHz: the same layout re-timed at 20 ns

The official flow times the design at 15 ns only. To measure the margin at
the operating clock, the p018 layout was re-timed at 20 ns with the flow's
own STA step. The work is in `<work dir>/sta50/` (`README.md`,
`results.json`, `results.md`); Slurm job 24077956 ran it. The method is
packaged in [`tools/sta/`](../../tools/sta/README.md); run from the repository,
it reproduces these results exactly (jobs 24089033, 24089058, 24089059;
section 10.7).

- **Tool and script.** OpenSTA 2.7.0 inside the `librelane-3.1.0.dev3`
  image, running LibreLane's `OpenROAD.STAPostPNR` script
  (`scripts/openroad/sta/corner.tcl` with `base.sdc`) unmodified, with the
  environment that the step builds. Only `CLOCK_PERIOD` (and the netlist)
  differ between runs. Every variable that `base.sdc` reads was checked
  against each layout's `resolved.json`.
- **Inputs.** p018's final netlist and nominal SPEF, the files that its
  STAPostPNR step read (sha256 checked against `final/SHA256SUMS`); and the
  `tt_submission` netlist and SPEF of run 36257636798 (`131e793`, p010).
- **Controls.** (a) p018 at 15 ns reproduces all 10 timing metrics at all
  three corners of the full run's `metrics.csv` exactly (maximum absolute
  difference 0). (c) The p010 artifact at 20 ns reproduces the CI
  `stats/metrics.csv` of run 36257636798 exactly. So the re-analysis gives
  the flow's own numbers where they exist.

Setup and hold WS in ns (setup-violating and hold-violating endpoints: 0 in
every run):

| Corner | (a) p018 at 15 ns, control | (b) p018 at 20 ns | (c) p010 at 20 ns, control | (b) − (c) |
|---|---:|---:|---:|---:|
| Setup, typ | +5.950 | +8.950 | +7.880 | +1.070 |
| Setup, fast | +6.924 | +9.924 | +9.307 | +0.617 |
| Setup, slow | +2.240 | +6.790 | +2.954 | +3.836 |
| Hold, typ | +0.372 | +0.372 | +0.318 | +0.054 |
| Hold, fast | +0.165 | +0.165 | +0.112 | +0.053 |
| Hold, slow | +0.743 | +0.743 | +0.657 | +0.086 |

At 20 ns the worst setup path of p018 is `ui_in[7]` → `uo_out[5]` at the
typical and fast corners and `ui_in[7]` → `_61238_/D` at the slow corner.
The max-slew, max-cap and max-fan-out counts are the same at both periods
(typ 1/0/1, fast 0/1/1, slow 4/0/1).

So the layout that the flow signs off at 15 ns has, at 50 MHz, 1.07 ns
(typical), 0.62 ns (fast) and 3.84 ns (slow) more setup slack than the
20 ns layout of `131e793`, and slightly more hold slack at every corner.

### 10.4 Why the slack shifts by path class

`base.sdc` makes two terms depend on the period T: the clock period itself
and the input and output delay X = T × `IO_DELAY_CONSTRAINT` / 100, with
`IO_DELAY_CONSTRAINT` 20 (not set in `src/config.json`), so X is 3.0 ns at
15 ns and 4.0 ns at 20 ns. The clock uncertainty (0.25 ns), clock
transition, derate (±5%, on cell and net delays only), output load and
driving cell do not depend on T. The netlist and parasitics are the same,
so every data arrival and clock latency is unchanged. Going from 15 to
20 ns (ΔT = 5 ns, ΔX = 1 ns), each path's slack therefore changes by an
amount fixed by its class:

| Class | Setup slack depends on | Setup change | Hold change |
|---|---|---:|---:|
| register to register | required time T later | +ΔT = +5 ns | 0 |
| input to register | required time T later, arrival ΔX later | ΔT − ΔX = +4 ns | +ΔX = +1 ns |
| register to output | required time T − X | ΔT − ΔX = +4 ns | +ΔX = +1 ns |
| input to output | required time T − X, arrival ΔX later | ΔT − 2·ΔX = +3 ns | +2·ΔX = +2 ns |

**Why input-to-output paths gain 3 ns.** Both ends of such a path carry an
I/O delay: the input arrives X after the clock edge, and the output is
required X before the next edge. The path therefore has T − 2X = 0.6·T of
the period (before uncertainty and its own delay), against 0.8·T for a path
with one I/O end and T for a register-to-register path. Lengthening the
period by 5 ns gives it 0.6 × 5 = 3 ns. Hold slack changes because the
input and output delays are set without `-min`/`-max`, so they also apply
to hold: the input arrival moves by ΔX, and the hold requirement at an
output is −X.

This design has such paths: the window-select bits `ui[7:6]` of the host
port drive write-ready and read-valid (`uo[4]`, `uo[5]`) combinationally
([info.md](../info.md), "Host interface": they "drop immediately when the
window bits change"). `ui_in[7]` → `uo_out[5]` is the worst setup path at
the typical and fast corners at both periods, so a rule that "I/O paths
gain 4 ns" would overstate the typical and fast margin by 1 ns.

### 10.5 Measured shifts

`pe_extra.tcl` (now [`tools/sta/pe_extra.tcl`](../../tools/sta/pe_extra.tcl)),
sourced by `corner.tcl` before its reports, wrote the worst path of each
class. Setup WS in ns:

| Corner | Class | Worst path (the same at both periods) | 15 ns | 20 ns | Change |
|---|---|---|---:|---:|---:|
| typ | register to register | `core.instruction_sram_e1_hi/A_DOUT[0]` → `_62360_/D` | +7.098166 | +12.098166 | +5.000000 |
| typ | input to register | `ui_in[7]` → `_61238_/D` | +6.175710 | +10.175710 | +4.000000 |
| typ | register to output | `_60769_/Q` → `uo_out[4]` | +8.050797 | +12.050797 | +4.000000 |
| typ | input to output | `ui_in[7]` → `uo_out[5]` | +5.949929 | +8.949929 | +3.000000 |
| fast | register to register | `_60776_/Q` → `_61238_/D` | +9.716922 | +14.716923 | +5.000001 |
| fast | input to register | `ui_in[7]` → `_61238_/D` | +8.148133 | +12.148133 | +4.000000 |
| fast | register to output | `_60769_/Q` → `uo_out[4]` | +9.277676 | +13.277677 | +4.000001 |
| fast | input to output | `ui_in[7]` → `uo_out[5]` | +6.924049 | +9.924049 | +3.000000 |
| slow | register to register | `core.instruction_sram_e1_hi/A_DOUT[0]` → `_62360_/D` | +2.239966 | +7.239967 | +5.000001 |
| slow | input to register | `ui_in[7]` → `_61238_/D` | +2.789646 | +6.789646 | +4.000000 |
| slow | register to output | `_60769_/Q` → `uo_out[4]` | +5.944791 | +9.944792 | +4.000001 |
| slow | input to output | `ui_in[7]` → `uo_out[5]` | +4.317587 | +7.317587 | +3.000000 |

Hold WS change of each class, 15 → 20 ns, in ns:

| Corner | Register to register | Input to register | Register to output | Input to output |
|---|---:|---:|---:|---:|
| typ | 0 | +1.000000 | +1.000000 | +1.999999 |
| fast | 0 | +1.000001 | +1.000000 | +1.999999 |
| slow | 0 | +1.000000 | +1.000000 | +2.000001 |

The worst hold path is register to register at every corner, so the hold
WS does not change. The residues of ±0.000001 ns are numerical: OpenSTA
stores times as single-precision seconds (a unit in the last place is
about 0.0000009 ns from 7.45 to 14.9 ns and 0.0000018 ns from 14.9 to
29.8 ns), and the
class values are printed to 6 decimals.

**Prediction check.** With the 15 ns class values, the overall WS at 20 ns
should be min(register to register + 5, input to register + 4, register to
output + 4, input to output + 3):

| Corner | Reg→reg + 5 | In→reg + 4 | Reg→out + 4 | In→out + 3 | Predicted | Measured at 20 ns |
|---|---:|---:|---:|---:|---:|---:|
| typ | 12.098166 | 10.175710 | 12.050797 | 8.949929 | 8.949929 | 8.949929 |
| fast | 14.716922 | 12.148133 | 13.277676 | 9.924049 | 9.924049 | 9.924049 |
| slow | 7.239966 | 6.789646 | 9.944791 | 7.317587 | 6.789646 | 6.789646 |

At the slow corner the limiting class changes: register to register at
15 ns (+2.239966 ns), input to register at 20 ns. The slow-corner gain is
therefore 6.789646 − 2.239966 = 4.549680 ns, not 5 ns. At the typical and
fast corners the input-to-output path limits at both periods, and the gain
is 3 ns.

**LibreLane's register-to-register metric.** `timing__setup_r2r__ws` changes
by 5.601027 (typ), 7.487226 (fast) and 5.308217 ns (slow), not 5 ns. It is
the minimum over the endpoints whose single worst path starts at a
register, and that set depends on the period: an endpoint whose worst path
comes from an SRAM output at 15 ns can have a worst path from an input at
20 ns, because the register path gains 5 ns and the input path 4 ns. For
example, the worst slow path into `_62360_/D` starts at
`core.instruction_sram_e1_hi/A_DOUT[0]` at 15 ns (+2.239966 ns) and at
`ui_in[7]` at 20 ns (+6.796289 ns, below +7.239966 ns), so that endpoint
leaves the set. The class minimum over all register-to-register paths
moves by 5 ns (table above).

### 10.6 What this shows and what it does not

- **The 50 MHz margins are a re-analysis.** They come from the flow's own
  STA script and SDC on the signed-off netlist and parasitics, and the two
  controls reproduce the flow's numbers exactly. They are not the output of
  the official flow, which times the design at 15 ns only.
- **The layout is not rebuilt.** Re-timing at 20 ns keeps the placement,
  buffering and routing done at 15 ns. A flow run at 20 ns would repair to
  different slacks and give a different layout (section 9 is that
  configuration).
- **The I/O delays are an assumption.** They are 20% of the period at both
  periods (3.0 and 4.0 ns). They are not derived from the Tiny Tapeout
  multiplexer, the pads or the demo board. Input-to-output paths are the
  first to lose margin if the real delays are larger.
- **Same knobs, two periods.** p018's knob values are those of v1 trial
  `#118` (the seed "20 ns best #1" of the 15 ns track; [optimization.md](optimization.md),
  "Seeds"). The optimizer ran that knob set through the whole pipeline at
  20 ns as control promotion p027 (full run job 24077610, `OPENROAD_THREADS`
  4, LVS 0; precheck job 24081008 9/9; gate-level job 24081009, 0 fail):
  setup WS typ/fast/slow +7.864 / +9.389 / +2.998 ns, hold WS
  +0.362 / +0.154 / +0.724 ns, utilization 0.6196 (the fast-mode trial,
  job 24014107, gave identical setup WS and utilization). Placed and repaired at
  15 ns and re-timed at 20 ns, the p018 layout has
  +8.950 / +9.924 / +6.790 ns, that is +1.085, +0.535 and +3.792 ns more,
  at a utilization of 0.6530. For this knob set, the larger margin at 50 MHz
  therefore comes from constraining the flow at 15 ns. This is one pair of
  runs; it does not show that the same holds for other knob sets
  (results.md R88).
- **The 6x4 fallback stays at 20 ns.** Its overlay pins `CLOCK_PERIOD` 20,
  so it is signed off at 50 MHz only ([6x4.md](6x4.md)).
- **13.33 ns (75.0 MHz) has a local sign-off only.** The optimizer's first
  13.33 ns promotion, p024, passed the full run, the precheck, the
  gate-level tests and the equivalence check (leaderboard of 2026-09-27
  13:37 UTC), with a small slow-corner margin (setup WS +0.76 ns at
  13.33 ns). It is not committed, so no official build exists
  ([optimization.md](optimization.md), "Current results").

The arithmetic of this section (the differences, the class shifts from the
SDC terms, the prediction minima and the rounding) was checked with AXLE
(Lean 4, `lean-4.28.0`, `okay: true`); the Lean files and results are in
`<work dir>/sta50/axle/` and `<work dir>/docs-15ns/axle/`.

### 10.7 Reproduction

- **Local sign-off:** the promotion pipeline of
  [optimization.md](optimization.md) ("Promotion", "Operation"); p018's
  jobs are listed in [results.md](../results.md) R84.
- **Official:** the `gds` workflow run of `d76f1cc`, artifact
  `tt_submission`, `stats/metrics.csv`.
- **Re-analysis at 20 ns:** [`tools/sta/`](../../tools/sta/README.md). The
  tool runs LibreLane's `corner.tcl` and `base.sdc` on a run's final netlist
  and nominal SPEF, per corner, with only `CLOCK_PERIOD` changed, and writes
  the path-class report. It reads the scripts from the `librelane-3.1.0.dev3`
  image at run time and records their sha256; it does not copy them.
  - **Commands.** `python3 tools/sta/sta_retime.py all --run-dir <run>
    --period <ns> --out <case>` for (a) p018 at 15 ns, (b) p018 at 20 ns and
    (c) p010 at 20 ns. Then `sta_retime.py compare` of (a) with (b) and of
    (c) with (b). It needs apptainer, the image and the IHP PDK, and takes
    about one minute per corner on 3 CPUs.
  - **Inputs.** They are not in git. (c) reads the `tt_submission` artifact
    of `gds` run 36257636798, a CI artifact. (a) and (b) read p018's final
    netlist and nominal SPEF from its local full run (`out/final/` of the
    optimizer run). These are cluster files.
  - **Check of the packaged copy.** Run from the repository as Slurm jobs
    24089033, 24089058 and 24089059, it reproduced job 24077956 exactly:
    every metric, path-class value and report file.
  - **AXLE.** The AXLE check of this section's arithmetic stays in
    `<work dir>/sta50/axle/`.
