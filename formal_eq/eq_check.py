#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Sequential equivalence check of the RTL against a gate-level netlist
(formal_eq/README.md).

  eq_check.py check     (--netlist NL | --run-dir RUN) [--variant NAME] [--selftest] [options]
  eq_check.py controls  [--out DIR]
  eq_check.py mutate    --netlist NL --kind near-output-nand|sram-din-swap --out FILE
  eq_check.py structure (--netlist NL | --run-dir RUN) [--liberty LIB | --pdk-root DIR]

gold = src/project.v + the core of the chosen variant (RTL); gate = the netlist
with the cell functions of the PDK's typical standard-cell liberty. Both are
wrapped in wrap.v (rst_n held low in the first cycle) and flattened; the SRAM
macros are cut points (their inputs become compared outputs, their read data a
free input shared by both sides). Yosys builds a miter whose single output is 1
when any compared bit differs, and ABC `dprove` proves that output 0 in every
reachable state, or finds a counterexample.

Exit status of `check` (fail closed: only 0 is a pass):
  0  equivalent (and, with --selftest, every control met its expectation)
  1  not equivalent (or, with --selftest, a control missed its expectation)
  2  error: bad input, a failed precondition or a tool failure
  3  undecided: ABC gave up, or the time limit was reached

Every run writes OUT/result.json. Standard library only; needs yosys and
yosys-abc (the OSS CAD Suite: $OSS_CAD_SUITE/bin, else PATH).

Environment:
  PE_VARIANT      default for --variant (default base)
  PE_CORE         default for --core
  OSS_CAD_SUITE   tool root; yosys and yosys-abc are taken from its bin/
  PE_PDK_ROOT or PDK_ROOT   PDK root holding ihp-sg13cmos5l/ (--pdk-root)
  PE_FLOW_ROOT    default $PE_WORK/sram-flow; the PDK default is $PE_FLOW_ROOT/pdk
  PE_WORK         default <repo>/build/tt-work
