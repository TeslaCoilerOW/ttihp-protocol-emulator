#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Optional Slurm body for formal_eq/eq_check.py: runs `eq_check.py check` with
# the given arguments; with --selftest the cases run in parallel on the
# allocated CPUs. Submit from the repository root (the job finds the tool
# through SLURM_SUBMIT_DIR, or through PE_EQ_TOOL):
#
#   sbatch -p <partition> --job-name=<name> --output=<log> \
#       formal_eq/slurm_job.sh --run-dir <tt_submission artifact> --variant diet4 --selftest --out <dir>
#
# The same script runs without Slurm: bash formal_eq/slurm_job.sh <args>.
# Environment: OSS_CAD_SUITE (yosys, yosys-abc), PE_PDK_ROOT or PDK_ROOT (or
# PE_FLOW_ROOT / PE_WORK), PE_VARIANT, PE_CORE (see eq_check.py).
# The exit status is eq_check.py's (0 only for 'equivalent').
#SBATCH --cpus-per-task=4
#SBATCH --mem=12G
#SBATCH --time=01:00:00
set -uo pipefail

TOOL=${PE_EQ_TOOL:-}
if [ -z "$TOOL" ]; then
  if [ -n "${SLURM_SUBMIT_DIR:-}" ] && [ -f "$SLURM_SUBMIT_DIR/formal_eq/eq_check.py" ]; then
    TOOL=$SLURM_SUBMIT_DIR/formal_eq
  else
    TOOL=$(cd "$(dirname "$0")" && pwd)
  fi
fi
[ -f "$TOOL/eq_check.py" ] || { echo "eq_check.py not found in '$TOOL' (set PE_EQ_TOOL)" >&2; exit 2; }

echo "job=${SLURM_JOB_ID:-local} host=$(hostname) start=$(date -Is) tool=$TOOL cpus=${SLURM_CPUS_PER_TASK:-1}"
rc=0
python3 "$TOOL/eq_check.py" check "$@" || rc=$?
echo "end=$(date -Is) rc=$rc"
exit $rc
