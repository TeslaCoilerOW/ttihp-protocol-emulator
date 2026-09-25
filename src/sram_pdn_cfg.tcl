# Copyright 2025 LibreLane Contributors
#
# Adapted from OpenLane
#
# Copyright 2020-2022 Efabless Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# ---------------------------------------------------------------------------
# SRAM-macro PDN for tt_um_teslacoilerow_protocol_emulator (IHP sg13cmos5l,
# Tiny Tapeout 8x4). SPDX-License-Identifier: Apache-2.0
#
# The macro-grid replacement below is adapted from thomasgilbert481/tt_um_loom
# src/pdn_cfg.tcl (Apache-2.0, commit 87524912e804de2f6b1a0f40cc852b2f05e90b76).
# That project took RM_IHPSG13_1P_512x16_c2_bm_bist through the TT cmos5l
# gds, precheck and gl_test actions with it. This version renames the helpers,
# restates the geometry for RM_IHPSG13_1P_64x16_c2 (same column lattice), and
# adds a check that mirrors the precheck's power-port rule.
#
# Everything from "source ...io.tcl" down to the macro-grid replacement is
# LibreLane 3.1.0.dev3's default scripts/openroad/common/pdn_cfg.tcl, verbatim.
# 3.1.0.dev3 is the version that TinyTapeout/tt-gds-action@ihp-cmos5l
# installs. Only the default's last ten lines, its macro grid, are replaced.
# All stripe geometry still comes from the FP_PDN_* keys in src/config.json.
#
# =============================================================================
# WHY THE DEFAULT MACRO GRID CANNOT WORK HERE
# =============================================================================
# On sg13cmos5l, PDN_VERTICAL_LAYER is Metal4, and TopMetal1 belongs to tt_top.
# The TT precheck forbids TopMetal1 in a user block, and TT sets
# FP_PDN_MULTILAYER 0. The default macro grid connects Metal4 to TopMetal1,
# so here it is empty (PDN-0232/0233). The macro's power pins are Metal4
# columns, so the only possible connection is a same-layer overlap. pdngen will
# not make that overlap by itself: it cuts every stripe short of a FIXED macro.
# The TT precheck (pin_check.py) also needs every Metal4 power port to reach
# within 10 um of both the top and the bottom block edge. So cut stripes plus
# bridges also fail.
#
# =============================================================================
# WHAT THIS SCRIPT DOES: FULL-HEIGHT STRIPES THROUGH SAME-NET PIN COLUMNS
# =============================================================================
# RM_IHPSG13_1P_64x16_c2.lef (236.8 x 64.36 um) has Metal4 power columns
# 2.81 um wide, in macro-local x:
#   VDD! (y 0..38.825) and VDDARRAY! (y 45.465..64.36)
#                     x0 = 4.26 + 11.24k and 151.05 + 11.24k   (k = 0..7)
#   VSS! (full height) x0 = 9.88 + 11.24k and 145.43 + 11.24k   (k = 0..7)
#   middle band x 88..151: four full-height VDD! and more VSS! columns
# This is the same lattice as the 512x16 macro that loom used.
#
# The stripe keys in src/config.json are:
#   FP_PDN_VWIDTH 2.1   the TT value. It is also the precheck's minimum power-port
#                       width on cmos5l.
#   FP_PDN_VSPACING 3.52  3.52 + 2.1 = 5.62, the POWER-to-GROUND column distance.
#   FP_PDN_VPITCH 67.44   6 x 11.24; steps over the irregular middle band.
#   FP_PDN_VOFFSET 25.40  measured from the core xMin 2.88. The first POWER
#                       stripe centre is 28.28. (loom uses 26.36 with its macro at
#                       x = 12. Here the whole lattice moves 0.96 um, two sites,
#                       to the left. Then the 26th pair ends 0.33 um inside the
#                       8x4 core instead of straddling the core edge. The geometry
#                       relative to each macro is unchanged.)
# With those keys, a macro at die x = 11.04 + 67.44 k gets exactly four stripe
# pairs:
#   POWER centres  (local) 17.24  84.68  152.12  219.56  -> VDD!/VDDARRAY! cols
#   GROUND centres (local) 22.86  90.30  157.74  225.18  -> VSS! cols
# The worst case leaves 0.02 um to the column edge, and at least 0.28 um to the
# OBS 0.26 um beside each column. Orientation must be N or FS (R0 / MX), so the
# columns keep their x. Never add -snap_to_grid: 11.24 is not a multiple of the
# 0.48 um Metal4 track.
#
# The POWER stripes cross the VDD!/VDDARRAY! split: local y 38.825..45.465,
# with LEF Metal4 OBS 39.085..45.205. We flattened
# macros/RM_IHPSG13_1P_64x16_c2/RM_IHPSG13_1P_64x16_c2.gds with KLayout on
# 2026-09-24 (docs/hardening.md). Strictly inside that band, it has no Metal4
# (any datatype), no Via3 and no TopMetal1 within 1 um of the four POWER
# stripes' x-ranges. So a stripe there joins VDD! to VDDARRAY! and nothing
# else. PDN_MACRO_CONNECTIONS already ties both of them to VPWR. Magic
# extracts the macro from its LEF, so it reports these crossings as
# "Illegal overlap between obsm4 and metal4". src/config.json sets
# ERROR_ON_ILLEGAL_OVERLAPS false for that reason.
#
# pdngen turns only FIXED instances into obstructions
# (Grid::makeInitialObstructions). pdn::check_setup requires every macro to be
# placed AND fixed (PDN-0234/0235). The wrapper at the end therefore runs
# pdngen's four steps itself, and marks the hard macros PLACED only around
# pdn::build_grids. Then:
#   1. every stripe-layer shape over a macro must lie inside power columns of
#      its own net. The only allowed gap is up to 7 um between two same-net
#      columns (the 6.64 um split);
#   2. every supply pin of every macro (8 x VDD!, VDDARRAY!, VSS!) must carry
#      at least one stripe;
#   3. every tall VPWR/VGND stripe must be at least FP_PDN_VWIDTH wide and must
#      reach within 10 um of the die's bottom and top edges (the precheck's
#      power-port rule);
#   4. check_power_grid must pass on every supply net, with its errors fatal.
# So a misplaced macro stops the flow at OpenROAD.GeneratePDN, in minutes.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl
set_global_connections

