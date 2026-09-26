#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Assemble the small result files kept in the repository from a campaign root.

  collect.py <campaign root> <out dir> --commit SHA [--netlist FILE] [--labels L ...]

Reads <root>/summaries/summary-<label>.json and summary-all.json (report.py),
negctl-detection.json and gl-vs-rtl.json (report_job.sh), and
<root>/configs/<label>.env (submit.sh), and writes

  <out dir>/campaigns.json      per-campaign totals, holes and xcov bins, job ids
  <out dir>/coverage-all.json   merged coverage of every listed campaign
  <out dir>/report.md           copy of <root>/summaries/report.md

The per-campaign entries have the fields of results/campaigns.json of the
73536f0 campaign, plus the design variant and the generator generation.
Paths under $PE_WORK (default: the parent of the root) become "<work dir>".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def env_file(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip()
    return out


def scrub(value, prefix: str):  # noqa: ANN001, ANN201
    """Replace the cluster work-directory prefix in every string (public-repo hygiene)."""
    if isinstance(value, str):
        return value.replace(prefix, "<work dir>")
    if isinstance(value, list):
        return [scrub(v, prefix) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v, prefix) for k, v in value.items()}
    return value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--netlist", type=Path, help="gate-level netlist (sha256 recorded)")
    ap.add_argument("--labels", nargs="*", help="campaigns in table order (default: every summary)")
    ap.add_argument("--negctl", default="negctl-xor", help="negative-control campaign label ('' for none)")
    args = ap.parse_args()
    args.root = args.root.resolve()
    prefix = os.environ.get("PE_WORK", "").rstrip("/") or str(args.root.parent)
    summaries = args.root / "summaries"
    labels = args.labels or sorted(p.name[len("summary-"):-len(".json")] for p in summaries.glob("summary-*.json")
                                   if p.name != "summary-all.json")
    campaigns = {}
    for label in labels:
        s = json.loads((summaries / f"summary-{label}.json").read_text())
        cfg = env_file(args.root / "configs" / f"{label}.env")
        jobs = sorted(j for j in s.get("slurm_array_jobs", []) if j)
        campaigns[label] = {
            "slurm_array_job": ", ".join(jobs),
            "variant": cfg.get("VARIANT", "?"),
            "design_variant": ", ".join(s.get("design_variants", [])) or cfg.get("DESIGN_VARIANT") or "base",
            "generation": s.get("generations", [None])[0] if len(s.get("generations", [])) == 1
            else s.get("generations"),
            "level": cfg.get("MODE", "?"),
            "seeds": s.get("seed_range"),
            "cases_per_seed": int(cfg["ITERS"]) if "ITERS" in cfg else None,
            "host_cycles_per_case": int(cfg["CYCLES"]) if "CYCLES" in cfg else None,
            "xcov": s.get("xcov") is not None,
            "totals": s["totals"],
            "infra_errors": len(s["infra_errors"]),
            "failures": len(s["failures"]),
            "failure_list": s["failures"][:50],
            "instructions_completed": s["instructions_completed"],
            "case_cycles": s["case_cycles"],
            "mover_transfers": s["coverage"]["mover"],
            "holes": s["holes"],
            "xcov_bins": s["xcov"]["bins"] if s.get("xcov") else None,
        }
    out: dict = {"commit": args.commit}
    if args.netlist:
        out["netlist_sha256"] = hashlib.sha256(args.netlist.read_bytes()).hexdigest()
    out["results_on_cluster"] = str(args.root).replace(prefix, "<work dir>")
    out["campaigns"] = campaigns
    negctl = summaries / "negctl-detection.json"
    if args.negctl and negctl.exists():
        detection = json.loads(negctl.read_text())
        neg_summary = summaries / f"{args.negctl}.json"
        if neg_summary.exists():
            detection["slurm_array_job"] = ", ".join(
                sorted(j for j in json.loads(neg_summary.read_text()).get("slurm_array_jobs", []) if j))
        out["negative_control"] = detection
    gl = summaries / "gl-vs-rtl.json"
    if gl.exists():
        out["gl_vs_rtl_case_cycles"] = json.loads(gl.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "campaigns.json").write_text(json.dumps(scrub(out, prefix), indent=1) + "\n")
    everything = json.loads((summaries / "summary-all.json").read_text())
    for key in ("failures",):
        everything[key] = everything[key][:50]
    everything.pop("labels", None)
    everything["labels"] = labels
    (args.out / "coverage-all.json").write_text(json.dumps(scrub(everything, prefix), indent=1) + "\n")
    (args.out / "report.md").write_text((summaries / "report.md").read_text().replace(prefix, "<work dir>"))
    print(json.dumps({k: {"cases": v["totals"].get("cases_run"), "failed": v["totals"].get("cases_failed"),
                          "infra": v["infra_errors"]} for k, v in campaigns.items()}, indent=1))


if __name__ == "__main__":
    main()
