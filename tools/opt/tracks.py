# SPDX-License-Identifier: Apache-2.0
"""Tracks of the physical-design optimizer (docs/optimization.md, "Tracks").

A track is one (core, die, clock period, search space) combination with its
own optuna study. Its committed configuration ("base") is what the repository
builds for that combination today:

  dor     design of record, 8x4, 20 ns: src/config.json as committed
  dor15   design of record, 8x4, CLOCK_PERIOD 15 ns (66.7 MHz): src/config.json
          with CLOCK_PERIOD 15 (and info.yaml clock_hz 66666667)
  dor13   design of record, 8x4, CLOCK_PERIOD 13.33 ns (75.0 MHz)
  diet4_6x4  the 6x4 fallback: variants6x4/protocol_emulator_core.v (diet4) on
          the 6x4 die, src/config.json with variants6x4/config.overlay.json
          merged as variants6x4/switch.py does (RFC 7386), 20 ns
  <variant>  one per published variant core (PE_WORK/variants/cores), 8x4, 20 ns

The track id contains the core sha256, the die, the period and the sha256 of
the committed configuration without its knob-controlled keys ("fixed part"):
knob values are absolute (space.py), so a new committed knob set (an adopted
configuration) keeps the tracks, and only a change of a fixed key (PDN,
pins, ...) starts new ones.

effective_config() mirrors scripts/sweep/make_snapshot.py build() (the
snapshot's config.json) without writing a snapshot; the driver checks it
against make_snapshot's config_changes_vs_repo for every trial it builds.
Standard library only.
"""

import copy
import glob
import hashlib
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import space as SPACE  # noqa: E402

MACRO = "RM_IHPSG13_1P_64x16_c2"
LOCAL_ONLY_KEYS = ("OPENROAD_THREADS",)

# (name, kind, period ns, weight group). Periods: 15 ns is the Tiny Tapeout demo
# board's clock limit (about 66.5 MHz from the RP2040), 13.33 ns = 75 MHz.
DOR_TRACKS = [("dor", "dor", 20.0, "dor20"), ("dor15", "freq", 15.0, "dor15"), ("dor13", "freq", 13.33, "dor13")]
SIXBY4 = dict(name="diet4_6x4", kind="6x4", period=20.0, weight="6x4", tiles="6x4", gl_variant="diet4",
              core="variants6x4/protocol_emulator_core.v", overlay="variants6x4/config.overlay.json")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha256_text(s):
    return hashlib.sha256(s.encode()).hexdigest()


def fmt_num(x):
    """20 -> '20', 13.33 -> '13p33' (as scripts/sweep/sweeplib.fmt_num)."""
    s = ("%.6f" % float(x)).rstrip("0").rstrip(".")
    return s.replace("-", "m").replace(".", "p")


def num(x):
    """JSON number as make_snapshot writes it: 20.0 -> 20, 0.05 -> 0.05."""
    x = float(x)
    return int(x) if x == int(x) else x


def merge_patch(target, patch):
    """RFC 7386 JSON merge patch (same as variants6x4/switch.py merge_patch)."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


def load_overlay(path):
    with open(path) as f:
        ov = json.load(f)
    ov.pop("//", None)
    return ov


def fixed_part(cfg):
    """The committed configuration without the knob-controlled keys and comments."""
    c = copy.deepcopy(cfg)
    c.pop("//", None)
    for k in SPACE.KNOB_KEYS:
        c.pop(k, None)
    for spec in (c.get("MACROS") or {}).values():
        spec.pop("instances", None)
    return c


def fixed_sha(cfg):
    return sha256_text(json.dumps(fixed_part(cfg), sort_keys=True, separators=(",", ":")))


def load_floorplans(tree):
    out = {}
    for p in sorted(glob.glob(os.path.join(tree, "floorplans", "*.json"))):
        with open(p) as f:
            fp = json.load(f)
        out[fp.get("id") or os.path.basename(p)[:-5]] = fp
    return out


def effective_config(repo_cfg, fp, snap, ov, period, threads):
    """The config.json make_snapshot build() writes for these parameters (see its source)."""
    cfg = copy.deepcopy(repo_cfg)
    cfg["CLOCK_PERIOD"] = num(period)
    cfg["PL_TARGET_DENSITY_PCT"] = num(snap["density"])
    cfg["PL_RESIZER_HOLD_SLACK_MARGIN"] = num(snap["pl_hold"])
    cfg["GRT_RESIZER_HOLD_SLACK_MARGIN"] = num(snap["grt_hold"])
    cfg["OPENROAD_THREADS"] = int(threads)
    macro = fp.get("macro", MACRO)
    cfg["MACROS"][macro]["instances"] = copy.deepcopy(fp["instances"])
    for k, v in list((fp.get("config") or {}).items()) + list(ov.items()):
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    return cfg


def changes(ref, cfg, skip=LOCAL_ONLY_KEYS):
    """{key: {"repo": old, "run": new}} like make_snapshot's config_changes_vs_repo."""
    out = {}
    for k in sorted(set(ref) | set(cfg)):
        if k == "//" or k in skip:
            continue
        a, b = ref.get(k, "<unset>"), cfg.get(k, "<unset>")
        if not SPACE.same(a, b):
            out[k] = {"repo": a, "run": b}
    return out