"""
import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DESIGN = "tt_um_teslacoilerow_protocol_emulator"
SRAM_CELL = "RM_IHPSG13_1P_64x16_c2"
SRAM_BLACKBOX = os.path.join(REPO, "models", "blackbox", SRAM_CELL + ".v")
WRAPPER = os.path.join(HERE, "wrap.v")
CONTROLS_DIR = os.path.join(HERE, "controls")
DEFAULT_PDK = "ihp-sg13cmos5l"
LIBERTY_REL = "libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib"
# variant -> (core file relative to the repository, config named in the core's header line)
VARIANTS = {
    "base": ("src/protocol_emulator_core.v", "configs/instruction-sram-32.json"),
    "diet4": ("variants6x4/protocol_emulator_core.v", "configs/variants/diet4.json"),
}
TT_INPUTS = {"ui_in": 8, "uio_in": 8, "ena": 1, "clk": 1, "rst_n": 1}
TT_OUTPUTS = {"uo_out": 8, "uio_out": 8, "uio_oe": 8}
CLOCK_PORT = "clk"
ABC_SCRIPT = "read_aiger miter.aig; print_stats; strash; print_stats; dprove -v; print_stats"

EXIT = {"equivalent": 0, "not_equivalent": 1, "error": 2, "undecided": 3, "timeout": 3}
SCHEMA = "pe-formal-eq/1"


class EqError(Exception):
    """An input or precondition problem: the run ends with verdict 'error'."""


# --------------------------------------------------------------------------- utilities

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def peak_child_rss_kb():
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss


def no_space(path, what):
    if re.search(r"\s", path):
        raise EqError("%s path contains white space (not supported in yosys scripts): %r" % (what, path))
    return path


def run_logged(cmd, log_path, timeout, cwd):
    """Run cmd (list) with stdout+stderr to log_path. Returns (rc, wall_s, timed_out)."""
    t0 = time.time()
    with open(log_path, "w") as log:
        p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=cwd,
                             start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            rc, timed_out = None, True
    return rc, round(time.time() - t0, 2), timed_out


def git_state():
    try:
        head = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, universal_newlines=True, timeout=30).stdout.strip()
        st = subprocess.run(["git", "-C", REPO, "status", "--porcelain", "--", "formal_eq"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            universal_newlines=True, timeout=30).stdout
        return {"commit": head or None, "formal_eq_modified": bool(st.strip())}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "formal_eq_modified": None}


def find_tools():
    oss = os.environ.get("OSS_CAD_SUITE", "")
    tools = {}
    for name in ("yosys", "yosys-abc"):
        path = os.path.join(oss, "bin", name) if oss else shutil.which(name)
        if not path or not os.access(path, os.X_OK):
            raise EqError("%s not found (set OSS_CAD_SUITE or put it on PATH)" % name)
        tools[name] = path
    try:
        ver = subprocess.run([tools["yosys"], "-V"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        raise EqError("yosys -V failed: %s" % e)
    return {"yosys": tools["yosys"], "yosys_abc": tools["yosys-abc"], "yosys_version": ver,
            "oss_cad_suite": oss or None}


# --------------------------------------------------------------------------- liberty

_LIB_TOKEN = re.compile(r'\s+|/\*.*?\*/|//[^\n]*|\\\n|("(?:[^"\\]|\\.)*")|([{}();:,])|([^\s{}();:,"]+)', re.S)


def _lib_tokens(text):
    for m in _LIB_TOKEN.finditer(text):
        if m.group(1) is not None:
            yield m.group(1)[1:-1]
        elif m.group(2) is not None:
            yield m.group(2)
        elif m.group(3) is not None:
            yield m.group(3)


def parse_liberty(path, wanted=None):
    """Return {cell: {"pins": {pin: {"direction", "function", "three_state"}},
    "ff": [{"clocked_on", "next_state", "clear", "preset"}], "latch": bool, "statetable": bool}}.
    Only a minimal Liberty subset is read: groups, simple and complex attributes."""
    with open(path) as f:
        toks = list(_lib_tokens(f.read()))
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        t = toks[pos[0]]
        pos[0] += 1
        return t

    def parse_group_body():
        """After '{': returns list of (name, args, value, children)."""
        items = []
        while True:
            t = take()
            if t == "}":
                return items
            name = t
            nxt = take()
            if nxt == ":":
                val = []
                while peek() not in (";", "}", None):
                    val.append(take())
                    # a simple attribute may end at a newline without ';' (rare); tolerate
                if peek() == ";":
                    take()
                items.append((name, None, " ".join(val), None))
            elif nxt == "(":
                args = []
                depth = 1
                while depth:
                    a = take()
                    if a == "(":
                        depth += 1
                    elif a == ")":
                        depth -= 1
                        if not depth:
                            break
                    if a != ",":
                        args.append(a)
                if peek() == "{":
                    take()
                    items.append((name, args, None, parse_group_body()))
                else:
                    if peek() == ";":
                        take()
                    items.append((name, args, None, None))
            else:
                raise EqError("liberty parse error near %r %r in %s" % (name, nxt, path))

    # library ( name ) { ... }
    while peek() is not None and peek() != "library":
        take()
    if peek() is None:
        raise EqError("no library group in %s" % path)
    take()
    if take() != "(":
        raise EqError("liberty parse error at library in %s" % path)
    while take() != ")":
        pass
    if take() != "{":
        raise EqError("liberty parse error at library body in %s" % path)
    body = parse_group_body()
    cells = {}
    for name, args, _, children in body:
        if name != "cell" or children is None or not args:
            continue
        cname = args[0]
        if wanted is not None and cname not in wanted:
            continue
        cell = {"pins": {}, "ff": [], "latch": False, "statetable": False}
        for gname, gargs, _, gchildren in children:
            if gname == "pin" and gchildren is not None:
                attrs = {n: v for n, a, v, c in gchildren if v is not None}
                for pname in gargs:
                    cell["pins"][pname] = {"direction": attrs.get("direction"),
                                           "function": attrs.get("function"),
                                           "three_state": attrs.get("three_state")}
            elif gname in ("bus", "bundle"):
                cell["bus"] = True
            elif gname == "ff" and gchildren is not None:
                attrs = {n: v for n, a, v, c in gchildren if v is not None}
                cell["ff"].append({"vars": gargs, "clocked_on": attrs.get("clocked_on"),
                                   "next_state": attrs.get("next_state"), "clear": attrs.get("clear"),
                                   "preset": attrs.get("preset")})
            elif gname in ("latch", "ff_bank", "latch_bank"):
                cell["latch"] = True
            elif gname == "statetable":
                cell["statetable"] = True
        cells[cname] = cell
    return cells


def _norm_func(expr):
    return re.sub(r"[\s()]", "", expr or "")


def cell_role(cell):
    """Classify a liberty cell: 'physical' (no outputs), 'identity' (one input, one
    output, function = that input), 'flop', 'comb', or 'unsupported: <why>'."""
    ins = [p for p, d in cell["pins"].items() if d["direction"] == "input"]
    outs = [p for p, d in cell["pins"].items() if d["direction"] == "output"]
    others = [p for p, d in cell["pins"].items() if d["direction"] not in ("input", "output")]
    if cell.get("bus"):
        return "unsupported: bus or bundle pins"
    if others:
        return "unsupported: pin direction %s" % others
    if cell["latch"] or cell["statetable"]:
        return "unsupported: latch or statetable"
    if any(cell["pins"][p]["three_state"] for p in outs):
        return "unsupported: three-state output"
    if not outs:
        return "physical"
    if any(not cell["pins"][p]["function"] for p in outs):
        return "unsupported: output without function"
    if cell["ff"]:
        if len(cell["ff"]) != 1:
            return "unsupported: several ff groups"
        clk = _norm_func(cell["ff"][0]["clocked_on"])
        if clk not in ins:
            return "unsupported: clocked_on %r is not a plain input pin (not a rising-edge flip-flop)" % \
                cell["ff"][0]["clocked_on"]
        return "flop"
    if len(ins) == 1 and len(outs) == 1 and _norm_func(cell["pins"][outs[0]]["function"]) == ins[0]:
        return "identity"
    return "comb"


# --------------------------------------------------------------------------- netlist

_NL_TOKEN = re.compile(
    r"\s+|//[^\n]*|/\*.*?\*/|\(\*.*?\*\)|`[^\n]*"
    r"|(\\\S+)"                                    # 1 escaped identifier
    r"|([A-Za-z_][A-Za-z0-9_$]*)"                  # 2 identifier
    r"|(\d*\s*'[sS]?[bBoOdDhH]\s*[0-9a-fA-FxXzZ_?]+|\d+)"  # 3 number
    r"|([()\[\]{},;.:=#])", re.S)                  # 4 punctuation


class Netlist(object):
    """A flat structural Verilog module: ports, wires, instances with named pin
    connections, and bit-level connectivity. Token spans are kept where the
    mutations need them."""

    def __init__(self, path):
        self.path = path
        with open(path) as f:
            self.text = f.read()
        self.module = None
        self.ports = {}        # name -> "input"/"output"/"inout"
        self.ranges = {}       # name -> (msb, lsb) for vectors
        self.insts = {}        # name -> {"cell", "cell_span", "pins": {pin: [bits]}, "spans": {pin: [(s,e)]}}
        self.assigns = []      # (lhs bits, rhs bits)
        self._parse()

    # tokens: (kind, text, start, end); kinds: E(scaped) I(dent) N(umber) P(unct)
    def _tokens(self):
        for m in _NL_TOKEN.finditer(self.text):
            if m.lastindex is None:
                continue
            k = m.lastindex
            yield ("EINP"[k - 1], m.group(k), m.start(k), m.end(k))

    def _parse(self):
        it = self._tokens()
        buf = []

        def nxt():
            if buf:
                return buf.pop()
            try:
                return next(it)
            except StopIteration:
                return None

        def back(t):
            buf.append(t)

        def expect(text):
            t = nxt()
            if t is None or t[1] != text:
                raise EqError("netlist parse error: expected %r, got %r at offset %s in %s"
                              % (text, t and t[1], t and t[2], self.path))
            return t

        def skip_to(text):
            while True:
                t = nxt()
                if t is None:
                    raise EqError("netlist parse error: missing %r in %s" % (text, self.path))
                if t[1] == text:
                    return

        def read_range():
            t = nxt()
            if t and t[1] == "[":
                msb = int(nxt()[1])
                expect(":")
                lsb = int(nxt()[1])
                expect("]")
                return (msb, lsb)
            back(t)
            return None

        def read_names():
            names = []
            while True:
                t = nxt()
                if t[0] in "EI":
                    names.append(t[1])
                t = nxt()
                if t[1] == ";":
                    return names
                if t[1] != ",":
                    raise EqError("netlist parse error in declaration at offset %s in %s" % (t[2], self.path))

        def read_expr():
            """-> (bits MSB first, [(span, width) per top-level element]); a
            primary is its own single element, a concatenation lists its parts."""
            t = nxt()
            if t is None:
                raise EqError("netlist parse error: unexpected end of %s" % self.path)
            if t[1] == "{":
                bits, elems = [], []
                while True:
                    eb, eparts = read_expr()
                    if len(eparts) == 1:
                        elems.append(eparts[0])
                    else:  # nested concatenation: keep its width, no single span
                        elems.append((None, len(eb)))
                    bits.extend(eb)
                    t = nxt()
                    if t is not None and t[1] == "}":
                        return bits, elems
                    if t is None or t[1] != ",":
                        raise EqError("netlist parse error in concatenation at offset %s in %s"
                                      % (t and t[2], self.path))
            if t[0] == "N":
                bits = const_bits(t[1])
                return bits, [((t[2], t[3]), len(bits))]
            if t[0] in "EI":
                name, start, end = t[1], t[2], t[3]
                t2 = nxt()
                if t2 is not None and t2[1] == "[":
                    hi = int(nxt()[1])
                    t3 = nxt()
                    if t3[1] == ":":
                        lo = int(nxt()[1])
                        t4 = expect("]")
                    elif t3[1] == "]":
                        lo, t4 = hi, t3
                    else:
                        raise EqError("netlist parse error in bit select at offset %s" % t3[2])
                    step = -1 if hi >= lo else 1
                    bits = ["%s[%d]" % (name, i) for i in range(hi, lo + step, step)]
                    return bits, [((start, t4[3]), len(bits))]
                back(t2)
                bits = self.expand(name)
                return bits, [((start, end), len(bits))]
            raise EqError("netlist parse error in expression at offset %s: %r" % (t[2], t[1]))

        t = nxt()
        while t is not None and t[1] != "module":
            t = nxt()
        if t is None:
            raise EqError("no module in %s" % self.path)
        self.module = nxt()[1]
        skip_to(";")
        while True:
            t = nxt()
            if t is None:
                raise EqError("netlist parse error: missing endmodule in %s" % self.path)
            word = t[1]
            if word == "endmodule":
                break
            if word == "module":
                raise EqError("more than one module in %s (a flat netlist is expected)" % self.path)
            if word in ("input", "output", "inout", "wire", "supply0", "supply1", "tri"):
                t2 = nxt()
                if t2[1] not in ("wire", "reg", "signed"):
                    back(t2)
                rng = read_range()
                for n in read_names():
                    if rng:
                        self.ranges[n] = rng
                    if word in ("input", "output", "inout"):
                        self.ports[n] = word
                continue
            if word == "assign":
                lhs, _ = read_expr()
                expect("=")
                rhs, _ = read_expr()
                expect(";")
                if len(lhs) != len(rhs):
                    raise EqError("assign width mismatch at offset %s in %s" % (t[2], self.path))
                self.assigns.append((lhs, rhs))
                continue
            if t[0] in "EI":
                cell, cell_span = word, (t[2], t[3])
                t2 = nxt()
                if t2[1] == "#":
                    raise EqError("parameterised instance at offset %s is not supported" % t2[2])
                iname = t2[1]
                expect("(")
                pins, spans = {}, {}
                t3 = nxt()
                while t3[1] != ")":
                    if t3[1] == ",":
                        t3 = nxt()
                        continue
                    if t3[1] != ".":
                        raise EqError("positional connection at offset %s is not supported" % t3[2])
                    pin = nxt()[1]
                    expect("(")
                    t4 = nxt()
                    if t4[1] == ")":
                        pins[pin], spans[pin] = [], []
                    else:
                        back(t4)
                        bits, sp = read_expr()
                        expect(")")
                        pins[pin], spans[pin] = bits, sp
                    t3 = nxt()
                expect(";")
                if iname in self.insts:
                    raise EqError("duplicate instance name %s" % iname)
                self.insts[iname] = {"cell": cell, "cell_span": cell_span, "pins": pins, "spans": spans}
                continue
            raise EqError("netlist parse error: unexpected %r at offset %s in %s" % (word, t[2], self.path))

    def expand(self, name):
        if name in self.ranges:
            hi, lo = self.ranges[name]
            step = -1 if hi >= lo else 1
            return ["%s[%d]" % (name, i) for i in range(hi, lo + step, step)]
        return [name]


def const_bits(text):
    """Verilog constant -> list of '1'b0'/'1'b1'/'1'bx' bits, MSB first."""
    t = text.replace(" ", "").replace("_", "")
    m = re.match(r"^(\d*)'[sS]?([bBoOdDhH])([0-9a-fA-FxXzZ?]+)$", t)
    if not m:
        v, width = int(t), 32
        return ["1'b%d" % ((v >> i) & 1) for i in range(width - 1, -1, -1)]
    width = int(m.group(1)) if m.group(1) else 32
    base = m.group(2).lower()
    digits = m.group(3).lower()
    per = {"b": 1, "o": 3, "h": 4}.get(base)
    if per is None:
        v = int(digits)
        bits = [str((v >> i) & 1) for i in range(width - 1, -1, -1)]
    else:
        bits = []
        for d in digits:
            if d in "xz?":
                bits.extend(["x"] * per)
            else:
                bits.extend(list(format(int(d, 16), "0%db" % per)))
        bits = (["0"] * width + bits)[-width:]
    return ["1'b" + b for b in bits]


