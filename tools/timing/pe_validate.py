# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Check pe_timing's static schedules against the Python reference model.

The reference model (``test/model/reference.py``) is the executable ISA
specification. This module wraps ``Reference.tick``/``_step``/``command``
(without changing their behaviour) to record, for every engine run (START to
STOP/reset/restart), on every edge:

* whether the engine attempted to issue an instruction, at which pc, and the
  outcome (completed, blocked, faulted);
* the physical state of each owned pad (driven 0, driven 1, released), from
  the model's public ``uio_out``/``uio_oe``.

Each run is then replayed against the static analysis of the program it was
started with, as a set of hypotheses (boundary-graph node, path variant,
anchor edge). A hypothesis survives an edge only if the observed attempt pc,
its outcome and every owned pad state are exactly what the variant predicts at
that offset from the anchor; pads may change only on predicted edges. At a
boundary the observed stall must lie in the node's [min, max] range (a
timeout must happen exactly LIMIT-1 edges after arrival), and the successor
node is anchored at the observed completion edge. A run fails if all
hypotheses die.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import random
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pe_timing as T

# ----------------------------------------------------------------------------
# Tracing
# ----------------------------------------------------------------------------

OK_DONE, OK_BLOCKED, OK_FAULT = "done", "blocked", "fault"


class EngineRun:
    __slots__ = ("inst", "engine", "start", "words", "ownership", "open_drain", "arch", "ticks",
                 "end_reason", "end_tick", "label")

    def __init__(self, inst: int, engine: int, start: int, e: Any, arch: T.Arch, label: str) -> None:
        self.inst, self.engine, self.start = inst, engine, start
        self.words = tuple(e.program)
        self.ownership, self.open_drain = e.ownership, e.open_drain
        self.arch = arch
        self.ticks: list[tuple] = []   # (tick, attempt or None, pads tuple, fault)
        self.end_reason = "open"
        self.end_tick = None
        self.label = label


class _Instance:
    def __init__(self, tracer: "Tracer", ref: Any) -> None:
        self.tracer = tracer
        self.n = 0
        self.open: dict[int, EngineRun] = {}
        self.attempts: dict[int, tuple] = {}
        self.started: set = set()
        self.stopped: set = set()
        cfg = ref.config
        self.arch = T.Arch(cfg.engines, cfg.width, cfg.program_words, cfg.fifo_words, cfg.fused, cfg.prefetch)
        self.id = len(tracer.instances)

    def close(self, idx: int, reason: str, n: int) -> None:
        run = self.open.pop(idx, None)
        if run is not None:
            run.end_reason, run.end_tick = reason, n
            self.tracer.finish(run)

    def after_tick(self, ref: Any, was_reset: bool) -> None:
        n = self.n
        if was_reset:
            for idx in list(self.open):
                self.close(idx, "reset", n)
        else:
            for idx in sorted(self.stopped):
                self.close(idx, "stop", n)
            for idx in sorted(self.started):
                self.close(idx, "restart", n)
                self.open[idx] = EngineRun(self.id, idx, n, ref.engines[idx], self.arch, self.tracer.label)
            out = ref.outputs(0)
            oe, val = out.uio_oe, out.uio_out
            for idx, run in self.open.items():
                e = ref.engines[idx]
                own = run.ownership
                pads = tuple((T.PZ if not (oe >> p) & 1 else (T.P1 if (val >> p) & 1 else T.P0))
                             if (own >> p) & 1 else 0 for p in range(8))
                att = None
                rec = self.attempts.get(idx)
                if rec is not None:
                    pre, post = rec
                    running, wait, xfer, pc, completed, fault = pre
                    if running and not wait and not xfer:
                        if post[5] and post[5] != fault:
                            att = (pc, OK_FAULT, post[5])
                        elif post[2] or post[4] != completed:
                            att = (pc, OK_DONE, 0)
                        else:
                            att = (pc, OK_BLOCKED, 0)
                run.ticks.append((n, att, pads, e.fault))
        self.n = n + 1


