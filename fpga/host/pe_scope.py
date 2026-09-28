#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Client for the on-board capture unit (fpga/rtl/pe_fpga_scope.v).

The UART-bridge bitstreams with bridge protocol 2 contain a capture unit that
timestamps every change of the protocol pins (uio), uo_out and ui_in with the
FPGA's own core-clock counter and stores the changes in block RAM. This
module arms it, reads it back over the bridge (CRC-checked), reconstructs
per-channel waveforms in core clock cycles and writes them as a VCD that
demo/pe_capture.py analyses without any cycle recovery (docs/fpga.md,
"On-board capture unit"; docs/demo.md, "Self-measured isolation").

    python3 fpga/host/pe_scope.py --port /dev/ttyUSB1 status
    python3 fpga/host/pe_scope.py --port /dev/ttyUSB1 capture --channels uio6,uio7 \\
        --trigger uio6,uio7 --limit 4096 --seconds 1 --json cap.json --vcd cap.vcd
    python3 fpga/host/pe_scope.py show cap.json
    python3 fpga/host/pe_scope.py vcd cap.json cap.vcd

Channel names: uio0..uio7 (protocol pins), uo0..uo7 (uo_out), ui0..ui7
(ui_in); aliases wvalid = ui4, rready = ui5, wready = uo4, rvalid = uo5,
event = uo6, fault = uo7.

Standard library only (pyserial for a real port, through fpga/host/pe_host.py).
"""

from __future__ import annotations

import argparse
import binascii
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

TOOL = "pe_scope"
FORMAT_VERSION = 1
SCOPE_VERSION = 1                     # pe_fpga_scope.v 'Q' byte 1
CHANNELS = [f"uio{i}" for i in range(8)] + [f"uo{i}" for i in range(8)] + [f"ui{i}" for i in range(8)]
ALIASES = {"wvalid": "ui4", "rready": "ui5", "wready": "uo4", "rvalid": "uo5", "event": "uo6", "fault": "uo7"}
STATES = {0: "idle", 1: "wait", 2: "run", 3: "full", 4: "done", 5: "settling"}
KINDS = {0: "change", 1: "start", 2: "marker", 3: "end"}
K_CHANGE, K_START, K_MARK, K_END = 0, 1, 2, 3
STAMP_BITS = 22
MARK_INTERVAL = 1 << 21
RECORD_BYTES = 9


class ScopeError(RuntimeError):
    pass


def channel_index(name: str) -> int:
    key = ALIASES.get(name.strip(), name.strip())
    if key not in CHANNELS:
        raise ScopeError(f"unknown channel {name!r} (uio0..7, uo0..7, ui0..7 or {', '.join(ALIASES)})")
    return CHANNELS.index(key)


def channel_mask(names: str | Iterable[str] | None) -> int:
    if not names:
        return 0
    if isinstance(names, str):
        names = [n for n in names.split(",") if n.strip()]
    mask = 0
    for n in names:
        mask |= 1 << channel_index(n)
    return mask


def mask_names(mask: int) -> list[str]:
    return [CHANNELS[i] for i in range(24) if mask >> i & 1]


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF), as pe_fpga_scope.v."""
    return binascii.crc_hqx(data, 0xFFFF)


# --------------------------------------------------------------------- status
@dataclass(frozen=True)
class Status:
    version: int
    state: int
    flags: int
    aw: int
    channels: int
    records: int
    lost: int
    start_stamp: int
    end_stamp: int
    now: int
    enable: int
    trigger: int
    mode: int
    limit: int

    @classmethod
    def parse(cls, reply: bytes) -> "Status":
        if len(reply) != 39 or reply[0] != ord("Q"):
            raise ScopeError(f"bad status reply ({len(reply)} bytes)")
        le = lambda a, b: int.from_bytes(reply[a:b], "little")  # noqa: E731
        return cls(reply[1], reply[2], reply[3], reply[4], reply[5], le(6, 8), le(8, 12), le(12, 18),
                   le(18, 24), le(24, 30), le(30, 33), le(33, 36), reply[36], le(37, 39))

    def check(self) -> "Status":
        """Plausibility of a reply that the CRC does not cover ('Q')."""
        if self.version != SCOPE_VERSION or self.channels != 24:
            raise ScopeError(f"unexpected capture unit (version {self.version}, {self.channels} channels)")
        if not (self.state in STATES and 2 <= self.aw <= 15 and self.flags < 16
                and self.records <= self.limit <= 1 << self.aw and self.limit >= 2 and self.mode < 4
                and self.enable < 1 << 24 and self.trigger < 1 << 24):
            raise ScopeError(f"implausible status reply: {self}")
        return self

    @property
    def state_name(self) -> str:
        return STATES.get(self.state, str(self.state))

    @property
    def depth(self) -> int:
        return 1 << self.aw

    @property
    def full(self) -> bool:
        return bool(self.flags & 1)

    @property
    def halted(self) -> bool:
        return bool(self.flags & 2)

    @property
    def forced(self) -> bool:
        return bool(self.flags & 4)

    @property
    def core_view(self) -> bool:
        return bool(self.flags & 8)

    @property
    def finished(self) -> bool:
        return self.state in (3, 4)

    def describe(self) -> str:
        return (f"capture unit v{self.version}: state {self.state_name}, {self.records}/{self.limit} records "
                f"(depth {self.depth}), lost {self.lost}, enable {','.join(mask_names(self.enable)) or '-'}, "
                f"trigger {','.join(mask_names(self.trigger)) or '-'}, "
                f"{'core' if self.core_view else 'pad'} view of uio"
                + (", full" if self.full else "") + (", halted" if self.halted else ""))


