#!/usr/bin/env bash
# Run one certificate task with sby and record the outcome.
#
# Usage: run_one.sh CERTDIR NAME TASK [ENGINE]
#   CERTDIR  directory written by gen_cert.py emit (NAME.sby, NAME.sv, ...)
#   NAME     certificate module name (NAME.sby)
#   TASK     bmc | cover
#   ENGINE   optional: run only these solvers (bitwuzla, boolector, yices,
#            bmc3, ric3; several joined with '+'); default: the file's portfolio
#
# Environment: SBY_TIMEOUT (seconds, default 86400), CERT_RESULTS (directory
# for the result JSON, default CERTDIR/results), SLURM_JOB_ID and
# SLURM_ARRAY_* are recorded when present. The sby work directory keeps the
# log and traces; copied sources are deleted afterwards to save inodes.
set -uo pipefail
CERTDIR=$1 NAME=$2 TASK=$3 ENGINE=${4:-}
RESULTS=${CERT_RESULTS:-$CERTDIR/results}
mkdir -p "$RESULTS" "$CERTDIR/work"
SBY=$CERTDIR/$NAME.sby
TAG=$NAME.$TASK${ENGINE:+.$ENGINE}
if [ -n "$ENGINE" ]; then
  # One solver, or several joined with '+' (a portfolio of those only).
  line=""
  for e in ${ENGINE//+/ }; do
    case "$e" in
      bitwuzla|boolector|yices) l="$TASK: smtbmc $e" ;;
      bmc3) l="$TASK: abc bmc3" ;;
      ric3) l="$TASK: aiger rIC3" ;;
      *) echo "run_one.sh: unknown engine $e" >&2; exit 2 ;;
    esac
    line="$line$l\n"
  done
  SBY=$CERTDIR/work/$TAG.sby
  awk -v line="$line" -v task="$TASK" '
    /^\[engines\]/ { print; printf "%s", line; skip = 1; next }
    /^\[/ { skip = 0 }
    skip && $0 ~ "^" task ":" { next }
    { print }' "$CERTDIR/$NAME.sby" > "$SBY"
fi
WORK=$CERTDIR/work/$TAG
start=$(date +%s)
(cd "$CERTDIR" && timeout "${SBY_TIMEOUT:-86400}" sby -f -d "$WORK" "$SBY" "$TASK") > "$WORK.log" 2>&1
rc=$?
seconds=$(( $(date +%s) - start ))
python3 - "$WORK.log" "$RESULTS/$TAG.json" "$NAME" "$TASK" "${ENGINE:-portfolio}" "$rc" "$seconds" <<'PY'
import json, os, re, sys
log_path, out, name, task, engine, rc, seconds = sys.argv[1:8]
text = open(log_path, errors="replace").read()
status = None
for m in re.finditer(r"DONE \(([A-Z]+), rc=(\d+)\)", text):
    status = m.group(1)
if status is None:
    status = "TIMEOUT" if rc == "124" else "ERROR"
winner = re.search(r"summary: engine_\d+ \(([^)]*)\) returned (\w+)", text)
failed = []
for m in re.finditer(r"failed assertion (\S+) at ([\w.]+\.(?:sv|vh)):(\d+)", text):
    failed.append(m.group(1).split(".")[-1])
for m in re.finditer(r"Assert failed in \w+: (?:(\w+) \()?([\w.]+\.(?:sv|vh)):(\d+)", text):
    failed.append(m.group(1) or f"{m.group(2)}:{m.group(3)}")
steps = [int(x) for x in re.findall(r"Checking assertions in step (\d+)", text)]
covers = len(re.findall(r"Reached cover statement", text))
json.dump({"certificate": name, "task": task, "engine": engine, "status": status, "rc": int(rc),
           "seconds": int(seconds), "winner": " ".join(winner.groups()) if winner else "",
           "failed_assertions": sorted(set(failed)), "last_step": max(steps) if steps else None,
           "covers_reached": covers,
           "slurm_job": os.environ.get("SLURM_ARRAY_JOB_ID") or os.environ.get("SLURM_JOB_ID"),
           "slurm_task": os.environ.get("SLURM_ARRAY_TASK_ID"), "host": os.uname().nodename},
          open(out, "w"))
print(f"{name}.{task}.{engine}: {status} (rc={rc}) in {seconds}s", " ".join(sorted(set(failed))))
PY
# Keep the log, the engine logs and traces; drop the copied sources.
rm -rf "$WORK/src" 2>/dev/null; find "$WORK/model" -type f ! -name "*.log" -delete 2>/dev/null
exit 0