class Tracer:
    """Context manager that records engine runs of every Reference instance."""

    def __init__(self, reference_module: Any, sink) -> None:  # noqa: ANN001
        self.R = reference_module
        self.sink = sink
        self.instances: dict[int, _Instance] = {}
        self.label = ""

    def _inst(self, ref: Any) -> _Instance:
        tag = f"_pe_timing_tag_{id(self)}"
        it = self.instances.get(id(ref))
        if it is None or getattr(ref, tag, None) is not it:
            it = _Instance(self, ref)
            self.instances[id(ref)] = it
            setattr(ref, tag, it)
        return it

    def finish(self, run: EngineRun) -> None:
        self.sink(run)

    def flush(self) -> None:
        for it in list(self.instances.values()):
            for idx in list(it.open):
                it.close(idx, "end", it.n)
        self.instances.clear()

    def __enter__(self) -> "Tracer":
        Ref = self.R.Reference
        self._orig = (Ref.tick, Ref._step, Ref.command)
        orig_tick, orig_step, orig_command = self._orig
        tracer = self

        def tick(ref, ui=0, pins=0, *, reset=False, enabled=True):  # noqa: ANN001, ANN202
            it = tracer._inst(ref)
            it.attempts, it.started, it.stopped = {}, set(), set()
            out = orig_tick(ref, ui, pins, reset=reset, enabled=enabled)
            it.after_tick(ref, reset or not enabled)
            return out

        def _step(ref, e, tx_available, rx_space):  # noqa: ANN001, ANN202
            it = tracer._inst(ref)
            idx = next(i for i, x in enumerate(ref.engines) if x is e)
            pre = (e.running, e.wait, e.transfer is not None, e.pc, e.completed, e.fault)
            result = orig_step(ref, e, tx_available, rx_space)
            it.attempts[idx] = (pre, (e.running, e.wait, e.transfer is not None, e.pc, e.completed,
                                      e.fault, e.stalled))
            return result

        def command(ref, word):  # noqa: ANN001, ANN202
            result = orig_command(ref, word)
            it = tracer._inst(ref)
            it.started |= set(ref._starts)
            it.stopped |= set(ref._stops)
            return result

        Ref.tick, Ref._step, Ref.command = tick, _step, command
        return self

    def __exit__(self, *exc: Any) -> None:
        Ref = self.R.Reference
        Ref.tick, Ref._step, Ref.command = self._orig
        self.flush()


# ----------------------------------------------------------------------------
# Checking a run against the static analysis
# ----------------------------------------------------------------------------

class Sched:
    """Per-variant lookup tables: attempts and pad events by offset."""

    def __init__(self, an: T.Analysis, node: T.Node, vi: int) -> None:
        v = node.variants[vi]
        self.v = v
        self.node = node
        self.attempts: dict[int, int] = {}
        self.pads: dict[int, list] = defaultdict(list)
        for e in v.events:
            if e[T.EK] in ("issue", "arrive"):
                self.attempts[e[T.ET]] = e[T.EPC]
            elif e[T.EK] == "pad":
                self.pads[e[T.ET]].append(e)
        self.periodic = None
        if v.kind == "periodic":
            t_first, period = v.end[1], v.end[2]
            self.periodic = (t_first, period)
        self.strict = {t: pc for (t, pc, code) in v.conditional_faults}

    def _fold(self, rel: int) -> int:
        if self.periodic is None or rel < self.periodic[0] + self.periodic[1]:
            return rel
        t_first, period = self.periodic
        return t_first + (rel - t_first) % period

    def attempt(self, rel: int) -> int | None:
        return self.attempts.get(self._fold(rel))

    def pad_events(self, rel: int) -> list:
        return self.pads.get(self._fold(rel), [])


class Hyp:
    __slots__ = ("key", "vi", "anchor", "phase", "arrive", "cur", "sched")

    def __init__(self, key: Any, vi: int, anchor: int, cur: list, sched: Sched) -> None:
        self.key, self.vi, self.anchor = key, vi, anchor
        self.phase = "seg"
        self.arrive = None
        self.cur = cur          # predicted pad-state sets per pin
        self.sched = sched


