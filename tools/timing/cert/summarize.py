#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Summarize certificate runs: manifests + run_one.sh result files -> tables.

Usage: summarize.py --nodes certs/manifest.json [--chunks long/manifest.json]
                    [--neg neg/manifest.json] --results DIR [--results DIR ...]
                    [--lemmas DIR] --out-md results.md --out-json results.json

A node (one segment certificate) is proved when its whole-segment
certificate met its expectation, or when every chunk of its split version
did. For every task the best evidence is taken: a PASS (a FAIL for a negative
control) from any solver or the portfolio.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_results(dirs: list[str]) -> dict[tuple[str, str], list[dict]]:
    runs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for d in dirs:
        for p in sorted(Path(d).glob("*.json")):
            r = json.loads(p.read_text())
            runs[(r["certificate"], r["task"])].append(r)
    return runs


def best(runs: list[dict], expect: str) -> dict | None:
    if not runs:
        return None
    good = [r for r in runs if r["status"] == expect]
    if good:
        return min(good, key=lambda r: r["seconds"])
    decided = [r for r in runs if r["status"] in ("PASS", "FAIL")]
    return (decided or runs)[0]


def solver(r: dict | None) -> str:
    if not r:
        return "-"
    w = r.get("winner") or ""
    if w:
        return w.rsplit(" ", 1)[0].replace("smtbmc ", "").replace("abc ", "")
    return r.get("engine", "-")


