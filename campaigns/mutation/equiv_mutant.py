#!/usr/bin/env python3
"""Try to prove surviving mutants equivalent to the unmutated core (Yosys equiv_*).

usage: equiv_mutant.py --design DIR --ids-file FILE --out DIR [--index I --count K] [--ids a,b]

gold = design/base_roundtrip.v (the prepared core, unmutated), gate = the
mutant, both written by `write_verilog` from base.il and read back with
`read_verilog -mem2reg`, so the eight FIFO arrays become ordinary registers
with the same public names (_7456[k]) in both copies.

Only these nets keep their names and are matched by equiv_make: every register
output, the module ports and every net on an SRAM macro pin. All other public
nets of both copies are hidden (`rename -hide`), so a difference that is
masked downstream of the mutated cell is not reported as a mismatch. The SRAM
macro instances have the same public names in both copies and equiv_make
shares them: the SRAM input nets carry $equiv cells (they must be proven
equal) and the fetched words are the same in both copies, which is exactly
"same program contents, same access sequence".

equiv_simple then equiv_induct prove that, from any state in which the two
copies agree on every register, they agree on every register, output and SRAM
input in the next cycle, hence forever. "equivalent" is reported only when
equiv_status says every $equiv cell is proven. This is a sufficient, not a
necessary, condition: a mutant that differs only in unreachable states is
reported "not_proven" and must be classified by hand.

Asynchronous resets (design variants cn, cn_s2, diet4, diet2): the register
outputs of $adff cells are kept as well, and `async2sync` rewrites both copies
after the hiding step, so SAT sees each asynchronous clear as a multiplexer on
the register output (the named register net stays the matched point). A design
without asynchronous flops is processed exactly as before.
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

YOSYS = os.path.join(os.environ["OSS_CAD_SUITE"], "bin", "yosys") if "OSS_CAD_SUITE" in os.environ else "yosys"

KEEP_EXPR = ("gold/t:$dff %x:+[Q] gold/t:$dff %d gold/x:* %u "
             "gold/t:RM_IHPSG13_1P_64x16_c2 %x:+[A_DOUT,A_ADDR,A_DIN,A_MEN,A_WEN,A_REN] "
             "gold/t:RM_IHPSG13_1P_64x16_c2 %d %u")
# the same plus the outputs of asynchronously reset registers (designs with $adff cells only)
KEEP_EXPR_ASYNC = KEEP_EXPR + " gold/t:$adff %x:+[Q] gold/t:$adff %d %u"
ASYNC_CELL = re.compile(r"^\s*cell \$(adff|adffe|aldff|aldffe|dffsr|dffsre)\b", re.M)


def has_async(design: Path) -> bool:
    """True when the prepared core (base.il) has asynchronously reset or loaded flops."""
    return bool(ASYNC_CELL.search((design / "base.il").read_text()))


def read_pair(design: Path, gate_v: Path) -> str:
    return f"""read_verilog -lib {design}/sram_blackbox.v
