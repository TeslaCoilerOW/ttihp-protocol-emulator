#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Logic-analyser capture analysis for the timing-isolation demonstration.

Reads sigrok captures (srzip ``.sr``, VCD, CSV) and simulation VCDs, turns
pin edges into engine-cycle timestamps, and reports edge-interval histograms,
jitter statistics and the frame structure of the timing probe
(demo/firmware/timing-probe). ``compare`` puts several runs side by side and
decides whether they are identical. Python 3.8+, standard library only;
numpy speeds up srzip decoding and matplotlib draws ``--plot`` figures when
installed.

Subcommands:

  analyze   capture(s) -> analysis JSON (+ text report)
  compare   analysis JSONs -> side-by-side histograms, verdicts, optional figure
  predict   firmware image -> the frame pattern predicted by tools/timing/pe_timing.py
  resample  exact-time VCD (simulation) -> srzip as a logic analyser would record it
  info      list the channels and sample rate of a capture

Converting edge times to engine cycles (--clock / --fclk):

  --clock CH   Host-clocked builds (a Pico supplies every clock edge). The
               capture includes the clock pin; an edge's cycle number is the
               number of rising clock edges at or before it. Exact for any
               sample rate that resolves the clock (the host clock runs at kHz
               rates); irregular host timing does not matter.
  --fclk HZ    Free-running clock (UART-bridge builds). Every pin change sits
               on the design's clock grid; a sampler at fs records it within
               one sample after the true edge, so each edge's cycle number can
               be recovered exactly by tracking the clock phase (the crystal
               offsets of the FPGA board and the analyser are estimated from
               the data). Needs fs >= 2.2 fclk: at fs = 2 fclk the design clock
               and its alias fs - fclk coincide, the direction of the phase
               drift is unobservable and cycle numbers slip; below 2 the
               margins are too small. A 24 MHz analyser therefore cannot do
               this for the 12 MHz osc12 build; a 120 Msps Pico analyser can
               (osc12, pll40 and pll50 builds).
  (neither)    Intervals in samples only (statistical comparison).

Examples:

  pe_capture.py analyze idle.sr --channels probe6=D1,probe7=D2 --clock D0 --json idle.json
  pe_capture.py analyze loaded.sr --channels probe6=D1,probe7=D2 --fclk 12e6 \\
      --context uart=D3,sck=D4 --uart uart:64 --json loaded.json
  pe_capture.py predict --image demo/firmware/timing-probe.image.json --json predicted.json
  pe_capture.py compare idle.json loaded.json --labels idle,loaded --expect same,same \\
      --reference predicted.json --plot isolation.png
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import re
import sys
import zipfile
from collections import Counter, deque
from fractions import Fraction
from pathlib import Path

TOOL = "pe_capture"
VERSION = 1
HERE = Path(__file__).resolve().parent
REPO = HERE.parent

try:  # optional
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None


class CaptureError(Exception):
    pass


# =========================================================== capture readers
class Signal:
    """One named signal: initial value and (tick, value) changes. Value is an
    int for defined values; for vectors a binary string when x/z bits exist;
    None for an undefined scalar."""

    def __init__(self, name, width=1, lsb=0, msb=None):
        self.name = name
        self.width = width
        self.lsb = lsb
        self.msb = msb if msb is not None else lsb + width - 1
        self.changes = []

    def bit(self, index):
        """Scalar changes of one bit (index in the declared range)."""
        pos = index - self.lsb if self.msb >= self.lsb else self.lsb - index
        if not 0 <= pos < self.width:
            raise CaptureError(f"{self.name}: bit {index} outside [{self.msb}:{self.lsb}]")
        out = []
        last = "unset"
        for tick, value in self.changes:
            if isinstance(value, int):
                v = (value >> pos) & 1
            elif value is None:
                v = None
            else:
                s = value
                pad = s[0] if s and s[0] in "xzXZ" else "0"
                s = s.rjust(self.width, pad)
                ch = s[-1 - pos]
                v = int(ch) if ch in "01" else None
            if v != last:
                out.append((tick, v))
                last = v
        return out


class Capture:
    """Signals with integer ticks. ``tick_seconds`` converts ticks to time;
    for sampled captures (srzip, CSV, sigrok VCD) ticks are sample indices and
    ``samplerate`` is set."""

    def __init__(self, path, fmt, tick_seconds, samplerate=None):
        self.path = Path(path)
        self.format = fmt
        self.tick_seconds = Fraction(tick_seconds)
        self.samplerate = samplerate
        self.signals = {}
        self.aliases = {}
        self.end_tick = 0

    def names(self):
        return sorted(self.signals)

    def resolve(self, spec):
        """'D1', 'tb.pads[6]', 'pads[6]' -> scalar change list."""
        m = re.fullmatch(r"(.+?)\[(\d+)\]", spec)
        base, index = (m.group(1), int(m.group(2))) if m else (spec, None)
        sig = self._lookup(base)
        if sig is None and index is not None:
            sig = self._lookup(spec)       # a scalar literally named 'x[3]'
            index = None
        if sig is None:
            raise CaptureError(f"{self.path.name}: no signal {spec!r}; have {', '.join(self.names()[:40])}")
        if index is None:
            if sig.width != 1:
                raise CaptureError(f"{spec}: {sig.width}-bit signal, select a bit as {spec}[n]")
            return sig.bit(sig.lsb)
        return sig.bit(index)

    def _lookup(self, name):
        if name in self.signals:
            return self.signals[name]
        if name in self.aliases:
            return self.signals[self.aliases[name]]
        hits = [k for k in self.signals if k.endswith("." + name)]
        if len(hits) == 1:
            return self.signals[hits[0]]
        if len(hits) > 1:
            raise CaptureError(f"{name!r} is ambiguous: {hits}")
        return None


_UNITS = {"s": 1, "ms": Fraction(1, 10**3), "us": Fraction(1, 10**6), "ns": Fraction(1, 10**9),
          "ps": Fraction(1, 10**12), "fs": Fraction(1, 10**15)}


def parse_rate(text):
    """'24 MHz', '24MHz', '24m', '1.5 kHz', '120000000' -> Fraction Hz."""
    t = str(text).strip().replace(" ", "")
    m = re.fullmatch(r"([0-9.]+(?:[eE][-+]?[0-9]+)?)([kKmMgG]?)(?:[hH][zZ])?", t)
    if not m:
        raise CaptureError(f"cannot parse sample rate {text!r}")
    scale = {"": 1, "k": 10**3, "K": 10**3, "m": 10**6, "M": 10**6, "g": 10**9, "G": 10**9}[m.group(2)]
    return Fraction(m.group(1)) * scale


