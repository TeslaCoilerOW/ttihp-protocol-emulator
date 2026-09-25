# Hardening the SRAM-macro design at 8x4 (task 1.4)

This document covers the recipe, the decisions and the evidence for taking
`tt_um_teslacoilerow_protocol_emulator` through the official Tiny Tapeout
CMOS5L gds action. The design is configured as `configs/instruction-sram-32.json`
with eight `RM_IHPSG13_1P_64x16_c2` instruction SRAMs. Last updated 2026-09-24.

**Status.** The configuration is written and passes every static check we can
run locally (section 6). A local LibreLane 3.1.0.dev3 run that mirrors the
action got through synthesis, macro placement, the checked PDN step, placement,
CTS and global routing (617 overflow, mostly Metal3). A second run, with the
thread fix, was in detailed routing at the time of writing: 34,515 violations
after iteration 0 (section 7). Routing convergence is the main open risk. Nothing in this document has been run by the
GitHub action yet, so none of it is a TT result. Section 9 lists what only the
action can confirm.

## 1. How the action consumes this repository

Read from source on 2026-09-24:

| Fact | Source |
|---|---|
| `gds.yaml` uses `TinyTapeout/tt-gds-action@ihp-cmos5l` with `pdk: ihp-sg13cmos5l`. | `.github/workflows/gds.yaml` |
| The action checks out `tt-support-tools` at branch `ihp-sg13cmos5l`. It then runs `tt_tool.py --create-user-config --ihp` and `tt_tool.py --harden --ihp`. | `tt-gds-action/action.yml` (branch `ihp-cmos5l`) |
| LibreLane is `librelane==3.1.0.dev3` (the action's default `librelane-version`). It runs `--dockerized`, so it uses image `ghcr.io/librelane/librelane:3.1.0.dev3`. | `action.yml`; `project.py` `harden()` |
| The harden command is `python -m librelane --dockerized --pdk ihp-sg13cmos5l --manual-pdk --run-tag wokwi --force-run-dir runs/wokwi src/config_merged.json`, run from the repository root. | `project.py` `harden()` |
| `src/config_merged.json` is `src/config.json` updated with `DESIGN_NAME`, `VERILOG_FILES` (= `info.yaml` `source_files`), `DIE_AREA`, `FP_DEF_TEMPLATE` (`dir::../tt/tech/ihp-sg13cmos5l/def/tt_block_8x4_pgvdd.def`), `VDD_PIN VPWR`, `GND_PIN VGND` and `RT_MAX_LAYER Metal4`. Every other key in `src/config.json` passes through unchanged, including `MACROS`, `PDN_MACRO_CONNECTIONS`, `PDN_CFG` and `meta`. | `project.py` `create_user_config()` and `create_merged_config()` |
| `dir::` paths resolve relative to `src/`. The whole repository is mounted, so `dir::../macros/...` and `dir::../models/...` work. The same pattern is used by passing entries (`tt_um_loom`, `MarcosAsh/protocol-emulator`). | Their `src/config.json` files and green runs |
| The PDK is IHP-Open-PDK `2bbec755dc67ca3db0261c3d6163e15735d66710`, a full git checkout. `ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram` is a symlink to `ihp-sg13g2/libs.ref/sg13g2_sram`, so `pdk_dir::` SRAM paths would also resolve. | `tt-gds-action/install_sg13cmos5l.sh`; GitHub contents API at that revision |
| 8x4 is a valid tile size: die `0 0 1724.16 710.64`. The DEF has rows from (2.88, 3.78), 3580 sites of 0.48 and 186 rows of 3.78, so the core is x 2.88..1721.28, y 3.78..706.86. The 43 I/O pins are Metal4 pins on the top edge at y 710.14, x 29.76..191.04. | `tt-support-tools` `d66cf179e` (2026-09-21), `tech/ihp-sg13cmos5l/{tile_sizes.yaml,def/tt_block_8x4_pgvdd.def}` |
| On cmos5l, the precheck runs KLayout SG13CMOS5L DRC (the PDK's `ihp-sg13cmos5l.drc`, default options), zero-area, top-cell and forbidden layers (TopMetal1 is forbidden), the pin check, the boundary check, the layer check (`SRAM.drawing` and `DigiBnd.drawing` are allowed since PR #187), the cell-name check and the analog pin check. **Magic DRC is not run on cmos5l.** | `precheck/precheck.py`, `precheck/tech_data.py` |
| The pin check requires every Metal4 `VPWR`/`VGND` port to be at least 2.1 um wide and to reach within 10 um of **both** the top and the bottom block edge. | `precheck/pin_check.py` |
| `gl_test` copies `tt_submission/*.v` (the final netlist) to `test/gate_level_netlist.v` and runs `GATES=yes make` in `test/`. | `tt-gds-action/gl_test/action.yml` |

Plugins: LibreLane imports any `librelane_plugin_*` module on `sys.path`, and
the repository root is on it. `WilliamZhang20/protocol-emulator` uses this
mechanism for its `Project.ExtendPowerStripes` step. **We do not need a
plugin** (see section 3).

## 2. What is in the recipe

| File | Role |
|---|---|
| `src/config.json` | The template, plus the SRAM block (`MACROS`, `PDN_MACRO_CONNECTIONS`, `PDN_CFG`, Magic/LVS keys, `OPENROAD_THREADS`) and four `FP_PDN_V*` stripe keys. |
| `src/sram_pdn_cfg.tcl` | LibreLane 3.1.0.dev3's default PDN script (verbatim), with the macro grid replaced by a checked `pdngen` wrapper. |
| `macros/RM_IHPSG13_1P_64x16_c2/` | Vendored GDS, LEF, three Liberty corners and CDL; byte-identical to IHP-Open-PDK `2bbec755` (`macros/README.md`). |
| `macros/LICENSE.IHP-Open-PDK` | Apache-2.0 text for those files. |
| `macros/check_macro_floorplan.py` | A tool-free floorplan checker. Run `python3 macros/check_macro_floorplan.py`. |
| `models/blackbox/RM_IHPSG13_1P_64x16_c2.v` | The interface-only module used as the macro's `nl` view (scaffold's file). |

The scaffold agent owns `info.yaml` and `src/*.v`. `source_files` stays
`project.v` and `protocol_emulator_core.v`. The macro module is defined only
by the `MACROS` `nl` view, and **the behavioural models must never be listed
in `source_files`**. `test/Makefile` already adds the IHP behavioural models
for both RTL and `GATES=yes`. That is the same approach as `tt_um_loom`,
whose `gl_test` passes with an unmodified model that has no power ports.

### Key settings

| Key | Value | Why |
|---|---|---|
| `MACROS.RM_IHPSG13_1P_64x16_c2.instances` | eight `core.instruction_sram_e<e>_<lo|hi>` | The flattened instance paths. `yosys` `flatten` of `src/project.v` + `src/protocol_emulator_core.v` reports exactly these names (checked 2026-09-24). LibreLane's CheckMacroInstances rejects anything else (loom run 1). |
| `lib` keys | `*_typ_*`, `*_fast_*`, `*_slow_*` | The cmos5l STA corners are `nom_typ_1p20V_25C`, `nom_fast_1p32V_m40C` and `nom_slow_1p08V_125C`. Keys named after the process corner give each corner its own lib. The loom project found that `nom_*`/`min_*`/`max_*` keys time every corner with typ. The fast lib is characterized at −55 °C and stands in for −40 °C. |
| `PDN_MACRO_CONNECTIONS` | `VDD!` and `VDDARRAY!` to `VPWR`, `VSS!` to `VGND`, per instance | These are the LEF's real supply pin names (checked by `check_macro_floorplan.py`). TT provides one supply. |
| `PDN_CFG` | `dir::sram_pdn_cfg.tcl` | Section 3. |
| `FP_PDN_VPITCH / VWIDTH / VSPACING / VOFFSET` | 67.44 / 2.1 / 3.52 / 25.40 | Section 3. VWIDTH keeps the template's 2.1 (the precheck minimum). |
| `ERROR_ON_ILLEGAL_OVERLAPS` | false | This covers one analysed case (section 3). Watch `magic__illegal_overlap__count`: expect a few dozen boxes (8 macros × 4 POWER stripes, possibly split). |
| `ERROR_ON_MAGIC_DRC` | false | Magic's cmos5l tech lacks the SRAM exceptions. Loom saw ~58k in-macro Magic errors. The precheck does not run Magic, and its KLayout deck passed on loom's merged GDS. |
| `MAGIC_EXT_ABSTRACT_CELLS` | `["RM_IHPSG13_.*"]` | This blackboxes the SRAM for LVS extraction. |
| `MAGIC_MACRO_STD_CELL_SOURCE` | `PDK` | Same as the reference projects. It is harmless if KLayout streams out. |
| `PL_TARGET_DENSITY_PCT`, `CLOCK_PERIOD` | 60, 20 ns | Template values. Predicted utilization is ~52% (51–53%) of the core, and standard-cell density outside the macros is ~47% (`docs/area-study.md`). 20 ns matches `info.yaml` `clock_hz` 50 MHz. The macro's slow-corner clock-to-output is ~5.0–5.2 ns (Liberty), and pre-route STA of this netlist had +8.6 ns setup slack (typ). |
| `OPENROAD_THREADS` | 4 | LibreLane 3.1.0.dev3 builds OpenROAD's thread argument as `str(OPENROAD_THREADS) or ...`. When the key is unset, that is `"None"`, so every OpenROAD step is called with `-threads None` (ORD-0032) and detailed routing runs single-threaded (observed in run1, section 7). 4 matches the runner's vCPUs. This key is our addition to the template. |
| Halos | LibreLane defaults, 10 um | Not overridden. The macro GDS NWell extends 0.225 um beyond the LEF box on the pin edge, and the halo covers it. |

## 3. Macro power: aligned full-height Metal4 stripes, no plugin

On sg13cmos5l, the block's only PDN stripe layer is Metal4, and TopMetal1
belongs to `tt_top` (the precheck forbids it in a user block). The macro's
supply pins are Metal4 columns, so the only possible connection is a Metal4
stripe lying inside a same-net Metal4 pin column. The pin check requires
full-height stripes, so every stripe that crosses a macro has to run through
it.

There are two public ways to do this:

* **tt_um_loom (adopted).** Choose the stripe grid so that the stripes
  crossing each macro land inside same-net power columns. Then let `pdngen`
  draw them straight through: the wrapper marks macros PLACED only during
  `pdn::build_grids`. Loom verifies the result in the same step and fails fast.
  Branch `sram-smoke` passed gds, precheck (including KLayout SG13CMOS5L DRC
  with 0 violations) and gl_test with `RM_IHPSG13_1P_512x16_c2_bm_bist`
  (run 35377845679). The loom 6x4 core design is green through run
  35940928210 (2026-09-24).
* **WilliamZhang20 / MarcosAsh / PRISM.** A `Project.ExtendPowerStripes`
  LibreLane plugin (`librelane_plugin_sram_pdn.py` + `odb_sram_stripes.py`)
  rewrites the stripes over the macro after `GeneratePDN`. These projects set
  `ERROR_ON_PDN_VIOLATIONS 0` and `RUN_MAGIC_DRC 0`. Their stripe finder is
  written for macros whose LEF declares only a few power rectangles (1024x8,
  512x16), and it hard-codes one macro name and LEF path. The William and
  Marcos repos also monkeypatch `librelane.steps.netgen` JSON parsing.

We chose the loom method for three reasons. First,
`RM_IHPSG13_1P_64x16_c2.lef` declares **every** power column as a pin (20
`VSS!`, 20 `VDD!`, 16 `VDDARRAY!` rectangles). Second, it has the **same
column lattice** as the 512x16: the same-net pitch is 11.24 um, POWER to
GROUND is 5.62 um, and the right half is 0.67 um off the left half's grid.
Third, the loom method keeps pdngen's own checks fatal instead of deferring
or disabling them.

Stripe numbers (derivation also in the header of `src/sram_pdn_cfg.tcl`):

* 64x16 columns (macro-local x0, all 2.81 um wide): `VDD!`/`VDDARRAY!` at
  4.26 + 11.24k and 151.05 + 11.24k; `VSS!` at 9.88 + 11.24k and
  145.43 + 11.24k. `VDD!` spans y 0..38.825 (the pin edge), `VDDARRAY!` spans
  y 45.465..64.36, and `VSS!` runs full height.
* VPITCH 67.44 = 6 × 11.24 steps over the irregular middle band (x 88..151).
  VSPACING 3.52 puts GROUND at 3.52 + 2.1 = 5.62 um right of POWER.
* A macro at **x = 11.04 + 67.44 k** gets four pairs: POWER at local 17.24,
  84.68, 152.12 and 219.56, and GROUND at 22.86, 90.30, 157.74 and 225.18.
  The worst-case margin to a column edge is 0.020 um (the checker prints
  every stripe).
* **VOFFSET is 25.40, not loom's 26.36.** With 26.36, the 26th pair on the
  8x4 core would span x 1714.19..1721.91, which straddles the core edge at
  1721.28. pdngen would then either drop that pair or clip it. A clipped
  stripe narrower than 2.1 um fails the precheck. Shifting the whole lattice
  0.96 um (two sites) left makes the last pair end 0.33 um inside the core.
  The geometry relative to each macro is unchanged, and x stays on the site
  grid for even k.
* The POWER stripes cross the `VDD!`/`VDDARRAY!` split. The LEF has Metal4
  OBS there (local y 39.085..45.205), and Magic reports it as an illegal
  overlap (waived). We flattened our vendored GDS with KLayout 0.30 (from the
  LibreLane SIF, script in section 6). Strictly inside the band y
  (38.825, 45.465) there is **no Metal4 (any datatype), no Via3 and no
  TopMetal1 within 1 um** of the four POWER stripes' x-ranges (`BAND_HITS 0`).
  The only nearby shapes are the pin columns themselves and Via3 inside them.
  So the stripe joins only `VDD!` to `VDDARRAY!`, and both are VPWR.

`src/sram_pdn_cfg.tcl` fails `OpenROAD.GeneratePDN` in any of these cases:

1. A stripe over a macro is not inside same-net columns. The only allowed gap
   is ≤ 7 um between two same-net pins.
2. One of the 24 macro supply pins carries no stripe.
3. A tall VPWR/VGND stripe is narrower than VWIDTH, or ends more than 10 um
   from the bottom or top die edge (this mirrors the precheck's power-port
   rule; it is our addition).
4. `check_power_grid` fails on VPWR or VGND.

The wrapper depends on the four internal steps of OpenROAD's `pdngen`
(`pdn::check_setup`, `build_grids`, `write_to_db`, `reset_shapes`) in the
OpenROAD pinned by LibreLane 3.1.0.dev3. If the action ever bumps LibreLane
and they change, the wrapper raises a clear error; it does not silently
misbehave.

## 4. Floorplan

```
 y 710.64 ┌───────── die 1724.16 × 710.64 (8x4) ────────────────────────────────────────────────┐
          │ I/O pins x 29.76..191.04 (top edge)                                                   │
 y 703.18 │           [e2_lo N][e2_hi N]                 [e3_lo N][e3_hi N]                       │
 y 638.82 │            pins ↓    pins ↓                    pins ↓   pins ↓                        │
          │                                                                                       │
          │                 standard-cell logic: full width, y ≈ 78..629, no macros               │
          │                                                                                       │
 y  68.14 │     pins ↑    pins ↑                  pins ↑    pins ↑                                │
 y   3.78 │   [e0_lo FS][e0_hi FS]              [e1_lo FS][e1_hi FS]                               │
          └───────────────────────────────────────────────────────────────────────────────────────┘
```

| Instance | Location (x, y) | Orientation | k in x = 11.04 + 67.44 k |
|---|---|---|---|
| `core.instruction_sram_e0_lo` | 145.92, 3.78 | FS | 2 |
| `core.instruction_sram_e0_hi` | 415.68, 3.78 | FS | 6 |
| `core.instruction_sram_e1_lo` | 955.20, 3.78 | FS | 14 |
| `core.instruction_sram_e1_hi` | 1224.96, 3.78 | FS | 18 |
| `core.instruction_sram_e2_lo` | 415.68, 638.82 | N | 6 |
| `core.instruction_sram_e2_hi` | 685.44, 638.82 | N | 10 |
| `core.instruction_sram_e3_lo` | 1090.08, 638.82 | N | 16 |
| `core.instruction_sram_e3_hi` | 1359.84, 638.82 | N | 20 |

Rules applied:

* All 43 signal pins are Metal2 stubs on the macro's LEF bottom edge. **The
  pin edge always faces the free logic region.** Bottom-row macros are FS
  (pins on top), and top-row macros are N (pins on the bottom). Loom's
  N-with-pins-6 um-from-the-core-edge run never converged in detailed
  routing. The FS pins-up run converged in 3 iterations.
* Macros sit on the core edges, so no logic is enclosed between macro rows.
  The whole band y ≈ 78..629 (≈ 950k um²) is free of macros, and so are the
  channels between macros.
* The top row stays clear of the I/O pin span (it starts at x 415.68).
* Within a pair, macros are 4 stripe pitches apart: a 32.96 um gap, more
  than 2 × 10 um halo. Even k keeps every x on the 0.48 um site grid. y
  values are row boundaries (3.78 = row 0; 638.82 = row 169).
* Only N/FS: they keep the Metal4 columns vertical and at the same x.
  Mirroring about X does not move them.

**Deviation from the plan's "existing routed floorplan".** The monorepo's
engineering placement had two mid-die rows at y = 160 and 460, x = 112, 400,
1008 and 1296, all N. We did not reuse it, for three reasons:

1. Its x values are not on the stripe lattice, so under the TT PDN rules the
   macros could not be powered.
2. Its global route did not complete. `docs/physical.md` (monorepo) records
   721 overflowing resources: 94 on Metal2, 413 on Metal3, 89 on Metal4 and
   125 on TopMetal1. The failed step produced no ODB. That run could also
   route on TopMetal1, which TT's `RT_MAX_LAYER Metal4` removes.
3. Mid-die rows split the core into enclosed bands.

The nearest lattice-snapped version of that floorplan is
x = 145.92, 415.68, 1022.64 and 1292.40 (k = 2, 6, 15, 19; the last two
are off the 0.48 site grid), with y 160 / 460 and orientation N. It passes
`check_macro_floorplan.py` (run on a scratch copy, 2026-09-24). It is kept
only as a fallback, and we do not expect it to route better.

**6x4 insurance-track placement (task 1.4a).** The same stripe keys work
unchanged on 6x4. The core is x 2.88..1286.40, and the last pair ends at
1248.87, with no straddle. `check_macro_floorplan.py` passes this placement
with `tiles: "6x4"`:

* Bottom row, FS, y 3.78: x = 11.04, 280.80, 550.56 and 820.32.
* Top row, N, y 638.82: x = 213.36, 483.12, 752.88 and 1022.64.

The top row uses odd k (off the 0.48 site grid, which MarcosAsh's passing
off-grid macros suggest is tolerated), because at even k it would run into
the I/O span or the right edge. Macros then take 13.5% of the core. Area
feasibility is task 1.4a's question (`docs/area-study.md`).

## 5. Issue #190 (KLayout DRC errors inside SRAM macros)

`TinyTapeout/tt-support-tools#190` is still open. It reports the
**ihp-sg13g2** precheck on `main` (`ihp-sg13g2.drc`) flagging 1,830
`Sdiod.d`/`Sdiod.e`/`Cnt.c.digibnd` violations inside
`RM_IHPSG13_1P_512x8_c3_bm_bist`. We judge that it does not block us:

* Our flow is the cmos5l precheck, which runs `ihp-sg13cmos5l.drc` from PDK
  `2bbec755`. It is a different deck from the one in the issue.
* On cmos5l, loom's merged GDS with `RM_IHPSG13_1P_512x16_c2_bm_bist` passed
  "KLayout SG13CMOS5L DRC" with 0 violations over 333 rules. That GDS
  contains the full macro hierarchy, 48 `RM_IHPSG13_*` cells. `tt_um_loom`,
  `MarcosAsh/protocol-emulator` (three 512x16), `WilliamZhang20` (1024x8) and
  `kdp1965` PRISM (8x4) all have green gds workflows, which include the
  precheck job.
* For our exact macro, monorepo job `22626482` ran standalone CMOS5L DRC on
  `RM_IHPSG13_1P_64x16_c2.gds` and found 0 markers across 332 categories
  (`docs/physical.md`). That run used the monorepo's `physical-assets-v4`
  deck, which may include a local overlay, so it is **supporting evidence,
  not proof**.
* The 64x16 GDS uses 27 layer/datatype pairs. All of them are in the cmos5l
  precheck's `valid_layers`, including `SRAM.drawing` 25/0 and
  `DigiBnd.drawing` 16/0 (mapped with the PDK's `sg13cmos5l.lyp`). It has no
  TopMetal1.

The residual risk is a `c2`-specific rule hit that the larger macros did not
show. Only the action's precheck settles it.

## 6. Local checks run (2026-09-24)

| Check | Result |
|---|---|
| `python3 macros/check_macro_floorplan.py` | **PASS.** Config is valid JSON; all referenced files exist; the 8 instance keys equal the flattened RTL paths; `PDN_MACRO_CONNECTIONS` cover `VDD!`/`VDDARRAY!`/`VSS!` for all 8; placements are inside the core, ≥ 2 halos apart and clear of the I/O span; 26 stripe pairs fit the core; each macro has 8 stripes inside same-net columns (VDD! ×4, VDDARRAY! ×4, VSS! ×4). |
| Negative controls, with the checker run on perturbed copies | Each one **FAILS** as expected: macro x off-lattice by 0.48; VOFFSET 26.36 (straddle); halo overlap; top macro under the I/O pins; a missing `VDDARRAY!` hookup; a wrong instance key; orientation E. |
| `yosys` (OSS CAD Suite 0.67): `hierarchy; proc; flatten; select -list t:RM_IHPSG13_1P_64x16_c2` | Eight cells, `core.instruction_sram_e{0..3}_{lo,hi}`. |
| Vendored views vs. upstream | GDS, LEF, 3 libs, CDL and the Verilog model are byte-identical between GitHub at `2bbec755` and the local PDK copy. |
| KLayout GDS band check (`klayout -b` in `librelane-3.0.0rc1.sif`; script below) | `BAND_HITS 0`; the layer inventory is listed in section 5. |
| `tclsh`: `info complete` on `src/sram_pdn_cfg.tcl` | 1 (the script parses). The OpenDB calls themselves only run inside OpenROAD. |

The KLayout band check (`klayout -b -r band.py -rd gds=<macro.gds>`):

```python
import pya
ly = pya.Layout(); ly.read(gds); top = ly.top_cell(); dbu = ly.dbu
bad = 0
for li in ly.layer_indexes():
    info = ly.get_info(li)
    if info.layer not in (50, 49, 126):      # Metal4, Via3, TopMetal1 (any datatype)
        continue
    reg = pya.Region(top.begin_shapes_rec(li)); reg.merge()
    for c in (17.24, 84.68, 152.12, 219.56):  # POWER stripe centres, macro-local
        box = pya.DBox(c - 1.05 - 1.0, 38.825 + 0.001, c + 1.05 + 1.0, 45.465 - 0.001)
        bad += (reg & pya.Region(box.to_itype(dbu))).count()
print("BAND_HITS", bad)
```

## 7. Local LibreLane run mirroring the action

The scripts are in `<local work dir>/tt-work/sram-flow/`:

* `setup.sh` (Slurm job 23713655, COMPLETED in 19 min). Pulled
  `docker://ghcr.io/librelane/librelane:3.1.0.dev3` into
  `librelane-3.1.0.dev3.sif`. This is the action's exact LibreLane version.
  The older `librelane-3.0.0rc1.sif` is **not** used for the flow, because its
  OpenROAD `pdngen` internals may differ from the ones the wrapper targets.
  The job also sparse-fetched IHP-Open-PDK at `2bbec755` into `pdk/`,
  including the 526 symlink targets outside `ihp-sg13cmos5l`, for example
  `sg13g2_sram`. It also cloned `tt-support-tools` `ihp-sg13cmos5l`
  (`d66cf179e`) into `tt/`. On compute nodes, apptainer is
  `module load apptainer/1.4.2`.
* `run.sh <tag>`: 8 CPU, 32 GB, 180 min. It copies the input snapshot `snap/`
  (hashes in `snap/SNAPSHOT.sha256`, equal to the repository files at
  submission), re-implements `create_user_config` and `create_merged_config`
  (the printed user config matches `project.py`), creates `runs/wokwi` as
  `harden()` does, and runs `python3 -m librelane --pdk-root pdk --pdk
  ihp-sg13cmos5l --manual-pdk --run-tag wokwi --force-run-dir runs/wokwi
  --hide-progress-bar --jobs 8 src/config_merged.json` inside the SIF.
  Differences from the action: apptainer instead of docker, and `--jobs 8`.

**run1** (job 23715924, before `OPENROAD_THREADS` was added). This is the
action's configuration otherwise. Measured results:

| Step | Result |
|---|---|
| Lint, synthesis, CheckMacroInstances, floorplan, ManualMacroPlacement | Passed. Synthesis: 465,164 um² of standard cells, plus 8 `RM_IHPSG13_1P_64x16_c2` blackbox cells. |
| `OpenROAD.GeneratePDN` with `sram_pdn_cfg.tcl` | **Passed.** All 8 macros are FIRM, at the configured bboxes (FS reported as MX). Each of the 24 supply pins carries 4 stripes. 64 stripe/macro crossings lie inside same-net columns. 32 POWER crossings pass the 6.64 um `VDD!`/`VDDARRAY!` gap. VPWR and VGND each have 26 full-height Metal4 stripes that meet the precheck port rule, and `check_power_grid` passed on both nets. |
| Global placement, CTS, resizing | Instance utilization 58.4% (standard cells 53.7%, 583,689 um²; 107,541 um² of it is timing-repair buffers). Mid-PnR STA at typ: setup WS +6.59 ns, hold WS +0.27 ns. |
| Global routing | Finished *with congestion*: 617 overflow (Metal2 24, Metal3 574, Metal4 19). Usage 58.9% on Metal2, 66.3% on Metal3 and 21.6% on Metal4. 41,558 nets, 3.12 m of wire. `GRT_ALLOW_CONGESTION 1` lets the flow continue. |
| Detailed routing | Iteration 0 had 7,274 violations at 30–50% complete. It was still running after ~20 min in DRT, on **one thread**, because OpenROAD was called with `-threads None` (ORD-0032). This is a LibreLane 3.1.0.dev3 bug, and it affects the action too (section 2). Cancelled, and replaced by run2. |

**run2** (job **23720702**) is the current configuration: `OPENROAD_THREADS 4`,
and nothing else changed. Everything up to global routing reproduced run1
exactly (same 617 overflow). OpenROAD now reports 4 threads, and DRT
iteration 0 took 16 min (about 2.7× faster than run1's pace). **DRT iteration 0
ended with 34,515 violations**, mostly Metal2: 16,466 Metal2 shorts, 5,770
Metal2 spacing, 4,319 Metal3 shorts and 7,449 recheck. Wire length was
0.82 m on Metal2, 1.16 m on Metal3 and only 0.21 m on Metal4. Iteration 1
was running when this document was written (≈35 min into the job).
For comparison, loom's small design started at 94. 34k is high. Whether
it converges within `DRT_OPT_ITERS` 64 and the job's 180-minute limit is
the open question. If it does not converge, apply section 8 ("Heavy GRT
overflow"). Metal4 is under-used (21.6% GRT usage), which points at Metal2/3
pin-access and fan-out density rather than a lack of total capacity.

Check on it with:

```sh
sacct -j 23720702 --format=JobID,JobName,State,Elapsed,ExitCode
W=<local work dir>/tt-work/sram-flow
grep -E "Completing|optimization iteration|Number of violations|LIBRELANE_EXIT" $W/logs/flow-23720702.log | tail
ls $W/run2/runs/wokwi/                       # one directory per step
grep -h SRAMPDN $W/run2/runs/wokwi/*-openroad-generatepdn/*.log | tail -n 40
python3 -c "import json;m=json.load(open('$W/run2/runs/wokwi/final/metrics.json'));[print(k,v) for k,v in sorted(m.items()) if any(s in k for s in ('utilization','overflow','drc_error','lvs','illegal_overlap','__ws','antenna'))]"
```

A local pass is evidence, not the result of record. After it, run the
official precheck locally on `run2/runs/wokwi/final/gds/*.gds` (tt-support-tools
`precheck/precheck.py --gds ... --tech ihp-sg13cmos5l`). That needs KLayout
and gdstk, both in the SIF.

## 8. If the action fails: what to change, in order

| Symptom | First change |
|---|---|
| `CheckMacroInstances` | The instance key must be the flattened path. Re-run `yosys ... flatten; select -list t:RM_*` on the current `src/*.v`. |
| `SRAMPDN BAD ...` / `no stripe runs through` | The macro x is off the `11.04 + 67.44 k` lattice, or the VOFFSET/core origin changed. Run `check_macro_floorplan.py`. |
| `SRAMPDN: pdn::... does not exist` | The action bumped LibreLane/OpenROAD. Pin `librelane-version: 3.1.0.dev3` in `gds.yaml` (the scaffold owns it), or port the wrapper. |
| GPL-0302 (density too low) | Raise `PL_TARGET_DENSITY_PCT` to 65–70. |
| Heavy GRT overflow / DRT not converging | Lower `PL_TARGET_DENSITY_PCT` to 55 to spread cells. Then apply the area-study diet (async reset: about −24k um², ~50%). Then spread the macros 5 stripe pitches apart instead of 4, so the gap grows from 33 to 100 um. For example, top row k = 6, 11, 16, 21 and bottom row k = 2, 7, 13, 18; odd k is off the site grid. Re-run `check_macro_floorplan.py` after the change. |
| Setup violations (slow corner) | Timing sign-off is typ-only by default. Check `nom_slow` first; the SRAM output is ~5.2 ns clock-to-output at slow. |
| Hold violations | Raise `PL_RESIZER_HOLD_SLACK_MARGIN` / `GRT_RESIZER_HOLD_SLACK_MARGIN`. |
| `Checker.IllegalOverlap` is still fatal | The waiver key did not apply. Confirm that the overlaps are only the POWER-stripe × OBS-band crossings. |
| Precheck pin check (power port distance) | A stripe was cut. The wrapper's check 3 should have caught it, so read `SRAMPDN` in the PDN log. |
| Precheck KLayout DRC inside the macro | Compare with loom's result (0 violations). Report upstream under #190 with the rule names. |
| `gl_test` X-propagation | Unwritten SRAM words read as X in the model. See loom `docs/tt_cmos5l_facts.md` §12. This belongs to the test owner. |

## 9. Unverified until the GitHub action runs

* That LibreLane 3.1.0.dev3 accepts the config exactly as written: `MACROS`
  with `nl` in `models/`, and the lib wildcard keys. Mitigation: loom and
  MarcosAsh use the same keys.
* That the wrapper behaves on eight macros as it did on loom's one macro.
  Nothing in it is single-macro specific.
* Placement, routing convergence and congestion at ~52% utilization with this
  floorplan, and routed timing at 20 ns in all three corners.
* LVS with eight abstracted macros, antenna results, and the Magic
  illegal-overlap count.
* The precheck, above all the KLayout SG13CMOS5L DRC over the merged GDS with
  eight 64x16 macros (section 5), and the pin check on the final LEF.
* `gl_test` with the behavioural models (test owner).
* TT's acceptance of an 8x4 SRAM project on the actual shuttle. This is a
  policy question (task 0.4), not a flow question.
