# tools/sta: re-time a signed-off layout at another clock period

`sta_retime.py` runs LibreLane's own post-route static timing analysis on the
final netlist and nominal SPEF of a finished LibreLane run, with
`CLOCK_PERIOD` replaced by a period of your choice. The layout, the parasitics
and every other constraint stay as the flow had them. It is the method behind
the 50 MHz margins in [timing-closure.md](../../docs/timing-closure.md)
section 10.3 and [results.md](../../docs/results.md) R86. The design is signed
off at 15 ns and operated at 20 ns; the flow itself times it at 15 ns only.

The tool also reports the worst path of four path classes: register to
register, input to register, register to output and input to output. It
compares two runs of the same layout at different periods against the shift
that `base.sdc` predicts for each class.

## What runs

For each corner, OpenSTA (`sta -no_splash -exit corner.tcl`, the command in
the step's `COMMANDS` file) runs inside the LibreLane image under apptainer:

- **Script.** LibreLane's `scripts/openroad/sta/corner.tcl`, the script of the
  `OpenROAD.STAPostPNR` step. It sources `openroad/common/io.tcl`, which
  reads the SDC `scripts/base.sdc` (LibreLane's `FALLBACK_SDC`, used because
  the design sets neither `PNR_SDC_FILE` nor `SIGNOFF_SDC_FILE`).
- **Nothing copied.** The tool does not ship these scripts. It reads them from
  the image at run time and records their sha256 in `case.json`, together
  with the LibreLane and OpenSTA versions. For LibreLane 3.1.0.dev3 (OpenSTA
  2.7.0) the values are:

  | File | sha256 |
  |---|---|
  | `corner.tcl` | `e0096884c1929ed80506366ecc3e809c78c8493180e366fc095e2b09f5c36f53` |
  | `io.tcl` | `c7b28322f0853c7c6ebe6076a454c0baf3d97cc6194c0931eae7bf17a442b80f` |
  | `set_global_connections.tcl` | `80c6f145ee34a241e089c916656d5586da2c330cdcca5ae7004b1786a1e3b599` |
  | `base.sdc` | `34c8f331c366db51a78ed8a0d2a7bbac3b6c1cfe0277a04c6aca9ba4948e7f78` |

  A different image is flagged (`image.matches_reference: false`), but the
  run still goes ahead.

**Process environment.** `corner.tcl` gets the environment that the step
builds (`OpenSTAStep.prepare_env`, `CornerFileList.set_env`,
`MultiCornerSTA.run_corner` and `STAPostPNR.prepare_env` in
`librelane/steps/openroad.py`):

| Variable | Value |
|---|---|
| `_TCL_ENV_IN` | `env.tcl` (below) |
| `SCRIPTS_DIR` | the image's `librelane/scripts` |
| `_CURRENT_CORNER_NAME` | the corner |
| `_CURRENT_CORNER_LIBS` | the corner's standard-cell and I/O libs (`CELL_LIBS`, `PAD_LIBS`) and macro libs (`MACROS.*.lib`), selected with LibreLane's wildcard rules (`toolbox.filter_views`, `get_timing_files_categorized`) |
| `_CURRENT_CORNER_NETLISTS`, `_CURRENT_CORNER_SPEFS` | empty (liberty macros, as in the flow) |
| `_CURRENT_SPEF_BY_CORNER` | the layout's nominal SPEF |
| `_SDC_IN` | the image's `base.sdc` |

`_SDF_SAVE_DIR` and `_LIB_SAVE_DIR` are left unset. They only write SDF and
liberty views after the metrics.

**Configuration (`env.tcl`).** The configuration comes from the run's
`resolved.json`: every variable that `corner.tcl`, `io.tcl` and `base.sdc`
read under OpenSTA. These are `DESIGN_NAME`, `CLOCK_PORT`, `CLOCK_PERIOD`,
`IO_DELAY_CONSTRAINT`, `MAX_FANOUT_CONSTRAINT`, `MAX_TRANSITION_CONSTRAINT`,
`MAX_CAPACITANCE_CONSTRAINT`, `SYNTH_DRIVING_CELL`, `SYNTH_CLK_DRIVING_CELL`,
`OUTPUT_CAP_LOAD`, `CLOCK_UNCERTAINTY_CONSTRAINT`,
`CLOCK_TRANSITION_CONSTRAINT`, `TIME_DERATING_CONSTRAINT` and
`STA_MAX_VIOLATOR_COUNT`, plus `CURRENT_NL` and
`OPENLANE_SDC_IDEAL_CLOCKS 0`.

- **Encoding.** Values are written the way LibreLane writes its `_env*.tcl`
  (`TclStep.value_to_tcl`, `TclUtils.escape`, with LibreLane 3.1.0.dev3's
  variable types; for example `CLOCK_PERIOD 15.0`).
- **What changes.** Only `CLOCK_PERIOD` (`--period`) and the netlist path
  differ from the flow's own step.
- **Unsupported configuration.** The tool refuses a run that sets
  `PNR_SDC_FILE`, `SIGNOFF_SDC_FILE`, `EXTRA_LIBS`, `EXTRA_VERILOG_MODELS`,
  `EXTRA_SPEFS` or `STA_EXTRA_CORNER_TCL_FILE`, or that times a macro from its
  netlist and SPEF.
- **Check against LibreLane's file.** `--compare-env` checks `env.tcl`
  against a `_env*.tcl` that LibreLane wrote for the run's STAPostPNR step.
  Every variable must match, apart from the netlist path and, at another
  period, `CLOCK_PERIOD`.

**Path classes.** `pe_extra.tcl` is sourced by `corner.tcl` through
`STA_EXTRA_CORNER_TCL_FILE`, after the design, SDC and SPEF are loaded and
before the reports. It runs read-only queries (`find_timing_paths`,
`report_checks`) and writes the worst setup and hold path of each class to
`extra.txt` and `extra_paths.rpt`:

| Class | From | To |
|---|---|---|
| r2r | register clock pins and macro clock pins | register data and async pins, macro data inputs |
| in2reg | `all_inputs -no_clocks` | the r2r end points |
| reg2out | the r2r start points | `all_outputs` |
| in2out | `all_inputs -no_clocks` | `all_outputs` |

The macro instances come from `resolved.json` (`MACROS.*.instances`). The
macro clock pins are the pins with `clock : true` in the macro libs (`A_CLK`
here); `--macro-clock-pins` overrides them.

## Inputs

**A run directory (`--run-dir`).** The tool recognises three layouts:

| Layout | Where it comes from | Files read |
|---|---|---|
| `tt_submission` | the `tt_submission` artifact of the `gds` workflow (GitHub Actions) | `resolved.json`, `<design>.v` (the unpowered netlist), `<design>.nom.spef`, `stats/metrics.csv` |
| `sweep_run` | a run of `scripts/sweep/` or `tools/opt/` (kept on the cluster) | `out/resolved.json`, `out/final/nl/<design>.nl.v.gz`, `out/final/spef/nom/<design>.nom.spef.gz` (checked against `out/final/SHA256SUMS`), `out/metrics.csv` |
| `librelane_run` | a LibreLane run directory | `resolved.json`, `final/nl/`, `final/spef/nom/`, `final/metrics.csv` |

`--resolved`, `--netlist`, `--spef` and `--ref-metrics` override single files.
Compressed inputs are decompressed into `CASE/inputs/`. In this
repository's flow the final netlist and nominal SPEF are the views that the
STAPostPNR step read: the fill-inserted netlist and the RCX nominal SPEF.
Control 1 below checks this for the run at hand.

**Other inputs:**

- **PDK.** The corner libs of `CELL_LIBS`/`PAD_LIBS`, mapped from their
  recorded path into `PDK_ROOT/<PDK>/…`.
- **Macro libs.** Taken from `macros/<macro>/` in this repository (or
  `--macro-dir`), matched by file name.
- **Corners.** `STA_CORNERS` (or `--corners`). Only `nom_*` corners are
  supported, because they are read with the nominal SPEF.

**Locations.** These are environment variables, with command-line overrides.
The defaults follow `scripts/sweep/sweeplib.py`:

| Variable | Meaning | Default |
|---|---|---|
| `PE_SIF` (`--sif`) | LibreLane image | `$PE_FLOW_ROOT/librelane-3.1.0.dev3.sif` |
| `PE_FLOW_ROOT` | flow mirror (image, PDK) | `$PE_WORK/sram-flow` |
| `PE_WORK` | work directory | `<repo>/build/tt-work` |
| `PE_PDK_ROOT` or `PDK_ROOT` (`--pdk-root`) | PDK root holding `ihp-sg13cmos5l/` | `$PE_FLOW_ROOT/pdk` |
| `PE_APPTAINER` | apptainer executable | `apptainer` on `PATH` |

The image and the PDK are the ones the flow ran with: LibreLane 3.1.0.dev3
and IHP-Open-PDK `2bbec755` (see [sweep.md](../../docs/sweep.md), "Where
things live"). The host needs Python 3.6 or later (standard library only)
and apptainer.

## Usage

```sh
# local: prepare, run the corners (3 in parallel) and collect
python3 tools/sta/sta_retime.py all --run-dir <run> --period 20 --out <case dir> --jobs 3

# the same as a Slurm job (from the repository root; about 1 min per corner, 1.3 GB each)
sbatch -p <partition> --job-name=<name> --output=<log> \
    tools/sta/slurm_job.sh --run-dir <run> --period 20 --out <case dir>

# per-class shifts between two cases of one layout, or WS differences between layouts
python3 tools/sta/sta_retime.py compare <case dir A> <case dir B> --json <file>

# unit tests of the tool-free parts
python3 -m unittest tools/sta/test_sta_retime.py
```

The steps can also run one at a time: `prepare`, `run` and `collect`. Other
options:

- `--period` defaults to the run's own `CLOCK_PERIOD`, which gives control 1
  below.
- `--hash-sif` records the image's sha256. This reads the whole 1.3 GB image.

`slurm_job.sh` sets no partition. If apptainer is not on `PATH` it loads the
module named in `PE_APPTAINER_MODULE`.

## Outputs

| File | Contents |
|---|---|
| `case.json` | inputs with sha256 (source and decompressed), the libs of each corner with sha256, `env.tcl` values, the `--compare-env` result, and the image: path, size, optional sha256, LibreLane and OpenSTA versions, sha256 of the LibreLane scripts, apptainer version, and the git commit of this tool |
| `env.tcl` | the `_TCL_ENV_IN` file |
| `<corner>/` | `sta.log.gz`; the step's own report files (`max.rpt.gz`, `min.rpt.gz`, `checks.rpt`, `violator_list.rpt`, `ws/wns/tns.{max,min}.rpt`, `skew.*.rpt`, `power.rpt`, …); `metrics.json` (the `%OL_METRIC*` lines, split out the way LibreLane's `run_subprocess` does); `extra.txt`, `extra_paths.rpt`; `run.json` (command, environment, return code, time) |
| `results.json`, `results.md` | per-corner metrics, the worst path of each class, the consistency checks and the control |

`collect` exits non-zero in any of these cases:

- a corner failed;
- a control does not reproduce its metrics;
- the overall worst slack differs from the minimum over the four classes or
  from `corner.tcl`'s metric by more than 1e-6 ns.

## Two controls before any other number is trusted

A re-timed number is only as good as the claim that this is the flow's own
analysis. So two runs must first reproduce metrics that the flow itself
wrote:

1. **The same layout at its own period.** Run the layout whose period you
   change at the period it was built for (`--period` omitted or equal to
   `CLOCK_PERIOD` in `resolved.json`).
   - `collect` compares every per-corner metric that both sides report
     against the run's `metrics.csv`, and requires every one to be equal
     (maximum absolute difference 0).
   - The metrics are the ten timing metrics (setup and hold WS, TNS,
     violation counts, and reg-to-reg WS and counts), setup and hold WNS,
     clock skew, and the max-slew, max-cap and max-fan-out counts.
   - This shows that the environment, the libraries and the extra queries
     reproduce the flow's STAPostPNR step on that layout.
2. **An official build at its own period.** Run an official `tt_submission`
   artifact at its own `CLOCK_PERIOD`, and compare it with that artifact's
   `stats/metrics.csv` in the same way.
   - This shows that the method holds for the GitHub Actions build, not only
     for local runs.
   - It also gives the reference that the re-timed layout is compared with.

Only when both controls pass is the run at the other period trusted. That
run changes nothing but `CLOCK_PERIOD`.

## Per-class analysis

`base.sdc` makes two quantities depend on the period T:

- the clock period itself;
- the input and output delay X = T · `IO_DELAY_CONSTRAINT` / 100. This is set
  without `-min`/`-max`, so it applies to hold too.

Clock uncertainty, transition, derating, load and driving cell do not depend
on T. Neither does any data arrival or clock latency. So when the period
changes by ΔT (and X by ΔX), the slack of every path changes by an amount
fixed by its class:

| Class | Setup | Hold |
|---|---|---|
| r2r | +ΔT | 0 |
| in2reg | +ΔT − ΔX | +ΔX |
| reg2out | +ΔT − ΔX | +ΔX |
| in2out | +ΔT − 2ΔX | +2ΔX |

With `IO_DELAY_CONSTRAINT` 20, going from 15 to 20 ns gives ΔT = 5 and ΔX = 1.
Setup then gains 5, 4, 4 and 3 ns; hold gains 0, 1, 1 and 2 ns.

`compare A B` works on two cases of the same layout, that is, the same
netlist and SPEF sha256 and the same values of every other SDC variable. It
checks three things:

- **Class shift.** Each class's worst slack moves by the predicted amount,
  within 5e-6 ns.
- **Same path.** The class's worst path keeps its start and end points (this
  is reported, not required).
