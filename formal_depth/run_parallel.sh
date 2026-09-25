#!/usr/bin/env bash
# Run every task of WORK/tasks-CLASS.tsv inside one allocation, P at a time
# (for short smoke runs where a Slurm array would hit per-user submit limits).
#   srun -p mit_quicktest -c 32 --mem=96G -t 15 formal_depth/run_parallel.sh WORK CLASS 16
set -u
WORK=$1; CLS=$2; P=${3:-8}
FD=${FD_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
export PATH=${OSS_CAD_SUITE:?set OSS_CAD_SUITE to the OSS CAD Suite root}/bin:$OSS_CAD_SUITE/py3bin:$PATH
mkdir -p "$WORK/logs"
n=$(wc -l < "$WORK/tasks-$CLS.tsv")
seq 0 $((n - 1)) | xargs -P "$P" -I{} sh -c \
  "SLURM_ARRAY_TASK_ID={} python3 '$FD/portfolio.py' run '$WORK' '$CLS' {} > '$WORK/logs/local-$CLS-{}.out' 2>&1"
echo "run_parallel: $n tasks done"
