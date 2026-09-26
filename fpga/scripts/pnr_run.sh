#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# One nextpnr-xilinx place-and-route run on a synthesized build directory.
# Used by build.sh (one call per PLACER:SEED) and by the seed/option sweeps
# (fpga/scripts/sweep/), so that a sweep result and a later build.sh run with
# the same seed and options are the same nextpnr invocation.
#
#   fpga/scripts/pnr_run.sh BUILD_DIR PLACER:SEED RUN_DIR [fasm]
#
# BUILD_DIR holds synth.json, board.xdc and build.env (written by build.sh).
# Options come from the environment (see build.sh): TIMING_WEIGHT,
# PNR_SETTINGS, ROUTER, REGION, PNR_PERIOD and the NEXTPNR_* knobs of
# nextpnr-xilinx.
# Writes RUN_DIR/report.json and nextpnr.log (and <name>.fasm with "fasm");
# prints "PLACER:SEED FMAX_MHZ" or "PLACER:SEED FAILED".
set -uo pipefail
bdir="$(cd "${1:?BUILD_DIR}" && pwd)"
run="${2:?PLACER:SEED}"
rdir="${3:?RUN_DIR}"
want_fasm="${4:-}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${OPENXC7:?set OPENXC7 to the unpacked tools-openxc7 package}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
# shellcheck disable=SC1091
source "$bdir/build.env"   # chipdb name freq
placer="${run%%:*}"
seed="${run##*:}"
mkdir -p "$rdir"

# nextpnr reads its settings from the top module of the JSON netlist; the
# netlist with settings is cached per settings value. Parallel runs may
# create the cache at the same time: each writes its own copy and links it
# into place, which never replaces a file another run is reading.
json="$bdir/synth.json"
settings=$(python3 -c '
import json, os
s = json.loads(os.environ.get("PNR_SETTINGS") or "{}")
if os.environ.get("TIMING_WEIGHT"):
    s["placerHeap/timingWeight"] = int(os.environ["TIMING_WEIGHT"])
print(json.dumps(s, sort_keys=True) if s else "")')
if [ -n "$settings" ]; then
  key=$(printf '%s' "$settings" | sha256sum | cut -c1-12)
  json="$bdir/pnr-$key.json"
  if [ ! -f "$json" ]; then
    python3 - "$bdir/synth.json" "$json.$$" "$settings" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
tops = [m for m in d["modules"].values() if m.get("attributes", {}).get("top")]
assert len(tops) == 1, "no unique top module"
tops[0].setdefault("settings", {}).update(json.loads(sys.argv[3]))
json.dump(d, open(sys.argv[2], "w"))
PY
    ln "$json.$$" "$json" 2>/dev/null || true
    rm -f "$json.$$"
  fi
fi
xdc="$bdir/board.xdc"
if [ -n "${PNR_PERIOD:-}" ]; then
  xdc="$rdir/pnr.xdc"
  sed -E "/get_nets \{ clk \}|get_ports \{ host_clk \}/ s/-period [0-9.]+/-period $PNR_PERIOD/" "$bdir/board.xdc" > "$xdc"
fi
args=(--chipdb "$chipdb" --xdc "$xdc" --json "$json" --report "$rdir/report.json"
      --placer "$placer" --seed "$seed" --freq "$freq" --timing-allow-fail -l "$rdir/nextpnr.log")
[ "$want_fasm" = fasm ] && args+=(--fasm "$rdir/$name.fasm")
[ -n "${ROUTER:-}" ] && args+=(--router "$ROUTER")
if [ -n "${REGION:-}" ]; then
  export PE_REGION="$REGION"
  args+=(--pre-place "$here/region.py")
fi
if "$OPENXC7/bin/nextpnr-xilinx" "${args[@]}" > "$rdir/nextpnr.stdout" 2>&1; then
  echo "$run $(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['fmax']['clk']['achieved'])" "$rdir/report.json")"
else
  echo "$run FAILED"
fi
grep -v '^Info:\|^Warning:' "$rdir/nextpnr.stdout" > "$rdir/python.log" || true
rm -f "$rdir/nextpnr.stdout"
[ -s "$rdir/python.log" ] || rm -f "$rdir/python.log"