def apply_changes(ref, ch):
    cfg = copy.deepcopy(ref)
    for k, v in (ch or {}).items():
        if v.get("run") == "<unset>":
            cfg.pop(k, None)
        else:
            cfg[k] = v.get("run")
    return cfg


def islands(tree, cfg, src_dir, tiles):
    """variants6x4/row_islands.py on a config: number of row segments without a lattice stripe
    (each would get a short VPWR/VGND strap, which fails the precheck)."""
    sys.path.insert(0, os.path.join(tree, "variants6x4"))
    import row_islands as RI  # noqa: E402
    core, n_rows = RI.core_box(tiles)
    macros = RI.macros_from_config(cfg, src_dir)
    strp = RI.stripes(cfg, core)
    hh = RI.dbu(cfg.get("FP_MACRO_HORIZONTAL_HALO", RI.DEFAULT_HALO))
    vh = RI.dbu(cfg.get("FP_MACRO_VERTICAL_HALO", RI.DEFAULT_HALO))
    return len(RI.islands(core, n_rows, macros, strp, hh, vh, RI.obstructions(cfg)))


class Track(object):
    """One track's fixed properties, its committed configuration and its search space."""

    def __init__(self, tree, name, kind, core, tiles, period, weight, gl_variant, overlay=None, restrict=None,
                 source=None):
        self.tree = tree
        self.name, self.kind, self.tiles, self.period = name, kind, tiles, float(period)
        self.weight, self.gl_variant, self.source = weight, gl_variant, source
        self.core = core
        self.core_sha = sha256_file(core)
        with open(os.path.join(tree, "src", "config.json")) as f:
            self.repo_cfg = json.load(f)
        self.repo_cfg_sha = sha256_file(os.path.join(tree, "src", "config.json"))
        self.overlay = overlay
        self.overlay_sha = sha256_file(overlay) if overlay else None
        self.base_cfg = merge_patch(self.repo_cfg, load_overlay(overlay)) if overlay else copy.deepcopy(self.repo_cfg)
        self.floorplans = load_floorplans(tree)
        self.space = "6x4" if tiles == "6x4" else "8x4"
        if restrict is None and self.space == "6x4":
            restrict = self.island_free_restrict()
        self.restrict = restrict or {}
        self.D = SPACE.defs(self.space, self.restrict)
        self.base = SPACE.base_knobs(self.base_cfg, self.floorplans, self.D)
        self.fixed = fixed_sha(self.base_cfg)
        self.id = "%s-%s-%s-p%s-f%s" % (name, self.core_sha[:6], tiles, fmt_num(period), self.fixed[:6])

    def island_free_restrict(self):
        """Vertical halo values for which the committed 6x4 placement has no island (row_islands)."""
        fp = self.floorplans[SPACE.FLOORPLANS_6X4[0]]
        ok = []
        for vh in SPACE.KNOBS["FP_MACRO_VERTICAL_HALO"]["choices"]:
            cfg = copy.deepcopy(self.base_cfg)
            cfg["MACROS"][fp.get("macro", MACRO)]["instances"] = fp["instances"]
            cfg.update(fp.get("config") or {})
            cfg["FP_MACRO_HORIZONTAL_HALO"] = SPACE.HALO_FIXED[fp["id"]]
            cfg["FP_MACRO_VERTICAL_HALO"] = vh
            if islands(self.tree, cfg, os.path.join(self.tree, "src"), self.tiles) == 0:
                ok.append(vh)
        return {"FP_MACRO_VERTICAL_HALO": ok}

    def record(self):
        """Fields of the track_new event."""
        return dict(track=self.id, name=self.name, kind=self.kind, core=self.core, core_sha256=self.core_sha,
                    tiles=self.tiles, period=self.period, weight=self.weight, gl_variant=self.gl_variant,
                    config_sha256=self.repo_cfg_sha, fixed_sha256=self.fixed, overlay=self.overlay,
                    overlay_sha256=self.overlay_sha, space=self.space, restrict=self.restrict, base=self.base,
                    source=self.source, primary=(self.kind == "dor"))

    # ---- knob sets
    def complete(self, knobs):
        return SPACE.complete(knobs, self.base, self.D)

    def materialize(self, knobs):
        kn = self.complete(knobs)
        fp = self.floorplans[kn["floorplan"]]
        return SPACE.materialize(kn, self.base, self.repo_cfg, fp.get("config"), self.D)

    def effective(self, knobs, threads=4):
        kn = self.complete(knobs)
        snap, ov = self.materialize(kn)
        return effective_config(self.repo_cfg, self.floorplans[kn["floorplan"]], snap, ov, self.period, threads)

    def diff_vs_repo(self, knobs):
        """Keys to change in the frozen src/config.json to build this knob set (local-only keys
        excluded)."""
        return changes(self.repo_cfg, self.effective(knobs))

    def diff_vs_base(self, knobs):
        """Keys to change relative to the committed build of this track (for the 6x4 track: the
        overlay merged into src/config.json, i.e. what variants6x4/switch.py apply writes)."""
        return changes(self.base_cfg, self.effective(knobs))


