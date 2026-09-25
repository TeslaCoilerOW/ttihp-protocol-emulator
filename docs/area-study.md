# Area study (task 1.3)

Date: 2026-09-24. Scope: decide (1) whether the private-instruction-SRAM
refinement (`configs/instruction-sram-32.json`) fits Tiny Tapeout 8x4
comfortably, and (2) which area diet makes a four-engine design fit 6x4.

All areas below are **mapped** standard-cell areas from Yosys against the
typical-corner liberty, unless a row says "placed" or "utilization". Mapped area
is not placed area. Section 2.4 explains how placed area and utilization are
estimated from one calibration run.

## 1. Bottom line

| Question | Answer |
|---|---|
| 8x4, design as is | Fits, but with little margin. Predicted utilization is **52% (51–53%)**. The 8x4 engineering run of this netlist reached 53%, and its first global route had 721 overflows before routing converged. |
| 8x4 first fallback, no ISA change | Asynchronous reset on `rst_n` for every flop: −24.0K µm² mapped, **~50%**. |
| 6x4, design as is | Does not fit: **~70%**. |
| 6x4, measured diet | Async reset, FIFO depth 4, no completed-instruction counters, and a 7-bit PC. Mapped area is 312.8K µm², giving **53.4% (52.5–54.5%)**. The standard-cell density outside the macros equals the 8x4 engineering run's, so the congestion risk is about the same. |
| 6x4 with margin (≤50%) | Take the measured diet, then add **one** of: FIFO depth 2 plus no barrel shifts (measured 46.8%), latch-array FIFO storage (estimated ~47.5–48%), or a 16-bit datapath (measured 41.1%, last resort). |

The private SRAM organization (8 × `RM_IHPSG13_1P_64x16_c2`) is already the
smallest program store available. Every other macro option and every
flop or latch store is larger (sections 5 and 6).

## 2. Method

### 2.1 Synthesis flow

The flow script is `synth.sh` in the work directory (section 9). It runs:

```
read_liberty -lib sg13cmos5l_stdcell_typ_1p20V_25C.lib
read_verilog -lib sram_bb.v          # RM_IHPSG13_1P_64x16_c2 as (* blackbox *)
read_verilog <design>.v
hierarchy -check -top <top>; synth -flatten -top <top>
techmap -map latch_map.v             # $_DLATCH_* -> dlhq_1 / dlhrq_1 (latch experiments only)
dfflibmap -liberty <lib>; abc -liberty <lib>; opt_clean -purge
setundef -zero; hilomap -hicell sg13cmos5l_tiehi L_HI -locell sg13cmos5l_tielo L_LO
stat -liberty <lib>
```

The tools were Yosys 0.67+111 from OSS CAD Suite 2026-07-29, running on Slurm
(`mit_quicktest`, 2 CPUs). A full-chip run takes 15–20 s and uses at most
0.34 GB. Macro area is added by hand from the LEF `SIZE`.

**Agreement with the prior numbers.** This flow reproduces the three prior
"native nominal" baselines to the µm² once the `tiehi` cells are removed. The
prior flow did not run `hilomap`.

| Variant | This flow, with ties | Tie cells | Without ties + macros | Prior number |
|---|---:|---:|---:|---:|
| 32-bit register program store (`flagship_reg.v`) | 1,151,918.3 | 12,065 × 7.2576 | 1,064,355.4 | 1,064,355 |
| 32-bit SRAM (`sram32.v`) | 442,554.7 | 3,857 × 7.2576 | 414,562.2 + 121,923.6 = 536,485.8 | 536,486 |
| 16-bit SRAM (`instruction-sram-16.json`) | 300,400.5 | 2,577 × 7.2576 | 281,697.7 + 121,923.6 = 403,621.3 | 403,621 |

The tie cells are real area. The CMOS5L library has **no flop without an
asynchronous reset**: the smallest flop is `dfrbpq_1`, and every flop has a
`RESET_B` pin. A flop without reset therefore needs a `tiehi` on `RESET_B`.
LibreLane's `repair_tie_fanout` also gives each load its own tie cell. In the
current design, ties are 28.0K µm² (6.3%) of the standard-cell area. All numbers
below include them.

