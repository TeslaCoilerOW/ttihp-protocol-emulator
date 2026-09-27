# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Line-unit test support (docs/extension.md): instruction encoders, the
firmware/ext image loader, independent bit-level references and a
behavioural CAN node.

The references (CRCs, NRZI, bit stuffing, Manchester, CAN/USB/Ethernet
framing) are written from the protocol specifications, independently of both
the RTL and the reference model (model/line_unit.py). The tests compare pad
waveforms and read-back values against them, and the harness compares the
RTL with the model on every cycle.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Union

import variants

REPO = Path(__file__).resolve().parent.parent
EXT_FIRMWARE = REPO / "firmware" / "ext"


def line_unit_enabled() -> bool:
    """True when the design under test (PE_VARIANT) has the line unit."""
    return variants.options(variants.design_config()).line_unit != "none"


SKIP_REASON = "PE_VARIANT has no line unit (options.line_unit); see docs/extension.md"

# ------------------------------------------------------------------ encoders
TX, RX, X, Y = range(4)


def ins(op: int, a: int = 0, b: int = 0, c: int = 0) -> int:
    for v in (op, a, b, c):
        if not 0 <= v <= 255:
            raise ValueError("field outside a byte")
    return op << 24 | a << 16 | b << 8 | c


def imm(op: int, value: int = 0) -> int:
    if not 0 <= value < 1 << 24:
        raise ValueError("immediate outside 24 bits")
    return op << 24 | value


NOP, HALT = ins(0), imm(1)
PULL, PUSH, PUSH_STRICT = ins(6), ins(7), ins(7, 1)


def set_(v: int) -> int: return imm(2, v)
def dir_(v: int) -> int: return imm(3, v)
def wait(n: int) -> int: return imm(4, n)
def jmp(t: int) -> int: return imm(5, t)
def count(n: int) -> int: return imm(10, n)
def loop(t: int) -> int: return imm(11, t)
def limit(n: int) -> int: return imm(12, n)
def waitpin(p: int, v: int) -> int: return ins(13, p, v)
def pins(ck: int, out: int, inp: int) -> int: return imm(16, ck | out << 3 | inp << 6)
def xfer(a: int, b: int, c: int) -> int: return ins(17, a, b, c)
def mov(d: int, s: int) -> int: return ins(18, d, s)
def load(d: int, v: int) -> int: return ins(19, d) | (v & 0xFFFF)
def add(d: int, s: int) -> int: return ins(20, d, s)
def xor(d: int, s: int) -> int: return ins(21, d, s)
def and_(d: int, s: int) -> int: return ins(22, d, s)
def or_(d: int, s: int) -> int: return ins(23, d, s)
def shl(d: int, n: int) -> int: return ins(24, d, 0, n)
def shr(d: int, n: int) -> int: return ins(25, d, 0, n)
def jz(r: int, t: int) -> int: return 26 << 24 | r << 16 | t
def not_(d: int) -> int: return ins(27, d)
def fault(code: int) -> int: return imm(29, code)


# extension (docs/extension.md)
def ltim(p: int, frac: int = 0, delay: int = 0) -> int:
    return imm(30, p | frac << 8 | delay << 16)


def lcfg(code: int = 0, stuff: int = 0, either: bool = False, pair: bool = False, arb: bool = False,
         se0: bool = False, init: int = 0) -> int:
    value = code | (4 | (stuff - 1) << 4 if stuff else 0) | 8 * either | 0x80 * pair
    return imm(31, value | 0x100 * arb | 0x200 * se0 | init << 10)


def crc_set(r: int) -> int: return ins(32, 0, r, 1)
def crc_get(r: int) -> int: return ins(32, r, 0, 2)
def crc_preset(k: int) -> int: return ins(32, 0, k, 3)
def lstat(r: int) -> int: return ins(33, r)


def xline(bits: int, *, msb: bool = False, drive: bool = False, sample: bool = False,
          crc: bool = False) -> int:
    return xfer(bits, 0, 0x20 | 4 * msb | 8 * drive | 0x10 * sample | 0x40 * crc)


