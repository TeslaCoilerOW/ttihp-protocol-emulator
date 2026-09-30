#!/usr/bin/env python3
"""Classify the survivors of the line-unit campaign (docs/extension.md section 11.4).

usage: line_survivor_classes.py DESIGN --ids-file FILE [--rip DIR] [--abc DIR]
                                [--tsv OUT] [--json OUT]
       line_survivor_classes.py DESIGN --chain-share STATUS_TSV [--json OUT]

DESIGN     a design directory of gen_line_mutants.sh (core_orig.v, mutations.tsv,
           regions.json)
--ids-file the survivors (push_summary.py --write-survivors)
--rip      run_mutant.py results of stage kill-rip (reach/infect/propagate);
           --rip-expected-failure TEST names a test that fails on the wrapper for
           the unmutated core too (mutant 0), so its failure does not make the
           run incomplete
--abc      formal_mutant.py results (miter + ABC); the column shows each id's status
--chain-share  instead of classifying survivors, count every mutant of a
           push_summary.py --status-tsv table by where its mutated statement lies:
           on the blocked-cycle count's opcode chain at the stage of an opcode
           below 30 (the base ISA), at the stage of one of 30-33 (the line unit),
           or off the chain; one row per place and status

Select roles. Most line-unit registers hold their value through a chain of
multiplexers whose selects are engine-control conditions. Those conditions are
located structurally, per engine, on the next-value path of the engine's
`running` register, which the RTL builds as

    STOP ? 0 : START ? 1 : ACTIVE ? (WAIT ? running : XFER ? running : FAULT ? 0 : ...) : running

and each step is checked: STOP selects the constant 0, START the constant 1,
ACTIVE holds `running` on its 0 input, WAIT is ~(wait_timer == 0), XFER is
~(transfer_edges == 0), FAULT selects 0 and is an OR with ~(pc < image_length)
as one operand. STOP is the host's STOP or BEGIN command for the engine, START
its START command, ACTIVE "running, no START/STOP, no fault code, out of
reset". The script stops with an error if the structure differs. The engine of
a role is that of its `running` register (engine_map.py).

A survivor's mutated statement is `assign w = S ? B : A;` for most cells; its
role is the role of S, "other" for another select, and "no select" when the
statement is not a two-way multiplexer. The engine column is the role's engine,
else the engine of the nearest named state when that is one engine. The port
is Yosys's port name (A, B, S, Y, ...) from the mutation.

Classes (the arguments are in docs/extension.md section 11.4; they are
arguments, not proofs):

  blocked count in a line XFER   region line/out-engine_ctrl and the nearest
                                 named state is blocked_cycles only
  fault edge                     role FAULT, port B (the value taken on the fault
                                 edge), or port S tied to 0
  STOP/BEGIN edge                role STOP, port B
  halted or faulted engine       role ACTIVE, port A (the value while not active),
                                 or port S tied to 1
  START value of the fraction    role START, port B, nearest named state line_frac

On the blocked-cycle count's opcode chain (each multiplexer selects one opcode's
next count, B, or passes the later opcodes' value on, A; WAITPIN and WAITEVENT,
opcodes 13 and 15, count; the other opcodes clear the count or hold it):

  blocked count, stages of      ports A, B or Y carrying no WAITPIN/WAITEVENT
  other instructions            value, mode const0 or cnot1; or port S, with the
                                constant 0 on B and no WAITPIN/WAITEVENT value
                                on Y, or tied to 0 at another opcode's stage
  blocked count written by HALT port B of the HALT stage (opcode 1)
  blocked count written at an   port A of the last stage (the hold, which only
  XFER or FAULT issue           XFER and FAULT, the opcodes without a stage,
                                reach)

  neither proven nor argued      everything else

The first five rules were written from the first sample's survivors (line/*),
the chain rules from the second sample's (ext/*); every rule applies to every
sample. The chain rules rest on one property of the unmutated core: a running
engine's count is 0 whenever an instruction other than WAITPIN/WAITEVENT
issues (every completion clears it and only a bounded wait makes it grow; a
halted or faulted engine holds it and START clears it).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine_map  # noqa: E402
import region_map  # noqa: E402
from survivor_classes import family, nearest_states  # noqa: E402

MUX = re.compile(r"^assign\s+(\w+)\s*=\s*(\w+)\s*\?\s*(\w+)\s*:\s*(\w+)\s*;$")
CONST = re.compile(r"^assign\s+\w+\s*=\s*\d+'b([01]+)\s*;$")
NOT = re.compile(r"^assign\s+\w+\s*=\s*~\s*(\w+)\s*;$")
RENAME = re.compile(r"^assign\s+\w+\s*=\s*(\w+)\s*;$")
# docs/isa.md: WAITPIN and WAITEVENT count blocked cycles; HALT stops the engine
WAITING = {13, 15}
HALT_OPCODE = 1


class Core:
    def __init__(self, core: Path):
        _, self.stmts, _, _, self.seq, _ = region_map.parse(core)
        self.define = {}
        for st in self.stmts:
            if st.kind in ("assign", "comb"):
                for x in st.lhs:
                    self.define[x] = st
        self.engine_of = engine_map.engine_finder(self.stmts, self.seq)

    def text(self, sig: str) -> str:
        st = self.define.get(sig)
        return st.text if st is not None else ""

    def resolve(self, sig: str) -> str:
        """Follow renames (assign a = b;) to the signal that computes the value."""
        for _ in range(64):
            m = RENAME.match(self.text(sig))
            if not m:
                return sig
            sig = m.group(1)
        return sig

    def const(self, sig: str) -> int | None:
        m = CONST.match(self.text(self.resolve(sig)))
        return int(m.group(1), 2) if m else None

    def mux(self, sig: str) -> tuple[str, str, str]:
        m = MUX.match(self.text(self.resolve(sig)))
        if not m:
            raise SystemExit(f"line_survivor_classes: {sig}: not a multiplexer: {self.text(self.resolve(sig))!r}")
        return m.group(2), m.group(3), m.group(4)

    def is_nonzero_test(self, sig: str, register_family: str) -> bool:
        """sig = ~(register == 0) for a register of the family"""
        m = NOT.match(self.text(self.resolve(sig)))
        if not m:
            return False
        e = re.match(r"^assign\s+\w+\s*=\s*(\w+)\s*==\s*(\w+)\s*;$", self.text(self.resolve(m.group(1))))
        return bool(e) and family(e.group(1)) == register_family and self.const(e.group(2)) == 0

    def is_fault(self, sig: str) -> bool:
        """sig = a | b with one operand ~(pc < image_length)"""
        e = re.match(r"^assign\s+\w+\s*=\s*(\w+)\s*\|\s*(\w+)\s*;$", self.text(self.resolve(sig)))
        if not e:
            return False
        for operand in e.groups():
            m = NOT.match(self.text(self.resolve(operand)))
            if m and re.match(r"^assign\s+\w+\s*=\s*pc(_\d+)?\s*<\s*image_length_\d+\s*;$",
                              self.text(self.resolve(m.group(1)))):
                return True
        return False

    def roles(self) -> dict[str, tuple[str, int]]:
        """{select net: (role, engine)} from every engine's running register."""
        out = {}
        for reg in sorted(r for r in self.seq if family(r) == "running"):
            st = next(s for s in self.stmts if s.kind == "seq" and reg in s.lhs)
            m = re.search(r"else\s+" + reg + r"\s*<=\s*(\w+)\s*;", st.text)
            if not m:
                raise SystemExit(f"line_survivor_classes: {reg}: no else branch in {st.text[:200]!r}")
            engine = self.engine_of(reg)

            def check(ok: bool, what: str) -> None:
                if not ok:
                    raise SystemExit(f"line_survivor_classes: {reg} (engine {engine}): {what}")

            s, b, a = self.mux(m.group(1))
            check(self.const(b) == 0, "first multiplexer (STOP) does not select 0")
            out[s] = ("STOP", engine)
            s, b, a = self.mux(a)
            check(self.const(b) == 1, "second multiplexer (START) does not select 1")
            out[s] = ("START", engine)
            s, b, a = self.mux(a)
            check(self.resolve(a) == reg, "third multiplexer (ACTIVE) does not hold running on 0")
            out[s] = ("ACTIVE", engine)
            s, b, a = self.mux(b)
            check(self.resolve(b) == reg and self.is_nonzero_test(s, "wait_timer"), "WAIT step")
            out[s] = ("WAIT", engine)
            s, b, a = self.mux(a)
            check(self.resolve(b) == reg and self.is_nonzero_test(s, "transfer_edges"), "XFER step")
            out[s] = ("XFER", engine)
            s, b, a = self.mux(a)
            check(self.const(b) == 0 and self.is_fault(s), "FAULT step")
            out[s] = ("FAULT", engine)
        engines = collections.Counter(e for _, e in out.values())
        if len(out) != 6 * len(engines) or any(n != 6 for n in engines.values()):
            raise SystemExit(f"line_survivor_classes: roles not six per engine: {engines}")
        return out

    def blocked_chain(self, roles: dict[str, tuple[str, int]]) -> dict[str, dict]:
        """{multiplexer output: position} on every engine's blocked-cycle count next-value path:
        STOP ? hold : START ? 0 : ACTIVE ? (WAIT ? hold : XFER ? ... : FAULT ? hold : op==k0 ? v0 :
        op==k1 ? v1 : ... : hold) : hold. For an opcode multiplexer, `opcodes` maps each port (B, A, Y)
        to the opcodes whose value passes through it ("other" for the opcodes no compare matches)."""
        out: dict[str, dict] = {}
        for reg in sorted(r for r in self.seq if family(r) == "blocked_cycles"):
            st = next(x for x in self.stmts if x.kind == "seq" and reg in x.lhs)
            m = re.search(r"else\s+" + reg + r"\s*<=\s*(\w+)\s*;", st.text)
            if not m:
                raise SystemExit(f"line_survivor_classes: {reg}: no else branch")
            sig, steps = self.resolve(m.group(1)), []
            for want in ("STOP", "START", "ACTIVE", "WAIT", "XFER", "FAULT"):
                s, b, a = self.mux(sig)
                if roles.get(s, ("",))[0] != want:
                    raise SystemExit(f"line_survivor_classes: {reg}: expected the {want} select, got {s}")
                sig = self.resolve(b if want == "ACTIVE" else a)
            while MUX.match(self.text(sig)):
                s, b, a = self.mux(sig)
                e = re.match(r"^assign\s+\w+\s*=\s*(\w+)\s*==\s*(\w+)\s*;$", self.text(self.resolve(s)))
                k = self.const(e.group(2)) if e else None
                if k is None:
                    raise SystemExit(f"line_survivor_classes: {reg}: select {s} is not an opcode compare")
                steps.append((sig, s, k, self.const(b)))
                sig = self.resolve(a)
            if self.resolve(sig) != reg:
                raise SystemExit(f"line_survivor_classes: {reg}: the opcode chain does not end in the hold")
            ops = [k for _, _, k, _ in steps]
            if not {1} | WAITING <= set(ops):
                raise SystemExit(f"line_survivor_classes: {reg}: opcodes {sorted(WAITING | {1})} not all in the chain")
            for i, (lhs, s, k, bconst) in enumerate(steps):
                if k in WAITING and bconst is not None:
                    raise SystemExit(f"line_survivor_classes: {reg}: opcode {k} does not count")
                later = set(ops[i + 1:]) | {"other"}
                out[lhs] = {"register": reg, "engine": self.engine_of(reg), "opcode": k, "b_const": bconst,
                            "opcodes": {"B": {k}, "A": later, "Y": {k} | later}}
        return out


