#!/usr/bin/env python3
"""Write formal/isa/isa.sby and print the run.sh job table (docs/isa-spec.md).

usage: gen_sby.py [--jobs | --groups | --timeouts]

The .sby file is committed; rerun this script after changing the check list
below and commit its output. With --jobs it prints the "job:sby task" lines
that formal/run.sh registers, with --groups the CI-sized job groups
("group:job job ..."), with --timeouts the wall-clock limits of the long
bounded jobs ("job:seconds").

Every check CHECK (isa_spec.sv) gives up to three jobs:
  isa_<name>         the check: k-induction of the invariant V and the
                     check's properties from V (sby prove), or BMC (isa_reset,
                     isa_queue_*, isa_xfer_e2e*)
  isa_<name>_cover   non-vacuity witnesses (cover mode, -DISA_COVER)
  isa_<name>_neg     negative control: the check on a mutant (mutate_isa.py)
                     must FAIL on the assertion whose "target of:" comment
                     names the job
"""
import sys

# name, CHECK, mode, depth, mutant (or None), cover depth (or None)
ONE_STEP = 3  # BMC from V: step 0 is any state of V, step 1 its successor; clocked
#              assertions report a sampled step one step later, so depth 3
CHECKS = [
    ("nop", 0, "prove", ONE_STEP, "nop_uncounted", ONE_STEP),
    ("halt", 1, "prove", ONE_STEP, "halt_runs", ONE_STEP),
    ("set", 2, "prove", ONE_STEP, "set_unowned", ONE_STEP),
    ("dir", 3, "prove", ONE_STEP, "dir_unowned", ONE_STEP),
    ("wait", 4, "prove", ONE_STEP, "wait_count", ONE_STEP),
    ("jmp", 5, "prove", ONE_STEP, "jmp_target", ONE_STEP),
    ("pull", 6, "prove", ONE_STEP, "pull_empty", ONE_STEP),
    ("push", 7, "prove", ONE_STEP, "push_full", ONE_STEP),
    ("out", 8, "prove", ONE_STEP, "out_value", ONE_STEP),
    ("in", 9, "prove", ONE_STEP, "in_keep", ONE_STEP),
    ("count", 10, "prove", ONE_STEP, "count_value", ONE_STEP),
    ("loop", 11, "prove", ONE_STEP, "loop_keep", ONE_STEP),
    ("limit", 12, "prove", ONE_STEP, "limit_value", ONE_STEP),
    ("waitpin", 13, "prove", ONE_STEP, "waitpin_uncounted", ONE_STEP),
    ("signal", 14, "prove", ONE_STEP, "signal_self", ONE_STEP),
    ("waitevent", 15, "prove", ONE_STEP, "waitevent_empty", ONE_STEP),
    ("pins", 16, "prove", ONE_STEP, "pins_value", ONE_STEP),
    ("xfer", 17, "prove", ONE_STEP, "xfer_issue", ONE_STEP),
    ("mov", 18, "prove", ONE_STEP, "mov_literal", ONE_STEP),
    ("load", 19, "prove", ONE_STEP, "load_value", ONE_STEP),
    ("add", 20, "prove", ONE_STEP, "add_xor", ONE_STEP),
    ("xor", 21, "prove", ONE_STEP, "xor_or", ONE_STEP),
    ("and", 22, "prove", ONE_STEP, "and_or", ONE_STEP),
    ("or", 23, "prove", ONE_STEP, "or_xor", ONE_STEP),
    ("shl", 24, "prove", ONE_STEP, "shl_right", ONE_STEP),
    ("shr", 25, "prove", ONE_STEP, "shr_left", ONE_STEP),
    ("jz", 26, "prove", ONE_STEP, "jz_never", ONE_STEP),
    ("not", 27, "prove", ONE_STEP, "not_copy", ONE_STEP),
    ("time", 28, "prove", ONE_STEP, "time_value", ONE_STEP),
    ("fault", 29, "prove", ONE_STEP, "fault_code", ONE_STEP),
    ("invalid", 30, "prove", ONE_STEP, "invalid_30", ONE_STEP),
    ("fetch", 40, "prove", ONE_STEP, "fetch_bound", ONE_STEP),
    ("hold", 41, "prove", ONE_STEP, "hold_stuck", ONE_STEP),
    ("idle", 42, "prove", ONE_STEP, "idle_x", ONE_STEP),
    ("pinmap", 43, "prove", ONE_STEP, "pinmap_open_drain", ONE_STEP),
    ("inv", 50, "prove", ONE_STEP, None, None),
    ("reset", 51, "bmc", ONE_STEP, None, None),
    ("cmd_select", 60, "prove", ONE_STEP, "cmd_select", ONE_STEP),
    ("cmd_begin", 61, "prove", ONE_STEP, "cmd_begin", ONE_STEP),
    ("cmd_commit", 62, "prove", ONE_STEP, "cmd_commit", ONE_STEP),
    ("cmd_own", 63, "prove", ONE_STEP, "cmd_own", ONE_STEP),
    ("cmd_start", 64, "prove", ONE_STEP, "cmd_start", ONE_STEP),
    ("cmd_stop", 65, "prove", ONE_STEP, "cmd_stop", ONE_STEP),
    ("cmd_route", 66, "prove", ONE_STEP, "cmd_route", ONE_STEP),
    ("cmd_clear", 67, "prove", ONE_STEP, "cmd_clear", ONE_STEP),
    ("cmd_event", 69, "prove", ONE_STEP, "cmd_event", ONE_STEP),
    ("cmd_flush", 70, "prove", ONE_STEP, "cmd_flush", ONE_STEP),
    ("cmd_trigger", 71, "prove", ONE_STEP, "cmd_trigger", ONE_STEP),
    ("queue_tx", 80, "bmc", 21, "queue_tx_head", 21),
    ("queue_rx", 82, "bmc", 16, "queue_rx_store", 16),
    ("xfer_e2e", 81, "bmc", 12, "xfer_edges", 12),
    ("xfer_e2e_a4", 81, "bmc", 12, None, 12),
    ("xfer_e2e_b5", 81, "bmc", 14, None, 14),
]
# Wall-clock limits (s) that run.sh uses for these jobs when SBY_TIMEOUT is not
# set (its default of 1500 s is too short for the bounded jobs; measured times
# are in docs/isa-spec.md).
TIMEOUTS = {"isa_queue_tx": 5400, "isa_queue_tx_cover": 3600, "isa_queue_tx_neg": 3600,
            "isa_xfer_e2e": 3600, "isa_xfer_e2e_a4": 3600, "isa_xfer_e2e_b5": 3600}
