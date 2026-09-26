#!/usr/bin/env python3
"""Run one campaign stage for a set of mutants (one Slurm array task).

usage: run_mutant.py --design DIR --stage STAGE --ids-file FILE --out DIR
                     [--index I --count K] [--ids 3,17,...]

For every mutant id assigned to this task (position % K == I in FILE, or the
explicit --ids), and whose result file OUT/<id>.json does not exist yet:

  1. unpack DIR/testtree.tgz (src/project.v, test/, firmware/, models/ of the
     frozen snapshot) into a node-local temporary directory;
  2. build the mutant core: `read_rtlil base.il; <mutate command>;
     write_verilog -noattr` (id 0 is the "none" control: no mutate command;
     id "orig" is the unmodified generated core, not round-tripped);
  3. run the stage's cocotb modules one at a time with the snapshot's own
     test/Makefile (icarus, WAVES=none), stopping at the first module that
     reports a failing test (the mutant is killed);
  4. write OUT/<id>.json atomically and delete the temporary directory.

Design variants: when DIR/variant.txt (written by gen_mutants.sh) names a
variant other than base, every make call gets PE_VARIANT=<name> and
PE_CORE=<task dir>/src/protocol_emulator_core.v (the mutant), so the snapshot's
Makefile compiles the mutant and the harness configures the variant's model.

Result status: killed (a test failed), survived (every test passed), timeout
(the stage exceeded its time budget) or error (build/simulator error without a
test verdict). The file is written only when the stage finished, so a
preempted or requeued task simply redoes the unfinished mutant.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

STAGES = {
    # fast kill-oriented set; first 8 random cases of the default seed
    "fast": {"modules": [("test_smoke", {}), ("test_protocols", {}), ("test_flagship", {}),
                         ("test_random", {"PE_RANDOM_ITERS": "8"})],
             "budget": 900},
    # the full 39-test suite with its defaults (64 random cases, 25 legacy replays)
    "full": {"modules": [("test_smoke", {}), ("test_protocols", {}), ("test_flagship", {}),
                         ("test_legacy", {}), ("test_random", {})],
             "budget": 2400},
    # reach/infect/propagate analysis of full-suite survivors: the full suite runs on a
    # wrapper holding the unmutated core (which drives the pins) and the mutant side
    # by side; every register, FIFO word and SRAM pin net is compared each cycle
    "rip": {"modules": [("test_smoke", {}), ("test_protocols", {}), ("test_flagship", {}),
                        ("test_legacy", {}), ("test_random", {})],
            "budget": 5400, "rip": True},
    # directed tests written from the survivor analysis (campaigns/mutation/directed/,
    # copied to DESIGN/directed/ and into test/ of the task tree)
    "directed": {"modules": [("test_mutation_gaps", {})], "budget": 900,
                 "extra_tests": ["directed/test_mutation_gaps.py"]},
    # the RIP wrapper under the directed tests (debugging a directed test that misses)
    "directed-rip": {"modules": [("test_mutation_gaps", {})], "budget": 900, "rip": True,
                     "extra_tests": ["directed/test_mutation_gaps.py"]},
    # extra random stimulus for full-suite survivors: 256 cases from another seed
    "deep": {"modules": [("test_random", {"PE_SEED": "0xD33B2027", "PE_RANDOM_ITERS": "256"})],
             "budget": 3000},
    # the snapshot's whole suite: the default COCOTB_TEST_MODULES of its test/Makefile
    # (66 tests in nine modules at c118027), defaults, stopping at the first failing module
    "suite": {"modules": "makefile", "budget": 3600},
}
MAKEFILE_MODULES = re.compile(r"^COCOTB_TEST_MODULES\s*\?=\s*(\S.*)$", re.M)


def stage_modules(spec: dict, test_dir: Path) -> list[tuple[str, dict]]:
    """The stage's (module, env) list; "makefile" means the snapshot's default module list."""
    if spec["modules"] != "makefile":
        return spec["modules"]
    m = MAKEFILE_MODULES.search((test_dir / "Makefile").read_text())
    if not m:
        raise RuntimeError("no COCOTB_TEST_MODULES default in test/Makefile")
    return [(name.strip(), {}) for name in m.group(1).split(",") if name.strip()]


def design_variant(design: Path) -> str:
    path = design / "variant.txt"
    return path.read_text().strip() if path.exists() else "base"


COMMON_ENV = {"PE_MINIMIZE": "0", "PYTHONDONTWRITEBYTECODE": "1"}
TOOL_PATH = [  # set PE_WORK (cluster work directory) and OSS_CAD_SUITE (tool root)
    p for p in (
        os.path.join(os.environ.get("PE_WORK", ""), "cocotb/bin"),
        os.path.join(os.environ.get("PE_WORK", ""), "cocotb/venv20/bin"),
        os.path.join(os.environ.get("OSS_CAD_SUITE", ""), "bin"),
    ) if os.path.isabs(p)
]


def load_mutants(design: Path) -> dict[str, dict]:
    out = {}
    for line in (design / "mutations.tsv").read_text().splitlines():
        mid, region, cmd = line.split("\t", 2)
        out[mid] = {"id": mid, "region": region, "cmd": cmd}
    out["orig"] = {"id": "orig", "region": "none", "cmd": "(unmodified generated core)"}
    return out


def run(cmd, cwd, env, log: Path, timeout: float) -> tuple[int | None, float]:
    """Run cmd in its own process group; return (exit code or None on timeout, seconds)."""
    t0 = time.time()
    with open(log, "ab") as fh:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            rc = proc.wait(timeout=max(timeout, 1))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            rc = None
    return rc, time.time() - t0


def parse_results(xml: Path) -> dict:
    res = {"tests": 0, "failures": 0, "skipped": 0, "failed": []}
    if not xml.exists():
        return res
    root = ET.parse(xml).getroot()
    for tc in root.iter("testcase"):
        res["tests"] += 1
        bad = tc.find("failure")
        if bad is None:
            bad = tc.find("error")
        if bad is not None:
            res["failures"] += 1
            msg = (bad.get("message") or bad.text or "").strip().replace("\n", " ")
            res["failed"].append({"test": tc.get("name"), "message": msg[:400]})
        elif tc.find("skipped") is not None:
            res["skipped"] += 1
    return res


RIP_PORTS = "input [7:0] uio_in, input ena, input rst_n, input clk, input [7:0] ui_in, " \
            "output [7:0] uo_out, output [7:0] uio_out, output [7:0] uio_oe"
RIP_OUTPUTS = [("read_nibble", "uo_out[3:0]"), ("host_flags", "uo_out[7:4]"),
               ("uio_out", "uio_out"), ("uio_oe", "uio_oe")]


def rip_wrapper(design: Path, mutant_v: str) -> str:
    """Gold + mutant side by side; gold drives the pins; divergences are logged."""
    gold_v = (design / "base_roundtrip.v").read_text().replace(
        "module protocol_emulator_core(", "module pe_rip_gold(", 1)
    mut_v = mutant_v.replace("module protocol_emulator_core(", "module pe_rip_mut(", 1)
    if "module pe_rip_gold(" not in gold_v or "module pe_rip_mut(" not in mut_v:
        raise RuntimeError("rip: module header not found")
    names = []
    for line in (design / "equiv_keep_gold.txt").read_text().split():
        name = line.split("/", 1)[1]
        base = name.split("[")[0]
        if base in ("uio_in", "ena", "rst_n", "clk", "ui_in", "uo_out", "uio_out", "uio_oe"):
            continue
        decl = re.compile(r"\b(reg|wire)\b[^;]*\b" + re.escape(base) + r"\b")
        if decl.search(mut_v) and decl.search(gold_v):
            names.append(name)
    conn = ".uio_in(uio_in), .ena(ena), .rst_n(rst_n), .clk(clk), .ui_in(ui_in)"
    out = [f"module protocol_emulator_core({RIP_PORTS});",
           f"  wire [7:0] m_uo_out, m_uio_out, m_uio_oe;",
           f"  pe_rip_gold g({conn}, .uo_out(uo_out), .uio_out(uio_out), .uio_oe(uio_oe));",
           f"  pe_rip_mut m({conn}, .uo_out(m_uo_out), .uio_out(m_uio_out), .uio_oe(m_uio_oe));",
           f"  integer rip_cycles = 0;",
           f"  integer rip_c [0:{len(names) + len(RIP_OUTPUTS) - 1}];",
           f"  integer rip_i;",
           f"  initial for (rip_i = 0; rip_i < {len(names) + len(RIP_OUTPUTS)}; rip_i = rip_i + 1) rip_c[rip_i] = 0;",
           f"  always @(negedge clk) begin",
           f"    rip_cycles = rip_cycles + 1;"]
    checks = [(n, f"g.{n}", f"m.{n}") for n in names]
    checks += [("out:" + label, expr, "m_" + expr) for label, expr in RIP_OUTPUTS]
    for k, (label, a, b) in enumerate(checks):
        out.append(f'    if ({a} !== {b}) begin if (rip_c[{k}] == 0) '
                   f'$display("PE_RIP first {label} %0t", $time); rip_c[{k}] = rip_c[{k}] + 1; end')
    out.append("  end")
    out.append("  final begin")
    out.append('    $display("PE_RIP cycles %0d", rip_cycles);')
    for k, (label, _, _) in enumerate(checks):
        out.append(f'    if (rip_c[{k}]) $display("PE_RIP count {label} %0d", rip_c[{k}]);')
    out.append("  end")
    out.append("endmodule")
    return "\n".join(out) + "\n" + gold_v + "\n" + mut_v


def rip_collect(logs: list[Path]) -> dict:
    counts, first, cycles = {}, {}, 0
    for log in logs:
        for line in log.read_text(errors="replace").splitlines():
            i = line.find("PE_RIP ")
            if i < 0:
                continue
            parts = line[i:].split()
            if parts[1] == "cycles":
                cycles += int(parts[2])
            elif parts[1] == "count":
                counts[parts[2]] = counts.get(parts[2], 0) + int(parts[3])
            elif parts[1] == "first":
                first.setdefault(parts[2], f"{log.stem[4:]}@{parts[3]}")
    return {"cycles": cycles, "diverged": counts, "first": first}


def tail(path: Path, n: int = 25) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return []


def one(mutant: dict, design: Path, stage: str, out: Path, env0: dict) -> dict:
    spec = STAGES[stage]
    t_start = time.time()
    base = os.environ.get("TMPDIR") or "/tmp"
    work = Path(tempfile.mkdtemp(prefix=f"pe-mut-{stage}-{mutant['id']}-", dir=base))
    result = {"id": mutant["id"], "stage": stage, "region": mutant["region"], "cmd": mutant["cmd"],
              "host": socket.gethostname(), "slurm_job": os.environ.get("SLURM_JOB_ID"),
              "slurm_array": f"{os.environ.get('SLURM_ARRAY_JOB_ID')}_{os.environ.get('SLURM_ARRAY_TASK_ID')}",
              "modules": []}
    variant = design_variant(design)
    if variant != "base":
        result["design_variant"] = variant
    try:
        with tarfile.open(design / "testtree.tgz") as tf:
            tf.extractall(work, filter="data")
        for extra in spec.get("extra_tests", []):
            shutil.copy(design / extra, work / "test" / Path(extra).name)
        core = work / "src" / "protocol_emulator_core.v"
        log = work / "build.log"
        if mutant["id"] == "orig":
            shutil.copy(design / "core_orig.v", core)
        else:
            cmd = mutant["cmd"]
            script = f"read_rtlil {design / 'base.il'}; "
            if "-mode none" not in cmd:
                script += cmd + "; "
            script += f"write_verilog -noattr {core}"
            rc, _ = run(["yosys", "-q", "-p", script], work, env0, log, 300)
            if rc != 0 or not core.exists():
                result.update(status="error", reason="yosys", log_tail=tail(log))
                return result
        result["mutant_sha256"] = hashlib.sha256(core.read_bytes()).hexdigest()
        if spec.get("rip"):
            core.write_text(rip_wrapper(design, core.read_text()))
        budget = spec["budget"]
        status = "survived"
        modules = stage_modules(spec, work / "test")
        for module, extra in modules:
            env = dict(env0, **COMMON_ENV, **extra)
            env["PWD"] = str(work / "test")  # test/Makefile uses $(PWD)
            if variant != "base":
                env["PE_VARIANT"], env["PE_CORE"] = variant, str(core)
            else:  # the design of record, whatever the submitting shell exported
                env.pop("PE_VARIANT", None)
                env.pop("PE_CORE", None)
            xml = work / f"results_{module}.xml"
            mlog = work / f"log_{module}.txt"
            remaining = budget - (time.time() - t_start)
            rc, secs = run(["make", "-C", str(work / "test"), f"SIM_BUILD={work / 'sim_build'}", "WAVES=none",
                            f"COCOTB_TEST_MODULES={module}", f"COCOTB_RESULTS_FILE={xml}"],
                           work / "test", env, mlog, remaining)
            res = parse_results(xml)
            entry = {"module": module, "env": extra, "rc": rc, "seconds": round(secs, 1),
                     "tests": res["tests"], "failures": res["failures"], "skipped": res["skipped"]}
            result["modules"].append(entry)
            if rc is None:
                status = "timeout"
                entry["log_tail"] = tail(mlog)
                break
            if spec.get("rip"):
                entry["failed_tests"] = [f["test"] for f in res["failed"]]
                if rc != 0 and res["tests"] == 0:
                    status = "error"
                    entry["log_tail"] = tail(mlog)
                    break
                continue
            if res["failures"]:
                status = "killed"
                result["killed_by"] = {"module": module, **res["failed"][0]}
                result["failed_tests"] = [f["test"] for f in res["failed"]]
                break
            if res["tests"] == 0 or rc != 0:
                status = "error"
                entry["log_tail"] = tail(mlog)
                break
        if spec.get("rip") and status == "survived":
            status = "ok"
            result["rip"] = rip_collect([work / f"log_{m}.txt" for m, _ in modules])
            result["tests_failed"] = sum(m["failures"] for m in result["modules"])
        result["status"] = status
        return result
    finally:
        result["seconds"] = round(time.time() - t_start, 1)
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--stage", required=True, choices=sorted(STAGES))
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ids-file", type=Path)
    ap.add_argument("--ids")
    ap.add_argument("--index", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", 0)))
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--deadline", type=float, default=0,
                    help="stop starting new mutants after this many seconds (short partitions)")
    args = ap.parse_args()
    args.design = args.design.resolve()

    mutants = load_mutants(args.design)
    if args.ids:
        ids = args.ids.split(",")
    else:
        all_ids = [x.strip() for x in args.ids_file.read_text().split() if x.strip()]
        ids = [x for i, x in enumerate(all_ids) if i % args.count == args.index]
    env0 = dict(os.environ)
    env0["PATH"] = ":".join(TOOL_PATH + [env0.get("PATH", "")])
    args.out.mkdir(parents=True, exist_ok=True)
    done = 0
    t_task = time.time()
    for mid in ids:
        if args.deadline and time.time() - t_task > args.deadline:
            print("deadline reached, stopping", flush=True)
            break
        target = args.out / f"{mid}.json"
        if target.exists():
            continue
        result = one(mutants[mid], args.design, args.stage, args.out, env0)
        tmp = target.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(result, indent=1))
        os.replace(tmp, target)
        done += 1
        print(f"{mid} {result['status']} {result['seconds']}s", flush=True)
    print(f"task {args.index}/{args.count}: {done} mutants run, {len(ids) - done} already done", flush=True)


if __name__ == "__main__":
    main()
