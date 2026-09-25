# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Cycle-accurate lockstep harness and nibble host driver for cocotb.

Every clock cycle the harness

1. at the falling edge reads the DUT outputs left by the previous rising edge
   and compares them with the reference model's post-edge prediction (the same
   check the monorepo's generated differential testbenches made);
2. computes the uio_in pad values from the DUT's *actual* uio_out/uio_oe via a
   pin supplier (protocol peers, open-drain bus resolution, random stimulus);
3. drives ui_in/uio_in/rst_n/ena, waits for ReadOnly and compares the pre-edge
   outputs (what a real host sees, including the combinational window-change
   bubble on ready/valid) with ``Reference.outputs(ui)``;
4. lets the rising edge happen and steps ``Reference.tick`` with the same
   inputs.

The host driver makes its handshake decisions from the DUT's sampled pins, not
from the model, so it behaves like a real host. Read nibbles are compared only
while read-valid is high (isa.md: unused read-nibble values are unspecified).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import variants
from model.reference import Config, Outputs, Reference
from model.scoreboards import WireSample
from model.variant import make_reference

REPO = Path(__file__).resolve().parent.parent
FIRMWARE = REPO / "firmware"
CLOCK_NS = 20  # 50 MHz, info.yaml clock_hz
# READ_SELECT 7 of the design under test: 2 for the design of record, 3 for
# variants that restrict the ISA (PE_VARIANT, see variants.py).
ISA_VERSION = variants.options(variants.design_config()).isa_version

# Host commands (isa.md, window 0).
SELECT, BEGIN, COMMIT, OWN, START, STOP, ROUTE, CLEAR = range(8)
READ_SELECT, EVENT, FLUSH, TRIGGER = 8, 9, 10, 11
# READ_SELECT values.
RS_STATUS, RS_TIMESTAMP, RS_LEVELS, RS_PC, RS_EVENT, RS_COUNT, RS_HELD_RX, RS_VERSION = range(8)
# ui/uo bit positions.
UI_WVALID, UI_RREADY = 16, 32
UO_WREADY, UO_RVALID, UO_IRQ, UO_FAULT = 16, 32, 64, 128

PinSupplier = Callable[[int, Outputs], int]


class LockstepMismatch(AssertionError):
    """DUT and reference model disagree on a public output."""


@dataclass(frozen=True)
class Step:
    cycle: int
    ui: int
    pins: int
    reset: bool
    enabled: bool
    pre: Outputs       # DUT outputs sampled before the rising edge
    expected: Outputs  # model outputs after the edge (checked next cycle)


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value in (None, "") else int(value, 0)


def fmt(out: Outputs | None) -> str:
    if out is None:
        return "X"
    return f"uo={out.uo:02x} uio_out={out.uio_out:02x} uio_oe={out.uio_oe:02x}"


def instruction(op: int, a: int = 0, b: int = 0, c: int = 0) -> int:
    return (op << 24) | (a << 16) | (b << 8) | c


def immediate(op: int, value: int = 0) -> int:
    return (op << 24) | (value & 0xFFFFFF)


def image_dir() -> Path:
    """Firmware image set: firmware/ for the design of record; for a variant the
    images reassembled for it (build/variants/<name>/firmware, scripts/gen_variants.sh)
    when present, else firmware/. PE_FIRMWARE=dir overrides."""
    explicit = os.environ.get("PE_FIRMWARE")
    if explicit:
        return Path(explicit)
    variant = variants.name()
    candidate = REPO / "build" / "variants" / variant / "firmware"
    return candidate if variant != "base" and candidate.is_dir() else FIRMWARE


