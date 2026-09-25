/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * FPGA drop-in for the IHP SRAM macro. It has the macro's module name and
 * ports, so src/protocol_emulator_core.v (generated, never edited) builds
 * unchanged for the FPGA: compile this file INSTEAD of
 * models/RM_IHPSG13_1P_64x16_c2.v + models/RM_IHPSG13_1P_core_behavioral.v.
 * The storage and its timing are in pe_fpga_sram_64x16.v.
 */

`default_nettype none

module RM_IHPSG13_1P_64x16_c2 (
    input  wire        A_CLK,
    input  wire        A_MEN,
    input  wire        A_WEN,
    input  wire        A_REN,
    input  wire [5:0]  A_ADDR,
    input  wire [15:0] A_DIN,
    input  wire        A_DLY,   // read-timing trim; tied high by the core, unused on FPGA
    output wire [15:0] A_DOUT
);

  pe_fpga_sram_64x16 sram (
      .clk (A_CLK),
      .men (A_MEN),
      .wen (A_WEN),
      .ren (A_REN),
      .addr(A_ADDR),
      .din (A_DIN),
      .dout(A_DOUT)
  );

`ifndef SYNTHESIS
  // Same guard as the IHP model: the macro requires A_DLY = 1.
  always @(A_DLY)
    if (A_DLY !== 1'b1 && $time > 0)
      $display("%m ERROR: A_DLY must be 1 (observed %b) at %0t", A_DLY, $time);
`endif

  wire unused_dly = A_DLY;

endmodule

`default_nettype wire
