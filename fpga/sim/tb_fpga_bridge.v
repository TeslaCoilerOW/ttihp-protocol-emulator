/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Board-level testbench for the UART-bridge build (CLOCK=pll50): the board
 * top sees only its oscillator pin (driven at the 50 MHz core rate; under
 * PE_FPGA_SIM the MMCM is a wire), the USB-UART pins and the protocol Pmod.
 * The Pmod pads have weak pull-ups (like the XDC's PULLTYPE PULLUP) and two
 * switchable jumpers, the wiring of the hardware loopback test:
 *   jumpers[0]: uio0 <-> uio1 (Pmod pins 1-2)
 *   jumpers[1]: uio3 <-> uio4 (Pmod pins 4-7)
 * test_fpga_bridge.py drives the UART with fpga/host/pe_host.py.
 * PE_TB_URBANA selects the Urbana top (default Cmod A7).
 */

`default_nettype none
`timescale 1ns / 1ps

module tb_bridge ();

`ifndef NO_WAVES
  initial begin
    $dumpfile("tb_bridge.fst");
    $dumpvars(1, tb_bridge);
    #1;
  end
`endif

  reg        clk = 1'b0;
  reg        btn0 = 1'b0;
  reg        uart_rx = 1'b1;    // PC -> FPGA
  wire       uart_tx;           // FPGA -> PC
  reg  [1:0] jumpers = 2'b00;
  reg        host_ext = 1'b0;   // 1: select the external pin host (bridge answers 'D')
  wire [7:0] pads;

  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_pull
      pullup (pads[i]);
    end
  endgenerate
  tranif1 j01 (pads[0], pads[1], jumpers[0]);
  tranif1 j34 (pads[3], pads[4], jumpers[1]);

`ifdef PE_TB_URBANA
  wire [15:0] led;
  wire [4:0]  jab_uo;
  wire        servo_uo5, host_clk;
  pe_top_urbana dut (
      .clk_100mhz(clk),
      .btn       ({3'b000, btn0}),
      .sw        ({15'd0, host_ext}), // sw[0] down: UART bridge
      .led       (led),
      .uart_rxd  (uart_rx),
      .uart_txd  (uart_tx),
      .pmoda     (pads),
      .pmodb     (8'd0),
      .jab_uo    (jab_uo),
      .host_clk  (host_clk),
      .servo_uo5 (servo_uo5),
      .servo_rst (1'b0)
  );
`else
  wire [1:0] led;
  wire       led0_r, led0_g, led0_b, host_clk;
  wire [7:0] uo_pin;
  pe_top_cmod_a7 dut (
      .sysclk    (clk),
      .btn       ({1'b0, btn0}),
      .led       (led),
      .led0_r    (led0_r),
      .led0_g    (led0_g),
      .led0_b    (led0_b),
      .uart_rxd  (uart_rx),
      .uart_txd  (uart_tx),
      .ui_pin    (8'd0),
      .uo_pin    (uo_pin),
      .host_clk  (host_clk),
      .host_rst_n(1'b1),
      .host_ena  (1'b1),
      .host_sel_n(!host_ext),         // open (1): UART bridge
      .ja        (pads)
  );
`endif

endmodule

`default_nettype wire