class RunChecker:
    def __init__(self, an: T.Analysis, stats: "Stats", name: str) -> None:
        self.an = an
        self.stats = stats
        self.name = name
        self._sched: dict[tuple, Sched] = {}

    def sched(self, node: T.Node, vi: int) -> Sched:
        k = (node.key, vi)
        s = self._sched.get(k)
        if s is None:
            s = self._sched[k] = Sched(self.an, node, vi)
        return s

    def spawn(self, node: T.Node, anchor: int, pads: tuple) -> list[Hyp]:
        return [Hyp(node.key, vi, anchor, list(pads), self.sched(node, vi)) for vi in range(len(node.variants))]

    def check(self, run: EngineRun) -> dict[str, Any]:
        an, st = self.an, self.stats
        own = an.own
        if not run.ticks:
            return {"ok": True, "ticks": 0}
        start = an.start_node()
        n0, att0, pads0, _ = run.ticks[0]
        for p in range(8):
            if (own >> p) & 1 and pads0[p] != T.PZ:
                return self._fail(run, n0, "pads not released at START", [])
        hyps = self.spawn(start, n0, tuple(T.PZ if (own >> p) & 1 else 0 for p in range(8)))
        prev_pads = pads0
        ended = False
        for n, att, pads, fault in run.ticks[1:]:
            st.ticks += 1
            if att is not None:
                st.attempts += 1
            changed = [p for p in range(8) if (own >> p) & 1 and pads[p] != prev_pads[p]]
            st.pad_changes += len(changed)
            if ended:
                if att is not None or changed:
                    return self._fail(run, n, "activity after a predicted halt/fault", [])
                prev_pads = pads
                continue
            survivors: list[Hyp] = []
            spawned: list[Hyp] = []
            reasons: list[str] = []
            terminal = False
            for h in hyps:
                r = self._advance(h, n, att, pads, prev_pads, fault, spawned)
                if r is True:
                    survivors.append(h)
                elif r == "next":
                    pass
                elif r == "end":
                    terminal = True
                else:
                    reasons.append(r)
            if terminal:
                ended = True
                hyps = []
            else:
                # identical (node, variant, anchor, phase) hypotheses are redundant: keep one
                seen: set = set()
                hyps = []
                for h in survivors + spawned:
                    k = (h.key, h.vi, h.anchor, h.phase, h.arrive)
                    if k not in seen:
                        seen.add(k)
                        hyps.append(h)
                if not hyps:
                    if not an.is_exact():
                        # over-approximate or budget-truncated analysis (random programs only):
                        # the rest of this run is not checked, and counted as such
                        st.runs_unchecked += 1
                        st.unchecked_ticks += len(run.ticks)
                        return {"ok": True, "unchecked": True}
                    return self._fail(run, n, "; ".join(sorted(set(reasons))[:4]), hyps_before(survivors, reasons))
            st.pad_matched += len(changed)
            prev_pads = pads
        st.runs_ok += 1
        return {"ok": True, "ticks": len(run.ticks)}

    def _advance(self, h: Hyp, n: int, att: tuple | None, pads: tuple, prev: tuple, fault: int,
                 spawned: list) -> Any:
        an, st = self.an, self.stats
        node = an.nodes[h.key]
        v = h.sched.v
        rel = n - h.anchor
        own = an.own
        if h.phase == "seg":
            if v.kind == "budget" and rel > v.t_end:
                st.unchecked_ticks += 1
                return True
            exp = h.sched.attempt(rel)
            if exp is None:
                if att is not None:
                    return f"unexpected attempt pc{att[0]} at +{rel}"
            else:
                if att is None:
                    return f"missing attempt pc{exp} at +{rel}"
                if att[0] != exp:
                    return f"attempt pc{att[0]} at +{rel}, predicted pc{exp}"
            if exp is not None and v.kind == "boundary" and rel == v.end[1]:
                # arrival: the boundary instruction writes no pads; a LIMIT=1 wait may time out here
                h.phase, h.arrive = "bnd", n
                return self._boundary(h, n, att, pads, spawned)
            if att is not None and att[1] == OK_FAULT and not (v.kind == "fault" and rel == v.end[1]):
                ins = an.p.ins[att[0]] if 0 <= att[0] < len(an.p.ins) else None
                if att[2] == 4 and ins is not None and ins.op == T.PUSH and ins.a == 1:
                    # strict PUSH on a full RX FIFO: fault4 and release on this edge
                    if any((own >> p) & 1 and pads[p] != T.PZ for p in range(8)):
                        return f"strict PUSH fault at +{rel} did not release the pads"
                    h.phase = "end"
                    st.strict_faults += 1
                    self._cover(h)
                    return "end"
                return f"unpredicted fault{att[2]} at +{rel} pc{att[0]}"
            # pads: changes only at predicted events, observed within predicted set
            events = h.sched.pad_events(rel)
            cur = h.cur
            for e in events:
                cur[e[T.EPIN]] = e[T.EAFTER]
            for p in range(8):
                if not (own >> p) & 1:
                    continue
                if not pads[p] & cur[p]:
                    return f"pin{p} pad {T.PAD_TEXT[pads[p]]} at +{rel}, predicted {T.PAD_TEXT[cur[p]]}"
                if pads[p] != prev[p] and not any(e[T.EPIN] == p for e in events):
                    return f"pin{p} changed at +{rel} without a predicted write"
            # narrow the predicted pad set to what was observed (same pin state persists)
            for p in range(8):
                if (own >> p) & 1:
                    cur[p] = pads[p]
            if exp is None:
                return True
            end = v.end
            if v.kind in ("halt", "fault") and rel == end[1]:
                want = OK_DONE if v.kind == "halt" else OK_FAULT
                if att[1] != want:
                    return f"terminal {v.kind} at +{rel} but outcome {att[1]}"
                if v.kind == "fault" and att[2] != end[2]:
                    return f"fault code {att[2]} at +{rel}, predicted {end[2]}"
                h.phase = "end"
                st.terminals += 1
                st.segments_complete += 1
                self._cover(h)
                return "end"
            if att[1] == OK_FAULT:
                return f"unpredicted fault{att[2]} at +{rel} pc{att[0]}"
            if att[1] != OK_DONE:
                return f"non-boundary pc{att[0]} blocked at +{rel}"
            return True
        # boundary phase
        if att is None:
            return f"no attempt while waiting at boundary pc{node.bpc}"
        return self._boundary(h, n, att, pads, spawned)

    def _boundary(self, h: Hyp, n: int, att: tuple, pads: tuple, spawned: list) -> Any:
        an, st = self.an, self.stats
        v = h.sched.v
        bpc = v.end[2]
        if att[0] != bpc:
            return f"attempt pc{att[0]} while at boundary pc{bpc}"
        stall = n - h.arrive
        succ = an.nodes[v.succ]
        if att[1] == OK_FAULT:
            # A bounded wait times out exactly on its LIMIT-th unsuccessful attempt
            # and the fault releases every owned pad on that edge.
            if att[2] == 3 and stall == succ.state.limit - 1 and succ.kind in (T.BLOCK_PIN, T.BLOCK_EVENT):
                if any((an.own >> p) & 1 and pads[p] != T.PZ for p in range(8)):
                    return f"timeout at pc{bpc} did not release the pads"
                st.timeouts += 1
                h.phase = "end"
                st.segments_complete += 1
                self._cover(h)
                return "end"
            return f"fault{att[2]} at boundary pc{bpc} after {stall} stalls"
        for p in range(8):
            if (an.own >> p) & 1 and pads[p] != h.cur[p]:
                return f"pin{p} changed while blocked at pc{bpc}"
        if att[1] == OK_BLOCKED:
            if stall + 1 > succ.max_stall:
                return f"blocked {stall + 1} edges at pc{bpc}, max {succ.max_stall}"
            return True
        # completed
        if stall < succ.min_stall:
            return f"boundary pc{bpc} completed after {stall} stalls, minimum {succ.min_stall}"
        st.boundaries += 1
        st.stall_hist[(succ.kind, "min" if stall == succ.min_stall else ">min")] += 1
        st.segments_complete += 1
        self._cover(h)
        spawned.extend(self.spawn(succ, n, tuple(pads)))
        return "next"

    def _cover(self, h: Hyp) -> None:
        if not self.name.startswith("custom-"):
            self.stats.covered[self.name].add((repr(h.key), h.vi))

    def _fail(self, run: EngineRun, n: int, why: str, hyps: list) -> dict[str, Any]:
        st = self.stats
        st.runs_failed += 1
        where = [f"{T.node_label(self.an, self.an.nodes[h.key])} v{h.vi} anchor={h.anchor}" for h in hyps[:4]]
        detail = {"ok": False, "program": self.name, "engine": run.engine, "label": run.label,
                  "start": run.start, "tick": n, "why": why, "hypotheses": where}
        if len(st.failures) < 20:
            st.failures.append(detail)
        return detail