def load_image(name: str) -> dict[str, Any]:
    """Load <image set>/<name>.image.json and verify its source/bytecode identity
    (and, for a variant image set, that it was assembled for the design under test)."""
    directory = image_dir()
    image: dict[str, Any] = json.loads((directory / f"{name}.image.json").read_text())
    if (image["schema_version"] != "protocol-emulator.firmware-image.v1"
            or image["isa_version"] not in range(1, ISA_VERSION + 1)):
        raise ValueError(f"unsupported firmware image {name}")
    if directory != FIRMWARE:
        design = design_config()
        architecture = image["architecture"]
        if (architecture["fifo_words"], architecture["data_width"], architecture["engine_count"]) != \
                (design.fifo_words, design.width, design.engines):
            raise ValueError(f"firmware {name} in {directory} targets {architecture}, not the design under test")
    source = (directory / f"{name}.source.json").read_bytes()
    if hashlib.sha256(source).hexdigest() != image["source_sha256"]:
        raise ValueError(f"firmware {name}: source identity mismatch")
    payload = b"".join(word.to_bytes(4, "little") for word in image["words"])
    if hashlib.sha256(payload).hexdigest() != image["bytecode_sha256"]:
        raise ValueError(f"firmware {name}: bytecode identity mismatch")
    return image


def model_config(architecture: dict[str, Any]) -> Config:
    return Config(engines=architecture["engine_count"], width=architecture["data_width"],
                  program_words=architecture["program_words"], fifo_words=architecture["fifo_words"],
                  fused=architecture["issue"] == "fused", prefetch=architecture["prefetch"])


def design_config() -> Config:
    """Configuration of the design under test (configs/instruction-sram-32.json by default;
    PE_VARIANT/PE_CONFIG select another, see variants.py)."""
    return variants.design_config()


