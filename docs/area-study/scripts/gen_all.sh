#!/bin/bash
# Emit every area-study variant (v2) from the patched Hardcaml copy hc/ into vg2/.
# Knobs are environment variables read by hc/lib/area_knobs.ml.  Prints a manifest
# (variant, sha256 of the emitted Verilog) to vg2/MANIFEST.sha256.
set -u
W=${W:?set W to the area-study work directory}
B=$W/_build/default/bin
G=$B/generate_refinement.exe      # private-SRAM refinement (8 x RM_IHPSG13_1P_64x16_c2)
R=$B/generate.exe                 # register program store
K=$B/area_blocks.exe              # standalone Engine / FIFO / Host blocks
out=$W/vg2; mkdir -p $out
gen()  { local n=$1 c=$2; shift 2; env "$@" $G --config $W/cfg/$c.json --output $out/$n.v || echo "FAIL $n"; }
genr() { local n=$1 c=$2; shift 2; env "$@" $R --config $W/cfg/$c.json --output $out/$n.v || echo "FAIL $n"; }
blk()  { local n=$1; shift; env "${@:1:$#-3}" $K "${@: -3:1}" "${@: -2:1}" "${@: -1}" $out/$n.v || echo "FAIL $n"; }
DIET="AREA_COMPLETED_WIDTH=0 AREA_NARROW_PC=1"   # no completed counters, 7-bit saturating PC
ASY="AREA_ASYNC_RESET=1"                         # async reset from ~(rst_n & ena); FLUSH stays synchronous
FR="AREA_FIFO_REGS=1"                            # FIFO storage as enabled registers
# --- synchronous clear (design of record) plus one knob ---
gen base        r32f8
gen fifo16      r32f16
gen fifo4       r32f4
gen fifo2       r32f2
gen comp0       r32f8 AREA_COMPLETED_WIDTH=0
gen comp16      r32f8 AREA_COMPLETED_WIDTH=16
gen narrowpc    r32f8 AREA_NARROW_PC=1
gen narrowimg   r32f8 AREA_NARROW_IMAGE=1
gen dpnoclear   r32f8 AREA_DP_NOCLEAR=1
gen noena       r32f8 AREA_NO_ENA=1
gen fiforegs    r32f8 $FR
gen noshift     r32f8 AREA_NO_SHIFT=1
gen byteshift   r32f8 AREA_BYTE_SHIFT=1
gen nostatus    r32f8 AREA_NO_STATUS=1
gen nomover     r32f8 AREA_NO_MOVER=1
gen dw16        r16f8
gen dw16f4      r16f4
gen syncdiet4   r32f4 $DIET
# --- asynchronous reset (ena kept) ---
gen async         r32f8 $ASY
gen async_fr      r32f8 $ASY $FR
gen async_fr_s2   r32f8 $ASY $FR AREA_RST_SYNC=2
gen async_fr_noena r32f8 $ASY $FR AREA_NO_ENA=1
gen async_fr4     r32f4 $ASY $FR
gen async_fr_rx4  r32f8 $ASY $FR AREA_RX_DEPTH=4
gen dietE         r32f4 $DIET $ASY $FR
gen dietE_s2      r32f4 $DIET $ASY $FR AREA_RST_SYNC=2
gen dietE_dpnc    r32f4 $DIET $ASY $FR AREA_DP_NOCLEAR=1
gen dietE_byte    r32f4 $DIET $ASY $FR AREA_BYTE_SHIFT=1
gen dietE_noshift r32f4 $DIET $ASY $FR AREA_NO_SHIFT=1
gen dietE8        r32f8 $DIET $ASY $FR
gen dietE8_rx4    r32f8 $DIET $ASY $FR AREA_RX_DEPTH=4
gen dietG2        r32f2 $DIET $ASY $FR
gen dietF16       r16f4 $DIET $ASY $FR
gen dietF16f8     r16f8 $DIET $ASY $FR
# --- contract-neutral bundle and diets with 7-bit image_length/image_loaded (IMG) ---
IMG="AREA_NARROW_IMAGE=1"
gen cn            r32f8 $ASY $FR $IMG
gen cn4           r32f4 $ASY $FR $IMG
gen dietEi        r32f4 $DIET $ASY $FR $IMG
gen dietEi_byte   r32f4 $DIET $ASY $FR $IMG AREA_BYTE_SHIFT=1
gen dietEi8       r32f8 $DIET $ASY $FR $IMG
gen dietGi        r32f2 $DIET $ASY $FR $IMG
gen dietGi_byte   r32f2 $DIET $ASY $FR $IMG AREA_BYTE_SHIFT=1
gen dietFi        r16f4 $DIET $ASY $FR $IMG
gen dietFi8       r16f8 $DIET $ASY $FR $IMG
# --- register program store ---
genr regp64f8     reg32p64f8
genr regp32f4E    reg32p32f4 $DIET $ASY $FR
# --- standalone blocks: <kind> <width> <depth> ---
blk blk_engine32        engine 32 8
blk blk_engine16        engine 16 8
blk blk_engine32_diet   $DIET $ASY engine 32 8
blk blk_host            host 32 8
blk blk_fifo32x8        fifo 32 8
blk blk_fifo32x4        fifo 32 4
blk blk_fifo16x8        fifo 16 8
blk blk_fifo32x8_afr    $ASY $FR fifo 32 8
blk blk_fifo32x4_afr    $ASY $FR fifo 32 4
( cd $out && sha256sum *.v > MANIFEST.sha256 )
wc -l < $out/MANIFEST.sha256
