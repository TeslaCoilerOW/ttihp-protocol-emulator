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
#
# Other snapshots and design variants (every default reproduces the 73536f0 base campaign):
#   RC_ROOT=<dir>           campaign root with scripts/, build/, configs/, logs/ (default $PE_WORK/vcamp/random)
#   SNAP=<dir> COMMIT=<sha> frozen snapshot and its full commit id
#   DESIGN_VARIANT=<name>   PE_VARIANT of the tasks (build SIMVVP with build.sh under the same PE_VARIANT)
#   GENERATION=<n>          pin the generator generation (VCAMP_GEN); default: the snapshot's default
#   NETLIST=<file.v>        gate-level netlist recorded for gl campaigns (default $RC_ROOT/netlist/...)
#   SIMVVP=<file>           prebuilt simulation (default $RC_ROOT/build/<mode>/sim.vvp)
#   MANIFEST=<file>         manifest that records the job (default $PE_WORK/vcamp/manifest.json)
#   JOB_PREFIX=<prefix>     Slurm job-name prefix (default pe-vcamp)
#   PARTITION=<list>        Slurm partition list (default mit_preemptable)
set -euo pipefail
LABEL=$1 VARIANT=$2 MODE=$3 NSEEDS=$4 OFFSET=$5 PER=$6 ITERS=$7 CYCLES=$8 TMO=$9
TTIME=${10} CPUS=${11} MEM=${12} THROTTLE=${13}
VCAMP=${PE_WORK:?set PE_WORK to the cluster work directory}/vcamp
R=${RC_ROOT:-$VCAMP/random}
SNAP=${SNAP:-$VCAMP/snapshot-73536f0}
COMMIT=${COMMIT:-73536f0bb6aff03e5e3579442e7fb4b5438a4d0d}
MANIFEST=${MANIFEST:-$VCAMP/manifest.json}
JOB_PREFIX=${JOB_PREFIX:-pe-vcamp}
PARTITION=${PARTITION:-mit_preemptable}
if [ "$MODE" = gl ]; then
  NETLIST=${NETLIST:-$R/netlist/tt_um_teslacoilerow_protocol_emulator.nl.v}
else
  NETLIST=""
fi
SIMVVP=${SIMVVP:-$R/build/$MODE/sim.vvp}
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
SNAP=$SNAP
CAMP=$R/scripts
SIMVVP=$SIMVVP
OUTDIR=$R/$LABEL
COMMIT=$COMMIT
NETLIST=$NETLIST
INJECT=${INJECT:-}
XCOV=${XCOV:-0}
DESIGN_VARIANT=${DESIGN_VARIANT:-}
GENERATION=${GENERATION:-}
EOF
NTASKS=$(((NSEEDS + PER - 1) / PER))
JOB=$(sbatch --parsable -J "$JOB_PREFIX-$LABEL" -p "$PARTITION" --requeue -c "$CPUS" --mem="$MEM" -t "$TTIME" \
  ${DEPENDENCY:+--dependency=$DEPENDENCY} --array="0-$((NTASKS - 1))%$THROTTLE" -o "$R/logs/$LABEL/%a.out" \
  "$R/scripts/run_task.sh" "$R/configs/$LABEL.env")
python3 "$R/scripts/manifest.py" "$MANIFEST" job "$JOB_PREFIX-$LABEL" "$JOB" "partition=$PARTITION" \
  "seeds=$((OFFSET + 1))..$((OFFSET + NSEEDS))" "cases_per_seed=$ITERS" "host_cycles=$CYCLES" \
  "variant=$VARIANT" "mode=$MODE" "tasks=$NTASKS" "seeds_per_task=$PER" "cpus_per_task=$CPUS" "dependency=${DEPENDENCY:-}" "inject=${INJECT:-}" "xcov=${XCOV:-0}" \
  "design_variant=${DESIGN_VARIANT:-base}" "generation=${GENERATION:-snapshot default}" "commit=$COMMIT"
echo "$JOB"
