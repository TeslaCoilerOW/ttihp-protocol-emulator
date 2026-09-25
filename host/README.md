# pe_host: host library for tt_um_teslacoilerow_protocol_emulator

One API (`pe_host.ProtocolEmulator`) for the chip's nibble host port
([docs/isa.md](../docs/isa.md)) on the Tiny Tapeout demo board (MicroPython,
`ttboard` SDK), on a Raspberry Pi Pico wired to an FPGA (MicroPython,
`machine.Pin`), and on CPython against the reference model or the RTL under
cocotb. Full documentation (wiring, API reference, running on silicon,
verification record): [docs/host.md](../docs/host.md).

```
pe_host/            the library (MicroPython compatible except ports/model.py, ports/cocotb_port.py)
  host.py           ProtocolEmulator
  protocol.py       constants, encoders, Status, fault names
  image.py          firmware image loading, SHA-256 and architecture binding
  peers.py          pad-level software peers (UART, SPI, I2C)
  flagship.py       firmware/flagship-scenario.json runner
  selftest.py       bring-up self-test (every host command, nothing attached)
  ports/            ttboard, pico, model, cocotb_port, replay backends
examples/           selftest_demo.py, flagship_demo.py (same scripts on every backend)
tests/              CPython unittest suite, MicroPython replay scripts, cocotb RTL test
tools/              upy_check.py (MicroPython subset), upy_heap.py, fuzz_host.py
```

```sh
make -C host test          # unit tests (reference model; MicroPython/Icarus parts when available)
make -C host upy-check     # static MicroPython-subset check
make -C host mpy           # mpy-cross the MicroPython modules into host/build/mpy
make -C host rtl-cocotb    # the library on the RTL through cocotb, lockstep with the model
make -C host rtl-cocotb-paths   # print the files rtl-cocotb uses (no cocotb needed)
python3 host/examples/flagship_demo.py
```

The make targets work from any directory: `make -C host ...` from the
repository root, `make ...` inside `host/`, or `make` inside
`host/tests/rtl_cocotb/`. The RTL test takes its paths from its Makefile's
location, not from `$PWD`. To use another checkout or scenario, pass
`RTL_DIR=/abs/path`, `MODEL_DIR=/abs/path/test` or
`PE_HOST_SCENARIO=/abs/file.json` on the make command line.
