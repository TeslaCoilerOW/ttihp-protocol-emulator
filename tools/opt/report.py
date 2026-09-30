#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Leaderboard of the optimizer: <opt root>/leaderboard.md, leaderboard.csv and
manifest.json (job ids), rebuilt from the results store. The driver calls
write_all() after every completed trial; `driver.py report` rebuilds them.

leaderboard.md has one section per track (tracks.py): the committed
configuration's trial, the best legal configuration with its exact difference
from the frozen src/config.json (for the 6x4 track also from the variants6x4
overlay build), the promotion verdicts and the top trials. Everything here
reports what the store records: fast-mode numbers come from trial runs
(OPENROAD_THREADS 32, no LVS), sign-off numbers from promoted full runs
(OPENROAD_THREADS 4, LVS), the TT precheck, the gate-level tests and the
RTL-vs-netlist equivalence check (verdict: gates.promotion_verdict). "Best"
means the best legal trial inside the current search space (space.FIXED); a
better-ranked trial outside it is named, not promoted. In the tracks with the
runtime rule (runtime.py: dor15, dor13, diet4_6x4) trials rank by min WS, then
projected official job time, "best" is the best eligible trial (projection at most
runtime.BOUND_S), and a promotion's verdict is PASS only if its full run's
projection is at most the bound too; every trial and promotion row shows its
projection.
Standard library only.
"""

import csv
import json
import os
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clockdepth as CD  # noqa: E402
import gates as GATES  # noqa: E402
import objective as OBJ  # noqa: E402
import runtime as RT  # noqa: E402
import space as SPACE  # noqa: E402
from store import State, Store  # noqa: E402

MACRO_KEY = "MACROS"
TITLES = {"dor": "Design of record, 8x4, %s", "freq": "Design of record, 8x4, %s",
          "6x4": "6x4 fallback (diet4, variants6x4), %s", "variant": "Variant core %s, 8x4, %s"}


def fmt(x, nd=2):
    if x is None:
        return "-"
    if isinstance(x, float):
        return ("%%.%df" % nd) % x
    return str(x)


def period_label(p):
    p = float(p or 20)
    return "%g ns (%.1f MHz)" % (p, 1000.0 / p)


def title(tr):
    kind = tr.get("kind")
    if kind == "variant":
        return TITLES[kind] % (tr.get("name"), period_label(tr.get("period")))
    if kind in TITLES:
        return TITLES[kind] % period_label(tr.get("period"))
    return "Earlier track %s" % tr["track"]


def is_rule(state, track):
    return (state.tracks.get(track) or {}).get("kind") in RT.RULE_KINDS


def ranked(state, track, legal_only=True, in_space=False, ctx=None):
    """As Driver.ranked: in a rule track (with ctx) min WS, then projected runtime."""
    ts = [t for t in state.trials_of(track) if t["state"] == "done" and not t.get("lost") and t.get("metrics")
          and not t.get("dup_of")]
    if legal_only:
        ts = [t for t in ts if t.get("legal")]
    if in_space:
        ts = [t for t in ts if not SPACE.outside(t.get("knobs"))]
    if ctx is not None and is_rule(state, track):
        return sorted(ts, key=lambda t: RT.rank_key(OBJ.key(t["metrics"], t.get("legal")), ctx.trial(t)["proj_s"]),
                      reverse=True)
    return sorted(ts, key=lambda t: OBJ.key(t["metrics"], t.get("legal")), reverse=True)


def proj_text(r, rule):
    """Leaderboard cell of a projection (runtime.Context.trial/promo result)."""
    if not r or r.get("proj_s") is None:
        return "-"
    h = "%.2f" % (r["proj_s"] / 3600.0)
    if rule:
        return "%s (%s)" % (h, "eligible" if r["eligible"] else "over %.1f h" % (RT.BOUND_S / 3600.0))
    return h


def outside_text(t):
    out = SPACE.outside(t.get("knobs"))
    return ", ".join("%s=%s" % (k, json.dumps(v)) for k, v in sorted(out.items()))


def stands_for(state, t):
    out = {t["uid"]}
    for k in ("dup_of", "imported_from"):
        if t.get(k):
            out.add(t[k])
            src = state.trials.get(t[k]) or {}
            if src.get("imported_from"):
                out.add(src["imported_from"])
    return out


def track_promos(state, track):
    uids = set()
    for t in state.trials_of(track):
        uids |= stands_for(state, t)
    return sorted([p for p in state.promos.values() if p["track"] == track or p["uid"] in uids],
                  key=lambda p: p["pid"])


def knob_summary(t, T=None):
    """What differs from the track's committed configuration."""
    if T is not None and t.get("knobs"):
        try:
            kn = T.complete(SPACE.upgrade({k: v for k, v in t["knobs"].items() if k not in SPACE.RETIRED}))
        except ValueError:
            kn = None
        if kn is not None:
            base = T.complete({})
            parts = ["%s=%s" % (n, json.dumps(kn[n])) for n in SPACE.ORDER
                     if n in kn and (n not in base or not SPACE.same(kn[n], base[n]))]
            parts += ["%s=(inactive)" % n for n in SPACE.ORDER if n in base and n not in kn]
            return "; ".join(parts) or "(committed configuration)"
    parts = []
    snap = t.get("snap") or {}
    for k in ("floorplan", "density", "pl_hold", "grt_hold"):
        if snap.get(k) is not None:
            parts.append("%s=%s" % (k, snap[k]))
    for k, v in sorted((t.get("overrides") or {}).items()):
        parts.append("%s=%s" % (k, json.dumps(v)))
    return "; ".join(parts) or "-"


