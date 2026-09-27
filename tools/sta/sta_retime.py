#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Re-time a LibreLane layout at a chosen clock period with LibreLane's own
post-route STA script (tools/sta/README.md).

  sta_retime.py prepare --run-dir RUN --out CASE [--period NS]
  sta_retime.py run     --out CASE [--jobs N]
  sta_retime.py collect --out CASE
  sta_retime.py all     --run-dir RUN --out CASE [--period NS] [--jobs N]
  sta_retime.py compare CASE_A CASE_B [--json FILE]

The host needs only the Python standard library and apptainer. The analysis
runs inside the LibreLane image: OpenSTA's `sta` executes LibreLane's
scripts/openroad/sta/corner.tcl (which sources openroad/common/io.tcl and reads
scripts/base.sdc) once per corner, with the process environment that
LibreLane's OpenROAD.STAPostPNR step builds for that corner. The layout's final
netlist and nominal SPEF are read unchanged. The configuration comes from the
run's resolved.json; only CLOCK_PERIOD is replaced. Nothing of LibreLane is
copied: its scripts are read from the image at run time and their sha256 is
recorded in CASE/case.json.

Locations (command-line options override them):
  PE_SIF        the LibreLane image; default $PE_FLOW_ROOT/librelane-3.1.0.dev3.sif
  PE_FLOW_ROOT  default $PE_WORK/sram-flow (as in scripts/sweep/sweeplib.py)
  PE_WORK       default <repo>/build/tt-work
  PE_PDK_ROOT or PDK_ROOT   the PDK root; default $PE_FLOW_ROOT/pdk
  PE_APPTAINER  the apptainer executable; default: apptainer on PATH
