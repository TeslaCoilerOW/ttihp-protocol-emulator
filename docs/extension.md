# Line-unit extension (`diet8_rec16`)

Status, 2026-09-29. A second round brought the verification of the variant
closer to that of the design of record: an unbounded formal suite with
ISA-derived references (section 6.4), a random lockstep campaign larger than
the design of record's (section 6.5), a reference written from public
standards (section 6.6), a mutation campaign with fourteen new test modules
(section 11), an antenna fix on the local mirror of the flow (section 7.2.1),
one design selection file that the workflows follow (section 12.2) and
capability bits in `host/` (section 8). Section 13 compares the evidence with
the design of record's. **The variant is still not the design of record.** One
behaviour differs from [isa.md](isa.md): LSTAT bits 13..8 after SE0 in the cell
of a trailing stuff bit (section 6.6); how to resolve it is open.

Status, 2026-09-27 (the first round; kept as written, superseded parts are
marked in the sections). The extension recommended by
[extension-study.md](extension-study.md) ("Conditional GO for `diet8` +
`REC16` at 8x4") is implemented in `hardcaml/` behind a default-off option
and generated as the variant `configs/variants/diet8_rec16.json`. **It is
not the design of record.** `src/protocol_emulator_core.v` still comes from
`configs/instruction-sram-32.json` and is byte-identical to the generator's
output without the option. (That holds on `main`; on branch
`eval/diet8-rec16`, `src/` carries the variant core since `ac436f7`, section
12.1.) Whether to adopt the variant is open (section 9, decision date
2026-11-06).

Every result below names the Slurm job that produced it; the jobs are listed
in section 10. "PASS" means a check that was run and passed.

## 1. Summary

| Item | Result |
|---|---|
| What it adds | Per engine: a bit ticker with an 8-bit fraction, NRZ/NRZI/Manchester (TX) line coding, bit stuffing, a complementary pin pair with SE0 end, an arbitration monitor, a 16-bit CRC with four polynomial presets. Opcodes 30-33 (LTIM, LCFG, CRC, LSTAT) and two XFER mode bits (section 3). |
| Variant | `diet8_rec16` = the `diet4` options with 8-word queues (`diet8`, also added) plus `"line_unit": "rec16"`. ISA version 3; READ_SELECT 7 reads 0x000F5F03 (section 3.3). |
| Default configuration | Unchanged: `make check-generated` PASS; design-of-record formal RTL byte-identical to git `HEAD`'s; default cocotb suite 102/102 with the same per-test status and simulated time as a `HEAD` control; `formal/run.sh --list` unchanged; the 12 quick formal jobs meet their expectations (section 6.1). Second round: with the new `formal/run.sh`, `--list` gives the same 16 jobs, the formal RTL is identical (7 of 7 files) and the 12 quick jobs meet their expectations (24307355); with the base design selection the workflows run the same commands as `main` (section 12.3). |
| RTL against the lockstep model | cocotb lockstep on every cycle (the model follows the RTL's structure; the independent checks are the references in the next row), CI-matching cocotb 2.0.1 and Icarus 13: 160/160 tests on `diet8_rec16` (the 102 default tests, 25 line-unit tests and the 33 tests of the fourteen `test_line_spec_*` modules; 24369277, and on the integrated branch tree 24376082, section 10.1), and the random lockstep campaign of section 6.5: 724,480 cases (304,128 of them line-unit cases), 4,935,357,251 lockstep cycles at RTL and gate level, without a mismatch, all 357 line-unit coverage bins hit. First round (superseded): 127/127 tests and 512 constrained-random cases (3,701,150 cycles) without a mismatch (section 6.3). |
| Independent references | A reference written from the contract and public standards before the RTL was read, `test/model/line_std_ref.py` (section 6.6): 159 standalone tests against published vectors pass; the RTL matches it on 40,997 of 41,000 random cases, and the 3 other cases and one directed test differ on the LSTAT[13:8] point below. The formal references of section 6.4 are written from [isa.md](isa.md). From the first round: nine catalogued CRC check values (CRC-16/IBM-3740 0x29B1, ARC 0xBB3D, USB 0xB4C8, CAN-15 0x059E, ...), bit-level line encoders and decoders, the ticker formula, and the study's three demos (10BASE-T UDP frame, CAN node, USB low-speed IN responder) decoded at the pads (sections 6.2, 6.3). No third-party protocol peer (section 8). |
| Formal | The 16 existing jobs on `diet8_rec16` meet their expectations. 46 line-unit jobs, all meeting their expectations (section 6.4): unbounded proofs of the tick schedule, the CRC of driven and sampled line XFERs against the reference fold, coder/decoder inverse for any valid ticker with delay 0 and up to 32 bits, the driven cells and pins against an ISA-derived encoder, the decoder against an ISA-derived decoder, the arbitration monitor, pin locality, reset/START and decode; bounded checks from reset against the closed-form CRC; at least one negative control per property, using all ten seeded defects. One LSTAT field differs from [isa.md](isa.md) in one case (section 6.4, "Finding"). First round (superseded): 15 line-unit jobs, the codec and end-to-end CRC properties bounded. |
| Mutation testing | Three uniform samples. The unit's state logic (3,000 of 90,372 `mutate` mutations): committed suite 52.39 %; with fourteen `test_line_spec_*` modules (33 tests) 92.98 % [91.99 %, 93.86 %] in-sample and 92.72 % [90.90 %, 94.19 %] on a held-out sample of 1,000. The rest of the logic the option adds or changes, except the statements downstream of line-unit state (2,117 of 63,773, of which 1,181 are on the blocked-cycle count's stages for base opcodes): committed suite 79.64 %, with the modules 87.72 % [86.22 %, 89.09 %]; all 246 of its survivors are argued on the blocked-cycle count. Neither proven nor argued: 76 (2.65 %), 26 (2.71 %) and 0 (section 11). First round: not run. |
| Area | TT-replica synthesis (Yosys 0.66, AREA 0): 432,203.4 µm² and 3,903 flops for the core, +56,980.1 µm² and +272 flops over `diet8` (section 7). |
| Hardening at 8x4 | One full LibreLane run with the committed 15 ns configuration: flow complete, utilization 63.2% (design of record 65.3%), setup met at all three corners (WS typ +6.03, fast +7.38, slow +2.07 ns), hold met (WS min +0.166 ns), route DRC 0, LVS 0, one antenna violation. On that layout the Tiny Tapeout precheck passes 9/9, the gate-level suite has 0 failures, and the netlist is sequentially equivalent to the RTL (`formal_eq`) (sections 7.2, 7.3). The official Tiny Tapeout actions then passed on branch `eval/diet8-rec16` (run 36360490711: gds, precheck, gl_test) with a `metrics.csv` byte-identical to the local run's, and the official netlist is equivalent to the RTL (section 12.1). Second round: with `GRT_ANTENNA_REPAIR_DIODE_ONLY` set in the branch's `src/config.json`, the local mirror gives antenna 0 with setup WS slow +2.21 ns, route DRC 0, LVS 0, precheck 9/9, gate level 0 failures and an equivalent netlist (section 7.2.1); no official run of that configuration exists yet. |
| Open finding | LSTAT[13:8] reads 1 instead of 0 when a sampling XFER ends on SE0 in the cell of a trailing stuff bit; found independently by the formal harnesses and the standards-based reference, present in the RTL, the official netlist and the lockstep model (sections 6.4, 6.6). Not resolved; `test_line_stdref` is therefore not in the default module list. |
| Workflows and host | `configs/design-selection.txt` names the design; regen, test, formal and equivalence follow it (section 12.2), checked by local emulation only. `host/` decodes the capability bits (section 8). |
| Comparison with the design of record | Section 13. |

## 2. What the extension does

It gives each engine the means to produce and receive serial line codes
without bit-banging: a free-running bit clock with fractional period, line
coders and decoders that run while the engine executes other instructions,
and a CRC that follows the data bits. With it, one engine can send a 10BASE-T
frame at 4 cycles per bit with no gap between queue words, send and receive
CAN 2.0A data frames with arbitration and acknowledgement (no remote, error or
overload frames, no retransmission after a lost arbitration, no
resynchronization, no error counters), and answer a USB low-speed IN token
within the bus turnaround time (packet level only, no address or endpoint
filter) ([extension-study.md](extension-study.md) section 7; the firmware is in
`firmware/ext/`, section 4).

The feature set is the study's `REC16`: the ticker shares the XFER tick and
period registers (a classic XFER stops it), and the CRC has four fixed
polynomials instead of a programmable one. It adds 68 flip-flops per engine.

### 2.1 Differences from the study's prototype

The RTL was re-implemented in `hardcaml/lib/` from the prototype of
`<local work dir>/tt-work/extension/` (extension-study.md section 10). The
behaviour of the prototype's firmware and checks is kept: the prototype's 27
engine-level and 16 whole-chip checks, ported to `hardcaml/test/line_*.ml`,
pass on the new RTL (section 6.2), and the three firmware listings assemble to
the same words (section 4). Differences:

- **CRC presets in both bit orders.** Each preset selects a polynomial; an
  LSB-first transfer uses its reflected form, an MSB-first transfer its normal
  form left-aligned in 16 bits. In the prototype each preset had one fixed
  constant, so MSB-first transfers were meaningful only with the CAN preset.
  This makes CRC-16/IBM-3740 (CCITT-FALSE, MSB first) and CRC-16/UMTS
  available and costs a second constant table.
- **LCFG with stuffing on either polarity and run length 1 is invalid.** In
  the prototype the stuff bit itself completed the next run, so such an XFER
  never ended (found by the directed tests here).
- **LTIM with P = 255 and a fraction is invalid.** P + carry would not fit the
  shared 8-bit tick register.
- **Pin writes compose with the Manchester second half.** An instruction that
  writes pins (OUT, a classic XFER) in the cycle in which the unit writes a
  Manchester second half writes on top of it; in the prototype the
  instruction's write replaced it.
- **LCFG also clears the cell bit**, so that every line-unit register is
  written by an instruction (needed by the structural register attribution of
  `formal/gen/generate_fv.ml`); no visible effect.
- **Capability bits** in READ_SELECT 7 (the study's proposal, section 4
  there), present only with the option.

## 3. ISA additions

The contract is [isa.md](isa.md), section "Line-unit extension"; in short:

| op | mnemonic | operation |
|---:|---|---|
| 17 | XFER | c bit 5: line XFER (a data bits; b = 0; c2 MSB first, c3 drive, c4 sample); c bit 6: feed the CRC (also in a classic XFER) |
| 30 | LTIM | half-period P, fraction Q/256, first-tick delay D; P = 0 stops the ticker |
| 31 | LCFG | line code, stuffing (enable, polarity, run length 1-8), pair, arbitration, SE0 end, initial level |
| 32 | CRC | set from a register, read into a register, select preset 0-3 |
| 33 | LSTAT | flags (SE0, arbitration lost, stuff error), queue flags (TX data, RX space), line level, ticker running, bits left at SE0 |

### 3.1 Timing

LTIM issued in cycle c0 ticks in cycles T(0) = c0 + D (P when D = 0) and
T(k+1) = T(k) + P + carry(k), with an 8-bit phase accumulator that adds Q per
tick and carries into one extra cycle. Even ticks are bit boundaries, odd ticks
mid-bit. A drive-only line XFER completes at the boundary tick of its last
cell, a sampling one at the mid-bit tick of its last bit (both measured by
`test_line_unit.test_completion_points`). With P >= 2 the next line XFER has
3 cycles to issue before the next boundary (study section 4), which
`PULL; XFER; LOOP` uses; the 10BASE-T tests check that the words stream with
no gap.

### 3.2 Examples

- USB low speed at 50 MHz: `LTIM 16, frac=171` gives 2 × (16 + 171/256) =
  33.3359375 cycles per bit, a rate 78 ppm below 1.5 Mbit/s.
- 10BASE-T at 40 MHz: `LTIM 2` with Manchester gives 4 cycles per bit, 10 Mbit/s.
- CAN at 50 MHz: `LTIM 50` gives 100 cycles per bit, 500 kbit/s.

### 3.3 Discovery

READ_SELECT 7 keeps the ISA version in bits 7..0 and carries constant
capability bits in 23..8: line unit, fraction, stuffing, arbitration,
CRC-16, (CRC-32), presets, and one bit per engine with the unit. `diet8_rec16`
reads 3 + 0xF5F × 256 = 0x000F5F03. Without the option the word is unchanged
(2, or 3 on the diet variants). A host that compares READ_SELECT 7 with an ISA
version must mask bits 7..0; `host/` does so and decodes bits 23..8 (section
8). (Until the second round `host/` did not mask them.)

## 4. Firmware (`firmware/ext/`)

Assembler sources and images, assembled with
`hardcaml/_build/default/bin/assemble.exe --source firmware/ext/NAME.source.json --variant configs/variants/diet8_rec16.json --output firmware/ext/NAME.image.json`.
The assembler accepts the line-unit mnemonics only for a target with the
option (`Isa.encode ~line_unit`, `assemble --variant`); without it they are
rejected. `scripts/gen_variants.sh` reassembles every image for a variant with
the option and fails unless the result equals `firmware/ext/` byte for byte
(its check 4).

| Image | Words | Pins | Clock | Function |
|---|---:|---|---|---|
| `10base-t-udp` | 34 | TD+ 0, TD- 1 | 40 MHz | Preamble and SFD made on chip, then 17 queue words (a 68-byte frame, destination to FCS), no gap; TP_IDL; a link pulse every 640,000 cycles (16.0 ms) while idle |
| `can-node` | 62 | TXD 0, RXD 1 | 50 MHz | CAN 2.0A at 500 kbit/s: TX of queued frames with CRC-15 and ACK check (fault 70), arbitration loss (fault 72), RX with CRC check (fault 71) and ACK, frames at the minimum spacing |
| `usb-ls-in-responder` | 51 | D+ 0, D- 1 | 50 MHz | Receives a token (NRZI, destuffing, CRC5 residue), answers IN with the queued DATA packet and a CRC16 computed by the unit; ignores tokens with a wrong CRC5 and non-IN PIDs. It has **no address or endpoint filter**: it answers an IN to any address |
| `relay` | 4 | none | any | Moves TX-queue words to the RX queue; with ROUTE it stages words for another engine (base ISA) |
| `crc16-stream` | 17 | 0 (not enabled) | any | CRC-16/CCITT over queued words, MSB first, 2 cycles per bit |

The first three are the study's listings (revision 3, sections 7.1-7.3 there).
Their words equal those of the listings in the ported Hardcaml tests
(`hardcaml/test/line_fw.ml`, printed by `line_test.exe --dump-firmware`), and
`can-node` also equals an independent Python encoding of the listing
(`test/line_demos.py`, `can_node_words`). Limits of the firmware are those of
the study (its sections 7.2-7.6): no CAN retry, error frames or bus-idle check,
USB at packet level only and without an address or endpoint filter, one staged
Ethernet frame per session.

## 5. Implementation

| Path | Content |
|---|---|
| `hardcaml/lib/line_options.ml` | The option (`"line_unit": "none" \| "rec16"`), read from the refinement's `"options"` object like `Timing_options`; validation (fused issue, 32-bit datapath, not with `split_instruction_decode`); the capability bits |
| `hardcaml/lib/line_unit.ml` | CRC presets and step function, and the seeded defects used by the negative controls |
| `hardcaml/lib/engine.ml` | The unit, built only with the option (`?line`); `?line_mutation` is used only by the formal generator and `line_test.exe` |
| `hardcaml/lib/processor.ml`, `refinement_config.ml`, `variant_options.ml` | Pass the option through; READ_SELECT 7; the debug export `dbg_queue_status_read` (LSTAT issue, formal only) |
| `hardcaml/lib/isa.ml`, `assembler.ml`, `bin/assemble.ml` | LTIM, LCFG, CRC, LSTAT and the XFER line/CRC bits, for targets with the option |
| `hardcaml/test/line_fw.ml`, `line_test.ml`, `line_sys_test.ml` | Engine-level and whole-chip checks ported from the study (executables). Since `4c0753c`, `dune test` also runs `line_test`; `line_sys_test` takes a config and is run separately (`hardcaml/test/README.md`) |
| `configs/variants/diet8.json`, `diet8_rec16.json` | The two new variants |
| `test/model/line_unit.py`, `test/model/variant.py` | Reference model of the unit (`LineReference`), selected only with the option |
| `test/line_support.py`, `line_scenarios.py`, `line_demos.py`, `line_random.py` | Encoders, independent references, scenarios, random generator |
| `test/test_line_unit.py`, `test_line_demos.py`, `test_line_random.py` | cocotb modules; skipped unless `PE_VARIANT` has the option; `test/Makefile` adds them for `PE_VARIANT=diet8_rec16` |
| `test/test_line_spec_*.py` (14 modules), `test_line_spec_common.py` (their helper) | Tests of specified behaviour written from the mutation survivors (section 11.3); `test/Makefile` adds them for `PE_VARIANT=diet8_rec16` |
| `test/model/line_std_ref.py`, `test/stdref/`, `test/test_line_stdref.py` | The standards-based reference, its standalone tests and its cocotb module (section 6.6); not in the default module list |
| `test/model/line_coverage.py`, `campaigns/random/line_gen.py`, `campaigns/random/results/diet8_rec16/` | Line-unit coverage bins, the campaign's line generator and the campaign summaries (section 6.5) |
| `campaigns/mutation/` (`gen_line_mutants.sh`, `line_region.py`, `line_survivor_classes.py`, `sample_score.py`, `campaign-line-diet8_rec16.env`, `results/diet8_rec16/`) | The line-unit mutation campaign (section 11) |
| `formal/line_crc.sv`, `line_codec.sv`, `line_engine.sv`, `line_tick.sv`, `line_tx.sv`, `line_rx.sv`, `line_arb.sv`, `line_ref.vh`, `*.sby` | Line-unit properties; registered in `formal/run.sh`, and their `.sby` files derived by `formal/variant_sby.py`, only for a variant with the option (section 6.4) |
| `configs/design-selection.txt`, `scripts/design_selection.sh` | The design selection that the workflows and `test/Makefile` follow (section 12.2) |
| `host/pe_host/protocol.py`, `host.py`, `image.py`, `selftest.py` | Capability bits, device profiles, the image capability check and the self-test's line-unit section (section 8) |
| `firmware/ext/` | Section 4 |

**Default-off guarantee.** Without the option `Engine.create` creates no
additional signal and keeps the order in which it creates signals, so the
emitted Verilog is unchanged. Checked by: `make check-generated`;
`scripts/gen_variants.sh` checks 1-3; generation of every published variant
core (`base`, `rstreg`, `cn`, `cn_s2`, `diet4`, `diet2`, `rstreg_timing`,
`cn_s2_timing`) byte for byte equal to `<local work dir>/tt-work/variants/cores/`
below the header line; and the formal RTL comparison of section 6.1.

**Existing tests touched.** `test/harness.py` (a `VERSION_WORD` constant),
`test/test_smoke.py` (compares READ_SELECT 7 with it), `test/variant_workloads.py`
(uses the line model for a variant with the option), `test/variants.py` (two
entries) and `test/test_kill_decode.py` (on a variant with the option, four
encodings that become legal line-unit instructions are replaced by invalid
encodings of the same opcodes). For the design of record each constant equals
its previous value and the operand sweep's case list is identical to `HEAD`'s
(same 541 cases per engine).

## 6. Verification

### 6.1 The default configuration is unchanged

| Check | Result | Evidence |
|---|---|---|
| `make check-generated` | PASS | local run (the generator writes the identical file) |
| `scripts/gen_variants.sh diet8 diet8_rec16`: checks 1 (design of record regenerates), 3 (fifo-8 firmware) and 4 (`firmware/ext`) | PASS | local run |
| Published variant cores regenerate byte for byte (8 cores) | PASS | local run |
| Formal RTL of the design of record (`fifo.v`, `engine.v`, `processor_debug.v`, `processor_fv.v`, the mutant and the attribution) from the working tree equals that from git `HEAD` | PASS: 7 of 7 files identical | 24121900, 24125084 |
| `formal/run.sh --list` | the same 16 jobs; the 15 line jobs appear only with `--variant diet8_rec16` | local run |
| Quick formal jobs of `scripts/reproduce.sh` on the design of record (12 jobs: `reset_safety`, `engine_safety`, `processor_invariants_prove`, `processor_inductive_prove`, `processor_inductive_cover`, `timing_isolation_prove_k0`..`k3`, `timing_isolation_cover`, both negative controls) | 12/12 meet their expectations | 24122038, 24125085 |
| Default cocotb suite (`make`, no variables) | 102/102; per-test status and simulated time equal to a control run of git `HEAD`'s `test/` and `src/` (102/102) | 24121511, 24122075, 24134620 (final tree); control 24121512 |
| `dune test` in `hardcaml/` (at the time the seven Hardcaml tests, including the `variant_test` quick suite; since `4c0753c` it also runs `line_test`, and the waveform expect tests when their packages are installed) | PASS | 24122140 |
| Second round: the design of record with the new `formal/run.sh` (`--list`, RTL generation, the 12 quick jobs) | `--list` gives the same 16 jobs (the 46 line jobs appear only with `--variant diet8_rec16`); 7 of 7 formal RTL files identical to the previous round's `HEAD` generation; 12/12 meet their expectations | 24307355 |
| Second round: the workflows with the base design selection (`main` 24f31f0 plus the workflow changes of section 12.2, local emulation) | `test`: 102/102 with the same names and results as unmodified 24f31f0; `formal`: the same 16 jobs in the same order, 16/16 meet their expectations; `regen`: no difference in `src/` | 24309463, 24309631, 24300291 (section 12.3) |

### 6.2 Hardcaml checks (ported from the study)

| Check | Result | Job |
|---|---|---|
| `line_test.exe`: engine level, 18 unit checks (CRC check values of seven catalogued CRCs, USB-LS TX and RX, CAN TX and arbitration, 10BASE-T Manchester) and 13 firmware checks (10BASE-T UDP frame and link pulse, CAN node TX/RX/arbitration/minimum spacing and the revision-2 negative control, USB IN responder) | 31/31 PASS | 24119789 |
| `line_sys_test.exe configs/variants/diet8_rec16.json`: whole chip on 8-word queues, no host service after START: staging capacities, 10BASE-T frame with one relay and its under-staged negative control, five CAN scenarios and two CAN negative controls, reset in the middle of a CAN frame (80 line-unit registers read 0), USB IN responder | 16/16 PASS | 24120022 |
| Seeded defects (`line_test.exe --mutation`) | 7 of 10 detected: `crc_tap` (9 failing checks), `stuff_run` (7), `fraction_carry` (6), `rx_destuff_run` (4), `nrzi_decode` (4), `manchester_halves` (2), `arbitration_off` (2). Not detected here: `pin_leak`, `start_keeps_crc`, `ltim_overflow`. Each of the ten defects is the seeded defect of at least one formal negative control (section 6.4) | 24120021 |

### 6.3 cocotb (RTL, lockstep with the reference model)

cocotb 2.0.1 and Icarus Verilog 13.0, the CI versions. The harness compares
`uo_out`, `uio_out` and `uio_oe` with `test/model/line_unit.py` before and
after every edge.

The lockstep model is not independent of the RTL: it implements the contract
of [isa.md](isa.md) but was written together with the RTL and follows
`engine.ml`'s structure step for step, so a misreading of the contract that
both share would not show up as a mismatch. The independent checks are the
published CRC check values and the references in `test/line_support.py`
(line encoders and decoders, the behavioural CAN node, the USB low-speed host
with its own CRC5, the Ethernet decoder with CRC-32) against which the tests
decode the pads, and the ticker timing formula. Section 6.6 adds a reference
that does not follow `engine.ml`, and the `test_line_spec_*` modules of
section 11.3 check values taken from the contract text.

| Module | Tests | Result | Job |
|---|---|---|---|
| `test_line_unit` | 12 directed tests: version word; nine CRC check values; CRC from classic and sampling XFERs; ticker timing for ten (P, Q, D) sets; 11 TX line-coding cases; 11 RX decoding cases (destuffing, stuff errors, SE0 end with remaining count, CRC of sampled bits); arbitration; 43 encodings (invalid ones fault with code 1, valid neighbours complete); LSTAT; START/STOP/reset; ticker sharing; completion points | 12/12 PASS | 24134619 |
| `test_line_demos` | 10BASE-T UDP frame with one relay (76 bytes decoded, CRC-32 residue 0xDEBB20E3, TP_IDL 12 cycles, first link pulse 639,998 cycles later, 4 cycles wide) and its under-staged negative control; CAN TX + RX at the minimum spacing (DLC 8/5 and 0/4/1), back to back with arbitration won, arbitration lost (fault 72), the revision-2 listing (fault 71) and RX overflow (fault 4) negative controls, reset mid-frame; the node's ACK of each received frame (dominant in the ACK slot, recessive at both delimiters, dominant nowhere else in the frame); USB IN responder (turnaround 3.02 bit times); USB tokens with a wrong CRC5, OUT and SETUP ignored, then an IN answered with its EOP (SE0 66 cycles, J 33 cycles, then released); an IN to another address and endpoint also answered (no filter); CRC stream | 12/12 PASS | 24134619 |
| `test_line_random` | 16 random cases per run | PASS | 24134619 |
| Random campaign 1 | 16 runs × 16 cases (seeds 0x11AE0000 + 1000 i + k), 1,848,487 cycles after START | 256/256 cases, 0 mismatches | 24121515 |
| Random campaign 2 | 16 runs × 16 cases (seeds 0x22BE0000 + 1000 i + k), 1,852,663 cycles | 256/256 cases, 0 mismatches | 24125398 |
| Random lockstep campaign (second round) | 17 campaigns, section 6.5 | 724,480/724,480 cases, 0 mismatches | section 6.5 |
| Full suite with `PE_VARIANT=diet8_rec16`, first round's default Makefile module list | 102 default + 25 line-unit tests | 127/127 PASS | 24134619 |
| Full suite with `PE_VARIANT=diet8_rec16`, second round's default module list | 102 default + 25 line-unit + 33 `test_line_spec_*` tests | 160/160 PASS on the `f9e0bf9` test tree plus the fourteen modules; 160/160 PASS on the integrated branch tree (section 10.1) | 24369277, 24376082 |
| Full default suite with `PE_VARIANT=diet8` | 102 default tests | 102/102 PASS | 24121514 |

Functional coverage of random campaign 1 (events counted by the model;
campaign 2 has the same event classes with similar counts):
7,302 line XFERs and 245 faulting ones, 1,087 classic XFERs (801 CRC feeds),
3,875 stuff bits sent (569 trailing), 6,238 removed, 5,567 stuff errors,
1,154 arbitration losses, 282 SE0 ends, 9,181 Manchester second halves,
3,759 LTIM, 2,365 LCFG, 5,054 CRC and 1,516 LSTAT instructions, and 609
faults on invalid line-unit encodings (68 CRC, 110 LCFG, 47 LSTAT, 40 LTIM,
99 classic XFER, 245 line XFER). Section 6.5 gives the second round's
coverage bins.

An earlier run of the full suite on the variant (24121513) failed one test,
`test_kill_decode.test_kill_operand_sweep`, on its own expectation that XFER
with c bit 6 faults; RTL and model agreed (the bit is the CRC feed). The sweep
now uses invalid encodings of those opcodes on such variants (section 5). The
first run of `test_line_demos` (24121510) failed in the reset test's register
read-out helper (1-bit handles), which was then fixed. The final RTL runs are on
snapshot s4 (the test tree after the independent review: the CAN ACK check and
the USB rejection, EOP and address tests; the model's docstring).

### 6.4 Formal

`formal/run.sh --variant diet8_rec16` runs the 16 existing jobs with the
variant's settings and 46 line-unit jobs (formal/README.md, "Line unit"). The
final run is array job 24307353 on snapshot s1, with RTL generated by 24307337 (the
generated variant core equals `src/protocol_emulator_core.v` of the branch
below its header line; 18 of the 21 generated files are byte-identical to
those of snapshot f2, job 24125084, and the other 3 are the new mutant files):
62 of 62 jobs meet their expectations (41 PASS, 21 negative controls FAIL as
required). The three new mutant files (`stuff_run`, `fraction_carry`,
`arbitration_off`) are generated with the same `--mutation` option as the
seven before. An independent re-run of all 62 jobs
from a separate copy of the tree (array 24311615, RTL from 24311313, identical
to 24307337's) gave the same outcome for every job.

**Existing jobs on `diet8_rec16`:** unchanged harnesses and RTL; results in
24307353:

| Job | Result | s |
|---|---|---:|
| `reset_safety` | PASS | 5 |
| `fifo_conservation` | PASS | 256 |
| `engine_safety` | PASS | 28 |
| `processor_invariants_bmc` | PASS | 28 |
| `processor_invariants_prove` | PASS | 4 |
| `processor_inductive_bmc` | PASS | 11 |
| `processor_inductive_prove` | PASS | 5 |
| `processor_inductive_cover` | PASS | 6 |
| `timing_isolation_prove_k0` | PASS | 42 |
| `timing_isolation_prove_k1` | PASS | 41 |
| `timing_isolation_prove_k2` | PASS | 43 |
| `timing_isolation_prove_k3` | PASS | 41 |
| `timing_isolation_bmc` | PASS | 295 |
| `timing_isolation_cover` | PASS | 15 |
| `timing_isolation_neg_pull` | FAIL as required | 10 |
| `timing_isolation_neg_mutant` | FAIL as required | 11 |

**Line-unit jobs.** Unbounded jobs are k-induction proofs whose harnesses
also assert lemmas (program state, equalities between engine registers and
the harness's reference model); lemmas are proved but are not claims. Where a
lemma or reference reads a register without an observation port
(`transfer_tick`, `transfer_period`, `tx`), the `.sby` script adds the port
with Yosys `expose`, which changes no logic. The reference models (tick
schedule, encoder, decoder, arbitration monitor, CRC fold) are written from
[isa.md](isa.md), not from `engine.ml`, with two qualifications: `line_arb`
relates the lost flag to the engine's own cell bit, which is checked against
the ISA only for drive-only XFERs (by `line_tx`), and after a wrong stuff bit
the decoder reference continues from the received bit, a state on which
isa.md is silent and in which the reference follows the RTL. The encoder,
decoder and arbitration references take the tick cycles from the engine's
tick counter; `line_tick_prove` proves that these cycles are the ISA's T(k).
In the independent re-run, removing any one assumption made the prove job
fail, and every seeded defect failed the full prove harness (24311894,
24312184, 24312831).

| Job | Property | Method | Result | s |
|---|---|---|---|---:|
| `line_tick_prove` | Arbitrary instruction streams and host commands: the phase register toggles exactly at T(0) = c0 + (D, or P when D = 0), T(k+1) = T(k) + P + carry(k), counted in cycles in which the engine runs; before tick k the phase is k mod 2; LSTAT's ticker-running flag follows LTIM, classic XFER, START and reset | k-induction (lemmas: tick counter = distance to T(k) + 1; period, fraction and accumulator = P, Q, acc(k); no classic XFER in progress) | PASS | 3 |
| `line_tick_cover` | Ticks with carries (P = 2, Q = 171), a carry at P = 1, a tick during WAIT | cover | PASS | 5 |
| `line_crc_tx_prove` | Driven line XFER, N 1..32, any valid LTIM (P 1..255, any fraction and delay), any legal LCFG (pair, every stuffing run length and polarity, NRZ/NRZI/Manchester), any preset, initial value and bit order: from the CRC set on, the CRC register equals the reference step folded over the data bits done, in shift order, one step per data bit; after the XFER over all N | k-induction | PASS | 15 |
| `line_crc_rx_prove` | Sampling line XFER (sample only, or drive and sample), free input pins, the same operands without Manchester: the CRC register equals the reference fold over the bits shifted into rx, in arrival order; rx shifts once per data bit | k-induction | PASS | 3 |
| `line_crc_tx_bmc`, `line_crc_rx_bmc` | The same from reset with P <= 2, no fraction or delay, N <= 6, any legal LCFG (pair, stuffing runs 1-8), also against the closed-form reference over DATA or rx, and the XFER completes by cycle 57 (59) | BMC 58, 60 | PASS, PASS | 282, 717 |
| `line_crc_tx_cover`, `line_crc_rx_cover` | Completed XFERs with the pair, stuffing runs 1-3 and P = 2; fraction with NRZI and either-polarity stuffing; 20 bits or more (Manchester for TX); SE0 end; drive and sample with pair, stuffing and fraction | cover | PASS, PASS | 185, 5 |
| `line_codec_prove` | Two engines, A drives, B samples A's pins: B receives A's N data bits (complements for Manchester), sees no stuff error and no SE0, and its CRC equals A's; any valid LTIM with delay 0 (P 1..255, any fraction), N 1..32, any legal LCFG except stuffing with Manchester | k-induction (lemmas: equal tickers; A at most one cell ahead of B, and one ISA encoder step ahead while a cell is pending) | PASS | 511 |
| `line_codec_cover` | Fraction with NRZI and either-polarity stuffing; NRZI with ones stuffing at P = 2; Manchester with pair and fraction | cover | PASS | 19 |
| `line_tx_prove` | Driven line XFER (any valid LTIM, any legal LCFG, N 1..32): from its first cell the data pin carries the level of the ISA's cell sequence (stuff bit when the run of equal bits or of 1s reached the run length; NRZ, NRZI, Manchester halves), the pair pin its complement, LSTAT's line level equals it, and the XFER completes when its last cell (with a trailing stuff bit when due) is sent | k-induction (reference encoder) | PASS | 30 |
| `line_rx_prove` | Sample-only line XFER, free input pins (any valid LTIM, legal LCFG without Manchester, N 1..32): rx equals the reference decoder's register (NRZ/NRZI decoding, destuffing), the stuff-error and SE0 flags and LSTAT's remaining-bits count equal the reference's (one case excluded, below), and the XFER completes when the reference ends | k-induction (reference decoder) | PASS | 2 |
| `line_arb_prove` | Drive-and-sample line XFER, monitor on, free input pins: the lost flag is set exactly at a mid-bit sample (after the first boundary, no SE0) that reads 0 while the current cell bit is 1, and every cell driven after that is a 1 | k-induction | PASS | 1 |
| `line_tx_cover`, `line_rx_cover`, `line_arb_cover` | Completed XFERs: NRZI with either-polarity stuffing and fraction; ones stuffing of run length 1 with the pair; Manchester with delay and pair; stuff errors; SE0 end with a delay; P = 2; arbitration lost and not lost | cover | PASS, PASS, PASS | 9, 6, 3 |
| `line_crc_rx_se0_stuff_cover` | From reset: a sampling XFER ends on SE0 in the cell of a trailing stuff bit (the excluded case, below) | cover | PASS | 3 |
| `line_pins_prove`, `line_reset_prove`, `line_decode_prove` | The claims of `line_pins_bmc`, `line_reset_bmc` and `line_decode_bmc` (same assertions), unbounded | k-induction | PASS, PASS, PASS | 3, 3, 3 |
| `line_crc_equiv` | The CRC step equals the reference for all 2^16 states, both bits, 4 presets and both bit orders | prove | PASS | 2 |
| `line_crc_e2e` | Driven CRC line XFER after LTIM 1, 1-10 bits, closed-form reference (as before) | BMC 42, bitwuzla | PASS | 719 |
| `line_codec_bmc` | Two engines, P = 1, 1-8 bits (as before) | BMC 46 | PASS | 1,300 (2,331 in the re-run 24311615; it needs `SBY_TIMEOUT` of at least 3,600) |
| `line_codec_bmc_p2` | Two engines, P = 2, 1-4 bits (as before) | BMC 46 | PASS | 143 |
| `line_pins_bmc` | Pin locality (as before) | BMC 20 | PASS | 4 |
| `line_reset_bmc` | Zero after reset and START (as before) | BMC 16 | PASS | 3 |
| `line_decode_bmc` | Invalid encodings fault, valid ones complete (as before) | BMC 16 | PASS | 5 |

By method, the 46 line-unit jobs are 11 unbounded proofs, 8 bounded checks,
8 covers and 19 negative controls.

**Negative controls.** Every line-unit property has at least one, which must
fire one of the property's claim assertions; all ten seeded defects of
`Line_unit.mutation` are now used (before: seven). A control counts only
when the assertion that names it (`target of:`) fired. The `*_prove` jobs of
`line_pins`, `line_reset` and `line_decode` check the same assertions as
their BMC jobs, whose controls are `line_pins_neg`, `line_reset_neg` and
`line_decode_neg`. The closed-form CRC assertion that only `line_crc_tx_bmc`
and `line_crc_rx_bmc` compile in has no control of its own; it shares the
`crc_tap` controls of claim C2.

| Control | Defect | Result | s |
|---|---|---|---:|
| `line_crc_equiv_neg` | `crc_tap` | FAIL as required | 2 |
| `line_crc_e2e_neg` | `crc_tap` | FAIL as required | 27 |
| `line_crc_tx_neg` | `crc_tap` | FAIL as required | 4 |
| `line_crc_rx_neg` | `crc_tap` | FAIL as required | 5 |
| `line_codec_neg_nrzi` | `nrzi_decode` | FAIL as required | 5 |
| `line_codec_prove_neg_nrzi` | `nrzi_decode` | FAIL as required | 3 |
| `line_rx_neg_nrzi` | `nrzi_decode` | FAIL as required | 7 |
| `line_codec_neg_destuff` | `rx_destuff_run` | FAIL as required | 5 |
| `line_codec_prove_neg_destuff` | `rx_destuff_run` | FAIL as required | 3 |
| `line_rx_neg_destuff` | `rx_destuff_run` | FAIL as required | 2 |
| `line_codec_neg_manchester` | `manchester_halves` | FAIL as required | 5 |
| `line_codec_prove_neg_manchester` | `manchester_halves` | FAIL as required | 3 |
| `line_tx_neg_manchester` | `manchester_halves` | FAIL as required | 2 |
| `line_tx_neg_stuff` | `stuff_run` (TX and RX stuff one bit late; invisible to the codec claims) | FAIL as required | 2 |
| `line_arb_neg` | `arbitration_off` | FAIL as required | 2 |
| `line_tick_neg` | `fraction_carry` | FAIL as required | 3 |
| `line_pins_neg` | `pin_leak` | FAIL as required | 3 |
| `line_reset_neg` | `start_keeps_crc` | FAIL as required | 3 |
| `line_decode_neg` | `ltim_overflow` | FAIL as required | 3 |

**What the line-unit properties exclude.** The CRC, codec, TX, RX and
arbitration jobs run fixed programs (one XFER after START) with free
operands; `line_tick`, `line_pins`, `line_reset` and `line_decode` take
arbitrary instruction streams. `line_codec_prove` uses LTIM delay 0 (with a
delay, B's first mid-bit tick can come before A's first cell in this
two-program set-up), a direct wire, B with A's LCFG (NRZ for Manchester,
arbitration off) and no stuffing with Manchester. The receiver of a
drive-and-sample XFER (whose stuffing follows the sampled bits) is checked
only for the arbitration monitor and the CRC. The classic XFER's CRC feed is
not in formal. The bounded jobs keep their bounds (`line_crc_e2e` P = 1 and
N <= 10; `line_codec_bmc` P = 1 or 2, N <= 8 or 4; `line_crc_tx_bmc` and
`line_crc_rx_bmc` P <= 2, N <= 6); the unbounded jobs above cover the other
cases. All are engine-level properties; "no effect on other engines" is
covered by the timing-isolation proofs and by `line_pins` with
`processor_invariants`, as before.

**Finding: LSTAT's remaining-bits count at SE0 in a trailing stuff cell.**
When a sampling XFER ends on SE0 in the cell of a trailing stuff bit (all N
data bits received, the stuff bit due after them), LSTAT bits 13..8 report 1,
although no data bit remains; [isa.md](isa.md) defines the field as "data bits
remaining at SE0". The CRC and rx hold all N bits. The RTL loads the field
from the XFER's counter, which holds 1 while the trailing stuff bit is
pending (`engine.ml`, SE0 branch: `l_rem <-- xremaining`); the lockstep
model does the same (`test/model/line_unit.py`). `line_rx_prove` (claim
R3) and `line_crc_rx_prove` (claim C1) exclude this case from their
bit-count claims only; their rx and CRC claims include it. Without the
exclusion, a BMC run of `line_rx` from reset fails on R3 (24312860).
`line_crc_rx_se0_stuff_cover` (24307353_45, PASS)
reaches it from reset with N = 1: LCFG 0x284 (NRZ, stuffing on runs of 1s of length 1,
pair, SE0 end), PINS 0x004 (RX pin 0, pair pin 4), LTIM P = 1, XFER 1 bit
sample-only; pin 0 reads 1 at the first mid-bit tick, both pins read 0 at the
next one; LSTAT then reports SE0 with 1 bit remaining, rx and the CRC hold
the received bit. The standards-based reference of section 6.6 found the same
deviation independently. There is no RTL change in this round; whether to change the
RTL (report N minus the bits received) or the ISA text (the count includes a
pending trailing stuff bit) is open. With the RTL change, the two exclusions
would be dropped and the cover would become an assertion that the count is 0.

**CI subset.** `formal/run.sh --variant diet8_rec16 --ci --parallel 4` runs
the 42 line-unit jobs except the four long bounded checks (`line_crc_e2e`,
`line_codec_bmc`, `line_crc_tx_bmc`, `line_crc_rx_bmc`). With the RTL of
24307337 it took 526 s (job 24307354, snapshot s1) and 411 s (job 24309392,
snapshot s2, the final `run.sh`) wall-clock on 4-CPU Slurm allocations, which
on these nodes are 4 cores with 8 hardware threads; all 42 expectations met in
each, the longest job `line_codec_prove` (481 s and 370 s). The independent
re-run took 695 s on such an allocation (24312498) and 525 s pinned to 4
hardware threads (24313629), all 42 met. The CI workflow runs these jobs as
separate matrix entries (section 12.2). No run on a GitHub-hosted runner
exists yet.

#### First round (2026-09-27), superseded by the run above

`formal/run.sh --variant diet8_rec16` runs the 16 existing jobs with the
variant's settings and 15 line-unit jobs (formal/README.md, "Line unit").
The final run is job 24125085 on snapshot f2 (RTL generated by 24125084),
together with the 12 quick jobs on the design of record: 43 of 43 jobs meet
their expectations. An earlier run (24122038, snapshot f1) gave the same
PASS/FAIL status for every job it completed; there the three codec negative
controls were counted as unmet because `run.sh` then required every negative
control to fire a pin assertion, which the target-assertion rule below
replaced, and the two deep jobs were cancelled for the solver comparison
(24122280-24122285).

**Existing jobs on `diet8_rec16`:** all 16 meet their expectations. The
timing-isolation miter now includes engine K's 19 line-unit registers and
7-bit transfer mode in its shared state, and an LSTAT of engine K (which
reads its queue flags) ends the window like PULL, PUSH and WAITEVENT
(`-DLINE_UNIT`).

| Job | Result | s |
|---|---|---:|
| `reset_safety` (variant core behind `src/project.v`) | PASS | 5 |
| `fifo_conservation` | PASS | 269 |
| `engine_safety` (engine with the unit) | PASS | 27 |
| `processor_invariants_bmc` / `_prove` | PASS / PASS | 20 / 4 |
| `processor_inductive_bmc` / `_prove` / `_cover` | PASS / PASS / PASS | 10 / 7 / 9 |
| `timing_isolation_prove_k0` .. `_k3` (unbounded, per engine) | PASS × 4 | 48, 49, 59, 52 |
| `timing_isolation_bmc` (20 cycles) | PASS | 335 |
| `timing_isolation_cover` | PASS | 15 |
| `timing_isolation_neg_pull`, `_neg_mutant` | FAIL as required, on a pin assertion | 9, 13 |

**Line-unit jobs:**

| Job | Property | Method | Result | s |
|---|---|---|---|---:|
| `line_crc_equiv` | The CRC step (all 2^16 states, both bits, 4 presets, both bit orders) equals a reference written from the published generator polynomials | prove | PASS | 2 |
| `line_crc_equiv_neg` | same, `crc_tap` defect | prove | FAIL as required | 2 |
| `line_crc_e2e` | Production engine: after LTIM 1, LCFG (NRZ, NRZI or Manchester; stuffing off or runs of 4-8; pair off), a driven CRC line XFER of 1-10 bits of any data, with any preset, initial value and bit order, leaves the reference CRC | BMC 42, bitwuzla | PASS | 1,230 |
| `line_crc_e2e_neg` | same, `crc_tap` defect | BMC | FAIL as required | 29 |
| `line_codec_bmc` | Two engines, A drives, B samples A's pins at P = 1: B receives A's 1-8 data bits (their complements for Manchester), sees no stuff error and no SE0, and its CRC equals A's; any data and bit order; LCFG any legal value except stuffing together with Manchester (B uses A's LCFG with NRZ for Manchester and the arbitration bit cleared) | BMC 46 | PASS | 2,336 |
| `line_codec_bmc_p2` | same at P = 2, 1-4 data bits | BMC 46 | PASS | 363 |
| `line_codec_neg_nrzi`, `_neg_destuff`, `_neg_manchester` | same with the `nrzi_decode`, `rx_destuff_run`, `manchester_halves` defects | BMC | FAIL as required | 6, 7, 5 |
| `line_pins_bmc` | Outside SET and OUT, the engine's logical outputs change only on the two pins named by PINS | BMC 20 | PASS | 3 |
| `line_pins_neg` | same, `pin_leak` defect | BMC | FAIL as required | 2 |
| `line_reset_bmc` | After reset and after START every line-unit register is 0 | BMC 16 | PASS | 2 |
| `line_reset_neg` | same, `start_keeps_crc` defect | BMC | FAIL as required | 2 |
| `line_decode_bmc` | Invalid line-unit encodings fault with code 1, keep the PC and change no line-unit configuration, flag or CRC; valid LTIM/LCFG/CRC/LSTAT complete; valid line XFERs start | BMC 16 | PASS | 9 |
| `line_decode_neg` | same, `ltim_overflow` defect | BMC | FAIL as required | 3 |

A negative control counts only when the assertion it targets fired (the
assertion's source line names the control; `formal/run.sh`).

**What the line-unit properties exclude** (first round; the unbounded jobs of
the second round remove most of these limits, see above). `line_crc_e2e`: P = 1 only, 1-10
data bits, drive-only XFERs, no pair, stuffing runs 1-3 (they would lengthen
the XFER beyond the bound); the CRC of sampled bits and of classic XFERs is
covered by `line_codec` (equal CRCs) and the cocotb tests, not by a reference
comparison in formal. `line_codec`: P = 1 or 2 only, 1-8 data bits at P = 1
and 1-4 at P = 2, no stuffing with Manchester (not a supported combination for
a receiver, since the unit decodes no Manchester), the receiver with the
transmitter's LCFG (NRZ for Manchester, arbitration off), no fraction, and no
bus other than a direct wire (no arbitration, SE0 or noise). `line_pins`,
`line_reset` and `line_decode` are bounded to 20, 16 and 16 cycles from
reset. All five are engine-level properties.

"No effect on other engines" is covered by the timing-isolation proofs (each
engine's pins do not depend on anything the other engines, their line units,
the host or the mover do) and by `line_pins_bmc` together with
`processor_invariants` (an engine's line unit writes only the pins PINS names,
and outputs are masked by ownership).

### 6.5 Random lockstep campaign

The random lockstep campaign of the design of record
([verification-campaign.md](verification-campaign.md), "Random differential
campaign": 536,064 cases, 3,207,184,824 lockstep cycles) was repeated on
`diet8_rec16`, with a generator for the line unit added. Scripts:
[`campaigns/random/`](../campaigns/random/README.md) (`line_gen.py`, README
"Line-unit campaigns"); line-unit bins: `test/model/line_coverage.py`;
summaries: [`campaigns/random/results/diet8_rec16/`](../campaigns/random/results/diet8_rec16/).
Job ids: `<local work dir>/tt-work/ext-random/manifest.json`. Runs from
2026-09-28 16:35 to 2026-09-29 11:34 (EDT).

| item | value |
|---|---|
| design under test | `git archive` of branch `eval/diet8-rec16` at `f9e0bf97…` (main `24f31f0` merged into `ac436f7`) plus the campaign files of this section; the RTL and gate-level simulations were built from snapshot s1, whose `src/` and `test/` files are identical to those of the final snapshot s2 (with `SOURCES.sha256`); `src/protocol_emulator_core.v` sha256 `d517e277…` (the published `diet8_rec16` core), `src/project.v` `2aacfd24…` |
| reference model | `test/model/variant.py` with `test/model/line_unit.py`, configured by `PE_VARIANT=diet8_rec16`; `uo_out`, `uio_out` and `uio_oe` compared before and after every edge. Every result file records the variant, `fifo_words` 8 and the model options (`line_unit` `rec16`) |
| stimulus | line cases (`line_gen.make_line_case`, profiles `line`, `line-dense`, `line-faulty`) and the generator variants of the design of record (`random_gen.make_case`, generation 2: `default`, `dense`, `faulty`, `hostile`, `deselect`) |
| gate-level netlist | `tt_submission/tt_um_teslacoilerow_protocol_emulator.v` of the official gds action run 36360490711 (commit `ac436f7`, the same core), sha256 `e2404100…`; IHP `sg13cmos5l` cell models, behavioural SRAM models, zero delay. The antenna-fix layout of section 7.2.1 has a different netlist, on which the random campaign was not run |
| simulator | Icarus Verilog 13.0 (the version of the Tiny Tapeout actions), cocotb 2.0.1, Python 3.12 |
| compute | Slurm `mit_preemptable` and `mit_normal` (with `--requeue`), `mit_quicktest` |

**Line cases.** Each loaded engine gets a line-unit program (85%) or a program
of the base generator. A line program sets up DIR, SET, PINS, LCFG, LTIM and
the CRC and then draws blocks: line XFERs of 1 to 32 bits (drive, sample, both,
rarely neither; LSB or MSB first; with or without the CRC bit) with data from
PULL, LOAD, NOT or MOV and results made visible by PUSH, CRC read and LSTAT;
LTIM with edge values of P (0, 1, 2, 254, 255), Q (0, 1, 128, 255 and others)
and D (0, 1, 255 and others); LCFG with every line code, both stuffing
polarities, run lengths 1 to 8, pair, arbitration monitor, SE0 end and initial
level; CRC set, read and preset selection; classic XFERs with the CRC bit; the
base instruction mix; and invalid line-unit encodings. The pads are driven by
a pad environment: toggling pads, delayed copies, inversions and wired-AND of
other pads, SE0 episodes on pin pairs, and transmitters that send stuffed NRZ
or NRZI frames with a complementary pair pin and an SE0 end, often at the bit
rate of a receiving engine. The host traffic is the base generator's (TX
writes, RX reads, all eight status selections, raw commands including ROUTE for
the mover, partial transfers, reloads, revive, mid-traffic deselect) plus
line-program reloads. Each case ends with STOP and a read of all eight status
selections of every engine, so READ_SELECT 7 (the capability bits) is compared
in every case. `line-dense` draws longer programs and fewer host idles;
`line-faulty` raises the rate of invalid encodings. A case is generated from
(seed, index) alone; a failing case is saved as JSON and replays through the
driver with `PE_REPLAY`.

**Fidelity check** (job 24210822): seed 1 through the unmodified upstream flow
(`upstream_run.sh`, `make COCOTB_TEST_MODULES=test_random PE_VARIANT=diet8_rec16`)
passed 64 cases with the same per-case lockstep cycle counts (64 of 64) as seed
1 of `r16-rtl-default` run through the campaign driver. The in-tree
`test_line_random` module, with the line-unit bins attached, passed its 16
cases in the same job.

| campaign | generator | level | seeds | cases/seed | host cycles/case | cases run | passed | failed | infra errors | lockstep cycles | sim CPU-h | Slurm array |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `r16-rtl-line` | line | RTL | 0x1..0x1000 (4096) | 64 | 6,000 | 262,144 | 262,144 | 0 | 0 | 2,160,539,004 | 164.5 | 24210655 |
| `r16-rtl-line-dense` | line-dense | RTL | 0x300001..0x300100 (256) | 64 | 6,000 | 16,384 | 16,384 | 0 | 0 | 142,627,943 | 9.5 | 24210666 |
| `r16-rtl-line-faulty` | line-faulty | RTL | 0x400001..0x400100 (256) | 64 | 6,000 | 16,384 | 16,384 | 0 | 0 | 134,751,258 | 9.2 | 24210667 |
| `r16-rtl-line-long` | line | RTL | 0x100001..0x100100 (256) | 16 | 50,000 | 4,096 | 4,096 | 0 | 0 | 214,000,318 | 15.3 | 24210668 |
| `r16-rtl-default` | default | RTL | 0x1..0x1000 (4096) | 64 | 2,000 | 262,144 | 262,144 | 0 | 0 | 1,016,818,199 | 88.6 | 24210657 |
| `r16-rtl-xcov-default` | default | RTL | 0x1001..0x1400 (1024) | 64 | 2,000 | 65,536 | 65,536 | 0 | 0 | 254,192,597 | 20.9 | 24210669 |
| `r16-rtl-long` | default | RTL | 0x100001..0x100100 (256) | 64 | 20,000 | 16,384 | 16,384 | 0 | 0 | 358,593,524 | 35.4 | 24296483 |
| `r16-rtl-xcov-dense` | dense | RTL | 0x300101..0x300200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 178,664,383 | 13.9 | 24296958 |
| `r16-rtl-xcov-faulty` | faulty | RTL | 0x400101..0x400200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 161,873,137 | 11.2 | 24296959 |
| `r16-rtl-xcov-hostile` | hostile | RTL | 0x500001..0x500100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 160,617,203 | 11.2 | 24296960 |
| `r16-rtl-xcov-deselect` | deselect | RTL | 0x600001..0x600100 (256) | 64 | 2,000 | 16,384 | 16,384 | 0 | 0 | 62,814,773 | 7.0 | 24296493 |
| `r16-gl-line` | line | GL | 0x1..0x80 (128) | 8 | 6,000 | 1,024 | 1,024 | 0 | 0 | 8,438,586 | 5.3 | 24210659 |
| `r16-gl-line-extended` | line | GL | 0x81..0x180 (256) | 16 | 6,000 | 4,096 | 4,096 | 0 | 0 | 33,717,963 | 18.5 | 24296549 |
| `r16-gl-default` | default | GL | 0x1..0x40 (64) | 8 | 2,000 | 512 | 512 | 0 | 0 | 1,993,883 | 0.9 | 24210660 |
| `r16-gl-extended` | default | GL | 0x41..0x140 (256) | 32 | 2,000 | 8,192 | 8,192 | 0 | 0 | 31,754,483 | 24.4 | 24296494 |
| `r16-gl-hostile` | hostile | GL | 0x500001..0x500040 (64) | 16 | 8,000 | 1,024 | 1,024 | 0 | 0 | 10,039,757 | 6.3 | 24296961 |
| `r16-gl-deselect` | deselect | GL | 0x600001..0x600040 (64) | 16 | 2,000 | 1,024 | 1,024 | 0 | 0 | 3,920,240 | 1.9 | 24296548 |
| **total** | | | 12,096 seed runs | | | **724,480** | **724,480** | **0** | 0 | **4,935,357,251** | 443.9 | |

- **Line cases:** 304,128 cases (299,008 RTL, 5,120 gate level) and
  2,694,075,072 lockstep cycles, 0 failures.
- **Generators of the design of record:** 420,352 cases and 2,241,282,179
  lockstep cycles, 0 failures; the default generator ran 5,120 seeds
  (327,680 cases), 256 seeds ran at 20,000 host cycles per case.
- **RTL:** 708,608 cases, 4,845,492,339 lockstep cycles. **Gate level:**
  15,872 cases, 89,864,912 lockstep cycles; each GL case took exactly as many
  lockstep cycles as the same seed and case at RTL (15,872 of 15,872, paired
  with `r16-rtl-line`, `r16-rtl-default`, `r16-rtl-xcov-hostile` and
  `r16-rtl-xcov-deselect`). Since both runs pass lockstep against the same
  deterministic model, equal counts are close to guaranteed; this is a pairing
  check.
- Every seed produced a result file: no timeouts, no simulator crashes, no
  infrastructure errors in these campaigns.
- In `r16-rtl-line` the fault output `uo[7]` (any engine faulted) is high in
  84.3% of the cycles the coverage observer counts (`r16-rtl-default`: 69.4%;
  `r16-rtl-line-long`, 50,000 host cycles per case: 96.8%).

**Line-unit coverage.** `test/model/line_coverage.py` counts 357 bins from the
reference model's state before and after each edge (in a passing case the DUT
equals the model on every public pin): the LTIM value classes of P, Q and D and
their edge values; ticker behaviour (fraction carry, the carry at P = 1 and at
P = 254, ticks during WAIT and while stalled on PULL, PUSH, WAITPIN and
WAITEVENT, a classic XFER stopping the ticker); every LCFG field value; CRC
set, read and preset per register and preset; CRC steps per preset, bit order
and source (line drive, line sample, classic drive, classic sample: 32 bins);
LSTAT per register and per field value; line XFERs started per mode (drive,
sample, both) and bit count (96 bins), completed per mode and length class,
per bit order, line code, pair and CRC bit; every invalid-encoding reason of
[isa.md](isa.md) except opcodes 34..255 (those are generated and compared in
lockstep, and counted in the base fault bins); stuff bits sent and removed per polarity and run length, trailing
stuff bits, stuff errors; arbitration loss per line code; SE0 end per class of
remaining bits; Manchester second halves (also after completion, with the pair,
and under OUT, SET and a classic XFER); line XFERs on 2, 3 and 4 engines at
once; the mover, host TX writes and RX reads during line XFERs; STOP, BEGIN,
reset and deselect during a line XFER; START clearing the unit; and three
protocol-shaped configurations (USB-like, CAN-like, Manchester with pair).

All 357 bins are hit in the merged campaigns, in `r16-rtl-line` alone, and at
gate level in `r16-gl-line` alone (1,024 cases); no bin is declared
unreachable. The rarest in `r16-rtl-line` (262,144 cases), per 1,000 cases:
SE0 end on a trailing stuff cell 3.79 (993 hits), the CAN-like drive+sample
XFER completed 42.7, START while a Manchester second half is pending 46.1, the
USB-like sampling XFER ended by SE0 50.5, reset or deselect during a line XFER
55.4, and drive+sample XFERs of 30 bits 56.1. Per-bin tables (merged, and per
campaign of line cases):
[`report.md`](../campaigns/random/results/diet8_rec16/report.md). The
generators of the design of record also run with the observer attached; they
issue few line-unit words (13 to 26 bins hit per RTL campaign) and serve the
decode check below. An independent review compared the bins with the model's
own state changes, edge by edge, in 768 model-only cases and found no
inconsistency (24310137).

For comparison, the in-tree generator of `test_line_random`
(`test/line_random.py`, the CI module) hits 245 of the 357 bins in 2,048
model-only cases (32 runs × 64 cases, seeds 0x11AE0000 + 1000 k + i, job
24297123). Its 112 holes: line XFER bit counts other than 1-3, 5, 8, 13, 16
and 32 (72 bins), stuffing run lengths 4 and 7 (4 LCFG and 8 stuff-bit bins),
P from 16 to 253 and D from 16 (4 bins), six invalid CRC field values, two
invalid LSTAT fields, three invalid line XFER reasons (a > 32, ticker stopped,
data pin not owned), CRC steps of MSB-first classic sampling XFERs (4 bins),
and 9 single bins (the polarity bit without stuffing, LSTAT reading the ticker
stopped, the ticker running while stalled on WAITEVENT, a line XFER with
neither drive nor sample, a line drive on an open-drain pin, the mover, BEGIN,
reset or deselect during a line XFER, START with a Manchester second half
pending). The CI module is a regression check; the campaign generator is the
one that reaches every bin.

**Coverage of the base bins.** The bins of `random_gen.Coverage` (opcodes per
engine, stalls, fault bins, XFER flag combinations and bit counts, host
commands accepted and rejected, host traffic) have no hole, in the merged
campaigns and in `r16-rtl-default` alone. The 71 bins of `xcov.py` (131,072
cases in the five `r16-rtl-xcov-*` campaigns) have no hole and no observer
error. The rarest xcov bin is the synchronous start of 4 engines, 0.397 per
1,000 default cases (26 hits in 65,536); on the design of record it was also
the rarest (0.36).

**Decode check.** For every line-unit instruction issue (LTIM, LCFG, CRC,
LSTAT, and XFER with c bit 5, 6 or 7) and every classic XFER, the coverage
observer classifies the encoding as valid or invalid from its own reading of
[isa.md](isa.md) ("Invalid encodings"; `contract_reasons`, not derived from the
model) and compares that with whether the DUT and the model faulted. There were
0 disagreements in all 17 campaigns (`r16-rtl-line` alone: 3,111,358
invalid-encoding reasons counted on faulting issues). Fed a deliberately wrong
contract reading, the check reports the disagreement (independent review,
24310137). Two
wording points of isa.md are counted as notes, not disagreements:

- isa.md lists "the data pin not owned when driving, or with the pair set a
  pair pin that is not owned or equals the data pin" among the invalid line
  XFERs, and the RTL (`engine.ml`, the line XFER rule) and the model apply the
  pair-pin conditions to driving XFERs only. A sample-only line XFER with the
  pair set and a pair pin that is not owned or equals the data pin executed
  497,828 times in the line campaigns, identically in the DUT and the model.
  A wording that states the rule of the RTL: "a line XFER with b != 0,
  c[1:0] != 0, a outside 1..32, the ticker stopped, Manchester with sampling,
  or, when driving, the data pin not owned or (with the pair set) a pair pin
  that is not owned or equals the data pin".
- The LCFG row of isa.md's opcode table says "[6:4] run length - 1 (length 1
  with [3] set is invalid)", which can be read as invalid with stuffing ([2])
  off; the "Invalid encodings" paragraph ("stuffing on either polarity with run
  length 1") matches the RTL, the model and the observer, which treat [3] = 1,
  [2] = 0, [6:4] = 0 as valid. A table wording that matches: "(length 1 with
  [2] and [3] set is invalid)".

Neither wording change has been made; they are listed in section 8.

**Negative controls.** Each control runs the stimulus of a normal campaign
with one seeded defect; the defect has to make the lockstep fail.

| control | defect | stimulus | seeds failing | cases failing | Slurm array |
|---|---|---|---:|---:|---|
| `r16-negctl-xor` | model: the result of XOR corrupted (`PE_INJECT_MODEL_BUG=xor`, as in the base campaign) | `default`, seeds 1-64 × 64 cases | 64 of 64 | 1,462 of 4,096 (35.7%) | 24210664, 24295697 |
| `r16-negctl-model-crc` | model: the LSB-first CRC step takes its feedback from bit 1 instead of bit 0 (`INJECT=line-crc`) | `line`, seeds 1-64 × 16 cases | 64 of 64 | 488 of 1,024 (47.7%) | 24210661 |
| `r16-negctl-rtl-crc` | RTL copy: the reflected CRC-16/CCITT polynomial 0x8408 changed to 0x8409 | `line`, seeds 1-64 × 16 cases | 56 of 64 | 158 of 1,024 (15.4%) | 24210662 |
| `r16-negctl-rtl-carry` | RTL copy: the ticker's fraction carry forced to 0 in all four engines | `line`, seeds 1-64 × 16 cases | 64 of 64 | 910 of 1,024 (88.9%) | 24210663 |

Every failing case failed with a lockstep mismatch. The RTL copies are
`src/protocol_emulator_core.v` with the edits in
`campaigns/random/results/diet8_rec16/rtl-defect-*.diff` (one line and four
lines), simulated like the real core (Icarus 13, job 24210470). The CRC defect
only shows when preset 3 steps LSB-first bits and the CRC value then reaches
the pins (for example CRC read, PUSH and a host RX read), which 158 of the
1,024 cases did. The independent review rebuilt the two defect cores from the
committed `.diff` files (identical hashes) and re-ran 19 seeds of the
campaigns and controls with identical per-case results (24310328).

**Mismatches.** None: no case failed in any of the 17 campaigns, so there was
nothing to classify as an RTL, model or generator bug. Infrastructure only:
task 0 of `r16-negctl-xor` (seeds 1 to 16) first ran on a node where the
simulator was about 20 times slower, and every seed hit the 900 s timeout
without a result file; its rerun 24212554 failed at start (environment), and
24295697 ran it. Four arrays submitted with 2,000 instead of 8,000 host cycles
per case were cancelled within a minute, before any result file, and
resubmitted with 8,000. The review re-summed all 12,096 per-seed result files
and got the totals above (24310062).

**Limits.** The oracle is the lockstep model, which follows `engine.ml`
(section 6.3), so a misreading of the contract that the RTL and the model share
passes. The parts of this campaign that do not depend on the model are the
decode check and the negative controls; the random line programs of section
6.6 are checked against a reference that does not follow `engine.ml`. The
stimulus is synchronous to the clock (no clock offsets between a transmitter
and a receiver beyond the fraction); the gate-level runs are zero-delay.

Arithmetic: `<local work dir>/tt-work/ext-random/axle/fragment_known.lean`
(33 theorems) and `fragment_totals.lean` (55 theorems), AXLE `okay: true`.


### 6.6 Pads against a standards-based reference (`test_line_stdref`)

The lockstep model follows `engine.ml` step for step (section 6.3), so a
misreading of the contract shared by the RTL and the model would not show up
as a lockstep mismatch. `test/model/line_std_ref.py` is a second reference,
written from the contract ([isa.md](isa.md), "Line-unit extension", and
sections 1 to 3.3 of this document), the `firmware/ext/` listings and public
standards (reveng CRC catalogue, USB 2.0 and the USB-IF CRC paper, Bosch CAN
2.0, IEEE 802.3) before the Hardcaml RTL, `test/model/line_unit.py`,
`formal/line_ref.vh` or the other line-unit tests were read. During that
clean-room phase the base-ISA sections of isa.md and section 1 of this page
were also read (contract and summary text, no implementation). It computes CRCs
by polynomial division over GF(2) (the contract's bit-serial rule is checked
against it), codes lines by the standards' rules (NRZI as in USB 2.0 7.1.8,
Manchester as in IEEE 802.3: complement, then the bit; stuffing by polarity
and run length as in USB 2.0 7.1.9 and CAN 2.0), schedules ticks by the
contract's recursion, and decodes pad traces back into bits, stuff errors,
SE0 and CRC. `test/test_line_stdref.py` checks the RTL or a netlist against
it only; its host driver (`test/stdref/chip.py`) does not use the harness or
the lockstep model. `test/stdref/README.md` lists the parts and sources.

