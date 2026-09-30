#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Promotion gates of the optimizer (docs/notes/optimization.md, "Promotion").

A promoted configuration goes through four stages: the full run (legal
including LVS 0), then, in parallel, the Tiny Tapeout precheck, the gate-level
cocotb subset and the RTL-vs-netlist equivalence check. promotion_verdict() is
the one place that turns the stage results into the verdict: PASS only if all
four pass. Anything else is FAIL (a stage failed), in progress, or INCOMPLETE
(the gate-level tests could not run for a variant without a reference-model
configuration). In the tracks with the runtime rule (runtime.RULE_KINDS) the
caller passes the full run's projected official job length, and all four gates
passing gives "PASS" only within runtime.BOUND_S; above it the verdict is
"PASS (over CI time bound: ...)", which is not eligible for adoption.

The equivalence stage runs formal_eq/eq_check.py (formal_eq/README.md,
docs/equivalence.md) through eq_job.sh and is fail-closed. Its result passes
only if formal_eq's result.json (schema pe-formal-eq/1) has verdict
"equivalent", exit_code 0 and pass true, the checker process exited 0, and the
sha256s it recorded for the netlist, the core and src/project.v are those of
the files the job gave it (and it used the expected PDK root and variant).
"not equivalent", "undecided", a time limit, an error, a missing or invalid
result.json, a schema mismatch or an input mismatch all fail, with the reason
recorded. parse_eq() maps formal_eq's result.json to the stage's verdict;
`gates.py eq-collect` (called by eq_job.sh) writes the stage's result.json;
eq_should_retry() is the driver's retry policy: only a time limit is retried.

Standard library only.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

sys.dont_write_bytecode = True

SIGNOFF = ("precheck", "gl", "eq")
STAGE_NAMES = {"full": "full run", "precheck": "precheck", "gl": "gate level", "eq": "equivalence"}

# the stage's verdicts (leaderboard wording)
EQUIVALENT = "equivalent"
NOT_EQUIVALENT = "not equivalent"
UNDECIDED = "undecided"
TIMEOUT = "timeout"
ERROR = "error"

FE_SCHEMA = "pe-formal-eq/1"
# formal_eq verdict -> (stage verdict, the exit code formal_eq gives it)
FE_VERDICTS = {"equivalent": (EQUIVALENT, 0), "not_equivalent": (NOT_EQUIVALENT, 1), "error": (ERROR, 2),
               "undecided": (UNDECIDED, 3), "timeout": (TIMEOUT, 3)}
# exit status of `timeout` (eq_job.sh's limit on the whole checker): 124 after SIGTERM,
# 137 after the SIGKILL of --kill-after
OUTER_TIMEOUT = (124, 137)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_result(path):
    """-> (formal_eq result dict or None, problem or None)."""
    try:
        with open(path) as f:
            r = json.load(f)
    except (IOError, OSError):
        return None, "formal_eq wrote no result.json"
    except ValueError as e:
        return None, "formal_eq's result.json is not valid JSON (%s)" % str(e)[:120]
    if not isinstance(r, dict):
        return None, "formal_eq's result.json is not a JSON object"
    return r, None


