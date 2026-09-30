# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""CPython tools for the lockstep PIO host port (docs/host.md, "Lockstep PIO port").

asm.py        the MicroPython ``rp2.asm_pio`` assembler DSL, reimplemented for CPython
sim.py        cycle-accurate RP2040/RP2350 PIO state-machine interpreter
pathcheck.py  exhaustive check of the lockstep program's clock and pin schedule
board.py      the PIO program, its GPIOs and the chip (reference model) co-simulated
timing.py     setup, hold and sampling margins of the program's schedule
cocotb/       the same program driving the RTL (and the FPGA host build) in cocotb

The program and the MicroPython driver live in pe_host/ports/pio_lockstep.py.
"""