def hyps_before(survivors: list, reasons: list) -> list:
    return survivors


class Stats:
    def __init__(self) -> None:
        self.runs = self.runs_ok = self.runs_failed = 0
        self.ticks = self.attempts = self.pad_changes = self.pad_matched = 0
        self.boundaries = self.timeouts = self.terminals = self.strict_faults = 0
        self.segments_complete = self.unchecked_ticks = self.runs_unchecked = 0
        self.stall_hist: Counter = Counter()
        self.failures: list[dict] = []
        self.covered: dict[str, set] = defaultdict(set)
        self.per_program: Counter = Counter()
        self.per_label: Counter = Counter()
        self.not_exact: set = set()

    def to_json(self, analyses: dict[str, T.Analysis]) -> dict[str, Any]:
        coverage = {}
        for name, an in analyses.items():
            total = sum(len(nd.variants) for nd in an.iter_nodes())
            coverage[name] = {"variants": total, "observed": len(self.covered.get(name, ())),
                              "exact": an.is_exact()}
        return {"runs": self.runs, "runs_ok": self.runs_ok, "runs_failed": self.runs_failed,
                "ticks_checked": self.ticks, "attempts_checked": self.attempts,
                "pad_changes_observed": self.pad_changes, "pad_changes_matched": self.pad_matched,
                "segments_completed": self.segments_complete, "boundaries_completed": self.boundaries,
                "timeouts_matched": self.timeouts, "terminals_matched": self.terminals,
                "strict_push_faults": self.strict_faults, "unchecked_ticks": self.unchecked_ticks,
                "runs_unchecked_budget": self.runs_unchecked,
                "stall_vs_min": {f"{k[0]}:{k[1]}": v for k, v in sorted(self.stall_hist.items())},
                "per_program_runs": dict(sorted(self.per_program.items())),
                "per_suite_runs": dict(sorted(self.per_label.items())),
                "variant_coverage": coverage, "failures": self.failures,
                "covered": {name: sorted(f"{k}#{vi}" for k, vi in self.covered.get(name, ()))
                            for name in analyses}}