set secondary []
foreach vdd $::env(VDD_NETS) gnd $::env(GND_NETS) {
    if { $vdd != $::env(VDD_NET)} {
        lappend secondary $vdd

        set db_net [[ord::get_db_block] findNet $vdd]
        if {$db_net == "NULL"} {
            set net [odb::dbNet_create [ord::get_db_block] $vdd]
            $net setSpecial
            $net setSigType "POWER"
        }
    }

    if { $gnd != $::env(GND_NET)} {
        lappend secondary $gnd

        set db_net [[ord::get_db_block] findNet $gnd]
        if {$db_net == "NULL"} {
            set net [odb::dbNet_create [ord::get_db_block] $gnd]
            $net setSpecial
            $net setSigType "GROUND"
        }
    }
}

set_voltage_domain -name CORE -power $::env(VDD_NET) -ground $::env(GND_NET) \
    -secondary_power $secondary



if { $::env(PDN_MULTILAYER) == 1 } {

    set arg_list [list]
    if { $::env(PDN_ENABLE_PINS) } {
        lappend arg_list -pins "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
    }

    define_pdn_grid \
        -name stdcell_grid \
        -starts_with POWER \
        -voltage_domain CORE \
        {*}$arg_list

    set arg_list [list]
    append_if_equals arg_list PDN_EXTEND_TO "core_ring" -extend_to_core_ring
    append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_VERTICAL_LAYER) \
        -width $::env(PDN_VWIDTH) \
        -pitch $::env(PDN_VPITCH) \
        -offset $::env(PDN_VOFFSET) \
        -spacing $::env(PDN_VSPACING) \
        -starts_with POWER \
        {*}$arg_list

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_HORIZONTAL_LAYER) \
        -width $::env(PDN_HWIDTH) \
        -pitch $::env(PDN_HPITCH) \
        -offset $::env(PDN_HOFFSET) \
        -spacing $::env(PDN_HSPACING) \
        -starts_with POWER \
        {*}$arg_list

    add_pdn_connect \
        -grid stdcell_grid \
        -layers "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
} else {

    set arg_list [list]
    if { $::env(PDN_ENABLE_PINS) } {
        lappend arg_list -pins "$::env(PDN_VERTICAL_LAYER)"
    }

    define_pdn_grid \
        -name stdcell_grid \
        -starts_with POWER \
        -voltage_domain CORE \
        {*}$arg_list

    set arg_list [list]
    append_if_equals arg_list PDN_EXTEND_TO "core_ring" -extend_to_core_ring
    append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_VERTICAL_LAYER) \
        -width $::env(PDN_VWIDTH) \
        -pitch $::env(PDN_VPITCH) \
        -offset $::env(PDN_VOFFSET) \
        -spacing $::env(PDN_VSPACING) \
        -starts_with POWER \
        {*}$arg_list
}

