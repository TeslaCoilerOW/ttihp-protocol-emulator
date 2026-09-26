#!/usr/bin/env bash
# Reproduce the repository's headline checks with one command.
# See docs/results.md for what each check reproduces and for the claims that
# need a cluster.
#
# Usage: scripts/reproduce.sh [--full] [--parallel] [--only STEP,...] [--skip STEP,...]
#                             [--list] [--print-cluster] [-h|--help]
#
# Quick tier (the default; `make reproduce`):
#   regen     regenerate the core from hardcaml/ + configs/instruction-sram-32.json
#             into the work directory and compare it byte for byte with
#             src/protocol_emulator_core.v (src/ is never written)
#   lint      scripts/lint.sh: iverilog elaboration, yosys hierarchy -check with
#             exactly 8 SRAM macros, verilator -Wall
#   cocotb    the cocotb RTL suite in test/ (`make clean; make`), as the test
#             action runs it; every test must pass
#   formal    a formal/ subset: reset_safety, engine_safety, the processor
#             invariant and inductive proofs and cover, timing isolation for
#             engines 0-3 (unbounded), its cover and both negative controls
#   timing    tools/timing/pe_timing.py report on firmware/, compared with the
#             committed tools/timing/report/, plus the analyzer's unit tests
#   host      host library unit tests (python3 -m unittest discover -s host/tests)
#
# Full tier (--full; `make reproduce-full`) adds:
#   formal-rest      the other four formal/ jobs: fifo_conservation,
#                    processor_invariants_bmc, processor_inductive_bmc and
#                    timing_isolation_bmc (the longest, about 8 to 10 minutes)
#   peers            test_ext/: the firmware against vendored third-party peers (RTL)
#   variants         scripts/gen_variants.sh with its byte-identity checks
#   hardcaml         the Hardcaml unit tests (dune test in hardcaml/)
#   timing-validate  pe_timing validate: scenarios, legacy, event, mutants, margins
#   gl               the gate-level subset of test/ on a hardened netlist; runs
#                    only when PDK_ROOT and GL_NETLIST are set, SKIP otherwise
# and then prints the cluster-scale campaign commands (--print-cluster prints
# only those and runs nothing).
#
# Options:
#   --full           add the full-tier steps
#   --parallel       run independent groups of steps concurrently (each step
#                    still runs single-threaded; about 4 CPUs are useful)
#   --only S1,S2     run only these steps (any tier)
#   --skip S1,S2     leave these steps out
#   --list           list the steps and exit
#
# Environment:
#   REPRO_WORK      work directory (default build/reproduce, gitignored); logs are
#                   in REPRO_WORK/logs/<step>.log, the summary in REPRO_WORK/summary.tsv
#   OCAML_ENV       shell file that puts dune on PATH (as for scripts/generate.sh);
#                   scripts/ocaml-env.local.sh is also tried
#   DUNE_JOBS       dune parallelism (default 2)
#   SBY_TIMEOUT     per-job formal time limit in seconds (default 1500)
#   PDK_ROOT, GL_NETLIST   for the gl step (the gds action's tt_submission netlist
#                   and an IHP-Open-PDK checkout that contains ihp-sg13cmos5l)
#
# Toolchain (see README.md and docs/results.md): OCaml 5.2.1 with the opam pins
# of scripts/opam-deps.txt; OSS CAD Suite 2026-07-29 (yosys, sby, yices,
# bitwuzla, verilator); Icarus Verilog 13.0 (the CI build) or 14; Python 3.10+
# with the packages of test/requirements.txt (cocotb 2.0.1).
#
# Exit status: 0 when no step FAILs (SKIP is allowed only for gl), 1 otherwise,
# 2 on a usage error.
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO"
export PYTHONDONTWRITEBYTECODE=1

