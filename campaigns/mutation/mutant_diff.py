#!/usr/bin/env python3
"""Print the netlist change of a mutant: its assignments that differ from the unmutated core.

usage: mutant_diff.py DESIGN_DIR ID [ID ...]   (or --ids-file FILE) [--out DIR] [--jobs N]

For each mutant: `read_rtlil base.il; <mutate command>; write_verilog -noattr`,
then the `assign` and `<=` lines of the result are compared with those of
DESIGN_DIR/base_roundtrip.v, after dropping Yosys function bodies and
renaming the numbered function names (_NNNNN_) to one placeholder, so only the
changed assignments remain. With --out DIR, DIR/<id>.diff gets the output.
This is the survivor-diff step of docs/mutation-push.md (section 2; the work
directory's mkdiffs.sh + cleandiff.py). Uses $OSS_CAD_SUITE/bin/yosys when set.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OSS = os.environ.get("OSS_CAD_SUITE", "")
YOSYS = os.path.join(OSS, "bin", "yosys") if OSS else "yosys"
FUNC = re.compile(r"_\d{5}_")


def assigns(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        if "function" in line or "endfunction" in line or re.search(r"_\d*_ = ", line):
            continue
        if "assign" in line or "<=" in line:
            out.append(line.strip())
    return out


def diff_one(design: Path, mid: str, cmd: str, gold: list[str]) -> str:
    with tempfile.TemporaryDirectory(prefix=f"pe-diff-{mid}-") as tmp:
        mv = Path(tmp) / "m.v"
        mut = "" if "-mode none" in cmd else cmd + "; "
        subprocess.run([YOSYS, "-q", "-p", f"read_rtlil {design / 'base.il'}; {mut}write_verilog -noattr {mv}"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        new = assigns(mv.read_text())
    gn = {FUNC.sub("_F_", x) for x in gold}
    nn = {FUNC.sub("_F_", x) for x in new}
    lines = [f"#### {mid}: {cmd}"]
    lines += [f"   - {x[:230]}" for x in gold if FUNC.sub("_F_", x) not in nn]
    lines += [f"   + {x[:230]}" for x in new if FUNC.sub("_F_", x) not in gn]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("design", type=Path)
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--ids-file", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--jobs", type=int, default=1)
    a = ap.parse_args()
    cmds = {}
    for line in (a.design / "mutations.tsv").read_text().splitlines():
        mid, _, cmd = line.split("\t", 2)
        cmds[mid] = cmd
    ids = list(a.ids) + (a.ids_file.read_text().split() if a.ids_file else [])
    gold = assigns((a.design / "base_roundtrip.v").read_text())
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(a.jobs, 1)) as pool:
        for mid, text in zip(ids, pool.map(lambda m: diff_one(a.design, m, cmds[m], gold), ids)):
            if a.out:
                (a.out / f"{mid}.diff").write_text(text)
            else:
                print(text, end="")


if __name__ == "__main__":
    main()