# -------------------------------------------------------------------- records
@dataclass(frozen=True)
class Record:
    stamp: int          # full stamp: index of the launching clock edge
    kind: int
    mask: int
    values: int

    @property
    def kind_name(self) -> str:
        return KINDS[self.kind]


def unpack(raw: int) -> tuple[int, int, int, int]:
    """72-bit record -> (stamp22, kind, mask, values)."""
    return raw & (1 << STAMP_BITS) - 1, raw >> 22 & 3, raw >> 24 & 0xFFFFFF, raw >> 48 & 0xFFFFFF


def pack(stamp: int, kind: int, mask: int, values: int) -> int:
    return (values & 0xFFFFFF) << 48 | (mask & 0xFFFFFF) << 24 | (kind & 3) << 22 | stamp & (1 << STAMP_BITS) - 1


def decode(raw: Sequence[int], start_stamp: int, end_stamp: int | None = None) -> list[Record]:
    """Full stamps from the 22-bit fields; checks the format's invariants."""
    out: list[Record] = []
    period = 1 << STAMP_BITS
    for i, word in enumerate(raw):
        s22, kind, mask, values = unpack(word)
        if i == 0:
            if kind != K_START:
                raise ScopeError(f"record 0 is a {KINDS[kind]} record, expected start")
            if s22 != start_stamp % period:
                raise ScopeError(f"record 0 stamp {s22:#x} does not match the start stamp {start_stamp:#x}")
            full = start_stamp
        else:
            if kind == K_START:
                raise ScopeError(f"record {i}: second start record")
            if out[-1].kind == K_END:
                raise ScopeError(f"record {i} follows the end record")
            delta = (s22 - out[-1].stamp) % period
            if delta == 0:
                raise ScopeError(f"record {i}: stamp does not advance")
            full = out[-1].stamp + delta
            if kind == K_MARK and (mask or full % MARK_INTERVAL):
                raise ScopeError(f"record {i}: marker at stamp {full} (mask {mask:#x})")
            if kind == K_CHANGE and not mask:
                raise ScopeError(f"record {i}: change record without a change")
        out.append(Record(full, kind, mask, values))
    # The status stamps are 48-bit (they wrap after 2^48 cycles, 65 days at 50 MHz).
    if out and out[-1].kind == K_END and end_stamp is not None and out[-1].stamp % (1 << 48) != end_stamp:
        raise ScopeError(f"end record stamp {out[-1].stamp} != status end stamp {end_stamp}")
    return out


