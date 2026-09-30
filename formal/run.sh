#!/usr/bin/env bash
# Formal verification driver. See formal/README.md.
#
# Usage: formal/run.sh [--variant NAME] [--no-generate | --generate-only] [--list]
#                     [--ci] [--parallel N] [JOB ...]
#   JOB              one or more job names from --list (default: every job)
#   --no-generate    reuse formal/build/rtl (e.g. a CI artifact) instead of
#                    regenerating it from hardcaml/
#   --generate-only  only (re)generate formal/build/rtl
#   --variant NAME   a design variant (configs/variants/NAME.json): its RTL goes
#                    to formal/build/variants/NAME/rtl and every job runs with
#                    the variant's harness settings (README.md, "Design variants");
#                    a variant with options.line_unit also gets the line_* jobs
#                    (README.md, "Line unit")
#   --ci             with a line-unit variant: the line-unit CI subset (every
#                    line_* job except the long bounded checks, README.md,
#                    "Line unit"); with --list, list it
#   --parallel N     run up to N jobs at a time (default 1); reports are printed
#                    as jobs finish, the summary table is in job order
#
# Environment:
#   FORMAL_VARIANT same as --variant
#   VARIANT_CORES  with --variant: directory of published cores (NAME.v); the
#                  generated variant core must equal NAME.v below its header line
#   FORMAL_CONFIG  refinement config (default configs/instruction-sram-32.json,
#                  or configs/variants/NAME.json with --variant)
#   FORMAL_WORK    put the build tree elsewhere; formal/build becomes a symlink
#   DUNE_JOBS      dune parallelism (default 2)
#   OCAML_ENV      shell file to source when dune is not on PATH (as in
#                  scripts/generate.sh; scripts/ocaml-env.local.sh is also tried)
#   SBY_TIMEOUT    per-job wall-clock limit in seconds (default 1500)
#   FORMAL_PARALLEL same as --parallel
#
# Exit status is non-zero if any job's outcome differs from its expectation:
# proofs/BMC/covers must PASS, negative controls must FAIL (sby 'expect fail').
set -euo pipefail

FORMAL=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(dirname "$FORMAL")
VARIANT=${FORMAL_VARIANT:-}
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --variant) [ $# -ge 2 ] || { echo "run.sh: --variant needs a name" >&2; exit 2; }
               VARIANT=$2; shift 2 ;;
    --variant=*) VARIANT=${1#--variant=}; shift ;;
    *) args+=("$1"); shift ;;
  esac
done
set -- ${args[@]+"${args[@]}"}
if [ -n "$VARIANT" ]; then
  [ -f "$REPO/configs/variants/$VARIANT.json" ] || {
    echo "run.sh: no configs/variants/$VARIANT.json" >&2; exit 2; }
  CONFIG=${FORMAL_CONFIG:-$REPO/configs/variants/$VARIANT.json}
else
  CONFIG=${FORMAL_CONFIG:-$REPO/configs/instruction-sram-32.json}