# Adds the standard cell rails if enabled.
if { $::env(PDN_ENABLE_RAILS) == 1 } {
    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_RAIL_LAYER) \
        -width $::env(PDN_RAIL_WIDTH) \
        -followpins

    add_pdn_connect \
        -grid stdcell_grid \
        -layers "$::env(PDN_RAIL_LAYER) $::env(PDN_VERTICAL_LAYER)"
}


# Adds the core ring if enabled.
if { $::env(PDN_CORE_RING) == 1 } {
    if { $::env(PDN_MULTILAYER) == 1 } {
        set arg_list [list]
        append_if_flag arg_list PDN_CORE_RING_ALLOW_OUT_OF_DIE -allow_out_of_die
        append_if_flag arg_list PDN_CORE_RING_CONNECT_TO_PADS -connect_to_pads
        append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary
        append_if_exists_argument arg_list PDN_CORE_RING_CONNECT_TO_PAD_LAYERS -connect_to_pad_layers

        set pdn_core_vertical_layer $::env(PDN_VERTICAL_LAYER)
        set pdn_core_horizontal_layer $::env(PDN_HORIZONTAL_LAYER)

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] } {
            set pdn_core_vertical_layer $::env(PDN_CORE_VERTICAL_LAYER)
        }

        if { [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            set pdn_core_horizontal_layer $::env(PDN_CORE_HORIZONTAL_LAYER)
        }

        add_pdn_ring \
            -grid stdcell_grid \
            -layers "$pdn_core_vertical_layer $pdn_core_horizontal_layer" \
            -widths "$::env(PDN_CORE_RING_VWIDTH) $::env(PDN_CORE_RING_HWIDTH)" \
            -spacings "$::env(PDN_CORE_RING_VSPACING) $::env(PDN_CORE_RING_HSPACING)" \
            -core_offset "$::env(PDN_CORE_RING_VOFFSET) $::env(PDN_CORE_RING_HOFFSET)" \
            {*}$arg_list

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
        }

        if { [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_HORIZONTAL_LAYER) $::env(PDN_VERTICAL_LAYER)"
        }

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] && [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_CORE_HORIZONTAL_LAYER)"
        }

    } else {
        throw APPLICATION "PDN_CORE_RING cannot be used when PDN_MULTILAYER is set to false."
    }
}


# =============================================================================
# Replacement for the default macro grid: full-height stripes through the
# macros' power columns, verified. See the header. (Adapted from tt_um_loom.)
# =============================================================================

# The longest stretch of a macro that a stripe may cross without a same-net pin
# under it. This is allowed only between two same-net pins; the
# VDD!/VDDARRAY! split is 6.64 um. Microns.
set ::pe_sram_max_pin_gap 7.0

# The precheck (tt-support-tools precheck/pin_check.py) requires each Metal4
# power port to come within this distance of the bottom and the top block
# edge. Microns.
set ::pe_sram_port_edge_max 10.0

