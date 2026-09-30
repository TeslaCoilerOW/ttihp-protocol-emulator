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

# Design variant (variants.py, README.md "Design variants"). The default is the
# design selection, ../configs/design-selection.txt read by
# ../scripts/design_selection.sh: base for configs/instruction-sram-32.json,
# NAME for configs/variants/NAME.json. It names the variant whose core src/
# carries, so `make` here and `make GATES=yes` in the Tiny Tapeout gl_test
# action run that variant's reference model. The selected variant simulates
# ../src/protocol_emulator_core.v exactly as the Tiny Tapeout actions do
# (PE_CORE=path substitutes another core of it, e.g. a published copy). Any
# other name simulates ../build/variants/<name>/protocol_emulator_core.v
# (override with PE_CORE=path) against the reference model configured by
# ../configs/variants/<name>.json (base: ../configs/instruction-sram-32.json).
# The selection is read only when the result depends on it: PE_VARIANT not
# given, or given without PE_CORE. With both given (a copied test tree without
# ../scripts/, e.g. a mutation campaign task) the script is not needed.
TEST_MAKEFILE_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
PE_SELECTION_SCRIPT := $(TEST_MAKEFILE_DIR)/../scripts/design_selection.sh
PE_DESIGN_VARIANT :=
ifeq ($(strip $(PE_VARIANT)),)
PE_READ_SELECTION := yes
else ifeq ($(strip $(PE_CORE)),)
PE_READ_SELECTION := yes
else
PE_READ_SELECTION := no
endif
ifeq ($(PE_READ_SELECTION),yes)
ifeq ($(wildcard $(PE_SELECTION_SCRIPT)),)
$(error the default PE_VARIANT and core come from the design selection, but ../scripts/design_selection.sh does not exist here; give both PE_VARIANT=<name> and PE_CORE=<core.v>)
endif
PE_DESIGN_VARIANT := $(shell $(PE_SELECTION_SCRIPT) variant)
ifeq ($(PE_DESIGN_VARIANT),)
$(error cannot read the design selection (../scripts/design_selection.sh variant failed))
endif
endif
ifeq ($(strip $(PE_VARIANT)),)
override PE_VARIANT := $(PE_DESIGN_VARIANT)
endif
export PE_VARIANT
ifeq ($(PE_VARIANT),base)
ifdef PE_CORE
CORE_SOURCES = $(SRC_DIR)/project.v $(PE_CORE)
RTL_BUILD    = sim_build/rtl-base-core
else ifeq ($(PE_DESIGN_VARIANT),base)
CORE_SOURCES = $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))
RTL_BUILD    = sim_build/rtl
else
PE_CORE      = $(PWD)/../build/variants/base/protocol_emulator_core.v
CORE_SOURCES = $(SRC_DIR)/project.v $(PE_CORE)
RTL_BUILD    = sim_build/rtl-base
endif
else
ifndef PE_CORE
ifeq ($(PE_VARIANT),$(PE_DESIGN_VARIANT))
PE_CORE      = $(SRC_DIR)/protocol_emulator_core.v
else
PE_CORE      = $(PWD)/../build/variants/$(PE_VARIANT)/protocol_emulator_core.v
endif
endif
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
# The PDK revision the gds action installs defines the ihp_mux2/ihp_mux4 UDPs
# used by sg13cmos5l_mux* cells in a separate file; older revisions inline them.
VERILOG_SOURCES += $(wildcard $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v)
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
# tests with `make COCOTB_TEST_FILTER=test_uart_tx`. test_directed..test_timewarp
# close the campaign gaps and test_kill_* the mutation survivors (README.md).
# Variants with the line unit (docs/extension.md) also run its three modules
# and the fourteen test_line_spec_* modules (docs/extension.md section 11.3;
# test_line_spec_common is their helper, not a module). test_line_stdref
# (docs/extension.md section 6.6) is not in this list while the LSTAT[13:8]
# point of that section is open; run it with
# `make COCOTB_TEST_MODULES=test_line_stdref`.
LINE_UNIT_VARIANTS = diet8_rec16
ifneq ($(filter $(PE_VARIANT),$(LINE_UNIT_VARIANTS)),)
COCOTB_TEST_MODULES ?= test_smoke,test_protocols,test_flagship,test_legacy,test_random,test_directed,test_mover,test_counters,test_timewarp,test_kill_decode,test_kill_blocked,test_kill_pc,test_kill_mover,test_kill_regs,test_kill_xfer,test_kill_host,test_kill_events,test_kill_pins,test_kill_edges,test_kill_fifo,test_line_unit,test_line_demos,test_line_random,test_line_spec_every_engine,test_line_spec_crc,test_line_spec_lstat,test_line_spec_registers,test_line_spec_pins,test_line_spec_ticker,test_line_spec_bounded_wait,test_line_spec_state_held,test_line_spec_stuffing,test_line_spec_xfer_crc_bit,test_line_spec_se0_pins,test_line_spec_invalid_fields,test_line_spec_image_end,test_line_spec_limit_bits
endif
COCOTB_TEST_MODULES ?= test_smoke,test_protocols,test_flagship,test_legacy,test_random,test_directed,test_mover,test_counters,test_timewarp,test_kill_decode,test_kill_blocked,test_kill_pc,test_kill_mover,test_kill_regs,test_kill_xfer,test_kill_host,test_kill_events,test_kill_pins,test_kill_edges,test_kill_fifo

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