**Synthesis script sensitivity.** LibreLane's default `AREA 0` ABC script (strash;
dch; map -B 0.9; topo; stime; buffer; upsize; dnsize, with a 20 ns target)
reports more mapped area:

| Variant | Plain ABC | AREA 0 script | Ratio |
|---|---:|---:|---:|
| Base | 442.6K | 520.0K | 1.175 |
| Diet E (section 7) | 312.8K | 379.0K | 1.212 |
| Diet F16 | 216.8K | 248.2K | 1.145 |

The OpenROAD resizer later strips and rebuilds buffers, so the calibration in
section 2.4 is what matters. If the TT action synthesizes with the AREA 0
strategy (not checked), its synthesis report will show the larger figures.

**Noise.** Identical RTL resynthesizes to the identical area; the Diet E
no-shift variant was checked twice. Small logically-neutral changes move ABC's
result by up to about ±3K µm²: `noena` is +3.0K although it is simpler logic.
Treat any delta under about 3K µm² as noise.

### 2.2 Ablations from Hardcaml

The monorepo Hardcaml source (read-only) was copied to a scratch directory and
patched with environment-variable knobs. The scratch copy is
`hc/`, with the diffs in `area-knobs-lib.patch` and `area-knobs-bin.patch`
(section 9). Each variant was then emitted with `generate_refinement.exe`, or
`generate.exe` for register-store variants. The patch also names every
register and FIFO memory (`e<k>_*`, `txq<k>_*`, `rxq<k>_*`, `host_*`,
`pin_*`), so flops can be attributed by name. Naming does not change logic: the
named base is exactly 442,554.7 µm², the same as the pre-generated `sram32.v`.

| Knob | Change | Semantic effect |
|---|---|---|
| `fifo_words` 2/4/16 in config (`Config.validate` relaxed) | Queue depth | ISA/config-visible |
| `AREA_RX_DEPTH=4` | RX FIFOs 4 deep, TX 8 deep | Config-visible |
| `AREA_COMPLETED_WIDTH=0/16` | Remove or narrow `completed_instructions` | READ_SELECT 5 returns 0 or a narrower count |
| `AREA_NARROW_PC=1` | PC 24→7 bits; out-of-range jump targets saturate to 127 | Only the PC readback after an out-of-range jump or fault differs |
| `AREA_NARROW_IMAGE=1` | `image_length`/`image_loaded` 16→7 bits | None (values are ≤64 by construction) |
| `AREA_ASYNC_RESET=1` + `AREA_NO_ENA=1` | Every register gets async reset from `~rst_n`; `ena` no longer clears | Reset timing only |
| `AREA_FIFO_REGS=1` | FIFO storage as enabled registers; with async reset, their `RESET_B` goes to the reset net instead of a tie | None |
| `AREA_DP_NOCLEAR=1` | No clear on start-initialized engine registers, host buffers and pin synchronizers | Power-up X only in non-observable registers |
| `AREA_NO_SHIFT=1` | Opcodes 24/25 (SHL/SHR by `c`) become moves | ISA change |
| `AREA_NO_STATUS=1`, `AREA_NO_MOVER=1` | Remove the status read mux or the mover | Measurement only; not proposals |

A block generator (`bin/area_blocks.ml`) also emits `Engine`, `Fifo` and `Host`
as standalone circuits for per-block synthesis. A hand-written DFFRAM-style
latch FIFO sketch (`vg/blk_fifo_latch.v`) was synthesized for the latch
estimate. It is an area sketch only, not verified RTL.

### 2.3 Flop and latch cells (typical liberty)

