/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Full FPGA-top testbench with the Tiny Tapeout port interface, so the
 * unchanged cocotb suite in test/ (harness.py lockstep checking against the
 * Python reference model) runs on the FPGA prototype: board top + shell +
 * tt_um_teslacoilerow_protocol_emulator + FPGA SRAM stand-in, with the
 * board's pads as the only connection.
 *
 *   clk, rst_n, ena, ui_in, uio_in  driven by cocotb, like test/tb.v
 *   uo_out                          read from the board's pins
 *   uio_out, uio_oe                 the values at the protocol pad drivers
 *                                   (what the lockstep checker compares);
 *                                   the pads themselves are checked below
 *
 * Defines: PE_TB_URBANA selects the Urbana top (default Cmod A7).
 * PE_TB_NETLIST: the board top is the Yosys synth_xilinx netlist (simulated
 * with Yosys' Xilinx cell models); only the top's kept wires are probed.
 * PE_CLOCK_HOST: host-clocked build (clk -> host_clk pin); otherwise the
 * pll50 build in external-host mode (clk -> board oscillator pin; under
 * PE_FPGA_SIM the MMCM is a wire, so the core runs on clk directly).
 *
 * Every protocol pad is driven by the testbench with uio_in[i] exactly when
 * the FPGA releases it, so each pad carries the FPGA's value when uio_oe[i]
 * and the environment's value otherwise, as on a real board. The checker
 * ends the simulation ($fatal, which fails the running cocotb test) if a
 * pad disagrees with that, or if a board pin that carries uo_out disagrees
 * with the core.
 */

`default_nettype none
`timescale 1ns / 1ps

module tb ();

`ifndef NO_WAVES
  initial begin
    $dumpfile("tb.fst");
    $dumpvars(1, tb);
    #1;
  end
`endif

  reg        clk;
  reg        rst_n;
  reg        ena;
  reg  [7:0] ui_in;
  reg  [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  wire [7:0] pads;

`ifdef PE_TB_URBANA
  wire [15:0] led;
  wire [4:0]  jab_uo;
  wire        servo_uo5;
  wire        uart_txd;
`ifndef PE_CLOCK_HOST
  wire        host_clk_out;
`endif
  pe_top_urbana dut (
      .clk_100mhz(
`ifdef PE_CLOCK_HOST
                  1'b0
`else
                  clk
`endif
                  ),
      .btn       (4'b0000),
      .sw        ({14'd0, !ena, 1'b1}),   // sw[0] = external host, sw[1] = deselect
      .led       (led),
      .uart_rxd  (1'b1),
      .uart_txd  (uart_txd),
      .pmoda     (pads),
      .pmodb     (ui_in),
      .jab_uo    (jab_uo),
`ifdef PE_CLOCK_HOST
      .host_clk  (clk),
`else
      .host_clk  (host_clk_out),
`endif
      .servo_uo5 (servo_uo5),
      .servo_rst (!rst_n)
  );
  // uo_out[7:6] (fault, event) exist only on LEDs on this board.
  assign uo_out = {led[2], led[1], servo_uo5, jab_uo};
`else
  wire [1:0] led;
  wire       led0_r, led0_g, led0_b, uart_txd;
`ifndef PE_CLOCK_HOST
  wire       host_clk_out;
`endif
  pe_top_cmod_a7 dut (
`ifdef PE_CLOCK_HOST
      .sysclk    (1'b0),
      .host_clk  (clk),
`else
      .sysclk    (clk),
      .host_clk  (host_clk_out),
`endif
      .btn       (2'b00),
      .led       (led),
      .led0_r    (led0_r),
      .led0_g    (led0_g),
      .led0_b    (led0_b),
      .uart_rxd  (1'b1),
      .uart_txd  (uart_txd),
      .ui_pin    (ui_in),
      .uo_pin    (uo_out),
      .host_rst_n(rst_n),
      .host_ena  (ena),
      .host_sel_n(1'b0),                  // external pin host
      .ja        (pads)
  );
`endif

  // Pad drivers inside the board top (inferred IOBUF: O = pad, T = ~uio_oe).
  assign uio_out = dut.uio_out;
  assign uio_oe  = dut.uio_oe;

`ifdef PE_TB_INJECT_PAD_FAULT
  // Checker self-test: a short to ground on pad 1 (test_smoke drives pin 1
  // high). Must end in $fatal.
  assign (strong0, strong1) pads[1] = 1'b0;
`endif

  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_env
      assign pads[i] = uio_oe[i] ? 1'bz : uio_in[i];
    end
  endgenerate

  // Pad-level checks, sampled just before each rising edge. Any violation
  // ends the simulation with $fatal, which fails the running cocotb test.
  integer errors = 0;
  always @(negedge clk) begin : check
    integer b;
    if (rst_n !== 1'bx && $time > 0) begin
      for (b = 0; b < 8; b = b + 1) begin
        if (uio_oe[b] === 1'b1 && pads[b] !== uio_out[b]) begin
          $display("%t PAD ERROR pin %0d driven by FPGA: pad=%b uio_out=%b", $time, b, pads[b], uio_out[b]);
          errors = errors + 1;
        end
        if (uio_oe[b] === 1'b0 && pads[b] !== uio_in[b]) begin
          $display("%t PAD ERROR pin %0d released: pad=%b env=%b", $time, b, pads[b], uio_in[b]);
          errors = errors + 1;
        end
      end
      if (dut.uio_in !== pads) begin
        $display("%t PAD ERROR core-side uio_in=%b pads=%b", $time, dut.uio_in, pads);
        errors = errors + 1;
      end
`ifndef PE_TB_NETLIST
      if (dut.shell.tt.uo_out !== uo_out && rst_n === 1'b1) begin
        $display("%t PIN ERROR core uo_out=%b board pins=%b", $time, dut.shell.tt.uo_out, uo_out);
        errors = errors + 1;
      end
`endif
      if (errors != 0) $fatal(1, "FPGA pad/pin check failed (%0d errors)", errors);
    end
  end

endmodule

`default_nettype wire
