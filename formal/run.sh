#!/usr/bin/env bash
# Formal verification driver. See formal/README.md.
#
# Usage: formal/run.sh [--variant NAME] [--no-generate | --generate-only] [--list] [JOB ...]
#   JOB              one or more job names from --list (default: every job)
#   --no-generate    reuse formal/build/rtl (e.g. a CI artifact) instead of
#                    regenerating it from hardcaml/
#   --generate-only  only (re)generate formal/build/rtl
#   --variant NAME   a design variant (configs/variants/NAME.json): its RTL goes
#                    to formal/build/variants/NAME/rtl and every job runs with
#                    the variant's harness settings (README.md, "Design variants")
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

generate=1
selected=()
for arg in "$@"; do
  case "$arg" in
    --no-generate) generate=0 ;;
    --generate-only) generate=2 ;;
    --list) for j in "${JOBS[@]}"; do echo "${j%%:*}"; done; exit 0 ;;
    -h|--help) sed -n '2,29p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "run.sh: unknown option $arg" >&2; exit 2 ;;
    *) selected+=("$arg") ;;
  esac
done
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

SUMMARY=$BUILD/summary.tsv
printf 'job\tsby\ttask\tstatus\texpected_met\tseconds\n' > "$SUMMARY"
failures=0
for name in "${selected[@]}"; do
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
  # A negative control counts only if a pin-equality assertion (one that
  # compares the owned pins, '& own') fired, not e.g. the observation-port
  # sanity check.
  if [ $rc -eq 0 ] && [[ $name == *_neg_* ]]; then
    fired=$(sed -n 's/.*Assert failed in [^:]*: \([A-Za-z0-9_]*\.sv\):\([0-9]*\)\..*/\1:\2/p' \
      "$BUILD/sby/$name.log" | head -n 1)
    if [ -z "$fired" ] || ! sed -n "${fired#*:}p" "$FORMAL/${fired%%:*}" | grep -q '& own'; then
      echo "run.sh: $name failed, but not on a pin-equality assertion (${fired:-none found})" >&2
      rc=1
    else
      echo "  negative control fired the pin assertion at $fired"
    fi
  fi
  if [ $rc -eq 0 ]; then met=yes; else met=no; failures=$((failures + 1)); fi
  grep -E "summary: (engine|successful|  failed|  reached)|DONE" "$BUILD/sby/$name.log" \
    | sed 's/^SBY [0-9:]* \[[^]]*\] /  /' || true
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$file" "$task" "$status" "$met" "$seconds" >> "$SUMMARY"
  echo "--- $name: $status (expectation met: $met) in ${seconds}s"
done
echo
column -t -s $'\t' "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
if [ $failures -ne 0 ]; then
  echo "run.sh: $failures job(s) did not meet their expectation" >&2
  exit 1
fi
