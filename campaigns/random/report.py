#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Markdown tables for docs/verification-campaign.md from the campaign results.

  report.py <vcamp/random> <label> [<label> ...] [--summary-dir DIR] [--default-label LABEL]

Writes DIR/summary-<label>.json (merge.py output) for each campaign and
DIR/summary-all.json, and prints the per-campaign table, the coverage tables
and the holes as Markdown.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from merge import MNEMONIC, STALLS, load, merge, xfer_modes


def row(label: str, results: list[dict], s: dict) -> str:
    ok = [r for r in results if not r.get("infra_error")]
    r0 = ok[0] if ok else {}
    t = s["totals"]
    seeds = sorted(r["seed"] for r in results)
    jobs = sorted({r.get("slurm_array_job") or r.get("slurm_job", "") for r in ok} - {""})
    return (f"| `{label}` | {r0.get('variant', '?')} | {'GL' if r0.get('gate_level') else 'RTL'} | "
            f"{seeds[0]:#x}..{seeds[-1]:#x} ({len(seeds)}) | {r0.get('iterations', '?')} | "
            f"{r0.get('host_cycles', '?'):,} | {t.get('cases_run', 0):,} | {t.get('cases_passed', 0):,} | "
            f"{t.get('cases_failed', 0)} | {len(s['infra_errors'])} | "
            f"{t.get('programs_loaded', 0):,} + {t.get('programs_reloaded', 0):,} | "
            f"{t.get('lockstep_cycles', 0):,} | {s['instructions_completed']:,} | "
            f"{t.get('wall_s', 0) / 3600:.1f} | {', '.join(jobs)} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("labels", nargs="+")
    ap.add_argument("--summary-dir", type=Path)
    ap.add_argument("--default-label", default="rtl-default",
                    help="campaign whose holes are reported as those of the unmodified generator")
    args = ap.parse_args()
    out = []
    out.append("| campaign | generator | level | seeds | cases/seed | host cycles/case | cases run | passed | "
               "failed | infra errors | programs loaded + reloads | lockstep cycles | instructions completed | "
               "sim CPU-h | Slurm array |")
    out.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    everything, rtl = [], []
    cache = {label: load([args.root / label]) for label in args.labels}
    for label in args.labels:
        results = cache[label]
        if not results:
            continue
        s = merge(results)
        if args.summary_dir:
            (args.summary_dir / f"summary-{label}.json").write_text(json.dumps(s, indent=1) + "\n")
        out.append(row(label, results, s))
        everything += results
        if not any(r.get("gate_level") for r in results if not r.get("infra_error")):
            rtl += results
    total = merge(everything)
    t = total["totals"]
    out.append(f"| **total** | | | {t.get('seeds', 0) + len(total['infra_errors']):,} seeds | | | "
               f"**{t.get('cases_run', 0):,}** | **{t.get('cases_passed', 0):,}** | **{t.get('cases_failed', 0)}** | "
               f"{len(total['infra_errors'])} | {t.get('programs_loaded', 0):,} + {t.get('programs_reloaded', 0):,} | "
               f"**{t.get('lockstep_cycles', 0):,}** | {total['instructions_completed']:,} | "
               f"{t.get('wall_s', 0) / 3600:.1f} | |")
    if args.summary_dir:
        (args.summary_dir / "summary-all.json").write_text(json.dumps(total, indent=1) + "\n")
    cov = total["coverage"]
    out.append("")
    out.append("Merged coverage, all campaigns (bins from `random_gen.Coverage`, counted on the model in lockstep):")
    out.append("")
    out.append("| opcode | " + " | ".join(f"engine {e}" for e in sorted(cov["executed"])) + " |")
    out.append("|---|" + "---:|" * len(cov["executed"]))
    for m in MNEMONIC:
        if m == "FAULT":
            continue
        out.append(f"| {m} | " + " | ".join(f"{cov['executed'][e].get(m, 0):,}" for e in sorted(cov["executed"])) + " |")
    out.append("")
    out.append("Stall cycles: " + ", ".join(f"{k} {cov['stalls'].get(k, 0):,}" for k in STALLS))
    out.append("")
    builtin = {k: v for k, v in cov["faults"].items() if not k.endswith("from FAULT")}
    explicit = {k: v for k, v in cov["faults"].items() if k.endswith("from FAULT")}
    out.append("Faults (code from instruction): " + ", ".join(f"{k} {v:,}" for k, v in sorted(builtin.items()))
               + f"; explicit `FAULT n`: {len(explicit)} distinct codes, {sum(explicit.values()):,} faults")
    out.append("")
    modes = [m for m in xfer_modes() if m in cov["xfer"]]
    counts = [cov["xfer"][m] for m in modes]
    out.append(f"XFER: {len(modes)}/32 CPOL/CPHA/bit-order/drive/sample combinations issued "
               f"(min {min(counts) if counts else 0:,}, max {max(counts) if counts else 0:,} per combination); "
               + ", ".join(f"{b} {cov['xfer'].get(b, 0):,}" for b in ("bits=1", "bits 2..width-1", "bits=width")))
    out.append("")
    out.append(f"Mover (DMA) word transfers: {cov['mover']:,}")
    out.append("")
    out.append("Host commands: " + ", ".join(f"{k} {v:,}" for k, v in sorted(cov["commands"].items())))
    out.append("")
    out.append("Host traffic: " + ", ".join(f"{k} {v:,}" for k, v in sorted(cov["host"].items())))
    out.append("")
    out.append("Other: " + ", ".join(f"{k} {v:,}" for k, v in sorted(cov["misc"].items())))
    out.append("")
    out.append("Holes (bins never hit), all campaigns: `" + json.dumps(total["holes"]) + "`")
    default = merge(cache[args.default_label]) if cache.get(args.default_label) else None
    if default:
        out.append("")
        out.append(f"Holes, upstream generator only (`{args.default_label}`): `" + json.dumps(default["holes"]) + "`")
    out.append("")
    out.append("Failures: " + (json.dumps(total["failures"], indent=1) if total["failures"] else "none"))
    # Extended cross coverage (xcov.py), per campaign that collected it.
    xlabels, xsums = [], []
    for label in args.labels:
        results = [r for r in cache[label] if r.get("xcov") is not None]
        if results:
            s = merge(results)
            xlabels.append((label, s["totals"].get("cases_run", 0)))
            xsums.append(s["xcov"]["bins"])
    if xlabels:
        out.append("")
        out.append("Extended cross coverage (`xcov.py`): hits per campaign, and hits per 1,000 cases in "
                   "parentheses; sorted by the rarest rate over all xcov campaigns.")
        out.append("")
        out.append("| bin | " + " | ".join(f"`{lab}` ({n:,} cases)" for lab, n in xlabels) + " |")
        out.append("|---|" + "---:|" * len(xlabels))
        from xcov import all_bins
        cases_total = sum(n for _, n in xlabels) or 1
        order = sorted(all_bins(), key=lambda b: sum(x.get(b, 0) for x in xsums) / cases_total)
        for b in order:
            cells = [f"{x.get(b, 0):,} ({1000 * x.get(b, 0) / n:.3g})" if n else "0" for x, (_, n) in
                     zip(xsums, xlabels, strict=True)]
            out.append(f"| {b} | " + " | ".join(cells) + " |")
        errors = {k: sum(x.get(k, 0) for x in xsums) for x in xsums for k in x if k.startswith("xcov-error")}
        out.append("")
        out.append(f"xcov observer errors: {errors or 'none'}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
