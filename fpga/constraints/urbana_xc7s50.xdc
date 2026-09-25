# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Real Digital Urbana (V2I1), xc7s50csga324-1: pins of pe_top_urbana.
# Package pins from Real Digital's urbana.xdc
# (https://www.realdigital.org/hardware/urbana, "Urbana Constraints File"),
# checked against the MIT 6.205 top_level.xdc and the Urbana schematic
# (sheet 5, PMOD HEADERS). The official file lists JB2_P/JB2_N as H13/H14,
# a copy of JA2; 6.205 corrects them to K14/J15 (IO_L23P/N_T3_15, the only
# differential pair consistent with the schematic net names), used here.
# Bank 35 (switches, buttons) is 2.5 V: LVCMOS25 as in the official file.
# Clock constraints are in clock_urbana_<mode>.xdc (build.sh appends one).
#
# Pmod+ header J1 (2x15). Pmod pin -> J1 pin -> FPGA pin (signal):
#   PMOD A 1,2,3,4 = J1.1,2,3,4    = F14,F15,H13,H14 (JA1_P,JA1_N,JA2_P,JA2_N)
#   PMOD A 7,8,9,10 = J1.30,29,28,27 = J13,J14,E14,E15 (JA3_P,JA3_N,JA4_P,JA4_N)
#   PMOD B 1,2,3,4 = J1.10,11,12,13 = H18,G18,K14,J15 (JB1_P,JB1_N,JB2_P,JB2_N)
#   PMOD B 7,8,9,10 = J1.21,20,19,18 = H16,H17,K16,J16 (JB3_P,JB3_N,JB4_P,JB4_N)
#   GPIO: J1.7 D11 (JAB_0), J1.8 C12 (JAB_1), J1.9 E16 (JAB_2),
#         J1.22 G16 (JAB_3), J1.23 C11 (JAB_4), J1.24 D10 (JAB_5)
#   GND: J1.5, 14, 17, 26; 3.3 V: J1.6, 15, 16, 25 (one net each).

## 100 MHz oscillator (MRCC)
set_property -dict { PACKAGE_PIN N15 IOSTANDARD LVCMOS33 } [get_ports { clk_100mhz }]

## Buttons (bank 35, 2.5 V)
set_property -dict { PACKAGE_PIN J2 IOSTANDARD LVCMOS25 } [get_ports { btn[0] }]
set_property -dict { PACKAGE_PIN J1 IOSTANDARD LVCMOS25 } [get_ports { btn[1] }]
set_property -dict { PACKAGE_PIN G2 IOSTANDARD LVCMOS25 } [get_ports { btn[2] }]
set_property -dict { PACKAGE_PIN H2 IOSTANDARD LVCMOS25 } [get_ports { btn[3] }]

## Slide switches (bank 35, 2.5 V)
set_property -dict { PACKAGE_PIN G1 IOSTANDARD LVCMOS25 } [get_ports { sw[0] }]
set_property -dict { PACKAGE_PIN F2 IOSTANDARD LVCMOS25 } [get_ports { sw[1] }]
set_property -dict { PACKAGE_PIN F1 IOSTANDARD LVCMOS25 } [get_ports { sw[2] }]
set_property -dict { PACKAGE_PIN E2 IOSTANDARD LVCMOS25 } [get_ports { sw[3] }]
set_property -dict { PACKAGE_PIN E1 IOSTANDARD LVCMOS25 } [get_ports { sw[4] }]
set_property -dict { PACKAGE_PIN D2 IOSTANDARD LVCMOS25 } [get_ports { sw[5] }]
set_property -dict { PACKAGE_PIN D1 IOSTANDARD LVCMOS25 } [get_ports { sw[6] }]
set_property -dict { PACKAGE_PIN C2 IOSTANDARD LVCMOS25 } [get_ports { sw[7] }]
set_property -dict { PACKAGE_PIN B2 IOSTANDARD LVCMOS25 } [get_ports { sw[8] }]
set_property -dict { PACKAGE_PIN A4 IOSTANDARD LVCMOS25 } [get_ports { sw[9] }]
set_property -dict { PACKAGE_PIN A5 IOSTANDARD LVCMOS25 } [get_ports { sw[10] }]
set_property -dict { PACKAGE_PIN A6 IOSTANDARD LVCMOS25 } [get_ports { sw[11] }]
set_property -dict { PACKAGE_PIN C7 IOSTANDARD LVCMOS25 } [get_ports { sw[12] }]
set_property -dict { PACKAGE_PIN A7 IOSTANDARD LVCMOS25 } [get_ports { sw[13] }]
set_property -dict { PACKAGE_PIN B7 IOSTANDARD LVCMOS25 } [get_ports { sw[14] }]
set_property -dict { PACKAGE_PIN A8 IOSTANDARD LVCMOS25 } [get_ports { sw[15] }]

