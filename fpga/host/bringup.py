#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Board bring-up and self-measured isolation demo, UART-bridge bitstreams with
the on-board capture unit (bridge protocol 2). fpga/scripts/bringup.sh
programs the board first and then runs this script (docs/fpga.md, "First
hour with a board").

    python3 fpga/host/bringup.py --port /dev/ttyUSB1 --board cmod_a7 --out bringup-out

Steps; the run stops at the first failure:

  1 link       'V' identifies the bitstream (board, clock, capture unit); the
               core clock is measured against the PC clock.
  2 selftest   fpga/host/pe_host.py selftest: ISA version, idle engines, core
               timestamp vs bridge counter, pads drive a5/5a and read back,
               pull-ups, open drain. Nothing may be connected to the protocol
               Pmod.
     (prompt)  fit the two jumper wires (uio0-uio1, uio3-uio4)
  3 loopback   fpga/host/pe_host.py loopback: UART -> jumper -> UART RX ->
               route -> SPI -> jumper -> SPI RX.
  4 capture    capture-unit sanity: stamps and markers against the bridge's
               cycle counter; the timing probe (engine 3) recorded in pad view
               and in core view, each equal to the static prediction frame
               for frame, both at the same frame phase; one capture that
               fills the whole record buffer (every block RAM of it), read
               back and checked the same way; the overflow path.
  5 isolation  the self-measured timing-isolation experiment (demo/pc_demo.py
               scope): idle and loaded phases, frame analysis, verdict.

Everything is written to --out (bringup.json, captures, analyses).
Requires Python 3.8+ and pyserial.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "demo"))

import pe_host  # noqa: E402  (fpga/host/pe_host.py)

sys.modules.setdefault("pe_bridge", pe_host)   # pc_demo loads the bridge module under this name
import pe_scope  # noqa: E402
import pc_demo  # noqa: E402
import pe_capture  # noqa: E402
import pe_demo  # noqa: E402

BOARDS = {
    "cmod_a7": {"id": 1, "name": "Cmod A7-35T",
                "jumpers": "Pmod JA pin 1 to pin 2 (uio0-uio1) and pin 4 to pin 7 (uio3-uio4)"},
    "urbana": {"id": 2, "name": "Urbana",
               "jumpers": "PMOD A pin 1 to pin 2 (uio0-uio1) and pin 4 to pin 7 (uio3-uio4)"},
}
PROBE = pe_scope.channel_mask("uio6,uio7")
PROBE_PERIOD = 344


class StepFailure(Exception):
    pass


def check(cond: bool, message: str) -> None:
    if not cond:
        raise StepFailure(message)


# ------------------------------------------------------------------- step 1
def link(bridge, board: str, log, sleep, now, measure_s: float) -> dict:
    v = bridge.version()
    log(f"bridge: {v.describe()}")
    check(v.board == BOARDS[board]["id"], f"bitstream is for board id {v.board}, not {board}")
    check(v.has_scope, "bitstream has no capture unit (bridge protocol 1): use the scope bitstreams")
    out = {"version": v.__dict__}
    if measure_s > 0:
        c0, t0 = bridge.cycles(), now()
        sleep(measure_s)
        c1, t1 = bridge.cycles(), now()
        mhz = ((c1 - c0) & 0xFFFFFFFF) / (t1 - t0) / 1e6
        out["measured_mhz"] = mhz
        log(f"core clock: {mhz:.3f} MHz measured over {t1 - t0:.3g} s (nominal {v.clk_hz / 1e6:g} MHz)")
        check(abs(mhz / (v.clk_hz / 1e6) - 1) < 0.02,
              f"core clock {mhz:.3f} MHz is not within 2 % of {v.clk_hz / 1e6:g} MHz (MMCM? try the osc12 bitstream)")
    st = pe_scope.Scope(bridge).status()
    log(st.describe())
    out["scope"] = st.__dict__
    return out