def sram_ports():
    """Directions of the SRAM macro's ports from the blackbox model."""
    with open(SRAM_BLACKBOX) as f:
        txt = f.read()
    ports = {}
    for d, name in re.findall(r"\b(input|output)\s+wire\s+(?:\[[^\]]*\]\s*)?(\w+)", txt):
        ports[name] = d
    if "A_DOUT" not in ports or "A_CLK" not in ports:
        raise EqError("unexpected SRAM blackbox model %s" % SRAM_BLACKBOX)
    return ports


def _by_role(roles, counts):
    out = {}
    for c, n in counts.items():
        r = "unsupported" if roles[c].startswith("unsupported") else roles[c]
        out[r] = out.get(r, 0) + n
    return dict(sorted(out.items()))


def analyze_structure(nl, lib):
    """Preconditions of the miter model, checked on the netlist:
    every cell is known, every output has a function, flip-flops are rising-edge
    liberty ff cells, every net has at most one driver, and every flip-flop clock
    pin and SRAM A_CLK is the clk port through identity buffers only (the AIG has
    one implicit clock). Returns a dict with 'errors' (empty when all hold)."""
    errors = []
    sram = sram_ports()
    roles, counts = {}, {}
    for name, inst in nl.insts.items():
        c = inst["cell"]
        counts[c] = counts.get(c, 0) + 1
    for c in counts:
        if c == SRAM_CELL:
            roles[c] = "sram"
        elif c not in lib:
            roles[c] = "unsupported: not in the liberty file"
        else:
            roles[c] = cell_role(lib[c])
    for c, r in sorted(roles.items()):
        if r.startswith("unsupported"):
            errors.append("cell %s (%d instances): %s" % (c, counts[c], r))
    if nl.module != DESIGN:
        errors.append("module is %s, expected %s" % (nl.module, DESIGN))
    for p, w in list(TT_INPUTS.items()) + list(TT_OUTPUTS.items()):
        want = "input" if p in TT_INPUTS else "output"
        if nl.ports.get(p) != want or len(nl.expand(p)) != w:
            errors.append("port %s is not a %d-bit %s" % (p, w, want))
    # drivers
    drivers = {}

    def add_driver(bit, who):
        if bit.startswith("1'b"):
            errors.append("%s drives a constant" % (who,))
            return
        if bit in drivers:
            errors.append("net %s has two drivers: %s and %s" % (bit, drivers[bit], who))
        else:
            drivers[bit] = who

    for p, d in nl.ports.items():
        if d == "input":
            for b in nl.expand(p):
                add_driver(b, ("port", p))
    for lhs, rhs in nl.assigns:
        for lb, rb in zip(lhs, rhs):
            add_driver(lb, ("assign", rb))
    for name in sorted(nl.insts):
        inst = nl.insts[name]
        c = inst["cell"]
        if roles.get(c, "").startswith("unsupported"):
            continue
        for pin, bits in inst["pins"].items():
            if c == SRAM_CELL:
                d = sram.get(pin)
            else:
                d = lib[c]["pins"].get(pin, {}).get("direction")
            if d is None:
                errors.append("instance %s (%s) connects unknown pin %s" % (name, c, pin))
            elif d == "output":
                for i, b in enumerate(bits):
                    add_driver(b, (name, pin, i))

    def clock_depth(bit, what):
        depth, seen = 0, set()
        while True:
            if bit == CLOCK_PORT:
                return depth
            if bit in seen:
                errors.append("%s: clock path loops at %s" % (what, bit))
                return None
            seen.add(bit)
            d = drivers.get(bit)
            if d is None:
                errors.append("%s: clock net %s has no driver" % (what, bit))
                return None
            if d[0] == "port":
                errors.append("%s: clock comes from port %s, not %s" % (what, d[1], CLOCK_PORT))
                return None
            if d[0] == "assign":
                bit = d[1]
                continue
            inst = nl.insts[d[0]]
            if roles.get(inst["cell"]) != "identity":
                errors.append("%s: clock path passes through %s (%s), which is not a buffer"
                              % (what, d[0], inst["cell"]))
                return None
            ins = [p for p, pd in lib[inst["cell"]]["pins"].items() if pd["direction"] == "input"]
            bit = inst["pins"][ins[0]][0]
            depth += 1

    ff_depth, sram_depth, n_ff = {}, {}, 0
    for name in sorted(nl.insts):
        inst = nl.insts[name]
        c = inst["cell"]
        if roles.get(c) == "flop":
            n_ff += 1
            clk_pin = _norm_func(lib[c]["ff"][0]["clocked_on"])
            bits = inst["pins"].get(clk_pin, [])
            if len(bits) != 1:
                errors.append("flip-flop %s: clock pin %s not connected" % (name, clk_pin))
                continue
            d = clock_depth(bits[0], "flip-flop %s" % name)
            if d is not None:
                ff_depth[d] = ff_depth.get(d, 0) + 1
        elif c == SRAM_CELL:
            for pin in sram:
                if pin not in inst["pins"]:
                    errors.append("SRAM %s: pin %s not connected" % (name, pin))
            bits = inst["pins"].get("A_CLK", [])
            if len(bits) == 1:
                d = clock_depth(bits[0], "SRAM %s A_CLK" % name)
                if d is not None:
                    sram_depth[name] = d
    del errors[200:]
    return {
        "module": nl.module,
        "instances": len(nl.insts),
        "cell_types": len(counts),
        "instances_by_role": _by_role(roles, counts),
        "flip_flops": n_ff,
        "flip_flop_cells": sorted(c for c in counts if roles[c] == "flop"),
        "srams": sorted(n for n, i in nl.insts.items() if i["cell"] == SRAM_CELL),
        "clock": {
            "ff_depth_histogram": {str(k): ff_depth[k] for k in sorted(ff_depth)},
            "ff_depth_min": min(ff_depth) if ff_depth else None,
            "ff_depth_max": max(ff_depth) if ff_depth else None,
            "sram_depth": sram_depth,
            "note": "buffer cells between the clk port and the pin (informational; "
                    "formal_eq/README.md, 'Clock depth')",
        },
        "errors": errors,
    }


