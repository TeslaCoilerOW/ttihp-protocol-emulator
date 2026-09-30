# Timing certificates

SymbiYosys certificates that tie the static firmware timing analyzer
(`tools/timing/pe_timing.py`) to the RTL. For every node of an image's
boundary graph, a certificate proves on the design of record that engine K's
pads change exactly on the edges the analyzer predicts, up to the next
synchronization boundary. What is certified, what is assumed and the results
are in [`docs/timing-certificates.md`](../../../docs/timing-certificates.md).

| file | role |
|---|---|
| `gen_cert.py` | Generator. `emit IMAGE...` runs `pe_timing.Analysis` on each image and writes one harness (`cert_<image>_n<node>.sv`) and one `.sby` per boundary-graph node, plus `manifest.json`. `--chunk N` also splits segments longer than N steps into chained chunks (`_c<j>`). `--mutant` (a `pe_validate.MUTANTS` analyzer) and `--schedule-mutant` (one perturbed prediction, including `branch-late` for soft branch nodes) emit negative controls; a node whose mutated schedule contradicts itself is skipped and listed under `not_generated` in the manifest, so no mutant stops the emission. `emit-lemmas` writes `boundary_lemmas.sby` and `sram_lemma.sby`. `crosscheck` re-derives every schedule cycle by cycle and compares it with the analyzer's. `preflight IMAGE...` runs, in memory, every emission `campaign.sh prepare` and `ci_prove.sh` do for an image and reports whether it can be certified and how many CI runs it needs (about 1 s for the 19 images of `24f31f0`, about 20 s with the six deep images added after it). `--chunk-controls` (with `--schedule-mutant` and `--chunk`) perturbs the cheapest chunk certificate of a long segment instead of a whole segment. `obligations()` digests exactly what `emit` writes for an image (for the ledger). |
| `ledger.py` | The certificate ledger (`results/summary.json`). `check` fails unless every `firmware/*.image.json` is certified for its current sha256 on the current RTL, SRAM models, harness includes and lemma files, and the generator still emits the proved certificates; `stale` lists the images `check` flags; `plan` picks the images CI re-proves (with `--budget`, leaving out those over the CI run budget; with `--rtl`, it first fails when `cert_dut.vh` does not fit that `processor_fv.v`, e.g. a design variant's); with a design selection other than the design of record, `check` says that the ledger's campaigns certified the design of record; `fingerprint` and `record` bind a campaign's results to its commit and hashes. Standard library only. |
| `ci_prove.sh` | Proves the certificates of some images on one machine (the `certs` workflow): whole segments up to 96 steps, chunk chains for longer ones, covers, and the negative controls of BMC depth up to 98 (96 steps); a portfolio run that ends in ERROR is rerun with yices alone (`retry_list.py`). An image needing more than `CI_MAX_RUNS` (150) runs is reported and skipped (the workflow does not start a job for it: `ledger.py plan --budget`). |
| `ci_lemmas.sh` | Proves the 12 boundary/SRAM lemma tasks on one machine (the `certs` workflow, when the lemmas, `cert_dut.vh`, the generator or the RTL inputs change). |
| `cert_dut.vh` | The `protocol_processor_fv` instance (`formal/gen/generate_fv.ml`) with the IHP FUNCTIONAL SRAM models, and the engine-K observation wires. |
| `cert_env.vh` | The certificate run B: engine K's state at the anchor, the image in K's SRAM, and the quiet rest of the chip. |
| `boundary_lemmas.sv` | Image-independent one-step lemmas (START, PULL, PUSH, WAITPIN, WAITEVENT, fetch, stopped engine, synchronizer), in any environment. |
| `sram_lemma.sv` | Data integrity of one IHP SRAM macro model: a read returns the last word written (IC3/PDR, unbounded). |
| `equiv_src.py` | Sequential equivalence of `protocol_processor_fv` with the committed top (`src/project.v` + `src/protocol_emulator_core.v`) on the chip ports: a yosys miter, AIGER, ABC `dprove`. `--gate NETLIST --bmc-only 30` runs the negative control (the `rtl_sync_3flop` netlist; a counterexample is expected). `--run` writes `result.json`. |
| `rtl_mutants.py` | Netlists with one RTL timing bug each (private copy of `hardcaml/lib`, then `formal/run.sh --generate-only`), for running the real certificates as negative controls. |
| `retry_list.py` | The runs to repeat after a batch (`campaign.sh retry`, `ci_prove.sh`): a run whose results are all ERROR (e.g. boolector killed for running out of memory, which makes sby stop the whole portfolio) again with yices alone; with `--missing`, a run without a result again as it was (a solver portfolio as yices alone). ABC/rIC3 runs are not repeated. |
| `run_one.sh` | Runs one certificate task (optionally one solver of its portfolio, or several joined with `+`) and writes a result JSON. With ABC `bmc3` a counterexample is replayed by Yosys `sim` (sby option `vcd_sim`). With `CERT_PRUNE_PASSED=1` (set by `certify`) a run that passed keeps only its log and result file. |
| `array.sbatch` | Slurm array task: a bundle of task-list lines, run in parallel. |
| `campaign.sh` | `prepare` (git archive, formal RTL, all certificates, negatives, lemmas, the `rtl_sync_3flop` netlist for the equivalence control, task lists, `fingerprint.json`), `submit` (Slurm arrays), `retry` (after the arrays: the `retry_list.py` runs as one more array; with `record`, then the record job), `summarize`, `record` (summarize, then `ledger.py record`), and `certify` (all of it, for the images `ledger.py check` flags and `gen_cert.py preflight` accepts, ending with `retry WORK record`; `CERT_DRY_RUN=1` stops after prepare). |
| `summarize.py` | Result tables from the manifests and result files. |
| `test_gen_cert.py`, `test_ledger.py`, `test_retry_list.py` | Unit tests of the generator (including every negative control of every image), of the staleness check and of the rerun list (`python3 -m unittest discover -s tools/timing/cert -p 'test_*.py'`). |
| `results/` | `summary.json`: the ledger (per image: sha256, campaign, obligation digests, proofs, covers, negative controls; per campaign: commit, RTL, model and include hashes, lemmas, equivalence, Slurm jobs; superseded records). `summary.md`: the same as tables. `campaigns/<id>.json`, `.md`: every campaign's full results, kept when superseded. `bmc3_neg_evidence.json`: ABC logs of four deep negative controls at c118027. |

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

# Is every committed image certified for its current sha256? (seconds; CI job `certs`)
python3 tools/timing/cert/ledger.py check

# Certify what check flags (new or changed images, RTL, includes or generator
# output changed): one command on the cluster, from a clean committed tree.
# OSS_CAD_SUITE and OCAML_ENV as in formal/run.sh. It submits a short job that
# snapshots REV (default HEAD), prepares and submits the arrays, and a retry
# job that reruns the runs that ended in ERROR (yices alone) and then submits
# the job that records the campaign in results/. Then check again and commit
# tools/timing/cert/results/.
tools/timing/cert/campaign.sh certify $WORK
CERT_ONLY="uart-rx-idle" tools/timing/cert/campaign.sh certify $WORK   # some of them
CERT_DRY_RUN=1 tools/timing/cert/campaign.sh certify $WORK             # prepare only: task counts

# Can these images be certified, and how many CI runs do they need? (seconds)
python3 tools/timing/cert/gen_cert.py preflight firmware/*.image.json

# Whole campaign by hand (as for 24f31f0), with the RTL mutants
CERT_RTL_MUTANTS=1 tools/timing/cert/campaign.sh prepare $WORK 24f31f0
tools/timing/cert/campaign.sh submit $WORK short chunks cover neg lemma whole_long rtlneg equiv
tools/timing/cert/campaign.sh retry $WORK        # after the arrays: ERROR runs again, yices alone
tools/timing/cert/campaign.sh record $WORK       # after that: summarize + ledger.py record

# A run that still did not finish, by hand; then record again
tools/timing/cert/run_one.sh $WORK/long cert_uart_rx_idle_n2_c0 cover yices

# The certificates of one image on one machine, as CI runs them; the lemmas
tools/timing/cert/ci_prove.sh $WORK/ci formal/build/rtl models firmware/uart-tx.image.json
tools/timing/cert/ci_lemmas.sh $WORK/ci formal/build/rtl models
```

The generator and the ledger need only the Python standard library and
`tools/timing/`. Running the certificates needs OSS CAD Suite 2026-07-29
(yosys 0.67, sby, yices, bitwuzla, boolector, abc). Each run keeps its sby
log and traces and deletes the copied sources. Images in `firmware/ext/`
target the extension variant's ISA and are not in the certified set.

## Harness conventions

- Step 0 of a certificate is the state right after the anchor edge (the
  START edge or the edge on which the previous boundary completed); step `n`
  is `n` edges later, the analyzer's offset `+n`.
- A soft branch node (a data-dependent `JZ`/`LOOP` in a loop without a
  blocking instruction) starts one step earlier, in the arrival state: the
  branch is attempted at step 0 and issues on the anchor edge, so each of its
  step numbers is one larger than for a boundary node.
- An instruction the analyzer issues at `+t` is attempted in step `t-1`:
  engine K is running, not faulted, its WAIT timer and XFER edge counter are
  zero, and its PC is the instruction's.
- Pad states use the analyzer's encoding: `1` driven low, `2` driven high,
  `4` released.
- Assertion labels: `flow`, `pad_v<i>_p<pin>`, `edge_v<i>_p<pin>`,
  `sample_v<i>_<n>`, `post_v<i>`, `env_push_v<i>_<n>`, `env_quiet`,
  `env_no_sram_write`; covers `cover_v<i>`.
