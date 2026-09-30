#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Freeze the inputs of the optimizer and submit its driver job (docs/notes/optimization.md).
#
#   PE_WORK=<cluster work dir> OSS_CAD_SUITE=<OSS CAD Suite root> tools/opt/launch.sh [--commit REV] [--no-submit]
#
# 0. checks that REV has formal_eq/eq_check.py (the equivalence checker of the
#    promotion stage, unless $PE_EQ_CHECK overrides it) and that yosys and yosys-abc
#    are in $OSS_CAD_SUITE/bin (else on PATH); the driver passes OSS_CAD_SUITE to its
#    jobs;
# 1. exports the committed tree REV (default HEAD) with `git archive` to
#    $PE_WORK/optimizer/tree/<commit12>-<tools sha8>/: src/, macros/, floorplans/,
#    scripts/sweep/, test/ and formal_eq/ come from the commit, never from the
#    working tree (other work may be in progress there);
# 2. overlays tools/opt/ from the working tree (the optimizer itself, which may be
#    newer than REV) and records both in OPT_TREE.json;
# 3. points $PE_WORK/optimizer/current at that tree;
# 4. creates the private venv with optuna if it is missing;
# 5. submits the driver job unless one is already queued or running.
# Re-running it is safe; running jobs keep the tree they started with.
set -euo pipefail
: "${PE_WORK:?set PE_WORK to the cluster work directory}"
OPT=${PE_OPT_ROOT:-$PE_WORK/optimizer}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
REV=HEAD
SUBMIT=1
while [ $# -gt 0 ]; do
  case $1 in
    --commit) REV=$2; shift 2 ;;
    --no-submit) SUBMIT=0; shift ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
COMMIT=$(git -C "$REPO" rev-parse "$REV")
if [ -n "${PE_EQ_CHECK:-}" ]; then
  echo "WARNING: PE_EQ_CHECK=$PE_EQ_CHECK overrides formal_eq/eq_check.py of the frozen tree" >&2
elif ! git -C "$REPO" cat-file -e "$COMMIT:formal_eq/eq_check.py" 2>/dev/null; then
  echo "commit $COMMIT has no formal_eq/eq_check.py: every equivalence stage would fail; commit formal_eq/ first" >&2
  exit 2
fi
if [ -n "$(git -C "$REPO" status --porcelain -- formal_eq)" ]; then
  echo "note: formal_eq/ has uncommitted changes; the frozen tree uses the version of $COMMIT" >&2
fi
if [ -n "${OSS_CAD_SUITE:-}" ]; then
  for b in yosys yosys-abc; do
    [ -x "$OSS_CAD_SUITE/bin/$b" ] || { echo "OSS_CAD_SUITE=$OSS_CAD_SUITE has no bin/$b" >&2; exit 2; }
  done
elif command -v yosys >/dev/null 2>&1 && command -v yosys-abc >/dev/null 2>&1; then
  echo "note: OSS_CAD_SUITE is unset; the equivalence jobs take yosys from PATH ($(command -v yosys))" >&2
else
  echo "set OSS_CAD_SUITE to the OSS CAD Suite root (yosys and yosys-abc of the equivalence stage)" >&2
  exit 2
fi
TOOLS_SHA=$(cd "$HERE" && cat $(ls *.py *.sh README.md | sort) | sha256sum | cut -c1-8)
NAME=${COMMIT:0:12}-$TOOLS_SHA
T=$OPT/tree/$NAME
mkdir -p "$OPT/tree" "$OPT/logs"
if [ ! -d "$T" ]; then
  tmp=$(mktemp -d "$OPT/tree/.new.XXXXXX")
  git -C "$REPO" archive "$COMMIT" | tar -x -C "$tmp"
  rm -rf "$tmp/tools/opt"; mkdir -p "$tmp/tools/opt"
  cp -p "$HERE"/*.py "$HERE"/*.sh "$HERE"/README.md "$tmp/tools/opt/"
  chmod +x "$tmp"/tools/opt/*.sh "$tmp"/scripts/sweep/*.sh
  dirty=$(git -C "$REPO" status --porcelain -- tools/opt | wc -l)
  eqsha=$(sha256sum "$tmp/formal_eq/eq_check.py" 2>/dev/null | cut -c1-64 || true)
  printf '{"commit": "%s", "tools_sha8": "%s", "tools_uncommitted_files": %s, "eq_check_sha256": "%s", "exported": "%s"}\n' \
    "$COMMIT" "$TOOLS_SHA" "$dirty" "$eqsha" "$(date -Is)" > "$tmp/OPT_TREE.json"
  mv "$tmp" "$T"   # never modified afterwards (running jobs use it)
fi
ln -sfn "tree/$NAME" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"
echo "tree: $T"
if [ ! -x "$OPT/venv/bin/python" ]; then
  python3 -m venv "$OPT/venv"
  "$OPT/venv/bin/pip" install -q --disable-pip-version-check optuna
fi
"$OPT/venv/bin/python" -c 'import optuna; print("optuna", optuna.__version__)'
[ "$SUBMIT" = 1 ] || exit 0
if squeue -h -u "$USER" -n pe-v2-optimizer-driver -o %i | grep -q .; then
  echo "a driver job is already queued or running:"; squeue -u "$USER" -n pe-v2-optimizer-driver
  exit 0
fi
sbatch --parsable -J pe-v2-optimizer-driver -p mit_preemptable --requeue -N 1 -n 1 -c 2 --mem 8G \
  -t 2-00:00:00 --open-mode=append -o "$OPT/logs/%x-%j.out" \
  --export=ALL,PE_WORK="$PE_WORK",PE_OPT_ROOT="$OPT" "$T/tools/opt/driver_job.sh"