"""
import argparse
import csv
import datetime
import fnmatch
import gzip
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
EXTRA_TCL = os.path.join(HERE, "pe_extra.tcl")

# LibreLane files that define the analysis, relative to librelane/scripts in the image.
LL_SCRIPTS = {
    "corner.tcl": "openroad/sta/corner.tcl",
    "io.tcl": "openroad/common/io.tcl",
    "set_global_connections.tcl": "openroad/common/set_global_connections.tcl",
    "base.sdc": "base.sdc",
}
# Their sha256 in librelane-3.1.0.dev3.sif, the image that scripts/sweep/ uses.
# Used only to flag a different image in case.json; the run is not refused.
LL_REFERENCE = {
    "librelane_version": "3.1.0.dev3",
    "opensta_version": "2.7.0",
    "sha256": {
        "corner.tcl": "e0096884c1929ed80506366ecc3e809c78c8493180e366fc095e2b09f5c36f53",
        "io.tcl": "c7b28322f0853c7c6ebe6076a454c0baf3d97cc6194c0931eae7bf17a442b80f",
        "set_global_connections.tcl":
            "80c6f145ee34a241e089c916656d5586da2c330cdcca5ae7004b1786a1e3b599",
        "base.sdc": "34c8f331c366db51a78ed8a0d2a7bbac3b6c1cfe0277a04c6aca9ba4948e7f78",
    },
}

# Every configuration variable that corner.tcl, io.tcl and base.sdc read when
# corner.tcl runs under OpenSTA (not OpenROAD), with its LibreLane 3.1.0.dev3
# type, which decides the Tcl text (TclStep.value_to_tcl: an integral Decimal
# is written as "15.0", an int as "8"). CLOCK_PERIOD is replaced by --period;
# CURRENT_NL is the netlist; OPENLANE_SDC_IDEAL_CLOCKS is set to 0 by
# STAPostPNR.prepare_env. Values of None are not written, as in LibreLane.
ENV_VARS = [
    ("DESIGN_NAME", "str"),
    ("CLOCK_PORT", "str_or_list"),
    ("CLOCK_PERIOD", "Decimal"),
    ("IO_DELAY_CONSTRAINT", "Decimal"),
    ("MAX_FANOUT_CONSTRAINT", "int"),
    ("MAX_TRANSITION_CONSTRAINT", "Decimal"),
    ("MAX_CAPACITANCE_CONSTRAINT", "Decimal"),
    ("SYNTH_DRIVING_CELL", "str"),
    ("SYNTH_CLK_DRIVING_CELL", "str"),
    ("OUTPUT_CAP_LOAD", "Decimal"),
    ("CLOCK_UNCERTAINTY_CONSTRAINT", "Decimal"),
    ("CLOCK_TRANSITION_CONSTRAINT", "Decimal"),
    ("TIME_DERATING_CONSTRAINT", "Decimal"),
    ("STA_MAX_VIOLATOR_COUNT", "int"),
]
REQUIRED_VARS = {"DESIGN_NAME", "CLOCK_PERIOD", "IO_DELAY_CONSTRAINT", "MAX_FANOUT_CONSTRAINT",
                 "SYNTH_DRIVING_CELL", "OUTPUT_CAP_LOAD", "CLOCK_UNCERTAINTY_CONSTRAINT",
                 "CLOCK_TRANSITION_CONSTRAINT", "TIME_DERATING_CONSTRAINT"}
# Configuration this tool does not reproduce: the run must leave them unset.
UNSUPPORTED_VARS = ["PNR_SDC_FILE", "SIGNOFF_SDC_FILE", "EXTRA_LIBS", "EXTRA_VERILOG_MODELS",
                    "EXTRA_SPEFS", "STA_EXTRA_CORNER_TCL_FILE"]

# The ten timing metrics that each corner reports (LibreLane metric names
# without the "__corner:<corner>" suffix).
TIMING_KEYS = ["timing__setup__ws", "timing__setup__tns", "timing__setup_vio__count",
               "timing__setup_r2r__ws", "timing__setup_r2r_vio__count",
               "timing__hold__ws", "timing__hold__tns", "timing__hold_vio__count",
               "timing__hold_r2r__ws", "timing__hold_r2r_vio__count"]
CLASSES = ["r2r", "in2reg", "reg2out", "in2out"]
MODES = ["setup", "hold"]
# Class slacks are printed with 6 decimals and OpenSTA keeps times as float32
# seconds (ULP about 0.9e-6 ns at 8-15 ns, 1.8e-6 ns at 15-30 ns).
CONSISTENCY_TOL = Decimal("1e-6")
SHIFT_TOL = Decimal("5e-6")


class Fail(Exception):
    pass


# ---------------------------------------------------------------- utilities

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def dump_json(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, default=str)
        f.write("\n")
    os.replace(tmp, path)


def load_resolved(path):
    # parse_float=Decimal keeps the JSON text of every number, as LibreLane does.
    with open(path) as f:
        return json.load(f, parse_float=Decimal)


def git_state():
    try:
        head = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, universal_newlines=True, check=True)
        st = subprocess.run(["git", "-C", REPO, "status", "--porcelain", "--", "tools/sta"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            universal_newlines=True, check=True)
        return {"commit": head.stdout.strip(), "tools_sta_modified": bool(st.stdout.strip())}
    except (OSError, subprocess.CalledProcessError):
        return None


# ------------------------------------------ LibreLane's Tcl value encoding
# Re-implemented from librelane/common/tcl.py (TclUtils) and
# librelane/steps/tclstep.py (TclStep.value_to_tcl), LibreLane 3.1.0.dev3.

_UNSAFE = re.compile(r"[^\w@%+=:,./-]", re.ASCII).search
_ESCAPES_IN_QUOTES = re.compile(r"([\\\$\"\[])")


def tcl_escape(s):
    if s == "":
        return '""'
    if not _UNSAFE(s):
        return s
    return '"' + _ESCAPES_IN_QUOTES.sub(r"\\\1", s).replace("\n", r"\n") + '"'


def tcl_join(items):
    return " ".join(tcl_escape(x) for x in items)


def value_to_tcl(value):
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out.append(value_to_tcl(k))
            out.append(value_to_tcl(v))
        return tcl_join(out)
    if isinstance(value, (list, tuple)):
        return tcl_join(value_to_tcl(x) for x in value)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, Decimal):
        if value.to_integral_value() == value:
            return "{:.1f}".format(value)
        return str(value)
    return str(value)


def typed(value, kind):
    if value is None:
        return None
    if kind == "Decimal":
        return value if isinstance(value, Decimal) else Decimal(str(value))
    if kind == "int":
        if isinstance(value, bool) or int(value) != value:
            raise Fail("expected an integer, got %r" % (value,))
        return int(value)
    if kind == "str_or_list":
        return value if isinstance(value, list) else str(value)
    return str(value)


def read_tcl_env(path):
    """set ::env(KEY) VALUE lines of a LibreLane _env*.tcl file (raw Tcl text)."""
    out = {}
    with open(path) as f:
        for ln in f:
            m = re.match(r"set ::env\((\w+)\) (.*)$", ln.rstrip("\n"))
            if m:
                out[m.group(1)] = m.group(2)
    return out


# ------------------------------------------- LibreLane's view selection
# Re-implemented from librelane/common/toolbox.py (filter_views,
# get_timing_files_categorized) and common/misc.py (Filter).

def filter_views(views_by_corner, corner):
    if not views_by_corner:
        return []
    allow = [k for k in views_by_corner if not k.startswith("!")]
    deny = [k for k in views_by_corner if k.startswith("!")]
    keys = [k for k in allow if fnmatch.fnmatchcase(corner, k)]
    keys += [k for k in deny if not fnmatch.fnmatchcase(corner, k[1:])]
    out = []
    for k in keys:
        v = views_by_corner[k]
        out += [v] if isinstance(v, str) else list(v)
    return out


def remap_pdk_path(path, pdk, pdk_root):
    """/any/prefix/<pdk>/libs.ref/... -> <pdk_root>/<pdk>/libs.ref/..."""
    marker = "/" + pdk + "/"
    i = path.rfind(marker)
    if i < 0:
        raise Fail("cannot map %s into PDK_ROOT (no '%s' component)" % (path, marker))
    local = os.path.join(pdk_root, pdk, path[i + len(marker):])
    if not os.path.isfile(local):
        raise Fail("PDK file not found: %s (from %s)" % (local, path))
    return os.path.realpath(local)


def find_macro_file(module, basename, macro_dirs):
    for d in macro_dirs:
        for cand in (os.path.join(d, module, basename), os.path.join(d, basename)):
            if os.path.isfile(cand):
                return os.path.realpath(cand)
    raise Fail("macro file %s of %s not found in %s" % (basename, module, macro_dirs))


def corner_timing_files(resolved, corner, pdk_root, macro_dirs):
    """The libs that OpenSTAStep._get_corner_files gives corner.tcl for one corner."""
    pdk = resolved["PDK"]
    libs = [remap_pdk_path(p, pdk, pdk_root) for p in filter_views(resolved["CELL_LIBS"], corner)]
    libs += [remap_pdk_path(p, pdk, pdk_root)
             for p in filter_views(resolved.get("PAD_LIBS") or {}, corner)]
    notes = []
    for module, macro in (resolved.get("MACROS") or {}).items():
        if resolved.get("STA_MACRO_PRIORITIZE_NL"):
            nl = macro.get("nl") or []
            nl = [nl] if isinstance(nl, str) else nl
            spefs = filter_views(macro.get("spef") or {}, corner)
            if nl and spefs:
                raise Fail("macro %s is timed from its netlist and SPEF at %s; "
                           "this tool supports liberty macros only" % (module, corner))
        mlibs = filter_views(macro.get("lib") or {}, corner)
        if not mlibs:
            notes.append("no lib for macro %s at %s: black-boxed, as in LibreLane" % (module, corner))
            continue
        libs += [find_macro_file(module, os.path.basename(p), macro_dirs) for p in mlibs]
    return libs, notes


def liberty_clock_pins(lib_path):
    """Names of pins declared with 'clock : true' in a liberty file."""
    pins = set()
    current = None
    with open(lib_path, errors="replace") as f:
        for ln in f:
            m = re.match(r"\s*(pin|bus)\s*\(\s*([^)\s]+)\s*\)", ln)
            if m:
                current = m.group(2) if m.group(1) == "pin" else None
                continue
            if current and re.match(r'\s*clock\s*:\s*"?true"?\s*;', ln):
                pins.add(current)
    return sorted(pins)


# --------------------------------------------------------------- inputs

def find_run_inputs(run_dir):
    """Locate resolved.json, netlist, nominal SPEF and the run's metrics.csv.

    Layouts:
      tt_submission   the gds workflow artifact: resolved.json, <design>.v,
                      <design>.nom.spef, stats/metrics.csv
      sweep_run       scripts/sweep/ and tools/opt/ runs: out/resolved.json,
                      out/final/nl/<design>.nl.v.gz, out/final/spef/nom/<design>.nom.spef.gz,
                      out/metrics.csv
      librelane_run   a LibreLane run directory: resolved.json, final/nl/, final/spef/nom/,
                      final/metrics.csv
    """
    def first(*cands):
        for c in cands:
            if os.path.isfile(c):
                return c
        return None

    for layout, base in (("sweep_run", os.path.join(run_dir, "out")), ("tt_or_ll", run_dir)):
        resolved = os.path.join(base, "resolved.json")
        if not os.path.isfile(resolved):
            continue
        d = load_json(resolved)["DESIGN_NAME"]
        if layout == "tt_or_ll":
            nl = first(os.path.join(base, d + ".v"))
            spef = first(os.path.join(base, d + ".nom.spef"))
            if nl and spef:
                return {"layout": "tt_submission", "resolved": resolved, "netlist": nl, "spef": spef,
                        "ref_metrics": first(os.path.join(base, "stats", "metrics.csv"))}
            layout = "librelane_run"
        fin = os.path.join(base, "final")
        nl = first(os.path.join(fin, "nl", d + ".nl.v"), os.path.join(fin, "nl", d + ".nl.v.gz"))
        spef = first(os.path.join(fin, "spef", "nom", d + ".nom.spef"),
                     os.path.join(fin, "spef", "nom", d + ".nom.spef.gz"))
        if nl and spef:
            return {"layout": layout, "resolved": resolved, "netlist": nl, "spef": spef,
                    "ref_metrics": first(os.path.join(base, "metrics.csv"),
                                         os.path.join(fin, "metrics.csv")),
                    "sha256sums": first(os.path.join(fin, "SHA256SUMS"))}
    raise Fail("no known run layout in %s (see find_run_inputs)" % run_dir)


def stage_input(src, inputs_dir):
    """Decompress a .gz input into CASE/inputs/; read other inputs in place."""
    rec = {"source": src, "source_sha256": sha256_file(src)}
    if src.endswith(".gz"):
        os.makedirs(inputs_dir, exist_ok=True)
        dst = os.path.join(inputs_dir, os.path.basename(src)[:-3])
        with gzip.open(src, "rb") as fi, open(dst + ".tmp", "wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
        os.replace(dst + ".tmp", dst)
        rec["path"] = os.path.realpath(dst)
        rec["sha256"] = sha256_file(dst)
    else:
        rec["path"] = os.path.realpath(src)
        rec["sha256"] = rec["source_sha256"]
    return rec


def check_sha256sums(sums_file, files):
    """Check .gz inputs against the run's final/SHA256SUMS ("<hash>  ./<rel>")."""
    base = os.path.dirname(sums_file)
    want = {}
    with open(sums_file) as f:
        for ln in f:
            parts = ln.split()
            if len(parts) == 2:
                want[os.path.normpath(os.path.join(base, parts[1]))] = parts[0]
    out = {}
    for rec in files:
        key = os.path.normpath(rec["source"])
        if key in want:
            out[os.path.relpath(key, base)] = want[key] == rec["source_sha256"]
    return out