def _get(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _num(x, nd=None):
    try:
        return round(float(x), nd) if nd else int(round(float(x)))
    except (TypeError, ValueError):
        return None


def parse_eq(fe, outer_exit=0, problem=None):
    """formal_eq's result.json (a dict, or None if missing/invalid) and the exit status of
    the checker process -> {verdict, reason, cex_frame, abc_s, build_s, latches, fe}.

    verdict is one of EQUIVALENT, NOT_EQUIVALENT, UNDECIDED, TIMEOUT, ERROR. EQUIVALENT
    here means formal_eq's own record is consistent and says so; the input sha256s are
    checked separately (check_inputs)."""
    out = {"verdict": ERROR, "reason": None, "cex_frame": None, "abc_s": None, "build_s": None,
           "latches": None, "verdict_line": None}
    if fe is None:
        if outer_exit in OUTER_TIMEOUT:
            out.update(verdict=TIMEOUT, reason="the checker did not finish within the job's limit (exit %s)"
                       % outer_exit)
        else:
            out["reason"] = "%s (checker exit %s)" % (problem or "formal_eq wrote no result.json", outer_exit)
        return out
    case = fe.get("case") if isinstance(fe.get("case"), dict) else {}
    steps = case.get("steps") if isinstance(case.get("steps"), dict) else {}
    out["abc_s"] = _num(_get(steps, "abc", "wall_s"))
    out["latches"] = _get(case, "abc", "aig_first", "latches")
    out["cex_frame"] = _get(case, "abc", "frame")
    out["verdict_line"] = _get(case, "abc", "verdict_line")
    gb = _get(fe, "gold_build", "wall_s")
    if gb is not None or steps:
        out["build_s"] = {"gold": _num(gb, 1), "gate": _num(_get(steps, "gate", "wall_s"), 1),
                          "miter": _num(_get(steps, "miter", "wall_s"), 1)}
    if fe.get("schema") != FE_SCHEMA:
        out["reason"] = "formal_eq result.json schema %r, expected %r" % (fe.get("schema"), FE_SCHEMA)
        return out
    v, code, passed = fe.get("verdict"), fe.get("exit_code"), fe.get("pass")
    if v not in FE_VERDICTS:
        out["reason"] = "formal_eq verdict %r not recognised" % (v,)
        return out
    verdict, want_code = FE_VERDICTS[v]
    if code != want_code or passed is not (code == 0):
        out["reason"] = "inconsistent formal_eq result: verdict %s, exit_code %r, pass %r" % (v, code, passed)
        return out
    if outer_exit != code:
        if outer_exit in OUTER_TIMEOUT:
            out.update(verdict=TIMEOUT, reason="the checker did not finish within the job's limit (exit %s)"
                       % outer_exit)
        else:
            out["reason"] = "checker exit %s differs from its result.json exit_code %s" % (outer_exit, code)
        return out
    why = fe.get("why")
    if verdict == EQUIVALENT:
        if case.get("verdict") != "equivalent":
            out["reason"] = "inconsistent formal_eq result: verdict equivalent, case verdict %r" % case.get("verdict")
            return out
        out["reason"] = "formal_eq: equivalent (ABC %s s)" % out["abc_s"]
    elif verdict == NOT_EQUIVALENT:
        out["reason"] = "formal_eq: not equivalent%s" % (
            " (miter output asserted in frame %s)" % out["cex_frame"] if out["cex_frame"] is not None else "")
    elif verdict == UNDECIDED:
        out["reason"] = "formal_eq: ABC undecided after %s s" % out["abc_s"]
    elif verdict == TIMEOUT:
        out["reason"] = "formal_eq: ABC reached its time limit of %s s" % _get(steps, "abc", "time_limit_s")
    else:
        limited = [n for n, s in (("gold", fe.get("gold_build")), ("gate", steps.get("gate")),
                                  ("miter", steps.get("miter"))) if isinstance(s, dict) and s.get("timed_out")]
        if limited:  # a yosys step reached --build-timeout: a time limit, like ABC's
            out.update(verdict=TIMEOUT, reason="formal_eq: the yosys %s build reached its time limit (%s)"
                       % (limited[0], why))
            return out
        out["reason"] = "formal_eq: error: %s" % (why or "no reason recorded")
    out["verdict"] = verdict
    return out


def check_inputs(fe, expect):
    """expect: {"netlist": sha256, "core": sha256, "project_v": sha256, "pdk_root": path or None,
    "variant": name or None, "job": Slurm job id or None, "checker": sha256 or None}. Returns the
    mismatches between what formal_eq recorded and what the job gave it (each one makes the
    result an error: the checker did not check what was asked)."""
    inp = fe.get("inputs") if isinstance(fe, dict) and isinstance(fe.get("inputs"), dict) else {}
    bad = []
    for role, key, what in (("netlist", ("netlist_source", "sha256"), "netlist"),
                            ("netlist", ("netlist", "sha256"), "netlist copy"),
                            ("core", ("core", "sha256"), "core"),
                            ("project_v", ("top", "sha256"), "src/project.v")):
        want, got = expect.get(role), _get(inp, *key)
        if not want:
            bad.append("no expected sha256 for the %s" % what)
        elif got is None:
            bad.append("formal_eq recorded no sha256 for the %s" % what)
        elif got != want:
            bad.append("formal_eq read the %s with sha256 %s, expected %s" % (what, str(got)[:12], want[:12]))
    root = expect.get("pdk_root")
    if root:
        got = _get(inp, "pdk", "root")
        if got is None or os.path.normpath(got) != os.path.normpath(root):
            bad.append("formal_eq used the PDK root %s, the run used %s" % (got, root))
    var = expect.get("variant")
    if var and inp.get("variant") != var:
        bad.append("formal_eq checked variant %r, expected %r" % (inp.get("variant"), var))
    job = expect.get("job")
    got = fe.get("slurm_job_id") if isinstance(fe, dict) else None
    if job and got and str(got) != str(job):
        bad.append("result.json is from job %s, not %s" % (got, job))
    chk = expect.get("checker")
    got = _get(fe, "tool_files", "formal_eq/eq_check.py")
    if chk and got and got != chk:
        bad.append("formal_eq recorded eq_check.py sha256 %s, the job ran %s" % (got[:12], chk[:12]))
    return bad


def eq_record(parsed, fe, expect, job_id, limit_s, outer_exit, wall_s, checker, abc_version=None, extra=None):
    """The eq stage's result.json: formal_eq's verdict, fail-closed on input mismatches, with
    formal_eq's provenance (tool_files, yosys and ABC versions, PDK, liberty)."""
    verdict, reason = parsed.get("verdict") or ERROR, parsed.get("reason")
    bad = check_inputs(fe, expect) if fe is not None else []
    if bad and verdict != ERROR:
        verdict, reason = ERROR, "; ".join(bad)
    fe = fe if isinstance(fe, dict) else {}
    inp = fe.get("inputs") if isinstance(fe.get("inputs"), dict) else {}
    tools = fe.get("tools") if isinstance(fe.get("tools"), dict) else {}
    rec = {"job_id": str(job_id), "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "verdict": verdict,
           "pass": verdict == EQUIVALENT, "reason": reason, "input_mismatches": bad,
           "cex_frame": parsed.get("cex_frame"), "abc_s": parsed.get("abc_s"), "build_s": parsed.get("build_s"),
           "latches": parsed.get("latches"), "limit_s": int(limit_s), "outer_exit": int(outer_exit),
           "wall_s": int(wall_s), "checker": checker,
           "netlist_sha256": expect.get("netlist"), "core_sha256": expect.get("core"),
           "project_v_sha256": expect.get("project_v"), "variant": expect.get("variant"),
           "pdk_root": expect.get("pdk_root"),
           "formal_eq": {
               "schema": fe.get("schema"), "verdict": fe.get("verdict"), "exit_code": fe.get("exit_code"),
               "pass": fe.get("pass"), "why": fe.get("why"), "verdict_line": parsed.get("verdict_line"),
               "abc_mode": _get(fe, "case", "abc", "mode"), "miter_aig_sha256": _get(fe, "case", "miter_aig_sha256"),
               "argv": fe.get("argv"), "host": fe.get("host"), "cpu_model": fe.get("cpu_model"),
               "slurm_job_id": fe.get("slurm_job_id"), "wall_s": fe.get("wall_s"),
               "peak_child_rss_kb": fe.get("peak_child_rss_kb"), "tool_git": fe.get("tool_git"),
               "tool_files": fe.get("tool_files"),
               "tools": {"yosys": tools.get("yosys"), "yosys_version": tools.get("yosys_version"),
                         "yosys_abc": tools.get("yosys_abc"), "abc_version": abc_version,
                         "oss_cad_suite": tools.get("oss_cad_suite")},
               "inputs": {k: inp.get(k) for k in ("netlist_source", "netlist", "top", "core", "variant", "pdk",
                                                  "liberty")},
               "structure_clock": _get(fe, "structure", "clock")}}
    if extra:
        rec.update(extra)
    return rec


def eq_should_retry(res, attempt, max_attempts):
    """The driver's retry policy for a finished equivalence job: only a time limit is
    retried (the time depends on the node's load), up to max_attempts jobs in all.
    "not equivalent", "undecided" and errors are final at once."""
    return bool(res) and res.get("verdict") == TIMEOUT and int(attempt or 1) < int(max_attempts)


# ---------------------------------------------------------------- verdict
def stage_failure(stage, r):
    """Short reason why a finished sign-off stage failed (None if it passed)."""
    r = r or {}
    if r.get("pass") is True:
        return None
    if r.get("error"):
        return r["error"]
    if stage == "precheck":
        return "%s of %s checks failed (exit %s)" % (r.get("n_fail"), r.get("n_checks"), r.get("exit"))
    if stage == "gl":
        return "%s failed of %s (make exit %s)" % (r.get("failed"), r.get("tests"), r.get("make_exit"))
    if stage == "eq":
        return "%s: %s" % (r.get("verdict") or ERROR, r.get("reason") or "no reason recorded")
    return "failed"


def promotion_verdict(stages, ci=None):
    """-> (text, passed): passed is True only when the full run is legal and the precheck, the
    gate-level tests and the equivalence check all passed; False when any stage failed; None
    while a stage is missing or in progress, or when the gate-level tests could not run.

    ci: for a track with the runtime rule (runtime.RULE_KINDS), {"proj_s": the official job
    length projected from the full run, "bound_s": runtime.BOUND_S}. Then all four gates passing
    gives "PASS" only if the projection is at most the bound; above it the verdict is
    "PASS (over CI time bound: ...)" with passed False (not eligible for adoption), and without a
    projection "PASS (CI time not projected)" with passed None."""
    full = stages.get("full") or {}
    fr = full.get("result") or {}
    if full.get("state") != "done":
        return "full run %s" % (full.get("state") or "not submitted"), None
    if not fr.get("legal"):
        return "FAIL (full run: %s)" % "; ".join((fr.get("blockers") or ["not legal"])[:2]), False
    fails, pending, notrun = [], [], False
    for s in SIGNOFF:
        st = stages.get(s) or {}
        if st.get("state") != "done":
            pending.append(STAGE_NAMES[s])
            continue
        r = st.get("result") or {}
        if s == "gl" and r.get("not_run"):
            notrun = True
            continue
        why = stage_failure(s, r)
        if why:
            fails.append("%s: %s" % (STAGE_NAMES[s], why))
    if fails:
        return "FAIL (%s%s)" % ("; ".join(fails), "; pending: %s" % ", ".join(pending) if pending else ""), False
    if pending:
        return "sign-off in progress (%s)" % ", ".join(pending), None
    if notrun:
        return "INCOMPLETE (gate-level tests not run: no reference-model configuration)", None
    if ci is not None:
        proj = ci.get("proj_s")
        if proj is None:
            return "PASS (CI time not projected: no step times of a complete full run)", None
        if proj > ci["bound_s"]:
            return ("PASS (over CI time bound: projected official job %.2f h > %.1f h)"
                    % (proj / 3600.0, ci["bound_s"] / 3600.0)), False
    return "PASS", True


def eq_text(st):
    """Leaderboard cell of the equivalence stage."""
    st = st or {}
    if st.get("state") != "done":
        return st.get("state") or "-"
    r = st.get("result") or {}
    if r.get("pass") is True:
        return "PASS (equivalent, ABC %s s)" % r.get("abc_s")
    v = r.get("verdict") or ERROR
    if v == NOT_EQUIVALENT:
        return "FAIL (not equivalent%s)" % (", frame %s" % r["cex_frame"] if r.get("cex_frame") is not None else "")
    if v == UNDECIDED:
        return "FAIL (undecided after %s s)" % r.get("abc_s")
    if v == TIMEOUT:
        return "FAIL (timeout, limit %s s)" % r.get("limit_s")
    return "FAIL (%s)" % (r.get("reason") or r.get("error") or "error")


# ---------------------------------------------------------------- eq_job.sh helper
def abc_version(path):
    """First version line of `yosys-abc -c version` (formal_eq records the yosys version and
    the yosys-abc path, not ABC's own version line)."""
    if not path:
        return None
    try:
        r = subprocess.run([path, "-c", "version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return "unavailable: %s" % str(e)[:100]
    lines = [ln.strip() for ln in r.stdout.splitlines() if "ABC" in ln and "command line" not in ln]
    return lines[0] if lines else None


def collect(a):
    fe, problem = load_result(a.fe_result)
    parsed = parse_eq(fe, outer_exit=a.outer_exit, problem=problem)
    expect = {"variant": a.variant, "pdk_root": a.pdk_root or None, "job": a.job if a.job.isdigit() else None}
    errs = []
    for role, path, before in (("netlist", a.netlist, a.netlist_sha), ("core", a.core, None),
                               ("project_v", os.path.join(a.tree, "src", "project.v"), None)):
        try:
            expect[role] = sha256_file(path)
        except (IOError, OSError) as e:
            expect[role] = None
            errs.append("%s %s: %s" % (role, path, e))
            continue
        if before and before != expect[role]:
            errs.append("the %s %s changed during the job (sha256 %s before, %s after)"
                        % (role, path, before[:12], expect[role][:12]))
    if a.core_sha not in ("", "-") and expect.get("core") and expect["core"] != a.core_sha:
        errs.append("core %s has sha256 %s, the track records %s" % (a.core, expect["core"][:12], a.core_sha[:12]))
    try:
        expect["checker"] = sha256_file(a.checker)
    except (IOError, OSError):
        expect["checker"] = None
    checker = {"path": a.checker, "sha256": expect["checker"], "tree": a.checker_tree}
    extra = {"pdk_root_source": a.pdk_source, "netlist": a.netlist, "core": a.core, "tree": a.tree}
    rec = eq_record(parsed, fe, expect, a.job, a.limit, a.outer_exit, a.wall, checker,
                    abc_version=abc_version(_get(fe, "tools", "yosys_abc")), extra=extra)
    if errs:
        rec.update(verdict=ERROR, reason="; ".join(errs), **{"pass": False})
    try:  # the clock-depth warning of the same netlist (informational; promotions made before
        # postprocess.py computed it have it only from here)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import clockdepth
        rec["clock_depth"] = clockdepth.summary(a.netlist)
    except Exception as e:  # noqa: BLE001 (never fails the stage)
        rec["clock_depth"] = {"error": str(e)[:200]}
    tmp = os.path.join(a.out, "result.json.tmp")
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
        f.write("\n")
    os.replace(tmp, os.path.join(a.out, "result.json"))
    print(json.dumps({k: rec.get(k) for k in ("job_id", "verdict", "pass", "reason", "abc_s", "wall_s")}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("eq-collect", help="write OUT/result.json of an equivalence job (eq_job.sh)")
    c.add_argument("--fe-result", required=True, help="formal_eq's result.json")
    c.add_argument("--out", required=True)
    c.add_argument("--netlist", required=True)
    c.add_argument("--netlist-sha", default="", help="sha256 of the netlist before the check")
    c.add_argument("--core", required=True)
    c.add_argument("--core-sha", default="")
    c.add_argument("--tree", required=True, help="the build's tree (its src/project.v is the expected top)")
    c.add_argument("--variant", required=True)
    c.add_argument("--pdk-root", default="")
    c.add_argument("--pdk-source", default="")
    c.add_argument("--checker", required=True)
    c.add_argument("--checker-tree", default="")
    c.add_argument("--job", required=True)
    c.add_argument("--limit", type=int, required=True)
    c.add_argument("--outer-exit", type=int, required=True)
    c.add_argument("--wall", type=int, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "eq-collect":
        return collect(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