QUICK_STEPS=(regen lint cocotb formal timing host)
FULL_STEPS=(formal-rest peers variants hardcaml timing-validate gl)
FORMAL_QUICK_JOBS=(reset_safety engine_safety processor_invariants_prove processor_inductive_prove
  processor_inductive_cover timing_isolation_prove_k0 timing_isolation_prove_k1
  timing_isolation_prove_k2 timing_isolation_prove_k3 timing_isolation_cover
  timing_isolation_neg_pull timing_isolation_neg_mutant)
FORMAL_REST_JOBS=(fifo_conservation processor_invariants_bmc processor_inductive_bmc timing_isolation_bmc)

usage() { sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

FULL=0 PARALLEL=0 ONLY="" SKIP="" LIST=0 CLUSTER_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --full) FULL=1; shift ;;
    --parallel) PARALLEL=1; shift ;;
    --only) [ $# -ge 2 ] || { echo "reproduce.sh: --only needs a step list" >&2; exit 2; }
            ONLY=$2; shift 2 ;;
    --only=*) ONLY=${1#--only=}; shift ;;
    --skip) [ $# -ge 2 ] || { echo "reproduce.sh: --skip needs a step list" >&2; exit 2; }
            SKIP=$2; shift 2 ;;
    --skip=*) SKIP=${1#--skip=}; shift ;;
    --list) LIST=1; shift ;;
    --print-cluster) CLUSTER_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "reproduce.sh: unknown argument $1 (see --help)" >&2; exit 2 ;;
  esac
done