fi
case "$CONFIG" in /*) ;; *) CONFIG=$PWD/$CONFIG ;; esac
SBY_TIMEOUT=${SBY_TIMEOUT:-1500}

# job name -> "sby-file task"
JOBS=(
  "reset_safety:reset_safety.sby prove"
  "fifo_conservation:fifo_conservation.sby prove"
  "engine_safety:engine_safety.sby bmc"
  "processor_invariants_bmc:processor_invariants.sby bmc"
  "processor_invariants_prove:processor_invariants.sby prove"
  "processor_inductive_bmc:processor_inductive.sby bmc"
  "processor_inductive_prove:processor_inductive.sby prove"
  "processor_inductive_cover:processor_inductive.sby cover"
  "timing_isolation_prove_k0:timing_isolation.sby prove"
  "timing_isolation_prove_k1:timing_isolation.sby prove_k1"
  "timing_isolation_prove_k2:timing_isolation.sby prove_k2"
  "timing_isolation_prove_k3:timing_isolation.sby prove_k3"
  "timing_isolation_bmc:timing_isolation.sby bmc"
  "timing_isolation_cover:timing_isolation.sby cover"
  "timing_isolation_neg_pull:timing_isolation.sby neg_pull"
  "timing_isolation_neg_mutant:timing_isolation.sby neg_mutant"
)

# Line-unit jobs (formal/line_*.sv, docs/extension.md), only for a variant
# whose config sets options.line_unit.
LINE_UNIT=""
if [ -n "$VARIANT" ] && python3 - "$CONFIG" <<'PY'
import json, sys
sys.exit(0 if json.load(open(sys.argv[1])).get("options", {}).get("line_unit", "none") != "none" else 1)
PY
then
  LINE_UNIT=1
  JOBS+=(
    "line_crc_equiv:line_crc.sby equiv"
    "line_crc_equiv_neg:line_crc.sby equiv_neg"
    "line_crc_e2e:line_crc.sby e2e"
    "line_crc_e2e_neg:line_crc.sby e2e_neg"
    "line_codec_bmc:line_codec.sby bmc"
    "line_codec_bmc_p2:line_codec.sby bmc_p2"
    "line_codec_neg_nrzi:line_codec.sby neg_nrzi"
    "line_codec_neg_destuff:line_codec.sby neg_destuff"
    "line_codec_neg_manchester:line_codec.sby neg_manchester"
    "line_pins_bmc:line_engine.sby pins"
    "line_pins_neg:line_engine.sby pins_neg"
    "line_reset_bmc:line_engine.sby reset"
    "line_reset_neg:line_engine.sby reset_neg"
    "line_decode_bmc:line_engine.sby decode"
    "line_decode_neg:line_engine.sby decode_neg"
    "line_pins_prove:line_engine.sby pins_prove"
    "line_reset_prove:line_engine.sby reset_prove"
    "line_decode_prove:line_engine.sby decode_prove"
    "line_tick_prove:line_tick.sby prove"
    "line_tick_cover:line_tick.sby cover"
    "line_tick_neg:line_tick.sby neg"
    "line_crc_tx_prove:line_crc.sby tx_prove"
    "line_crc_tx_cover:line_crc.sby tx_cover"
    "line_crc_tx_neg:line_crc.sby tx_neg"
    "line_crc_tx_bmc:line_crc.sby tx_bmc"
    "line_crc_rx_prove:line_crc.sby rx_prove"
    "line_crc_rx_cover:line_crc.sby rx_cover"
    "line_crc_rx_neg:line_crc.sby rx_neg"
    "line_crc_rx_bmc:line_crc.sby rx_bmc"
    "line_crc_rx_se0_stuff_cover:line_crc.sby rx_se0_stuff"
    "line_codec_prove:line_codec.sby prove"
    "line_codec_cover:line_codec.sby cover"
    "line_codec_prove_neg_nrzi:line_codec.sby prove_neg_nrzi"
    "line_codec_prove_neg_destuff:line_codec.sby prove_neg_destuff"
    "line_codec_prove_neg_manchester:line_codec.sby prove_neg_manchester"
    "line_tx_prove:line_tx.sby prove"
    "line_tx_cover:line_tx.sby cover"
    "line_tx_neg_stuff:line_tx.sby neg_stuff"
    "line_tx_neg_manchester:line_tx.sby neg_manchester"
    "line_rx_prove:line_rx.sby prove"
    "line_rx_cover:line_rx.sby cover"
    "line_rx_neg_nrzi:line_rx.sby neg_nrzi"
    "line_rx_neg_destuff:line_rx.sby neg_destuff"
    "line_arb_prove:line_arb.sby prove"
    "line_arb_cover:line_arb.sby cover"
    "line_arb_neg:line_arb.sby neg"
  )
fi
# The line-unit CI subset (--ci): every line_* job except these long bounded
# checks (README.md, "Line unit", has their run times).
CI_EXCLUDE=" line_crc_e2e line_codec_bmc line_crc_tx_bmc line_crc_rx_bmc "

generate=1
selected=()
list=0
ci=0
parallel=${FORMAL_PARALLEL:-1}
while [ $# -gt 0 ]; do
  case "$1" in
    --no-generate) generate=0 ;;
    --generate-only) generate=2 ;;
    --list) list=1 ;;
    --ci) ci=1 ;;
    --parallel) [ $# -ge 2 ] || { echo "run.sh: --parallel needs a number" >&2; exit 2; }
                parallel=$2; shift ;;
    --parallel=*) parallel=${1#--parallel=} ;;
    -h|--help) sed -n '2,35p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "run.sh: unknown option $1" >&2; exit 2 ;;
    *) selected+=("$1") ;;
  esac
  shift
done
case "$parallel" in ''|*[!0-9]*|0) echo "run.sh: --parallel needs a positive number" >&2; exit 2 ;; esac
if [ $ci = 1 ]; then
  [ -n "$LINE_UNIT" ] || { echo "run.sh: --ci needs a line-unit variant (--variant)" >&2; exit 2; }
  [ ${#selected[@]} -eq 0 ] || { echo "run.sh: --ci takes no job names" >&2; exit 2; }
  for j in "${JOBS[@]}"; do
    name=${j%%:*}
    [[ $name == line_* ]] && [[ $CI_EXCLUDE != *" $name "* ]] && selected+=("$name")
  done
fi
if [ $list = 1 ]; then
  if [ $ci = 1 ]; then printf '%s\n' "${selected[@]}"
  else for j in "${JOBS[@]}"; do echo "${j%%:*}"; done; fi
  exit 0
fi
if [ ${#selected[@]} -eq 0 ]; then
  for j in "${JOBS[@]}"; do selected+=("${j%%:*}"); done
fi
lookup() {
  local j
  for j in "${JOBS[@]}"; do [ "${j%%:*}" = "$1" ] && { echo "${j#*:}"; return 0; }; done
  echo "run.sh: unknown job $1 (see --list)" >&2; return 1
}
for name in "${selected[@]}"; do lookup "$name" >/dev/null; done

if [ -n "${FORMAL_WORK:-}" ]; then
  mkdir -p "$FORMAL_WORK"
  if [ ! -e "$FORMAL/build" ]; then ln -s "$FORMAL_WORK" "$FORMAL/build"; fi
fi
BUILD=$FORMAL/build
[ -n "$VARIANT" ] && BUILD=$FORMAL/build/variants/$VARIANT
RTL=$BUILD/rtl
mkdir -p "$RTL" "$BUILD/sby"

generate_rtl() (
  # Subshell: an OCaml environment file may replace PATH; keep that local.
  if ! command -v dune >/dev/null 2>&1; then
    for env_file in "${OCAML_ENV:-}" "$REPO/scripts/ocaml-env.local.sh"; do
      if [ -n "$env_file" ] && [ -f "$env_file" ]; then
        # shellcheck disable=SC1090
        . "$env_file"
        break
      fi
    done
  fi
  command -v dune >/dev/null 2>&1 || {
    echo "run.sh: dune not found; install scripts/opam-deps.txt or set OCAML_ENV" >&2; exit 2; }
  # A private dune workspace: a copy of hardcaml/{lib,bin} plus the
  # formal-only observation generator in formal/gen. hardcaml/ is not touched.
  local hc=$BUILD/hc
  rm -rf "$hc"
  mkdir -p "$hc/fvgen"
  cp -R "$REPO/hardcaml/dune-project" "$REPO/hardcaml/lib" "$REPO/hardcaml/bin" "$hc/"
  cp "$FORMAL/gen/dune" "$FORMAL/gen/generate_fv.ml" "$hc/fvgen/"
  local extra=()
  if [ -n "$VARIANT" ]; then
    mkdir -p "$hc/fvvariant"
    cp "$FORMAL/gen/variant/dune" "$FORMAL/gen/variant/generate_blocks.ml" "$hc/fvvariant/"
    extra=(./fvvariant/generate_blocks.exe ./bin/generate_refinement.exe)
  fi
  (cd "$hc" && dune build --root . --build-dir "$BUILD/_build" -j "${DUNE_JOBS:-2}" \
      ./bin/generate_formal.exe ./bin/generate_refinement_formal.exe ./fvgen/generate_fv.exe \
      ${extra[@]+"${extra[@]}"})
  local exe=$BUILD/_build/default
  python3 - "$CONFIG" "$RTL/architecture.json" <<'PY'
import json, sys
config = json.load(open(sys.argv[1]))
if config.get("schema_version") != "protocol-emulator.refinement.v1":
    raise SystemExit("run.sh: FORMAL_CONFIG must be an instruction-SRAM refinement config")
json.dump(config["architecture"], open(sys.argv[2], "w"))
PY
  if [ -n "$VARIANT" ]; then
    # The standalone blocks with the variant's options, and the variant core
    # itself (reset_safety reads it in place of src/protocol_emulator_core.v).
    "$exe/fvvariant/generate_blocks.exe" --config "$CONFIG" --target fifo --output "$RTL/fifo.v"
    "$exe/fvvariant/generate_blocks.exe" --config "$CONFIG" --target engine --output "$RTL/engine.v"
    "$exe/bin/generate_refinement.exe" --config "$CONFIG" --output "$RTL/protocol_emulator_core.v" \
        --name protocol_emulator_core
    if [ -n "$LINE_UNIT" ]; then
      # line_* jobs: the engine with observation ports, the CRC step, and
      # one seeded defect per negative control (Line_unit.mutation)
      "$exe/fvvariant/generate_blocks.exe" --config "$CONFIG" --target engine_line --output "$RTL/engine_line.v"
      "$exe/fvvariant/generate_blocks.exe" --config "$CONFIG" --target crc_step --output "$RTL/crc_step.v"
      "$exe/fvvariant/generate_blocks.exe" --config "$CONFIG" --target crc_step --mutation crc_tap \
          --output "$RTL/crc_step_crc_tap.v"
      for m in crc_tap nrzi_decode rx_destuff_run manchester_halves pin_leak start_keeps_crc ltim_overflow \
               stuff_run fraction_carry arbitration_off; do
        "$exe/fvvariant/generate_blocks.exe" --config "$CONFIG" --target engine_line --mutation "$m" \
            --output "$RTL/engine_line_$m.v"
      done
    fi
    if [ -n "${VARIANT_CORES:-}" ]; then
      cmp -s <(tail -n +2 "$VARIANT_CORES/$VARIANT.v") "$RTL/protocol_emulator_core.v" || {
        echo "run.sh: generated $VARIANT core differs from $VARIANT_CORES/$VARIANT.v" >&2; exit 1; }
      echo "run.sh: generated core equals $VARIANT_CORES/$VARIANT.v below its header line"
    fi
  else
  "$exe/bin/generate_formal.exe" --config "$RTL/architecture.json" --target fifo --output "$RTL/fifo.v"
  "$exe/bin/generate_formal.exe" --config "$RTL/architecture.json" --target engine --output "$RTL/engine.v"
  fi
  "$exe/bin/generate_refinement_formal.exe" --config "$CONFIG" --target processor \
      --output "$RTL/processor_debug.v"
  "$exe/fvgen/generate_fv.exe" --config "$CONFIG" --output "$RTL/processor_fv.v" \
      > "$RTL/processor_fv.attribution.txt"
  python3 "$FORMAL/mutate.py" sram-host-stall 0 "$RTL/processor_fv.v" "$RTL/processor_fv_mutant.v"
  # The harness parameters below assume this architecture (a variant's
  # fifo_words and options are applied to derived .sby files instead).
  python3 - "$RTL/architecture.json" "$VARIANT" <<'PY'
import json, sys
a = json.load(open(sys.argv[1]))
want = {"engine_count": 4, "data_width": 32} if sys.argv[2] else {"engine_count": 4, "data_width": 32, "fifo_words": 8}
bad = {k: a[k] for k in want if a[k] != want[k]}
if bad:
    raise SystemExit(f"run.sh: .sby chparam settings assume {want}; config has {bad}")
PY
  echo "run.sh: generated RTL in $RTL"
)

# Variant: derive the .sby files (harness defines, parameters, absolute paths).
derive_sby() {
  python3 "$FORMAL/variant_sby.py" --config "$CONFIG" --formal "$FORMAL" --repo "$REPO" \
      --rtl "$RTL" --output "$BUILD/sbyfiles"
}

if [ "$generate" != 0 ]; then generate_rtl; fi
SBYDIR=$FORMAL
if [ -n "$VARIANT" ]; then derive_sby; SBYDIR=$BUILD/sbyfiles; fi
[ "$generate" = 2 ] && exit 0
for f in fifo.v engine.v processor_debug.v processor_fv.v processor_fv_mutant.v; do
  [ -f "$RTL/$f" ] || { echo "run.sh: missing $RTL/$f (run without --no-generate)" >&2; exit 2; }
done
if [ -n "$VARIANT" ] && [ ! -f "$RTL/protocol_emulator_core.v" ]; then
  echo "run.sh: missing $RTL/protocol_emulator_core.v (run without --no-generate)" >&2; exit 2
fi
if [ -n "$LINE_UNIT" ] && [ ! -f "$RTL/engine_line_ltim_overflow.v" ]; then
  echo "run.sh: missing the line-unit RTL in $RTL (run without --no-generate)" >&2; exit 2
fi

SUMMARY=$BUILD/summary.tsv
printf 'job\tsby\ttask\tstatus\texpected_met\tseconds\n' > "$SUMMARY"
# run_job NAME: run one job, print its report, write its summary row to
# $BUILD/sby/NAME.row; the return status says whether its expectation was met.
run_job() {
  local name=$1 file task workdir start rc seconds status fired met
  read -r file task <<<"$(lookup "$name")"
  workdir=$BUILD/sby/$name
  echo "=== $name ($file $task)"
  start=$(date +%s)
  set +e
  (cd "$SBYDIR" && timeout "$SBY_TIMEOUT" sby -f -d "$workdir" "$file" "$task") > "$BUILD/sby/$name.log" 2>&1
  rc=$?
  set -e
  seconds=$(( $(date +%s) - start ))
  status=$(sed -n 's/.*DONE (\([A-Z]*\), rc=.*/\1/p' "$BUILD/sby/$name.log" | tail -n 1)
  [ -n "$status" ] || status=$([ $rc -eq 124 ] && echo TIMEOUT || echo ERROR)
  # A line-unit negative control (line_*_neg*) counts only if the assertion it
  # targets fired: that assertion's line names the job ("target of: <job>").
  if [ $rc -eq 0 ] && [[ $name == line_* ]] && [[ $name == *_neg* ]]; then
    fired=$(sed -n 's/.*Assert failed in [^:]*: \([A-Za-z0-9_]*\.sv\):\([0-9]*\)\..*/\1:\2/p' \
      "$BUILD/sby/$name.log" | head -n 1)
    if [ -z "$fired" ] || ! sed -n "${fired#*:}p" "$FORMAL/${fired%%:*}" | grep -q "target of:.*\b$name\b"; then
      echo "run.sh: $name failed, but not on its target assertion (${fired:-none found})" >&2
      rc=1
    else
      echo "  negative control fired its target assertion at $fired"
    fi
  # A negative control counts only if a pin-equality assertion (one that
  # compares the owned pins, '& own') fired, not e.g. the observation-port
  # sanity check.
  elif [ $rc -eq 0 ] && [[ $name == *_neg_* ]]; then
    fired=$(sed -n 's/.*Assert failed in [^:]*: \([A-Za-z0-9_]*\.sv\):\([0-9]*\)\..*/\1:\2/p' \
      "$BUILD/sby/$name.log" | head -n 1)
    if [ -z "$fired" ] || ! sed -n "${fired#*:}p" "$FORMAL/${fired%%:*}" | grep -q '& own'; then
      echo "run.sh: $name failed, but not on a pin-equality assertion (${fired:-none found})" >&2
      rc=1
    else
      echo "  negative control fired the pin assertion at $fired"
    fi
  fi
  if [ $rc -eq 0 ]; then met=yes; else met=no; fi
  grep -E "summary: (engine|successful|  failed|  reached)|DONE" "$BUILD/sby/$name.log" \
    | sed 's/^SBY [0-9:]* \[[^]]*\] /  /' || true
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$file" "$task" "$status" "$met" "$seconds" > "$BUILD/sby/$name.row"
  echo "--- $name: $status (expectation met: $met) in ${seconds}s"
  [ $met = yes ]
}
failures=0
wall=$(date +%s)
if [ "$parallel" -le 1 ]; then
  for name in "${selected[@]}"; do
    run_job "$name" || failures=$((failures + 1))
    cat "$BUILD/sby/$name.row" >> "$SUMMARY"
  done
