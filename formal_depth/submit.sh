#!/usr/bin/env bash
# Plan a portfolio and submit one Slurm array per resource class.
#   formal_depth/submit.sh WORK [--only REGEX] [--class light|heavy]
# Resource classes: light = 2 CPUs / 8 GB, heavy = 2 CPUs / 32 GB. Both run
# on mit_preemptable or mit_normal (whichever starts first; --requeue).
# FD_PARTITION / FD_TIME override the partition list and time limit (e.g.
# FD_PARTITION=mit_quicktest FD_TIME=15 with plan --timeout 840 for smoke runs).
set -euo pipefail
FD=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=$1; shift
python3 "$FD/portfolio.py" plan "$WORK" "$@"
mkdir -p "$WORK/logs"
label=$(basename "$WORK")
for tsv in "$WORK"/tasks-*.tsv; do
  cls=$(basename "$tsv" .tsv); cls=${cls#tasks-}
  n=$(wc -l < "$tsv")
  [ "$n" -gt 0 ] || continue
  case $cls in
    heavy) mem=32G ;;
    *) mem=8G ;;
  esac
  part=${FD_PARTITION:-mit_preemptable,mit_normal}
  tlimit=${FD_TIME:-11:55:00}
  id=$(sbatch --parsable -p "$part" --requeue -c 2 --mem=$mem -t "$tlimit" \
       -J "pe-x-formal-depth-$label-$cls" --array=0-$((n-1)) \
       -o "$WORK/logs/%x.%A_%a.out" --export=ALL,FD_DIR="$FD" \
       "$FD/array.sbatch" "$WORK" "$cls")
  echo "$cls: $n tasks -> array job $id"
  printf '%s\t%s\t%s\t%s\n' "$(date -Is)" "$label" "$cls" "$id" >> "$WORK/submitted.tsv"
done
