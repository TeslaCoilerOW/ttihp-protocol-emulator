# Copied verbatim (apart from this header) from the asic-lab monorepo:
#   projects/protocol-emulator/python/protocol_emulator/verification.py @ commit 18676a4
# Independent pure-Python ISA2 reference (stdlib only). Keep in sync with
# docs/isa.md; any semantic change must update RTL, assembler and model.
# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Reusable independent reference workloads for fixed verification workers."""

from __future__ import annotations

import hashlib
import json
import random
from itertools import pairwise
from pathlib import Path
from typing import Any

from .frozen_firmware import frozen_firmware_host
from .host import Host
from .reference import Config, Fault, Reference
from .reference import immediate as imm
from .reference import instruction as ins
from .revision2 import SCENARIOS as REVISION2_SCENARIOS
from .revision2 import uart_overflow_host
from .scoreboards import I2CPeer, WireSample, i2c_decode, spi_decode, uart_decode

SCENARIOS = ("queues", "pins_events", "spi", "dma", "congestion", "dma_congestion",
             "random_alu", "timeout_atomic", "spi_matrix", "pc_range", "xfer_pin_conflict",
             *REVISION2_SCENARIOS)
FIRMWARE_SCENARIOS = ("firmware_i2c_write", "firmware_i2c_read", "firmware_i2c_restart",
                      "firmware_i2c_nack", "firmware_flagship", "firmware_flagship_fast", "firmware_spi_target",
                      "firmware_i2c_target_write", "firmware_i2c_target_read", "firmware_uart_overflow",
                      "firmware_jtag", "firmware_waveform")


def _firmware(name: str) -> dict[str, Any]:
    directory = Path(__file__).resolve().parents[2] / "firmware"
    image: dict[str, Any] = json.loads((directory / f"{name}.image.json").read_text())
    source = (directory / f"{name}.source.json").read_bytes()
    if image["schema_version"] != "protocol-emulator.firmware-image.v1" or image["isa_version"] not in (1, 2):
        raise ValueError("unsupported firmware image")
    if hashlib.sha256(source).hexdigest() != image["source_sha256"]:
        raise ValueError("firmware source identity mismatch")
    payload = b"".join(word.to_bytes(4, "little") for word in image["words"])
    if hashlib.sha256(payload).hexdigest() != image["bytecode_sha256"]:
        raise ValueError("firmware bytecode identity mismatch")
    return image


def _load_firmware(host: Host, name: str) -> dict[str, Any]:
    image = _firmware(name)
    if host.status(7) < image["isa_version"]:
        raise ValueError("firmware requires a newer processor ISA")
    host.load(image["engine"], image["words"], ownership=image["owned_pins"],
              open_drain=image["open_drain"])
    return image


