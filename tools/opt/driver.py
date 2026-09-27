#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Autonomous physical-design optimizer (docs/optimization.md).

  driver.py run        the main loop (run as a Slurm job by driver_job.sh)
  driver.py once       one loop iteration (with --dry-run: plan, build snapshots, submit nothing)
  driver.py status     one-screen summary of the campaign
  driver.py report     rewrite leaderboard.md / leaderboard.csv / manifest.json

The driver keeps up to CPU_CAP CPUs of LibreLane runs in flight through the
sweep harness (scripts/sweep/make_snapshot.py builds each run snapshot,
scripts/sweep/run_one.sh runs it, wrapped by tools/opt/trial_job.sh). It runs
several tracks (tracks.py): the design of record at 20, 15 and 13.33 ns, the
6x4 diet4 fallback and one track per published variant core, each with its own
optuna TPE study. It records every trial in the append-only store (store.py),
imports finished trials of earlier tracks whose effective configuration is
identical, shares the CPUs between the tracks by weight, retires dominated
variant tracks, promotes the best configurations of each track to a full-mode
run with OPENROAD_THREADS 4, then the TT precheck, the gate-level cocotb subset
and the RTL-vs-netlist equivalence check (gates.py: the verdict is PASS only if
all four pass), and rewrites the leaderboard after every completed trial. It
resumes from the store after preemption, and resubmits itself before its time
limit until the budget is spent, the stop date is reached or <opt root>/STOP
exists. Knobs in space.FIXED are not sampled, and trials that set them to
another value are not promoted, seeded or transferred.

Runtime (runtime.py; docs/optimization.md, "Runtime and the 6-hour limit"): every
run's step times are recorded (in its metrics; runs from before v3 once from their
result.json). In the tracks of the kinds in runtime.RULE_KINDS (dor15, dor13 and
diet4_6x4, which the gds and gds_6x4 actions build) a trial is promoted only if the
official job projected from its step times fits runtime.BOUND_S (5.5 h), trials rank
by min WS and then that projection, the TPE value of an ineligible trial is lowered
(also for a duplicate of a trial told before v3), trial jobs get TRIAL_RULE_TIME, and
the leaderboard's promotion verdict requires the full run's projection to fit.

