# Hardcaml sources

This directory is the generator of the chip: `scripts/generate.sh` builds
`bin/generate_refinement.exe` and writes `src/protocol_emulator_core.v` from
`configs/instruction-sram-32.json`, and the RTL variants of `docs/variants.md`
from `configs/variants/*.json`. CI (`.github/workflows/regen.yaml`) fails if
the committed core differs from a fresh generation. The tests are described in
[test/README.md](test/README.md).

```bash
dune build --root hardcaml             # library, generators and tests
scripts/generate.sh                    # src/protocol_emulator_core.v
scripts/gen_variants.sh                # every variant, with its equality checks
```

The OCaml packages are pinned in `scripts/opam-deps.txt`. The library uses
`ppx_jane` derivers (`compare`, `enumerate`, `equal`, `sexp_of`), which are in
hardcaml's own dependency closure; it does not use `ppx_hardcaml`, which is a
test-only package, so its two Hardcaml interfaces (`Engine.I` and
`Engine_line.Registers`) are written out by hand.

## Executables (`bin/`)

| Executable | Output |
|------------|--------|
| `generate_refinement` | The chip with the instruction-SRAM macros (`Processor.create_refinement`): `src/protocol_emulator_core.v` and the variant cores |
| `generate` | The register-store top of an architecture config (`Processor.create`) |
| `generate_formal` | Standalone FIFO, engine and debug-processor circuits for `formal/` |
| `generate_refinement_formal` | The SRAM adapter, an engine with its SRAM, and the debug processor, for `formal/` |
| `generate_payload_sram` | The payload SRAM adapter (not part of the chip) |
| `assemble` | Firmware images: `--firmware NAME` (built-ins of `Firmware`) or `--source FILE` |

## Library (`lib/`)

Every module has an `.mli` with its contract.

### Instruction set

| Module | Contents |
|--------|----------|
| `Opcode` | The 34 opcodes as a variant: encoding (`to_int`), mnemonic, and whether the line unit is needed |
| `Host_command` | The 12 host commands of window 0 as a variant |
| `Issue` | `Scalar` or `Fused` (the architecture's `"issue"` field) |
| `Isa` | The assembler's encoder: operand rules per opcode, written from `docs/isa.md`; timing annotations |
| `Assembler` | Firmware source JSON to image JSON (labels, targets, encoding) |
| `Firmware` | The built-in example programs (`assemble --firmware`) |

### Configuration

| Module | Contents |
|--------|----------|
| `Config` | The architecture (engines, widths, queue depth, issue) |
| `Refinement_config` | A refinement config: architecture plus the three option sets below |
| `Variant_options` | RTL variant knobs (reset style, PC width, shifts, ...; `docs/variants.md`) |
| `Timing_options` | Behaviour-preserving restructurings (`docs/timing-closure.md`) |
| `Line_options` | The line-unit extension (`docs/extension.md`) |

### Hardware

| Module | Contents |
|--------|----------|
| `Processor` | The top: host command decode, engines, queues, mover, mailboxes, triggers, pin ownership, instruction memories |
| `Host` | The nibble-serial host port |
| `Fifo` | The TX/RX queues and their timing-knob forms |
| `Instruction_sram`, `Payload_sram` | Wrappers of the IHP SRAM macros, with a Cyclesim model sharing the same control logic |
| `Engine` | One engine: issue, stalls, faults, the execute stage and the control tree; its inputs as the interface `Engine.I` |
| `Engine_datapath` | The engine's architectural registers and register-transfer helpers (register file, pin writes, shifts, START values) |
| `Engine_decode` | Operand fields of the instruction word and the validity rule of each opcode |
| `Engine_transfer` | XFER: issue and the per-edge transfer body |
| `Engine_line` | The line unit (only with `Line_options.enabled`): its register interface and its part of the datapath |
| `Line_unit` | The CRC step and the seeded defects used by the formal negative controls |
| `Instruction_sram_formal` | Verification wrappers around the production functions |

### How `Engine.create` is organised

`Engine.create` builds one engine in this order: the registers
(`Engine_datapath.create`, and `Engine_line.Registers.create` with the line
unit); the decoder (`Engine_decode.create`), which gives the operand fields
and `valid`; the issue and stall signals; the execute stage, a function
`Opcode.t -> Always.t list option` whose results form the `switch` on the
opcode; the XFER bodies (`Engine_transfer`); and the line unit
(`Engine_line.create`). It then compiles one `Always` tree for all of the
engine's registers: a fault clear, then STOP, then START
(`Engine_datapath.start` and the line unit's `on_start`), then, while the
engine runs without a fault, the WAIT countdown, a transfer in progress, or
the next instruction. With the timing option `split_instruction_decode` the
completed-instruction counter (`completed`) is left out of that tree and has
a second `compile` of its own.

Every per-opcode function matches on `Opcode.t` without a wildcard:
`Engine_decode`'s rule, `Engine`'s execute stage, `Isa.encode` and
`Opcode.mnemonic`. In dune's default (dev) profile, which `scripts/generate.sh`
and CI use, a non-exhaustive match (warning 8) is an error, so an opcode added to
`Opcode.t` fails the build until it has a validity rule, an execute body, an
encoder rule and a mnemonic. `Processor`'s command acceptance rule matches on
`Host_command.t` in the same way. A rule or body is `None` when the opcode is
not implemented in the configuration (XFER's rule with scalar issue, the
line-unit opcodes without the unit); the decoder then treats the opcode as
invalid (fault code 1).

## Generated Verilog and the structure of the source

The generated Verilog is one module (plus the 8 SRAM macro instances, two per
engine, in the cores with the instruction SRAM); it has no other hierarchy. Named registers keep
their names (`pc`, `transfer_edges`, `line_run`, ...), with a numeric suffix
when several signals have the same name (one per engine). Other signals are named `_N`, where
`N` is the signal's uid after `Circuit.create_exn` renumbers the graph
(`Signal_graph.normalize_uids`, on by default in Hardcaml v0.17.0): wires
first, then a depth-first walk of each wire's driver. The numbers and the
order of the declarations therefore depend on the circuit graph (which nodes
exist, which are shared, operand order, names), not on the order in which the
OCaml code creates the signals. A source change keeps the Verilog byte for
byte when it builds the same graph; for example, splitting `Engine.create`
into the `Engine_*` modules, and building the register banks with `map`,
created the signals in a different order and left every generated file
unchanged. Changes that do alter the graph, and so the Verilog, include
sharing one node where the code built two (or the reverse), reordering a
`switch`'s cases or an `if_`'s branches, and adding a hierarchy level.

Keeping the Verilog of the signed-off design unchanged is a rule of this
repository, so the source follows the existing graph in a few places:

- `finish` (the "instruction completes" assignments) is built once and shared
  by every execute body; `JMP` builds its own copy (`retire`).
- `Engine_datapath.write_register`, `write_pin`, `shift_tx` and the decode
  rules build their logic at each use, as before the split.
- XFER's body is elaborated with scalar issue too (the decoder rejects it
  there).
- `Engine.next_pc` reads the PC register's D input from the compiled register
  (`Signal.Type.Reg`) instead of rebuilding the next-PC logic.
