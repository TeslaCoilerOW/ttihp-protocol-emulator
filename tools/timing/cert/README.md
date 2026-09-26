# Timing certificates

SymbiYosys certificates that tie the static firmware timing analyzer
(`tools/timing/pe_timing.py`) to the RTL. For every node of an image's
boundary graph, a certificate proves on the design of record that engine K's
pads change exactly on the edges the analyzer predicts, up to the next
synchronization boundary. What is certified, what is assumed and the results
are in [`docs/timing-certificates.md`](../../../docs/timing-certificates.md).

| file | role |
|---|---|
| `gen_cert.py` | Generator. `emit IMAGE...` runs `pe_timing.Analysis` on each image and writes one harness (`cert_<image>_n<node>.sv`) and one `.sby` per boundary-graph node, plus `manifest.json`. `--chunk N` also splits segments longer than N steps into chained chunks (`_c<j>`). `--mutant` (a `pe_validate.MUTANTS` analyzer) and `--schedule-mutant` (one perturbed prediction) emit negative controls. `emit-lemmas` writes `boundary_lemmas.sby` and `sram_lemma.sby`. `crosscheck` re-derives every schedule cycle by cycle and compares it with the analyzer's. |
| `cert_dut.vh` | The `protocol_processor_fv` instance (`formal/gen/generate_fv.ml`) with the IHP FUNCTIONAL SRAM models, and the engine-K observation wires. |
| `cert_env.vh` | The certificate run B: engine K's state at the anchor, the image in K's SRAM, and the quiet rest of the chip. |
| `boundary_lemmas.sv` | Image-independent one-step lemmas (START, PULL, PUSH, WAITPIN, WAITEVENT, fetch, stopped engine, synchronizer), in any environment. |
| `sram_lemma.sv` | Data integrity of one IHP SRAM macro model: a read returns the last word written (IC3/PDR, unbounded). |
| `equiv_src.py` | Sequential equivalence of `protocol_processor_fv` with the committed top (`src/project.v` + `src/protocol_emulator_core.v`) on the chip ports: a yosys miter, AIGER, ABC `dprove`. `--gate` runs the negative control. |
| `rtl_mutants.py` | Netlists with one RTL timing bug each (private copy of `hardcaml/lib`, then `formal/run.sh --generate-only`), for running the real certificates as negative controls. |
| `run_one.sh` | Runs one certificate task (optionally one solver of its portfolio) and writes a result JSON. |
| `array.sbatch` | Slurm array task: a bundle of task-list lines, run in parallel. |
| `campaign.sh` | `prepare` (git archive, formal RTL, all certificates, negatives, lemmas, task lists), `submit` (Slurm arrays), `summarize`. |
| `summarize.py` | Result tables from the manifests and result files. |
| `test_gen_cert.py` | Unit tests of the generator (`python3 -m unittest discover -s tools/timing/cert -p 'test_*.py'`). |
| `results/` | Committed summary of the runs quoted in the docs. |

## Commands

```sh
# Certificates for one image (RTL from formal/run.sh --generate-only)
python3 tools/timing/cert/gen_cert.py emit firmware/uart-tx.image.json \
    --out $WORK/certs --rtl formal/build/rtl --models models
tools/timing/cert/run_one.sh $WORK/certs cert_uart_tx_n1 bmc          # portfolio
tools/timing/cert/run_one.sh $WORK/certs cert_uart_tx_n1 bmc bitwuzla # one solver
tools/timing/cert/run_one.sh $WORK/certs cert_uart_tx_n1 cover

# Negative controls: must FAIL
python3 tools/timing/cert/gen_cert.py emit firmware/uart-tx.image.json --out $WORK/neg \
    --rtl formal/build/rtl --models models --mutant wait-n-cycles --pick-one
python3 tools/timing/cert/gen_cert.py emit firmware/uart-tx.image.json --out $WORK/neg \
    --rtl formal/build/rtl --models models --schedule-mutant pad-late --pick-one --append

# Whole campaign on Slurm (OSS_CAD_SUITE and OCAML_ENV as in formal/run.sh)
tools/timing/cert/campaign.sh prepare $WORK c118027
tools/timing/cert/campaign.sh submit $WORK
tools/timing/cert/campaign.sh summarize $WORK
```

The generator needs only the Python standard library and `tools/timing/`.
Running the certificates needs OSS CAD Suite 2026-07-29 (yosys 0.67, sby,
yices, bitwuzla, boolector, abc). Each run keeps its sby log and traces and
deletes the copied sources.

## Harness conventions

- Step 0 of a certificate is the state right after the anchor edge (the
  START edge or the edge on which the previous boundary completed); step `n`
  is `n` edges later, the analyzer's offset `+n`.
- An instruction the analyzer issues at `+t` is attempted in step `t-1`:
  engine K is running, not faulted, its WAIT timer and XFER edge counter are
  zero, and its PC is the instruction's.
- Pad states use the analyzer's encoding: `1` driven low, `2` driven high,
  `4` released.
- Assertion labels: `flow`, `pad_v<i>_p<pin>`, `edge_v<i>_p<pin>`,
  `sample_v<i>_<n>`, `post_v<i>`, `env_push_v<i>_<n>`, `env_quiet`,
  `env_no_sram_write`; covers `cover_v<i>`.
