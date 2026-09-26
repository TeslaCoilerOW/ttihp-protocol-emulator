#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Submit the demonstration's simulation-validation matrix to Slurm, one
# single-CPU job per case (demo/sim/run_case.sh), then collect with
# demo/sim/collect.py.
#
#   DEMO_WORK=/path/to/work [DEMO_ENV=env.sh] [SIGROK_CLI=...] \
#   DEMO_SBATCH_ARGS="-p PARTITION --requeue" demo/sim/submit.sh [--dry-run]
#
# Job names are ${DEMO_JOB_PREFIX:-pe-demo}-<case>. Job ids are appended to
# DEMO_WORK/jobs.tsv. The script runs the tree it lives in; freeze a copy of
# the repository first when the working tree may change during the run.

set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
: "${DEMO_WORK:?set DEMO_WORK}"
prefix=${DEMO_JOB_PREFIX:-pe-demo}
dry=${1:-}
mkdir -p "$DEMO_WORK/logs"

# tag tb mode core seed cycles [test]
cases=$(cat <<'EOF'
pins-idle-base-1 pins idle base 1 200000
pins-loaded-base-1 pins loaded base 1 200000
pins-loaded-base-2 pins loaded base 2 200000
pins-loaded-base-3 pins loaded base 3 200000
pins-idle-timer-host-1 pins idle timer-host 1 200000
pins-loaded-timer-host-1 pins loaded timer-host 1 200000
pins-idle-timer-engine-1 pins idle timer-engine 1 200000
pins-loaded-timer-engine-1 pins loaded timer-engine 1 200000
pins-idle-host-fetch-1 pins idle host-fetch 1 200000
pins-loaded-host-fetch-1 pins loaded host-fetch 1 200000
pins-loaded-engine-fetch-1 pins loaded engine-fetch 1 200000
bridge-idle-base-1 bridge idle base 1 600000
bridge-loaded-base-1 bridge loaded base 1 600000
bridge-loaded-base-2 bridge loaded base 2 600000
bridge-idle-timer-host-1 bridge idle timer-host 1 600000
bridge-loaded-timer-host-1 bridge loaded timer-host 1 600000
bridge-loaded-timer-engine-1 bridge loaded timer-engine 1 600000
bridge-idle-host-fetch-1 bridge idle host-fetch 1 600000
bridge-loaded-host-fetch-1 bridge loaded host-fetch 1 600000
four-1 pins - base 1 0 test_four
bridge-chain-1 pins - base 1 130000 test_bridge_chain
EOF
)

while read -r tag tb mode core seed cycles test; do
  [ -z "$tag" ] && continue
  test=${test:-test_isolation}
  cmd="bash $HERE/run_case.sh $tag $tb $mode $core $seed $cycles $test"
  if [ "$dry" = --dry-run ]; then
    echo "$cmd"
    continue
  fi
  # shellcheck disable=SC2086
  id=$(sbatch --parsable ${DEMO_SBATCH_ARGS:-} -c 1 --mem=4G --time=03:00:00 -J "$prefix-$tag" \
         --output="$DEMO_WORK/logs/$tag.out" --export=ALL --wrap "$cmd")
  printf '%s\t%s\t%s\n' "$id" "$prefix-$tag" "$cmd" >> "$DEMO_WORK/jobs.tsv"
  echo "$id $prefix-$tag"
done <<< "$cases"