Environment: PE_WORK (cluster work directory: sram-flow/, variants/,
drc-triage/, cocotb/, host/), OSS_CAD_SUITE (yosys and yosys-abc for the
equivalence check; else PATH), optional PE_OPT_ROOT (default
$PE_WORK/optimizer), optional PE_EQ_CHECK (overrides the equivalence checker,
formal_eq/eq_check.py of the frozen tree; eq_job.sh). The driver runs from a
frozen export of the repository (launch.sh); the scripts/sweep, src/,
floorplans/, variants6x4/ and formal_eq/ it uses are that export's.
"""

import argparse
import datetime
import glob
import gzip
import json
import os
import random
import shutil
import socket
import sys
import time
import traceback
import warnings

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(TREE, "scripts", "sweep"))

if not os.environ.get("PE_WORK"):
    raise SystemExit("set PE_WORK (the cluster work directory)")
WORK = os.environ["PE_WORK"]
OPT = os.environ.get("PE_OPT_ROOT", os.path.join(WORK, "optimizer"))

import gates as GATES  # noqa: E402
import objective as OBJ  # noqa: E402
import report as REPORT  # noqa: E402
import runtime as RT  # noqa: E402
import slurm as SL  # noqa: E402
import space as SPACE  # noqa: E402
import tracks as TR  # noqa: E402
from store import State, Store  # noqa: E402

# ---------------------------------------------------------------- campaign settings
LABEL = "optimizer"
JOB_PREFIX = "pe-v2-%s-" % LABEL
PARTITIONS = "mit_preemptable,mit_normal"
CPU_CAP = 700                    # all pe-v2-optimizer-* jobs, queued + running, driver included (per-user cap 1024;
                                 # 960 until v3, which leaves CPUs to the other workstreams)
USER_JOB_CAP = 400               # stay below the 448 submitted-job limit per user (all workstreams)
MAX_SUBMIT_PER_LOOP = 24
MAX_NOSUBMIT_PER_LOOP = 40       # duplicate/rejected trials per loop (they use no CPUs)
MAX_NOSUBMIT_PER_TRACK = 4       # ... per track and loop, after which the track waits for the next loop
BUDGET_RUNS = 3000               # LibreLane runs (trials + promoted full runs), whole campaign since v1
PROMO_RESERVE = 20               # runs kept back from trials for the final promotions
STOP_AT = datetime.datetime(2026, 10, 24, 0, 0, 0)   # local time of the cluster
# Share of new trials per weight group (tracks.py); a variant track gets WEIGHTS["variant"]
# divided by the number of variant tracks still receiving trials. Groups without a track
# that may receive a trial drop out, and the others keep their proportions. The shares
# count the trials submitted since the first driver start with the current WEIGHTS
# (weights_epoch()), so a change of weights applies to the trials that follow it.
# Since d76f1cc the committed CLOCK_PERIOD is 15 ns (p018), so dor15 is the committed track
# (weights until then: dor20 0.35, dor15 0.22, dor13 0.08; from d76f1cc to v3: dor20 0.15,
# dor15 0.35, dor13 0.15, 6x4 0.20, variant 0.15). v3 aims at an official sign-off at 13.33 ns
# (docs/optimization.md, "The 75 MHz plan"), so dor13 is the main track.
WEIGHTS = {"dor20": 0.05, "dor15": 0.15, "dor13": 0.45, "6x4": 0.20, "variant": 0.15}
VARIANT_TRIAL_CAP = 200          # trials per variant track
# Retirement of a variant track (docs/optimization.md, "Retirement"): at least
# RETIRE_MIN_TRIALS finished trials, and either no legal trial, or its best legal min WS
# at least RETIRE_WS_MARGIN below the design of record's (20 ns) best while its
# utilization is not RETIRE_UTIL_MARGIN or more below the design of record's.
RETIRE_MIN_TRIALS = 40
RETIRE_WS_MARGIN = 0.25
RETIRE_UTIL_MARGIN = 0.05
MAX_PROMOS_IN_FLIGHT = 5
PROMO_MIN_TRIALS = {"dor": 0, "freq": 10, "6x4": 10, "variant": 20}
PERIODIC_EVERY = 50              # dor/freq: also promote a top-3 trial every N new trials
PROMO_MIN_GAIN = 0.05           # ns of min WS over the best promoted trial of the track
SEED_TOP = {"freq": 8, "6x4": 5, "variant": 3}   # best 20 ns configurations seeded into new tracks
# Seed revisions: seeds added after tracks existed are enqueued once into every active track
# whose recorded revision is lower (track_seeds events). 1: the seeds of the v2 launch.
# 2: for the frequency tracks, the committed and the best 20 ns configurations with
# PL_RESIZER_SETUP_SLACK_MARGIN scaled by period / 20 ns (the first 13.33 ns trials, with the
# 20 ns margins of 5.75 to 6 ns, were still in post-CTS repair after 3 hours).
# 3 (v3): for the frequency tracks (the rule tracks whose space has the knob), the
# RUNTIME_SEED_TOP best legal trials of the track whose projected official job exceeds the bound
# (runtime.py), each with PL_RESIZER_SETUP_REPAIR_TNS_PCT at each of RUNTIME_SEED_TNS (the
# runtime knob, space.py). On the committed configuration at
# 13.33 ns (p024's), 0 gave slow WS -0.988 ns and 10 gave +0.142 ns with the repair cut from
# 10,842 to 3,272 normalized seconds (jobs 24120814, 24120815; docs/optimization.md, "The 75 MHz
# plan"), so 0 is not seeded.
SEED_REVISION = 3
RUNTIME_SEED_TOP = 3
RUNTIME_SEED_TNS = (25, 10)
KEEP_FINAL_TOP = 10              # keep out/final of the top-N legal trials per track
RESULT_GRACE_S = 600             # a finished job's result may appear late on the pool
MAX_ATTEMPTS = 3
LOOP_S = 60
DRIVER_RESUBMIT_S = 1800
DISCOVER_S = 600

TRIAL = dict(cpus=32, mem="64G", time="06:00:00", threads=32, mode="fast")
# Time limit of a trial of a track with the runtime rule (runtime.RULE_KINDS). An eligible trial's
# flow takes at most runtime.trial_time_limit_s() = 16,710 s (4.64 h) on the slowest local node
# measured (speed factor 2.15); the job gets USR1 900 s before its limit, which leaves 4.75 h.
TRIAL_RULE_TIME = "05:00:00"
PFULL = dict(cpus=8, mem="32G", time="11:00:00", threads=4, mode="full")
PCHECK = dict(cpus=16, mem="48G", time="02:00:00")
PGL = dict(cpus=4, mem="16G", time="04:00:00")
# Equivalence check (eq_job.sh -> formal_eq/eq_check.py; docs/equivalence.md section 1): ABC
# dprove proved the 6x4 netlists (5,139 latches) equivalent in 67-71 s and the 8x4 netlists
# (7,795 latches) in 349-692 s (p018 alone: 446 s, job 24093888); it gave up (undecided) on a
# one-gate mutant after 1,708 s. The limit is 3,600 s for ABC, 1,200 s for each yosys step,
# and 3,600 + 1,200 s for the whole checker. Anything but "equivalent" fails (gates.py); only
# a time limit is resubmitted, up to MAX_ATTEMPTS jobs in all (gates.eq_should_retry).
EQ_LIMIT_S = 3600
EQ_CHECK = os.environ.get("PE_EQ_CHECK") or os.path.join(TREE, "formal_eq", "eq_check.py")
PEQ = dict(cpus=2, mem="8G", time="01:30:00")
DRIVER = dict(cpus=2, mem="8G", time="2-00:00:00")

TOP = "tt_um_teslacoilerow_protocol_emulator"
PATHS = {
    "store": os.path.join(OPT, "store", "events.jsonl"),
    "runs": os.path.join(OPT, "runs"),
    "logs": os.path.join(OPT, "logs"),
    "studies": os.path.join(OPT, "studies"),
    "cores": os.path.join(OPT, "cores"),
    "promos": os.path.join(OPT, "promotions"),
    "trees": os.path.join(OPT, "tree"),
    "stop": os.path.join(OPT, "STOP"),
    "finished": os.path.join(OPT, "FINISHED"),
    "lease": os.path.join(OPT, "driver.lease"),
    "log": os.path.join(OPT, "driver.log"),
}

# The 6x4 configuration signed off before the p010 adoption (docs/6x4.md section 4:
# variants6x4 overlay on the c118027 src/config.json, which set none of the timing keys).
PRE_ADOPTION_6X4 = dict({n: SPACE.KNOBS[n]["unset"] for n in SPACE.SIMPLE},
                        abc_fine_tune="none", gpl_pad=0, grt_adj_m2=0.0, grt_adj_m3=0.0, grt_adj_m4=0.0,
                        pl_hold=0.1, density=60)


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return default


def write_json_atomic(path, obj):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def tree_info():
    return load_json(os.path.join(TREE, "OPT_TREE.json"), {}) or {}


def track_period(tr):
    return float(tr.get("period") or 20.0)


def clean_knobs(knobs):
    """Knob set without retired knobs at their accepted value; a complete stored knob set from
    before a LATE knob existed gets that knob's unset value, the value its run used
    (space.upgrade)."""
    return SPACE.upgrade({k: v for k, v in (knobs or {}).items()
                          if not (k in SPACE.RETIRED and v == SPACE.RETIRED[k])})


# ---------------------------------------------------------------- optuna
def open_study(track):
    import optuna
    from optuna.storages import JournalStorage
    from optuna.storages.journal import JournalFileBackend
    warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    os.makedirs(PATHS["studies"], exist_ok=True)
    storage = JournalStorage(JournalFileBackend(os.path.join(PATHS["studies"], track + ".journal")))
    sampler = optuna.samplers.TPESampler(n_startup_trials=12, multivariate=True, group=True, constant_liar=True,
                                         n_ei_candidates=48)
    return optuna.create_study(study_name=track, storage=storage, sampler=sampler, direction="maximize",
                               load_if_exists=True)


# ---------------------------------------------------------------- the driver
class Driver(object):
    def __init__(self, dry_run=False):
        self.dry = dry_run
        for k in ("runs", "logs", "studies", "cores", "promos"):
            os.makedirs(PATHS[k], exist_ok=True)
        self.store = Store(PATHS["store"])
        self.state = State(self.store.events())
        self.studies = {}
        self.live = {}          # uid -> optuna Trial asked in this process
        self.dirty = True
        self.backoff_until = 0
        self.last_scan = 0
        self.pruned = set()
        self.job_id = os.environ.get("SLURM_JOB_ID")
        self.tinfo = tree_info()
        self.free = 0
        self.n_user = 0
        self.transferred = set()
        self.T = {}             # track id -> tracks.Track of the frozen tree (the active tracks)
        self.active_tracks, self.primary = [], None
        self.legacy_cfg = {}
        self.import_skip = set()
        self.promo_skip = set()  # trials whose promotion snapshot could not be built
        self._ctx = None         # runtime.Context of the current state (rebuilt after every event)
        if dry_run and os.path.abspath(OPT) == os.path.abspath(os.path.join(WORK, "optimizer")):
            raise SystemExit("--dry-run needs a scratch PE_OPT_ROOT (it records fake job ids)")
        self.import_sweep()

    def import_sweep(self):
        import make_snapshot as MS  # noqa: E402 (from the frozen tree)
        self.MS = MS

    def owns(self, path):
        """True for a path inside this optimizer root (a dry run never touches the real runs)."""
        root = os.path.realpath(OPT) + os.sep
        return bool(path) and os.path.realpath(path).startswith(root)

    def emit(self, ev, **kw):
        rec = self.store.append(ev, **kw)
        self.state.apply(rec)
        self._ctx = None
        return rec

    # ------------------------------------------------------------ runtime (runtime.py)
    def ctx(self):
        if self._ctx is None:
            self._ctx = RT.Context(self.state)
        return self._ctx

    def is_rule(self, track):
        """True for a track with the runtime rule (runtime.RULE_KINDS: dor15, dor13)."""
        return (self.state.tracks.get(track) or {}).get("kind") in RT.RULE_KINDS

    def projection(self, t, metrics):
        """Projected official gds job seconds of a trial from its (new) metrics."""
        ctx = self.ctx()
        rt = RT.from_metrics(metrics) or {}
        key = ctx.key(t)
        ctx.speed.add(key, rt.get("synth_s"))
        return RT.project_trial(rt, ctx.speed.factor(key, rt.get("synth_s")))

    def backfill_runtime(self):
        """Record the step runtimes of runs that finished before the metrics carried them
        (trial_runtime / promo_runtime events; read once from each run's result.json)."""
        n = 0
        for t in list(self.state.trials.values()):
            if t["state"] != "done" or t.get("dup_of") or not t.get("run_dir") or \
                    RT.Context.trial_runtime(t) is not None:
                continue
            res = load_json(os.path.join(t["run_dir"], "result.json"))
            resolved = load_json(os.path.join(t["run_dir"], "out", "resolved.json"))
            self.emit("trial_runtime", uid=t["uid"], runtime=RT.extract(res, resolved) if res else {"missing": True})
            n += 1
        for p in list(self.state.promos.values()):
            full = p["stages"].get("full") or {}
            if full.get("state") != "done" or RT.Context.promo_runtime(p) is not None:
                continue
            res = load_json(os.path.join(p["run_dir"], "result.json"))
            resolved = load_json(os.path.join(p["run_dir"], "out", "resolved.json"))
            self.emit("promo_runtime", pid=p["pid"], runtime=RT.extract(res, resolved) if res else {"missing": True})
            n += 1
        if n:
            log("recorded the step runtimes of %d earlier runs" % n)

    def promo_runtime_ok(self, t):
        """False if every promotion that stands for trial t fails the runtime rule
        (runtime.Context.promo_ok), else True."""
        ctx = self.ctx()
        ps = [p for p in self.state.promos.values() if p["uid"] in self.chain(t)]
        return not ps or any(ctx.promo_ok(p) is not False for p in ps)

    # ------------------------------------------------------------ tracks
    def ensure_core_copy(self, src, name, sha):
        dst = os.path.join(PATHS["cores"], "%s-%s.v" % (name, sha[:12]))
        if not os.path.exists(dst):
            tmp = dst + ".tmp.%d" % os.getpid()
            shutil.copyfile(src, tmp)
            if TR.sha256_file(tmp) != sha:
                os.remove(tmp)
                raise RuntimeError("core %s changed while copying" % src)
            os.replace(tmp, dst)
        return dst

    def discover_tracks(self):
        """Tracks of the frozen tree (tracks.discover); new ones are recorded, filled with the
        identical finished trials of earlier tracks, then seeded."""
        new = []
        active = []
        for t in TR.discover(TREE, WORK, log):
            try:
                t.core = self.ensure_core_copy(t.core, t.name, t.core_sha)
            except (IOError, OSError, RuntimeError) as e:
                log("track %s: %s" % (t.id, e))
                continue
            if t.id not in self.state.tracks:
                self.emit("track_new", tree=self.tinfo, **t.record())
                new.append(t.id)
                log("new track %s (%s, %s, %s ns)" % (t.id, t.kind, t.tiles, t.period))
            self.T[t.id] = t
            active.append(t.id)
        self.active_tracks = active
        self.primary = next((i for i in active if self.T[i].kind == "dor"), None)
        self.repair_imports()
        self.import_trials()
        for tid in new:
            self.seed_track(tid, self.seeds(tid) + [x for r in range(2, SEED_REVISION + 1)
                                                    for x in self.late_seeds(tid, r)])
            self.emit("track_seeds", track=tid, revision=SEED_REVISION)
        for tid in active:
            if tid in new:
                continue
            rev = int(self.state.tracks[tid].get("seed_revision") or 1)
            if rev < SEED_REVISION:
                late = [x for r in range(rev + 1, SEED_REVISION + 1) for x in self.late_seeds(tid, r)]
                if late:
                    self.seed_track(tid, late)
                self.emit("track_seeds", track=tid, revision=SEED_REVISION)

    def seeds(self, tid):
        t = self.T[tid]
        out = [("baseline (committed configuration%s)" % ("" if t.period == 20 else ", CLOCK_PERIOD %g" % t.period),
                {})]
        best20 = self.ranked(self.primary, in_space=True) if self.primary else []
        if t.kind == "6x4":
            out.append(("6x4 point signed off before the p010 adoption (docs/6x4.md)", PRE_ADOPTION_6X4))
            for i, b in enumerate(best20[:SEED_TOP["6x4"]], 1):
                out.append(("20 ns 8x4 best #%d %s (floorplan/halo of the 6x4 build)" % (i, b["uid"]), b["knobs"]))
            for b in self.legacy_ranked(t.core_sha, "8x4")[:3]:
                out.append(("8x4 diet4 trial %s (floorplan/halo of the 6x4 build)" % b["uid"], b["knobs"]))
        elif t.kind in ("freq", "variant"):
            for i, b in enumerate(best20[:SEED_TOP[t.kind]], 1):
                out.append(("20 ns best #%d %s" % (i, b["uid"]), b["knobs"]))
        return out

    def late_seeds(self, tid, rev):
        """Seeds of revision `rev` (see SEED_REVISION) for one track."""
        t = self.T[tid]
        out = []
        if rev == 2 and t.kind == "freq":
            best20 = self.ranked(self.primary, in_space=True)[:SEED_TOP["freq"]] if self.primary else []
            for i, b in enumerate([{"uid": None, "knobs": {}}] + best20):
                try:
                    kn = SPACE.fix(t.complete(clean_knobs(b["knobs"])))
                except ValueError:
                    continue
                m = kn["PL_RESIZER_SETUP_SLACK_MARGIN"]
                scaled = round(round(m * t.period / 20.0 / 0.05) * 0.05, 2)
                if abs(scaled - m) < 1e-9:
                    continue
                kn["PL_RESIZER_SETUP_SLACK_MARGIN"] = scaled
                out.append(("%s, setup margin scaled to the period (%.2f -> %.2f ns)"
                            % ("committed configuration" if b["uid"] is None else "20 ns best #%d %s" % (i, b["uid"]),
                               m, scaled), kn))
        if rev == 3 and t.kind in RT.RULE_KINDS and "PL_RESIZER_SETUP_REPAIR_TNS_PCT" in t.D:
            ctx = self.ctx()
            over = [b for b in self.ranked(tid, in_space=True) if not ctx.trial(b)["eligible"]]
            bases = [("best #%d over the runtime bound %s" % (i, b["uid"]), b["knobs"])
                     for i, b in enumerate(over[:RUNTIME_SEED_TOP], 1)]
            for label, partial in bases:
                try:
                    kn = SPACE.fix(t.complete(clean_knobs(partial)))
                except ValueError:
                    continue
                for v in RUNTIME_SEED_TNS:
                    k2 = dict(kn)
                    k2["PL_RESIZER_SETUP_REPAIR_TNS_PCT"] = v
                    out.append(("%s, PL_RESIZER_SETUP_REPAIR_TNS_PCT %s" % (label, v), k2))
        return out

    def legacy_ranked(self, core_sha, tiles):
        """Best legal trials of other tracks (active or not) with this core and die."""
        ts = [t for t in self.state.trials.values() if t["state"] == "done" and t.get("legal") and t.get("metrics")
              and not t.get("dup_of") and not t.get("imported") and not SPACE.outside(t.get("knobs"))
              and self.state.tracks.get(t["track"], {}).get("core_sha256") == core_sha
              and (self.state.tracks[t["track"]].get("tiles") or "8x4") == tiles]
        return sorted(ts, key=lambda t: OBJ.key(t["metrics"], True), reverse=True)

    def seed_track(self, track, seeds):
        T = self.T[track]
        study = self.study(track)
        seen = set()
        for label, partial in seeds:
            try:
                kn = SPACE.fix(T.complete(clean_knobs(partial)) if T.space == "8x4" or not partial else
                               SPACE.translate(clean_knobs(partial), T.base, T.D))
            except ValueError as e:
                log("seed %s for %s skipped: %s" % (label, track, e))
                continue
            k = SPACE.canonical(kn)
            if k in seen:
                continue
            seen.add(k)
            study.enqueue_trial(kn, user_attrs={"label": label})
        log("track %s: %d seeds enqueued" % (track, len(seen)))

    def study(self, track):
        if track not in self.studies:
            self.studies[track] = open_study(track)
        return self.studies[track]

    # ------------------------------------------------------------ import of identical trials
    def tree_config(self, commit):
        """src/config.json of the frozen tree of a commit (any tools version: src/ is the commit's)."""
        if commit not in self.legacy_cfg:
            cands = sorted(glob.glob(os.path.join(PATHS["trees"], "%s-*" % commit[:12], "src", "config.json")))
            if not cands and os.path.abspath(OPT) != os.path.abspath(os.path.join(WORK, "optimizer")):
                cands = sorted(glob.glob(os.path.join(WORK, "optimizer", "tree", "%s-*" % commit[:12], "src",
                                                      "config.json")))
            self.legacy_cfg[commit] = load_json(cands[0]) if cands else None
        return self.legacy_cfg[commit]

    def import_trials(self):
        """Record, in each active track, the finished trials of inactive tracks with the same
        core, die and period whose effective configuration equals the one this track's knob
        set gives (no new LibreLane run), and add them to the track's study."""
        import optuna
        targets = {}
        for tid in self.active_tracks:
            T = self.T[tid]
            targets.setdefault((T.core_sha, T.tiles, T.period), tid)
        have = {(t["track"], t.get("imported_from")) for t in self.state.trials.values() if t.get("imported")}
        n_new = 0
        for s in sorted(self.state.trials.values(), key=lambda t: (t["track"], t["number"])):
            if s["track"] in self.T or s["state"] != "done" or s.get("lost") or s.get("dup_of") or \
                    s.get("imported") or not s.get("metrics") or not s.get("config_changes") or \
                    s["uid"] in self.import_skip:
                continue
            tr = self.state.tracks.get(s["track"]) or {}
            tid = targets.get((tr.get("core_sha256"), tr.get("tiles") or "8x4", track_period(tr)))
            if tid is None or (tid, s["uid"]) in have:
                continue
            T = self.T[tid]
            why = None
            try:
                kn = T.complete(clean_knobs(s["knobs"]))
                ref = self.tree_config((s.get("tree") or {}).get("commit") or "")
                if ref is None:
                    why = "frozen tree of %s not found" % (s.get("tree") or {}).get("commit")
                else:
                    old = TR.apply_changes(ref, s["config_changes"])
                    new = T.effective(kn, threads=int(old.get("OPENROAD_THREADS") or TRIAL["threads"]))
                    d = TR.changes(old, new)
                    if d:
                        why = "effective configuration differs in %s" % ", ".join(sorted(d))
            except (ValueError, KeyError) as e:
                why = "knobs outside the current space: %s" % e
            if why:
                self.import_skip.add(s["uid"])
                continue
            study = self.study(tid)
            dist = SPACE.distributions(T.D)
            val = float(s.get("value") if s.get("value") is not None else OBJ.FAIL_VALUE)
            vobj = None
            if self.is_rule(tid) and s.get("metrics"):
                vobj = s.get("value_objective") if s.get("value_objective") is not None else val
                val = RT.penalized_value(vobj, self.ctx().trial(s)["proj_s"])
            try:
                ft = optuna.trial.create_trial(value=val, params=kn, distributions={n: dist[n] for n in kn},
                                               user_attrs={"label": "imported", "imported_from": s["uid"]})
            except ValueError as e:
                log("import %s: %s" % (s["uid"], e))
                self.import_skip.add(s["uid"])
                continue
            number = len(study.get_trials(deepcopy=False))
            study.add_trial(ft)
            self.record_import(tid, number, kn, s, value=val, value_objective=vobj)
            have.add((tid, s["uid"]))
            n_new += 1
        if n_new:
            log("imported %d finished trials of earlier tracks" % n_new)
            self.dirty = True

    def repair_imports(self):
        """An import is added to the study first and recorded in the store second; record the
        imports a stopped driver added to a study but not to the store."""
        import optuna
        for tid in self.active_tracks:
            if not os.path.exists(os.path.join(PATHS["studies"], tid + ".journal")):
                continue
            known = {t["number"] for t in self.state.trials_of(tid)}
            for ft in self.study(tid).get_trials(deepcopy=False, states=(optuna.trial.TrialState.COMPLETE,)):
                src = (ft.user_attrs or {}).get("imported_from")
                if src and ft.number not in known and src in self.state.trials:
                    self.record_import(tid, ft.number, dict(ft.params), self.state.trials[src], value=ft.value)
                    log("recorded the import of %s as %s#%d" % (src, tid, ft.number))

    def record_import(self, tid, number, kn, s, value=None, value_objective=None):
        T = self.T[tid]
        m = OBJ.derive_fmax(dict(s["metrics"]), T.period)
        self.emit("trial_import", uid="%s#%d" % (tid, number), track=tid, number=number, knobs=kn,
                  key=SPACE.canonical(kn), label="imported from %s" % s["uid"], imported_from=s["uid"],
                  metrics=m, legal=s.get("legal"), blockers=s.get("blockers"), magnitude=s.get("magnitude"),
                  value=s.get("value") if value is None else value, value_objective=value_objective,
                  runtime=s.get("runtime"), run_id=s.get("run_id"), run_dir=s.get("run_dir"),
                  job_id=s.get("done_job") or s.get("job_id"), snap=s.get("snap"), overrides=s.get("overrides"),
                  config_changes=T.diff_vs_repo(kn), source_config_changes=s.get("config_changes"))

    # ------------------------------------------------------------ bookkeeping helpers
    def ranked(self, track, legal_only=True, in_space=False):
        """Finished trials of a track, best first. in_space: only trials inside the current
        search space (no FIXED knob at another value, space.outside()); those are the ones
        the driver promotes, seeds and transfers."""
        ts = [t for t in self.state.trials_of(track) if t["state"] == "done" and not t.get("lost")
              and t.get("metrics") and not t.get("dup_of")]
        if legal_only:
            ts = [t for t in ts if t.get("legal")]
        if in_space:
            ts = [t for t in ts if not SPACE.outside(t.get("knobs"))]
        if self.is_rule(track):
            # min WS (10 ps), then the projected official runtime (runtime.rank_key)
            ctx = self.ctx()
            return sorted(ts, key=lambda t: RT.rank_key(OBJ.key(t["metrics"], t.get("legal")),
                                                        ctx.trial(t)["proj_s"]), reverse=True)
        return sorted(ts, key=lambda t: OBJ.key(t["metrics"], t.get("legal")), reverse=True)

    def chain(self, t):
        """uids a trial stands for: itself, the trial it duplicates and the trial it was imported from."""
        out = {t["uid"]}
        seen = 0
        cur = t
        while cur is not None and seen < 5:
            seen += 1
            nxt = cur.get("dup_of") or cur.get("imported_from")
            if not nxt or nxt in out:
                break
            out.add(nxt)
            cur = self.state.trials.get(nxt)
            if cur is not None and cur.get("imported_from"):
                out.add(cur["imported_from"])
        return out

    def track_key(self, t):
        return t.get("key") or SPACE.canonical(self.T[t["track"]].complete(clean_knobs(t["knobs"])))

    def jobs(self):
        j = SL.my_jobs()
        if j is None:
            raise RuntimeError("squeue failed")
        return j

    def my_cpus(self, jobs):
        return sum(v["cpus"] for v in jobs.values() if v["name"].startswith(JOB_PREFIX) and v["state"] in SL.ACTIVE)

    # ------------------------------------------------------------ submission
    def export_env(self, extra=None):
        env = ["ALL", "PE_WORK=" + WORK, "PE_OPT_ROOT=" + OPT, "PE_HARNESS=" + os.path.join(TREE, "scripts", "sweep"),
               "PE_OPT_HARNESS=" + HERE, "PE_FLOW_ROOT=" + os.path.join(WORK, "sram-flow"),
               "PE_OPT_PY=" + os.path.join(OPT, "venv", "bin", "python")]
        if os.environ.get("OSS_CAD_SUITE"):  # yosys/yosys-abc of the equivalence check (eq_job.sh)
            env.append("OSS_CAD_SUITE=" + os.environ["OSS_CAD_SUITE"])
        for k, v in (extra or {}).items():
            env.append("%s=%s" % (k, v))
        return "--export=" + ",".join(env)

    def submit(self, name, res, script, args, extra_env=None, signal=True):
        """sbatch one job if the CPU cap, the per-user job cap and the backoff allow it."""
        if time.time() < self.backoff_until:
            return None
        if self.free < res["cpus"] or self.n_user >= USER_JOB_CAP:
            return None
        opts = ["-J", JOB_PREFIX + name, "-p", PARTITIONS, "--requeue", "-N", "1", "-n", "1",
                "-c", str(res["cpus"]), "--mem", res["mem"], "-t", res["time"], "--open-mode=append",
                "-o", os.path.join(PATHS["logs"], "%x-%j.out"), self.export_env(extra_env)]
        if signal:
            opts += ["--signal=B:USR1@900"]
        jid, err = SL.sbatch(opts, [script] + list(args), dry_run=self.dry)
        if not jid:
            log("sbatch failed for %s: %s; backing off 5 min" % (name, err))
            self.backoff_until = time.time() + 300
            return None
        self.free -= res["cpus"]
        self.n_user += 1
        return jid

    def build_snapshot(self, track, knobs, mode, threads, runs_sub):
        T = self.T[track]
        snap, ov = T.materialize(knobs)
        q = self.MS.normalize_params(dict(core=T.core, core_tag=T.name, tiles=T.tiles, period=T.period,
                                          mode=mode, threads=threads, overrides=ov, **snap))
        q["tag"] = "opt"
        runs_dir = os.path.join(PATHS["runs"], runs_sub)
        run_dir, params = self.MS.build(q, runs_dir, quiet=True)
        cfg = load_json(os.path.join(run_dir, "snap", "src", "config.json"))
        n_isl = TR.islands(TREE, cfg, os.path.join(run_dir, "snap", "src"), T.tiles)
        if n_isl:
            shutil.rmtree(os.path.join(run_dir, "snap"), ignore_errors=True)
            raise ValueError("variants6x4/row_islands.py: %d row segment(s) without a lattice stripe "
                             "(short power straps)" % n_isl)
        mirror = TR.changes(self.T[track].repo_cfg, TR.effective_config(
            T.repo_cfg, T.floorplans[snap["floorplan"]], snap, ov, T.period, threads))
        got = {k: v for k, v in (params.get("config_changes_vs_repo") or {}).items() if k not in TR.LOCAL_ONLY_KEYS}
        if json.dumps(mirror, sort_keys=True) != json.dumps(got, sort_keys=True):
            log("WARNING: tracks.effective_config differs from make_snapshot for %s" % params.get("run_id"))
            params["mirror_mismatch"] = True
        return run_dir, params, snap, ov

    def maybe_transfer(self, track):
        """Offer the design of record's (20 ns) current best legal configuration to every other
        track once per distinct best."""
        if track == self.primary or not self.primary:
            return
        ranked = self.ranked(self.primary, in_space=True)
        if not ranked:
            return
        T = self.T[track]
        try:
            kn = SPACE.fix(T.complete(clean_knobs(ranked[0]["knobs"])) if T.space == "8x4"
                           else SPACE.translate(clean_knobs(ranked[0]["knobs"]), T.base, T.D))
        except ValueError:
            return
        key = SPACE.canonical(kn)
        if (track, key) in self.transferred:
            return
        self.transferred.add((track, key))
        if any(self.track_key(t) == key for t in self.state.trials_of(track) if t.get("knobs")):
            return
        import optuna
        study = self.study(track)
        for ft in study.get_trials(deepcopy=False, states=(optuna.trial.TrialState.WAITING,)):
            fixed = (ft.system_attrs or {}).get("fixed_params") or {}
            try:
                if SPACE.canonical(SPACE.fix(T.complete(fixed))) == key:
                    return  # already queued (a seed)
            except ValueError:
                continue
        study.enqueue_trial(kn, user_attrs={"label": "transfer from %s" % ranked[0]["uid"]})

    def new_trial(self, track):
        T = self.T[track]
        self.maybe_transfer(track)
        study = self.study(track)
        trial = study.ask()
        uid = "%s#%d" % (track, trial.number)
        label = trial.user_attrs.get("label") or "sampled"
        try:
            # FIXED knobs are not sampled; fix() also overrides them in a queued seed or transfer
            knobs = SPACE.fix(T.complete(SPACE.suggest(trial, T.D)))
        except ValueError as e:
            self.emit("trial_new", uid=uid, track=track, number=trial.number, label=label, knobs=None, snap=None,
                      overrides=None, run_id=None, run_dir=None, tree=self.tinfo)
            self.emit("trial_reject", uid=uid, reason=str(e)[:500])
            study.tell(trial.number, OBJ.FAIL_VALUE)
            return "rejected"
        key = SPACE.canonical(knobs)
        # identical effective configuration already run, running or imported in this track: reuse it
        orig = next((t for t in self.state.trials_of(track) if t.get("key") == key
                     and t["state"] in ("submitted", "done") and not t.get("lost") and not t.get("dup_of")), None)
        if orig is not None:
            self.emit("trial_new", uid=uid, track=track, number=trial.number, label=label, knobs=knobs, key=key,
                      snap=orig.get("snap"), overrides=orig.get("overrides"), run_id=orig.get("run_id"),
                      run_dir=orig.get("run_dir"), tree=self.tinfo, config_changes=orig.get("config_changes"))
            self.live[uid] = trial
            self.emit("trial_dup", uid=uid, of=orig["uid"])
            if orig["state"] == "done":
                self.finish_dup(self.state.trials[uid], orig)
            log("trial %s duplicates %s" % (uid, orig["uid"]))
            return "dup"
        try:
            run_dir, params, snap, ov = self.build_snapshot(track, knobs, TRIAL["mode"], TRIAL["threads"], track)
        except (SystemExit, Exception) as e:  # floorplan check or island model refused, bad core, ...
            self.emit("trial_new", uid=uid, track=track, number=trial.number, label=label, knobs=knobs, key=key,
                      snap=None, overrides=None, run_id=None, run_dir=None, tree=self.tinfo)
            self.emit("trial_reject", uid=uid, reason=str(e)[:500])
            study.tell(trial.number, OBJ.FAIL_VALUE)
            log("trial %s rejected: %s" % (uid, str(e)[:200]))
            return "rejected"
        self.emit("trial_new", uid=uid, track=track, number=trial.number, label=label, knobs=knobs, key=key,
                  snap=snap, overrides=ov, run_id=params["run_id"], run_dir=run_dir, tree=self.tinfo,
                  config_changes=params.get("config_changes_vs_repo"), snapshot_sha256=params.get("snapshot_sha256"),
                  mirror_mismatch=params.get("mirror_mismatch", False))
        self.live[uid] = trial
        return self.submit_trial(self.state.trials[uid], attempt=1)

    def submit_trial(self, t, attempt):
        name = "%s-%d" % (self.state.tracks[t["track"]]["name"], t["number"])
        res = dict(TRIAL, time=TRIAL_RULE_TIME) if self.is_rule(t["track"]) else TRIAL
        jid = self.submit(name, res, os.path.join(HERE, "trial_job.sh"), [t["run_dir"]])
        if jid:
            self.emit("trial_submit", uid=t["uid"], job_id=jid, attempt=attempt, cpus=TRIAL["cpus"],
                      partition=PARTITIONS)
            log("submitted %s as %s (attempt %d) %s" % (t["uid"], jid, attempt, t["run_id"]))
        return jid

    def finish_dup(self, t, orig):
        value, extra = orig.get("value"), {}
        if self.is_rule(t["track"]) and orig.get("metrics") and value is not None \
                and orig.get("value_objective") is None:
            # the original's value was told before the runtime rule (pre-v3): penalize the duplicate
            proj = self.ctx().trial(orig)["proj_s"]
            extra = {"value_objective": value, "ci_proj_s": proj}
            value = RT.penalized_value(value, proj)
        elif orig.get("value_objective") is not None:
            extra = {"value_objective": orig.get("value_objective"), "ci_proj_s": orig.get("ci_proj_s")}
        self.emit("trial_done", uid=t["uid"], job_id=orig.get("done_job"), metrics=orig.get("metrics"),
                  legal=orig.get("legal"), blockers=orig.get("blockers"), magnitude=orig.get("magnitude"),
                  value=value, dup_of=orig["uid"], lost=orig.get("lost", False), **extra)
        self.tell(t, value)

    def tell(self, t, value):
        study = self.study(t["track"])
        try:
            study.tell(t["number"], value if value is not None else OBJ.FAIL_VALUE, skip_if_finished=True)
        except Exception as e:  # already finished or unknown trial
            log("tell %s: %s" % (t["uid"], e))
        self.live.pop(t["uid"], None)

    # ------------------------------------------------------------ polling trials
    def evaluate_run(self, run_dir, job_id, mode, period):
        res = load_json(os.path.join(run_dir, "result.json"))
        if not res or str(res.get("job_id")) != str(job_id):
            return None
        post = load_json(os.path.join(run_dir, "opt_post.json"))
        if not post or str(post.get("job_id")) != str(job_id):
            import postprocess
            post = postprocess.process(run_dir, job_id)
        m = OBJ.flatten(res, post, period=period, resolved=load_json(os.path.join(run_dir, "out", "resolved.json")))
        legal, blockers, mag = OBJ.legality(m, mode=mode)
        return {"metrics": m, "legal": legal, "blockers": blockers, "magnitude": mag,
                "value": OBJ.value(m, legal, mag), "status": res.get("status")}

    def job_outcome(self, job_id, jobs, run_dir, mode, period):
        """-> ('active'|'done'|'wait'|'retry', evaluation or None)."""
        if job_id in jobs and jobs[job_id]["state"] in SL.ACTIVE:
            return "active", None
        done = load_json(os.path.join(run_dir, "opt_done.json"))
        ev = None
        if (done and str(done.get("job_id")) == str(job_id)) or \
                (load_json(os.path.join(run_dir, "status.json"), {}).get("state") == "finished"):
            ev = self.evaluate_run(run_dir, job_id, mode, period)
        if ev is not None:
            if ev["status"] == "terminated":
                return "retry", ev
            return "done", ev
        acct = SL.sacct([job_id]).get(str(job_id), {})
        ago = SL.ended_seconds_ago(acct.get("end"))
        if ago is None or ago < RESULT_GRACE_S:
            return "wait", None
        return "retry", None

    def poll_trials(self, jobs):
        for t in list(self.state.in_flight_trials()):
            if self.dry and not self.owns(t["run_dir"]):
                continue
            period = track_period(self.state.tracks.get(t["track"]) or {})
            out, ev = self.job_outcome(t["job_id"], jobs, t["run_dir"], TRIAL["mode"], period)
            if out in ("active", "wait"):
                continue
            if out == "retry" and t.get("attempt", 1) < MAX_ATTEMPTS:
                log("trial %s job %s ended without a usable result; resubmitting" % (t["uid"], t["job_id"]))
                self.submit_trial(t, attempt=t.get("attempt", 1) + 1)
                continue
            if ev is None:
                ev = {"metrics": None, "legal": False, "blockers": ["no result after %d attempts" % t.get("attempt", 1)],
                      "magnitude": 1e4, "value": OBJ.FAIL_VALUE}
                lost = True
            else:
                lost = False
            extra = {}
            if self.is_rule(t["track"]) and ev["metrics"] and ev["metrics"].get("min_ws") is not None:
                # runtime rule: the TPE value of an ineligible trial is lowered (runtime.penalized_value)
                proj = self.projection(t, ev["metrics"])
                extra = {"value_objective": ev["value"], "ci_proj_s": proj}
                ev["value"] = RT.penalized_value(ev["value"], proj)
            self.emit("trial_done", uid=t["uid"], job_id=t["job_id"], metrics=ev["metrics"], legal=ev["legal"],
                      blockers=ev["blockers"], magnitude=ev["magnitude"], value=ev["value"], lost=lost, **extra)
            self.tell(t, ev["value"])
            for d in self.state.trials.values():
                if d["state"] == "dup" and d.get("dup_of") == t["uid"]:
                    self.finish_dup(d, self.state.trials[t["uid"]])
            m = ev["metrics"] or {}
            log("done %s job %s: legal %s min WS %s (%s) value %.3f%s %s" % (
                t["uid"], t["job_id"], ev["legal"], m.get("min_ws"), m.get("min_ws_corner"), ev["value"],
                " projected CI %.2f h" % (extra["ci_proj_s"] / 3600.0) if extra.get("ci_proj_s") else "",
                "; ".join(ev["blockers"][:3])))
            self.after_run(t["run_dir"], t["job_id"], keep_snap=False)
            self.dirty = True

    def after_run(self, run_dir, job_id, keep_snap):
        if not self.owns(run_dir):
            return
        if not keep_snap:
            shutil.rmtree(os.path.join(run_dir, "snap"), ignore_errors=True)
        for lp in glob.glob(os.path.join(PATHS["logs"], "*-%s.out" % job_id)):
            try:
                with open(lp, "rb") as fi, gzip.open(lp + ".gz", "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                os.remove(lp)
            except (IOError, OSError):
                pass

    # ------------------------------------------------------------ promotions
    def promo_dir(self, pid):
        d = os.path.join(PATHS["promos"], pid)
        os.makedirs(d, exist_ok=True)
        return d

    def promote(self, t, reason):
        if self.free < PFULL["cpus"]:
            return None
        track = t["track"]
        pid = "p%03d-%s" % (len(self.state.promos) + 1, self.state.tracks[track]["name"])
        try:
            run_dir, params, _, _ = self.build_snapshot(track, clean_knobs(t["knobs"]), PFULL["mode"],
                                                        PFULL["threads"], track + "-full")
        except (SystemExit, Exception) as e:
            log("promotion of %s failed to build: %s" % (t["uid"], e))
            self.promo_skip.add(t["uid"])
            self.emit("note", text="promotion of %s not built: %s" % (t["uid"], str(e)[:300]))
            return None
        m = t.get("metrics") or {}
        warn = ("clock depth: SRAM A_CLK up to %s cells, flip-flop CLK from %s (excess %s)"
                % (m.get("clk_sram_max"), m.get("clk_ff_min"), m.get("clk_sram_excess"))) if m.get("clk_warn") else None
        self.emit("promo_new", pid=pid, uid=t["uid"], track=track, reason=reason, run_id=params["run_id"],
                  run_dir=run_dir, config_changes=params.get("config_changes_vs_repo"), warning=warn)
        self.submit_promo_full(self.state.promos[pid], attempt=1)
        log("promotion %s of %s (%s)%s" % (pid, t["uid"], reason, "; WARNING " + warn if warn else ""))
        return pid

    def submit_promo_full(self, p, attempt):
        jid = self.submit("pfull-%s" % p["pid"], PFULL, os.path.join(HERE, "trial_job.sh"), [p["run_dir"]])
        if jid:
            self.emit("promo_submit", pid=p["pid"], stage="full", job_id=jid, attempt=attempt)
        return jid

    def poll_promos(self, jobs):
        for p in list(self.state.promos.values()):
            if self.dry and not self.owns(p["run_dir"]):
                continue
            st = p["stages"]
            full = st.get("full")
            if full and full.get("state") == "submitted":
                period = track_period(self.state.tracks.get(p["track"]) or {})
                out, ev = self.job_outcome(full["job_id"], jobs, p["run_dir"], "full", period)
                if out in ("active", "wait"):
                    continue
                if out == "retry" and full.get("attempt", 1) < MAX_ATTEMPTS:
                    self.submit_promo_full(p, full.get("attempt", 1) + 1)
                    continue
                res = ev or {"metrics": None, "legal": False, "blockers": ["no result"], "value": OBJ.FAIL_VALUE}
                self.emit("promo_done", pid=p["pid"], stage="full",
                          result={k: res.get(k) for k in ("metrics", "legal", "blockers", "value")})
                log("promotion %s full run: legal %s %s" % (p["pid"], res.get("legal"), res.get("blockers")))
                info = os.path.join(p["run_dir"], "snap", "info.yaml")
                if os.path.exists(info):
                    shutil.copyfile(info, os.path.join(self.promo_dir(p["pid"]), "info.yaml"))
                self.after_run(p["run_dir"], full["job_id"], keep_snap=False)
                self.dirty = True
            for stage in GATES.SIGNOFF:
                s = st.get(stage)
                if s and s.get("state") == "submitted":
                    if s["job_id"] in jobs and jobs[s["job_id"]]["state"] in SL.ACTIVE:
                        continue
                    res = self.read_signoff(p, stage, s["job_id"])
                    if res is not None and stage == "eq" and GATES.eq_should_retry(res, s.get("attempt", 1),
                                                                                     MAX_ATTEMPTS):
                        # a time limit can come from a loaded node (ABC on p018's netlist: 446 s
                        # alone, 692 s next to its self-test); undecided, "not equivalent" and
                        # errors are final
                        log("promotion %s eq: time limit in job %s (%s); resubmitting"
                            % (p["pid"], s["job_id"], res.get("reason")))
                        try:
                            self.submit_signoff(p, stage, s.get("attempt", 1) + 1)
                            continue
                        except (IOError, OSError) as e:
                            res = {"pass": False, "error": str(e)[:300]}
                    if res is None:
                        acct = SL.sacct([s["job_id"]]).get(str(s["job_id"]), {})
                        ago = SL.ended_seconds_ago(acct.get("end"))
                        if ago is None or ago < RESULT_GRACE_S:
                            continue
                        if s.get("attempt", 1) < MAX_ATTEMPTS:
                            try:
                                self.submit_signoff(p, stage, s.get("attempt", 1) + 1)
                                continue
                            except (IOError, OSError) as e:
                                res = {"pass": False, "error": str(e)[:300]}
                        else:
                            res = {"pass": False, "error": "no result from job %s" % s["job_id"]}
                    self.emit("promo_done", pid=p["pid"], stage=stage, result=res)
                    log("promotion %s %s: pass %s%s" % (p["pid"], stage, res.get("pass"),
                                                        " (%s)" % res.get("reason") if stage == "eq" else ""))
                    if stage == "precheck":
                        for g in glob.glob(os.path.join(self.promo_dir(p["pid"]), "sub", "*.gds")):
                            os.remove(g)  # the gzip copy stays in the run's out/final
                    self.dirty = True

    def prepare_sub(self, p, need=("gds", "lef", "v")):
        """Submission-like directory for the precheck, the GL test and the equivalence check:
        info.yaml, GDS, LEF and the unpowered final netlist (what the gds action puts in
        tt_submission/). need: the files a stage uses (the equivalence check: the netlist)."""
        d = self.promo_dir(p["pid"])
        sub = os.path.join(d, "sub")
        os.makedirs(sub, exist_ok=True)
        if "gds" in need and not os.path.exists(os.path.join(sub, "info.yaml")):
            shutil.copyfile(os.path.join(d, "info.yaml"), os.path.join(sub, "info.yaml"))
        for src, ext in (("gds/%s.gds.gz", "gds"), ("lef/%s.lef.gz", "lef"), ("nl/%s.nl.v.gz", "v")):
            if ext not in need:
                continue
            dst = os.path.join(sub, "%s.%s" % (TOP, ext))
            if os.path.exists(dst):
                continue
            s = os.path.join(p["run_dir"], "out", "final", src % TOP)
            with gzip.open(s, "rb") as fi, open(dst + ".tmp", "wb") as fo:
                shutil.copyfileobj(fi, fo)
            os.replace(dst + ".tmp", dst)
        return sub

    def pending_signoffs(self):
        out = []
        for p in self.state.promos.values():
            full = p["stages"].get("full") or {}
            if full.get("state") == "done" and (full.get("result") or {}).get("legal"):
                # promotions from before the equivalence stage existed get it too (backfill)
                for stage in GATES.SIGNOFF:
                    if stage not in p["stages"]:
                        out.append((p, stage))
        return out

    def eq_inputs(self, p):
        """What the promotion's full run was built from, for the equivalence check:
        {core, core_sha, tree, variant, pdk_root, pdk_source}. The core copy and the frozen
        tree come from its params.json (make_snapshot records both), else the track record and
        the driver's tree; the variant is the track's (as for the gate-level tests); the PDK
        root is the one LibreLane used (out/resolved.json PDK_ROOT), else the one
        make_snapshot recorded (params.json flow.pdk_root), else the sweep harness's default
        ($PE_PDK_ROOT, else $PE_WORK/sram-flow/pdk, as scripts/sweep/run_one.sh)."""
        params = load_json(os.path.join(p["run_dir"], "params.json"), {}) or {}
        tr = self.state.tracks.get(p["track"]) or {}
        core = params.get("core_path") or params.get("core") or tr.get("core")
        sha = params.get("core_sha256") or tr.get("core_sha256")
        tree = (params.get("repo") or {}).get("path") or TREE
        if not core or not os.path.exists(core):
            raise IOError("core %s of %s not found" % (core, p["pid"]))
        if not os.path.exists(os.path.join(tree, "src", "project.v")):
            tree = TREE
        variant = tr.get("gl_variant") or ("base" if tr.get("primary") else tr.get("name")) or "base"
        resolved = load_json(os.path.join(p["run_dir"], "out", "resolved.json"), {}) or {}
        flow = params.get("flow") if isinstance(params.get("flow"), dict) else {}
        if resolved.get("PDK_ROOT"):
            pdk, how = resolved["PDK_ROOT"], "out/resolved.json PDK_ROOT"
        elif flow.get("pdk_root"):
            pdk, how = flow["pdk_root"], "params.json flow.pdk_root"
        else:
            pdk = os.environ.get("PE_PDK_ROOT") or os.path.join(WORK, "sram-flow", "pdk")
            how = "sweep default"
        return {"core": core, "core_sha": sha, "tree": tree, "variant": variant, "pdk_root": pdk,
                "pdk_source": how}

    def submit_signoff(self, p, stage, attempt):
        d = self.promo_dir(p["pid"])
        res = {"precheck": PCHECK, "gl": PGL, "eq": PEQ}[stage]
        if self.free < res["cpus"]:
            return None
        if stage == "eq":
            ei = self.eq_inputs(p)
        sub = self.prepare_sub(p, need=("v",) if stage == "eq" else ("gds", "lef", "v"))
        out = os.path.join(d, stage + (".a%d" % attempt if attempt > 1 else ""))
        os.makedirs(out, exist_ok=True)
        if stage == "precheck":
            jid = self.submit("pchk-%s" % p["pid"], PCHECK, os.path.join(HERE, "precheck_job.sh"), [sub, out],
                              signal=False)
        elif stage == "eq":
            jid = self.submit("peq-%s" % p["pid"], PEQ, os.path.join(HERE, "eq_job.sh"),
                              [os.path.join(sub, TOP + ".v"), ei["core"], ei["core_sha"] or "-", ei["tree"], out,
                               str(EQ_LIMIT_S), ei["variant"], ei["pdk_root"], ei["pdk_source"]],
                              signal=False)
        else:
            tr = self.state.tracks[p["track"]]
            variant = tr.get("gl_variant") or ("base" if tr.get("primary") else tr["name"])
            jid = self.submit("pgl-%s" % p["pid"], PGL, os.path.join(HERE, "gl_job.sh"),
                              [os.path.join(sub, TOP + ".v"), variant, TREE, out], signal=False)
        if jid:
            self.emit("promo_submit", pid=p["pid"], stage=stage, job_id=jid, attempt=attempt, out=out)
        return jid

    def read_signoff(self, p, stage, job_id):
        subs = p["stages"][stage]["submits"]
        out = subs[-1].get("out")
        r = load_json(os.path.join(out, "result.json"))
        if not r or str(r.get("job_id")) != str(job_id):
            return None
        return r

    def promoted_uids(self):
        return {p["uid"] for p in self.state.promos.values()}

    def track_promos(self, track):
        """Promotions that stand for trials of this track (directly, or of an imported trial's source)."""
        uids = set()
        for t in self.state.trials_of(track):
            if t["state"] == "done" and not t.get("dup_of"):
                uids |= self.chain(t)
        return [p for p in self.state.promos.values() if p["track"] == track or p["uid"] in uids]

    def maybe_promote(self, budget_left):
        # the equivalence stage (2 CPUs, at most 90 min) does not count: its backfill on the
        # promotions made before it existed must not hold up new promotions
        inflight = [p for p in self.state.promos.values()
                    if any(s.get("state") == "submitted" for k, s in p["stages"].items() if k != "eq")]
        if len(inflight) >= MAX_PROMOS_IN_FLIGHT or budget_left <= 0:
            return
        real = self.promoted_uids()
        promoted = real | self.promo_skip
        for track in self.active_tracks:
            T = self.T[track]
            tr = self.state.tracks[track]
            if tr.get("retired"):
                continue
            # only trials inside the current search space are promoted (space.FIXED), and only
            # their promotions set the bar a new best must clear
            ranked = self.ranked(track, in_space=True)
            rule = self.is_rule(track)
            if rule:
                # runtime rule: only trials whose projected official gds job fits the bound
                ctx = self.ctx()
                ranked = [t for t in ranked if ctx.trial(t)["eligible"]]
            done = [t for t in self.state.trials_of(track) if t["state"] == "done" and not t.get("dup_of")]
            # control: the committed build of the design of record and of the 6x4 fallback goes
            # through the pipeline once (unless an identical configuration already has)
            if T.kind in ("dor", "6x4"):
                bkey = SPACE.canonical(T.complete({}))
                base = [t for t in done if t.get("key") == bkey and t.get("metrics")]
                if base and not any(self.chain(t) & promoted for t in base):
                    self.promote(base[0], "control: committed configuration through the promotion pipeline")
                    return
            if not ranked or len(done) < PROMO_MIN_TRIALS.get(T.kind, 20):
                continue
            best = ranked[0]
            prom = [t for t in done if self.chain(t) & real and t.get("metrics")
                    and t["metrics"].get("min_ws") is not None and not SPACE.outside(t.get("knobs"))
                    and (not rule or self.promo_runtime_ok(t))]
            prom_ws = [t["metrics"]["min_ws"] for t in prom]
            if not (self.chain(best) & promoted) and \
                    (not prom_ws or best["metrics"]["min_ws"] > max(prom_ws) + PROMO_MIN_GAIN):
                self.promote(best, "best legal trial of the track")
                return
            if T.kind in ("dor", "freq"):
                n_new = sum(1 for t in self.state.trials_of(track) if not t.get("imported"))
                if n_new >= PERIODIC_EVERY * (len(self.track_promos(track)) + 1):
                    for t in ranked[1:3]:
                        if not (self.chain(t) & promoted):
                            self.promote(t, "periodic: top-3 legal trial not yet promoted")
                            return

    # ------------------------------------------------------------ allocation and retirement
    def weight(self, track, cands):
        T = self.T[track]
        if T.kind == "variant":
            nv = sum(1 for c in cands if self.T[c].kind == "variant")
            return WEIGHTS["variant"] / max(nv, 1)
        return WEIGHTS[T.weight]

    def candidates(self, counts_all, exclude=()):
        out = []
        for tid in self.active_tracks:
            if self.state.tracks[tid].get("retired") or tid in exclude:
                continue
            if self.T[tid].kind == "variant" and counts_all.get(tid, 0) >= VARIANT_TRIAL_CAP:
                continue
            out.append(tid)
        return out

    def weights_epoch(self):
        """Time of the first driver start whose settings had the current WEIGHTS (None if none)."""
        for d in self.state.drivers:
            if ((d.get("settings") or {}).get("weights") or {}) == WEIGHTS:
                return d.get("t")
        return None

    def pick_track(self, counts_new, counts_all, exclude=()):
        """counts_new: trials submitted to LibreLane per track since weights_epoch() (imported,
        duplicate and rejected trials use no CPUs and do not count). A track without any goes
        first (in weight order); then the track with the smallest (count + 1) / weight."""
        cands = self.candidates(counts_all, exclude)
        if not cands:
            return None
        zero = [c for c in cands if counts_new.get(c, 0) == 0]
        if zero:
            return max(zero, key=lambda c: (self.weight(c, cands), -self.active_tracks.index(c)))
        return min(cands, key=lambda c: ((counts_new.get(c, 0) + 1) / self.weight(c, cands), random.random()))

    def retire_check(self):
        if not self.primary:
            return
        best_d = self.ranked(self.primary)
        if not best_d:
            return
        bd = best_d[0]["metrics"]
        for tid in self.active_tracks:
            T = self.T[tid]
            tr = self.state.tracks[tid]
            if T.kind != "variant" or tr.get("retired"):
                continue
            done = [t for t in self.state.trials_of(tid) if t["state"] == "done" and not t.get("dup_of")]
            if len(done) < RETIRE_MIN_TRIALS:
                continue
            rk = self.ranked(tid)
            why = None
            if not rk:
                why = "no legal trial in %d finished trials" % len(done)
            else:
                bv = rk[0]["metrics"]
                if bd["min_ws"] - bv["min_ws"] >= RETIRE_WS_MARGIN and \
                        (bv.get("utilization") or 1) >= (bd.get("utilization") or 0) - RETIRE_UTIL_MARGIN:
                    why = ("dominated after %d finished trials: best min WS %.3f ns (%s) vs %.3f ns (%s) for the "
                           "design of record, utilization %.4f vs %.4f"
                           % (len(done), bv["min_ws"], rk[0]["uid"], bd["min_ws"], best_d[0]["uid"],
                              bv.get("utilization") or 0, bd.get("utilization") or 0))
            if why:
                self.emit("track_retire", track=tid, reason=why)
                log("retired %s: %s" % (tid, why))
                self.dirty = True

    def stop_reason(self):
        """-> (reason, hard). hard stops (STOP file, date) also end promotions and cancel
        trial jobs that have not started; the budget stop only ends new trials."""
        if os.path.exists(PATHS["stop"]):
            return "STOP file", True
        if datetime.datetime.now() >= STOP_AT:
            return "stop date %s reached" % STOP_AT.isoformat(), True
        if self.state.librelane_runs() >= BUDGET_RUNS - PROMO_RESERVE:
            return "budget: %d LibreLane runs submitted" % self.state.librelane_runs(), False
        return None, False

    def fill(self, stop, hard):
        """Use the free CPUs: sign-off jobs first, then promotions, then new trials."""
        for p in self.state.promos.values():
            if "full" not in p["stages"] and not hard:  # promotion whose first sbatch failed
                if not self.submit_promo_full(p, attempt=1):
                    return 0
        for p, stage in self.pending_signoffs():
            try:
                jid = self.submit_signoff(p, stage, 1)
            except (IOError, OSError) as e:
                self.emit("promo_done", pid=p["pid"], stage=stage, result={"pass": False, "error": str(e)[:300]})
                continue
            if not jid:
                return 0  # keep the next free CPUs for it
        if not hard:
            self.maybe_promote(BUDGET_RUNS - self.state.librelane_runs())
        if stop:
            return 0
        counts_new, counts_all = {}, {}
        epoch = self.weights_epoch() or ""
        for t in self.state.trials.values():
            counts_all[t["track"]] = counts_all.get(t["track"], 0) + 1
            if t.get("submits") and (t.get("created") or "") >= epoch:
                counts_new[t["track"]] = counts_new.get(t["track"], 0) + 1
        n = 0
        # trials created but never submitted (sbatch failed, or the driver stopped in between)
        for t in [t for t in self.state.trials.values() if t["state"] == "new" and t.get("run_dir")]:
            if not self.submit_trial(t, attempt=1):
                return n
            n += 1
        idle, idle_by = 0, {}
        while (self.free >= TRIAL["cpus"] and n < MAX_SUBMIT_PER_LOOP and idle < MAX_NOSUBMIT_PER_LOOP
               and self.n_user < USER_JOB_CAP and self.state.librelane_runs() < BUDGET_RUNS - PROMO_RESERVE
               and time.time() >= self.backoff_until):
            track = self.pick_track(counts_new, counts_all,
                                    exclude={k for k, v in idle_by.items() if v >= MAX_NOSUBMIT_PER_TRACK})
            if track is None:
                break
            out = self.new_trial(track)
            counts_all[track] = counts_all.get(track, 0) + 1
            if out in ("dup", "rejected"):
                idle += 1
                idle_by[track] = idle_by.get(track, 0) + 1
                continue
            if not out:
                break
            counts_new[track] = counts_new.get(track, 0) + 1
            n += 1
        return n

    def drain(self, jobs):
        """On a stop condition: cancel trial jobs that have not started; running ones finish."""
        pend = [t["job_id"] for t in self.state.in_flight_trials()
                if t["job_id"] in jobs and jobs[t["job_id"]]["state"] == "PENDING"]
        if pend and not self.dry:
            SL.scancel(pend)
            log("stop: cancelled %d pending trial jobs %s" % (len(pend), " ".join(pend)))
            for t in self.state.in_flight_trials():
                if t["job_id"] in pend:
                    self.emit("trial_done", uid=t["uid"], job_id=t["job_id"], metrics=None, legal=False,
                              blockers=["cancelled before start (stop)"], magnitude=1e4, value=OBJ.FAIL_VALUE,
                              lost=True)
                    self.tell(t, OBJ.FAIL_VALUE)

    # ------------------------------------------------------------ pruning
    def prune_finals(self):
        keep = set()
        for track in self.state.tracks:
            for t in self.ranked(track)[:KEEP_FINAL_TOP]:
                keep.add(t.get("run_dir"))
            for t in self.state.trials_of(track):
                if (t.get("label") or "").startswith("baseline"):
                    keep.add(t.get("run_dir"))
        for p in self.state.promos.values():
            keep.add(p["run_dir"])
            keep.add(self.state.trials.get(p["uid"], {}).get("run_dir"))
        for t in self.state.trials.values():
            rd = t.get("run_dir")
            if t["state"] != "done" or not rd or rd in keep or rd in self.pruned or t.get("imported") \
                    or not self.owns(rd):
                continue
            shutil.rmtree(os.path.join(rd, "out", "final"), ignore_errors=True)
            self.pruned.add(rd)

    # ------------------------------------------------------------ reconcile after a restart
    def reconcile(self):
        import optuna
        for track in self.state.tracks:
            if not os.path.exists(os.path.join(PATHS["studies"], track + ".journal")):
                continue
            study = self.study(track)
            known = {t["number"]: t for t in self.state.trials_of(track)}
            for ft in study.get_trials(deepcopy=False, states=(optuna.trial.TrialState.RUNNING,)):
                t = known.get(ft.number)
                if t is None:
                    study.tell(ft.number, OBJ.FAIL_VALUE, skip_if_finished=True)
                    log("reconcile: study %s trial %d had no store record; told FAIL_VALUE" % (track, ft.number))
                elif t["state"] in ("done", "rejected"):
                    study.tell(ft.number, t.get("value") if t.get("value") is not None else OBJ.FAIL_VALUE,
                               skip_if_finished=True)

    # ------------------------------------------------------------ main loop
    def other_driver_running(self, jobs):
        return [j for j, v in jobs.items() if v["name"] == JOB_PREFIX + "driver" and v["state"] == "RUNNING"
                and j != self.job_id]

    def resubmit_self(self, jobs):
        pend = [j for j, v in jobs.items() if v["name"] == JOB_PREFIX + "driver" and v["state"] == "PENDING"]
        if pend:
            log("successor driver already queued: %s" % " ".join(pend))
            return True
        script = os.path.join(OPT, "current", "tools", "opt", "driver_job.sh")
        if not os.path.exists(script):
            script = os.path.join(HERE, "driver_job.sh")
        opts = ["-J", JOB_PREFIX + "driver", "-p", "mit_preemptable", "--requeue", "-N", "1", "-n", "1",
                "-c", str(DRIVER["cpus"]), "--mem", DRIVER["mem"], "-t", DRIVER["time"], "--open-mode=append",
                "-o", os.path.join(PATHS["logs"], "%x-%j.out"),
                "--export=ALL,PE_WORK=%s,PE_OPT_ROOT=%s" % (WORK, OPT)]
        if self.job_id:
            opts += ["--dependency=afterany:%s" % self.job_id]
        jid, err = SL.sbatch(opts, [script], dry_run=self.dry)
        log("successor driver: %s %s" % (jid, err or ""))
        if jid:
            self.emit("note", text="successor driver %s submitted" % jid, job_id=jid)
        return bool(jid)

    def write_lease(self):
        write_json_atomic(PATHS["lease"], {"job_id": self.job_id, "host": socket.gethostname(), "pid": os.getpid(),
                                           "heartbeat": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                           "tree": self.tinfo})

    def report(self, jobs=None, stop=None):
        REPORT.write_all(self.state, OPT, jobs=jobs, stop=stop, tree=self.tinfo, settings=settings(),
                         tracks=self.T, active=self.active_tracks)
        self.dirty = False

    def run(self, once=False):
        for i in range(30):
            try:
                jobs = self.jobs()
                break
            except RuntimeError:
                time.sleep(30)
        else:
            raise SystemExit("squeue unavailable")
        others = [] if self.dry else self.other_driver_running(jobs)
        if others:
            log("another driver is running (%s); exiting" % " ".join(others))
            return 0
        self.emit("driver_start", job_id=self.job_id, host=socket.gethostname(), tree=self.tinfo,
                  dry_run=self.dry, settings=settings())
        log("driver start: job %s tree %s tools %s" % (self.job_id, self.tinfo.get("commit"),
                                                       self.tinfo.get("tools_sha8")))
        log("equivalence checker %s (%s); OSS_CAD_SUITE %s" % (
            EQ_CHECK, "present" if os.path.isfile(EQ_CHECK) else "MISSING: every equivalence stage will fail",
            os.environ.get("OSS_CAD_SUITE") or "unset (yosys from PATH)"))
        self.backfill_runtime()
        self.discover_tracks()
        self.last_scan = time.time()
        self.reconcile()
        drained = False
        while True:
            try:
                self.write_lease()
                jobs = self.jobs()
                # a dry run submits nothing, so it plans for the whole cap
                self.free = CPU_CAP - (0 if self.dry else self.my_cpus(jobs))
                self.n_user = 0 if self.dry else len(jobs)
                if time.time() - self.last_scan > DISCOVER_S:
                    self.discover_tracks()
                    self.last_scan = time.time()
                self.poll_trials(jobs)
                self.poll_promos(jobs)
                self.import_trials()
                self.retire_check()
                stop, hard = self.stop_reason()
                if hard and not drained:
                    self.drain(jobs)
                    drained = True
                if self.fill(stop, hard):
                    self.dirty = True
                if self.dirty:
                    self.prune_finals()
                    self.report(self.jobs(), stop)
                busy = self.state.in_flight_trials() or any(
                    s.get("state") == "submitted" for p in self.state.promos.values() for s in p["stages"].values()) \
                    or (self.pending_signoffs() and not hard)
                if stop and not busy:
                    self.report(self.jobs(), stop)
                    write_json_atomic(PATHS["finished"], {"reason": stop, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                                          "librelane_runs": self.state.librelane_runs()})
                    self.emit("note", text="campaign finished: %s" % stop)
                    log("campaign finished: %s" % stop)
                    return 0
                if once:
                    self.report(self.jobs(), stop)
                    return 0
                left = SL.parse_time_left(jobs.get(self.job_id, {}).get("time_left")) if self.job_id else None
                if left is not None and left < DRIVER_RESUBMIT_S:
                    if self.resubmit_self(jobs):
                        log("time limit near (%d s left); exiting for the successor" % left)
                        return 0
            except Exception:
                log("loop error:\n" + traceback.format_exc())
                if once:
                    raise
            time.sleep(LOOP_S)


def settings():
    return {"cpu_cap": CPU_CAP, "user_job_cap": USER_JOB_CAP, "budget_runs": BUDGET_RUNS,
            "promo_reserve": PROMO_RESERVE, "stop_at": STOP_AT.isoformat(), "weights": WEIGHTS,
            "variant_trial_cap": VARIANT_TRIAL_CAP,
            "retire": {"min_trials": RETIRE_MIN_TRIALS, "ws_margin_ns": RETIRE_WS_MARGIN,
                       "util_margin": RETIRE_UTIL_MARGIN},
            "promotion": {"max_in_flight": MAX_PROMOS_IN_FLIGHT, "min_trials": PROMO_MIN_TRIALS,
                          "periodic_every": PERIODIC_EVERY, "min_gain_ns": PROMO_MIN_GAIN},
            "trial": TRIAL, "promotion_full": PFULL, "precheck": PCHECK, "gl": PGL,
            "eq": dict(PEQ, limit_s=EQ_LIMIT_S, checker=EQ_CHECK, oss_cad_suite=os.environ.get("OSS_CAD_SUITE")),
            "fixed_knobs": SPACE.FIXED, "partitions": PARTITIONS,
            "job_prefix": JOB_PREFIX,
            "runtime": {"limit_s": RT.LIMIT_S, "bound_s": RT.BOUND_S, "overhead_s": RT.OVERHEAD_S, "s_ci": RT.S_CI,
                        "c_full": RT.C_FULL, "k_rsz": RT.K_RSZ, "k_rest": RT.K_REST, "rule_kinds": RT.RULE_KINDS,
                        "penalty_ns": RT.PENALTY_NS, "trial_rule_time": TRIAL_RULE_TIME,
                        "seed_tns": RUNTIME_SEED_TNS, "seed_top": RUNTIME_SEED_TOP}}


def status():
    st = State(Store(PATHS["store"]).events())
    jobs = SL.my_jobs() or {}
    mine = {k: v for k, v in jobs.items() if v["name"].startswith(JOB_PREFIX)}
    by = {}
    for t in st.trials.values():
        by.setdefault(t["track"], {}).setdefault(t["state"] + ("/imported" if t.get("imported") else ""), 0)
        by[t["track"]][t["state"] + ("/imported" if t.get("imported") else "")] += 1
    print("store: %s" % PATHS["store"])
    print("LibreLane runs submitted: %d / %d" % (st.librelane_runs(), BUDGET_RUNS))
    last = st.drivers[-1] if st.drivers else {}
    print("last driver start: job %s tree %s" % (last.get("job_id"), (last.get("tree") or {}).get("commit")))
    for tr, c in sorted(by.items(), key=lambda kv: (st.tracks.get(kv[0], {}).get("kind") is None, kv[0])):
        info = st.tracks.get(tr, {})
        print("  %-40s %-8s %s%s" % (tr, info.get("kind") or "legacy", " ".join("%s=%d" % kv for kv in sorted(c.items())),
                                     ("  RETIRED: " + info["retired"][:80]) if info.get("retired") else ""))
    print("promotions: %d" % len(st.promos))
    for p in st.promos.values():
        print("  %s %s %s" % (p["pid"], p["uid"], {k: (v.get("state"), v.get("job_id")) for k, v in p["stages"].items()}))
    print("jobs: %d (%d CPUs): %s" % (len(mine), sum(v["cpus"] for v in mine.values()),
                                       " ".join(sorted(set(v["state"] for v in mine.values())))))
    lease = load_json(PATHS["lease"])
    print("lease: %s" % lease)
    for f in ("stop", "finished"):
        if os.path.exists(PATHS[f]):
            print("%s present: %s" % (f.upper(), PATHS[f]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("run", "once", "status", "report"))
    ap.add_argument("--dry-run", action="store_true", help="plan and build snapshots, submit nothing")
    a = ap.parse_args()
    if a.cmd == "status":
        status()
        return 0
    if a.cmd == "report":
        st = State(Store(PATHS["store"]).events())
        tracks = {t.id: t for t in TR.discover(TREE, WORK, log)}
        REPORT.write_all(st, OPT, jobs=SL.my_jobs(), stop=None, tree=tree_info(), settings=settings(),
                         tracks=tracks, active=list(tracks))
        print(os.path.join(OPT, "leaderboard.md"))
        return 0
    d = Driver(dry_run=a.dry_run)
    return d.run(once=(a.cmd == "once"))


if __name__ == "__main__":
    sys.exit(main())