# ------------------------------------------------------------------- capture
@dataclass
class Capture:
    """One finished capture: status, decoded records, activity counters."""

    status: Status
    raw: list[int]
    activity: list[int]
    fclk_hz: float | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.records = decode(self.raw, self.status.start_stamp,
                              self.status.end_stamp if self.status.finished else None)
        self.check()

    # -- structure
    @property
    def enabled(self) -> list[int]:
        return [i for i in range(24) if self.status.enable >> i & 1]

    @property
    def complete(self) -> bool:
        """The records end with the end record (halted or buffer/limit reached)."""
        return bool(self.records) and self.records[-1].kind == K_END

    def window(self) -> tuple[int, int] | None:
        """First and last cycle covered by the records (inclusive)."""
        if not self.records:
            return None
        return self.records[0].stamp, self.records[-1].stamp

    def initial(self, ch: int) -> int:
        r0 = self.records[0]
        return (r0.values ^ r0.mask) >> ch & 1

    def edges(self, ch: int) -> list[tuple[int, int]]:
        """[(cycle, new level)] of an enabled channel inside the window."""
        if ch not in self.enabled:
            raise ScopeError(f"{CHANNELS[ch]} was not enabled in this capture")
        return [(r.stamp, r.values >> ch & 1) for r in self.records if r.mask >> ch & 1]

    def check(self) -> None:
        """Enabled channels change only in records whose mask says so."""
        if not self.records:
            return
        en = self.status.enable
        level = (self.records[0].values ^ self.records[0].mask) & en
        for i, r in enumerate(self.records):
            if r.mask & ~en:
                raise ScopeError(f"record {i}: mask {r.mask:#x} outside the enabled channels")
            if (level ^ r.values) & en != r.mask:
                raise ScopeError(f"record {i}: enabled levels changed without a mask bit "
                                 f"(before {level:#x}, record {r.values & en:#x}, mask {r.mask:#x})")
            level = r.values & en
        act = self.activity
        for ch in self.enabled:
            n = sum(1 for r in self.records if r.mask >> ch & 1)
            if act and act[ch] != n and act[ch] != 0xFFFFFFFF:
                raise ScopeError(f"{CHANNELS[ch]}: activity counter {act[ch]} != {n} recorded edges")

    def summary(self) -> dict:
        w = self.window()
        kinds = {name: sum(1 for r in self.records if r.kind == k) for k, name in KINDS.items()}
        return {"records": len(self.records), "kinds": kinds, "window": list(w) if w else None,
                "window_cycles": (w[1] - w[0] + 1) if w else 0, "complete": self.complete,
                "full": self.status.full, "halted": self.status.halted, "lost": self.status.lost,
                "enabled": mask_names(self.status.enable),
                "edges": {CHANNELS[ch]: len(self.edges(ch)) for ch in self.enabled},
                "activity": {CHANNELS[i]: n for i, n in enumerate(self.activity) if n}}

    # -- files
    def to_json(self) -> dict:
        return {"tool": TOOL, "format": FORMAT_VERSION, "fclk_hz": self.fclk_hz, "meta": self.meta,
                "status": asdict(self.status), "activity": self.activity,
                "records": [f"{w:018x}" for w in self.raw], "summary": self.summary()}

    @classmethod
    def from_json(cls, data: dict) -> "Capture":
        if data.get("tool") != TOOL:
            raise ScopeError("not a pe_scope capture file")
        return cls(Status(**data["status"]), [int(w, 16) for w in data["records"]], list(data["activity"]),
                   data.get("fclk_hz"), data.get("meta", {}))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=1) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "Capture":
        return cls.from_json(json.loads(Path(path).read_text()))

    def write_vcd(self, path: str | Path, channels: Iterable[int] | None = None) -> None:
        """VCD whose tick is one core clock cycle (stamp = tick). demo/pe_capture.py
        recognises the pe_scope comment and uses the ticks as cycle numbers."""
        chans = sorted(self.enabled if channels is None else channels)
        w = self.window()
        if w is None:
            raise ScopeError("empty capture: nothing to write")
        ids = {ch: chr(37 + k) for k, ch in enumerate(chans)}   # '%'..'<'
        win_id = "!"
        head = [f"$version {TOOL} {FORMAT_VERSION} $end",
                f"$comment pe_scope tick=cycle fclk_hz={self.fclk_hz or 0:g} window={w[0]}..{w[1]} "
                f"lost={self.status.lost} view={'core' if self.status.core_view else 'pad'} $end",
                "$timescale 1ns $end", "$scope module pe_scope $end", f"$var wire 1 {win_id} window $end"]
        head += [f"$var wire 1 {ids[ch]} {CHANNELS[ch]} $end" for ch in chans]
        head += ["$upscope $end", "$enddefinitions $end"]
        t0 = max(w[0] - 1, 0)
        body = [f"#{t0}", "$dumpvars", f"0{win_id}"] + [f"{self.initial(ch)}{ids[ch]}" for ch in chans] + ["$end"]
        for i, r in enumerate(self.records):
            lines = [f"1{win_id}"] if i == 0 else []
            lines += [f"{r.values >> ch & 1}{ids[ch]}" for ch in chans if r.mask >> ch & 1]
            if lines:
                body.append(f"#{r.stamp}")
                body += lines
        body += [f"#{w[1] + 1}", f"0{win_id}"]
        Path(path).write_text("\n".join(head + body) + "\n")