def read_vcd(path, samplerate=None):
    if str(path).endswith(".gz"):
        import gzip
        with gzip.open(path, "rt", errors="replace") as handle:
            text = handle.read()
    else:
        text = Path(path).read_text(errors="replace")
    tokens = text.split()
    n = len(tokens)
    i = 0
    timescale = Fraction(1, 10**9)
    scopes = []
    by_id = {}
    comment_rate = None
    cap_signals = {}
    while i < n:
        tok = tokens[i]
        if tok == "$timescale":
            j = tokens.index("$end", i)
            spec = "".join(tokens[i + 1:j])
            m = re.fullmatch(r"(\d+)([munpf]?s)", spec)
            if not m:
                raise CaptureError(f"bad timescale {spec!r}")
            timescale = int(m.group(1)) * _UNITS[m.group(2)]
            i = j + 1
        elif tok == "$scope":
            scopes.append(tokens[i + 2])
            i = tokens.index("$end", i) + 1
        elif tok == "$upscope":
            if scopes:
                scopes.pop()
            i = tokens.index("$end", i) + 1
        elif tok == "$var":
            j = tokens.index("$end", i)
            parts = tokens[i + 1:j]
            width, ident, ref = int(parts[1]), parts[2], parts[3]
            msb = lsb = None
            rng = "".join(parts[4:]) if len(parts) > 4 else ""
            m = re.fullmatch(r"(.*?)\[(\d+)(?::(\d+))?\]", ref)
            if m and not rng:
                ref, rng = m.group(1), "[%s%s]" % (m.group(2), ":" + m.group(3) if m.group(3) else "")
            m = re.fullmatch(r"\[(\d+)(?::(\d+))?\]", rng)
            if m:
                msb = int(m.group(1))
                lsb = int(m.group(2)) if m.group(2) is not None else msb
            if lsb is None:
                lsb, msb = 0, width - 1
            full = ".".join(scopes + [ref])
            sig = Signal(full, width, lsb, msb)
            cap_signals[full] = sig
            by_id.setdefault(ident, []).append(sig)
            i = j + 1
        elif tok == "$comment":
            j = tokens.index("$end", i)
            body = " ".join(tokens[i + 1:j])
            m = re.search(r"at ([0-9.]+ ?[kMG]?Hz)", body)
            if m:
                comment_rate = parse_rate(m.group(1))
            i = j + 1
        elif tok == "$enddefinitions":
            i = tokens.index("$end", i) + 1
            break
        elif tok.startswith("$"):
            try:
                i = tokens.index("$end", i) + 1
            except ValueError:
                i += 1
        else:
            i += 1
    rate = Fraction(samplerate) if samplerate else comment_rate
    t = 0
    last = {}
    while i < n:
        tok = tokens[i]
        c = tok[0]
        if c == "#":
            t = int(tok[1:])
            i += 1
            continue
        if c == "$":
            i += 1
            continue
        if c in "bB":
            value, ident = tok[1:], tokens[i + 1]
            i += 2
            v = int(value, 2) if all(ch in "01" for ch in value) else value.lower()
        elif c in "rR":
            i += 2
            continue
        elif c in "01xXzZ":
            ident = tok[1:]
            value = c
            i += 1
            v = int(c) if c in "01" else None
        else:
            i += 1
            continue
        if last.get(ident, "unset") == v:
            continue
        last[ident] = v
        for sig in by_id.get(ident, ()):
            sig.changes.append((t, v))
    if rate:
        # Sampled capture (sigrok VCD): ticks -> sample indices.
        cap = Capture(path, "vcd", 1 / rate, rate)
        scale = timescale * rate
        for sig in cap_signals.values():
            sig.changes = [(round(tk * scale), v) for tk, v in sig.changes]
        cap.end_tick = round(t * scale)
    else:
        cap = Capture(path, "vcd", timescale, None)
        cap.end_tick = t
    cap.signals = cap_signals
    for full in cap_signals:
        short = full.split(".")[-1]
        if short not in cap.aliases and sum(1 for k in cap_signals if k.split(".")[-1] == short) == 1:
            cap.aliases[short] = full
    return cap


def _ini(text):
    sections = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
        elif "=" in line and current is not None:
            k, v = line.split("=", 1)
            current[k.strip()] = v.strip()
    return sections


def read_srzip(path):
    """sigrok session file (srzip): metadata + logic-1-N chunks; probeN is bit N-1
    of each unitsize-byte sample (as libsigrok's srzip input module reads it)."""
    with zipfile.ZipFile(path) as z:
        meta = _ini(z.read("metadata").decode())
        device = next((s for k, s in meta.items() if k.startswith("device") and "capturefile" in s), None)
        if device is None:
            raise CaptureError(f"{path}: no logic capture in metadata")
        rate = parse_rate(device["samplerate"])
        unitsize = int(device.get("unitsize", "1"))
        base = device["capturefile"]
        chunks = []
        for name in z.namelist():
            if name == base:
                chunks.append((0, name))
            m = re.fullmatch(re.escape(base) + r"-(\d+)", name)
            if m:
                chunks.append((int(m.group(1)), name))
        chunks.sort()
        probes = {}
        for k, v in device.items():
            m = re.fullmatch(r"probe(\d+)", k)
            if m:
                probes[v] = int(m.group(1)) - 1
        changes, total = _unit_changes([z.read(name) for _, name in chunks], unitsize)
    cap = Capture(path, "srzip", 1 / rate, rate)
    cap.end_tick = total
    for name, bit in probes.items():
        sig = Signal(name)
        last = None
        for idx, value in changes:
            v = (value >> bit) & 1
            if v != last:
                sig.changes.append((idx, v))
                last = v
        cap.signals[name] = sig
    return cap


def _unit_changes(blobs, unitsize):
    """[(sample index, unit value)] wherever the unit value changes."""
    out = []
    offset = 0
    prev = None
    for blob in blobs:
        count = len(blob) // unitsize
        if count == 0:
            continue
        if _np is not None:
            arr = _np.frombuffer(blob[:count * unitsize], dtype=_np.uint8).reshape(count, unitsize)
            vals = _np.zeros(count, dtype=_np.uint64)
            for k in range(unitsize):
                vals |= arr[:, k].astype(_np.uint64) << _np.uint64(8 * k)
            idx = _np.flatnonzero(vals[1:] != vals[:-1]) + 1
            if prev is None or int(vals[0]) != prev:
                out.append((offset, int(vals[0])))
            out.extend(zip((idx + offset).tolist(), vals[idx].tolist()))
            prev = int(vals[-1])
        else:
            if unitsize == 1:
                it = blob[:count]
            else:
                it = [int.from_bytes(blob[k * unitsize:(k + 1) * unitsize], "little") for k in range(count)]
            for k, v in enumerate(it):
                if v != prev:
                    out.append((offset + k, v))
                    prev = v
        offset += count
    return out, offset


def read_csv(path, samplerate=None):
    """sigrok CSV without a time column (one row per sample, dedup off):
    sigrok-cli ... -O csv:label=channel. The rate comes from the
    '; Samplerate:' comment or --samplerate."""
    rate = Fraction(samplerate) if samplerate else None
    names = None
    comment_names = None
    signals = None
    idx = 0
    prev = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith(";"):
                m = re.match(r";\s*Samplerate:\s*(.+)", line)
                if m and rate is None:
                    rate = parse_rate(m.group(1))
                m = re.match(r";\s*Channels \([^)]*\):\s*(.+)", line)
                if m:
                    comment_names = [c.strip() for c in m.group(1).split(",")]
                continue
            cells = [c.strip() for c in line.split(",")]
            if names is None and not all(c in "01" for c in cells):
                if cells and cells[0].lower().startswith("time"):
                    raise CaptureError("CSV has a time column; re-export with "
                                       "'sigrok-cli -i CAPTURE.sr -O csv:label=channel' (no time column), "
                                       "or analyse the .sr file directly")
                names = cells if not all(c in ("logic", "") for c in cells) else comment_names
                continue
            if names is None:
                names = comment_names or ["D%d" % k for k in range(len(cells))]
            if signals is None:
                signals = [Signal(nm) for nm in names]
            if prev is None or cells != prev:
                for k, c in enumerate(cells):
                    v = int(c)
                    s = signals[k]
                    if not s.changes or s.changes[-1][1] != v:
                        s.changes.append((idx, v))
                prev = cells
            idx += 1
    if rate is None:
        raise CaptureError("CSV without a sample rate: give --samplerate")
    cap = Capture(path, "csv", 1 / rate, rate)
    cap.end_tick = idx
    for s in signals or []:
        cap.signals[s.name] = s
    return cap


def read_capture(path, fmt=None, samplerate=None):
    p = Path(path)
    suffix = "".join(p.suffixes[-2:]).lower() if p.suffix.lower() == ".gz" else p.suffix.lower()
    fmt = fmt or {".sr": "srzip", ".vcd": "vcd", ".vcd.gz": "vcd", ".csv": "csv"}.get(suffix)
    if fmt == "srzip":
        return read_srzip(p)
    if fmt == "vcd":
        return read_vcd(p, samplerate)
    if fmt == "csv":
        return read_csv(p, samplerate)
    raise CaptureError(f"{p.name}: unknown format (use --format srzip|vcd|csv)")


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ================================================================== edges
def edges_of(changes, start=None, end=None):
    """0->1 / 1->0 transitions: [(tick, new level)], plus the level before the first."""
    out = []
    level = None
    initial = None
    for tick, v in changes:
        if start is not None and tick < start:
            level = v
            continue
        if end is not None and tick > end:
            break
        if initial is None:
            initial = level
        if v in (0, 1) and level in (0, 1) and v != level:
            out.append((tick, v))
        level = v
    return out, initial


def parse_map(text):
    """'probe6=D1,probe7=D2' or 'D1,D2' -> [(name, spec)]."""
    out = []
    if not text:
        return out
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            name, spec = item.split("=", 1)
        else:
            name = spec = item
        out.append((name.strip(), spec.strip()))
    return out


# ============================================================ cycle recovery
def clock_cycles(clock_changes, ticks, edge="rising"):
    """Cycle number of each tick: count of active clock edges at or before it."""
    want = 1 if edge == "rising" else 0
    active = [t for t, v in edges_of(clock_changes)[0] if v == want]
    return [bisect.bisect_right(active, t) for t in ticks], active


