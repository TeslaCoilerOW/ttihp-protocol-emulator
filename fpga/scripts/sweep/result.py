#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Result of one sweep run as JSON (run_task.sh): fmax of the core clock,
the critical path's logic/routing split and first net, utilisation.

    result.py RUN_DIR RUN_ID BUILD_DIR PLACER:SEED OPTIONS SECONDS HOST
"""
import json
import re
import sys
from pathlib import Path

out, rid, bdir, run, opts, secs, host = sys.argv[1:8]
out = Path(out)
r = {"run_id": rid, "build": bdir, "run": run, "placer": run.split(":")[0], "seed": int(run.split(":")[1]),
     "options": opts, "secs": int(secs), "host": host, "fmax": None}
try:
    rep = json.loads((out / "report.json").read_text())
    r["fmax"] = rep["fmax"]["clk"]["achieved"]
    r["util"] = {k: v["used"] for k, v in rep.get("utilization", {}).items() if v.get("used")}
except (OSError, KeyError, ValueError):
    pass
log = (out / "nextpnr.log").read_text(errors="replace") if (out / "nextpnr.log").exists() else ""
split = re.findall(r"Info: ([0-9.]+) ns logic, ([0-9.]+) ns routing", log)
if split:
    r["logic_ns"], r["route_ns"] = float(split[-1][0]), float(split[-1][1])
crit = log.rfind("Critical path report for clock 'clk'")
if crit >= 0:
    first = re.search(r"Net (\S+)", log[crit:])
    r["crit_first_net"] = first.group(1) if first else None
r["errors"] = len(re.findall(r"^ERROR:", log, re.M))
if (out / "timeout").exists():
    r["timeout"] = True
print(json.dumps(r))