# Extra parameters per check (chparam).
PARAMS = {"xfer_e2e": {"XA": 2, "XB": 2}, "xfer_e2e_a4": {"XA": 4, "XB": 1},
          "xfer_e2e_b5": {"XA": 1, "XB": 5}}
SOURCES = "isa/isa_insn.sv isa/isa_cmd.sv isa/isa_spec.sv"


def jobs():
    out = []
    for name, _, _, _, mutant, cover in CHECKS:
        out.append(f"isa_{name}")
        if cover is not None:
            out.append(f"isa_{name}_cover")
        if mutant is not None:
            out.append(f"isa_{name}_neg")
    return out


def groups():
    """CI-sized groups (run.sh runs a group's jobs in turn): the one-step checks,
    their covers and their negative controls; not the bounded isa_queue_* and
    isa_xfer_e2e* jobs."""
    one_step = [c for c in CHECKS if c[3] == ONE_STEP]
    insn = [c[0] for c in one_step if c[1] <= 30]
    other = [c[0] for c in one_step if c[1] > 30]
    half = (len(insn) + 1) // 2
    out = {
        "isa_ci_insn_a": [f"isa_{n}" for n in insn[:half]],
        "isa_ci_insn_b": [f"isa_{n}" for n in insn[half:]],
        "isa_ci_other": [f"isa_{n}" for n in other],
        "isa_ci_cover": [f"isa_{c[0]}_cover" for c in one_step if c[5] is not None],
        "isa_ci_neg": [f"isa_{c[0]}_neg" for c in one_step if c[4] is not None],
    }
    return out


