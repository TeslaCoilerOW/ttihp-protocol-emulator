# cocotb suite for tt_um_teslacoilerow_protocol_emulator.
# See README.md. `make` runs RTL; `make GATES=yes` runs gate level (the TT
# gl_test action copies gate_level_netlist.v into this directory first).

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = project.v protocol_emulator_core.v

# IHP SRAM behavioral models (Apache-2.0, see ../models/NOTICE.IHP-Open-PDK).
# The core instantiates eight RM_IHPSG13_1P_64x16_c2 macros; in both RTL and
# gate-level simulation they are simulated with these FUNCTIONAL models (the
# hardened netlist keeps the macros as instances).
SRAM_MODEL_DIR ?= $(PWD)/../models
SRAM_MODELS = $(SRAM_MODEL_DIR)/RM_IHPSG13_1P_64x16_c2.v \
              $(SRAM_MODEL_DIR)/RM_IHPSG13_1P_core_behavioral.v

# Design variant (variants.py, README.md "Design variants"). The default, base,
# simulates ../src/protocol_emulator_core.v exactly as the Tiny Tapeout actions
# do (PE_CORE=path substitutes another design-of-record core, e.g. a published
# copy). Another name simulates ../build/variants/<name>/protocol_emulator_core.v
# (override with PE_CORE=path) against the reference model configured by
# ../configs/variants/<name>.json.
PE_VARIANT ?= base
ifeq ($(strip $(PE_VARIANT)),)
PE_VARIANT := base
endif
export PE_VARIANT
ifeq ($(PE_VARIANT),base)
ifdef PE_CORE
CORE_SOURCES = $(SRC_DIR)/project.v $(PE_CORE)
RTL_BUILD    = sim_build/rtl-base-core
else
CORE_SOURCES = $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))
RTL_BUILD    = sim_build/rtl
endif
else
PE_CORE      ?= $(PWD)/../build/variants/$(PE_VARIANT)/protocol_emulator_core.v
CORE_SOURCES = $(SRC_DIR)/project.v $(PE_CORE)
RTL_BUILD    = sim_build/rtl-$(PE_VARIANT)
endif

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD       ?= $(RTL_BUILD)
COMPILE_ARGS    += -DFUNCTIONAL
VERILOG_SOURCES += $(CORE_SOURCES)
VERILOG_SOURCES += $(SRAM_MODELS)

else

# Gate level simulation:
SIM_BUILD       ?= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog/sg13cmos5l_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v
VERILOG_SOURCES += $(SRAM_MODELS)

# this gets copied in by the GDS action workflow (override GL_NETLIST to
# simulate a netlist kept elsewhere)
GL_NETLIST ?= $(PWD)/gate_level_netlist.v
VERILOG_SOURCES += $(GL_NETLIST)

# Tests scale their workloads down for the (much slower) gate-level run:
# a representative legacy-replay subset and 2 random cases (see README.md).
export PE_GATE_LEVEL = 1

endif

# Waveform scope: tb ports only (default), all (whole hierarchy) or none.
WAVES ?= ports
ifeq ($(WAVES),all)
COMPILE_ARGS += -DDUMP_ALL
endif
ifeq ($(WAVES),none)
COMPILE_ARGS += -DNO_WAVES
endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix.
# Select a subset with e.g. `make COCOTB_TEST_MODULES=test_random` or single
# tests with `make COCOTB_TEST_FILTER=test_uart_tx`.
COCOTB_TEST_MODULES ?= test_smoke,test_protocols,test_flagship,test_legacy,test_random

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