- **Overall worst slack.** The overall worst slack in B equals the minimum,
  over the classes, of (class slack in A + its shift).

The tolerance allows for two things. The class slacks are printed with 6
decimals, and OpenSTA keeps times as float32 seconds: the ULP is about
0.9e-6 ns at 8 to 15 ns and 1.8e-6 ns at 15 to 30 ns.

Two consequences:

- **The limiting class can change.** A design with combinational input to
  output paths gains less than ΔT − ΔX at the corners where such a path is the
  worst. At another corner the limiting class can switch, for example from
  r2r to in2reg.
- **LibreLane's r2r metric is not the r2r class.** `timing__setup_r2r__ws` is
  the minimum over end points whose single worst path is register to
  register. An end point can leave that set when the period changes, because
  its input-sourced path gains less. So its shift is not ΔT. The r2r class
  above (all register-to-register paths) moves by exactly ΔT.

For two different layouts, `compare` reports only the per-corner differences
of setup and hold WS.

## Files

| File | Purpose |
|---|---|
| `sta_retime.py` | the driver: `prepare`, `run`, `collect`, `all`, `compare` |
| `pe_extra.tcl` | the path-class queries sourced by `corner.tcl` |
| `slurm_job.sh` | optional Slurm body for `sta_retime.py all` |
| `test_sta_retime.py` | unit tests (encoding, view selection, class shifts) |
