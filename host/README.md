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

## Lockstep PIO port

`pe_host/ports/pio_lockstep.py` keeps the project clock running on an
RP2040/RP2350 PIO state machine and moves the host's nibble transfers on the
same clock, so the engines run at a steady rate (f_sys / 6: 25 MHz at 150 MHz)
while the host services queues. It needs MicroPython's `rp2` and `array`
modules and a board whose `ui_in` and `uo_out` are contiguous GPIO runs
(demo board v3, or a Pico wired as `pe_host.ports.pico.PIN_MAP`). It has not
run on hardware. See docs/host.md, "Lockstep PIO port", for the program,
the rate, the timing margins and the limits.

```
pio/                CPython tools for it (not deployed to the board)
  asm.py            MicroPython's rp2.asm_pio DSL for CPython
  sim.py            cycle-accurate PIO interpreter
  pathcheck.py      every program path keeps the clock and pin schedule
  board.py          interpreter + reference model at the pins; rp2/machine/time stand-ins
  scenarios.py      uart_stream, uart_bridge, flagship peers timed by the chip
  replay.py         call log of the driver for the MicroPython replay (upy_replay.py)
  timing.py         set-up, hold and sampling margins from the program and the STA
  uo_cone.py        combinational fan-in of every output (Yosys)
  cocotb/           the program driving the RTL or the FPGA host build
```

```sh
python3 -m unittest discover -s host/tests -p 'test_pio_*.py'   # interpreter, program, port, replay
python3 -m pio.pathcheck            # in host/: listing and path check
python3 -m pio.timing               # in host/: margin table
python3 -m pio.uo_cone              # in host/: needs yosys
make -C host/pio/cocotb             # RTL, cocotb 2.0.1 + Icarus; SIM_BUILD=... for a private build
make -C fpga/sim FPGA_CLOCK=host COCOTB_TEST_MODULES=test_pio_rtl WAVES=none \
     PYTHONPATH=$PWD/host/pio/cocotb:$PWD/host:$PWD/test   # FPGA host-clock build
```

Both cocotb commands run from the repository root. `WAVES=none` skips the
testbench's waveform dump (`fpga/sim/tb.fst`). Add
`SIM_BUILD=/abs/dir` (and `COCOTB_RESULTS_FILE=/abs/file.xml`) to keep the
build out of the source tree, and `COCOTB_TEST_FILTER=test_uart_bridge_lockstep`
(or another test of `host/pio/cocotb/test_pio_rtl.py`) to run one scenario.

The MicroPython replay test uses `PE_HOST_MICROPYTHON` and `PE_HOST_MPY_CROSS`
like `test_host_micropython.py`. Two optional oracles:
`PE_HOST_RP2_PY_DIR` (MicroPython's `rp2.py` files named `rp2_<tag>.py`) and
`PE_HOST_PIOASM_DIR` (`adafruit_pioasm.py`).
