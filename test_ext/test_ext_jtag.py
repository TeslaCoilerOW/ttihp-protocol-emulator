# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""JTAG firmware against a third-party JTAG TAP (pins TCK0/TDI1/TDO2/TMS3).

Peer: Wren6991/Hazard3 hazard3_jtag_dtm (RISC-V JTAG-DTM: the IEEE 1149.1 TAP
state machine, 5-bit IR, IDCODE 0x01, DTMCS 0x10, DMI 0x11, every other IR
value BYPASS), instantiated in tb_ext.v with IDCODE 0x1DECA0E1. It is not a
fully 1149.1-conformant TAP: in Capture-IR it loads the current IR value
(hazard3_jtag_dtm.v line 110), where 1149.1 requires a pattern ending in 01.
The IR-capture checks below are therefore specific to this peer.
"""

from __future__ import annotations

import cocotb

import ext_harness  # noqa: F401  (first: puts ../test and vendor/cocotbext-* on sys.path)
from ext_harness import EDGE_CHECK, SKEWS, START, STOP, assert_released, data, drain, load, rng, start

IDCODE = 0x1DECA0E1
IR_IDCODE, IR_DTMCS, IR_BYPASS = 0x01, 0x10, 0x1F
DTMCS = (4 << 12) | (7 << 4) | 1  # idle hint 4, dmistat 0, abits 7, version 1 (hazard3_jtag_dtm_core.v)
TAP_SHIFT_DR = 4


def tck_edges(h, board, skew: str) -> dict[str, int]:
    edges = board.coincidences("co_tck")
    h.log("skew %s: DUT pin moves on the same clk edge as a TCK rise/fall: %s", skew, edges)
    return edges


def assert_tck_edges(edges: dict[str, int]) -> None:
    """IEEE 1149.1: the TAP samples TMS/TDI on the rising TCK edge, so the
    controller must not move them on that edge (this project's check, in
    addition to the peer)."""
    if EDGE_CHECK:
        assert edges["tck_tdi_rise"] == edges["tck_tms_rise"] == 0, f"TDI/TMS moved on a rising TCK edge: {edges}"


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_jtag_firmware_idcode_scan(dut, skew):
    """Shipped jtag firmware: TAP reset, Shift-DR, eight LSB-first byte scans.

    After Test-Logic-Reset the TAP selects IDCODE, so the first four bytes
    returned are the IDCODE (LSB first) and the next four are the first four
    TDI bytes, delayed by the 32-bit IDCODE register.
    """
    h, board = await start(dut, skew=skew, sel_jtag=1)
    tdi = data(rng("jtag"), [0x69, 0xB4, 0x02, 0xD2, 0x3C, 0xA5, 0x0F, 0xF0])
    await load(h, "jtag")
    for word in tdi:
        await h.write(2, word)
    await h.command(START, 1)
    await h.run_until(lambda: len(h.engine(0).rx) == 8, 20000, "JTAG scan bytes")
    got = await drain(h, 0, 8)
    expected = list(IDCODE.to_bytes(4, "little")) + tdi[:4]
    edges = tck_edges(h, board, skew)
    assert got == expected, f"TDO bytes {[hex(b) for b in got]} != {[hex(b) for b in expected]}"
    assert board.get("jtag_tap_state") == TAP_SHIFT_DR and board.get("jtag_ir") == IR_IDCODE
    assert_tck_edges(edges)
    h.assert_no_faults()
    await h.command(STOP, 1)
    await h.idle(2)
    assert_released(h, 0x0B)


class Vectors:
    """Host-side builder for the jtag-vector program: a list of (TMS, TDI) bits."""

    def __init__(self) -> None:
        self.bits: list[tuple[int, int]] = []
        self.scans: list[tuple[str, slice, int, int]] = []  # (label, TDO slice, length, TDI value)

    def reset(self) -> None:
        """Five TMS=1 clocks reach Test-Logic-Reset from any state; one more, then Run-Test/Idle."""
        self.bits += [(1, 0)] * 6 + [(0, 0)]

    def _scan(self, label: str, value: int, length: int) -> None:
        start = len(self.bits)
        self.bits += [(int(i == length - 1), value >> i & 1) for i in range(length)]  # last bit -> Exit1
        self.bits += [(1, 0), (0, 0)]  # Update, Run-Test/Idle
        self.scans.append((label, slice(start, start + length), length, value))

    def ir(self, label: str, value: int, length: int = 5) -> None:
        self.bits += [(1, 0), (1, 0), (0, 0), (0, 0)]  # Select-DR, Select-IR, Capture-IR, Shift-IR
        self._scan(label, value, length)

    def dr(self, label: str, value: int, length: int) -> None:
        self.bits += [(1, 0), (0, 0), (0, 0)]  # Select-DR, Capture-DR, Shift-DR
        self._scan(label, value, length)

    def words(self) -> list[int]:
        bits = self.bits + [(0, 0)] * (-len(self.bits) % 16)  # pad in Run-Test/Idle
        out = []
        for base in range(0, len(bits), 16):
            word = 0
            for k, (tms, tdi) in enumerate(bits[base:base + 16]):
                word |= tms << 2 * k | tdi << 2 * k + 1
            out.append(word)
        return out


def value_of(tdo: list[int]) -> int:
    return sum(bit << i for i, bit in enumerate(tdo))


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_jtag_vector_ir_dr_scans(dut, skew):
    """Test-only jtag-vector program: TAP walks, IR scans and DR scans against Hazard3.

    Checks IDCODE after reset, the IR capture value (Hazard3-specific: the
    current IR, not the 1149.1 ...01 pattern), DTMCS through an IR scan,
    the one-bit BYPASS register, Test-Logic-Reset restoring IDCODE, and the
    32-bit IDCODE register length (TDI reappears on TDO after 32 bits).
    """
    h, board = await start(dut, skew=skew, sel_jtag=1)
    v = Vectors()
    v.reset()
    v.dr("IDCODE after reset", 0, 32)
    v.ir("IR capture = current IR (IDCODE)", IR_DTMCS)
    v.dr("DTMCS", 0, 32)
    v.ir("IR capture = current IR (DTMCS)", IR_BYPASS)
    generator = rng("jtag_vector")
    pattern = 0xB38F if generator is None else generator.getrandbits(16)
    v.dr("BYPASS", pattern, 16)
    v.reset()
    through = 0x5A3C_96E1 if generator is None else generator.getrandbits(32)
    v.dr("IDCODE then TDI", through, 64)
    words = v.words()
    await load(h, "jtag-vector")
    await h.command(START, 1)
    tdo_words: list[int] = []
    for base in range(0, len(words), 8):  # chunks keep both FIFOs (8 words) from blocking
        chunk = words[base:base + 8]
        for word in chunk:
            await h.write(2, word)
        tdo_words += await drain(h, 0, len(chunk))
    tdo = [word >> k & 1 for word in tdo_words for k in range(16)]
    expected = {
        "IDCODE after reset": IDCODE,
        "IR capture = current IR (IDCODE)": IR_IDCODE,
        "DTMCS": DTMCS,
        "IR capture = current IR (DTMCS)": IR_DTMCS,
        "BYPASS": (pattern << 1) & 0xFFFF,
        "IDCODE then TDI": IDCODE | through << 32,
    }
    for label, bits, length, _ in v.scans:
        got = value_of(tdo[bits])
        h.log("%-30s %2d bits TDO=%#x", label, length, got)
        assert got == expected[label], f"{label}: TDO {got:#x} != {expected[label]:#x}"
    assert board.get("jtag_tap_state") == 1 and board.get("jtag_ir") == IR_IDCODE  # Run-Test/Idle
    assert_tck_edges(tck_edges(h, board, skew))
    h.assert_no_faults()
    await h.command(STOP, 1)
    await h.idle(2)
    assert_released(h, 0x0B)
