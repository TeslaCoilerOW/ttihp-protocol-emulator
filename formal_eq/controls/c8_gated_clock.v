// SPDX-License-Identifier: Apache-2.0
// Recipe control c8 (formal_eq/README.md, "Recipe controls").
// pe-eq-control: expect=error srams=0
// The gate clocks its register with clk & ui_in[7] (a gated clock). The
// AIG has one implicit clock and would silently drop the gating, so the
// recipe must refuse such a netlist (the miter script asserts that every
// flip-flop is clocked by clk) instead of calling it equivalent.
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
`ifdef PE_GATE
  wire ck = clk & ui_in[7];
`else
  wire ck = clk;
`endif
  reg y;
  always @(posedge ck or negedge rst_n)
    if (!rst_n) y <= 1'b0;
    else y <= ui_in[0];
  assign uo_out  = {7'b0, y};
  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;
  wire _unused = &{ena, uio_in, ui_in[6:1], 1'b0};
endmodule
