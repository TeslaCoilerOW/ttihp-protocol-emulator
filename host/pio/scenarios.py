# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Host scenarios that need service while the chip's clock keeps running.

Each scenario has a host side (a function of any ProtocolEmulator, using
only the public API, so the same code runs on the reference-model port and on
LockstepEmulator) and pad-level peers for the
chip side (pe_host.peers on the uio pads, stepped by the board once per
rising edge, as external hardware would be).

uart_stream   The host streams more words than the 8-word TX queue holds to
              engine 0 (firmware uart-tx, 8N1 on uio0) while it transmits:
              it tops the queue up whenever READ_SELECT 2 shows space.
uart_bridge   Bytes arrive on uio1 (engine 1, firmware uart-rx-idle); the host
              reads engine 1's RX queue and writes every word to engine 0's
              TX queue (uart-tx on uio0), with no ROUTE. More bytes arrive
              than a queue holds, and uart-rx-idle faults (code 4) if its RX
              queue is full when a frame completes, so the bridge works only
              if the host keeps up while the engines run.
"""

import os

from pe_host.flagship import UART_START_OFFSET, FlagshipPeers
from pe_host.image import FirmwareImage, load_scenario
from pe_host.peers import Environment, UartMonitor, UartSource

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.environ.get("PE_HOST_FIRMWARE_DIR") or os.path.join(HERE, "..", "..", "firmware")
BIT_CYCLES = 64          # uart-tx / uart-rx-idle: 64 clocks per bit
GAP_CYCLES = 64          # idle clocks between frames of the UART source


def image(name):
    return FirmwareImage.load(os.path.join(FIRMWARE, name + ".image.json"))


def stream_words(count, seed=1):
    """Deterministic byte values (a small LCG)."""
    out = []
    state = seed
    for _ in range(count):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((state >> 16) & 0xFF)
    return out


class UartPeers:
    """Chip-side peers: a monitor on uio0 and (for the bridge) a source on uio1."""

    def __init__(self, source_words=None, start=None):
        self.monitor = UartMonitor(0, BIT_CYCLES)
        peers = [self.monitor]
        self.source = None
        if source_words is not None:
            self.source = UartSource(1, source_words, BIT_CYCLES, GAP_CYCLES, start=start)
            peers.append(self.source)
        self.env = Environment(peers, pullups=0xFF)


class Result:
    def __init__(self):
        self.passed = False
        self.sent = []
        self.received = []
        self.host_writes = 0
        self.host_reads = 0
        self.polls = 0
        self.faults = None
        self.cycles = 0
        self.notes = []

    def summary(self):
        return ("%s: %d words sent, %d received, %d host TX writes, %d host RX reads, "
                "%d level polls, %d clocks%s"
                % ("PASS" if self.passed else "FAIL", len(self.sent), len(self.received),
                   self.host_writes, self.host_reads, self.polls, self.cycles,
                   "".join("; " + n for n in self.notes)))


def _wait_tx_drained(pe, engine, monitor, expected, limit_cycles):
    start = pe.cycles
    while True:
        tx, _ = pe.levels(engine)
        if tx == 0 and len(monitor.words) >= expected:
            return True
        if pe.cycles - start > limit_cycles:
            return False
        pe.idle(4 * BIT_CYCLES)


def uart_stream(pe, peers, words, max_cycles=None):
    """Host side of uart_stream; ``peers`` is the UartPeers on the chip side."""
    result = Result()
    result.sent = list(words)
    depth = pe.architecture["fifo_words"]
    frame = 10 * BIT_CYCLES
    if max_cycles is None:
        max_cycles = (len(words) + 4) * (frame + 200) * 4
    pe.reset()
    pe.check_isa()
    pe.load_image(image("uart-tx"), engine=0, check_isa=False)
    first = words[:depth]
    pe.tx_write(first, engine=0)
    pe.start(1)
    sent = len(first)
    start = pe.cycles
    while sent < len(words):
        tx, _ = pe.levels(0)
        space = depth - tx
        result.polls += 1
        if space:
            chunk = words[sent:sent + space]
            pe.tx_write(chunk, engine=0)
            result.host_writes += len(chunk)
            sent += len(chunk)
        else:
            pe.idle(frame // 2)
        if pe.cycles - start > max_cycles:
            result.notes.append("host did not finish streaming")
            break
    if not _wait_tx_drained(pe, 0, peers.monitor, len(words), max_cycles):
        result.notes.append("TX queue did not drain")
    pe.idle(frame)
    pe.stop(1)
    result.received = list(peers.monitor.words)
    result.faults = pe.fault_report()
    result.cycles = pe.cycles - start
    result.passed = (result.received == result.sent and not peers.monitor.errors
                     and result.faults.ok)
    if peers.monitor.errors:
        result.notes.append("framing errors: %r" % (peers.monitor.errors[:3],))
    return result


def uart_bridge(pe, peers, count, max_cycles=None, start_offset=400):
    """Host side of uart_bridge. ``peers.source`` must hold ``count`` words;
    its start is set here, ``start_offset`` clocks after START."""
    result = Result()
    result.sent = list(peers.source.words)
    frame = 10 * BIT_CYCLES + GAP_CYCLES
    if max_cycles is None:
        max_cycles = (count + 4) * frame * 4
    pe.reset()
    pe.check_isa()
    pe.load_image(image("uart-tx"), engine=0, check_isa=False)
    pe.load_image(image("uart-rx-idle"), engine=1, check_isa=False)
    pe.start(0b11)
    peers.source.start = pe.cycles + start_offset
    start = pe.cycles
    forwarded = 0
    while forwarded < count:
        _, level = pe.levels(1)
        got = pe.rx_read_many(level) if level else []
        result.polls += 1
        result.host_reads += len(got)
        if got:
            pe.tx_write(got, engine=0)
            result.host_writes += len(got)
            forwarded += len(got)
        if pe.fault_visible and pe.uo & 0x80:
            result.notes.append("FAULT pin high")
            break
        if pe.cycles - start > max_cycles:
            result.notes.append("bridge did not finish")
            break
    if not _wait_tx_drained(pe, 0, peers.monitor, count, max_cycles):
        result.notes.append("TX queue did not drain")
    pe.idle(frame)
    pe.stop(0b11)
    result.received = list(peers.monitor.words)
    result.faults = pe.fault_report()
    result.cycles = pe.cycles - start
    result.passed = (result.received == result.sent and not peers.monitor.errors
                     and result.faults.ok)
    if not result.faults.ok:
        result.notes.append(repr(result.faults))
    return result


class ChipTimedUartSource(UartSource):
    """The flagship's UART sender, started relative to the chip's own clock.

    run_flagship sets ``uart_in.start = pe.cycles + 96`` right after START,
    which on a host-clocked port is 96 edges after START. On the lockstep
    port pe.cycles is the edge of the last sample, and the free clock has run
    on by the host's latency since; so this source takes the assignment as
    "96 edges from now" on the board (as an external sender that is told to
    begin would), which keeps its first start bit after START.
    """

    def __init__(self, board_ref, *args, **kw):
        self._board_ref = board_ref
        self._start = None
        UartSource.__init__(self, *args, **kw)

    @property
    def start(self):
        return self._start

    @start.setter
    def start(self, value):
        board = self._board_ref.get("board")
        if value is None or board is None:
            self._start = value
        else:
            self._start = board.edges + UART_START_OFFSET


def flagship_peers(scenario_path, stretch=0):
    """(FlagshipPeers with a ChipTimedUartSource, bind) for pad-level use;
    call bind(board) once the board exists."""
    ref = {}
    peers = FlagshipPeers(load_scenario(scenario_path), stretch)
    old = peers.uart_in
    peers.uart_in = ChipTimedUartSource(ref, old.pin, old.words, old.bit_cycles, old.gap)
    peers.env = Environment([peers.uart_in, peers.spi, peers.i2c, peers.uart_out], pullups=0xFF)

    def bind(board):
        ref["board"] = board
    return peers, bind


class Handles:
    """FlagshipPeers-like view for run_flagship: the peers run at the pads
    (board side), so the port gets no software environment."""

    def __init__(self, peers):
        self.uart_in, self.uart_out = peers.uart_in, peers.uart_out
        self.spi, self.i2c = peers.spi, peers.i2c
        self.env = None
