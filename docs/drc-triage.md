# DRC triage: Magic markers, illegal overlaps and the cmos5l precheck

Last updated 2026-09-25. This page answers three questions about the
SRAM-macro design (eight `RM_IHPSG13_1P_64x16_c2`, 8x4 tiles):

1. Where are the ~65,000 to 69,500 Magic DRC markers and the 84 illegal
   overlaps that every full local LibreLane run reports, and do they matter?
2. What would the Tiny Tapeout precheck report on the final GDS?
3. Can `RUN_MAGIC_DRC` be switched off to save runtime in the gds action?

All numbers come from local runs on the MIT Engaging cluster with the gds
action's exact LibreLane (3.1.0.dev3, same image) and PDK revision
(IHP-Open-PDK `2bbec755`). None of it has been run by the GitHub action.
`<work dir>` below is `tt-work/drc-triage/` on the cluster pool. It holds
the scripts (`scripts/`), the outputs (`out/`, `pdnexp/`) and every Slurm
job id (`manifest.json`).

## Verdict

| Question | Answer | Section |
|---|---|---|
| Where are the Magic DRC markers? | **All inside the eight macro footprints.** run2: 69,448 of 69,448 are inside a macro's LEF box. None cross a macro edge, none are in the 10 um halo, and none are in the standard-cell/routing area. The seven sweep full runs show the same (65,613 to 69,453 markers, 0 outside). | 2 |
| Are they intrinsic to the IHP macro? | **Yes.** Every in-chip marker matches a same-rule marker that Magic reports on `RM_IHPSG13_1P_64x16_c2.gds` by itself (7,815 markers): 69,285 match exactly, 67 lie inside a standalone marker, and 96 (in mirrored instances) lie within 1 um of one. None appear only in the chip. 8,631 chip entries are exact duplicates of another entry. After removing them, the chip has 60,817 distinct markers; 8 × 7,599 distinct standalone markers is 60,792. | 3 |
| Are they real violations? | **No evidence of any.** With the PDK's own SRAM read script (`read_sram_gds.tcl`, a list of `gds flatglob` patterns), Magic reports 20 markers on the macro instead of 7,815, and 180 on run2's full GDS instead of 69,448. The remaining 20 per macro are contact enclosures inside `DigiBnd`, where the sign-off rule is relaxed (`Cnt.c.digibnd` 0.05 um) or not applied (`Cnt.c` inside `SRAM`). KLayout finds 0 violations with both the precheck deck and the PDK's larger "maximal" deck, which does implement Magic's `M2.d`, `NW.d` and `LU.d`. That holds on the macro alone and on run2's full GDS. | 3, 5 |
| Where are the 84 illegal overlaps? | 84 boxes = **32 crossings** (8 macros × 4 VPWR stripes). Each is a 2.1 um wide VPWR stripe over the macro's LEF Metal4 OBS band at macro-local y 39.085..45.205 (the `VDD!`/`VDDARRAY!` split). They are split into 2 or 4 boxes each. This is exactly the case `ERROR_ON_ILLEGAL_OVERLAPS false` waives. Same 84 in every sweep run. | 4 |
| Precheck "KLayout SG13CMOS5L DRC" | **PASS on every GDS tested.** 0 items in all 332 rule categories on run2's final GDS, on six sweep GDS files and on both fixed builds (section 6), with KLayout 0.30.9 in deep mode as the precheck runs it. **run2 also passes with KLayout 0.30.4**, the exact binary the precheck job uses. That run takes 2.5 h, almost all of it in one single-threaded phase. Scaled with loom's GitHub timing, it is about 2 h 50 min on the runner, inside the precheck job's 6-hour limit. | 5 |
| Precheck overall | **FAIL, on the Pin check (not on DRC).** 8 LEF errors on run2 (and 8 on each 8x4 sweep GDS, 14 on the 6x4 one). pdngen adds a short VPWR/VGND Metal4 strap pair in each 32.96 um channel between the paired macros. The straps are 79.8 to 83.6 um tall, and Magic.WriteLEF exports them as power ports. The precheck needs every Metal4 power port to reach within 10 um of both the top and bottom edges. **This blocks submission and must be fixed before the next action run.** Setting `FP_MACRO_HORIZONTAL_HALO` to 16.48 (half the gap, so the halos meet) removes the straps. **A full run with that key and `RUN_MAGIC_DRC false` passes the complete precheck** (all 9 checks; route DRC 0, LVS 0, typ timing met), and the committed `check_macro_floorplan.py` accepts the value. 17 also works, but the checker rejects it. | 6 |
| Can `RUN_MAGIC_DRC` be disabled? | **Yes.** It is not a precheck check on cmos5l, and it can never fail the run (`ERROR_ON_MAGIC_DRC false`). It reports only macro-internal Magic artefacts, and it costs 36 to 64 min locally (43 min on GitHub for `tt_um_loom`). Illegal-overlap checking and LVS are unaffected. | 7 |

