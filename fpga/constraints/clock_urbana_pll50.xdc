# Urbana, CLOCK=pll50: 100 MHz oscillator, 50 MHz MMCM output on net clk.
create_clock -period 10.000 [get_ports { clk_100mhz }]
create_clock -period 20.000 [get_nets { clk }]
