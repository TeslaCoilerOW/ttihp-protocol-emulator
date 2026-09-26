#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Open-source FPGA build (openXC7): Yosys synth_xilinx -> nextpnr-xilinx ->
# fasm2frames -> xc7frames2bit.
#
#   fpga/scripts/build.sh BOARD CLOCK OUT_DIR [PLACER:SEED ...]
#
#   BOARD  cmod_a7 | urbana
#   CLOCK  pll50 (MMCM, 50 MHz core, UART bridge; the default build)
#          host  (core clock from the host pin, like the TT demo board)
#          osc12 (cmod_a7 only: core on the 12 MHz oscillator, UART bridge)
#          pll40 (as pll50 at 40 MHz: MMCM /15 on the Cmod A7, /25 on the Urbana)
#   BRIDGE_ONLY=1 (environment, cmod_a7 bridge builds): leave the DIP pin-host
#          pins unused (PE_NO_PIN_HOST); output <name>_bridgeonly.
#   PLACER:SEED  nextpnr runs to try (default "heap:1"), e.g. heap:1 heap:2 sa:1.
#          Synthesis runs once; the place-and-route runs go in parallel
#          (JOBS, default: number of CPUs; each run is single-threaded,
#          OMP_NUM_THREADS=1) and the run with the highest fmax for the core
#          clock becomes the bitstream.
#
# Implementation options (environment; unset = the flow of the 2026-09-25
# builds). fpga/scripts/release.tsv records the options of each published
# bitstream, and fpga/scripts/release.sh rebuilds one from it.
#   TIMING_WEIGHT=N     nextpnr HeAP placer setting placerHeap/timingWeight
#                       (nextpnr default 10): weight of timing-critical
#                       connections in the analytic placement.
#   PNR_SETTINGS=JSON   further nextpnr settings as a JSON object, e.g.
#                       '{"router2/estimateWeight": "1.25"}'.
#   ROUTER=router1      nextpnr router (default router2).
#   SYNTH_OPTS=OPTS     extra synth_xilinx options, e.g. -nowidelut (no
#                       MUXF7/MUXF8: LUTs of at most 6 inputs).
#   ABC9_W=PS           ABC9 wire delay for LUT mapping (Yosys default 300 ps
#                       for xc7); larger values favour fewer LUT levels.
#   ABC9_SCRIPT=NAME    ABC9 script fpga/scripts/abc9/NAME.abc (Yosys'
#                       abc9.script.NAME with {W} = -W ABC9_W) instead of the
#                       default script.
#   REGION=X0,Y0,X1,Y1  place every slice cell in SLICE_X<X0..X1>Y<Y0..Y1>
#                       (fpga/scripts/region.py, --pre-place).
#   PNR_PERIOD=NS       core-clock period given to the placer and router
#                       (over-constraint); fmax is still the achieved value.
#   NEXTPNR_PLACER_BETA, NEXTPNR_PLACER_ALPHA, NEXTPNR_SPREAD_SCALE_X/_Y:
#                       openXC7 nextpnr-xilinx HeAP knobs, passed through.
#   SYNTH_ONLY=1        stop after synthesis (for fpga/scripts/sweep/).
# Place and route of each run is fpga/scripts/pnr_run.sh.
#
# Tools: yosys on PATH (OSS CAD Suite; 0.67 for the recorded builds) and
# OPENXC7 = unpacked FPGAwars tools-openxc7 package (nextpnr-xilinx,
# fasm2frames, xc7frames2bit, prjxray-db) with the part's chipdb in
# $OPENXC7/chipdb/ (docs/fpga.md, "Toolchain").
#
# Outputs in OUT_DIR: src/ (exact inputs), inputs.sha256, recipe.json,
# synth_stat.txt, synth_netlist.v (for fpga/sim FPGA_NETLIST=...),
# board.xdc, pnr/<placer>_<seed>/ (report.json, nextpnr.log), the best run's
# <name>.fasm and <name>.bit (+ .sha256), readback.json (readback.sh),
# summary.json, logs/.
set -euo pipefail