def diff_block(changes, floorplan=None):
    """{key: {repo, run}} -> (keys to set, keys to remove)."""
    set_, remove = {}, []
    for k, v in sorted((changes or {}).items()):
        if k in ("OPENROAD_THREADS",):
            continue
        if k == MACRO_KEY:
            set_[k] = "<MACROS.RM_IHPSG13_1P_64x16_c2.instances from floorplans/%s.json>" % floorplan
            continue
        if v.get("run") == "<unset>":
            remove.append(k)
        else:
            set_[k] = v.get("run")
    return set_, remove


def promo_rows(state, promos, ctx=None):
    ctx = ctx or RT.Context(state)
    rows = []
    for p in promos:
        st = p["stages"]
        full = st.get("full") or {}
        fr = full.get("result") or {}
        fm = OBJ.derive_fmax(dict(fr.get("metrics") or {}),
                             float((state.tracks.get(p["track"]) or {}).get("period") or 20.0))
        cd = ((st.get("eq") or {}).get("result") or {}).get("clock_depth") or {}
        if fm.get("clk_ff_min") is None and cd.get("ff_min") is not None:
            # full runs processed before postprocess.py computed it: from the equivalence job
            for k in ("ff_min", "ff_max", "sram_min", "sram_max", "sram_excess"):
                fm["clk_" + k] = cd.get(k)
            fm["clk_warn"] = cd.get("warn")
        pc = (st.get("precheck") or {})
        gl = (st.get("gl") or {})
        eq = (st.get("eq") or {})
        pcr, glr = pc.get("result") or {}, gl.get("result") or {}
        ci = None
        if is_rule(state, p["track"]):
            ci = dict(ctx.promo(p), bound_s=RT.BOUND_S)
        verdict, passed = GATES.promotion_verdict(st, ci)
        rows.append({
            "pid": p["pid"], "uid": p["uid"], "reason": p.get("reason"), "run_id": p.get("run_id"),
            "full_jobs": " ".join(s["job_id"] for s in full.get("submits", [])),
            "legal": fr.get("legal"), "lvs": fm.get("lvs_errors"), "min_ws": fm.get("min_ws"),
            "typ_ws": fm.get("typ_setup_ws"), "fast_ws": fm.get("fast_setup_ws"), "slow_ws": fm.get("slow_setup_ws"),
            "util": fm.get("utilization"), "drv": fm.get("drv_vio_sum"),
            "fmax": "/".join(fmt(fm.get("%s_fmax_mhz" % c), 1) for c in OBJ.CORNERS),
            "precheck": ("%s (%d/%d)" % ("PASS" if pcr.get("pass") else "FAIL", pcr.get("n_checks", 0) - pcr.get("n_fail", 0),
                                         pcr.get("n_checks", 0)) if pc.get("state") == "done" else (pc.get("state") or "-")),
            "precheck_jobs": " ".join(s["job_id"] for s in pc.get("submits", [])),
            "gl": ("not run (no reference-model configuration)" if glr.get("not_run") else
                   "%s (%s pass, %s skip, %s fail)" % ("PASS" if glr.get("pass") else "FAIL", glr.get("passed"),
                                                       glr.get("skipped"), glr.get("failed"))
                   if gl.get("state") == "done" else (gl.get("state") or "-")),
            "gl_jobs": " ".join(s["job_id"] for s in gl.get("submits", [])),
            "eq": GATES.eq_text(eq), "eq_jobs": " ".join(s["job_id"] for s in eq.get("submits", [])),
            "clk": CD.text(fm), "verdict": verdict, "passed": passed, "config_changes": p.get("config_changes"),
            "track": p["track"], "outside": outside_text(state.trials.get(p["uid"]) or {}),
            "ci": proj_text(ctx.promo(p), is_rule(state, p["track"])),
        })
    return rows


