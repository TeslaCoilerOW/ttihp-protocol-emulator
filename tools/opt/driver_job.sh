#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Slurm job body of the optimizer driver (docs/optimization.md). Submitted by
# launch.sh and, before each time limit, by the driver itself (with
# --dependency=afterany on the running driver). Runs the driver of the tree that
# $PE_OPT_ROOT/current points to at start, so a relaunch with new tools is picked
# up by the next driver job. Preemption requeues the job; the driver resumes from
# the results store.
set -uo pipefail
: "${PE_WORK:?PE_WORK not set}"
OPT=${PE_OPT_ROOT:-$PE_WORK/optimizer}
TREE=$(readlink -f "$OPT/current")
PY=$OPT/venv/bin/python
echo "[driver_job $(date '+%F %T')] job ${SLURM_JOB_ID:-?} restart ${SLURM_RESTART_COUNT:-0} node $(hostname -s) tree $TREE"
cd "$OPT" || exit 2
exec "$PY" -u "$TREE/tools/opt/driver.py" run >> "$OPT/driver.log" 2>&1