print_cluster() {
  cat <<'EOF'
== Cluster-scale campaigns (not run by this script)

These reproduce the campaign numbers in docs/results.md. They need a Slurm
cluster; the commands are the ones documented next to each result. Set
PE_WORK to a cluster work directory and OSS_CAD_SUITE to an OSS CAD Suite
2026-07-29 root. Every campaign runs against a frozen `git archive` of one
commit, not the working tree.

Random lockstep campaign (docs/verification-campaign.md; campaigns/random/README.md).
  8,704 seed runs, 536,064 cases, about 269 simulator CPU-hours at 73536f0:
    campaigns/random/build.sh rtl <snapshot> <build>/rtl
    campaigns/random/submit.sh rtl-default default rtl 4096 0 64 64 2000 900 00:45:00 8 4G 24
    campaigns/random/report_job.sh
  Any single seed of a default campaign, locally, in a checkout of 73536f0
  (at later commits add PE_RANDOM_GEN=1; a trailing deselect then also runs):
    cd test && PE_SEED=<seed> make COCOTB_TEST_MODULES=test_random

Mutation campaign (docs/verification-campaign.md, "Mutation testing" and
"Gap closure"; campaigns/mutation/README.md). 2,420 mutants, about 109 CPU-hours
for the first score: follow the command list in campaigns/mutation/README.md
("Reproduce"), starting from `source campaigns/mutation/campaign.env`.

Deeper and new formal properties (docs/formal-depth.md; formal_depth/README.md):
    formal_depth/run.sh generate
    formal_depth/run.sh submit "$PE_WORK/formal-depth"
    formal_depth/run.sh summarize "$PE_WORK/formal-depth" --tsv summary.tsv --curves curves.json
  Without Slurm, one class at a time:
    FD_PARALLEL=8 formal_depth/run.sh local "$PE_WORK/formal-depth" --only '^rr_'

Formal suite on every design variant (docs/variants.md section 7.2):
    formal/run.sh --variant <base|rstreg|cn|cn_s2|diet4|diet2>

Independent-peer seeded campaign (docs/independent-peers.md, "Results"):
  33,280 RTL test runs (seeds 1-512) and 16,640 gate-level runs (seeds 1-256):
    cd test_ext && PE_EXT_SEED=<seed> make

Static timing analyzer validation (docs/timing-analysis.md; tools/timing/README.md):
    sbatch -p mit_preemptable,mit_normal --requeue --array=0-127 --job-name=pe-x-timing-stress \
      --export=ALL,TOOL=$PWD/tools/timing,REPO=$PWD,OUT=$OUT,SUITE=stress,COUNT=25,CYCLES=30000 \
      tools/timing/slurm/validate.sbatch
    python3 tools/timing/pe_timing.py merge "$OUT"/*.json --out validation-summary.json

Host library fuzzing (docs/host.md, "Verification of the library"):
    python3 host/tools/fuzz_host.py --help

Physical sweep of the gds flow (docs/sweep.md; LibreLane 3.1.0.dev3 SIF and
IHP-Open-PDK 2bbec755 under $PE_WORK/sram-flow):
    PE_WORK=... python3 scripts/sweep/submit.py --help
    PE_WORK=... python3 scripts/sweep/collect.py

FPGA bitstreams (docs/fpga.md, "Toolchain" and "Build results"): openXC7 builds
through $PE_WORK/fpga/jobs/build_all.sbatch; SRAM stand-in equivalence with
fpga/formal/run.sh.

The official results of record are the GitHub Actions runs (gds, precheck,
gl_test, test, formal, regen); docs/results.md lists the run URLs.
EOF
}

if [ "$CLUSTER_ONLY" = 1 ]; then print_cluster; exit 0; fi

ALL_STEPS=("${QUICK_STEPS[@]}" "${FULL_STEPS[@]}")
if [ "$LIST" = 1 ]; then
  echo "quick: ${QUICK_STEPS[*]}"
  echo "full:  ${FULL_STEPS[*]} (in addition to quick)"
  exit 0
fi

in_list() { local x=$1; shift; local y; for y in "$@"; do [ "$x" = "$y" ] && return 0; done; return 1; }
split_csv() { local IFS=,; read -r -a _out <<<"$1"; printf '%s\n' ${_out[@]+"${_out[@]}"}; }

SELECTED=()
if [ -n "$ONLY" ]; then
  while read -r s; do
    [ -n "$s" ] || continue
    in_list "$s" "${ALL_STEPS[@]}" || { echo "reproduce.sh: unknown step $s (see --list)" >&2; exit 2; }
    SELECTED+=("$s")
  done < <(split_csv "$ONLY")
else
  SELECTED=("${QUICK_STEPS[@]}")
  [ "$FULL" = 1 ] && SELECTED+=("${FULL_STEPS[@]}")
fi
if [ -n "$SKIP" ]; then
  keep=()
  skips=()
  while read -r s; do [ -n "$s" ] && skips+=("$s"); done < <(split_csv "$SKIP")
  for s in "${SELECTED[@]}"; do in_list "$s" ${skips[@]+"${skips[@]}"} || keep+=("$s"); done
  SELECTED=(${keep[@]+"${keep[@]}"})
fi
[ ${#SELECTED[@]} -gt 0 ] || { echo "reproduce.sh: no steps selected" >&2; exit 2; }
selected() { in_list "$1" "${SELECTED[@]}"; }

WORK=${REPRO_WORK:-$REPO/build/reproduce}
case "$WORK" in /*) ;; *) WORK=$PWD/$WORK ;; esac
mkdir -p "$WORK/logs" "$WORK/results"
rm -f "$WORK"/results/*.tsv

# ---------------------------------------------------------------- helpers

missing_tools() { local t m=(); for t in "$@"; do command -v "$t" >/dev/null 2>&1 || m+=("$t"); done; echo "${m[*]:-}"; }

have_dune() {
  command -v dune >/dev/null 2>&1 && return 0
  [ -n "${OCAML_ENV:-}" ] && [ -f "$OCAML_ENV" ] && return 0
  [ -f "$REPO/scripts/ocaml-env.local.sh" ] && return 0
  return 1
}

# Record one step result: step, PASS|FAIL|SKIP, seconds, detail.
record() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" > "$WORK/results/$1.tsv"; }

# Run step function step_<name> with its output in logs/<name>.log. The step
# function prints its one-line detail as the last line starting with "DETAIL: "
# and returns 0 (PASS), 1 (FAIL) or 3 (SKIP).
run_step() {
  local name=$1 fn=step_${1//-/_} log=$WORK/logs/$1.log start rc status detail secs
  start=$(date +%s)
  echo "[reproduce] $name: started $(date '+%H:%M:%S') (log: $log)"
  "$fn" > "$log" 2>&1
  rc=$?
  secs=$(( $(date +%s) - start ))
  case $rc in 0) status=PASS ;; 3) status=SKIP ;; *) status=FAIL ;; esac
  detail=$(sed -n 's/^DETAIL: //p' "$log" | tail -n 1)
  [ -n "$detail" ] || detail="exit status $rc; see $log"
  record "$name" "$status" "$secs" "$detail"
  echo "[reproduce] $name: $status in ${secs}s: $detail"
  if [ "$status" = FAIL ]; then
    echo "[reproduce] $name: last lines of $log:"
    tail -n 25 "$log" | sed 's/^/    /'
  fi
}

detail() { echo "DETAIL: $*"; }

# ---------------------------------------------------------------- steps

step_regen() {
  have_dune || { detail "dune not found: install scripts/opam-deps.txt or set OCAML_ENV"; return 1; }
  local out=$WORK/regen/protocol_emulator_core.v
  rm -f "$out"
  DUNE_BUILD_DIR=$WORK/dune-regen OUTPUT=$out scripts/generate.sh configs/instruction-sram-32.json || {
    detail "scripts/generate.sh failed"; return 1; }
  local sha
  sha=$(sha256sum "$out" | cut -c1-16)
  if cmp -s "$out" src/protocol_emulator_core.v; then
    detail "regenerated core identical to src/protocol_emulator_core.v (sha256 $sha...)"
    return 0
  fi
  diff "$out" src/protocol_emulator_core.v | head -n 20
  detail "regenerated core differs from src/protocol_emulator_core.v"
  return 1
}

step_lint() {
  local m
  m=$(missing_tools iverilog yosys verilator)
  [ -z "$m" ] || { detail "missing tools: $m"; return 1; }
  rm -rf "$WORK/lint"
  LINT_WORK_DIR=$WORK/lint scripts/lint.sh || { detail "scripts/lint.sh failed"; return 1; }
  local v
  v=$(grep -o 'verilator: rc=[0-9]* warnings=[0-9]* errors=[0-9]*' "$WORK/logs/lint.log" | tail -n 1)
  detail "iverilog OK; yosys hierarchy OK with 8 SRAM macros; ${v:-verilator OK}"
}

# Parse a cocotb results.xml: prints "tests pass fail skip".
junit_counts() {
  python3 - "$1" <<'PY'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
tests = fail = skip = 0
for tc in root.iter("testcase"):
    tests += 1
    if tc.find("failure") is not None or tc.find("error") is not None:
        fail += 1
    elif tc.find("skipped") is not None:
        skip += 1
print(tests, tests - fail - skip, fail, skip)
PY
}

step_cocotb() {
  local m
  m=$(missing_tools iverilog vvp cocotb-config python3)
  [ -z "$m" ] || { detail "missing tools: $m"; return 1; }
  iverilog -V 2>/dev/null | head -n 1
  cocotb-config --version 2>/dev/null | sed 's/^/cocotb /'
  (cd test && make clean && make) ; local rc=$?
  [ -f test/results.xml ] || { detail "make exited $rc and wrote no test/results.xml"; return 1; }
  cp test/results.xml "$WORK/cocotb-results.xml"
  local tests pass fail skip
  read -r tests pass fail skip < <(junit_counts test/results.xml)
  detail "$tests tests: $pass pass, $fail fail, $skip skip (Icarus $(iverilog -V 2>/dev/null | head -n 1 | awk '{print $4}'))"
  [ "$rc" -eq 0 ] && [ "$tests" -gt 0 ] && [ "$fail" -eq 0 ] && [ "$skip" -eq 0 ]
}

formal_summary() {
  # met/total and the statuses from formal/build/summary.tsv
  python3 - "$REPO/formal/build/summary.tsv" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1]), delimiter="\t"))
met = sum(r["expected_met"] == "yes" for r in rows)
parts = ", ".join(f'{r["job"]} {r["status"]}' for r in rows)
print(f"{met}/{len(rows)} jobs met their expectation ({parts})")
PY
}

step_formal() {
  have_dune || { detail "dune not found: install scripts/opam-deps.txt or set OCAML_ENV"; return 1; }
  local m
  m=$(missing_tools yosys sby yices-smt2 python3)
  [ -z "$m" ] || { detail "missing tools: $m"; return 1; }
  yosys -V
  formal/run.sh "${FORMAL_QUICK_JOBS[@]}"; local rc=$?
  [ -f formal/build/summary.tsv ] || { detail "formal/run.sh exited $rc before writing a summary"; return 1; }
  detail "$(formal_summary)"
  return $rc
}

step_formal_rest() {
  local m
  m=$(missing_tools yosys sby yices-smt2 bitwuzla yosys-abc python3)
  [ -z "$m" ] || { detail "missing tools: $m"; return 1; }
  local gen=()
  # Reuse the formal RTL when the formal step generated it in this run.
  if [ -f "$WORK/results/formal.tsv" ] && [ "$(cut -f2 "$WORK/results/formal.tsv")" = PASS ]; then
    gen=(--no-generate)
  else
    have_dune || { detail "dune not found: install scripts/opam-deps.txt or set OCAML_ENV"; return 1; }
  fi
  formal/run.sh ${gen[@]+"${gen[@]}"} "${FORMAL_REST_JOBS[@]}"; local rc=$?
  [ -f formal/build/summary.tsv ] || { detail "formal/run.sh exited $rc before writing a summary"; return 1; }
  detail "$(formal_summary)"
  return $rc
}

step_timing() {
  command -v python3 >/dev/null 2>&1 || { detail "missing tools: python3"; return 1; }
  local out=$WORK/timing
  rm -rf "$out"
  # report exits 1 when any check FAILs (the committed report has none); the
  # verdict here is whether the regenerated report equals the committed one.
  python3 tools/timing/pe_timing.py report --firmware firmware --out "$out" \
    --validation tools/timing/report/validation-summary.json
  local rc=$?
  [ -f "$out/checks.json" ] || { detail "pe_timing report exited $rc without checks.json"; return 1; }
  local same=1
  cmp -s "$out/checks.json" tools/timing/report/checks.json || { echo "checks.json differs"; same=0; }
  cmp -s "$out/report.md" tools/timing/report/timing-report.md || { echo "timing-report.md differs"; same=0; }
  local counts
  counts=$(python3 - "$out/checks.json" <<'PY'
import collections, json, sys
c = collections.Counter()
fails = []
for image, checks in json.load(open(sys.argv[1])).get("images", {}).items():
    for chk in checks:
        c[chk.get("status")] += 1
        if chk.get("status") == "FAIL":
            fails.append(f'{image}:{chk.get("id")}')
d = json.load(open(sys.argv[1])).get("flagship")
def walk(o):
    if isinstance(o, dict):
        if isinstance(o.get("status"), str):
            c[o["status"]] += 1
            if o["status"] == "FAIL":
                fails.append(f'flagship:{o.get("id")}')
        for v in o.values():
            if isinstance(v, (dict, list)):
                walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)
walk(d)
print(f'{c["PASS"]} PASS, {c["FAIL"]} FAIL ({", ".join(fails) or "none"}), {c["WARN"]} WARN, {c["INFO"]} INFO')
PY
)
  python3 -m unittest discover -s tools/timing -p 'test_*.py'; local urc=$?
  if [ "$same" = 1 ] && [ "$urc" = 0 ]; then
    detail "report identical to tools/timing/report/ ($counts); unit tests OK"
    return 0
  fi
  detail "report identical: $([ $same = 1 ] && echo yes || echo no); unit tests rc=$urc; $counts"
  return 1
}

step_host() {
  command -v python3 >/dev/null 2>&1 || { detail "missing tools: python3"; return 1; }
  python3 -m unittest discover -s host/tests; local rc=$?
  local ran ok
  ran=$(grep -o '^Ran [0-9]* tests*' "$WORK/logs/host.log" | tail -n 1)
  ok=$(grep -E '^(OK|FAILED)' "$WORK/logs/host.log" | tail -n 1)
  detail "${ran:-no summary}: ${ok:-no result} (MicroPython replays skip when micropython is not on PATH)"
  return $rc
}

step_peers() {
  local m
  m=$(missing_tools iverilog vvp cocotb-config python3)
  [ -z "$m" ] || { detail "missing tools: $m"; return 1; }
  (cd test_ext && make clean && make); local rc=$?
  [ -f test_ext/results.xml ] || { detail "make exited $rc and wrote no test_ext/results.xml"; return 1; }
  cp test_ext/results.xml "$WORK/peers-results.xml"
  local tests pass fail skip
  read -r tests pass fail skip < <(junit_counts test_ext/results.xml)
  detail "$tests tests: $pass pass, $fail fail, $skip skip"
  [ "$rc" -eq 0 ] && [ "$tests" -gt 0 ] && [ "$fail" -eq 0 ]
}

step_variants() {
  have_dune || { detail "dune not found: install scripts/opam-deps.txt or set OCAML_ENV"; return 1; }
  DUNE_BUILD_DIR=$WORK/dune-variants scripts/gen_variants.sh || {
    detail "scripts/gen_variants.sh failed"; return 1; }
  local n
  n=$(ls build/variants/*/protocol_emulator_core.v 2>/dev/null | wc -l)
  detail "$n variant cores generated; design-of-record, base and firmware byte-identity checks passed"
}

