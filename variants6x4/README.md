# 6x4 fallback build (diet4)

This directory holds everything needed to build the `diet4` variant on a 6x4
tile footprint instead of the 8x4 design of record. Nothing here is used by the
8x4 flow: `src/`, `info.yaml` and `.github/workflows/gds.yaml` are unchanged.
The CI build is `.github/workflows/gds_6x4.yaml`. What differs from the 8x4
design, the local signoff results and the switching procedure are in
[`docs/6x4.md`](../docs/6x4.md).

| File | Role |
|---|---|
| `protocol_emulator_core.v` | The generated `diet4` core: module `protocol_emulator_core`, ISA version 3 (`docs/variants.md` section 4, `docs/isa.md`). It drops in behind the unchanged `src/project.v`. |
| `PROVENANCE.json` | Generator command, source commit and trees, config and core sha256, toolchain. |
| `check_core.sh` | Regenerates the core from `hardcaml/` + `configs/variants/diet4.json` into a temporary file and compares it with the committed copy (`--update` rewrites it). CI job `core_current`. |
| `config.overlay.json` | RFC 7386 JSON merge patch applied to `src/config.json` (a `null` value removes a key): the 6x4 macro placement (`MACROS` instances) and one `FP_OBSTRUCTIONS` box, `PL_TARGET_DENSITY_PCT` and `FP_MACRO_HORIZONTAL_HALO`, since `4bd30c8` the timing-repair, CTS and routing keys of optimizer promotion p014 (`docs/6x4.md` section 4b), and since `8a05de7` and `fdc23f2` all 33 keys that `tools/opt/space.py` treats as flow knobs (a value, or `null` for LibreLane's default; `tools/opt/test_opt.py` checks this; `docs/6x4.md` section 4c). Every other key comes from `src/config.json`. |
| `info.overlay.json` | `info.yaml` changes: `tiles` `"6x4"` (and the comment on that line), and since `fdc23f2` `clock_hz` 50000000, the 6x4 build's signed-off clock. |
| `switch.py` | `check` validates the overlays against the checkout; `apply` rewrites `src/protocol_emulator_core.v`, `src/config.json` and `info.yaml` in place. Standard library only. |
| `row_islands.py` | Tool-free model of `OpenROAD.CutRows` + the stripe lattice. It predicts the short VPWR/VGND straps that fail the precheck Pin check, derives the horizontal halo that prevents them, and models `FP_OBSTRUCTIONS` (`docs/6x4.md` section 2). |

Typical use:

```sh
python3 variants6x4/switch.py check            # consistency, nothing written
variants6x4/check_core.sh                      # needs the pinned OCaml toolchain
python3 variants6x4/switch.py apply            # switch this checkout (CI workspace)
python3 variants6x4/row_islands.py --floorplan floorplans --derive   # halo for every floorplan file
```

`switch.py apply` refuses a checkout whose `src/protocol_emulator_core.v` is
already a variant core. To go back, restore the three files from git.
