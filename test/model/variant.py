# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Configuration variants of the ISA2 reference model (docs/isa.md, "Configuration variants").

Written for this repository; not part of the monorepo copy. ``reference.py``
stays verbatim: every variant difference is expressed here as a subclass, and
the base options (``Options()``) reproduce ``Reference`` exactly.

Knobs (the optional ``"options"`` object of a refinement config):

``reset``
    Where the chip-wide clear comes from. ``raw`` below is "reset requested"
    (``rst_n`` low or ``ena`` low) as presented before a rising edge.

    ``sync``                synchronous clear from ``raw`` (the design of record).
    ``sync_registered``     ``raw`` passes through two flops (q1 <= raw, q2 <= q1);
                            the synchronous clear is q2, so reset assertion and
                            release both take effect two edges later.
    ``async``               every register is asynchronously reset by ``raw``;
                            FLUSH stays synchronous. Edge behaviour equals ``sync``
                            when the inputs change between edges; in addition the
                            state (and so every public output) is already reset
                            before the first reset edge.
    ``async_sync_release``  asynchronous assertion from ``raw``; release through
                            two flops that ``raw`` sets asynchronously
                            (q1 <= 0, q2 <= q1 on edges without ``raw``). The
                            clear is ``raw | q2``: the chip leaves reset two edges
                            after ``raw`` falls.
``fifo_storage_reset``, ``narrow_image_regs``
    Contract-neutral implementation choices: no model change.
``host_nibble_slots``, ``split_command_decode``, ``split_engine_issue``,
``split_instruction_decode``, ``fifo_write_staging``, ``clear_outputs_only``,
``keep_counter_increments``, ``fifo_write_free_slot``
    Timing restructuring knobs (docs/timing-closure.md). They change how the
    logic is built, not what it computes: no model change.
``fifo_words``
    Architecture field; 2 and 4 are allowed in addition to 8 and 32.
``debug_counters``
    False removes the completed-instruction counters: READ_SELECT 5 reads 0.
``pc_bits``
    ``saturating_7``: every PC write (jump, loop and branch targets, next PC)
    saturates to 127. Out-of-image PCs still fault with code 2; READ_SELECT 3
    reads the saturated value.
``shift``
    ``byte_lane``: SHL/SHR accept only c in {0, 8, 16, 24} (and c < width);
    any other count faults with code 1.

READ_SELECT 7 reads 3 when debug_counters is false, pc_bits is saturating_7
or shift is byte_lane, and 2 otherwise.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .reference import Config, Engine, Fault, Outputs, Reference

RESET_STYLES = ("sync", "sync_registered", "async", "async_sync_release")
PC_BITS = ("full", "saturating_7")
SHIFTS = ("barrel", "byte_lane")
FIFO_WORDS = (2, 4, 8, 32)
TIMING_KEYS = ("host_nibble_slots", "split_command_decode", "split_engine_issue",
               "split_instruction_decode", "fifo_write_staging", "clear_outputs_only",
               "keep_counter_increments", "fifo_write_free_slot")
OPTION_KEYS = ("reset", "fifo_storage_reset", "narrow_image_regs", "debug_counters", "pc_bits", "shift",
               *TIMING_KEYS)


@dataclass(frozen=True)
class Options:
    reset: str = "sync"
    fifo_storage_reset: bool = False
    narrow_image_regs: bool = False
    debug_counters: bool = True
    pc_bits: str = "full"
    shift: str = "barrel"
    host_nibble_slots: bool = False
    split_command_decode: bool = False
    split_engine_issue: bool = False
    split_instruction_decode: bool = False
    fifo_write_staging: bool = False
    clear_outputs_only: bool = False
    keep_counter_increments: bool = False
    fifo_write_free_slot: bool = False

    def __post_init__(self) -> None:
        if self.reset not in RESET_STYLES:
            raise ValueError(f"unsupported reset style {self.reset!r}")
        if self.pc_bits not in PC_BITS:
            raise ValueError(f"unsupported pc_bits {self.pc_bits!r}")
        if self.shift not in SHIFTS:
            raise ValueError(f"unsupported shift {self.shift!r}")
        for name in ("fifo_storage_reset", "narrow_image_regs", "debug_counters", *TIMING_KEYS):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"option {name} must be a boolean")

    @staticmethod
    def from_json(data: dict[str, Any] | None) -> Options:
        data = dict(data or {})
        unknown = sorted(set(data) - set(OPTION_KEYS))
        if unknown:
            raise ValueError(f"unknown variant options {unknown}")
        return Options(**data)

    @property
    def isa_version(self) -> int:
        restricted = (not self.debug_counters or self.pc_bits != "full" or self.shift != "barrel")
        return 3 if restricted else 2

    @property
    def reset_latency(self) -> int:
        """Extra edges between a reset request and the chip being usable again."""
        return 2 if self.reset in ("sync_registered", "async_sync_release") else 0

    @property
    def async_assert(self) -> bool:
        """True when a reset request clears the state before the next edge."""
        return self.reset in ("async", "async_sync_release")

    @property
    def is_base(self) -> bool:
        return self == Options()


@dataclass(frozen=True)
class VariantConfig(Config):
    """``Config`` plus variant options; also admits 2- and 4-word FIFOs."""

    options: Options = field(default_factory=Options)

    def __post_init__(self) -> None:  # replaces Config's (which admits only 8/32 FIFO words)
        if self.engines not in (2, 4) or self.width not in (16, 32):
            raise ValueError("unsupported engine count or datapath")
        if self.program_words not in (32, 64, 128) or self.fifo_words not in FIFO_WORDS:
            raise ValueError("unsupported storage capacity")

    @property
    def base(self) -> Config:
        return Config(engines=self.engines, width=self.width, program_words=self.program_words,
                      fifo_words=self.fifo_words, fused=self.fused, prefetch=self.prefetch)


