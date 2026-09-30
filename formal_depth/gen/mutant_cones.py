#!/usr/bin/env python3
"""List the logic each negative-control mutant changes (formal_depth/).

    mutant_cones.py MODELS_DIR RTL_DIR [NAME ...]

compares RTL_DIR/processor_fd.v with each RTL_DIR/processor_fd_<NAME>.v (by
default every mutant there). Yosys reads each netlist (the SRAM macro models
as black boxes, `proc; memory_collect`) and writes it as JSON. Every state
element (flip-flop, FIFO memory, SRAM macro) is a leaf, and each *sink* (an
input of a state element, or a module output) gets a structural hash of its
combinational cone: cell types, parameters and, recursively, the hashes of
the cells' inputs, down to leaves, which are hashed by label only. A sink's
hash therefore changes only when the logic that drives it changes, not when a
register further upstream changes, and the sinks that differ are the
next-state functions and outputs that the mutation reaches directly.

Leaves are labelled by the register's Hardcaml name, else by the
observation port that carries its bits when both netlists have that port
(generate_fd --lenient ties the fd_host_* ports of some mutants to 0), else
they are paired between the two netlists by the hash of their own input
cones. An unnamed register whose cone changed is paired with the one leftover
register of its kind on the other side ("paired"); leftovers that cannot be
paired one to one are reported as "unmatched".

The report is for reading, not a pass/fail check; the only error is a
mutant that changes no sink. MUTANT_JOBS (default 4) yosys runs at a time.
"""
import collections
import concurrent.futures
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

YOSYS = os.environ.get("YOSYS", "yosys")
SEQ = {"$dff", "$dffe", "$sdff", "$sdffe", "$sdffce", "$adff", "$adffe", "$aldff", "$aldffe",
       "$dffsr", "$dffsre", "$ff", "$mem", "$mem_v2"}
NUMBERED = re.compile(r"^_\d+$")


def to_json(models, netlist, out):
    lib = " ".join(os.path.join(models, m) for m in
                   ("RM_IHPSG13_1P_core_behavioral.v", "RM_IHPSG13_1P_64x16_c2.v"))
    script = (f"read_verilog -lib -DFUNCTIONAL -DSYNTHESIS {lib}; read_verilog {netlist}; "
              "hierarchy -top protocol_processor_fd; proc; opt_clean; memory_collect; opt_clean; "
              f"write_json {out}")
    subprocess.run([YOSYS, "-q", "-p", script], check=True)


def h(*parts):
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:24]


class Netlist:
    """One elaborated netlist: drivers, state elements and their leaf labels."""

    def __init__(self, path):
        mod = json.load(open(path))["modules"]["protocol_processor_fd"]
        self.cells, self.ports = mod["cells"], mod["ports"]
        # A register's own name: not a module port, not a numbered _NNN name.
        names = {}
        for name, net in sorted(mod["netnames"].items()):
            if NUMBERED.match(name) or name.startswith("$") or net.get("hide_name") or name in self.ports:
                continue
            for i, b in enumerate(net["bits"]):
                if isinstance(b, int):
                    names.setdefault(b, (name, i))
        ports = {}
        for name, net in sorted(self.ports.items()):
            if net["direction"] == "output":
                for i, b in enumerate(net["bits"]):
                    if isinstance(b, int):
                        ports.setdefault(b, (name, i))
        self.driver = {}
        for cname, c in self.cells.items():
            for p, d in c["port_directions"].items():
                if d == "output":
                    for i, b in enumerate(c["connections"][p]):
                        if isinstance(b, int):
                            self.driver[b] = (cname, p, i)
        for pname, p in self.ports.items():
            if p["direction"] == "input":
                for i, b in enumerate(p["bits"]):
                    self.driver[b] = ("<input>", pname, i)
        self.seq = [cn for cn, c in self.cells.items() if self.is_seq(cn)]
        self.labels = {cn: self.own_label(cn, names, ports) for cn in self.seq}

    def is_seq(self, cname):
        t = self.cells[cname]["type"]
        return t in SEQ or not t.startswith("$")

    def kind(self, cname):
        c = self.cells[cname]
        return f"{c['type']}/{len(c['connections'].get('Q', []))}"

    def inputs(self, cname):
        c = self.cells[cname]
        return [p for p in sorted(c["port_directions"]) if c["port_directions"][p] != "output"]

    def own_label(self, cname, names, ports):
        c = self.cells[cname]
        if c["type"] in ("$mem", "$mem_v2"):
            return "mem:" + c["parameters"].get("MEMID", cname).lstrip("\\")
        if not c["type"].startswith("$"):
            return "sram:" + cname.lstrip("\\")
        q = c["connections"].get("Q", [])
        if not q or not isinstance(q[0], int):
            return None
        for table, tag in ((names, "reg"), (ports, "port")):
            first = table.get(q[0])
            if first and all(table.get(b) == (first[0], first[1] + i) for i, b in enumerate(q)):
                return f"{tag}:{first[0]}[{first[1]}+:{len(q)}]"
        return None

    def hashes(self):
        """Hash of every sink under the current leaf labels."""
        memo, cells, driver = {}, self.cells, self.driver

        def bit(b):
            if not isinstance(b, int):
                return "const:" + b
            if b not in driver:
                return "undriven"
            cn, p, i = driver[b]
            if cn == "<input>":
                return f"in:{p}[{i}]"
            if self.is_seq(cn):
                return f"{self.labels[cn] or 'anon/' + self.kind(cn)}.{p}[{i}]"
            return h(cell(cn), p, i)

        def comb_deps(cn):
            return [driver[b][0] for p in self.inputs(cn) for b in cells[cn]["connections"][p]
                    if isinstance(b, int) and b in driver and driver[b][0] != "<input>"
                    and not self.is_seq(driver[b][0])]

        def cell(cname):
            stack = [(cname, False)]
            while stack:  # iterative post-order: the cones are deep
                cn, ready = stack.pop()
                if cn in memo:
                    continue
                pending = [d for d in comb_deps(cn) if d not in memo]
                if pending and not ready:
                    stack.append((cn, True))
                    stack.extend((d, False) for d in pending)
                    continue
                c = cells[cn]
                memo[cn] = h(c["type"], sorted(c["parameters"].items()),
                             [(p, [bit(b) for b in c["connections"][p]]) for p in self.inputs(cn)])
            return memo[cname]

        sinks = {}
        for cn in self.seq:
            params = sorted(cells[cn]["parameters"].items())
            for p in self.inputs(cn):
                sinks[(cn, p)] = h(params, *[bit(b) for b in cells[cn]["connections"][p]])
        for pname, pdef in self.ports.items():
            if pdef["direction"] == "output":
                sinks[("<output>", pname)] = h(*[bit(b) for b in pdef["bits"]])
        return sinks


