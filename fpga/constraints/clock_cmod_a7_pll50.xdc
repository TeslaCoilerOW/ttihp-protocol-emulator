# Cmod A7, CLOCK=pll50: 12 MHz oscillator, 50 MHz MMCM output on net clk.
create_clock -period 83.333 [get_ports { sysclk }]
create_clock -period 20.000 [get_nets { clk }]
