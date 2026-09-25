# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Infrastructure shared by the independent-peer tests (test_ext_*.py).

* ``PadHarness`` is ../test/harness.py's lockstep host driver with one change:
  the DUT's protocol inputs are no longer supplied by Python but are the
  resolved pad levels of tb_ext.v (DUT + third-party peers + pull-ups). After
  every rising edge the harness reads ``uio_in_q`` (the pads sampled at the
  preceding falling edge, i.e. exactly what the DUT just sampled) and feeds it
  to the reference model. An unresolved pad (X/Z from bus contention or an X
  from the DUT) fails the test.
* ``Board`` wraps the peer-control registers and logs of tb_ext.v.
* Firmware: shipped images (../firmware) are loaded with the harness's own
  loader; the test-only programs in test_ext/firmware are loaded with the
  same SHA-256 identity checks.
* Skew corners (tb_ext.v "Skew corners"): tests in which the DUT drives a
  clock and data pins that a peer samples on that clock run once with the
  data pins arriving SKEW_NS after the clock pins ("data") and once with the
  clock pins arriving later ("clock"), so a data change on the peer's sampling
  edge fails in at least one corner instead of winning a simulator race.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
for _path in (HERE.parent / "test", *(HERE / "vendor" / name for name in
                                     ("cocotbext-uart", "cocotbext-i2c", "cocotbext-spi"))):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import harness  # noqa: E402  (../test/harness.py, used unmodified)
from harness import (CLEAR, RS_LEVELS, RS_STATUS, RS_VERSION, SELECT, START, STOP,  # noqa: E402,F401
                     CocotbHarness)

EXT_FIRMWARE = HERE / "firmware"
# PE_EXT_SEED unset: every test uses its fixed, documented stimulus. Set: tests
# draw their payload bytes, addresses, delays and baud error from a generator
# seeded by (seed, test name), so a failure reproduces with the same seed and
# COCOTB_TEST_FILTER.
SEED = os.environ.get("PE_EXT_SEED") or None


def rng(test: str) -> random.Random | None:
    """Per-test generator, or None for the fixed stimulus."""
    return None if SEED is None else random.Random(f"{int(SEED, 0)}:{test}")


def data(generator: random.Random | None, default: list[int], length: int | None = None) -> list[int]:
    """``default`` bytes, or random bytes of the same (or given) length."""
    if generator is None:
        return list(default)
    return [generator.randrange(256) for _ in range(len(default) if length is None else length)]


# PE_EXT_SKEW unset: skew-sensitive tests run in both corners (see the module
# docstring). PE_EXT_SKEW=data|clock|none (or a comma list) selects corners.
# Tests without a skew parameter (the DUT drives at most one pin of the
# protocol, or stays idle) use the first corner.
SKEW_CODES = {"none": 0, "data": 1, "clock": 2}
SKEWS = [name.strip() for name in (os.environ.get("PE_EXT_SKEW") or "data,clock").split(",") if name.strip()]
assert SKEWS and all(name in SKEW_CODES for name in SKEWS), f"PE_EXT_SKEW={SKEWS}: use data, clock or none"

# tb_ext.v edge-coincidence counters: a DUT data pin moved on the same clk
# edge as a DUT clock pin rose/fell. The tests always log them and assert the
# protocol's rule (e.g. SPI: MOSI never moves on the sampling edge) unless
# PE_EXT_NO_EDGE_CHECK is set, which leaves only the third-party peers to
# judge (used to measure what the peers alone detect).
EDGE_CHECK = not os.environ.get("PE_EXT_NO_EDGE_CHECK")
COINCIDENCE_COUNTERS = ("co_sck_mosi_rise", "co_sck_mosi_fall", "co_sck_csn_rise", "co_sck_csn_fall",
                        "co_tck_tdi_rise", "co_tck_tdi_fall", "co_tck_tms_rise", "co_tck_tms_fall",
                        "co_scl_sda_rise", "co_scl_sda_fall")


class PadContention(AssertionError):
    """A pad did not resolve to 0/1 while the checker was active."""


class PadHarness(CocotbHarness):
    """Lockstep harness whose pin inputs are the board's resolved pads."""

    def __init__(self, dut: Any, **kwargs: Any) -> None:
        super().__init__(dut, **kwargs)
        self._pads = dut.uio_in_q  # tb_ext.uio_in (the host driver's pin writes) is unconnected

    def _advance(self, ui: int, pins: int, reset: bool, enabled: bool, pre: Any) -> None:
        value = self._pads.value
        if value.is_resolvable:
            pins = value.to_unsigned()
        else:
            if self.checking and not reset and enabled:
                raise PadContention(f"cycle {self.cycle}: pads resolved to {value} "
                                    "(contention between drivers, or an X from the DUT)")
            text = str(value)
            pins = int("".join(c if c in "01" else "1" for c in text), 2)
        super()._advance(ui, pins, reset, enabled, pre)


def fault_of(status: int) -> int:
    return status >> 8 & 0xFF if status & 8 else 0


