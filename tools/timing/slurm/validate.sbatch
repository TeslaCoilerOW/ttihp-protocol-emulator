#!/bin/bash
# Validate pe_timing static schedules against the reference model (one array task).
# Required env: TOOL (dir with pe_timing.py), REPO (repo root with test/ firmware/), OUT, SUITE.
# Optional env: COUNT (items per task), CYCLES, SEED, PY (python3 >= 3.10), EXTRA (extra image dir).
#SBATCH --job-name=pe-x-timing-validate
#SBATCH --cpus-per-task=1
#SBATCH --mem=3G
#SBATCH --time=01:30:00
#SBATCH --requeue
#SBATCH --output=%x-%A_%a.out
set -euo pipefail
: "${TOOL:?}" "${REPO:?}" "${OUT:?}" "${SUITE:?}"
IDX=${SLURM_ARRAY_TASK_ID:-0}
COUNT=${COUNT:-25}
CYCLES=${CYCLES:-30000}
SEED=${SEED:-0x7131}
PY=${PY:-python3}
FIRST=$((IDX * COUNT))
SUITE=${SUITE//+/,}
TAG=${SUITE//,/+}
ARGS=()
for s in ${SUITE//,/ }; do ARGS+=(--suite "$s"); done
if [ -n "${EXTRA:-}" ]; then ARGS+=(--extra-images "$EXTRA"); fi
mkdir -p "$OUT"
export PYTHONDONTWRITEBYTECODE=1
"$PY" "$TOOL/pe_timing.py" validate --repo "$REPO" --out "$OUT/${TAG}-${IDX}.json" \
    "${ARGS[@]}" --seed "$SEED" --count "$COUNT" --first "$FIRST" --cycles "$CYCLES" \
    > "$OUT/${TAG}-${IDX}.log" 2>&1