| Cell | Function | Area (µm²) |
|---|---|---:|
| `dfrbpq_1` / `dfrbpq_2` | DFF, async reset, Q | 48.9888 / 50.8032 |
| `dfrbp_1` / `dfrbp_2` | DFF, async reset, Q + QN | 52.6176 / 54.432 |
| `sdfrbpq_1` | Scan DFF (D/SCD mux on SCE), async reset, Q | 63.504 |
| `sdfrbp_1`, `sdfbbp_1` | Scan DFF; `sdfbbp_1` also has set | 68.9472, 63.504 |
| `dlhq_1` | Latch, active-high gate, Q | 30.8448 |
| `dlhrq_1` | Latch, active-high gate, reset, Q | 27.216 |
| `dlhr_1` | Latch, active-high gate, reset, Q + QN | 32.6592 |
| `dllrq_1` / `dllr_1` | Latch, active-low gate, reset | 29.0304 / 34.4736 |
| `lgcp_1` / `slgcp_1` | Integrated clock gate (posedge) / scan variant | 27.216 / 30.8448 |
| `tiehi` / `tielo` | Tie cell | 7.2576 |
| `mux2_1` / `mux4_1` (for reference) | Multiplexer | 18.144 / 38.1024 |

A flop costs 48.99 µm², plus either a 7.26 µm² tie or a share of a reset buffer
tree. A latch costs 27.2–30.8 µm². Yosys maps every flop in this design to
`dfrbpq_1`.

### 2.4 From mapped area to utilization

- **Core area.** The TT template sets LEFT/RIGHT_MARGIN_MULT to 6 site widths
  (0.48 µm) and TOP/BOTTOM_MARGIN_MULT to 1 site height (3.78 µm).
  - 8x4 core = 1718.40 × 703.08 = **1,208,173 µm²**
  - 6x4 core = 1283.52 × 703.08 = **902,417 µm²**

  Die sizes were checked against `tt-support-tools@ihp-sg13cmos5l`
  `tech/ihp-sg13cmos5l/tile_sizes.yaml`.
- **Macros.** 8 × 236.80 × 64.36 = **121,923.6 µm²**. That is 10.1% of the 8x4
  core and 13.5% of the 6x4 core.
- **Placement growth.** The prior 8x4 placement of this netlist had about 620–640K
  µm² placed at about 53%. So placed standard-cell area ≈ (630K − 121.9K) / 442.6K
  = **1.15 × mapped** (range 1.125–1.17). This includes CTS, resizer, hold
  buffers and ties.
- **Utilization.** U = (1.15 × mapped + 121.9K) / core. Ranges in
  parentheses use k = 1.125–1.17. An alternative calibration on the AREA 0
  mapped area (placed ≈ 0.977 × AREA 0 mapped) gives 52.1% for the base at 8x4
  and 54.5% for Diet E at 6x4. It agrees with the base and runs about 1 point
  higher for diets, which are more combinational.
- **Density outside the macros.** Placed standard-cell area / (core − macros). The
  8x4 engineering run was at 46.9%. `PL_TARGET_DENSITY_PCT` is 60.

## 3. Where the area is (base = `instruction-sram-32.json`, 442.6K µm² mapped)

### 3.1 Flip-flops by Hardcaml register family

There are 3,901 RTL flop bits. Synthesis keeps 3,849, which are 188.6K µm² of
flops plus 27.9K µm² of ties: 49% of the standard-cell area. Area in the table
is RTL bits × 56.25 µm² (flop + tie).

| Family | RTL bits | Flop + tie µm² | Notes |
|---|---:|---:|---|
| FIFO storage, 8 queues × 8 × 32 | 2,048 | 115,200 | 26% of the standard-cell area in flops alone |
| FIFO count/rd/wr pointers | 80 | 4,500 | The standalone FIFO maps 269 flops for 266 RTL bits |
| Engine tx/rx/x/y (4 × 4 × 32) | 512 | 28,800 | |
| Engine wait_timer/wait_limit/blocked_cycles (4 × 3 × 24) | 288 | 16,200 | |
| Engine transfer edges/tick/period/mode/pins | 148 | 8,325 | |
| Engine completed_instructions (4 × 32) | 128 | 7,200 | Debug/status only |
| Engine pc (4 × 24) | 96 | 5,400 | Only 7 bits are meaningful for 64 words |
| Engine repeat_count (4 × 16) | 64 | 3,600 | |
| Engine logical_output/enable | 64 | 3,600 | |
| Engine running/fault_code | 36 | 2,025 | |
| Image management (length/loaded/valid/writing) | 136 | 7,650 | Narrowing length/loaded to 7 bits in RTL leaves the mapped flop count unchanged (3,849), so synthesis already drops the unused upper bits |
| Ownership/open-drain | 64 | 3,600 | |
| Mover route_remaining/destination/round-robin | 74 | 4,163 | |
| Host port (write buffer, snapshot, indices) | 73 | 4,106 | The standalone host maps 69 |
| Timestamp | 32 | 1,800 | |
| Events/pin triggers | 28 | 1,575 | |
| Pin synchronizers (3 × 8) | 24 | 1,350 | |
| Host select/fault/read_select | 6 | 338 | |

