/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Behavioural RAMB36E1 for post-synthesis simulation of the capture unit's
 * record buffer (fpga/sim/Makefile, FPGA_NETLIST=...). Yosys' own
 * cells_sim.v RAMB36E1 carries timing only and drives no data, and AMD's
 * unisim model is not available here, so the Makefile drops Yosys' module
 * and uses this one.
 *
 * It models only the configurations Yosys emits for pe_fpga_scope.v and
 * stops the simulation ($fatal) on any other: RAM_MODE "TDP", no cascade,
 * port widths 0 (unused) or 9 (4,096 x 9: DI/DO[7:0] and the parity bit
 * DIP/DOP[0], address bits [14:3]), no output registers, WRITE_MODE
 * READ_FIRST, byte-write enables all equal. Each port reads the word stored
 * before a write in the same cycle (READ_FIRST); a read on one port of the
 * word the other port writes in the same cycle returns the old word, like
 * READ_FIRST in the same clock domain. Contents start unknown (x); INIT
 * values are accepted and ignored (the capture unit reads only words it has
 * written). Not a substitute for AMD's model: it checks the Yosys mapping
 * (address slicing, data and parity bits, bank multiplexing), not the
 * primitive.
 */

`timescale 1ns / 1ps

module RAMB36E1 #(
    parameter [255:0] INIT_00 = 256'h0,
    parameter [255:0] INIT_01 = 256'h0,
    parameter [255:0] INIT_02 = 256'h0,
    parameter [255:0] INIT_03 = 256'h0,
    parameter [255:0] INIT_04 = 256'h0,
    parameter [255:0] INIT_05 = 256'h0,
    parameter [255:0] INIT_06 = 256'h0,
    parameter [255:0] INIT_07 = 256'h0,
    parameter [255:0] INIT_08 = 256'h0,
    parameter [255:0] INIT_09 = 256'h0,
    parameter [255:0] INIT_0A = 256'h0,
    parameter [255:0] INIT_0B = 256'h0,
    parameter [255:0] INIT_0C = 256'h0,
    parameter [255:0] INIT_0D = 256'h0,
    parameter [255:0] INIT_0E = 256'h0,
    parameter [255:0] INIT_0F = 256'h0,
    parameter [255:0] INIT_10 = 256'h0,
    parameter [255:0] INIT_11 = 256'h0,
    parameter [255:0] INIT_12 = 256'h0,
    parameter [255:0] INIT_13 = 256'h0,
    parameter [255:0] INIT_14 = 256'h0,
    parameter [255:0] INIT_15 = 256'h0,
    parameter [255:0] INIT_16 = 256'h0,
    parameter [255:0] INIT_17 = 256'h0,
    parameter [255:0] INIT_18 = 256'h0,
    parameter [255:0] INIT_19 = 256'h0,
    parameter [255:0] INIT_1A = 256'h0,
    parameter [255:0] INIT_1B = 256'h0,
    parameter [255:0] INIT_1C = 256'h0,
    parameter [255:0] INIT_1D = 256'h0,
    parameter [255:0] INIT_1E = 256'h0,
    parameter [255:0] INIT_1F = 256'h0,
    parameter [255:0] INIT_20 = 256'h0,
    parameter [255:0] INIT_21 = 256'h0,
    parameter [255:0] INIT_22 = 256'h0,
    parameter [255:0] INIT_23 = 256'h0,
    parameter [255:0] INIT_24 = 256'h0,
    parameter [255:0] INIT_25 = 256'h0,
    parameter [255:0] INIT_26 = 256'h0,
    parameter [255:0] INIT_27 = 256'h0,
    parameter [255:0] INIT_28 = 256'h0,
    parameter [255:0] INIT_29 = 256'h0,
    parameter [255:0] INIT_2A = 256'h0,
    parameter [255:0] INIT_2B = 256'h0,
    parameter [255:0] INIT_2C = 256'h0,
    parameter [255:0] INIT_2D = 256'h0,
    parameter [255:0] INIT_2E = 256'h0,
    parameter [255:0] INIT_2F = 256'h0,
    parameter [255:0] INIT_30 = 256'h0,
    parameter [255:0] INIT_31 = 256'h0,
    parameter [255:0] INIT_32 = 256'h0,
    parameter [255:0] INIT_33 = 256'h0,
    parameter [255:0] INIT_34 = 256'h0,
    parameter [255:0] INIT_35 = 256'h0,
    parameter [255:0] INIT_36 = 256'h0,
    parameter [255:0] INIT_37 = 256'h0,
    parameter [255:0] INIT_38 = 256'h0,
    parameter [255:0] INIT_39 = 256'h0,
    parameter [255:0] INIT_3A = 256'h0,
    parameter [255:0] INIT_3B = 256'h0,
    parameter [255:0] INIT_3C = 256'h0,
    parameter [255:0] INIT_3D = 256'h0,
    parameter [255:0] INIT_3E = 256'h0,
    parameter [255:0] INIT_3F = 256'h0,
    parameter [255:0] INIT_40 = 256'h0,
    parameter [255:0] INIT_41 = 256'h0,
    parameter [255:0] INIT_42 = 256'h0,
    parameter [255:0] INIT_43 = 256'h0,
    parameter [255:0] INIT_44 = 256'h0,
    parameter [255:0] INIT_45 = 256'h0,
    parameter [255:0] INIT_46 = 256'h0,
    parameter [255:0] INIT_47 = 256'h0,
    parameter [255:0] INIT_48 = 256'h0,
    parameter [255:0] INIT_49 = 256'h0,
    parameter [255:0] INIT_4A = 256'h0,
    parameter [255:0] INIT_4B = 256'h0,
    parameter [255:0] INIT_4C = 256'h0,
    parameter [255:0] INIT_4D = 256'h0,
    parameter [255:0] INIT_4E = 256'h0,
    parameter [255:0] INIT_4F = 256'h0,
    parameter [255:0] INIT_50 = 256'h0,
    parameter [255:0] INIT_51 = 256'h0,
    parameter [255:0] INIT_52 = 256'h0,
    parameter [255:0] INIT_53 = 256'h0,
    parameter [255:0] INIT_54 = 256'h0,
    parameter [255:0] INIT_55 = 256'h0,
    parameter [255:0] INIT_56 = 256'h0,
    parameter [255:0] INIT_57 = 256'h0,
    parameter [255:0] INIT_58 = 256'h0,
    parameter [255:0] INIT_59 = 256'h0,
    parameter [255:0] INIT_5A = 256'h0,
    parameter [255:0] INIT_5B = 256'h0,
    parameter [255:0] INIT_5C = 256'h0,
    parameter [255:0] INIT_5D = 256'h0,
    parameter [255:0] INIT_5E = 256'h0,
    parameter [255:0] INIT_5F = 256'h0,
    parameter [255:0] INIT_60 = 256'h0,
    parameter [255:0] INIT_61 = 256'h0,
    parameter [255:0] INIT_62 = 256'h0,
    parameter [255:0] INIT_63 = 256'h0,
    parameter [255:0] INIT_64 = 256'h0,
    parameter [255:0] INIT_65 = 256'h0,
    parameter [255:0] INIT_66 = 256'h0,
    parameter [255:0] INIT_67 = 256'h0,
    parameter [255:0] INIT_68 = 256'h0,
    parameter [255:0] INIT_69 = 256'h0,
    parameter [255:0] INIT_6A = 256'h0,
    parameter [255:0] INIT_6B = 256'h0,
    parameter [255:0] INIT_6C = 256'h0,
    parameter [255:0] INIT_6D = 256'h0,
    parameter [255:0] INIT_6E = 256'h0,
    parameter [255:0] INIT_6F = 256'h0,
    parameter [255:0] INIT_70 = 256'h0,
    parameter [255:0] INIT_71 = 256'h0,
    parameter [255:0] INIT_72 = 256'h0,
    parameter [255:0] INIT_73 = 256'h0,
    parameter [255:0] INIT_74 = 256'h0,
    parameter [255:0] INIT_75 = 256'h0,
    parameter [255:0] INIT_76 = 256'h0,
    parameter [255:0] INIT_77 = 256'h0,
    parameter [255:0] INIT_78 = 256'h0,
    parameter [255:0] INIT_79 = 256'h0,
    parameter [255:0] INIT_7A = 256'h0,
    parameter [255:0] INIT_7B = 256'h0,
    parameter [255:0] INIT_7C = 256'h0,
    parameter [255:0] INIT_7D = 256'h0,
    parameter [255:0] INIT_7E = 256'h0,
    parameter [255:0] INIT_7F = 256'h0,
    parameter [255:0] INITP_00 = 256'h0,
    parameter [255:0] INITP_01 = 256'h0,
    parameter [255:0] INITP_02 = 256'h0,
    parameter [255:0] INITP_03 = 256'h0,
    parameter [255:0] INITP_04 = 256'h0,
    parameter [255:0] INITP_05 = 256'h0,
    parameter [255:0] INITP_06 = 256'h0,
    parameter [255:0] INITP_07 = 256'h0,
    parameter [255:0] INITP_08 = 256'h0,
    parameter [255:0] INITP_09 = 256'h0,
    parameter [255:0] INITP_0A = 256'h0,
    parameter [255:0] INITP_0B = 256'h0,
    parameter [255:0] INITP_0C = 256'h0,
    parameter [255:0] INITP_0D = 256'h0,
    parameter [255:0] INITP_0E = 256'h0,
    parameter [255:0] INITP_0F = 256'h0,
    parameter integer DOA_REG = 0,
    parameter integer DOB_REG = 0,
    parameter [35:0] INIT_A = 36'h0,
    parameter [35:0] INIT_B = 36'h0,
    parameter INIT_FILE = "NONE",
    parameter RAM_EXTENSION_A = "NONE",
    parameter RAM_EXTENSION_B = "NONE",
    parameter RAM_MODE = "TDP",
    parameter RDADDR_COLLISION_HWCONFIG = "DELAYED_WRITE",
    parameter integer READ_WIDTH_A = 0,
    parameter integer READ_WIDTH_B = 0,
    parameter RSTREG_PRIORITY_A = "RSTREG",
    parameter RSTREG_PRIORITY_B = "RSTREG",
    parameter SIM_COLLISION_CHECK = "ALL",
    parameter SIM_DEVICE = "7SERIES",
    parameter [71:0] SRVAL_A = 72'h0,
    parameter [71:0] SRVAL_B = 72'h0,
    parameter WRITE_MODE_A = "WRITE_FIRST",
    parameter WRITE_MODE_B = "WRITE_FIRST",
    parameter integer WRITE_WIDTH_A = 0,
    parameter integer WRITE_WIDTH_B = 0,
    parameter EN_ECC_READ = "FALSE",
    parameter EN_ECC_WRITE = "FALSE",
    parameter [0:0] IS_CLKARDCLK_INVERTED = 1'b0,
    parameter [0:0] IS_CLKBWRCLK_INVERTED = 1'b0,
    parameter [0:0] IS_ENARDEN_INVERTED = 1'b0,
    parameter [0:0] IS_ENBWREN_INVERTED = 1'b0,
    parameter [0:0] IS_RSTRAMARSTRAM_INVERTED = 1'b0,
    parameter [0:0] IS_RSTRAMB_INVERTED = 1'b0,
    parameter [0:0] IS_RSTREGARSTREG_INVERTED = 1'b0,
    parameter [0:0] IS_RSTREGB_INVERTED = 1'b0
) (
    output wire        CASCADEOUTA,
    output wire        CASCADEOUTB,
    output wire        DBITERR,
    output reg  [31:0] DOADO = 32'd0,
    output reg  [31:0] DOBDO = 32'd0,
    output reg  [3:0]  DOPADOP = 4'd0,
    output reg  [3:0]  DOPBDOP = 4'd0,
    output wire [7:0]  ECCPARITY,
    output wire [8:0]  RDADDRECC,
    output wire        SBITERR,
    input  wire [15:0] ADDRARDADDR,
    input  wire [15:0] ADDRBWRADDR,
    input  wire        CASCADEINA,
    input  wire        CASCADEINB,
    input  wire        CLKARDCLK,
    input  wire        CLKBWRCLK,
    input  wire [31:0] DIADI,
    input  wire [31:0] DIBDI,
    input  wire [3:0]  DIPADIP,
    input  wire [3:0]  DIPBDIP,
    input  wire        ENARDEN,
    input  wire        ENBWREN,
    input  wire        INJECTDBITERR,
    input  wire        INJECTSBITERR,
    input  wire        REGCEAREGCE,
    input  wire        REGCEB,
    input  wire        RSTRAMARSTRAM,
    input  wire        RSTRAMB,
    input  wire        RSTREGARSTREG,
    input  wire        RSTREGB,
    input  wire [3:0]  WEA,
    input  wire [7:0]  WEBWE
);
  assign CASCADEOUTA = 1'b0;
  assign CASCADEOUTB = 1'b0;
  assign DBITERR     = 1'b0;
  assign SBITERR     = 1'b0;
  assign ECCPARITY   = 8'd0;
  assign RDADDRECC   = 9'd0;

  initial begin
    if (RAM_MODE != "TDP" || RAM_EXTENSION_A != "NONE" || RAM_EXTENSION_B != "NONE"
        || DOA_REG != 0 || DOB_REG != 0
        || !(READ_WIDTH_A == 0 || READ_WIDTH_A == 9) || !(READ_WIDTH_B == 0 || READ_WIDTH_B == 9)
        || !(WRITE_WIDTH_A == 0 || WRITE_WIDTH_A == 9) || !(WRITE_WIDTH_B == 0 || WRITE_WIDTH_B == 9)
        || (WRITE_WIDTH_A == 9 && WRITE_MODE_A != "READ_FIRST")
        || (WRITE_WIDTH_B == 9 && WRITE_MODE_B != "READ_FIRST")
        || IS_CLKARDCLK_INVERTED || IS_CLKBWRCLK_INVERTED || IS_ENARDEN_INVERTED || IS_ENBWREN_INVERTED)
      $fatal(1, "RAMB36E1 %m: configuration outside fpga/sim/netlist_bram.v's model");
  end

  reg [8:0] mem [0:4095];
  wire [11:0] aa = ADDRARDADDR[14:3];
  wire [11:0] ab = ADDRBWRADDR[14:3];

  always @(posedge CLKARDCLK) begin
    if (ENARDEN) begin
      if (WRITE_WIDTH_A == 9 && WEA != 4'h0 && WEA != 4'hF)
        $fatal(1, "RAMB36E1 %m: unequal WEA bits");
      if (READ_WIDTH_A == 9) begin
        DOADO[7:0] <= mem[aa][7:0];
        DOPADOP[0] <= mem[aa][8];
      end
      if (WRITE_WIDTH_A == 9 && WEA[0]) mem[aa] <= {DIPADIP[0], DIADI[7:0]};
    end
  end

  always @(posedge CLKBWRCLK) begin
    if (ENBWREN) begin
      if (WRITE_WIDTH_B == 9 && WEBWE[3:0] != 4'h0 && WEBWE[3:0] != 4'hF)
        $fatal(1, "RAMB36E1 %m: unequal WEBWE bits");
      if (READ_WIDTH_B == 9) begin
        DOBDO[7:0] <= mem[ab][7:0];
        DOPBDOP[0] <= mem[ab][8];
      end
      if (WRITE_WIDTH_B == 9 && WEBWE[0]) mem[ab] <= {DIPBDIP[0], DIBDI[7:0]};
    end
  end
endmodule