class Validator:
    def __init__(self, repo: Path, extra: list[Path] | None = None) -> None:
        self.repo = repo
        self.images: dict[tuple, str] = {}
        self.programs: dict[str, T.Program] = {}
        paths = sorted((repo / "firmware").glob("*.image.json"))
        for directory in extra or []:
            paths += sorted(Path(directory).glob("*.image.json"))
        for path in paths:
            prog = T.Program.from_image(path)
            name = path.name.removesuffix(".image.json")
            self.images[(prog.words, prog.ownership, prog.open_drain, prog.arch)] = name
            self.programs[name] = prog
        self.analyses: dict[str, T.Analysis] = {}
        self.checkers: dict[str, RunChecker] = {}
        self.stats = Stats()
        self.pending: list[EngineRun] = []
        # Random programs are analyzed on the fly; keep only a few (LRU) to bound memory.
        self.custom_lru: list[str] = []
        self.custom_seen: set[str] = set()
        self.custom_not_exact: set[str] = set()
        self.custom_cache = 32

    def analysis_for(self, run: EngineRun) -> tuple[str, RunChecker]:
        key = (run.words, run.ownership, run.open_drain, run.arch)
        name = self.images.get(key)
        if name is not None and self.programs[name].arch != run.arch:
            name = None
        if name is None:
            digest = hashlib.sha256(repr((run.words, run.ownership, run.open_drain, run.arch)).encode()).hexdigest()[:10]
            name = f"custom-{digest}"
            if name not in self.programs:
                self.programs[name] = T.Program(name, run.arch, run.engine, run.ownership, run.open_drain,
                                                run.words)
        checker = self.checkers.get(name)
        if checker is None:
            an = T.Analysis(self.programs[name], T.Limits(max_contexts=256, max_variants=1024,
                                                         max_events=100_000, total_events=1_500_000))
            self.analyses[name] = an
            checker = self.checkers[name] = RunChecker(an, self.stats, name)
            if name.startswith("custom-"):
                self.custom_seen.add(name)
                if not an.is_exact():
                    self.custom_not_exact.add(name)
        if name.startswith("custom-"):
            if name in self.custom_lru:
                self.custom_lru.remove(name)
            self.custom_lru.append(name)
            while len(self.custom_lru) > self.custom_cache:
                old = self.custom_lru.pop(0)
                self.analyses.pop(old, None)
                self.checkers.pop(old, None)
                self.programs.pop(old, None)
        return name, checker

    def sink(self, run: EngineRun) -> None:
        if len(run.ticks) <= 1 or not run.words:
            return
        name, checker = self.analysis_for(run)
        self.stats.runs += 1
        self.stats.per_program[name] += 1
        self.stats.per_label[run.label] += 1
        checker.check(run)


# ----------------------------------------------------------------------------
# Workloads
# ----------------------------------------------------------------------------

def _import_repo(repo: Path) -> dict[str, Any]:
    test = repo / "test"
    if str(test) not in sys.path:
        sys.path.insert(0, str(test))
    mods = {}
    for name in ("model.reference", "model.host", "model.verification", "harness", "scenarios"):
        mods[name] = importlib.import_module(name)
    try:
        mods["random_gen"] = importlib.import_module("random_gen")
    except Exception:  # noqa: BLE001 - optional
        mods["random_gen"] = None
    return mods


def suite_scenarios(mods: dict, tracer: Tracer) -> list[str]:
    """test/scenarios.py protocol scenarios (as run by test_protocols/test_flagship), model only."""
    H, S = mods["harness"], mods["scenarios"]
    jobs = [("uart_tx", lambda h: S.uart_tx(h, list(b"Tiny Tapeout!"))),
            ("uart_rx", lambda h: S.uart_rx(h, [0x00, 0xFF, 0x55, 0xA5, 0x31, 0x7E]))]
    for mode in range(4):
        jobs.append((f"spi_controller_mode{mode}",
                     lambda h, m=mode: S.spi_controller(h, m, [0xA6, 0x01, 0xFF], [0x5A, 0x80, 0x3C])))
    jobs += [("i2c_write", S.i2c_write), ("i2c_read", S.i2c_read), ("i2c_nack", S.i2c_nack),
             ("flagship", S.flagship)]
    done = []
    for name, fn in jobs:
        tracer.label = f"scenario:{name}"
        H.run_model(fn)
        tracer.flush()
        done.append(name)
    return done


def suite_legacy(mods: dict, tracer: Tracer) -> list[str]:
    """All 25 monorepo differential workloads (test_legacy.py), model only."""
    V = mods["model.verification"]
    names = list(V.SCENARIOS) + list(V.FIRMWARE_SCENARIOS)
    for name in names:
        tracer.label = f"legacy:{name}"
        V.differential_host(name)
        tracer.flush()
    return names


def _load(host: Any, prog: T.Program) -> None:
    host.load(prog.engine, prog.words, ownership=prog.ownership, open_drain=prog.open_drain)