# LSTAT bits
ST_SE0, ST_LOST, ST_STUFF_ERR, ST_TX_DATA, ST_RX_SPACE, ST_LEVEL, ST_RUN = (1 << k for k in range(7))

Item = Union[int, str, Callable[[Callable[[str], int]], int]]


def assemble(items: Iterable[Item]) -> list[int]:
    """Words, labels (str) and label-resolving callables."""
    items = list(items)
    labels: dict[str, int] = {}
    pc = 0
    for item in items:
        if isinstance(item, str):
            labels[item] = pc
        else:
            pc += 1
    return [item(labels.__getitem__) if callable(item) else item for item in items
            if not isinstance(item, str)]


# ------------------------------------------------------------------ firmware/ext
def load_ext_image(name: str) -> dict[str, Any]:
    """firmware/ext/<name>.image.json with its source and bytecode identity checked."""
    image = json.loads((EXT_FIRMWARE / f"{name}.image.json").read_text())
    source = (EXT_FIRMWARE / f"{name}.source.json").read_bytes()
    if image["schema_version"] != "protocol-emulator.firmware-image.v1":
        raise ValueError(f"{name}: not a firmware image")
    if hashlib.sha256(source).hexdigest() != image["source_sha256"]:
        raise ValueError(f"firmware/ext/{name}: source identity mismatch")
    payload = b"".join(word.to_bytes(4, "little") for word in image["words"])
    if hashlib.sha256(payload).hexdigest() != image["bytecode_sha256"]:
        raise ValueError(f"firmware/ext/{name}: bytecode identity mismatch")
    return image


# ------------------------------------------------------------------ references
def bits_lsb(value: int, n: int) -> list[int]:
    return [value >> k & 1 for k in range(n)]


def bits_msb(value: int, n: int) -> list[int]:
    return [value >> (n - 1 - k) & 1 for k in range(n)]


def from_bits_lsb(bits: list[int]) -> int:
    return sum(b << k for k, b in enumerate(bits))


def from_bits_msb(bits: list[int]) -> int:
    value = 0
    for b in bits:
        value = value << 1 | b
    return value


def reflect(value: int, width: int) -> int:
    return from_bits_msb(bits_lsb(value, width))


def crc_catalogue(data: bytes, *, width: int, poly: int, init: int, refin: bool, refout: bool,
                  xorout: int) -> int:
    """The parametrised CRC model of R. Williams' "A painless guide to CRC
    error detection algorithms" (the form used by CRC catalogues): poly is
    the normal (MSB-first) polynomial without its top bit."""
    crc, mask = init, (1 << width) - 1
    for byte in data:
        if refin:
            byte = reflect(byte, 8)
        for k in range(8):
            feedback = (crc >> (width - 1) & 1) ^ (byte >> (7 - k) & 1)
            crc = (crc << 1) & mask
            if feedback:
                crc ^= poly
    if refout:
        crc = reflect(crc, width)
    return crc ^ xorout


@dataclass(frozen=True)
class CrcVector:
    """A catalogued CRC and its published check value over b"123456789"."""
    name: str
    width: int
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int
    check: int
    preset: int  # line-unit preset with this polynomial


# Check values as published in the CRC RevEng catalogue (and R. Williams'
# guide for CRC-16/ARC); reproduced by crc_catalogue in test_line_unit.
CRC_VECTORS = (
    CrcVector("CRC-16/IBM-3740 (CCITT-FALSE)", 16, 0x1021, 0xFFFF, False, False, 0x0000, 0x29B1, 3),
    CrcVector("CRC-16/XMODEM", 16, 0x1021, 0x0000, False, False, 0x0000, 0x31C3, 3),
    CrcVector("CRC-16/KERMIT", 16, 0x1021, 0x0000, True, True, 0x0000, 0x2189, 3),
    CrcVector("CRC-16/IBM-SDLC (X-25)", 16, 0x1021, 0xFFFF, True, True, 0xFFFF, 0x906E, 3),
    CrcVector("CRC-16/ARC", 16, 0x8005, 0x0000, True, True, 0x0000, 0xBB3D, 1),
    CrcVector("CRC-16/USB", 16, 0x8005, 0xFFFF, True, True, 0xFFFF, 0xB4C8, 1),
    CrcVector("CRC-16/UMTS", 16, 0x8005, 0x0000, False, False, 0x0000, 0xFEE8, 1),
    CrcVector("CRC-15/CAN", 15, 0x4599, 0x0000, False, False, 0x0000, 0x059E, 2),
    CrcVector("CRC-5/USB", 5, 0x05, 0x1F, True, True, 0x1F, 0x19, 0),
)


