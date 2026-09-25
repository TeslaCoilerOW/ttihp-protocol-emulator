# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The flagship concurrent scenario (../firmware/flagship-scenario.json) with
every attached peer written by a third party.

Engine 0 UART TX (pin 0), engine 1 UART RX (pin 1), engine 2 SPI mode-0
controller (pins 2-5), engine 3 I2C controller (pins 6/7), plus the
autonomous route engine1 RX -> engine2 TX. The scenario's stimulus and
peer-independent expectations (UART TX words, SPI MOSI words, I2C write
words, route exhaustion) are taken from the JSON; the MISO words depend on
the SPI peer (SpiSlaveLoopback returns the previous MOSI byte instead of the
recipe's constant 0xA5).
"""

from __future__ import annotations

import json

import cocotb

import ext_harness  # noqa: F401  (first: puts ../test and vendor/cocotbext-* on sys.path)
from cocotbext.i2c import I2cMemory
from cocotbext.spi import SpiBus, SpiConfig
from cocotbext.spi.devices.generic import SpiSlaveLoopback
from cocotbext.uart import UartSink, UartSource
from ext_harness import EDGE_CHECK, SELECT, SKEWS, START, STOP, drain, load, start, uart_bit_cycles
from harness import FIRMWARE, ROUTE

ROUTE_ENABLE = 1 << 4  # isa.md ROUTE: bit4 enable

SCENARIO = json.loads((FIRMWARE / "flagship-scenario.json").read_text())


def _spi_loopback(dut) -> SpiSlaveLoopback:
    config = SpiConfig(word_width=8, cpol=False, cpha=False, msb_first=True, cs_active_low=True,
                       frame_spacing_ns=20)
    return SpiSlaveLoopback(SpiBus(dut, sclk_name="pad2", mosi_name="pad3", miso_name="py_spi_miso",
                                   cs_name="pad5"), config)


async def _flagship(dut, kind: str, skew: str) -> None:
    stimulus, expected = SCENARIO["stimulus"], SCENARIO["expected"]
    if kind == "verilog":
        # verilog-uart TX/RX, verilog-i2c i2c_slave at the recipe's address, and
        # additional passive listeners: cocotbext UartSink on pin 0 and nandland
        # SPI_Slave (mode 0, MISO not connected) on the SPI pins.
        peers = dict(sel_vuart_tx=1, sel_vi2c_slave=1, vi2cs_address=stimulus["i2c_ack_address"],
                     sel_py_spi_slave=1, nslave_mode=0, nslave_miso_en=0)
    else:
        # cocotbext-uart source/sink, cocotbext-spi loopback, cocotbext-i2c I2cMemory at the
        # recipe's address and a second I2cMemory at 0x50 that must stay silent.
        peers = dict(sel_py_uart_tx=1, sel_py_spi_slave=1, sel_py_i2c=1, sel_py_i2c_b=1)
    h, board = await start(dut, skew=skew, **peers)
    spi = _spi_loopback(dut)
    images = {}
    for entry in SCENARIO["images"]:
        image = await load(h, entry["source"].removesuffix(".source.json"))
        assert image["engine"] == entry["engine"]
        images[image["name"]] = image
    bit_cycles = uart_bit_cycles(images["uart-tx"])
    assert bit_cycles == uart_bit_cycles(images["uart-rx"]) == stimulus["uart_bit_cycles"]
    baud = images["uart-tx"]["clock_hz"] / bit_cycles
    board.set(uart_prescale=bit_cycles // 8)
    sink = UartSink(dut.pad0, baud=baud)
    memory = other = None
    if kind == "python":
        memory = I2cMemory(sda=dut.pad7, sda_o=dut.py_i2c_sda_o, scl=dut.pad6, scl_o=dut.py_i2c_scl_o,
                           addr=stimulus["i2c_ack_address"], size=256)
        other = I2cMemory(sda=dut.pad7, sda_o=dut.py_i2c_b_sda_o, scl=dut.pad6, scl_o=dut.py_i2c_b_scl_o,
                          addr=0x50, size=256)
        other.ptr = 0x11
    for prefill in SCENARIO["prefill_tx"]:
        await h.command(SELECT, prefill["engine"])
        for word in prefill["words"]:
            await h.write(2, word)
    for route in SCENARIO["routes"]:
        await h.command(ROUTE, route["source_engine"] | route["destination_engine"] << 2 | ROUTE_ENABLE
                        | route["word_count"] << 5)
    await h.command(START, SCENARIO["start_mask"])
    started = h.cycle
    await h.idle(96)  # idle line before the first start bit (as in ../test/scenarios.py)
    words = stimulus["uart_rx_words"]
    if kind == "verilog":
        board.fill("vutx_mem", words)
        board.set(vutx_len=len(words), vutx_gap=stimulus["uart_interframe_idle_cycles"], vutx_go=1)
    else:
        UartSource(dut.py_uart_rxd, baud=baud).write_nowait(words)
    total = len(expected["spi_mosi_words"])

    def i2c_done() -> bool:
        if kind == "verilog":
            return board.get("vi2cs_stops") >= 1
        return memory.ptr == expected["i2c_write_words"][1]

    def done() -> bool:
        return (board.get("vurx_count") >= len(expected["uart_tx_words"]) and sink.count() >= len(expected["uart_tx_words"])
                and len(h.engine(2).rx) >= total and i2c_done())

    limit = len(words) * (10 * bit_cycles + stimulus["uart_interframe_idle_cycles"]) + 4000
    for _ in range(limit):
        if done():
            break
        await h.step()
        h.assert_no_faults()
    else:
        raise AssertionError("flagship scenario did not complete")
    elapsed = h.cycle - started
    await h.idle(bit_cycles)
    assert board.log("vurx_log", "vurx_count") == expected["uart_tx_words"]
    assert board.get("vurx_frame_errors") == 0
    assert list(sink.read_nowait()) == expected["uart_tx_words"]
    assert await spi.get_contents() == expected["spi_mosi_words"][-1]
    if kind == "verilog":
        assert board.log("nslave_log", "nslave_count") == expected["spi_mosi_words"]
        assert board.log("vi2cs_log", "vi2cs_count") == [0x100 | expected["i2c_write_words"][1]]
        assert board.get("vi2cs_bus_address") == expected["i2c_write_words"][0] >> 1
    else:
        assert memory.ptr == expected["i2c_write_words"][1]
        assert other.ptr == 0x11, "the I2C device at 0x50 reacted to a transaction for 0x42"
    assert h.model.routes[1] is None, "route should be exhausted after its word count"
    assert board.get("od_violations") == 0
    await h.command(STOP, SCENARIO["start_mask"])
    got = await drain(h, 2, total)
    assert got == [0x00] + expected["spi_mosi_words"][:-1], got
    h.assert_no_faults()
    edges = board.coincidences("co_sck", "co_scl")
    h.log("flagship (%s peers, skew %s): complete %d cycles after START; same-edge pin moves %s", kind, skew,
          elapsed, edges)
    # SPI mode 0 samples on the rising edge; I2C data moves only while SCL is low.
    if EDGE_CHECK:
        assert edges["sck_mosi_rise"] == edges["sck_csn_rise"] == edges["sck_csn_fall"] == 0, edges
        assert edges["scl_sda_rise"] == edges["scl_sda_fall"] == 0, edges


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_flagship_verilog_peers(dut, skew):
    """Flagship with verilog-uart, verilog-i2c, cocotbext-spi and passive listeners."""
    await _flagship(dut, "verilog", skew)


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_flagship_cocotbext_peers(dut, skew):
    """Flagship with cocotbext-uart, cocotbext-spi and two cocotbext-i2c devices."""
    await _flagship(dut, "python", skew)
