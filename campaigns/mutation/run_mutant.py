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
     reports a failing test (the mutant is killed); stages marked all_modules
     (`kill`) run every module and list the killing ones in killed_modules;
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
    # by side; every register, FIFO word and SRAM pin net is compared each cycle; the time
    # warp is applied to both cores (RIP_WARP_PATCH)
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
    # the snapshot's whole suite: the default COCOTB_TEST_MODULES that its test/Makefile
    # gives the design under test (66 tests in nine modules at c118027, 102 tests in 20
    # modules from aa07868 on), defaults, stopping at the first failing module. On a
    # c118027 test tree this is the stage `head` of docs/mutation-push.md (same modules,
    # order, stop rule and budget)
    "suite": {"modules": "makefile", "budget": 3600},
    # docs/mutation-push.md: the modules listed in DESIGN/kill_modules.txt (mk_design.sh),
    # in that order; every module runs (no stop at the first kill) so that each kill is
    # attributed to every module that detects it (result field killed_modules)
    "kill": {"modules": "kill_modules", "budget": 2400, "all_modules": True},
    # the same modules on the reach/infect/propagate wrapper of stage rip
    "kill-rip": {"modules": "kill_modules", "budget": 2400, "rip": True},
}
# A target appended to the snapshot's test/Makefile (read from stdin) that prints the
# module list make arrives at. Since 76a81f5 the Makefile has two `COCOTB_TEST_MODULES ?=`
# lines: the first, inside `ifneq ($(filter $(PE_VARIANT),$(LINE_UNIT_VARIANTS)),)`,
# holds the line-unit variants' list, the second every other design's. Until this
# target was used (docs/mutation-push.md section 10) the runner took the first `?=`
# line of the file, so on such a tree the design of record also ran the three
# line-unit modules, whose tests skip there.
PRINT_MODULES = "pe-print-modules:\n\t@echo $(COCOTB_TEST_MODULES)\n"


def makefile_modules(test_dir: Path, env: dict) -> list[str]:
    """The default COCOTB_TEST_MODULES of the snapshot's test/Makefile as make evaluates it
    for the design under test (PE_VARIANT in ``env``; unset for the design of record)."""
    env = {k: v for k, v in env.items() if k != "COCOTB_TEST_MODULES"}
    env["PWD"] = str(test_dir)  # test/Makefile uses $(PWD)
    proc = subprocess.run(["make", "-s", "--no-print-directory", "-f", "Makefile", "-f", "-", "pe-print-modules"],
                          input=PRINT_MODULES, cwd=test_dir, env=env, capture_output=True, text=True, timeout=300)
    names = [name.strip() for name in proc.stdout.strip().split(",") if name.strip()]
    if proc.returncode != 0 or not names:
        raise RuntimeError(f"no COCOTB_TEST_MODULES default in test/Makefile: {proc.stderr.strip()[-400:]}")
    return names


def stage_modules(spec: dict, test_dir: Path, design: Path, env: dict) -> list[tuple[str, dict]]:
    """The stage's (module, env) list; "makefile" means the snapshot's default module list
    for the design under test, "kill_modules" the whitespace-separated list in
    DESIGN/kill_modules.txt."""
    if spec["modules"] == "kill_modules":
        return [(name, {}) for name in (design / "kill_modules.txt").read_text().split()]
    if spec["modules"] != "makefile":
        return spec["modules"]
    return [(name, {}) for name in makefile_modules(test_dir, env)]


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