def crc_bits_reflected(bits: Iterable[int], *, poly: int, init: int) -> int:
    crc = init
    for b in bits:
        feedback = (crc & 1) ^ b
        crc = (crc >> 1) ^ (poly if feedback else 0)
    return crc


def crc_bits_normal(bits: Iterable[int], *, width: int, poly: int, init: int) -> int:
    crc, mask = init, (1 << width) - 1
    for b in bits:
        feedback = (crc >> (width - 1) & 1) ^ b
        crc = ((crc << 1) & mask) ^ (poly if feedback else 0)
    return crc


def stuff_ones(n: int, bits: Iterable[int]) -> list[int]:
    """USB/HDLC: a 0 after n consecutive 1s (also after the last data bit)."""
    out, run = [], 0
    for b in bits:
        out.append(b)
        run = run + 1 if b else 0
        if run == n:
            out.append(0)
            run = 0
    return out


def stuff_either(n: int, bits: Iterable[int], initial: int | None = None) -> list[int]:
    """CAN: the complement after n equal bits; the stuff bit starts the next run.
    ``initial``: the line level before the first bit (counts as its run's start
    only if equal; None = no previous bit)."""
    out: list[int] = []
    last, run = initial, 0
    for b in bits:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == n:
            out.append(1 - b)
            last, run = 1 - b, 1
    return out


def destuff_ones(n: int, bits: list[int]) -> tuple[list[int], bool]:
    """(data, error): drops the bit after n 1s; error if that bit is 1."""
    out, run, error, skip = [], 0, False, False
    for b in bits:
        if skip:
            error |= b != 0
            skip, run = False, 0
            continue
        out.append(b)
        run = run + 1 if b else 0
        if run == n:
            skip = True
    return out, error


def destuff_either(n: int, bits: list[int]) -> tuple[list[int], bool]:
    out: list[int] = []
    last, run, error, skip = None, 0, False, False
    for b in bits:
        if skip:
            error |= b == last
            last, run, skip = b, 1, False
            continue
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == n:
            skip = True
    return out, error


def nrzi_encode(initial: int, bits: Iterable[int]) -> list[int]:
    """0 = transition, 1 = no transition."""
    level, out = initial, []
    for b in bits:
        if b == 0:
            level ^= 1
        out.append(level)
    return out


def nrzi_decode(initial: int, levels: Iterable[int]) -> list[int]:
    previous, out = initial, []
    for level in levels:
        out.append(1 if level == previous else 0)
        previous = level
    return out


def words_le(data: list[int]) -> list[int]:
    return [data[k] | data[k + 1] << 8 | data[k + 2] << 16 | data[k + 3] << 24
            for k in range(0, len(data), 4)]


def words_be(data: list[int]) -> list[int]:
    return [data[k] << 24 | data[k + 1] << 16 | data[k + 2] << 8 | data[k + 3]
            for k in range(0, len(data), 4)]


# ------------------------------------------------------------------ CAN 2.0A
def can_frame_bits(ident: int, data: list[int]) -> tuple[list[int], int]:
    """SOF..data bits (unstuffed) and the CRC-15 over them."""
    header = [0] + bits_msb(ident, 11) + [0, 0, 0] + bits_msb(len(data), 4)
    body = header + [b for byte in data for b in bits_msb(byte, 8)]
    return body, crc_bits_normal(body, width=15, poly=0x4599, init=0)


def can_line(frame: tuple[int, list[int]]) -> list[int]:
    """Stuffed SOF..CRC sequence as it appears on the bus."""
    body, crc = can_frame_bits(*frame)
    return stuff_either(5, body + bits_msb(crc, 15))


