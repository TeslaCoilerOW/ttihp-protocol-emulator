#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Summarise one LibreLane run directory (runs/wokwi) as a result JSON.

Works on finished, failed, killed and still-running runs: metrics come from
final/metrics.json when the flow loop completed, otherwise from the newest
step state_out.json. Logs supply what the metrics lack (GRT congestion table,
DRT iteration history, GPL density). Standard library only; Python 3.6+.

Usage:
  extract_result.py --run-dir RUN/runs/wokwi [--params params.json] [--exit-code N]
                    [--status S] [--wall-s T] [--out result.json]
"""

import argparse
import glob
import json
import os
import re
import socket
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

STEP_RE = re.compile(r"^(\d+)-(.+)$")


def step_dirs(run_dir):
    out = []
    for d in os.listdir(run_dir):
        m = STEP_RE.match(d)
        if m and os.path.isdir(os.path.join(run_dir, d)):
            out.append((int(m.group(1)), m.group(2), os.path.join(run_dir, d)))
    out.sort()
    return out


def hms_to_s(s):
    m = re.match(r"^\s*(?:(\d+)-)?(\d+):(\d+):(\d+(?:\.\d+)?)\s*$", s or "")
    if not m:
        return None
    d = int(m.group(1) or 0)
    return d * 86400 + int(m.group(2)) * 3600 + int(m.group(3)) * 60 + float(m.group(4))


def read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except (IOError, OSError):
        return None


def last_step(steps, name_re):
    hits = [s for s in steps if re.match(name_re, s[1])]
    return hits[-1] if hits else None


def first_step(steps, name_re):
    hits = [s for s in steps if re.match(name_re, s[1])]
    return hits[0] if hits else None


def step_log(step):
    logs = sorted(glob.glob(os.path.join(step[2], "*.log")))
    return read(logs[0]) if logs else None


def parse_grt(text):
    """Final congestion report of OpenROAD GRT -> {layer: {usage_pct, overflow}}, total."""
    if not text:
        return None
    i = text.rfind("Final congestion report")
    if i < 0:
        return None
    res = {"layers": {}}
    for line in text[i:].splitlines()[1:40]:
        m = re.match(r"^\s*(\S+)\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", line)
        if m:
            name = m.group(1)
            ent = {"resource": int(m.group(2)), "demand": int(m.group(3)), "usage_pct": float(m.group(4)),
                   "overflow": int(m.group(7))}
            if name == "Total":
                res["total"] = ent
                break
            res["layers"][name] = ent
    wl = re.search(r"GRT-0018\] Total wirelength: (\d+)", text)
    if wl:
        res["wirelength_um"] = int(wl.group(1))
    res["congested"] = "GRT-0115" in text or "finished with congestion" in text
    return res


def parse_gpl(text):
    if not text:
        return None
    res = {}
    t = re.findall(r"Placement target density:\s*([\d.]+)", text)
    if t:
        res["target_density"] = float(t[-1])
    t = re.findall(r"Minimum Feasible Density\s+([\d.]+)", text)
    if t:
        res["min_feasible_density"] = float(t[-1])
    t = re.findall(r"GPL-0063\] New Target Density:\s*([\d.]+)", text)
    if t:
        res["final_target_density"] = float(t[-1])
    t = re.findall(r"Total routing overflow:\s*([\d.]+)", text)
    if t:
        res["routability_overflow_last"] = float(t[-1])
    t = re.findall(r"\[NesterovSolve\] Iter:\s*\d+.*?overflow:\s*([\d.]+)", text)
    if t:
        res["final_overflow"] = float(t[-1])
    return res


def parse_drt(text):
    """DRT log -> main-route iteration history (the first sequence of optimization iterations)."""
    if not text:
        return None
    seqs = []
    last_it = []
    cur = None
    for line in text.splitlines():
        m = re.search(r"DRT-0195\] Start (\d+)(?:st|nd|rd|th) (?:optimization|guides tiles|stubborn tiles)?\s*iteration", line)
        if m:
            it = int(m.group(1))
            if it == 0:
                cur = []
                seqs.append(cur)
                last_it.append(0)
            elif last_it:
                last_it[-1] = it
            continue
        m = re.search(r"Number of violations = (\d+)", line)
        if m and cur is not None:
            cur.append(int(m.group(1)))
    res = {"sequences": len(seqs)}
    if seqs:
        main = seqs[0]
        res["main_last_iteration"] = last_it[0]
        res["main_iterations"] = len(main)
        res["main_history"] = main[:8] + (["..."] + main[-3:] if len(main) > 11 else main[8:])
        res["iter0_violations"] = main[0] if main else None
        res["main_final_violations"] = main[-1] if main else None
        res["reroute_final_violations"] = [s[-1] for s in seqs[1:] if s]
    thr = re.findall(r"ORD-0030\] Using (\d+) thread", text)
    if thr:
        res["threads"] = int(thr[-1])
    return res


def metric(m, key, corner=None):
    if corner:
        key = "%s__corner:%s" % (key, corner)
    return m.get(key)


def extract(run_dir, params=None, exit_code=None, status=None, wall_s=None):
    res = {"schema": 1, "extracted": L.now_iso(), "host": socket.gethostname(), "run_dir": run_dir}
    if params:
        for k in ("run_id", "tag", "core_tag", "core_sha256", "tiles", "floorplan", "density", "period",
                  "pl_hold", "grt_hold", "mode", "threads", "overrides", "config_merged_sha256"):
            res[k] = params.get(k)
    res["exit_code"] = exit_code
    res["wall_s"] = wall_s
    if not os.path.isdir(run_dir):
        res["status"] = status or "no_run_dir"
        return res
    steps = step_dirs(run_dir)
    final_metrics = os.path.join(run_dir, "final", "metrics.json")
    complete = os.path.isfile(final_metrics)
    m = L.load_json(final_metrics) if complete else None
    msrc = "final/metrics.json" if m is not None else None
    if m is None:
        for s in reversed(steps):
            st = L.load_json(os.path.join(s[2], "state_out.json"))
            if st and st.get("metrics"):
                m = st["metrics"]
                msrc = os.path.basename(s[2]) + "/state_out.json"
                break
    m = m or {}
    res["metrics_source"] = msrc

    # ---- status
    err = read(os.path.join(run_dir, "error.log")) or ""
    if status:
        res["status"] = status
    elif complete and (exit_code in (0, None)) and not err.strip():
        res["status"] = "complete"
    elif complete:
        res["status"] = "complete_with_errors"
    elif exit_code is None:
        res["status"] = "partial"
    else:
        res["status"] = "failed"
    res["flow_complete"] = complete
    res["errors"] = [l for l in err.splitlines() if l.strip()][:20]
    res["last_step"] = os.path.basename(steps[-1][2]) if steps else None
    res["n_steps"] = len(steps)

    # ---- per-step wall time and peak memory
    st_list = []
    peak = 0.0
    for num, name, d in steps:
        rt = read(os.path.join(d, "runtime.txt"))
        ent = {"step": "%02d-%s" % (num, name), "runtime_s": hms_to_s(rt.strip()) if rt else None}
        ps = glob.glob(os.path.join(d, "*.process_stats.json"))
        if ps:
            pj = L.load_json(ps[0], {})
            rss = L.parse_mem_gib(((pj.get("peak_resources") or {}).get("memory_rss")) or "")
            if rss is not None:
                ent["peak_rss_gib"] = round(rss, 2)
                peak = max(peak, rss)
        st_list.append(ent)
    res["steps"] = st_list
    res["peak_step_rss_gib"] = round(peak, 2) if peak else None
    res["flow_runtime_s"] = sum(e["runtime_s"] or 0 for e in st_list) if st_list else None

    def rt_of(name_re):
        tot = None
        for e in st_list:
            if re.match(r"^\d+-" + name_re, e["step"]) and e["runtime_s"] is not None:
                tot = (tot or 0) + e["runtime_s"]
        return tot

    res["time_s"] = {
        "synthesis": rt_of(r"yosys-synthesis$"),
        "global_placement": rt_of(r"openroad-globalplacement$"),
        "cts": rt_of(r"openroad-cts$"),
        "resizer_post_cts": rt_of(r"openroad-resizertimingpostcts$"),
        "global_routing": rt_of(r"openroad-globalrouting$"),
        "detailed_routing": rt_of(r"openroad-detailedrouting$"),
        "repair_antennas": rt_of(r"openroad-repairantennas$"),
        "sta_post_pnr": rt_of(r"openroad-stapostpnr$"),
        "magic_streamout": rt_of(r"magic-streamout$"),
        "magic_drc": rt_of(r"magic-drc$"),
        "spice_extraction": rt_of(r"magic-spiceextraction$"),
        "lvs": rt_of(r"netgen-lvs$"),
    }

    # ---- logs
    grt = last_step(steps, r"openroad-globalrouting$")
    res["grt"] = parse_grt(step_log(grt)) if grt else None
    gpl = last_step(steps, r"openroad-globalplacement$")
    res["gpl"] = parse_gpl(step_log(gpl)) if gpl else None
    drt = last_step(steps, r"openroad-detailedrouting$")
    res["drt"] = parse_drt(step_log(drt)) if drt else None
    if drt:
        res["drt_step_complete"] = os.path.isfile(os.path.join(drt[2], "state_out.json"))
    pdn = last_step(steps, r"openroad-generatepdn$")
    if pdn:
        t = step_log(pdn) or ""
        res["pdn"] = {"step_complete": os.path.isfile(os.path.join(pdn[2], "state_out.json")),
                      "macros": len(re.findall(r"^SRAMPDN macro ", t, re.M)),
                      "stripe_macro_crossings_inside": len(re.findall(r"^SRAMPDN stripe .* runs inside", t, re.M)),
                      "bad": len(re.findall(r"SRAMPDN BAD", t)),
                      "errors": re.findall(r"^.*SRAMPDN.*(?:BAD|error|fail).*$", t, re.M | re.I)[:10]}
    drtlog = step_log(drt) if drt else ""
    res["drt0418_pins"] = len(re.findall(r"DRT-0418\]", drtlog or ""))

    # ---- metrics
    def g(k, c=None):
        return metric(m, k, c)

    res["utilization"] = g("design__instance__utilization")
    res["utilization_stdcell"] = g("design__instance__utilization__stdcell")
    res["core_area"] = g("design__core__area")
    res["stdcell_area"] = g("design__instance__area__stdcell")
    res["instance_area"] = g("design__instance__area")
    res["macro_area"] = g("design__instance__area__macros")
    res["instance_count"] = g("design__instance__count")
    res["stdcell_count"] = g("design__instance__count__stdcell")
    res["hold_buffers"] = g("design__instance__count__hold_buffer")
    res["setup_buffers"] = g("design__instance__count__setup_buffer")
    res["timing_repair_buffer_area"] = g("design__instance__area__class:timing_repair_buffer")
    res["sequential_cells"] = g("design__instance__count__class:sequential_cell")
    res["route_drc_errors"] = g("route__drc_errors")
    res["route_wirelength"] = g("route__wirelength")
    res["antenna_violations"] = g("route__antenna_violation__count")
    res["antenna_violating_nets"] = g("antenna__violating__nets")
    res["antenna_violating_pins"] = g("antenna__violating__pins")
    res["antenna_diodes"] = g("antenna_diodes_count")
    res["disconnected_pins_critical"] = g("design__critical_disconnected_pin__count")
    res["power_grid_violations"] = g("design__power_grid_violation__count")
    res["magic_drc_errors"] = g("magic__drc_error__count")
    res["illegal_overlaps"] = g("magic__illegal_overlap__count")
    res["lvs_errors"] = g("design__lvs_error__count")
    res["lvs"] = {k: v for k, v in m.items() if k.startswith("design__lvs")} or None
    res["klayout_drc_errors"] = g("klayout__drc_error__count")
    res["max_slew_violations"] = g("design__max_slew_violation__count")
    res["max_cap_violations"] = g("design__max_cap_violation__count")
    res["max_fanout_violations"] = g("design__max_fanout_violation__count")
    res["power_total_w"] = g("power__total")
    res["ir_drop_worst"] = g("ir__drop__worst")

    period = None
    if params and params.get("period"):
        period = float(params["period"])
    else:
        # a run made outside the harness: take the parameters from its resolved config
        rj = L.load_json(os.path.join(run_dir, "resolved.json"), {})
        if rj.get("CLOCK_PERIOD"):
            period = float(rj["CLOCK_PERIOD"])
            res["period"] = period
            res["density"] = rj.get("PL_TARGET_DENSITY_PCT")
            res["threads"] = rj.get("OPENROAD_THREADS")
            res["pl_hold"] = rj.get("PL_RESIZER_HOLD_SLACK_MARGIN")
            res["grt_hold"] = rj.get("GRT_RESIZER_HOLD_SLACK_MARGIN")
            die = rj.get("DIE_AREA")
            if isinstance(die, (list, tuple)):
                die = [float(v) for v in die]
            elif isinstance(die, str):
                die = [float(v) for v in die.split()]
            try:
                for t, s in L.read_tile_sizes().items():
                    if die and all(abs(a - float(b)) < 1e-6 for a, b in zip(die, s.split())):
                        res["tiles"] = t
            except (IOError, OSError):
                pass
            res["mode"] = "full" if any(s[1] == "magic-drc" for s in steps) else "fast"
    sta = {}
    have_sta = False
    for c in L.CORNERS:
        cs = L.CORNER_SHORT[c]
        ent = {
            "setup_ws": g("timing__setup__ws", c), "setup_wns": g("timing__setup__wns", c),
            "setup_tns": g("timing__setup__tns", c), "setup_vio": g("timing__setup_vio__count", c),
            "setup_r2r_ws": g("timing__setup_r2r__ws", c), "setup_r2r_vio": g("timing__setup_r2r_vio__count", c),
            "hold_ws": g("timing__hold__ws", c), "hold_wns": g("timing__hold__wns", c),
            "hold_tns": g("timing__hold__tns", c), "hold_vio": g("timing__hold_vio__count", c),
            "hold_r2r_ws": g("timing__hold_r2r__ws", c),
            "max_slew_vio": g("design__max_slew_violation__count", c),
            "max_cap_vio": g("design__max_cap_violation__count", c),
            "max_fanout_vio": g("design__max_fanout_violation__count", c),
        }
        if ent["setup_ws"] is not None:
            have_sta = True
        if period and ent["setup_ws"] is not None and period - ent["setup_ws"] > 0:
            ent["min_period_ns"] = round(period - ent["setup_ws"], 4)
            ent["fmax_mhz"] = round(1000.0 / (period - ent["setup_ws"]), 2)
        if period and ent["setup_r2r_ws"] is not None and period - ent["setup_r2r_ws"] > 0:
            ent["fmax_r2r_mhz"] = round(1000.0 / (period - ent["setup_r2r_ws"]), 2)
        sta[cs] = ent
    res["sta"] = sta
    # Which STA the numbers come from: post-route multi-corner STA if it ran
    sta_step = last_step(steps, r"openroad-stapostpnr$")
    if sta_step and os.path.isfile(os.path.join(sta_step[2], "state_out.json")):
        res["sta_source"] = "post-route"
    elif have_sta:
        pre = [s for s in steps if re.match(r"openroad-sta(prepnr|midpnr)", s[1])
               and os.path.isfile(os.path.join(s[2], "state_out.json"))]
        res["sta_source"] = ("pre-pnr" if pre and pre[-1][1].startswith("openroad-staprepnr") else "mid-pnr")
    else:
        res["sta_source"] = None

    # ---- candidate flag (keep final GDS/netlist/SPEF): routed clean and timing-clean at the TT sign-off
    # corner (typ setup, as TIMING_VIOLATION_CORNERS), hold clean at every corner, LVS clean if it ran.
    def zero(v):
        return v is not None and float(v) == 0

    reasons = []
    if not complete:
        reasons.append("flow did not complete")
    if res["sta_source"] != "post-route":
        reasons.append("no post-route STA")
    if not zero(res["route_drc_errors"]):
        reasons.append("route DRC %s" % res["route_drc_errors"])
    if not zero(res["antenna_violations"]):
        reasons.append("antenna %s" % res["antenna_violations"])
    if not zero(res["disconnected_pins_critical"]):
        reasons.append("disconnected pins %s" % res["disconnected_pins_critical"])
    if not zero(sta["typ"].get("setup_vio")):
        reasons.append("typ setup violations %s" % sta["typ"].get("setup_vio"))
    for c in ("typ", "fast", "slow"):
        if not zero(sta[c].get("hold_vio")):
            reasons.append("%s hold violations %s" % (c, sta[c].get("hold_vio")))
    if params and params.get("mode") == "full":
        if not zero(res["lvs_errors"]):
            reasons.append("LVS errors %s" % res["lvs_errors"])
    res["candidate"] = not reasons
    res["candidate_blockers"] = reasons
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", required=True, help="the LibreLane run directory (…/runs/wokwi)")
    ap.add_argument("--params", help="params.json of the sweep run")
    ap.add_argument("--exit-code", type=int)
    ap.add_argument("--status", help="override the derived status (timeout, preempted, …)")
    ap.add_argument("--wall-s", type=float)
    ap.add_argument("--extra", action="append", default=[], help="KEY=VALUE added to the result")
    ap.add_argument("--out", help="write here (default stdout)")
    args = ap.parse_args()
    params = L.load_json(args.params) if args.params else None
    res = extract(args.run_dir, params, args.exit_code, args.status, args.wall_s)
    for kv in args.extra:
        k, _, v = kv.partition("=")
        res[k] = v
    if args.out:
        L.write_json_atomic(args.out, res)
    else:
        json.dump(res, sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