PROMO_HEAD = ("| promotion | trial | reason | full-run jobs | legal | LVS | min WS | typ / fast / slow WS | "
              "fmax typ/fast/slow MHz | util | clock depth FF / SRAM | precheck (jobs) | GL (jobs) | "
              "equivalence (jobs) | verdict | projected CI job h (full run) |\n"
              "|---|---|---|---|---|---:|---:|---|---|---:|---|---|---|---|---|---|")


def promo_line(r):
    uid = r["uid"] + (" (outside the current space: %s)" % r["outside"] if r.get("outside") else "")
    return "| %s | %s | %s | %s | %s | %s | %s | %s / %s / %s | %s | %s | %s | %s (%s) | %s (%s) | %s (%s) | %s | %s |" % (
        r["pid"], uid, r["reason"], r["full_jobs"] or "-", fmt(r["legal"]), fmt(r["lvs"]), fmt(r["min_ws"], 3),
        fmt(r["typ_ws"], 3), fmt(r["fast_ws"], 3), fmt(r["slow_ws"], 3), r["fmax"], fmt(r["util"], 4), r["clk"],
        r["precheck"], r["precheck_jobs"] or "-", r["gl"], r["gl_jobs"] or "-", r["eq"], r["eq_jobs"] or "-",
        r["verdict"].replace("|", "/"), r.get("ci") or "-")


HEAD = ("| # | trial | legal | min WS ns (corner) | typ WS | fast WS | slow WS | fmax typ/fast/slow MHz | "
        "slow worst start | slew/cap/fanout vio | util | clock depth FF / SRAM | projected CI job h | job | "
        "configuration vs committed |\n"
        "|---:|---|---|---|---:|---:|---:|---|---|---|---:|---|---|---|---|")


def trial_row(i, t, T=None, ci="-"):
    m = t["metrics"]
    job = t.get("done_job") or t.get("job_id") or "-"
    name = t["uid"].split("#")[1]
    if t.get("imported"):
        name += " (= %s)" % t["imported_from"]
    if SPACE.outside(t.get("knobs")):
        name += " (outside the current space)"
    return "| %s | %s | %s | %s (%s) | %s | %s | %s | %s | %s | %s/%s/%s | %s | %s | %s | %s | %s |" % (
        i, name, "yes" if t.get("legal") else "no", fmt(m.get("min_ws")), m.get("min_ws_corner") or "-",
        fmt(m.get("typ_setup_ws")), fmt(m.get("fast_setup_ws")), fmt(m.get("slow_setup_ws")),
        "/".join(fmt(m.get("%s_fmax_mhz" % c), 1) for c in OBJ.CORNERS),
        m.get("slow_worst_start") or "-", fmt(m.get("max_slew_vio")), fmt(m.get("max_cap_vio")),
        fmt(m.get("max_fanout_vio")), fmt(m.get("utilization"), 4), CD.text(m), ci, job,
        knob_summary(t, T).replace("|", "/"))


