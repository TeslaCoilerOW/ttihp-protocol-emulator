#!/bin/bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# One Slurm array task of the random-differential campaign.
#   run_task.sh <campaign.env> [task-id]      (task-id defaults to SLURM_ARRAY_TASK_ID)
# Runs SEEDS_PER_TASK seeds, SLURM_CPUS_PER_TASK of them in parallel (one vvp process
# per seed, 1 CPU each). Seed ordinal k = task*SEEDS_PER_TASK + j + 1 (1-based),
# seed = SEED_OFFSET + k. Each seed runs campaign_random.py against the prebuilt
# sim.vvp and writes OUTDIR/results/seed-<seed>.json atomically at the end.
# Idempotent: a seed whose JSON exists is skipped, so a preempted and requeued task
# only redoes the seeds that were in flight. Scratch goes to node-local TMPDIR and is
# deleted after every seed.
set -u
CFG=$1
TASK=${2:-${SLURM_ARRAY_TASK_ID:?no task id}}
# shellcheck disable=SC1090
source "$CFG"
source ${PE_WORK:?set PE_WORK to the cluster work directory}/cocotb/env20.sh

PYBIN=${PE_WORK:?set PE_WORK to the cluster work directory}/cocotb/venv20/bin/python3
LIBDIR=$("$PYBIN" -m cocotb_tools.config --lib-dir)
LIBNAME=$("$PYBIN" -m cocotb_tools.config --lib-name vpi icarus)
LIBPY=$("$PYBIN" -m cocotb_tools.config --libpython)
LANES=${LANES:-${SLURM_CPUS_PER_TASK:-1}}
mkdir -p "$OUTDIR/results" "$OUTDIR/failures"

run_seed() {
  local seed=$1 out work t0 t1 rc
  out=$(printf '%s/results/seed-%08x.json' "$OUTDIR" "$seed")
  work=$(mktemp -d "${TMPDIR:-/tmp}/pe-vcamp.XXXXXX")
  t0=$(date +%s)
  (
    cd "$work" || exit 99
    export COCOTB_TEST_MODULES=campaign_random COCOTB_TOPLEVEL=tb TOPLEVEL_LANG=verilog
    export PYGPI_PYTHON_BIN=$PYBIN LIBPYTHON_LOC=$LIBPY COCOTB_RESULTS_FILE=$work/results.xml
    export PYTHONPATH=$CAMP:$SNAP/test PYTHONDONTWRITEBYTECODE=1
    export VCAMP_SEED=$seed VCAMP_RESULT=$out VCAMP_FAIL_DIR=$OUTDIR/failures VCAMP_LABEL=$LABEL
    export VCAMP_VARIANT=$VARIANT VCAMP_COMMIT=$COMMIT VCAMP_NETLIST=${NETLIST:-}
    # Design variant (config DESIGN_VARIANT, default base) configures the model; the
    # matching core is compiled into SIMVVP by build.sh. GENERATION pins the generator.
    if [ -n "${DESIGN_VARIANT:-}" ]; then export PE_VARIANT=$DESIGN_VARIANT; fi
    if [ -n "${GENERATION:-}" ]; then export VCAMP_GEN=$GENERATION; fi
    export PE_RANDOM_ITERS=$ITERS PE_RANDOM_CYCLES=$CYCLES PE_RANDOM_FIRST=0 PE_MINIMIZE=0
    if [ "$MODE" = gl ]; then export PE_GATE_LEVEL=1; fi
    # Optional negative control (config INJECT=xor): corrupt the model after XOR.
    if [ -n "${INJECT:-}" ]; then export PE_INJECT_MODEL_BUG=$INJECT VCAMP_SAVE_CASES=0; fi
    export VCAMP_XCOV=${XCOV:-0}
    exec timeout --signal=TERM "$SEED_TIMEOUT" vvp -M "$LIBDIR" -m "$LIBNAME" "$SIMVVP"
  ) >"$work/log" 2>&1
  rc=$?
  t1=$(date +%s)
  if [ ! -s "$out" ]; then
    if [ "$rc" -ne 124 ] && [ "$rc" -ge 128 ]; then
      # Killed by a signal (preemption): leave no result so the requeued task redoes it.
      echo "seed $seed: killed rc=$rc"
      rm -rf "$work"
      return
    fi
    # Timeout (124) or infrastructure error: record a small stub with the log tail so
    # the merge sees it (delete the stub to retry).
    "$PYBIN" - "$out" "$seed" "$rc" "$work/log" "$LABEL" <<'EOF'
import json, sys, os, socket
out, seed, rc, log, label = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
tail = open(log, errors="replace").read().splitlines()[-60:] if os.path.exists(log) else []
json.dump({"schema": "pe-vcamp.random.v1", "label": label, "seed": seed, "infra_error": True,
           "kind": "timeout" if rc == 124 else "no-result", "rc": rc, "host": socket.gethostname(),
           "slurm_job": os.environ.get("SLURM_JOB_ID", ""), "log_tail": tail}, open(out, "w"), indent=1)
EOF
    echo "seed $seed: NO RESULT rc=$rc ($((t1 - t0)) s)"
    gzip -c "$work/log" >"$(printf '%s/failures/%s-seed-%08x.log.gz' "$OUTDIR" "$LABEL" "$seed")"
  else
    echo "seed $seed: rc=$rc $((t1 - t0)) s $(grep -o '"cases_failed": [0-9]*' "$out")"
    if [ -z "${INJECT:-}" ] && ! grep -q '"cases_failed": 0,' "$out"; then
      gzip -c "$work/log" >"$(printf '%s/failures/%s-seed-%08x.log.gz' "$OUTDIR" "$LABEL" "$seed")"
    fi
  fi
  rm -rf "$work"
}

echo "task $TASK on $(hostname): $LANES lanes, config $CFG"
for ((j = 0; j < SEEDS_PER_TASK; j++)); do
  k=$((TASK * SEEDS_PER_TASK + j + 1))
  [ "$k" -gt "$NSEEDS" ] && break
  seed=$((SEED_OFFSET + k))
  [ -s "$(printf '%s/results/seed-%08x.json' "$OUTDIR" "$seed")" ] && continue
  while [ "$(jobs -rp | wc -l)" -ge "$LANES" ]; do wait -n; done
  run_seed "$seed" &
done
wait
echo "task $TASK done"
