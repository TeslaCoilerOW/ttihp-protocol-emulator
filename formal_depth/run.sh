#!/usr/bin/env bash
# formal_depth driver. See formal_depth/README.md.
#
#   formal_depth/run.sh generate          build every netlist the jobs read
#   formal_depth/run.sh submit WORK [ARGS] plan + one Slurm array per class
#                                          (ARGS go to portfolio.py plan:
#                                          --only REGEX --engine REGEX
#                                          --class light|heavy --timeout S)
#   formal_depth/run.sh local WORK [ARGS]  plan + run here, 4 tasks at a time
#   formal_depth/run.sh summarize WORK     results table (and depth curves)
#
# Environment:
#   FD_ROOT   work area (default $PE_WORK/formal-depth)
#   FD_REV    committed revision to verify (default 73536f0)
#   OCAML_ENV shell file that puts dune/opam on PATH (default $FD_ROOT/ocaml-env.sh)
#
# `generate` exports FD_REV with git archive into $FD_ROOT/snap (never the
# working tree), runs its formal/run.sh --generate-only (processor_fv.v,
# processor_debug.v, engine.v, fifo.v -> $FD_ROOT/fbuild/rtl), builds
# gen/generate_fd.ml in a private dune workspace and writes processor_fd.v, the
# negative-control mutants and engine_safety_inductive.sv to $FD_ROOT/rtl.
set -euo pipefail
FD=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(dirname "$FD")
ROOT=${FD_ROOT:-${PE_WORK:?set PE_WORK or FD_ROOT}/formal-depth}
REV=${FD_REV:-73536f0}
OCAML_ENV=${OCAML_ENV:-$ROOT/ocaml-env.sh}
export FD_SNAP=$ROOT/snap FD_RTL=$ROOT/fbuild/rtl FD_FDRTL=$ROOT/rtl
export PATH=${OSS_CAD_SUITE:?set OSS_CAD_SUITE to the OSS CAD Suite root}/bin:$OSS_CAD_SUITE/py3bin:$PATH

cmd=${1:-}; shift || true
case $cmd in
  generate)
    rm -rf "$FD_SNAP"; mkdir -p "$FD_SNAP" "$FD_FDRTL"
    git -C "$REPO" archive "$REV" | tar -x -C "$FD_SNAP"
    (cd "$FD_SNAP" && FORMAL_WORK=$ROOT/fbuild OCAML_ENV=$OCAML_ENV formal/run.sh --generate-only)
    (
      # shellcheck disable=SC1090
      command -v dune >/dev/null 2>&1 || . "$OCAML_ENV"
      hc=$ROOT/hc
      rm -rf "$hc"; mkdir -p "$hc/fdgen"
      cp -R "$FD_SNAP/hardcaml/dune-project" "$FD_SNAP/hardcaml/lib" "$FD_SNAP/hardcaml/bin" "$hc/"
      cp "$FD/gen/dune" "$FD/gen/generate_fd.ml" "$hc/fdgen/"
      (cd "$hc" && dune build --root . --build-dir "$ROOT/_build" -j "${DUNE_JOBS:-2}" ./fdgen/generate_fd.exe)
      "$ROOT/_build/default/fdgen/generate_fd.exe" --config "$FD_SNAP/configs/instruction-sram-32.json" \
        --output "$FD_FDRTL/processor_fd.raw.v" > "$FD_FDRTL/processor_fd.attribution.txt"
      python3 "$FD/gen/expose_fifo_mem.py" "$FD_FDRTL/processor_fd.raw.v" "$FD_FDRTL/processor_fd.v"
      rm -f "$FD_FDRTL/processor_fd.raw.v"
      python3 "$FD/gen/make_dut_include.py" "$FD_FDRTL/processor_fd.v" "$FD_FDRTL/fd_dut.vh"
      cmp -s "$FD_FDRTL/fd_dut.vh" "$FD/harness/fd_dut.vh" || {
        echo "run.sh: harness/fd_dut.vh is stale; copy $FD_FDRTL/fd_dut.vh over it" >&2; exit 1; }
      python3 "$FD/gen/mutants.py" "$FD_SNAP" "$ROOT" "$FD_FDRTL"
      rm -rf "$ROOT/mut"
    )
    python3 "$FD/gen/make_engine_inductive.py" "$FD_SNAP/formal/engine_safety.sv" \
      "$FD_FDRTL/engine_safety_inductive.sv"
    echo "run.sh: netlists in $FD_RTL and $FD_FDRTL"
    ;;
  submit)
    work=$1; shift
    FD_DIR=$FD "$FD/submit.sh" "$work" "$@"
    ;;
  local)
    work=$1; shift
    python3 "$FD/portfolio.py" plan "$work" "$@"
    for tsv in "$work"/tasks-*.tsv; do
      cls=$(basename "$tsv" .tsv); cls=${cls#tasks-}
      FD_DIR=$FD "$FD/run_parallel.sh" "$work" "$cls" "${FD_PARALLEL:-4}"
    done
    python3 "$FD/portfolio.py" summarize "$work"
    ;;
  summarize)
    python3 "$FD/portfolio.py" summarize "$@"
    ;;
  *)
    sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
