#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""ledger: which firmware images are certified, for which bytes, on which RTL.

``results/summary.json`` is the certificate ledger. For every certified
firmware image it records the sha256 of the image file, the campaign (git
commit) whose certificates were proved, the digests of the proof obligations
(``gen_cert.obligations``: every harness and normalized sby file) and whether
every segment, cover and negative control met its expectation. Each campaign
records the RTL it ran on: the sha256 of ``processor_fv.v`` and of the
committed ``src/project.v`` and ``src/protocol_emulator_core.v``, the SRAM
models and the harness includes. ``results/campaigns/<id>.json`` keeps the full
results of every campaign, including superseded ones.

Commands:

  check [--rtl DIR] [--no-obligations] [--summary FILE]
      Exit 1 unless every firmware/*.image.json has a certified record for its
      current sha256, on the current src/ RTL, SRAM models, harness includes
      and lemma files (boundary_lemmas.sv, sram_lemma.sv: the composition
      rests on them), and (unless --no-obligations) the current generator
      still emits exactly the proved obligations. --rtl DIR also compares
      DIR/processor_fv.v with the record. For every flagged image it runs
      gen_cert preflight, so the report says whether certify can handle it.
      Needs only the Python standard library; under a second when every image
      is certified, about 20 s while the six deep images are flagged (preflight).
  stale [--rtl DIR] [--no-obligations]
      The image files check flags, one path per line (campaign.sh certify).
  plan --changed FILE [--rtl DIR] [--all] [--budget N --chunk C --report FILE]
      For CI: the images whose certificates must be re-proved after a change
      (files listed in FILE), as a JSON list. With --budget, an image whose
      proofs need more than N runs (gen_cert preflight) is left out of the
      list and named in the --report file instead (cluster campaign only).
  fingerprint WORK
      Write WORK/fingerprint.json (campaign.sh prepare).
  record WORK [--id ID]
      Merge a finished campaign (after campaign.sh summarize) into the ledger
      and render results/summary.md.
  render
      Re-render results/summary.md from results/summary.json.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RESULTS = HERE / "results"
LEDGER = RESULTS / "summary.json"
SCHEMA = "protocol-emulator.timing-certificates.v2"
RTL_FILES = ("src/project.v", "src/protocol_emulator_core.v")
MODEL_FILES = ("RM_IHPSG13_1P_core_behavioral.v", "RM_IHPSG13_1P_64x16_c2.v")
INCLUDES = ("cert_dut.vh", "cert_env.vh")
LEMMA_FILES = ("boundary_lemmas.sv", "sram_lemma.sv")
# Paths whose change can change a certificate's proof obligation (CI plan).
RTL_INPUTS = ("hardcaml/", "formal/gen/", "formal/run.sh", "models/", "src/project.v",
              "src/protocol_emulator_core.v", "configs/")
TOOL_INPUTS = ("tools/timing/",)
CERTIFY = "tools/timing/cert/campaign.sh certify $WORK"


def sha_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def images_in(repo: Path) -> list[Path]:
    """The certified set: firmware/*.image.json of the design of record.
    (firmware/ext/ holds images for the extension variant's ISA.)"""
    return sorted((repo / "firmware").glob("*.image.json"))


def tag(path: Path) -> str:
    return path.name.replace(".image.json", "")


def load_ledger(path: Path | None = None) -> dict[str, Any]:
    path = path or LEDGER
    if not path.exists():
        return {"schema": SCHEMA, "campaigns": {}, "images": [], "superseded": []}
    data = json.loads(path.read_text())
    if data.get("schema") != SCHEMA:
        raise SystemExit(f"ledger: {path} is not a {SCHEMA} ledger")
    return data


def gen_cert():
    sys.path.insert(0, str(HERE))
    import gen_cert as G  # noqa: PLC0415
    return G


# ----------------------------------------------------------------------------
# fingerprint / record
# ----------------------------------------------------------------------------

def fingerprint(work: Path) -> dict[str, Any]:
    """What a campaign ran on: its snapshot (WORK/src), formal RTL
    (WORK/fbuild/rtl) and this directory's harness includes."""
    snap, rtl = work / "src", work / "fbuild" / "rtl"
    fp = {
        "rev": (work / "REVISION").read_text().strip(),
        "chunk": None, "short": None,
        "rtl": {"processor_fv.v": sha_file(rtl / "processor_fv.v"),
                **{f: sha_file(snap / f) for f in RTL_FILES}},
        "models": {f: sha_file(snap / "models" / f) for f in MODEL_FILES},
        "harness": {f: sha_file(HERE / f) for f in INCLUDES + LEMMA_FILES},
        "images": {tag(p): sha_file(p) for p in images_in(snap)},
        # the image's own hash of its words (tools/evidence/check_consistency.py compares it)
        "bytecodes": {tag(p): json.loads(p.read_text()).get("bytecode_sha256") for p in images_in(snap)},
    }
    for m in ("certs", "long"):
        mf = work / m / "manifest.json"
        if mf.exists():
            man = json.loads(mf.read_text())
            fp.setdefault("tools", {"gen_cert": man.get("generator"), "pe_timing": man.get("pe_timing")})
    return fp


def cmd_fingerprint(args: argparse.Namespace) -> int:
    work = Path(args.work).resolve()
    fp = fingerprint(work)
    fp["chunk"], fp["short"] = args.chunk, args.short
    (work / "fingerprint.json").write_text(json.dumps(fp, indent=1) + "\n")
    print(f"ledger: {work / 'fingerprint.json'} (rev {fp['rev'][:7]}, {len(fp['images'])} images)")
    return 0


def campaign_obligations(work: Path, includes: dict[str, str | None]) -> dict[str, dict[str, Any]]:
    """Obligation digests of what the campaign actually ran, from its files."""
    G = gen_cert()
    out: dict[str, dict[str, Any]] = {}
    for sub, key in (("certs", "whole"), ("long", "chunks")):
        mf = work / sub / "manifest.json"
        if not mf.exists():
            continue
        for c in json.loads(mf.read_text())["certificates"]:
            if key == "chunks" and c["chunk"] is None:
                continue
            name = c["certificate"]
            line = G.obligation_line(name, (work / sub / f"{name}.sv").read_text(),
                                     (work / sub / f"{name}.sby").read_text())
            rec = out.setdefault(c["image"], {"whole": [], "chunks": [], "image_sha256": set()})
            rec[key].append(line)
            rec["image_sha256"].add(c.get("image_sha256"))
    return {img: {"whole_sha256": G.digest_lines(r["whole"], includes),
                  "chunks_sha256": G.digest_lines(r["chunks"], includes),
                  "segments": len(r["whole"]), "chunk_certificates": len(r["chunks"]),
                  "image_sha256": sorted(x for x in r["image_sha256"] if x)} for img, r in out.items()}


_MUTANTS: dict[str, Any] = {}


def preflight(paths: list[Path], chunk: int) -> list[dict[str, Any]]:
    """gen_cert preflight of some images (the analyzer mutants built once)."""
    G = gen_cert()
    if not _MUTANTS:
        _MUTANTS.update({m: G.analyzer(m) for m in G.ANALYZER_MUTANTS})
    return [G.preflight_image(p, chunk, _MUTANTS) for p in paths]


def current_obligations(path: Path, chunk: int) -> dict[str, Any]:
    return gen_cert().obligations(path, chunk, {f: sha_file(HERE / f) for f in INCLUDES})


def load_result(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def cmd_record(args: argparse.Namespace) -> int:
    work = Path(args.work).resolve()
    results_path = work / "results.json"
    if not results_path.exists():
        raise SystemExit(f"ledger: {results_path} missing; run campaign.sh summarize first")
    res = json.loads(results_path.read_text())
    fp_path = work / "fingerprint.json"
    fp = json.loads(fp_path.read_text()) if fp_path.exists() else fingerprint(work)
    if fp.get("chunk") is None:
        fp["chunk"] = args.chunk
    ledger = load_ledger()
    cid = args.id or fp["rev"][:7]
    base, n = cid, 2
    while cid in ledger["campaigns"] and ledger["campaigns"][cid].get("work") != work.name:
        cid, n = f"{base}-{n}", n + 1
    includes = {f: fp["harness"].get(f) for f in INCLUDES}
    obl = campaign_obligations(work, includes)

    # Campaign-level evidence: lemmas, equivalence and its negative control.
    lemmas = res.get("lemmas", [])
    lemmas_ok = bool(lemmas) and all(r["met"] for r in lemmas)
    eq = load_result(work / "equiv" / "result.json")
    eq_neg = load_result(work / "equiv_neg" / "result.json")
    eq_ok = bool(eq) and eq["result"] == "EQUIVALENT"
    eq_neg_ok = bool(eq_neg) and eq_neg["result"] == "COUNTEREXAMPLE"
    campaign_problems = []
    if not lemmas_ok:
        campaign_problems.append("boundary/SRAM lemmas not all met")
    if not eq_ok:
        campaign_problems.append("formal netlist not proved equivalent to src/ (equiv/result.json)")
    if not eq_neg_ok:
        campaign_problems.append("equivalence negative control found no counterexample (equiv_neg/result.json)")

    negs: dict[str, list] = {}
    for r in res.get("negatives", []):
        negs.setdefault(r["image"], []).append(r)
    not_generated: dict[str, list] = {}
    for n in res.get("negatives_not_generated", []):
        not_generated.setdefault(n["image"], []).append({k: v for k, v in n.items() if k != "image"})
    date = datetime.date.today().isoformat()
    records = []
    for row in res["images"]:
        img = row["image"]
        o = obl.get(img, {})
        sha = fp["images"].get(img)
        problems = list(campaign_problems)
        if o.get("image_sha256") and o["image_sha256"] != [sha]:
            problems.append(f"manifest image sha256 {o['image_sha256']} != snapshot {sha}")
        if row["proved"] != row["segments"]:
            problems.append(f"{row['segments'] - row['proved']} segment(s) not proved")
        if row["covers"] != row["segments"]:
            problems.append(f"{row['segments'] - row['covers']} cover(s) not reached")
        n_neg = negs.get(img, [])
        # A negative control that PASSES means a wrong prediction was proved:
        # that blocks the image. One that did not finish (a very deep one) is
        # recorded as undecided.
        bad = [r["certificate"] for r in n_neg if r["status"] == "PASS"]
        undecided = [r["certificate"] for r in n_neg if r["status"] not in ("PASS", "FAIL")]
        if bad:
            problems.append(f"negative control(s) proved, not failed: {' '.join(bad)}")
        records.append({
            "image": img, "image_sha256": sha, "bytecode_sha256": fp.get("bytecodes", {}).get(img),
            "campaign": cid, "rev": fp["rev"],
            "engine_index": row["engine_index"], "segments": row["segments"], "variants": row["variants"],
            "pad_events": row["pad_events"], "known_level_edges": row["known_level_edges"],
            "issue_attempts": row["issue_attempts"], "samples": row["samples"], "longest": row["longest"],
            "proved": row["proved"], "whole": row["whole"], "split": row["split"], "covers": row["covers"],
            "negatives": len(n_neg), "negatives_failed": sum(r["met"] for r in n_neg),
            "negatives_undecided": undecided,
            "negatives_not_generated": not_generated.get(img, []),
            "obligations": {"chunk": fp["chunk"], "whole_sha256": o.get("whole_sha256"),
                            "chunks_sha256": o.get("chunks_sha256")},
            "max_seconds": row["max_seconds"], "total_seconds": row["total_seconds"],
            "solvers": row["solvers"], "jobs": row["jobs"], "cover_jobs": row["cover_jobs"],
            "certified": not problems, "problems": problems,
        })

    # Every Slurm job that wrote a result file of this campaign.
    jobs = {str(e["slurm_job"]) for e in (eq, eq_neg) if e and e.get("slurm_job")}
    for sub in ("certs", "long", "neg", "rtlneg"):
        for f in (work / sub / "results").glob("*.json") if (work / sub / "results").is_dir() else []:
            j = json.loads(f.read_text()).get("slurm_job")
            if j:
                jobs.add(str(j).split("_")[0])
    jobs = sorted(jobs | {j for r in records for j in r["jobs"] + r["cover_jobs"]})
    campaign = {"id": cid, "rev": fp["rev"], "recorded": date, "work": work.name,
                "rtl": fp["rtl"], "models": fp["models"], "harness": fp["harness"],
                "tools": fp.get("tools"), "chunk": fp["chunk"], "short": fp.get("short"),
                "images": sorted(r["image"] for r in records),
                "lemmas_ok": lemmas_ok, "equivalence": eq, "equivalence_negative": eq_neg,
                "problems": campaign_problems, "slurm_jobs": jobs}
    camp_dir = RESULTS / "campaigns"
    camp_dir.mkdir(parents=True, exist_ok=True)
    (camp_dir / f"{cid}.json").write_text(json.dumps({"campaign": campaign, "records": records, **res},
                                                     indent=1) + "\n")
    if (work / "results.md").exists():
        # campaigns/ is one level below results/: deepen relative links
        body = re.sub(r"\]\(\.\./", "](../../", (work / "results.md").read_text())
        (camp_dir / f"{cid}.md").write_text(
            f"# Timing certificates: campaign {cid} (commit {fp['rev'][:7]})\n\n"
            "Generated by `summarize.py`; the ledger is [`../summary.json`](../summary.json).\n\n"
            + body)
    merge(ledger, campaign, records, res)
    LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")
    render(ledger)
    ok = sum(r["certified"] for r in records)
    print(f"ledger: campaign {cid}: {ok}/{len(records)} image(s) certified")
    for r in records:
        if r["problems"]:
            print(f"  {r['image']}: {'; '.join(r['problems'])}")
    return 0 if ok == len(records) else 1


def merge(ledger: dict[str, Any], campaign: dict[str, Any], records: list[dict], res: dict[str, Any]) -> None:
    cid = campaign["id"]
    ledger["campaigns"][cid] = campaign
    current = {r["image"]: r for r in ledger["images"]}
    for r in records:
        old = current.get(r["image"])
        if old and old["campaign"] != cid:
            reason = ("image changed" if old["image_sha256"] != r["image_sha256"]
                      else "re-certified (same image)")
            ledger["superseded"].append({**{k: old.get(k) for k in ("image", "image_sha256", "bytecode_sha256",
                                                                     "campaign", "rev", "segments", "proved",
                                                                     "certified")},
                                         "superseded_by": cid, "reason": reason})
        current[r["image"]] = r
    ledger["images"] = sorted(current.values(), key=lambda r: r["image"])
    # Per-segment and per-control rows of the current records.
    keep = {(r["image"], r["campaign"]) for r in ledger["images"]}
    for key in ("nodes", "negatives"):
        rows = [x for x in ledger.get(key, []) if (x["image"], x["campaign"]) in keep]
        rows += [{**x, "campaign": cid} for x in res.get(key, [])]
        ledger[key] = sorted(rows, key=lambda x: (x["image"], x.get("node", 0), x.get("certificate", "")))
    ledger["lemmas"] = {**ledger.get("lemmas", {}), cid: res.get("lemmas", [])}
    if res.get("rtl_mutants"):
        ledger["rtl_mutants"] = {**ledger.get("rtl_mutants", {}), cid: res["rtl_mutants"]}
    tot = {k: sum(r[k] for r in ledger["images"]) for k in (
        "segments", "variants", "pad_events", "known_level_edges", "issue_attempts", "samples", "proved",
        "whole", "covers", "negatives", "negatives_failed")}
    tot["images"] = len(ledger["images"])
    tot["certified"] = sum(r["certified"] for r in ledger["images"])
    ledger["totals"] = tot


# ----------------------------------------------------------------------------
# check / stale / plan
# ----------------------------------------------------------------------------

def check(repo: Path, rtl: Path | None, obligations: bool) -> tuple[list[dict], list[str]]:
    """One row per image: status 'certified' or the reasons it is not."""
    ledger = load_ledger()
    by_image = {r["image"]: r for r in ledger["images"]}
    src = {f: sha_file(repo / f) for f in RTL_FILES}
    inc = {f: sha_file(HERE / f) for f in INCLUDES + LEMMA_FILES}
    models = {f: sha_file(repo / "models" / f) for f in MODEL_FILES}
    fv = sha_file(rtl / "processor_fv.v") if rtl else None
    rows, notes = [], []
    present = set()
    for path in images_in(repo):
        name = tag(path)
        present.add(name)
        sha = sha_file(path)
        rec = by_image.get(name)
        why = []
        if rec is None:
            why.append("no certificate")
        else:
            camp = ledger["campaigns"][rec["campaign"]]
            if rec["image_sha256"] != sha:
                why.append(f"image changed (certified {rec['image_sha256'][:12]}, now {sha[:12]})")
            if not rec["certified"]:
                why.append("campaign did not certify it: " + "; ".join(rec["problems"]))
            for f in RTL_FILES:
                if camp["rtl"].get(f) != src[f]:
                    why.append(f"{f} changed since campaign {rec['campaign']}")
            for f in MODEL_FILES:
                if camp["models"].get(f) != models[f]:
                    why.append(f"models/{f} changed since campaign {rec['campaign']}")
            for f in INCLUDES + LEMMA_FILES:
                if camp["harness"].get(f) != inc[f]:
                    why.append(f"tools/timing/cert/{f} changed since campaign {rec['campaign']}")
            if rtl and camp["rtl"].get("processor_fv.v") != fv:
                why.append(f"processor_fv.v differs from campaign {rec['campaign']}")
            if obligations and not why:
                try:
                    cur = current_obligations(path, rec["obligations"]["chunk"] or 0)
                except SystemExit as exc:           # the generator refuses the image
                    why.append(f"generator: {exc}")
                else:
                    if cur["whole_sha256"] != rec["obligations"]["whole_sha256"]:
                        why.append("the generator now emits different certificates (pe_timing or gen_cert changed)")
                    elif cur["chunks_sha256"] != rec["obligations"]["chunks_sha256"]:
                        why.append("the generator now emits different chunk certificates")
        if rec is None or why:
            # say early whether certify can handle it (everything prepare emits)
            pf = preflight([path], (rec or {}).get("obligations", {}).get("chunk") or 96)[0]
            if not pf["ok"]:
                why.append(f"not certifiable by gen_cert as is: {pf['reason']}")
        rows.append({"image": name, "path": str(path.relative_to(repo)), "sha256": sha,
                     "campaign": rec["campaign"] if rec else None,
                     "status": "certified" if not why else "stale", "reasons": why})
    for name in sorted(set(by_image) - present):
        notes.append(f"ledger record without an image file: {name} (kept as history)")
    return rows, notes


def cmd_check(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    rows, notes = check(repo, Path(args.rtl) if args.rtl else None, not args.no_obligations)
    stale = [r for r in rows if r["status"] != "certified"]
    lines = ["| image | sha256 | campaign | status |", "|---|---|---|---|"]
    for r in rows:
        status = r["status"] if not r["reasons"] else "STALE: " + "; ".join(r["reasons"])
        lines.append(f"| `{r['image']}` | `{r['sha256'][:12]}` | {r['campaign'] or '-'} | {status} |")
    head = (f"Timing certificates: {len(rows) - len(stale)}/{len(rows)} firmware images certified "
            f"for their current sha256 on the current RTL.")
    text = [head, ""] + lines + [""] + notes
    if stale:
        text += ["", f"Certify the stale images on Slurm with `{CERTIFY}` (after committing them); "
                 "see docs/timing-certificates.md, section 8."]
    print("\n".join(text))
    if args.summary:
        with open(args.summary, "a") as f:
            f.write("\n".join(text) + "\n")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1) + "\n")
    return 1 if stale else 0


def cmd_stale(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    rows, _ = check(repo, Path(args.rtl) if args.rtl else None, not args.no_obligations)
    for r in rows:
        if r["status"] != "certified":
            print(r["path"])
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Images to re-prove in CI. Changed image files are proved; a change to
    the analyzer or generator proves the images whose obligations changed; a
    change to the RTL inputs proves every image once the regenerated
    processor_fv.v (--rtl) differs from the ledger's."""
    repo = Path(args.repo).resolve()
    changed = [line.strip() for line in Path(args.changed).read_text().splitlines() if line.strip()] \
        if args.changed else []
    all_images = [tag(p) for p in images_in(repo)]
    want: set[str] = set()
    if args.all:
        want = set(all_images)
    for f in changed:
        if f.startswith("firmware/") and f.endswith(".image.json") and f.count("/") == 1:
            name = Path(f).name.replace(".image.json", "")
            if name in all_images:
                want.add(name)
    tools_changed = any(f.startswith(TOOL_INPUTS) for f in changed)
    rtl_changed = any(f.startswith(RTL_INPUTS) for f in changed)
    if tools_changed or rtl_changed:
        rtl = Path(args.rtl) if args.rtl else None
        rows, _ = check(repo, rtl, True)
        for r in rows:
            if r["status"] != "certified":
                want.add(r["image"])
    report = []
    if args.budget is not None:
        # An image whose proofs exceed the CI budget is certified by the
        # cluster campaign only: leave it out, and say so. (An image gen_cert
        # refuses stays in: its proof job then fails with the reason.)
        for pf in preflight([repo / "firmware" / f"{n}.image.json" for n in sorted(want)], args.chunk):
            if pf["ok"] and pf["ci_runs"] > args.budget:
                want.discard(pf["image"])
                report.append(f"{pf['image']}: {pf['ci_runs']} proof runs exceed the CI budget of {args.budget}; "
                              f"not proved in CI (cluster campaign only: {CERTIFY})")
    if args.report:
        Path(args.report).write_text("".join(line + "\n" for line in report))
    print(json.dumps(sorted(want)))
    return 0


# ----------------------------------------------------------------------------
# render
# ----------------------------------------------------------------------------

def fmt_s(s: float | None) -> str:
    if s is None:
        return "-"
    return f"{s:.0f}" if s < 3600 else f"{s / 3600:.1f} h"


def render(ledger: dict[str, Any]) -> None:
    L = ["# Timing certificates: ledger", "",
         "Generated by `ledger.py` from [`summary.json`](summary.json). A firmware image is certified "
         "for the sha256 recorded here, on the RTL of its campaign; `ledger.py check` (CI workflow "
         "`certs`) fails when a committed image, the RTL, the harness includes or the generator's "
         "output no longer match. Method: [`docs/timing-certificates.md`](../../../../docs/timing-certificates.md). "
         "Full results per campaign, including superseded ones: [`campaigns/`](campaigns/).", "",
         "## Campaigns", "",
         "| campaign | commit | images | `processor_fv.v` sha256 | `src/protocol_emulator_core.v` sha256 "
         "| lemmas | equivalence (negative control) |",
         "|---|---|---:|---|---|---|---|"]
    for cid, c in ledger["campaigns"].items():
        eq = (c.get("equivalence") or {}).get("result", "not recorded")
        eqn = (c.get("equivalence_negative") or {}).get("result", "not recorded")
        lem = {True: "all met", False: "NOT all met", None: "not recorded"}[c.get("lemmas_ok")]
        L.append(f"| [{cid}](campaigns/{cid}.md) | `{c['rev'][:7]}` | {len(c['images'])} | "
                 f"`{(c['rtl'].get('processor_fv.v') or '-')[:12]}` | "
                 f"`{(c['rtl'].get('src/protocol_emulator_core.v') or '-')[:12]}` | {lem} | {eq} ({eqn}) |")
    L += ["", "## Certified images", "",
          "| image | sha256 | campaign | engine | segments | variants | pad events | known-level edges "
          "| issue slots | input samples | longest segment | proved | covers | negative controls failed "
          "| solver time max / total (s) | certified |",
          "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in ledger["images"]:
        L.append(f"| `{r['image']}` | `{r['image_sha256'][:12]}` | {r['campaign']} | {r['engine_index']} | "
                 f"{r['segments']} | {r['variants']} | {r['pad_events']} | {r['known_level_edges']} | "
                 f"{r['issue_attempts']} | {r['samples']} | {r['longest']} | {r['proved']}/{r['segments']} | "
                 f"{r['covers']}/{r['segments']} | {r['negatives_failed']}/{r['negatives']}"
                 f"{' (' + str(len(r.get('negatives_undecided') or [])) + ' undecided)' if r.get('negatives_undecided') else ''} | "
                 f"{fmt_s(r['max_seconds'])} / {fmt_s(r['total_seconds'])} | "
                 f"{'yes' if r['certified'] else 'NO: ' + '; '.join(r['problems'])} |")
    t = ledger.get("totals", {})
    if t:
        L.append(f"| **total** ({t['certified']}/{t['images']} certified) | | | | {t['segments']} | "
                 f"{t['variants']} | {t['pad_events']} | {t['known_level_edges']} | {t['issue_attempts']} | "
                 f"{t['samples']} | | {t['proved']}/{t['segments']} | {t['covers']}/{t['segments']} | "
                 f"{t['negatives_failed']}/{t['negatives']} | | |")
    ng = [(r["image"], n) for r in ledger["images"] for n in r.get("negatives_not_generated") or []]
    if ng:
        L += ["", "## Negative controls not generated", "",
              "A deliberately wrong analyzer can predict a schedule that contradicts itself on some nodes; "
              "no certificate can be written for those, so the control comes from another node the "
              "mutation changes (or there is none for that image).", "",
              "| image | mutation | nodes | reason | control from another node |", "|---|---|---|---|---|"]
        for img, n in ng:
            nodes = "whole analysis" if n.get("nodes") is None else ", ".join(f"n{i}" for i in n["nodes"])
            L.append(f"| `{img}` | {n['mutation']} | {nodes} | {n['reason']} | "
                     f"{'yes' if n.get('control_emitted') else 'no'} |")
    if ledger["superseded"]:
        L += ["", "## Superseded records", "",
              "| image | sha256 | campaign | proved | superseded by | reason |", "|---|---|---|---:|---|---|"]
        for s in ledger["superseded"]:
            L.append(f"| `{s['image']}` | `{s['image_sha256'][:12]}` | {s['campaign']} | "
                     f"{s['proved']}/{s['segments']} | {s['superseded_by']} | {s['reason']} |")
    (RESULTS / "summary.md").write_text("\n".join(L) + "\n")


def cmd_render(args: argparse.Namespace) -> int:
    render(load_ledger())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="fail unless every image is certified for its current sha256")
    s = sub.add_parser("stale", help="print the image files check flags")
    for p in (c, s):
        p.add_argument("--repo", default=str(REPO))
        p.add_argument("--rtl", help="directory with a regenerated processor_fv.v to compare as well")
        p.add_argument("--no-obligations", action="store_true",
                       help="skip re-emitting the certificates (image, RTL and include hashes only)")
    c.add_argument("--summary", help="append the report (markdown) to this file, e.g. $GITHUB_STEP_SUMMARY")
    c.add_argument("--json", help="write the per-image rows to this file")
    pl = sub.add_parser("plan", help="CI: images to re-prove after a change")
    pl.add_argument("--repo", default=str(REPO))
    pl.add_argument("--changed", help="file with the changed paths, one per line")
    pl.add_argument("--rtl", help="regenerated formal RTL directory")
    pl.add_argument("--all", action="store_true", help="every image")
    pl.add_argument("--budget", type=int, help="leave out images needing more CI proof runs than this")
    pl.add_argument("--chunk", type=int, default=96, help="chunk size of ci_prove.sh (run count)")
    pl.add_argument("--report", help="write one line per image left out to this file")
    f = sub.add_parser("fingerprint", help="write WORK/fingerprint.json")
    f.add_argument("work")
    f.add_argument("--chunk", type=int, default=96)
    f.add_argument("--short", type=int, default=200)
    r = sub.add_parser("record", help="merge a finished campaign into the ledger")
    r.add_argument("work")
    r.add_argument("--id", help="campaign id (default: the commit's short hash)")
    r.add_argument("--chunk", type=int, default=96, help="chunk size, when WORK has no fingerprint.json")
    sub.add_parser("render", help="re-render results/summary.md")
    args = ap.parse_args(argv)
    return {"check": cmd_check, "stale": cmd_stale, "plan": cmd_plan, "fingerprint": cmd_fingerprint,
            "record": cmd_record, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
