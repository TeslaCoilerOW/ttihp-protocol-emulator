"""Stdlib-only ISA2 reference model, host recorder and wire scoreboards.

Copied (not imported) from the asic-lab monorepo so this Tiny Tapeout
repository is self-contained. Source: projects/protocol-emulator/python/
protocol_emulator/ at commit 18676a4. Each file carries a provenance header;
bodies are unchanged. SHA-256 of the upstream files at copy time:

  reference.py        a79ce1d6575b1a8d62583b02bfadde451a8d6a712725e7f35b2235bf17bedab0
  host.py             c8471be046d251e0aaf1eb90cbcc1834a424f678bb8362d8ab1f54e802e50e66
  scoreboards.py      2aaa9988156076cfdf55e04b1ff23805d730212998dc5e44476f72a36a99f9a1
  verification.py     0df528f6dbd9042f022e1b17e36d5d28c308828f81876511bd740cbcc05bea1f
  revision2.py        aea569dc0dd8b36863edc9e9adbb57a7a4bc45a59c9c49952469e580aaba71bc
  frozen_firmware.py  966bd5ab19c73e9b56aa7a8e83b4355be6eed416bb282592626d08916a0c4d13
  jtag.py             230db64ed2d4838c38dbe38a83b8aa34b9ebe343a373f2a036e8323e51a75835

verification.py locates firmware images at <repo>/firmware, which resolves to
this repository's firmware/ directory from test/model/.

Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""