def best_text(b):
    m = b["metrics"]
    src = (" (imported: the same effective configuration as %s)" % b["imported_from"]) if b.get("imported") else ""
    return ("Trial `%s`%s, label \"%s\", job %s, run `%s`: min setup WS %s ns (%s corner) at %s; setup WS "
            "typ/fast/slow %s/%s/%s ns (register-to-register %s/%s/%s); fmax estimate typ/fast/slow %s/%s/%s MHz; "
            "hold WS typ/fast/slow %s/%s/%s ns; slew/cap/fanout violations %s/%s/%s; utilization %s." % (
                b["uid"], src, b.get("label"), b.get("done_job") or b.get("job_id"), b.get("run_id"),
                fmt(m.get("min_ws"), 3), m.get("min_ws_corner"), period_label(m.get("period_ns")),
                fmt(m.get("typ_setup_ws"), 3), fmt(m.get("fast_setup_ws"), 3), fmt(m.get("slow_setup_ws"), 3),
                fmt(m.get("typ_setup_r2r_ws"), 3), fmt(m.get("fast_setup_r2r_ws"), 3),
                fmt(m.get("slow_setup_r2r_ws"), 3),
                fmt(m.get("typ_fmax_mhz"), 1), fmt(m.get("fast_fmax_mhz"), 1), fmt(m.get("slow_fmax_mhz"), 1),
                fmt(m.get("typ_hold_ws"), 3), fmt(m.get("fast_hold_ws"), 3), fmt(m.get("slow_hold_ws"), 3),
                m.get("max_slew_vio"), m.get("max_cap_vio"), m.get("max_fanout_vio"), fmt(m.get("utilization"), 4)))


def json_block(L, s, rm):
    L.append("```json")
    L.append(json.dumps(s, indent=2) if s else "{}")
    L.append("```")
    if rm:
        L.append("")
        L.append("Keys to remove: %s" % ", ".join(rm))


