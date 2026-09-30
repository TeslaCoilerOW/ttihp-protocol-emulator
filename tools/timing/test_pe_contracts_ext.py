# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Tests of pe_contracts_ext (run: python3 -m unittest tools/timing/test_pe_contracts_ext.py).

The committed SWD, WS2812B, PS/2 and 1-Wire images pass every contract check;
each negative control changes one instruction of an image (the notes stay as
declared) and must make the named check fail.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("PE_TIMING_REPO", HERE.parent.parent)).resolve()
sys.path.insert(0, str(HERE))

import pe_contracts as C  # noqa: E402
import pe_contracts_ext as X  # noqa: E402
import pe_timing as T  # noqa: E402

FIRMWARE = REPO / "firmware"
IMAGES = sorted(X.FAMILY_CHECKS)


def program(name: str, patch: dict[int, tuple[str, int]] | None = None,
            notes: tuple[str, str] | None = None) -> T.Program:
    """The committed image, optionally with patched fields: {pc: (field, value)},
    field one of imm (imm24), a, b, c, and one text replacement in its notes
    (old, new). A patched image carries no identity hashes."""
    data = json.loads((FIRMWARE / f"{name}.image.json").read_text())
    if notes:
        assert any(notes[0] in n for n in data["notes"]), notes
        data["notes"] = [n.replace(*notes) for n in data["notes"]]
        patch = patch or {}
    words = list(data["words"])
    for pc, (field, value) in (patch or {}).items():
        w = words[pc]
        if field == "imm":
            w = (w & 0xFF000000) | value
        else:
            shift = {"a": 16, "b": 8, "c": 0}[field]
            w = (w & ~(0xFF << shift)) | value << shift
        words[pc] = w
    timing = None if patch else data["timing"]
    return T.Program(name=data["name"], arch=T.Arch.from_json(data["architecture"]), engine=data["engine"],
                     ownership=data["owned_pins"], open_drain=data["open_drain"], words=tuple(words),
                     labels=data["labels"], clock_hz=data["clock_hz"], timing=timing, notes=data["notes"],
                     path=None if patch else str(FIRMWARE / f"{name}.image.json"),
                     bytecode_sha256=None if patch else data["bytecode_sha256"],
                     source_sha256=None if patch else data["source_sha256"])


def statuses(name: str, patch: dict | None = None, notes: tuple[str, str] | None = None) -> dict[str, str]:
    an = T.Analysis(program(name, patch, notes))
    return {c["id"]: c["status"] for c in C.check_program(an, name=name)}


def pc_of(name: str, mnemonic: str, nth: int = 0, **operands: int) -> int:
    source = json.loads((FIRMWARE / f"{name}.source.json").read_text())
    hits = [pc for pc, i in enumerate(source["instructions"]) if i["mnemonic"] == mnemonic
            and all(i.get(k, 0) == v for k, v in operands.items())]
    return hits[nth]


@unittest.skipUnless(all((FIRMWARE / f"{n}.image.json").exists() for n in IMAGES), "images not present")
class CommittedImages(unittest.TestCase):
    def test_every_check_passes(self) -> None:
        for name in IMAGES:
            with self.subTest(image=name):
                got = statuses(name)
                fam = [k for k in got if k.split("-")[0] in ("swd", "ws2812", "ps2", "onewire")]
                self.assertGreaterEqual(len(fam), 4, got)
                self.assertEqual([k for k, v in got.items() if v in ("FAIL", "WARN")], [], got)
                self.assertTrue(T.Analysis(program(name)).is_exact())