**Provenance after the clean-room phase.** The clean-room version of the
reference is kept in the work directory (`line_std_ref.py` sha256
`38d03908…`). After the first RTL runs the reference received fixes of its
own bugs, each moving it closer to the contract text (the Manchester
level-update timing, a separate NRZI previous sample, and a guard against an
unbounded prediction loop), and nine `Interpretation` fields for the points
the contract leaves open (below). Where the clean-room version had a
behaviour, the field defaults reproduce it; `classic_ds_crc_basis` (the
clean-room program model rejected a classic drive-and-sample XFER with the CRC
bit) and the reset reading of the stuff-error state were added after the RTL
runs, the latter after `test/model/line_unit.py` had been read, which weakens
that one reading's independence. The random generator `test/stdref/cases.py`
was written after the clean-room phase; it is not derived from
`test/line_random.py`.

For every engine of every case the module compares `uio_out` and `uio_oe` of
the engine's pins in every cycle from START to the end of the program with the
reference's prediction, decodes every driven line XFER from the pads, and
compares the pushed words (CRC, LSTAT, rx), the TX queue level and the fault
status. Receive stimuli are built by the standards' encoders and played at
the contract's sample ticks; in about half of the receiving engines the pins
carry the complement in every cycle except the predicted sample cycle, so a
sample one cycle early or late reads the wrong level.

