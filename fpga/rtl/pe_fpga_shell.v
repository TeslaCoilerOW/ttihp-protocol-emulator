/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Board-independent FPGA shell around the unchanged Tiny Tapeout top
 * tt_um_teslacoilerow_protocol_emulator (src/project.v).
 *
 * Host port owner (host_ext):
 *   0  UART bridge (pe_uart_host_bridge): a PC on the board's USB-UART
 *      drives ui_in / reads uo_out / controls rst_n and ena.
 *   1  external pin host (Pico, logic analyser, another FPGA): ext_ui,
 *      ext_rst_n and ext_ena go to the TT ports directly, with no
 *      synchronizer, exactly like the chip's pins (the host is synchronous to
 *      clk, docs/isa.md). ext_uo always carries uo_out.
 * host_ext_req is a strap/switch and is synchronized here. With BRIDGE = 0
 * (host-clocked builds) the external host always owns the port.
 *
 * Reset: arst (button, clock not locked) is asserted asynchronously and
 * released synchronously; it resets the bridge and holds the core's rst_n
 * low. The protocol pins go straight from the core to the pad buffers
 * (uio_out, uio_oe active high) and from the pads to uio_in; the core's own
 * two-flop input synchronizers are the only ones, as on silicon.
 */

`default_nettype none

module pe_fpga_shell #(
    parameter integer BRIDGE   = 1,
    parameter integer CLK_HZ   = 50_000_000,
    parameter integer BAUD     = 1_000_000,
    parameter [7:0]   BOARD_ID = 8'h00,
    parameter [7:0]   CLOCK_ID = 8'h00
) (
    input  wire       clk,
    input  wire       arst,
    input  wire       host_ext_req,
    input  wire [7:0] ext_ui,
    output wire [7:0] ext_uo,
    input  wire       ext_rst_n,
    input  wire       ext_ena,
    input  wire       uart_rx,
    output wire       uart_tx,
    input  wire [7:0] uio_pad_in,
    output wire [7:0] uio_pad_out,
    output wire [7:0] uio_pad_oe,
    output wire [7:0] status
);

  // ---- board reset: async assert, sync release ----------------------------
  (* ASYNC_REG = "TRUE" *) reg [2:0] rst_sync = 3'b000;
  always @(posedge clk or posedge arst)
    if (arst) rst_sync <= 3'b000;
    else rst_sync <= {rst_sync[1:0], 1'b1};
  wire board_rst_n = rst_sync[2];

  // ---- host owner select --------------------------------------------------
  wire host_ext;
  generate
    if (BRIDGE != 0) begin : g_sel
      (* ASYNC_REG = "TRUE" *) reg [1:0] sel_sync = 2'b00;
      always @(posedge clk) sel_sync <= {sel_sync[0], host_ext_req};
      assign host_ext = sel_sync[1];
    end else begin : g_nosel
      assign host_ext = 1'b1;
    end
  endgenerate

  // ---- the design ----------------------------------------------------------
  wire [7:0] ui;
  wire [7:0] uo;
  wire       tt_rst_n;
  wire       tt_ena;

  tt_um_teslacoilerow_protocol_emulator tt (
      .ui_in  (ui),
      .uo_out (uo),
      .uio_in (uio_pad_in),
      .uio_out(uio_pad_out),
      .uio_oe (uio_pad_oe),
      .ena    (tt_ena),
      .clk    (clk),
      .rst_n  (tt_rst_n)
  );

  assign ext_uo = uo;

  // ---- UART bridge ---------------------------------------------------------
  wire [7:0] br_ui;
  wire       br_rst_n;
  wire       br_ena;
  wire       br_activity;
  generate
    if (BRIDGE != 0) begin : g_bridge
      pe_uart_host_bridge #(
          .CLK_HZ  (CLK_HZ),
          .BAUD    (BAUD),
          .BOARD_ID(BOARD_ID),
          .CLOCK_ID(CLOCK_ID)
      ) bridge (
          .clk       (clk),
          .rst       (!board_rst_n),
          .enable    (!host_ext),
          .uart_rx   (uart_rx),
          .uart_tx   (uart_tx),
          .ui        (br_ui),
          .uo        (uo),
          .core_rst_n(br_rst_n),
          .core_ena  (br_ena),
          .uio_pad   (uio_pad_in),
          .uio_out   (uio_pad_out),
          .uio_oe    (uio_pad_oe),
          .activity  (br_activity)
      );
    end else begin : g_nobridge
      assign br_ui       = 8'd0;
      assign br_rst_n    = 1'b1;
      assign br_ena      = 1'b1;
      assign br_activity = 1'b0;
      assign uart_tx     = 1'b1;
    end
  endgenerate

  assign ui       = host_ext ? ext_ui : br_ui;
  assign tt_rst_n = board_rst_n & (host_ext ? ext_rst_n : br_rst_n);
  assign tt_ena   = host_ext ? ext_ena : br_ena;

  // ---- status (LEDs) -------------------------------------------------------
  reg [25:0] beat = 26'd0;
  always @(posedge clk) beat <= beat + 26'd1;

  assign status = {tt_rst_n,          // 7 core out of reset
                   |uio_pad_oe,       // 6 some protocol pin driven
                   board_rst_n,       // 5 clock locked, button released
                   br_activity,       // 4 UART traffic
                   host_ext,          // 3 external pin host owns the port
                   uo[7],             // 2 fault
                   uo[6],             // 1 event / IRQ
                   beat[25]};         // 0 heartbeat (~0.75 Hz at 50 MHz)

endmodule

`default_nettype wire
