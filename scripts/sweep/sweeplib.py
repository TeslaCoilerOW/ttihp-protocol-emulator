# SPDX-License-Identifier: Apache-2.0
"""Shared constants and helpers for the local LibreLane sweep harness.

See docs/sweep.md. Everything here is standard library only and works with
Python 3.6+ (the compute nodes' /usr/bin/python3), because run_one.sh may call
extract_result.py with whichever python3 it finds.
"""

import errno
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

# Where runs, logs and the manifest live (pool: plenty of bytes, limited inodes).
# PE_WORK is the cluster work directory (holds sweep/, sram-flow/, variants/).
WORK = os.environ.get("PE_WORK", os.path.join(REPO, "build", "tt-work"))
MAIN_SWEEP_ROOT = os.path.join(WORK, "sweep")
SWEEP_ROOT = os.environ.get("PE_SWEEP_ROOT", MAIN_SWEEP_ROOT)
# The proven local mirror of the gds action (docs/hardening.md section 7).
FLOW_ROOT = os.environ.get("PE_FLOW_ROOT", os.path.join(WORK, "sram-flow"))
SIF = os.environ.get("PE_SIF", os.path.join(FLOW_ROOT, "librelane-3.1.0.dev3.sif"))
PDK_ROOT = os.environ.get("PE_PDK_ROOT", os.path.join(FLOW_ROOT, "pdk"))
TT_DIR = os.environ.get("PE_TT_DIR", os.path.join(FLOW_ROOT, "tt"))
TECH = "ihp-sg13cmos5l"

RUNS_DIR = os.path.join(SWEEP_ROOT, "runs")
LOGS_DIR = os.path.join(SWEEP_ROOT, "logs")
ARRAYS_DIR = os.path.join(SWEEP_ROOT, "arrays")
HARNESS_DIR = os.path.join(SWEEP_ROOT, "harness")
MANIFEST = os.path.join(SWEEP_ROOT, "manifest.json")
FLOORPLAN_DIR = os.path.join(REPO, "floorplans")

MACRO = "RM_IHPSG13_1P_64x16_c2"
CORNERS = ("nom_typ_1p20V_25C", "nom_fast_1p32V_m40C", "nom_slow_1p08V_125C")
CORNER_SHORT = {"nom_typ_1p20V_25C": "typ", "nom_fast_1p32V_m40C": "fast", "nom_slow_1p08V_125C": "slow"}

# 'fast' mode: everything up to and including post-route STA, IR drop, both
# stream-outs, render, LEF and the design antenna check; then only the cheap
# final checkers. Skipped with LibreLane's own `--skip` (flows/sequential.py
# SequentialFlow.run in the 3.1.0.dev3 SIF; IDs from steps/magic.py,
# steps/netgen.py, steps/checker.py). KLayout DRC/XOR are already gated off by
# the TT template (RUN_KLAYOUT_DRC 0, RUN_KLAYOUT_XOR 0).
FAST_SKIP_STEPS = [
    "Magic.DRC",
    "Checker.MagicDRC",
    "Magic.SpiceExtraction",
    "Checker.IllegalOverlap",
    "Netgen.LVS",
    "Checker.LVS",
]
ACTION_OPENROAD_THREADS = 4  # the value in src/config.json (GitHub runner vCPUs)

# Keys a sweep run may set (anything else goes through "overrides").
REPO_DEFAULTS = {
    "core": "src/protocol_emulator_core.v",
    "core_tag": "base",
    "tiles": "8x4",
    "floorplan": "fp8_base",
    "density": 60,
    "period": 20,
    "pl_hold": 0.1,
    "grt_hold": 0.05,
    "mode": "fast",
    "threads": 32,
    "overrides": {},
}


def sha256_file(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return default


def write_json_atomic(path, obj, indent=2):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=indent, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def fmt_num(x):
    """20 -> '20', 16.667 -> '16p667', 0.05 -> '0p05' (for run ids)."""
    s = ("%.6f" % float(x)).rstrip("0").rstrip(".")
    return s.replace("-", "m").replace(".", "p")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def read_tile_sizes(tt_dir=TT_DIR):
    """tt-support-tools tech/<pdk>/tile_sizes.yaml -> {tiles: 'x0 y0 x1 y1'}."""
    path = os.path.join(tt_dir, "tech", TECH, "tile_sizes.yaml")
    sizes = {}
    with open(path) as f:
        for line in f:
            m = re.match(r'^\s*"?([0-9]+x[0-9]+)"?\s*:\s*"([^"]+)"', line)
            if m:
                sizes[m.group(1)] = m.group(2)
    return sizes


def git_head(repo=REPO):
    try:
        out = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except Exception:
        return None


def git_dirty(paths, repo=REPO):
    """Paths (relative to repo) that differ from HEAD (tracked) or are untracked."""
    try:
        out = subprocess.check_output(["git", "-C", repo, "status", "--porcelain", "--"] + list(paths),
                                      stderr=subprocess.DEVNULL)
        return [l[3:] for l in out.decode().splitlines() if l.strip()]
    except Exception:
        return None


@contextmanager
def locked(path):
    """Exclusive advisory lock on <path>.lock (manifest appends from several shells)."""
    lock = path + ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o664)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def manifest_load():
    data = load_json(MANIFEST, None)
    if data is None:
        return {"schema": 1, "description": "pe-sweep submissions (scripts/sweep/submit.py); append-only",
                "records": []}
    return data


