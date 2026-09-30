# Working notes

These pages record engineering work done while the design was built:
studies, local physical-design harnesses and the steps to the committed
sign-off configuration. Each describes the state at the date given and is
kept as a record, except that [variants.md](variants.md) (the definitions of
the design-variant knobs) and [6x4.md](6x4.md) (the 6x4 fallback build and its
switch procedure) are still maintained. The current status and its evidence are in
[../results.md](../results.md), [../signoff-history.md](../signoff-history.md)
and [../limitations.md](../limitations.md). The documentation check
(`tools/evidence/check_consistency.py`) checks these pages as well.

| Note | What it is | Current as of |
|---|---|---|
| [area-study.md](area-study.md), [area-study/](area-study/) | Area study, revision 2: whether the private-instruction-SRAM configuration fits 8x4, and which area reductions would fit 6x4; its synthesis scripts and configurations | 2026-09-25, before the first official build |
| [hardening.md](hardening.md) | Recipe for the first 8x4 build with eight SRAM macros through the official `gds` action: macro power stripes, floorplan, local mirror of the action | `c118027` (2026-09-25); `src/config.json` has changed since (p010 in `25e331e`, 15 ns and p018 in `d76f1cc`) |
| [drc-triage.md](drc-triage.md) | Magic DRC markers (all inside the SRAM macros), the 84 illegal overlaps, and the precheck Pin-check failure with its halo fix | 2026-09-25 (`1e2cfb3`) |
| [sweep.md](sweep.md) | Local LibreLane sweep harness (`scripts/sweep/`) on the MIT Engaging cluster, its floorplans and the 2026-09-25 launches | 2026-09-25 |
| [variants.md](variants.md) | RTL variant knobs of the generator (`Variant_options`) and the six named variants (`base`, `rstreg`, `cn`, `cn_s2`, `diet4`, `diet2`), with their verification | 2026-09-25; notes added up to 2026-09-30 |
| [timing-closure.md](timing-closure.md) | The slow-corner failure of `c118027`, behaviour-preserving RTL restructurings (`Timing_options`, off by default) that did not close it, closure by LibreLane configuration (p010, official build of `131e793`) and the 15 ns sign-off (`d76f1cc`) | 2026-09-27; one table row updated 2026-09-30 |
| [optimization.md](optimization.md) | Physical-design optimizer (`tools/opt/`): search over LibreLane knobs, promotion gates, and the campaigns that produced promotions p010 and p018 | 2026-09-27 (`75ac8af`) |
| [6x4.md](6x4.md) | 6x4 fallback build of the `diet4` variant: floorplan, local sign-off, the `gds_6x4` workflow and its runs, and how to switch the submission | 2026-09-30; a fallback since the organizers confirmed 8x4 on 2026-09-28 |
