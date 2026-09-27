// SPDX-License-Identifier: Apache-2.0
// Recipe control c9 (formal_eq/README.md, "Recipe controls").
// pe-eq-control: expect=error srams=0
// The gate's register samples on the falling clock edge. The AIG writer
// treats every flip-flop alike, so the recipe must refuse anything but
// rising-edge flip-flops (the miter script asserts that only AND, NOT and
// rising-edge D flip-flops remain).
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
`ifdef PE_GATE
  always @(negedge clk or negedge rst_n)
`else
  always @(posedge clk or negedge rst_n)
`endif
    if (!rst_n) y <= 1'b0;
    else y <= ui_in[0];
  assign uo_out  = {7'b0, y};
  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;
  wire _unused = &{ena, uio_in, ui_in[7:1], 1'b0};
endmodule