class Harness:
    """Host driver + reference model; subclasses supply ``step`` (one clock)."""

    def __init__(self, *, config: Config | None = None, history: int = 24) -> None:
        self.model: Reference = make_reference(config or design_config())
        self.cycle = 0
        self.window = 0
        self.pins: int | PinSupplier = 0
        self.samples: list[WireSample] | None = None  # set to [] to record pad values
        self.history: deque[Step] = deque(maxlen=history)
        self.observers: list[Any] = []  # objects with before(h, ui, pins) / after(h)
        self.checking = False
        self.expected: Outputs | None = None
        self.last_pre = Outputs(0, 0, 0)
        self.context: Callable[[], str] | None = None
        self.quiet = False  # suppress mismatch logging (minimizer re-runs)
        self.diverged = False  # a mismatch was raised since the last reset edge

    async def start(self, reset_cycles: int = 4) -> None:
        await self.reset(reset_cycles)

    async def reset(self, cycles: int = 2, *, deselect: bool = False) -> None:
        """Reset (rst_n low) or deselect (ena low) for ``cycles`` clocks.

        Also resynchronizes the checker after an aborted run: comparison
        restarts after the first reset edge. On variants with a reset latency
        the checker stays on through the raw reset cycles (the chip keeps
        running until the synchronized reset applies) unless the run diverged.
        """
        self.window = 0
        if getattr(self.model, "reset_latency", 0) and self.checking and not self.diverged:
            pass  # keep comparing: model and DUT apply the delayed reset together
        else:
            self.checking = False
            self.expected = None
        for _ in range(cycles):
            if deselect:
                await self.step(0, enabled=False)
            else:
                await self.step(0, reset=True)
        if deselect and not self.diverged:
            self.checking = True
        await self.settle()

    async def settle(self) -> None:
        """Idle while a reset request is still in flight: variants with a reset
        synchronizer apply and release reset two edges late (model/variant.py).
        No cycles for the design of record."""
        while getattr(self.model, "settling", False):
            await self.step()

    def clear_active(self, reset: bool, enabled: bool) -> bool | None:
        """Is the chip-wide clear active for these raw inputs (None = unknown)?"""
        clear = getattr(self.model, "clear_active", None)
        return (reset or not enabled) if clear is None else clear(reset or not enabled)

    @property
    def async_assert(self) -> bool:
        """Reset clears the state before the edge (asynchronous-assertion variants)."""
        return bool(getattr(getattr(self.model, "options", None), "async_assert", False))

    def log(self, message: str, *args: Any) -> None:
        print(message % args if args else message)

    def _supply(self, post: Outputs, pins: int | None) -> int:
        if pins is None:
            supplier = self.pins
            pins = supplier(self.cycle, post) if callable(supplier) else supplier
        return pins & 0xFF

    def _advance(self, ui: int, pins: int, reset: bool, enabled: bool, pre: Outputs) -> None:
        self.expected = self.model.tick(ui, pins, reset=reset, enabled=enabled)
        applied = getattr(self.model, "reset_applied", None)
        if (reset or not enabled) if applied is None else applied:
            self.checking = True
            self.diverged = False
        for observer in self.observers:
            observer.after(self)
        if self.samples is not None:
            self.samples.append(WireSample(self.cycle, pins, pre.uio_oe))
        self.history.append(Step(self.cycle, ui, pins, reset, enabled, pre, self.expected))
        self.last_pre = pre
        self.cycle += 1

    async def step(self, ui: int | None = None, *, reset: bool = False, enabled: bool = True,
                   pins: int | None = None) -> Outputs:
        raise NotImplementedError


    async def idle(self, count: int = 1) -> None:
        for _ in range(count):
            await self.step()

    async def run_until(self, predicate: Callable[[], bool], limit: int, what: str) -> int:
        for n in range(limit):
            if predicate():
                return n
            await self.step()
        raise AssertionError(f"timeout after {limit} cycles waiting for {what}")

    # ------------------------------------------------------------ host driver
    async def set_window(self, window: int) -> None:
        """Change the host window; the change costs one bubble cycle."""
        if self.window != window:
            self.window = window
            await self.step()

    async def write(self, window: int, word: int, *, timeout: int = 100000) -> None:
        """Write one 32-bit word as eight little-endian nibbles (ready AND valid)."""
        if window not in (0, 1, 2) or not 0 <= word < 1 << 32:
            raise ValueError("invalid host write")
        await self.set_window(window)
        for nibble in range(8):
            ui = window << 6 | UI_WVALID | (word >> (4 * nibble) & 15)
            for _ in range(timeout):
                if (await self.step(ui)).uo & UO_WREADY:
                    break
            else:
                raise TimeoutError(f"host write backpressure did not clear (window {window})")
        await self.step()

    async def read(self, window: int = 0, *, timeout: int = 100000, pauses: Iterable[int] = ()) -> int:
        """Read one word; window 0 = selected status, window 3 = selected RX FIFO."""
        if window not in (0, 3):
            raise ValueError("invalid host read")
        await self.set_window(window)
        delays = iter(pauses)
        result = 0
        for nibble in range(8):
            await self.idle(next(delays, 0))
            for _ in range(timeout):
                sampled = await self.step(window << 6 | UI_RREADY)
                if sampled.uo & UO_RVALID:
                    result |= (sampled.uo & 15) << (4 * nibble)
                    break
            else:
                raise TimeoutError(f"host read data did not arrive (window {window})")
        return result

    async def bounce(self) -> None:
        """Leave the current window for one cycle (abandons partial transfers)."""
        await self.set_window(self.window ^ 1)

    async def try_write(self, window: int, word: int, *, max_wait: int, nibbles: int = 8) -> bool:
        """Like write(), but gives up (window bounce) after ``max_wait`` stalled cycles
        on a nibble, or after ``nibbles`` < 8 accepted nibbles (deliberate abandon)."""
        await self.set_window(window)
        for nibble in range(nibbles):
            ui = window << 6 | UI_WVALID | (word >> (4 * nibble) & 15)
            for _ in range(max_wait + 1):
                if (await self.step(ui)).uo & UO_WREADY:
                    break
            else:
                await self.bounce()
                return False
        if nibbles < 8:
            await self.bounce()
            return False
        await self.step()
        return True

    async def try_read(self, window: int, *, max_wait: int, nibbles: int = 8,
                       pauses: Iterable[int] = ()) -> int | None:
        """Like read(), but abandons (window bounce) on timeout or after ``nibbles`` < 8."""
        await self.set_window(window)
        delays = iter(pauses)
        result = 0
        for nibble in range(nibbles):
            await self.idle(next(delays, 0))
            for _ in range(max_wait + 1):
                sampled = await self.step(window << 6 | UI_RREADY)
                if sampled.uo & UO_RVALID:
                    result |= (sampled.uo & 15) << (4 * nibble)
                    break
            else:
                await self.bounce()
                return None
        if nibbles < 8:
            await self.bounce()
            return None
        return result

    async def command(self, opcode: int, payload: int = 0) -> None:
        if not 0 <= opcode <= 255 or not 0 <= payload < 1 << 24:
            raise ValueError("invalid host command")
        await self.write(0, opcode << 24 | payload)

    async def status(self, selection: int = RS_STATUS) -> int:
        """READ_SELECT then read window 0 (a window bounce drops a stale snapshot)."""
        await self.command(READ_SELECT, selection)
        self.window = 1
        await self.step()
        return await self.read(0)

    async def load(self, engine: int, words: Iterable[int], *, ownership: int = 0,
                   open_drain: int = 0) -> None:
        image = tuple(words)
        await self.command(SELECT, engine)
        await self.command(BEGIN)
        await self.command(OWN, ownership | open_drain << 8)
        for word in image:
            await self.write(1, word)
        await self.command(COMMIT, len(image))

    async def load_firmware(self, name: str) -> dict[str, Any]:
        image = load_image(name)
        version = await self.status(RS_VERSION)
        assert version >= image["isa_version"], f"{name} needs ISA {image['isa_version']}, device reports {version}"
        await self.load(image["engine"], image["words"], ownership=image["owned_pins"],
                        open_drain=image["open_drain"])
        return image

    # -------------------------------------------------------------- helpers
    def engine(self, index: int):  # noqa: ANN201 - model.reference.Engine
        return self.model.engines[index]

    def assert_no_faults(self) -> None:
        faults = {i: e.fault for i, e in enumerate(self.model.engines) if e.fault}
        assert not self.model.host_fault and not faults, f"host_fault={self.model.host_fault} engine faults={faults}"