## LEDs: [7:0] status, [15:8] protocol pin levels
set_property -dict { PACKAGE_PIN C13 IOSTANDARD LVCMOS33 } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN C14 IOSTANDARD LVCMOS33 } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN D14 IOSTANDARD LVCMOS33 } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN D15 IOSTANDARD LVCMOS33 } [get_ports { led[3] }]
set_property -dict { PACKAGE_PIN D16 IOSTANDARD LVCMOS33 } [get_ports { led[4] }]
set_property -dict { PACKAGE_PIN F18 IOSTANDARD LVCMOS33 } [get_ports { led[5] }]
set_property -dict { PACKAGE_PIN E17 IOSTANDARD LVCMOS33 } [get_ports { led[6] }]
set_property -dict { PACKAGE_PIN D17 IOSTANDARD LVCMOS33 } [get_ports { led[7] }]
set_property -dict { PACKAGE_PIN C17 IOSTANDARD LVCMOS33 } [get_ports { led[8] }]
set_property -dict { PACKAGE_PIN B18 IOSTANDARD LVCMOS33 } [get_ports { led[9] }]
set_property -dict { PACKAGE_PIN A17 IOSTANDARD LVCMOS33 } [get_ports { led[10] }]
set_property -dict { PACKAGE_PIN B17 IOSTANDARD LVCMOS33 } [get_ports { led[11] }]
set_property -dict { PACKAGE_PIN C18 IOSTANDARD LVCMOS33 } [get_ports { led[12] }]
set_property -dict { PACKAGE_PIN D18 IOSTANDARD LVCMOS33 } [get_ports { led[13] }]
set_property -dict { PACKAGE_PIN E18 IOSTANDARD LVCMOS33 } [get_ports { led[14] }]
set_property -dict { PACKAGE_PIN G17 IOSTANDARD LVCMOS33 } [get_ports { led[15] }]

## USB-UART (FTDI). uart_rxd = official UART_TXD (B16, FTDI -> FPGA);
## uart_txd = official UART_RXD (A16, FPGA -> FTDI). Same directions as the
## 6.205 files (uart_rxd B16, uart_txd A16).
set_property -dict { PACKAGE_PIN B16 IOSTANDARD LVCMOS33 } [get_ports { uart_rxd }]
set_property -dict { PACKAGE_PIN A16 IOSTANDARD LVCMOS33 } [get_ports { uart_txd }]

## Protocol pins uio[0..7] on PMOD A pins 1,2,3,4,7,8,9,10 (weak pull-ups;
## use real 2.2-4.7 kOhm pull-ups on I2C SCL/SDA)
set_property -dict { PACKAGE_PIN F14 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { pmoda[0] }]
set_property -dict { PACKAGE_PIN F15 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { pmoda[1] }]
set_property -dict { PACKAGE_PIN H13 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { pmoda[2] }]
set_property -dict { PACKAGE_PIN H14 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { pmoda[3] }]
set_property -dict { PACKAGE_PIN J13 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { pmoda[4] }]
set_property -dict { PACKAGE_PIN J14 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { pmoda[5] }]
set_property -dict { PACKAGE_PIN E14 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { pmoda[6] }]
set_property -dict { PACKAGE_PIN E15 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { pmoda[7] }]

## Host ui_in[0..7] on PMOD B pins 1,2,3,4,7,8,9,10
set_property -dict { PACKAGE_PIN H18 IOSTANDARD LVCMOS33 } [get_ports { pmodb[0] }]
set_property -dict { PACKAGE_PIN G18 IOSTANDARD LVCMOS33 } [get_ports { pmodb[1] }]
set_property -dict { PACKAGE_PIN K14 IOSTANDARD LVCMOS33 } [get_ports { pmodb[2] }]
set_property -dict { PACKAGE_PIN J15 IOSTANDARD LVCMOS33 } [get_ports { pmodb[3] }]
set_property -dict { PACKAGE_PIN H16 IOSTANDARD LVCMOS33 } [get_ports { pmodb[4] }]
set_property -dict { PACKAGE_PIN H17 IOSTANDARD LVCMOS33 } [get_ports { pmodb[5] }]
set_property -dict { PACKAGE_PIN K16 IOSTANDARD LVCMOS33 } [get_ports { pmodb[6] }]
set_property -dict { PACKAGE_PIN J16 IOSTANDARD LVCMOS33 } [get_ports { pmodb[7] }]

## uo_out[0..4] on the Pmod+ GPIO pins JAB_0, JAB_2, JAB_3, JAB_4, JAB_5
set_property -dict { PACKAGE_PIN D11 IOSTANDARD LVCMOS33 } [get_ports { jab_uo[0] }]
set_property -dict { PACKAGE_PIN E16 IOSTANDARD LVCMOS33 } [get_ports { jab_uo[1] }]
set_property -dict { PACKAGE_PIN G16 IOSTANDARD LVCMOS33 } [get_ports { jab_uo[2] }]
set_property -dict { PACKAGE_PIN C11 IOSTANDARD LVCMOS33 } [get_ports { jab_uo[3] }]
set_property -dict { PACKAGE_PIN D10 IOSTANDARD LVCMOS33 } [get_ports { jab_uo[4] }]

## Host clock on JAB_1 (C12, IO_L13P_T2_MRCC_16, clock capable)
set_property -dict { PACKAGE_PIN C12 IOSTANDARD LVCMOS33 } [get_ports { host_clk }]

## Servo header signal pins (510 Ohm series resistor on the board):
## SERVO1 (L17) = uo_out[5] read-valid, SERVO0 (L18) = host reset, active high.
## The servo headers' middle pin is +5 V servo power: never connect it.
set_property -dict { PACKAGE_PIN L17 IOSTANDARD LVCMOS33 } [get_ports { servo_uo5 }]
set_property -dict { PACKAGE_PIN L18 IOSTANDARD LVCMOS33 PULLTYPE PULLDOWN } [get_ports { servo_rst }]