# --------------------------------------------------------------------------- mutations

def mutate_near_output_nand(nl, lib, src_cell="sg13cmos5l_nand2_1", dst_cell="sg13cmos5l_nor2_1"):
    """mutA-style control: the src_cell instance nearest (breadth-first, backwards
    through any cell except the SRAM macros) to a uo_out/uio_out bit becomes
    dst_cell. Ties are broken by instance name. Returns (text, record)."""
    for c in (src_cell, dst_cell):
        if c not in lib:
            raise EqError("mutation cell %s not in the liberty file" % c)
    if set(lib[src_cell]["pins"]) != set(lib[dst_cell]["pins"]):
        raise EqError("%s and %s do not have the same pins" % (src_cell, dst_cell))
    drivers = {}
    for name, inst in nl.insts.items():
        c = inst["cell"]
        if c == SRAM_CELL or c not in lib:
            continue
        for pin, bits in inst["pins"].items():
            if lib[c]["pins"].get(pin, {}).get("direction") == "output":
                for b in bits:
                    drivers[b] = (name, pin)
    assign_src = {}
    for lhs, rhs in nl.assigns:
        for lb, rb in zip(lhs, rhs):
            assign_src[lb] = rb
    frontier = sorted(set(nl.expand("uo_out") + nl.expand("uio_out")))
    seen, level = set(), 0
    while frontier:
        found, nxt = [], set()
        for b in frontier:
            if b in seen:
                continue
            seen.add(b)
            if b in assign_src:
                nxt.add(assign_src[b])
                continue
            d = drivers.get(b)
            if d is None:
                continue
            inst = nl.insts[d[0]]
            if inst["cell"] == src_cell:
                found.append(d[0])
                continue
            for pin, bits in inst["pins"].items():
                if lib[inst["cell"]]["pins"].get(pin, {}).get("direction") == "input":
                    nxt.update(x for x in bits if not x.startswith("1'b"))
        if found:
            target = sorted(found)[0]
            inst = nl.insts[target]
            s, e = inst["cell_span"]
            text = nl.text[:s] + dst_cell + nl.text[e:]
            return text, {"kind": "near-output-nand", "instance": target, "from": src_cell,
                          "to": dst_cell, "levels_from_output": level,
                          "pins": {p: v for p, v in inst["pins"].items()}}
        frontier = sorted(nxt)
        level += 1
    raise EqError("no %s instance reaches uo_out/uio_out" % src_cell)


def mutate_sram_din_swap(nl, instance=None, bits=(3, 2)):
    """mutC-style control: swap two A_DIN bits of one SRAM macro (default: the
    first SRAM instance by name, bits 3 and 2; if those two are the same net or
    a constant, the first adjacent pair from bit 0 that differs)."""
    srams = sorted(n for n, i in nl.insts.items() if i["cell"] == SRAM_CELL)
    if not srams:
        raise EqError("no %s instance in the netlist" % SRAM_CELL)
    name = instance or srams[0]
    if name not in nl.insts or nl.insts[name]["cell"] != SRAM_CELL:
        raise EqError("no SRAM instance %s" % name)
    inst = nl.insts[name]
    din, elems = inst["pins"].get("A_DIN", []), inst["spans"].get("A_DIN", [])
    width = len(din)
    if len(elems) != width or any(w != 1 or s is None for s, w in elems):
        raise EqError("A_DIN of %s is not a concatenation of single bits" % name)
    spans = [s for s, _ in elems]

    def ok(pair):
        a, b = [din[width - 1 - i] for i in pair]
        return a != b and not a.startswith("1'b") and not b.startswith("1'b")

    pair = tuple(bits)
    if not ok(pair):
        pair = next(((i + 1, i) for i in range(width - 1) if ok((i + 1, i))), None)
        if pair is None:
            raise EqError("no two distinct A_DIN nets on %s" % name)
    ia, ib = [width - 1 - i for i in pair]          # list positions (MSB first)
    (sa, ea), (sb, eb) = spans[ia], spans[ib]
    if sa > sb:
        (sa, ea), (sb, eb) = (sb, eb), (sa, ea)
    t = nl.text

    def moved(s, e):
        # an escaped identifier ends at white space; keep one after it when it moves
        return t[s:e] + " " if t[s] == "\\" else t[s:e]

    text = t[:sa] + moved(sb, eb) + t[ea:sb] + moved(sa, ea) + t[eb:]
    return text, {"kind": "sram-din-swap", "instance": name, "bits": list(pair),
                  "nets": {"A_DIN[%d]" % pair[0]: din[ia], "A_DIN[%d]" % pair[1]: din[ib]}}


# --------------------------------------------------------------------------- yosys scripts

def gold_script(reads, n_srams):
    return "\n".join(reads + [
        "hierarchy -check -top pe_eq_wrap",
        "proc",
        "flatten",
        "opt_clean",
        "memory -nomap",
        "memory_map",
        "opt_clean",
        "select -assert-count %d t:%s" % (n_srams, SRAM_CELL),
        "expose -evert t:%s" % SRAM_CELL,
        "rename pe_eq_wrap gold",
        "hierarchy -top gold",
        "check -assert",
        "stat",
        "write_rtlil gold.il",
        ""])


def gate_script(reads, n_srams):
    return "\n".join(reads + [
        "hierarchy -check -top pe_eq_wrap",
        "proc",
        "flatten",
        "opt_clean",
        "select -assert-count %d t:%s" % (n_srams, SRAM_CELL),
        "expose -evert t:%s" % SRAM_CELL,
        "rename pe_eq_wrap gate",
        "hierarchy -top gate",
        "check -assert",
        "stat",
        "write_rtlil gate.il",
        ""])


