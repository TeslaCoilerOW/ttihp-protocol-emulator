#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Autonomous physical-design optimizer for the 8x4 design of record and the
published variant cores (docs/optimization.md).

  driver.py run        the main loop (run as a Slurm job by driver_job.sh)
  driver.py status     one-screen summary of the campaign
  driver.py report     rewrite leaderboard.md / leaderboard.csv / manifest.json

The driver keeps up to CPU_CAP CPUs of LibreLane runs in flight through the
sweep harness (scripts/sweep/make_snapshot.py builds each run snapshot,
scripts/sweep/run_one.sh runs it, wrapped by tools/opt/trial_job.sh), records
every trial in the append-only store (store.py), asks optuna TPE (one study
per track, journal file storage) for the next configuration, promotes the best
configurations to a full-mode run with OPENROAD_THREADS 4, the TT precheck and
the gate-level cocotb subset, and rewrites the leaderboard after every
completed trial. It resumes from the store after preemption, and resubmits
itself before its time limit until the budget is spent, the stop date is
reached or <opt root>/STOP exists.

Environment: PE_WORK (cluster work directory: sram-flow/, variants/,
drc-triage/, cocotb/, host/), optional PE_OPT_ROOT (default $PE_WORK/optimizer).
The driver runs from a frozen export of the repository (launch.sh); the
scripts/sweep and src/ it uses are that export's.
"""

import argparse
import datetime
import glob
import gzip
import hashlib
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

import objective as OBJ  # noqa: E402
import report as REPORT  # noqa: E402
import slurm as SL  # noqa: E402
import space as SPACE  # noqa: E402
from store import State, Store  # noqa: E402

# ---------------------------------------------------------------- campaign settings
LABEL = "optimizer"
JOB_PREFIX = "pe-v2-%s-" % LABEL
PARTITIONS = "mit_preemptable,mit_normal"
CPU_CAP = 640                    # all pe-v2-optimizer-* jobs, queued + running, driver included
USER_JOB_CAP = 400               # stay below the 448 submitted-job limit per user (all workstreams)
MAX_SUBMIT_PER_LOOP = 20
BUDGET_RUNS = 1500               # LibreLane runs (trials + promoted full runs)
PROMO_RESERVE = 12               # runs kept back from trials for the final promotions
STOP_AT = datetime.datetime(2026, 10, 10, 0, 0, 0)   # local time of the cluster
PRIMARY_SHARE = 0.6
VARIANT_TRIAL_CAP = 200          # trials per variant track
MAX_PROMOS_IN_FLIGHT = 3
VARIANT_PROMO_MIN_TRIALS = 20
PRIMARY_PROMO_EVERY = 50         # also promote the best unpromoted top-3 every N primary trials
PROMO_MIN_GAIN = 0.05           # ns of min WS over the best promoted trial of the track
KEEP_FINAL_TOP = 10              # keep out/final of the top-N legal trials per track
RESULT_GRACE_S = 600             # a finished job's result may appear late on the pool
MAX_ATTEMPTS = 3
LOOP_S = 60
DRIVER_RESUBMIT_S = 1800

TRIAL = dict(cpus=32, mem="64G", time="06:00:00", threads=32, mode="fast")
PFULL = dict(cpus=8, mem="32G", time="11:00:00", threads=4, mode="full")
PCHECK = dict(cpus=16, mem="48G", time="02:00:00")
PGL = dict(cpus=4, mem="16G", time="04:00:00")
DRIVER = dict(cpus=2, mem="8G", time="2-00:00:00")

TOP = "tt_um_teslacoilerow_protocol_emulator"
PATHS = {
    "store": os.path.join(OPT, "store", "events.jsonl"),
    "runs": os.path.join(OPT, "runs"),
    "logs": os.path.join(OPT, "logs"),
    "studies": os.path.join(OPT, "studies"),
    "cores": os.path.join(OPT, "cores"),
    "promos": os.path.join(OPT, "promotions"),
    "stop": os.path.join(OPT, "STOP"),
    "finished": os.path.join(OPT, "FINISHED"),
    "lease": os.path.join(OPT, "driver.lease"),
    "log": os.path.join(OPT, "driver.log"),
    "variants_manifest": os.path.join(WORK, "variants", "cores", "MANIFEST.sha256"),
}


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def body_sha(path):
    """sha256 of a generated core without its leading '//' header lines (the published
    variants/cores/base.v differs from src/ only in the generator comment)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        lines = f.read().split(b"\n")
    i = 0
    while i < len(lines) and lines[i].startswith(b"//"):
        i += 1
    h.update(b"\n".join(lines[i:]))
    return h.hexdigest()


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
        self.active_tracks, self.primary = [], None
        if dry_run and os.path.abspath(OPT) == os.path.abspath(os.path.join(WORK, "optimizer")):
            raise SystemExit("--dry-run needs a scratch PE_OPT_ROOT (it records fake job ids)")
        self.import_sweep()

    def import_sweep(self):
        import make_snapshot as MS  # noqa: E402 (from the frozen tree)
        self.MS = MS

    def emit(self, ev, **kw):
        rec = self.store.append(ev, **kw)
        self.state.apply(rec)
        return rec

    # ------------------------------------------------------------ tracks
    def config_sha(self):
        return sha256_file(os.path.join(TREE, "src", "config.json"))

    def ensure_core_copy(self, src, name, sha):
        dst = os.path.join(PATHS["cores"], "%s-%s.v" % (name, sha[:12]))
        if not os.path.exists(dst):
            tmp = dst + ".tmp.%d" % os.getpid()
            shutil.copyfile(src, tmp)
            if sha256_file(tmp) != sha:
                os.remove(tmp)
                raise RuntimeError("core %s changed while copying" % src)
            os.replace(tmp, dst)
        return dst

    def discover_tracks(self):
        """Primary track (src/ of the frozen tree) + one per published variant core."""
        cfg = self.config_sha()
        core = os.path.join(TREE, "src", "protocol_emulator_core.v")
        csha = sha256_file(core)
        primary = "dor-%s-c%s" % (csha[:6], cfg[:6])
        active = [primary]
        if primary not in self.state.tracks:
            path = self.ensure_core_copy(core, "dor", csha)
            self.emit("track_new", track=primary, name="dor", core=path, core_sha256=csha, config_sha256=cfg,
                      primary=True, body_sha256=body_sha(core), tree=self.tinfo)
            self.seed_track(primary, SPACE.FIRST_WAVE)
            log("new primary track %s" % primary)
        pbody = body_sha(core)
        man = PATHS["variants_manifest"]
        if os.path.exists(man):
            with open(man) as f:
                lines = [l.split() for l in f if l.strip()]
            for parts in lines:
                if len(parts) != 2 or not parts[1].endswith(".v"):
                    continue
                sha, fname = parts
                name = os.path.basename(fname)[:-2]
                src = os.path.join(os.path.dirname(man), fname)
                tid = "%s-%s-c%s" % (name, sha[:6], cfg[:6])
                if tid in self.state.tracks:
                    active.append(tid)
                    continue
                try:
                    if sha256_file(src) != sha:
                        log("variant %s: sha256 differs from MANIFEST.sha256 (being republished?); skipped" % fname)
                        continue
                    if body_sha(src) == pbody:
                        continue  # the design of record under another header
                    path = self.ensure_core_copy(src, name, sha)
                except (IOError, OSError, RuntimeError) as e:
                    log("variant %s: %s" % (fname, e))
                    continue
                self.emit("track_new", track=tid, name=name, core=path, core_sha256=sha, config_sha256=cfg,
                          primary=False, source=fname, tree=self.tinfo)
                self.seed_track(tid, SPACE.VARIANT_FIRST_WAVE + self.transfer_seeds(primary))
                active.append(tid)
                log("new variant track %s" % tid)
        self.active_tracks = active
        self.primary = primary

    def transfer_seeds(self, primary, n=3):
        ranked = self.ranked(primary)
        return [("transfer from %s" % t["uid"], t["knobs"]) for t in ranked[:n]]

    def seed_track(self, track, seeds):
        study = self.study(track)
        seen = set()
        for label, partial in seeds:
            kn = SPACE.complete(partial)
            k = json.dumps(kn, sort_keys=True)
            if k in seen:
                continue
            seen.add(k)
            study.enqueue_trial(kn, user_attrs={"label": label})

    def study(self, track):
        if track not in self.studies:
            self.studies[track] = open_study(track)
        return self.studies[track]

    # ------------------------------------------------------------ bookkeeping helpers
    def ranked(self, track, legal_only=True):
        ts = [t for t in self.state.trials_of(track) if t["state"] == "done" and not t.get("lost")
              and t.get("metrics") and not t.get("dup_of")]
        if legal_only:
            ts = [t for t in ts if t.get("legal")]
        return sorted(ts, key=lambda t: OBJ.key(t["metrics"], t.get("legal")), reverse=True)

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
        tr = self.state.tracks[track]
        snap, ov = SPACE.materialize(knobs)
        q = self.MS.normalize_params(dict(core=tr["core"], core_tag=tr["name"], tiles="8x4", period=20,
                                          mode=mode, threads=threads, overrides=ov, **snap))
        q["tag"] = "opt"
        runs_dir = os.path.join(PATHS["runs"], runs_sub)
        run_dir, params = self.MS.build(q, runs_dir, quiet=True)
        return run_dir, params, snap, ov

    def run_id(self, track, knobs, mode, threads):
        tr = self.state.tracks[track]
        snap, ov = SPACE.materialize(knobs)
        q = self.MS.normalize_params(dict(core=tr["core"], core_tag=tr["name"], tiles="8x4", period=20,
                                          mode=mode, threads=threads, overrides=ov, **snap))
        return self.MS.run_id_for(q, tr["core_sha256"])

    def maybe_transfer(self, track):
        """Offer the primary track's current best legal configuration to a variant track once."""
        if track == self.primary:
            return
        ranked = self.ranked(self.primary)
        if not ranked:
            return
        kn = SPACE.complete(ranked[0]["knobs"])
        key = (track, json.dumps(kn, sort_keys=True))
        if key in self.transferred:
            return
        self.transferred.add(key)
        rid = self.run_id(track, kn, TRIAL["mode"], TRIAL["threads"])
        if any(t.get("run_id") == rid for t in self.state.trials_of(track)):
            return
        self.study(track).enqueue_trial(kn, user_attrs={"label": "transfer from %s" % ranked[0]["uid"]})

    def new_trial(self, track):
        self.maybe_transfer(track)
        study = self.study(track)
        trial = study.ask()
        knobs = SPACE.suggest(trial)
        uid = "%s#%d" % (track, trial.number)
        label = trial.user_attrs.get("label") or ("tpe" if trial.number >= 12 else "startup")
        try:
            rid = self.run_id(track, knobs, TRIAL["mode"], TRIAL["threads"])
        except (SystemExit, Exception):
            rid = None
        # identical effective configuration already run (or running) in this track: reuse it
        orig = next((t for t in self.state.trials_of(track) if rid and t.get("run_id") == rid
                     and t["state"] in ("submitted", "done") and not t.get("lost")), None)
        if orig is not None:
            self.emit("trial_new", uid=uid, track=track, number=trial.number, label=label, knobs=knobs,
                      snap=orig.get("snap"), overrides=orig.get("overrides"), run_id=rid, run_dir=orig.get("run_dir"),
                      tree=self.tinfo)
            self.live[uid] = trial
            self.emit("trial_dup", uid=uid, of=orig["uid"])
            if orig["state"] == "done":
                self.finish_dup(self.state.trials[uid], orig)
            log("trial %s duplicates %s" % (uid, orig["uid"]))
            return "dup"
        try:
            run_dir, params, snap, ov = self.build_snapshot(track, knobs, TRIAL["mode"], TRIAL["threads"], track)
        except (SystemExit, Exception) as e:  # floorplan check refused, bad core, ...
            self.emit("trial_new", uid=uid, track=track, number=trial.number, label=label, knobs=knobs,
                      snap=None, overrides=None, run_id=rid, run_dir=None, tree=self.tinfo)
            self.emit("trial_reject", uid=uid, reason=str(e)[:500])
            study.tell(trial.number, OBJ.FAIL_VALUE)
            log("trial %s rejected: %s" % (uid, str(e)[:200]))
            return "rejected"
        self.emit("trial_new", uid=uid, track=track, number=trial.number, label=label, knobs=knobs, snap=snap,
                  overrides=ov, run_id=params["run_id"], run_dir=run_dir, tree=self.tinfo,
                  config_changes=params.get("config_changes_vs_repo"), snapshot_sha256=params.get("snapshot_sha256"))
        self.live[uid] = trial
        return self.submit_trial(self.state.trials[uid], attempt=1)

    def submit_trial(self, t, attempt):
        name = "%s-%d" % (self.state.tracks[t["track"]]["name"], t["number"])
        jid = self.submit(name, TRIAL, os.path.join(HERE, "trial_job.sh"), [t["run_dir"]])
        if jid:
            self.emit("trial_submit", uid=t["uid"], job_id=jid, attempt=attempt, cpus=TRIAL["cpus"],
                      partition=PARTITIONS)
            log("submitted %s as %s (attempt %d) %s" % (t["uid"], jid, attempt, t["run_id"]))
        return jid

    def finish_dup(self, t, orig):
        self.emit("trial_done", uid=t["uid"], job_id=orig.get("done_job"), metrics=orig.get("metrics"),
                  legal=orig.get("legal"), blockers=orig.get("blockers"), magnitude=orig.get("magnitude"),
                  value=orig.get("value"), dup_of=orig["uid"], lost=orig.get("lost", False))
        self.tell(t, orig.get("value"))

    def tell(self, t, value):
        study = self.study(t["track"])
        try:
            study.tell(t["number"], value if value is not None else OBJ.FAIL_VALUE, skip_if_finished=True)
        except Exception as e:  # already finished or unknown trial
            log("tell %s: %s" % (t["uid"], e))
        self.live.pop(t["uid"], None)

    # ------------------------------------------------------------ polling trials
    def evaluate_run(self, run_dir, job_id, mode):
        res = load_json(os.path.join(run_dir, "result.json"))
        if not res or str(res.get("job_id")) != str(job_id):
            return None
        post = load_json(os.path.join(run_dir, "opt_post.json"))
        if not post or str(post.get("job_id")) != str(job_id):
            import postprocess
            post = postprocess.process(run_dir, job_id)
        m = OBJ.flatten(res, post, period=20.0)
        legal, blockers, mag = OBJ.legality(m, mode=mode)
        return {"metrics": m, "legal": legal, "blockers": blockers, "magnitude": mag,
                "value": OBJ.value(m, legal, mag), "status": res.get("status")}

    def job_outcome(self, job_id, jobs, run_dir, mode):
        """-> ('active'|'done'|'wait'|'retry', evaluation or None)."""
        if job_id in jobs and jobs[job_id]["state"] in SL.ACTIVE:
            return "active", None
        done = load_json(os.path.join(run_dir, "opt_done.json"))
        ev = None
        if (done and str(done.get("job_id")) == str(job_id)) or \
                (load_json(os.path.join(run_dir, "status.json"), {}).get("state") == "finished"):
            ev = self.evaluate_run(run_dir, job_id, mode)
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
            out, ev = self.job_outcome(t["job_id"], jobs, t["run_dir"], TRIAL["mode"])
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
            self.emit("trial_done", uid=t["uid"], job_id=t["job_id"], metrics=ev["metrics"], legal=ev["legal"],
                      blockers=ev["blockers"], magnitude=ev["magnitude"], value=ev["value"], lost=lost)
            self.tell(t, ev["value"])
            for d in self.state.trials.values():
                if d["state"] == "dup" and d.get("dup_of") == t["uid"]:
                    self.finish_dup(d, self.state.trials[t["uid"]])
            m = ev["metrics"] or {}
            log("done %s job %s: legal %s min WS %s (%s) value %.3f %s" % (
                t["uid"], t["job_id"], ev["legal"], m.get("min_ws"), m.get("min_ws_corner"), ev["value"],
                "; ".join(ev["blockers"][:3])))
            self.after_run(t["run_dir"], t["job_id"], keep_snap=False)
            self.dirty = True

    def after_run(self, run_dir, job_id, keep_snap):
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
        pid = "p%03d-%s" % (len(self.state.promos) + 1, self.state.tracks[t["track"]]["name"])
        try:
            run_dir, params, _, _ = self.build_snapshot(t["track"], t["knobs"], PFULL["mode"], PFULL["threads"],
                                                        t["track"] + "-full")
        except (SystemExit, Exception) as e:
            log("promotion of %s failed to build: %s" % (t["uid"], e))
            return None
        self.emit("promo_new", pid=pid, uid=t["uid"], track=t["track"], reason=reason, run_id=params["run_id"],
                  run_dir=run_dir, config_changes=params.get("config_changes_vs_repo"))
        self.submit_promo_full(self.state.promos[pid], attempt=1)
        log("promotion %s of %s (%s)" % (pid, t["uid"], reason))
        return pid

    def submit_promo_full(self, p, attempt):
        jid = self.submit("pfull-%s" % p["pid"], PFULL, os.path.join(HERE, "trial_job.sh"), [p["run_dir"]])
        if jid:
            self.emit("promo_submit", pid=p["pid"], stage="full", job_id=jid, attempt=attempt)
        return jid

    def poll_promos(self, jobs):
        for p in list(self.state.promos.values()):
            st = p["stages"]
            full = st.get("full")
            if full and full.get("state") == "submitted":
                out, ev = self.job_outcome(full["job_id"], jobs, p["run_dir"], "full")
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
            for stage in ("precheck", "gl"):
                s = st.get(stage)
                if s and s.get("state") == "submitted":
                    if s["job_id"] in jobs and jobs[s["job_id"]]["state"] in SL.ACTIVE:
                        continue
                    res = self.read_signoff(p, stage, s["job_id"])
                    if res is None:
                        acct = SL.sacct([s["job_id"]]).get(str(s["job_id"]), {})
                        ago = SL.ended_seconds_ago(acct.get("end"))
                        if ago is None or ago < RESULT_GRACE_S:
                            continue
                        if s.get("attempt", 1) < MAX_ATTEMPTS:
                            self.submit_signoff(p, stage, s.get("attempt", 1) + 1)
                            continue
                        res = {"pass": False, "error": "no result from job %s" % s["job_id"]}
                    self.emit("promo_done", pid=p["pid"], stage=stage, result=res)
                    log("promotion %s %s: pass %s" % (p["pid"], stage, res.get("pass")))
                    if stage == "precheck":
                        for g in glob.glob(os.path.join(self.promo_dir(p["pid"]), "sub", "*.gds")):
                            os.remove(g)  # the gzip copy stays in the run's out/final
                    self.dirty = True

    def prepare_sub(self, p):
        """Submission-like directory for the precheck and the GL test: info.yaml, GDS, LEF and
        the unpowered final netlist (what the gds action puts in tt_submission/)."""
        d = self.promo_dir(p["pid"])
        sub = os.path.join(d, "sub")
        os.makedirs(sub, exist_ok=True)
        if not os.path.exists(os.path.join(sub, "info.yaml")):
            shutil.copyfile(os.path.join(d, "info.yaml"), os.path.join(sub, "info.yaml"))
        for src, ext in (("gds/%s.gds.gz", "gds"), ("lef/%s.lef.gz", "lef"), ("nl/%s.nl.v.gz", "v")):
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
                for stage in ("precheck", "gl"):
                    if stage not in p["stages"]:
                        out.append((p, stage))
        return out

    def submit_signoff(self, p, stage, attempt):
        d = self.promo_dir(p["pid"])
        need = PCHECK["cpus"] if stage == "precheck" else PGL["cpus"]
        if self.free < need:
            return None
        sub = self.prepare_sub(p)
        out = os.path.join(d, stage + (".a%d" % attempt if attempt > 1 else ""))
        os.makedirs(out, exist_ok=True)
        if stage == "precheck":
            jid = self.submit("pchk-%s" % p["pid"], PCHECK, os.path.join(HERE, "precheck_job.sh"), [sub, out],
                              signal=False)
        else:
            tr = self.state.tracks[p["track"]]
            variant = "base" if tr.get("primary") else tr["name"]
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

    def maybe_promote(self, budget_left):
        inflight = [p for p in self.state.promos.values()
                    if any(s.get("state") == "submitted" for s in p["stages"].values())]
        if len(inflight) >= MAX_PROMOS_IN_FLIGHT or budget_left <= 0:
            return
        promoted = {p["uid"] for p in self.state.promos.values()}
        # control: the design of record itself through the promotion pipeline, once per primary track
        base = [t for t in self.state.trials_of(self.primary) if t.get("label", "").startswith("baseline")]
        if base and base[0]["uid"] not in promoted:
            self.promote(base[0], "control: design of record through the promotion pipeline")
            return
        for track in self.active_tracks:
            ranked = self.ranked(track)
            if not ranked:
                continue
            tr = self.state.tracks[track]
            n_done = sum(1 for t in self.state.trials_of(track) if t["state"] == "done")
            if not tr.get("primary") and n_done < VARIANT_PROMO_MIN_TRIALS:
                continue
            best = ranked[0]
            prom_ws = [self.state.trials[p["uid"]]["metrics"]["min_ws"] for p in self.state.promos.values()
                       if p["track"] == track and (self.state.trials.get(p["uid"]) or {}).get("metrics")
                       and self.state.trials[p["uid"]]["metrics"].get("min_ws") is not None]
            if best["uid"] not in promoted and (not prom_ws or best["metrics"]["min_ws"] > max(prom_ws) + PROMO_MIN_GAIN):
                self.promote(best, "best legal trial of the track")
                return
            if tr.get("primary"):
                done_promos = sum(1 for p in self.state.promos.values() if p["track"] == track)
                if n_done >= PRIMARY_PROMO_EVERY * done_promos:
                    for t in ranked[1:3]:
                        if t["uid"] not in promoted:
                            self.promote(t, "periodic: top-3 legal trial not yet promoted")
                            return

    # ------------------------------------------------------------ scheduling
    def pick_track(self, counts):
        """Track with the smallest (trials + 1) / weight among those allowed a new trial; the
        primary track's designed first wave goes first."""
        if counts.get(self.primary, 0) < len(SPACE.FIRST_WAVE):
            return self.primary
        variants = [t for t in self.active_tracks if t != self.primary
                    and counts.get(t, 0) < VARIANT_TRIAL_CAP]
        weights = {self.primary: PRIMARY_SHARE if variants else 1.0}
        for v in variants:
            weights[v] = (1.0 - PRIMARY_SHARE) / len(variants)
        return min(weights, key=lambda k: ((counts.get(k, 0) + 1) / weights[k], random.random()))

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
        counts = {}
        for t in self.state.trials.values():
            counts[t["track"]] = counts.get(t["track"], 0) + 1
        n = 0
        # trials created but never submitted (sbatch failed, or the driver stopped in between)
        for t in [t for t in self.state.trials.values() if t["state"] == "new" and t.get("run_dir")]:
            if not self.submit_trial(t, attempt=1):
                return n
            n += 1
        while (self.free >= TRIAL["cpus"] and n < MAX_SUBMIT_PER_LOOP and self.n_user < USER_JOB_CAP
               and self.state.librelane_runs() < BUDGET_RUNS - PROMO_RESERVE and time.time() >= self.backoff_until):
            track = self.pick_track(counts)
            out = self.new_trial(track)
            counts[track] = counts.get(track, 0) + 1
            if out not in ("dup", "rejected"):
                if not out:
                    break
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
            if t["state"] != "done" or not rd or rd in keep or rd in self.pruned:
                continue
            shutil.rmtree(os.path.join(rd, "out", "final"), ignore_errors=True)
            self.pruned.add(rd)

    # ------------------------------------------------------------ reconcile after a restart
    def reconcile(self):
        import optuna
        for track in self.state.tracks:
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
        REPORT.write_all(self.state, OPT, jobs=jobs, stop=stop, tree=self.tinfo, settings=settings())
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
        self.emit("driver_start", job_id=self.job_id, host=socket.gethostname(), tree=self.tinfo)
        log("driver start: job %s tree %s" % (self.job_id, self.tinfo.get("commit")))
        self.discover_tracks()
        self.reconcile()
        drained = False
        while True:
            try:
                self.write_lease()
                jobs = self.jobs()
                self.free = CPU_CAP - self.my_cpus(jobs)
                self.n_user = len(jobs)
                if time.time() - self.last_scan > 600:
                    self.discover_tracks()
                    self.last_scan = time.time()
                self.poll_trials(jobs)
                self.poll_promos(jobs)
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
                    return 0
                left = SL.parse_time_left(jobs.get(self.job_id, {}).get("time_left")) if self.job_id else None
                if left is not None and left < DRIVER_RESUBMIT_S:
                    if self.resubmit_self(jobs):
                        log("time limit near (%d s left); exiting for the successor" % left)
                        return 0
            except Exception:
                log("loop error:\n" + traceback.format_exc())
            time.sleep(LOOP_S)


