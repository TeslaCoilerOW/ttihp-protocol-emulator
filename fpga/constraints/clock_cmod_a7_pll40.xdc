# Cmod A7, CLOCK=pll40: 12 MHz oscillator, 40 MHz MMCM output on net clk.
create_clock -period 83.333 [get_ports { sysclk }]
create_clock -period 25.000 [get_nets { clk }]
