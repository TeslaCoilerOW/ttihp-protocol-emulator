#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Run line LINE of a sweep task table (plan.py) and write
# <runs>/<run_id>/result.json.
#
#   fpga/scripts/sweep/run_task.sh TASKS_TSV LINE
#
# Task table columns (tab-separated): run_id, build_dir (a build.sh
# SYNTH_ONLY=1 output), PLACER:SEED, options ("K=V K=V", or "-"; the build.sh
# place-and-route options TIMING_WEIGHT, PNR_SETTINGS, ROUTER, REGION, PNR_PERIOD,
# NEXTPNR_*). Results go to $SWEEP_RUNS (default: <tasks dir>/runs/<table name>).
# A line whose result.json exists is skipped, so a requeued job resumes, and
# a line is claimed (atomic mkdir in <runs>/.claims) before it runs, so
# several workers can share one table.
set -uo pipefail
tsv="$(cd "$(dirname "${1:?TASKS_TSV}")" && pwd)/$(basename "$1")"
line="${2:?LINE}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
runs="${SWEEP_RUNS:-$(dirname "$tsv")/runs/$(basename "$tsv" .tsv)}"
IFS=$'\t' read -r rid bdir run opts < <(sed -n "${line}p" "$tsv")
[ -n "${rid:-}" ] || { echo "no line $line in $tsv" >&2; exit 2; }
out="$runs/$rid"
[ -f "$out/result.json" ] && exit 0
# worker.sbatch SWEEP_STOP_AFTER: start no new run after the deadline
[ -n "${SWEEP_DEADLINE:-}" ] && [ "$(date +%s)" -ge "$SWEEP_DEADLINE" ] && exit 0
mkdir -p "$runs/.claims"
mkdir "$runs/.claims/$rid" 2>/dev/null || exit 0
echo "${SWEEP_WORKER:-}" > "$runs/.claims/$rid/owner"
rm -rf "$out"
mkdir -p "$out"
envs=()
[ "$opts" = - ] || read -r -a envs <<< "$opts"
t0=$(date +%s)
# SWEEP_RUN_TIMEOUT=SECONDS (environment): stop a run that takes longer
# (router1 runs have a long tail); it is recorded as failed ("timeout").
tmo=()
[ -n "${SWEEP_RUN_TIMEOUT:-}" ] && tmo=(timeout "$SWEEP_RUN_TIMEOUT")
"${tmo[@]}" env "${envs[@]}" bash "$here/../pnr_run.sh" "$bdir" "$run" "$out" > "$out/pnr_run.txt"
[ $? = 124 ] && echo timeout > "$out/timeout"
t1=$(date +%s)
python3 "$here/result.py" "$out" "$rid" "$bdir" "$run" "$opts" "$((t1 - t0))" "$(hostname)" > "$out/result.json.tmp" &&
  mv "$out/result.json.tmp" "$out/result.json"
gzip -f "$out/nextpnr.log" 2>/dev/null || true
rm -f "$out/pnr_run.txt"
