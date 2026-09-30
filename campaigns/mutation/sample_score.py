#!/usr/bin/env python3
"""Score of a uniform mutant sample with a Wilson confidence interval.

usage: sample_score.py SUMMARY.json [--z 1.96] [--population N]

SUMMARY.json is push_summary.py's --json output for a design whose mutants are a
uniform random sample without replacement (gen_line_mutants.sh). The score is
killed / (mutants - proven equivalent), as in push_summary.py. The mutants that
are not proven equivalent are a uniform sample of the population's mutants that
the same proof methods would not prove, so the score estimates that
population's score; the interval is the Wilson score interval

    {p : (k - n p)^2 <= z^2 n p (1 - p)},   k = killed, n = mutants - equivalent,

without a finite-population correction (with --population the sampling fraction
is printed; the correction factor sqrt((N - n) / (N - 1)) would only narrow the
interval). The same is printed per region of the summary's by_region table.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def wilson(k: int, n: int, z: float) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("summary", type=Path)
    ap.add_argument("--z", type=float, default=1.96)
    ap.add_argument("--population", type=int)
    a = ap.parse_args()
    s = json.loads(a.summary.read_text())
    rows = [("total", s["killed"], s["mutants"], s["equivalent"])]
    for region, c in sorted(s.get("by_region", {}).items()):
        rows.append((region, c.get("killed", 0), c.get("mutants", 0), c.get("equivalent", 0)))
    out = []
    for name, k, m, e in rows:
        n = m - e
        lo, hi = wilson(k, n, a.z)
        out.append({"region": name, "mutants": m, "equivalent": e, "killed": k,
                    "score": round(k / n, 4) if n else None, "wilson_low": round(lo, 4), "wilson_high": round(hi, 4)})
        print(f"{name:24s} {k:5d} / ({m:5d} - {e:3d}) = {100 * k / n if n else 0:6.2f} %   "
              f"Wilson (z = {a.z}): [{100 * lo:6.2f} %, {100 * hi:6.2f} %]")
    if a.population:
        print(f"sampling fraction {s['mutants']} / {a.population} = {s['mutants'] / a.population:.4f}")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
