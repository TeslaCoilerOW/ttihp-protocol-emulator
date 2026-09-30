#!/usr/bin/env bash
# Design-of-record analogues of line-unit survivors (docs/extension.md section
# 11.4; results/diet8_rec16/dor_waitevent_analogues.tsv): apply one mutation of
# that table to the design of record's core, then run the base variant's
# default modules and test_line_spec_limit_bits on it, each module as its own
# make call (a failing module does not stop the others).
#
# Usage: dor_analogues.sh DESIGN TREE ID OUT
#   DESIGN  the design of record's mutation design directory, with base.il and
#           core_orig.v (the push campaign's $PUSH_DIR/design, campaign-push.env)
#   TREE    a git archive whose test/ has test_line_spec_limit_bits.py; its
#           test/Makefile's last default COCOTB_TEST_MODULES line (the base
#           variant's modules) gives the module list
#   ID      orig (the unmodified core_orig.v), or an id of the table
#           (0 = mutate -mode none, D331, D333, D1438, D1442)
#   OUT     output directory: mutant.v, yosys.log, <module>.log and .xml,
#           summary.txt (one line per module)
# Environment: TABLE (default: results/diet8_rec16/dor_waitevent_analogues.tsv
# next to this script); yosys, Icarus Verilog and cocotb on PATH.
set -uo pipefail
[ $# -eq 4 ] || { sed -n '2,20p' "$0" >&2; exit 2; }
DESIGN=$(cd "$1" && pwd); TREE=$(cd "$2" && pwd); ID=$3; OUT=$4
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TABLE=${TABLE:-$HERE/results/diet8_rec16/dor_waitevent_analogues.tsv}
[ -f "$DESIGN/base.il" ] && [ -f "$DESIGN/core_orig.v" ] || { echo "dor_analogues.sh: $DESIGN has no base.il/core_orig.v" >&2; exit 2; }
[ -f "$TREE/test/test_line_spec_limit_bits.py" ] || { echo "dor_analogues.sh: $TREE/test has no test_line_spec_limit_bits.py" >&2; exit 2; }
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)

if [ "$ID" = orig ]; then
  cp "$DESIGN/core_orig.v" "$OUT/mutant.v"
else
  CMD=$(awk -F'\t' -v id="$ID" '$1 == id {print $2}' "$TABLE")
  [ -n "$CMD" ] || { echo "dor_analogues.sh: no id $ID in $TABLE" >&2; exit 2; }
  case $CMD in
    "mutate -mode none") CMD="" ;;
    "mutate -mode cnot1 -module protocol_emulator_core -cell "*) ;;
    *) echo "dor_analogues.sh: unexpected command for $ID: $CMD" >&2; exit 2 ;;
  esac
  yosys -q -p "read_rtlil $DESIGN/base.il; $CMD; write_verilog -noattr $OUT/mutant.v" > "$OUT/yosys.log" 2>&1 \
    || { echo "dor_analogues.sh: yosys failed (yosys.log)" >&2; exit 1; }
fi

MODS=$(sed -n 's/^COCOTB_TEST_MODULES ?= //p' "$TREE/test/Makefile" | tail -1 | tr ',' ' ')
[ -n "$MODS" ] || { echo "dor_analogues.sh: no default module list in $TREE/test/Makefile" >&2; exit 2; }
export PYTHONDONTWRITEBYTECODE=1 PE_MINIMIZE=0
cd "$TREE/test" || exit 2
: > "$OUT/summary.txt"
for m in $MODS test_line_spec_limit_bits; do
  s=$(date +%s)
  make SIM_BUILD="$OUT/sim_build" WAVES=none PE_VARIANT=base PE_CORE="$OUT/mutant.v" \
    COCOTB_TEST_MODULES="$m" COCOTB_RESULTS_FILE="$OUT/$m.xml" > "$OUT/$m.log" 2>&1
  echo "$m rc $? $(( $(date +%s) - s )) s $(grep -o 'TESTS=[0-9]* PASS=[0-9]* FAIL=[0-9]* SKIP=[0-9]*' "$OUT/$m.log" | tail -1)" \
    | tee -a "$OUT/summary.txt"
done
rm -rf "$OUT/sim_build"