step_hardcaml() {
  have_dune || { detail "dune not found: install scripts/opam-deps.txt or set OCAML_ENV"; return 1; }
  (
    if ! command -v dune >/dev/null 2>&1; then
      saved=$PATH
      for env_file in "${OCAML_ENV:-}" "$REPO/scripts/ocaml-env.local.sh"; do
        if [ -n "$env_file" ] && [ -f "$env_file" ]; then
          # shellcheck disable=SC1090
          . "$env_file"; break
        fi
      done
      PATH=$PATH:$saved   # keep yosys/sby for the SRAM formal wrapper test
    fi
    cd hardcaml && dune test --root . --build-dir "$WORK/dune-hardcaml" -j "${DUNE_JOBS:-2}" --force
  ); local rc=$?
  if [ $rc -eq 0 ]; then detail "dune test in hardcaml/ passed (7 test executables)"; else detail "dune test failed (exit $rc)"; fi
  return $rc
}

step_timing_validate() {
  command -v python3 >/dev/null 2>&1 || { detail "missing tools: python3"; return 1; }
  mkdir -p "$WORK/timing-validate"
  python3 tools/timing/pe_timing.py validate --repo . --out "$WORK/timing-validate/validate.json" \
    --suite scenarios --suite legacy --suite event --suite mutants --suite margins
  local rc=$?
  local line
  line=$(grep -E '^(PASS|FAIL)' "$WORK/logs/timing-validate.log" | tail -n 1)
  detail "${line:-exit status $rc}"
  return $rc
}

