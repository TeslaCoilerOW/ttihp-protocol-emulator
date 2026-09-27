# SPDX-License-Identifier: Apache-2.0
# pe_extra.tcl: path-class queries for tools/sta/sta_retime.py.
#
# LibreLane's corner.tcl sources this file through STA_EXTRA_CORNER_TCL_FILE,
# after the design, SDC and SPEF are loaded and the command corner is set, and
# before it writes its own reports and metrics. Only read-only timing queries:
# no constraint, netlist or parasitic changes.
#
# Environment:
#   PE_OUT               output directory (the corner directory)
#   PE_MACRO_INSTANCES   Tcl list of macro instance names (resolved.json MACROS)
#   PE_MACRO_CLOCK_PINS  Tcl list of macro clock pin names (e.g. A_CLK)
#
# Writes $PE_OUT/extra.txt ("key value..." per line) and $PE_OUT/extra_paths.rpt
# (the worst path of each class, full_clock_expanded).
#
# Path classes (start points -> end points):
#   r2r      register clock pins and macro clock pins -> register data and
#            async pins and macro data inputs
#   in2reg   all_inputs -no_clocks -> the r2r end points
#   reg2out  the r2r start points  -> all_outputs
#   in2out   all_inputs -no_clocks -> all_outputs

if { ![info exists corner_name] } {
    set corner_name $::env(_CURRENT_CORNER_NAME)
}
set pe_dir $::env(PE_OUT)
file mkdir $pe_dir
set pe_fh [open $pe_dir/extra.txt w]
proc pe_put {args} { global pe_fh; puts $pe_fh [join $args " "] }

set pe_clocks [sta::sort_by_name [sta::all_clocks]]
pe_put corner $corner_name
pe_put clock_period [get_property [lindex $pe_clocks 0] period]

# Macro pins: clock pins start reg->reg paths, the other inputs end them.
set pe_macro_clk [list]
set pe_macro_d [list]
set pe_n_macros 0
foreach pe_inst $::env(PE_MACRO_INSTANCES) {
    set pe_cells [get_cells -quiet $pe_inst]
    if { [llength $pe_cells] == 0 } {
        pe_put missing_macro_instance $pe_inst
        continue
    }
    incr pe_n_macros
    foreach p [get_pins -quiet -of_objects $pe_cells] {
        if { [get_property $p direction] ne "input" } {
            continue
        }
        set pe_name [get_property $p full_name]
        set pe_leaf [string range $pe_name [expr {[string last "/" $pe_name] + 1}] end]
        if { [lsearch -exact $::env(PE_MACRO_CLOCK_PINS) $pe_leaf] >= 0 } {
            lappend pe_macro_clk $p
        } else {
            lappend pe_macro_d $p
        }
    }
}

set pe_in   [all_inputs -no_clocks]
set pe_outp [all_outputs]
set pe_reg_start [concat [all_registers -clock_pins] $pe_macro_clk]
set pe_reg_end   [concat [all_registers -data_pins] [all_registers -async_pins] $pe_macro_d]
pe_put n_inputs_no_clk [llength $pe_in]
pe_put n_outputs [llength $pe_outp]
pe_put n_reg_start_pins [llength $pe_reg_start]
pe_put n_reg_end_pins [llength $pe_reg_end]
pe_put n_macro_instances $pe_n_macros
pe_put n_macro_clk [llength $pe_macro_clk]
pe_put n_macro_data_in [llength $pe_macro_d]

# The worst path of a class: one path per path group and end point, then the
# minimum slack over the groups.
proc pe_worst {tag mode args} {
    set paths [find_timing_paths -path_delay $mode -group_path_count 1 \
                   -endpoint_path_count 1 -sort_by_slack {*}$args]
    if { [llength $paths] == 0 } {
        pe_put $tag none
        return
    }
    set best ""
    set bs 1e30
    foreach p $paths {
        set s [get_property $p slack]
        if { $s < $bs } {
            set bs $s
            set best $p
        }
    }
    set sp [get_property $best startpoint]
    set ep [get_property $best endpoint]
    pe_put $tag [format %.15g $bs] [get_property $sp full_name] [get_property $ep full_name]
}

foreach {mode mtag} {max setup min hold} {
    pe_worst ${mtag}_all     $mode
    pe_worst ${mtag}_r2r     $mode -from $pe_reg_start -to $pe_reg_end
    pe_worst ${mtag}_in2reg  $mode -from $pe_in        -to $pe_reg_end
    pe_worst ${mtag}_reg2out $mode -from $pe_reg_start -to $pe_outp
    pe_worst ${mtag}_in2out  $mode -from $pe_in        -to $pe_outp
}
pe_put worst_slack_max [format %.15g [worst_slack -corner $corner_name -max]]
pe_put worst_slack_min [format %.15g [worst_slack -corner $corner_name -min]]
close $pe_fh

sta::redirect_file_begin $pe_dir/extra_paths.rpt
foreach {mode mtag} {max SETUP min HOLD} {
    foreach {cls from to} [list all {} {} r2r $pe_reg_start $pe_reg_end in2reg $pe_in $pe_reg_end \
                               reg2out $pe_reg_start $pe_outp in2out $pe_in $pe_outp] {
        puts "\n######## $mtag $cls"
        set pe_args [list]
        if { $cls ne "all" } {
            set pe_args [list -from $from -to $to]
        }
        report_checks -path_delay $mode {*}$pe_args -group_path_count 1 -endpoint_path_count 1 \
            -sort_by_slack -fields {slew cap input net fanout} -format full_clock_expanded \
            -corner $corner_name
    }
}
sta::redirect_file_end
