# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The documented RTL entry points resolve the same files from every working
directory: `make -C host rtl-cocotb` (repository root), `cd host && make
rtl-cocotb` and `make` inside host/tests/rtl_cocotb. GNU make does not update
$PWD for -C, so the cocotb Makefile must not derive paths from it (verifier
finding, job 23763692). Uses the `paths` target, which needs no cocotb."""

import os
import shutil
import subprocess
import unittest

import support

MAKE = shutil.which("make")
RTL_COCOTB = support.HERE / "rtl_cocotb"


def resolved(cwd, *args, pwd=None):
    env = dict(os.environ)
    env.pop("PE_HOST_SCENARIO", None)
    env.pop("RTL_DIR", None)
    env.pop("MODEL_DIR", None)
    env.pop("MAKEFLAGS", None)
    env["PWD"] = pwd or str(cwd)
    out = subprocess.run([MAKE, "-s", "--no-print-directory"] + list(args), cwd=str(cwd),
                         env=env, check=True, capture_output=True, text=True).stdout
    values = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


@unittest.skipIf(MAKE is None, "GNU make not available")
class RtlMakefileTest(unittest.TestCase):
    def forms(self):
        return {
            "make -C host rtl-cocotb-paths (repository root)":
                resolved(support.REPO, "-C", "host", "rtl-cocotb-paths"),
            "cd host && make rtl-cocotb-paths":
                resolved(support.HOST, "rtl-cocotb-paths"),
            "cd host/tests/rtl_cocotb && make paths":
                resolved(RTL_COCOTB, "paths"),
            "stale PWD":
                resolved(support.REPO, "-C", "host", "rtl-cocotb-paths", pwd="/nonexistent"),
        }

    def test_every_form_resolves_the_checkout(self):
        repo = support.REPO.resolve()
        for form, v in self.forms().items():
            with self.subTest(form=form):
                self.assertEqual(v["THIS_DIR"], str(RTL_COCOTB.resolve()))
                self.assertEqual(v["HOST_DIR"], str(support.HOST.resolve()))
                self.assertEqual(v["RTL_DIR"], str(repo))
                self.assertEqual(v["MODEL_DIR"], str(repo / "test"))
                self.assertTrue(os.path.isfile(v["TB"]), v["TB"])
                self.assertEqual(v["PE_HOST_SCENARIO"],
                                 str(repo / "firmware" / "flagship-scenario.json"))
                self.assertIn(str(support.HOST.resolve()), v["PYTHONPATH"].split(":"))
                if (repo / "src" / "project.v").is_file():
                    for source in v["SOURCES"].split():
                        self.assertTrue(os.path.isfile(source), source)

    def test_overrides(self):
        v = resolved(support.REPO, "-C", "host", "rtl-cocotb-paths", "RTL_DIR=/x/rtl")
        self.assertEqual(v["RTL_DIR"], "/x/rtl")
        self.assertEqual(v["PE_HOST_SCENARIO"], "/x/rtl/firmware/flagship-scenario.json")
        v = resolved(support.HOST, "rtl-cocotb-paths", "PE_HOST_SCENARIO=/x/s.json")
        self.assertEqual(v["PE_HOST_SCENARIO"], "/x/s.json")
        # A stale PE_HOST_SCENARIO in the environment does not win.
        os.environ["PE_HOST_SCENARIO"] = "/stale.json"
        try:
            env = dict(os.environ)
            out = subprocess.run([MAKE, "-s", "--no-print-directory", "-C", "host",
                                  "rtl-cocotb-paths"], cwd=str(support.REPO), env=env,
                                 check=True, capture_output=True, text=True).stdout
        finally:
            del os.environ["PE_HOST_SCENARIO"]
        self.assertNotIn("/stale.json", out)


if __name__ == "__main__":
    unittest.main()
