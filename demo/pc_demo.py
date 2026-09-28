#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""PC side of the hardware demonstration (docs/demo.md), UART-bridge builds.

Drives the FPGA prototype through fpga/host/pe_host.py (the bridge tool; it
is loaded from its file under the module name ``pe_bridge`` because host/
also provides a package called ``pe_host``) and runs the scenarios of
demo/pe_demo.py.

    python3 demo/pc_demo.py --port /dev/ttyUSB1 isolation --mode idle   --seconds 5 --json idle.json
    python3 demo/pc_demo.py --port /dev/ttyUSB1 isolation --mode loaded --seconds 5 --json loaded.json
    python3 demo/pc_demo.py --port /dev/ttyUSB1 four   --uart /dev/ttyUSB2 --rounds 20
    python3 demo/pc_demo.py --port /dev/ttyUSB1 bridge --uart /dev/ttyUSB2 --seconds 10
    python3 demo/pc_demo.py --port /dev/ttyUSB1 scope --out results/ --captures 4

``scope`` is the self-measured timing-isolation experiment (docs/demo.md,
"Self-measured isolation"): the idle and loaded phases of ``isolation``, with
the probe pins recorded by the bitstream's on-board capture unit
(fpga/host/pe_scope.py; bridge protocol 2) instead of an external logic
analyser, then the frame analysis and comparison of pe_capture.py and a
verdict. It needs the two loopback jumpers and nothing else.

--port is the FPGA board's bridge serial port (Cmod A7 and Urbana: the second
of the two ports). --uart is a separate USB-UART adapter wired to the chip's
UART pins (four, bridge). Requires Python 3.8+ and pyserial.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import pe_demo  # noqa: E402

FIRMWARE_DIRS = (HERE / "firmware", REPO / "firmware")