# No -ignore_gold_x: with it, miter builds gold_x = $eqx(gold, 1'bx) per output
# bit, and the setundef -zero below would turn that into (gold == 0), masking
# every mismatch where the gold bit is 0 (control c1).
MITER_SCRIPT = "\n".join([
    "read_rtlil ../gold/gold.il",
    "read_rtlil gate.il",
    "miter -equiv -flatten gold gate miter",
    "hierarchy -top miter",
    "delete t:$scopeinfo",
    # asynchronous resets act at the clock edge and on the outputs in the same cycle
    "async2sync",
    "dffunmap",
    # every flip-flop starts at 0 on both sides (the wrapper resets both in cycle 0)
    "setundef -zero -init",
    "opt_clean",
    "techmap",
    "opt -fast",
    "dffunmap",
    "techmap",
    # remaining x constants (RTL don't-cares, out-of-range reads of the RTL arrays) -> 0
    "setundef -zero",
    "aigmap",
    "opt_clean -purge",
    # one implicit clock: only AND, NOT and rising-edge D flip-flops may remain
    # (control c9), and every flip-flop is clocked by the clk input (control c8)
    "select -assert-none t:* t:$_AND_ t:$_NOT_ t:$_DFF_P_ %u %u %d",
    "select -assert-none t:$_DFF_P_ %x:+[C] t:$_DFF_P_ %d w:in_clk %d",
    "stat",
    "write_aiger -zinit -map miter.aigmap miter.aig",
    ""])


# --------------------------------------------------------------------------- ABC verdict

def parse_abc(text, rc, timed_out):
    """Map ABC dprove output to a verdict. Anything unexpected is an error."""
    info = {}
    stats = re.findall(r"i/o\s*=\s*(\d+)\s*/\s*(\d+)\s+lat\s*=\s*(\d+)\s+and\s*=\s*(\d+)", text)
    if stats:
        i, o, lat, ands = [int(x) for x in stats[0]]
        info["aig_first"] = {"inputs": i, "outputs": o, "latches": lat, "and_nodes": ands}
    if len(stats) > 1:
        i, o, lat, ands = [int(x) for x in stats[1]]
        info["aig_strashed"] = {"inputs": i, "outputs": o, "latches": lat, "and_nodes": ands}
    # ABC's dprove prints one of: "Networks are equivalent." (or "... equivalent
    # after <engine>."), "Networks are not equivalent." (BMC) or "Networks are NOT
    # EQUIVALENT." (e.g. interpolation), "Networks are UNDECIDED." (or "undecided (...)")
    eq = re.findall(r"^Networks are equivalent\b", text, re.M)
    neq = re.findall(r"^Networks are not equivalent\b", text, re.M | re.I)
    und = re.findall(r"^Networks are undecided\b", text, re.M | re.I)
    disproved = re.search(r"^Property DISPROVED\b", text, re.M)
    fr = re.search(r'Output (\d+) of miter "[^"]*" was asserted in frame (\d+)', text)
    if fr:
        info["failed_output"] = int(fr.group(1))
        info["frame"] = int(fr.group(2))
    t = re.search(r"^Networks are \S+\.?\s+Time\s*=\s*([\d.]+)\s*sec", text, re.M)
    if t:
        info["abc_reported_s"] = float(t.group(1))
    lines = [ln for ln in text.splitlines() if ln.startswith("Networks are")]
    info["verdict_line"] = lines[-1].strip() if lines else None
    if timed_out:
        return "timeout", info
    if info.get("aig_first", {}).get("outputs") != 1:
        info["why"] = "the miter has %s outputs, expected 1" % info.get("aig_first", {}).get("outputs")
        return "error", info
    if re.search(r"^The network has no latches\. Running CEC\.", text, re.M):
        # a purely combinational miter: dprove runs CEC on the single output
        sat = re.findall(r"^SATISFIABLE\b", text, re.M)
        unsat = re.findall(r"^UNSATISFIABLE\b", text, re.M)
        info["mode"] = "cec"
        info["verdict_line"] = "UNSATISFIABLE" if unsat else "SATISFIABLE" if sat else None
        if rc != 0 or len(sat) + len(unsat) != 1 or eq or neq or und:
            info["why"] = "unrecognised CEC output (exit status %s)" % rc
            return "error", info
        return ("equivalent" if unsat else "not_equivalent"), info
    info["mode"] = "sequential"
    if len(eq) + len(neq) + len(und) != 1:
        info["why"] = "expected exactly one 'Networks are ...' line, found %d" % (len(eq) + len(neq) + len(und))
        return "error", info
    if rc != 0:
        info["why"] = "yosys-abc exit status %s" % rc
        return "error", info
    if eq:
        if fr or disproved:
            info["why"] = "equivalent and a counterexample in one log"
            return "error", info
        return "equivalent", info
    if neq:
        return "not_equivalent", info
    return "undecided", info


# --------------------------------------------------------------------------- one case

class Runner(object):
    def __init__(self, tools, abc_timeout, build_timeout):
        self.tools = tools
        self.abc_timeout = abc_timeout
        self.build_timeout = build_timeout

    def yosys(self, script, name, cwd):
        path = os.path.join(cwd, name + ".ys")
        with open(path, "w") as f:
            f.write(script)
        rc, wall, to = run_logged([self.tools["yosys"], "-q", "-l", name + ".log", name + ".ys"],
                                  os.path.join(cwd, name + ".stdout"), self.build_timeout, cwd)
        step = {"rc": rc, "wall_s": wall, "timed_out": to}
        if rc != 0:
            step["tail"] = tail(os.path.join(cwd, name + ".log"), 8) or tail(os.path.join(cwd, name + ".stdout"), 8)
        return step

    def build_gold(self, reads, n_srams, gold_dir):
        os.makedirs(gold_dir, exist_ok=True)
        il = os.path.join(gold_dir, "gold.il")
        if os.path.exists(il):
            os.remove(il)
        step = self.yosys(gold_script(reads, n_srams), "gold", gold_dir)
        step["ok"] = step["rc"] == 0 and os.path.isfile(il) and os.path.getsize(il) > 0
        return step

    def run_case(self, case_dir, gate_reads, n_srams):
        """gate build + miter + ABC in case_dir (gold.il must exist in ../gold)."""
        os.makedirs(case_dir, exist_ok=True)
        for stale in ("gate.il", "miter.aig", "miter.aigmap", "abc.log"):
            if os.path.exists(os.path.join(case_dir, stale)):
                os.remove(os.path.join(case_dir, stale))
        res = {"steps": {}}
        g = self.yosys(gate_script(gate_reads, n_srams), "gate", case_dir)
        res["steps"]["gate"] = g
        if g["rc"] != 0:
            res["verdict"], res["why"] = "error", "gate build failed" + (" (time limit)" if g["timed_out"] else "")
            return res
        m = self.yosys(MITER_SCRIPT, "miter", case_dir)
        res["steps"]["miter"] = m
        if m["rc"] != 0 or not os.path.exists(os.path.join(case_dir, "miter.aig")):
            res["verdict"], res["why"] = "error", "miter build failed" + (" (time limit)" if m["timed_out"] else "")
            return res
        res["miter_aig_sha256"] = sha256_file(os.path.join(case_dir, "miter.aig"))
        rc, wall, to = run_logged([self.tools["yosys_abc"], "-c", ABC_SCRIPT],
                                  os.path.join(case_dir, "abc.log"), self.abc_timeout, case_dir)
        with open(os.path.join(case_dir, "abc.log"), errors="replace") as f:
            text = f.read()
        verdict, info = parse_abc(text, rc, to)
        res["steps"]["abc"] = {"rc": rc, "wall_s": wall, "timed_out": to, "command": ABC_SCRIPT,
                               "time_limit_s": self.abc_timeout}
        res["abc"] = info
        res["verdict"] = verdict
        if verdict == "error":
            res["why"] = info.get("why", "ABC output not recognised")
        return res


