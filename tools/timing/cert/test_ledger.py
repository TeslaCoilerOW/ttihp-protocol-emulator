# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the certificate ledger's staleness check (no solver needed).

python3 -m unittest discover -s tools/timing/cert -p 'test_*.py'

The tests copy the certified images, src/ files and SRAM models into a
temporary tree and check it against the committed ledger. src/ holds the core
of the design selection (configs/design-selection.txt); when that is not the
RTL the ledger's campaigns certified (a branch whose selection names a design
variant), the tests use a copy of the ledger whose campaigns record the tree's
RTL hashes, so that they test the checker's logic, not which design the
checkout carries (`ledger.py check` on the checkout reports that).
"""

from __future__ import annotations

import contextlib
import io
import json
import re
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


def ledger_for_tree(root: Path) -> Path | None:
    """A copy of the ledger whose campaigns record root's src/ hashes, written
    to root/ledger-for-tree.json, or None when the ledger already records them
    for every campaign."""
    led = L.load_ledger()
    src = {f: L.sha_file(root / f) for f in L.RTL_FILES}
    if all(camp["rtl"].get(f) == src[f] for camp in led["campaigns"].values() for f in L.RTL_FILES):
        return None
    for camp in led["campaigns"].values():
        camp["rtl"] = dict(camp["rtl"], **src)
    path = root / "ledger-for-tree.json"
    path.write_text(json.dumps(led))
    return path


def fv_ports(widths: dict[str, int]) -> str:
    """A processor_fv.v port list with the given widths."""
    body = "".join(f"    output [{w - 1}:0] {p};\n" if w > 1 else f"    output {p};\n" for p, w in widths.items())
    return "module protocol_processor_fv (\n);\n" + body + "endmodule\n"


def harness_ports() -> dict[str, int]:
    """Port -> width of every connection of cert_dut.vh's instance."""
    text = (HERE / "cert_dut.vh").read_text()
    wires = L._widths(text, r"\bwire\s*(?:\[(\d+):0\])?\s*([A-Za-z_][\w\s,]*?);")
    inst = re.search(r"protocol_processor_fv\s+\w+\s*\((.*?)\);", text, re.S).group(1)
    return {port: wires.get(wire, 1) for port, wire in re.findall(r"\.(\w+)\s*\(\s*(\w+)\s*\)", inst)}


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.names = certified_tree(self.root)
        if not self.names:
            self.skipTest("no image of the working tree matches a certified ledger record")
        self.real_ledger = L.LEDGER
        adjusted = ledger_for_tree(self.root)
        if adjusted is not None:
            L.LEDGER = adjusted

    def tearDown(self):
        L.LEDGER = self.real_ledger
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


class DesignTest(unittest.TestCase):
    """Which design the certificates are for: the harness interface check of
    `plan --rtl` and the note of `check` for a variant design selection."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def rtl(self, widths: dict[str, int]) -> Path:
        d = self.root / "rtl"
        d.mkdir(exist_ok=True)
        (d / "processor_fv.v").write_text(fv_ports(widths))
        return d

    def test_interface_of_the_design_of_record_fits(self):
        ports = harness_ports()
        self.assertEqual(ports["fv_pc"], 96)
        self.assertEqual(L.interface_problems(self.rtl(ports)), [])

    def test_interface_mismatch_is_named(self):
        ports = harness_ports()
        # a design variant's formal RTL: 7-bit PCs, a 7-bit transfer mode, no fv_sync1
        ports.update(fv_pc=28, fv_transfer_mode=28)
        del ports["fv_sync1"]
        problems = L.interface_problems(self.rtl(ports))
        self.assertEqual(len(problems), 3, problems)
        self.assertTrue(any(p.startswith("fv_pc: 28 bits") for p in problems), problems)
        self.assertIn("port fv_sync1 missing", problems)
        self.assertEqual(L.interface_problems(self.root / "none"), [f"{self.root / 'none' / 'processor_fv.v'} not found"])

    def test_plan_refuses_an_rtl_the_harness_does_not_fit(self):
        changed = self.root / "changed.txt"
        changed.write_text("hardcaml/lib/engine.ml\n")
        ports = harness_ports()
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = L.main(["plan", "--repo", str(self.root), "--changed", str(changed),
                         "--rtl", str(self.rtl(dict(ports, fv_pc=28)))])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("cannot plan certificate proofs", err.getvalue())
        self.assertIn("fv_pc: 28 bits", err.getvalue())

    def test_check_names_a_variant_design_selection(self):
        (self.root / "firmware").mkdir()
        (self.root / "src").mkdir()
        shutil.copytree(REPO / "models", self.root / "models")
        for f in L.RTL_FILES:
            (self.root / f).write_text("// another core\n")
        led = L.load_ledger()
        name = led["images"][0]["image"]
        shutil.copy(REPO / "firmware" / f"{name}.image.json", self.root / "firmware")
        (self.root / "configs").mkdir()
        for selection, note in (("configs/variants/diet8_rec16.json", True), (L.BASE_CONFIG, False)):
            (self.root / "configs" / "design-selection.txt").write_text(f"# comment\n{selection}\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = L.main(["check", "--repo", str(self.root), "--no-obligations"])
            self.assertEqual(rc, 1)
            self.assertEqual(f"names `{selection}`, not the design of record" in out.getvalue(), note)
            self.assertEqual("campaign.sh certify $WORK` (after committing" in out.getvalue(), not note)


if __name__ == "__main__":
    unittest.main()
