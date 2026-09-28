# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# AMD Vivado non-project batch build of the FPGA prototype, for vendor timing
# sign-off. It reads the same RTL, pin constraints and clock constraints as
# the open-source flow (fpga/scripts/build.sh): the board top, shell, UART
# bridge, on-board capture unit, SRAM stand-in, the unchanged Tiny Tapeout
# top and generated core.
#
#   vivado -mode batch -source fpga/vivado/build.tcl -tclargs BOARD [CLOCK] [OUT_DIR] [KEY=VALUE ...]
#
#   BOARD    cmod_a7 (xc7a35tcpg236-1) | urbana (xc7s50csga324-1)
#   CLOCK    pll50 (default) | pll40 | host | osc12 (cmod_a7 only)
#   OUT_DIR  default: build/vivado_<board>_<clock>[_bridgeonly]
#   KEY=VALUE options:
#     BRIDGE_ONLY=1    Cmod A7 bridge builds: DIP pin-host pins unused (PE_NO_PIN_HOST)
#     DEFINES=A,B=1    extra Verilog defines, e.g. PE_SCOPE_AW=14 or PE_NO_SCOPE
#     CLOCKS=shared    (default) the open-source flow's clock XDC as is, including
#                      create_clock on the core clock net `clk`
#     CLOCKS=derived   only the port clock of that XDC (oscillator or host_clk);
#                      Vivado derives the MMCM output clock from the MMCM
#                      parameters (includes MMCM jitter and phase error)
#     DIRECTIVE=NAME   place_design / route_design directive (default Default)
#     BITSTREAM=1      (default) write the bitstream only when the timing
#                      verdict is PASS; BITSTREAM=always writes it anyway;
#                      BITSTREAM=0 never
#
# Outputs in OUT_DIR: post_synth.dcp, post_route.dcp, timing_summary.rpt,
# utilization.rpt, utilization_synth.rpt, clocks.rpt, drc.rpt,
# methodology.rpt, sources.txt, clock.xdc (the clock constraints read),
# vivado_timing.txt, and <name>.bit. The verdict, printed as "VIVADO TIMING
# PASS" or "VIVADO TIMING FAIL" (exit status 1 on FAIL), needs WNS >= 0,
# TNS = 0, WHS >= 0, THS = 0, at least one timed path, and no unclocked
# register and no unconstrained internal endpoint in the check_timing
# section of timing_summary.rpt: the rules of fpga/vivado/timing_check.py,
# which gives the same verdict from the report alone.
#
# Status: written for Vivado 2023.2 or later; not yet run (no Vivado
# installation was available). docs/fpga.md, "Vivado sign-off flow".

proc usage {} {
  puts "usage: vivado -mode batch -source build.tcl -tclargs BOARD \[CLOCK\] \[OUT_DIR\] \[KEY=VALUE ...\]"
  exit 2
}

set here [file dirname [file normalize [info script]]]
set fpga [file dirname $here]
set repo [file dirname $fpga]

# ---- arguments ---------------------------------------------------------
if {[llength $argv] < 1} { usage }
set board [lindex $argv 0]
set clock pll50
set out ""
array set opt {BRIDGE_ONLY 0 DEFINES "" CLOCKS shared DIRECTIVE Default BITSTREAM 1}
set pos 0
foreach a [lrange $argv 1 end] {
  if {[regexp {^([A-Z_]+)=(.*)$} $a -> k v]} {
    if {![info exists opt($k)]} { puts "unknown option $k"; usage }
    set opt($k) $v
  } elseif {$pos == 0} {
    set clock $a
    incr pos
  } elseif {$pos == 1} {
    set out $a
    incr pos
  } else {
    usage
  }
}

switch -- $board {
  cmod_a7 { set top pe_top_cmod_a7; set part xc7a35tcpg236-1; set pin_xdc cmod_a7_35t.xdc }
  urbana  { set top pe_top_urbana;  set part xc7s50csga324-1; set pin_xdc urbana_xc7s50.xdc }
  default { puts "unknown board $board"; usage }
}
set defines {}
switch -- $clock {
  pll50 {}
  pll40 { lappend defines PE_CLOCK_PLL40 }
  host  { lappend defines PE_CLOCK_HOST }
  osc12 {
    if {$board ne "cmod_a7"} { puts "osc12 is a cmod_a7 option"; exit 2 }
    lappend defines PE_CLOCK_OSC
  }
  default { puts "unknown clock $clock"; usage }
}
set name pe_${board}_${clock}
if {$opt(BRIDGE_ONLY)} {
  if {$board ne "cmod_a7" || $clock eq "host"} { puts "BRIDGE_ONLY=1 is a cmod_a7 bridge-build option"; exit 2 }
  lappend defines PE_NO_PIN_HOST
  append name _bridgeonly
}
foreach d [split $opt(DEFINES) ,] {
  if {$d ne ""} { lappend defines $d }
  # 16-bit record counts in the capture unit's status: at most 2^15 records
  if {[regexp {^PE_SCOPE_AW=(.*)$} $d -> aw] && (![string is integer -strict $aw] || $aw < 2 || $aw > 15)} {
    puts "PE_SCOPE_AW=$aw: the capture unit supports 2..15 (at most 32,768 records)"
    exit 2
  }
}
if {[lsearch -exact {0 1 always} $opt(BITSTREAM)] < 0} { puts "BITSTREAM must be 0, 1 or always"; usage }
if {$out eq ""} { set out [file join build vivado_[string range $name 3 end]] }
file mkdir $out
set out [file normalize $out]

