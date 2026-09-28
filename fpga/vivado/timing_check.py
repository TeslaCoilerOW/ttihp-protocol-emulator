#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""PASS/FAIL from a Vivado timing summary (fpga/vivado/build.tcl outputs).

    python3 fpga/vivado/timing_check.py OUT_DIR/timing_summary.rpt [--utilization OUT_DIR/utilization.rpt]
        [--methodology OUT_DIR/methodology.rpt] [--drc OUT_DIR/drc.rpt] [--json out.json]

Reads the "Design Timing Summary" table of report_timing_summary: WNS, TNS,
WHS, THS, WPWS, TPWS and their endpoint counts. PASS requires WNS >= 0,
TNS = 0, WHS >= 0, THS = 0, WPWS >= 0, TPWS = 0 and at least one
constrained setup endpoint. The check_timing counts of the same report
(no_clock, unconstrained_internal_endpoints, no_input_delay, ...) are
reported; unclocked registers or unconstrained internal endpoints also fail
(every register of this design is clocked by the core clock; the board's
asynchronous inputs, which have no input delay, only show up as warnings).
With --methodology and --drc, every "Critical Warning" row of
report_methodology and every "Error" row of report_drc also fails (a
methodology critical warning such as TIMING-2 or TIMING-4 means the clock
constraints themselves are suspect). With --utilization, the Slice LUT,
register, block RAM and DSP rows of
report_utilization are added. The worst setup path of the report (its first
"Max Delay Paths" entry with the smallest slack) is summarised with the
fmax it implies, 1 / (requirement - slack). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FIELDS = ["WNS", "TNS", "TNS_failing", "TNS_total", "WHS", "THS", "THS_failing", "THS_total",
          "WPWS", "TPWS", "TPWS_failing", "TPWS_total"]
FAIL_CHECKS = ("no_clock", "unconstrained_internal_endpoints")


def parse_summary(text: str) -> dict:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "WNS(ns)" in line and "WHS(ns)" in line:
            for row in lines[i + 1:i + 6]:
                tok = row.split()
                if len(tok) == len(FIELDS) and not set(row.strip()) <= set("- "):
                    vals = {}
                    for k, v in zip(FIELDS, tok):
                        try:
                            vals[k] = int(v) if k.endswith(("failing", "total")) else float(v)
                        except ValueError:
                            vals[k] = None       # "NA": no paths of that kind
                    return vals
            break
    raise ValueError("no Design Timing Summary table found")


def parse_checks(text: str) -> dict:
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"checking (\w+) \((\d+)\)", text)}


def parse_utilization(text: str) -> dict:
    out = {}
    for name in ("Slice LUTs", "Slice Registers", "Block RAM Tile", "DSPs", "LUT as Memory", "Bonded IOB"):
        m = re.search(r"^\|\s*" + re.escape(name) + r"\*?\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"
                      r"(?:\s*([\d.]+)\s*\|)?\s*([\d.]+)\s*\|\s*([\d.<]+)\s*\|", text, re.M)
        if m:
            used, avail, pct = m.group(1), m.group(4), m.group(5)
            out[name] = {"used": float(used), "available": float(avail), "percent": pct}
    return out


def parse_worst_path(text: str) -> dict | None:
    """The worst of the first setup paths listed under each "Max Delay Paths"."""
    worst = None
    for block in text.split("Max Delay Paths")[1:]:
        m = re.search(r"Slack \((MET|VIOLATED)\)\s*:\s*(-?[\d.]+)ns", block)
        if not m:
            continue
        slack = float(m.group(2))
        get = lambda pat: (re.search(pat, block).group(1).strip() if re.search(pat, block) else None)  # noqa: E731
        req = get(r"Requirement:\s*([\d.]+)ns")
        path = {"slack_ns": slack, "source": get(r"Source:\s*(\S+)"), "destination": get(r"Destination:\s*(\S+)"),
                "path_group": get(r"Path Group:\s*(\S+)"), "requirement_ns": float(req) if req else None,
                "data_path_delay": get(r"Data Path Delay:\s*(.+)"), "logic_levels": get(r"Logic Levels:\s*(.+)")}
        if path["requirement_ns"] and path["requirement_ns"] - slack > 0:
            path["implied_fmax_mhz"] = round(1000.0 / (path["requirement_ns"] - slack), 2)
        if worst is None or slack < worst["slack_ns"]:
            worst = path
    return worst


