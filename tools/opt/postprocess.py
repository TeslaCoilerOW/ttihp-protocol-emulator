#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Post-process one finished optimizer run directory (run by trial_job.sh after
scripts/sweep/run_one.sh, or by the driver when that did not happen).

Writes RUN_DIR/opt_post.json with
  lef          checks.py lef on out/final/lef/<top>.lef.gz (kept for candidates only)
  pdn          the GeneratePDN DEF check written by trial_job.sh (opt_pdn.json), if any
  worst_paths  start/end point and slack of the worst setup path per corner, read from the
               post-route STA max.rpt files inside out/librelane-logs.tar.gz
  resizer      the RSZ-* messages of the post-CTS and post-GRT timing-repair steps (whether
               setup repair acted at all, buffers inserted, hold buffers), from the same tarball

Standard library only; Python 3.6+.
"""

import argparse
import glob
import json
import os
import re
import sys
import tarfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checks  # noqa: E402

CORNER_DIRS = {"nom_typ_1p20V_25C": "typ", "nom_fast_1p32V_m40C": "fast", "nom_slow_1p08V_125C": "slow"}
MEMBER_RE = re.compile(r"(?:^|/)\d+-openroad-stapostpnr/(nom_[a-z]+_[0-9p]+V_m?\d+C)/max\.rpt$")


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return None


RSZ_RE = re.compile(r"(?:^|/)\d+-openroad-(resizertimingpostcts|resizertimingpostgrt)/[^/]+\.log$")


def scan_logs(tar_path):
    """-> (worst_paths, resizer). worst_paths: {corner: {start, end, slack}} from the first path of
    each post-route max.rpt; resizer: {step: [RSZ message lines]}."""
    out, rsz = {}, {}
    if not os.path.isfile(tar_path):
        return out, rsz
    try:
        with tarfile.open(tar_path, "r|gz") as tf:
            for mem in tf:
                m = MEMBER_RE.search(mem.name)
                r = RSZ_RE.search(mem.name)
                if not (m and m.group(1) in CORNER_DIRS) and not r:
                    continue
                f = tf.extractfile(mem)
                if f is None:
                    continue
                if r:
                    text = f.read(4000000).decode("utf-8", "replace")
                    lines = re.findall(r"^\[(?:INFO|WARNING|ERROR) RSZ-\d+\].*$", text, re.M)
                    rsz[r.group(1)] = lines[:40]
                    continue
                head = f.read(200000).decode("utf-8", "replace")
                ent = {}
                s = re.search(r"^Startpoint:\s*(\S+)", head, re.M)
                e = re.search(r"^Endpoint:\s*(\S+)", head, re.M)
                sl = re.search(r"^\s*(-?[\d.]+)\s+slack", head, re.M)
                ent["start"] = s.group(1) if s else None
                ent["end"] = e.group(1) if e else None
                ent["slack"] = float(sl.group(1)) if sl else None
                out[CORNER_DIRS[m.group(1)]] = ent
    except (tarfile.TarError, OSError, EOFError) as exc:
        out["error"] = str(exc)
    return out, rsz


def process(run_dir, job_id=None):
    post = {"schema": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "job_id": job_id}
    lefs = sorted(glob.glob(os.path.join(run_dir, "out", "final", "lef", "*.lef.gz")))
    if lefs:
        post["lef"] = checks.check_lef(lefs[0])
        post["lef"]["file"] = os.path.relpath(lefs[0], run_dir)
    pdn = load(os.path.join(run_dir, "opt_pdn.json"))
    if pdn:
        pdn["file"] = os.path.basename(pdn.get("file") or "")
        post["pdn"] = pdn
    post["worst_paths"], post["resizer"] = scan_logs(os.path.join(run_dir, "out", "librelane-logs.tar.gz"))
    tmp = os.path.join(run_dir, "opt_post.json.tmp.%d" % os.getpid())
    with open(tmp, "w") as f:
        json.dump(post, f, indent=2)
        f.write("\n")
    os.replace(tmp, os.path.join(run_dir, "opt_post.json"))
    return post


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--job-id")
    a = ap.parse_args()
    post = process(a.run_dir, a.job_id)
    print(json.dumps({k: (v if k not in ("lef", "resizer") else
                          ({"ok": v.get("ok"), "port_violations": v.get("port_violations")} if k == "lef"
                           else {st: len(ls) for st, ls in v.items()}))
                      for k, v in post.items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
