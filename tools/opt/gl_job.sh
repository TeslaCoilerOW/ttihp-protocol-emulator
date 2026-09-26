#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Slurm job body: the gate-level cocotb subset on a promoted configuration's
# final netlist (docs/optimization.md, "Promotion"), as the tt-gds-action gl_test
# step runs it: the unpowered final netlist becomes test/gate_level_netlist.v and
# `make GATES=yes` runs in test/ (here with GL_NETLIST=<netlist>, which the
# Makefile supports). Icarus Verilog 13.0 (the CI version, $PE_WORK/host/iv13/bin),
# cocotb 2.0.1 ($PE_WORK/cocotb/env20.sh) and the IHP PDK revision of the flow
# ($PE_WORK/sram-flow/pdk). The test tree is a copy of the frozen repository
# export in a node-local directory; WAVES=none (no waveform file).
#
#   gl_job.sh NETLIST VARIANT TREE OUT_DIR
#
# VARIANT is "base" for the design of record, else the variant name (PE_VARIANT;
# the reference model is configured from configs/variants/<name>.json). Writes
# OUT_DIR/{gl.log, results.xml, result.json}.
set -uo pipefail
NETLIST=${1:?usage: gl_job.sh NETLIST VARIANT TREE OUT_DIR}
VARIANT=${2:?}
TREE=${3:?}
OUT=${4:?}
: "${PE_WORK:?PE_WORK not set}"
JOB=${SLURM_JOB_ID:-local$$}
mkdir -p "$OUT"
LOCAL=$(mktemp -d "${TMPDIR:-/tmp}/pe-opt-gl.XXXXXX")
trap 'rm -rf "$LOCAL"' EXIT
# shellcheck disable=SC1091
source "$PE_WORK/cocotb/env20.sh"
export PDK_ROOT=$PE_WORK/sram-flow/pdk
export PATH=$PE_WORK/host/iv13/bin:$PATH
echo "host $(hostname -s) job $JOB start $(date -Is) iverilog: $(iverilog -V 2>&1 | head -1)"
echo "cocotb $(cocotb-config --version 2>/dev/null) variant $VARIANT"
for d in test src models configs firmware; do
  [ -d "$TREE/$d" ] && cp -r "$TREE/$d" "$LOCAL/"
done
chmod -R u+w "$LOCAL"
NL_SHA=$(sha256sum "$NETLIST" | cut -d' ' -f1)
cd "$LOCAL/test"
rm -rf sim_build results.xml
start=$(date +%s)
if [ "$VARIANT" != "base" ] && [ ! -f "$LOCAL/configs/variants/$VARIANT.json" ]; then
  echo "no configs/variants/$VARIANT.json in the tree: GL test not run" | tee "$OUT/gl.log"
  rc=127
else
  make GATES=yes GL_NETLIST="$NETLIST" PE_VARIANT="$VARIANT" WAVES=none > "$OUT/gl.log" 2>&1
  rc=$?
fi
wall=$(( $(date +%s) - start ))
[ -f results.xml ] && cp results.xml "$OUT/results.xml"
python3 - "$OUT" "$JOB" "$rc" "$wall" "$NL_SHA" "$VARIANT" <<'PY'
import json, os, sys, time
import xml.etree.ElementTree as ET
out, job, rc, wall, sha, variant = sys.argv[1:7]
tests = fail = skip = 0
names = {"fail": [], "skip": 0}
p = os.path.join(out, "results.xml")
if os.path.exists(p):
    for tc in ET.parse(p).getroot().iter("testcase"):
        tests += 1
        if tc.find("failure") is not None or tc.find("error") is not None:
            fail += 1
            names["fail"].append(tc.get("name"))
        elif tc.find("skipped") is not None:
            skip += 1
res = {"job_id": job, "make_exit": int(rc), "wall_s": int(wall), "netlist_sha256": sha, "variant": variant,
       "tests": tests, "failed": fail, "skipped": skip, "passed": tests - fail - skip,
       "failed_names": names["fail"][:20], "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
res["pass"] = int(rc) == 0 and tests > 0 and fail == 0 and res["passed"] > 0
if int(rc) == 127:  # no reference-model configuration for this variant in the tree
    res["not_run"] = True
    res["pass"] = None
tmp = os.path.join(out, "result.json.tmp")
json.dump(res, open(tmp, "w"), indent=2)
os.replace(tmp, os.path.join(out, "result.json"))
print(json.dumps(res))
PY
exit 0
