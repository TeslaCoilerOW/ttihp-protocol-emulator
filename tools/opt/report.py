#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Leaderboard of the optimizer: <opt root>/leaderboard.md, leaderboard.csv and
manifest.json (job ids), rebuilt from the results store. The driver calls
write_all() after every completed trial; `report.py` alone rebuilds them.

Everything here reports what the store records: fast-mode numbers come from
trial runs (OPENROAD_THREADS 32, no LVS), sign-off numbers from promoted full
runs (OPENROAD_THREADS 4, LVS), the TT precheck and the gate-level tests.
Standard library only.
"""

import csv
import json
import os
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import objective as OBJ  # noqa: E402
from store import State, Store  # noqa: E402

LOCAL_ONLY_KEYS = ("OPENROAD_THREADS",)
MACRO_KEY = "MACROS"


def fmt(x, nd=2):
    if x is None:
        return "-"
    if isinstance(x, float):
        return ("%%.%df" % nd) % x
    return str(x)


def ranked(state, track, legal_only=True):
    ts = [t for t in state.trials_of(track) if t["state"] == "done" and not t.get("lost") and t.get("metrics")
          and not t.get("dup_of")]
    if legal_only:
        ts = [t for t in ts if t.get("legal")]
    return sorted(ts, key=lambda t: OBJ.key(t["metrics"], t.get("legal")), reverse=True)


def knob_summary(t):
    """Compact description of what differs from the committed configuration."""
    parts = []
    snap = t.get("snap") or {}
    if snap.get("floorplan") and snap["floorplan"] != "fp8_base":
        parts.append("fp=%s" % snap["floorplan"])
    if snap.get("density") not in (None, 60, 60.0):
        parts.append("density=%s" % fmt(snap["density"], 0))
    if snap.get("pl_hold") not in (None, 0.1):
        parts.append("PL_HOLD=%s" % snap["pl_hold"])
    if snap.get("grt_hold") not in (None, 0.05):
        parts.append("GRT_HOLD=%s" % snap["grt_hold"])
    for k, v in sorted((t.get("overrides") or {}).items()):
        parts.append("%s=%s" % (k, json.dumps(v)))
    return "; ".join(parts) or "(committed configuration)"


def config_diff(changes, floorplan=None):
    """params.json config_changes_vs_repo -> (set, remove) for src/config.json, without local-only keys."""
    set_, remove = {}, []
    for k, v in sorted((changes or {}).items()):
        if k in LOCAL_ONLY_KEYS:
            continue
        if k == MACRO_KEY:
            set_[k] = "<MACROS.RM_IHPSG13_1P_64x16_c2.instances from floorplans/%s.json>" % floorplan
            continue
        if v.get("run") == "<unset>":
            remove.append(k)
        else:
            set_[k] = v.get("run")
    return set_, remove


def promo_rows(state):
    rows = []
    for p in sorted(state.promos.values(), key=lambda p: p["pid"]):
        st = p["stages"]
        full = st.get("full") or {}
        fr = full.get("result") or {}
        fm = fr.get("metrics") or {}
        pc = (st.get("precheck") or {})
        gl = (st.get("gl") or {})
        pcr, glr = pc.get("result") or {}, gl.get("result") or {}
        if full.get("state") != "done":
            verdict = "full run %s" % (full.get("state") or "not submitted")
        elif not fr.get("legal"):
            verdict = "FAIL (full run: %s)" % "; ".join((fr.get("blockers") or [])[:2])
        elif pc.get("state") != "done" or gl.get("state") != "done":
            verdict = "sign-off running"
        elif pcr.get("pass") and glr.get("not_run"):
            verdict = "PASS (gate-level tests not run)"
        else:
            verdict = "PASS" if (pcr.get("pass") and glr.get("pass")) else "FAIL"
        rows.append({
            "pid": p["pid"], "uid": p["uid"], "reason": p.get("reason"), "run_id": p.get("run_id"),
            "full_jobs": " ".join(s["job_id"] for s in full.get("submits", [])),
            "legal": fr.get("legal"), "lvs": fm.get("lvs_errors"), "min_ws": fm.get("min_ws"),
            "typ_ws": fm.get("typ_setup_ws"), "slow_ws": fm.get("slow_setup_ws"), "util": fm.get("utilization"),
            "drv": fm.get("drv_vio_sum"),
            "precheck": ("%s (%d/%d)" % ("PASS" if pcr.get("pass") else "FAIL", pcr.get("n_checks", 0) - pcr.get("n_fail", 0),
                                         pcr.get("n_checks", 0)) if pc.get("state") == "done" else (pc.get("state") or "-")),
            "precheck_jobs": " ".join(s["job_id"] for s in pc.get("submits", [])),
            "gl": ("not run (no reference-model configuration)" if glr.get("not_run") else
                   "%s (%s pass, %s skip, %s fail)" % ("PASS" if glr.get("pass") else "FAIL", glr.get("passed"),
                                                       glr.get("skipped"), glr.get("failed"))
                   if gl.get("state") == "done" else (gl.get("state") or "-")),
            "gl_jobs": " ".join(s["job_id"] for s in gl.get("submits", [])),
            "verdict": verdict, "config_changes": p.get("config_changes"), "track": p["track"],
        })
    return rows


def write_csv(state, path):
    rows = []
    for t in sorted(state.trials.values(), key=lambda t: (t["track"], t["number"])):
        m = t.get("metrics") or {}
        r = {"uid": t["uid"], "track": t["track"], "number": t["number"], "label": t.get("label"),
             "state": t["state"], "legal": t.get("legal"), "value": t.get("value"),
             "blockers": "; ".join(t.get("blockers") or []), "dup_of": t.get("dup_of") or "",
             "job_ids": " ".join(s["job_id"] for s in t.get("submits", [])), "run_id": t.get("run_id"),
             "knobs": json.dumps(t.get("knobs"), sort_keys=True), "overrides": json.dumps(t.get("overrides"), sort_keys=True),
             "created": t.get("created"), "finished": t.get("finished")}
        for k in sorted(m):
            r["m_" + k] = m[k]
        rows.append(r)
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def write_manifest(state, path, settings):
    man = {"label": "optimizer", "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "settings": settings,
           "driver_jobs": [{"job_id": d.get("job_id"), "host": d.get("host"), "start": d.get("t"),
                            "tree": (d.get("tree") or {}).get("commit")} for d in state.drivers],
           "successor_driver_jobs": [n.get("job_id") for n in state.notes if n.get("job_id")],
           "trials": [{"uid": t["uid"], "run_id": t.get("run_id"), "state": t["state"],
                       "job_ids": [s["job_id"] for s in t.get("submits", [])]}
                      for t in sorted(state.trials.values(), key=lambda t: (t["track"], t["number"]))],
           "promotions": [{"pid": p["pid"], "uid": p["uid"], "run_id": p.get("run_id"),
                           "jobs": {k: [s["job_id"] for s in v.get("submits", [])] for k, v in p["stages"].items()}}
                          for p in sorted(state.promos.values(), key=lambda p: p["pid"])]}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(man, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def trial_row(i, t):
    m = t["metrics"]
    job = t.get("done_job") or t.get("job_id") or "-"
    return "| %s | %s | %s | %s (%s) | %s | %s | %s | %s | %s/%s/%s | %s | %s | %s | %s |" % (
        i, t["uid"].split("#")[1], "yes" if t.get("legal") else "no", fmt(m.get("min_ws")), m.get("min_ws_corner") or "-",
        fmt(m.get("typ_setup_ws")), fmt(m.get("fast_setup_ws")), fmt(m.get("slow_setup_ws")),
        m.get("slow_worst_start") or "-", fmt(m.get("max_slew_vio")), fmt(m.get("max_cap_vio")),
        fmt(m.get("max_fanout_vio")), fmt(m.get("typ_fmax_mhz"), 1), fmt(m.get("utilization"), 4), job,
        knob_summary(t).replace("|", "/"))


HEAD = ("| # | trial | legal | min WS ns (corner) | typ WS | fast WS | slow WS | slow worst start | "
        "slew/cap/fanout vio | typ fmax MHz | util | job | configuration vs committed |\n"
        "|---:|---:|---|---|---:|---:|---:|---|---|---:|---:|---|---|")


def write_md(state, path, jobs=None, stop=None, tree=None, settings=None):
    settings = settings or {}
    L = []
    now = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    tracks = sorted(state.tracks.values(), key=lambda t: (not t.get("primary"), t["track"]))
    primary = [t for t in state.tracks.values() if t.get("primary")]  # creation order: the newest is current
    primary = primary[-1]["track"] if primary else None
    done = [t for t in state.trials.values() if t["state"] == "done"]
    inflight = state.in_flight_trials()
    mine = {k: v for k, v in (jobs or {}).items() if v["name"].startswith(settings.get("job_prefix", "pe-v2-optimizer-"))}
    L.append("# Physical-design optimizer: leaderboard")
    L.append("")
    L.append("Generated %s by tools/opt/report.py from the results store; see docs/optimization.md. "
             "Trial numbers are fast-mode runs (post-route STA at all three corners, antenna, power-port checks; "
             "no LVS; OPENROAD_THREADS 32). Sign-off numbers are promoted full runs (OPENROAD_THREADS 4, LVS), "
             "the TT precheck and the gate-level tests." % now)
    L.append("")
    L.append("| Item | Value |")
    L.append("|---|---|")
    L.append("| Frozen tree | commit %s, tools %s |" % ((tree or {}).get("commit", "-"), (tree or {}).get("tools_sha8", "-")))
    L.append("| LibreLane runs submitted | %d of %s |" % (state.librelane_runs(), settings.get("budget_runs", "-")))
    L.append("| Trials done / legal / in flight | %d / %d / %d |" % (
        len(done), sum(1 for t in done if t.get("legal")), len(inflight)))
    L.append("| Jobs now (pe-v2-optimizer-*) | %d, %d CPUs (%d running) of cap %s |" % (
        len(mine), sum(v["cpus"] for v in mine.values()), sum(1 for v in mine.values() if v["state"] == "RUNNING"),
        settings.get("cpu_cap", "-")))
    L.append("| Stop condition | %s (date limit %s; STOP file) |" % (stop or "none reached", settings.get("stop_at", "-")))
    L.append("")

    # ---- best legal configuration
    if primary:
        tr = state.tracks[primary]
        rk = ranked(state, primary)
        base = [t for t in state.trials_of(primary) if (t.get("label") or "").startswith("baseline")
                and t["state"] == "done" and t.get("metrics")]
        L.append("## Design of record (track `%s`)" % primary)
        L.append("")
        L.append("Core sha256 `%s`, src/config.json sha256 `%s`." % (tr.get("core_sha256", "")[:16],
                                                                      tr.get("config_sha256", "")[:16]))
        L.append("")
        if base:
            L.append("Baseline trial (committed configuration, fast mode):")
            L.append("")
            L.append(HEAD)
            L.append(trial_row("base", base[0]))
            if not base[0].get("legal"):
                L.append("")
                L.append("Baseline blockers: %s" % "; ".join(base[0].get("blockers") or []))
            L.append("")
        L.append("### Best legal configuration")
        L.append("")
        if rk:
            b = rk[0]
            m = b["metrics"]
            L.append("Trial `%s` (%s), job %s, run `%s`: min WS %s ns (%s corner); typ/fast/slow setup WS "
                     "%s/%s/%s ns; hold WS typ/fast/slow %s/%s/%s ns; slew/cap/fanout violations %s/%s/%s; "
                     "typ fmax %s MHz; utilization %s." % (
                         b["uid"], b.get("label"), b.get("done_job") or b.get("job_id"), b.get("run_id"),
                         fmt(m.get("min_ws"), 3), m.get("min_ws_corner"), fmt(m.get("typ_setup_ws"), 3),
                         fmt(m.get("fast_setup_ws"), 3), fmt(m.get("slow_setup_ws"), 3), fmt(m.get("typ_hold_ws"), 3),
                         fmt(m.get("fast_hold_ws"), 3), fmt(m.get("slow_hold_ws"), 3), m.get("max_slew_vio"),
                         m.get("max_cap_vio"), m.get("max_fanout_vio"), fmt(m.get("typ_fmax_mhz"), 1),
                         fmt(m.get("utilization"), 4)))
            L.append("")
            proms = [r for r in promo_rows(state) if r["uid"] == b["uid"]]
            L.append("Promotion: %s." % ("; ".join("%s %s" % (r["pid"], r["verdict"]) for r in proms)
                                         if proms else "not promoted yet"))
            L.append("")
            s, rm = config_diff(b.get("config_changes"), (b.get("snap") or {}).get("floorplan"))
            L.append("Exact difference from the committed src/config.json (keys to set; OPENROAD_THREADS is a "
                     "local-only deviation and is not part of it):")
            L.append("")
            L.append("```json")
            L.append(json.dumps(s, indent=2) if s else "{}")
            L.append("```")
            if rm:
                L.append("")
                L.append("Keys to remove: %s" % ", ".join(rm))
            L.append("")
        else:
            L.append("No legal trial yet.")
            L.append("")
        L.append("### Top 20 legal trials")
        L.append("")
        L.append(HEAD)
        for i, t in enumerate(rk[:20], 1):
            L.append(trial_row(i, t))
        L.append("")

    # ---- promotions
    pr = promo_rows(state)
    L.append("## Promotions (full run + TT precheck + gate-level tests)")
    L.append("")
    if pr:
        L.append("| promotion | trial | reason | full-run jobs | legal | LVS | min WS | typ WS | slow WS | util | "
                 "precheck (jobs) | GL (jobs) | verdict |")
        L.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|")
        for r in pr:
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s (%s) | %s (%s) | %s |" % (
                r["pid"], r["uid"], r["reason"], r["full_jobs"] or "-", fmt(r["legal"]), fmt(r["lvs"]),
                fmt(r["min_ws"], 3), fmt(r["typ_ws"], 3), fmt(r["slow_ws"], 3), fmt(r["util"], 4), r["precheck"],
                r["precheck_jobs"] or "-", r["gl"], r["gl_jobs"] or "-", r["verdict"]))
        passed = [r for r in pr if r["verdict"] == "PASS" and not r["reason"].startswith("control")
                  and r["track"] == primary]
        if passed:
            best = max(passed, key=lambda r: (r["min_ws"] if r["min_ws"] is not None else -1e9))
            s, rm = config_diff(best["config_changes"],
                                (state.trials.get(best["uid"], {}).get("snap") or {}).get("floorplan"))
            L.append("")
            L.append("Best promoted configuration of the design of record with a PASS verdict: %s (trial %s). "
                     "Difference from the committed src/config.json:" % (best["pid"], best["uid"]))
            L.append("")
            L.append("```json")
            L.append(json.dumps(s, indent=2) if s else "{}")
            L.append("```")
            if rm:
                L.append("Keys to remove: %s" % ", ".join(rm))
    else:
        L.append("None yet.")
    L.append("")

    # ---- variant tracks
    var = [t for t in tracks if not t.get("primary")]
    L.append("## Variant cores (second track)")
    L.append("")
    if var:
        L.append("| track | core | trials done | legal | best min WS | best typ/slow WS | util | best trial | configuration |")
        L.append("|---|---|---:|---:|---:|---|---:|---|---|")
        for tr in var:
            ts = [t for t in state.trials_of(tr["track"]) if t["state"] == "done"]
            rk = ranked(state, tr["track"])
            if rk:
                m = rk[0]["metrics"]
                L.append("| %s | %s | %d | %d | %s | %s / %s | %s | %s | %s |" % (
                    tr["track"], tr.get("source") or tr.get("name"), len(ts), sum(1 for t in ts if t.get("legal")),
                    fmt(m.get("min_ws"), 3), fmt(m.get("typ_setup_ws")), fmt(m.get("slow_setup_ws")),
                    fmt(m.get("utilization"), 4), rk[0]["uid"].split("#")[1], knob_summary(rk[0]).replace("|", "/")))
            else:
                L.append("| %s | %s | %d | 0 | - | - | - | - | - |" % (tr["track"], tr.get("source") or tr.get("name"),
                                                                     len(ts)))
    else:
        L.append("No variant track.")
    L.append("")

    # ---- why trials were not legal
    blk = {}
    for t in done:
        if t.get("legal"):
            continue
        for b in (t.get("blockers") or ["unknown"]):
            key = " ".join(w for w in b.split(" (")[0].split()
                           if not w.replace(".", "", 1).replace("-", "", 1).isdigit())
            blk[key] = blk.get(key, 0) + 1
    L.append("## Illegal or failed trials by blocker")
    L.append("")
    if blk:
        L.append("| blocker | trials |")
        L.append("|---|---:|")
        for k, v in sorted(blk.items(), key=lambda kv: -kv[1]):
            L.append("| %s | %d |" % (k, v))
    else:
        L.append("None.")
    L.append("")
    rej = [t for t in state.trials.values() if t["state"] == "rejected"]
    if rej:
        L.append("Rejected before LibreLane (snapshot/floorplan check): %d." % len(rej))
        L.append("")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(L) + "\n")
    os.replace(tmp, path)


def write_all(state, opt_root, jobs=None, stop=None, tree=None, settings=None):
    write_md(state, os.path.join(opt_root, "leaderboard.md"), jobs=jobs, stop=stop, tree=tree, settings=settings)
    write_csv(state, os.path.join(opt_root, "leaderboard.csv"))
    write_manifest(state, os.path.join(opt_root, "manifest.json"), settings or {})


def main():
    opt = os.environ.get("PE_OPT_ROOT") or os.path.join(os.environ["PE_WORK"], "optimizer")
    st = State(Store(os.path.join(opt, "store", "events.jsonl")).events())
    write_all(st, opt)
    print(os.path.join(opt, "leaderboard.md"))


if __name__ == "__main__":
    main()