step_gl() {
  if [ -z "${PDK_ROOT:-}" ] || [ -z "${GL_NETLIST:-}" ]; then
    detail "not run: set PDK_ROOT and GL_NETLIST (the gds action's tt_submission/<top>.v)"
    return 3
  fi
  [ -f "$GL_NETLIST" ] || { detail "GL_NETLIST not found: $GL_NETLIST"; return 1; }
  local m
  m=$(missing_tools iverilog vvp cocotb-config python3)
  [ -z "$m" ] || { detail "missing tools: $m"; return 1; }
  local res=$WORK/gl-results.xml
  rm -f "$res"
  (cd test && rm -rf sim_build/gl && COCOTB_RESULTS_FILE=$res make GATES=yes GL_NETLIST="$GL_NETLIST" PDK_ROOT="$PDK_ROOT")
  local rc=$?
  [ -f "$res" ] || { detail "make exited $rc and wrote no results file"; return 1; }
  local tests pass fail skip
  read -r tests pass fail skip < <(junit_counts "$res")
  detail "$tests tests: $pass pass, $fail fail, $skip skip (gate level, zero delay)"
  [ "$rc" -eq 0 ] && [ "$tests" -gt 0 ] && [ "$fail" -eq 0 ]
}

# ---------------------------------------------------------------- run

