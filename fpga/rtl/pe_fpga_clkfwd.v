/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Forward the core clock to a pin (ODDR, D1 = 1, D2 = 0) so that an external
 * synchronous host can follow it. The pin rises with the core's rising edge
 * (plus the output delay).
 */

`default_nettype none

module pe_fpga_clkfwd (
    input  wire clk,
    output wire q
);
`ifdef PE_FPGA_SIM
  assign q = clk;
`else
  ODDR #(
      .DDR_CLK_EDGE("SAME_EDGE"),
      .INIT        (1'b0),
      .SRTYPE      ("SYNC")
  ) oddr (
      .Q (q),
      .C (clk),
      .CE(1'b1),
      .D1(1'b1),
      .D2(1'b0),
      .R (1'b0),
      .S (1'b0)
  );
`endif
endmodule

`default_nettype wire