def recover_cycles(samples, ratio, tolerance=0.25, block=2048, keep=32):
    """Exact cycle numbers of edge sample indices on a free-running clock.

    Model: an edge at true time T = phi + n*r (in samples, r = fs/fclk) is
    recorded at the first sample at or after it, s = ceil(T), so the clock
    phase satisfies s - 1 - r*n < phi <= s - r*n. Each assigned edge adds
    that interval; the intersection over the recent edges (widened by eps
    samples for analyser jitter and channel skew) is the feasible phase, and
    a new edge gets the cycle whose interval overlaps it. Candidate intervals
    are r samples apart and one sample wide, so for r >= 2 at most one fits.
    r itself (both crystals' offsets) is refined by least squares over long
    spans. Afterwards every block of edges is refitted and its residual
    spread (the 'arc', at most one sample when every assignment is right)
    is checked.
    """
    if ratio <= 1:
        raise CaptureError(f"fs/fclk = {ratio:.3f} <= 1: cycle numbers are not recoverable")
    if not samples:
        return [], {"nominal_ratio": float(ratio), "estimated_ratio": float(ratio), "relative_offset_ppm": 0.0,
                    "ambiguous_edges": 0, "unmatched_edges": 0, "blocks": 0, "max_arc_samples": None,
                    "refits": 0, "arc_limit_samples": 1 + tolerance, "consistent": True, "edges": 0}
    r = float(ratio)
    unc = 2e-4                     # relative uncertainty of r before the first fit (crystals)
    ns = [0] * len(samples)
    recent = deque(maxlen=keep)
    window = deque()
    sums = [0, 0, 0, 0, 0]          # count, sum n, sum s, sum n*n, sum n*s (exact integers)
    ambiguous = unmatched = refits = 0
    for i, s in enumerate(samples):
        if i:
            # Only edges close enough that the ratio uncertainty moves them by
            # at most 0.1 sample constrain the phase (always the latest one).
            span_limit = 0.1 / (r * unc)
            last_n = recent[-1][0]
            pts = [p for p in recent if last_n - p[0] <= span_limit] or [recent[-1]]
            lo = max(sj - 1 - r * nj for nj, sj in pts)
            hi = min(sj - r * nj for nj, sj in pts)
            if lo > hi:                           # jitter/skew: keep a 1-sample window
                mid = (lo + hi) / 2
                lo, hi = mid - 0.5, mid + 0.5
            x = (s - 0.5 - (lo + hi) / 2) / r
            n0 = int(math.floor(x + 0.5))
            cands = []
            for n in (n0 - 1, n0, n0 + 1):
                a, b = s - 1 - r * n, s - r * n
                gap = max(0.0, max(lo, a) - min(hi, b))
                overlap = max(0.0, min(hi, b) - max(lo, a))
                cands.append((gap, -overlap, abs(n - x), n))
            cands.sort()
            best = cands[0]
            if best[0] > tolerance:
                unmatched += 1
            if cands[1][0] == 0 and cands[1][1] < 0:
                ambiguous += 1
            ns[i] = best[3]
        n = ns[i]
        recent.append((n, s))
        window.append((n, s))
        sums[0] += 1
        sums[1] += n
        sums[2] += s
        sums[3] += n * n
        sums[4] += n * s
        if len(window) > 8192:
            on, os_ = window.popleft()
            sums[0] -= 1
            sums[1] -= on
            sums[2] -= os_
            sums[3] -= on * on
            sums[4] -= on * os_
        if i % 64 == 63:
            span = window[-1][0] - window[0][0]
            k, sn, ss, snn, sns = sums
            den = k * snn - sn * sn
            if span >= 1000 and den:
                r = (k * sns - sn * ss) / den
                unc = max(1e-7, 1.0 / (r * span))
                refits += 1
    span = ns[-1] - ns[0]
    est = (_slope(list(zip(ns, samples))) if span else None) or float(ratio)
    # Residual spread per block with the capture-wide slope (per-block offset,
    # so slow crystal wander over a long capture does not accumulate).
    arcs = []
    for b0 in range(0, len(ns), block):
        pts = list(zip(ns[b0:b0 + block], samples[b0:b0 + block]))
        if len(pts) < 8:
            continue
        res = [s - est * n for n, s in pts]
        arcs.append(max(res) - min(res))
    info = {"nominal_ratio": float(ratio), "estimated_ratio": est,
            "relative_offset_ppm": (est / float(ratio) - 1) * 1e6,
            "ambiguous_edges": ambiguous, "unmatched_edges": unmatched, "blocks": len(arcs),
            "max_arc_samples": max(arcs) if arcs else None, "refits": refits,
            "arc_limit_samples": 1 + tolerance}
    info["consistent"] = bool(arcs) and max(arcs) <= 1 + tolerance and unmatched == 0
    return ns, info


def _slope(pts):
    pts = list(pts)
    k = len(pts)
    if k < 2:
        return None
    mn = sum(p[0] for p in pts) / k
    my = sum(p[1] for p in pts) / k
    sxx = sum((p[0] - mn) ** 2 for p in pts)
    if sxx == 0:
        return None
    return sum((p[0] - mn) * (p[1] - my) for p in pts) / sxx


# ================================================================ analysis
class Event:
    __slots__ = ("cycle", "ch", "pol", "tick")

    def __init__(self, cycle, ch, pol, tick):
        self.cycle, self.ch, self.pol, self.tick = cycle, ch, pol, tick