echo "[reproduce] repository: $REPO"
if command -v git >/dev/null 2>&1 && git -C "$REPO" rev-parse HEAD >/dev/null 2>&1; then
  echo "[reproduce] commit: $(git -C "$REPO" rev-parse HEAD)$(git -C "$REPO" diff --quiet HEAD -- 2>/dev/null || echo ' (with local changes)')"
else
  echo "[reproduce] commit: unknown (not a git checkout)"
fi
echo "[reproduce] work directory: $WORK"
echo "[reproduce] steps: ${SELECTED[*]}"

# Groups of steps that share nothing. regen runs first; within a group the
# order is kept. formal-rest follows formal (same formal/build tree); gl
# follows cocotb (same test/ directory).
LANES=("regen" "formal formal-rest" "cocotb gl" "lint timing host peers" "variants hardcaml timing-validate")
T0=$(date +%s)
run_lane() { local s; for s in $1; do selected "$s" && run_step "$s"; done; return 0; }
run_lane "regen"
if [ "$PARALLEL" = 1 ]; then
  pids=()
  for lane in "${LANES[@]:1}"; do run_lane "$lane" & pids+=($!); done
  for p in "${pids[@]}"; do wait "$p"; done
else
  for lane in "${LANES[@]:1}"; do run_lane "$lane"; done
