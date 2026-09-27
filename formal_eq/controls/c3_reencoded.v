// SPDX-License-Identifier: Apache-2.0
// Recipe control c3 (formal_eq/README.md, "Recipe controls").
// pe-eq-control: expect=equivalent srams=0
// Equal behaviour, different state encoding: the gate stores the inverted
// counter and resets it to 4'hf. Every flip-flop starts at 0 in the miter, so
// the two sides only agree because the wrapper (formal_eq/wrap.v) holds
// rst_n low in the first cycle. A recipe that does not reset both sides, or
// that matches registers by name or value, fails this control.
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
  reg [3:0] ncnt;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) ncnt <= 4'hf;
    else if (ui_in[0]) ncnt <= ~((~ncnt) + 4'd1);
  wire [3:0] cnt = ~ncnt;
  assign uo_out = {3'b0, ~(cnt != 4'd9), cnt};
`else
  reg [3:0] cnt;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) cnt <= 4'd0;
    else if (ui_in[0]) cnt <= cnt + 4'd1;
  assign uo_out = {3'b0, cnt == 4'd9, cnt};
`endif
  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;
  wire _unused = &{ena, uio_in, ui_in[7:1], 1'b0};
endmodule
