#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Deliberately timing-coupled cores for the demonstration's negative controls.

usage: mutate.py MUTATION ENGINE INPUT.v OUTPUT.v

host-fetch
    Engine ENGINE's two instruction-SRAM macros skip their fetch in every
    cycle where the host drives write-valid (ui_in[4]); the engine then
    executes the stale word. This is formal/mutate.py's ``sram-host-stall``
    (the formal negative control ``timing_isolation_neg_mutant``); the script
    checks that its output is byte-identical to formal/mutate.py's.
engine-fetch
    The same fetch stall, triggered instead by engine 0's UART TX pin being
    driven low (uio_oe[0] & ~uio_out[0]): coupling from another engine's
    traffic rather than from the host.
timer-host
    Engine ENGINE's WAIT countdown register holds its value in every cycle
    where the host drives write-valid (ui_in[4]): a WAIT lasts one cycle
    longer per host strobe cycle inside it. A pure timing shift (nothing
    else changes), the kind of coupling a shared prescaler or a stall line
    would introduce.
timer-engine
    The same WAIT stretch, triggered by engine 0's UART TX pin being driven low.

The WAIT countdown register of engine ENGINE is found structurally, with the
rule formal/gen/generate_fv.ml uses: an engine register belongs to engine k
when the combinational cone of its next-state logic reads the processor
register image_length_k (and no other image_length).

All four are fixed textual edits of the generated core
(src/protocol_emulator_core.v) that fail loudly if the expected SRAM instances,
registers or net drivers are absent. With the timing probe on ENGINE and
nothing else active, every mutant behaves exactly like the real core; only
the loaded scenario can expose them.
"""

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

HOST_STROBE = "ui_in[4]"
UART_LOW = "(uio_oe[0] & ~uio_out[0])"
CONDITIONS = {
    "host-fetch": HOST_STROBE,
    "engine-fetch": UART_LOW,
    "timer-host": HOST_STROBE,
    "timer-engine": UART_LOW,
}
IDENT = re.compile(r"[A-Za-z_]\w*")


def engine_register(rtl: str, prefix: str, engine: int) -> str:
    """The register named prefix* whose next-state cone reads image_length_<engine> only."""
    assigns = {m.group(1): m.group(2)
               for m in re.finditer(r"\n\s*assign\s+([A-Za-z_]\w*)\s*=\s*([^;]*);", rtl)}
    regs = set(re.findall(r"\n\s*reg\s+(?:\[[^\]]*\]\s*)?([A-Za-z_]\w*)\s*;", rtl))
    blocks = {}
    for m in re.finditer(r"always @\(posedge clk\) begin(.*?)\n    end", rtl, re.S):
        body = m.group(1)
        for target in set(re.findall(r"([A-Za-z_]\w*)\s*<=", body)):
            blocks.setdefault(target, set()).update(IDENT.findall(body))
    owners = {}
    for reg in sorted(r for r in regs if re.fullmatch(prefix + r"(_\d+)?", r)):
        seen, leaves, todo = set(), set(), list(blocks.get(reg, set()) - {reg})
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            if x in regs:
                leaves.add(x)
            elif x in assigns:
                todo.extend(IDENT.findall(assigns[x]))
        lengths = sorted(l for l in leaves if re.fullmatch(r"image_length_\d+", l))
        if len(lengths) != 1:
            raise SystemExit(f"mutate.py: {reg} reads {lengths}, cannot attribute it to one engine")
        owners.setdefault(int(lengths[0].rsplit("_", 1)[1]), []).append(reg)
    if sorted(owners) != list(range(len(owners))) or any(len(v) != 1 for v in owners.values()):
        raise SystemExit(f"mutate.py: {prefix} registers do not map one-to-one to engines: {owners}")
    return owners[engine][0]


def timer_hold(rtl: str, engine: int, condition: str) -> str:
    reg = engine_register(rtl, "wait_timer", engine)
    pattern = re.compile(r"(\n\s*assign ([A-Za-z_]\w*) = )%s - ([A-Za-z_]\w*);" % re.escape(reg))
    rtl, n = pattern.subn(lambda m: "%s%s ? %s : (%s - %s);" % (m.group(1), condition, reg, reg, m.group(3)), rtl)
    if n != 1:
        raise SystemExit(f"mutate.py: expected one decrement of {reg}, found {n}")
    return rtl


def fetch_stall(rtl: str, engine: int, condition: str) -> str:
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
    pattern = re.compile(r"(\n\s*assign %s = )([^;]*);" % re.escape(wire))
    rtl, n = pattern.subn(lambda m: "%s(%s) & ~%s;" % (m.group(1), m.group(2), condition), rtl)
    if n != 1:
        raise SystemExit(f"mutate.py: expected one driver of {wire}, found {n}")
    return rtl


def formal_reference(rtl: str, engine: int) -> str:
    spec = importlib.util.spec_from_file_location("formal_mutate", REPO / "formal" / "mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.sram_host_stall(rtl, engine)


def main() -> None:
    if len(sys.argv) != 5 or sys.argv[1] not in CONDITIONS:
        raise SystemExit(__doc__)
    name, engine, source, target = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
    rtl = Path(source).read_text()
    if name.startswith("timer-"):
        mutated = timer_hold(rtl, engine, CONDITIONS[name])
    else:
        mutated = fetch_stall(rtl, engine, CONDITIONS[name])
    if mutated == rtl:
        raise SystemExit("mutate.py: mutation left the RTL unchanged")
    if name == "host-fetch" and mutated != formal_reference(rtl, engine):
        raise SystemExit("mutate.py: host-fetch differs from formal/mutate.py sram-host-stall")
    Path(target).write_text(mutated)
    print(f"{name} on engine {engine}: {target}")


if __name__ == "__main__":
    main()