def detect_pattern(events, max_events=4096):
    """Smallest k such that the event sequence repeats every k events with a
    constant cycle period P (checked in the middle of the capture). Returns
    (pattern, period) with the pattern rotated to start at the event with the
    largest gap before it, or (None, None)."""
    n = len(events)
    if n < 8:
        return None, None
    r0 = n // 4
    sig = [(e.ch, e.pol) for e in events]
    cyc = [e.cycle for e in events]
    for k in range(1, min(max_events, (n - r0) // 3) + 1):
        period = cyc[r0 + k] - cyc[r0]
        if period <= 0:
            continue
        length = min(n - r0 - k, 3 * k + 64)
        ok = True
        for i in range(r0, r0 + length):
            if sig[i] != sig[i + k] or cyc[i + k] - cyc[i] != period:
                ok = False
                break
        if ok:
            block = events[r0:r0 + k]
            gaps = []
            for j in range(k):
                prev = block[j - 1].cycle - (period if j == 0 else 0)
                gaps.append(block[j].cycle - prev)
            anchor = max(range(k), key=lambda j: (gaps[j], -j))
            rot = block[anchor:] + block[:anchor]
            base = rot[0].cycle
            pattern = [((e.cycle - base) % period if idx else 0, e.ch, e.pol) for idx, e in enumerate(rot)]
            pattern.sort(key=lambda p: (p[0], p[1]))
            return pattern, period
    return None, None


def frames_against(events, pattern, period):
    """Compare the capture with a frame pattern.

    local: split the capture at anchors (edges with the pattern's first
    signature and at least its quiet gap before them) and count frames whose
    events and length equal the pattern.
    grid: from the first frame that equals the pattern, is every later event
    exactly where the periodic schedule puts it? Reports the first departure.
    """
    k = len(pattern)
    sig = [(p[1], p[2]) for p in pattern]
    offsets = [p[0] for p in pattern]
    anchor_sig = sig[0]
    anchor_gap = period - (offsets[-1] if k > 1 else 0)
    n = len(events)
    want = [tuple(p) for p in pattern]
    # An anchor needs its quiet gap observed, so a capture's first edge is never one.
    anchors = [i for i in range(1, n)
               if (events[i].ch, events[i].pol) == anchor_sig
               and events[i].cycle - events[i - 1].cycle >= anchor_gap]
    frames = []
    for a, b in zip(anchors, anchors[1:]):
        base = events[a].cycle
        body = [(events[i].cycle - base, events[i].ch, events[i].pol) for i in range(a, b)]
        frames.append((base, events[b].cycle - base, body, a))
    identical = [f for f in frames if f[1] == period and f[2] == want]
    start = identical[0][3] if identical else None
    result = {"pattern_found": start is not None, "events_before_first_frame": start,
              "frames_complete": len(frames), "frames_identical": len(identical),
              "frame_lengths": dict(sorted(Counter(f[1] for f in frames).items()))}
    departure = None
    if start is None:
        if frames:
            departure = {"event_index": frames[0][3], "cycle": frames[0][0], "cycle_from_first_frame": 0,
                         "frame": 0, "note": "no frame equals the pattern"}
        result["on_grid_events"] = 0
    else:
        c0 = events[start].cycle
        first_frame = next(j for j, f in enumerate(frames) if f[3] == start)
        for idx in range(start, n):
            j = idx - start
            f, m = divmod(j, k)
            expect_cycle = c0 + f * period + offsets[m]
            e = events[idx]
            if (e.ch, e.pol) != sig[m] or e.cycle != expect_cycle:
                departure = {"event_index": idx, "cycle": e.cycle, "cycle_from_first_frame": e.cycle - c0,
                             "frame": first_frame + f, "expected_cycle": expect_cycle,
                             "expected": [offsets[m], sig[m][0], sig[m][1]],
                             "got": [e.cycle - (c0 + f * period), e.ch, e.pol]}
                break
        result["on_grid_events"] = (departure["event_index"] if departure else n) - start
    result["grid_departure"] = departure
    result["_anchors"] = anchors
    return result, [(f[0], f[1], f[2]) for f in frames]


def interval_histograms(events, channels):
    """Per channel: durations high (rise->fall) and low (fall->rise) in cycles."""
    per = {name: {"high": Counter(), "low": Counter()} for name in channels}
    last = {}
    for e in events:
        if e.ch in last:
            prev = last[e.ch]
            kind = "high" if prev.pol == 1 else "low"
            per[channels[e.ch]][kind][e.cycle - prev.cycle] += 1
        last[e.ch] = e
    return {name: {kind: dict(sorted(c.items())) for kind, c in d.items()} for name, d in per.items()}


def frame_histograms(events, lo, hi, names, into):
    """Add the intervals that start at events[lo:hi] (to the next edge of the
    same channel, wherever it is) into the ``into`` counters."""
    nxt = {}
    following = [None] * len(events)
    for i in range(len(events) - 1, -1, -1):
        following[i] = nxt.get(events[i].ch)
        nxt[events[i].ch] = i
    for i in range(lo, hi):
        j = following[i]
        if j is None:
            continue
        e = events[i]
        into[names[e.ch]]["high" if e.pol == 1 else "low"][events[j].cycle - e.cycle] += 1


def class_stats(frames, pattern, period):
    """Jitter per interval class: for each event position in the frame, the
    offset from the frame anchor across all structurally matching frames."""
    k = len(pattern)
    rows = [[] for _ in range(k)]
    lengths = []
    matched = 0
    for _, length, body in frames:
        if len(body) != k or [(b[1], b[2]) for b in body] != [(p[1], p[2]) for p in pattern]:
            continue
        matched += 1
        lengths.append(length)
        for j, b in enumerate(body):
            rows[j].append(b[0])
    out = []
    for j, p in enumerate(pattern):
        vals = rows[j]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        out.append({"position": j, "channel": p[1], "polarity": p[2], "expected_offset": p[0],
                    "min": min(vals), "max": max(vals), "peak_to_peak": max(vals) - min(vals),
                    "std": math.sqrt(var)})
    pp = max((r["peak_to_peak"] for r in out), default=None)
    flen = {"min": min(lengths), "max": max(lengths)} if lengths else None
    return {"frames_matched": matched, "max_peak_to_peak_cycles": pp, "frame_length": flen,
            "positions": out}


def decode_uart(events_ch, initial, bit, first=None, last=None):
    """8N1 from one channel's cycle-domain edges. Returns (bytes, framing errors).
    Within a capture that starts at cycle ``first`` and ends at ``last``, frames
    that may have started before the capture (an edge in the first ten bit
    times) or end after it are skipped."""
    cyc = [e.cycle for e in events_ch]
    lev = [e.pol for e in events_ch]

    def level(c):
        i = bisect.bisect_right(cyc, c) - 1
        return lev[i] if i >= 0 else (initial if initial is not None else 1)

    out, errors = [], 0
    i = 0
    n = len(cyc)
    while i < n:
        if lev[i] != 0:
            i += 1
            continue
        c0 = cyc[i]
        if level(c0 + bit // 2) != 0 or (first is not None and c0 < first + 10 * bit
                                          and cyc[0] < first + 10 * bit):
            i += 1
            continue
        if last is not None and c0 + 10 * bit > last:
            break
        byte = 0
        for b in range(8):
            byte |= level(c0 + bit * (b + 1) + bit // 2) << b
        if level(c0 + bit * 9 + bit // 2) != 1:
            errors += 1
        out.append(byte)
        stop = c0 + bit * 9 + bit // 2
        i = bisect.bisect_right(cyc, stop)
    return out, errors


def activity(events_ch, initial, first, last):
    span = max(1, last - first)
    high = 0
    level = initial if initial in (0, 1) else 0
    t = first
    for e in events_ch:
        if e.cycle < first:
            level = e.pol
            continue
        if level:
            high += min(e.cycle, last) - t
        t = min(e.cycle, last)
        level = e.pol
    if level:
        high += last - t
    return {"edges": len(events_ch), "edges_per_1000_cycles": 1000 * len(events_ch) / span,
            "high_fraction": high / span}


def analyze(args):
    caps = [read_capture(p, args.format, args.samplerate) for p in args.captures]
    channels = parse_map(args.channels)
    if not channels:
        raise CaptureError("--channels is required")
    context = parse_map(args.context)
    names = [c[0] for c in channels]
    all_events = []
    segments = []
    seg_start = 0
    ctx_segments = []          # per capture: {name: (initial level, events)}
    conversion = []
    time_domain = {name: Counter() for name in names}
    cycle_offset = 0
    for cap in caps:
        start = end = None
        per_ch = []
        initials = {}
        gate = None
        if args.gate:
            g = [(t, v) for t, v in cap.resolve(args.gate)]
            gate = ([t for t, _ in g], [v for _, v in g])
        if args.window_ns:
            a_ns, b_ns = (float(x) for x in args.window_ns.split(":"))
            start = math.ceil(Fraction(a_ns) / (cap.tick_seconds * 10**9))
            end = math.floor(Fraction(b_ns) / (cap.tick_seconds * 10**9))
        for name, spec in channels + context:
            ev, init = edges_of(cap.resolve(spec), start, end)
            if gate is not None:
                ev = [(t, v) for t, v in ev if _level(gate, t) == 1]
            per_ch.append((name, ev))
            initials[name] = init
        ticks = sorted({t for _, ev in per_ch for t, _ in ev})
        if args.clock:
            clock_changes = cap.resolve(args.clock)
            cycles, active = clock_cycles(clock_changes, ticks, args.clock_edge)
            m = dict(zip(ticks, cycles))
            periods = [b - a for a, b in zip(active, active[1:])]
            conv = {"mode": "clock", "clock": args.clock, "clock_edges": len(active)}
            cedges = edges_of(clock_changes)[0]
            phases = [b[0] - a[0] for a, b in zip(cedges, cedges[1:])]
            if phases and cap.samplerate:
                # A clock phase shorter than two samples risks a missed edge.
                conv["shortest_clock_phase_samples"] = min(phases)
            if periods:
                ts = float(cap.tick_seconds) * 1e9
                conv["clock_period_ns"] = {"min": min(periods) * ts, "max": max(periods) * ts,
                                           "mean": sum(periods) / len(periods) * ts}
        elif args.fclk:
            rate = cap.samplerate or (1 / cap.tick_seconds)
            ratio = float(rate) / float(args.fclk)
            if ratio < 2.2 and not args.force:
                raise CaptureError(
                    f"fs/fclk = {ratio:.3f}: cycle recovery needs fs >= 2.2 fclk (at fs = 2 fclk the clock "
                    "and its alias fs - fclk coincide); use a faster analyser, a slower design clock, "
                    "or a host-clocked build with --clock (docs/demo.md). --force overrides")
            probe_ticks = sorted({t for name, ev in per_ch if name in names for t, _ in ev})
            tolerance = max(0.25, args.tolerance_ns * 1e-9 * float(rate))
            ns, info = recover_cycles(probe_ticks, ratio, tolerance=tolerance)
            m = dict(zip(probe_ticks, ns))
            # Context channels: nearest cycle with the estimated ratio (not validated).
            est = info.get("estimated_ratio") or ratio
            t0, n0 = (probe_ticks[0], ns[0]) if probe_ticks else ((ticks[0], 0) if ticks else (0, 0))
            for t in ticks:
                if t not in m:
                    m[t] = n0 + int(math.floor((t - t0) / est + 0.5))
            conv = dict(info, mode="realtime", fclk_hz=float(args.fclk), samplerate_hz=float(rate))
            if not info.get("consistent") and not args.force:
                raise CaptureError(f"cycle recovery inconsistent: {info}")
        else:
            m = {t: t for t in ticks}
            conv = {"mode": "samples"}
        conversion.append(dict(conv, capture=cap.path.name))
        seg_ctx = {}
        ctx_segments.append(seg_ctx)
        for k, (name, ev) in enumerate(per_ch):
            if name in names:
                ch = names.index(name)
                prev = None
                for t, v in ev:
                    all_events.append(Event(m[t] + cycle_offset, ch, v, t))
                    if prev is not None and cap.samplerate:
                        time_domain[name][t - prev] += 1
                    prev = t
            else:
                seg_ctx[name] = (initials[name], [Event(m[t] + cycle_offset, -1, v, t) for t, v in ev])
        seg_events = all_events[seg_start:]
        seg_events.sort(key=lambda e: (e.cycle, e.ch))
        segments.append(seg_events)
        # Separate captures never share a frame: leave a gap of a million cycles.
        last = max((e.cycle for e in all_events), default=cycle_offset)
        cycle_offset = last + 1_000_000
        seg_start = len(all_events)
    all_events.sort(key=lambda e: (e.cycle, e.ch))
    report = {"tool": TOOL, "version": VERSION, "label": args.label,
              "inputs": [{"file": c.path.name, "format": c.format,
                          "samplerate_hz": float(c.samplerate) if c.samplerate else None,
                          "tick_seconds": float(c.tick_seconds), "sha256": file_sha256(c.path)} for c in caps],
              "channels": dict(channels), "context_channels": dict(context), "conversion": conversion,
              "events": len(all_events), "captures": len(caps)}
    if args.skip_cycles:
        trimmed = []
        for seg in segments:
            if seg:
                first = seg[0].cycle
                seg = [e for e in seg if e.cycle >= first + args.skip_cycles]
            trimmed.append(seg)
        segments = trimmed
    if args.max_cycles:
        segments = [[e for e in seg if e.cycle <= seg[0].cycle + args.max_cycles] if seg else seg
                    for seg in segments]
    all_events = [e for seg in segments for e in seg]
    report["events_analysed"] = len(all_events)
    unit = "cycles" if (args.clock or args.fclk) else "samples"
    report["unit"] = unit
    report["span_cycles"] = sum(seg[-1].cycle - seg[0].cycle for seg in segments if seg)
    hist = {name: {"high": Counter(), "low": Counter()} for name in names}
    for seg in segments:
        for name, kinds in interval_histograms(seg, names).items():
            for kind, h in kinds.items():
                hist[name][kind].update(h)
    report["histograms"] = {n: {k: dict(sorted(c.items())) for k, c in d.items()} for n, d in hist.items()}
    if unit == "cycles" and any(c.samplerate for c in caps):
        report["time_domain_histograms_samples"] = {k: dict(sorted(v.items())) for k, v in time_domain.items()}
    pattern = period = None
    source = None
    if args.reference:
        ref = json.loads(Path(args.reference).read_text())
        pattern, period = [tuple(p) for p in ref["pattern"]["events"]], ref["pattern"]["period_cycles"]
        pattern = [(p[0], names.index(ref["pattern"]["channels"][p[1]]), p[2]) for p in pattern]
        source = "reference:" + Path(args.reference).name
    elif unit == "cycles":
        for seg in sorted(segments, key=len, reverse=True):
            pattern, period = detect_pattern(seg)
            if pattern:
                break
        source = "self"
    if pattern:
        agg = {"pattern_found": False, "frames_complete": 0, "frames_identical": 0, "on_grid_events": 0,
               "frame_lengths": Counter(), "grid_departure": None, "segments": []}
        frames = []
        per_frame = {name: {"high": Counter(), "low": Counter()} for name in names}
        for k, seg in enumerate(segments):
            info_k, frames_k = frames_against(seg, pattern, period)
            anchors = info_k.pop("_anchors")
            if len(anchors) >= 2:
                frame_histograms(seg, anchors[0], anchors[-1], names, per_frame)
            agg["pattern_found"] |= info_k["pattern_found"]
            agg["frames_complete"] += info_k["frames_complete"]
            agg["frames_identical"] += info_k["frames_identical"]
            agg["on_grid_events"] += info_k["on_grid_events"]
            agg["frame_lengths"].update(info_k["frame_lengths"])
            if info_k["grid_departure"] and agg["grid_departure"] is None:
                agg["grid_departure"] = dict(info_k["grid_departure"], capture=k)
            agg["segments"].append({"capture": k, "events": len(seg), "frames": info_k["frames_complete"],
                                    "identical": info_k["frames_identical"],
                                    "departure_frame": (info_k["grid_departure"] or {}).get("frame")})
            frames += frames_k
            if frames_k:
                b0 = frames_k[0][0]
                agg.setdefault("frame_offsets", []).append(
                    [f[0] - b0 - j * period for j, f in enumerate(frames_k[:20000])])
        agg["frame_lengths"] = dict(sorted(agg["frame_lengths"].items()))
        report["pattern"] = {"source": source, "period_cycles": period, "events_per_frame": len(pattern),
                             "channels": names, "events": [list(p) for p in pattern]}
        report["frames"] = agg
        report["jitter"] = class_stats(frames, pattern, period)
        nf = agg["frames_complete"]
        report["histograms_per_frame"] = {
            n: {k: {dt: round(c / nf, 4) for dt, c in sorted(cnt.items())} for k, cnt in d.items()}
            for n, d in per_frame.items()} if nf else None
    else:
        report["pattern"] = None
    ctx = {}
    # Context statistics cover each capture's extent: from its first to its
    # last edge on any analysed channel (the probe may stop early).
    extents = []
    for seg, seg_ctx in zip(segments, ctx_segments):
        cyc = [e.cycle for e in seg] + [e.cycle for _, evs in seg_ctx.values() for e in evs]
        extents.append((min(cyc), max(cyc)) if cyc else None)
    for name, _ in context:
        total = {"edges": 0, "cycles": 0, "high": 0.0}
        for ext, seg_ctx in zip(extents, ctx_segments):
            if not ext or name not in seg_ctx:
                continue
            init, evs = seg_ctx[name]
            a = activity(sorted(evs, key=lambda e: e.cycle), init, ext[0], ext[1])
            span = max(1, ext[1] - ext[0])
            total["edges"] += a["edges"]
            total["cycles"] += span
            total["high"] += a["high_fraction"] * span
        cycles = max(1, total["cycles"])
        ctx[name] = {"edges": total["edges"], "edges_per_1000_cycles": 1000 * total["edges"] / cycles,
                     "high_fraction": total["high"] / cycles}
    for spec in args.uart or []:
        name, bit = spec.split(":")
        data, errors = [], 0
        for ext, seg_ctx in zip(extents, ctx_segments):
            if name in seg_ctx and ext:
                init, evs = seg_ctx[name]
                bounds = ext
                d, e = decode_uart(sorted(evs, key=lambda x: x.cycle), init, int(bit), *bounds)
                data += d
                errors += e
        entry = ctx.setdefault(name, {})
        entry["uart"] = {"bit_cycles": int(bit), "bytes": len(data), "framing_errors": errors,
                         "first_bytes_hex": " ".join("%02x" % b for b in data[:24])}
        if args.uart_expect:
            allowed = {int(x, 16) for x in args.uart_expect.split(",")}
            entry["uart"]["bytes_outside_expected_set"] = sum(1 for b in data if b not in allowed)
    report["context"] = ctx
    return report


def _level(gate, tick):
    ticks, values = gate
    i = bisect.bisect_right(ticks, tick) - 1
    return values[i] if i >= 0 else None


def _per_frame_hist(pattern, period, names):
    """The interval histogram of one frame of the pattern (cyclic)."""
    hist = {name: {"high": Counter(), "low": Counter()} for name in names}
    for ch in range(len(names)):
        evs = [p for p in pattern if p[1] == ch]
        for j, p in enumerate(evs):
            nxt = evs[(j + 1) % len(evs)]
            dt = (nxt[0] - p[0]) % period or period
            hist[names[ch]]["high" if p[2] == 1 else "low"][dt] += 1
    return {n: {k: dict(sorted(c.items())) for k, c in d.items()} for n, d in hist.items()}


# =================================================================== report
def bar(n, scale, width=40):
    return "#" * max(1 if n else 0, round(width * n / scale)) if scale else ""


def text_report(rep):
    lines = []
    lab = rep.get("label") or ", ".join(i["file"] for i in rep["inputs"])
    lines.append(f"== {lab}")
    for c in rep["conversion"]:
        if c["mode"] == "clock":
            p = c.get("clock_period_ns", {})
            short = c.get("shortest_clock_phase_samples")
            lines.append(f"   {c['capture']}: cycles from clock channel {c['clock']} ({c['clock_edges']} edges; "
                         f"period {p.get('min', 0):.0f}..{p.get('max', 0):.0f} ns"
                         + (f"; shortest clock phase {short} samples" if short is not None else "") + ")")
            if short is not None and short < 2:
                lines.append("   WARNING: a clock phase shorter than 2 samples; raise the sample rate")
        elif c["mode"] == "realtime":
            arc = c['max_arc_samples']
            lines.append(f"   {c['capture']}: cycles recovered, fs/fclk {c['estimated_ratio']:.6f} "
                         f"({c['relative_offset_ppm']:+.1f} ppm vs nominal), max arc "
                         + (f"{arc:.3f}" if arc is not None else "-") +
                         f" samples (limit {c['arc_limit_samples']:.2f}), consistent={c['consistent']}")
        else:
            lines.append(f"   {c['capture']}: sample-domain intervals only")
    lines.append(f"   events {rep['events']}, unit {rep['unit']}")
    fr = rep.get("frames")
    if fr:
        pat = rep["pattern"]
        lines.append(f"   pattern ({pat['source']}): {pat['events_per_frame']} events per {pat['period_cycles']}-cycle frame")
        dep = fr["grid_departure"]
        lines.append(f"   frames: {fr['frames_complete']} complete, {fr['frames_identical']} identical to the pattern; "
                     f"frame lengths {fr['frame_lengths']}")
        if not fr.get("pattern_found"):
            lines.append("   schedule: no frame equals the pattern")
        elif not dep:
            lines.append("   schedule: every event from the first matching frame on is on the periodic schedule")
        else:
            lines.append(f"   schedule: first departure at event {dep['event_index']}, frame {dep['frame']}, "
                         f"cycle +{dep['cycle_from_first_frame']} (expected {dep.get('expected')}, got {dep.get('got')})")
        j = rep.get("jitter") or {}
        if j.get("max_peak_to_peak_cycles") is not None:
            lines.append(f"   jitter: max peak-to-peak over {j['frames_matched']} matching frames = "
                         f"{j['max_peak_to_peak_cycles']} cycles")
    for name, kinds in rep["histograms"].items():
        for kind, h in kinds.items():
            if not h:
                continue
            scale = max(h.values())
            lines.append(f"   {name} {kind} ({rep['unit']}):")
            for k, v in h.items():
                lines.append(f"     {int(k):>6} {v:>8} {bar(v, scale)}")
    for name, c in rep.get("context", {}).items():
        extra = ""
        if "uart" in c:
            u = c["uart"]
            extra = f"; UART {u['bytes']} bytes, {u['framing_errors']} framing errors, first {u['first_bytes_hex'][:47]}"
        if "edges" in c:
            lines.append(f"   context {name}: {c['edges']} edges ({c['edges_per_1000_cycles']:.2f}/1000 cycles), "
                         f"high {100 * c['high_fraction']:.1f}%{extra}")
    return "\n".join(lines)


# ================================================================== compare
def _norm_hist(rep):
    """Histograms normalised per complete frame when the run has frames."""
    return rep.get("histograms_per_frame") or rep["histograms"]


def _tv(rep, ref):
    """Largest total-variation distance between normalised interval histograms."""
    worst = 0.0
    for ch, kinds in ref["histograms"].items():
        for kind, h0 in kinds.items():
            h1 = rep["histograms"].get(ch, {}).get(kind, {})
            n0, n1 = sum(h0.values()), sum(h1.values())
            if not n0 or not n1:
                worst = max(worst, 1.0 if (n0 or n1) else 0.0)
                continue
            keys = set(h0) | set(h1)
            worst = max(worst, 0.5 * sum(abs(h0.get(k, 0) / n0 - h1.get(k, 0) / n1) for k in keys))
    return worst


def verdict(rep, ref_pattern, ref_period, ref_rep=None, tv_limit=0.05):
    if rep.get("unit") == "samples":
        if ref_rep is None or ref_rep is rep:
            return "same", []
        tv = _tv(rep, ref_rep)
        return ("same" if tv <= tv_limit else "different"), [f"statistical: histogram TV distance {tv:.4f} "
                                                              f"(limit {tv_limit})"]
    fr = rep.get("frames") or {}
    pat = rep.get("pattern")
    reasons = []
    if not pat:
        reasons.append("no periodic pattern")
    else:
        if ref_pattern is not None and ([tuple(p) for p in pat["events"]] != ref_pattern
                                        or pat["period_cycles"] != ref_period):
            reasons.append("frame pattern differs from the reference")
        if fr.get("grid_departure"):
            reasons.append("departs from the periodic schedule")
        if fr.get("frames_complete", 0) == 0:
            reasons.append("no complete frame")
        elif fr["frames_identical"] != fr["frames_complete"]:
            reasons.append(f"{fr['frames_complete'] - fr['frames_identical']} frames differ")
    return ("same" if not reasons else "different"), reasons


def _num(v):
    return str(int(v)) if float(v).is_integer() else f"{v:.3f}"


def compare(args):
    reps = [json.loads(Path(p).read_text()) for p in args.analyses]
    labels = args.labels.split(",") if args.labels else [r.get("label") or Path(p).stem for r, p in zip(reps, args.analyses)]
    if args.reference:
        ref = json.loads(Path(args.reference).read_text())
        ref_pattern = [tuple(p) for p in ref["pattern"]["events"]]
        ref_period = ref["pattern"]["period_cycles"]
        ref_name = Path(args.reference).name
    else:
        pat = reps[0].get("pattern")
        ref_pattern = [tuple(p) for p in pat["events"]] if pat else None
        ref_period = pat["period_cycles"] if pat else None
        ref_name = labels[0]
    out = [f"reference frame pattern: {ref_name}"
           + (f" ({len(ref_pattern)} events per {ref_period} cycles)" if ref_pattern else "")]
    verdicts = []
    for lab, rep in zip(labels, reps):
        v, reasons = verdict(rep, ref_pattern, ref_period, reps[0], args.tv_limit)
        verdicts.append(v)
        fr = rep.get("frames") or {}
        j = rep.get("jitter") or {}
        conv = rep["conversion"][0]["mode"] if rep["conversion"] else "?"
        out.append(f"{lab:>14}: {v.upper():9} frames {fr.get('frames_identical', 0)}/{fr.get('frames_complete', 0)} "
                   f"identical, events {rep['events']}, jitter p-p {j.get('max_peak_to_peak_cycles')} cycles, "
                   f"cycles from {conv}" + (f"; {'; '.join(reasons)}" if reasons else ""))
    # Side-by-side per-frame histograms.
    hists = [_norm_hist(r) for r in reps]
    chans = list(hists[0])
    out.append("")
    out.append("interval histograms, counts per frame (cycles)" if reps[0].get("histograms_per_frame")
               else "interval histograms, counts")
    for ch in chans:
        for kind in ("high", "low"):
            keys = sorted({int(k) for h in hists for k in h.get(ch, {}).get(kind, {})})
            if not keys:
                continue
            out.append(f"  {ch} {kind}: " + " | ".join(f"{lab:>10}" for lab in labels))
            for k in keys:
                vals = [h.get(ch, {}).get(kind, {}).get(str(k), h.get(ch, {}).get(kind, {}).get(k, 0)) for h in hists]
                out.append(f"  {k:>8}        " + " | ".join(f"{_num(v):>10}" for v in vals))
    expect = args.expect.split(",") if args.expect else None
    ok = True
    if expect:
        if len(expect) != len(reps):
            raise CaptureError("--expect needs one value per analysis")
        out.append("")
        for lab, v, e in zip(labels, verdicts, expect):
            match = v == e
            ok &= match
            out.append(f"{lab:>14}: expected {e}, got {v}: {'OK' if match else 'MISMATCH'}")
        out.append("COMPARE " + ("PASS" if ok else "FAIL"))
    text = "\n".join(out)
    print(text)
    if args.text:
        Path(args.text).write_text(text + "\n")
    if args.json:
        Path(args.json).write_text(json.dumps({"labels": labels, "verdicts": verdicts, "expect": expect,
                                               "pass": ok, "reference": ref_name}, indent=1) + "\n")
    if args.plot:
        plot(reps, labels, args.plot, args.title)
    return 0 if ok else 1


# ===================================================================== plot
# Categorical slots 1-4 of the dataviz reference palette (light surface).
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


def plot(reps, labels, path, title=None):
    """Three panels: (a) each frame's start relative to the undisturbed
    periodic schedule, (b) the distribution of frame lengths, (c) the
    edge-interval histograms per channel (counts per frame)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: no figure written", file=sys.stderr)
        return
    if len(reps) > len(SERIES):
        print(f"plot: at most {len(SERIES)} runs per figure; plotting the first {len(SERIES)}", file=sys.stderr)
        reps, labels = reps[:len(SERIES)], labels[:len(SERIES)]
    hists = [_norm_hist(r) for r in reps]
    chans = list(hists[0])
    fig = plt.figure(figsize=(10, 3.2 + 2.4 + 2.6), facecolor=SURFACE)
    grid = fig.add_gridspec(3, len(chans), height_ratios=[1.2, 1.0, 1.1], hspace=0.65, wspace=0.18)
    # (a) schedule offset per frame
    ax = fig.add_subplot(grid[0, :])
    boundaries = set()
    for i, (rep, lab) in enumerate(zip(reps, labels)):
        segs = (rep.get("frames") or {}).get("frame_offsets", [])
        offs = [o for seg in segs for o in seg]
        if not offs:
            continue
        n = 0
        for seg in segs[:-1]:
            n += len(seg)
            boundaries.add(n)
        ax.step(range(len(offs)), offs, where="post", color=SERIES[i], linewidth=2, label=lab)
        ax.annotate(lab, (len(offs) - 1, offs[-1]), xytext=(4, 10 * i), textcoords="offset points",
                    fontsize=8, color=INK2, va="center")
    for b in sorted(boundaries):
        ax.axvline(b, color=GRID, linewidth=0.8, linestyle="--", zorder=0)
    ax.set_title("(a) frame start minus the undisturbed schedule (cycles)"
                 + ("; restarts at each capture (dashed)" if boundaries else ""),
                 fontsize=9, color=INK, loc="left")
    ax.set_xlabel("frame index", color=INK2, fontsize=8)
    ax.set_ylabel("cycles", color=INK2, fontsize=8)
    _style(ax)
    # (b) frame lengths, fraction of frames
    ax = fig.add_subplot(grid[1, :])
    lengths = [{int(k): v for k, v in ((r.get("frames") or {}).get("frame_lengths") or {}).items()} for r in reps]
    keys = sorted({k for d in lengths for k in d})
    if len(keys) > 14:
        common = sorted(keys, key=lambda k: -sum(d.get(k, 0) for d in lengths))[:13]
        keys = sorted(common) + ["other"]
    width = 0.8 / len(reps)
    for i, (d, lab) in enumerate(zip(lengths, labels)):
        total = sum(d.values()) or 1
        vals = [(d.get(k, 0) if k != "other" else total - sum(d.get(c, 0) for c in keys[:-1])) / total
                for k in keys]
        ax.bar([x + (i - (len(reps) - 1) / 2) * width for x in range(len(keys))], vals, width=width * 0.9,
               color=SERIES[i], label=lab, edgecolor=SURFACE, linewidth=1)
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels([str(k) for k in keys], fontsize=8, color=INK2)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("fraction of frames", color=INK2, fontsize=8)
    ax.set_title("(b) frame length (cycles)", fontsize=9, color=INK, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    _style(ax)
    # (c) interval histograms per channel
    for c, ch in enumerate(chans):
        ax = fig.add_subplot(grid[2, c])
        cats = []
        for kind, tag in (("high", "H"), ("low", "L")):
            ks = sorted({int(k) for h in hists for k in h.get(ch, {}).get(kind, {})})
            cats += [(kind, k, f"{tag}{k}") for k in ks]
        common = [cat for cat in cats if any(float(h.get(ch, {}).get(cat[0], {}).get(str(cat[1]),
                  h.get(ch, {}).get(cat[0], {}).get(cat[1], 0))) >= 0.5 for h in hists[:1])]
        rare = [cat for cat in cats if cat not in common]
        shown = common + ([("rare", None, "other")] if rare else [])
        for i, (h, lab) in enumerate(zip(hists, labels)):
            d = h.get(ch, {})
            vals = []
            for kind, k, _ in shown:
                if kind == "rare":
                    vals.append(sum(float(d.get(rk, {}).get(str(rv), d.get(rk, {}).get(rv, 0))) for rk, rv, _ in rare))
                else:
                    vals.append(float(d.get(kind, {}).get(str(k), d.get(kind, {}).get(k, 0))))
            ax.bar([x + (i - (len(reps) - 1) / 2) * width for x in range(len(shown))], vals, width=width * 0.9,
                   color=SERIES[i], label=lab, edgecolor=SURFACE, linewidth=1)
        ax.set_xticks(range(len(shown)))
        ax.set_xticklabels([t for _, _, t in shown], fontsize=7, color=INK2, rotation=0)
        ax.set_title(f"(c) {ch}: high (H) / low (L) durations, per frame", fontsize=9, color=INK, loc="left")
        if c == 0:
            ax.set_ylabel("per frame", color=INK2, fontsize=8)
        _style(ax)
    if title:
        fig.suptitle(title, fontsize=10, color=INK, x=0.01, ha="left")
    fig.savefig(path, dpi=130, facecolor=SURFACE, bbox_inches="tight")
    print(f"figure: {path}")


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


# ================================================================== predict
def predict(args):
    """Frame pattern of a boundary-free periodic image, from tools/timing/pe_timing.py."""
    import subprocess
    import tempfile
    image = Path(args.image)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "timing.json"
        subprocess.run([sys.executable, str(REPO / "tools" / "timing" / "pe_timing.py"), "analyze",
                        str(image), "--json", str(out)], check=True, stdout=subprocess.DEVNULL,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        rep = json.loads(out.read_text())
    if isinstance(rep, list):
        rep = rep[0]
    seg = rep["segments"][0]
    m = re.search(r"period (\d+) from \+(\d+)", seg["end"])
    if not m:
        raise CaptureError(f"{image.name}: not a boundary-free periodic program ({seg['end']})")
    period, start = int(m.group(1)), int(m.group(2))
    pins = dict((name, int(spec)) for name, spec in parse_map(args.channels))
    names = list(pins)
    evs = []
    for off, pin, a, b, *_ in seg["pads"]:
        if start <= off < start + period and a in "01" and b in "01" and a != b and pin in pins.values():
            ch = names.index([n for n in names if pins[n] == pin][0])
            evs.append(Event(off, ch, int(b), off))
    evs.sort(key=lambda e: (e.cycle, e.ch))
    extended = [Event(e.cycle + k * period, e.ch, e.pol, 0) for k in range(4) for e in evs]
    pattern, p = detect_pattern(extended)
    if pattern is None or p != period:
        raise CaptureError("could not form a frame pattern from the static schedule")
    out = {"tool": TOOL, "version": VERSION, "label": "predicted (pe_timing)",
           "image": image.name, "pattern": {"source": "pe_timing:" + image.name, "period_cycles": period,
                                            "events_per_frame": len(pattern), "channels": names,
                                            "events": [list(q) for q in pattern]},
           "histograms_per_frame": _per_frame_hist(pattern, period, names), "events": len(pattern),
           "conversion": [{"mode": "static"}], "unit": "cycles",
           "frames": {"frames_complete": 1, "frames_identical": 1, "grid_departure": None,
                      "frame_lengths": {str(period): 1}},
           "jitter": {"max_peak_to_peak_cycles": 0, "frames_matched": 1}}
    Path(args.json).write_text(json.dumps(out, indent=1) + "\n")
    print(f"{image.name}: {len(pattern)} events per {period}-cycle frame -> {args.json}")
    return 0


# ================================================================= resample
def write_srzip(path, rate, names, changes, total, unitsize=1):
    """Write a sigrok srzip session from per-sample unit-value changes."""
    data = bytearray()
    cur = 0
    last_idx = 0
    for idx, value in changes:
        data += bytes([cur]) * (idx - last_idx) if unitsize == 1 else \
            cur.to_bytes(unitsize, "little") * (idx - last_idx)
        cur, last_idx = value, idx
    data += bytes([cur]) * (total - last_idx) if unitsize == 1 else cur.to_bytes(unitsize, "little") * (total - last_idx)
    rate_text = _rate_text(rate)
    meta = ["[global]", "sigrok version=0.5.2", "", "[device 1]", "capturefile=logic-1",
            f"total probes={8 * unitsize}", f"samplerate={rate_text}", "total analog=0"]
    meta += [f"probe{k + 1}={n}" for k, n in enumerate(names)]
    meta += [f"unitsize={unitsize}", ""]
    chunk = 4 << 20
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version", "2")
        z.writestr("metadata", "\n".join(meta))
        for k in range(0, max(1, len(data)), chunk):
            z.writestr(f"logic-1-{k // chunk + 1}", bytes(data[k:k + chunk]))


def _rate_text(rate):
    rate = Fraction(rate)
    for scale, unit in ((10**9, "GHz"), (10**6, "MHz"), (10**3, "kHz")):
        if rate >= scale:
            v = rate / scale
            return f"{float(v):g} {unit}"
    return f"{float(rate):g} Hz"


def resample(args):
    """Emulate a logic analyser on an exact-time capture: sample at
    rate*(1+ppm*1e-6) starting at a phase offset; a pin change at time T is
    seen by the first sample at or after T (plus optional Gaussian jitter)."""
    import random
    cap = read_capture(args.input, args.format)
    if cap.samplerate:
        raise CaptureError("resample needs an exact-time (simulation) VCD")
    chans = parse_map(args.channels)
    rate = Fraction(args.rate) * (1 + Fraction(str(args.ppm)) / 10**6)
    period_s = 1 / rate
    ts = cap.tick_seconds
    rng = random.Random(args.seed)
    window = [Fraction(x) / 10**9 for x in args.window_ns.split(":")] if args.window_ns else None
    phase = Fraction(str(args.phase)) * period_s + (window[0] if window else 0)
    jitter = args.jitter_ps * 1e-12
    per = []
    for k, (name, spec) in enumerate(chans):
        for tick, v in cap.resolve(spec):
            t = tick * ts
            if jitter:
                t += Fraction(rng.gauss(0.0, jitter)).limit_denominator(10**15)
            idx = math.ceil((t - phase) / period_s)
            per.append((max(0, idx), k, v))
    per.sort()
    end_time = window[1] if window else cap.end_tick * ts
    total = math.ceil((end_time - phase) / period_s) + 1
    per = [x for x in per if x[0] < total]
    cur = 0
    changes = []
    for idx, k, v in per:
        new = (cur & ~(1 << k)) | ((v or 0) << k)
        if changes and changes[-1][0] == idx:
            changes[-1] = (idx, new)
        elif new != cur:
            changes.append((idx, new))
        cur = new
    unitsize = 1 if len(chans) <= 8 else 2
    names = [n for n, _ in chans]
    seg = args.segment_samples or total
    outputs = []
    for k, first in enumerate(range(0, total, seg)):
        last = min(total, first + seg)
        part = [(0, _value_at(changes, first))] + [(i - first, v) for i, v in changes if first < i < last]
        out = args.output if not args.segment_samples else \
            re.sub(r"(\.sr)?$", f"-{k + 1:03d}.sr", args.output, count=1)
        write_srzip(out, Fraction(args.rate), names, part, last - first, unitsize)
        outputs.append(Path(out).name)
    print(f"{', '.join(outputs[:3])}{' ...' if len(outputs) > 3 else ''}: {total} samples at "
          f"{_rate_text(Fraction(args.rate))} in {len(outputs)} file(s) "
          f"(analyser clock {float(rate):.3f} Hz, {args.ppm:+g} ppm, phase {args.phase}), "
          f"{len(changes)} value changes")
    return 0


def _value_at(changes, index):
    value = 0
    for i, v in changes:
        if i > index:
            break
        value = v
    return value


def info(args):
    cap = read_capture(args.capture, args.format, args.samplerate)
    print(f"{cap.path.name}: format {cap.format}, "
          + (f"sample rate {_rate_text(cap.samplerate)}, {cap.end_tick} samples" if cap.samplerate
             else f"tick {float(cap.tick_seconds):g} s, end tick {cap.end_tick}"))
    for name in cap.names():
        s = cap.signals[name]
        print(f"  {name} width {s.width} changes {len(s.changes)}")
    return 0


# ===================================================================== main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("analyze", help="captures -> analysis JSON")
    p.add_argument("captures", nargs="+")
    p.add_argument("--channels", required=True, help="probe channels, e.g. probe6=D1,probe7=D2")
    p.add_argument("--context", help="other channels to summarise, e.g. uart=D3,wvalid=D5")
    p.add_argument("--clock", help="clock channel (host-clocked builds)")
    p.add_argument("--clock-edge", choices=("rising", "falling"), default="rising")
    p.add_argument("--fclk", type=float, help="design clock in Hz (free-running builds)")
    p.add_argument("--force", action="store_true", help="allow fs/fclk < 2.2 and inconsistent recovery")
    p.add_argument("--tolerance-ns", type=float, default=2.0,
                   help="edge timing tolerance (analyser jitter, channel skew) for --fclk; at least 0.25 samples")
    p.add_argument("--reference", help="analysis or predict JSON whose frame pattern to check against")
    p.add_argument("--uart", action="append", help="decode 8N1 on a context channel: NAME:BITCYCLES")
    p.add_argument("--uart-expect", help="comma-separated hex bytes the UART may carry")
    p.add_argument("--skip-cycles", type=int, default=0, help="ignore events in the first N cycles")
    p.add_argument("--max-cycles", type=int, default=0, help="ignore events more than N cycles after a capture's first")
    p.add_argument("--window-ns", help="only edges between A and B ns from the start of each capture (A:B)")
    p.add_argument("--gate", help="only edges while this channel is high (the Pico's phase marker)")
    p.add_argument("--format", choices=("srzip", "vcd", "csv"))
    p.add_argument("--samplerate", type=parse_rate, help="override or supply the sample rate")
    p.add_argument("--label")
    p.add_argument("--json", help="write the analysis here")
    p.add_argument("--quiet", action="store_true")
    p = sub.add_parser("compare", help="analysis JSONs -> side-by-side comparison")
    p.add_argument("analyses", nargs="+")
    p.add_argument("--labels")
    p.add_argument("--expect", help="comma-separated same|different per analysis")
    p.add_argument("--reference", help="predict JSON (default: the first analysis)")
    p.add_argument("--plot", help="PNG figure (matplotlib)")
    p.add_argument("--tv-limit", type=float, default=0.05,
                   help="sample-domain runs: largest histogram total-variation distance counted as same")
    p.add_argument("--title")
    p.add_argument("--text", help="also write the text report here")
    p.add_argument("--json", help="verdicts as JSON")
    p = sub.add_parser("predict", help="frame pattern from the static timing analyser")
    p.add_argument("--image", required=True)
    p.add_argument("--channels", default="probe6=6,probe7=7", help="NAME=PIN,...")
    p.add_argument("--json", required=True)
    p = sub.add_parser("resample", help="exact-time VCD -> srzip as sampled by an analyser")
    p.add_argument("input")
    p.add_argument("--channels", required=True, help="NAME=SIGNAL,... (NAME becomes the sigrok channel name)")
    p.add_argument("--rate", type=parse_rate, required=True)
    p.add_argument("--ppm", type=float, default=0.0, help="analyser clock offset")
    p.add_argument("--phase", type=float, default=0.0, help="first sample, in sample periods")
    p.add_argument("--jitter-ps", type=float, default=0.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--segment-samples", type=int, help="split into files of N samples (analyser depth)")
    p.add_argument("--window-ns", help="capture only from A to B ns of simulation time (A:B)")
    p.add_argument("--format", choices=("vcd",))
    p.add_argument("-o", "--output", required=True)
    p = sub.add_parser("info", help="list a capture's channels")
    p.add_argument("capture")
    p.add_argument("--format", choices=("srzip", "vcd", "csv"))
    p.add_argument("--samplerate", type=parse_rate)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "analyze":
            rep = analyze(a)
            if a.json:
                Path(a.json).write_text(json.dumps(rep, indent=1) + "\n")
            if not a.quiet:
                print(text_report(rep))
            return 0
        if a.cmd == "compare":
            return compare(a)
        if a.cmd == "predict":
            return predict(a)
        if a.cmd == "resample":
            return resample(a)
        if a.cmd == "info":
            return info(a)
    except CaptureError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
