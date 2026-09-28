#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Compare on-board captures with the simulation VCD of the same run.

usage: scope_vs_vcd.py SIM_VCD[.gz] CAPTURE.json... [--json OUT.json]

Ground truth is the VCD written by demo_dump.v in the bridge testbench
(tb_bridge.clk, tb_bridge.pads, the core's ui_in/uo_out, and the capture
unit's start_stamp register), read here by its own streaming parser (not
pe_capture.py's reader). Cycle numbers: a value change at time t belongs to
clock edge n = the number of rising edges of tb_bridge.clk at or before t,
and a signal's level in cycle n is its last value before edge n + 1 (the
value the capture unit samples). A channel has an edge at cycle n when its
level in cycle n differs from its level in cycle n - 1.

For every capture (fpga/host/pe_scope.py JSON) and every enabled channel, the
edges inside the capture window [start, end] must equal the VCD's edges,
cycle for cycle, and the level before the window must equal the capture's
initial level. The stamp origin is checked independently of the records:
the start_stamp register takes the value S (the start record's stamp) at
rising edge S + 3 (pe_fpga_scope.v: sampled at S + 1, compared at S + 2,
written at S + 3).
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
from bisect import bisect_right
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SIGNALS = {
    "clk": "tb_bridge.clk",
    "pad": "tb_bridge.pads",
    "ui": "tb_bridge.dut.shell.tt.ui_in",
    "uo": "tb_bridge.dut.shell.tt.uo_out",
    "start_stamp": "tb_bridge.dut.shell.g_bridge.g_scope.scope.start_stamp",
}


def load_scope():
    if "pe_scope" in sys.modules:
        return sys.modules["pe_scope"]
    spec = importlib.util.spec_from_file_location("pe_scope", REPO / "fpga" / "host" / "pe_scope.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pe_scope"] = mod
    spec.loader.exec_module(mod)
    return mod


def _value(text: str):
    return int(text, 2) if all(c in "01" for c in text) else None


def parse_vcd(path: Path) -> dict:
    """Stream the VCD: rising-edge times of clk and, per wanted signal, its
    changes as (edge count at the change, value; None if x/z)."""
    opener = gzip.open if str(path).endswith(".gz") else open
    ids: dict[str, list[str]] = {}
    scopes: list[str] = []
    changes = {key: [] for key in SIGNALS if key != "clk"}
    by_name = {v: k for k, v in SIGNALS.items()}
    edges = 0
    edge_times: list[int] = []
    clk_level = None
    t = 0
    pending: dict[str, object] = {}

    def flush() -> None:
        nonlocal edges, clk_level
        if "clk" in pending:
            v = pending.pop("clk")
            if v == 1 and clk_level == 0:
                edges += 1
                edge_times.append(t)
            clk_level = v
        for key, v in pending.items():
            changes[key].append((edges, v))
        pending.clear()

    with opener(path, "rt") as fh:
        for line in fh:                                  # header
            tok = line.split()
            if not tok:
                continue
            if tok[0] == "$scope":
                scopes.append(tok[2])
            elif tok[0] == "$upscope":
                scopes.pop()
            elif tok[0] == "$var":
                ref = tok[4]
                name = ".".join(scopes + [ref.split("[")[0]])
                if name in by_name:
                    ids.setdefault(tok[3], []).append(by_name[name])
            elif tok[0] == "$enddefinitions":
                break
        missing = set(SIGNALS) - {k for keys in ids.values() for k in keys}
        if missing:
            raise SystemExit(f"{path}: signals not in the VCD: {sorted(SIGNALS[k] for k in missing)}")
        for line in fh:                                  # value changes
            if not line or line[0] == "$":
                continue
            c = line[0]
            if c == "#":
                flush()
                t = int(line[1:])
                continue
            if c in "bB":
                value, ident = line[1:].split()
                v = _value(value)
            elif c in "01xXzZ":
                ident = line[1:].strip()
                v = int(c) if c in "01" else None
            else:
                continue
            for key in ids.get(ident, ()):
                pending[key] = v
        flush()
    return {"edges": edges, "edge_times": edge_times, "changes": changes}


def channel_edges(changes: list, bit: int, lo: int, hi: int) -> tuple[int | None, list]:
    """Level of one bit before cycle lo, and its edges (cycle, level) in [lo, hi]."""
    per_cycle: dict[int, int | None] = {}
    for cyc, v in changes:                      # last value in each cycle
        per_cycle[cyc] = None if v is None else v >> bit & 1
    level = None
    out = []
    initial = None
    for cyc in sorted(per_cycle):
        if cyc > hi:
            break
        lvl = per_cycle[cyc]
        if cyc < lo:
            level = lvl
            continue
        if initial is None:
            initial = level
        if lvl is None:
            raise SystemExit(f"bit {bit}: x/z inside the window at cycle {cyc}")
        if lvl != level:
            out.append((cyc, lvl))
        level = lvl
    return (level if initial is None else initial), out


def compare(vcd: dict, cap, name: str) -> dict:
    sc = load_scope()
    lo, hi = cap.window()
    ss = vcd["changes"]["start_stamp"]
    # The start_stamp register changes to the start stamp at edge start + 3.
    hits = [n for n, v in ss if v == cap.status.start_stamp]
    origin_ok = bool(hits) and hits[-1] == cap.status.start_stamp + 3
    result = {"capture": name, "window": [lo, hi], "records": len(cap.records), "origin_ok": origin_ok,
              "start_stamp_edge": hits[-1] if hits else None, "channels": {}, "ok": origin_ok}
    view = "pad"
    if cap.status.core_view:
        raise SystemExit(f"{name}: core-view captures are compared in fpga/sim (the VCD has no uio_out/oe)")
    for ch in cap.enabled:
        key, bit = (view, ch) if ch < 8 else (("uo", ch - 8) if ch < 16 else ("ui", ch - 16))
        initial, want = channel_edges(vcd["changes"][key], bit, lo, hi)
        got = cap.edges(ch)
        same = got == want and initial == cap.initial(ch)
        entry = {"edges": len(got), "vcd_edges": len(want), "identical": same}
        if not same:
            k = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
            entry["first_difference"] = {"index": k, "scope": got[k:k + 3], "vcd": want[k:k + 3],
                                         "initial": [cap.initial(ch), initial]}
        result["channels"][sc.CHANNELS[ch]] = entry
        result["ok"] &= same
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vcd")
    ap.add_argument("captures", nargs="+")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    sc = load_scope()
    vcd = parse_vcd(Path(a.vcd))
    results = []
    for path in a.captures:
        cap = sc.Capture.load(path)
        if not cap.records:
            results.append({"capture": Path(path).name, "records": 0, "ok": True, "note": "empty"})
            continue
        results.append(compare(vcd, cap, Path(path).name))
    ok = all(r["ok"] for r in results)
    report = {"vcd": Path(a.vcd).name, "clock_edges": vcd["edges"], "captures": results, "pass": ok}
    for r in results:
        chans = ", ".join(f"{k} {v['edges']}{'' if v['identical'] else ' DIFFERENT'}"
                          for k, v in r.get("channels", {}).items())
        print(f"{r['capture']}: window {r.get('window')}, {r['records']} records, origin "
              f"{'ok' if r.get('origin_ok', True) else 'WRONG'}; edges {chans}")
    print("SCOPE-VS-VCD " + ("PASS" if ok else "FAIL"))
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
