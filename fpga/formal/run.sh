#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# FPGA SRAM stand-in vs IHP SRAM behavioral model: formal equivalence.
#
#   fpga/formal/run.sh [WORK_DIR] [TASK...]
#
# Copies the sources into WORK_DIR (default: fpga/formal/work), maps the FPGA
# SRAM with synth_xilinx (synth_sram_netlist.ys) and runs the SymbiYosys tasks
# of sram_equiv.sby (default: all). Requires yosys, sby, yosys-abc and
# bitwuzla on PATH (OSS CAD Suite). Exit status is non-zero if any task fails.
# The synthesis options of fpga/scripts/build.sh (SYNTH_OPTS, ABC9_W,
# ABC9_SCRIPT; environment) apply to the mapping here too, so the netlist
# proof covers the mapping of a build made with them.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
work="${1:-$here/work}"
shift || true
tasks=("$@")
if [ ${#tasks[@]} -eq 0 ]; then
  tasks=(rtl_prove netlist_prove mutants rtl_cover rtl_smt rtl_bmc netlist_bmc)
fi

# Mirror the repository layout so the relative paths in sram_equiv.sby hold.
mkdir -p "$work/fpga/formal" "$work/fpga/rtl" "$work/models"
cp "$here/sram_equiv.sby" "$here/sram_equiv_miter.sv" "$here/synth_sram_netlist.ys" "$work/fpga/formal/"
cp "$repo/fpga/rtl/pe_fpga_sram_64x16.v" "$work/fpga/rtl/"
cp "$repo/models/RM_IHPSG13_1P_core_behavioral.v" "$work/models/"

cd "$work/fpga/formal"
{
  if [ -n "${ABC9_W:-}" ]; then echo "scratchpad -set synth_xilinx.abc9.W $ABC9_W"; fi
  if [ -n "${ABC9_SCRIPT:-}" ]; then
    sed "s/{W}/-W ${ABC9_W:-300}/g; s/{D}//g; s/{R}//g" "$repo/fpga/scripts/abc9/${ABC9_SCRIPT}.abc" > "$PWD/abc9.script"
    echo "scratchpad -set abc9.script $PWD/abc9.script"
  fi
  sed "s/^synth_xilinx -flatten -abc9 /synth_xilinx -flatten -abc9 ${SYNTH_OPTS:-} /" synth_sram_netlist.ys
} > synth_sram_netlist.run.ys
echo "== synth_xilinx options: ${SYNTH_OPTS:-} ABC9_W=${ABC9_W:-} ABC9_SCRIPT=${ABC9_SCRIPT:-} =="
yosys -q -l synth_sram_netlist.log synth_sram_netlist.run.ys
echo "== synth_xilinx netlist of pe_fpga_sram_64x16 =="
grep -E '^ +[0-9]+ +(RAM|FD|LUT|MUX|CARRY)' sram_netlist.stat || true

# Mutants of the FPGA model: each must make the equivalence FAIL.
declare -A mutants=(
  [mut_write_updates_dout]='s/^        if (ren) dout_r <= din;/        dout_r <= din;/'
  [mut_read_first]='s/^        if (ren) dout_r <= din;/        if (ren) dout_r <= mem[addr];/'
  [mut_no_hold]='s/^      end else if (ren) begin/      end else begin/'
  [mut_ignore_men]='s/^    if (men) begin/    begin/'
)
run_mutants() {
  local rc=0 name dir
  for name in "${!mutants[@]}"; do
    dir="$work/$name"
    mkdir -p "$dir/fpga/formal" "$dir/fpga/rtl" "$dir/models"
    cp sram_equiv.sby sram_equiv_miter.sv "$dir/fpga/formal/"
    cp "$work/models/RM_IHPSG13_1P_core_behavioral.v" "$dir/models/"
    sed "${mutants[$name]}" "$work/fpga/rtl/pe_fpga_sram_64x16.v" > "$dir/fpga/rtl/pe_fpga_sram_64x16.v"
    if cmp -s "$work/fpga/rtl/pe_fpga_sram_64x16.v" "$dir/fpga/rtl/pe_fpga_sram_64x16.v"; then
      echo "    $name: mutation did not apply"; rc=1; continue
    fi
    if (cd "$dir/fpga/formal" && sby -f sram_equiv.sby rtl_mutant > sby.log 2>&1); then
      echo "    $name: equivalence still PASSES (mutant not killed)"; rc=1
    elif grep -q "DONE (FAIL" "$dir/fpga/formal/sby.log"; then
      echo "    $name: killed ($(grep -o 'Assert failed in [^ ]*' "$dir/fpga/formal/sby.log" | head -1 || true))"
    else
      echo "    $name: sby error, see $dir/fpga/formal/sby.log"; rc=1
    fi
  done
  return $rc
}

status=0
for task in "${tasks[@]}"; do
  if [ "$task" = mutants ]; then
    if run_mutants > mutants.log 2>&1; then echo "PASS mutants (all killed)"; else echo "FAIL mutants"; status=1; fi
    sed 's/^/    /' mutants.log
    continue
  fi
  if sby -f sram_equiv.sby "$task" > "sby_$task.log" 2>&1; then
    echo "PASS $task"
  else
    echo "FAIL $task (see $work/fpga/formal/sby_$task.log)"
    status=1
  fi
  tail -n 3 "sby_$task.log" | sed 's/^/    /'
done
exit $status