# ------------------------------------------------------------ the image

def tool_paths(args):
    work = os.environ.get("PE_WORK", os.path.join(REPO, "build", "tt-work"))
    flow = os.environ.get("PE_FLOW_ROOT", os.path.join(work, "sram-flow"))
    sif = args.sif or os.environ.get("PE_SIF") or os.path.join(flow, "librelane-3.1.0.dev3.sif")
    pdk_root = (args.pdk_root or os.environ.get("PE_PDK_ROOT") or os.environ.get("PDK_ROOT")
                or os.path.join(flow, "pdk"))
    return os.path.realpath(sif), os.path.realpath(pdk_root)


def apptainer_exe():
    exe = os.environ.get("PE_APPTAINER") or shutil.which("apptainer")
    if not exe:
        raise Fail("apptainer not found: put it on PATH or set PE_APPTAINER")
    return exe


PROBE = r"""
import hashlib, json, os, subprocess, sys
import librelane
s = os.path.join(os.path.dirname(librelane.__file__), "scripts")
out = {"librelane_version": getattr(librelane, "__version__", None), "scripts_dir": s,
       "sha256": {}}
for k, rel in json.loads(sys.argv[1]).items():
    with open(os.path.join(s, rel), "rb") as f:
        out["sha256"][k] = hashlib.sha256(f.read()).hexdigest()
p = subprocess.run(["sta", "-version"], stdout=subprocess.PIPE, universal_newlines=True)
out["opensta_version"] = p.stdout.strip()
out["sta_path"] = subprocess.run(["sh", "-c", "command -v sta"], stdout=subprocess.PIPE,
                                 universal_newlines=True).stdout.strip()
print("PROBE_JSON " + json.dumps(out))
"""


