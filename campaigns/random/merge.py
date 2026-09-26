#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Merge per-seed campaign results (results/seed-*.json) and list coverage holes.

  merge.py <campaign_dir> [<campaign_dir> ...] [--json out.json] [--md out.md]

Each campaign directory holds results/seed-XXXXXXXX.json written by
campaign_random.py (or an infra-error stub written by run_task.sh). Coverage bins
are the ones Coverage in test/random_gen.py collects; "holes" are bins of the
enumerable spaces below that no seed hit.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from xcov import all_bins as xcov_bins

MNEMONIC = ["NOP", "HALT", "SET", "DIR", "WAIT", "JMP", "PULL", "PUSH", "OUT", "IN", "COUNT", "LOOP",
            "LIMIT", "WAITPIN", "SIGNAL", "WAITEVENT", "PINS", "XFER", "MOV", "LOAD", "ADD", "XOR", "AND",
            "OR", "SHL", "SHR", "JZ", "NOT", "TIME", "FAULT"]
COMMANDS = ["SELECT", "BEGIN", "COMMIT", "OWN", "START", "STOP", "ROUTE", "CLEAR", "READ_SELECT", "EVENT",
            "FLUSH", "TRIGGER"]
STALLS = ["PULL", "PUSH", "WAITPIN", "WAITEVENT"]
# Fault bins the generator aims at (random_gen.ProgramGenerator.bad + blocking timeouts/overflow).
FAULT_BINS = (["code 1 from INVALID", "code 1 from PUSH", "code 1 from OUT", "code 1 from SHL",
               "code 1 from LIMIT", "code 1 from XFER", "code 1 from MOV", "code 1 from FAULT",
               "code 1 from SIGNAL", "code 1 from NOP", "code 1 from PINS", "code 1 from SET",
               "code 1 from DIR", "code 2 from PC-RANGE", "code 3 from WAITPIN", "code 3 from WAITEVENT",
               "code 4 from PUSH"])
HOST_BINS = (["tx word accepted", "tx write abandoned (full)", "rx word read", "rx read abandoned (empty)"]
             + [f"status read select={s}" for s in range(8)]
             + [f"partial write abandoned (window {w})" for w in range(3)]
             + [f"partial read abandoned (window {w})" for w in (0, 3)]
             + ["engine reprogrammed", "revive: cleared fault", "revive: restarted", "deselect (ena low)"])
MISC_BINS = ["cycles with IRQ asserted", "cycles with fault output asserted", "cycles with any pin driven",
             "engine starts"]
# Bins that only exist for some results: generator generation 2 (random_gen.GENERATION) and the
# restricted-ISA design variants (test/variants.py: byte-lane shifts, 7-bit saturating PC).
# They join the hole lists only when a merged result says it was run that way.
HOST_BINS_GEN2 = ["program word written", "program word not accepted"]
MISC_BINS_BYTE_LANE = ["byte-lane shift count faults (code 1)"]
FAULT_BINS_BYTE_LANE = ["code 1 from SHR"]
MISC_BINS_SATURATING_PC = ["jump targets saturated to PC 127"]


def extra_bins(results: list[dict]) -> dict[str, list[str]]:
    """Host, misc and fault bins that the merged results' generator generation and design variant can hit."""
    extra: dict[str, list[str]] = {"host": [], "misc": [], "faults": []}
    if any(int(r.get("generation") or 1) >= 2 for r in results):
        extra["host"] += HOST_BINS_GEN2
    options = [r.get("model_options") or {} for r in results]
    if any(isinstance(o, dict) and o.get("shift") == "byte_lane" for o in options):
        extra["misc"] += MISC_BINS_BYTE_LANE
        extra["faults"] += FAULT_BINS_BYTE_LANE
    if any(isinstance(o, dict) and o.get("pc_bits") == "saturating_7" for o in options):
        extra["misc"] += MISC_BINS_SATURATING_PC
    return extra


def xfer_modes() -> list[str]:
    return [f"CPOL{c & 1} CPHA{c >> 1 & 1} {'MSB' if c & 4 else 'LSB'}-first drive={c >> 3 & 1} sample={c >> 4 & 1}"
            for c in range(32)]


def load(dirs: list[Path]) -> list[dict]:
    results = []
    for d in dirs:
        for path in sorted((d / "results").glob("seed-*.json")):
            data = json.loads(path.read_text())
            data["_path"] = str(path)
            results.append(data)
    return results