| Check | Result | Job |
|---|---|---|
| Standalone reference tests (`pytest test/stdref`, no simulator): 24 reveng catalogue entries (check value and residue), the USB-IF paper's 5 token and 2 data CRC examples and residuals, `binascii` CRC-16/XMODEM, CRC-16/IBM-3740 and CRC-32 cross-checks, the contract's serial CRC rule against division for all 2^17 (register, bit) pairs of each preset and bit order, USB SYNC/NRZI/stuffing, CAN stuffing and the Bosch CRC register, the ticker formula, the decoder's error reports, and four `firmware/ext` demos (all but `relay`) run on the reference and decoded into standard frames | 159/159 PASS | 24301323; integrated tree 24376082 |
| Ticker closed form T(k) = T(0) + kP + floor(kQ/256) | proved by induction (Lean 4 + Mathlib, `test/stdref/ticker_closed_form.lean`, AXLE check) | — |
| RTL, whole module with defaults: timing convention, capability word 0x000F5F03, demos (crc16-stream against CRC-16/IBM-3740 and XMODEM; 10BASE-T UDP frame and link pulse; USB IN token answered with DATA1 and EOP; CAN frame sent and ACKed, received and ACKed, lost arbitration), 17 open-point probes, 2 directed SE0 cases, random cases 0..1999 | 7 of 9 tests PASS; `test_se0_in_trailing_stuff_cell_leaves_no_data_bits` and random case 1171 fail on one point (below) | 24301324; the same result on the integrated tree, 24376082 |
| RTL random campaign, cases 2000..40999 (25 jobs) | 39,000 cases; 2 fail (31108, 38972), on the same point | 24301325, 24301327-24301334, 24301337, 24301339-24301344, 24301389-24301396, 24301435 |
| Gate level, official netlist of run 36360490711 (commit ac436f7), default gate-level mode (CAN demo skipped, one USB data set, 4 random cases) | 7 PASS, 1 SKIP, 1 FAIL (the directed SE0 test) | 24301439 |
| Gate level, same netlist, `PE_LINE_GL_FULL=1` and random cases 0..1999 (10 jobs) | all demos and probes PASS; random: 1,999 of 2,000 cases pass, case 1171 fails as at RTL; the directed SE0 test fails as at RTL | 24301440-24301448, 24301450 |
| Lockstep model (`test/model` at f9e0bf9, no RTL) on cases 0..40999, the probes and the directed SE0 cases, against `line_std_ref` with the RTL's readings | differs from `line_std_ref` on cases 1171, 31108 and 38972 and the directed SE0 cases only, as the RTL does; the same reading as the RTL on all 17 probes | 24301457..24301460 |
| Seeded defects (6 hand-edited copies of the core, below) | 6 of 6 detected | 24301451..24301456 |