def suite_event(mods: dict, tracer: Tracer, validator: Validator, seed: int) -> list[str]:
    """event-transmitter: host EVENT commands and a pin-7 rising-edge trigger at random times."""
    R, Hm = mods["model.reference"], mods["model.host"]
    prog = validator.programs["event-transmitter"]
    rng = random.Random(seed)
    model = R.Reference()
    host = Hm.Host(model)
    level = {"v": 0, "until": 0}

    def pins(cycle: int) -> int:
        if cycle >= level["until"]:
            level["v"] ^= 1
            level["until"] = cycle + rng.choice((1, 2, 3, 5, 20, 40, 200))
        out = model.outputs()
        return (out.uio_out & out.uio_oe) | (level["v"] << 7)

    tracer.label = "event:event-transmitter"
    host.pins = pins
    host.cycle(reset=True)
    _load(host, prog)
    host.command(0, prog.engine)
    host.command(11, 7 | 0 << 3 | 32)       # TRIGGER: pin7 rising edge
    host.command(4, 1 << prog.engine)
    for _ in range(400):
        choice = rng.random()
        if choice < 0.5:
            host.command(9, 1 << prog.engine)   # EVENT
        host.idle(rng.choice((0, 1, 2, 10, 30, 31, 32, 33, 34, 35, 60, 300)))
    host.command(5, 1 << prog.engine)
    tracer.flush()
    return ["event-transmitter"]


def suite_stress(mods: dict, tracer: Tracer, validator: Validator, seed: int, count: int,
                 first: int, cycles: int) -> list[str]:
    """Every firmware image under random host traffic, random input pins and random events.

    Owned push-pull pins loop back their driven value; open-drain pins are
    wired-AND with a random external pull-down; unowned pins toggle with random
    dwell times. The host randomly writes TX words, drains RX, sends EVENTs and
    occasionally STOPs/restarts or clears a faulted engine."""
    R, Hm = mods["model.reference"], mods["model.host"]
    names = [n for n in sorted(validator.programs) if not n.startswith("custom-")]
    done = []
    for name in names:
        prog = validator.programs[name]
        for index in range(first, first + count):
            rng = random.Random(f"{seed}:{name}:{index}")
            a = prog.arch
            model = R.Reference(R.Config(engines=a.engines, width=a.width, program_words=a.program_words,
                                         fifo_words=a.fifo_words, fused=a.fused, prefetch=a.prefetch))
            host = Hm.Host(model)
            ext = {"v": rng.randrange(256), "until": 0, "pull": 0}
            dwell = rng.choice((1, 3, 8, 20, 64, 200, 1000))

            def pins(cycle: int, model=model, ext=ext, rng=rng, dwell=dwell) -> int:
                if cycle >= ext["until"]:
                    ext["v"] ^= 1 << rng.randrange(8)
                    ext["pull"] = rng.randrange(256) if rng.random() < 0.3 else 0
                    ext["until"] = cycle + 1 + int(rng.expovariate(1 / dwell))
                out = model.outputs()
                driven = out.uio_oe
                level = (out.uio_out & driven) | (ext["v"] & ~driven)
                # open-drain: released lines are pulled up unless an external agent pulls low
                od_released = prog.open_drain & ~driven
                level = (level & ~od_released) | (od_released & ~ext["pull"])
                return level & 0xFF

            tracer.label = f"stress:{name}:{index}"
            host.pins = pins
            host.cycle(reset=True)
            _load(host, prog)
            host.command(0, prog.engine)
            for _ in range(rng.randrange(0, 9)):
                host.write(2, rng.randrange(1 << 16))
            host.command(4, 1 << prog.engine)
            while len(host.cycles) < cycles:
                r = rng.random()
                try:
                    if r < 0.30:
                        host.idle(rng.randrange(1, 400))
                    elif r < 0.55:
                        host.write(2, rng.randrange(1 << 16), timeout=rng.randrange(1, 300))
                    elif r < 0.75:
                        host.read(3, timeout=rng.randrange(1, 300))
                    elif r < 0.85:
                        host.command(9, 1 << prog.engine)
                    elif r < 0.90:
                        host.status(rng.randrange(8))
                    elif r < 0.93:
                        host.command(5, 1 << prog.engine)
                        host.command(7, 1 << prog.engine)
                        host.command(4, 1 << prog.engine)
                    elif r < 0.96:
                        host.command(7, 1 << prog.engine)
                        host.command(4, 1 << prog.engine)
                    else:
                        host.idle(rng.randrange(1000, 5000))
                except TimeoutError:
                    host.window ^= 1       # abandon the partial transfer (window change)
                    host.cycle()
            tracer.flush()
            done.append(f"{name}:{index}")
    return done