## 1. Inputs and method

* **run2**: `sram-flow/run2/runs/wokwi` (Slurm 23720702, complete, unpruned),
  final GDS sha256 `058cc6b0…`. Magic DRC ran on the KLayout stream-out (`CURRENT_GDS`
  = `58-klayout-streamout`, which is also `final/gds`), style `drc(full)`.
* **Sweep full runs** (`sweep/results.csv`, mode `full`): `base` t32, t32-pre and t48; `rstreg`
  t32; `cn_s2` t32; `cn` t32 (failed LVS, reports only); and `diet4` 6x4
  `fp6_tworow` t32. Reports were taken from each run's `librelane-logs.tar.gz`, and
  GDS/LEF from `final/` (candidates only). The two `fp6_tworow_trk_top*` full
  runs were still running and are not included.
* **Classification** (`scripts/geom.py`, `magic_triage.py`): every marker box
  is tested against each instance's LEF box from `resolved.json` `MACROS`
  (236.80 × 64.36 um; FS/N handled): *in macro* = fully inside; *macro edge* =
  crosses the box boundary; *halo* = outside but within the 10 um default halo;
  *core* = everything else. In-macro boxes are mapped to macro-local
  coordinates and compared with the standalone macro report. The match is
  *exact* (same box ±0.005 um), *covered* (the box lies inside standalone
  boxes of the same rule; Magic splits the error region differently), or
  *near* (within 1 um of a same-rule standalone box). Anything else is
  *chip-only*.
* **Illegal overlaps** (`scripts/overlap_triage.py`): parsed from
  `64-magic-spiceextraction/feedback.xml`. Each box is matched against the
  final DEF's Metal4 special-net stripes and the macro LEF's Metal4 OBS.
* **Tools**: Magic 8.3.674 and KLayout 0.30.9 from the LibreLane 3.1.0.dev3
  SIF, with the same magicrc and tech as the flow. The exact precheck KLayout
  (0.30.4-1, Ruby 3.3.9) was obtained by building the precheck's own
  `precheck/default.nix` (nixpkgs `cee01fe2`) in a `nixos/nix` container. It
  resolves to the same `nix-shell` store path
  (`br7b4cz1h7gcwfrw9yf2khzy7qimxldi`) that `tt_um_loom`'s GitHub precheck log
  shows.
* **Precheck**: `tt-support-tools` branch `ihp-sg13cmos5l` at `d66cf179e`,
  which is the branch head on 2026-09-25 and includes PR #187. `precheck.py`
  was run unmodified, with the pinned Python requirements (gdstk 0.9.52,
  klayout 0.28.17.post1). It was run on a submission directory laid out like
  the action's `tt_submission/`: `<top>.gds` and `<top>.lef` from `final/`,
  plus `info.yaml`.

## 2. Magic DRC: where the markers are (run2)

69,448 markers in 7 rule categories. The regions are defined in section 1.