def manifest_load_path(path):
    data = load_json(path, None)
    if data is None:
        return {"schema": 1, "description": "pe-sweep submissions (scripts/sweep/submit.py); append-only",
                "records": []}
    return data


def manifest_append(records):
    """Append to this sweep root's manifest; records of any other root (e.g. the smoke
    test) are also appended to the main manifest, tagged with their sweep_root."""
    targets = [MANIFEST]
    main = os.path.join(MAIN_SWEEP_ROOT, "manifest.json")
    if os.path.abspath(MANIFEST) != os.path.abspath(main):
        targets.append(main)
    for path in targets:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with locked(path):
            data = manifest_load_path(path)
            data["records"].extend(records)
            write_json_atomic(path, data)


def records_for_root(records, root=None):
    """Manifest records that belong to sweep root `root` (records without sweep_root: the main root)."""
    root = os.path.abspath(root or SWEEP_ROOT)
    return [r for r in records if os.path.abspath(r.get("sweep_root") or MAIN_SWEEP_ROOT) == root]


def run_dir_for(run_id):
    return os.path.join(RUNS_DIR, run_id)


def squeue_states(user=None):
    """{job_id ('123' or '123_4'): state} for this user's queued/running jobs (one line per array task)."""
    user = user or os.environ.get("USER")
    try:
        out = subprocess.check_output(["squeue", "-h", "-r", "-u", user, "-o", "%i|%T|%M|%N|%j"],
                                      stderr=subprocess.DEVNULL).decode()
    except Exception:
        return {}
    states = {}
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            states[parts[0]] = {"state": parts[1], "elapsed": parts[2] if len(parts) > 2 else "",
                                "node": parts[3] if len(parts) > 3 else "",
                                "name": parts[4] if len(parts) > 4 else ""}
    return states


def sacct_states(job_ids):
    """{job_id: {state, exit, elapsed, node, maxrss_gib}} from sacct (allocation line + max over steps)."""
    job_ids = [j for j in job_ids if j]
    res = {}
    if not job_ids:
        return res
    for chunk in [job_ids[i:i + 200] for i in range(0, len(job_ids), 200)]:
        try:
            out = subprocess.check_output(
                ["sacct", "-n", "-P", "-j", ",".join(chunk), "-o",
                 "JobID,State,ExitCode,Elapsed,NodeList,MaxRSS,AllocCPUS"],
                stderr=subprocess.DEVNULL).decode()
        except Exception:
            continue
        for line in out.splitlines():
            p = line.split("|")
            if len(p) < 7:
                continue
            jid = p[0]
            base = jid.split(".")[0]
            ent = res.setdefault(base, {"state": None, "exit": None, "elapsed": None, "node": None,
                                        "maxrss_gib": None, "cpus": None})
            if "." not in jid:
                ent.update(state=p[1].split()[0] if p[1] else None, exit=p[2], elapsed=p[3], node=p[4],
                           cpus=p[6])
            rss = parse_mem_gib(p[5])
            if rss is not None and (ent["maxrss_gib"] is None or rss > ent["maxrss_gib"]):
                ent["maxrss_gib"] = rss
    return res


def parse_mem_gib(s):
    """'6940840K' / '5GiB' / '738MiB' -> GiB (float) or None."""
    if not s:
        return None
    m = re.match(r"^\s*([0-9.]+)\s*([KMGTP]?)(i?B)?\s*$", s)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    scale = {"": 1.0 / (1 << 30), "K": 1.0 / (1 << 20), "M": 1.0 / (1 << 10), "G": 1.0, "T": 1024.0,
             "P": 1024.0 ** 2}[unit]
    if unit == "" and not m.group(3):
        scale = 1.0 / (1 << 30)  # bytes
    return v * scale


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