def suite_random(mods: dict, tracer: Tracer, seed: int, count: int, first: int, cycles: int) -> list[str]:
    """test/random_gen.py cases: random legal programs on all four engines plus host traffic."""
    H, RG = mods["harness"], mods["random_gen"]
    if RG is None:
        return []
    done = []
    for index in range(first, first + count):
        h = H.ModelHarness()
        case = RG.make_case(seed, index, h.model.config, cycles=cycles)

        async def scenario(hh, case=case):  # noqa: ANN001, ANN202
            await hh.start()
            await RG.run_case(hh, case)

        tracer.label = f"random:{seed:#x}:{index}"
        try:
            H.run_model(scenario, h)
        except AssertionError as exc:  # the case's own checks; timing checks are independent
            print(f"random case {index}: scenario assertion {exc!s:.120}", file=sys.stderr)
        tracer.flush()
        done.append(str(index))
    return done


# Deliberate mis-modellings of the ISA timing rules. Each must make the
# validation of the scenario + legacy workloads FAIL (negative controls).
MUTANTS = {
    "wait-n-cycles": ("dt = ins.imm24 + 1", "dt = ins.imm24"),
    "xfer-late-edge": ("path.t = t + (k + 1) * half", "path.t = t + (k + 1) * half + (1 if k == 3 else 0)"),
    "count-n-iterations": ("new = st._replace(repeat=ins.imm16)",
                           "new = st._replace(repeat=max(0, ins.imm16 - 1))"),
    "loopback-latency-4": ("LOOPBACK_LATENCY = 3 ", "LOOPBACK_LATENCY = 4 "),
    "open-drain-as-push-pull": ("                mask |= P0 if (d and not v) else PZ",
                                "                mask |= (P1 if v else P0) if d else PZ"),
    "halt-keeps-pins": ('self._release(path, pc, "HALT")', 'pass'),
    "set-two-cycles": ("new = st._replace(vk=0xFF, vb=ins.imm24 & 0xFF)",
                       "new = st._replace(vk=0xFF, vb=ins.imm24 & 0xFF); dt = 2"),
}


