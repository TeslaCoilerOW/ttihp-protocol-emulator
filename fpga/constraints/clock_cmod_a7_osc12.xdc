# Cmod A7, CLOCK=osc12: the core runs on the 12 MHz oscillator.
create_clock -period 83.333 [get_ports { sysclk }]
create_clock -period 83.333 [get_nets { clk }]
