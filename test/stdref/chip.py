# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Minimal cocotb driver for the Tiny Tapeout top, without a reference model.

The stdref tests check the design against line_std_ref only, so this driver
does not use harness.CocotbHarness (which compares every cycle with the
lockstep model).  It implements the host interface of docs/isa.md ("Host
interface"): eight little-endian nibbles per word, ready AND valid, one
bubble cycle per window change.

Cycle ``n`` ends with rising edge ``n``.  At the falling edge inside cycle n
the driver reads ``uio_out``/``uio_oe`` (the pad state of cycle n), computes
``uio_in`` for cycle n with ``pad_fn(n, out, oe)`` and records all three.
"""

from __future__ import annotations

from typing import Callable, Optional

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

CLOCK_NS = 20
SELECT, BEGIN, COMMIT, OWN, START, STOP, ROUTE, CLEAR, READ_SELECT = range(9)
UI_WVALID, UI_RREADY = 16, 32
UO_WREADY, UO_RVALID = 16, 32


class Chip:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.n = 0
        self.window = 0
        self.pad_fn: Optional[Callable[[int, int, int], int]] = None
        self.static_pins = 0
        self.recording = False
        self.out: dict[int, int] = {}
        self.oe: dict[int, int] = {}
        self.pins_in: dict[int, int] = {}
        self._falling = FallingEdge(dut.clk)
        self._rising = RisingEdge(dut.clk)

    async def begin(self, reset_cycles: int = 4) -> None:
        dut = self.dut
        dut.ena.value = 1
        dut.rst_n.value = 0
        dut.ui_in.value = 0
        dut.uio_in.value = 0
        cocotb.start_soon(Clock(dut.clk, CLOCK_NS, unit="ns").start())
        await self.reset(reset_cycles)

    async def reset(self, cycles: int = 2) -> None:
        self.window = 0
        for _ in range(cycles):
            await self.cycle(0, reset=True)
        await self.cycle(0)

    @staticmethod
    def _value(handle) -> int:
        value = handle.value
        return value.to_unsigned() if value.is_resolvable else -1

    async def cycle(self, ui: Optional[int] = None, *, reset: bool = False) -> int:
        """One clock; returns uo_out sampled before the edge (after ui is applied)."""
        if ui is None:
            ui = self.window << 6
        await self._falling
        out = self._value(self.dut.uio_out)
        oe = self._value(self.dut.uio_oe)
        pins = self.pad_fn(self.n, out, oe) if self.pad_fn else self.static_pins
        self.dut.ui_in.value = ui
        self.dut.uio_in.value = pins & 0xFF
        self.dut.rst_n.value = 0 if reset else 1
        if self.recording:
            self.out[self.n] = out
            self.oe[self.n] = oe
            self.pins_in[self.n] = pins & 0xFF
        await ReadOnly()
        uo = self._value(self.dut.uo_out)
        await self._rising
        self.n += 1
        return uo

    async def idle(self, count: int) -> None:
        for _ in range(count):
            await self.cycle()

    async def set_window(self, window: int) -> None:
        if self.window != window:
            self.window = window
            await self.cycle()

    async def write(self, window: int, word: int, *, timeout: int = 100000) -> int:
        """Write a word; returns the cycle whose edge accepted the last nibble."""
        await self.set_window(window)
        accepted = -1
        for nibble in range(8):
            ui = window << 6 | UI_WVALID | ((word >> (4 * nibble)) & 15)
            for _ in range(timeout):
                n = self.n
                uo = await self.cycle(ui)
                if uo >= 0 and uo & UO_WREADY:
                    accepted = n
                    break
            else:
                raise TimeoutError(f"write backpressure (window {window})")
        await self.cycle()
        return accepted

    async def try_write(self, window: int, word: int, *, max_wait: int) -> bool:
        """Write a word unless a nibble waits longer than ``max_wait`` (then bounce)."""
        await self.set_window(window)
        for nibble in range(8):
            ui = window << 6 | UI_WVALID | ((word >> (4 * nibble)) & 15)
            for _ in range(max_wait + 1):
                uo = await self.cycle(ui)
                if uo >= 0 and uo & UO_WREADY:
                    break
            else:
                await self.set_window(window ^ 1)
                return False
        await self.cycle()
        return True

    async def read(self, window: int, *, timeout: int = 100000) -> int:
        await self.set_window(window)
        result = 0
        for nibble in range(8):
            for _ in range(timeout):
                uo = await self.cycle(window << 6 | UI_RREADY)
                if uo >= 0 and uo & UO_RVALID:
                    result |= (uo & 15) << (4 * nibble)
                    break
            else:
                raise TimeoutError(f"read data did not arrive (window {window})")
        return result

    async def command(self, opcode: int, payload: int = 0) -> int:
        return await self.write(0, opcode << 24 | payload)

    async def status(self, selection: int) -> int:
        await self.command(READ_SELECT, selection)
        await self.set_window(1)
        return await self.read(0)

    async def load(self, engine: int, words: list[int], *, owned: int, open_drain: int = 0) -> None:
        await self.command(SELECT, engine)
        await self.command(BEGIN)
        await self.command(OWN, owned | open_drain << 8)
        for word in words:
            await self.write(1, word)
        await self.command(COMMIT, len(words))

    async def fill_tx(self, engine: int, words: list[int], *, max_wait: int = 50) -> int:
        await self.command(SELECT, engine)
        written = 0
        for word in words:
            if not await self.try_write(2, word, max_wait=max_wait):
                break
            written += 1
        return written

    async def start(self, mask: int) -> int:
        """START; returns the issue cycle of every started engine's first instruction."""
        edge = await self.command(START, mask)
        return edge + 1

    async def drain(self, engine: int, count: int) -> list[int]:
        await self.command(SELECT, engine)
        return [await self.read(3) for _ in range(count)]
