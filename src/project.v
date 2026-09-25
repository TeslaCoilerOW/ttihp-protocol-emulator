/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Tiny Tapeout top for the four-engine programmable protocol processor.
 * All logic lives in protocol_emulator_core, which is generated from the
 * Hardcaml sources in hardcaml/ by scripts/generate.sh. Do not hand-edit it.
 *
 * Host port: ui[3:0] write nibble, ui[4] write-valid, ui[5] read-ready,
 * ui[7:6] window; uo[3:0] read nibble, uo[4] write-ready, uo[5] read-valid,
 * uo[6] event/IRQ, uo[7] fault. uio[7:0] are the programmable protocol pins.
 * The core folds ~ena into its synchronous clear, so ena is consumed there.
 */

`default_nettype none

module tt_um_teslacoilerow_protocol_emulator (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  protocol_emulator_core core (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

endmodule

`default_nettype wire
