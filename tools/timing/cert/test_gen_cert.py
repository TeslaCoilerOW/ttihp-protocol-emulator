# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the certificate generator (no solver needed).

python3 -m unittest discover -s tools/timing/cert -p 'test_*.py'
PE_TIMING_REPO points at another checkout (default: this one).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import gen_cert as G  # noqa: E402
import pe_timing as T  # noqa: E402

REPO = Path(os.environ.get("PE_TIMING_REPO", HERE.parents[2]))
IMAGES = sorted((REPO / "firmware").glob("*.image.json"))


def analysis(name: str, TT=T):
    return TT.Analysis(TT.Program.from_image(REPO / "firmware" / f"{name}.image.json"))


class GenCertTest(unittest.TestCase):
    def test_every_committed_image_is_supported(self):
        self.assertGreaterEqual(len(IMAGES), 19)
        for path in IMAGES:
            an = T.Analysis(T.Program.from_image(path))
            certs = G.build(an, T)
            self.assertEqual(len(certs), len(an.order), path.name)
            for nc in certs:
                for v in nc.variants:
                    # the cycle-level re-derivation agrees with pe_timing
                    G.rtl_trace(an, T, nc, v)

    def test_uart_tx_frame(self):
        certs = G.build(analysis("uart-tx"), T)
        frame = certs[1]
        self.assertEqual(frame.kind, T.BLOCK_TX)
        (v,) = frame.variants
        self.assertEqual(v.horizon, 643)            # arrival at +644
        steps = [s for s, _ in v.pads[0]]
        self.assertEqual(steps[:3], [0, 2, 66])     # start bit at +2, first data bit at +66
        self.assertIsNone(frame.pre["regs"][0])     # tx is the pulled word: free

    def test_split_chains(self):
        an = analysis("spi-controller-mode0")
        nc = G.build(an, T)[1]
        chunks = G.split(an, T, nc, 96)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].pre, nc.pre)
        for a, b in zip(chunks, chunks[1:]):
            self.assertEqual(a.variants[0].post["kind"], "cut")
            post = dict(a.variants[0].post)
            post.pop("kind")
            self.assertEqual(post, b.pre)
        self.assertEqual(chunks[-1].variants[0].post, nc.variants[0].post)

    def test_mutants_are_detected_by_the_rederivation(self):
        for mutant in ("wait-n-cycles", "xfer-late-edge", "count-n-iterations", "set-two-cycles"):
            TT = G.analyzer(mutant)
            found = False
            for path in IMAGES:
                an = TT.Analysis(TT.Program.from_image(path))
                try:
                    for nc in G.build(an, TT):
                        for v in nc.variants:
                            G.rtl_trace(an, TT, nc, v)
                except SystemExit:
                    found = True
                    break
            self.assertTrue(found, mutant)

    def test_schedule_mutants_change_the_certificate(self):
        for kind in G.SCHEDULE_MUTANTS:
            real = G.build(analysis("uart-tx"), T)[1]
            mutated = G.build(analysis("uart-tx"), T)[1]
            self.assertTrue(G.mutate_schedule(mutated, kind), kind)
            self.assertTrue(G._differs(mutated, real), kind)

    def test_emit(self):
        with tempfile.TemporaryDirectory() as out:
            rc = G.main(["emit", str(REPO / "firmware" / "uart-tx.image.json"), "--out", out,
                         "--rtl", out, "--models", out])
            self.assertEqual(rc, 0)
            sv = (Path(out) / "cert_uart_tx_n1.sv").read_text()
            for label in ("flow:", "pad_v0_p0:", "edge_v0_p0:", "post_v0:", "cover_v0:"):
                self.assertIn(label, sv)
            sby = (Path(out) / "cert_uart_tx_n1.sby").read_text()
            self.assertIn("bmc: depth 645", sby)


if __name__ == "__main__":
    unittest.main()