| Magic rule (message) | Chip markers | Exact duplicates | Standalone × 8 | In macro | Macro edge | Halo | Core | Exact match | Covered | Near | Chip-only |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `Can't overlap those layers` | 34,435 | 6,897 | 27,536 | 34,435 | 0 | 0 | 0 | 34,433 | 2 | 0 | 0 |
| `Metal2 minimum area < 5760 (M2.d)` | 30,720 | 0 | 30,720 | 30,720 | 0 | 0 | 0 | 30,720 | 0 | 0 | 0 |
| `This layer can't abut or partially overlap between subcells` | 2,824 | 1,734 | 2,816 | 2,824 | 0 | 0 | 0 | 2,808 | 16 | 0 | 0 |
| `Extension of tie diffusion beyond tie contact < 6.0um (LU.d, LU.d1)` | 1,056 | 0 | 1,056 | 1,056 | 0 | 0 | 0 | 1,056 | 0 | 0 | 0 |
| `N-Diffusion spacing to N-well in SRAM < 54 (NW.d exception)` | 233 | 0 | 232 | 233 | 0 | 0 | 0 | 120 | 17 | 96 | 0 |
| `N-diffusion overlap of N-diffusion contact < 14 (Cnt.c)` | 96 | 0 | 96 | 96 | 0 | 0 | 0 | 96 | 0 | 0 | 0 |
| `N-diffusion overlap of contact in SRAM < 4 (Cnt.c SRAM exception)` | 84 | 0 | 64 | 84 | 0 | 0 | 0 | 52 | 32 | 0 | 0 |
| **Total** | **69,448** | **8,631** | **62,520** | **69,448** | **0** | **0** | **0** | **69,285** | **67** | **96** | **0** |

Magic limits are in its internal 0.005 um units. For example, `M2.d` < 5760
means < 0.144 um², and `Cnt.c` < 14 means < 0.07 um.

Per instance the count ranges from 7,816 to 11,267. The
standalone count is 7,815. The excess on `e1_lo`, `e1_hi` and `e3_lo` is
almost entirely exact duplicates of `Can't overlap those layers`. Magic lists the
same error box twice there. The duplicates depend on placement: the 6x4
`fp6_tworow` run has 4,791 instead of 8,631. The 96 *near* `NW.d` boxes all
belong to the four FS (mirrored) instances. Magic draws the same spacing
error as a slightly different box when the cell is mirrored. Against a
standalone macro placed FS (`out/magic-macro-fs`), 88 of the 117 FS `NW.d`
boxes match exactly.

**Control layouts** (control GDS built with KLayout by
`scripts/make_variants.py`, checked with the same Magic script):

| Control GDS | Magic markers | Reading |
|---|---:|---|
| Macro GDS alone (N) | 7,815 | the intrinsic set |
| Macro GDS alone, placed FS in a wrapper cell | 5,784 | the same rule kinds; Magic lists fewer boxes for this wrapper, and 5,734 of them are identical to N boxes after un-mirroring |
| run2 with only the 8 macro instances (no std cells, no routing, no stripes; 2.6 min) | 65,054 | 56,626 of its 56,639 distinct boxes are also in the chip report |
| run2 with the macro cell emptied (instances kept, geometry removed; job 23805018, 46 min) | 20 | everything outside the macros. All 20 are `M2.d` on five Metal2 pin-access stubs of top-row macro pins (4 boxes each: `e2_lo` local x 116.2, 147.3, 158.6; `e2_hi` x 10.7, 25.9; y −0.57..+0.13 um of the macro edge). They are below minimum area only because the control deleted the macro pins they land on. In the real chip they merge with the pins, and the chip report has 0 markers there. |
| run2 unchanged, re-run with the same script (job 23806716) | 69,448 | the report is line-for-line identical to LibreLane's, so the controls are comparable |

### Sweep full runs

| Run | Tiles | Magic markers | Distinct | Outside macros | Chip-only | Magic DRC time | Illegal overlaps (all VPWR × OBS band) |
|---|---|---:|---:|---:|---:|---:|---:|
| run2 (23720702) | 8x4 | 69,448 | 60,817 | 0 | 0 | 44:22 | 84 |
| base t32 (23749129_0) | 8x4 | 69,448 | 60,817 | 0 | 0 | 46:02 | 84 |
| base t32-pre (23749537_0) | 8x4 | 69,448 | 60,817 | 0 | 0 | 54:13 | 84 |
| base t48 (23749132_0) | 8x4 | 69,448 | 60,817 | 0 | 0 | 55:39 | 84 |
| rstreg t32 (23751798_0) | 8x4 | 69,452 | 60,821 | 0 | 0 | 54:59 | 84 |
| cn_s2 t32 (23751802_0) | 8x4 | 69,453 | 60,822 | 0 | 0 | 1:03:59 | 84 |
| cn t32 (23751800_0, LVS failed) | 8x4 | 69,449 | 60,818 | 0 | 0 | 1:00:10 | 84 |
| diet4 fp6_tworow t32 (23763343_0) | 6x4 | 65,613 | 60,822 | 0 | 0 | 36:14 | 84 |

