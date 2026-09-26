# tools/opt: physical-design optimizer

An autonomous search over LibreLane configuration knobs for the 8x4 design of
record and for the published variant cores. It runs on the cluster through the
sweep harness (`scripts/sweep/`, used unchanged) and promotes its best
configurations to a full run, the Tiny Tapeout precheck and the gate-level
tests. Method, search space, objective, budget and the adoption procedure are
in [`docs/optimization.md`](../../docs/optimization.md).

| File | Role |
|---|---|
| `space.py` | Search space: knobs, legal values, conditions, the designed first wave, mapping to `make_snapshot.py` parameters and LibreLane overrides |
| `objective.py` | Legality, the lexicographic ranking key and the scalar value TPE maximizes |
| `checks.py` | Power-port checks: short VPWR/VGND straps in the GeneratePDN DEF, and the precheck's power-port rule on the final LEF |
| `store.py` | Append-only JSONL results store and the state rebuilt from it |
| `driver.py` | Main loop: tracks, optuna TPE studies, submission within the CPU cap, polling, promotion, pruning, self-resubmission |
| `report.py` | `leaderboard.md`, `leaderboard.csv`, `manifest.json` |
| `slurm.py` | squeue/sacct/sbatch/scancel helpers |
| `trial_job.sh` | Job body of a run: PDN watcher, `scripts/sweep/run_one.sh`, `postprocess.py` |
| `postprocess.py` | LEF check and worst setup paths of a finished run |
| `precheck_job.sh` | TT precheck on a promoted configuration (local reproduction of `docs/drc-triage.md`) |
| `gl_job.sh` | Gate-level cocotb subset on a promoted configuration's final netlist |
| `driver_job.sh` | Job body of the driver |
| `launch.sh` | Exports the committed tree, freezes the tools, submits the driver |

```sh
export PE_WORK=<cluster work directory>        # holds sram-flow/, variants/, drc-triage/, cocotb/, host/
tools/opt/launch.sh                            # export HEAD, submit the driver (no-op if one is queued)
$PE_WORK/optimizer/venv/bin/python $PE_WORK/optimizer/current/tools/opt/driver.py status
touch $PE_WORK/optimizer/STOP                  # stop: no new work; pending trial jobs are cancelled
```

The leaderboard is `$PE_WORK/optimizer/leaderboard.md`.
