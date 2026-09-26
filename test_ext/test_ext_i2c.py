# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""I2C firmware against third-party I2C peers (SCL6/SDA7, open drain, pull-ups).

Peers: alexforencich/verilog-i2c i2c_slave and i2c_master (Verilog) and
alexforencich/cocotbext-i2c I2cMemory and I2cMaster (Python). The tests with
the DUT on the bus also check that it never actively drove an I2C pad high
(tb_ext.v od_violations).
"""

from __future__ import annotations

import cocotb

import ext_harness  # noqa: F401  (first: puts ../test and vendor/cocotbext-* on sys.path)
from cocotbext.i2c import I2cMaster, I2cMemory
from ext_harness import (CLEAR, EDGE_CHECK, RS_STATUS, SKEWS, START, STOP, assert_released, drain, fault_of, load, rng,
                         start, status)

ADDRESS = 0x42  # 7-bit address used by the firmware notes (0x84 = write, 0x85 = read)
ENGINE, MASK = 3, 8


def memory_pattern() -> bytes:
    return bytes((i * 37 + 11) & 0xFF for i in range(256))


def pick(test: str, *defaults: int, delay: bool = False) -> list[int]:
    """The fixed values, or (PE_EXT_SEED set) random bytes; with ``delay`` the
    last value is a target stretch delay in clocks, drawn from 0..1499."""
    generator = rng(test)
    if generator is None:
        return list(defaults)
    values = [generator.randrange(256) for _ in defaults]
    if delay:
        values[-1] = generator.randrange(1500)
    return values


async def _controller(h, image: str, tx: list[int]) -> None:
    await load(h, image)
    for word in tx:
        await h.write(2, word)
    await h.command(START, MASK)


PULL = 6  # opcode (docs/isa.md)


async def _wait_done(h, stops, rx_words: int, what: str, limit: int = 12000) -> None:
    """Run until the target has seen a STOP and the controller firmware blocks
    at PULL for its next TX word (a blocked WAITPIN also counts as stalled)."""
    engine = h.engine(ENGINE)

    def at_pull() -> bool:
        return engine.stalled and engine.program[engine.pc] >> 24 == PULL

    await h.run_until(lambda: engine.fault or (at_pull() and len(engine.rx) >= rx_words and stops() >= 1),
                      limit, what)
    await h.idle(64)


def _finish(h, board) -> None:
    edges = board.coincidences("co_scl")
    h.log("DUT SDA moves on the same clk edge as an SCL rise/fall: %s", edges)
    assert board.get("od_violations") == 0, "DUT drove an open-drain I2C pad high"
    # UM10204: data changes while SCL is low (set-up before the SCL rise, hold
    # after the fall); a same-edge SDA move is a set-up or hold violation.
    if EDGE_CHECK:
        assert edges["scl_sda_rise"] == edges["scl_sda_fall"] == 0, f"SDA moved on an SCL edge: {edges}"


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_i2c_write_vs_verilog_slave(dut, skew):
    """i2c-write: address 0x42+W and one byte into verilog-i2c i2c_slave."""
    (value,) = pick("i2c_write", 0x5A)
    h, board = await start(dut, skew=skew, sel_vi2c_slave=1, vi2cs_address=ADDRESS)
    await _controller(h, "i2c-write", [ADDRESS << 1, value])
    await _wait_done(h, lambda: board.get("vi2cs_stops"), 0, "I2C write STOP")
    h.assert_no_faults()
    assert board.log("vi2cs_log", "vi2cs_count") == [0x100 | value]  # {tlast, byte}: last byte of the write
    assert board.get("vi2cs_bus_address") == ADDRESS
    _finish(h, board)
    await h.command(STOP, MASK)


def slave_read_result(board, value: int) -> int:
    """What a controller must read from verilog-i2c i2c_slave.

    i2c_slave has a defect (docs/independent-peers.md): after stretching SCL
    for read data it releases SCL and drives the first data bit in the same
    clock (i2c_slave.v lines 387-395). If it was the last device holding SCL
    low and the bit is 1, SDA rises from the ACK level together with SCL;
    i2c_slave's own detector (line 238) takes that as a STOP, it goes idle and
    releases SDA, so the controller reads 0xFF. tb_ext.v flags that event
    (vi2cs_self_stop); it may only occur after a stretch and with bit 7 set.
    test_i2c_slave_stretch_defect_without_dut reproduces it with verilog-i2c's
    own i2c_master.
    """
    if board.get("vi2cs_self_stop"):
        assert board.get("vi2cs_read_stretched") and value & 0x80, "self-STOP without stretch or with bit 7 = 0"
        return 0xFF
    return value


@cocotb.test()
@cocotb.parametrize(first_bit=[0, 1], skew=SKEWS)
async def test_i2c_read_vs_verilog_slave_with_stretching(dut, first_bit, skew):
    """i2c-read: one byte from i2c_slave, which stretches SCL for 400 clocks before supplying it."""
    value, delay = pick(f"i2c_read{first_bit}", (0x69, 0xC3)[first_bit], 400, delay=True)
    value = value & 0x7F | first_bit << 7
    h, board = await start(dut, skew=skew, fill={"vi2cs_tx": [value]}, sel_vi2c_slave=1, vi2cs_address=ADDRESS,
                           vi2cs_delay=delay)
    await _controller(h, "i2c-read", [ADDRESS << 1 | 1])
    await _wait_done(h, lambda: board.get("vi2cs_stops"), 1, "I2C read STOP")
    h.assert_no_faults()
    expected = slave_read_result(board, value)
    h.log("i2c_slave byte %#04x, delay %d, stretched %d, self-STOP %d -> expected %#04x", value, delay,
          board.get("vi2cs_read_stretched"), board.get("vi2cs_self_stop"), expected)
    assert await drain(h, ENGINE, 1) == [expected]
    assert board.get("vi2cs_tx_idx") == 1 and board.get("vi2cs_count") == 0
    _finish(h, board)
    await h.command(STOP, MASK)


@cocotb.test()
@cocotb.parametrize(value=[0x43, 0xC3])
async def test_i2c_slave_stretch_defect_without_dut(dut, value):
    """Adjudication without the DUT: verilog-i2c i2c_master reads i2c_slave stretching 400 clocks.

    A first data bit of 0 reads back correctly; a first data bit of 1 reads
    0xFF, exactly what the DUT controller observes.
    """
    h, board = await start(dut, fill={"vi2cs_tx": [value]}, sel_vi2c_slave=1, vi2cs_address=ADDRESS,
                           vi2cs_delay=400, sel_vi2c_master=1, vi2cm_prescale=32)
    board.set(vi2cm_address=ADDRESS, vi2cm_read=1, vi2cm_go=1)
    await h.run_until(lambda: board.get("vi2cm_count") == 1 and not board.get("vi2cm_busy"), 20000,
                      "i2c_master read")
    await h.idle(64)
    got = board.log("vi2cm_log", "vi2cm_count")
    h.log("i2c_slave byte %#04x: i2c_master read %s", value, [hex(b) for b in got])
    assert board.get("vi2cs_read_stretched") == 1
    assert board.get("vi2cs_self_stop") == (value >> 7)
    assert got == [0xFF if value & 0x80 else value]
    assert h.last_pre.uio_oe == 0, "the DUT must stay off the bus"


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_i2c_repeated_start_vs_verilog_slave(dut, skew):
    """i2c-repeated-start: 0x42+W, register byte, repeated START, 0x42+R, one byte, NACK, STOP."""
    register, value, delay = pick("i2c_repeated_start", 0x10, 0xC3, 40, delay=True)
    h, board = await start(dut, skew=skew, fill={"vi2cs_tx": [value]}, sel_vi2c_slave=1, vi2cs_address=ADDRESS,
                           vi2cs_delay=delay)
    await _controller(h, "i2c-repeated-start", [ADDRESS << 1, register, ADDRESS << 1 | 1])
    await _wait_done(h, lambda: board.get("vi2cs_stops"), 1, "I2C repeated-start STOP", 16000)
    h.assert_no_faults()
    # i2c_slave marks the register byte as the last byte of the write when it sees the repeated START.
    assert board.log("vi2cs_log", "vi2cs_count") == [0x100 | register]
    expected = slave_read_result(board, value)
    h.log("i2c_slave byte %#04x, delay %d, stretched %d, self-STOP %d -> expected %#04x", value, delay,
          board.get("vi2cs_read_stretched"), board.get("vi2cs_self_stop"), expected)
    assert await drain(h, ENGINE, 1) == [expected]
    # After its STOP, i2c-repeated-start waits at PULL for the first TX word of
    # the next transaction before it generates a START (docs/independent-peers.md),
    # so the third-party target sees an idle bus with both lines released.
    assert board.get("vi2cs_stops") == 1 and board.get("vi2cs_active") == 0
    assert board.get("pad6") == 1 and board.get("pad7") == 1
    _finish(h, board)
    await h.command(STOP, MASK)


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_i2c_address_nack_vs_verilog_slave(dut, skew):
    """i2c-write to 0x42 while the only target is i2c_slave at 0x43: NACK -> STOP and fault 65, bus released."""
    h, board = await start(dut, skew=skew, sel_vi2c_slave=1, vi2cs_address=ADDRESS + 1)
    await _controller(h, "i2c-write", [ADDRESS << 1, 0x5A])
    await h.run_until(lambda: h.engine(ENGINE).fault != 0, 6000, "I2C NACK fault")
    await h.idle(4)
    assert fault_of(await status(h, ENGINE, RS_STATUS)) == 65
    assert_released(h, 0xC0)
    assert board.get("vi2cs_count") == 0
    # The NACK ends the transaction with a STOP (the SDA release is FAULT 65's
    # pin release), which i2c_slave recognises: its bus is no longer active.
    assert board.get("vi2cs_stops") == 1 and board.get("vi2cs_active") == 0
    _finish(h, board)
    await h.command(CLEAR, MASK | 1 << 23)


class CountingMemory(I2cMemory):
    """I2cMemory that counts the STOP conditions it recognises (its handle_stop hook)."""

    stops = 0

    def handle_stop(self) -> None:
        self.stops += 1


def _memory(dut, address: int) -> CountingMemory:
    memory = CountingMemory(sda=dut.pad7, sda_o=dut.py_i2c_sda_o, scl=dut.pad6, scl_o=dut.py_i2c_scl_o,
                            addr=address, size=256)
    memory.write_mem(0, memory_pattern())
    return memory


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_i2c_eeprom_reads_vs_cocotbext_memory(dut, skew):
    """cocotbext-i2c I2cMemory (256-byte EEPROM model at 0x42):

    1. i2c-write: one byte P (default 0x5A) = set the address pointer;
    2. i2c-read: current-address read -> memory[P];
    3. i2c-repeated-start: register R (default 0x37), repeated START, read -> memory[R] (random read).
    """
    pointer, register = pick("i2c_eeprom", 0x5A, 0x37)
    h, board = await start(dut, skew=skew, sel_py_i2c=1)
    memory = _memory(dut, ADDRESS)
    pattern = memory_pattern()
    await _controller(h, "i2c-write", [ADDRESS << 1, pointer])
    await _wait_done(h, lambda: memory.stops, 0, "pointer write")
    h.assert_no_faults()
    assert memory.ptr == pointer, f"I2cMemory pointer {memory.ptr:#x}"
    await h.command(STOP, MASK)
    await _controller(h, "i2c-read", [ADDRESS << 1 | 1])
    await _wait_done(h, lambda: memory.stops - 1, 1, "current-address read")
    h.assert_no_faults()
    assert await drain(h, ENGINE, 1) == [pattern[pointer]]
    await h.command(STOP, MASK)
    await _controller(h, "i2c-repeated-start", [ADDRESS << 1, register, ADDRESS << 1 | 1])
    await _wait_done(h, lambda: memory.stops - 2, 1, "repeated-start random read", 16000)
    h.assert_no_faults()
    assert await drain(h, ENGINE, 1) == [pattern[register]]
    _finish(h, board)
    await h.command(STOP, MASK)


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_i2c_address_nack_vs_cocotbext_memory(dut, skew):
    """i2c-repeated-start to 0x42 while I2cMemory answers only 0x50: NACK -> fault 65."""
    h, board = await start(dut, skew=skew, sel_py_i2c=1)
    _memory(dut, 0x50)
    await _controller(h, "i2c-repeated-start", [ADDRESS << 1, 0x37, ADDRESS << 1 | 1])
    await h.run_until(lambda: h.engine(ENGINE).fault != 0, 6000, "I2C NACK fault")
    await h.idle(4)
    assert fault_of(await status(h, ENGINE, RS_STATUS)) == 65
    assert_released(h, 0xC0)
    _finish(h, board)
    await h.command(CLEAR, MASK | 1 << 23)


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_i2c_target_vs_verilog_master(dut, skew):
    """i2c-target-write then i2c-target-read (address 0x42) driven by verilog-i2c i2c_master.

    The read is started before the host queues the TX byte, so the DUT must
    stretch SCL before its address ACK and i2c_master must wait.
    """
    written, served = pick("i2c_target_verilog", 0xA7, 0x3C)
    h, board = await start(dut, skew=skew, sel_vi2c_master=1, vi2cm_prescale=32)
    await load(h, "i2c-target-write")
    await h.command(START, MASK)
    await h.idle(16)
    board.set(vi2cm_address=ADDRESS, vi2cm_read=0, vi2cm_wdata=written, vi2cm_go=1)
    await h.run_until(lambda: len(h.engine(ENGINE).rx) == 1 and not board.get("vi2cm_busy"), 8000,
                      "target write")
    await h.idle(64)
    assert board.get("vi2cm_missed_acks") == 0
    assert await drain(h, ENGINE, 1) == [written]
    h.assert_no_faults()
    await h.command(STOP, MASK)
    await load(h, "i2c-target-read")
    await h.command(START, MASK)
    await h.idle(16)
    board.set(vi2cm_read=1, vi2cm_go=0)  # toggle = next command
    await h.idle(3000)  # the DUT holds SCL low: no TX byte queued yet
    assert board.get("pad6") == 0 and board.get("vi2cm_count") == 0, "DUT did not stretch SCL"
    await h.write(2, served)
    await h.run_until(lambda: board.get("vi2cm_count") == 1 and not board.get("vi2cm_busy"), 8000,
                      "target read")
    await h.idle(64)
    assert board.log("vi2cm_log", "vi2cm_count") == [served]
    assert board.get("vi2cm_missed_acks") == 0
    h.assert_no_faults()
    _finish(h, board)
    await h.command(STOP, MASK)


@cocotb.test()
@cocotb.parametrize(skew=SKEWS)
async def test_i2c_target_vs_cocotbext_master(dut, skew):
    """i2c-target-write then i2c-target-read driven by cocotbext-i2c I2cMaster at 400 kHz."""
    written, served = pick("i2c_target_python", 0x5E, 0xB4)
    h, board = await start(dut, skew=skew, sel_py_i2c=1)
    master = I2cMaster(sda=dut.pad7, sda_o=dut.py_i2c_sda_o, scl=dut.pad6, scl_o=dut.py_i2c_scl_o, speed=400e3)
    await load(h, "i2c-target-write")
    await h.command(START, MASK)
    await h.idle(16)

    async def write() -> None:
        await master.write(ADDRESS, bytes([written]))
        await master.send_stop()

    task = cocotb.start_soon(write())
    await h.run_until(lambda: task.done(), 20000, "I2cMaster write")
    await h.idle(64)
    assert await drain(h, ENGINE, 1) == [written]
    h.assert_no_faults()
    await h.command(STOP, MASK)
    await load(h, "i2c-target-read")
    await h.write(2, served)
    await h.command(START, MASK)
    await h.idle(16)
    result: list[bytes] = []

    async def read() -> None:
        result.append(bytes(await master.read(ADDRESS, 1)))
        await master.send_stop()

    task = cocotb.start_soon(read())
    await h.run_until(lambda: task.done(), 20000, "I2cMaster read")
    await h.idle(64)
    assert result == [bytes([served])], result
    h.assert_no_faults()
    _finish(h, board)
    await h.command(STOP, MASK)
