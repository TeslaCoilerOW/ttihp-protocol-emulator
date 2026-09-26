#!/bin/bash
# One Slurm array task = BUNDLE consecutive lines of a task list, PAR at a
# time; each line is "CERTDIR NAME TASK [ENGINE]" for run_one.sh. Bundling
# keeps the number of queued jobs under the per-user submit limit.
#   sbatch -p <partitions> --requeue --array=0-N%M -c C --mem=... -t ... \
#       --export=ALL,TASKS=tasks.txt,BUNDLE=10,PAR=2,CERT_TOOL=.../tools/timing/cert,OSS_CAD_SUITE=... \
#       tools/timing/cert/array.sbatch
set -uo pipefail
[ -n "${OSS_CAD_SUITE:-}" ] && export PATH=$OSS_CAD_SUITE/bin:$PATH
HERE=${CERT_TOOL:?set CERT_TOOL to tools/timing/cert}
B=${BUNDLE:-1}
first=$((SLURM_ARRAY_TASK_ID * B + 1))
last=$((first + B - 1))
sed -n "${first},${last}p" "$TASKS" | grep -v '^\s*$' |
  xargs -P "${PAR:-1}" -L 1 bash -c '"$0"/run_one.sh "$@"' "$HERE"
