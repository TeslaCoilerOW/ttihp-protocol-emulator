`default_nettype none
`timescale 1ns / 1ps

/* Testbench top for the cocotb suite (test_*.py in this directory). It only instantiates the
   Tiny Tapeout top and exposes the TT ports; all stimulus, the lockstep
   reference model and the protocol peers live in Python.

   Waveforms: by default only the tb-level TT ports are dumped (fast, small).
   Build with WAVES=all (make WAVES=all) to dump the whole hierarchy, or
   WAVES=none to disable dumping.
*/
module tb ();

`ifndef NO_WAVES
  initial begin
    $dumpfile("tb.fst");
`ifdef DUMP_ALL
    $dumpvars(0, tb);
`else
    $dumpvars(1, tb);
`endif
    #1;
  end
`endif

  // Wire up the inputs and outputs:
  reg clk;
  reg rst_n;
  reg ena;
  reg [7:0] ui_in;
  reg [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  tt_um_teslacoilerow_protocol_emulator user_project (
      .ui_in  (ui_in),    // Dedicated inputs
      .uo_out (uo_out),   // Dedicated outputs
      .uio_in (uio_in),   // IOs: Input path
      .uio_out(uio_out),  // IOs: Output path
      .uio_oe (uio_oe),   // IOs: Enable path (active high: 0=input, 1=output)
      .ena    (ena),      // enable - goes high when design is selected
      .clk    (clk),      // clock
      .rst_n  (rst_n)     // not reset
  );

endmodule
