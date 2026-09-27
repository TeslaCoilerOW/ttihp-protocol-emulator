#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Slurm job body: the RTL-vs-netlist equivalence check on a promoted
# configuration's final netlist (docs/optimization.md, "Promotion" and
# "Equivalence check"). It runs after a legal full run, next to the precheck and
# the gate-level tests.
#
#   eq_job.sh NETLIST CORE CORE_SHA256 TREE OUT_DIR LIMIT_S VARIANT PDK_ROOT [PDK_SOURCE]
#
# gold = TREE/src/project.v + CORE (the track's core as the full run was built from
# it: src/ for the design-of-record tracks, variants6x4/ for the 6x4 track, the
# published variant core for a variant track; TREE is the frozen tree of that
# build); gate = NETLIST (the unpowered final netlist of the promotion's sub/).
# VARIANT is formal_eq's --variant (base, diet4 or the variant's name); PDK_ROOT is
# the PDK root the full run used (its out/resolved.json), passed as --pdk-root.
#
# The checker is formal_eq/eq_check.py of the driver's frozen tree ($PE_OPT_HARNESS/../..;
# $PE_EQ_CHECK overrides it), run as
#   eq_check.py check --netlist NETLIST --variant VARIANT --core CORE --top TREE/src/project.v
#       --pdk-root PDK_ROOT --timeout LIMIT_S --build-timeout 1200 --jobs 1 --out <node-local dir>
# with yosys and yosys-abc from $OSS_CAD_SUITE/bin (else PATH). The whole checker
# runs under `timeout LIMIT_S + 1200`. Its work directory is node-local; the job
# keeps OUT_DIR/formal_eq/{result.json, *.ys, abc.log, gold.log.gz, gate.log.gz,
# miter.log.gz} and OUT_DIR/checker.log. OUT_DIR/result.json (tools/opt/gates.py
# eq-collect) passes only if formal_eq's result.json says "equivalent" with exit
# code 0 for exactly these inputs; anything else is a failure with its reason.
set -uo pipefail
NL=${1:?usage: eq_job.sh NETLIST CORE CORE_SHA256 TREE OUT_DIR LIMIT_S VARIANT PDK_ROOT [PDK_SOURCE]}
CORE=${2:?}
CORE_SHA=${3:?}
TREE=${4:?}
OUT=${5:?}
LIMIT=${6:?}
VARIANT=${7:?}
PDK=${8:?}
PDK_SOURCE=${9:-argument}
# Under Slurm $0 is the spooled copy of this script, so the tree is found through
# PE_OPT_HARNESS (the driver exports its tools/opt), else next to this file.
HARNESS=${PE_OPT_HARNESS:-$(cd "$(dirname "$0")" && pwd)}
MYTREE=$(cd "$HARNESS/../.." && pwd)
CHECK=${PE_EQ_CHECK:-$MYTREE/formal_eq/eq_check.py}
PY=${PE_OPT_PY:-}
if [ -z "$PY" ] || ! "$PY" -c 'import sys' 2>/dev/null; then PY=$(command -v python3 || echo /usr/bin/python3); fi
JOB=${SLURM_JOB_ID:-local$$}
BUILD_T=1200
mkdir -p "$OUT/formal_eq"
rm -f "$OUT/result.json" "$OUT/formal_eq/result.json"
LOCAL=$(mktemp -d "${TMPDIR:-/tmp}/pe-opt-eq.XXXXXX")
trap 'rm -rf "$LOCAL"' EXIT
NL_SHA=$(sha256sum "$NL" 2>/dev/null | cut -d' ' -f1)
echo "host $(hostname -s) job $JOB restart ${SLURM_RESTART_COUNT:-0} start $(date -Is) checker $CHECK" \
     "variant $VARIANT pdk $PDK limit ${LIMIT}s oss_cad_suite ${OSS_CAD_SUITE:-<PATH>}"
start=$(date +%s)
if [ -f "$CHECK" ]; then
  timeout --kill-after=60 $((LIMIT + BUILD_T)) "$PY" -B "$CHECK" check --netlist "$NL" --variant "$VARIANT" \
    --core "$CORE" --top "$TREE/src/project.v" --pdk-root "$PDK" --timeout "$LIMIT" \
    --build-timeout "$BUILD_T" --jobs 1 --label netlist --out "$LOCAL/fe" > "$OUT/checker.log" 2>&1
  rc=$?
else
  echo "checker $CHECK not found" > "$OUT/checker.log"
  rc=127
fi
wall=$(( $(date +%s) - start ))
echo "checker exit $rc after $wall s"
[ -f "$LOCAL/fe/result.json" ] && cp "$LOCAL/fe/result.json" "$OUT/formal_eq/result.json"
for f in gold/gold.ys netlist/gate.ys netlist/miter.ys netlist/abc.log; do
  [ -f "$LOCAL/fe/$f" ] && cp "$LOCAL/fe/$f" "$OUT/formal_eq/"
done
for f in gold/gold.log netlist/gate.log netlist/miter.log; do
  [ -f "$LOCAL/fe/$f" ] && gzip -c "$LOCAL/fe/$f" > "$OUT/formal_eq/$(basename "$f").gz"
done
"$PY" -B "$HARNESS/gates.py" eq-collect --fe-result "$OUT/formal_eq/result.json" --out "$OUT" \
  --netlist "$NL" --netlist-sha "${NL_SHA:-}" --core "$CORE" --core-sha "$CORE_SHA" --tree "$TREE" \
  --variant "$VARIANT" --pdk-root "$PDK" --pdk-source "$PDK_SOURCE" --checker "$CHECK" \
  --checker-tree "$MYTREE" --job "$JOB" --limit "$LIMIT" --outer-exit "$rc" --wall "$wall"
exit 0
