# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Digilent Cmod A7-35T (rev. B), xc7a35tcpg236-1: pins of pe_top_cmod_a7.
# Package pins from Digilent's Cmod-A7-Master.xdc
# (https://github.com/Digilent/digilent-xdc/blob/master/Cmod-A7-Master.xdc).
# "DIP n" is the Cmod's 48-pin DIP header pin n (pio n in the master XDC).
# Clock constraints are in clock_cmod_a7_<mode>.xdc (build.sh appends one).
# Literal one-line-per-port form: the nextpnr-xilinx XDC reader does not
# evaluate Tcl loops or wildcards.

## 12 MHz oscillator
set_property -dict { PACKAGE_PIN L17 IOSTANDARD LVCMOS33 } [get_ports { sysclk }]

## Buttons (active high) and LEDs
set_property -dict { PACKAGE_PIN A18 IOSTANDARD LVCMOS33 } [get_ports { btn[0] }]
set_property -dict { PACKAGE_PIN B18 IOSTANDARD LVCMOS33 } [get_ports { btn[1] }]
set_property -dict { PACKAGE_PIN A17 IOSTANDARD LVCMOS33 } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN C16 IOSTANDARD LVCMOS33 } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN C17 IOSTANDARD LVCMOS33 } [get_ports { led0_r }]
set_property -dict { PACKAGE_PIN B16 IOSTANDARD LVCMOS33 } [get_ports { led0_g }]
set_property -dict { PACKAGE_PIN B17 IOSTANDARD LVCMOS33 } [get_ports { led0_b }]

## USB-UART (FTDI). uart_rxd: FTDI TXD -> FPGA (master XDC uart_txd_in);
## uart_txd: FPGA -> FTDI RXD (master XDC uart_rxd_out).
set_property -dict { PACKAGE_PIN J17 IOSTANDARD LVCMOS33 } [get_ports { uart_rxd }]
set_property -dict { PACKAGE_PIN J18 IOSTANDARD LVCMOS33 } [get_ports { uart_txd }]

## Protocol pins uio[0..7] on Pmod JA pins 1, 2, 3, 4, 7, 8, 9, 10.
## Weak pull-ups keep undriven pins high (UART idle, SPI CS, I2C); use real
## 2.2-4.7 kOhm pull-ups on I2C SCL/SDA.
set_property -dict { PACKAGE_PIN G17 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { ja[0] }]
set_property -dict { PACKAGE_PIN G19 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { ja[1] }]
set_property -dict { PACKAGE_PIN N18 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { ja[2] }]
set_property -dict { PACKAGE_PIN L18 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { ja[3] }]
set_property -dict { PACKAGE_PIN H17 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { ja[4] }]
set_property -dict { PACKAGE_PIN H19 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { ja[5] }]
set_property -dict { PACKAGE_PIN J19 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { ja[6] }]
set_property -dict { PACKAGE_PIN K18 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { ja[7] }]

## External host port: ui_in[0..7] on DIP 1-8
set_property -dict { PACKAGE_PIN M3  IOSTANDARD LVCMOS33 } [get_ports { ui_pin[0] }]
set_property -dict { PACKAGE_PIN L3  IOSTANDARD LVCMOS33 } [get_ports { ui_pin[1] }]
set_property -dict { PACKAGE_PIN A16 IOSTANDARD LVCMOS33 } [get_ports { ui_pin[2] }]
set_property -dict { PACKAGE_PIN K3  IOSTANDARD LVCMOS33 } [get_ports { ui_pin[3] }]
set_property -dict { PACKAGE_PIN C15 IOSTANDARD LVCMOS33 } [get_ports { ui_pin[4] }]
set_property -dict { PACKAGE_PIN H1  IOSTANDARD LVCMOS33 } [get_ports { ui_pin[5] }]
set_property -dict { PACKAGE_PIN A15 IOSTANDARD LVCMOS33 } [get_ports { ui_pin[6] }]
set_property -dict { PACKAGE_PIN B15 IOSTANDARD LVCMOS33 } [get_ports { ui_pin[7] }]

## uo_out[0..7] on DIP 9-14, 17, 18
set_property -dict { PACKAGE_PIN A14 IOSTANDARD LVCMOS33 } [get_ports { uo_pin[0] }]
set_property -dict { PACKAGE_PIN J3  IOSTANDARD LVCMOS33 } [get_ports { uo_pin[1] }]
set_property -dict { PACKAGE_PIN J1  IOSTANDARD LVCMOS33 } [get_ports { uo_pin[2] }]
set_property -dict { PACKAGE_PIN K2  IOSTANDARD LVCMOS33 } [get_ports { uo_pin[3] }]
set_property -dict { PACKAGE_PIN L1  IOSTANDARD LVCMOS33 } [get_ports { uo_pin[4] }]
set_property -dict { PACKAGE_PIN L2  IOSTANDARD LVCMOS33 } [get_ports { uo_pin[5] }]
set_property -dict { PACKAGE_PIN M1  IOSTANDARD LVCMOS33 } [get_ports { uo_pin[6] }]
set_property -dict { PACKAGE_PIN N3  IOSTANDARD LVCMOS33 } [get_ports { uo_pin[7] }]

## Host control: DIP 45 host select (pull-up: open = UART bridge, GND = pins),
## DIP 46 host clock (W7, IO_L13P_T2_MRCC_34, clock capable),
## DIP 47 rst_n (pull-up), DIP 48 ena (pull-up)
set_property -dict { PACKAGE_PIN U7 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { host_sel_n }]
set_property -dict { PACKAGE_PIN W7 IOSTANDARD LVCMOS33 } [get_ports { host_clk }]
set_property -dict { PACKAGE_PIN U8 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { host_rst_n }]
set_property -dict { PACKAGE_PIN V8 IOSTANDARD LVCMOS33 PULLTYPE PULLUP } [get_ports { host_ena }]