def tail(path, n):
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return None


# --------------------------------------------------------------------------- inputs

def resolve_run_dir(run_dir):
    """Find the gate-level netlist and metadata in a tt_submission artifact
    (the artifact root or its tt_submission/), a LibreLane run, a run of
    scripts/sweep or tools/opt, or a final/ directory."""
    rd = os.path.abspath(run_dir)
    meta = {"run_dir": rd}
    tts = [os.path.join(rd, "tt_submission"), rd]
    for d in tts:
        nl = os.path.join(d, DESIGN + ".v")
        if os.path.isfile(nl) and os.path.isfile(os.path.join(d, "pdk.json")):
            meta["layout"] = "tt_submission"
            for j in ("pdk.json", "commit_id.json"):
                p = os.path.join(d, j)
                if os.path.isfile(p):
                    with open(p) as f:
                        meta[j.replace(".json", "")] = json.load(f)
            root = os.path.dirname(d) if d.endswith("tt_submission") else None
            if root and os.path.isdir(os.path.join(root, "src")):
                meta["artifact_src"] = {n: sha256_file(os.path.join(root, "src", n))
                                        for n in ("project.v", "protocol_emulator_core.v")
                                        if os.path.isfile(os.path.join(root, "src", n))}
                meta["artifact_src_dir"] = os.path.join(root, "src")
            return nl, meta
    for layout, sub in (("librelane_run", "final/nl"), ("sweep_run", "out/final/nl"), ("final_dir", "nl")):
        for ext in (".nl.v", ".nl.v.gz"):
            nl = os.path.join(rd, sub, DESIGN + ext)
            if os.path.isfile(nl):
                meta["layout"] = layout
                sums = os.path.join(os.path.dirname(os.path.dirname(nl)), "SHA256SUMS")
                if os.path.isfile(sums):
                    rel = "./nl/" + os.path.basename(nl)
                    want = None
                    with open(sums) as f:
                        for line in f:
                            parts = line.split()
                            if len(parts) == 2 and parts[1] in (rel, rel[2:]):
                                want = parts[0]
                    if want is not None:
                        got = sha256_file(nl)
                        meta["sha256sums_check"] = "ok" if got == want else "MISMATCH"
                        if got != want:
                            raise EqError("%s does not match %s" % (nl, sums))
                return nl, meta
    raise EqError("no gate-level netlist found under %s (tt_submission/%s.v, final/nl/, out/final/nl/ or nl/)"
                  % (rd, DESIGN))


def pdk_root_default():
    work = os.environ.get("PE_WORK", os.path.join(REPO, "build", "tt-work"))
    flow = os.environ.get("PE_FLOW_ROOT", os.path.join(work, "sram-flow"))
    return os.environ.get("PE_PDK_ROOT") or os.environ.get("PDK_ROOT") or os.path.join(flow, "pdk")


def pdk_version(pdk_root, pdk):
    """The PDK revision at pdk_root: the SOURCES file that volare/ciel write
    ('IHP-Open-PDK <commit>'), else the git HEAD of a checkout."""
    src = os.path.join(pdk_root, pdk, "SOURCES")
    if os.path.isfile(src):
        with open(src) as f:
            txt = f.read()
        m = re.search(r"\b([0-9a-f]{40})\b", txt)
        return (m.group(1) if m else None), "SOURCES: " + txt.strip().splitlines()[0] if txt.strip() else "SOURCES"
    for d in (os.path.join(pdk_root, pdk), pdk_root):
        try:
            out = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, universal_newlines=True, timeout=30)
            if out.returncode == 0 and re.match(r"^[0-9a-f]{40}$", out.stdout.strip()):
                return out.stdout.strip(), "git HEAD of %s" % d
        except (OSError, subprocess.SubprocessError):
            pass
    return None, "unknown (no SOURCES file, not a git checkout)"


def resolve_inputs(args):
    """-> dict of resolved paths and provenance; raises EqError."""
    inp = {}
    if bool(args.netlist) == bool(args.run_dir):
        raise EqError("give exactly one of --netlist and --run-dir")
    if args.run_dir:
        nl_src, meta = resolve_run_dir(args.run_dir)
        inp["run"] = meta
    else:
        nl_src = os.path.abspath(args.netlist)
        if not os.path.isfile(nl_src):
            raise EqError("no netlist %s" % nl_src)
    inp["netlist_source"] = {"path": nl_src, "sha256": sha256_file(nl_src)}
    # RTL
    variant = args.variant or os.environ.get("PE_VARIANT") or "base"
    inp["variant"] = variant
    rtl_from_artifact = getattr(args, "rtl_from_artifact", False)
    if rtl_from_artifact:
        src = inp.get("run", {}).get("artifact_src_dir")
        if not src:
            raise EqError("--rtl-from-artifact needs a tt_submission artifact root with src/")
        top, core = os.path.join(src, "project.v"), os.path.join(src, "protocol_emulator_core.v")
    else:
        top = os.path.abspath(args.top) if args.top else os.path.join(REPO, "src", "project.v")
        core_arg = args.core or os.environ.get("PE_CORE")
        if core_arg:
            core = os.path.abspath(core_arg)
        elif variant in VARIANTS:
            core = os.path.join(REPO, VARIANTS[variant][0])
        else:
            raise EqError("variant %r has no packaged core; give --core (e.g. build/variants/%s/"
                          "protocol_emulator_core.v from scripts/gen_variants.sh)" % (variant, variant))
    for p in (top, core):
        if not os.path.isfile(p):
            raise EqError("missing RTL file %s" % p)
    with open(core) as f:
        header = f.readline().strip()
    inp["top"] = {"path": top, "sha256": sha256_file(top)}
    inp["core"] = {"path": core, "sha256": sha256_file(core), "header": header}
    if variant in VARIANTS and VARIANTS[variant][1] not in header:
        raise EqError("the core's header line names another configuration than variant %s (%s): %r"
                      % (variant, VARIANTS[variant][1], header))
    art = inp.get("run", {}).get("artifact_src")
    if art:
        inp["artifact_src_matches"] = {
            "project.v": art.get("project.v") == inp["top"]["sha256"],
            "protocol_emulator_core.v": art.get("protocol_emulator_core.v") == inp["core"]["sha256"]}
    # liberty / PDK
    pdk = args.pdk or inp.get("run", {}).get("pdk", {}).get("PDK") or DEFAULT_PDK
    if args.liberty:
        lib = os.path.abspath(args.liberty)
        inp["pdk"] = {"name": pdk, "root": None, "version": None, "version_source": "--liberty given"}
    else:
        root = os.path.abspath(args.pdk_root or pdk_root_default())
        lib = os.path.join(root, pdk, LIBERTY_REL)
        ver, how = pdk_version(root, pdk)
        inp["pdk"] = {"name": pdk, "root": root, "version": ver, "version_source": how}
    if not os.path.isfile(lib):
        raise EqError("no liberty file %s (set PDK_ROOT or --pdk-root, or give --liberty)" % lib)
    inp["liberty"] = {"path": lib, "sha256": sha256_file(lib)}
    want = inp.get("run", {}).get("pdk", {}).get("PDK_VERSION")
    if want:
        inp["pdk"]["netlist_pdk_version"] = want
        have = inp["pdk"].get("version")
        match = have == want
        inp["pdk"]["matches_netlist"] = match if have else None
        if not match and not args.allow_pdk_mismatch:
            raise EqError("the netlist was built with %s %s but the PDK here is %s (%s); install that "
                          "revision or pass --allow-pdk-mismatch" % (pdk, want, have, inp["pdk"]["version_source"]))
    for k in ("top", "core", "liberty"):
        no_space(inp[k]["path"], k)
    no_space(SRAM_BLACKBOX, "SRAM blackbox")
    no_space(WRAPPER, "wrapper")
    return inp