else
  # Up to $parallel jobs at a time; each report is printed when its job ends.
  declare -A pid_of=()
  finish_one() {
    local p name
    wait -n || true
    for name in "${!pid_of[@]}"; do
      p=${pid_of[$name]}
      if ! kill -0 "$p" 2>/dev/null; then
        wait "$p" 2>/dev/null || true
        cat "$BUILD/sby/$name.report"
        unset "pid_of[$name]"
      fi
    done
  }
  for name in "${selected[@]}"; do
    while [ ${#pid_of[@]} -ge "$parallel" ]; do finish_one; done
    rm -f "$BUILD/sby/$name.row"
    ( run_job "$name" || true ) > "$BUILD/sby/$name.report" 2>&1 &
    pid_of[$name]=$!
  done
  while [ ${#pid_of[@]} -gt 0 ]; do finish_one; done
  for name in "${selected[@]}"; do
    if [ -f "$BUILD/sby/$name.row" ]; then cat "$BUILD/sby/$name.row" >> "$SUMMARY"
    else printf '%s\t-\t-\tERROR\tno\t-\n' "$name" >> "$SUMMARY"; fi
    [ "$(cut -f5 "$BUILD/sby/$name.row" 2>/dev/null)" = yes ] || failures=$((failures + 1))
  done
fi
wall=$(( $(date +%s) - wall ))
echo
column -t -s $'\t' "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
echo "run.sh: ${#selected[@]} job(s) in ${wall}s wall-clock (--parallel $parallel)"
if [ $failures -ne 0 ]; then
  echo "run.sh: $failures job(s) did not meet their expectation" >&2
  exit 1
fi
