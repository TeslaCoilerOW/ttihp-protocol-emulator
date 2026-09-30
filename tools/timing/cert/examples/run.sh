#!/usr/bin/env bash
# The worked example of docs/timing-certificates-tutorial.md, end to end.
#
#   tools/timing/cert/examples/run.sh WORK [RTL_DIR]
#
# RTL_DIR holds processor_fv.v (formal/run.sh --generate-only; default
# formal/build/rtl). Needs Python 3.10+ and OSS CAD Suite on PATH (sby,
# yosys, bitwuzla, boolector, yices). With ASSEMBLE set to the assembler
# (hardcaml/bin/assemble.exe), the image is first reassembled from its source
# and compared with the committed one; a difference stops the script (exit 1).
#
# Exit status: that of ci_prove.sh in section 5, which is 0 only when every
# certificate of the image is proved, every cover is reached and every
# negative control that ran fails. Sections 3 and 4 print their outcome
# without setting it (run_one.sh exits 0 for every outcome); section 5 runs
# the same certificate, cover and control again, with CI's solvers.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CERT=$(dirname "$HERE")
REPO=$(cd "$CERT/../../.." && pwd)
WORK=${1:?usage: run.sh WORK [RTL_DIR]}
RTL=${2:-$REPO/formal/build/rtl}
IMG=$HERE/toy-pulses.image.json
mkdir -p "$WORK"
if [ -n "${ASSEMBLE:-}" ]; then
  "$ASSEMBLE" --source "$HERE/toy-pulses.source.json" --output "$WORK/toy-pulses.image.json"
  cmp "$WORK/toy-pulses.image.json" "$IMG" || { echo "run.sh: assembler output differs from $IMG" >&2; exit 1; }
  echo "run.sh: assembler output equals $IMG"
fi
echo "== 1. static schedule"
python3 "$REPO/tools/timing/pe_timing.py" analyze "$IMG"
echo "== 2. certificates, one per boundary-graph node"
python3 "$CERT/gen_cert.py" emit "$IMG" --out "$WORK/certs" --rtl "$RTL" --models "$REPO/models"
echo "== 3. proof and cover of the PULL node's certificate"
"$CERT/run_one.sh" "$WORK/certs" cert_toy_pulses_n1 bmc
"$CERT/run_one.sh" "$WORK/certs" cert_toy_pulses_n1 cover
echo "== 4. negative control: one pad change predicted one edge late (must FAIL)"
python3 "$CERT/gen_cert.py" emit "$IMG" --out "$WORK/neg" --rtl "$RTL" --models "$REPO/models" \
    --schedule-mutant pad-late --pick-one
"$CERT/run_one.sh" "$WORK/neg" cert_toy_pulses_n1_neg_pad_late bmc
grep -E 'Assert failed|failed assertion|summary|DONE' "$WORK/neg/work/cert_toy_pulses_n1_neg_pad_late.bmc.log" || true
echo "== 5. every certificate, cover and negative control of the image, as CI runs them"
status=0
"$CERT/ci_prove.sh" "$WORK/ci" "$RTL" "$REPO/models" "$IMG" || status=$?
exit "$status"
