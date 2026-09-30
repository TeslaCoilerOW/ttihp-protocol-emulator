#!/usr/bin/env python3
"""Build the negative-control mutant netlists for formal_depth/.

Each mutant is one or two exact source substitutions in a private copy of
hardcaml/lib (never the repository's hardcaml/). The copy is built with its
own dune build directory and generate_fd --lenient then emits
processor_fd_<mutant>.v, with the FIFO arrays exposed as for the real design.
Every substitution must match exactly once, so a mutant cannot silently
become a no-op when the sources change. A substitution may list alternatives
for older snapshots (OLD and NEW are then tuples, newest first, and FILE may
be a tuple too when the alternatives are in different files): the first
alternative whose OLD occurs exactly once in its file is used.

Where a source line has a second copy in code the design of record does not
elaborate (the host_nibble_slots and split_command_decode timing knobs, off in
configs/instruction-sram-32.json), the pattern includes enough context to
select the copy that is elaborated. When OUT_RTL_DIR already holds the
unmutated processor_fd.v, a mutant netlist equal to it is an error.

    mutants.py SNAP_DIR WORK_DIR OUT_RTL_DIR [NAME ...]

MUTANT_JOBS (default 1) builds that many mutants at once; DUNE_JOBS is the
dune parallelism of each build. The harness claim each mutant must break is
listed with it (jobs.py runs each mutant as an 'expect fail' BMC job with
-DFD_SPEC_ONLY).
"""
import concurrent.futures
import filecmp
import os
import shutil
import subprocess
import sys

MUTANTS = {
    # host_protocol.sv
    # From 131e793 host.ml also holds the host_nibble_slots implementation,
    # with its own copy of this line; the word assembly of the shifting write
    # buffer (the implementation the design of record uses) selects the copy.
    "host_early_commit": ("a word commits on the 7th nibble", [
        ("host.ml", "select write_buffer.value 31 4] in\n"
                    "  let write = wr_accept &: (write_index.value ==:. 7) in",
         "select write_buffer.value 31 4] in\n"
         "  let write = wr_accept &: (write_index.value ==:. 6) in")]),
    "host_keep_partial": ("a window change keeps the partial write count", [
        ("host.ml", "if_ changed [write_index <--. 0; write_buffer <--. 0;",
         "if_ changed [write_buffer <--. 0;")]),
    # As above: the "]]]];" that closes the shifting-buffer implementation's
    # register updates selects its read multiplexer.
    "host_live_read": ("read nibbles come from live data, not the snapshot", [
        ("host.ml", "[presenting <--. 0]]]];\n  let nibble = mux read_index.value\n"
                    "      (List.init 8 (fun n -> select snapshot.value (4*n+3) (4*n)))",
         "[presenting <--. 0]]]];\n  let nibble = mux read_index.value\n"
         "      (List.init 8 (fun n -> select read_data (4*n+3) (4*n)))")]),
    # program_load.sv
    # The COMMIT rule's "payload = words loaded" conjunct: with the
    # narrow_image_regs variant option after c377d97 (both branches go), the
    # plain comparison before.
    "load_partial_commit": ("COMMIT accepts an incompletely written image", [
        ("processor.ml",
         ("\n           &: (if options.narrow_image_regs\n"
          "               then uresize payload 16 ==: uresize (selected_var loaded) 16\n"
          "               else uresize payload 16 ==: selected_var loaded)",
          "\n           &: (uresize payload 16 ==: selected_var loaded)"),
         ("", ""))]),
    "load_start_uncommitted": ("START accepts an engine without a committed image", [
        ("processor.ml", "|: (committed.(k).value &: (engines.(k).fault ==:. 0))))",
         "|: (engines.(k).fault ==:. 0)))")]),
    # mover.sv
    # grant_index is a signal after 131e793 (the round-robin grant has a
    # split_command_decode form too), the wire variable's .value before.
    "mover_wrong_data": ("the mover pushes the word of the cursor's FIFO, not the granted one", [
        ("processor.ml",
         ("let granted_data=mux_arr grant_index (fun",
          "let granted_data=mux_arr grant_index.value"),
         ("let granted_data=mux_arr rr.value (fun",
          "let granted_data=mux_arr rr.value"))]),
    # The decrement is wrapped in the keep_counter_increments helper (step)
    # after 131e793; the guard is the same.
    "mover_quota_on_eligible": ("the quota decrements on eligibility, not on an accepted move", [
        ("processor.ml", "when_ grants.(k) [route_count.(k) <--",
         "when_ eligible.(k) [route_count.(k) <--")]),
    "mover_no_pop": ("a grant pushes the destination without popping the source", [
        ("processor.ml", "rx_pop.(k) <== ((host_rx &: selected_is k) |: grants.(k))",
         "rx_pop.(k) <== (host_rx &: selected_is k)")]),
    # rr_bound.sv
    # As for mover_wrong_data: signals after 131e793, variables before.
    "rr_no_advance": ("the cursor moves to the granted source, not past it", [
        ("processor.ml",
         ("when_ grant_valid [rr <-- grant_index +:. 1]",
          "when_ grant_valid.value [rr <-- grant_index.value +:. 1]"),
         ("when_ grant_valid [rr <-- grant_index]",
          "when_ grant_valid.value [rr <-- grant_index.value]"))]),
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
    # The OWN case of the command acceptance rule: a Host_command.t
    # constructor after 16cfc20, the command code 3 up to 16cfc20.
    "pin_own_overlap": ("OWN no longer rejects overlapping ownership", [
        ("processor.ml",
         ("| Own -> halted &: (select payload 23 16 ==:. 0) &: ~:overlap",
          "| 3 -> halted &: (select payload 23 16 ==:. 0) &: ~:overlap"),
         ("| Own -> halted &: (select payload 23 16 ==:. 0) &: ~:(overlap &: gnd)",
          "| 3 -> halted &: (select payload 23 16 ==:. 0) &: ~:(overlap &: gnd)"))]),
    "pin_od_drive_high": ("open-drain pins are not masked in uio_out", [
        ("processor.ml", "e.pin_values &: owners.(k).value &: ~:(drains.(k).value)",
         "e.pin_values &: owners.(k).value")]),
}


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def substitute(name, lib, fname, old, new):
    """Apply the first alternative whose OLD occurs exactly once in its file;
    return (file, alternative index)."""
    olds, news = (old, new) if isinstance(old, tuple) else ((old,), (new,))
    fnames = fname if isinstance(fname, tuple) else (fname,) * len(olds)
    if not len(fnames) == len(olds) == len(news):
        raise SystemExit(f"mutants: {name}: FILE, OLD and NEW have different lengths")
    counts = []
    for k, (f, o, n) in enumerate(zip(fnames, olds, news)):
        path = os.path.join(lib, f)
        text = open(path).read() if os.path.exists(path) else ""
        counts.append(text.count(o))
        if counts[-1] == 1:
            open(path, "w").write(text.replace(o, n))
            return f, k
    raise SystemExit(f"mutants: {name}: substitution site found {counts} times in {list(fnames)}")