# ------------------------------------------------------------------- step 4
def _probe_image():
    img = pe_host.load_image("timing-probe", firmware=REPO / "demo" / "firmware")
    return img


def _analyze(cap, out: Path, name: str, pred: Path, log) -> dict:
    vcd = out / f"{name}.vcd"
    cap.save(out / f"{name}.json")
    cap.write_vcd(vcd)
    rc = pe_capture.main(["analyze", str(vcd), "--channels", pc_demo.PROBE_CHANNELS, "--reference", str(pred),
                          "--label", name, "--json", str(out / f"{name}.analysis.json"), "--quiet"])
    check(rc == 0, f"{name}: capture analysis failed")
    rep = json.loads((out / f"{name}.analysis.json").read_text())
    fr, jit = rep.get("frames") or {}, rep.get("jitter") or {}
    log(f"{name}: {len(cap.records)} records, {cap.window()[1] - cap.window()[0] + 1} cycles, "
        f"frames {fr.get('frames_identical')}/{fr.get('frames_complete')} identical to the prediction, "
        f"jitter {jit.get('max_peak_to_peak_cycles')} cycles")
    check(fr.get("frames_complete", 0) > 0 and fr["frames_identical"] == fr["frames_complete"]
          and not fr.get("grid_departure"), f"{name}: probe frames differ from the static prediction")
    return {"records": len(cap.records), "frames": fr.get("frames_complete"),
            "identical": fr.get("frames_identical"), "jitter": jit.get("max_peak_to_peak_cycles")}


def _wait(scope, sleep, poll_s: float, timeout_s: float, until: Callable) -> pe_scope.Status:
    waited = 0.0
    while True:
        st = scope.status()
        if until(st) or waited > timeout_s:
            return st
        sleep(poll_s)
        waited += poll_s


