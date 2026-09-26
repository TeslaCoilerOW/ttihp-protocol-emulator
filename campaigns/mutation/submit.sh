#!/usr/bin/env bash
# Submit one campaign stage as a Slurm job array (1 CPU per task).
#
# usage: submit.sh STAGE IDS_FILE NTASKS TIME [PARTITION] [extra sbatch args...]
#
# STAGE      fast | full | deep (run_mutant.py) or equiv* (equiv_mutant.py,
#            results in results/<STAGE>/)
# IDS_FILE   mutant ids, whitespace separated; task i runs ids at positions
#            i, i+NTASKS, ... and skips any id whose result file exists
# NTASKS     array size (each task takes one CPU)
# TIME       per-task wall time (e.g. 01:00:00)
# PARTITION  default mit_preemptable; tasks requeue on preemption and resume
#
# Environment: RUNNER_ARGS (extra run_mutant.py options, e.g. --deadline 600),
# CAMP (campaign work dir), default
# $PE_WORK/vcamp/mutation; MANIFEST (default $CAMP/../manifest.json);
# JOB_PREFIX (Slurm job-name prefix, default pe-vcamp-mut);
# SNAPSHOT_COMMIT (recorded in the manifest, default 73536f0).
# The design variant is a property of $CAMP/design (variant.txt, gen_mutants.sh).
set -euo pipefail
STAGE=$1
IDS=$(readlink -f "$2")
N=$3
T=$4
PART=${5:-mit_preemptable}
shift $(( $# < 5 ? $# : 5 ))
CAMP=${CAMP:-${PE_WORK:?set PE_WORK to the cluster work directory}/vcamp/mutation}
HERE=$(dirname "$(readlink -f "$0")")
PY=${PE_WORK:?set PE_WORK to the cluster work directory}/cocotb/venv20/bin/python3

# freeze the runner used by this submission (equiv* stages use the formal
# equivalence runner, the others the simulation runner)
case $STAGE in
  equiv*) SCRIPT=equiv_mutant ; STAGEARG= ;;
  *)      SCRIPT=run_mutant ; STAGEARG="--stage $STAGE" ;;
esac
SHA=$(sha256sum "$HERE/$SCRIPT.py" | cut -c1-12)
mkdir -p "$CAMP/bin" "$CAMP/logs" "$CAMP/results/$STAGE"
cp "$HERE/$SCRIPT.py" "$CAMP/bin/$SCRIPT-$SHA.py"

# PAR>1: each array task takes PAR CPUs and runs PAR runner processes
# (slice index task*PAR+j of N*PAR); for partitions that limit the job count
PAR=${PAR:-1}
RUN="$PY $CAMP/bin/$SCRIPT-$SHA.py --design $CAMP/design $STAGEARG --ids-file $IDS --out $CAMP/results/$STAGE --count $((N * PAR)) ${RUNNER_ARGS:-}"
JOB=$(sbatch --parsable -p "$PART" --requeue --open-mode=append \
  -J "${JOB_PREFIX:-pe-vcamp-mut}-$STAGE" -c "$PAR" --mem=$((3 * PAR))G -t "$T" --array=0-$((N - 1)) \
  -o "$CAMP/logs/$STAGE-%A_%a.out" "$@" \
  --wrap "for j in \$(seq 0 $((PAR - 1))); do $RUN --index \$((SLURM_ARRAY_TASK_ID * $PAR + j)) & done; wait")
echo "$JOB"
"$PY" "$HERE/manifest.py" "${MANIFEST:-$CAMP/../manifest.json}" mutation \
  job_id="$JOB" name="${JOB_PREFIX:-pe-vcamp-mut}-$STAGE" stage="$STAGE" partition="$PART" array_tasks="$N" \
  cpus_per_task="$PAR" ids_file="$IDS" ids="$(wc -w < "$IDS")" runner="bin/$SCRIPT-$SHA.py" \
  snapshot_commit="${SNAPSHOT_COMMIT:-73536f0}" \
  design_variant="$(cat "$CAMP/design/variant.txt" 2>/dev/null || echo base)" >/dev/null
