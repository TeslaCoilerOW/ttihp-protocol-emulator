# Convenience targets. The Tiny Tapeout GitHub actions are the flow of record.
#   make generate                     regenerate src/protocol_emulator_core.v
#   make generate CONFIG=configs/...  from another refinement/architecture config
#   make check-generated              fail if the generated core differs from a fresh generation
#   make lint                         iverilog + yosys hierarchy + verilator -Wall
#   make test                         cocotb RTL suite (test/Makefile)

CONFIG ?= configs/instruction-sram-32.json

.PHONY: generate check-generated lint test clean

generate:
	scripts/generate.sh $(CONFIG)

check-generated: generate
	git diff --exit-code -- src/protocol_emulator_core.v

lint:
	scripts/lint.sh

test:
	$(MAKE) -C test

clean:
	rm -rf hardcaml/_build
	-$(MAKE) -C test clean

# One-command reproduction (scripts/reproduce.sh; docs/results.md).
#   make reproduce                    regen check, lint, cocotb RTL suite, formal subset,
#                                     pe_timing report, host unit tests; PASS/FAIL summary
#   make reproduce-full               adds the remaining formal jobs, test_ext peers,
#                                     variants, Hardcaml tests, pe_timing validation and
#                                     (with PDK_ROOT and GL_NETLIST) gate level, then
#                                     prints the cluster-scale campaign commands
#   make reproduce REPRO_ARGS=--parallel   run independent steps concurrently
REPRO_ARGS ?=

.PHONY: reproduce reproduce-full

reproduce:
	scripts/reproduce.sh $(REPRO_ARGS)

reproduce-full:
	scripts/reproduce.sh --full $(REPRO_ARGS)
