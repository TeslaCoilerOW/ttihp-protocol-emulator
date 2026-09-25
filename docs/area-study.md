# Area study (task 1.3)

Revision 2, 2026-09-25. Scope: decide (1) whether the private-instruction-SRAM
refinement (`configs/instruction-sram-32.json`) fits Tiny Tapeout 8x4
comfortably, and (2) which area diet makes a four-engine design fit 6x4.

Mapped (synthesized) area is not placed area. Section 2.4 converts one into the
other with a model calibrated on a local run of the TT flow itself.

**Changes from revision 1.** Revision 1 overstated the margin at 8x4 and got
some contract impacts wrong. All numbers below were regenerated from one source
state (manifest in section 10), except the one row marked as carried over
from revision 1 in section 6.
1. **Utilization.** It is now calibrated on the local LibreLane 3.1.0.dev3 run
   of the design of record (`docs/hardening.md` section 7, the TT action's
   LibreLane version and configuration). That run measured **58.4%**, not the
   52% revision 1 predicted. The TT flow adds about 75K µm² of hold buffers after
   CTS, and revision 1's calibration run did not.
2. **Synthesis.** Areas are now reported for a replica of the TT synthesis.
   That replica is LibreLane's default `SYNTH_STRATEGY` "AREA 0" with the Yosys
   0.66 it ships. Revision 1's "AREA 0" column was actually LibreLane's AREA 3
   script.
3. **Asynchronous reset.** The probe now resets from `~(rst_n & ena)`, so
   deselection still resets as `isa.md` requires, and FLUSH stays a synchronous
   clear. Revision 1 dropped `ena`, which breaks the ISA and the smoke test. It
   also turned the FLUSH decode into an asynchronous reset.
4. **Shifts.** Removing SHL/SHR is no longer recommended: 15 of 19 firmware
   images use them. A byte-lane shift (c ∈ {0, 8, 16, 24}) keeps every image
   and saves as much area.
5. **ISA impact.** Removing the completed-instruction counters and narrowing the
   PC are marked as ISA changes.
6. **Also:** the macro table is now complete; the latch estimate uses the
   asynchronous-reset FIFO baseline; and the scripts and patch are published
   in `docs/area-study/` (section 10).

## 1. Bottom line

"TT synthesis" is the replica of the action's synthesis (section 2.1).
Utilization U is placed instance area over core area, with macros included. D is
standard-cell density outside the macros.

| Question | Answer |
|---|---|
| 8x4, design as is | **Fits; routing converged locally, with little margin.** The local TT-flow run measured **58.4%** (D 53.7%). Global routing had 617 overflows (574 on Metal3). Detailed routing went from 34,515 violations to 0 at iteration 46; the re-routes after antenna repair also ended at 0 (00:40, 2026-09-25). Post-route STA and the later steps are in `docs/hardening.md` section 7. |
| 8x4 fallback with no contract change | Asynchronous reset from `~(rst_n & ena)`, FIFO storage as reset registers, and 7-bit `image_length`/`image_loaded`. TT synthesis −48.7K µm², predicted **53.8%** (D 48.6%). |
| 6x4, design as is | Does not fit: **78.1%**. |
| 6x4, config change only (FIFO depth 4, plus the neutral items) | 60.0% (D 53.7%). This is the same density that barely routed at 8x4, with more macro blockage (section 8c). Too tight. |
| **6x4 recommended diet** | Neutral items, FIFO depth 4, no completed-instruction counters, 7-bit PC, byte-lane shifts. TT synthesis 297.3K µm², predicted **55.2%** (D 48.2%). |
| 6x4 with more margin | Add FIFO depth 2: **49.2%** (D 41.2%). Or add latch-array FIFO storage at depth 4: ~48.5–51% (estimate). A 16-bit datapath gives 43.9% but breaks 15 of 19 firmware images. |

The private SRAM organization (8 × `RM_IHPSG13_1P_64x16_c2`, 121,924 µm²) is
still the smallest program store. Every other macro set is 192–225K µm², and
flop or latch stores are larger again (sections 6 and 7).

## 2. Method

### 2.1 Synthesis flows

Two flows run every variant. The script is `synth3.sh` (section 10).

- **`ll66`, "TT synthesis" (primary).** This is a line-by-line port of LibreLane
  3.1.0.dev3 `librelane/scripts/pyosys/synthesize.py`, with the `opt` loops
  unrolled. It runs with:
  - `SYNTH_STRATEGY` "AREA 0", flatten, and default options;
  - the Yosys 0.66 inside the LibreLane 3.1.0.dev3 SIF, via apptainer;
  - the `AREA_0.abc` script and ABC SDC that LibreLane generated for this design.

  The mapping step is `dfflibmap` → `abc -script AREA_0.abc -constr` (driving
  cell `buf_4`, load 6) → `setundef -zero` → `hilomap` → `splitnets` →
  `insbuf`.

  "AREA 0" is what the action uses. It is LibreLane's default, and neither
  tt-support-tools (`IHPTech.librelane_config = {}`) nor `src/config.json`
  overrides it. The local run's `resolved.json` shows `SYNTH_STRATEGY "AREA 0"`.
