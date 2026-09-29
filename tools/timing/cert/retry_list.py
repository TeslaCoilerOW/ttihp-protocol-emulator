#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Runs to repeat after a batch of run_one.sh runs (campaign.sh retry, ci_prove.sh).

Usage: retry_list.py [--missing] TASKS...

TASKS are task lists as campaign.sh and ci_prove.sh write them, one run per
line: "CERTDIR NAME TASK [ENGINE]". The result files of a run are
CERTDIR/results/NAME.TASK*.json (run_one.sh). Printed, one per line:

- a run whose result files all have status ERROR: the same run with yices
  alone. A portfolio run ends in ERROR when one solver crashes, for example
  boolector killed for running out of memory on the cover of a long
  segment's first chunk; sby then stops the other solvers too. yices alone
  needs far less memory (four chunk covers at once, yices alone, peaked at
  about 9 GB).
- with --missing, a run without any result file (its Slurm task was killed or
  lost before run_one.sh wrote the result): the same line again.

Runs on ABC or rIC3 (engine bmc3 or ric3) are never repeated: they are the
deep runs, which SMT BMC with yices does not reach, and a second attempt of
the same run would not finish either. A run is listed at most once, even
when it appears in several lists or a previous retry already failed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SMT = {"bitwuzla", "boolector", "yices"}


def status(certdir: str, name: str, task: str) -> list[str]:
    return [json.loads(p.read_text()).get("status", "ERROR")
            for p in sorted(Path(certdir, "results").glob(f"{name}.{task}*.json"))
            if p.name.split(".")[1] == task]


def retries(lists: list[Path], missing: bool = False) -> list[str]:
    out, seen = [], set()
    for lst in lists:
        for line in lst.read_text().splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            certdir, name, task = parts[:3]
            engine = parts[3] if len(parts) > 3 else ""
            if (certdir, name, task) in seen:
                continue
            if engine and not set(engine.split("+")) <= SMT:
                continue                                  # bmc3 / ric3: not repeated
            seen.add((certdir, name, task))
            st = status(certdir, name, task)
            if st and all(s == "ERROR" for s in st):
                out.append(f"{certdir} {name} {task} yices")
            elif not st and missing:
                out.append(line.strip())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--missing", action="store_true", help="also list runs without a result file")
    ap.add_argument("tasks", nargs="+", type=Path)
    args = ap.parse_args(argv)
    for line in retries([p for p in args.tasks if p.exists()], args.missing):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
