#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Slurm worker for a sweep task table: worker j of an array of W first runs
# the table lines l with (l - 1) mod W = j, then any line no other worker has
# claimed yet (run_task.sh), one nextpnr run per CPU (nextpnr runs are
# single-threaded). Lines with a result are skipped; after a requeue the
# worker releases its own unfinished claims, so it can be submitted with
# --requeue.
#
#   sbatch --array=0-7 -c 30 --mem=64G --requeue -J <name> \
#     fpga/scripts/sweep/worker.sbatch "$PWD/fpga/scripts/sweep" TASKS_TSV
#
# SWEEP_STOP_AFTER=SECONDS (environment): start no new run after that many
# seconds, so that a job with a short time limit ends with no run cut off.
# Needs OPENXC7 (and python3) in the job environment.
#SBATCH --time=06:00:00
set -uo pipefail
sweep="${1:?absolute path of fpga/scripts/sweep}"
tsv="${2:?TASKS_TSV}"
export OMP_NUM_THREADS=1
W=${SLURM_ARRAY_TASK_COUNT:-1}
j=${SLURM_ARRAY_TASK_ID:-0}
P=${SLURM_CPUS_PER_TASK:-$(nproc)}
# Run from a private copy of fpga/scripts, so that editing the repository
# during a sweep cannot change or break the scripts of running jobs.
priv=$(mktemp -d "${TMPDIR:-/tmp}/pe-sweep.XXXXXX")
trap 'rm -rf "$priv"' EXIT
cp -r "$sweep/.." "$priv/scripts"
sweep="$priv/scripts/sweep"
n=$(wc -l < "$tsv")
echo "worker $j/$W on $(hostname), $P parallel runs, $n lines"
export SWEEP_WORKER="${SLURM_ARRAY_JOB_ID:-$$}_$j"
if [ -n "${SWEEP_STOP_AFTER:-}" ]; then export SWEEP_DEADLINE=$(( $(date +%s) + SWEEP_STOP_AFTER )); fi
runs="${SWEEP_RUNS:-$(cd "$(dirname "$tsv")" && pwd)/runs/$(basename "$tsv" .tsv)}"
if [ -d "$runs/.claims" ]; then
  grep -lx "$SWEEP_WORKER" "$runs"/.claims/*/owner 2>/dev/null | while read -r o; do
    d=$(dirname "$o"); [ -f "$runs/$(basename "$d")/result.json" ] || rm -rf "$d"
  done
fi
{ awk -v W="$W" -v j="$j" '(NR - 1) % W == j {print NR}' "$tsv"; seq 1 "$n"; } |
  xargs -P "$P" -I{} bash "$sweep/run_task.sh" "$tsv" {}
echo "worker $j done"