# --------------------------------------------------------------------- client
class Scope:
    """Capture-unit commands over a pe_host.Bridge (bridge protocol 2).

    Read commands ('Q', 'P', 'U') are repeated, at most ``retries`` times,
    when a reply is short (a transport timeout), fails its CRC ('U'), or is
    implausible: a 'U' reply whose record count n differs from
    min(count, records stored - first), or a 'Q' reply outside the ranges of
    pe_fpga_scope.v. Before a repeat the input is drained, so a misframed
    reply cannot shift the replies that follow. The 'U' header (n) and the
    'Q' and 'P' replies are not CRC-covered; the range checks, and Capture's
    cross-checks of status, records and counters, stand in for a CRC there.
    Commands that change state ('A', 'F', 'H') are not repeated."""

    CHUNK = 512          # records per 'U' request (4.6 kB, 46 ms at 1 Mbaud)
    RETRIES = 3

    def __init__(self, bridge, log: Callable[[str], None] | None = None) -> None:
        self.b = bridge
        self.log = log or (lambda _: None)
        self.crc_retries = 0          # 'U' replies with a CRC mismatch
        self.link_retries = 0         # short or implausible replies

    def _drain(self) -> None:
        """Discard input until the link is quiet (the rest of a misframed reply)."""
        t = self.b.t
        if hasattr(t, "flush_input"):
            t.flush_input()
            return
        for _ in range(256):
            if not t.read(4096, 0.1):
                return

    def _read(self, what: str, fn, retries: int | None = None):
        retries = self.RETRIES if retries is None else retries
        for attempt in range(retries + 1):
            try:
                return fn()
            except RuntimeError as err:       # pe_host.BridgeError (short reply) or ScopeError
                if attempt == retries:
                    raise ScopeError(f"{what}: {err} (after {retries + 1} attempts)") from err
                self.link_retries += 1
                self.log(f"{what}: {err} (attempt {attempt + 1}); draining the link and retrying")
                self._drain()

    def arm(self, enable: int, trigger: int = 0, *, wait: bool = False, core_view: bool = False,
            limit: int = 0) -> None:
        if not 0 <= limit < 1 << 16:
            raise ScopeError("limit must fit 16 bits")
        mode = int(wait) | int(core_view) << 1
        self.b.one(b"A" + enable.to_bytes(3, "little") + trigger.to_bytes(3, "little") + bytes([mode])
                   + limit.to_bytes(2, "little"))

    def force(self) -> None:
        self.b.one(b"F")

    def halt(self) -> None:
        self.b.one(b"H")

    def status(self) -> Status:
        return self._read("status", lambda: Status.parse(self.b.one(b"Q")).check())

    def activity(self) -> list[int]:
        def get():
            r = self.b.one(b"P")
            return [int.from_bytes(r[1 + 4 * i:5 + 4 * i], "little") for i in range(24)]
        return self._read("activity counters", get)

    def dump(self, first: int, count: int, retries: int | None = None, expect: int | None = None) -> list[int]:
        """Records [first, first + count), CRC-checked. ``expect``: the number
        of records the reply must hold (min(count, records stored - first));
        without it, any n <= count is accepted."""
        def get():
            r = self.b.one(b"U" + first.to_bytes(2, "little") + count.to_bytes(2, "little"))
            n = int.from_bytes(r[1:3], "little")
            if n > count or (expect is not None and n != expect):
                raise ScopeError(f"read-back of records {first}..: reply holds {n} records, expected "
                                 f"{count if expect is None else expect}")
            body, crc = r[3:3 + RECORD_BYTES * n], int.from_bytes(r[3 + RECORD_BYTES * n:], "little")
            if crc16(body) != crc:
                self.crc_retries += 1
                raise ScopeError(f"records {first}..{first + n - 1}: CRC mismatch")
            return [int.from_bytes(body[RECORD_BYTES * i:RECORD_BYTES * (i + 1)], "little") for i in range(n)]
        return self._read(f"records {first}..{first + count - 1}", get, retries)

    def read_all(self, count: int) -> list[int]:
        out: list[int] = []
        while len(out) < count:
            n = min(self.CHUNK, count - len(out))
            out += self.dump(len(out), n, expect=n)
        return out

    def collect(self, fclk_hz: float | None = None, meta: dict | None = None) -> Capture:
        """Halt if needed, read status, counters and every record. If the
        cross-checks of the result fail (Capture), everything is read once
        more before the error is raised."""
        st = self.status()
        if st.state in (1, 2, 5):
            self.halt()
            st = self.status()
        if st.state == 3:           # FULL: halt so the lost counter stops too
            self.halt()
            st = self.status()
        for attempt in (0, 1):
            try:
                raw = self.read_all(st.records)
                return Capture(st, raw, self.activity(), fclk_hz, dict(meta or {}))
            except ScopeError as err:
                if attempt:
                    raise
                self.link_retries += 1
                self.log(f"capture check failed ({err}); reading status, records and counters again")
                self._drain()
                st = self.status()

    def run(self, enable: int, trigger: int = 0, *, wait: bool = False, core_view: bool = False,
            limit: int = 0, timeout_s: float = 5.0, before: Callable[[], None] | None = None,
            sleep: Callable[[float], None] = time.sleep, fclk_hz: float | None = None,
            meta: dict | None = None) -> Capture:
        """Arm, call ``before`` (e.g. START an engine), wait until the buffer or
        the limit is full (or the timeout), then collect."""
        self.arm(enable, trigger, wait=wait, core_view=core_view, limit=limit)
        if before is not None:
            before()
        deadline = time.monotonic() + timeout_s
        while True:
            st = self.status()
            if st.finished or time.monotonic() > deadline:
                break
            sleep(0.01)
        return self.collect(fclk_hz, meta)


