# Vendored hard-macro views

`RM_IHPSG13_1P_64x16_c2/` holds the physical and timing views of the IHP
64-word x 16-bit single-port SRAM. `protocol_emulator_core` instantiates eight
of them as private instruction memories: `instruction_sram_e<engine>_<lo|hi>`.
`src/config.json` (`MACROS`) consumes these files. `src/sram_pdn_cfg.tcl` checks
the power hookup, and `macros/check_macro_floorplan.py` checks the placement
without running any EDA tools.

## Provenance

These are unmodified copies from IHP-Open-PDK
(<https://github.com/IHP-GmbH/IHP-Open-PDK>) at revision
`2bbec755dc67ca3db0261c3d6163e15735d66710`. That is the same revision that
`TinyTapeout/tt-gds-action@ihp-cmos5l` (`install_sg13cmos5l.sh`) installs as
the flow PDK. At that revision, `ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram` is a
symlink to `../../ihp-sg13g2/libs.ref/sg13g2_sram`, so the upstream paths are
`ihp-sg13g2/libs.ref/sg13g2_sram/{gds,lef,lib,cdl}/`.

| File | SHA-256 |
|---|---|
| `RM_IHPSG13_1P_64x16_c2.gds` | `0cc794e39c7c6006a7df329c97f73cb38ae6c94feb8761e7756b378cda4302a7` |
| `RM_IHPSG13_1P_64x16_c2.lef` | `c4f569509aabce950785903cc835312539e504a94d3a03579405e5b84b984bc3` |
| `RM_IHPSG13_1P_64x16_c2_typ_1p20V_25C.lib` | `4969c236a1fd96f2652d980c9b967798e2e0b3588c79c42fa90966f8926eac44` |
| `RM_IHPSG13_1P_64x16_c2_slow_1p08V_125C.lib` | `17c7ba8a5ea84c2a18a591052ba02d5e1008f6f8a4d2584692b9d9a01dcd6411` |
| `RM_IHPSG13_1P_64x16_c2_fast_1p32V_m55C.lib` | `9f67d502554b015a48a63d08b09102b3ba61c1dd1d345b4532795f9396b30ef6` |
| `RM_IHPSG13_1P_64x16_c2.cdl` | `240ef98547a85fa24378a13d5dbd46099b0f23962e953903ad60a81d98b3702b` |

We checked these on 2026-09-24. Each file was fetched from GitHub at that
revision, and each was byte-identical to the local `physical-assets-v4` PDK
copy.

We vendor the files instead of using `pdk_dir::` paths because
`tt-gds-action@ihp-cmos5l` is a moving branch. If it bumps the PDK, these
views stay put. The stripe alignment in `src/config.json` depends on the LEF's
power-column geometry. If a future LEF moves those columns, the checker in
`src/sram_pdn_cfg.tcl` stops the flow at the PDN step. Nothing is silently
mis-powered.

The simulation models (`RM_IHPSG13_1P_64x16_c2.v` and
`RM_IHPSG13_1P_core_behavioral.v`) live in `models/`. The interface-only
blackbox that synthesis sees is `models/blackbox/RM_IHPSG13_1P_64x16_c2.v`.

## License

Copyright 2025 IHP PDK Authors. Licensed under the Apache License 2.0; the full
text is in `LICENSE.IHP-Open-PDK`, which is identical to the upstream
repository's `LICENSE`.