def pair_labels(base, mut):
    """Make the two netlists' leaf labels comparable; return (paired, unmatched)."""
    # A port label counts only if the other netlist has the same one.
    have = [{v for v in n.labels.values() if v and v.startswith("port:")} for n in (base, mut)]
    for net, other in ((base, have[1]), (mut, have[0])):
        for cn, v in net.labels.items():
            if v and v.startswith("port:") and v not in other:
                net.labels[cn] = None
    # Unnamed registers: equal input cones (all unnamed leaves alike) pair up.
    groups = collections.defaultdict(lambda: ([], []))
    for side, net in enumerate((base, mut)):
        sinks = net.hashes()
        for cn in net.seq:
            if net.labels[cn] is None:
                sig = h(net.kind(cn), *[sinks[(cn, p)] for p in net.inputs(cn)])
                groups[sig][side].append(cn)
    left = collections.defaultdict(lambda: ([], []))
    for sig, (b, m) in groups.items():
        if len(b) == len(m):
            for net, cns in ((base, b), (mut, m)):
                for cn in cns:
                    net.labels[cn] = "unnamed:" + sig
        else:
            for net, cns, side in ((base, b, 0), (mut, m, 1)):
                for cn in cns:
                    left[net.kind(cn)][side].append(cn)
    paired, unmatched = [], []
    for kind, (b, m) in sorted(left.items()):
        if len(b) == 1 and len(m) == 1:
            base.labels[b[0]] = mut.labels[m[0]] = "paired:" + kind
            paired.append(kind)
        else:
            for net, cns, side in ((base, b, "base"), (mut, m, "mutant")):
                for cn in cns:
                    net.labels[cn] = f"unmatched-{side}:{cn}"
            unmatched.append(f"{kind} (base {len(b)}, mutant {len(m)})")
    return paired, unmatched


def compare(base, mut):
    paired, unmatched = pair_labels(base, mut)
    counts = []
    for net in (base, mut):
        c = collections.Counter()
        for (cn, p), v in net.hashes().items():
            c[("out:" + p if cn == "<output>" else f"{net.labels[cn]}.{p}", v)] += 1
        counts.append(c)
    diff = sorted({k for k, _ in (counts[0] - counts[1]) + (counts[1] - counts[0])})
    return diff, paired, unmatched


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    models, rtl = sys.argv[1:3]
    base = os.path.join(rtl, "processor_fd.v")
    names = sys.argv[3:] or sorted(os.path.basename(p)[len("processor_fd_"):-2]
                                   for p in glob.glob(os.path.join(rtl, "processor_fd_*.v"))
                                   if not p.endswith(".raw.v"))
    paths = [base] + [os.path.join(rtl, f"processor_fd_{n}.v") for n in names]
    with tempfile.TemporaryDirectory() as tmp:
        jsons = [os.path.join(tmp, f"{k}.json") for k in range(len(paths))]
        with concurrent.futures.ThreadPoolExecutor(int(os.environ.get("MUTANT_JOBS", "4"))) as pool:
            list(pool.map(lambda a: to_json(models, *a), zip(paths, jsons)))
        print("# formal_depth/gen/mutant_cones.py: for each mutant, the sinks whose combinational")
        print("# cone differs from processor_fd.v. A sink is an input of a state element")
        print("# (reg:NAME[LSB+:WIDTH].PORT, or port:... when only an observation port names it;")
        print("# mem:MEMORY.PORT; sram:INSTANCE.PORT) or a module output (out:PORT).")
        noop = []
        for name, j in zip(names, jsons[1:]):
            diff, paired, unmatched = compare(Netlist(jsons[0]), Netlist(j))
            print(f"{name}: {len(diff)} sinks differ"
                  + (f"; unnamed registers paired across the change: {', '.join(paired)}" if paired else "")
                  + (f"; unmatched: {', '.join(unmatched)}" if unmatched else ""))
            for k in diff:
                print(f"  {k}")
            if not diff:
                noop.append(name)
    if noop:
        sys.exit(f"mutant_cones: no sink differs for {', '.join(noop)}")


if __name__ == "__main__":
    main()
