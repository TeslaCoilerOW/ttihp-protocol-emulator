# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Host library checks (docs/host.md). All targets run from host/.
#
#   make test          CPython unit tests (reference model; MicroPython and
#                      Icarus replays run when the tools are available)
#   make upy-check     static MicroPython-subset check
#   make mpy           precompile the MicroPython modules with mpy-cross into
#                      build/mpy (MPY_ARCH=armv6m for RP2040, armv7emsp for RP2350)
#   make rtl-cocotb    the library on the RTL through cocotb (lockstep); works
#                      as `make -C host rtl-cocotb` from the repository root too
#   make rtl-cocotb-paths  print the paths rtl-cocotb resolves (no cocotb needed)
#
# Tools: PE_HOST_MICROPYTHON, PE_HOST_MPY_CROSS, PE_HOST_IVERILOG_BIN,
# PE_HOST_TEST_DIR, PE_HOST_RTL_DIR (see tests/support.py and the tests).

PYTHON ?= python3
MPY_CROSS ?= $(or $(PE_HOST_MPY_CROSS),mpy-cross)
MPY_ARCH ?= armv6m
UPY_MODULES = $(shell $(PYTHON) -c "import sys; sys.path.insert(0, 'tools'); import upy_check; print(' '.join(m for m in upy_check.MICROPYTHON_MODULES if m.startswith('pe_host/')))")

.PHONY: test upy-check mpy rtl-cocotb rtl-cocotb-paths clean

test:
	$(PYTHON) -m unittest discover -s tests -v

upy-check:
	$(PYTHON) tools/upy_check.py

mpy:
	@for m in $(UPY_MODULES); do \
	  mkdir -p build/mpy/$$(dirname $$m); \
	  $(MPY_CROSS) -march=$(MPY_ARCH) -o build/mpy/$${m%.py}.mpy $$m || exit 1; \
	done; ls -l build/mpy/pe_host build/mpy/pe_host/ports

rtl-cocotb:
	$(MAKE) -C tests/rtl_cocotb

rtl-cocotb-paths:
	@$(MAKE) -s --no-print-directory -C tests/rtl_cocotb paths

clean:
	rm -rf build tests/rtl_cocotb/sim_build tests/rtl_cocotb/results.xml
