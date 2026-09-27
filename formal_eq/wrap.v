// SPDX-License-Identifier: Apache-2.0
// Equivalence-check wrapper (formal_eq/README.md, "Method"). The same file
// wraps the RTL (gold) and the gate-level netlist (gate). The design sees
// rst_n held low in the first cycle, so both sides start from their reset
// state whatever their flip-flop encoding (a register that resets to 1 in the
// RTL may be an inverted flip-flop that resets to 0 in the netlist). After the
// first cycle rst_n is the free input again.
module pe_eq_wrap (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  reg started = 1'b0;
  always @(posedge clk) started <= 1'b1;
  tt_um_teslacoilerow_protocol_emulator dut (
      .ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in), .uio_out(uio_out),
      .uio_oe(uio_oe), .ena(ena), .clk(clk), .rst_n(rst_n & started));
endmodule
