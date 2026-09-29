"""Unit tests for check_consistency.py (standard library only).

Each test builds a small git repository in a temporary directory, so the
checks run against known history, documents and workflows.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_consistency as cc  # noqa: E402

REAL_CONFIG = json.loads((HERE / "consistency.json").read_text(encoding="utf-8"))

WORKFLOW_PUSH = """name: equivalence
on:
  push:
  workflow_dispatch:
jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - run: python3 formal_eq/eq_check.py check
"""

WORKFLOW_MANUAL = """name: deep
on:
  workflow_dispatch:
jobs:
  deep:
    runs-on: ubuntu-24.04
    steps:
      - run: formal_depth/run.sh local
"""


def config(**over) -> dict:
    cfg = json.loads(json.dumps(REAL_CONFIG))
    cfg["strict_docs"] = ["README.md", "docs/bug-ledger.md", "docs/results.md", "docs/limitations.md"]
    cfg.update(over)
    return cfg


class RepoCase(unittest.TestCase):
    """A temporary git repository with helpers to write files and commit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", HOME=str(self.root))
        self.env = env
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        self.git("config", "commit.gpgsign", "false")
        self.write("README.md", "# Test\n")
        self.write("tools/timing/report/checks.json",
                   json.dumps({"images": {"a": [{"id": "x", "status": "PASS"}]}}))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str) -> str:
        out = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                             text=True, env=self.env, check=True)
        return out.stdout.strip()

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def commit(self, msg: str = "c") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "--short=7", "HEAD")

    def run_checks(self, *only: str, cfg: dict | None = None) -> list[cc.Finding]:
        chk = cc.Checker(self.root, cfg or config())
        return chk.run(set(only) if only else None)

    @staticmethod
    def errors(findings, check: str | None = None):
        return [f for f in findings if f.severity == cc.ERROR and (check is None or f.check == check)]

    @staticmethod
    def warnings(findings, check: str | None = None):
        return [f for f in findings if f.severity == cc.WARNING and (check is None or f.check == check)]


LEDGER = """# Bug ledger

| ID | Class | Defect | Found by | Fix | Status (checked) |
|---|---|---|---|---|---|
| BL-1 | Firmware | a | b | {fix} | {status} |

## Details

### BL-1: something (firmware, {heading})

text
"""


class LedgerTests(RepoCase):
    def ledger(self, fix: str, status: str, heading: str = "fixed") -> None:
        self.write("docs/bug-ledger.md", LEDGER.format(fix=fix, status=status, heading=heading))

    def test_open_row_with_fix_commit_fails(self) -> None:
        sha = self.commit("fix")
        self.ledger(f"`{sha}`", "**Open**", heading="open")
        errs = self.errors(self.run_checks("ledger"), "ledger")
        self.assertEqual(len(errs), 1)
        self.assertIn("marked Open", errs[0].message)

    def test_fixed_row_passes(self) -> None:
        sha = self.commit("fix")
        self.ledger(f"`{sha}`", "Fixed: evidence")
        self.assertEqual(self.errors(self.run_checks("ledger")), [])

    def test_partly_fixed_row_passes(self) -> None:
        sha = self.commit("fix")
        self.ledger(f"`{sha}` (part)", "Partly fixed: one item open", heading="partly fixed")
        self.assertEqual(self.errors(self.run_checks("ledger")), [])

    def test_open_row_without_commit_passes(self) -> None:
        self.commit()
        self.ledger("none", "**Open**", heading="open")
        self.assertEqual(self.errors(self.run_checks("ledger")), [])

    def test_unknown_fix_commit_fails(self) -> None:
        self.commit()
        self.ledger("`abcdef1`", "Fixed")
        errs = self.errors(self.run_checks("ledger"), "ledger")
        self.assertTrue(any("does not resolve" in e.message for e in errs))

    def test_heading_contradicts_table(self) -> None:
        sha = self.commit("fix")
        self.ledger(f"`{sha}`", "Fixed", heading="open")
        errs = self.errors(self.run_checks("ledger"), "ledger")
        self.assertTrue(any("heading says BL-1 is open" in e.message for e in errs))

    def test_shallow_checkout_fails_closed(self) -> None:
        self.commit()
        self.ledger("none", "Open", heading="open")
        chk = cc.Checker(self.root, config())
        chk.repo.shallow = True
        chk.check_ledger()
        self.assertTrue(any("shallow" in f.message for f in chk.findings))

    def test_status_word(self) -> None:
        self.assertEqual(cc.Checker.status_word("**Open** (WARN/INFO)"), "open")
        self.assertEqual(cc.Checker.status_word("Fixed in part: x"), "partly")
        self.assertEqual(cc.Checker.status_word("Partly fixed: x"), "partly")
        self.assertEqual(cc.Checker.status_word("Fixed; confirmed"), "fixed")


