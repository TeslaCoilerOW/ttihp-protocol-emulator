# pe_timing: static cycle-exact timing analyzer

It needs only the Python standard library, version 3.10 or later. The method,
the guarantees and the results are in
[`docs/timing-analysis.md`](../../docs/timing-analysis.md).

| file | role |
|---|---|
| `pe_timing.py` | Decoder, abstract execution, boundary graph, event-distance queries, summaries and the CLI. |
| `pe_contracts.py` | Timing declarations parsed from image notes and `docs/firmware.md`, and the per-protocol checks: UART, SPI controller/target, I2C controller/target (UM10204 limits), JTAG, waveform, event transmitter. Also the flagship scenario checks and the report writer. |
| `pe_validate.py` | Ground truth. Traces the Python reference model (`test/model/`) and checks every observed edge against the static schedule. Contains the workload suites, the mutant negative controls and the merge step. |
| `pe_margins.py` | Margin experiments: the static protocol limits compared with sweeps of external timing in the model. |
| `test_pe_timing.py` | `unittest` tests. Set `PE_TIMING_REPO` to point at another checkout. |
| `slurm/validate.sbatch` | One Slurm array task of `validate`. |
| `report/` | Generated report for the committed images (`timing-report.md`, `checks.json`, `validation-summary.json`). |

## Commands

```sh
# One or more images: listing checks, boundary table, pin spacing, loop periods,
# protocol checks and per-segment edge schedules. Exit status 1 on any FAIL.
python3 tools/timing/pe_timing.py analyze firmware/i2c-write.image.json [--json out.json] \
    [--clock 25MHz,66MHz] [--issue scalar]

# Every image in a directory plus flagship-scenario.json.
# Writes report.md, report.json and checks.json.
python3 tools/timing/pe_timing.py report --firmware firmware --out build/timing \
    [--validation validation-summary.json]

# Ground truth against the reference model. Suites:
#   scenarios  test/scenarios.py (model only)
#   legacy     the 25 test_legacy.py workloads
#   event      event-transmitter with random EVENTs and a pin trigger
#   stress     every image under random host, pin and event traffic; --count runs per image
#   random     test/random_gen.py random programs; --count cases
#   mutants    7 deliberately wrong analyzers; each must be caught
#   margins    UART-RX baud, SPI-target and I2C-target timing sweeps
python3 tools/timing/pe_timing.py validate --repo . --out v.json --suite scenarios --suite legacy \
    [--suite stress --count 4 --cycles 30000] [--extra-images DIR] [--seed 0x7131 --first 0]

# Merge validation outputs, e.g. from Slurm array tasks.
python3 tools/timing/pe_timing.py merge out/*.json --out validation-summary.json

python3 -m unittest discover -s tools/timing -p 'test_*.py'
```

`validate` imports `test/model`, `test/harness.py`, `test/scenarios.py` and
`test/random_gen.py` from `--repo`. It wraps `Reference.tick`, `Reference._step`
and `Reference.command` for the duration of the run. Their behaviour is
unchanged; the wrapper only records it.

On Slurm, freeze a copy of the tool and submit arrays. Each task writes
`<suite>-<index>.json`; merge them afterwards.

```sh
sbatch -p mit_preemptable,mit_normal --requeue --array=0-127 --job-name=pe-x-timing-stress \
  --export=ALL,TOOL=$PWD/tools/timing,REPO=$PWD,OUT=$OUT,SUITE=stress,COUNT=25,CYCLES=30000 \
  tools/timing/slurm/validate.sbatch
```

## Output conventions

- **Offsets.** `+n` means n clock edges after the segment's anchor. The anchor
  is the edge on which the previous boundary completed: START, `PULL`,
  `PUSH a=0`, `WAITPIN`, `WAITEVENT`, or a soft branch boundary.
- **Pad states.** `0` and `1` mean the pad is driven; `Z` means released. `0|1`,
  `0|Z` and `?` mean the state depends on data.
- **Ranges.** A range `a..b` that crosses a boundary uses that boundary's
  minimum stall for `a`. For `b` it uses LIMIT-1 (`WAITPIN`/`WAITEVENT`) or
  `inf` (`PULL`/`PUSH`).
- **Status.** Checks are `PASS`/`FAIL` against a declaration, `WARN` for a
  hazard nothing declares, and `INFO` for derived numbers.
