# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The library's primitives are cycle-identical to test/harness.py's host driver.

The same operation list runs on a harness.ModelHarness and on
ProtocolEmulator(ModelPort); every cycle's (ui, uio_in, rst, ena, sampled uo)
and every returned value must match. The operations cover every command
(accepted and rejected), every READ_SELECT, TX writes under backpressure, RX
reads with pauses, abandoned partial writes and reads, window bounces,
program loading and firmware-image loading.
"""

import sys
import unittest

import support
from pe_host import ProtocolEmulator
from pe_host import protocol as P
from pe_host.ports.model import ModelPort
from pe_host.selftest import ECHO, FAULTER, WAITEV

sys.path.insert(0, str(support.HERE / "upy"))
from ops import run_ops  # noqa: E402

OPS = [
    ("reset", 4),
    ("load", 0, ECHO, 0x01, 0),
    ("load", 3, FAULTER, 0xC0, 0xC0),
    ("load", 2, WAITEV, 0, 0),
    ("command", P.SELECT, 0),
] + [("write", 2, w) for w in (0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88)] + [
    ("try_write", 2, 0x99, 6, 8),          # TX full: gives up
    ("status", P.RS_LEVELS),
    ("command", P.ROUTE, P.route_payload(0, 1, 3)),
    ("command", P.START, 0b1101),
    ("idle", 40),
    ("status", P.RS_STATUS),
    ("read", 3, None),
    ("read", 3, [0, 2, 0, 5, 0, 0, 1, 9]),
    ("try_read", 3, 4, 3),                 # abandoned after 3 nibbles
    ("read", 3, None),
    ("command", P.EVENT, 4),
    ("idle", 6),
    ("command", P.SELECT, 2),
    ("read", 3, None),
    ("command", P.SELECT, 3),
] + [("status", i) for i in range(8)] + [
    ("command", P.STOP, 0xF),
    ("command", P.CLEAR, 8 | P.CLEAR_HOST_FAULT),
    ("command", P.SELECT, 7),              # rejected: 7 >= engine_count
    ("command", P.CLEAR, P.CLEAR_HOST_FAULT),
    ("command", P.SELECT, 1),
    ("command", P.TRIGGER, P.trigger_payload(4, P.TRIG_HIGH)),
    ("command", P.FLUSH, 0),
    ("command", P.TRIGGER, 0),
    ("command", P.BEGIN, 0),
    ("write", 1, ECHO[0]),
    ("try_write", 1, ECHO[1], 3, 5),       # abandoned program word
    ("write", 1, ECHO[1]),
    ("command", P.COMMIT, 3),              # rejected: only 2 written
    ("command", P.COMMIT, 2),
    ("bounce",),
    ("command", P.OWN, 0x01),              # rejected: engine 0 owns pin 0
    ("command", P.CLEAR, P.CLEAR_HOST_FAULT),
    ("command", 12, 0),                    # unknown command
    ("command", P.CLEAR, P.CLEAR_HOST_FAULT),
    ("firmware", "uart-tx"),
    ("firmware", "i2c-write"),
    ("status", P.RS_VERSION),
    ("idle", 3),
]


def run_harness(ops):
    """The same op program through test/harness.py's host driver."""
    import harness

    class Recording(harness.ModelHarness):
        def __init__(self):
            super().__init__()
            self.log = []

        async def step(self, *args, **kw):
            pre = await super().step(*args, **kw)
            s = self.history[-1]
            self.log.append((s.ui, s.pins, s.reset, s.enabled, s.pre.uo))
            return pre

    h = Recording()
    results = []

    async def body(h):
        for op in ops:
            kind = op[0]
            if kind == "reset":
                await h.reset(op[1])
            elif kind == "deselect":
                await h.reset(op[1], deselect=True)
            elif kind == "load":
                await h.load(op[1], op[2], ownership=op[3], open_drain=op[4])
            elif kind == "command":
                await h.command(op[1], op[2])
            elif kind == "write":
                await h.write(op[1], op[2])
            elif kind == "try_write":
                results.append([kind, await h.try_write(op[1], op[2], max_wait=op[3],
                                                        nibbles=op[4])])
            elif kind == "status":
                results.append([kind, await h.status(op[1])])
            elif kind == "read":
                results.append([kind, await h.read(op[1], pauses=op[2] or ())])
            elif kind == "try_read":
                results.append([kind, await h.try_read(op[1], max_wait=op[2], nibbles=op[3])])
            elif kind == "idle":
                await h.idle(op[1])
            elif kind == "bounce":
                await h.bounce()
            elif kind == "firmware":
                await h.load_firmware(op[1])
    harness.run_model(body, h)
    return h.log, results


