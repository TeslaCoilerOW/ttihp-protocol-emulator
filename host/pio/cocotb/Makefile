# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# The lockstep PIO program (host/pio) driving the RTL through cocotb: the PIO
# interpreter generates clk and ui_in at the RP2's system-clock resolution and
# samples uo_out; pe_host runs unchanged on top (test_pio_rtl.py). Same RTL
# sources and defines as test/Makefile; the testbench is
# host/tests/rtl_cocotb/tb_host.v (the Tiny Tapeout ports only).
#
#   make                                   # all tests, RTL of this checkout
#   make COCOTB_TEST_FILTER=test_uart_bridge_lockstep
#   make PE_PIO_CPU_CYCLES=12              # CPU cost per FIFO access (default 450)
#   make PE_PIO_TRACE_DIR=/abs/dir         # write clock/ui/uo traces there
#   make SIM_BUILD=/abs/dir                # private build directory
#
# The FPGA host build (fpga/sim, FPGA_CLOCK=host) runs the same module:
#   make -C fpga/sim FPGA_CLOCK=host COCOTB_TEST_MODULES=test_pio_rtl \
#        PYTHONPATH=$PWD/host/pio/cocotb:$PWD/host

SIM ?= icarus
TOPLEVEL_LANG ?= verilog
THIS_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
HOST_DIR := $(abspath $(THIS_DIR)/../..)
RTL_DIR ?= $(abspath $(HOST_DIR)/..)
MODEL_DIR ?= $(RTL_DIR)/test
SRC_DIR = $(RTL_DIR)/src
SRAM_MODELS = $(RTL_DIR)/models/RM_IHPSG13_1P_64x16_c2.v \
              $(RTL_DIR)/models/RM_IHPSG13_1P_core_behavioral.v

SIM_BUILD       ?= sim_build/rtl
COMPILE_ARGS    += -DFUNCTIONAL -I$(SRC_DIR)
VERILOG_SOURCES += $(SRC_DIR)/project.v $(SRC_DIR)/protocol_emulator_core.v $(SRAM_MODELS)
VERILOG_SOURCES += $(HOST_DIR)/tests/rtl_cocotb/tb_host.v
TOPLEVEL = tb_host
COCOTB_TEST_MODULES ?= test_pio_rtl

export PYTHONPATH := $(THIS_DIR):$(HOST_DIR):$(MODEL_DIR):$(PYTHONPATH)
export PE_HOST_TEST_DIR := $(MODEL_DIR)
export PE_HOST_FIRMWARE_DIR := $(RTL_DIR)/firmware
export PE_HOST_SCENARIO := $(RTL_DIR)/firmware/flagship-scenario.json

include $(shell cocotb-config --makefiles)/Makefile.sim
