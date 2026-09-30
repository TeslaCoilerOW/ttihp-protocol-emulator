#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""RTL timing mutants for the certificates' negative controls.

Each mutant is one exact substitution in a private copy of hardcaml/lib (never
the repository's hardcaml/); formal/run.sh --generate-only then builds that
copy into processor_fv.v exactly as for the real design. A certificate run
against a mutant netlist must fail: the certificates are meant to catch RTL
timing bugs, not only wrong schedules.

    rtl_mutants.py SNAP_DIR OUT_DIR [NAME ...]

SNAP_DIR is a git-archive snapshot of the repository; OUT_DIR/<name>/fbuild/rtl
receives the netlists. OCAML_ENV must point at the OCaml toolchain as for
formal/run.sh.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# name: (what the mutant breaks, FILE, OLD, NEW), FILE relative to hardcaml/lib.
# A site is one substitution, or alternatives for older snapshots (newest
# first): OLD and NEW are then tuples, and so is FILE when the alternatives
# are in different files.
MUTANTS = {
    # The WAIT body of the execute stage (engine.ml); the second alternative is
    # the same case of the opcode switch in snapshots up to 16cfc20.
    "rtl_wait_plus1": ("WAIT n holds for n+1 extra cycles instead of n",
                       "engine.ml",
                       ("| Wait -> Some (finish @ [timer <-- imm24])",
                        "4,finish @ [timer <-- imm24];"),
                       ("| Wait -> Some (finish @ [timer <-- imm24 +:. 1])",
                        "4,finish @ [timer <-- imm24 +:. 1];")),
    # The classic XFER transfer body: in engine_transfer.ml after 16cfc20,
    # which the design of record (no line unit) uses; in engine.ml before.
    # From 76a81f5 to 16cfc20 engine.ml also has the line unit's copy, so that
    # alternative includes the "| None ->" branch; the last one fits snapshots
    # before 76a81f5.
    "rtl_xfer_short_tick": ("after the first XFER transition every half period is one cycle short",
                            ("engine_transfer.ml", "engine.ml", "engine.ml"),
                            ("[xtick <-- d.transfer_period.value; xremaining <-- xremaining.value -:. 1;",
                             "    | None ->\n  [if_ (xtick.value >:. 1) [xtick <-- xtick.value -:. 1]\n"
                             "    [xtick <-- xperiod.value; xremaining <-- xremaining.value -:. 1;",
                             "[xtick <-- xperiod.value; xremaining <-- xremaining.value -:. 1;"),
                            ("[xtick <-- d.transfer_period.value -:. 1; xremaining <-- xremaining.value -:. 1;",
                             "    | None ->\n  [if_ (xtick.value >:. 1) [xtick <-- xtick.value -:. 1]\n"
                             "    [xtick <-- xperiod.value -:. 1; xremaining <-- xremaining.value -:. 1;",
                             "[xtick <-- xperiod.value -:. 1; xremaining <-- xremaining.value -:. 1;")),
    "rtl_od_drive_high": ("open-drain pins are not masked in uio_out; uio_out then shows 1 only while "
                          "uio_oe is 0, so no pad changes (a control the certificates must NOT flag)",
                          "processor.ml", "e.pin_values &: owners.(k).value &: ~:(drains.(k).value)",
                          "e.pin_values &: owners.(k).value"),
    "rtl_od_oe_ungated": ("an enabled open-drain pin at logical 1 keeps its output enable, so it pulls "
                          "low instead of releasing",
                          "processor.ml", "let electrical_mask=(~:(drains.(k).value)) |: (~:(e.pin_values)) in",
                          "let electrical_mask=(~:(drains.(k).value)) |: drains.(k).value in"),
    "rtl_sync_3flop": ("the pad inputs pass through three flip-flops instead of two",
                       "processor.ml", "let synced_pins = reg spec sync1 in",
                       "let synced_pins = reg spec (reg spec sync1) in"),
}


def build(snap: Path, out: Path, name: str) -> Path:
    _, fname, old, new = MUTANTS[name]
    tree = out / name / "src"
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    for part in ("formal", "hardcaml/lib", "hardcaml/bin", "configs"):
        # Not formal/build: after a generation in SNAP_DIR it is a link to the
        # real design's RTL, and the mutant must be built into its own tree.
        shutil.copytree(snap / part, tree / part, ignore=shutil.ignore_patterns("build", "_build"))
    shutil.copy(snap / "hardcaml" / "dune-project", tree / "hardcaml" / "dune-project")
    # The first alternative whose OLD occurs exactly once in its file is used.
    olds, news = (old, new) if isinstance(old, tuple) else ((old,), (new,))
    fnames = fname if isinstance(fname, tuple) else (fname,) * len(olds)
    assert len(fnames) == len(olds) == len(news), name
    counts = []
    for f, o, n in zip(fnames, olds, news):
        src = tree / "hardcaml" / "lib" / f
        text = src.read_text() if src.exists() else ""
        counts.append(text.count(o))
        if counts[-1] == 1:
            src.write_text(text.replace(o, n))
            break
    else:
        raise SystemExit(f"rtl_mutants: {name}: substitution site found "
                         f"{counts} times in {list(fnames)}")
    env = dict(os.environ, FORMAL_WORK=str(out / name / "fbuild"))
    subprocess.run([str(tree / "formal" / "run.sh"), "--generate-only"], cwd=tree, env=env, check=True)
    rtl = out / name / "fbuild" / "rtl"
    print(f"rtl_mutants: {name}: {MUTANTS[name][0]} -> {rtl / 'processor_fv.v'}")
    return rtl


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    snap, out = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    for name in argv[2:] or list(MUTANTS):
        build(snap, out, name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
