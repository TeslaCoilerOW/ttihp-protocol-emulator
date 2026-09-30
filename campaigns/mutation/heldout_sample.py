#!/usr/bin/env python3
"""Draw the held-out mutant sample of docs/mutation-push.md ("Held-out sample").

usage: heldout_sample.py SRC_DESIGN OUT_DIR --quotas REGION=N,... --seed S
                         --exclude FILE [--exclude FILE ...]

SRC_DESIGN  a design directory written by gen_mutants.sh (base.il, sel/<region>.txt)
OUT_DIR     receives db/<region>.txt.gz, mutations.tsv and heldout_params.json

The mutants come from the same generator as the campaign (Yosys `mutate` on the
campaign's prepared core base.il, one region selection sel/<region>.txt at a time),
but they are drawn uniformly instead of by mutate's coverage-weighted pick:

1. For region k of --quotas (in that order, k = 1, 2, ...; as in gen_mutants.sh),
   `mutate -list` with a count above the region's database size and seed S+k
   returns the region's whole raw mutation database: for every bit of every port
   of every selected cell the modes inv, const0 and const1, and cnot0 and cnot1
   with a control bit that mutate draws from its seeded generator (skipped when it
   equals the port bit or is a constant). The -cfg weights of gen_mutants.sh only
   steer mutate's reduction step, which does not run when the count exceeds the
   database, so they are not passed.
2. Every entry whose (mode, cell, port) equals the (mode, cell, port) of a mutant
   listed in an --exclude file (any line containing a `mutate -mode ...` command,
   e.g. a mutations.tsv) is removed, whatever its port bit or control bit.
3. N entries of the rest of region k are drawn uniformly without replacement with
   Python's random.Random(S + k).sample.
4. OUT_DIR/mutations.tsv numbers the drawn mutants 1..total (regions in --quotas
   order, database order within a region) after the no-op control 0, in the format
   of gen_mutants.sh, so run_mutant.py, submit.sh and push_summary.py take it as is.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
import tempfile
from pathlib import Path

YOSYS = os.path.join(os.environ["OSS_CAD_SUITE"], "bin", "yosys") if "OSS_CAD_SUITE" in os.environ else "yosys"
LIST_ALL = 100_000_000  # above any region's database size: mutate -list returns the whole database


def parse(cmd: str) -> dict:
    """Fields of a `mutate -mode ...` command line."""
    tok = cmd.split()
    out = {}
    i = tok.index("mutate") + 1
    while i < len(tok):
        if tok[i].startswith("-") and i + 1 < len(tok):
            key = tok[i][1:]
            if key == "src":
                out.setdefault("src", []).append(tok[i + 1])
            else:
                out[key] = tok[i + 1]
            i += 2
        else:
            i += 1
    return out


def key3(f: dict) -> tuple[str, str, str]:
    return f.get("mode", ""), f.get("cell", ""), f.get("port", "")


def key5(f: dict) -> tuple[str, str, str, str, str]:
    return f.get("mode", ""), f.get("cell", ""), f.get("port", ""), f.get("portbit", ""), f.get("ctrlbit", "")


def exclusions(files: list[Path]) -> tuple[set, set, int]:
    k3, k5, n = set(), set(), 0
    for path in files:
        for line in path.read_text().splitlines():
            if "mutate -mode" not in line or "-mode none" in line:
                continue
            f = parse(line[line.index("mutate -mode"):])
            k3.add(key3(f))
            k5.add(key5(f))
            n += 1
    return k3, k5, n


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src_design", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--quotas", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--exclude", type=Path, action="append", default=[])
    a = ap.parse_args()
    src = a.src_design.resolve()
    out = a.out.resolve()
    (out / "db").mkdir(parents=True, exist_ok=True)
    quotas = [(q.split("=")[0], int(q.split("=")[1])) for q in a.quotas.split(",")]
    ex3, ex5, n_ex = exclusions(a.exclude)

    params = {"src_design": str(src), "base_il_sha256": sha256(src / "base.il"),
              "core_orig_sha256": sha256(src / "core_orig.v"), "seed": a.seed,
              "quotas": a.quotas, "exclude_files": {str(p): sha256(p) for p in a.exclude},
              "excluded_mutants": n_ex, "excluded_keys_mode_cell_port": len(ex3),
              "python": platform.python_version(),
              "yosys": subprocess.run([YOSYS, "-V"], capture_output=True, text=True).stdout.strip(),
              "regions": {}}
    rows = []
    for k, (region, n) in enumerate(quotas, start=1):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or None) as tmp:
            lst = Path(tmp) / "list.txt"
            script = (f"read_rtlil {src / 'base.il'}; select -read {src / 'sel' / (region + '.txt')}; "
                      f"mutate -list {LIST_ALL} -seed {a.seed + k} -o {lst}")
            subprocess.run([YOSYS, "-q", "-p", script], check=True, cwd=tmp,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            lines = lst.read_text().splitlines()
            db_path = out / "db" / f"{region}.txt.gz"
            with gzip.open(db_path, "wt") as fh:
                fh.write("".join(line + "\n" for line in lines))
        fields = [parse(line) for line in lines]
        keep = [i for i, f in enumerate(fields) if key3(f) not in ex3]
        same5 = sum(1 for f in fields if key5(f) in ex5)
        if n > len(keep):
            raise SystemExit(f"{region}: {n} requested, {len(keep)} available")
        drawn = sorted(random.Random(a.seed + k).sample(keep, n))
        rows += [(region, lines[i]) for i in drawn]
        modes = {}
        for i in drawn:
            modes[fields[i]["mode"]] = modes.get(fields[i]["mode"], 0) + 1
        params["regions"][region] = {
            "k": k, "mutate_seed": a.seed + k, "sample_seed": a.seed + k, "database": len(lines),
            "excluded_mode_cell_port": len(lines) - len(keep), "same_mode_cell_port_bit_ctrl": same5,
            "population": len(keep), "drawn": n, "drawn_by_mode": dict(sorted(modes.items())),
            "database_gz_sha256": sha256(db_path),
            "database_text_sha256": hashlib.sha256("".join(line + "\n" for line in lines).encode()).hexdigest()}
        print(region, params["regions"][region], flush=True)

    tsv = out / "mutations.tsv"
    tsv.write_text("0\tnone\tmutate -mode none\n"
                   + "".join(f"{i}\t{r}\t{c}\n" for i, (r, c) in enumerate(rows, start=1)))
    params["mutants"] = len(rows)
    params["database_total"] = sum(v["database"] for v in params["regions"].values())
    params["population_total"] = sum(v["population"] for v in params["regions"].values())
    params["mutations_tsv_sha256"] = sha256(tsv)
    (out / "heldout_params.json").write_text(json.dumps(params, indent=1) + "\n")
    print(json.dumps({k: v for k, v in params.items() if k != "regions"}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