def fmt_s(s: float | None) -> str:
    if s is None:
        return "-"
    return f"{s:.0f}" if s < 3600 else f"{s / 3600:.1f} h"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--nodes", required=True)
    ap.add_argument("--chunks")
    ap.add_argument("--neg")
    ap.add_argument("--results", action="append", required=True)
    ap.add_argument("--lemmas", help="directory of boundary_lemmas result files")
    ap.add_argument("--engine-evidence", help="JSON list of negative-control failures found by an engine "
                    "whose sby run did not finish (e.g. ABC bmc3 before the witness replay)")
    ap.add_argument("--rtl-mutants", action="append", default=[],
                    help="manifest of certificates run against an RTL mutant (rtl_mutants.py)")
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--out-json", required=True)
    args = ap.parse_args(argv)
    runs = load_results(args.results + ([args.lemmas] if args.lemmas else []))
    nodes = json.loads(Path(args.nodes).read_text())["certificates"]
    chunks = json.loads(Path(args.chunks).read_text())["certificates"] if args.chunks else []
    negs = json.loads(Path(args.neg).read_text())["certificates"] if args.neg else []

    by_node: dict[tuple[str, int], list] = defaultdict(list)
    for c in chunks:
        by_node[(c["image"], c["node"])].append(c)

    node_rows = []
    for n in nodes:
        whole = best(runs.get((n["certificate"], "bmc"), []), "PASS")
        whole_cov = best(runs.get((n["certificate"], "cover"), []), "PASS")
        ch = by_node.get((n["image"], n["node"]), [])
        ch_runs = [(c, best(runs.get((c["certificate"], "bmc"), []), "PASS"),
                    best(runs.get((c["certificate"], "cover"), []), "PASS")) for c in ch]
        whole_ok = bool(whole and whole["status"] == "PASS")
        chunks_ok = bool(ch) and all(r and r["status"] == "PASS" for _, r, _ in ch_runs)
        cover_ok = bool(whole_cov and whole_cov["status"] == "PASS") or (
            bool(ch) and all(cv and cv["status"] == "PASS" for _, _, cv in ch_runs))
        evidence = [whole] if whole_ok else [r for _, r, _ in ch_runs] if chunks_ok else []
        covers = [whole_cov] if whole_cov and whole_cov["status"] == "PASS" else [cv for _, _, cv in ch_runs]
        node_rows.append({**n, "proved": whole_ok or chunks_ok, "whole_ok": whole_ok,
                          "has_chunks": bool(ch), "chunks_ok": chunks_ok,
                          "method": "whole" if whole_ok else f"{len(ch)} chunks" if chunks_ok else "-",
                          "cover_ok": cover_ok, "seconds": sum(r["seconds"] for r in evidence) if evidence else None,
                          "max_seconds": max((r["seconds"] for r in evidence), default=None),
                          "solvers": sorted({solver(r) for r in evidence}),
                          "jobs": sorted({str(r["slurm_job"]) for r in evidence if r.get("slurm_job")}),
                          "cover_jobs": sorted({str(r["slurm_job"]) for r in covers if r and r.get("slurm_job")})})

    md = ["| image | engine | segments | variants | pad events | known-level edges | issue slots | input samples "
          "| longest segment | proved | whole-segment BMC | split into chunks | covers "
          "| solver time max / total (s) | solvers | Slurm jobs (proofs) |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    table = []
    images = sorted({r["image"] for r in node_rows})
    for image in images:
        rs = [r for r in node_rows if r["image"] == image]
        secs = [r["seconds"] for r in rs if r["seconds"] is not None]
        mx = [r["max_seconds"] for r in rs if r["max_seconds"] is not None]
        row = {"image": image, "engine_index": rs[0]["engine_index"], "segments": len(rs),
               "variants": sum(r["variants"] for r in rs), "pad_events": sum(r["pad_events"] for r in rs),
               "known_level_edges": sum(r["known_level_edges"] for r in rs),
               "issue_attempts": sum(r["issue_attempts"] for r in rs), "samples": sum(r["samples"] for r in rs),
               "longest": max(r["depth"] - 2 for r in rs), "proved": sum(r["proved"] for r in rs),
               "whole": sum(r["whole_ok"] for r in rs),
               "split": f"{sum(r['chunks_ok'] for r in rs)}/{sum(r['has_chunks'] for r in rs)}",
               "covers": sum(r["cover_ok"] for r in rs),
               "max_seconds": max(mx) if mx else None, "total_seconds": sum(secs) if secs else None,
               "solvers": sorted({s for r in rs for s in r["solvers"]}),
               "jobs": sorted({j for r in rs for j in r["jobs"]}),
               "cover_jobs": sorted({j for r in rs for j in r["cover_jobs"]})}
        table.append(row)
        md.append(f"| `{image}` | {row['engine_index']} | {row['segments']} | {row['variants']} | {row['pad_events']} | "
                  f"{row['known_level_edges']} | {row['issue_attempts']} | {row['samples']} | {row['longest']} | "
                  f"{row['proved']}/{row['segments']} | {row['whole']} | {row['split']} | {row['covers']}/{row['segments']} | "
                  f"{fmt_s(row['max_seconds'])} / {fmt_s(row['total_seconds'])} | {', '.join(row['solvers'])} | "
                  f"{', '.join(row['jobs'])} |")
    tot = {k: sum(r[k] for r in table) for k in ("segments", "variants", "pad_events", "known_level_edges",
                                                   "issue_attempts", "samples", "proved", "whole", "covers")}
    tot["split"] = (f"{sum(r['chunks_ok'] for r in node_rows)}/{sum(r['has_chunks'] for r in node_rows)}")
    cover_jobs = sorted({j for r in table for j in r["cover_jobs"]})
    md.append(f"| **total** | | {tot['segments']} | {tot['variants']} | {tot['pad_events']} | "
              f"{tot['known_level_edges']} | {tot['issue_attempts']} | {tot['samples']} | | "
              f"{tot['proved']}/{tot['segments']} | {tot['whole']} | {tot['split']} | "
              f"{tot['covers']}/{tot['segments']} | | | |")
    md.append("")
    md.append(f"Cover runs: Slurm jobs {', '.join(cover_jobs)}.")

    neg_rows = []
    if negs:
        md += ["", "| negative control | image | node | depth | outcome | failed assertions | s | solver | Slurm job |",
               "|---|---|---:|---:|---|---|---:|---|---|"]
        extra = {e["certificate"]: e for e in json.loads(Path(args.engine_evidence).read_text())} \
            if args.engine_evidence else {}
        for c in sorted(negs, key=lambda c: (c["mutation"], c["image"], c["node"])):
            b = best(runs.get((c["certificate"], "bmc"), []), "FAIL") or {}
            if b.get("status") != "FAIL" and c["certificate"] in extra:
                e = extra[c["certificate"]]
                b = {"status": "FAIL", "failed_assertions": [f"engine log: frame {e['frame']}"],
                     "seconds": int(e["abc_seconds"]), "winner": e["engine"] + " FAIL", "slurm_job": e["slurm_job"]}
            met = b.get("status") == "FAIL"
            neg_rows.append({**c, "status": b.get("status"), "met": met,
                             "failed": b.get("failed_assertions"), "seconds": b.get("seconds"),
                             "slurm_job": b.get("slurm_job")})
            md.append(f"| {c['mutation']} | `{c['image']}` | {c['node']} | {c['depth']} | "
                      f"{b.get('status', 'not run')}{' (expected)' if met else ''} | "
                      f"{', '.join((b.get('failed_assertions') or [])[:3])} | {fmt_s(b.get('seconds'))} | "
                      f"{solver(b) if b else '-'} | {b.get('slurm_job', '-')} |")
    lemma_rows = []
    if args.lemmas:
        md += ["", "| lemma task | outcome | s | solver | Slurm job |", "|---|---|---:|---|---|"]
        tasks = [("boundary_lemmas", t) for t in ("k0", "k1", "k2", "k3", "cover_k0", "cover_k1", "cover_k2",
                                                  "cover_k3", "neg")]
        tasks += [("sram_lemma", t) for t in ("prove", "bmc", "cover", "neg")]
        for name, t in tasks:
            expect = "FAIL" if t == "neg" else "PASS"
            b = best(runs.get((name, t), []), expect) or {}
            if name == "sram_lemma" and t == "bmc" and not b:
                continue
            lemma_rows.append({"harness": name, "task": t, "status": b.get("status"),
                               "met": b.get("status") == expect, "seconds": b.get("seconds"),
                               "covers": b.get("covers_reached"), "slurm_job": b.get("slurm_job")})
            md.append(f"| `{name}` {t} | {b.get('status', 'not run')}{' (expected)' if t == 'neg' and b.get('status') == 'FAIL' else ''} "
                      f"| {fmt_s(b.get('seconds'))} | {solver(b) if b else '-'} | {b.get('slurm_job', '-')} |")
    rtl_rows = []
    if args.rtl_mutants:
        md += ["", "| RTL mutant | image | certificates run | failed | failed assertions | Slurm jobs |",
               "|---|---|---:|---:|---|---|"]
        for m in args.rtl_mutants:
            certs = json.loads(Path(m).read_text())["certificates"]
            groups: dict[tuple[str, str], list] = defaultdict(list)
            for c in certs:
                groups[(c["mutation"], c["image"])].append(c)
            for (mut, image), cs in sorted(groups.items()):
                rs = [best(runs.get((c["certificate"], "bmc"), []), "FAIL") for c in cs]
                ran = [r for r in rs if r and r["status"] in ("PASS", "FAIL")]
                failed = [r for r in ran if r["status"] == "FAIL"]
                labels = sorted({a for r in failed for a in r["failed_assertions"]})
                jobs = sorted({str(r["slurm_job"]) for r in ran if r.get("slurm_job")})
                rtl_rows.append({"mutant": mut, "image": image, "certificates": len(cs), "ran": len(ran),
                                 "failed": len(failed), "assertions": labels, "jobs": jobs})
                md.append(f"| {mut} | `{image}` | {len(ran)}/{len(cs)} | {len(failed)} | "
                          f"{', '.join(labels[:4])} | {', '.join(jobs)} |")
    out = {"images": table, "totals": tot, "rtl_mutants": rtl_rows, "nodes": [
        {k: r[k] for k in ("image", "node", "label", "depth", "variants", "pad_events", "proved", "whole_ok",
                           "has_chunks", "chunks_ok", "cover_ok", "seconds", "solvers", "jobs")} for r in node_rows],
        "negatives": [{k: r.get(k) for k in ("certificate", "image", "node", "mutation", "status", "met",
                                             "failed", "seconds", "slurm_job")} for r in neg_rows],
        "lemmas": lemma_rows}
    Path(args.out_md).write_text("\n".join(md) + "\n")
    Path(args.out_json).write_text(json.dumps(out, indent=1) + "\n")
    print("\n".join(md))
    missing = [f"{r['image']}:n{r['node']}" for r in node_rows if not r["proved"]]
    if missing:
        print(f"\nsummarize: {len(missing)} segment(s) not proved yet: {' '.join(missing[:20])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
