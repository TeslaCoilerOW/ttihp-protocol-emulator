# SPDX-License-Identifier: Apache-2.0
"""Objective of the physical-design optimizer (docs/optimization.md, "Objective").

Lexicographic, in this order:
  1. legal: the flow completed with exit 0 and post-route STA; route DRC 0;
     antenna 0; no critical disconnected pins; no setup violation at the typ
     corner (TIMING_VIOLATION_CORNERS, the gds action fails otherwise); no hold
     violation at any corner; no power-port violation (short power straps) in
     the PDN DEF or the final LEF (checks.py); in full mode also LVS 0;
  2. the minimum setup worst slack over the typ, fast and slow corners (higher
     is better; compared at 10 ps resolution so that tool noise does not decide);
  3. the sum of the max slew, max cap and max fan-out violation counts (lower);
  4. the typ-corner fmax estimate 1000 / (period - typ WS) (higher);
  5. the utilization (lower).

value() folds this into the single number TPE maximizes. Standard library only.
"""

import math

CORNERS = ("typ", "fast", "slow")
FAIL_VALUE = -60.0          # no post-route STA (flow failed, timed out or never ran)
ILLEGAL_OFFSET = 15.0       # illegal but timed: below every plausible legal result


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def flatten(res, post=None, period=20.0):
    """result.json (+ opt_post.json) -> flat metric dict."""
    res = res or {}
    post = post or {}
    sta = res.get("sta") or {}
    m = {
        "status": res.get("status"), "exit_code": res.get("exit_code"), "wall_s": res.get("wall_s"),
        "flow_runtime_s": res.get("flow_runtime_s"), "last_step": res.get("last_step"),
        "sta_source": res.get("sta_source"), "flow_complete": res.get("flow_complete"),
        "mode": res.get("mode"), "utilization": _f(res.get("utilization")),
        "utilization_stdcell": _f(res.get("utilization_stdcell")),
        "stdcell_area": _f(res.get("stdcell_area")), "instance_count": res.get("instance_count"),
        "hold_buffers": res.get("hold_buffers"), "setup_buffers": res.get("setup_buffers"),
        "route_drc": res.get("route_drc_errors"), "antenna": res.get("antenna_violations"),
        "disconnected_pins": res.get("disconnected_pins_critical"), "lvs_errors": res.get("lvs_errors"),
        "illegal_overlaps": res.get("illegal_overlaps"),
        "max_slew_vio": res.get("max_slew_violations"), "max_cap_vio": res.get("max_cap_violations"),
        "max_fanout_vio": res.get("max_fanout_violations"), "wirelength": res.get("route_wirelength"),
        "power_w": _f(res.get("power_total_w")),
        "grt_overflow": ((res.get("grt") or {}).get("total") or {}).get("overflow"),
        "drt_iters": (res.get("drt") or {}).get("main_iterations"),
        "drt_iter0": (res.get("drt") or {}).get("iter0_violations"),
        "drt0418": res.get("drt0418_pins"),
        "job_id": res.get("job_id"), "node": res.get("node"), "partition": res.get("partition"),
    }
    for c in CORNERS:
        e = sta.get(c) or {}
        for k in ("setup_ws", "setup_tns", "setup_vio", "setup_r2r_ws", "hold_ws", "hold_vio"):
            m["%s_%s" % (c, k)] = e.get(k)
    ws = [(_f(m["%s_setup_ws" % c]), c) for c in CORNERS if _f(m["%s_setup_ws" % c]) is not None]
    if ws and len(ws) == len(CORNERS):
        m["min_ws"], m["min_ws_corner"] = min(ws)
    else:
        m["min_ws"], m["min_ws_corner"] = None, None
    tw = _f(m["typ_setup_ws"])
    m["typ_fmax_mhz"] = round(1000.0 / (period - tw), 3) if tw is not None and period - tw > 0 else None
    vio = [m["max_slew_vio"], m["max_cap_vio"], m["max_fanout_vio"]]
    m["drv_vio_sum"] = int(sum(int(v) for v in vio)) if all(v is not None for v in vio) else None
    lef = post.get("lef") or {}
    pdn = post.get("pdn") or {}
    m["lef_port_vio"] = (lef.get("port_violations", 0) + lef.get("wrong_layer_ports", 0)
                         + len(lef.get("missing_nets") or [])) if lef else None
    m["pdn_short_stripes"] = (pdn.get("short_stripes", 0) + pdn.get("port_violations", 0)) if pdn else None
    for c in CORNERS:
        m["%s_worst_start" % c] = ((post.get("worst_paths") or {}).get(c) or {}).get("start")
    m["slow_worst_end"] = ((post.get("worst_paths") or {}).get("slow") or {}).get("end")
    cts = (post.get("resizer") or {}).get("resizertimingpostcts")
    m["rsz_postcts_setup_found"] = (None if cts is None else
                                    not any("RSZ-0098" in l for l in cts))  # RSZ-0098: no setup violations found
    return m