def main() -> None:
    if sys.argv[1:] == ["--jobs"]:
        for j in jobs():
            print(f"{j}:isa/isa.sby {j}")
        return
    if sys.argv[1:] == ["--groups"]:
        for g, members in groups().items():
            print(f"{g}:{' '.join(members)}")
        return
    if sys.argv[1:] == ["--timeouts"]:
        for j, t in TIMEOUTS.items():
            print(f"{j}:{t}")
        return
    if sys.argv[1:]:
        raise SystemExit(__doc__)
    tasks, options, script_rtl, script_read, script_param, files = [], [], [], [], [], []
    for name, check, mode, depth, mutant, cover in CHECKS:
        tag = f"c_{name}"
        tasks.append(f"isa_{name} {tag} plain")
        options += [f"isa_{name}: mode {mode}", f"isa_{name}: depth {depth}"]
        if cover is not None:
            tasks.append(f"isa_{name}_cover {tag} cover")
            options += [f"isa_{name}_cover: mode cover", f"isa_{name}_cover: depth {cover}"]
        if mutant is not None:
            tasks.append(f"isa_{name}_neg {tag} neg")
            options += [f"isa_{name}_neg: mode bmc",
                        f"isa_{name}_neg: depth {depth if mode == 'bmc' else ONE_STEP}",
                        f"isa_{name}_neg: expect fail"]
            script_rtl.append(f"isa_{name}_neg: read_verilog processor_fv_isa_{mutant}.v")
            files.append(f"isa_{name}_neg: build/rtl/processor_fv_isa_{mutant}.v")
        params = " ".join(f"-set {k} {v}" for k, v in PARAMS.get(name, {}).items())
        script_param.append(f"{tag}: chparam -set CHECK {check} {params}".rstrip() + " isa_spec")
    text = [
        "# Instruction-level specification (docs/isa-spec.md, formal/isa/isa_spec.sv).",
        "# Generated by formal/isa/gen_sby.py; edit the table there.",
        "#   isa_<name>        check: k-induction (prove, depth 3) of the invariant V and",
        "#                     the check's properties from V; BMC for isa_reset (one",
        "#                     reset edge from any state), isa_queue_* and isa_xfer_e2e*",
        "#   isa_<name>_cover  non-vacuity witnesses",
        "#   isa_<name>_neg    NEGATIVE CONTROL on a mutant (mutate_isa.py): must FAIL on",
        "#                     the assertion whose 'target of:' comment names the job",
        "[tasks]", *tasks, "",
        "[options]", *options, "",
        "[engines]", "smtbmc yices", "",
        "[script]",
        "read_verilog -DFUNCTIONAL -DSYNTHESIS RM_IHPSG13_1P_core_behavioral.v RM_IHPSG13_1P_64x16_c2.v",
        "~neg: read_verilog processor_fv.v", *script_rtl,
        f"cover: read -formal -DISA_COVER {' '.join(s.split('/')[1] for s in SOURCES.split())}",
        f"neg: read -formal -DISA_NEG {' '.join(s.split('/')[1] for s in SOURCES.split())}",
        f"plain: read -formal {' '.join(s.split('/')[1] for s in SOURCES.split())}",
        *script_param,
        "prep -top isa_spec", "",
        "[files]",
        "../models/RM_IHPSG13_1P_core_behavioral.v",
        "../models/RM_IHPSG13_1P_64x16_c2.v",
        "~neg: build/rtl/processor_fv.v", *files,
        "isa/isa_insn.vh", *SOURCES.split(), "",
    ]
    with open(__file__.rsplit("/", 1)[0] + "/isa.sby" if "/" in __file__ else "isa.sby", "w") as f:
        f.write("\n".join(text))


if __name__ == "__main__":
    main()
