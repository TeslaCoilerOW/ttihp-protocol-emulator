#!/usr/bin/env python3
"""Build the negative-control mutant netlists for formal_depth/.

Each mutant is one or two exact source substitutions in a private copy of
hardcaml/lib (never the repository's hardcaml/). The copy is built with its
own dune build directory and generate_fd --lenient then emits
processor_fd_<mutant>.v, with the FIFO arrays exposed as for the real design.
Every substitution must match exactly once, so a mutant cannot silently
become a no-op when the sources change.

    mutants.py SNAP_DIR WORK_DIR OUT_RTL_DIR [NAME ...]

The harness claim each mutant must break is listed with it (jobs.py runs
each mutant as an 'expect fail' BMC job with -DFD_SPEC_ONLY).
"""
import os
import shutil
import subprocess
import sys

MUTANTS = {
    # host_protocol.sv
    "host_early_commit": ("a word commits on the 7th nibble", [
        ("host.ml", "let write = wr_accept &: (write_index.value ==:. 7) in",
         "let write = wr_accept &: (write_index.value ==:. 6) in")]),
    "host_keep_partial": ("a window change keeps the partial write count", [
        ("host.ml", "if_ changed [write_index <--. 0; write_buffer <--. 0;",
         "if_ changed [write_buffer <--. 0;")]),
    "host_live_read": ("read nibbles come from live data, not the snapshot", [
        ("host.ml", "(List.init 8 (fun n -> select snapshot.value (4*n+3) (4*n)))",
         "(List.init 8 (fun n -> select read_data (4*n+3) (4*n)))")]),
    # program_load.sv
    "load_partial_commit": ("COMMIT accepts an incompletely written image", [
        ("processor.ml", "\n           &: (uresize payload 16 ==: selected_var loaded)", "")]),
    "load_start_uncommitted": ("START accepts an engine without a committed image", [
        ("processor.ml", "|: (committed.(k).value &: (engines.(k).fault ==:. 0))))",
         "|: (engines.(k).fault ==:. 0)))")]),
    # mover.sv
    "mover_wrong_data": ("the mover pushes the word of the cursor's FIFO, not the granted one", [
        ("processor.ml", "let granted_data=mux_arr grant_index.value",
         "let granted_data=mux_arr rr.value")]),
    "mover_quota_on_eligible": ("the quota decrements on eligibility, not on an accepted move", [
        ("processor.ml", "when_ grants.(k) [route_count.(k) <-- route_count.(k).value -:. 1];",
         "when_ eligible.(k) [route_count.(k) <-- route_count.(k).value -:. 1];")]),
    "mover_no_pop": ("a grant pushes the destination without popping the source", [
        ("processor.ml", "rx_pop.(k) <== ((host_rx &: selected_is k) |: grants.(k))",
         "rx_pop.(k) <== (host_rx &: selected_is k)")]),
    # rr_bound.sv
    "rr_no_advance": ("the cursor moves to the granted source, not past it", [
        ("processor.ml", "when_ grant_valid.value [rr <-- grant_index.value +:. 1]",
         "when_ grant_valid.value [rr <-- grant_index.value]")]),
    # fault_release.sv
    "fault_restart": ("START is accepted while the engine is faulted (clearing it)", [
        ("processor.ml", "|: (committed.(k).value &: (engines.(k).fault ==:. 0))))",
         "|: committed.(k).value))")]),
    "fault_keeps_oe": ("a fault neither stops the engine nor gates its output enables", [
        ("engine.ml", "let fail code = [fault <-- code; running <--. 0; enables <--. 0; xremaining <--. 0] in",
         "let fail code = [fault <-- code; xremaining <--. 0] in"),
        ("processor.ml", "e.pin_enables &: owners.(k).value &: electrical_mask\n"
                         "    &: repeat (e.running &: (e.fault ==:. 0) &: ~:clear) 8)) in",
         "e.pin_enables &: owners.(k).value &: electrical_mask\n"
         "    &: repeat (e.running &: ~:clear) 8)) in")]),
    # pin_safety.sv
    "pin_own_overlap": ("OWN no longer rejects overlapping ownership", [
        ("processor.ml", "| 3 -> halted &: (select payload 23 16 ==:. 0) &: ~:overlap",
         "| 3 -> halted &: (select payload 23 16 ==:. 0) &: ~:(overlap &: gnd)")]),
    "pin_od_drive_high": ("open-drain pins are not masked in uio_out", [
        ("processor.ml", "e.pin_values &: owners.(k).value &: ~:(drains.(k).value)",
         "e.pin_values &: owners.(k).value")]),
}


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def main():
    snap, work, out = (os.path.abspath(a) for a in sys.argv[1:4])
    names = sys.argv[4:] or list(MUTANTS)
    here = os.path.dirname(os.path.abspath(__file__))
    config = os.path.join(snap, "configs", "instruction-sram-32.json")
    os.makedirs(out, exist_ok=True)
    for name in names:
        what, subs = MUTANTS[name]
        ws = os.path.join(work, "mut", name)
        shutil.rmtree(ws, ignore_errors=True)
        os.makedirs(os.path.join(ws, "fdgen"))
        for item in ("dune-project", "lib", "bin"):
            src = os.path.join(snap, "hardcaml", item)
            (shutil.copytree if os.path.isdir(src) else shutil.copy)(src, os.path.join(ws, item))
        for f in ("dune", "generate_fd.ml"):
            shutil.copy(os.path.join(here, f), os.path.join(ws, "fdgen", f))
        for fname, old, new in subs:
            path = os.path.join(ws, "lib", fname)
            text = open(path).read()
            if text.count(old) != 1:
                raise SystemExit(f"mutants: {name}: pattern occurs {text.count(old)} times in {fname}")
            open(path, "w").write(text.replace(old, new))
        build = os.path.join(work, "mut", "_build-" + name)
        run(["dune", "build", "--root", ".", "--build-dir", build, "-j", os.environ.get("DUNE_JOBS", "2"),
             "./fdgen/generate_fd.exe"], cwd=ws)
        raw = os.path.join(out, f"processor_fd_{name}.raw.v")
        with open(os.path.join(out, f"processor_fd_{name}.attribution.txt"), "w") as log:
            run([os.path.join(build, "default", "fdgen", "generate_fd.exe"), "--lenient",
                 "--config", config, "--output", raw], stdout=log)
        run([sys.executable, os.path.join(here, "expose_fifo_mem.py"), raw,
             os.path.join(out, f"processor_fd_{name}.v")])
        os.remove(raw)
        shutil.rmtree(ws)
        print(f"mutants: {name}: {what}")


if __name__ == "__main__":
    main()
