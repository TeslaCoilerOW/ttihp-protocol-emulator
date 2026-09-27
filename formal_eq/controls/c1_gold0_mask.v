// SPDX-License-Identifier: Apache-2.0
// Recipe control c1 (formal_eq/README.md, "Recipe controls").
// pe-eq-control: expect=not_equivalent srams=0
// The gate differs from the gold only where the gold output bit is 0
// (gold a, gate a|b). A miter that ignores output bits where the gold value
// is 0 (e.g. -ignore_gold_x followed by setundef -zero) reports these two
// designs as equivalent; a sound recipe must not.
`default_nettype none
module tt_um_teslacoilerow_protocol_emulator (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  reg y;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) y <= 1'b0;
`ifdef PE_GATE
    else y <= ui_in[0] | ui_in[1];
`else
    else y <= ui_in[0];
`endif
  assign uo_out  = {7'b0, y};
  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;
  wire _unused = &{ena, uio_in, ui_in[7:2], 1'b0};
endmodule