def can_rtr_index(frame: tuple[int, list[int]]) -> int:
    body, _ = can_frame_bits(*frame)
    return len(stuff_either(5, body[:12]))


def can_loss_bit(a: tuple[int, list[int]], b: tuple[int, list[int]]) -> int:
    """First stuffed bit at which frame a sends recessive and b dominant (-1: none)."""
    for k, (x, y) in enumerate(zip(can_line(a), can_line(b))):
        if x == 1 and y == 0:
            return k
        if x != y:
            return -1
    return -1


def can_word(ident: int, dlc: int) -> int:
    header = [0] + bits_msb(ident, 11) + [0, 0, 0] + bits_msb(dlc, 4)
    return sum(b << (31 - k) for k, b in enumerate(header))


def can_tx_words(ident: int, data: list[int]) -> list[int]:
    """TX queue words of the can-node firmware: header, 8 data bytes MSB first."""
    assert len(data) == 8
    return [can_word(ident, 8), *words_be(data)]


def can_rx_expect(ident: int, data: list[int]) -> list[tuple[int, int]]:
    """(value, mask) per RX word the can-node firmware pushes for a received frame."""
    body, _ = can_frame_bits(ident, data)
    header = from_bits_msb(body[:19])
    n = len(data)
    groups = [] if n == 0 else [data] if n <= 4 else [data[:n - 4], data[n - 4:]]
    out = [(header, 0x7FFFF)]
    for g in groups:
        value = 0
        for byte in g:
            value = value << 8 | byte
        out.append((value, (1 << 8 * len(g)) - 1))
    return out


def can_rx_words(frame: tuple[int, list[int]]) -> int:
    n = len(frame[1])
    return 1 + (n > 0) + (n > 4)


@dataclass
class CanOther:
    """A frame of the other node: it starts once the bus has been recessive for
    ``recessive`` bit times since the last dominant bit (11 = end of
    intermission, 10 = the 3rd intermission bit), after ``after`` SOFs of
    ours and not before cycle ``not_before``."""
    ident: int
    data: list[int]
    recessive: int
    after: int = 1
    not_before: int = 0