def probe_image(exe, sif):
    cmd = [exe, "exec", "--cleanenv", "--containall", "--no-home", "--pwd", "/", sif,
           "python3", "-c", PROBE, json.dumps(LL_SCRIPTS)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    for ln in p.stdout.splitlines():
        if ln.startswith("PROBE_JSON "):
            return json.loads(ln[len("PROBE_JSON "):])
    raise Fail("image probe failed (rc %d): %s" % (p.returncode, p.stderr.strip()[-2000:]))


def bind_dirs(paths):
    """Smallest set of directories that covers every path (for apptainer --bind)."""
    dirs = sorted({os.path.realpath(p if os.path.isdir(p) else os.path.dirname(p)) for p in paths})
    out = []
    for d in dirs:
        if not any(d == o or d.startswith(o + os.sep) for o in out):
            out.append(d)
    return out


# -------------------------------------------------------------- prepare

def cmd_prepare(args):
    out = os.path.realpath(args.out)
    os.makedirs(out, exist_ok=True)
    sif, pdk_root = tool_paths(args)
    if not os.path.isfile(sif):
        raise Fail("LibreLane image not found: %s (set PE_SIF or --sif)" % sif)
    if not os.path.isdir(pdk_root):
        raise Fail("PDK root not found: %s (set PE_PDK_ROOT/PDK_ROOT or --pdk-root)" % pdk_root)

    found = find_run_inputs(os.path.realpath(args.run_dir)) if args.run_dir else {"layout": "explicit"}
    for key in ("resolved", "netlist", "spef", "ref_metrics"):
        if getattr(args, key):
            found[key] = os.path.realpath(getattr(args, key))
    for key in ("resolved", "netlist", "spef"):
        if not found.get(key):
            raise Fail("no %s: give --run-dir or --%s" % (key, key.replace("_", "-")))
    resolved = load_resolved(found["resolved"])
    design = resolved["DESIGN_NAME"]

    for k in UNSUPPORTED_VARS:
        if resolved.get(k):
            raise Fail("%s is set in %s; this tool re-times with LibreLane's base.sdc and the "
                       "run's own libraries only" % (k, found["resolved"]))

    inputs_dir = os.path.join(out, "inputs")
    netlist = stage_input(found["netlist"], inputs_dir)
    spef = stage_input(found["spef"], inputs_dir)
    os.makedirs(inputs_dir, exist_ok=True)
    shutil.copyfile(found["resolved"], os.path.join(inputs_dir, "resolved.json"))
    ref = None
    if found.get("ref_metrics"):
        ref = {"path": found["ref_metrics"], "sha256": sha256_file(found["ref_metrics"])}
        shutil.copyfile(found["ref_metrics"], os.path.join(inputs_dir, "ref_metrics.csv"))
    sums = None
    if found.get("sha256sums"):
        sums = check_sha256sums(found["sha256sums"], [netlist, spef])
        if not all(sums.values()):
            raise Fail("inputs do not match %s: %s" % (found["sha256sums"], sums))

    run_period = typed(resolved["CLOCK_PERIOD"], "Decimal")
    period = Decimal(args.period) if args.period else run_period

    corners = args.corners.split(",") if args.corners else list(resolved["STA_CORNERS"])
    for c in corners:
        if not fnmatch.fnmatchcase(c, "nom_*"):
            raise Fail("corner %s: only nom_* corners (read with the nominal SPEF) are supported" % c)
    macro_dirs = [os.path.realpath(d) for d in (args.macro_dir or [])] or \
        [os.path.join(REPO, "macros")]
    corner_libs, notes = {}, []
    for c in corners:
        libs, n = corner_timing_files(resolved, c, pdk_root, macro_dirs)
        corner_libs[c] = [{"path": p, "sha256": sha256_file(p)} for p in libs]
        notes += n

    # Macro instances and clock pins for the path classes (pe_extra.tcl).
    instances, clock_pins, macro_libs = [], set(), set()
    for module, macro in (resolved.get("MACROS") or {}).items():
        instances += list((macro.get("instances") or {}).keys())
        for c in corners:
            for p in filter_views(macro.get("lib") or {}, c):
                macro_libs.add(find_macro_file(module, os.path.basename(p), macro_dirs))
    for p in sorted(macro_libs):
        clock_pins.update(liberty_clock_pins(p))
    if args.macro_clock_pins is not None:
        clock_pins = set(args.macro_clock_pins.split(",")) - {""}

    # _TCL_ENV_IN: the configuration variables that the scripts read.
    env_vals = {}
    for name, kind in ENV_VARS:
        v = typed(resolved.get(name), kind)
        if name == "CLOCK_PERIOD":
            v = period
        if v is None:
            if name in REQUIRED_VARS:
                raise Fail("%s missing from %s" % (name, found["resolved"]))
            continue
        env_vals[name] = tcl_escape(value_to_tcl(v))
    env_vals["CURRENT_NL"] = tcl_escape(netlist["path"])
    env_vals["OPENLANE_SDC_IDEAL_CLOCKS"] = "0"
    env_tcl = os.path.join(out, "env.tcl")
    with open(env_tcl, "w") as f:
        f.write("# _TCL_ENV_IN for LibreLane corner.tcl, written by tools/sta/sta_retime.py\n")
        for k, v in env_vals.items():
            f.write("set ::env(%s) %s\n" % (k, v))

    env_compare = None
    if args.compare_env:
        theirs = read_tcl_env(args.compare_env)
        skip = {"CURRENT_NL"} | ({"CLOCK_PERIOD"} if period != run_period else set())
        keys = {}
        for name in [n for n, _ in ENV_VARS] + ["OPENLANE_SDC_IDEAL_CLOCKS"]:
            if name in skip:
                continue
            ours = env_vals.get(name)
            keys[name] = {"ours": ours, "librelane": theirs.get(name), "same": ours == theirs.get(name)}
        env_compare = {"file": os.path.realpath(args.compare_env),
                       "sha256": sha256_file(args.compare_env), "skipped": sorted(skip),
                       "vars": keys, "all_same": all(v["same"] for v in keys.values())}
        if not env_compare["all_same"]:
            raise Fail("env.tcl differs from %s: %s" % (
                args.compare_env, {k: v for k, v in keys.items() if not v["same"]}))

    exe = apptainer_exe()
    image = probe_image(exe, sif)
    st = os.stat(sif)
    image.update({"sif": sif, "sif_size": st.st_size,
                  "sif_mtime": datetime.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(),
                  "sif_sha256": sha256_file(sif) if args.hash_sif else None,
                  "apptainer": exe,
                  "apptainer_version": subprocess.run([exe, "--version"], stdout=subprocess.PIPE,
                                                      universal_newlines=True).stdout.strip()})
    image["matches_reference"] = (image["sha256"] == LL_REFERENCE["sha256"]
                                  and image["librelane_version"] == LL_REFERENCE["librelane_version"])
    if not image["matches_reference"]:
        print("warning: LibreLane scripts in %s differ from LibreLane %s (see case.json)"
              % (sif, LL_REFERENCE["librelane_version"]), file=sys.stderr)
    fallback = str(resolved.get("FALLBACK_SDC"))
    base_sdc = os.path.join(image["scripts_dir"], LL_SCRIPTS["base.sdc"])

    io_pct = typed(resolved["IO_DELAY_CONSTRAINT"], "Decimal")
    case = {
        "tool": "tools/sta/sta_retime.py",
        "tool_sha256": {n: sha256_file(os.path.join(HERE, n))
                        for n in ("sta_retime.py", "pe_extra.tcl", "slurm_job.sh")},
        "tool_git": git_state(),
        "label": args.label or os.path.basename(out),
        "prepared": now(), "host": socket.gethostname(),
        "run_dir": os.path.realpath(args.run_dir) if args.run_dir else None,
        "layout": found["layout"],
        "design": design,
        "period": str(period), "run_period": str(run_period),
        "io_delay_constraint_pct": str(io_pct),
        "io_delay_ns": str(period * io_pct / 100),
        "control": ref is not None and period == run_period,
        "inputs": {"resolved": {"path": found["resolved"], "sha256": sha256_file(found["resolved"])},
                   "netlist": netlist, "spef": spef, "ref_metrics": ref, "sha256sums_check": sums},
        "resolved_checks": {
            "librelane_version": (resolved.get("meta") or {}).get("librelane_version"),
            "fallback_sdc": fallback,
            "fallback_sdc_is_image_base_sdc": fallback == base_sdc,
            "sta_macro_prioritize_nl": resolved.get("STA_MACRO_PRIORITIZE_NL"),
        },
        "pdk": resolved["PDK"], "pdk_root": pdk_root, "macro_dirs": macro_dirs,
        "corners": corners,
        "corner_libs": corner_libs,
        "notes": notes,
        "macros": {"instances": instances, "clock_pins": sorted(clock_pins)},
        "env_tcl": {"path": env_tcl, "vars": env_vals},
        "env_compare": env_compare,
        "image": image,
        "sdc_in": base_sdc,
        "binds": bind_dirs([out, HERE, pdk_root, netlist["path"], spef["path"]]
                           + [lib["path"] for libs in corner_libs.values() for lib in libs]),
    }
    dump_json(case, os.path.join(out, "case.json"))
    print("prepared %s: %s at %s ns (run period %s ns, control %s), corners %s" % (
        out, design, period, run_period, case["control"], " ".join(corners)))
    return 0


# ------------------------------------------------------------------ run

def split_log(cdir):
    """Split corner.tcl stdout as LibreLane's run_subprocess does:
    %OL_CREATE_REPORT/%OL_END_REPORT blocks -> report files, %OL_METRIC* -> metrics.json.
    sta.log, max.rpt and min.rpt are gzip-compressed afterwards."""
    metrics, fh = {}, None
    with open(os.path.join(cdir, "sta.log"), encoding="utf8", errors="replace") as f:
        for line in f:
            if line.startswith("%OL_CREATE_REPORT"):
                if fh:
                    fh.close()
                fh = open(os.path.join(cdir, line.split(None, 1)[1].strip()), "w")
                continue
            if line.startswith("%OL_END_REPORT"):
                if fh:
                    fh.close()
                fh = None
                continue
            if line.startswith("%OL_METRIC"):
                parts = line.split()
                kind, key, val = parts[0], parts[1], " ".join(parts[2:])
                if kind == "%OL_METRIC_F":
                    metrics[key] = {"repr": val, "value": float(val)}
                elif kind == "%OL_METRIC_I":
                    metrics[key] = {"repr": val, "value": int(val)}
                else:
                    metrics[key] = {"repr": val, "value": val}
                continue
            if fh:
                fh.write(line)
    if fh:
        fh.close()
    dump_json(metrics, os.path.join(cdir, "metrics.json"))
    for name in ("sta.log", "max.rpt", "min.rpt"):
        p = os.path.join(cdir, name)
        if os.path.exists(p):
            with open(p, "rb") as src, gzip.open(p + ".gz", "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            os.remove(p)
    return any(k.startswith("timing__setup__ws__corner:") for k in metrics)


def run_corner(case, corner):
    out = os.path.dirname(case["env_tcl"]["path"])
    cdir = os.path.join(out, corner)
    if os.path.isdir(cdir):
        shutil.rmtree(cdir)
    os.makedirs(cdir)
    scripts = case["image"]["scripts_dir"]
    # The process environment of OpenROAD.STAPostPNR for one corner
    # (OpenSTAStep.prepare_env, CornerFileList.set_env, MultiCornerSTA.run_corner,
    # STAPostPNR.prepare_env; the rest of the configuration is in _TCL_ENV_IN).
    # _SDF_SAVE_DIR and _LIB_SAVE_DIR are not set: they only write SDF and lib
    # views after all metrics. STA_EXTRA_CORNER_TCL_FILE and PE_* add the
    # read-only path-class queries of pe_extra.tcl.
    env = [
        ("_TCL_ENV_IN", case["env_tcl"]["path"]),
        ("SCRIPTS_DIR", scripts),
        ("_CURRENT_CORNER_NAME", corner),
        ("_CURRENT_CORNER_LIBS", tcl_join(lib["path"] for lib in case["corner_libs"][corner])),
        ("_CURRENT_CORNER_NETLISTS", ""),
        ("_CURRENT_CORNER_SPEFS", ""),
        ("_CURRENT_SPEF_BY_CORNER", case["inputs"]["spef"]["path"]),
        ("_SDC_IN", case["sdc_in"]),
        ("STA_EXTRA_CORNER_TCL_FILE", EXTRA_TCL),
        ("PE_OUT", cdir),
        ("PE_MACRO_INSTANCES", tcl_join(case["macros"]["instances"])),
        ("PE_MACRO_CLOCK_PINS", tcl_join(case["macros"]["clock_pins"])),
    ]
    exe = apptainer_exe()
    cmd = [exe, "exec", "--cleanenv", "--containall", "--no-home"]
    for d in bind_dirs(case["binds"] + [HERE]):
        cmd += ["--bind", d]
    cmd += ["--pwd", cdir, case["image"]["sif"], "env"] + ["%s=%s" % kv for kv in env]
    cmd += ["sta", "-no_splash", "-exit", os.path.join(scripts, LL_SCRIPTS["corner.tcl"])]
    t0 = time.time()
    with open(os.path.join(cdir, "sta.log"), "w") as log:
        rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT)
    seconds = round(time.time() - t0, 1)
    ok = split_log(cdir)
    rec = {"corner": corner, "rc": rc, "metrics_found": ok, "seconds": seconds,
           "host": socket.gethostname(), "finished": now(), "env": dict(env), "command": cmd}
    dump_json(rec, os.path.join(cdir, "run.json"))
    print("corner %s: rc %d, %.0f s" % (corner, rc, seconds))
    return 0 if rc == 0 and ok else 1


def cmd_run(args):
    out = os.path.realpath(args.out)
    case = load_json(os.path.join(out, "case.json"))
    jobs = args.jobs or int(os.environ.get("SLURM_CPUS_PER_TASK", "1"))
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        rcs = list(pool.map(lambda c: run_corner(case, c), case["corners"]))
    return 0 if not any(rcs) else 1


# -------------------------------------------------------------- collect

def read_extra(path):
    d = {}
    with open(path) as f:
        for ln in f:
            parts = ln.split()
            if not parts:
                continue
            k = parts[0]
            if parts[1:] == ["none"]:
                d[k] = None
            elif k.split("_")[0] in MODES and k not in ("setup", "hold"):
                d[k] = {"slack": parts[1], "start": parts[2], "end": parts[3]}
            else:
                d[k] = " ".join(parts[1:])
    return d


def read_metrics_csv(path):
    d = {}
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) == 2:
                d[row[0]] = row[1]
    return d


