#!/usr/bin/env python3
"""Sequential equivalence of one mutant against the unmutated core (miter + ABC).

usage: formal_mutant.py --design DIR --ids-file FILE --out DIR [--index I --count K]
                        [--pdr-seconds S] [--props strict,contract] [--recipe NAME]

For every mutant: yosys `mutate` on base.il -> mut.v; mk_miter.py builds the
gold/mutant miter (shared free SRAM read data, SRAM inputs compared, all flops
zero-initialised, power-up reset); yosys writes an AIGER model the way
SymbiYosys does; ABC runs the recipe:

  lcorr   : fold; strash; lcorr; scorr; pdr -T S   (register correspondence,
            signal correspondence by induction, then IC3/PDR)
  dprove  : fold; strash; dprove                     (ABC's sequential
            equivalence script)

Property `strict`: uo_out, uio_out, uio_oe and SRAM inputs equal every cycle.
Property `contract`: the same, but the read nibble uo_out[3:0] only while
read-valid is high. `contract` runs only when `strict` is not proven.
Status per property: proven | cex (PDR/BMC found an assertion failure) |
unknown (timeout/resource) | error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OSS = os.environ.get("OSS_CAD_SUITE", "")
YOSYS = os.path.join(OSS, "bin", "yosys") if OSS else "yosys"
ABC = os.path.join(OSS, "bin", "yosys-abc") if OSS else "yosys-abc"

AIG_SCRIPT = """read_verilog -sv -mem2reg {defs} miter.v
prep -top miter
flatten
setundef -zero -init
zinit -all
scc -select; simplemap; select -clear
memory_nordff
async2sync
chformal -assume -early
opt_clean
formalff -setundef -clk2ff -ff2anyinit -hierarchy
chformal -live -fair -cover -remove
opt_clean
setundef -undriven -anyseq
opt -fast
delete */t:$print
formalff -assume
flatten
setundef -undriven -anyseq
setattr -unset keep
delete -output
opt -full
techmap
opt -fast
memory_map -formal
formalff -clk2ff -ff2anyinit
simplemap
dffunmap
aigmap
opt_clean
stat
write_aiger -I -B -zinit -no-startoffset {aig}
"""

RECIPES = {
    "gscorr": "read_aiger {aig}; fold; strash; print_stats; &get -n; &scorr; &put; print_stats; pdr -v -l -T {t}",
    "gscorr2": "read_aiger {aig}; fold; strash; print_stats; &get -n; &scorr -F 2; &put; print_stats; scorr; "
               "print_stats; pdr -v -l -T {t}",
    "lcorr": "read_aiger {aig}; fold; strash; print_stats; lcorr; print_stats; scorr; print_stats; pdr -v -l -T {t}",
    "dprove": "read_aiger {aig}; fold; strash; dprove -v",
}


def run(cmd, cwd, log, timeout):
    t0 = time.time()
    with open(log, "wb") as fh:
        try:
            p = subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = None
    return rc, round(time.time() - t0, 1)


def verdict(text: str) -> str:
    if re.search(r"Property proved|Networks are equivalent", text):
        return "proven"
    if re.search(r"was asserted in frame|Networks are NOT EQUIVALENT|Counter-example|output \d+ failed", text,
                 re.I) and "Counter-example is not available" not in text:
        return "cex"
    return "unknown"


def one(mid, cmd, design, props, recipe, pdr_seconds, abc_timeout):
    t0 = time.time()
    work = Path(tempfile.mkdtemp(prefix=f"pe-f-{mid}-", dir=os.environ.get("TMPDIR") or "/tmp"))
    res = {"id": mid, "cmd": cmd, "host": socket.gethostname(), "recipe": recipe,
           "slurm_array": f"{os.environ.get('SLURM_ARRAY_JOB_ID')}_{os.environ.get('SLURM_ARRAY_TASK_ID')}"}
    try:
        mut = "" if "-mode none" in cmd else cmd + "; "
        rc, _ = run([YOSYS, "-q", "-p", f"read_rtlil {design}/base.il; {mut}write_verilog -noattr mut.v"],
                    work, work / "mut.log", 600)
        if rc != 0:
            res["status"] = "error"
            res["error"] = "mutate"
            return res
        subprocess.run(["python3", str(HERE / "mk_miter.py"), str(design / "base_roundtrip.v"), "mut.v", "miter.v"],
                       cwd=work, check=True, env=dict(os.environ, MITER_MODE=os.environ.get("MITER_MODE", "")))
        for prop in props:
            defs = "-DCONTRACT" if prop == "contract" else ""
            aig = f"{prop}.aig"
            (work / f"{prop}.ys").write_text(AIG_SCRIPT.format(defs=defs, aig=aig))
            rc, secs_y = run([YOSYS, "-q", "-s", f"{prop}.ys"], work, work / f"{prop}.ylog", 1200)
            if rc != 0:
                res[prop] = {"status": "error", "stage": "yosys"}
                continue
            script = RECIPES[recipe].format(aig=aig, t=pdr_seconds)
            rc, secs = run([ABC, "-c", script], work, work / f"{prop}.alog", abc_timeout)
            text = (work / f"{prop}.alog").read_text(errors="replace")
            st = verdict(text) if rc is not None else "unknown"
            stats = re.findall(r"lat =\s*(\d+)\s+and =\s*(\d+)", text)
            res[prop] = {"status": st, "abc_seconds": secs, "yosys_seconds": secs_y, "rc": rc,
                         "stats": stats[:4], "tail": text.strip().splitlines()[-6:]}
            if st == "proven":
                break  # strict implies contract
        s = res.get("strict", {}).get("status")
        c = res.get("contract", {}).get("status")
        res["status"] = ("equivalent" if s == "proven" else
                         "contract_equivalent" if c == "proven" else
                         "cex" if s == "cex" and c in (None, "cex") else "unknown")
    except Exception as e:  # noqa: BLE001
        res["status"] = "error"
        res["error"] = repr(e)
    finally:
        res["seconds"] = round(time.time() - t0, 1)
        shutil.rmtree(work, ignore_errors=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ids-file", type=Path)
    ap.add_argument("--ids")
    ap.add_argument("--index", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", 0)))
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--pdr-seconds", type=int, default=600)
    ap.add_argument("--abc-timeout", type=int, default=900, help="wall-clock limit of one ABC run")
    ap.add_argument("--props", default="strict,contract")
    ap.add_argument("--recipe", default="lcorr", choices=sorted(RECIPES))
    a = ap.parse_args()
    design = a.design.resolve()
    cmds = {}
    for line in (design / "mutations.tsv").read_text().splitlines():
        mid, _, cmd = line.split("\t", 2)
        cmds[mid] = cmd
    ids = a.ids.split(",") if a.ids else [x for i, x in enumerate(a.ids_file.read_text().split())
                                         if i % a.count == a.index]
    a.out.mkdir(parents=True, exist_ok=True)
    for mid in ids:
        target = a.out / f"{mid}.json"
        if target.exists():
            continue
        r = one(mid, cmds[mid], design, a.props.split(","), a.recipe, a.pdr_seconds, a.abc_timeout)
        tmp = target.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(r, indent=1))
        os.replace(tmp, target)
        print(mid, r["status"], r["seconds"], flush=True)


if __name__ == "__main__":
    main()
