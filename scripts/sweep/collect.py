#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Aggregate sweep results into results.csv and a markdown summary.

Sources, per run id (latest manifest record wins):
  * <run>/result.json  written by run_one.sh at the end of every attempt;
  * <run>/status.json  job id / node / state of the latest attempt;
  * squeue (queued/running) and sacct (ended without a result: preempted,
    killed, node failure) for the latest job id.
Extra LibreLane run directories can be added with --run-dir (e.g. the
pre-harness run2 of tt-work/sram-flow); they are extracted on the fly.

fmax estimate per corner: 1000 / (CLOCK_PERIOD - worst setup slack) MHz, from
post-route STA when it ran (mid-PnR STA otherwise; the column sta_source
says which). It assumes every path scales with the period, which the I/O
constraints (a fraction of the period in the TT SDC) and the SRAM
clock-to-output only approximate; "r2r" uses the register-to-register slack.

Usage: collect.py [--sweep-root DIR] [--run-dir PATH[:LABEL] ...] [--out-dir DIR] [--no-slurm]
"""

import argparse
import csv
import json
import math
import os
import re
import tarfile
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402
import extract_result as X  # noqa: E402

COLUMNS = [
    "run_id", "tag", "status", "candidate", "job_id", "job_state", "partition", "cpus", "threads", "mode",
    "tiles", "floorplan", "density", "period", "pl_hold", "grt_hold", "core_tag", "core_sha", "config_sha",
    "wall_s", "flow_runtime_s", "t_grt_s", "t_drt_s", "t_magic_drc_s", "t_lvs_s", "peak_step_rss_gib",
    "maxrss_gib", "last_step",
    "util", "util_stdcell", "gpl_target", "gpl_min_feasible", "stdcell_area", "instance_count",
    "hold_buffers", "repair_buf_area",
    "grt_overflow", "grt_of_m2", "grt_of_m3", "grt_of_m4", "grt_use_m2", "grt_use_m3", "grt_use_m4",
    "drt_iter0", "drt_last_iter", "drt_final", "drt_residual", "drt0418", "antenna_viol", "antenna_nets", "antenna_diodes",
    "magic_drc", "illegal_overlap", "lvs_errors", "pdn_ok",
    "sta_source",
    "typ_setup_ws", "typ_setup_tns", "typ_setup_vio", "typ_r2r_ws", "typ_hold_ws", "typ_hold_vio",
    "fast_setup_ws", "fast_setup_tns", "fast_setup_vio", "fast_r2r_ws", "fast_hold_ws", "fast_hold_vio",
    "slow_setup_ws", "slow_setup_tns", "slow_setup_vio", "slow_r2r_ws", "slow_hold_ws", "slow_hold_vio",
    "fmax_typ", "fmax_fast", "fmax_slow", "fmax_r2r_typ", "fmax_r2r_slow",
    "max_slew_vio", "max_cap_vio", "max_fanout_vio", "wirelength", "power_w",
    "candidate_blockers", "errors", "flow_error", "run_dir",
]


def flat(res, rec=None, job=None):
    res = res or {}
    g = lambda *ks: _dig(res, ks)  # noqa: E731
    row = {c: None for c in COLUMNS}
    row.update({
        "run_id": res.get("run_id") or (rec or {}).get("run_id"),
        "tag": res.get("tag") or (rec or {}).get("tag"),
        "status": res.get("status"),
        "candidate": res.get("candidate"),
        "job_id": res.get("job_id") or (rec or {}).get("job_id"),
        "partition": res.get("partition") or (rec or {}).get("partition"),
        "cpus": res.get("cpus") or (rec or {}).get("cpus"),
        "threads": res.get("threads"),
        "mode": res.get("mode"),
        "tiles": res.get("tiles"),
        "floorplan": res.get("floorplan"),
        "density": res.get("density"),
        "period": res.get("period"),
        "pl_hold": res.get("pl_hold"),
        "grt_hold": res.get("grt_hold"),
        "core_tag": res.get("core_tag"),
        "core_sha": (res.get("core_sha256") or "")[:12] or None,
        "config_sha": (res.get("config_merged_sha256") or "")[:12] or None,
        "wall_s": res.get("wall_s"),
        "flow_runtime_s": _r(res.get("flow_runtime_s"), 0),
        "t_grt_s": _r(g("time_s", "global_routing"), 0),
        "t_drt_s": _r(g("time_s", "detailed_routing"), 0),
        "t_magic_drc_s": _r(g("time_s", "magic_drc"), 0),
        "t_lvs_s": _r(g("time_s", "lvs"), 0),
        "peak_step_rss_gib": res.get("peak_step_rss_gib"),
        "last_step": res.get("last_step"),
        "util": _r(res.get("utilization"), 4),
        "util_stdcell": _r(res.get("utilization_stdcell"), 4),
        "gpl_target": g("gpl", "target_density"),
        "gpl_min_feasible": g("gpl", "min_feasible_density"),
        "stdcell_area": _r(res.get("stdcell_area"), 0),
        "instance_count": res.get("instance_count"),
        "hold_buffers": res.get("hold_buffers"),
        "repair_buf_area": _r(res.get("timing_repair_buffer_area"), 0),
        "grt_overflow": g("grt", "total", "overflow"),
        "grt_of_m2": g("grt", "layers", "Metal2", "overflow"),
        "grt_of_m3": g("grt", "layers", "Metal3", "overflow"),
        "grt_of_m4": g("grt", "layers", "Metal4", "overflow"),
        "grt_use_m2": g("grt", "layers", "Metal2", "usage_pct"),
        "grt_use_m3": g("grt", "layers", "Metal3", "usage_pct"),
        "grt_use_m4": g("grt", "layers", "Metal4", "usage_pct"),
        "drt_iter0": g("drt", "iter0_violations"),
        "drt_last_iter": g("drt", "main_last_iteration"),
        "drt_final": res.get("route_drc_errors"),
        "drt0418": res.get("drt0418_pins"),
        "antenna_viol": res.get("antenna_violations"),
        "antenna_nets": res.get("antenna_violating_nets"),
        "antenna_diodes": res.get("antenna_diodes"),
        "magic_drc": res.get("magic_drc_errors"),
        "illegal_overlap": res.get("illegal_overlaps"),
        "lvs_errors": res.get("lvs_errors"),
        "pdn_ok": (g("pdn", "step_complete") and g("pdn", "bad") == 0) if res.get("pdn") else None,
        "sta_source": res.get("sta_source"),
        "max_slew_vio": res.get("max_slew_violations"),
        "max_cap_vio": res.get("max_cap_violations"),
        "max_fanout_vio": res.get("max_fanout_violations"),
        "wirelength": res.get("route_wirelength"),
        "power_w": _r(res.get("power_total_w"), 5),
        "candidate_blockers": "; ".join(res.get("candidate_blockers") or []) or None,
        "errors": " | ".join(res.get("errors") or [])[:300] or None,
        "run_dir": res.get("run_dir"),
    })
    for c in ("typ", "fast", "slow"):
        s = g("sta", c) or {}
        row[c + "_setup_ws"] = _r(s.get("setup_ws"), 3)
        row[c + "_setup_tns"] = _r(s.get("setup_tns"), 2)
        row[c + "_setup_vio"] = s.get("setup_vio")
        row[c + "_r2r_ws"] = _r(s.get("setup_r2r_ws"), 3)
        row[c + "_hold_ws"] = _r(s.get("hold_ws"), 3)
        row[c + "_hold_vio"] = s.get("hold_vio")
        row["fmax_" + c] = s.get("fmax_mhz")
    row["fmax_r2r_typ"] = g("sta", "typ", "fmax_r2r_mhz")
    row["fmax_r2r_slow"] = g("sta", "slow", "fmax_r2r_mhz")
    if job:
        row["job_state"] = job.get("state")
        if job.get("maxrss_gib") is not None:
            row["maxrss_gib"] = round(job["maxrss_gib"], 2)
    return row


def _dig(d, ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _r(v, n):
    if v is None:
        return None
    try:
        return round(float(v), n) if n else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def collect(sweep_root, extra_dirs, use_slurm=True):
    manifest = L.load_json(os.path.join(sweep_root, "manifest.json"), {"records": []})
    latest = {}
    for r in L.records_for_root(manifest.get("records", []), sweep_root):
        latest[r["run_id"]] = r
    sq = L.squeue_states() if use_slurm else {}
    ended = [r["job_id"] for r in latest.values() if r.get("job_id") not in sq]
    sa = L.sacct_states(ended + [r["job_id"] for r in latest.values() if r.get("job_id") in sq]) if use_slurm else {}
    rows = []
    for rid, rec in sorted(latest.items()):
        rd = rec.get("run_dir") or os.path.join(sweep_root, "runs", rid)
        res = L.load_json(os.path.join(rd, "result.json"))
        st = L.load_json(os.path.join(rd, "status.json"), {})
        jid = rec.get("job_id")
        job = dict(sa.get(jid, {}))
        if jid in sq:
            job["state"] = sq[jid]["state"]
        else:
            arr = str(rec.get("array_job_id"))
            for k, v in sq.items():
                if k.startswith(arr + "_[") and v["state"] == "PENDING":
                    job["state"] = "PENDING"
        params = L.load_json(os.path.join(rd, "params.json"), {})
        if res and res.get("job_id") and jid and res.get("job_id") != jid and job.get("state") in ("PENDING", "RUNNING"):
            res = None  # result belongs to an older attempt; the new job is active
        if res is None:
            res = {k: params.get(k) for k in ("run_id", "tag", "core_tag", "core_sha256", "tiles", "floorplan",
                                              "density", "period", "pl_hold", "grt_hold", "mode", "threads",
                                              "config_merged_sha256")}
            res["tag"] = res.get("tag") or rec.get("tag")
            state = job.get("state")
            if state in ("PENDING", "RUNNING", "REQUEUED", "CONFIGURING", "COMPLETING"):
                res["status"] = state.lower()
            elif state:
                res["status"] = "no_result(%s)" % state
            else:
                res["status"] = "unknown"
            res["run_dir"] = rd
            if st.get("node"):
                res["errors"] = ["attempt %s on %s" % (st.get("attempt"), st.get("node"))]
        row = flat(res, rec, job)
        if row["status"] not in ("complete", "running", "pending", "requeued", "configuring", "completing"):
            row["flow_error"] = slurm_log_error(sweep_root, rec)
        try:
            if (row["drt_final"] not in (None, "") and float(row["drt_final"]) > 0
                    and (res.get("flow_complete") or res.get("drt_step_complete"))):
                row["drt_residual"] = drt_residual(rd)
        except (TypeError, ValueError):
            pass
        rows.append(row)
    for spec in extra_dirs:
        path, _, label = spec.partition(":")
        r = X.extract(path)
        r["run_id"] = label or path
        r["tag"] = "external"
        rows.append(flat(r))
    return rows


def drt_residual(run_dir):
    """Classify the final detailed-routing violations of a run that ended with route DRC errors:
    how many lie inside a macro footprint, by type and layer. Reads the final
    <NN>-openroad-detailedrouting/<design>.drc from out/librelane-logs.tar.gz and the macro
    placement from snap/src/config.json (MACROS instances) and the macro LEF SIZE; caches the
    answer in out/drt_residual.json. Returns a short string or None."""
    out = os.path.join(run_dir, "out")
    cache = os.path.join(out, "drt_residual.json")
    c = L.load_json(cache)
    if c is not None:
        return c.get("summary")
    tgz = os.path.join(out, "librelane-logs.tar.gz")
    cfg = L.load_json(os.path.join(run_dir, "snap", "src", "config.json"), {})
    if not os.path.isfile(tgz) or not cfg.get("MACROS"):
        return None
    boxes = []
    for macro, m in cfg["MACROS"].items():
        size = None
        for d, _, names in os.walk(os.path.join(run_dir, "snap", "macros", macro)):
            for n in names:
                if n.endswith(".lef"):
                    mm = re.search(r"^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", open(os.path.join(d, n)).read(), re.M)
                    if mm:
                        size = (float(mm.group(1)), float(mm.group(2)))
        if not size:
            continue
        for inst in (m.get("instances") or {}).values():
            x, y = inst["location"]
            w, h = size if inst.get("orientation", "N") in ("N", "S", "FN", "FS") else size[::-1]
            boxes.append((x, y, x + w, y + h))
    text = None
    try:
        with tarfile.open(tgz, "r:gz") as tf:
            for mem in tf:
                if re.match(r"^\./\d+-openroad-detailedrouting/[^/]+\.drc$", mem.name):
                    text = tf.extractfile(mem).read().decode("utf-8", "replace")
    except (IOError, OSError, tarfile.TarError):
        return None
    if text is None:
        return None
    viol = re.findall(r"violation type: ([^\n]+?)\s*\n\s+srcs: [^\n]*\s+bbox = \(([\d.]+), ([\d.]+)\) - "
                      r"\(([\d.]+), ([\d.]+)\) on Layer (\S+)", text)
    inside, outside = {}, {}
    for typ, x0, y0, x1, y1, layer in viol:
        cx, cy = (float(x0) + float(x1)) / 2, (float(y0) + float(y1)) / 2
        hit = any(a <= cx <= c2 and b <= cy <= d2 for a, b, c2, d2 in boxes)
        k = "%s %s" % (typ.strip(), layer)
        tgt = inside if hit else outside
        tgt[k] = tgt.get(k, 0) + 1
    summary = "%d in macro footprints (%s); %d outside (%s)" % (
        sum(inside.values()), ", ".join("%s %d" % kv for kv in sorted(inside.items())) or "-",
        sum(outside.values()), ", ".join("%s %d" % kv for kv in sorted(outside.items())) or "-")
    try:
        L.write_json_atomic(cache, {"summary": summary, "inside": inside, "outside": outside, "total": len(viol)})
    except (IOError, OSError):
        pass
    return summary


def slurm_log_error(sweep_root, rec, tail_bytes=65536):
    """LibreLane's final error block ("The following error was encountered ...", e.g. the deferred
    'Setup violations found in the following corners: * nom_typ_1p20V_25C') from the tail of the
    run's Slurm log. LibreLane prints it on the console only (error.log stays empty for deferred
    checker errors), so result.json cannot carry it. Returns None when absent."""
    if not rec or not rec.get("job_name") or rec.get("array_job_id") is None:
        return None
    path = os.path.join(sweep_root, "logs", "%s-%s_%s.out" % (rec["job_name"], rec["array_job_id"],
                                                             rec.get("array_index", 0)))
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail_bytes))
            text = f.read().decode("utf-8", "replace")
    except (IOError, OSError):
        return None
    i = text.rfind("The following error was encountered")
    if i < 0:
        return None
    j = text.find("will now quit", i)
    block = text[i:j if j > 0 else i + 4000]
    out = []
    for line in block.splitlines():
        line = re.sub(r"^\[\d\d:\d\d:\d\d\]\s+(ERROR|WARNING|INFO)\s+", "", line)
        line = re.sub(r"\s+[\w./]+\.py:\d+\s*$", "", line).strip()
        if line and not line.startswith("[") and "LibreLane" not in line:
            out.append(line)
    msg = " ".join(out)
    msg = re.sub(r"^The following error was encountered while running the flow:\s*", "", msg)
    return msg[:400] or None


def fmt(v, nd=None):
    if v is None or v == "":
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if math.isnan(v):
            return "–"
        if nd is not None:
            return ("%%.%df" % nd) % v
        return ("%.3f" % v).rstrip("0").rstrip(".")
    return str(v)


def sort_key(r):
    order = {"complete": 0, "complete_with_errors": 1}
    return (0 if r.get("candidate") else 1, order.get(r.get("status"), 2),
            -(r.get("fmax_typ") or 0), r.get("run_id") or "")


def summary_md(rows, sweep_root):
    lines = ["# pe-sweep summary", "",
             "Generated %s by scripts/sweep/collect.py from %s (manifest, result.json, squeue/sacct)."
             % (L.now_iso(), sweep_root), ""]
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines.append("Runs: %d. By status: %s." % (len(rows), ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
    lines += ["",
              "fmax = 1000 / (period − worst setup slack) per corner, from post-route STA when `sta` says "
              "post-route. TT timing sign-off is the typical corner; `slow` is reported, not signed off. "
              "`r2r` uses the register-to-register slack only. `cand` = routed with 0 DRC and 0 antenna "
              "violations, typ setup and all-corner hold clean (and LVS clean in full mode).", ""]
    hdr = ["run", "tag", "status", "cand", "job", "tiles", "fp", "dens", "T ns", "hold", "mode", "thr",
           "wall h", "DRT h", "util", "GRT OF", "DRT it0", "DRT last", "DRT", "ant", "sta",
           "typ WS", "slow WS", "slow r2r", "hold WS min", "fmax typ", "fmax slow", "fmax r2r slow"]
    lines.append("| " + " | ".join(hdr) + " |")
    lines.append("|" + "---|" * len(hdr))
    for r in sorted(rows, key=sort_key):
        hold_min = min([x for x in (r["typ_hold_ws"], r["fast_hold_ws"], r["slow_hold_ws"]) if x is not None],
                       default=None)
        wall = r["wall_s"]
        try:
            wall = float(wall) / 3600 if wall not in (None, "") else None
        except ValueError:
            wall = None
        lines.append("| " + " | ".join([
            "`%s`" % r["run_id"], fmt(r["tag"]), fmt(r["status"]), fmt(r["candidate"]), fmt(r["job_id"]),
            fmt(r["tiles"]), fmt(r["floorplan"]), fmt(r["density"]), fmt(r["period"]),
            "%s/%s" % (fmt(r["pl_hold"]), fmt(r["grt_hold"])), fmt(r["mode"]), fmt(r["threads"]),
            fmt(wall, 2), fmt((r["t_drt_s"] or 0) / 3600.0 if r["t_drt_s"] is not None else None, 2),
            fmt(r["util"]), fmt(r["grt_overflow"]), fmt(r["drt_iter0"]), fmt(r["drt_last_iter"]),
            fmt(r["drt_final"]), fmt(r["antenna_viol"]), fmt(r["sta_source"]),
            fmt(r["typ_setup_ws"], 2), fmt(r["slow_setup_ws"], 2), fmt(r["slow_r2r_ws"], 2), fmt(hold_min, 3),
            fmt(r["fmax_typ"], 1), fmt(r["fmax_slow"], 1), fmt(r["fmax_r2r_slow"], 1)]) + " |")
    lines += ["", "## Best candidates per tile size", ""]
    by_tiles = {}
    for r in rows:
        if r.get("candidate"):
            by_tiles.setdefault(r["tiles"], []).append(r)
    if not by_tiles:
        lines.append("No candidates yet.")
    for tiles, rs in sorted(by_tiles.items()):
        lines.append("**%s** (ranked by typ fmax, then slow fmax):" % tiles)
        lines.append("")
        for r in sorted(rs, key=lambda r: (-(r["fmax_typ"] or 0), -(r["fmax_slow"] or 0)))[:5]:
            lines.append("- `%s`: fmax typ %s / fast %s / slow %s MHz (r2r slow %s), util %s, GRT overflow %s, "
                         "DRT iter0 %s, full-mode LVS %s, Magic DRC %s"
                         % (r["run_id"], fmt(r["fmax_typ"], 1), fmt(r["fmax_fast"], 1), fmt(r["fmax_slow"], 1),
                            fmt(r["fmax_r2r_slow"], 1), fmt(r["util"]), fmt(r["grt_overflow"]),
                            fmt(r["drt_iter0"]), fmt(r["lvs_errors"]), fmt(r["magic_drc"])))
        lines.append("")
    cwe = [r for r in rows if r["status"] == "complete_with_errors"]
    if cwe:
        lines += ["", "## Completed with a LibreLane error exit (the action would fail)", ""]
        for r in cwe:
            lines.append("- `%s` (job %s): %s%s%s" % (r["run_id"], r["job_id"],
                                                      r.get("flow_error") or "error text not found in the Slurm log",
                                                      (" — blockers: " + r["candidate_blockers"])
                                                      if r.get("candidate_blockers") else "",
                                                      (" — residual DRT: " + r["drt_residual"])
                                                      if r.get("drt_residual") else ""))
    bad = [r for r in rows if r["status"] not in ("complete", "complete_with_errors", "pending", "running")]
    if bad:
        lines += ["", "## Not complete", ""]
        for r in bad:
            lines.append("- `%s` (%s, job %s): %s%s%s" % (r["run_id"], r["status"], r["job_id"],
                                                            r["last_step"] or "",
                                                            (" — " + r["errors"]) if r["errors"] else "",
                                                            (" — LibreLane: " + r["flow_error"])
                                                            if r.get("flow_error") else "")
                         + ((" — residual DRT: " + r["drt_residual"]) if r.get("drt_residual") else ""))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sweep-root", default=L.SWEEP_ROOT)
    ap.add_argument("--run-dir", action="append", default=[],
                    help="extra LibreLane run dir (…/runs/wokwi), optionally PATH:LABEL")
    ap.add_argument("--out-dir", help="where results.csv and summary.md go (default: the sweep root)")
    ap.add_argument("--no-slurm", action="store_true", help="do not query squeue/sacct")
    args = ap.parse_args()
    rows = collect(args.sweep_root, args.run_dir, use_slurm=not args.no_slurm)
    out = args.out_dir or args.sweep_root
    L.mkdir_p(out)
    csv_path = os.path.join(out, "results.csv")
    with open(csv_path + ".tmp", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in sorted(rows, key=sort_key):
            w.writerow(r)
    os.replace(csv_path + ".tmp", csv_path)
    md = summary_md(rows, args.sweep_root)
    md_path = os.path.join(out, "summary.md")
    with open(md_path, "w") as f:
        f.write(md)
    print("wrote %s (%d rows) and %s" % (csv_path, len(rows), md_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
