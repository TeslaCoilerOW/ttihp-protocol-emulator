# Line-unit extension (`diet8_rec16`)

Status, 2026-09-27. The extension recommended by
[extension-study.md](extension-study.md) ("Conditional GO for `diet8` +
`REC16` at 8x4") is implemented in `hardcaml/` behind a default-off option
and generated as the variant `configs/variants/diet8_rec16.json`. **It is
not the design of record.** `src/protocol_emulator_core.v` still comes from
`configs/instruction-sram-32.json` and is byte-identical to the generator's
output without the option. Whether to adopt the variant is open (section 9,
decision date 2026-11-06).

Every result below names the Slurm job that produced it; the jobs are listed
in section 10. "PASS" means a check that was run and passed.

## 1. Summary

| Item | Result |
|---|---|
| What it adds | Per engine: a bit ticker with an 8-bit fraction, NRZ/NRZI/Manchester (TX) line coding, bit stuffing, a complementary pin pair with SE0 end, an arbitration monitor, a 16-bit CRC with four polynomial presets. Opcodes 30-33 (LTIM, LCFG, CRC, LSTAT) and two XFER mode bits (section 3). |
| Variant | `diet8_rec16` = the `diet4` options with 8-word queues (`diet8`, also added) plus `"line_unit": "rec16"`. ISA version 3; READ_SELECT 7 reads 0x000F5F03 (section 3.3). |
| Default configuration | Unchanged: `make check-generated` PASS; design-of-record formal RTL byte-identical to git `HEAD`'s; default cocotb suite 102/102 with the same per-test status and simulated time as a `HEAD` control; `formal/run.sh --list` unchanged; the 12 quick formal jobs meet their expectations (section 6.1). |
| RTL against the lockstep model | cocotb lockstep on every cycle (the model follows the RTL's structure; the independent checks are the references in the next row), CI-matching cocotb 2.0.1 and Icarus 13: 127/127 tests on `diet8_rec16` (the 102 default tests and 25 line-unit tests), and 512 constrained-random cases (3,701,150 cycles) without a mismatch (section 6.3). |
| Independent references | Nine catalogued CRC check values (CRC-16/IBM-3740 0x29B1, ARC 0xBB3D, USB 0xB4C8, CAN-15 0x059E, ...), bit-level line encoders and decoders, the ticker formula, and the study's three demos (10BASE-T UDP frame, CAN node, USB low-speed IN responder) decoded at the pads (sections 6.2, 6.3). |
| Formal | The 16 existing jobs on `diet8_rec16`, including the timing-isolation proofs with the line-unit state and LSTAT in the miter, meet their expectations. New line-unit properties (CRC reference, coder/decoder inverse, pin locality, reset/START, decode) with a negative control each (section 6.4). |
| Area | TT-replica synthesis (Yosys 0.66, AREA 0): 432,203.4 µm² and 3,903 flops for the core, +56,980.1 µm² and +272 flops over `diet8` (section 7). |
| Hardening at 8x4 | One full LibreLane run with the committed 15 ns configuration: flow complete, utilization 63.2% (design of record 65.3%), setup met at all three corners (WS typ +6.03, fast +7.38, slow +2.07 ns), hold met (WS min +0.166 ns), route DRC 0, LVS 0, one antenna violation. On that layout the Tiny Tapeout precheck passes 9/9, the gate-level suite has 0 failures, and the netlist is sequentially equivalent to the RTL (`formal_eq`) (sections 7.2, 7.3). The official Tiny Tapeout actions then passed on branch `eval/diet8-rec16` (run 36360490711: gds, precheck, gl_test) with a `metrics.csv` byte-identical to the local run's, and the official netlist is equivalent to the RTL (section 12.1). |

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
version must mask bits 7..0; `host/` does not do that yet (section 8).

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
| `hardcaml/lib/line_options.ml` | The option (`"line_unit": "none" | "rec16"`), read from the refinement's `"options"` object like `Timing_options`; validation (fused issue, 32-bit datapath, not with `split_instruction_decode`); the capability bits |
| `hardcaml/lib/line_unit.ml` | CRC presets and step function, and the seeded defects used by the negative controls |
| `hardcaml/lib/engine_line.ml`, `engine_decode.ml`, `engine_transfer.ml`, `engine.ml` | The unit (`Engine_line`: its registers and its part of the datapath), its validity rules (`Engine_decode`) and the line mode of `XFER` (`Engine_transfer`), built by `Engine.create` only with the option (`?line`); `?line_mutation` is used only by the formal generator and `line_test.exe`. Until `16cfc20` all of this was in `engine.ml` |
| `hardcaml/lib/processor.ml`, `refinement_config.ml`, `variant_options.ml` | Pass the option through; READ_SELECT 7; the debug export `dbg_queue_status_read` (LSTAT issue, formal only) |
| `hardcaml/lib/isa.ml`, `assembler.ml`, `bin/assemble.ml` | LTIM, LCFG, CRC, LSTAT and the XFER line/CRC bits, for targets with the option |
| `hardcaml/test/line_fw.ml`, `line_test.ml`, `line_sys_test.ml` | Engine-level and whole-chip checks ported from the study (executables). Since `4c0753c`, `dune test` also runs `line_test`; `line_sys_test` takes a config and is run separately (`hardcaml/test/README.md`) |
| `configs/variants/diet8.json`, `diet8_rec16.json` | The two new variants |
| `test/model/line_unit.py`, `test/model/variant.py` | Reference model of the unit (`LineReference`), selected only with the option |
| `test/line_support.py`, `line_scenarios.py`, `line_demos.py`, `line_random.py` | Encoders, independent references, scenarios, random generator |
| `test/test_line_unit.py`, `test_line_demos.py`, `test_line_random.py` | cocotb modules; skipped unless `PE_VARIANT` has the option; `test/Makefile` adds them for `PE_VARIANT=diet8_rec16` |
| `formal/line_crc.sv`, `line_codec.sv`, `line_engine.sv`, `line_ref.vh`, `*.sby` | Line-unit properties; registered in `formal/run.sh`, and their `.sby` files derived by `formal/variant_sby.py`, only for a variant with the option |
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

### 6.2 Hardcaml checks (ported from the study)

| Check | Result | Job |
|---|---|---|
| `line_test.exe`: engine level, 18 unit checks (CRC check values of seven catalogued CRCs, USB-LS TX and RX, CAN TX and arbitration, 10BASE-T Manchester) and 13 firmware checks (10BASE-T UDP frame and link pulse, CAN node TX/RX/arbitration/minimum spacing and the revision-2 negative control, USB IN responder) | 31/31 PASS | 24119789 |
| `line_sys_test.exe configs/variants/diet8_rec16.json`: whole chip on 8-word queues, no host service after START: staging capacities, 10BASE-T frame with one relay and its under-staged negative control, five CAN scenarios and two CAN negative controls, reset in the middle of a CAN frame (80 line-unit registers read 0), USB IN responder | 16/16 PASS | 24120022 |
| Seeded defects (`line_test.exe --mutation`) | 7 of 10 detected: `crc_tap` (9 failing checks), `stuff_run` (7), `fraction_carry` (6), `rx_destuff_run` (4), `nrzi_decode` (4), `manchester_halves` (2), `arbitration_off` (2). Not detected here: `pin_leak`, `start_keeps_crc`, `ltim_overflow`; the formal negative controls target them (section 6.4) | 24120021 |

### 6.3 cocotb (RTL, lockstep with the reference model)

cocotb 2.0.1 and Icarus Verilog 13.0, the CI versions. The harness compares
`uo_out`, `uio_out` and `uio_oe` with `test/model/line_unit.py` before and
after every edge.

The lockstep model is not independent of the RTL: it implements the contract
of [isa.md](isa.md) but was written together with the RTL and follows the
RTL's structure step for step (`engine.ml` until `16cfc20`, `engine_line.ml`
after it), so a misreading of the contract that
both share would not show up as a mismatch. The independent checks are the
published CRC check values and the references in `test/line_support.py`
(line encoders and decoders, the behavioural CAN node, the USB low-speed host
with its own CRC5, the Ethernet decoder with CRC-32) against which the tests
decode the pads, and the ticker timing formula.

| Module | Tests | Result | Job |
|---|---|---|---|
| `test_line_unit` | 12 directed tests: version word; nine CRC check values; CRC from classic and sampling XFERs; ticker timing for ten (P, Q, D) sets; 11 TX line-coding cases; 11 RX decoding cases (destuffing, stuff errors, SE0 end with remaining count, CRC of sampled bits); arbitration; 43 encodings (invalid ones fault with code 1, valid neighbours complete); LSTAT; START/STOP/reset; ticker sharing; completion points | 12/12 PASS | 24134619 |
| `test_line_demos` | 10BASE-T UDP frame with one relay (76 bytes decoded, CRC-32 residue 0xDEBB20E3, TP_IDL 12 cycles, first link pulse 639,998 cycles later, 4 cycles wide) and its under-staged negative control; CAN TX + RX at the minimum spacing (DLC 8/5 and 0/4/1), back to back with arbitration won, arbitration lost (fault 72), the revision-2 listing (fault 71) and RX overflow (fault 4) negative controls, reset mid-frame; the node's ACK of each received frame (dominant in the ACK slot, recessive at both delimiters, dominant nowhere else in the frame); USB IN responder (turnaround 3.02 bit times); USB tokens with a wrong CRC5, OUT and SETUP ignored, then an IN answered with its EOP (SE0 66 cycles, J 33 cycles, then released); an IN to another address and endpoint also answered (no filter); CRC stream | 12/12 PASS | 24134619 |
| `test_line_random` | 16 random cases per run | PASS | 24134619 |
| Random campaign 1 | 16 runs × 16 cases (seeds 0x11AE0000 + 1000 i + k), 1,848,487 cycles after START | 256/256 cases, 0 mismatches | 24121515 |
| Random campaign 2 | 16 runs × 16 cases (seeds 0x22BE0000 + 1000 i + k), 1,852,663 cycles | 256/256 cases, 0 mismatches | 24125398 |
| Full suite with `PE_VARIANT=diet8_rec16` (default Makefile module list: 102 default + 25 line-unit tests) | 127/127 PASS | 24134619 |
| Full default suite with `PE_VARIANT=diet8` | 102/102 PASS | 24121514 |

Functional coverage of random campaign 1 (events counted by the model;
campaign 2 has the same event classes with similar counts):
7,302 line XFERs and 245 faulting ones, 1,087 classic XFERs (801 CRC feeds),
3,875 stuff bits sent (569 trailing), 6,238 removed, 5,567 stuff errors,
1,154 arbitration losses, 282 SE0 ends, 9,181 Manchester second halves,
3,759 LTIM, 2,365 LCFG, 5,054 CRC and 1,516 LSTAT instructions, and 609
faults on invalid line-unit encodings (68 CRC, 110 LCFG, 47 LSTAT, 40 LTIM,
99 classic XFER, 245 line XFER).

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

**What the line-unit properties exclude.** `line_crc_e2e`: P = 1 only, 1-10
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

## 7. Area and timing

### 7.1 TT-replica synthesis

`docs/notes/area-study/scripts/synth3.sh ll66` (the LibreLane 3.1.0.dev3 "AREA 0"
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
| Antenna | **1 violating net** (`net12189` into a repair buffer: Metal2 cumulative area ratio 253.34 against 200); 25 diodes | 0 |
| Max slew / cap / fan-out violations | 2 (slow) / 1 (fast) / 3 | |
| Magic illegal overlaps | 86 (the macro's, as in R18) | |
| Global-routing overflow | 0 | |
| Power (typ) | 21.2 mW | |

The core that was hardened is `d517e277…`, published as
`<local work dir>/tt-work/variants/cores/diet8_rec16.v` and listed in that
directory's `MANIFEST.sha256`, together with `diet8.v` (`5fb364f2…`), the area
reference of section 7.1. Precheck and gate-level
results on this layout are in section 7.3.

### 7.3 Precheck and gate level on the hardened layout

| Check | Result | Job |
|---|---|---|
| Tiny Tapeout precheck (tt-support-tools d66cf179e `precheck.py`, unmodified, on the final GDS and LEF with the repository's `info.yaml`; `drc-triage/scripts/precheck.sbatch`, KLayout 0.30.9 from the LibreLane image instead of the action's 0.30.4) | 9/9 checks pass (pin label overlap, SG13CMOS5L DRC, zero area, KLayout checks, pin check, boundary, layer, cell name, analog pin); exit 0 after 333 s | 24131046 |
| Gate-level cocotb on the final `nl.v` (`make GATES=yes PE_VARIANT=diet8_rec16`, cocotb 2.0.1, Icarus 13, the PDK 2bbec755 cell models) | Test tree before the review additions: 125 tests, 64 pass, 61 skipped by design at gate level, 0 fail. Of the 23 line-unit tests 18 pass and the 5 CAN demos are skipped | 24131047 |
| The line-unit modules at gate level with `PE_LINE_GL_FULL=1` (CAN demos and the 10BASE-T link pulse included) | 23/23 pass (10BASE-T frame and link pulse after 660,000 cycles, all CAN scenarios and negative controls, USB, random), 871 s | 24131401 |
| The whole suite at gate level on the final test tree with `PE_LINE_GL_FULL=1` (127 tests, including the ACK and USB checks added after review) | 127 tests: 71 pass, 56 skipped by design at gate level (all in the default modules), 0 fail; all 25 line-unit tests pass; 992 s | 24134621 |
| RTL-to-netlist sequential equivalence (`formal_eq/eq_check.py check --run-dir <run> --variant diet8_rec16 --core <diet8_rec16 core> --top src/project.v --selftest`; Yosys 0.67 and ABC `dprove`; `formal_eq/` unmodified at `75ac8af`) of the final `nl.v` against the `diet8_rec16` RTL behind `src/project.v` | **equivalent**; the two netlist mutants are found not equivalent and every recipe control meets its expectation (`selftest.pass`); 153 s | 24134508 |

## 8. What is not verified

- **No silicon, no FPGA, no bench.** Electrical requirements of the stretch
  protocols (USB-LS edge rates, 10BASE-T magnetics and levels, CAN transceiver)
  are not addressed (study R7).
- **The references are ours.** The CRC check values are published values;
  the line encoders, the CAN node, the USB host and the Ethernet decoder are
  behavioural models written for these tests, not conformance testers or real
  controllers. CAN was not tested against another CAN controller, with clock
  offsets, error frames or resynchronization (study R6).
- **Formal bounds and exclusions.** The coder/decoder and end-to-end CRC
  properties are bounded and restricted as listed in section 6.4 (P = 1 or 2,
  up to 8 or 10 data bits, no pair and no short stuffing runs in the CRC
  property, no Manchester with stuffing and no fraction in the codec
  property); the CRC step equivalence is unbounded. The line-unit properties
  are engine-level; the whole-chip properties are the existing jobs.
- **USB firmware scope.** `usb-ls-in-responder` answers an IN token to any
  address and endpoint (tested); it ignores tokens with a wrong CRC5 and non-IN
  PIDs (tested with OUT and SETUP). Handshakes, data toggles and every other
  USB transaction type are outside it.
- **CAN firmware scope.** Data frames only: no remote, error or overload
  frames, no retransmission after arbitration loss, no resynchronization, no
  error counters, a fixed 50% sample point (study R6).
- **Mutation testing** of the unit with the cocotb suite was not run (section 11).
- **Host library.** `host/` does not know the capability bits. Its self-test
  expects READ_SELECT 7 to be one of its known ISA versions and would report
  0x000F5F03 as a mismatch; its image loader compares with `>=` and so also
  loads a line-unit image on a device without the unit, which then faults with
  code 1 at the first line-unit instruction. Images keep `isa_version` 2.
- **Timing knobs.** The option is rejected together with
  `split_instruction_decode`; the other timing knobs were not combined with it.
- **One variant.** Only `diet8_rec16` was generated and verified; the study's
  6x4 fallback (`diet2` + `REC16`) and other combinations were not.
- **Official build on a branch, not on main.** The layout, precheck and
  gate-level results were first obtained with the local mirror of the Tiny
  Tapeout flow, then confirmed by the official actions on branch
  `eval/diet8-rec16` (section 12.1). The variant is not the design of record,
  so no official build of it exists on `main`. The one antenna violation of
  section 7.2 was not repaired; it is present in the official build as well
  (`antenna__violating__nets` 1), and the design of record's official build
  has none.

## 9. Open decision

Adopt `diet8_rec16` as the design of record, or keep the design of record and
the variant as it is. Due 2026-11-06 (the study's go/no-go date). Adopting it
means:

- the diet options come with it: asynchronous reset with synchronous release,
  no debug counters, a 7-bit PC, byte-lane shifts, ISA version 3
  ([notes/variants.md](notes/variants.md) section 4);
- `src/protocol_emulator_core.v` becomes the variant core, and CI must select
  the variant (section 12);
- the host library needs the capability-bit check (section 8);
- the timing and routing margin of section 7.2 applies.

The study's other open item, R1 (the host cannot service queues while the chip
clock free-runs), still limits every demo to what the queues and relay engines
hold per session.

## 10. Reproduction and jobs

Work directory: `<local work dir>/tt-work/ext-rec16/` (not in the repository),
with `manifest.json` (every Slurm job), `jobs/` (job scripts), `snap/` and
`fsnap/` (the snapshots the jobs ran on, with `SOURCES.sha256`), `results/`
(cocotb logs and `results.xml`), `formal/` (formal RTL and per-job logs),
`synth/`, `sweep/` and `harness/` (the LibreLane run), `axle/` (the
arithmetic checks below).

**Arithmetic.** The numbers derived in this page (register and state widths,
the version word, the ticker examples, area and flop differences, the
placement-model percentages, run totals, fmax estimates, the runtime estimate,
the antenna ratio) and the nine CRC check values (with a Lean model of the
parametrised CRC) were checked with the AXLE Lean engine (lean-4.28.0): the
independent verifier's `axle/ext_arith.lean` (37 theorems) and the author's
`axle/ext_arith_author_part1.lean` to `part3.lean`, each `okay: true`
(check records `*.check.json` next to them).

```sh
scripts/gen_variants.sh diet8 diet8_rec16                    # cores, firmware, checks 1-4
cd test && make PE_VARIANT=diet8_rec16                        # 127 tests
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
| 24125085 | formal, snapshot f2: 31 variant jobs and 12 design-of-record quick jobs | section 6.4 |
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

## 11. Mutation testing (not run)

The Hardcaml seeded defects (section 6.2) and the formal negative controls are
single, hand-chosen defects. A mutation campaign over the unit, as
`campaigns/mutation/` does for the design of record, would need: the campaign
driver pointed at the `diet8_rec16` core instead of `src/`; a mutant generator
restricted to the line-unit logic (its registers are named `line_*`,
`stuff_*`, `arbitration_lost`, `crc_*`, and it can be located structurally as
`formal/gen/generate_fv.ml` does); the test list extended with
`test_line_unit`, `test_line_demos` and `test_line_random` and
`PE_VARIANT=diet8_rec16`; and a survivor classification for the new logic.
`campaigns/` is outside this work.

## 12. Running the official GitHub actions on a branch

To have the Tiny Tapeout `gds`, `precheck` and `gl_test` actions build and test
the variant, a branch needs (relative to `main`):

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