## 3. Why Magic flags the macro, and why it is not a layout problem

* **All 7,815 standalone markers lie under the macro's `DigiBnd` (16/0)
  marker**, and 7,353 of them also lie under `SRAM` (25/0)
  (`scripts/marker_layers.py`).
* **The PDK documents the cause.** `ihp-sg13cmos5l/libs.tech/magic/read_sram_gds.tcl`
  explains that SRAM subcells must be flattened on GDS read "to avoid layer
  ambiguities which in turn cause overlap errors to appear in magic":
  `lvsres_*`, `*_CELL_SUB` (metal2 overlap), `VIA_M1_*`, `VIA_M2_*` and
  `RSC_*` (ContBar vias), and `*_CELL_CORNER` and `*_BITKIT_CORNER` (NWell
  not under diffusion). Magic also checks minimum area per cell, so Metal2
  pieces that only reach 0.144 um² once merged with the parent are flagged
  (`M2.d`).
* **With those `gds flatglob` patterns, the standalone macro drops from 7,815
  to 20 markers** (job 23806700): 12 `Cnt.c` and 8 `Cnt.c SRAM exception`.
  `Can't overlap`, `abut`, `M2.d`, `LU.d` and `NW.d` all go to 0. **On run2's
  full GDS the same setting gives 180 markers instead of 69,448** (job
  23806701). They are 96 `Cnt.c` and 84 `Cnt.c SRAM exception`, all inside
  the macros, and all match the 20-per-macro flattened standalone set (148
  exactly, 32 covered). There are none outside the macros.
* **The remaining 20 are contact enclosures that the sign-off deck treats
  differently.** The 12 `Cnt.c` are inside `DigiBnd`, where the KLayout rule is
  `Cnt.c.digibnd` = 0.05 um instead of Magic's 0.07 um. The 8 are inside `SRAM`,
  where the KLayout deck does not apply `Cnt.c` at all (it has a separate
  `Cnt.c.SRAM` category).
* **KLayout finds nothing on the macro**: 0 items with the precheck deck
  `ihp-sg13cmos5l.drc` (332 categories, KLayout 0.30.9 and 0.30.4). It also finds 0
  with the PDK's `rule_decks/sg13cmos5l_maximal.drc` (271 categories,
  job 23808859), which, unlike the precheck deck, implements `M2.d`, `NW.d`,
  `LU.d` and `LU.d1`. The maximal deck also reports 0 on run2's full GDS
  (job 23809322), so these rules are clean across the whole chip as well.

So the Magic count measures how Magic reads IHP's SRAM hierarchy. It says
nothing about the standard-cell area, where Magic reports 0 markers.

## 4. The 84 illegal overlaps

| Property | run2 |
|---|---|
| Category | `obsm4-metal4.ILLEGAL_OVERLAP` ("Illegal overlap between obsm4 and metal4 (types do not connect)") |
| Region | 84 in macro, 0 elsewhere |
| Net under the box (final DEF Metal4 stripes) | VPWR for all 84 |
| Macro-local x | the four POWER stripe positions 16.19..18.29, 83.63..85.73, 151.07..153.17, 218.51..220.61 (centres 17.24, 84.68, 152.12, 219.56) |
| Macro-local y | inside 39.085..45.205, the LEF Metal4 OBS band at the `VDD!`/`VDDARRAY!` split |
| Per macro | 10 or 12 boxes; 4 stripes each, split by Magic into 2 or 4 boxes |

This is exactly the case described in `src/config.json` and
`docs/hardening.md` section 3. The stripe crosses a LEF obstruction that
abstracts the split between two same-net (VPWR) pin columns. The flattened
macro GDS has no Metal4, Via3 or TopMetal1 within 1 um of the stripes in that
band (`BAND_HITS 0`). The KLayout deck, which checks the real merged GDS,
finds no Metal4 spacing or short there. `Checker.IllegalOverlap` reads a
metric written by `Magic.SpiceExtraction`, so it does not depend on
`RUN_MAGIC_DRC`.

