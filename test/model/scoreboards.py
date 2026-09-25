# Copied verbatim (apart from this header) from the asic-lab monorepo:
#   projects/protocol-emulator/python/protocol_emulator/scoreboards.py @ commit 18676a4
# Independent pure-Python ISA2 reference (stdlib only). Keep in sync with
# docs/isa.md; any semantic change must update RTL, assembler and model.
# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""External wire protocol decoders; independent of processor instructions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise


class ProtocolViolation(ValueError):
    pass


@dataclass(frozen=True)
class WireSample:
    cycle: int
    values: int
    enables: int = 0


def _bit(sample: WireSample, pin: int) -> int:
    if not 0 <= pin < 8:
        raise ValueError("pin outside interface")
    return sample.values >> pin & 1


def uart_decode(samples: Sequence[WireSample], *, pin: int, bit_cycles: int,
                data_bits: int = 8, parity: str = "none", stop_bits: int = 1) -> list[int]:
    """Decode fixed-baud UART by sampling bit centers after falling start edges."""
    if bit_cycles < 2 or not 5 <= data_bits <= 9 or parity not in ("none", "even", "odd"):
        raise ValueError("unsupported UART configuration")
    if stop_bits not in (1, 2):
        raise ValueError("unsupported UART stop bits")
    values: list[int] = []
    i = 1
    while i < len(samples):
        if not (_bit(samples[i-1], pin) and not _bit(samples[i], pin)):
            i += 1
            continue
        start_cycle = samples[i].cycle
        frame_bits = 1 + data_bits + int(parity != "none") + stop_bits
        centers = [start_cycle + bit_cycles // 2 + bit * bit_cycles for bit in range(frame_bits)]
        sampled: list[int] = []
        cursor = i
        for center in centers:
            while cursor < len(samples) and samples[cursor].cycle < center:
                cursor += 1
            if cursor == len(samples):
                raise ProtocolViolation("truncated UART frame")
            sampled.append(_bit(samples[cursor], pin))
        if sampled[0] or any(bit != 1 for bit in sampled[-stop_bits:]):
            raise ProtocolViolation("invalid UART start or stop bit")
        data = sampled[1:1+data_bits]
        if parity != "none":
            expected = sum(data) % 2 ^ int(parity == "odd")
            if sampled[1+data_bits] != expected:
                raise ProtocolViolation("UART parity mismatch")
        values.append(sum(bit << n for n, bit in enumerate(data)))
        end = start_cycle + frame_bits * bit_cycles
        while i < len(samples) and samples[i].cycle < end:
            i += 1
    return values


def spi_decode(samples: Sequence[WireSample], *, clock: int, data: int,
               cpol: int = 0, cpha: int = 0, msb_first: bool = True,
               bits_per_word: int = 8, select: int | None = None) -> list[int]:
    if cpol not in (0, 1) or cpha not in (0, 1) or bits_per_word < 1:
        raise ValueError("invalid SPI configuration")
    result: list[int] = []
    bits: list[int] = []
    for previous, current in pairwise(samples):
        if select is not None and _bit(current, select):
            if bits:
                raise ProtocolViolation("SPI chip select ended a partial word")
            continue
        old, new = _bit(previous, clock), _bit(current, clock)
        if old == new or (new != cpol) != (cpha == 0):
            continue
        bits.append(_bit(current, data))
        if len(bits) == bits_per_word:
            ordered = reversed(bits) if msb_first else iter(bits)
            result.append(sum(bit << i for i, bit in enumerate(ordered)))
            bits.clear()
    if bits:
        raise ProtocolViolation("incomplete SPI word")
    return result


@dataclass(frozen=True)
class I2CTransaction:
    bytes: tuple[int, ...]
    acknowledged: tuple[bool, ...]
    repeated_start: bool
    stopped: bool


def i2c_decode(samples: Sequence[WireSample], *, scl: int, sda: int) -> list[I2CTransaction]:
    """Decode START/repeated START/STOP and nine-clock byte+ACK groups."""
    result: list[I2CTransaction] = []
    active = repeated = False
    bits: list[int] = []
    data: list[int] = []
    acks: list[bool] = []
    for old, new in pairwise(samples):
        oc, nc = _bit(old, scl), _bit(new, scl)
        od, nd = _bit(old, sda), _bit(new, sda)
        if oc and nc and od != nd:
            if od and not nd:
                if active:
                    # A final SCL rise while SDA remains high can be setup for
                    # a repeated START, rather than a first bit of another byte.
                    if len(bits) > 1:
                        raise ProtocolViolation("I2C restart in partial byte")
                    result.append(I2CTransaction(tuple(data), tuple(acks), repeated, False))
                repeated, active = active, True
                bits, data, acks = [], [], []
            elif active:
                if len(bits) > 1:
                    raise ProtocolViolation("I2C stop in partial byte")
                result.append(I2CTransaction(tuple(data), tuple(acks), repeated, True))
                active = False
                bits, data, acks = [], [], []
            continue
        if active and not oc and nc:
            bits.append(nd)
            if len(bits) == 9:
                data.append(sum(bit << (7-i) for i, bit in enumerate(bits[:8])))
                acks.append(bits[8] == 0)
                bits.clear()
    if active:
        raise ProtocolViolation("I2C transaction has no STOP")
    return result


def assert_open_drain(samples: Sequence[WireSample], mask: int) -> None:
    for sample in samples:
        if sample.values & sample.enables & mask:
            raise ProtocolViolation(f"actively driven high open-drain pin at cycle {sample.cycle}")


class I2CPeer:
    """Independent open-drain target for controller-firmware verification.

    ACKs received bytes, serves supplied read bytes, and can stretch every clock
    release for a fixed number of system cycles. It interprets bus edges only;
    it cannot inspect processor PC, instruction, registers or FIFO state.
    """

    def __init__(self, read_bytes: Sequence[int] = (0x5A,), *, scl: int = 6,
                 sda: int = 7, stretch_cycles: int = 0, nack_byte: int | None = None) -> None:
        if not read_bytes or any(not 0 <= byte <= 255 for byte in read_bytes):
            raise ValueError("read response must contain bytes")
        self.read_bytes = tuple(read_bytes)
        self.scl, self.sda = scl, sda
        self.stretch_cycles = stretch_cycles
        self.nack_byte = nack_byte
        self.received: list[int] = []
        self.master_acks: list[bool] = []
        self.starts = self.stops = 0
        self._phase = "idle"
        self._next_phase = "receive"
        self._clock = self._data = 1
        self._drive_data = False
        self._bits = self._byte = self._read_index = 0
        self._address = True
        self._ack_seen = False
        self._stretch_armed = False
        self._stretch_left = 0

    def resolve(self, values: int, enables: int) -> int:
        """Resolve one cycle, returning voltages on all eight pulled-up pins."""
        if values & enables & ((1 << self.scl) | (1 << self.sda)):
            raise ProtocolViolation("controller actively drove an open-drain bus high")
        controller_low_clock = bool(enables >> self.scl & 1)
        if controller_low_clock:
            self._stretch_armed = True
        elif self._stretch_armed:
            self._stretch_left = self.stretch_cycles
            self._stretch_armed = False
        stretching = self._stretch_left > 0
        if self._stretch_left:
            self._stretch_left -= 1
        clock = int(not controller_low_clock and not stretching)
        data = int(not (enables >> self.sda & 1) and not self._drive_data)
        if self._clock and clock and self._data != data:
            if self._data and not data:
                self.starts += 1
                self._phase, self._address = "receive", True
                self._bits = self._byte = self._read_index = 0
                self._drive_data = False
            elif self._phase != "idle":
                self.stops += 1
                self._phase = "idle"
                self._drive_data = False
        elif not self._clock and clock:
            if self._phase == "receive":
                self._byte = (self._byte << 1 | data) & 255
                self._bits += 1
                if self._bits == 8:
                    self.received.append(self._byte)
                    self._next_phase = "transmit" if self._address and self._byte & 1 else "receive"
                    self._address = False
            elif self._phase == "ack":
                self._ack_seen = True
            elif self._phase == "transmit":
                self._bits += 1
            elif self._phase == "master_ack":
                self.master_acks.append(data == 0)
                self._ack_seen = True
        elif self._clock and not clock:
            if self._phase == "receive" and self._bits == 8:
                self._phase = "ack"
                self._ack_seen = False
                self._drive_data = len(self.received)-1 != self.nack_byte
            elif self._phase == "ack" and self._ack_seen:
                self._phase = self._next_phase
                self._bits = self._byte = 0
                self._drive_data = (self._phase == "transmit"
                                    and not self.read_bytes[self._read_index] >> 7 & 1)
            elif self._phase == "transmit":
                if self._bits == 8:
                    self._phase, self._drive_data, self._ack_seen = "master_ack", False, False
                else:
                    self._drive_data = not self.read_bytes[self._read_index] >> (7-self._bits) & 1
            elif self._phase == "master_ack" and self._ack_seen:
                if self.master_acks[-1]:
                    self._read_index = (self._read_index + 1) % len(self.read_bytes)
                    self._phase, self._bits = "transmit", 0
                    self._drive_data = not self.read_bytes[self._read_index] >> 7 & 1
                else:
                    self._phase, self._drive_data = "await_stop", False
        # Changes in the target's drive occur only while SCL is low.
        data = int(not (enables >> self.sda & 1) and not self._drive_data)
        self._clock, self._data = clock, data
        result = values | (~enables & 255)
        result = (result & ~(1 << self.scl | 1 << self.sda)) | clock << self.scl | data << self.sda
        return result & 255
