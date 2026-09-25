# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Directed protocol scenarios, runnable on the DUT (cocotb) or the model only.

Every scenario is an ``async def f(h: Harness)``. Under cocotb the harness
checks every cycle against the reference model; in addition each scenario
checks protocol-level results with independent wire monitors and peers.
"""

from __future__ import annotations

import json
from typing import Any

from harness import (CLEAR, FIRMWARE, ROUTE, RS_LEVELS, RS_STATUS, SELECT, START,
                     STOP, Harness, pad_value)
from model.reference import Outputs
from model.scoreboards import I2CPeer, assert_open_drain, i2c_decode, spi_decode, uart_decode
from peers import OpenDrainI2CBus, SpiTarget, UartMonitor, UartSource, bit

BIT_CYCLES = 64  # UART firmware: 64 clocks per bit (781.25 kbaud at 50 MHz)


def fault_of(status: int) -> int:
    return status >> 8 & 0xFF if status & 8 else 0


async def uart_tx(h: Harness, words: list[int]) -> None:
    """uart-tx on engine 0: host bytes appear on pin 0 as 8N1 frames."""
    await h.start()
    monitor = UartMonitor(bit_cycles=BIT_CYCLES)
    started: list[int] = []

    def pins(cycle: int, out: Outputs) -> int:
        pads = pad_value(out, 0xFF)  # pull-ups on undriven pads
        if started:
            assert out.uio_oe & 1, f"pin0 released while transmitting (cycle {cycle})"
            monitor.sample(cycle, bit(pads, 0))
        return pads

    h.pins = pins
    h.samples = []
    await h.load_firmware("uart-tx")
    await h.command(SELECT, 0)
    prefill = min(8, h.model.config.fifo_words)  # 8 on the design of record
    for word in words[:prefill]:
        await h.write(2, word)
    await h.command(START, 1)
    await h.idle(3)
    started.append(h.cycle)
    for word in words[prefill:]:  # remaining bytes under TX backpressure
        await h.write(2, word)
    limit = (len(words) + 2) * 11 * BIT_CYCLES
    await h.run_until(lambda: len(monitor.words) == len(words), limit, "UART TX frames")
    await h.idle(BIT_CYCLES)
    assert not monitor.errors, monitor.errors
    assert monitor.words == words, f"UART TX monitor {monitor.words} != {words}"
    assert uart_decode(h.samples, pin=0, bit_cycles=BIT_CYCLES) == words
    assert await h.status(RS_LEVELS) == 0
    h.assert_no_faults()
    started.clear()
    await h.command(STOP, 1)
    await h.idle(2)
    assert h.last_pre.uio_oe == 0


async def uart_rx(h: Harness, words: list[int]) -> None:
    """uart-rx on engine 1: frames driven on pin 1 are read from the RX FIFO.

    Afterwards: the documented idle bound faults with code 3, and a frame with
    a low stop bit faults with the firmware's explicit framing code 64.
    """
    await h.start()
    source = UartSource(words, bit_cycles=BIT_CYCLES, gap_cycles=BIT_CYCLES)
    h.pins = lambda cycle, out: pad_value(out, 0xFD | source.level(cycle) << 1)
    await h.load_firmware("uart-rx")
    await h.command(SELECT, 1)
    await h.command(START, 2)
    source.start = h.cycle + 50
    for word in words:
        value = await h.read(3)
        assert value == word, f"UART RX read {value:#x}, expected {word:#x}"
    h.assert_no_faults()
    # Idle longer than LIMIT 768: bounded wait timeout (fault 3).
    await h.run_until(lambda: h.engine(1).fault != 0, 2000, "UART RX idle timeout")
    assert fault_of(await h.status(RS_STATUS)) == 3
    # Framing error: clear, restart, send one frame with a low stop bit.
    await h.command(CLEAR, 2)
    source.words, source.bad_stop = [0xA5], {0}
    await h.command(START, 2)
    source.start = h.cycle + 40
    await h.run_until(lambda: h.engine(1).fault != 0, 1200, "UART RX framing fault")
    assert fault_of(await h.status(RS_STATUS)) == 64
    assert await h.status(RS_LEVELS) == 0
    await h.command(CLEAR, 2 | 1 << 23)


async def spi_controller(h: Harness, mode: int, tx: list[int], responses: list[int]) -> None:
    """spi-controller-mode<N> on engine 2 against a behavioral SPI target."""
    await h.start()
    target = SpiTarget(mode, responses)

    def pins(cycle: int, out: Outputs) -> int:
        pads = pad_value(out, 0xFF)
        miso = target.update(pads)
        return (pads & ~(1 << target.miso)) | miso << target.miso

    h.pins = pins
    h.samples = []
    await h.load_firmware(f"spi-controller-mode{mode}")
    await h.command(SELECT, 2)
    depth = h.model.config.fifo_words
    for word in tx[:depth]:
        await h.write(2, word)
    await h.command(START, 4)
    if depth >= len(tx):  # the design of record: every word queued before START
        limit = len(tx) * (16 * 32 + 64) + 200
        await h.run_until(lambda: len(h.engine(2).rx) == len(tx), limit, f"SPI mode{mode} transfers")
        await h.idle(8)
    else:  # small queues: top up TX under backpressure, then read MISO words as they arrive
        for word in tx[depth:]:
            await h.write(2, word)
        early = [await h.read(3) for _ in tx]
        await h.idle(8)
    assert not target.violations, target.violations
    assert target.received == tx, f"SPI target received {target.received} != {tx}"
    cpol, cpha = mode >> 1, mode & 1
    assert spi_decode(h.samples, clock=2, data=3, select=5, cpol=cpol, cpha=cpha) == tx
    got = [await h.read(3) for _ in tx] if depth >= len(tx) else early
    assert got == [r & 0xFF for r in responses[:len(tx)]], f"SPI mode{mode} MISO words {got} != {responses}"
    h.assert_no_faults()
    await h.command(STOP, 4)


def _i2c(h: Harness, peer: I2CPeer) -> None:
    bus = OpenDrainI2CBus(peer)

    def pins(cycle: int, out: Outputs) -> int:
        levels = bus.resolve(out)
        return (pad_value(out, 0xFF) & ~bus.mask) | (levels & bus.mask)

    h.pins = pins
    h.samples = []


async def i2c_write(h: Harness) -> None:
    """i2c-write on engine 3: address 0x42+W, one data byte, with clock stretching."""
    await h.start()
    peer = I2CPeer((0,), stretch_cycles=3)
    _i2c(h, peer)
    await h.load_firmware("i2c-write")
    await h.command(SELECT, 3)
    for word in (0x84, 0x5A):
        await h.write(2, word)
    await h.command(START, 8)
    await h.run_until(lambda: peer.stops or h.engine(3).fault, 6000, "I2C write STOP")
    await h.idle(4)
    h.assert_no_faults()
    assert peer.received == [0x84, 0x5A] and peer.starts == 1 and peer.stops == 1
    decoded = i2c_decode(h.samples, scl=6, sda=7)
    assert len(decoded) == 1 and decoded[0].bytes == (0x84, 0x5A), decoded
    assert decoded[0].acknowledged == (True, True)
    assert_open_drain(h.samples, 0xC0)
    await h.command(STOP, 8)


async def i2c_read(h: Harness) -> None:
    """i2c-read on engine 3: address 0x42+R, one byte returned, controller NACK."""
    await h.start()
    peer = I2CPeer((0x69,), stretch_cycles=3)
    _i2c(h, peer)
    await h.load_firmware("i2c-read")
    await h.command(SELECT, 3)
    await h.write(2, 0x85)
    await h.command(START, 8)
    await h.run_until(lambda: peer.stops or h.engine(3).fault, 6000, "I2C read STOP")
    await h.idle(4)
    h.assert_no_faults()
    assert peer.received == [0x85] and peer.master_acks == [False] and peer.stops == 1
    decoded = i2c_decode(h.samples, scl=6, sda=7)
    assert len(decoded) == 1 and decoded[0].bytes == (0x85, 0x69), decoded
    assert await h.read(3) == 0x69
    await h.command(STOP, 8)


async def i2c_nack(h: Harness) -> None:
    """An address NACK makes i2c-write fault with its explicit code 65 and release the bus."""
    await h.start()
    peer = I2CPeer((0,), stretch_cycles=0, nack_byte=0)
    _i2c(h, peer)
    await h.load_firmware("i2c-write")
    await h.command(SELECT, 3)
    for word in (0x84, 0x5A):
        await h.write(2, word)
    await h.command(START, 8)
    await h.run_until(lambda: h.engine(3).fault != 0, 4000, "I2C NACK fault")
    await h.idle(2)
    assert fault_of(await h.status(RS_STATUS)) == 65
    assert h.last_pre.uio_oe == 0, "faulted engine must release its pins"


async def flagship(h: Harness) -> dict[str, Any]:
    """firmware/flagship-scenario.json: four engines + autonomous UART-RX->SPI route."""
    scenario = json.loads((FIRMWARE / "flagship-scenario.json").read_text())
    stimulus, expected = scenario["stimulus"], scenario["expected"]
    await h.start()
    peer = I2CPeer(stretch_cycles=3)
    bus = OpenDrainI2CBus(peer)
    spi = SpiTarget(0, [stimulus["spi_return_word"]])
    uart_in = UartSource(stimulus["uart_rx_words"], bit_cycles=stimulus["uart_bit_cycles"],
                         gap_cycles=stimulus["uart_interframe_idle_cycles"])
    uart_out = UartMonitor(bit_cycles=stimulus["uart_bit_cycles"])

    def pins(cycle: int, out: Outputs) -> int:
        pads = pad_value(out, 0xFF)
        pads = (pads & ~bus.mask) | (bus.resolve(out) & bus.mask)
        miso = spi.update(pads)
        pads = (pads & ~0x12) | uart_in.level(cycle) << 1 | miso << 4
        uart_out.sample(cycle, bit(pads, 0))
        return pads

    h.pins = pins
    h.samples = []
    for entry in scenario["images"]:
        image = await h.load_firmware(entry["source"].removesuffix(".source.json"))
        assert image["engine"] == entry["engine"]
    for prefill in scenario["prefill_tx"]:
        await h.command(SELECT, prefill["engine"])
        for word in prefill["words"]:
            await h.write(2, word)
    for route in scenario["routes"]:
        await h.command(ROUTE, route["source_engine"] | route["destination_engine"] << 2 | 16
                        | route["word_count"] << 5)
    await h.command(START, scenario["start_mask"])
    uart_in.start = h.cycle + 96
    started = h.cycle
    total = len(expected["spi_mosi_words"])

    def done() -> bool:
        return (len(uart_out.words) >= len(expected["uart_tx_words"]) and len(spi.received) >= total
                and peer.stops >= 1 and len(h.engine(2).rx) >= total)

    limit = 96 + len(uart_in.words) * uart_in.frame_cycles + 2000
    for _ in range(limit):
        if done():
            break
        await h.step()
        h.assert_no_faults()
    else:
        raise AssertionError("flagship scenario did not complete")
    elapsed = h.cycle - started
    await h.idle(BIT_CYCLES)
    assert uart_out.words == expected["uart_tx_words"], uart_out.words
    assert uart_decode(h.samples, pin=0, bit_cycles=BIT_CYCLES) == expected["uart_tx_words"]
    assert spi.received == expected["spi_mosi_words"], spi.received
    assert spi_decode(h.samples, clock=2, data=3, select=5) == expected["spi_mosi_words"]
    assert peer.received == expected["i2c_write_words"] and peer.stops == 1
    assert h.model.routes[1] is None, "route should be exhausted after its word count"
    assert list(h.engine(2).rx) == expected["engine2_rx_words"]
    assert_open_drain(h.samples, 0xC0)
    # Freeze everything (the idle UART receiver would otherwise time out), then drain.
    await h.command(STOP, scenario["start_mask"])
    await h.command(SELECT, 2)
    got = [await h.read(3) for _ in range(total)]
    assert got == expected["engine2_rx_words"], got
    h.assert_no_faults()
    return {"cycles_to_complete": elapsed}


class _NoFaults:
    """Observer: fail as soon as the model reports a host or engine fault."""

    def before(self, h: Harness, ui: int, pins: int) -> None:
        pass

    def after(self, h: Harness) -> None:
        h.assert_no_faults()


async def flagship_topup(h: Harness, name: str = "flagship-scenario-topup.json",
                         spi_image: str | None = None) -> dict[str, Any]:
    """firmware/flagship-scenario-topup.json: the flagship workload for 2..32-word queues.

    The host prefills at most ``fifo_words`` words, starts all engines, then
    polls: top up engine 0's UART TX queue, drain engine 2's RX queue (SPI MISO
    words). Every decision is taken from the chip's ready/valid pins
    (try_write/try_read give up after ``max_wait_cycles``), never from the model.
    ``spi_image`` replaces engine 2's SPI controller image (e.g. spi-controller-fast).
    """
    scenario = json.loads((FIRMWARE / name).read_text())
    stimulus, expected = scenario["stimulus"], scenario["expected"]
    depth = h.model.config.fifo_words
    assert depth == scenario["architecture"]["fifo_words"] or depth in scenario["also_valid_fifo_words"]
    wait = scenario["host_poll"]["max_wait_cycles"]
    await h.start()
    peer = I2CPeer(stretch_cycles=3)
    bus = OpenDrainI2CBus(peer)
    spi = SpiTarget(0, [stimulus["spi_return_word"]])
    uart_in = UartSource(stimulus["uart_rx_words"], bit_cycles=stimulus["uart_bit_cycles"],
                         gap_cycles=stimulus["uart_interframe_idle_cycles"])
    uart_out = UartMonitor(bit_cycles=stimulus["uart_bit_cycles"])

    def pins(cycle: int, out: Outputs) -> int:
        pads = pad_value(out, 0xFF)
        pads = (pads & ~bus.mask) | (bus.resolve(out) & bus.mask)
        miso = spi.update(pads)
        pads = (pads & ~0x12) | uart_in.level(cycle) << 1 | miso << 4
        uart_out.sample(cycle, bit(pads, 0))
        return pads

    h.pins = pins
    h.samples = []
    for entry in scenario["images"]:
        image_name = entry["source"].removesuffix(".source.json")
        if spi_image is not None and entry["engine"] == 2:
            image_name = spi_image
        image = await h.load_firmware(image_name)
        assert image["engine"] == entry["engine"]
    pending = {entry["engine"]: list(entry["words"]) for entry in scenario["host_tx"]}
    for engine, words in pending.items():  # engines are halted: exactly min(depth, n) fit
        await h.command(SELECT, engine)
        for _ in range(min(depth, len(words))):
            await h.write(2, words.pop(0))
    for route in scenario["routes"]:
        await h.command(ROUTE, route["source_engine"] | route["destination_engine"] << 2 | 16
                        | route["word_count"] << 5)
    await h.command(START, scenario["start_mask"])
    uart_in.start = h.cycle + 96
    started = h.cycle
    total = len(expected["spi_mosi_words"])
    drains = {entry["engine"]: entry["word_count"] for entry in scenario["host_rx_drain"]}
    drained: dict[int, list[int]] = {engine: [] for engine in drains}
    guard = _NoFaults()
    h.observers.append(guard)

    def done() -> bool:
        return (len(uart_out.words) >= len(expected["uart_tx_words"]) and len(spi.received) >= total
                and peer.stops >= 1 and all(len(drained[e]) >= n for e, n in drains.items())
                and not any(pending.values()))

    limit = 96 + len(uart_in.words) * uart_in.frame_cycles + 4000
    try:
        while not done():
            assert h.cycle - started < limit, "top-up flagship scenario did not complete"
            progressed = False
            for engine, words in pending.items():
                if words:
                    await h.command(SELECT, engine)
                    if await h.try_write(2, words[0], max_wait=wait):
                        words.pop(0)
                        progressed = True
            for engine, count in drains.items():
                if len(drained[engine]) < count:
                    await h.command(SELECT, engine)
                    value = await h.try_read(3, max_wait=wait)
                    if value is not None:
                        drained[engine].append(value)
                        progressed = True
            if not progressed:
                await h.idle(8)
    finally:
        h.observers.remove(guard)
    elapsed = h.cycle - started
    await h.idle(BIT_CYCLES)
    assert uart_out.words == expected["uart_tx_words"], uart_out.words
    assert uart_decode(h.samples, pin=0, bit_cycles=BIT_CYCLES) == expected["uart_tx_words"]
    assert spi.received == expected["spi_mosi_words"], spi.received
    assert spi_decode(h.samples, clock=2, data=3, select=5) == expected["spi_mosi_words"]
    assert peer.received == expected["i2c_write_words"] and peer.stops == 1
    assert h.model.routes[1] is None, "route should be exhausted after its word count"
    assert drained[2] == expected["engine2_rx_words"], drained
    assert_open_drain(h.samples, 0xC0)
    await h.command(STOP, scenario["start_mask"])
    await h.command(SELECT, 2)
    assert await h.status(RS_LEVELS) == 0
    h.assert_no_faults()
    return {"cycles_to_complete": elapsed}