@unittest.skipUnless(all((FIRMWARE / f"{n}.image.json").exists() for n in IMAGES), "images not present")
class NegativeControls(unittest.TestCase):
    def fails(self, name: str, patch: dict, check: str, notes: tuple[str, str] | None = None,
              passes: str | None = None) -> None:
        got = statuses(name, patch, notes)
        self.assertEqual(got.get(check), "FAIL", f"{name} {patch} {notes}: {check} is {got.get(check)}")
        if passes:
            self.assertEqual(got.get(passes), "PASS", f"{name} {patch} {notes}: {passes} is {got.get(passes)}")

    def test_swd(self) -> None:
        first = pc_of("swd-read", "XFER", a=32, b=16, c=8)
        self.fails("swd-read", {first: ("a", 8)}, "swd-sequence")             # 32 fewer high cycles
        self.fails("swd-read", {first: ("b", 8)}, "swd-clock")                # shorter half period
        header = pc_of("swd-read", "XFER", a=8, b=16, c=8)
        self.fails("swd-read", {header: ("b", 8)}, "swd-host-drive")          # header bits: 8-clock set-up
        idle = pc_of("swd-read", "XFER", 1, a=8, b=16, c=0)
        self.fails("swd-read", {idle: ("a", 4)}, "swd-clock-after-data")     # 5 rises after the parity bit
        self.fails("swd-read", {idle: ("a", 4)}, "swd-sequence")

    def test_ws2812(self) -> None:
        for name, wait in (("ws2812", 18), ("ws2812b-v5", 13)):
            first = pc_of(name, "WAIT", imm=wait)
            self.fails(name, {first: ("imm", wait + 8)}, "ws2812-bit-schedule")   # T0H of 23 bits changed
        # The same schedules judged against the other datasheet table.
        self.fails("ws2812b-v5", {}, "ws2812-datasheet-bits", notes=("WS2812B-V5 datasheet V1.0", "WS2812B datasheet"),
                   passes="ws2812-bit-schedule")
        self.fails("ws2812", {}, "ws2812-datasheet-bits", notes=("Worldsemi WS2812B datasheet", "Worldsemi WS2812B-V5"),
                   passes="ws2812-bit-schedule")
        self.fails("ws2812", {pc_of("ws2812", "WAIT", imm=15000): ("imm", 2000)}, "ws2812-reset")

    def test_ps2(self) -> None:
        self.fails("ps2-device", {pc_of("ps2-device", "WAIT", imm=1998): ("imm", 2600)}, "ps2-frame-timing")
        self.fails("ps2-device", {pc_of("ps2-device", "COUNT", imm=55): ("imm", 40)}, "ps2-idle-before-frame")
        self.fails("ps2-device", {pc_of("ps2-device", "WAIT", imm=995): ("imm", 1400)}, "ps2-frame-timing")

    def test_ps2_host(self) -> None:
        inhibit = pc_of("ps2-host", "WAIT", imm=5998)
        self.fails("ps2-host", {inhibit: ("imm", 3998)}, "ps2-host-request")      # schedule != declaration
        self.fails("ps2-host", {inhibit: ("imm", 3998)}, "ps2-host-request",      # 80 us < 100 us
                   notes=("CLK low 6000 clocks", "CLK low 4000 clocks"))
        self.fails("ps2-host", {pc_of("ps2-host", "LIMIT", imm=1100000): ("imm", 900000)}, "ps2-host-handshake",
                   notes=("by LIMIT 1100000", "by LIMIT 900000"))                 # 18 ms < 20 ms response time

    def test_onewire(self) -> None:
        self.fails("onewire-master", {pc_of("onewire-master", "WAIT", imm=24998): ("imm", 23000)}, "onewire-reset")
        self.fails("onewire-master", {pc_of("onewire-master", "WAIT", imm=398): ("imm", 500)}, "onewire-schedule")
        # Sampling at 16 us, declared as such: the schedule matches, tRDV (15 us) does not.
        self.fails("onewire-master", {pc_of("onewire-master", "WAIT", imm=398): ("imm", 498),
                                      pc_of("onewire-master", "WAIT", imm=2548): ("imm", 2448)},
                   "onewire-slot", notes=("DQ sampled 700 clocks", "DQ sampled 800 clocks"), passes="onewire-schedule")
        # A 55 us write-0 low time, declared as such: tLOW0 (60 us) fails.
        self.fails("onewire-master", {pc_of("onewire-master", "WAIT", imm=2548): ("imm", 2048),
                                      pc_of("onewire-master", "WAIT", imm=497): ("imm", 997)},
                   "onewire-slot", notes=("releases DQ at 3250 clocks", "releases DQ at 2750 clocks"),
                   passes="onewire-schedule")


class Limits(unittest.TestCase):
    def test_judge_and_clock_range(self) -> None:
        r = X.judge("T0H", 20, X.WS2812B["T0H"], 50e6)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["f_min"], 20 / 550e-9)
        self.assertAlmostEqual(r["f_max"], 20 / 250e-9)
        self.assertFalse(X.judge("T0H", 20, X.WS2812B_V5["T0H"], 50e6)["ok"])

    def test_swd_headers(self) -> None:
        self.assertTrue(X.header_ok(0xA5))
        self.assertTrue(X.header_ok(0x8D))
        self.assertFalse(X.header_ok(0xA5 ^ 0x20))                           # parity bit flipped


if __name__ == "__main__":
    unittest.main()