def run_library(ops, record_replay=False, oracle=False, narrow=False):
    """The op program through ProtocolEmulator on the model (tests/upy/ops.py).

    oracle=True attaches tests/acceptance.py's AcceptanceOracle (as
    port.oracle): every command() result and the selection record are
    checked against the model. narrow=True gives the port a 6-bit uo_out
    view (FAULT and IRQ unwired, like PicoPort on the Urbana build).
    """
    port = ModelPort(pins=0, record=True, record_replay=record_replay)
    if narrow:
        port.uo_visible = 0x3F
    pe = ProtocolEmulator(port, strict=False)
    hooks = None
    if oracle:
        from acceptance import AcceptanceOracle
        hooks = port.oracle = AcceptanceOracle(pe, port)
    pairs = run_ops(pe, ops, str(support.FIRMWARE), hooks)
    log = [(ui, pads, reset, enabled, pre.uo) for (_c, ui, pads, reset, enabled, pre, _a)
           in port.trace]
    return log, pairs, port


class PrimitiveEquivalenceTest(unittest.TestCase):
    def test_cycle_identical_to_the_harness_driver(self):
        ref_log, ref_results = run_harness(OPS)
        log, pairs, port = run_library(OPS)
        self.assertGreater(len(ref_log), 1500)
        self.assertEqual(len(log), len(ref_log))
        for i, (a, b) in enumerate(zip(ref_log, log)):
            self.assertEqual(a, b, "first difference at cycle %d" % i)
        self.assertEqual([p for p in pairs if p[0] != "command"], ref_results)
        # The operation list really exercised the interesting cases.
        values = [p[1] for p in pairs]
        self.assertIn(False, values)                   # try_write gave up / command rejected
        self.assertIn(None, values)                    # try_read abandoned
        self.assertEqual(port.model.engines[1].program, ECHO[:2])
        rejected = [op for op, p in zip([o for o in OPS if o[0] == "command"],
                                        [p for p in pairs if p[0] == "command"]) if p[1] is False]
        # SELECT 7, COMMIT 3 and command 12 are seen rejected; commands sent
        # while FAULT is already high (engine 3's fault, or the sticky host
        # fault) report None: acceptance cannot be inferred from uo[7].
        self.assertEqual([(op[1], op[2]) for op in rejected], [(0, 7), (2, 3), (12, 0)])
        self.assertIn(None, [p[1] for p in pairs if p[0] == "command"])
        kinds = {op[1] for op in OPS if op[0] == "command"}
        self.assertTrue(set(range(12)) <= kinds | {P.READ_SELECT, P.OWN, P.BEGIN, P.COMMIT},
                        "every command opcode appears")


def random_programs(seeds):
    """tools/fuzz_host.py's random op programs for the given seeds."""
    tools = str(support.HOST / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import fuzz_host
    images = support.image_names()
    return [fuzz_host.generate(seed, images) for seed in seeds]


class AcceptanceOracleTest(unittest.TestCase):
    """command() results and the selection record versus the model's own
    accept/reject decisions (tests/acceptance.py), on a full and on a 6-bit
    uo_out port."""

    def check(self, ops):
        log, _pairs, port = run_library(ops, oracle=True)
        self.assertEqual(port.oracle.problems, [])
        narrow_log, _pairs, narrow = run_library(ops, oracle=True, narrow=True)
        self.assertEqual(narrow.oracle.problems, [])
        self.assertEqual(narrow_log, log, "a 6-bit uo view does not change the stimulus")
        return port.oracle.stats + narrow.oracle.stats

    def test_operation_list(self):
        stats = self.check(OPS)
        self.assertGreater(stats["SELECT/READ_SELECT library False / model False"], 0)

    def test_random_programs(self):
        from collections import Counter
        total = Counter()
        for ops in random_programs(range(900000, 900030)):
            total.update(self.check(ops))
        for key in ("SELECT/READ_SELECT library True / model True",
                    "SELECT/READ_SELECT library False / model False",
                    "other library True / model True",
                    "other library False / model False",
                    "other library None / model True",
                    "other library None / model False"):
            self.assertGreater(total[key], 0, key)

    def test_oracle_catches_the_old_selection_bug(self):
        """Before the fix, SELECT/READ_SELECT returned None under a high FAULT
        pin and the library recorded the new selection anyway."""
        from acceptance import AcceptanceOracle

        class OldRule(ProtocolEmulator):
            def _static_acceptance(self, op, payload):
                return None      # command() then infers acceptance from uo[7] only

        problems = 0
        for ops in random_programs(range(900000, 900030)):
            port = ModelPort(pins=0)
            pe = OldRule(port, strict=False)
            oracle = AcceptanceOracle(pe, port)
            try:
                run_ops(pe, ops, str(support.FIRMWARE), oracle)
            except (IndexError, ValueError):
                problems += 1     # the diverged selection broke the run itself
                continue
            problems += len(oracle.problems)
        self.assertGreater(problems, 0)


if __name__ == "__main__":
    unittest.main()