def collect_case(out):
    case = load_json(os.path.join(out, "case.json"))
    res = {"label": case["label"], "design": case["design"], "period": case["period"],
           "run_period": case["run_period"], "io_delay_ns": case["io_delay_ns"],
           "inputs": {k: case["inputs"][k]["sha256"] for k in ("netlist", "spef")},
           "input_paths": {k: case["inputs"][k]["source"] for k in ("netlist", "spef")},
           "env_vars": case["env_tcl"]["vars"],
           "image": {k: case["image"][k] for k in ("librelane_version", "opensta_version", "sha256",
                                                   "matches_reference", "sif_sha256")},
           "corners": {}, "ok": True}
    for c in case["corners"]:
        cdir = os.path.join(out, c)
        run = load_json(os.path.join(cdir, "run.json"))
        m = load_json(os.path.join(cdir, "metrics.json"))
        e = read_extra(os.path.join(cdir, "extra.txt"))
        suffix = "__corner:" + c
        r = {"rc": run["rc"], "seconds": run["seconds"],
             "metrics": {k[:-len(suffix)]: v["repr"] for k, v in m.items() if k.endswith(suffix)},
             "classes": {mode: {k: e.get(mode + "_" + k) for k in ["all"] + CLASSES} for mode in MODES},
             "counts": {k: e[k] for k in e if k.startswith("n_")}}
        for mode in MODES:
            cls = [Decimal(v["slack"]) for k, v in r["classes"][mode].items() if k != "all" and v]
            allv = Decimal(r["classes"][mode]["all"]["slack"])
            ws = Decimal(r["metrics"]["timing__%s__ws" % mode])
            r[mode + "_consistent"] = (abs(min(cls) - allv) <= CONSISTENCY_TOL
                                       and abs(ws - allv) <= CONSISTENCY_TOL)
            res["ok"] &= r[mode + "_consistent"]
        res["ok"] &= run["rc"] == 0
        res["corners"][c] = r
    ctl = {"applicable": case["control"], "ref": case["inputs"]["ref_metrics"]}
    if case["control"]:
        ref = read_metrics_csv(os.path.join(out, "inputs", "ref_metrics.csv"))
        rows, maxdiff, ok = {}, 0.0, True
        for c, r in res["corners"].items():
            for k, got in sorted(r["metrics"].items()):
                want = ref.get(k + "__corner:" + c)
                if want is None:
                    continue
                diff = 0.0 if float(got) == float(want) else abs(float(got) - float(want))
                rows["%s__corner:%s" % (k, c)] = {"got": got, "ref": want, "absdiff": diff}
                maxdiff = max(maxdiff, diff)
                ok &= diff == 0.0
        missing = [k for k in TIMING_KEYS for c in res["corners"]
                   if "%s__corner:%s" % (k, c) not in rows]
        ctl.update({"metrics_compared": len(rows), "timing_keys_missing": missing,
                    "max_absdiff": maxdiff, "ok": ok and not missing and bool(rows), "rows": rows})
        res["ok"] &= ctl["ok"]
    else:
        ctl["reason"] = ("period %s ns differs from the run's %s ns" % (case["period"], case["run_period"])
                         if case["inputs"]["ref_metrics"] else "no metrics.csv for this run")
    res["control"] = ctl
    return res


