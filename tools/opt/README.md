# tools/opt: physical-design optimizer

An autonomous search over LibreLane configuration knobs. It runs several
tracks, each with its own optuna TPE study: the 8x4 design of record at
CLOCK_PERIOD 20, 15 and 13.33 ns, the 6x4 `diet4` fallback of `variants6x4/`,
and one track per published variant core. It runs on the cluster through the
sweep harness (`scripts/sweep/`, used unchanged) and promotes the best
configurations of each track to a full run, then the Tiny Tapeout precheck, the
gate-level tests and an RTL-vs-netlist equivalence check with
[`formal_eq/`](../../formal_eq/README.md) (verdict PASS only if all four
pass). Tracks, method, search space, objective, the SDC analysis behind the
frequency tracks, budget and the adoption procedure are in
[`docs/notes/optimization.md`](../../docs/notes/optimization.md).

| File | Role |
|---|---|
| `space.py` | Search space: knobs, legal values, conditions; absolute knob values (`base_knobs()` of a committed configuration, `materialize()` into `make_snapshot.py` parameters and LibreLane overrides); `FIXED` knobs (`CTS_MAX_SLEW`) that are no longer sampled; knobs of one space only (the post-CTS repair runtime knobs, 8x4) |
| `tracks.py` | Tracks of a frozen tree (design of record at three periods, 6x4, variants), their committed configuration, track ids, and a mirror of `make_snapshot.py`'s config edit used for imports and config diffs |
| `objective.py` | Legality, the lexicographic ranking key, the scalar value TPE maximizes, per-corner fmax estimates, the step runtimes of a run |
| `runtime.py` | Projection of the official gds job's length (GitHub stops it at 6 h) from a trial's or full run's step times, normalized by the node's synthesis speed; the promotion rule of the tracks the official actions build (15 ns, 13.33 ns and 6x4: projection at most 5.5 h, for the trial and, in the verdict, for the full run), their ranking key and TPE penalty |
| `checks.py` | Power-port checks: short VPWR/VGND straps in the GeneratePDN DEF, and the precheck's power-port rule on the final LEF |
| `gates.py` | Promotion verdict (full run, precheck, gate level, equivalence; in the runtime-rule tracks also the full run's projected official job length), the fail-closed reading of `formal_eq`'s `result.json` (verdict, exit code, schema, input sha256s, PDK root) and the retry policy of the equivalence stage (only a time limit is retried) |
| `clockdepth.py` | Clock depth of a final netlist (flip-flop and SRAM clock pins); a warning when the SRAM clocks are much deeper |
| `store.py` | Append-only JSONL results store and the state rebuilt from it |
| `driver.py` | Main loop: tracks, imports of identical earlier trials, TPE studies, weighted allocation, retirement, submission within the CPU cap, polling, promotion (with the runtime rule in the 15 ns, 13.33 ns and 6x4 tracks), pruning, self-resubmission |
| `report.py` | `leaderboard.md` (one section per track), `leaderboard.csv`, `manifest.json` |
| `slurm.py` | squeue/sacct/sbatch/scancel helpers |
| `trial_job.sh` | Job body of a run: PDN watcher, `scripts/sweep/run_one.sh`, `postprocess.py` |
| `postprocess.py` | LEF check, worst setup paths and clock depth of a finished run |
| `precheck_job.sh` | TT precheck on a promoted configuration (local reproduction of `docs/notes/drc-triage.md`) |
| `gl_job.sh` | Gate-level cocotb subset on a promoted configuration's final netlist (`PE_VARIANT` per track) |
| `eq_job.sh` | RTL-vs-netlist equivalence check on a promoted configuration's final netlist: `formal_eq/eq_check.py` of the frozen tree (`$PE_EQ_CHECK` overrides it) with the PDK root of the full run and yosys from `$OSS_CAD_SUITE` |
| `driver_job.sh` | Job body of the driver |
| `launch.sh` | Exports the committed tree (with `formal_eq/`), freezes the tools, checks the equivalence tools, submits the driver |
| `dry_run.sh` | Runs the driver against a copy of the store with nothing submitted |
| `test_opt.py` | Unit tests (standard library only): absolute knob values, 6x4 overlay equivalence, island-free halos, fmax, fixed knobs, promotion gates, the reading of `formal_eq`'s `result.json`, the equivalence retry policy, clock depth, the runtime knobs, the runtime model against its calibration data and the driver's runtime rule |

```sh
export PE_WORK=<cluster work directory>        # holds sram-flow/, variants/, drc-triage/, cocotb/, host/
export OSS_CAD_SUITE=<OSS CAD Suite root>      # yosys and yosys-abc of the equivalence stage
python3 tools/opt/test_opt.py                  # unit tests, no cluster needed
tools/opt/dry_run.sh $PE_WORK/<scratch dir>    # on a compute node: one planned loop, nothing submitted
tools/opt/launch.sh                            # export HEAD, submit the driver (no-op if one is queued)
$PE_WORK/optimizer/venv/bin/python $PE_WORK/optimizer/current/tools/opt/driver.py status
touch $PE_WORK/optimizer/STOP                  # stop: no new work; pending trial jobs are cancelled
```

The leaderboard is `$PE_WORK/optimizer/leaderboard.md`.
