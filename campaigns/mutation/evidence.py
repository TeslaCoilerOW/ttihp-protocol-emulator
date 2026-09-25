#!/usr/bin/env python3
"""Merge the per-survivor evidence (equivalence proof, RIP analysis, deep random).

usage: evidence.py CAMP_DIR IDS_FILE [--json OUT] [--tsv OUT]

For every id: region, mutation, formal equivalence status, the RIP verdict
(state never diverged / diverged internally only / reached a pin), the
registers that diverged, and the deep random stage verdict.

RIP verdicts (full 39-test suite, unmutated core driving the pins):
  not_activated   no register, FIFO word or SRAM pin net ever differed
  internal_only   some state differed, the four output groups never did
  reached_pins    an output group differed (only possible where the harness
                  does not compare, e.g. read nibbles while read-valid is low)
"""
import argparse
import json
from pathlib import Path


def load(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("camp", type=Path)
    ap.add_argument("ids", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--tsv", type=Path)
    a = ap.parse_args()
    muts = {}
    for line in (a.camp / "design" / "mutations.tsv").read_text().splitlines():
        mid, region, cmd = line.split("\t", 2)
        muts[mid] = (region, cmd)
    rows = []
    for mid in a.ids.read_text().split():
        region, cmd = muts[mid]
        opts = cmd.split()
        get = lambda k: opts[opts.index(k) + 1] if k in opts else "-"
        row = {"id": mid, "region": region, "mode": get("-mode"), "cell": get("-cell"),
               "port": f"{get('-port')}[{get('-portbit')}]", "ctrlbit": get("-ctrlbit")}
        eq = load(a.camp / "results" / "equiv" / f"{mid}.json")
        row["equiv"] = eq["status"] if eq else "pending"
        deep = load(a.camp / "results" / "deep" / f"{mid}.json")
        row["deep"] = deep["status"] if deep else "pending"
        d = load(a.camp / "results" / "directed" / f"{mid}.json")
        row["directed"] = d["status"] if d else "pending"
        if d and d.get("failed_tests"):
            row["directed_tests"] = d["failed_tests"]
        rip = load(a.camp / "results" / "rip" / f"{mid}.json")
        if rip and rip.get("status") == "ok":
            div = rip["rip"]["diverged"]
            outs = {k: v for k, v in div.items() if k.startswith("out:")}
            state = {k: v for k, v in div.items() if not k.startswith("out:")}
            row["rip"] = "reached_pins" if outs else "internal_only" if state else "not_activated"
            row["rip_state"] = dict(sorted(state.items(), key=lambda kv: -kv[1])[:6])
            row["rip_outputs"] = outs
            row["rip_first"] = {k: rip["rip"]["first"].get(k) for k in list(row["rip_state"])[:3]}
            row["rip_tests_failed"] = rip.get("tests_failed")
        else:
            row["rip"] = rip["status"] if rip else "pending"
        rows.append(row)
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1) + "\n")
    lines = ["id\tregion\tmode\tport\tequiv\trip\tdeep\tdirected\tdiverged_state\toutputs"]
    for r in rows:
        lines.append("\t".join([r["id"], r["region"], r["mode"], r["port"], r["equiv"], r["rip"], r["deep"], r["directed"],
                                ",".join(f"{k}:{v}" for k, v in r.get("rip_state", {}).items()),
                                ",".join(f"{k}:{v}" for k, v in r.get("rip_outputs", {}).items())]))
    text = "\n".join(lines) + "\n"
    if a.tsv:
        a.tsv.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