board="${1:?BOARD (cmod_a7|urbana)}"
clock="${2:?CLOCK (pll50|pll40|host|osc12)}"
out="${3:?OUT_DIR}"
shift 3
runs=("$@")
[ ${#runs[@]} -gt 0 ] || runs=(heap:1)
jobs="${JOBS:-$(nproc)}"
# nextpnr's analytic placer solves with Eigen; with several runs in parallel,
# extra solver threads only oversubscribe the CPUs (one run then took minutes
# instead of seconds). Results do not depend on the thread count.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fpga="$(cd "$here/.." && pwd)"
repo="$(cd "$fpga/.." && pwd)"
: "${OPENXC7:?set OPENXC7 to the unpacked tools-openxc7 package}"

case "$board" in
  cmod_a7) top=pe_top_cmod_a7; part=xc7a35tcpg236-1; family=artix7;   xdc=cmod_a7_35t.xdc ;;
  urbana)  top=pe_top_urbana;  part=xc7s50csga324-1; family=spartan7; xdc=urbana_xc7s50.xdc ;;
  *) echo "unknown board $board" >&2; exit 2 ;;
esac
case "$clock" in
  pll50) defines="" ;;
  pll40) defines="-DPE_CLOCK_PLL40" ;;
  host)  defines="-DPE_CLOCK_HOST" ;;
  osc12) [ "$board" = cmod_a7 ] || { echo "osc12 is a cmod_a7 option" >&2; exit 2; }
         defines="-DPE_CLOCK_OSC" ;;
  *) echo "unknown clock $clock" >&2; exit 2 ;;
esac
if [ "${BRIDGE_ONLY:-0}" = 1 ]; then
  [ "$board" = cmod_a7 ] && [ "$clock" != host ] || { echo "BRIDGE_ONLY=1 is a cmod_a7 bridge-build option" >&2; exit 2; }
  defines="$defines -DPE_NO_PIN_HOST"
fi
chipdb="$OPENXC7/chipdb/${part%-*}.bin"
db="$OPENXC7/share/nextpnr/external/prjxray-db/$family"
name="pe_${board}_${clock}"
[ "${BRIDGE_ONLY:-0}" = 1 ] && name="${name}_bridgeonly"
freq=50
[ "$clock" = osc12 ] && freq=12
[ "$clock" = pll40 ] && freq=40

mkdir -p "$out/logs" "$out/src" "$out/pnr"
out="$(cd "$out" && pwd)"
sources=(
  "$repo/src/project.v"
  "$repo/src/protocol_emulator_core.v"
  "$fpga/rtl/RM_IHPSG13_1P_64x16_c2_fpga.v"
  "$fpga/rtl/pe_fpga_sram_64x16.v"
  "$fpga/rtl/pe_uart_host_bridge.v"
  "$fpga/rtl/pe_fpga_shell.v"
  "$fpga/rtl/pe_fpga_clkgen.v"
  "$fpga/rtl/pe_fpga_clkfwd.v"
  "$fpga/rtl/pe_top_${board}.v"
)
for f in "${sources[@]}"; do cp "$f" "$out/src/"; done
if [ -n "${ABC9_SCRIPT:-}" ]; then
  cp "$here/abc9/${ABC9_SCRIPT}.abc" "$out/src/"
  sed "s/{W}/-W ${ABC9_W:-300}/g; s/{D}//g; s/{R}//g" "$here/abc9/${ABC9_SCRIPT}.abc" > "$out/abc9.script"
