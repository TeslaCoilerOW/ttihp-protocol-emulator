# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Stimuli and expected frames for the firmware/ext demos, from the standards.

Shared by test/stdref/test_firmware_ext.py (reference only) and
test/test_line_stdref.py (RTL).  Every expected frame is built with the
standard encoders of line_std_ref (USB packets, CAN 2.0A frames, Ethernet
frames), never from the firmware's own words.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from _ref import ref

FIRMWARE = pathlib.Path(__file__).resolve().parents[2] / "firmware" / "ext"


@dataclass
class Image:
    name: str
    words: list[int]
    engine: int
    owned: int
    open_drain: int
    clock_hz: int


def load_image(name: str) -> Image:
    data = json.loads((FIRMWARE / f"{name}.image.json").read_text())
    return Image(name, list(data["words"]), data["engine"], data["owned_pins"],
                 data["open_drain"], data["clock_hz"])


# -- crc16-stream -----------------------------------------------------------

def crc16_stream_job(init: int, words: list[int]) -> tuple[list[int], int]:
    """TX words of one job and the expected CRC (big-endian bytes of each word)."""
    data = b"".join(w.to_bytes(4, "big") for w in words)
    alg = ref.CATALOGUE_BY_NAME["CRC-16/IBM-3740" if init == 0xFFFF else "CRC-16/XMODEM"]
    expected = alg.compute(data) if init in (0, 0xFFFF) else None
    return [init, len(words)] + list(words), expected


# -- 10BASE-T ---------------------------------------------------------------

def udp_frame(payload: bytes = b"stdref 10BASE-T check!") -> bytes:
    """A 68-byte Ethernet II / IPv4 / UDP frame including the FCS (RFC 768, 791)."""
    dst = bytes.fromhex("ffffffffffff")
    src = bytes.fromhex("020000000001")
    udp_len = 8 + len(payload)
    ip_len = 20 + udp_len
    ip = bytearray([0x45, 0, ip_len >> 8, ip_len & 0xFF, 0, 0, 0x40, 0, 64, 17, 0, 0,
                    192, 168, 0, 2, 192, 168, 0, 255])
    total = sum(int.from_bytes(ip[i:i + 2], "big") for i in range(0, 20, 2))
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    ip[10:12] = (total ^ 0xFFFF).to_bytes(2, "big")
    udp = (4000).to_bytes(2, "big") + (4001).to_bytes(2, "big") + udp_len.to_bytes(2, "big") + b"\0\0"
    frame = dst + src + b"\x08\x00" + bytes(ip) + udp + payload
    assert len(frame) == 64, len(frame)
    return frame + ref.ethernet_fcs(frame)


def frame_words_lsb_first(frame: bytes) -> list[int]:
    """Words for an LSB-first XFER: byte 0 in bits 7..0."""
    assert len(frame) % 4 == 0
    return [int.from_bytes(frame[i:i + 4], "little") for i in range(0, len(frame), 4)]


# -- USB low speed ----------------------------------------------------------

USB_LS_BIT_CYCLES_NUM, USB_LS_BIT_CYCLES_DEN = 100, 3      # 50 MHz / 1.5 Mbit/s


def usb_host_packet(start: int, packet_bits: list[int], length: int,
                    eop_bits: int = 2) -> tuple[list[int], list[int]]:
    """D+ and D- pad levels of a low-speed packet sent by a host from ``start``.

    Stuffing and NRZI from idle J, then SE0 for ``eop_bits`` bit times, then J.
    Bit i starts at start + floor(i * 100 / 3).
    """
    levels, _ = ref.usb_line_levels(packet_bits)
    dplus = [ref.USB_LS_J_DPLUS] * length
    dminus = [ref.USB_LS_J_DPLUS ^ 1] * length

    def edge(i):
        return start + (i * USB_LS_BIT_CYCLES_NUM) // USB_LS_BIT_CYCLES_DEN

    for i, level in enumerate(levels):
        for n in range(edge(i), min(edge(i + 1), length)):
            dplus[n], dminus[n] = level, level ^ 1
    for n in range(edge(len(levels)), min(edge(len(levels) + eop_bits), length)):
        dplus[n], dminus[n] = 0, 0
    return dplus, dminus