proc pe_sram_check_macro_stripes {} {
    set block [ord::get_db_block]
    set layer_name $::env(PDN_VERTICAL_LAYER)
    set dbu [$block getDbUnitsPerMicron]
    set max_gap [expr {round($::pe_sram_max_pin_gap * $dbu)}]
    set um [expr {1.0 / $dbu}]

    # --- 1. every power/ground pin rectangle of every hard macro on the stripe
    #        layer, in die coordinates, with the block net it is tied to
    set macros [list]
    set columns [list]
    set counts [dict create]
    foreach inst [$block getInsts] {
        if { ![[$inst getMaster] isBlock] } { continue }
        set iname [$inst getName]
        set orient [$inst getOrient]
        if { $orient ne "R0" && $orient ne "MX" } {
            error "SRAMPDN: $iname has orientation $orient; only R0 (N) and MX (FS) keep the Metal4 power columns vertical"
        }
        set bb [$inst getBBox]
        set bx0 [$bb xMin]
        set by0 [$bb yMin]
        set bx1 [$bb xMax]
        set by1 [$bb yMax]
        lappend macros [list $iname $bx0 $by0 $bx1 $by1]
        puts [format "SRAMPDN macro %s orient %s bbox %.3f %.3f %.3f %.3f status %s" $iname $orient \
            [expr {$bx0 * $um}] [expr {$by0 * $um}] [expr {$bx1 * $um}] [expr {$by1 * $um}] \
            [$inst getPlacementStatus]]
        foreach iterm [$inst getITerms] {
            set mterm [$iterm getMTerm]
            set sig [$mterm getSigType]
            if { $sig ne "POWER" && $sig ne "GROUND" } { continue }
            set key "$iname/[$mterm getName]"
            set net [$iterm getNet]
            if { $net eq "NULL" } {
                error "SRAMPDN: $key is not connected to any net (check PDN_MACRO_CONNECTIONS)"
            }
            dict set counts $key 0
            set ux1 ""
            foreach mpin [$mterm getMPins] {
                foreach box [$mpin getGeometry] {
                    if { [$box isVia] } { continue }
                    if { [[$box getTechLayer] getName] ne $layer_name } { continue }
                    set x1 [expr {$bx0 + [$box xMin]}]
                    set x2 [expr {$bx0 + [$box xMax]}]
                    if { $orient eq "R0" } {
                        set y1 [expr {$by0 + [$box yMin]}]
                        set y2 [expr {$by0 + [$box yMax]}]
                    } else {
                        set y1 [expr {$by1 - [$box yMax]}]
                        set y2 [expr {$by1 - [$box yMin]}]
                    }
                    lappend columns [list $net $key $x1 $y1 $x2 $y2]
                    if { $ux1 eq "" } {
                        lassign [list $x1 $y1 $x2 $y2] ux1 uy1 ux2 uy2
                    } else {
                        set ux1 [expr {min($ux1, $x1)}]
                        set uy1 [expr {min($uy1, $y1)}]
                        set ux2 [expr {max($ux2, $x2)}]
                        set uy2 [expr {max($uy2, $y2)}]
                    }
                }
            }
            # Cross-check the hand-written transform against OpenDB's own.
            set ib [$iterm getBBox]
            if { $ux1 ne "" && ($ux1 != [$ib xMin] || $uy1 != [$ib yMin] || $ux2 != [$ib xMax] || $uy2 != [$ib yMax]) } {
                error "SRAMPDN: transformed pins of $key ($ux1 $uy1 $ux2 $uy2) disagree with the ITerm bbox ([$ib xMin] [$ib yMin] [$ib xMax] [$ib yMax])"
            }
        }
    }
    if { [llength $macros] == 0 } {
        puts "SRAMPDN no hard macros; nothing to check"
        return
    }

    # --- 2. every stripe-layer shape of every net, checked against every macro
    set bad [list]
    foreach net [$block getNets] {
        if { ![$net isSpecial] } { continue }
        foreach swire [$net getSWires] {
            foreach sbox [$swire getWires] {
                if { [$sbox isVia] } { continue }
                if { [[$sbox getTechLayer] getName] ne $layer_name } { continue }
                set sx1 [$sbox xMin]
                set sy1 [$sbox yMin]
                set sx2 [$sbox xMax]
                set sy2 [$sbox yMax]
                foreach m $macros {
                    lassign $m iname mx0 my0 mx1 my1
                    if { $sx2 <= $mx0 || $sx1 >= $mx1 || $sy2 <= $my0 || $sy1 >= $my1 } { continue }
                    set desc [format "%s x %.3f..%.3f y %.3f..%.3f" [$net getName] \
                        [expr {$sx1 * $um}] [expr {$sx2 * $um}] [expr {$sy1 * $um}] [expr {$sy2 * $um}]]
                    set ylo [expr {max($sy1, $my0)}]
                    set yhi [expr {min($sy2, $my1)}]
                    # same-net pin rectangles that contain the stripe's x-range
                    set cover [list]
                    foreach c $columns {
                        lassign $c cnet key px1 py1 px2 py2
                        if { $cnet ne $net } { continue }
                        if { $px1 > $sx1 || $px2 < $sx2 } { continue }
                        if { $py2 <= $ylo || $py1 >= $yhi } { continue }
                        lappend cover [list $py1 $py2 $key]
                    }
                    set cover [lsort -integer -index 0 $cover]
                    set cur $ylo
                    set keys [list]
                    set why ""
                    foreach r $cover {
                        lassign $r py1 py2 key
                        if { $py1 > $cur } {
                            if { $cur == $ylo } {
                                set why [format "no same-net pin under it from y %.3f to %.3f" [expr {$cur * $um}] [expr {$py1 * $um}]]
                                break
                            }
                            if { $py1 - $cur > $max_gap } {
                                set why [format "crosses %.3f um without a same-net pin (y %.3f..%.3f)" \
                                    [expr {($py1 - $cur) * $um}] [expr {$cur * $um}] [expr {$py1 * $um}]]
                                break
                            }
                            puts [format "SRAMPDN   %s crosses a %.3f um gap between same-net pins at y %.3f..%.3f" \
                                $desc [expr {($py1 - $cur) * $um}] [expr {$cur * $um}] [expr {$py1 * $um}]]
                        }
                        set cur [expr {max($cur, $py2)}]
                        lappend keys $key
                    }
                    if { $why eq "" && $cur < $yhi } {
                        set why [format "no same-net pin under it from y %.3f to %.3f" [expr {$cur * $um}] [expr {$yhi * $um}]]
                    }
                    if { $why ne "" } {
                        lappend bad "$desc over $iname: $why"
                        continue
                    }
                    foreach key [lsort -unique $keys] { dict incr counts $key }
                    puts "SRAMPDN stripe $desc runs inside [join [lsort -unique $keys] { + }]"
                }
            }
        }
    }
    if { [llength $bad] > 0 } {
        foreach b $bad { puts "SRAMPDN BAD $b" }
        error "SRAMPDN: [llength $bad] stripe shape(s) over a macro are not inside same-net power pins (would short or float); fix FP_PDN_V* or the macro location"
    }

    # --- 3. every supply pin of every macro needs at least one stripe
    set missing [list]
    dict for {key n} $counts {
        puts "SRAMPDN $key: $n stripe(s)"
        if { $n == 0 } { lappend missing $key }
    }
    if { [llength $missing] > 0 } {
        error "SRAMPDN: no stripe runs through: [join $missing {, }]"
    }
}

