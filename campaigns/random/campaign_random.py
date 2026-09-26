# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""cocotb campaign driver for the constrained-random lockstep differential test.

For one seed it runs exactly the cases ``test/test_random.py`` runs for
``PE_SEED=<seed>``: ``make_case(seed, i, config, cycles=PE_RANDOM_CYCLES)`` for
``i`` in ``[PE_RANDOM_FIRST, PE_RANDOM_FIRST + PE_RANDOM_ITERS)``, executed by the
same ``run_case`` / ``Coverage`` code imported from the frozen snapshot's
``test/`` directory (put it on PYTHONPATH). One simulator process per seed,
exactly as ``PE_SEED=<seed> make COCOTB_TEST_MODULES=test_random`` would do.

Differences from test_random (campaign bookkeeping only; the stimulus and the
checks are unchanged):

* a failing case is recorded (case JSON + first lines of the mismatch) and the
  run continues with the next case (later cases of that seed are flagged
  ``after_failure``); minimization happens later, in triage, with PE_REPLAY;
* per-case results and the functional coverage are written as one JSON file
  (``VCAMP_RESULT``) so that coverage can be merged across seeds;
* optional generator variants (``VCAMP_VARIANT``) that only re-weight the
  upstream generator from outside. Every generated case is still a plain
  ``random_gen.Case`` and can be replayed by the upstream test with PE_REPLAY.