read_verilog -mem2reg {design}/base_roundtrip.v
rename protocol_emulator_core gold
read_verilog -mem2reg {gate_v}
rename protocol_emulator_core gate
proc
opt_clean
"""


def ensure_keep(design: Path, work: Path) -> tuple[Path, Path]:
    gold, gate = design / "equiv_keep_gold.txt", design / "equiv_keep_gate.txt"
    if gold.exists() and gate.exists():
        return gold, gate
    tmp = work / "keep.txt"
    expr = KEEP_EXPR_ASYNC if has_async(design) else KEEP_EXPR
    script = read_pair(design, design / "base_roundtrip.v") + f"select -write {tmp} {expr}\n"
    (work / "keep.ys").write_text(script)
    subprocess.run([YOSYS, "-q", "-s", str(work / "keep.ys")], check=True, cwd=work,
                   stdout=subprocess.DEVNULL)
    lines = tmp.read_text().splitlines()
    for path, prefix in ((gold, "gold/"), (gate, "gate/")):
        t = path.with_suffix(f".tmp.{os.getpid()}")
        t.write_text("".join(prefix + ln.split("/", 1)[1] + "\n" for ln in lines))
        os.replace(t, path)
    return gold, gate


def one(mid: str, cmd: str, design: Path, seq: int) -> dict:
    t0 = time.time()
    work = Path(tempfile.mkdtemp(prefix=f"pe-eq-{mid}-", dir=os.environ.get("TMPDIR") or "/tmp"))
    res = {"id": mid, "cmd": cmd, "host": socket.gethostname(),
           "slurm_array": f"{os.environ.get('SLURM_ARRAY_JOB_ID')}_{os.environ.get('SLURM_ARRAY_TASK_ID')}"}
    try:
        keep_gold, keep_gate = ensure_keep(design, work)
        gate_v = work / "mutant.v"
        mut = "" if "-mode none" in cmd else cmd + "; "
        subprocess.run([YOSYS, "-q", "-p", f"read_rtlil {design}/base.il; {mut}write_verilog -noattr {gate_v}"],
                       check=True, cwd=work, stdout=subprocess.DEVNULL, timeout=300)
        script = read_pair(design, gate_v) + f"""select -read {keep_gate}
select -set keepgate %
select -clear
rename -hide gate/w:* @keepgate %d
select -read {keep_gold}
select -set keepgold %
select -clear
rename -hide gold/w:* @keepgold %d
{"async2sync" if has_async(design) else ""}
equiv_make gold gate equiv
hierarchy -top equiv
equiv_simple -seq {seq}
equiv_induct -ignore-unknown-cells -seq {seq}
equiv_status
"""
        (work / "eq.ys").write_text(script)
        log = work / "eq.log"
        p = subprocess.run([YOSYS, "-s", str(work / "eq.ys"), "-l", str(log)], cwd=work,
                           stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=3000)
        text = log.read_text(errors="replace")
        m = re.findall(r"Of those cells (\d+) are proven and (\d+) are unproven", text)
        if m:
            proven, unproven = map(int, m[-1])
            res.update(proven=proven, unproven=unproven,
                       status="equivalent" if unproven == 0 and "Equivalence successfully proven" in text
                       else "not_proven")
            # names of the first unproven points, for the survivor write-up
            tailtext = text[text.rfind("EQUIV_STATUS"):]
            res["unproven_points"] = re.findall(r"Unproven \$equiv \S+: (\S+) (\S+)", tailtext)[:12]
        else:
            res.update(status="error", rc=p.returncode, log_tail=text.splitlines()[-15:])
    except subprocess.TimeoutExpired:
        res["status"] = "timeout"
    except subprocess.CalledProcessError as e:
        res.update(status="error", rc=e.returncode)
    finally:
        res["seconds"] = round(time.time() - t0, 1)
        shutil.rmtree(work, ignore_errors=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ids-file", type=Path)
    ap.add_argument("--ids")
    ap.add_argument("--index", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", 0)))
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--seq", type=int, default=2)
    ap.add_argument("--deadline", type=float, default=0, help="stop starting new mutants after N seconds")
    args = ap.parse_args()
    args.design = args.design.resolve()
    cmds = {}
    for line in (args.design / "mutations.tsv").read_text().splitlines():
        mid, _, cmd = line.split("\t", 2)
        cmds[mid] = cmd
    if args.ids:
        ids = args.ids.split(",")
    else:
        all_ids = args.ids_file.read_text().split()
        ids = [x for i, x in enumerate(all_ids) if i % args.count == args.index]
    args.out.mkdir(parents=True, exist_ok=True)
    t_task = time.time()
    for mid in ids:
        if args.deadline and time.time() - t_task > args.deadline:
            break
        target = args.out / f"{mid}.json"
        if target.exists():
            continue
        r = one(mid, cmds[mid], args.design, args.seq)
        t = target.with_suffix(f".tmp.{os.getpid()}")
        t.write_text(json.dumps(r, indent=1))
        os.replace(t, target)
        print(mid, r["status"], r.get("proven"), r.get("unproven"), r["seconds"], flush=True)


if __name__ == "__main__":
    main()