# Mirror of the precheck's Metal4 power-port rule, applied to the tall
# stripes of every supply net: width >= PDN_VWIDTH, and both ends within
# pe_sram_port_edge_max of the die edges. A macro-induced cut or a clipped
# last stripe would otherwise surface only in the precheck job.
proc pe_sram_check_power_ports {} {
    set block [ord::get_db_block]
    set layer_name $::env(PDN_VERTICAL_LAYER)
    set dbu [$block getDbUnitsPerMicron]
    set um [expr {1.0 / $dbu}]
    set die [$block getDieArea]
    set dy0 [$die yMin]
    set dy1 [$die yMax]
    set tall [expr {round(100.0 * $dbu)}]
    set min_w [expr {round($::env(PDN_VWIDTH) * $dbu)}]
    set edge [expr {round($::pe_sram_port_edge_max * $dbu)}]
    set bad [list]
    foreach net_name [concat $::env(VDD_NETS) $::env(GND_NETS)] {
        set net [$block findNet $net_name]
        if { $net eq "NULL" } { error "SRAMPDN: supply net $net_name not found" }
        set spans [dict create]
        foreach swire [$net getSWires] {
            foreach sbox [$swire getWires] {
                if { [$sbox isVia] } { continue }
                if { [[$sbox getTechLayer] getName] ne $layer_name } { continue }
                if { [$sbox yMax] - [$sbox yMin] < $tall } { continue }
                set k "[$sbox xMin] [$sbox xMax]"
                if { [dict exists $spans $k] } {
                    lassign [dict get $spans $k] a b
                    dict set spans $k [list [expr {min($a, [$sbox yMin])}] [expr {max($b, [$sbox yMax])}]]
                } else {
                    dict set spans $k [list [$sbox yMin] [$sbox yMax]]
                }
            }
        }
        if { [dict size $spans] == 0 } {
            lappend bad "$net_name has no tall $layer_name stripe"
        }
        dict for {k v} $spans {
            lassign $k x0 x1
            lassign $v y0 y1
            set d [format "%s x %.3f..%.3f y %.3f..%.3f" $net_name [expr {$x0 * $um}] [expr {$x1 * $um}] [expr {$y0 * $um}] [expr {$y1 * $um}]]
            if { $x1 - $x0 < $min_w } { lappend bad "$d narrower than PDN_VWIDTH" }
            if { $y0 - $dy0 > $edge } { lappend bad "$d ends more than $::pe_sram_port_edge_max um above the bottom edge" }
            if { $dy1 - $y1 > $edge } { lappend bad "$d ends more than $::pe_sram_port_edge_max um below the top edge" }
        }
        puts "SRAMPDN $net_name: [dict size $spans] tall $layer_name stripe(s)"
    }
    if { [llength $bad] > 0 } {
        foreach b $bad { puts "SRAMPDN BAD $b" }
        error "SRAMPDN: [llength $bad] power stripe(s) would fail the TT precheck power-port rule"
    }
}

