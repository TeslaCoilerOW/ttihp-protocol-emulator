# Reprogrammable protocol examples

These JSON source programs are emitted by the OCaml firmware library and assembled
by the OCaml assembler. Their contents are reproducible from the named built-ins;
images bind exact source bytes and little-endian instruction bytes with SHA-256.
See [the firmware contract](../docs/firmware.md) for commands, timing and support
limits. Each source targets the full four-engine, 32-bit, 64-instruction flagship
configuration. Other architecture choices are explicit assembler parameters.

`flagship-scenario.json` is a wiring, stimulus and expected-result recipe for all
four engines plus an autonomous UART-RX-to-SPI-TX route. Its qualification field
remains `UNEXECUTED_BENCH_RECIPE`: passing simulation, RTL or FPGA evidence belongs
in separate retained execution records tied to exact source and RTL hashes.
In this repository, `test/test_flagship.py` runs the scenario against the
generated RTL in lockstep with the reference model (`test/README.md`).

Protocol examples are project-authored code, not third-party device firmware.