- **`plain` (revision 1's flow).** `synth -flatten`, `dfflibmap`,
  `abc -liberty`, `setundef -zero`, `hilomap`. It uses the local Yosys 0.67+111.

Both use the typical-corner liberty `sg13cmos5l_stdcell_typ_1p20V_25C.lib`. The
macros are black boxes, and their LEF area is added by hand.

**Replica check** on the exact netlist that the local LibreLane run synthesized
(`src/project.v` + `src/protocol_emulator_core.v` snapshot):

| | Area (µm²) | `dfrbpq_1` | `tiehi` |
|---|---:|---:|---:|
| LibreLane 3.1.0.dev3, real run (`06-yosys-synthesis/reports/stat.rpt`) | 465,164.3 | 3,921 | 3,929 |
| `ll66` replica | 462,710.3 (−0.53%) | 3,921 | 3,929 |
| `plain` | 442,925.9 | 3,849 | 3,857 |

The replica matches the flop and tie counts exactly and the area to within 0.53%.
- **Flop counts.** Yosys 0.66 keeps 72 flops that 0.67 removes: the constant-zero
  upper bits of the four 16-bit `image_length`/`image_loaded` registers. So in
  the TT flow, narrowing those registers is not free, as revision 1 said. It
  saves 8.4K µm².
- **Script mislabel.** Revision 1's "AREA 0" script (`strash; dch; map -B 0.9;
  topo; stime; buffer; upsize; dnsize`) is LibreLane's **AREA 3**. The real
  AREA 0 is `fx; mfs; strash; drf; resyn2; retime; choice2; amap; retime; &nf`.

**Plain-flow check against the prior numbers.** Unchanged from revision 1, and
re-run. Without tie cells, `plain` reproduces the prior "native nominal"
baselines to the µm²:
- register store: 1,064,355;
- SRAM-32: 414,562.2 + 121,923.6 macros = 536,486;
- SRAM-16: 403,621.

The CMOS5L library has **no flop without an asynchronous reset**. Every
non-reset flop therefore needs a `tiehi` on `RESET_B`, at 7.26 µm² each.

### 2.2 Ablations from Hardcaml

The Hardcaml source was patched in a scratch copy with environment-variable
knobs (the patch is in section 10). It applies with `patch -p1` inside this
repository's `hardcaml/`. Each variant is emitted with `generate_refinement.exe`,
or `generate.exe` for register program stores. The patch also names every
register, and naming does not change logic: the named base is bit-identical to
revision 1's and synthesizes to the same 442,554.7 µm² as the pre-generated
`sram32.v`.

| Knob | Change | Contract impact |
|---|---|---|
| `fifo_words` 2/4/16 (`Config.validate` relaxed) | Queue depth | Config/ISA: `isa.md:14` allows only 8/32. The flagship scenario prefills 8 TX words. |
| `AREA_RX_DEPTH=4` | RX 4 deep, TX 8 deep | Config/ISA |
| `AREA_COMPLETED_WIDTH=0/16` | Remove or narrow `completed_instructions` | **ISA change.** READ_SELECT 5 (`isa.md:123`) and the strict-overflow rule (`isa.md:94`) define this count. Needs an ISA revision. |
| `AREA_NARROW_PC=1` | PC 24→7 bits; out-of-range targets saturate to 127 | **ISA change.** The READ_SELECT 3 readback differs after an out-of-range jump or fault (fault code 2 is unchanged). |
| `AREA_NARROW_IMAGE=1` | `image_length`/`image_loaded` 16→7 bits | None. Both are ≤ 64 by construction and are never read back. |
| `AREA_ASYNC_RESET=1` | Every register is asynchronously reset by `~(rst_n & ena)`. FIFO FLUSH stays a synchronous clear. | Deselection still resets everything, as `isa.md:26-27` requires. The register state after every edge equals the synchronous design's when `rst_n`/`ena` change between edges, which is how `test/harness.py` drives them (at the falling edge). Not simulated yet. |
| `AREA_RST_SYNC=2` (with async) | Two-flop reset-release synchronizer | Reset release is 2 cycles later. The model and tests must change: `test_smoke` expects write-ready in the first cycle after reset. |
| `AREA_NO_ENA=1` | Drop `ena` from reset | **Breaks** `isa.md:26`, the model (`test/model/reference.py:486`) and `test_smoke`. Measurement only. |
| `AREA_FIFO_REGS=1` | FIFO storage as enabled registers. Under async reset they also take the reset, so `RESET_B` needs no tie. | None (storage contents are not observable after reset) |
| `AREA_DP_NOCLEAR=1` | No clear on start-initialized engine registers, host buffers and pin synchronizers | Pin synchronizers: `isa.md:166-167` says reset clears previous samples. Measurement only. |
| `AREA_BYTE_SHIFT=1` | SHL/SHR honour only c[4:3], so shifts are 0/8/16/24 | ISA restriction. Every current image complies: all 15 images that shift use c = 24. |
| `AREA_NO_SHIFT=1` | SHL/SHR become moves | **Breaks 15 of 19 images**, including 3 of the 4 flagship images. Measurement only. |
| `AREA_NO_STATUS=1`, `AREA_NO_MOVER=1` | Remove the status mux or the mover | Measurement only |

`bin/area_blocks.ml` emits `Engine`, `Fifo` and `Host` standalone. A hand-written
latch-array FIFO (`vg2/blk_fifo_latch.v`) is an area sketch only, not
verified RTL.

### 2.3 Flop and latch cells (typical liberty)

| Cell | Function | Area (µm²) |
|---|---|---:|
| `dfrbpq_1` / `dfrbpq_2` | DFF, async reset, Q | 48.9888 / 50.8032 |
| `dfrbp_1` / `dfrbp_2` | DFF, async reset, Q + QN | 52.6176 / 54.432 |
| `sdfrbpq_1` | Scan DFF, async reset, Q | 63.504 |
| `dlhq_1` | Latch, active-high gate, Q | 30.8448 |
| `dlhrq_1` / `dlhr_1` | Latch with reset, Q / Q + QN | 27.216 / 32.6592 |
| `dllrq_1` / `dllr_1` | Latch, active-low gate, reset | 29.0304 / 34.4736 |
| `lgcp_1` / `slgcp_1` | Clock gate / scan clock gate | 27.216 / 30.8448 |
| `tiehi` / `tielo` | Tie | 7.2576 |
| `mux2_1` / `mux4_1` (for reference) | Multiplexer | 18.144 / 38.1024 |

The PDK's LibreLane `synth_exclude.cells` lists `lgcp_1`, `slgcp_1`,
`sdfbbp_1`, `dfrbp_2` and `sighold`. So synthesis never infers a clock gate, and
a latch FIFO would have to instantiate one.

### 2.4 From synthesis to utilization

**Core.** TT template margins apply (6 sites left and right, 1 row top and
bottom). The 8x4 core is 1718.40 × 703.08 = **1,208,173 µm²**; the local run
reports 1,208,170. The 6x4 core is 1283.52 × 703.08 = **902,417 µm²**.

**Macros.** 8 × 236.80 × 64.36 = **121,923.6 µm²**: 10.1% of the 8x4 core and
13.5% of the 6x4 core.

**Where placed area comes from.** These are the TT-flow stage metrics of the
design of record: local run2, job 23720702, LibreLane 3.1.0.dev3, 8x4, action
configuration. Figures are from each step's `state_out.json` and log.

| Step | Std-cell area (µm²) | Added | Cause |
|---|---:|---:|---|
| Yosys.Synthesis | 465,164 | | 3,921 flops, 3,929 ties |
| GlobalPlacement | 465,207 | +43 | |
| RepairDesignPostGPL | 498,062 | +32,855 | 4,235 buffers on 1,053 nets: 941 fanout, 234 slew and 72 capacitance violations (`MAX_FANOUT_CONSTRAINT` 10) |
| CTS | 509,003 | +10,941 | 415 clock buffers, 143 clock inverters |
| ResizerTimingPostCTS | **583,689** | +74,686 | **4,574 hold buffers** for 3,911 endpoints with hold slack below the 0.1 ns margin. No setup repair was needed. |

That gives **U = 58.40%** (705,612 / 1,208,170) and D = 53.7%.

The hold buffering is a flow property:
- The base SDC's 0.25 ns clock uncertainty applies to hold checks (the min-path
  reports show it), and `PL_RESIZER_HOLD_SLACK_MARGIN` adds 0.1 ns.
- Post-CTS, only 107 endpoints violated hold at zero margin (worst −0.306 ns,
  into an SRAM data input). But almost every flop's hold slack was under the
  margin, so almost every flop got a buffer.
- Caveat: the 107 is the typical corner only. The hold repair itself loads the
  fast, slow and typical libraries, reports 3,911 endpoints with hold
  violations, and pre-PnR STA already showed 3,748 hold violations at the fast
  corner (`nom_fast_1p32V_m40C`, worst −0.44 ns). The fast corner, not the
  0.1 ns margin, is therefore the likelier driver.
- These are flow settings owned by the hardening task. They cost about
  19 µm² per flop. The model below uses the measured per-flop cost, so the
  cause does not change the numbers.

**Model.** Two terms, both taken from the table above: logic-proportional
overhead (placement, design repair, CTS) and per-flop hold buffers.

```
placed_std = 1.100 × S_TT + 19.05 × F        (S_TT = TT-synthesis area, F = flops)
U = (placed_std + 121,923.6) / core          D = placed_std / (core − 121,923.6)
```

1.100 = (583,689 − 74,686) / 462,710 (replica area) and 19.05 = 74,686 / 3,921.
A purely proportional model (placed_std = 1.2615 × S_TT) agrees within 1 point
for every candidate. Section 5 gives it in parentheses where it matters.

**Revision 1's calibration** (k = 1.15 on plain area, from the monorepo
engineering run) matches this run *after CTS* exactly: 509,003 / 442,555 =
1.150. It missed the post-CTS hold repair.

**Routability anchor.** The same run's global route finished with 617 overflows:
Metal2 24, Metal3 574, Metal4 19. Layer usage was Metal2 58.9%, Metal3 66.3% and
Metal4 21.6%. Detailed routing then converged: 34,515 → 20,002 → 18,238 →
1,948 → 426 → 130 → 66 → … → 27 (iteration 12) → 2 (iterations 42–45) → 0
(iteration 46). Antenna repair followed (62, then 5, then 1 violating nets),
and each incremental re-route ended at 0 violations (status at 00:40 on
2026-09-25). This study therefore treats **D ≈ 54% at 8x4 as the edge of
routability**. Section 8c explains why 6x4 needs a lower D.

### 2.5 Reproducibility and noise

- **Same RTL, same area.** The regenerated base is byte-identical to revision 1's
  (SHA-256 `3a721a1b…`), and both flows give identical areas on repeated runs.
  The compact script `synth3.sh` reproduced 442,554.7 (plain), 462,171.9 (TT)
  and 462,710.3 (snapshot) exactly.
- **Cross-flow disagreement.** For single knobs, the TT and plain deltas differ
  by up to 6–8K µm² (`narrowpc` −14.3K against −8.1K; `narrowimg` −8.4K
  against +0.1K, explained above; scan-enable −4.7K against −10.2K). **Items
  under about 5K µm² cannot be ranked reliably.** Diet totals differ by 9–15K
  (TT synthesis always saves more).

## 3. Where the area is (base = `instruction-sram-32.json`)

### 3.1 Flip-flops by Hardcaml register family

The named families hold 3,901 RTL bits. TT synthesis maps 3,921 flops and
plain maps 3,849; the difference is the 72 constant image bits. The remaining
20 flops in each count are outside the named families and are not attributed. In the
TT flow a synchronous-clear flop costs about **81 µm² placed**: 1.1 × (48.99
flop + 7.26 tie) + 19.05 of hold buffer. With asynchronous reset it costs 73.

| Family | RTL bits | Placed flop cost (bits × 81 µm²) | Notes |
|---|---:|---:|---|
| FIFO storage, 8 queues × 8 × 32 | 2,048 | 166K | Plus the write-enable and read-mux logic (section 3.3) |
| FIFO count/rd/wr pointers | 80 | 6.5K | |
| Engine tx/rx/x/y (4 × 4 × 32) | 512 | 41.4K | |
| Engine wait_timer/wait_limit/blocked_cycles (4 × 3 × 24) | 288 | 23.3K | |
| Engine transfer edges/tick/period/mode/pins | 148 | 12.0K | |
| Engine completed_instructions (4 × 32) | 128 | 10.4K | ISA-visible (READ_SELECT 5) |
| Engine pc (4 × 24) | 96 | 7.8K | 7 bits address 64 words |
| Engine repeat_count (4 × 16) | 64 | 5.2K | |
| Engine logical_output/enable | 64 | 5.2K | |
| Engine running/fault_code | 36 | 2.9K | |
| Image management (length/loaded/valid/writing) | 136 | 11.0K | 72 constant bits are kept by TT synthesis |
| Ownership/open-drain | 64 | 5.2K | |
| Mover route_remaining/destination/round-robin | 74 | 6.0K | |
| Host port (write buffer, snapshot, indices) | 73 | 5.9K | |
| Timestamp | 32 | 2.6K | |
| Events/pin triggers | 28 | 2.3K | |
| Pin synchronizers (3 × 8) | 24 | 1.9K | |
| Host select/fault/read_select | 6 | 0.5K | |

### 3.2 Blocks synthesized standalone (TT synthesis; plain in parentheses)

| Block | Count | Area each | Flops each | Total | Share of 462.2K |
|---|---:|---:|---:|---:|---:|
| Engine, 32-bit | 4 | 58,149.0 (56,904.7) | 334 | 232.6K | 50.3% |
| FIFO 32 × 8 | 8 | 24,102.5 (23,414.8) | 269 | 192.8K | 41.7% |
| Host port | 1 | 6,375.8 (5,968.7) | 69 | 6.4K | 1.4% |
| Top glue (residual) | 1 | | ~364 | 30.4K | 6.6% |

The glue is command decode, status mux, image management, ownership, mover,
triggers, timestamp, SRAM adapters and pin output. Other blocks:
- 16-bit engine: 44,793.3.
- 32 × 4 FIFO: 11,614.0 with ties, or 11,184.6 with async reset and storage registers.
- 32 × 8 FIFO with async reset and storage registers: 21,900.0.
- 16 × 8 FIFO: 12,709.9.

### 3.3 Attribution by in-context ablation (TT synthesis)

| Item | TT-synthesis Δ | Placed Δ (model) | How measured |
|---|---:|---:|---|
| FIFO storage, depth 8 → 4 (1,024 bits) | −95.5K | −125.2K, **~122 µm² per bit** | `fifo4` |
| Completed-instruction counters, 4 × 32 | −11.2K | −14.7K | `comp0` |
| PC 24 → 7 bits (saturating) | −14.3K | −17.7K | `narrowpc`; also lets synthesis drop some image bits |
| Image registers 16 → 7 bits | −8.4K | −10.6K | `narrowimg` (72 flops) |
| Barrel shifts (SHL/SHR by c) | −9.3K | −10.2K | `noshift`; byte-lane only: −9.7K (`byteshift`) |
| Status read mux, excluding counters | ~−3.0K | | `nostatus` (−14.2K) − `comp0` |
| Mover | −9.2K | −11.5K | `nomover` |
| Synchronous clear + ties → async reset, FIFO memory storage (storage keeps ties) | −21.3K | −23.9K | `async` |
| Same, with FIFO storage as reset registers | **−45.5K** | **−50.5K** | `async_fr`. FIFO storage as registers with sync clear alone: −10.5K (`fiforegs`) |
| Program store: 64-word flop register store instead of the SRAM | +709.2K + 8,216 flops | +936.7K (vs 121.9K of macros) | `regp64f8` |

## 4. Contract-neutral reset and storage changes

Three changes need no ISA or config change. Together they are `cn`:
1. Asynchronous reset from `~(rst_n & ena)` on every register, with FLUSH kept
   synchronous.
2. FIFO storage as registers that take the reset.
3. 7-bit image registers.

| Variant | TT synth | Plain | Flops | Ties | U 8x4 | D 8x4 |
|---|---:|---:|---:|---:|---:|---:|
| Base | 462,171.9 | 442,554.7 | 3,921 | 3,929 | 58.4% | 53.7% |
| `async_fr` (1 + 2) | 416,714.0 | 399,744.2 | 3,897 | 8 | 54.2% | 49.0% |
| `async_fr_noena` (drops `ena`, for comparison) | 417,138.0 | 399,742.9 | 3,897 | 8 | 54.2% | 49.1% |
| `async_fr_s2` (1 + 2 + 2-flop release synchronizer) | 418,551.2 | 400,180.1 | 3,899 | 9 | 54.3% | 49.2% |
| **`cn`** (1 + 2 + 3) | **413,427.9** | 398,681.1 | 3,825 | 8 | **53.8%** | **48.6%** |

**Revision 1 corrections.** Keeping `ena` costs nothing measurable: the
version without `ena` is 0.4K larger in TT synthesis and 1.3 µm² smaller in
plain. Keeping FLUSH synchronous is also *smaller* than revision 1's
illegal version: plain `async_fr` 399.7K against revision 1's 418.6K. That
agrees with an independent reviewer's corrected probe (399.3K).

**Reset timing.** With `AREA_RST_SYNC=0`, reset asserts and releases directly
from the pins, so release is asynchronous to `clk` on silicon.
- The TT harness changes `rst_n`/`ena` half a cycle before the edge, so that
  version should be lockstep-equivalent to the model. This was reasoned, not
  simulated.
- Adding the 2-flop release synchronizer costs +1.8K (TT) but delays release by
  2 cycles. That is a reset-timing contract change for the model and tests.
- This study measured both and recommends neither over the other; it is a
  verification decision. Before adoption, both need:
  - `Engine.next_pc` updated (the design of record rejects a PC register without
    synchronous clear);
  - Cyclesim and formal reset models updated;
  - reset recovery/removal checked in STA.

## 5. Measured alternatives (full chip)

U uses the model in section 2.4, with the proportional model in parentheses for
6x4. Δ is against the base TT synthesis (462,171.9). All rows are measured.
Contract impacts are in section 2.2.

| Variant | TT synth | Δ | Plain | Flops | U 8x4 | U 6x4 | D 6x4 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Base | 462,171.9 | 0 | 442,554.7 | 3,921 | 58.4% | 78.1% | 74.7% |
| FIFO depth 16 | 646,544.2 | +184.4K | 623,512.4 | 6,001 | 78.4% | 105% | — |
| FIFO depth 4 | 366,675.9 | −95.5K | 349,883.2 | 2,865 | 48.0% | 64.3% (64.8) | 58.7% |
| FIFO depth 2 | 320,901.5 | −141.3K | 305,491.4 | 2,321 | 43.0% | 57.5% (58.4) | 50.9% |
| Completed counters removed | 450,992.3 | −11.2K | 429,900.8 | 3,793 | 57.1% | 76.5% | |
| Completed counters 16-bit | 457,311.6 | −4.9K | 434,708.7 | 3,857 | 57.8% | 77.4% | |
| PC 7-bit, saturating | 447,882.0 | −14.3K | 434,475.2 | 3,817 | 56.9% | 76.2% | |
| Image registers 7-bit | 453,815.3 | −8.4K | 442,651.1 | 3,849 | 57.5% | 77.0% | |
| No clear on datapath registers (sync) | 458,950.9 | −3.2K | 441,345.9 | 3,921 | 58.1% | 77.7% | |
| Drop `ena` from clear (sync) | 463,160.5 | +1.0K | 445,560.6 | 3,921 | 58.4% | 78.2% | |
| FIFO storage as registers (sync) | 451,645.5 | −10.5K | 434,096.6 | 3,897 | 57.4% | 76.8% | |
| Byte-lane shifts | 452,468.0 | −9.7K | 429,699.5 | 3,921 | 57.5% | 76.9% | |
| No barrel shifts | 452,893.9 | −9.3K | 436,171.7 | 3,921 | 57.5% | 77.0% | |
| No status mux | 447,945.6 | −14.2K | 426,350.8 | 3,790 | 56.9% | 76.1% | |
| No mover | 452,974.8 | −9.2K | 433,233.4 | 3,847 | 57.4% | 76.8% | |
| Scan flop as enable flop (techmap) | 457,445.1 | −4.7K | 432,316.6 | 3,921 | 57.9% | 77.5% | |
| Async reset, FIFO memory storage | 440,836.8 | −21.3K | 423,048.0 | 3,897 | 56.4% | 75.5% | |
| `async_fr` | 416,714.0 | −45.5K | 399,744.2 | 3,897 | 54.2% | 72.5% | 68.2% |
| **`cn`** (section 4) | 413,427.9 | −48.7K | 398,681.1 | 3,825 | **53.8%** | 72.0% | 67.6% |
| `cn` + FIFO depth 4 (`cn4`) | 333,190.7 | −129.0K | 319,210.5 | 2,777 | 44.8% | 60.0% (60.1) | 53.7% |
| `async_fr` + RX 4 deep, TX 8 deep | 378,437.2 | −83.7K | 358,862.7 | 3,373 | 49.9% | 66.8% | |
| Sync clear + FIFO 4 + no counters + 7-bit PC | 338,927.3 | −123.2K | 330,368.2 | 2,633 | 45.1% | 60.4% | |
| Diet E: `async_fr` + FIFO 4 + no counters + 7-bit PC | 313,348.9 | −148.8K | 302,574.5 | 2,617 | 42.7% | 57.2% (57.3) | 50.6% |
| Diet E + 2-flop release synchronizer | 314,195.9 | −148.0K | 302,346.6 | 2,619 | 42.8% | 57.3% | |
| Diet E + no datapath clear | 320,506.4 | −141.7K | 312,133.0 | 2,617 | 43.4% | 58.1% | |
| Diet E + no shifts | 310,914.0 | −151.3K | 293,950.2 | 2,617 | 42.5% | 56.9% | |
| **Diet Ei** = `cn4` + no counters + 7-bit PC | 312,814.4 | −149.4K | 302,524.5 | 2,581 | 42.6% | 57.1% (57.2) | 50.4% |
| **Diet Ei + byte-lane shifts** | **297,314.7** | −164.9K | 290,920.4 | 2,581 | 41.2% | **55.2% (55.1)** | **48.2%** |
| Diet Ei at FIFO depth 8 | 389,803.4 | −72.4K | 381,342.2 | 3,629 | 51.3% | 68.7% | 63.8% |
| Diet E at depth 8, RX 4 deep | 353,019.6 | −109.2K | 342,414.8 | 3,141 | 47.2% | 63.2% | 57.4% |
| Diet Gi = Diet Ei at FIFO depth 2 | 269,171.9 | −193.0K | 263,256.8 | 2,045 | 37.8% | 50.6% (51.1) | 42.9% |
| **Diet Gi + byte-lane shifts** | 257,019.4 | −205.2K | 251,036.0 | 2,045 | 36.7% | **49.2% (49.4)** | 41.2% |
| 16-bit datapath | 318,911.4 | −143.3K | 300,400.5 | 2,641 | 43.3% | 58.0% | |
| 16-bit + FIFO 4 | 266,517.5 | −195.7K | 251,705.3 | 2,097 | 37.7% | 50.4% | |
| Diet Fi = Diet Ei, 16-bit | 217,538.2 | −244.6K | 212,728.9 | 1,813 | 32.8% | 43.9% (43.9) | 35.1% |
| Diet Fi at FIFO depth 8 | 257,976.5 | −204.2K | 253,521.5 | 2,349 | 37.3% | 49.9% (49.6) | 42.1% |
| Diet E + scan-enable techmap | 314,833.7 | −147.3K | 300,068.0 | 2,617 | 42.9% | 57.4% | |

Findings:
- **No clear on datapath registers is worse under async reset** (+7.2K),
  because those flops need ties again. Under async reset, every flop should
  keep its reset.
- **Scan-enable mapping** helps the plain flow (−10.2K) but not TT synthesis
  (−4.7K on base, +1.5K on Diet E). Dropped.
- **Removing shifts outright** saves no more than byte-lane shifts do
  (−9.3K against −9.7K).

**Latch-array FIFO storage (estimate).** This comes from standalone blocks
(TT synthesis); it was not measured in context. The flop baseline is the
async-reset storage-register block, the form the diets use.

| 32-bit FIFO | Flop block (async, storage regs) | Latch, no capture register | Latch + 32-bit capture register |
|---|---:|---:|---:|
| Depth 4 | 11,184.6 | 5,982.1 (−5.2K) | 7,723.9 (−3.5K) |
| Depth 8 | 21,900.0 | 12,757.0 (−9.1K) | 14,498.9 (−7.4K) |

Each block has 1 `lgcp_1` clock gate per word and `dlhq_1` latches.

The whole-queue range runs from two layouts:
- **Upper (8 × no-capture − 2 shared capture registers).** RX FIFOs latch the
  engine `rx` register directly, since `rx` cannot change in the cycle after a
  PUSH. TX FIFOs share two capture registers (host word and mover word).
- **Lower (4 × no-capture + 4 × with-capture).**

In TT synthesis that is **−34.7K to −38.1K at depth 4** and **−66.2K to
−69.7K at depth 8**.

A latch captures half a cycle after its data launches, so latch storage should
need no hold buffers. With that assumption, and removing the replaced flops'
19 µm² hold buffers, the placed saving is:
- depth 4: 55.2–60.2K (38.1–42.0K if latches still get hold buffers);
- depth 8: 109.4–114.4K (72.8–76.6K if they do).

On Diet Ei + byte-lane shifts at depth 4, that gives **48.5–49.1%** at 6x4, or
50.6–51.0% if latches still get hold buffers. On Diet Ei at depth 8 it gives
56.0–56.6% (60.2–60.6%).

The reset-latch variant (`dlhrq`) did not survive the TT flow's latch mapping
(0 latches mapped), so it is not used.

## 6. Program-store organizations

| Store | Program-store cost | vs current |
|---|---:|---:|
| 8 × `1P_64x16_c2` macros (current) | 121,924 µm² macros + a small address-mux adapter | — |
| Flop register store, 64 words | +709.2K TT synthesis, +8,216 flops: **+937K placed** | +815K |
| Flop register store, 32 words, Diet E | `regp32f4E` − Diet E: +347.6K TT synthesis, +4,096 flops: +460K placed | +338K, and half the words |
| Latch store, 32 words (estimate carried over from revision 1, plain flow; not regenerated) | ~126K of `dlhq` + ~50K read mux + ICGs + capture ≈ 185K before placement | > +60K, and half the words |
| 2 × `2P_256x32_c2_bm_bist` (2 engines per macro, 128 words each) | 192,533 | +70.6K; 703 µm wide |
| 4 × `1P_256x32_c2_bm_bist` | 197,954 | +76.0K |
| 4 × `1P_64x64_c2_bm_bist` (half the width unused) | 201,956 | +80.0K |
| 4 × `2P_64x32_c2` | 210,484 | +88.6K; 703 µm wide each |
| 8 × `1P_256x16_c2_bm_bist` | 225,017 | +103.1K |

No option beats the current macros. 64 × 16 is the smallest macro in the
library.

Moving the 2,048 FIFO payload bits into one `2P_64x32_c2` (52.6K µm², exactly
64 × 32) would need 8 head registers, arbitration for up to 16 accesses per
cycle over 2 ports, and different PULL/PUSH timing. It is an architecture change
with a 703 µm-wide macro, so it is not recommended.

## 7. SRAM macro footprints (LEF `SIZE`, all 30 in `libs.ref/sg13cmos5l_sram/lef`)

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
| RM_IHPSG13_1P_512x64_c2_bm_bist | 784.48 × 191.34 | 150,102.4 |
| RM_IHPSG13_1P_1024x8_c2_bm_bist | 146.88 × 336.46 | 49,419.2 |
| RM_IHPSG13_1P_1024x16_c2_bm_bist | 236.80 × 336.46 | 79,673.7 |
| RM_IHPSG13_1P_1024x32_c2_bm_bist | 416.64 × 336.46 | 140,182.7 |
| RM_IHPSG13_1P_1024x64_c2_bm_bist | 784.48 × 336.46 | 263,946.1 |
| RM_IHPSG13_1P_2048x32_c2_bm_bist | 416.64 × 626.70 | 261,108.3 |
| RM_IHPSG13_1P_2048x64_c2_bm_bist | 784.48 × 626.70 | 491,633.6 |
| RM_IHPSG13_1P_4096x8_c3_bm_bist | 236.80 × 618.30 | 146,413.4 |
| RM_IHPSG13_1P_4096x16_c3_bm_bist | 416.64 × 618.30 | 257,608.5 |
| RM_IHPSG13_1P_8192x32_c4 | 1520.16 × 618.30 | 939,914.9 |
| RM_IHPSG13_2P_64x22_c2_bm_bist | 526.03 × 74.87 | 39,383.9 |
| RM_IHPSG13_2P_64x32_c2 | 702.83 × 74.87 | 52,620.9 |
| RM_IHPSG13_2P_256x8_c2_bm_bist | 278.51 × 136.97 | 38,147.5 |
| RM_IHPSG13_2P_256x16_c2_bm_bist | 419.95 × 136.97 | 57,520.6 |
| RM_IHPSG13_2P_256x32_c2_bm_bist | 702.83 × 136.97 | 96,266.6 |
| RM_IHPSG13_2P_512x8_c2_bm_bist | 261.17 × 219.77 | 57,397.3 |
| RM_IHPSG13_2P_512x16_c2_bm_bist | 402.61 × 219.77 | 88,481.6 |
| RM_IHPSG13_2P_512x32_c2_bm_bist | 685.49 × 219.77 | 150,650.1 |
| RM_IHPSG13_2P_1024x16_c2_bm_bist | 402.61 × 385.37 | 155,153.8 |
| RM_IHPSG13_2P_1024x32_c2_bm_bist | 685.49 × 385.37 | 264,167.3 |

In `RM_IHPSG13_1P_64x16_c2`:
- All 43 signal pins are on the bottom edge (y 0–0.26 µm, Metal2).
- VDD!, VSS! and VDDARRAY! are on Metal4.
- OBS covers the whole footprint on Metal1 and Metal3, most of Metal2, and about
  49% of Metal4.

## 8. Recommendations

### (a) 8x4 first harden

Harden `configs/instruction-sram-32.json` unchanged, as decided.
- **Expected utilization is 58.4%, with D 53.7%.** This is measured by the local
  TT-flow run, not predicted.
- The expected TT synthesis report is about 465K µm².
- Timing met at typical: setup WS +6.59 ns before routing (+2.94 ns post-route), and hold met after repair.
- Detailed routing reached 0 violations at iteration 46, and the re-routes after
  antenna repair also ended at 0. Post-route STA: typical and fast met; slow
  setup −5.09 ns, dominated by `rst_n` input paths (`docs/hardening.md` section 7).

It fits, but it is at the edge; this is not a comfortable margin. If the action's
routing fails, apply these in order:
1. **`cn`**: −48.7K TT synthesis, predicted **53.8% (D 48.6%)**. This needs no
   ISA or config change, only the RTL and verification work in section 4.
2. **Hold-buffer flow settings** (hardening owner). They add 74.7K µm² (12.8% of
   the placed standard cells). Most of it is probably fast-corner hold repair
   (section 2.4), so relaxing the margin may recover little. This study does not
   recommend relaxing them without a fast-corner hold check.
3. **ISA changes, each about 1 point:** remove the completed-instruction
   counters (−11.2K), a 7-bit PC (−14.3K), byte-lane shifts (−9.7K). The
   counters and the PC together with `cn` at depth 8 (Diet Ei at depth 8) give
   51.3%.

### (b) 6x4 ranked diet

**Budget.** The demonstrated routable point is D 53.7% at 8x4. At 6x4 the
macros take 13.5% of the core and block 74% of the width in their bands (section
8c), so this study targets **D ≤ ~50%, which is U ≤ ~57% at 6x4**. D 45%
corresponds to U 52.4%.

Items are ranked by placed µm² saved per unit of contract, verification and tool
risk. Rows 1–7 are cumulative, and every cumulative row was measured.

| # | Item | Placed saved | Cumulative TT synth | U 6x4 | D 6x4 | Contract / verification impact |
|---|---|---:|---:|---:|---:|---|
| 0 | Base | | 462.2K | 78.1% | 74.7% | |
| 1 | Async reset from `~(rst_n & ena)` for every register, including FIFO storage; FLUSH stays synchronous | 50.5K | 416.7K | 72.5% | 68.2% | None if released directly; +2-cycle release with a synchronizer (section 4). RTL: `Engine.next_pc`, reset models, recovery/removal STA. |
| 2 | 7-bit `image_length`/`image_loaded` | 5.0K | 413.4K (`cn`) | 72.0% | 67.6% | None |
| 3 | FIFO depth 8 → 4, TX and RX | 108.2K | 333.2K (`cn4`) | 60.0% | 53.7% | **Config/ISA**: `docs/isa.md:14` and the monorepo's `python/protocol_emulator/contracts.py:46` (`Literal[8, 32]`) must allow 4, and the images must be reassembled (`fifo_words` is bound in every image). **`flagship-scenario.json` prefills 8 TX words into engine 0 and must change**, for example to a host top-up. |
| 4 | Remove `completed_instructions` | | | | | **ISA change**: READ_SELECT 5 becomes reserved or 0, and the strict-overflow text (`isa.md:94`) changes. Needs an ISA version bump plus model and host decode updates. |
| 5 | PC 24 → 7 bits, saturating | 26.1K (4 + 5) | 312.8K (Diet Ei) | 57.1% | 50.4% | **ISA change**: READ_SELECT 3 after an out-of-range jump or fault reads 127 instead of the target. Fault behaviour is unchanged. |
| 6 | Byte-lane SHL/SHR (c ∈ {0, 8, 16, 24}) | 17.1K | **297.3K** | **55.2%** | **48.2%** | ISA restriction (`isa.md:60-61`). All 15 shifting images use c = 24, so no firmware changes. The assembler must reject other counts. |
| 7a | FIFO depth 4 → 2 | 54.5K | 257.0K | **49.2%** | 41.2% | Same as 3, plus less buffering; check host and mover latency. |
| 7b | *or* latch-array FIFO storage at depth 4 (ICG + `dlhq`) | ~55–60K (estimate; 38–42K if latches need hold buffers) | ~259–263K (estimate) | ~48.5–51% | | No contract change, but high tool risk (section 8c) |
| 7c | *or* 16-bit datapath (Diet Fi = Diet Ei, 16-bit, no byte-lane shifts) | 119.5K vs Diet Ei | 217.5K | 43.9% | 35.1% | **Last resort.** It loses 32-bit words, and every SHL/SHR by 24 (15 of 19 images, 3 of 4 flagship images) becomes illegal (`c < datapath width`). |
| — | Not worth it | | | | | See the list below. |

Rejected items:
- **Datapath no-clear:** −3.2K sync; +7.2K under async reset.
- **Scan-enable techmap:** −4.7K, and +1.5K on Diet E.
- **Dropping `ena`:** it saves nothing and breaks the ISA.
- **Removing shifts outright:** it saves no more than byte-lane shifts and breaks 15 images.
- **Removing the status mux (−14.2K) or the mover (−9.2K):** these break the host contract or remove a differentiator.
- **RX-only depth 4** (Diet E at depth 8 with RX 4): 63.2%.
- **2-flop release synchronizer:** +1.8K. It is a reset-timing decision, not an area one.

**Recommended 6x4 insurance variant: items 1–6.** That is 297.3K TT synthesis,
predicted **55.2% (proportional model 55.1%) with D 48.2%**. D is 5.5 points
below the 8x4 run that routed, and synthesis is 164.9K below the base.

If 6x4 still fails to route, add 7a (depth 2, 49.2%). Keep 7b in reserve for
when 4-deep queues must be kept and the latch tool risk has been retired. Hold
7c back as the last resort.

### (c) Risks

- **Routing layers.** TT sets `RT_MAX_LAYER Metal4` for CMOS5L
  (`tech.py IHPTech.project_top_metal_layer`, used by `project.py`), and the
  precheck forbids TopMetal1 in projects. The layers are:
  - Metal1: horizontal, 0.42 µm pitch, mostly cell pins.
  - Metal2: vertical, 0.48 µm.
  - Metal3: horizontal, 0.42 µm.
  - Metal4: vertical, 0.48 µm, also carrying the PDN. The TT repo config has
    VPWR/VGND Metal4 stripes at a 67.44 µm pitch, 2.1 µm wide.

  In the 8x4 run, Metal3 is the constraint: 574 of 617 overflows, 66.3% usage
  against 21.6% on Metal4.
- **Macro-induced congestion at 6x4.** The macros obstruct Metal1 and Metal3
  over their whole footprint, so no horizontal wire crosses a macro. In the
  edge-row floorplan (`docs/hardening.md` section 4), the four macros of each
  row block horizontal routing across **947.2 µm**:
  - 6x4: **73.8%** of the 1,283.5 µm core width.
  - 8x4: **55.1%** of 1,718.4 µm.

  Cells placed in the macro bands, and the 43-pin access region at every macro
  edge, therefore have far fewer horizontal escape tracks at 6x4. Macros are
  also 13.5% of the 6x4 core against 10.1% at 8x4. **The congestion risk at 6x4
  is higher than at 8x4 at equal density**, which is why the budget above targets
  D ≈ 48–50% instead of 53.7%. There is no 6x4 routing data point yet.
- **Macro placement.** Pins must face the logic (bottom row FS, top row N), and
  macros stay on the core edges. The 6x4 placement in `docs/hardening.md` passes
  the lattice check, with its top row on off-grid x.
- **PDN and DRC.** The macros need the SRAM PDN wrapper. `TinyTapeout/tt-support-tools#190`
  (KLayout SG13G2 DRC fails inside hard SRAM macros) was still open when checked
  on 2026-09-24. Neither issue affects area.
- **Async reset.** It adds a reset tree with about 3,800 loads. Design repair
  already buffers the synchronous-clear net of the same fanout, so the model
  keeps that overhead. Recovery/removal and release timing must be signed off
  (section 4).
- **Latches and ICGs (7b).**
  - `lgcp_1` is on the PDK's synthesis exclusion list, so it must be
    instantiated explicitly. Whether LibreLane accepts an instantiated excluded
    cell was not checked.
  - Hardcaml has no latch primitive. It needs instantiated cells and a separate
    Cyclesim model, as the SRAM already has.
  - STA with latch time-borrowing, CTS through ICGs, and SymbiYosys latch
    modelling all need proof.
  - The RX-source-stability argument must be proven, not assumed.
- **Estimate error.**
  - The model has one calibration run. The two-term and proportional models
    agree within about 1 point for the candidates.
  - Flows disagree by up to about 8K for single items.
  - Diet variants have not been placed. Their hold-buffer and design-repair
    rates are assumed to scale as in the base.

## 9. Caveats

- Mapped area is not placed area. Every utilization except the base 8x4 58.4% is
  a model prediction.
- Only the typical corner was used.
- Per-block numbers are standalone. Latch, ICG and latch-store numbers are
  estimates, and are marked as such.
- The knobs are area probes in a scratch copy of the Hardcaml source. None has
  been simulated, formally checked or compared with the reference model. The
  async-reset, 7-bit PC, byte-shift and FIFO-depth variants all need model,
  formal, test and firmware updates before adoption.
- The local LibreLane run is the hardening task's evidence, not the result of
  record. Its detailed routing had not finished when these figures were read;
  it later reached 0 violations (section 2.4).

## 10. Reproduction

Everything needed to rerun the study is in `docs/area-study/` (listed below).
The work directory (`<local work dir>/tt-work/area/`) also keeps:
- `runs2/<plain|ll66>/<variant>/{stat.txt,pre_map_stat.txt,synth.ys,time.txt}` (Yosys logs and the dune build directory were deleted);
- `vg2/` with `MANIFEST.sha256` (55 Verilog files: 54 emitted variants and blocks, plus the hand-written latch sketch);
- `summary2.json`.

Revision 1's files are archived in `v1/`, with its `analyze.py` latch count
fixed to exclude `dlygate` cells.

Steps:
1. Apply the patch in `hardcaml/` (`patch -p1 < area-knobs-v2.patch`).
2. Build: `dune build --build-dir <dir> -j 2 bin/generate_refinement.exe
   bin/generate.exe bin/area_blocks.exe`.
3. Set `W` in the scripts, then run `gen_all.sh` → `vg2/`.
4. Run `run_all.sh` on a compute node (measured 22 + 3 min on 2 CPUs, peak
   3.9 GB; the `ll66` mode needs apptainer and the LibreLane 3.1.0.dev3 SIF).
5. Run `analyze2.py`.

Key hashes:
- `base.v` `3a721a1b4c6f…`
- `cn.v` `2feafacf8991…`
- `dietEi_byte.v` `4b9c9b3a3929…`
- `async_fr.v` `c5a5036eed62…`
- `dietE.v` `8f88718f6343…`
- `fifo4.v` `2fe0e488a3d8…`
- `dietF16.v` `d3cd4cdf4333…`

**Reproduction check (2026-09-25).** I applied `area-knobs-v2.patch` to a fresh copy
of this repository's `hardcaml/` (`patch -p1`), built it, and emitted `base`,
`cn` and `dietEi_byte`. All three are byte-identical to the manifest.

The scripts read their locations from the environment: `W` (work directory),
`OSS` (OSS CAD Suite `bin/`), `PDK_ROOT` (IHP-Open-PDK at `2bbec755`) and `FLOW`
(the local LibreLane run directory with its SIF). They were used with absolute
paths on the study machine; only those assignments differ from the versions run.

### Files in `docs/area-study/`

| File | Contents |
|---|---|
| [`area-knobs-v2.patch`](area-study/area-knobs-v2.patch) | Knob patch against `hardcaml/` (monorepo 18676a4; applies with `patch -p1` inside this repository's `hardcaml/`). Byte-identical to the patch used for every number above. |
| [`scripts/gen_all.sh`](area-study/scripts/gen_all.sh) | Emit every variant into `vg2/` with a SHA-256 manifest |
| [`scripts/synth3.sh`](area-study/scripts/synth3.sh) | Synthesis flows `plain` and `ll66` (the LibreLane 3.1.0.dev3 "AREA 0" replica) |
| [`scripts/run_all.sh`](area-study/scripts/run_all.sh) | Run every synthesis job, two at a time |
| [`scripts/analyze2.py`](area-study/scripts/analyze2.py) | Tabulate results and apply the utilization model |
| [`stub/AREA_0.abc`](area-study/stub/AREA_0.abc), [`stub/synthesis.abc.sdc`](area-study/stub/synthesis.abc.sdc) | ABC script and constraints LibreLane 3.1.0.dev3 generated for this design |
| [`stub/sram_bb.v`](area-study/stub/sram_bb.v) | SRAM blackbox used during synthesis |
| [`stub/latch_map.v`](area-study/stub/latch_map.v), [`stub/scan_enable_map.v`](area-study/stub/scan_enable_map.v) | Techmaps for the latch and scan-enable experiments |
| [`vg2/blk_fifo_latch.v`](area-study/vg2/blk_fifo_latch.v) | Latch-array FIFO area sketch (not verified RTL) |
| [`cfg/*.json`](area-study/cfg/) | Variant configurations |
