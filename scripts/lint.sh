#!/usr/bin/env bash
# Static checks on the Tiny Tapeout top (fast; no PDK needed):
#   1. iverilog -g2012 elaboration with the IHP SRAM behavioral models (FUNCTIONAL)
#   2. yosys hierarchy -check with the SRAM macro as a blackbox
#   3. verilator --lint-only -Wall with the SRAM blackbox, mirroring LibreLane's
#      lint step (--Wno-fatal: warnings are reported, only errors fail)
# Needs iverilog, yosys and verilator on PATH.
# Environment: LINT_WORK_DIR (default: a temporary directory, removed on exit).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO"

TOP=tt_um_teslacoilerow_protocol_emulator
SOURCES=(src/project.v src/protocol_emulator_core.v)
SRAM_MODELS=(models/RM_IHPSG13_1P_64x16_c2.v models/RM_IHPSG13_1P_core_behavioral.v)
SRAM_BLACKBOX=models/blackbox/RM_IHPSG13_1P_64x16_c2.v

if [ -n "${LINT_WORK_DIR:-}" ]; then
  WORK=$LINT_WORK_DIR
  mkdir -p "$WORK"
else
  WORK=$(mktemp -d)
  trap 'rm -rf "$WORK"' EXIT
fi

echo "== iverilog elaboration (${SOURCES[*]} + SRAM models, -DFUNCTIONAL)"
iverilog -g2012 -DFUNCTIONAL -s "$TOP" -o "$WORK/elab.vvp" "${SOURCES[@]}" "${SRAM_MODELS[@]}"
echo "iverilog: OK"

echo "== yosys hierarchy -check (SRAM macro as blackbox)"
yosys -q -l "$WORK/yosys.log" -p "read_verilog -lib $SRAM_BLACKBOX; read_verilog ${SOURCES[*]}; hierarchy -check -top $TOP" >/dev/null
echo "yosys: OK ($(grep -c 'RM_IHPSG13_1P_64x16_c2' "$WORK/yosys.log" || true) log lines mention the SRAM blackbox)"

echo "== verilator --lint-only -Wall (SRAM macro as blackbox)"
cat > "$WORK/blackbox.vlt" <<EOF
\`verilator_config
lint_off -file "*$SRAM_BLACKBOX"
EOF
set +e
verilator --lint-only -Wall --Wno-fatal --top-module "$TOP" \
  "$WORK/blackbox.vlt" "$SRAM_BLACKBOX" "${SOURCES[@]}" > "$WORK/verilator.log" 2>&1
rc=$?
set -e
warnings=$(grep -c '^%Warning' "$WORK/verilator.log" || true)
errors=$(grep -c '^%Error' "$WORK/verilator.log" || true)
echo "verilator: rc=$rc warnings=$warnings errors=$errors"
grep -E '^%(Warning|Error)' "$WORK/verilator.log" | sed -E 's/^(%[A-Za-z]+-?[A-Z0-9_]*).*/\1/' | sort | uniq -c || true
if [ "$rc" -ne 0 ] || [ "$errors" -ne 0 ]; then
  cat "$WORK/verilator.log"
  exit 1
fi
