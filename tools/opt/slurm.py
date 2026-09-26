# SPDX-License-Identifier: Apache-2.0
"""Small Slurm helpers for the optimizer driver (squeue/sacct/sbatch/scancel).
Standard library only."""

import os
import re
import subprocess
import time

ACTIVE = ("PENDING", "RUNNING", "REQUEUED", "CONFIGURING", "COMPLETING", "SUSPENDED", "REQUEUE_HOLD",
          "REQUEUE_FED", "RESIZING", "SIGNALING", "STAGE_OUT")


def _run(cmd, timeout=60):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          timeout=timeout)


def my_jobs(user=None):
    """All of this user's jobs, one entry per job or array task:
    {id: {name, state, cpus, partition, node, reason, time_left}}. None if squeue failed."""
    user = user or os.environ.get("USER")
    try:
        r = _run(["squeue", "-h", "-r", "-u", user, "-o", "%i|%j|%T|%C|%P|%N|%r|%L"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    jobs = {}
    for line in r.stdout.splitlines():
        p = line.split("|")
        if len(p) < 8:
            continue
        jobs[p[0]] = {"name": p[1], "state": p[2], "cpus": int(p[3]) if p[3].isdigit() else 0,
                      "partition": p[4], "node": p[5], "reason": p[6], "time_left": p[7]}
    return jobs


def sacct(job_ids):
    """{job_id: {state, end, elapsed, exit}} for the allocation lines."""
    out = {}
    ids = [str(j) for j in job_ids if j]
    for i in range(0, len(ids), 100):
        try:
            r = _run(["sacct", "-n", "-X", "-P", "-j", ",".join(ids[i:i + 100]), "-o", "JobID,State,End,Elapsed,ExitCode"])
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in r.stdout.splitlines():
            p = line.split("|")
            if len(p) >= 5:
                out[p[0]] = {"state": p[1].split()[0] if p[1] else None, "end": p[2], "elapsed": p[3], "exit": p[4]}
    return out


def ended_seconds_ago(end):
    try:
        t = time.mktime(time.strptime(end.strip(), "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, AttributeError):
        return None
    return time.time() - t


def parse_time_left(s):
    """squeue %L ('1-02:03:04', '02:03:04', '03:04', 'INVALID', 'UNLIMITED') -> seconds or None."""
    if not s or not re.match(r"^[\d:-]+$", s):
        return None
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [int(x) for x in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts[-3:]
    return days * 86400 + h * 3600 + m * 60 + sec


def sbatch(args, script_args, dry_run=False):
    """Submit; returns (job_id, error)."""
    cmd = ["sbatch", "--parsable"] + list(args) + list(script_args)
    if dry_run:
        return "dry", None
    try:
        r = _run(cmd, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout).strip()[:500]
    return r.stdout.strip().split(";")[0], None


def scancel(job_ids):
    ids = [str(j) for j in job_ids if j]
    if ids:
        try:
            _run(["scancel"] + ids)
        except (OSError, subprocess.TimeoutExpired):
            pass
