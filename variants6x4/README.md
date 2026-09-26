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
| `config.overlay.json` | RFC 7386 JSON merge patch applied to `src/config.json`: the 6x4 macro placement (`MACROS` instances) and one `FP_OBSTRUCTIONS` box, with `PL_TARGET_DENSITY_PCT` and `FP_MACRO_HORIZONTAL_HALO` restated at their signed-off values. Every other key comes from `src/config.json`. |
| `info.overlay.json` | `info.yaml` change: `tiles` `"6x4"` (and the comment on that line). |
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