An independent review re-ran the standalone tests (24303948), the RTL module
on cases 1100..1299 (24303949), 1,500 cases of a new seed (24303951, 0
failures), cases 31100..31109 (24303953), the six defect cores rebuilt with
identical hashes (24304009-24304013, 24304028, all detected) and the gate
level on the re-downloaded official netlist (24304029, 24304030), with the
results above.

In the 2,000-case RTL run (24301324) the random programs checked 4,735
engine programs and 14,208 line XFERs (NRZ: 2,792 drive, 1,698 sample, 2,332
drive and sample; NRZI: 2,778, 1,944 and 1,227; Manchester: 1,437 drive),
3,404,491 simulated cycles, 16,312 stuff cells, 641 XFERs with stuff errors,
260 SE0 ends, 450 lost arbitrations, 8,936 XFERs with a fractional ticker,
7,669 with the pair, and 349 programs ending with an invalid encoding
(fault 1). All 15 stuffing configurations (runs of 1s, run length 1 to 8;
either polarity, 2 to 8) occur at least 459 times. At gate level the same
2,000 cases take the same 3,404,491 cycles.

**Points the contract leaves open.** Each is a named field of
`line_std_ref.Interpretation`; `test_contract_open_points` runs one directed
program per point under each reading and requires the RTL to match
`RTL_READING`. The RTL and the lockstep model follow the same reading on
all of them:

| Point | Contract text | RTL and lockstep model | Classification |
|---|---|---|---|
| LSTAT[5] after sampling | "[5] line level" | only driving (and LCFG) sets it | contract ambiguity |
| NRZI previous sample after a drive-only XFER | "a 1 when it equals the previous sample" | only samples (and LCFG) set it | the reference's first reading counted the driven level; the RTL follows the text |
| Run counter after a wrong received stuff bit | not stated | either polarity: one bit of the received level; runs of 1s: no 1s | contract ambiguity |
| SE0 end with the pair clear | "both pair pins low when SE0 end is set" | needs the pair | contract ambiguity |
| Pair-pin fault rule in a sample-only XFER | "the data pin not owned when driving, or with the pair set a pair pin that is not owned or equals the data pin" | only when driving | contract ambiguity; it can also be read as a deviation from the literal text, like the next row (section 6.5 proposes a wording) |
| LCFG bit 3 with run length 1 and stuffing off | field table: "length 1 with [3] set is invalid"; invalid encodings: "stuffing on either polarity with run length 1" | no fault | the two statements differ; the RTL follows the second |
| Classic XFER that drives and samples with c bit 6 | "feeds the bits it shifts out, or the bits it samples" | the sampled bits | contract ambiguity |
| LCFG between a Manchester XFER's last boundary and its mid-bit tick | LCFG "resets the line state" | the second half is dropped | contract ambiguity |
| LTIM in the same interval | "the unit writes the bit itself at the next mid-bit tick" | written at T(1) of the restarted ticker | contract ambiguity; the RTL follows the literal text |

The probes also check these readings, on which the RTL and the model agree: a
line XFER issued in the cycle of a tick of the needed parity waits for the
next one; SE0 is read on the RX field's pin and the pair pin; a
drive-and-sample XFER counts stuffing runs on, and feeds the CRC with, the
received bits; the arbitration check applies in stuff cells; after a lost
arbitration the XFERs that follow also drive 1; LCFG's initial level does
not start a stuffing run. The readings are measured, not derived: the module
shows that the RTL follows them consistently, not that they are the intended
ones.

**Disagreement with the contract.** When SE0 appears in the cell of a due
trailing stuff bit, after all data bits of the XFER were received, the
contract gives LSTAT[13:8] = 0 ("[13:8] data bits remaining at SE0"; "stuff
bits are not shifted, counted or fed to the CRC"). The RTL, the gate-level
netlist and the lockstep model report 1: `engine.ml` keeps the trailing stuff
cell pending as `xremaining = 1` and latches that count into `line_remaining`
on SE0 (`test/model/line_unit.py` does the same with `line.rem`). The SE0 flag,
the pins and the received bits agree with the contract. Reproduction:
`test_se0_in_trailing_stuff_cell_leaves_no_data_bits` (NRZ, stuffing after
three 1s or after two equal bits, pair, SE0 end; LSTAT reads 0x171 against
0x071), and random cases 1171, 31108 and 38972 (LSTAT 0x175 against 0x075);
`line_std_ref` with `se0_left_counts_trailing_stuff=True` matches the RTL on
all of them. The formal harnesses reached the same result independently
(section 6.4, "Finding"; `formal/line_rx.sv` excludes the case from claim R3
and `formal/line_crc.sv` from claim C1). Classification: the RTL and the
lockstep model deviate from the contract text. The two ways to resolve it:

- RTL change: in `hardcaml/lib/engine.ml`'s SE0 branch latch 0 while the
  trailing stuff cell is pending (for example
  `l.l_rem <-- mux2 l.l_trail.value (zero 6) (select xremaining.value 5 0)`),
  with the matching change in `test/model/line_unit.py`
  (`line.rem = 0 if line.trail else t.remaining & 63`); then regenerate the
  core, drop the two formal exclusions and re-run the RTL, formal and
  official checks.
- Contract change: define the value as 1 in this case in isa.md's LSTAT row;
  then set `se0_left_counts_trailing_stuff=True` in `RTL_READING` and rename
  or invert the directed test.

The choice is open. Until it is made, `test_line_stdref` fails at RTL (the
directed test and random case 1171 of the default 2,000) and at gate level
(the directed test), and it is not in `test/Makefile`'s default module list.

**Seeded defects.** Fixed textual edits of copies of the published core (the
per-engine nets belong to engine 2, the only engine with failures; the CRC
constant tables are shared by all four engines). Each run stops after 5
failing random cases:

| Defect | Failing tests | First failing random cases | Job |
|---|---|---|---|
| Stuff bit after run length + 1 equal bits | random | 2, 3, 6, 15, 16 | 24301451 |
| Ticker fraction carry dropped | random | 0, 3, 4, 6, 8 | 24301452 |
| Preset 3 reflected constant 0x8408 → 0x8409 | random | 7, 13, 18, 20, 21 | 24301453 |
| Preset 1 reflected constant 0xA001 → 0xA003 | USB demo, random | 2, 10, 11, 15, 21 | 24301454 |
| After a lost arbitration the unit keeps driving its own bits | random | 2, 27, 42, 43, 145 | 24301455 |
| NRZI receive decode inverted | random | 15, 29, 30, 31, 41 | 24301456 |

The directed SE0 test fails on every core, the unmodified one included (see
above). The demos, the probes and the directed tests run on engine 0, so of
these defects they can only see the shared CRC constants; the USB demo sees
the preset 1 defect (its DATA1 CRC16), none of the demos uses preset 3 LSB
first. The random failures of the four per-engine defects are all on
engine 2.

**Not covered.** The reference's schedule (`test/stdref/program.py`) covers
the instructions the stdref programs and demos use; blocking PULL or PUSH,
events, ROUTE and host transfers while an engine runs are not modelled. The
random programs are straight-line, each engine on its own two pins, and the
classic XFERs in them sample only. By construction (a WAIT of 3P + 4 cycles
first) the random programs never issue LCFG or LTIM between a Manchester XFER's
last boundary and its mid-bit tick; only the two probes do. In the receive
scenario a sample taken early is detected only in the strobed engines (a late
one in all). The `relay` demo is not run. Gate-level runs are zero-delay (no
SDF). The first smoke run of an earlier module version (24212117) failed the
CAN demo with fault 72 at cycle 2016; that did not recur with the revised
module, and its cause was not established because that version was not kept.

Reproduction: `python3 -m pytest test/stdref -q`; in `test/`,
`make COCOTB_TEST_MODULES=test_line_stdref` (RTL) and
`make GATES=yes COCOTB_TEST_MODULES=test_line_stdref` (gate level, with the
netlist as `gate_level_netlist.v` or `GL_NETLIST=<netlist>`);
`PE_STDREF_FIRST`/`PE_STDREF_CASES` select random cases, `PE_LINE_GL_FULL=1`
runs the full demos at gate level. Job ids and the read log:
`<local work dir>/tt-work/ext-stdref/manifest.json` and `read-log.md`.


## 7. Area and timing

### 7.1 TT-replica synthesis

`docs/area-study/scripts/synth3.sh ll66` (the LibreLane 3.1.0.dev3 "AREA 0"
script with the Yosys 0.66 of the LibreLane image; typical liberty; SRAM
macros as black boxes), job 24122044. The `base` run reproduces the published
462,171.9 µm² exactly, and `diet8` the study's 375,223.3 µm².

| Core | TT synth, core (µm²) | Flops | Cells | With `src/project.v` (µm²) |
|---|---:|---:|---:|---:|
| `base` (design of record) | 462,171.9 | 3,921 | 32,056 | |
| `diet8` | 375,223.3 | 3,631 | 23,667 | 374,863.4 |
| `diet8_rec16` | 432,203.4 | 3,903 | 28,523 | 433,999.2 |
| difference | +56,980.1 | +272 | +4,856 | +59,135.7 |

The study's prototype gave 430,385.7 µm² for `diet8` + `REC16`; the 1.8K
difference is within the synthesis noise the study quotes (about ±5K).
The area study's placement model (placed = 1.100 S + 19.05 F; section 6 of the
study) predicts U = 55.6% and D = 50.6% at 8x4, against the design of record's
58.4% (58.5% routed).

### 7.2 LibreLane at 8x4

One full LibreLane run (job 24121613): the sweep harness snapshot
(`scripts/sweep/make_snapshot.py`) of the committed `src/config.json` with the
`diet8_rec16` core (`config_changes_vs_repo` is empty: 8x4, `fp8_spread_trk`
macro placement, `CLOCK_PERIOD` 15, density 61, hold margins 0.15/0.05,
`OPENROAD_THREADS` 4), LibreLane 3.1.0.dev3 from the image the `gds` action
uses, IHP-Open-PDK 2bbec755, tt-support-tools d66cf179e config merge, full
mode (SPICE extraction and LVS included; Magic DRC is off in the committed
config). The job body is a copy of `scripts/sweep/run_one.sh` that also keeps
the final views of runs that are not sweep candidates. The design of record's
official 15 ns build (results.md R85) is given for comparison.