def classify(region: str, fams: list[str], role: str, port: str, mode: str, chain: dict | None) -> str:
    if chain is not None:  # a multiplexer of the blocked-cycle count's opcode chain
        through = chain["opcodes"].get(port)
        if through is not None and not through & WAITING and mode in ("const0", "cnot1"):
            return "blocked count, stages of other instructions (argued)"
        if port == "S" and ((chain["b_const"] == 0 and not chain["opcodes"]["Y"] & WAITING)
                            or (mode == "const0" and chain["opcode"] not in WAITING)):
            return "blocked count, stages of other instructions (argued)"
        if port == "A" and chain["opcodes"]["A"] == {"other"}:
            return "blocked count written at an XFER or FAULT issue (argued)"
        if port == "B" and chain["opcode"] == HALT_OPCODE:
            return "blocked count written by HALT (argued)"
    if region == "line/out-engine_ctrl" and fams == ["blocked_cycles"]:
        return "blocked count in a line XFER (argued)"
    if role == "FAULT" and (port == "B" or (port == "S" and mode == "const0")):
        return "fault edge (argued)"
    if role == "STOP" and port == "B":
        return "STOP/BEGIN edge (argued)"
    if role == "ACTIVE" and (port == "A" or (port == "S" and mode == "const1")):
        return "halted or faulted engine (argued)"
    if role == "START" and port == "B" and fams == ["line_frac"]:
        return "START value of the fraction (argued)"
    return "neither proven nor argued"


