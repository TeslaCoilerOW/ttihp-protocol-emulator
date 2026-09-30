# SPDX-License-Identifier: Apache-2.0
"""Runtime of the official gds job (docs/notes/optimization.md, "Runtime and the 6-hour limit").

GitHub's hosted runner stops a job after 6 h (21,600 s). The gds job of the Tiny
Tapeout workflow is one such job: runner setup, the LibreLane flow on 4 vCPUs,
then the summaries, the artifacts and the PNG render. This module projects its
length from a local run and decides whether a configuration of the 13.33 ns and
15 ns tracks and of the 6x4 track (whose gds_6x4 job has the same limit) may be
promoted.

Speed factor. Local nodes and GitHub runners differ in single-thread speed. A
run's yosys synthesis step is single-threaded and does the same work for the
same synthesis settings and core, so

    s = synthesis time / fastest synthesis time of that synthesis key

measures the machine (s = 1: the fastest local node class). The key is taken from
the run's own out/resolved.json when it was recorded (synth_cfg), else from the
knob set. Normalized time =
time / s. Over the 18 official jobs paired with a local full run of the same
configuration, the runners had s = 0.908 to 0.974 (3 jobs) and 1.401 to 1.517
(15 jobs); normalized, the official flow took 1.189 to 1.333 times the local
full run's (raw ratio 1.044 to 1.913).

Models (constants: maxima over those 18 jobs, rounded up; slow runner):

  full run (OPENROAD_THREADS 4):
      CI job <= OVERHEAD_S + S_CI * C_FULL * flow / s
  fast-mode trial (OPENROAD_THREADS 32; post-CTS and post-GRT repair = "repair"):
      CI job <= OVERHEAD_S + S_CI * (K_RSZ * repair / s + K_REST * (flow - repair) / s)

Both over-predict every one of the 18 official jobs (docs/notes/optimization.md). A
projection needs a flow that ran to its end (flow_complete); a timed-out or
failed run has none.
Eligibility (tracks of the kinds in RULE_KINDS: dor15, dor13, diet4_6x4): the trial
projection must be at most BOUND_S (5.5 h); a trial without a projection is not
eligible. The promotion verdict of those tracks also requires the full run's
projection to be at most BOUND_S (gates.promotion_verdict).

Standard library only.
"""

import json

LIMIT_S = 21600          # GitHub-hosted runner: a job is stopped after 6 h
BOUND_S = 19800          # 5.5 h: the projection a promotion of a rule track must not exceed
OVERHEAD_S = 1370        # max over the 18 jobs of (flow start - job start) + (job end - flow end): 1,367 s
S_CI = 1.52              # max runner speed factor: 1.517 (gds_6x4 job 108525489501)
C_FULL = 1.34            # max normalized official flow / normalized local full-run flow: 1.333
K_RSZ = 1.65             # normalized official repair / normalized trial repair: 1.556 measured (p018, 3
#                          jobs, the only configuration with a long repair); p021's full run over its trial
#                          (1.197) times p018's official over full run (1.328) gives 1.589; rounded up
K_REST = 3.74            # max normalized official rest / normalized trial rest: 3.738 (p001, 4 configurations)
RULE_KINDS = ("freq", "6x4")   # track kinds with the rule: dor15, dor13 (gds) and diet4_6x4 (gds_6x4)
PENALTY_NS = 1.0         # TPE value of an ineligible trial: minus 1 ns, minus 1 ns per hour over BOUND_S
# ABC scripts that read CLOCK_PERIOD (space.py, SYNTH_STRATEGY); for the others the period is
# not part of the synthesis key
PERIOD_STRATEGIES = ("AREA 0", "AREA 1", "AREA 2", "DELAY 0", "DELAY 1", "DELAY 2", "DELAY 3")
FIELDS = ("synth_s", "rsz_s", "rsz_grt_s", "drt_s", "flow_s", "wall_s", "node", "threads", "mode", "complete",
          "synth_cfg")
