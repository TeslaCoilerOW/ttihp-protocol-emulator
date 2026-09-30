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

## Devices and capability bits

READ_SELECT 7 holds the ISA version in bits 7..0 and constant capability bits
in bits 23..8 ([docs/isa.md](../docs/isa.md), "Discovery"). The library
compares only bits 7..0 with an ISA version (`isa_version()`, `check_isa()`),
decodes bits 23..8 as `protocol.Capabilities` (`capabilities()`), and knows two
devices (`protocol.DEVICES`):

| `device` | configuration | READ_SELECT 7 | READ_SELECT 5 |
|---|---|---|---|
| `base` (default) | `configs/instruction-sram-32.json` | `0x00000002` | completed instructions |
| `diet8_rec16` | `configs/variants/diet8_rec16.json` ([docs/extension.md](../docs/extension.md)) | `0x000F5F03` | 0 (no debug counters) |

The default device, `base`, is fixed in `pe_host/host.py`; it does not follow
the design selection (`configs/design-selection.txt`), which a board running
MicroPython does not have. `ProtocolEmulator(port, device="diet8_rec16")`
accepts ISA 3 and gives the self-test that device's expectations; `ModelPort(device="diet8_rec16")` and
`CocotbPort(dut, device="diet8_rec16")` use that variant's reference model.
The self-test's `line_unit` section runs a CRC/LTIM/LSTAT probe twice on every
engine the capability bits name, or, on a device without the unit, requires
LSTAT to fault with code 1.

An image needs the capabilities its words use (line-unit opcodes 30-33, XFER
c bits 5 and 6, and the fraction, stuffing, arbitration and preset fields),
plus any listed in an optional `"requires"` array of names from
`protocol.CAPABILITY_NAMES`; a `"requires"` list that omits a used capability is
an `ImageError`. `load_image()` raises `CapabilityMismatch` when the device
lacks a needed capability or the line unit on the target engine, before
anything is written. `isa_version` keeps its meaning (minimum ISA version).

The RTL tests follow the design selection (`configs/design-selection.txt`,
`scripts/design_selection.sh`): `tests/test_host_rtl.py` replays each device
against its own core (`src/` for the selected one, `build/variants/<name>/`
from `scripts/gen_variants.sh` for the other, skipped when absent), and
`make rtl-cocotb` sets `PE_HOST_DEVICE` from it.