| Metric | `diet8_rec16` (24121613) | Design of record, official (R85) |
|---|---|---|
| Flow | complete, exit 0, 10,040 s on 16 CPUs (detailed routing 6,147 s) | 4 h 24 min on the GitHub runner (7,833 s locally) |
| Utilization (`design__instance__utilization`) | 63.21% (standard cells 59.08%) | 65.30% |
| Instances | 90,735 (40,816 standard cells, 3,903 flops; 4,359 hold and 1,351 setup buffers) | |
| Setup WS at 15 ns, typ / fast / slow | +6.03 / +7.38 / +2.07 ns, 0 violations | +5.95 / +6.92 / +2.24 ns, 0 violations |
| Register-to-register setup WS, typ / fast / slow | +7.29 / +9.87 / +2.07 ns | |
| Hold WS, typ / fast / slow | +0.370 / +0.166 / +0.737 ns, 0 violations | min +0.165 ns (fast), 0 violations |
| fmax estimate, typ / slow (1000 / (15 − WS)) | 111.4 / 77.4 MHz | |
| Route DRC | 0 (29 iterations; 28,659 at iteration 0) | 0 |
| LVS | 0 errors | 0 |
| Antenna | **1 violating net** (`net12189` into a repair buffer: Metal2 cumulative area ratio 253.34 against 200); 25 diodes. Also in the official build (section 12.1); removed on the local mirror by the configuration change of section 7.2.1 | 0 |
| Max slew / cap / fan-out violations | 2 (slow) / 1 (fast) / 3 | |
| Magic illegal overlaps | 86 (the macro's, as in R18) | |
| Global-routing overflow | 0 | |
| Power (typ) | 21.2 mW | |

The core that was hardened is `d517e277…`, published as
`<local work dir>/tt-work/variants/cores/diet8_rec16.v` and listed in that
directory's `MANIFEST.sha256`, together with `diet8.v` (`5fb364f2…`), the area
reference of section 7.1. Precheck and gate-level
results on this layout are in section 7.3.

### 7.2.1 The antenna net and its fix (second round)

All runs: the local mirror of the `gds` action as above (LibreLane 3.1.0.dev3,
IHP-Open-PDK 2bbec755, tt-support-tools d66cf179e config merge, full mode,
`OPENROAD_THREADS` 4, 16 CPUs), branch `eval/diet8-rec16` at `f9e0bf9`
(`diet8_rec16` core `d517e277…`). Job ids and logs:
`<local work dir>/tt-work/ext-antenna/manifest.json`.

**Where the one violation comes from.** On the baseline layout (local job
24121613, byte-identical `metrics.csv` to the official `gds` run 36360490711)
the global-routing repair (`OpenROAD.RepairAntennas`, OpenROAD `dcf36133`
`repair_antennas` with LibreLane's defaults) found 58 violations on the global
routes and removed them with 21 jumpers (20 on 19 nets, then 1) and 57 diodes.
`net12189` (driven by the resizer buffer `wire12189`; its sinks are the
buffer input `wire12188/A` and `A_DIN[10]` of `core.instruction_sram_e3_hi`)
was not one of the 58. After detailed routing, `check_antennas`
reported 22 nets; LibreLane's post-routing loop (`drt.tcl`,
`DRT_ANTENNA_REPAIR_ITERS` 3) inserted 23, then 1, then 1 diode and re-routed
after each insertion. 21 nets were fixed in the first iteration; `net12189`
kept a Metal2 cumulative area ratio of 253.34 against 200 at `wire12188/A`
after all three, with three diodes on the net (`ANTENNA_75`, `ANTENNA_81`,
`ANTENNA_82` in the final netlist). The required ratio stayed 200, which the
technology LEF's `ANTENNACUMDIFFAREARATIO` table gives only for less than
0.16 µm² of connected diffusion; each diode pin has 2.0154 µm² and the driving
`buf_2` output 0.7068 µm², so the checker counted neither the diodes nor the
driver on that Metal2 piece (inferred from the reported required ratio and the
LEF table; not traced in the layout, whose final database was not kept).

**Candidates.** Each run changes only the listed keys of the branch's
`src/config.json`; everything else is the committed configuration (the
baseline snapshot rebuilt from the branch has the same snapshot hash,
`0df0f4bf…`, as job 24121613). Variable names and defaults are those of the
LibreLane 3.1.0.dev3 image.

| Change | Job | Result |
|---|---|---|
| `GRT_ANTENNA_REPAIR_MARGIN` 15, 20, 30, 50 (default 10) | 24296550, 24296552, 24296553, 24296554 | Same layout as the baseline: `nl.v` and `metrics.csv` byte-identical to 24121613, one antenna net (`net12189`, 253.34). The repair ran with the requested `-ratio_margin` (15, 20, 30 or 50), found the same 58 violations and inserted the same 21 jumpers and 57 diodes: in this OpenROAD version the margin does not change the repair of this design. |
| `GRT_ANTENNA_REPAIR_DIODE_ONLY` true (default false) | 24296555 | **Antenna 0**, all sign-off checks below pass; applied to the branch. |
| `RUN_HEURISTIC_DIODE_INSERTION` true, `HEURISTIC_ANTENNA_THRESHOLD` 200 | 24296556 | Flow error at `Odb.FuzzyDiodePlacement` (2,036 s): the PDK sets `DIODE_CELL` to `sg13cmos5l_antennanp` without a pin name and the step splits it at "/". |
| as above, plus `DIODE_CELL` `sg13cmos5l_antennanp/A` | 24301786 | Antenna 0, route DRC 0, LVS 0, but rejected: `FuzzyDiodePlacement` reports 8,916 diodes; the layout has 10,102 antenna cells after the heuristic step and 10,108 at the end (55,019.9 µm²); utilization 67.73%, slow-corner setup WS +1.844 ns (below the +1.87 ns bound set for this change), 861 max-fan-out and 10 max-cap violations. |

`DRT_ANTENNA_REPAIR_MARGIN` was not tried: the `DetailedRouting` step declares
it, but `drt.tcl` in the image passes `GRT_ANTENNA_REPAIR_MARGIN` to the
post-routing `repair_antennas`.

**Change applied to the branch.** The branch's `src/config.json` sets
`"GRT_ANTENNA_REPAIR_DIODE_ONLY": true` (one key plus its comment; every other
key unchanged). A snapshot of the edited file gives the same merged LibreLane
configuration as job 24296555. `tools/opt/test_opt.py` passes (36 tests, 1
skipped, the same skip as before the change: `switch.py` refuses a checkout
whose `src/` already holds a variant core); `macros/check_macro_floorplan.py`
passes. `variants6x4/switch.py` builds the 6x4 configuration from
`src/config.json` plus an overlay that does not name this key, so a 6x4 build
from a tree with this `src/config.json` would inherit it unless the overlay
sets it to `null`; the repository tests do not check this key.

| Metric | Baseline layout (24121613 = official 36360490711) | `GRT_ANTENNA_REPAIR_DIODE_ONLY` (24296555) |
|---|---|---|
| Antenna | 1 net (`net12189`, Metal2 253.34 against 200) | **0** |
| Global-routing repair | 58 violations; 21 jumpers, 57 diodes | 58 violations; 79 diodes (78, then 1), no jumpers |
| Post-routing repair | 22 nets; 23 + 1 + 1 diodes; 1 net left after 3 iterations | 20 nets; 20 + 1 diodes; 0 left after 2 iterations |
| Antenna cells (area) | 82 (446.34 µm²) | 100 (544.32 µm²) |
| Route DRC | 0 (first routing pass clean at iteration 29) | 0 (first routing pass clean at iteration 11) |
| LVS | 0 errors | 0 errors |
| Setup WS at 15 ns, typ / fast / slow | +6.03 / +7.38 / +2.07 ns, 0 violations | +6.08 / +7.38 / +2.21 ns, 0 violations |
| Hold WS, typ / fast / slow | +0.370 / +0.166 / +0.737 ns, 0 violations | +0.370 / +0.166 / +0.738 ns, 0 violations |
| fmax estimate, typ / slow (1000 / (15 − WS)) | 111.4 / 77.4 MHz | 112.1 / 78.2 MHz |
| Utilization (standard cells) | 63.21% (59.08%) | 63.22% (59.09%) |
| Standard cells | 40,816 | 40,834 (the 18 additional antenna cells; every other standard-cell class has the same count; 7 fewer fill cells) |
| Hold / setup buffers | 4,359 / 1,351 | 4,359 / 1,351 |
| Max slew / cap / fan-out violations | 2 (slow) / 1 (fast) / 3 | 2 (slow) / 2 (fast) / 4 |
| Magic illegal overlaps | 86 | 86 |
| Power (typ) | 21.2 mW | 21.2 mW |
| Detailed-routing step | 3,604 to 3,750 s (jobs 24296550 to 24296554, same layout, other nodes of the same session) | 2,436 s |

Placement, clock-tree synthesis and the post-CTS resizer run before the
antenna repair, so the two layouts have the same cells up to the antenna
cells; the timing differences come from the routing and from the placement of
the antenna cells. The slow-corner setup WS is 0.14 ns higher than the
baseline's. One more fast-corner max-cap violation and one more max-fan-out
violation are reported; the flow reports these checks without failing on
them, as for the baseline and the official run. The routing times were
measured on different nodes and do not predict the time of the GitHub `gds`
job.

**Checks on the layout of job 24296555.**

| Check | Result | Job |
|---|---|---|
| Tiny Tapeout precheck (tt-support-tools d66cf179e `precheck.py`, unmodified, on the final GDS with the branch's `info.yaml`; a copy of `drc-triage/scripts/precheck.sbatch`, KLayout 0.30.9 from the LibreLane image) | 9/9 checks pass; exit 0 after 414 s | 24309378 |
| Gate-level cocotb, committed test tree of `f9e0bf9`, `make GATES=yes` (default variant `diet8_rec16`) on the final `nl.v` (cocotb 2.0.1, Icarus 13, PDK 2bbec755 cell models) | 127 tests: 66 pass, 61 skipped by design at gate level, 0 fail (the counts of the official `gl_test` of run 36360490711); 458 s | 24307910 |
| The same with `PE_LINE_GL_FULL=1` | 127 tests: 71 pass, 56 skipped, 0 fail; 1,093 s | 24307911 |
| The integrated test tree (160 tests, section 10.1), `gl_test` commands on the final `nl.v` | 160 tests: 66 pass, 94 skipped, 0 fail | 24376083 |
| RTL-to-netlist sequential equivalence (`formal_eq/eq_check.py check --run-dir <run> --variant diet8_rec16 --core <diet8_rec16 core> --top src/project.v --selftest`, `formal_eq/` of `f9e0bf9` unmodified) | **equivalent**; the netlist mutants `mutA` and `mutC` are not equivalent and the recipe controls `c1` to `c9` meet their expectations (`selftest.pass`); 299 s | 24306936 |

Three earlier check jobs were harness errors and were rerun: 24306933 (the
KLayout container wrapper mounted the output directory read-only), 24306934
and 24306935 (the test snapshot lacked `firmware/`).

**Status.** These are local-mirror results. The mirror reproduced the
official run of the unchanged configuration byte for byte (`metrics.csv` of
36360490711 and 24121613), but the official `gds`, `precheck` and `gl_test`
actions have not yet run on a commit with this key. The fix rests on one
deterministic layout; a later RTL or configuration change can produce a new
antenna net.


### 7.3 Precheck and gate level on the hardened layout

| Check | Result | Job |
|---|---|---|
| Tiny Tapeout precheck (tt-support-tools d66cf179e `precheck.py`, unmodified, on the final GDS and LEF with the repository's `info.yaml`; `drc-triage/scripts/precheck.sbatch`, KLayout 0.30.9 from the LibreLane image instead of the action's 0.30.4) | 9/9 checks pass (pin label overlap, SG13CMOS5L DRC, zero area, KLayout checks, pin check, boundary, layer, cell name, analog pin); exit 0 after 333 s | 24131046 |
| Gate-level cocotb on the final `nl.v` (`make GATES=yes PE_VARIANT=diet8_rec16`, cocotb 2.0.1, Icarus 13, the PDK 2bbec755 cell models) | Test tree before the review additions: 125 tests, 64 pass, 61 skipped by design at gate level, 0 fail. Of the 23 line-unit tests 18 pass and the 5 CAN demos are skipped | 24131047 |
| The line-unit modules at gate level with `PE_LINE_GL_FULL=1` (CAN demos and the 10BASE-T link pulse included) | 23/23 pass (10BASE-T frame and link pulse after 660,000 cycles, all CAN scenarios and negative controls, USB, random), 871 s | 24131401 |
| The whole suite at gate level on the final test tree with `PE_LINE_GL_FULL=1` (127 tests, including the ACK and USB checks added after review) | 127 tests: 71 pass, 56 skipped by design at gate level (all in the default modules), 0 fail; all 25 line-unit tests pass; 992 s | 24134621 |
| RTL-to-netlist sequential equivalence (`formal_eq/eq_check.py check --run-dir <run> --variant diet8_rec16 --core <diet8_rec16 core> --top src/project.v --selftest`; Yosys 0.67 and ABC `dprove`; `formal_eq/` unmodified at `75ac8af`) of the final `nl.v` against the `diet8_rec16` RTL behind `src/project.v` | **equivalent**; the two netlist mutants are found not equivalent and every recipe control meets its expectation (`selftest.pass`); 153 s | 24134508 |

The second round's gate-level runs (the antenna-fix layout, the
standards-based reference, the random campaign and the integrated test tree)
are in sections 7.2.1, 6.6, 6.5 and 10.1.

## 8. What is not verified

State after the second round (2026-09-29). The bullets that this list
replaced are at its end.

- **No silicon, no FPGA, no bench.** Electrical requirements of the stretch
  protocols (USB-LS edge rates, 10BASE-T magnetics and levels, CAN transceiver)
  are not addressed (study R7).
- **The references are ours.** No third-party protocol peer checks the line
  unit (the design of record has them, [independent-peers.md](independent-peers.md)).
  The CRC check values are published values. `test/model/line_std_ref.py`
  (section 6.6) follows public standards and was written without reading the
  RTL or the lockstep model, but by this project, and some of its readings of
  open points were added after the RTL ran. The formal reference models are
  written from [isa.md](isa.md) by this project (section 6.4). The line
  encoders, the CAN node, the USB host and the Ethernet decoder of
  `test/line_support.py` are behavioural models written for these tests, not
  conformance testers or real controllers. CAN was not tested against another
  CAN controller, with clock offsets, error frames or resynchronization
  (study R6).
- **Formal scope.** The line-unit properties are engine-level and, except
  `line_tick`, `line_pins`, `line_reset` and `line_decode`, run fixed
  programs with free operands. Not in formal: LTIM delays in the two-engine
  codec property, the receiver side of drive-and-sample XFERs beyond the
  arbitration monitor and the CRC, the classic XFER's CRC feed, stuffing with
  Manchester in the codec property, a bus other than a direct wire. The
  reference models are written from [isa.md](isa.md) by the same project, not
  taken from an external source, and `line_arb` uses the engine's own cell
  bit (section 6.4). LSTAT's remaining-bits count after SE0 in a trailing
  stuff cell differs from [isa.md](isa.md) (sections 6.4, 6.6). The timing
  certificates ([timing-certificates.md](timing-certificates.md)) and the
  `formal_depth/` runs ([formal-depth.md](formal-depth.md)) are for the
  design of record's RTL; they were not run on `diet8_rec16`.
- **Random stimulus against the model.** The random campaign (section 6.5)
  is checked against the lockstep model, which is not independent of the RTL;
  its parts that do not depend on the model are the decode check and the
  negative controls. The random programs of section 6.6 (41,000 cases at RTL)
  are the part checked against an independent reference. The in-tree CI
  generator (`test_line_random`) reaches 245 of the 357 line-unit bins.
- **Mutation testing** covers the line unit's state logic and its first output
  statements (a sample of 3,000 of 90,372 mutations, and a held-out sample of
  1,000) and the rest of the logic that the option adds or changes: the decode
  of opcodes 30-33, the encoding checks, the next-value selections they feed
  and the logic tied to line-unit state (2,117 of 63,773). It does not cover
  the 1,260 changed statements that read line-unit state beyond the first
  statement of their path (268,232 mutations, more than the 154,145 of both
  populations), nor the flip-flops of other registers (section 11.1).
  Survivors neither proven nor argued: 76, 26 and 0 (section 11.4); the
  argued survivors (125, 44 and 246) stay in the denominators, and the second
  sample's 246 rest on one property of the blocked-cycle count that no formal
  method here proves.
- **One deviation from the contract, open.** LSTAT[13:8] after SE0 in the
  cell of a due trailing stuff bit reads 1, the contract gives 0 (section
  6.6). `test_line_stdref` is not in the default module list until it is
  resolved.
- **Contract wording.** Nine points that [isa.md](isa.md) leaves open are
  measured, not specified (section 6.6). Two wordings can be read against the
  RTL: the pair-pin fault rule for sample-only line XFERs and the LCFG table's
  "length 1 with [3] set is invalid" (section 6.5 proposes wordings; isa.md is
  unchanged).
- **USB firmware scope.** `usb-ls-in-responder` answers an IN token to any
  address and endpoint (tested); it ignores tokens with a wrong CRC5 and non-IN
  PIDs (tested with OUT and SETUP). Handshakes, data toggles and every other
  USB transaction type are outside it.
- **CAN firmware scope.** Data frames only: no remote, error or overload
  frames, no retransmission after arbitration loss, no resynchronization, no
  error counters, a fixed 50% sample point (study R6).
- **Host library.** `host/` compares only READ_SELECT 7 bits 7..0 with an ISA
  version, decodes bits 23..8 (`protocol.Capabilities`) and knows two devices,
  `base` (word `0x00000002`) and `diet8_rec16` (`0x000F5F03`, no debug
  counters). An image needs the capabilities its words use (line-unit
  opcodes 30-33, XFER c bits 5 and 6, and the fraction, stuffing, arbitration
  and preset fields), plus any it lists in an optional `"requires"` array;
  `load_image()` raises `CapabilityMismatch` on a device, or an engine,
  without them, before writing anything. Images keep `isa_version` 2. The
  assembler does not write `"requires"`; the loader derives the requirement
  from the words. The self-test checks the whole READ_SELECT 7 word against
  the device profile and, on a device with the unit, runs a CRC/LTIM/LSTAT
  probe twice on every engine the capability bits name (on one without it,
  LSTAT must fault with code 1); in the RTL lockstep replay (`make
  rtl-cocotb`) it runs 77 checks on the `base` core and 93 on `diet8_rec16`
  (24312940; 93 again in 24376085). Checked on the model, the RTL and
  MicroPython replays (89 tests; 24309463, 24376085); not run on silicon or
  an FPGA.
- **Timing knobs.** The option is rejected together with
  `split_instruction_decode`; the other timing knobs were not combined with it.
- **One variant.** Only `diet8_rec16` was generated and verified; the study's
  6x4 fallback (`diet2` + `REC16`) and other combinations were not.
- **Official build on a branch, not on main.** The layout, precheck and
  gate-level results were first obtained with the local mirror of the Tiny
  Tapeout flow, then confirmed by the official actions on branch
  `eval/diet8-rec16` (section 12.1). The variant is not the design of record,
  so no official build of it exists on `main`. The official build has one
  antenna violation (`antenna__violating__nets` 1); the design of record's
  official build has none. The configuration change of section 7.2.1 removes
  it on the local mirror; no official run with it exists yet, and the random
  gate-level cases of section 6.5 used the netlist of the official build
  without it.
- **Workflow changes not run on GitHub.** The design selection and the
  changed `regen`, `test`, `formal` and `equiv` workflows (section 12.2) were
  checked by local emulation of their `run:` steps only (sections 12.3,
  10.1). Run times of the formal CI subset on a hosted runner are not known.

Replaced bullets of the 2026-09-27 version of this list (superseded):
"Formal bounds and exclusions" (the coder/decoder and end-to-end CRC
properties bounded, section 6.4 "First round"; now mostly unbounded);
"Mutation testing of the unit with the cocotb suite was not run (section 11)"
(now run, section 11); "Host library. `host/` does not know the capability
bits ..." (it does now); and, in "Official build on a branch", "The one
antenna violation of section 7.2 was not repaired" (repaired on the local
mirror, section 7.2.1).


## 9. Open decision

Adopt `diet8_rec16` as the design of record, or keep the design of record and
the variant as it is. Due 2026-11-06 (the study's go/no-go date). Section 13
compares the verification evidence of the two. Adopting it means:

- the diet options come with it: asynchronous reset with synchronous release,
  no debug counters, a 7-bit PC, byte-lane shifts, ISA version 3
  ([variants.md](variants.md) section 4);
- `src/protocol_emulator_core.v` becomes the variant core, and
  `configs/design-selection.txt` names `configs/variants/diet8_rec16.json`
  (section 12.2); the workflows and `test/Makefile` follow it, the items listed
  in section 12.2 under "What adoption changes" do not;
- the host library already handles the capability bits (section 8); its
  default device is `base`, fixed in `host/pe_host/host.py` and independent of
  the selection file, so a host for the adopted design passes
  `device="diet8_rec16"` or the default is changed with the adoption;
- the timing and routing margin of section 7.2 applies; with the antenna
  change of section 7.2.1, `src/config.json` carries
  `GRT_ANTENNA_REPAIR_DIODE_ONLY`, which the 6x4 build would inherit unless
  its overlay sets the key to `null`;
- the LSTAT[13:8] point of section 6.6 is open: an RTL change (with the model,
  regeneration and a re-run of the RTL, formal and official checks) or an
  isa.md change; `test_line_stdref` joins the default module list once either
  is made.

Before this round, the list also said "the host library needs the
capability-bit check (section 8)" (done since) and "`src/protocol_emulator_core.v`
becomes the variant core, and CI must select the variant (section 12)" (the
selection is now one file, section 12.2).

The study's other open item, R1 (the host cannot service queues while the chip
clock free-runs), still limits every demo to what the queues and relay engines
hold per session.

Independently of the decision, the mutation campaign found a test gap of the
design of record: four `cnot1` mutations of WAITEVENT's blocked-cycle count
stage pass all 102 tests of the design of record's suite and are killed by
`test_line_spec_limit_bits`, which does not use the line unit (section 11.4,
job 24370776).


## 10. Reproduction and jobs

Work directory of the first round: `<local work dir>/tt-work/ext-rec16/` (not in the repository),
with `manifest.json` (every Slurm job), `jobs/` (job scripts), `snap/` and
`fsnap/` (the snapshots the jobs ran on, with `SOURCES.sha256`), `results/`
(cocotb logs and `results.xml`), `formal/` (formal RTL and per-job logs),
`synth/`, `sweep/` and `harness/` (the LibreLane run), `axle/` (the
arithmetic checks below). The second round's work directories are
`<local work dir>/tt-work/ext-formal/`, `ext-random/`, `ext-stdref/`,
`ext-mutation/`, `ext-antenna/`, `ext-ci/` and `ext-integrate/`, each with a
`manifest.json` of its jobs, an `axle/` directory and the independent review's
`verify-*/` directory.

**Arithmetic.** The numbers derived in this page (register and state widths,
the version word, the ticker examples, area and flop differences, the
placement-model percentages, run totals, fmax estimates, the runtime estimate,
the antenna ratio) and the nine CRC check values (with a Lean model of the
parametrised CRC) were checked with the AXLE Lean engine (lean-4.28.0): the
independent verifier's `axle/ext_arith.lean` (37 theorems) and the author's
`axle/ext_arith_author_part1.lean` to `part3.lean`, each `okay: true`
(check records `*.check.json` next to them). The second round's numbers are
checked by each work directory's `axle/` files (all `okay: true`): ext-formal
3 files, ext-random 88 theorems, ext-stdref 3 files, ext-mutation 212
theorems, ext-antenna 3 files, ext-ci 48 theorems, and ext-integrate's
`axle/` for the sums and counts first stated in sections 1, 6.4, 6.6, 7.2.1,
10.1, 11.3 and 13.

Second round:

```sh
cd test && make                                               # the design selection (diet8_rec16): 160 tests
make COCOTB_TEST_MODULES=test_line_stdref                     # section 6.6; fails on the LSTAT[13:8] point
cd .. && python3 -m pytest test/stdref -q                     # 159 standalone reference tests
SBY_TIMEOUT=10800 formal/run.sh --variant diet8_rec16         # 62 jobs; line_codec_bmc needs > 1,500 s
formal/run.sh --variant diet8_rec16 --ci --parallel 4         # the 42-job line-unit CI subset
```

The random campaign's commands are in `campaigns/random/README.md`
("Line-unit campaigns"), the mutation campaign's in
`campaigns/mutation/README.md` ("Line unit (diet8_rec16)").

First round:

```sh
scripts/gen_variants.sh diet8 diet8_rec16                    # cores, firmware, checks 1-4
cd test && make PE_VARIANT=diet8_rec16                        # 127 tests (first round's module list)
make PE_VARIANT=diet8_rec16 COCOTB_TEST_MODULES=test_line_random PE_SEED=0x11AE0000 PE_LINE_RANDOM_ITERS=16
SBY_TIMEOUT=3600 formal/run.sh --variant diet8_rec16          # 31 jobs; line_codec_bmc needs > 1,500 s
dune build ./test/line_test.exe ./test/line_sys_test.exe      # in hardcaml/
```

