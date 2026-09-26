/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal simulation models for the two Xilinx primitives of the pll50 /
 * pll40 / osc12 netlists that Yosys' cells_sim.v leaves as black boxes, so
 * that post-synthesis simulation (make FPGA_NETLIST=...) also covers those
 * builds:
 *   MMCME2_ADV  CLKOUT0 and CLKFBOUT follow CLKIN1, LOCKED = 1 (the MMCM
 *               itself is not simulated, as in the RTL simulation, where
 *               pe_fpga_clkgen is a wire under PE_FPGA_SIM)
 *   ODDR        SAME_EDGE, as used by pe_fpga_clkfwd: Q = D1 after a rising
 *               edge, D2 after a falling edge
 * Parameters are accepted and ignored.
 */

`timescale 1ns / 1ps

module MMCME2_ADV #(
    parameter BANDWIDTH = "OPTIMIZED",
    parameter real CLKFBOUT_MULT_F = 5.0,
    parameter real CLKFBOUT_PHASE = 0.0,
    parameter real CLKIN1_PERIOD = 0.0,
    parameter real CLKIN2_PERIOD = 0.0,
    parameter real CLKOUT0_DIVIDE_F = 1.0,
    parameter real CLKOUT0_DUTY_CYCLE = 0.5,
    parameter real CLKOUT0_PHASE = 0.0,
    parameter integer DIVCLK_DIVIDE = 1,
    parameter real REF_JITTER1 = 0.0,
    parameter STARTUP_WAIT = "FALSE",
    parameter COMPENSATION = "ZHOLD"
) (
    output wire        CLKFBOUT,
    output wire        CLKFBOUTB,
    output wire        CLKFBSTOPPED,
    output wire        CLKINSTOPPED,
    output wire        CLKOUT0,
    output wire        CLKOUT0B,
    output wire        CLKOUT1,
    output wire        CLKOUT1B,
    output wire        CLKOUT2,
    output wire        CLKOUT2B,
    output wire        CLKOUT3,
    output wire        CLKOUT3B,
    output wire        CLKOUT4,
    output wire        CLKOUT5,
    output wire        CLKOUT6,
    output wire [15:0] DO,
    output wire        DRDY,
    output wire        LOCKED,
    output wire        PSDONE,
    input  wire        CLKFBIN,
    input  wire        CLKIN1,
    input  wire        CLKIN2,
    input  wire        CLKINSEL,
    input  wire [6:0]  DADDR,
    input  wire        DCLK,
    input  wire        DEN,
    input  wire [15:0] DI,
    input  wire        DWE,
    input  wire        PSCLK,
    input  wire        PSEN,
    input  wire        PSINCDEC,
    input  wire        PWRDWN,
    input  wire        RST
);
  assign CLKOUT0 = CLKIN1;
  assign CLKFBOUT = CLKIN1;
  assign {CLKFBOUTB, CLKOUT0B, CLKOUT1, CLKOUT1B, CLKOUT2, CLKOUT2B, CLKOUT3, CLKOUT3B, CLKOUT4, CLKOUT5, CLKOUT6} = 11'd0;
  assign {CLKFBSTOPPED, CLKINSTOPPED, DRDY, PSDONE} = 4'd0;
  assign DO = 16'd0;
  assign LOCKED = 1'b1;
endmodule

module ODDR #(
    parameter DDR_CLK_EDGE = "OPPOSITE_EDGE",
    parameter INIT = 1'b0,
    parameter SRTYPE = "SYNC",
    parameter IS_C_INVERTED = 1'b0,
    parameter IS_D1_INVERTED = 1'b0,
    parameter IS_D2_INVERTED = 1'b0
) (
    output reg  Q,
    input  wire C,
    input  wire CE,
    input  wire D1,
    input  wire D2,
    input  wire R,
    input  wire S
);
  initial Q = INIT;
  always @(posedge C) if (CE) Q <= D1;
  always @(negedge C) if (CE) Q <= D2;
endmodule