class ModelHarness(Harness):
    """Model-only backend (no simulator) for developing scenarios quickly."""

    async def step(self, ui: int | None = None, *, reset: bool = False, enabled: bool = True,
                   pins: int | None = None) -> Outputs:
        if ui is None:
            ui = self.window << 6
        pins = self._supply(self.model.outputs(), pins)
        pre = self.model.outputs(ui, reset=reset, enabled=enabled)
        for observer in self.observers:
            observer.before(self, ui, pins)
        self._advance(ui, pins, reset, enabled, pre)
        return pre


def run_model(scenario: Callable[[Harness], Any], harness: Harness | None = None) -> Harness:
    """Run an async scenario against the model only (no event loop needed)."""
    harness = harness or ModelHarness()
    coroutine = scenario(harness)
    try:
        coroutine.send(None)
    except StopIteration:
        return harness
    raise RuntimeError("model-only scenario awaited a simulator trigger")


class CocotbHarness(Harness):
    """One DUT + one Reference, advanced together one clock at a time."""

    def __init__(self, dut: Any, *, config: Config | None = None, history: int = 24) -> None:
        super().__init__(config=config, history=history)
        self.dut = dut
        self._clk, self._ui, self._uio_in = dut.clk, dut.ui_in, dut.uio_in
        self._rst_n, self._ena = dut.rst_n, dut.ena
        self._uo, self._uio_out, self._uio_oe = dut.uo_out, dut.uio_out, dut.uio_oe
        self._clock_started = False
        # Imported here so the model-only backend works outside a simulator.
        import cocotb
        from cocotb.clock import Clock
        from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
        self._cocotb, self._Clock = cocotb, Clock
        self._falling, self._readonly, self._rising = FallingEdge(dut.clk), ReadOnly(), RisingEdge(dut.clk)

    def log(self, message: str, *args: Any) -> None:
        self.dut._log.info(message, *args)

    async def start(self, reset_cycles: int = 4) -> None:
        if not self._clock_started:
            self._ena.value = 1
            self._rst_n.value = 0
            self._ui.value = 0
            self._uio_in.value = 0
            self._cocotb.start_soon(self._Clock(self._clk, CLOCK_NS, unit="ns").start())
            self._clock_started = True
        await self.reset(reset_cycles)

    def _read(self) -> Outputs | None:
        uo, out, oe = self._uo.value, self._uio_out.value, self._uio_oe.value
        if not (uo.is_resolvable and out.is_resolvable and oe.is_resolvable):
            return None
        return Outputs(uo.to_unsigned(), out.to_unsigned(), oe.to_unsigned())

    def _raw(self) -> str:
        return f"uo={self._uo.value} uio_out={self._uio_out.value} uio_oe={self._uio_oe.value}"

    def _fail(self, what: str, got: Outputs | None, want: Outputs, ui: int, pins: int) -> None:
        lines = [f"LOCKSTEP MISMATCH ({what}) at cycle {self.cycle}: ui={ui:02x} uio_in={pins:02x}",
                 f"  DUT:   {fmt(got) if got is not None else self._raw()}",
                 f"  model: {fmt(want)} (read nibble ignored when read-valid is low)",
                 "  recent cycles (cycle ui uio_in | DUT pre-edge | model post-edge):"]
        for s in self.history:
            lines.append(f"    {s.cycle:7d} {s.ui:02x} {s.pins:02x} | {fmt(s.pre)} | {fmt(s.expected)}")
        if self.context is not None:
            lines.append(self.context())
        message = "\n".join(lines)
        if not self.quiet:
            self.dut._log.error(message)
        self.diverged = True
        raise LockstepMismatch(message)

    @staticmethod
    def _equal(got: Outputs | None, want: Outputs) -> bool:
        if got is None:
            return False
        nibble_mask = 0xFF if want.uo & UO_RVALID else 0xF0
        return ((got.uo & nibble_mask) == want.uo and got.uio_out == want.uio_out
                and got.uio_oe == want.uio_oe)

    async def step(self, ui: int | None = None, *, reset: bool = False, enabled: bool = True,
                   pins: int | None = None) -> Outputs:
        """Advance one clock; return the DUT outputs sampled before the edge."""
        if ui is None:
            ui = self.window << 6
        await self._falling
        post = self._read()
        if self.checking and self.expected is not None and not self._equal(post, self.expected):
            previous = self.history[-1] if self.history else None
            self._fail("post-edge", post, self.expected, previous.ui if previous else ui,
                       previous.pins if previous else 0)
        pins = self._supply(post if post is not None else self.model.outputs(), pins)
        self._ui.value = ui
        self._uio_in.value = pins
        self._rst_n.value = 0 if reset else 1
        self._ena.value = 1 if enabled else 0
        await self._readonly
        pre = self._read()
        if self.checking:
            clear = self.clear_active(reset, enabled)
            if not clear:
                want = self.model.outputs(ui)
                if not self._equal(pre, want):
                    self._fail("pre-edge", pre, want, ui, pins)
            elif self.async_assert:
                # Asynchronous assertion: state and outputs are already reset.
                want = self.model.outputs(ui, reset=reset, enabled=enabled)
                if not self._equal(pre, want):
                    self._fail("pre-edge (asynchronous reset)", pre, want, ui, pins)
        for observer in self.observers:
            observer.before(self, ui, pins)
        await self._rising
        if pre is None:
            pre = Outputs(0, 0, 0)
        self._advance(ui, pins, reset, enabled, pre)
        return pre


def pad_value(out: Outputs, external: int) -> int:
    """Pad level: driven bits from the DUT, undriven bits from the environment."""
    return (out.uio_out & out.uio_oe) | (external & ~out.uio_oe & 0xFF)