def results_md(res):
    L = ["# %s: %s at %s ns" % (res["label"], res["design"], res["period"]), ""]
    L.append("Run period %s ns; I/O delay %s ns. Overall result ok: %s." % (
        res["run_period"], res["io_delay_ns"], res["ok"]))
    ctl = res["control"]
    if ctl["applicable"]:
        L.append("Control against %s: ok %s, %d metrics, max |diff| %s." % (
            ctl["ref"]["path"], ctl["ok"], ctl["metrics_compared"], ctl["max_absdiff"]))
    else:
        L.append("Control: not applicable (%s)." % ctl["reason"])
    L += ["", "| Corner | Setup WS | Setup TNS | Setup vio | LL r2r setup WS | Hold WS | Hold vio | "
          "LL r2r hold WS | Worst setup path |", "|---|---|---|---|---|---|---|---|---|"]
    for c, r in res["corners"].items():
        m = dict((k, r["metrics"].get(k, "n/a")) for k in TIMING_KEYS)
        s = r["classes"]["setup"]["all"]
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | `%s` -> `%s` |" % (
            c, m["timing__setup__ws"], m["timing__setup__tns"], m["timing__setup_vio__count"],
            m["timing__setup_r2r__ws"], m["timing__hold__ws"], m["timing__hold_vio__count"],
            m["timing__hold_r2r__ws"], s["start"], s["end"]))
    L += ["", "Worst path of each path class (pe_extra.tcl):", "",
          "| Corner | Class | Setup WS | Setup path | Hold WS | Hold path |", "|---|---|---|---|---|---|"]
    for c, r in res["corners"].items():
        for k in ["all"] + CLASSES:
            s, h = r["classes"]["setup"][k], r["classes"]["hold"][k]
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                c, k, s["slack"] if s else "none", "`%s` -> `%s`" % (s["start"], s["end"]) if s else "",
                h["slack"] if h else "none", "`%s` -> `%s`" % (h["start"], h["end"]) if h else ""))
        L.append("| %s | consistent | %s | | %s | |" % (c, r["setup_consistent"], r["hold_consistent"]))
    return "\n".join(L) + "\n"