def capture_checks(chip, fclk: float, out: Path, log, sleep, limit: int = 4096,
                   markers: int = 2, full_limit: int = 0) -> dict:
    b = chip.b
    scope = pe_scope.Scope(b, log)
    res = {}
    # (a) stamps: no channel enabled -> start record, markers, end record.
    c0 = b.cycles()
    scope.arm(0, 0)
    c1 = b.cycles()
    st = scope.status()
    first_mark = (st.start_stamp // pe_scope.MARK_INTERVAL + 1) * pe_scope.MARK_INTERVAL
    target = first_mark + (markers - 1) * pe_scope.MARK_INTERVAL + 16
    st = _wait(scope, sleep, 0.05, 10 + 4 * markers * pe_scope.MARK_INTERVAL / fclk, lambda s: s.now >= target)
    scope.halt()
    cap = scope.collect(fclk)
    kinds = [r.kind for r in cap.records]
    marks = [r.stamp for r in cap.records if r.kind == pe_scope.K_MARK]
    start = cap.records[0].stamp
    log(f"stamps: start {start} between bridge counter readings {c0}..{c1} (mod 2^32); "
        f"markers: {len(marks)}, every {pe_scope.MARK_INTERVAL} cycles")
    check(kinds[0] == pe_scope.K_START and kinds[-1] == pe_scope.K_END
          and set(kinds[1:-1]) <= {pe_scope.K_MARK}, f"unexpected records {kinds[:8]}")
    check((start - c0) % (1 << 32) <= (c1 - c0) % (1 << 32), "start stamp outside the bridge counter window")
    check(len(marks) >= markers and all(m % pe_scope.MARK_INTERVAL == 0 for m in marks)
          and all(y - x == pe_scope.MARK_INTERVAL for x, y in zip(marks, marks[1:])), f"markers {marks}")
    res["stamps"] = {"start": start, "counter": [c0, c1], "markers": len(marks)}
    # (b, c) the timing probe in pad view and in core view
    pred = out / "predicted.json"
    check(pe_capture.main(["predict", "--image", str(REPO / "demo" / "firmware" / "timing-probe.image.json"),
                           "--channels", "probe6=6,probe7=7", "--json", str(pred)]) == 0, "prediction failed")
    chip.reset()
    img = _probe_image()
    chip.load(img["engine"], img["words"], ownership=img["owned_pins"], open_drain=img["open_drain"])
    scope.arm(PROBE, PROBE, wait=True, limit=limit)
    chip.command(pe_host.START, 1 << img["engine"])
    _wait(scope, sleep, 0.02, 30, lambda s: s.finished)
    pad = scope.collect(fclk, {"check": "probe pad view"})
    res["pad_view"] = _analyze(pad, out, "check-pad", pred, log)
    scope.arm(PROBE, 0, core_view=True, limit=limit)
    _wait(scope, sleep, 0.02, 30, lambda s: s.finished)
    core = scope.collect(fclk, {"check": "probe core view"})
    res["core_view"] = _analyze(core, out, "check-core", pred, log)
    phase = lambda c: sorted({(r.stamp % PROBE_PERIOD, r.mask, r.values & PROBE)  # noqa: E731
                              for r in c.records[1:-1] if r.mask})
    same_phase = phase(pad) == phase(core)
    log("pad and core views: " + ("same frame phase (the pad path settles within one clock period)"
                                  if same_phase else "DIFFERENT frame phase"))
    check(same_phase, "the pad view is not at the core view's cycle: the pad input path takes more than one "
          "clock period, or something drives the probe pins (uio6/uio7)")
    # (d) the whole buffer: every record slot, so every block RAM, written and read back
    scope.arm(PROBE, 0, limit=full_limit)
    _wait(scope, sleep, 0.02, 60, lambda s: s.finished)
    full = scope.collect(fclk, {"check": "whole buffer"})
    want = full_limit or full.status.depth
    res["full_buffer"] = _analyze(full, out, "check-full", pred, log)
    rams = full.status.depth * 72 // 36864
    log(f"whole buffer: {len(full.records)} of {full.status.depth} records written and read back"
        + (f" (all {rams} RAMB36)" if not full_limit else f" (limit {full_limit} in this run)"))
    check(len(full.records) == want and full.records[-1].kind == pe_scope.K_END,
          f"whole-buffer capture holds {len(full.records)} records, expected {want}")
    # (e) overflow
    scope.arm(PROBE, 0, limit=64)
    full = _wait(scope, sleep, 0.02, 30, lambda s: s.finished)
    sleep(0.01)
    later = scope.status()
    ovf = scope.collect(fclk)
    log(f"overflow: state {full.state_name}, {full.records} records, end record last, "
        f"{later.lost} changes counted as lost after it")
    check(full.state == 3 and full.full and full.records == 64 and ovf.records[-1].kind == pe_scope.K_END
          and later.lost > 0, f"overflow path: {full.describe()} / {later.describe()}")
    res["overflow"] = {"records": full.records, "lost": later.lost}
    chip.command(pe_host.STOP, 1 << img["engine"])
    chip.reset()
    res["crc_retries"] = scope.crc_retries
    return res


# ------------------------------------------------------------------- driver
def run(bridge, board: str, out: Path, *, log=print, prompt: Callable[[str], None] | None = None,
        sleep=time.sleep, now=time.monotonic, measure_s: float = 1.0, skip=(), check_limit: int = 4096,
        markers: int = 2, full_limit: int = 0, captures: int = 4, limit: int = 0, seed: int = 1,
        max_seconds: float = 60.0, expect=("same", "same")) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    chip = pe_host.Chip(bridge)
    steps = {}
    report = {"board": board, "steps": steps, "pass": False}
    fclk = None

    def step(name: str, fn):
        if name in skip:
            steps[name] = {"result": "SKIPPED"}
            log(f"[{name}] SKIPPED")
            return None
        log(f"[{name}]")
        t0 = now()
        try:
            detail = fn()
        except (StepFailure, pe_host.CheckFailure, pe_host.BridgeError, pe_scope.ScopeError,
                pe_demo.DemoError) as err:
            steps[name] = {"result": "FAIL", "error": str(err)}
            log(f"[{name}] FAIL: {err}")
            raise StepFailure(name)
        steps[name] = {"result": "PASS", "seconds": round(now() - t0, 2), "detail": detail}
        log(f"[{name}] PASS")
        return detail

    try:
        info = step("link", lambda: link(bridge, board, log, sleep, now, measure_s))
        fclk = (info or {}).get("version", {}).get("clk_hz") or bridge.version().clk_hz
        step("selftest", lambda: pe_host.selftest(chip, log))
        if prompt is not None:
            prompt(f"Fit the two jumper wires: {BOARDS[board]['jumpers']}. Nothing else on the Pmod.")
        step("loopback", lambda: pe_host.loopback(chip, log))
        step("capture", lambda: capture_checks(chip, fclk, out, log, sleep, check_limit, markers, full_limit))

        def isolation():
            images = pc_demo.bridge_images(pe_host, pe_demo.ISOLATION_IMAGES)
            adapter = pc_demo.BridgeChip(chip, pe_host.BridgeError, fclk, sleep=sleep)
            phases, runs = {}, {}
            for mode in ("idle", "loaded"):
                log(f"--- {mode} phase")
                result, caps = pc_demo.scope_measure(adapter, bridge, images, mode, fclk_hz=fclk,
                                                     captures=captures, limit=limit, seed=seed,
                                                     max_cycles=int(max_seconds * fclk), log=log)
                check(result["probe_running"], f"probe engine stopped in the {mode} phase")
                phases[mode] = caps
                runs[mode] = {k: v for k, v in result.items() if not k.startswith("engines_")}
            (out / "isolation-runs.json").write_text(json.dumps(runs, indent=1) + "\n")
            summary = pc_demo.scope_report(phases, out / "isolation", expect=expect, log=log)
            check(summary["pass"], "; ".join(summary["reasons"]) or "verdict")
            return {"frames": summary["frames"], "jitter": summary["jitter_pp_cycles"],
                    "load_evidence": summary["load_evidence"],
                    "host_operations": runs.get("loaded", {}).get("host_operations")}

        step("isolation", isolation)
        report["pass"] = True
    except StepFailure as err:
        report["failed_step"] = str(err)
    (out / "bringup.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    log("")
    for name in ("link", "selftest", "loopback", "capture", "isolation"):
        log(f"  {name:<10} {steps.get(name, {}).get('result', 'NOT RUN')}")
    log("BRING-UP " + ("PASS" if report["pass"] else "FAIL"))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="the board's UART (second USB serial port)")
    ap.add_argument("--board", required=True, choices=sorted(BOARDS))
    ap.add_argument("--out", default="bringup-out", help="output directory")
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--yes", action="store_true", help="do not wait for Enter at the jumper prompt "
                    "(jumpers already fitted; then use --skip selftest)")
    ap.add_argument("--skip", default="", help="comma-separated steps to skip (e.g. selftest)")
    ap.add_argument("--captures", type=int, default=4, help="captures per isolation phase")
    ap.add_argument("--limit", type=int, default=0, help="records per isolation capture (0 = whole buffer)")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    try:
        bridge = pe_host.Bridge(pe_host.SerialTransport(a.port, a.baud))
    except Exception as err:  # pyserial missing, no such port, permission denied
        print(f"FAIL: cannot open {a.port}: {err}\n(the board's UART is the second of its two USB serial "
              "ports; on Linux check the udev rules or the dialout group)", file=sys.stderr)
        return 2

    def prompt(text):
        print("\n>>> " + text)
        if not a.yes:
            input(">>> press Enter to continue ")

    skip = tuple(s for s in a.skip.split(",") if s)
    report = run(bridge, a.board, Path(a.out), prompt=prompt, skip=skip, captures=a.captures, limit=a.limit,
                 seed=a.seed)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