def usb_in_token(addr: int = 0x70, endp: int = 4) -> list[int]:
    return ref.usb_packet_bits("IN", ref.usb_token_body(addr, endp))


def usb_data1_words(data: bytes) -> list[int]:
    """TX words of usb-ls-in-responder: SYNC+DATA1 PID word, then 8 data bytes."""
    assert len(data) == 8
    sync_pid = ref.bits_to_int(ref.USB_SYNC + ref.usb_pid_bits(ref.USB_PID["DATA1"]), lsb_first=True)
    return [sync_pid] + [int.from_bytes(data[i:i + 4], "little") for i in (0, 4)]


# -- CAN 2.0A ---------------------------------------------------------------

def can_header_word(ident: int, dlc: int) -> int:
    """can-node TX header word: SOF, ID, RTR, IDE, r0, DLC in bits 31..13."""
    bits = [0] + ref.int_to_bits(ident, 11, False) + [0, 0, 0] + ref.int_to_bits(dlc, 4, False)
    return ref.bits_to_int(bits, lsb_first=False) << 13


def can_tx_words(ident: int, data: bytes) -> list[int]:
    assert len(data) == 8
    return [can_header_word(ident, 8), int.from_bytes(data[:4], "big"),
            int.from_bytes(data[4:], "big")]


def can_other_node(start: int, bit_cycles: int, cells: list[int], length: int,
                   recessive_after: bool = True) -> list[int]:
    """Level driven by another node that sends ``cells`` from ``start``."""
    level = [1] * length
    for i, bit in enumerate(cells):
        for n in range(start + i * bit_cycles, min(start + (i + 1) * bit_cycles, length)):
            level[n] = bit
    return level


class CanTransmitterNode:
    """Another CAN node that sends one data frame (Bosch CAN 2.0 A).

    It starts at ``start`` or, with ``sync_on_sof``, on the first dominant bus
    level (both nodes then send SOF together).  It drops out (recessive) when it
    sends recessive and reads dominant in the arbitration field.  ``drive(n)``
    is its output in cycle n; ``observe(n, bus)`` must follow in cycle order.
    """

    def __init__(self, ident: int, data: bytes, bit_cycles: int, start: int | None = None,
                 sync_on_sof: bool = False):
        frame = ref.can_frame(ident, data)
        self.frame = frame
        self.cells = frame["stuffed"] + [1, 1, 1] + [1] * 7     # CRC delim, ACK slot, ACK delim, EOF
        self.arbitration_cells = self._arbitration_cells(frame)
        self.bit_cycles = bit_cycles
        self.start = start
        self.sync_on_sof = sync_on_sof
        self.lost = False
        self.ack_seen = None
        self.ack_cell = len(frame["stuffed"]) + 1

    @staticmethod
    def _arbitration_cells(frame) -> int:
        """Cells up to and including RTR (SOF + 11 ID bits + RTR, with stuff bits)."""
        data_bits = 0
        for index, (_, is_stuff) in enumerate(frame["cells"]):
            if not is_stuff:
                data_bits += 1
                if data_bits == 13:
                    return index + 1
        return 0

    def _cell(self, n: int) -> int | None:
        if self.start is None or n < self.start:
            return None
        index = (n - self.start) // self.bit_cycles
        return index if index < len(self.cells) else None

    def drive(self, n: int) -> int:
        index = self._cell(n)
        if index is None or self.lost:
            return 1
        return self.cells[index]

    def observe(self, n: int, bus: int) -> None:
        if self.start is None:
            if self.sync_on_sof and bus == 0:
                self.start = n
            return
        index = self._cell(n)
        if index is None:
            return
        if (n - self.start) % self.bit_cycles == self.bit_cycles // 2:
            if index < self.arbitration_cells and self.cells[index] == 1 and bus == 0:
                self.lost = True
            if index == self.ack_cell and not self.lost:
                self.ack_seen = bus == 0


