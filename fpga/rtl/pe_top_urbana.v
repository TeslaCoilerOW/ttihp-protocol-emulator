/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Real Digital Urbana (Spartan-7 xc7s50csga324-1, MIT 6.205) top for the
 * protocol emulator. Pin map: fpga/constraints/urbana_xc7s50.xdc, docs/fpga.md.
 *
 * The 30-pin Pmod+ header has 22 signals: PMOD A (8), PMOD B (8) and six
 * GPIO (JAB_0..5). The protocol pins take PMOD A, ui_in takes PMOD B, and
 * uo_out[4:0] plus the host clock take the six GPIO. uo_out[5] (read-valid)
 * and the host's reset input use two servo-header signal pins; uo_out[7:6]
 * (fault, event/IRQ) are on LEDs only (both are also readable as status
 * over the host port).
 *
 * Clock build options (Verilog defines, set by fpga/scripts/build.sh):
 *   (default)      CLOCK=pll50  100 MHz oscillator -> MMCM -> 50 MHz core
 *                               clock; UART bridge; host_clk outputs the
 *                               core clock (ODDR) for an external host.
 *   PE_CLOCK_PLL40 CLOCK=pll40  as pll50 with MMCM x10 /25 = 40 MHz.
 *   PE_CLOCK_HOST  CLOCK=host   core clock from the host on host_clk (JAB_1,
 *                               a clock-capable pin); external host only.
 *
 * Switches: sw[0] host select (down = UART bridge, up = external pin host);
 * sw[1] up = deselect (ena low) when the external host is selected.
 * btn[0] resets the board logic. LED[7:0] status, LED[15:8] live levels of
 * the protocol pins uio[0..7].
 */

`default_nettype none

module pe_top_urbana (
    input  wire        clk_100mhz,  // N15
    input  wire [3:0]  btn,         // btn[0]: board reset (active high)
    input  wire [15:0] sw,
    output wire [15:0] led,
    input  wire        uart_rxd,    // from the FTDI (B16)
    output wire        uart_txd,    // to the FTDI (A16)
    inout  wire [7:0]  pmoda,       // PMOD A 1-4, 7-10: protocol pins uio[0..7]
    input  wire [7:0]  pmodb,       // PMOD B 1-4, 7-10: host -> ui_in[0..7]
    output wire [4:0]  jab_uo,      // JAB_0,2,3,4,5: uo_out[0..4]
`ifdef PE_CLOCK_HOST
    input  wire        host_clk,    // JAB_1 (C12, MRCC): core clock from the host
`else
    output wire        host_clk,    // JAB_1: core clock to the host
`endif
    output wire        servo_uo5,   // SERVO1 signal (L17): uo_out[5] read-valid
    input  wire        servo_rst    // SERVO0 signal (L18): host reset, active HIGH (pull-down)
);

`ifdef PE_CLOCK_HOST
  localparam integer BRIDGE   = 0;
  localparam integer CLK_HZ   = 50_000_000;  // nominal only: set by the host
  localparam [7:0]   CLOCK_ID = 8'd2;
`elsif PE_CLOCK_PLL40
  localparam integer BRIDGE   = 1;
  localparam integer CLK_HZ   = 40_000_000;
  localparam [7:0]   CLOCK_ID = 8'd3;
`else
  localparam integer BRIDGE   = 1;
  localparam integer CLK_HZ   = 50_000_000;
  localparam [7:0]   CLOCK_ID = 8'd0;
`endif

  // ---- clock -------------------------------------------------------------
  wire clk;
  wire locked;
`ifdef PE_CLOCK_HOST
  pe_fpga_clkgen #(.MODE("BUFG")) clkgen (.clk_in(host_clk), .clk_out(clk), .locked(locked));
`elsif PE_CLOCK_PLL40
  pe_fpga_clkgen #(
      .MODE("MMCM"), .CLKIN_PERIOD(10.0), .MULT(10), .DIVCLK(1), .DIV0(25)
  ) clkgen (.clk_in(clk_100mhz), .clk_out(clk), .locked(locked));
  pe_fpga_clkfwd clkfwd (.clk(clk), .q(host_clk));
`else
  pe_fpga_clkgen #(
      .MODE("MMCM"), .CLKIN_PERIOD(10.0), .MULT(10), .DIVCLK(1), .DIV0(20)
  ) clkgen (.clk_in(clk_100mhz), .clk_out(clk), .locked(locked));
  pe_fpga_clkfwd clkfwd (.clk(clk), .q(host_clk));
`endif

  // ---- protocol pins -----------------------------------------------------
  // keep: these names survive flattening, so the synthesized netlist can be
  // simulated with fpga/sim/tb_fpga_pins.v (FPGA_NETLIST=...).
  (* keep *) wire [7:0] uio_in;
  (* keep *) wire [7:0] uio_out;
  (* keep *) wire [7:0] uio_oe;
  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_pad
      assign pmoda[i] = uio_oe[i] ? uio_out[i] : 1'bz;
    end
  endgenerate
  assign uio_in = pmoda;

  // ---- shell -------------------------------------------------------------
  wire [7:0] uo;
  wire [7:0] status;
  pe_fpga_shell #(
      .BRIDGE  (BRIDGE),
      .CLK_HZ  (CLK_HZ),
      .BAUD    (1_000_000),
      .BOARD_ID(8'd2),
      .CLOCK_ID(CLOCK_ID)
  ) shell (
      .clk         (clk),
      .arst        (btn[0] | !locked),
      .host_ext_req(sw[0]),
      .ext_ui      (pmodb),
      .ext_uo      (uo),
      .ext_rst_n   (!servo_rst),
      .ext_ena     (!sw[1]),
      .uart_rx     (uart_rxd),
      .uart_tx     (uart_txd),
      .uio_pad_in  (uio_in),
      .uio_pad_out (uio_out),
      .uio_pad_oe  (uio_oe),
      .status      (status)
  );

  assign jab_uo    = uo[4:0];
  assign servo_uo5 = uo[5];
  assign led       = {uio_in, status};

  wire unused = &{1'b0, btn[3:1], sw[15:2]};

endmodule

`default_nettype wire
