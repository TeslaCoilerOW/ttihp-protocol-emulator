# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the certificate ledger's staleness check (no solver needed).

python3 -m unittest discover -s tools/timing/cert -p 'test_*.py'

The tests copy the certified images, src/ files and SRAM models into a
temporary tree and check it against the committed ledger.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ledger as L  # noqa: E402

REPO = HERE.parents[2]


def certified_tree(dest: Path, limit: int = 3) -> list[str]:
    """A tree with up to ``limit`` images whose bytes match the ledger."""
    led = L.load_ledger()
    (dest / "firmware").mkdir()
    (dest / "src").mkdir()
    shutil.copytree(REPO / "models", dest / "models")
    for f in L.RTL_FILES:
        shutil.copy(REPO / f, dest / f)
    names = []
    for rec in led["images"]:
        path = REPO / "firmware" / f"{rec['image']}.image.json"
        if rec["certified"] and path.exists() and L.sha_file(path) == rec["image_sha256"]:
            shutil.copy(path, dest / "firmware" / path.name)
            names.append(rec["image"])
        if len(names) == limit:
            break
    return names


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.names = certified_tree(self.root)
        if not self.names:
            self.skipTest("no image of the working tree matches a certified ledger record")

    def tearDown(self):
        self.tmp.cleanup()

    def status(self, **kw) -> dict[str, dict]:
        rows, _ = L.check(self.root, None, True, **kw)
        return {r["image"]: r for r in rows}

    def test_certified_images_pass(self):
        rows = self.status()
        for name in self.names:
            self.assertEqual(rows[name]["status"], "certified", rows[name]["reasons"])

    def test_changed_image_is_stale(self):
        path = self.root / "firmware" / f"{self.names[0]}.image.json"
        data = json.loads(path.read_text())
        data["notes"] = list(data.get("notes", [])) + ["edited"]
        path.write_text(json.dumps(data))
        row = self.status()[self.names[0]]
        self.assertEqual(row["status"], "stale")
        self.assertTrue(any("image changed" in r for r in row["reasons"]), row["reasons"])

    def test_new_image_is_stale(self):
        src = self.root / "firmware" / f"{self.names[0]}.image.json"
        shutil.copy(src, self.root / "firmware" / "zz-new.image.json")
        row = self.status()["zz-new"]
        self.assertEqual(row["reasons"][0], "no certificate")

    def test_changed_rtl_makes_every_image_stale(self):
        core = self.root / "src" / "protocol_emulator_core.v"
        core.write_text(core.read_text() + "\n// edited\n")
        rows = self.status()
        for name in self.names:
            self.assertEqual(rows[name]["status"], "stale")

    def test_changed_obligation_is_stale(self):
        # A different generator output for the same image bytes (simulated by
        # a record digest that no longer matches) is caught by --obligations.
        led = L.load_ledger()
        rec = next(r for r in led["images"] if r["image"] == self.names[0])
        rec["obligations"] = dict(rec["obligations"], whole_sha256="0" * 64)
        ledger_file = self.root / "summary.json"
        ledger_file.write_text(json.dumps(led))
        old = L.LEDGER
        L.LEDGER = ledger_file
        try:
            row = self.status()[self.names[0]]
            self.assertTrue(any("generator" in r for r in row["reasons"]), row["reasons"])
            rows, _ = L.check(self.root, None, False)
            self.assertEqual({r["image"]: r for r in rows}[self.names[0]]["status"], "certified")
        finally:
            L.LEDGER = old

    def test_changed_lemma_is_stale(self):
        # The composition rests on the lemmas: a lemma file that differs from
        # the one its campaign proved flags every image of that campaign.
        led = L.load_ledger()
        rec = next(r for r in led["images"] if r["image"] == self.names[0])
        camp = led["campaigns"][rec["campaign"]]
        camp["harness"] = dict(camp["harness"], **{"boundary_lemmas.sv": "0" * 64})
        ledger_file = self.root / "summary.json"
        ledger_file.write_text(json.dumps(led))
        old = L.LEDGER
        L.LEDGER = ledger_file
        try:
            row = self.status()[self.names[0]]
            self.assertEqual(row["status"], "stale")
            self.assertTrue(any("boundary_lemmas.sv" in r for r in row["reasons"]), row["reasons"])
        finally:
            L.LEDGER = old

    def test_plan_budget(self):
        changed = self.root / "changed.txt"
        changed.write_text(f"firmware/{self.names[0]}.image.json\n")
        out, report = self.root / "plan.txt", self.root / "over.txt"
        for budget, want in ((100000, [self.names[0]]), (0, [])):
            with open(out, "w") as f:
                old, sys.stdout = sys.stdout, f
                try:
                    L.main(["plan", "--repo", str(self.root), "--changed", str(changed),
                            "--budget", str(budget), "--report", str(report)])
                finally:
                    sys.stdout = old
            self.assertEqual(json.loads(out.read_text()), want)
            self.assertEqual(bool(report.read_text()), not want)
        self.assertIn("exceed the CI budget of 0", report.read_text())

    def test_plan(self):
        changed = self.root / "changed.txt"
        changed.write_text(f"firmware/{self.names[0]}.image.json\nREADME.md\n")
        out = self.root / "plan.txt"
        with open(out, "w") as f:
            old, sys.stdout = sys.stdout, f
            try:
                L.main(["plan", "--repo", str(self.root), "--changed", str(changed)])
            finally:
                sys.stdout = old
        self.assertEqual(json.loads(out.read_text()), [self.names[0]])


if __name__ == "__main__":
    unittest.main()