@dataclass
class CanBus:
    """One other node on a wired-AND bus, ``bit`` cycles per bit (port of the
    extension study's behavioural node, docs/extension-study.md section 7.2).

    ``step(t, ours)`` returns the bus level at cycle t given our TXD as it
    reaches the bus. The other node sends its frames (``theirs``), samples at
    mid-bit, loses arbitration when it reads dominant while sending recessive
    up to the RTR bit (then retries under the same rule), acknowledges our
    frames unless it is transmitting, and records any other collision or bit
    error in ``errors``."""
    ours: list[tuple[int, list[int]]]
    theirs: list[CanOther]
    ack_ours: bool = True
    bit: int = 100
    our_sofs: list[int] = field(default_factory=list)
    sent: list[tuple[int, int]] = field(default_factory=list)
    lost: list[tuple[int, int, int]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._our_lens = [len(can_line(f)) for f in self.ours]
        self._pending = list(self.theirs)
        self._sending: tuple[CanOther, list[int], int, int] | None = None
        self._recessive = 1 << 60
        self._prev_ours = 1
        self._seen_ours = 0
        self._current: tuple[int, int] | None = None
        self._ack_block = -1

    def step(self, t: int, ours: int) -> int:
        bit = self.bit
        if ours == 0:
            self._recessive = 0
        if self._current is not None and t >= self._current[0] + bit * (self._current[1] + 3):
            self._current = None
        if self._prev_ours == 1 and ours == 0 and self._current is None and t >= self._ack_block:
            if self._sending is not None and t - self._sending[2] >= bit * 3 // 2:
                self.errors.append(f"dominant edge of ours at cycle {t}, bit "
                                   f"{(t - self._sending[2]) // bit} of the other node's frame")
            if self._our_lens:
                self._current = (t, self._our_lens.pop(0))
                self._seen_ours += 1
                self.our_sofs.append(t)
            else:
                self.errors.append(f"unexpected dominant edge of ours at cycle {t}")
        if self._sending is None and self._pending:
            f = self._pending[0]
            if (self._seen_ours >= f.after and t >= f.not_before
                    and self._recessive >= bit * f.recessive):
                frame = (f.ident, f.data)
                self._sending = (f, can_line(frame), t, can_rtr_index(frame))
        theirs = 1
        if self._sending is not None:
            f, line, start, _ = self._sending
            k = (t - start) // bit
            if k < len(line):
                theirs = line[k]
            else:
                self._sending = None
                self._pending.pop(0)
                self.sent.append((f.ident, start))
                self._ack_block = start + bit * (len(line) + 2) + bit // 2
                if self._current is not None and start - 3 * bit // 2 < self._current[0] < start + 3 * bit // 2:
                    self._current = None
        ack = False
        if self.ack_ours and self._sending is None and self._current is not None:
            start, length = self._current
            ack = start + bit * (length + 1) <= t < start + bit * (length + 2)
        bus = ours & theirs & (0 if ack else 1)
        if self._sending is not None and (t - self._sending[2]) % bit == bit // 2:
            f, line, start, rtr = self._sending
            k = (t - start) // bit
            if k < len(line) and line[k] == 1 and bus == 0:
                if k <= rtr:
                    self.lost.append((f.ident, start, k))
                    self._sending = None
                else:
                    self.errors.append(f"bit error in the other node's frame {f.ident:#x} at bit {k}")
        self._recessive = self._recessive + 1 if bus == 1 else 0
        self._prev_ours = ours
        return bus


# ------------------------------------------------------------------ USB low speed
def usb_token_line(pid: int, address: int, endpoint: int) -> list[int]:
    """NRZI line levels (1 = K: D+ high) of SYNC + token, stuffed, J idle before."""
    fields = bits_lsb(address, 7) + bits_lsb(endpoint, 4)
    crc5 = crc_bits_reflected(fields, poly=0x14, init=0x1F) ^ 0x1F
    token = bits_lsb(0x80, 8) + bits_lsb(pid | (~pid & 15) << 4, 8) + fields + bits_lsb(crc5, 5)
    # J (D- high) idles; NRZI starts from J = line 0 here
    return nrzi_encode(0, stuff_ones(6, token))


def usb_crc16(data: list[int]) -> int:
    return crc_bits_reflected([b for byte in data for b in bits_lsb(byte, 8)], poly=0xA001,
                              init=0xFFFF) ^ 0xFFFF


# ------------------------------------------------------------------ 10BASE-T
PREAMBLE = [0x55] * 7 + [0xD5]


def udp_frame(payload: bytes) -> list[int]:
    """Broadcast UDP datagram frame from destination address to FCS, padded to
    at least 60 bytes and a multiple of 4 before the FCS (CRC-32, IEEE 802.3)."""
    mac_d, mac_s = [0xFF] * 6, [0x02, 0, 0, 0, 0, 0x01]
    udp_len, ip_len = 8 + len(payload), 28 + len(payload)
    ip = [0x45, 0, ip_len >> 8, ip_len & 255, 0, 1, 0x40, 0, 64, 17, 0, 0, 10, 0, 0, 2, 255, 255, 255, 255]
    total = sum(ip[k] << 8 | ip[k + 1] for k in range(0, 20, 2))
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    checksum = ~total & 0xFFFF
    ip[10], ip[11] = checksum >> 8, checksum & 255
    udp = [0x04, 0xD2, 0x16, 0x2E, udp_len >> 8, udp_len & 255, 0, 0]
    body = mac_d + mac_s + [0x08, 0x00] + ip + udp + list(payload)
    target = max(60, len(body))
    target += (4 - target % 4) % 4
    body += [0] * (target - len(body))
    fcs = crc_catalogue(bytes(body), width=32, poly=0x04C11DB7, init=0xFFFFFFFF, refin=True,
                        refout=True, xorout=0xFFFFFFFF)
    return body + [fcs & 255, fcs >> 8 & 255, fcs >> 16 & 255, fcs >> 24]
