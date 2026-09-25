#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Randomized three-way differential campaign for the host library.

Per seed, a random host-operation program (tests/upy/ops.py format: every
command with valid and invalid payloads, abandoned partial writes and reads
in every window, read pauses, window bounces, program and firmware loads,
resets and deselection) runs

  A. through test/harness.py's host driver on ModelHarness,
  B. through pe_host.ProtocolEmulator on ModelPort (CPython), and
  C. optionally through the same library under the MicroPython unix port on
     ReplayPort (replaying B's trace, which checks every cycle's ui byte).

A and B must produce identical per-cycle (ui_in, uio_in, rst_n, ena, uo_out)
and identical returned values; C must reproduce B's values digest.

The harness driver returns nothing for commands, so B's command() results
are checked separately against the model's own accept/reject decisions
(tests/acceptance.py): True/False/None per the documented rule, SELECT and
READ_SELECT exact, and the library's SELECT/READ_SELECT record equal to the
model's after every op. Every fourth seed repeats B on a port with a 6-bit
uo_out view (uo[7] and uo[6] unwired): same stimulus, same oracle.

  D. optionally (--rtl) on the RTL with Icarus Verilog: B's exact stimulus as
     a self-checking testbench (test/model/host.py render_testbench) that
     compares uo_out/uio_out/uio_oe after every edge.

    python3 host/tools/fuzz_host.py --seeds 0:2000 [--jobs N] [--micropython BIN]
        [--rtl RTL_CHECKOUT --iverilog-bin DIR] [--out summary.json]
