// SPDX-License-Identifier: Apache-2.0
// Recipe control c5 (formal_eq/README.md, "Recipe controls").
// pe-eq-control: expect=equivalent srams=1
// Both sides drive one SRAM macro with the same inputs (the gate through
// double inversions and a clock buffer). The macro is a cut point: its inputs
// are compared, its read data is one free input shared by both sides.
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
  wire [15:0] dout;
  wire [7:0] rd;
  reg  [7:0] q;
  // the read data reaches uo_out through a reset flip-flop (a sequential miter)
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 8'd0;
    else q <= rd;
  assign uo_out = q;
`ifdef PE_GATE
  wire ck = clk;
  RM_IHPSG13_1P_64x16_c2 mem (
      .A_CLK(ck), .A_MEN(1'b1), .A_WEN(ui_in[6]), .A_REN(ui_in[7]),
      .A_ADDR(ui_in[5:0]), .A_DIN({uio_in, ~(~ui_in)}), .A_DLY(1'b1), .A_DOUT(dout));
  assign rd = ~(~dout[7:0]);
`else
  RM_IHPSG13_1P_64x16_c2 mem (
      .A_CLK(clk), .A_MEN(1'b1), .A_WEN(ui_in[6]), .A_REN(ui_in[7]),
      .A_ADDR(ui_in[5:0]), .A_DIN({uio_in, ui_in}), .A_DLY(1'b1), .A_DOUT(dout));
  assign rd = dout[7:0];
`endif
  assign uio_out = dout[15:8];
  assign uio_oe  = 8'hff;
  wire _unused = &{ena, 1'b0};
endmodule