def config_from_json(document: dict[str, Any]) -> VariantConfig:
    """A refinement (or architecture) config document -> VariantConfig."""
    architecture = document.get("architecture", document)
    options = Options.from_json(document.get("options"))
    return VariantConfig(engines=architecture["engine_count"], width=architecture["data_width"],
                         program_words=architecture["program_words"],
                         fifo_words=architecture["fifo_words"], fused=architecture["issue"] == "fused",
                         prefetch=architecture["prefetch"], options=options)


def load_config(path: str | Path) -> VariantConfig:
    return config_from_json(json.loads(Path(path).read_text()))


def describe(config: Config) -> str:
    options = config.options if isinstance(config, VariantConfig) else Options()
    fields = {k: v for k, v in asdict(options).items() if k not in TIMING_KEYS or v}
    return (f"engines={config.engines} width={config.width} program_words={config.program_words} "
            f"fifo_words={config.fifo_words} options={fields} isa={options.isa_version}")


class VariantReference(Reference):
    """``Reference`` with the variant options applied (see module docstring).

    Reset modelling: ``tick``/``outputs`` still take the raw ``reset``/``enabled``
    inputs of the cycle. ``reset_applied`` tells whether the last ``tick`` cleared
    the state; ``settling`` whether an earlier request is still in flight.
    The two reset-synchronizer flops start unknown (``None``) unless
    ``settled=True`` (as after a long-past power-on reset).
    """

    def __init__(self, config: VariantConfig | Config | None = None, *, settled: bool = False) -> None:
        if config is None:
            config = VariantConfig()
        elif not isinstance(config, VariantConfig):
            config = VariantConfig(engines=config.engines, width=config.width,
                                   program_words=config.program_words, fifo_words=config.fifo_words,
                                   fused=config.fused, prefetch=config.prefetch)
        self.options = config.options
        # Reset synchronizer, True = reset requested (q1 first stage, q2 second).
        initial = False if settled else None
        self._q: tuple[bool | None, bool | None] = (initial, initial)
        self.reset_applied = False
        super().__init__(config)

    # ------------------------------------------------------------ reset
    @property
    def reset_latency(self) -> int:
        return self.options.reset_latency

    def clear_active(self, raw: bool) -> bool | None:
        """The chip-wide clear for raw request ``raw`` and the current synchronizer
        state (None = unknown). Registers clear on an edge while it is true;
        public outputs are masked while it is true."""
        style = self.options.reset
        if style in ("sync", "async"):
            return raw
        q1, q2 = self._q
        if style == "sync_registered":
            return q2
        return True if raw else q2  # async_sync_release

    def _clock_synchronizer(self, raw: bool) -> None:
        style = self.options.reset
        q1, _ = self._q
        if style == "sync_registered":
            self._q = (raw, q1)
        elif style == "async_sync_release":
            self._q = (True, True) if raw else (False, q1)

    @property
    def settling(self) -> bool:
        """A reset requested earlier has not been applied and released yet."""
        if self.options.reset in ("sync", "async"):
            return False
        return self._q != (False, False)

    def tick(self, ui: int = 0, pins: int = 0, *, reset: bool = False,
             enabled: bool = True) -> Outputs:
        raw = reset or not enabled
        clear = self.clear_active(raw)
        self.reset_applied = clear is True
        if clear is True:
            self.reset()
        else:
            super().tick(ui, pins)
        self._clock_synchronizer(raw)
        return self.outputs(ui, reset=reset, enabled=enabled)

    def outputs(self, ui: int = 0, *, reset: bool = False, enabled: bool = True) -> Outputs:
        """While the clear is active the pins and the host ready/valid bits are
        masked. The IRQ and fault bits are not masked: they show the state, which
        is already reset except with ``sync_registered``, where the clear becomes
        visible one cycle before its first clearing edge. (In the design of record
        and the asynchronous styles the state is reset whenever the clear is
        visible, so they read 0 there, as ``Reference.outputs`` returns.)"""
        if self.clear_active(reset or not enabled):
            if self.options.async_assert:
                return Outputs(0, 0, 0)  # state already reset asynchronously
            irq = any(e.event or e.rx for e in self.engines)
            fault = self.host_fault or any(e.fault for e in self.engines)
            return Outputs(int(irq) << 6 | int(fault) << 7, 0, 0)
        return super().outputs(ui)

    # ------------------------------------------------------------ ISA knobs
    def _status(self) -> int:
        if self.read_select == 7:
            return self.options.isa_version
        if self.read_select == 5 and not self.options.debug_counters:
            return 0
        return super()._status()

    def _step(self, e: Engine, tx_available: bool, rx_space: bool) -> tuple[bool, bool, int]:
        if (self.options.shift == "byte_lane" and e.running and not e.wait and e.transfer is None
                and e.committed and 0 <= e.pc < len(e.program)):
            word = e.program[e.pc]
            if word >> 24 in (24, 25) and (word & 255) % 8:
                e.stalled = False
                self._fail(e, Fault.INVALID_OPERAND)
                return False, False, 0
        result = super()._step(e, tx_available, rx_space)
        if self.options.pc_bits == "saturating_7" and e.pc > 127:
            e.pc = 127
        return result


def make_reference(config: Config | None = None, *, settled: bool = False) -> Reference:
    """The plain verbatim ``Reference`` for base configurations, else a VariantReference."""
    if isinstance(config, VariantConfig) and not (config.options.is_base and config.fifo_words in (8, 32)):
        return VariantReference(config, settled=settled)
    if isinstance(config, VariantConfig):
        return Reference(config.base)
    return Reference(config)
