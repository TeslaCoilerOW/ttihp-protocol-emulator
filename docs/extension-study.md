# Protocol-extension study (plan task 3.1 prep)

Revision 3, 2026-09-25. Scope: the smallest ISA and hardware additions that let
this four-engine processor run the competition's stretch protocols (USB 2.0
low-speed, 10BASE-T transmit with link pulses, CAN), plus fractional-period
timing. It prepares the go/no-go decision due 2026-11-06.

Nothing here is in the design of record. The prototype is in a scratch copy of
`hardcaml/`, outside this repository (section 10). "PASS" below means a check
that was run and passed; everything else is labelled as an estimate or a sketch.

**What changed in revision 3.** A verification pass found that the CAN node sketch could not receive a frame that another node starts at the legal minimum spacing after the node's own frame (section 7.2). Revision 2's tests had started the other node's frames 2–3 bits later than the minimum, which hid the bug. Revision 3:
- ends both CAN frame tails at the middle of the 2nd intermission bit and delays a pending transmission to the end of intermission, at the same 62/63/64 words;
- replaces the scripted CAN bus at both test levels with a behavioural node that starts frames at the minimum spacing or in the 3rd intermission bit, arbitrates and acknowledges;
- adds back-to-back transmission, arbitration won and lost against a simultaneous SOF, and DLC 0, 1 and 4 receptions;
- keeps the revision-2 listing as a negative control, which fails at the minimum spacing;
- runs the whole-chip checks with each configuration's own reset style (asynchronous resets emulated as in `test/variant_test.ml`) and adds a mid-frame reset check;
- corrects minor errors in sections 1, 5 and 7.3.

**What changed in revision 2.** Revision 1 simulated the firmware sketches
with unlimited queues: an endless TX supply and an RX queue drained every cycle.
The recommended variants have 8-deep (`diet8`) or 2-deep (`diet2`) queues. The
host cannot service them while the chip clock free-runs (risk R1). Revision 2
therefore:
- adds a whole-chip simulation with the real queue depths and no host service
  after START (section 5.2);
- packs received CAN bytes into words;
- makes the preamble for 10BASE-T on chip;
- measures the staging capacity of mover relays;
- narrows the recommendation for 6x4 (sections 1 and 9).

In revisions 2 and 3 the RTL did not change, so the area results in section 6 still apply.

## 1. Bottom line

| Question | Answer |
|---|---|
| What is the minimal extension? | One "line unit" per engine: a free-running bit ticker with an 8-bit fraction, NRZ/NRZI/Manchester coding, programmable bit stuffing, a complementary pin pair with SE0 detection, a CAN arbitration monitor, and a 16-bit CRC with four polynomial presets. It uses 4 new opcodes (30–33) and one new XFER mode bit (section 4). This is the `REC16` configuration. |
| Does the prototype work? | In simulation, yes. **Engine level:** every check PASS on all four feature sets: 27 on each CRC16 set and 28 on each CRC-32 set (section 5.1). **Whole chip:** with `REC16`, 16 checks PASS on each of `base`, `diet8`, `diet4` and `diet2`, using the real queue depths, each configuration's own reset style, and no host service after START (section 5.2). The whole-chip runs cover the three firmware sketches; CAN at the minimum frame spacing, back to back, and with arbitration won and lost; measured staging capacities; a reset in the middle of a frame; and three negative controls (under-staging, RX overflow, the revision-2 CAN listing) that fail as intended. All 6 seeded RTL bugs are caught. Evidence: job 23789566, with its log. |
| Area | `REC16` adds **+69.0K µm²** of TT synthesis (+272 flops) to the design of record, or about +81K µm² placed. The full unoptimized set with a programmable CRC16 (`FULL16`) adds +79.3K. CRC-32 instead of CRC16 adds +12.6K with presets (`REC32` − `REC16`) or +23.4K with a programmable polynomial (`FULL32` − `FULL16`) (section 6). |
| Fit at 8x4 | Design of record + `REC16`: model **65.1%** (D 61.1%). That is far above the only routed point (58.4%, D 53.7%), so **no**. `diet8` + `REC16` is **55.4%** (D 50.4%); `diet8` is the diet4 options with 8-deep queues. `diet4` + `REC16` is **46.7%** (D 40.8%). |
| Fit at 6x4 | `diet4` + `REC16`: 62.6% (D 56.7%), so no. `diet2` + `REC16`: **56.2%** (D 49.4%), at the edge of the area study's D ≤ ~50% budget. On 2 of the 4 engines: **52.8%** (D 45.4%). On `diet2` each stretch demo also needs 2–4 of the 4 engines as queue relays (section 7.6). |
| Clock | **40 MHz** is the best single clock. With the fraction, 10BASE-T is exact (2 cycles per half-bit), the USB-LS rate is within 100 ppm, CAN is exact, and slow-corner register paths gain about 5 ns over 50 MHz (section 3). |
| Recommendation | **Conditional GO for `diet8` + `REC16` at 8x4.** All three stretch demos were simulated there on 8-deep queues with no host service after START. **6x4 (`diet2` + `REC16`) is insurance only.** It fits at the edge of the area budget, but one stretch protocol runs at a time, 10BASE-T uses all four engines, and frames are limited to 68 bytes. **NO-GO for USB-LS enumeration** unless the host port becomes usable while the chip clock free-runs (R1). R1 also sets the per-session limits of section 7.6, so decide it by 2026-11-06. |

## 2. What the stretch protocols require

