#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Slurm job body for one pe-sweep run (docs/sweep.md). submit.py submits a copy
# of this directory (frozen under <sweep>/harness/<sha>/) as a job array:
#
#   run_one.sh RUN_DIR          plain job
#   run_one.sh --list FILE      array task: line SLURM_ARRAY_TASK_ID+1 of FILE is RUN_DIR
#
# RUN_DIR holds snap/ and params.json (make_snapshot.py). The job
#   1. stages snap/ to node-local disk (/scratch/$USER, else /tmp/$USER, else
#      RUN_DIR/work on the pool) and verifies SNAPSHOT.sha256;
#   2. runs LibreLane 3.1.0.dev3 from the SIF exactly like tt-work/sram-flow/run.sh
#      (itself the local mirror of the gds action: same image, PDK revision and
#      tt-support-tools config merge), with --jobs = allocated CPUs, plus
#      `--skip <id>` for every params.json skip_steps entry in fast mode;
#   3. always writes RUN_DIR/result.json (extract_result.py), also after a
#      failure or the USR1 warning signal that submit.py requests 15 min before
#      the time limit;
#   4. keeps metrics, logs, reports and resolved config under RUN_DIR/out/
#      (small files uncompressed, the rest in librelane-logs.tar.gz), plus the
#      final GDS/netlists/SPEF (gzip) only when result.json says candidate;
#   5. deletes the node-local run directory.
# A requeued (preempted) task starts again from the snapshot: runs are idempotent.
#
# Environment (submit.py passes these with --export):
#   PE_HARNESS     directory holding this script, extract_result.py, sweeplib.py
#   PE_FLOW_ROOT   the proven mirror: librelane-3.1.0.dev3.sif, pdk/, tt/
#   PE_LOCAL_BASE  optional node-local base directory override
#   PE_MIN_LOCAL_GB  free space needed on the node-local disk (default 40)
#   PE_LL_EXTRA_ARGS extra LibreLane CLI arguments, for harness smoke tests only
#                  (e.g. "--to OpenROAD.Floorplan"); recorded in result.json
set -uo pipefail

log() { echo "[run_one $(date '+%F %T')] $*"; }

if [ "${1:-}" = "--list" ]; then
  LIST=$2
  IDX=${SLURM_ARRAY_TASK_ID:?array task id missing}
  RUN_DIR=$(sed -n "$((IDX + 1))p" "$LIST")
else
  RUN_DIR=${1:?usage: run_one.sh RUN_DIR | --list FILE}
fi
[ -f "$RUN_DIR/params.json" ] || { log "no params.json in '$RUN_DIR'"; exit 2; }
HARNESS=${PE_HARNESS:?PE_HARNESS not set}
FLOW_ROOT=${PE_FLOW_ROOT:-${PE_WORK:?set PE_WORK or PE_FLOW_ROOT}/sram-flow}
SIF=${PE_SIF:-$FLOW_ROOT/librelane-3.1.0.dev3.sif}
PDK=${PE_PDK_ROOT:-$FLOW_ROOT/pdk}
MIN_GB=${PE_MIN_LOCAL_GB:-40}
RUN_ID=$(basename "$RUN_DIR")
if [ -n "${SLURM_ARRAY_JOB_ID:-}" ]; then JOB=${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}; else JOB=${SLURM_JOB_ID:-local$$}; fi
ATTEMPT=${SLURM_RESTART_COUNT:-0}
CPUS=${SLURM_CPUS_PER_TASK:-$(nproc)}
NODE=$(hostname -s)

PY=$(command -v python3 || echo /usr/bin/python3)
pjson() { "$PY" -c 'import json,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]; print(" ".join(v) if isinstance(v,list) else v)' "$RUN_DIR/params.json" "$1"; }
MODE=$(pjson mode)
SKIP_STEPS=$(pjson skip_steps)
log "run $RUN_ID job $JOB attempt $ATTEMPT node $NODE cpus $CPUS mode $MODE"

# apptainer: module on compute nodes; the module's wrapper is self-contained
set +u
if ! command -v apptainer >/dev/null 2>&1; then
  source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null || true
  module load apptainer/1.4.2 >/dev/null 2>&1 || true