# Wrap LibreLane's single `pdngen` call (scripts/openroad/pdn.tcl sources this
# file first, then runs `pdngen`, with -skip_trim only if PDN_SKIPTRIM).
#
# OpenROAD's pdngen proc (src/pdn/src/pdn.tcl at the revision that LibreLane
# 3.1.0.dev3 pins) runs these steps:
#     parse flags ; pdn::check_setup ; pdn::build_grids $trim
#     pdn::write_to_db $add_pins $failed_via_report ; pdn::reset_shapes
# check_setup requires every macro to be placed AND fixed (PDN-0234/0235).
# build_grids turns only fixed instances into obstructions. So the wrapper runs
# the same four steps itself, and releases the hard macros only around
# build_grids. Any other flag goes to the original proc unchanged.
if { [info commands ::pe_sram_pdngen_unwrapped] eq "" } {
    rename ::pdngen ::pe_sram_pdngen_unwrapped
    proc ::pdngen { args } {
        foreach a $args {
            if { $a ne "-skip_trim" } {
                return [::pe_sram_pdngen_unwrapped {*}$args]
            }
        }
        foreach cmd {pdn::check_setup pdn::build_grids pdn::write_to_db pdn::reset_shapes} {
            if { [info commands ::$cmd] eq "" } {
                error "SRAMPDN: $cmd does not exist; this wrapper follows pdngen in the OpenROAD pinned by LibreLane 3.1.0.dev3"
            }
        }
        set trim [expr {[lsearch -exact $args -skip_trim] < 0}]

        ::pdn::check_setup

        set released [list]
        foreach inst [[ord::get_db_block] getInsts] {
            if { [[$inst getMaster] isBlock] && [$inst isFixed] } {
                lappend released [list $inst [$inst getPlacementStatus]]
                $inst setPlacementStatus "PLACED"
            }
        }
        set rc [catch { ::pdn::build_grids $trim } msg opts]
        foreach r $released {
            lassign $r inst status
            $inst setPlacementStatus $status
        }
        if { $rc } {
            return -options $opts $msg
        }

        ::pdn::write_to_db 1 ""
        ::pdn::reset_shapes

        pe_sram_check_macro_stripes
        pe_sram_check_power_ports
        foreach net_name [concat $::env(VDD_NETS) $::env(GND_NETS)] {
            puts "SRAMPDN check_power_grid -net $net_name"
            check_power_grid -net $net_name
        }
    }
}