# out/resolved.json keys that decide the synthesis work (the synthesis key)
SYNTH_CFG_KEYS = ("SYNTH_STRATEGY", "SYNTH_ABC_BUFFERING", "SYNTH_SIZING", "MAX_FANOUT_CONSTRAINT", "CLOCK_PERIOD")


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def extract(res, resolved=None):
    """result.json (scripts/sweep/extract_result.py) and out/resolved.json -> runtime record:
    synthesis, post-CTS and post-GRT repair, detailed routing and flow seconds, wall seconds, node,
    threads, mode, whether the flow ran to its end (result.json flow_complete) and the resolved
    synthesis settings (None without resolved.json)."""
    res = res or {}
    ts = res.get("time_s") or {}
    grt = [_f(s.get("runtime_s")) for s in (res.get("steps") or [])
           if str(s.get("step") or "").endswith("resizertimingpostgrt")]
    return {"synth_s": _f(ts.get("synthesis")), "rsz_s": _f(ts.get("resizer_post_cts")),
            "rsz_grt_s": sum(g for g in grt if g) if grt else 0.0, "drt_s": _f(ts.get("detailed_routing")),
            "flow_s": _f(res.get("flow_runtime_s")), "wall_s": _f(res.get("wall_s")), "node": res.get("node"),
            "threads": res.get("threads"), "mode": res.get("mode"), "complete": bool(res.get("flow_complete")),
            "synth_cfg": {k: resolved.get(k) for k in SYNTH_CFG_KEYS} if resolved else None}


def from_metrics(m):
    """The runtime record inside a flat metric dict (objective.flatten), or None if it has none."""
    if not m or "rt_synth_s" not in m:
        return None
    return {k: m.get("rt_" + k) for k in FIELDS}


def _int(x):
    try:
        f = float(x)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return x


def synth_key(knobs, period, core_sha, synth_cfg=None):
    """Synthesis settings that decide the work of the yosys step (a string): from the run's
    resolved configuration (synth_cfg) when given, else from its knob set and track period."""
    if synth_cfg:
        strat = synth_cfg.get("SYNTH_STRATEGY")
        abc = ("buffering" if synth_cfg.get("SYNTH_ABC_BUFFERING") else
               "sizing" if synth_cfg.get("SYNTH_SIZING") else "none")
        fanout = synth_cfg.get("MAX_FANOUT_CONSTRAINT")
        period = synth_cfg.get("CLOCK_PERIOD", period)
    else:
        kn = knobs or {}
        strat, abc, fanout = kn.get("SYNTH_STRATEGY"), kn.get("abc_fine_tune"), kn.get("MAX_FANOUT_CONSTRAINT")
    key = [strat, abc, _int(fanout), (core_sha or "")[:16]]
    if strat in PERIOD_STRATEGIES:
        key.append(round(float(period), 4))
    return json.dumps(key)


class Speed(object):
    """Fastest synthesis time per synthesis key over a set of runs."""

    def __init__(self, samples=()):
        self.ref = {}
        for key, synth in samples:
            self.add(key, synth)

    def add(self, key, synth):
        synth = _f(synth)
        if key is not None and synth and synth > 0:
            self.ref[key] = min(self.ref.get(key, synth), synth)

    def factor(self, key, synth):
        """s >= 1 (1 when the key or the synthesis time is unknown: no normalization)."""
        synth = _f(synth)
        ref = self.ref.get(key)
        if not synth or not ref:
            return 1.0
        return max(1.0, synth / ref)


def project_trial(rt, s):
    """Projected official gds job seconds from a fast-mode trial (None without the data or
    when the flow did not run to its end)."""
    if not rt or rt.get("complete") is not True or rt.get("rsz_s") is None or rt.get("flow_s") is None:
        return None
    rep = rt["rsz_s"] + (rt.get("rsz_grt_s") or 0.0)
    rest = max(rt["flow_s"] - rep, 0.0)
    return OVERHEAD_S + S_CI * (K_RSZ * rep / s + K_REST * rest / s)


def project_full(rt, s):
    """Projected official gds job seconds from a full-mode run (None without the data or when
    the flow did not run to its end)."""
    if not rt or rt.get("complete") is not True or rt.get("flow_s") is None:
        return None
    return OVERHEAD_S + S_CI * C_FULL * rt["flow_s"] / s