# --------------------------------------------------------------------------- controls

def load_controls():
    ctl = []
    for fn in sorted(os.listdir(CONTROLS_DIR)):
        if not fn.endswith(".v"):
            continue
        path = os.path.join(CONTROLS_DIR, fn)
        with open(path) as f:
            m = re.search(r"pe-eq-control:\s*(.*)", f.read())
        if not m:
            continue
        kv = dict(x.split("=", 1) for x in m.group(1).split())
        ctl.append({"name": fn[:-2], "path": path, "expect": kv["expect"], "srams": int(kv.get("srams", 0)),
                    "min_frame": int(kv["min_frame"]) if "min_frame" in kv else None})
    return ctl


def run_controls(runner, out_dir, jobs):
    """Known-answer cases for the recipe itself (same scripts, small designs)."""
    os.makedirs(out_dir, exist_ok=True)
    ctl = load_controls()
    if not ctl:
        raise EqError("no controls in %s" % CONTROLS_DIR)

    def one(c):
        d = os.path.join(out_dir, c["name"])
        os.makedirs(d, exist_ok=True)
        base = ["read_verilog -sv %s" % SRAM_BLACKBOX, "read_verilog -sv %s" % WRAPPER]
        g = runner.build_gold(["read_verilog -sv %s" % c["path"]] + base, c["srams"], os.path.join(d, "gold"))
        if not g["ok"]:
            return dict(c, verdict="error", why="gold build failed", steps={"gold": g})
        r = runner.run_case(os.path.join(d, "case"),
                            ["read_verilog -sv -DPE_GATE %s" % c["path"]] + base, c["srams"])
        r["steps"]["gold"] = g
        out = dict(c)
        out.update(r)
        ok = r["verdict"] == c["expect"]
        if ok and c["min_frame"] is not None:
            ok = r.get("abc", {}).get("frame", -1) >= c["min_frame"]
        out["met_expectation"] = ok
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        results = list(ex.map(one, ctl))
    return results


# --------------------------------------------------------------------------- commands

def tool_files():
    """sha256 of the files that define the check (the tool may run from a
    modified checkout; tool_git says whether it did)."""
    files = [os.path.abspath(__file__), WRAPPER, SRAM_BLACKBOX]
    files += [c["path"] for c in load_controls()]
    return {os.path.relpath(p, REPO): sha256_file(p) for p in files}


def cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def base_result(label, command):
    return {"schema": SCHEMA, "label": label, "command": command, "argv": sys.argv[1:],
            "time_start": now_iso(), "host": socket.gethostname().split(".")[0], "cpu_model": cpu_model(),
            "slurm_job_id": os.environ.get("SLURM_JOB_ID"), "tool_git": git_state(),
            "tool_files": tool_files()}


def default_out(label):
    return os.path.join(REPO, "build", "formal_eq", label)


def cmd_check(args):
    t0 = time.time()
    label = args.label or (os.path.basename(os.path.abspath(args.run_dir)) if args.run_dir
                           else os.path.splitext(os.path.basename(args.netlist or "netlist"))[0])
    out = os.path.abspath(args.out or default_out(label))
    os.makedirs(out, exist_ok=True)
    res_path = os.path.join(out, "result.json")
    if os.path.exists(res_path):      # never leave a stale verdict behind
        os.remove(res_path)
    res = base_result(label, "check" + (" --selftest" if args.selftest else ""))
    res["out"] = out

    def finish(verdict, code):
        res["verdict"] = verdict
        res["exit_code"] = code
        res["pass"] = code == 0
        res["wall_s"] = round(time.time() - t0, 1)
        res["peak_child_rss_kb"] = peak_child_rss_kb()
        res["time_end"] = now_iso()
        write_json(res_path, res)
        print(json.dumps({k: res.get(k) for k in ("label", "verdict", "exit_code", "pass", "wall_s")}))
        if res.get("why"):
            print("why: %s" % res["why"], file=sys.stderr)
        return code

    try:
        res["tools"] = find_tools()
        inp = resolve_inputs(args)
        res["inputs"] = inp
        os.makedirs(os.path.join(out, "inputs"), exist_ok=True)
        nl_src = inp["netlist_source"]["path"]
        nl_path = os.path.join(out, "inputs", DESIGN + ".v")
        if nl_src.endswith(".gz"):
            with gzip.open(nl_src, "rb") as fi, open(nl_path + ".tmp", "wb") as fo:
                shutil.copyfileobj(fi, fo)
            os.replace(nl_path + ".tmp", nl_path)
        else:
            shutil.copyfile(nl_src, nl_path)
        inp["netlist"] = {"path": nl_path, "sha256": sha256_file(nl_path)}
        # structural preconditions
        ts = time.time()
        nl = Netlist(nl_path)
        lib = parse_liberty(inp["liberty"]["path"], wanted=set(i["cell"] for i in nl.insts.values()) |
                            {"sg13cmos5l_nand2_1", "sg13cmos5l_nor2_1"})
        st = analyze_structure(nl, lib)
        st["wall_s"] = round(time.time() - ts, 1)
        res["structure"] = st
        if st["errors"]:
            res["why"] = "structural precondition failed: %s" % st["errors"][0]
            return finish("error", EXIT["error"])
        n_srams = len(st["srams"])
        runner = Runner(res["tools"], args.timeout, args.build_timeout)
        jobs = args.jobs or int(os.environ.get("SLURM_CPUS_PER_TASK", "1") or 1)
        # cases: the netlist, plus the two netlist mutants with --selftest
        cases = [("netlist", nl_path, "equivalent", None)]
        if args.selftest:
            text, rec = mutate_near_output_nand(nl, lib)
            p = os.path.join(out, "inputs", "mutA.v")
            with open(p, "w") as f:
                f.write(text)
            rec["sha256"] = sha256_file(p)
            cases.append(("mutA", p, "not_equivalent", rec))
            text, rec = mutate_sram_din_swap(nl)
            p = os.path.join(out, "inputs", "mutC.v")
            with open(p, "w") as f:
                f.write(text)
            rec["sha256"] = sha256_file(p)
            cases.append(("mutC", p, "not_equivalent", rec))
        del nl
        gold_reads = ["read_verilog -sv %s" % SRAM_BLACKBOX,
                      "read_verilog -sv %s %s" % (inp["top"]["path"], inp["core"]["path"]),
                      "read_verilog -sv %s" % WRAPPER]
        g = runner.build_gold(gold_reads, n_srams, os.path.join(out, "gold"))
        res["gold_build"] = g
        if not g["ok"]:
            res["why"] = "gold (RTL) build failed" + (" (time limit)" if g["timed_out"] else "")
            return finish("error", EXIT["error"])

        def one(case):
            name, path, expect, rec = case
            reads = ["read_liberty -ignore_miss_func %s" % inp["liberty"]["path"],
                     "read_verilog -sv %s" % SRAM_BLACKBOX,
                     "read_verilog %s" % no_space(path, "netlist"),
                     "read_verilog -sv %s" % WRAPPER]
            r = runner.run_case(os.path.join(out, name), reads, n_srams)
            r.update({"name": name, "expect": expect})
            if rec:
                r["mutation"] = rec
            r["met_expectation"] = r["verdict"] == expect
            return r

        ctl_future = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            if args.selftest:
                ctl_future = ex.submit(run_controls, runner, os.path.join(out, "controls"), 1)
            case_results = list(ex.map(one, cases))
            ctl_results = ctl_future.result() if ctl_future else None
        main = case_results[0]
        res["case"] = main
        res["timings_s"] = {"structure": st["wall_s"], "gold_build": g["wall_s"]}
        for k in ("gate", "miter", "abc"):
            res["timings_s"][k if k == "abc" else k + "_build"] = main["steps"].get(k, {}).get("wall_s")
        verdict = main["verdict"]
        if main.get("why"):
            res["why"] = main["why"]
        code = EXIT[verdict]
        if args.selftest:
            res["selftest"] = {"netlist_controls": case_results[1:], "recipe_controls": ctl_results}
            missed = [c["name"] for c in case_results[1:] + ctl_results if not c["met_expectation"]]
            res["selftest"]["missed"] = missed
            res["selftest"]["pass"] = not missed
            if missed and code == 0:
                code = 1
                res["why"] = "self-test: %s missed the expected verdict" % ", ".join(missed)
        return finish(verdict, code)
    except EqError as e:
        res["why"] = str(e)
        return finish("error", EXIT["error"])
    except Exception as e:  # noqa: BLE001 - any crash is an error verdict, never a pass
        res["why"] = "internal error: %s: %s" % (type(e).__name__, e)
        return finish("error", EXIT["error"])