### 3.2 Blocks synthesized standalone

| Block | Count | Mapped each | Flops each | Combinational each | Total | Share |
|---|---:|---:|---:|---:|---:|---:|
| Engine, 32-bit | 4 | 56,904.7 | 334 | 38,118 | 227,619 | 51.4% |
| FIFO 32 × 8 | 8 | 23,414.8 | 269 | 8,285 | 187,318 | 42.3% |
| Host port | 1 | 5,968.7 | 69 | 2,088 | 5,969 | 1.3% |
| Top glue (residual) | 1 | — | ~292 | ~5,200 | 21,649 | 4.9% |

The glue covers command decode, status mux, image management, ownership,
mover, triggers, timestamp, SRAM adapters and pin output. Its figure is a lower
bound. Standalone blocks cannot use in-context constants, for example
`image_length` ≤ 64 simplifies the engine's PC compares in the chip.

Other block sizes: a 16-bit engine is 42,746.9, a 32 × 4 FIFO is 11,617.6, and a
16 × 8 FIFO is 12,357.9.

### 3.3 Combinational attribution by in-context ablation

| Item | Mapped µm² | How measured |
|---|---:|---|
| FIFO storage, per bit, including write-enable and read-mux share | **~90** (92.7K per 1,024 bits) | Depth 8→4, full chip |
| FIFO storage, total (2,048 bits) | ~185K (42%) | Same |
| Program store read/write muxes, register store | 709.4K for 8,192 bits (86.6 µm²/bit, including 56.25 of flop + tie) | `flagship_reg` − `sram32` |
| Per-engine datapath | ~57K each: 18.8K flops + 38.1K combinational | Standalone block |
| Completed-instruction counters, 4 × 32-bit | 12.7K | `comp0` |
| Barrel shifters (SHL/SHR by `c`), 4 engines | 6.4K | `noshift` |
| Host/status read mux, excluding the counters | ~3.5K | `nostatus` (−16.2K) − `comp0` |
| Mover (route registers, arbiter, data mux) | 9.3K | `nomover` |
| Synchronous clear + ties vs async reset | 21.7K–24.0K | `async`, `async_fr` |

## 4. Measured alternatives (full chip, 32-bit SRAM refinement)

Deltas are against base (442,554.7). U uses k = 1.15.

| Variant | Mapped | Δ | Flops | U 8x4 | U 6x4 |
|---|---:|---:|---:|---:|---:|
| Base | 442,554.7 | 0 | 3,849 | 52.2% | 69.9% |
| FIFO depth 16 | 623,512.4 | +180,958 | 5,929 | 69.4% | 93.0% |
| FIFO depth 4 | 349,883.2 | **−92,672** | 2,793 | 43.4% | 58.1% |
| FIFO depth 2 | 305,491.4 | −137,063 | 2,249 | 39.2% | 52.4% |
| RX depth 4 / TX depth 8, async + FIFO regs | 373,924.9 | −68,630 (−44.7K vs async + FIFO regs) | 3,301 | 45.7% | 61.2% |
| Completed counters removed | 429,900.8 | −12,654 | 3,721 | 51.0% | 68.3% |
| Completed counters 16-bit | 434,708.7 | −7,846 | 3,785 | 51.5% | 68.9% |
| PC 7-bit, saturating | 434,475.2 | −8,080 | 3,781 | 51.4% | 68.9% |
| image_length/loaded 7-bit | 442,651.1 | +96 (noise) | 3,849 | — | — |
| No clear on datapath registers (sync mode) | 441,345.9 | −1,209 (noise) | 3,849 | — | — |
| Drop `ena` from clear only | 445,560.6 | +3,006 (noise) | 3,849 | — | — |
| **Async reset on `rst_n`, no `ena`** | 420,860.1 | **−21,695** | 3,825 | 50.2% | 67.1% |
| Async reset + FIFO storage on the reset net (no flop ties; 8 SRAM `A_DLY` ties remain) | 418,590.7 | **−23,964** | 3,825 | 49.9% | 66.9% |
| FIFO storage as registers (sync) | 434,096.6 | −8,458 | 3,825 | 51.4% | 68.8% |
| No barrel shifts | 436,171.7 | −6,383 | 3,849 | 51.6% | 69.1% |
| 16-bit datapath | 300,400.5 | −142,154 | 2,569 | 38.7% | 51.8% |
| 16-bit + FIFO depth 4 | 251,705.3 | −190,849 | 2,025 | 34.1% | 45.6% |
| Scan-flop-as-enable mapping (flow only, custom techmap) | 436,268.6 | −6,286 | 3,849 | 51.6% | 69.1% |