class Board:
    """Peer-control registers and peer logs of tb_ext.v."""

    def __init__(self, dut: Any) -> None:
        self.dut = dut

    def set(self, **regs: int) -> None:
        for name, value in regs.items():
            getattr(self.dut, name).value = value

    def get(self, name: str) -> int:
        return int(getattr(self.dut, name).value)

    def fill(self, memory: str, values: bytes | list[int]) -> None:
        array = getattr(self.dut, memory)
        for index, value in enumerate(values):
            array[index].value = value

    def log(self, memory: str, count: str) -> list[int]:
        array = getattr(self.dut, memory)
        return [int(array[i].value) for i in range(self.get(count))]

    def release(self) -> None:
        """Take every third-party peer out of reset."""
        self.dut.peer_rst.value = 0

    def coincidences(self, *prefixes: str) -> dict[str, int]:
        """Edge-coincidence counters (all, or those starting with a prefix such as 'co_sck')."""
        return {name.removeprefix("co_"): self.get(name) for name in COINCIDENCE_COUNTERS
                if not prefixes or name.startswith(prefixes)}


# Power-on values of every peer-control register of tb_ext.v. cocotb runs all
# tests of a module in one simulation, so start() restores these (and holds
# every peer in reset through the DUT reset) before each test.
DEFAULTS = dict(
    peer_rst=1, skew_sel=SKEW_CODES[SKEWS[0]], uart_prescale=8,
    sel_vuart_tx=0, vutx_len=0, vutx_gap=0, vutx_go=0, sel_py_uart_tx=0, py_uart_rxd=1,
    sel_flash=0, nslave_mode=0, nslave_miso_en=0,
    nmaster_sel=0, nmaster_len=0, nmaster_gap=64, nmaster_go=0,
    sel_py_spi_master=0, py_spi_sclk=0, py_spi_mosi=1, py_spi_cs=1, sel_py_spi_slave=0, py_spi_miso=1,
    sel_vi2c_slave=0, vi2cs_address=0x42, vi2cs_delay=0,
    sel_vi2c_master=0, vi2cm_prescale=32, vi2cm_address=0x42, vi2cm_read=0, vi2cm_wdata=0, vi2cm_go=0,
    sel_py_i2c=0, py_i2c_scl_o=1, py_i2c_sda_o=1, sel_py_i2c_b=0, py_i2c_b_scl_o=1, py_i2c_b_sda_o=1,
    sel_jtag=0,
)


async def start(dut: Any, fill: dict[str, Any] | None = None, skew: str | None = None,
                **peers: int) -> tuple[PadHarness, Board]:
    """Reset the DUT and all peers with the listed peers attached (and the peer
    data tables in ``fill`` loaded) in skew corner ``skew`` (default: the first
    of SKEWS), then release the peers."""
    unknown = set(peers) - set(DEFAULTS)
    assert not unknown, f"unknown peer controls {unknown}"
    board = Board(dut)
    board.set(**{**DEFAULTS, **peers, "skew_sel": SKEW_CODES[skew or SKEWS[0]]})
    for memory, values in (fill or {}).items():
        board.fill(memory, values)
    h = PadHarness(dut)
    await h.start()
    board.release()
    await h.idle(4)
    return h, board


def load_ext_image(name: str) -> dict[str, Any]:
    """test_ext/firmware/<name>.image.json with the harness's identity checks."""
    image: dict[str, Any] = json.loads((EXT_FIRMWARE / f"{name}.image.json").read_text())
    if image["schema_version"] != "protocol-emulator.firmware-image.v1" or image["isa_version"] != 2:
        raise ValueError(f"unsupported firmware image {name}")
    source = (EXT_FIRMWARE / f"{name}.source.json").read_bytes()
    if hashlib.sha256(source).hexdigest() != image["source_sha256"]:
        raise ValueError(f"firmware {name}: source identity mismatch")
    payload = b"".join(word.to_bytes(4, "little") for word in image["words"])
    if hashlib.sha256(payload).hexdigest() != image["bytecode_sha256"]:
        raise ValueError(f"firmware {name}: bytecode identity mismatch")
    return image


async def load(h: PadHarness, name: str) -> dict[str, Any]:
    """Load a shipped image (../firmware) or a test-only image (test_ext/firmware)."""
    if (EXT_FIRMWARE / f"{name}.image.json").exists():
        image = load_ext_image(name)
        version = await h.status(RS_VERSION)
        assert version >= image["isa_version"], f"{name} needs ISA {image['isa_version']}, device reports {version}"
        await h.load(image["engine"], image["words"], ownership=image["owned_pins"],
                     open_drain=image["open_drain"])
        return image
    return await h.load_firmware(name)


def uart_bit_cycles(image: dict[str, Any]) -> int:
    """The bit period the UART firmware declares in its notes ('64 clocks per bit' /
    'exact bit period 64 clocks')."""
    for note in image["notes"]:
        match = re.search(r"(\d+) clocks per bit|bit period (\d+) clocks", note)
        if match:
            return int(match.group(1) or match.group(2))
    raise ValueError(f"{image['name']}: no declared bit period")


async def drain(h: PadHarness, engine: int, count: int) -> list[int]:
    """Read ``count`` words from an engine's RX FIFO (window 3)."""
    await h.command(SELECT, engine)
    return [await h.read(3) for _ in range(count)]


async def status(h: PadHarness, engine: int, selection: int = RS_STATUS) -> int:
    await h.command(SELECT, engine)
    return await h.status(selection)


def assert_released(h: PadHarness, mask: int) -> None:
    assert h.last_pre.uio_oe & mask == 0, f"pins {mask:#04x} still driven: uio_oe={h.last_pre.uio_oe:#04x}"
