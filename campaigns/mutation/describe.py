#!/usr/bin/env python3
"""Describe mutants in source terms (for survivor classification).

usage: describe.py DESIGN_DIR ID [ID ...]      (or --ids-file FILE)

For each mutant prints the mutation (mode, cell, port, bit), its region, the
Hardcaml statement(s) named by the cell's src tags, and the nearest named
state the statement feeds (from regions.json).
"""
import json
import re
import sys
from pathlib import Path

MODES = {
    "inv": "bit inverted",
    "const0": "bit tied to 0",
    "const1": "bit tied to 1",
    "cnot0": "bit inverted when ctrl bit (another bit of the same port) is 0: bit XNOR ctrl",
    "cnot1": "bit inverted when ctrl bit (another bit of the same port) is 1: bit XOR ctrl",
}


def cone_paths(core: Path):
    """Statement index and consumer map from region_map's parser."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import region_map
    _, stmts, _, memories, seq_regs, outputs = region_map.parse(core)
    by_lhs, consumers = {}, {}
    for st in stmts:
        for x in st.lhs:
            by_lhs.setdefault(x, st)
        for r in st.rhs:
            consumers.setdefault(r, set()).update(st.lhs)

    def is_anchor(sig):
        return (sig.startswith("inst:") or sig in memories or sig in outputs
                or (sig in seq_regs and not sig.startswith("_")))

    def path(start, limit=10):
        prev, frontier = {start: None}, [start]
        while frontier:
            nxt = []
            for sig in frontier:
                for c in sorted(consumers.get(sig, ())):
                    if c in prev:
                        continue
                    prev[c] = sig
                    if is_anchor(c):
                        chain = [c]
                        while prev[chain[-1]] is not None:
                            chain.append(prev[chain[-1]])
                        return list(reversed(chain))[:limit]
                    nxt.append(c)
            frontier = nxt
        return []
    return by_lhs, path


def main() -> None:
    design = Path(sys.argv[1])
    show_path = "--path" in sys.argv
    sys.argv = [a for a in sys.argv if a != "--path"]
    args = sys.argv[2:]
    if args[:1] == ["--ids-file"]:
        args = Path(args[1]).read_text().split()
    lines = (design / "core_orig.v").read_text().splitlines()
    regions = json.loads((design / "regions.json").read_text())
    line_region = regions["line_region"]
    anchors = regions["line_anchor"]
    by_lhs, path = cone_paths(design / "core_orig.v") if show_path else (None, None)
    muts = {}
    for row in (design / "mutations.tsv").read_text().splitlines():
        mid, region, cmd = row.split("\t", 2)
        muts[mid] = (region, cmd)
    for mid in args:
        region, cmd = muts[mid]
        opts = dict(re.findall(r"-(mode|cell|port|portbit|ctrlbit|wire|wirebit) (\S+)", cmd))
        srcs = [int(x) for x in re.findall(r"-src core_orig\.v:(\d+)\.", cmd)]
        m = re.search(r"core_orig\.v:(\d+)\$", opts.get("cell", ""))
        if m:
            srcs.append(int(m.group(1)))
        print(f"== mutant {mid} [{region}] {opts.get('mode')} ({MODES.get(opts.get('mode'), '')}) cell={opts.get('cell')} port={opts.get('port')}"
              f"[{opts.get('portbit')}] ctrlbit={opts.get('ctrlbit', '-')} wire={opts.get('wire', '-')}"
              f"[{opts.get('wirebit', '-')}]")
        for ln in sorted(set(srcs)):
            if str(ln) not in line_region:
                continue  # the declaration of the mutated wire
            # show the statement start for lines inside always blocks
            start = ln
            while start > 1 and str(start) not in anchors and ln - start < 80:
                start -= 1
            text = lines[ln - 1].strip()
            head = lines[start - 1].strip() if start != ln else ""
            print(f"   L{ln} ({line_region.get(str(ln), '?')}): {text[:150]}")
            if head:
                print(f"      in statement at L{start}: {head[:120]}")
            if str(start) in anchors:
                print(f"      nearest named state: {anchors[str(start)]}")
            if show_path:
                lhs = re.match(r"assign (\w+)", text)
                sig = lhs.group(1) if lhs else None
                if sig is None:
                    m2 = re.match(r"(\w+)\s*(\[[^]]*\])?\s*<=", text)
                    sig = m2.group(1) if m2 else None
                for hop in (path(sig) if sig else [])[1:]:
                    st = by_lhs.get(hop)
                    shown = st.text if st is not None and st.kind == "assign" else (
                        f"(register/case/instance {hop})" if st is None or st.kind != "assign" else "")
                    print(f"        -> {shown[:160]}")


if __name__ == "__main__":
    main()