On removing the clear from datapath registers: combined with async reset it
is **worse**. Diet C (321.8K) is 8.5K above Diet B (313.3K), because the
un-reset flops need tie cells again. Under async reset, every flop should keep
its reset.

Latch and clock-gating estimates come from standalone FIFO blocks; they were
not measured in context:

| FIFO block (32-bit) | Flop FIFO | Latch FIFO (ICG per word, no capture register) | Latch FIFO + 32-bit capture register | Latch with reset (`dlhrq`) |
|---|---:|---:|---:|---:|
| Depth 8 | 23,414.8 | 12,050.0 (−11.4K) | 13,849.8 (−9.6K) | 11,117.2 (−12.3K) |
| Depth 4 | 11,617.6 | 6,014.7 (−5.6K) | 7,832.7 (−3.8K) | — |

**Whole-queue estimate.** RX FIFOs can latch the engine `rx` register
directly. PUSH does not modify `rx`, and any instruction issued in the next
cycle cannot change `rx` before the following edge, so no capture register is
needed. TX FIFOs need captured data, because the host word includes live
`ui_in` nibbles and the mover's source head pops. Two shared capture registers
(host word and mover word, about 3.6K) serve all four TX FIFOs, since at most
one host push and one mover push happen per cycle. The estimate is
**−37.6K to −41.2K at depth 4** and **−83.7K to −87.3K at depth 8**.

Clock-gated flops alone (an ICG per word instead of per-bit enable muxes) are
estimated at about −18K at depth 4 and about −38K at depth 8.

## 5. Program-store organizations

| Store | Program-store cost (µm²) | vs current |
|---|---:|---:|
| 8 × `1P_64x16_c2` macros (current) | 121,924 macros + small address-mux adapter | — |
| Flop register store, 64 words | 709,364 standard-cell (measured) | +587K |
| Flop register store, 32 words (with diet) | 331,744 (measured: `regp32f4d` − Diet C) | +210K |
| Latch store, 32 words (estimate) | ~126K `dlhq` + ~50K read mux + ~4K ICG + capture ≈ 185K | +63K, and half the words |
| 2 × `2P_256x32_c2_bm_bist`, 2 engines/macro, 128 words each | 192,533 | +70.6K; doubles words; 703 µm wide |
| 4 × `2P_64x32_c2` | 210,484 | +88.6K; 703 µm wide each |
| 4 × `1P_64x64_c2_bm_bist` (half width unused) | 201,956 | +80.0K |
| 4 × `1P_256x32_c2_bm_bist` | 197,954 | +76.0K |
| 8 × `1P_256x16_c2_bm_bist` | 225,017 | +103.1K |

No option beats the current macros. Fewer words do not help, because 64 × 16
is the smallest macro. Moving the 2,048 FIFO payload bits into one
`2P_64x32_c2` (52.6K µm², exactly 64 × 32) would need 8 head registers
(~14K), arbitration for up to 16 accesses per cycle over 2 ports, and changed
PULL/PUSH timing. That is an architecture change with a 703 µm-wide macro, so it
is not recommended.

## 6. SRAM macro footprints (LEF `SIZE`, `libs.ref/sg13cmos5l_sram/lef`)

