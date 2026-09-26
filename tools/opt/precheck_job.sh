#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Slurm job body: the Tiny Tapeout precheck on one promoted configuration
# (docs/optimization.md, "Promotion"). It is the local precheck reproduction of
# docs/drc-triage.md (tt-support-tools d66cf179e precheck.py, unmodified, run as
# the tt-gds-action precheck step runs it:
#   python precheck.py --gds <sub>/<top>.gds --tech ihp-sg13cmos5l   (cwd tt/precheck)
# with KLayout from the LibreLane 3.1.0.dev3 SIF), using the reproduction's
# installation under $PE_WORK/drc-triage (tt/, bin/, venv/, pdk/) read-only. The
# only difference from that reproduction's job script is where it writes: a
# node-local work directory, then OUT_DIR.
#
#   precheck_job.sh SUB_DIR OUT_DIR
#
# SUB_DIR holds info.yaml, <top>.gds, <top>.lef (and <top>.v), like the action's
# tt_submission/. Writes OUT_DIR/{precheck.log, reports/, result.json}.
set -uo pipefail
SUB=${1:?usage: precheck_job.sh SUB_DIR OUT_DIR}
OUT=${2:?usage: precheck_job.sh SUB_DIR OUT_DIR}
B=${PE_DRC_TRIAGE:-${PE_WORK:?PE_WORK not set}/drc-triage}
TOP=tt_um_teslacoilerow_protocol_emulator
JOB=${SLURM_JOB_ID:-local$$}
mkdir -p "$OUT"
LOCAL=$(mktemp -d "${TMPDIR:-/tmp}/pe-opt-precheck.XXXXXX")
trap 'rm -rf "$LOCAL"' EXIT
export TMPDIR=$LOCAL           # the SIF wrapper binds $TMPDIR read-write
mkdir -p "$LOCAL/tt"
cp -r "$B/tt/precheck" "$LOCAL/tt/"
ln -s "$B/tt/tech" "$LOCAL/tt/tech"
rm -rf "$LOCAL/tt/precheck/reports"; mkdir -p "$LOCAL/tt/precheck/reports"
export PATH=$B/bin:$B/venv/bin:$PATH PDK_ROOT=$B/pdk PDK=ihp-sg13cmos5l
echo "host $(hostname -s) cpus ${SLURM_CPUS_PER_TASK:-?} job $JOB start $(date -Is)"
GDS_SHA=$(sha256sum "$SUB/$TOP.gds" | cut -d' ' -f1)
echo "gds sha256 $GDS_SHA"
cd "$LOCAL/tt/precheck"
start=$(date +%s)
python precheck.py --gds "$SUB/$TOP.gds" --tech ihp-sg13cmos5l > "$OUT/precheck.log" 2>&1
rc=$?
wall=$(( $(date +%s) - start ))
echo "PRECHECK_EXIT $rc after $wall s"
rm -rf "$OUT/reports"; cp -r reports "$OUT/reports"
python3 - "$OUT" "$JOB" "$rc" "$wall" "$GDS_SHA" <<'PY'
import json, os, re, sys, time
out, job, rc, wall, sha = sys.argv[1:6]
md = ""
try:
    md = open(os.path.join(out, "reports", "results.md"), encoding="utf-8").read()
except OSError:
    pass
checks = {}
for line in md.splitlines():
    m = re.match(r"^\|\s*(.+?)\s*\|\s*(\S+)\s*\|\s*$", line)
    if m and m.group(1) not in ("Check", "-----------"):
        checks[m.group(1)] = "pass" if "✅" in m.group(2) else "fail"
res = {"job_id": job, "exit": int(rc), "wall_s": int(wall), "gds_sha256": sha, "checks": checks,
       "n_checks": len(checks), "n_fail": sum(1 for v in checks.values() if v != "pass"),
       "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
res["pass"] = int(rc) == 0 and len(checks) > 0 and res["n_fail"] == 0
tmp = os.path.join(out, "result.json.tmp")
json.dump(res, open(tmp, "w"), indent=2)
os.replace(tmp, os.path.join(out, "result.json"))
print(json.dumps(res))
PY
exit 0