def settings():
    return {"cpu_cap": CPU_CAP, "budget_runs": BUDGET_RUNS, "stop_at": STOP_AT.isoformat(),
            "primary_share": PRIMARY_SHARE, "variant_trial_cap": VARIANT_TRIAL_CAP,
            "trial": TRIAL, "promotion_full": PFULL, "precheck": PCHECK, "gl": PGL, "partitions": PARTITIONS,
            "job_prefix": JOB_PREFIX}


def status():
    st = State(Store(PATHS["store"]).events())
    jobs = SL.my_jobs() or {}
    mine = {k: v for k, v in jobs.items() if v["name"].startswith(JOB_PREFIX)}
    by = {}
    for t in st.trials.values():
        by.setdefault(t["track"], {}).setdefault(t["state"], 0)
        by[t["track"]][t["state"]] += 1
    print("store: %s" % PATHS["store"])
    print("LibreLane runs submitted: %d / %d" % (st.librelane_runs(), BUDGET_RUNS))
    for tr, c in sorted(by.items()):
        print("  %-40s %s" % (tr, " ".join("%s=%d" % kv for kv in sorted(c.items()))))
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
        REPORT.write_all(st, OPT, jobs=SL.my_jobs(), stop=None, tree=tree_info(), settings=settings())
        print(os.path.join(OPT, "leaderboard.md"))
        return 0
    d = Driver(dry_run=a.dry_run)
    return d.run(once=(a.cmd == "once"))


if __name__ == "__main__":
    sys.exit(main())
