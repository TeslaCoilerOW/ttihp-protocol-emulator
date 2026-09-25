/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * FPGA stand-in for one IHP RM_IHPSG13_1P_64x16_c2 single-port SRAM macro.
 *
 * Cycle behaviour is that of the IHP behavioral model
 * (models/RM_IHPSG13_1P_core_behavioral.v, SRAM_1P_behavioral), which the
 * core relies on:
 *
 *   on posedge clk, when men:
 *     wen & ren   write din at addr and load dout <= din  (write-through)
 *     wen & !ren  write din at addr, dout holds
 *     !wen & ren  dout <= mem[addr]                        (registered read)
 *     otherwise   nothing
 *   when !men     nothing (memory and dout hold)
 *
 * dout is a register: data read on edge N is visible after edge N and holds
 * until the next read or write-through. There is no read-during-write
 * conflict because a write never reads the array.
 *
 * Mapping: the array is asynchronous-read LUT RAM (64 deep x 16 bits: sixteen
 * 64x1 LUT RAMs, packed by Yosys into four RAM64M) and dout is a separate
 * fabric register with clock enable. Keeping the output register in fabric,
 * instead of letting a block RAM absorb it, keeps "write without read holds
 * dout" exact: a 7-series block RAM in WRITE_FIRST mode would update its
 * output on every enabled write.
 *
 * Differences from silicon, none of them visible to the core:
 *   - power-up contents: FPGA configuration initialises the array and dout
 *     to zero; the IHP macro powers up with unknown contents (X in the model).
 *     COMMIT only accepts a fully written image and execution is bounded by
 *     the committed length (docs/isa.md), so no unwritten word is executed.
 *   - A_DLY (read-timing trim) has no FPGA meaning; the core ties it high.
 *
 * Equivalence with SRAM_1P_behavioral #(16, 6) is proved in
 * fpga/formal/ (RTL and the Yosys synth_xilinx netlist of this module).
 */

`default_nettype none

module pe_fpga_sram_64x16 (
    input  wire        clk,
    input  wire        men,
    input  wire        wen,
    input  wire        ren,
    input  wire [5:0]  addr,
    input  wire [15:0] din,
    output wire [15:0] dout
);

  (* ram_style = "distributed" *)
  reg [15:0] mem [0:63];
  reg [15:0] dout_r;

  integer i;
  initial begin
    for (i = 0; i < 64; i = i + 1) mem[i] = 16'h0000;
    dout_r = 16'h0000;
  end

  // Write port.
  always @(posedge clk)
    if (men && wen) mem[addr] <= din;

  // Output register: write-through, registered read, or hold.
  always @(posedge clk)
    if (men) begin
      if (wen) begin
        if (ren) dout_r <= din;
      end else if (ren) begin
        dout_r <= mem[addr];
      end
    end

  assign dout = dout_r;

endmodule

`default_nettype wire
