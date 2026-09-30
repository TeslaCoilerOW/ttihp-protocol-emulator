# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Paths shared by the host-library tests (CPython, stdlib unittest).

PE_HOST_TEST_DIR   directory holding model/, harness.py, scenarios.py, peers.py
                   (default <repo>/test)
PE_HOST_FIRMWARE_DIR  firmware images (default <test dir>/../firmware)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST = HERE.parent
REPO = HOST.parent
TEST_DIR = Path(os.environ.get("PE_HOST_TEST_DIR") or REPO / "test")
FIRMWARE = Path(os.environ.get("PE_HOST_FIRMWARE_DIR") or TEST_DIR.parent / "firmware")
SCENARIO = FIRMWARE / "flagship-scenario.json"
os.environ.setdefault("PE_HOST_TEST_DIR", str(TEST_DIR))

# host/ first (pe_host); the test/ directory last, so that test/'s own
# test_*.py modules can never shadow these (they need cocotb).
if str(HOST) not in sys.path:
    sys.path.insert(0, str(HOST))
if str(TEST_DIR) not in sys.path:
    sys.path.append(str(TEST_DIR))


def micropython_binary():
    """The MicroPython unix port: $PE_HOST_MICROPYTHON or 'micropython' on PATH."""
    explicit = os.environ.get("PE_HOST_MICROPYTHON")
    if explicit:
        return explicit if os.path.exists(explicit) else None
    return shutil.which("micropython")


def image_names():
    return sorted(p.name[:-len(".image.json")] for p in FIRMWARE.glob("*.image.json"))


def ext_image_names():
    """Images of firmware/ext/ (the line-unit extension, docs/extension.md)."""
    return sorted(p.name[:-len(".image.json")] for p in (FIRMWARE / "ext").glob("*.image.json"))


def selected_device(repo=REPO):
    """The design selection of a checkout (scripts/design_selection.sh variant):
    the device whose core its src/ carries. A checkout without the script
    predates the selection and carries the base design."""
    script = Path(repo) / "scripts" / "design_selection.sh"
    if not script.exists():
        return "base"
    return subprocess.run(["bash", str(script), "variant"], check=True, capture_output=True,
                          text=True).stdout.strip()