fi
set -u
APPTAINER=$(command -v apptainer || echo "${PE_APPTAINER:-apptainer}")
export APPTAINER_IGNORE_PROOT=1 PROOT_NO_SECCOMP=1
"$APPTAINER" --version || { log "apptainer not usable"; exit 3; }

# ---- node-local staging
pick_base() {
  local b avail
  for b in ${PE_LOCAL_BASE:-} /scratch/$USER /tmp/$USER; do
    [ -n "$b" ] || continue
    mkdir -p "$b/pe-sweep" 2>/dev/null || continue
    avail=$(df -Pk "$b/pe-sweep" 2>/dev/null | awk 'NR==2{print int($4/1048576)}')
    if [ -n "$avail" ] && [ "$avail" -ge "$MIN_GB" ]; then echo "$b/pe-sweep"; return; fi
  done
  echo "$RUN_DIR/work"
}
BASE=$(pick_base)
LOCAL=$BASE/$RUN_ID.$JOB
rm -rf "$BASE/$RUN_ID".* "$RUN_DIR/work" 2>/dev/null   # stale attempts of this run on this node
mkdir -p "$LOCAL"
echo "$(date '+%F %T') job $JOB attempt $ATTEMPT node $NODE local $LOCAL" >> "$RUN_DIR/staging.log"
[ -f "$RUN_DIR/result.json" ] && mv -f "$RUN_DIR/result.json" "$RUN_DIR/result.prev.json"
"$PY" - "$RUN_DIR/status.json" "$RUN_ID" "$JOB" "$ATTEMPT" "$NODE" "$LOCAL" "$CPUS" <<'PY'
import json, sys, time
p, rid, job, att, node, local, cpus = sys.argv[1:8]
json.dump({"run_id": rid, "job_id": job, "attempt": int(att), "node": node, "local_dir": local,
           "cpus": int(cpus), "state": "running", "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
          open(p, "w"), indent=2)
PY

cp -a "$RUN_DIR/snap/." "$LOCAL/" || { log "staging copy failed"; exit 4; }
( cd "$LOCAL" && sha256sum --quiet -c SNAPSHOT.sha256 ) || { log "snapshot hash mismatch"; exit 5; }
mkdir -p "$LOCAL/tmp"
rm -rf "$LOCAL/runs/wokwi"; mkdir -p "$LOCAL/runs/wokwi"   # tt_tool.py harden(): rmtree + makedirs

SKIP_ARGS=()
if [ "$MODE" = "fast" ]; then
  for s in $SKIP_STEPS; do SKIP_ARGS+=(--skip "$s"); done
fi
read -r -a EXTRA_ARGS <<< "${PE_LL_EXTRA_ARGS:-}"
[ ${#EXTRA_ARGS[@]} -gt 0 ] && log "EXTRA LibreLane args (smoke test only): ${EXTRA_ARGS[*]}"
log "local dir $LOCAL; skip: ${SKIP_ARGS[*]:-none}"

# ---- LibreLane (same invocation as sram-flow/run.sh)
SIGNALLED=""
PID=""
on_sig() {
  SIGNALLED=$1
  log "caught SIG$1; stopping LibreLane"
  [ -n "$PID" ] && kill -TERM "$PID" 2>/dev/null
}
trap 'on_sig USR1' USR1
trap 'on_sig TERM' TERM
trap 'on_sig INT' INT

start=$(date +%s)
"$APPTAINER" exec --cleanenv --containall --no-home \
  --bind "$LOCAL:$LOCAL:rw" --bind "$PDK:$PDK:ro" --pwd "$LOCAL" \
  --env TMPDIR="$LOCAL/tmp" --env CI=1 \
  "$SIF" python3 -m librelane --pdk-root "$PDK" --pdk ihp-sg13cmos5l --manual-pdk \
  --run-tag wokwi --force-run-dir runs/wokwi --hide-progress-bar --jobs "$CPUS" \
  "${SKIP_ARGS[@]}" "${EXTRA_ARGS[@]}" src/config_merged.json &
PID=$!
rc=0
while true; do
  wait "$PID"; rc=$?
  if kill -0 "$PID" 2>/dev/null; then
    if [ -n "$SIGNALLED" ]; then
      for _ in $(seq 60); do kill -0 "$PID" 2>/dev/null || break; sleep 2; done
      kill -KILL "$PID" 2>/dev/null
    fi
    continue
  fi
  break
done
wall=$(( $(date +%s) - start ))
log "LIBRELANE_EXIT $rc after $wall s${SIGNALLED:+ (signal $SIGNALLED)}"

STATUS_ARGS=()
case "$SIGNALLED" in
  USR1) STATUS_ARGS=(--status timeout) ;;
  TERM|INT) STATUS_ARGS=(--status terminated) ;;
esac

# ---- result.json (always)
"$PY" "$HARNESS/extract_result.py" --run-dir "$LOCAL/runs/wokwi" --params "$RUN_DIR/params.json" \
  --exit-code "$rc" --wall-s "$wall" "${STATUS_ARGS[@]}" \
  --extra job_id="$JOB" --extra attempt="$ATTEMPT" --extra node="$NODE" --extra cpus="$CPUS" \
  --extra partition="${SLURM_JOB_PARTITION:-}" --extra local_dir="$LOCAL" \
  --extra ll_extra_args="${PE_LL_EXTRA_ARGS:-}" \
  --out "$RUN_DIR/result.json" || log "extract_result.py failed"
CAND=$("$PY" -c 'import json,sys; print(1 if json.load(open(sys.argv[1])).get("candidate") else 0)' "$RUN_DIR/result.json" 2>/dev/null || echo 0)

# ---- keep: small files + logs/reports tarball (+ final views for candidates)
OUT=$RUN_DIR/out
rm -rf "$OUT.new"; mkdir -p "$OUT.new"
if [ -d "$LOCAL/runs/wokwi" ]; then
  cd "$LOCAL/runs/wokwi"
  for f in resolved.json error.log warning.log flow.log final/metrics.json final/metrics.csv; do
    [ -f "$f" ] && cp "$f" "$OUT.new/"
  done
  sta=$(ls -d [0-9]*-openroad-stapostpnr 2>/dev/null | tail -1)
  [ -n "$sta" ] && [ -f "$sta/summary.rpt" ] && cp "$sta/summary.rpt" "$OUT.new/sta-postpnr-summary.rpt"
  find . -path ./tmp -prune -o -type f -size -64M \
    ! -name '*.odb' ! -name '*.def' ! -name '*.gds' ! -name '*.mag' ! -name '*.spef' ! -name '*.sdf' \
    ! -name '*.v' ! -name '*.lib' ! -name '*.guide' ! -name '*.png' ! -name '*.spice' ! -name '*.h.json' \
    ! -name '*.lyrdb' -print > "$LOCAL/pack.list"
  tar -cf - -T "$LOCAL/pack.list" 2>/dev/null | pigz -p "$CPUS" > "$OUT.new/librelane-logs.tar.gz"
  if [ "$CAND" = "1" ] && [ -d final ]; then
    mkdir -p "$OUT.new/final"
    for f in final/gds/*.gds final/nl/*.nl.v final/pnl/*.pnl.v final/spef/*/*.spef final/sdc/*.sdc final/lef/*.lef; do
      [ -f "$f" ] || continue
      d=$OUT.new/$(dirname "$f"); mkdir -p "$d"
      pigz -p "$CPUS" -c "$f" > "$d/$(basename "$f").gz"
    done
    ( cd "$OUT.new/final" && find . -type f -name '*.gz' -exec sha256sum {} + > SHA256SUMS )
  fi
  cd /
fi
rm -rf "$OUT"; mv "$OUT.new" "$OUT"

"$PY" - "$RUN_DIR/status.json" "$RUN_DIR/result.json" "$rc" <<'PY'
import json, sys, time
p, rp, rc = sys.argv[1:4]
s = json.load(open(p))
try:
    r = json.load(open(rp))
except Exception:
    r = {}
s.update(state="finished", result_status=r.get("status"), exit_code=int(rc),
         finished=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
json.dump(s, open(p, "w"), indent=2)
PY

rm -rf "$LOCAL"
log "done: $(cat "$RUN_DIR/status.json" | tr -d '\n' | tr -s ' ')"
exit "$rc"