def parse_rules(text: str) -> list[dict]:
    """Rule-table rows "| ID | Severity | Description | Violations |" of
    report_methodology / report_drc."""
    out = []
    for m in re.finditer(r"^\|\s*([A-Z][A-Z0-9]*-\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|", text, re.M):
        out.append({"id": m.group(1), "severity": m.group(2), "description": m.group(3), "violations": int(m.group(4))})
    return out


def verdict(summary: dict, checks: dict) -> tuple[bool, list[str]]:
    reasons = []
    s = summary
    if not s.get("TNS_total"):
        reasons.append("no constrained setup endpoints")
    if s.get("WNS") is None or s["WNS"] < 0:
        reasons.append(f"WNS {s.get('WNS')} ns")
    if s.get("TNS") not in (0, 0.0):
        reasons.append(f"TNS {s.get('TNS')} ns")
    if s.get("WHS") is None or s["WHS"] < 0:
        reasons.append(f"WHS {s.get('WHS')} ns")
    if s.get("THS") not in (0, 0.0):
        reasons.append(f"THS {s.get('THS')} ns")
    if s.get("WPWS") is not None and s["WPWS"] < 0:
        reasons.append(f"WPWS {s['WPWS']} ns")
    if s.get("TPWS") not in (0, 0.0, None):
        reasons.append(f"TPWS {s['TPWS']} ns")
    for k in FAIL_CHECKS:
        if checks.get(k):
            reasons.append(f"check_timing {k}: {checks[k]}")
    return not reasons, reasons


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("summary", type=Path)
    ap.add_argument("--utilization", type=Path)
    ap.add_argument("--methodology", type=Path)
    ap.add_argument("--drc", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    text = a.summary.read_text(errors="replace")
    try:
        summary = parse_summary(text)
    except ValueError as err:
        print(f"FAIL: {a.summary.name}: {err}", file=sys.stderr)
        return 2
    checks = parse_checks(text)
    ok, reasons = verdict(summary, checks)
    rules = {}
    for key, path, bad in (("methodology", a.methodology, "Critical Warning"), ("drc", a.drc, "Error")):
        if path:
            rules[key] = parse_rules(path.read_text(errors="replace"))
            for r in rules[key]:
                if r["severity"] == bad:
                    reasons.append(f"{key} {r['id']} ({bad}: {r['description']}, {r['violations']})")
    ok = not reasons
    result = {"report": a.summary.name, "summary": summary, "check_timing": checks,
              "met_line": "All user specified timing constraints are met." in text, "pass": ok,
              "reasons": reasons, "worst_setup_path": parse_worst_path(text), "rules": rules}
    if a.utilization:
        result["utilization"] = parse_utilization(a.utilization.read_text(errors="replace"))
    print(f"WNS {summary['WNS']} ns, TNS {summary['TNS']} ns ({summary['TNS_failing']}/{summary['TNS_total']} "
          f"failing), WHS {summary['WHS']} ns, THS {summary['THS']} ns ({summary['THS_failing']}/"
          f"{summary['THS_total']}), WPWS {summary['WPWS']} ns")
    warn = {k: v for k, v in checks.items() if v and k not in FAIL_CHECKS}
    if warn:
        print("check_timing warnings: " + ", ".join(f"{k} {v}" for k, v in warn.items()))
    for key, rows in rules.items():
        if rows:
            print(f"{key}: " + ", ".join(f"{r['id']} {r['severity']} ({r['violations']})" for r in rows))
    w = result["worst_setup_path"]
    if w:
        print(f"worst setup path ({w['path_group']}): {w['source']} -> {w['destination']}, slack {w['slack_ns']} ns "
              f"of {w['requirement_ns']} ns, {w['data_path_delay']}, logic levels {w['logic_levels']}; "
              f"implied fmax {w.get('implied_fmax_mhz')} MHz")
    for name, u in result.get("utilization", {}).items():
        print(f"{name}: {u['used']:g} of {u['available']:g} ({u['percent']} %)")
    print("VIVADO TIMING " + ("PASS" if ok else "FAIL: " + "; ".join(reasons)))
    if a.json:
        a.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
