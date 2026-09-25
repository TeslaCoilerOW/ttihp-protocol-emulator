# Urbana, CLOCK=pll40: 100 MHz oscillator, 40 MHz MMCM output on net clk.
create_clock -period 10.000 [get_ports { clk_100mhz }]
create_clock -period 25.000 [get_nets { clk }]
