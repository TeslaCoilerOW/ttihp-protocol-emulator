#!/usr/bin/env python3
"""Group surviving mutants by the state they feed and by what the RIP run saw.

usage: survivor_classes.py DESIGN_DIR --ids-file FILE [--rip DIR] [--tsv OUT] [--json OUT]

For each id: region and mode (mutations.tsv); the nearest named state of the
mutated statement (describe.py, which follows the statement's forward cone
through anonymous signals to named registers, memories, SRAM pins or outputs),
reduced to register families (per-engine suffixes _0.._2 removed; anonymous
registers of the round-trip netlist, such as queue words, reported as
"anonymous"); and, with --rip (results of
run_mutant.py stage kill-rip), the reach/infect/propagate outcome:

  not infected   no register, queue word, SRAM pin net or output of the mutant ever
                 differed from the unmutated core's during the run
  infected       some register differed; the families that differed are listed
  output         an output differed as well (the test still passed)
  incomplete     a test failed on the wrapper (the unmutated core drives the pins,
                 so every test must pass); the run does not cover that test

This is bookkeeping for a hand classification, not an argument: a survivor
that was never infected is only unexercised by the suite, not unobservable.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def nearest_states(design: Path, ids: list[str]) -> dict[str, list[str]]:
    """{id: nearest named state names} from describe.py's output."""
    text = subprocess.run([sys.executable, str(HERE / "describe.py"), str(design), *ids],
                          check=True, capture_output=True, text=True).stdout
    out = {}
    for block in re.split(r"(?m)^== mutant ", text)[1:]:
        mid = block.split()[0]
        names = []
        for lst in re.findall(r"nearest named state: (\[.*?\])", block):
            names += ast.literal_eval(lst)
        out[mid] = names
    return out


def family(name: str) -> str:
    name = name.split("[")[0]
    if name.startswith("inst:"):
        return "sram_pins"
    if re.fullmatch(r"_\d+", name):
        return "anonymous"
    return re.sub(r"_\d$", "", name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("design", type=Path)
    ap.add_argument("--ids-file", type=Path, required=True)
    ap.add_argument("--rip", type=Path)
    ap.add_argument("--tsv", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    ids = a.ids_file.read_text().split()
    muts = {}
    for line in (a.design / "mutations.tsv").read_text().splitlines():
        mid, region, cmd = line.split("\t", 2)
        muts[mid] = (region, cmd.split("-mode ", 1)[1].split()[0])
    near = nearest_states(a.design, ids)
    rows, classes = [], collections.Counter()
    for mid in ids:
        region, mode = muts[mid]
        fams = sorted({family(n) for n in near.get(mid, [])}) or ["-"]
        rip = "-"
        if a.rip:
            f = a.rip / f"{mid}.json"
            if f.exists():
                r = json.loads(f.read_text())
                div = r.get("rip", {}).get("diverged", {})
                if r.get("status") != "ok":
                    rip = r.get("status", "?")
                elif r.get("tests_failed"):  # the gold core drives the pins: every test must pass
                    rip = f"incomplete: {r['tests_failed']} tests failed on the wrapper"
                    print(f"warning: {mid}: {rip}", file=sys.stderr)
                elif not div:
                    rip = "not infected"
                else:
                    regs = sorted({family(k) for k in div if not k.startswith("out:")})
                    outs = sorted(k for k in div if k.startswith("out:"))
                    rip = "infected: " + ",".join(regs) + (" | output: " + ",".join(outs) if outs else "")
        key = ("/".join(fams), rip.split(":")[0])
        classes[key] += 1
        rows.append({"id": mid, "region": region, "mode": mode, "state": ",".join(fams), "rip": rip})
    if a.tsv:
        a.tsv.write_text("id\tregion\tmode\tstate\trip\n" +
                         "".join(f"{r['id']}\t{r['region']}\t{r['mode']}\t{r['state']}\t{r['rip']}\n" for r in rows))
    if a.json:
        a.json.write_text(json.dumps({"rows": rows, "classes": [
            {"state": s, "rip": k, "count": n} for (s, k), n in classes.most_common()]}, indent=1) + "\n")
    for (s, k), n in classes.most_common():
        print(f"{n:4d}  {s:40s}  {k}")


if __name__ == "__main__":
    main()