# ---- sources: the list of fpga/scripts/build.sh ------------------------
set sources [list \
  $repo/src/project.v \
  $repo/src/protocol_emulator_core.v \
  $fpga/rtl/RM_IHPSG13_1P_64x16_c2_fpga.v \
  $fpga/rtl/pe_fpga_sram_64x16.v \
  $fpga/rtl/pe_uart_host_bridge.v \
  $fpga/rtl/pe_fpga_scope.v \
  $fpga/rtl/pe_fpga_shell.v \
  $fpga/rtl/pe_fpga_clkgen.v \
  $fpga/rtl/pe_fpga_clkfwd.v \
  $fpga/rtl/pe_top_${board}.v]
set fh [open $out/sources.txt w]
foreach f [concat $sources [list $fpga/constraints/$pin_xdc $fpga/constraints/clock_${board}_${clock}.xdc]] {
  if {![file exists $f]} { puts "missing $f"; exit 2 }
  puts $fh $f
}
puts $fh "defines: $defines"
puts $fh "options: [array get opt]"
close $fh

# ---- constraints --------------------------------------------------------
# The pin XDC is shared with nextpnr-xilinx unchanged. The clock XDC is read
# as is (CLOCKS=shared) or with its create_clock on nets removed
# (CLOCKS=derived). Vivado-only settings (configuration bank voltage, used by
# DRC only) go into vivado_only.xdc.
set src_clk [open $fpga/constraints/clock_${board}_${clock}.xdc r]
set dst_clk [open $out/clock.xdc w]
while {[gets $src_clk line] >= 0} {
  if {$opt(CLOCKS) eq "derived" && [string match "*get_nets*" $line]} {
    puts $dst_clk "# (CLOCKS=derived) $line"
  } else {
    puts $dst_clk $line
  }
}
close $src_clk
close $dst_clk
set vo [open $out/vivado_only.xdc w]
puts $vo "set_property CFGBVS VCCO \[current_design\]"
puts $vo "set_property CONFIG_VOLTAGE 3.3 \[current_design\]"
close $vo

# ---- flow ---------------------------------------------------------------
read_verilog $sources
read_xdc $fpga/constraints/$pin_xdc
read_xdc $out/clock.xdc
read_xdc $out/vivado_only.xdc

if {[llength $defines]} {
  synth_design -top $top -part $part -verilog_define $defines
} else {
  synth_design -top $top -part $part
}
write_checkpoint -force $out/post_synth.dcp
report_utilization -file $out/utilization_synth.rpt

opt_design
place_design -directive $opt(DIRECTIVE)
phys_opt_design
route_design -directive $opt(DIRECTIVE)
write_checkpoint -force $out/post_route.dcp

report_timing_summary -max_paths 10 -report_unconstrained -file $out/timing_summary.rpt
report_utilization -file $out/utilization.rpt
report_clocks -file $out/clocks.rpt
report_drc -file $out/drc.rpt
report_methodology -file $out/methodology.rpt

# ---- verdict: worst setup / hold slack, total negative slack -------------
proc slack_of {paths} {
  if {[llength $paths] == 0} { return "" }
  return [get_property SLACK [lindex $paths 0]]
}
set wns [slack_of [get_timing_paths -delay_type max -max_paths 1 -nworst 1]]
set whs [slack_of [get_timing_paths -delay_type min -max_paths 1 -nworst 1]]
set tns 0.0
foreach p [get_timing_paths -delay_type max -max_paths 100000 -nworst 1 -slack_lesser_than 0] {
  set tns [expr {$tns + [get_property SLACK $p]}]
}
set ths 0.0
foreach p [get_timing_paths -delay_type min -max_paths 100000 -nworst 1 -slack_lesser_than 0] {
  set ths [expr {$ths + [get_property SLACK $p]}]
}
# check_timing counts from the report (the section report_timing_summary writes)
set fh [open $out/timing_summary.rpt r]
set rpt [read $fh]
close $fh
array set chk {}
foreach {all k n} [regexp -all -inline {checking (\w+) \((\d+)\)} $rpt] { set chk($k) $n }
set reasons {}
if {$wns eq "" || $wns < 0} { lappend reasons "WNS $wns" }
if {$whs eq "" || $whs < 0} { lappend reasons "WHS $whs" }
if {$tns != 0} { lappend reasons "TNS $tns" }
if {$ths != 0} { lappend reasons "THS $ths" }
foreach k {no_clock unconstrained_internal_endpoints} {
  if {![info exists chk($k)]} {
    lappend reasons "check_timing $k not found in the report"
  } elseif {$chk($k) != 0} {
    lappend reasons "check_timing $k $chk($k)"
  }
}
set pass [expr {[llength $reasons] == 0}]
set verdict [expr {$pass ? "PASS" : "FAIL"}]
set fh [open $out/vivado_timing.txt w]
puts $fh "design $name part $part clocks $opt(CLOCKS)"
puts $fh "WNS $wns TNS $tns WHS $whs THS $ths"
puts $fh "check_timing [array get chk]"
puts $fh "VIVADO TIMING $verdict [join $reasons {; }]"
close $fh
puts "WNS $wns TNS $tns WHS $whs THS $ths; check_timing [array get chk]"

if {$opt(BITSTREAM) eq "always" || ($opt(BITSTREAM) eq "1" && $pass)} {
  write_bitstream -force $out/$name.bit
} elseif {$opt(BITSTREAM) eq "1"} {
  puts "no bitstream written: timing FAIL (BITSTREAM=always writes it anyway)"
}
puts "VIVADO TIMING $verdict ($name, $part, clocks $opt(CLOCKS)) [join $reasons {; }]; reports in $out"
if {!$pass} { exit 1 }
