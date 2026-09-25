#!/bin/bash
# Slurm array runner for formal_depth/portfolio.py: one sby task (one solver
# engine) per array index. Submit through formal_depth/submit.sh.
#   sbatch --array=0-N ... array.sbatch WORK CLASS
#SBATCH --requeue
#SBATCH --open-mode=append
set -u
WORK=$1
CLS=$2
FD=${FD_DIR:-$(dirname "$(readlink -f "$0")")}
export PATH=${OSS_CAD_SUITE:?set OSS_CAD_SUITE to the OSS CAD Suite root}/bin:$OSS_CAD_SUITE/py3bin:$PATH
echo "host=$(hostname) job=${SLURM_ARRAY_JOB_ID:-}_${SLURM_ARRAY_TASK_ID:-} part=${SLURM_JOB_PARTITION:-} restarts=${SLURM_RESTART_COUNT:-0} start=$(date -Is)"
/usr/bin/time -v python3 "$FD/portfolio.py" run "$WORK" "$CLS" "$SLURM_ARRAY_TASK_ID" 2>&1 | grep --line-buffered -v "^\s*\(Average\|Page size\|Socket\|Signals\|Swaps\|File system\|Voluntary\|Involuntary\|Minor\|Major\|Exit status\|Percent\)"
echo "end=$(date -Is)"
