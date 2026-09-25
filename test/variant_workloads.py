# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Record the monorepo differential workloads (model/verification.py) on a variant.

The workloads construct their own ``Reference`` and ``Host`` and carry their
own model-level assertions. For a variant (PE_VARIANT, variants.py) they are
recorded against ``VariantReference`` instead, by rebinding those two names in
the three workload modules for the duration of the recording:

* ``Reference`` -> ``VariantReference`` of the design under test (queue depth
  and ISA knobs; workloads that size themselves from ``config.fifo_words``
  follow the variant's depth);
* ``Host`` -> ``SettlingHost``, which idles while a reset request is still in
  flight (reset-synchronizer variants) before its next action, as a host that
  knows the reset latency would, and which checks READ_SELECT 7 against the
  variant's ISA version before handing the ISA-2 workload the value 2 it
  checks for (a version-3 device runs every ISA-2 image unchanged).

For the design of record nothing is rebound: ``test_legacy`` calls
``differential_host`` directly, exactly as before variants existed.

A few workloads state base-contract facts that a variant deliberately changes.
``ADAPTED`` re-implements those (the monorepo originals stay verbatim in
``model/``); each docstring names the one change. ``SUBSTITUTE`` names workloads
that are replaced by a live cocotb scenario (``scenarios.py``) instead.
"""

from __future__ import annotations

import contextlib
import random
from collections.abc import Callable, Iterator
from itertools import pairwise

from model import frozen_firmware, revision2, verification
from model.host import Host
from model.jtag import TapPeer, lsb_bytes
from model.reference import Outputs
from model.reference import immediate as imm
from model.reference import instruction as ins
from model.scoreboards import I2CPeer, WireSample, i2c_decode
from model.variant import VariantConfig, VariantReference
from model.verification import _load_firmware, differential_host


class SettlingHost(Host):
    """``Host`` that waits out the reset latency before its next non-reset cycle."""

    isa_version: int | None = None  # set while recording a variant

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.last_reset = -1  # index in cycles of the last edge that applied reset

    def cycle(self, ui: int | None = None, *, reset: bool = False, enabled: bool = True) -> Outputs:
        if not reset and enabled:
            while getattr(self.machine, "settling", False):
                self._cycle(None, reset=False, enabled=True)
        return self._cycle(ui, reset=reset, enabled=enabled)

    def _cycle(self, ui: int | None, *, reset: bool, enabled: bool) -> Outputs:
        sampled = super().cycle(ui, reset=reset, enabled=enabled)
        if getattr(self.machine, "reset_applied", reset or not enabled):
            self.last_reset = len(self.cycles) - 1
        return sampled

    def status(self, selection: int = 0) -> int:
        value = super().status(selection)
        if selection == 7 and self.isa_version is not None:
            assert value == self.isa_version, f"READ_SELECT 7 read {value}, variant ISA {self.isa_version}"
            return 2  # ISA-2 workloads check for 2; every ISA-2 image runs on ISA 3
        return value


_MODULES = (verification, revision2, frozen_firmware)


@contextlib.contextmanager
def variant_workloads(config: VariantConfig,
                      image_loader: Callable[[str], dict] | None = None) -> Iterator[None]:
    """Rebind Reference/Host (and, with ``image_loader``, the firmware image loader)."""
    def reference(_config: object = None) -> VariantReference:
        # Settled synchronizer: the harness replays after its own reset/settle.
        return VariantReference(config, settled=True)

    saved = [(module, module.Reference, module.Host) for module in _MODULES]
    saved_loader = verification._firmware
    SettlingHost.isa_version = config.options.isa_version
    try:
        for module in _MODULES:
            module.Reference = reference
            module.Host = SettlingHost
        if image_loader is not None:
            verification._firmware = image_loader
        yield
    finally:
        SettlingHost.isa_version = None
        verification._firmware = saved_loader
        for module, saved_reference, saved_host in saved:
            module.Reference = saved_reference
            module.Host = saved_host


def _host(config: VariantConfig) -> tuple[VariantReference, SettlingHost]:
    model = VariantReference(config, settled=True)
    return model, SettlingHost(model)


# ------------------------------------------------------------ adapted workloads
def dma_host(config: VariantConfig) -> Host:
    """verification.py "dma": at most ``fifo_words`` of the three words are written
    before START, the rest after START under backpressure (2-word queues)."""
    model, host = _host(config)
    host.cycle(reset=True)
    host.idle(2)
    depth = config.fifo_words
    chain_length = min(3, config.engines)
    for engine in range(chain_length):
        host.load(engine, [ins(6), ins(18, 1, 0), ins(7), imm(5, 0)])
    values = (0x01020304, 0xAABBCCDD, 0x76543210)
    host.command(0, 0)
    for value in values[:depth]:
        host.write(2, value)
    for engine in range(chain_length-1):
        host.command(6, engine | (engine+1) << 2 | 16 | 3 << 5)
    host.command(4, (1 << chain_length)-1)
    for value in values[depth:]:
        host.write(2, value)
    host.idle(60)
    host.command(0, chain_length-1)
    for value in values:
        assert host.read(3) == value & model.mask
    host.command(5, (1 << chain_length)-1)
    return host


def congestion_host(config: VariantConfig) -> Host:
    """verification.py "congestion": after the prefill, min(4, fifo_words + 1) more
    words (the pipeline holds fifo_words in RX plus one in the engine)."""
    model, host = _host(config)
    host.cycle(reset=True)
    host.idle(2)
    depth = config.fifo_words
    extra = min(4, depth + 1)
    host.load(0, [ins(6), ins(18, 1, 0), ins(7), imm(5, 0)])
    for value in range(depth):
        host.write(2, value)
    host.command(4, 1)
    host.idle(depth*5+10)
    for value in range(depth, depth+extra):
        host.write(2, value)
    host.idle(20)
    for value in range(depth+extra):
        assert host.read(3, pauses=[2, 0, 1, 1, 0, 3, 0, 1]) == value
    host.command(5, 1)
    return host


def random_alu_host(config: VariantConfig) -> Host:
    """verification.py "random_alu": on byte-lane designs every SHL/SHR count is
    rounded down to a byte lane (c - c % 8); same random stream otherwise."""
    model, host = _host(config)
    host.cycle(reset=True)
    host.idle(2)
    byte_lane = config.options.shift == "byte_lane"
    rng = random.Random(0x1A5A)
    words = [ins(19, reg, rng.randrange(256), rng.randrange(256)) for reg in range(4)]
    for _ in range(min(25, config.program_words-11)):
        op = rng.choice([18, 20, 21, 22, 23, 24, 25, 27, 28])
        reg = rng.randrange(4)
        if op in (24, 25):
            count = rng.randrange(config.width)
            words.append(ins(op, reg, 0, count - count % 8 if byte_lane else count))
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
    return host


def i2c_firmware_host(config: VariantConfig, scenario: str) -> Host:
    """verification.py firmware_host I2C cases: at most ``fifo_words`` queued words
    before START, the rest after START under backpressure."""
    cases = {"firmware_i2c_write": ("i2c-write", (0x84, 0x5A), None),
             "firmware_i2c_read": ("i2c-read", (0x85,), 0x69),
             "firmware_i2c_restart": ("i2c-repeated-start", (0x84, 0x12, 0x85), 0x96),
             "firmware_i2c_nack": ("i2c-write", (0x84, 0x5A), None)}
    name, queued, received = cases[scenario]
    model, host = _host(config)
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
    depth = config.fifo_words
    for word in queued[:depth]:
        host.write(2, word)
    host.command(4, 1 << image["engine"])
    for word in queued[depth:]:
        host.write(2, word)
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


def i2c_target_write_host(config: VariantConfig) -> Host:
    """verification.py i2c_target_host(read=False): RX is pre-filled to
    ``fifo_words`` (not 8) words, and fifo_words - 1 (not 7) of them are read back
    before the received byte."""
    model, host = _host(config)
    depth = config.fifo_words
    samples: list[WireSample] = []
    start: int | None = None
    position = elapsed = stretch_observations = 0
    segments = [(1, 1, 32), (1, 0, 32)]

    def send_byte(value: int) -> None:
        for bit in (value >> i & 1 for i in range(7, -1, -1)):
            segments.extend([(0, bit, 32), (1, bit, 32)])
        segments.extend([(0, 1, 32), (1, 1, 32)])
    send_byte(0x84)
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
    host.load(3, [ins(19, 1, 0, 99), imm(10, depth - 1), ins(7), imm(11, 2), ins(1)])
    host.command(4, 8)
    host.idle(40)
    assert len(model.engines[3].rx) == depth
    _load_firmware(host, "i2c-target-write")
    host.command(4, 8)
    start = len(host.cycles) + 40
    for _ in range(3000):
        host.cycle()
        if stretch_observations >= 80:
            break
    else:
        raise AssertionError("congested I2C target did not stretch the clock")
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
    assert decoded[0].bytes == (0x84, 0xA6)
    assert decoded[0].acknowledged == (True, True)
    assert model.engines[3].fault == 0
    for _ in range(depth - 1):
        assert host.read(3) == 99
    assert host.read(3) == 0xA6
    return host


def jtag_host(config: VariantConfig) -> Host:
    """frozen_firmware.py "firmware_jtag": at most ``fifo_words`` TX words before
    START; with fewer RX words than responses the host reads each response as soon
    as it is queued instead of after the 3000-cycle run."""
    model, host = _host(config)
    response = (0xD2, 0x3C, 0xA5)
    transmitted = (0x69, 0xB4, 0x02)
    peer = TapPeer(response)

    def external(_: int) -> int:
        out = model.outputs()
        return peer.resolve(out.uio_out, out.uio_oe)
    host.pins = external
    host.cycle(reset=True)
    _load_firmware(host, "jtag")
    depth = config.fifo_words
    for word in transmitted[:depth]:
        host.write(2, word)
    host.command(4, 1)
    for word in transmitted[depth:]:
        host.write(2, word)
    early = [host.read(3) for _ in response] if depth < len(response) else []
    host.idle(3000)
    assert lsb_bytes(peer.received) == list(transmitted)
    assert lsb_bytes(peer.returned) == list(response)
    assert [x[1] for x in peer.transitions[:10]] == [1] * 6 + [0, 1, 0, 0]
    assert peer.transitions[5][2] == "reset" and peer.state == "shift_dr"
    assert (early or [host.read(3) for _ in response]) == list(response)
    assert not (host.status() >> 8 & 255)
    host.command(5, 1)
    assert model.outputs().uio_oe == 0
    return host


def waveform_host(config: VariantConfig) -> Host:
    """frozen_firmware.py "firmware_waveform": the TIME value is compared with
    the cycle index minus the index of the last reset edge (0 on the design of
    record, later when reset is applied through a synchronizer)."""
    model, host = _host(config)
    host.cycle(reset=True)
    _load_firmware(host, "waveform")
    host.command(4, 1)
    host.idle(1800)
    rising: list[int] = []
    falling: list[int] = []
    for index, (previous, current) in enumerate(pairwise(host.cycles), 1):
        old = previous.expected.uio_out & previous.expected.uio_oe & 1
        new = current.expected.uio_out & current.expected.uio_oe & 1
        if old != new:
            (rising if new else falling).append(index)
    assert len(rising) == len(falling) == 16
    assert all(low - high == 32 for high, low in zip(rising, falling, strict=True))
    assert all(high - low == 64 for low, high in zip(falling[:-1], rising[1:], strict=True))
    assert model.outputs().uio_oe == 0
    assert host.last_reset >= 0
    assert host.read(3) == falling[-1] + 63 - host.last_reset
    assert not (host.status() >> 8 & 255)
    return host


Predicate = Callable[[VariantConfig], bool]
ADAPTED: dict[str, tuple[Predicate, Callable[[VariantConfig], Host]]] = {
    "dma": (lambda c: c.fifo_words < 3, dma_host),
    "congestion": (lambda c: c.fifo_words < 3, congestion_host),
    "random_alu": (lambda c: c.options.shift == "byte_lane", random_alu_host),
    "firmware_i2c_restart": (lambda c: c.fifo_words < 3,
                             lambda c: i2c_firmware_host(c, "firmware_i2c_restart")),
    "firmware_i2c_target_write": (lambda c: c.fifo_words < 8, i2c_target_write_host),
    "firmware_jtag": (lambda c: c.fifo_words < 3, jtag_host),
    "firmware_waveform": (lambda c: c.options.reset_latency > 0, waveform_host),
}

# Workloads replaced by a live scenario: name -> (predicate, scenarios.py function, kwargs).
SUBSTITUTE: dict[str, tuple[Predicate, str, dict[str, str]]] = {
    "firmware_flagship": (lambda c: c.fifo_words < 8, "flagship_topup",
                          {"spi_image": "spi-controller-mode0"}),
    "firmware_flagship_fast": (lambda c: c.fifo_words < 8, "flagship_topup",
                               {"spi_image": "spi-controller-fast"}),
}


def substitute(scenario: str, config: VariantConfig) -> tuple[str, dict[str, str]] | None:
    entry = SUBSTITUTE.get(scenario)
    if entry is None or not entry[0](config):
        return None
    return entry[1], entry[2]


def record(scenario: str, config: VariantConfig,
           image_loader: Callable[[str], dict] | None = None) -> Host:
    """The recorded workload for this variant (adapted where ADAPTED applies)."""
    with variant_workloads(config, image_loader):
        adapted = ADAPTED.get(scenario)
        if adapted is not None and adapted[0](config):
            return adapted[1](config)
        return differential_host(scenario)
