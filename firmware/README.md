# Reprogrammable protocol examples

The 26 images here are assembled by the OCaml assembler. The sources of 19 of
them are emitted by the OCaml firmware library, and their contents are
reproducible from the named built-ins; the other 7 are explicit source files
(below). Images bind exact source bytes and little-endian instruction bytes with
SHA-256.
See [the firmware contract](../docs/firmware.md) for commands, timing and support
limits. Each source targets the full four-engine, 32-bit, 64-instruction flagship
configuration. Other architecture choices are explicit assembler parameters.

`flagship-scenario.json` is a wiring, stimulus and expected-result recipe for all
four engines plus an autonomous UART-RX-to-SPI-TX route. Its qualification field
remains `UNEXECUTED_BENCH_RECIPE`: passing simulation, RTL or FPGA evidence belongs
in separate retained execution records tied to exact source and RTL hashes.
In this repository, `test/test_flagship.py` runs the scenario against the
generated RTL in lockstep with the reference model (`test/README.md`).
Engine 1 of the scenario (and of `flagship-scenario-topup.json`) runs
`uart-rx-idle` (24 words), an 8N1 receiver at 64 clocks per bit whose idle and
start-bit waits have no bound, so an idle line does not fault it. It is an
explicit source file; the bounded built-in `uart-rx` (18 words) is still here.

Six more images are explicit source files rather than OCaml built-ins:
`swd-read` (Arm SWD reads, 60 of 64 words), `ws2812` and `ws2812b-v5`
(WS2812B LED data, 22 words each), `ps2-device` and `ps2-host` (PS/2 in both
directions, 45 and 49 words) and `onewire-master` (1-Wire reset, presence and
byte slots, 28 words). Their images are assembled from these files with
`assemble.exe --source`, which reproduces the committed bytes. The protocol
subset, timing, cited specification and limits of each are in
[docs/firmware.md](../docs/firmware.md#swd-ws2812b-ps2-and-1-wire-images); the
timing contracts are in `tools/timing/pe_contracts_ext.py` and the pin-level
tests in `test/test_protocols_ext.py` (not part of `make` or the gate-level
test; CI runs them on RTL in the `protocols-ext` job of
`.github/workflows/test.yaml`).

Protocol examples are project-authored code, not third-party device firmware.