fi
TOTAL=$(( $(date +%s) - T0 ))

SUMMARY=$WORK/summary.tsv
printf 'step\tresult\tseconds\tdetail\n' > "$SUMMARY"
fails=0
echo
echo "== Summary ($(date '+%Y-%m-%d %H:%M:%S'), ${TOTAL}s wall)"
for s in "${ALL_STEPS[@]}"; do
  selected "$s" || continue
  f=$WORK/results/$s.tsv
  if [ -f "$f" ]; then
    cat "$f" >> "$SUMMARY"
    IFS=$'\t' read -r _ result secs det < "$f"
  else
    result=FAIL secs=0 det="step did not record a result"
    printf '%s\t%s\t%s\t%s\n' "$s" "$result" "$secs" "$det" >> "$SUMMARY"
  fi
  printf '%-16s %-4s %6ss  %s\n' "$s" "$result" "$secs" "$det"
  case "$result" in
    PASS) ;;
    SKIP) [ "$s" = gl ] || fails=$((fails + 1)) ;;
    *) fails=$((fails + 1)) ;;
  esac
done
echo "Summary written to $SUMMARY"
if [ "$FULL" = 1 ]; then echo; print_cluster; fi
if [ $fails -ne 0 ]; then
  echo "reproduce.sh: $fails step(s) did not pass" >&2
  exit 1
fi
echo "reproduce.sh: all selected steps passed"