| Macro | W × H (µm) | Area (µm²) |
|---|---|---:|
| RM_IHPSG13_1P_64x16_c2 | 236.80 × 64.36 | 15,240.4 |
| RM_IHPSG13_1P_64x64_c2_bm_bist | 784.48 × 64.36 | 50,489.1 |
| RM_IHPSG13_1P_256x8_c3_bm_bist | 236.80 × 74.10 | 17,546.9 |
| RM_IHPSG13_1P_256x16_c2_bm_bist | 236.80 × 118.78 | 28,127.1 |
| RM_IHPSG13_1P_256x32_c2_bm_bist | 416.64 × 118.78 | 49,488.5 |
| RM_IHPSG13_1P_256x48_c2_bm_bist | 596.48 × 118.78 | 70,849.9 |
| RM_IHPSG13_1P_256x64_c2_bm_bist | 784.48 × 118.78 | 93,180.5 |
| RM_IHPSG13_1P_512x8_c3_bm_bist | 236.80 × 110.38 | 26,138.0 |
| RM_IHPSG13_1P_512x16_c2_bm_bist | 236.80 × 191.34 | 45,309.3 |
| RM_IHPSG13_1P_512x32_c2_bm_bist | 416.64 × 191.34 | 79,719.9 |
| RM_IHPSG13_1P_1024x8_c2_bm_bist | 146.88 × 336.46 | 49,419.2 |
| RM_IHPSG13_1P_1024x16_c2_bm_bist | 236.80 × 336.46 | 79,673.7 |
| RM_IHPSG13_2P_64x22_c2_bm_bist | 526.03 × 74.87 | 39,383.9 |
| RM_IHPSG13_2P_64x32_c2 | 702.83 × 74.87 | 52,620.9 |
| RM_IHPSG13_2P_256x8_c2_bm_bist | 278.51 × 136.97 | 38,147.5 |
| RM_IHPSG13_2P_256x16_c2_bm_bist | 419.95 × 136.97 | 57,520.6 |
| RM_IHPSG13_2P_256x32_c2_bm_bist | 702.83 × 136.97 | 96,266.6 |
| RM_IHPSG13_2P_512x32_c2_bm_bist | 685.49 × 219.77 | 150,650.1 |

Larger macros (≥ 1024 words) are omitted. All of them exceed 49K µm².

In `RM_IHPSG13_1P_64x16_c2`:
- All 43 signal pins sit on the bottom edge (y = 0–0.26 µm, Metal2).
- Power pins (VDD!, VSS!, VDDARRAY!) are on Metal4.
- OBS covers the full footprint on Metal1 and Metal3, heavily on Metal2, and
  about 49% on Metal4.

## 7. Recommendations

### (a) 8x4 first harden

Harden `configs/instruction-sram-32.json` unchanged, as decided.
- Expected mapped area: 442.6K µm² with the plain flow, or about 520K if the TT
  action synthesizes with LibreLane's AREA 0 strategy (not checked).
- Expected placed area: about 630K µm² including macros.
- **Expected utilization: 52% (51–53%).** Density outside the macros is about 47%,
  under the TT `PL_TARGET_DENSITY_PCT` of 60.

This is the same operating point as the 8x4 engineering run, which routed after
an initial overflow. It fits, but not comfortably. If the TT global or
detailed route struggles, apply fixes in this order:
1. Async reset: −24K, to ~49.9%. No ISA change. It also removes the
   ~3,800-load synchronous-clear cone behind the worst routed setup path from
   `rst_n`.
2. Remove the completed-instruction counters: −12.7K, about −1.2 points more.
3. Narrow the PC: −8.1K.

### (b) 6x4 ranked diet

Budget: to reach 50% at 6x4, mapped standard-cell area must be ≤ 286K µm²;
53% allows 310K and 55% allows 326K. Current is 442.6K.

Ranked by µm² saved per unit of ISA, verification and risk cost. The
"Cumulative" column is measured wherever a combined variant was synthesized.