def track_section(L, state, tr, T, active, ctx=None):
    ctx = ctx or RT.Context(state)
    tid = tr["track"]
    rule = is_rule(state, tid)
    ci = lambda t: proj_text(ctx.trial(t), rule)  # noqa: E731
    ts = state.trials_of(tid)
    done = [t for t in ts if t["state"] == "done"]
    rk = ranked(state, tid, ctx=ctx)
    rk_in = ranked(state, tid, in_space=True, ctx=ctx)
    L.append("## %s" % title(tr))
    L.append("")
    L.append("Track `%s`: core sha256 `%s`, die %s, CLOCK_PERIOD %s ns, weight group %s. Trials: %d new, %d "
             "imported, %d done, %d legal, %d in flight, %d rejected before LibreLane." % (
                 tid, (tr.get("core_sha256") or "")[:12], tr.get("tiles") or "8x4", fmt(tr.get("period")),
                 tr.get("weight") or "-", sum(1 for t in ts if not t.get("imported")),
                 sum(1 for t in ts if t.get("imported")), len(done), sum(1 for t in done if t.get("legal")),
                 sum(1 for t in ts if t["state"] == "submitted"), sum(1 for t in ts if t["state"] == "rejected")))
    if tr.get("retired"):
        L.append("")
        L.append("**Retired** %s: %s" % (tr.get("retired_t", ""), tr["retired"]))
    L.append("")
    if T is not None:
        bkey = SPACE.canonical(T.complete({}))
        base = [t for t in ts if t.get("key") == bkey and t["state"] == "done" and t.get("metrics")]
        base = [t for t in base if not t.get("dup_of")] or base
        if base:
            L.append("Committed configuration%s (fast mode):" % (
                "" if float(tr.get("period") or 20) == 20 else " at CLOCK_PERIOD %g" % float(tr["period"])))
            L.append("")
            L.append(HEAD)
            L.append(trial_row("base", base[0], T, ci(base[0])))
            L.append("")
        else:
            L.append("Committed configuration: no finished trial yet.")
            L.append("")
    L.append("### Best legal configuration")
    L.append("")
    promos = track_promos(state, tid)
    prows = promo_rows(state, promos, ctx)
    if rk and (not rk_in or rk[0]["uid"] != rk_in[0]["uid"]):
        better = rk[:rk.index(rk_in[0])] if rk_in else rk
        L.append("Outside the current search space (`space.FIXED`; not promoted, seeded or transferred), %d "
                 "legal trial(s) rank higher, best `%s` (min WS %s ns; %s)." % (
                     len(better), rk[0]["uid"], fmt(rk[0]["metrics"].get("min_ws"), 3), outside_text(rk[0])))
        L.append("")
    if rule:
        el = [t for t in rk_in if ctx.trial(t)["eligible"]]
        if rk_in and (not el or rk_in[0]["uid"] != el[0]["uid"]):
            over = rk_in[:rk_in.index(el[0])] if el else rk_in
            L.append("Runtime rule (`runtime.py`, projected official gds job at most %.1f h): %d legal trial(s) "
                     "inside the space rank higher but exceed it (not promoted), best `%s` (min WS %s ns; "
                     "projected %s h)." % (RT.BOUND_S / 3600.0, len(over), over[0]["uid"],
                                           fmt(over[0]["metrics"].get("min_ws"), 3),
                                           fmt(RT.hours(ctx.trial(over[0])["proj_s"]), 2)))
            L.append("")
        rk_in = el
    if rk_in:
        b = rk_in[0]
        L.append(best_text(b))
        if rule:
            L.append("")
            r = ctx.trial(b)
            L.append("Projected official gds job (from this trial, `runtime.py`): %s h (%s)." % (
                fmt(RT.hours(r["proj_s"]), 2), "eligible" if r["eligible"] else "not eligible"))
        L.append("")
        mine = [r for r in prows if r["uid"] in stands_for(state, b)]
        L.append("Promotion: %s." % ("; ".join("%s %s" % (r["pid"], r["verdict"]) for r in mine)
                                     if mine else "not promoted"))
        L.append("")
        if T is not None:
            kn = T.complete(SPACE.upgrade({k: v for k, v in b["knobs"].items() if k not in SPACE.RETIRED}))
            s, rm = diff_block(T.diff_vs_repo(kn), kn["floorplan"])
            L.append("Exact difference from the frozen `src/config.json` (keys to set; `OPENROAD_THREADS` is a "
                     "local-only deviation and not part of it):")
            L.append("")
            json_block(L, s, rm)
            notes = []
            if float(tr.get("period") or 20) != 20:
                notes.append("`info.yaml` `clock_hz: %d` (tt-support-tools only checks that it is an integer; "
                             "the flow's clock is `CLOCK_PERIOD`)" % int(round(1e9 / float(tr["period"]))))
            if tr.get("tiles") and tr["tiles"] != "8x4":
                notes.append("`info.yaml` `tiles: \"%s\"` (`variants6x4/info.overlay.json`) and the core "
                             "`variants6x4/protocol_emulator_core.v` (`variants6x4/switch.py apply`)" % tr["tiles"])
            if notes:
                L.append("")
                L.append("Also: %s." % "; ".join(notes))
            if T.overlay:
                s2, rm2 = diff_block(T.diff_vs_base(kn), kn["floorplan"])
                L.append("")
                L.append("Difference from the committed 6x4 build (`src/config.json` with "
                         "`variants6x4/config.overlay.json` merged, as `variants6x4/switch.py apply` writes it). "
                         "These keys go into the overlay (which states every knob key); a key that `src/config.json` lacks "
                         "must also be listed in `variants6x4/PROVENANCE.json` `overlay_new_keys` unless its value "
                         "is `null`, and a key to remove becomes `null`:")
                L.append("")
                json_block(L, s2, rm2)
            L.append("")
    else:
        L.append("No legal trial inside the current search space yet.")
        L.append("")
    L.append("### Promotions of this track")
    L.append("")
    if prows:
        L.append(PROMO_HEAD)
        for r in prows:
            L.append(promo_line(r))
    else:
        L.append("None.")
    L.append("")
    L.append("### Top 10 legal trials")
    L.append("")
    if rk:
        L.append(HEAD)
        for i, t in enumerate(rk[:10], 1):
            L.append(trial_row(i, t, T, ci(t)))
    else:
        L.append("None.")
    L.append("")


