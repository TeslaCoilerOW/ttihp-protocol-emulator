#!/usr/bin/env bash
# Make a design directory that re-runs the mutants of an existing one under another test tree.
#
# usage: mk_design.sh SRC_DESIGN SNAPSHOT_DIR DST_DESIGN [KILL_MODULE...]
#
# SRC_DESIGN    a design directory written by gen_mutants.sh (and the first equiv task):
#               its prepared core and mutant list are linked, not copied, so every stage
#               of every DST_DESIGN rebuilds exactly the same mutants
# SNAPSHOT_DIR  frozen tree (git archive of the commit whose tests are to be run); for a
#               design variant it must hold build/variants/<name>/ (scripts/gen_variants.sh)
# DST_DESIGN    new design directory: links + a new testtree.tgz packed from SNAPSHOT_DIR
#               with the same members as gen_mutants.sh
# KILL_MODULE   written to DST_DESIGN/kill_modules.txt, the module list of the run_mutant.py
#               stages kill and kill-rip (docs/mutation-push.md)
#
# Environment: EXTRA_TESTS="a.py b.py ..." copies these files into test/ of the packed tree
# (the push packed uncommitted test_kill_*.py versions onto the c118027 tree this way).
#
# DST_DESIGN/testtree.sha256 lists the sha256 of every packed file.
set -euo pipefail
SRC=$(readlink -f "$1")
SNAP=$(readlink -f "$2")
DST=$3
shift 3
mkdir -p "$DST"
DST=$(readlink -f "$DST")
[ -f "$SRC/base.il" ] && [ -f "$SRC/mutations.tsv" ] || { echo "$SRC: not a design directory" >&2; exit 1; }
for f in base.il base_roundtrip.v core_orig.v equiv_keep_gold.txt equiv_keep_gate.txt mutations.tsv \
         sram_blackbox.v variant.txt regions.json cells.tsv gen_params.txt stat.txt engine_map.json \
         line_region.json all_mutations.tsv sample_index.tsv; do
  if [ -e "$SRC/$f" ]; then ln -sfn "$(readlink -f "$SRC/$f")" "$DST/$f"; fi
done
VARIANT=$(cat "$SRC/variant.txt" 2>/dev/null || echo base)
EXTRA=()
if [ "$VARIANT" != base ] && [ ! -d "$SNAP/build/variants/$VARIANT" ] && cmp -s "$SNAP/src/protocol_emulator_core.v" "$SRC/core_orig.v"; then
  :  # a branch whose src/ holds the variant core (eval/diet8-rec16, gen_line_mutants.sh): no build/variants/
elif [ "$VARIANT" != base ]; then
  [ -d "$SNAP/build/variants/$VARIANT" ] || { echo "no $SNAP/build/variants/$VARIANT (scripts/gen_variants.sh $VARIANT)" >&2; exit 1; }
  cmp -s "$SNAP/build/variants/$VARIANT/protocol_emulator_core.v" "$SRC/core_orig.v" \
    || { echo "$SNAP/build/variants/$VARIANT/protocol_emulator_core.v differs from the mutated core" >&2; exit 1; }
  EXTRA=("build/variants/$VARIANT")
fi
T=$(mktemp -d "${TMPDIR:-/tmp}/pe-mkdesign-XXXXXX")
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/src"
cp "$SNAP/src/project.v" "$T/src/"
cp -r "$SNAP/test" "$SNAP/firmware" "$SNAP/models" "$SNAP/configs" "$T/"
if [ ${#EXTRA[@]} -gt 0 ]; then
  mkdir -p "$T/build/variants"
  cp -r "$SNAP/build/variants/$VARIANT" "$T/build/variants/"
  rm -f "$T/build/variants/$VARIANT/protocol_emulator_core.v"   # as gen_mutants.sh: the mutant replaces it
fi
for f in ${EXTRA_TESTS:-}; do cp "$f" "$T/test/"; done
rm -rf "$T/test/sim_build" "$T/test/__pycache__" "$T/test/results.xml"
(cd "$T" && tar -czf "$DST/testtree.tgz" src/project.v test firmware models configs ${EXTRA[@]+"${EXTRA[@]}"} \
  && find src test firmware models configs ${EXTRA[@]+"${EXTRA[@]}"} -type f | sort | xargs sha256sum > "$DST/testtree.sha256")
if [ $# -gt 0 ]; then echo "$*" > "$DST/kill_modules.txt"; fi
echo "$DST: variant $VARIANT, $(wc -l < "$DST/testtree.sha256") files in testtree.tgz${1:+, kill modules: $*}"
