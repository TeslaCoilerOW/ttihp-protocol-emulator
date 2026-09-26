# SPDX-License-Identifier: Apache-2.0
"""Append-only results store of the optimizer: one JSON event per line.

The store is the record of the campaign: every trial, submission, result and
promotion is an event, and the in-memory state is rebuilt from it at every
driver start (so a preempted or restarted driver resumes where it stopped).
Lines are appended with O_APPEND, flushed and fsync'ed; a torn last line (a
kill in the middle of a write) is ignored on load. Standard library only.

Events (field "ev"):
  driver_start   job_id, host, tree, commit
  track_new      track, name, core, core_sha256, config_sha256, primary
  trial_new      uid, track, number, label, knobs, snap, overrides, run_id, run_dir, tree
  trial_reject   uid, reason                  (snapshot/floorplan check refused the point)
  trial_submit   uid, job_id, attempt, cpus, partition
  trial_dup      uid, of                      (identical effective configuration)
  trial_done     uid, job_id, metrics, legal, blockers, magnitude, value
  promo_new      pid, uid, track, reason, run_id, run_dir
  promo_submit   pid, stage (full|precheck|gl), job_id, attempt
  promo_done     pid, stage, result
  note           text
"""

import json
import os
import time


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Store(object):
    def __init__(self, path):
        self.path = path
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)

    def append(self, ev, **fields):
        rec = {"t": now_iso(), "ev": ev}
        rec.update(fields)
        line = (json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n").encode()
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o664)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        return rec

    def events(self):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "rb") as f:
            for raw in f:
                try:
                    out.append(json.loads(raw.decode()))
                except ValueError:
                    continue  # torn line
        return out


class State(object):
    """Campaign state rebuilt from the events."""

    def __init__(self, events):
        self.tracks = {}
        self.trials = {}
        self.promos = {}
        self.drivers = []
        self.notes = []
        for e in events:
            self.apply(e)

    def apply(self, e):
        ev = e.get("ev")
        if ev == "driver_start":
            self.drivers.append(e)
        elif ev == "track_new":
            self.tracks[e["track"]] = dict(e)
        elif ev == "trial_new":
            t = dict(e)
            t.update(state="new", submits=[], created=e["t"])
            self.trials[e["uid"]] = t
        elif ev == "trial_reject":
            t = self.trials.get(e["uid"])
            if t is not None:
                t.update(state="rejected", reason=e.get("reason"), finished=e["t"])
        elif ev == "trial_submit":
            t = self.trials.get(e["uid"])
            if t is not None:
                t["submits"].append(e)
                t.update(state="submitted", job_id=e["job_id"], attempt=e.get("attempt", 1))
        elif ev == "trial_dup":
            t = self.trials.get(e["uid"])
            if t is not None:
                t.update(state="dup", dup_of=e["of"])
        elif ev == "trial_done":
            t = self.trials.get(e["uid"])
            if t is not None:
                t.update(state="done", metrics=e.get("metrics"), legal=e.get("legal"),
                         blockers=e.get("blockers"), magnitude=e.get("magnitude"), value=e.get("value"),
                         done_job=e.get("job_id"), finished=e["t"], lost=e.get("lost", False))
        elif ev == "promo_new":
            p = dict(e)
            p.update(stages={}, created=e["t"])
            self.promos[e["pid"]] = p
        elif ev == "promo_submit":
            p = self.promos.get(e["pid"])
            if p is not None:
                st = p["stages"].setdefault(e["stage"], {"submits": []})
                st["submits"].append(e)
                st.update(job_id=e["job_id"], state="submitted", attempt=e.get("attempt", 1))
        elif ev == "promo_done":
            p = self.promos.get(e["pid"])
            if p is not None:
                st = p["stages"].setdefault(e["stage"], {"submits": []})
                st.update(state="done", result=e.get("result"), finished=e["t"])
        elif ev == "note":
            self.notes.append(e)

    # ---- queries
    def trials_of(self, track):
        return [t for t in self.trials.values() if t["track"] == track]

    def in_flight_trials(self):
        return [t for t in self.trials.values() if t["state"] == "submitted"]

    def librelane_runs(self):
        """LibreLane runs submitted (first attempts of trials and promoted full runs)."""
        n = sum(1 for t in self.trials.values() if t["submits"])
        n += sum(1 for p in self.promos.values() if "full" in p["stages"])
        return n