def load_scope_module(path: Path = REPO / "fpga" / "host" / "pe_scope.py"):
    """fpga/host/pe_scope.py (the on-board capture unit client)."""
    if "pe_scope" in sys.modules:
        return sys.modules["pe_scope"]
    spec = importlib.util.spec_from_file_location("pe_scope", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pe_scope"] = module
    spec.loader.exec_module(module)
    return module


def load_bridge_module(path: Path = REPO / "fpga" / "host" / "pe_host.py"):
    """fpga/host/pe_host.py under the name pe_bridge (no clash with host/pe_host)."""
    if "pe_bridge" in sys.modules:
        return sys.modules["pe_bridge"]
    spec = importlib.util.spec_from_file_location("pe_bridge", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pe_bridge"] = module
    spec.loader.exec_module(module)
    return module


def bridge_images(bridge_mod, names, directories=FIRMWARE_DIRS) -> dict:
    """Images with the bridge tool's identity checks (source and bytecode SHA-256)."""
    out = {}
    for name in names:
        for directory in directories:
            if (Path(directory) / f"{name}.image.json").exists():
                img = bridge_mod.load_image(name, firmware=Path(directory))
                out[name] = pe_demo.ImageRef(name, img["engine"], img["words"], img["owned_pins"],
                                             img["open_drain"])
                break
        else:
            raise pe_demo.DemoError(f"image {name} not found in {[str(d) for d in directories]}")
    return out


class BridgeChip:
    """pe_demo chip interface over the UART bridge. The core clock free-runs,
    so ``now`` reads the bridge's cycle counter and ``wait`` sleeps."""

    host_clocked = False

    def __init__(self, chip, bridge_error, clk_hz: float, sleep=time.sleep, try_limit: int = 64):
        self.chip = chip
        self.b = chip.b
        self.error = bridge_error
        self.clk_hz = clk_hz
        self.sleep = sleep
        self.try_limit = try_limit
        self._limit = None
        self._last = None
        self._high = 0

    def _set_limit(self, cycles: int) -> None:
        if self._limit != cycles:
            self.b.set_limit(cycles)
            self._limit = cycles

    def reset(self):
        self._set_limit(0)
        self.chip.reset()

    def isa(self):
        return self.chip.status(pe_demo.RS_VERSION)

    def load(self, engine, image):
        self.chip.load(engine, image.words, ownership=image.owned_pins, open_drain=image.open_drain)

    def command(self, op, payload=0):
        self.chip.command(op, payload)
        return None                     # acceptance is not reported by the bridge

    def select(self, engine):
        self.chip.command(pe_demo.SELECT, engine)

    def read_select(self, index, engine):
        self.select(engine)
        return self.chip.status(index)

    def levels(self, engine):
        word = self.read_select(pe_demo.RS_LEVELS, engine)
        return word & 0xFFFF, word >> 16

    def status(self, engine):
        return self.read_select(pe_demo.RS_STATUS, engine)

    def tx_write(self, engine, words):
        self._set_limit(0)
        self.chip.push_tx(engine, list(words))

    def tx_try(self, engine, word, max_wait):
        self._set_limit(max(1, max_wait) + self.try_limit)
        self.select(engine)
        try:
            self.b.write(pe_demo.W_TX, word)
            return True
        except self.error:
            return False

    def rx_read(self, engine, count):
        self._set_limit(0)
        return self.chip.pop_rx(engine, count) if count else []

    def rx_try(self, engine, max_wait):
        self._set_limit(max(1, max_wait) + self.try_limit)
        self.select(engine)
        try:
            return self.b.read(pe_demo.W_RX)
        except self.error:
            return None

    def now(self):
        value = self.b.cycles()
        if self._last is not None and value < self._last:
            self._high += 1 << 32
        self._last = value
        return self._high + value

    def wait(self, cycles):
        self.sleep(cycles / self.clk_hz)


# ------------------------------------------------------ on-board capture
PROBE_CHANNELS = "probe6=uio6,probe7=uio7"
# Load evidence: activity counters of these channels inside the capture
# windows (engine 0 UART TX -> jumper -> engine 1 RX; engine 2 SPI SCK and
# MOSI -> jumper -> MISO; host write-valid / read-ready strobes).
LOAD_CHANNELS = ("uio0", "uio1", "uio2", "uio3", "uio4", "ui4", "ui5")
LOAD_REQUIRED = ("uio0", "uio2", "ui4")


def burst_frames(bridge_mod, rng: "pe_demo.Rng", rounds: int = 3) -> list:
    """Pipelined host-port operations that never fail and never touch the
    probe's program or run state: status reads with a fresh snapshot of a
    random engine, and EVENTs to random engine masks (pe_demo.Hammer's
    'readsel' and 'event'). Sent in the same USB write as the ARM frame, so
    the bridge executes them back to back inside the capture window."""
    frames = []
    for _ in range(rounds):
        engine = rng.below(4)
        frames += [bridge_mod.Bridge.write_frame(0, pe_demo.SELECT << 24 | engine),
                   bridge_mod.Bridge.write_frame(0, pe_demo.READ_SELECT << 24 | rng.below(8)),
                   bytes([ord("R"), 0x80]),
                   bridge_mod.Bridge.write_frame(0, pe_demo.EVENT << 24 | rng.below(15) + 1)]
    return frames


class ScopeSession:
    """pe_demo.isolation monitor that records the probe pins with the
    on-board capture unit: ``captures`` captures of up to ``limit`` records
    each (0 = the whole buffer), re-armed as soon as one is read back. In
    the loaded phase each ARM is pipelined with ``burst`` rounds of
    host-port operations (burst_frames)."""

    def __init__(self, bridge, fclk_hz: float, *, captures: int = 4, limit: int = 0,
                 channels: str = "uio6,uio7", core_view: bool = False, burst: int = 0, seed: int = 1,
                 log=print, meta: dict | None = None):
        self.scope_mod = load_scope_module()
        self.bridge_mod = load_bridge_module()
        self.b = bridge
        self.scope = self.scope_mod.Scope(bridge, log)
        self.fclk_hz = fclk_hz
        self.target = captures
        self.limit = limit
        self.enable = self.scope_mod.channel_mask(channels)
        self.core_view = core_view
        self.burst = burst
        self.rng = pe_demo.Rng(seed ^ 0xB5)
        self.log = log
        self.meta = dict(meta or {})
        self.captures = []
        self.active = False
        self.burst_ops = 0

    def _arm(self) -> None:
        mode = int(self.core_view) << 1
        arm = (b"A" + self.enable.to_bytes(3, "little") + (0).to_bytes(3, "little") + bytes([mode])
               + self.limit.to_bytes(2, "little"))
        frames = [arm] + (burst_frames(self.bridge_mod, self.rng, self.burst) if self.burst else [])
        self.b.transact(frames)
        self.burst_ops += len(frames) - 1
        self.active = True

    def _collect(self) -> None:
        cap = self.scope.collect(self.fclk_hz, meta=dict(self.meta, index=len(self.captures)))
        self.captures.append(cap)
        self.active = False
        s = cap.summary()
        self.log("capture %d: %d records, %d cycles, %s%s" % (
            len(self.captures) - 1, s["records"], s["window_cycles"],
            "buffer/limit full" if s["full"] else "halted", ", %d changes after the end" % s["lost"]
            if s["lost"] else ""))

    def start(self, chip) -> None:
        self._arm()

    def poll(self, chip) -> bool:
        if not self.active:
            return len(self.captures) >= self.target
        if not self.scope.status().finished:
            return False
        self._collect()
        if len(self.captures) >= self.target:
            return True
        self._arm()
        return False

    def stop(self, chip) -> None:
        if self.active:
            self._collect()


def load_evidence(captures) -> dict:
    """Activity-counter sums over the capture windows."""
    out = {name: 0 for name in LOAD_CHANNELS}
    cycles = 0
    for cap in captures:
        w = cap.window()
        if w:
            cycles += w[1] - w[0] + 1
        for name in LOAD_CHANNELS:
            out[name] += cap.activity[cap_channel(name)] if cap.activity else 0
    return {"window_cycles": cycles, "edges": out}


def cap_channel(name: str) -> int:
    return load_scope_module().channel_index(name)


def scope_measure(adapter, bridge, images, mode, *, fclk_hz, captures, limit, seed, max_cycles,
                  burst=3, core_view=False, log=print, poll_cycles=None):
    """One phase of the self-measured experiment: pe_demo.isolation with a ScopeSession."""
    session = ScopeSession(bridge, fclk_hz, captures=captures, limit=limit, core_view=core_view,
                           burst=burst if mode == "loaded" else 0, seed=seed, log=log,
                           meta={"mode": mode, "seed": seed})
    result = pe_demo.isolation(adapter, images, mode, max_cycles, seed=seed, log=log, monitor=session,
                               poll_cycles=poll_cycles or max(1, int(fclk_hz // 1000)))
    result["burst_operations"] = session.burst_ops
    result["capture_retries"] = session.scope.crc_retries
    return result, session.captures


def scope_report(phases: dict, out: Path, expect=("same", "same"), log=print, plot=True) -> dict:
    """Write captures (JSON + VCD), analyse them with pe_capture.py against
    the static prediction, compare the phases and decide.

    PASS requires: the comparison verdicts equal ``expect`` (predicted, then
    one per phase); at least one complete frame per phase; and, when every
    expectation is 'same', load evidence in the loaded windows (engine 0
    UART, engine 2 SCK and host write-valid all toggled) and none in the idle
    windows."""
    import pe_capture
    out.mkdir(parents=True, exist_ok=True)
    pred = out / "predicted.json"
    if pe_capture.main(["predict", "--image", str(HERE / "firmware" / "timing-probe.image.json"),
                        "--channels", "probe6=6,probe7=7", "--json", str(pred)]) != 0:
        raise pe_demo.DemoError("static prediction of the timing probe failed")
    analyses, evidence = [], {}
    for phase, caps in phases.items():
        vcds = []
        for k, cap in enumerate(caps):
            cap.save(out / f"{phase}-{k}.json")
            if cap.records:
                cap.write_vcd(out / f"{phase}-{k}.vcd")
                vcds.append(str(out / f"{phase}-{k}.vcd"))
        if not vcds:
            raise pe_demo.DemoError(f"{phase}: no capture holds any record")
        if pe_capture.main(["analyze", *vcds, "--channels", PROBE_CHANNELS, "--reference", str(pred),
                            "--label", phase, "--json", str(out / f"{phase}.json"), "--quiet"]) != 0:
            raise pe_demo.DemoError(f"{phase}: capture analysis failed")
        analyses.append(str(out / f"{phase}.json"))
        evidence[phase] = load_evidence(caps)
    args = ["compare", str(pred), *analyses, "--labels", ",".join(["predicted", *phases]),
            "--expect", ",".join(["same", *expect]), "--text", str(out / "compare.txt"),
            "--json", str(out / "compare.json")]
    if plot:
        try:
            import matplotlib  # noqa: F401
            args += ["--plot", str(out / "isolation.png")]
        except ImportError:
            pass
    compare_ok = pe_capture.main(args) == 0
    reps = {phase: json.loads(Path(a).read_text()) for phase, a in zip(phases, analyses)}
    frames = {phase: (r.get("frames") or {}).get("frames_complete", 0) for phase, r in reps.items()}
    log("")
    log("load evidence (activity counters inside the capture windows, edges):")
    log("  %-8s %10s " % ("phase", "cycles") + " ".join("%7s" % n for n in LOAD_CHANNELS))
    for phase, ev in evidence.items():
        log("  %-8s %10d " % (phase, ev["window_cycles"]) + " ".join("%7d" % ev["edges"][n] for n in LOAD_CHANNELS))
    reasons = []
    if not compare_ok:
        reasons.append("verdicts differ from the expectation")
    reasons += [f"{p}: no complete frame" for p, n in frames.items() if n == 0 and expect[list(phases).index(p)] == "same"]
    if all(e == "same" for e in expect):
        if "loaded" in evidence and not all(evidence["loaded"]["edges"][n] for n in LOAD_REQUIRED):
            reasons.append("loaded windows show no load on " + ", ".join(
                n for n in LOAD_REQUIRED if not evidence["loaded"]["edges"][n]))
        if "idle" in evidence and any(evidence["idle"]["edges"][n] for n in LOAD_REQUIRED):
            reasons.append("idle windows show activity on " + ", ".join(
                n for n in LOAD_REQUIRED if evidence["idle"]["edges"][n]))
    ok = not reasons
    summary = {"pass": ok, "reasons": reasons, "expect": list(expect), "frames": frames, "load_evidence": evidence,
               "jitter_pp_cycles": {p: (r.get("jitter") or {}).get("max_peak_to_peak_cycles") for p, r in reps.items()}}
    (out / "verdict.json").write_text(json.dumps(summary, indent=1) + "\n")
    log("")
    if ok and all(e == "same" for e in expect):
        log("NON-INTERFERENCE PASS: engine 3's pin edges are cycle-identical to the prediction in every "
            "phase (%s complete frames; jitter %s cycles peak-to-peak)" % (
                ", ".join(f"{p} {n}" for p, n in frames.items()),
                ", ".join(f"{p} {v}" for p, v in summary["jitter_pp_cycles"].items())))
    elif ok:
        log("SELF-MEASURE PASS: verdicts as expected (%s)" % ", ".join(expect))
    else:
        log("NON-INTERFERENCE FAIL: " + "; ".join(reasons))
    return summary


def connect(port: str, baud: int = 1_000_000):
    bridge_mod = load_bridge_module()
    bridge = bridge_mod.Bridge(bridge_mod.SerialTransport(port, baud))
    version = bridge.version()
    chip = bridge_mod.Chip(bridge)
    return bridge_mod, chip, version


def cmd_isolation(a, bridge_mod, chip, version) -> int:
    images = bridge_images(bridge_mod, pe_demo.ISOLATION_IMAGES)
    adapter = BridgeChip(chip, bridge_mod.BridgeError, version.clk_hz)
    cycles = int(a.seconds * version.clk_hz)
    t = time.monotonic()
    result = pe_demo.isolation(adapter, images, a.mode, cycles, seed=a.seed, log=print)
    result["wall_seconds"] = time.monotonic() - t
    result["board"] = version.describe()
    print(json.dumps({k: v for k, v in result.items() if not k.startswith("engines_")}, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1) + "\n")
    return 0 if result["probe_running"] else 1


def _open_uart(path: str, baud: int):
    import serial  # pyserial
    port = serial.Serial(path, baud, timeout=0.05)
    port.reset_input_buffer()
    return port


def _read_for(port, seconds: float) -> bytes:
    out = bytearray()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        out += port.read(4096)
    return bytes(out)


def cmd_scope(a, bridge_mod, chip, version) -> int:
    if not version.has_scope:
        print("FAIL: this bitstream has no capture unit (bridge protocol < 2)", file=sys.stderr)
        return 1
    images = bridge_images(bridge_mod, pe_demo.ISOLATION_IMAGES)
    adapter = BridgeChip(chip, bridge_mod.BridgeError, version.clk_hz)
    phases = {}
    results = {}
    for mode in ("idle", "loaded"):
        print(f"--- {mode} phase")
        t = time.monotonic()
        result, caps = scope_measure(adapter, chip.b, images, mode, fclk_hz=version.clk_hz,
                                     captures=a.captures, limit=a.limit, seed=a.seed,
                                     max_cycles=int(a.seconds * version.clk_hz), core_view=a.core_view)
        result["wall_seconds"] = time.monotonic() - t
        results[mode] = {k: v for k, v in result.items() if not k.startswith("engines_")}
        phases[mode] = caps
        if not result["probe_running"]:
            print(f"FAIL: probe engine not running after the {mode} phase", file=sys.stderr)
            return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "runs.json").write_text(json.dumps({"board": version.describe(), "phases": results}, indent=1) + "\n")
    print("--- analysis (pe_capture.py)")
    summary = scope_report(phases, out, expect=tuple(a.expect.split(",")))
    return 0 if summary["pass"] else 1


def cmd_four(a, bridge_mod, chip, version) -> int:
    images = bridge_images(bridge_mod, pe_demo.FOUR_IMAGES)
    adapter = BridgeChip(chip, bridge_mod.BridgeError, version.clk_hz)
    uart = _open_uart(a.uart, a.uart_baud)
    pe_demo.four_setup(adapter, images, i2c_address=a.i2c_address, log=print)
    greeting = b"engine 0: UART TX\r\n"
    adapter.tx_write(0, list(greeting))
    got = _read_for(uart, 0.2)
    print(f"UART TX -> adapter: {got!r}")
    ok = got == greeting
    for rnd in range(a.rounds):
        text = b"echo %d\r\n" % rnd
        uart.write(text)                        # engine 1 receives, the mover echoes through engine 0
        pe_demo.four_round(adapter, b"", a.i2c_address)
        spi, i2c = pe_demo.four_collect(adapter, 1, 1, int(0.5 * version.clk_hz), log=print)
        echoed = _read_for(uart, 0.05)
        man, typ, cap = pe_demo.jedec_id(spi[0]) if spi else (0, 0, 0)
        temp = pe_demo.tmp_celsius(i2c[0] & 0xFF) if i2c else None
        print(f"round {rnd}: SPI JEDEC {man:02x} {typ:02x} {cap:02x}" if spi else f"round {rnd}: no SPI reply",
              f"| I2C 0x{a.i2c_address:02x}: {temp} C" if i2c else "| no I2C reply",
              f"| UART echo {echoed!r}")
        ok &= bool(spi) and bool(i2c) and (spi[0] & 0xFFFFFF) not in (0, 0xFFFFFF) and echoed == text
    statuses = pe_demo.bridge_status(adapter)
    for s in statuses:
        print(s)
    ok &= all(s["running"] and not s["fault"] for s in statuses)
    print("FOUR-PROTOCOL", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_bridge(a, bridge_mod, chip, version) -> int:
    names = ("uart-tx-115200", "read-ticker", "hex-formatter", "i2c-read-100k")
    images = bridge_images(bridge_mod, names)
    adapter = BridgeChip(chip, bridge_mod.BridgeError, version.clk_hz)
    uart = _open_uart(a.uart, a.uart_baud)
    pe_demo.bridge_setup(adapter, images, log=print)
    print(f"host idle; reading the UART adapter for {a.seconds} s")
    data = _read_for(uart, a.seconds)
    readings = pe_demo.parse_hex_lines(data)
    print(f"{len(data)} bytes, {len(readings)} readings: "
          + " ".join(f"{pe_demo.tmp_celsius(r)}C" for r in readings[:20]))
    for s in pe_demo.bridge_status(adapter):
        print(s)
    return 0 if readings else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="FPGA bridge serial port")
    ap.add_argument("--bridge-baud", type=int, default=1_000_000, help="bridge baud rate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("isolation", help="timing-isolation measurement phase")
    p.add_argument("--mode", choices=("idle", "loaded"), required=True)
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--json")
    p = sub.add_parser("four", help="four protocols against real parts")
    p.add_argument("--uart", required=True, help="USB-UART adapter port wired to pins 0/1")
    p.add_argument("--uart-baud", type=int, default=115200)
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--i2c-address", type=lambda s: int(s, 0), default=pe_demo.TMP_ADDRESS)
    p = sub.add_parser("scope", help="self-measured timing isolation (on-board capture unit)")
    p.add_argument("--out", required=True, help="directory for captures, analyses and the verdict")
    p.add_argument("--captures", type=int, default=4, help="captures per phase")
    p.add_argument("--limit", type=int, default=0, help="records per capture (0 = whole buffer)")
    p.add_argument("--seconds", type=float, default=60.0, help="upper bound per phase")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--core-view", action="store_true", help="record uio from the core outputs, not the pads")
    p.add_argument("--expect", default="same,same", help="expected verdicts of idle,loaded")
    p = sub.add_parser("bridge", help="host-free I2C sensor -> UART chain")
    p.add_argument("--uart", required=True)
    p.add_argument("--uart-baud", type=int, default=115200)
    p.add_argument("--seconds", type=float, default=10.0)
    a = ap.parse_args(argv)
    bridge_mod, chip, version = connect(a.port, a.bridge_baud)
    print("bridge:", version.describe())
    handler = {"isolation": cmd_isolation, "four": cmd_four, "bridge": cmd_bridge, "scope": cmd_scope}[a.cmd]
    try:
        return handler(a, bridge_mod, chip, version)
    except (bridge_mod.BridgeError, pe_demo.DemoError, load_scope_module().ScopeError) as err:
        print(f"FAIL: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