# ------------------------------------------------------------------------ CLI
def _connect(port: str, baud: int):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pe_host  # fpga/host/pe_host.py
    bridge = pe_host.Bridge(pe_host.SerialTransport(port, baud))
    version = bridge.version()
    if not version.has_scope:
        raise ScopeError(f"{version.describe()}: this bitstream has no capture unit (bridge protocol < 2)")
    return bridge, version


def _print_summary(cap: Capture) -> None:
    s = cap.summary()
    print(f"window {s['window'][0]}..{s['window'][1]} ({s['window_cycles']} cycles)" if s["window"] else "no records")
    print(f"records {s['records']} {s['kinds']}; complete {s['complete']}, full {s['full']}, lost {s['lost']}")
    for ch in cap.enabled:
        ev = cap.edges(ch)
        gaps = sorted({b[0] - a[0] for a, b in zip(ev, ev[1:])})
        print(f"  {CHANNELS[ch]:>5}: initial {cap.initial(ch)}, {len(ev)} edges"
              + (f", intervals {gaps[:12]}{' ...' if len(gaps) > 12 else ''} cycles" if gaps else ""))
    other = {k: v for k, v in s["activity"].items() if k not in s["enabled"]}
    if other:
        print("  activity on channels not recorded: " + ", ".join(f"{k} {v}" for k, v in other.items()))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="bridge serial port (status, capture)")
    ap.add_argument("--baud", type=int, default=1_000_000)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="capture-unit status")
    p = sub.add_parser("capture", help="arm, wait, read back")
    p.add_argument("--channels", required=True, help="channels to record, e.g. uio6,uio7")
    p.add_argument("--trigger", help="start at the first change of these channels (default: immediately)")
    p.add_argument("--core-view", action="store_true", help="uio from the core's outputs instead of the pads")
    p.add_argument("--limit", type=int, default=0, help="records (default: the whole buffer)")
    p.add_argument("--seconds", type=float, default=5.0, help="stop after this long if not full")
    p.add_argument("--json", help="write the capture (records, status, counters)")
    p.add_argument("--vcd", help="write the enabled channels as a VCD (1 tick = 1 core clock cycle)")
    p = sub.add_parser("show", help="summarise a capture file")
    p.add_argument("capture")
    p = sub.add_parser("vcd", help="capture file -> VCD")
    p.add_argument("capture")
    p.add_argument("output")
    a = ap.parse_args(argv)
    try:
        if a.cmd in ("show", "vcd"):
            cap = Capture.load(a.capture)
            if a.cmd == "show":
                _print_summary(cap)
            else:
                cap.write_vcd(a.output)
            return 0
        if not a.port:
            ap.error("--port is required for status and capture")
        bridge, version = _connect(a.port, a.baud)
        scope = Scope(bridge, log=print)
        if a.cmd == "status":
            print(version.describe())
            print(scope.status().describe())
            return 0
        cap = scope.run(channel_mask(a.channels), channel_mask(a.trigger), wait=bool(a.trigger),
                        core_view=a.core_view, limit=a.limit, timeout_s=a.seconds, fclk_hz=version.clk_hz,
                        meta={"board": version.describe()})
        print(cap.status.describe())
        _print_summary(cap)
        if a.json:
            cap.save(a.json)
        if a.vcd and cap.records:
            cap.write_vcd(a.vcd)
        return 0
    except ScopeError as err:
        print(f"FAIL: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