def cmd_collect(args):
    out = os.path.realpath(args.out)
    res = collect_case(out)
    dump_json(res, os.path.join(out, "results.json"))
    md = results_md(res)
    with open(os.path.join(out, "results.md"), "w") as f:
        f.write(md)
    print(md)
    return 0 if res["ok"] else 1


def cmd_all(args):
    for step in (cmd_prepare, cmd_run, cmd_collect):
        rc = step(args)
        if rc:
            return rc
    return 0


# -------------------------------------------------------------- compare

def expected_shifts(dT, dX):
    """Slack change per path class when the period changes by dT and the
    base.sdc input and output delay (IO_DELAY_CONSTRAINT % of the period, set
    without -min/-max, so it also applies to hold) changes by dX."""
    return {"setup": {"r2r": dT, "in2reg": dT - dX, "reg2out": dT - dX, "in2out": dT - 2 * dX},
            "hold": {"r2r": Decimal(0), "in2reg": dX, "reg2out": dX, "in2out": 2 * dX}}


def dstr(d):
    """Decimal as text; an exact zero as "0" (not "0E-17")."""
    return "0" if d == 0 else str(d)


def compare(a, b):
    """b relative to a: per-corner WS differences; for one layout at two
    periods, also the per-class shifts against the SDC prediction."""
    same_layout = a["inputs"] == b["inputs"]
    other_a = {k: v for k, v in a["env_vars"].items() if k not in ("CLOCK_PERIOD", "CURRENT_NL")}
    other_b = {k: v for k, v in b["env_vars"].items() if k not in ("CLOCK_PERIOD", "CURRENT_NL")}
    out = {"a": a["label"], "b": b["label"], "same_layout": same_layout,
           "other_sdc_vars_equal": other_a == other_b, "corners": {}, "ok": True}
    dT = Decimal(b["period"]) - Decimal(a["period"])
    dX = Decimal(b["io_delay_ns"]) - Decimal(a["io_delay_ns"])
    exp = expected_shifts(dT, dX)
    out.update({"dT": dstr(dT), "dX": dstr(dX)})
    for c in a["corners"]:
        if c not in b["corners"]:
            continue
        ra, rb = a["corners"][c], b["corners"][c]
        row = {}
        for mode in MODES:
            key = "timing__%s__ws" % mode
            row[mode + "_ws_delta"] = dstr(Decimal(rb["metrics"][key]) - Decimal(ra["metrics"][key]))
            key = "timing__%s_r2r__ws" % mode
            row[mode + "_ll_r2r_delta"] = dstr(Decimal(rb["metrics"][key]) - Decimal(ra["metrics"][key]))
        if same_layout and out["other_sdc_vars_equal"]:
            for mode in MODES:
                pred = []
                for k in CLASSES:
                    ca, cb = ra["classes"][mode][k], rb["classes"][mode][k]
                    if not ca or not cb:
                        row["%s_%s" % (mode, k)] = None
                        continue
                    d = Decimal(cb["slack"]) - Decimal(ca["slack"])
                    ok = abs(d - exp[mode][k]) <= SHIFT_TOL
                    row["%s_%s" % (mode, k)] = {
                        "a": ca["slack"], "b": cb["slack"], "delta": dstr(d),
                        "expected": dstr(exp[mode][k]), "ok": ok,
                        "same_path": (ca["start"], ca["end"]) == (cb["start"], cb["end"])}
                    out["ok"] &= ok
                    pred.append((Decimal(ca["slack"]) + exp[mode][k], k))
                p, limiting = min(pred)
                measured = Decimal(rb["metrics"]["timing__%s__ws" % mode])
                row[mode + "_prediction"] = {"predicted": str(p), "limiting_class": limiting,
                                             "measured": str(measured),
                                             "ok": abs(measured - p) <= SHIFT_TOL}
                out["ok"] &= row[mode + "_prediction"]["ok"]
        out["corners"][c] = row
    return out


