# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Simulation of the demonstration (docs/demo.md) on the full FPGA top: board
# top + shell + tt_um_teslacoilerow_protocol_emulator + FPGA SRAM stand-in,
# with the testbenches of fpga/sim unchanged. Icarus Verilog + cocotb.
#
#   make DEMO_TB=pins   ...   Cmod A7 host-clock build; the host library
#                             (host/pe_host, the Pico's code path) clocks it
#   make DEMO_TB=bridge ...   Cmod A7 osc12 build (12 MHz oscillator, UART
#                             bridge); fpga/host/pe_host.py drives the bridge
#
# Variables read by test_demo.py: DEMO_MODE (idle|loaded), DEMO_CYCLES,
# DEMO_SEED, DEMO_OUT (directory for result JSON), DEMO_LOCKSTEP (pins: 1 =
# compare with the reference model every cycle), DEMO_CLOCK (pins: jitter|fixed).
# DEMO_VCD=path writes the analyser-view VCD (demo_dump.v).
# PE_CORE=path replaces src/protocol_emulator_core.v (the mutants of mutate.py).
# COCOTB_TEST_FILTER selects a test (test_isolation, test_four, test_bridge_chain).

SIM ?= icarus
TOPLEVEL_LANG ?= verilog
WAVES :=
FST :=

REPO        := $(abspath $(CURDIR)/../..)
PE_SRC_DIR  ?= $(REPO)/src
PE_CORE     ?= $(PE_SRC_DIR)/protocol_emulator_core.v
DEMO_TB     ?= pins
CORE_TAG    ?= base
export DEMO_TB

RTL := $(REPO)/fpga/rtl
VERILOG_SOURCES += $(PE_SRC_DIR)/project.v $(PE_CORE) \
                   $(RTL)/RM_IHPSG13_1P_64x16_c2_fpga.v $(RTL)/pe_fpga_sram_64x16.v \
                   $(RTL)/pe_uart_host_bridge.v $(RTL)/pe_fpga_shell.v \
                   $(RTL)/pe_fpga_clkgen.v $(RTL)/pe_fpga_clkfwd.v \
                   $(RTL)/pe_top_cmod_a7.v $(CURDIR)/demo_dump.v
COMPILE_ARGS += -DPE_FPGA_SIM -DNO_WAVES -s demo_dump

ifeq ($(DEMO_TB),bridge)
COCOTB_TOPLEVEL = tb_bridge
VERILOG_SOURCES += $(REPO)/fpga/sim/tb_fpga_bridge.v
COMPILE_ARGS += -DPE_CLOCK_OSC -DDEMO_BRIDGE
else
COCOTB_TOPLEVEL = tb
VERILOG_SOURCES += $(REPO)/fpga/sim/tb_fpga_pins.v
COMPILE_ARGS += -DPE_CLOCK_HOST
endif

COCOTB_TEST_MODULES ?= test_demo
ifneq ($(DEMO_VCD),)
COCOTB_PLUSARGS += +demo_vcd=$(DEMO_VCD)
endif
export PYTHONPATH := $(CURDIR):$(REPO)/demo:$(REPO)/host:$(PYTHONPATH)
export PYTHONDONTWRITEBYTECODE := 1
SIM_BUILD ?= sim_build/$(DEMO_TB)_$(CORE_TAG)

include $(shell cocotb-config --makefiles)/Makefile.sim
