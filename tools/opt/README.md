# tools/opt: physical-design optimizer

An autonomous search over LibreLane configuration knobs. It runs several
tracks, each with its own optuna TPE study: the 8x4 design of record at
CLOCK_PERIOD 20, 15 and 13.33 ns, the 6x4 `diet4` fallback of `variants6x4/`,
and one track per published variant core. It runs on the cluster through the
sweep harness (`scripts/sweep/`, used unchanged) and promotes the best
configurations of each track to a full run, the Tiny Tapeout precheck and the
gate-level tests. Tracks, method, search space, objective, the SDC analysis
behind the frequency tracks, budget and the adoption procedure are in
[`docs/optimization.md`](../../docs/optimization.md).

| File | Role |
|---|---|
| `space.py` | Search space: knobs, legal values, conditions; absolute knob values (`base_knobs()` of a committed configuration, `materialize()` into `make_snapshot.py` parameters and LibreLane overrides) |
| `tracks.py` | Tracks of a frozen tree (design of record at three periods, 6x4, variants), their committed configuration, track ids, and a mirror of `make_snapshot.py`'s config edit used for imports and config diffs |
| `objective.py` | Legality, the lexicographic ranking key, the scalar value TPE maximizes, per-corner fmax estimates |
| `checks.py` | Power-port checks: short VPWR/VGND straps in the GeneratePDN DEF, and the precheck's power-port rule on the final LEF |
| `store.py` | Append-only JSONL results store and the state rebuilt from it |
| `driver.py` | Main loop: tracks, imports of identical earlier trials, TPE studies, weighted allocation, retirement, submission within the CPU cap, polling, promotion, pruning, self-resubmission |
| `report.py` | `leaderboard.md` (one section per track), `leaderboard.csv`, `manifest.json` |
| `slurm.py` | squeue/sacct/sbatch/scancel helpers |
| `trial_job.sh` | Job body of a run: PDN watcher, `scripts/sweep/run_one.sh`, `postprocess.py` |
| `postprocess.py` | LEF check and worst setup paths of a finished run |
| `precheck_job.sh` | TT precheck on a promoted configuration (local reproduction of `docs/drc-triage.md`) |
| `gl_job.sh` | Gate-level cocotb subset on a promoted configuration's final netlist (`PE_VARIANT` per track) |
| `driver_job.sh` | Job body of the driver |
| `launch.sh` | Exports the committed tree, freezes the tools, submits the driver |
| `dry_run.sh` | Runs the driver against a copy of the store with nothing submitted |
| `test_opt.py` | Unit tests (standard library only): absolute knob values, 6x4 overlay equivalence, island-free halos, fmax |

```sh
export PE_WORK=<cluster work directory>        # holds sram-flow/, variants/, drc-triage/, cocotb/, host/
python3 tools/opt/test_opt.py                  # unit tests, no cluster needed
tools/opt/dry_run.sh $PE_WORK/<scratch dir>    # on a compute node: one planned loop, nothing submitted
tools/opt/launch.sh                            # export HEAD, submit the driver (no-op if one is queued)
$PE_WORK/optimizer/venv/bin/python $PE_WORK/optimizer/current/tools/opt/driver.py status
touch $PE_WORK/optimizer/STOP                  # stop: no new work; pending trial jobs are cancelled
```

The leaderboard is `$PE_WORK/optimizer/leaderboard.md`.
