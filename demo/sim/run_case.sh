#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# One simulation case of the demonstration's validation (docs/demo.md,
# "Validation in simulation"), followed by the same capture analysis that a
# hardware capture gets.
#
#   demo/sim/run_case.sh TAG TB MODE CORE SEED CYCLES [TEST]
#
#   TB     pins (host-clock build, host library) | bridge (osc12 build, UART bridge)
#   MODE   idle | loaded                (test_isolation)
#   CORE   base | host-fetch | engine-fetch   (mutants from demo/sim/mutate.py)
#   TEST   test_isolation (default) | test_four | test_bridge_chain
#
# Environment:
#   DEMO_WORK   output root (required); the case writes DEMO_WORK/runs/TAG/
#   DEMO_ENV    optional script to source first (cocotb 2.0.1, Icarus, Python)
#   SIGROK_CLI  optional sigrok-cli command; test_four then decodes its
#               emulated capture with sigrok's UART/SPI/I2C decoders
#
# The simulation VCD is kept gzipped (sim.vcd.gz); demo/sim/analyze_case.sh
# then runs the capture analysis and can be re-run on its own.

set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
tag=$1 tb=$2 mode=$3 core=$4 seed=$5 cycles=$6 test=${7:-test_isolation}
: "${DEMO_WORK:?set DEMO_WORK to the output directory}"
if [ -n "${DEMO_ENV:-}" ]; then
  # shellcheck disable=SC1090
  source "$DEMO_ENV"
fi
export PYTHONDONTWRITEBYTECODE=1
out=$DEMO_WORK/runs/$tag
rm -rf "$out"
mkdir -p "$out"
cap="python3 $REPO/demo/pe_capture.py"

core_file=$REPO/src/protocol_emulator_core.v
lockstep=1
if [ "$core" != base ]; then
  python3 "$HERE/mutate.py" "$core" 3 "$core_file" "$out/core.v" > "$out/mutate.log"
  core_file=$out/core.v
  lockstep=0          # the reference model is the unmutated design
fi
clock=jitter
[ "$test" = test_four ] && clock=fixed

start=$(date +%s)
set +e
make -C "$HERE" SIM_BUILD="$out/sim_build" DEMO_TB="$tb" CORE_TAG="$core" PE_CORE="$core_file" \
  DEMO_MODE="$mode" DEMO_CYCLES="$cycles" DEMO_SEED="$seed" DEMO_OUT="$out" DEMO_TAG=result \
  DEMO_VCD="$out/sim.vcd" DEMO_LOCKSTEP="$lockstep" DEMO_CLOCK="$clock" COCOTB_TEST_FILTER="$test" \
  COCOTB_RESULTS_FILE="$out/results.xml" > "$out/sim.log" 2>&1
sim_status=$?
set -e
end=$(date +%s)
python3 - "$out" "$sim_status" "$((end - start))" "$tag" "$tb" "$mode" "$core" "$seed" "$cycles" "$test" <<'EOF'
import json, re, sys
from pathlib import Path
out, status, secs, tag, tb, mode, core, seed, cycles, test = sys.argv[1:]
xml = (Path(out) / "results.xml").read_text() if (Path(out) / "results.xml").exists() else ""
case = {"tag": tag, "tb": tb, "mode": mode, "core": core, "seed": int(seed), "cycles": int(cycles),
        "test": test, "make_status": int(status), "wall_seconds": int(secs),
        "tests": xml.count("<testcase"), "failures": len(re.findall(r"<failure", xml)),
        "skipped": len(re.findall(r"<skipped", xml))}
Path(out, "case.json").write_text(json.dumps(case, indent=1) + "\n")
print(case)
EOF
rm -rf "$out/sim_build"

if [ -f "$out/sim.vcd" ]; then
  gzip -f "$out/sim.vcd"
fi
bash "$HERE/analyze_case.sh" "$out"
exit "$sim_status"
