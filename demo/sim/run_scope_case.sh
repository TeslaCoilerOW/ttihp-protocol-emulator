#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# One case of the self-measured isolation experiment in simulation
# (docs/demo.md, "Self-measured isolation"): the Cmod A7 osc12 bridge build
# with the on-board capture unit, driven over its simulated USB-UART by
# demo/pc_demo.py, then demo/sim/scope_case.py.
#
#   demo/sim/run_scope_case.sh TAG MODE CORE SEED EXPECT
#
#   MODE    idle | loaded
#   CORE    base | host-fetch | engine-fetch | timer-host | timer-engine (mutate.py)
#   EXPECT  same | different  (verdict the case must produce)
#
# Environment:
#   DEMO_WORK      output root (required); the case writes DEMO_WORK/runs/TAG/
#   DEMO_ENV       optional script to source first (cocotb 2.0.1, Icarus, Python)
#   DEMO_CAPTURES  captures per phase (default 2)
#   DEMO_LIMIT     records per capture (default 2048)
#   DEMO_CYCLES    upper bound of the phase in core cycles (default 6000000)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
tag=$1 mode=$2 core=$3 seed=$4 expect=$5
: "${DEMO_WORK:?set DEMO_WORK to the output directory}"
if [ -n "${DEMO_ENV:-}" ]; then
  # shellcheck disable=SC1090
  source "$DEMO_ENV"
fi
export PYTHONDONTWRITEBYTECODE=1
out=$DEMO_WORK/runs/$tag
rm -rf "$out"
mkdir -p "$out"
core_file=$REPO/src/protocol_emulator_core.v
if [ "$core" != base ]; then
  python3 "$HERE/mutate.py" "$core" 3 "$core_file" "$out/core.v" > "$out/mutate.log"
  core_file=$out/core.v
fi
start=$(date +%s)
set +e
make -C "$HERE" SIM_BUILD="$out/sim_build" DEMO_TB=bridge CORE_TAG="$core" PE_CORE="$core_file" \
  DEMO_MODE="$mode" DEMO_CYCLES="${DEMO_CYCLES:-6000000}" DEMO_SEED="$seed" DEMO_OUT="$out" DEMO_TAG=result \
  DEMO_CAPTURES="${DEMO_CAPTURES:-2}" DEMO_LIMIT="${DEMO_LIMIT:-2048}" \
  DEMO_VCD="$out/sim.vcd" COCOTB_TEST_FILTER=test_scope_isolation \
  COCOTB_RESULTS_FILE="$out/results.xml" > "$out/sim.log" 2>&1
sim_status=$?
set -e
end=$(date +%s)
python3 - "$out" "$sim_status" "$((end - start))" "$tag" "$mode" "$core" "$seed" <<'PY'
import json, re, sys
from pathlib import Path
out, status, secs, tag, mode, core, seed = sys.argv[1:]
xml = (Path(out) / "results.xml").read_text() if (Path(out) / "results.xml").exists() else ""
case = {"tag": tag, "tb": "bridge", "mode": mode, "core": core, "seed": int(seed), "test": "test_scope_isolation",
        "make_status": int(status), "wall_seconds": int(secs), "tests": xml.count("<testcase"),
        "failures": len(re.findall(r"<failure", xml))}
Path(out, "case.json").write_text(json.dumps(case, indent=1) + "\n")
print(case)
PY
rm -rf "$out/sim_build"
[ -f "$out/sim.vcd" ] && gzip -f "$out/sim.vcd"
set +e
python3 "$HERE/scope_case.py" "$out" --expect "$expect" > "$out/scope-case.log" 2>&1
case_status=$?
set -e
tail -n 1 "$out/scope-case.log"
[ "$sim_status" = 0 ] && exit "$case_status"
exit "$sim_status"
