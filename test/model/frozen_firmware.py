# Copied verbatim (apart from this header) from the asic-lab monorepo:
#   projects/protocol-emulator/python/protocol_emulator/frozen_firmware.py @ commit 18676a4
# Independent pure-Python ISA2 reference (stdlib only). Keep in sync with
# reference/isa.md; any semantic change must update RTL, assembler and model.
# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Firmware-only JTAG and custom waveform demonstrations on the existing ISA2."""

from __future__ import annotations

from itertools import pairwise

from .host import Host
from .jtag import TapPeer, lsb_bytes
from .reference import Reference


def frozen_firmware_host(scenario: str) -> Host:
    from .verification import _load_firmware

    machine = Reference()
    host = Host(machine)
    if scenario == "firmware_jtag":
        response = (0xD2, 0x3C, 0xA5)
        transmitted = (0x69, 0xB4, 0x02)
        peer = TapPeer(response)

        def external(_: int) -> int:
            out = machine.outputs()
            return peer.resolve(out.uio_out, out.uio_oe)

        host.pins = external
        host.cycle(reset=True)
        _load_firmware(host, "jtag")
        for word in transmitted:
            host.write(2, word)
        host.command(4, 1)
        host.idle(3000)
        assert lsb_bytes(peer.received) == list(transmitted)
        assert lsb_bytes(peer.returned) == list(response)
        assert [x[1] for x in peer.transitions[:10]] == [1] * 6 + [0, 1, 0, 0]
        assert peer.transitions[5][2] == "reset" and peer.state == "shift_dr"
        assert [host.read(3) for _ in response] == list(response)
        assert not (host.status() >> 8 & 255)
        host.command(5, 1)
        assert machine.outputs().uio_oe == 0
    elif scenario == "firmware_waveform":
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
        assert machine.outputs().uio_oe == 0
        # TIME follows the final low interval and LOOP. Timestamp is sampled
        # before the clock counter advances; cycle0 was the reset edge.
        assert host.read(3) == falling[-1] + 63
        assert not (host.status() >> 8 & 255)
    else:
        raise ValueError("unknown firmware-only demonstration")
    return host
