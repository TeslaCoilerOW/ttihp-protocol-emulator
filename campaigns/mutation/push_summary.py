#!/usr/bin/env python3
"""Combine simulation stages and formal classifications into one mutation score.

usage: push_summary.py DESIGN_DIR --sim NAME=DIR [--sim NAME=DIR ...]
                       [--proof NAME=DIR ...] [--json OUT] [--status-tsv OUT]
                       [--write-survivors FILE] [--write-unclassified FILE]

This is the bookkeeping of docs/mutation-push.md (the work-directory script
summary97.py, generalised): every mutant of DESIGN_DIR/mutations.tsv (id 0, the
no-op control, and "orig" are excluded from the counts) gets one status:

  killed      a --sim stage (run_mutant.py result directory) reports "killed";
              the first such stage, in command-line order, is recorded
  equivalent  not killed, and a --proof directory (equiv_mutant.py,
              formal_mutant.py or equiv_inv.py results) reports "equivalent";
              the first such method is recorded
  survived    neither (after every --sim stage that has a result for it)
  timeout / error / missing   the first --sim stage has no verdict for it

Score = killed / (mutants - equivalent). A mutant that a proof calls equivalent
but a test kills is counted as killed and listed under "conflicts" (a soundness
alarm; expected empty). Controls are listed per stage.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def load(d: Path) -> dict[str, dict]:
    out = {}
    for f in d.glob("*.json"):
        try:
            r = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        out[str(r["id"])] = r
    return out


def pairs(items: list[str]) -> list[tuple[str, Path]]:
    out = []
    for it in items:
        name, _, path = it.partition("=")
        out.append((name, Path(path)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("design", type=Path)
    ap.add_argument("--sim", action="append", default=[], help="NAME=DIR, simulation stage results, in order")
    ap.add_argument("--proof", action="append", default=[], help="NAME=DIR, formal results, in order")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--status-tsv", type=Path)
    ap.add_argument("--write-survivors", type=Path, help="ids neither killed nor proven equivalent")
    ap.add_argument("--write-unclassified", type=Path, help="ids without a verdict in the first --sim stage")
    a = ap.parse_args()

    muts = {}
    for line in (a.design / "mutations.tsv").read_text().splitlines():
        mid, region, cmd = line.split("\t", 2)
        if mid != "0":
            muts[mid] = {"region": region, "mode": cmd.split("-mode ", 1)[1].split()[0]}
    sims = [(n, load(d)) for n, d in pairs(a.sim)]
    proofs = [(n, load(d)) for n, d in pairs(a.proof)]
    if not sims:
        raise SystemExit("at least one --sim stage is needed")

    status, how, conflicts = {}, {}, []
    for mid in muts:
        first = sims[0][1].get(mid)
        if first is None or first["status"] in ("timeout", "error"):
            status[mid] = "missing" if first is None else first["status"]
            how[mid] = sims[0][0]
            continue
        kill = next(((n, r[mid]) for n, r in sims if r.get(mid, {}).get("status") == "killed"), None)
        proof = next((n for n, r in proofs if r.get(mid, {}).get("status") == "equivalent"), None)
        if kill:
            n, r = kill
            status[mid] = "killed"
            how[mid] = f"{n}:" + ",".join(r.get("killed_modules") or [r["killed_by"]["module"]])
            if proof:
                conflicts.append({"id": mid, "killed": how[mid], "proof": proof})
        elif proof:
            status[mid], how[mid] = "equivalent", proof
        else:
            status[mid], how[mid] = "survived", ""

    c = collections.Counter(status.values())
    n = len(muts)
    eq = c["equivalent"]
    score = c["killed"] / (n - eq) if n > eq else 0.0
    by_region = collections.defaultdict(collections.Counter)
    by_mode = collections.defaultdict(collections.Counter)
    for mid, s in status.items():
        for table, key in ((by_region, muts[mid]["region"]), (by_mode, muts[mid]["mode"])):
            table[key][s] += 1
            table[key]["mutants"] += 1
    first_name = sims[0][0]
    first_module = collections.Counter(
        how[m].split(":", 1)[1].split(",")[0] for m in muts if status[m] == "killed" and how[m].startswith(first_name + ":"))
    summary = {
        "design": str(a.design),
        "sim_stages": [nm for nm, _ in sims],
        "proof_methods": [nm for nm, _ in proofs],
        "mutants": n,
        "killed": c["killed"],
        "equivalent": eq,
        "survived": c["survived"],
        "timeout": c["timeout"], "error": c["error"], "missing": c["missing"],
        "score": round(score, 4),
        "score_formula": f"{c['killed']} / ({n} - {eq})",
        "killed_by_stage": dict(collections.Counter(how[m].split(":", 1)[0] for m in muts if status[m] == "killed")),
        "killed_first_stage_by_module": dict(first_module.most_common()),
        "equivalent_by_method": dict(collections.Counter(how[m] for m in muts if status[m] == "equivalent")),
        "conflicts": conflicts,
        "controls": {nm: {cid: r[cid]["status"] for cid in ("orig", "0") if cid in r} for nm, r in sims},
        "survivors": sorted((m for m in muts if status[m] == "survived"), key=int),
        "by_region": {k: dict(v) for k, v in sorted(by_region.items())},
        "by_mode": {k: dict(v) for k, v in sorted(by_mode.items())},
    }
    if a.json:
        a.json.write_text(json.dumps(summary, indent=1) + "\n")
    if a.status_tsv:
        rows = ["id\tregion\tmode\tstatus\thow"]
        rows += [f"{m}\t{muts[m]['region']}\t{muts[m]['mode']}\t{status[m]}\t{how[m]}" for m in sorted(muts, key=int)]
        a.status_tsv.write_text("\n".join(rows) + "\n")
    if a.write_survivors:
        a.write_survivors.write_text("".join(m + "\n" for m in summary["survivors"]))
    if a.write_unclassified:
        a.write_unclassified.write_text(
            "".join(m + "\n" for m in sorted(muts, key=int) if status[m] in ("missing", "timeout", "error")))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("survivors", "by_region", "by_mode")}, indent=1))
    for r, v in sorted(by_region.items()):
        print(r, dict(v))


if __name__ == "__main__":
    main()