class CanReceiverNode:
    """A CAN receiver: hard sync on SOF, 50 % sample point, destuffing, CRC-15
    check and a dominant ACK slot after a matching CRC (Bosch CAN 2.0 A)."""

    def __init__(self, bit_cycles: int, idle_bits: int = 11):
        self.bit_cycles = bit_cycles
        self.idle_bits = idle_bits
        self.recessive_run = idle_bits * bit_cycles   # the bus starts idle
        self.sof = None
        self.cells: list[int] = []
        self.frames: list[dict] = []
        self.ack_window = None
        self._reset_frame()

    def _reset_frame(self):
        self.state = ref.RunState()
        self.data: list[int] = []
        self.stuff_error = False
        self.need = None

    def drive(self, n: int) -> int:
        if self.ack_window and self.ack_window[0] <= n < self.ack_window[1]:
            return 0
        return 1

    def observe(self, n: int, bus: int) -> None:
        if self.sof is None:
            if bus == 0 and self.recessive_run >= self.idle_bits * self.bit_cycles:
                self.sof = n
                self.cells = []
                self._reset_frame()
            self.recessive_run = self.recessive_run + 1 if bus else 0
            return
        offset = n - self.sof
        if offset % self.bit_cycles != self.bit_cycles // 2:
            return
        cell = offset // self.bit_cycles
        self.cells.append(bus)
        rule = ref.CAN_STUFFING
        fields_done = self.need is not None and len(self.data) >= self.need
        if not fields_done:
            if rule.due(self.state):
                if bus != rule.value(self.state):
                    self.stuff_error = True
                self.state = rule.after_stuff(bus)
            else:
                self.data.append(bus)
                self.state = rule.after_data(self.state, bus)
                if len(self.data) == 19:
                    dlc = ref.bits_to_int(self.data[15:19], lsb_first=False)
                    self.need = 19 + 8 * min(dlc, 8) + 15
            if self.need is not None and len(self.data) >= self.need and not rule.due(self.state):
                self.crc_delim_cell = cell + 1
            return
        # fixed-form tail: CRC delimiter, ACK slot, ACK delimiter, EOF
        if cell == self.crc_delim_cell:
            fields = self.data[:self.need - 15]
            crc = ref.bits_to_int(self.data[self.need - 15:self.need], lsb_first=False)
            ok = crc == ref.can_crc15(fields) and not self.stuff_error and bus == 1
            ident = ref.bits_to_int(fields[1:12], lsb_first=False)
            dlc = ref.bits_to_int(fields[15:19], lsb_first=False)
            payload = ref.bits_to_int(fields[19:], lsb_first=False) if len(fields) > 19 else 0
            self.frames.append({"ident": ident, "dlc": dlc, "fields": fields, "crc": crc,
                                "crc_ok": ok, "payload": payload,
                                "data": payload.to_bytes(min(dlc, 8), "big") if dlc else b"",
                                "stuff_error": self.stuff_error})
            if ok:
                start = self.sof + (cell + 1) * self.bit_cycles
                self.ack_window = (start, start + self.bit_cycles)
        if cell >= self.crc_delim_cell + 9:          # end of EOF
            self.sof = None
            self.recessive_run = 0


class Bus:
    """Wired-AND bus of the device pin and other nodes, computed in cycle order."""

    def __init__(self, device_level, nodes):
        self.device_level = device_level     # n -> 0/1 (1 when not driving)
        self.nodes = nodes
        self.levels: list[int] = []

    def level(self, n: int) -> int:
        while len(self.levels) <= n:
            m = len(self.levels)
            value = self.device_level(m)
            for node in self.nodes:
                value &= node.drive(m)
            self.levels.append(value)
            for node in self.nodes:
                node.observe(m, value)
        return self.levels[n]
