# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Simulation of the full FPGA top (board top + shell + TT design + FPGA SRAM
# stand-in), Icarus Verilog + cocotb (same versions as test/).
#
#   make                                   # FPGA_TB=pins: test/ lockstep suite on the FPGA top
#   make FPGA_TB=bridge                    # UART bridge + fpga/host/pe_host.py bring-up tests
#   make FPGA_BOARD=urbana                 # Urbana top instead of the Cmod A7 top
#   make FPGA_CLOCK=pll50                  # pll50 build (external-host mode) instead of host clock
#   make COCOTB_TEST_MODULES=test_smoke    # subset of the test/ suite
#   make COCOTB_TEST_MODULES=test_fpga_pico  # Pico host driver, host-clocked (FPGA_CLOCK=host)
#   make PE_TEST_DIR=/path/to/snapshot/test  # run a frozen copy of test/ (and its firmware/)
#   make FPGA_NETLIST=build/synth_netlist.v  # post-synthesis netlist; FPGA_CLOCK=host for a host build,
#                                          # FPGA_CLOCK=pll50 for pll50/pll40/osc12 builds
#
# FPGA_TB=pins reuses test/*.py unchanged: tb_fpga_pins.v exposes the TT
# port names, and test/harness.py checks the FPGA top against the Python
# reference model every cycle. FPGA_TB=bridge always uses the pll50 build.

SIM ?= icarus
TOPLEVEL_LANG ?= verilog
FST ?= -fst

REPO        := $(abspath $(CURDIR)/../..)
PE_SRC_DIR  ?= $(REPO)/src
PE_TEST_DIR ?= $(REPO)/test
FPGA_BOARD  ?= cmod_a7
FPGA_CLOCK  ?= host
FPGA_TB     ?= pins
export FPGA_BOARD FPGA_CLOCK

RTL := $(REPO)/fpga/rtl
ifeq ($(FPGA_NETLIST),)
VERILOG_SOURCES += $(PE_SRC_DIR)/project.v $(PE_SRC_DIR)/protocol_emulator_core.v \
                   $(RTL)/RM_IHPSG13_1P_64x16_c2_fpga.v $(RTL)/pe_fpga_sram_64x16.v \
                   $(RTL)/pe_uart_host_bridge.v $(RTL)/pe_fpga_shell.v \
                   $(RTL)/pe_fpga_clkgen.v $(RTL)/pe_fpga_clkfwd.v \
                   $(RTL)/pe_top_$(FPGA_BOARD).v
COMPILE_ARGS += -DPE_FPGA_SIM
else
# Post-synthesis: the synth_xilinx netlist (build.sh writes synth_netlist.v)
# with Yosys' Xilinx simulation models, plus netlist_prims.v for the MMCM and
# ODDR of the pll50/pll40/osc12 builds (black boxes in cells_sim.v).
VERILOG_SOURCES += $(FPGA_NETLIST) $(shell yosys-config --datdir)/xilinx/cells_sim.v \
                   $(CURDIR)/netlist_prims.v
COMPILE_ARGS += -DPE_TB_NETLIST
endif
ifeq ($(FPGA_BOARD),urbana)
COMPILE_ARGS += -DPE_TB_URBANA
endif
ifeq ($(WAVES),none)
COMPILE_ARGS += -DNO_WAVES
endif
# e.g. EXTRA_DEFINES=-DPE_TB_INJECT_PAD_FAULT (checker self-test: must fail),
# EXTRA_DEFINES=-DPE_NO_PIN_HOST (Cmod A7 BRIDGE_ONLY build, FPGA_TB=bridge)
COMPILE_ARGS += $(EXTRA_DEFINES)
ifneq ($(findstring PE_NO_PIN_HOST,$(EXTRA_DEFINES)),)
export FPGA_BRIDGE_ONLY = 1
endif

ifeq ($(FPGA_TB),bridge)
TOPLEVEL = tb_bridge
VERILOG_SOURCES += $(CURDIR)/tb_fpga_bridge.v
COCOTB_TEST_MODULES ?= test_fpga_bridge
export PYTHONPATH := $(CURDIR):$(REPO)/fpga/host:$(PYTHONPATH)
SIM_BUILD ?= sim_build/bridge_$(FPGA_BOARD)
else
ifeq ($(FPGA_CLOCK),host)
COMPILE_ARGS += -DPE_CLOCK_HOST
endif
TOPLEVEL = tb
VERILOG_SOURCES += $(CURDIR)/tb_fpga_pins.v
COCOTB_TEST_MODULES ?= test_smoke,test_flagship
export PYTHONPATH := $(PE_TEST_DIR):$(CURDIR):$(REPO)/fpga/host:$(PYTHONPATH)
SIM_BUILD ?= sim_build/pins_$(FPGA_BOARD)_$(FPGA_CLOCK)
endif

include $(shell cocotb-config --makefiles)/Makefile.sim
