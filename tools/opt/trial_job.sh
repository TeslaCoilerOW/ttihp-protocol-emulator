#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Slurm job body for one optimizer run (docs/optimization.md): a wrapper around
# the sweep harness's scripts/sweep/run_one.sh, which is used unchanged.
#
#   trial_job.sh RUN_DIR
#
# 1. starts a watcher that runs `checks.py def` on the OpenROAD.GeneratePDN DEF as
#    soon as that step has finished (short VPWR/VGND straps; the DEF itself is
#    pruned by run_one.sh at the end, so it has to be checked while the run is live);
# 2. runs $PE_HARNESS/run_one.sh RUN_DIR (snapshot staging, LibreLane, result.json,
#    pruning), forwarding USR1/TERM/INT to it: sbatch --signal=B:USR1@900 only
#    signals this batch shell, and run_one.sh turns USR1 into a recorded timeout;
# 3. runs postprocess.py (LEF power-port check, worst paths) -> RUN_DIR/opt_post.json;
# 4. writes RUN_DIR/opt_done.json with the job id, which the driver waits for.
#
# Environment (set by driver.py via sbatch --export): PE_HARNESS (scripts/sweep of the
# frozen tree), PE_OPT_HARNESS (tools/opt of the frozen tree), PE_WORK or PE_FLOW_ROOT,
# optional PE_OPT_PY (python for the checks; any python3 >= 3.6 works).
set -uo pipefail
RUN_DIR=${1:?usage: trial_job.sh RUN_DIR}
: "${PE_HARNESS:?PE_HARNESS not set}" "${PE_OPT_HARNESS:?PE_OPT_HARNESS not set}"
PY=${PE_OPT_PY:-}
if [ -z "$PY" ] || ! "$PY" -c 'import sys' 2>/dev/null; then PY=$(command -v python3 || echo /usr/bin/python3); fi
JOB=${SLURM_JOB_ID:-local$$}
log() { echo "[trial_job $(date '+%F %T')] $*"; }
log "run $(basename "$RUN_DIR") job $JOB restart ${SLURM_RESTART_COUNT:-0} node $(hostname -s)"
rm -f "$RUN_DIR/opt_pdn.json" "$RUN_DIR/opt_post.json" "$RUN_DIR/opt_done.json"

watch_pdn() {
  local i job ld d def
  for i in $(seq 1 1440); do
    sleep 15
    [ -f "$RUN_DIR/status.json" ] || continue
    read -r job ld < <("$PY" -c 'import json,sys; s=json.load(open(sys.argv[1])); print(s.get("job_id",""), s.get("local_dir",""))' \
                        "$RUN_DIR/status.json" 2>/dev/null)
    [ "$job" = "$JOB" ] && [ -n "$ld" ] || continue
    d=$(ls -d "$ld"/runs/wokwi/*-openroad-generatepdn 2>/dev/null | tail -1)
    [ -n "$d" ] && [ -f "$d/state_out.json" ] || continue
    def=$(ls "$d"/*.def 2>/dev/null | head -1)
    [ -n "$def" ] || continue
    "$PY" "$PE_OPT_HARNESS/checks.py" def "$def" --out "$RUN_DIR/opt_pdn.json" > /dev/null
    log "PDN DEF check: $("$PY" -c 'import json,sys; r=json.load(open(sys.argv[1])); print("ok" if r.get("ok") else "FAIL", "short_stripes", r.get("short_stripes"), "port_violations", r.get("port_violations"))' "$RUN_DIR/opt_pdn.json" 2>/dev/null)"
    return 0
  done
}
watch_pdn &
WPID=$!

CHILD=""
fwd() { log "caught SIG$1; forwarding to run_one.sh"; [ -n "$CHILD" ] && kill -"$1" "$CHILD" 2>/dev/null; }
trap 'fwd USR1' USR1
trap 'fwd TERM' TERM
trap 'fwd INT' INT

"$PE_HARNESS/run_one.sh" "$RUN_DIR" &
CHILD=$!
rc=0
while true; do
  wait "$CHILD"; rc=$?
  kill -0 "$CHILD" 2>/dev/null || break
done
kill "$WPID" 2>/dev/null
wait "$WPID" 2>/dev/null

"$PY" "$PE_OPT_HARNESS/postprocess.py" --run-dir "$RUN_DIR" --job-id "$JOB" > /dev/null || log "postprocess failed"
"$PY" - "$RUN_DIR/opt_done.json" "$JOB" "$rc" <<'PY'
import json, os, sys, time
p, job, rc = sys.argv[1:4]
tmp = p + ".tmp"
json.dump({"job_id": job, "rc": int(rc), "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, open(tmp, "w"))
os.replace(tmp, p)
PY
log "done rc $rc"
exit "$rc"
