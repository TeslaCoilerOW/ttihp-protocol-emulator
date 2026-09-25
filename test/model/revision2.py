# Copied verbatim (apart from this header) from the asic-lab monorepo:
#   projects/protocol-emulator/python/protocol_emulator/revision2.py @ commit 18676a4
# Independent pure-Python ISA2 reference (stdlib only). Keep in sync with
# reference/isa.md; any semantic change must update RTL, assembler and model.
# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Independent ISA2 public-interface scenarios, including rejected receive words."""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise

from .host import Host
from .reference import Config, Reference, immediate, instruction


def strict_push_host(config: Config | None = None) -> Host:
    """A full pre-edge RX queue traps without consuming a descriptor or word."""
    machine = Reference(config)
    host = Host(machine)
    host.cycle(reset=True)
    depth = machine.config.fifo_words
    # Produce distinct accepted words and leave the first rejected word in RX.
    host.load(0, [immediate(19, 0x100), instruction(19, a=2, c=1),
                  instruction(18, a=1), instruction(7, a=1), instruction(20, b=2),
                  immediate(5, 2)])
    host.command(4, 1)
    host.idle(6 * depth + 20)
    engine = machine.engines[0]
    assert engine.fault == 4 and not engine.running and not engine.stalled
    assert list(engine.rx) == list(range(0x100, 0x100 + depth))
    assert engine.regs[1] == 0x100 + depth and engine.pc == 3
    completed = engine.completed
    assert host.status(6) == 0x100 + depth
    assert host.status(7) == 2
    assert [host.read(3) for _ in range(depth)] == list(range(0x100, 0x100 + depth))
    host.idle(12)
    assert engine.fault == 4 and engine.completed == completed
    assert host.status(6) == 0x100 + depth
    assert host.status() & 0xF0F == 0x408 | 2
    return host


def trigger_host(config: Config | None = None) -> Host:
    """Input events survive unrelated instruction execution and coalesce."""
    machine = Reference(config)
    host = Host(machine)
    host.cycle(reset=True)
    host.load(0, [immediate(4, 64), instruction(15), immediate(14, 2), instruction(1)])
    host.command(11, 32 | 7)  # Rising edge on observed pin7, no ownership required.
    host.load(1, [instruction(15), instruction(19, a=1, c=0xA6), instruction(7, a=1), instruction(1)])
    host.command(4, 3)
    host.idle(3)
    host.pins = 128
    host.idle(2)
    assert not machine.engines[0].event
    host.cycle()
    assert machine.engines[0].event and machine.engines[0].wait > 0
    assert host.cycles[-1].expected.uo & 64
    host.idle(90)
    assert not machine.engines[0].event and not machine.engines[1].event
    assert not any(engine.running for engine in machine.engines)
    host.command(0, 1)
    assert host.read(3) == 0xA6
    # A static high level does not create a rising event when rearmed.
    host.command(0, 0)
    host.command(11, 32 | 7)
    host.idle(5)
    assert not machine.engines[0].event
    # Configure the falling edge while halted; it still delivers a mailbox/IRQ.
    host.command(11, 32 | 8 | 7)
    host.pins = 0
    host.idle(2)
    assert not machine.engines[0].event
    host.cycle()
    assert machine.engines[0].event and not machine.engines[0].running
    assert host.status(4) == 1
    host.cycle(enabled=False)
    host.idle(4)
    assert not any(engine.trigger or engine.event for engine in machine.engines)
    assert host.status(7) == 2
    return host


def uart_overflow_host() -> Host:
    """Overloaded UART bridge reports potential loss and preserves accepted data.

    The receiver firmware must contain strict PUSH. Two other engines keep
    independent pin periods while the route's destination is deliberately full.
    """
    from .verification import _load_firmware

    machine = Reference()
    host = Host(machine, pins=2)
    host.cycle(reset=True)
    _load_firmware(host, "uart-rx")
    assert instruction(7, a=1) in machine.engines[1].program
    host.load(2, [instruction(13, a=7, b=1), instruction(1)])
    for value in range(machine.config.fifo_words):
        host.write(2, 0x6000 + value)
    for index, pin, low, high in ((0, 0, 2, 3), (3, 6, 5, 6)):
        host.load(index, [immediate(3, 1 << pin), immediate(2, 1 << pin),
                          immediate(4, low), immediate(2, 0), immediate(4, high),
                          immediate(5, 1)], ownership=1 << pin)
    count = machine.config.fifo_words + 3
    host.command(6, 1 | 2 << 2 | 16 | count << 5)
    host.command(4, 15)
    start = len(host.cycles) + 32
    frames = list(range(0x30, 0x30 + machine.config.fifo_words + 2))

    def wire(cycle: int) -> int:
        offset = cycle - start
        if offset < 0:
            return 2
        frame, phase = divmod(offset, 704)
        if frame >= len(frames):
            return 2
        bit = phase // 64
        return (0 if bit == 0 else (frames[frame] >> (bit - 1) & 1 if bit <= 8 else 1)) << 1

    host.pins = wire
    first_fault: int | None = None
    while len(host.cycles) < start + len(frames) * 704:
        host.cycle()
        if machine.engines[1].fault and first_fault is None:
            first_fault = len(host.cycles) - 1
    depth = machine.config.fifo_words
    receiver = machine.engines[1]
    assert first_fault is not None and start + depth * 704 <= first_fault < start + (depth + 1) * 704
    assert receiver.fault == 4 and receiver.regs[1] == frames[depth]
    assert list(receiver.rx) == frames[:depth]
    assert machine.routes[1] == (2, count)
    assert list(machine.engines[2].tx) == list(range(0x6000, 0x6000 + depth))
    for pin, period in ((0, 10), (6, 16)):
        rising = [i for i in range(start + 1, len(host.cycles))
                  if host.cycles[i].expected.uio_out >> pin & 1
                  and not host.cycles[i - 1].expected.uio_out >> pin & 1]
        assert len(rising) > 20 and all(b - a == period for a, b in pairwise(rising))
    host.pins = 2
    host.command(0, 1)
    assert host.status(6) == frames[depth]
    assert host.status() & 0xF0F == 0x40A
    assert [host.read(3) for _ in range(depth)] == frames[:depth]
    host.idle(32)
    assert receiver.fault == 4 and host.status(6) == frames[depth]
    assert machine.routes[1] == (2, count)
    return host


SCENARIOS: dict[str, Callable[[Config | None], Host]] = {
    "strict_push": strict_push_host,
    "input_triggers": trigger_host,
}