def write_csv(state, path):
    rows = []
    for t in sorted(state.trials.values(), key=lambda t: (t["track"], t["number"])):
        m = t.get("metrics") or {}
        r = {"uid": t["uid"], "track": t["track"], "number": t["number"], "label": t.get("label"),
             "state": t["state"], "imported_from": t.get("imported_from") or "", "legal": t.get("legal"),
             "value": t.get("value"), "blockers": "; ".join(t.get("blockers") or []), "dup_of": t.get("dup_of") or "",
             "job_ids": " ".join(s["job_id"] for s in t.get("submits", [])) or (t.get("done_job") or ""),
             "run_id": t.get("run_id"), "knobs": json.dumps(t.get("knobs"), sort_keys=True),
             "overrides": json.dumps(t.get("overrides"), sort_keys=True),
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
                            "tree": (d.get("tree") or {}).get("commit"),
                            "tools": (d.get("tree") or {}).get("tools_sha8"), "dry_run": d.get("dry_run", False)}
                           for d in state.drivers],
           "successor_driver_jobs": [n.get("job_id") for n in state.notes if n.get("job_id")],
           "tracks": [{"track": tr["track"], "kind": tr.get("kind") or "legacy", "retired": tr.get("retired")}
                      for tr in state.tracks.values()],
           "trials": [{"uid": t["uid"], "run_id": t.get("run_id"), "state": t["state"],
                       "imported_from": t.get("imported_from"),
                       "job_ids": [s["job_id"] for s in t.get("submits", [])]}
                      for t in sorted(state.trials.values(), key=lambda t: (t["track"], t["number"]))],
           "promotions": [{"pid": p["pid"], "uid": p["uid"], "track": p["track"], "run_id": p.get("run_id"),
                           "jobs": {k: [s["job_id"] for s in v.get("submits", [])] for k, v in p["stages"].items()}}
                          for p in sorted(state.promos.values(), key=lambda p: p["pid"])]}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(man, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def summary_row(state, tr, weight, ctx=None):
    ctx = ctx or RT.Context(state)
    tid = tr["track"]
    ts = state.trials_of(tid)
    done = [t for t in ts if t["state"] == "done"]
    rk = ranked(state, tid, in_space=True, ctx=ctx)
    m = rk[0]["metrics"] if rk else {}
    if is_rule(state, tid):
        el = [t for t in rk if ctx.trial(t)["eligible"]]
        best_el = ("%s (%s h)" % (fmt(el[0]["metrics"].get("min_ws"), 3), fmt(RT.hours(ctx.trial(el[0])["proj_s"]), 2))
                   if el else "none")
    else:
        best_el = "(no rule)"
    return "| `%s` | %s | %s | %s | %s | %d | %d | %d | %d | %s (%s) | %s | %s / %s / %s | %s | %s | %d | %s |" % (
        tid, tr.get("kind") or "legacy", tr.get("tiles") or "8x4", fmt(tr.get("period")), weight,
        sum(1 for t in ts if not t.get("imported")), sum(1 for t in ts if t.get("imported")),
        sum(1 for t in done if t.get("legal")), sum(1 for t in ts if t["state"] == "submitted"),
        fmt(m.get("min_ws"), 3), m.get("min_ws_corner") or "-", best_el, fmt(m.get("typ_setup_ws")),
        fmt(m.get("fast_setup_ws")),
        fmt(m.get("slow_setup_ws")), "/".join(fmt(m.get("%s_fmax_mhz" % c), 1) for c in OBJ.CORNERS) if m else "-",
        fmt(m.get("utilization"), 4), len(track_promos(state, tid)),
        ("retired" if tr.get("retired") else "active"))


def write_md(state, path, jobs=None, stop=None, tree=None, settings=None, tracks=None, active=None):
    settings = settings or {}
    tracks = tracks or {}
    active = [a for a in (active or []) if a in state.tracks]
    L = []
    now = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    done = [t for t in state.trials.values() if t["state"] == "done" and not t.get("imported")]
    inflight = state.in_flight_trials()
    mine = {k: v for k, v in (jobs or {}).items() if v["name"].startswith(settings.get("job_prefix", "pe-v2-optimizer-"))}
    W = settings.get("weights") or {}
    ctx = RT.Context(state)
    L.append("# Physical-design optimizer: leaderboard")
    L.append("")
    L.append("Generated %s by tools/opt/report.py from the results store; see docs/notes/optimization.md. "
             "Trial numbers are fast-mode runs (post-route STA at all three corners at the track's CLOCK_PERIOD, "
             "antenna, power-port checks; no LVS; OPENROAD_THREADS 32). Sign-off numbers are promoted full runs "
             "(OPENROAD_THREADS 4, LVS), the TT precheck, the gate-level tests and the RTL-vs-netlist equivalence "
             "check; a promotion's verdict is PASS only if all four pass. Best trials are the best legal ones "
             "inside the current search space (docs/notes/optimization.md, \"Search space\"). fmax estimates are "
             "1000 / (period - setup WS) per corner (docs/notes/optimization.md, \"Frequency tracks and the SDC\"). "
             "An imported trial is a finished run of an earlier track whose effective configuration is identical; "
             "it keeps that run's job id. \"Projected CI job h\" is the projected length of the official gds job "
             "(GitHub stops it at 6 h) from the run's step times (docs/notes/optimization.md, \"Runtime and the 6-hour "
             "limit\"); in the dor15, dor13 and diet4_6x4 tracks a trial is promoted only if its projection is at "
             "most %.1f h, trials rank by min WS, then projection, and a promotion's verdict is PASS only if its "
             "full run's projection is within that bound too." % (now, RT.BOUND_S / 3600.0))
    L.append("")
    L.append("| Item | Value |")
    L.append("|---|---|")
    L.append("| Frozen tree | commit %s, tools %s |" % ((tree or {}).get("commit", "-"), (tree or {}).get("tools_sha8", "-")))
    L.append("| LibreLane runs submitted (since the first launch) | %d of %s |" % (
        state.librelane_runs(), settings.get("budget_runs", "-")))
    L.append("| Trials run: done / legal / in flight | %d / %d / %d |" % (
        len(done), sum(1 for t in done if t.get("legal")), len(inflight)))
    L.append("| Jobs now (pe-v2-optimizer-*) | %d, %d CPUs (%d running) of cap %s |" % (
        len(mine), sum(v["cpus"] for v in mine.values()), sum(1 for v in mine.values() if v["state"] == "RUNNING"),
        settings.get("cpu_cap", "-")))
    L.append("| Weights of new trials | %s |" % ", ".join("%s %s" % kv for kv in W.items()))
    L.append("| Stop condition | %s (date limit %s; STOP file) |" % (stop or "none reached", settings.get("stop_at", "-")))
    L.append("")
    L.append("## Tracks")
    L.append("")
    L.append("| track | kind | die | period ns | weight | new trials | imported | legal | in flight | "
             "best min WS in space (corner) | best eligible min WS (projected CI job h) | best typ / fast / slow WS | "
             "fmax typ/fast/slow MHz | util | promotions | state |")
    L.append("|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---:|---:|---|")
    nvar = sum(1 for a in active if state.tracks[a].get("kind") == "variant" and not state.tracks[a].get("retired"))
    for a in active:
        tr = state.tracks[a]
        if tr.get("kind") == "variant":
            w = "%.3f" % (W.get("variant", 0) / max(nvar, 1)) if not tr.get("retired") else "0"
        else:
            w = str(W.get(tr.get("weight"), "-"))
        L.append(summary_row(state, tr, w, ctx))
    L.append("")
    order = [a for a in active if not state.tracks[a].get("retired")] + \
            [a for a in active if state.tracks[a].get("retired")]
    for a in order:
        track_section(L, state, state.tracks[a], tracks.get(a), active, ctx)

    # ---- earlier tracks
    old = [tr for tid, tr in state.tracks.items() if tid not in active]
    L.append("## Earlier tracks (no longer receiving trials)")
    L.append("")
    if old:
        L.append("Tracks of earlier frozen trees or tools. Their finished trials whose effective configuration "
                 "equals a current track's knob set are imported there (column \"imported\").")
        L.append("")
        L.append("| track | trials | done | legal | in flight | best min WS | imported into current tracks |")
        L.append("|---|---:|---:|---:|---:|---:|---:|")
        imp = {}
        for t in state.trials.values():
            if t.get("imported"):
                src = state.trials.get(t["imported_from"]) or {}
                imp[src.get("track")] = imp.get(src.get("track"), 0) + 1
        for tr in sorted(old, key=lambda tr: tr["track"]):
            ts = state.trials_of(tr["track"])
            rk = ranked(state, tr["track"])
            L.append("| `%s` | %d | %d | %d | %d | %s | %d |" % (
                tr["track"], len(ts), sum(1 for t in ts if t["state"] == "done"),
                sum(1 for t in ts if t["state"] == "done" and t.get("legal")),
                sum(1 for t in ts if t["state"] == "submitted"),
                fmt(rk[0]["metrics"].get("min_ws"), 3) if rk else "-", imp.get(tr["track"], 0)))
        L.append("")
        shown = set()
        for a in active:
            shown |= {p["pid"] for p in track_promos(state, a)}
        oldp = [p for p in state.promos.values() if p["track"] not in active and p["pid"] not in shown]
        if oldp:
            L.append("Promotions of earlier tracks:")
            L.append("")
            L.append(PROMO_HEAD)
            for r in promo_rows(state, sorted(oldp, key=lambda p: p["pid"]), ctx):
                L.append(promo_line(r))
            L.append("")
    else:
        L.append("None.")
        L.append("")

    # ---- why trials were not legal (current tracks)
    blk = {}
    for t in state.trials.values():
        if t["track"] not in active or t["state"] != "done" or t.get("legal") or t.get("imported"):
            continue
        for b in (t.get("blockers") or ["unknown"]):
            key = " ".join(w for w in b.split(" (")[0].split()
                           if not w.replace(".", "", 1).replace("-", "", 1).isdigit())
            blk[key] = blk.get(key, 0) + 1
    L.append("## Illegal or failed trials of the current tracks by blocker")
    L.append("")
    if blk:
        L.append("| blocker | trials |")
        L.append("|---|---:|")
        for k, v in sorted(blk.items(), key=lambda kv: -kv[1]):
            L.append("| %s | %d |" % (k, v))
    else:
        L.append("None.")
    L.append("")
    rej = [t for t in state.trials.values() if t["state"] == "rejected" and t["track"] in active]
    if rej:
        L.append("Rejected before LibreLane (snapshot, floorplan or island check): %d." % len(rej))
        L.append("")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(L) + "\n")
    os.replace(tmp, path)


def write_all(state, opt_root, jobs=None, stop=None, tree=None, settings=None, tracks=None, active=None):
    write_md(state, os.path.join(opt_root, "leaderboard.md"), jobs=jobs, stop=stop, tree=tree, settings=settings,
             tracks=tracks, active=active)
    write_csv(state, os.path.join(opt_root, "leaderboard.csv"))
    write_manifest(state, os.path.join(opt_root, "manifest.json"), settings or {})


def main():
    opt = os.environ.get("PE_OPT_ROOT") or os.path.join(os.environ["PE_WORK"], "optimizer")
    st = State(Store(os.path.join(opt, "store", "events.jsonl")).events())
    write_all(st, opt)
    print(os.path.join(opt, "leaderboard.md"))


if __name__ == "__main__":
    main()
