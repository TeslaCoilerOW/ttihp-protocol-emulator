#!/bin/bash
# Area-study synthesis.  usage: synth3.sh <out_dir> <top> <mode> <design.v>...
#  plain: read; synth -flatten; dfflibmap; abc -liberty; setundef -zero; hilomap   (v1 flow)
#  ll66 : port of LibreLane 3.1.0.dev3 pyosys/synthesize.py (SYNTH_STRATEGY "AREA 0",
#         flatten, defaults) run with the Yosys 0.66 inside the LibreLane SIF, the exact
#         AREA_0.abc + ABC SDC LibreLane generated, and the action's liberty revision.
#  ll   : same script with the local Yosys 0.67 ("abc" instead of "abc -fast").
# env EXTRA_MAP=<techmap.v>: extra techmap before dfflibmap (scan-enable experiment).
set -euo pipefail
OSS=${OSS:?set OSS to the OSS CAD Suite bin directory}
PDKR=${PDK_ROOT:?set PDK_ROOT to an IHP-Open-PDK checkout at 2bbec755}
LIB=$PDKR/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib
FLOW=${FLOW:?set FLOW to the local LibreLane run directory (SIF, PDK 2bbec755)}
W=${W:?set W to the area-study work directory}
out=$1; top=$2; mode=$3; shift 3; mkdir -p "$out"
[ "$mode" = ll66 ] && LIB=$FLOW/pdk/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib
TRI=$PDKR/ihp-sg13cmos5l/libs.tech/librelane/sg13cmos5l_stdcell/tribuff_map.v
rep() { local n=$1; shift; for ((i = 0; i < n; i++)); do printf '%s\n' "$@"; done; }
E="-mux_undef -mux_bool -undriven -fine"
{
  echo "read_liberty -lib $LIB"; echo "read_verilog -lib $W/stub/sram_bb.v"
  for f in "$@"; do echo "read_verilog $f"; done
  if [ "$mode" = plain ]; then
    printf '%s\n' "hierarchy -check -top $top" "synth -flatten -top $top" "tee -o $out/pre_map_stat.txt stat" \
      "techmap -map $W/stub/latch_map.v" ${EXTRA_MAP:+"techmap -map $EXTRA_MAP"} "dfflibmap -liberty $LIB" \
      "abc -liberty $LIB" "opt_clean -purge" "setundef -zero" \
      "hilomap -hicell sg13cmos5l_tiehi L_HI -locell sg13cmos5l_tielo L_LO" "opt_clean"
  else   # librelane_synth(): opt loops unrolled 3x (extra iterations are no-ops)
    H="hierarchy -check -top $top -nokeep_prints -nokeep_asserts"
    printf '%s\n' "$H" tribuf "$H" "chformal -remove" proc_clean proc_rmdead proc_prune proc_init proc_arst \
      proc_rom proc_mux proc_dlatch proc_dff proc_memwr proc_clean opt_expr flatten opt_expr opt_clean \
      opt_expr "opt_merge -nomux"
    rep 3 opt_muxtree opt_reduce opt_merge "opt_dff -nodffe -nosdff" opt_clean opt_expr
    echo fsm; printf '%s\n' opt_expr "opt_merge -nomux"; rep 3 opt_muxtree opt_reduce opt_merge opt_dff opt_clean opt_expr
    printf '%s\n' wreduce peepopt opt_clean booth alumacc arith_tree share opt_expr "opt_merge -nomux"
    rep 3 opt_muxtree opt_reduce opt_merge opt_dff opt_clean opt_expr
    printf '%s\n' "memory -nomap" opt_clean; rep 2 "opt_expr $E" opt_merge opt_dff opt_clean; echo opt_clean
    printf '%s\n' memory_map "opt_expr $E" "opt_merge -nomux"
    rep 3 opt_muxtree "opt_reduce -fine" opt_merge opt_share opt_dff opt_clean "opt_expr $E"
    echo techmap; rep 2 opt_expr opt_merge opt_dff opt_clean; echo opt_clean; rep 2 opt_expr opt_merge opt_dff opt_clean; echo opt_clean
    [ "$mode" = ll66 ] && echo "abc -fast" || echo "abc"
    printf '%s\n' "opt -fast" "$H" 'delete t:$print' 'delete t:$assert' opt "opt_clean -purge" \
      "tee -o $out/pre_map_stat.txt stat" "techmap -map $TRI" simplemap "techmap -map $W/stub/latch_map.v" simplemap \
      ${EXTRA_MAP:+"techmap -map $EXTRA_MAP"} "dfflibmap -liberty $LIB" \
      "abc -script $W/stub/AREA_0.abc -constr $W/stub/synthesis.abc.sdc -liberty $LIB" "setundef -zero" \
      "hilomap -hicell sg13cmos5l_tiehi L_HI -locell sg13cmos5l_tielo L_LO" splitnets "opt_clean -purge" \
      "insbuf -buf sg13cmos5l_buf_1 A X"
  fi
  echo "tee -o $out/stat.txt stat -liberty $LIB"
} > "$out/synth.ys"
[ -n "${DRYRUN:-}" ] && exit 0
if [ "$mode" = ll66 ]; then
  command -v apptainer >/dev/null 2>&1 || { source /etc/profile.d/modules.sh; module load apptainer/1.4.2; }
  binds="--bind $W:$W:rw --bind $PDKR:$PDKR:ro --bind $FLOW/pdk:$FLOW/pdk:ro"
  for f in "$@"; do case $f in $W/*) ;; *) binds+=" --bind $(dirname "$f"):$(dirname "$f"):ro" ;; esac; done
  YOSYS="apptainer exec --cleanenv --containall --no-home $binds --pwd $out $FLOW/librelane-3.1.0.dev3.sif yosys"
else
  YOSYS=$OSS/yosys
fi
/usr/bin/time -v $YOSYS -q -l "$out/yosys.log" "$out/synth.ys" > "$out/time.txt" 2>&1 || { tail -20 "$out/yosys.log"; exit 1; }
grep -E "Elapsed|Maximum resident" "$out/time.txt"; grep "Chip area" "$out/stat.txt" | tail -1
gzip -f "$out/yosys.log"
