// Interface-only (blackbox) view of the IHP RM_IHPSG13_1P_64x16_c2 SRAM macro,
// for lint and synthesis-hierarchy checks. Never simulate this file: use
// ../RM_IHPSG13_1P_64x16_c2.v + ../RM_IHPSG13_1P_core_behavioral.v with
// +define+FUNCTIONAL instead.
//
// Port list derived from the IHP-Open-PDK wrapper
// ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/verilog/RM_IHPSG13_1P_64x16_c2.v
// (revision 2bbec755dc67ca3db0261c3d6163e15735d66710).
// Copyright 2025 IHP PDK Authors. SPDX-License-Identifier: Apache-2.0
// See ../LICENSE.IHP-Open-PDK and ../NOTICE.IHP-Open-PDK.

(* blackbox *)
module RM_IHPSG13_1P_64x16_c2 (
    input  wire        A_CLK,
    input  wire        A_MEN,
    input  wire        A_WEN,
    input  wire        A_REN,
    input  wire [5:0]  A_ADDR,
    input  wire [15:0] A_DIN,
    input  wire        A_DLY,
    output wire [15:0] A_DOUT
);
endmodule