def suite_mutants(mods: dict, repo: Path) -> dict[str, Any]:
    """Run the scenario + legacy workloads against deliberately wrong analyzers."""
    import types
    global T
    real = T
    source = Path(real.__file__).read_text()
    results = {}
    for name, (old, new) in MUTANTS.items():
        if old not in source:
            results[name] = {"detected": False, "error": "mutation site not found"}
            continue
        mutant = types.ModuleType(f"pe_timing_{name.replace('-', '_')}")
        mutant.__file__ = real.__file__
        sys.modules[mutant.__name__] = mutant
        exec(compile(source.replace(old, new, 1), real.__file__, "exec"), mutant.__dict__)  # noqa: S102
        T = mutant
        try:
            validator = Validator(repo)
            with Tracer(mods["model.reference"], validator.sink) as tracer:
                suite_scenarios(mods, tracer)
                suite_legacy(mods, tracer)
            st = validator.stats
            results[name] = {"detected": st.runs_failed > 0, "runs_failed": st.runs_failed, "runs": st.runs,
                             "first_failure": st.failures[0]["why"] if st.failures else None}
        except Exception as exc:  # noqa: BLE001 - a crash is reported, not counted as detection
            results[name] = {"detected": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            T = real
    return results


def main_validate(args: Any) -> int:
    repo = Path(args.repo).resolve()
    mods = _import_repo(repo)
    validator = Validator(repo, [Path(p) for p in (getattr(args, "extra_images", None) or [])])
    suites = args.suite or ["scenarios", "legacy", "event", "stress"]
    out: dict[str, Any] = {"repo": str(repo), "suites": {}, "tool": f"pe_timing {T.TOOL_VERSION}",
                           "seed": args.seed, "count": args.count, "first": args.first, "cycles": args.cycles}
    started = time.time()
    if "mutants" in suites:
        t0 = time.time()
        out["mutants"] = suite_mutants(mods, repo)
        out["suites"]["mutants"] = {"items": len(out["mutants"]), "seconds": round(time.time() - t0, 1)}
        suites = [s for s in suites if s != "mutants"]
    with Tracer(mods["model.reference"], validator.sink) as tracer:
        for suite in suites:
            t0 = time.time()
            try:
                if suite == "scenarios":
                    items = suite_scenarios(mods, tracer)
                elif suite == "legacy":
                    items = suite_legacy(mods, tracer)
                elif suite == "event":
                    items = suite_event(mods, tracer, validator, args.seed)
                elif suite == "stress":
                    items = suite_stress(mods, tracer, validator, args.seed, args.count, args.first, args.cycles)
                elif suite == "random":
                    items = suite_random(mods, tracer, args.seed, args.count, args.first, args.cycles)
                elif suite == "margins":
                    import pe_margins  # noqa: PLC0415
                    out["margins"] = pe_margins.run_all(mods, validator, tracer, args.seed)
                    items = list(out["margins"])
                else:
                    raise SystemExit(f"unknown suite {suite}")
                out["suites"][suite] = {"items": len(items), "seconds": round(time.time() - t0, 1)}
            except Exception as exc:  # noqa: BLE001
                out["suites"][suite] = {"error": f"{type(exc).__name__}: {exc}",
                                        "trace": traceback.format_exc()[-2000:]}
                tracer.flush()
    firmware = {k: v for k, v in validator.analyses.items() if not k.startswith("custom-")}
    out["stats"] = validator.stats.to_json(firmware)
    out["custom_programs"] = {"count": len(validator.custom_seen),
                              "not_exact": sorted(validator.custom_not_exact)}
    out["seconds"] = round(time.time() - started, 1)
    ok = validator.stats.runs_failed == 0 and not any("error" in s for s in out["suites"].values())
    if "mutants" in out:
        ok = ok and all(m.get("detected") for m in out["mutants"].values())
    if "margins" in out:
        ok = ok and all(m.get("agrees") for m in out["margins"].values())
    out["result"] = "PASS" if ok else "FAIL"
    Path(args.out).write_text(json.dumps(out, indent=1, default=T._json_default))
    s = out["stats"]
    print(f"{out['result']}: {s['runs_ok']}/{s['runs']} engine runs, {s['ticks_checked']} edges, "
          f"{s['attempts_checked']} issue attempts, {s['pad_changes_matched']}/{s['pad_changes_observed']} "
          f"pad changes, {s['segments_completed']} segments, {s['boundaries_completed']} boundaries, "
          f"{s['timeouts_matched']} timeouts ({out['seconds']} s)")
    for f in s["failures"][:5]:
        print("  FAIL", f)
    return 0 if ok else 1


def merge_results(paths: list[Path]) -> dict[str, Any]:
    """Combine validation JSON files (e.g. Slurm array tasks) into one summary."""
    total: dict[str, Any] = {"files": 0, "result": "PASS", "suites": defaultdict(lambda: {"items": 0, "seconds": 0.0}),
                             "stats": Counter(), "failures": [], "covered": defaultdict(set), "variants": {},
                             "stall_vs_min": Counter(), "per_program_runs": Counter(), "custom_programs": 0,
                             "custom_not_exact": set(), "mutants": None, "margins": None, "errors": []}
    keys = ("runs", "runs_ok", "runs_failed", "ticks_checked", "attempts_checked", "pad_changes_observed",
            "pad_changes_matched", "segments_completed", "boundaries_completed", "timeouts_matched",
            "terminals_matched", "strict_push_faults", "unchecked_ticks", "runs_unchecked_budget")
    for path in paths:
        d = json.loads(Path(path).read_text())
        total["files"] += 1
        if d.get("result") != "PASS":
            total["result"] = "FAIL"
        for name, s in d.get("suites", {}).items():
            if "error" in s:
                total["errors"].append(f"{path}: {name}: {s['error']}")
                total["result"] = "FAIL"
                continue
            total["suites"][name]["items"] += s["items"]
            total["suites"][name]["seconds"] += s["seconds"]
        st = d.get("stats", {})
        for k in keys:
            total["stats"][k] += st.get(k, 0)
        total["stall_vs_min"].update(st.get("stall_vs_min", {}))
        for prog, n in st.get("per_program_runs", {}).items():
            total["per_program_runs"]["custom programs" if prog.startswith("custom-") else prog] += n
        total["failures"] += st.get("failures", [])[:5]
        for name, cov in st.get("variant_coverage", {}).items():
            total["variants"][name] = cov["variants"]
        for name, items in st.get("covered", {}).items():
            total["covered"][name].update(items)
        cp = d.get("custom_programs", {})
        total["custom_programs"] += cp.get("count", 0)
        total["custom_not_exact"].update(cp.get("not_exact", []))
        if d.get("mutants"):
            total["mutants"] = d["mutants"]
        if d.get("margins"):
            total["margins"] = {k: {kk: vv for kk, vv in v.items() if kk != "rows"} | {"rows": v.get("rows")}
                                for k, v in d["margins"].items()}
    coverage = {name: {"variants": n, "observed": len(total["covered"].get(name, ())),
                       "percent": round(100 * len(total["covered"].get(name, ())) / n, 1) if n else 100.0}
                for name, n in sorted(total["variants"].items())}
    stats = dict(total["stats"])
    stats.update({"stall_vs_min": dict(total["stall_vs_min"]), "per_program_runs": dict(total["per_program_runs"]),
                  "variant_coverage": coverage, "failures": total["failures"][:20]})
    return {"result": total["result"], "files": total["files"], "suites": dict(total["suites"]), "stats": stats,
            "custom_programs": {"count": total["custom_programs"],
                                "not_exact": sorted(total["custom_not_exact"])[:50],
                                "not_exact_count": len(total["custom_not_exact"])},
            "mutants": total["mutants"], "margins": total["margins"], "errors": total["errors"]}
