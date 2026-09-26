/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Second simulation root for demo/sim: dumps only the board-level signals a
 * logic analyser would see to the VCD named by +demo_vcd=PATH. The FPGA
 * testbenches' own dumps are disabled (NO_WAVES).
 *
 *   pins testbench (host-clock build):  clk (the host clock pin), rst_n,
 *                                       pads (protocol Pmod), ui_in, uo_out
 *   bridge testbench (UART bridge):     clk (board oscillator), pads,
 *                                       uart_rx/uart_tx (USB-UART), and the
 *                                       core's ui_in/uo_out (inside the FPGA,
 *                                       for the load evidence only)
 */

`timescale 1ns / 1ps

module demo_dump ();
  reg [8*512-1:0] path;
  initial begin
    if ($value$plusargs("demo_vcd=%s", path)) begin
      $dumpfile(path);
`ifdef DEMO_BRIDGE
      $dumpvars(0, tb_bridge.clk, tb_bridge.pads, tb_bridge.uart_rx, tb_bridge.uart_tx,
                tb_bridge.dut.shell.tt.ui_in, tb_bridge.dut.shell.tt.uo_out);
`else
      $dumpvars(0, tb.clk, tb.rst_n, tb.pads, tb.ui_in, tb.uo_out);
`endif
    end
  end
endmodule
