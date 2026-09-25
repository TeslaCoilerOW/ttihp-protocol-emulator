# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# The host library (pe_host) driving the RTL through cocotb: CocotbPort runs the
# synchronous library in a cocotb bridge thread and checks every cycle against
# the reference model (lockstep). Same sources and defines as test/Makefile.
#
#   make                      # RTL of this checkout
#   make RTL_DIR=/path/to/checkout MODEL_DIR=/path/to/checkout/test
#   make PE_HOST_SCENARIO=/path/to/flagship-scenario.json
#   make GATES=yes GL_NETLIST=... PDK_ROOT=...
#   make paths                # print the resolved paths (no cocotb needed)
#
# Paths derive from this Makefile's own location, never from $(PWD): GNU make
# does not update PWD for `make -C`, so `make -C host rtl-cocotb` from the
# repository root and `cd host && make rtl-cocotb` resolve the same files as
# running make here. Pass RTL_DIR/MODEL_DIR as absolute paths.

SIM ?= icarus
TOPLEVEL_LANG ?= verilog
THIS_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
HOST_DIR := $(abspath $(THIS_DIR)/../..)
RTL_DIR ?= $(abspath $(HOST_DIR)/..)
MODEL_DIR ?= $(RTL_DIR)/test
SRC_DIR = $(RTL_DIR)/src
SRAM_MODELS = $(RTL_DIR)/models/RM_IHPSG13_1P_64x16_c2.v \
              $(RTL_DIR)/models/RM_IHPSG13_1P_core_behavioral.v

ifneq ($(GATES),yes)
SIM_BUILD       ?= sim_build/rtl
COMPILE_ARGS    += -DFUNCTIONAL
VERILOG_SOURCES += $(SRC_DIR)/project.v $(SRC_DIR)/protocol_emulator_core.v $(SRAM_MODELS)
else
SIM_BUILD       ?= sim_build/gl
COMPILE_ARGS    += -DGL_TEST -DFUNCTIONAL -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog/sg13cmos5l_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v
VERILOG_SOURCES += $(SRAM_MODELS) $(GL_NETLIST)
endif

COMPILE_ARGS    += -I$(SRC_DIR)
VERILOG_SOURCES += $(THIS_DIR)/tb_host.v
TOPLEVEL = tb_host
COCOTB_TEST_MODULES ?= cocotb_host_rtl

export PYTHONPATH := $(HOST_DIR):$(MODEL_DIR):$(THIS_DIR):$(PYTHONPATH)
export PE_HOST_TEST_DIR := $(MODEL_DIR)
# ':=' so a stale PE_HOST_SCENARIO in the environment cannot win; override it
# on the make command line.
export PE_HOST_SCENARIO := $(RTL_DIR)/firmware/flagship-scenario.json

ifeq ($(MAKECMDGOALS),paths)
.PHONY: paths
paths:
	@echo "THIS_DIR=$(THIS_DIR)"
	@echo "HOST_DIR=$(HOST_DIR)"
	@echo "RTL_DIR=$(RTL_DIR)"
	@echo "MODEL_DIR=$(MODEL_DIR)"
	@echo "TB=$(THIS_DIR)/tb_host.v"
	@echo "SOURCES=$(VERILOG_SOURCES)"
	@echo "PE_HOST_SCENARIO=$(PE_HOST_SCENARIO)"
	@echo "PYTHONPATH=$(PYTHONPATH)"
else
include $(shell cocotb-config --makefiles)/Makefile.sim
endif