## 5. Reproduced precheck: KLayout SG13CMOS5L DRC

The precheck runs `ihp-sg13cmos5l.drc` with `-rd sg13cmos5l=true -rd input=…
-rd thr=1 -rd report=…`. The deck ignores `thr`. `threads` defaults to the
CPU count, `run_mode` is unset (deep mode), and `precheck_drc` is unset, so
the full main table runs (333 rule executions reporting into 332 categories),
including the recommended rules.

| GDS | KLayout | Mode | Result | Deck time |
|---|---|---|---|---:|
| run2 final (precheck.py, job 23803606) | 0.30.9 (SIF) | deep | **0 items / 332 categories** | 341 s |
| run2 final (deck direct, job 23803609) | 0.30.9 | tiled, 64 threads | 0 / 332 | 627 s |
| run2 final (job 23806755) | **0.30.4-1 (precheck's nix closure)**, threads 4 like the runner | deep | **0 / 332** | **9,004 s**, of which 8,026 s is connectivity setup |
| macro alone (jobs 23803715, 23806755) | 0.30.9 and 0.30.4 | deep | 0 / 332 | 7 s / 9 s |
| six sweep GDS: base t32, t32-pre, t48; rstreg t32; cn_s2 t32; diet4 6x4 (precheck.py, jobs 23806044–23806050) | 0.30.9 | deep | 0 / 332 each | 246–573 s |
| fixed builds `pdnexp/halo16p48-full-p` and `pdnexp/halo17-full` (precheck.py, jobs 23856538, 23834454) | 0.30.9 | deep | 0 / 332 each | 321 s, 352 s |
| run2 final, PDK `sg13cmos5l_maximal.drc` (not a precheck deck; job 23809322) | 0.30.9 | deep | 0 / 271 (includes `M2.d`, `NW.d`, `LU.d`, `LU.d1`) | 1,233 s |

**Full precheck result on run2's final GDS/LEF** (identical on every sweep GDS
except for the number of pin errors):