class PhraseTests(RepoCase):
    def test_banned_phrase_fails(self) -> None:
        self.write("docs/architecture.md", "The design has not completed physical signoff.\n")
        errs = self.errors(self.run_checks("phrases"), "phrases")
        self.assertEqual(len(errs), 1)

    def test_history_marker_exempts(self) -> None:
        self.write("docs/architecture.md",
                   "Superseded: as first written, it said the design has not completed physical signoff.\n")
        self.assertEqual(self.errors(self.run_checks("phrases")), [])

    def test_ledger_header_phrase(self) -> None:
        self.write("docs/bug-ledger.md", "| ID | Status at `c118027` |\n|---|---|\n| BL-1 | x |\n")
        errs = self.errors(self.run_checks("phrases"), "phrases")
        self.assertEqual(len(errs), 1)


class TimingTests(RepoCase):
    def counts(self, **n: int) -> None:
        checks = []
        for status, k in n.items():
            checks += [{"id": f"{status}{i}", "status": status} for i in range(k)]
        self.write("tools/timing/report/checks.json", json.dumps({"images": {"img": checks}}))

    def test_stale_claim_in_strict_doc_fails(self) -> None:
        self.counts(PASS=3, INFO=1)
        self.write("docs/results.md", "| R40 | 2 PASS, 1 FAIL, 0 WARN, 1 INFO | x |\n")
        errs = self.errors(self.run_checks("timing"), "timing")
        self.assertEqual(len(errs), 1)

    def test_superseded_claim_passes(self) -> None:
        self.counts(PASS=3, INFO=1)
        self.write("docs/results.md", "| R40 | **Superseded by R40b**. 2 PASS, 1 FAIL, 0 WARN | x |\n")
        self.assertEqual(self.errors(self.run_checks("timing")), [])

    def test_current_claim_passes(self) -> None:
        self.counts(PASS=3, INFO=1)
        self.write("docs/results.md", "| R40b | 3 PASS, 0 FAIL, 0 WARN, 1 INFO | x |\n")
        self.assertEqual(self.run_checks("timing"), [])

    def test_zero_fail_claim_with_fail_rows(self) -> None:
        self.counts(PASS=2, FAIL=1)
        self.write("docs/other.md", "Superseded or not: 3 PASS, 0 FAIL.\n")
        errs = self.errors(self.run_checks("timing"), "timing")
        self.assertEqual(len(errs), 1)
        self.assertIn("claims 0 FAIL", errs[0].message)

    def test_non_strict_doc_warns(self) -> None:
        self.counts(PASS=3)
        self.write("docs/other.md", "The report gave 2 PASS, 1 FAIL.\n")
        f = self.run_checks("timing")
        self.assertEqual(self.errors(f), [])
        self.assertEqual(len(self.warnings(f, "timing")), 1)


def image(name: str, digest: str) -> str:
    return json.dumps({"name": name, "bytecode_sha256": digest})