| Job | Purpose | Result |
|---|---|---|
| 24119789 | `line_test.exe` | 31/31 PASS |
| 24120021 | `line_test.exe` with 10 seeded defects | 7 detected |
| 24120022 | `line_sys_test.exe`, `diet8_rec16` | 16/16 PASS |
| 24121510 | cocotb line-unit modules, snapshot s1 | 22/23 (test helper bug, fixed) |
| 24121511, 24121512 | default suite, s1 and git `HEAD` control | 102/102 each, identical |
| 24121513 | full suite, `diet8_rec16`, s1 | 101/102 (operand-sweep expectation, adapted) |
| 24121514 | full suite, `diet8` | 102/102 |
| 24121515 | random campaign, 256 cases | 0 mismatches |
| 24121900, 24125084 | formal RTL generation (snapshots f1, f2) and design-of-record comparison | identical |
| 24122038 | formal, snapshot f1: 30 variant jobs and 12 design-of-record quick jobs | superseded by 24125085 (two deep jobs cancelled there, see 24122280-24122285) |
| 24122280-24122285 | formal solver comparison for the deep line jobs (yices, bitwuzla) | chosen: yices for `line_codec`, bitwuzla for `line_crc_e2e` |
| 24125085 | formal, snapshot f2: 31 variant jobs and 12 design-of-record quick jobs | section 6.4 (first round); superseded by 24307353 |
| 24125398 | random campaign 2 | 0 mismatches |
| 24122044 | TT-replica synthesis | section 7.1 |
| 24122074 | full suite, `diet8_rec16`, s2 | 125/125 |
| 24122075 | default suite, s2 | 102/102, identical to the control |
| 24122140 | `dune test` in `hardcaml/` | PASS |
| 24121613 | LibreLane full flow at 8x4 | section 7.2 |
| 24131046 | Tiny Tapeout precheck on its GDS/LEF | 9/9 |
| 24131047 | gate-level suite on its netlist | 125 tests: 64 pass, 61 skip, 0 fail |
| 24131401 | gate-level line-unit modules with `PE_LINE_GL_FULL=1` | 23/23 |
| 24134508 | RTL-to-netlist equivalence (`formal_eq`) of the netlist of 24121613 | equivalent, controls met |
| 24134619 | full suite, `diet8_rec16`, s4 (after review: CAN ACK check, USB rejection/EOP and address tests) | 127/127 |
| 24134620 | default suite, s4 | 102/102, identical to the control |
| 24134621 | gate level, s4, `PE_LINE_GL_FULL=1` | 127 tests: 71 pass, 56 skip, 0 fail |
| **Second round** | | |
| 24307337 | formal RTL generation, snapshot s1 (core equal to `src/` below the header; 18 files identical to f2) | OK |
| 24307353 | formal, snapshot s1: all 62 `diet8_rec16` jobs (16 existing, 46 line-unit) | 62/62 met |
| 24307354, 24309392 | formal CI subset, `--ci --parallel 4` on 4-CPU allocations (snapshots s1, s2) | 42/42 met in each; 526 s, 411 s |
| 24307355 | design of record with the s1 `run.sh`: `--list` (16 jobs), RTL generation (7 of 7 files identical to the git `HEAD` generation of the previous round), the 12 quick jobs | 16 jobs listed; 12/12 met |
| 24311313, 24311615, 24311894, 24312184, 24312831, 24312860, 24312498, 24313629, 24311910 | formal, independent review: RTL generation, all 62 jobs, assumption-removal and defect runs, the R3 exclusion removed, CI subset timing, the design of record with the new `run.sh` (16 jobs) | same outcomes as 24307353; R3 fails without the exclusion; CI subset 695 s and 525 s (4 hardware threads); 16/16 |
| 24210411, 24210470 | random campaign builds: RTL and GL simulations (Icarus 13), two RTL copies with a seeded defect | built |
| 24210822 | fidelity: upstream `test_random` seed 1 on `diet8_rec16`; `test_line_random` with the line-unit bins | 64/64 per-case cycles equal; PASS |
| 24210655, 24210657, 24210659, 24210660, 24210666-24210669, 24296483, 24296493, 24296494, 24296548, 24296549, 24296958-24296961 | random lockstep campaign, 17 campaigns (section 6.5) | 724,480/724,480 cases |
| 24210661-24210664, 24295697 | random campaign negative controls | detected (section 6.5) |
| 24297123 | line-unit bins of the in-tree generator, model only | 245/357 |
| 24307854 | merge and collect (`report_job.sh`, `collect.py`) | `campaigns/random/results/diet8_rec16/` |
| 24211642, 24212379, 24307853 | campaign scripts on the committed base-campaign results | output identical to the f9e0bf9 scripts |
| 24310062, 24310137, 24310196, 24310328 | random campaign, independent review: totals from the raw result files, bin consistency and decode-check control, rebuilds, 19 seed re-runs | totals and per-case results identical |
| 24301323 | `pytest test/stdref` | 159/159 |
| 24301324 | `test_line_stdref`, RTL, cases 0..1999 | 7/9 (LSTAT[13:8] point) |
| 24301325, 24301327-24301334, 24301337, 24301339-24301344, 24301389-24301396, 24301435 | `test_line_stdref` RTL campaign, cases 2000..40999 (25 jobs) | 2 of 39,000 fail, same point |
| 24301439; 24301440-24301448, 24301450 | `test_line_stdref`, gate level (official netlist), default mode and full mode with cases 0..1999 | default: 7 PASS, 1 SKIP (CAN demo), 1 FAIL (directed SE0 test); full: the directed SE0 test and case 1171 fail as at RTL, all else PASS |
| 24301451-24301456 | `test_line_stdref`, 6 seeded defects | 6/6 detected |
| 24301457-24301460 | lockstep model against `line_std_ref`, cases 0..40999 | differs on the same cases as the RTL |
| 24303948, 24303949, 24303951, 24303953, 24304009-24304013, 24304028, 24304029, 24304030 | `test_line_stdref`, independent review | same results |
| 24296550, 24296552, 24296553, 24296554 | LibreLane, `GRT_ANTENNA_REPAIR_MARGIN` 15/20/30/50 | layout identical to the baseline, antenna 1 |
| 24296555 | LibreLane, `GRT_ANTENNA_REPAIR_DIODE_ONLY` | antenna 0; section 7.2.1 |
| 24296556, 24301786 | LibreLane, heuristic diode insertion | flow error; antenna 0 but rejected (timing, 8,916 diodes) |
| 24309378 | precheck on the 24296555 GDS | 9/9 |
| 24307910, 24307911 | gate level on the 24296555 netlist, default and `PE_LINE_GL_FULL=1` | 127: 66/61/0 and 71/56/0 (pass/skip/fail) |
| 24306936 | `formal_eq --selftest` on the 24296555 netlist | equivalent, self-test passed |
| 24300291, 24309190 | `regen.yaml` emulated (branch; `main` 743d534 with the merged file) | no difference in `src/` |
| 24308326 | `test/Makefile` variables, 14 cases, and copied test trees | as intended (section 12.3) |
| 24308920 | `formal.yaml` job selection | 58 of 62 on the branch; 16 on `main` |
| 24309079, 24300249, 24300555 | `equiv.yaml` select and check jobs | section 12.3 |
| 24309230 | `run_mutant.py` control mutant from packed trees | section 12.3 |
| 24309463 | `test.yaml`, `gl_test` commands, `host/` | 102/102 (`main`), 127/127 (branch), 107/107 (743d534); 3/3 gl; host 89/89 |
| 24309631 | `formal.yaml` emulated in full | 58/58 (branch), 16/16 (`main`) |
| 24312940, 24312942 | workflows, independent review (on `main` f8142fc and the branch) | regen, formal selection, 10 sby jobs, host, test samples, gl commands as expected |
| section 11.5 | mutation campaign | section 11 |
| 24372386, 24372387 | mutation campaign, independent review: 18 drawn mutants, `limit_bits` and the design-of-record analogues, four extra `cnot1` mutants | every outcome as recorded |
| 24376082-24376085 | integration checks of the branch tree, section 10.1 | section 10.1 |

### 10.1 Integration checks (2026-09-29)

The worktree of branch `eval/diet8-rec16` (`f9e0bf9` plus all uncommitted
files of this round, including the `test/Makefile` module list) was packed as
snapshot i1 (798 files, `SOURCES.sha256` in
`<local work dir>/tt-work/ext-integrate/snap/`) and unpacked on node-local
storage for each job. The workflow jobs ran with the emulator of section 12.3
(`run:` steps verbatim; tool installation replaced by Icarus 13.0, cocotb
2.0.1, OSS CAD Suite 2026-07-29 and the pinned OCaml switch). No file other
than `docs/extension.md` changed after the snapshot.

| Check | Result | Job |
|---|---|---|
| `test.yaml` emulated: job `test` (design selection `diet8_rec16`, `PE_CORE=src/...`; `make` with the default module list) and job `lint` (`scripts/lint.sh`) | success; 160 tests in 37 modules, 160 pass, 0 fail, 0 skip (441 s); lint success | 24376082 |
| `test_line_stdref` at RTL (`make COCOTB_TEST_MODULES=test_line_stdref`, defaults: random cases 0..1999) | 7 of 9 tests pass; the directed SE0 test (0x171 against 0x071) and random case 1171 (0x175 against 0x075) fail on the LSTAT[13:8] point, as in 24301324 | 24376082 |
| `python3 -m pytest test/stdref -q` | 159 passed | 24376082 |
| `formal.yaml` emulated in full: job `rtl` (design selection, generation with `VARIANT_CORES`, job selection) and every matrix entry (`SBY_TIMEOUT` 1500, 20 entries at a time) | success; the generated core equals `src/` below its header line; 58 of 62 jobs selected (the 4 long bounded checks left out); 58 of 58 meet their expectations (37 PASS, 21 negative controls FAIL as required); longest `line_codec_prove` 354 s; 387 s wall | 24376084 |
| `regen.yaml` emulated (`scripts/generate.sh configs/variants/diet8_rec16.json`, then `git diff --exit-code -- src`) | success, no difference in `src/` | 24376085 |
| `host/`: `python3 -m unittest discover -s tests` (with the base core generated for the replays on the other device), `make upy-check`, `make mpy`, `make rtl-cocotb` (device from the selection: `diet8_rec16`) | 89 tests OK, none skipped; upy-check, mpy rc 0; rtl-cocotb 2 of 2 (self-test 93 checks) | 24376085 |
| `gl_test` commands (netlist copied to `test/gate_level_netlist.v`; `make clean; GATES=yes make`; no `PE_VARIANT` or `PE_CORE`), official netlist of run 36360490711 (sha256 `e2404100…`) | 160 tests: 66 pass, 94 skipped, 0 fail (the 61 skips of the official run and the 33 `test_line_spec_*` tests) | 24376083 |
| The same on the antenna-fix netlist of 24296555 (sha256 `a2f2fe7d…`) | 160 tests: 66 pass, 94 skipped, 0 fail | 24376083 |
| The same on the official netlist with `PE_LINE_GL_FULL=1` | 160 tests: 102 pass, 58 skipped, 0 fail (2,170 s); all 25 line-unit tests and 31 of the 33 `test_line_spec_*` tests pass (the 2 tests of `test_line_spec_limit_bits` use the time warp and are skipped at gate level); the first gate-level run of these modules | 24376083 |
| Python 3.11 (the `test.yaml` version) syntax check of every Python file of the tree; imports of `test/` and `host/` outside the standard library | 239 files compile; only `cocotb`, `pytest` (both in `test/requirements.txt`), `pe_host` and MicroPython's `machine` | login node |
| Public-repository hygiene grep (`git ls-files -co --exclude-standard` piped to `xargs grep -nIE` for local paths and the user name) | no output | login node |


## 11. Mutation testing

A mutation campaign over the line unit, with the method of
[mutation-push.md](mutation-push.md) (Yosys `mutate`, the cocotb suite on every
mutant, the same formal classification), on three uniform samples: one of the
line unit's state logic, one of the rest of the logic that the option adds or
changes, and a held-out sample of the first population that no test was
written from. Scripts and commands:
[`campaigns/mutation/README.md`](../campaigns/mutation/README.md), "Line unit
(diet8_rec16)"; parameters: `campaigns/mutation/campaign-line-diet8_rec16.env`;
results: [`campaigns/mutation/results/diet8_rec16/`](../campaigns/mutation/results/diet8_rec16/)
(files without a suffix: first sample; `-ext`: second sample; `-held`:
held-out sample). "Round 1" to "round 3" in this section are the campaign's
own iterations (tests written from survivors, then re-measured); all of them
belong to the second round of this page.

The first version of this section (2026-09-27, "Mutation testing (not run)")
listed what such a campaign would need: the driver pointed at the
`diet8_rec16` core, a mutant generator restricted to the line-unit logic, the
line-unit test modules and a survivor classification for the new logic. It is
superseded by 11.1 to 11.5.

### 11.1 Setup

| Item | Value |
|---|---|
| Core | `src/protocol_emulator_core.v` of branch `eval/diet8-rec16` at `f9e0bf9` (a `git archive`), the published `diet8_rec16` core, sha256 `d517e277…`; `scripts/gen_variants.sh` in that snapshot regenerates it byte for byte and gives the `diet8` core `5fb364f2…` (job 24295723) |
| First population: the unit's state logic | `line_region.py`, anchored on the registers: the 19 line-unit registers of `Engine.line_register_names` (76 in the core) added to `region_map.py`'s categories. The statements whose nearest named state is a line-unit register (1,731 of 9,187; `line/state`) and the first cell-producing statements on every path out of them (260; `line/out-*`, into the pins, the data registers, engine control, ...): 1,991 statements, 1,304 of the 5,387 cells after `prep`, 90,372 mutations. 104 of the 1,991 also occur in `diet8` (64 bit selects and concatenations, 20 compares, 16 other, 4 constants): they tie to a line-unit register, and some are classic-XFER logic that the unit shares, such as the tick compare `_2888 < transfer_tick` and XORs of `transfer_mode` bits; 21 of the 3,000 sampled mutants are in them |
| Second population: the option's other logic | Compared statement by statement with `diet8`, 3,912 statements are added or changed by the option (a change propagates to every statement with a changed input); 1,887 of them are in the first population. Of the other 2,025, the second population (`sel/ext.txt`) takes the 556 combinational statements with no line-unit register in their same-cycle fan-in (`ext/no-line-input`: the decode of opcodes 30-33, i.e. the 72 compares with 30 to 33 that the first population does not hold (it holds the other 68); the encoding checks, such as the LCFG reserved-bits field; and the next-value selections they feed, among them every opcode's stage of the blocked-cycle count and of the PC; 108 of the 556 have no changed input). It adds the 255 combinational statements with a line-unit register among their nearest named states that the tie-break gave another region (`ext/tie`: 247 `shared`, 8 `engine_data`; 76 of them changed). In all 811 statements, 582 cells, 63,773 mutations (60,490 in `ext/no-line-input`, 3,283 in `ext/tie`; modes `inv`, `const0`, `const1` 14,588 each, `cnot0` 9,963, `cnot1` 10,046) |
| Not selected | Of the 2,025: the 1,260 added-or-changed combinational statements that read line-unit state in the same cycle and lie beyond the first cell-producing statement of their path (`ext/downstream`: engine data 368, engine control 276, XFER timing 200, pins 176, queues 108, events 104, instruction SRAM ports 16, host 12; 1,292 cells, 268,232 mutations), the 125 clocked blocks of other registers whose next-state input changed, and 8 SRAM instances. Base logic that the option leaves unchanged is not selected either |
| Samples | Uniform, without replacement: (1) 3,000 of the first population (3.32 %; `random.seed(20270118)`; job 24297818); (2) 2,117 of the second, at the same sampling fraction (round(63,773 × 3,000 / 90,372); same seed; job 24335278); (3) held-out: 1,000 of the 87,372 first-population mutations that sample (1) did not draw (draw seed 20270119, same population; job 24335406), drawn after the ten first `test_line_spec` modules had been written from sample (1)'s survivors |
| Suite | Sample (1): the variant's 23 default modules of `test/Makefile` at `f9e0bf9` (127 tests), the three line-unit modules first, each passed as `COCOTB_TEST_MODULES`, stopping at the first failing module (stage `ordered`); then the kill stage `kill-final2`, the thirteen `test_line_spec` modules of round 2 on every survivor. Samples (2) and (3): stage `ordered` with the same 23 modules followed by the ten first `test_line_spec` modules (155 tests), then the kill stage `kill3` with the three modules written from sample (2) in round 2. Round 3, every sample: the kill stage `kill4` runs the four modules changed or added in round 3 (`se0_pins`, `invalid_fields`, `image_end`, `limit_bits`) on every mutant that no earlier stage killed and on the earlier kills of the three. Because the committed modules run first, the committed suite's result is read from the same run (`push_summary.py --only-modules`). `PE_VARIANT=diet8_rec16`, `PE_CORE=<mutant>`; Icarus Verilog 14.0 and cocotb 2.0.1, as in the earlier campaigns |
| Controls | The unmodified core and the no-op mutant 0 pass all tests of each stage with none skipped (127 tests: job 24297895; 155 tests: jobs 24335507, 24335508) and survive every stage, `kill4`, `kill5` and `kill4so` included |
| Score | As in mutation-push.md: killed / (mutants − proven equivalent). Survivors that are argued, not proven, to be unobservable stay in the denominator. The interval is the Wilson 95 % score interval for a uniform sample (`sample_score.py`; no finite-population correction, which would only narrow it) |

### 11.2 Result

| | (1) first population | (3) held-out, first population | (2) second population |
|---|---:|---:|---:|
| mutants | 3,000 | 1,000 | 2,117 |
| killed, committed suite (127 tests) | 1,501 | 514 | 1,596 |
| killed with the ten first `test_line_spec` modules (155 tests) | 2,664 | 891 | 1,727 |
| killed with the thirteen of round 2 (158 tests) | 2,664 | 891 | 1,754 |
| killed with all fourteen (160 tests) | 2,664 | 891 | 1,758 |
| proven equivalent, `equiv_induct` (campaign method) | 66 | 15 | 108 |
| proven equivalent, `equiv_induct` with invariants | 0 | 0 | 0 |
| proven equivalent, miter + ABC, 900 s cap | 69 | 24 | 5 |
| score, committed suite, campaign proofs | 51.16 % | 52.18 % | 79.44 % |
| score, committed suite, with the ABC proofs | 52.39 % [50.56, 54.22] | 53.49 % [50.32, 56.62] | 79.64 % [77.82, 81.35] |
| score, 155 tests, campaign proofs | 90.80 % [89.70, 91.79] | 90.46 % [88.46, 92.14] | 85.96 % [84.38, 87.41] |
| score, 155 tests, with the ABC proofs | 92.98 % [91.99, 93.86] | 92.72 % [90.90, 94.19] | 86.18 % [84.60, 87.62] |
| score, 160 tests, campaign proofs | 90.80 % [89.70, 91.79] | 90.46 % [88.46, 92.14] | 87.51 % [85.99, 88.88] |
| **score, 160 tests, with the ABC proofs** | **92.98 % [91.99, 93.86]** | **92.72 % [90.90, 94.19]** | **87.72 % [86.22, 89.09]** |
| survivors (160 tests, all proofs) | 201 | 70 | 246 |
| argued unobservable (11.4; not counted) | 125 | 44 | 246 |
| **neither proven nor argued** | **76, 2.65 % of 2,865** | **26, 2.71 % of 961** | **0 of 2,004** |

Intervals are Wilson 95 % intervals in %. The ten first `test_line_spec`
modules were written from sample (1)'s survivors, so sample (1)'s figures with
them are in-sample. Sample (3) measures the same suite on mutants of the same
population that no test was written from: 92.72 % [90.90, 94.19] against 92.98 %
in-sample (raw kill rates 89.10 % and 88.80 %). The four last modules (the
three of round 2 and `test_line_spec_limit_bits` of round 3) were written from
sample (2)'s survivors: they add 31 kills there (27 and 4; in-sample), and none
in sample (1) beyond the ten and none in sample (3). For sample (2) the
155-test figures are out-of-sample for every module.

Samples (1) and (2) have the same sampling fraction (3.3196 %), so together
they are a proportionally stratified sample of the 154,145 mutations of both
populations: 5,117 mutants, 248 proven equivalent; committed suite 3,097
killed, 63.61 % [62.24, 64.95]; 160 tests 4,422 killed, 90.82 % [89.98, 91.60]
(in-sample for the `test_line_spec` modules; 89.46 % with the campaign's
proofs only); 76 survivors (1.56 %) neither proven nor argued. The interval
treats the pooled sample as a simple random sample; stratification can only
narrow it.

For comparison, mutation-push.md reports 96.82 % for the design of record
(97.93 % with the longer ABC cap) and 95.24 % (96.66 %) for `diet4`. One of
the `diet4` proofs (mutant 1860) is of a clock inversion, which the proof
method does not see (11.4); without it `diet4`'s figures are 95.19 %
(96.62 %). Those mutants were drawn with the region quotas and coverage
weights of `gen_mutants.sh`, not uniformly from one selection, so the numbers
are not a paired comparison.

By sub-region (160 tests, with the ABC proofs; 95 % intervals where given):

| Sub-region | Mutants | Proven equivalent | Killed | Survived | Score |
|---|---:|---:|---:|---:|---:|
| (1) `line/state` | 2,161 | 114 | 1,921 | 126 | 93.84 % [92.72, 94.81] |
| (1) `line/out-engine_ctrl` | 161 | 5 | 90 | 66 | 57.69 % [49.85, 65.17] |
| (1) `line/out-engine_data` | 507 | 12 | 494 | 1 | 99.80 % |
| (1) `line/out-engine_xfer` | 77 | 3 | 71 | 3 | 95.95 % |
| (1) `line/out-pins` | 79 | 1 | 73 | 5 | 93.59 % |
| (1) `line/out-shared`, `line/out-events` | 15 | 0 | 15 | 0 | 100 % |
| (3) `line/state` | 710 | 30 | 643 | 37 | 94.56 % [92.59, 96.03] |
| (3) `line/out-engine_ctrl` | 60 | 3 | 28 | 29 | 49.12 % [36.62, 61.74] |
| (3) `line/out-engine_data` | 177 | 4 | 173 | 0 | 100 % |
| (3) `line/out-engine_xfer` | 21 | 2 | 18 | 1 | 94.74 % |
| (3) `line/out-pins` | 23 | 0 | 20 | 3 | 86.96 % |
| (3) `line/out-shared`, `line/out-events` | 9 | 0 | 9 | 0 | 100 % |
| (2) `ext/no-line-input` | 2,009 | 97 | 1,666 | 246 | 87.13 % [85.56, 88.56] |
| (2) `ext/tie` | 108 | 16 | 92 | 0 | 100 % [95.99, 100] |

In `line/out-engine_ctrl`, 64 of sample (1)'s 66 survivors and all 29 of sample
(3)'s are blocked-cycle-count mutations inside a line XFER; in
`ext/no-line-input`, all 246 survivors are on the blocked-cycle count's opcode
stages (11.4).

Of sample (2)'s 2,117 mutants, 1,181 are on the blocked-cycle count's stages
for opcodes below 30, base-ISA logic that the design of record also has (930
killed, 66 proven equivalent, 185 survived); 177 are on its stages for
opcodes 30-33 (102, 14, 61); 759 are off that chain (726, 33, 0). Every
mutant off the chain that is not proven equivalent was killed (726 of 726).
Counts: `line_survivor_classes.py --chain-share`, `chain_share-ext.json`.

### 11.3 New tests: `test/test_line_spec_*.py`

Fourteen modules with 33 tests: ten written from the survivors of sample (1)
and four (`se0_pins`, `invalid_fields`, `image_end`, `limit_bits`) from those
of sample (2). Each states in its docstring the behaviour of [isa.md](isa.md)
("Line-unit extension", "Machine" and "Instructions") that it checks and
quotes the text. The expected values come from that text (the CRC step rule
and preset table, the ticker formula, the encoders of `test/line_support.py`,
the encoding rules, the cycle counts), and the harness also compares the DUT
with the reference model on every cycle. In the four modules written from
sample (2), `PE_SPEC_ONLY=1` turns that comparison off (`spec_harness` in
`test/test_line_spec_common.py`, the shared helpers). At gate level the
modules are skipped unless `PE_LINE_GL_FULL=1`; with it, 31 of their 33 tests
pass on the official netlist of run 36360490711 (24376083, section 10.1). `test_line_spec_limit_bits`
uses the time warp and is skipped at gate level in any case; it does not use
the line unit and also runs on the design of record (11.4).