def discover(tree, work, log=print):
    """All tracks of a frozen tree: the design-of-record tracks, the 6x4 track and one per
    published variant core (sha256 as in PE_WORK/variants/cores/MANIFEST.sha256; a core whose
    body equals the design of record is skipped)."""
    out = []
    dor_core = os.path.join(tree, "src", "protocol_emulator_core.v")
    for name, kind, period, weight in DOR_TRACKS:
        out.append(Track(tree, name, kind, dor_core, "8x4", period, weight, "base"))
    six_core = os.path.join(tree, SIXBY4["core"])
    six_ov = os.path.join(tree, SIXBY4["overlay"])
    if os.path.exists(six_core) and os.path.exists(six_ov):
        out.append(Track(tree, SIXBY4["name"], SIXBY4["kind"], six_core, SIXBY4["tiles"], SIXBY4["period"],
                         SIXBY4["weight"], SIXBY4["gl_variant"], overlay=six_ov))
    man = os.path.join(work, "variants", "cores", "MANIFEST.sha256")
    if os.path.exists(man):
        pbody = body_sha(dor_core)
        with open(man) as f:
            lines = [l.split() for l in f if l.strip()]
        for parts in lines:
            if len(parts) != 2 or not parts[1].endswith(".v"):
                continue
            sha, fname = parts
            src = os.path.join(os.path.dirname(man), fname)
            name = os.path.basename(fname)[:-2]
            try:
                if sha256_file(src) != sha:
                    log("variant %s: sha256 differs from MANIFEST.sha256 (being republished?); skipped" % fname)
                    continue
                if body_sha(src) == pbody:
                    continue  # the design of record under another header
            except (IOError, OSError) as e:
                log("variant %s: %s" % (fname, e))
                continue
            out.append(Track(tree, name, "variant", src, "8x4", 20.0, "variant", name, source=fname))
    return out


def body_sha(path):
    """sha256 of a generated core without its leading '//' header lines (the published
    variants/cores/base.v differs from src/ only in the generator comment)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        lines = f.read().split(b"\n")
    i = 0
    while i < len(lines) and lines[i].startswith(b"//"):
        i += 1
    h.update(b"\n".join(lines[i:]))
    return h.hexdigest()
