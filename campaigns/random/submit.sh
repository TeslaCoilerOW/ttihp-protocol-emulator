#!/bin/bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Write a campaign config and submit it as a Slurm array on mit_preemptable
# (--requeue, idempotent tasks; each task runs <cpus> seeds in parallel, one
# 1-CPU simulator per seed), recording the job id in the shared manifest.
# Array tasks count against the per-user submit limit (448 on mit_preemptable,
# shared), hence few multi-CPU tasks rather than many 1-CPU tasks.
#   submit.sh <label> <variant> <rtl|gl> <nseeds> <seed_offset> <seeds_per_task> \
#             <cases_per_seed> <host_cycles> <seed_timeout_s> <task_time> <cpus> <mem> <throttle>
# Seeds are SEED_OFFSET+1 .. SEED_OFFSET+NSEEDS (deterministic). Set DEPENDENCY=afterany:<job>
# to queue behind another array (keeps the total within the CPU share); INJECT=xor
# makes a negative-control campaign (model corrupted after XOR; failures expected);
# XCOV=1 adds the extended cross-coverage bins (xcov.py).
set -euo pipefail
LABEL=$1 VARIANT=$2 MODE=$3 NSEEDS=$4 OFFSET=$5 PER=$6 ITERS=$7 CYCLES=$8 TMO=$9
TTIME=${10} CPUS=${11} MEM=${12} THROTTLE=${13}
VCAMP=${PE_WORK:?set PE_WORK to the cluster work directory}/vcamp
R=$VCAMP/random
NETLIST=""
[ "$MODE" = gl ] && NETLIST=$R/netlist/tt_um_teslacoilerow_protocol_emulator.nl.v
mkdir -p "$R/configs" "$R/logs/$LABEL"
cat >"$R/configs/$LABEL.env" <<EOF
LABEL=$LABEL
VARIANT=$VARIANT
MODE=$MODE
NSEEDS=$NSEEDS
SEED_OFFSET=$OFFSET
SEEDS_PER_TASK=$PER
ITERS=$ITERS
CYCLES=$CYCLES
SEED_TIMEOUT=$TMO
SNAP=$VCAMP/snapshot-73536f0
CAMP=$R/scripts
SIMVVP=$R/build/$MODE/sim.vvp
OUTDIR=$R/$LABEL
COMMIT=73536f0bb6aff03e5e3579442e7fb4b5438a4d0d
NETLIST=$NETLIST
INJECT=${INJECT:-}
XCOV=${XCOV:-0}
EOF
NTASKS=$(((NSEEDS + PER - 1) / PER))
JOB=$(sbatch --parsable -J "pe-vcamp-$LABEL" -p mit_preemptable --requeue -c "$CPUS" --mem="$MEM" -t "$TTIME" \
  ${DEPENDENCY:+--dependency=$DEPENDENCY} --array="0-$((NTASKS - 1))%$THROTTLE" -o "$R/logs/$LABEL/%a.out" \
  "$R/scripts/run_task.sh" "$R/configs/$LABEL.env")
python3 "$R/scripts/manifest.py" "$VCAMP/manifest.json" job "pe-vcamp-$LABEL" "$JOB" partition=mit_preemptable \
  "seeds=$((OFFSET + 1))..$((OFFSET + NSEEDS))" "cases_per_seed=$ITERS" "host_cycles=$CYCLES" \
  "variant=$VARIANT" "mode=$MODE" "tasks=$NTASKS" "seeds_per_task=$PER" "cpus_per_task=$CPUS" "dependency=${DEPENDENCY:-}" "inject=${INJECT:-}" "xcov=${XCOV:-0}"
echo "$JOB"