| Module | Specified behaviour checked | (1): kills (only this module) | (3): first failing module | (2): first failing module / kill stage |
|---|---|---:|---:|---:|
| `test_line_spec_every_engine` | "one line unit per engine": the eleven directed scenarios of `test_line_unit` on engines 1, 2 and 3 (an engine-renaming view of the harness) | 659 (14) | 211 | 29 |
| `test_line_spec_crc` | the CRC step rule for every preset, both bit orders, driven and sampled line XFERs and classic XFERs, CRC set from and read into every register; a sampling XFER ended by SE0 feeds only the bits before it | 640 (73) | 54 | 0 |
| `test_line_spec_xfer_crc_bit` | without c bit 6 an XFER leaves the CRC unchanged (classic and line, odd bit counts); with it, drive-and-sample XFERs on a loopback feed the bits (all four classic modes) | 424 (19) | 9 | 0 |
| `test_line_spec_stuffing` | stuffing and destuffing for runs of 1s of length 1-8 and runs of either polarity of length 2-8, trailing stuff bits, stuff bits not fed to the CRC, wrong stuff bits flagged | 398 (4) | 5 | 1 |
| `test_line_spec_registers` | CRC read-back and LSTAT write register a and no other register; the CRC is zero-extended | 341 (115) | 31 | 0 |
| `test_line_spec_state_held` | 28 base-instruction cases (WAIT, a stalled PULL, ALU, jumps, ...) leave the unit's flags, SE0 count, ticker and CRC unchanged | 332 (18) | 11 | 0 |
| `test_line_spec_lstat` | LSTAT into every register: SE0 with 21, 10 and 32 bits remaining, stuff error, arbitration loss, queue bits; flags stay set until LCFG | 153 (28) | 23 | 4 |
| `test_line_spec_bounded_wait` | WAITPIN/WAITEVENT after each line-unit path time out after exactly LIMIT samples; each engine's status register reports fault code 3 | 79 (60) | 21 | 85 |
| `test_line_spec_pins` | a driving line XFER writes the data pin named by PINS and the pair pin and no other pin, for every data pin | 50 (46) | 9 | 10 |
| `test_line_spec_ticker` | the ticker formula over 128 streamed boundaries (256 ticks) for fractions that set every bit | 10 (5) | 3 | 2 |
| `test_line_spec_se0_pins` | SE0 end reads the pair pin that the PINS clock field names: every pair pin 0-7 on every engine, with the other pins high and then low; LSTAT reports SE0 and 11 bits left | 39 (0) | 0 | 18 (18) |
| `test_line_spec_invalid_fields` | each rule-breaking bit of the LTIM, LCFG, CRC, LSTAT and line-XFER fields, and each opcode of 34 or more one bit away from 30-33, faults with code 1 at its PC; each valid neighbour completes; every engine | 1 (0) | 0 | 3 (3) |
| `test_line_spec_image_end` | an instruction that advances the PC by one, in word 63 of a 64-word image, is followed by fault code 2 with the PC at 64: 34 cases covering the 31 opcodes that can do so (all but HALT, JMP and FAULT; PULL with a queued word, OUT, a LOOP and a JZ not taken, classic and line XFER); every engine | 1 (0) | 0 | 6 (6) |
| `test_line_spec_limit_bits` | WAITEVENT with no event and WAITPIN on a pin that stays low time out on the LIMIT-th unsuccessful sample for LIMIT 2^k + 2, k = 0..23, on every engine: the engine's output enable is high for exactly LIMIT + 1 edges (DIR's edge to the fault's), and the status register reports fault code 3 | 0 (0) | 0 | 4 (4) |

Column (1): every module on each of the 1,499 survivors of the committed
suite: the thirteen modules of round 2 in the final kill stage `kill-final2`
(jobs 24352061, 24355309), and `image_end` as now written and `limit_bits` in
`kill5` (job 24369236); a mutant counts for every module that fails on it.
Columns (3) and (2): the first failing module of stage `ordered`, in which the
committed modules had passed (jobs 24337259; 24337258, 24349684, 24354787),
and, for the four last modules, the kill stages `kill3` (jobs 24355090,
24356733) and `kill4`. `kill4` ran the four modules as now written on every
mutant that no earlier stage had killed and on the earlier kills of the three
(jobs 24369232, 24369234, 24369235). It kills each of the 40 earlier kills of
the three in sample (1) and the 27 in sample (2) again, adds 4 in sample (2),
all by `limit_bits`, and none in sample (3). Against round 2,
`test_line_spec_image_end` has five more cases (PULL, OUT, LOOP, JZ and a
classic XFER; kills unchanged). Against round 1, `test_line_spec_bounded_wait`
reads each engine's fault code from the DUT's status register (it read the
model's); in the final kill stage it no longer fails on mutant 2520, which
five other modules kill. The suite with the fourteen modules (160 tests: the
127, 28, 3 and 2) passes on the unmodified core with Icarus 13.0 and cocotb
2.0.1, the CI versions: 160 pass, 0 fail, 0 skipped; 433 s of test time, 94 s
of it in the `test_line_spec` modules; 7 min 22 s wall (job 24369277).

**Without the reference model.** With `PE_SPEC_ONLY=1` the four modules
written from sample (2) run without the per-cycle comparison, so only their own assertions
can fail; they read the DUT's status registers, PC, RX words and output-enable
pins and compare them with values from isa.md. On `kill4`'s kills (stage
`kill4so`, jobs 24369237, 24369238):
- Sample (2): all 31 fail again, on the same modules (`se0_pins` 18,
  `image_end` 6, `limit_bits` 4, `invalid_fields` 3).
- Sample (1): 30 of the 40 fail. The other 10 are `se0_pins` kills that fail
  only through the comparison with the model; other modules also kill each
  of them.
A re-run of the 61 with logs (job 24370775) shows that each one fails on an
assertion of its module (`results/diet8_rec16/spec_only.tsv`). The ten first
modules construct the harness directly and were not run this way.

### 11.4 Survivors

`campaigns/mutation/line_survivor_classes.py` places each survivor by its
mutated multiplexer's select signal and the port mutated. It locates the
select signals structurally: per engine, the conditions on the next-value path
of the `running` register (STOP/BEGIN, START, "active": running with no fault
code and out of reset, WAIT, XFER in progress, the fault condition), and the
blocked-cycle count's opcode stages on the count's own next-value path; it
checks each step's structure and stops if the core differs. The class rules
are in its docstring. Tables: `survivor_classes.tsv`,
`survivor_classes-held.tsv`, `survivor_classes-ext.tsv`.

| Class | (1) | (3) | (2) | Argument |
|---|---:|---:|---:|---|
| blocked-cycle count in a line XFER | 64 (47 never infected) | 29 | 0 | The mutation is in the count's next-value selection inside a line XFER. The count holds while the transfer runs, the transfer's completion clears it, and no bounded wait starts during a transfer; `test_line_spec_bounded_wait` checks the timeout after each completion path it drives (mutation-push.md section 5, "blocked-cycle count during an XFER") |
| blocked-cycle count, stages of other instructions | 0 | 0 | 233 | The mutation is on the count's opcode stages where no WAITPIN or WAITEVENT value passes: it forces a bit to 0 or flips a bit only while another bit is 1, or changes which of the other opcodes' stages is taken. In a running engine the count is 0 whenever an instruction other than WAITPIN/WAITEVENT issues: every completion clears it and it only grows in a bounded wait (mutation-push.md section 5, "blocked-cycle count, stages of other instructions") |
| blocked-cycle count written by HALT | 0 | 0 | 7 | HALT's stage writes the count in the cycle the engine halts; a halted engine holds the count, and START clears it before the engine runs again |
| blocked-cycle count written at an XFER or FAULT issue | 0 | 0 | 6 | The hold at the end of the opcode stages is reached only by XFER and FAULT, the two valid opcodes without a stage (an invalid encoding takes the fault branch before the stages). An XFER holds the count and clears it at completion; FAULT stops the engine, and START clears the count |
| register changed on the edge the engine faults | 40 | 14 | 0 | The hold value (or the hold select, tied to 0) of a line-unit register in the cycle in which the engine faults (PC outside the image or an invalid encoding). The faulted engine's unit is stopped and not readable; only START runs it again, and START clears the ticker, LCFG, line state, flags, CRC and preset |
| register changed on the STOP/BEGIN edge | 11 | 0 | 0 | As above for the STOP or BEGIN edge |
| register of a halted or faulted engine | 7 | 1 | 0 | The mutation acts on the hold path of an engine that is not running; START clears the register before the unit is used again |
| START value of the fraction | 3 | 0 | 0 | START writes the fraction register with the mutated value; the ticker reads it only while running, and only LTIM starts the ticker, which writes the fraction |
| **neither proven nor argued** | **76** | **26** | **0** | see below |

The arguments are not proofs; argued survivors stay in the denominator, as in
mutation-push.md. The rules of the classes of sample (1) were written from its
survivors; applied unchanged to the held-out sample, they argue 44 of its 70
survivors. The three rules on the count's opcode stages were written from
sample (2)'s survivors. All four blocked-count arguments rest on one property
of the unmodified core (in a running engine the count is 0 whenever an
instruction other than WAITPIN/WAITEVENT issues). `equiv_inv.py`'s invariant
library holds a form of it, but proves none of these mutants (jobs 24345910,
24355289, 24356908).

Neither proven nor argued:

* Sample (1), 76: the reach/infect/propagate wrapper run with the three
  line-unit modules and nine of the ten first modules (job 24318039; mutant 0
  shows no difference in 1,623,113 cycles) shows 56 infected (a line-unit
  register differed) and 20 not infected, none with a pin or read difference.
  By nearest state: trailing-stuff flag 10, fraction accumulator 9, LCFG
  register 8, CRC 7, stuff run 6, pin outputs 5, boundary flag 5, Manchester
  pending 4, others 22. 11 of them sit on the path selected when no WAIT is
  running and were not analysed further.
* Sample (3), 26: not run on the wrapper. By nearest state: remaining-bit
  count 5, pin outputs 3, fraction 3, last stuffed bit 2, trailing-stuff flag
  2, boundary flag 2, fraction accumulator 2, others 7.
* Sample (2), none. Round 2 left 4 here (331, 333, 1438, 1442): `cnot1` on
  WAITEVENT's stage of the count of engines 0 and 2, which flips bit p of the
  next count while bit c is 1, (p, c) = (10, 13), (11, 14), (16, 18),
  (7, 21). `test_line_spec_limit_bits` kills all four, at LIMIT 8,194,
  16,386, 262,146 and 2,097,154 (2^c + 2), on the engine each one mutates,
  also with `PE_SPEC_ONLY=1` (jobs 24369234, 24369238, 24370775). The same
  holds for any pair p ≠ c of the 24 count bits:
  - Below 2^c, bit c of the count is 0, so the flip first acts on the sample
    on which the count reaches 2^c.
  - The mutant then stores 2^c + 2^p. On the next sample,
    2^c + 2^p + 1 ≥ 2^c + 2 raises the timeout one sample early; the
    unmutated count, 2^c, gives 2^c + 1 < 2^c + 2.

  `test_kill_blocked` times WAITEVENT out at LIMIT 3, 5 and 2^k + 5 for
  k = 12, 17, 20 and 23; the four survived it and the rest of the committed
  suite. The same four mutations, applied to WAITEVENT's stage of engines 0
  and 2 in the design of record's core (`src/` of `main` at `24f31f0`, sha256
  `26a873db…`), pass its 102 tests (the base variant's 20 default modules)
  and fail `test_line_spec_limit_bits`. The unmodified core and mutant 0 pass
  all 104 (job 24370776; `results/diet8_rec16/dor_waitevent_analogues.tsv`).
  The design of record's suite therefore has the same gap.

**Formal classification.** `equiv_mutant.py` on every survivor of the
committed suite (sample (1), jobs 24311211, 24315679) or of stage `ordered`
(samples (2) and (3), jobs 24344147, 24354709, 24356732; 24344149, 24352050):
66, 108 and 15 proven. In sample (1) it also "proves" mutant 231, which
inverts the clock input of an `arbitration_lost` flip-flop and is killed by
`test_line_spec_every_engine`: the method models each flip-flop as a one-cycle
delay of the one clock, so a flip-flop moved to the falling edge looks
equivalent. `push_summary.py --no-clock-proofs` does not count such proofs; all
scores above use it. The same applies to `diet4` mutant 1860 in
mutation-push.md section 7 (a CLK inversion among its 107 `equiv_induct`
proofs; 95.19 % and 96.62 % without it). `equiv_inv.py` with the invariant
library proves the positive controls and none of the killed controls, and no
survivor in any sample (jobs 24311245, 24316702; 24345910, 24355289,
24356908; 24345911, 24353783). The miter with ABC (900 s cap) proves 69, 5
and 24 survivors (jobs 24318038; 24347636, 24355290, 24356909; 24347638,
24353784). Controls (`results/diet8_rec16/controls.txt`): mutant 0 is proven
by all three methods in samples (2) and (3), and by `equiv_mutant.py` and
`equiv_inv.py` in sample (1), where the miter was not run on it; in sample (1)
the miter proves 5 of the 6 `equiv_mutant.py`-proven controls (185, 200, 344,
822, 2263; 231, the clock inversion, is undecided); of sample (2)'s three
`equiv_mutant.py`-proven controls the miter proves two (the third is
undecided), and no killed control is proven by any method. No mutant counted
as proven equivalent is killed by a test.

### 11.5 Jobs

| Step | Job | Result |
|---|---|---|
| `gen_variants.sh` in the snapshot | 24295723 | `diet8_rec16` core equals `src/`; `diet8` core `5fb364f2…` |
| sample (1): mutant list and sample | 24297818 | 90,372 mutations, 3,000 sampled |
| sample (1): stage `ordered` | 24297895 (controls), 24299453 (37 × 8; 11 tasks on two slow nodes cancelled), 24306953 (their ids and 36 budget timeouts, 17 × 8) | 1,501 killed, 1,499 survived; 279.7 CPU-hours |
| sample (1): kill iterations while the ten first modules were written | 24312563, 24315680, 24316698, 24316987, 24317187, 24324016, 24324779 | superseded by the final kill stage |
| sample (1): final kill stage `kill-final2`, the thirteen modules of round 2 | 24352061, 24355309 | 1,163 of 1,499 killed; `orig` and 0 survive; 62.1 CPU-hours |
| sample (1): `equiv_mutant.py` / `equiv_inv.py` / miter + ABC | 24299476, 24311211, 24315679 / 24311245, 24316702 / 24318038 | 66 counted / 0 / 69 |
| sample (1): reach/infect/propagate | 24315700 (controls), 24318039 | table 11.4 |
| selection of the second population | 24334996 | 582 cells, 63,773 mutations |
| samples (2) and (3): lists and samples | 24335278, 24335406 | 2,117 and 1,000 sampled; (3)'s population identical to (1)'s |
| samples (2) and (3): controls of stage `ordered` | 24335507, 24335508 | `orig` and 0 survive, 155 tests each |
| sample (2): stage `ordered` | 24337258 (25 × 8), 24349684 (helper, reverse order), 24354787 (18 ids that hit the 3,600 s budget on one slow node and 2 unfinished ones, run elsewhere) | 1,727 killed, 390 survived; 283.4 CPU-hours |
| sample (3): stage `ordered` | 24337259 (12 × 8) | 891 killed, 109 survived; 104.7 CPU-hours |
| samples (2), (3): kill stage, three last modules | 24355090, 24356733; 24353785, 24353812 | 27 and 0 killed |
| samples (2), (3): `equiv_mutant.py` | 24344147, 24354709, 24356732; 24344149, 24352050 | 108; 15 |
| samples (2), (3): `equiv_inv.py` | 24345910, 24355289, 24356908; 24345911, 24353783 | 0; 0 |
| samples (2), (3): miter + ABC, 900 s | 24347636, 24355290, 24356909; 24347638, 24353784 | 5; 24 |
| suite with the thirteen modules, CI tool versions | 24357186 | 158/158 pass |
| round 3: `kill4`, samples (1), (2), (3) | 24369232, 24369234, 24369235 | 40, 31 and 0 killed (4 not killed before, in sample (2)); controls survive; 17.1 CPU-hours |
| round 3: `kill5`, sample (1) | 24369236 | `image_end` 1, `limit_bits` 0 of 1,499; controls survive; 15.7 CPU-hours |
| round 3: `kill4so` (`PE_SPEC_ONLY=1`), and the re-run with logs | 24369237, 24369238; 24370775 | 30 of 40 and 31 of 31; controls survive; each failure an assertion of its module |
| round 3: the four mutations on the design of record's core | 24370776 | pass its 102 tests, fail `limit_bits`; controls pass all 104 |
| suite with the fourteen modules, CI tool versions | 24369277 | 160/160 pass |

Compute: sums of per-mutant run times. The arithmetic of this section is
checked with AXLE (lean-4.28.0, `okay: true`; work directory
`<local work dir>/tt-work/ext-mutation/`: `axle/line_mutation_arith.lean`,
`r2/axle/line_mutation_r2.lean`, `r3/axle/line_mutation_r3.lean`).

An independent review rebuilt every mutant's status from the raw result files
with its own script and got the same scores; it re-ran 9 uniformly drawn
survivors (all pass the 160 tests) and 9 drawn kills (each fails its recorded
module), `test_line_spec_limit_bits` on the unmodified core, on 331, 1442 and
333 and on the design-of-record analogues, and four `cnot1` mutants of other
bit pairs that are in no sample, each killed at LIMIT 2^c + 2 (24372386,
24372387).

The design-of-record comparison of 11.4 (`dor_waitevent_analogues.tsv`) was
run from the work directory's scripts; `campaigns/mutation/README.md` lists the
result file but not yet the commands.


## 12. Running the official GitHub actions on a branch

To have the Tiny Tapeout `gds`, `precheck` and `gl_test` actions build and test
the variant, a branch needs (relative to `main`; this list describes commit
`ac436f7` of 2026-09-28 and is superseded for the workflows by the design
selection of section 12.2):

- `src/protocol_emulator_core.v` replaced by `build/variants/diet8_rec16/protocol_emulator_core.v`
  (its first line names `configs/variants/diet8_rec16.json`);
- `src/config.json`, `src/project.v` and `info.yaml` unchanged (8x4, the
  committed 15 ns flow settings; `info.yaml` keeps `clock_hz` 50 MHz);
- `PE_VARIANT: diet8_rec16` in the environment of the `gl_test` job of
  `.github/workflows/gds.yaml` (as `gds_6x4.yaml` does for `diet4`), or
  `PE_VARIANT ?= diet8_rec16` in `test/Makefile`. The gl_test action runs
  `make GATES=yes` in `test/`; with the variant set, the reference model is the
  variant's and the three line-unit modules run as well (their long tests are
  skipped or shortened at gate level);
- `.github/workflows/test.yaml`: the RTL suite needs `PE_VARIANT=diet8_rec16`
  and `PE_CORE=$PWD/../src/protocol_emulator_core.v`, as `gds_6x4.yaml`'s
  `rtl_test` does; otherwise it compares the variant core with the design of
  record's model and fails;
- `.github/workflows/regen.yaml`: it regenerates `src/` from
  `configs/instruction-sram-32.json` and would fail; on the branch it must
  generate with `--variant diet8_rec16 --output src/protocol_emulator_core.v`
  and accept that header line, or be disabled;
- `.github/workflows/formal.yaml`: `reset_safety` reads `src/` and would check
  the variant core with the design of record's settings; the variant's formal
  run is `formal/run.sh --variant diet8_rec16`.

**Runtime.** The design of record's p018 flow took 7,833 s locally and
4 h 24 min in the `gds` job. This run took 10,040 s locally, 1.28 times as
long; if the runner scales the same way, the `gds` job would take about
5.6 h, close to GitHub's 6 h job limit. This is an estimate from one run on
each side.

### 12.1 Official run (2026-09-28)

Branch `eval/diet8-rec16` (commit `ac436f7`) carries the published
`diet8_rec16` core (sha256 `d517e277…`) in `src/` and sets `test/Makefile`'s
default variant; `src/config.json` and `info.yaml` are those of `main`.

| Workflow | Run | Result |
|---|---|---|
| `gds` | 36360490711 | job `gds` success (23:59 to 03:58 UTC, 3 h 59 min); `precheck` success ("Precheck passed", finished 07:12 UTC); `gl_test` success, `TESTS=127 PASS=66 FAIL=0 SKIP=61`; `viewer` failed (GitHub Pages is not enabled), as on `main` |
| `test` | 36360490678 | success: the RTL suite with the variant, 127 tests |
| `formal` | 36360490685 | success |
| `regen` | 36360490692 | failure, as expected: it regenerates `src/` from the design of record's configuration, which the branch replaces on purpose |
| `docs` | 36360490710 | success |

The `tt_submission` artifact's `stats/metrics.csv` is byte-identical to the
local run's (job 24121613): setup WS typ/fast/slow +6.03/+7.38/+2.07 ns and
hold WS min +0.166 ns at 15 ns with 0 violations, utilization 63.21%, route
DRC 0, LVS 0, one antenna net. The official gate-level netlist is
sequentially equivalent to the RTL, with the self-test passed
(`formal_eq/eq_check.py check --run-dir <artifact> --variant diet8_rec16
--core <artifact src core> --top <artifact src/project.v> --selftest`, Slurm
job 24163332, 168 s). The `gds` job took 3 h 59 min, less than the 5.6 h
estimated above.

This run predates the antenna change of section 7.2.1 and the workflow
changes of section 12.2; neither has run on GitHub yet.

### 12.2 Design selection

`configs/design-selection.txt` names the generator configuration of
`src/protocol_emulator_core.v` in one line: `configs/instruction-sram-32.json`
on `main` (the base design), `configs/variants/diet8_rec16.json` on
`eval/diet8-rec16`. `scripts/design_selection.sh` reads it (fields `config`,
`variant`, `env`, `check`) and rejects anything but one of those two forms
(`configs/variants/NAME.json`, NAME not `base`) naming an existing refinement
configuration. Every consumer takes the design from it:

| Consumer | Base (`main`) | Variant NAME (`eval/diet8-rec16`) |
|---|---|---|
| `regen.yaml` | `scripts/generate.sh configs/instruction-sram-32.json`, then `git diff --exit-code -- src` (as before) | `scripts/generate.sh configs/variants/NAME.json`, same check; the header line of `src/` names that configuration |
| `test.yaml` | `PE_VARIANT=base`, no `PE_CORE` (test/Makefile uses `src/` directly, as before) | `PE_VARIANT=NAME`, `PE_CORE=src/protocol_emulator_core.v` |
| `test/Makefile` default `PE_VARIANT` (used by the Tiny Tapeout `gl_test` action, `make GATES=yes`) | `base` | `NAME` |
| `formal.yaml` | `formal/run.sh --generate-only`; matrix = `formal/run.sh --list` (the same 16 jobs as before) | `formal/run.sh --variant NAME --generate-only` with `VARIANT_CORES` (the generated core must equal `src/` below its header line); matrix = the jobs of `formal/run.sh --variant NAME --list` that are not `line_*`, plus the line-unit CI subset `formal/run.sh --variant NAME --ci --list` |
| `equiv.yaml` (gds runs) | variant `base` | variant `NAME`, read from the built commit's selection file |