def mutated_statement(cmd: str, line_of: dict):  # noqa: ANN201 - region_map statement or None
    """The mutated statement: the line in the cell name, else the first -src line inside a
    statement (the other -src lines are wire declarations)."""
    lines = [int(x) for x in re.findall(r"-cell \S*core_orig\.v:(\d+)\$", cmd)]
    lines += [int(x) for x in re.findall(r"-src core_orig\.v:(\d+)\.", cmd)]
    return next((line_of[n] for n in lines if n in line_of), None)


def chain_share(a: argparse.Namespace, muts: dict, line_of: dict, chain: dict) -> None:
    """--chain-share: every mutant of the status table by its place on the blocked-cycle count's
    opcode chain and its status."""
    rows = [r.split("\t") for r in a.chain_share.read_text().splitlines()]
    header, rows = rows[0], rows[1:]
    col = {name: i for i, name in enumerate(header)}
    counts: collections.Counter = collections.Counter()
    for r in rows:
        mid, status = r[col["id"]], r[col["status"]]
        st = mutated_statement(muts[mid][1], line_of)
        m = MUX.match(st.text) if st is not None else None
        stage = chain.get(m.group(1)) if m else None
        if stage is None:
            place = "off the chain"
        elif stage["opcode"] >= 30:
            place = "chain, stage of an opcode 30-33"
        else:
            place = "chain, stage of an opcode below 30"
        counts[(place, status)] += 1
    places = sorted({p for p, _ in counts})
    statuses = sorted({s for _, s in counts})
    table = {p: {s: counts[(p, s)] for s in statuses} for p in places}
    print("\t".join(["place", *statuses, "total"]))
    for p in places:
        print("\t".join([p, *(str(table[p][s]) for s in statuses), str(sum(table[p].values()))]))
    if a.json:
        a.json.write_text(json.dumps({"status_tsv": a.chain_share.name, "mutants": len(rows), "places": table},
                                     indent=1) + "\n")


