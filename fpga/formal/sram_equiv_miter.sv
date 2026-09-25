/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Equivalence miter: IHP SRAM behavioral model vs FPGA SRAM stand-in.
 *
 *   ref : SRAM_1P_behavioral #(16, 6)  (models/RM_IHPSG13_1P_core_behavioral.v,
 *         the model RM_IHPSG13_1P_64x16_c2 uses under FUNCTIONAL)
 *   dut : pe_fpga_sram_64x16 (fpga/rtl/, as RTL or as the Yosys
 *         synth_xilinx netlist simulated with Yosys' Xilinx cell models)
 *
 * Both are driven by the same unconstrained inputs every cycle (men, wen,
 * ren, addr, din are free; A_DLY is tied high, as the core ties it).
 *
 * The IHP model powers up with arbitrary array and output contents (its
 * registers have no initial value, so the solver may pick any), while the
 * FPGA array and output register start at zero. The property therefore
 * compares the outputs whenever the reference output is DEFINED by the
 * history: `known[a]` records that address a has been written, and
 * `dout_known` that the reference output was last loaded from din
 * (write-through) or from a written address. Whenever dout_known holds, the
 * two outputs must be equal. With PDR this is an unbounded proof over all
 * input sequences and all reference power-up states.
 *
 * Cover properties show that each behaviour of the port is reachable
 * (write-through, write-hold, read of a written word, men-low hold).
 */

`default_nettype none

module sram_equiv_miter (
    input wire        clk,
    input wire        men,
    input wire        wen,
    input wire        ren,
    input wire [5:0]  addr,
    input wire [15:0] din
);

  wire [15:0] q_ref;
  wire [15:0] q_dut;

  SRAM_1P_behavioral #(
      .P_DATA_WIDTH(16),
      .P_ADDR_WIDTH(6)
  ) ref_model (
      .A_CLK (clk),
      .A_MEN (men),
      .A_WEN (wen),
      .A_REN (ren),
      .A_ADDR(addr),
      .A_DLY (1'b1),
      .A_DIN (din),
      .A_DOUT(q_ref)
  );

  pe_fpga_sram_64x16 dut (
      .clk (clk),
      .men (men),
      .wen (wen),
      .ren (ren),
      .addr(addr),
      .din (din),
      .dout(q_dut)
  );

  // Definedness tracking of the reference output.
  reg [63:0] known = 64'd0;
  reg        dout_known = 1'b0;
  always @(posedge clk) begin
    if (men && wen) begin
      known[addr] <= 1'b1;
      if (ren) dout_known <= 1'b1;
    end else if (men && ren) begin
      dout_known <= known[addr];
    end
  end

  always @(*) begin
    if (dout_known) assert (q_dut == q_ref);
  end

  // Reachability of each port behaviour with a defined, checked output.
  reg past_valid = 1'b0;
  reg p_men = 1'b0, p_wen = 1'b0, p_ren = 1'b0;
  reg [15:0] p_q = 16'd0;
  always @(posedge clk) begin
    past_valid <= 1'b1;
    p_men <= men; p_wen <= wen; p_ren <= ren; p_q <= q_dut;
  end
  always @(*) begin
    if (past_valid) begin
      // write-through: output shows the written word
      cover (dout_known && p_men && p_wen && p_ren && q_dut == 16'hA5C3);
      // write without read: output holds a different, defined value
      cover (dout_known && p_men && p_wen && !p_ren && q_dut == p_q && q_dut == 16'h1234);
      // registered read of a previously written word
      cover (dout_known && p_men && !p_wen && p_ren && q_dut == 16'h5A5A);
      // men low: nothing changes even with wen/ren high
      cover (dout_known && !p_men && p_wen && p_ren && q_dut == p_q && q_dut == 16'h0F0F);
    end
  end

endmodule

`default_nettype wire
