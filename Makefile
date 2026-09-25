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
