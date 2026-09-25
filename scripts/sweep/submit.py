#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create snapshots for a sweep matrix and submit them as Slurm job arrays.

Matrix file (YAML or JSON; see scripts/sweep/matrices/):

  name: baseline-8x4
  defaults:                 # any make_snapshot parameter + a slurm block
    core: src/protocol_emulator_core.v
    core_tag: base
    tiles: 8x4
    floorplan: fp8_base
    density: 60
    period: 20
    pl_hold: 0.1
    grt_hold: 0.05
    mode: fast
    threads: 32             # local OPENROAD_THREADS
    slurm: {partition: mit_preemptable, cpus: 32, mem: 64G, time: "10:00:00", requeue: true}
  env: {}                   # optional extra job environment, e.g. PE_LL_EXTRA_ARGS for smoke tests
  runs:
    - {tag: dens, density: [50, 55, 65]}       # list values expand as a cartesian product
    - {tag: hold, hold: [[0.05, 0.02], [0, 0]]} # (pl_hold, grt_hold) pairs
    - {tag: canary, mode: full, slurm: {partition: mit_normal, time: "12:00:00", requeue: false}}

Runs with identical parameters collapse to one run id. Every run is submitted
as one task of a job array per (partition, cpus, mem, time, requeue) group,
named pe-sweep-<matrix>-<group>. The harness scripts are frozen (copied) under
<sweep>/harness/<sha>/ so later edits never change a queued job.

Idempotent: a run is skipped when its latest job is still queued/running, or
when it has a result.json whose status is complete/complete_with_errors. Runs
whose last attempt ended without a result, timed out, was terminated or
preempted are resubmitted; status "failed" (a LibreLane error) is only
resubmitted with --retry-failed. --rerun-complete forces everything.

Every submission appends one record per run to <sweep>/manifest.json:
run id, parameters, core sha256, config hashes, partition/resources and job id.

A run may pin its core with `core_sha256: <hex>`; the submission aborts if the
core file's sha256 differs (e.g. a variant core republished under the same name).

--cpu-budget N caps the CPUs of ALL of this user's pe-sweep-* tasks (queued +
running) on each partition at N. Runs that fit into the free capacity are
submitted as before; the rest are chained with --dependency=afterany:<job> on
earlier pe-sweep tasks (existing or just submitted), so that each one starts
only when enough capacity has been released. Capacity is conserved, so the cap
holds at every moment without anything polling, also across preemption/requeue
(a requeued task is not "ended"). Dependency targets are chosen by estimated
finish time (EST_H per mode; this only affects the order, never the cap).

