// SPDX-License-Identifier: Apache-2.0
// Recipe control c4 (formal_eq/README.md, "Recipe controls").
// pe-eq-control: expect=not_equivalent srams=0 min_frame=13
// A difference that needs a long input sequence: a free-running 4-bit
// counter; the gate also raises the output at count 13 when ui_in[0] is 1.
// The first mismatch is at least 13 cycles after reset.
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
  reg [3:0] cnt;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) cnt <= 4'd0;
    else cnt <= cnt + 4'd1;
`ifdef PE_GATE
  wire y = (cnt == 4'd11) | ((cnt == 4'd13) & ui_in[0]);
`else
  wire y = (cnt == 4'd11);
`endif
  assign uo_out  = {7'b0, y};
  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;
  wire _unused = &{ena, uio_in, ui_in[7:1], 1'b0};
endmodule
