#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Optional Slurm body for tools/sta/sta_retime.py: runs `sta_retime.py all`
# with the given arguments, the corners in parallel on the allocated CPUs.
# Submit from the repository root (the job finds the tool through
# SLURM_SUBMIT_DIR, or through PE_STA_TOOL):
#
#   sbatch -p <partition> --job-name=<name> --output=<log> \
#       tools/sta/slurm_job.sh --run-dir <run> --period 20 --out <case dir> --hash-sif
#
# The same script runs without Slurm: bash tools/sta/slurm_job.sh <args>.
# Environment: PE_SIF / PE_FLOW_ROOT / PE_WORK, PE_PDK_ROOT or PDK_ROOT,
# PE_APPTAINER (see sta_retime.py), and optionally PE_APPTAINER_MODULE, an
# environment module to load when apptainer is not on PATH.
#SBATCH --cpus-per-task=3
#SBATCH --mem=4G
#SBATCH --time=00:15:00
set -euo pipefail

TOOL=${PE_STA_TOOL:-}
if [ -z "$TOOL" ]; then
  if [ -n "${SLURM_SUBMIT_DIR:-}" ] && [ -f "$SLURM_SUBMIT_DIR/tools/sta/sta_retime.py" ]; then
    TOOL=$SLURM_SUBMIT_DIR/tools/sta
  else
    TOOL=$(cd "$(dirname "$0")" && pwd)
  fi
fi
[ -f "$TOOL/sta_retime.py" ] || { echo "sta_retime.py not found in '$TOOL' (set PE_STA_TOOL)" >&2; exit 2; }

if [ -z "${PE_APPTAINER:-}" ] && ! command -v apptainer >/dev/null 2>&1 && [ -n "${PE_APPTAINER_MODULE:-}" ]; then
  set +u
  source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null || true
  module load "$PE_APPTAINER_MODULE"
  set -u
fi

echo "job=${SLURM_JOB_ID:-local} host=$(hostname) start=$(date -Is) tool=$TOOL"
rc=0
python3 "$TOOL/sta_retime.py" all --jobs "${SLURM_CPUS_PER_TASK:-3}" "$@" || rc=$?
echo "end=$(date -Is) rc=$rc"
exit $rc
