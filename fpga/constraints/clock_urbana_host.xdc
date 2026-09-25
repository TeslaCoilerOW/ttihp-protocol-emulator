# Urbana, CLOCK=host: core clock from the host on host_clk (JAB_1).
# Constrained at 50 MHz, the chip's target; a Pico host runs far slower.
create_clock -period 20.000 [get_ports { host_clk }]
create_clock -period 20.000 [get_nets { clk }]