| Check | Result |
|---|---|
| KLayout pin label overlapping drawing | pass (0 items) |
| KLayout SG13CMOS5L DRC | **pass (0 items)** |
| KLayout zero area | pass (0 items) |
| KLayout Checks (top cell name, forbidden TopMetal1, prBoundary present) | pass |
| **Pin check** | **FAIL: "Some ports are missing or have wrong dimensions, see 8 LEF errors above"** |
| Boundary check | pass |
| Layer check (`SRAM.drawing`, `DigiBnd.drawing` allowed by PR #187) | pass |
| Cell name check | pass |
| Analog pin check | pass |

**PR #187 and issue #190.** The layer check passes only because PR #187
(`cfa06bae`, in `d66cf179e`) allows `SRAM.drawing` 25/0 and `DigiBnd.drawing`
16/0. Without those layers the macro would fail the layer check, or it would
be checked under non-SRAM rules. Issue #190 reports `Sdiod.d`, `Sdiod.e` and
`Cnt.c.digibnd` errors inside `RM_IHPSG13_1P_512x8_c3_bm_bist`, from the
**sg13g2** deck on `main`. The cmos5l precheck deck has no `Sdiod` rules; they
exist only in `sg13cmos5l_maximal.drc`, which the precheck does not run. Its
`Cnt.c.digibnd` and `Cnt.c.SRAM` categories report 0 on our macro and on the
full chip. #190 therefore does not reproduce for this macro on the cmos5l
precheck.

**Precheck runtime.** The GitHub precheck job has its own 6-hour limit.
Under the precheck's KLayout 0.30.4, almost all of the deck's time goes into
one single-threaded phase, "DRC connectivity setup". That phase is the
`connect` statements plus `nwell_drw.nets`, a netlist extraction over the
whole chip.

| GDS | Where | KLayout | Connectivity setup | Whole deck |
|---|---|---|---:|---:|
| `tt_um_loom` (6x4, one 512x16 macro) | GitHub runner, run 35940928210 | 0.30.4 | 6,351 s | 7,092 s |
| `tt_um_loom`, same GDS (job 23836020) | cluster node | 0.30.4 | 5,707 s | 6,195 s (0 items) |
| `tt_um_loom` (job 23836021) | cluster node | 0.30.9 | 25 s | 254 s |
| run2 (job 23806755) | cluster node | 0.30.4 | 8,026 s | 9,004 s |
| run2 (job 23803606) | cluster node | 0.30.9 | 31 s | 341 s |

The same loom GDS runs about 1.14 times faster on the cluster node than on
the GitHub runner (6,195 s against 7,092 s). If run2's node (a different one)
is similar, **run2's deck would take about 9,004 × 1.14 ≈ 10,300 s (2 h 50 min)
on the runner**. The precheck job's other checks and setup add a few
minutes. That is inside the 6-hour limit with about a factor of 2 to spare,
and the precheck job will take about 1.5 times as long as loom's (just under 2 h).

KLayout's release notes list a fix for "poor netlister performance in some
cases" (issue #2277, 0.30.5/0.30.7). It is the likely reason 0.30.9 is ~250
times faster here, but that has not been verified. Nothing in the design
needs to change. If the precheck ever runs short of time, the lever is
upstream: the nixpkgs pin in `tt-support-tools/precheck/default.nix`.

## 6. The blocking finding: Pin check (short power straps)

`pin_check.py` requires every Metal4 `VPWR`/`VGND` LEF port to be at least
2.1 um wide and to reach within 10 um of **both** the bottom and the top edge
of the block. run2's final LEF has 30 Metal4 port rectangles per net: 26
full-height lattice stripes and **4 short straps**:

| Net | x (um) | y (um) | Channel |
|---|---|---|---|
| VPWR / VGND | 395.43..397.53 / 401.19..403.29 | 3.56..83.38 | between `e0_lo` (ends 382.72) and `e0_hi` (starts 415.68) |
| VPWR / VGND | 1204.71..1206.81 / 1210.47..1212.57 | 3.56..83.38 | between `e1_lo` (1192.00) and `e1_hi` (1224.96) |
| VPWR / VGND | 665.19..667.29 / 670.95..673.05 | 623.48..707.08 | between `e2_lo` (652.48) and `e2_hi` (685.44) |
| VPWR / VGND | 1339.59..1341.69 / 1345.35..1347.45 | 623.48..707.08 | between `e3_lo` (1326.88) and `e3_hi` (1359.84) |

Each bottom strap is 627.26 um from the top edge, and each top strap is
623.48 um from the bottom edge: 2 errors per net per row, 8 in all.

* **Origin.** The straps are already in the DEF right after
  `OpenROAD.GeneratePDN` (reproduced from scratch in `pdnexp/base`, job
  23808070). The 32.96 um gap between paired macros leaves
  standard-cell rows outside the 10 um halos: in the DEF, 20 row segments
  x 393.12..405.60, y 3.78..79.38, in the `e0` gap, and the same in the
  others. No lattice stripe crosses those rows (the nearest ones run over the macros), so
  pdngen adds a VPWR/VGND strap pair to connect them. The strap spans only the
  channel's height. The wrapper's `pdn::write_to_db` call adds pins, so every
  Metal4 stripe, short or tall, becomes a shape of the `VPWR`/`VGND` pin in the
  DEF (30 per net in run2). Magic.WriteLEF then writes all of them as LEF
  ports. The wrapper in `src/sram_pdn_cfg.tcl` (check 3) mirrors the
  precheck rule only for stripes at least 100 um tall, so it lets these
  through ("26 tall Metal4 stripe(s)" per net).
* **Scope.** Every full run so far has it: all 8x4 `fp8_base` GDS (8 errors)
  and the 6x4 `fp6_tworow` GDS (14 errors, including a channel at the right
  core edge). The GitHub action would fail at the precheck in the same way.
* **Recommended fix: `"FP_MACRO_HORIZONTAL_HALO": 16.48`**, half the 32.96 um
  pair gap, so the two halos meet and OpenROAD.CutRows leaves no rows between
  the paired macros. All runs below use a private copy of the run2 snapshot
  (its RTL and `src/`), with only the listed keys changed.
  `OPENROAD_THREADS 32` only shortens the local run; the action keeps 4.
  * To GeneratePDN from scratch (`pdnexp/halo16p48`, job 23850207): PDN step
    passes, with 26 Metal4 stripes per net and 0 short.
  * Full run with `RUN_MAGIC_DRC false` and `OPENROAD_THREADS 32`
    (`pdnexp/halo16p48-full-p`, job 23850490): LibreLane exit 0 in 39 min.
    It had 0 short stripes and 0 row segments in any of the four gaps. Route
    DRC was 0, antenna 0, critical disconnected pins 0 and LVS 0, with 84
    illegal overlaps (the waived case) and utilisation 58.5%. Typ setup WS was
    +0.89 ns with 0 violations, hold WS +0.11 ns, and slow setup WS −8.52 ns.
  * **The unmodified precheck passes all 9 checks on it** (job 23856538;
    KLayout SG13CMOS5L DRC 0 items, pin check clean).
  * The committed `macros/check_macro_floorplan.py` passes with 16.48. It
    requires the macros to be at least two halos apart, and 32.96 is exactly
    2 × 16.48.
* **Also tested: `"FP_MACRO_HORIZONTAL_HALO": 17`.** Its PDN-only run
  (`pdnexp/halo17`, job 23808071) had 26 stripes per net and 0 short. Its full
  run (`pdnexp/halo17-full`, job 23808951; repeated on a non-preemptable node
  as 23816463 with identical metrics) finished in 45 to 56 min with route DRC
  0, antenna 0, disconnected pins 0, LVS 0, 84 illegal overlaps, utilisation
  58.6%, typ setup WS +1.11 ns (0 violations), clean hold and slow setup
  WS −8.45 ns. The precheck passes all 9 checks on it (job 23834454). However,
  17 fails the committed floorplan checker ("closer than two halos").
* **Cost and caveats.** Either value gives up the four 12.48 um wide row strips
  (roughly 4,000 um² of placement area) and adds 6.5 to 7 um of halo on each
  macro's outer sides. Slow-corner setup differs from run2 (−5.09 ns), but
  run2 used 4 OpenROAD threads and these runs used 32, so the difference
  cannot be attributed to the halo alone. Slow setup is not a sign-off corner
  (`docs/hardening.md` section 7).
* **Other options** (not tested): move the pairs 5 lattice pitches apart,
  so a full-height stripe pair runs through the 100 um gap (the fallback
  already listed in `docs/hardening.md` section 8); or have the wrapper drop
  pins for, or fail on, *any* VPWR/VGND Metal4 shape that does not span the
  die. Whichever is chosen, extend the wrapper's check 3 to all stripes so the
  failure shows up at GeneratePDN, in minutes, and not at the precheck.
  `<work dir>/scripts/short_stripes.py <def>` performs that check on a DEF.

This is outside this document's scope for editing: `src/config.json` and
`src/sram_pdn_cfg.tcl` belong to the hardening owner.

## 7. Recommendation: `RUN_MAGIC_DRC`

**Set `"RUN_MAGIC_DRC": false` in the SRAM block of `src/config.json`** (above
the template's "do not change" line), together with the section 6 fix. Both
were tested together in `pdnexp/halo16p48-full-p` and `pdnexp/halo17-full`,
and both builds pass the full precheck.
The reasons:

1. **It cannot fail the run and is not a submission check.** `ERROR_ON_MAGIC_DRC`
   is already false. `precheck.py` runs Magic DRC only for `sky130A` and
   `gf180mcuD`, not for `ihp-sg13cmos5l`. Nothing in `tt-support-tools`
   (`--print-stats`, `--create-tt-submission`) reads the Magic DRC metric or
   report.
2. **Its output is noise for this design.** Every marker is inside the IHP
   macro (sections 2 and 3), and Magic reports 0 in the rest of the chip. The
   macro-emptied control shows only 5 pin-access stubs that lose their macro
   pin in the control. The signal that matters is already there:
   route DRC 0, LVS 0, and above all the precheck's KLayout deck, 0 on the
   merged GDS.
3. **It is 36 to 64 min of a job that has a 6-hour limit.** Locally it took
   44:22 (run2) and 36:14 to 1:03:59 (sweep). It is single-threaded (peak RSS 2 GiB),
   so the runner's 4 vCPUs do not help. The time goes into the standard-cell
   area, not the macros: Magic takes 46 min on run2 with the macros emptied and
   2.6 min on the 8 macros alone. So `MAGIC_GDS_FLATGLOB` (below) would not make
   it cheaper. On GitHub, `tt_um_loom`'s Magic.DRC
   took 43 min 11 s of a 5 h 02 min gds job (run 35940928210, 57,924 markers).
4. **What stays.** `RUN_MAGIC_DRC` gates only `Magic.DRC` and
   `Checker.MagicDRC` (`librelane/flows/classic.py`, 3.1.0.dev3).
   `Magic.SpiceExtraction`, `Checker.IllegalOverlap` (the 84 boxes above),
   `Netgen.LVS` and `Checker.LVS` still run.
5. **Precedent.** `WilliamZhang20/protocol-emulator` (run 35904473066) and
   `MarcosAsh/protocol-emulator` (run 35918774027) set `RUN_MAGIC_DRC 0` and
   have green gds and precheck jobs on cmos5l with IHP SRAM macros.

If a Magic DRC is wanted anyway (for example as a local sign-off extra), set
`MAGIC_GDS_FLATGLOB` to the PDK's patterns: `lvsres_*`, `*_CELL_SUB`,
`VIA_M1_*`, `VIA_M2_*`, `RSC_*`, `*_CELL_CORNER`, `*_BITKIT_CORNER`.
`Magic.DRC` is the only step that reads that variable. On run2 that turns
69,448 markers into 180, all of them the macro's DigiBnd/SRAM contact cases
(section 3). It does not save time: that run took 74 min on a cluster node.

**The evidence the precheck already provides.** On the merged GDS the
precheck runs the PDK's full main deck (FEOL, BEOL, offgrid, angle, pin,
forbidden; 333 rules) over both macro and standard cells, plus zero-area,
layer, boundary, cell-name and power-port checks. It is the result of record.
This page shows that it passes DRC locally on every GDS tested, and that it
fails only on the power-port issue in section 6.

## 8. Next steps (for the owners of the files named)

1. `src/config.json` (hardening owner): add `"FP_MACRO_HORIZONTAL_HALO": 16.48`
   and `"RUN_MAGIC_DRC": false` to the SRAM block, then re-run
   `macros/check_macro_floorplan.py` (it passes at HEAD with 16.48). Both were
   tested together on the run2 snapshot, and the precheck passes (section 6).
   If the pair spacing ever changes, the halo must stay at least half the gap.
2. `src/sram_pdn_cfg.tcl` (hardening owner): apply check 3 to every
   VPWR/VGND Metal4 shape, not just the tall ones, so a short strap fails
   `OpenROAD.GeneratePDN` in minutes.
3. `docs/hardening.md` section 8: the "Precheck pin check" row says the
   wrapper's check 3 should catch a cut stripe. It does not catch
   channel-repair straps under 100 um.
4. Floorplan variants (`floorplans/`, sweep owner): every macro-to-macro or
   macro-to-core-edge gap that leaves std-cell rows outside the halos gets
   channel straps. The 6x4 `fp6_tworow` has one at the right core edge. Run
   `scripts/short_stripes.py` on the GeneratePDN DEF of each candidate.

## 9. Reproduce

```sh
W=<work dir>                       # tt-work/drc-triage
# Magic marker triage (login node is fine; < 1 s per report)
python3 $W/scripts/magic_triage.py run2 <run>/62-magic-drc/reports/drc.magic.rpt \
    <run>/resolved.json $W/out/magic-macro/drc.magic.rpt $W/out/magic-run2.json
python3 $W/scripts/overlap_triage.py <run>/64-magic-spiceextraction/feedback.xml \
    <run>/resolved.json <run>/final/def/<top>.def macros/RM_IHPSG13_1P_64x16_c2/RM_IHPSG13_1P_64x16_c2.lef
# Precheck (Slurm; submission dir = <top>.gds + <top>.lef + info.yaml)
sbatch -J pe-x-drc-triage-precheck-<name> $W/scripts/precheck.sbatch <name> <submission dir>
# Standalone Magic DRC (optional 4th arg: the PDK's read_sram_gds.tcl)
sbatch -J pe-x-drc-triage-magic-<name> $W/scripts/magic_standalone.sbatch <name> <gds> <topcell> [flatglob.tcl]
# Short power straps after PDN
python3 $W/scripts/short_stripes.py <run>/<NN>-openroad-generatepdn/<top>.def
```
