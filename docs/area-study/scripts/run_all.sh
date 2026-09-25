#!/bin/bash
# Area study v2: synthesize every variant in vg2/ with the plain flow and with the
# LibreLane 3.1.0.dev3 "AREA 0" replica (Yosys 0.66 from the LibreLane SIF).
# (The v2 results were produced by an earlier synth2.sh plus run_extra.sh; synth3.sh emits
# identical Yosys scripts and reproduced base/snapshot areas exactly.)
# Two jobs at a time (Slurm allocation: 2 CPUs).  Skips jobs whose stat.txt exists.
# usage (from a compute node): run_all.sh [plain|ll66|all]
set -u
W=${W:?set W to the area-study work directory}
S=$W/scripts/synth3.sh
V=$W/vg2
T=tt_um_protocol_processor
want=${1:-all}
source /etc/profile.d/modules.sh 2>/dev/null; module load apptainer/1.4.2 >/dev/null 2>&1
FULL="base fifo16 fifo4 fifo2 comp0 comp16 narrowpc narrowimg dpnoclear noena fiforegs noshift byteshift
nostatus nomover dw16 dw16f4 syncdiet4 async async_fr async_fr_s2 async_fr_noena async_fr4 async_fr_rx4
dietE dietE_s2 dietE_dpnc dietE_byte dietE_noshift dietE8 dietE8_rx4 dietG2 dietF16 dietF16f8 regp64f8 regp32f4E
cn cn4 dietEi dietEi_byte dietEi8 dietGi dietGi_byte dietFi dietFi8"
jobs_list=()
for m in ll66 plain; do
  [ "$want" = all ] || [ "$want" = $m ] || continue
  for v in $FULL; do jobs_list+=("$m/$v|$m|$T|$V/$v.v|"); done
  for b in blk_engine32 blk_engine16 blk_engine32_diet; do jobs_list+=("$m/$b|$m|engine_block|$V/$b.v|"); done
  for b in blk_fifo32x8 blk_fifo32x4 blk_fifo16x8 blk_fifo32x8_afr blk_fifo32x4_afr; do jobs_list+=("$m/$b|$m|fifo_block|$V/$b.v|"); done
  jobs_list+=("$m/blk_host|$m|host_block|$V/blk_host.v|")
  for t in fl_w32d8_cap fl_w32d8_nocap fl_w32d8_nocap_rst fl_w32d4_nocap fl_w32d4_cap \
           fl_w32d8_cap_a fl_w32d8_nocap_a fl_w32d4_cap_a fl_w32d4_nocap_a; do
    jobs_list+=("$m/$t|$m|$t|$V/blk_fifo_latch.v|"); done
  jobs_list+=("$m/scanen_base|$m|$T|$V/base.v|$W/stub/scan_enable_map.v")
  jobs_list+=("$m/scanen_dietE|$m|$T|$V/dietE.v|$W/stub/scan_enable_map.v")
done
run() {
  IFS='|' read -r name mode top file xmap <<< "$1"
  local out=$W/runs2/$name
  [ -f "$out/stat.txt" ] && { echo "skip $name"; return; }
  mkdir -p "$out"
  echo "== $name"
  EXTRA_MAP="$xmap" nice -n 19 $S "$out" "$top" "$mode" "$file" > "$out/run.txt" 2>&1 || echo "FAIL $name"
  tail -1 "$out/run.txt"
}
i=0
for j in "${jobs_list[@]}"; do
  run "$j" &
  i=$((i+1))
  if (( i % 2 == 0 )); then wait; fi
done
wait
echo DONE
