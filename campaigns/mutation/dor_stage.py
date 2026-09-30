#!/usr/bin/env python3
"""List the WAITEVENT and WAITPIN stages of every engine's blocked-cycle count
in a core, with the RTLIL cells of each stage (docs/extension.md section 11.4).

usage: dor_stage.py CORE BASE_IL

CORE     the core a mutation design was made from (core_orig.v of a
         gen_mutants.sh or gen_line_mutants.sh design directory)
BASE_IL  that design's base.il (the RTLIL the mutations are applied to)

Each engine's blocked-cycle count is a chain of multiplexers, one per opcode
(line_survivor_classes.py, Core.blocked_chain). For the stages of WAITPIN (13)
and WAITEVENT (15) the script prints one tab-separated line: engine, count
register, the stage's output wire, its source line in CORE, the statement, and
the `$ternary` cells of base.il that come from that line
(`$ternary$core_orig.v:LINE$ID`, the names `mutate -cell` takes).

The design-of-record analogues of results/diet8_rec16/dor_waitevent_analogues.tsv
apply the port, port bit and control bit of the line-unit survivors 331, 333,
1438 and 1442 (second sample, WAITEVENT stages) to the WAITEVENT stage cells
of the same engines in the design of record's design (README.md, "Line unit
(diet8_rec16)").
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import line_survivor_classes as lsc  # noqa: E402

STAGES = {13: "WAITPIN", 15: "WAITEVENT"}
ZERO_EXTENDED = re.compile(r"^assign\s+\w+\s*=\s*\{\s*(\w+)\s*,\s*image_length_\d+\s*\}\s*;$", re.S)


class Core(lsc.Core):
    """line_survivor_classes.Core, whose FAULT step also accepts the form of the
    design of record's core: the 24-bit PC is compared with the zero-extended
    16-bit image length, pc < {8'b0, image_length_N} (the diet8 cores compare
    the 7-bit PC with the narrow image length directly)."""

    def is_fault(self, sig: str) -> bool:
        if super().is_fault(sig):
            return True
        e = re.match(r"^assign\s+\w+\s*=\s*(\w+)\s*\|\s*(\w+)\s*;$", self.text(self.resolve(sig)))
        if not e:
            return False
        for operand in e.groups():
            m = lsc.NOT.match(self.text(self.resolve(operand)))
            if not m:
                continue
            c = re.match(r"^assign\s+\w+\s*=\s*pc(_\d+)?\s*<\s*(\w+)\s*;$", self.text(self.resolve(m.group(1))))
            z = ZERO_EXTENDED.match(self.text(self.resolve(c.group(2)))) if c else None
            if z and self.const(z.group(1)) == 0:
                return True
        return False


def stages(core_v: Path, base_il: Path) -> list[tuple]:
    core = Core(core_v)
    chain = core.blocked_chain(core.roles())
    il = base_il.read_text()
    out = []
    for lhs, info in sorted(chain.items(), key=lambda x: (x[1]["engine"], x[1]["opcode"])):
        if info["opcode"] not in STAGES:
            continue
        st = next(s for s in core.stmts
                  if s.kind in ("assign", "comb") and re.match(r"^assign\s+" + lhs + r"\s*=", s.text))
        ids = sorted(set(re.findall(r"^\s*cell \S+ \$ternary\$core_orig\.v:%d\$(\d+)$" % st.first, il, re.M)),
                     key=int)
        cells = ["$ternary$core_orig.v:%d$%s" % (st.first, i) for i in ids]
        out.append((info["engine"], STAGES[info["opcode"]], info["register"], lhs, st.first,
                    st.text.strip(), cells))
    return out


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__.split("\n\n")[1])
    rows = stages(Path(sys.argv[1]), Path(sys.argv[2]))
    print("engine\tstage\tregister\twire\tline\tstatement\tcells")
    for engine, stage, reg, lhs, line, text, cells in rows:
        print(f"{engine}\t{stage}\t{reg}\t{lhs}\t{line}\t{text}\t{' '.join(cells) or '-'}")


if __name__ == "__main__":
    main()