class CertTests(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("firmware/a.image.json", image("a", "aa"))
        self.write("firmware/b.image.json", image("b", "bb"))
        self.base = self.commit("certified")

    def summary(self, images: list[dict]) -> None:
        self.write("tools/timing/cert/results/summary.json",
                   json.dumps({"images": images, "totals": {"segments": 368}}))

    def cfg(self, **over) -> dict:
        c = config()
        c["certificates"] = dict(c["certificates"], certified_commit=self.base, **over)
        return c

    def test_unchanged_images_pass(self) -> None:
        self.summary([{"image": "a"}, {"image": "b"}])
        self.assertEqual(self.run_checks("certs", cfg=self.cfg()), [])

    def test_changed_image_warns_without_hashes(self) -> None:
        self.summary([{"image": "a"}, {"image": "b"}])
        self.write("firmware/a.image.json", image("a", "a2"))
        f = self.run_checks("certs", cfg=self.cfg())
        self.assertEqual(self.errors(f), [])
        self.assertTrue(any("a: certified at" in w.message for w in self.warnings(f, "certs")))

    def test_changed_image_fails_when_enforced(self) -> None:
        self.summary([{"image": "a"}, {"image": "b"}])
        self.write("firmware/a.image.json", image("a", "a2"))
        errs = self.errors(self.run_checks("certs", cfg=self.cfg(enforce=True)), "certs")
        self.assertEqual(len(errs), 1)

    def test_recorded_hash_mismatch_fails(self) -> None:
        self.summary([{"image": "a", "bytecode_sha256": "aa"}, {"image": "b", "bytecode_sha256": "bb"}])
        self.assertEqual(self.errors(self.run_checks("certs", cfg=self.cfg())), [])
        self.write("firmware/a.image.json", image("a", "a2"))
        errs = self.errors(self.run_checks("certs", cfg=self.cfg()), "certs")
        self.assertEqual(len(errs), 1)
        self.assertIn("re-run the certificates", errs[0].message)

    def test_uncertified_image(self) -> None:
        self.summary([{"image": "a", "bytecode_sha256": "aa"}])
        errs = self.errors(self.run_checks("certs", cfg=self.cfg()), "certs")
        self.assertTrue(any("b: no certificate results" in e.message for e in errs))

    def test_headline_needs_scope_while_stale(self) -> None:
        self.summary([{"image": "a"}, {"image": "b"}])
        self.write("firmware/a.image.json", image("a", "a2"))
        self.write("README.md", "All 368 segments are certified.\n")
        errs = self.errors(self.run_checks("certs", cfg=self.cfg()), "certs")
        self.assertTrue(any("headline without its scope" in e.message for e in errs))
        self.write("README.md", "All 368 segments are certified; image a awaits re-certification.\n")
        self.assertEqual(self.errors(self.run_checks("certs", cfg=self.cfg())), [])

    def test_qualification_stale_once_current(self) -> None:
        self.summary([{"image": "a"}, {"image": "b"}])
        self.write("README.md", "Two images await re-certification.\n")
        errs = self.errors(self.run_checks("certs", cfg=self.cfg()), "certs")
        self.assertTrue(any("await re-certification, but" in e.message for e in errs))


class LinkTests(RepoCase):
    def test_missing_relative_link_fails(self) -> None:
        self.write("docs/a.md", "See [b](b.md) and [c](c.md).\n")
        self.write("docs/b.md", "# B\n")
        errs = self.errors(self.run_checks("links"), "links")
        self.assertEqual([e.message for e in errs if "c.md" in e.message] != [], True)
        self.assertEqual(len(errs), 1)

    def test_code_spans_and_fences_are_ignored(self) -> None:
        self.write("docs/a.md", "`[x](missing.md)`\n\n```\n[y](missing.md)\n```\n")
        self.assertEqual(self.run_checks("links"), [])

    def test_link_above_root_is_ignored(self) -> None:
        self.write("README.md", "![b](../../workflows/gds/badge.svg)\n")
        self.assertEqual(self.run_checks("links"), [])

    def test_missing_anchor_warns(self) -> None:
        self.write("docs/a.md", "See [b](b.md#nope) and [c](b.md#two-words).\n")
        self.write("docs/b.md", "# B\n\n## Two words\n")
        f = self.run_checks("links")
        self.assertEqual(self.errors(f), [])
        self.assertEqual(len(self.warnings(f, "links")), 1)

    def test_blob_link_to_missing_path(self) -> None:
        url = REAL_CONFIG["repo_url"] + "/blob/main/docs/none.md"
        self.write("README.md", f"[x]({url})\n")
        self.assertEqual(len(self.errors(self.run_checks("links"), "links")), 1)

    def test_ignored_file_counts_as_missing(self) -> None:
        self.write(".gitignore", "build/\n")
        self.write("build/out.md", "x\n")
        self.write("docs/a.md", "[o](../build/out.md)\n")
        self.assertEqual(len(self.errors(self.run_checks("links"), "links")), 1)


class PathTests(RepoCase):
    def test_missing_backticked_path_in_strict_doc(self) -> None:
        self.write("tools/x.py", "")
        self.write("docs/results.md", "Run `tools/x.py` and `tools/y.py`.\n")
        errs = self.errors(self.run_checks("paths"), "paths")
        self.assertEqual(len(errs), 1)
        self.assertIn("tools/y.py", errs[0].message)

    def test_non_strict_doc_is_not_checked(self) -> None:
        self.write("docs/other.md", "Run `tools/y.py`.\n")
        self.assertEqual(self.run_checks("paths"), [])

    def test_generated_and_placeholder_paths_skipped(self) -> None:
        self.write("docs/results.md", "`formal/build/rtl/x.v`, `<work dir>/a`, `test/*.py`, `tools/a.py report`.\n")
        self.assertEqual(self.run_checks("paths"), [])

    def test_history_exempts_removed_file(self) -> None:
        self.write("docs/results.md", "It replaced `formal_eq/old.yaml` (removed in `abcdef1`).\n")
        self.assertEqual(self.run_checks("paths"), [])

    def test_workflow_names(self) -> None:
        self.write(".github/workflows/equiv.yaml", WORKFLOW_PUSH)
        self.write("docs/results.md", "The `equivalence` workflow, the `check` job and the `nosuch` workflow.\n"
                   "See `.github/workflows/equiv.yaml` and `.github/workflows/gone.yaml`.\n")
        errs = self.errors(self.run_checks("paths"), "paths")
        msgs = " ".join(e.message for e in errs)
        self.assertIn("`nosuch`", msgs)
        self.assertIn("gone.yaml", msgs)
        self.assertEqual(len(errs), 2)


class CiClaimTests(RepoCase):
    def test_not_in_ci_claim_contradicted(self) -> None:
        self.write(".github/workflows/equiv.yaml", WORKFLOW_PUSH)
        self.write("docs/results.md", "**Equivalence (`formal_eq/`, local; not in CI).** Text.\n")
        errs = self.errors(self.run_checks("ci-claims"), "ci-claims")
        self.assertEqual(len(errs), 1)
        self.assertIn("equiv.yaml", errs[0].message)

    def test_manual_workflow_does_not_count(self) -> None:
        self.write(".github/workflows/deep.yaml", WORKFLOW_MANUAL)
        self.write("docs/limitations.md", "`formal_depth/` is not in CI.\n")
        self.assertEqual(self.run_checks("ci-claims"), [])

    def test_does_not_run_list(self) -> None:
        self.write(".github/workflows/equiv.yaml", WORKFLOW_PUSH)
        self.write("docs/limitations.md",
                   "CI runs `formal/`. CI does not run `formal_depth/`, `formal_eq/` or the host tests.\n")
        errs = self.errors(self.run_checks("ci-claims"), "ci-claims")
        self.assertEqual(len(errs), 1)
        self.assertIn("formal_eq/", errs[0].message)

    def test_nearest_subject_only(self) -> None:
        self.write(".github/workflows/equiv.yaml", WORKFLOW_PUSH)
        self.write("docs/limitations.md", "`formal_eq/` runs in CI, `formal_depth/` is not in CI.\n")
        self.assertEqual(self.run_checks("ci-claims"), [])

    def test_triggers(self) -> None:
        self.assertEqual(cc.Checker.triggers("on: [push, workflow_dispatch]\n"), {"push", "workflow_dispatch"})
        self.assertEqual(cc.Checker.triggers(WORKFLOW_MANUAL), {"workflow_dispatch"})
        self.assertEqual(cc.Checker.triggers("on:\n  workflow_run:\n    workflows: [gds]\n"), {"workflow_run"})


class StatusRunTests(RepoCase):
    def test_current_run_with_changed_design(self) -> None:
        self.write("src/config.json", json.dumps({"//": "a", "CLOCK_PERIOD": 20}))
        sha = self.commit("design")
        self.write("README.md", f"| x | `{sha}`, run 36298635436 (current) |\n|---|---|\n")
        self.assertEqual(self.errors(self.run_checks("status-runs")), [])
        self.write("src/config.json", json.dumps({"//": "b", "CLOCK_PERIOD": 20}))
        self.assertEqual(self.errors(self.run_checks("status-runs")), [], "comment-only change")
        self.write("src/config.json", json.dumps({"//": "b", "CLOCK_PERIOD": 15}))
        errs = self.errors(self.run_checks("status-runs"), "status-runs")
        self.assertEqual(len(errs), 1)
        self.assertIn("src/config.json", errs[0].message)

    def test_superseded_cell_is_exempt(self) -> None:
        self.write("src/config.json", "{}")
        sha = self.commit("design")
        self.write("src/config.json", '{"A": 1}')
        self.write("README.md", f"| x | `{sha}`, run 36298635436 (current; superseded) |\n|---|---|\n")
        self.assertEqual(self.errors(self.run_checks("status-runs")), [])

    def test_in_progress_warns_offline(self) -> None:
        self.write("docs/other.md", "Run 36298635436 is still in progress.\n")
        f = self.run_checks("status-runs")
        self.assertEqual(len(self.warnings(f, "status-runs")), 1)

    def test_in_progress_online_completed_fails(self) -> None:
        self.write("docs/other.md", "Run 36298635436 is still in progress.\n")
        chk = cc.Checker(self.root, config(), online=True)
        chk._run_cache["36298635436"] = {"status": "completed", "conclusion": "success", "head_sha": "0" * 40}
        chk.check_status_runs()
        self.assertEqual(len([f for f in chk.findings if f.severity == cc.ERROR]), 1)


class CitationTests(RepoCase):
    def test_superseded_citation_warns(self) -> None:
        self.write("docs/results.md", "| R40 | **Superseded by R40b**. x |\n|---|---|\n| R40b | y |\n")
        self.write("docs/other.md", "As R40 shows.\n\nAs R40b shows.\n\nR40 at first, R40b now.\n")
        f = self.run_checks("citations")
        self.assertEqual([w.line for w in self.warnings(f, "citations")], [1])


class MarkdownTests(unittest.TestCase):
    def test_split_cells(self) -> None:
        self.assertEqual(cc.split_cells("| a | `b|c` | d\\|e |"), ["a", "`b|c`", "d|e"])

    def test_sentences_keep_lines(self) -> None:
        bl = cc.blocks("x.md", "First sentence here.\nSecond one `a.b` here. Third.\n")
        units = cc.units_of(bl[0])
        self.assertEqual([u.line for u in units], [1, 2, 2])

    def test_slugs(self) -> None:
        slugs = cc.github_slugs("# A `code` title\n## 9. Claims that are stale\n## Dup\n## Dup\n")
        self.assertTrue({"a-code-title", "9-claims-that-are-stale", "dup", "dup-1"} <= slugs)


class MainTests(RepoCase):
    def test_exit_codes(self) -> None:
        self.write("docs/bug-ledger.md", LEDGER.format(fix="none", status="Open", heading="open"))
        cfg_path = self.root / "cfg.json"
        cfg_path.write_text(json.dumps(config()), encoding="utf-8")
        self.commit()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cc.main(["--root", str(self.root), "--config", str(cfg_path), "--only", "ledger"])
        self.assertEqual(rc, 0, out.getvalue())
        self.write("docs/a.md", "[x](missing.md)\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cc.main(["--root", str(self.root), "--config", str(cfg_path), "--only", "links"])
        self.assertEqual(rc, 1)
        self.write("docs/a.md", "[x](b.md#nope)\n")
        self.write("docs/b.md", "# B\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cc.main(["--root", str(self.root), "--config", str(cfg_path), "--only", "links"]), 0)
            self.assertEqual(cc.main(["--root", str(self.root), "--config", str(cfg_path), "--only", "links",
                                      "--strict"]), 1)

    def test_missing_config(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cc.main(["--root", str(self.root), "--config", str(self.root / "no.json")]), 2)


class ConfigTests(unittest.TestCase):
    def test_patterns_compile(self) -> None:
        for p in REAL_CONFIG["history_markers"]:
            re.compile(p)
        for b in REAL_CONFIG["banned_phrases"]:
            re.compile(b["pattern"])
            self.assertTrue(b["reason"])
        for key in ("around_patterns", "after_patterns"):
            for p in REAL_CONFIG["ci_claims"][key]:
                re.compile(p)
        for key in ("headline_pattern", "qualification_pattern"):
            re.compile(REAL_CONFIG["certificates"][key].replace("{segments}", "368"))

    def test_no_local_paths(self) -> None:
        # Cluster and home paths must not reach the public repository; the
        # pattern is assembled so that this file passes the same scan.
        local = re.compile("/" + "orcd" + "/|/" + "home" + "/")
        for name in ("consistency.json", "check_consistency.py", "README.md"):
            self.assertIsNone(local.search((HERE / name).read_text(encoding="utf-8")), name)


if __name__ == "__main__":
    unittest.main()