def firmware_host(scenario: str) -> Host:
    """Execute pinned OCaml-built images with independent external wire peers."""
    if scenario in ("firmware_jtag", "firmware_waveform"):
        return frozen_firmware_host(scenario)
    if scenario == "firmware_uart_overflow":
        return uart_overflow_host()
    if scenario in ("firmware_flagship", "firmware_flagship_fast"):
        return flagship_host(fast_spi=scenario == "firmware_flagship_fast")
    if scenario == "firmware_spi_target":
        return spi_target_host()
    if scenario in ("firmware_i2c_target_write", "firmware_i2c_target_read"):
        return i2c_target_host(read=scenario.endswith("read"))
    cases = {"firmware_i2c_write": ("i2c-write", (0x84, 0x5A), None),
             "firmware_i2c_read": ("i2c-read", (0x85,), 0x69),
             "firmware_i2c_restart": ("i2c-repeated-start", (0x84, 0x12, 0x85), 0x96),
             "firmware_i2c_nack": ("i2c-write", (0x84, 0x5A), None)}
    name, queued, received = cases[scenario]
    model = Reference()
    host = Host(model)
    peer = I2CPeer((received or 0,), stretch_cycles=3,
                   nack_byte=0 if scenario == "firmware_i2c_nack" else None)
    samples: list[WireSample] = []
    def external(cycle: int) -> int:
        out = model.outputs()
        pins = peer.resolve(out.uio_out, out.uio_oe)
        samples.append(WireSample(cycle, pins, out.uio_oe))
        return pins
    host.pins = external
    host.cycle(reset=True)
    image = _load_firmware(host, name)
    for word in queued:
        host.write(2, word)
    host.command(4, 1 << image["engine"])
    for _ in range(10000):
        host.cycle()
        if peer.stops or any(e.fault for e in model.engines):
            break
    else:
        raise AssertionError("I2C firmware did not finish within its bounded workload")
    if scenario == "firmware_i2c_nack":
        assert model.engines[image["engine"]].fault == 65
        assert model.outputs().uio_oe == 0
    else:
        assert not any(e.fault for e in model.engines)
        assert peer.received == list(queued)
        assert peer.stops == 1
        decoded = i2c_decode(samples, scl=6, sda=7)
        assert sum(len(transaction.bytes) for transaction in decoded) == len(queued) + int(received is not None)
        if received is not None:
            assert peer.master_acks == [False]
            assert host.read(3) == received
        if scenario == "firmware_i2c_restart":
            assert peer.starts == 2
    return host


def spi_target_host() -> Host:
    host = Host(Reference())
    host.cycle(reset=True)
    for mode in range(4):
        _spi_target_mode(host, mode)
    return host


