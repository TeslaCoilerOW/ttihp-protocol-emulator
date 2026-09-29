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

Six images are explicit source files rather than OCaml built-ins:
`swd-read` (Arm SWD reads, 60 of 64 words), `ws2812` and `ws2812b-v5`
(WS2812B LED data, 22 words each), `ps2-device` and `ps2-host` (PS/2 in both
directions, 45 and 49 words) and `onewire-master` (1-Wire reset, presence and
byte slots, 28 words). Their images are assembled from these files with
`assemble.exe --source`, which reproduces the committed bytes. The protocol
subset, timing, cited specification and limits of each are in
[docs/firmware.md](../docs/firmware.md#swd-ws2812b-ps2-and-1-wire-images); the
timing contracts are in `tools/timing/pe_contracts_ext.py` and the pin-level
tests in `test/test_protocols_ext.py` (run explicitly, not part of `make` or
the gate-level test).

Protocol examples are project-authored code, not third-party device firmware.