Sources:
- [UNH-IOL 10BASE-T MAU test suite v4.1](https://www.iol.unh.edu/sites/default/files/testsuites/ethernet/CL14_MAU/MAU_Test_Suite_v4.1.pdf), which quotes IEEE 802.3 clause 14.
- [fpga4fun 10BASE-T recipe](https://www.fpga4fun.com/10BASE-T3.html).
- [USB communications](https://en.wikipedia.org/wiki/USB_communications) and the [Infineon USB 2.0 KBA](https://community.infineon.com/t5/Knowledge-Base-Articles/USB2-0-Explained-With-Captures-and-Traces/ta-p/773150), which cite USB 2.0 §7.1.
- Bosch CAN 2.0 / ISO 11898-1 for CAN.

Values marked † were not read from the primary specification text in this study; check them before the go/no-go. The USB ones are the usual USB 2.0 Table 7-9/7-10 figures. The 10BASE-T jitter limits were not confirmed: the UNH and Microchip PDFs could not be text-extracted here, and secondary summaries disagree (±11 ns and ±20 ns).

### 2.1 USB 2.0 low-speed (device side)

| Item | Requirement | What the line unit provides |
|---|---|---|
| Data rate | 1.50 Mb/s ± 1.5% (USB 2.0 §7.1.11) | Ticker P = 16 with fraction 171/256 at 50 MHz (rate −78 ppm). P = 13, Q = 85 at 40 MHz (rate +98 ppm). P = 8 exact at 24 MHz; P = 16 exact at 48 MHz. |
| Line code | NRZI: 0 = transition, 1 = no transition | `LCFG` code 1 |
| Bit stuffing | A 0 after six consecutive 1s, before NRZI, including just before EOP. Seven 1s is a receive error. | Stuff run 6, ones only. RX drops stuff bits and flags a wrong one. |
| SYNC / PID | KJKJKJKK (00000001, LSB first); PID is 4 bits plus their complement | Ordinary line XFERs |
| CRC | Token CRC5 x⁵+x²+1, init 11111, residual 01100. Data CRC16 x¹⁶+x¹⁵+x²+1, init FFFF, residual 800D. Both are sent complemented, LSB first. | Presets 0 and 1 (reflected 0x14, 0xA001). The reflected residues 0x06 and 0xB001 were checked. |
| EOP | SE0 for about 2 bits, then J. LS source EOP 1.25–1.50 µs†; receiver accepts SE0 ≥ 670 ns†. | `SET` both low; `LCFG` SE0-end ends an RX XFER and records the remaining bits |
| Turnaround | Packets ≥ 2 bit times apart; a device answers within 6.5 bit times (TRSPIPD1); host timeout 16–18 bit times† | Measured 2.99 bit times in the IN responder (section 7) |
| Electrical | D+/D− single-ended receivers plus a differential pair. LS device: 1.5 kΩ pull-up on D− to 3.0–3.6 V. VOH ≥ 2.8 V, VOL ≤ 0.3 V†. LS edges 75–300 ns†. Host 15 kΩ pull-downs. | Pair mode drives D− = ¬D+, and `SET` drives SE0. The IHP CMOS5L pads are 3.3 V cells (PDK `sg13cmos5l_io_typ_1p2V_3p3V`). Edges are ~1–9 ns into 1–10 pF, far faster than LS, so an external series R/C is needed for compliance. |
| Bus management | Keep-alive: an LS EOP every 1 ms. Suspend after 3 ms idle. Reset is SE0 ≥ 10 ms, recognised after 2.5 µs†. | Firmware. A bounded wait faults after ≤ 2²⁴ cycles, 0.42 s at 40 MHz (R5). |

### 2.2 10BASE-T transmit

| Item | Requirement | Provision |
|---|---|---|
| Coding | Manchester, 10 Mb/s. The first half of each cell is the complement of the bit, the second half the bit. Half-bits are 50 ns, a 20 MHz edge rate; an all-ones or all-zeros run is a 10 MHz square wave. | `LCFG` code 2. The second half is applied by the line unit at the mid tick, with no instruction. |
| Frame | Preamble 7 × 0x55, SFD 0xD5, then 64–1518 bytes from destination to FCS. FCS is CRC-32 (reflected 0xEDB88320, init and xorout FFFFFFFF, residue 0xDEBB20E3). IFG 9.6 µs. | Preamble and SFD are made on chip (section 7.1). FCS comes from the host, or from the `REC32` option (+12.6K µm²). The frame streams from the TX queue, staged through mover relays (section 7.1). |
| Timing | Zero crossings at 8.0/8.5 BT within a jitter limit (UNH tests 14.1.10/11)†: ±11 ns with the twisted-pair model is the figure used here; a secondary summary gives ±20 ns. | Exact at 40 MHz. At 50 MHz (P = 2, Q = 128) half-bits alternate 40/60 ns, a deterministic ±10 ns. |
| TP_IDL | Starts with the last positive transition, stays positive for "about 3 bit times" (fpga4fun), then silence of 0 ± 50 mV (UNH 14.1.1). Template in 802.3 fig. 14-10. | Firmware `SET`: 300 ns positive, then both pins low |
| Link pulses | NLP about 100 ns (fig. 14-12 template) every 16 ± 8 ms. Partners accept 2–7 ms minimum and 25–150 ms maximum spacing; link_loss is 50–150 ms. | Firmware `SET`/`WAIT`: 100 ns, 16.0 ms |
| Electrical | 2.2–2.8 V peak differential into 100 Ω, through 1:1 magnetics | Two pins in pair mode through a resistor network and a transformer, as in common FPGA practice. Not designed here. |

### 2.3 CAN 2.0A

| Item | Requirement | Provision |
|---|---|---|
| Coding | NRZ, dominant 0. A complement stuff bit after 5 equal bits from SOF through the CRC sequence; 6 equal bits is a stuff error. | Stuff run 5, either polarity. The stuff bit starts the next run. |
| CRC | CRC-15: x¹⁵+x¹⁴+x¹⁰+x⁸+x⁷+x⁴+x³+1 (0x4599), init 0, MSB first, over the unstuffed SOF..data | Preset 2 (0x4599 left-aligned). An MSB-first XFER selects the normal form. |
| Frame | SOF, ID 11, RTR, IDE, r0, DLC 4, 0–8 bytes, CRC 15, CRC delimiter, ACK slot, ACK delimiter, EOF 7, IFS 3 | Firmware (section 7.2) |
| Interframe space | Intermission is 3 recessive bits; no node may start a data frame during it. A frame that became pending during another frame starts in the first bit after intermission. A dominant bit sampled in the 3rd intermission bit is interpreted as SOF (Bosch CAN 2.0 Part B §10.3.4.1 and the note in §10.3.5.1; [NXP reprint](https://www.nxp.com/docs/en/reference-manual/BCANPSV2.pdf), pages 93–94). | Both frame tails end in the middle of the 2nd intermission bit, so `WAITPIN` is armed before the 3rd. A pending frame starts 150 cycles later, measured 7–10 cycles (0.07–0.10 bit) after the end of intermission. A pending frame does not join an SOF sent in the 3rd intermission bit (section 7.2). |
| Arbitration / ACK | A node that reads dominant while sending recessive in the arbitration field stops sending and receives. Receivers with a good CRC drive the ACK slot dominant. | `LCFG` arbitration: on the first loss the engine drives recessive for the rest of the frame and keeps receiving (section 5.1). The 64-word node sketch stops with fault 72 on a loss instead of retrying (section 7.2). Winning and losing against a simultaneous SOF are simulated on the whole chip (section 5.2). The node drives the ACK slot at the start of its RX tail XFER. |
| Bit timing | SYNC_SEG 1 tq, PROP_SEG 1–8, PHASE_SEG1 1–8, PHASE_SEG2 ≤ 8, SJW ≤ min(4, PS1). Hard sync on SOF, resync on recessive→dominant edges. | Hard sync by `WAITPIN` + `LTIM`. The sample point is fixed at 50%. There is no resync: with ±100 ppm crystals the drift over a 130-bit frame is ≤ 0.03 bit. |
| Electrical | External transceiver (3.3 V parts such as SN65HVD230 or TJA1051T/3) on TXD/RXD. A fault releases the output enables, so TXD needs a pull-up to stay recessive. | Logic pins only |

Error frames and error counters are not implemented. Firmware stops with an explicit fault code instead: 70 = no ACK, 71 = CRC error, 72 = arbitration lost.

## 3. Clock and I/O feasibility

| Clock | USB-LS | 10BASE-T | CAN 500k / 1M | Notes |
|---|---|---|---|---|
| 24–25 MHz | P = 8 exact at 24 MHz | not possible (P = 2 needs 40 MHz) | P = 25 / 12.5 at 25 MHz (1M needs Q = 128) | Only matters if a host that clocks the chip in lockstep (R1 (b)) is limited to about 25 MHz. Arithmetic only; not simulated at these clocks. |
| 40 MHz | P = 13, Q = 85: rate +98 ppm. Integer 27 cycles: rate −1.23%, inside ±1.5%. | P = 2, **exact** | P = 40 / 20 | Recommended single clock. At 50 MHz the slow corner had 2 failing register-to-register endpoints at −0.34 ns (`docs/hardening.md` §7), so 40 MHz gains ~5 ns. Not re-timed. |
| 48 MHz | P = 16, exact | P = 2, Q = 102: ±10.4 ns | P = 48 / 24 | |
| 50 MHz (`info.yaml`) | P = 16, Q = 171: rate −78 ppm. Integer 33 cycles: rate +1.0%. | P = 2, Q = 128: ±10 ns, nearly all of a ±11 ns budget† (half of a ±20 ns one) | P = 50 / 25 | Typical and fast corners met. At the slow corner (`docs/hardening.md` §7), setup WS is −5.09 ns over 1,345 endpoints, dominated by the `rst_n` path; 2 register-to-register endpoints also fail at −0.34 ns. |
| 60 MHz | P = 20 | P = 3, exact | P = 60 / 30 | Beyond the slow-corner register paths |

TT I/O figures are the Tiny Tapeout pages ([GPIO](https://tinytapeout.com/specs/gpio/), [clock](https://tinytapeout.com/specs/clock/)), which describe the sky130 pads:
- 66 MHz maximum input, 33 MHz maximum output;
- about 20 ns mux round trip, with < 2 ns skew between pins;
- a demo-board clock of 1 Hz–66.5 MHz from the RP2040.

10BASE-T needs at most a 10 MHz square wave. The CMOS5L IO library's typical output transition is 1–9 ns into 1–10 pF, depending on drive strength. Nothing here has been measured on CMOS5L silicon.

## 4. Proposed ISA additions

These use the 32-bit format of [isa.md](isa.md) (op = [31:24], a, b, c, imm24) and unused opcodes 30–33. As in isa.md, unused operands must be zero and invalid encodings fault with code 1.

| op | mnemonic | operands / operation |
|---:|---|---|
|17|XFER (line mode)|c bit5 = 1 selects line mode. a is the bit count 1..width; b = 0; c[1:0] = 0; c2 MSB first; c3 drive; c4 sample; c6 feeds the data bits to the CRC; c7 = 0. The ticker must be running. Manchester requires c4 = 0. The TX pin carries data and the RX pin is sampled. With `LCFG` pair set, the PINS clock field names the complementary pin. c6 is also legal in classic (SPI) XFERs.|
|30|LTIM|imm24: [7:0] half-period P, where 0 stops the ticker; [15:8] fraction Q/256; [23:16] first-tick delay D, where 0 means P. It (re)starts the engine's ticker. The first tick is a bit boundary D cycles after issue. Ticks then alternate mid-bit and boundary every P cycles, plus 1 cycle whenever the 8-bit phase accumulator (+Q per tick) carries.|
|31|LCFG|imm24: [1:0] code: 0 NRZ, 1 NRZI, 2 Manchester (TX only), 3 invalid. [2] stuff enable. [3] stuff on runs of either polarity (CAN), else runs of 1s (USB, HDLC). [6:4] run length − 1. [7] pair. [8] arbitration monitor. [9] end an RX XFER on SE0 (both pair pins low). [10] initial line level and previous sample. [23:11] zero. It resets the line state and flags, but not the ticker or the CRC.|
|32|CRC|c = 1: CRC := reg b (a = 0). c = 2: reg a := CRC zero-extended (b = 0). c = 3: polynomial preset b: 0 CRC-5/USB 0x14, 1 CRC-16/USB 0xA001, 2 CRC-15/CAN left-aligned, 3 CRC-16/CCITT reflected 0x8408 (or 0xEDB88320 in a 32-bit build). A programmable build instead has c = 0: poly := reg b, which is +6.4K µm².|
|33|LSTAT|reg a := [0] SE0 seen, [1] arbitration lost, [2] stuff error, [3] TX queue has data, [4] RX queue has space, [5] line level, [6] ticker running, [13:8] data bits remaining when an XFER ended on SE0. b = c = 0.|

**Line-mode XFER timing** (as prototyped):
- **Drive only.** It starts at the next boundary tick and drives one bit per boundary. It completes, advancing the PC, at the boundary of its last data bit or trailing stuff bit. With P ≥ 2 the next line XFER has at least 3 cycles to issue before the next boundary, so `PULL; XFER; LOOP` streams words with no gap at 4 cycles per bit.
- **Manchester.** The second half-bit is applied at the mid tick by the line unit itself.
- **Sample only.** It samples at mid ticks and completes at the mid tick of its last bit, or at SE0. Samples shift into rx without clearing it, so consecutive byte XFERs accumulate up to one word (used by the CAN receiver).
- **Drive and sample.** It starts at a boundary and, like sample only, completes at the mid tick of its last bit. The CAN frame tails rely on this (section 7.2).

**Stuff bits, CRC and arbitration:**
- A stuff bit is inserted or removed when the run is reached, including after the last data bit of an XFER. Stuff bits are never shifted, counted or fed to the CRC.
- LSB-first XFERs use the reflected (right-shift) CRC and MSB-first XFERs the left-aligned normal form. That covers USB, Ethernet and CAN with no reflect flags.
- A drive-only XFER feeds the driven bits to the CRC; any sampling XFER feeds the sampled bits. So after arbitration loss the CRC follows the winner's frame. Feeding a frame's own CRC bits back leaves the register at 0 for CRC-15 (init 0), which the CAN sketch uses instead of a per-frame reset.
- With arbitration enabled, sampling 0 while driving 1 sets `lost`. The engine then drives 1 for all later data and stuff bits and keeps receiving.

**START, faults and state.**
- START clears all line-unit state: ticker, `LCFG`, flags, CRC, and the polynomial (preset 0).
- The prototype decodes these as invalid (fault 1):
  - `LCFG` code 3, or nonzero reserved bits;
  - a line XFER while the ticker is stopped;
  - Manchester with sampling;
  - a drive or pair pin outside ownership, or the pair pin equal to the TX pin;
  - an extension opcode, or an extension field, in a build without that feature.

  No check exercises these fault cases yet.

**Discovery by the host (proposal, not prototyped).** Today READ_SELECT 7 returns the ISA version: 2 for the design of record, or 3 when a variant option changes the ISA (`docs/variants.md`). Proposal:
- bits [7:0] keep that meaning;
- bits [23:8] become constant capability bits: [8] line unit; [9] fraction; [10] stuffing; [11] arbitration; [12] CRC16; [13] CRC-32; [14] CRC presets instead of a programmable polynomial; [19:16] which engines have the unit.

This costs only constant bits. Hosts must then mask [7:0] before comparing the version, which is a contract change to isa.md.

**What stays the same.** Classic XFER, `WAIT`, queue semantics and ownership rules are unchanged. Line-mode output goes through the engine's existing pin value and enable registers, so ownership, run and fault masking still apply. Line-mode output timing depends only on the engine's own instructions, its ticker, and the input pins, so the timing-isolation miter's assumptions (shared inputs, private image) still hold.

**Formal obligations for task 3.2 (not done):**
- After reset and after START, every line-unit register is zero.
- The engine-safety output-masking proofs hold with the new state, and the arbitration monitor forces only the engine's own TX pin.
- The invalid encodings above fault with code 1 and change nothing else.
- `LSTAT` bits 3–4 observe the queues, so `LSTAT` joins PULL/PUSH/WAITEVENT in the timing-isolation miter's exclusion list.
- Each CRC preset is equivalent to a bitwise reference, for bounded input length.
- The assembler's cycle-schedule check must model the ticker phase.

Other ISA items this study found. They cost no area and are not prototyped:
1. an unbounded-wait option for `WAITPIN`/`WAITEVENT`, because an idle CAN or USB bus otherwise faults after ≤ 2²⁴ cycles;
2. a non-blocking queue test. `LSTAT` bits 3–4 provide it here.

## 5. Prototype and verification

**Scratch copy.** It is `…/tt-work/extension/hc/` (section 10). It starts from a snapshot of the working-tree `hardcaml/` taken 2026-09-25 01:47. The variant options in that snapshot were committed afterwards in c377d97 (`docs/variants.md`, `configs/variants/`). The snapshot's 46 files differ from c377d97's `hardcaml/` in only two places: a validation bound in `variant_options.ml` (a 7-bit PC allowed up to 128 program words instead of 64) and `test/variant_test.ml`. Generated RTL for the 64-word configurations is unaffected. With `PE_EXT` unset, the scratch generator emits:
- 73536f0's `src/protocol_emulator_core.v` byte for byte, apart from that file's "generated by" header line;
- the `diet4` and `diet2` cores (`1c105a55…`, `342f26b8…`). These equal the published variant cores (variants.md §8, `cc27c465…` and `f885b0a0…`) apart from their variant header line, checked with `cmp`.

Evidence: job 23789566 (section 4 of its log), as before in 23777273.

**Knobs.** Features are selected at generation time:
- `PE_EXT` is a comma list of `line`, `frac`, `stuff`, `arb`, `crc16` or `crc32`, plus the optimizations `share` and `crcpre`.
- `PE_EXT_MASK` selects which engines get the unit.

**New state per engine.** `FULL16` adds 98 flops per engine:
- ticker: count 9, period 8, run, phase;
- fraction: 16;
- `LCFG`: 10 (bit 10, the initial line level, is applied when `LCFG` issues and never read afterwards, so synthesis drops that flop: "Removed top 1 bits (of 11) from wire line_cfg" in the `ll66` `FULL16` log);
- line state: 12;
- stuffing: 6;
- arbitration: 1;
- CRC and polynomial: 32;
- 2 XFER mode bits.

`REC16` adds 68 per engine. Its ticker reuses the XFER tick/period registers (a classic XFER stops the ticker), and the polynomial is a 2-bit preset.

### 5.1 Engine-level checks (`test/ext_test.ml`)

These run one engine in Hardcaml Cyclesim. The expected values come from encoders, decoders and bitwise CRCs written independently of the RTL (`test/ext_fw.ml`). Inputs pass through a modelled 2-cycle synchronizer. The CAN node checks use the behavioural bus of section 7.2. The program exits with status 1 if any check fails.

| Check | Result |
|---|---|
| CRC check values of "123456789": CRC-16/USB 0xB4C8, CRC-5/USB 0x19, CRC-15/CAN 0x059E, CRC-32 0xCBF43926 (32-bit build) | PASS |
| USB-LS DATA0 transmit at 50 MHz (P 16, Q 171): NRZI, destuff and byte decode match; maximum raw run of 1s is 6; mean bit 33.33 cycles ± 0.2%; SE0 63–75 cycles, then J | PASS (4 checks) |
| USB-LS receive with the host 0.25% fast: IN token (PID, address, endpoint, CRC5 residue 0x06, SE0 with 8 bits remaining); DATA0 with three stuffed runs (words, CRC16 residue 0xB001, no stuff error) | PASS (2 checks) |
| CAN at 500 kbit/s: frame on TXD equals the independent stuffing encoder with CRC-15. Arbitration against ID 0x120 on a modelled wired-AND bus: `lost` set, winner's 19-bit header received, TXD never dominant after the loss. | PASS (3 checks) |
| 10BASE-T at 40 MHz: 128 Manchester cells of 4 cycles across 4 queued words with no gap; pair complementary; TP_IDL | PASS (2 checks) |
| Firmware (section 7), engine level. 10BASE-T: UDP frame, TP_IDL, NLP. CAN node: TX with ACK; no-ACK fault 70; RX of a DLC-8 frame and then a DLC-5 frame sent in the 3rd intermission bit, into an RX queue bounded at 8 (6 packed words), with ACK in each ACK slot only; arbitration loss against a frame started in the same cycle, fault 72; TX then RX at the minimum spacing (DLC 0 in the 3rd intermission bit, then DLC 4); two frames back to back, the 2nd winning against a simultaneous SOF that is then retransmitted and received; RX, then a TX that became pending during it. USB IN responder: packet and turnaround. | PASS (12 checks) |
| Negative control, engine level: the revision-2 CAN listing at the minimum spacing after its own frame | fails as intended (fault 71); counted as a 13th firmware check |

Totals, job 23789566:
- 27 checks with `crc16` (14 unit + 13 firmware) and 28 with `crc32` (15 + 13);
- all PASS on each of `FULL16`, `FULL32`, `REC16` and `REC32`, all exit 0.

Measured CAN start times (all four feature sets): the 2nd SOF of a back-to-back pair comes 10 cycles after the end of intermission, and a TX that became pending during a reception starts 7 cycles after it (bit = 100 cycles).

Program sizes are reported separately and are not counted as checks. In the engine-level 10BASE-T check the TX queue is unbounded, standing in for the relay chain; section 5.2 runs it on real queues.

**Negative controls** (FULL16, job 23789566; same counts as revision 2's job 23777273). Each seeded RTL mutation made checks fail, while the unmutated suite had 0 failures of 27. Under the stuff-run mutation the CAN node suite raises an exception (`index out of bounds` while sampling a frame that is too long), which counts as one failure and skips its remaining checks, so that run reports 20 checks.

| Mutation | Failing checks |
|---|---:|
| stuff run off by one | 7 |
| Manchester halves swapped | 2 |
| arbitration monitor disabled | 2 |
| fraction carry ignored | 6 |
| wrong CRC feedback tap | 7 |
| NRZI decode inverted | 4 |

### 5.2 Whole-chip checks on bounded queues (`test/sys_test.ml`)

The whole chip (`Processor.create_refinement_model`) runs in Cyclesim with `REC16`:
- four engines;
- the host nibble port;
- queues of the configuration's depth;
- the mover;
- the 64-word store model.

The run has three phases:
1. **Setup.** Through the host port, the host loads programs, sets routes, starts relay engines and prefills queues.
2. **Free run.** The host STARTs the protocol engine and then performs no host transaction, which models the free-running clock of R1. The pin environment acts on `uio`: the behavioural CAN node of section 7.2, a USB host, an Ethernet decoder.
3. **Read-back.** The host reads status and RX queues again.

All the config's options apply, including its reset style: `sync` for `base` and `async_sync_release` for the diet configurations (revision 2 forced `sync`). Cyclesim applies asynchronous resets only through `Cyclesim.reset`, so the harness emulates them exactly as `test/variant_test.ml` does: the raw reset clears every register as soon as it is asserted and at every edge while asserted, and with `async_sync_release` the two synchronizer flops keep clocking while the other registers are held. The mid-frame reset check reads every line-unit, CRC and transfer-mode register by name (80 in `REC16`, 20 per engine).

Not done: a lockstep equivalence of the extended cores across reset styles, as `variant_test` does for the cores without the extension (section 9, next steps).

| Check (per configuration) | `base` (8) | `diet8` (8) | `diet4` (4) | `diet2` (2) |
|---|---|---|---|---|
| Staging capacity, measured, engine 0 plus 0/1/2/3 relays (words) | 8 / 25 / 42 / 59 | 8 / 25 / 42 / 59 | 4 / 13 / 22 / 31 | 2 / 7 / 12 / 17 |
| 10BASE-T UDP, 76-byte frame (17 queued words + on-chip preamble): decoded, no gap, CRC-32 residue; TP_IDL; NLP 16.0 ms later; engine still running | PASS, 1 relay | PASS, 1 relay | PASS, 2 relays | PASS, 3 relays |
| Negative control: one relay fewer; the host stages what fits and starts anyway | PASS: 8 of 17 staged; frame breaks after them | PASS: 8 of 17 | PASS: 13 of 17 | PASS: 12 of 17 |
| CAN: TX of an 8-byte frame (exact, ACKed), then RX of DLC 8 at the end of intermission and DLC 5 in the 3rd intermission bit; 6 packed words read back; no fault, no arbitration, no collision | PASS | PASS | PASS, RX drain relay | PASS, TX relay and RX drain relay |
| CAN: the same with DLC 0 (3rd intermission bit), DLC 4 (end of intermission) and DLC 1 (3rd intermission bit); 5 words | PASS | PASS | PASS, RX drain relay | PASS, TX and drain relays |
| CAN: 2 frames back to back, 2nd SOF 10 cycles after the end of intermission; the other node's simultaneous SOF loses at stuffed bit 2, retransmits at the end of intermission and is received (3 words) | PASS | PASS | PASS, TX relay | PASS, TX and drain relays |
| CAN: the 2nd frame loses to a simultaneous SOF at stuffed bit 1: fault 72, TXD recessive from that bit on, the winner's frame completes | PASS | PASS | PASS, TX relay | PASS, TX relay |
| Negative control: the revision-2 CAN listing at the minimum spacing | PASS: fails with fault 71 | PASS: fault 71 | PASS: fault 71 | PASS: fault 71 |
| Negative control, RX at the minimum spacing without drain or host service: fault 4 | PASS: 3rd frame (9 words) | PASS: 3rd frame | PASS: 2nd frame (6 words) | PASS: 1st frame (3 words) |
| Reset asserted for 2 cycles in the middle of a CAN frame: all 80 line-unit/CRC/transfer-mode registers read 0 (7 were nonzero before), TXD released, engine 0 idle, no fault | PASS (`sync`) | PASS (`async_sync_release`) | PASS (`async_sync_release`) | PASS (`async_sync_release`) |
| USB IN responder: DATA1 + 8 bytes + CRC16, turnaround 2.99 bit times | PASS | PASS | PASS | PASS, 1 relay |

Every configuration had 16 checks, all PASS, exit 0 (job 23789566). Revision 2's 11 checks per configuration (job 23777273) are superseded: its CAN checks used the later frame spacing and forced `sync` reset. The measured capacity equals d + r(2d + 1) in every case: each relay holds its TX queue, its RX queue and one word in its `rx` register.

## 6. Area

The RTL is unchanged since revision 1 (only the tests and firmware changed), so these synthesis results still apply.

**Flow.** `docs/area-study/scripts/synth3.sh`, unchanged:
- `ll66` is the LibreLane 3.1.0.dev3 "AREA 0" replica with the SIF's Yosys 0.66;
- `plain` is the study's revision-1 flow;
- both use the typical liberty, SRAM macros as black boxes, and the core module.

**Model.** U and D use the area study's model (§2.4):
- `placed = 1.100·S + 19.05·F`;
- `U = (placed + 121,923.6)/core` and `D = placed/(core − 121,923.6)`.

The only routed calibration point is the design of record at 58.4% (58.5% after routing). Every other U/D is a model prediction. Differences under ~5K µm² are synthesis noise: for example `LSTAT` alone came out −5.0K in `plain`, and its `ll66` run hung in ABC and was cancelled.

**Jobs.** The per-core results are in `synth/runs/<flow>/<core>/stat.txt`:
- base set: synthesis array 23755202 (cores from 23754994);
- optimized and diet set: 23763784 (cores from 23763137);
- `diet8`: 23764660.

The base, `cn`, `diet4` and `diet2` areas reproduce [variants.md](variants.md) §5 and [area-study.md](area-study.md) exactly: 462,171.9, 412,823.7, 295,845.4 and 254,425.7 µm².

### 6.1 Per feature (4 engines, on the design of record)

| Feature | Measured as | TT synth Δ (µm²) | plain Δ | Flops Δ | Placed Δ (model) | ΔU 8x4 | ΔU 6x4 |
|---|---|---:|---:|---:|---:|---:|---:|
| Line core: ticker, `LCFG`, NRZI/Manchester, pair/SE0, line XFER, `LSTAT` | `L` − base | +29.0K | +23.3K | +140 | +34.6K | +2.9 | +3.8 |
| Line core, ticker shares the XFER registers | `LSH` − base | +23.6K | +19.5K | +76 | +27.4K | +2.3 | +3.0 |
| Fraction (8-bit phase accumulator) | `LF` − `L` | +8.3K | +8.3K | +64 | +10.4K | +0.9 | +1.2 |
| Bit stuffing (insert/remove, run 1–8, two modes) | `LS` − `L` | +18.9K | +18.4K | +48 | +21.8K | +1.8 | +2.4 |
| CAN arbitration monitor | `LA` − `L` | −1.3K | +0.8K | +8 | ~0 (noise) | 0 | 0 |
| CRC16, programmable polynomial | `C16` − base | +19.4K | +17.3K | +132 | +23.9K | +2.0 | +2.7 |
| CRC16, 4 presets | `C16P` − base | +13.0K | +10.8K | +76 | +15.8K | +1.3 | +1.8 |
| CRC32, programmable | `C32` − base | +38.9K | +36.9K | +260 | +47.8K | +4.0 | +5.3 |
| **All, unoptimized, CRC16** (`FULL16`) | − base | **+79.3K** | +77.8K | +392 | +94.8K | +7.8 | +10.5 |
| All, unoptimized, CRC32 (`FULL32`) | − base | +102.8K | +100.1K | +520 | +123.0K | +10.2 | +13.6 |
| **Recommended: shared ticker, CRC16 presets** (`REC16`) | − base | **+69.0K** | +63.9K | +272 | +81.1K | +6.7 | +9.0 |
| `REC16` with CRC-32 presets (`REC32`) | − base | +81.6K | +76.6K | +336 | +96.2K | +8.0 | +10.7 |
| `REC16` without fraction (`MIN16`) | − base | +65.4K | +55.5K | +208 | +75.9K | +6.3 | +8.4 |
| `FULL16` on engines 0–1 only (`FULL16_2E`) | − base | +42.4K | +37.3K | +196 | +50.4K | +4.2 | +5.6 |

On the diet variants, `REC16` adds +55–60K of TT synthesis and 272 flops. On two engines it adds +28–30K and 136 flops.

The prototype was written for clarity, not area. Stuffing in particular (4.7K per engine) is a candidate for hand optimization.

### 6.2 Resulting utilization

| Configuration | Queue depth | TT synth (µm²) | Flops | U 8x4 | D 8x4 | U 6x4 | D 6x4 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Design of record (`base`) | 8 | 462,171.9 | 3,921 | 58.4 (routed: 58.5) | 53.7 | 78.1 | 74.7 |
| base + `REC16` | 8 | 531,210.8 | 4,193 | **65.1** | 61.1 | 87.1 | 85.1 |
| base + `FULL16` | 8 | 541,520.6 | 4,313 | 66.2 | 62.4 | 88.6 | 86.8 |
| `cn` + `REC16` | 8 | 472,534.8 | 4,097 | 59.6 | 55.0 | 79.8 | 76.6 |
| `diet8` (diet4 options, FIFO 8) | 8 | 375,223.3 | 3,631 | 50.0 | 44.4 | 66.9 | 61.7 |
| **`diet8` + `REC16`** | 8 | 430,385.7 | 3,903 | **55.4** | **50.4** | 74.2 | 70.2 |
| `diet8` + `REC16`, 2 engines | 8 | 403,307.5 | 3,767 | 52.8 | 47.4 | 70.6 | 66.0 |
| `diet4` | 4 | 295,845.4 | 2,583 | 41.1 | 34.5 | 55.0 | 48.0 |
| `diet4` + `REC16` | 4 | 352,973.5 | 2,855 | 46.7 | 40.8 | 62.6 | 56.7 |
| `diet4` + `REC16`, 2 engines | 4 | 323,928.5 | 2,719 | 43.9 | 37.6 | 58.7 | 52.3 |
| `diet4` + `REC32` | 4 | 361,610.6 | 2,919 | 47.6 | 41.7 | 63.8 | 58.1 |
| `diet4` + `FULL16` | 4 | 364,725.4 | 2,975 | 48.0 | 42.2 | 64.3 | 58.7 |
| `diet2` | 2 | 254,425.7 | 2,047 | 36.5 | 29.4 | 48.8 | 40.9 |
| **`diet2` + `REC16`** | 2 | 310,399.3 | 2,319 | 42.0 | 35.5 | **56.2** | **49.4** |
| **`diet2` + `REC16`, 2 engines** | 2 | 284,309.5 | 2,183 | 39.4 | 32.6 | **52.8** | **45.4** |
| `diet2` + `FULL16` | 2 | 321,947.0 | 2,439 | 43.3 | 36.9 | 57.9 | 51.3 |

Reading against the area study's budgets:
- 8x4: D ≈ 54% is the edge of routability.
- 6x4: D ≤ ~50%, because the macro bands block 74% of the width.

Options:
- `diet8` + `REC16` keeps the queue depth and sits 3.3 points of D below the 8x4 routed point.
- `diet4` + `REC16` has a comfortable 8x4 margin, but its 4-deep queues cost relays (section 7.6).
- At 6x4 only `diet2` + `REC16` (at the budget) or its 2-engine form fits.

The diet option set is: reset `async_sync_release`, FIFO storage reset, narrow image registers, no debug counters, a saturating 7-bit PC, and byte-lane shifts (SHL/SHR by 0/8/16/24 only). All firmware in section 7 uses byte-lane shift counts. `diet8` is that set with `fifo_words` 8. It exists only in the scratch work directory (`configs/variants/diet8.json` there), not in this repository's `configs/variants/`.

## 7. Firmware sketches

Assembly follows [firmware.md](firmware.md) mnemonics. Line-mode flags are written `line|drive|sample|msb|crc`. The simulated listings are exactly the instruction lists in `test/ext_fw.ml`, and both test levels use them. Word counts are for the 64-word store.

**Queue model.** The host prefills queues while it clocks the chip, then the clock free-runs and the host is absent (R1). Section 5.2 simulates exactly this.

### 7.1 10BASE-T UDP transmitter: 34 words, simulated on real queues

Setup: 40 MHz clock, TD+ on pin 0, TD− on pin 1. The frame from destination to FCS (68 bytes, 17 queue words) comes from the host; the preamble and SFD are made on chip.

```
        PINS    clk=1, tx=0, rx=0      ; clock field = pair pin (TD-)
        LCFG    MANCHESTER|PAIR
        LTIM    2                      ; 50 ns half-bits at 40 MHz
        SET     0b00                   ; silence (no differential)
        DIR     0b11
idle:   LSTAT   x
        LOAD    y, 8                   ; TX queue has data?
        AND     x, y
        JZ      x, nlp
        LOAD    tx, 0x5555             ; preamble and SFD, made on chip
        MOV     x, tx
        SHL     x, 16
        OR      tx, x                  ; tx = 0x55555555
        LOAD    y, 0x8000
        SHL     y, 16
        MOV     x, tx
        OR      x, y                   ; x = 0xD5555555 (SFD in the top byte)
        COUNT   16                     ; 17 frame words
        XFER    32, line|drive         ; 4 x 0x55
        MOV     tx, x
        XFER    32, line|drive         ; 3 x 0x55, SFD
word:   PULL
        XFER    32, line|drive         ; LSB first; no gap between words
        LOOP    word
        WAIT    2                      ; to the next cell boundary
        SET     0b01                   ; TP_IDL: positive 300 ns
        WAIT    9
        SET     0b00
        JMP     gap
nlp:    SET     0b01                   ; link pulse, 100 ns
        WAIT    2
        SET     0b00
gap:    WAIT    639990                 ; 16.0 ms
        JMP     idle
```

**Staging.** Engine 0's TX queue alone holds d words. Each relay engine adds 2d + 1, because it holds its TX queue, its RX queue and one word in `rx`. The host routes relay k's RX queue to engine k − 1's TX queue and writes the frame into the last relay. Measured capacities are in section 5.2.

The relay is 4 words:

```
relay:  PULL
        MOV     rx, tx
        PUSH                           ; blocking
        JMP     relay
```

| Queue depth | Relays needed for the 68-byte frame | Largest frame per staging (destination..FCS) with 0 / 1 / 2 / 3 relays |
|---:|---:|---|
| 8 (`base`, `diet8`) | 1 | 32 / 100 / 168 / 236 bytes |
| 4 (`diet4`) | 2 | 16 / 52 / 88 / 124 bytes |
| 2 (`diet2`) | 3 (all four engines) | 8 / 28 / 48 / 68 bytes |

A legal frame is at least 64 bytes. So `diet2` can send only frames of 64–68 bytes (UDP payload ≤ 22 bytes), and only with every engine busy. Each staging sends one frame; the next frame needs the host again.

**Simulated results.** The 76-byte frame decodes with CRC-32 residue 0xDEBB20E3, and TP_IDL and the first NLP 16.0 ms later appear:
- engine level, TX queue unbounded;
- whole chip on all four depths, with 1, 1, 2 and 3 relays.

The under-staged negative control breaks the frame right after the staged words (section 5.2).

**Cycle budget.** A drive-only XFER completes at the last bit's boundary. The next XFER then issues within the 3 cycles before the next boundary: after `MOV` (preamble), or after `LOOP` and `PULL`. That leaves 0 cycles spare at 40 MHz. Repeating the frame by pushing each sent word back into the chain would need one more cycle, so it is not supported.

### 7.2 CAN 2.0A node: 62 words with presets (63 programmable CRC16, 64 programmable CRC32), simulated on real queues

Setup: 500 kbit/s at 50 MHz, TXD on pin 0, RXD on pin 1.
- **Transmit** when the TX queue has a frame: a header word, then 8 data bytes in 2 words.
- **Otherwise** wait for SOF and receive.
- **Received frames** push a header word (SOF..DLC in its low 19 bits; the upper bits are stale), then the data packed into at most 2 words. When DLC > 4, the first word holds the first DLC − 4 bytes and the second the last 4. The valid bytes of each word are its low 8·n bits, MSB first.

```
        PINS    clk=2, tx=0, rx=1
        SET     1                      ; recessive
        DIR     1
        LIMIT   0xFFFFFF
        CRC     preset=2               ; CRC-15/CAN
        NOT     y                      ; y = -1 (START cleared x, y and the CRC)
idle:   LCFG    NRZ|STUFF5|EITHER|ARB|INIT1
        LSTAT   x
        LOAD    tx, 8                  ; TX queue has data?
        AND     x, tx
        JZ      x, rx
        LTIM    50, delay=150          ; SOF 150 cycles on: just after intermission
        PULL                           ; SOF..DLC in bits 31..13
        XFER    19, line|drive|sample|msb|crc
        LSTAT   x
        LOAD    tx, 2                  ; arbitration lost?
        AND     x, tx
        JZ      x, tx_ok
        FAULT   72                     ; header consumed: the host requeues
tx_ok:  PULL
        XFER    32, line|drive|sample|msb|crc
        PULL
        XFER    32, line|drive|sample|msb|crc
        CRC     get=tx
        SHL     tx, 16
        XFER    15, line|drive|sample|msb|crc  ; CRC; feeding it back leaves CRC = 0
        LCFG    NRZ|INIT1                      ; stuffing off
        LOAD    tx, 0xFFFF
        SHL     tx, 16
        XFER    12, line|drive|sample|msb      ; delimiter, ACK slot, delimiter, EOF, 2 of 3 IFS bits
        MOV     x, rx
        LOAD    tx, 0x400                      ; ACK slot bit
        AND     x, tx
        JZ      x, idle                        ; dominant = acknowledged
        FAULT   70                             ; no ACK
rx:     WAITPIN 1, 0                           ; SOF (hard sync)
        LTIM    50, delay=1
        XFER    19, line|sample|msb|crc
        PUSH    strict                         ; header
        MOV     x, rx
        LOAD    tx, 15
        AND     x, tx                          ; x = DLC
byte:   JZ      x, crc
        XFER    8, line|sample|msb|crc         ; bytes accumulate in rx
        ADD     x, y                           ; x -= 1
        LOAD    tx, 4
        XOR     tx, x
        JZ      tx, flush                      ; 4 bytes left: push the first group
        JZ      x, flush                       ; last byte: push
        JMP     byte
flush:  PUSH    strict
        JMP     byte
crc:    XFER    15, line|sample|msb|crc
        LCFG    NRZ|INIT1
        XFER    1, line|sample|msb             ; CRC delimiter
        CRC     get=x
        JZ      x, ack                         ; residue 0: good, and the CRC is reset
        FAULT   71                             ; CRC error
ack:    LOAD    tx, 0x7FFF
        SHL     tx, 16
        XFER    11, line|drive|sample|msb      ; ACK dominant, delimiter, EOF, 2 of 3 IFS bits
        JMP     idle
```

**Frame spacing (fixed in revision 3).** A drive-and-sample line XFER completes at the mid-bit of its last bit; a drive-only XFER completes at the start of its last bit (section 4).
- **The revision-2 bug.** Revision 2 ended the TX tail with `XFER 14`, drive and sample, which completes in the middle of the first bit after intermission. That is exactly where a node with a pending frame sends its SOF. `WAITPIN` then caught such an SOF half a bit late, the bit grid slipped by half a bit, and the node faulted 71. The verification pass found this (its jobs 23781683 and 23783254). Revision 2's own tests had started the other node's frames 16 bits after our CRC and 15 bits after each other's CRC; the minimum is 13.
- **The fix.** Both tails now end in the middle of the 2nd intermission bit: `XFER 12` after a TX, and `XFER 11` after an RX (now drive and sample instead of drive only). From there `WAITPIN` is reached within 10 instructions, half a bit before the 3rd intermission bit, the earliest point at which another node's dominant bit counts as SOF.
- **Pending frames.** A pending frame waits for `LTIM`'s 150-cycle first-tick delay, so its SOF comes just after intermission: measured 10 cycles after it for back-to-back frames and 7 cycles after it following a reception.
- The word counts are unchanged.

**Test bus.** Both test levels use one behavioural other node (`can_bus` in `test/ext_fw.ml`) on a wired-AND bus with a 5-cycle transceiver loop, at 100 cycles per bit. It works as follows:
- It starts each of its frames once the bus has been recessive for 11 bits since the last dominant bit (the end of intermission, the minimum spacing) or for 10 bits (the 3rd intermission bit).
- It samples at mid-bit. Reading dominant while sending recessive up to the RTR bit loses arbitration; it then releases the bus and retries under the same rule.
- It acknowledges our frames unless it is transmitting.
- It records any other collision or bit error, and a recorded error fails the check.

**Queue budget.** Each transmitted frame takes 3 TX words. Each received frame pushes 1 + (DLC > 0) + (DLC > 4) ≤ 3 words. Without host service (the whole-chip runs send 1 frame, or 2 back to back, and check every RX limit below):

| Queue depth | TX | RX before fault 4 |
|---:|---|---|
| 8 | 2 frames direct | 2 frames |
| 4 | 1 frame direct | 1 frame of more than 4 bytes; 2 frames with a drain relay |
| 2 | needs a TX relay | the first frame of more than 4 bytes already faults; with a drain relay, 7 words (2 frames) |

The whole-chip runs in section 5.2 check the frames and the overflow faults at each depth.

**Simulated results** (engine level on four feature sets, whole chip on four configurations; job 23789566):
- A frame with ID 0x2A5 and 8 data bytes goes out exactly as the independent stuffing and CRC-15 encoder predicts; the tail is recessive and the ACK is seen. Without an ACK the engine faults with code 70 (engine level).
- It receives frames at the minimum spacing after its own frame and after a received frame, including SOFs in the 3rd intermission bit, with DLC 8, 5, 4, 1 and 0. It pushes exactly the packed words and drives the ACK only in each ACK slot.
- Two queued frames go out back to back, the 2nd SOF 10 cycles after the end of intermission. A TX that became pending during a reception starts 7 cycles after the end of intermission (engine level).
- Against a simultaneous SOF, with the lower ID (0x155 against 0x3A5) it wins; the other node retransmits at the end of intermission and the frame is received. With the higher ID (0x7A5 against 0x120) it loses at the first differing bit, faults 72 and never drives dominant after the lost bit, and the winner's frame completes. At engine level it also loses to 0x120, started in the same cycle, while sending 0x123.
- The revision-2 listing fails the minimum-spacing case at both levels (fault 71), so the tests detect the bug.

The longest path between two XFERs is 9 instructions (the byte loop with a push), against a 100-cycle bit. From the end of a frame tail to `WAITPIN` is at most 10 instructions, against the 50 cycles left before the 3rd intermission bit. Those are hand counts; the simulations confirm that no bit is missed.

**Limits.**
- **No retry.** The store is nearly full at 62 words, so the sketch does not retry after arbitration loss. It stops with fault 72, and the host requeues the frame and restarts. A node that saves the header, retries, and receives the winning frame needs about 69 words by hand count: more than one engine's store, or the 128-word store option.
- **No bus-idle check before the first transmission after START.** A START while another node's frame is on the bus collides with it. The host should START the node on an idle bus. Waiting for 11 recessive bits would cost about 5 words by hand count, more than the 2 left in the preset build.
- **A pending frame does not join an early SOF.** CAN lets a node with a pending frame treat a dominant bit in the 3rd intermission bit as SOF and send its identifier from the next bit. This node starts its own SOF at the end of intermission instead, one bit behind, so the two frames would collide (not simulated). By estimate, arbitration still works while another node's SOF leads ours by less than about 0.4 bit, because the node samples at the middle of its own bit grid; only a lead of 0.1 bit (the simultaneous-SOF cases) was simulated. The 3rd-intermission-bit SOF is simulated only when no frame of ours is pending.
- Remote frames, error and overload frames, and extended IDs also need a second engine.

### 7.3 USB-LS IN responder: 51 words with presets (54 programmable), simulated on real queues

Setup: 50 MHz, D+ on pin 0, D− on pin 1. The engine receives a token, checks CRC5 and PID = IN, waits for the end of EOP, and sends the DATA1 packet queued by the host (SYNC/PID word, 8 bytes, CRC16 computed by the unit).

```
        PINS    clk=1, tx=0, rx=0      ; D- = pair pin
        LIMIT   0xFFFFFF
        CRC     preset=0               ; CRC-5/USB
rx:     LCFG    NRZI|STUFF6|PAIR|SE0END|INIT1
        WAITPIN 0, 1                   ; SOP: J -> K
        LTIM    16, frac=171, delay=33 ; mid-bit sampling of SYNC bit 1
        XFER    7, line|sample         ; rest of SYNC
        XFER    8, line|sample         ; PID
        LOAD    x, 0x1F
        CRC     set=x
        XFER    16, line|sample|crc    ; ADDR, ENDP, CRC5
        XFER    8, line|sample         ; ends at SE0
        CRC     get=x
        LOAD    y, 6                   ; CRC5 residue
        XOR     x, y
        JZ      x, crc_ok
        JMP     rx
crc_ok: MOV     x, rx
        SHR     x, 8
        LOAD    y, 0xFF
        AND     x, y
        LOAD    y, 0x69                ; IN
        XOR     x, y
        JZ      x, is_in
        JMP     rx
is_in:  WAITPIN 1, 1                   ; J after SE0
        LCFG    NRZI|STUFF6|PAIR|INIT0
        LTIM    16, frac=171, delay=95 ; >= 2 bit times of idle
        SET     0b10                   ; J
        DIR     0b11
        LOAD    x, 0xFFFF
        CRC     set=x
        CRC     preset=1               ; CRC-16/USB
        PULL
        XFER    16, line|drive         ; SYNC + PID
        PULL
        XFER    32, line|drive|crc
        PULL
        XFER    32, line|drive|crc
        CRC     get=x
        NOT     x
        MOV     tx, x
        XFER    16, line|drive         ; CRC16
        WAIT    31
        SET     0b00                   ; SE0 1.32 us
        WAIT    64
        SET     0b10                   ; J
        WAIT    31
        DIR     0                      ; release (pull-up holds J)
        CRC     preset=0
        JMP     rx
```

**Simulated results** (engine level, and whole chip on all four depths):
- The DATA1 packet decodes exactly: SYNC, PID 0x4B, 8 bytes and the CRC16 from the independent reference.
- SOP comes **2.99 bit times** (~100 cycles) after the EOP's SE0→J transition. The allowed window is 2–6.5 bit times. 95 of those cycles (2.85 bit times) are the deliberate `LTIM` delay, so the decision path itself takes about 5 cycles. The margin to the 6.5-bit limit is about 3.5 bit times (~117 cycles).

**Queue budget.** Each IN response takes 3 TX words. Without host service, 8-deep queues serve 2 responses, 4-deep 1, and 2-deep 2 with one relay. That is arithmetic from the capacities; the whole-chip runs simulate one response, with one relay at depth 2.

### 7.4 USB-LS HID device: sketch, not simulated

Ownership of D+/D− is exclusive, and handshakes must be sent within 6.5 bit times. So the sketch splits the serial interface engine over two engines and the mover.

**R: receiver.** It owns no pins and is estimated at ~50 words. For each packet it:
- samples SYNC and PID through line XFERs;
- chooses the CRC5 or CRC16 preset from the PID;
- receives data 32 bits at a time until SE0, using `LSTAT` to get the remaining bit count;
- checks the residue.

It then pushes a command word (ACK, NAK, SEND n or FORWARD n), followed by any data. Its RX queue is routed by the mover to T's TX queue. The host feeds IN data (packet images) into R's TX queue, and R forwards them on an IN token.

**T: transmitter.** It owns D+/D−, starts with `PULL`, and is estimated at ~25 words. Handshakes are one constant 16-bit XFER. SEND n streams n words with the §7.3 transmit tail. FORWARD n copies received words to T's RX queue for the host.

**Budget.** R decides after SE0 (≤ 15 instructions), the mover moves 1–4 words in 1–4 cycles, and T pulls and starts in ≤ 10 cycles plus one bit of `LTIM` delay. That is well inside the 216-cycle (6.5-bit) window at 50 MHz, by the same arithmetic as the simulated §7.3 path.

**Enumeration is not on-chip.** It needs the host to answer SETUP requests: device descriptor 18 bytes, configuration 34 bytes, HID report descriptor ~50 bytes, SET_ADDRESS and SET_CONFIGURATION. With the queue budgets of §7.3, the host must refill queues between transactions while the chip clock runs at 24–50 MHz (risk R1).

### 7.5 Summary

| Firmware | Words | Status | Tightest budget |
|---|---:|---|---|
| 10BASE-T UDP + TP_IDL + NLP | 34 (+ 4 per relay engine) | simulated on real queues, PASS | 3 of 3 cycles between words at 40 MHz |
| CAN node (TX with ACK check, back to back; RX at the minimum frame spacing with packed words and ACK; arbitration-loss stop) | 62 / 63 / 64 | simulated on real queues, PASS | 64-word store; ≤ 10 instructions from a frame tail to `WAITPIN` in half a bit; ≤ 9 between XFERs per 100-cycle bit |
| USB-LS IN responder | 51 / 54 (+ 4 for a relay) | simulated on real queues, PASS | turnaround 2.99 bits (window 2–6.5) |
| USB-LS SIE receiver R | ~50 | sketch | 33-cycle bit, SE0 decision |
| USB-LS SIE transmitter T | ~25 | sketch | |

### 7.6 Engines per demo without host service

| Demo | 8-deep (`base`, `diet8`) | 4-deep (`diet4`) | 2-deep (`diet2`) |
|---|---|---|---|
| 10BASE-T, one 64–100-byte frame per staging | 2 engines (≤ 100 bytes; ≤ 236 with 4) | 3 engines (≤ 88 bytes) | 4 engines (≤ 68 bytes) |
| CAN node, 1 TX frame and 2 RX frames per session | 1 engine | 2 engines (drain relay) | 3 engines (TX relay, drain relay) |
| USB IN responder, 2 responses per session | 1 engine | 2 engines | 2 engines |

On 8-deep queues each stretch demo leaves two or three engines free for the flagship protocols. On `diet2` a 10BASE-T demo takes the whole chip.

## 8. Public competitors (claims as of each repository head, 2026-09-25)

| | [tt_um_loom](https://github.com/thomasgilbert481/tt_um_loom) (`c100634d4`) | [MarcosAsh/protocol-emulator](https://github.com/MarcosAsh/protocol-emulator) (`2062b10aa`) | [kaikino/core-asic](https://github.com/kaikino/core-asic) (`00e5951db`) | This design + `REC16` |
|---|---|---|---|---|
| Tiles, clock | 6x4, 50 MHz | 6x4, 50 MHz | 8x4, 40 MHz; 69% at sign-off | 8x4 (6x4 insurance). 40 MHz recommended. |
| Execution | 4 threads on one barrel pipeline | 2 cores, 16-bit ISA | 2 PIO engines, 16-bit microprograms | 4 independent engines, private fetch, mover |
| Line coding | NRZ/NRZI/Manchester, USB and CAN stuffing, DIFF pin pair ("M3 slice A") | Manchester pin-pair mode, stuffing and CRC in config registers | Bit-banged (`shout_*`), 2 instructions per Manchester half-bit at 40 MHz | NRZ/NRZI/Manchester, stuffing run 1–8 in two modes, pair + SE0, arbitration monitor |
| CRC | 16-bit programmable per thread; CRC-32 not provided (host computes FCS) | CRC in config | 1 shared 32-bit reflected unit; FCS mode appends the CRC-32 | 16-bit per engine with presets; `REC32` option |
| Fractional timing | 16.8 fixed-point tick; deadline-latched pin writes | Deadline `t`, period `p` with fraction; static analyser bounds every edge | 1 instruction per clock; 0.22 ns TDC/DTC | 8-bit fraction on the line ticker; committed XFER timing |
| USB-LS | Planned stretch | Keyboard + mouse enumeration in the OCaml test suite (protocol model, lockstep with the RTL) | DATA TX, token capture, IN → DATA response (simulation) | IN responder simulated on the whole chip; enumeration needs host service (R1) |
| 10BASE-T | "10 Mbit Manchester is not a target" (D-029) | UDP transmit in tests; frame builder | Frame with FCS, receiver, link pulses | UDP frame + TP_IDL + NLP simulated on the whole chip at 40 MHz; FCS from host; one staged frame per session |
| CAN | Planned stretch | Not listed | Frame TX with stuffing, CRC-15, ACK | TX with ACK check and back-to-back frames; RX at the minimum frame spacing with packed words and ACK; arbitration won and lost against a simultaneous SOF (simulated on the whole chip, against a behavioural node) |
| Host port | SPI | SPI slave, SCK ≤ clk/8 (oversampled, usable while cores run) | SPI mode 0, ≤ clk/8 | Synchronous nibble port; the host supplies every clock (R1) |
| Frame/descriptor storage | Instruction memory via LD/ST | 512-word shared data SRAM with autopull | 128-byte FIFO | 8-word queues on `diet8` (2 on `diet2`); relay chains stage up to 59 words (236 bytes) on 8-deep queues, 17 words (68 bytes) on 2-deep |

The competitors that claim USB enumeration or full Ethernet frames have at least one of two things this design lacks:
- storage for descriptors or frames: MarcosAsh has a 512-word data SRAM, kaikino a 128-byte FIFO;
- a host link that works while the cores run: all three use an SPI port sampled by the core clock.

With `REC16`, line coding and CRC reach parity. On 8-deep queues the relay chain matches kaikino's 128-byte FIFO, but only by occupying engines. The live host link does not reach parity.

The differentiators in the plan's Differentiation section (four concurrent engines, the mover, the non-interference proof, assembler schedules) are unaffected. The mover relay (§7.1) and the two-engine SIE (§7.4) are ways to use them for the stretch protocols.

## 9. Go/no-go recommendation

**Minimal feature set (`REC16`).** It adds +69.0K µm² of TT synthesis and 68 flops per engine:
1. line XFER mode with a ticker that shares the XFER registers;
2. the 8-bit fraction;
3. NRZ/NRZI/Manchester, TX only for Manchester;
4. programmable stuffing;
5. pair drive with SE0 end-of-RX;
6. the arbitration monitor (free within noise);
7. CRC16 with the four presets;
8. `LSTAT`.

Leave out:
- CRC-32 (`REC32`, +12.6K): the host computes the Ethernet FCS, as loom does.
- Manchester RX: 10BASE-T receive is not a stretch target.
- CAN resync and error frames.

**Decision:**
- **GO for `diet8` + `REC16` at 8x4** (model 55.4%, D 50.4%). CAN, 10BASE-T transmit and the USB-LS IN responder all ran on the whole chip with 8-deep queues and no host service after START (job 23789566). Each demo occupies 1–2 engines (section 7.6), and the per-session limits of section 7.6 apply until R1 is resolved.
- **6x4 insurance, `diet2` + `REC16`** (model 56.2%, D 49.4%). It is functionally demonstrated (the same checks PASS on 2-deep queues), but only as a degraded fallback:
  - its area is at the edge of the 6x4 budget;
  - 10BASE-T takes all four engines, with frames of 64–68 bytes;
  - CAN takes three engines, USB two;
  - so stretch demos cannot run alongside the flagship protocols.

  Do not plan the competition demo around it.
- **NO-GO** on the design of record (65.1%) or on `cn` (59.6%, D 55.0%) without further area recovery. `diet4` + `REC16` fits 8x4 easily (46.7%) but needs 2–3 engines per demo.
- **USB-LS:** GO for packet-level demos (IN responder, packet capture). NO-GO for HID enumeration unless R1 is resolved by 2026-11-06.

**Risks:**

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | The host port is synchronous: the RP2040 supplies every clock edge (`docs/info.md`; the host library in [host.md](host.md) clocks manually, then PWM-clocks, stops and drains). While the clock free-runs the host cannot service queues. | Blocks USB enumeration and host-fed streams. It sets the per-session limits of §7.6: one Ethernet frame per staging, and 2 CAN frames or 2 USB responses on 8-deep queues. It also affects the flagship bridging demo whenever queues need service during free-running operation. | (a) An asynchronous host handshake mode: 2-flop synchronizers on `ui`, toggle-based write/read strobes, and a toggle acknowledge on `uo`. Estimated ~30 flops, ≈2–3K µm²; not prototyped; it changes the host contract. (b) A Pico PIO host that clocks the chip and drives nibbles in lockstep. Its rate is estimated at only ~25–33 MHz, which is enough for CAN (P = 25 at 25 MHz) and USB-LS (P = 8 at 24 MHz) but not for 10BASE-T (needs 40 MHz), so 10BASE-T always needs on-chip staging. |
| R2 | Small storage and no data memory. Queues are 8 words on `base`/`diet8`, 4 on `diet4` and 2 on `diet2`. | Measured staging capacity with 0–3 relays is 8/25/42/59 words at depth 8, 4/13/22/31 at depth 4 and 2/7/12/17 at depth 2. On `diet2` a 64-byte Ethernet frame needs all four engines; a CAN frame needs a TX relay, and a received 8-byte frame faults without a drain relay. USB descriptors cannot live on chip. | Prefer 8-deep queues (`diet8`). Use relay firmware (4 words per engine, measured). Or add a payload SRAM: `payload_sram.ml` exists, and a 2 KiB `1P_512x32` is 79.7K µm² of macro, 416.64 × 191.34 µm; floorplan impact not studied. |
| R3 | Area and routability predictions come from a model with one calibration point, and synthesis noise is ±5K | Extended variants may not route | Harden `diet8`+`REC16` and `diet2`+`REC16` on Slurm before the go/no-go |
| R4 | No STA on extended netlists. New paths include pin mux → NRZI/destuff → CRC XOR → register. | Possible 50 MHz slow-corner loss | Run TT-flow STA. Prefer 40 MHz. |
| R5 | Bounded waits fault after ≤ 2²⁴ cycles (0.34 s at 50 MHz) | Idle CAN or USB buses fault | Add an unbounded-wait option (§4) or re-arm loops |
| R6 | CAN: 50% sample point, no resync, no error or overload frames, no counters. In 64 words: no retry after arbitration loss, no bus-idle check before the first TX after START, and a pending frame does not join an SOF sent in the 3rd intermission bit (section 7.2). | Not ISO-conformant. Simulated only against a behavioural node (minimum frame spacing, 3rd-intermission-bit SOFs, simultaneous SOFs), not against a real controller or with clock offsets. | State it in the datasheet. Test against a real controller (MCP2515 or an MCU), including back-to-back traffic from both sides. START the node on an idle bus. A retrying or bus-checking node needs a second engine or the 128-word store. |
| R7 | Electrical: USB-LS edges too fast without an RC; 10BASE-T needs magnetics and a resistor network; CAN TXD needs a pull-up because faults release outputs; IO skew on CMOS5L unmeasured | Interop | Bench validation with a PHY and a USB analyzer |
| R8 | Verification debt for task 3.2: Python model, OCaml assembler (new mnemonics and ticker-aware schedules), productionized RTL behind `Variant_options`, cocotb peers, formal obligations (§4), ISA version and capability bits (§4), reset-style lockstep of the extended cores (§5.2) | Schedule to 2026-12-18 | Port `ext_fw.ml`'s references first; they already serve as independent models. `sys_test.ml` is the template for cocotb whole-chip checks. |

**Next steps (not done here):**
1. Decide on R1 (host-contract change).
2. Harden `diet8`+`REC16` (8x4) and `diet2`+`REC16` (6x4) with the TT flow, and run STA.
3. Add `diet8` to `configs/variants/`, and implement `REC16` as `Variant_options` fields in `hardcaml/`.
4. Extend `test/model/reference.py` and the assembler.
5. Port the §5 checks to cocotb peers and whole-chip scenarios.
6. Run `variant_test`'s reset-style lockstep on the extended cores. So far the whole-chip runs use each configuration's reset style and a mid-frame reset check (§5.2), but nothing compares reset styles cycle by cycle.

## 10. Reproduction

**Everything in sections 5 and 6 was run from the scratch work directory, not from this repository.** A clone of 73536f0, c377d97 or 0ec5138 contains none of the following:
- the extension RTL, its tests and the firmware listings as code;
- `diet8.json`;
- the generated cores and the synthesis runs.

Until task 3.2 lands the prototype in `hardcaml/`, these results can be rerun only from the paths below. Each path is identified by a hash, so a later import can be checked against it. The variant configs used here equal `configs/variants/` at c377d97, apart from the extra `diet8.json`.

Work directory: `<local work dir>/tt-work/extension/` (not in the repository). `manifest.json` records every Slurm job submitted for this study, with notes.

| Path | Contents |
|---|---|
| `hc/` | Scratch Hardcaml: snapshot + prototype. `hc-snapshot.sha256` hashes the snapshot. |
| `extension-prototype.patch` | RTL diff against the snapshot: `lib/engine.ml`, `lib/engine.mli`, `lib/processor.ml`. It also holds the revision-1 `test/dune` and `test/ext_test.ml`. SHA-256 `b6839467350fd3ba…`. The RTL is unchanged in revision 2. |
| `extension-tests-rev2.patch` | Revision-2 test changes against `rev1/`: `test/dune`, `test/ext_test.ml`, and the new `test/ext_fw.ml` and `test/sys_test.ml`. SHA-256 `decf2f09c6ae7f07…`. |
| `extension-tests-rev3.patch` | Revision-3 test and firmware changes against `rev2/`: `test/ext_fw.ml` (CAN tails, `can_bus`), `test/ext_test.ml`, `test/sys_test.ml`. SHA-256 `d7fa07150d7a08a5…`. The resulting files: `ext_fw.ml` `dd585996…`, `ext_test.ml` `1102a646…`, `sys_test.ml` `05a52c81…` (section 5 of the evidence log). |
| `rev1/`, `rev2/` | Earlier test files, kept for comparison |
| `configs/variants/` | Copies of the committed variant configs, plus `diet8.json` |
| `cores/ext/*.v`, `cores/ext/MANIFEST.sha256` | 40 generated cores. Examples: base `314d714c…` (= design of record), `REC16` `5c411c96…`, `diet8_REC16` `d7f1f43f…`, `diet2_REC16` `90d8fe0f…`. |
| `gen_tasks*.txt`, `env/gen_ext*.sh` | Variant name / `PE_EXT` / config / engine mask, and the generators |
| `synth/` | `synth3.sh` (from 73536f0), stubs, `tasks*.txt`, `runs/<flow>/<core>/stat.txt`, `summary.txt`, `summary.json` |
| `env/evidence_rev3.sh` | One script that runs everything in section 5, in parallel: build, engine checks on 4 feature sets, whole-chip checks on 4 configurations, mutation controls, the `PE_EXT`-unset identity check, and file hashes (`env/evidence_rev2.sh` is the revision-2 version) |
| `logs/` | Job logs. `23789566-evidence-rev3.out` is the revision-3 evidence, with the per-run outputs in `ev3-23789566/`; `23777273-evidence3.out` is the revision-2 evidence. |

| Job | Purpose | Result (log) |
|---|---|---|
| 23751082 | Build snapshot; emit base/diet4/diet2 | Byte-identical to 73536f0 `src/` (minus header) and published cores (`logs/23751082-build0.out`) |
| 23754994, 23763137 | Generate extension cores; `PE_EXT` unset = design of record | Identity PASS (`logs/23754994-gen2.out`, `logs/23763137-gen3.out`) |
| 23755202, 23763784, 23764660 | `ll66` + `plain` synthesis of 40 cores | Tables in §6 (`synth/runs/`); `ll66/LSTAT` cancelled (ABC hang) |
| 23775719, 23775720 | First revision-2 whole-chip runs, `diet8` and `diet2` | 10/10 PASS each (`logs/23775719-sys-diet8.out`, `logs/23775720-sys-diet2.out`); superseded by 23789566 |
| 23777273 | Revision-2 evidence: engine checks (`FULL16`, `FULL32`, `REC16`, `REC32`); whole-chip checks (`base`, `diet8`, `diet4`, `diet2`); mutations; identity | 23/24 engine checks PASS per set; 11/11 whole-chip PASS per configuration; 6/6 mutations caught; base/diet4/diet2 identical (`logs/23777273-evidence3.out`). Superseded by 23789566: its CAN checks used the later frame spacing, which hid the revision-2 bug. |
| 23788549, 23788787, 23789247 | Revision-3 iterations | 23788549 did not build (no results). In 23788787 the new bus model started the other node's frame in the same cycle as our first SOF, so 1 engine check and 2 whole-chip checks failed; fixed in the model. 23789247 passed everything it ran (REC16 27/27; `diet8`, `diet2` 16/16) with a 145-cycle TX delay, since changed to 150. |
| **23789566** | **Revision-3 evidence**: the same four parts, with the revision-3 firmware and tests | 27/28 engine checks PASS per set; 16/16 whole-chip PASS per configuration; 6/6 mutations caught; base/diet4/diet2 identical; `EVIDENCE rc=0` (`logs/23789566-evidence-rev3.out`) |

Revision-1 jobs 23754605 (engine checks), 23754784 (mutations) and 23753718 (identity) left no log files, so job 23777273 supersedes them. The verification pass's own reproductions of the CAN bug are in `verify-2/` (jobs 23781683 and 23783254).

To rerun, from the work directory:
1. `. env/ocaml-env.sh`
2. `sbatch -p mit_quicktest -c 16 --mem=16G -t 00:15:00 -o logs/%j-evidence.out env/evidence_rev3.sh` (about 1 minute on 16 CPUs)
3. For the cores and area: `env/gen_ext.sh gen_tasks.txt`, then submit `synth/scripts/task.sh` as a Slurm array over `synth/tasks*.txt`, then `python3 synth/scripts/analyze.py`.