Usage: submit.py MATRIX [--dry-run] [--only TAG ...] [--retry-failed] [--rerun-complete] [--cpu-budget N]
"""

import argparse
import itertools
import json
import os
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402
import make_snapshot as MS  # noqa: E402

PARAM_KEYS = ("core", "core_tag", "tiles", "floorplan", "density", "period", "pl_hold", "grt_hold",
              "mode", "threads", "overrides", "id_suffix")
EXPANDABLE = ("core", "tiles", "floorplan", "density", "period", "mode", "threads", "hold")
SLURM_DEFAULT = {"partition": "mit_preemptable", "cpus": 32, "mem": "64G", "time": "10:00:00", "requeue": True}
DONE = ("complete", "complete_with_errors")
ACTIVE = ("PENDING", "RUNNING", "REQUEUED", "CONFIGURING", "COMPLETING", "SUSPENDED", "REQUEUE_HOLD",
          "REQUEUE_FED", "RESIZING", "SIGNALING", "STAGE_OUT")
# rough wall-clock hours per run at 32 threads (first fast run: 0.36 h); only orders --cpu-budget chains
EST_H = {"fast": 0.5, "full": 1.5}
JOB_PREFIX = "pe-sweep-"


def load_matrix(path):
    with open(path) as f:
        text = f.read()
    if path.endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


def expand(matrix):
    defaults = dict(matrix.get("defaults", {}))
    out = []
    for entry in matrix["runs"]:
        e = dict(defaults)
        slurm = dict(SLURM_DEFAULT)
        slurm.update(defaults.get("slurm", {}))
        slurm.update(entry.get("slurm", {}))
        e.update({k: v for k, v in entry.items() if k != "slurm"})
        axes = []
        for k in EXPANDABLE:
            v = e.get(k)
            if k == "hold":
                if v is None:
                    continue
                pairs = v if (isinstance(v, list) and v and isinstance(v[0], list)) else [v]
                axes.append((k, pairs))
            elif isinstance(v, list):
                axes.append((k, v))
        for combo in itertools.product(*[vals for _, vals in axes]) if axes else [()]:
            p = dict(e)
            for (k, _), val in zip(axes, combo):
                if k == "hold":
                    p["pl_hold"], p["grt_hold"] = val
                else:
                    p[k] = val
            p.pop("hold", None)
            p["slurm"] = slurm
            out.append(p)
    return out


def freeze_harness():
    """Copy run_one.sh, extract_result.py, sweeplib.py to <sweep>/harness/<sha12>/ (content-addressed)."""
    names = ["run_one.sh", "extract_result.py", "sweeplib.py"]
    h = L.sha256_bytes(b"".join(open(os.path.join(L.HERE, n), "rb").read() for n in names))[:12]
    dst = os.path.join(L.HARNESS_DIR, h)
    if not os.path.isdir(dst):
        tmp = dst + ".tmp.%d" % os.getpid()
        L.mkdir_p(tmp)
        for n in names:
            shutil.copy2(os.path.join(L.HERE, n), os.path.join(tmp, n))
        os.chmod(os.path.join(tmp, "run_one.sh"), 0o755)
        os.rename(tmp, dst)
    return h, dst


def latest_records():
    recs = {}
    for r in L.records_for_root(L.manifest_load()["records"]):
        recs[r["run_id"]] = r
    return recs


def run_state(run_id, last_rec, sq):
    """-> (action, reason). action: 'skip' or 'submit'."""
    rd = L.run_dir_for(run_id)
    if last_rec:
        jid = last_rec.get("job_id")
        if jid in sq and sq[jid]["state"] in ACTIVE:
            return "skip", "job %s %s" % (jid, sq[jid]["state"])
        # pending array tasks show as ARRAYID_[a-b]; treat the parent id as active
        arr = str(last_rec.get("array_job_id"))
        for k, v in sq.items():
            if k.startswith(arr + "_[") and v["state"] in ACTIVE:
                return "skip", "array %s %s" % (k, v["state"])
    res = L.load_json(os.path.join(rd, "result.json"))
    if res:
        st = res.get("status")
        if st in DONE:
            return "done", "result %s" % st
        if st == "failed":
            return "failed", "result failed at %s" % res.get("last_step")
        return "submit", "result %s" % st
    if last_rec:
        # a task that ended seconds ago may not have its result.json visible on this node yet (the
        # pool's metadata caching; seen 2026-09-25 for 23763343_0 and 23768784_0): wait for it
        ended = recently_ended(last_rec.get("job_id"))
        if ended:
            return "skip", "job %s ended %s, result.json not visible yet; re-run later" % (
                last_rec.get("job_id"), ended)
        return "submit", "no result from job %s" % last_rec.get("job_id")
    return "submit", "new"


def recently_ended(job_id, window_s=300):
    """End time (str) of job_id if it ended less than window_s seconds ago (sacct), else None."""
    if not job_id:
        return None
    try:
        out = subprocess.check_output(["sacct", "-n", "-X", "-P", "-j", str(job_id), "-o", "End"],
                                      stderr=subprocess.DEVNULL).decode().split()
    except Exception:
        return None
    import time
    for e in out:
        try:
            t = time.mktime(time.strptime(e.strip(), "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
        if 0 <= time.time() - t < window_s:
            return e.strip()
    return None


def parse_elapsed_h(s):
    """squeue %M ('1-02:03:04', '02:03:04', '03:04') -> hours."""
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [int(x) for x in s.split(":") if x.isdigit()]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts[-3:]
    return days * 24 + h + m / 60.0 + sec / 3600.0


def _jid_key(jid):
    a, _, t = jid.partition("_")
    return (int(a) if a.isdigit() else 0, int(t) if t.isdigit() else 0)


def active_sweep_jobs(partition):
    """[{id, cpus, state, elapsed_h, mode, deps}] of this user's queued/running pe-sweep-* tasks on
    `partition` (one entry per array task; pending tasks included). deps = job ids of still
    unfulfilled dependencies (squeue %E)."""
    user = os.environ.get("USER")
    out = subprocess.check_output(["squeue", "-h", "-r", "-u", user, "-p", partition, "-o", "%i|%T|%C|%M|%j|%E"],
                                  stderr=subprocess.DEVNULL).decode()
    modes = {}
    for r in L.manifest_load_path(os.path.join(L.MAIN_SWEEP_ROOT, "manifest.json"))["records"]:
        modes[r.get("job_id")] = (r.get("params") or {}).get("mode")
    jobs = []
    for line in out.splitlines():
        p = line.split("|")
        if len(p) < 6 or not p[4].startswith(JOB_PREFIX) or p[1] not in ACTIVE:
            continue
        mode = modes.get(p[0]) or "fast"
        el = parse_elapsed_h(p[3]) if p[1] == "RUNNING" else 0.0
        deps = re.findall(r"(\d+(?:_\d+)?)\(unfulfilled\)", p[5]) if p[1] == "PENDING" else []
        jobs.append({"id": p[0], "cpus": int(p[2]), "state": p[1], "elapsed_h": el, "mode": mode, "deps": deps})
    return jobs


def _take(chunks, idxs, need):
    """Take `need` CPUs from chunks[idxs] in order; returns (taken, deps, avail) and shrinks the chunks."""
    taken, deps, avail = 0, frozenset(), 0.0
    for i in idxs:
        if taken >= need:
            break
        t = min(chunks[i]["cap"], need - taken)
        if t <= 0:
            continue
        chunks[i]["cap"] -= t
        taken += t
        deps |= chunks[i]["deps"]
        avail = max(avail, chunks[i]["avail"])
    return taken, deps, avail


def capacity_chunks(budget, active):
    """Replay the existing pe-sweep jobs into capacity chunks {deps, cap, avail}: capacity that becomes
    free once every job in `deps` has ended (avail = estimated hours until then).

    Running jobs and pending jobs without an open dependency hold their CPUs now. A pending job with
    dependencies (e.g. an earlier --cpu-budget chain) holds capacity released by those jobs only, so
    it is replayed onto the chunks of its dependencies (never onto capacity that frees later than it
    may start); anything it needs beyond that is charged to the earliest chunks (conservative)."""
    now = [j for j in active if not j["deps"]]
    later = sorted([j for j in active if j["deps"]], key=lambda j: _jid_key(j["id"]))
    chunks = []
    free = budget - sum(j["cpus"] for j in now)
    if free > 0:
        chunks.append({"deps": frozenset(), "cap": free, "avail": 0.0})
    deficit = max(0, -free)
    est = {}
    for j in now:
        if j["state"] in ("RUNNING", "COMPLETING", "CONFIGURING"):
            est[j["id"]] = max(0.1, EST_H.get(j["mode"], 1.5) - j["elapsed_h"])
        else:
            est[j["id"]] = EST_H.get(j["mode"], 1.5) + 0.5  # queued, no dependency: start time unknown
    for j in sorted(now, key=lambda j: est[j["id"]]):
        take = min(j["cpus"], deficit)  # already over budget: the earliest releases pay the excess back first
        deficit -= take
        chunks.append({"deps": frozenset([j["id"]]), "cap": j["cpus"] - take, "avail": est[j["id"]]})
    for j in later:
        chunks.sort(key=lambda ch: ch["avail"])
        dset = set(j["deps"])
        ok = [i for i, ch in enumerate(chunks) if ch["cap"] > 0 and ch["deps"] <= dset]
        ok.sort(key=lambda i: (not chunks[i]["deps"], chunks[i]["avail"]))  # its own dependencies first
        taken, deps, avail = _take(chunks, ok, j["cpus"])
        if taken < j["cpus"]:  # not fully covered by its own dependencies: charge the earliest capacity
            t2, _, a2 = _take(chunks, list(range(len(chunks))), j["cpus"] - taken)
            avail = max(avail, a2)
        chunks = [ch for ch in chunks if ch["cap"] > 0]
        chunks.append({"deps": frozenset([j["id"]]), "cap": j["cpus"],
                       "avail": avail + EST_H.get(j["mode"], 1.5)})
    return [ch for ch in chunks if ch["cap"] > 0]


def plan_budget(todo, budget, active):
    """Assign afterany dependencies so that the CPUs of `active` + `todo` never exceed `budget`.

    todo: [(rid, p, params, run_dir)] in priority order, all on one partition. Returns {rid: [dep, ...]}
    where dep is an existing job id (str) or ('new', rid) for a run submitted earlier in this call."""
    chunks = capacity_chunks(budget, active)
    plan = {}
    for rid, p, params, run_dir in todo:
        c = int(p["slurm"]["cpus"])
        if c > budget:
            raise SystemExit("run %s needs %d CPUs, more than --cpu-budget %d" % (rid, c, budget))
        chunks.sort(key=lambda ch: ch["avail"])
        single = next((i for i, ch in enumerate(chunks) if ch["cap"] >= c), None)
        acc, idx = 0, []
        for i, ch in enumerate(chunks):
            acc += ch["cap"]
            idx.append(i)
            if acc >= c:
                break
        if acc < c and single is None:
            raise SystemExit("internal: cannot place %s within the budget" % rid)
        comb_avail = max(chunks[i]["avail"] for i in idx) if acc >= c else float("inf")
        use = [single] if single is not None and chunks[single]["avail"] <= comb_avail else idx
        deps = frozenset().union(*[chunks[i]["deps"] for i in use])
        total = sum(chunks[i]["cap"] for i in use)
        avail = max(chunks[i]["avail"] for i in use)
        chunks = [ch for i, ch in enumerate(chunks) if i not in use]
        if total - c > 0:
            chunks.append({"deps": deps, "cap": total - c, "avail": avail})
        chunks.append({"deps": frozenset([("new", rid)]), "cap": c,
                       "avail": avail + EST_H.get(params.get("mode"), 1.5)})
        plan[rid] = sorted(deps, key=str)
    return plan


def sbatch_array(items, gname, part, cpus, mem, tlim, requeue, hdir, matrix, args, dependency=None):
    """Submit `items` as one job array; returns the array job id (None on --dry-run)."""
    # The list file must be unique per array: two arrays of the same group submitted within one
    # second (e.g. a fast and a full array of one matrix) once shared "<gname>.<time>.list", and the
    # second overwrote the first while its tasks were queued (arrays 23793630/23793631, 2026-09-25).
    # Name = group, time, pid, sha of the content; created with O_EXCL so a clash fails loudly.
    content = "".join(it[3] + "\n" for it in items)
    list_path = os.path.join(L.ARRAYS_DIR, "%s.%s.p%d.%s.list" % (
        gname, L.now_iso().replace(":", ""), os.getpid(), L.sha256_bytes(content.encode())[:10]))
    if not args.dry_run:
        fd = os.open(list_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
        with os.fdopen(fd, "w") as f:
            f.write(content)
    n = len(items)
    cmd = ["sbatch", "--parsable", "-J", "pe-sweep-" + gname, "-p", part, "-N", "1", "-n", "1",
           "-c", str(cpus), "--mem", mem, "-t", tlim,
           "--array", "0-%d%s" % (n - 1, ("%%%d" % args.throttle) if args.throttle else ""),
           "--signal", "B:USR1@900", "--open-mode", "append",
           "-o", os.path.join(L.LOGS_DIR, "%x-%A_%a.out"),
           "--export", ",".join(["ALL", "PE_HARNESS=" + hdir, "PE_FLOW_ROOT=" + L.FLOW_ROOT,
                                 "PE_SWEEP_ROOT=" + L.SWEEP_ROOT]
                                + ["%s=%s" % kv for kv in sorted(matrix.get("env", {}).items())]),
           "--requeue" if requeue else "--no-requeue"]
    if dependency:
        cmd += ["--dependency", dependency]
    if args.exclude:
        cmd += ["--exclude", args.exclude]
    cmd += [os.path.join(hdir, "run_one.sh"), "--list", list_path]
    print("+", " ".join(cmd))
    if args.dry_run:
        return None, list_path
    out = subprocess.check_output(cmd).decode().strip()
    array_id = out.split(";")[0]
    print("submitted array %s (%d tasks) for group %s%s" % (array_id, n, gname,
                                                            (" after " + dependency) if dependency else ""))
    return array_id, list_path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("matrix")
    ap.add_argument("--dry-run", action="store_true", help="create snapshots and print, but do not submit")
    ap.add_argument("--only", action="append", default=[], help="only runs with this tag (repeatable)")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--rerun-complete", action="store_true")
    ap.add_argument("--throttle", type=int, default=0, help="max simultaneously running tasks per array (0: none)")
    ap.add_argument("--exclude", default=os.environ.get("PE_EXCLUDE_NODES", ""),
                    help="sbatch --exclude node list, e.g. preemptable nodes whose owners keep reclaiming them "
                         "(default $PE_EXCLUDE_NODES)")
    ap.add_argument("--cpu-budget", type=int, default=0,
                    help="cap on the CPUs of all of this user's pe-sweep-* tasks per partition (queued + running); "
                         "runs beyond the free capacity are chained with afterany dependencies (0: no cap)")
    args = ap.parse_args()

    matrix = load_matrix(args.matrix)
    mname = matrix.get("name") or os.path.splitext(os.path.basename(args.matrix))[0]
    points = expand(matrix)
    if args.only:
        points = [p for p in points if p.get("tag") in args.only]

    for d in (L.RUNS_DIR, L.LOGS_DIR, L.ARRAYS_DIR, L.HARNESS_DIR):
        L.mkdir_p(d)
    hsha, hdir = freeze_harness()
    sq = L.squeue_states()
    last = latest_records()

    # snapshots (idempotent) and dedupe by run id
    seen = {}
    for p in points:
        q = MS.normalize_params({k: p[k] for k in PARAM_KEYS if k in p})
        q["tag"] = p.get("tag")
        want = p.get("core_sha256")
        if want:
            have = L.sha256_file(MS.resolve_core(q["core"]))
            if have != want:
                raise SystemExit("core %s: sha256 %s, matrix pins %s (republished?)" % (q["core"], have, want))
        run_dir, params = MS.build(q, L.RUNS_DIR, quiet=True)
        rid = params["run_id"]
        if want and params.get("core_sha256") != want:
            raise SystemExit("run %s: snapshot core sha256 %s != pinned %s" % (rid, params.get("core_sha256"), want))
        if rid in seen:
            print("dup   %-72s (tag %s, same as tag %s)" % (rid, p.get("tag"), seen[rid][0].get("tag")))
            continue
        seen[rid] = (p, params, run_dir)

    todo = []
    for rid, (p, params, run_dir) in seen.items():
        action, why = run_state(rid, last.get(rid), sq)
        if action == "done" and args.rerun_complete:
            action = "submit"
        if action == "failed":
            action = "submit" if args.retry_failed else "skip"
        print("%-6s %-72s %s" % (action, rid, why))
        if action != "submit":
            continue
        todo.append((rid, p, params, run_dir))

    def gkey(p):
        s = p["slurm"]
        return (s["partition"], int(s["cpus"]), str(s["mem"]), str(s["time"]), bool(s.get("requeue", True)))

    def gname_for(key):
        part, cpus = key[0], key[1]
        return "%s-%s-c%d" % (mname, "pre" if part == "mit_preemptable" else part.replace("mit_", ""), cpus)

    def make_records(items, array_id, key, list_path, dependency):
        part, cpus, mem, tlim, requeue = key
        recs = []
        for i, (rid, p, params, run_dir) in enumerate(items):
            recs.append({
                "run_id": rid,
                "sweep_root": L.SWEEP_ROOT,
                "kind": "sweep" if os.path.abspath(L.SWEEP_ROOT) == os.path.abspath(L.MAIN_SWEEP_ROOT)
                        else "aux:" + os.path.basename(L.SWEEP_ROOT.rstrip("/")),
                "tag": p.get("tag"),
                "matrix": mname,
                "matrix_file": os.path.abspath(args.matrix),
                "submitted": L.now_iso(),
                "job_id": "%s_%d" % (array_id, i),
                "array_job_id": array_id,
                "array_index": i,
                "job_name": "pe-sweep-" + gname_for(key),
                "partition": part, "cpus": cpus, "mem": mem, "time": tlim, "requeue": requeue,
                "dependency": dependency,
                "exclude": args.exclude or None,
                "cpu_budget": args.cpu_budget or None,
                "run_dir": run_dir,
                "list_file": list_path,
                "harness_sha": hsha,
                "env": matrix.get("env", {}),
                "params": {k: params.get(k) for k in ("core", "core_tag", "tiles", "floorplan", "density", "period",
                                                      "pl_hold", "grt_hold", "mode", "threads", "overrides",
                                                      "clock_hz")},
                "core_sha256": params.get("core_sha256"),
                "config_sha256": params.get("config_sha256"),
                "config_merged_sha256": params.get("config_merged_sha256"),
                "snapshot_sha256": params.get("snapshot_sha256"),
                "repo_head": (params.get("repo") or {}).get("head"),
            })
        if recs and not args.dry_run:
            L.manifest_append(recs)  # per sbatch call, so a later failure never loses a submitted job
            print("manifest: +%d records -> %s" % (len(recs), L.MANIFEST))
        return recs

    n_rec = 0
    if not args.cpu_budget:
        groups = {}
        for it in todo:
            groups.setdefault(gkey(it[1]), []).append(it)
        for key, items in sorted(groups.items()):
            array_id, list_path = sbatch_array(items, gname_for(key), *key, hdir=hdir, matrix=matrix, args=args)
            if array_id:
                n_rec += len(make_records(items, array_id, key, list_path, None))
    else:
        # capacity-chained submission, per partition, in matrix order (= priority order)
        by_part = {}
        for it in todo:
            by_part.setdefault(gkey(it[1])[0], []).append(it)
        for part, items in sorted(by_part.items()):
            active = active_sweep_jobs(part)
            used = sum(j["cpus"] for j in active)
            print("budget %s: %d CPUs cap; pe-sweep tasks active %d (%d CPUs: %d running, %d pending); "
                  "%d new runs (%d CPUs)" % (part, args.cpu_budget, len(active), used,
                                             sum(j["cpus"] for j in active if j["state"] == "RUNNING"),
                                             sum(j["cpus"] for j in active if j["state"] != "RUNNING"),
                                             len(items), sum(int(i[1]["slurm"]["cpus"]) for i in items)))
            plan = plan_budget(items, args.cpu_budget, active)
            for it in items:
                print("plan   %-72s %s" % (it[0], " & ".join(d[1] if isinstance(d, tuple) else d
                                                          for d in plan[it[0]]) or "start now"))
            newid = {}
            free_now = [it for it in items if not plan[it[0]]]
            groups = {}
            for it in free_now:
                groups.setdefault(gkey(it[1]), []).append(it)
            for key, g in sorted(groups.items()):
                print("start now (%d): %s" % (len(g), ", ".join(i[0] for i in g)))
                array_id, list_path = sbatch_array(g, gname_for(key), *key, hdir=hdir, matrix=matrix, args=args)
                for i, it in enumerate(g):
                    newid[it[0]] = "%s_%d" % (array_id, i) if array_id else "<new:%s>" % it[0]
                if array_id:
                    n_rec += len(make_records(g, array_id, key, list_path, None))
            for it in items:
                deps = plan[it[0]]
                if not deps:
                    continue
                ids = [newid[d[1]] if isinstance(d, tuple) else d for d in deps]
                dependency = "afterany:" + ":".join(ids)
                key = gkey(it[1])
                array_id, list_path = sbatch_array([it], gname_for(key), *key, hdir=hdir, matrix=matrix, args=args,
                                                   dependency=dependency)
                newid[it[0]] = "%s_0" % array_id if array_id else "<new:%s>" % it[0]
                if array_id:
                    n_rec += len(make_records([it], array_id, key, list_path, dependency))
    if n_rec:
        print("manifest: %d records appended in total" % n_rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