def merge(results: list[dict]) -> dict:
    ok = [r for r in results if not r.get("infra_error")]
    infra = [r for r in results if r.get("infra_error")]
    cov: dict = {"executed": {}, "stalls": Counter(), "faults": Counter(), "deliberate": Counter(),
                 "xfer": Counter(), "commands": Counter(), "host": Counter(), "misc": Counter(), "mover": 0,
                 "cases": 0, "cycles": 0}
    xcov: Counter = Counter()
    xcov_seeds = 0
    case_cycles, failures, mover_zero = [], [], 0
    totals = Counter()
    for r in ok:
        c = r["coverage"]
        for engine, counts in c["executed"].items():
            cov["executed"].setdefault(engine, Counter()).update(counts)
        for key in ("stalls", "faults", "deliberate", "xfer", "commands", "host", "misc"):
            cov[key].update(c[key])
        if r.get("xcov") is not None:
            xcov.update(r["xcov"])
            xcov_seeds += 1
        cov["mover"] += c["mover"]
        mover_zero += c["mover"] == 0
        cov["cases"] += c["cases"]
        cov["cycles"] += c["cycles"]
        totals["seeds"] += 1
        totals["cases_run"] += r["cases_run"]
        totals["cases_passed"] += r["cases_passed"]
        totals["cases_failed"] += r["cases_failed"]
        totals["lockstep_cycles"] += r["lockstep_cycles"]
        totals["wall_s"] += r["wall_s"]
        totals["seeds_all_pass"] += r["cases_failed"] == 0
        for case in r["cases"]:
            case_cycles.append(case["cycles"])
            totals["programs_loaded"] += case["programs_loaded"]
            totals["programs_reloaded"] += case["reloads_executed"]
            totals["program_words_loaded"] += case["program_words"]
            if case["status"] == "fail":
                failures.append({"label": r["label"], "seed": r["seed"], "case": case["index"],
                                 "kind": case["kind"], "after_failure": case["after_failure"],
                                 "first_line": case["message"].splitlines()[0] if case["message"] else "",
                                 "case_json": case.get("case_json"), "result": r["_path"]})
    instr = sum(sum(v.values()) for v in cov["executed"].values())
    all_ops = [m for m in MNEMONIC if m != "FAULT"]
    extra = extra_bins(ok)
    holes = {
        "opcode_x_engine": {e: [m for m in all_ops if m not in cov["executed"].get(e, {})]
                            for e in sorted(cov["executed"])},
        "engines_without_any_completion": [str(e) for e in range(4) if str(e) not in cov["executed"]],
        "stall_kinds": [s for s in STALLS if s not in cov["stalls"]],
        "fault_bins": [b for b in FAULT_BINS + extra["faults"] if b not in cov["faults"]],
        "xfer_flag_combinations": [m for m in xfer_modes() if m not in cov["xfer"]],
        "xfer_bit_counts": [b for b in ("bits=1", "bits 2..width-1", "bits=width") if b not in cov["xfer"]],
        "host_commands": [f"{c} {a}" for c in COMMANDS for a in ("accepted", "rejected")
                          if f"{c} {a}" not in cov["commands"]],
        "host_traffic": [b for b in HOST_BINS + extra["host"] if b not in cov["host"]],
        "misc": [b for b in MISC_BINS + extra["misc"] if b not in cov["misc"]],
    }
    if xcov_seeds:
        holes["xcov"] = [b for b in xcov_bins() if b not in xcov]
    explicit_codes = sorted({int(k.split()[1]) for k in cov["faults"] if k.endswith("from FAULT")})
    summary = {
        "totals": dict(totals), "infra_errors": [{k: r.get(k) for k in ("label", "seed", "kind", "rc", "host")}
                                                 for r in infra],
        "instructions_completed": instr,
        "case_cycles": ({"min": min(case_cycles), "median": statistics.median(case_cycles),
                         "max": max(case_cycles)} if case_cycles else {}),
        "labels": sorted({r["label"] for r in results}),
        **({"design_variants": sorted({r["design_variant"] for r in ok if "design_variant" in r}),
            "generations": sorted({r["generation"] for r in ok if "generation" in r})}
           if any("design_variant" in r for r in ok) else {}),
        "seed_range": [min(r["seed"] for r in results), max(r["seed"] for r in results)] if results else [],
        "slurm_array_jobs": sorted({r.get("slurm_array_job") or r.get("slurm_job", "") for r in results}),
        "seeds_with_zero_mover_transfers": mover_zero,
        "failures": failures,
        "coverage": {k: (dict(v) if isinstance(v, Counter) else
                         {e: dict(c) for e, c in v.items()} if k == "executed" else v) for k, v in cov.items()},
        "explicit_fault_codes_hit": {"distinct": len(explicit_codes), "min": explicit_codes[0] if explicit_codes else None,
                                     "max": explicit_codes[-1] if explicit_codes else None},
        "holes": holes,
        "xcov": {"seeds": xcov_seeds, "bins": dict(sorted(xcov.items()))} if xcov_seeds else None,
    }
    return summary


def markdown(s: dict) -> str:
    t = s["totals"]
    lines = [f"| labels | {', '.join(s['labels'])} |", "|---|---|",
             f"| seeds (result files) | {t.get('seeds', 0)} (+{len(s['infra_errors'])} infra errors) |",
             f"| cases run / passed / failed | {t.get('cases_run', 0)} / {t.get('cases_passed', 0)} / "
             f"{t.get('cases_failed', 0)} |",
             f"| programs loaded (+ reloads) | {t.get('programs_loaded', 0)} (+{t.get('programs_reloaded', 0)}) |",
             f"| lockstep cycles | {t.get('lockstep_cycles', 0):,} |",
             f"| instructions completed (model) | {s['instructions_completed']:,} |",
             f"| mover (DMA) word transfers | {s['coverage']['mover']:,} |",
             f"| simulator wall time | {t.get('wall_s', 0) / 3600:.1f} CPU-h |"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--md", type=Path)
    args = ap.parse_args()
    s = merge(load(args.dirs))
    if args.json:
        args.json.write_text(json.dumps(s, indent=1) + "\n")
    if args.md:
        args.md.write_text(markdown(s) + "\n")
    brief = {k: s[k] for k in ("totals", "infra_errors", "instructions_completed", "case_cycles",
                               "seeds_with_zero_mover_transfers", "explicit_fault_codes_hit", "holes")}
    brief["failures"] = s["failures"][:20]
    brief["n_failures"] = len(s["failures"])
    print(json.dumps(brief, indent=1))


if __name__ == "__main__":
    main()
