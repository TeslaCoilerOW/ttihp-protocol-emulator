#!/usr/bin/env python3
"""Fixed RTL mutants for the formal negative controls.

usage: mutate.py MUTATION ENGINE INPUT.v OUTPUT.v

sram-host-stall
    The memory-enable net of engine ENGINE's two instruction-SRAM macros is
    forced low whenever the host drives its write strobe ui_in[4], so the
    macros skip that cycle's fetch and hold the previous word. This couples the
    engine's instruction fetch, and therefore its pin timing, to host
    activity, as a shared or badly arbitrated program-memory port would.
    The timing-isolation property must produce a counterexample on it.

The mutation is a fixed textual edit of the generated Hardcaml Verilog, never
caller-supplied code, and fails loudly if the expected instances are absent.
"""
import re
import sys


def sram_host_stall(rtl: str, engine: int) -> str:
    wires = set()
    for half in ("lo", "hi"):
        found = re.findall(
            r"RM_IHPSG13_1P_64x16_c2\s+instruction_sram_e%d_%s\s*\([^;]*?\.A_MEN\(([A-Za-z_][A-Za-z_0-9]*)\)"
            % (engine, half), rtl)
        if len(found) != 1:
            raise SystemExit(f"mutate.py: engine {engine} {half} SRAM A_MEN not found")
        wires.add(found[0])
    if len(wires) != 1:
        raise SystemExit("mutate.py: the two SRAM halves use different A_MEN nets")
    wire = wires.pop()
    # Patch the net's driver, not the macro pin, so every reader of the
    # memory-enable net (both macros, the write-enable gate and the formal
    # observation port) sees the same mutated value.
    pattern = re.compile(r"(\n\s*assign %s = )([^;]*);" % re.escape(wire))
    rtl, n = pattern.subn(r"\1(\2) & ~ui_in[4];", rtl)
    if n != 1:
        raise SystemExit(f"mutate.py: expected one driver of {wire}, found {n}")
    return rtl


MUTATIONS = {"sram-host-stall": sram_host_stall}


def main() -> None:
    if len(sys.argv) != 5 or sys.argv[1] not in MUTATIONS:
        raise SystemExit(__doc__)
    name, engine, source, target = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
    with open(source) as f:
        rtl = f.read()
    mutated = MUTATIONS[name](rtl, engine)
    if mutated == rtl:
        raise SystemExit("mutate.py: mutation left the RTL unchanged")
    with open(target, "w") as f:
        f.write(mutated)


if __name__ == "__main__":
    main()
