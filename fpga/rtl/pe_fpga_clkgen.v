/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * Core clock generation for the FPGA prototype.
 *
 * MODE "MMCM": board oscillator -> MMCME2_ADV CLKOUT0 -> BUFG.
 *   f_out = f_in * MULT / (DIVCLK * DIV0), VCO = f_in * MULT / DIVCLK.
 *   7-series -1 MMCM limits (DS181/DS189): VCO 600..1200 MHz, PFD 10..450 MHz.
 *   Integer settings only (the fractional counters are avoided on purpose):
 *     Cmod A7  12 MHz: MULT 50, DIVCLK 1, DIV0 12 -> VCO 600 MHz, 50.000 MHz
 *     Urbana  100 MHz: MULT 10, DIVCLK 1, DIV0 20 -> VCO 1000 MHz, 50.000 MHz
 *     (CLOCK=pll40: DIV0 15 on the Cmod A7, 25 on the Urbana -> 40.000 MHz)
 *   Feedback is CLKFBOUT -> CLKFBIN directly with COMPENSATION "ZHOLD", the
 *   arrangement of openXC7's primitive-tests/mmcm-blinky.
 * MODE "BUFG": clk_in -> BUFG (board oscillator used as is, or a clock from
 *   the host on a clock-capable pin). locked is 1.
 *
 * Under PE_FPGA_SIM both modes are a plain wire: the testbench drives the
 * core-rate clock directly (Xilinx primitives are not simulated).
 */

`default_nettype none

module pe_fpga_clkgen #(
    parameter        MODE         = "MMCM",
    parameter real   CLKIN_PERIOD = 83.333,  // ns, for the MMCM
    parameter integer MULT        = 50,
    parameter integer DIVCLK      = 1,
    parameter integer DIV0        = 12
) (
    input  wire clk_in,
    output wire clk_out,
    output wire locked
);

`ifdef PE_FPGA_SIM
  assign clk_out = clk_in;
  assign locked  = 1'b1;
`else
  generate
    if (MODE == "MMCM") begin : g_mmcm
      wire fb;
      wire clk0;
      MMCME2_ADV #(
          .BANDWIDTH       ("OPTIMIZED"),
          .COMPENSATION    ("ZHOLD"),
          .CLKIN1_PERIOD   (CLKIN_PERIOD),
          .DIVCLK_DIVIDE   (DIVCLK),
          .CLKFBOUT_MULT_F (MULT),
          .CLKFBOUT_PHASE  (0.0),
          .CLKOUT0_DIVIDE_F(DIV0),
          .CLKOUT0_PHASE   (0.0),
          .CLKOUT0_DUTY_CYCLE(0.5),
          .REF_JITTER1     (0.01),
          .STARTUP_WAIT    ("FALSE")
      ) mmcm (
          .CLKIN1  (clk_in),
          .CLKIN2  (1'b0),
          .CLKINSEL(1'b1),
          .CLKFBIN (fb),
          .CLKFBOUT(fb),
          .CLKOUT0 (clk0),
          .LOCKED  (locked),
          .RST     (1'b0),
          .PWRDWN  (1'b0),
          .PSCLK   (1'b0),
          .PSEN    (1'b0),
          .PSINCDEC(1'b0),
          .DCLK    (1'b0),
          .DEN     (1'b0),
          .DWE     (1'b0),
          .DADDR   (7'd0),
          .DI      (16'd0)
      );
      BUFG bufg_core (.I(clk0), .O(clk_out));
    end else begin : g_bufg
      BUFG bufg_core (.I(clk_in), .O(clk_out));
      assign locked = 1'b1;
    end
  endgenerate
`endif

endmodule

`default_nettype wire
