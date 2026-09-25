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
    for word in words[:8]:
        await h.write(2, word)
    await h.command(START, 1)
    await h.idle(3)
    started.append(h.cycle)
    for word in words[8:]:  # remaining bytes under TX backpressure
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
    for word in tx:
        await h.write(2, word)
    await h.command(START, 4)
    limit = len(tx) * (16 * 32 + 64) + 200
    await h.run_until(lambda: len(h.engine(2).rx) == len(tx), limit, f"SPI mode{mode} transfers")
    await h.idle(8)
    assert not target.violations, target.violations
    assert target.received == tx, f"SPI target received {target.received} != {tx}"
    cpol, cpha = mode >> 1, mode & 1
    assert spi_decode(h.samples, clock=2, data=3, select=5, cpol=cpol, cpha=cpha) == tx
    got = [await h.read(3) for _ in tx]
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