def build(name, snap, work, out, here, config):
    what, subs = MUTANTS[name]
    ws = os.path.join(work, "mut", name)
    shutil.rmtree(ws, ignore_errors=True)
    os.makedirs(os.path.join(ws, "fdgen"))
    for item in ("dune-project", "lib", "bin"):
        src = os.path.join(snap, "hardcaml", item)
        (shutil.copytree if os.path.isdir(src) else shutil.copy)(src, os.path.join(ws, item))
    for f in ("dune", "generate_fd.ml"):
        shutil.copy(os.path.join(here, f), os.path.join(ws, "fdgen", f))
    sites = [substitute(name, os.path.join(ws, "lib"), fname, old, new) for fname, old, new in subs]
    build_dir = os.path.join(work, "mut", "_build-" + name)
    run(["dune", "build", "--root", ".", "--build-dir", build_dir, "-j", os.environ.get("DUNE_JOBS", "2"),
         "./fdgen/generate_fd.exe"], cwd=ws)
    raw = os.path.join(out, f"processor_fd_{name}.raw.v")
    netlist = os.path.join(out, f"processor_fd_{name}.v")
    with open(os.path.join(out, f"processor_fd_{name}.attribution.txt"), "w") as log:
        run([os.path.join(build_dir, "default", "fdgen", "generate_fd.exe"), "--lenient",
             "--config", config, "--output", raw], stdout=log)
    run([sys.executable, os.path.join(here, "expose_fifo_mem.py"), raw, netlist])
    os.remove(raw)
    shutil.rmtree(ws)
    shutil.rmtree(build_dir, ignore_errors=True)
    unmutated = os.path.join(out, "processor_fd.v")
    if os.path.exists(unmutated) and filecmp.cmp(unmutated, netlist, shallow=False):
        raise SystemExit(f"mutants: {name}: the netlist equals the unmutated processor_fd.v")
    where = ", ".join(f"{f} alternative {k}" for f, k in sites)
    return f"mutants: {name}: {what} ({where})"


def main():
    snap, work, out = (os.path.abspath(a) for a in sys.argv[1:4])
    names = sys.argv[4:] or list(MUTANTS)
    here = os.path.dirname(os.path.abspath(__file__))
    config = os.path.join(snap, "configs", "instruction-sram-32.json")
    os.makedirs(out, exist_ok=True)
    jobs = max(1, int(os.environ.get("MUTANT_JOBS", "1")))
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for line in pool.map(lambda n: build(n, snap, work, out, here, config), names):
            print(line, flush=True)


if __name__ == "__main__":
    main()
