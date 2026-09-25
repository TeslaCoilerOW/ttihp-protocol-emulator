/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Digilent Cmod A7-35T (xc7a35tcpg236-1) top for the protocol emulator.
 * Pin map: fpga/constraints/cmod_a7_35t.xdc, docs/fpga.md.
 *
 * Clock build options (Verilog defines, set by fpga/scripts/build.sh):
 *   (default)       CLOCK=pll50  12 MHz oscillator -> MMCM -> 50 MHz core
 *                                clock; UART bridge; host_clk outputs the
 *                                core clock (ODDR) for an external host.
 *   PE_CLOCK_PLL40  CLOCK=pll40  as pll50 with MMCM x50 /15 = 40 MHz (timing
 *                                margin on this part; firmware rates x0.8).
 *   PE_CLOCK_OSC    CLOCK=osc12  core runs on the 12 MHz oscillator directly
 *                                (no MMCM; firmware rates scale by 12/50).
 *   PE_CLOCK_HOST   CLOCK=host   core clock comes from the host on host_clk
 *                                (like the Tiny Tapeout demo board, where the
 *                                RP2040 clocks the chip); external pin host
 *                                only, no UART bridge.
 * PE_NO_PIN_HOST (with pll50/pll40/osc12; build.sh BRIDGE_ONLY=1): the DIP
 * host pins are left unused, so placement is not pulled towards them.
 */

`default_nettype none

module pe_top_cmod_a7 (
    input  wire       sysclk,      // 12 MHz oscillator (L17)
    input  wire [1:0] btn,         // btn[0]: board reset (active high)
    output wire [1:0] led,         // led[0] heartbeat, led[1] event/IRQ
    output wire       led0_r,      // RGB LED, active low: red = fault,
    output wire       led0_g,      //   green = UART traffic,
    output wire       led0_b,      //   blue = external pin host selected
    input  wire       uart_rxd,    // from the FTDI (J17)
    output wire       uart_txd,    // to the FTDI (J18)
    input  wire [7:0] ui_pin,      // host -> ui_in   (DIP 1-8)
    output wire [7:0] uo_pin,      // uo_out -> host  (DIP 9-14, 17, 18)
`ifdef PE_CLOCK_HOST
    input  wire       host_clk,    // DIP 46: core clock from the host
`else
    output wire       host_clk,    // DIP 46: core clock to the host
`endif
    input  wire       host_rst_n,  // DIP 47: rst_n from the host (pull-up)
    input  wire       host_ena,    // DIP 48: ena from the host (pull-up)
    input  wire       host_sel_n,  // DIP 45: 0 = external pin host, open = UART bridge
    inout  wire [7:0] ja           // Pmod JA 1-4, 7-10: protocol pins uio[0..7]
);

`ifdef PE_CLOCK_HOST
  localparam integer BRIDGE   = 0;
  localparam integer CLK_HZ   = 50_000_000;  // nominal only: set by the host
  localparam [7:0]   CLOCK_ID = 8'd2;
`elsif PE_CLOCK_OSC
  localparam integer BRIDGE   = 1;
  localparam integer CLK_HZ   = 12_000_000;
  localparam [7:0]   CLOCK_ID = 8'd1;
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
`elsif PE_CLOCK_OSC
  pe_fpga_clkgen #(.MODE("BUFG")) clkgen (.clk_in(sysclk), .clk_out(clk), .locked(locked));
  pe_fpga_clkfwd clkfwd (.clk(clk), .q(host_clk));
`elsif PE_CLOCK_PLL40
  pe_fpga_clkgen #(
      .MODE("MMCM"), .CLKIN_PERIOD(83.333), .MULT(50), .DIVCLK(1), .DIV0(15)
  ) clkgen (.clk_in(sysclk), .clk_out(clk), .locked(locked));
  pe_fpga_clkfwd clkfwd (.clk(clk), .q(host_clk));
`else
  pe_fpga_clkgen #(
      .MODE("MMCM"), .CLKIN_PERIOD(83.333), .MULT(50), .DIVCLK(1), .DIV0(12)
  ) clkgen (.clk_in(sysclk), .clk_out(clk), .locked(locked));
  pe_fpga_clkfwd clkfwd (.clk(clk), .q(host_clk));
`endif

  // ---- protocol pins: tri-state pads (IOBUF, T = ~uio_oe) ----------------
  // keep: these names survive flattening, so the synthesized netlist can be
  // simulated with fpga/sim/tb_fpga_pins.v (FPGA_NETLIST=...).
  (* keep *) wire [7:0] uio_in;
  (* keep *) wire [7:0] uio_out;
  (* keep *) wire [7:0] uio_oe;
  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_pad
      assign ja[i] = uio_oe[i] ? uio_out[i] : 1'bz;
    end
  endgenerate
  assign uio_in = ja;

  // ---- shell -------------------------------------------------------------
  wire [7:0] status;
  pe_fpga_shell #(
      .BRIDGE  (BRIDGE),
      .CLK_HZ  (CLK_HZ),
      .BAUD    (1_000_000),
      .BOARD_ID(8'd1),
      .CLOCK_ID(CLOCK_ID)
  ) shell (
      .clk         (clk),
      .arst        (btn[0] | !locked),
`ifdef PE_NO_PIN_HOST
      // UART bridge only: the DIP host pins are unused (uo_pin driven low).
      .host_ext_req(1'b0),
      .ext_ui      (8'd0),
      .ext_uo      (),
      .ext_rst_n   (1'b1),
      .ext_ena     (1'b1),
`else
      .host_ext_req(!host_sel_n),
      .ext_ui      (ui_pin),
      .ext_uo      (uo_pin),
      .ext_rst_n   (host_rst_n),
      .ext_ena     (host_ena),
`endif
      .uart_rx     (uart_rxd),
      .uart_tx     (uart_txd),
      .uio_pad_in  (uio_in),
      .uio_pad_out (uio_out),
      .uio_pad_oe  (uio_oe),
      .status      (status)
  );

  assign led[0] = status[0];
  assign led[1] = status[1];
  assign led0_r = !status[2];
  assign led0_g = !status[4];
  assign led0_b = !status[3];

`ifdef PE_NO_PIN_HOST
  assign uo_pin = 8'd0;
  wire unused_pins = &{1'b0, ui_pin, host_rst_n, host_ena, host_sel_n};
`endif

  wire unused_btn1 = btn[1];

endmodule

`default_nettype wire
