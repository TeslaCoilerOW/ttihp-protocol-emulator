# Copied verbatim (apart from this header) from the asic-lab monorepo:
#   projects/protocol-emulator/python/protocol_emulator/jtag.py @ commit 18676a4
# Independent pure-Python ISA2 reference (stdlib only). Keep in sync with
# reference/isa.md; any semantic change must update RTL, assembler and model.
# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Independent binary TAP peer for the documented continuous Shift-DR example.

State transitions follow AMD UG570's IEEE 1149.1 TAP diagram. This peer does
not implement a device instruction set or establish board-level JTAG timing.
"""

from __future__ import annotations

TAP_NEXT: dict[str, tuple[str, str]] = {
    "reset": ("idle", "reset"),
    "idle": ("idle", "select_dr"),
    "select_dr": ("capture_dr", "select_ir"),
    "capture_dr": ("shift_dr", "exit1_dr"),
    "shift_dr": ("shift_dr", "exit1_dr"),
    "exit1_dr": ("pause_dr", "update_dr"),
    "pause_dr": ("pause_dr", "exit2_dr"),
    "exit2_dr": ("shift_dr", "update_dr"),
    "update_dr": ("idle", "select_dr"),
    "select_ir": ("capture_ir", "reset"),
    "capture_ir": ("shift_ir", "exit1_ir"),
    "shift_ir": ("shift_ir", "exit1_ir"),
    "exit1_ir": ("pause_ir", "update_ir"),
    "pause_ir": ("pause_ir", "exit2_ir"),
    "exit2_ir": ("shift_ir", "update_ir"),
    "update_ir": ("idle", "select_dr"),
}


class TapPeer:
    """TCK0/TDI1/TDO2/TMS3; capture on rising, change TDO on falling edges."""

    def __init__(self, response: tuple[int, ...], *, initial: str = "shift_ir") -> None:
        if initial not in TAP_NEXT or any(not 0 <= x < 256 for x in response):
            raise ValueError("invalid TAP state or response byte")
        self.state = initial
        self.response = tuple((x >> bit) & 1 for x in response for bit in range(8))
        self.clock = 0
        self.tdo = 0
        self.position = 0
        self.received: list[int] = []
        self.returned: list[int] = []
        self.transitions: list[tuple[str, int, str]] = []

    def resolve(self, values: int, enables: int) -> int:
        if enables & ~0x0B:
            raise ValueError("JTAG example must not drive TDO or unrelated pins")
        driven = values & enables
        clock, tms = driven & 1, driven >> 3 & 1
        if clock and not self.clock:
            before = self.state
            if before == "capture_dr":
                self.position = 0
            if before == "shift_dr":
                self.received.append(driven >> 1 & 1)
                self.returned.append(self.tdo)
                self.position += 1
            self.state = TAP_NEXT[before][tms]
            self.transitions.append((before, tms, self.state))
        if self.clock and not clock:
            self.tdo = (self.response[self.position]
                        if self.state == "shift_dr" and self.position < len(self.response) else 0)
        self.clock = clock
        return self.tdo << 2


def lsb_bytes(bits: list[int]) -> list[int]:
    if len(bits) % 8:
        raise ValueError("partial JTAG byte")
    return [sum(bit << offset for offset, bit in enumerate(bits[start:start + 8]))
            for start in range(0, len(bits), 8)]