# The harness's time warp (Harness.warp, warp_routes, warp_completed) deposits into the
# core's registers under dut.user_project.core. On the RIP wrapper that scope holds the
# two cores as instances g (unmutated) and m (mutant), so without this patch a warp
# raises WarpUnsupported: the warping tests fail at their first warp and
# test_kill_route_count_parity skips its warped cases. The patch, appended to the task
# tree's copy of test/harness.py (the repository file is not changed), gives every warp
# deposit to the same-named registers of both instances; each deposit adds a delta to
# the register's current value, so a difference present before the warp is kept.
RIP_WARP_PATCH = '''

# ---- appended by campaigns/mutation/run_mutant.py (RIP stages): warp both cores ----
def _pe_rip_cores(self):
    scope = self.dut
    try:
        for name in CORE_PATH:
            scope = getattr(scope, name)
        return [getattr(scope, "g"), getattr(scope, "m")]
    except AttributeError as missing:
        raise WarpUnsupported("RIP wrapper instances g and m not visible") from missing


def _pe_rip_core_registers(self, family):
    found = []
    for core in _pe_rip_cores(self):
        try:
            getattr(core, WARP_FAMILIES["timestamp"])
        except AttributeError as missing:
            raise WarpUnsupported("no core registers visible in the RIP wrapper") from missing
        base = WARP_FAMILIES[family]
        mine = []
        for name in (base, *(f"{base}_{k}" for k in range(8))):
            try:
                mine.append(getattr(core, name))
            except AttributeError:
                continue
        expected = 1 if family == "timestamp" else self.model.config.engines
        if mine and len(mine) != expected:
            raise WarpUnsupported(f"core has {len(mine)} {base} registers, expected {expected}")
        found += mine
    return found


def _pe_rip_deposit_routes(self, sources, words):
    for core in _pe_rip_cores(self):
        for source in sources:
            self._deposits.append((getattr(core, f"route_remaining_{source}"), -words))


CocotbHarness.core_registers = _pe_rip_core_registers
CocotbHarness._deposit_routes = _pe_rip_deposit_routes
'''
RIP_WARP_NEEDS = ("CORE_PATH = ", "WARP_FAMILIES = ", "class WarpUnsupported", "class CocotbHarness",
                  "def core_registers(self, family", "def _deposit_routes(self, sources",
                  "self._deposits")


def rip_warp_patch(test_dir: Path) -> str:
    """Append RIP_WARP_PATCH to the task tree's test/harness.py; return the patched file's sha256."""
    harness = test_dir / "harness.py"
    text = harness.read_text()
    missing = [needle for needle in RIP_WARP_NEEDS if needle not in text]
    if missing:
        raise RuntimeError(f"rip: harness.py lacks {missing}; the warp patch does not apply")
    harness.write_text(text + RIP_WARP_PATCH)
    return hashlib.sha256(harness.read_bytes()).hexdigest()


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
            result["rip_harness_sha256"] = rip_warp_patch(work / "test")
        budget = spec["budget"]
        status = "survived"
        design_env = dict(env0, **COMMON_ENV)
        if variant != "base":
            design_env["PE_VARIANT"], design_env["PE_CORE"] = variant, str(core)
        else:  # the design of record, whatever the submitting shell exported
            design_env.pop("PE_VARIANT", None)
            design_env.pop("PE_CORE", None)
        modules = stage_modules(spec, work / "test", design, design_env)
        result["module_list"] = [m for m, _ in modules]
        every = bool(spec.get("all_modules"))
        for module, extra in modules:
            env = dict(design_env, **extra)
            env["PWD"] = str(work / "test")  # test/Makefile uses $(PWD)
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
                entry["log_tail"] = tail(mlog)
                if status != "killed":  # all_modules: a kill by an earlier module stands
                    status = "timeout"
                break
            if spec.get("rip"):
                entry["failed_tests"] = [f["test"] for f in res["failed"]]
                if rc != 0 and res["tests"] == 0:
                    status = "error"
                    entry["log_tail"] = tail(mlog)
                    break
                continue
            if res["failures"]:
                if every:
                    entry["failed_tests"] = [f["test"] for f in res["failed"]]
                    result.setdefault("killed_modules", []).append(module)
                if status != "killed":
                    result["killed_by"] = {"module": module, **res["failed"][0]}
                    result["failed_tests"] = [f["test"] for f in res["failed"]]
                status = "killed"
                if every:
                    continue
                break
            if res["tests"] == 0 or rc != 0:
                entry["log_tail"] = tail(mlog)
                if status == "killed":  # all_modules: a kill by an earlier module stands
                    entry["error"] = True
                    continue
                status = "error"
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
