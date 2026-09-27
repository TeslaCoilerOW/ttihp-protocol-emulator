#!/usr/bin/env python3
"""Yosys equiv_induct with more correspondences: invariants, liveness-masked registers, SRAM model.

usage: equiv_inv.py --design DIR --spec SPEC.json --out DIR [--ids a,b | --ids-file F --index I --count K]
                    [--seq N] [--timeout S]

Extends the campaign's equiv_mutant.py (equiv_make / equiv_simple / equiv_induct on
every register output) in three ways; each keeps the check sound:

1. SRAM model. Each RM_IHPSG13_1P_64x16_c2 instance is replaced, in both the gold
   core and the mutant, by the macro's registered-read behaviour
   (models/RM_IHPSG13_1P_core_behavioral.v) over unknown contents: DOUT is a
   register that changes only on a read; a read of the address read last, with
   no write since, returns the same word; any other read returns a free input
   word `sram_free` that the gold and the mutant share. The model's registers
   (gd, la, lv) get correspondences like every register, so both copies must
   read the same addresses, and a named write-port vector (enable, address,
   data) is matched too, so both must write the same words.
2. Invariants. A named wire inv_<n> is the invariant (a Boolean over the gold
   core's registers) in the gold copy and the constant 1 in the mutant copy; its
   correspondence makes equiv_induct assume the invariant on the previous time
   steps and prove it on the next (k-induction with the invariant as a lemma).
3. Liveness masking. A register that the spec marks as dead in some states is
   not matched directly; instead view_<reg> = live ? reg : 0 is matched in both
   copies (live computed from each copy's own registers, which the other
   correspondences force to be equal). The mutant may then differ in that
   register only while it is dead.

The result is 'equivalent' only when equiv_status reports every $equiv cell
proven: then, from equal states satisfying the invariants (the all-zero reset
state does), the pins, SRAM traffic and every correspondence stay equal
forever, for any SRAM contents and host/pin inputs.

Design variants with asynchronously reset flops (cn, cn_s2, diet4, diet2): as in
equiv_mutant.py, the outputs of the $adff registers are matched points too and
`async2sync` rewrites both copies before equiv_make. A design without
asynchronous flops (the design of record) gets exactly the script that
docs/mutation-push.md section 4.3 ran.
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

OSS = os.environ.get("OSS_CAD_SUITE", "")
YOSYS = os.path.join(OSS, "bin", "yosys") if OSS else "yosys"
INST = re.compile(r"  RM_IHPSG13_1P_64x16_c2 (\w+) \(\n(.*?)\n  \);\n", re.S)
PORT = re.compile(r"\.(A_\w+)\((.*?)\)(?:,)?$")
REG = re.compile(r"^  reg (?:\[(\d+):0\] )?(\w+)(?: \[(\d+):0\])?;$", re.M)
SRAM_ORDER = ["e0_hi", "e0_lo", "e1_hi", "e1_lo", "e2_hi", "e2_lo", "e3_hi", "e3_lo"]
ONE = "1'b1"
ASYNC_CELL = re.compile(r"^\s*cell \$(adff|adffe|aldff|aldffe|dffsr|dffsre)\b", re.M)


def has_async(design: Path) -> bool:
    """True when the prepared core (base.il) has asynchronously reset or loaded flops (design variants)."""
    return bool(ASYNC_CELL.search((design / "base.il").read_text()))


def transform(src: str, name: str, spec: dict, gold: bool) -> str:
    src = src.replace("module protocol_emulator_core(uio_in, ena, rst_n, clk, ui_in, uo_out, uio_out, uio_oe);",
                      f"module {name}(uio_in, ena, rst_n, clk, ui_in, uo_out, uio_out, uio_oe, sram_free);", 1)
    assert f"module {name}(" in src
    body = []
    seen = []

    def repl(m: re.Match) -> str:
        inst = m.group(1)
        k = SRAM_ORDER.index(inst.replace("instruction_sram_", ""))
        seen.append(k)
        p = {}
        for line in m.group(2).splitlines():
            pm = PORT.match(line.strip())
            p[pm.group(1)] = pm.group(2)
        body.append(f"""  reg [15:0] gd{k} = 16'd0; reg [5:0] la{k} = 6'd0; reg lv{k} = 1'b0;
  wire rd{k} = ({p['A_MEN']}) & ({p['A_REN']}) & ~({p['A_WEN']});
  wire wr{k} = ({p['A_MEN']}) & ({p['A_WEN']});
  wire [22:0] sram_w{k} = wr{k} ? {{1'b1, {p['A_ADDR']}, {p['A_DIN']}}} : 23'd0;
  always @(posedge clk) begin
    if (rd{k}) begin gd{k} <= (lv{k} && la{k} == {p['A_ADDR']}) ? gd{k} : sram_free[{16 * k + 15}:{16 * k}];
      la{k} <= {p['A_ADDR']}; lv{k} <= 1'b1; end
    else if (wr{k}) lv{k} <= 1'b0;
  end
  assign {p['A_DOUT']} = gd{k};
""")
        return ""

    src = INST.sub(repl, src)
    assert sorted(seen) == list(range(8)), seen
    for e in range(4):
        hi, lo = SRAM_ORDER.index(f"e{e}_hi"), SRAM_ORDER.index(f"e{e}_lo")
        body.append(f"  wire [31:0] instr{e} = {{gd{hi}, gd{lo}}};\n")
    for n, inv in enumerate(spec.get("invariants", [])):
        body.append(f"  wire inv_{n} = {inv if gold else ONE};\n")
    for reg, live in spec.get("dead", {}).items():
        width = spec["widths"][reg]
        body.append(f"  wire [{width - 1}:0] view_{reg} = ({live}) ? {reg} : {width}'d0;\n")
    src = src.replace("  input [7:0] uio_in;\n", "  input [127:0] sram_free;\n  input [7:0] uio_in;\n", 1)
    idx = src.rfind("endmodule")
    return src[:idx] + "".join(body) + src[idx:]


def one(mid: str, cmd: str, design: Path, spec: dict, seq: int, timeout: int) -> dict:
    t0 = time.time()
    work = Path(tempfile.mkdtemp(prefix=f"pe-ei-{mid}-", dir=os.environ.get("TMPDIR") or "/tmp"))
    res = {"id": mid, "cmd": cmd, "spec": {k: v for k, v in spec.items() if k != "widths"}, "seq": seq,
           "host": socket.gethostname(),
           "slurm_array": f"{os.environ.get('SLURM_ARRAY_JOB_ID')}_{os.environ.get('SLURM_ARRAY_TASK_ID')}"}
    try:
        mut = "" if "-mode none" in cmd else cmd + "; "
        subprocess.run([YOSYS, "-q", "-p", f"read_rtlil {design}/base.il; {mut}write_verilog -noattr mut.v"],
                       check=True, cwd=work, stdout=subprocess.DEVNULL, timeout=600)
        gold_src = (design / "base_roundtrip.v").read_text()
        widths = {}
        for m in REG.finditer(gold_src):
            widths[m.group(2)] = int(m.group(1)) + 1 if m.group(1) else 1
        spec = dict(spec, widths=widths)
        (work / "gold.v").write_text(transform(gold_src, "gold", spec, True))
        (work / "gate.v").write_text(transform((work / "mut.v").read_text(), "gate", spec, False))
        dead = list(spec.get("dead", {}))

        is_async = has_async(design)

        def keep(side: str) -> str:
            expr = (f"{side}/t:$dff %x:+[Q] {side}/t:$dff %d {side}/x:* %u {side}/w:sram_w* %u "
                    f"{side}/w:inv_* %u {side}/w:view_* %u")
            if is_async:  # as equiv_mutant.py: asynchronously reset registers are matched points too
                expr += f" {side}/t:$adff %x:+[Q] {side}/t:$adff %d %u"
            return expr
        script = f"""read_verilog -mem2reg gold.v
read_verilog -mem2reg gate.v
proc
opt_clean
setundef -zero
select -set keepg {keep('gold')}
select -set keepm {keep('gate')}
rename -hide gold/w:* @keepg %d
rename -hide gate/w:* @keepm %d
"""
        for r in dead:  # dead registers are matched only through their liveness-masked views
            script += f"rename -hide gold/w:{r}\nrename -hide gate/w:{r}\n"
        if is_async:  # as equiv_mutant.py: each asynchronous clear becomes a mux on the register output
            script += "async2sync\n"
        flow = os.environ.get("EQUIV_FLOW", "simple+induct")
        script += "equiv_make gold gate equiv\nhierarchy -top equiv\n"
        if "simple" in flow:
            script += f"equiv_simple -seq {seq}\n"
        script += f"equiv_induct {'-undef ' if 'undef' in flow else ''}-seq {seq}\nequiv_status\n"
        (work / "eq.ys").write_text(script)
        log = work / "eq.log"
        p = subprocess.run([YOSYS, "-s", str(work / "eq.ys"), "-l", str(log)], cwd=work,
                           stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=timeout)
        text = log.read_text(errors="replace")
        m = re.findall(r"Of those cells (\d+) are proven and (\d+) are unproven", text)
        if m:
            proven, unproven = map(int, m[-1])
            res.update(proven=proven, unproven=unproven,
                       status="equivalent" if unproven == 0 and "Equivalence successfully proven" in text
                       else "not_proven")
            tailtext = text[text.rfind("EQUIV_STATUS"):]
            res["unproven_points"] = re.findall(r"Unproven \$equiv \S+: (\S+) (\S+)", tailtext)[:400]
        else:
            res.update(status="error", rc=p.returncode, log_tail=text.splitlines()[-15:])
    except subprocess.TimeoutExpired:
        res["status"] = "timeout"
    except Exception as e:  # noqa: BLE001
        res.update(status="error", error=repr(e))
    finally:
        res["seconds"] = round(time.time() - t0, 1)
        shutil.rmtree(work, ignore_errors=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ids")
    ap.add_argument("--ids-file", type=Path)
    ap.add_argument("--index", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", 0)))
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--seq", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=3000)
    ap.add_argument("--spec-key", help="use this spec entry for every mutant (diagnostics)")
    ap.add_argument("--tag", default="", help="suffix of the result file name")
    a = ap.parse_args()
    design = a.design.resolve()
    cmds = {}
    for line in (design / "mutations.tsv").read_text().splitlines():
        mid, _, cmd = line.split("\t", 2)
        cmds[mid] = cmd
    specs = json.loads(a.spec.read_text())
    ids = a.ids.split(",") if a.ids else [x for i, x in enumerate(a.ids_file.read_text().split())
                                         if i % a.count == a.index]
    a.out.mkdir(parents=True, exist_ok=True)
    for mid in ids:
        target = a.out / f"{mid}{a.tag}.json"
        if target.exists():
            continue
        spec = specs[a.spec_key] if a.spec_key else specs.get(mid, specs.get("default", {}))
        r = one(mid, cmds[mid], design, spec, a.seq, a.timeout)
        tmp = target.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(r, indent=1))
        os.replace(tmp, target)
        print(mid, r["status"], r.get("proven"), r.get("unproven"), r["seconds"], flush=True)


if __name__ == "__main__":
    main()
