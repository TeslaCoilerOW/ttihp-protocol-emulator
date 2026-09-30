#!/usr/bin/env bash
# Mutant list of the line-unit campaign (docs/extension.md section 11): every Yosys
# `mutate` mutation of the line-unit logic, and a uniform random sample of it.
#
# usage: gen_line_mutants.sh SNAPSHOT_DIR REFERENCE_CORE OUT_DIR SEED SAMPLE
#
# SNAPSHOT_DIR    frozen tree (git archive of the recorded commit) whose src/ holds the
#                 variant core (branch eval/diet8-rec16), or MUTATE_CORE=<file>
# REFERENCE_CORE  the same variant without the line unit (build/variants/diet8/
#                 protocol_emulator_core.v from scripts/gen_variants.sh diet8); used only for
#                 line_region.py's added-or-changed statistic
# OUT_DIR         design directory for run_mutant.py / submit.sh: testtree.tgz, core_orig.v,
#                 base.il, base_roundtrip.v, line_region.json, cells.tsv, regions.json,
#                 sel/line.txt, all_mutations.tsv (the whole enumeration), mutations.tsv
#                 (control 0 and the sample, numbered 1..SAMPLE), variant.txt, gen_params.txt
# SEED            seed of the sample (Python random.seed) and of `mutate` (control-bit choice
#                 of the cnot modes)
# SAMPLE          sample size; 0 or a value at least the enumeration's size keeps every mutant
#
# POPULATION (default line) picks the selection: "line" is sel/line.txt (the line-unit state
# logic and its first output statements, region column line/<sub-region>); "ext" is
# sel/ext.txt, the second population of line_region.py (option logic outside sel/line.txt:
# statements tied to a line-unit register and added-or-changed statements with no line-unit
# register in their same-cycle fan-in; region column ext/<kind>).
# EXCLUDE (optional) is the sample_index.tsv of an earlier sample of the same population; its
# population lines are removed before the draw (a held-out sample). SAMPLE_SEED (default SEED)
# seeds only the draw: a held-out sample keeps SEED, so `mutate` lists the same population.
#
# PE_VARIANT (default diet8_rec16) is written to variant.txt, so run_mutant.py runs every
# make with PE_VARIANT=<name> and PE_CORE=<mutant>. The test tree holds what the
# test action uses on the branch (src/project.v, test, firmware, models, configs; no
# build/variants/, so the harness loads firmware/ as it does there).
#
# The selection is line_region.py's: the statements that compute line-unit state
# (region_map.py's nearest-named-state rule with the 19 line-unit registers as a
# category) and the first statements on every path out of them. `mutate -list N`
# with N at least the size of its mutation database returns the whole database (every
# port bit of every selected cell, modes inv, const0, const1, cnot0, cnot1), so
# all_mutations.tsv is the full mutant population of the selection. The sample is drawn
# from it uniformly without replacement.
set -euo pipefail

SNAP=$(readlink -f "$1")
REF=$(readlink -f "$2")
OUT=$3
SEED=$4
SAMPLE=$5
HERE=$(dirname "$(readlink -f "$0")")
VARIANT=${PE_VARIANT:-diet8_rec16}
POPULATION=${POPULATION:-line}
case $POPULATION in line|ext) ;; *) echo "POPULATION must be line or ext" >&2; exit 1 ;; esac
EXCLUDE=${EXCLUDE:+$(readlink -f "$EXCLUDE")}
SAMPLE_SEED=${SAMPLE_SEED:-$SEED}
CORE=${MUTATE_CORE:-$SNAP/src/protocol_emulator_core.v}
mkdir -p "$OUT"
cd "$OUT"
head -1 "$CORE" | grep -q "configs/variants/$VARIANT.json" \
  || { echo "$CORE: header does not name configs/variants/$VARIANT.json" >&2; exit 1; }

echo "$VARIANT" > variant.txt
cp "$CORE" core_orig.v
tar -C "$SNAP" --exclude='test/sim_build' --exclude='test/__pycache__' --exclude='test/results.xml' \
  -czf testtree.tgz src/project.v test firmware models configs
cp "$SNAP/models/blackbox/RM_IHPSG13_1P_64x16_c2.v" sram_blackbox.v

yosys -q -l prep.log -p "
read_verilog -lib sram_blackbox.v
read_verilog core_orig.v
hierarchy -top protocol_emulator_core
prep -top protocol_emulator_core
write_rtlil base.il
write_verilog -noattr base_roundtrip.v
tee -q -o stat.txt stat
"

python3 "$HERE/line_region.py" core_orig.v base.il . "$REF"

yosys -q -l list_$POPULATION.log -p "
read_rtlil base.il
select -read sel/$POPULATION.txt
mutate -list 100000000 -seed $SEED -o list_$POPULATION.txt
"
# region column: line/<sub-region> or ext/<kind> of the mutated cell (cells.tsv, line_region.py)
python3 - "$POPULATION" <<'PY'
import re, sys
pop = sys.argv[1]
col = {"line": 4, "ext": 5}[pop]
sub = {}
for row in open("cells.tsv").read().splitlines()[1:]:
    f = row.split("\t")
    sub[f[0]] = f[col]
with open("all_mutations.tsv", "w") as out:
    for k, cmd in enumerate(open(f"list_{pop}.txt").read().splitlines(), 1):
        cell = re.search(r"-cell (\S+)", cmd).group(1)
        assert sub[cell] != "-", cell
        out.write(f"{k}\t{pop}/{sub[cell]}\t{cmd}\n")
PY

python3 - "$SAMPLE_SEED" "$SAMPLE" "${EXCLUDE:-}" <<'PY'
import random, sys
seed, n, exclude = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
rows = open("all_mutations.tsv").read().splitlines()
if exclude:
    drop = {line.split("\t")[1] for line in open(exclude).read().splitlines()}
    rows = [r for r in rows if r.split("\t")[0] not in drop]
    print(f"excluded {len(drop)} population lines of {exclude}; {len(rows)} left")
if 0 < n < len(rows):
    random.seed(seed)
    rows = sorted(random.sample(rows, n), key=lambda r: int(r.split("\t")[0]))
with open("mutations.tsv", "w") as out:
    out.write("0\tnone\tmutate -mode none\n")
    for k, row in enumerate(rows, 1):
        _, region, cmd = row.split("\t", 2)
        out.write(f"{k}\t{region}\t{cmd}\n")
# population index of every sampled mutant (all_mutations.tsv numbering)
with open("sample_index.tsv", "w") as out:
    for k, row in enumerate(rows, 1):
        out.write(f"{k}\t{row.split(chr(9))[0]}\n")
PY

{
  echo "snapshot: $SNAP"
  echo "variant: $VARIANT"
  echo "selection: $POPULATION (sel/$POPULATION.txt)"
  [ -z "${EXCLUDE:-}" ] || echo "excluded: $(sha256sum "$EXCLUDE" | cut -c1-64) ($(wc -l < "$EXCLUDE") population lines)"
  echo "reference core: $(sha256sum "$REF" | cut -c1-64)"
  echo "seed: $SEED"
  [ "$SAMPLE_SEED" = "$SEED" ] || echo "sample seed: $SAMPLE_SEED"
  echo "population: $(wc -l < all_mutations.tsv)"
  echo "sample: $(($(wc -l < mutations.tsv) - 1))"
  yosys -V
  sha256sum core_orig.v base.il base_roundtrip.v sel/$POPULATION.txt all_mutations.tsv mutations.tsv
} > gen_params.txt
cat gen_params.txt
