# Hardcaml tests

Tests of the Hardcaml sources in `hardcaml/lib`. They only read the library:
none of them changes the generated Verilog in `src/`.

## Running

The OCaml packages are pinned in `scripts/opam-deps.txt` (OCaml 5.2.1): the
generator's packages, plus the test-only packages on the lines starting with
`#test ` (`hardcaml_waveterm`, `ppx_hardcaml`, `ppx_jane`, `ppx_expect`,
`base_quickcheck`). From the repository root:

```bash
opam install $(grep -v '^#' scripts/opam-deps.txt) $(sed -n 's/^#test //p' scripts/opam-deps.txt)
dune test --root hardcaml                      # everything below except line_sys_test
(cd hardcaml && dune build ./test/line_sys_test.exe)
hardcaml/_build/default/test/line_sys_test.exe configs/variants/diet8_rec16.json
```

CI runs the same commands in the `hardcaml-tests` job of
`.github/workflows/regen.yaml`, separate from the job that regenerates the
core and checks that `src/` is current.

In a switch without the test-only packages, the `expect/` library is
`(optional)`, so dune skips it and still runs the other tests. The CI job
checks that the packages are installed, so there the expect tests always run.

When an expect test fails, dune prints the difference between the recorded
output and the new one. If the change is intended, accept it with
`dune promote --root hardcaml` and commit the updated test file.

## What runs

| Test | Checks |
|------|--------|
| `smoke.ml` | Host loading, an event, an autonomous mover transfer and deselection, in Cyclesim, for six architectures |
| `assembler_test.ml` | Assembler and `Firmware` images, byte-lane and FIFO-depth variants, and rejection of invalid images |
| `isa2_test.ml` | Strict and blocking `PUSH`, stalls, RX and version status, the four trigger modes |
| `instruction_sram_test.ml` | The SRAM refinement: config validation, adapter timing, next-PC prediction, and cycle-by-cycle comparison against the register-store processor |
| `instruction_sram_formal_test.ml`, `payload_sram_test.ml` | The formal wrappers and the payload SRAM model |
| `variant_test.ml` | The variant knobs of `docs/variants.md`, with lockstep comparisons and negative controls |
| `line_test.ml` | The line unit of `docs/extension.md` at engine level (part of `dune test`) |
| `line_sys_test.ml` | The line unit on the whole chip; takes a config, so it is run separately |
| `expect/` | Waveform expect tests, below |

## Waveform expect tests (`expect/`)

Each `let%expect_test` drives the chip through its pins in Cyclesim and prints
a [Hardcaml_waveterm](https://github.com/janestreet/hardcaml_waveterm)
waveform, plus a few decoded values. The recorded output is part of the test
file, so the waveforms double as documentation of the pin-level behaviour, and
any change in that behaviour fails `dune test`.

| File | What the waveform shows |
|------|-------------------------|
| `test_host_port.ml` | A command write in window 0, a TX word write in window 2 and a status read in window 0: the nibble order, write-ready and read-valid, and the bubble cycle on each window change (the timing sketch in `docs/info.md`) |
| `test_program_load.ml` | `SELECT`, `BEGIN`, three words in window 1, `OWN`, a `COMMIT` with the wrong length (rejected; the host fault is set), `CLEAR` of the host fault, and the accepted `COMMIT` |
| `test_uart_tx.ml` | The `uart-tx` example firmware sending 0xA5 on pin 0 at 16 clocks per bit, decoded from the pin |
| `test_xfer.ml` | An 8-bit `XFER` in SPI mode 0 with a MISO target model, and the four SPI modes side by side |
| `test_push_fault.ml` | A strict `PUSH` filling the RX queue and faulting with code 4, next to a blocking `PUSH` that stalls |
| `test_wait.ml` | `WAIT n` taking n + 1 cycles, and `WAITPIN` bounded by `LIMIT`: fault code 3, or no fault when the pin rises in time |

How to read them:

- Each clock cycle is two characters wide, except where a test compresses
  the waveform: then one character holds one cycle (the program-load and
  XFER tests) or four cycles (the UART test). `╥`/`╨` marks a character that
  contains several transitions.
- Values are sampled just before the rising edge that ends the cycle, which is
  what a host sampling `uo_out` sees.
- The ports are the chip's pins with `ui_in` and `uo_out` split into the
  host-port fields (`window`, `write_valid`, `write_nibble`, `read_ready`,
  `write_ready`, `read_valid`, `read_nibble`, `irq`, `fault`) and `uio_*` split
  per pin. `command_accepted`, `image_valid<i>`, `running<i>`, `tx_level<i>`
  and `rx_level<i>` are debug outputs of the processor, for engine i.

The simulated circuit is `Processor.create_refinement_model ~debug:true` for
the design of record's architecture (`Config.default`: four engines, 32-bit
datapath, 64-word images, 8-word queues). It is built by the same generator
code as `src/protocol_emulator_core.v`, with a synchronous memory model in place
of the SRAM macro instances, which Cyclesim cannot simulate.
`expect/harness.ml` drives the processor's own port wires from typed
`[@@deriving hardcaml]` interfaces and provides host-port helpers (`command`,
`load`, `read_status`) that follow the sequences in `docs/info.md`.
Instructions are encoded by `Isa.encode` from their mnemonics.
