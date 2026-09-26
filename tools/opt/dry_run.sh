#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Dry run of the optimizer driver against a copy of the campaign's results store
# (docs/optimization.md, "Operation"). Nothing is submitted and nothing outside
# DRY_DIR is written.
#
#   PE_WORK=<cluster work dir> tools/opt/dry_run.sh DRY_DIR [LOOPS]
#
# 1. exports HEAD with `git archive` and overlays the working tree's tools/opt, as
#    launch.sh does, into DRY_DIR/tree/;
# 2. copies $PE_WORK/optimizer/store/events.jsonl and the study journals (read only) to
#    DRY_DIR/store/ and DRY_DIR/studies/ unless DRY_DIR already has a store (a second call
#    continues the same dry campaign);
# 3. runs `driver.py once --dry-run` LOOPS times (default 1) with PE_OPT_ROOT=DRY_DIR and
#    the campaign's venv. A dry driver records the job id "dry" for every submission,
#    plans for the whole CPU cap, and neither polls nor prunes run directories outside
#    DRY_DIR. The result is DRY_DIR/driver.log, DRY_DIR/leaderboard.md and the snapshots
#    under DRY_DIR/runs/.
# Run it on a compute node (it builds up to one snapshot per planned trial).
set -euo pipefail
: "${PE_WORK:?set PE_WORK to the cluster work directory}"
DRY=${1:?usage: dry_run.sh DRY_DIR [LOOPS]}
LOOPS=${2:-1}
REAL=$PE_WORK/optimizer
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
COMMIT=$(git -C "$REPO" rev-parse HEAD)
TOOLS_SHA=$(cd "$HERE" && cat $(ls *.py *.sh README.md | sort) | sha256sum | cut -c1-8)
mkdir -p "$DRY/store" "$DRY/tree"
T=$DRY/tree/${COMMIT:0:12}-$TOOLS_SHA
if [ ! -d "$T" ]; then
  mkdir -p "$T"
  git -C "$REPO" archive "$COMMIT" | tar -x -C "$T"
  rm -rf "$T/tools/opt"; mkdir -p "$T/tools/opt"
  cp -p "$HERE"/*.py "$HERE"/*.sh "$HERE"/README.md "$T/tools/opt/"
  printf '{"commit": "%s", "tools_sha8": "%s", "tools_uncommitted_files": %s, "exported": "%s", "dry_run": true}\n' \
    "$COMMIT" "$TOOLS_SHA" "$(git -C "$REPO" status --porcelain -- tools/opt | wc -l)" "$(date -Is)" > "$T/OPT_TREE.json"
fi
if [ ! -s "$DRY/store/events.jsonl" ]; then
  mkdir -p "$DRY/studies"
  cp "$REAL"/studies/*.journal "$DRY/studies/" 2>/dev/null || true
  cp "$REAL/store/events.jsonl" "$DRY/store/events.jsonl"
fi
echo "dry tree $T; store $(wc -l < "$DRY/store/events.jsonl") events"
for i in $(seq 1 "$LOOPS"); do
  PE_OPT_ROOT=$DRY "$REAL/venv/bin/python" -u "$T/tools/opt/driver.py" once --dry-run 2>&1 | tee -a "$DRY/driver.log"
done
PE_OPT_ROOT=$DRY "$REAL/venv/bin/python" "$T/tools/opt/driver.py" status