def _spi_target_mode(host: Host, mode: int) -> None:
    model = host.machine
    samples: list[WireSample] = []
    start: int | None = None
    cpol, cpha = mode >> 1, mode & 1
    def external(cycle: int) -> int:
        out = model.outputs()
        relative = -1 if start is None else cycle-start
        select = int(relative < 0 or relative >= 576)
        clock = cpol
        if 32 <= relative < 544:
            clock = cpol ^ int(((relative-32)//32) % 2 == 0)
        bit = min(7, max(0, (relative-(32 if cpha else 0))//64))
        mosi = 0xA6 >> (7-bit) & 1
        pins = ((out.uio_out | (~out.uio_oe & 255)) & ~(4 | 8 | 32))
        pins |= clock << 2 | mosi << 3 | select << 5
        samples.append(WireSample(cycle, pins, out.uio_oe))
        return pins
    host.pins = external
    _load_firmware(host, f"spi-target-mode{mode}")
    host.write(2, 0x5A)
    host.command(4, 4)
    start = len(host.cycles) + 40
    host.idle(700)
    assert spi_decode(samples, clock=2, data=4, select=5, cpol=cpol, cpha=cpha) == [0x5A]
    assert host.read(3) == 0xA6
    assert model.engines[2].fault == 0
    assert model.outputs().uio_oe & 16 == 0


def i2c_target_host(*, read: bool) -> Host:
    """A wire-level controller honors target clock stretching under congestion."""
    model = Reference()
    host = Host(model)
    samples: list[WireSample] = []
    start: int | None = None
    position = elapsed = stretch_observations = 0
    segments = [(1, 1, 32), (1, 0, 32)]
    def send_byte(value: int) -> None:
        for bit in (value >> i & 1 for i in range(7, -1, -1)):
            segments.extend([(0, bit, 32), (1, bit, 32)])
        segments.extend([(0, 1, 32), (1, 1, 32)])
    send_byte(0x85 if read else 0x84)
    if read:
        for _ in range(9):  # Eight returned data bits, then controller NACK.
            segments.extend([(0, 1, 32), (1, 1, 32)])
    else:
        send_byte(0xA6)
    segments.extend([(0, 0, 32), (1, 0, 32), (1, 1, 32)])
    def external(cycle: int) -> int:
        nonlocal position, elapsed, stretch_observations
        out = model.outputs()
        active = start is not None and cycle >= start and position < len(segments)
        clock, data = segments[position][:2] if active else (1, 1)
        clock &= int(not (out.uio_oe & 64))
        data &= int(not (out.uio_oe & 128))
        pins = ((out.uio_out | (~out.uio_oe & 255)) & 63) | clock << 6 | data << 7
        samples.append(WireSample(cycle, pins, out.uio_oe))
        if active:
            if segments[position][0] and not clock:
                stretch_observations += 1
            else:
                elapsed += 1
                if elapsed == segments[position][2]:
                    position += 1
                    elapsed = 0
        return pins
    host.pins = external
    host.cycle(reset=True)
    if not read:
        # Fill RX through real instructions, then retain it across image reload.
        host.load(3, [ins(19, 1, 0, 99), imm(10, 7), ins(7), imm(11, 2), ins(1)])
        host.command(4, 8)
        host.idle(40)
        assert len(model.engines[3].rx) == 8
    _load_firmware(host, "i2c-target-read" if read else "i2c-target-write")
    host.command(4, 8)
    start = len(host.cycles) + 40
    for _ in range(3000):
        host.cycle()
        if stretch_observations >= 80:
            break
    else:
        raise AssertionError("congested I2C target did not stretch the clock")
    if read:
        host.write(2, 0x5A)
    else:
        assert host.read(3) == 99
    for _ in range(5000):
        host.cycle()
        if position == len(segments):
            break
    else:
        raise AssertionError("I2C target did not resume after congestion cleared")
    host.idle(20)
    decoded = i2c_decode(samples, scl=6, sda=7)
    assert len(decoded) == 1
    assert decoded[0].bytes == ((0x85, 0x5A) if read else (0x84, 0xA6))
    assert decoded[0].acknowledged == ((True, False) if read else (True, True))
    assert model.engines[3].fault == 0
    if not read:
        for _ in range(7):
            assert host.read(3) == 99
        assert host.read(3) == 0xA6
    return host


def flagship_host(*, fast_spi: bool = False) -> Host:
    """Four live protocols plus UART-RX-to-SPI DMA, checked at public wires."""
    model = Reference()
    host = Host(model)
    peer = I2CPeer(stretch_cycles=3)
    samples: list[WireSample] = []
    uart_in = [74, 65, 78, 69, 83, 84, 82, 84]
    uart_out = [67, 79, 78, 67, 85, 82, 82, 69]
    uart_start: int | None = None
    previous_clock, previous_select, spi_bit = 0, 1, 0
    def external(cycle: int) -> int:
        nonlocal previous_clock, previous_select, spi_bit
        out = model.outputs()
        pins = peer.resolve(out.uio_out, out.uio_oe)
        clock, select = pins >> 2 & 1, pins >> 5 & 1
        if select or previous_select:
            spi_bit = 0
        elif previous_clock and not clock:
            spi_bit = (spi_bit + 1) % 8
        previous_clock, previous_select = clock, select
        miso = 0xA5 >> (7-spi_bit) & 1
        rx = 1
        if uart_start is not None and cycle >= uart_start:
            frame, offset = divmod(cycle-uart_start, 704)
            if frame < len(uart_in):
                bit_index = offset // 64
                if bit_index == 0:
                    rx = 0
                elif bit_index <= 8:
                    rx = uart_in[frame] >> (bit_index-1) & 1
        pins = (pins & ~(1 << 1 | 1 << 4)) | rx << 1 | miso << 4
        samples.append(WireSample(cycle, pins, out.uio_oe))
        return pins
    host.pins = external
    host.cycle(reset=True)
    spi_image = "spi-controller-fast" if fast_spi else "spi-controller-mode0"
    for name in ("uart-tx", "uart-rx", spi_image, "i2c-write"):
        _load_firmware(host, name)
    host.command(0, 0)
    for word in uart_out:
        host.write(2, word)
    host.command(0, 3)
    host.write(2, 0x84)
    host.write(2, 0x5A)
    host.command(6, 1 | 2 << 2 | 16 | len(uart_in) << 5)
    host.command(4, 15)
    uart_start = len(host.cycles) + 96
    for _ in range(96 + 7*704 + 64*10 + 620):
        host.cycle()
        assert not model.host_fault and not any(e.fault for e in model.engines)
    assert uart_decode(samples, pin=0, bit_cycles=64) == uart_out
    assert spi_decode(samples, clock=2, data=3, select=5) == uart_in
    assert peer.received == [0x84, 0x5A] and peer.stops == 1
    assert model.routes[1] is None
    assert len(model.engines[1].rx) == 0
    assert list(model.engines[2].rx) == [0xA5] * 8
    # Freeze the completed workload before draining results so the intentionally
    # bounded idle UART receiver cannot time out during host diagnostics.
    host.command(5, 15)
    host.command(0, 2)
    for _ in uart_in:
        assert host.read(3) == 0xA5
    return host


def _dma_congestion(host: Host) -> None:
    """Overload a finite autonomous bridge, then recover every queued word.

    Only real host transactions and external pins drive the machine. The source
    generates two words beyond the descriptor quota, so public FIFO reads also
    expose an off-by-one transfer or continued service after quota exhaustion.
    LIMIT applies to WAITPIN/WAITEVENT; a full PUSH is a permitted unbounded
    stall. A later, deliberately unanswered WAITEVENT checks the timeout fault.
    """
    model, depth = host.machine, host.machine.config.fifo_words
    quota, base = 2 * depth + 3, 0x1200
    host.load(0, [imm(12, 3), ins(19, 1, base >> 8, base & 255),
                  ins(19, 2, 0, 1), imm(10, quota + 1),
                  ins(7), ins(20, 1, 2), imm(11, 4), ins(1)])
    host.load(1, [imm(12, 4095), ins(13, 0, 1), imm(10, quota - 1),
                  ins(6), ins(18, 1, 0), ins(7), imm(11, 3),
                  ins(13, 1, 1), imm(12, 7), ins(15), ins(1)])
    if model.config.engines == 4:
        for engine, half_period in ((2, 4), (3, 7)):
            pin = 1 << engine
            host.load(engine, [imm(3, pin), imm(2, pin), imm(4, half_period - 2),
                               imm(2, 0), imm(4, half_period - 3), imm(5, 1)], ownership=pin)
    host.command(6, 1 << 2 | 16 | quota << 5)
    host.command(4, (1 << model.config.engines) - 1)
    host.idle(8 * depth + 20)
    source, destination = model.engines[:2]
    assert list(destination.tx) == list(range(base, base + depth))
    assert list(source.rx) == list(range(base + depth, base + 2 * depth))
    assert source.pc == 4 and source.stalled and source.fault == 0
    assert model.routes[0] == (1, quota - depth)

    # Keep the full queues stable for much longer than source LIMIT=3. Neither
    # an attempted DMA grant nor an unaccepted PUSH may consume a word/quota.
    blocked_start = len(host.cycles)
    host.idle(6 * depth + 23)
    assert source.pc == 4 and source.stalled and source.fault == 0
    assert source.regs[1] == base + 2 * depth
    assert list(source.rx) == list(range(base + depth, base + 2 * depth))
    assert list(destination.tx) == list(range(base, base + depth))
    assert model.routes[0] == (1, quota - depth)
    host.command(0, 0)
    assert host.status(0) == 7  # running, committed, stalled; no fault
    assert host.status(2) == depth << 16
    host.command(0, 1)
    assert host.status(2) == depth

    # A synchronized external release starts consumption while the source and
    # mover continue autonomously. Delayed host reads create a second bottleneck
    # at destination RX and exercise simultaneous accepted FIFO operations.
    host.pins = 1
    host.idle(8 * depth + 20)
    assert len(destination.rx) == depth and destination.stalled
    for value in range(base, base + quota):
        assert host.read(3, pauses=[2, 0, 1, 1, 0, 3, 0, 1]) == value
    host.idle(10)
    assert model.routes[0] is None
    assert not source.tx and not destination.tx and not destination.rx
    assert list(source.rx) == [base + quota, base + quota + 1]
    assert not source.running and source.fault == 0
    assert destination.pc == 7 and destination.fault == 0
    assert host.status(2) == 0
    assert host.status(3) == 7
    host.command(0, 0)
    assert host.status(2) == 2 << 16
    assert host.read(3) == base + quota
    assert host.read(3) == base + quota + 1
    assert host.status(2) == 0

    # Permit the final WAITPIN, then verify exactly seven failed mailbox waits.
    host.pins = 3
    for _ in range(8):
        host.cycle()
        if destination.pc == 9:
            break
    else:
        raise AssertionError("bridge receiver did not reach its bounded event wait")
    assert destination.blocked == 0 and destination.limit == 7
    for blocked in range(1, 8):
        host.cycle()
        assert destination.blocked == blocked
        assert destination.fault == (Fault.TIMEOUT if blocked == 7 else 0)
    host.command(0, 1)
    assert host.status(0) == (Fault.TIMEOUT << 8 | 10)
    assert not model.host_fault and not source.fault

    if model.config.engines == 4:
        for engine, half_period in ((2, 4), (3, 7)):
            pin = 1 << engine
            observations = host.cycles[blocked_start:]
            assert all(step.expected.uio_oe & pin for step in observations)
            edges = [index for index in range(1, len(observations))
                     if (observations[index - 1].expected.uio_out
                         ^ observations[index].expected.uio_out) & pin]
            assert len(edges) > 20
            assert all(right - left == half_period for left, right in pairwise(edges))
            assert model.engines[engine].running and not model.engines[engine].fault
    host.command(5, (1 << model.config.engines) - 1)


def differential_host(scenario: str, config: Config | None = None) -> Host:
    if scenario in REVISION2_SCENARIOS:
        return REVISION2_SCENARIOS[scenario](config)
    if scenario in FIRMWARE_SCENARIOS:
        if config is not None and config != Config():
            raise ValueError("retained firmware workloads currently bind the flagship architecture")
        return firmware_host(scenario)
    model = Reference(config)
    config = model.config
    host = Host(model)
    engine_mask = (1 << config.engines) - 1
    host.cycle(reset=True)
    host.idle(2)
    if scenario == "queues":
        for engine in range(config.engines):
            host.load(engine, [ins(6), ins(18, 1, 0), ins(7), imm(5, 0)])
            host.write(2, 0x1200 + engine)
            host.write(2, 0x5600 + engine)
        host.command(4, engine_mask)
        host.idle(50)
        for engine in range(config.engines):
            host.command(0, engine)
            assert host.read(3, pauses=[1, 2, 1, 0, 0, 2, 0, 1]) == 0x1200 + engine
            assert host.read(3) == 0x5600 + engine
        host.command(5, engine_mask)
    elif scenario == "pins_events":
        host.load(0, [imm(3, 1), imm(2, 1), imm(14, 2), imm(4, 2), imm(2, 0), ins(1)], ownership=1)
        host.load(1, [ins(15), imm(3, 2), imm(2, 2), imm(4, 3), ins(1)], ownership=2)
        host.command(4, 3)
        host.idle(20)
        host.cycle(enabled=False)
        host.idle(3)
    elif scenario == "spi":
        host.load(0, [imm(3, 3), ins(6), imm(16, 1 << 3 | 2 << 6),
                      ins(17, 8, 4, 8), ins(1)], ownership=3)
        host.write(2, 0xA6)
        host.command(4, 1)
        host.idle(80)
    elif scenario == "spi_matrix":
        if not config.fused:
            host.load(0, [ins(17, 1, 1, 8)], ownership=3)
            host.command(4, 1)
            host.idle(5)
            assert model.engines[0].fault == 1
            return host
        host.pins = lambda cycle: (cycle // 3 & 1) << 2
        for mode in range(8):
            for enables in (8, 16, 24):
                for period in (1, 4):
                    host.load(0, [imm(3, 3), ins(6), imm(16, 1 << 3 | 2 << 6),
                                  ins(17, 8, period, mode | enables), ins(7), ins(1)], ownership=3)
                    host.write(2, 0xA6 << (config.width-8) if mode & 4 else 0xA6)
                    host.command(4, 1)
                    host.idle(16 * period + 12)
                    host.read(3)
    elif scenario == "pc_range":
        host.load(0, [imm(5, 0x10000), imm(5, 0)])
        host.command(4, 1)
        host.idle(10)
        assert host.machine.engines[0].fault == 2
    elif scenario == "xfer_pin_conflict":
        host.load(0, [imm(2, 1), imm(3, 1), imm(16, 0), ins(17, 1, 1, 8), ins(1)], ownership=1)
        host.command(4, 1)
        host.idle(10)
        assert host.machine.engines[0].fault == 1
        assert host.machine.outputs().uio_oe == 0
    elif scenario == "dma":
        chain_length = min(3, config.engines)
        for engine in range(chain_length):
            host.load(engine, [ins(6), ins(18, 1, 0), ins(7), imm(5, 0)])
        host.command(0, 0)
        for value in (0x01020304, 0xAABBCCDD, 0x76543210):
            host.write(2, value)
        for engine in range(chain_length-1):
            host.command(6, engine | (engine+1) << 2 | 16 | 3 << 5)
        host.command(4, (1 << chain_length)-1)
        host.idle(60)
        host.command(0, chain_length-1)
        for value in (0x01020304, 0xAABBCCDD, 0x76543210):
            assert host.read(3) == value & model.mask
        host.command(5, (1 << chain_length)-1)
    elif scenario == "congestion":
        host.load(0, [ins(6), ins(18, 1, 0), ins(7), imm(5, 0)])
        for value in range(config.fifo_words):
            host.write(2, value)
        host.command(4, 1)
        host.idle(config.fifo_words*5+10)
        for value in range(config.fifo_words, config.fifo_words+4):
            host.write(2, value)
        host.idle(20)
        for value in range(config.fifo_words+4):
            assert host.read(3, pauses=[2, 0, 1, 1, 0, 3, 0, 1]) == value
        host.command(5, 1)
    elif scenario == "dma_congestion":
        _dma_congestion(host)
    elif scenario == "random_alu":
        rng = random.Random(0x1A5A)
        words = [ins(19, reg, rng.randrange(256), rng.randrange(256)) for reg in range(4)]
        for _ in range(min(25, config.program_words-11)):
            op = rng.choice([18, 20, 21, 22, 23, 24, 25, 27, 28])
            reg = rng.randrange(4)
            if op in (24, 25):
                words.append(ins(op, reg, 0, rng.randrange(config.width)))
            elif op in (27, 28):
                words.append(ins(op, reg))
            else:
                words.append(ins(op, reg, rng.randrange(4)))
        words += [ins(18, 1, 0), ins(7), ins(18, 1, 2), ins(7), ins(18, 1, 3), ins(7), ins(1)]
        host.load(0, words)
        host.command(4, 1)
        host.idle(50)
        host.read(3)
        host.read(3)
        host.read(3)
    elif scenario == "timeout_atomic":
        host.load(0, [imm(3, 1), imm(12, 3), ins(13, 2, 1), ins(1)], ownership=1)
        host.command(4, 1)
        host.idle(12)
        host.command(7, 1)
        host.command(1)
        host.write(1, ins(1))
        host.command(2, 2)  # Incomplete image must reject atomically.
        host.command(4, 1)
        host.idle(4)
        host.cycle(reset=True)
    else:
        raise ValueError(scenario)
    return host
