# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of retry_list.py, the rerun step of campaign.sh and ci_prove.sh
(no solver needed).

python3 -m unittest discover -s tools/timing/cert -p 'test_*.py'
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import retry_list as R  # noqa: E402


def result(d: Path, name: str, task: str, engine: str | None, status: str) -> None:
    (d / "results").mkdir(parents=True, exist_ok=True)
    tag = f"{name}.{task}" + (f".{engine}" if engine else "")
    (d / "results" / f"{tag}.json").write_text(json.dumps({"certificate": name, "task": task, "status": status}))


class RetryListTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.long = self.root / "long"
        self.certs = self.root / "certs"

    def tearDown(self):
        self.tmp.cleanup()

    def lists(self, **lines: list[str]) -> list[Path]:
        out = []
        for name, ls in lines.items():
            p = self.root / f"tasks_{name}.txt"
            p.write_text("".join(line + "\n" for line in ls))
            out.append(p)
        return out

    def test_error_is_rerun_with_yices_alone(self):
        result(self.long, "cert_a_n2_c0", "cover", "boolector+yices", "ERROR")
        result(self.long, "cert_a_n2_c1", "cover", "boolector+yices", "PASS")
        lists = self.lists(cover=[f"{self.long} cert_a_n2_c0 cover boolector+yices",
                                  f"{self.long} cert_a_n2_c1 cover boolector+yices"])
        self.assertEqual(R.retries(lists), [f"{self.long} cert_a_n2_c0 cover yices"])

    def test_a_better_result_is_kept(self):
        # the portfolio crashed, the yices rerun passed: nothing to do
        result(self.long, "cert_a_n2_c0", "cover", "boolector+yices", "ERROR")
        result(self.long, "cert_a_n2_c0", "cover", "yices", "PASS")
        # a negative control that failed (as required) is not an error
        result(self.root / "neg", "cert_a_n1_neg_pad_late", "bmc", None, "FAIL")
        lists = self.lists(cover=[f"{self.long} cert_a_n2_c0 cover boolector+yices"],
                           neg=[f"{self.root / 'neg'} cert_a_n1_neg_pad_late bmc"])
        self.assertEqual(R.retries(lists, missing=True), [])

    def test_missing_results_only_with_flag(self):
        lists = self.lists(chunks=[f"{self.long} cert_a_n2_c3 bmc"])
        self.assertEqual(R.retries(lists), [])
        self.assertEqual(R.retries(lists, missing=True), [f"{self.long} cert_a_n2_c3 bmc"])

    def test_missing_portfolio_run_is_repeated_with_yices_alone(self):
        # its Slurm task was killed (out of memory) before a result was written
        lists = self.lists(cover=[f"{self.long} cert_a_n2_c0 cover boolector+yices",
                                  f"{self.certs} cert_a_n3 cover yices"])
        self.assertEqual(R.retries(lists), [])
        self.assertEqual(R.retries(lists, missing=True),
                         [f"{self.long} cert_a_n2_c0 cover yices", f"{self.certs} cert_a_n3 cover yices"])

    def test_abc_runs_are_not_repeated(self):
        result(self.certs, "cert_a_n4", "bmc", "bmc3", "ERROR")
        lists = self.lists(whole=[f"{self.certs} cert_a_n4 bmc bmc3", f"{self.certs} cert_a_n5 bmc bmc3"],
                           neg=[f"{self.root / 'neg'} cert_a_n4_neg_post_limit bmc bmc3"])
        self.assertEqual(R.retries(lists, missing=True), [])

    def test_prefix_names_and_lemma_tasks_do_not_mix(self):
        # n2's results must not include n2_c0's; 'cover' must not match 'cover_k0'
        result(self.certs, "cert_a_n2", "cover", None, "ERROR")
        result(self.certs, "cert_a_n2_c0", "cover", None, "PASS")
        result(self.certs, "boundary_lemmas", "cover_k0", None, "PASS")
        lists = self.lists(cover=[f"{self.certs} cert_a_n2 cover", f"{self.certs} cert_a_n2_c0 cover",
                                  f"{self.certs} boundary_lemmas cover"])
        self.assertEqual(R.retries(lists, missing=True),
                         [f"{self.certs} cert_a_n2 cover yices", f"{self.certs} boundary_lemmas cover"])

    def test_listed_once(self):
        result(self.long, "cert_a_n2_c0", "bmc", None, "ERROR")
        lists = self.lists(chunks=[f"{self.long} cert_a_n2_c0 bmc"], retry=[f"{self.long} cert_a_n2_c0 bmc"])
        self.assertEqual(R.retries(lists), [f"{self.long} cert_a_n2_c0 bmc yices"])

    def test_both_drivers_use_it(self):
        camp = (HERE / "campaign.sh").read_text()
        self.assertIn('retry_list.py" --missing', camp)
        # certify ends with 'retry WORK record', which submits the record job
        self.assertIn("campaign.sh retry $WORK record", camp)
        self.assertRegex(camp, r'"retry \d+ \d+G [0-9:]+ \d+ \d+"')
        self.assertIn('retry_list.py" "$OUT/tasks.txt"', (HERE / "ci_prove.sh").read_text())
        # the retry list runs only when named (like whole_long)
        m = re.search(r'if \[ "\$only" = "  " \]; then (.*)$', camp, re.M)
        self.assertIsNotNone(m)
        self.assertIn('"$name" != retry', m.group(1))


if __name__ == "__main__":
    unittest.main()
