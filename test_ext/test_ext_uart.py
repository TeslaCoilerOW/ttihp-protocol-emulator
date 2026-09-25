# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""UART firmware against third-party UARTs at the firmware's declared baud rate.

Peers: alexforencich/verilog-uart uart_rx/uart_tx (Verilog) and
alexforencich/cocotbext-uart UartSource/UartSink (Python). The baud rate is
derived from the image's own declaration (clock_hz / declared clocks per bit),
not from a constant in this file.
"""

from __future__ import annotations

import cocotb

import ext_harness  # noqa: F401  (first: puts ../test and vendor/cocotbext-* on sys.path)
from cocotbext.uart import UartSink, UartSource
from ext_harness import (RS_LEVELS, START, STOP, assert_released, data, load, rng, start, status,
                         uart_bit_cycles)

TX_WORDS = list(b"Jane Street TT!") + [0x00, 0xFF, 0x55, 0xAA, 0x80, 0x01]
RX_WORDS = [0x00, 0xFF, 0x55, 0xAA, 0x31, 0x7E, 0x80, 0x01] + list(b"independent peers")


@cocotb.test()
async def test_uart_tx_vs_third_party_receivers(dut):
    """uart-tx (engine 0, pin 0) decoded by verilog-uart uart_rx and cocotbext-uart UartSink."""
    h, board = await start(dut)
    words = data(rng("uart_tx"), TX_WORDS)
    image = await load(h, "uart-tx")
    bit_cycles = uart_bit_cycles(image)
    baud = image["clock_hz"] / bit_cycles
    board.set(uart_prescale=bit_cycles // 8)  # verilog-uart: prescale = f_clk / (baud * 8)
    sink = UartSink(dut.pad0, baud=baud, bits=8, stop_bits=1)
    h.log("uart-tx declares %d clocks/bit -> %.2f baud", bit_cycles, baud)
    for word in words[:8]:
        await h.write(2, word)
    await h.command(START, 1)
    for word in words[8:]:  # the rest under TX-FIFO backpressure
        await h.write(2, word)
    n = len(words)
    await h.run_until(lambda: board.get("vurx_count") >= n and sink.count() >= n,
                      (n + 2) * 12 * bit_cycles, "third-party UART receivers")
    await h.idle(2 * bit_cycles)
    assert board.log("vurx_log", "vurx_count") == words, board.log("vurx_log", "vurx_count")
    assert board.get("vurx_frame_errors") == 0
    assert list(sink.read_nowait()) == words
    assert await status(h, 0, RS_LEVELS) == 0
    h.assert_no_faults()
    await h.command(STOP, 1)
    await h.idle(2)
    assert_released(h, 0x01)


async def _uart_rx(dut, source_kind: str, baud_error: float | None = 0.0) -> None:
    peers = {"sel_vuart_tx": 1} if source_kind == "verilog" else {"sel_py_uart_tx": 1}
    generator = rng(f"uart_rx:{source_kind}:{baud_error}")
    words = data(generator, RX_WORDS)
    if baud_error is None:  # cocotbext source at the nominal rate, or a random error of up to 3 %
        baud_error = 0.0 if generator is None else generator.uniform(-0.03, 0.03)
    h, board = await start(dut, **peers)
    image = await load(h, "uart-rx")
    bit_cycles = uart_bit_cycles(image)
    baud = image["clock_hz"] / bit_cycles
    h.log("uart-rx declares %d clocks/bit -> %.2f baud; source: %s, baud error %+.2f %%", bit_cycles, baud, source_kind,
          100 * baud_error)
    board.set(uart_prescale=bit_cycles // 8)
    await h.command(START, 2)
    # A UART receiver must see the idle (high) line before the first start bit:
    # the firmware waits for idle, then for a falling edge. Give it one bit
    # period of idle; its idle/start waits are bounded at 12 bit periods.
    await h.idle(bit_cycles)
    if source_kind == "verilog":
        board.fill("vutx_mem", words)
        board.set(vutx_len=len(words), vutx_gap=0, vutx_go=1)  # back-to-back frames
    else:
        source = UartSource(dut.py_uart_rxd, baud=baud * (1 + baud_error), bits=8, stop_bits=1)
        source.write_nowait(words)
    engine, got = h.engine(1), []
    for _ in words:  # the host drains concurrently (the RX FIFO holds 8 words)
        await h.run_until(lambda: engine.rx or engine.fault, 16 * bit_cycles, "UART RX word")
        assert not engine.fault, f"uart-rx fault {engine.fault} after receiving {got}"
        got.append(await h.read(3))
    assert got == words, f"UART RX read {got} != {words}"
    h.assert_no_faults()
    await h.command(STOP, 2)
    assert await status(h, 1, RS_LEVELS) == 0


@cocotb.test()
async def test_uart_rx_vs_verilog_uart_tx(dut):
    """uart-rx (engine 1, pin 1) receives back-to-back frames from verilog-uart uart_tx."""
    await _uart_rx(dut, "verilog")


@cocotb.test()
async def test_uart_rx_vs_cocotbext_source(dut):
    """uart-rx (engine 1, pin 1) receives frames from cocotbext-uart UartSource."""
    await _uart_rx(dut, "python", None)


@cocotb.test()
@cocotb.parametrize(error_percent=[-3, 3])
async def test_uart_rx_baud_tolerance(dut, error_percent):
    """uart-rx receives back-to-back cocotbext-uart frames sent 3 % slow or fast."""
    await _uart_rx(dut, "python", error_percent / 100)
