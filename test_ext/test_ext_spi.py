# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""SPI firmware against third-party SPI peers (pins SCK2/MOSI3/MISO4/CSn5).

Peers: YosysHQ picorv32 spiflash.v (Verilog SPI flash model, modes 0/3),
schang412/cocotbext-spi SpiSlaveLoopback and SpiMaster (Python, modes 0-3),
nandland spi-master SPI_Master_With_Single_CS and spi-slave SPI_Slave (Verilog,
modes 0-3).
"""

from __future__ import annotations

import cocotb

import ext_harness  # noqa: F401  (first: puts ../test and vendor/cocotbext-* on sys.path)
from cocotbext.spi import SpiBus, SpiConfig, SpiMaster
from cocotbext.spi.devices.generic import SpiSlaveLoopback
from ext_harness import EDGE_CHECK, SKEWS, RS_LEVELS, START, STOP, assert_released, data, drain, load, rng, start, status
from flash_image import SIZE, flash_bytes

MODES = [0, 1, 2, 3]
LAST = 1 << 8  # spi-flash-mode*: release CSn after this byte
TX = [0xA6, 0x01, 0xFF, 0x5A, 0x3C, 0x80]
RESPONSES = [0x5A, 0x80, 0x3C, 0xC3, 0x01, 0xFE]


def stimulus(test: str) -> tuple[list[int], list[int]]:
    """(controller bytes, target response bytes) for a test."""
    generator = rng(test)
    return data(generator, TX), data(generator, RESPONSES)


def frame(*payload: int) -> list[int]:
    """TX words for one chip-select frame of the spi-flash firmware."""
    return [*payload[:-1], payload[-1] | LAST]


async def _flash_transaction(h, words: list[int]) -> list[int]:
    assert len(words) <= 8
    for word in words:
        await h.write(2, word)
    return await drain(h, 2, len(words))


def spi_edges(h, board, what: str) -> dict[str, int]:
    edges = board.coincidences("co_sck")
    h.log("%s: DUT pin moves on the same clk edge as an SCK rise/fall: %s", what, edges)
    return edges


def assert_spi_edges(edges: dict[str, int], mode: int, what: str) -> None:
    """Clock-phase check against the SPI mode definition itself (this project's
    reading, in addition to the peers): inside a frame the controller must not
    move MOSI on the edge where the target samples it (rising in modes 0/3,
    falling in modes 1/2), and CSn must not move on an SCK edge."""
    if not EDGE_CHECK:
        return
    sampling = "rise" if mode in (0, 3) else "fall"
    assert edges[f"sck_mosi_{sampling}"] == 0, f"{what}: MOSI moved on the sampling edge (SCK {sampling}): {edges}"
    assert edges["sck_csn_rise"] == edges["sck_csn_fall"] == 0, f"{what}: CSn moved on an SCK edge: {edges}"


@cocotb.test()
@cocotb.parametrize(mode=[0, 3], skew=SKEWS)
async def test_spi_flash_read(dut, mode, skew):
    """spi-flash-mode<N> (multi-byte CS frames) reads the picorv32 spiflash.v memory.

    Release-power-down (AB), READ (03) at two addresses, power-down (B9): a READ
    while powered down must not return memory data, a second AB restores it.
    """
    h, board = await start(dut, skew=skew, sel_flash=1)
    await load(h, f"spi-flash-mode{mode}")
    await h.command(START, 4)
    memory = flash_bytes()
    n = 4
    generator = rng(f"flash{mode}")
    addresses = (0x000000, 0x000F7C) if generator is None else tuple(generator.randrange(SIZE - n) for _ in range(2))
    h.log("flash reads at %s", [hex(a) for a in addresses])

    async def read(address: int) -> list[int]:
        cmd = [0x03, address >> 16 & 0xFF, address >> 8 & 0xFF, address & 0xFF]
        return await _flash_transaction(h, frame(*cmd, *([0x00] * n)))

    await _flash_transaction(h, frame(0xAB))  # release from deep power-down
    for address in addresses:
        got = await read(address)
        # spiflash.v shifts MOSI through its buffer, so MISO echoes the previous
        # byte during the command phase; data starts with the 5th byte.
        assert got[:4] == [0x00, 0x03, address >> 16 & 0xFF, address >> 8 & 0xFF], got
        assert got[4:] == list(memory[address:address + n]), f"read @{address:#08x}: {got[4:]}"
    await _flash_transaction(h, frame(0xB9))  # deep power-down
    got = await read(0x000010)
    assert got[4:] != list(memory[0x10:0x10 + n]), "flash returned data while powered down"
    await _flash_transaction(h, frame(0xAB))
    got = await read(0x000010)
    assert got[4:] == list(memory[0x10:0x10 + n]), got
    what = f"flash mode{mode} skew {skew}"
    assert_spi_edges(spi_edges(h, board, what), mode, what)
    h.assert_no_faults()
    await h.command(STOP, 4)
    await h.idle(2)
    assert_released(h, 0x2C)


@cocotb.test()
@cocotb.parametrize(mode=MODES, skew=SKEWS)
async def test_spi_controller_vs_cocotbext_loopback(dut, mode, skew):
    """spi-controller-mode<N> against cocotbext-spi SpiSlaveLoopback (MISO = previous MOSI word).

    SpiSlave samples MOSI on the edge its mode defines. With the skew corners a
    controller that changes MOSI on that edge (wrong CPHA) delivers shifted
    bytes in at least one corner, so this checks the controller's clock phase
    as well as its polarity.
    """
    TX, RESPONSES = stimulus(f"loopback{mode}")
    h, board = await start(dut, skew=skew, sel_py_spi_slave=1)
    config = SpiConfig(word_width=8, cpol=bool(mode >> 1), cpha=bool(mode & 1), msb_first=True,
                       cs_active_low=True, frame_spacing_ns=20)
    slave = SpiSlaveLoopback(SpiBus(dut, sclk_name="pad2", mosi_name="pad3", miso_name="py_spi_miso",
                                    cs_name="pad5"), config)
    await load(h, f"spi-controller-mode{mode}")
    for word in TX:
        await h.write(2, word)
    await h.command(START, 4)
    await h.run_until(lambda: len(h.engine(2).rx) == len(TX), len(TX) * 700 + 400, "SPI transfers")
    await h.idle(8)
    got = await drain(h, 2, len(TX))
    what = f"loopback mode{mode} skew {skew}"
    edges = spi_edges(h, board, what)
    assert got == [0x00] + TX[:-1], f"mode{mode}: MISO words {got}"
    assert await slave.get_contents() == TX[-1]
    assert_spi_edges(edges, mode, what)
    h.assert_no_faults()
    await h.command(STOP, 4)


def nslave_mosi_ok(mode: int, skew: str, sent: int, received: int) -> bool:
    """What nandland SPI_Slave must log for a MOSI byte (see the test docstring)."""
    if mode in (0, 1):
        return received == sent  # sampled on the correct edge
    if skew == "none":
        return True  # modes 2/3 without skew: simulator race, only logged
    if (mode, skew) in ((2, "data"), (3, "clock")):
        # Sampled on the edge where a correct controller changes MOSI; the
        # change arrives after (mode 2, data late) or before (mode 3, clock
        # late) the edge, which happens to give the intended bit.
        return received == sent
    if mode == 2:  # clock late: every sample sees the next bit; bit 0 is followed by an undefined level
        return received >> 1 == sent & 0x7F
    return received & 0x7F == sent >> 1  # mode 3, data late: every sample sees the previous bit


@cocotb.test()
@cocotb.parametrize(mode=MODES, skew=SKEWS)
async def test_spi_controller_vs_nandland_slave(dut, mode, skew):
    """spi-controller-mode<N> against nandland SPI_Slave, an adjudicated defective peer.

    SPI_Slave clocks both its MOSI sampler and its MISO shifter on the rising
    edge of w_SPI_Clk = CPHA ? ~SCK : SCK and never uses CPOL (upstream issue
    #5), and after presenting bit 7 at CS assertion its MISO bit counter starts
    at 7 again. Against the SPI mode definitions this predicts:
      MOSI: sampled on the correct edge in modes 0/1 -> must arrive intact in
            both skew corners; in modes 2/3 it is sampled on the edge where
            the controller changes MOSI, so the result depends on the corner:
            mode 2 exact with data late, shifted one bit left with clock late;
            mode 3 exact with clock late, shifted one bit right with data late
            (the bit outside the byte is not defined by SPI and not checked);
      MISO: modes 0/1/2 repeat bit 7 (shifter on the controller's sampling
            edge in modes 0/1, repeated counter start in mode 2; upstream issue
            #11), so the controller must read (b & 0x80) | (b >> 1); mode 3
            shifts on the leading edge with a correct count -> exact bytes.
    See docs/independent-peers.md.
    """
    TX, RESPONSES = stimulus(f"nslave{mode}")
    h, board = await start(dut, skew=skew, fill={"nslave_tx": RESPONSES}, nslave_mode=mode, nslave_miso_en=1)
    await load(h, f"spi-controller-mode{mode}")
    for word in TX:
        await h.write(2, word)
    await h.command(START, 4)
    await h.run_until(lambda: len(h.engine(2).rx) == len(TX), len(TX) * 700 + 400, "SPI transfers")
    await h.idle(8)
    received = board.log("nslave_log", "nslave_count")
    got = await drain(h, 2, len(TX))
    h.log("mode%d skew %s: SPI_Slave received %s, DUT read %s", mode, skew, [hex(b) for b in received],
          [hex(b) for b in got])
    what = f"nandland mode{mode} skew {skew}"
    edges = spi_edges(h, board, what)
    assert len(received) == len(TX)
    bad = [(hex(s), hex(r)) for s, r in zip(TX, received) if not nslave_mosi_ok(mode, skew, s, r)]
    assert not bad, f"mode{mode} skew {skew}: SPI_Slave MOSI bytes (sent, logged) off the prediction: {bad}"
    if mode in (0, 1, 2):
        defect = [(b & 0x80) | (b >> 1) for b in RESPONSES]
        assert got == defect, f"mode{mode}: MISO words {got}, peer-defect prediction {defect}, raw {RESPONSES}"
    else:
        assert got == RESPONSES, f"mode{mode}: MISO words {got} != {RESPONSES}"
    assert_spi_edges(edges, mode, what)
    h.assert_no_faults()
    await h.command(STOP, 4)


@cocotb.test()
@cocotb.parametrize(mode=MODES)
async def test_nandland_slave_vs_cocotbext_master(dut, mode):
    """Adjudication without the DUT: cocotbext-spi SpiMaster talks to nandland SPI_Slave.

    The DUT is reset and idle (every pin released). The same TX/response bytes
    as in test_spi_controller_vs_nandland_slave must give the same outcome with
    this independent controller: the MISO bytes the DUT read are then fully
    explained by SPI_Slave itself.
    """
    TX, RESPONSES = stimulus(f"nslave_ref{mode}")
    h, board = await start(dut, fill={"nslave_tx": RESPONSES}, nslave_mode=mode, nslave_miso_en=1,
                           sel_py_spi_master=1, py_spi_sclk=mode >> 1, py_spi_cs=1)
    config = SpiConfig(word_width=8, sclk_freq=1e6, cpol=bool(mode >> 1), cpha=bool(mode & 1),
                       msb_first=True, cs_active_low=True, frame_spacing_ns=2000)
    master = SpiMaster(SpiBus(dut, sclk_name="py_spi_sclk", mosi_name="py_spi_mosi", miso_name="pad4",
                              cs_name="py_spi_cs"), config)
    master.write_nowait(TX)
    await h.run_until(lambda: master.count_rx() == len(TX), len(TX) * 800 + 800, "cocotbext SPI master frames")
    await h.idle(16)
    got = list(master.read_nowait())
    received = board.log("nslave_log", "nslave_count")
    h.log("mode%d: SPI_Slave received %s, SpiMaster read %s", mode, [hex(b) for b in received], [hex(b) for b in got])
    if mode in (0, 1):
        assert received == TX, f"mode{mode}: SPI_Slave received {received} != {TX}"
    if mode in (0, 1, 2):
        assert got == [(b & 0x80) | (b >> 1) for b in RESPONSES], f"mode{mode}: SpiMaster read {got}"
    else:
        assert got == RESPONSES, f"mode{mode}: SpiMaster read {got}"
    assert h.last_pre.uio_oe == 0, "the DUT must stay off the bus"


async def _spi_target(dut, mode: int, master_kind: str) -> None:
    TX, RESPONSES = stimulus(f"target_{master_kind}{mode}")
    peers = {"nmaster_sel": 1 << mode} if master_kind == "verilog" else {"sel_py_spi_master": 1}
    if master_kind == "python":
        peers.update(py_spi_sclk=mode >> 1, py_spi_cs=1)
    h, board = await start(dut, **peers)
    await load(h, f"spi-target-mode{mode}")
    for word in RESPONSES:  # TX must be queued before CS assertion
        await h.write(2, word)
    await h.command(START, 4)
    await h.idle(16)
    if master_kind == "verilog":
        board.fill("nmaster_tx", TX)
        board.set(nmaster_len=len(TX), nmaster_gap=64, nmaster_go=1)
        await h.run_until(lambda: board.get("nmaster_count") == len(TX), len(TX) * 900 + 400,
                          "nandland SPI master frames")
        received = board.log("nmaster_log", "nmaster_count")
    else:
        config = SpiConfig(word_width=8, sclk_freq=1e6, cpol=bool(mode >> 1), cpha=bool(mode & 1),
                           msb_first=True, cs_active_low=True, frame_spacing_ns=2000)
        master = SpiMaster(SpiBus(dut, sclk_name="py_spi_sclk", mosi_name="py_spi_mosi", miso_name="pad4",
                                  cs_name="py_spi_cs"), config)
        master.write_nowait(TX)
        await h.run_until(lambda: master.count_rx() == len(TX), len(TX) * 800 + 800, "cocotbext SPI master frames")
        received = list(master.read_nowait())
    await h.idle(16)
    assert received == RESPONSES, f"mode{mode}: controller received {received} != {RESPONSES}"
    got = await drain(h, 2, len(TX))
    assert got == TX, f"mode{mode}: target RX FIFO {got} != {TX}"
    h.assert_no_faults()
    assert await status(h, 2, RS_LEVELS) == 0
    assert_released(h, 0x10)
    await h.command(STOP, 4)


@cocotb.test()
@cocotb.parametrize(mode=MODES)
async def test_spi_target_vs_nandland_master(dut, mode):
    """spi-target-mode<N> driven by nandland SPI_Master_With_Single_CS (32 clocks per SCK phase)."""
    await _spi_target(dut, mode, "verilog")


@cocotb.test()
@cocotb.parametrize(mode=MODES)
async def test_spi_target_vs_cocotbext_master(dut, mode):
    """spi-target-mode<N> driven by cocotbext-spi SpiMaster at 1 MHz."""
    await _spi_target(dut, mode, "python")
