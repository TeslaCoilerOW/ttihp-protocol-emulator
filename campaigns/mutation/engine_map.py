#!/usr/bin/env python3
"""Map per-engine registers of the generated core to engine indices.

usage: engine_map.py CORE.v   -> prints JSON {register: engine}

Hardcaml de-duplicates the per-engine register names (pc, pc_0, pc_1, pc_2, ...)
in elaboration order, which is not the engine order. A register belongs to the
engine whose instruction SRAM (instruction_sram_e<k>_*) it depends on: a
backward walk from the register through combinational logic and anonymous
registers, stopping at named registers, reaches one SRAM instance first.
Processor-level registers (image_*_k, ownership_k, mailbox_k, route_*_k, ...)
are named with the engine index by processor.ml and need no mapping.
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import region_map  # noqa: E402

FAMILIES = ["pc", "running", "fault_code", "tx", "rx", "x", "y", "repeat_count", "wait_timer", "wait_limit",
            "blocked_cycles", "logical_output", "logical_enable", "transfer_pins", "completed_instructions",
            "transfer_edges", "transfer_tick", "transfer_period", "transfer_mode"]


def main() -> None:
    _, stmts, _, _, seq, _ = region_map.parse(Path(sys.argv[1]))
    drivers = {}
    for st in stmts:
        for x in st.lhs:
            drivers.setdefault(x, st)

    def engine_of(sig):
        seen, frontier = {sig}, [sig]
        while frontier:
            nxt, hits = [], collections.Counter()
            for s in frontier:
                st = drivers.get(s)
                if st is None:
                    continue
                if st.kind == "inst":
                    inst = next(x for x in st.lhs if x.startswith("inst:"))
                    hits[int(re.search(r"_e(\d)_", inst).group(1))] += 1
                    continue
                for r in st.rhs:
                    if r not in seen and not (r in seq and not r.startswith("_")):
                        seen.add(r)
                        nxt.append(r)
            if hits:
                return hits.most_common(1)[0][0] if len(hits) == 1 else dict(hits)
            frontier = nxt
        return None

    out = {}
    for fam in FAMILIES:
        for suffix in ("", "_0", "_1", "_2"):
            out[fam + suffix] = engine_of(fam + suffix)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