`gds.yaml` and `gds_6x4.yaml` are unchanged. A variant other than the selected
one still runs as before (`make PE_VARIANT=diet4` simulates
`build/variants/diet4/`); on the branch, `make PE_VARIANT=base` now simulates
`build/variants/base/` instead of the variant core in `src/`. `test/Makefile`
runs `scripts/design_selection.sh` only when its result is needed, that is when
`PE_VARIANT` is not given or is given without `PE_CORE`; a copied test tree
without `scripts/` then stops with an error that names both variables. With
both given (as `run_mutant.py` passes them for a variant) the script is not
used.

**What adoption changes.** Adopting a variant as the design of record means
changing this one line and committing `src/protocol_emulator_core.v`
regenerated from it. The workflow files (`regen.yaml`, `test.yaml`,
`formal.yaml`, `equiv.yaml`) and `test/Makefile` need no further edits. The
following do not follow the selection and need their own change at adoption:

- `host/`: `ProtocolEmulator` defaults to the device `base` (`host/pe_host/host.py`);
  the default does not follow the selection file, which a MicroPython board
  cannot read. A host for the adopted design passes `device="diet8_rec16"` (or
  the default in `host.py` is changed).
- `formal_eq/test_eq_check.py` (`TestInputs.test_pdk_mismatch_refused`) assumes
  that `src/` holds the base core; a proposed change gives the test its own
  base core file (proposed, not applied; section 12.3).
- The mutation tooling (`campaigns/mutation/gen_mutants.sh`,
  `gen_line_mutants.sh`, `mk_design.sh`) packs test trees without `scripts/`;
  a base task (no `PE_VARIANT`) from a commit that carries this `test/Makefile`
  then stops at the error above. A proposed change packs
  `scripts/design_selection.sh` when the snapshot has it (proposed, not applied;
  section 12.3).
- `certs.yaml` on `main` (timing certificates, added after 24f31f0) generates
  the formal RTL with `formal/run.sh --generate-only`, that is for the base
  configuration, whatever the selection says.
- `scripts/generate.sh` without arguments and `scripts/gen_variants.sh` checks
  1 and 2 still assume `configs/instruction-sram-32.json`.

**Formal CI subset.** On the branch the formal matrix is the 16 existing jobs
with the variant's settings plus the line-unit CI subset of `formal/run.sh
--ci` (every `line_*` job except the long bounded checks; the list is
`formal/run.sh`'s, see formal/README.md "Line unit"). The job summary lists
the jobs left out. The job-selection step fails when `--ci --list` names no
`line_*` job, names a job that is not a `line_*` job, or names a job that
`--list` does not. With the committed `formal/run.sh` of f9e0bf9 (no `--ci`)
the step fails (`run.sh: unknown option --ci`), so `formal.yaml` and the
`formal/run.sh` with `--ci` must be committed together. With the
`formal/run.sh` of section 6.4 the step selects 58 of 62 jobs: the 16 and 42
line-unit jobs, leaving out `line_crc_e2e`, `line_codec_bmc`,
`line_crc_tx_bmc` and `line_crc_rx_bmc` (Slurm 24308920; the integrated tree,
24376084).

**Equivalence on the branch.** `equiv.yaml` now triggers after `gds` runs of
`main` and `eval/diet8-rec16`. The select job first requires the run's `gds`
job to have succeeded (otherwise a `workflow_run` event is skipped, as before,
without reading anything else). For a `gds` run it then reads
`configs/design-selection.txt` of the built commit through the GitHub API as
data (same rules as `scripts/design_selection.sh`); a commit without the file
is base, and any other read error fails the job. The check job then requires,
from its sparse checkout of the built commit, that the selection names the
same configuration (read with `scripts/design_selection.sh --repo rtl` of the
workflow's own commit) and that the core's first line names that
configuration, before running `formal_eq/eq_check.py --variant NAME`. GitHub
runs `workflow_run` workflows from the default branch, so the branch's `gds`
runs are checked only once this `equiv.yaml` is on `main`; until then,
`workflow_dispatch` with the run id. GitHub also lists a `workflow_run` run
under the default branch's latest commit: the check of a branch build, pass or
fail, appears on `main`'s latest commit as the job `equivalence (diet8_rec16)`.

**The formal job list.** `formal.yaml` takes its matrix from `formal/run.sh
--list` (and `--ci --list` for a line-unit variant) instead of a static list;
for the base design the generated matrix equals the former static 16-job
matrix in the same order. `formal/run.sh` is therefore the single source of
the CI job list: a job removed from its list leaves CI without an error, which
the static matrix would have reported.

**Porting to `main`.** `main` has moved on since 24f31f0 (a "Hardcaml tests"
job in `regen.yaml`, a `protocols-ext` job in `test.yaml`, `certs.yaml` and
`consistency.yaml`), so these files go to `main` by a 3-way merge, not by
copying. On `main` 743d534 a `git merge-file` of `regen.yaml` applied without
conflict (24309190); on `main` f8142fc it has one conflict, in the comment
above the `opam install` line, and every other workflow file merges cleanly;
with main's comment kept, actionlint and shellcheck report nothing
(independent review, 24312940). On `main` the selection file names
`configs/instruction-sram-32.json`.

### 12.3 Validation of the workflow changes (local emulation)

The workflows' `run:` steps ran verbatim (`bash --noprofile --norc -eo
pipefail`) under a local emulator; tool installation steps were replaced by
the same tool versions on the cluster (OCaml 5.2.1 switch with the pinned opam
packages, Icarus 13.0, cocotb 2.0.1, OSS CAD Suite 2026-07-29). Snapshots:
`main` 24f31f0 and `eval/diet8-rec16` f9e0bf9 with the files of section 12.2
("these files"; the selection set to base for `main`); f9e0bf9 with these
files and the `formal/` of section 6.4 (identical to the `formal/` of Slurm
job 24307354); `main` 743d534 with these files, its `regen.yaml` produced by
`git merge-file` (743d534, base 24f31f0, the branch's version); and the
unmodified commits for comparison. None of these files has run on GitHub
yet.

| Check | Base (`main` + these files) | `diet8_rec16` (`eval/diet8-rec16` f9e0bf9 + these files) | Unmodified commit, for comparison | Job |
|---|---|---|---|---|
| `regen.yaml` | 24f31f0: `scripts/generate.sh configs/instruction-sram-32.json`, no difference in `src/`; 743d534 (merged `regen.yaml`): the same, its "Hardcaml tests" step not run here (it starts with `sudo apt-get`) | `scripts/generate.sh configs/variants/diet8_rec16.json`; no difference in `src/` | `main`: no difference; branch: `src/` differs (the failure of run 36360490692) | 24300291, 24309190 |
| `test.yaml`, test job | 24f31f0: `PE_VARIANT=base`; 102 of 102 pass, the same names and results as the unmodified 24f31f0 and as run 36391218353; 743d534: 107 of 107, the same as the unmodified 743d534 | `PE_VARIANT=diet8_rec16`, `PE_CORE=src/...`; 127 of 127 pass, the same names and results as the unmodified branch and as run 36360490678 | as stated | 24309463 (24299915 for the unmodified 24f31f0 and f9e0bf9) |
| `test.yaml`, lint job | passes (24f31f0 and 743d534) | passes | passes | 24309463 |
| `test/Makefile` resolved variables, 14 sets of command-line and environment variables | 13 of 14 identical to the unmodified `main`; `make PE_VARIANT=` (empty) now falls back to the selection, where `main` left the variant empty and the core path `build/variants//...` | 11 of 14 identical to the unmodified branch; `PE_VARIANT=base` and `=diet4` now simulate `build/variants/<name>/` (the branch simulated the variant core in `src/` against another model), and `PE_VARIANT=` falls back to the selection | | 24308326 |
| Copied test trees (`src/project.v test firmware models configs`, as the mutation tooling packs them) | with `PE_VARIANT` and `PE_CORE` given: the same variables as the full checkout; without them: `make` stops ("../scripts/design_selection.sh does not exist here; give both PE_VARIANT=<name> and PE_CORE=<core.v>"); with `scripts/design_selection.sh` added: the same as the full checkout in every case | the same | trees of the unmodified commits: the same as their full checkouts | 24308326 |
| `run_mutant.py`, control mutant 0, stage `kill` with `test_smoke` | tree packed by the current tooling: `error` (the `make` stop above); packed with the proposed packing change: 3 of 3 pass (survived), from `gen_mutants.sh` and from `mk_design.sh` | packed by the current and by the proposed tooling (`gen_line_mutants.sh`, `mk_design.sh`): 3 of 3 pass in each of the four cases | unmodified 24f31f0: 3 of 3 pass | 24309230 |
| Tiny Tapeout `gl_test` commands (`make clean; GATES=yes make`, `test_smoke` only), no variables set | official netlist of gds run 36391218317: 3 of 3 pass (default `PE_VARIANT` base) | official netlist of gds run 36360490711: 3 of 3 pass (default `PE_VARIANT` diet8_rec16) | | 24309463 |
| `formal.yaml`, job selection | 16 of 16 jobs, the static matrix of `main` in the same order (24f31f0 and 743d534) | 58 of 62 jobs (16 + 42 `line_*`); with the f9e0bf9 `formal/run.sh` the step fails (`unknown option --ci`); stubbed `--ci --list` outputs (empty, a non-`line_*` job, an unknown job) each fail the step | | 24308920 |
| `formal.yaml`, all jobs | 24f31f0: RTL generated as before; 16 of 16 matrix jobs meet their expectations (14 PASS, the 2 negative controls FAIL as required), the same jobs in the same order as the static matrix of the unmodified 24f31f0 (24300250) | generated core equals `src/` below its header line (`VARIANT_CORES`); 58 of 58 matrix jobs meet their expectations (37 PASS, 21 negative controls FAIL as required); longest `line_codec_prove` 416 s, then `timing_isolation_bmc` 333 s (8 jobs at a time) | | 24309631 |
| `equiv.yaml`, select job | `workflow_dispatch` for gds run 36391218317: runs, variant base (24f31f0 has no selection file) | `workflow_run` for gds run 36360490711 (selection served for ac436f7 by an emulation shim): runs, variant `diet8_rec16`; with the selection read failing (HTTP 500 from a shim): the job fails | `workflow_run` for gds run 36120104740 (its `gds` job was cancelled) with the selection read failing: skipped (`go=false`); the order of the previous version of this file failed instead | 24309079 |
| `equiv.yaml`, check job (unchanged in this round) | `workflow_dispatch` for gds run 36391218317: equivalent with the self-test passed; netlist sha256 `09841e31...`, the same verdict and inputs as CI run 36448275402 | `workflow_run` for gds run 36360490711 with the workflow and tool checkout of the base snapshot (as GitHub runs `workflow_run` from the default branch), the branch selection served for ac436f7 (which predates the file): variant `diet8_rec16`, core sha256 `d517e277...`, equivalent with the self-test passed (mutants not equivalent at frames 13 and 6, 9 of 9 recipe controls) | | 24300249, 24300555 |
| `equiv.yaml`, dispatched with the branch's own `formal_eq/` | | fails at "Unit tests of formal_eq": `formal_eq/test_eq_check.py` `TestInputs.test_pdk_mismatch_refused` uses the core in `src/` with variant base (a proposed change gives the test its own base core file; not applied) | | 24300249 |
| `equiv.yaml`, fail-closed case | | dispatch for run 36360490711 (commit ac436f7 predates the selection file): variant base selected, the check job stops at "Require the built commit's design selection and core header" because the core names `configs/variants/diet8_rec16.json` | | 24300249 |
| `host/` | `make test`: 89 tests pass (including the RTL replays of the self-test and the flagship scenario on both cores, Icarus 13, and the MicroPython 1.29 replays); `upy-check` 0 problems; `make mpy`; `make rtl-cocotb` (cocotb 2.0.1 lockstep, device base) 2 of 2 | the same 89 tests pass; `rtl-cocotb` with device `diet8_rec16` 2 of 2 | | 24309463 |

The formal round's own run of the `--ci` subset on the same `formal/`
(`formal/run.sh --variant diet8_rec16 --no-generate --ci --parallel 4`, Slurm
24307354) reports 42 of 42 jobs meeting their expectations, the longest in
481 s. Run times on a hosted runner (4 vCPUs, one job per runner) are not
known yet; `SBY_TIMEOUT` is 1,500 s and the job limit 30 minutes.

Static checks: actionlint 1.7.12 with shellcheck 0.11.0 reports nothing for
`regen.yaml`, `test.yaml`, `formal.yaml` and `equiv.yaml` on both snapshots
(on `main` 24f31f0 the same run reports two SC2046 warnings in `regen.yaml`
and `formal.yaml`, the intended word splitting of `opam install $(grep ...)`,
now annotated). On 743d534 with the merged `regen.yaml` it reports three
findings (two SC2046, one SC2086), all in `main`'s "Hardcaml tests" step, which
the unmodified 743d534 file reports as well (four findings there, the fourth
being the annotated `opam install` line). shellcheck 0.11.0 reports nothing
for `scripts/design_selection.sh`.

The integrated branch tree of this round, which adds the test modules,
formal files and campaign files of sections 6.4 to 6.6 and 11, was checked the
same way (section 10.1).


## 13. Verification parity

The evidence for the design of record and for `diet8_rec16`, side by side.
"Design of record" means the base configuration as documented on this branch
(`main` at 24f31f0 and earlier); `main` has added evidence since (for example
timing certificates in CI), which this table does not follow. Each cell names
its source: a section of this page, another page of `docs/`, a Slurm job or a
GitHub Actions run.

| Evidence | Design of record | `diet8_rec16` |
|---|---|---|
| Random lockstep campaign | 536,064 cases, 3,207,184,824 lockstep cycles, 0 failures; 10,752 of the cases at gate level ([verification-campaign.md](verification-campaign.md), "Campaigns and totals"; [results.md](results.md) R20) | 724,480 cases, 4,935,357,251 lockstep cycles, 0 failures; 304,128 line-unit cases; 15,872 cases at gate level (section 6.5, 17 Slurm arrays) |
| Oracle of the random campaign | the lockstep model `test/model/`, which shares the specification with the RTL ([limitations.md](limitations.md) section 4) | the same model with `line_unit.py`, which follows `engine.ml` step for step (section 6.3); model-independent parts: the contract-text decode check (0 disagreements) and 4 negative controls, all detected (section 6.5) |
| Random negative controls | XOR model bug: 64 of 64 seeds, 1,536 of 4,096 cases ([verification-campaign.md](verification-campaign.md), "Negative control") | XOR model bug, a model CRC defect and two RTL defects, all detected (section 6.5) |
| Coverage bins | `random_gen.Coverage`: every bin hit; `xcov.py`: 71 of 71 ([verification-campaign.md](verification-campaign.md), "Coverage") | the same bins: every bin hit, 71 of 71; line unit: 357 of 357, also at gate level alone; the in-tree CI generator 245 of 357 (section 6.5) |
| cocotb suite in CI | 102 tests, 102 pass (`test` run 36298635420 of d76f1cc, [results.md](results.md) section 2b) | 160 tests: 102 + 25 line-unit + 33 `test_line_spec_*`, 160 pass on the integrated tree (24376082, local emulation of `test.yaml`); on GitHub only the 127-test list has run (run 36360490678) |
| Reference independent of the RTL's structure | third-party protocol peers, vendored unmodified ([independent-peers.md](independent-peers.md)); protocol checks of the static timing analyzer ([timing-analysis.md](timing-analysis.md)) | no third-party peer; `line_std_ref.py`, written from the contract and public standards before the RTL was read: 159 standalone tests against published vectors; 40,997 of 41,000 random cases, the demos and the 17 probes match at RTL; the 3 other cases and one directed test differ on the LSTAT[13:8] point (section 6.6); published CRC check values and the `test/line_support.py` references (sections 6.2, 6.3) |
| Formal jobs in `formal/` | 16: 8 unbounded proofs, 4 bounded checks, 2 covers, 2 negative controls; all meet their expectations (formal/README.md, "What is verified"; `formal` run 36298635454) | 62: the same 16 on the variant and 46 line-unit jobs (11 unbounded proofs, 8 bounded checks, 8 covers, 19 negative controls); 62 of 62 meet their expectations (24307353, re-run 24311615; section 6.4) |
| Formal in CI | the 16 jobs (`formal.yaml`) | 58 jobs: the 16 and the 42-job line-unit subset; 58 of 58 met in local emulation (24309631; integrated tree 24376084); not yet run on GitHub (section 12.2) |
| Formal beyond `formal/` | `formal_depth/` deeper runs and properties ([formal-depth.md](formal-depth.md)); timing certificates, 368 of 368 segments of the 19 images at c118027 ([timing-certificates.md](timing-certificates.md)) | not run on the variant (section 8) |
| Negative controls of the formal properties | 2 (`timing_isolation_neg_pull`, `_neg_mutant`) | 2 + 19 line-unit controls over all 10 seeded defects (section 6.4) |
| Mutation campaign, sampling | 2,420 mutants of the whole core, drawn with region quotas and coverage weights (`gen_mutants.sh`) ([mutation-push.md](mutation-push.md)) | three uniform samples of the logic the option adds or changes: 3,000 of the unit's state logic, a held-out 1,000 of the same population and 2,117 of the rest; the 268,232 mutations of the statements downstream of line-unit state are not sampled (section 11.1) |
| Mutation score | 96.82 % (2,223 / 2,296); 97.93 % with the longer ABC cap ([mutation-push.md](mutation-push.md), "Result" and 4.5) | 92.98 % [91.99, 93.86] (in-sample for the new modules), 92.72 % [90.90, 94.19] held-out, 87.72 % [86.22, 89.09] second population; pooled 90.82 % [89.98, 91.60] (section 11.2) |
| Mutation accounting | killed / (mutants − proven equivalent); argued survivors stay in the denominator; 73 survivors, all argued, 0 neither proven nor argued (47 after the longer cap) ([mutation-push.md](mutation-push.md)) | the same formula and proof methods (ABC cap 900 s); clock-inversion "proofs" not counted; Wilson 95 % intervals; 76, 26 and 0 survivors neither proven nor argued; not a paired comparison with the design of record (sections 11.2, 11.4) |
| Test gap found by the other campaign | the four WAITEVENT blocked-count `cnot1` analogues pass all 102 tests (24370776, section 11.4); no fix on this branch | killed by `test_line_spec_limit_bits` (section 11.3) |
| Official gate level (`gl_test`) | 102 tests: 46 pass, 56 skipped by design, 0 fail (`gds` run 36298635436, [results.md](results.md) R85) | 127 tests: 66 pass, 61 skipped by design, 0 fail (run 36360490711, section 12.1); the integrated tree's 160 tests: 66 pass, 94 skipped, 0 fail on that netlist and on the antenna-fix netlist (24376083, local) |
| Other gate-level simulation | 10,752 random cases ([verification-campaign.md](verification-campaign.md)) | 15,872 random cases (section 6.5); `test_line_stdref` 1,999 of 2,000 random cases, the other on the LSTAT point (section 6.6); full-mode suites 127 tests: 71 pass, 56 skipped, 0 fail (24134621, 24307911) and on the integrated tree 160 tests: 102 pass, 58 skipped, 0 fail, 31 of them `test_line_spec_*` tests (24376083) |
| RTL-to-netlist equivalence | official 15 ns netlist equivalent, self-test passed (run 36298635436, job 24093884, [equivalence.md](equivalence.md) section 1); checked in CI by `equiv.yaml` ([equivalence.md](equivalence.md) section 7) | official netlist equivalent (24163332, section 12.1); antenna-fix netlist equivalent (24306936, section 7.2.1); `equiv.yaml` for branch builds not yet run on GitHub (section 12.2) |
| Official Tiny Tapeout actions | `gds`, `precheck`, `gl_test` pass on d76f1cc, antenna 0 ([results.md](results.md) R85) | `gds`, `precheck`, `gl_test` pass on ac436f7 with 1 antenna net (run 36360490711); the antenna change gives antenna 0 on the local mirror only (section 7.2.1) |
| Other official workflows | `test`, `formal`, `regen` pass on d76f1cc (runs 36298635420, 36298635454, 36298635381) | on ac436f7: `test` and `formal` pass (runs 36360490678, 36360490685; the `formal` workflow of that commit ran the design of record's jobs and settings); `regen` fails by design (36360490692); the design-selection workflows of section 12.2 not yet run |
| Timing at 15 ns (official flow) | setup WS slow +2.24 ns, hold WS min +0.165 ns (R85) | +2.07 ns and +0.166 ns (official, section 7.2); +2.21 ns and +0.166 ns with the antenna change (local, section 7.2.1) |
| Open deviations from the contract | no defect in the generated RTL found by these methods ([bug-ledger.md](bug-ledger.md)) | LSTAT[13:8] after SE0 in a trailing stuff cell (sections 6.4, 6.6); two isa.md wordings (section 8) |

**Where the variant's evidence is now stronger or equal.** More random
lockstep cases and cycles, with the same base bins covered; a line-unit bin
set that is fully hit; unbounded formal proofs for most line-unit properties
with a negative control each; a reference that does not share the RTL's
structure and found a deviation from the contract; a mutation campaign with
uniform samples, a held-out sample and confidence intervals.

**Where it is still weaker.** No third-party peer for the line protocols; no
timing certificates or `formal_depth/` runs; a lower mutation score than the
design of record's, with 76 and 26 survivors neither proven nor argued and a
second-population score that rests on one unproven property; the official
flow has run only once, without the antenna change, and none of the new
workflows has run on GitHub; one open deviation from the contract.