def rip_outcome(path: Path, expected: set[str]) -> str:
    if not path.exists():
        return "-"
    r = json.loads(path.read_text())
    div = r.get("rip", {}).get("diverged", {})
    if r.get("status") != "ok":
        return r.get("status", "?")
    failed = {t for m in r.get("modules", []) for t in m.get("failed_tests") or []}
    if r.get("tests_failed") and (failed - expected or not failed):
        return f"incomplete: {r['tests_failed']} tests failed on the wrapper"
    if not div:
        return "not infected"
    regs = sorted(k for k in div if not k.startswith("out:"))
    outs = sorted(k for k in div if k.startswith("out:"))
    return "infected: " + ",".join(regs) + (" | output: " + ",".join(outs) if outs else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("design", type=Path)
    ap.add_argument("--ids-file", type=Path)
    ap.add_argument("--chain-share", type=Path, metavar="STATUS_TSV")
    ap.add_argument("--rip", type=Path)
    ap.add_argument("--rip-expected-failure", action="append", default=[], metavar="TEST",
                    help="a test that fails on the wrapper for every mutant and the unmutated core "
                         "(test_can_reset_mid_frame reads core registers through the hierarchy)")
    ap.add_argument("--abc", type=Path)
    ap.add_argument("--tsv", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    if not a.ids_file and not a.chain_share:
        ap.error("--ids-file or --chain-share is required")
    core = Core(a.design / "core_orig.v")
    roles = core.roles()
    chain = core.blocked_chain(roles)
    line_of = {}
    for st in core.stmts:
        for n in range(st.first, st.last + 1):
            line_of[n] = st
    muts = {}
    for row in (a.design / "mutations.tsv").read_text().splitlines():
        mid, region, cmd = row.split("\t", 2)
        muts[mid] = (region, cmd)
    if a.chain_share:
        chain_share(a, muts, line_of, chain)
        return
    ids = a.ids_file.read_text().split()
    near = nearest_states(a.design, ids)
    rows = []
    for mid in ids:
        region, cmd = muts[mid]
        opts = dict(re.findall(r"-(mode|port) (\S+)", cmd))
        mode, port = opts.get("mode", "-"), opts.get("port", "-")
        st = mutated_statement(cmd, line_of)
        m = MUX.match(st.text) if st is not None else None
        role, engine = ("no select", None)
        if m:
            role, engine = roles.get(m.group(2), ("other", None))
        names = near.get(mid, [])
        fams = sorted({family(n) for n in names}) or ["-"]
        if engine is None:
            found = {core.engine_of(n.split("[")[0]) for n in names
                     if n.split("[")[0] in core.seq and not n.startswith("_")}
            found = {e for e in found if isinstance(e, int)}
            engine = found.pop() if len(found) == 1 else None
        in_chain = chain.get(m.group(1)) if m else None
        cls = classify(region, fams, role, port, mode, in_chain)
        rip = rip_outcome(a.rip / f"{mid}.json", set(a.rip_expected_failure)) if a.rip else "-"
        abc = "-"
        if a.abc and (a.abc / f"{mid}.json").exists():
            abc = json.loads((a.abc / f"{mid}.json").read_text()).get("status", "?")
        rows.append({"id": mid, "region": region, "mode": mode, "nearest_state": ",".join(fams),
                     "engine": "-" if engine is None else str(engine), "select_role": role, "port": port,
                     "class": cls, "rip": rip, "miter_abc": abc,
                     "statement": st.text[:160] if st is not None else "-"})
    cols = ["id", "region", "mode", "nearest_state", "engine", "select_role", "port", "class", "rip", "miter_abc"]
    if a.tsv:
        a.tsv.write_text("\t".join(cols) + "\n" + "".join("\t".join(r[c] for c in cols) + "\n" for r in rows))
    counts = collections.Counter(r["class"] for r in rows)
    if a.json:
        a.json.write_text(json.dumps({
            "roles": {s: {"role": r, "engine": e} for s, (r, e) in sorted(roles.items(), key=lambda x: (x[1][1], x[1][0]))},
            "classes": dict(counts.most_common()), "rows": rows}, indent=1) + "\n")
    for cls, n in counts.most_common():
        print(f"{n:5d}  {cls}")


if __name__ == "__main__":
    main()