"""

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

HOST = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOST / "tests"))
import support  # noqa: E402

sys.path.insert(0, str(support.HERE / "upy"))
from ops import digest  # noqa: E402
from pe_host import protocol as P  # noqa: E402
from pe_host.selftest import ECHO, FAULTER, HALT, WAITEV, imm, ins, pulse  # noqa: E402

LOAD, ADD, PUSH, JMP, SIGNAL, WAITEVENT = 19, 20, 7, 5, 14, 15
COUNTER = [ins(LOAD, 2) | 1, ins(ADD, 1, 2), ins(PUSH), imm(JMP, 1)]
STRICT_COUNTER = [ins(LOAD, 2) | 1, ins(ADD, 1, 2), ins(PUSH, 1), imm(JMP, 1)]


def program(rng):
    """(words, owned, open_drain) from a pool of small legal and faulting programs."""
    choice = rng.randrange(9)
    if choice == 0:
        return ECHO, 0, 0
    if choice == 1:
        return WAITEV, 0, 0
    if choice == 2:
        return FAULTER, 0, 0
    if choice == 3:
        return [imm(HALT)], 0, 0
    if choice == 4:
        pin = rng.randrange(8)
        return pulse(pin), 1 << pin, (1 << pin) if rng.random() < 0.3 else 0
    if choice == 5:
        return COUNTER, 0, 0
    if choice == 6:
        return STRICT_COUNTER, 0, 0
    if choice == 7:
        return [imm(SIGNAL, rng.randrange(1, 16)), imm(WAITEVENT), imm(JMP, 0)], 0, 0
    words = [rng.getrandbits(32) for _ in range(rng.randrange(1, 6))]  # mostly invalid code
    return words, rng.getrandbits(8), 0


def payload(rng, op):
    r = rng.random()
    if r < 0.12:
        return rng.getrandbits(24)                                  # usually invalid
    if op == P.SELECT:
        return rng.randrange(5)
    if op in (P.BEGIN, P.FLUSH):
        return 0 if r < 0.9 else 1
    if op == P.COMMIT:
        return rng.randrange(0, 9)
    if op == P.OWN:
        owned = rng.getrandbits(8)
        return owned | (owned & rng.getrandbits(8)) << 8
    if op in (P.START, P.STOP, P.EVENT):
        return rng.randrange(16)
    if op == P.CLEAR:
        return rng.randrange(16) | (P.CLEAR_HOST_FAULT if rng.random() < 0.5 else 0)
    if op == P.ROUTE:
        return P.route_payload(rng.randrange(4), rng.randrange(4), rng.randrange(0, 6),
                               rng.random() < 0.8)
    if op == P.READ_SELECT:
        return rng.randrange(9)
    if op == P.TRIGGER:
        return rng.randrange(64)
    return rng.getrandbits(8)


def generate(seed, images):
    rng = random.Random(seed)
    ops = [["reset", 4]]
    for _ in range(rng.randrange(30, 110)):
        r = rng.random()
        if r < 0.10:
            words, owned, od = program(rng)
            ops.append(["load", rng.randrange(4), words, owned, od])
        elif r < 0.13:
            ops.append(["firmware", rng.choice(images)])
        elif r < 0.42:
            if rng.random() < 0.12:
                # Recover a known state now and then: otherwise the sticky
                # host fault would hide most accept/reject outcomes.
                ops.append(["command", P.STOP, 15])
                ops.append(["command", P.CLEAR, 15 | P.CLEAR_HOST_FAULT])
                continue
            op = rng.randrange(12) if rng.random() < 0.95 else rng.randrange(12, 256)
            ops.append(["command", op, payload(rng, op)])
        elif r < 0.56:
            ops.append(["try_write", rng.choice((1, 2, 2)), rng.getrandbits(32),
                        rng.randrange(0, 13), 8 if rng.random() < 0.6 else rng.randrange(1, 8)])
        elif r < 0.66:
            ops.append(["status", rng.randrange(8)])
        elif r < 0.70:
            pauses = [rng.randrange(0, 6) for _ in range(8)] if rng.random() < 0.5 else None
            ops.append(["read", 0, pauses])
        elif r < 0.84:
            ops.append(["try_read", rng.choice((0, 3, 3)), rng.randrange(0, 31),
                        8 if rng.random() < 0.6 else rng.randrange(1, 8)])
        elif r < 0.93:
            ops.append(["idle", rng.randrange(0, 61)])
        elif r < 0.97:
            ops.append(["bounce"])
        elif r < 0.99:
            ops.append(["reset", rng.randrange(1, 4)])
        else:
            ops.append(["deselect", rng.randrange(1, 3)])
    return ops


def rtl_replay(port, rtl, bin_dir, tmpdir, seed):
    """Run the recorded stimulus on the RTL; returns None or an error string."""
    tb = os.path.join(tmpdir, "seed%d_tb.v" % seed)
    sim = os.path.join(tmpdir, "seed%d.vvp" % seed)
    Path(tb).write_text(port.render_testbench())
    sources = [rtl + "/src/project.v", rtl + "/src/protocol_emulator_core.v",
               rtl + "/models/RM_IHPSG13_1P_64x16_c2.v",
               rtl + "/models/RM_IHPSG13_1P_core_behavioral.v"]
    try:
        subprocess.run([os.path.join(bin_dir, "iverilog"), "-g2012", "-DFUNCTIONAL", "-o", sim,
                        "-s", "tb", tb] + sources, check=True, capture_output=True, text=True,
                       timeout=1800)
        run = subprocess.run([os.path.join(bin_dir, "vvp"), "-n", sim], capture_output=True,
                             text=True, timeout=1800, cwd=tmpdir)
    finally:
        for path in (tb, sim):
            if os.path.exists(path):
                os.unlink(path)
    if "DIFFERENTIAL_OK" not in run.stdout:
        return "rtl: " + (run.stdout + run.stderr)[-600:]
    return None


def run_seed(args):
    seed, micropython, tmpdir, rtl, bin_dir = args
    import test_host_harness_equivalence as eq
    images = support.image_names()
    ops = generate(seed, images)
    result = {"seed": seed, "ops": len(ops)}
    try:
        ref_log, ref_values = eq.run_harness(ops)
        log, pairs, port = eq.run_library(ops, record_replay=micropython is not None,
                                          oracle=True)
        narrow = eq.run_library(ops, oracle=True, narrow=True) if seed % 4 == 0 else None
    except Exception as exc:  # noqa: BLE001 - reported per seed
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result
    result["cycles"] = len(log)
    if port.oracle.problems:
        result["error"] = "acceptance oracle: %s" % port.oracle.problems[:3]
        return result
    if narrow is not None:
        if narrow[0] != log:
            result["error"] = "6-bit uo port: stimulus differs"
            return result
        if narrow[2].oracle.problems:
            result["error"] = "acceptance oracle (6-bit uo): %s" % narrow[2].oracle.problems[:3]
            return result
    if log != ref_log:
        first = next((i for i, (a, b) in enumerate(zip(ref_log, log)) if a != b),
                     min(len(log), len(ref_log)))
        result["error"] = "trace differs at cycle %d (lengths %d/%d)" % (first, len(ref_log), len(log))
        return result
    if [p for p in pairs if p[0] != "command"] != ref_values:
        result["error"] = "returned values differ"
        return result
    stats = Counter()
    for (kind, value), op in zip(pairs, [o for o in ops if o[0] in
                                         ("command", "try_write", "status", "read", "try_read")]):
        if kind == "command":
            stats["command %d %s" % (op[1] if op[1] < 12 else 12, value)] += 1
        elif kind == "try_write":
            stats["try_write %s" % ("done" if value else "abandoned")] += 1
        elif kind == "try_read":
            stats["try_read %s" % ("abandoned" if value is None else "done")] += 1
        else:
            stats[kind] += 1
    for key, value in port.oracle.stats.items():
        stats["accept " + key] += value
    if narrow is not None:
        stats["narrow seeds"] += 1
        for key, value in narrow[2].oracle.stats.items():
            stats["accept 6-bit " + key] += value
    result["stats"] = dict(stats)
    if micropython is not None:
        trace = os.path.join(tmpdir, "seed%d.trace" % seed)
        program_path = os.path.join(tmpdir, "seed%d.json" % seed)
        Path(trace).write_bytes(bytes(port.replay))
        Path(program_path).write_text(json.dumps(ops))
        proc = subprocess.run([micropython, str(HOST / "tests" / "upy" / "replay_main.py"),
                               str(HOST), "ops", trace, program_path, str(support.FIRMWARE)],
                              capture_output=True, text=True, timeout=600)
        os.unlink(trace)
        os.unlink(program_path)
        want = "REPLAY_OK ops %d %s" % (len(log), digest(pairs))
        if proc.returncode != 0 or not proc.stdout.startswith(want):
            result["error"] = "micropython: " + (proc.stdout + proc.stderr)[-600:]
            return result
        result["micropython"] = True
    if rtl:
        error = rtl_replay(port, rtl, bin_dir, tmpdir, seed)
        if error:
            result["error"] = error
            return result
        result["rtl"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", default="0:200", help="start:stop")
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", "1")))
    parser.add_argument("--micropython", default=os.environ.get("PE_HOST_MICROPYTHON"))
    parser.add_argument("--rtl", help="repository checkout whose src/ and models/ to replay on")
    parser.add_argument("--iverilog-bin", default=os.environ.get("PE_HOST_IVERILOG_BIN", ""))
    parser.add_argument("--out")
    args = parser.parse_args()
    start, stop = (int(x) for x in args.seeds.split(":"))
    tmpdir = tempfile.mkdtemp(prefix="pe_host_fuzz_")
    t0 = time.time()
    work = [(seed, args.micropython, tmpdir, args.rtl, args.iverilog_bin)
            for seed in range(start, stop)]
    with Pool(args.jobs) as pool:
        results = pool.map(run_seed, work, chunksize=4)
    failures = [r for r in results if "error" in r]
    totals = Counter()
    for r in results:
        totals.update(r.get("stats", {}))
    summary = {
        "seeds": [start, stop], "jobs": args.jobs, "seconds": round(time.time() - t0, 1),
        "passed": len(results) - len(failures), "failed": len(failures),
        "cycles": sum(r.get("cycles", 0) for r in results),
        "ops": sum(r["ops"] for r in results),
        "micropython_replays": sum(1 for r in results if r.get("micropython")),
        "rtl_replays": sum(1 for r in results if r.get("rtl")),
        "coverage": dict(sorted(totals.items())),
        "failures": failures[:50],
    }
    text = json.dumps(summary, indent=1)
    if args.out:
        Path(args.out).write_text(text)
    print(text[:4000])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
