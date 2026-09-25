# Independent-peer cocotb suite for tt_um_teslacoilerow_protocol_emulator.
# The DUT is exercised by third-party protocol peers (vendor/, see
# ../docs/independent-peers.md) through a model board (tb_ext.v), while the
# lockstep host driver of ../test/harness.py checks it against the reference
# model every cycle. `make` runs RTL; `make GATES=yes` runs gate level.
# PE_EXT_SEED=<n>: random stimulus. PE_EXT_SKEW=data|clock|none: one skew
# corner of tb_ext.v instead of both. PE_EXT_NO_EDGE_CHECK=1: leave the
# protocol timing verdict to the third-party peers alone.

SIM ?= icarus
FST ?= -fst
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = project.v protocol_emulator_core.v
VENDOR = $(PWD)/vendor

SRAM_MODEL_DIR ?= $(PWD)/../models
SRAM_MODELS = $(SRAM_MODEL_DIR)/RM_IHPSG13_1P_64x16_c2.v \
              $(SRAM_MODEL_DIR)/RM_IHPSG13_1P_core_behavioral.v

ifneq ($(GATES),yes)

SIM_BUILD       ?= sim_build/rtl
COMPILE_ARGS    += -DFUNCTIONAL
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))
VERILOG_SOURCES += $(SRAM_MODELS)

else

SIM_BUILD       ?= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog/sg13cmos5l_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v
VERILOG_SOURCES += $(SRAM_MODELS)
# The TT gl_test action copies the hardened netlist to ../test/gate_level_netlist.v.
GL_NETLIST ?= $(PWD)/../test/gate_level_netlist.v
VERILOG_SOURCES += $(GL_NETLIST)

endif

WAVES ?= ports
ifeq ($(WAVES),all)
COMPILE_ARGS += -DDUMP_ALL
endif
ifeq ($(WAVES),none)
COMPILE_ARGS += -DNO_WAVES
endif

COMPILE_ARGS += -I$(SRC_DIR)

# Testbench first (it carries `timescale 1ns/1ps), then the third-party peers.
VERILOG_SOURCES += $(PWD)/tb_ext.v
VERILOG_SOURCES += $(VENDOR)/verilog-uart/uart_rx.v $(VENDOR)/verilog-uart/uart_tx.v
VERILOG_SOURCES += $(VENDOR)/verilog-i2c/i2c_slave.v $(VENDOR)/verilog-i2c/i2c_master.v
VERILOG_SOURCES += $(VENDOR)/picorv32-spiflash/spiflash.v
VERILOG_SOURCES += $(VENDOR)/nandland-spi-slave/SPI_Slave.v
VERILOG_SOURCES += $(VENDOR)/nandland-spi-master/SPI_Master.v $(VENDOR)/nandland-spi-master/SPI_Master_With_Single_CS.v
VERILOG_SOURCES += $(VENDOR)/hazard3-jtag-dtm/hazard3_jtag_dtm.v $(VENDOR)/hazard3-jtag-dtm/hazard3_jtag_dtm_core.v \
                   $(VENDOR)/hazard3-jtag-dtm/hazard3_apb_async_bridge.v $(VENDOR)/hazard3-jtag-dtm/hazard3_sync_1bit.v \
                   $(VENDOR)/hazard3-jtag-dtm/hazard3_reset_sync.v
TOPLEVEL = tb_ext

# Python: ../test (harness, reference model; used unmodified) and the vendored
# cocotbext packages (ext_harness.py also adds them when run without make).
export PYTHONPATH := $(PWD):$(PWD)/../test:$(VENDOR)/cocotbext-uart:$(VENDOR)/cocotbext-i2c:$(VENDOR)/cocotbext-spi$(if $(PYTHONPATH),:$(PYTHONPATH))

# Memory image of the SPI flash model (flash_image.py), passed as +firmware=.
FLASH_HEX = $(SIM_BUILD)/flash.hex
CUSTOM_SIM_DEPS += $(FLASH_HEX)
COCOTB_PLUSARGS += +firmware=$(FLASH_HEX)

COCOTB_TEST_MODULES ?= test_ext_uart,test_ext_spi,test_ext_i2c,test_ext_jtag,test_ext_flagship

include $(shell cocotb-config --makefiles)/Makefile.sim

$(FLASH_HEX): $(PWD)/flash_image.py | $(SIM_BUILD)
	python3 $(PWD)/flash_image.py $@