Environment:
  VCAMP_SEED       seed (int, any base)
  VCAMP_RESULT     output JSON path (written atomically at the end)
  VCAMP_FAIL_DIR   directory for failing-case JSON files (default: next to result)
  VCAMP_LABEL      campaign label recorded in the result
  VCAMP_VARIANT    default | dense (long programs, host traffic without idles)
                   | faulty (fault rate 0.10, more revive/clear traffic)
                   | hostile (illegal/aborted host command sequences, see hostile_host_op)
                   | deselect (the upstream trailing deselect op moved into the traffic;
                     on a generation-2 generator, which already does that, every case
                     gets one mid-traffic deselect)
  VCAMP_GEN        generator generation passed to make_case (snapshots whose make_case
                   takes ``generation``; default: the snapshot's own default)
  PE_VARIANT       design variant of the snapshot's test/variants.py (default base);
                   the model is configured from it, the core is chosen at build time
  PE_RANDOM_ITERS / PE_RANDOM_FIRST / PE_RANDOM_CYCLES   as in test_random
  PE_REPLAY        run one saved case JSON instead (triage); VCAMP_SEED ignored
  PE_INJECT_MODEL_BUG  xor: corrupt the model after XOR (negative control, as in test_random)
  VCAMP_XCOV       1: also collect the extended cross-coverage bins of xcov.py
  VCAMP_SAVE_CASES 0: do not write failing-case JSON (negative control; keeps inode count low)
  VCAMP_MINIMIZE   1: shrink each failing case with random_gen.minimize (triage;
                   PE_MINIMIZE_BUDGET re-runs) and write <stem>-min.json
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import platform
import socket
import sys
import time
import traceback
from pathlib import Path

import cocotb

import random_gen
from harness import CocotbHarness, LockstepMismatch, env_int
from random_gen import BugInjector, Case, Coverage, minimize, run_case


# ------------------------------------------------------------ generator variants
def apply_variant(name: str) -> None:
    """Re-weight the upstream generator without editing it (monkeypatch)."""
    if name in ("", "default"):
        return
    if name == "dense":
        generate = random_gen.ProgramGenerator.generate
        host_op = random_gen.host_op

        def long_generate(self, length):  # noqa: ANN001, ANN202
            # Long programs: 40..program_words words (upstream: 3..program_words, mostly short).
            return generate(self, max(length, self.rng.randint(40, self.config.program_words)))

        def busy_host_op(rng, config, ownership):  # noqa: ANN001, ANN202
            # Host traffic without long idles: redraw 85% of idle ops.
            op = host_op(rng, config, ownership)
            while op[0] == "idle" and rng.random() < 0.85:
                op = host_op(rng, config, ownership)
            return op

        random_gen.ProgramGenerator.generate = long_generate
        random_gen.host_op = busy_host_op
        return
    if name == "faulty":
        init = random_gen.ProgramGenerator.__init__
        host_op = random_gen.host_op

        def faulty_init(self, rng, config, ownership, fault_rate):  # noqa: ANN001, ANN202
            init(self, rng, config, ownership, max(fault_rate, 0.10))

        def revive_host_op(rng, config, ownership):  # noqa: ANN001, ANN202
            op = host_op(rng, config, ownership)
            if op[0] == "idle" and rng.random() < 0.3:
                return ["revive", rng.randrange(1, (1 << config.engines))]
            return op

        random_gen.ProgramGenerator.__init__ = faulty_init
        random_gen.host_op = revive_host_op
        return
    if name == "hostile":
        random_gen.host_op = hostile_host_op(random_gen.host_op)
        return
    if name == "deselect":
        # Upstream appends the optional ["deselect", n] op (10% of cases) after the op list
        # whose estimated cost already exceeds the cycle budget, and run_case stops at the
        # budget, so the op almost never executes. Move it to a deterministic random position
        # in the first 70% of the op list (mid-traffic, engines running).
        # Generation 2 of the generator (random_gen.GENERATION >= 2) already moves it there,
        # so the move would be a no-op: there, every case without a deselect gets one, drawn
        # the same way from the same separate stream (10x the generator's own rate).
        make = random_gen.make_case

        def make_with_deselect(seed, index, config, *, cycles, **kwargs):  # noqa: ANN001, ANN202
            case = make(seed, index, config, cycles=cycles, **kwargs)
            rng = random_gen.random.Random((seed * 1_000_003 + index) ^ 0xDE5E1EC7)
            if case.ops and case.ops[-1][0] == "deselect":
                op = case.ops.pop()
                case.ops.insert(rng.randrange(max(1, int(len(case.ops) * 0.7))), op)
            elif generation_of(kwargs) >= 2 and not any(op[0] == "deselect" for op in case.ops):
                op = ["deselect", rng.randint(1, 3)]
                case.ops.insert(rng.randrange(max(1, int(len(case.ops) * 0.7))), op)
            return case

        random_gen.make_case = make_with_deselect
        return
    raise ValueError(f"unknown VCAMP_VARIANT {name!r}")


def generation_of(kwargs: dict) -> int:
    """Generator generation a make_case call uses (1 for snapshots that predate generations)."""
    return int(kwargs.get("generation", getattr(random_gen, "GENERATION", 1)))


def make_case_kwargs() -> dict:
    """``generation=VCAMP_GEN`` when requested and the snapshot's make_case takes it."""
    value = os.environ.get("VCAMP_GEN", "")
    if not value:
        return {}
    if "generation" not in inspect.signature(random_gen.make_case).parameters:
        raise ValueError("VCAMP_GEN is set but this snapshot's make_case has no generation parameter")
    return {"generation": int(value, 0)}


def design_info(config) -> dict:  # noqa: ANN001
    """Design variant under test (snapshot test/variants.py, when present) and its model configuration."""
    info = {"design_variant": os.environ.get("PE_VARIANT", "") or "base",
            "fifo_words": getattr(config, "fifo_words", None), "model_config": type(config).__name__}
    options = getattr(config, "options", None)
    if options is not None:
        info["model_options"] = (dataclasses.asdict(options) if dataclasses.is_dataclass(options)
                                 else repr(options))
    return info


def hostile_host_op(host_op):  # noqa: ANN001, ANN201
    """Add illegal/aborted host command sequences the upstream generator never emits.

    Upstream only sends well-formed BEGIN/COMMIT/STOP/EVENT/ROUTE payloads and
    only writes program words inside a BEGIN..COMMIT load, so those rejection
    paths are never exercised. Sequences are queued per make_case RNG so that
    generation stays a pure function of (seed, index). Only op kinds that
    random_gen.execute already supports are used: ["cmd", word, engine] and
    ["partial", window, nibbles, word] (nibbles=8 completes the write).
    """
    from harness import BEGIN, COMMIT, EVENT, ROUTE, START, STOP

    state: dict = {"rng": None, "pending": []}

    def sequence(rng, config, ownership):  # noqa: ANN001, ANN202
        engines, words = config.engines, config.program_words
        e = rng.randrange(engines)
        kind = rng.choice(["begin-payload", "commit-bad", "stop-bits", "event-bits", "route-bits",
                           "stray-program-write", "aborted-reload", "overflow-reload"])
        if kind == "begin-payload":
            return [["cmd", BEGIN << 24 | rng.randint(1, 0xFFFFFF), e]]
        if kind == "commit-bad":
            length = rng.choice([0, rng.randint(1, words), words + rng.randint(1, 40), rng.randint(1, 0xFF) << 16])
            return [["cmd", COMMIT << 24 | length & 0xFFFFFF, e]]
        if kind == "stop-bits":
            return [["cmd", STOP << 24 | rng.randint(1 << engines, 0xFFFFFF), e]]
        if kind == "event-bits":
            return [["cmd", EVENT << 24 | rng.randint(1 << engines, 0xFFFFFF), e]]
        if kind == "route-bits":
            return [["cmd", ROUTE << 24 | 1 << rng.randint(21, 23) | rng.getrandbits(21), e]]
        if kind == "stray-program-write":
            # A full program-word write with no BEGIN (targets the currently selected engine).
            return [["partial", 1, 8, rng.getrandbits(32)] for _ in range(rng.randint(1, 3))]
        spec = random_gen.ProgramGenerator(rng, config, ownership[e], 0.03).generate(
            words if kind == "overflow-reload" else rng.randint(3, 16))
        n = len(spec.words)
        seq = [["cmd", BEGIN << 24, e]]
        if kind == "overflow-reload":
            # Fill the image, then one word too many (rejected), then a legal COMMIT.
            seq += [["partial", 1, 8, w] for w in spec.words] + [["partial", 1, 8, rng.getrandbits(32)]]
            seq += [["cmd", COMMIT << 24 | n, e], ["cmd", START << 24 | 1 << e, e]]
            return seq
        written = rng.randint(0, n)
        seq += [["partial", 1, 8, w] for w in spec.words[:written]]
        # START before COMMIT (rejected), COMMIT with the wrong length (rejected), then maybe
        # finish the load properly.
        seq.append(["cmd", START << 24 | 1 << e, e])
        seq.append(["cmd", COMMIT << 24 | rng.choice([written + 1, max(written - 1, 0), words + 1]), e])
        if rng.random() < 0.5:
            seq += [["partial", 1, 8, w] for w in spec.words[written:]]
            seq += [["cmd", COMMIT << 24 | n, e], ["cmd", START << 24 | 1 << e, e]]
        return seq

    def wrapped(rng, config, ownership):  # noqa: ANN001, ANN202
        if state["rng"] is not rng:
            state["rng"], state["pending"] = rng, []
        if state["pending"]:
            return state["pending"].pop(0)
        if rng.random() < 0.12:
            state["pending"] = sequence(rng, config, ownership)
            return state["pending"].pop(0)
        return host_op(rng, config, ownership)

    return wrapped


def coverage_dict(c: Coverage) -> dict:
    return {"cases": c.cases, "cycles": c.cycles,
            "executed": {str(k): dict(v) for k, v in sorted(c.executed.items())},
            "stalls": dict(c.stalls), "faults": dict(c.faults), "deliberate": dict(c.deliberate),
            "xfer": dict(c.xfer), "commands": dict(c.commands), "host": dict(c.host),
            "mover": c.mover, "misc": dict(c.misc)}


def classify(exc: BaseException) -> str:
    if isinstance(exc, LockstepMismatch):
        return "lockstep-mismatch"
    if isinstance(exc, AssertionError):
        return "checker-assertion"
    if isinstance(exc, TimeoutError):
        return "host-timeout"
    return "exception:" + type(exc).__name__


@cocotb.test()
async def test_random_campaign(dut):
    """One seed of the random lockstep campaign; results as JSON."""
    variant = os.environ.get("VCAMP_VARIANT", "default")
    apply_variant(variant)
    replay = os.environ.get("PE_REPLAY")
    seed = int(os.environ.get("VCAMP_SEED", "0x5EED2027"), 0)
    gate_level = bool(os.environ.get("PE_GATE_LEVEL"))
    iterations = env_int("PE_RANDOM_ITERS", 2 if gate_level else 64)
    first = env_int("PE_RANDOM_FIRST", 0)
    cycles = env_int("PE_RANDOM_CYCLES", 2000)
    result_path = Path(os.environ.get("VCAMP_RESULT", f"vcamp-seed{seed:#x}.json"))
    fail_dir = Path(os.environ.get("VCAMP_FAIL_DIR", result_path.parent))
    label = os.environ.get("VCAMP_LABEL", "adhoc")

    h = CocotbHarness(dut)
    if os.environ.get("PE_INJECT_MODEL_BUG") == "xor":  # negative control, as in test_random
        h.observers.append(BugInjector())
    coverage = Coverage()
    xcov = None
    if os.environ.get("VCAMP_XCOV") == "1":  # extended cross coverage (xcov.py)
        from xcov import ExtendedCoverage
        xcov = ExtendedCoverage(coverage)
        h.observers.append(xcov)
    await h.start()
    gen_kwargs = make_case_kwargs()
    if replay:
        cases = [Case.from_json(Path(replay).read_text())]
    else:
        cases = (random_gen.make_case(seed, i, h.model.config, cycles=cycles, **gen_kwargs)  # variants may wrap it
                 for i in range(first, first + iterations))
    records = []
    failed_before = False
    started = time.time()
    for case in cases:
        before_cycle, before_t = h.cycle, time.time()
        reloads_before = coverage.host.get("engine reprogrammed", 0)
        record = {"index": case.index, "programs_loaded": sum(1 for s in case.engines if s is not None),
                  "program_words": sum(len(s.words) for s in case.engines if s is not None),
                  "host_ops_generated": len(case.ops), "after_failure": failed_before}
        try:
            await run_case(h, case, coverage)
            record["status"] = "pass"
        except Exception as exc:  # noqa: BLE001 - record every failure kind and keep going
            coverage.detach(h)
            h.context = None
            failed_before = True
            record["status"] = "fail"
            record["kind"] = classify(exc)
            text = str(exc) if isinstance(exc, AssertionError) else "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__))
            record["message"] = "\n".join(text.splitlines()[:40])
            stem = f"{label}-seed{case.seed:#x}-case{case.index}"
            if os.environ.get("VCAMP_SAVE_CASES", "1") == "1":
                fail_dir.mkdir(parents=True, exist_ok=True)
                (fail_dir / f"{stem}.json").write_text(case.to_json())
                record["case_json"] = str(fail_dir / f"{stem}.json")
            dut._log.error("case %d (seed %#x) FAILED (%s):\n%s", case.index, case.seed, record["kind"],
                           record["message"])
            if os.environ.get("VCAMP_MINIMIZE") == "1":
                # Triage mode: the upstream minimizer (random_gen.minimize), as report_failure does.
                budget = env_int("PE_MINIMIZE_BUDGET", 60)
                h.quiet = True
                try:
                    small = await minimize(h, case, budget, lambda text: dut._log.info("%s", text))
                finally:
                    h.quiet = False
                (fail_dir / f"{stem}-min.json").write_text(small.to_json())
                record["min_case_json"] = str(fail_dir / f"{stem}-min.json")
                h.quiet = True
                try:
                    await run_case(h, small)
                    record["min_message"] = "minimized case PASSES on re-run"
                except Exception as again:  # noqa: BLE001
                    record["min_message"] = "\n".join(str(again).splitlines()[:40])
                finally:
                    h.quiet = False
                    h.context = None
                dut._log.error("minimized case:\n%s\n%s", small.describe(), record["min_message"])
            # Resynchronize: the next run_case starts with a reset.
        record["cycles"] = h.cycle - before_cycle
        record["reloads_executed"] = coverage.host.get("engine reprogrammed", 0) - reloads_before
        record["wall_s"] = round(time.time() - before_t, 3)
        records.append(record)
        dut._log.info("case %d (seed %#x): %d cycles, %s", case.index, case.seed, record["cycles"],
                      record["status"])
    summary = coverage.summary()
    dut._log.info("%s", summary)
    result = {
        "schema": "pe-vcamp.random.v1", "label": label, "variant": variant, "seed": seed,
        "replay": replay, "first": first, "iterations": iterations, "host_cycles": cycles,
        "gate_level": gate_level, "inject_model_bug": os.environ.get("PE_INJECT_MODEL_BUG", ""),
        "generation": generation_of(gen_kwargs), **design_info(h.model.config),
        "commit": os.environ.get("VCAMP_COMMIT", ""),
        "netlist": os.environ.get("VCAMP_NETLIST", ""),
        "host": socket.gethostname(), "slurm_job": os.environ.get("SLURM_JOB_ID", ""),
        "slurm_array_job": os.environ.get("SLURM_ARRAY_JOB_ID", ""),
        "slurm_array_task": os.environ.get("SLURM_ARRAY_TASK_ID", ""),
        "simulator": f"{cocotb.SIM_NAME} {cocotb.SIM_VERSION}", "cocotb": cocotb.__version__,
        "python": platform.python_version(),
        "cases_run": len(records), "cases_passed": sum(r["status"] == "pass" for r in records),
        "cases_failed": sum(r["status"] == "fail" for r in records),
        "lockstep_cycles": h.cycle, "wall_s": round(time.time() - started, 2),
        "cases": records, "coverage": coverage_dict(coverage), "coverage_summary": summary,
        "xcov": dict(xcov.bins) if xcov is not None else None,
        "argv": sys.argv[:1],
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = result_path.with_name(result_path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(result, indent=1))
    os.replace(tmp, result_path)
    assert result["cases_failed"] == 0, f"{result['cases_failed']} failing case(s); see {result_path}"
