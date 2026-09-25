# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""MicroPython: the pure-Python SHA-256 fallback equals hashlib.sha256 and
verifies firmware images when forced.

    micropython sha_check.py HOST_DIR FIRMWARE_DIR IMAGE...
"""

import hashlib
import sys

sys.path.insert(0, sys.argv[1])
from pe_host import image  # noqa: E402

for n in (0, 1, 55, 56, 63, 64, 65, 119, 120, 200, 1000):
    data = bytes((i * 7 + n) & 255 for i in range(n))
    if image.sha256_py(data) != hashlib.sha256(data).digest():
        raise SystemExit("sha256_py differs for length %d" % n)
image._native_sha256 = None          # force the fallback for image verification
for name in sys.argv[3:]:
    loaded = image.FirmwareImage.load(image.join(sys.argv[2], name + ".image.json"),
                                      source="required")
    if not loaded.source_verified:
        raise SystemExit("source not verified: " + name)
print("SHA_OK", len(sys.argv) - 3)
