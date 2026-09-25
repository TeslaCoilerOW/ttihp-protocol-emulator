#!/usr/bin/env python3
"""Aggregate the mutation campaign result files into a summary.

usage: summarize.py CAMP_DIR [--write-survivors FILE] [--json OUT.json]

Reads CAMP_DIR/design/mutations.tsv and CAMP_DIR/results/{fast,full,deep,equiv,directed}/<id>.json.
Mutant 0 ("none", the Yosys round-trip of the unmodified core) and "orig" are
controls and are excluded from the counts. A mutant's final status is:

  killed_fast   a test of the fast set failed
  killed_full   survived the fast set, a test of the full 39-test suite failed
  timeout       the fast or full stage exceeded its time budget (counted separately)
  error         build or simulator error without a test verdict
  survived      passed the fast set and the full suite
  pending       not run yet

Mutation score = killed / (total - proven equivalent), where killed counts
killed_fast + killed_full (timeouts and errors are not counted as killed), and
"proven equivalent" is a survivor whose results/equiv/<id>.json reports a
passing formal equivalence proof.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def load(d: Path) -> dict[str, dict]:
    out = {}
    if d.exists():
        for f in d.glob("*.json"):
            try:
                r = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            out[str(r["id"])] = r
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("camp", type=Path)
    ap.add_argument("--write-survivors", type=Path, help="ids that survived the fast set (input to the full stage)")
    ap.add_argument("--write-final-survivors", type=Path, help="ids that survived the full suite")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--status-tsv", type=Path, help="one row per mutant: id, region, mode, verdicts per stage")
    args = ap.parse_args()

    muts = {}
    for line in (args.camp / "design" / "mutations.tsv").read_text().splitlines():
        mid, region, cmd = line.split("\t", 2)
        mode = cmd.split("-mode ", 1)[1].split()[0]
        muts[mid] = {"region": region, "mode": mode}
    fast, full, deep, equiv, directed = (load(args.camp / "results" / s)
                                         for s in ("fast", "full", "deep", "equiv", "directed"))

    final = {}
    for mid in muts:
        if mid == "0":
            continue
        f = fast.get(mid)
        if f is None:
            final[mid] = "pending"
        elif f["status"] == "killed":
            final[mid] = "killed_fast"
        elif f["status"] in ("timeout", "error"):
            final[mid] = f["status"]
        else:
            g = full.get(mid)
            if g is None:
                final[mid] = "pending_full"
            elif g["status"] == "killed":
                final[mid] = "killed_full"
            elif g["status"] in ("timeout", "error"):
                final[mid] = g["status"]
            else:
                final[mid] = "survived"

    total = len(final)
    counts = collections.Counter(final.values())
    proven_eq = sorted((m for m, s in final.items() if s == "survived"
                        and equiv.get(m, {}).get("status") == "equivalent"), key=int)
    killed = counts["killed_fast"] + counts["killed_full"]
    denom = total - len(proven_eq)
    by_region = collections.defaultdict(collections.Counter)
    by_mode = collections.defaultdict(collections.Counter)
    killer_module = collections.Counter()
    killer_test = collections.Counter()
    for mid, s in final.items():
        by_region[muts[mid]["region"]][s] += 1
        by_mode[muts[mid]["mode"]][s] += 1
        src = fast.get(mid) if s == "killed_fast" else full.get(mid) if s == "killed_full" else None
        if src and src.get("killed_by"):
            killer_module[("fast: " if s == "killed_fast" else "full: ") + src["killed_by"]["module"]] += 1
            killer_test[src["killed_by"]["module"] + "." + src["killed_by"]["test"]] += 1
    deep_killed = sorted((m for m, s in final.items() if s == "survived"
                          and deep.get(m, {}).get("status") == "killed"), key=int)
    deep_run = sorted((m for m, s in final.items() if s == "survived" and m in deep), key=int)

    directed_killed = sorted((m for m, s in final.items() if s == "survived"
                              and directed.get(m, {}).get("status") == "killed"), key=int)
    extra = sorted(set(deep_killed) | set(directed_killed), key=int)

    def secs(rs, status):
        v = sorted(r["seconds"] for r in rs.values() if r.get("status") == status and r["id"] not in ("0", "orig"))
        return {"n": len(v), "median": v[len(v) // 2] if v else None, "max": v[-1] if v else None,
                "sum_hours": round(sum(v) / 3600, 2)}

    summary = {
        "total_mutants": total,
        "status_counts": dict(counts),
        "killed": killed,
        "proven_equivalent": len(proven_eq),
        "proven_equivalent_ids": proven_eq,
        "mutation_score": round(killed / denom, 4) if denom else None,
        "mutation_score_formula": f"{killed} / ({total} - {len(proven_eq)})",
        "by_region": {r: dict(c) for r, c in sorted(by_region.items())},
        "by_mode": {r: dict(c) for r, c in sorted(by_mode.items())},
        "killed_by_module": dict(killer_module.most_common()),
        "killed_by_test_top": dict(killer_test.most_common(25)),
        "deep_random_run_on_survivors": len(deep_run),
        "deep_random_killed": deep_killed,
        "directed_run_on_survivors": sum(1 for m, s in final.items() if s == "survived" and m in directed),
        "directed_killed": directed_killed,
        "directed_killed_not_by_deep": sorted(set(directed_killed) - set(deep_killed), key=int),
        "extended_killed": len(extra),
        "extended_score": round((killed + len(extra)) / denom, 4) if denom else None,
        "extended_score_formula": f"({killed} + {len(extra)}) / ({total} - {len(proven_eq)})"
                                  " -- fast/full kills plus survivors killed by the deep random"
                                  " stage or the directed tests",
        "controls": {s: {k: (d.get(k) or {}).get("status") for k in ("0", "orig")}
                     for s, d in (("fast", fast), ("full", full), ("directed", directed))},
        "runtime_seconds": {"fast_killed": secs(fast, "killed"), "fast_survived": secs(fast, "survived"),
                            "full_killed": secs(full, "killed"), "full_survived": secs(full, "survived")},
        "slurm_array_jobs": {s: dict(collections.Counter(r["slurm_array"].split("_")[0] for r in d.values()
                                                         if r.get("slurm_array", "None_None") != "None_None"))
                             for s, d in (("fast", fast), ("full", full), ("deep", deep), ("equiv", equiv),
                                         ("directed", directed))},
    }
    if args.write_survivors:
        ids = sorted((m for m in final if fast.get(m, {}).get("status") == "survived"), key=int)
        args.write_survivors.write_text("\n".join(ids) + "\n")
        summary["fast_survivor_ids_written"] = len(ids)
    if args.write_final_survivors:
        ids = sorted((m for m, s in final.items() if s == "survived"), key=int)
        args.write_final_survivors.write_text("\n".join(ids) + "\n")
        summary["final_survivor_ids_written"] = len(ids)
    if args.status_tsv:
        rows = ["id\tregion\tmode\tfinal\tfast\tfull\tequiv\tdeep\tdirected"]
        for mid in sorted(final, key=int):
            st = lambda d: (d.get(mid) or {}).get("status", "-")
            rows.append("\t".join([mid, muts[mid]["region"], muts[mid]["mode"], final[mid], st(fast), st(full),
                                   st(equiv), st(deep), st(directed)]))
        args.status_tsv.write_text("\n".join(rows) + "\n")
    text = json.dumps(summary, indent=1)
    if args.json:
        args.json.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