def compare_md(cmp):
    L = ["# %s relative to %s" % (cmp["b"], cmp["a"]), "",
         "Same layout: %s. Other SDC variables equal: %s. dT %s ns, dX %s ns. ok: %s." % (
             cmp["same_layout"], cmp["other_sdc_vars_equal"], cmp["dT"], cmp["dX"], cmp["ok"]), "",
         "| Corner | Setup WS delta | Hold WS delta | LL r2r setup delta | LL r2r hold delta |",
         "|---|---|---|---|---|"]
    for c, r in cmp["corners"].items():
        L.append("| %s | %s | %s | %s | %s |" % (c, r["setup_ws_delta"], r["hold_ws_delta"],
                                                r["setup_ll_r2r_delta"], r["hold_ll_r2r_delta"]))
    if cmp["same_layout"] and cmp["other_sdc_vars_equal"]:
        L += ["", "| Corner | Quantity | a | b | Delta | Expected | ok | Same path |",
              "|---|---|---|---|---|---|---|---|"]
        for c, r in cmp["corners"].items():
            for mode in MODES:
                for k in CLASSES:
                    v = r.get("%s_%s" % (mode, k))
                    if v:
                        L.append("| %s | %s %s | %s | %s | %s | %s | %s | %s |" % (
                            c, mode, k, v["a"], v["b"], v["delta"], v["expected"], v["ok"],
                            v["same_path"]))
                p = r[mode + "_prediction"]
                L.append("| %s | %s WS prediction | | %s | | %s (%s) | %s | |" % (
                    c, mode, p["measured"], p["predicted"], p["limiting_class"], p["ok"]))
    return "\n".join(L) + "\n"


def cmd_compare(args):
    a = load_json(os.path.join(os.path.realpath(args.case_a), "results.json"))
    b = load_json(os.path.join(os.path.realpath(args.case_b), "results.json"))
    cmp = compare(a, b)
    if args.json:
        dump_json(cmp, args.json)
    print(compare_md(cmp))
    return 0 if cmp["ok"] else 1


# ----------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True

    def prep_opts(p):
        p.add_argument("--run-dir", help="run directory (tt_submission artifact, sweep/optimizer run "
                                         "or LibreLane run directory)")
        p.add_argument("--period", help="CLOCK_PERIOD in ns (default: the run's own)")
        p.add_argument("--resolved", help="resolved.json (overrides --run-dir)")
        p.add_argument("--netlist", help="final netlist, .v or .v.gz (overrides --run-dir)")
        p.add_argument("--spef", help="nominal SPEF, .spef or .spef.gz (overrides --run-dir)")
        p.add_argument("--ref-metrics", help="the run's metrics.csv for the control")
        p.add_argument("--corners", help="comma-separated corners (default: STA_CORNERS)")
        p.add_argument("--macro-dir", action="append",
                       help="directory with macro views, searched as DIR/<macro>/<file> and "
                            "DIR/<file> (default: <repo>/macros)")
        p.add_argument("--macro-clock-pins", help="comma-separated macro clock pin names "
                                                  "(default: pins with 'clock : true' in the macro libs)")
        p.add_argument("--compare-env", help="a LibreLane-written _env*.tcl of the run's STAPostPNR "
                                             "step; fail unless env.tcl agrees with it")
        p.add_argument("--sif", help="LibreLane image (default: $PE_SIF)")
        p.add_argument("--pdk-root", help="PDK root (default: $PE_PDK_ROOT or $PDK_ROOT)")
        p.add_argument("--hash-sif", action="store_true", help="record the image's sha256 (reads it)")
        p.add_argument("--label", help="case label (default: basename of --out)")

    for name, fn, helptext in (("prepare", cmd_prepare, "resolve inputs, write env.tcl and case.json"),
                               ("run", cmd_run, "run corner.tcl for every corner"),
                               ("collect", cmd_collect, "write results.json and results.md"),
                               ("all", cmd_all, "prepare, run and collect")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--out", required=True, help="case directory")
        if name in ("prepare", "all"):
            prep_opts(p)
        if name in ("run", "all"):
            p.add_argument("--jobs", type=int, help="corners in parallel "
                                                    "(default: $SLURM_CPUS_PER_TASK or 1)")
        p.set_defaults(fn=fn)
    p = sub.add_parser("compare", help="compare two collected cases")
    p.add_argument("case_a")
    p.add_argument("case_b")
    p.add_argument("--json", help="write the comparison as JSON")
    p.set_defaults(fn=cmd_compare)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Fail as e:
        print("sta_retime: error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