| # | Item | Saved (mapped) | Cumulative mapped | U 6x4 | ISA / verification impact |
|---|---|---:|---:|---:|---|
| 0 | Base | — | 442.6K | 69.9% | — |
| 1 | Async reset on `rst_n` for every flop, including FIFO storage; ignore `ena`; add a 2-flop reset-release synchronizer | 24.0K | 418.6K (measured) | 66.9% | Not ISA-visible. Update Hardcaml `Engine.next_pc` (it rejects non-sync-clear PCs), the formal reset properties and Cyclesim reset tests. |
| 2 | FIFO depth 8 → 4, TX and RX | 88.0K | 330.6K (measured) | 55.6% | Config-visible. `Config.validate` and `contracts.py` must allow 4 (both currently allow only `Literal[8, 32]`). Reassemble images (`fifo_words` is bound in every image). **`flagship-scenario.json` prefills 8 TX words into engine 0 and must change**, for example by host top-up. |
| 3 | Remove `completed_instructions` (READ_SELECT 5 → 0 or reserved) | 12.7K alone | — | — | Debug-only status word; model and host decode change. |
| 4 | PC 24 → 7 bits, saturating out-of-range targets | 8.1K alone; 3+4 together 17.8K | 312.8K (measured, 3+4 on top of 2) | **53.4%** | Only the PC readback after an out-of-range jump or fault changes (reports 127). |
| 5 | Remove SHL/SHR by `c` (opcodes 24/25), or reduce them to 1-bit | 5.2K | 307.6K (measured) | 52.7% | ISA change; firmware using them must change. |
| 6a | FIFO depth 4 → 2 | 46.4K | 261.2K (measured, with 5) | **46.8%** | Less buffering; check host and mover latency slack. Same scenario impact as item 2. |
| 6b | *or* latch-array FIFO storage (ICG + `dlhq`) at depth 4 | ~37.6–41.2K (estimate) | ~266–270K | ~47.5–48% | Structural only, but high tool risk (section 7c). Can instead keep depth 8: Diet E8 401.3K − 87K ≈ 314K ≈ 53.5%. |
| 6c | *or* 16-bit datapath (`instruction-sram-16.json`) | 96.0K on top of #1–4 | 216.8K (measured, `dietF16`) | **41.1%** | Last resort: loses 32-bit SPI words. |
| 7 | Flow trick: map enable flops onto `sdfrbpq_1` (scan mux as enable) | 6.3–13.5K (measured) | 299.3K for Diet E | 51.7% | No RTL change, but needs a custom techmap that LibreLane does not provide by default. Unproven in the TT flow. |
| — | Not worth it | | | | Narrowing image regs (0), datapath no-clear (−1.2K; +8.5K under async), dropping `ena` alone (noise), removing the status mux (−3.5K, breaks the host contract), removing the mover (−9.3K, differentiator), RX-only depth 4 (44.7K; with #1, #3, #4 gives 355.9K = 58.9%). |

**Recommended 6x4 insurance variant.**
- Minimum: items 1 + 2 + 3 + 4 (Diet E): 312.8K mapped, **53.4%
  (52.5–54.5%)**. Density outside the macros is 46.1%, the same as the 8x4
  engineering run.
- With margin: add 5 and 6a, giving 261.2K, **46.8%**. Or add 6b if 8-word
  queues are worth the latch risk.
- Keep 6c (16-bit) as the fallback if 6x4 still fails to route.

### (c) Risks

- **Macro-induced congestion.** TT sets `RT_MAX_LAYER = Metal4` for CMOS5L
  projects (`tt-support-tools` `tech.py`). The routing layers are:
  - Metal1: horizontal, 0.42 µm pitch, mostly cell pins.
  - Metal2: vertical, 0.48 µm.
  - Metal3: horizontal, 0.42 µm.
  - Metal4: vertical, 0.48 µm.

  TopMetal1 (2.28 µm pitch) belongs to TT. The macros obstruct Metal1 and
  Metal3 over their whole footprint, so **no horizontal signal can cross a
  macro**. Each `64x16` macro is a 236.8 µm-wide wall, and there are eight.
  Vertical crossing is limited to the unobstructed half of Metal4, which also
  carries the macro power pins and, presumably, the TT PDN stripes
  (`FP_PDN_VPITCH` 50 µm, width 2.1 µm; not checked).
- **Macro placement.** All signal pins are on each macro's bottom edge. Place
  macros at a core edge with pins facing the logic. Never enclose logic
  between macro rows or between a macro and the die edge. Only non-rotated
  orientations (N/S/FN/FS) are assumed.
  - 6x4: two rows of four macros span about 947 µm of the 1,283.5 µm core width
    and about 129 µm of height plus halos.
  - 8x4: there is more room (1,718.4 µm).

  The existing routed floorplan is 8x4-only; 6x4 needs a new macro placement.
- **Macro fraction.** Macros are 13.5% of the 6x4 core, compared with 10.1% at
  8x4. At equal overall utilization, 6x4 therefore has less routing capacity
  per cell than the 8x4 run. The 721 initial GRT overflows at 53% on 8x4 are
  the only congestion data point.
- **PDN and DRC.** The macros need the open-source SRAM PDN plugin to reach
  their Metal4 power pins. Issue #190 (in-macro KLayout DRC) is still open.
  Neither affects area, but either can block the 8x4 attempt independently.
- **Async reset.** It adds a reset tree with about 3,800 loads. This area is
  not in the mapped numbers; allow a few K µm² of buffers. Reset
  recovery/removal must also be checked, and deassertion must be synchronized
  to `clk`.
- **Latches and ICGs** (item 6b).
  - Hardcaml has no latch primitive, so the cells must be instantiated
    (`dlhq_1`, `lgcp_1`), with a separate Cyclesim model. This follows the
    pattern already used for the SRAM.
  - LibreLane's linter may reject inferred latches, so instantiate the cells
    explicitly. This was not checked in the TT flow.
  - STA must handle latch time-borrowing, CTS must go through ICGs, and the
    SymbiYosys flows need latch modeling.
  - The source-stability argument for RX FIFOs must be proven, not assumed.
- **Estimate error.**
  - The mapped-to-placed factor comes from one run: k = 1.125–1.17.
  - Diet variants are more combinational. The AREA 0 calibration puts Diet E
    at 6x4 at 54.5% instead of 53.4%, so plan with the upper value.
  - ABC noise is about ±3K µm².
  - The 16-bit and depth-2 options change capability. They are not free area.

## 8. Caveats

- Mapped area is not placed area. Utilization is an estimate calibrated on
  one prior placement.
- Only the typical corner (1.20 V, 25 °C) was used, and there was no timing-driven sizing.
  Area-optimized netlists may upsize in the slow corner.
- Per-block numbers are standalone syntheses. The in-context total differs by
  the glue and by cross-boundary constants (section 3.2).
- Latch, ICG, capture-register and program-store-latch numbers are estimates
  from block synthesis and hand arithmetic. They are marked as such.
- All knobs live in a scratch copy of the Hardcaml source. None has been
  simulated or verified against the reference model. They are area probes, not
  design changes. In particular, the 7-bit saturating PC and the async-reset
  variant would need model, formal and test updates before adoption.

## 9. Reproduction

Work directory: `<local work dir>/tt-work/area/`. Kept files:
- `scripts/synth.sh`, `synth_x.sh` (with an extra techmap), `batch*.sh`,
  `gen_variants*.sh`, `regbits.py`, `analyze.py`.
- `stub/` (SRAM blackbox, latch map, scan-enable map).
- `cfg/` (variant configs).
- `hc/` (patched Hardcaml copy) and `area-knobs-*.patch`.
- `runs/<variant>/{stat.txt,pre_map_stat.txt,synth.ys}`.
- `vg/base.v` (named baseline) and `vg/blk_fifo_latch.v`.
- `summary.json`.

Mapped netlists, Yosys logs, the other generated variants (regenerate them
with `gen_variants*.sh`) and the dune build directory were deleted.

```
. <ocaml-env.sh>; cd hc && dune build --build-dir ../_build -j 2 \
    bin/generate_refinement.exe bin/generate.exe bin/area_blocks.exe
scripts/gen_variants.sh; scripts/gen_variants2.sh      # emit vg/*.v
srun -p mit_quicktest -c 2 --mem=8G -t 15 scripts/batch1.sh   # (batch2..4 likewise)
python3 scripts/analyze.py
```
