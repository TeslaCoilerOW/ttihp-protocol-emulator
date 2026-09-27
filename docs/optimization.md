# Physical-design optimizer

`tools/opt/` is an autonomous search over LibreLane configuration knobs. It
runs several **tracks**, each with its own optuna study:

- the 8x4 design of record at `CLOCK_PERIOD` 20 ns (50 MHz, the operating
  clock; the committed period until `d76f1cc`);
- the same design at 15 ns (66.7 MHz; the committed period since
  `d76f1cc`) and at 13.33 ns (75.0 MHz), the **frequency tracks**;
- the 6x4 fallback: the `diet4` core with the committed `variants6x4/`
  overlay on the 6x4 die (`docs/6x4.md`);
- one track per variant core that the variant workflow publishes
  (`docs/variants.md`).

It runs on the Engaging cluster through the sweep harness of `docs/sweep.md`,
which it uses unchanged. It records every run in an append-only store, and it
promotes the best configurations of each track to a full-flow run, the Tiny
Tapeout precheck, the gate-level cocotb suite and a formal RTL-vs-netlist
equivalence check with [`formal_eq/`](../formal_eq/README.md)
([Promotion](#promotion)).

Like every local run, a result here is evidence. It is not the result of
record (`docs/hardening.md` sections 7 and 9). A configuration becomes the
design of record only after it is committed to `src/config.json` and the
GitHub gds, precheck and gl_test actions pass on that commit (see
[Adopting a configuration](#adopting-a-configuration)).

The first version of the optimizer (v1, 2026-09-26 03:36 to 13:40) searched
the design of record at 20 ns and the variant cores. Its record is in
[Campaign record](#campaign-record). v2 added the frequency tracks, the 6x4
track, absolute knob values, the import of identical earlier trials,
weighted allocation and retirement; it runs today. v3 (the tools of this
page's runtime sections, not yet running) makes the length of the official
`gds` and `gds_6x4` jobs part of the search: GitHub stops such a job after
6 hours, and p024, the 13.33 ns promotion, is projected to exceed that
([Runtime and the 6-hour limit](#runtime-and-the-6-hour-limit),
[The 75 MHz plan](#the-75-mhz-plan)).

## Current results

Three kinds of result appear on this page, with decreasing weight:

1. **Result of record:** a configuration committed to `src/config.json` (or
   to `variants6x4/config.overlay.json`) and built by the GitHub actions.
2. **Promoted sign-off run (local):** a full-mode run with
   `OPENROAD_THREADS` 4 and LVS, the unmodified Tiny Tapeout precheck, the
   gate-level cocotb suite and, in the tools after driver job 24077569, the
   RTL-vs-netlist equivalence check (`formal_eq`, see [Equivalence
   check](#equivalence-check)). The verdict is PASS only if all of them
   pass ([Promotion](#promotion)). The verdicts in the tables below were
   given before the equivalence stage existed; the next driver runs that
   stage on every earlier promotion with a legal full run.
3. **Fast-mode trial (local):** 32 threads, no LVS, no precheck, no
   gate-level test. It ranks configurations; it is not a sign-off.

**Of record.** Since `d76f1cc`, `src/config.json` carries the 15 ns
promotion p018 (see [Adoption of p018](#adoption-of-p018-d76f1cc)): the flow
signs the design off at 66.7 MHz, and the operating clock is 50 MHz
(`info.yaml` `clock_hz` 50000000). p018 passed the local sign-off (full run
24042265 legal with LVS 0; setup WS typ/fast/slow +5.950/+6.924/+2.240 ns at
15 ns; precheck 24053971 9/9; gate level 24053972 0 fail; `docs/results.md`
R84). Its official `gds` run, 36298635436, passed gds, precheck and
gl_test with a `metrics.csv` and a gate-level netlist byte-identical to the
full run's (2026-09-27 13:33 UTC; R85), so it is the result of record in
the sense of [Current results](#current-results), item 1. Re-timed at 20 ns with the
flow's own STA script, the p018 layout has setup WS +8.950/+9.924/+6.790 ns
(R86; `docs/timing-closure.md` section 10).

From `25e331e` until `d76f1cc` the design of record was p010 at 20 ns:
GitHub run 36257636798 on `131e793` passed gds, precheck and gl_test with
setup WS typ/fast/slow +7.88/+9.31/+2.95 ns, and its `metrics.csv` is
byte-identical to that of p010's full run, job 24010051 (`docs/results.md`,
R16 to R19; still true for that commit). The 6x4 overlay carries p014 since
`4bd30c8`, at 20 ns; its official `gds_6x4` run 36274474540 passed, with
`metrics.csv` byte-identical to that of p014's full run, job 24036160
(`docs/results.md` R83; `docs/6x4.md` section 5b), and the run of `1e5b1d8`
(36285537630) reproduced it byte for byte (R87).

**Per track** (leaderboard of 2026-09-27 06:20 UTC, 02:20 cluster time,
on tree `f511c97`; WS at the track's period):

| Track | Trials (new + imported; legal) | Best fast-mode trial: min WS (corner), job | Best PASS promotion: typ / fast / slow WS | Promotions in flight |
|---|---|---|---|---|
| `dor`, 8x4, 20 ns | 84 + 134; 179 | +5.533 ns (slow), trial #175, job 24054351 | p022: +8.933 / +9.878 / +5.533 ns | p027 (control, full run 24077610) |
| `dor15`, 8x4, 15 ns (66.7 MHz), committed since `d76f1cc` | 56 + 0; 27 | +2.556 ns (slow), trial #13, job 24037613 | p021: +5.806 / +6.737 / +2.556 ns (committed: p018, +5.950 / +6.924 / +2.240 ns) | none |
| `dor13`, 8x4, 13.33 ns (75.0 MHz) | 24 + 0; 13 | +0.756 ns (slow), trial #1, job 24026489 | none | p024 (full run 24068227) |
| `diet4_6x4`, 6x4, 20 ns | 47 + 0; 34 | +5.784 ns (slow), trial #43, job 24075174 | p025: +8.935 / +9.906 / +5.380 ns | none (p026 FAIL at gate level) |
| 7 variant tracks, 8x4, 20 ns | 6 + 11 or 12 each | +4.722 (`cn_s2_timing`) to +5.863 ns (`diet2`) | none (variants are not promoted before 20 finished trials) | none |

Superseded snapshot (leaderboard of 2026-09-27 01:20 UTC, 2026-09-26 21:20
cluster time, on tree `24b807b`):

| Track | Trials (new + imported; legal) | Best fast-mode trial: min WS (corner), job | Best PASS promotion: typ / fast / slow WS | Promotions in flight |
|---|---|---|---|---|
| `dor`, 8x4, 20 ns | 50 + 134; 146 | +5.533 ns (slow), trial #175, job 24054351 | p016: +7.986 / +9.271 / +4.835 ns | p022 (full run 24057278) |
| `dor15`, 8x4, 15 ns (66.7 MHz) | 30 + 0; 20 | +2.556 ns (slow), trial #13, job 24037613 | p018: +5.950 / +6.924 / +2.240 ns | p019 (24045480), p021 (24048020) |
| `dor13`, 8x4, 13.33 ns (75.0 MHz) | 11 + 0; 4 | +0.756 ns (slow), trial #1, job 24026489 | none | none |
| `diet4_6x4`, 6x4, 20 ns | 28 + 0; 17 | +4.757 ns (slow), trial #25, job 24057279 | p020: +8.492 / +10.371 / +3.621 ns | p023 (full run 24060216) |
| 7 variant tracks, 8x4, 20 ns | 4 + 11 or 12 each | +2.494 (`cn_s2_timing`) to +5.122 ns (`diet4` at 8x4) | none (variants are not promoted before 20 finished trials) | none |

**15 ns is committed and officially built.** Promotion p018 (15 ns,
66.7 MHz) passed the full run (legal, LVS 0), the precheck (9/9) and the
gate-level tests (0 fail): setup WS +5.95 / +6.92 / +2.24 ns and hold WS min
+0.165 ns at 15 ns (`docs/results.md` R84). `d76f1cc` committed it with
`CLOCK_PERIOD` 15 and `clock_hz` unchanged at 50 MHz, the operating clock
([Adoption of p018](#adoption-of-p018-d76f1cc)). The GitHub actions passed
on that commit (R85; the `gds` job took 4 h 24 min of GitHub's 6 h limit).
The 13.33 ns track's first promotion, p024, passed the full run, the
precheck and the gate-level tests by 2026-09-27 10:31 UTC and the
equivalence stage (job 24098118) by 13:37 UTC: a local sign-off at
75.0 MHz with setup WS +4.962 / +6.095 / +0.756 ns. It is not committed
([Promotion](#promotion)).

**75 MHz: the official job would be too long.** p024's full run took
26,549 s of flow time (7.37 h), 82.1% of it in post-CTS timing repair, on a
node whose synthesis step took 2.15 times as long as on the fastest local
nodes. The v3 runtime model projects its official `gds` job at 7.37 h from
the full run and 9.92 h from its trial, against GitHub's 6 h limit
([Runtime and the 6-hour limit](#runtime-and-the-6-hour-limit)). The plan
for an official 13.33 ns sign-off is in [The 75 MHz plan](#the-75-mhz-plan).

Until `d76f1cc` this paragraph read: "Above 50 MHz there is a local
sign-off, not an official one. [...] It is not committed, so no GitHub
action has built it; `src/config.json` stays at 20 ns and `info.yaml` at
50 MHz. [...] A frequency-track configuration becomes the result of record
only after it is committed with `CLOCK_PERIOD` and `clock_hz` changed and
the GitHub actions pass on that commit."

**Promotions of v2** (v1's p001 to p010 are in [Campaign record](#campaign-record)):

| Promotion | Track, trial | Full run | Precheck | Gate level | Setup WS typ / fast / slow | Verdict |
|---|---|---|---|---|---|---|
| p011 | `dor` #134 | 24026087: legal, LVS 0 | 24033557: 9/9 | 24033558: 102 tests, 46 pass, 56 skip, 0 fail | +8.138 / +9.487 / +3.548 ns | PASS |
| p012 | `diet4_6x4` #0 (control: the committed 6x4 build of `131e793`) | 24029191: route DRC 69, flow stopped in `Netgen.LVS` | not run | not run | (+5.654 / +9.294 / −0.627 ns, before LVS) | FAIL |
| p013 | `dor` #143 | 24030864: legal, LVS 0 | 24036014: 9/9 | 24036159: 102 tests, 46 pass, 56 skip, 0 fail | +8.120 / +9.545 / +3.634 ns | PASS |
| p014 | `diet4_6x4` #8 | 24036160: legal, LVS 0 | 24041156: 9/9 | 24041157: 102 tests, 46 pass, 56 skip, 0 fail (`PE_VARIANT` diet4) | +7.706 / +10.518 / +2.737 ns | PASS; in the 6x4 overlay since `4bd30c8` |
| p015 | `dor` #150 | 24037612: legal, LVS 0 | 24044821: 9/9 | 24044822: 102 tests, 46 pass, 56 skip, 0 fail | +7.641 / +9.035 / +4.275 ns | PASS |
| p016 | `dor` #154 | 24038331: legal, LVS 0 | 24043244: 9/9 | 24043245: 102 tests, 46 pass, 56 skip, 0 fail | +7.986 / +9.271 / +4.835 ns | PASS |
| p017 | `diet4_6x4` #12 | 24041769: legal, LVS 0 | 24047685: 9/9 | 24047686: 102 tests, 46 pass, 56 skip, 0 fail (`PE_VARIANT` diet4) | +8.156 / +10.232 / +2.989 ns | PASS |
| p018 | `dor15` #1 (15 ns) | 24042265: legal, LVS 0 | 24053971: 9/9 | 24053972: 102 tests, 46 pass, 56 skip, 0 fail | +5.950 / +6.924 / +2.240 ns at 15 ns | PASS; in `src/config.json` since `d76f1cc` |
| p019 | `dor15` #11 (15 ns) | 24045480: legal, LVS 0 | 24061403: 9/9 | 24061404: 102 tests, 46 pass, 56 skip, 0 fail | +6.076 / +6.916 / +2.364 ns at 15 ns | PASS |
| p020 | `diet4_6x4` #16 | 24047687: legal, LVS 0 | 24055302: 9/9 | 24055303: 102 tests, 46 pass, 56 skip, 0 fail (`PE_VARIANT` diet4) | +8.492 / +10.371 / +3.621 ns | PASS |
| p021 | `dor15` #13 (15 ns) | 24048020: legal, LVS 0 | 24071210: 9/9 | 24071211: 102 tests, 46 pass, 56 skip, 0 fail | +5.806 / +6.737 / +2.556 ns at 15 ns | PASS |
| p022 | `dor` #175 | 24057278: legal, LVS 0 | 24062465: 9/9 | 24062466: 102 tests, 46 pass, 56 skip, 0 fail | +8.933 / +9.878 / +5.533 ns | PASS |
| p023 | `diet4_6x4` #25 | 24060216: legal, LVS 0 | 24063192: 9/9 | 24063193: 102 tests, 46 pass, 56 skip, 0 fail (`PE_VARIANT` diet4) | +8.926 / +10.208 / +4.757 ns | PASS |
| p024 | `dor13` #1 (13.33 ns) | 24068227: legal, LVS 0 | 24092337: 9/9 | 24092338: 102 tests, 46 pass, 56 skip, 0 fail | +4.962 / +6.095 / +0.756 ns at 13.33 ns | PASS, including equivalence (job 24098118; leaderboard of 2026-09-27 13:37 UTC) |
| p025 | `diet4_6x4` #35 | 24069405: legal, LVS 0 | 24073830: 9/9 | 24073831: 102 tests, 46 pass, 56 skip, 0 fail (`PE_VARIANT` diet4) | +8.935 / +9.906 / +5.380 ns | PASS |
| p026 | `diet4_6x4` #43 | 24076184: legal, LVS 0 | 24078116: 9/9 | 24078117: 102 tests, 38 pass, 56 skip, **8 fail** (`PE_VARIANT` diet4) | +9.406 / +10.221 / +5.784 ns | FAIL |
| p027 | `dor` #114 (control: the committed configuration of the `dor` track since tree `f511c97`, that is p018's knob set at 20 ns) | 24077610: legal, LVS 0 | 24081008: 9/9 | 24081009: 102 tests, 46 pass, 56 skip, 0 fail | +7.864 / +9.389 / +2.998 ns | PASS (leaderboard of 2026-09-27 08:36 UTC) |

The verdicts are as of 2026-09-27 06:20 UTC (leaderboard of 02:20 cluster
time); the live list is the leaderboard.

- **Adopted:** p018, in `src/config.json` since `d76f1cc`
  ([Adoption of p018](#adoption-of-p018-d76f1cc)); p014, in the 6x4 overlay
  since `4bd30c8`.
- **15 ns:** p019 and p021 passed with more slow-corner slack than p018
  (+2.364 and +2.556 ns against +2.240 ns) and were not adopted (same
  section).
- **20 ns:** p022 has the largest slow-corner slack of any signed-off 20 ns
  configuration (+5.533 ns; p016 +4.835 ns, p010 +2.954 ns). No 20 ns
  configuration is committed since `d76f1cc`.
- **6x4:** p025 has the largest slow-corner slack of any signed-off 6x4
  configuration (+5.380 ns; p023 +4.757 ns, p020 +3.621 ns, p014 of record
  +2.737 ns). None has been adopted; the overlay carries p014. p025 sets
  `CTS_MAX_SLEW` 0.75 and has the same SRAM and flip-flop clock depths as
  p026; it is outside the current search space
  ([`CTS_MAX_SLEW` is fixed unset](#cts_max_slew-is-fixed-unset)).
- **p026 failed at gate level.** Its full run is legal with LVS 0 and its
  precheck passed, but 8 of the 46 gate-level tests that run failed
  (`test_uart_tx`, `test_flagship_concurrent`, `test_random_lockstep`,
  `test_gap_xfer_neighbour_pins` and four `test_kill_*` tests), with
  lockstep mismatches against the reference model (`gl/result.json` and
  `gl.log` of the promotion; netlist sha256 `e0471ec9…`). The failure is a
  race of the zero-delay simulation at an SRAM input, not a functional
  difference of the netlist: the netlist is equivalent to its RTL
  (`formal_eq` job 24093886, and job 24096968 in a test of the
  equivalence stage), and STA meets hold at every corner ([The p026 case](#the-p026-case)). The Tiny
  Tapeout gl_test action runs the same zero-delay simulation, so p026
  stays FAIL, and a configuration with verdict FAIL is not eligible for
  adoption ([Adopting a configuration](#adopting-a-configuration)).
  `CTS_MAX_SLEW`, the knob implicated, is no longer sampled
  ([`CTS_MAX_SLEW` is fixed unset](#cts_max_slew-is-fixed-unset)).
- **13.33 ns:** p024 passed the full run, the precheck and the gate-level
  tests by 2026-09-27 10:31 UTC and the equivalence stage (job 24098118,
  ABC 374 s) by 13:37 UTC (table above). Until then this item read: "p024 (13.33 ns), in its
  full run (still running at 2026-09-27 08:40 UTC)."
- **`dor` control:** p027 was in its full run at 06:20 UTC and passed by
  08:36 UTC: p018's knob set run at 20 ns gives +2.998 ns at the slow
  corner, against +6.790 ns for the p018 layout re-timed at 20 ns
  (`docs/results.md` R88).

Earlier note (verdicts as of 2026-09-27 01:20 UTC): p016 had the largest
slow-corner slack of any signed-off 20 ns configuration (+4.835 ns against
p010's +2.954 ns, fmax estimate at the slow corner 65.9 MHz), and p020 that
of any signed-off 6x4 configuration (+3.621 ns against p014's +2.737 ns).
Neither was adopted: `src/config.json` then carried p010 and the 6x4 overlay
p014 (R16 to R19, R83).

## Starting point

**`v0.1-hardened`.** The design of record at tag `v0.1-hardened` (`c118027`)
passed the official gds, precheck and gl_test actions in GitHub run
36144357821. That run had utilization 58.5% and, at 50 MHz (20 ns):

- typ setup worst slack (WS) +0.89 ns;
- slow-corner setup WS −8.52 ns;
- route DRC, LVS and antenna all 0.

The flow signs off setup only at the typ corner (`TIMING_VIOLATION_CORNERS`
`*typ*`), so the slow corner does not fail the action. Almost every
slow-corner violation starts at `rst_n`: 2,467 of the 2,482 in that run.

**Adopted v1 result.** Commit `25e331e` adopted v1's promotion p010 (trial
`dor-26a873-c8bf57e#94`) into `src/config.json`. Its promoted full run
(job 24010051, `OPENROAD_THREADS` 4) had setup WS typ/fast/slow
+7.88/+9.31/+2.95 ns at 20 ns, slew/cap/fan-out violations 4/0/3, typ fmax
estimate 82.5 MHz and utilization 61.9%; the TT precheck passed 9/9 (job
24011934) and the gate-level suite had 0 failures (job 24011935). The RTL did
not change. For `131e793`, which carries that configuration:

- the GitHub gds run (36257636798) passed gds, precheck and gl_test (102
  tests, 46 pass, 56 skip, 0 fail). Its setup WS is typ/fast/slow
  +7.88/+9.31/+2.95 ns with 0 violations at every corner, and its
  `metrics.csv` is byte-identical to that of the promoted full run
  (job 24010051), with the same gate-level netlist. The prediction of the
  fast-mode trial (job 24009268) and of the promotion matched the official
  result (`docs/results.md`, R16 to R19). (When this paragraph was first
  written, the run was still in progress.)
- the gds_6x4 run (36257636751) failed in "Build GDS" with `69 Routing DRC
  errors found`. The 6x4 build inherited the adopted keys through
  `variants6x4/config.overlay.json`, which then restated placement,
  obstruction, density and halo but no timing key. The optimizer's trial of
  the same configuration shows the same count ([Campaign record](#campaign-record),
  v2). `4bd30c8` put promotion p014 into the overlay (`docs/6x4.md`
  sections 4b and 5b).

**Two observations from v1 that still shape the search space.** The first
comes from sweep run `base-26a873-8x4-fp8_base-d60-p20-h0p1_0p05-fast-t32`
(job 23749131_0, before the halo change), the second from LibreLane itself.

1. **The post-CTS resizer barely acts on setup at small margins.**
   - In job 23749131_0, the post-CTS resizer log
     (`37-openroad-resizertimingpostcts`) reports `RSZ-0098 No setup
     violations found`, although all three corners are loaded
     (`RSZ_CORNERS` unset, so `STA_CORNERS`).
   - Post-route STA of the same run then shows slow-corner WS −5.09 ns. The
     worst slow path is `rst_n` → input buffer → a chain of
     `sg13cmos5l_buf_1` fan-out buffers with slews of about 0.9 ns → logic →
     flip-flop. With placement-estimated parasitics, the path shows positive
     slack.
   - So the setup-repair margins (`PL_RESIZER_SETUP_SLACK_MARGIN`, and the
     post-GRT repair) are searched up to several nanoseconds. v1 confirmed
     the lever: its best 20 ns trials use 5.75 to 6.0 ns, the top of its
     0–6 ns range, so v2 searches 0–9 ns.
2. **`GRT_ADJUSTMENT` has no effect in this flow.**
   - `scripts/openroad/common/set_layer_adjustments.tcl` (LibreLane
     3.1.0.dev3) first applies `GRT_ADJUSTMENT` to every layer.
   - It then overrides each layer with `GRT_LAYER_ADJUSTMENTS`, and the IHP
     PDK sets that for all five routing layers (`0,0,0,0,0`).
   - The per-layer values are therefore searched instead.

## Tracks

`tools/opt/tracks.py` derives the tracks from the frozen tree the driver runs
on. Each track has a core, a die, a clock period, a search space and a
**committed configuration** (its "base"): what the repository builds for that
combination today.

| Track | Core | Die | `CLOCK_PERIOD` | Committed configuration | Weight | Gate-level `PE_VARIANT` |
|---|---|---|---|---|---:|---|
| `dor` | `src/protocol_emulator_core.v` (sha256 `26a873db…`) | 8x4 | 20 ns (50 MHz) | `src/config.json` with `CLOCK_PERIOD` 20 (as committed until `d76f1cc`) | v3: 0.05 (0.15 from `f511c97`; 0.35 before) | `base` |
| `dor15` | same | 8x4 | 15 ns (66.7 MHz) | `src/config.json` as committed since `d76f1cc` (before: with `CLOCK_PERIOD` 15); the snapshot's `info.yaml` gets `clock_hz` 66666667 | v3: 0.15 (0.35 from `f511c97`; 0.22 before) | `base` |
| `dor13` | same | 8x4 | 13.33 ns (75.0 MHz) | `src/config.json` with `CLOCK_PERIOD` 13.33; `clock_hz` 75018755 | v3: 0.45 (0.15 from `f511c97`; 0.08 before) | `base` |
| `diet4_6x4` | `variants6x4/protocol_emulator_core.v` (`diet4`, sha256 `cc27c465…`) | 6x4 | 20 ns | `src/config.json` with `variants6x4/config.overlay.json` merged (RFC 7386, as `variants6x4/switch.py apply` does); `info.yaml` `tiles` "6x4" | 0.20 | `diet4` |
| one per variant | `$PE_WORK/variants/cores/<name>.v` (sha256 as in its `MANIFEST.sha256`) | 8x4 | 20 ns | `src/config.json` (with `CLOCK_PERIOD` 20 since `d76f1cc`) | 0.15, shared | `<name>` |

The weights are the shares of new trials ([Allocation](#allocation)). In
v3 `dor13` gets the largest share, 0.45, because the goal is an official
sign-off at 13.33 ns ([The 75 MHz plan](#the-75-mhz-plan)); `dor15` gets
0.15, `dor` 0.05, `diet4_6x4` 0.20 and the variant tracks 0.15 together.
From `f511c97` (driver job 24077569, from 2026-09-27 01:57 cluster time)
`dor15`, the committed period, gets the largest share: `dor15` 0.35,
`dor13` 0.15, `dor` 0.15, `diet4_6x4` 0.20 and the variant tracks 0.15
together, so the frequency tracks together get 0.50. Until then the
frequency tracks together got 0.30, split 0.22/0.08 because 15 ns was the
primary target (the Tiny Tapeout demo board clock goes up to about 66.5 MHz;
TT clock page, quoted in `docs/extension-study.md` section 3), and `dor`
got 0.35.

Because the tracks derive their committed configuration from the frozen
tree, the move of `src/config.json` to p018 changed two baselines at the
relaunch on `f511c97`, without starting new tracks (the knob keys are not
part of the track identity): the committed configuration of `dor15` is now
p018's trial `#1`, and that of `dor` (and of each variant track) is
p018's knob set at 20 ns. For `dor`, v1 had already run that knob set as
trial `dor-26a873-c8bf57e#118` (imported as `#114`; fast mode, setup WS
+7.86 / +9.39 / +3.00 ns); its control promotion is p027.

A variant core is used only if its sha256 matches the MANIFEST; a core whose
body equals the design of record is skipped; every core is copied to
`optimizer/cores/<name>-<sha12>.v`, so that a republish cannot change a
track. The driver rescans the MANIFEST every 10 minutes.

### Absolute knob values

A trial is a set of knob values ([Search space](#search-space)). In v2 these
values are absolute:

- `space.base_knobs()` maps a committed configuration to its knob set. For
  the design of record at `25e331e` this is exactly the knob set of v1 trial
  `#94` (p010).
- `space.materialize()` turns any knob set into the `make_snapshot.py`
  parameters and LibreLane overrides. A key is written when the knob value
  differs from the committed value. It is deleted (a `null` override) when
  the knob value is the LibreLane/PDK default and the committed config sets
  the key. The defaults were checked against the `resolved.json` of a run
  without any of these keys.
- So a knob set gives the same effective configuration whatever the
  committed configuration is. `tools/opt/test_opt.py` checks this on 300
  random knob sets and two committed configurations: `src/config.json`, and
  the same file with every knob-controlled timing key removed and the
  `fp8_base` placement with halo 16.48 (as before the adoption). It also
  checks that the committed configuration needs no override, and that a
  knob set is recovered from its own effective configuration.

**Why this changed.** v1 stored knob values relative to the configuration
committed at its launch (`c118027`: density 60, halo 16.48, `fp8_base`, no
timing keys) and left out every value equal to those defaults. After
`25e331e` changed the committed configuration, the v1 tools on tree
`131e793` (driver job 24020736, started 13:35) produced wrong configurations:

- every knob set with floorplan `fp8_base` kept the committed halo 20, which
  `macros/check_macro_floorplan.py` rejects ("closer than two halos"); 33
  trials were rejected that way;
- a knob at its old default (for example `PL_TIMING_DRIVEN` false) silently
  kept the new committed value, so the recorded knobs of the three trials
  that were submitted (jobs 24020881, 24020882, 24021036) do not describe
  their configurations.

That driver was cancelled at 13:40. Its trials stay in the store under
the earlier track `dor-26a873-ccca9e6` and are not imported (below).

### Track identity and imported trials

A track id is `<name>-<core sha6>-<die>-p<period>-f<fixed sha6>`, for example
`dor15-26a873-8x4-p15-f174dc0`. The "fixed part" is the committed
configuration without its knob-controlled keys and comments (`tracks.fixed_part()`).
Because knob values are absolute, adopting a new knob set keeps the tracks;
only a change of a fixed key (PDN, pins, template keys, ...) starts new ones.

A finished trial of an earlier track is **imported** into a current track
when:

1. the two tracks have the same core, die and period; and
2. the earlier trial's effective configuration equals the effective
   configuration of its knob set on the current tree. The earlier
   configuration is its frozen tree's `src/config.json` with the trial's
   recorded `config_changes_vs_repo` applied. The current one is computed
   by `tracks.effective_config()`, a mirror of `make_snapshot.py`'s config
   edit.

An imported trial is a `trial_import` event: the same metrics, legality and
value, the original run directory and job id, and a note of the trial it
stands for. It is added to the track's optuna study with
`Study.add_trial()`, so the sampler continues from it. No LibreLane run is
repeated. The driver imports at every loop, so earlier trials that finish
later are imported too.

At the v2 launch (driver job 24024261) the driver imported 207 trials:

- 196 at start: 122 into `dor` and 9 to 12 into each variant track, from the
  v1 tracks `*-c8bf57e`;
- 11 in the first loop, from v1 trial jobs that had finished while no driver
  ran.

Three more were imported later, when the last v1 trial jobs finished:
`dor-26a873-c8bf57e#32` (job 23998222) at 15:22, `#89` (job 24007758) at
15:52 and `cn-0416d6-c8bf57e#10` (job 24011966) at 17:42, all three not
legal. The total at 18:17 was 210: 134 in `dor`, 10 to 12 in each variant
track (the "imported" column of the leaderboard).

Not imported:

- the two v1 trials with `DRT_OPT_ITERS` 80 (a retired value);
- every trial of `*-ccca9e6`, whose recorded knobs do not match their
  configurations.

Before the launch, the same equality was checked offline for every finished
trial of the v1 tracks `*-c8bf57e`. The only trials it failed for were
those two `DRT_OPT_ITERS` trials.

Every trial the driver builds is also checked the other way: its
`make_snapshot.py` `config_changes_vs_repo` must equal the mirror's. A
difference is logged and recorded as `mirror_mismatch` in the trial.

### Seeds

A new track starts with a queue of designed trials ("seeds"). After them,
TPE samples.

- **`dor`:** the committed configuration. It duplicates the imported
  trial that stands for v1 `#94`, so it runs nothing.
- **`dor15`, `dor13`:** the committed configuration at the track's period,
  and the 8 best legal 20 ns configurations of `dor`. The committed
  configuration is among those 8, so there are 8 distinct seeds.
- **`diet4_6x4`:** 8 distinct seeds:
  - the committed 6x4 build;
  - the 6x4 point signed off before the p010 adoption
    (`docs/6x4.md` section 4: every timing key at its LibreLane default,
    density 60, hold margin 0.1; typ +4.30, slow −2.80 ns in full mode, run
    23980402_9);
  - the 5 best 20 ns configurations of `dor` and the 3 best 8x4 `diet4`
    trials, translated into the 6x4 space (`space.translate()`). The 6x4
    floorplan and halo replace theirs, and any value outside the 6x4 space
    falls back to the committed one.
- **Variant tracks:** the committed configuration and the 3 best 20 ns
  configurations of `dor`.

After that, every other track is offered the current best legal
configuration of `dor` once per distinct best, unless it has already run
or is already queued.

**Seed revisions.** Seeds designed after a track exists are added once to
every active track whose recorded revision is lower (`SEED_REVISION` in
`driver.py`, recorded as `track_seeds` events).

- Revision 1 is the list above.
- Revision 2 (2026-09-26 17:17) adds up to 9 seeds to `dor15` and `dor13`:
  the committed configuration and the 8 best 20 ns configurations of `dor`,
  with `PL_RESIZER_SETUP_SLACK_MARGIN` scaled by period / 20 ns and rounded
  to 0.05 ns (for example 5.75 → 4.30 ns at 15 ns and 3.85 ns at 13.33 ns).
- The reason for revision 2: the first 13.33 ns trial (the committed
  configuration, margin 5.75 ns, job 24024333) was still in
  `OpenROAD.ResizerTimingPostCTS` after almost 3 hours. At 17:12 its log
  showed 3,773 endpoints below the margin.
- Revision 3 (v3) adds to `dor15` and `dor13` the three best legal trials
  of the track whose projected official job exceeds 5.5 h, each with
  `PL_RESIZER_SETUP_REPAIR_TNS_PCT` 25 and 10 (the runtime knob,
  [Timing-repair runtime knobs](#timing-repair-runtime-knobs-v3)); the 6x4
  space has no such knob. Which trials those are depends on the store at
  the relaunch: dry run 24130924 (store of 18:20) enqueued 6 seeds in each,
  from `dor13` #51, #1 (p024's trial) and #52 and from `dor15` #13
  (p021's), #128 and #126. The value 0 is not seeded
  ([Experiment](#experiment-pl_resizer_setup_repair_tns_pct-on-the-committed-configuration)).

## Frequency tracks and the SDC

The frequency tracks change only `CLOCK_PERIOD` (and `info.yaml`
`clock_hz`). Whether they are a valid Tiny Tapeout submission, and what their
slacks mean, depends on how LibreLane and tt-support-tools use the period.

**What LibreLane 3.1.0.dev3 constrains.** The constraints were read in the
`librelane` package of `librelane-3.1.0.dev3.sif`: `scripts/base.sdc`
(sha256 `34c8f331…`), `steps/openroad.py`, `config/flow.py` and
`scripts/pyosys/construct_abc_script.py`. The values below are from a v1
run's `resolved.json`.

- **Which SDC.** `PNR_SDC_FILE` and `SIGNOFF_SDC_FILE` are null in this
  project. Every OpenROAD step, including the post-route multi-corner STA
  (`OpenROAD.STAPostPNR`), therefore reads `FALLBACK_SDC`, whose default is
  `scripts/base.sdc`: `_SDC_IN = PNR_SDC_FILE or FALLBACK_SDC`, and
  `SIGNOFF_SDC_FILE` only if it is set.
- **Clock.** `create_clock` on `clk` with `-period $CLOCK_PERIOD`.
- **I/O delays scale with the period.** `set_input_delay` (all inputs except
  the clock) and `set_output_delay` (all outputs) are both
  `CLOCK_PERIOD × IO_DELAY_CONSTRAINT / 100`, with `IO_DELAY_CONSTRAINT` 20:
  4.0 ns at 20 ns, 3.0 ns at 15 ns and 2.67 ns at 13.33 ns.
- **Fixed terms.** The following do not depend on the period:
  - clock uncertainty `CLOCK_UNCERTAINTY_CONSTRAINT` 0.25 ns;
  - clock transition `CLOCK_TRANSITION_CONSTRAINT` 0.15 ns;
  - timing derate ±`TIME_DERATING_CONSTRAINT` 5%;
  - `set_max_fanout MAX_FANOUT_CONSTRAINT` (a knob);
  - driving cell `sg13cmos5l_buf_4/X` (`SYNTH_DRIVING_CELL`);
  - output load `OUTPUT_CAP_LOAD` 6 fF;
  - no `MAX_TRANSITION_CONSTRAINT` or `MAX_CAPACITANCE_CONSTRAINT` (the
    liberty values apply);
  - propagated clocks at sign-off.
- **Synthesis depends on the period for some strategies.**
  `construct_abc_script.py` passes `CLOCK_PERIOD` × 1000 ps as the `-D`
  target of `retime` and of the `upsize`/`dnsize` fine-tuning in the
  AREA 0–2 and DELAY 0–3 scripts. The AREA 3 and DELAY 4 scripts (the ORFS
  scripts; DELAY 4 is the committed strategy) do not use it.
- **Placement and repair depend on the period.** Timing-driven placement,
  the setup repair with its margin, CTS and the post-GRT repair all work
  on slacks at the constrained period. So a frequency track is not the
  20 ns layout timed at a shorter period, which is one reason for a
  separate study per period.

So at period T, a register-to-register path must fit in T − 0.25 ns, and an
input-to-register or register-to-output path in 0.8·T − 0.25 ns (before
derate, clock skew and the flip-flop's setup time). The **fmax estimates**
of the leaderboard are
`1000 / (T − WS)` per corner, plus a register-to-register-only version,
as in `docs/sweep.md`. For a register-to-register worst path this
extrapolates correctly from fixed delays. For an I/O worst path the slack
changes by 0.8 ns per ns of period, so the estimate is conservative when WS
is positive and optimistic when it is negative. The frequency tracks do not
depend on the estimate: their STA runs at 15 or 13.33 ns, and a
non-negative WS at a corner means that corner meets that period in the model.

**What Tiny Tapeout takes from `info.yaml`.** In tt-support-tools
`d66cf179e` (the revision of the precheck reproduction, `docs/drc-triage.md`):

- `project_info.py` requires `clock_hz` to be an integer.
- `project.py` and `doc_utils.py` use it only in generated documentation
  (the datasheet's clock line, `DocsHelper.pretty_clock`, and the project
  index).
- `create_user_config()` writes `DESIGN_NAME`, `VERILOG_FILES`, `DIE_AREA`,
  `FP_DEF_TEMPLATE`, `VDD_PIN`, `GND_PIN`, `RT_MAX_LAYER` and the tech's
  LibreLane config. It does not write `CLOCK_PERIOD`, which comes from
  `src/config.json`.
- The precheck does not read `clock_hz`.

So a 15 ns configuration is a Tiny Tapeout submission with `CLOCK_PERIOD`
15 in `src/config.json`. `make_snapshot.py` writes `CLOCK_PERIOD` and
`clock_hz` = 10⁹ / period (66666667 for 15 ns) into every frequency-track
snapshot, and a promotion's precheck runs on that `info.yaml`. The
repository sets them independently: `d76f1cc` commits `CLOCK_PERIOD` 15 and
keeps `clock_hz` 50000000, because `clock_hz` states the clock the chip is
operated at, and the datasheet, the firmware and the host library assume
50 MHz ([Adoption of p018](#adoption-of-p018-d76f1cc)). The gds action
fails on a typ-corner setup violation at the configured period
(`TIMING_VIOLATION_CORNERS` `*typ*`), which is legality rule 1 below.

## Runtime and the 6-hour limit

GitHub stops a job on a hosted runner after 6 hours (21,600 s). The Tiny
Tapeout `gds` job is one such job. It sets up the runner, runs the whole
LibreLane flow on the runner's 4 vCPUs (`OPENROAD_THREADS` 4), then writes
the summaries, uploads the artifacts and renders a PNG of the layout. A
configuration is signed off officially only if that job finishes.
The `gds_6x4` job, which builds the 6x4 fallback, has the same limit.
`tools/opt/runtime.py` (v3) projects the job's length from a local run, and
the driver uses the projection as a promotion rule in the tracks that an
official action builds: `dor15` and `dor13` (`gds`) and `diet4_6x4`
(`gds_6x4`). The `dor` track (20 ns) has not been built by an official
action since `d76f1cc` set `CLOCK_PERIOD` 15, and the variant tracks never
are; they have no rule.

### The official jobs

Every official `gds` or `gds_6x4` job whose configuration has a local full
run was paired with that run. The pairing rebuilt the local run's
configuration from its frozen tree's `src/config.json` and its recorded
`config_changes_vs_repo`, and compared it key by key with the commit's
`src/config.json`, with `variants6x4/config.overlay.json` merged for 6x4.
All 18 pairs are identical, `OPENROAD_THREADS` included. The official step
times come from the job logs: LibreLane's `Running '<step>'` lines with
GitHub's timestamps. The local ones come from each run's `result.json`.

| Configuration | Local full run: job, node, flow time | Official jobs: commit, GitHub run, job length |
|---|---|---|
| p001: 8x4, 20 ns, committed from `c118027` to `39d21e8` | 23993269, node1631, 5,500 s | `39d21e8` 36238342723 11,160 s; `be7dbda` 36225500459 10,637 s; `8ba08e5` 36225133221 10,501 s; `aa07868` 36222609980 10,455 s; `ea98c08` 36221321894 10,625 s; `c118027` 36188299524 10,450 s and 36144357821 6,798 s |
| p010: 8x4, 20 ns, committed from `131e793` to `4bd30c8` | 24010051, node1605, 2,785 s | `131e793` 36257636798 6,687 s; `1e5b1d8` 36285537636 6,566 s; `4bd30c8` 36274474548 4,117 s |
| p018: 8x4, 15 ns, committed since `d76f1cc` | 24042265, node1611, 7,833 s | `d76f1cc` 36298635436 15,856 s; `4c30622` 36308043760 9,966 s; `fff6746` 36323174075 15,967 s |
| p014: 6x4, 20 ns, in the overlay since `4bd30c8` | 24036160, node1380, 3,495 s | `gds_6x4` of `4bd30c8` 36274474540 3,983 s; `1e5b1d8` 36285537630 4,106 s; `d76f1cc` 36298635404 3,989 s; `4c30622` 36308043804 4,074 s; `fff6746` 36323174037 3,926 s |

The `gds_6x4` job of `131e793` (36257636751) is not in the table because it
failed: route DRC 69 after 15,009 s, with no complete flow. Its
configuration is that of p012, whose full run (24029191) failed the same
way ([Starting point](#starting-point)), so neither has a complete flow time.

**Variance.** The same configuration took 9,966 to 15,967 s (p018), 4,117 to
6,687 s (p010) and 6,798 to 11,160 s (p001), a factor of 1.60 to 1.64
between the fastest and the slowest job. The five 6x4 jobs took 3,926 to
4,106 s. Relative to the local full run, the official flow took 1.044 to
1.913 times as long, and the whole job 1.123 to 2.401 times.

The spread comes from two machines: the runner and the local node.

- **Speed factor.** The yosys synthesis step is single-threaded and does
  the same work for the same synthesis settings and core, so its time
  measures the machine. The speed factor of a run is s = its synthesis time
  / the fastest synthesis time with the same synthesis key among the
  optimizer's runs.
- **Runners.** The runners fall into two groups: s = 0.908 to 0.974 (3
  jobs: p018 on `4c30622`, p010 on `4bd30c8` and the first `c118027` job)
  and s = 1.401 to 1.517 (the other 15).
- **Local nodes.** They range from s = 1.00 (node16xx, node31xx) to 2.15
  (node1384, where p024's full run ran), the maximum observed.
- **Normalized.** Divided by s, the official flow took 1.189 to 1.333 times
  the local full run's in all 18 jobs, against 1.044 to 1.913 before the
  division.

Around the flow, the job spent 116 to 167 s before it (checkout, tool
installation) and 141 to 151 s (6x4) or 617 to 1,217 s (8x4) after it.
Most of the time after the flow is `tt_tool.py --create-png`: 570 to
1,151 s for 8x4, 87 to 98 s for 6x4.

**Where the time goes at 13.33 ns.** p024's full run (job 24068227,
node1384) took 26,549 s of flow time. 21,807 s of it (82.1%) was
`OpenROAD.ResizerTimingPostCTS`. Its trial (job 24026489, node1620) took
13,484 s, 12,082 s of it (89.6%) in that step. In p018's official jobs the
step took 7,687 and 7,556 s on the slower runners and 4,752 s on the
faster one; p018's local full run took 4,133 s there.

### Model

`runtime.py` takes each constant as the maximum over the 18 jobs, rounded
up (`K_RSZ` with more headroom, see its row):

| Constant | Value | Maximum observed |
|---|---:|---|
| `S_CI`, runner speed factor | 1.52 | 1.517 (`gds_6x4` job of `1e5b1d8`) |
| `C_FULL`, normalized official flow / normalized local full-run flow | 1.34 | 1.333 (p018, `fff6746`) |
| `K_RSZ`, normalized official timing repair / normalized trial repair | 1.65 | 1.556 measured (p018, `fff6746`; the only configuration of the 18 jobs with a long repair). p021's full run took 1.197 times its trial's repair (normalized), against 1.172 for p018, and 1.197 × p018's largest official/full ratio 1.328 is 1.589, so the value is raised to 1.65. |
| `K_REST`, the same for the flow without the repair | 3.74 | 3.738 (p001, `39d21e8`) |
| `OVERHEAD_S`, job time outside the flow | 1,370 s | 1,367 s (p018, `d76f1cc`) |

With F the flow time, R the timing-repair time (post-CTS plus post-GRT) and
s the run's speed factor:

- **From a full run** (`OPENROAD_THREADS` 4):
  job ≤ 1,370 + 1.52 × 1.34 × F / s.
- **From a fast-mode trial** (32 threads):
  job ≤ 1,370 + 1.52 × (1.65 × R + 3.74 × (F − R)) / s.
- **Complete flows only.** Both need a flow that ran to its end
  (`flow_complete` in `result.json`). A timed-out or failed run has no
  projection; `dor13` #26 and #27, which timed out in detailed routing,
  would otherwise have looked short.
- **Why the repair is separate.** The repair hardly speeds up with threads.
  Normalized, a full run's repair took 0.94 to 1.20 times its trial's in the
  four promotions with a long repair (p018, p019, p021 and p024). The rest
  of the flow took 1.75 to 2.93 times as long at 4 threads as at 32 threads
  (all 29 promotions).

**Validation.**

- Both projections exceed each of the 18 official jobs: the full-run one by
  at least 310 s (4.6%), the trial one by at least 770 s. `test_opt.py`
  checks the constants and both projections against the 18 jobs.
- Over the 28 promotions with a complete full run (p012's did not
  complete), the trial projection is 0.96 to 1.46 times the full-run
  projection, and the two agree on the 5.5 h bound in all 28.
- **No trial margin.** Only p001 has a trial projection below its full-run
  one (0.963 times). A trial bound 2% or more below 5.5 h would have
  rejected p018 (trial 5.45 h, full run 4.75 h, official jobs at most
  4.44 h). The full-run projection is part of the verdict instead (below),
  so a trial projection that is too low costs one full run, not an
  adoption.

**Local flow-time bound.** For the official job to stay under 5.5 h
(19,800 s) on the slower runner group:

- A full run's normalized flow time must be at most
  (19,800 − 1,370) / (1.52 × 1.34) = 9,048 s. That is 2.51 h on the fastest
  local nodes (s = 1), and 9,048 × s seconds on a slower node.
- For a trial the condition is 1.65 × R / s + 3.74 × (F − R) / s ≤ 12,125 s.

Projections of the promotions with an official job or a period below 20 ns
(reference synthesis time 56.638 s for the 8x4 core with DELAY 4, as the
driver computes it):

| Promotion | Full run: job, node, flow time, s | Projected official job | Official jobs |
|---|---|---:|---|
| p010 (20 ns) | 24010051, node1605, 2,785 s, 1.008 | 1.94 h | 1.14 to 1.86 h |
| p001 (20 ns) | 23993269, node1631, 5,500 s, 1.016 | 3.44 h | 1.89 to 3.10 h |
| p014 (6x4) | 24036160, node1380, 3,495 s, 1.740 | 1.52 h | 1.09 to 1.14 h |
| p018 (15 ns) | 24042265, node1611, 7,833 s, 1.013 | 4.75 h | 2.77 to 4.44 h |
| p019 (15 ns) | 24045480, node1600, 9,888 s, 1.021 | 5.86 h | none |
| p021 (15 ns) | 24048020, node1398, 16,321 s, 1.565 | 6.28 h | none |
| p024 (13.33 ns) | 24068227, node1384, 26,549 s, 2.150 | 7.37 h | none |

p024's official job is projected at 7.37 h from its full run and 9.92 h
from its trial, above 6 h on the slower runner group, which ran 15 of the 18
jobs. With the faster group's maxima instead (s 0.974, 781.6 s outside the
flow, the same 1.333), the projection from the full run is 4.67 h. The job
would therefore depend on the runner it gets.

### The rule in the driver (v3)

- **Step times.** Every run's metrics carry the step times of `runtime.py`
  (`rt_synth_s`, `rt_rsz_s`, `rt_rsz_grt_s`, `rt_drt_s`, `rt_flow_s`,
  `rt_wall_s`, node, threads), whether the flow ran to its end
  (`rt_complete`), and the synthesis settings of the run's
  `out/resolved.json` (`rt_synth_cfg`). Runs that finished before v3 get
  them once from their `result.json` and `out/resolved.json`
  (`trial_runtime` and `promo_runtime` events).
- **Synthesis key.** The key is `SYNTH_STRATEGY`, the ABC fine-tuning,
  `MAX_FANOUT_CONSTRAINT` and the core, plus `CLOCK_PERIOD` for the
  strategies that read it. It is taken from the run's resolved configuration
  when recorded, else from its knob set. That matters for the `*-ccca9e6`
  trials whose recorded knobs do not describe their runs
  ([Absolute knob values](#absolute-knob-values)): `dor-26a873-ccca9e6` #13
  and #20 recorded AREA 0 and fan-out 10 but ran DELAY 4 and fan-out 8. The
  reference time of a key is the minimum synthesis time over the store's
  runs with that key.
- **Rule tracks.** `runtime.RULE_KINDS`: `dor15`, `dor13` and
  `diet4_6x4`.
- **Eligibility.** In a rule track, a trial is promoted only if its
  projection is at most 5.5 h. A trial without a projection is not
  eligible. A promotion whose projection exceeds the bound (from its full
  run once that has finished, else from its trial) does not set the bar
  that a new best must clear. So p019, p021 and p024 no longer hold back a
  promotion.
- **Verdict.** In a rule track, a promotion whose four gates pass is PASS
  only if its full run's projection is at most 5.5 h. Above that the
  leaderboard shows "PASS (over CI time bound: projected official job …)",
  which is not eligible for adoption; p019 (5.86 h), p021 (6.28 h) and p024
  (7.37 h) read that way in the dry runs. A full run without a projection
  reads "PASS (CI time not projected …)".
- **Ranking.** Trials rank by min WS (10 ps), then by projection, then by
  the rest of the objective.
- **Leaderboard.** It shows the projection of every trial and promotion.
  "Best" in these tracks is the best eligible trial, and better-ranked
  ineligible trials are named.
- **TPE.** The value of an ineligible trial is lowered by 1 ns, plus 1 ns
  per hour over 5.5 h. The values of trials told before v3 stay as they
  were told; the study is not re-valued. A duplicate of such a trial (an
  identical knob set sampled again) is told the penalized value, and the
  `trial_done` event records the objective's own value and the projection
  (`value_objective`, `ci_proj_s`).
- **Trial time limit.** Trial jobs of the rule tracks get 5 h instead of
  6 h. An eligible trial's flow takes at most 12,125 / 1.65 = 7,348
  normalized seconds, which is 15,799 s (4.39 h) at s = 2.15, the slowest
  local node observed; a slower node would need longer. The job is warned
  900 s before its limit.

## The 75 MHz plan

**Goal.** An official Tiny Tapeout sign-off of the design of record at
`CLOCK_PERIOD` 13.33 ns (75.0 MHz), with `info.yaml` `clock_hz` unchanged
at 50000000, the operating clock. Official means that the `gds`,
`precheck` and `gl_test` actions pass, with setup met at every corner. A
13.33 ns configuration counts only after those actions pass on it. The
local promotion comes before that as a gate: the same LibreLane image, the
Tiny Tapeout precheck, the gate-level cocotb suite with the CI's simulator,
and `formal_eq`.

### Where it stands

These numbers come from dry run 24130924 on a copy of the store of
2026-09-27 18:20 cluster time (2,545 events).

- **p024.** p024 (`dor13` #1, the committed configuration at 13.33 ns)
  passed the local sign-off with setup WS +4.962 / +6.095 / +0.756 ns. Its
  official job is projected at 7.37 h from the full run and 9.92 h from
  the trial. It is not eligible, its verdict reads "PASS (over CI time
  bound …)", and on the slower runner group its job would exceed 6 h
  ([Runtime and the 6-hour limit](#runtime-and-the-6-hour-limit)).
- **Best legal `dor13` trial.** Since 2026-09-27 17:45 cluster time it is
  #51 (job 24113063, margin 8.55 ns): +4.656 / +5.645 / +0.796 ns,
  projected 8.42 h, not eligible.
- **Eligible `dor13` trials.** 18 of the 34 legal `dor13` trials are
  eligible. The best is `dor13` #14 (job 24070219, setup margin 4.45 ns),
  with setup WS +4.569 / +5.634 / +0.450 ns, hold WS minimum +0.121 ns
  (fast) and a projection of 4.62 h. The dry run promoted it (p033 in its
  copy of the store).
- **`dor15`.** 32 of the 83 legal trials are eligible. The best is #16
  (job 24043124), with +5.741 / +6.682 / +2.473 ns and a projection of
  3.70 h (p032 in the dry run). The committed p018 projects to 4.75 h, and
  its official jobs took 2.77 to 4.44 h. p019 (5.86 h) and p021 (6.28 h)
  exceed the bound.
- **`diet4_6x4`.** 56 of the 62 legal trials are eligible; the other 6
  project to up to 9.26 h (#13). The best, #79 (+5.960 ns at the slow
  corner, projected 1.70 h), is promotion p031 of the running campaign.

### Why the repair takes that long

The first `repair_timing` call of `OpenROAD.ResizerTimingPostCTS` is
`repair_timing -setup -setup_margin <PL_RESIZER_SETUP_SLACK_MARGIN>`. It
works through every endpoint whose slack is below the margin, worst first
(OpenROAD `dcf36133`, the build in the image, `src/rsz/src/RepairSetup.cc`).

- **Per endpoint.** An endpoint gets up to `-max_passes` (10,000) passes.
  It stops when it reaches the margin, when a pass changes nothing, or
  after more than 50 passes without improvement.
- **The early stop.** Every 100 iterations after the first 1,000,
  `terminateProgress()` divides the improvement in total negative slack
  (TNS) since its last check by the initial TNS. It stops the current
  endpoint when that rate is below a threshold (0.01%, doubled every 1,000
  iterations), and two such stops in a row end the repair.
- **Why it never stops here.** With a margin of several nanoseconds and no
  negative slack at the start, the initial TNS is 0 and the rate is 0/0
  (not a number). The comparison is then false, so the repair visits every
  endpoint below the margin.

In p024 (margin 5.8 ns) that meant 3,898 endpoints and 67,857 iterations.
The resizer's worst slack (placement parasitics) rose from +0.407 to
+2.005 ns by iteration 320 (0.47% of the iterations) and ended at +2.018 ns
(from iteration 28,401 on). In all, the repair resized 5,593 instances and
inserted 1,253 buffers (area +7.6%). Trials whose
initial worst slack is negative stop early instead. For example, `dor15`
#76 (AREA 1 synthesis, initial worst slack −0.416 ns, margin 6.9 ns)
stopped after 1,200 iterations and 276 s.

The knob that bounds this loop is `PL_RESIZER_SETUP_REPAIR_TNS_PCT`
([Timing-repair runtime knobs](#timing-repair-runtime-knobs-v3)). With it,
only the worst endpoints are repaired. p024's post-route worst endpoint had
at least +2.018 ns in the resizer's view (its worst slack) and +0.756 ns
after routing, and the
[experiment](#experiment-pl_resizer_setup_repair_tns_pct-on-the-committed-configuration)
shows the post-route slack falling with a partial repair.

### Slow-corner paths of p024 at 13.33 ns

`tools/sta/` on p024's full run (job 24119756). Control 1 passed: 51
metrics are equal to the run's `metrics.csv`, with maximum difference 0.
The worst path of each class at the slow corner:

| Class | Setup WS | Worst path |
|---|---:|---|
| register to register (macros included) | +0.756 ns | `core.instruction_sram_e2_hi/A_DOUT[14]` → `_62488_/D` (`core.completed_instructions_0[31]`) |
| input to register | +1.635 ns | `ui_in[1]` → `_61195_/D` |
| input to output | +3.907 ns | `ena` → `uio_out[5]` |
| register to output | +4.813 ns | `_62088_/Q` → `uio_out[5]` |

Hold WS is +0.736 ns at the slow corner. The flow's own slow-corner report
(`max.rpt` of job 24068227) lists the 1,000 worst setup endpoints, all
between +0.756 and +2.040 ns:

- **Start points.** 446 of the paths start at an SRAM output: 434 of them
  end at a flip-flop and 12 at an SRAM input (both ends on the SRAM clock).
  The other 554 start and end at a flip-flop.
- **Below +1.0 ns: 6 endpoints.** Four are bits 27, 29, 30 and 31 of
  `core.completed_instructions_0`, from `instruction_sram_e2_hi`, at
  +0.756 to +0.963 ns. One is `core._2457`, from `instruction_sram_e1_lo`,
  at +0.984 ns. One is flip-flop to flip-flop, `core._1623[6]` →
  `core._2033[7]`, at +0.996 ns. The generator's name numbering does not
  identify the engine (`docs/timing-closure.md` section 6.1).
- **+1.0 to +1.25 ns: 46 more.** These are SRAM paths into
  `completed_instructions_0[28]`, `x_0`, `x_2`, `y_0`, `y_2`,
  `completed_instructions_2` and unnamed registers.

The worst path, in the order a signal takes:

- **Launch.** The SRAM's `A_CLK` sees the clock at 1.477 ns. That includes
  five delay buffers (`delaybuf_0_clk` to `delaybuf_4_clk`, 0.721 ns), which
  CTS's latency balancing inserts between the macro tree and the register
  tree.
- **SRAM read.** The SRAM clock-to-output delay is 5.257 ns at the slow
  corner, 39.4% of the period.
- **Logic.** 6.724 ns of logic follows. Its last 2.836 ns are an AND4
  chain ending in XOR2 and NOR2 at bit 31: the counter's increment, with
  the late enable inside the carry chain (docs/timing-closure.md section 4,
  "The carry-chain effect").
- **Capture.** The capture clock latency is 1.350 ns, the uncertainty
  0.25 ns and the setup time 0.215 ns, so the required time is 14.215 ns
  against an arrival of 13.458 ns.

p021's 15 ns layout re-timed at 13.33 ns (job 24119757) gives +0.886 ns at
the slow corner, its +2.556 ns shifted by the period difference.

### Can configuration knobs reach +1 ns at the slow corner?

- **What is needed.** +0.244 ns on the 6 endpoints below +1.0 ns, with the
  next 46 between +1.0 and +1.25 ns.
- **What trials have reached.**
  - No legal trial of `dor13` or `dor15` reaches the equivalent of +1.0 ns
    at 13.33 ns.
  - The best legal slow-corner slacks are +0.796 ns at 13.33 ns (`dor13`
    #51; p024 has +0.756 ns) and +2.556 ns at 15 ns. The 15 ns one is +0.886 ns at 13.33 ns when
    re-timed (above), and it needed an exhaustive repair (p021, 6.28 h
    projected).
  - Among the eligible trials the best is +0.450 ns.
- **The SRAM delay is fixed.** Configuration knobs do not change the
  SRAM's 5.26 ns.
- **The delay buffers cannot be switched off.** LibreLane passes
  `CTS_DELAY_BUFFER_DERATE_PCT` as `-delay_buffer_derate`, but the OpenROAD
  build of the image stores that option without using it: in the 26 files
  of `src/cts/src` at `dcf36133`, `getDelayBufferDerate()` is called only by
  the Tcl getter `get_delay_buffer_derate`.
  The latency balancing itself (`insertionDelayEnabled`) is not a LibreLane
  variable.
- **The runtime knob costs slack here.** On the committed configuration,
  `PL_RESIZER_SETUP_REPAIR_TNS_PCT` 0 and 10 ended at −0.988 and +0.142 ns
  at the slow corner, against +0.756 ns with the full repair (experiment
  below; the slacks of 25 are pending).
- **Assessment.** The evidence does not support +1 ns at the slow corner
  from configuration knobs alone within the runtime bound, and it does not
  rule it out: TPE has not yet searched the space with the rule and the
  new knobs.

### RTL change for more margin (described, not implemented)

Four of the six endpoints below +1.0 ns are bits of a 32-bit counter whose
increment enable comes from the SRAM output through the instruction
decoder, and their paths end in its carry chain. Two behaviour-preserving
timing options of the generator remove exactly this structure
(`docs/timing-closure.md` section 4):

- `split_instruction_decode` gives the completed counter its own increment
  enable. The 256-way multiplexer between the SRAM output and the counter
  goes away.
- `keep_counter_increments` puts a `keep` attribute on the incremented
  value of the wide counters (PC, completed, blocked, repeat and wait
  counters). ABC can then no longer merge the enable into the carry chain.
  A Yosys experiment measured 32 gate levels from the enable to the flops
  without the attribute and 1 with it.

The design of record uses neither: every timing option is false in
`configs/instruction-sram-32.json`. The named variants `rstreg_timing` and
`cn_s2_timing` use them together with other reset styles. The optimizer
has run those only at 20 ns.

The change would set the two options for the design of record and
regenerate `src/protocol_emulator_core.v`. It would then repeat the checks
of `docs/timing-closure.md` section 6 and the repository's verification.
The new core sha256 starts new optimizer tracks. The expected effect on the
four counter endpoints is the removal of the 2.836 ns carry-chain tail
(an estimate; not measured). The next limits in p024's layout are
`core._2457` (+0.984 ns), the flip-flop path (+0.996 ns) and the SRAM paths
into `x`, `y` and unnamed registers (+1.02 to +1.25 ns), which these
options are not designed to change.

### Steps

1. **Relaunch the optimizer with v3** ([Operation](#operation)). `dor13`
   then gets 45% of the new trials. The revision-3 seeds run
   `PL_RESIZER_SETUP_REPAIR_TNS_PCT` 25 and 10 on the three best trials
   of `dor13` and `dor15` that exceed the runtime bound.
2. **Promote the best eligible `dor13` trial.** At first this is #14. The
   promotion must pass the four local gates. Its full-run projection (the
   leaderboard's "projected CI job h (full run)") must be at most 5.5 h,
   which means a normalized full-run flow of at most 9,048 s.
3. **Run the candidate through the official actions on a branch** (next
   subsection).
4. **Adopt it on `main`** ([Adopting a configuration](#adopting-a-configuration))
   with `CLOCK_PERIOD` 13.33 and `clock_hz` unchanged. The `gds` run of that
   commit is the result of record.
5. **If more slow-corner margin is wanted,** make the RTL change above.

### Official verification on a branch

`.github/workflows/gds.yaml` runs on every push to any branch (`on: push`).
It runs `gds`, then `precheck`, `gl_test` and `viewer`, and it has no
concurrency group, so a branch run does not cancel a run on `main`.
`gds_6x4.yaml` runs on pushes to `main` only, or through
`workflow_dispatch`: for a 6x4 candidate on a branch, run
`gh workflow run gds_6x4.yaml --ref <branch>`. Since `a15f6f2` the `equivalence` workflow
(`equiv.yaml`) runs `formal_eq` on the `tt_submission` artifact after every
`gds` run whose `gds` job succeeded (`workflow_run`, no branch filter), so a
branch run gets it too once `equiv.yaml` is on the default branch.

1. **Check the runtime first.** Push only a candidate whose full-run
   projection is at most 5.5 h. The model over-predicted each of the 18
   official jobs, and GitHub stops a job at 6 h.
2. **Make the branch commit.** Branch from `main`. Write the leaderboard's
   exact difference of the promoted trial into `src/config.json` (it
   includes `CLOCK_PERIOD` 13.33), add a `"//"` comment with the promotion
   id and its jobs, leave `info.yaml` unchanged, and push the branch.
3. **Compare with the local full run.** When `gds`, `precheck` and
   `gl_test` have passed, download the `tt_submission` artifact
   (`gh run download <run id> -n tt_submission`) and check:
   - `cmp` of its `stats/metrics.csv` with the full run's `out/metrics.csv`.
     They were byte-identical for p010, p014 and p018 (`docs/results.md`
     R19, R83, R85).
   - the gate-level netlist `tt_um_teslacoilerow_protocol_emulator.v`
     against the full run's `out/final/nl/` (sha256);
   - the setup and hold WS of every corner in `metrics.csv`;
   - the `gds` job's length against the projection;
   - the `equivalence` run of that `gds` run: "equivalent" with its
     self-test passed.
4. **Adopt on `main`** only after all of that holds, with the same
   `src/config.json`.

### Experiment: `PL_RESIZER_SETUP_REPAIR_TNS_PCT` on the committed configuration

The committed configuration at 13.33 ns (p024's knob set, margin 5.8 ns)
was run with `PL_RESIZER_SETUP_REPAIR_TNS_PCT` 0, 10 and 25. The runs went
through the optimizer's own code path (`tracks.Track.materialize()`,
`make_snapshot.py`, `trial_job.sh`) in a scratch directory, in fast mode
with `OPENROAD_THREADS` 8 on 8 CPUs. p024's trial (32 threads) and full
run (4 threads) have the same slacks, so the thread count is not expected
to change them. All three found the same 3,898 endpoints below the margin
(`RSZ-0094`) as p024.

| | p024's trial (unset = 100%) | 0 | 10 | 25 |
|---|---|---|---|---|
| Job, node | 24026489, node1620 | 24120814, node1407 | 24120815, node1408 | 24120816, node1414 |
| Endpoints repaired (`RSZ-0099`) | 3,898 | 1 | 389 | 974 |
| Post-CTS repair, seconds (normalized) | 12,082 (10,842) | 198 (119) | 5,120 (3,272) | 10,260 (6,265) |
| Resized / buffers / area | 5,593 / 1,253 / +7.6% | 166 / 73 / +0.2% | 1,145 / 358 / +1.6% | 1,955 / 544 / +2.8% |
| Resizer's worst slack after repair (placement parasitics) | +2.018 ns | +2.005 ns | +2.005 ns | +2.007 ns |
| Setup WS typ / fast / slow | +4.962 / +6.095 / +0.756 ns | +3.331 / +5.179 / **−0.988** ns | +4.055 / +5.323 / +0.142 ns | – |
| Hold WS typ / fast / slow | +0.368 / +0.163 / +0.736 ns | +0.358 / +0.146 / +0.720 ns | +0.359 / +0.148 / +0.722 ns | – |
| Utilization | 0.6723 | 0.6296 | 0.6374 | – |
| Projected official job (trial model) | 9.92 h | – | 5.56 h, over the bound (its rest ran at 8 threads, which the model's 32-thread constant does not cover) | – |

- **0 (only the worst endpoint):** the repair took 198 s, and the slow
  corner fails by 0.988 ns at 579 endpoints. The worst path is `ui_in[3]` →
  `_59994_`; register-to-register paths keep +0.089 ns.
- **10:** the repair drops to 3,272 normalized seconds (70% less), and the
  run is legal with route DRC 0 and antenna 0. But the slow corner falls
  to +0.142 ns, 0.614 ns below p024 and below the best eligible trial
  (+0.450 ns, `dor13` #14, which instead lowers the margin to 4.45 ns and
  repairs every endpoint below it).
- **25:** the repair took 6,265 normalized seconds, 42% less than the full
  repair. The job was in detailed routing at 18:27 cluster time, so its
  slacks are pending (its run directory is listed in
  `<work dir>/mhz75/manifest.json`).

In the resizer's own view the worst slack after repair is +2.005 ns with
0 and 10 against +2.018 ns with the full repair. After routing, the slow
corner differs by 1.744 ns between 0 and the full repair, so the endpoints
beyond the worst ones carry the post-route margin. On this configuration a
partial repair buys runtime with slow-corner slack, and a lower margin gave
more slack for the same runtime. Revision-3
seeds therefore use 25 and 10 only, and only on trials over the runtime
bound. The knob keeps 0 among its values for TPE.

## Objective

A run is ranked lexicographically (`tools/opt/objective.py`). Everything is
evaluated at the track's `CLOCK_PERIOD`: STA ran at that period.

1. **Legal.** All of the following must hold:
   - the flow completed with LibreLane exit 0 and post-route multi-corner STA;
   - route DRC 0, antenna violations 0 and critical disconnected pins 0;
   - no setup violation at the typ corner at the track's period (the gds
     action fails otherwise);
   - no hold violation at any corner;
   - no power-port violation. `tools/opt/checks.py` checks both:
     - the short VPWR/VGND Metal4 straps of `docs/drc-triage.md` section 6,
       on the `OpenROAD.GeneratePDN` DEF;
     - the precheck's power-port rule on the final LEF;
   - in a promoted full run, also LVS 0.
2. **Maximum of the minimum setup WS over the typ, fast and slow corners at
   the track's period.** The comparison uses 10 ps resolution.
3. **Minimum of the sum of the max-slew, max-cap and max-fan-out violation
   counts.** These were 290, 73 and 524 in v1's trial of `c118027`, and
   4, 0 and 3 in p010.
4. **Maximum of the typ-corner fmax estimate** `1000 / (T − typ WS)`. At a
   fixed period this only breaks ties of criteria 2 and 3.
5. **Minimum utilization.**

The leaderboard also reports the fmax estimate at every corner and for
register-to-register paths only (see the previous section).

TPE needs a single number. The scalar it maximizes is the minimum setup WS in
nanoseconds, adjusted as follows:

- minus 10⁻⁵ per slew/cap/fan-out violation;
- minus 10⁻³ × utilization;
- an illegal result is further lowered by 15 + 2·log10(1 + number of
  violations);
- a run without post-route STA gets −60. This covers a failed flow, a
  timeout, or a job that never produced a result.

A legal result therefore always scores above an illegal one. The leaderboard
uses the exact lexicographic order, not the scalar.

**Rule tracks (v3).** In `dor15`, `dor13` and `diet4_6x4` the projected official job
length ([Runtime and the 6-hour limit](#runtime-and-the-6-hour-limit)) is
inserted after criterion 2 (lower first), and the scalar of a trial whose
projection exceeds 5.5 h is lowered by 1 ns plus 1 ns per hour over. The
driver records the scalar it told the study and, for these tracks, the
objective's own value and the projection (`value_objective`, `ci_proj_s`
in the `trial_done` event).

### How the two power-port checks were validated

**LEF check.**

- On the CI artifact of `c118027`, the final LEF has 26 VPWR and 26 VGND
  ports and 0 violations.
- On the final LEF of sweep run 23749131_0 (before `FP_MACRO_HORIZONTAL_HALO`
  16.48), the check reports 8 violating ports. Each is 627.26 um from one
  edge. These are the 8 LEF errors that failed the precheck in
  `docs/drc-triage.md`.

**DEF check.** The drc-triage PDN-only runs give the same counts as the
drc-triage script:

- the `pdnexp/base` DEF: 8 short stripes;
- the `pdnexp/halo16p48` DEF: 0.

## Search space

Every knob is an ordinary LibreLane variable, or a floorplan file of
`floorplans/` (the `MACROS` instances and the file's `config` keys). A Tiny
Tapeout project may set these in `src/config.json` above the template's "DO
NOT CHANGE" line. The name, type, default and effect of each knob were
checked against the `librelane` package inside `librelane-3.1.0.dev3.sif`:
`steps/pyosys.py`, `steps/openroad.py`, `steps/common_variables.py`,
`config/flow.py`, `flows/classic.py` and `scripts/openroad/*.tcl`. The
"committed value" is the value in `src/config.json` at `25e331e`, or the
LibreLane/PDK default when `src/config.json` does not set the key. The table
summarizes `tools/opt/space.py`; `space.table_rows()` prints every knob with
the committed value of a track.

| Knob | LibreLane variable | Values searched | Committed value | Why |
|---|---|---|---|---|
| `floorplan` | `MACROS` instances | fp8_base, fp8_base_trk, fp8_wide, fp8_wide_trk, fp8_spread_trk | fp8_spread_trk | Macro placement sets the pin-access channels, the routing detours around the SRAMs and the reset/clock wire lengths across the die. |
| `halo_h_wide` | `FP_MACRO_HORIZONTAL_HALO` | 16.48, 10, 12.5, 20, 25 | (inactive) | Only for fp8_wide(_trk). Horizontal keep-out around each macro (see below). |
| `halo_h_spread` | `FP_MACRO_HORIZONTAL_HALO` | 16.48, 10, 12.5, 20 | 20 | Only for fp8_spread_trk; ≤ 22.32 keeps the first top macro clear of the I/O pins. |
| `FP_MACRO_VERTICAL_HALO` | same | 10, 5, 15, 20 | 10 | Keep-out above/below the macros, which is the pin-access channel of the SRAM signal pins (all on the edge facing the logic). |
| `density` | `PL_TARGET_DENSITY_PCT` | 50..72 | 61 | Global-placement target density: wire length versus routing congestion. |
| `SYNTH_STRATEGY` | same | AREA 0-3, DELAY 0-4 | DELAY 4 | ABC mapping script; DELAY scripts shorten logic depth at an area cost. AREA 0-2 and DELAY 0-3 use `CLOCK_PERIOD` as their `-D` target; AREA 3 and DELAY 4 do not. |
| `abc_fine_tune` | `SYNTH_ABC_BUFFERING` / `SYNTH_SIZING` | none, buffering, sizing | none | `construct_abc_script.py` applies buffering first when both are set, so the two are one choice. |
| `MAX_FANOUT_CONSTRAINT` | same | 10, 8, 6 | 8 | Fan-out limit for ABC buffering and repair_design. Only values ≤ 10 (the PDK value) are searched, because the value is also the SDC `set_max_fanout` limit that objective 3 counts against. |
| `PL_TIMING_DRIVEN` | same | false, true | true | Timing-driven global placement. |
| `PL_ROUTABILITY_DRIVEN` | same | true, false | true | Routability-driven global placement. |
| `gpl_pad` | `GPL_CELL_PADDING` | 0, 2, 4 | 0 (PDK) | Global-placement padding (sites, split over both sides). |
| `dpl_pad` | `DPL_CELL_PADDING` | 0, 2 | 0 (PDK) | Only if `gpl_pad` ≥ 2, so that it never exceeds `GPL_CELL_PADDING`. |
| `DESIGN_REPAIR_MAX_WIRE_LENGTH` | same | 0, 150, 300, 500, 800 um | 300 | Buffering of long wires; the reset and enable nets span the die. |
| `DESIGN_REPAIR_MAX_SLEW_PCT` | same | 10..50 step 5 | 35 | Slew margin of repair_design. |
| `DESIGN_REPAIR_MAX_CAP_PCT` | same | 10..50 step 5 | 50 | Capacitance margin of repair_design. |
| `RUN_POST_GRT_DESIGN_REPAIR` | same | false, true | false | repair_design with global-route parasitics (`OpenROAD.RepairDesignPostGRT`, marked experimental). |
| `PL_RESIZER_SETUP_SLACK_MARGIN` | same | 0..9 ns step 0.05 | 5.75 | Post-CTS setup-repair margin (see observation 1). v1 searched 0..6. |
| `pl_hold` | `PL_RESIZER_HOLD_SLACK_MARGIN` | 0.1, 0.05, 0.15, 0.2 | 0.15 | Post-CTS hold margin. In sweep job 23749131_12, margins 0/0 left a fast-corner hold violation. |
| `PL_RESIZER_SETUP_MAX_UTIL_PCT` | same | unset, 65, 75 | unset | Utilization cap of setup repair; bounds the area cost of large margins. |
| `PL_RESIZER_SETUP_REPAIR_TNS_PCT` (v3, 8x4 only) | same | unset, 50, 25, 10, 5, 0 | unset (100%) | Share of the endpoints below the margin that setup repair visits; bounds its runtime ([Timing-repair runtime knobs](#timing-repair-runtime-knobs-v3)). |
| `PL_RESIZER_SETUP_GATE_CLONING` (v3, 8x4 only) | same | true, false | true | false = `-skip_gate_cloning`; the clone move never committed in 559 trials. |
| `PL_RESIZER_SETUP_BUFFER_REMOVAL` (v3, 8x4 only) | same | true, false | true | false = `-skip_buffer_removal`; the unbuffer move never committed in 559 trials. |
| `RUN_POST_GRT_RESIZER_TIMING` | same | false, true | false | Timing repair with global-route parasitics (`OpenROAD.ResizerTimingPostGRT`, marked experimental). |
| `GRT_RESIZER_SETUP_SLACK_MARGIN` | same | 0..4 ns step 0.05 | (inactive; default 0.025) | Only if post-GRT timing repair is on; only that step reads it. |
| `grt_hold` | `GRT_RESIZER_HOLD_SLACK_MARGIN` | 0.05, 0.02, 0.1 | (inactive; 0.05) | Only if post-GRT timing repair is on; only that step reads it. |
| `GRT_RESIZER_SETUP_REPAIR_TNS_PCT` (v3, 8x4 only) | same | unset, 50, 25, 10, 5, 0 | (inactive; unset) | Only if post-GRT timing repair is on; as `PL_RESIZER_SETUP_REPAIR_TNS_PCT` for that step. |
| `CTS_SINK_CLUSTERING_SIZE` | same | unset, 10, 16, 25, 35 | 16 | Sinks per leaf cluster: clock latency and skew, which the input and reset paths see directly. |
| `CTS_SINK_CLUSTERING_MAX_DIAMETER` | same | unset, 30, 60, 100 um | unset | Leaf cluster diameter. |
| `CTS_MAX_SLEW` | same | fixed unset (up to driver job 24077569: unset, 0.4, 0.75, 1.2 ns) | unset (lib: 2.5 ns) | CTS characterization slew limit. Not sampled any more (next section). |
| `CTS_MAX_CAP` | same | unset, 0.1, 0.2 pF | 0.2 | CTS characterization capacitance limit (lib: 0.3 pF). |
| `CTS_CLK_MAX_WIRE_LENGTH` | same | 0, 250, 500 um | 0 | `repair_clock_nets` maximum wire length. |
| `CTS_OBSTRUCTION_AWARE` | same | unset, true | true | Keeps clock buffers off the macros. |
| `grt_adj_m2/m3/m4` | `GRT_LAYER_ADJUSTMENTS[1..3]` | 0, 0.1, 0.2, 0.3 (Metal4: ≤ 0.2) | 0.2 / 0 / 0.1 | Per-layer global-routing capacity reduction (see observation 2). Metal1 and TopMetal1 stay 0. |
| `GRT_MACRO_EXTENSION` | same | 0, 1 | 0 | GCells added around macro blockages. |

**6x4 space.** The `diet4_6x4` track searches the same knobs with three
restrictions (`space.defs("6x4")`, `tracks.Track.island_free_restrict()`):

- the floorplan is fixed to `fp6_tworow_trk_top30_edgeobs`, the placement of
  `variants6x4/config.overlay.json`, including its `FP_OBSTRUCTIONS` box;
- the horizontal halo is fixed to 16.48;
- `FP_MACRO_VERTICAL_HALO` is limited to the island-free values 10 and 5
  ([Macro halo and short power straps](#macro-halo-and-short-power-straps));
- the four v3 timing-repair runtime knobs are not in the 6x4 space: every
  6x4 knob set leaves their keys at the LibreLane default.

### Timing-repair runtime knobs (v3)

The v3 tools add four knobs, all in the 8x4 space only (`"spaces"` in
`space.py`), so the 6x4 track and its overlay are unchanged. Each is a
variable of `OpenROAD.ResizerTimingPostCTS` or
`OpenROAD.ResizerTimingPostGRT` (`steps/openroad.py`). Its script,
`scripts/openroad/rsz_timing_postcts.tcl` or `rsz_timing_postgrt.tcl`,
passes it to `repair_timing` of OpenROAD `dcf36133`
([Why the repair takes that long](#why-the-repair-takes-that-long)).

| Knob | Values searched | Unset | `repair_timing` option | Why |
|---|---|---|---|---|
| `PL_RESIZER_SETUP_REPAIR_TNS_PCT` | unset, 50, 25, 10, 5, 0 | unset (100%) | `-repair_tns` | The share of the endpoints below the setup margin that the repair visits, worst first. With 0 only the worst endpoint is visited (OpenROAD always repairs at least one). This bounds the repair loop. |
| `PL_RESIZER_SETUP_GATE_CLONING` | true, false | true | `-skip_gate_cloning` when false | No clone move committed in the 559 trials with a post-CTS repair log (no `RSZ-0049` message). The move is still evaluated in every pass that reaches it. |
| `PL_RESIZER_SETUP_BUFFER_REMOVAL` | true, false | true | `-skip_buffer_removal` when false | No unbuffer move committed in those 559 trials (no `RSZ-0059`). |
| `GRT_RESIZER_SETUP_REPAIR_TNS_PCT` | unset, 50, 25, 10, 5, 0 | unset | `-repair_tns` of the post-GRT repair | Only when `RUN_POST_GRT_RESIZER_TIMING` is true. |

**Considered and not added.**

- **`PL_RESIZER_SETUP_MAX_BUFFER_PCT` and
  `GRT_RESIZER_SETUP_MAX_BUFFER_PCT`.** LibreLane passes
  `-max_buffer_percent` to the setup call too. In OpenROAD `dcf36133` only
  `rsz::repair_hold` takes it (`Resizer.tcl`), so it has no effect on setup
  repair.
- **`PL_RESIZER_SETUP_BUFFERING`** (`-skip_buffering`). Buffer moves
  committed in 412 of the 559 trials (`RSZ-0040`). Switching it off would
  change the result, not only the runtime.
- **`-max_passes`, `-max_iterations`, `-max_repairs_per_pass`,
  `-skip_last_gasp`, `-skip_pin_swap` and `-sequence`.** These are options
  of `repair_timing` that `rsz_timing_postcts.tcl` does not pass. No
  configuration variable of LibreLane 3.1.0.dev3 reaches them.
- **Already searched:** `RUN_POST_GRT_RESIZER_TIMING`,
  `GRT_RESIZER_SETUP_SLACK_MARGIN` and `PL_RESIZER_SETUP_MAX_UTIL_PCT`.
  Once the utilization cap is reached, `overMaxArea()` ends each remaining
  endpoint after one pass.
- **`CTS_DELAY_BUFFER_DERATE_PCT`.** Not used by the OpenROAD build
  ([The 75 MHz plan](#the-75-mhz-plan)).

**Identity of stored trials.**

- A stored knob set has no value for the new knobs, and its run used their
  unset values. `space.canonical()` leaves a new knob out while it has its
  unset value, so the key of every stored trial is unchanged. This was
  checked on the 651 stored trials of the 12 current tracks (store of
  2026-09-27 18:20 cluster time).
- A stored knob set from before v3 is completed with the new knobs at their
  unset values, the values its run used (`space.upgrade()`), not with the
  committed values. After an adoption that sets one of them, a stored
  trial therefore still reproduces its own run, and `test_opt.py` checks
  this on a copy of the tree with `PL_RESIZER_SETUP_REPAIR_TNS_PCT` 10
  committed and stated `null` in the 6x4 overlay.
- The track ids are unchanged too, because `src/config.json` sets none of
  the four keys.
- The 6x4 overlay states the 33 keys of the 6x4 space.
  `test_opt.py` fails if `src/config.json` sets one of the four 8x4-only
  keys without the overlay stating it.

### `CTS_MAX_SLEW` is fixed unset

In the tools after driver job 24077569, `CTS_MAX_SLEW` is no longer sampled
(`space.FIXED`): every new knob set leaves it unset, so CTS characterization
uses the library's slew limit (2.5 ns).

**Why.** p026 failed 8 gate-level tests because the clock pins of its SRAM
macros are several cells deeper than those of the flip-flops that drive
them, which makes the zero-delay simulation race
([The p026 case](#the-p026-case)).

- p025 and p026 are the only promotions that set `CTS_MAX_SLEW` (0.75 and
  1.2 ns). Both also set `CTS_CLK_MAX_WIRE_LENGTH` 250.
- In both, the SRAM clock pins are 15 to 18 cells from the `clk` port and
  the flip-flop clock pins 11 to 13. In the 24 other promotions with a
  netlist, the deepest SRAM clock pin is 1 to 5 cells deeper than the
  shallowest flip-flop clock pin; in p025 and p026 it is 7 (clock-depth
  survey `gleq` job 24086842, reproduced by `tools/opt/clockdepth.py` in
  job 24092904; [Clock depth warning](#clock-depth-warning)).
- The CTS logs (`gleq` job 24087265: p014, p017, p018, p020, p022, p023,
  p025, p026) show where the depth comes from. With
  `configure_cts_characterization -max_slew` the register tree has 6 to 7
  buffers (3 to 5 in the other six), and latency balancing adds 11 and 12
  delay buffers (5 or 6 in the other six). The wire-length repair of
  `CTS_CLK_MAX_WIRE_LENGTH` 250 inserted 29 and 28 buffers (`RSZ-0048`) in
  p025 and p026, and 43 and 40 in p022 and p023, which have the same
  setting without `CTS_MAX_SLEW`.

**Caveat: two samples.** The evidence is two promotions, and one of them
(p025) passed the gate-level tests with the same clock depths as p026.
p022 and p023 set `CTS_CLK_MAX_WIRE_LENGTH` 250 without `CTS_MAX_SLEW` and
passed; their SRAM clock pins are at most 1 and 5 cells deeper than the
shallowest flip-flop clock pin. So the data name `CTS_MAX_SLEW` as the knob
that deepened the register tree in both cases; they do not show that every
value of it fails, or that no other knob can do the same.
`CTS_CLK_MAX_WIRE_LENGTH` stays in the space. The clock-depth warning
flags the same condition whatever knob causes it.

**What changes in the driver.**

- `space.suggest()` does not sample a fixed knob, and `space.fix()` sets it
  unset in every new knob set: sampled trials, seeds and transfers. A seed
  or transfer that was queued with `CTS_MAX_SLEW` set runs with it unset.
- The knob's definition keeps its four values. optuna refuses a changed
  categorical choice list for a parameter name that earlier trials of a
  study used, and a stored knob set must still reproduce the run it
  describes (`test_opt.py` checks both).
- A finished trial with `CTS_MAX_SLEW` set is **outside the current search
  space** (`space.outside()`). The driver does not promote it, seed or
  transfer it, and its promotions do not count toward the bar that a new
  best must clear ([Promotion](#promotion)).
- The leaderboard's best legal configuration of a track is the best one
  inside the space. When trials outside it rank higher, the section names
  the best of them; the top-10 table marks them "outside the current
  space".
- The committed configurations leave `CTS_MAX_SLEW` unset, so every
  track's baseline is inside the space (`test_opt.py` checks the design of
  record and the 6x4 build).

Trials outside the space, from the store of 2026-09-27 06:37 cluster time
(2,057 events). The first column counts every trial with a knob set, the
second the finished legal trials without duplicates:

| Track | Trials with `CTS_MAX_SLEW` set (0.4 / 0.75 / 1.2 ns) | Legal trials: outside / all | Best legal trial | Best legal trial inside the space |
|---|---|---|---|---|
| `dor` | 15 (2 / 11 / 2) | 10 / 180 | #175, +5.533 ns | the same |
| `dor15` | 15 (8 / 4 / 3) | 4 / 49 | #13, +2.556 ns | the same |
| `dor13` | 6 (3 / 3 / 0) | 3 / 20 | #1, +0.756 ns | the same |
| `diet4_6x4` | 17 (1 / 9 / 7) | 11 / 35 | #43 (p026's trial, 1.2 ns), +5.784 ns | #41, +4.894 ns (fourth overall) |

For `diet4_6x4`, the best legal trial inside the space, #41 (job
24073745), is 0.137 ns above the best promoted trial inside the space
(#25, p023, +4.757 ns). This is more than the 0.05 ns promotion threshold,
so the next driver promotes it. The dry runs of these tools planned it as
p028 (jobs 24094067 and 24096961, [Campaign record](#v2-from-2026-09-26-1411)).

### Macro halo and short power straps

The halo values follow the short-strap rule of `docs/drc-triage.md` section
6. pdngen adds a short strap when a standard-cell row segment between
macros is not crossed by a lattice stripe.

- **`fp8_base` and `fp8_base_trk`.** The paired macros are 32.96 um apart,
  so the halo stays exactly 16.48. At that value the halos meet and the gap
  holds no rows. A larger value fails `macros/check_macro_floorplan.py`
  ("closer than two halos").
- **`fp8_wide(_trk)`.** The gaps are 100.4 um. A lattice stripe pair runs
  through the middle of every gap, at macro x + 286.99, so any halo below
  about 50 um leaves every row segment crossed.
- **`fp8_spread_trk`.** The gaps are at least 167.8 um, with two stripes
  per gap. The halo is limited by the I/O pin clearance of the first top
  macro.
- **`fp6_tworow_trk_top30_edgeobs` (6x4).**
  - The pair gaps are 32.96 um again, so the halo is exactly 16.48.
  - The stripe-free gap between the top row's last macro and the right core
    edge is covered by the floorplan's `FP_OBSTRUCTIONS` box
    (`docs/6x4.md` section 2). That box starts at the top row's y minus the
    default 10 um vertical halo.
  - `variants6x4/row_islands.py` finds, with halo 16.48, 0 island row
    segments for vertical halos 5 and 10. It finds 1 for 15 and 3 for 20,
    each of which would get a short strap.

**Checks on every trial.**

- For every 8x4 floorplan and halo combination of the table,
  `row_islands.py` predicts 0 islands at every vertical halo.
- Before any LibreLane run, the driver runs `row_islands.py` on every
  snapshot's `config.json` and rejects a snapshot with an island.
- `make_snapshot.py` runs the floorplan checker on every snapshot and
  refuses a point that fails it.
- Every trial is still checked on its own PDN DEF and final LEF (Objective,
  item 1), and every promoted configuration passes through the precheck.

### Never searched

- **Kept as in `src/config.json` and the TT template:** die size (tiles,
  except that the 6x4 track uses the 6x4 die), the PDN keys (`FP_PDN_*`,
  `src/sram_pdn_cfg.tcl`), pin placement (`FP_IO_*`, the DEF template),
  `RT_MAX_LAYER`, and every key below "DO NOT CHANGE".
- **`CLOCK_PERIOD`** is a property of a track (20, 15 or 13.33 ns), not a
  knob.
- **Every timing-constraint variable:** `IO_DELAY_CONSTRAINT`,
  `CLOCK_UNCERTAINTY_CONSTRAINT`, `CLOCK_TRANSITION_CONSTRAINT`,
  `OUTPUT_CAP_LOAD`, `MAX_TRANSITION_CONSTRAINT`,
  `MAX_CAPACITANCE_CONSTRAINT`, `TIME_DERATING_CONSTRAINT` and the SDC
  files. Changing them would change what STA measures, not the design.
- **`DRT_OPT_ITERS` (retired in v1):** it was searched over 64 and 80. Both
  trials with 80 stopped in `OpenROAD.DetailedRouting` with `drt.tcl, 107
  Wrong number of arguments :utl::warn` from the OpenROAD build in the SIF
  (jobs 23995145 and 23995228). Every trial with 64 got past that step. The
  knob is fixed at 64, the LibreLane default (`space.RETIRED`).
- **`OPENROAD_THREADS`:** trial runs use 32 threads (a local-only deviation
  recorded in `params.json`). Promoted runs use the committed value, 4.

## Method

### Sampler

The sampler is optuna 5.0.0 `TPESampler`, one study per track:

- `multivariate=True` and `group=True` (the space is conditional);
- `constant_liar=True` (up to about 21 trials run at once);
- `n_startup_trials=12` and `n_ei_candidates=48`.

The study state is an optuna journal file per track
(`optimizer/studies/<track>.journal`). optuna is installed in a private venv
(`optimizer/venv`). Imported trials count as completed trials of the study,
so `dor` and the variant tracks go straight to TPE sampling.

### Allocation

The driver shares new trials between the tracks that may receive one:

- **Weights** (`WEIGHTS` in `driver.py`): in v3 `dor13` 0.45, `dor15`
  0.15, `dor` 0.05, `diet4_6x4` 0.20, and 0.15 for the variant tracks
  together (from `f511c97`: `dor15` 0.35, `dor13` 0.15, `dor` 0.15; until
  `f511c97`: `dor` 0.35, `dor15` 0.22, `dor13` 0.08; the others unchanged).
  A variant track gets 0.15 divided by the number of variant tracks still
  receiving trials (0.021 each for 7).
- **What counts:** a track's count is the number of its trials submitted
  to LibreLane. Imported, duplicate and rejected trials use no CPUs and do
  not count. In v3 only trials created since the first driver start with
  the current weights count (`Driver.weights_epoch()`), so new weights
  apply to the trials that follow them. Counting since the launch, `dor13`
  would have received every new trial until its count caught up with its
  new weight (dry run 24120704 planned 42 trials, all `dor13`; the later
  dry runs, with the epoch, planned the mix of [Campaign record](#v3-tools-of-the-runtime-rule-not-yet-running)).
- **Start:** a track with no submitted trial goes first, in weight order.
  So every track gets a trial in the first loop.
- **After that:** the next trial goes to the track with the smallest
  (count + 1) / weight.
- **Tracks that stop receiving trials** (retired, or a variant track at 200
  trials) drop out. The others keep their proportions.
- **Duplicates and rejections:** a track that yields 4 of them in one loop
  waits for the next loop.

### Retirement

A variant track is **retired**, which means it receives no new trials, when:

- it has at least 40 finished trials (imported ones included); and
- either:
  - it has no legal trial; or
  - its best legal minimum setup WS is at least 0.25 ns below the best of
    `dor` (the design of record at 20 ns), and its utilization is not 0.05
    or more below the utilization of that best.

The reasoning:

- Every variant changes the design's contract (`docs/variants.md`
  section 4). It is worth keeping only if it buys margin or area over the
  design of record.
- 0.25 ns is 25 times the 10 ps resolution of the ranking and 5 times the
  0.05 ns promotion threshold.
- 0.05 utilization separates the `diet` cores (0.40 to 0.45 at their best)
  from the rest (0.59 to 0.66).
- The comparison is not between equal search efforts. Until `f511c97`
  `dor` got 0.35 of the new trials and each of the 7 variant tracks
  0.15 / 7, about a sixteenth of `dor`'s share (since `f511c97` `dor` gets
  0.15, seven times a variant track's share), and `dor` also imported 134
  v1 trials. At
  18:17 `dor` had 159 finished trials and each variant track 12 to 14. A
  variant track is judged after 40 trials against the best of `dor` after
  many more, so a variant that would improve with more trials can be
  retired. The 0.25 ns margin is defined from the ranking resolution and
  the promotion threshold (above), not from the gain that more trials give,
  so it does not correct for this.

Retirement is recorded as a `track_retire` event with the numbers that
triggered it, and it is permanent. It applies only to variant tracks: the
design of record, the frequency tracks and the 6x4 fallback are never
retired.

At the v2 launch no variant track had 40 finished trials (they had 10 to
13). The comparison is always with the current best of `dor`.

- Against v1's best (3.00 ns), `cn`, `cn_s2`, `cn_s2_timing` and `rstreg`
  (best min WS 2.40 to 2.51 ns, utilization within 0.05) met the margin
  condition. `diet2` and `diet4` (utilization 0.40 and 0.45) and
  `rstreg_timing` (3.53 ns) did not.
- The best of `dor` rose to 4.28 ns by 17:00 (trial #150) and to 4.835 ns
  by 18:17 (trial #154, job 24036312). Against that, every variant track
  meets the margin condition unless it improves; the two `diet` tracks are
  exempt through the utilization condition.
- The rule acts only when a track reaches 40 finished trials. At 18:17 the
  variant tracks had 12 to 14, and none was retired.

### One trial

1. **Snapshot.** `scripts/sweep/make_snapshot.py` (`build()`, imported from
   the frozen tree) writes the snapshot. It contains the committed files,
   the track's core, die and period, the knob values as `make_snapshot`
   parameters (floorplan, density, hold margins) and LibreLane overrides
   (`space.materialize()`), and the tt-support-tools config merge.
   `info.yaml` gets the track's `tiles` and `clock_hz`.
   - A knob set identical to one already run, running or imported in the
     same track is recorded as a duplicate and reuses that result. No
     LibreLane job runs.
   - `row_islands.py` runs on the snapshot. A snapshot with an island is
     rejected.
2. **Slurm job.** One job per trial: `tools/opt/trial_job.sh`, 32 CPUs,
   64 GB, 6 h limit (5 h in `dor15`, `dor13` and `diet4_6x4` in v3,
   [Runtime and the 6-hour limit](#runtime-and-the-6-hour-limit)), on
   `mit_preemptable,mit_normal` with `--requeue` and
   `--signal=B:USR1@900`. It runs `scripts/sweep/run_one.sh` in fast mode.
   - Fast mode skips `Magic.DRC`, `Magic.SpiceExtraction`, the
     illegal-overlap check and LVS.
   - Post-route STA at all corners, antenna, the stream-outs and
     `Magic.WriteLEF` still run.
   - The wrapper forwards the USR1 time-limit warning to `run_one.sh`, which
     records a `timeout` result.
   - Around the run, the wrapper also:
     - runs the PDN DEF check as soon as `OpenROAD.GeneratePDN` finishes
       (`run_one.sh` deletes the DEF at the end);
     - runs `postprocess.py` afterwards: the LEF check, and the worst setup
       path start and end points per corner;
     - finally writes `opt_done.json`.
3. **Result.** The driver reads `result.json` and `opt_post.json` and
   computes legality, the rank key and the scalar at the track's period. It
   appends a `trial_done` event to the store, tells the study, and rewrites
   the leaderboard.

### Promotion

A promotion runs the same knob values in full mode, with the track's die and
period, `OPENROAD_THREADS` 4 (the committed value), 8 CPUs and an 11 h limit.

If that run is legal, including LVS 0, three jobs follow in parallel:

- **Precheck.** `tools/opt/precheck_job.sh` runs the precheck reproduction
  of `docs/drc-triage.md` on a submission-like directory: the snapshot's
  `info.yaml` (with the track's `tiles` and `clock_hz`) and the GDS, LEF and
  unpowered netlist, gunzipped from the run's `final/`. That reproduction is
  tt-support-tools `d66cf179e` `precheck.py`, unmodified, with KLayout from
  the SIF.
- **Gate-level tests.** `tools/opt/gl_job.sh` runs `make GATES=yes` in a
  copy of the frozen `test/`. It uses the final netlist, Icarus 13.0,
  cocotb 2.0.1 and the flow's PDK, with `PE_VARIANT` of the track: `base`
  for the design-of-record tracks, `diet4` for `diet4_6x4`, and the variant
  name for a variant track. A variant without `configs/variants/<name>.json`
  in the frozen tree is reported as not run.
- **Equivalence check** (in the tools after driver job 24077569).
  `tools/opt/eq_job.sh` (job name `pe-v2-optimizer-peq-<pid>`) runs
  `formal_eq/eq_check.py` of the frozen tree. It checks the
  final netlist against the RTL it was built from, on 2 CPUs with 8 GB and
  a 90-minute limit. See [Equivalence check](#equivalence-check).

**Verdict** (`tools/opt/gates.py`, `promotion_verdict()`): PASS only if the
full run is legal and the precheck, the gate-level tests and the
equivalence check all pass. In the rule tracks (`dor15`, `dor13`,
`diet4_6x4`, v3) the full run's projected official job must also be at
most 5.5 h; otherwise the verdict is "PASS (over CI time bound: …)", which
is not eligible for adoption ([Runtime and the 6-hour limit](#runtime-and-the-6-hour-limit)).

- A stage that fails decides FAIL at once; the leaderboard names the stage
  and the reason, and lists the stages still pending.
- While a stage is missing or running, the verdict is "sign-off in
  progress".
- A variant track without a reference-model configuration gets
  INCOMPLETE. Until these tools that case read "PASS (gate-level tests not
  run)"; no promotion had it.
- **Backfill.** Promotions from before the equivalence stage have no such
  stage. At its first start the new driver submits it for every promotion
  whose full run is legal. The dry runs of these tools submitted 26: p001
  to p027 without p012, whose full run failed (jobs 24094067 and
  24096961). Of these, 20 are 8x4 netlists (`base`) and 6 are 6x4 netlists
  (`diet4`). Until each of
  them finishes, its verdict reads "sign-off in progress (equivalence)",
  and p026 reads FAIL at once because of its gate-level result. The
  equivalence stage does not count toward the 5 promotions in flight, so
  the backfill does not hold up new promotions.

What gets promoted (at most 5 promotions in flight, not counting
equivalence stages; only trials inside the current search space, see
[`CTS_MAX_SLEW` is fixed unset](#cts_max_slew-is-fixed-unset); in v3, in
`dor15`, `dor13` and `diet4_6x4`, only trials whose projected official job
is at most 5.5 h, see [Runtime and the 6-hour limit](#runtime-and-the-6-hour-limit)):

- **Control:** the committed configuration of `dor` and of
  `diet4_6x4` goes through the whole pipeline once. For `dor` on the trees
  up to `24b807b` this was already done: the committed configuration was
  v1's p010, which is PASS. On tree `f511c97` the committed configuration of
  `dor` is p018's knob set at 20 ns; its control is p027 (full run job
  24077610, submitted 2026-09-27 01:57 cluster time). For `diet4_6x4` the
  control was p012, the 6x4 build of the frozen tree `131e793`, which is
  FAIL (route DRC 69). When this was first written the driver still ran on
  that tree, so its 6x4 "committed configuration" was the pre-`4bd30c8`
  overlay; since the relaunch on `24b807b` (driver job 24059198) it is p014
  (the leaderboard's committed 6x4 configuration is trial `#8`, p014's
  trial), which is already PASS.
- **Best of a track:** the best legal trial of a track is promoted when
  its minimum WS is at least 0.05 ns above the best promoted trial of that
  track. Both are taken inside the current search space (since the tools
  after driver job 24077569). Promotions of the trials an imported trial
  stands for count here. In `dor15`, `dor13` and `diet4_6x4` (v3) both are also taken
  among the eligible ones: a promotion whose projection exceeds 5.5 h (from
  its full run once finished) does not set the bar.
  A track first needs this many finished trials: `dor` 0, `dor15`/`dor13`
  10, `diet4_6x4` 10, variant 20.
- **Periodic:** for `dor`, `dor15` and `dor13`, once a track has at least
  50 × (its promotions + 1) trials of its own (imported ones not counted),
  the best not-yet-promoted trial among its top three is promoted. The
  threshold grows with every promotion of the track, including the
  best-of-track ones. For `dor` the ten v1 promotions count, so it started
  at 550 own trials; with p011, p013, p015 and p016 (14 promotions) it was
  750 at 18:17, against 29 own trials. For `dor15`, with p018, it was 100
  (200 after p019 and p021).

### Equivalence check

The stage runs [`formal_eq/eq_check.py`](../formal_eq/README.md), the
repository's formal RTL-vs-netlist check. It asks whether the final netlist
and the RTL produce the same outputs, cycle by cycle from reset, for every
input sequence. [`docs/equivalence.md`](equivalence.md) gives the method,
the controls that show the check can fail, what it does not cover, and the
correction of the first recipe (its section 3).

**Inputs** (`Driver.eq_inputs()`, then `tools/opt/eq_job.sh`):

- **Gold** is `src/project.v` and the track's core. The core is the copy
  the full run was built from, as its `params.json` records it:
  `src/protocol_emulator_core.v` for `dor`, `dor15` and `dor13`,
  `variants6x4/protocol_emulator_core.v` for `diet4_6x4`, the published
  core for a variant track. `src/project.v` is the one in the frozen tree
  the full run was built from (also in `params.json`). It is
  byte-identical in all the frozen trees of the promotions (sha256
  `2aacfd24…`).
- **Variant.** It is the track's, as for the gate-level tests: `base`,
  `diet4`, or the variant's name. For `base` and `diet4`, `formal_eq`
  requires the core's first line to name the variant's configuration.
- **Gate** is the unpowered final netlist of the promotion's `sub/`.
- **PDK.** The cell functions come from the typical-corner standard-cell
  liberty under the PDK root that LibreLane used for the full run
  (`PDK_ROOT` in its `out/resolved.json`, else `params.json`, else the
  sweep harness's default). It is passed as `--pdk-root`. For all 26
  promotions with a legal full run that is the flow mirror's `pdk/`,
  IHP-Open-PDK `2bbec755`.
- **Checker.** It is `formal_eq/eq_check.py` of the driver's frozen tree,
  which `launch.sh` exports from the commit together with `src/`.
  `$PE_EQ_CHECK` overrides it. `launch.sh` refuses a commit without
  `formal_eq/eq_check.py`.
- **Tools.** yosys and yosys-abc come from `$OSS_CAD_SUITE/bin`.
  `launch.sh` checks that they are there, and the driver passes
  `OSS_CAD_SUITE` to its jobs. ABC runs on one thread.

The command is:

```
eq_check.py check --netlist sub/<top>.v --variant <variant> --core <core copy>
    --top <tree>/src/project.v --pdk-root <PDK root of the run>
    --timeout 3600 --build-timeout 1200 --jobs 1 --out <node-local directory>
```

The stage does not use `--selftest`. The self-test's two netlist mutants
and nine recipe controls ran with the checks of the official netlists
(docs/equivalence.md, section 1). On an 8x4 netlist one mutant took ABC
588 s.

The job has 2 CPUs, 8 GB and 90 minutes (job name
`pe-v2-optimizer-peq-<pid>`). `formal_eq` works in a node-local directory.
The promotion keeps `eq/result.json` (the stage's verdict) and
`eq/formal_eq/`: `formal_eq`'s own `result.json`, the yosys scripts,
`abc.log` and the gzipped yosys logs.

**Fail-closed verdict** (`tools/opt/gates.py`: `parse_eq()`,
`check_inputs()`, `eq_record()`):

- **Pass.** The stage passes only if all of the following hold:
  - `formal_eq`'s `result.json` has schema `pe-formal-eq/1`, `verdict`
    `equivalent`, `exit_code` 0 and `pass` true;
  - the checker process exited 0;
  - `formal_eq` recorded, for the netlist (source and copy), the core and
    `src/project.v`, the sha256 of the files the job gave it;
  - it used the PDK root of the full run and the track's variant.
- **Fail.** Everything else fails, with the reason in `eq/result.json`:
  - not equivalent, with the frame in which the miter output was
    asserted;
  - undecided;
  - a time limit;
  - an error, with `formal_eq`'s reason;
  - no `result.json`, or one that is not valid JSON;
  - a schema mismatch;
  - fields that contradict each other;
  - an input mismatch.
- **Provenance.** The stage's `result.json` also records the following:
  - `formal_eq`'s `tool_files`: the sha256 of `eq_check.py`, `wrap.v`, the
    SRAM blackbox and the nine controls;
  - the yosys version, the yosys-abc path, and the version line of
    `yosys-abc -c version`;
  - the PDK revision and the liberty's sha256;
  - the miter AIG's sha256, the host and the CPU.
- **Limits.** ABC gets 3,600 s, each yosys step 1,200 s, and the whole
  checker 4,800 s. ABC took 62 to 71 s on the 6x4 netlists and 349 to
  1,014 s on the 8x4 netlists (tables below). The longest was p018 through
  the stage on an Intel Xeon Gold 6230 node (job 24096976), and the limit
  is more than three times that.
- **Retries** (`gates.eq_should_retry()`, used by `Driver.poll_promos()`).
  A time limit is resubmitted, up to twice, because the time depends on the
  node's load. That covers ABC's limit, a yosys step's, and the job's own.
  The third time limit is FAIL. "Not equivalent", undecided and errors are
  final at once. As for every sign-off stage, a job that ended without
  writing a `result.json` at all (killed by Slurm, or a failed node) is
  also resubmitted, up to the same three jobs.
- **Why undecided fails.** mutB is p014's netlist with one random `nand2`
  changed to `nor2`. `dprove` stopped undecided after 1,708 s (job
  24093081), and the gate-level suite passes it (docs/equivalence.md,
  section 4). A gate that passed on undecided would accept such a netlist.

**The first checker was not a proof.** Until these tools the stage called
`eq_check.sh` of the `gleq/` investigation in the cluster work directory.
Its miter used `miter -equiv -ignore_gold_x` followed by `setundef -zero`.
That turns the x test of each compared bit into "gold bit = 0", so the
miter compared a bit only where the RTL value was 1 (docs/equivalence.md,
section 3; `docs/results.md` R91). Ablation job 24094085 ran the recipe
controls through that recipe. It called two different designs equivalent:
c1 (gate `a|b` against gold `a`) and c4 (a difference after 13 cycles).

- **Its "equivalent" results are superseded.** The earlier version of this
  section quoted these:
  - p014: `gleq` jobs 24087083 and 24088368; `eq_job.sh` 24092905; driver
    path 24093377 and 24094084;
  - p018: 24087778 and 24092982;
  - p025: 24087438;
  - p026: 24087083, 24088368, 24092983 and 24093380.

  They showed only that no compared bit is ever 1 in the RTL and 0 in the
  netlist. They were not proofs of equivalence, and the `formal_eq`
  results below supersede them.
- **Its "not equivalent" results stand.** The counterexamples for p014_mutA
  and p026_mutC were genuine differences.
- **Its timeout runs tested the retry path, not the proof.** Those are
  jobs 24093661, 24093683 and 24093709.
- **These tools do not use `gleq`.**

**Results with `formal_eq`** (docs/equivalence.md, section 1; job records in
`<work dir>/eq-repo/manifest.json`):

| Netlist | Verdict | ABC | Job |
|---|---|---|---|
| Official 6x4 build: `gds_6x4` run 36298635404 on `d76f1cc`; byte-identical to p014's netlist (`f085554e…`) | equivalent, self-test passed | 67 s | 24093883 |
| Official 8x4 build at 15 ns: `gds` run 36298635436 on `d76f1cc`; byte-identical to the final netlist of p018's full run (`09841e31…`) | equivalent, self-test passed | 692 s | 24093884 |
| Official 8x4 build at 20 ns: `gds` run 36257636798 on `131e793` (p010's configuration) | equivalent, self-test passed | 349 s | 24093885 |
| p018, its local full run | equivalent | 446 s | 24093888 |
| p026 | equivalent | 71 s | 24093886 |
| p025 | equivalent | 69 s | 24093887 |

Each of these `result.json` files has `verdict` `equivalent`, `exit_code`
0 and `pass` true.

**The stage itself** was run with these tools (`af25eba8`). The runs used
copies of the campaign's store in scratch optimizer roots, a tree exported
from `e0b5223` (which contains `formal_eq/`) with the working tree's
`tools/opt`, and job names `pe-v3-eqwire-*`. Each went through
`Driver.submit_signoff()`, `eq_job.sh`, `formal_eq` and
`gates.py eq-collect`. Then `Driver.poll_promos()` recorded the result, and
`gates.promotion_verdict()` gave the promotion's verdict.

| Promotion | Stage result | ABC; job wall time | Promotion verdict | Job |
|---|---|---|---|---|
| p014 | equivalent; the miter AIG is byte-identical to that of job 24093883 (`b06f5eff…`) | 62 s; 101 s | PASS | 24096966 |
| p026 | equivalent; the miter AIG is byte-identical to that of job 24093886 (`2997de06…`) | 62 s; 87 s | FAIL (gate level: 8 of 102 failed), unchanged | 24096968 |
| p018 | equivalent; the miter AIG is byte-identical to that of jobs 24093888 and 24093884 (`df863faf…`) | 1,014 s on an Intel Xeon Gold 6230 node; 1,094 s | PASS | 24096976 |
| p014's run with `formal_eq`'s mutA netlist in `sub/` (`_32857_` `nand2_1` → `nor2_1`; a scratch promotion p901) | not equivalent, frame 8; final, not resubmitted | 0 s; 38 s | FAIL | 24096965 |
| p014 with an ABC limit of 5 s | time limit, resubmitted twice, then FAIL | 5 s each | FAIL | 24096967, 24096999, 24097014 |

The first attempt, with tools `0a19ce4b`, failed closed in every job:
24096857, 24096858, 24096859 and 24096874 each gave "error: formal_eq
wrote no result.json (checker exit 127)". Under Slurm, `$0` of a batch
script is a spooled copy, so `eq_job.sh` looked for the checker next to
that copy. It now finds the tree through `PE_OPT_HARNESS`, which the driver
exports. `test_opt.py` runs `eq_job.sh` from a copy in another directory to
catch this.

**What the check covers.** It checks that the netlist implements the RTL
cycle for cycle at the Tiny Tapeout outputs and at every SRAM macro input,
independently of simulation event order. It caught the mutants above. LVS
compares the layout with the netlist, and the precheck checks the layout's
rules; neither compares the netlist with the RTL.

**What it does not cover** (docs/equivalence.md, section 6):

- the SRAM macros themselves, which are cut points;
- the liberty cell functions against the cell layouts;
- timing, including zero-delay simulation races (STA and the gate-level
  tests cover those);
- power-up values other than 0 of the 2,048 register bits of the base core
  that no reset initialises;
- a bug in the yosys front end, which the flow also synthesizes with, so
  such a bug would affect both sides alike;
- ABC itself: no proof certificate is checked.

### The p026 case

p026 (`diet4_6x4` trial #43) has a legal full run with LVS 0 (job
24076184) and passed the precheck (job 24078116), but 8 gate-level tests
failed (job 24078117). The `gleq` investigation in the cluster work
directory found a race of the zero-delay simulation, and `formal_eq` showed
that the netlist has no functional difference from its RTL
(docs/equivalence.md, section 5).

- **Deterministic.** A rerun of the full suite on the same netlist failed
  the same 8 tests (job 24085496).
- **Only the SRAM clocks matter.** With every clock pin of the netlist
  rewired to the `clk` port, the suite passes (46 pass, 0 fail; job
  24085496; the clock tree is buffers only, so the logic is unchanged).
  With only the 8 SRAM `A_CLK` pins rewired it also passes (46 / 0), and
  with only the flip-flop clock pins rewired 42 tests fail (job 24086215).
- **Where.** A monitor that compares every flip-flop and SRAM input at the
  `clk` edge with its value at the sink's own clock edge found 3,762 SRAM
  race events in p026 and none in p014 or p025 (job 24086215), nor in p018
  (job 24088391). Every event is at `core.instruction_sram_e0_lo`; in all
  400 printed events the input that changed is `A_DIN[3]`. The first
  divergence from the RTL is in cycle 232, a read of that macro (0x0036
  instead of 0x003e, bit 3; job 24085806).
- **Mechanism.** `A_DIN[3]` comes from flip-flop `_37730_` (the host
  write-data shift register) through three cells. That flip-flop's clock
  pin is 12 cells from `clk`, and the macro's `A_CLK` 18. In a zero-delay
  simulation each cell costs an evaluation step, so the SRAM model
  (`always @(posedge ...)` reading `A_DIN`) samples the value the
  flip-flop launched at the same edge.
- **Real timing is the other way round.** In the post-route clock report
  of p026 (fast corner, job 24087092) the earliest clock arrival is at an
  SRAM clock pin (0.796 ns) and the latest at a flip-flop (1.478 ns), and
  STA meets hold at every corner (worst +0.114 ns, 0 violations).
- **The netlist is equivalent to its RTL.** `formal_eq` proved it in job
  24093886, and again in job 24096968, a test of the equivalence stage
  ([Equivalence check](#equivalence-check)). The `gleq` checker had also called it
  equivalent (jobs 24087083 and 24088368), but its recipe was unsound, so
  those results were not proofs.

**Consequence.** The Tiny Tapeout gl_test action runs the same zero-delay
Icarus simulation, so a layout like p026 would also fail there. It stays
FAIL. The optimizer now avoids the knob that produced it
([`CTS_MAX_SLEW` is fixed unset](#cts_max_slew-is-fixed-unset)) and flags
the condition on every run ([Clock depth warning](#clock-depth-warning)).
The gate-level test bench is unchanged: making it independent of SRAM
clock depth (for example by forcing the SRAM clocks to `clk` under
`GL_TEST`) is a repository decision outside the optimizer.

### Clock depth warning

`postprocess.py` runs `tools/opt/clockdepth.py` on the final netlist of
every trial and full run (`out/final/nl/`). It counts the cells between the
`clk` port and each flip-flop clock pin and each SRAM `A_CLK` pin, and
reports `sram_excess`: the deepest SRAM clock pin minus the shallowest
flip-flop clock pin.

- An excess of 6 or more is a warning. The leaderboard shows it as `WARN`
  in the "clock depth FF / SRAM" column of the trial and promotion tables.
  The driver writes it to its log and to the `promo_new` event when it
  promotes such a trial.
- It is not part of legality or the ranking, and it does not block a
  promotion: the gate-level tests decide.
- The equivalence job also records the clock depth of its netlist, so
  promotions whose full run predates `clockdepth.py` get the column from
  the backfill. Trials that finished before these tools show no value.

Calibration (job 24092904, the final netlists of all promotions that have
one; it reproduces the `gleq` survey of job 24086842):

| Promotions | Flip-flop clock depth | SRAM clock depth | `sram_excess` |
|---|---|---|---|
| p001 to p027 without p012 (no netlist), p022, p023, p025 and p026 | 4 to 8 | 7 to 9 | 1 to 4 |
| p022 (`CTS_CLK_MAX_WIRE_LENGTH` 250) | 12 to 15 | 10 to 13 | 1 |
| p023 (`CTS_CLK_MAX_WIRE_LENGTH` 250) | 8 to 13 | 10 to 13 | 5 |
| p025, p026 (`CTS_MAX_SLEW` 0.75 and 1.2 ns) | 11 to 13 | 15 to 18 | 7 |

The fast-mode run of p026's trial (#43, job 24075174) has the same depths
as p026's full run (excess 7), so the warning is available before a
promotion. `diet4_6x4` #41 (job 24073745), which the next driver promotes,
has flip-flop depths 9 to 13 and SRAM depths 10 to 13 (excess 4).

The threshold separates the two promotions with `CTS_MAX_SLEW` from the 24
others, and it rests on those two. It flags a risk, not a failure: p025 has
the same depths as p026 and passed. A finer static measure (per SRAM input:
launching flip-flop depth plus path cells, minus SRAM clock depth) gave −3
both for the pin that raced in p026 and for two pins of p025 that did not,
so it is not used.

### Scheduling limits, preemption and pruning

**Limits.**

- All `pe-v2-optimizer-*` jobs together stay at or below 700 CPUs in v3,
  queued and running, driver included (960 from tree `24b807b`, driver job
  24059198, to v3; 700 before). The partition's per-user cap is 1,024, and
  the rest is left for other workstreams. Every submission is charged
  against the CPUs free at the start of the loop.
- Submissions stop while this user has 400 or more jobs queued (the
  per-user limit is 448, shared by all workstreams).
- At most 24 submissions per loop.
- A failed `sbatch` backs off for 5 minutes.
- Sign-off jobs go first, then promotions, then trials.

**Preemption.**

- A preempted trial is requeued by Slurm and restarts from its snapshot.
- A job that ends without a result is resubmitted up to twice. The driver
  waits 10 minutes for the pool to show a late `result.json` first.

**Driver.**

- The driver is itself a job: 2 CPUs on `mit_preemptable`, 2-day limit,
  `--requeue`.
- It rebuilds its state from the store at every start. A trial that optuna
  holds as running with no store record is closed as failed. An import
  that reached the study but not the store is recorded.
- About 30 minutes before its time limit, it submits its successor with
  `--dependency=afterany` on itself and exits.
- A driver that finds another driver running exits at once.

**Pruning.**

- A finished run keeps `params.json`, `result.json`, `opt_*.json` and
  `out/` (metrics, logs tarball). Its `snap/` is deleted.
- `out/final/` (the gzipped GDS, netlists and SPEF) is kept only for the ten
  best legal trials of each track, the committed-configuration trials and
  promoted runs.
- Slurm logs are gzipped.
- The driver never prunes a directory outside its own optimizer root.

## Budget and stop conditions

The campaign stops starting new work at whichever comes first:

- **3,000 LibreLane runs** (trials plus promoted full runs, counted since
  v1's first launch; 227 had been submitted when v2 started). Trials stop 20
  runs earlier, to leave room for the last promotions.
- **2026-10-24 00:00** (cluster local time).
- **The file `$PE_WORK/optimizer/STOP` exists.**

On the date or the STOP file, trial jobs that have not started are
cancelled. Running jobs finish and are recorded. When nothing is in flight,
the driver writes the final leaderboard and `optimizer/FINISHED`, and does
not resubmit itself.

## Operation

```sh
export PE_WORK=<cluster work directory>
export OSS_CAD_SUITE=<OSS CAD Suite root>   # yosys and yosys-abc of the equivalence stage
python3 tools/opt/test_opt.py       # unit tests (standard library, no cluster)
srun -p mit_quicktest -c 4 --mem 8G -t 15 tools/opt/dry_run.sh $PE_WORK/<scratch dir> 2
                                    # dry run: copy of the store, 2 loops, nothing submitted
tools/opt/launch.sh                 # export HEAD (with formal_eq/) + tools/opt, point optimizer/current
                                    # at it, create the venv if missing, submit the driver (no-op if queued)
P=$PE_WORK/optimizer/venv/bin/python
$P $PE_WORK/optimizer/current/tools/opt/driver.py status   # per track: trials by state, imports, retirement
less $PE_WORK/optimizer/leaderboard.md                     # rewritten after every finished trial
```

- **Dry runs.** `dry_run.sh` works like `launch.sh`:
  - it exports HEAD with the working tree's `tools/opt` and copies the
    store;
  - it runs `driver.py once --dry-run` against the copy;
  - a dry driver records the job id `dry` for every submission and plans
    for the whole CPU cap;
  - it neither polls nor prunes run directories outside its own root, so
    the campaign's runs are untouched;
  - its snapshots, `driver.log` and `leaderboard.md` show what a real loop
    would do.

The files under `$PE_WORK/optimizer/`:

| Path | Content |
|---|---|
| `store/events.jsonl` | Append-only record of every track, trial, import, submission, result, retirement and promotion (`tools/opt/store.py`) |
| `leaderboard.md`, `leaderboard.csv` | One section per track (committed configuration, best legal configuration with its exact config difference, promotions, top trials), the earlier tracks, blockers; every trial with every metric |
| `manifest.json` | Every Slurm job id (drivers, trials, promotion stages) |
| `runs/<track>/<run id>/` | Sweep-format run directories (`params.json`, `result.json`, `opt_post.json`, `out/`) |
| `promotions/<pid>/` | `sub/` (submission-like directory), `precheck/`, `gl/`, `eq/` (`result.json` with the verdict, the reason, the input sha256s, `formal_eq`'s provenance and the clock depth; `formal_eq/` with `formal_eq`'s own `result.json`, yosys scripts, `abc.log` and gzipped logs; `checker.log`) |
| `driver.log`, `logs/` | Driver log; Slurm logs (gzipped when finished) |
| `tree/<commit>-<tools>/`, `current` | Frozen exports; the driver job uses `current` |

- **Stop:** `touch $PE_WORK/optimizer/STOP`. To stop at once, also run
  `scancel -n` on the job names.
- **Equivalence checker.** `eq_job.sh` runs `formal_eq/eq_check.py` of
  the driver's frozen tree. It finds that tree through `PE_OPT_HARNESS`,
  which the driver exports.
  - **Commit first.** `launch.sh` exports `formal_eq/` from the commit,
    like `src/`, and refuses a commit without it. It notes uncommitted
    changes to `formal_eq/`, which the tree does not get.
  - **Tools.** `launch.sh` also checks that `$OSS_CAD_SUITE/bin` has yosys
    and yosys-abc; without `OSS_CAD_SUITE` it takes them from `PATH`.
  - **Override.** `$PE_EQ_CHECK` replaces the checker. `launch.sh` warns
    when it is set. Leave it unset for the campaign.
  - **Work files.** `formal_eq` works in a node-local temporary directory,
    which the job removes.
  - **At start** the driver logs the checker's path, whether it exists, and
    `OSS_CAD_SUITE`.
- **Stop only the driver** (to relaunch with new tools): `scancel` the
  `pe-v2-optimizer-driver` job. Trial and promotion jobs are separate jobs
  and keep running; the next driver records their results from the store
  and the run directories.
- **Resume after a stop:** remove `STOP` and `FINISHED`, then run
  `tools/opt/launch.sh`.
- **Follow a new commit or updated tools:** run `tools/opt/launch.sh`
  again. It exports a new tree and moves `current`. The running driver
  keeps its tree until its successor starts; cancel it to switch earlier.
  - If a fixed key of `src/config.json` changed, new tracks start.
  - If only knob-controlled keys changed (an adopted configuration), the
    tracks continue with the new committed configuration as their
    baseline.

## Adopting a configuration

Only a promoted configuration with verdict PASS qualifies. That means a
full run that is legal including LVS 0, the precheck passing all checks,
the gate-level tests without failures and, in the tools after driver job
24077569, the equivalence check reporting "equivalent". The adopted p014 and
p018 predate that stage. `formal_eq` proved their netlists equivalent to
their RTL:

- **p014.** The official 6x4 build's netlist is byte-identical to p014's
  (job 24093883), and a test of the stage gave "equivalent" in job
  24096966.
- **p018.** Its local full run gave "equivalent" in job 24093888, and the
  official 8x4 build at 15 ns, byte-identical to it, in job 24093884.
- **The earlier results were not proofs.** The `gleq` checker had called
  both equivalent (jobs 24088368 and 24087778), but its recipe was unsound
  ([Equivalence check](#equivalence-check)).

Each track's section of the
leaderboard shows its promotions with their job ids. It also prints the
exact difference of its best legal configuration from the frozen
`src/config.json`:

- The difference lists the keys to set and the keys to remove. It is
  computed from the knob values (`tracks.Track.diff_vs_repo()`), without the
  local-only `OPENROAD_THREADS`, and equals `make_snapshot.py`'s
  `config_changes_vs_repo`.
- If the floorplan differs, it says so. In that case, copy the
  `MACROS.RM_IHPSG13_1P_64x16_c2.instances` block from
  `floorplans/<id>.json`.

Steps for the 8x4 design of record:

1. Add the keys to `src/config.json` above the "DO NOT CHANGE" line, and
   remove the listed keys. Add a `"//"` comment that names the promotion id
   and its job ids. Do not edit anything below that line.
2. Run `python3 macros/check_macro_floorplan.py`. It must pass.
3. Optionally, rebuild a snapshot from the edited repository with
   `scripts/sweep/make_snapshot.py --mode full --threads 4`. Its
   `config_changes_vs_repo` must be empty, which confirms that the edit
   reproduces the promoted configuration.
4. Commit and push. The GitHub gds, precheck and gl_test actions on that
   commit are the result of record; compare their timing with the promoted
   run.

**Runtime (v3).** For `dor15`, `dor13` and `diet4_6x4` the verdict must be
exactly PASS, which includes a full-run projection of at most 5.5 h
([Runtime and the 6-hour limit](#runtime-and-the-6-hour-limit)). The `dor`
track (20 ns) and the variant tracks are not built by an official action
today; adopting one of their configurations would make it a CI target, so
check its full-run projection (the leaderboard shows it) the same way.

**A frequency-track configuration** (`dor15` or `dor13`): its full-run
projection must be at most 5.5 h ([Runtime and the 6-hour limit](#runtime-and-the-6-hour-limit);
the leaderboard's "projected CI job h (full run)"), and it goes through the
official actions on a branch before `main`
([Official verification on a branch](#official-verification-on-a-branch)).
The difference also contains `CLOCK_PERIOD`. The leaderboard also prints the `clock_hz`
of the track's snapshot (66666667 for 15 ns). Whether to change
`info.yaml` `clock_hz` is a separate decision: it states the clock the chip
is operated at, which the datasheet, the firmware and the host library
assume. `d76f1cc` changed `CLOCK_PERIOD` to 15 and kept `clock_hz`
50000000 (below). Until then this paragraph said to set `clock_hz` to the
leaderboard's value and to change every text that states 50 MHz.

**A 6x4 configuration** (`diet4_6x4`): the leaderboard prints a second
difference, from the committed 6x4 build (`src/config.json` with
`variants6x4/config.overlay.json` merged, as `variants6x4/switch.py apply`
writes it).

- Those keys go into `variants6x4/config.overlay.json`; a key to remove
  becomes `null` there.
- A key that `src/config.json` does not have must also be listed in
  `variants6x4/PROVENANCE.json` `overlay_new_keys`, or `switch.py check`
  fails; a `null` needs no listing. Every knob key is already in the
  overlay (`tools/opt/test_opt.py` checks this), so an adoption only
  changes values there.
- Then run `python3 variants6x4/switch.py check`. The CI workflow
  `gds_6x4.yaml` is the result of record for the 6x4 build.
- `4bd30c8` did this for p014: the merged configuration equals the full
  run's key for key (`docs/6x4.md` section 4b).
- The difference lists only the keys in which the promoted configuration
  differs from the committed 6x4 build. At `4bd30c8` the knob keys that
  were equal stayed inherited from `src/config.json`, among them
  `SYNTH_STRATEGY`, `PL_TIMING_DRIVEN` and `DESIGN_REPAIR_MAX_WIRE_LENGTH`.
  Since `8a05de7` and `fdc23f2` the overlay states all 33 knob keys of the
  6x4 space (`docs/6x4.md` section 4c), so an adoption for the 8x4 build no
  longer changes the 6x4 build's knob settings, and
  `variants6x4/info.overlay.json` keeps the 6x4 `clock_hz` at 50 MHz. Keep
  it that way when adopting: the difference only changes keys that are
  already in the overlay. The four v3 timing-repair runtime keys are 8x4
  only and not in the overlay. An 8x4 adoption that sets one of them in
  `src/config.json` must add it to the overlay as `null`;
  `tools/opt/test_opt.py` fails until it does.

### Adoption of p018 (`d76f1cc`)

`d76f1cc` (2026-09-27 05:56 UTC) put promotion p018 (trial
`dor15-26a873-8x4-p15-f174dc0#1`) into `src/config.json`. The difference
from the committed configuration of `131e793` was four keys:
`CLOCK_PERIOD` 20 → 15, `DESIGN_REPAIR_MAX_CAP_PCT` 50 → 45,
`GRT_LAYER_ADJUSTMENTS` [0, 0.2, 0, 0.1, 0] → [0, 0.2, 0.2, 0.1, 0] and
`PL_RESIZER_SETUP_SLACK_MARGIN` 5.75 → 5.8 (the promotion's
`config_changes_vs_repo`; `MACROS` equal). The commit changes exactly these
keys and adds `"//"` comments that name the promotion and its jobs.

**Clock.** `info.yaml` `clock_hz` stays 50000000. The chip is operated at
50 MHz; the firmware images, the host library, `pe_timing` and the
datasheet assume that clock, and tt-support-tools `d66cf179e` only checks
that `clock_hz` is an integer and never reads `CLOCK_PERIOD`. p018's local
precheck (job 24053971) ran on the snapshot's `info.yaml` with `clock_hz`
66666667; the precheck does not read it. The flow therefore signs the
design off at 66.7 MHz, and the margin at 50 MHz was measured separately
by re-timing the p018 layout at 20 ns (`docs/results.md` R86;
`docs/timing-closure.md` section 10).

**6x4.** `variants6x4/config.overlay.json` pins `CLOCK_PERIOD` 20 and every
other knob key, and `variants6x4/info.overlay.json` pins `clock_hz`, so the
6x4 build is unchanged ([`docs/6x4.md`](6x4.md) section 4c;
`docs/results.md` section 2c).

**Why p018 and not p019 or p021.** All three 15 ns promotions passed the
whole pipeline. Full-run values at 15 ns:

| | p018 | p019 | p021 |
|---|---:|---:|---:|
| Setup WS typ / fast / slow (ns) | +5.950 / +6.924 / +2.240 | +6.076 / +6.916 / +2.364 | +5.806 / +6.737 / +2.556 |
| Hold WS typ / fast / slow (ns) | +0.372 / +0.165 / +0.743 | +0.348 / +0.141 / +0.702 | +0.351 / +0.157 / +0.680 |
| Max-slew / max-cap / max-fan-out violations (worst corner; sum) | 4 / 1 / 1 (6) | 1 / 6 / 3 (10) | 3 / 5 / 1 (9) |
| Utilization | 0.6530 | 0.6646 | 0.6570 |
| Keys changed from `131e793` | 4 | 5 (adds `PL_TARGET_DENSITY_PCT` 59) | 6 (adds `PL_TARGET_DENSITY_PCT` 60 and `DESIGN_REPAIR_MAX_SLEW_PCT` 40) |

p019 and p021 have 0.124 and 0.316 ns more slow-corner setup slack. p018
was chosen for its hold slack, the largest at every corner (at the fast
corner +0.165 ns against +0.141 and +0.157 ns), the fewest slew, cap and
fan-out violations, and the smallest difference from the configuration that
CI had built; it also has the lowest utilization of the three. The
optimizer's own ranking (minimum setup WS first) would have put p021
first.

**CI.** The `gds` run of `d76f1cc` (36298635436) passed: `gds` in 4 h 24 min,
`gl_test` (102 tests, 46 pass, 56 skip, 0 fail) and `precheck` (finished
2026-09-27 13:33 UTC); `test`, `formal`, `regen` and `docs` passed on that
commit (`docs/results.md` section 2b, "Later commits"). As for p010 and
p014 (R19, R83), the official `metrics.csv` is byte-identical to the full
run's `out/metrics.csv`, and the gate-level netlists are identical (R85).

**Optimizer after the adoption.** `f511c97` changed the track weights so
that `dor15`, now the committed period, gets the largest share
([Tracks](#tracks)); driver job 24077569 runs on that tree. The `dor`
track's committed configuration became p018's knob set at 20 ns, whose
control promotion p027 passed (setup WS +7.864 / +9.389 / +2.998 ns at
20 ns; `docs/results.md` R88).

## Campaign record

### v1 (2026-09-26 03:36 to 13:40)

**Launch.** The campaign was launched on 2026-09-26 from commit `be7dbda`.
`src/` at that commit is identical to `c118027`. Driver jobs:

| Driver job | Started | Why |
|---|---|---|
| 23993146 | 03:36 | First launch |
| 23994123 | 03:54 | Relaunch: `postprocess.py` records the resizer messages |
| 23995423 | 04:40 | Relaunch: `DRT_OPT_ITERS` retired |
| 23996205 | 04:53 | Relaunch: an unused variable removed from `driver.py`; no change in behavior |
| 24020736 | 13:35 | Relaunch on tree `131e793` with the same tools; cancelled at 13:40 (see [Absolute knob values](#absolute-knob-values)) |

Each relaunch resumed from the store and submitted nothing twice: the store
held the same 19 trial submissions before and after the first relaunch.

**Pre-launch smoke tests.** These ran on compute nodes; the job ids are in
`optimizer/smoke/SMOKE.json`.

- **PDN watcher on the design-of-record PDN** (job 23993041): 26 VPWR and
  26 VGND stripes, 0 short.
- **Precheck job body on the `c118027` CI GDS** (job 23992848): 9/9 checks
  pass.
- **Gate-level job body on the `c118027` CI netlist** (job 23992849): 102
  tests, 46 pass, 56 skip, 0 fail.

**First wave, design of record.** All numbers are fast mode, from
`optimizer/store/events.jsonl` at 05:15. Trials 4 (DELAY 3), 6 (ABC
buffering) and 18 (combination) were still in detailed routing then.

| Trial | Job | Point | Legal | typ / fast / slow setup WS (ns) | Slow worst start | Utilization |
|---:|---|---|---|---|---|---:|
| 0 | 23993203 | Committed configuration (`c118027`) | yes | +0.89 / +6.15 / −8.52 | `rst_n` | 0.5853 |
| 1 | 23993204 | Setup margin 2 ns | yes | +2.93 / +6.66 / −5.10 | `rst_n` | 0.5856 |
| 2 | 23993205 | Setup margin 5 ns | yes | +5.06 / +7.32 / −1.10 | a flip-flop | 0.5861 |
| 3 | 23993206 | Post-GRT timing repair, margin 1 ns | yes | +2.08 / +6.12 / −6.65 | `ena` | 0.5859 |
| 5 | 23993208 | `SYNTH_STRATEGY` DELAY 1 | no: route DRC 18, 1 typ setup violation | −0.04 / +4.11 / −9.34 | `ui_in[7]` | 0.6012 |
| 7 | 23993210 | `MAX_FANOUT_CONSTRAINT` 6 | yes | +1.85 / +5.66 / −7.00 | `rst_n` | 0.5990 |
| 8 | 23993212 | Repair max wire length 300 um | no: route DRC 8 | +4.37 / +7.64 / −2.63 | `rst_n` | 0.5983 |
| 9 | 23993214 | Repair slew/cap margins 40% | no: 23 typ setup violations | −0.38 / +5.35 / −10.50 | `rst_n` | 0.5861 |
| 10 | 23993215 | Timing-driven placement | yes | +3.67 / +7.88 / −3.71 | `ui_in[3]` | 0.5843 |
| 11 | 23993217 | CTS slew 0.75 ns, clusters of 16 | no: 43 typ setup violations | −0.54 / +5.24 / −10.68 | `rst_n` | 0.5926 |
| 12 | 23993219 | `fp8_base_trk` | no: 7 typ setup violations | −0.37 / +5.39 / −10.50 | `rst_n` | 0.5853 |
| 13 | 23993220 | `fp8_wide_trk`, halo 10 | yes | +1.19 / +6.35 / −8.01 | `rst_n` | 0.5850 |
| 14 | 23993221 | Density 52 | yes | +1.36 / +5.92 / −7.78 | `rst_n` | 0.5852 |
| 15 | 23993223 | Density 68 | yes | +0.78 / +5.52 / −8.54 | `rst_n` | 0.5855 |
| 16 | 23993224 | GRT Metal2/Metal3 adjustment 0.2 | yes | +1.42 / +5.91 / −7.73 | `rst_n` | 0.5858 |
| 17 | 23993226 | Hold margin 0.05 | yes | +0.23 / +5.89 / −9.77 | `rst_n` | 0.5784 |

**What the first wave showed.**

- **The baseline trial reproduces the official result.** It matches the
  typ and slow WS of GitHub run 36144357821 (+0.89 / −8.52 ns). Its
  slew/cap/fan-out counts are 290/73/524.
- **Setup margin.** At 5 ns (trial 2), the post-CTS resizer found 2,282
  endpoints below the margin, inserted 79 buffers and resized 58 instances
  (`RSZ-0062`: not all repaired). Slow-corner WS moved from −8.52 to
  −1.10 ns, and typ WS to +5.06 ns.
- **Timing-driven placement** (trial 10) reached slow-corner WS −3.71 ns.
  Detailed routing converged in 6 iterations (46 in run2), and global
  routing ended with 0 overflow.
- **Trials 19 and 20** (TPE) were the two trials with `DRT_OPT_ITERS` 80
  and failed as described under "Never searched".
- **TPE trial 23** (job 23995853) was the first trial with positive setup
  WS at all three corners in fast mode: typ/fast/slow +6.40/+8.68/+0.73 ns.

**Promotions of v1.** All ten passed the whole pipeline (full run legal with
LVS 0, precheck 9/9, gate-level 102 tests: 46 pass, 56 skip, 0 fail). Full
runs use `OPENROAD_THREADS` 4; WS is from the full run, at 20 ns.

| Promotion | Trial | Full run | Precheck | Gate level | typ / fast / slow setup WS (ns) | Utilization |
|---|---:|---|---|---|---|---:|
| p001 (control, `c118027`) | 0 | 23993269 | 23997237 | 23997238 | +0.885 / +6.153 / −8.517 | 0.5853 |
| p002 | 10 | 23994350 | 23997459 | 23997460 | +3.675 / +7.878 / −3.709 | 0.5843 |
| p003 | 2 | 23994927 | 23997682 | 23997683 | +5.059 / +7.321 / −1.102 | 0.5861 |
| p004 | 23 | 23997360 | 23998685 | 23998686 | +6.404 / +8.676 / +0.732 | 0.5777 |
| p005 | 33 | 23999287 | 24002102 | 24002103 | +6.509 / +8.945 / +0.918 | 0.6004 |
| p006 | 39 | 24000165 | 24003018 | 24003019 | +7.038 / +8.679 / +1.683 | 0.5990 |
| p007 | 41 | 24000424 | 24003204 | 24003205 | +7.751 / +9.375 / +2.085 | 0.6111 |
| p008 | 45 | 24002310 | 24004838 | 24004839 | +7.323 / +9.212 / +2.197 | 0.6018 |
| p009 | 56 | 24003206 | 24004529 | 24004530 | +7.367 / +8.857 / +2.629 | 0.6177 |
| p010 (adopted in `25e331e`) | 94 | 24010051 | 24011934 | 24011935 | +7.880 / +9.307 / +2.954 | 0.6192 |

**Variant tracks of v1.** Each had 12 trials, including the committed
configuration and transfers of the design of record's best. Best legal trial
per core, fast mode, at 20 ns:

| Core | Best min WS (ns) | typ / slow WS (ns) | Utilization |
|---|---:|---|---:|
| rstreg_timing | 3.529 | +9.31 / +3.53 | 0.6635 |
| diet2 | 3.464 | +8.26 / +3.46 | 0.4005 |
| diet4 (8x4) | 2.933 | +8.36 / +2.93 | 0.4516 |
| rstreg | 2.508 | +7.58 / +2.51 | 0.6226 |
| cn | 2.495 | +7.66 / +2.50 | 0.5882 |
| cn_s2 | 2.433 | +8.05 / +2.43 | 0.5887 |
| cn_s2_timing | 2.401 | +9.05 / +2.40 | 0.6396 |

Every variant changes the design's contract in some way
(`docs/variants.md` section 4); the diet cores also change the ISA and the
queue depth. These trials rank the variants on physical design only. The
best legal trial of the design of record at that point was v1 `#118`
(min WS 2.998 ns, utilization 0.6196), which was not promoted: its gain over
p010's 2.954 ns is below the 0.05 ns promotion threshold.

### v2 (from 2026-09-26 14:11)

**Before the launch.**

- `tools/opt/test_opt.py`: 8 tests pass.
- Three dry runs on `mit_quicktest` against copies of the store (jobs
  24023899, 24024091 and 24024248). They created the 11 tracks, imported
  196 trials, enqueued the seeds and planned full loops. They also built
  15 ns, 13.33 ns and 6x4 snapshots:
  - the 15 ns snapshot's only config change is `CLOCK_PERIOD` 15, with
    `info.yaml` `clock_hz` 66666667;
  - the 6x4 committed-build snapshot's changes are exactly the overlay's
    (`FP_MACRO_HORIZONTAL_HALO` 16.48, `FP_OBSTRUCTIONS`, `MACROS`,
    `PL_TARGET_DENSITY_PCT` 60), with `tiles` "6x4", the
    `tt_block_6x4_pgvdd.def` template and the `diet4` core. The floorplan
    checker passed.
- The driver of v1 (job 24020736) was cancelled at 13:40. Its 19 trial jobs
  kept running. The v2 driver recorded the 13 that had finished in its first
  loop (11 of them imported).

**Launch.** `tools/opt/launch.sh` at 14:11 on tree `131e793` with tools
`05d186a8` (driver job 24024261). It created the tracks
`dor-26a873-8x4-p20-f174dc0`, `dor15-26a873-8x4-p15-f174dc0`,
`dor13-26a873-8x4-p13p33-f174dc0`, `diet4_6x4-cc27c4-6x4-p20-f174dc0` and one
track per variant core (`cn`, `cn_s2`, `cn_s2_timing`, `diet2`, `diet4`,
`rstreg`, `rstreg_timing`), imported 207 trials in its first loop, and
submitted 15 trials (all the CPUs the cap left next to the 6 v1 trial jobs
still running). Every track got one; `dor` got three, `dor15` and `diet4_6x4`
two each.

**First trials of the new tracks** (fast mode, from the store; the
leaderboard has the current state). WS is at the track's period.

| Trial | Job | Seed | Legal | typ / fast / slow setup WS (ns) | fmax estimate typ / fast / slow (MHz) | Utilization |
|---|---|---|---|---|---|---:|
| `dor15` #0 | 24024331 | committed configuration at 15 ns | yes | +5.92 / +6.82 / +1.46 | 110.1 / 122.3 / 73.9 | 0.6512 |
| `dor15` #1 | 24024343 | 20 ns best #1 (v1 `#118`) | yes | +5.95 / +6.92 / +2.24 | 110.5 / 123.8 / 78.4 | 0.6530 |
| `dor15` #3 | 24026026 | 20 ns best #4 | yes | +6.11 / +7.02 / +1.65 | 112.5 / 125.3 / 74.9 | 0.6645 |
| `dor15` #5 | 24028165 | 20 ns best #6 | yes | +4.91 / +6.18 / +1.66 | 99.1 / 113.4 / 75.0 | 0.6448 |
| `diet4_6x4` #0 | 24024332 | committed 6x4 build | no: route DRC 69, LibreLane exit 2 | +5.65 / +9.29 / −0.63 | – | 0.6097 |
| `diet4_6x4` #1 | 24024344 | 6x4 point signed off before the adoption | yes | +4.30 / +8.50 / −2.80 | 63.7 / 86.9 / 43.9 | 0.5628 |
| `diet4_6x4` #2 | 24025965 | 20 ns 8x4 best #1, translated | yes | +6.72 / +9.89 / +1.26 | 75.3 / 98.9 / 53.4 | 0.6059 |
| `diet4_6x4` #4 | 24027464 | 20 ns 8x4 best #3, translated | yes | +7.29 / +10.28 / +2.06 | 78.7 / 102.9 / 55.8 | 0.5888 |
| `dor` #134 | 24024341 | TPE | yes | +8.14 / +9.49 / +3.55 | 84.3 / 95.1 / 60.8 | 0.6061 |
| `dor` #143 | 24028378 | TPE | yes | +8.12 / +9.55 / +3.63 | 84.2 / 95.6 / 61.1 | 0.6187 |

- **15 ns.**
  - The committed configuration meets setup at all three corners at
    15 ns in fast mode, with +1.46 ns at the slow corner.
  - The STA logs show the SDC scaling described above: input and output
    delay 3.0 ns in `dor15` #0 (job 24024331) against 4.0 ns in the 20 ns
    trial `dor` #134 (job 24024341). Uncertainty 0.25 ns, transition
    0.15 ns and derate 5% are the same in both.
  - 15 ns trials take longer than 20 ns ones. The 11 that had finished by
    18:17 took 38 to 167 minutes (median 90), against a median of 32
    minutes for the v1 trials of the design of record and 24 minutes for
    the v2 20 ns trials (`flow_runtime_s` in the store). (The first
    finished 15 ns trials, when this list was first written, took 66 to
    118 minutes.)
- **6x4.**
  - The committed 6x4 build (the overlay on the adopted `src/config.json`)
    ended with 69 route DRC violations in fast mode. The GitHub gds_6x4 run
    of `131e793` (36257636751) failed with the same count: `69 Routing DRC
    errors found`. The control promotion p012 ran the same configuration
    in full mode with `OPENROAD_THREADS` 4 (full run job 24029191): route
    DRC 69, and the flow stopped in `Netgen.LVS`, so p012 is FAIL.
  - The pre-adoption 6x4 point reproduced its full-mode sign-off numbers of
    `docs/6x4.md` (+4.30 / +8.50 / −2.80 ns, utilization 56.3%, hold
    minimum +0.099 ns) to within 0.01 ns.
  - Two translated 20 ns configurations are legal with positive setup WS
    at all corners (+1.26 and +2.06 ns at the slow corner).
- **20 ns.**
  - TPE trial #134 (setup margin 6.35 ns, outside v1's range) was promoted
    as p011: **PASS**.
    - Full run job 24026087 (`OPENROAD_THREADS` 4): legal, LVS 0, setup WS
      typ/fast/slow +8.138/+9.487/+3.548 ns, the same as the trial's.
    - Precheck job 24033557: 9/9.
    - Gate-level job 24033558: 102 tests, 46 pass, 56 skip, 0 fail.
  - Trial #143 was promoted as p013: **PASS**.
    - Full run job 24030864: legal, LVS 0, setup WS typ/fast/slow
      +8.120/+9.545/+3.634 ns.
    - Precheck job 24036014: 9/9.
    - Gate-level job 24036159: 102 tests, 46 pass, 56 skip, 0 fail.
  - By 17:00, TPE trial #150 (job 24033993) reached +7.64/+9.04/+4.28 ns
    with 3/2/0 slew/cap/fan-out violations and utilization 0.6178. It is
    promoted as p015 (full run job 24037612, still running at 18:17).
  - TPE trial #154 (job 24036312) reached +7.99/+9.27/+4.83 ns and was
    promoted as p016: **PASS**.
    - Full run job 24038331: legal, LVS 0, setup WS typ/fast/slow
      +7.986/+9.271/+4.835 ns, the same as the trial's; hold WS minimum
      +0.140 ns (fast); utilization 0.6183.
    - Precheck job 24043244: 9/9.
    - Gate-level job 24043245: 102 tests, 46 pass, 56 skip, 0 fail.
  - In the same period, 6x4 trial #10 (job 24035515) reached +2.76 ns at
    the slow corner and is legal. The best 6x4 trial at 16:39, #8
    (+2.74 ns, job 24032431), was promoted as p014: **PASS**.
    - Full run job 24036160: legal, LVS 0, setup WS typ/fast/slow
      +7.706/+10.518/+2.737 ns, the same as the trial's; hold WS minimum
      +0.051 ns (fast); utilization 0.5890.
    - Precheck job 24041156: 9/9.
    - Gate-level job 24041157 (`PE_VARIANT` diet4): 102 tests, 46 pass,
      56 skip, 0 fail.
    - `4bd30c8` put it into `variants6x4/config.overlay.json` (see
      [Adopting a configuration](#adopting-a-configuration)).
  - The best 6x4 trial at 18:17 was #12 (job 24039112, +2.99 ns at the slow
    corner), a transfer of `dor` #154; it is promoted as p017 (full run job
    24041769, still running at 18:17).
- **13.33 ns.** The first finished trial was `dor13` #4 (job 24037546,
  seed "20 ns best #5", 27 minutes):
  - not legal: one fast-corner hold violation (hold WS −0.022 ns), so
    LibreLane exited with 2;
  - setup WS typ/fast/slow +2.49/+5.29/−2.35 ns (register-to-register
    +4.69/+11.30/−0.50); the worst slow path starts at `ui_in[3]`;
  - its STA log sets input and output delay to 2.666 ns, 20% of 13.33 ns;
  - its `info.yaml` has `clock_hz` 75018755.

  Trials #0 to #3 were still running at 17:25. #0 was in post-CTS repair
  (see [Seeds](#seeds), revision 2).

**Relaunches.** Each relaunch cancelled only the driver job; trial and
promotion jobs kept running, and each new driver resumed from the store.
None of them created a track, imported a trial twice or submitted a trial
twice.

| Driver job | Started | Tools | Why |
|---|---|---|---|
| 24024261 | 14:11 | `05d186a8` | v2 launch |
| 24037129 | 16:52 | `caa2d93f` | Documentation strings only (`space.py` text, `README.md`) |
| 24038948 | 17:17 | `9b5326e2` | Seed revision 2. `dry_run.sh` also copies the study journals. Dry run 24038928 checked it on a copy of the live store first. |
| 24059198 | 21:01 | `c1afc5db` | Tree `24b807b`: CPU cap 700 → 960. The tree also carries the p014 overlay of `4bd30c8`, so the 6x4 track's committed configuration became p014. |
| 24077569 | 2026-09-27 01:57 | `4240a2a4` | Tree `f511c97`: `src/config.json` carries p018 (`d76f1cc`), and the weights favour `dor15` ([Tracks](#tracks)). No track was created; `dor15` and `dor` took the new committed configurations as their baselines. |
| 24097862 | 2026-09-27 08:42 | `af25eba8` | Tree `0f8576e`: the equivalence stage on `formal_eq` and `CTS_MAX_SLEW` fixed unset (the next two items). |

**Equivalence stage and fixed `CTS_MAX_SLEW` (tools `931b809b`, never
run by a driver).** Driver job 24077569 runs the earlier tools
(`4240a2a4`); the changes take effect at the next driver start
([Operation](#operation)). The equivalence checker of these tools was
`gleq/eq_check.sh`, whose recipe was unsound. The "equivalent" results in
this list are therefore not proofs, and the `formal_eq` results of the next
item supersede them ([Equivalence check](#equivalence-check)). Checks
before that relaunch, on copies of the store and under separate job names
(`pe-v3-eq-opt-*`):

- `tools/opt/test_opt.py`: 20 tests pass (9 before).
- `eq_job.sh` alone (job 24092905): p014 equivalent (ABC 81 s), p014_mutA
  not equivalent (frame 8), p014 with a 5 s ABC limit a timeout, so FAIL.
- `clockdepth.py` on the 26 promotion netlists (job 24092904): the table
  in [Clock depth warning](#clock-depth-warning), equal to the `gleq`
  survey.
- The gate through the driver's own code path: `Driver.submit_signoff()`
  builds `sub/` and submits `eq_job.sh`, `Driver.poll_promos()` records the
  result, and the leaderboard shows it.
  - With tools `931b809b`: p014 equivalent (job 24094084, ABC 111 s).
  - With earlier tools: p018 equivalent (job 24092982, tools `6c8051a1`,
    ABC 913 s); p026 equivalent (jobs 24092983 and 24093380, tools
    `6c8051a1` and `790341d9`); p014 equivalent (job 24093377, tools
    `790341d9`). In the path test's leaderboard p018's verdict is PASS and
    p026's stays FAIL on its gate-level result.
  - p014 with a 5 s ABC limit (tools `5efda372`): the driver resubmitted
    the timed-out check twice (jobs 24093661, 24093683, 24093709) and then
    recorded FAIL.
  - Differences from `931b809b`, apart from comments: all three earlier
    versions gave ABC 2,400 s and the job 70 minutes; `6c8051a1` did not
    yet copy the clock depth into the equivalence result or catch an error
    while resubmitting a sign-off job; `790341d9` did not yet resubmit
    timeouts or record a missing input file as an error.
- Dry runs against copies of the store (jobs 24092979, 24093336, 24093680,
  24093804 and, with tools `931b809b`, 24094067; two loops each). Each
  planned 48 new trials, all with `CTS_MAX_SLEW` unset and none rejected,
  so the existing optuna studies accept sampling without the knob. Each
  planned the equivalence stage for the 26 promotions with a legal full
  run and the promotion of `diet4_6x4` #41 as p028.

**Equivalence stage on `formal_eq` (tools `af25eba8`; running since driver
job 24097862, 2026-09-27 08:42 cluster time; this item was written before
that start).**
These tools replace the `gleq` checker with `formal_eq/eq_check.py` of the
frozen tree ([Equivalence check](#equivalence-check)). They also add the
checks of `launch.sh` for `formal_eq/` and the OSS CAD Suite. The driver
job 24077569 still runs tools `4240a2a4`, which have no equivalence stage,
and the campaign's store has no equivalence result. The checks before the
relaunch used copies of the store in scratch optimizer roots and job names
`pe-v3-eqwire-*`:

- **Unit tests.** `tools/opt/test_opt.py`: 26 tests pass. New tests cover:
  - the reading of `formal_eq`'s `result.json`: every verdict,
    inconsistent fields, a missing or invalid file, a schema mismatch, and
    sha256 mismatches of the netlist, its copy, the core and
    `src/project.v`, the PDK root and the variant;
  - the retry policy, both in `gates.eq_should_retry()` and through
    `Driver.poll_promos()`: a time limit is resubmitted until the third
    job, while undecided and "not equivalent" are final;
  - `gates.py eq-collect` on real files;
  - `eq_job.sh` run from a copy in another directory, with a stand-in
    checker.
- **The stage, live** (table in [Equivalence check](#equivalence-check)):
  - p014 equivalent (job 24096966) and PASS;
  - p026 equivalent (job 24096968), while its promotion stays FAIL on the
    gate level;
  - p018 equivalent (job 24096976, ABC 1,014 s) and PASS;
  - a copy of p014's promotion with the mutA netlist: not equivalent,
    final (job 24096965);
  - p014 with an ABC limit of 5 s: three time limits (jobs 24096967,
    24096999 and 24097014), then FAIL.
- **The first attempt** with tools `0a19ce4b` failed closed with checker
  exit 127 (jobs 24096857, 24096858, 24096859 and 24096874). The fix is in
  [Equivalence check](#equivalence-check).
- **Inputs of every promotion.** `Driver.eq_inputs()` was evaluated for
  the 26 promotions with a legal full run, read-only. Each gives:
  - the flow mirror's PDK root from its `out/resolved.json`;
  - a core whose sha256 matches the track's record, and whose first line
    names its variant's configuration (20 `base`, 6 `diet4`);
  - a frozen tree whose `src/project.v` has sha256 `2aacfd24…`.
- **Dry run** (job 24096961, two loops, tools `af25eba8` on `e0b5223`):
  - it planned the equivalence stage for the 26 promotions and p028
    (`diet4_6x4` #41);
  - it planned 48 new trials, none with `CTS_MAX_SLEW` set;
  - it created no track and logged no loop error;
  - it logged the checker as present.

### v3 (tools of the runtime rule; not yet running)

The v3 tools add `tools/opt/runtime.py`, the four timing-repair runtime
knobs, the eligibility rule and ranking of the 15 ns and 13.33 ns tracks,
the recording of step times, seed revision 3, the new weights and CPU cap
([Allocation](#allocation), [Scheduling limits, preemption and
pruning](#scheduling-limits-preemption-and-pruning)), and the 5 h limit for
trials of the rule tracks. A driver runs them only after the relaunch
([Operation](#operation)). The checks before the relaunch used scratch
optimizer roots and job names `pe-v4-mhz75-*`:

- **Unit tests.** `tools/opt/test_opt.py`: 36 tests pass (26 before). The
  new tests cover:
  - the knobs' spaces and materialization, the keys of stored trials and
    the track ids, on the committed tree and on a copy after an adoption
    that commits `PL_RESIZER_SETUP_REPAIR_TNS_PCT` 10 (with `null` in the
    6x4 overlay);
  - the runtime constants against the 18 official jobs, including p021's
    repair ratio for `K_RSZ`;
  - the bound, the TPE penalty, the projection of incomplete flows (none),
    the extraction of step times and resolved synthesis settings, and the
    speed factor;
  - the verdict with the full-run projection;
  - the driver's ranking, promotion, bar and revision-3 seeds in a rule
    track, the penalty of a duplicate of a pre-v3 trial, the synthesis key
    from the resolved configuration, the trial time limit, the weights
    epoch, and the backfill of step times.
- **Adoption, end to end.** On a copy of the repository with
  `PL_RESIZER_SETUP_REPAIR_TNS_PCT` 10 in `src/config.json` and `null` in
  the 6x4 overlay, all 36 tests pass and `variants6x4/switch.py check`
  passes. Without the `null`, two tests fail
  (`test_overlay_states_every_knob`, `test_matches_switch_py`).
- **Stored trials.** The new tools reproduce the recorded key of all 651
  trials of the 12 current tracks and derive the same 12 track ids as the
  running campaign (store of 2026-09-27 18:20 cluster time).
- **Independent review** of the v3 tools and this page; its 11 minor
  findings (the penalty of duplicates, the verdict, the 6x4 rule, the
  trial time limit's basis and the synthesis key of mis-recorded runs, stale
  counts, adoption-proof tests, incomplete flows, `K_RSZ`, the `gds_6x4`
  exclusion reason, a path count, a stale Lean file) are fixed in these
  tools.
- **Dry runs** on copies of the store, two loops each: job 24120704, whose
  weights still counted trials since the launch; jobs 24120795 and
  24121599, whose revision-3 seeds still included the value 0 and the
  committed configuration; jobs 24128457 and 24130828, before the review
  fixes were complete; job 24130924 (tools `eb18202e`, store of 18:20 with
  2,545 events), the source of the numbers in
  [Where it stands](#where-it-stands); and job 24131206 with the final
  tools (`be2998b3` on `a15f6f2`, which differ from `eb18202e` in comments
  only; store of 18:26 with 2,552 events). Job 24131206:
  - recorded the step times of 862 earlier runs;
  - enqueued 6 seeds each in `dor15` and `dor13` (revision 3);
  - promoted `dor15` #16 (p032, projection 3.70 h) and `dor13` #14 (p033,
    4.62 h); the running campaign had meanwhile made p030 (`diet2` #16) and
    p031 (`diet4_6x4` #79);
  - planned 42 new trials: 18 `dor13`, 8 `diet4_6x4`, 6 `dor15`, 2 `dor`
    and one for each of the 8 variant tracks (`diet8_rec16`, which the
    running campaign created at 16:45, included);
  - showed "PASS (over CI time bound …)" for p019, p021 and p024 and PASS
    for every other promotion whose four gates passed;
  - created no track and logged no loop error.
- **STA of p024** at 13.33 ns (job 24119756) and of p021's layout re-timed
  at 13.33 ns (job 24119757): [The 75 MHz plan](#the-75-mhz-plan).
- **Experiment** with `PL_RESIZER_SETUP_REPAIR_TNS_PCT` 0, 10 and 25 on the
  committed configuration at 13.33 ns (jobs 24120814, 24120815 and
  24120816): [The 75 MHz plan](#experiment-pl_resizer_setup_repair_tns_pct-on-the-committed-configuration).

## Limitations

- **Fast trials differ from promoted runs.**
  - Trials use `OPENROAD_THREADS` 32; the action uses 4. Four runs of the
    submission point gave identical results at 4, 32 and 48 threads
    (`docs/sweep.md`, "Repeatability"), but detailed routing can depend on
    the thread count. Promoted runs therefore use 4.
  - Trials do not run LVS.
- **Imported trials rely on determinism.** An imported trial is the result
  of an identical effective configuration on an earlier frozen tree. The
  flow inputs are identical: only `src/config.json` changed between
  `be7dbda` and `131e793` among the flow's inputs, and only in
  knob-controlled keys. The result is not re-run.
- **The LEF check approximates the precheck.** It merges only vertically
  touching rectangles with the same x span; the precheck merges all touching
  rectangles. For the full-height vertical stripes of this design the two
  agree. The precheck itself runs on every promotion.
- **The island model is a model.** `row_islands.py` reproduces every
  earlier strap observation (`docs/6x4.md` section 2). The PDN DEF and LEF
  checks of every trial and the precheck of every promotion remain the
  checks that count.
- **The slow corner is not a sign-off corner.** Objective 2 still ranks by
  it, because the goal is margin at all corners.
- **Official sign-off above 50 MHz: 15 ns only.** The 15 ns configuration
  p018 is committed since `d76f1cc`, and its official `gds` run
  (36298635436) passed (see [Current results](#current-results)). At
  13.33 ns, p024 passed the local sign-off including the equivalence stage
  (2026-09-27 13:37 UTC); no 13.33 ns configuration is committed, and p024's
  official job is projected above 6 h ([The 75 MHz plan](#the-75-mhz-plan)).
- **The runtime model rests on 18 official jobs of 4 configurations.**
  - Only one of them (p018) has a long timing repair, so `K_RSZ` comes from
    3 jobs; it is set above the measured maximum (1.65 against 1.556) to
    cover p021's larger full-run/trial repair ratio.
  - The speed factor assumes that a node's synthesis speed predicts its
    speed in the other steps. After the division by s, the official flow
    still took 1.189 to 1.333 times the local full run's.
  - The reference synthesis time is the fastest one in the store. A faster
    node class appearing later would lower it and every projection with it.
  - The runners seen so far fall into two speed groups. A slower runner
    would break the bound. The trial time limit likewise rests on the
    slowest local node observed (s = 2.15).
  - The TPE values of trials told before v3 are not re-valued; only
    duplicates of them get the penalty.
  - The model is empirical: it over-predicted every one of the 18 jobs, and
    it is not a guarantee.
- **fmax estimates extrapolate** from one period (see "Frequency tracks and
  the SDC"). The frequency tracks measure at their period instead.
- **Variant-track gate-level tests** use the committed firmware images,
  because `build/variants/` is not in the frozen export. The results are
  reported as they are. The variant cores' own verification belongs to the
  variant workflow.
- **TPE is a heuristic.** The leaderboard reports only what was run. No
  claim is made that an optimum was reached.
- **The equivalence check has limits** ([Equivalence
  check](#equivalence-check); docs/equivalence.md, section 6).
  - It does not cover the SRAM macros, the liberty cell functions against
    the layout, timing, or other power-up values of the base core's
    registers without reset.
  - It shares the yosys front end with synthesis.
  - Because undecided and time limits fail, it can also reject a correct
    netlist. Every `formal_eq` check of a real netlist so far was decided.
  - The stage runs without `formal_eq`'s self-test.
- **Equivalence results before `formal_eq` were not proofs.** The `gleq`
  checker's recipe compared an output bit only where the RTL value was 1
  (ablation job 24094085). Its results are superseded
  ([Equivalence check](#equivalence-check)).
- **The clock-depth threshold rests on two promotions** (p025 and p026),
  and `CTS_MAX_SLEW` was fixed unset on the same two samples
  ([`CTS_MAX_SLEW` is fixed unset](#cts_max_slew-is-fixed-unset)).
