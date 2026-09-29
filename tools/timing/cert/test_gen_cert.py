# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the certificate generator (no solver needed).

python3 -m unittest discover -s tools/timing/cert -p 'test_*.py'
PE_TIMING_REPO points at another checkout (default: this one).
"""

from __future__ import annotations

import json
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
            if kind == "branch-late":           # soft branch nodes only (SoftBranchTest)
                self.assertFalse(G.mutate_schedule(G.build(analysis("uart-tx"), T)[1], kind))
                continue
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


    def test_shell_scripts_use_the_generator_mutant_lists(self):
        # campaign.sh and ci_prove.sh loop over the same negative controls
        import re
        for script in ("campaign.sh", "ci_prove.sh"):
            text = (HERE / script).read_text()
            for var, want in (("MUTANTS", G.ANALYZER_MUTANTS), ("SCHEDULE_MUTANTS", G.SCHEDULE_MUTANTS)):
                m = re.search(rf'^{var}="([^"]*)"', text, re.M)
                self.assertIsNotNone(m, f"{script}: {var}")
                self.assertEqual(tuple(m.group(1).split()), want, f"{script}: {var}")


def corrupt_first_pad_event(an) -> int:
    """Make one predicted pad event contradict the pad timeline (as a wrong
    analyzer can): its 'before' state is not the state predicted so far.
    Returns the node index."""
    for i, key in enumerate(an.order):
        for v in an.nodes[key].variants:
            for j, e in enumerate(v.events):
                if e[T.EK] == "pad":
                    e = list(e)
                    e[T.EBEFORE] = 7 - e[T.EBEFORE] if e[T.EBEFORE] != 7 else 1
                    v.events[j] = tuple(e)
                    return i
    raise AssertionError("no pad event")


class NegativeControlTest(unittest.TestCase):
    def test_inconsistent_node_is_skipped_not_fatal(self):
        an = analysis("uart-tx")
        bad = corrupt_first_pad_event(an)
        with self.assertRaises(SystemExit):
            G.build(an, T)                              # real certificates: still an error
        certs = G.build(an, T, tolerant=True)
        self.assertEqual(len(certs), len(an.order))
        self.assertIn("pad timeline mismatch", certs[bad].invalid)
        self.assertEqual(certs[bad].variants, [])
        self.assertTrue(all(c.invalid is None for c in certs if c.index != bad))

    def test_failing_mutant_analysis_is_reported_not_fatal(self):
        class Broken:
            class Program:
                @staticmethod
                def from_image(path):
                    return path

            class Analysis:
                def __init__(self, program):
                    raise SystemExit("gen_cert: mutated analysis failed")
        img = REPO / "firmware" / "uart-tx.image.json"
        emitted, rejected = G.certificates(img, Broken, mutant="set-two-cycles", pick_one=True)
        self.assertEqual(emitted, [])
        self.assertIsNone(rejected["nodes"])
        self.assertIn("failed", rejected["reason"])

    def test_emit_every_negative_control_of_every_image(self):
        # What campaign.sh prepare and ci_prove.sh emit, for every image: no
        # analyzer or schedule mutant may stop the emission (exit 0), and a
        # node that cannot be written is listed under not_generated.
        with tempfile.TemporaryDirectory() as out:
            for m in G.ANALYZER_MUTANTS:
                rc = G.main(["emit", *map(str, IMAGES), "--out", out, "--rtl", out, "--models", out,
                             "--mutant", m, "--pick-one", "--append"])
                self.assertEqual(rc, 0, m)
            for m in G.SCHEDULE_MUTANTS:
                rc = G.main(["emit", *map(str, IMAGES), "--out", out, "--rtl", out, "--models", out,
                             "--schedule-mutant", m, "--pick-one", "--append"])
                self.assertEqual(rc, 0, m)
            man = json.loads((Path(out) / "manifest.json").read_text())
        self.assertGreater(len(man["certificates"]), len(IMAGES))
        for n in man.get("not_generated", []):
            self.assertIn(n["mutation"], G.ANALYZER_MUTANTS)
            self.assertTrue(n["reason"])

    def test_ps2_device_open_drain_control(self):
        # The case that used to abort campaign.sh prepare: two nodes of the
        # mutated analysis contradict themselves; the control comes from another node.
        img = REPO / "firmware" / "ps2-device.image.json"
        if not img.exists():
            self.skipTest("ps2-device not in this checkout")
        emitted, rejected = G.certificates(img, G.analyzer("open-drain-as-push-pull"),
                                           mutant="open-drain-as-push-pull", pick_one=True)
        self.assertEqual(len(emitted), 1)
        self.assertTrue(rejected and rejected["nodes"])
        self.assertNotIn(emitted[0].index, rejected["nodes"])

    def test_chunk_level_controls(self):
        img = REPO / "firmware" / "spi-controller-mode0.image.json"
        an = analysis("spi-controller-mode0")
        real = {(ch.index, ch.chunk, ch.group): ch for nc in G.build(an, T) for ch in G.split(an, T, nc, 96)
                if ch.chunk is not None}
        for m in ("pad-late", "pad-early", "pad-level", "post-limit"):
            (ch,), _ = G.certificates(img, T, schedule_mutant=m, chunk=96, chunk_controls=True, pick_one=True)
            self.assertIsNotNone(ch.chunk, m)
            self.assertEqual(ch.mutation, m)
            self.assertLessEqual(ch.depth, 96 + 2, m)
            self.assertTrue(G._differs(ch, real[(ch.index, ch.chunk, ch.group)]), m)
        # post-limit also applies to a chunk's cut post (the next chunk's precondition)
        mid = next(ch for ch in G.split(an, T, G.build(an, T)[1], 96) if ch.chunk == 1)
        self.assertEqual(mid.variants[0].post["kind"], "cut")
        self.assertTrue(G.mutate_schedule(mid, "post-limit"))
        # branch-late: soft branch nodes only
        self.assertEqual(G.certificates(img, T, schedule_mutant="branch-late", chunk=96, chunk_controls=True)[0], [])
        with self.assertRaises(SystemExit):
            G.certificates(img, T, chunk=96, chunk_controls=True)

    def test_preflight(self):
        mutants = {m: G.analyzer(m) for m in G.ANALYZER_MUTANTS}
        rec = G.preflight_image(REPO / "firmware" / "uart-tx.image.json", 96, mutants)
        self.assertTrue(rec["ok"], rec["reason"])
        certs = G.build(analysis("uart-tx"), T)
        short = sum(1 for nc in certs if nc.depth <= 98)
        self.assertEqual(rec["segments"], len(certs))
        self.assertEqual(rec["ci_runs"], 2 * short + 2 * rec["chunk_certificates"] + rec["shallow_negatives"])
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "broken.image.json"
            bad.write_text("{}")
            rec = G.preflight_image(bad, 96, mutants)
        self.assertFalse(rec["ok"])
        self.assertTrue(rec["reason"])


def soft_image(out: Path, wait: int = 3) -> Path:
    """A poll loop (IN pin1 / JZ rx back to IN) with no blocking instruction:
    pe_timing anchors a soft branch boundary at the JZ. After the exit, pin 0
    pulses high for ``wait`` + 1 cycles."""
    words = [0x02000000,            # 0: SET 0
             0x03000001,            # 1: DIR 1 (pin 0 driven)
             0x09010000,            # 2: IN pin1 -> rx
             0x1A010002,            # 3: JZ rx, 2       (soft branch)
             0x02000001,            # 4: SET 1
             0x04000000 | wait,     # 5: WAIT wait
             0x02000000,            # 6: SET 0
             0x05000002]            # 7: JMP 2
    path = out / "soft-poll.image.json"
    path.write_text(json.dumps({
        "schema_version": "protocol-emulator.firmware-image.v1", "isa_version": 2, "name": "soft-poll",
        "architecture": {"schema_version": "protocol-emulator.architecture.v1", "engine_count": 4,
                         "data_width": 32, "program_words": 64, "fifo_words": 8, "issue": "fused",
                         "prefetch": False},
        "engine": 0, "owned_pins": 1, "open_drain": 0, "clock_hz": 50000000, "words": words, "labels": {}}))
    return path


class SoftBranchTest(unittest.TestCase):
    def test_soft_branch_node(self):
        with tempfile.TemporaryDirectory() as d:
            an = T.Analysis(T.Program.from_image(soft_image(Path(d))))
            certs = G.build(an, T)                      # check_chain runs inside
        soft = [nc for nc in certs if nc.kind == T.BLOCK_BRANCH]
        self.assertEqual(len(soft), 1)
        nc = soft[0]
        self.assertEqual(nc.pre["pc"], 3)               # starts in the arrival state, at the JZ
        self.assertEqual(nc.start.pc, 3)
        firsts = sorted(v.attempts[1] for v in nc.variants)
        self.assertEqual(firsts, [2, 4])                # taken (IN) and not taken (SET 1)
        for v in nc.variants:
            self.assertEqual(v.attempts[0], 3)          # the branch itself is attempted at step 0
            self.assertEqual(v.post["pc"], 3)           # both loops come back to the JZ
            G.rtl_trace(an, T, nc, v)
        taken = next(v for v in nc.variants if v.attempts[1] == 2)
        self.assertEqual(taken.horizon, 2)              # IN at +1, JZ attempted again at step 2
        exit_ = next(v for v in nc.variants if v.attempts[1] == 4)
        # SET 1 issues on edge +2 (attempted at step 1): the pad changes at step 2
        self.assertEqual([s for s, _ in exit_.pads[0]][:2], [0, 2])
        # the START certificate's post is the soft node's precondition as it is
        start = certs[0]
        self.assertEqual({k: x for k, x in start.variants[0].post.items() if k != "kind"}, nc.pre)

    def test_branch_late(self):
        with tempfile.TemporaryDirectory() as d:
            an = T.Analysis(T.Program.from_image(soft_image(Path(d))))
        certs = G.build(an, T)
        real = next(nc for nc in certs if nc.kind == T.BLOCK_BRANCH)
        nc = next(c for c in G.build(an, T) if c.kind == T.BLOCK_BRANCH)
        self.assertTrue(G.mutate_schedule(nc, "branch-late"))
        self.assertTrue(G._differs(nc, real))
        for v, r in zip(nc.variants, real.variants):
            self.assertEqual(v.attempts[0], r.attempts[0])          # the branch attempt stays at step 0
            self.assertEqual(sorted(v.attempts)[1:], [c + 1 for c in sorted(r.attempts)[1:]])
            self.assertEqual(v.horizon, r.horizon + 1)
        self.assertFalse(G.mutate_schedule(certs[0], "branch-late"))  # START is not a soft branch

    def test_soft_branch_split(self):
        with tempfile.TemporaryDirectory() as d:
            an = T.Analysis(T.Program.from_image(soft_image(Path(d), wait=150)))
        nc = next(c for c in G.build(an, T) if c.kind == T.BLOCK_BRANCH)
        chunks = G.split(an, T, nc, 96)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].pre, nc.pre)
        for ch in chunks:
            G.sv_node({"engine": 0, "words": [0] * 8, "owned_pins": 1, "open_drain": 0}, ch, "m")


class ObligationTest(unittest.TestCase):
    def test_digest_matches_the_emitted_files(self):
        img = REPO / "firmware" / "uart-tx.image.json"
        inc = {"cert_dut.vh": "a", "cert_env.vh": "b"}
        with tempfile.TemporaryDirectory() as d:
            G.main(["emit", str(img), "--out", f"{d}/w", "--rtl", d, "--models", d])
            G.main(["emit", str(img), "--out", f"{d}/c", "--rtl", "/x/rtl", "--models", "/y/models",
                    "--chunk", "96", "--engines", "yices"])
            lines = {}
            for sub in ("w", "c"):
                man = json.loads((Path(d) / sub / "manifest.json").read_text())["certificates"]
                lines[sub] = [G.obligation_line(c["certificate"], (Path(d) / sub / f"{c['certificate']}.sv").read_text(),
                                                (Path(d) / sub / f"{c['certificate']}.sby").read_text())
                              for c in man if sub == "w" or c["chunk"] is not None]
        ob = G.obligations(img, 96, inc)
        self.assertEqual(ob["segments"], len(lines["w"]))
        self.assertEqual(ob["chunk_certificates"], len(lines["c"]))
        self.assertGreater(len(lines["c"]), 0)
        self.assertEqual(ob["whole_sha256"], G.digest_lines(lines["w"], inc))
        self.assertEqual(ob["chunks_sha256"], G.digest_lines(lines["c"], inc))
        self.assertNotEqual(ob["whole_sha256"], G.obligations(img, 96, {"cert_dut.vh": "c"})["whole_sha256"])

    def test_normalize_sby(self):
        a = G.sby_text("m.sv", "m", 10, ["yices"], Path("/a/rtl"), Path("/a/models"), Path("/a/cert"))
        b = G.sby_text("m.sv", "m", 10, ["bitwuzla", "boolector"], Path("/b"), Path("/c"), Path("/d"))
        c = G.sby_text("m.sv", "m", 11, ["yices"], Path("/a/rtl"), Path("/a/models"), Path("/a/cert"))
        self.assertEqual(G.normalize_sby(a), G.normalize_sby(b))
        self.assertNotEqual(G.normalize_sby(a), G.normalize_sby(c))
        self.assertNotIn("smtbmc", G.normalize_sby(a))
        self.assertNotIn("/a/", G.normalize_sby(a))


if __name__ == "__main__":
    unittest.main()
