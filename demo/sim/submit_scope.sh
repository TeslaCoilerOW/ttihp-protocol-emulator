#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Submit the self-measured isolation matrix (docs/demo.md, "Self-measured
# isolation") as one Slurm array job, one CPU per case
# (demo/sim/run_scope_case.sh), then collect with demo/sim/collect_scope.py.
#
#   DEMO_WORK=/path/to/work [DEMO_ENV=env.sh] [DEMO_CONCURRENCY=8] \
#   DEMO_SBATCH_ARGS="-p PARTITION --requeue" demo/sim/submit_scope.sh [--dry-run]
#
# The array job is named ${DEMO_JOB_PREFIX:-pe-demo-scope}; its id is appended
# to DEMO_WORK/jobs.tsv. The script runs the tree it lives in; freeze a copy
# of the repository first when the working tree may change during the run.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
: "${DEMO_WORK:?set DEMO_WORK}"
mkdir -p "$DEMO_WORK/logs"
# tag mode core seed expect
cat > "$DEMO_WORK/scope-cases.txt" <<'CASES'
scope-idle-base-1 idle base 1 same
scope-loaded-base-1 loaded base 1 same
scope-loaded-base-2 loaded base 2 same
scope-idle-timer-host-1 idle timer-host 1 same
scope-loaded-timer-host-1 loaded timer-host 1 different
scope-loaded-timer-engine-1 loaded timer-engine 1 different
scope-loaded-engine-fetch-1 loaded engine-fetch 1 different
scope-idle-host-fetch-1 idle host-fetch 1 different
scope-loaded-host-fetch-1 loaded host-fetch 1 different
CASES
n=$(grep -c . "$DEMO_WORK/scope-cases.txt")
cmd='line=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" '"$DEMO_WORK"'/scope-cases.txt); bash '"$HERE"'/run_scope_case.sh $line'
if [ "${1:-}" = --dry-run ]; then
  cat "$DEMO_WORK/scope-cases.txt"
  exit 0
fi
# shellcheck disable=SC2086
id=$(sbatch --parsable ${DEMO_SBATCH_ARGS:-} --array=0-$((n - 1))%${DEMO_CONCURRENCY:-8} -c 1 --mem=8G \
       --time=04:00:00 -J "${DEMO_JOB_PREFIX:-pe-demo-scope}" --output="$DEMO_WORK/logs/%x-%A_%a.out" \
       --export=ALL --wrap "$cmd")
printf '%s\t%s\t%s\n' "$id" "${DEMO_JOB_PREFIX:-pe-demo-scope}" "run_scope_case.sh x $n" >> "$DEMO_WORK/jobs.tsv"
echo "$id"