fi
cat "$fpga/constraints/$xdc" "$fpga/constraints/clock_${board}_${clock}.xdc" > "$out/board.xdc"
(cd "$out/src" && sha256sum ./* > ../inputs.sha256)
sha256sum "$out/board.xdc" "$chipdb" >> "$out/inputs.sha256"

# The implementation options of this build, as recorded in summary.json.
python3 - "$out/recipe.json" <<'PY'
import json, os, sys
keys = ["BRIDGE_ONLY", "SYNTH_OPTS", "ABC9_W", "ABC9_SCRIPT", "TIMING_WEIGHT", "PNR_SETTINGS", "ROUTER", "REGION", "PNR_PERIOD",
        "NEXTPNR_PLACER_BETA", "NEXTPNR_PLACER_ALPHA", "NEXTPNR_SPREAD_SCALE_X", "NEXTPNR_SPREAD_SCALE_Y"]
json.dump({k: os.environ[k] for k in keys if os.environ.get(k)}, open(sys.argv[1], "w"), indent=1, sort_keys=True)
PY

cd "$out"
t0=$(date +%s)
{
  echo "read_verilog $defines $(for f in "${sources[@]}"; do printf 'src/%s ' "$(basename "$f")"; done)"
  if [ -n "${ABC9_W:-}" ]; then echo "scratchpad -set synth_xilinx.abc9.W $ABC9_W"; fi
  if [ -n "${ABC9_SCRIPT:-}" ]; then echo "scratchpad -set abc9.script $out/abc9.script"; fi
  echo "synth_xilinx -flatten -abc9 ${SYNTH_OPTS:-} -arch xc7 -top $top"
  echo "tee -o synth_stat.txt stat"
  echo "write_json synth.json"
  echo "write_verilog -noattr synth_netlist.v"
} > synth.ys
yosys -q -l logs/yosys.log synth.ys
t1=$(date +%s)

cat > build.env <<ENV
chipdb=$chipdb
name=$name
freq=$freq
ENV
if [ "${SYNTH_ONLY:-0}" = 1 ]; then
  echo "synthesis done (SYNTH_ONLY=1): $out"
  exit 0
fi

# Place and route: one pnr_run.sh call per PLACER:SEED, JOBS at a time.
printf '%s\n' "${runs[@]}" |
  xargs -P "$jobs" -I{} sh -c 'bash "$1" "$2" "$3" "$2/pnr/$(echo "$3" | tr : _)" fasm' _ "$here/pnr_run.sh" "$out" {} |
  sort > pnr/results.txt
t2=$(date +%s)
cat pnr/results.txt
best=$(awk '$2 != "FAILED" {print $2, $1}' pnr/results.txt | sort -g -r | head -n 1 | cut -d' ' -f2)
[ -n "$best" ] || { echo "all place-and-route runs failed" >&2; exit 1; }
bestdir="pnr/${best%%:*}_${best##*:}"
echo "best run: $best"
cp "$bestdir/$name.fasm" "$bestdir/report.json" .
cp "$bestdir/nextpnr.log" logs/nextpnr.log
for d in pnr/*/; do [ "$d" = "$bestdir/" ] || rm -f "$d"/*.fasm; done
rm -f pnr-*.json

# fasm2frames prints a file-locking warning on stdout on some network file
# systems; keep only frame lines.
"$OPENXC7/bin/fasm2frames" --part "$part" --db-root "$db" "$name.fasm" 2> logs/fasm2frames.log \
  | grep '^0x' > "$name.frames.tmp"
mv "$name.frames.tmp" "$name.frames"
"$OPENXC7/bin/xc7frames2bit" --part_file "$db/$part/part.yaml" --part_name "$part" \
  --frm_file "$name.frames" --output_file "$name.bit" > logs/xc7frames2bit.log 2>&1
t3=$(date +%s)

sha256sum "$name.bit" > "$name.bit.sha256"
# Bit-level readback: the .bit must carry exactly the frames of the FASM.
bash "$here/readback.sh" . "$part" > logs/readback.log 2>&1 || echo "WARNING: bitstream readback failed, see logs/readback.log" >&2
python3 "$here/summarize.py" --board "$board" --clock "$clock" --part "$part" --best "$best" \
  --times "$((t1 - t0))" "$((t2 - t1))" "$((t3 - t2))" . > summary.json
cat summary.json