def _zero(v):
    try:
        return v is not None and float(v) == 0
    except (TypeError, ValueError):
        return False


def legality(m, mode="fast"):
    """-> (legal, blockers, magnitude). magnitude grows with the number of violations."""
    b = []
    mag = 0.0
    if not m.get("flow_complete") or m.get("sta_source") != "post-route":
        return False, ["no post-route STA (status %s, last step %s)" % (m.get("status"), m.get("last_step"))], 1e4
    if m.get("status") != "complete" or m.get("exit_code") not in (0, "0"):
        b.append("LibreLane exit %s (%s)" % (m.get("exit_code"), m.get("status")))
        mag += 1
    for k, label in (("route_drc", "route DRC"), ("antenna", "antenna"), ("disconnected_pins", "disconnected pins"),
                     ("typ_setup_vio", "typ setup violations")):
        if not _zero(m.get(k)):
            b.append("%s %s" % (label, m.get(k)))
            mag += float(m.get(k) or 1)
    for c in CORNERS:
        if not _zero(m.get("%s_hold_vio" % c)):
            b.append("%s hold violations %s" % (c, m.get("%s_hold_vio" % c)))
            mag += float(m.get("%s_hold_vio" % c) or 1)
    if m.get("pdn_short_stripes") not in (None, 0):
        b.append("PDN short power straps/ports %s" % m["pdn_short_stripes"])
        mag += 10 * m["pdn_short_stripes"]
    if m.get("lef_port_vio") is None:
        b.append("LEF power-port check did not run")
        mag += 1
    elif m["lef_port_vio"]:
        b.append("LEF power-port violations %s" % m["lef_port_vio"])
        mag += 10 * m["lef_port_vio"]
    if mode == "full" and not _zero(m.get("lvs_errors")):
        b.append("LVS errors %s" % m.get("lvs_errors"))
        mag += float(m.get("lvs_errors") or 1)
    return not b, b, mag


def key(m, legal):
    """Sort key (larger = better) implementing the lexicographic objective."""
    ws = m.get("min_ws")
    return (1 if legal else 0,
            round(ws, 2) if ws is not None else -1e9,
            -(m.get("drv_vio_sum") if m.get("drv_vio_sum") is not None else 1e9),
            m.get("typ_fmax_mhz") or 0.0,
            -(m.get("utilization") if m.get("utilization") is not None else 1e9))


def value(m, legal, magnitude):
    """Scalar for TPE (maximize): min WS in ns, tiny tie-breaks, and a penalty below
    every legal value for an illegal result."""
    ws = m.get("min_ws")
    if ws is None or m.get("sta_source") != "post-route":
        return FAIL_VALUE
    v = ws
    if m.get("drv_vio_sum") is not None:
        v -= 1e-5 * m["drv_vio_sum"]
    if m.get("utilization") is not None:
        v -= 1e-3 * m["utilization"]
    if not legal:
        v -= ILLEGAL_OFFSET + 2.0 * math.log10(1.0 + magnitude)
    return max(v, FAIL_VALUE + 1.0)
