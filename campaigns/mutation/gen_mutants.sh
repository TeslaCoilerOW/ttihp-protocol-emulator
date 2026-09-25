#!/usr/bin/env bash
# Generate the mutant list for the RTL mutation campaign (Yosys `mutate`, the
# mutation engine behind mcy).
#
# usage: gen_mutants.sh SNAPSHOT_DIR OUT_DIR QUOTAS SEED
#
# SNAPSHOT_DIR  frozen tree (git archive of the recorded commit)
# OUT_DIR       receives testtree.tgz, base.il, base_roundtrip.v, regions.json, cells.tsv,
#               sel/, mutations.tsv, prep.log, gen_params.txt
# QUOTAS        region=count,... (see campaign.env); a region missing from the
#               list gets no mutants
# SEED          base RNG seed; region k (in QUOTAS order) uses SEED+k
#
# Only src/protocol_emulator_core.v is mutated. The eight IHP SRAM macros are
# read as an interface-only blackbox (models/blackbox/), so the macros are never
# mutated; the nets the core drives into them and reads from them can be.
#
# The design is prepared once (`prep`) and saved as base.il. Every mutant is
# that design plus exactly one `mutate -mode ...` command (one port bit of one
# cell inverted, tied to 0/1, or XORed with another bit of the same port), so a
# mutant is rebuilt with `read_rtlil base.il; <command>; write_verilog`.
#
# Bias: region_map.py assigns every cell to a functional region (engine
# control, host/loader, mover, events, pins, XFER timing, instruction-SRAM
# port, FIFO, engine datapath, timestamp, shared). Each region gets its own
# seeded `mutate -list` with an explicit quota, so the engine, host and mover
# logic get more mutants than their cell share and the wide datapath fewer.
# Within a region the -cfg options switch off the two per-*bit* queues
# (weight_pq_b/weight_pq_mb), which favour wide buses, and weight up the
# per-source-statement queue and the coverage queue, so a region's mutants
# spread over its Hardcaml statements rather than over the bits of a few
# 32-bit muxes.
set -euo pipefail

SNAP=$(readlink -f "$1")
OUT=$2
QUOTAS=$3
SEED=$4
HERE=$(dirname "$(readlink -f "$0")")
mkdir -p "$OUT"
cd "$OUT"

CFG="-cfg weight_pq_b 0 -cfg weight_pq_mb 0 -cfg weight_pq_w 100 -cfg weight_pq_mw 100 \
-cfg weight_pq_c 100 -cfg weight_pq_mc 100 -cfg weight_pq_s 300 -cfg weight_pq_ms 300 \
-cfg weight_cover 2000 -cfg pick_cover_prcnt 90"

cp "$SNAP/src/protocol_emulator_core.v" core_orig.v
# everything a simulation task needs besides the core (the harness reads
# ../firmware and ../configs relative to test/)
tar -C "$SNAP" -czf testtree.tgz src/project.v test firmware models configs
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

python3 "$HERE/region_map.py" core_orig.v base.il .

: > mutations.tsv
printf '0\tnone\tmutate -mode none\n' >> mutations.tsv
: > mutations.body
k=0
for q in ${QUOTAS//,/ }; do
  region=${q%%=*}
  n=${q#*=}
  k=$((k + 1))
  [ "$n" -gt 0 ] || continue
  [ -s "sel/$region.txt" ] || { echo "no cells in region $region" >&2; exit 1; }
  yosys -q -l "list_$region.log" -p "
read_rtlil base.il
select -read sel/$region.txt
mutate -list $n -seed $((SEED + k)) $CFG -o list_$region.txt
"
  awk -v r="$region" '{print r "\t" $0}' "list_$region.txt" >> mutations.body
done
# number the mutants 1..N in generation order
awk -F'\t' 'BEGIN{OFS="\t"} {print NR, $1, $2}' mutations.body >> mutations.tsv
rm -f mutations.body

{
  echo "snapshot: $SNAP"
  echo "quotas: $QUOTAS"
  echo "seed: $SEED (region k uses seed+k)"
  echo "cfg: $CFG"
  yosys -V
  sha256sum core_orig.v base.il base_roundtrip.v mutations.tsv
} > gen_params.txt
echo "mutants (excluding control 0): $(($(wc -l < mutations.tsv) - 1))"
