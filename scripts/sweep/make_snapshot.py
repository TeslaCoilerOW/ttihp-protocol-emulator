#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a self-contained LibreLane run snapshot for one sweep point.

Starts from the repository's submission files (src/project.v, src/config.json,
src/sram_pdn_cfg.tcl, info.yaml, macros/RM_*, models/blackbox) and applies ONLY
the requested changes:

  info.yaml    tiles, clock_hz (= 1e9 / CLOCK_PERIOD, rounded)
  config.json  CLOCK_PERIOD, PL_TARGET_DENSITY_PCT, PL_/GRT_RESIZER_HOLD_SLACK_MARGIN,
               OPENROAD_THREADS (local-only), MACROS.<macro>.instances from
               floorplans/<id>.json (+ that file's optional "config" keys),
               then any extra LibreLane overrides (null deletes a key)
  core         the given Verilog, copied as src/protocol_emulator_core.v

It then reproduces tt-support-tools (d66cf179e) create_user_config() and
create_merged_config() exactly (project.py, config_utils.py): user_config.json =
DESIGN_NAME, VERILOG_FILES, DIE_AREA (tile_sizes.yaml), FP_DEF_TEMPLATE
(dir::../tt/tech/ihp-sg13cmos5l/def/tt_block_<tiles>_pgvdd.def), VDD_PIN,
GND_PIN, RT_MAX_LAYER Metal4; config_merged.json = config.json minus its "//"
key, updated with user_config. The needed DEF template is copied into the
snapshot. macros/check_macro_floorplan.py runs on the snapshot and must pass.

Output: <runs>/<run_id>/{snap/, params.json, floorplan_check.txt}. params.json
records every parameter, the sha256 of the core, of each snapshot file, of
config.json and config_merged.json, and the local-only deviations from the
action. Re-running with the same parameters is a no-op (same run id, same
hashes); different content under an existing run id is an error unless --force.

Usage (one run):
  python3 scripts/sweep/make_snapshot.py --tiles 8x4 --period 20 --density 60 \
      --floorplan fp8_base --mode fast --threads 32 [--core PATH] [--core-tag base] \
      [--pl-hold 0.1 --grt-hold 0.05] [--override KEY=JSON ...] [--runs-dir DIR]
"""

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

INFO_SOURCES = ["project.v", "protocol_emulator_core.v"]


def resolve_core(core):
    core = os.path.expandvars(core)  # matrices may use ${PE_WORK}
    if "$" in core:
        raise SystemExit("core path %r: unset environment variable (set PE_WORK)" % core)
    return core if os.path.isabs(core) else os.path.normpath(os.path.join(L.REPO, core))


def load_floorplan(fp_id):
    path = fp_id if fp_id.endswith(".json") else os.path.join(L.FLOORPLAN_DIR, fp_id + ".json")
    with open(path) as f:
        fp = json.load(f)
    return path, fp


def normalize_params(p):
    """Fill defaults and canonicalise types. Returns a new dict."""
    q = dict(L.REPO_DEFAULTS)
    q.update({k: v for k, v in p.items() if v is not None})
    q["density"] = float(q["density"])
    q["period"] = float(q["period"])
    q["pl_hold"] = float(q["pl_hold"])
    q["grt_hold"] = float(q["grt_hold"])
    q["threads"] = int(q["threads"])
    q["overrides"] = dict(q.get("overrides") or {})
    if q["mode"] not in ("fast", "full"):
        raise SystemExit("mode must be fast or full, got %r" % q["mode"])
    return q


def num(x):
    """JSON number as the repo writes it: 20.0 -> 20, 0.05 -> 0.05."""
    x = float(x)
    return int(x) if x == int(x) else x


def run_id_for(q, core_sha):
    rid = "%s-%s-%s-%s-d%s-p%s-h%s_%s-%s-t%d" % (
        q["core_tag"], core_sha[:6], q["tiles"], q["floorplan"], L.fmt_num(q["density"]),
        L.fmt_num(q["period"]), L.fmt_num(q["pl_hold"]), L.fmt_num(q["grt_hold"]), q["mode"], q["threads"])
    if q["overrides"]:
        rid += "-x" + L.sha256_bytes(L.canonical_json(q["overrides"]).encode())[:6]
    if q.get("id_suffix"):
        rid += "-" + q["id_suffix"]
    return rid


def edit_info_yaml(text, tiles, clock_hz):
    new, n1 = re.subn(r'^(\s*tiles:\s*)"[^"]*"', lambda m: '%s"%s"' % (m.group(1), tiles), text, count=1,
                      flags=re.M)
    new, n2 = re.subn(r"^(\s*clock_hz:\s*)\d+", lambda m: "%s%d" % (m.group(1), clock_hz), new, count=1,
                      flags=re.M)
    if n1 != 1 or n2 != 1:
        raise SystemExit("info.yaml: could not find tiles/clock_hz lines")
    return new


def info_fields(text):
    top = re.search(r'^\s*top_module:\s*"([^"]+)"', text, re.M).group(1)
    tiles = re.search(r'^\s*tiles:\s*"([^"]+)"', text, re.M).group(1)
    m = re.search(r"^\s*source_files:\s*\n((?:\s*-\s*.*\n)+)", text, re.M)
    srcs = [re.sub(r'^\s*-\s*"?([^"#]+?)"?\s*(#.*)?$', r"\1", l) for l in m.group(1).splitlines() if l.strip()]
    return top, tiles, srcs


def build(q, runs_dir, force=False, check=True, quiet=False):
    core_path = resolve_core(q["core"])
    if not os.path.isfile(core_path):
        raise SystemExit("core Verilog not found: %s" % core_path)
    core_sha = L.sha256_file(core_path)
    fp_path, fp = load_floorplan(q["floorplan"])
    if fp.get("tiles") and fp["tiles"] != q["tiles"] and not q.get("allow_tile_mismatch"):
        raise SystemExit("floorplan %s is for tiles %s, run asks for %s" % (fp.get("id"), fp["tiles"], q["tiles"]))
    sizes = L.read_tile_sizes()
    if q["tiles"] not in sizes:
        raise SystemExit("tiles %s not in %s tile_sizes.yaml" % (q["tiles"], L.TECH))
    def_src = os.path.join(L.TT_DIR, "tech", L.TECH, "def", "tt_block_%s_pgvdd.def" % q["tiles"])
    if not os.path.isfile(def_src):
        raise SystemExit("missing DEF template %s" % def_src)

    run_id = q.get("run_id") or run_id_for(q, core_sha)
    run_dir = os.path.join(runs_dir, run_id)

    # ---- config.json: repo config, loaded as tt-support-tools loads it (json.load)
    with open(os.path.join(L.REPO, "src", "config.json")) as f:
        repo_cfg = json.load(f)
    cfg = copy.deepcopy(repo_cfg)
    cfg["CLOCK_PERIOD"] = num(q["period"])
    cfg["PL_TARGET_DENSITY_PCT"] = num(q["density"])
    cfg["PL_RESIZER_HOLD_SLACK_MARGIN"] = num(q["pl_hold"])
    cfg["GRT_RESIZER_HOLD_SLACK_MARGIN"] = num(q["grt_hold"])
    cfg["OPENROAD_THREADS"] = int(q["threads"])
    macro = fp.get("macro", L.MACRO)
    if macro not in cfg.get("MACROS", {}):
        raise SystemExit("floorplan macro %s not in src/config.json MACROS" % macro)
    cfg["MACROS"][macro]["instances"] = copy.deepcopy(fp["instances"])
    for k, v in list(fp.get("config", {}).items()) + list(q["overrides"].items()):
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    changes = {}
    for k in sorted(set(repo_cfg) | set(cfg)):
        if k == "//":
            continue
        if repo_cfg.get(k, "<unset>") != cfg.get(k, "<unset>"):
            changes[k] = {"repo": repo_cfg.get(k, "<unset>"), "run": cfg.get(k, "<unset>")}

    # ---- info.yaml: tiles + clock_hz only
    with open(os.path.join(L.REPO, "info.yaml")) as f:
        info_text = f.read()
    clock_hz = int(round(1e3 / q["period"] * 1e6))  # period in ns -> Hz
    info_new = edit_info_yaml(info_text, q["tiles"], clock_hz)
    top, tiles_chk, srcs = info_fields(info_new)
    if tiles_chk != q["tiles"] or srcs != INFO_SOURCES:
        raise SystemExit("unexpected info.yaml fields: tiles %s sources %s" % (tiles_chk, srcs))

    # ---- build in a temp dir next to the run dir, then compare/replace
    L.mkdir_p(runs_dir)
    tmp = tempfile.mkdtemp(prefix=".snap-%s." % run_id, dir=runs_dir)
    try:
        snap = os.path.join(tmp, "snap")
        for d in ("src", "macros", "models/blackbox", "tt/tech/%s/def" % L.TECH):
            L.mkdir_p(os.path.join(snap, d))
        shutil.copyfile(os.path.join(L.REPO, "src", "project.v"), os.path.join(snap, "src", "project.v"))
        shutil.copyfile(core_path, os.path.join(snap, "src", "protocol_emulator_core.v"))
        shutil.copyfile(os.path.join(L.REPO, "src", "sram_pdn_cfg.tcl"), os.path.join(snap, "src", "sram_pdn_cfg.tcl"))
        shutil.copytree(os.path.join(L.REPO, "macros", macro), os.path.join(snap, "macros", macro))
        shutil.copyfile(os.path.join(L.REPO, "macros", "LICENSE.IHP-Open-PDK"),
                        os.path.join(snap, "macros", "LICENSE.IHP-Open-PDK"))
        shutil.copyfile(os.path.join(L.REPO, "models", "blackbox", macro + ".v"),
                        os.path.join(snap, "models", "blackbox", macro + ".v"))
        shutil.copyfile(def_src, os.path.join(snap, "tt", "tech", L.TECH, "def", os.path.basename(def_src)))
        with open(os.path.join(snap, "info.yaml"), "w") as f:
            f.write(info_new)
        with open(os.path.join(snap, "src", "config.json"), "w") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        # == tt-support-tools create_user_config() + create_merged_config()
        user = {
            "DESIGN_NAME": top,
            "VERILOG_FILES": ["dir::%s" % s for s in srcs],
            "DIE_AREA": sizes[q["tiles"]],
            "FP_DEF_TEMPLATE": "dir::../tt/tech/%s/def/tt_block_%s_pgvdd.def" % (L.TECH, q["tiles"]),
            "VDD_PIN": "VPWR",
            "GND_PIN": "VGND",
            "RT_MAX_LAYER": "Metal4",
        }
        with open(os.path.join(snap, "src", "user_config.json"), "w") as f:
            json.dump(user, f, indent=2)
        with open(os.path.join(snap, "src", "config.json")) as f:
            merged = json.load(f)
        merged.pop("//", None)
        u2 = dict(user)
        u2.pop("//", None)
        merged.update(u2)
        with open(os.path.join(snap, "src", "config_merged.json"), "w") as f:
            json.dump(merged, f, indent=2)

        # ---- floorplan check on the snapshot itself
        chk = subprocess.run([sys.executable, os.path.join(L.REPO, "macros", "check_macro_floorplan.py"),
                              "--repo", snap, "--tiles", q["tiles"]],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        with open(os.path.join(tmp, "floorplan_check.txt"), "w") as f:
            f.write(chk.stdout)
        if check and chk.returncode != 0:
            raise SystemExit("floorplan check FAILED for %s:\n%s" % (run_id, "\n".join(
                l for l in chk.stdout.splitlines() if l.startswith(("FAIL", "RESULT")))))
        m = re.search(r"INFO pin access: (\d+)", chk.stdout)
        pred_418 = int(m.group(1)) if m else None

        # ---- hashes
        files = []
        for root, _, names in os.walk(snap):
            for n in names:
                files.append(os.path.relpath(os.path.join(root, n), snap))
        files.sort()
        lines = ["%s  %s" % (L.sha256_file(os.path.join(snap, p)), p) for p in files]
        with open(os.path.join(snap, "SNAPSHOT.sha256"), "w") as f:
            f.write("\n".join(lines) + "\n")
        snapshot_sha = L.sha256_file(os.path.join(snap, "SNAPSHOT.sha256"))
        config_sha = L.sha256_file(os.path.join(snap, "src", "config.json"))
        merged_sha = L.sha256_file(os.path.join(snap, "src", "config_merged.json"))

        deviations = [
            "OPENROAD_THREADS %d in the run's config.json (local only; the submission and the action use %d)"
            % (q["threads"], L.ACTION_OPENROAD_THREADS),
            "LibreLane runs in apptainer (same 3.1.0.dev3 image, via SIF) instead of docker",
            "--jobs = allocated CPUs (runner: 4 vCPU)",
        ]
        if q["mode"] == "fast":
            deviations.append("fast mode: --skip " + " ".join(L.FAST_SKIP_STEPS))
        params = {
            "schema": 1,
            "run_id": run_id,
            "created": L.now_iso(),
            "core": q["core"],
            "core_path": core_path,
            "core_tag": q["core_tag"],
            "core_sha256": core_sha,
            "tiles": q["tiles"],
            "die_area": sizes[q["tiles"]],
            "floorplan": fp.get("id", q["floorplan"]),
            "floorplan_file": os.path.relpath(fp_path, L.REPO) if fp_path.startswith(L.REPO) else fp_path,
            "floorplan_sha256": L.sha256_file(fp_path),
            "density": q["density"],
            "period": q["period"],
            "clock_hz": clock_hz,
            "pl_hold": q["pl_hold"],
            "grt_hold": q["grt_hold"],
            "mode": q["mode"],
            "threads": q["threads"],
            "overrides": q["overrides"],
            "skip_steps": L.FAST_SKIP_STEPS if q["mode"] == "fast" else [],
            "config_changes_vs_repo": changes,
            "config_sha256": config_sha,
            "config_merged_sha256": merged_sha,
            "snapshot_sha256": snapshot_sha,
            "predicted_drt0418": pred_418,
            "repo": {"path": L.REPO, "head": L.git_head(),
                     "dirty_inputs": L.git_dirty(["src", "info.yaml", "macros", "models", "floorplans"])},
            "flow": {"sif": L.SIF, "pdk_root": L.PDK_ROOT, "tt_dir": L.TT_DIR,
                     "tt_rev": (open(os.path.join(L.FLOW_ROOT, "tt-support-tools.rev")).read().strip()
                                if os.path.exists(os.path.join(L.FLOW_ROOT, "tt-support-tools.rev")) else None),
                     "librelane": "3.1.0.dev3"},
            "local_only_deviations": deviations,
            "tag": q.get("tag"),
        }

        # ---- idempotency
        if os.path.isdir(os.path.join(run_dir, "snap")):
            old = L.load_json(os.path.join(run_dir, "params.json"), {})
            if old.get("snapshot_sha256") == snapshot_sha:
                if not quiet:
                    print("unchanged", run_id)
                return run_dir, old
            if not force:
                raise SystemExit("run %s exists with a different snapshot (old %s, new %s); use --force"
                                 % (run_id, old.get("snapshot_sha256"), snapshot_sha))
            shutil.rmtree(os.path.join(run_dir, "snap"))
        L.mkdir_p(run_dir)
        os.rename(snap, os.path.join(run_dir, "snap"))
        shutil.move(os.path.join(tmp, "floorplan_check.txt"), os.path.join(run_dir, "floorplan_check.txt"))
        L.write_json_atomic(os.path.join(run_dir, "params.json"), params)
        if not quiet:
            print("created", run_id)
        return run_dir, params
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_override(s):
    if "=" not in s:
        raise argparse.ArgumentTypeError("override must be KEY=JSON")
    k, v = s.split("=", 1)
    try:
        val = json.loads(v)
    except ValueError:
        val = v
    return k, val


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--core", default=L.REPO_DEFAULTS["core"], help="core Verilog (repo-relative or absolute)")
    ap.add_argument("--core-tag", default=L.REPO_DEFAULTS["core_tag"])
    ap.add_argument("--tiles", default=L.REPO_DEFAULTS["tiles"])
    ap.add_argument("--period", type=float, default=L.REPO_DEFAULTS["period"], help="CLOCK_PERIOD, ns")
    ap.add_argument("--density", type=float, default=L.REPO_DEFAULTS["density"], help="PL_TARGET_DENSITY_PCT")
    ap.add_argument("--floorplan", default=L.REPO_DEFAULTS["floorplan"], help="floorplans/<id>.json or a path")
    ap.add_argument("--pl-hold", type=float, default=L.REPO_DEFAULTS["pl_hold"])
    ap.add_argument("--grt-hold", type=float, default=L.REPO_DEFAULTS["grt_hold"])
    ap.add_argument("--mode", choices=("fast", "full"), default=L.REPO_DEFAULTS["mode"])
    ap.add_argument("--threads", type=int, default=L.REPO_DEFAULTS["threads"], help="local OPENROAD_THREADS")
    ap.add_argument("--override", action="append", type=parse_override, default=[],
                    help="extra LibreLane key, KEY=JSON (null deletes the key); repeatable")
    ap.add_argument("--run-id", help="explicit run id (default: derived from the parameters)")
    ap.add_argument("--runs-dir", default=L.RUNS_DIR)
    ap.add_argument("--force", action="store_true", help="replace an existing snapshot with different content")
    ap.add_argument("--no-check", action="store_true", help="keep the snapshot even if the floorplan check fails")
    args = ap.parse_args()
    q = normalize_params({
        "core": args.core, "core_tag": args.core_tag, "tiles": args.tiles, "period": args.period,
        "density": args.density, "floorplan": args.floorplan, "pl_hold": args.pl_hold,
        "grt_hold": args.grt_hold, "mode": args.mode, "threads": args.threads,
        "overrides": dict(args.override), "run_id": args.run_id,
    })
    run_dir, params = build(q, args.runs_dir, force=args.force, check=not args.no_check)
    print(run_dir)
    print(json.dumps({k: params[k] for k in ("run_id", "core_sha256", "config_merged_sha256", "snapshot_sha256")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