def eligible(proj_s):
    return proj_s is not None and proj_s <= BOUND_S


def penalized_value(value, proj_s):
    """TPE value of a trial of a rule track: unchanged when eligible; otherwise minus
    PENALTY_NS and 1 ns per hour of projection over BOUND_S (no projection: minus PENALTY_NS)."""
    if value is None or eligible(proj_s):
        return value
    over = 0.0 if proj_s is None else max(proj_s - BOUND_S, 0.0) / 3600.0
    return value - PENALTY_NS - over


SLOWEST_LOCAL_S = 2.15   # the slowest local node observed: node1384 (p024's full run, synthesis 121.77 s
#                          against 56.638 s); a slower node would need longer


def trial_time_limit_s():
    """Longest flow (seconds) of an eligible trial on a node with speed factor SLOWEST_LOCAL_S
    (the maximum observed): all repair, no rest."""
    return (BOUND_S - OVERHEAD_S) / S_CI / K_RSZ * SLOWEST_LOCAL_S


class Context(object):
    """Speed factors and projections for the trials and promotions of a results store (State)."""

    def __init__(self, state):
        self.state = state
        self.speed = Speed()
        for t in state.trials.values():
            rt = self.trial_runtime(t)
            if rt:
                self.speed.add(self.key(t), rt.get("synth_s"))
        for p in state.promos.values():
            rt = self.promo_runtime(p)
            t = state.trials.get(p["uid"])
            if rt and t:
                self.speed.add(self.key(t, rt), rt.get("synth_s"))

    def key(self, t, rt=None):
        """Synthesis key of trial t's run (or of the run whose runtime record rt is given)."""
        tr = self.state.tracks.get(t.get("track")) or {}
        rt = rt if rt is not None else self.trial_runtime(t)
        return synth_key(t.get("knobs"), tr.get("period") or 20.0, tr.get("core_sha256"),
                         (rt or {}).get("synth_cfg"))

    @staticmethod
    def trial_runtime(t):
        return from_metrics(t.get("metrics")) or t.get("runtime") or None

    @staticmethod
    def promo_runtime(p):
        full = (p.get("stages") or {}).get("full") or {}
        m = (full.get("result") or {}).get("metrics")
        return from_metrics(m) or p.get("runtime") or None

    def trial(self, t):
        """{s, proj_s, eligible, repair_n, rest_n} of a trial (proj_s None without the data)."""
        rt = self.trial_runtime(t)
        s = self.speed.factor(self.key(t), (rt or {}).get("synth_s"))
        proj = project_trial(rt, s)
        out = {"s": s, "proj_s": proj, "eligible": eligible(proj)}
        if proj is not None:
            rep = rt["rsz_s"] + (rt.get("rsz_grt_s") or 0.0)
            out["repair_n"] = rep / s
            out["rest_n"] = max(rt["flow_s"] - rep, 0.0) / s
        return out

    def promo(self, p):
        """{s, proj_s, eligible} of a promotion's full run (proj_s None before it finished)."""
        rt = self.promo_runtime(p)
        t = self.state.trials.get(p["uid"]) or {}
        s = self.speed.factor(self.key(t, rt or {}), (rt or {}).get("synth_s")) if t else 1.0
        proj = project_full(rt, s)
        return {"s": s, "proj_s": proj, "eligible": eligible(proj)}

    def promo_ok(self, p):
        """Runtime verdict of a promotion: the full run's projection when it has one, else its
        trial's; None when neither exists."""
        r = self.promo(p)
        if r["proj_s"] is not None:
            return r["eligible"]
        t = self.state.trials.get(p["uid"])
        if t is None:
            return None
        r = self.trial(t)
        return r["eligible"] if r["proj_s"] is not None else None


def rank_key(base_key, proj_s):
    """objective.key with the projection (lower first) inserted after the min WS: rule tracks
    rank by min WS (10 ps), then projected official runtime."""
    b = tuple(base_key)
    return b[:2] + (-(proj_s if proj_s is not None else 1e12),) + b[2:]


def hours(x):
    return None if x is None else x / 3600.0
