# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Deterministic memory image for the third-party SPI flash model (vendor/picorv32-spiflash).

``python3 flash_image.py <out.hex>`` writes the image in $readmemh format (one
byte per line); the tests import ``flash_bytes()`` to know the expected data.
The first bytes are an ASCII banner, the rest SHA-256 counter-mode bytes, so
that every address holds a distinct, non-trivial value.
"""

from __future__ import annotations

import hashlib
import sys

SIZE = 4096
BANNER = b"protocol-emulator independent-peer flash image\n"


def flash_bytes() -> bytes:
    data = bytearray(BANNER)
    counter = 0
    while len(data) < SIZE:
        data += hashlib.sha256(b"test_ext/spiflash:%d" % counter).digest()
        counter += 1
    return bytes(data[:SIZE])


def main() -> None:
    with open(sys.argv[1], "w", encoding="ascii") as out:
        out.write("".join(f"{b:02x}\n" for b in flash_bytes()))


if __name__ == "__main__":
    main()