def cmd_controls(args):
    t0 = time.time()
    out = os.path.abspath(args.out or default_out("controls"))
    os.makedirs(out, exist_ok=True)
    if os.path.exists(os.path.join(out, "result.json")):
        os.remove(os.path.join(out, "result.json"))
    res = base_result("controls", "controls")
    try:
        res["tools"] = find_tools()
        runner = Runner(res["tools"], args.timeout, args.build_timeout)
        res["recipe_controls"] = run_controls(runner, out, args.jobs or 1)
        missed = [c["name"] for c in res["recipe_controls"] if not c["met_expectation"]]
        res["missed"] = missed
        code = 0 if not missed else 1
    except Exception as e:  # noqa: BLE001 - fail closed
        res["why"] = "%s: %s" % (type(e).__name__, e)
        code = 2
    res.update({"exit_code": code, "pass": code == 0, "wall_s": round(time.time() - t0, 1), "time_end": now_iso()})
    write_json(os.path.join(out, "result.json"), res)
    for c in res.get("recipe_controls", []):
        print("%-22s expect %-15s got %-15s frame %-4s %s" % (
            c["name"], c["expect"], c["verdict"], c.get("abc", {}).get("frame", "-"),
            "ok" if c["met_expectation"] else "MISSED"))
    return code


def cmd_mutate(args):
    lib_path = args.liberty or os.path.join(pdk_root_default(), DEFAULT_PDK, LIBERTY_REL)
    nl = Netlist(os.path.abspath(args.netlist))
    if args.kind == "near-output-nand":
        lib = parse_liberty(lib_path, wanted=set(i["cell"] for i in nl.insts.values()) |
                            {"sg13cmos5l_nand2_1", "sg13cmos5l_nor2_1"})
        text, rec = mutate_near_output_nand(nl, lib)
    else:
        text, rec = mutate_sram_din_swap(nl, args.instance)
    with open(args.out, "w") as f:
        f.write(text)
    rec["sha256"] = sha256_file(args.out)
    print(json.dumps(rec))
    return 0


def cmd_structure(args):
    if bool(args.netlist) == bool(args.run_dir):
        raise EqError("give exactly one of --netlist and --run-dir")
    path = args.netlist if args.netlist else resolve_run_dir(args.run_dir)[0]
    if path.endswith(".gz"):
        raise EqError("decompress the netlist first")
    lib_path = args.liberty or os.path.join(os.path.abspath(args.pdk_root or pdk_root_default()),
                                            DEFAULT_PDK, LIBERTY_REL)
    nl = Netlist(os.path.abspath(path))
    lib = parse_liberty(lib_path, wanted=set(i["cell"] for i in nl.insts.values()))
    st = analyze_structure(nl, lib)
    print(json.dumps(st, indent=2))
    return 0 if not st["errors"] else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True

    def common(p):
        p.add_argument("--timeout", type=int, default=1800,
                       help="time limit of each ABC run in seconds (default 1800); reaching it is 'undecided'")
        p.add_argument("--build-timeout", type=int, default=1200,
                       help="time limit of each yosys step in seconds (default 1200)")
        p.add_argument("--jobs", type=int, default=0,
                       help="parallel cases (default $SLURM_CPUS_PER_TASK or 1)")
        p.add_argument("--out", help="output directory (default <repo>/build/formal_eq/<label>)")

    p = sub.add_parser("check", help="equivalence check of one netlist")
    p.add_argument("--netlist", help="gate-level netlist (.v or .v.gz)")
    p.add_argument("--run-dir", help="tt_submission artifact, LibreLane run, sweep/opt run or final/ directory")
    p.add_argument("--variant", help="RTL variant: base or diet4 (default $PE_VARIANT, else base)")
    p.add_argument("--core", help="core RTL file (default: the variant's; $PE_CORE)")
    p.add_argument("--top", help="top RTL file (default src/project.v)")
    p.add_argument("--rtl-from-artifact", action="store_true",
                   help="take project.v and the core from the tt_submission artifact's src/")
    p.add_argument("--liberty", help="standard-cell liberty file (default from the PDK root)")
    p.add_argument("--pdk-root", help="PDK root (default $PE_PDK_ROOT, $PDK_ROOT, else $PE_FLOW_ROOT/pdk)")
    p.add_argument("--pdk", help="PDK name (default from pdk.json, else %s)" % DEFAULT_PDK)
    p.add_argument("--allow-pdk-mismatch", action="store_true",
                   help="run although the PDK revision differs from the artifact's pdk.json")
    p.add_argument("--selftest", action="store_true",
                   help="also run the recipe controls and the two netlist mutants (mutA, mutC)")
    p.add_argument("--label", help="case label (default from the input name)")
    common(p)

    p = sub.add_parser("controls", help="recipe controls only (no PDK needed)")
    common(p)

    p = sub.add_parser("mutate", help="write a mutant netlist")
    p.add_argument("--netlist", required=True)
    p.add_argument("--kind", required=True, choices=["near-output-nand", "sram-din-swap"])
    p.add_argument("--instance", help="SRAM instance for sram-din-swap (default: first by name)")
    p.add_argument("--liberty")
    p.add_argument("--out", required=True)

    p = sub.add_parser("structure", help="structural preconditions and clock depths of a netlist")
    p.add_argument("--netlist")
    p.add_argument("--run-dir")
    p.add_argument("--liberty")
    p.add_argument("--pdk-root")

    args = ap.parse_args(argv)
    try:
        return {"check": cmd_check, "controls": cmd_controls, "mutate": cmd_mutate,
                "structure": cmd_structure}[args.cmd](args)
    except EqError as e:
        print("eq_check: %s" % e, file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - a crash exits 2 (error), never 0 or 1
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
